"""Archive audited round-four raw evidence once, including failed-run diagnostics."""
from pathlib import Path
import hashlib,json,tarfile,time,resource

ROOT=Path(__file__).resolve().parents[1]
output=ROOT/'results/round4_evidence'
state=json.loads((ROOT/'results/round4_dev/workflow.json').read_text())
assert state['status'] in ('screen_not_promoted','confirm_not_promoted','final_audited')
review=json.loads((ROOT/'results/round4_dev/builds/publication_verification.json').read_text())
assert review['sampling_source_and_binaries_unchanged'] and review['promotion_rules_unchanged']
assert all(b['content_bindings_verified'] for b in review['batches'])
output.mkdir(exist_ok=False)
folders=sorted(p for base in (ROOT/'results').glob('round4_*') if base.is_dir()
               for p in base.rglob('raw') if p.is_dir())
files=sorted(p for folder in folders for p in folder.rglob('*') if p.is_file())
assert len(files)==len(set(files))
assert all(not p.is_symlink() for p in files)
def digest(stream):
    h=hashlib.sha256()
    for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
start=time.monotonic();cpu=time.process_time();members={}
archive=output/'raw.tar.gz'
with tarfile.open(archive,'x:gz',compresslevel=6) as tar:
    for path in files:
        name=str(path.relative_to(ROOT))
        with path.open('rb') as f:members[name]=dict(bytes=path.stat().st_size,sha256=digest(f))
        tar.add(path,arcname=name,recursive=False)
with tarfile.open(archive,'r:gz') as tar:
    seen=set()
    for entry in tar:
        assert entry.isfile() and entry.name in members and entry.name not in seen
        assert entry.size==members[entry.name]['bytes']
        with tar.extractfile(entry) as f:assert digest(f)==members[entry.name]['sha256']
        seen.add(entry.name)
    assert seen==set(members)
with archive.open('rb') as f:archive_sha=digest(f)
manifest=dict(scope='Round-four audited development/screen conclusion; no 300s or 900s result implied',
    archive='raw.tar.gz',archive_sha256=archive_sha,archive_bytes=archive.stat().st_size,
    uncompressed_bytes=sum(m['bytes'] for m in members.values()),files=len(members),members=members,
    wall_seconds=time.monotonic()-start,cpu_seconds=time.process_time()-cpu,
    verification='Every compressed member re-read and checked against its source SHA-256',
    restore='From repository root: tar -xzf results/round4_evidence/raw.tar.gz')
(output/'archive.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({k:v for k,v in manifest.items() if k!='members'},indent=2))
