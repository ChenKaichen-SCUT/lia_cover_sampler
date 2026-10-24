#!/usr/bin/env python3
"""Analyze the two frozen macro queues; preserve empty/incomplete paired prefixes."""
import argparse,csv,json,gzip,statistics,subprocess
from collections import defaultdict
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,dump,write_csv,model_key
from analyze_early_coverage import patterns,ROOT
from analyze_plateaus import diagnose
TIMES=(1,2,5,10,20,30);PREFIXES=(2,5,10,20,50)
def readlines(p):return [json.loads(x) for x in p.read_text().splitlines()]
def analyze_one(args):
    folder,record=args;folder=Path(folder);d=folder/record['output'];audit=json.loads((folder/'audit'/(record['id'].replace('/','__')+'.json')).read_text());summary=audit['summary'];initial=json.loads((d/'initial.json').read_text());rounds=readlines(d/'rounds.jsonl');events=readlines(d/'events.jsonl');curves=audit['curve'];kind=record['kind']
    row={k:record[k] for k in ('id','instance','state','seed','mode','split','kind')};row.update(formal_count=len(curves),gain=summary['gain'],final_covered=audit['final_covered'],total=audit['total'],final_percent=100*audit['final_covered']/audit['total'],session_seconds=record['process_seconds'],sampler_observed_seconds=summary['session_seconds'],initialization_seconds=summary['initialization_seconds'],engine_seconds=summary['engine_seconds'],gain_per_complete_second=summary['gain']/record['process_seconds'],no_candidate=int(not any(c['index']>(0 if kind=='mechanism' else 1) for c in curves)),first_positive_seconds=next((c['seconds'] for c in curves if c['gain']>0),None),macro_calls=sum(r['macro_calls'] for r in rounds),fallback_calls=sum(r['fallback_calls'] for r in rounds),broad_calls=sum(r['broad_calls'] for r in rounds),micro_seconds=sum(r['micro_seconds'] for r in rounds),broad_seconds=sum(r['broad_seconds'] for r in rounds),stall_events=summary['stall_events'],recoveries=sum(e['kind']=='recovery' for e in events),recovery_gain=sum(e.get('gain',0) for e in events if e['kind']=='recovery'),macro_recovery_gain=sum(e.get('gain',0) for e in events if e['kind']=='recovery' and e['origin']=='macro_choice'),unrecovered_stall=summary['unrecovered_stall'],terminal=summary['terminal'],overrun=max(0,record['process_seconds']-record['seconds']))
    catpath=ROOT/'results/round8_analysis/cases'/record['instance']/'catalog.json';cat=json.loads(catpath.read_text()) if catpath.exists() else json.loads((d/'catalog.json').read_text());row['supported_groups']=len(cat['groups']) if cat else 0
    refpath=ROOT/'results/round8_analysis/cases'/record['instance']/'reference.json';reference=json.loads(refpath.read_text())['states'] if refpath.exists() else None;witnesses=defaultdict(list)
    if reference:
        reference=[list(map(int,x)) for x in reference]
        for w in json.loads(refpath.with_name('pool_witnesses.json').read_text()):witnesses[w['node']].append(w)
    receipts={r['id']:r for r in readlines(d/'commits.jsonl')};models=[m for m in readlines(d/'models.jsonl') if receipts[m['id']]['timely']];states=[];actual=[];seenpatterns=set();previous=None
    with no_search():
        metric=FrozenCoverage(Path(record['formula']).read_text());state=metric.empty.copy()
        if kind=='mechanism':
            for m in initial['prefix']+[initial['initial_model']]:state=metric.apply(state,m)
        states.append(metric.export(state)['states'])
        for m in models:
            state=metric.apply(state,m['model']);states.append(metric.export(state)['states']);pat=patterns(cat['groups'],{x['name']:int(x['value']) for x in m['model']}) if cat else ();novel=bool(cat and cat['groups']) and pat not in seenpatterns;changed=previous is not None and previous!=pat
            if previous is None and kind=='mechanism' and cat:changed=patterns(cat['groups'],{x['name']:int(x['value']) for x in initial['initial_model']})!=pat
            seenpatterns.add(pat);previous=pat;actual.append(dict(index=len(actual)+1,seconds=receipts[m['id']]['completion'],gain=m['gain'],origin=m['origin'],mode_changed=changed,new_mode=novel,covered=metric.count(state),total=metric.total))
    end=min(record['seconds'],summary['session_seconds']);text=str(end)+'\n'+''.join(f"{r['seconds']:.17g} {r['index']} {r['gain']} {int(r['new_mode'])} {r['origin']}\n" for r in actual)
    plateau=json.loads(subprocess.check_output([str(ROOT/'build/round8/release/choice_catalog_probe'),'--plateau'],input=text,text=True));segs=[];known=0
    for i,s in enumerate(plateau['segments']):
        diagnosis=diagnose([list(map(int,x)) for x in states[s['start_index']]],reference,witnesses) if reference else dict(reachability='REACHABILITY_UNKNOWN',known_missing_bits=0)
        duration=s['end']-s['start'];known+=duration*(diagnosis['reachability']=='PLATEAU_WITH_KNOWN_REACHABLE_GAP');segs.append(dict(id=record['id'],segment=i,**s,**diagnosis,duration=duration))
    row.update(longest_zero_run=plateau['longest_zero_run'],longest_no_growth=plateau['longest_no_growth'],known_gap_seconds=known,known_gap_fraction=known/end if end else 0,actual_mode_changes=sum(c['mode_changed'] for c in actual),macro_formal_gain=sum(c['gain'] for c in actual if c['origin']=='macro_choice'),macro_formal_models=sum(c['origin']=='macro_choice' for c in actual),changed_mode_gain=sum(c['gain'] for c in actual if c['mode_changed']),zero_gain_mode_changes=sum(c['mode_changed'] and c['gain']==0 for c in actual))
    row['macro_statuses']=json.dumps(dict(__import__('collections').Counter(q['status'] for r in rounds for q in r['broad_queries'] if q['origin']=='macro_choice')))
    checkpoints=[]
    for axis,levels in [('time',TIMES if kind=='online' else (.4,)),('prefix',PREFIXES if kind=='online' else (1,))]:
        for level in levels:
            available=axis=='time' or len(actual)>=level
            chosen=[c for c in actual if c['seconds']<=level] if axis=='time' else actual[:int(level)]
            covered=chosen[-1]['covered'] if chosen else audit['base_covered'];checkpoints.append(dict(id=record['id'],instance=record['instance'],state=record['state'],seed=record['seed'],mode=record['mode'],split=record['split'],axis=axis,level=level,available=available,covered=covered if available else None,percent=100*covered/audit['total'] if available else None,models=len(chosen)))
    actions=[];anchor={x['name']:int(x['value']) for x in initial['initial_model']} if initial['initial_model'] else {};byround={m['round']:m for m in models}
    for r in rounds:
        assessed={x['query']:x for x in r['broad_assessments']}
        for q in r['broad_queries']:
            candidate=assessed.get(q['query']);forced=set()
            if cat:
                for g,a in q.get('choices',[]):
                    for v in cat['groups'][g]['alternatives'][a]['assignments']:
                        if anchor[v['name']]!=int(v['value']):forced.add(v['name'])
            actual_changed=None
            if candidate and cat:actual_changed=patterns(cat['groups'],anchor)!=patterns(cat['groups'],{x['name']:int(x['value']) for x in candidate['model']})
            actions.append(dict(id=record['id'],instance=record['instance'],state=record['state'],seed=record['seed'],mode=record['mode'],round=r['round'],query=q['query'],origin=q['origin'],status=q['status'],reason=q.get('reason',''),forced_distinct_variables=len(forced),actual_mode_changed=actual_changed,gain=candidate['gain'] if candidate else None,timely=candidate['timely'] if candidate else None,query_seconds=q['query_seconds'],broad_action_seconds=r['broad_seconds'],round_seconds=r['full_seconds'],session_initialization_seconds=summary['initialization_seconds'],process_seconds=record['process_seconds'],choices=json.dumps(q.get('choices',[])),global_single_alternative_proof=q.get('global_single_alternative_proof',False)))
        if r['round'] in byround:anchor={x['name']:int(x['value']) for x in byround[r['round']]['model']}
    return row,checkpoints,segs,actual,actions

def main():
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('--jobs',type=int,default=9);a=p.parse_args();records=readlines(a.input/'runs.jsonl');assert json.loads((a.input/'AUDIT.json').read_text())['all_passed'];rows=[];checkpoints=[];segments=[];actions=[]
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:
        for row,cs,ss,curve,qs in pool.map(analyze_one,[(str(a.input),r) for r in records]):
            rows.append(row);checkpoints+=cs;segments+=ss;actions+=qs;dump(a.input/'audit'/(row['id'].replace('/','__')+'.curve.json'),curve)
    pairs=[];group=defaultdict(dict)
    for r in checkpoints:group[r['state'],r['seed'],r['axis'],r['level']][r['mode']]=r
    for (state,seed,axis,level),m in group.items():
        x,y=m['micro_plain'],m['micro_macro'];available=x['available'] and y['available'];delta=y['percent']-x['percent'] if available else None
        pairs.append(dict(instance=x['instance'],state=state,seed=seed,split=x['split'],axis=axis,level=level,common=available,plain_percent=x['percent'],macro_percent=y['percent'],delta_pp=delta,outcome='incomplete' if delta is None else 'win' if delta>0 else 'loss' if delta<0 else 'tie'))
    grouped=defaultdict(list)
    for r in pairs:grouped[r['instance'],r['axis'],r['level']].append(r)
    aggregate=[]
    for (instance,axis,level),rs in grouped.items():
        ds=[r['delta_pp'] for r in rs if r['common']];aggregate.append(dict(instance=instance,axis=axis,level=level,pairs=len(rs),common_pairs=len(ds),incomplete=len(rs)-len(ds),mean_delta_pp=statistics.mean(ds) if ds else None,median_delta_pp=statistics.median(ds) if ds else None,wins=sum(x>0 for x in ds),ties=sum(x==0 for x in ds),losses=sum(x<0 for x in ds)))
    for name,data in [('sessions',rows),('checkpoints',checkpoints),('pairs',pairs),('aggregate',aggregate),('plateau_segments',segments),('broad_queries',actions)]:write_csv(a.input/(name+'.csv'),data)
    group=defaultdict(list)
    for r in rows:group[r['instance'],r['mode']].append(r)
    summary=[]
    fields=('gain','final_percent','session_seconds','initialization_seconds','gain_per_complete_second','longest_zero_run','longest_no_growth','known_gap_fraction','actual_mode_changes','macro_formal_gain','recoveries','recovery_gain','macro_recovery_gain')
    for (instance,mode),rs in group.items():
        result=dict(instance=instance,mode=mode,n=len(rs),failures=sum(r['no_candidate'] for r in rs))
        for f in fields:result['mean_'+f]=statistics.mean(r[f] for r in rs);result['median_'+f]=statistics.median(r[f] for r in rs)
        summary.append(result)
    write_csv(a.input/'state_summary.csv',summary)
    decision=dict(kind=records[0]['kind'],automatic_followup=False)
    if records[0]['kind']=='online':
        eligible=[];regressions=[]
        for instance in set(r['instance'] for r in rows):
            entries=[r for r in aggregate if r['instance']==instance and ((r['axis']=='time' and r['level']==30) or (r['axis']=='prefix' and r['level']==10))]
            if any(r['common_pairs']==3 and r['mean_delta_pp']>0 and r['wins']+r['ties']>=2 for r in entries):eligible.append(instance)
            if any(r['common_pairs'] and r['mean_delta_pp']<-1 for r in entries):regressions.append(instance)
        macro_recovery=sum(r['macro_recovery_gain'] for r in rows);recovery={m:sum(r['recovery_gain'] for r in rows if r['mode']==m) for m in ('micro_plain','micro_macro')}
        gains={m:sum(r['gain_per_complete_second']/r['total'] for r in rows if r['mode']==m) for m in ('micro_plain','micro_macro')}
        passed=len(eligible)>=2 and not regressions and macro_recovery>0 and recovery['micro_macro']>=recovery['micro_plain'] and gains['micro_macro']>gains['micro_plain']
        decision.update(improved_cases=eligible,obvious_regressions=regressions,macro_recovery_gain=macro_recovery,total_recovery=recovery,normalized_gain_rate=gains,promote=passed,conclusion='仅建议进一步测试，不自动开展' if passed else '停止扩大实验')
    dump(a.input/'decision.json',decision)
if __name__=='__main__':main()
