#!/usr/bin/env python3
"""Frozen round-eight queues: no retries, no follow-up experiments."""
import argparse,hashlib,json,os,signal,subprocess,tarfile,threading,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
ENV={k:'1' for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','VECLIB_MAXIMUM_THREADS','BLIS_NUM_THREADS')}
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def run(job,binary,out):
    dest=out/'raw'/job['id'];dest.parent.mkdir(parents=True,exist_ok=True)
    cmd=[str(binary),job['formula'],job['mode'],str(job['seconds']),str(job['seed']),str(dest)]
    if job['kind']=='mechanism':cmd += [job['prefix_values'],job['seed_values']]
    record=dict(job,command=cmd,output=str(dest.relative_to(out)),start_utc=datetime.now(timezone.utc).isoformat());start=time.monotonic();expired=threading.Event()
    with dest.with_suffix('.stdout').open('wb') as stdout,dest.with_suffix('.stderr').open('wb') as stderr:
        child=subprocess.Popen(cmd,stdout=stdout,stderr=stderr,stdin=subprocess.DEVNULL,env={**os.environ,**ENV},start_new_session=True)
        def kill():
            try:os.killpg(child.pid,signal.SIGKILL);expired.set()
            except ProcessLookupError:pass
        timer=threading.Timer(job['seconds']+30,kill);timer.start()
        try:_,status,usage=os.wait4(child.pid,0);child.returncode=os.waitstatus_to_exitcode(status)
        finally:timer.cancel()
    record.update(exitcode=child.returncode,watchdog=expired.is_set(),process_seconds=time.monotonic()-start,cpu_seconds=usage.ru_utime+usage.ru_stime,
                  hashes={str(p.relative_to(dest)):digest(p) for p in dest.glob('*') if p.is_file()})
    if (dest/'summary.json').exists():record['summary']={k:v for k,v in json.loads((dest/'summary.json').read_text()).items() if k!='coverage'}
    write(dest.with_suffix('.run.json'),record);return record

def main():
    p=argparse.ArgumentParser();p.add_argument('--kind',choices=['mechanism','online'],required=True);p.add_argument('--binary',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--jobs',type=int,default=15);a=p.parse_args()
    if not 1<=a.jobs<=15:raise ValueError('jobs 1..15')
    subprocess.run(['git','diff','--exit-code','HEAD','--'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);(out/'raw').mkdir();jobs=[]
    if a.kind=='mechanism':
        states=json.loads((ROOT/'results/round3_snapshots/states/manifest.json').read_text())['snapshots'];states=[s for s in states if s['prefix'] in (1,10)]
        for s in states:
            assert s['split']=='development' and digest(s['formula'])==s['input_sha256']
            for name,key in [('prefix.values','prefix_sha256'),('seed.values','seed_sha256'),('coverage.json','coverage_sha256'),('assignments.json','assignments_sha256')]:
                assert digest(Path(s['folder'])/name)==s[key], 'frozen snapshot changed'
        sources=[dict(instance=s['instance'],state=s['id'],formula=s['formula'],prefix_values=str(Path(s['folder'])/'prefix.values'),seed_values=str(Path(s['folder'])/'seed.values'),split='development') for s in states]
        for name in ('choice_equation','grouped_choice'):
            d=ROOT/'tests/fixtures/macro'/name;sources.append(dict(instance=name,state=name,formula=str(d.with_suffix('.smt2')),prefix_values=str(d.with_suffix('.prefix.values')),seed_values=str(d.with_suffix('.seed.values')),split='synthetic'))
    else:
        sources=[dict(instance=c['path'],state=c['path'],formula=str(ROOT/'benchmarks'/c['path']),split='development') for c in json.loads((ROOT/'benchmarks/manifest.json').read_text()) if c['split']=='development']
    for i,s in enumerate(sources):
        for seed in (0,1,2):
            modes=['micro_plain','micro_macro'] if (i+seed)%2==0 else ['micro_macro','micro_plain']
            for mode in modes:jobs.append(dict(s,seed=seed,mode=mode,kind=a.kind,seconds=.4 if a.kind=='mechanism' else 30,id=f"{s['state']}/seed-{seed}/{mode}",queue_index=len(jobs)))
    cpp=sorted(list((ROOT/'src').glob('*.cpp'))+list((ROOT/'include').glob('*.hpp')));source=hashlib.sha256(''.join(digest(p) for p in cpp).encode()).hexdigest()
    paths=cpp+[ROOT/'CMakeLists.txt',ROOT/'docs/第八轮实验协议.md',Path(__file__),a.binary.resolve(),ROOT/'scripts/audit_macro_pairs.py',ROOT/'scripts/analyze_macro_pairs.py',ROOT/'scripts/reevaluate_round6_saved_candidates.py',ROOT/'python/validation.py',ROOT/'python/coverage_differential_test.py']
    for s in sources:
        paths.append(Path(s['formula']))
        if a.kind=='mechanism':paths.extend([Path(s['prefix_values']),Path(s['seed_values'])])
    hroot=Path(os.environ['HIGHDIV_ROOT']);metric=hroot/'HighDiv/my_scripts/calc_metric.py';lib=hroot/'venv/lib/libz3.so';paths.extend([metric,lib])
    assert digest(metric)=='a51cdd069321cd32becf8eeb5e606cb71fbab16ae3e0231f68963eaadf493f04'
    assert digest(lib)=='c66acd3c4743444ce90b255820a88a65cbeaa4bd13808ed59ac7be91721967ca'
    plan=dict(base='13eea92',commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source,kind=a.kind,jobs=a.jobs,tasks=jobs,hashes={str(p):digest(p) for p in paths},created_utc=datetime.now(timezone.utc).isoformat())
    write(out/'plan.json',plan);records=[]
    with (out/'runs.jsonl').open('w') as journal,ThreadPoolExecutor(max_workers=a.jobs) as pool:
        for f in as_completed([pool.submit(run,j,a.binary.resolve(),out) for j in jobs]):
            r=f.result();records.append(r);journal.write(json.dumps(r)+'\n');journal.flush();print(r['id'],r['exitcode'],flush=True)
    with tarfile.open(out/'raw.tar.gz','w:gz') as archive:archive.add(out/'raw',arcname='raw')
    write(out/'archive.json',dict(sha256=digest(out/'raw.tar.gz'),files={str(p.relative_to(out)):digest(p) for p in (out/'raw').rglob('*') if p.is_file()}))
    write(out/'COMPLETE.json',dict(runs=len(records),failed=sum(r['exitcode']!=0 for r in records),plan_sha256=digest(out/'plan.json'),journal_sha256=digest(out/'runs.jsonl')))
if __name__=='__main__':main()
