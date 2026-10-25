#!/usr/bin/env python3
"""User-requested 15-slot continuation: retain the live first pair, launch queued pairs.

No sampler is restarted. The old serial controller finishes its first pair and
cannot claim the already-created later run directories; its expected collision
is recorded as scheduler supersession, not a sampler failure.
"""
import argparse,json,os,signal,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from run_bofill_900s import ROOT,CONFIGS,dump,sha

def group_processes(groups):
    result={g:[] for g in groups}
    for directory in Path('/proc').iterdir():
        if not directory.name.isdigit():continue
        try:
            fields=(directory/'stat').read_text().rsplit(')',1)[1].split();pgid=int(fields[2])
            if pgid not in result or fields[0]=='Z':continue
            status={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in (directory/'status').read_text().splitlines() if ':' in line}
            result[pgid].append(dict(pid=int(directory.name),state=fields[0],rss_kib=int(status.get('VmRSS','0 kB').split()[0]),hwm_kib=int(status.get('VmHWM','0 kB').split()[0])))
        except (FileNotFoundError,ProcessLookupError,ValueError):pass
    return result

def signal_group(pgid,sig):
    try:os.killpg(pgid,sig)
    except ProcessLookupError:pass

def set_group_affinity(group,cpus):
    changed=[]
    for row in group_processes([group])[group]:
        for task in Path(f'/proc/{row["pid"]}/task').iterdir():
            try:os.sched_setaffinity(int(task.name),cpus);changed.append(int(task.name))
            except ProcessLookupError:pass
    return changed

def main():
    parser=argparse.ArgumentParser();parser.add_argument('out');a=parser.parse_args();out=Path(a.out).resolve()
    plan=json.loads((out/'plan.json').read_text());progress=json.loads((out/'progress.json').read_text())
    assert progress['group']==0 and progress['status']=='sampling';assert CONFIGS==['plain_clean','choice_compiled']
    assert plan['seconds']==900 and plan['seeds']==[0,1,2]
    cpus=set(range(15));assert cpus.issubset(os.sched_getaffinity(0));os.sched_setaffinity(0,{15})
    old_pid=json.loads((ROOT/'results/bofill_900s_launch.json').read_text())['pid'];binary=ROOT/'build/bofill/release/bofill_sampler'
    assert sha(binary)==plan['files'][str(binary.relative_to(ROOT))]
    rows=[];amended=[]
    for d in sorted((out/'runs').iterdir()):
        spec=json.loads((d/'spec.json').read_text());ready=json.loads((d/'ready.json').read_text());gate=json.loads((d/'gate.json').read_text())
        assert spec['group']==0
        rows.append(dict(dir=d,spec=spec,pgid=ready['pid'],start=gate['start_monotonic_ns'],p=None,termination=None,killed=False,adopted=True))
        amended.append(dict(run=d.name,original_core=spec['core'],affected_tids=set_group_affinity(ready['pid'],cpus)))
    amendment=dict(reason='user explicitly requested 15-way parallelism; retain both already-running samplers',utc=datetime.now(timezone.utc).isoformat(),monotonic_ns=time.monotonic_ns(),max_workers=15,total_runs=12,active_existing=2,new_queued=10,cpus=sorted(cpus),supervisor_cpu=15,old_controller=old_pid,existing_affinity_change=amended,script_sha256=sha(Path(__file__)),gate_script_sha256=sha(ROOT/'scripts/run_bofill_900s.py'),binary_sha256=sha(binary),memory_guard='aggregate sampler RSS >20GiB or host MemAvailable <3GiB: terminate largest process group and record resource stop',all_seed0_first_pair_preserved=True)
    dump(out/'parallel_amendment.json',amendment)
    new=[]
    for group,(entry,seed) in enumerate((entry,seed) for entry in plan['inputs'] for seed in plan['seeds']):
        if group==0:continue
        for config in CONFIGS:
            d=out/'runs'/f'{group:02d}_{config}';d.mkdir()
            spec=dict(group=group,formula=entry['name'],seed=seed,configuration=config,core=None,cpus=sorted(cpus),seconds=900,command=[str(binary),entry['path'],'plain' if config=='plain_clean' else 'choice_compiled','900',str(seed),str(d/'raw')])
            dump(d/'spec.json',spec);log=(d/'process.log').open('wb')
            p=subprocess.Popen([sys.executable,str(ROOT/'scripts/run_bofill_900s.py'),'--gate',str(d/'spec.json')],stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            row=dict(dir=d,spec=spec,pgid=p.pid,start=None,p=p,termination=None,killed=False,adopted=False,log=log);rows.append(row);new.append(row)
    until=time.monotonic()+20
    while not all((r['dir']/'ready.json').exists() for r in new):
        if time.monotonic()>until or any(r['p'].poll() is not None for r in new):raise RuntimeError('parallel gate failure')
        time.sleep(.02)
    start=time.monotonic_ns()+100_000_000
    for r in new:r['start']=start;r['p'].stdin.write(f'{start}\n'.encode());r['p'].stdin.flush();r['p'].stdin.close()
    amendment['new_start_monotonic_ns']=start;dump(out/'parallel_amendment.json',amendment)
    print('Retained two running samplers; launched ten queued tasks. 12 active, 15-slot limit.',flush=True)
    last_resource=-1;old_status=None
    while any('done' not in r for r in rows):
        now=time.monotonic_ns();groups=group_processes([r['pgid'] for r in rows if 'done' not in r])
        if now/1e9-last_resource>=1:
            mem={line.split(':')[0]:int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if ':' in line}
            total=sum(p['rss_kib'] for ps in groups.values() for p in ps)
            if total>20*1024**2 or mem['MemAvailable']<3*1024**2:
                candidates=[r for r in rows if 'done' not in r and groups[r['pgid']] and not r['termination']]
                if candidates:
                    victim=max(candidates,key=lambda r:sum(p['rss_kib'] for p in groups[r['pgid']]))
                    signal_group(victim['pgid'],signal.SIGTERM);victim['termination']=(now-victim['start'])/1e9;victim['resource_stop']=True
            for r in rows:
                if 'done' not in r:
                    with (r['dir']/'parallel_resources.jsonl').open('a') as f:f.write(json.dumps(dict(elapsed=(now-r['start'])/1e9,processes=groups[r['pgid']],aggregate_rss_kib=total,host_available_kib=mem['MemAvailable']))+'\n')
            last_resource=now/1e9
        for r in rows:
            if 'done' in r:continue
            elapsed=(now-r['start'])/1e9
            if not groups[r['pgid']]:
                if r['adopted']:
                    if not (r['dir']/'process_result.json').exists():continue
                else:
                    code=r['p'].wait(timeout=1);r['log'].close()
                    result={**r['spec'],'start_monotonic_ns':r['start'],'process_wall_seconds':elapsed,'returncode':code,'termination_sent_seconds':r['termination'],'hard_kill':r['killed'],'cleanup_after_deadline_seconds':max(0,elapsed-900),'resource_stop':r.get('resource_stop',False)}
                    dump(r['dir']/'process_result.json',result)
                r['done']=True;continue
            if not r['adopted'] and elapsed>=900 and r['termination'] is None:
                signal_group(r['pgid'],signal.SIGTERM);r['termination']=elapsed
            # Also bounds orphaned children of the previous controller's time wrapper.
            if (elapsed>=902 or r.get('resource_stop') and elapsed-r['termination']>=.5) and not r['killed']:
                try:os.killpg(r['pgid'],signal.SIGKILL)
                except ProcessLookupError:pass
                r['killed']=True
        dump(out/'parallel_progress.json',dict(status='sampling',completed=sum('done' in r for r in rows),total=12,active=sum('done' not in r for r in rows),max_workers=15))
        time.sleep(.1)
    # The superseded controller may only fail to create the already-owned group-1 dir.
    for _ in range(50):
        try:
            status=Path(f'/proc/{old_pid}/stat').read_text().rsplit(')',1)[1].split()[0]
            if status=='Z':break
        except FileNotFoundError:break
        time.sleep(.1)
    dump(out/'process_results.json',[json.loads((r['dir']/'process_result.json').read_text()) for r in rows])
    dump(out/'parallel_progress.json',dict(status='sampling_complete',completed=12,total=12))
    dump(out/'progress.json',dict(status='sampling_complete',groups=6,controller='parallel15',completed_utc=datetime.now(timezone.utc).isoformat()))
    env=dict(os.environ,HIGHDIV_ROOT='/tmp/lia-highdiv-frozen');python='/tmp/lia-highdiv-frozen/venv/bin/python3';os.sched_setaffinity(0,set(range(16)))
    for script,extra in [('audit_bofill_results.py',['--jobs','3']),('analyze_bofill_results.py',[]),('archive_bofill_results.py',[])]:
        subprocess.run([python,str(ROOT/'scripts'/script),str(out),*extra],env=env,check=True)
    dump(out/'progress.json',dict(status='audit_and_analysis_complete',groups=6,controller='parallel15',completed_utc=datetime.now(timezone.utc).isoformat()))
    dump(out/'parallel_progress.json',dict(status='audit_and_analysis_complete',completed=12,total=12))
if __name__=='__main__':main()
