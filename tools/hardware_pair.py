#!/usr/bin/env python3
"""Call between two hardware modems, logging training and exact payloads.

With 1901/1903 on this VG204 this is a local POTS bridge, not an RTP test.
Use an external SIP relay and --dial 4242 to test the packet path instead.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import termios
import time

from modem_test import (ROOT, call, command, configure_serial_controls, identify,
                        query, terminal_failure, write_payload)
from modem_diagnostics import collect as collect_diagnostics


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--caller', required=True)
    ap.add_argument('--answer', required=True)
    ap.add_argument('--dial', required=True, choices=('1901', '1903', '4242', '4343'))
    ap.add_argument('--caller-init', default='ATX3')
    ap.add_argument('--answer-init', default='ATX3')
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--wait', type=float, default=70)
    ap.add_argument('--hold', type=float, default=5)
    ap.add_argument('--bytes', type=int, default=1024)
    ap.add_argument('--payload-bps', type=int, default=4800)
    ap.add_argument('--relay-secret', type=Path,
                    help='start the existing RTP relay using this external credential file')
    ap.add_argument('--relay-to', choices=('1901', '1903'),
                    help='relay outbound destination; caller must dial the relay account')
    args = ap.parse_args()
    if os.path.realpath(args.caller) == os.path.realpath(args.answer):
        ap.error('caller and answer must be different devices')
    if bool(args.relay_secret) != bool(args.relay_to):
        ap.error('--relay-secret and --relay-to are required together')
    if args.relay_to and args.dial not in ('4242', '4343'):
        ap.error('relay calls must dial the relay account 4242 or 4343')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    result = dict(dial=args.dial, status='failed', stage='identification',
                  path='VG204 local POTS bridge' if args.dial.startswith('19') else 'external relay required',
                  connected=False, payload={}, caller_init=args.caller_init,
                  answer_init=args.answer_init)
    ends = []
    relay = relay_log = None
    with (out/'call.log').open('w') as log:
        call.T0 = time.time()
        try:
            if args.relay_to:
                result['path'] = 'VG204 -> Asterisk -> packet relay -> Asterisk -> VG204'
                result['relay_to'] = args.relay_to
                relay_log = (out/'relay.log').open('w')
                relay = subprocess.Popen([sys.executable, str(ROOT/'tools/rtp_relay.py'),
                    '--secret', str(args.relay_secret.resolve()), '--dial', args.relay_to,
                    '--rec', str(out/'relay'), '--hold', str(args.wait+120)],
                    stdout=relay_log, stderr=relay_log, start_new_session=True)
                deadline = time.monotonic()+15
                while 'REGISTERED' not in (out/'relay.log').read_text(errors='replace'):
                    if relay.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError('relay did not register; see relay.log')
                    time.sleep(.1)
            identities = [identify(path) for path in (args.caller, args.answer)]
            result['hardware'] = dict(zip(('caller', 'answer'), identities))
            for name, path in (('caller', args.caller), ('answer', args.answer)):
                end = call.End(name, call.open_raw(path), log)
                ends.append(end)
                fcntl.ioctl(end.fd, termios.TIOCMBIS,
                            struct.pack('I', termios.TIOCM_RTS | termios.TIOCM_DTR))
            result['stage'] = 'initialization'
            for end, identity, extra in zip(ends, identities, (args.caller_init, args.answer_init)):
                for text in ('ATZ', 'AT&F', 'ATE0Q0V1'):
                    command(end, ends, text)
                if 'USRobotics' in ' '.join(identity.values()):
                    for text in ('AT&B1', 'AT&A3'):
                        command(end, ends, text)
                configure_serial_controls(end, ends,
                    'usr' if 'USRobotics' in ' '.join(identity.values()) else 'other')
                for text in extra.split(';'):
                    if text.strip():
                        command(end, ends, text.strip())
            caller, answer = ends
            command(caller, ends, 'ATS0=0')
            command(answer, ends, 'ATS0=1')
            command(answer, ends, 'ATS0?')
            result['stage'] = 'routing'
            caller.send('ATDT'+args.dial+'\r')
            call.pump(ends, args.wait, until=lambda es: all(e.connected for e in es) or
                      any(terminal_failure(e.text) for e in es))
            result['ring_seen'] = 'RING' in answer.text
            result['stage'] = 'training' if result['ring_seen'] else 'routing'
            if not all(e.connected for e in ends):
                raise RuntimeError('both hardware modems did not CONNECT')
            result['connected'] = True
            result['stage'] = 'data'
            call.pump(ends, 5)
            for sender, receiver in ((caller, answer), (answer, caller)):
                payload = ('HWPAIR-'+os.urandom(12).hex()+':' +
                           ''.join(chr(33+b%90) for b in os.urandom(args.bytes))+'\r\n').encode()
                receiver.rx = b''
                written = write_payload(sender, payload, ends, 30, args.payload_bps)
                call.pump(ends, 30, until=lambda es: payload in receiver.rx or
                          any('NO CARRIER' in e.text for e in es))
                result['payload'][sender.name] = dict(sent=len(payload), written=written,
                    received=len(receiver.rx), exact=payload in receiver.rx,
                    sha256=hashlib.sha256(payload).hexdigest())
            call.pump(ends, args.hold)
            result['carrier_held'] = all('NO CARRIER' not in e.text for e in ends)
            result['hold_seconds'] = args.hold
            result['status'] = ('carrier_lost' if not result['carrier_held'] else
                                'passed' if all(p['exact'] for p in result['payload'].values()) else 'data_failed')
            if result['status'] == 'passed':
                result['stage'] = 'complete'
        except Exception as exc:
            result['error'] = str(exc)
        finally:
            result['terminal_before_cleanup'] = {
                end.name: terminal_failure(end.text) for end in ends}
            # Guard time, escape and hang up; never save a changed profile.
            call.pump(ends, 1.2)
            for end in ends:
                end.send(b'+++')
            call.pump(ends, 1.3)
            for end in ends:
                end.send('ATH\r')
            call.pump(ends, 2)
            for end, identity in zip(ends, result.get('hardware', {}).values()):
                try:
                    kind = 'usr' if 'USRobotics' in ' '.join(identity.values()) else 'other'
                    report = collect_diagnostics(kind, lambda text: query(end, ends, text))
                    (out/f'{end.name}-diagnostics.json').write_text(json.dumps(report,indent=2)+'\n')
                except Exception as exc:
                    result[f'{end.name}_diagnostics_error'] = str(exc)
            for end in ends:
                end.send('ATS0=0\r')
            call.pump(ends, .5)
            for end in ends:
                termios.tcflush(end.fd, termios.TCOFLUSH)
                os.close(end.fd)
            if relay:
                if relay.poll() is None:
                    os.killpg(relay.pid, signal.SIGTERM)
                try:
                    relay.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    os.killpg(relay.pid, signal.SIGKILL)
                    relay.wait()
                result['relay_exit_code'] = relay.returncode
            if relay_log:
                relay_log.close()
            (out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
