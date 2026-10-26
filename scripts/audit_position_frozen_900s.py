#!/usr/bin/env python3
"""Independent frozen evaluator audit plus timing/resource pairing evidence."""
import argparse,json,re,time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from audit_native_delivery_results import one_run
from reevaluate_round6_saved_candidates import dump,write_csv,require,file_hash

def elapsed_value(value):
    answer=0.
    for part in value.split(':'):answer=60*answer+float(part)
    return answer

def resource_fields(text):
    def value(label):
        return next((l.split(label,1)[1].strip() for l in text.splitlines() if label in l),None)
    return dict(user_seconds=float(value('User time (seconds):')),system_seconds=float(value('System time (seconds):')),wall_seconds=elapsed_value(value('Elapsed (wall clock) time (h:mm:ss or m:ss):')),max_rss_kib=int(value('Maximum resident set size (kbytes):')),exit_status=int(value('Exit status:')))

def audit_resources(out,plan):
    rows=[];bygroup={};batch_intervals={};identity={r['cpu']:(r['package'],r['physical_core']) for r in plan['cpu_topology']}
    jobs={(r['group'],r['configuration']):r for r in plan['jobs']}
    for d in sorted((out/'runs').iterdir()):
        s=json.loads((d/'spec.json').read_text());p=json.loads((d/'process_result.json').read_text());g=json.loads((d/'gate.json').read_text());ready=json.loads((d/'ready.json').read_text());j=jobs[s['group'],s['configuration']]
        require(all(s[k]==j[k] for k in ['group','batch','formula','seed','configuration','seconds','core']),'task matrix changed')
        require(ready['affinity']==[s['core']] and s['core'] not in [plan['supervisor_cpu'],plan['reserved_cpu']],'CPU affinity/reserved core')
        require(g['start_monotonic_ns']==p['start_monotonic_ns'],'start evidence');resource=resource_fields((d/'resource.txt').read_text());require(p['returncode']==resource['exit_status']==0 and not p['resource_stop'] and not p['hard_kill'],'failed or truncated performance run')
        initial=json.loads((d/'raw/initial.json').read_text());require(initial['test_query_limit']==2**32-1,'test query cap leaked');require(initial['mode'] in ['first_immediate','position_immediate'],'batch delivery enabled')
        max_threads=0;sample_count=0
        for l in (d/'resource_samples.jsonl').read_text().splitlines():
            r=json.loads(l);sample_count+=1
            for proc in r['processes']:
                if (proc.get('Name') or '').startswith('native_deliver'):max_threads=max(max_threads,int(proc['Threads']))
        require(max_threads<=1,'multithreaded sampler');offset=(g['exec_monotonic_ns']-g['start_monotonic_ns'])/1e9
        row={k:s[k] for k in ['group','batch','formula','seed','configuration','core']};row.update(run=d.name,physical_core=str(identity[s['core']]),start_monotonic_ns=p['start_monotonic_ns'],exec_offset_seconds=offset,process_wall_seconds=p['process_wall_seconds'],maximum_observed_sampler_threads=max_threads,resource_sample_count=sample_count,**resource);rows.append(row);bygroup.setdefault(s['group'],[]).append(row)
        start=p['start_monotonic_ns']/1e9;end=start+p['process_wall_seconds'];batch_intervals.setdefault(s['batch'],[]).append((start,end))
    paired=[]
    for group,rs in sorted(bygroup.items()):
        require(len(rs)==2 and {r['configuration'] for r in rs}=={'first_immediate','position_immediate'},'incomplete pair')
        require(len({r['start_monotonic_ns'] for r in rs})==1 and len({r['physical_core'] for r in rs})==2,'pair release/core fairness')
        skew=abs(rs[0]['exec_offset_seconds']-rs[1]['exec_offset_seconds']);paired.append(dict(group=group,formula=rs[0]['formula'],seed=rs[0]['seed'],exec_skew_seconds=skew,within_250ms=skew<=.25))
    previous_end=None
    for batch,intervals in sorted(batch_intervals.items()):
        require(len({s for s,e in intervals})==1,'batch not released together')
        start=intervals[0][0];require(previous_end is None or start>=previous_end,'batch barrier broken');previous_end=max(e for s,e in intervals)
        if plan['concurrent_pair_groups']>1 and len(intervals)==2*plan['concurrent_pair_groups']:
            counts=Counter((r['physical_core'],r['configuration']) for r in rows if r['batch']==batch)
            require(all(n==1 for n in counts.values()) and len(counts)==len(intervals),'worker-core symmetric SMT placement')
    require(len(rows)==plan['total_sessions']==28 and len(paired)==14,'missing sessions')
    write_csv(out/'resource_audit.csv',rows);write_csv(out/'pair_timing_audit.csv',paired)
    return dict(passed=True,all_pairs_synchronized=True,max_exec_skew_seconds=max(r['exec_skew_seconds'] for r in paired),all_within_250ms=all(r['within_250ms'] for r in paired),max_observed_sampler_threads=max(r['maximum_observed_sampler_threads'] for r in rows),max_rss_kib=max(r['max_rss_kib'] for r in rows),sampling_physical_cores=len({r['physical_core'] for r in rows}),configured_concurrent_sessions=plan['max_concurrent_sessions'])

def main():
    p=argparse.ArgumentParser();p.add_argument('out',type=Path);p.add_argument('--jobs',type=int,default=15);a=p.parse_args();out=a.out.resolve();require(1<=a.jobs<=15,'audit worker cap')
    require(json.loads((out/'sampling_complete.json').read_text())['passed'],'no audit during sampling')
    plan=json.loads((out/'plan.json').read_text());resource=audit_resources(out,plan);names=[p.name for p in sorted((out/'runs').iterdir()) if p.is_dir()]
    started=time.perf_counter()
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:results=list(pool.map(one_run,[(str(out),name) for name in names]))
    # For immediate delivery, all formal outputs follow the generated sequence;
    # a late tail may be excluded, but cannot be reordered into formal scoring.
    import csv
    for r in results:
        with (out/'audit'/r['run']/'delivery.csv').open() as f:ds=list(csv.DictReader(f))
        require([int(d['generation_index']) for d in ds]==list(range(1,len(ds)+1)),'immediate trajectory reordered or omitted')
    dump(out/'audit_summary.json',dict(passed=True,runs=results,resources=resource,search_calls=0,workers=a.jobs,elapsed=time.perf_counter()-started,source_plan_sha256=file_hash(out/'plan.json')))
    print(json.dumps(dict(passed=True,runs=len(results),resources=resource)))
if __name__=='__main__':main()
