#!/usr/bin/env python3
"""Independent one-parameter SMT checks of scoped diagnostic claims; timeout remains unknown."""
import argparse,json,sys,time
from pathlib import Path
from collections import Counter,defaultdict
import z3
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from validation import nodes_preorder,variables,decode_assignment

def reach_expressions(root):
    nodes=nodes_preorder(root);parents=defaultdict(list)
    for i,node in enumerate(nodes):
        for j,child in enumerate(node.children()):parents[child.get_id()].append((i,j))
    cache={0:z3.BoolVal(True)};indices={n.get_id():i for i,n in enumerate(nodes)}
    def reach(i):
        if i in cache:return cache[i]
        terms=[]
        for p,position in parents[nodes[i].get_id()]:
            parent=nodes[p];children=parent.children();conditions=[reach(p)]
            if z3.is_and(parent):conditions+=children[:position]
            if z3.is_or(parent):conditions += [z3.Not(c) for c in children[:position]]
            if z3.is_app_of(parent,z3.Z3_OP_ITE) and position:conditions.append(children[0] if position==1 else z3.Not(children[0]))
            terms.append(z3.And(conditions))
        cache[i]=z3.Or(terms);return cache[i]
    return nodes,reach

def check_record(text,context,event,prefix=(),timeout=1000):
    start=time.monotonic();solver=z3.Solver();solver.set(timeout=timeout)
    code=event['code'];expected='unsat';details={}
    if code=='NO_DIRECTION_PROVED':solver.add(z3.parse_smt2_string(event['proof_query']))
    else:
        original=z3.And(z3.parse_smt2_string(text));nodes,reach=reach_expressions(original)
        decls=variables(original);base=decode_assignment(context['base'],decls)
        scope=z3.And(z3.parse_smt2_string(context['scope_query']))
        parameter=z3.FreshInt('diagnostic_parameter');mapping=[]
        for i,e in enumerate(decls):
            value=base[str(e.decl().name()),str(e.sort())];delta=int(context['direction'][i])
            if z3.is_bool(e):
                if delta:raise ValueError('Boolean direction must be zero')
                replacement=z3.BoolVal(value)
            else:replacement=z3.IntVal(value)+parameter*delta
            mapping.append((e,replacement))
        solver.add(z3.substitute(scope,*mapping),parameter!=0)
        if code in ('REACH_EMPTY','BIT_VALUE_UNREACHABLE') or code.startswith('DUPLICATE_'):
            node=nodes[event['target_node']]
            if context['module']=='line' or code=='REACH_EMPTY':solver.add(z3.substitute(reach(event['target_node']),*mapping))
            if code!='REACH_EMPTY':
                desired=event['target_desired'];j=event['target_bit']
                predicate=node if z3.is_bool(node) else node%(2**(j+1))>=2**j
                solver.add(z3.substitute(predicate if desired else z3.Not(predicate),*mapping))
        if code.startswith('DUPLICATE_'):
            # A selected duplicate never proves there is no different witness.
            solver.add(parameter!=int(event['z']))
            for assignment in prefix:
                values=decode_assignment(assignment,decls)
                solver.add(z3.Or([term!= (z3.BoolVal(values[str(e.decl().name()),str(e.sort())]) if z3.is_bool(e) else z3.IntVal(values[str(e.decl().name()),str(e.sort())])) for e,term in mapping]))
            expected='sat_or_unsat_or_unknown'
    result=solver.check();answer=str(result)
    if result==z3.unknown:details['unknown_reason']=solver.reason_unknown()
    if result==z3.sat:details['witness']=str(solver.model())
    return dict(code=code,result=answer,expected=expected,passed=answer!='sat' if expected=='unsat' else True,
        proved=answer=='unsat' and expected=='unsat',wall_seconds=time.monotonic()-start,
        diagnostic_only=True,**details)

def verify_file(path,formula=None,prefix=(),cap=12):
    data=json.loads(Path(path).read_text());text=formula or data['formula'];records=data['diagnostics']['records']
    contexts={r['direction_id']:r for r in records if r['unit']=='context'};counts=Counter();checks=[]
    for event in records:
        code=event.get('code','')
        if not(code in ('ORIGIN_ONLY','REACH_EMPTY','BIT_VALUE_UNREACHABLE','NO_DIRECTION_PROVED') or code.startswith('DUPLICATE_')):continue
        if event['unit'] not in ('phase','target','batch'):continue
        if counts[code]>=cap:continue
        counts[code]+=1
        record=check_record(text,contexts.get(event.get('direction_id')),event,prefix)
        checks.append(dict(file=str(path),direction_id=event.get('direction_id'),**record))
        if not record['passed']:raise ValueError('diagnostic proof contradicted: '+json.dumps(checks[-1]))
    return checks

def main():
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();checks=[]
    for file in sorted(a.folder.glob('*.json')):checks+=verify_file(file)
    a.output.write_text(json.dumps(dict(checks=checks,all_noncontradictory=True,unknown=sum(c['result']=='unknown' for c in checks)),indent=2)+'\n')
    print(len(checks),'independent scoped checks')
if __name__=='__main__':main()
