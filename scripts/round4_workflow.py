#!/usr/bin/env python3
"""Run A/B/C once in order, then stop for evidence review; never launch long runs."""
import argparse,json,os,subprocess,sys,time,traceback
from pathlib import Path
from datetime import datetime,timezone
from run_iteration import ROOT,digest,write
def main():
    p=argparse.ArgumentParser();p.add_argument('--configs',type=Path,required=True);p.add_argument('--prefix-audit',type=Path,required=True)
    p.add_argument('--probe',type=Path,required=True);p.add_argument('--replay-binary',type=Path,required=True)
    p.add_argument('--state',type=Path,default=ROOT/'results/round4_dev/workflow.json');a=p.parse_args()
    if a.state.exists():raise ValueError('existing workflow evidence; refusing overwrite')
    start=time.monotonic();logs=ROOT/'results/round4_dev/builds';python=sys.executable
    build_path=logs/'final.json';build=json.loads(build_path.read_text())
    for file,sha in build['source_hashes'].items():
        if digest(ROOT/file)!=sha:raise ValueError('Source differs from frozen build: '+file)
    for item in build['binaries'].values():
        if digest(item['path'])!=item['sha256']:raise ValueError('Frozen binary modified')
    assert a.probe.resolve()==Path(build['binaries']['snapshot_probe']['path'])
    assert a.replay_binary.resolve()==Path(build['binaries']['ab_replay']['path'])
    configs=json.loads(a.configs.read_text());assert set(configs)=={'ab_reference','ab_polish'}
    assert all(c['binary_sha256']==build['binaries']['ab_sampler']['sha256'] for c in configs.values())
    assert json.loads(a.prefix_audit.read_text())['binary_sha256']==build['binaries']['ab_sampler']['sha256']
    state=dict(pid=os.getpid(),started_utc=datetime.now(timezone.utc).isoformat(),jobs=12,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        frozen_build=str(build_path),frozen_build_sha256=digest(build_path),
        workflow_sha256=digest(__file__),analysis_sha256=digest(ROOT/'scripts/analyze_round4.py'),
        configs=str(a.configs.resolve()),configs_sha256=digest(a.configs),pending_after_pipeline=['Review complete evidence, report and publish code/results'])
    def update(status,**extra):state.update(status=status,checked_utc=datetime.now(timezone.utc).isoformat(),**extra);write(a.state,state);print(status,flush=True)
    def run(name,command):
        with (logs/(name+'.log')).open('w') as log:subprocess.run([python,*map(str,command)],stdout=log,stderr=subprocess.STDOUT,cwd=ROOT,check=True)
    try:
        diag=ROOT/'results/round4_diagnostic/main';replay=ROOT/'results/round4_replay/main';screen=ROOT/'results/round4_screen/main'
        update('running_A_scoped_diagnosis',diagnosis=str(diag),replay=str(replay),screen=str(screen))
        run('stage_A',[ROOT/'scripts/diagnose_round4.py','--probe',a.probe.resolve(),'--output',diag,'--jobs','12'])
        diagnosis=json.loads((diag/'COMPLETE.json').read_text());assert diagnosis['runs']==324 and diagnosis['all_returned_models_audited']
        update('running_B_fixed_raw_transform')
        run('stage_B',[ROOT/'scripts/replay_ab_prefix.py','--binary',a.replay_binary.resolve(),'--output',replay,'--jobs','12'])
        fixed=json.loads((replay/'COMPLETE.json').read_text());assert fixed['runs']==9 and fixed['all_valid'] and fixed['all_preserved']
        update('running_C_120s_screen')
        run('stage_C_sampling',[ROOT/'scripts/run_iteration.py','--stage','screen','--configs',a.configs.resolve(),'--output',screen,
            '--seconds','120','--jobs','12','--seeds','0,1,2','--prefix-audit',a.prefix_audit.resolve(),'--purpose','Round4 paired AB reference vs isolated bounded safe output polish; formal online prefixes'])
        update('auditing_C_formal_outputs')
        run('stage_C_audit',[ROOT/'scripts/audit_iteration.py',screen,'--jobs','12'])
        update('auditing_C_raw_formal_isolation')
        run('stage_C_states',[ROOT/'scripts/audit_ab_states.py',screen,'--jobs','12'])
        assert digest(ROOT/'scripts/analyze_round4.py')==state['analysis_sha256'],'Screen rules changed during execution'
        run('stage_C_analysis',[ROOT/'scripts/analyze_round4.py',screen,'--diagnosis',diag,'--replay',replay])
        decision=json.loads((screen/'promotion.json').read_text())
        update('screen_promising' if decision['approved'] else 'screen_not_promoted',failed_checks=[k for k,v in decision['checks'].items() if not v],workflow_wall_seconds=time.monotonic()-start,awaiting_review=True)
        run('report',[ROOT/'scripts/report_round4.py','--state',a.state]);state['report']='docs/第四轮实验报告.md';write(a.state,state)
    except BaseException as error:
        update('failed_preserved',error=str(error),traceback=traceback.format_exc(),workflow_wall_seconds=time.monotonic()-start);raise
if __name__=='__main__':main()
