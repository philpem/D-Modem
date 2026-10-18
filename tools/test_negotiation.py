#!/usr/bin/env python3
"""Known-payload, framing and independent V.21 waveform controls."""
import tempfile
from pathlib import Path
import numpy as np
from negotiation import bis_payload, crc16, hdlc, recover, v8_frames


def octet(value):
    return [(value >> b) & 1 for b in range(8)]


def framed(value):
    return [0]+octet(value)+[1]


def hdlc_bits(payload, corrupt=False):
    fcs = crc16(payload)^0xffff
    data = bytearray(payload+fcs.to_bytes(2, 'little'))
    if corrupt:
        data[0] ^= 1
    bits, ones = [], 0
    for value in data:
        for bit in octet(value):
            bits.append(bit)
            ones = ones+1 if bit else 0
            if ones == 5:
                bits.append(0); ones = 0
    flag = octet(0x7e)
    return [1]*100+flag*3+bits+flag*3+[1]*100


def events(bits):
    return [(round(i*8000/300), b) for i, b in enumerate(bits)]


def waveform(bits, channel):
    # Independent continuous-phase FSK generator, not the SpanDSP transmitter.
    b = np.array(bits)[np.minimum((np.arange(int(len(bits)*8000/300))*300/8000).astype(int), len(bits)-1)]
    mark, space = (980,1180) if channel == 'ch1' else (1650,1850)
    phase = np.cumsum(np.where(b,mark,space)*2*np.pi/8000)
    audio = 10000*np.sin(phase)+np.random.default_rng(8).normal(0,80,len(b))
    return np.r_[np.zeros(800), audio, np.zeros(1600)].astype('<i2').tobytes()


def main():
    assert crc16(b'123456789')^0xffff == 0x906e
    cl = bytes.fromhex('12 81 80 80 81 06 d0')
    decoded = bis_payload(cl)
    assert decoded['type'] == 'CL'
    assert decoded['standard']['children'][0]['parameters'] == ['V.42','V.42bis','V.34']
    nested = bis_payload(bytes.fromhex('11 80 80 80 84 41 41 c5'))
    assert nested['standard']['children'][0]['children'][0]['parameters'] == ['V.42','PPP']
    ns = bis_payload(bytes.fromhex('12 c0 80 80 80 05 b4 01 42 ab cd'))
    assert ns['nonstandard'][0]['data_hex'] == 'ab cd'
    for bad in (b'', cl[:-1]):
        try:
            bis_payload(bad)
        except ValueError:
            pass
        else:
            raise AssertionError('malformed payload accepted')
    payload = bytes.fromhex('c1 45 11 10 2a 0d')
    v8bits = [1]*100 + (framed(0xe0)+sum((framed(b) for b in payload), [])+[1]*10)*3
    v8bits += sum((framed(0) for _ in range(3)), [])+[1]*100
    with tempfile.TemporaryDirectory() as tmp:
        for channel in ('ch1','ch2'):
            for p in (cl, bytes.fromhex('14'), bytes.fromhex('12 c0 80 80 80 05 b4 01 ff ff ff')):
                frames = hdlc(recover(waveform(hdlc_bits(p),channel), channel,'sync',Path(tmp)))
                good = [f for f in frames if f['crc_valid']]
                assert len(good)==1 and good[0]['decoded']['payload_hex']==p.hex(' '), (channel, frames)
            bad = hdlc(recover(waveform(hdlc_bits(cl,True),channel),channel,'sync',Path(tmp)))
            assert bad and not any(f['crc_valid'] for f in bad), bad
            packets = v8_frames(recover(waveform(v8bits,channel),channel,'async',Path(tmp)),channel)
            good = [p for p in packets if p['decoded']['payload_hex']==payload.hex(' ') and p['repeat_confirmed']]
            assert len(good)>=2, packets
            assert good[0]['decoded']['modulations']==['V.34','V.32/V.32bis']
            assert any(p['type']=='CJ' for p in packets), packets
            for raw in (np.zeros(16000,dtype='<i2').tobytes(),
                        np.random.default_rng(8).normal(0,3000,16000).astype('<i2').tobytes()):
                assert not any(f['crc_valid'] for f in hdlc(recover(raw,channel,'sync',Path(tmp))))
                assert not any(p['repeat_confirmed'] for p in v8_frames(recover(raw,channel,'async',Path(tmp)),channel))
    print('PASS: CRC check vector, nested/provider capability blocks; both V.21 channels: '
          '6 CRC-valid waveform frames, repeated V.8/CJ, 2 corrupt frames rejected, 4 silence/noise controls')


if __name__ == '__main__':
    main()
