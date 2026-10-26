#!/usr/bin/env python3
"""Eight frozen 900-second sessions, 15-slot resource ceiling; never run HighDiv."""
import argparse,json,os,signal,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from run_native_feedback_pairs import ROOT,INPUTS,dump,sha,gate,sample_resources

CONFIGS=('plain','native_random_one')
SEEDS=(0,1)
SECONDS=900
BRANCH='native-random-900s-frozen'
BINARY=ROOT/'build/native_feedback/release/native_feedback_sampler'
LIBRARY=ROOT/'dependencies/z3-native-feedback-phase/install/lib/libz3.so'
PROTOCOL=ROOT/'docs/原生随机900秒冻结验证协议.md'
PYTHON='/tmp/lia-highdiv-frozen/venv/bin/python3'

def jobs(inputs):
    return [dict(group=g,formula=e['name'],input=e['path'],seed=seed,configuration=c,seconds=SECONDS)
            for g,(e,seed) in enumerate((e,s) for s in SEEDS for e in inputs) for c in CONFIGS]

def verify_frozen():
    prior=json.loads((ROOT/'results/native_feedback/screen_120s/plan.json').read_text())
    files={p:h for p,h in prior['files'].items() if p.startswith(('src/','include/','dependencies/')) or p in ('CMakeLists.txt',str(BINARY.relative_to(ROOT)))}
    for relative,wanted in files.items():assert sha(ROOT/relative)==wanted,'frozen file changed: '+relative
    assert sha(LIBRARY)==prior['z3_sha256'],'frozen library changed'
    files[str(LIBRARY.relative_to(ROOT))]=prior['z3_sha256']
    return files

def copy_highdiv(out):
    source=ROOT/'results/bofill_900s/historical_highdiv.json';ref=json.loads(source.read_text())
    proof=out/'historical';proof.mkdir();evidence={str(source.relative_to(ROOT)):sha(source)}
    manifest=ROOT/ref['manifest_path'];assert sha(manifest)==ref['manifest_sha256']
    (proof/'manifest.json').write_bytes(manifest.read_bytes());evidence[ref['manifest_path']]=sha(manifest)
    expected=dict(INPUTS)
    assert {r['formula'] for r in ref['rows']}==set(expected)
    for r in ref['rows']:
        audit=ROOT/r['audit_path'];assert sha(audit)==r['audit_sha256']
        data=json.loads(audit.read_text());assert r['input_sha256']==expected[r['formula']]
        assert r['seed']==0 and r['seconds']==900 and r['covered']==data['covered'] and r['total']==data['total']
        assert r['distinct']==data['unique_valid_in_budget'] and r['curve']==data['curve']
        (proof/(r['formula']+'.seed-0.audit.json')).write_bytes(audit.read_bytes());evidence[r['audit_path']]=sha(audit)
    (out/'historical_highdiv.json').write_bytes(source.read_bytes())
    dump(proof/'source_hashes.json',evidence)
    return evidence

def stop_group(pid,sig):
    try:os.killpg(pid,sig)
    except ProcessLookupError:pass

def resource_victim(active,measurements):
    groups={r['spec']['group'] for r in active if r['termination'] is None}
    return max(groups,key=lambda g:sum(int((p.get('VmRSS') or '0 kB').split()[0]) for r in active if r['spec']['group']==g for p in measurements[r['p'].pid])) if groups else None

def sample(out,plan):
    running=[];start=None;last_resource=-1.;last_progress=-1.
    try:
        for spec in jobs(plan['inputs']):
            d=out/'runs'/f"{spec['group']:02d}_{spec['configuration']}";d.mkdir(parents=True)
            spec.update(core=None,cpus=plan['sampler_cpus'],command=[str(BINARY),spec.pop('input'),spec['configuration'],str(SECONDS),str(spec['seed']),str(d/'raw')])
            dump(d/'spec.json',spec);log=(d/'process.log').open('wb')
            p=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--gate',str(d/'spec.json')],stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            running.append(dict(p=p,dir=d,spec=spec,log=log,termination=None,killed=False,resource_stop=False))
        assert len(running)==8<=plan['max_workers']
        deadline=time.monotonic()+30
        while not all((r['dir']/'ready.json').exists() for r in running):
            assert time.monotonic()<deadline and all(r['p'].poll() is None for r in running),'gate initialization failed'
            time.sleep(.02)
        assert sha(BINARY)==plan['files'][str(BINARY.relative_to(ROOT))]
        start=time.monotonic_ns()+100_000_000
        for r in running:r['p'].stdin.write(f'{start}\n'.encode());r['p'].stdin.flush();r['p'].stdin.close()
        dump(out/'start.json',dict(start_monotonic_ns=start,utc=datetime.now(timezone.utc).isoformat(),active_sessions=8,max_workers=15))
        print('Eight sessions released together: two inputs x seeds 0/1 x plain/native_random_one, 900 seconds; 15-slot ceiling.',flush=True)
        while any('result' not in r for r in running):
            elapsed=(time.monotonic_ns()-start)/1e9;active=[r for r in running if 'result' not in r]
            if elapsed-last_resource>=1:
                samples={r['p'].pid:sample_resources(r['p'].pid) for r in active}
                rss=sum(int((p.get('VmRSS') or '0 kB').split()[0]) for ps in samples.values() for p in ps)
                available=int(next(l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')).split()[1])
                if rss>20*1024**2 or available<3*1024**2:
                    victim=resource_victim(active,samples)
                    for r in active:
                        if r['spec']['group']==victim and r['termination'] is None:
                            stop_group(r['p'].pid,signal.SIGTERM);r['termination']=elapsed;r['resource_stop']=True
                for r in active:
                    with (r['dir']/'resource_samples.jsonl').open('a') as f:f.write(json.dumps(dict(elapsed=elapsed,processes=samples[r['p'].pid],aggregate_rss_kib=rss,host_available_kib=available))+'\n')
                last_resource=elapsed
            for r in active:
                code=r['p'].poll()
                if code is not None:
                    r['log'].close();result={**r['spec'], 'start_monotonic_ns':start,'process_wall_seconds':elapsed,'returncode':code,
                        'termination_sent_seconds':r['termination'],'hard_kill':r['killed'],'resource_stop':r['resource_stop'],'cleanup_after_deadline_seconds':max(0,elapsed-SECONDS)}
                    dump(r['dir']/'process_result.json',result);r['result']=result
                elif elapsed>=SECONDS and r['termination'] is None:
                    stop_group(r['p'].pid,signal.SIGTERM);r['termination']=elapsed
                elif r['termination'] is not None and elapsed-r['termination']>=2 and not r['killed']:
                    stop_group(r['p'].pid,signal.SIGKILL);r['killed']=True
            if elapsed-last_progress>=.5:
                dump(out/'progress.json',dict(status='sampling',elapsed=elapsed,seconds=SECONDS,total=8,completed=sum('result' in r for r in running),active=sum('result' not in r for r in running),queued=0,max_workers=15))
                last_progress=elapsed
            if any('result' not in r for r in running):time.sleep(.02)
        results=[r['result'] for r in running];dump(out/'process_results.json',results)
        dump(out/'progress.json',dict(status='sampling_complete',total=8,completed=8,queued=0,max_workers=15))
        assert not any(r['hard_kill'] for r in results),'forced kill; preserve partial evidence for review, no automatic rerun'
        assert not any(r['returncode']!=0 for r in results),'sampler failed; preserve logs, no automatic rerun'
    finally:
        for r in running:
            if r['p'].poll() is None:stop_group(r['p'].pid,signal.SIGKILL);r['p'].wait()
            r['log'].close()

def main():
    if len(sys.argv)>1 and sys.argv[1]=='--gate':
        # The time wrapper must reap the sampler and write its CPU accounting.
        # The frozen sampler installs its own SIGTERM handler after exec.
        signal.signal(signal.SIGTERM,signal.SIG_IGN);gate(sys.argv[2]);return
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--postprocess',action='store_true');p.add_argument('--publish',action='store_true');a=p.parse_args()
    assert not a.publish or a.postprocess,'publication requires independent audit and report'
    os.chdir(ROOT);assert subprocess.check_output(['git','branch','--show-current'],text=True).strip()==BRANCH
    files=verify_frozen();assert not subprocess.check_output(['git','status','--short','--untracked-files=no'],text=True).strip(),'freeze tracked source before sampling'
    allowed=os.sched_getaffinity(0);assert set(range(16))<=allowed,'expected 15 sampler CPUs plus one controller CPU'
    os.environ.pop('NATIVE_TEST_MAX_QUERIES',None);os.environ.pop('BOFILL_START_MONO_NS',None)
    out=Path(a.out).resolve();out.mkdir(parents=True,exist_ok=False);(out/'inputs').mkdir()
    historical=copy_highdiv(out);inputs=[]
    for name,wanted in INPUTS:
        source=ROOT/'benchmarks'/name;assert sha(source)==wanted
        dest=out/'inputs'/name;dest.write_bytes(source.read_bytes());inputs.append(dict(name=name,sha256=wanted,path=str(dest)))
    for p in [Path(__file__),PROTOCOL,ROOT/'scripts/analyze_native_frozen_900s.py',ROOT/'scripts/audit_native_feedback_results.py',ROOT/'scripts/run_native_feedback_pairs.py',ROOT/'scripts/archive_bofill_results.py']:
        files[str(p.relative_to(ROOT))]=sha(p)
    plan=dict(schema=1,kind='native_frozen_900s',algorithm_commit='40874ea',git_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),seconds=SECONDS,seeds=list(SEEDS),configurations=list(CONFIGS),inputs=inputs,files=files,
        max_workers=15,total_sessions=8,actual_initial_concurrency=8,sampler_cpus=list(range(15)),controller_cpu=15,gate='one shared monotonic release for all eight tasks',
        resource_limits=dict(address_space_gib=6,per_file_gib=1,aggregate_rss_gib=20,min_host_available_gib=3,cleanup_grace_seconds=2),
        highdiv_execution='forbidden; read-only historical seed-0 reference',historical_sources=historical,algorithm_parameters_changed=False,
        cpu_topology=subprocess.check_output(['lscpu','-e=CPU,CORE,SOCKET,ONLINE'],text=True),environment=subprocess.check_output(['lscpu'],text=True),memory=Path('/proc/meminfo').read_text(),
        runtime_links=subprocess.check_output(['ldd',str(BINARY)],text=True),created_utc=datetime.now(timezone.utc).isoformat())
    dump(out/'plan.json',plan);os.sched_setaffinity(0,{15})
    try:
        sample(out,plan);os.sched_setaffinity(0,allowed)
        if a.postprocess:
            env=dict(os.environ,HIGHDIV_ROOT='/tmp/lia-highdiv-frozen')
            for label,cmd in [('auditing',[PYTHON,'scripts/audit_native_feedback_results.py',str(out),'--jobs','15']),
                              ('analyzing',[PYTHON,'scripts/analyze_native_frozen_900s.py',str(out)]),
                              ('archiving',[PYTHON,'scripts/archive_bofill_results.py',str(out)])]:
                dump(out/'progress.json',dict(status=label,completed=8,total=8))
                with (out/(label+'.log')).open('w') as f:subprocess.run(cmd,cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
            for relative,wanted in files.items():assert sha(ROOT/relative)==wanted,'frozen file changed during run: '+relative
            dump(out/'progress.json',dict(status='audit_analysis_archive_complete',completed=8,total=8,automatic_further_experiments=False))
        if a.publish:
            assert out.is_relative_to(ROOT/'results'),'only publish the designated result directory'
            report=ROOT/'docs/原生随机900秒冻结验证报告.md';report.write_bytes((out/'报告.md').read_bytes())
            subprocess.run(['git','add',str(out),str(report)],check=True)
            subprocess.run(['git','commit','-m','Record frozen plain versus native random 900-second paired validation'],check=True)
            subprocess.run(['git','push','-u','origin',BRANCH],check=True)
            print('Results, complete archives and Chinese report pushed to '+BRANCH,flush=True)
    except Exception as exc:
        dump(out/'failure.json',dict(error=str(exc),utc=datetime.now(timezone.utc).isoformat(),automatic_retry=False));raise
    finally:os.sched_setaffinity(0,allowed)
if __name__=='__main__':main()
