#!/usr/bin/env python3
"""Transform frozen AB prefixes, accounting for all new work; not online performance."""
import argparse,json,subprocess,sys,time,resource
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
from run_iteration import ROOT,digest,write
sys.path.insert(0,str(ROOT/'python'))
from validation import original_formula,variables,verify
from coverage_differential_test import load_metric,differential

def one(payload):
    instance,seed,binary,out,limit=payload;out=Path(out);formula=ROOT/'benchmarks'/instance['path']
    source=ROOT/'results/round3_screen/main/control_ab/raw'/instance['path']/f'seed-{seed}/models.jsonl'
    source_sha=digest(source);raw=[json.loads(line) for line in source.read_text().splitlines()][:limit]
    folder=out/'raw'/instance['path']/f'seed-{seed}';folder.mkdir(parents=True)
    values=folder/'prefix.values';timestamps=folder/'allocation_times.values'
    values.write_text(''.join(' '.join(str(v['value']).lower() for v in m['assignment'])+'\n' for m in raw))
    timestamps.write_text(''.join(str(m['elapsed'])+'\n' for m in raw))
    cmd=[binary,str(formula),str(values),str(timestamps),str(folder/'transformed'),str(seed)]
    before=resource.getrusage(resource.RUSAGE_CHILDREN);start=time.monotonic()
    result=subprocess.run(cmd,capture_output=True,text=True,timeout=300)
    after=resource.getrusage(resource.RUSAGE_CHILDREN)
    (folder/'process.log').write_text(result.stdout+'\n'+result.stderr)
    if result.returncode:raise RuntimeError('prefix replay failed: '+str(folder))
    process_wall=time.monotonic()-start;cpu=after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime
    audit_start=time.monotonic();audit_cpu=time.process_time()
    transformed=folder/'transformed';emitted=[json.loads(s) for s in (transformed/'models.jsonl').read_text().splitlines()]
    metric=load_metric();old=metric.WireCoverageStatistics();new=metric.WireCoverageStatistics()
    old_eval=metric.ManualSatisfiesMetric(formula.read_text(),old);new_eval=metric.ManualSatisfiesMetric(formula.read_text(),new)
    root=original_formula(formula);decls=variables(root);seen=set()
    def sample(assignment):return [(v['name'],v['value'] if v['sort']=='Bool' else int(v['value'])) for v in assignment]
    for m in emitted:
        assert verify(root,m['assignment'],declarations=decls)=='valid'
        key=json.dumps(m['assignment'],sort_keys=True);assert key not in seen;seen.add(key)
    prefixes=[];j=0
    for i,m in enumerate(raw):
        assert old_eval.count_sample(sample(m['assignment']))
        while j<len(emitted) and emitted[j]['raw_generator_index']<=i:
            assert new_eval.count_sample(sample(emitted[j]['assignment']))
            assert sum(new.node_count(*s) for s in new._storage.values())==emitted[j]['covered'];j+=1
        old_states=list(old._storage.values());new_states=list(new._storage.values())
        assert len(old_states)==len(new_states)
        for a,b in zip(old_states,new_states):
            assert not(int(a[0])&~int(b[0])) and not(int(a[1])&~int(b[1]))
        old_gain=sum(old.node_count(*s) for s in old_states);new_gain=sum(new.node_count(*s) for s in new_states)
        assert new_gain>=old_gain
        prefixes.append(dict(raw_prefix=i+1,emitted_samples=j,raw_covered=old_gain,emitted_covered=new_gain))
    differential(formula,[m['assignment'] for m in emitted[:5]],ROOT/'build/lia_cover_sampler')
    write(folder/'independent_prefix_audit.json',dict(all_preserved=True,prefixes=prefixes))
    summary=json.loads((transformed/'summary.json').read_text())
    total=summary['total'];last=prefixes[-1]
    return dict(instance=instance['path'],seed=seed,input_sha256=instance['sha256'],source=str(source),source_sha256=source_sha,
        command=cmd,binary_sha256=digest(binary),raw_prefix=len(raw),emitted=len(emitted),all_preserved=True,all_valid=True,
        raw_percent=100*last['raw_covered']/total,emitted_percent=100*last['emitted_covered']/total,
        process_wall_seconds=process_wall,cpu_seconds=cpu,audit_seconds=time.monotonic()-audit_start,audit_cpu_seconds=time.process_time()-audit_cpu,
        summary=summary,limitation='Historical times only allocate extra-cost credit; emitted times measure replay processing, not real sampling')

def main():
    p=argparse.ArgumentParser();p.add_argument('--binary',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--jobs',type=int,default=12);p.add_argument('--prefix',type=int,default=100);a=p.parse_args()
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);rows=json.loads((ROOT/'benchmarks/manifest.json').read_text())
    jobs=[(r,s,str(a.binary.resolve()),str(out),a.prefix) for r in rows if r['split']=='development' for s in (0,1,2)]
    write(out/'plan.json',dict(stage='fixed_raw_prefix_transform',jobs=jobs,prefix_limit=a.prefix,workers=a.jobs,
        source='frozen round3 control_ab online outputs',interpretation='Not online timing; no solver learning state restored'))
    start=time.monotonic();results=[]
    with ProcessPoolExecutor(max_workers=a.jobs) as pool,(out/'runs.jsonl').open('w') as log:
        for future in as_completed([pool.submit(one,j) for j in jobs]):
            row=future.result();results.append(row);log.write(json.dumps(row)+'\n');log.flush()
    write(out/'analysis.json',dict(rows=sorted(results,key=lambda r:(r['instance'],r['seed'])),all_valid=True,all_preserved=True,
        wall_seconds=time.monotonic()-start,cpu_seconds=sum(r['cpu_seconds'] for r in results),
        audit_worker_seconds=sum(r['audit_seconds'] for r in results),audit_cpu_seconds=sum(r['audit_cpu_seconds'] for r in results)))
    write(out/'COMPLETE.json',dict(runs=len(results),raw_prefixes=sum(r['raw_prefix'] for r in results),emitted=sum(r['emitted'] for r in results),
        all_valid=True,all_preserved=True,plan_sha256=digest(out/'plan.json'),journal_sha256=digest(out/'runs.jsonl')))
    print('fixed AB prefix transforms complete',len(results))
if __name__=='__main__':main()
