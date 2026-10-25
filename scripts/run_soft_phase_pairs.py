#!/usr/bin/env python3
"""Up to five concurrent synchronized triplets (15 single-core sessions); no evaluation/build during sampling."""
import argparse, hashlib, json, os, resource, signal, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CONFIGS=['plain','random_soft','coverage_soft']
INPUTS=[('SMT_random_LIA_ex14600_2600_100.smt2','114fb8ac7228c36cc6124bd9f2d95fd16a3bf2d330c39e185e8145d56821de1f'),('SMT_real_LIA_ex8500_2400_100.smt2','9ff3c643c1756057b1f127c5818bf38c468761ad3d2dd742cf34e3511e0690ec')]
def dump(p,obj):
    p=Path(p);p.write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n')
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def sample_resources(pid):
    pending=[pid];rows=[]
    while pending:
        current=pending.pop()
        try:
            children=Path(f'/proc/{current}/task/{current}/children').read_text().split()
            pending.extend(map(int,children))
            status=Path(f'/proc/{current}/status').read_text().splitlines()
            fields={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in status if ':' in line}
            rows.append({k:fields.get(k) for k in ['Name','Pid','VmRSS','VmHWM','VmSize','Threads']})
        except (FileNotFoundError,ProcessLookupError,PermissionError):pass
    return rows
def gate(spec_path):
    spec=json.loads(Path(spec_path).read_text());os.sched_setaffinity(0,set(spec.get('cpus',[spec['core']])))
    resource.setrlimit(resource.RLIMIT_AS,(6*1024**3,6*1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE,(1024**3,1024**3))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    dump(Path(spec_path).parent/'ready.json',dict(pid=os.getpid(),affinity=sorted(os.sched_getaffinity(0)),address_space_bytes=6*1024**3,per_file_bytes=1024**3))
    start=int(sys.stdin.readline());os.environ['BOFILL_START_MONO_NS']=str(start)
    for name in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:os.environ[name]='1'
    delay=(start-time.monotonic_ns())/1e9
    if delay>0:time.sleep(delay)
    dump(Path(spec_path).parent/'gate.json',dict(start_monotonic_ns=start,exec_monotonic_ns=time.monotonic_ns()))
    os.chdir(Path(spec_path).parent)
    os.execv('/usr/bin/time',['/usr/bin/time','-v','-o','resource.txt',*spec['command']])
def slot_layout(allowed):
    """Fifteen logical CPUs; each triplet uses distinct physical cores.

    SMT siblings are shared across groups and explicitly recorded. Mode/core
    assignment rotates by group ID, including the final partial wave.
    """
    by_physical={}
    for c in sorted(allowed):
        topo=Path(f'/sys/devices/system/cpu/cpu{c}/topology')
        identity=((topo/'physical_package_id').read_text().strip(),(topo/'core_id').read_text().strip())
        by_physical.setdefault(identity,[]).append(c)
    # Round-robin physical cores before taking another SMT sibling.
    ordered=[cpus[i] for i in range(max(map(len,by_physical.values()))) for cpus in by_physical.values() if i<len(cpus)]
    if len(ordered)<15:raise RuntimeError('15 permitted logical CPUs required')
    chosen=ordered[:15];slots=[chosen[i:i+3] for i in range(0,15,3)]
    def identity(c):return next(k for k,v in by_physical.items() if c in v)
    if any(len({identity(c) for c in cs})!=3 for cs in slots):raise RuntimeError('triplet must span three physical cores')
    return slots,[dict(cpu=c,package=identity(c)[0],physical_core=identity(c)[1]) for c in chosen],ordered[15:]

def execute_groups(jobs, slots, launch, collect, release, progress, interval=.02):
    """Refill only after a complete paired triplet finishes; never split a group."""
    pending=list(enumerate(jobs));active={};finished=0
    try:
        while pending or active:
            for slot,cores in enumerate(slots):
                if slot not in active and pending:
                    group,job=pending.pop(0);active[slot]=launch(group,job,slot,cores)
            for slot,batch in list(active.items()):
                if collect(batch):release(batch);del active[slot];finished+=1
            progress(active,len(pending),finished)
            if active:time.sleep(interval)
    finally:
        for batch in active.values():release(batch,abort=True)

def main():
    if len(sys.argv)>1 and sys.argv[1]=='--gate':gate(sys.argv[2]);return
    a=argparse.ArgumentParser();a.add_argument('--out',required=True);a.add_argument('--stage',choices=['exposure','screen'],required=True);a.add_argument('--postprocess',action='store_true');args=a.parse_args()
    seconds=30. if args.stage=='exposure' else 60.
    
    out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=False)
    allowed=os.sched_getaffinity(0);slots,identities,spare=slot_layout(allowed)
    cores=[c for cs in slots for c in cs]
    if spare:os.sched_setaffinity(0,{spare[0]})
    binaries={mode:ROOT/'build/soft_phase/release/soft_phase_sampler' for mode in CONFIGS}
    files={str(p.relative_to(ROOT)):sha(p) for p in [*binaries.values(),ROOT/'dependencies/patches/z3_soft_phase.patch',ROOT/'dependencies/soft_phase_build.json',ROOT/'scripts/run_soft_phase_pairs.py',ROOT/'scripts/audit_soft_phase_results.py',ROOT/'scripts/analyze_soft_phase_results.py',ROOT/'CMakeLists.txt',ROOT/'docs/覆盖软偏好实验协议.md']}
    for directory in ['src','include']:
        for p in sorted((ROOT/directory).glob('*')):
            if p.is_file():files[str(p.relative_to(ROOT))]=sha(p)
    inputs=[]
    (out/'inputs').mkdir()
    for name,expected in INPUTS:
        p=ROOT/'benchmarks'/name;assert sha(p)==expected
        dest=out/'inputs'/name;dest.write_bytes(p.read_bytes());inputs.append(dict(name=name,sha256=expected,path=str(dest)))
    manifest=dict(schema=1,kind=args.stage,seconds=seconds,seeds=[0,1,2] if args.stage=='screen' else [0],configurations=CONFIGS,cores=cores,cpu_topology=identities,group_slots=slots,max_concurrent_sessions=15,max_concurrent_groups=5,controller_core=spare[0] if spare else None,concurrency_authority="user explicitly requested full 15-way concurrency; synchronized means within-triplet",inputs=inputs,files=files,git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),git_status=subprocess.check_output(['git','status','--short','--untracked-files=no'],cwd=ROOT,text=True),z3_sha256=sha(ROOT/'dependencies/z3-soft-phase/install/lib/libz3.so'),created_utc=datetime.now(timezone.utc).isoformat(),limits=dict(address_space_gib=6,per_file_gib=1,cleanup_grace_seconds=2,model_count_cap=None),environment=subprocess.check_output(['lscpu'],text=True),memory=Path('/proc/meminfo').read_text(),library_links=subprocess.check_output(['ldd',str(binaries['plain'])],text=True))
    manifest['highdiv_execution']='disabled; historical background only'
    dump(out/'plan.json',manifest)
    results=[];jobs=[(entry,seed) for seed in manifest['seeds'] for entry in inputs]
    batches=[];last_progress=0
    def launch(group,job,slot,slot_cores):
        entry,seed=job
        for binary in set(binaries.values()):
            assert sha(binary)==files[str(binary.relative_to(ROOT))], 'frozen binary changed'
        batch=dict(group=group,slot=slot,formula=entry['name'],seed=seed,running=[],last_resource=-1.)
        batches.append(batch)
        for i,config in enumerate(CONFIGS):
            d=out/'runs'/f'{group:02d}_{config}';d.mkdir(parents=True)
            command=[str(binaries[config]),entry['path'],config,str(seconds),str(seed),str(d/'raw')]
            spec=dict(group=group,slot=slot,formula=entry['name'],seed=seed,configuration=config,core=slot_cores[(i+group)%3],command=command,seconds=seconds)
            dump(d/'spec.json',spec);log=(d/'process.log').open('wb')
            p=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--gate',str(d/'spec.json')],stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            batch['running'].append(dict(p=p,dir=d,log=log,spec=spec,termination_sent=None,killed=False))
        wait_end=time.monotonic()+20
        while not all((r['dir']/'ready.json').exists() for r in batch['running']):
            if time.monotonic()>wait_end or any(r['p'].poll() is not None for r in batch['running']):raise RuntimeError('gate preparation failed')
            for existing in batches:
                if 'start' in existing:collect(existing)
            time.sleep(.02)
        start=time.monotonic_ns()+100_000_000;batch['start']=start
        for r in batch['running']:r['p'].stdin.write(f'{start}\n'.encode());r['p'].stdin.flush();r['p'].stdin.close()
        print(f"group {group} slot={slot}: {entry['name']} seed={seed}; synchronized triplet; budget={seconds}s",flush=True)
        return batch
    def collect(batch):
        elapsed=(time.monotonic_ns()-batch['start'])/1e9
        if elapsed-batch['last_resource']>=1:
            for r in batch['running']:
                if 'result' not in r:
                    with (r['dir']/'resource_samples.jsonl').open('a') as monitor:monitor.write(json.dumps(dict(elapsed=elapsed,processes=sample_resources(r['p'].pid)))+'\n')
            batch['last_resource']=elapsed
        for r in batch['running']:
            if 'result' in r:continue
            code=r['p'].poll()
            if code is not None:
                r['log'].close();result={**r['spec'],'start_monotonic_ns':batch['start'],'process_wall_seconds':elapsed,'returncode':code,'termination_sent_seconds':r['termination_sent'],'hard_kill':r['killed'],'cleanup_after_deadline_seconds':max(0,elapsed-seconds)}
                dump(r['dir']/'process_result.json',result);r['result']=result;results.append(result)
            elif elapsed>=seconds and r['termination_sent'] is None:
                try:os.killpg(r['p'].pid,signal.SIGTERM)
                except ProcessLookupError:pass
                r['termination_sent']=elapsed
            elif elapsed>=seconds+2 and not r['killed']:
                try:os.killpg(r['p'].pid,signal.SIGKILL)
                except ProcessLookupError:pass
                r['killed']=True
        return all('result' in r for r in batch['running'])
    def release(batch,abort=False):
        for r in batch['running']:
            if r['p'].poll() is None:
                try:os.killpg(r['p'].pid,signal.SIGKILL)
                except ProcessLookupError:pass
                r['p'].wait()
            r['log'].close()
        if not abort:
            dump(out/'process_results.json',results)
            for r in batch['running']:
                if r['result']['returncode']!=0 and r['termination_sent'] is None:
                    raise RuntimeError(f"implementation or resource failure: {r['dir']} (preserved logs)")
    def progress(active,queued,finished):
        nonlocal last_progress
        now=time.monotonic()
        if now-last_progress>=.5 or not active:
            dump(out/'progress.json',dict(status='sampling',max_concurrent_sessions=15,active_groups=[dict(group=b['group'],slot=b['slot'],formula=b['formula'],seed=b['seed'],start_monotonic_ns=b['start'],active_processes=sum(r['p'].poll() is None for r in b['running'])) for b in active.values()],queued_groups=queued,finished_groups=finished,total_groups=len(jobs)))
            last_progress=now
    try:
        execute_groups(jobs,slots,launch,collect,release,progress)
    except Exception as exc:
        dump(out/'progress.json',dict(status='failed',error=str(exc)));raise
    finally:
        # Also covers a partially prepared group whose gate failed in launch().
        for batch in batches:
            if any(r['p'].poll() is None for r in batch['running']):release(batch,abort=True)
    dump(out/'progress.json',dict(status='sampling_complete',groups=len(jobs),completed_utc=datetime.now(timezone.utc).isoformat()))
    if args.postprocess:
        env=dict(os.environ,HIGHDIV_ROOT='/tmp/lia-highdiv-frozen');python='/tmp/lia-highdiv-frozen/venv/bin/python3'
        os.sched_setaffinity(0,allowed)
        subprocess.run([python,str(ROOT/'scripts/audit_soft_phase_results.py'),str(out),'--jobs','15'],env=env,check=True)
        subprocess.run([python,str(ROOT/'scripts/analyze_soft_phase_results.py'),str(out)],env=env,check=True)
        subprocess.run([python,str(ROOT/'scripts/archive_bofill_results.py'),str(out)],env=env,check=True)
        dump(out/'progress.json',dict(status='audit_and_analysis_complete',groups=len(jobs),completed_utc=datetime.now(timezone.utc).isoformat()))
if __name__=='__main__':main()
