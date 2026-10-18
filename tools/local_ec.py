#!/usr/bin/env python3
"""Summarize read-only local DSP echo telemetry from reconstructed-ec builds."""
import argparse
import csv
import json
import os
from pathlib import Path

os.environ.setdefault('MPLCONFIGDIR', '/tmp/slmodem-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def power_ratio(input_power, residual_power):
    if input_power <= 0 or residual_power <= 0:
        return None
    return float(10*np.log10(input_power/residual_power))


def analyse(directory):
    report = dict(perspective='local reconstructed DSP', streams={},
        erle_db=None,
        interpretation='Input/residual power reduction includes receive correction and far-end signal; '
                       'it is not echo-only ERLE. No far-end silence interval is assumed.',
        time_origin='First observed echo routine invocation; each stream uses its own origin.')
    for name in ('v34','v92'):
        audio_path = directory/f'local-ec-{name}.f32'
        stats_path = directory/f'local-ec-{name}.csv'
        if not audio_path.exists() or not stats_path.exists():
            continue
        raw = np.fromfile(audio_path, dtype='<f4')
        format_path = directory/f'local-ec-{name}-format.json'
        version = json.loads(format_path.read_text())['version'] if format_path.exists() else 1
        columns = 5 if version == 2 else 4
        if len(raw)%columns:
            raise ValueError(f'{audio_path}: incomplete telemetry row')
        data = raw.reshape(-1,columns).astype(float)
        x = data[:,[0,2,3,4]] if version == 2 else data
        with stats_path.open() as stream:
            records = list(csv.DictReader(stream))
        if not len(x):
            continue
        if not np.isfinite(x).all():
            raise ValueError('non-finite telemetry')
        flags = x[:,3].astype(np.uint16)
        p_in, p_out = np.mean(x[:,1:3]**2,axis=0)
        item = dict(samples=len(x), stats_windows=len(records), format_version=version,
            input_position=('after receive correction, before echo filter sum' if version==2 else
                            'before receive correction and echo filter sum (legacy)'),
            input_rms=float(np.sqrt(p_in)), residual_rms=float(np.sqrt(p_out)),
            applied_difference_rms=float(np.sqrt(np.mean((x[:,1]-x[:,2])**2))),
            input_residual_power_reduction_db=power_ratio(p_in,p_out),
            max_near_nonzero=max((int(r['near_nonzero']) for r in records),default=0),
            max_far_nonzero=max((int(r['far_nonzero']) for r in records),default=0),
            max_near_coefficient_norm=max((float(r['near_norm']) for r in records),default=0),
            max_far_coefficient_norm=max((float(r['far_norm']) for r in records),default=0),
            max_near_adapt_count=max((int(r['near_adapt_count']) for r in records),default=0),
            max_far_adapt_count=max((int(r['far_adapt_count']) for r in records),default=0))
        if version == 2:
            item['raw_input_rms'] = float(np.sqrt(np.mean(data[:,1]**2)))
            item['receive_correction_rms'] = float(np.sqrt(np.mean((data[:,2]-data[:,1])**2)))
            report['interpretation'] = ('Corrected-input/residual power reduction isolates the echo filter sum '
                'but includes the far-end signal; it is not echo-only ERLE. No far-end silence interval is assumed.')
        if name == 'v34':
            item.update(sample_rate=9600, duration_seconds=len(x)/9600,
                        feed_enabled_samples=int(np.count_nonzero(flags&0x200)),
                        frozen_samples=int(np.count_nonzero(flags&0x4)),
                        data_flag_samples=int(np.count_nonzero(flags&0x10)))
            item['nonzero_integer_coefficients_observed'] = bool(item['max_near_nonzero'] or item['max_far_nonzero'])
            item['coefficient_precision'] = 'Q14 integer filter coefficients; fractional adaptation accumulators are not captured'
            item['status'] = ('coefficients observed' if item['nonzero_integer_coefficients_observed'] else
                              'no nonzero integer coefficients observed')
            item['intervals'] = {}
            for label, mask in [('feed_enabled',(flags&0x200)!=0),
                                ('data_with_feed',((flags&0x210)==0x210)),
                                ('unfrozen_with_feed',((flags&0x204)==0x200))]:
                count = int(mask.sum())
                group = dict(samples=count)
                if count:
                    before,after = np.mean(x[mask,1:3]**2,axis=0)
                    group.update(input_rms=float(np.sqrt(before)),residual_rms=float(np.sqrt(after)),
                        applied_difference_rms=float(np.sqrt(np.mean((x[mask,1]-x[mask,2])**2))),
                        input_residual_power_reduction_db=power_ratio(before,after))
                item['intervals'][label] = group
        else:
            item['state_values'] = np.unique(flags).tolist()
        report['streams'][name] = item
        if not records:
            continue
        stats = {k:np.array([float(r[k]) for r in records]) for k in records[0]}
        rate = 9600 if name == 'v34' else 1
        t = stats['sample_end']/rate
        fig, axes = plt.subplots(4,1,figsize=(12,10),sharex=True)
        for key,label in [('input_power','Input'),('residual_power','Residual'),('estimate_power','Applied difference')]:
            power = stats[key]
            levels = np.full(len(power),np.nan)
            positive = power>0
            levels[positive] = 10*np.log10(power[positive]/(32768**2 if name=='v34' else 1))
            axes[0].plot(t,levels,label=label+(' (zero)' if not positive.any() else ''))
        axes[0].set_ylabel('Power (dB re full scale)' if name=='v34' else 'Power (dB)')
        reduction = np.full(len(t),np.nan)
        valid = (stats['input_power']>0)&(stats['residual_power']>0)
        reduction[valid] = 10*np.log10(stats['input_power'][valid]/stats['residual_power'][valid])
        axes[1].plot(t,reduction,label='Total input / residual (not echo-only ERLE)')
        axes[1].set_ylabel('Power reduction (dB)')
        axes[2].plot(t,stats['near_norm'],label='Near coefficient norm')
        axes[2].plot(t,stats['far_norm'],label='Far coefficient norm')
        axes[2].set_ylabel('Coefficient L2 norm')
        if name=='v34':
            f = stats['flags'].astype(np.uint16)
            for mask,label,offset in ((0x200,'Feed enabled',0),(4,'Frozen',1.5),(0x10,'Data flag',3)):
                axes[3].step(t,((f&mask)!=0)+offset,where='post',label=label)
            axes[3].set_ylabel('Flags (offset for clarity)')
        else:
            axes[3].step(t,stats['flags'],where='post',label='State')
        axes[3].set_xlabel('Seconds since first observed V.34 EC invocation' if name=='v34' else 'Observed sample count')
        for ax in axes:
            ax.grid(True,alpha=.3); ax.legend(fontsize=8)
        fig.suptitle(directory.name+' — local DSP '+name.upper()+' echo telemetry')
        fig.tight_layout(); fig.savefig(directory/f'local-ec-{name}.png',dpi=150); plt.close(fig)
    report['available'] = bool(report['streams'])
    (directory/'local-ec.json').write_text(json.dumps(report,indent=2)+'\n')
    lines = [report['perspective'],report['interpretation']]
    for name,item in report['streams'].items():
        lines.extend([name.upper(),json.dumps(item,indent=2)])
    if not report['available']:
        lines.append('No local EC instrumentation capture is present.')
    (directory/'local-ec.txt').write_text('\n'.join(lines)+'\n')
    return report


def selftest():
    import tempfile
    # Synthetic apparatus control, not a claim of reachable DSP state.
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp)
        x = np.tile([100,800,80,512],(9600,1)).astype('<f4')
        x.tofile(path/'local-ec-v34.f32')
        header = 'sample_end,samples,path,txstate,rxstate,flags,far_enabled,near_nonzero,far_nonzero,near_norm,far_norm,near_adapt_count,far_adapt_count,input_power,residual_power,estimate_power\n'
        (path/'local-ec-v34.csv').write_text(header+'9600,9600,0,3,0,512,0,1,0,0.1,0,4,0,640000,6400,518400\n')
        report = analyse(path)['streams']['v34']
        assert abs(report['input_residual_power_reduction_db']-20)<1e-10
        assert report['feed_enabled_samples']==9600 and report['max_near_nonzero']==1
        v2 = np.column_stack([x[:,0],x[:,1]-100,x[:,1:]])
        v2.astype('<f4').tofile(path/'local-ec-v34.f32')
        (path/'local-ec-v34-format.json').write_text('{"version":2}')
        report = analyse(path)['streams']['v34']
        assert report['receive_correction_rms']==100 and report['applied_difference_rms']==720
        v2[:,1:4]=0; v2[:,4]=4; v2.astype('<f4').tofile(path/'local-ec-v34.f32')
        report = analyse(path)['streams']['v34']
        assert report['input_residual_power_reduction_db'] is None and report['feed_enabled_samples']==0
    print('PASS: 9600-sample synthetic 20 dB reduction, coefficient/feed detection; silence returns no ratio')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('directory',type=Path,nargs='?')
    ap.add_argument('--selftest',action='store_true')
    args = ap.parse_args()
    if args.selftest:
        selftest(); return
    if args.directory is None:
        ap.error('directory required')
    print(json.dumps(analyse(args.directory.resolve()),indent=2))


if __name__=='__main__':
    main()
