#!/usr/bin/env python3
"""Standalone descriptive curves, mean and min/max of three solver seeds (not CI)."""
import json,argparse,bisect,statistics
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
MODES=['plain_only','plain_extra','projection_escape'];COLORS=['#0072B2','#E69F00','#009E73']
def main():
 p=argparse.ArgumentParser();p.add_argument('input',type=Path);a=p.parse_args();rows=[json.loads(x.read_text()) for x in (a.input/'audit').glob('*.json')];cases=sorted({r['instance'] for r in rows});names=['Bromberger','SMT_random','convert'];fig,axes=plt.subplots(1,3,figsize=(12,3.5),layout='constrained')
 times=[i/100 for i in range(1001)]
 for ax,case,title in zip(axes,cases,names):
  for mode,color in zip(MODES,COLORS):
   rr=[r for r in rows if r['instance']==case and r['mode']==mode];curves=[]
   for r in rr:
    tt=[0.];cc=[0.]
    for c in r['curve']:tt.append(c['seconds']);cc.append(100*(c['covered']-r['base_covered'])/r['total'])
    curves.append([cc[bisect.bisect_right(tt,t)-1] for t in times])
   mean=list(map(statistics.mean,zip(*curves)));low=list(map(min,zip(*curves)));high=list(map(max,zip(*curves)))
   ax.plot(times,mean,label=mode,color=color,lw=1.8);ax.fill_between(times,low,high,color=color,alpha=.13,linewidth=0)
  ax.set(title=title,xlabel='Complete session time (s)',xlim=(0,10),ylim=(0,None));ax.grid(alpha=.2)
 axes[0].set_ylabel('New coverage (percentage points)');axes[2].legend(fontsize=8,loc='lower right');fig.suptitle('Round 10: 3 solver seeds; shading = min/max, not confidence interval',fontsize=11)
 out=a.input/'figures';out.mkdir(exist_ok=True)
 for suffix in ('png','pdf','svg'):fig.savefig(out/('coverage_curves.'+suffix),dpi=180)
 plt.close(fig)
if __name__=='__main__':main()
