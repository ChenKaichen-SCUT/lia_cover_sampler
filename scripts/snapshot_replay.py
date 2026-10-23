#!/usr/bin/env python3
"""Development-only fixed formula/model/AST-coverage snapshots and paired local replay."""
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
import json,os,resource,subprocess,sys,time
from pathlib import Path
from run_iteration import ROOT,digest,write
sys.path.insert(0,str(ROOT/'python'))
from validation import original_formula,variables,verify,nodes_preorder
from coverage_differential_test import load_metric

def create(output,probe):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False);snapshots=[]
    for row in json.loads((ROOT/'benchmarks/manifest.json').read_text()):
        if row['split']!='development':continue
        formula=ROOT/'benchmarks'/row['path'];source=ROOT/'results/round2_formal_v2/full_time/raw'/row['path']/'seed-0/models.jsonl'
        data=[]
        with source.open() as f:
            for line in f:
                data.append(json.loads(line)['assignment'])
                if len(data)>=101:break
        decls=variables(original_formula(formula))
        def values(assignment):
            mapping={(r['name'],r['sort']):r['value'] for r in assignment}
            return ' '.join(str(mapping[str(v.decl().name()),str(v.sort())]).lower() for v in decls)
        source_sha=digest(source)
        for k in (1,5,10,50):
            if len(data)<=k:continue
            folder=output/row['path']/f'prefix-{k}';folder.mkdir(parents=True)
            (folder/'prefix.values').write_text('\n'.join(values(a) for a in data[:k])+'\n')
            (folder/'seed.values').write_text(values(data[k])+'\n')
            write(folder/'assignments.json',dict(prefix=data[:k],seed=data[k]))
            export=folder/'export.json'
            subprocess.run([str(probe),str(formula),str(folder/'prefix.values'),str(folder/'seed.values'),
                'export','new','1','0',str(export),'4'],check=True,capture_output=True)
            snapshot=json.loads(export.read_text())['snapshot'];write(folder/'coverage.json',snapshot)
            snapshots.append(dict(id=row['path']+f'/prefix-{k}',instance=row['path'],split='development',
                formula=str(formula),input_sha256=row['sha256'],prefix=k,folder=str(folder),
                source_models=str(source),source_sha256=source_sha,coverage_sha256=digest(folder/'coverage.json'),
                prefix_sha256=digest(folder/'prefix.values'),seed_sha256=digest(folder/'seed.values'),
                assignments_sha256=digest(folder/'assignments.json')))
    write(output/'manifest.json',dict(definition='F, legal candidate seed m, coverage S of first k committed outputs',
        solver_state='no SMT learned clauses restored; local mechanism experiment only',
        source_commit='21befad43206c4917104767138b9317740ecf9ae',probe_sha256=digest(probe),snapshots=snapshots))
    return output/'manifest.json'

_CACHE={}
def oracle(snapshot):
    key=snapshot['id']
    if key in _CACHE:return _CACHE[key]
    folder=Path(snapshot['folder']);data=json.loads((folder/'assignments.json').read_text());metric=load_metric()
    stat=metric.WireCoverageStatistics();evaluation=metric.ManualSatisfiesMetric(Path(snapshot['formula']).read_text(),stat)
    root=original_formula(snapshot['formula']);decls=variables(root)
    def sample(a):return [(r['name'],r['value'] if r['sort']=='Bool' else int(r['value'])) for r in a]
    for assignment in data['prefix']:
        if verify(root,assignment,declarations=decls)!='valid':raise ValueError('invalid saved prefix')
        if not evaluation.count_sample(sample(assignment)):raise ValueError('frozen evaluator rejected prefix')
    nodes=nodes_preorder(metric.z3.And(evaluation._solver.assertions()))
    expected=dict(covered=sum(stat.node_count(*s) for s in stat._storage.values()),
        total=sum(stat.node_total(k,v[2]) for k,v in stat._storage.items()),
        states=[[str(int(stat._storage[n.get_id()][1])),str(int(stat._storage[n.get_id()][0]))] for n in nodes])
    if expected!=json.loads((folder/'coverage.json').read_text()):raise ValueError('snapshot AST coverage mismatch')
    value=(root,decls,stat,evaluation,stat._storage.copy(),expected,sample);_CACHE[key]=value;return value

def one(payload):
    snapshot,name,config,module,seed,seconds,out=payload;out=Path(out)
    folder=Path(snapshot['folder']);path=out/'raw'/name/snapshot['id']/module/f'seed-{seed}.json';path.parent.mkdir(parents=True,exist_ok=True)
    for file,key in [('prefix.values','prefix_sha256'),('seed.values','seed_sha256'),('coverage.json','coverage_sha256'),('assignments.json','assignments_sha256')]:
        if digest(folder/file)!=snapshot[key]:raise ValueError('modified snapshot')
    if digest(snapshot['formula'])!=snapshot['input_sha256']:raise ValueError('modified formula')
    command=[config['binary'],snapshot['formula'],str(folder/'prefix.values'),str(folder/'seed.values'),module,
             config['mode'],str(seconds),str(seed),str(path),str(config.get('step_candidates',4))]
    before=resource.getrusage(resource.RUSAGE_CHILDREN);start=time.monotonic()
    result=subprocess.run(command,capture_output=True,text=True,timeout=60)
    after=resource.getrusage(resource.RUSAGE_CHILDREN);wall=time.monotonic()-start
    if result.returncode:
        path.with_suffix('.failure.log').write_text(result.stdout+'\n'+result.stderr);raise RuntimeError('micro candidate failed: '+str(path))
    data=json.loads(path.read_text());audit_start=time.monotonic();audit_cpu_start=time.process_time()
    root,decls,stat,evaluation,storage,expected,sample=oracle(snapshot)
    if data['snapshot']!=expected:raise ValueError('module used a different coverage snapshot')
    if data['candidate'] is not None:
        if verify(root,data['candidate'],declarations=decls)!='valid':raise ValueError('invalid micro candidate')
        stat._storage=storage.copy();evaluation.count_sample(sample(data['candidate']))
        gain=sum(stat.node_count(*s) for s in stat._storage.values())-expected['covered']
        if gain!=data['gain']:raise ValueError('micro final single-model gain differs from original evaluator')
    # Compact outputs refer to one immutable full AST snapshot, not repeated megabyte copies.
    data.pop('snapshot');data['snapshot_id']=snapshot['id'];data['coverage_sha256']=snapshot['coverage_sha256'];write(path,data)
    return dict(snapshot=snapshot['id'],instance=snapshot['instance'],module=module,configuration=name,seed=seed,
        seconds=seconds,process_wall_seconds=wall,cpu_seconds=after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,
        audit_seconds=time.monotonic()-audit_start,audit_cpu_seconds=time.process_time()-audit_cpu_start,
        command=command,output=str(path.relative_to(out)),
        binary_sha256=config['binary_sha256'],valid=True,**{k:data[k] for k in ('elapsed_seconds','before_gain','gain','first_valid_seconds','first_positive_seconds','moves','attempted_steps','status','statistics')})

def run_micro(args):
    configs=json.loads(args.configs.read_text());snapshots=json.loads(args.snapshot_manifest.read_text())['snapshots']
    if any(s['split']!='development' for s in snapshots):raise ValueError('micro only uses development states')
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    for c in configs.values():
        c['binary']=str(Path(c['binary']).resolve());actual=digest(c['binary'])
        if c.get('binary_sha256',actual)!=actual:raise ValueError('micro binary hash mismatch')
        c['binary_sha256']=actual
    seeds=[int(s) for s in args.seeds.split(',')];seconds=args.seconds or .06
    jobs=[(s,name,c,module,seed,seconds,str(out)) for s in snapshots for module in ('safe','line','polish') for seed in seeds for name,c in configs.items()]
    write(out/'plan.json',dict(stage='micro',seconds=seconds,configs=configs,seeds=seeds,jobs=len(jobs),workers=args.jobs,
        manifest=str(args.snapshot_manifest.resolve()),manifest_sha256=digest(args.snapshot_manifest),
        purpose='fixed local state comparison; not full SMT solver replay or end-to-end coverage evidence'))
    start=time.monotonic();rows=[]
    with ProcessPoolExecutor(max_workers=args.jobs) as pool,(out/'runs.jsonl').open('w') as journal:
        for future in as_completed([pool.submit(one,j) for j in jobs]):
            row=future.result();rows.append(row);journal.write(json.dumps(row)+'\n');journal.flush()
    from statistics import mean
    summary=[]
    for module in ('safe','line','polish'):
        for name in configs:
            group=[r for r in rows if r['module']==module and r['configuration']==name]
            valid=[r['first_valid_seconds'] for r in group if r['first_valid_seconds'] is not None]
            positive=[r['first_positive_seconds'] for r in group if r['first_positive_seconds'] is not None]
            summary.append(dict(module=module,configuration=name,runs=len(group),valid_candidate_runs=len(valid),positive_candidate_runs=len(positive),
                mean_first_valid=mean(valid) if valid else None,mean_first_positive=mean(positive) if positive else None,
                gain=sum(r['gain'] for r in group),module_seconds=sum(r['elapsed_seconds'] for r in group),
                accepted_moves=sum(r['moves'] for r in group),attempted_steps=sum(r['attempted_steps'] for r in group)))
    pairs=[]
    if 'eager' in configs and 'new' in configs:
        lookup={(r['snapshot'],r['module'],r['seed'],r['configuration']):r for r in rows}
        for s in snapshots:
            for module in ('safe','line','polish'):
                for seed in seeds:
                    a=lookup[s['id'],module,seed,'new'];b=lookup[s['id'],module,seed,'eager']
                    pairs.append(dict(snapshot=s['id'],module=module,seed=seed,gain_delta=a['gain']-b['gain'],
                        efficiency_delta=a['gain']/max(1e-9,a['elapsed_seconds'])-b['gain']/max(1e-9,b['elapsed_seconds']),
                        candidate_delta=int(a['first_valid_seconds'] is not None)-int(b['first_valid_seconds'] is not None)))
    write(out/'analysis.json',dict(stage='micro',all_valid=True,summary=summary,paired=pairs,
        wall_seconds=time.monotonic()-start,cpu_seconds=sum(r['cpu_seconds'] for r in rows),
        audit_worker_seconds=sum(r['audit_seconds'] for r in rows),audit_cpu_seconds=sum(r['audit_cpu_seconds'] for r in rows)))
    write(out/'COMPLETE.json',dict(runs=len(rows),plan_sha256=digest(out/'plan.json'),journal_sha256=digest(out/'runs.jsonl')))
    print('micro completed',len(rows),flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--create',type=Path,required=True);p.add_argument('--probe',type=Path,default=ROOT/'build/snapshot_probe')
    a=p.parse_args();print(create(a.create,a.probe.resolve()))
if __name__=='__main__':main()
