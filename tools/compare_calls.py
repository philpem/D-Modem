#!/usr/bin/env python3
"""Collect exact-data results and overlay received V.34 responses from several calls."""
import argparse
import csv
import json
import os
from pathlib import Path
import re

os.environ.setdefault('MPLCONFIGDIR', '/tmp/slmodem-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def modem_name(result):
    identity = ' '.join(result.get('hardware', {}).values())
    if 'USRobotics' in identity or 'Courier' in identity:
        return 'Courier'
    if "Oli'Net" in identity or 'Olinet' in identity:
        return 'Olinet'
    return result.get('tty', 'Unknown modem').split('/')[-1]


def plot(curves, output, title, limits, colors):
    fig, axes = plt.subplots(2, 1, sharex=True, figsize=(12, 8))
    for name, modem, values in curves:
        for i, ax in enumerate(axes):
            ax.plot(values[:, 0], values[:, i+1], 'o-', ms=3, label=name,color=colors[name])
    axes[0].set_ylabel('Magnitude relative to 750 Hz (dB)')
    axes[1].set_ylabel('Phase, delay removed (degrees)')
    axes[1].set_xlabel('Frequency (Hz)')
    axes[1].set_xticks(np.arange(150, 3751, 150))
    axes[1].set_xlim(100, 3800)
    axes[1].tick_params(axis='x', labelrotation=60, labelsize=9)
    for ax, ylim in zip(axes, limits):
        ax.set_ylim(ylim)
        ax.grid(True, alpha=.3)
        ax.legend(fontsize=8)
    fig.suptitle(title)
    fig.text(.5,.01,'Markers are measured tones; 900, 1200, 1800 and 2400 Hz are deliberately unexcited.',
             ha='center',fontsize=9)
    fig.tight_layout(rect=(0,.03,1,1))
    fig.savefig(output, dpi=150)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('calls', nargs='+', type=Path)
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--title', default='Received V.34 line probe; complete inbound path')
    ap.add_argument('--split-modems', action='store_true',
                    help='also plot each modem separately, with the same axis scales')
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    summaries, curves, curve_names = [], [], []
    grouped_curves = []
    for directory in args.calls:
        result = json.loads((directory / 'result.json').read_text())
        log = (directory / 'call.log').read_text(errors='replace')
        summary = dict(call=directory.name, dsp=result.get('dsp'), status=result['status'],
                       modem=modem_name(result),
                       connected=result['connected'], payload=result['payload'],
                       tx_rate=re.findall(r'dmodem << TxRate: (\d+)', log),
                       rx_rate=re.findall(r'dmodem << CONNECT (\d+)', log))
        lastcall = directory / 'hardware-last-call.txt'
        if lastcall.exists():
            summary['hardware_diagnostics'] = dict(re.findall(
                r'(SNR|Near Echo Loss|Far Echo Loss)\s+\( dB \)\s+([\d.]+)', lastcall.read_text()))
        analysis = directory / 'analysis.json'
        if analysis.exists():
            summary['hardware_report'] = json.loads(analysis.read_text()).get('hardware_report')
        summaries.append(summary)
        path = directory / 'v34-response.csv'
        if not path.exists():
            continue
        with path.open() as stream:
            rows = list(csv.DictReader(stream))
        values = np.array([[float(r[k]) for k in ('hz', 'db_relative_750', 'phase_delay_removed_deg')]
                           for r in rows])
        curves.append(values)
        curve_names.append(directory.name)
        grouped_curves.append((directory.name, summary['modem'], values))
    graphs = []
    if curves:
        limits = []
        for column in (1, 2):
            low = min(c[:, column].min() for c in curves)
            high = max(c[:, column].max() for c in curves)
            margin = max((high-low)*.08, .1)
            limits.append((low-margin, high+margin))
        filename = 'combined-response.png' if args.split_modems else 'response.png'
        palette = plt.rcParams['axes.prop_cycle'].by_key()['color']
        colors = {name:palette[i%len(palette)] for i,(name,_,_) in enumerate(grouped_curves)}
        plot(grouped_curves, args.out / filename, args.title, limits, colors)
        graphs.append(filename)
        if args.split_modems:
            for modem in sorted({c[1] for c in grouped_curves}):
                filename = re.sub(r'[^a-z0-9]+', '-', modem.lower())+'-response.png'
                plot([c for c in grouped_curves if c[1] == modem], args.out / filename,
                     modem+' — '+args.title, limits, colors)
                graphs.append(filename)
    report = dict(calls=summaries, graphs=len(graphs), response_curves=len(curves), requested_calls=len(args.calls))
    report['graph_files'] = graphs
    report['frequency_tick_hz'] = 150
    if curves:
        report['shared_y_limits'] = limits
    if len(curves) >= 2 and all(np.array_equal(curves[0][:,0], c[:,0]) for c in curves):
        report['differences_from_first'] = [dict(
            call=curve_names[i],
            magnitude_rms_db=float(np.sqrt(np.mean((c[:,1]-curves[0][:,1])**2))),
            phase_rms_degrees=float(np.sqrt(np.mean((c[:,2]-curves[0][:,2])**2))))
            for i, c in enumerate(curves[1:], 1)]
    report['within_modem_differences'] = {}
    for modem in sorted({c[1] for c in grouped_curves}):
        group = [c for c in grouped_curves if c[1]==modem]
        baseline = group[0]
        report['within_modem_differences'][modem] = dict(reference=baseline[0],differences=[dict(
            call=name,magnitude_rms_db=float(np.sqrt(np.mean((values[:,1]-baseline[2][:,1])**2))),
            phase_rms_degrees=float(np.sqrt(np.mean((values[:,2]-baseline[2][:,2])**2))))
            for name,_,values in group[1:] if np.array_equal(values[:,0],baseline[2][:,0])])
    (args.out / 'results.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    return 0 if curves else 1


if __name__ == '__main__':
    raise SystemExit(main())
