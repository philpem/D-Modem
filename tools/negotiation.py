#!/usr/bin/env python3
"""Passive V.8/V.8bis payload decoding, using SpanDSP for V.21 clock recovery.

V.8bis layouts: ITU-T V.8bis (11/2000), clauses 7 and 8. Unknown bits and
non-standard provider payloads are retained rather than assigned guessed names.
V.8 octet tags/extension layouts follow ITU-T V.8 and the independent SpanDSP
implementation. Every accepted V.8bis frame must pass its HDLC CRC-16.
"""
import argparse
import collections
import json
import re
from pathlib import Path
import subprocess
import tempfile
import wave

from integration_paths import ROOT, DSP_ROOT
SOURCE = 'https://www.itu.int/rec/dologin_pub.asp?id=T-REC-V.8bis-200011-I!!PDF-E&lang=e&type=items'
TYPES = {1:'MS', 2:'CL', 3:'CLR', 4:'ACK(1)', 5:'ACK(2)',
         8:'NAK(1)', 9:'NAK(2)', 10:'NAK(3)', 11:'NAK(4)', 13:'V.92-defined'}
FUNCTIONS = {0:'TBS', 1:'H.324', 2:'V.18', 3:'T.101', 4:'T.30 transmit fax',
             5:'T.30 receive fax', 6:'V-series modem data', 7:'extension'}
STANDARD = ['Data','Simultaneous voice and data','H.324','V.18','T.30 fax',
            'Analogue telephony','T.101','H.324 Multilink','Multilink additional connection']
LEVEL2 = {
 'Network': ['Cellular','ISDN','Digital PSTN',None,None,'Non-standard network'],
 'Data': ['Transparent data','V.42','V.42bis','V.14','T.120','Non-standard',
          'T.84 SPIFF','T.434','V.80 synchronous HDLC',None,'V.34','V.32bis',
          'V.32','V.22bis','V.22','V.21','V.90 analogue','V.90 digital',
          'V.91','V.92 analogue','V.92 digital'],
 'Simultaneous voice and data': ['V.70','V.61',None,'V.34','V.32bis','Non-standard',
          'Transparent data','V.42','V.42bis','V.14','T.120','Synchronous HDLC',
          'T.84 SPIFF','T.434','V.76 default suspend/resume'],
 'H.324': ['Video','Audio','Encryption',None,None,'Non-standard'],
 'H.324 Multilink': ['Video','Audio','Encryption',None,None,'Non-standard'],
 'V.18': [],
 'Analogue telephony': ['PSTN'],
 'T.101': ['Duplex','V.29 short train','V.27ter',None,None,'Non-standard'],
 'T.30 fax': ['T.30 without V.8','V.34 half duplex','V.34 duplex','V.61','V.70','Non-standard',
              'H.324','Extended negotiation','MS initiated session',None,None,'Non-standard'],
 'Multilink additional connection': [None,None,None,'Call association present','Call association MSB','Call association']}


def names(octets, width, table):
    result = []
    for i, octet in enumerate(octets):
        for bit in range(width):
            if octet & (1 << bit):
                n = i*width+bit
                result.append(table[n] if n < len(table) and table[n] else f'reserved/unknown bit {n+1}')
    return result


def bis_payload(payload):
    if not payload:
        raise ValueError('missing identification octet')
    if len(payload) > 64:
        raise ValueError('capability payload exceeds 64 octets')
    result = dict(type=TYPES.get(payload[0]&15, 'reserved'), revision=payload[0]>>4,
                  payload_hex=payload.hex(' '))
    if payload[0]&15 not in (1,2,3):
        if payload[0]&15 in (4,5,8,9,10,11) and len(payload)!=1:
            raise ValueError('ACK/NAK must contain only its identification octet')
        result['extra_hex'] = payload[1:].hex(' ')
        return result
    pos = 1

    def block(endbit):
        nonlocal pos
        out = []
        while pos < len(payload):
            b = payload[pos]; pos += 1; out.append(b)
            if b & endbit:
                return out
        raise ValueError('unterminated capability block')

    def field(identification):
        nonselecting = block(0x80)
        selecting = block(0x80)
        ntable = (['V.8 startup','Short V.8','Additional information','Transmit ACK(1)',None,None,'Non-standard field']
                  if identification else [None]*6+['Non-standard capabilities'])
        stable = ['Network'] if identification else STANDARD
        result = dict(nonselecting=names(nonselecting,7,ntable),
                      selecting=names(selecting,7,stable),
                      raw_nonselecting=bytes(nonselecting).hex(' '),
                      raw_selecting=bytes(selecting).hex(' '), children=[])
        for i, byte in enumerate(selecting):
            for bit in range(7):
                if not byte & (1 << bit):
                    continue
                k = i*7+bit
                label = stable[k] if k < len(stable) else f'unknown selector {k+1}'
                npars = block(0x40)
                child = dict(capability=label, parameters=names(npars,6,LEVEL2.get(label,[])),
                             raw_nonselecting=bytes(npars).hex(' '), children=[])
                if not npars[-1] & 0x80:
                    spars = block(0x40)
                    child['raw_selecting'] = bytes(spars).hex(' ')
                    child['selecting'] = names(spars,6,['Data'] if label.startswith('H.324') else [])
                    for j, value in enumerate(spars):
                        for b in range(6):
                            if value & (1 << b):
                                p3 = block(0x40)
                                table = (['V.42','V.14','PPP','T.120','T.84','T.434','T.140']
                                         if label.startswith('H.324') and j == b == 0 else [])
                                child['children'].append(dict(selector=j*6+b+1,
                                    parameters=names(p3,6,table), raw=bytes(p3).hex(' ')))
                    if not spars[-1] & 0x80:
                        # Bit 8 ends the complete Par(2), in its last NPar(3)
                        # octet when children follow the SPar(2) block.
                        if not child['children'] or not p3[-1] & 0x80:
                            raise ValueError('missing end of Par(2)')
                result['children'].append(child)
        return result

    result['identification'] = field(True)
    result['standard'] = field(False)
    result['nonstandard'] = []
    if pos < len(payload) and 'Non-standard field' not in result['identification']['nonselecting']:
        raise ValueError('non-standard field present without identification flag')
    while pos < len(payload):
        length = payload[pos]; pos += 1
        body = payload[pos:pos+length]; pos += length
        if len(body) != length or not body:
            raise ValueError('truncated non-standard block')
        k = 2 if body[0] == 255 else 1
        if len(body) <= k:
            raise ValueError('missing provider length')
        plen = body[k]
        if k+1+plen > len(body):
            raise ValueError('truncated provider identifier')
        result['nonstandard'].append(dict(country_code_hex=body[:k].hex(' '),
            provider_code_hex=body[k+1:k+1+plen].hex(' '), data_hex=body[k+1+plen:].hex(' ')))
    return result


def crc16(data):
    crc = 0xffff
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (0x8408 if crc & 1 else 0)
    return crc


def hdlc(events):
    frames, candidate, history, last_flag = [], [], [], None
    for sample, bit in events:
        if bit < 0:
            candidate, history, last_flag = [], [], None
            continue
        history.append(bit); history = history[-8:]
        if last_flag is not None:
            candidate.append(bit)
        if history != [0,1,1,1,1,1,1,0]:
            if len(candidate) > 800:
                candidate, last_flag = [], None
            continue
        raw = candidate[:-8] if last_flag is not None else []
        if raw:
            bits, ones, error = [], 0, None
            for value in raw:
                if ones == 5:
                    if value:
                        error = 'invalid bit stuffing'; break
                    ones = 0; continue
                bits.append(value)
                ones = ones+1 if value else 0
            octets = bytes(sum(bits[i+b] << b for b in range(8)) for i in range(0,len(bits)-7,8))
            if len(bits)%8: error = 'non-integral frame'
            if len(octets)<3: error = 'short frame'
            valid = not error and crc16(octets) == 0xf0b8
            frame = dict(start_seconds=last_flag/8000, end_seconds=sample/8000,
                         frame_hex=octets.hex(' '), crc_valid=bool(valid))
            if not valid:
                frame['error'] = error or 'CRC mismatch'
            else:
                try: frame['decoded'] = bis_payload(octets[:-2])
                except ValueError as exc: frame['parse_error'] = str(exc)
            frames.append(frame)
        candidate, last_flag = [], sample
    return frames


def v8_payload(payload):
    result = dict(payload_hex=payload.hex(' '), fields=[], modulations=[])
    pos = 0
    while pos < len(payload):
        b = payload[pos]; pos += 1
        tag = b&31
        ext = []
        while pos < len(payload) and payload[pos]&0x38 == 0x10:
            ext.append(payload[pos]); pos += 1
        field = dict(tag=tag, value=b>>5, raw=bytes([b]+ext).hex(' '))
        if tag == 1:
            field.update(name='call function', decoded=FUNCTIONS.get(b>>5,'unknown'))
        elif tag == 5:
            mods = []
            for octet, mapping in zip([b]+ext,
                ({0x80:'V.34 half duplex',0x40:'V.34',0x20:'V.90'},
                 {0x80:'V.27ter',0x40:'V.29',4:'V.17',2:'V.22/V.22bis',1:'V.32/V.32bis'},
                 {0x80:'V.21',0x40:'V.23 half duplex',4:'V.23',2:'V.26bis',1:'V.26ter'})):
                mods.extend(name for mask,name in mapping.items() if octet&mask)
            field.update(name='modulation modes', decoded=mods)
            result['modulations'] = mods
        elif tag == 10:
            field.update(name='protocol', decoded={0:'none',1:'LAPM/V.42',7:'extension'}.get(b>>5,'reserved'))
        elif tag == 13:
            field.update(name='PSTN access', decoded=[name for mask,name in
                ((1,'caller cellular'),(2,'answerer cellular'),(4,'digital access')) if b>>5&mask])
        elif tag == 7:
            field.update(name='PCM availability', decoded=[name for mask,name in
                ((1,'V.90/V.92 analogue'),(2,'V.90/V.92 digital'),(4,'V.91')) if b>>5&mask])
        elif tag == 15:
            field.update(name='non-standard facilities', decoded='raw value retained')
        elif tag == 14:
            field.update(name='T.66', decoded='reserved value retained')
        else:
            field.update(name='unknown/reserved', decoded='raw value retained')
        result['fields'].append(field)
    return result


def v8_frames(events, channel):
    packets, window, current, since, zeros = [], 0, None, 0, 0
    syncs = {0x803ff:'CI',0xf03ff:'CM' if channel=='ch1' else 'JM',0xaabff:'V.92'}
    def finish():
        if current and current['octets']:
            data = bytes(current['octets'])
            packet = {k:v for k,v in current.items() if k != 'octets'}
            packet['decoded'] = v8_payload(data) if current['type'] in ('CM','JM','CI') else dict(payload_hex=data.hex(' '))
            packets.append(packet)
    for sample, bit in events:
        if bit < 0:
            finish(); current, window, since, zeros = None,0,0,0
            continue
        window = ((window >> 1) | (bit << 19)) & 0xfffff
        if window in syncs:
            finish()
            current = dict(type=syncs[window],start_seconds=max(0,sample/8000-20/300),octets=[])
            since, zeros = 0, 0
            continue
        since += 1
        if current and since >= 10 and window&0x80400 == 0x80000:
            value = window>>11&255
            if len(current['octets']) < 64: current['octets'].append(value)
            zeros = zeros+1 if value == 0 else 0
            # A CJ preceded by >=10 marks also matches the CI sync on its
            # first zero octet. The next two zero octets distinguish it.
            if current['type']=='CI' and current['octets']==[0,0]:
                packets.append(dict(type='CJ', start_seconds=current['start_seconds'],
                                    decoded=dict(payload_hex='00 00 00')))
                current = None; since = 0
                continue
            if zeros == 3:
                current['octets'] = current['octets'][:-3]
                finish(); current = None
                packets.append(dict(type='CJ', start_seconds=sample/8000-30/300, decoded=dict(payload_hex='00 00 00')))
            since = 0
    finish()
    counts = collections.Counter((p['type'],p['decoded']['payload_hex']) for p in packets)
    for p in packets:
        p['identical_copies'] = counts[p['type'],p['decoded']['payload_hex']]
        p['repeat_confirmed'] = p['type']=='CJ' or p['identical_copies']>=2
    return packets


def recover(audio, channel, mode, directory):
    with tempfile.NamedTemporaryFile(dir=directory,suffix='.raw') as raw:
        raw.write(audio); raw.flush()
        proc = subprocess.run([str(ROOT/'scratch/fsk_bits'),raw.name,channel,mode],
                              check=True,capture_output=True,text=True)
    return [(int(a),int(b)) for a,b in (line.split(',') for line in proc.stdout.splitlines())]


def analyse(directory, role):
    report = dict(source=SOURCE, observations=[], samples_scanned={})
    for direction in ('rx','tx'):
        path = directory/f'modem_{direction}_8k.wav'
        if not path.exists(): continue
        with wave.open(str(path),'rb') as w:
            if (w.getframerate(),w.getnchannels(),w.getsampwidth())!=(8000,1,2):
                raise ValueError('expected mono 16-bit 8000 Hz audio')
            audio = w.readframes(w.getnframes())
        report['samples_scanned'][direction] = len(audio)//2
        channel = ('ch1' if role=='answerer' else 'ch2') if direction=='rx' else ('ch2' if role=='answerer' else 'ch1')
        async_bits = recover(audio,channel,'async',directory)
        for event in v8_frames(async_bits,channel):
            event.update(protocol='V.8',direction=direction,channel=channel)
            report['observations'].append(event)
        # V.8bis initiating/responding roles do not necessarily match the
        # telephone caller/answerer (the automatic answerer can initiate).
        for bis_channel in ('ch1','ch2'):
            for event in hdlc(recover(audio,bis_channel,'sync',directory)):
                event.update(protocol='V.8bis',direction=direction,channel=bis_channel)
                report['observations'].append(event)
    report['observations'].sort(key=lambda x:x['start_seconds'])
    report['v8_messages'] = sum(e['protocol']=='V.8' for e in report['observations'])
    report['v8bis_crc_valid_frames'] = sum(e['protocol']=='V.8bis' and e['crc_valid'] for e in report['observations'])
    menus = {}
    for e in report['observations']:
        if e.get('type') in ('CM','JM') and e['repeat_confirmed']:
            menus[e['type']] = e['decoded']
    report['capabilities'] = menus
    if all(k in menus for k in ('CM','JM')):
        report['common_advertised_modulations'] = [m for m in menus['CM']['modulations'] if m in menus['JM']['modulations']]
    report['cj_observed'] = any(e.get('type')=='CJ' for e in report['observations'])
    log = directory/'call.log'
    text = log.read_text(errors='replace') if log.exists() else ''
    report['connection'] = dict(
        modulation=re.findall(r'dmodem << Modulation: ([^\n]+)',text),
        protocol=re.findall(r'dmodem << Protocol: ([^\n]+)',text),
        tx_rates=re.findall(r'dmodem << TxRate: (\d+)',text),
        rx_rates=re.findall(r'dmodem << CONNECT (\d+)',text))
    (directory/'negotiation.json').write_text(json.dumps(report,indent=2)+'\n')
    lines = ['V.8 menus advertise capabilities; the CONNECT result establishes the actual data mode/rates.',
             'Common advertised modes: '+', '.join(report.get('common_advertised_modulations',[])),
             'Observed connection: '+json.dumps(report['connection']), '']
    for e in report['observations']:
        decoded = e.get('decoded',{})
        lines.append(f"{e['start_seconds']:8.3f} {e['direction']:2s} {e['protocol']:6s} "
                     f"{e.get('type',decoded.get('type','invalid frame'))}: {decoded.get('payload_hex',e.get('frame_hex',''))}")
        if e['protocol']=='V.8':
            for field in decoded.get('fields',[]): lines.append(f"           {field['name']}: {field['decoded']} [{field['raw']}]")
            lines.append(f"           identical copies={e['identical_copies']}; repeat confirmed={e['repeat_confirmed']}")
        else:
            lines.append('           '+json.dumps(e.get('decoded',{'error':e.get('error')})))
    lines.append(f"Scanned {report['samples_scanned']}; V.8 messages={report['v8_messages']}; CRC-valid V.8bis frames={report['v8bis_crc_valid_frames']}")
    (directory/'negotiation.txt').write_text('\n'.join(lines)+'\n')
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('directory',type=Path)
    ap.add_argument('--role',choices=('caller','answerer'),default='caller')
    args = ap.parse_args()
    report = analyse(args.directory.resolve(),args.role)
    print(json.dumps({k:v for k,v in report.items() if k!='observations'},indent=2))


if __name__=='__main__': main()
