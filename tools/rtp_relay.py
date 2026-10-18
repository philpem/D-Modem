#!/usr/bin/env python3
"""Run the existing packet-preserving hardware control relay with external credentials.

Example: rtp_relay.py --secret /path/to/account.secret --dial 1901 --rec scratch/relay
Then have the Olinet dial that account (4242) and the Courier answer.
This adds two VG204 RTP legs; a failure alone does not isolate a single leg.
"""
import argparse
from pathlib import Path
import signal
import sys

from integration_paths import ROOT, DSP_ROOT
sys.path.insert(0, str(DSP_ROOT/'testbench'))
import rtprelay


class Relay(rtprelay.Relay):
    unregistering = False

    def say(self, msg):
        if self.unregistering and msg.startswith('REGISTERED'):
            msg = msg.replace('REGISTERED','UNREGISTERED',1)
        super().say(msg)

    def send(self, msg):
        if self.unregistering and msg.startswith('REGISTER '):
            msg = msg.replace('Expires: 300\r\n', 'Expires: 0\r\n')
        super().send(msg)


def main():
    # Reuse the upstream SIP and RTP implementations. Never pass credentials
    # on the command line or print the credential file or parsed values.
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--secret', required=True, type=Path)
    ap.add_argument('--dial', required=True, choices=('1901', '1903'))
    ap.add_argument('--hold', type=float, default=120)
    ap.add_argument('--wait-inbound', type=float, default=60)
    ap.add_argument('--rec', help='prefix for per-direction a-law recordings')
    ap.add_argument('--port', type=int, default=5080)
    ap.add_argument('--rtp-a', type=int, default=4200)
    ap.add_argument('--rtp-b', type=int, default=4202)
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    rtprelay.SECRET = str(args.secret.resolve())
    server, extension, password = rtprelay.read_secret()
    relay = Relay(server, extension, password, args.port, args.rtp_a, args.rtp_b, None)
    del password
    relay._chal = None
    registered = False
    try:
        registered = relay.register()
        if not registered:
            return 2
        if not relay.wait_inbound(args.wait_inbound):
            relay.say('no inbound call')
            return 3
        if not relay.invite(args.dial):
            return 4
        relay.forward(args.hold, args.rec)
        return 0 if all(side['rx'] for side in relay.rtp.values()) else 5
    finally:
        relay.bye()
        if registered:
            relay.unregistering = True
            if not relay.register():
                relay.say('registration removal failed')
        relay.sip.close()
        for side in relay.rtp.values():
            side['sock'].close()


if __name__ == '__main__':
    sys.exit(main())
