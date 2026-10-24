#!/usr/bin/env python3
"""Frozen-state component audit; original Manual only, no new search."""
import argparse,json
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from audit_projection_fix import lines,observed,values
from audit_projection_fix_inputs import typed_tokens
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,model_key,model_id,require,file_hash,dump,write_csv

def audit_job(args):
 base,r,source=args;d=Path(base)/r['output'];initial=json.loads((d/'initial.json').read_text());summary=json.loads((d/'summary.json').read_text());qs=lines(d/'queries.jsonl');phase_costs=lines(d/'phase_costs.jsonl');cs=lines(d/'candidates.jsonl');cb={c['action']:c for c in cs};seen_projection=set();assessed=[]
 require(len(phase_costs)==len(qs),'complete phase cost receipts')
 require(abs(sum(c['seconds'] for c in phase_costs)-summary['stage_seconds'])<1e-8,'full phase cost sum')
 require(r['exitcode']==0 and not r['watchdog'],'process exit')
 for f,h in r['hashes'].items():require(file_hash(d/f)==h,'raw binding')
 require(initial['source_sha256']==summary['source_sha256']==source,'source identity')
 template=initial['seed_model'];require([typed_tokens(s.split(),template) for s in Path(r['prefix_values']).read_text().splitlines()]==initial['prefix'],'input prefix');require([typed_tokens(s.split(),template) for s in Path(r['seed_values']).read_text().splitlines()]==[initial['seed_model']],'input seed')
 with no_search():
  metric=FrozenCoverage(Path(r['formula']).read_text());state=metric.empty.copy();formal=set();history=[];validated=0
  for m in initial['prefix']+[initial['seed_model']]:
   if model_key(m) not in formal:state=metric.apply(state,m);formal.add(model_key(m));history=(history+[m])[-4:];validated+=1
  require(metric.export(state)==initial['coverage']==summary['coverage'],'frozen full state unchanged');supply=set(formal)
  for i,q in enumerate(qs):
   require(phase_costs[i]['action']==i and phase_costs[i]['seconds']>=q['stage_before_log_seconds'],'query log charged')
   require(q['action']==i and len(qs)<=16,'bounded component actions');e=q['evidence'];require(e['granted_timeout_ms']<=e['requested_timeout_ms']<=200,'actual requested timeout');require(q['allowance']<=.25,'phase cap')
   condition=q['condition'];require((i in cb)==(e['raw_status']=='sat'),'every SAT saved')
   if condition:require(condition['blocks_before']==len(seen_projection),'persistent block before')
   if i in cb:
    c=cb[i];key=model_key(c['model']);new=metric.apply(state,c['model']);validated+=1;gain=metric.count(new)-metric.count(state);obs=observed(new)-observed(state)
    require((gain,obs)==(c['gain'],c['observations'])==(q['gain'],q['observations']),'marginal/observation')
    fresh=key not in supply;require(c['new_to_supply']==fresh and c['formal_duplicate']==(key in formal) and c['internal_duplicate']==(key not in formal and not fresh),'exact identity classification');supply.add(key)
    require(c['validated']<=q['checkpoint_completed'],'actual write receipt');require(not q['timely'] or q['checkpoint_completed']<=min(q['started']+q['allowance'],r['seconds']),'timely receipt')
    if condition:
     names=[v['name'] for v in condition['variables']];expected={tuple(values(h)[v] for v in names) for h in history};actual={tuple(map(int,h)) for h in condition['forbidden']};require(expected==actual,'history projections');require(tuple(values(c['model'])[v] for v in names) not in actual,'projection witness');require(key not in seen_projection,'persistent complete no-good');seen_projection.add(key)
    assessed.append(dict(action=i,model_hash=model_id(c['model']),gain=gain,observations=obs,timely=q['timely'],new_to_supply=fresh,formal_duplicate=key in formal,internal_duplicate=not fresh and key not in formal))
   if condition:require(condition['blocks_after']==len(seen_projection)<=256 and condition['block_added']==(i in cb),'persistent blocks after')
  require(summary['blocks']==len(seen_projection),'block total');require(summary['checks']==sum(q['evidence']['check_started'] for q in qs),'actual check count');require(summary['sat']==len(cs),'SAT count');require(summary['timely_distinct']==sum(c['new_to_supply'] and c['timely'] for c in assessed),'timely distinct');require(summary['positive']==sum(c['new_to_supply'] and c['timely'] and c['gain']>0 for c in assessed),'positive count')
 return dict(id=r['id'],instance=r['instance'],mode=r['mode'],seed=r['seed'],status='passed',validated_models=validated,summary=summary,candidates=assessed)
def main():
 p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('--jobs',type=int,default=6);a=p.parse_args();plan=json.loads((a.input/'plan.json').read_text());records=lines(a.input/'runs.jsonl');done=json.loads((a.input/'COMPLETE.json').read_text())
 require(file_hash(a.input/'plan.json')==done['plan_sha256'] and file_hash(a.input/'runs.jsonl')==done['journal_sha256'],'batch binding');require(len(records)==len(plan['tasks']),'complete tasks')
 for f,h in plan['hashes'].items():require(file_hash(f)==h,'source changed '+f)
 out=a.input/'audit';out.mkdir(exist_ok=False);rows=[]
 with ProcessPoolExecutor(max_workers=a.jobs) as pool:
  for r in pool.map(audit_job,[(str(a.input),r,plan['source_sha256']) for r in records]):dump(out/(r['id'].replace('/','__')+'.json'),r);rows.append(dict(id=r['id'],status=r['status'],validated_models=r['validated_models']))
 write_csv(a.input/'audit_summary.csv',rows);dump(a.input/'AUDIT.json',dict(all_passed=True,sessions=len(rows),validated_models=sum(r['validated_models'] for r in rows),search_calls=0))
if __name__=='__main__':main()
