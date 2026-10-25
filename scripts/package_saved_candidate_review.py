#!/usr/bin/env python3
"""Losslessly package only new Round12 CSVs; verify every gzip round trip."""
import argparse,gzip,hashlib,json
from pathlib import Path
from reevaluate_round6_saved_candidates import dump,file_hash,require

TABLES=('candidates','queries','original_trajectory','simulated_trajectory',
        'pool_lifecycle','residual_changes')

def main():
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);a=p.parse_args();base=a.input.resolve()
    require((base/'ARTIFACT_AUDIT.json').exists(),'completed offline artifact audit required')
    compression=[]
    for rn in (10,11):
        for table in TABLES:
            src=base/f'round{rn}'/(table+'.csv');dst=Path(str(src)+'.gz')
            if src.exists():
                data=src.read_bytes();packed=gzip.compress(data,compresslevel=9,mtime=0)
                require(gzip.decompress(packed)==data,'gzip round trip')
                dst.write_bytes(packed);src.unlink()
            else:
                require(dst.exists(),'missing table');data=gzip.decompress(dst.read_bytes())
            compression.append(dict(path=str(dst.relative_to(base)),uncompressed_sha256=hashlib.sha256(data).hexdigest(),
                                    uncompressed_bytes=len(data),compressed_bytes=dst.stat().st_size))
    dump(base/'PACKAGING.json',dict(lossless=True,tables=compression))
    hashes={str(p.relative_to(base)):file_hash(p) for p in sorted(base.rglob('*'))
            if p.is_file() and p.name!='SHA256SUMS.json' and '__pycache__' not in p.parts}
    dump(base/'SHA256SUMS.json',hashes)
    print(json.dumps(dict(files=len(hashes),bytes=sum(p.stat().st_size for p in base.rglob('*') if p.is_file()))))
if __name__=='__main__':main()
