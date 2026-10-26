#!/usr/bin/env python3
"""Bind the completed build/regression evidence before any performance run."""
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def cases(p):
    results={};pending=None
    for line in p.read_text().splitlines():
        if m:=re.match(r'^(test_\S+) \(([^)]+)\)',line):
            assert pending is None,'test result missing';pending=(m[2],m[1])
        if pending and (m:=re.search(r'(?:\.\.\. |^)(ok|FAIL|ERROR|skipped.*)$',line)):
            results[pending]=m[1];pending=None
    assert pending is None,'unterminated test result'
    return results
def main():
    root=ROOT;out=root/'results/native_delivery_validation'
    release=cases(out/'release/python_unittest.txt');san=cases(out/'sanitize_initial_frozen_guard/python_unittest.txt');san.update(cases(out/'sanitize_followup/python_unittest.txt'))
    assert len(release)==186 and len(san)==186 and set(release)==set(san) and set(release.values())=={'ok'} and set(san.values())=={'ok'},(len(release),len(san),set(san.values()))
    for p in ['release/ctest.txt','sanitize_initial_frozen_guard/ctest.txt']:assert '100% tests passed, 0 tests failed out of 22' in (out/p).read_text()
    assert (out/'release/postprocessing_followup.txt').read_text().endswith('OK\n')
    for p in ['release/native_check.json','sanitize_followup/native_check.json']:assert json.loads((out/p).read_text())['passed']
    paths=[root/'CMakeLists.txt',root/'docs/有界交付与干预位置实验协议.md',root/'build/native_delivery/release/native_delivery_sampler',root/'build/native_delivery/sanitize/native_delivery_sampler',root/'build/native_feedback/release/native_feedback_sampler',root/'dependencies/z3-native-feedback-phase/install/lib/libz3.so',root/'dependencies/z3-native-feedback-phase/install-sanitize/lib/libz3.so']
    for directory in ['src','include']:paths+=sorted(p for p in (root/directory).glob('*') if p.is_file())
    paths+=sorted((root/'scripts').glob('*native_delivery*.py'));paths+=sorted((root/'tests').glob('*native_delivery*.py'));paths+=sorted((root/'tests').glob('bounded_delivery*'));paths+=[root/'tests/test_native_frozen_900s.py']
    assert sha(root/'build/native_feedback/release/native_feedback_sampler')=='36fff382eaa72bd8f5238a9fdfac9ce36c627cf3fa03a1d4e6a135244dba9889'
    assert sha(root/'dependencies/z3-native-feedback-phase/install/lib/libz3.so')=='eb11f29555356fcda30d546c251bc1f77cb900336cea2e2abbb40484de9c2559'
    proof=dict(passed=True,release=dict(ctest=22,python=186),sanitize=dict(ctest=22,python_distinct_passed=186,method='Initial 183-test full run passed 182; old frozen-tree identity test rejected new research tree. Adapted identity test and affected/new tests passed in 15-test followup. Initial failure logs preserved. Historical controller and binaries unchanged.'),
        frozen_files={str(p.relative_to(root)):sha(p) for p in paths},evidence_sha256={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='summary.json'},
        commands=dict(release='LIA_NATIVE_DELIVERY_BINARY=$PWD/build/native_delivery/release/native_delivery_sampler /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/test_native_feedback_build.py --build build/native_delivery/release --out results/native_delivery_validation/release',sanitize_initial='LIA_NATIVE_DELIVERY_BINARY=$PWD/build/native_delivery/sanitize/native_delivery_sampler /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/test_native_feedback_build.py --build build/native_delivery/sanitize --out results/native_delivery_validation/sanitize --sanitize',sanitize_followup='PYTHONPATH=scripts:python:tests LIA_NATIVE_DELIVERY_BINARY=$PWD/build/native_delivery/sanitize/native_delivery_sampler /tmp/lia-highdiv-frozen/venv/bin/python3 -m unittest test_native_delivery test_native_delivery_analysis test_native_frozen_900s -v'),
        sanitizer_environment=dict(ASAN_OPTIONS='detect_leaks=1:halt_on_error=1:fast_unwind_on_malloc=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1',LSAN_OPTIONS='suppressions='+str(root/'results/round3_dev/sanitize_diagnostic/lsan_external_z3.supp')),sanitizer_cxx_flags='-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer')
    (out/'summary.json').write_text(json.dumps(proof,indent=2,ensure_ascii=False)+'\n');print(json.dumps(dict(passed=True,release=proof['release'],sanitize=proof['sanitize'])))
if __name__=='__main__':main()
