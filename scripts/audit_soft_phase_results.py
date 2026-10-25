#!/usr/bin/env python3
"""Independent original evaluator replay plus conditional summary/phase audit.
No SMT searches. The ordinary audit checks every full coverage fingerprint.
"""
import argparse,json,time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from audit_bofill_results import audit_run,jsonlines
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,require,dump,write_csv,model_id
import z3

def score(obs,state,nodes):
    covered=observed=0
    for n,zero,one in obs:
        a,b,_=state[nodes[n].get_id()];zero=int(zero);one=int(one)
        covered+=(zero&int(a)&~int(b)).bit_count()+(one&int(b)&~int(a)).bit_count()
        observed+=(zero&~int(b)).bit_count()+(one&~int(a)).bit_count()
    return covered,observed

def atom_value(node,values):
    if z3.is_int_value(node):return node.as_long()
    if z3.is_true(node):return True
    if z3.is_false(node):return False
    if z3.is_const(node):return values[str(node.decl().name())]
    if z3.is_app_of(node,z3.Z3_OP_UMINUS):return -atom_value(node.arg(0),values)
    if z3.is_eq(node):return atom_value(node.arg(0),values)==atom_value(node.arg(1),values)
    raise ValueError('unexpected supported discriminator')

def one_run(arg):
    baseline=audit_run(arg);out,name=arg;out=Path(out);raw=out/'runs'/name/'raw';dest=out/'audit'/name
    queries,_=jsonlines(raw/'queries.jsonl');models,_=jsonlines(raw/'models.jsonl');commits,_=jsonlines(raw/'commits.jsonl')
    candidates, candidate_issues=jsonlines(raw/'candidates.jsonl')
    models={r['query']:r for r in models};commits={r['query']:r for r in commits}
    catalog=json.loads((raw/'summaries.json').read_text()) if (raw/'summaries.json').exists() else {'atoms':[]}
    rows=[];phases=[];candidate_audit=[];lower_checks=0;subset_checks=0;cov_version=0
    started=time.perf_counter()
    with no_search() as attempts:
        metric=FrozenCoverage((out/'inputs'/baseline['formula']).read_text());state=metric.empty.copy()
        for saved in candidates:
            require(saved['valid'],'saved SAT candidate invalid')
            metric.apply(metric.empty,saved['model'])
            candidate_audit.append(dict(query=saved['query'],model_sha256=model_id(saved['model']),coverage_version=saved['coverage_version'],validated_seconds=saved['validated_seconds'],has_formal_record=saved['query'] in models,has_query_record=any(q['query']==saved['query'] for q in queries)))
            if saved['query'] in models:require(saved['model']==models[saved['query']]['model'],'SAT/formal assignment mismatch')
        require(len({r['query'] for r in candidates})==len(candidates),'duplicate SAT candidate query')
        candidate_by_query={r['query']:r for r in candidates}
        for query in queries:
            if query['evidence']['raw_status']=='sat':require(query['query'] in candidate_by_query,'completed SAT query has no full assignment')
        atoms={a['node']:a for a in catalog['atoms']}
        for q in queries:
            require(q['coverage_version']==cov_version,'query coverage version stale')
            prefs=q['preferences'];hook=q['hook'];applied=[e for e in hook.get('events',[]) if e['applied']]
            require(len(applied)==hook.get('applied',0),'lost actual override events')
            require(len(applied)<=8,'override cap exceeded')
            require(len({e['group'] for e in applied})==len(applied),'group used twice')
            require(len({e['atom_id'] for e in applied})==len(applied),'atom used twice')
            for e in applied:
                require(e['conflicts']<64 and e['preferred_truth']!=e['native_truth'],'nonordinary/capped intervention')
                require(any(p['ast_id']==e['atom_id'] and p['group']==e['group'] and p['truth']==e['preferred_truth'] for p in prefs),'actual event/preference identity mismatch')
            for p in prefs:
                require(p['coverage_version']==cov_version,'preference state differs')
                a=atoms[p['node']]
                for truth,tag in ((False,'zero'),(True,'one')):
                    expected=score(a['sides'][truth]['observations'],state,metric.nodes)
                    require(expected==(p[tag+'_covered'],p[tag+'_observed']),'saved feedback score differs')
                require(p['lower_bound']==p[('one' if p['truth'] else 'zero')+'_covered'],'lower bound label')
                if baseline['configuration']=='coverage_soft':
                    x=(p['zero_covered'],p['zero_observed']);y=(p['one_covered'],p['one_observed'])
                    require(x!=y and p['truth']==(y>x),'coverage preference selection')
            m=candidate_by_query.get(q['query']); realized=0
            if m:
                single=metric.apply(metric.empty,m['model']);trial=metric.apply(state,m['model']);gain=metric.count(trial)-metric.count(state)
                values={v['name']:int(v['value']) if v['sort']=='Int' else v['value'] for v in m['model']}
                for a in catalog['atoms']:
                    truth=bool(atom_value(metric.nodes[a['node']],values));summary=a['sides'][truth]
                    for n,z,o in summary['observations']:
                        actual_one,actual_zero,_=single[metric.nodes[n].get_id()]
                        require(not(int(o)&~int(actual_one)) and not(int(z)&~int(actual_zero)),'guaranteed observation not visited/value wrong')
                    subset_checks+=1
                    require(gain>=score(summary['observations'],state,metric.nodes)[0],'conditional lower bound violated');lower_checks+=1
                for p in prefs:
                    truth=bool(atom_value(metric.nodes[p['node']],values));hit=truth==p['truth'];realized+=hit
                    event=next((e for e in applied if e['atom_id']==p['ast_id']),None)
                    phases.append(dict(query=q['query'],node=p['node'],group=p['group'],coverage_version=cov_version,preferred_truth=p['truth'],actual_truth=truth,actual_override=event is not None,realized=hit,lower_bound=p['lower_bound'],model_gain=gain,warning='shared outcome; not additive attribution'))
                c=commits.get(q['query'])
                if c and c['timely'] and c['completion']<=baseline['seconds']:
                    require(q['emitted'],'query/receipt disagree');state=trial;cov_version+=1
            rows.append(dict(query=q['query'],coverage_version=q['coverage_version'],raw_status=q['evidence']['raw_status'],emitted=q['emitted'],proposed=len(prefs),mapped=sum(x['mapped'] for x in hook.get('mapping',[])),unsupported=sum(not x['supported'] for x in hook.get('mapping',[])),unmapped=sum(x['supported'] and not x['mapped'] for x in hook.get('mapping',[])),negated=sum(x['negated'] for x in hook.get('mapping',[])),opportunities=hook.get('opportunities',0),same_native=hook.get('same_native',0),applied=hook.get('applied',0),already_used=hook.get('already_used',0),override_cap=hook.get('override_cap',0),conflict_cap=hook.get('conflict_cap',0),special_interface=hook.get('special_interface',0),already_assigned=hook.get('already_assigned',0),lookup_seconds=hook.get('lookup_seconds',0),realized_proposals=realized,check_seconds=q['evidence'].get('actual_check_seconds'),grant_ms=q['evidence'].get('granted_timeout_ms')))
        require(cov_version==baseline['distinct'],'missing query/receipt association')
        require(not attempts,'audit searched')
    write_csv(dest/'candidate_audit.csv',candidate_audit);write_csv(dest/'phase_queries.csv',rows);write_csv(dest/'phase_realization.csv',phases)
    keys=['proposed','mapped','unsupported','unmapped','negated','opportunities','same_native','applied','already_used','override_cap','conflict_cap','special_interface','already_assigned','lookup_seconds','realized_proposals']
    result=dict(baseline,saved_sat_candidates=len(candidates),candidate_journal_issues=candidate_issues,summary_catalog={k:v for k,v in catalog.items() if k!='atoms'},supported_atoms=len(catalog['atoms']),phase_counters={k:sum(r[k] for r in rows) for k in keys},subset_checks=subset_checks,lower_bound_checks=lower_checks,applied_realized=sum(r['actual_override'] and r['realized'] for r in phases),applied_not_realized=sum(r['actual_override'] and not r['realized'] for r in phases),conditional_audit_seconds=time.perf_counter()-started)
    dump(dest/'soft_summary.json',result);return result

def main():
    a=argparse.ArgumentParser();a.add_argument('out');a.add_argument('--jobs',type=int,default=3);v=a.parse_args();out=Path(v.out).resolve()
    if (out/'archive_manifest.json').exists():
        from archive_bofill_results import restore
        restore(out)
    args=[(str(out),p.name) for p in sorted((out/'runs').iterdir()) if p.is_dir()]
    with ProcessPoolExecutor(max_workers=v.jobs) as pool:rows=list(pool.map(one_run,args))
    dump(out/'audit_summary.json',dict(passed=True,runs=rows,method='frozen ManualSatisfiesMetric; complete states, conditional subsets, per-phase lower bounds; no solver search'))
    print(f'Passed independent audit: {len(rows)} sessions, {sum(r["distinct"] for r in rows)} models',flush=True)
if __name__=='__main__':main()
