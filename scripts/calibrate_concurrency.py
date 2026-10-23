#!/usr/bin/env python3
"""Compare the same 15 development jobs at 3/6/9/12/15 concurrent processes."""
import argparse,json,subprocess,time
from collections import defaultdict
from pathlib import Path
from run_iteration import ROOT,write,digest

def verify_reusable(root,configs,prefix_audit,jobs):
    plan=json.loads((root/'plan.json').read_text());done=json.loads((root/'COMPLETE.json').read_text())
    expected=json.loads(Path(configs).read_text())['candidate']
    if (done['cancelled'] or done['errors'] or done['runs']!=15 or plan['configs']!={'candidate':expected}
        or plan['seconds']!=15 or plan['workers']!=jobs or plan['seeds']!=[0,1,2,3,4]
        or plan['split']!='development' or plan['samples']!=4294967295
        or plan['prefix_audit_sha256']!=digest(prefix_audit)):
        raise ValueError('existing calibration does not match frozen requested work')
    for filename,key in [('plan.json','plan_sha256'),('queue.jsonl','queue_sha256'),('resources.json','resources_sha256')]:
        if digest(root/filename)!=done[key]:raise ValueError('changed completed calibration evidence')
    if digest(expected['binary'])!=expected['binary_sha256']:raise ValueError('frozen binary changed')

def calibrate(configs,prefix_audit,output,python,resume=False):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=resume);rows=[];start=time.monotonic()
    if (output/'selection.json').exists():raise ValueError('completed calibration cannot be overwritten')
    for jobs in (3,6,9,12,15):
        root=output/f'jobs-{jobs}'
        command=[str(python),str(ROOT/'scripts/run_iteration.py'),'--stage','screen','--configs',str(configs),
            '--select','candidate','--output',str(root),'--seconds','15','--jobs',str(jobs),'--seeds','0,1,2,3,4',
            '--prefix-audit',str(prefix_audit),'--purpose','concurrency calibration only, not algorithm comparison']
        reuse=resume and root.exists()
        if reuse:verify_reusable(root,configs,prefix_audit,jobs)
        with (output/f'jobs-{jobs}.log').open('a' if reuse else 'x') as log:
            if reuse:
                log.write('\nResume: generation evidence verified; reusing all saved samples.\n');log.flush()
            else:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,cwd=ROOT)
            subprocess.run([str(python),str(ROOT/'scripts/audit_iteration.py'),str(root),'--jobs',str(jobs)],stdout=log,stderr=subprocess.STDOUT,check=True,cwd=ROOT)
        resources=json.loads((root/'resources.json').read_text());reports=json.loads((root/'candidate/audit_summary.json').read_text())
        audit=json.loads((root/'AUDIT_COMPLETE.json').read_text())
        # Old audit completions are kept even when a changed reader invalidates the cache.
        audits={digest(p):json.loads(p.read_text()) for p in root.glob('AUDIT_COMPLETE.json*') if '.tmp' not in p.name}
        work=defaultdict(int)
        for r in reports:work[r['instance']]+=r['unique_valid_in_budget']
        rows.append(dict(jobs=jobs,work=dict(work),wall_seconds=resources['wall_seconds'],cpu_seconds=resources['cpu_seconds'],
            cpu_utilization_fraction=resources['cpu_utilization_fraction'],peak_rss_kib=resources['peak_total_rss_kib'],
            context_switches=resources['context_switches'],cgroup_after=resources['cgroup_after'],
            audit_cpu_seconds=sum(a['cpu_seconds'] for a in audits.values()),
            audit_wall_seconds=sum(a['wall_seconds'] for a in audits.values()),generation_reused=reuse))
    max_work={case:max(r['work'][case] for r in rows) for case in rows[0]['work']}
    memory=Path('/sys/fs/cgroup/memory.max').read_text().strip();memory_limit=int(memory) if memory!='max' else 2**63
    for row in rows:
        row['normalized_work_per_second']=sum(row['work'][case]/value for case,value in max_work.items() if value)/row['wall_seconds']
        row['memory_stable']=row['peak_rss_kib']*1024 < memory_limit*.8
    allowed=[r for r in rows if r['memory_stable']]
    if not allowed:raise RuntimeError('no memory-stable concurrency choice')
    selected=max(allowed,key=lambda r:r['normalized_work_per_second'])
    result=dict(selected_jobs=selected['jobs'],rows=rows,wall_seconds=time.monotonic()-start,
        criterion='Same cases/seeds/15s caps. Sum of per-case normalized independently valid outputs / sampling wall, subject to <80% memory limit.',
        caveat='Exploratory throughput calibration, not a strict coverage comparison across resource levels. No claim of global optimal concurrency.')
    write(output/'selection.json',result);return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--configs',type=Path,required=True);p.add_argument('--prefix-audit',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--python',type=Path,default=Path('/tmp/lia-highdiv-frozen/venv/bin/python3'))
    a=p.parse_args();print(json.dumps(calibrate(a.configs.resolve(),a.prefix_audit.resolve(),a.output,a.python)))
if __name__=='__main__':main()
