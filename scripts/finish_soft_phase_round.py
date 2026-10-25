#!/usr/bin/env python3
"""Fail-closed continuation: sanitizer build/tests -> exposure -> conditional screen.
No HighDiv invocation. Sampling starts only after all compilation and regressions.
"""
import argparse,json,os,shutil,subprocess,sys,time,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'results/soft_phase_validation/pipeline_status.json'
PYTHON='/tmp/lia-highdiv-frozen/venv/bin/python3'
def status(stage,**kw):
    STATE.parent.mkdir(parents=True,exist_ok=True);STATE.write_text(json.dumps(dict(stage=stage,updated_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),**kw),ensure_ascii=False,indent=2)+'\n');print(stage,flush=True)
def run(cmd,log,env=None):
    with (ROOT/log).open('w') as f:subprocess.run(cmd,cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
def main():
    a=argparse.ArgumentParser();a.add_argument('--sanitizer-make-pid',type=int,default=0);v=a.parse_args()
    p=Path('/proc')/str(v.sanitizer_make_pid);start=(p/'stat').read_text().split()[21] if p.exists() else None
    try:
        status('waiting_for_native_sanitizer_build',pid=v.sanitizer_make_pid)
        while True:
            try:fields=(p/'stat').read_text().split();current=fields[21]
            except FileNotFoundError:break
            if fields[2]=='Z':break
            if current!=start:break
            time.sleep(5)
        status('finish_and_verify_native_sanitizer_build')
        run(['make','-C','dependencies/z3-soft-phase/build_sanitize','-j8','libz3.so'],'results/soft_phase_validation/native_sanitize_make.txt')
        z=ROOT/'dependencies/z3-soft-phase';install=z/'install-sanitize';(install/'lib').mkdir(parents=True,exist_ok=True)
        shutil.copytree(z/'install/include',install/'include',dirs_exist_ok=True);shutil.copyfile(z/'build_sanitize/libz3.so',install/'lib/libz3.so')
        status('build_project_sanitizers')
        run(['cmake','-S','.','-B','build/soft_phase/sanitize','-DCMAKE_BUILD_TYPE=Debug','-DLIA_ENABLE_SOFT_PHASE=ON','-DZ3_PREFIX='+str(install),'-DCMAKE_CXX_FLAGS=-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer -fno-pie','-DCMAKE_EXE_LINKER_FLAGS=-fsanitize=address,undefined -no-pie'],'results/soft_phase_validation/sanitize_configure.txt')
        run(['cmake','--build','build/soft_phase/sanitize','-j8'],'results/soft_phase_validation/sanitize_build.txt')
        status('sanitizer_regressions')
        run([sys.executable,'scripts/test_soft_phase_build.py','--build','build/soft_phase/sanitize','--out','results/soft_phase_validation/sanitize','--sanitize'],'results/soft_phase_validation/sanitize_driver.txt')
        status('freeze_release_and_repeat_final_regressions')
        run(['cmake','--build','build/soft_phase/release','-j8'],'results/soft_phase_validation/release_build.txt')
        run([sys.executable,'scripts/test_soft_phase_build.py','--build','build/soft_phase/release','--out','results/soft_phase_validation/release'],'results/soft_phase_validation/release_driver.txt')
        run([sys.executable,'scripts/check_soft_phase_patch.py','--patched','dependencies/z3-soft-phase','--out','results/soft_phase_validation/hook_off'],'results/soft_phase_validation/hook_off_driver.txt')
        run([sys.executable,'scripts/finalize_soft_phase_build.py'],'results/soft_phase_validation/dependency_manifest.txt')
        for source,dest in [('/tmp/soft-z3-config.log','z3_release_configure.txt'),('/tmp/soft-z3-build.log','z3_release_build.txt'),('/tmp/soft-z3-install.log','z3_release_install.txt'),('/tmp/soft-z3-sanitize-build.log','z3_sanitize_build.txt')]:
            shutil.copyfile(source,ROOT/'results/soft_phase_validation'/dest)
        status('exposure_sampling_then_offline_audit')
        run([sys.executable,'scripts/run_soft_phase_pairs.py','--stage','exposure','--out','results/soft_phase_exposure','--postprocess'],'results/soft_phase_validation/exposure_controller.txt')
        exposure=ROOT/'results/soft_phase_exposure';audit=json.loads((exposure/'audit_summary.json').read_text());runs=audit['runs'];soft=[r for r in runs if r['configuration']!='plain']
        passed=audit['passed'] and len(runs)==6 and all(r['supported_atoms']>0 and r['phase_counters']['mapped']>0 and r['phase_counters']['applied']>0 for r in soft)
        gate=dict(passed=passed,rule='all four soft sessions: reliable catalog, mapped atoms and actual native phase overrides; independent coverage/summary audit passes; no coverage-win condition',sessions=[dict(formula=r['formula'],mode=r['configuration'],atoms=r['supported_atoms'],mapped=r['phase_counters']['mapped'],applied=r['phase_counters']['applied']) for r in soft])
        (exposure/'gate.json').write_text(json.dumps(gate,ensure_ascii=False,indent=2)+'\n')
        if passed:
            status('formal_sampling_then_offline_audit')
            run([sys.executable,'scripts/run_soft_phase_pairs.py','--stage','screen','--out','results/soft_phase_screen','--postprocess'],'results/soft_phase_validation/screen_controller.txt')
        status('write_evidence_report',formal_screen_ran=passed)
        run([PYTHON,'scripts/report_soft_phase_results.py'],'results/soft_phase_validation/report_generation.txt',dict(os.environ,HIGHDIV_ROOT='/tmp/lia-highdiv-frozen'))
        status('complete_ready_for_review_and_push',formal_screen_ran=passed,report='docs/覆盖软偏好实现与实验报告.md')
    except Exception as exc:
        status('failed_stopped_before_next_stage',error=str(exc));traceback.print_exc();raise
if __name__=='__main__':main()
