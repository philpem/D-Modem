#!/usr/bin/env python3
"""Live USR/slmodem presentation: AT transcript, negotiation audio and song."""
import argparse
import array
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import struct
import sys
import termios
import threading
import time
import wave
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
USR_TTY = '/dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller_CGEGb135B02-if00-port0'


class Console:
    def __init__(self, stream, plain=False):
        self.stream = stream
        self.start = time.monotonic()
        self.colour = sys.stdout.isatty() and not plain

    def show(self, name, direction, text):
        # Show every complete received line, including the statistics tables.
        text = ''.join(c for c in text if c.isprintable() or c == '\t')
        row = f'{time.monotonic()-self.start:7.2f}  {name:8} {direction} {text}'
        self.stream.write(row + '\n')
        self.stream.flush()
        colour = '\033[36m' if name == 'USR' else '\033[35m' if name == 'slmodem' else '\033[33m'
        print((colour + row + '\033[0m') if self.colour else row, flush=True)

    def stage(self, text):
        self.show('DEMO', '*', text)


class Modem:
    def __init__(self, name, fd, session):
        self.name, self.fd, self.session = name, fd, session
        self.text, self.rx, self.pending = '', b'', b''
        self.connected = False

    def send(self, data):
        if isinstance(data, str):
            data = data.encode('ascii')
        self.session.console.show(self.name, '>', data.decode('ascii').rstrip('\r\n'))
        sent = 0
        deadline = time.monotonic() + 5
        while sent < len(data):
            if time.monotonic() >= deadline:
                raise TimeoutError(f'{self.name}: terminal write timed out')
            self.session.pump(.01)
            try:
                sent += os.write(self.fd, data[sent:sent+64])
            except BlockingIOError:
                pass

    def drain(self):
        try:
            data = os.read(self.fd, 4096)
        except BlockingIOError:
            return
        if not data:
            return
        self.rx += data
        self.text += data.decode('ascii', errors='replace')
        self.pending += data
        while b'\r' in self.pending or b'\n' in self.pending:
            index = min(self.pending.index(c) for c in (b'\r', b'\n') if c in self.pending)
            line, self.pending = self.pending[:index], self.pending[index+1:]
            text = line.decode('ascii', errors='replace').strip()
            if text:
                self.session.console.show(self.name, '<', text)
                if re.match(r'^CONNECT(?:\s|$)', text):
                    self.connected = True
                elif text == 'NO CARRIER':
                    self.connected = False


class Session:
    def __init__(self, console):
        self.console, self.ends = console, []

    def pump(self, seconds, until=None):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ready, _, _ = select.select([e.fd for e in self.ends], [], [], min(.05, max(0, deadline-time.monotonic())))
            for end in self.ends:
                if end.fd in ready:
                    end.drain()
            if until and until():
                return True
        return bool(until and until())

    def query(self, end, command, timeout=4):
        self.pump(.05)
        start = len(end.text)
        end.send(command + '\r')
        self.pump(timeout, lambda: bool(re.search(r'(?:^|[\r\n])(?:OK|ERROR)(?:[\r\n]|$)', end.text[start:])))
        self.pump(.15)
        return end.text[start:]

    def command(self, end, command, timeout=4):
        reply = self.query(end, command, timeout)
        if not re.search(r'\bOK\b', reply) or re.search(r'\bERROR\b', reply):
            raise RuntimeError(f'{end.name}: {command} did not return OK')
        return reply

    def transfer(self, sender, receiver, line, timeout):
        payload = line.encode('ascii') + b'\r\n'
        receiver.rx = b''
        sender.send(payload)
        self.pump(timeout, lambda: payload in receiver.rx or not all(e.connected for e in self.ends))
        if payload not in receiver.rx:
            raise RuntimeError(f'{sender.name} -> {receiver.name}: payload did not arrive intact')
        return dict(sender=sender.name, receiver=receiver.name, bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(), exact=True)


def stereo(rx, tx):
    """Interleave captured little-endian PCM without feeding it to the modem."""
    left, right = array.array('h'), array.array('h')
    left.frombytes(rx)
    right.frombytes(tx)
    output = array.array('h', [0]) * (len(left)*2)
    output[0::2], output[1::2] = left, right
    return output.tobytes()


class NegotiationAudio:
    def __init__(self, folder, backend, device):
        self.folder, self.backend, self.device = folder, backend, device
        self.stop_event = threading.Event()
        self.frames, self.error = 0, None
        self.proc = self.thread = None

    def start(self):
        if self.backend == 'off':
            return
        backend = self.backend
        if backend == 'auto':
            backend = 'pulse' if shutil.which('paplay') else 'alsa'
        if backend == 'pulse':
            cmd = ['paplay', '--raw', '--format=s16le', '--rate=8000', '--channels=2', '--latency-msec=100', '--client-name=D-Modem demo']
            if self.device:
                cmd += ['--device', self.device]
        else:
            cmd = ['aplay', '-q', '-t', 'raw', '-f', 'S16_LE', '-r', '8000', '-c', '2']
            if self.device:
                cmd += ['-D', self.device]
        self.audio_log = (self.folder/'audio-player.log').open('wb')
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=self.audio_log,
                                     stderr=self.audio_log)
        time.sleep(.2)
        if self.proc.poll() is not None:
            self.close()
            raise RuntimeError('audio player failed; see audio-player.log or select --audio/--audio-device')
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        inputs = []
        try:
            with wave.open(str(self.folder/'negotiation-stereo.wav'), 'wb') as recording:
                recording.setparams((2, 2, 8000, 0, 'NONE', 'not compressed'))
                while not self.stop_event.is_set():
                    paths = [self.folder/f'modem_{side}_8k.raw' for side in ('rx', 'tx')]
                    if len(inputs) != 2:
                        if not all(p.exists() for p in paths):
                            self.stop_event.wait(.02)
                            continue
                        inputs = [p.open('rb') for p in paths]
                    available = min(os.fstat(f.fileno()).st_size-f.tell() for f in inputs)
                    count = min(320, available//2*2)
                    if not count:
                        self.stop_event.wait(.01)
                        continue
                    data = stereo(*(f.read(count) for f in inputs))
                    recording.writeframesraw(data)
                    self.proc.stdin.write(data)
                    self.proc.stdin.flush()
                    self.frames += count//2
        except Exception as exc:
            if not self.stop_event.is_set():
                self.error = str(exc)
        finally:
            for f in inputs:
                f.close()

    def close(self):
        self.stop_event.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.thread:
            self.thread.join(timeout=2)
        if self.proc:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            self.audio_log.close()


def hangup(session, sw, hw):
    # Only escape slmodem. Let the USR record the remote cleardown first.
    if sw.connected:
        session.pump(1.2)
        sw.send(b'+++')
        session.pump(1.3)
    try:
        session.command(sw, 'ATH')
    except RuntimeError:
        # Input can abort an incomplete handshake without returning OK first.
        session.command(sw, 'ATH')
    session.pump(8, lambda: not hw.connected)
    if hw.connected:
        session.pump(1.2)
        hw.send(b'+++')
        session.pump(1.3)
        session.command(hw, 'ATH')
    elif not any('CONNECT' in end.text for end in session.ends):
        session.command(hw, 'ATH')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--tty', default=USR_TTY)
    ap.add_argument('--dial', default='4242', help='internal SIP extension called by the USR')
    ap.add_argument('--secret', type=Path, default=Path(os.environ.get('DMODEM_SECRET', ROOT.parent/'asterisk-login-4242.secret')))
    ap.add_argument('--dsp-root', type=Path, default=Path(os.environ.get('DSP_ROOT', ROOT.parent/'slmodem_dsp_re')))
    ap.add_argument('--dsp', choices=('original', 'reconstructed'), default='reconstructed')
    ap.add_argument('--song', type=Path, default=ROOT/'demo/major-general.txt')
    ap.add_argument('--out', type=Path, default=ROOT/'scratch'/time.strftime('demo-%Y%m%d-%H%M%S'))
    ap.add_argument('--line-delay', type=float, default=.5)
    ap.add_argument('--wait', type=float, default=90)
    ap.add_argument('--audio', choices=('auto', 'pulse', 'alsa', 'off'), default='auto')
    ap.add_argument('--audio-device')
    ap.add_argument('--no-build', action='store_true')
    ap.add_argument('--plain', action='store_true')
    ap.add_argument('--extended-stats', action='store_true')
    args = ap.parse_args()
    if args.line_delay < 0 or args.wait <= 0:
        ap.error('line delay must be nonnegative and wait must be positive')
    if not args.no_build and args.dsp == 'reconstructed' and not (args.dsp_root/'Makefile').is_file():
        ap.error('--dsp-root must point to the reconstruction checkout when building; use --no-build for prepared binaries')
    if not args.secret.is_file():
        ap.error('SIP credential file not found; select it with --secret /absolute/path/to/account.secret')
    os.environ['DSP_ROOT'] = str(args.dsp_root.resolve())
    os.environ.setdefault('SLMODEMD_DSP_DEBUG', '0')
    from modem_test import start_daemon, save_audio
    from terminal_io import open_raw
    from modem_diagnostics import collect
    song = [s for s in args.song.read_text(encoding='ascii').splitlines() if s.strip()]
    if len(song) < 2:
        ap.error('song must contain at least two non-empty ASCII lines')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    result = dict(status='failed', payload=[], audio_requested=args.audio != 'off')
    proc = audio = None
    ends = []
    with (out/'transcript.log').open('w') as transcript, (out/'slmodemd.log').open('w') as daemon_log:
        console = Console(transcript, args.plain)
        session = Session(console)
        try:
            console.stage('USR + slmodem | > sent to modem, < received from modem')
            if not args.no_build:
                console.stage('Building the selected DSP and SIP endpoint; details in build.log')
                with (out/'build.log').open('w') as build_log:
                    subprocess.run(['make', '-C', str(ROOT), f'DSP={args.dsp}',
                                    f'DSP_ROOT={args.dsp_root.resolve()}'],
                                   stdout=build_log, stderr=subprocess.STDOUT, check=True)
            config = SimpleNamespace(dsp=args.dsp, secret=args.secret.resolve(), device_num=57)
            proc, pty, binary = start_daemon(config, out, daemon_log)
            result['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
            for name, path in [('USR', args.tty), ('slmodem', pty)]:
                fd = open_raw(path)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                ends.append(Modem(name, fd, session))
            session.ends = ends
            hw, sw = ends
            fcntl.ioctl(hw.fd, termios.TIOCMBIS,
                        struct.pack('I', termios.TIOCM_RTS | termios.TIOCM_DTR))
            console.stage('Identify the USRobotics modem and prepare both ends')
            session.command(hw, 'AT')
            identity = session.command(hw, 'ATI3')
            if not re.search(r'USRobotics|Courier', identity, re.I):
                raise RuntimeError('the selected serial port is not the USRobotics modem')
            result['usr_identity'] = identity
            session.command(hw, 'AT&F')
            session.command(hw, 'AT&B1')
            session.command(hw, 'AT&A3')
            session.command(sw, 'ATZ')
            for end in ends:
                for command in ('ATE0Q0V1', 'AT&D0'):
                    session.command(end, command)
            for command in ('AT&H0', 'AT&I0', 'AT&R1', 'ATX3', 'ATM0', 'ATS0=0'):
                session.command(hw, command)
            for command in ('AT+MS=34,1', 'ATS70=7', 'ATL0', 'ATS0=1'):
                session.command(sw, command)
            session.pump(3)
            console.stage('Dial and negotiate | audio left: USR -> slmodem; right: slmodem -> USR')
            audio = NegotiationAudio(out, args.audio, args.audio_device)
            audio.start()
            hw.send('ATDT' + args.dial + '\r')
            connected = session.pump(args.wait, lambda: all(e.connected for e in ends) or
                any('NO CARRIER' in e.text for e in ends))
            audio.close()
            result['audio_frames_per_channel'] = audio.frames
            if not connected or not all(e.connected for e in ends):
                raise RuntimeError('both modems did not CONNECT; retain the logs and retry the demo')
            if args.audio != 'off' and (audio.error or not audio.frames):
                raise RuntimeError('negotiation playback failed: ' + (audio.error or 'no captured audio'))
            session.pump(1)
            console.stage("The Major-General's Song | alternating directions, verified line by line")
            for index, line in enumerate(song):
                sender, receiver = ends if index % 2 == 0 else ends[::-1]
                result['payload'].append(session.transfer(sender, receiver, line, 15))
                session.pump(args.line_delay)
            console.stage('Hang up, then read the USR last-call statistics before any reset')
            hangup(session, sw, hw)
            report = collect('usr', lambda cmd: session.query(hw, cmd) if
                             args.extended_stats or cmd in ('ATI6', 'ATI11') else None)
            (out/'usr-statistics.json').write_text(json.dumps(report, indent=2)+'\n')
            if any(report['queries'][cmd]['status'] != 'supported' for cmd in ('ATI6', 'ATI11')):
                raise RuntimeError('USR did not return both last-call reports')
            result['status'] = 'passed'
            console.stage(f"Verified {len(song)} lines / {sum(p['bytes'] for p in result['payload'])} bytes. Reports: {out}")
        except KeyboardInterrupt:
            result['status'], result['error'] = 'cancelled', 'interrupted'
            console.stage('Interrupted; hanging up and releasing the demo devices')
        except Exception as exc:
            result['error'] = str(exc)
            console.stage('DEMO FAILED: ' + str(exc))
        finally:
            if audio:
                audio.close()
            if len(ends) == 2 and (result['status'] != 'passed' or any(e.connected for e in ends)):
                try:
                    hangup(session, ends[1], ends[0])
                except Exception as exc:
                    result['cleanup_error'] = str(exc)
            if proc:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    proc.wait(timeout=5)
                except ProcessLookupError:
                    pass
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            for end in ends:
                os.close(end.fd)
            save_audio(out)
            (out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
