#!/usr/bin/env python3
"""Freeze build, regression and historical-binary identity before sampling."""
import json,re
from pathlib import Path
from finalize_native_delivery_validation import cases
from run_native_feedback_pairs import ROOT,sha,dump

def main():
    out=ROOT/'results/position_fixed4_validation';counts={}
    for mode in ['release','sanitize']:
        p=out/mode;tests=cases(p/'python_unittest.txt');assert len(tests)>=220 and set(tests.values())=={'ok'},(mode,len(tests),set(tests.values()))
        assert '100% tests passed, 0 tests failed out of 22' in (p/'ctest.txt').read_text();assert json.loads((p/'native_check.json').read_text())['passed']
        assert json.loads((p/'native_feedback.jsonl').read_text())['passed'];counts[mode]=dict(ctest=22,python=len(tests))
    assert set(cases(out/'release_diagnostic_followup.txt').values())=={'ok'}
    assert len(cases(out/'final_diagnostic_followup.txt'))==14 and set(cases(out/'final_diagnostic_followup.txt').values())=={'ok'}
    assert len(cases(out/'analysis_end_to_end.txt'))==4 and set(cases(out/'analysis_end_to_end.txt').values())=={'ok'}
    # Explicit historical evidence; do not derive an expected hash from current files.
    frozen={'build/native_delivery/release/native_delivery_sampler':'c9a3864366e38af02ee385815ad2e667542019b09ca53964df2ef7a995b4c8b5','build/native_delivery/sanitize/native_delivery_sampler':'30e8ebe8d98257a7d9730cc5083cb8934682c12ee7e4598a991dbe5ebae177d6','build/native_feedback/release/native_feedback_sampler':'36fff382eaa72bd8f5238a9fdfac9ce36c627cf3fa03a1d4e6a135244dba9889','dependencies/z3-native-feedback-phase/install/lib/libz3.so':'eb11f29555356fcda30d546c251bc1f77cb900336cea2e2abbb40484de9c2559','dependencies/z3-native-feedback-phase/install-sanitize/lib/libz3.so':'ed4e2d04ef46e9d12cd83a87689c83ee16fd9b8dcd863f007c8d984cb89704ee'}
    for p,h in frozen.items():assert sha(ROOT/p)==h,'historical binary/library changed: '+p
    paths=[ROOT/'CMakeLists.txt',ROOT/'docs/固定第四位置120秒消融冻结协议.md']+[ROOT/p for p in frozen]
    for directory,pattern in [('src','*'),('include','*'),('scripts','*position_fixed4.py'),('tests','*position_fixed4.py')]:paths+=sorted(p for p in (ROOT/directory).glob(pattern) if p.is_file())
    paths += [ROOT/p for p in ['scripts/audit_native_delivery_results.py','tests/native_feedback_test.cpp','tests/test_native_delivery.py','tests/bounded_delivery_test.cpp']]
    paths += [ROOT/'build/position_fixed4'/mode/'native_delivery_sampler' for mode in ['release','sanitize']]
    proof=dict(passed=True,builds=counts,release_diagnostic_followup=len(cases(out/'release_diagnostic_followup.txt')),pure_python_analysis_followup=4,final_diagnostic_followup=14,historical_binaries_unchanged=True,source_base='6d2cab4',files={str(p.relative_to(ROOT)):sha(p) for p in paths},evidence={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='summary.json'},commands={mode:f'HIGHDIV_ROOT=/tmp/lia-highdiv-frozen PYTHONPATH=scripts:python LIA_NATIVE_DELIVERY_BINARY=$PWD/build/position_fixed4/{mode}/native_delivery_sampler /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/test_native_feedback_build.py --build build/position_fixed4/{mode} --out results/position_fixed4_validation/{mode}'+(' --sanitize' if mode=='sanitize' else '') for mode in ['release','sanitize']},initial_build_fix='An existing delivery PositionPolicy enum collided with the new enum; renamed NativePositionPolicy before successful rebuild. Original failure log retained. No performance experiment had started.')
    dump(out/'summary.json',proof);print(json.dumps(dict(passed=True,builds=counts,historical_binaries_unchanged=True)))
if __name__=='__main__':main()
