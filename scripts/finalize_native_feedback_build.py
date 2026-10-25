#!/usr/bin/env python3
"""Freeze dependency provenance only after both complete regression suites pass."""
import hashlib,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.file_digest(Path(p).open('rb'),'sha256').hexdigest() if hasattr(hashlib,'file_digest') else hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
    z=ROOT/'dependencies/z3-native-feedback-phase';validation=ROOT/'results/native_feedback_validation';files=[]
    for mode,install,build in [('release','install','build'),('sanitize','install-sanitize','build_sanitize')]:
        tests=validation/mode
        assert '100% tests passed' in (tests/'ctest.txt').read_text()
        text=(tests/'python_unittest.txt').read_text();assert '\nOK\n' in text and 'FAILED' not in text and 'skipped' not in text
        assert json.loads((tests/'native_feedback.jsonl').read_text())['passed']
        evidence=json.loads((validation/(mode+'_dependency.json')).read_text())
        assert sha(z/install/'lib/libz3.so')==evidence['library_sha256']
        for path,wanted in evidence['historical_library_sha256'].items():assert sha(Path(path))==wanted
        files+=[z/install/'lib/libz3.so',z/build/'config.mk',ROOT/f'build/native_feedback/{mode}/native_feedback_sampler',validation/(mode+'_dependency.json')]
        files+=sorted(p for p in tests.iterdir() if p.is_file())
    frozen=Path('/tmp/lia-highdiv-frozen/venv/lib/libz3.so');assert sha(frozen)=='c66acd3c4743444ce90b255820a88a65cbeaa4bd13808ed59ac7be91721967ca'
    files+=list(sorted((z/'install/include').glob('*')))+[ROOT/'dependencies/patches'/n for n in ('z3_soft_phase.patch','z3_native_feedback_phase.patch')]
    output=dict(base_commit='6b54afe',upstream_commit='f2c6e1756b13281d96cbf90a8bd940129e56cce4',frozen_library_sha256=sha(frozen),
        compiler=subprocess.check_output(['g++','--version'],text=True),files={str(p.relative_to(ROOT)):sha(p) for p in files},
        release_config=(z/'build/config.mk').read_text(),sanitizer_config=(z/'build_sanitize/config.mk').read_text(),
        runtime_links=subprocess.check_output(['ldd',str(ROOT/'build/native_feedback/release/native_feedback_sampler')],text=True),
        tests='Release and ASan/UBSan: all CTest and Python regressions, original soft hook and new native hook; no skipped test',
        reproduction='docs/原生决策反馈接口与正确性.md')
    (ROOT/'dependencies/native_feedback_build.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    print('Native feedback builds and full regression evidence frozen',flush=True)
if __name__=='__main__':main()
