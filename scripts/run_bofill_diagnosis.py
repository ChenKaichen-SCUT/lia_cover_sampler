#!/usr/bin/env python3
"""Frozen first-SAT diagnosis. Four-process batches pair both controls and both seeds."""
import argparse, json, os, sys, subprocess, time, signal
from pathlib import Path
from run_bofill_900s import gate, sha, dump, INPUTS
ROOT=Path(__file__).resolve().parents[1]
MODES=['plain','simplify_only','eq_only','choice_only','full']
def plan(out,extension=False):
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False);(out/'inputs').mkdir()
    binary=ROOT/'build/bofill_diagnosis/release/bofill_first_sat_probe';runs=[];batch=0
    for fi,(formula,expected) in enumerate(INPUTS):
        source=ROOT/'benchmarks'/formula;assert sha(source)==expected;(out/'inputs'/formula).write_bytes(source.read_bytes())
        if extension and fi:continue
        for mi,mode in enumerate(['plain','full'] if extension else MODES):
            configs=[(0,'continuous')] if extension else [(seed,control) for seed in [0,1] for control in ['repeated_2s','continuous']]
            for i,(seed,control) in enumerate(configs):
                name=f'{fi}_{mode}_{seed}_{control}';d=out/'runs'/name;sec=120 if extension else 30
                command=[str(binary),str(out/'inputs'/formula),mode,control,str(sec),str(seed),str(d/'raw')]
                runs.append(dict(directory=str(d.relative_to(out)),formula=formula,input_sha256=expected,mode=mode,seed=seed,control=control,seconds=sec,batch=0 if extension else batch,core=[0,2,4,6][(i+batch)%4],command=command))
            batch+=1
    files=[*sorted((ROOT/'src').glob('*.cpp')),*sorted((ROOT/'include').glob('*.hpp')),ROOT/'CMakeLists.txt',Path(__file__),binary,ROOT/'build/bofill_diagnosis/release/compile_commands.json',ROOT/'docs/bofill编译诊断实施指引.md',ROOT/'docs/bofill诊断冻结协议.md']
    meta=dict(schema=1,frozen_base='a31e91e',created_monotonic_ns=time.monotonic_ns(),git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),git_status=subprocess.check_output(['git','status','--short','--untracked-files=no'],cwd=ROOT,text=True),files={str(p.relative_to(ROOT)):sha(p) for p in files},z3_sha256=sha('/tmp/lia-highdiv-frozen/venv/lib/libz3.so'),runs=runs,max_parallel=4,memory_limit_gib=6,solver='z3 simple',cpu_topology=subprocess.check_output(['lscpu'],text=True),total_seconds_per_session=120 if extension else 30,initial_models=False,historical_hints=False,validation_reserve_seconds=.025,extension_preselected='SMT_random seed0 plain/full continuous only',termination='SIGTERM at total+0.25s; SIGKILL at total+2s; only timely receipts accepted')
    dump(out/'plan.json',meta);(out/'plan.sha256').write_text(sha(out/'plan.json')+'\n');return meta

def run(out):
    out=Path(out).resolve();meta=json.loads((out/'plan.json').read_text());assert sha(out/'plan.json')==(out/'plan.sha256').read_text().strip()
    for name,digest in meta['files'].items():assert sha(ROOT/name)==digest, name
    os.sched_setaffinity(0,{8});results=[]
    for batch in sorted({r['batch'] for r in meta['runs']}):
        specs=[r for r in meta['runs'] if r['batch']==batch];running=[]
        for spec in specs:
            d=out/spec['directory'];d.mkdir(parents=True,exist_ok=False);dump(d/'spec.json',spec);log=(d/'process.log').open('wb')
            child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--gate',str(d/'spec.json')],stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            running.append((spec,d,child,log))
        end=time.monotonic()+20
        while not all((d/'ready.json').exists() for _,d,_,_ in running):
            if time.monotonic()>end or any(p.poll() is not None for _,_,p,_ in running):raise RuntimeError('gate failed')
            time.sleep(.02)
        start=time.monotonic_ns()+100_000_000
        for _,_,p,_ in running:p.stdin.write(f'{start}\n'.encode());p.stdin.flush();p.stdin.close()
        dump(out/'progress.json',dict(batch=batch,status='running',completed=len(results),total=len(meta['runs'])))
        print(f'batch {batch}: '+', '.join("{mode}/{seed}/{control}".format(**s[0]) for s in running),flush=True)
        pending=list(running);terminated=set()
        while pending:
            for item in pending[:]:
                spec,d,p,log=item;elapsed=(time.monotonic_ns()-start)/1e9;code=p.poll()
                if code is not None:
                    log.close();r=dict(**spec,returncode=code,process_wall_seconds=elapsed,start_monotonic_ns=start);dump(d/'process_result.json',r);results.append(r);pending.remove(item)
                    if code!=0:raise RuntimeError(f'probe failed {d}: {code}')
                elif elapsed>spec['seconds']+2:os.killpg(p.pid,signal.SIGKILL)
                elif elapsed>spec['seconds']+.25 and p.pid not in terminated:os.killpg(p.pid,signal.SIGTERM);terminated.add(p.pid)
            time.sleep(.02)
        dump(out/'process_results.json',results)
    dump(out/'progress.json',dict(status='complete',completed=len(results),total=len(meta['runs'])))
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--gate':gate(sys.argv[2]);sys.exit()
    p=argparse.ArgumentParser();p.add_argument('action',choices=['plan','run','extension-plan']);p.add_argument('out');a=p.parse_args()
    if a.action=='run':run(a.out)
    else:plan(a.out,a.action=='extension-plan')
