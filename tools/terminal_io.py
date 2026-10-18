"""Persistent raw-terminal I/O shared by integration runners.

Adapted from philpem/slmodem_dsp_re testbench/call.py at 28ccee0669a7.
Keep the reusable helpers independent of that command-line script's entry point.
"""
import os
import select
import sys
import termios
import time


def open_raw(path, speed=termios.B115200):
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    a = termios.tcgetattr(fd)
    a[0] = 0                                     # iflag: no xon/xoff, no cr/nl
    a[1] = 0                                     # oflag: no post-processing
    a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    a[3] = 0                                     # lflag: raw, no echo
    a[4] = a[5] = speed
    a[6][termios.VMIN] = 0
    a[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, a)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd


class End:
    """One end of the call: a file descriptor plus its line-assembly buffer."""

    def __init__(self, name, fd, log):
        self.name, self.fd, self.log = name, fd, log
        self.buf = b""
        self.text = ""          # everything seen, for CONNECT/CARRIER tests
        self.connected = False
        self.rx = b""           # payload seen after CONNECT, for the data test

    def send(self, s):
        os.write(self.fd, s.encode() if isinstance(s, str) else s)
        self.log.write("%8.3f %s >> %r\n" % (time.time() - T0, self.name, s))
        self.log.flush()

    def drain(self):
        try:
            c = os.read(self.fd, 4096)
        except OSError:
            return
        if not c:
            return
        self.text += c.decode(errors="replace")
        if self.connected:
            self.rx += c
        self.buf += c
        while b"\r" in self.buf or b"\n" in self.buf:
            i = min((self.buf.index(b) for b in (b"\r", b"\n") if b in self.buf))
            line, self.buf = self.buf[:i], self.buf[i + 1:]
            line = line.decode(errors="replace").strip()
            if line:
                stamp = time.time() - T0
                self.log.write("%8.3f %s << %s\n" % (stamp, self.name, line))
                self.log.flush()
                print("   %7.2f %-5s %s" % (stamp, self.name, line[:100]))
                sys.stdout.flush()
                if "CONNECT" in line:
                    self.connected = True


def pump(ends, seconds, until=None):
    """Read both ends for `seconds`, or until `until(ends)` returns True."""
    end_at = time.time() + seconds
    while time.time() < end_at:
        r, _, _ = select.select([e.fd for e in ends], [], [], 0.2)
        for e in ends:
            if e.fd in r:
                e.drain()
        if until and until(ends):
            return True
    return False


T0 = time.time()
