#!/usr/bin/env python3
"""Compare the same 15 development jobs at 3/6/9/12/15 concurrent processes."""
import argparse,json,subprocess,time
from collections import defaultdict
from pathlib import Path
from run_iteration import ROOT,write

def calibrate(configs,prefix_audit,output,python):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False);rows=[];start=time.monotonic()
    for jobs in (3,6,9,12,15):
        root=output/f'jobs-{jobs}'
        command=[str(python),str(ROOT/'scripts/run_iteration.py'),'--stage','screen','--configs',str(configs),
            '--select','candidate','--output',str(root),'--seconds','15','--jobs',str(jobs),'--seeds','0,1,2,3,4',
            '--prefix-audit',str(prefix_audit),'--purpose','concurrency calibration only, not algorithm comparison']
        with (output/f'jobs-{jobs}.log').open('w') as log:
            subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,cwd=ROOT)
            subprocess.run([str(python),str(ROOT/'scripts/audit_iteration.py'),str(root),'--jobs',str(jobs)],stdout=log,stderr=subprocess.STDOUT,check=True,cwd=ROOT)
        resources=json.loads((root/'resources.json').read_text());reports=json.loads((root/'candidate/audit_summary.json').read_text())
        audit=json.loads((root/'AUDIT_COMPLETE.json').read_text())
        work=defaultdict(int)
        for r in reports:work[r['instance']]+=r['unique_valid_in_budget']
        rows.append(dict(jobs=jobs,work=dict(work),wall_seconds=resources['wall_seconds'],cpu_seconds=resources['cpu_seconds'],
            cpu_utilization_fraction=resources['cpu_utilization_fraction'],peak_rss_kib=resources['peak_total_rss_kib'],
            context_switches=resources['context_switches'],cgroup_after=resources['cgroup_after'],
            audit_cpu_seconds=audit['cpu_seconds'],audit_wall_seconds=audit['wall_seconds']))
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
