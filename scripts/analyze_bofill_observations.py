#!/usr/bin/env python3
"""Observation complementarity of saved witnesses, without generating or selecting new outputs."""
import json
from pathlib import Path
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,dump,write_csv,model_id,file_hash
ROOT=Path(__file__).resolve().parents[1]
def merge(a,b):return {k:(int(a[k][0])|int(b[k][0]),int(a[k][1])|int(b[k][1]),a[k][2]) for k in a}
def observed(s):return sum((int(a)|int(b)).bit_count() for a,b,_ in s.values())
def contribution(before,after,kind):
    return sum(((int(a)&int(b))&~(int(before[k][0])&int(before[k][1]))).bit_count() for k,(a,b,t) in after.items() if t==kind)
def main():
    out=ROOT/'results/bofill_diagnosis/observation_analysis';out.mkdir(exist_ok=False);modelrows=[];histrows=[];prefix=[];oldrows=[]
    old=ROOT/'results/bofill_900s';historical=json.loads((old/'audit_summary.json').read_text())
    with no_search() as searches:
        plan=json.loads((ROOT/'results/bofill_diagnosis/first_sat_30s/plan.json').read_text())
        for fi,formula in enumerate(sorted({r['formula'] for r in plan['runs']})):
            case='random' if 'SMT_random' in formula else 'real';offset=0 if case=='random' else 3
            metric=FrozenCoverage((ROOT/'benchmarks'/formula).read_text());history=[]
            for seed in [0,1,2]:
                src=old/'runs'/f'{offset+seed:02d}_plain_clean/raw/models.jsonl'
                for row in list(map(json.loads,src.read_text().splitlines()))[:10]:history.append((seed,row,metric.apply(metric.empty.copy(),row['model'])))
            h0=[state for seed,_,state in history if seed==0]
            for experiment in ['first_sat_30s','first_sat_120s']:
                base=ROOT/'results/bofill_diagnosis'/experiment;config=json.loads((base/'plan.json').read_text())
                for spec in [r for r in config['runs'] if r['formula']==formula]:
                    d=base/spec['directory'];audit=json.loads((d/'audit.json').read_text());s=metric.empty.copy();info=dict(experiment=experiment,formula=formula,mode=spec['mode'],seed=spec['seed'],control=spec['control'])
                    if audit['accepted']:
                        row=json.loads((d/'raw/candidate.json').read_text());s=metric.apply(s,row['model']);combined=s.copy()
                        for other in h0:combined=merge(combined,other)
                        modelrows.append(dict(**info,kind='single_diagnostic_witness_not_sampling_trajectory',model_sha256=model_id(row['model']),index=1,new_covered_bits=metric.count(s),first_observed_bits=observed(s),opposite_completion_bits=metric.count(s),changed_original_variables=None,recent_repeat_observation_ratio=None,bool_covered=contribution(metric.empty,s,'bool'),int_covered=contribution(metric.empty,s,'int'),bool_first_observed=sum((int(a)|int(b)).bit_count() for a,b,t in s.values() if t=='bool'),int_first_observed=sum((int(a)|int(b)).bit_count() for a,b,t in s.values() if t=='int'),historical_seed0_first10_joint_extra=metric.count(combined)-metric.count(s)))
                    for k in [1,2,5,10,50,100]:prefix.append(dict(**info,prefix=k,completed=bool(audit['accepted'] and k==1),covered=metric.count(s) if audit['accepted'] and k==1 else None,reason='first-SAT probe stops at one model' if k>1 else 'timely witness' if audit['accepted'] else 'no timely witness'))
            # Separate historical final plateaus from the new first-SAT evidence.
            final=json.loads((old/'audit'/f'{offset:02d}_plain_clean/coverage_final.json').read_text());state=metric.empty.copy()
            for n,(zero,one) in zip(metric.nodes,final['states']):state[n.get_id()]=(int(one),int(zero),state[n.get_id()][2])
            assert metric.count(state)==final['covered'];joint=state.copy()
            for seed,row,obs in history:
                if seed==0:continue
                individual=metric.count(merge(state,obs))-metric.count(state);after=merge(joint,obs);marginal=metric.count(after)-metric.count(joint);joint=after
                histrows.append(dict(formula=formula,baseline='historical900s_plain_seed0_final',source_seed=seed,source_index=row['index'],model_sha256=model_id(row['model']),independent_extra=individual,ordered_union_marginal=marginal,ordered_union_extra=metric.count(joint)-metric.count(state),interpretation='offline witness only; not supplied to solver'))
            for r in historical['runs']:
                if r['formula']==formula and r['configuration']=='plain_clean':oldrows.append(dict(formula=formula,seed=r['seed'],models=r['distinct'],positive=r['positive'],zero_fraction=1-r['positive']/r['distinct'],longest_zero_models=r['longest_zero_gain_models'],longest_no_growth_seconds=r['longest_no_growth_seconds'],covered=r['covered']))
        assert not searches
    for name,rows in [('new_diagnostic_witnesses',modelrows),('new_prefix_availability',prefix),('historical_plateau_witnesses',histrows),('historical_zero_gain',oldrows)]:write_csv(out/(name+'.csv'),rows)
    dump(out/'summary.json',dict(no_search_calls=0,new_sampling_sessions=0,diagnostic_witnesses=len(modelrows),historical_analysis='fixed seeds1,2 first10 in order evaluated against historical seed0 final; no future candidates in performance',caveat='No 2/5/10-model new trajectories exist; no quality or throughput advantage claimed',hashes={str(p.relative_to(out)):file_hash(p) for p in out.glob('*.csv')}))
if __name__=='__main__':main()
