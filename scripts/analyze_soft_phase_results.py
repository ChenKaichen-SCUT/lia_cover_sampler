#!/usr/bin/env python3
"""Pre-frozen paired comparisons. Derived filtering is offline only."""
import argparse,csv,json,statistics
from pathlib import Path
from itertools import combinations
from reevaluate_round6_saved_candidates import dump,write_csv,require
PREFIXES=[2,5,10,50,100]
MODES=['plain','random_soft','coverage_soft']
def med(xs):return statistics.median(xs) if xs else None
def main():
    a=argparse.ArgumentParser();a.add_argument('out');args=a.parse_args();out=Path(args.out);audit=json.loads((out/'audit_summary.json').read_text());require(audit['passed'],'audit failed')
    rows=[];prefixes=[];derived=[];times=[];attain=[]
    for r in audit['runs']:
        base={k:r[k] for k in ('run','formula','seed','configuration','seconds')}
        with (out/'audit'/r['run']/'timeline.csv').open() as f:ts=[{k:float(v) if k in ('completion','covered','observed','gain') else v for k,v in x.items()} for x in csv.DictReader(f)]
        prev=0;filtered=[];longest=0;last_new=0;zeros=0;max_zero=0;observed=0;right_censored=False
        for x in ts:
            new=x['observed']-prev;prev=x['observed'];observed=x['observed']
            if new:
                longest=max(longest,x['completion']-last_new);last_new=x['completion'];zeros=0;filtered.append(x)
                derived.append(dict(base,derived_index=len(filtered),original_index=x['index'],completion=x['completion'],covered=x['covered'],observed=x['observed']))
            else:zeros+=1;max_zero=max(max_zero,zeros)
        tail=r['seconds']-last_new;right_censored=tail>0;longest=max(longest,tail)
        costs=(r.get('costs') or {}).get('seconds',{})
        # Stats JSON has named timing map; assert schema rather than silently lose costs.
        if r.get('costs'):require('seconds' in r['costs'],'unexpected costs schema')
        extra=sum(costs.get(k,0) for k in ('summary_preparation','summary_log','feedback_scoring_and_table_encoding','table_install','hook_log_capture_and_clear'))+r['phase_counters']['lookup_seconds']
        row=dict(base,models=r['distinct'],models_per_second=r['distinct']/r['seconds'],new_observations=observed,new_observations_per_second=observed/r['seconds'],covered=r['covered'],coverage=r['coverage'],first_model_seconds=r['first_model_seconds'],process_wall_seconds=r['process_wall_seconds'],new_observation_models=len(filtered),zero_new_observation_fraction=(len(ts)-len(filtered))/len(ts) if ts else None,longest_no_observation_seconds=longest,tail_no_observation_seconds=tail,tail_right_censored=right_censored,longest_zero_observation_models=max_zero,extra_seconds=extra,supported_atoms=r['supported_atoms'],applied_realized=r['applied_realized'],applied_not_realized=r['applied_not_realized'],**r['phase_counters'])
        rows.append(row)
        for label,sequence in [('original',ts),('new_observation_derived',filtered)]:
            for n in PREFIXES:
                ok=len(sequence)>=n;x=sequence[n-1] if ok else None
                prefixes.append(dict(base,sequence=label,prefix=n,complete=ok,covered=x['covered'] if x else None,arrival_seconds=x['completion'] if x else None))
        for t in [10,30,60]:
            if t>r['seconds']:continue
            available=[x for x in ts if x['completion']<=t]
            times.append(dict(base,time=t,covered=available[-1]['covered'] if available else 0,models=len(available)))
        # Shared absolute targets are determined only from plain's observed curve below.
        attain.append((base,ts))
    pairs=[]
    for f in sorted({r['formula'] for r in rows}):
        for seed in sorted({r['seed'] for r in rows if r['formula']==f}):
            group={r['configuration']:r for r in rows if r['formula']==f and r['seed']==seed}
            for left,right in [('coverage_soft','plain'),('coverage_soft','random_soft'),('random_soft','plain')]:
                x,y=group[left],group[right]
                pairs.append(dict(formula=f,seed=seed,left=left,right=right,covered_difference=x['covered']-y['covered'],model_difference=x['models']-y['models'],observation_difference=x['new_observations']-y['new_observations'],extra_seconds_difference=x['extra_seconds']-y['extra_seconds']))
    prefix_pairs=[]
    for r in prefixes:
        if r['configuration']!='coverage_soft':continue
        for control in ['plain','random_soft']:
            other=next(x for x in prefixes if all(x[k]==r[k] for k in ('formula','seed','sequence','prefix')) and x['configuration']==control)
            ok=r['complete'] and other['complete'];prefix_pairs.append(dict(formula=r['formula'],seed=r['seed'],sequence=r['sequence'],prefix=r['prefix'],left='coverage_soft',right=control,complete=ok,difference=r['covered']-other['covered'] if ok else None))
    equal=[]
    for base,seq in attain:
        ref=next(s for b,s in attain if b['formula']==base['formula'] and b['seed']==base['seed'] and b['configuration']=='plain')
        final=ref[-1]['covered'] if ref else 0
        for fraction in [.25,.5,.75,1.]:
            target=int(final*fraction);hit=next((x for x in seq if x['covered']>=target),None) if target else None
            equal.append(dict(base,plain_final_fraction=fraction,target_covered=target,reached=bool(hit),arrival_seconds=hit['completion'] if hit else None))
    decisions=[]
    for f in sorted({r['formula'] for r in rows}):
        group=[r for r in rows if r['formula']==f];cov=[r for r in group if r['configuration']=='coverage_soft'];plain=[r for r in group if r['configuration']=='plain']
        seeds={r['seed'] for r in cov};both=sum(all(next(p for p in pairs if p['formula']==f and p['seed']==s and p['left']=='coverage_soft' and p['right']==control)['covered_difference']>0 for control in ('plain','random_soft')) for s in seeds)
        medians={control:med([p['covered_difference'] for p in pairs if p['formula']==f and p['left']=='coverage_soft' and p['right']==control]) for control in ('plain','random_soft')}
        throughput=med([r['models_per_second'] for r in cov])/med([r['models_per_second'] for r in plain]) if med([r['models_per_second'] for r in plain]) else None
        negatives={control:[n for n in PREFIXES if sum(p['complete'] and p['difference']<0 for p in prefix_pairs if p['formula']==f and p['right']==control and p['sequence']=='original' and p['prefix']==n)>=2] for control in ('plain','random_soft')}
        positive=len(seeds)==3 and both>=2 and all(v>0 for v in medians.values());supply=throughput is not None and throughput>=.9;early=any(len(ns)>=2 for ns in negatives.values());cost=med([r['extra_seconds']/r['seconds'] for r in cov])>.1
        decisions.append(dict(formula=f,seeds=len(seeds),both_positive_seeds=both,median_differences=medians,throughput_ratio=throughput,early_regression_prefixes=negatives,cost_risk=cost,repeated_positive=positive,passes_guard=positive and supply and not early and not cost,interpretation='performance_screen' if len(seeds)==3 else 'functionality_only_not_performance_decision'))
    summaries=[]
    for f in sorted({p['formula'] for p in pairs}):
        for left,right in [('coverage_soft','plain'),('coverage_soft','random_soft'),('random_soft','plain')]:
            xs=[p['covered_difference'] for p in pairs if p['formula']==f and p['left']==left and p['right']==right]
            summaries.append(dict(formula=f,left=left,right=right,wins=sum(x>0 for x in xs),ties=sum(x==0 for x in xs),losses=sum(x<0 for x in xs),median_difference=med(xs),mean_difference=statistics.mean(xs)))
    for name,data in [('sessions',rows),('prefixes',prefixes),('derived_observation_sequence',derived),('time_coverage',times),('paired_differences',pairs),('prefix_pairs',prefix_pairs),('equal_coverage_time',equal),('paired_summary',summaries)]:write_csv(out/(name+'.csv'),data)
    dump(out/'decision.json',dict(rules='docs/覆盖软偏好实验协议.md',formulas=decisions,formal=all(d['seeds']==3 for d in decisions),positive_formulas=sum(d['passes_guard'] for d in decisions)))
    print(json.dumps(decisions,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
