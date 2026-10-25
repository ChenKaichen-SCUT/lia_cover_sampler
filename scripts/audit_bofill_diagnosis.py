#!/usr/bin/env python3
"""Saved-assignment and structural diagnostics only; every solver search API disabled."""
import argparse, json, time, collections
from pathlib import Path
import z3
from reevaluate_round6_saved_candidates import FrozenCoverage, no_search, file_hash, dump, write_csv, model_id
ROOT=Path(__file__).resolve().parents[1]

def nodes(roots):
    seen={};todo=list(roots)
    while todo:
        e=todo.pop()
        if e.get_id() in seen:continue
        seen[e.get_id()]=e;todo.extend(e.children())
    return seen

def flatten(e):
    if z3.is_and(e):return [a for c in e.children() for a in flatten(c)]
    return [e]

def variables(e):
    return {str(v):v for v in nodes([e]).values() if z3.is_const(v) and v.decl().kind()==z3.Z3_OP_UNINTERPRETED}

def affine(e,cache):
    if e.get_id() in cache:return cache[e.get_id()]
    result=None;k=e.decl().kind()
    if z3.is_int_value(e):result=({},e.as_long())
    elif z3.is_const(e) and e.sort().kind()==z3.Z3_INT_SORT:result=({str(e):1},0)
    elif k in (z3.Z3_OP_ADD,z3.Z3_OP_SUB,z3.Z3_OP_UMINUS,z3.Z3_OP_MUL):
        xs=[affine(a,cache) for a in e.children()]
        if all(x is not None for x in xs):
            if k==z3.Z3_OP_MUL:
                nonconst=[x for x in xs if x[0]];factor=1
                for ts,c in xs:
                    if not ts:factor*=c
                if len(nonconst)<=1:
                    ts,c=nonconst[0] if nonconst else ({},1);result=({v:a*factor for v,a in ts.items() if a*factor},c*factor)
            else:
                ts=collections.Counter();constant=0
                for i,(t,c) in enumerate(xs):
                    sign=-1 if k==z3.Z3_OP_UMINUS or (k==z3.Z3_OP_SUB and i) else 1
                    for v,a in t.items():ts[v]+=sign*a
                    constant+=sign*c
                result=({v:a for v,a in ts.items() if a},constant)
    cache[e.get_id()]=result;return result

def structure(path,original,recovery):
    began=time.perf_counter();parsed=z3.parse_smt2_file(str(path),ctx=original.ctx);root=z3.And(*parsed);ns=nodes([root]);origvars=variables(original);vs=variables(root)
    allvars={**origvars,**vs};decls={v.decl().name():v for v in allvars.values()}
    rec=[]
    for i,r in enumerate(recovery):
        # Parse as equality to its original variable: the same original declaration is reused.
        rec.extend(z3.parse_smt2_string('(assert (= '+r['original']+' '+r['expression']+'))',decls=decls,ctx=original.ctx)[0].children()[1:])
    refs=collections.Counter();seen=set();todo=flatten(root)+rec
    while todo:
        e=todo.pop()
        if z3.is_const(e) and e.decl().kind()==z3.Z3_OP_UNINTERPRETED:refs[str(e)]+=1
        if e.get_id() in seen:continue
        seen.add(e.get_id());todo.extend(e.children())
    kinds=collections.Counter(e.decl().kind() for e in ns.values());bits=collections.Counter(abs(e.as_long()).bit_length() or 1 for e in ns.values() if z3.is_int_value(e))
    clauses=flatten(root);graph=[];candidate=collections.Counter();cache={};eligible=[]
    for i,e in enumerate(clauses):
        if not z3.is_eq(e) or e.arg(0).sort().kind()!=z3.Z3_INT_SORT:continue
        a,b=affine(e.arg(0),cache),affine(e.arg(1),cache)
        if a is None or b is None:candidate['unsupported_linear']+=1;continue
        row=dict(a[0])
        for v,c in b[0].items():row[v]=row.get(v,0)-c
        row={v:c for v,c in row.items() if c};graph.append(dict(clause=i,variables=sorted(row),coefficients={v:str(c) for v,c in sorted(row.items())}))
        if not row:candidate['constant_row']+=1;continue
        if len(row)>4:candidate['row_width']+=1;continue
        for v,c in row.items():
            if abs(c)!=1:candidate['non_unit_coefficient']+=1;continue
            if v not in origvars:candidate['not_retained_original']+=1;continue
            width=len(row)-1;growth=max(0,width-1)*refs[v]
            if refs[v]>8:candidate['reference_limit']+=1;continue
            if growth>8:candidate['growth_limit']+=1;continue
            candidate['eligible_pivot_occurrences']+=1;eligible.append(dict(variable=v,clause=i,width=width,references=refs[v],estimated_growth=growth))
    adjacency={v:set() for row in graph for v in row['variables']}
    for row in graph:
        for v in row['variables']:adjacency[v].update(set(row['variables'])-{v})
    components=[];pending=set(adjacency)
    while pending:
        todo=[pending.pop()];part=set(todo)
        while todo:
            v=todo.pop();fresh=adjacency[v]&pending;pending-=fresh;part|=fresh;todo.extend(fresh)
        components.append(sorted(part))
    depcache={}
    def deps(e):
        if e.get_id() not in depcache:
            if z3.is_const(e) and e.decl().kind()==z3.Z3_OP_UNINTERPRETED:depcache[e.get_id()]={str(e)}
            else:depcache[e.get_id()]=set().union(*(deps(a) for a in e.children()))
        return depcache[e.get_id()]
    aux=sorted(set(vs)-set(origvars));dependencies={e.get_id():deps(e) for e in ns.values() if e.sort().kind()==z3.Z3_INT_SORT and e.num_args()}
    clause_deps=[deps(e) for e in clauses]
    coupling=[]
    for v in aux:
        coupled=[i for i,dep in enumerate(clause_deps) if v in dep]
        coupling.append(dict(variable=v,arithmetic_expression_count=sum(v in dep for dep in dependencies.values()),clauses=coupled,co_variables=sorted(set().union(*(clause_deps[i] for i in coupled))-{v})))
    return dict(seconds=time.perf_counter()-began,dag_nodes=len(ns),original_variables=len(origvars),formula_variables=len(vs),auxiliary_variables=aux,or_count=kinds[z3.Z3_OP_OR],and_count=kinds[z3.Z3_OP_AND],equalities=kinds[z3.Z3_OP_EQ],inequalities=sum(kinds[k] for k in [z3.Z3_OP_LE,z3.Z3_OP_GE,z3.Z3_OP_LT,z3.Z3_OP_GT]),constant_times_expression=sum(e.decl().kind()==z3.Z3_OP_MUL and any(z3.is_int_value(a) for a in e.children()) for e in ns.values()),coefficient_bit_histogram=dict(bits),necessary_equality_graph=graph,components=components,auxiliary_coupling=coupling,remaining_pivot_reasons=dict(candidate),remaining_eligible_pivots=eligible,recovery_dag_nodes=len(nodes(rec)))

def audit(out):
    out=Path(out);plan=json.loads((out/'plan.json').read_text());rows=[]
    with no_search() as search:
        for run in plan['runs']:
            d=out/run['directory'];raw=d/'raw';summary=json.loads((raw/'summary.json').read_text());formula=out/'inputs'/run['formula']
            assert file_hash(formula)==run['input_sha256'];metric=FrozenCoverage(formula.read_text());s=metric.empty.copy();accepted=False
            if (raw/'candidate.json').exists():
                c=json.loads((raw/'candidate.json').read_text());r=json.loads((raw/'receipt.json').read_text());assert r['query']==c['query'];s=metric.apply(s,c['model']);assert c['validated_seconds']<=r['completion']
                accepted=r['timely'] and r['completion']<=run['seconds'];assert accepted==(summary['terminal']=='SAT')
                identity=model_id(c['model'])
            else:identity=None;assert summary['terminal']!='SAT'
            dump(d/'audit.json',dict(valid_saved_model=identity is not None,accepted=accepted,model_sha256=identity,coverage=metric.export(s) if accepted else metric.export(metric.empty.copy()),search_calls=0))
            rows.append(dict(**run,terminal=summary['terminal'],accepted=accepted,model_sha256=identity,covered=metric.count(s) if accepted else 0))
        assert not search
    write_csv(out/'audit.csv',rows);dump(out/'audit_summary.json',dict(runs=len(rows),sat=sum(r['accepted'] for r in rows),search_calls=0,all_passed=True))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('out');a=p.parse_args();audit(a.out)
