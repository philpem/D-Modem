#!/usr/bin/env python3
"""Compile/run the 32-bit synthetic observer forwarding and recording controls."""
import csv
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    from integration_paths import ROOT as root, DSP_ROOT
    (root/'scratch').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='hs-wrapper-',dir=root/'scratch') as tmp:
        out=Path(tmp);binary=out/'fixture'
        subprocess.run(['gcc','-m32','-O2','-fno-pie','-no-pie',
            '-I'+str(DSP_ROOT/'include'),str(root/'tools/handshake_probe.c'),
            str(root/'tools/test_handshake_probe.c'),'-o',str(binary)],check=True)
        for enabled in (False,True):
            folder=out/str(int(enabled));folder.mkdir()
            subprocess.run([str(binary)],check=True,env={**os.environ,
                'SLMODEMD_HANDSHAKE_PROBE':str(int(enabled)),
                'SLMODEMD_CAPTURE_DIR':str(folder)})
            output=folder/'handshake-state.csv'
            if not enabled:
                assert not output.exists();continue
            with output.open() as stream:
                header=stream.readline();rows=list(csv.DictReader(stream))
            assert 'rows=3 dropped=0 handshake_calls=1' in header
            assert [r['stage'] for r in rows]==['0','1','2']
            assert rows[0]['microstate']=='46' and rows[1]['microstate']=='47'
            assert rows[1]['sr']=='15' and rows[1]['nbits']=='2'
            assert all(rows[1][f'input{i}']=='0' for i in range(4))
    print('PASS: synthetic forwarding/state preservation; enabled 3 rows, disabled 0 files; not DSP reachability evidence')


if __name__=='__main__':main()
