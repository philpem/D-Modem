#!/usr/bin/env python3
"""Identify hardware, map its extension, or test a D-Modem call and both data directions."""
import argparse
import hashlib
import json
import os
import fcntl
import struct
import termios
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import time
import wave
from modem_diagnostics import collect as collect_diagnostics

from integration_paths import ROOT, DSP_ROOT
sys.path.insert(0, str(DSP_ROOT / 'testbench'))
import atprobe
import terminal_io as call


def terminal_failure(text):
    return bool(re.search(r'\b(?:BUSY|NO CARRIER|NO DIAL\s*TONE)\b',text))


def query(end, ends, text, seconds=4):
    start = len(end.text)
    end.send(text + '\r')
    call.pump(ends, seconds, until=lambda es: bool(
        re.search(r'\b(OK|ERROR)\b', end.text[start:])))
    call.pump(ends, 0.3)
    return end.text[start:]


def command(end, ends, text, seconds=4):
    reply = query(end, ends, text, seconds)
    if 'ERROR' in reply or 'OK' not in reply:
        raise RuntimeError(f'{end.name}: command {text} failed: {reply!r}')
    return reply


def configure_serial_controls(end, ends, kind, hardware_flow='none'):
    # Console cables may carry only RX/TX/GND. Host ioctl state does not
    # establish that the modem physically receives DTR or RTS.
    commands = ['AT&D0']
    if kind == 'usr':
        commands += (['AT&H0', 'AT&I0', 'AT&R1'] if hardware_flow == 'none'
                     else ['AT&H1', 'AT&I0', 'AT&R2'])
    else:
        commands += ['AT&K0' if hardware_flow == 'none' else 'AT&K3']
    for text in commands:
        command(end, ends, text)


def identify(path):
    ok, _, detail = atprobe.probe(path, 2)
    if not ok:
        raise RuntimeError(f'{path}: {detail}')
    fd = atprobe.open_raw(path)
    try:
        time.sleep(0.3)
        replies = {cmd: atprobe.command(fd, cmd, 1.0)
                   for cmd in ('AT', 'ATI0', 'ATI3', 'ATI4', 'ATI7')}
        if 'OK' not in replies['AT']:
            raise RuntimeError(f'{path}: no response to AT')
        return replies
    finally:
        os.close(fd)


def start_daemon(args, out, log):
    binary = (args.binary.resolve() if getattr(args, 'binary', None) else
              ROOT / f'slmodemd/slmodemd-{args.dsp}')
    env = os.environ.copy()
    env.update(SLMODEMD_CAPTURE_DIR=str(out), SLMODEMD_V8_OPEN_STUB='0')
    # Some reconstructed sites cast this unsigned global to signed int.
    # UINT_MAX suppresses those prints by becoming -1; INT_MAX enables both.
    env.setdefault('SLMODEMD_DSP_DEBUG','2147483647')
    env.setdefault('SLMODEMD_DSP_PLAIN','1')
    if args.dsp == 'reconstructed-ec':
        env.setdefault('SLMODEMD_EC_PROBE', '1')
    if args.dsp == 'reconstructed-hs':
        env.setdefault('SLMODEMD_HANDSHAKE_PROBE', '1')
    if getattr(args, 'selftest_id', None):
        env.update(DMODEM_SELFTEST_ID=args.selftest_id,
                   DMODEM_SELFTEST_DIR=str(out.parent))
    if args.secret:
        env['DMODEM_SECRET'] = str(args.secret.resolve())
    proc = subprocess.Popen([str(binary), '-n', '-d9', '-e',
                             str(getattr(args, 'executor', ROOT / 'tools/sip_exec.py')),
                             f'/dev/slamr{getattr(args, "device_num", 47)}'],
                            cwd=out, env=env, stdout=log, stderr=log,
                            start_new_session=True)
    for _ in range(100):
        text = (out / 'slmodemd.log').read_text(errors='replace')
        match = re.search(r"TTY is `([^']+)'", text)
        if match:
            return proc, match[1], binary
        if proc.poll() is not None:
            raise RuntimeError('slmodemd exited before creating its PTY; see slmodemd.log')
        time.sleep(0.1)
    os.killpg(proc.pid, signal.SIGTERM)
    raise RuntimeError('no PTY from slmodemd')


def save_audio(out):
    for raw in out.glob('modem_*.raw'):
        rate = 8000 if '_8k' in raw.stem else 9600
        data = raw.read_bytes()
        with wave.open(str(raw.with_suffix('.wav')), 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(data)
        raw.unlink()


def write_payload(sender, payload, ends, timeout, bits_per_second=0):
    """Handle short nonblocking serial writes while draining both receivers."""
    sent = 0
    deadline = time.monotonic()+timeout
    next_write = time.monotonic()
    while sent < len(payload):
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f'{sender.name}: wrote {sent}/{len(payload)} payload bytes')
        now = time.monotonic()
        writable = [sender.fd] if now >= next_write else []
        pause = min(.2,remaining,max(.001,next_write-now)) if not writable else min(.2,remaining)
        readers, writers, _ = select.select([e.fd for e in ends],writable,[],pause)
        for end in ends:
            if end.fd in readers:
                end.drain()
        if writers:
            try:
                count = os.write(sender.fd,payload[sent:sent+(64 if bits_per_second else 512)])
                sent += count
                if bits_per_second:
                    next_write = time.monotonic()+count*8/bits_per_second
            except BlockingIOError:
                continue
    sender.log.write('%8.3f %s >> payload bytes=%d sha256=%s\n' % (
        time.time()-call.T0,sender.name,sent,hashlib.sha256(payload).hexdigest()))
    sender.log.flush()
    return sent


def transfer_payloads(ends, args, prefix='DMODEM'):
    """Verify fresh, paced payloads in both directions, including after a hold."""
    rows = {}
    for sender, receiver in ((ends[1], ends[0]), (ends[0], ends[1])):
        payload = (prefix + '-' + os.urandom(12).hex() + ':' +
                   ''.join(chr(33 + b % 90) for b in os.urandom(args.bytes)) + '\r\n').encode()
        receiver.rx = b''
        written = write_payload(sender, payload, ends, args.data_wait, args.payload_bps)
        call.pump(ends, args.data_wait, until=lambda es: payload in receiver.rx or
                  any('NO CARRIER' in e.text for e in es))
        rows[sender.name + '_to_' + receiver.name] = dict(
            sent=len(payload), written=written, received=len(receiver.rx),
            exact=payload in receiver.rx, sha256=hashlib.sha256(payload).hexdigest())
    return rows


def run_call(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    identity = identify(args.tty)
    (out / 'identity.json').write_text(json.dumps(identity, indent=2) + '\n')
    kind = 'usr' if 'USRobotics' in ' '.join(identity.values()) else 'other'
    result = dict(dsp=args.dsp, hardware=identity, tty=args.tty,
                  originator=args.originator, dial=args.dial,
                  hardware_flow_control=args.hardware_flow,
                  hardware_init=args.hardware_init,
                  software_init=args.software_init,
                  auto_answer=args.auto_answer,
                  payload_bits_per_second=args.payload_bps,
                  ec_probe_enabled=args.dsp=='reconstructed-ec' and os.environ.get('SLMODEMD_EC_PROBE','1')!='0',
                  handshake_probe_enabled=args.dsp=='reconstructed-hs' and os.environ.get('SLMODEMD_HANDSHAKE_PROBE','1')!='0',
                  connected=False, payload={}, status='failed',stage='initialization')
    proc = None
    ends = []
    with (out / 'slmodemd.log').open('w') as debug, (out / 'call.log').open('w') as log:
        call.T0 = time.time()
        try:
            proc, pty, binary = start_daemon(args, out, debug)
            result['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
            if args.dsp.startswith('reconstructed'):
                build_name={'reconstructed-ec':'dmodem-ec','reconstructed-hs':'dmodem-hs'}.get(args.dsp,'dmodem')
                manifest = (args.build_manifest if args.build_manifest else
                            DSP_ROOT/'build'/build_name/'build.json')
                if manifest.exists():
                    (out/'dsp-build.json').write_bytes(manifest.read_bytes())
            ends = [call.End('hardware', call.open_raw(args.tty), log),
                    call.End('dmodem', call.open_raw(pty), log)]
            hw, sw = ends
            fcntl.ioctl(hw.fd, termios.TIOCMBIS,
                        struct.pack('I', termios.TIOCM_RTS | termios.TIOCM_DTR))
            for end in ends:
                command(end, ends, 'ATZ')
            command(hw, ends, 'AT&F')
            if kind == 'usr':
                command(hw, ends, 'AT&B1')
                command(hw, ends, 'AT&A3')
            for end in ends:
                command(end, ends, 'ATE0Q0V1')
            configure_serial_controls(hw, ends, kind, args.hardware_flow)
            # Raw DTE bytes; no software flow control, echo or compression.
            command(sw, ends, 'AT+MS=34,1')
            command(sw, ends, 'ATS70=7')
            for cmd in args.software_init.split(';'):
                if cmd.strip():
                    command(sw, ends, cmd.strip())
            for cmd in args.hardware_init.split(';'):
                if cmd.strip():
                    command(hw, ends, cmd.strip())
            if args.hardware_flow == 'rtscts':
                bits = struct.unpack('I',fcntl.ioctl(hw.fd,termios.TIOCMGET,struct.pack('I',0)))[0]
                if not bits & termios.TIOCM_CTS:
                    raise RuntimeError('hardware CTS is low; refusing to queue writes behind unavailable flow control')
                attrs = termios.tcgetattr(hw.fd)
                attrs[2] |= termios.CRTSCTS
                termios.tcsetattr(hw.fd,termios.TCSANOW,attrs)
            command(hw, ends, 'ATS0=0')
            command(sw, ends, 'ATS0=0')
            origin, answer = (sw, hw) if args.originator == 'dmodem' else (hw, sw)
            if args.auto_answer:
                command(answer, ends, 'ATS0=1')
            # Give SIP registration time to complete.
            call.pump(ends, 3)
            result['stage'] = 'routing'
            origin.send('ATDT' + args.dial + '\r')
            call.pump(ends, args.wait, until=lambda es: 'RING' in answer.text or
                      terminal_failure(origin.text))
            if 'RING' not in answer.text:
                raise RuntimeError('no RING at answering end')
            if args.map_only:
                text = (out / 'slmodemd.log').read_text(errors='replace')
                found = re.search(r'(?:Incoming call from |From:)[^\n]*?sip:(19\d\d)@', text)
                if not found:
                    raise RuntimeError('RING received but no numeric caller identity in SIP log')
                result.update(status='mapped', extension=found[1])
                return result
            result['stage'] = 'training'
            if not args.auto_answer:
                answer.send('ATA\r')
            call.pump(ends, args.wait, until=lambda es: all(e.connected for e in es) or
                      any('NO CARRIER' in e.text for e in es))
            if not all(e.connected for e in ends):
                raise RuntimeError('both ends did not CONNECT')
            result['connected'] = True
            result['stage'] = 'data'
            call.pump(ends, 2)
            result['payload'] = transfer_payloads(ends, args)
            call.pump(ends, args.hold)
            result['hold_seconds'] = args.hold
            if args.verify_after_hold:
                result['post_hold_payload'] = transfer_payloads(ends, args, 'POST-HOLD')
            result['carrier_held'] = all('NO CARRIER' not in e.text for e in ends)
            result['status'] = ('carrier_lost' if not result['carrier_held'] else
                               'passed' if all(p['exact'] for group in
                                   (result['payload'], result.get('post_hold_payload', {}))
                                   for p in group.values())
                               else 'data_failed')
            if result['status']=='passed':
                result['stage']='complete'
        except Exception as exc:
            result['error'] = str(exc)
        finally:
            if ends:
                result['terminal_before_cleanup'] = {
                    end.name: terminal_failure(end.text) for end in ends}
                # Restore on-hook state without changing saved modem profiles.
                for end in ends:
                    end.send(b'+++')
                call.pump(ends, 1.3)
                for end in ends:
                    end.send('ATH\r')
                call.pump(ends, 2.5)
                if len(ends) == 2:
                    for end, category in ((ends[0], kind), (ends[1], 'software')):
                        try:
                            report = collect_diagnostics(category,
                                lambda text: query(end, ends, text))
                            (out/f'{end.name}-diagnostics.json').write_text(
                                json.dumps(report, indent=2)+'\n')
                        except Exception as exc:
                            result[f'{end.name}_diagnostics_error'] = str(exc)
                for end in ends:
                    termios.tcflush(end.fd,termios.TCOFLUSH)
                    os.close(end.fd)
            if proc:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            save_audio(out)
            (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--tty', required=True)
    ap.add_argument('--identify', action='store_true')
    ap.add_argument('--diagnose', action='store_true', help='read the hardware last-call report')
    ap.add_argument('--hardware-query', action='append', default=[], metavar='AT_COMMAND',
                    help='query the modem without placing a call; repeat for several commands')
    ap.add_argument('--map-only', action='store_true')
    ap.add_argument('--dsp', choices=('original', 'reconstructed', 'reconstructed-ec','reconstructed-hs'), default='reconstructed')
    ap.add_argument('--binary', type=Path, help='test a candidate binary without replacing a control build')
    ap.add_argument('--build-manifest', type=Path, help='candidate DSP build manifest')
    ap.add_argument('--originator', choices=('hardware', 'dmodem'), default='dmodem')
    ap.add_argument('--dial', default='4242')
    ap.add_argument('--secret', type=Path)
    ap.add_argument('--out', type=Path)
    ap.add_argument('--wait', type=float, default=70)
    ap.add_argument('--hold', type=float, default=10)
    ap.add_argument('--verify-after-hold', action='store_true',
                    help='require fresh exact payloads both ways after the carrier hold')
    ap.add_argument('--bytes', type=int, default=256)
    ap.add_argument('--data-wait', type=float, default=30,
                    help='maximum wait for writing/receiving each payload')
    ap.add_argument('--hardware-flow',choices=('none','rtscts'),default='none')
    ap.add_argument('--payload-bps',type=int,default=0,
                    help='pace payloads to this bit rate (0: unpaced); useful with three-wire serial adapters')
    ap.add_argument('--hardware-init', default='')
    ap.add_argument('--software-init', default='',
                    help='additional software modem AT commands after the standard V.34 setup')
    ap.add_argument('--auto-answer', action='store_true',
                    help='use S0=1 instead of sending ATA immediately after the first RING')
    args = ap.parse_args()
    if args.identify:
        print(json.dumps(identify(args.tty), indent=2))
        return 0
    if args.hardware_query:
        if any(not text.upper().startswith('AT') or '\r' in text or '\n' in text
               for text in args.hardware_query):
            ap.error('--hardware-query requires a single AT command per argument')
        fd = atprobe.open_raw(args.tty)
        try:
            time.sleep(.3)
            replies = {text: atprobe.command(fd, text, 2, deadline=5)
                       for text in args.hardware_query}
            print(json.dumps(replies, indent=2))
        finally:
            termios.tcflush(fd, termios.TCOFLUSH)
            os.close(fd)
        return 0
    if args.diagnose:
        identity = identify(args.tty)
        fd = atprobe.open_raw(args.tty)
        try:
            time.sleep(.3)
            cmd = 'ATI11' if 'USRobotics' in ' '.join(identity.values()) else 'AT&V1'
            print(atprobe.command(fd, cmd, 2, deadline=5))
        finally:
            os.close(fd)
        return 0
    if not args.out:
        ap.error('--out is required for a call')
    if args.map_only and args.originator != 'hardware':
        ap.error('--map-only requires --originator hardware')
    if args.dial not in {'4242', '4343', '1901', '1902', '1903'}:
        ap.error('only documented internal test extensions may be dialled')
    result = run_call(args)
    print(json.dumps({k: v for k, v in result.items() if k != 'hardware'}, indent=2))
    return 0 if result['status'] in ('passed', 'mapped') else 1


if __name__ == '__main__':
    sys.exit(main())
