#!/usr/bin/env python3
"""Collect supported last-call reports without resetting a hardware modem.

Courier: ATI6/11/16/17. Conexant/Olitec: &V1, S86 and #UD.
Reference for standard #UD keys: USRobotics 5637 OEM manual, tables 204-210,
https://support.usr.com/support/5637-oem/5637-oem-files/5637-OEM.pdf
Raw vendor values are retained; noise estimates are not converted to SNR.
"""
import argparse
import json
import os
from pathlib import Path
import re
import termios
import time

QUERIES = {
    'usr': ('ATI6', 'ATI11', 'ATI16', 'ATI17', 'ATI4'),
    'other': ('ATS86?', 'AT&V1', 'AT#UD', 'ATS91?', 'AT%L', 'AT%Q', 'AT&V'),
    'software': ('ATI11', 'AT#UD', 'AT&V'),
}
UD_FIELDS = {
    0x10: 'rx_power_minus_dbm', 0x11: 'tx_power_minus_dbm',
    0x12: 'estimated_noise_minus_dbm', 0x13: 'normalized_mse',
    0x14: 'near_echo_loss_db', 0x15: 'far_echo_loss_db', 0x16: 'far_echo_delay_ms',
    0x22: 'tx_symbols_per_second', 0x23: 'rx_symbols_per_second',
    0x24: 'tx_carrier_hz', 0x25: 'rx_carrier_hz',
    0x26: 'initial_tx_bps', 0x27: 'initial_rx_bps',
    0x30: 'temporary_carrier_losses', 0x31: 'rate_renegotiations',
    0x32: 'local_retrains', 0x33: 'remote_retrains',
    0x34: 'last_tx_bps', 0x35: 'last_rx_bps',
    0x41: 'frame_size', 0x42: 'link_timeouts', 0x43: 'link_naks',
    0x50: 'tx_flow_control', 0x51: 'rx_flow_control',
    0x52: 'tx_dte_characters', 0x53: 'rx_dte_characters',
    0x54: 'tx_overrun_characters', 0x55: 'rx_overrun_characters',
    0x56: 'tx_frames', 0x57: 'rx_frames', 0x58: 'tx_frame_errors', 0x59: 'rx_frame_errors',
}
S86 = {0:'normal hangup', 4:'carrier lost', 5:'peer has no error correction',
       6:'no negotiation response', 8:'no common framing', 9:'no common protocol',
       10:'invalid negotiation response', 11:'no synchronization from peer',
       12:'peer hung up', 13:'retransmission limit', 14:'protocol violation',
       15:'DTR lost', 17:'idle timeout', 18:'unsupported speed',
       19:'long space', 20:'keyboard abort', 21:'previous reason cleared',
       22:'connection not established', 23:'three retrains exhausted',
       24:'call waiting', 25:'extension pickup', 26:'remote hangup detected'}
UD_TERMINATION = {0x00:'unidentified', 0x01:'no previous call', 0x02:'call active',
    0x20:'DTR turned off', 0x2c:'call setup timer expired', 0x2e:'loop current interrupted',
    0x3c:'carrier lost', 0x3d:'training failed', 0x3e:'no common modulation',
    0x3f:'retrain failed', 0x40:'retrain limit', 0x41:'received cleardown',
    0x50:'keyboard abort', 0x51:'DTE hangup command', 0x52:'DTE reset',
    0x5b:'no error control', 0x5c:'protocol violation', 0x5d:'retry limit',
    0x5e:'negotiation failed', 0x5f:'received disconnect frame'}


def decode_ud(text):
    frames = re.findall(r'DIAG\s*<([0-9A-Fa-f]{8})\s+([^>]+)>', text)
    raw, values, tokens = {}, {}, set()
    for token, body in frames:
        tokens.add(token.upper())
        for key, value in re.findall(r'([0-9A-Fa-f]+)=((?:"[^"]*")|[^\s]+)', body):
            number = int(key, 16)
            raw[f'{number:02X}'] = value
            if re.fullmatch(r'[0-9A-Fa-f]+', value):
                values[number] = int(value, 16)
    decoded = {name: values[key] for key, name in UD_FIELDS.items() if key in values}
    for key, name in ((0x20,'tx_modulation'), (0x21,'rx_modulation')):
        if key in values:
            decoded[name] = {0xa:'V.32', 0xb:'V.32bis', 0xc:'V.34', 0xe:'V.PCM',
                             0x81:'K56flex'}.get(values[key], f'unknown 0x{values[key]:X}')
    if 0x40 in values:
        decoded['error_control'] = {0:'none',1:'V.42 LAPM',2:'MNP'}.get(values[0x40], 'unknown')
    if 0x44 in values:
        decoded['compression'] = {0:'none',1:'V.42bis',0x80:'MNP5'}.get(values[0x44], 'unknown')
    if 0x60 in values:
        decoded['termination_code'] = values[0x60]
        decoded['termination'] = UD_TERMINATION.get(values[0x60], 'unknown')
    invalid = [name for key,name,low,high in (
        (0x10,'rx_power_minus_dbm',0,43), (0x11,'tx_power_minus_dbm',0,17),
        (0x12,'estimated_noise_minus_dbm',10,90))
        if key in values and not low <= values[key] <= high]
    return dict(frames_parsed=len(frames), keys_parsed=len(raw), tokens=sorted(tokens),
                raw=raw, decoded=decoded,
                invalid_signal_metrics=invalid,
                note='Vendor estimates and failed-call sentinel values require validation; no derived SNR.')


def collect(kind, ask):
    queries = {}
    for text in QUERIES[kind]:
        reply = ask(text)
        if reply is None:
            queries[text] = dict(status='not_queried',raw=None,body='')
            continue
        lines = [line.strip() for line in reply.replace('\r','\n').splitlines()
                 if line.strip() and line.strip() not in (text, 'OK', 'ERROR')]
        status = ('unsupported' if re.search(r'\bERROR\b', reply) else
                  'no_terminal_result' if not re.search(r'\bOK\b', reply) else
                  'empty' if not lines else
                  'identity_only' if all('Oli\'Net' in line for line in lines) else 'supported')
        queries[text] = dict(status=status, raw=reply, body='\n'.join(lines))
    result = dict(kind=kind, queries=queries, queries_planned=len(queries),
                  queries_attempted=sum(q['status']!='not_queried' for q in queries.values()),
                  reports_with_data=sum(q['status']=='supported' for q in queries.values()),
                  note='Read after call teardown, before reset. Local teardown can affect termination reports.')
    if 'AT#UD' in queries:
        result['unimodem'] = decode_ud(queries['AT#UD']['raw'] or '')
        link = queries.get('AT&V1', {}).get('body','')
        established = bool(re.search(r'LAST (?:TX|RX) rate\.+\s+\d+ BPS',link))
        if link:
            result['connection_established_in_last_call_report'] = established
            result['unimodem']['connection_measurements_valid'] = established
    s86 = queries.get('ATS86?', {}).get('body', '')
    if re.fullmatch(r'\d+', s86):
        result['s86'] = dict(value=int(s86), reason=S86.get(int(s86), 'unknown or reserved'))
    joined = '\n'.join(q['body'] for q in queries.values())
    result['reported_metrics'] = {key: float(value) for key, value in re.findall(
        r'(SNR|Near Echo Loss|Far Echo Loss|Roundtrip Delay|Timing Offset|Carrier Offset)\s+\([^)]*\)\s+(-?[\d.]+)', joined)}
    result['invalid_reported_metrics'] = [key for key,value in result['reported_metrics'].items()
        if key in ('SNR','Near Echo Loss','Far Echo Loss') and
        not -20 <= value <= (80 if key=='SNR' else 100)]
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--tty', required=True)
    ap.add_argument('--kind', required=True, choices=QUERIES)
    ap.add_argument('--out', required=True, type=Path)
    args = ap.parse_args()
    from modem_test import atprobe
    fd = atprobe.open_raw(args.tty)
    try:
        time.sleep(.3)
        report = collect(args.kind, lambda text: atprobe.command(fd,text,2,deadline=5))
        args.out.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({key:value for key,value in report.items() if key not in ('queries','unimodem')},indent=2))
    finally:
        termios.tcflush(fd,termios.TCOFLUSH)
        os.close(fd)


if __name__ == '__main__':
    main()
