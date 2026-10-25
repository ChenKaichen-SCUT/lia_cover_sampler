#!/usr/bin/env python3
"""One synchronous quadruplet on four physical cores, barrier between groups."""
import argparse, hashlib, json, os, resource, signal, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CONFIGS=['plain','catalog_random_one','native_random_one','native_feedback_one']
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
    physical={}
    for cpu in sorted(allowed):
        p=Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
        key=((p/'physical_package_id').read_text().strip(),(p/'core_id').read_text().strip())
        physical.setdefault(key,[]).append(cpu)
    if len(physical)<5:raise RuntimeError('four distinct physical sampler cores plus controller core required')
    keys=list(physical);cores=[physical[k][0] for k in keys[:4]]
    topology=[dict(cpu=c,package=k[0],physical_core=k[1]) for k in keys for c in physical[k]]
    return [cores],topology,[physical[keys[4]][0]]

def execute_groups(jobs, slots, launch, collect, release, progress, interval=.02):
    """Refill only after a complete paired quadruplet finishes; never split a group."""
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
    seconds=30. if args.stage=='exposure' else 120.
    
    out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=False)
    allowed=os.sched_getaffinity(0);slots,identities,spare=slot_layout(allowed)
    cores=[c for cs in slots for c in cs]
    if spare:os.sched_setaffinity(0,{spare[0]})
    os.environ.pop('NATIVE_TEST_MAX_QUERIES',None)
    binaries={mode:ROOT/'build/native_feedback/release/native_feedback_sampler' for mode in CONFIGS}
    files={str(p.relative_to(ROOT)):sha(p) for p in [*binaries.values(),ROOT/'dependencies/patches/z3_native_feedback_phase.patch',ROOT/'dependencies/patches/z3_soft_phase.patch',ROOT/'dependencies/native_feedback_build.json',ROOT/'scripts/run_native_feedback_pairs.py',ROOT/'scripts/audit_native_feedback_results.py',ROOT/'scripts/analyze_native_feedback_results.py',ROOT/'CMakeLists.txt',ROOT/'docs/原生决策反馈实验协议.md']}
    for directory in ['src','include']:
        for p in sorted((ROOT/directory).glob('*')):
            if p.is_file():files[str(p.relative_to(ROOT))]=sha(p)
    inputs=[]
    (out/'inputs').mkdir()
    for name,expected in INPUTS:
        p=ROOT/'benchmarks'/name;assert sha(p)==expected
        dest=out/'inputs'/name;dest.write_bytes(p.read_bytes());inputs.append(dict(name=name,sha256=expected,path=str(dest)))
    manifest=dict(schema=1,kind=args.stage,seconds=seconds,seeds=[0,1,2] if args.stage=='screen' else [0],configurations=CONFIGS,cores=cores,cpu_topology=identities,group_slots=slots,max_concurrent_sessions=4,max_concurrent_groups=1,controller_core=spare[0] if spare else None,concurrency_authority="new guide: one quadruplet on four distinct physical cores; no SMT sharing",inputs=inputs,files=files,git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),git_status=subprocess.check_output(['git','status','--short','--untracked-files=no'],cwd=ROOT,text=True),z3_sha256=sha(ROOT/'dependencies/z3-native-feedback-phase/install/lib/libz3.so'),created_utc=datetime.now(timezone.utc).isoformat(),limits=dict(address_space_gib=6,per_file_gib=1,cleanup_grace_seconds=2,model_count_cap=None),environment=subprocess.check_output(['lscpu'],text=True),memory=Path('/proc/meminfo').read_text(),library_links=subprocess.check_output(['ldd',str(binaries['plain'])],text=True))
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
            spec=dict(group=group,slot=slot,formula=entry['name'],seed=seed,configuration=config,core=slot_cores[(i+group)%4],command=command,seconds=seconds)
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
        print(f"group {group} slot={slot}: {entry['name']} seed={seed}; synchronized quadruplet; budget={seconds}s",flush=True)
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
            dump(out/'progress.json',dict(status='sampling',max_concurrent_sessions=4,active_groups=[dict(group=b['group'],slot=b['slot'],formula=b['formula'],seed=b['seed'],start_monotonic_ns=b['start'],active_processes=sum(r['p'].poll() is None for r in b['running'])) for b in active.values()],queued_groups=queued,finished_groups=finished,total_groups=len(jobs)))
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
        subprocess.run([python,str(ROOT/'scripts/audit_native_feedback_results.py'),str(out),'--jobs','15'],env=env,check=True)
        subprocess.run([python,str(ROOT/'scripts/analyze_native_feedback_results.py'),str(out)],env=env,check=True)
        subprocess.run([python,str(ROOT/'scripts/archive_bofill_results.py'),str(out)],env=env,check=True)
        dump(out/'progress.json',dict(status='audit_and_analysis_complete',groups=len(jobs),completed_utc=datetime.now(timezone.utc).isoformat()))
if __name__=='__main__':main()
