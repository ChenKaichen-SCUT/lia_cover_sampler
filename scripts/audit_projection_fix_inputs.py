#!/usr/bin/env python3
"""Supplement: exact original snapshot tokens, pair lifecycle and archive hashes. No solver."""
import argparse,json,hashlib,tarfile
from pathlib import Path
from collections import defaultdict
from audit_projection_fix import lines
from reevaluate_round6_saved_candidates import file_hash,dump,require,object_hash

def typed_tokens(row,template):
    require(len(row)==len(template),'snapshot width')
    result=[]
    for value,t in zip(row,template):
        if t['sort']=='Bool':require(value in ('true','false'),'Boolean literal');v=value=='true'
        else:v=str(int(value))
        result.append(dict(name=t['name'],sort=t['sort'],value=v))
    return result

def audit(base):
    plan=json.loads((base/'plan.json').read_text());archive=json.loads((base/'archive.json').read_text());runs=lines(base/'runs.jsonl');paired=defaultdict(list)
    require(file_hash(base/'raw.tar.gz')==archive['sha256'],'archive digest')
    with tarfile.open(base/'raw.tar.gz') as tar:
        members={m.name:hashlib.sha256(tar.extractfile(m).read()).hexdigest() for m in tar if m.isfile()}
    require(members==archive['files'],'archive complete member binding')
    for path,sha in members.items():require(file_hash(base/path)==sha,'disk/archive member identity')
    for r in runs:
        d=base/r['output'];initial=json.loads((d/'initial.json').read_text());template=initial['seed_model'];require(template==sorted(template,key=lambda x:(x['name'],x['sort']=='Bool')),'snapshot canonical declaration order')
        for key in ('formula','prefix_values','seed_values'):require(file_hash(r[key])==plan['hashes'][r[key]],'original input hash')
        prefix=[typed_tokens(line.split(),template) for line in Path(r['prefix_values']).read_text().splitlines()]
        seed=[typed_tokens(line.split(),template) for line in Path(r['seed_values']).read_text().splitlines()]
        require(prefix==initial['prefix'] and seed==[initial['seed_model']],'exact original prefix and seed')
        require(initial['seed']==r['seed'],'solver seed')
        paired[r['state'],r['seed']].append(dict(mode=r['mode'],wave=r['wave'],seconds=r['seconds'],input=object_hash(dict(prefix=prefix,seed=seed,coverage=initial['coverage'])),group_id=r['group_id']))
    for key,group in paired.items():
        expected={'plain_extra','projection_fixed'} if plan['stage']=='B' else {'plain_only','plain_extra','projection_fixed'}
        require({r['mode'] for r in group}==expected and len(group)==len(expected),'complete paired group')
        require(len({(r['wave'],r['group_id'],r['seconds'],r['input']) for r in group})==1,'same starting state and load wave')
    result=dict(all_passed=True,sessions=len(runs),pairs=len(paired),archive_members=len(members),search_calls=0,script_sha256=file_hash(__file__),plan_sha256=file_hash(base/'plan.json'),archive_sha256=archive['sha256'])
    dump(base/'INPUT_ARCHIVE_AUDIT.json',result);return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);print(json.dumps(audit(p.parse_args().input),indent=2))
