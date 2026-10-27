#!/usr/bin/env python3
"""One frozen MVP: at most six samplers on separate physical cores; no retries."""
import argparse,json,os,signal,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from run_native_feedback_pairs import ROOT,INPUTS,dump,sha,gate,sample_resources
from run_position_frozen_900s import memory_snapshot,stop,GiB
from complete_position_frozen_900s import thread_snapshot
BASE='0e656558bb0f27388cf4ea2ef7f8d90975392079'
BRANCH='arith-coverage-construction'
BINARY=ROOT/'build/arith_coverage/release/arith_coverage_sampler'
LIBRARY=ROOT/'dependencies/z3-arith-coverage/install/lib/libz3.so'
OUT=ROOT/'results/arith_coverage_v1'
CONFIGS=['phase_only','arith_random','arith_coverage']
ALL_INPUTS=INPUTS+[
 ('20180326-Bromberger_v45_problem__003.smt2.slack.smt2','3d6448604971312772c02ae450fe761b9dc3eb9bcd5e8bac4b282b384614dc67'),
 ('convert_convert-jpg2gif-query-1459.smt2','0ea6c3119acb120f7655fae65cc9c743358f1914111e7cf2ab5290951d896f32')]
STAGES={'smoke':(10,[80],CONFIGS,ALL_INPUTS[:2]),'probe':(30,[90,91,92],['arith_probe'],ALL_INPUTS),'screen':(120,list(range(100,107)),CONFIGS,ALL_INPUTS)}

def topology():
    cores={};rows=[]
    for cpu in sorted(os.sched_getaffinity(0)):
        p=Path(f'/sys/devices/system/cpu/cpu{cpu}/topology');key=(int((p/'physical_package_id').read_text()),int((p/'core_id').read_text()))
        cores.setdefault(key,[]).append(cpu);rows.append(dict(cpu=cpu,package=key[0],physical_core=key[1]))
    assert len(cores)>=7,'six physical worker cores plus supervisor required'
    cs=[v for _,v in sorted(cores.items())];return [v[0] for v in cs[:6]],cs[6][0],rows

def seed_evidence():
    evidence=[];used=set()
    for p in sorted((ROOT/'results').rglob('plan.json')):
        if 'arith_coverage_' in str(p):continue
        try:v=json.loads(p.read_text())
        except (ValueError,OSError):continue
        seeds=set(v.get('seeds',[]))
        for key in ['jobs','runs','tasks']:
            if isinstance(v.get(key),list):seeds.update(r['seed'] for r in v[key] if isinstance(r,dict) and isinstance(r.get('seed'),int))
        used.update(seeds);evidence.append(dict(path=str(p.relative_to(ROOT)),sha256=sha(p),seeds=sorted(seeds)))
    proposed={s for _,seeds,_,_ in STAGES.values() for s in seeds}
    assert not proposed&used,'historical seeds overlap; freeze a fresh interval before any performance data'
    return dict(plans=evidence,proposed=sorted(proposed),overlap=[])

def prepare(stage,out):
    assert subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()==BRANCH
    assert not subprocess.check_output(['git','status','--short','--untracked-files=no'],cwd=ROOT,text=True).strip(),'commit tested code first'
    validation=OUT/'correctness/summary.json';v=json.loads(validation.read_text());assert v['passed']
    for p,h in v['files'].items():assert sha(ROOT/p)==h,'validation identity: '+p
    if stage=='probe':assert json.loads((OUT/'smoke/audit_summary.json').read_text())['passed']
    if stage=='screen':assert json.loads((OUT/'probe/gate.json').read_text())['passed'],'S2 failed: do not run S3'
    seconds,seeds,configs,entries=STAGES[stage];workers,supervisor,topo=topology();mem=memory_snapshot();assert mem['available']>=10*GiB
    out.mkdir(parents=True,exist_ok=False);(out/'inputs').mkdir();inputs=[]
    for name,h in entries:
        source=ROOT/'benchmarks'/name;assert sha(source)==h;dest=out/'inputs'/name;dest.write_bytes(source.read_bytes());inputs.append(dict(name=name,sha256=h,path=str(dest)))
    jobs=[]
    for group,(seed,entry) in enumerate((s,e) for s in seeds for e in inputs):
        width=len(configs);batch=group//(6//width);slot=group%(6//width)
        for offset in range(width):
            c=(offset+group)%width;core=workers[(slot*width+c+batch)%6]
            jobs.append(dict(batch=batch,group=group,formula=entry['name'],input=entry['path'],seed=seed,configuration=configs[c],seconds=seconds,core=core))
    files=dict(v['files'])
    for p in [ROOT/'CMakeLists.txt',ROOT/'docs/算术覆盖构造冻结实验协议.md',ROOT/'dependencies/patches/z3_arith_coverage.patch',validation,BINARY,LIBRARY]:files[str(p.relative_to(ROOT))]=sha(p)
    for folder in ['src','include','scripts','python']:
        for p in sorted((ROOT/folder).glob('*')):
            if p.is_file():files[str(p.relative_to(ROOT))]=sha(p)
    dump(out/'seed_audit.json',seed_evidence())
    plan=dict(schema=1,kind='screen' if stage=='screen' else 'exposure',stage=stage,base_commit=BASE,seconds=seconds,seeds=seeds,configurations=configs,total_sessions=len(jobs),inputs=inputs,jobs=jobs,files=files,preparation_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),created_utc=datetime.now(timezone.utc).isoformat(),max_concurrent_sessions=6,expected_loaded_z3_path=str(LIBRARY.resolve()),batches=max(j['batch'] for j in jobs)+1,supervisor_cpu=supervisor,cpu_topology=topo,worker_cores=workers,memory=mem,limits=dict(address_space_gib=6,per_file_gib=1,total_rss_stop_gib=20,minimum_free_memory_gib=2,cleanup_seconds=2),library_links=subprocess.check_output(['ldd',str(BINARY)],text=True),environment=subprocess.check_output(['lscpu'],text=True),highdiv_execution=False,automatic_retry=False,screen_requires_gate=True)
    dump(out/'plan.json',plan);print(json.dumps(dict(prepared=True,stage=stage,sessions=len(jobs),concurrent=6,batches=plan['batches'])))

def sample(out,plan):
    assert not (out/'execution.json').exists(),'one execution only; no automatic rerun'
    for p,h in plan['files'].items():assert sha(ROOT/p)==h,'frozen file changed: '+p
    assert not subprocess.check_output(['git','status','--short','--untracked-files=no'],text=True).strip(),'commit frozen plan first'
    allowed=os.sched_getaffinity(0);os.sched_setaffinity(0,{plan['supervisor_cpu']});results=[];all_children=[]
    dump(out/'execution.json',dict(pid=os.getpid(),git_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),started_utc=datetime.now(timezone.utc).isoformat(),algorithm_binary_sha256=sha(BINARY),plan_sha256=sha(out/'plan.json')))
    for name in ['NATIVE_TEST_MAX_QUERIES','BOFILL_START_MONO_NS']:os.environ.pop(name,None)
    try:
        for batch in range(plan['batches']):
            active=[]
            for original in [s for s in plan['jobs'] if s['batch']==batch]:
                spec=dict(original);d=out/'runs'/f"{spec['group']:02d}_{spec['configuration']}";d.mkdir(parents=True)
                spec['command']=[str(BINARY),spec.pop('input'),spec['configuration'],str(plan['seconds']),str(spec['seed']),str(d/'raw')];dump(d/'spec.json',spec)
                log=(d/'process.log').open('wb');p=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--gate',str(d/'spec.json')],stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                r=dict(p=p,dir=d,spec=spec,log=log,termination=None,killed=False);active.append(r);all_children.append(r)
            deadline=time.monotonic()+30
            while not all((r['dir']/'ready.json').exists() for r in active):
                assert time.monotonic()<deadline and all(r['p'].poll() is None for r in active),'gate setup failed';time.sleep(.02)
            start=time.monotonic_ns()+200_000_000
            for r in active:r['p'].stdin.write(f'{start}\n'.encode());r['p'].stdin.flush();r['p'].stdin.close()
            dump(out/f'batch_{batch}_start.json',dict(batch=batch,start_monotonic_ns=start,utc=datetime.now(timezone.utc).isoformat(),sessions=len(active)))
            print(f'Batch {batch+1}/{plan["batches"]}: {len(active)} single-thread samplers released together for {plan["seconds"]}s',flush=True)
            last_resource=last_progress=-1;resource_failure=False
            while any('result' not in r for r in active):
                elapsed=(time.monotonic_ns()-start)/1e9
                if elapsed-last_resource>=1:
                    mem=memory_snapshot();samples={r['p'].pid:sample_resources(r['p'].pid) for r in active if 'result' not in r};rss=sum(int((v.get('VmRSS') or '0 kB').split()[0])*1024 for rows in samples.values() for v in rows)
                    if rss>20*GiB or mem['available']<2*GiB:
                        resource_failure=True
                        for r in active:
                            if 'result' not in r and r['termination'] is None:stop(r['p'].pid,signal.SIGTERM);r['termination']=elapsed
                    for r in active:
                        if 'result' not in r:
                            for proc in samples[r['p'].pid]:
                                if (proc.get('Name') or '').startswith('arith_coverage'):
                                    proc['thread_affinity']=thread_snapshot(int(proc['Pid']))
                                    try:proc['loaded_z3_paths']=sorted({line.split()[-1] for line in Path('/proc',proc['Pid'],'maps').read_text().splitlines() if 'libz3.so' in line})
                                    except (FileNotFoundError,ProcessLookupError,PermissionError):proc['loaded_z3_paths']=[]
                            with (r['dir']/'resource_samples.jsonl').open('a') as f:f.write(json.dumps(dict(elapsed=elapsed,processes=samples[r['p'].pid],aggregate_rss_bytes=rss,memory_available=mem['available'],resource_guard=resource_failure))+'\n')
                    with (out/'resources.jsonl').open('a') as f:f.write(json.dumps(dict(batch=batch,elapsed=elapsed,rss=rss,memory=mem,resource_guard=resource_failure))+'\n')
                    last_resource=elapsed
                for r in active:
                    if 'result' in r:continue
                    code=r['p'].poll()
                    if code is not None:
                        r['log'].close();result=r['spec']|dict(start_monotonic_ns=start,process_wall_seconds=elapsed,returncode=code,termination_sent_seconds=r['termination'],hard_kill=r['killed'],resource_stop=resource_failure,cleanup_after_deadline_seconds=max(0,elapsed-plan['seconds']));dump(r['dir']/'process_result.json',result);r['result']=result;results.append(result)
                    elif elapsed>=plan['seconds'] and r['termination'] is None:stop(r['p'].pid,signal.SIGTERM);r['termination']=elapsed
                    elif r['termination'] is not None and elapsed-r['termination']>=2 and not r['killed']:stop(r['p'].pid,signal.SIGKILL);r['killed']=True
                if elapsed-last_progress>=.5:
                    dump(out/'progress.json',dict(status='sampling',batch=batch,total_batches=plan['batches'],batch_elapsed=elapsed,total=plan["total_sessions"],completed=len(results),active=sum('result' not in r for r in active),queued=plan["total_sessions"]-len(results)-sum('result' not in r for r in active),sampler_limit=plan['max_concurrent_sessions'],supervisor_cpu=plan['supervisor_cpu']));last_progress=elapsed
                if any('result' not in r for r in active):time.sleep(.02)
            dump(out/'process_results.json',results)
            assert not resource_failure and all(r['result']['returncode']==0 and not r['killed'] for r in active),'batch failed; preserve partial results, no retry or seed replacement'
        for p,h in plan['files'].items():assert sha(ROOT/p)==h,'changed during frozen sampling: '+p
        dump(out/'sampling_complete.json',dict(passed=True,sessions=len(results),complete_utc=datetime.now(timezone.utc).isoformat()))
        dump(out/'progress.json',dict(status='sampling_complete',total=plan["total_sessions"],completed=plan["total_sessions"],active=0,queued=0))
    finally:
        for r in all_children:
            if r['p'].poll() is None:stop(r['p'].pid,signal.SIGKILL);r['p'].wait()
            r['log'].close()
        os.sched_setaffinity(0,allowed)

def main():
    if len(sys.argv)>1 and sys.argv[1]=='--gate':signal.signal(signal.SIGTERM,signal.SIG_IGN);gate(sys.argv[2]);return
    p=argparse.ArgumentParser();p.add_argument('--stage',choices=STAGES,required=True);p.add_argument('--out',type=Path);p.add_argument('--prepare',action='store_true');p.add_argument('--run',action='store_true');a=p.parse_args();os.chdir(ROOT);out=(a.out or OUT/a.stage).resolve();assert a.prepare!=a.run
    if a.prepare:prepare(a.stage,out);return
    try:sample(out,json.loads((out/'plan.json').read_text()))
    except Exception as e:dump(out/'failure.json',dict(error=repr(e),automatic_retry=False));raise
if __name__=='__main__':main()
