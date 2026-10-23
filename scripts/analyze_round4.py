#!/usr/bin/env python3
"""Predeclared two-configuration screening; no automatic 300/900-second promotion."""
import argparse,json,csv
from pathlib import Path
from collections import defaultdict,Counter
from statistics import mean,median
from bisect import bisect_right
from run_iteration import ROOT,digest,write
from analyze_iteration import csvfile
TIMES=(5,10,30,60,120)
PREFIXES=(2,5,10,50,100,1000)

def analyze(root,diagnosis,replay):
    plan=json.loads((root/'plan.json').read_text());done=json.loads((root/'COMPLETE.json').read_text())
    budget_seconds=plan['seconds']
    audit=json.loads((root/'AUDIT_COMPLETE.json').read_text());state_audit=json.loads((root/'STATE_AUDIT_COMPLETE.json').read_text())
    assert not done['cancelled'] and not done['errors'] and audit['all_passed'] and state_audit['all_valid']
    assert digest(root/'plan.json')==done['plan_sha256'] and digest(root/'queue.jsonl')==done['queue_sha256']
    out=root/'analysis';out.mkdir(exist_ok=True);points=[];endpoints=[];mechanisms=[]
    for name in ('ab_reference','ab_polish'):
        for r in json.loads((root/name/'audit_summary.json').read_text()):
            detail=json.loads((root/name/'audit'/r['instance']/f'seed-{r["seed"]}.json').read_text());curve=detail['curve']
            prior=0;stamp=0
            for i,p in enumerate(curve,1):assert p['k']==i and prior<=p['covered']<=p['total'] and stamp<=p['seconds']<=budget_seconds;prior=p['covered'];stamp=p['seconds']
            base=dict(configuration=name,instance=r['instance'],seed=r['seed'])
            endpoints.append(dict(**base,samples=len(curve),percent=r['percent'],reached_1000=len(curve)>=1000))
            for t in sorted(set([t for t in TIMES if t<=budget_seconds]+[budget_seconds])):
                i=bisect_right([p['seconds'] for p in curve],t)
                points.append(dict(**base,metric='time',budget=t,complete=True,percent=100*curve[i-1]['covered']/r['total'] if i else 0))
            for k in PREFIXES:points.append(dict(**base,metric='prefix',budget=k,complete=len(curve)>=k,percent=100*curve[k-1]['covered']/r['total'] if len(curve)>=k else None))
            mechanisms.append(dict(**base,partial=r.get('statistics_partial',False),metadata=r.get('sampler',{})))
    lookup={(r['configuration'],r['instance'],r['seed'],r['metric'],r['budget']):r for r in points};pairs=[]
    for r in points:
        if r['configuration']!='ab_polish':continue
        a=lookup['ab_reference',r['instance'],r['seed'],r['metric'],r['budget']];paired=a['complete'] and r['complete']
        pairs.append(dict(instance=r['instance'],seed=r['seed'],metric=r['metric'],budget=r['budget'],paired=paired,delta_pp=r['percent']-a['percent'] if paired else None))
    summary=[]
    for kind,budget in sorted({(r['metric'],r['budget']) for r in pairs}):
        values=[r['delta_pp'] for r in pairs if r['metric']==kind and r['budget']==budget and r['paired']]
        summary.append(dict(metric=kind,budget=budget,paired=len(values),mean_delta_pp=mean(values) if values else None,median_delta_pp=median(values) if values else None,
            wins=sum(v>1e-10 for v in values),ties=sum(abs(v)<=1e-10 for v in values),losses=sum(v< -1e-10 for v in values)))
    endpoint=[r for r in pairs if r['metric']=='time' and r['budget']==budget_seconds];by_case=defaultdict(list)
    for r in endpoint:by_case[r['instance']].append(r['delta_pp'])
    diag=json.loads((diagnosis/'COMPLETE.json').read_text());fixed=json.loads((replay/'COMPLETE.json').read_text())
    counts=Counter()
    for r in mechanisms:
        if r['configuration']=='ab_polish':counts.update(r['metadata'].get('output_statistics',{}).get('counts',{}))
    checks=dict(endpoint_mean_nonnegative=mean(r['delta_pp'] for r in endpoint)>=0,
        endpoint_median_nonnegative=median(r['delta_pp'] for r in endpoint)>=-1e-10,
        positive_on_two_development_cases=sum(mean(v)>1e-10 for v in by_case.values())>=2,
        two_thirds_seed_pairs_nonnegative=sum(r['delta_pp']>=-1e-10 for r in endpoint)>=len(endpoint)*2/3,
        formal_screen_protocol=budget_seconds==120 and len(endpoint)==9 and set(plan['seeds'])=={0,1,2},
        mechanism_audits_passed=diag['all_returned_models_audited'] and fixed['all_valid'] and fixed['all_preserved'],
        output_state_isolation_and_preservation=state_audit['all_processed_prefixes_preserved'],
        replacements_actually_accepted=counts['replacement_accepted']>0,
        observed_extra_cost_within_allowance=all(r['completed_cost_within_allowance'] for r in state_audit['rows'] if r['configuration']=='ab_polish'),
        all_output_transactions_accounted=all(r['pending_raw_models']==0 and r['incomplete_raw_lines']==0 and r['incomplete_transaction_lines']==0 for r in state_audit['rows']))
    for k in (10,50):
        values=[r['delta_pp'] for r in pairs if r['metric']=='prefix' and r['budget']==k and r['paired']]
        checks[f'prefix_{k}_mean_nonnegative']=bool(values) and mean(values)>=0
    resources=json.loads((root/'resources.json').read_text())
    result=dict(budget_seconds=budget_seconds,configurations=[dict(configuration=name,runs=sum(r['configuration']==name for r in endpoints),mean_percent=mean(r['percent'] for r in endpoints if r['configuration']==name),samples=sum(r['samples'] for r in endpoints if r['configuration']==name)) for name in ('ab_reference','ab_polish')],
        paired=summary,per_case_mean_delta={c:mean(v) for c,v in by_case.items()},checks=checks,promising=all(checks.values()),
        sampling_cpu_seconds=resources['cpu_seconds'],sampling_wall_seconds=resources['wall_seconds'],
        formal_audit_cpu_seconds=audit['cpu_seconds'],formal_audit_wall_seconds=audit['wall_seconds'],
        state_audit_cpu_seconds=state_audit['cpu_seconds'],state_audit_wall_seconds=state_audit['wall_seconds'])
    csvfile(out/'endpoints.csv',endpoints);csvfile(out/'curves.csv',points);csvfile(out/'paired_by_case_seed.csv',pairs);csvfile(out/'paired_summary.csv',summary)
    write(out/'analysis.json',result);write(out/'mechanisms.json',mechanisms)
    write(root/'promotion.json',dict(approved=all(checks.values()),checks=checks,automatic_long_runs=False,
        decision='Eligible for separately selected follow-up' if all(checks.values()) else 'Stop: failed predeclared screen gates',
        analysis_sha256=digest(out/'analysis.json'),configurations=plan['configs']))
    print(json.dumps(result['configurations']))
def main():
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--diagnosis',type=Path,required=True);p.add_argument('--replay',type=Path,required=True)
    a=p.parse_args();analyze(a.root.resolve(),a.diagnosis.resolve(),a.replay.resolve())
if __name__=='__main__':main()
