#!/usr/bin/env python3
"""Content-bound audit reuse for NEW iteration directories; historical evidence stays immutable."""
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
from collections import defaultdict
import hashlib,json,os,resource,time
from pathlib import Path
from run_iteration import ROOT,digest,write
from audit_experiments import audit_job

def bindings(root,job,seconds):
    folder=root/job['run'];metric=Path(os.environ['HIGHDIV_ROOT'])/'HighDiv/my_scripts/calc_metric.py'
    files=[ROOT/'benchmarks'/job['instance'],folder/'models.jsonl',folder/'emission.csv',metric,
           ROOT/'python/validation.py',ROOT/'python/coverage_differential_test.py',ROOT/'scripts/audit_experiments.py',
           Path(__file__),ROOT/'build/lia_cover_sampler',
           Path(os.environ['HIGHDIV_ROOT'])/'venv/lib/libz3.so']
    for name in ('summary.json','progress.json'):
        if (folder/name).exists():files.append(folder/name)
    return dict(files={str(p):digest(p) for p in files},deadline=seconds,metric='original-wire64-lazy-v1',
                job_sha256=hashlib.sha256(json.dumps(job,sort_keys=True).encode()).hexdigest(),filter_reconstructed=False)

def audit_one(payload):
    root,job,seconds=payload;root=Path(root);started=time.monotonic();before=resource.getrusage(resource.RUSAGE_SELF)
    key=bindings(root,job,seconds);report_path=root/'audit'/job['instance']/f'seed-{job["seed"]}.json'
    stamp=report_path.with_suffix('.binding.json');hit=False
    if stamp.exists() and report_path.exists():
        previous=json.loads(stamp.read_text());hit=previous.get('bindings')==key and previous.get('report_sha256')==digest(report_path)
    if not hit and report_path.exists():
        backup=report_path.with_name(report_path.name+'.stale-'+digest(report_path)[:12]);report_path.replace(backup)
    report=audit_job((str(root),job,'full',seconds,False))
    after=resource.getrusage(resource.RUSAGE_SELF)
    elapsed=time.monotonic()-started;cpu=after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime
    if not hit:write(stamp,dict(bindings=key,report_sha256=digest(report_path),audit_seconds=elapsed,audit_cpu_seconds=cpu))
    if any(report['counts'][k] for k in ('invalid','unknown','malformed','duplicate','coverage_mismatch','after_deadline','timestamp_missing')):
        raise RuntimeError('correctness failure; preserve evidence and stop promotion: '+str(report_path))
    return str(root),report,dict(cache_hit=hit,wall_seconds=elapsed,cpu_seconds=cpu)

def audit(root,jobs):
    root=Path(root).resolve();complete=json.loads((root/'COMPLETE.json').read_text())
    if complete['cancelled'] or complete['errors']:raise ValueError('generation failed; do not promote')
    payloads=[]
    for folder in sorted(root.iterdir()):
        if not(folder/'manifest.json').exists():continue
        manifest=json.loads((folder/'manifest.json').read_text())
        for row in map(json.loads,(folder/'runs.jsonl').read_text().splitlines()):payloads.append((str(folder),row,manifest['seconds']))
    began=time.monotonic();reports=defaultdict(list);cost=[]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        for future in as_completed([pool.submit(audit_one,p) for p in payloads]):
            folder,report,usage=future.result();reports[folder].append(report);cost.append(usage)
    for folder,rows in reports.items():write(Path(folder)/'audit_summary.json',sorted(rows,key=lambda r:(r['instance'],r['seed'])))
    result=dict(runs=len(cost),all_passed=True,wall_seconds=time.monotonic()-began,cpu_seconds=sum(r['cpu_seconds'] for r in cost),
                worker_seconds=sum(r['wall_seconds'] for r in cost),cache_hits=sum(r['cache_hit'] for r in cost),workers=jobs)
    write(root/'AUDIT_COMPLETE.json',result);print(json.dumps(result));return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('--jobs',type=int,default=15)
    a=p.parse_args();audit(a.root,a.jobs)
if __name__=='__main__':main()
