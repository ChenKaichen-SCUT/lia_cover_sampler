#!/usr/bin/env python3
"""Replay existing development snapshots; do not change the generator's strategy."""
import argparse,csv,json,subprocess,sys,time
from pathlib import Path
from collections import Counter,defaultdict
from run_iteration import ROOT,digest,write
from check_diagnostic_witnesses import verify_file

def main():
    p=argparse.ArgumentParser();p.add_argument('--probe',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--jobs',type=int,default=12);a=p.parse_args();a.output=a.output.resolve();a.output.mkdir(parents=True,exist_ok=False)
    a.probe=a.probe.resolve();snapshots=ROOT/'results/round3_snapshots/states/manifest.json'
    configs={n:dict(binary=str(a.probe),binary_sha256=digest(a.probe),mode=mode,step_candidates=4)
        for n,mode in [('off','diagnostic_off'),('counters','new'),('trace','diagnostic')]}
    write(a.output/'configs.json',configs)
    command=[sys.executable,str(ROOT/'scripts/run_iteration.py'),'--stage','micro','--configs',str(a.output/'configs.json'),
        '--snapshot-manifest',str(snapshots),'--output',str(a.output/'replay'),'--seconds','.06','--jobs',str(a.jobs),'--seeds','0,1,2']
    with (a.output/'replay.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    manifest=json.loads(snapshots.read_text());lookup={s['id']:s for s in manifest['snapshots']}
    rows=[json.loads(s) for s in (a.output/'replay/runs.jsonl').read_text().splitlines()]
    rows.sort(key=lambda r:(r['snapshot'],r['module'],r['seed'],r['configuration']))
    grouped=defaultdict(lambda:[0,0.]);light=[];checks=[];checked=Counter();budget_followups={}
    check_start=time.monotonic()
    for r in rows:
        snapshot=lookup[r['snapshot']];path=a.output/'replay'/r['output'];data=json.loads(path.read_text())
        if r['configuration']=='counters':
            for key,value in r['statistics']['counts'].items():
                if key.startswith('diag_'):light.append(dict(instance=r['instance'],seed=r['seed'],snapshot=r['snapshot'],module=r['module'],code=key,count=value,seconds=r['statistics']['seconds'].get(key,0)))
        if r['configuration']!='trace':continue
        for event in data['diagnostics']['records']:
            if event['unit']=='context':continue
            key=(r['instance'],r['seed'],r['snapshot'],r['module'],event['source'],event['unit'],event['code'],event['stage'])
            grouped[key][0]+=1;grouped[key][1]+=event['seconds']
            if 'BUDGET' in event['code'] and r['module'] in ('safe','line'):
                budget_followups.setdefault((r['instance'],r['module']),r)
        if checked[r['instance'],r['module']]<2:
            prefix=json.loads((Path(snapshot['folder'])/'assignments.json').read_text())['prefix']
            checks+=verify_file(path,Path(snapshot['formula']).read_text(),prefix,cap=2)
            checked[r['instance'],r['module']]+=1
    def save_csv(file,records):
        if not records:return
        with file.open('w') as f:w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
    save_csv(a.output/'lightweight_reasons.csv',light)
    save_csv(a.output/'trace_reasons.csv',[dict(zip(('instance','seed','snapshot','module','source','unit','code','stage'),k),count=v[0],seconds=v[1]) for k,v in sorted(grouped.items())])
    write(a.output/'independent_checks.json',dict(checks=checks,wall_seconds=time.monotonic()-check_start,
        unknown=sum(c['result']=='unknown' for c in checks),all_noncontradictory=all(c['passed'] for c in checks)))
    # A separate larger diagnostic budget does not overwrite or relabel the 60ms result.
    followups=[]
    for index,r in enumerate(budget_followups.values()):
        path=a.output/f'budget_followup_{index}.json';command=list(r['command']);command[6]='0.3';command[8]=str(path)
        start=time.monotonic();subprocess.run(command,check=True,capture_output=True,timeout=30)
        data=json.loads(path.read_text());followups.append(dict(original_output=r['output'],output=str(path),module=r['module'],instance=r['instance'],
            seed=r['seed'],original_budget=.06,diagnostic_budget=.3,process_wall_seconds=time.monotonic()-start,status=data['status'],gain=data['gain'],
            limitation='Separate replay, not a correction of the original 60ms diagnosis'))
        prefix=json.loads((Path(lookup[r['snapshot']]['folder'])/'assignments.json').read_text())['prefix']
        verify_file(path,Path(lookup[r['snapshot']]['formula']).read_text(),prefix,cap=1)
        from snapshot_replay import oracle
        from validation import verify
        audit_start=time.monotonic()
        root,decls,stat,evaluation,storage,expected,sample=oracle(lookup[r['snapshot']])
        assert data['snapshot']==expected
        if data['candidate'] is not None:
            assert verify(root,data['candidate'],declarations=decls)=='valid'
            stat._storage=storage.copy();assert evaluation.count_sample(sample(data['candidate']))
            assert sum(stat.node_count(*s) for s in stat._storage.values())-expected['covered']==data['gain']
        followups[-1]['audit_seconds']=time.monotonic()-audit_start
    write(a.output/'budget_followups.json',followups)
    write(a.output/'COMPLETE.json',dict(runs=len(rows),all_returned_models_audited=True,scoped_checks=len(checks),unknown=sum(c['result']=='unknown' for c in checks),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),probe_sha256=digest(a.probe),
        scope='Replay of existing development snapshots; never recovered original per-call history',
        cost_accounting='replay/analysis.json contains process CPU, wall and independent coverage audit CPU; one-parameter checks and followups separately timed'))
    print('diagnosis complete',len(rows),'replays',len(checks),'independent scoped checks',flush=True)
if __name__=='__main__':main()
