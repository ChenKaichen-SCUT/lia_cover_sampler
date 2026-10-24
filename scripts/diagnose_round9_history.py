#!/usr/bin/env python3
"""Bounded R8 offline diagnostics. No solver calls; select before attaching witnesses."""
import json,csv
from functools import cache
from collections import Counter,defaultdict
from pathlib import Path
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,dump,write_csv,file_hash,model_id,model_key,require
from analyze_early_coverage import BATCHES,patterns
file_hash=cache(file_hash)
ROOT=Path(__file__).resolve().parents[1]
CASE='SMT_random_LIA_ex14600_2600_100.smt2'
def lines(p):return [json.loads(x) for x in p.read_text().splitlines()]
def main():
 out=ROOT/'results/round9_diagnostic';out.mkdir(exist_ok=False);rows=[];counts=Counter();queried=set();sources={};contexts=[]
 for batch in ('online_30s','mechanism_400ms'):
  base=ROOT/'results/round8_macro'/batch
  for rec in lines(base/'runs.jsonl'):
   d=base/rec['output'];initial=json.loads((d/'initial.json').read_text());cat=json.loads((d/'catalog.json').read_text());prior=defaultdict(list)
   for name,sha in rec['hashes'].items():require(file_hash(d/name)==sha,'raw source changed');sources[str(d/name)]=sha
   if rec['instance']==CASE and cat:contexts.append((rec['id'],initial,cat))
   for r in lines(d/'rounds.jsonl'):
    for q in r['broad_queries']:
     choices=q.get('choices',[]);before=[prior[tuple(x)].copy() for x in choices]
     row=dict(batch=batch,formula=rec['instance'],seed=rec['seed'],session=rec['id'],round=r['round'],query=q['query'],origin=q['origin'],choices=choices,status=q['status'],reason_unknown=q['reason'],requested_timeout_ms='unknown',granted_timeout_ms='unknown',check_seconds='unknown',query_seconds=q['query_seconds'],action_remaining_before_check='unknown',session_remaining_before_check='unknown',external_interrupt='unknown',prior_attempts=[len(x) for x in before],prior_results=before,macro_timer_seconds=r['statistics']['seconds'].get('macro_solve_seconds','unknown') if r['macro_calls']==1 and q['origin']=='macro_choice' else 'unknown',diagnosis='insufficient_per_query_evidence' if q['status'] not in ('sat','unsat','exploration_exhausted') else 'saved_solver_result',raw_sha256=file_hash(d/'rounds.jsonl'))
     rows.append(row)
     for x in choices:
      key=tuple(x);prior[key].append(q['status'])
      if rec['instance']==CASE:queried.add(key);counts[key]+=q['status'] not in ('sat',)
 write_csv(out/'historical_queries.csv',rows)
 cat=contexts[0][2];allkeys=[(g,a) for g,x in enumerate(cat['groups']) for a in range(len(x['alternatives']))]
 selected=sorted(queried,key=lambda k:(-counts[k],k))[:3]+[k for k in allkeys if k not in queried][:3]
 selection=[dict(group=g,alternative=a,group_node=cat['groups'][g]['node'],alternative_node=cat['groups'][g]['alternatives'][a]['node'],old_failures=counts[(g,a)],kind='queried' if (g,a) in queried else 'unqueried') for g,a in selected]
 dump(out/'selection.json',dict(rule='top3 failures then first3 unqueried; frozen before witness lookup',alternatives=selection,formula=str(ROOT/'benchmarks'/CASE),protocol_sha256=file_hash(ROOT/'docs/第九轮实验协议.md')))
 # Only the related formula, first 100 timely saved models in each historical stream.
 pools=[]
 with no_search():
  metric=FrozenCoverage((ROOT/'benchmarks'/CASE).read_text())
  expected=json.loads((ROOT/'results/round8_analysis/cases'/CASE/'hashes.json').read_text())
  for mode,batch in BATCHES.items():
   base=ROOT/'results'/batch;rec=next(r for r in lines(base/'runs.jsonl') if r['instance']==CASE and r['seed']==0);d=base/rec['run'];p=d/(CASE+'.samples' if mode=='highdiv' else 'models.jsonl');t=d/(CASE+'.times.csv' if mode=='highdiv' else 'emission.csv')
   require(file_hash(p)==expected[str(p)] and file_hash(t)==expected[str(t)],'historical model binding');sources[str(p)]=file_hash(p);sources[str(t)]=file_hash(t)
   times={int(r[0]):float(r[1]) for r in csv.reader(t.open()) if r and r[0].isdigit()};n=0
   for line_no,line in enumerate(p.open(),1):
    if mode=='highdiv':
     idx,body=line.strip().split(':',1);idx=int(idx);values={x.split(':',1)[0].strip():int(x.split(':',1)[1].strip('()').replace(' ','')) for x in body.split(';') if x};m=[dict(name=k,sort='Int',value=str(v)) for k,v in sorted(values.items())]
    else:r=json.loads(line);idx=r['index'];m=r['assignment'];values={x['name']:int(x['value']) for x in m}
    if idx not in times or times[idx]>900:continue
    metric.apply(metric.empty.copy(),m) # full original F, complete typed assignment
    pools.append(dict(mode=mode,index=idx,line=line_no,model=m,model_hash=model_id(m),source=str(p),source_sha256=file_hash(p),pattern=patterns(cat['groups'],values)));n+=1
    if n==100:break
  witnesses={k:next((w for w in pools if k[1] in w['pattern'][k[0]]),None) for k in allkeys}
  if not any(witnesses[k] for k in selected):
   for k in [k for k in allkeys if witnesses[k] and k not in selected][:2]:selected.append(k);g,a=k;selection.append(dict(group=g,alternative=a,group_node=cat['groups'][g]['node'],alternative_node=cat['groups'][g]['alternatives'][a]['node'],old_failures=counts[k],kind='known_feasibility_positive_control'))
  wr=[];cv=[]
  for g,a in allkeys:
   w=witnesses[(g,a)];wr.append(dict(group=g,alternative=a,group_node=cat['groups'][g]['node'],alternative_node=cat['groups'][g]['alternatives'][a]['node'],label='WITNESSED_FEASIBLE' if w else 'NO_WITNESS_IN_POOL',selected=(g,a) in selected,model_hash=w['model_hash'] if w else '',source=w['source'] if w else '',source_sha256=w['source_sha256'] if w else '',line=w['line'] if w else '',F_and_alternative_valid=bool(w)))
  for sid,init,c in contexts:
   state=metric.empty.copy();seen=set()
   for m in init['prefix']+[init['initial_model']]:
    if model_key(m) not in seen:state=metric.apply(state,m);seen.add(model_key(m))
   for g,a in selected:
    w=witnesses[(g,a)];cv.append(dict(session=sid,group=g,alternative=a,model_hash=w['model_hash'] if w else '',different_from_seed=model_key(w['model'])!=model_key(init['initial_model']) if w else '',formal_duplicate=model_key(w['model']) in seen if w else '',gain=metric.count(metric.apply(state.copy(),w['model']))-metric.count(state) if w else ''))
  dump(out/'diagnostic_set.json',dict(formula=str(ROOT/'benchmarks'/CASE),alternatives=[dict(s,label='WITNESSED_FEASIBLE' if witnesses[(s['group'],s['alternative'])] else 'NO_WITNESS_IN_POOL') for s in selection],selection_sha256=file_hash(out/'selection.json'),pool_size=len(pools),search_calls=0))
  dump(out/'witness_models.json',[{k:v for k,v in w.items() if k!='pattern'} for w in pools if w['model_hash'] in {witnesses[k]['model_hash'] for k in selected if witnesses[k]}])
 write_csv(out/'feasibility.csv',wr);write_csv(out/'witness_value.csv',cv);dump(out/'sources.json',sources);dump(out/'COMPLETE.json',dict(queries=len(rows),pool_models=len(pools),search_calls=0,selected=len(selection),unknown_reasons=Counter(r['reason_unknown'] for r in rows if r['origin']=='macro_choice' and r['status']=='unknown')))
if __name__=='__main__':main()
