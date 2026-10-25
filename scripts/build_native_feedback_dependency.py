#!/usr/bin/env python3
"""Independent native-feedback library; optional exact-flag object-cache reuse.
The old source, objects and installed libraries are never modified or hardlinked.
"""
import argparse,hashlib,json,os,subprocess,tarfile,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
COMMIT='f2c6e1756b13281d96cbf90a8bd940129e56cce4'
def run(cmd,cwd,env=None):subprocess.run(cmd,cwd=cwd,env=env,check=True)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser();p.add_argument('--jobs',type=int,default=12);p.add_argument('--sanitize',action='store_true');a=p.parse_args()
    root=ROOT/'dependencies/z3-native-feedback-phase'
    if not root.exists():
        root.mkdir()
        with tempfile.TemporaryFile() as f:
            subprocess.run(['git','archive',COMMIT+':z3'],cwd='/tmp/lia-highdiv-frozen',stdout=f,check=True);f.seek(0)
            with tarfile.open(fileobj=f) as t:t.extractall(root,filter='data')
        for name in ('z3_soft_phase.patch','z3_native_feedback_phase.patch'):run(['patch','-p1','-i',str(ROOT/'dependencies/patches'/name)],root)
    mode='sanitize' if a.sanitize else 'release';build='build_sanitize' if a.sanitize else 'build';install=root/('install-sanitize' if a.sanitize else 'install')
    assert not install.exists(),'do not overwrite an already installed research library'
    prior=ROOT/'dependencies/z3-soft-phase'/build
    if prior.exists() and not (root/build).exists():run(['cp','-a','--reflink=auto',str(prior),str(root/build)],ROOT)
    # A historical binary-only cache may have a dangling Python-output link.
    # Remove only that link in this independent copy before mk_make creates a directory.
    pyout=root/build/'python'
    if pyout.is_symlink() and not pyout.is_dir():pyout.unlink()
    env=dict(os.environ)
    if a.sanitize:
        env.update(CXXFLAGS='-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer',CFLAGS='-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer',LDFLAGS='-fsanitize=address,undefined')
    run(['python3','scripts/mk_make.py','--prefix='+str(install),'--pypkgdir='+str(install/'python'),'-b',build,'--python'],root,env)
    # mk_make's release defaults include -O3; replace only for sanitizer mode.
    if a.sanitize:
        config=root/build/'config.mk';text=config.read_text().replace(' -O3',' -O1')
        config.write_text(text)
    before={str(p):sha(p) for p in (ROOT/'dependencies/z3-soft-phase/install/lib/libz3.so',ROOT/'dependencies/z3-soft-phase/install-sanitize/lib/libz3.so')}
    run(['make','-C',build,'-j'+str(a.jobs),'libz3.so'],root,env)
    run(['make','-C',build,'-j'+str(a.jobs),'install'],root,env)
    assert all(sha(Path(p))==h for p,h in before.items()),'historical library changed'
    evidence=ROOT/'results/native_feedback_validation';evidence.mkdir(parents=True,exist_ok=True)
    (evidence/(mode+'_dependency.json')).write_text(json.dumps(dict(upstream=COMMIT,mode=mode,cache_source=str(prior),cache_copy='independent copy, never hardlink; make rebuilds all changed-header dependents',config=(root/build/'config.mk').read_text(),library_sha256=sha(install/'lib/libz3.so'),historical_library_sha256=before,patches={n:sha(ROOT/'dependencies/patches'/n) for n in ('z3_soft_phase.patch','z3_native_feedback_phase.patch')}),indent=2)+'\n')
    print('Independent library installed:',install,flush=True)
if __name__=='__main__':main()
