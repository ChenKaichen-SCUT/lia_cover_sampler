#!/usr/bin/env python3
"""Replay historical models with the immutable manual metric; search is forbidden."""
import argparse, csv, gzip, json, math, os, subprocess, time, re
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,file_hash,dump,write_csv,require,object_hash
from validation import decode_assignment
import z3
ROOT=Path(__file__).resolve().parents[1]
BATCHES={'highdiv':'highdiv_time900','lia_original':'lia_time900','lia_ab_fixed':'round2_formal_v2/control_ab_time','lia_round2_full':'round2_formal_v2/full_time'}
PREFIXES=(2,5,10,20,50,100)
MASK=(1<<64)-1
class Changes(dict):
    def __init__(self,state): super().__init__(state);self.before={}
    def __setitem__(self,key,value):
        old=self[key]
        if old!=value and key not in self.before:self.before[key]=old
        super().__setitem__(key,value)

def patterns(groups,values):
    return tuple(tuple(j for j,a in enumerate(g['alternatives']) if all(values[v['name']]==int(v['value']) for v in a['assignments'])) for g in groups)

def work(case,output,probe):
    out=Path(output)/'cases'/case['path'];out.mkdir(parents=True,exist_ok=False)
    formula=ROOT/'benchmarks'/case['path'];require(file_hash(formula)==case['sha256'],'formula hash')
    catalog=json.loads(subprocess.check_output([probe,str(formula)],text=True));dump(out/'catalog.json',catalog)
    early=[];nodes=[];modes=[];prefixes=[];anomalies=[];hashes={str(formula):file_hash(formula)};summaries=[]
    with no_search():
        metric=FrozenCoverage(formula.read_text());ids={n.get_id():i for i,n in enumerate(metric.nodes)}
        classes={n.get_id():('boolean' if z3.is_bool(n) else 'integer_variable' if n.decl().kind()==z3.Z3_OP_UNINTERPRETED else 'integer_constant' if z3.is_int_value(n) else 'arithmetic_expression') for n in metric.nodes}
        metadata=[dict(node=i,kind=classes[n.get_id()],expression_sha256=object_hash(n.sexpr()),expression=n.sexpr()) for i,n in enumerate(metric.nodes)]
        # Expressions are source-bound by original node identity; avoid huge repeated strings in event CSV.
        with gzip.open(out/'nodes.json.gz','wt') as f:json.dump(metadata,f)
        expected={(str(e.decl().name()),str(e.sort())) for e in metric.decls}
        reference={k:(0,0,v[2]) for k,v in metric.empty.items()};pool_events=[]
        for mode,batch in BATCHES.items():
            folder=ROOT/'results'/batch
            manifest=json.loads((folder/'manifest.json').read_text());complete=json.loads((folder/'COMPLETE.json').read_text())
            require(file_hash(folder/'manifest.json')==complete['manifest_sha256'],'manifest hash');require(file_hash(folder/'runs.jsonl')==complete['journal_sha256'],'journal hash')
            require(manifest['seconds']==900 and manifest['samples']==4294967295,'protocol')
            jobs=[json.loads(x) for x in (folder/'runs.jsonl').read_text().splitlines()];job=next(x for x in jobs if x['instance']==case['path'] and x['seed']==0)
            auditpath=folder/'audit'/case['path']/'seed-0.json';audit=json.loads(auditpath.read_text());curve=audit['curve']
            raw=folder/job['run'];sample=raw/(case['path']+'.samples' if mode=='highdiv' else 'models.jsonl');timing=raw/(case['path']+'.times.csv' if mode=='highdiv' else 'emission.csv')
            for path in (folder/'manifest.json',folder/'COMPLETE.json',folder/'runs.jsonl',folder/'archive.json',sample,timing,auditpath):hashes[str(path)]=file_hash(path)
            timestamps={int(row[0]):float(row[1]) for row in csv.reader(timing.open()) if row and row[0].isdigit()}
            storage=Changes(metric.empty);metric.stat._storage=storage;observations={};original_visit=metric.stat.evaluate_node
            def visit(node,value):observations[node]=value;original_visit(node,value)
            metric.stat.evaluate_node=visit
            seen=set();seen_modes=set();lastpat=None;previous_values={};previous_observations={};covered=0;count=0;positive=0
            stream=gzip.open(out/(mode+'.samples.jsonl.gz'),'wt');updates=gzip.open(out/(mode+'.updates.jsonl.gz'),'wt')
            for line_no,line in enumerate(sample.open(),1):
                if mode=='highdiv':
                    index_s,body=line.strip().split(':',1);index=int(index_s);values={a.split(':',1)[0].strip():int(a.split(':',1)[1].strip('()').replace(' ','')) for a in body.split(';') if a}
                    model=[dict(name=name,sort='Int',value=str(v)) for name,v in sorted(values.items())]
                else:
                    record=json.loads(line);index=record['index'];model=record['assignment'];values={a['name']:int(a['value']) for a in model}
                if index not in timestamps or timestamps[index]>900:
                    anomalies.append(dict(instance=case['path'],mode=mode,line=line_no,index=index,reason='timestamp_missing' if index not in timestamps else 'after_deadline'));continue
                # Complete typed decode plus original manual evaluation, never a solver fallback.
                decoded={}
                for item in model:
                    name,sort,v=item['name'],item['sort'],item['value'];k=(name,sort)
                    require(k in expected and k not in decoded and set(item)=={'name','sort','value'},'typed declaration mismatch')
                    require((sort=='Int' and type(v) is str and re.fullmatch(r'-?(0|[1-9][0-9]*)',v)) or (sort=='Bool' and type(v) is bool),'typed value mismatch')
                    decoded[k]=int(v) if sort=='Int' else v
                require(decoded.keys()==expected,'incomplete model')
                key=object_hash(model);require(key not in seen,'duplicate historical formal output');seen.add(key)
                storage.before.clear();observations.clear();require(metric.evaluator.count_sample([(name,v) for (name,sort),v in decoded.items()]),'invalid historical model')
                count+=1;gain=0;newobs=0;firstobs=0;parts=Counter();changes=[]
                for node,old in storage.before.items():
                    new=storage[node];one,zero=int(new[0]),int(new[1]);oldone,oldzero=int(old[0]),int(old[1]);bits=(one&zero)&~(oldone&oldzero);dg=bits.bit_count();gain+=dg
                    n0=(zero&~oldzero);n1=(one&~oldone);newobs+=n0.bit_count()+n1.bit_count();first=((one|zero)&~(oldone|oldzero)).bit_count();firstobs+=first
                    parts[classes[node]]+=dg
                    if new[2]=='int':
                        for label,mask in [('low',255),('mid',((1<<32)-1)^255),('high',MASK^((1<<32)-1))]:parts[label]+=(bits&mask).bit_count()
                    changes.append([ids[node],str(zero),str(one)])
                    rp=reference[node];r0,r1=int(rp[1]),int(rp[0]);add0=zero&~r0;add1=one&~r1
                    if add0 or add1:pool_events.append(dict(mode=mode,index=count,raw_line=line_no,model_hash=key,node=ids[node],zero=str(add0),one=str(add1)))
                    reference[node]=(r1|one,r0|zero,new[2])
                    if count<=100:
                        nodes.append(dict(instance=case['path'],mode=mode,index=count,model_hash=key,node=ids[node],kind=classes[node],new_covered_mask=str(bits),new_zero=str(n0),new_one=str(n1),gain=dg,first_observed=first))
                covered+=gain;positive+=gain>0
                require(count<=len(curve),'extra timely model');point=curve[count-1]
                require(point['covered']==covered and point['total']==metric.total and abs(point['seconds']-timestamps[index])<1e-8,'frozen curve differential mismatch')
                if changes:updates.write(json.dumps(dict(index=count,changes=changes))+'\n')
                pat=patterns(catalog['groups'],values);newmode=pat not in seen_modes and bool(catalog['groups']);seen_modes.add(pat);changed=lastpat is not None and pat!=lastpat
                row=dict(instance=case['path'],split=case['split'],mode=mode,seed=0,index=count,raw_index=index,raw_line=line_no,model_hash=key,seconds=timestamps[index],gain=gain,covered=covered,total=metric.total,new_mode=newmode,mode_changed=changed,mode_hash=object_hash(pat))
                stream.write(json.dumps(row)+'\n')
                if count<=100:
                    early.append(dict(row,percent=100*covered/metric.total,new_observations=newobs,first_observations=firstobs,opposite_completions=gain,
                        **{k:parts[k] for k in ('boolean','integer_variable','integer_constant','arithmetic_expression','low','mid','high')},
                        variable_changes=sum(v!=previous_values.get(k,v) for k,v in values.items()),sign_changes=sum((v<0)!=(previous_values.get(k,v)<0) for k,v in values.items()),
                        max_variable_delta=str(max((abs(v-previous_values.get(k,v)) for k,v in values.items()),default=0)),
                        observed_ast_changes=sum(v!=previous_observations[k] for k,v in observations.items() if k in previous_observations),
                        visited_symmetric_difference=len(set(observations)^set(previous_observations))))
                    for g,alts in zip(catalog['groups'],pat):modes.append(dict(instance=case['path'],mode=mode,index=count,group_node=g['node'],actual_alternatives=json.dumps(alts),exclusive=g['exclusive'],model_hash=key))
                    previous_values=values;previous_observations=observations.copy()
                if count in PREFIXES:prefixes.append(dict(instance=case['path'],split=case['split'],mode=mode,prefix=count,covered=covered,total=metric.total,percent=100*covered/metric.total,seconds=timestamps[index]))
                lastpat=pat
                if count==100:metric.stat.evaluate_node=original_visit
            metric.stat.evaluate_node=original_visit;stream.close();updates.close()
            require(count==len(curve),'missing original prefix');require(covered==metric.count(storage),'incremental total mismatch')
            dump(out/(mode+'.final.json'),metric.export(storage))
            summaries.append(dict(instance=case['path'],mode=mode,count=count,positive_outputs=positive,zero_outputs=count-positive,covered=covered,total=metric.total,actual_modes=len(seen_modes) if catalog['groups'] else 0,terminal=job['status'],process_seconds=job['elapsed'],binary_sha256=manifest['binary_sha256']))
        dump(out/'reference.json',metric.export(reference));dump(out/'pool_witnesses.json',pool_events);dump(out/'hashes.json',hashes)
        for name,rows in [('early_samples',early),('early_nodes',nodes),('early_modes',modes),('prefixes',prefixes),('anomalies',anomalies),('runs',summaries)]:write_csv(out/(name+'.csv'),rows)
    return case['path']

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'results/round8_analysis');p.add_argument('--probe',type=Path,default=ROOT/'build/round8/release/choice_catalog_probe');p.add_argument('--jobs',type=int,default=9);a=p.parse_args();require(1<=a.jobs<=15,'workers')
    a.output.mkdir(parents=True,exist_ok=False);cases=json.loads((ROOT/'benchmarks/manifest.json').read_text());start=time.time()
    dump(a.output/'plan.json',dict(base='13eea92',search_forbidden=True,batches=BATCHES,prefixes=PREFIXES,jobs=a.jobs,probe_sha256=file_hash(a.probe),script_sha256=file_hash(__file__),metric_sha256=file_hash(Path(os.environ['HIGHDIV_ROOT'])/'HighDiv/my_scripts/calc_metric.py'),cases=cases))
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:
        for f in as_completed([pool.submit(work,c,str(a.output),str(a.probe.resolve())) for c in cases]):print('completed',f.result(),flush=True)
    for name in ('early_samples','early_nodes','early_modes','prefixes','anomalies','runs'):
        rows=[]
        for c in cases:
            with (a.output/'cases'/c['path']/(name+'.csv')).open() as f:rows.extend(csv.DictReader(f))
        write_csv(a.output/(name+'.csv'),rows)
    dump(a.output/'COMPLETE.json',dict(seconds=time.time()-start,runs=36,search_calls=0))
if __name__=='__main__':main()
