#!/usr/bin/env python3
"""Fixed three-slot synchronized soft-phase triplets; no evaluation/build during sampling."""
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
def main():
    if len(sys.argv)>1 and sys.argv[1]=='--gate':gate(sys.argv[2]);return
    a=argparse.ArgumentParser();a.add_argument('--out',required=True);a.add_argument('--stage',choices=['exposure','screen'],required=True);a.add_argument('--postprocess',action='store_true');args=a.parse_args()
    seconds=30. if args.stage=='exposure' else 60.
    
    out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=False)
    allowed=os.sched_getaffinity(0);cores=[0,2,4];assert set(cores).issubset(allowed)
    # Verify the chosen logical CPUs are three distinct physical cores.
    identities=[(Path(f'/sys/devices/system/cpu/cpu{c}/topology/physical_package_id').read_text().strip(),Path(f'/sys/devices/system/cpu/cpu{c}/topology/core_id').read_text().strip()) for c in cores]
    assert len(set(identities))==3
    if 6 in allowed:os.sched_setaffinity(0,{6})
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
    manifest=dict(schema=1,kind=args.stage,seconds=seconds,seeds=[0,1,2] if args.stage=='screen' else [0],configurations=CONFIGS,cores=cores,physical_cores=identities,inputs=inputs,files=files,git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),git_status=subprocess.check_output(['git','status','--short','--untracked-files=no'],cwd=ROOT,text=True),z3_sha256=sha(ROOT/'dependencies/z3-soft-phase/install/lib/libz3.so'),created_utc=datetime.now(timezone.utc).isoformat(),limits=dict(address_space_gib=6,per_file_gib=1,cleanup_grace_seconds=2,model_count_cap=None),environment=subprocess.check_output(['lscpu'],text=True),memory=Path('/proc/meminfo').read_text(),library_links=subprocess.check_output(['ldd',str(binaries['plain'])],text=True))
    manifest['highdiv_execution']='disabled; historical background only'
    dump(out/'plan.json',manifest)
    results=[];group=0
    try:
        for entry in inputs:
            for seed in manifest['seeds']:
                for binary in set(binaries.values()):
                    assert sha(binary)==files[str(binary.relative_to(ROOT))], 'frozen binary changed'
                running=[];start=None
                for i,config in enumerate(CONFIGS):
                    d=out/'runs'/f'{group:02d}_{config}';d.mkdir(parents=True)
                    raw=d/'raw'
                    command=[str(binaries[config]),entry['path'],config,str(seconds),str(seed),str(raw)]
                    spec=dict(group=group,formula=entry['name'],seed=seed,configuration=config,core=cores[(i+group)%3],command=command,seconds=seconds)
                    dump(d/'spec.json',spec);log=(d/'process.log').open('wb')
                    p=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--gate',str(d/'spec.json')],stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                    running.append(dict(p=p,dir=d,log=log,spec=spec,termination_sent=None,killed=False))
                wait_end=time.monotonic()+20
                while not all((r['dir']/'ready.json').exists() for r in running):
                    if time.monotonic()>wait_end or any(r['p'].poll() is not None for r in running):raise RuntimeError('gate preparation failed')
                    time.sleep(.02)
                start=time.monotonic_ns()+100_000_000
                for r in running:r['p'].stdin.write(f'{start}\n'.encode());r['p'].stdin.flush();r['p'].stdin.close()
                dump(out/'progress.json',dict(status='sampling',group=group,total_groups=len(inputs)*len(manifest['seeds']),formula=entry['name'],seed=seed,start_monotonic_ns=start,deadline_monotonic_ns=start+int(seconds*1e9)))
                print(f'group {group}: {entry["name"]} seed={seed} synchronized; budget={seconds}s',flush=True)
                last_resource=-1.
                while any('result' not in r for r in running):
                    now=time.monotonic_ns();elapsed=(now-start)/1e9
                    if elapsed-last_resource>=1:
                        for r in running:
                            if 'result' not in r:
                                with (r['dir']/'resource_samples.jsonl').open('a') as monitor:monitor.write(json.dumps(dict(elapsed=elapsed,processes=sample_resources(r['p'].pid)))+'\n')
                        last_resource=elapsed
                    for r in running:
                        if 'result' in r:continue
                        code=r['p'].poll()
                        if code is not None:
                            r['log'].close();result={**r['spec'], 'start_monotonic_ns':start,'process_wall_seconds':elapsed,'returncode':code,'termination_sent_seconds':r['termination_sent'],'hard_kill':r['killed'],'cleanup_after_deadline_seconds':max(0,elapsed-seconds)}
                            dump(r['dir']/'process_result.json',result);r['result']=result;results.append(result)
                        elif elapsed>=seconds and r['termination_sent'] is None:
                            os.killpg(r['p'].pid,signal.SIGTERM);r['termination_sent']=elapsed
                        elif elapsed>=seconds+2 and not r['killed']:
                            os.killpg(r['p'].pid,signal.SIGKILL);r['killed']=True
                    time.sleep(.02)
                dump(out/'process_results.json',results)
                for r in running:
                    if r['result']['returncode']>0 and r['termination_sent'] is None:
                        raise RuntimeError(f"implementation or resource failure: {r['dir']} (inspect preserved logs before further runs)")
                group+=1
    except Exception as exc:
        dump(out/'progress.json',dict(status='failed',group=group,error=str(exc)))
        raise
    finally:
        for r in locals().get('running',[]):
            if r['p'].poll() is None:
                os.killpg(r['p'].pid,signal.SIGKILL);r['p'].wait()
            r['log'].close()
    dump(out/'progress.json',dict(status='sampling_complete',groups=group,completed_utc=datetime.now(timezone.utc).isoformat()))
    if args.postprocess:
        env=dict(os.environ,HIGHDIV_ROOT='/tmp/lia-highdiv-frozen');python='/tmp/lia-highdiv-frozen/venv/bin/python3'
        os.sched_setaffinity(0,allowed)
        subprocess.run([python,str(ROOT/'scripts/audit_soft_phase_results.py'),str(out),'--jobs','3'],env=env,check=True)
        subprocess.run([python,str(ROOT/'scripts/analyze_soft_phase_results.py'),str(out)],env=env,check=True)
        subprocess.run([python,str(ROOT/'scripts/archive_bofill_results.py'),str(out)],env=env,check=True)
        dump(out/'progress.json',dict(status='audit_and_analysis_complete',groups=group,completed_utc=datetime.now(timezone.utc).isoformat()))
if __name__=='__main__':main()
