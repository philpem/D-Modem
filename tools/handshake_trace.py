#!/usr/bin/env python3
"""Summarize reconstructed-only handshake observations and INFO0 recovery triggers."""
import argparse
import csv
import json
from pathlib import Path
import re
import statistics


def summarize(rows):
    transitions=[];recoveries=[];before=fsk=None;last=None;total=0;tone_b_rms=[]
    for row in rows:
        r={key:int(value) for key,value in row.items()}
        total+=1
        if r['stage']==0:
            before=r;fsk=None
        elif r['stage']==1:
            fsk=r
            if r['microstate']==49 and 'input0' in r:
                tone_b_rms.append((sum(r[f'input{i}']**2 for i in range(4))/4)**.5)
        elif r['stage']==2:
            key=tuple(r[name] for name in ('microstate','txstate','rxstate','recovery','armed'))
            if key!=last:
                transitions.append(r);last=key
            if (before and fsk and before['call']==r['call']==fsk['call'] and
                before['recovery']==0 and r['recovery']==4 and
                before['microstate'] in (47,56)):
                event=dict(before=before,after_fsk=fsk,after=r,
                    fresh_bit_arrived=fsk['nbits']>before['nbits'],
                    four_ones=(fsk['sr']&15)==15,
                    discriminator_previous=fsk['prev'],
                    counter_before=before['counter'],
                    normal_probe_transition_counter=96)
                if 'input0' in fsk:
                    event['fsk_input_rms_counts']=(sum(fsk[f'input{i}']**2 for i in range(4))/4)**.5
                if tone_b_rms:
                    event['preceding_tone_b_median_rms_counts']=statistics.median(tone_b_rms)
                    event['preceding_tone_b_blocks']=len(tone_b_rms)
                recoveries.append(event)
    return dict(rows_parsed=total,state_transitions=len(transitions),transitions=transitions,
                phase2_recovery_events=len(recoveries),recoveries=recoveries,
                stages={'0':'before handshake','1':'after FSK demodulation (input samples valid here)',
                        '2':'after handshake'},
                note='Observer does not modify DSP fields. Counter and discriminator values are internal units; capture truncation is reported separately.')


def selftest():
    base=dict(call=1,stage=0,microstate=47,txstate=60,rxstate=43,recovery=0,
              armed=2,counter=84,sr=263,nbits=236,prev=-1)
    sampled={**base,'stage':1,'sr':527,'nbits':237}
    after={**sampled,'stage':2,'microstate':41,'recovery':4,'armed':1,'sr':65535,'nbits':0}
    valid=summarize([base,sampled,after]);event=valid['recoveries'][0]
    assert valid['rows_parsed']==3 and valid['phase2_recovery_events']==1
    assert event['fresh_bit_arrived'] and event['four_ones']
    stale=summarize([base,{**sampled,'nbits':236},after])
    assert not stale['recoveries'][0]['fresh_bit_arrived']
    clean=summarize([base,sampled,{**after,'recovery':0}])
    assert clean['phase2_recovery_events']==0
    print('PASS: 3-row fresh recovery control, stale-bit and no-recovery negative controls')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('csv',nargs='?',type=Path)
    ap.add_argument('--out',type=Path)
    ap.add_argument('--selftest',action='store_true')
    args=ap.parse_args()
    if args.selftest:
        selftest();return
    if not args.csv:ap.error('a CSV or --selftest is required')
    with args.csv.open() as stream:
        header=stream.readline()
        if not header.startswith('# rows='):raise ValueError('missing observer metadata')
        report=summarize(csv.DictReader(stream))
    report['capture']={key:int(value) for key,value in re.findall(r'(\w+)=(\d+)',header)}
    if report['capture']['rows']!=report['rows_parsed']:raise ValueError('truncated CSV')
    if args.out:args.out.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({key:value for key,value in report.items() if key!='transitions'},indent=2))


if __name__=='__main__':main()
