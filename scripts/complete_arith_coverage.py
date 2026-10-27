#!/usr/bin/env python3
"""Finish the authorized frozen stage sequence; stop on every failure or failed gate."""
import argparse,json,os,subprocess,time
from pathlib import Path
from run_arith_coverage import ROOT,OUT,BASE,BRANCH,ALL_INPUTS
from run_native_feedback_pairs import dump,sha
PY='/tmp/lia-highdiv-frozen/venv/bin/python3'
CODE=['tests/search_test.cpp','CMakeLists.txt','dependencies/patches/z3_arith_coverage.patch','include/arith_coverage_bridge.hpp','src/arith_coverage_bridge.cpp','src/arith_coverage_sampler.cpp','tests/arith_coverage_hook_test.cpp','tests/test_arith_coverage_online.py','tests/test_arith_coverage_protocol.py']
CODE+=['scripts/'+n for n in ['build_arith_coverage_dependency.py','finalize_arith_coverage_validation.py','run_arith_coverage.py','audit_arith_coverage.py','analyze_arith_coverage.py','report_arith_coverage.py','complete_arith_coverage.py']]
CODE+=['docs/'+n for n in ['算术覆盖构造实施指引.md','算术覆盖构造接入审查.md','算术覆盖构造机制与正确性.md','算术覆盖构造冻结实验协议.md']]
ENV=dict(os.environ,HIGHDIV_ROOT='/tmp/lia-highdiv-frozen',PYTHONPATH='scripts:python')
def run(cmd,log=None):
    print('Running',cmd,flush=True)
    if log:
        with log.open('w') as f:subprocess.run(cmd,cwd=ROOT,env=ENV,stdout=f,stderr=subprocess.STDOUT,check=True)
    else:subprocess.run(cmd,cwd=ROOT,env=ENV,check=True)
def commit(paths,message):
    run(['git','add','--',*paths]);run(['git','commit','-m',message])
def main():
    p=argparse.ArgumentParser();p.add_argument('--wait-validation-pid',type=int);p.add_argument('--push',action='store_true');p.add_argument('--attempt',default='');a=p.parse_args();os.chdir(ROOT)
    assert subprocess.check_output(['git','branch','--show-current'],text=True).strip()==BRANCH
    assert not a.attempt or a.attempt.replace('_','').isalnum(),'invalid attempt identity'
    control='pipeline'+('_'+a.attempt if a.attempt else '')
    assert not (OUT/(control+'_started.json')).exists(),'one pipeline invocation; inspect preserved failure instead of automatic retry'
    # Keep the active controller log outside the tracked tree, to avoid a changing
    # tracked logfile invalidating the frozen worktree check during sampling.
    dump(OUT/(control+'_started.json'),dict(attempt=a.attempt,pid=os.getpid(),base=BASE,authorized_stages=['smoke','probe','screen_if_gate'],push=a.push))
    try:
        if a.wait_validation_pid:
            start=time.monotonic()
            while Path('/proc',str(a.wait_validation_pid)).exists():
                try:
                    if Path('/proc',str(a.wait_validation_pid),'stat').read_text().split()[2]=='Z':break
                except FileNotFoundError:break
                assert time.monotonic()-start<4*3600,'validation timeout; no sampling'
                time.sleep(2)
        proof=json.loads((OUT/'correctness/summary.json').read_text());assert proof['passed']
        for path,h in proof['files'].items():assert sha(ROOT/path)==h,'validated artifact changed: '+path
        dump(OUT/'build_manifest.json',dict(parent=BASE,validation=sha(OUT/'correctness/summary.json'),dependencies={mode:json.loads((OUT/'correctness'/f'{mode}_dependency.json').read_text()) for mode in ['release','sanitize']}))
        dump(OUT/'input_manifest.json',[dict(name=n,sha256=h,development=True) for n,h in ALL_INPUTS])
        tracked=subprocess.check_output(['git','diff','--name-only'],text=True).splitlines();assert all(p in CODE for p in tracked),'unrelated user changes; refuse staging'
        commit(CODE+[str(OUT.relative_to(ROOT))],'Implement bounded internal integer moves with pure AST scoring and verified lifecycle')
        completed=[]
        for stage in ['smoke','probe','screen']:
            if stage=='screen' and not json.loads((OUT/'probe/gate.json').read_text())['passed']:
                print('S2 gate failed: no 120-second screening is launched',flush=True);break
            run(['python3','scripts/run_arith_coverage.py','--prepare','--stage',stage])
            commit([str((OUT/stage).relative_to(ROOT))],f'Freeze arithmetic {stage} inputs, seeds, budgets and physical-core plan')
            run(['python3','scripts/run_arith_coverage.py','--run','--stage',stage],OUT/stage/'sampling.log')
            run([PY,'scripts/audit_arith_coverage.py',str(OUT/stage),'--jobs','6'],OUT/stage/'audit.log')
            run([PY,'scripts/analyze_arith_coverage.py',str(OUT/stage)],OUT/stage/'analysis.log')
            run([PY,'scripts/archive_bofill_results.py',str(OUT/stage)],OUT/stage/'archive.log')
            commit([str((OUT/stage).relative_to(ROOT))],f'Archive arithmetic {stage} sessions, complete models and independent coverage audit')
            completed.append(stage)
        run([PY,'scripts/report_arith_coverage.py',str(OUT)],OUT/'report.log')
        dump(OUT/'completion.json',dict(complete=True,stages=completed,highdiv_rerun=False,long_runs=False,screen_ran='screen' in completed))
        commit([str(OUT.relative_to(ROOT)),'docs/算术覆盖构造实验报告.md'],'Report bounded arithmetic construction evidence and frozen continue-stop decision')
        if a.push:run(['git','push','-u','origin',BRANCH])
        # Receipt is outside the archive to keep HEAD clean after the final push.
        dump(Path('/tmp/arith_coverage_publication.json'),dict(complete=True,pushed=a.push,commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),branch=BRANCH,stages=completed))
    except Exception as e:
        dump(OUT/(control+'_failure.json'),dict(attempt=a.attempt,error=repr(e),automatic_retry=False,no_automatic_next_stage=True));raise
if __name__=='__main__':main()
