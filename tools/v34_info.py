#!/usr/bin/env python3
"""Recover CRC-checked V.34 INFO0 capability frames from mono PCM WAV audio.

ITU-T V.34 (02/98), 10.1.2.3 and table 14: 600 bit/s DPSK,
1200 Hz caller / 2400 Hz answerer, 17 information bits and CRC-16.
The detector scans every symbol offset; only CRC-valid frames are reported.
"""
import argparse
import json
from pathlib import Path
import wave
from collections import defaultdict

import numpy as np
from scipy.signal import firwin, resample_poly

SYNC = np.array([int(c) for c in '111101110010'], dtype=np.uint8)


def crc16(bits):
    crc = 0xffff
    for bit in bits:
        feedback = (crc >> 15) ^ int(bit)
        crc = (crc << 1) & 0xffff
        if feedback:
            crc ^= 0x1021
    return crc


def integer(bits):
    return sum(int(bit) << i for i, bit in enumerate(bits))


def capabilities(bits):
    # Scalar fields are numbered LSB:MSB by the recommendation, while the
    # reconstructed DSP's byte dump packs the chronological bits MSB first.
    return dict(symbol_rates_supported=[2400, 3000, 3200]+
        [rate for pos, rate in ((0,2743),(1,2800),(2,3429)) if bits[pos]],
        tx_3000_low_carrier=bool(bits[3]), tx_3000_high_carrier=bool(bits[4]),
        tx_3200_low_carrier=bool(bits[5]), tx_3200_high_carrier=bool(bits[6]),
        tx_3429_allowed=bool(bits[7]), power_reduction=bool(bits[8]),
        max_symbol_rate_step_difference=integer(bits[9:12]), cme=bool(bits[12]),
        constellation_1664=bool(bits[13]),
        clock_source={0:'internal',1:'receive synchronized',2:'external',3:'reserved'}[integer(bits[14:16])],
        recovery_ack=bool(bits[16]))


def analyse(audio, rate):
    from math import gcd
    original_samples = len(audio)
    divisor = gcd(rate, 9600)
    x = resample_poly(np.asarray(audio,dtype=float),9600//divisor,rate//divisor)
    t = np.arange(len(x))/9600
    frames = []
    sync_hits = bits_examined = 0
    for carrier in (1200,2400):
        z = np.convolve(x*np.exp(-2j*np.pi*carrier*t),
                        firwin(65,450,fs=9600),mode='same')
        for offset in range(16):
            symbols = z[offset::16]
            product = symbols[1:]*symbols[:-1].conj()
            bits = (product.real < 0).astype(np.uint8)
            bits_examined += len(bits)
            if len(bits) < 45:
                continue
            matches = np.flatnonzero(np.all(
                np.lib.stride_tricks.sliding_window_view(bits,12) == SYNC,axis=1))
            sync_hits += len(matches)
            for start in matches:
                data = bits[start+12:start+45]
                if len(data) < 33:
                    continue
                payload, received = data[:17], data[17:]
                crc = crc16(payload)
                if crc != int(''.join(map(str,received)),2):
                    continue
                # The first recovered bit belongs to the second symbol.
                seconds = (offset+16*(int(start)+1))/9600
                confidence = float(np.mean(np.abs(product[start:start+45].real) /
                    np.maximum(np.abs(product[start:start+45]),1e-15)))
                frames.append(dict(time_seconds=seconds,carrier_hz=carrier,
                    sender='caller' if carrier==1200 else 'answerer',
                    information_bits=''.join(map(str,payload)),crc_hex=f'{crc:04X}',
                    confidence=confidence,capabilities=capabilities(payload)))
    # Adjacent timing hypotheses detect the same physical packet.
    chosen = []
    for frame in sorted(frames,key=lambda f:-f['confidence']):
        if not any(f['carrier_hz']==frame['carrier_hz'] and
                   abs(f['time_seconds']-frame['time_seconds']) < .008 for f in chosen):
            chosen.append(frame)
    return dict(samples_examined=original_samples, sample_rate=rate,
        symbol_timing_hypotheses=32, bits_examined=bits_examined,
        sync_candidates=int(sync_hits), crc_valid_timing_candidates=len(frames),
        frames_decoded=len(chosen), frames=sorted(chosen,key=lambda f:f['time_seconds']),
        scope='Standard 17-bit V.34 INFO0 only; PCM extensions and INFO1 are not decoded.')


def render_text(report):
    groups=defaultdict(list)
    for frame in report['frames']:
        groups[(frame['sender'],frame['information_bits'])].append(frame)
    lines=[f"CRC-valid INFO0 frames: {report['frames_decoded']}; "
           f"sync candidates: {report['sync_candidates']}; samples: {report['samples_examined']}",
           report['scope'],
           'Carrier identifies transmitter role; a receive recording may also contain returned local echo.']
    for (sender,bits),frames in groups.items():
        c=frames[0]['capabilities']
        lines += [f"{sender} ({frames[0]['carrier_hz']} Hz): {len(frames)} frames, "
                  f"{frames[0]['time_seconds']:.3f}..{frames[-1]['time_seconds']:.3f} s",
                  f"  information bits 12..28: {bits}; CRC: {frames[0]['crc_hex']}",
                  f"  symbol rates: {sorted(c['symbol_rates_supported'])}; "
                  f"3429 TX allowed: {c['tx_3429_allowed']}",
                  f"  3000 low/high carrier: {c['tx_3000_low_carrier']}/{c['tx_3000_high_carrier']}; "
                  f"3200 low/high: {c['tx_3200_low_carrier']}/{c['tx_3200_high_carrier']}",
                  f"  power reduction: {c['power_reduction']}; "
                  f"max symbol-rate step difference: {c['max_symbol_rate_step_difference']}",
                  f"  CME: {c['cme']}; 1664-point constellation: {c['constellation_1664']}; "
                  f"clock: {c['clock_source']}; recovery ACK: {c['recovery_ack']}"]
    return '\n'.join(lines)+'\n'


def selftest():
    rng=np.random.default_rng(344)
    payload=np.array([int(c) for c in '11111111100001000'],dtype=np.uint8)
    crc=crc16(payload)
    assert crc==0x8fd9
    # Literal CRC also matches the accepted FF 84 47 EC DSP wire dump
    # (its final CRC bit is not included in that byte-oriented diagnostic).
    frame=np.concatenate((SYNC,payload,[int(c) for c in '1000111111011001'],[1]*4))
    def waveform(bits,carrier):
        signs=np.concatenate(([1.0],np.cumprod(1-2*np.asarray(bits,dtype=float))))
        base=np.repeat(signs,16)
        t=np.arange(len(base))/9600
        signal=base*np.cos(2*np.pi*carrier*t)
        return np.concatenate((np.zeros(1920),signal,np.zeros(1920)))
    positives=0
    for carrier in (1200,2400):
        report=analyse(waveform(frame,carrier),9600)
        assert report['frames_decoded']==1,report
        decoded=report['frames'][0]
        assert decoded['carrier_hz']==carrier and decoded['crc_hex']=='8FD9'
        assert decoded['capabilities']['clock_source']=='internal'
        assert not decoded['capabilities']['recovery_ack']
        positives+=1
    corrupt=frame.copy();corrupt[15]^=1
    controls=[waveform(corrupt,1200),np.zeros(9600),rng.normal(size=9600)]
    for signal in controls:
        assert analyse(signal,9600)['frames_decoded']==0
    print(f'PASS: INFO0 {positives} valid carrier controls, {len(controls)} negative controls; CRC 8FD9')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('wav',nargs='?',type=Path)
    ap.add_argument('--out',type=Path)
    ap.add_argument('--selftest',action='store_true')
    args=ap.parse_args()
    if args.selftest:
        selftest();return
    if not args.wav:
        ap.error('a WAV or --selftest is required')
    with wave.open(str(args.wav),'rb') as reader:
        if reader.getnchannels()!=1 or reader.getsampwidth()!=2:
            raise ValueError('requires mono signed 16-bit PCM')
        rate=reader.getframerate()
        audio=np.frombuffer(reader.readframes(reader.getnframes()),dtype='<i2')/32768.0
    report=analyse(audio,rate)
    if args.out:
        args.out.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
