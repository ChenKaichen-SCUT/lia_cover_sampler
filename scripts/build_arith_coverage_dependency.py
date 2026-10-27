#!/usr/bin/env python3
"""Build an isolated Z3 library from the exact historical subtree plus three patches."""
import argparse,json,os,subprocess,tarfile,tempfile,shutil,hashlib,difflib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE='f2c6e1756b13281d96cbf90a8bd940129e56cce4'
PATCHES=['z3_soft_phase.patch','z3_native_feedback_phase.patch','z3_arith_coverage.patch']
FILES=['src/smt/arith_sampling.h','src/smt/arith_sampling_impl.h','src/util/lia_sampling_math.h','src/smt/smt_context.h','src/smt/smt_context.cpp','src/solver/solver.h','src/smt/smt_solver.cpp','src/api/z3_api.h','src/api/api_solver.cpp','src/math/lp/int_solver.h','src/smt/theory_lra.cpp']
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
 return h.hexdigest()
def save_patch(root):
 old=ROOT/'dependencies/z3-native-feedback-phase';parts=[]
 for f in FILES:
  p=old/f;q=root/f
  parts.extend(difflib.unified_diff(p.read_text().splitlines(True) if p.exists() else [],q.read_text().splitlines(True),fromfile='a/'+f if p.exists() else '/dev/null',tofile='b/'+f))
 (ROOT/'dependencies/patches/z3_arith_coverage.patch').write_text(''.join(parts))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--jobs',type=int,default=6);ap.add_argument('--out',type=Path,default=ROOT/'dependencies/z3-arith-coverage');ap.add_argument('--sanitize',action='store_true');ap.add_argument('--save-patch',action='store_true');a=ap.parse_args();root=a.out.resolve()
 if a.save_patch:save_patch(root);return
 if not root.exists():
  root.mkdir(parents=True)
  with tempfile.TemporaryFile() as f:
   subprocess.run(['git','archive',BASE+':z3'],cwd='/tmp/lia-highdiv-frozen',stdout=f,check=True);f.seek(0)
   with tarfile.open(fileobj=f) as t:t.extractall(root,filter='data')
  for name in PATCHES:subprocess.run(['patch','-p1','-i',str(ROOT/'dependencies/patches'/name)],cwd=root,check=True)
 mode='sanitize' if a.sanitize else 'release';build='build_sanitize' if a.sanitize else 'build';install=root/('install-sanitize' if a.sanitize else 'install');assert not install.exists(),'never overwrite installed library'
 old=ROOT/'dependencies/z3-native-feedback-phase';prior=old/build
 assert sha(root/'src/util/lia_sampling_math.h')==sha(ROOT/'include/math.hpp'),'private exact math copy changed'
 before={str(p.relative_to(ROOT)):sha(p) for p in [old/'install/lib/libz3.so',old/'install-sanitize/lib/libz3.so']}
 # Copy, never hardlink. Keep old mtimes for unchanged source and headers, so
 # the verified same-flag cache only rebuilds changed dependencies.
 if prior.exists() and not (root/build).exists():subprocess.run(['cp','-a','--reflink=auto',str(prior),str(root/build)],check=True)
 for p in (root/'src').rglob('*'):
  o=old/p.relative_to(root)
  if p.is_file() and o.is_file() and sha(p)==sha(o):st=o.stat();os.utime(p,ns=(st.st_atime_ns,st.st_mtime_ns))
 pyout=root/build/'python'
 if pyout.is_symlink() and not pyout.exists():pyout.unlink()
 env=dict(os.environ,LDFLAGS='-lgmpxx -lgmp')
 if a.sanitize:env.update(CXXFLAGS='-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer',CFLAGS='-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer',LDFLAGS='-fsanitize=address,undefined -lgmpxx -lgmp')
 subprocess.run(['python3','scripts/mk_make.py','--prefix='+str(install),'--pypkgdir='+str(install/'python'),'-b',build,'--python'],cwd=root,env=env,check=True)
 p=root/build/'config.mk';config=p.read_text();config=config.replace('SLINK_EXTRA_FLAGS=', 'SLINK_EXTRA_FLAGS=-lgmpxx -lgmp ');
 if a.sanitize:config=config.replace(' -O3',' -O1').replace('SLINK_EXTRA_FLAGS=', 'SLINK_EXTRA_FLAGS=-fsanitize=address,undefined ')
 p.write_text(config)
 subprocess.run(['make','-C',build,'-j'+str(a.jobs),'libz3.so'],cwd=root,env=env,check=True)
 subprocess.run(['make','-C',build,'-j'+str(a.jobs),'install'],cwd=root,env=env,check=True)
 assert all(sha(ROOT/p)==h for p,h in before.items()),'historical library changed'
 dest=ROOT/'results/arith_coverage_v1/correctness';dest.mkdir(parents=True,exist_ok=True)
 proof=dict(source_base=BASE,subtree='z3',mode=mode,cache_copy='independent copy; exact flags and content-bound unchanged source timestamps',patches={n:sha(ROOT/'dependencies/patches'/n) for n in PATCHES},modified_sources={f:sha(root/f) for f in FILES},library=str(install/'lib/libz3.so'),library_sha256=sha(install/'lib/libz3.so'),config=(root/build/'config.mk').read_text(),compiler=subprocess.check_output(['g++','--version'],text=True),historical_libraries=before,math_sha256=sha(ROOT/'include/math.hpp'),links=subprocess.check_output(['ldd',str(install/'lib/libz3.so')],text=True))
 (dest/(mode+'_dependency.json')).write_text(json.dumps(proof,indent=2)+'\n');print('Installed',install,flush=True)
if __name__=='__main__':main()
