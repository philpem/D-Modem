#!/usr/bin/env python3
"""Prove payload transfer tolerates short, nonblocking terminal writes."""
import io
import os
import socket
from unittest.mock import patch
from types import SimpleNamespace
from modem_test import terminal_failure, transfer_payloads, write_payload


class End:
    def __init__(self,name,sock):
        self.name,self.fd,self.sock = name,sock.fileno(),sock
        self.rx=b''; self.log=io.StringIO(); self.text=''

    def drain(self):
        try:
            self.rx += self.sock.recv(4096)
        except BlockingIOError:
            pass


def main():
    for status in ('BUSY','NO CARRIER','NO DIALTONE','NO DIAL TONE'):
        assert terminal_failure('\r\n'+status+'\r\n')
    assert not terminal_failure('OK\r\nCONNECT 33600\r\n')
    a,b=socket.socketpair()
    try:
        a.setblocking(False); b.setblocking(False)
        sender,receiver=End('sender',a),End('receiver',b)
        payload=bytes(range(256))*20
        real_write=os.write
        counts=[]
        def short_write(fd,data):
            # Include EAGAIN as well as short successful writes.
            if len(counts)%9==8:
                counts.append(0); raise BlockingIOError()
            n=real_write(fd,data[:7]); counts.append(n); return n
        with patch('modem_test.os.write',side_effect=short_write):
            sent=write_payload(sender,payload,[sender,receiver],3)
        receiver.drain()
        assert sent==len(payload) and receiver.rx==payload
        assert len(counts)>700 and 0 in counts
        print(f'PASS: {sent} exact bytes over {len(counts)} writes, including short writes and EAGAIN')
        args = SimpleNamespace(bytes=64, data_wait=.2, payload_bps=0)
        before = transfer_payloads([sender, receiver], args)
        after = transfer_payloads([sender, receiver], args, 'POST-HOLD')
        assert all(row['exact'] for group in (before, after) for row in group.values())
        assert all(before[key]['sha256'] != after[key]['sha256'] for key in before)
        def corrupt_write(fd, data):
            return real_write(fd, bytes([data[0] ^ 1]) + data[1:])
        with patch('modem_test.os.write', side_effect=corrupt_write):
            damaged = transfer_payloads([sender, receiver], args, 'POST-HOLD')
        assert all(not row['exact'] and row['received'] == row['sent']
                   for row in damaged.values())
        print('PASS: fresh payloads both ways; full-length corrupted post-hold data rejected')
    finally:
        a.close(); b.close()


if __name__=='__main__':
    main()
