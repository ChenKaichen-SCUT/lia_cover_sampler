#!/usr/bin/env python3
"""Background staged workflow. Failed development gates stop; no repeated monitoring required."""
import argparse,json,os,subprocess,sys,time,traceback,shutil
from pathlib import Path
from datetime import datetime,timezone
from run_iteration import ROOT,digest,write
from calibrate_concurrency import calibrate

def main():
    p=argparse.ArgumentParser();p.add_argument('--configs',type=Path,required=True);p.add_argument('--micro-configs',type=Path,required=True)
    p.add_argument('--snapshots',type=Path,required=True);p.add_argument('--prefix-audit',type=Path,required=True)
    p.add_argument('--state',type=Path,default=ROOT/'results/round3_dev/workflow.json')
    p.add_argument('--resume',action='store_true',help='resume a failed calibration; reuse verified completed generation')
    a=p.parse_args();a.configs=a.configs.resolve();a.micro_configs=a.micro_configs.resolve();a.snapshots=a.snapshots.resolve();a.prefix_audit=a.prefix_audit.resolve()
    previous=None;preserved=None
    if a.state.exists():
        if not a.resume:raise ValueError('workflow state exists; use explicit resume for a failed calibration')
        previous=json.loads(a.state.read_text())
        if previous['status']!='failed_preserved' or any(k in previous for k in ('micro','screen','confirm','final','concurrency')):
            raise ValueError('only a failed calibration may be resumed by this entry point')
        if previous['configs_sha256']!=digest(a.configs):raise ValueError('changed configs cannot resume frozen work')
        preserved=a.state.with_name('workflow.failed-'+digest(a.state)[:12]+'.json');shutil.copy2(a.state,preserved)
    elif a.resume:raise ValueError('no failed workflow to resume')
    state=dict(status='starting',pid=os.getpid(),started_utc=datetime.now(timezone.utc).isoformat(),configs=str(a.configs),
        configs_sha256=digest(a.configs),prefix_audit=str(a.prefix_audit),snapshots=str(a.snapshots),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        user_preference='run in background; user will request status',remaining_after_pipeline=['Review full evidence/report, archive final evidence once, push code/results to GitHub'])
    if previous:state.update(resumed_from=str(preserved),previous_workflow_wall_seconds=previous['workflow_wall_seconds'])
    def update(status,**values):
        state.update(status=status,checked_utc=datetime.now(timezone.utc).isoformat(),**values);write(a.state,state);print(status,flush=True)
    def run(command,log):
        with Path(log).open('w') as output:subprocess.run(command,cwd=ROOT,stdout=output,stderr=subprocess.STDOUT,check=True)
    python=Path(sys.executable);logs=ROOT/'results/round3_dev/builds';begin=time.monotonic()
    try:
        update('calibrating_concurrency')
        calibration=ROOT/'results/round3_dev/concurrency'
        selected=calibrate(a.configs,a.prefix_audit,calibration,python,resume=a.resume);jobs=selected['selected_jobs']
        update('running_fixed_snapshots',concurrency=str(calibration/'selection.json'),jobs=jobs)
        micro=ROOT/'results/round3_snapshots/micro'
        run([str(python),str(ROOT/'scripts/run_iteration.py'),'--stage','micro','--configs',str(a.micro_configs),
            '--snapshot-manifest',str(a.snapshots),'--output',str(micro),'--seconds','.06','--jobs',str(jobs),'--seeds','0,1,2'],logs/'micro.log')
        state['micro']=str(micro)
        promotion=None
        for stage,seconds,seeds,split,root in [
            ('screen',120,'0,1,2','development',ROOT/'results/round3_screen/main'),
            ('confirm',300,'0,1,2','development',ROOT/'results/round3_confirm/main'),
            ('final',900,'0','all',ROOT/'results/round3_final/main')]:
            update('running_'+stage,**{stage:str(root)})
            command=[str(python),str(ROOT/'scripts/run_iteration.py'),'--stage',stage,'--configs',str(a.configs),
                '--output',str(root),'--seconds',str(seconds),'--jobs',str(jobs),'--seeds',seeds,'--split',split,
                '--prefix-audit',str(a.prefix_audit),'--purpose',stage+' paired AB/candidate; unified online time and sample-prefix metrics']
            if promotion:command+=['--promotion',str(promotion)]
            run(command,logs/(stage+'_sampling.log'));update('auditing_'+stage)
            run([str(python),str(ROOT/'scripts/audit_iteration.py'),str(root),'--jobs',str(jobs)],logs/(stage+'_audit.log'))
            run([str(python),str(ROOT/'scripts/analyze_iteration.py'),str(root),'--micro-analysis',str(micro/'analysis.json')],logs/(stage+'_analysis.log'))
            if stage!='final':
                promotion=root/'promotion.json';decision=json.loads(promotion.read_text())
                if not decision['approved']:
                    update(stage+'_not_promoted',failed_checks=[k for k,v in decision['checks'].items() if not v]);break
            else:update('final_audited')
        update(state['status'],workflow_wall_seconds=time.monotonic()-begin)
        run([str(python),str(ROOT/'scripts/report_iteration.py'),'--state',str(a.state)],logs/'report.log')
        state['report']='docs/第三轮实验报告.md';state['awaiting_review']=True
        write(a.state,state)
        print('Evidence ready for review; no additional unselected jobs queued.',flush=True)
    except BaseException as error:
        update('failed_preserved',error=str(error),traceback=traceback.format_exc(),workflow_wall_seconds=time.monotonic()-begin)
        raise
if __name__=='__main__':main()
