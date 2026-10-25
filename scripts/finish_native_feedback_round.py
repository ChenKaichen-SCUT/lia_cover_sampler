#!/usr/bin/env python3
"""Run the frozen gates, eight exposures, then at most 24 prescribed sessions.

No tuning, no historical mutation and no additional performance stage on failure.
Can wait for the already-running isolated sanitizer dependency build.
"""
import argparse,hashlib,json,os,subprocess,time,traceback
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PYTHON='/tmp/lia-highdiv-frozen/venv/bin/python3'
def exposure_gate(runs,frozen):
    native=[r for r in runs if r['configuration'] in ('native_random_one','native_feedback_one')]
    checks=dict(eight_audited_sessions=len(runs)==8,four_native_sessions=len(native)==4,
        each_native_session_has_opportunity=all(r['selected']>0 for r in native),
        outside_catalog_actual=sum(r['outside_catalog'] for r in native)>0,
        keep_actual=sum(r['keep'] for r in native)>0,flip_actual=sum(r['flip'] for r in native)>0)
    return dict(passed=all(checks.values()),checks=checks,frozen_commit=frozen,selection_uses_coverage_results=False)
def main():
    p=argparse.ArgumentParser();p.add_argument('--wait-for-sanitizer-pid',type=int);p.add_argument('--push',action='store_true');a=p.parse_args()
    os.chdir(ROOT);out=ROOT/'results/native_feedback';out.mkdir(exist_ok=True)
    status=ROOT/'build/native_feedback/pipeline_status.json';env=dict(os.environ,HIGHDIV_ROOT='/tmp/lia-highdiv-frozen');env.pop('NATIVE_TEST_MAX_QUERIES',None)
    def update(stage,**extra):
        status.write_text(json.dumps(dict(stage=stage,pid=os.getpid(),utc=datetime.now(timezone.utc).isoformat(),**extra),indent=2)+'\n')
        print(stage,extra,flush=True)
    def run(command,label):
        update(label,command=command)
        log=(status.parent if label=='push_branch' else out)/(label+'.log')
        with log.open('w') as f:subprocess.run(command,cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
    def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()
    try:
        assert git('branch','--show-current')=='native-feedback-phase','wrong branch'
        subprocess.run(['git','merge-base','--is-ancestor','6b54afe','HEAD'],check=True)
        evidence=ROOT/'results/native_feedback_validation/sanitize_dependency.json'
        if not evidence.exists():
            assert a.wait_for_sanitizer_pid,'sanitizer library not built; no performance run permitted'
            update('waiting_for_sanitizer_dependency',builder_pid=a.wait_for_sanitizer_pid)
            deadline=time.monotonic()+7200
            while not evidence.exists():
                assert Path(f'/proc/{a.wait_for_sanitizer_pid}').exists(),'dependency builder exited without success evidence'
                assert time.monotonic()<deadline,'dependency build wait exceeded two hours'
                time.sleep(5)
        prefix=str(ROOT/'dependencies/z3-native-feedback-phase/install-sanitize')
        run(['cmake','-S','.','-B','build/native_feedback/sanitize','-DCMAKE_BUILD_TYPE=Debug','-DLIA_ENABLE_SOFT_PHASE=ON','-DLIA_ENABLE_NATIVE_FEEDBACK=ON','-DZ3_PREFIX='+prefix,
             '-DCMAKE_CXX_FLAGS=-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer -fno-pie','-DCMAKE_EXE_LINKER_FLAGS=-fsanitize=address,undefined -no-pie'],'sanitize_configure')
        run(['cmake','--build','build/native_feedback/sanitize','-j7'],'sanitize_project_build')
        # Test source and independent replay are now frozen. Both final logs include all cases.
        for mode in ('release','sanitize'):
            cmd=['python3','scripts/test_native_feedback_build.py','--build','build/native_feedback/'+mode,'--out','results/native_feedback_validation/'+mode]
            if mode=='sanitize':cmd.append('--sanitize')
            run(cmd,mode+'_final_regressions')
        run(['python3','scripts/finalize_native_feedback_build.py'],'freeze_build')
        # Do not launch from an unreviewed working-tree edit to any tracked source.
        assert not git('status','--short','--untracked-files=no'),'tracked source changed during gates'
        subprocess.run(['git','add','dependencies/native_feedback_build.json','results/native_feedback_validation'],check=True)
        subprocess.run(['git','commit','-m','Freeze native feedback Release and sanitizer regression evidence'],check=True)
        frozen=git('rev-parse','HEAD');update('performance_protocol_frozen',commit=frozen)
        for stage in ('exposure','screen'):
            path=out/('exposure_30s' if stage=='exposure' else 'screen_120s')
            run(['python3','scripts/run_native_feedback_pairs.py','--out',str(path),'--stage',stage,'--postprocess'],stage+'_sampling_audit_analysis')
            if stage=='exposure':
                result=json.loads((path/'analysis/summary.json').read_text());runs=result['sessions']
                gate=exposure_gate(runs,frozen)
                (out/'exposure_gate.json').write_text(json.dumps(gate,indent=2)+'\n')
                if not gate['passed']:break
        run(['python3','scripts/report_native_feedback_results.py'],'chinese_report')
        update('complete_local',frozen_commit=frozen,formal_started=(out/'screen_120s').exists(),automatic_further_experiments=False)
        (out/'pipeline_result.json').write_bytes(status.read_bytes())
        subprocess.run(['git','add','results/native_feedback','docs/原生决策反馈实现与实验报告.md'],check=True)
        subprocess.run(['git','commit','-m','Record native feedback paired results, complete archives and Chinese report'],check=True)
        if a.push:
            run(['git','push','-u','origin','native-feedback-phase'],'push_branch')
            update('complete_and_pushed',commit=git('rev-parse','HEAD'),branch='native-feedback-phase',automatic_further_experiments=False)
    except Exception as exc:
        update('stopped_on_error',error=str(exc),traceback=traceback.format_exc(),automatic_retry=False)
        raise
if __name__=='__main__':main()
