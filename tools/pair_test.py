#!/usr/bin/env python3
"""Test two DSP variants over local audio transport or two Asterisk SIP accounts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

from modem_test import ROOT, call, command, save_audio, start_daemon


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--caller', choices=('original', 'reconstructed'), default='reconstructed')
    ap.add_argument('--answer', choices=('original', 'reconstructed'), default='original')
    ap.add_argument('--transport', choices=('local', 'sip'), default='local')
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--wait', type=float, default=70)
    ap.add_argument('--hold', type=float, default=5)
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    processes, logs, ends = [], [], []
    result = dict(caller=args.caller, answer=args.answer, transport=args.transport,
                  connected=False, status='failed', payload={})
    call.T0 = time.time()
    with (out / 'call.log').open('w') as seriallog:
        try:
            for i, variant in enumerate((args.caller, args.answer)):
                side = out / ('caller' if i == 0 else 'answer')
                side.mkdir()
                log = (side / 'slmodemd.log').open('w')
                logs.append(log)
                cfg = SimpleNamespace(dsp=variant, device_num=47+i,
                    selftest_id=str(1001+i) if args.transport == 'local' else None,
                    secret=ROOT.parent / f'asterisk-login-{4242 if i == 0 else 4343}.secret')
                proc, pty, binary = start_daemon(cfg, side, log)
                processes.append(proc)
                result['caller_sha256' if i == 0 else 'answer_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
                end = call.End('caller' if i == 0 else 'answer', call.open_raw(pty), seriallog)
                ends.append(end)
                for text in ('ATZ', 'ATE0Q0V1', 'AT+MS=34,1', 'ATS70=7', 'ATS0=0'):
                    command(end, ends, text)
            caller, answer = ends
            call.pump(ends, 3)
            caller.send('ATDT' + ('1002' if args.transport == 'local' else '4343') + '\r')
            if not call.pump(ends, 20, until=lambda es: 'RING' in answer.text):
                raise RuntimeError('no RING from peer')
            answer.send('ATA\r')
            call.pump(ends, args.wait, until=lambda es: all(e.connected for e in es) or
                      any('NO CARRIER' in e.text for e in es))
            if not all(e.connected for e in ends):
                raise RuntimeError('both modems did not CONNECT')
            result['connected'] = True
            call.pump(ends, 2)
            for sender, receiver in ((caller, answer), (answer, caller)):
                payload = ('DMODEM-'+os.urandom(12).hex()+':'+'0123456789abcdef'*16+'\r\n').encode()
                receiver.rx = b''
                sender.send(payload)
                exact = call.pump(ends, 15, until=lambda es: payload in receiver.rx)
                result['payload'][sender.name] = dict(sent=len(payload), received=len(receiver.rx), exact=bool(exact))
            call.pump(ends, args.hold)
            result['status'] = 'passed' if all(r['exact'] for r in result['payload'].values()) else 'data_failed'
        except Exception as exc:
            result['error'] = str(exc)
        finally:
            for proc in processes:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    proc.wait(timeout=5)
                except ProcessLookupError:
                    pass
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
            for end in ends:
                os.close(end.fd)
            for log in logs:
                log.close()
            for name in ('caller', 'answer'):
                if (out / name).exists():
                    save_audio(out / name)
            (out / 'result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
