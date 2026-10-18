#!/usr/bin/env python3
"""Compare EC observation on/off on identical recorded input, without a live call.

This reuses the upstream blocking audio replay peer. It is an open-loop
apparatus check: the recording cannot respond to this run's transmitted audio.
It does not establish a negotiated rate or modem interoperability.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import time
from types import SimpleNamespace
from integration_paths import DSP_ROOT
from modem_test import ROOT, call, command, save_audio, start_daemon


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('wav',type=Path)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--wait',type=float,default=10,help='wall-clock seconds to allow each blocking replay')
    args = ap.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    os.environ.update(REPLAY_WAV=str(args.wav.resolve()),REPLAY_PAD='1')
    results = []
    for label,dsp,enabled in [('plain','reconstructed','0'),
                              ('probe-off','reconstructed-ec','0'),
                              ('probe-on','reconstructed-ec','1')]:
        directory = (args.out/label).resolve(); directory.mkdir()
        os.environ['SLMODEMD_EC_PROBE']=enabled
        settings = SimpleNamespace(dsp=dsp,secret=None,
                                   executor=DSP_ROOT/'testbench/replay.py')
        with (directory/'slmodemd.log').open('w') as log, (directory/'call.log').open('w') as serial:
            proc,pty,_ = start_daemon(settings,directory,log)
            end = call.End('dmodem',call.open_raw(pty),serial)
            try:
                call.T0=time.time()
                for text in ('ATZ','ATE0Q0V1','AT+MS=34,1','ATS70=7'):
                    command(end,[end],text)
                end.send('ATA\r')
                call.pump([end],args.wait,until=lambda es:'NO CARRIER' in end.text)
            finally:
                os.close(end.fd)
                os.killpg(proc.pid,signal.SIGTERM)
                proc.wait(timeout=5)
        save_audio(directory)
        p = directory/'modem_tx.wav'
        results.append(dict(build=label,bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    # Compare the samples, allowing only a different WAV container header.
    import wave
    audio = []
    for result in results:
        with wave.open(str(args.out/result['build']/'modem_tx.wav'),'rb') as wav:
            audio.append(wav.readframes(wav.getnframes()))
    report = dict(open_loop=True, runs=results,
                  exact_tx_pcm_equal=audio[0]==audio[1]==audio[2])
    (args.out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return 0 if report['exact_tx_pcm_equal'] else 1


if __name__=='__main__':
    raise SystemExit(main())
