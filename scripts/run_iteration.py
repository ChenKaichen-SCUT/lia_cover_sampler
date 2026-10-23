#!/usr/bin/env python3
"""Immutable, resource-accounted queues; screen by default, explicit promotion for long runs."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import hashlib,json,os,signal,subprocess,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def write(path,value):
    path=Path(path);temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n');temp.replace(path)

def cgroup():
    result={}
    for name in ('cpu.stat','cpu.max','memory.max'):
        path=Path('/sys/fs/cgroup')/name
        if path.exists():result[name]=path.read_text().strip()
    return result

def run_queue(args):
    configs=json.loads(args.configs.read_text())
    if args.select:configs={k:v for k,v in configs.items() if k in args.select.split(',')}
    if not configs:raise ValueError('empty configuration selection')
    inputs=json.loads((ROOT/'benchmarks/manifest.json').read_text())
    inputs=[r for r in inputs if args.split=='all' or r['split']==args.split]
    seeds=[int(v) for v in args.seeds.split(',')]
    if len(set(seeds))!=len(seeds):raise ValueError('duplicate seeds')
    for config in configs.values():
        config['binary']=str(Path(config['binary']).resolve());actual=digest(config['binary'])
        if config.get('binary_sha256',actual)!=actual:raise ValueError('binary hash mismatch')
        config['binary_sha256']=actual
    if not args.prefix_audit:raise ValueError('sample-cap code-path and controlled-prefix audit required')
    prefix_audit=json.loads(args.prefix_audit.read_text())
    if not prefix_audit['passed'] or not prefix_audit['code_path_stop_only']:raise ValueError('sample-cap audit failed')
    for path,value in prefix_audit['source_hashes'].items():
        if digest(path)!=value:raise ValueError('generation source changed after sample-cap audit')
    if args.stage in ('confirm','final'):

        if not args.promotion:raise ValueError('explicit promotion checklist required')
        promotion=json.loads(args.promotion.read_text())
        if not promotion['approved'] or promotion['next_stage']!=args.stage:raise ValueError('not promoted')
        if not all(promotion['checks'].values()):raise ValueError('promotion checks not satisfied')
        for name,config in configs.items():
            if name not in promotion['selected'] or promotion['selected'][name]!=config:raise ValueError('configuration changed after promotion')
        if digest(promotion['analysis'])!=promotion['analysis_sha256']:raise ValueError('changed promotion evidence')
    root=args.output.resolve();root.mkdir(parents=True,exist_ok=False)
    stamp=datetime.now(timezone.utc).isoformat();jobs=[]
    for name,config in configs.items():
        folder=root/name;folder.mkdir();batch=[]
        for instance in inputs:
            path=ROOT/'benchmarks'/instance['path']
            if digest(path)!=instance['sha256']:raise ValueError('benchmark changed')
            for seed in seeds:
                run=Path('raw')/instance['path']/f'seed-{seed}'
                command=[config['binary'],'-i',str(path),'-o',str(folder/run),'-n',str(args.samples),'-t',str(args.seconds),'-s',str(seed),*config.get('args',[])]
                job=dict(batch=name,configuration=name,instance=instance['path'],split=instance['split'],seed=seed,
                         input_sha256=instance['sha256'],input=str(path),run=str(run),command=command)
                batch.append(job);jobs.append(job)
        write(folder/'manifest.json',dict(stage=args.stage,purpose=args.purpose,created_utc=stamp,
            binary=config['binary'],binary_sha256=config['binary_sha256'],configuration=config,
            variant='full',seconds=args.seconds,samples=args.samples,jobs=args.jobs,instances=batch))
    # Matched case/seed blocks, rotating configuration order. Both algorithms
    # experience the same pool and per-process single-thread environment.
    names=list(configs)
    jobs.sort(key=lambda j:(j['seed'],j['instance'],(names.index(j['batch'])+seeds.index(j['seed'])+
        next(i for i,r in enumerate(inputs) if r['path']==j['instance']))%len(names)))
    write(root/'plan.json',dict(stage=args.stage,purpose=args.purpose,created_utc=stamp,
        configs=configs,workers=args.jobs,seconds=args.seconds,samples=args.samples,seeds=seeds,split=args.split,
        prefix_audit=str(args.prefix_audit.resolve()),prefix_audit_sha256=digest(args.prefix_audit),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        runner_sha256=digest(__file__),queue=jobs,baseline_policy='reuse frozen HighDiv and old LIA audits; never generate',
        metric_definition='original-wire64-lazy-v1',protocol='single_time_trajectory_online_prefixes',
        promotion=str(args.promotion.resolve()) if args.promotion else None,cgroup_before=cgroup(),
        cpu_affinity=sorted(os.sched_getaffinity(0)),dependencies_lock_sha256=digest(ROOT/'dependencies/lock.json'),
        z3_library_sha256=digest(Path(os.environ.get('HIGHDIV_ROOT','/tmp/lia-highdiv-frozen'))/'venv/lib/libz3.so')))
    begin=time.monotonic();stopped=threading.Event();done=threading.Event();lock=threading.Lock();active={}
    usage=dict(peak_total_rss_kib=0,peak_active=0);resource_samples=[]
    def cancel(*_):
        stopped.set()
        with lock:
            for pid in active:
                try:os.killpg(pid,signal.SIGKILL)
                except ProcessLookupError:pass
    signal.signal(signal.SIGTERM,cancel);signal.signal(signal.SIGINT,cancel)
    def monitor():
        while not done.wait(.1):
            with lock:pids=list(active)
            rss=0
            for pid in pids:
                try:
                    for line in Path(f'/proc/{pid}/status').read_text().splitlines():
                        if line.startswith('VmRSS:'):rss+=int(line.split()[1])
                except (FileNotFoundError,ProcessLookupError):pass
            usage['peak_total_rss_kib']=max(usage['peak_total_rss_kib'],rss)
            usage['peak_active']=max(usage['peak_active'],len(pids))
            resource_samples.append(dict(seconds=time.monotonic()-begin,active=len(pids),rss_kib=rss))
    watcher=threading.Thread(target=monitor,daemon=True);watcher.start()
    def run(job):
        if stopped.is_set():return dict(**job,status='cancelled',elapsed=0,returncode=None)
        folder=root/job['batch']/job['run'];folder.mkdir(parents=True)
        start=time.monotonic();utc=datetime.now(timezone.utc).isoformat();status='ok';killed=False
        with (folder/'process.log').open('wb') as log:
            proc=subprocess.Popen(job['command'],stdout=log,stderr=subprocess.STDOUT,cwd=folder,
                start_new_session=True,env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1'))
            with lock:active[proc.pid]=job
            while True:
                pid,code,resources=os.wait4(proc.pid,os.WNOHANG)
                if pid:break
                if not killed and (stopped.is_set() or time.monotonic()-start>=args.seconds):
                    try:os.killpg(proc.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    killed=True;status='cancelled' if stopped.is_set() else 'timeout_partial'
                time.sleep(.005)
            proc.returncode=os.waitstatus_to_exitcode(code)
            with lock:active.pop(proc.pid,None)
        if proc.returncode and status=='ok':status='error';cancel()
        result=dict(**job,status=status,returncode=proc.returncode,elapsed=time.monotonic()-start,
            started_utc=utc,finished_utc=datetime.now(timezone.utc).isoformat(),
            cpu_seconds=resources.ru_utime+resources.ru_stime,user_seconds=resources.ru_utime,system_seconds=resources.ru_stime,
            max_rss_kib=resources.ru_maxrss,voluntary_context_switches=resources.ru_nvcsw,
            involuntary_context_switches=resources.ru_nivcsw)
        write(folder/'process.json',result);return result
    completed=[]
    try:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool,(root/'queue.jsonl').open('w') as journal:
            for future in as_completed([pool.submit(run,j) for j in jobs]):
                result=future.result();completed.append(result);line=json.dumps(result)+'\n';journal.write(line);journal.flush()
                with (root/result['batch']/'runs.jsonl').open('a') as f:f.write(line)
                print(f'{len(completed)}/{len(jobs)} {result["batch"]} {result["instance"]} seed={result["seed"]} {result["status"]}',flush=True)
    finally:done.set();watcher.join()
    for name in configs:
        folder=root/name;write(folder/'COMPLETE.json',dict(runs=sum(j['batch']==name for j in completed),
            cancelled=stopped.is_set(),manifest_sha256=digest(folder/'manifest.json'),journal_sha256=digest(folder/'runs.jsonl')))
    elapsed=time.monotonic()-begin;cpu=sum(r.get('cpu_seconds',0) for r in completed)
    write(root/'resources.json',dict(**usage,wall_seconds=elapsed,cpu_seconds=cpu,cpu_core_equivalents=cpu/elapsed,
        cpu_utilization_fraction=cpu/elapsed/len(os.sched_getaffinity(0)),
        context_switches=sum(r.get('voluntary_context_switches',0)+r.get('involuntary_context_switches',0) for r in completed),
        cgroup_after=cgroup(),samples=resource_samples))
    write(root/'COMPLETE.json',dict(runs=len(completed),cancelled=stopped.is_set(),errors=sum(r['status']=='error' for r in completed),
        plan_sha256=digest(root/'plan.json'),queue_sha256=digest(root/'queue.jsonl'),resources_sha256=digest(root/'resources.json')))
    if stopped.is_set():raise SystemExit('run failed/cancelled; preserved all evidence; no promotion')
    return root

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=['micro','screen','confirm','final'],default='screen')
    p.add_argument('--configs',type=Path,required=True);p.add_argument('--select')
    p.add_argument('--output',type=Path,required=True);p.add_argument('--seconds',type=float)
    p.add_argument('--jobs',type=int,default=15);p.add_argument('--seeds',default='0,1,2')
    p.add_argument('--samples',type=int,default=4294967295);p.add_argument('--split',choices=['development','all'])
    p.add_argument('--promotion',type=Path);p.add_argument('--purpose',default='development screening, not a 900-second result')
    p.add_argument('--snapshot-manifest',type=Path)
    p.add_argument('--prefix-audit',type=Path)
    a=p.parse_args()
    if not 1<=a.jobs<=15:p.error('jobs must be 1..15')
    if not 1<=a.samples<=4294967295:p.error('sample cap must be 1..4294967295')
    if a.stage=='micro':
        if not a.snapshot_manifest:p.error('micro requires a saved snapshot manifest')
        from snapshot_replay import run_micro
        run_micro(a);return
    cap=dict(screen=120,confirm=300,final=900)[a.stage];a.seconds=cap if a.seconds is None else a.seconds
    if not 0<a.seconds<=cap:p.error('deadline exceeds the stage limit')
    if a.stage=='final' and a.seconds!=900:p.error('final means exactly 900 seconds')
    a.split=a.split or ('all' if a.stage=='final' else 'development')
    if a.stage=='screen' and a.split!='development':p.error('screen uses development cases only')
    run_queue(a)
if __name__=='__main__':main()
