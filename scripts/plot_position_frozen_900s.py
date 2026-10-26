#!/usr/bin/env python3
"""Standalone scientific plots from audited saved staircases, no solver imports."""
import argparse,gzip,json,hashlib,sys
from collections import defaultdict
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def stairs(rows,end=900):return [0]+[r['completion'] for r in rows]+[end],[0]+[r['covered'] for r in rows]+[rows[-1]['covered'] if rows else 0]
def difference(first,position,end=900):
    events=defaultdict(int)
    for sign,rows in [(-1,first),(1,position)]:
        for r in rows:events[r['completion']]+=sign*r['gain']
    x=[0];y=[0];current=0
    for t,delta in sorted(events.items()):current+=delta;x.append(t);y.append(current)
    return x+[end],y+[current]
def main():
    a=argparse.ArgumentParser();a.add_argument('out',type=Path);args=a.parse_args();out=args.out;dest=out/'figures';dest.mkdir(exist_ok=True);source=out/'analysis/full_curves.jsonl.gz';runs=defaultdict(list)
    plan=json.loads((out/'plan.json').read_text());summary=json.loads((out/'analysis/summary.json').read_text())
    with gzip.open(source,'rt') as f:
        for line in f:
            r=json.loads(line);runs[r['formula'],r['seed'],r['configuration']].append(r)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'axes.spines.top':False,'axes.spines.right':False,'savefig.dpi':180})
    for formula in [i['name'] for i in plan['inputs']]:
        short='random' if 'random' in formula else 'real';fig,axes=plt.subplots(4,2,figsize=(11,12),sharex=True,layout='constrained');means=defaultdict(lambda:defaultdict(float))
        for ax,seed in zip(axes.flat,plan['seeds']):
            for mode,color in [('first_immediate','#2563a6'),('position_immediate','#d95b27')]:
                rows=runs[formula,seed,mode];x,y=stairs(rows);ax.step(x,y,where='post',color=color,label=mode,linewidth=1.2)
                for r in rows:means[mode][r['completion']]+=r['gain']/len(plan['seeds'])
            ax.set_title('Seed '+str(seed));ax.set_ylabel('Covered bits');ax.grid(alpha=.2);ax.set_xlim(0,900)
        ax=axes.flat[-1]
        for mode,color in [('first_immediate','#2563a6'),('position_immediate','#d95b27')]:
            current=0;x=[0];y=[0]
            for t,delta in sorted(means[mode].items()):current+=delta;x.append(t);y.append(current)
            ax.step(x+[900],y+[current],where='post',label=mode,color=color,linewidth=1.8)
        ax.set_title('Mean of seven saved step curves');ax.set_ylabel('Covered bits');ax.legend();ax.grid(alpha=.2)
        for ax in axes[-1]:ax.set_xlabel('Elapsed seconds (input loading included)')
        fig.suptitle(f'{short}: frozen position policy, 900 s / seven new seeds')
        for ext in ['png','pdf']:fig.savefig(dest/(short+'_coverage.'+ext))
        plt.close(fig)
        fig,axes=plt.subplots(2,1,figsize=(11,7),layout='constrained');finals=[]
        for seed in plan['seeds']:
            x,y=difference(runs[formula,seed,'first_immediate'],runs[formula,seed,'position_immediate']);axes[0].step(x,y,where='post',label=str(seed),linewidth=1);finals.append(y[-1])
        axes[0].axhline(0,color='black',linewidth=.8);axes[0].set(xlim=(0,900),xlabel='Elapsed seconds',ylabel='Covered-bit difference',title='Position minus first: exact saved staircases');axes[0].legend(title='Seed',ncol=7);axes[0].grid(alpha=.2)
        axes[1].bar([str(s) for s in plan['seeds']],finals,color=['#d95b27' if x>=0 else '#2563a6' for x in finals]);axes[1].axhline(0,color='black',linewidth=.8);axes[1].set(xlabel='New solver seed',ylabel='900 s covered-bit difference');fig.suptitle(short)
        for ext in ['png','pdf']:fig.savefig(dest/(short+'_difference.'+ext))
        plt.close(fig)
    manifest=dict(source=str(source.relative_to(out)),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),matplotlib_version=matplotlib.__version__,python=sys.version,method='all audited submission events, post steps; mean at union of actual event times, no checkpoint interpolation',files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dest.glob('*')) if p.suffix in ['.png','.pdf']})
    (dest/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
if __name__=='__main__':main()
