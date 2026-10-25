#!/usr/bin/env python3
"""Predeclared paired metrics from independently audited native-phase traces."""
import argparse,csv,json,statistics
from pathlib import Path
from reevaluate_round6_saved_candidates import dump,write_csv,require

PREFIXES=(2,5,10,50,100,1000)
COMPARISONS=(('native_random_one','catalog_random_one'),('native_feedback_one','native_random_one'),('native_feedback_one','plain'))
def read(path):
    with path.open() as f:return list(csv.DictReader(f))
def average(xs):return statistics.mean(xs) if xs else None
def median(xs):return statistics.median(xs) if xs else None
def main():
    p=argparse.ArgumentParser();p.add_argument('out');a=p.parse_args();out=Path(a.out).resolve()
    audit=json.loads((out/'audit_summary.json').read_text());require(audit['passed'],'independent audit required')
    results=[];prefixes=[];times=[];predictions=[]
    for run in audit['runs']:
        name=run['run'];timeline=read(out/'audit'/name/'timeline.csv');decisions=read(out/'audit'/name/'decision_audit.csv')
        common={k:run[k] for k in ('formula','seed','configuration','group','run','seconds','total')}
        previous=0;first=0;after=first_side=covered_after=zero=longest_zero=0
        for i,row in enumerate(timeline):
            obs=int(row['observed'])-previous;previous=int(row['observed'])
            if i==0:first=obs
            else:after+=obs;first_side+=obs-int(row['gain']);covered_after+=int(row['gain'])
            zero=zero+1 if obs==0 else 0;longest_zero=max(zero,longest_zero)
        for n in PREFIXES:
            row=timeline[n-1] if len(timeline)>=n else None
            prefixes.append(dict(common,prefix=n,completed=row is not None,covered=int(row['covered']) if row else None,coverage=float(row['coverage']) if row else None,completion=float(row['completion']) if row else None))
        for t in (30,60,120):
            if t>run['seconds']:continue
            available=[row for row in timeline if float(row['completion'])<=t]
            value=int(available[-1]['covered']) if available else 0
            times.append(dict(common,time_seconds=t,covered=value,coverage=value/run['total'] if run['total'] else 0,model_count=len(available)))
        selected=[r for r in decisions if r['selected']=='True'];model=[r for r in selected if r['predicted_selected_gain']!='']
        for group in ('all','cold_start','after_warmup'):
            rs=[r for r in model if group=='all' or (r['cold_start']=='True')==(group=='cold_start')]
            predictions.append(dict(common,feedback_stage=group,selected_queries=len(rs),gain_mae=average([abs(float(r['gain_prediction_error'])) for r in rs]),
                cost_mae_seconds=average([abs(float(r['cost_prediction_error'])) for r in rs]),
                unequal_utility_predictions=sum(float(r['predicted_utility_gap'])!=0 for r in rs),positive_delivered_labels=sum(float(r['observed_reward'])>0 for r in rs),
                warning='Only chosen actions; no counterfactual regret or independent-atom causal claim'))
        counters=run['native_counters'];n=counters.get('selected',0);query_counts=run['query_counts'] or dict(sat=0,unknown=0,unsat=0)
        results.append(dict(common,covered=run['covered'],coverage=run['coverage'],formal_models=run['distinct'],process_wall_seconds=run['process_wall_seconds'],
            first_model_seconds=run['first_model_seconds'],first_model_observations=first,post_first_observations=after,post_first_only_first_side=first_side,post_first_covered=covered_after,
            longest_no_growth_seconds=run['longest_no_growth_seconds'],longest_zero_new_observation_models=longest_zero,
            sat=query_counts['sat'],unknown=query_counts['unknown'],selected=n,keep=counters.get('KeepNative',0),flip=counters.get('FlipOnce',0),
            outside_catalog=counters.get('outside_catalog',0),outside_catalog_fraction=counters.get('outside_catalog',0)/n if n else None,
            supported_encounters=counters.get('supported_encounters',0),outside_catalog_encounters=counters.get('outside_catalog_encounters',0),native_decisions_examined=counters.get('native_decisions_examined',0),
            realized=counters.get('realized',0),unrealized=counters.get('unrealized',0),no_model=counters.get('no_model',0),cold_start=counters.get('cold',0),
            exploration=counters.get('exploration',0),exploitation=counters.get('exploitation',0),mechanism_seconds=run['mechanism_seconds'],mechanism_stop=run['mechanism_stop'],
            selected_positive_gain=sum(int(r['formal_gain'])>0 for r in selected),selected_zero_gain=sum(int(r['formal_gain'])==0 for r in selected),
            feature_seconds=run['feature_seconds'],prediction_seconds=run['prediction_seconds'],training_seconds=run['training_seconds'],
            check_seconds=sum(float(r['check_seconds']) for r in decisions),hook_seconds_nested=sum(float(r['hook_seconds']) for r in decisions),
            full_cycle_seconds=run['cycle_cost_sum'],tail_seconds=run['tail_cost'],incomplete_prefixes=sum(len(timeline)<n for n in PREFIXES)))
    paired=[]
    for collection,dimension,field in ((prefixes,'prefix','covered'),(times,'time_seconds','covered')):
        for formula in dict.fromkeys(r['formula'] for r in collection):
            for seed in sorted({r['seed'] for r in collection if r['formula']==formula}):
                for checkpoint in sorted({r[dimension] for r in collection if r['formula']==formula}):
                    rs={r['configuration']:r for r in collection if r['formula']==formula and r['seed']==seed and r[dimension]==checkpoint}
                    for left,right in COMPARISONS:
                        x,y=rs[left],rs[right];complete=dimension!='prefix' or x['completed'] and y['completed']
                        paired.append(dict(formula=formula,seed=seed,dimension=dimension,checkpoint=checkpoint,left=left,right=right,completed=complete,
                            left_value=x[field] if complete else None,right_value=y[field] if complete else None,delta=x[field]-y[field] if complete else None,
                            left_run=x['run'],right_run=y['run'],total=x['total']))
    aggregates=[]
    keys=list(dict.fromkeys((r['formula'],r['dimension'],r['checkpoint'],r['left'],r['right']) for r in paired))
    for key in keys:
        rs=[r for r in paired if tuple(r[k] for k in ('formula','dimension','checkpoint','left','right'))==key];ds=[r['delta'] for r in rs if r['completed']]
        aggregates.append(dict(zip(('formula','dimension','checkpoint','left','right'),key),pairs=len(ds),incomplete_pairs=len(rs)-len(ds),wins=sum(d>0 for d in ds),ties=sum(d==0 for d in ds),losses=sum(d<0 for d in ds),mean_delta=average(ds),median_delta=median(ds)))
    output=out/'analysis';output.mkdir(exist_ok=True)
    for name,rs in (('sessions',results),('prefixes',prefixes),('time_points',times),('paired',paired),('paired_summary',aggregates),('prediction_errors',predictions)):write_csv(output/(name+'.csv'),rs)
    dump(output/'summary.json',dict(passed=True,sessions=results,paired_summary=aggregates,stage=json.loads((out/'plan.json').read_text())['kind'],
        warning='Development data; session is the replicate, not each native decision. Exposure kept separate.'))
    print('Paired native-feedback analysis complete:',len(results),'sessions',flush=True)
if __name__=='__main__':main()
