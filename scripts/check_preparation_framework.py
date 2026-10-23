#!/usr/bin/env python3
"""Pure-data regression for paired aggregation and immutable audit bindings; no sampler."""
import sys,tempfile,json,pathlib,hashlib
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
from report_preparation_pairs import flatten,pair_and_cluster,aggregate,report
from run_preparation_pairs import CAPS,RULE
snapshots=[dict(id=f'case-{i//4}/prefix-{[1,5,10,50][i%4]}',instance=f'case-{i//4}',prefix=[1,5,10,50][i%4]) for i in range(12)]
rows=[];audits=[]
for i,s in enumerate(snapshots):
 for seed in range(3):
  for repeat in range(3):
   for mode in ('old','reuse'):
    gain=repeat*30 if mode=='reuse' else 0
    data=dict(mode=mode,budget_seconds=.06,full_seconds=.04,setup_seconds=.01,formula_initialization_seconds=.005,snapshot_restore_seconds=.004,search_initialization_seconds=.001,
      final_validation_seconds=.001,preservation_seconds=.001,completed_in_budget=True,setup_exhausted=False,
      input_gain=100,proposed_gain=100+gain,accepted_extra_coverage=gain,late_extra_coverage=0,extra_per_full_second=gain/.04,
      total=1000,prefix_covered=300,valid_candidates=int(gain>0),timely_valid_candidates=int(gain>0),first_valid_seconds=.03 if gain else None,
      statistics=dict(counts=dict(polish_total_attempt_limit=1536,preparation_region_builds=1,polish_attempt_limited_batches=1),seconds=dict(preparation_region_seconds=.001)))
    path=s['id']+f'/seed-{seed}/repeat-{repeat}/{mode}.json'
    rows.append(dict(snapshot=s['id'],instance=s['instance'],prefix=s['prefix'],seed=seed,repeat=repeat,mode=mode,pair_index=(i*3+seed)*3+repeat,order_position=0 if mode=='old' else 1,
      budget_seconds=.06,process_wall_seconds=.05,child_cpu_seconds=.04,exitcode=0,watchdog_timeout=False,success=True,output=path,probe=data))
    audits.append(dict(output=path,passed=True,actual_candidate_gain=100+gain))
plan=dict(snapshots=snapshots,seeds=[0,1,2],repeats=3,budget_seconds=.06,jobs=15,binary='/frozen/probe',attempt_caps=CAPS,stability_rule=RULE)
calls=[flatten(r,a) for r,a in zip(rows,audits)];pairs,clusters=pair_and_cluster(calls,3);a=aggregate(calls,clusters,plan)
assert len(pairs)==108 and len(clusters)==36 and a['stable_primary_gain'] and a['mean_cluster_delta_bits']==30
assert a['modes'][0]['no_candidate_calls']==108 and a['modes'][0]['mean_accepted_extra_coverage']==0
assert all(c['attempt_limited_batches']==1 and c['valid_candidates'] in (0,1) for c in calls)
limited=[dict(c) for c in calls]
for c in limited:
 if c['snapshot']!=snapshots[0]['id']:
  c['accepted_extra_coverage']=0;c['extra_per_full_second']=0;c['extra_per_process_wall_second']=0
_,lc=pair_and_cluster(limited,3);assert not aggregate(limited,lc,plan)['stable_primary_gain']
failed=[dict(c) for c in calls];failed[0]['audit_passed']=False
fp,fc=pair_and_cluster(failed,3);assert len(fp)==108 and len(fc)==36 and sum(x['complete_and_audited'] for x in fc)==35
assert not aggregate(failed,fc,plan)['stable_primary_gain']
with tempfile.TemporaryDirectory(prefix='preparation-report-test-') as d:
 out=pathlib.Path(d)
 def w(name,obj):(out/name).write_text(json.dumps(obj)+'\n')
 def h(name):return hashlib.sha256((out/name).read_bytes()).hexdigest()
 w('plan.json',plan);(out/'runs.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
 w('TIMED_COMPLETE.json',dict(finished_utc='2026-10-23T00:00:00+00:00',wall_seconds=1,child_cpu_seconds=2))
 (out/'audit').mkdir();w('audit/audit.json',dict(calls=audits))
 w('AUDIT_COMPLETE.json',dict(started_utc='2026-10-23T00:00:01+00:00',wall_seconds=1,cpu_seconds=2,all_passed=True,audit_files_sha256={'audit/audit.json':h('audit/audit.json')}))
 w('COMPLETE.json',dict(plan_sha256=h('plan.json'),runs_sha256=h('runs.jsonl'),timed_complete_sha256=h('TIMED_COMPLETE.json'),audit_complete_sha256=h('AUDIT_COMPLETE.json'),all_passed=True,unchanged_sources=True))
 result=report(out);assert result['clusters']==36 and (out/'calls.csv').exists()
 text=(out/'report.md').read_text();assert '15 对并行' in text and '36 个快照' in text
 # Review-generated tables cannot silently consume a changed audit result.
 with (out/'audit/audit.json').open('a') as f:f.write(' ')
 try:report(out)
 except AssertionError:pass
 else:raise AssertionError('changed audit evidence was accepted')
print('PASS: 216 calls -> 108 pairs -> 36 clusters; no-candidate retained; failed calls retained; one-snapshot gate; attempt-limited not candidate; Chinese report; changed-audit rejected; no probe invoked')
