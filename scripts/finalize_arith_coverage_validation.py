#!/usr/bin/env python3
"""Finish isolated sanitizer build and bind passing regressions to exact artifacts."""
import argparse,json,os,subprocess,time
from pathlib import Path
from build_arith_coverage_dependency import ROOT,FILES,sha
OUT=ROOT/'results/arith_coverage_v1'
DEP=ROOT/'dependencies/z3-arith-coverage'
PY='/tmp/lia-highdiv-frozen/venv/bin/python3'
def dump(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
def run(cmd,log,env=None):
    print('Running',cmd,flush=True)
    with log.open('w') as stream:subprocess.run(cmd,cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT,check=True)
def main():
    a=argparse.ArgumentParser();a.add_argument('--wait-dependency-pid',type=int);a.add_argument('--existing-builds',action='store_true');a.add_argument('--release-evidence',default='release_final');a.add_argument('--sanitize-evidence',default='sanitize');v=a.parse_args();os.chdir(ROOT);dest=OUT/'correctness'
    if v.wait_dependency_pid:
        start=time.monotonic()
        while Path('/proc',str(v.wait_dependency_pid)).exists():
            stat=Path('/proc',str(v.wait_dependency_pid),'stat')
            try:
                if stat.read_text().split()[2]=='Z':break
            except FileNotFoundError:break
            assert time.monotonic()-start<3*3600,'dependency did not finish within three hours'
            time.sleep(2)
    proof=dest/'sanitize_dependency.json';assert proof.exists(),'sanitizer dependency failed; do not start sampling'
    if not v.existing_builds:
        # The initial asynchronous builder predated the explicit shared-link flag fix.
        # Re-link this new installation only, with no makefile regeneration/source changes.
        config=DEP/'build_sanitize/config.mk';text=config.read_text();rows=[]
        for line in text.splitlines():
            if line.startswith('SLINK_EXTRA_FLAGS='):line='SLINK_EXTRA_FLAGS=-fsanitize=address,undefined -lgmpxx -lgmp -lpthread -Wl,-soname,libz3.so'
            rows.append(line)
        config.write_text('\n'.join(rows)+'\n')
        run(['make','-C',str(DEP/'build_sanitize'),'-j6','-W','smt/smt.a','libz3.so'],dest/'sanitize_shared_link.log')
        run(['make','-C',str(DEP/'build_sanitize'),'-j6','install'],dest/'sanitize_install_final.log')
        recorded=json.loads(proof.read_text());recorded.update(library_sha256=sha(DEP/'install-sanitize/lib/libz3.so'),config=config.read_text(),links=subprocess.check_output(['ldd',str(DEP/'install-sanitize/lib/libz3.so')],text=True),modified_sources={f:sha(DEP/f) for f in FILES});recorded['patches']['z3_arith_coverage.patch']=sha(ROOT/'dependencies/patches/z3_arith_coverage.patch');dump(proof,recorded)
        build=ROOT/'build/arith_coverage/sanitize'
        run(['cmake','-S',str(ROOT),'-B',str(build),'-DCMAKE_BUILD_TYPE=Debug','-DCMAKE_CXX_FLAGS=-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer','-DZ3_PREFIX='+str(DEP/'install-sanitize'),'-DLIA_ENABLE_SOFT_PHASE=ON','-DLIA_ENABLE_NATIVE_FEEDBACK=ON','-DLIA_ENABLE_ARITH_COVERAGE=ON'],dest/'sanitize_project_configure.log')
        run(['cmake','--build',str(build),'-j6'],dest/'sanitize_project_build.log')
    build=ROOT/'build/arith_coverage/sanitize'
    env=dict(os.environ,LIA_NATIVE_DELIVERY_BINARY=str(build/'native_delivery_sampler'),LIA_ARITH_COVERAGE_BINARY=str(build/'arith_coverage_sampler'),HIGHDIV_ROOT='/tmp/lia-highdiv-frozen',PYTHONPATH='scripts:python')
    run(['python3','scripts/test_native_feedback_build.py','--build',str(build),'--out',str(dest/v.sanitize_evidence),'--sanitize'],dest/(v.sanitize_evidence+'_tests.log'),env)
    for mode,sub in [('release',v.release_evidence),('sanitize',v.sanitize_evidence)]:
        assert '100% tests passed' in (dest/sub/'ctest.txt').read_text()
        txt=(dest/sub/'python_unittest.txt').read_text();assert '\nOK\n' in txt and 'Ran 226 tests' in txt
        assert 'Native feedback and all applicable regressions passed' in (dest/(sub+'_tests.log')).read_text()
    historic=json.loads((ROOT/'results/position_fixed4_validation/summary.json').read_text())['files'];old={p:h for p,h in historic.items() if p.startswith('build/') or p.startswith('dependencies/z3-')}
    for p,h in old.items():assert sha(ROOT/p)==h,'historical artifact modified: '+p
    files=dict(old)
    for folder in ['src','include','tests']:
        for p in sorted((ROOT/folder).glob('*')):
            if p.is_file():files[str(p.relative_to(ROOT))]=sha(p)
    for p in [ROOT/'CMakeLists.txt',ROOT/'dependencies/patches/z3_arith_coverage.patch',ROOT/'docs/算术覆盖构造冻结实验协议.md',DEP/'install/lib/libz3.so',DEP/'install-sanitize/lib/libz3.so']:
        files[str(p.relative_to(ROOT))]=sha(p)
    for mode in ['release','sanitize']:
        for p in sorted((ROOT/'build/arith_coverage'/mode).iterdir()):
            if p.is_file() and os.access(p,os.X_OK):files[str(p.relative_to(ROOT))]=sha(p)
        proof=json.loads((dest/(mode+'_dependency.json')).read_text())
        for f,h in proof['modified_sources'].items():assert sha(DEP/f)==h,'source changed after build: '+f
    dump(dest/'summary.json',dict(passed=True,source_base='0e656558bb0f27388cf4ea2ef7f8d90975392079',builds={'release':{'ctest':23,'python':226},'sanitize':{'ctest':23,'python':226}},historical_binaries_unchanged=True,files=files,evidence_directories={'release':v.release_evidence,'sanitize':v.sanitize_evidence},test_deadline_review='search_failure_review/comparison.json; 50ms timeout reproduced with old and new libs; correctness-only bound 2000ms; formal budgets unchanged',prior_preflight_failures='preserved: trace=true repeats DAG visits; forbidden callback API reports error to outer caller; final tests assert intended behavior'))
    print('S0 complete; both builds and all regressions passed',flush=True)
if __name__=='__main__':main()
