#!/usr/bin/env python3
"""R10 saved-model diagnosis. Original Manual evaluator; search is disabled."""
import json,csv,statistics,math
from pathlib import Path
from collections import defaultdict,Counter
from concurrent.futures import ProcessPoolExecutor
from functools import cache
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,dump,write_csv,file_hash,model_id,model_key,require,object_hash
from analyze_plateaus import diagnose
from analyze_early_coverage import BATCHES
ROOT=Path(__file__).resolve().parents[1]
file_hash=cache(file_hash)
def lines(p):return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
def dependency(metric):
 ids={n.get_id():i for i,n in enumerate(metric.nodes)};parents=[[] for n in metric.nodes]
 for i,n in enumerate(metric.nodes):
  for c in n.children():parents[ids[c.get_id()]].append(i)
 related=[];truncated=[]
 for v in metric.decls:
  todo=[ids[v.get_id()]];seen=set();ops=0
  while todo and len(seen)<128 and ops<512:
   n=todo.pop();ops+=1
   if n in seen:continue
   seen.add(n)
   for p in parents[n]:
    ops+=1
    if ops>=512:break
    todo.append(p)
  related.append(sorted(seen));truncated.append(bool(todo) or ops>=512)
 return related,truncated

def work(case,records):
 out=ROOT/'results/round10_analysis'/case;out.mkdir();formal=[];candidates=[];missing=[];vars_rows=[];sources={};witness_requests={}
 with no_search():
  metric=FrozenCoverage((ROOT/'benchmarks'/case).read_text());nodes=metric.nodes;ids=[n.get_id() for n in nodes];related,truncated=dependency(metric);names=[str(v.decl().name()) for v in metric.decls];nvars=len(names)
  refdir=ROOT/'results/round8_analysis/cases'/case;manifest=json.loads((ROOT/'results/round8_analysis/case_evidence_archive.json').read_text())['files']
  for name in ('reference.json','pool_witnesses.json','hashes.json'):
   path=refdir/name;require(file_hash(path)==manifest[str(path.relative_to(ROOT/'results/round8_analysis'))],'reference binding');sources[str(path)]=file_hash(path)
  reference=[list(map(int,x)) for x in json.loads((refdir/'reference.json').read_text())['states']];witnesses=defaultdict(list)
  for w in json.loads((refdir/'pool_witnesses.json').read_text()):witnesses[w['node']].append(w)
  for batch,rec in records:
   base=ROOT/'results'/batch;d=base/rec['output'];inp=json.loads((d/'initial.json').read_text());summary=json.loads((d/'summary.json').read_text())
   for name,sha in rec['hashes'].items():require(file_hash(d/name)==sha,'source changed');sources[str(d/name)]=sha
   rounds=lines(d/'rounds.jsonl');receipts={r['id']:r for r in lines(d/'commits.jsonl')};models=lines(d/'models.jsonl');byround={m['round']:m for m in models if receipts[m['id']]['timely']};state=metric.empty.copy();history=[];seen=set();zero=0;vstats=[Counter() for _ in names];local=[];pending={};cumwall=0;cumcheck=0;checks_known=batch.startswith('round9');query_count=0
   def values(m):return [int(next(x['value'] for x in m if x['name']==n)) for n in names]
   if inp['mechanism']:
    for m in inp['prefix']+[inp['initial_model']]:
     if model_key(m) not in seen:state=metric.apply(state,m);seen.add(model_key(m));history.append(values(m));history=history[-4:]
   def describe(m,index,origin,roundno,query,qwall,formal_flag,seconds):
    current=values(m);arr=[[int(state[k][1]),int(state[k][0])] for k in ids];weights=[(a^b).bit_count() for a,b in arr];gap=sum(weights);potential=[sum(weights[n] for n in rel) for rel in related]
    new=metric.apply(state,m);gain=metric.count(new)-metric.count(state);obs=sum(((int(new[k][0])&~int(state[k][0])).bit_count()+(int(new[k][1])&~int(state[k][1])).bit_count()) for k in ids)
    changes=[i for i in range(nvars) if history and current[i]!=history[-1][i]];recent=[i for i in range(nvars) if history and all(current[i]!=h[i] for h in history)]
    stable=[i for i in range(nvars) if len(history)>=2 and all(h[i]==history[-1][i] for h in history)]
    diag=diagnose(arr,reference,witnesses)
    if diag['witness_hash']:
     w=next(w for w in witnesses[diag['witness_node']] if w['model_hash']==diag['witness_hash']);witness_requests[w['model_hash']]=w
    row=dict(batch=batch,instance=case,session=rec['id'],seed=rec['seed'],state=rec['state'],model_index=index,round=roundno,query=query,origin=origin,formal=formal_flag,seconds=seconds,model_hash=model_id(m),actual_marginal_covered_bits=gain,new_observation_bits=obs,recent_zero_gain_run=zero,changed_variables_from_previous_model=len(changes),changed_variables_from_recent_history=len(recent),changed_missing_bit_related_variables=sum(potential[i]>0 for i in changes),coverage_gap_before=gap,completely_unobserved_nodes=sum(not(a|b) for a,b in arr),stable_variables_with_gap=sum(potential[i]>0 for i in stable),stable_related_gap_weight=sum(potential[i] for i in stable),changed_related_gap_weight=sum(potential[i] for i in changes),variables=nvars,dependency_truncated_variables=sum(truncated),query_wall_seconds=qwall,ordinary_query_wall_cumulative=cumwall,ordinary_check_cumulative=cumcheck if checks_known else None,ordinary_query_count=query_count,future_bits_completed_from_this_zero_observation=0,**diag)
    return row,new,current,potential,changes
   def commit(m,roundno,q,qwall):
    nonlocal state,zero,history
    row,new,current,potential,changes=describe(m['model'],m['index'],m['origin'],roundno,q,qwall,True,receipts[m['id']]['completion']);require(row['actual_marginal_covered_bits']==m['gain'],'saved marginal mismatch');require(model_key(m['model']) not in seen,'formal duplicate');seen.add(model_key(m['model']))
    gains=[]
    for i,k in enumerate(ids):
     old0,old1=int(state[k][1]),int(state[k][0]);new0,new1=int(new[k][1]),int(new[k][0]);added=(new0&new1)&~(old0&old1);gains.append(added.bit_count())
     while added:
      bit=added&-added;added-=bit
      if (i,bit) in pending:pending.pop((i,bit))['future_bits_completed_from_this_zero_observation']+=1
     if row['actual_marginal_covered_bits']==0:
      first=(new0|new1)&~(old0|old1)
      while first:bit=first&-first;first-=bit;pending[(i,bit)]=row
    for i in range(nvars):
     vstats[i]['observed_models']+=1;vstats[i]['gap_weight_sum']+=potential[i];stable=history and all(current[i]==h[i] for h in history);vstats[i]['stable_with_gap']+=bool(stable and potential[i]);vstats[i]['changes']+=i in changes
     if i in changes:vstats[i]['changes_with_zero_global_gain']+=not row['actual_marginal_covered_bits'];vstats[i]['related_new_bits_during_change']+=sum(gains[n] for n in related[i])
    zero=0 if row['actual_marginal_covered_bits'] else zero+1;state=new;history=(history+[current])[-4:];local.append(row)
   if -1 in byround:commit(byround[-1],-1,None,None)
   for r in rounds:
    require(metric.count(state)==r['covered_before'],'frozen state mismatch');matched={}
    for q in r['broad_queries']:
     if q['origin']!='exploration':continue
     cumwall+=q['query_seconds'];cumcheck+=q.get('evidence',{}).get('actual_check_seconds',0);query_count+=1
     aa=[x for x in r['broad_assessments'] if x['query']==q['query']]
     if aa:
      require(len(aa)==1,'ambiguous query candidate');v=aa[0];row,_,_,_,_=describe(v['model'],None,'exploration',r['round'],q['query'],q['query_seconds'],False,None);require(row['actual_marginal_covered_bits']==v['gain'],'internal gain');row['saved_candidate_timely']=v['timely'];candidates.append(row);matched[model_key(v['model'])]=(q['query'],q['query_seconds'])
     elif q['status']=='sat':missing.append(dict(batch=batch,session=rec['id'],round=r['round'],query=q['query'],reason='SAT assignment not saved; cannot reconstruct duplicate or missing assessment'))
    if r['round'] in byround:
     m=byround[r['round']];q,qwall=matched.get(model_key(m['model']),(None,None));commit(m,r['round'],q,qwall)
    require(metric.count(state)==r['covered_after'],'coverage after round')
   require(metric.export(state)==summary['coverage'],'final original state')
   # Forward waits are descriptive; tail failures stay right-censored.
   for i,row in enumerate(local):
    nxt=next((r for r in local[i+1:] if r['actual_marginal_covered_bits']>0),None)
    row['next_positive_censored']=nxt is None;row['models_until_next_positive']=next((j for j,r in enumerate(local[i+1:],1) if r['actual_marginal_covered_bits']>0),None);row['ordinary_query_wall_until_next_positive']=(nxt['ordinary_query_wall_cumulative']-row['ordinary_query_wall_cumulative']) if nxt else cumwall-row['ordinary_query_wall_cumulative'];row['ordinary_check_until_next_positive']=(nxt['ordinary_check_cumulative']-row['ordinary_check_cumulative']) if nxt and checks_known else None
   formal+=local
   for i,name in enumerate(names):vars_rows.append(dict(batch=batch,instance=case,session=rec['id'],variable=name,variable_index=i,related_nodes=len(related[i]),dependency_truncated=truncated[i],**vstats[i]))
  # Revalidate only the concrete archived witnesses actually cited above.
  requests=defaultdict(dict)
  for w in witness_requests.values():requests[w['mode']][w['raw_line']]=w
  verified=[];sourcehashes=json.loads((refdir/'hashes.json').read_text())
  for mode,requests_by_line in requests.items():
   p=next(Path(path) for path in sourcehashes if f'/results/{BATCHES[mode]}/raw/' in path and path.endswith('.samples' if mode=='highdiv' else 'models.jsonl'));require(file_hash(p)==sourcehashes[str(p)],'witness raw hash');sources[str(p)]=file_hash(p)
   for line_no,line in enumerate(p.open(),1):
    if line_no not in requests_by_line:continue
    if mode=='highdiv':
     body=line.strip().split(':',1)[1];vals={x.split(':',1)[0].strip():int(x.split(':',1)[1].strip('()').replace(' ','')) for x in body.split(';') if x};m=[dict(name=k,sort='Int',value=str(v)) for k,v in sorted(vals.items())]
    else:m=json.loads(line)['assignment']
    w=requests_by_line[line_no];require(object_hash(m)==w['model_hash'],'witness identity');st=metric.apply(metric.empty.copy(),m);n=ids[w['node']];require((int(st[n][0])&int(w['one']))==int(w['one']) and (int(st[n][1])&int(w['zero']))==int(w['zero']),'witness observations');verified.append(dict(mode=mode,raw_line=line_no,model_hash=w['model_hash'],source_sha256=file_hash(p),validated=True));
    if line_no>=max(requests_by_line):break
  for name,rows in [('model_redundancy',formal),('candidate_redundancy',candidates),('variable_feedback',vars_rows),('missing_candidates',missing),('verified_witnesses',verified)]:write_csv(out/(name+'.csv'),rows)
  dump(out/'sources.json',sources)
 return dict(instance=case,formal_models=len(formal),saved_ordinary_candidates=len(candidates),missing_candidates=len(missing),verified_reference_models=len(verified))

def main():
 out=ROOT/'results/round10_analysis';out.mkdir(exist_ok=False);jobs=defaultdict(list)
 for batch,mode in [('round9_mechanism/main_10s','plain_fixed'),('round8_macro/online_30s','micro_plain')]:
  for r in lines(ROOT/'results'/batch/'runs.jsonl'):
   if r['mode']==mode:jobs[r['instance']].append((batch,r))
 dump(out/'plan.json',dict(base='86f97e4',script_sha256=file_hash(__file__),search_forbidden=True,dependency_node_cap=128,dependency_operation_cap=512,sessions=sum(map(len,jobs.values())),cases=sorted(jobs)))
 with ProcessPoolExecutor(max_workers=3) as pool:results=list(pool.map(work,jobs.keys(),jobs.values()))
 for name in ('model_redundancy','candidate_redundancy','variable_feedback','missing_candidates','verified_witnesses'):
  rows=[]
  for case in jobs:
   p=out/case/(name+'.csv')
   if p.stat().st_size>1:rows+=list(csv.DictReader(p.open()))
  write_csv(out/(name+'.csv'),rows)
 dump(out/'COMPLETE.json',dict(search_calls=0,cases=results))
if __name__=='__main__':main()
