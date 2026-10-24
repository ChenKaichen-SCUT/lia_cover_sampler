#!/usr/bin/env python3
"""Saved model audit only; no search, no after-the-fact target selection."""
import argparse,json
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from audit_macro_pairs import audit_job,lines
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,require,file_hash,dump,write_csv

def audit(args):
 base,r,source=args;d=Path(base)/r['output']
 for p,sha in r['hashes'].items():require(file_hash(d/p)==sha,'raw hash')
 require(r['exitcode']==0 and not r['watchdog'],'failed process retained: audit cannot pass')
 if r['stage']=='A':
  s=json.loads((d/'summary.json').read_text());m=json.loads((d/'model.json').read_text());c=json.loads((d/'catalog.json').read_text());alt=c['groups'][r['group']]['alternatives'][r['alternative']]
  require(s['source_sha256']==source,'source');require(s['group_node']==r['group_node'] and s['alternative_node']==r['alternative_node'],'frozen AST identity')
  require(s['has_model']==(m is not None),'model presence');require(s['evidence']['requested_timeout_ms']==r['timeout_ms'],'requested budget')
  if m is not None:
   with no_search():
    metric=FrozenCoverage(Path(r['formula']).read_text());metric.apply(metric.empty.copy(),m)
   values={x['name']:int(x['value']) for x in m};require(all(values[x['name']]==int(x['value']) for x in alt['assignments']),'whole alternative')
  return dict(id=r['id'],status='passed',summary=s,validated_models=int(m is not None))
 result=audit_job(args)
 for row in lines(d/'rounds.jsonl'):
  require(row['broad_calls']<=1,'R9 one broad call/action');require(row['broad_calls']==len(row['broad_queries']),'query log completeness')
  for q in row['broad_queries']:
   e=q['evidence'];require(e['granted_timeout_ms']<=e['requested_timeout_ms']<=2000,'query cap')
   if e['raw_status']!='unsat':require(not q.get('global_single_alternative_proof',False),'non-proof cached')
   if q.get('choices'):
    require(len(q['choices'])==1,'no auto combo')
    before=q['attempt_before'];after=q['attempt_after']
    require(after['attempts']==before['attempts']+int(e['check_started']),'actual attempt ledger')
    require(after['attempts']<=6 and after['retry_level']<=3,'bounded per alternative')
   if e['control_classification']=='suspected_local_timeout':
    require(e['raw_status']=='unknown' and e['raw_reason'] in ('canceled','cancelled') and e['inferred'],'preserve uncertainty')
    require(e['actual_check_seconds']*1000>=.8*e['granted_timeout_ms'] and not e['external_interrupt'] and not e['session_deadline_reached'],'local timing evidence')
   if e['budget_clip_reason']!='none':require(e['retry_level_after']==e['retry_level_before'],'clipped attempt consumed tier')
 return result

def main():
 p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('--jobs',type=int,default=6);a=p.parse_args();plan=json.loads((a.input/'plan.json').read_text());records=lines(a.input/'runs.jsonl');complete=json.loads((a.input/'COMPLETE.json').read_text())
 require(file_hash(a.input/'plan.json')==complete['plan_sha256'] and file_hash(a.input/'runs.jsonl')==complete['journal_sha256'],'batch binding');require(len(records)==len(plan['tasks']),'missing tasks')
 for path,sha in plan['hashes'].items():require(file_hash(path)==sha,'source changed: '+path)
 dest=a.input/'audit';dest.mkdir(exist_ok=False);rows=[]
 with ProcessPoolExecutor(max_workers=a.jobs) as pool:
  for r in pool.map(audit,[(str(a.input),r,plan['source_sha256']) for r in records]):dump(dest/(r['id'].replace('/','__')+'.json'),r);rows.append(dict(id=r['id'],status=r['status'],validated_models=r['validated_models']))
 write_csv(a.input/'audit_summary.csv',rows);dump(a.input/'AUDIT.json',dict(sessions=len(rows),all_passed=True,validated_models=sum(r['validated_models'] for r in rows),search_calls=0))
if __name__=='__main__':main()
