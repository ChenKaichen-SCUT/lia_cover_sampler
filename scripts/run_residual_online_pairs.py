#!/usr/bin/env python3
"""Frozen 36-session round-seven protocol. No automatic retries or follow-up runs."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
BASE = 'b2f3047c76f89cd6cb84cb1b74ad408d94113fd5'
ENV = {k:'1' for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','VECLIB_MAXIMUM_THREADS','BLIS_NUM_THREADS')}
JOURNALS = ('initial.json','models.jsonl','commits.jsonl','checkpoints.jsonl','rounds.jsonl','summary.json')


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def utc(): return datetime.now(timezone.utc).isoformat()
def write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def snapshot_bindings(snapshot):
    folder = Path(snapshot['folder'])
    result = {str(folder/name):snapshot[field] for name,field in (
        ('prefix.values','prefix_sha256'),('seed.values','seed_sha256'),('coverage.json','coverage_sha256'),('assignments.json','assignments_sha256'))}
    result[snapshot['formula']] = snapshot['input_sha256']
    for path, expected in result.items():
        if digest(path) != expected: raise ValueError('frozen snapshot changed: '+path)
    return result


def run_session(job, binary, out, source):
    s = job['snapshot']; folder = Path(s['folder']); mode = job['mode']
    destination = out/'raw'/s['id']/f'seed-{job["seed"]}'/mode
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [str(binary), s['formula'], str(folder/'prefix.values'), str(folder/'seed.values'), mode, '10', str(job['seed']), str(destination)]
    r = dict(session=job['session'], snapshot=s['id'], instance=s['instance'], prefix=s['prefix'], seed=job['seed'], mode=mode,
        queue_index=job['queue_index'], output=str(destination.relative_to(out)), command=command, started_utc=utc(), success=False)
    start = time.monotonic(); expired = threading.Event(); env = os.environ.copy(); env.update(ENV)
    try:
        with destination.with_suffix('.stdout.log').open('wb') as stdout, destination.with_suffix('.stderr.log').open('wb') as stderr:
            child = subprocess.Popen(command, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, env=env, start_new_session=True)
            def kill():
                try: os.killpg(child.pid, signal.SIGKILL); expired.set()
                except ProcessLookupError: pass
            timer = threading.Timer(60, kill); timer.start()
            try:
                _,status,usage = os.wait4(child.pid, 0); child.returncode = os.waitstatus_to_exitcode(status)
            finally: timer.cancel()
        r.update(exitcode=child.returncode, watchdog_timeout=expired.is_set(), cpu_seconds=usage.ru_utime+usage.ru_stime)
        r['hashes'] = {name:digest(destination/name) for name in JOURNALS if (destination/name).is_file()}
        if child.returncode: raise RuntimeError('probe exit '+str(child.returncode))
        initial = json.loads((destination/'initial.json').read_text()); summary = json.loads((destination/'summary.json').read_text())
        if not (initial['mode']==mode and initial['rng_seed']==job['seed'] and initial['source_sha256']==source==summary['source_sha256']):
            raise RuntimeError('session/source identity mismatch')
        r.update(success=True, summary={k:v for k,v in summary.items() if k!='final_coverage'})
    except Exception as exc: r.update(error=str(exc), traceback=traceback.format_exc())
    r.update(process_wall_seconds=time.monotonic()-start, finished_utc=utc())
    write(destination.with_suffix('.run.json'), r)
    return r


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--jobs', type=int, default=15); a = p.parse_args()
    if not 1<=a.jobs<=15: p.error('jobs must be 1..15; no concurrency calibration')
    subprocess.run(['git','diff','--exit-code','HEAD','--'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
    out = a.output.resolve(); out.mkdir(parents=True, exist_ok=False); binary = a.binary.resolve()
    manifest = ROOT/'results/round3_snapshots/states/manifest.json'
    snapshots = [s for s in json.loads(manifest.read_text())['snapshots'] if s['prefix'] in (1,10)]
    if len(snapshots)!=6 or not all(s['split']=='development' for s in snapshots): raise ValueError('six frozen development states required')
    cpp = sorted(list((ROOT/'src').glob('*.cpp'))+list((ROOT/'include').glob('*.hpp')))
    source = hashlib.sha256(''.join(digest(path) for path in cpp).encode()).hexdigest()
    support = [ROOT/'CMakeLists.txt', ROOT/'docs/第七轮实验协议.md', Path(__file__), ROOT/'scripts/audit_residual_online.py',
        ROOT/'scripts/analyze_residual_online.py', ROOT/'scripts/reevaluate_round6_saved_candidates.py', ROOT/'python/validation.py',
        ROOT/'python/coverage_differential_test.py', ROOT/'dependencies/source_audit.json']
    metric = Path(os.environ.get('HIGHDIV_ROOT', ROOT.parent/'HighDiv-master'))/'HighDiv/my_scripts/calc_metric.py'
    expected = json.loads((ROOT/'dependencies/source_audit.json').read_text())['HighDiv/my_scripts/calc_metric.py']['sha256']
    if digest(metric)!=expected: raise ValueError('frozen evaluator changed')
    z3_library = Path(os.environ.get('HIGHDIV_ROOT', ROOT.parent/'HighDiv-master'))/'venv/lib/libz3.so'
    expected_z3 = json.loads((ROOT/'results/round6_dev/builds/validated.json').read_text())['z3_sha256']
    if digest(z3_library)!=expected_z3: raise ValueError('frozen Z3 library changed')
    sources = {str(path):digest(path) for path in cpp+support+[manifest,binary,metric,z3_library]}
    for snapshot in snapshots: sources.update(snapshot_bindings(snapshot))
    jobs=[]
    for i,s in enumerate(snapshots):
        for seed in (0,1,2):
            modes = ['global_pack','residual_cardinality'] if (i+seed)%2==0 else ['residual_cardinality','global_pack']
            for mode in modes:
                jobs.append(dict(queue_index=len(jobs), session=f'{s["id"]}|seed-{seed}|{mode}', snapshot=s, seed=seed, mode=mode))
    plan=dict(created_utc=utc(), frozen_base=BASE, implementation_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source_sha256=source, binary=str(binary), binary_sha256=digest(binary), source_hashes=sources, snapshots=snapshots,
        sessions=36, seeds=[0,1,2], budget_seconds=10, round_cap_seconds=1, target_calls_cap=3, window_cap=8,
        workers=a.jobs, environment=ENV, jobs=jobs, no_automatic_followup=True,
        conditions=dict(platform=platform.platform(), cpu_count=os.cpu_count(), affinity=sorted(os.sched_getaffinity(0)), load_average=os.getloadavg(),
            cpuinfo=Path('/proc/cpuinfo').read_text(), memory=Path('/proc/meminfo').read_text()),
        unit='whole online session; outputs correlated; two prefixes share each benchmark family')
    write(out/'plan.json',plan)
    print(json.dumps(dict(stage='sampling', sessions=36, workers=a.jobs, session_budget=10, output=str(out))),flush=True)
    start=time.monotonic(); rows=[]
    with ThreadPoolExecutor(max_workers=a.jobs) as pool,(out/'runs.jsonl').open('w') as log:
        futures=[pool.submit(run_session,job,binary,out,source) for job in jobs]
        for future in as_completed(futures):
            row=future.result();rows.append(row);log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
    write(out/'TIMED_COMPLETE.json',dict(finished_utc=utc(),sessions=len(rows),all_successful=all(r['success'] for r in rows),
        wall_seconds=time.monotonic()-start,cpu_seconds=sum(r.get('cpu_seconds',0) for r in rows),load_average=os.getloadavg()))
    print(json.dumps(dict(stage='audit', sessions=len(rows))),flush=True)
    from audit_residual_online import run_audit
    passed=run_audit(out,out/'audit',min(a.jobs,6))
    unchanged=all(digest(path)==expected for path,expected in sources.items())
    write(out/'COMPLETE.json',dict(all_passed=passed and unchanged and all(r['success'] for r in rows),unchanged_sources=unchanged,sessions=36,
        finished_utc=utc(),no_followup_started=True,bindings={name:digest(out/name) for name in ('plan.json','runs.jsonl','TIMED_COMPLETE.json','audit/COMPLETE.json')}))
    from analyze_residual_online import analyze
    summary=analyze(out,out/'analysis')
    print(json.dumps(dict(stage='complete',all_passed=passed and unchanged,recommend_continue=summary['recommend_continue'])),flush=True)
    if not passed or not unchanged: raise SystemExit(1)

if __name__=='__main__': main()
