#!/usr/bin/env python3
"""Session-level frozen comparisons; includes zero-output/timeout sessions."""
import argparse,json,statistics
from collections import Counter,defaultdict
from pathlib import Path
from audit_macro_pairs import lines
from reevaluate_round6_saved_candidates import dump,write_csv

def gate(rows):
 by={(r['state'],r['seed'],r['mode']):r for r in rows};seeds=set();wins=set();deltas=[]
 for r in rows:
  if r['mode']!='macro_fair_retry':continue
  if r['macro_positive_outputs']>0:seeds.add(r['seed'])
  p=by[(r['state'],r['seed'],'plain_fixed')];d=r['gain']-p['gain'];deltas.append(d)
  if d>0:wins.add(r['seed'])
 enter=len(seeds)>=2 and len(wins)>=2 and statistics.mean(deltas)>0 and statistics.median(deltas)>=0
 return dict(enter_C=enter,decision='ENTER_C' if enter else 'NO_ONLINE_PROMOTION',macro_positive_seeds=sorted(seeds),coverage_win_seeds=sorted(wins),mean_paired_delta=statistics.mean(deltas),median_paired_delta=statistics.median(deltas),pairs=len(deltas),rule='>=2 macro-positive seeds; >=2 winning seeds; mean delta>0 and median>=0; all correctness audits pass',round8_decision='unchanged: STOP')

def main():
 p=argparse.ArgumentParser();p.add_argument('input',type=Path);a=p.parse_args();base=a.input;plan=json.loads((base/'plan.json').read_text());assert json.loads((base/'AUDIT.json').read_text())['all_passed'];records=lines(base/'runs.jsonl');rows=[];queries=[];prefixes=[]
 for r in sorted(records,key=lambda r:r['id']):
  d=base/r['output'];s=json.loads((d/'summary.json').read_text())
  if plan['stage']=='A':
   row={k:r[k] for k in ('id','group','alternative','group_node','alternative_node','seed','timeout_ms','label','kind')};row.update(s['evidence']);row.update({k:v for k,v in s.items() if k not in ('statistics','evidence','source_sha256')});row.update(process_seconds=r['process_seconds'],cpu_seconds=r['cpu_seconds']);rows.append(row);continue
  audit=json.loads((base/'audit'/(r['id'].replace('/','__')+'.json')).read_text());curve=audit['curve'];rounds=lines(d/'rounds.jsonl');allq=[q for t in rounds for q in t['broad_queries']];macroq=[q for q in allq if q['origin']=='macro_choice'];macrom=[m for m in curve if m['origin']=='macro_choice'];distinct=set();repeat_cost=0
  for t in rounds:
   for q in t['broad_queries']:
    key=tuple(map(tuple,q.get('choices',[])));repeat=bool(key and key in distinct)
    if repeat:repeat_cost+=q['query_seconds']
    if key:distinct.add(key)
    row={k:r[k] for k in ('id','state','seed','mode','wave','group_id')};row.update(round=t['round'],query=q['query'],origin=q['origin'],status=q['status'],reason=q['reason'],query_seconds=q['query_seconds'],choices=key,repeat=repeat,selection_queue=q.get('selection_queue','plain'),attempt_before=q.get('attempt_before'),attempt_after=q.get('attempt_after'));row.update(q['evidence']);queries.append(row)
  first=next((m['seconds'] for m in curve if m['gain']>0),None)
  row={k:r[k] for k in ('id','instance','state','seed','mode','wave','group_id','start_utc','end_utc')};row.update(formal_outputs=len(curve),positive_outputs=sum(m['gain']>0 for m in curve),gain=s['gain'],base_covered=audit['base_covered'],final_covered=audit['final_covered'],total=audit['total'],first_positive_seconds=first,no_output=int(not curve),no_positive=int(first is None),macro_queries=len(macroq),macro_sat=sum(q['evidence']['raw_status']=='sat' for q in macroq),macro_outputs=len(macrom),macro_positive_outputs=sum(m['gain']>0 for m in macrom),macro_output_gain=sum(m['gain'] for m in macrom),fallback_outputs=sum(m['origin']=='exploration' for m in curve),unique_alternatives=len(distinct),repeat_query_seconds=repeat_cost,queries=len(allq),query_statuses=dict(Counter(q['status'] for q in allq)),initialization_seconds=s['initialization_seconds'],engine_seconds=s['engine_seconds'],catalog_seconds=s['catalog_seconds'],macro_engine_seconds=s['macro_engine_seconds'],broad_spent_seconds=s['broad_spent_seconds'],process_seconds=r['process_seconds'],cpu_seconds=r['cpu_seconds'],gain_per_process_second=s['gain']/r['process_seconds']);rows.append(row)
  for n in (1,2,5,10):prefixes.append(dict(id=r['id'],state=r['state'],seed=r['seed'],mode=r['mode'],new_sample_prefix=n,reached=len(curve)>=n,gain=sum(m['gain'] for m in curve[:n]) if len(curve)>=n else None,seconds=curve[n-1]['seconds'] if len(curve)>=n else None))
 write_csv(base/'sessions.csv',rows)
 if plan['stage']=='A':
  agg=[]
  for ms in (50,200,1000,2000):
   group=[r for r in rows if r['timeout_ms']==ms];agg.append(dict(timeout_ms=ms,n=len(group),sat=sum(r['raw_status']=='sat' for r in group),unsat=sum(r['raw_status']=='unsat' for r in group),unknown=sum(r['raw_status']=='unknown' for r in group),timely_models=sum(r['has_model'] and r['timely'] for r in group),full_grants=sum(r['granted_timeout_ms']==ms for r in group),mean_check_seconds=statistics.mean(r['actual_check_seconds'] for r in group),median_check_seconds=statistics.median(r['actual_check_seconds'] for r in group),mean_full_seconds=statistics.mean(r['process_seconds'] for r in group)))
  write_csv(base/'aggregate.csv',agg);return
 write_csv(base/'queries.csv',queries);write_csv(base/'new_prefixes.csv',prefixes);agg=[]
 for state in sorted({r['state'] for r in rows}):
  for mode in sorted({r['mode'] for r in rows}):
   group=[r for r in rows if r['state']==state and r['mode']==mode];entry=dict(state=state,mode=mode,n=len(group),failures=sum(r['no_output'] for r in group),zero_gain=sum(r['no_positive'] for r in group))
   for key in ('gain','formal_outputs','positive_outputs','unique_alternatives','repeat_query_seconds','initialization_seconds','catalog_seconds','macro_engine_seconds','process_seconds','gain_per_process_second'):
    entry['mean_'+key]=statistics.mean(r[key] for r in group);entry['median_'+key]=statistics.median(r[key] for r in group)
   agg.append(entry)
 write_csv(base/'aggregate.csv',agg);pairs=[];by={(r['state'],r['seed'],r['mode']):r for r in rows}
 comparisons=[('macro_budget_fixed','plain_fixed'),('macro_fair_retry','plain_fixed'),('macro_fair_retry','macro_budget_fixed')] if plan['stage']=='B' else [('micro_macro','micro_plain')]
 for left,right in comparisons:
  for state,seed in sorted({(r['state'],r['seed']) for r in rows}):
   l=by[state,seed,left];r=by[state,seed,right];delta=l['gain']-r['gain'];pairs.append(dict(state=state,seed=seed,left=left,right=right,left_gain=l['gain'],right_gain=r['gain'],delta=delta,outcome='win' if delta>0 else 'loss' if delta<0 else 'tie',same_wave=l['wave']==r['wave']))
 write_csv(base/'pairs.csv',pairs);summary=[]
 for left,right in comparisons:
  group=[p for p in pairs if p['left']==left and p['right']==right];summary.append(dict(left=left,right=right,n=len(group),wins=sum(p['delta']>0 for p in group),ties=sum(p['delta']==0 for p in group),losses=sum(p['delta']<0 for p in group),mean_delta=statistics.mean(p['delta'] for p in group),median_delta=statistics.median(p['delta'] for p in group)))
 write_csv(base/'paired_summary.csv',summary)
 if plan['stage']=='B':dump(base/'decision.json',gate(rows))
if __name__=='__main__':main()
