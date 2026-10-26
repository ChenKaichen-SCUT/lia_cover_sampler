#!/usr/bin/env python3
"""Independent full-state coverage replay, then position/cost/resource audit."""
import argparse,csv,json,time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from audit_native_delivery_results import one_run
from audit_position_frozen_900s import resource_fields
from reevaluate_round6_saved_candidates import dump,write_csv,require,file_hash,model_id
from run_position_fixed4 import CONFIGS
POLICIES=dict(zip(CONFIGS,['first','random_supported_rank','fixed_fourth']))
def read(path):return [json.loads(l) for l in path.read_text().splitlines()]
def diagnostics(out,name,allow_test_limit=False):
    d=out/'runs'/name;raw=d/'raw';s=json.loads((d/'spec.json').read_text());initial=json.loads((raw/'initial.json').read_text());summary=json.loads((raw/'summary.json').read_text())
    common={k:s[k] for k in ['formula','seed','configuration','group']};common['run']=name
    require(initial['position_policy']==POLICIES[s['configuration']] and initial['mechanism_quota_fraction']==.05,'position/quota metadata')
    require(initial['solver_kind']=='simple_incremental' and initial['solver_random_seed']==s['seed'],'solver initialization')
    require(initial['action_sampling']=='after_target_and_prechoice_budget_checks','action semantics')
    require(allow_test_limit or initial['test_query_limit']==2**32-1,'test cap leaked')
    qs=read(raw/'queries.jsonl');ps=read(raw/'positions.jsonl');ds=read(raw/'diagnostics.jsonl');require(len(qs)==len(ps)==len(ds),'diagnostic rows missing')
    rows=[];previous=0;last_stats={};counts=Counter();first_stop=None
    for q,p,x in zip(qs,ps,ds):
        require(q['query']==p['query']==x['query'],'diagnostic query association');m=x['mechanism'];pos=p['position'];sel=p['decision']['selected'];arrived=pos.get('target_arrived',False)
        require(not sel or arrived,'selected before target');require(not arrived or pos['eligible'],'target without eligibility')
        require(x['mechanism_before_seconds']>=previous-1e-12,'mechanism cost decreased between queries')
        if m:
            require(m['policy']==POLICIES[s['configuration']] and m['quota_seconds']==.05*s['seconds'],'mechanism configuration')
            require(m['charged_seconds']>=x['mechanism_before_seconds'] and m['feature_seconds']+m['prediction_seconds']<=m['charged_seconds']+1e-9,'mechanism costs inconsistent')
            require(not m['action_drawn'] or arrived,'action drawn without target')
            if m['stop_reason']:
                require(0<=m['stop_at_seconds']<=x['record_seconds'] and m['stop_cost_seconds']<=m['charged_seconds'],'missing stop timing/cost')
                evidence=(m['stop_reason'],m['stop_query'],m['stop_at_seconds'],m['stop_cost_seconds'])
                require(first_stop is None or first_stop==evidence,'stop evidence changed');first_stop=evidence
                if m['stop_reason']=='mechanism_cost_quota':require(m['stop_cost_seconds']>=m['quota_seconds'],'quota stop premature')
                if m['stop_at_seconds']<=x['cycle_start']:require(not pos['eligible'] and not sel,'selection after permanent stop')
            previous=m['charged_seconds']
        counts['queries']+=1;counts['eligible']+=pos['eligible'];counts['enabled_checks']+=bool(p['hook']);counts['target_arrived']+=arrived;counts['selected']+=sel;counts['flip']+=p['decision'].get('action')=='FlipOnce';counts['not_reached']+=pos['eligible'] and not arrived;counts['reached_no_selection']+=arrived and not sel;counts['disabled']+=not pos['eligible'];counts['scan_attempts']+=len(p['attempts'])
        counts['reason_'+pos['reason']]+=1
        row=common|dict(query=q['query'],coverage_version_before=q['generation_version_before'],eligible=pos['eligible'],hook_installed_for_check=bool(p['hook']),target=pos['target'],target_arrived=arrived,selected=sel,action=p['decision'].get('action'),action_drawn=m['action_drawn'] if m else False,action_draw_count=m['action_draw_count'] if m else 0,reason=pos['reason'],scan_attempts=len(p['attempts']),native_decisions_examined=p['hook'].get('decisions_examined',0),distinct_supported=pos.get('distinct_supported',0),raw_status=q['evidence']['raw_status'],grant_ms=q['evidence']['granted_timeout_ms'],check_seconds=q['evidence']['actual_check_seconds'],mechanism_before=x['mechanism_before_seconds'],mechanism_record=m['charged_seconds'] if m else 0,stop_reason=m['stop_reason'] if m else '',stop_at=m['stop_at_seconds'] if m else None,stop_query=m['stop_query'] if m else None,statistics_capture_seconds=x['statistics_capture_seconds'])
        for key in ['conflicts','decisions','propagations','restarts','rlimit count','num checks','arith conflicts']:
            value=x['solver_statistics_raw'].get(key);old=last_stats.get(key);row['raw_'+key]=value;row['monotone_difference_'+key]=value-old if value is not None and old is not None and value>=old else None
            row['counter_reset_'+key]=value is not None and old is not None and value<old
            if value is not None:last_stats[key]=value
        rows.append(row)
    m=summary['mechanism_diagnostics'];require(m is None or m['charged_seconds']>=previous,'final cost missing')
    first=read(raw/'deliveries.jsonl');before=[q for q in qs if q['generation_version_before']==0]
    init=common|{k:initial[k] for k in ['source_sha256','z3_version','total_bits','formula_nodes','original_variables','solver_kind','solver_random_seed','action_rng_seed','position_rng_seed','formula_load_seconds','solver_initialization_seconds','initialization_record_seconds','position_policy']}
    init.update(first_model_id=model_id(first[0]['model']) if first else None,first_query=first[0]['query'] if first else None,first_sat_seconds=summary['first_sat_seconds'],prefirst_status_grants=json.dumps([(q['evidence']['raw_status'],q['evidence']['granted_timeout_ms']) for q in before]),prefirst_queries=len(before),prefirst_selected=sum(q['decision']['selected'] for q in before))
    require(init['prefirst_selected']==0,'intervention before first witness')
    session=common|dict(counts)|dict(mechanism_seconds=summary['mechanism_seconds'],mechanism_stop=summary['mechanism_stop'],quota_seconds=.05*s['seconds'],stop_at_seconds=m['stop_at_seconds'] if m else None,stop_query=m['stop_query'] if m else None,stop_cost_seconds=m['stop_cost_seconds'] if m else None,feature_seconds=m['feature_seconds'] if m else 0,prediction_seconds=m['prediction_seconds'] if m else 0,statistics_capture_seconds=summary['stats']['seconds'].get('query_statistics_capture',0),diagnostic_journal_seconds=summary['stats']['seconds'].get('diagnostic_journal',0),all_stats_times=json.dumps(summary['stats']['seconds'],sort_keys=True))
    for key,denominator in [('arrival_rate_all','queries'),('selection_rate_all','queries'),('flip_rate_all','queries'),('arrival_rate_eligible','eligible')]:
        num='selected' if key.startswith('selection') else 'flip' if key.startswith('flip') else 'target_arrived';session[key]=counts[num]/counts[denominator] if counts[denominator] else None
    return rows,init,session

def resources(out,plan):
    rows=[];groups={};intervals={};identity={r['cpu']:(r['package'],r['physical_core']) for r in plan['cpu_topology']};jobs={(r['group'],r['configuration']):r for r in plan['jobs']}
    for d in sorted((out/'runs').iterdir()):
        s=json.loads((d/'spec.json').read_text());p=json.loads((d/'process_result.json').read_text());g=json.loads((d/'gate.json').read_text());ready=json.loads((d/'ready.json').read_text());j=jobs[s['group'],s['configuration']]
        require(all(s[k]==j[k] for k in ['group','batch','formula','seed','configuration','seconds','core']),'task matrix');require(ready['affinity']==[s['core']] and s['core']!=plan['supervisor_cpu'],'affinity')
        r=resource_fields((d/'resource.txt').read_text());require(r['exit_status']==p['returncode']==0 and not p['hard_kill'] and not p['resource_stop'],'failed process retained; cannot treat as completed')
        require(g['start_monotonic_ns']==p['start_monotonic_ns'],'gate chronology');n=maximum=0
        for sample in read(d/'resource_samples.jsonl'):
            for proc in sample['processes']:
                if not (proc.get('Name') or '').startswith('native_deliver'):continue
                maximum=max(maximum,int(proc['Threads']));ts=proc['thread_affinity']
                if ts:require(all(t['affinity']==str(s['core']) for t in ts),'thread escaped assigned CPU');n+=1
        require(n>0 and maximum<=2,'main plus timeout helper evidence missing')
        row={k:s[k] for k in ['group','batch','formula','seed','configuration','core']};row.update(run=d.name,physical_core=str(identity[s['core']]),start_monotonic_ns=p['start_monotonic_ns'],exec_offset_seconds=(g['exec_monotonic_ns']-g['start_monotonic_ns'])/1e9,process_wall_seconds=p['process_wall_seconds'],maximum_os_threads=maximum,thread_samples=n,**r);rows.append(row);groups.setdefault(s['group'],[]).append(row)
        start=p['start_monotonic_ns']/1e9;intervals.setdefault(s['batch'],[]).append((start,start+p['process_wall_seconds']))
    timing=[]
    for group,rs in groups.items():
        require(len(rs)==3 and {r['configuration'] for r in rs}==set(CONFIGS),'incomplete triplet');require(len({r['start_monotonic_ns'] for r in rs})==1 and len({r['physical_core'] for r in rs})==3,'triplet timing/core separation')
        timing.append(dict(group=group,formula=rs[0]['formula'],seed=rs[0]['seed'],exec_skew_seconds=max(r['exec_offset_seconds'] for r in rs)-min(r['exec_offset_seconds'] for r in rs)))
    end=None
    for batch,spans in sorted(intervals.items()):
        require(len(spans)==plan['batch_sizes'][batch] and len({s for s,e in spans})==1 and (end is None or spans[0][0]>=end),'batch release/barrier');end=max(e for s,e in spans)
    require(len(rows)==42 and len(timing)==14,'missing sessions');write_csv(out/'resource_audit.csv',rows);write_csv(out/'triplet_timing_audit.csv',timing)
    return dict(passed=True,maximum_parallel_sessions=15,batches=[15,15,12],max_exec_skew_seconds=max(r['exec_skew_seconds'] for r in timing),main_and_timeout_helper_share_single_cpu=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('out',type=Path);p.add_argument('--jobs',type=int,default=15);a=p.parse_args();out=a.out.resolve();require(1<=a.jobs<=15,'workers');require(json.loads((out/'sampling_complete.json').read_text())['passed'],'audit only after sampling')
    plan=json.loads((out/'plan.json').read_text());res=resources(out,plan);names=[d.name for d in sorted((out/'runs').iterdir()) if d.is_dir()];start=time.perf_counter()
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:runs=list(pool.map(one_run,[(str(out),n) for n in names]))
    queries=[];init=[];sessions=[]
    for name in names:
        q,i,s=diagnostics(out,name);queries+=q;init.append(i);sessions.append(s)
        with (out/'audit'/name/'delivery.csv').open() as f:ds=list(csv.DictReader(f))
        require([int(r['generation_index']) for r in ds]==list(range(1,len(ds)+1)),'immediate delivery order')
    # Union fields include explicitly absent stop-reason counters from other runs.
    keys=sorted(set().union(*(r.keys() for r in sessions)));sessions=[{k:r.get(k,0 if k.startswith('reason_') else None) for k in keys} for r in sessions]
    write_csv(out/'query_diagnostics.csv',queries);write_csv(out/'initialization.csv',init);write_csv(out/'intervention_costs.csv',sessions)
    paired=[]
    for group in sorted({r['group'] for r in init}):
        rs=[r for r in init if r['group']==group];require(len(rs)==3,'initialization triplet')
        for k in ['source_sha256','z3_version','total_bits','formula_nodes','original_variables','solver_kind','solver_random_seed','action_rng_seed','position_rng_seed']:require(len({r[k] for r in rs})==1,'initialization parameter differs: '+k)
        paired.append(dict(group=group,formula=rs[0]['formula'],seed=rs[0]['seed'],parameters_equal=True,first_models_equal=len({r['first_model_id'] for r in rs})==1,prefirst_status_grants_equal=len({r['prefirst_status_grants'] for r in rs})==1,first_models=json.dumps({r['configuration']:r['first_model_id'] for r in rs})))
    write_csv(out/'initialization_pairs.csv',paired);dump(out/'audit_summary.json',dict(passed=True,runs=runs,resources=res,search_calls=0,elapsed=time.perf_counter()-start,workers=a.jobs,plan_sha256=file_hash(out/'plan.json')));print(json.dumps(dict(passed=True,runs=len(runs),search_calls=0)))
if __name__=='__main__':main()
