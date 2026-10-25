#!/usr/bin/env python3
import argparse,json,hashlib,tarfile,subprocess
from pathlib import Path
from run_bofill_900s import sha,dump
ROOT=Path(__file__).resolve().parents[1]
def check(out):
    out=Path(out).resolve();manifest=json.loads((out/'archive_manifest.json').read_text());members=0
    for entry in manifest['runs']:
        assert len(entry['parts'])==1
        part=entry['parts'][0];p=out/part['file'];assert sha(p)==part['sha256']
        with tarfile.open(p,'r:gz') as tar:
            for relative,expected in entry['content_sha256'].items():
                assert hashlib.sha256(tar.extractfile(relative).read()).hexdigest()==expected
                assert sha(out/relative)==expected;members+=1
    plan=json.loads((out/'plan.json').read_text());assert sha(out/'plan.json')==(out/'plan.sha256').read_text().strip()
    for run in plan['runs']:
        d=out/run['directory'];spec=json.loads((d/'spec.json').read_text());assert spec==run
        audit=json.loads((d/'audit.json').read_text());summary=json.loads((d/'raw/summary.json').read_text());assert audit['accepted']==(summary['terminal']=='SAT')
        for q in map(json.loads,(d/'raw/queries.jsonl').read_text().splitlines()):
            e=q['evidence'];assert e['granted_timeout_ms']<=e['requested_timeout_ms'];assert e['granted_timeout_ms']<=max(0,int((run['seconds']-q['start'])*1000))
            if e['raw_status']=='unknown':assert summary['terminal']!='UNSAT'
    changed=subprocess.check_output(['git','diff','--name-only','-z','a31e91e','--','results/bofill_900s'],cwd=ROOT)
    assert not changed,'old 900s artifacts changed'
    dump(out/'integrity.json',dict(plan_hash_valid=True,archives_valid=True,archive_members=members,sessions=len(plan['runs']),old_results_unchanged=True))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('out');a=p.parse_args();check(a.out)
