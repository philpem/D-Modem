#!/usr/bin/env python3
"""Measure the received V.34 L1/L2 probe's magnitude and relative phase.

The reference is the reconstructed standard 64-sample probe, not our own
transmit recording (which contains the opposite direction of the call).
This measures the complete inbound path, including the far modem and ATA.
Absolute gain, propagation delay, and constant phase are not identifiable.
"""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import sys

os.environ.setdefault('MPLCONFIGDIR', '/tmp/slmodem-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import minimize_scalar

from integration_paths import ROOT, DSP_ROOT
sys.path.insert(0, str(DSP_ROOT / 'testbench'))
from capture_io import load
from probeplot import NOISE_BINS


def reference():
    source = (DSP_ROOT / 'src/pump/v34/V34hshak.c').read_text()
    body = re.search(r'const short probe\[V34_PROBE_SAMPLES\] = \{(.*?)\};',
                     source, re.S).group(1)
    samples = np.array([int(n) for n in re.findall(r'-?\d+', body)])
    if len(samples) != 64:
        raise ValueError('expected the standard 64-sample V.34 probe')
    return samples


def projection(x, rate, fundamental=150.):
    n = np.arange(len(x))
    freq = np.arange(1, 26) * fundamental
    return np.exp(-2j * np.pi * freq[:, None] * n / rate) @ x * (2 / len(x))


def measure(x, rate, start=None, duration=.12):
    if rate != 9600 or x.ndim != 1:
        raise ValueError('use the mono 9600 Hz modem_rx.wav capture')
    n = int(round(duration * rate / 64)) * 64
    active = np.array([i not in NOISE_BINS for i in range(25)])
    f = np.arange(1, 26) * 150.
    candidates = []
    starts = ([int(start * rate)] if start is not None else
              range(0, len(x) - n + 1, 192))
    basis = np.exp(-2j * np.pi * f[:, None] * np.arange(n) / rate) * (2 / n)
    for a in starts:
        z = np.asarray(x[a:a+n], float)
        if len(z) != n:
            continue
        power = np.mean(z*z)
        if power < 30**2:
            continue
        c = basis @ z
        tone_power = abs(c[active])**2
        fraction = tone_power.sum() / (2 * power)
        # Reject sparse tones and broadband data, not just silence. A probe
        # must expose at least 16 of the 21 tones within 30 dB of its peak.
        tones = int(np.sum(tone_power > tone_power.max() * .001))
        if fraction > .65 and tones >= 16:
            candidates.append((fraction, a))
    if not candidates:
        raise ValueError('no qualified V.34 probe; no response graph can be inferred')
    fraction, a = max(candidates)
    z = np.asarray(x[a:a+n], float)
    fit = minimize_scalar(lambda base: -float(np.sum(abs(projection(z, rate, base)[active])**2)),
                          bounds=(149.85, 150.15), method='bounded')
    c = projection(z, rate, fit.x)
    ref = np.fft.rfft(reference())[1:26] * (2 / 64)
    h = c[active] / ref[active]
    hz = f[active]
    gain = 20 * np.log10(np.maximum(abs(h), 1e-12))
    gain -= gain[np.where(hz == 750)[0][0]]
    # Estimate the arbitrary probe origin using adjacent occupied bins first.
    # Unwrapping across a missing tone before removing this delay can choose
    # the wrong 2*pi branch at each 300 Hz gap and invent phase ripple.
    adjacent = (np.diff(hz) == 150) & (hz[:-1] >= 450) & (hz[1:] <= 3150)
    steps = np.angle(h[1:] * h[:-1].conj())[adjacent]
    coarse_slope = np.angle(np.mean(np.exp(1j * steps))) / 150
    phase = np.unwrap(np.angle(h * np.exp(-1j * coarse_slope * hz)))
    slope, intercept = np.polyfit(hz, phase, 1)
    residual = phase - (slope * hz + intercept)
    slope += coarse_slope
    noise = float(np.mean(abs(c[~active])**2))
    rows = [dict(hz=float(ff), db_relative_750=float(g),
                 phase_delay_removed_deg=float(p * 180 / np.pi),
                 tone_to_omitted_bin_db=float(10*np.log10(max(abs(cc)**2, 1e-12) / max(noise, 1e-12))))
            for ff, g, p, cc in zip(hz, gain, residual, c[active])]
    meta = dict(start_seconds=a / rate, duration_seconds=n / rate,
                qualified_windows=len(candidates), samples_scanned=len(x),
                comb_power_fraction=float(fraction),
                clock_offset_ppm=float((fit.x / 150 - 1) * 1e6),
                phase_delay_removed_ms=float(-slope / (2*np.pi)*1000),
                phase_delay_ambiguity_ms=1000/150,
                measurement='inbound end-to-end path; gain relative to 750 Hz; constant and linear phase removed')
    return rows, meta


def selftest():
    from scipy.signal import lfilter
    rng = np.random.default_rng(34)
    template = reference()
    fir = np.array([.7, .2, -.08, .04])
    clean = np.tile(template, 200) * .3
    db_error = phase_error = 0.
    for delay in (0, 7, 23, 53):
        y = np.r_[np.zeros(delay), lfilter(fir, [1], clean)]
        y += rng.normal(0, 1, len(y))
        rows, _ = measure(y, 9600)
        f = np.array([r['hz'] for r in rows])
        truth = np.exp(-2j*np.pi*f[:, None]*np.arange(len(fir))/9600) @ fir
        db = 20*np.log10(abs(truth)); db -= db[np.where(f == 750)[0][0]]
        phase = np.unwrap(np.angle(truth)); phase -= np.polyval(np.polyfit(f, phase, 1), f)
        db_error = max(db_error, float(max(abs(np.array([r['db_relative_750'] for r in rows])-db))))
        phase_error = max(phase_error, float(max(abs(np.array([r['phase_delay_removed_deg'] for r in rows])-phase*180/np.pi))))
        assert db_error < .1, (delay, db_error)
        assert phase_error < .5, (delay, phase_error)
    for name, bad in [('silence', np.zeros(9600)),
                      ('noise', rng.normal(0, 1000, 9600)),
                      ('single_tone', np.sin(np.arange(9600)*2*np.pi*150/9600)*1000)]:
        try:
            measure(bad, 9600)
        except ValueError:
            pass
        else:
            raise AssertionError('false probe on ' + name)
    print(f'PASS: known FIR, 4 delays, 21 tones, max magnitude error {db_error:.4f} dB, '
          f'phase error {phase_error:.4f} degrees; 3 negative controls rejected')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('capture', nargs='?')
    ap.add_argument('--out', type=Path)
    ap.add_argument('--start', type=float)
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0
    if not args.capture or not args.out:
        ap.error('capture and --out are required')
    x, rate = load(args.capture)
    try:
        rows, meta = measure(x, rate, args.start)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.with_suffix('.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    args.out.with_suffix('.json').write_text(json.dumps(meta, indent=2)+'\n')
    fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    f = [r['hz'] for r in rows]
    axes[0].plot(f, [r['db_relative_750'] for r in rows], 'o-')
    axes[1].plot(f, [r['phase_delay_removed_deg'] for r in rows], 'o-')
    axes[0].set_ylabel('Magnitude relative to 750 Hz (dB)')
    axes[1].set_ylabel('Phase, delay removed (degrees)')
    axes[1].set_xlabel('Frequency (Hz)')
    axes[1].set_xticks(np.arange(150, 3751, 150))
    axes[1].set_xlim(100, 3800)
    axes[1].tick_params(axis='x', labelrotation=60, labelsize=8)
    for ax in axes:
        ax.grid(True, alpha=.3)
    fig.suptitle('Received V.34 line probe — complete inbound path\n' +
                 Path(args.capture).name + f"; t={meta['start_seconds']:.2f} s")
    fig.text(.5,.01,'Markers are measured tones; 900, 1200, 1800 and 2400 Hz are deliberately unexcited.',
             ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.03,1,1))
    fig.savefig(args.out.with_suffix('.png'), dpi=150)
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
