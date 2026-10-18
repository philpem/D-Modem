#!/usr/bin/env python3
"""Compare CRC-checked INFO0 transmissions and recovery acknowledgments in calls."""
import argparse
import json
import os
from pathlib import Path

os.environ.setdefault('MPLCONFIGDIR','/tmp/slmodem-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


def load_frames(directory, direction):
    choices=(directory/f'info0-{direction}.json',directory/f'modem_{direction}_8k.info0.json')
    path=next((path for path in choices if path.exists()),None)
    if path is None:
        raise ValueError(f'{directory}: run analyse_call.py first')
    return json.loads(path.read_text())


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('calls',nargs='+',type=Path)
    ap.add_argument('--out',required=True,type=Path)
    args=ap.parse_args()
    fig,axes=plt.subplots(len(args.calls),1,figsize=(12,2.5*len(args.calls)+1),
                          sharex=True,squeeze=False)
    summaries=[]
    for axis,directory in zip(axes[:,0],args.calls):
        result=json.loads((directory/'result.json').read_text())
        local='caller' if result['originator']=='dmodem' else 'answerer'
        remote='answerer' if local=='caller' else 'caller'
        tx=load_frames(directory,'tx');rx=load_frames(directory,'rx')
        local_frames=[frame for frame in tx['frames'] if frame['sender']==local]
        remote_frames=[frame for frame in rx['frames'] if frame['sender']==remote]
        if not local_frames or not remote_frames:
            raise ValueError(f'{directory}: need valid frames from both physical transmitters')
        origin=min(frame['time_seconds'] for frame in local_frames+remote_frames)
        counts={}
        for name,frames,y,color in (('local_tx',local_frames,0,'#c54936'),
                                    ('remote_rx',remote_frames,1,'#236ea0')):
            counts[name]=dict(frames=len(frames),recovery_ack_frames=sum(
                frame['capabilities']['recovery_ack'] for frame in frames))
            for ack in (False,True):
                times=[frame['time_seconds']-origin for frame in frames
                       if frame['capabilities']['recovery_ack']==ack]
                axis.scatter(times,[y]*len(times),s=25,marker='s' if ack else 'o',
                             facecolors=color if ack else 'none',edgecolors=color)
        axis.set(yticks=(0,1),yticklabels=('Local TX','Remote RX'),ylim=(-.5,1.6),
                 title=f'{directory.name}: {result["status"]}')
        axis.grid(axis='x',alpha=.25)
        axis.text(.99,.93, f'Local {len(local_frames)} frames; remote {len(remote_frames)} frames; '
                  f'remote ACK {counts["remote_rx"]["recovery_ack_frames"]}',
                  transform=axis.transAxes,ha='right',va='top',fontsize=9)
        summaries.append(dict(call=str(directory),status=result['status'],**counts))
    axes[-1,0].set_xlabel('Seconds from first decoded INFO0 in this call')
    fig.suptitle('V.34 INFO0 exchange decoded from audio (CRC checked)')
    fig.legend(handles=[Line2D([],[],marker='o',color='gray',markerfacecolor='none',
                               linestyle='none',label='ACK bit 0'),
                        Line2D([],[],marker='s',color='gray',linestyle='none',label='ACK bit 1')],
               loc='lower center',ncol=2,bbox_to_anchor=(.5,.035))
    fig.text(.5,.01,'Returned local echo in RX is excluded from the remote row.',ha='center',fontsize=9)
    fig.tight_layout(rect=(0,.1,1,.95))
    fig.savefig(args.out,dpi=150)
    args.out.with_suffix('.json').write_text(json.dumps(summaries,indent=2)+'\n')
    plt.close(fig)


if __name__=='__main__':
    main()
