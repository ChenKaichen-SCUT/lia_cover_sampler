#!/usr/bin/env python3
"""Resource-only observation, then a documented correction of the OS-thread audit.

The frozen Z3 scoped_timer owns a timeout helper. Both tasks inherit one CPU.
This file does not modify the frozen sampler, controller, plan or first auditor.
It runs the corrected offline audit only after all 28 sessions have finished.
"""
import argparse,fcntl,json,os,subprocess,sys,time
from collections import Counter
from pathlib import Path
import audit_position_frozen_900s as original
from reevaluate_round6_saved_candidates import require,dump,write_csv
from run_position_frozen_900s import ROOT,PY,BRANCH,sha

def thread_snapshot(pid):
    records=[]
    for p in sorted((Path('/proc')/str(pid)/'task').glob('*')):
        try:
            status={l.split(':',1)[0]:l.split(':',1)[1].strip() for l in (p/'status').read_text().splitlines() if ':' in l};s=(p/'stat').read_text().rsplit(') ',1)[1].split()
            records.append(dict(tid=int(p.name),main=int(p.name)==pid,affinity=status['Cpus_allowed_list'],state=s[0],utime_ticks=int(s[11]),stime_ticks=int(s[12]),wchan=(p/'wchan').read_text().strip()))
        except (FileNotFoundError,ProcessLookupError):pass
    return records

def validate_threads(core,maximum,snapshots):
    require(1<=maximum<=2,'unexpected OS thread count beyond main/timeout helper')
    require(bool(snapshots),'missing per-thread affinity evidence')
    require(all(r['threads'] and all(t['affinity']==str(core) for t in r['threads']) for r in snapshots),'a thread escaped its single assigned logical CPU')
    require(max(len(r['threads']) for r in snapshots)>=maximum,'thread evidence did not capture reported maximum')

def observe(out):
    plan=json.loads((out/'plan.json').read_text());allowed=os.sched_getaffinity(0);os.sched_setaffinity(0,{plan['supervisor_cpu']});support=out/'thread_audit_support';support.mkdir(exist_ok=True)
    source=ROOT/'dependencies/z3-native-feedback-phase/src/util/scoped_timer.cpp';(support/'scoped_timer.cpp').write_bytes(source.read_bytes())
    dump(support/'source.json',dict(source=str(source.relative_to(ROOT)),sha256=sha(source),frozen_library_sha256=plan['files']['dependencies/z3-native-feedback-phase/install/lib/libz3.so'],explanation='std::thread(thread_func,s) waits on timed mutex/condition variable and dispatches TIMEOUT_EH_CALLER; it is not another SMT search worker.'))
    counts=Counter();target=support/'thread_samples.jsonl'
    if target.exists():
        for l in target.read_text().splitlines():counts[json.loads(l)['run']]+=1
    controller=json.loads((out/'execution.json').read_text())['pid']
    try:
        print('Resource observer active; coverage audit waits for all sampling to finish.',flush=True)
        while True:
            proc=Path('/proc')/str(controller)/'stat'
            try:state=proc.read_text().rsplit(') ',1)[1].split()[0]
            except (FileNotFoundError,ProcessLookupError):break
            if state=='Z':break
            for d in sorted((out/'runs').iterdir()):
                if counts[d.name]>=3:continue
                path=d/'resource_samples.jsonl'
                if not path.exists():continue
                with path.open('rb') as f:
                    f.seek(max(0,path.stat().st_size-8192));data=f.read()
                lines=data.splitlines();lines=lines if data.endswith(b'\n') else lines[:-1]
                if not lines:continue
                try:row=json.loads(lines[-1])
                except json.JSONDecodeError:continue
                child=next((p for p in row['processes'] if (p.get('Name') or '').startswith('native_deliver')),None)
                if not child:continue
                pid=int(child['Pid']);threads=thread_snapshot(pid)
                if len(threads)<2:continue
                s=json.loads((d/'spec.json').read_text());record=dict(run=d.name,group=s['group'],seed=s['seed'],configuration=s['configuration'],core=s['core'],pid=pid,monotonic_ns=time.monotonic_ns(),threads=threads)
                with target.open('a') as f:f.write(json.dumps(record)+'\n')
                counts[d.name]+=1
            time.sleep(2)
    finally:os.sched_setaffinity(0,allowed)
    require((out/'sampling_complete.json').exists() and json.loads((out/'sampling_complete.json').read_text())['passed'],'sampling failed; never retry or replace seeds')
    require((out/'failure.json').exists() and 'multithreaded sampler' in (out/'audit.log').read_text(),'unexpected original completion; do not mask another failure')
    require(len(counts)==28 and all(n>=1 for n in counts.values()),'incomplete per-run thread evidence')
    for p,h in plan['files'].items():require(sha(ROOT/p)==h,'frozen protected file changed: '+p)
    dump(support/'correction.json',dict(reason='original offline resource assertion equated OS thread count with compute parallelism',original_auditor_sha256=sha(ROOT/'scripts/audit_position_frozen_900s.py'),corrected_auditor_sha256=sha(Path(__file__)),original_failure_sha256=sha(out/'failure.json'),original_audit_log_sha256=sha(out/'audit.log'),algorithm_changed=False,process_affinity_changed=False,models_regenerated=False,coverage_or_statistics_changed=False,snapshots_per_run=dict(counts)))

def audit_resources(out,plan):
    rows=[];groups={};intervals={};identity={r['cpu']:(r['package'],r['physical_core']) for r in plan['cpu_topology']};jobs={(r['group'],r['configuration']):r for r in plan['jobs']};snapshots={}
    for l in (out/'thread_audit_support/thread_samples.jsonl').read_text().splitlines():
        r=json.loads(l);snapshots.setdefault(r['run'],[]).append(r)
    for d in sorted((out/'runs').iterdir()):
        s=json.loads((d/'spec.json').read_text());p=json.loads((d/'process_result.json').read_text());g=json.loads((d/'gate.json').read_text());ready=json.loads((d/'ready.json').read_text());j=jobs[s['group'],s['configuration']]
        require(all(s[k]==j[k] for k in ['group','batch','formula','seed','configuration','seconds','core']),'task matrix changed');require(ready['affinity']==[s['core']] and s['core'] not in [plan['supervisor_cpu'],plan['reserved_cpu']],'CPU allocation')
        require(g['start_monotonic_ns']==p['start_monotonic_ns'],'start evidence');resource=original.resource_fields((d/'resource.txt').read_text());require(p['returncode']==resource['exit_status']==0 and not p['resource_stop'] and not p['hard_kill'],'failed/truncated process')
        initial=json.loads((d/'raw/initial.json').read_text());require(initial['test_query_limit']==2**32-1 and initial['mode'] in ['first_immediate','position_immediate'],'test override or batch mode')
        maximum=0;n=0
        for l in (d/'resource_samples.jsonl').read_text().splitlines():
            r=json.loads(l);n+=1
            for proc in r['processes']:
                if (proc.get('Name') or '').startswith('native_deliver'):maximum=max(maximum,int(proc['Threads']))
        validate_threads(s['core'],maximum,snapshots.get(d.name,[]))
        row={k:s[k] for k in ['group','batch','formula','seed','configuration','core']};row.update(run=d.name,physical_core=str(identity[s['core']]),start_monotonic_ns=p['start_monotonic_ns'],exec_offset_seconds=(g['exec_monotonic_ns']-g['start_monotonic_ns'])/1e9,process_wall_seconds=p['process_wall_seconds'],maximum_observed_sampler_threads=maximum,assigned_logical_cpus=1,resource_sample_count=n,**resource);rows.append(row);groups.setdefault(s['group'],[]).append(row)
        start=p['start_monotonic_ns']/1e9;intervals.setdefault(s['batch'],[]).append((start,start+p['process_wall_seconds']))
    paired=[]
    for group,rs in sorted(groups.items()):
        require(len(rs)==2 and {r['configuration'] for r in rs}=={'first_immediate','position_immediate'},'incomplete pair');require(len({r['start_monotonic_ns'] for r in rs})==1 and len({r['physical_core'] for r in rs})==2,'pair synchronization/cores')
        skew=abs(rs[0]['exec_offset_seconds']-rs[1]['exec_offset_seconds']);paired.append(dict(group=group,formula=rs[0]['formula'],seed=rs[0]['seed'],exec_skew_seconds=skew,within_250ms=skew<=.25))
    end=None
    for batch,spans in sorted(intervals.items()):
        require(len({s for s,e in spans})==1 and (end is None or spans[0][0]>=end),'batch release/barrier');end=max(e for s,e in spans)
        if plan['concurrent_pair_groups']>1 and len(spans)==2*plan['concurrent_pair_groups']:
            counts=Counter((r['physical_core'],r['configuration']) for r in rows if r['batch']==batch);require(all(n==1 for n in counts.values()) and len(counts)==len(spans),'symmetric SMT allocation')
    require(len(rows)==plan['total_sessions']==28 and len(paired)==14,'missing sessions');write_csv(out/'resource_audit.csv',rows);write_csv(out/'pair_timing_audit.csv',paired)
    return dict(passed=True,all_pairs_synchronized=True,max_exec_skew_seconds=max(r['exec_skew_seconds'] for r in paired),all_within_250ms=all(r['within_250ms'] for r in paired),max_observed_sampler_threads=max(r['maximum_observed_sampler_threads'] for r in rows),assigned_logical_cpus_per_sampler=1,timeout_helper_included_in_os_thread_count=True,max_rss_kib=max(r['max_rss_kib'] for r in rows),sampling_physical_cores=len({r['physical_core'] for r in rows}),configured_concurrent_sessions=plan['max_concurrent_sessions'],resource_audit_correction='thread_audit_support/correction.json')

def self_test():
    validate_threads(3,2,[dict(threads=[dict(affinity='3'),dict(affinity='3')])])
    for maximum,records in [(3,[dict(threads=[dict(affinity='3')])]),(2,[dict(threads=[dict(affinity='3'),dict(affinity='4')])]),(2,[])]:
        try:validate_threads(3,maximum,records)
        except ValueError:continue
        raise AssertionError('invalid thread evidence accepted')
    return dict(passed=True,cases=4,model_search=False)

def main():
    p=argparse.ArgumentParser();p.add_argument('out',type=Path);p.add_argument('--audit-only',action='store_true');p.add_argument('--push-results',action='store_true');a=p.parse_args();out=a.out.resolve();os.chdir(ROOT)
    if a.audit_only:
        original.audit_resources=audit_resources;sys.argv=[sys.argv[0],str(out),'--jobs','15'];original.main();return
    # The lock lives outside the evidence tree and prevents two completion jobs
    # from writing the same audit or archives. It does not control any sampler.
    lock=open('/tmp/position_frozen_900s_completion.lock','a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    env=dict(os.environ,HIGHDIV_ROOT='/tmp/lia-highdiv-frozen',PYTHONPATH='scripts:python')
    commands=[('corrected_audit',[PY,str(Path(__file__).resolve()),str(out),'--audit-only']),('analysis',[PY,'scripts/analyze_position_frozen_900s.py',str(out)]),('figures',['python3','scripts/plot_position_frozen_900s.py',str(out)]),('archive',[PY,'scripts/archive_bofill_results.py',str(out)])]
    try:
        observe(out);dump(out/'thread_audit_support/regression.json',self_test())
        for name,command in commands:
            print('Starting '+name+' after sampling completion.',flush=True)
            dump(out/'progress.json',dict(status=name,total=28,completed=28,active=0,queued=0,resource_audit_correction=True))
            with (out/(name+'.log')).open('w') as f:subprocess.run(command,env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
        note='\n\n## OS线程计数审计修正\n\n冻结Z3的scoped_timer使用一个超时辅助线程，因此进程显示2个OS线程；前轮相同二进制同样如此。主线程及辅助线程均实测绑定到该进程唯一分配的逻辑CPU，并未获得第二个CPU并行求解。14个采样进程仍仅使用14个工作逻辑CPU，监督器使用预留物理核的一个逻辑CPU。原审计将OS线程数直接等同于并行求解数而拒绝了记录；保留其失败日志，并依据逐线程亲和性、CPU计数及冻结计时器源码完成修正。采样器、依赖、参数、原始模型、正式计分与统计规则均未修改，未重跑模型。证据及4项资源审计回归在thread_audit_support。\n'
        doc=ROOT/'docs/原生干预位置900秒新seed验证报告.md';doc.write_text(doc.read_text()+note);(out/'报告.md').write_text(doc.read_text())
        dump(out/'evidence_manifest.json',dict(files={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and 'runs' not in p.relative_to(out).parts and p.name not in ['progress.json','evidence_manifest.json','publication.log']},algorithm_commit='75cd6b6',resource_audit_corrected=True))
        dump(out/'progress.json',dict(status='complete',total=28,completed=28,active=0,queued=0,resource_audit_corrected=True,additional_experiments=False))
        if a.push_results:
            with (out/'publication.log').open('w') as f:
                for cmd in [['git','add','--',str(out.relative_to(ROOT)),str(doc.relative_to(ROOT))],['git','commit','-m','Archive 28 frozen 900s paired runs and corrected timer-thread audit'],['git','push','-u','origin',BRANCH]]:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
            dump(out/'publication_receipt.local.json',dict(pushed=True,commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),branch=BRANCH))
        print('Audit, analysis, figures and archival complete.',flush=True)
    except Exception as exc:dump(out/'corrective_failure.json',dict(error=repr(exc),automatic_retry=False));raise
if __name__=='__main__':main()
