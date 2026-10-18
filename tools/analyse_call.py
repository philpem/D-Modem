#!/usr/bin/env python3
"""Decode DSP diagnostics and V.8 audio; extract probe and echo evidence."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import wave

os.environ.setdefault('MPLCONFIGDIR', '/tmp/slmodem-matplotlib')
from integration_paths import ROOT, DSP_ROOT
sys.path.insert(0, str(DSP_ROOT / 'tools'))
from eddecode import decode_line
sys.path.pop(0)
from v8bis_trace import detect as v8bis_detect
from negotiation import analyse as negotiation_analyse
from local_ec import analyse as ec_analyse
from v34_info import analyse as info0_analyse, render_text as info0_text
sys.path.insert(0, str(DSP_ROOT / 'testbench'))
from capture_io import load


def analyse(out, role):
    rawlog = (out / 'slmodemd.log').read_text(encoding='latin1')
    text = ''.join(decode_line(line) for line in rawlog.splitlines(keepends=True))
    (out / 'dsp-readable.log').write_text(text, encoding='latin1')
    # Keep the original timestamps, including retransmissions and failures.
    trace = [line for line in text.splitlines() if re.search(
        r'V8[:_ ]|v8:|v8shim:|V34|V90|V92|echo|Echo|[Ee]rror|[Tt]ime.?[Oo]ut|[Uu]nwritten', line)]
    (out / 'dsp-trace.log').write_text('\n'.join(trace)+'\n', encoding='latin1')
    summary = dict(log_lines=len(text.splitlines()), trace_lines=len(trace),
        encoded_frames=rawlog.count('$!$ '),
        v8_state_changes=len(re.findall('V8: State changed', text)),
        v8bis_note='Signal tones and CRC-checked capability frames are decoded independently.',
        debug_configuration=re.findall(r'DSP diagnostics:.*', text),
        diagnostics=[line for line in trace if re.search(
            r'[Tt]ime.?[Oo]ut|[Uu]nwritten|Link Error|[Ee]rrorrecovery|[Ee]rror recovery', line)],
        echo_messages=[line for line in trace if 'echo' in line.lower()])
    for direction in ('rx', 'tx'):
        wav = out / f'modem_{direction}_8k.wav'
        if not wav.exists():
            continue
        with wave.open(str(wav), 'rb') as reader:
            if (reader.getframerate(), reader.getnchannels(), reader.getsampwidth()) != (8000, 1, 2):
                raise ValueError('V.8 decoder requires mono 16-bit 8 kHz PCM')
            audio = reader.readframes(reader.getnframes())
        x, rate = load(str(wav))
        info0 = info0_analyse(x, rate)
        (out/f'info0-{direction}.json').write_text(json.dumps(info0,indent=2)+'\n')
        summary[f'info0_{direction}'] = {key:value for key,value in info0.items() if key != 'frames'}
        bis = (v8bis_detect(x, rate) if len(x) >= rate*.1 else
               dict(samples_scanned=len(x), signals_detected=0, error='capture too short'))
        (out / f'v8bis-{direction}.json').write_text(json.dumps(bis, indent=2)+'\n')
        summary[f'v8bis_{direction}'] = bis
        with tempfile.NamedTemporaryFile(dir=out, suffix='.raw') as tmp:
            tmp.write(audio); tmp.flush()
            observer = (role if direction == 'rx' else
                        ('answerer' if role == 'caller' else 'caller'))
            for name, argv in [
                ('v8', [str(ROOT / 'scratch/v8analyse'), tmp.name, observer]),
                ('v21', [sys.executable, str(DSP_ROOT / 'testbench/v21decode.py'),
                         tmp.name, '8000',
                         'ch1' if observer == 'answerer' else 'ch2'])]:
                proc = subprocess.run(argv, capture_output=True, text=True)
                (out / f'{name}-{direction}.log').write_text(proc.stdout+proc.stderr)
                summary[f'{name}_{direction}'] = dict(exit_code=proc.returncode,
                    samples=len(audio)//2, output_lines=len((proc.stdout+proc.stderr).splitlines()))
    wav = out / 'modem_rx.wav'
    if wav.exists():
        proc = subprocess.run([sys.executable, str(ROOT / 'tools/v34_response.py'),
            str(wav), '--out', str(out / 'v34-response')], capture_output=True, text=True,
            env={**os.environ, 'OPENBLAS_NUM_THREADS': '1'})
        (out / 'v34-response.log').write_text(proc.stdout+proc.stderr)
        summary['v34_response'] = dict(exit_code=proc.returncode,
                                      graph_created=proc.returncode == 0)
    # The coefficient report is not an ERLE or residual-echo measurement.
    # Preserve every report (near and far can be printed repeatedly).
    reports = []
    current = None
    for line in text.splitlines():
        match = re.search(r'==== (Near|Far) Echo Canceller report', line)
        if match:
            current = dict(kind=match[1].lower(), coefficients=[])
            reports.append(current)
        taps = re.search(r'\?(-?\d+(?: +-?\d+){5})\s*$', line)
        if current is not None and taps:
            current['coefficients'].extend(map(int, taps[1].split()))
    summary['echo_coefficient_reports'] = reports
    hardware = out / 'hardware-last-call.txt'
    serial = out / 'call.log'
    hardware_text = (hardware.read_text(errors='replace') if hardware.exists() else
                     serial.read_text(errors='replace') if serial.exists() else '')
    metrics = {key: float(value) for key, value in re.findall(
        r'(SNR|Near Echo Loss|Far Echo Loss)\s+\( dB \)\s+(-?[\d.]+)', hardware_text)}
    summary['hardware_report'] = dict(perspective='far-end hardware modem', metrics_db=metrics,
        plausible=bool(metrics) and all(-20 <= value <= (80 if key == 'SNR' else 100)
                                       for key, value in metrics.items()))
    if any(r['coefficients'] for r in reports):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, 4))
        for i, report in enumerate(reports):
            coeff = report['coefficients']
            if coeff:
                ax.plot(coeff, label=f"{report['kind']} report {i+1}")
        ax.set(xlabel='Tap index', ylabel='Raw coefficient', title='DSP echo canceller coefficients')
        ax.legend(); fig.tight_layout(); fig.savefig(out / 'echo-coefficients.png', dpi=150)
        plt.close(fig)
    negotiation = negotiation_analyse(out, role)
    with (out/'negotiation.txt').open('a') as readable:
        readable.write('\nV.34 INFO0 capability and recovery frames\n')
        for direction in ('rx','tx'):
            path=out/f'info0-{direction}.json'
            if path.exists():
                readable.write(f'\n{direction.upper()} recording:\n'+info0_text(json.loads(path.read_text())))
    summary['negotiation'] = {k:v for k,v in negotiation.items() if k != 'observations'}
    summary['local_ec'] = ec_analyse(out)
    (out / 'analysis.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps({k: v for k, v in summary.items()
                      if k not in ('echo_messages', 'echo_coefficient_reports')}, indent=2))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('directory', type=Path)
    ap.add_argument('--role', choices=('caller', 'answerer'), default='caller')
    args = ap.parse_args()
    analyse(args.directory.resolve(), args.role)


if __name__ == '__main__':
    main()
