#!/usr/bin/env python3
"""Frozen descriptive metrics and S2 gate; never schedules a search."""
import argparse,csv,json,statistics
from collections import defaultdict,Counter
from pathlib import Path
from reevaluate_round6_saved_candidates import dump,write_csv
from audit_arith_coverage import table

def read(p):
    with p.open() as f:return list(csv.DictReader(f))
def summaries(values):
    return dict(n=len(values),mean=statistics.mean(values) if values else None,median=statistics.median(values) if values else None,wins=sum(x>0 for x in values),ties=sum(x==0 for x in values),losses=sum(x<0 for x in values),worst=min(values) if values else None)
def main():
    p=argparse.ArgumentParser();p.add_argument('out',type=Path);a=p.parse_args();out=a.out;plan=json.loads((out/'plan.json').read_text());audit=json.loads((out/'audit_summary.json').read_text());assert audit['passed'];metrics=[];prefixes=[];bins=[]
    for r in audit['runs']:
        rs=read(out/'audit'/r['run']/'delivery.csv');common={k:r[k] for k in ['formula','seed','configuration','run','total']};seconds=plan['seconds']
        for cutoff in sorted({1,5,10,30,60,seconds}&set(range(1,seconds+1))):
            eligible=[x for x in rs if float(x['completion'])<=cutoff];end=int(eligible[-1]['covered']) if eligible else 0;auc=sum(int(x['gain'])*(cutoff-float(x['completion'])) for x in eligible)
            metrics.append(common|dict(metric='C_'+str(cutoff)+'s',value=end,complete=True,models=len(eligible),auc=auc))
        for k in [1,2,5,10,50,100,1000]:
            complete=len(rs)>=k;v=int(rs[k-1]['covered']) if complete else None;t=float(rs[k-1]['completion']) if complete else None
            metrics.append(common|dict(metric='C_n'+str(k),value=v,complete=complete,models=k if complete else len(rs),auc=None));prefixes.append(common|dict(prefix=k,complete=complete,time_to_prefix=t,available_models=len(rs)))
        for name,lo,hi in [('0',0,0),('1-2',1,2),('3-9',3,9),('10-99',10,99),('100+',100,10**15)]:
            chosen=[int(x['gain']) for x in rs if lo<=int(x['gain'])<=hi];bins.append(common|dict(gain_bin=name,events=len(chosen),covered_contribution=sum(chosen)))
    table(out/'metrics.csv',metrics);table(out/'prefix_completion.csv',prefixes);table(out/'gain_bins.csv',bins)
    if plan['stage']=='probe':
        pools=read(out/'probe_pools.csv');cost=read(out/'mechanism_costs.csv');by=[]
        for f in [x['name'] for x in plan['inputs']]:
            ps=[r for r in pools if r['formula']==f];cs=[r for r in cost if r['formula']==f]
            row=dict(formula=f,hook_queries=sum(int(r.get('hook_queries') or 0) for r in cs),verified_complete_pools=len(ps),nontrivial_pools=sum(int(r['pool_size'])>1 for r in ps),strictly_better_noop=sum(r['strictly_better_than_noop']=='True' for r in ps),covered_above_random=sum(r['covered_above_random']=='True' for r in ps),arith_seconds=sum(float(r['arith_seconds']) for r in cs),phase_seconds=sum(float(r['phase_seconds']) for r in cs));by.append(row)
        main=by[:2];qualified=[r for r in by if r['verified_complete_pools']>=10 and r['strictly_better_noop']>0]
        passed=all(r['hook_queries']>0 for r in main) and len(qualified)>=2 and any(r in qualified and r['covered_above_random']>=2 for r in main)
        table(out/'mechanism_support.csv',by);dump(out/'gate.json',dict(passed=passed,rule='two main hooks; >=2 formulas with >=10 verified complete pools and better-than-noop; >=1 main among them with covered>random expectation on >=2 states; no errors',formulas=by,screen_authorized_by_gate=passed,automatic_launch=False,interpretation='only same saved pool; no end-to-end performance claim'))
    else:
        index={(r['formula'],r['seed'],r['configuration'],r['metric']):r for r in metrics};diff=[]
        for f in [x['name'] for x in plan['inputs']]:
            for seed in plan['seeds']:
                for new,base in [('arith_coverage','phase_only'),('arith_coverage','arith_random'),('arith_random','phase_only')]:
                    for metric in sorted({r['metric'] for r in metrics}):
                        n=index[f,seed,new,metric];b=index[f,seed,base,metric];ok=n['complete'] and b['complete'];delta=n['value']-b['value'] if ok else None
                        diff.append(dict(formula=f,seed=seed,comparison=new+'-'+base,metric=metric,new_complete=n['complete'],base_complete=b['complete'],complete=ok,new_value=n['value'],base_value=b['value'],delta=delta,percentage_points=100*delta/n['total'] if ok else None))
        table(out/'paired_differences.csv',diff);groups=defaultdict(list)
        for r in diff:groups[r['formula'],r['comparison'],r['metric']].append(r)
        table(out/'paired_summary.csv',[dict(formula=k[0],comparison=k[1],metric=k[2],incomplete=sum(not r['complete'] for r in rs),**summaries([r['delta'] for r in rs if r['complete']])) for k,rs in groups.items()])
        if plan['stage']=='screen':
            formula_names=[r['name'] for r in plan['inputs']];gate_rows=[];comparisons=[];common_rows=[]
            for formula in formula_names:
                common_seeds=[seed for seed in plan['seeds'] if all(index[formula,seed,c,'C_n100']['complete'] for c in plan['configurations'])]
                common_rows.append(dict(formula=formula,common_n100_seeds=json.dumps(common_seeds),common_count=len(common_seeds),eligible_for_common_comparison=len(common_seeds)>=6))
            for base in ['phase_only','arith_random']:
                full=[];small=[];positive=0;positive100=0;no_completion_loss=True;no_severe=True
                for f in formula_names:
                    rs=[r for r in diff if r['formula']==f and r['comparison']=='arith_coverage-'+base and r['metric']=='C_120s'];end=summaries([r['delta'] for r in rs]);norm=statistics.mean(r['percentage_points'] for r in rs);full.append(norm);positive+=end['median']>0
                    common=json.loads(next(r['common_n100_seeds'] for r in common_rows if r['formula']==f));n100=[r for r in diff if r['formula']==f and r['seed'] in common and r['comparison']=='arith_coverage-'+base and r['metric']=='C_n100'];quality=summaries([r['delta'] for r in n100])
                    if len(common)>=6:small.append(statistics.mean(r['percentage_points'] for r in n100));positive100+=quality['median']>0
                    new_done=sum(index[f,seed,'arith_coverage','C_n100']['complete'] for seed in plan['seeds']);base_done=sum(index[f,seed,base,'C_n100']['complete'] for seed in plan['seeds']);no_completion_loss &= new_done>=base_done
                    severe=[]
                    for metric in ['C_n10','C_120s']:
                        ns=[index[f,seed,'arith_coverage',metric]['value'] for seed in plan['seeds'] if index[f,seed,'arith_coverage',metric]['complete']];bs=[index[f,seed,base,metric]['value'] for seed in plan['seeds'] if index[f,seed,base,metric]['complete']]
                        if len(ns)!=7 or len(bs)!=7:severe.append(dict(metric=metric,assessed=False));no_severe=False;continue
                        nmed,bmed=statistics.median(ns),statistics.median(bs);bad=bmed>0 and nmed<.95*bmed;no_severe &= not bad;severe.append(dict(metric=metric,assessed=True,new_median=nmed,base_median=bmed,relative=(nmed-bmed)/bmed if bmed else None,severe=bad))
                    gate_rows.append(dict(formula=f,comparison='arith_coverage-'+base,endpoint_mean_pp=norm,endpoint_median_bits=end['median'],common100_n=len(common),common100_median_bits=quality['median'],new100_completion=new_done,base100_completion=base_done,early_endpoint_checks=json.dumps(severe)))
                passed=statistics.mean(full)>0 and positive>=3 and len(small)>=3 and statistics.mean(small)>0 and positive100>=3 and no_completion_loss and no_severe
                comparisons.append(dict(comparison='arith_coverage-'+base,passed=passed,equal_formula_endpoint_mean_pp=statistics.mean(full),positive_endpoint_formulas=positive,common100_formulas=len(small),common100_mean_pp=statistics.mean(small) if small else None,positive_common100_formulas=positive100,no_prefix_completion_loss=no_completion_loss,no_severe_or_unknown_regression=no_severe))
            gate=json.loads((out.parent/'probe/gate.json').read_text());costs=read(out/'mechanism_costs.csv');committed=sum(int(r.get('committed') or 0) for r in costs if r['configuration']=='arith_coverage')
            table(out/'common_prefix100.csv',common_rows);table(out/'screen_gate_details.csv',gate_rows);dump(out/'screen_gate.json',dict(passed=all(r['passed'] for r in comparisons) and gate['passed'] and committed>0,comparisons=comparisons,coverage_commits=committed,automatic_next_experiment=False))
    print(json.dumps(dict(stage=plan['stage'],complete=True,search_started=False)))
if __name__=='__main__':main()
