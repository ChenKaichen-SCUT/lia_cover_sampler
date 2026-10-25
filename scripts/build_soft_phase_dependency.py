#!/usr/bin/env python3
"""Build an isolated patched Z3; never edit or install into the frozen tree."""
import argparse,hashlib,json,os,subprocess,tarfile,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
COMMIT='f2c6e1756b13281d96cbf90a8bd940129e56cce4'
def run(cmd,cwd):subprocess.run(cmd,cwd=cwd,check=True)
def main():
    a=argparse.ArgumentParser();a.add_argument('--upstream',default='/tmp/lia-highdiv-frozen');a.add_argument('--destination',required=True);a.add_argument('--jobs',type=int,default=12);v=a.parse_args()
    source=Path(v.upstream).resolve();dest=Path(v.destination).resolve();assert not dest.exists(),'must use a new independent directory';assert not dest.is_relative_to(source),'frozen tree is read-only'
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()==COMMIT
    dest.mkdir(parents=True)
    with tempfile.TemporaryFile() as f:
        subprocess.run(['git','archive',COMMIT+':z3'],cwd=source,stdout=f,check=True);f.seek(0)
        with tarfile.open(fileobj=f,mode='r:') as t:t.extractall(dest,filter='data')
    patch=ROOT/'dependencies/patches/z3_soft_phase.patch';run(['patch','-p1','-i',str(patch)],dest)
    install=dest/'install'
    run(['python3','scripts/mk_make.py','--prefix='+str(install),'--pypkgdir='+str(install/'python'),'-b','build','--python'],dest)
    run(['make','-C','build','-j'+str(v.jobs),'libz3.so'],dest)
    run(['make','-C','build','-j'+str(v.jobs),'install'],dest)
    print(str(install))
if __name__=='__main__':main()
