#!/usr/bin/env python3
"""Post-run reporting from saved R9 artifacts; does not alter frozen score or search."""
import csv,json,statistics,hashlib,tarfile
from pathlib import Path
from collections import Counter,defaultdict
ROOT=Path(__file__).resolve().parents[1]
def read(p):return json.loads(p.read_text())
def lines(p):return [json.loads(s) for s in p.read_text().splitlines()]
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def csvout(p,rows):
 with p.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def main():
 a=ROOT/'results/round9_diagnostic_queries/main';b=ROOT/'results/round9_mechanism/main_10s';out=ROOT/'results/round9_report';out.mkdir(exist_ok=False)
 sessions=list(csv.DictReader((b/'sessions.csv').open()));queries=list(csv.DictReader((b/'queries.csv').open()));feasible={(int(r['group']),int(r['alternative'])) for r in csv.DictReader((ROOT/'results/round9_diagnostic/feasibility.csv').open()) if r['label']=='WITNESSED_FEASIBLE'}
 integrity={};contradictions=[];late=Counter();same_level=0;bounds=[]
 for batch in (a,b):
  archive=read(batch/'archive.json');assert digest(batch/'raw.tar.gz')==archive['sha256']
  for p,sha in archive['files'].items():assert digest(batch/p)==sha
  with tarfile.open(batch/'raw.tar.gz') as tar:
   for m in tar:
    if m.isfile():assert hashlib.sha256(tar.extractfile(m).read()).hexdigest()==archive['files'][m.name]
  plan=read(batch/'plan.json');complete=read(batch/'COMPLETE.json');assert digest(batch/'plan.json')==complete['plan_sha256'] and digest(batch/'runs.jsonl')==complete['journal_sha256']
  for path,sha in plan['hashes'].items():assert digest(Path(path))==sha
  recs=lines(batch/'runs.jsonl');assert len(recs)==len(plan['tasks']) and all(r['exitcode']==0 and not r['watchdog'] for r in recs)
  for wave in sorted({r['wave'] for r in recs}):
   group=[r for r in recs if r['wave']==wave];bounds.append(dict(stage=plan['stage'],wave=wave,processes=len(group),first_start_utc=min(r['start_utc'] for r in group),last_start_utc=max(r['start_utc'] for r in group),last_end_utc=max(r['end_utc'] for r in group)))
  integrity[str(batch.relative_to(ROOT))]=dict(runs=len(recs),archive_verified=True,raw_verified=True,frozen_bindings_verified=True)
 for rec in lines(b/'runs.jsonl'):
  d=b/rec['output'];seen=set()
  for row in lines(d/'rounds.jsonl'):
   late['late_internal']+=row['late_candidates'];late['duplicates']+=row['duplicates'];late['session_overrun_rounds']+=row['started']+row['full_seconds']>rec['seconds']
   for q in row['broad_queries']:
    if q.get('choices'):
     key=tuple(q['choices'][0]);e=q['evidence']
     if q['status']=='unsat' and key in feasible:contradictions.append((rec['id'],key))
     level=(key,e['requested_timeout_ms'])
     if e['budget_clip_reason']=='none' and e['raw_status']=='unknown' and level in seen:same_level+=1
     if e['budget_clip_reason']=='none':seen.add(level)
  for r in lines(d/'commits.jsonl'):late['late_formal']+=not r['timely']
 assert not contradictions and not same_level and not late['late_formal']
 costs=[];totals=[]
 for mode in ('plain_fixed','macro_budget_fixed','macro_fair_retry'):
  rs=[r for r in sessions if r['mode']==mode];qs=[q for q in queries if q['mode']==mode]
  costs.append(dict(mode=mode,sessions=len(rs),process_seconds=sum(float(r['process_seconds']) for r in rs),initialization_seconds=sum(float(r['initialization_seconds']) for r in rs),catalog_seconds=sum(float(r['catalog_seconds']) for r in rs),macro_engine_seconds=sum(float(r['macro_engine_seconds']) for r in rs),query_seconds=sum(float(q['query_seconds']) for q in qs),check_seconds=sum(float(q['actual_check_seconds']) for q in qs),constraints_seconds=sum(float(q['constraint_seconds']) for q in qs),completion_seconds=sum(float(q['completion_seconds']) for q in qs),query_validation_seconds=sum(float(q['validation_seconds']) for q in qs),broad_full_seconds=sum(float(r['broad_spent_seconds']) for r in rs),repeat_query_seconds=sum(float(r['repeat_query_seconds']) for r in rs)))
  totals.append(dict(mode=mode,sessions=len(rs),formal_outputs=sum(int(r['formal_outputs']) for r in rs),positive_outputs=sum(int(r['positive_outputs']) for r in rs),gain_sum=sum(int(r['gain']) for r in rs),gain_mean=statistics.mean(int(r['gain']) for r in rs),gain_median=statistics.median(int(r['gain']) for r in rs),failures=sum(int(r['no_output']) for r in rs),macro_sat=sum(int(r['macro_sat']) for r in rs),macro_distinct_outputs=sum(int(r['macro_outputs']) for r in rs),macro_positive_outputs=sum(int(r['macro_positive_outputs']) for r in rs),mean_unique_alternatives=statistics.mean(int(r['unique_alternatives']) for r in rs)))
 csvout(out/'costs.csv',costs);csvout(out/'totals.csv',totals);csvout(out/'controlled_waves.csv',bounds)
 decision=read(b/'decision.json');assert not decision['enter_C']
 dump(out/'delivery_audit.json',dict(batches=integrity,witness_vs_unsat_contradictions=contradictions,repeated_full_timeout_levels=same_level,delivery_counts=dict(late),online_C_started=False,round8_frozen_decision_unchanged=True,search_calls=0))
 print(json.dumps(totals,indent=2))
if __name__=='__main__':main()
