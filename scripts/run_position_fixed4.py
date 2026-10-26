#!/usr/bin/env python3
"""Frozen 42-session minimal fixed-fourth ablation; never retries a run."""
import argparse,json,os,signal,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from run_native_feedback_pairs import ROOT,INPUTS,dump,sha,gate,sample_resources
from run_position_frozen_900s import memory_snapshot,stop,GiB
from complete_position_frozen_900s import thread_snapshot
BRANCH='position-fixed4-ablation'
BASE='6d2cab4'
CONFIGS=['first_immediate','position_immediate','fixed4_immediate']
SEEDS=list(range(10,17))
SECONDS=120
BINARY=ROOT/'build/position_fixed4/release/native_delivery_sampler'
PY='/tmp/lia-highdiv-frozen/venv/bin/python3'
PROTOCOL=ROOT/'docs/固定第四位置120秒消融冻结协议.md'

def topology():
    rows=[];cores={}
    for cpu in sorted(os.sched_getaffinity(0)):
        p=Path(f'/sys/devices/system/cpu/cpu{cpu}/topology');key=(int((p/'physical_package_id').read_text()),int((p/'core_id').read_text()))
        cores.setdefault(key,[]).append(cpu);rows.append(dict(cpu=cpu,package=key[0],physical_core=key[1]))
    cores=[v for _,v in sorted(cores.items()) if len(v)==2]
    assert len(cores)>=8,'15 worker logical CPUs plus supervisor required'
    return cores[:8],rows

def slot_assignments(batch,cores,n):
    # Deterministic balanced co-runner mix; no outcomes are inspected.
    pair_types=[(0,1)]*3+[(0,2)]*2+[(1,2)]*2 if n==5 else [(0,1)]*2+[(0,2)]*2+[(1,2)]*2
    slots={c:[] for c in range(3)}
    for i,pair in enumerate(pair_types):
        physical=cores[(i+3*batch)%7]
        for lane,c in enumerate(pair):slots[(c+batch)%3].append(physical[(lane+batch)%2])
    if n==5:slots[(2+batch)%3].append(cores[7][0])
    identities={cpu:i for i,pair in enumerate(cores) for cpu in pair}
    def arrange(left,rows):
        if len(rows)==n:return rows
        for a in left[0]:
            for b in left[1]:
                for c in left[2]:
                    if len({identities[a],identities[b],identities[c]})!=3:continue
                    found=arrange({j:[x for x in left[j] if x!=v] for j,v in enumerate((a,b,c))},rows+[(a,b,c)])
                    if found:return found
        return None
    rows=arrange(slots,[]);assert rows and len({c for row in rows for c in row})==3*n
    return rows

def task_matrix(inputs,cores):
    groups=[(entry,seed) for seed in SEEDS for entry in inputs];jobs=[]
    for batch,begin in enumerate(range(0,len(groups),5)):
        subset=groups[begin:begin+5];slots=slot_assignments(batch,cores,len(subset))
        for j,(entry,seed) in enumerate(subset):
            # Rotate launch order too; all workers still share one release time.
            for c in [(j+batch+i)%3 for i in range(3)]:
                jobs.append(dict(batch=batch,group=begin+j,formula=entry['name'],input=entry['path'],seed=seed,configuration=CONFIGS[c],seconds=SECONDS,core=slots[j][c]))
    return jobs

def prepare(out):
    assert subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()==BRANCH
    assert not subprocess.check_output(['git','status','--short','--untracked-files=no'],cwd=ROOT,text=True).strip(),'commit validated implementation first'
    validation=ROOT/'results/position_fixed4_validation/summary.json';v=json.loads(validation.read_text());assert v['passed']
    for p,h in v['files'].items():assert sha(ROOT/p)==h,'validation identity: '+p
    for path in ['results/native_delivery/screen_120s/plan.json','results/position_frozen_900s/plan.json']:
        assert not set(SEEDS)&set(json.loads((ROOT/path).read_text())['seeds']),'seed reuse'
    cores,cpu=topology();mem=memory_snapshot();assert mem['available']>=18*GiB,'memory preflight: 15x1GiB plus3GiB reserve'
    out.mkdir(parents=True,exist_ok=False);(out/'inputs').mkdir();inputs=[]
    for name,h in INPUTS:
        src=ROOT/'benchmarks'/name;assert sha(src)==h;dst=out/'inputs'/name;dst.write_bytes(src.read_bytes());inputs.append(dict(name=name,sha256=h,path=str(dst)))
    jobs=task_matrix(inputs,cores);files=dict(v['files'])|{str(PROTOCOL.relative_to(ROOT)):sha(PROTOCOL),str(validation.relative_to(ROOT)):sha(validation)}
    for folder in ['scripts','python']:
        files.update({str(p.relative_to(ROOT)):sha(p) for p in sorted((ROOT/folder).glob('*.py'))})
    plan=dict(schema=1,kind='screen',experiment='position_fixed4_120s',base_commit=BASE,seconds=SECONDS,seeds=SEEDS,configurations=CONFIGS,total_sessions=42,inputs=inputs,jobs=jobs,files=files,preparation_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),created_utc=datetime.now(timezone.utc).isoformat(),requested_threads=15,max_concurrent_sessions=15,batches=3,batch_sizes=[15,15,12],supervisor_cpu=cores[7][1],cpu_topology=cpu,worker_cores=cores,memory=mem,initialization_comparison='same formula, seed, solver kind and pre-first trace; compare first assignment, never assume equality',action_semantics='draw only after reaching target and passing prechoice checks; count an actual draw even if subsequent budget check prevents selection',limits=dict(address_space_gib=6,per_file_gib=1,total_rss_stop_gib=20,minimum_free_memory_gib=2,cleanup_seconds=2),highdiv_execution=False,automatic_next_stage=False)
    dump(out/'plan.json',plan);print(json.dumps(dict(prepared=True,sessions=42,concurrent=15,batches=[15,15,12])))

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
            print(f'Batch {batch+1}/{plan["batches"]}: {len(active)} single-thread samplers released together for 120s',flush=True)
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
                                if (proc.get('Name') or '').startswith('native_deliver'):proc['thread_affinity']=thread_snapshot(int(proc['Pid']))
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
                    dump(out/'progress.json',dict(status='sampling',batch=batch,total_batches=plan['batches'],batch_elapsed=elapsed,total=42,completed=len(results),active=sum('result' not in r for r in active),queued=42-len(results)-sum('result' not in r for r in active),sampler_limit=plan['max_concurrent_sessions'],supervisor_cpu=plan['supervisor_cpu']));last_progress=elapsed
                if any('result' not in r for r in active):time.sleep(.02)
            dump(out/'process_results.json',results)
            assert not resource_failure and all(r['result']['returncode']==0 and not r['killed'] for r in active),'batch failed; preserve partial results, no retry or seed replacement'
        for p,h in plan['files'].items():assert sha(ROOT/p)==h,'changed during frozen sampling: '+p
        dump(out/'sampling_complete.json',dict(passed=True,sessions=len(results),complete_utc=datetime.now(timezone.utc).isoformat()))
        dump(out/'progress.json',dict(status='sampling_complete',total=42,completed=42,active=0,queued=0))
    finally:
        for r in all_children:
            if r['p'].poll() is None:stop(r['p'].pid,signal.SIGKILL);r['p'].wait()
            r['log'].close()
        os.sched_setaffinity(0,allowed)

def postprocess(out,publish):
    env=dict(os.environ,HIGHDIV_ROOT='/tmp/lia-highdiv-frozen',PYTHONPATH='scripts:python')
    commands=[('audit',[PY,'scripts/audit_position_fixed4.py',str(out),'--jobs','15']),('analysis',[PY,'scripts/analyze_position_fixed4.py',str(out)]),('archive',[PY,'scripts/archive_bofill_results.py',str(out)])]
    for name,cmd in commands:
        dump(out/'progress.json',dict(status=name,total=42,completed=42,active=0,queued=0))
        with (out/(name+'.log')).open('w') as f:subprocess.run(cmd,env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
    dump(out/'evidence_manifest.json',dict(files={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and 'runs' not in p.relative_to(out).parts and p.name not in ['progress.json','evidence_manifest.json','publication.log']},no_additional_experiments=True))
    dump(out/'progress.json',dict(status='complete',total=42,completed=42,active=0,queued=0))
    if publish:
        with (out/'publication.log').open('w') as f:
            for cmd in [['git','add','--',str(out.relative_to(ROOT)),'docs/固定第四位置120秒消融报告.md'],['git','commit','-m','Archive 42 fixed-fourth 120s ablation sessions and independent audit'],['git','push','-u','origin',BRANCH]]:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
        dump(out/'publication_receipt.local.json',dict(pushed=True,commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),branch=BRANCH))

def main():
    if len(sys.argv)>1 and sys.argv[1]=='--gate':signal.signal(signal.SIGTERM,signal.SIG_IGN);gate(sys.argv[2]);return
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=ROOT/'results/position_fixed4_120s');p.add_argument('--prepare',action='store_true');p.add_argument('--run',action='store_true');p.add_argument('--postprocess',action='store_true');p.add_argument('--push-results',action='store_true');a=p.parse_args();os.chdir(ROOT);out=a.out.resolve()
    assert a.prepare!=a.run,'choose prepare or run'
    if a.prepare:prepare(out);return
    try:
        sample(out,json.loads((out/'plan.json').read_text()))
        if a.postprocess:postprocess(out,a.push_results)
    except Exception as exc:dump(out/'failure.json',dict(error=repr(exc),automatic_retry=False));raise
if __name__=='__main__':main()
