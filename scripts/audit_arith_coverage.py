#!/usr/bin/env python3
"""Unmodified frozen manual evaluator, no solving; full trajectories + bounded probe pools."""
import argparse,json,time,csv
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from audit_native_delivery_results import one_run
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,require,dump,write_csv,model_id,file_hash
from prepare_highdiv_complementarity_gap import layout_for,pack_state
from highdiv_gap_common import observed
from audit_position_frozen_900s import resource_fields

def read(p):return [json.loads(l) for l in p.read_text().splitlines()]
def fnv_model(m):
    h=14695981039346656037
    for x in m:
        value=str(int(x['value'])) if isinstance(x['value'],bool) else x['value']
        for c in (value+';').encode():h=((h^c)*1099511628211)&((1<<64)-1)
    return format(h,'x')
def extra(task):
    out,name=task;out=Path(out);d=out/'runs'/name;raw=d/'raw';dest=out/'audit'/name
    spec=json.loads((d/'spec.json').read_text());initial=json.loads((raw/'initial.json').read_text());summary=json.loads((raw/'summary.json').read_text())
    common={k:spec[k] for k in ['formula','seed','configuration','group']};common['run']=name
    require(initial['position_policy']=='random_supported_rank','outer position changed')
    process=json.loads((d/'process_result.json').read_text());ready=json.loads((d/'ready.json').read_text())
    require(process['returncode']==0 and not process['hard_kill'] and not process['resource_stop'],'process failed; no ranking')
    require(ready['affinity']==[spec['core']],'affinity mismatch');thread_samples=0;library_samples=0;plan=json.loads((out/'plan.json').read_text())
    for sample in read(d/'resource_samples.jsonl'):
        for p in sample['processes']:
            if (p.get('Name') or '').startswith('arith_coverage'):
                require(int(p['Threads'])<=2,'unexpected solver threads')
                if p.get('loaded_z3_paths'):require(p['loaded_z3_paths']==[plan['expected_loaded_z3_path']],'wrong actual loaded Z3');library_samples+=1
                for t in p.get('thread_affinity',[]):require(t['affinity']==str(spec['core']),'helper escaped core');thread_samples+=1
    require(thread_samples>0 and library_samples>0,'missing live thread/library evidence')
    qs=read(raw/'queries.jsonl');cost_rows=read(raw/'costs.jsonl');ars=read(raw/'arithmetic.jsonl');ds=read(raw/'deliveries.jsonl');dc=read(raw/'delivery_commits.jsonl');cs=read(raw/'candidates.jsonl');gc=read(raw/'generation_commits.jsonl')
    formal=[r for r,c in zip(ds,dc) if c['timely']];formal_by_query={r['query']:r for r in formal};generations={c['query']:r for r,c in zip(cs,gc) if c['timely']};final={r['query']:r for r in cs}
    rows=[];pools=[];counts=Counter();prefixes=[];all_prefixes=[set()];seen=set();previous=0
    with no_search() as searches:
        metric=FrozenCoverage((out/'inputs'/spec['formula']).read_text());layout=layout_for(metric);state=metric.empty.copy();prefixes=[state]
        for r in formal:state=metric.apply(state,r['model']);prefixes.append(state)
        for q in qs:
            if q['query'] in generations:seen=seen|{model_id(generations[q['query']]['model'])}
            all_prefixes.append(seen)
        for r in ars:
            qid=r['query_id'];q=qs[qid];b=r['backend'];cv=r['coverage_version_before'];require(cv>0 and cv<=len(formal),'arith before first/formal version');require(cv==sum(1 for x in formal if x['query']<qid),'stale formal coverage')
            require(not r['implementation_failure'] and not b.get('implementation_failure',False),'arithmetic implementation failure')
            require(b.get('columns_scanned',0)<=8 and b.get('lp_nonzeros_read',0)<=8192 and b.get('pool_distinct',0)<=16,'limits exceeded')
            require(not b.get('committed_move') or b['pool_complete'],'partial pool committed')
            require(spec['configuration']!='arith_probe' or not b.get('committed_move'),'probe changed live state')
            require(spec['configuration']!='arith_coverage' or not b.get('committed_move') or r['score_complete'],'partial score committed')
            require(r['mechanism_cumulative_time']>=previous,'lost cost');previous=r['mechanism_cumulative_time']
            if b:require(b['query_epoch']==qid and b['coverage_version']==cv and b['soft_limit_seconds']<=.002+1e-9,'epoch/allowance')
            actual=formal_by_query.get(qid);require(r['formally_delivered']==bool(actual) and r['actual_delta_covered']==(actual['gain'] if actual else 0) and r['actual_delta_observed']==(actual['observations'] if actual else 0),'arithmetic actual gain is not formal marginal')
            generated=final.get(qid);require(r['final_model_produced']==bool(generated),'missing final model association')
            if generated:require(r['final_assignment_hash']==fnv_model(generated['model']),'final identity')
            if spec['configuration']=='arith_probe' and r['score_complete']:
                require(b['pool_complete'] and len(r['probe_models'])==b['pool_distinct']>0,'complete probe missing models');state=prefixes[cv];before=pack_state(metric,layout,state);scores=[];ids=[]
                for i,m in enumerate(r['probe_models']):
                    identity=model_id(m);require(identity not in ids and identity not in all_prefixes[qid],'duplicate or prior blocker');ids.append(identity)
                    after=metric.apply(state,m);new=pack_state(metric,layout,after);score=(metric.count(after)-metric.count(state),observed(new)-observed(before));scores.append(score)
                    require(list(score)==r['pool_scores'][i],'pure scoring differs from frozen evaluator');require(fnv_model(m)==b['proposals'][i]['identity_fnv'],'proposal identity')
                require(fnv_model(r['probe_models'][0])==r['original_assignment_hash'],'noop identity')
                best=max(scores);sel=r['offered_candidate'];require(scores[sel]==best and (scores[0]!=best or sel==0),'not lexicographic maximum/noop tie')
                expected=sum(v[0] for v in scores)/len(scores);require(abs(expected-r['random_expected_covered'])<1e-5,'random expectation')
                pools.append(common|dict(query=qid,pool_size=len(scores),coverage_version=cv,noop_covered=scores[0][0],noop_observed=scores[0][1],best_covered=best[0],best_observed=best[1],random_expected_covered=expected,strictly_better_than_noop=best>scores[0],covered_above_random=best[0]>expected,verified=True,pool_fingerprint=b['pool_fingerprint']))
            counts['queries']+=1;counts['hook_queries']+=bool(b.get('engine_seen'));counts['complete_pools']+=bool(b.get('pool_complete'));counts['scored_pools']+=r['score_complete'];counts['committed']+=bool(b.get('committed_move'));counts['soft_overruns']+=r['action_soft_overrun'];counts['reason_'+b.get('reason',r['reason'])]+=1
            row=common|{k:v for k,v in r.items() if k not in ['backend','pool_scores','probe_models']};row.update({k:v for k,v in b.items() if k not in ['proposals','support_reasons']});row.update(diagnostic_time=cost_rows[qid].get('arith_diagnostic_seconds'),immediate_restore_time=0,raw_status=q['evidence']['raw_status'],reason_unknown=q['evidence'].get('raw_reason'),requested_ms=q['evidence']['requested_timeout_ms'],granted_ms=q['evidence']['granted_timeout_ms'],query_start=q['check_call_start'],query_end=q['check_call_finish']);rows.append(row)
        require(not searches,'auditor searched')
    am=summary.get('arithmetic');require(am is None or am['mechanism_seconds']>=previous,'final cost missing')
    first=formal[0] if formal else None;before=[q for q in qs if q['generation_version_before']==0]
    init=common|{k:initial[k] for k in ['source_sha256','z3_version','total_bits','formula_nodes','original_variables','solver_kind','solver_random_seed','action_rng_seed','position_rng_seed','formula_load_seconds','solver_initialization_seconds']}
    init.update(first_model_id=model_id(first['model']) if first else None,first_query=first['query'] if first else None,prefirst_status_grants=json.dumps([(q['evidence']['raw_status'],q['evidence']['granted_timeout_ms']) for q in before]),first_sat_seconds=summary['first_sat_seconds'],first_arith_prepare=am['first_prepare_at'] if am else None,first_arith_install=am['first_install_at'] if am else None)
    if first and am:require(am['first_prepare_at']>=next(c['delivery_complete_time'] for c in dc if c['timely']),'arith prepared before first delivery')
    costs=common|dict(counts)|dict(arith_seconds=am['mechanism_seconds'] if am else 0,phase_seconds=summary['mechanism_seconds'],arith_stop=am['stop_reason'] if am else '',arith_stop_at=am['stop_at'] if am else None,arith_stop_query=am['stop_query'] if am else None,phase_stop=summary['mechanism_stop'],quota=.05*spec['seconds'],diagnostic_seconds=summary['stats']['seconds'].get('arith_diagnostic_journal',0),process_wall_seconds=process['process_wall_seconds'],resource=json.dumps(resource_fields((d/'resource.txt').read_text())))
    dump(dest/'arithmetic_audit.json',dict(passed=True,queries=len(rows),verified_pools=len(pools),search_calls=0));return rows,pools,init,costs

def table(path,rows):
    keys=sorted(set().union(*(r.keys() for r in rows))) if rows else []
    write_csv(path,[{k:r.get(k) for k in keys} for r in rows])
def main():
    a=argparse.ArgumentParser();a.add_argument('out',type=Path);a.add_argument('--jobs',type=int,default=6);v=a.parse_args();out=v.out.resolve();plan=json.loads((out/'plan.json').read_text());names=[p.name for p in sorted((out/'runs').iterdir()) if p.is_dir()];require(len(names)==plan['total_sessions'],'missing run')
    require(json.loads((out/'sampling_complete.json').read_text())['passed'],'audit must follow completed sampling')
    for batch in sorted({j['batch'] for j in plan['jobs']}):
        jobs=[j for j in plan['jobs'] if j['batch']==batch];require(len(jobs)<=6 and len({j['core'] for j in jobs})==len(jobs),'core overlap')
    with ProcessPoolExecutor(max_workers=v.jobs) as pool:results=list(pool.map(one_run,[(str(out),n) for n in names]));extras=list(pool.map(extra,[(str(out),n) for n in names]))
    for filename,i in [('mechanism_queries.csv',0),('probe_pools.csv',1),('initialization.csv',2),('mechanism_costs.csv',3)]:table(out/filename,[r for group in extras for r in (group[i] if i<2 else [group[i]])])
    dump(out/'audit_summary.json',dict(passed=True,runs=results,search_calls=0,plan_sha256=file_hash(out/'plan.json')));print(json.dumps(dict(passed=True,runs=len(results),probe_pools=sum(len(x[1]) for x in extras))))
if __name__=='__main__':main()
