#!/usr/bin/env python3
"""slmodemd -e wrapper: load external SIP credentials directly into the environment."""
import os
from pathlib import Path
import sys

from integration_paths import ROOT


def main():
    if not os.environ.get('DMODEM_SELFTEST_ID'):
        path = Path(os.environ.get('DMODEM_SECRET',
                    str(ROOT.parent / 'asterisk-login-4242.secret')))
        fields = {}
        # Never print the file, values, or environment; passwords stay out of argv.
        for line in path.read_text().splitlines():
            key, sep, value = line.partition(':')
            if sep:
                fields[key.strip().lower()] = value.strip()
        for field, env in [('server', 'SIP_SERVER'), ('extension', 'SIP_USER'),
                           ('secret', 'SIP_PASSWORD')]:
            if not fields.get(field):
                sys.exit('SIP credential file is missing a required field')
            os.environ[env] = fields[field]
    binary = ROOT / 'd-modem'
    os.execv(str(binary), [str(binary), *sys.argv[1:]])


if __name__ == '__main__':
    main()
