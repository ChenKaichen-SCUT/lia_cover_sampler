#!/usr/bin/env python3
"""Additional first-100 short-circuit branch observation diagnostics; no search."""
import csv,gzip,json,argparse
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from analyze_early_coverage import ROOT,BATCHES
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,write_csv,object_hash
import z3

def work(case):
    path=case['path'];out=ROOT/'results/round8_analysis';rows=[];modes=[];choices=[]
    cat=json.loads((out/'cases'/path/'catalog.json').read_text())
    with no_search():
        metric=FrozenCoverage((ROOT/'benchmarks'/path).read_text());ors=[(i,n.get_id(),[(c.get_id(),j) for j,c in enumerate(n.children())]) for i,n in enumerate(metric.nodes) if n.decl().kind()==z3.Z3_OP_OR]
        old=metric.stat.evaluate_node;obs={}
        def visit(n,v):obs[n]=v;old(n,v)
        metric.stat.evaluate_node=visit
        for mode,batch in BATCHES.items():
            prev=None;prevvalues=None;visited=set();alt_seen=set()
            for lineno,line in enumerate((ROOT/'results'/batch/'raw'/path/'seed-0'/(path+'.samples' if mode=='highdiv' else 'models.jsonl')).open(),1):
                if lineno>100:break
                if mode=='highdiv':
                    _,body=line.strip().split(':',1);values={a.split(':',1)[0].strip():int(a.split(':',1)[1].strip('()').replace(' ','')) for a in body.split(';') if a}
                else:values={a['name']:int(a['value']) for a in json.loads(line)['assignment']}
                obs.clear();metric.evaluator.count_sample(list(values.items()))
                signature=[]
                for node,zid,children in ors:
                    if zid in obs:
                        actual=[(j,bool(obs[c])) for c,j in children if c in obs];signature.append((node,actual))
                        if lineno<=10:modes.append(dict(instance=path,mode=mode,index=lineno,or_node=node,observed_children=json.dumps(actual),semantics='actual short-circuit visit, not full satisfying-alternative set'))
                key=object_hash(signature);rows.append(dict(instance=path,mode=mode,index=lineno,observed_or_mode_hash=key,observed_or_mode_changed=prev is not None and key!=prev,observed_or_nodes=len(signature),new_observed_or_mode=key not in visited))
                prev=key;visited.add(key)
                for g in cat['groups']:
                    for j,a in enumerate(g['alternatives']):
                        satisfied=all(values[v['name']]==int(v['value']) for v in a['assignments']);forced=sum(values[v['name']]!=int(v['value']) for v in a['assignments'])
                        if satisfied:alt_seen.add((g['node'],j))
                        if lineno in (1,10,100):choices.append(dict(instance=path,mode=mode,prefix=lineno,group=g['node'],alternative=j,forced_changes_from_current_model=forced,visited_by_prefix=(g['node'],j) in alt_seen))
    return rows,modes,choices

def main():
    cases=json.loads((ROOT/'benchmarks/manifest.json').read_text());rows=[];modes=[];choices=[]
    with ProcessPoolExecutor(max_workers=9) as pool:
        for a,b,c in pool.map(work,cases):rows+=a;modes+=b;choices+=c
    out=ROOT/'results/round8_analysis';write_csv(out/'early_branch_samples.csv',rows);write_csv(out/'early_branch_modes.csv',modes);write_csv(out/'choice_prefix_visits.csv',choices)
if __name__=='__main__':main()
