#!/usr/bin/env python3
"""Bind historical read-only inputs and archive derived case detail."""
import json,tarfile,csv
from pathlib import Path
from analyze_early_coverage import ROOT,BATCHES
from reevaluate_round6_saved_candidates import file_hash,dump,require
out=ROOT/'results/round8_analysis';bindings=[]
for mode,batch in BATCHES.items():
    folder=ROOT/'results'/batch;archive=json.loads((folder/'archive.json').read_text());require(file_hash(folder/archive['archive'])==archive['sha256'],'historical archive hash mismatch')
    for path,expected in archive['files'].items():require(file_hash(folder/path)==expected,'historical raw hash mismatch: '+path)
    bindings.append(dict(mode=mode,archive=str(folder/archive['archive']),sha256=archive['sha256'],raw_files_verified=len(archive['files'])))
with tarfile.open(out/'case_evidence.tar.gz','w:gz') as tar:tar.add(out/'cases',arcname='cases')
dump(out/'case_evidence_archive.json',dict(sha256=file_hash(out/'case_evidence.tar.gz'),files={str(p.relative_to(out)):file_hash(p) for p in (out/'cases').rglob('*') if p.is_file()}))
dump(out/'source_integrity.json',dict(base='13eea92',historical_archives=bindings,validated_distinct_timely_models=sum(int(r['count']) for r in csv.DictReader((out/'runs.csv').open())),manual_metric_sha256='a51cdd069321cd32becf8eeb5e606cb71fbab16ae3e0231f68963eaadf493f04',no_search=True,
    note='Two parser setup failures (Z3 type API / HighDiv delimiter whitespace) preceded successful replay; no models generated or historical evidence modified.'))
