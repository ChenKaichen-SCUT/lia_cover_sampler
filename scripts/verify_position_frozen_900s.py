#!/usr/bin/env python3
"""Finalize regression/algorithm identity evidence before freezing the new plan."""
import json,os,subprocess
from pathlib import Path
from run_position_frozen_900s import ROOT,verify_algorithm,PY,sha
from finalize_native_delivery_validation import cases
from reevaluate_round6_saved_candidates import dump

def main():
    out=ROOT/'results/position_frozen_900s_validation';release=cases(out/'release/python_unittest.txt');release.update(cases(out/'release/final_analysis_followup.txt'))
    initial=cases(out/'sanitize/python_unittest.txt');bad={k:v for k,v in initial.items() if v!='ok'}
    assert bad=={('test_bofill.Bofill','test_bounded_time_no_sample_cap'):'FAIL'},bad
    sanitized=dict(initial);sanitized.update(cases(out/'sanitize/final_followup.txt'))
    assert len(release)==len(sanitized)==197 and set(release)==set(sanitized) and set(release.values())==set(sanitized.values())=={'ok'},(len(release),len(sanitized))
    for mode in ['release','sanitize']:assert '100% tests passed, 0 tests failed out of 22' in (out/mode/'ctest.txt').read_text()
    assert json.loads((out/'release/native_check.json').read_text())['passed']
    # The initial sanitizer harness stopped at the legacy timing assertion.
    # Run its remaining native lifecycle evidence on the same frozen binaries.
    env=dict(os.environ,ASAN_OPTIONS='detect_leaks=1:halt_on_error=1:fast_unwind_on_malloc=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1',LSAN_OPTIONS='suppressions='+str(ROOT/'results/round3_dev/sanitize_diagnostic/lsan_external_z3.supp'))
    for name,binary in [('native','native_soft_phase_test'),('native_feedback','native_feedback_test')]:
        target=out/'sanitize'/(name+'.jsonl')
        if not target.exists():
            with target.open('w') as f,(out/'sanitize'/(name+'.stderr')).open('w') as e:subprocess.run([str(ROOT/'build/native_delivery/sanitize'/binary)],stdout=f,stderr=e,env=env,check=True)
    abandoned=0
    for r in map(json.loads,(out/'sanitize/native.jsonl').read_text().splitlines()):
        if 'hook' not in r:continue
        final=dict(r['final']);used=[e for e in r['hook']['events'] if e['applied']]
        assert len(used)==r['hook']['applied']<=8 and len({e['group'] for e in used})==len(used)
        assert all(e['conflicts']<64 and e['preferred_truth']!=e['native_truth'] for e in used)
        abandoned+=sum(e['atom_id'] in final and e['preferred_truth']!=final[e['atom_id']] for e in used)
    assert abandoned>0;dump(out/'sanitize/native_check.json',dict(passed=True,actual_overrides_abandoned=abandoned))
    files=verify_algorithm()
    for folder in ['scripts','python']:files.update({str(p.relative_to(ROOT)):sha(p) for p in sorted((ROOT/folder).glob('*.py'))})
    for p in [ROOT/'tests/test_bofill.py',ROOT/'tests/test_position_frozen_900s.py',ROOT/'docs/原生干预位置900秒新seed冻结协议.md',ROOT/'dependencies/source_audit.json']:files[str(p.relative_to(ROOT))]=sha(p)
    metric=Path('/tmp/lia-highdiv-frozen/HighDiv/my_scripts/calc_metric.py');require_hash=json.loads((ROOT/'dependencies/source_audit.json').read_text())['HighDiv/my_scripts/calc_metric.py']['sha256'];assert sha(metric)==require_hash;files[str(metric)]=require_hash
    dump(out/'summary.json',dict(passed=True,algorithm_commit='75cd6b6',release_ctest=22,sanitize_ctest=22,python_distinct_tests_passed_per_build=197,method='Full release 196 plus 11-test final analysis/resource followup; full sanitizer 185/186 initially passed plus 12-test final followup and native lifecycle checks. The only initial failure was the historical 150ms Bofill liveness expectation; its 1s instrumented liveness window and original 150ms deadline/coverage audit both pass. All initial failure/diagnostic evidence retained. No sampler source, binary, library or experiment parameter changed.',files=files,evidence_sha256={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p not in [out/'summary.json',out/'verification.log']},regression_window_change=dict(test='test_bofill.Bofill.test_bounded_time_no_sample_cap',release_seconds=.15,instrumented_liveness_seconds=1,instrumented_deadline_seconds=.15,formal_experiment_seconds=900)))
    print(json.dumps(dict(passed=True,ctest_per_build=22,python_tests_per_build=197,algorithm_commit='75cd6b6')))
if __name__=='__main__':main()
