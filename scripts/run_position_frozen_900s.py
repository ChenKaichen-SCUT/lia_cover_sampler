#!/usr/bin/env python3
"""Explicitly authorized frozen 900s experiment: 14 symmetric pairs, no gate."""
import argparse,json,os,re,signal,subprocess,sys,time,hashlib
from datetime import datetime,timezone
from pathlib import Path
from run_native_feedback_pairs import ROOT,INPUTS,dump,sha,gate,sample_resources

BRANCH='position-frozen-900s'
BASE='75cd6b6'
CONFIGS=['first_immediate','position_immediate']
SEEDS=list(range(3,10))
SECONDS=900
BINARY=ROOT/'build/native_delivery/release/native_delivery_sampler'
LIBRARY=ROOT/'dependencies/z3-native-feedback-phase/install/lib/libz3.so'
PY='/tmp/lia-highdiv-frozen/venv/bin/python3'
PROTOCOL=ROOT/'docs/原生干预位置900秒新seed冻结协议.md'
GiB=1024**3

def verify_algorithm():
    proof=json.loads(subprocess.check_output(['git','show',BASE+':results/native_delivery_validation/summary.json'],cwd=ROOT,text=True))
    files={p:h for p,h in proof['frozen_files'].items() if p.startswith(('src/','include/','build/','dependencies/')) or p=='CMakeLists.txt'}
    assert not subprocess.check_output(['git','diff',BASE,'--','src','include','CMakeLists.txt','dependencies/patches'],cwd=ROOT),'algorithm sources differ from frozen commit'
    for p,h in files.items():assert sha(ROOT/p)==h,'algorithm identity changed: '+p
    return files

def topology(allowed=None):
    allowed=sorted(os.sched_getaffinity(0) if allowed is None else allowed);cores={};rows=[]
    for cpu in allowed:
        p=Path(f'/sys/devices/system/cpu/cpu{cpu}/topology');key=(int((p/'physical_package_id').read_text()),int((p/'core_id').read_text()))
        cores.setdefault(key,[]).append(cpu);rows.append(dict(cpu=cpu,package=key[0],physical_core=key[1]))
    full=[sorted(v) for _,v in sorted(cores.items()) if len(v)>=2]
    assert len(full)>=3,'need separate paired worker cores and a reserved supervisor core'
    return full[:-1],full[-1][:2],rows

def memory_snapshot():
    mem={l.split(':')[0]:int(l.split()[1])*1024 for l in Path('/proc/meminfo').read_text().splitlines() if l.split(':')[0] in ['MemAvailable','MemTotal','SwapTotal','SwapFree']}
    cg={}
    for name in ['memory.max','memory.current','memory.swap.max','memory.events','cpu.max','cpuset.cpus.effective']:
        p=Path('/sys/fs/cgroup')/name;cg[name]=p.read_text().strip() if p.exists() else None
    limit=int(cg['memory.max']) if cg['memory.max'] and cg['memory.max']!='max' else mem['MemTotal']
    current=int(cg['memory.current'] or 0);return dict(host=mem,cgroup=cg,available=min(mem['MemAvailable'],max(0,limit-current)))

def memory_capacity(available,peak_rss,physical_groups):
    per=max(GiB,4*peak_rss);groups=min(7,physical_groups,max(0,(available-3*GiB)//(2*per)))
    assert groups>=1,'insufficient memory before any sampling'
    return int(groups),dict(per_process_envelope=per,reserve=3*GiB,available=available,predicted_workers=int(groups)*2,predicted_worker_bytes=int(groups)*2*per)

def task_matrix(inputs,workers,groups):
    assert 1<=groups<=7 and len(workers)>=max(groups,2)
    pairs=[(entry,seed) for seed in SEEDS for entry in inputs];jobs=[]
    for g,(entry,seed) in enumerate(pairs):
        batch,j=divmod(g,groups);rotation=(3*batch)%groups;lane=batch%2;shift=1 if batch%2==0 else 2
        if groups>1 and shift%groups==0:shift=1
        for side,config in enumerate(CONFIGS):
            if groups==1:core=workers[side][(lane+side)%2]
            else:core=workers[(j+rotation+(shift if side else 0))%groups][(lane+side)%2]
            jobs.append(dict(batch=batch,group=g,formula=entry['name'],input=entry['path'],seed=seed,configuration=config,seconds=SECONDS,core=core))
    return jobs

def prepare(out):
    assert subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()==BRANCH
    assert not subprocess.check_output(['git','status','--short','--untracked-files=no'],cwd=ROOT,text=True).strip(),'commit code/protocol/validation first'
    algorithm=verify_algorithm();validation=ROOT/'results/position_frozen_900s_validation/summary.json';v=json.loads(validation.read_text());assert v['passed']
    for p,h in v['files'].items():assert sha(ROOT/p)==h,'changed after validation: '+p
    out.mkdir(parents=True,exist_ok=False);(out/'inputs').mkdir();history=[]
    for base in ['results/native_delivery/screen_120s','results/native_frozen_900s']:
        manifest=json.loads((ROOT/base/'archive_manifest.json').read_text())
        for entry in manifest['runs']:
            rel='runs/'+entry['run']+'/resource.txt';src=ROOT/base/rel
            assert src.is_file(),'restore reference resource logs into a separate directory before preparation: '+str(src)
            assert sha(src)==entry['content_sha256'][rel],'historical resource hash'
            text=src.read_text();peak=int(next(l for l in text.splitlines() if 'Maximum resident set size' in l).split(':')[-1])*1024
            dst=out/'resource_reference'/Path(base).name/(entry['run']+'.txt');dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(src.read_bytes())
            history.append(dict(source=str(src.relative_to(ROOT)),sha256=sha(src),copy=str(dst.relative_to(out)),peak_rss_bytes=peak,archive_manifest=str((ROOT/base/'archive_manifest.json').relative_to(ROOT)),archive_manifest_sha256=sha(ROOT/base/'archive_manifest.json')))
    assert len(history)==32
    mem=memory_snapshot();workers,reserved,cpu=topology();groups,capacity=memory_capacity(mem['available'],max(r['peak_rss_bytes'] for r in history),len(workers))
    inputs=[]
    for name,h in INPUTS:
        src=ROOT/'benchmarks'/name;assert sha(src)==h;dest=out/'inputs'/name;dest.write_bytes(src.read_bytes());inputs.append(dict(name=name,sha256=h,path=str(dest)))
    jobs=task_matrix(inputs,workers,groups)
    prior=json.loads((ROOT/'results/native_delivery/screen_120s/plan.json').read_text());assert not(set(prior['seeds'])&set(SEEDS)) and not (ROOT/'results/native_delivery/long_900s/plan.json').exists(),'new seed condition changed'
    files=dict(v['files'])|algorithm|{str(PROTOCOL.relative_to(ROOT)):sha(PROTOCOL),str(validation.relative_to(ROOT)):sha(validation)}
    for folder in ['scripts','python']:
        files.update({str(p.relative_to(ROOT)):sha(p) for p in sorted((ROOT/folder).glob('*.py'))})
    plan=dict(schema=1,kind='long',experiment='position_frozen_900s',seconds=SECONDS,seeds=SEEDS,configurations=CONFIGS,total_sessions=28,inputs=inputs,jobs=jobs,files=files,algorithm_commit=BASE,preparation_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),created_utc=datetime.now(timezone.utc).isoformat(),requested_threads=15,max_concurrent_sessions=2*groups,concurrent_pair_groups=groups,batches=(14+groups-1)//groups,worker_physical_cores=workers[:max(groups,2)],supervisor_cpu=reserved[0],reserved_cpu=reserved[1],cpu_topology=cpu,lscpu=subprocess.check_output(['lscpu','-e=CPU,CORE,SOCKET,ONLINE'],text=True),affinity=sorted(os.sched_getaffinity(0)),memory=mem,memory_capacity=capacity,resource_reference=history,disk_usage=subprocess.check_output(['df','-B1',str(out)],text=True),library_links=subprocess.check_output(['ldd',str(BINARY)],text=True),highdiv_execution=False,authorization='new explicit user guide overrides prior failed automatic promotion gate',criteria=dict(endpoint_wins_each_formula=5,endpoint_pairs_each_formula=7,endpoint_positive_median=True,repeated_early_losses=4,extra_experiments=False),limits=dict(address_space_gib=6,per_file_gib=1,cleanup_seconds=2,total_rss_stop_gib=20,minimum_free_memory_gib=2))
    dump(out/'plan.json',plan);print(json.dumps(dict(prepared=True,sessions=28,concurrent=2*groups,batches=plan['batches'],capacity=capacity)))

def stop(pid,sig):
    try:os.killpg(pid,sig)
    except ProcessLookupError:pass

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
                spec['command']=[str(BINARY),spec.pop('input'),spec['configuration'],str(SECONDS),str(spec['seed']),str(d/'raw')];dump(d/'spec.json',spec)
                log=(d/'process.log').open('wb');p=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--gate',str(d/'spec.json')],stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                r=dict(p=p,dir=d,spec=spec,log=log,termination=None,killed=False);active.append(r);all_children.append(r)
            deadline=time.monotonic()+30
            while not all((r['dir']/'ready.json').exists() for r in active):
                assert time.monotonic()<deadline and all(r['p'].poll() is None for r in active),'gate setup failed';time.sleep(.02)
            start=time.monotonic_ns()+200_000_000
            for r in active:r['p'].stdin.write(f'{start}\n'.encode());r['p'].stdin.flush();r['p'].stdin.close()
            dump(out/f'batch_{batch}_start.json',dict(batch=batch,start_monotonic_ns=start,utc=datetime.now(timezone.utc).isoformat(),sessions=len(active)))
            print(f'Batch {batch+1}/{plan["batches"]}: {len(active)} single-thread samplers released together for 900s',flush=True)
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
                            with (r['dir']/'resource_samples.jsonl').open('a') as f:f.write(json.dumps(dict(elapsed=elapsed,processes=samples[r['p'].pid],aggregate_rss_bytes=rss,memory_available=mem['available'],resource_guard=resource_failure))+'\n')
                    with (out/'resources.jsonl').open('a') as f:f.write(json.dumps(dict(batch=batch,elapsed=elapsed,rss=rss,memory=mem,resource_guard=resource_failure))+'\n')
                    last_resource=elapsed
                for r in active:
                    if 'result' in r:continue
                    code=r['p'].poll()
                    if code is not None:
                        r['log'].close();result=r['spec']|dict(start_monotonic_ns=start,process_wall_seconds=elapsed,returncode=code,termination_sent_seconds=r['termination'],hard_kill=r['killed'],resource_stop=resource_failure,cleanup_after_deadline_seconds=max(0,elapsed-SECONDS));dump(r['dir']/'process_result.json',result);r['result']=result;results.append(result)
                    elif elapsed>=SECONDS and r['termination'] is None:stop(r['p'].pid,signal.SIGTERM);r['termination']=elapsed
                    elif r['termination'] is not None and elapsed-r['termination']>=2 and not r['killed']:stop(r['p'].pid,signal.SIGKILL);r['killed']=True
                if elapsed-last_progress>=.5:
                    dump(out/'progress.json',dict(status='sampling',batch=batch,total_batches=plan['batches'],batch_elapsed=elapsed,total=28,completed=len(results),active=sum('result' not in r for r in active),queued=28-len(results)-sum('result' not in r for r in active),sampler_limit=plan['max_concurrent_sessions'],supervisor_cpu=plan['supervisor_cpu']));last_progress=elapsed
                if any('result' not in r for r in active):time.sleep(.02)
            dump(out/'process_results.json',results)
            assert not resource_failure and all(r['result']['returncode']==0 and not r['killed'] for r in active),'batch failed; preserve partial results, no retry or seed replacement'
        for p,h in plan['files'].items():assert sha(ROOT/p)==h,'changed during frozen sampling: '+p
        dump(out/'sampling_complete.json',dict(passed=True,sessions=len(results),complete_utc=datetime.now(timezone.utc).isoformat()))
        dump(out/'progress.json',dict(status='sampling_complete',total=28,completed=28,active=0,queued=0))
    finally:
        for r in all_children:
            if r['p'].poll() is None:stop(r['p'].pid,signal.SIGKILL);r['p'].wait()
            r['log'].close()
        os.sched_setaffinity(0,allowed)

def postprocess(out,publish):
    assert json.loads((out/'sampling_complete.json').read_text())['passed'];env=dict(os.environ,HIGHDIV_ROOT='/tmp/lia-highdiv-frozen',PYTHONPATH='scripts:python')
    commands=[('audit',[PY,'scripts/audit_position_frozen_900s.py',str(out),'--jobs','15']),('analysis',[PY,'scripts/analyze_position_frozen_900s.py',str(out)]),('figures',['python3','scripts/plot_position_frozen_900s.py',str(out)]),('archive',[PY,'scripts/archive_bofill_results.py',str(out)])]
    for name,cmd in commands:
        dump(out/'progress.json',dict(status=name,total=28,completed=28,active=0,queued=0))
        with (out/(name+'.log')).open('w') as f:subprocess.run(cmd,env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
    evidence={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and 'runs' not in p.relative_to(out).parts and p.name not in ['progress.json','evidence_manifest.json']}
    dump(out/'evidence_manifest.json',dict(files=evidence,algorithm_commit=BASE,archived_raw_models=True))
    dump(out/'progress.json',dict(status='complete',total=28,completed=28,active=0,queued=0,additional_experiments=False))
    if publish:
        with (out/'publication.log').open('w') as f:
            subprocess.run(['git','add','--',str(out.relative_to(ROOT)),'docs/原生干预位置900秒新seed验证报告.md'],stdout=f,stderr=subprocess.STDOUT,check=True)
            subprocess.run(['git','commit','-m','Archive frozen 900s position validation on seven new seeds'],stdout=f,stderr=subprocess.STDOUT,check=True)
            subprocess.run(['git','push','-u','origin',BRANCH],stdout=f,stderr=subprocess.STDOUT,check=True)
        dump(out/'publication_receipt.local.json',dict(pushed=True,commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),branch=BRANCH,utc=datetime.now(timezone.utc).isoformat()))

def main():
    if len(sys.argv)>1 and sys.argv[1]=='--gate':signal.signal(signal.SIGTERM,signal.SIG_IGN);gate(sys.argv[2]);return
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=ROOT/'results/position_frozen_900s');p.add_argument('--prepare',action='store_true');p.add_argument('--run',action='store_true');p.add_argument('--postprocess',action='store_true');p.add_argument('--push-results',action='store_true');a=p.parse_args();os.chdir(ROOT);out=a.out.resolve()
    assert a.prepare!=a.run,'choose prepare or run'
    if a.prepare:prepare(out);return
    try:
        sample(out,json.loads((out/'plan.json').read_text()))
        if a.postprocess:postprocess(out,a.push_results)
    except Exception as exc:dump(out/'failure.json',dict(error=repr(exc),automatic_retry=False));raise
if __name__=='__main__':main()
