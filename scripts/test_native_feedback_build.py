#!/usr/bin/env python3
"""Run new native-feedback and all applicable original regressions against one build."""
import argparse,os,subprocess,sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    a=argparse.ArgumentParser();a.add_argument('--build',required=True);a.add_argument('--out',required=True);a.add_argument('--sanitize',action='store_true');v=a.parse_args();b=Path(v.build).resolve();out=Path(v.out);out.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,HIGHDIV_ROOT='/tmp/lia-highdiv-frozen')
    mapping={'LIA_BINARY':'lia_cover_sampler','LIA_AB_BINARY':'ab_sampler','LIA_PREPARATION_PROBE':'preparation_probe','LIA_COVERAGE_BLOCK_PROBE':'coverage_block_probe','LIA_RESIDUAL_ONLINE_PROBE':'residual_online_probe','LIA_MACRO_ONLINE_PROBE':'macro_online_probe','LIA_MACRO_RETRY_PROBE':'macro_retry_probe','LIA_MACRO_DIAGNOSTIC_PROBE':'macro_diagnostic_probe','LIA_BOFILL_BINARY':'bofill_sampler','LIA_BOFILL_FIRST_SAT_PROBE':'bofill_first_sat_probe','LIA_SOFT_PHASE_BINARY':'soft_phase_sampler','LIA_NATIVE_FEEDBACK_BINARY':'native_feedback_sampler'}
    for key,value in mapping.items():env[key]=str(b/value)
    if v.sanitize:
        # Both project and dependency keep frame pointers in this build.
        env['ASAN_OPTIONS']='detect_leaks=1:halt_on_error=1:fast_unwind_on_malloc=1'
        env['UBSAN_OPTIONS']='halt_on_error=1:print_stacktrace=1'
        env['LSAN_OPTIONS']='suppressions='+str(ROOT/'results/round3_dev/sanitize_diagnostic/lsan_external_z3.supp')
    with (out/'ctest.txt').open('w') as f:subprocess.run(['ctest','--test-dir',str(b),'--output-on-failure'],stdout=f,stderr=subprocess.STDOUT,env=env,check=True)
    with (out/'python_unittest.txt').open('w') as f:subprocess.run(['/tmp/lia-highdiv-frozen/venv/bin/python3','-m','unittest','discover','-s','tests','-p','test_*.py','-v'],stdout=f,stderr=subprocess.STDOUT,env=env,cwd=ROOT,check=True)
    with (out/'native.jsonl').open('w') as f:subprocess.run([str(b/'native_soft_phase_test')],stdout=f,env=env,check=True)
    records=[json.loads(line) for line in (out/'native.jsonl').read_text().splitlines()]
    abandoned=0
    for r in records:
        if 'hook' not in r:continue
        final=dict(r['final']);events=r['hook']['events'];used=[e for e in events if e['applied']]
        assert len(used)==r['hook']['applied']<=8
        assert len({e['group'] for e in used})==len(used)
        assert all(e['conflicts']<64 and e['preferred_truth']!=e['native_truth'] for e in used)
        abandoned+=sum(e['atom_id'] in final and e['preferred_truth']!=final[e['atom_id']] for e in used)
    assert abandoned>0,'no actual override abandoned in final model: incomplete mechanism test'
    (out/'native_check.json').write_text(json.dumps(dict(passed=True,actual_overrides_abandoned=abandoned),indent=2)+'\n')
    with (out/'native_feedback.jsonl').open('w') as f:subprocess.run([str(b/'native_feedback_test')],stdout=f,env=env,check=True)
    print('Native feedback and all applicable regressions passed',flush=True)
if __name__=='__main__':main()
