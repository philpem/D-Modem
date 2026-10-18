#!/usr/bin/env python3
"""Trace V.8bis dual-tone signals and their identifying second segments.

Frequency and duration tables: ITU-T V.8bis (09/1998), section 7.1,
https://www.itu.int/rec/dologin_pub.asp?id=T-REC-V.8bis-199809-S!!PDF-E&lang=e&type=items
This is a signal detector, not a decoder for V.8bis capability message frames.
"""
import argparse
import json
from pathlib import Path
import sys

import numpy as np

from integration_paths import ROOT, DSP_ROOT
sys.path.insert(0, str(DSP_ROOT / 'testbench'))
from capture_io import load

PAIRS = {'initiating': (1375, 2002), 'responding': (1529, 2225)}
SEGMENTS = {650: 'MRe', 1150: 'MRd', 400: 'CRe', 1900: 'CRd',
            980: 'ESi', 1650: 'ESr'}


def amplitude(x, rate, frequencies):
    w = np.hanning(len(x))
    basis = np.exp(-2j*np.pi*np.array(frequencies)[:, None]*np.arange(len(x))/rate)
    return abs(basis @ (x*w) * (2/w.sum()))


def detect(x, rate):
    x = np.asarray(x, float)
    if x.ndim != 1 or len(x) < rate * .1:
        raise ValueError('at least 100 ms of mono PCM is required')
    win, hop = round(rate*.04), round(rate*.01)
    events = []
    for kind, frequencies in PAIRS.items():
        hits = []
        for a in range(0, len(x)-win+1, hop):
            z = x[a:a+win]
            power = float(np.mean(z*z))
            amp = amplitude(z, rate, frequencies)
            p = amp*amp/2
            hits.append(power > 30**2 and p.sum()/power > .65 and
                        p.min()/max(p.max(), 1e-12) > .15)
        padded = np.r_[False, hits, False]
        bounds = np.flatnonzero(padded[1:] != padded[:-1])
        for a, b in zip(bounds[::2], bounds[1::2]):
            duration = (b-a)*hop/rate
            if duration < .23:
                continue
            # Windows centred over the transition lose dual-tone purity.
            # Start the segment-2 observation after the final full window.
            start = b*hop+win
            segment = x[start:start+round(.06*rate)]
            label, confidence = 'unclassified segment 2', 0.
            if len(segment) >= rate*.04:
                freqs = list(SEGMENTS)
                powers = amplitude(segment, rate, freqs)**2/2
                k = int(np.argmax(powers))
                confidence = float(powers[k]/max(np.mean(segment*segment), 1e-12))
                if confidence > .65:
                    label = SEGMENTS[freqs[k]]
            # MRe/CRe and ESi initiate; ESr only responds. Preserve the
            # measured frequencies when the combination is inconsistent.
            allowed = {'MRe', 'MRd', 'CRe', 'CRd', 'ESi'} if kind == 'initiating' else {'MRd', 'CRd', 'ESr'}
            if label not in allowed:
                label = 'unclassified segment 2'
            events.append(dict(start_seconds=a*hop/rate,
                dual_tone_duration_seconds=duration, direction=kind,
                frequencies_hz=list(frequencies), signal=label,
                segment2_explained_power=confidence))
    return dict(samples_scanned=len(x), seconds_scanned=len(x)/rate,
                signals_detected=len(events), events=sorted(events, key=lambda e:e['start_seconds']))


def selftest():
    rate = 8000
    t = np.arange(round(.4*rate))/rate
    for kind, pair in PAIRS.items():
        second = 650 if kind == 'initiating' else 1650
        x = np.r_[np.zeros(800), 1500*sum(np.sin(2*np.pi*f*t) for f in pair),
                  1500*np.sin(2*np.pi*second*np.arange(800)/rate), np.zeros(800)]
        report = detect(x, rate)
        assert report['signals_detected'] == 1, report
        assert report['events'][0]['signal'] == SEGMENTS[second], report
    rng = np.random.default_rng(8)
    for x in (np.zeros(8000), rng.normal(0, 500, 8000),
              1500*np.sin(2*np.pi*1375*np.arange(8000)/rate)):
        assert detect(x, rate)['signals_detected'] == 0
    print('PASS: 2 V.8bis signals classified; silence, noise and single-tone controls rejected')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('capture', nargs='?')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()
    if args.selftest:
        selftest()
    else:
        if not args.capture:
            ap.error('capture is required unless --selftest is selected')
        x, rate = load(args.capture)
        print(json.dumps(detect(x, rate), indent=2))


if __name__ == '__main__':
    main()
