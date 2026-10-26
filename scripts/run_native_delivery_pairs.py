#!/usr/bin/env python3
"""Frozen quadruplets on four physical cores. No audits/builds during sampling."""
import argparse,json,os,signal,subprocess,sys,time
from pathlib import Path
from datetime import datetime,timezone
from run_native_feedback_pairs import ROOT,INPUTS,dump,sha,gate,slot_layout,execute_groups,sample_resources
CONFIGS=['first_immediate','first_batch','position_immediate','position_batch']
BINARY=ROOT/'build/native_delivery/release/native_delivery_sampler'
LIBRARY=ROOT/'dependencies/z3-native-feedback-phase/install/lib/libz3.so'
BRANCH='native-position-bounded-delivery'

def tasks_for_stage(inputs,stage,candidate=None):
    seeds={'exposure':[0],'screen':[0,1,2],'long':[3,4]}[stage]
    if stage=='long':
        assert candidate in CONFIGS[1:],'one approved nonbaseline configuration required'
        return [[(entry,seed,c) for entry in inputs for c in ['first_immediate',candidate]] for seed in seeds]
    return [[(entry,seed,c) for c in CONFIGS] for seed in seeds for entry in inputs]

def main():
    if len(sys.argv)>1 and sys.argv[1]=='--gate':signal.signal(signal.SIGTERM,signal.SIG_IGN);gate(sys.argv[2]);return
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--stage',choices=['exposure','screen','long'],required=True);p.add_argument('--decision',type=Path);p.add_argument('--postprocess',action='store_true');a=p.parse_args();os.chdir(ROOT)
    assert subprocess.check_output(['git','branch','--show-current'],text=True).strip()==BRANCH
    assert not subprocess.check_output(['git','status','--short','--untracked-files=no'],text=True).strip(),'freeze tracked changes before sampling'
    seconds={'exposure':30,'screen':120,'long':900}[a.stage];seeds={'exposure':[0],'screen':[0,1,2],'long':[3,4]}[a.stage];configs=CONFIGS
    decision=None
    if a.stage=='long':
        assert a.decision is not None;decision=json.loads(a.decision.read_text());assert decision['enter_900'] and decision['selected'] in CONFIGS[1:] and decision['stage']=='screen' and decision['audit_passed'];configs=['first_immediate',decision['selected']]
        for path,wanted in decision['frozen_files'].items():assert sha(ROOT/path)==wanted,'screening artifact changed: '+path
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=False);(out/'inputs').mkdir();allowed=os.sched_getaffinity(0);slots,topology,spare=slot_layout(allowed)
    for env in ['NATIVE_TEST_MAX_QUERIES','BOFILL_START_MONO_NS']:os.environ.pop(env,None)
    frozen=[BINARY,LIBRARY,ROOT/'CMakeLists.txt',ROOT/'docs/有界交付与干预位置实验协议.md',Path(__file__),ROOT/'scripts/audit_native_delivery_results.py',ROOT/'scripts/analyze_native_delivery_results.py']
    for directory in ['src','include']:frozen.extend(p for p in sorted((ROOT/directory).glob('*')) if p.is_file())
    files={str(p.relative_to(ROOT)):sha(p) for p in frozen};inputs=[]
    if decision:assert decision['binary_sha256']==sha(BINARY),'candidate changed after screening'
    for name,expected in INPUTS:
        source=ROOT/'benchmarks'/name;assert sha(source)==expected;target=out/'inputs'/name;target.write_bytes(source.read_bytes());inputs.append(dict(name=name,sha256=expected,path=str(target)))
    jobs=tasks_for_stage(inputs,a.stage,decision['selected'] if decision else None)
    manifest=dict(schema=1,kind=a.stage,seconds=seconds,seeds=seeds,configurations=configs,inputs=inputs,files=files,git_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),max_concurrent_sessions=4,group_slots=slots,controller_core=spare[0],cpu_topology=topology,environment=subprocess.check_output(['lscpu'],text=True),memory=Path('/proc/meminfo').read_text(),library_links=subprocess.check_output(['ldd',str(BINARY)],text=True),created_utc=datetime.now(timezone.utc).isoformat(),highdiv_execution='forbidden; post-sampling read-only reference only',total_sessions=sum(map(len,jobs)),decision=decision,limits=dict(address_space_gib=6,per_file_gib=1,cleanup_grace_seconds=2))
    dump(out/'plan.json',manifest);results=[];batches=[];last_progress=0;os.sched_setaffinity(0,{spare[0]})
    def launch(group,tasks,slot,cores):
        assert sha(BINARY)==files[str(BINARY.relative_to(ROOT))],'binary changed';batch=dict(group=group,slot=slot,running=[],last_resource=-1);batches.append(batch)
        for i,(entry,seed,config) in enumerate(tasks):
            short='random' if 'random' in entry['name'] else 'real';d=out/'runs'/f'{group:02d}_{short}_{config}';d.mkdir(parents=True)
            spec=dict(group=group,formula=entry['name'],seed=seed,configuration=config,seconds=seconds,core=cores[(i+group)%4],command=[str(BINARY),entry['path'],config,str(seconds),str(seed),str(d/'raw')]);dump(d/'spec.json',spec)
            log=(d/'process.log').open('wb');process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--gate',str(d/'spec.json')],stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            batch['running'].append(dict(p=process,dir=d,spec=spec,log=log,termination=None,killed=False))
        deadline=time.monotonic()+30
        while not all((r['dir']/'ready.json').exists() for r in batch['running']):
            assert time.monotonic()<deadline and all(r['p'].poll() is None for r in batch['running']),'gate failed';time.sleep(.02)
        start=time.monotonic_ns()+100_000_000;batch['start']=start
        for r in batch['running']:r['p'].stdin.write(f'{start}\n'.encode());r['p'].stdin.flush();r['p'].stdin.close()
        print(f'{a.stage} group {group+1}/{len(jobs)}: {[(x[0]["name"],x[1],x[2]) for x in tasks]}, {seconds}s',flush=True);return batch
    def collect(batch):
        elapsed=(time.monotonic_ns()-batch['start'])/1e9
        if elapsed-batch['last_resource']>=1:
            for r in batch['running']:
                if 'result' not in r:
                    with (r['dir']/'resource_samples.jsonl').open('a') as f:f.write(json.dumps(dict(elapsed=elapsed,processes=sample_resources(r['p'].pid)))+'\n')
            batch['last_resource']=elapsed
        for r in batch['running']:
            if 'result' in r:continue
            code=r['p'].poll()
            if code is not None:
                r['log'].close();result=r['spec']|dict(start_monotonic_ns=batch['start'],process_wall_seconds=elapsed,returncode=code,termination_sent_seconds=r['termination'],hard_kill=r['killed'],cleanup_after_deadline_seconds=max(0,elapsed-seconds));dump(r['dir']/'process_result.json',result);r['result']=result;results.append(result)
            elif elapsed>=seconds and r['termination'] is None:
                try:os.killpg(r['p'].pid,signal.SIGTERM)
                except ProcessLookupError:pass
                r['termination']=elapsed
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
            assert all(r['result']['returncode']==0 and not r['killed'] for r in batch['running']),'sampler failed; logs preserved, no automatic rerun'
    def progress(active,queued,finished):
        nonlocal last_progress
        now=time.monotonic()
        if now-last_progress>=.5 or not active:
            dump(out/'progress.json',dict(status='sampling',stage=a.stage,total_groups=len(jobs),completed_groups=finished,queued_groups=queued,active_groups=[dict(group=b['group'],elapsed=(time.monotonic_ns()-b['start'])/1e9,active_sessions=sum(r['p'].poll() is None for r in b['running'])) for b in active.values()],max_concurrent_sessions=4));last_progress=now
    try:execute_groups(jobs,slots,launch,collect,release,progress)
    except Exception as exc:dump(out/'failure.json',dict(error=str(exc),automatic_retry=False));raise
    finally:
        for b in batches:
            if any(r['p'].poll() is None for r in b['running']):release(b,abort=True)
        os.sched_setaffinity(0,allowed)
    for path,wanted in files.items():assert sha(ROOT/path)==wanted,'frozen source changed during sampling: '+path
    dump(out/'progress.json',dict(status='sampling_complete',sessions=len(results)))
    if a.postprocess:
        py='/tmp/lia-highdiv-frozen/venv/bin/python3';env=dict(os.environ,HIGHDIV_ROOT='/tmp/lia-highdiv-frozen')
        for stage,cmd in [('audit',[py,'scripts/audit_native_delivery_results.py',str(out),'--jobs','15']),('analysis',[py,'scripts/analyze_native_delivery_results.py',str(out)]),('archive',[py,'scripts/archive_bofill_results.py',str(out)])]:
            dump(out/'progress.json',dict(status=stage,sessions=len(results)))
            with (out/(stage+'.log')).open('w') as f:subprocess.run(cmd,env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
        dump(out/'progress.json',dict(status='audit_analysis_archive_complete',sessions=len(results)))
if __name__=='__main__':main()
