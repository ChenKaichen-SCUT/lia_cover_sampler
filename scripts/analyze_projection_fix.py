#!/usr/bin/env python3
"""Predeclared R11 descriptive contrasts and D gate. Session is the paired unit."""
import argparse,json,statistics
from pathlib import Path
from collections import defaultdict,Counter
from audit_projection_fix import lines
from reevaluate_round6_saved_candidates import write_csv,dump,require
MODES=['plain_only','plain_extra','projection_fixed'];PREFIXES=(1,2,5,10)
def gate(rows):
 by={(r['instance'],r['seed'],r['mode']):r for r in rows};cases=sorted({r['instance'] for r in rows});positive=[];early=[]
 for case in cases:
  pp=[by[case,s,'projection_fixed'] for s in (0,1)]
  if all(by[case,s,'projection_fixed']['gain']>by[case,s,m]['gain'] for s in (0,1) for m in MODES[:2]) and any(r['projection_positive_strict_winners']>0 for r in pp):positive.append(case)
  for control in MODES[:2]:
   for p in PREFIXES:
    regressed=[]
    for s in (0,1):
     a=by[case,s,'projection_fixed'][f'prefix_{p}'];b=by[case,s,control][f'prefix_{p}']
     if b is not None and (a is None or a<.9*b):regressed.append(s)
    if len(regressed)>=2:early.append(dict(instance=case,control=control,prefix=p,seeds=regressed))
 ratios={}
 for metric in ('gain','supply_per_second'):
  for case in cases:
   means={m:statistics.mean(r[metric] for r in rows if r['mode']==m and r['instance']==case) for m in MODES}
   for control in MODES[:2]:ratios[case+'/'+metric+'/'+control]=means['projection_fixed']/means[control] if means[control] else (1 if means['projection_fixed']==0 else float('inf'))
 protection=not early and all(v>=.9 for k,v in ratios.items() if '/supply_per_second/' in k)
 return dict(positive_independent_formulas=positive,early_regressions=early,ratios=ratios,protection_passed=protection,enter_D=False,propose_30s_only=len(positive)>=2 and protection,decision='propose_only_no_automatic_run' if len(positive)>=2 and protection else 'STOP_performance_not_passed')
def main():
 p=argparse.ArgumentParser();p.add_argument('input',type=Path);a=p.parse_args();require(json.loads((a.input/'AUDIT.json').read_text())['all_passed'],'audit required');runs={r['id']:r for r in lines(a.input/'runs.jsonl')};rows=[];curves=[];costs=[]
 for path in sorted((a.input/'audit').glob('*.json')):
  d=json.loads(path.read_text());r=runs[d['id']];s=d['summary'];curve=d['curve'];raw=a.input/r['output'];queries=lines(raw/'queries.jsonl');cy=lines(raw/'cycles.jsonl');extra=lines(raw/'extra.jsonl');plateau=json.loads((raw/'plateau.json').read_text())
  row={k:d[k] for k in ('id','instance','state','mode','seed','gain','total')};row.update(split=r['split'],seconds=r['seconds'],process_seconds=r['process_seconds'],cpu_seconds=r['cpu_seconds'],gain_percentage_points=100*d['gain']/d['total'],outputs=len(curve),zero_outputs=sum(x['gain']==0 for x in curve),positive_outputs=sum(x['gain']>0 for x in curve),first_positive_seconds=next((x['seconds'] for x in curve if x['gain']>0),None),supply_per_second=s['unique_validated_candidates']/r['process_seconds'],ordinary_opportunities_per_second=s['plain_calls']/r['process_seconds'],ordinary_sat_per_second=s['plain_sat']/r['process_seconds'],projection_positive_strict_winners=sum(x['origin']=='projection' and x['gain']>0 and x['net_replacement_gain'] is not None and x['net_replacement_gain']>0 for x in curve),extra_formal_winners=sum(x['origin']!='plain' for x in curve),extra_net_replacement_gain=sum(x['net_replacement_gain'] for x in curve if x['origin']!='plain' and x['net_replacement_gain'] is not None),stage_overruns=len(d['stage_overruns']))
  for k in ('plain_calls','plain_sat','plain_seconds','unique_validated_candidates','triggers','extra_calls','extra_sat','extra_spent','extra_overrun','feedback_seconds','guided_initialization_seconds','guided_cleanup_seconds','formula_seconds','restore_seconds','ordinary_engine_seconds','initialization_seconds','output_seconds','session_seconds','duplicates','late_candidates','projection_blocks','projection_stop_reason'):row[k]=s[k]
  zero_run=maximum=0;positive_times=[0.]
  for c in curve:
   zero_run=zero_run+1 if c['gain']==0 else 0;maximum=max(maximum,zero_run)
   if c['gain']>0:positive_times.append(c['seconds'])
   curves.append(dict(id=d['id'],instance=d['instance'],state=d['state'],mode=d['mode'],seed=d['seed'],**c))
  positive_times.append(r['seconds']);row['longest_zero_run']=maximum;row['longest_plateau_seconds']=max(b-a for a,b in zip(positive_times,positive_times[1:]))
  for n in PREFIXES:row[f'prefix_{n}']=sum(x['gain'] for x in curve[:n]) if len(curve)>=n else None
  for origin in ('plain','extra_plain','projection'):
   qq=[q for q in queries if q['origin']==origin];cc=[c for c in d['candidates'] if c['origin']==origin]
   cost=dict(id=d['id'],instance=d['instance'],mode=d['mode'],seed=d['seed'],origin=origin,calls=len(qq),statuses=dict(Counter(q['status'] for q in qq)),saved_candidates=len(cc),positive_candidates=sum(c['gain']>0 for c in cc),timely_distinct_candidates=sum(c['timely'] and not c['formal_duplicate'] for c in cc),query_seconds=sum(q['query_seconds'] for q in qq))
   for k in ('actual_check_seconds','constraint_seconds','completion_seconds','validation_seconds'):cost[k]=sum(q['evidence'].get(k,0) for q in qq)
   cost['assessment_seconds']=sum(c['statistics'].get('seconds',{}).get(origin+'_assessment_seconds',0) for c in cy)
   costs.append(cost)
  pc=[c for c in d['candidates'] if c['origin']=='projection']
  row.update(projection_positive_candidates=sum(c['gain']>0 for c in pc),projection_formal_duplicates=sum(c['formal_duplicate'] for c in pc),projection_internal_duplicates=sum(c['internal_duplicate'] for c in pc),projection_new_candidates=sum(c['new_to_supply'] for c in pc),projection_block_seconds=sum(e['condition']['block_seconds'] for e in extra if e['condition']),projection_check_seconds=sum(q['evidence']['actual_check_seconds'] for q in queries if q['origin']=='projection'))
  rows.append(row)
 query_rows=[]
 for r in runs.values():
  raw=a.input/r['output'];extra={e['cycle']:e for e in lines(raw/'extra.jsonl')}
  for q in lines(raw/'queries.jsonl'):
   e=extra.get(q['cycle'],{});block=e.get('condition') or {}
   query_rows.append(dict(id=r['id'],instance=r['instance'],seed=r['seed'],mode=r['mode'],cycle=q['cycle'],origin=q['origin'],status=q['status'],query_seconds=q['query_seconds'],**q['evidence'],block_seconds=block.get('block_seconds') if q['origin']=='projection' else 0,blocks_before=block.get('blocks_before') if q['origin']=='projection' else None,blocks_after=block.get('blocks_after') if q['origin']=='projection' else None))
 write_csv(a.input/'queries.csv',query_rows)
 pairs=[];group=defaultdict(dict)
 for r in rows:group[r['state'],r['seed']][r['mode']]=r
 for (state,seed),g in group.items():
  for x,y in [('projection_fixed','plain_only'),('projection_fixed','plain_extra'),('plain_extra','plain_only')]:
   q=dict(state=state,instance=g[x]['instance'],seed=seed,contrast=x+' - '+y,split=g[x]['split'],gain_difference=g[x]['gain']-g[y]['gain'],supply_difference=g[x]['supply_per_second']-g[y]['supply_per_second'])
   for n in PREFIXES:q[f'prefix_{n}_difference']=g[x][f'prefix_{n}']-g[y][f'prefix_{n}'] if g[x][f'prefix_{n}'] is not None and g[y][f'prefix_{n}'] is not None else None
   pairs.append(q)
 aggregates=[]
 for case in sorted({r['instance'] for r in rows}):
  for mode in MODES:
   rr=[r for r in rows if r['instance']==case and r['mode']==mode];q=dict(instance=case,mode=mode,sessions=len(rr),no_output=sum(r['outputs']==0 for r in rr),zero_gain_sessions=sum(r['gain']==0 for r in rr))
   for k in ('gain','outputs','positive_outputs','zero_outputs','supply_per_second','plain_sat','plain_seconds','extra_spent','extra_net_replacement_gain','longest_zero_run','longest_plateau_seconds'):
    q[k+'_mean']=statistics.mean(r[k] for r in rr);q[k+'_median']=statistics.median(r[k] for r in rr)
   aggregates.append(q)
 pairsummary=[]
 for case in sorted({r['instance'] for r in pairs}):
  for contrast in sorted({r['contrast'] for r in pairs}):
   rr=[r for r in pairs if r['instance']==case and r['contrast']==contrast];ds=[r['gain_difference'] for r in rr]
   pairsummary.append(dict(instance=case,contrast=contrast,pairs=len(ds),win=sum(x>0 for x in ds),tie=sum(x==0 for x in ds),loss=sum(x<0 for x in ds),mean_difference=statistics.mean(ds),median_difference=statistics.median(ds)))
 for name,data in [('sessions',rows),('curves',curves),('costs',costs),('pairs',pairs),('summary',aggregates),('paired_summary',pairsummary)]:write_csv(a.input/(name+'.csv'),data)
 if json.loads((a.input/'plan.json').read_text())['stage']=='C':dump(a.input/'decision.json',gate(rows))
 dump(a.input/'ANALYZED.json',dict(sessions=len(rows),paired_unit='session; seeds are solver seeds, not independent formulas',zero_sessions_retained=True))
if __name__=='__main__':main()
