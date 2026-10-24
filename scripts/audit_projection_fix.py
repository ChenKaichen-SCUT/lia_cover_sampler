#!/usr/bin/env python3
"""R11 saved evidence only. Frozen Manual evaluation; all search entrypoints disabled."""
import argparse,json,math
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,require,file_hash,dump,write_csv,model_id,model_key

def lines(p):return [json.loads(s) for s in Path(p).read_text().splitlines()] if Path(p).exists() else []
def observed(state):return sum(int(a).bit_count()+int(b).bit_count() for a,b,_ in state.values())
def timely(w,seconds):return w['timely'] and w['completion']<=min(w['deadline'],seconds)
def values(m):return {x['name']:int(x['value']) for x in m}
def best_candidate(candidates):
    eligible=[c for c in candidates if c['timely'] and not c['formal_duplicate'] and not (c['origin']=='projection' and not c['new_to_supply'])]
    return max(eligible,key=lambda c:(c['gain'],c['observations'],c['origin']=='plain'),default=None)

def audit_job(args):
    base,r,source=args;d=Path(base)/r['output']
    for p,sha in r['hashes'].items():require(file_hash(d/p)==sha,'raw hash')
    require(r['exitcode']==0 and not r['watchdog'],'failed process retained; cannot pass')
    initial=json.loads((d/'initial.json').read_text());summary=json.loads((d/'summary.json').read_text())
    require(initial['source_sha256']==summary['source_sha256']==source,'source binding')
    require(initial['mode']==summary['mode']==r['mode'],'mode binding')
    require(initial['seconds']==r['seconds']==summary['budget_seconds'],'budget binding')
    cycles=lines(d/'cycles.jsonl');queries=lines(d/'queries.jsonl');models=lines(d/'models.jsonl');candidates=lines(d/'candidates.jsonl');extras=lines(d/'extra.jsonl');commits=lines(d/'commits.jsonl')
    cb,qb,mb,eb=(defaultdict(list) for _ in range(4))
    for a in candidates:cb[a['cycle']].append(a)
    for a in queries:qb[a['cycle']].append(a)
    for a in models:mb[a['cycle']].append(a)
    for a in extras:eb[a['cycle']].append(a)
    require(len({w['id'] for w in commits})==len(commits),'unique receipts');receipts={w['id']:w for w in commits}
    curve=[];assessed=[];nvalidated=0;version=0;last=0;first=True;opportunities=4;zeros=0;spent=0;dep_ops=0;stage_overruns=[];unique_supply=0;projection_seen=set()
    with no_search():
        metric=FrozenCoverage(Path(r['formula']).read_text());state=metric.empty.copy();seen=set();history=[]
        for m in initial['prefix']:
            if model_key(m) not in seen:state=metric.apply(state,m);seen.add(model_key(m));history=(history+[m])[-4:];nvalidated+=1
        added=model_key(initial['seed_model']) not in seen;require(added==initial['seed_added'],'seed duplication')
        if added:state=metric.apply(state,initial['seed_model']);seen.add(model_key(initial['seed_model']));history=(history+[initial['seed_model']])[-4:];nvalidated+=1
        require(metric.export(state)==initial['coverage'],'full initial seen0/seen1');basecovered=metric.count(state);supply=set(seen)
        names=[str(v.decl().name()) for v in metric.decls]
        for i,row in enumerate(cycles):
            require(row['cycle']==i and row['version_before']==version,'cycle/version order')
            require(row['covered_before']==metric.count(state),'frozen cycle state')
            qs=qb[i];require(qs and qs[0]['origin']=='plain' and sum(q['origin']=='plain' for q in qs)==1,'ordinary first exactly once')
            require(len(qs)<=2 and len(eb[i])<=1,'bounded extra query')
            require(all(q['version']==version for q in qs),'query frozen version')
            for q in qs:
                e=q['evidence'];require(e['granted_timeout_ms']<=e['requested_timeout_ms']<=2000,'timeout bound')
                if q['origin']!='plain':require(e['requested_timeout_ms']<=200,'shared extra request cap')
                if q['origin']=='projection':require(e['requested_timeout_ms']<=200,'projection timeout');require(not e.get('global_target_proof',False),'local projection not global proof')
            ws={w['id']:w for w in row['writes']};require(len(ws)==len(cb[i]),'all SAT assessments written')
            require(len(cb[i])==sum(q['evidence']['raw_status']=='sat' for q in qs),'all raw SAT models saved')
            local=[]
            for c in cb[i]:
                require(c['version']==version,'candidate version');w=ws[c['id']]
                require(c['validated']<=w['completion'],'actual checkpoint completion')
                require(not w['timely'] or w['completion']<=min(w['deadline'],r['seconds']),'late checkpoint marked timely')
                require(any(q['origin']==c['origin'] and q['evidence']['raw_status']=='sat' for q in qs),'candidate query association')
                new=metric.apply(state,c['model']);nvalidated+=1;gain=metric.count(new)-metric.count(state);obs=observed(new)-observed(state);key=model_key(c['model'])
                require((gain,obs)==(c['gain'],c['observations']),'all candidate full coverage differential')
                require(c['formal_duplicate']==(key in seen),'candidate formal dedup')
                fresh=key not in seen and key not in supply;require(c['internal_duplicate']==(key not in seen and not fresh),'internal duplicate classification');require(c['new_to_supply']==fresh,'unique candidate accounting');supply.add(key)
                unique_supply+=fresh and c['validated']<=r['seconds']
                a=dict(c,timely=timely(w,r['seconds']),completion=w['completion'],model_hash=model_id(c['model']))
                local.append(a);assessed.append({k:v for k,v in a.items() if k!='model'})
            plain=next((c for c in local if c['origin']=='plain' and c['timely'] and not c['formal_duplicate']),None)
            opportunities+=1
            if plain:zeros=0 if plain['gain'] else zeros+1
            reason='initial_complement' if first and plain else 'four_zero_candidates' if plain and zeros>=4 and opportunities>=4 else 'none'
            require(row['trigger']==reason,'fixed trigger')
            if reason!='none' and (r['mode']=='plain_only' or row['extra_attempted']):first=False;opportunities=0
            require(row['zero_plain_candidates']==zeros and row['ordinary_opportunities_since_extra']==opportunities,'trigger ledger')
            require(bool(eb[i])==row['extra_attempted'],'extra phase journal')
            if r['mode']=='plain_only':require(not row['extra_attempted'] and len(qs)==1,'plain-only no extra')
            if eb[i]:
                e=eb[i][0];require(reason!='none' and e['version']==version,'extra trigger/version')
                require(.04-1e-9<=e['allowance']<=.25+1e-9,'stage cap')
                require(e['allowance']<=.1*r['seconds']-spent-.005+1e-6,'total extra grant')
                require(e['started']+e['allowance']+.05<=r['seconds']+1e-5,'formal delivery reserve')
                if row['extra_seconds']>e['allowance']:stage_overruns.append(dict(cycle=i,actual=row['extra_seconds'],allowed=e['allowance']))
                selection=e['selection'];condition=e['condition']
                if selection:
                    require(len(selection['variables'])<=3 and len(selection['assessed'])<=32,'bounded variable selection')
                    dep_ops+=selection['dependency_operations'];require(dep_ops<=200000,'dependency work bound')
                    if selection['complete']:
                        require(selection['missing_bits']==sum((int(a)^int(b)).bit_count() for a,b,_ in state.values()),'single-sided gap')
                        require(selection['unobserved_nodes']==sum(not(int(a)|int(b)) for a,b,_ in state.values()),'unobserved separate')
                    for s in selection['assessed']:
                        require(names[s['variable']]==s['name'] and s['related_nodes']<=128,'variable mapping/dependency cap')
                        stable=sum(values(history[j-1])[s['name']]==values(history[j])[s['name']] for j in range(1,len(history)))
                        require(stable==s['stable_transitions'] and s['score']==s['gap_weight']*(1+stable),'stable multiplier')
                    require(set(selection['variables'])<=set(s['variable'] for s in selection['assessed'][:8] if s['score']>0),'top positive pool')
                if condition:
                    require(condition['blocks_before']==len(projection_seen),'persistent block count before')
                    for c in local:
                        if c['origin']=='projection':
                            key=model_key(c['model']);require(key not in projection_seen,'projection full model repeated despite block');projection_seen.add(key)
                    require(condition['blocks_after']==len(projection_seen)<=256,'persistent block count after')
                    require(condition['block_added']==any(c['origin']=='projection' for c in local),'every legal SAT persistently blocked')
                    require(condition['block_seconds']>=0 and not condition['internal_duplicate'],'blocking cost/repeat guard')
                    require(r['mode']=='projection_fixed','projection mode');vn=[v['name'] for v in condition['variables']]
                    require(0<len(vn)<=3 and len(set(vn))==len(vn),'projection bounds')
                    require([v['index'] for v in condition['variables']]==selection['variables'],'selected variables used')
                    expected={tuple(values(h)[n] for n in vn) for h in history}
                    actual={tuple(map(int,h)) for h in condition['forbidden']}
                    require(actual==expected and len(actual)==len(condition['forbidden'])<=4,'exact recent deduplicated projections')
                    for c in local:
                        if c['origin']=='projection':require(tuple(values(c['model'])[n] for n in vn) not in actual,'projection condition')
                if r['mode']=='plain_extra':require(selection is None and condition is None,'no forced solver in control')
            spent+=row['extra_seconds'];require(math.isclose(spent,row['extra_spent'],abs_tol=1e-8),'all extra phase costs')
            best=best_candidate(local);require(len(mb[i])<=1,'single formal winner');committed=False
            for m in mb[i]:
                require(best is not None and model_key(best['model'])==model_key(m['model']) and best['origin']==m['origin'],'best timely candidate retained')
                require((m['gain'],m['observations'])==(best['gain'],best['observations']),'winner score')
                w=receipts[m['id']];require(not w['timely'] or w['completion']<=w['deadline']==r['seconds'],'formal deadline')
                if not timely(w,r['seconds']):continue
                require(w['completion']>=max(last,best['completion']),'chronological delivery');key=model_key(m['model']);require(key not in seen,'formal dedup')
                new=metric.apply(state,m['model']);nvalidated+=1;require(metric.count(new)-metric.count(state)==m['gain'],'formal marginal')
                state=new;seen.add(key);history=(history+[m['model']])[-4:];version+=1;last=w['completion'];committed=True
                curve.append(dict(index=len(curve)+1,cycle=i,seconds=last,gain=m['gain'],covered=metric.count(state),origin=m['origin'],model_hash=model_id(m['model']),ordinary_gain=plain['gain'] if plain else None,net_replacement_gain=m['gain']-plain['gain'] if plain else None))
            require(committed==row['committed'] and version==row['version_after'],'commit semantics')
            require(row['covered_after']==metric.count(state),'cycle coverage')
        require(summary['projection_blocks']==len(projection_seen),'final no-good count')
        if len(projection_seen)==256:require(summary['projection_stop_reason']=='projection_block_cap','no block reset')
        require(len(cycles)==summary['cycles'] and len(models)==len(commits),'journal completeness')
        require(metric.export(state)==summary['coverage'],'final seen0/seen1 differential')
        require(sum(x['gain'] for x in curve)==summary['gain']==metric.count(state)-basecovered,'gain telescope')
        require(len(curve)==summary['formal_count'],'formal count')
        require(unique_supply==summary['unique_validated_candidates'],'supply count')
        require(math.isclose(spent+summary['guided_cleanup_seconds'],summary['extra_spent'],abs_tol=1e-8),'cleanup charged')
        require(summary['extra_overrun']==(summary['extra_spent']>summary['extra_quota']),'quota overrun visible')
        require(summary['plain_calls']==len(cycles) and summary['extra_calls']==len(queries)-len(cycles),'call counts')
        return dict(id=r['id'],instance=r['instance'],state=r['state'],seed=r['seed'],mode=r['mode'],kind=r['kind'],seconds=r['seconds'],process_seconds=r.get('process_seconds'),status='passed',validated_models=nvalidated,base_covered=basecovered,total=metric.total,gain=summary['gain'],curve=curve,candidates=assessed,summary=summary,stage_overruns=stage_overruns)

def main():
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('--jobs',type=int,default=6);a=p.parse_args()
    plan=json.loads((a.input/'plan.json').read_text());records=lines(a.input/'runs.jsonl');done=json.loads((a.input/'COMPLETE.json').read_text())
    require(file_hash(a.input/'plan.json')==done['plan_sha256'] and file_hash(a.input/'runs.jsonl')==done['journal_sha256'],'batch binding')
    require(len(records)==len(plan['tasks']),'missing planned tasks')
    for path,sha in plan['hashes'].items():require(file_hash(path)==sha,'changed source: '+path)
    out=a.input/'audit';out.mkdir(exist_ok=False);rows=[]
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:
        for result in pool.map(audit_job,[(str(a.input),r,plan['source_sha256']) for r in records]):
            dump(out/(result['id'].replace('/','__')+'.json'),result)
            rows.append(dict(id=result['id'],status=result['status'],validated_models=result['validated_models'],stage_overruns=len(result['stage_overruns']),quota_overrun=result['summary']['extra_overrun']))
    write_csv(a.input/'audit_summary.csv',rows)
    dump(a.input/'AUDIT.json',dict(sessions=len(rows),all_passed=True,validated_models=sum(x['validated_models'] for x in rows),stage_overruns=sum(x['stage_overruns'] for x in rows),quota_overruns=sum(x['quota_overrun'] for x in rows),search_calls=0))
if __name__=='__main__':main()
