#!/usr/bin/env python3
"""Lossless run archives with <=50MiB Git-friendly pieces; originals stay intact."""
import argparse, hashlib, json, shutil, tarfile, tempfile
from pathlib import Path
CHUNK=50*1024*1024
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()
def restore(out):
    out=Path(out);manifest=json.loads((out/'archive_manifest.json').read_text())
    for entry in manifest['runs']:
        if (out/'runs'/entry['run']).exists():
            for relative,expected in entry['content_sha256'].items():assert sha(out/relative)==expected,'existing raw content differs from archive'
            continue
        with tempfile.TemporaryFile() as merged:
            for part in entry['parts']:
                p=out/part['file'];assert sha(p)==part['sha256'],'archive hash mismatch'
                with p.open('rb') as f:shutil.copyfileobj(f,merged)
            merged.seek(0)
            with tarfile.open(fileobj=merged,mode='r:gz') as archive:
                for member in archive.getmembers():
                    assert member.name=='runs/'+entry['run'] or member.name.startswith('runs/'+entry['run']+'/')
                    assert (out/member.name).resolve().is_relative_to(out.resolve())
                    assert not member.issym() and not member.islnk()
                archive.extractall(out,filter='data')
            for relative,expected in entry['content_sha256'].items():assert sha(out/relative)==expected,'restored content mismatch'
def pack(out):
    out=Path(out);dest=out/'archives';dest.mkdir(exist_ok=True);manifest=[]
    for run in sorted((out/'runs').iterdir()):
        if not run.is_dir():continue
        target=dest/(run.name+'.tar.gz');assert not target.exists(),'new archive required'
        with tarfile.open(target,'w:gz',compresslevel=6) as archive:archive.add(run,arcname='runs/'+run.name)
        paths=[target]
        if target.stat().st_size>CHUNK:
            paths=[]
            with target.open('rb') as f:
                while data:=f.read(CHUNK):
                    p=target.with_name(target.name+f'.part{len(paths):03d}');p.write_bytes(data);paths.append(p)
            target.unlink() # only the just-built redundant intermediate; original logs untouched
        manifest.append(dict(run=run.name,parts=[dict(file=str(p.relative_to(out)),sha256=sha(p),bytes=p.stat().st_size) for p in paths],content_sha256={str(p.relative_to(out)):sha(p) for p in sorted(run.rglob('*')) if p.is_file()}))
    (out/'archive_manifest.json').write_text(json.dumps(dict(runs=manifest,method='lossless gzip tar; originals preserved; 50MiB chunks'),indent=2)+'\n')
def main():
    a=argparse.ArgumentParser();a.add_argument('out');a.add_argument('--restore',action='store_true');v=a.parse_args();(restore if v.restore else pack)(Path(v.out).resolve())
if __name__=='__main__':main()
