#!/usr/bin/env python3
"""Re-read persisted Round12 artifacts; check causality and source-union value.

Uses literal assignments and frozen Manual only. Source unions are descriptive
unbounded sets, explicitly not an equal-count candidate pool or online run.
"""
import argparse,csv,gzip,json,tarfile,time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from review_saved_candidates import ROOT,ORDINARY,FrozenCoverage,no_search,pack,union,covered,marginal,state_hash
from reevaluate_round6_saved_candidates import model_id,require,dump,write_csv,file_hash


def read(path):
    path=Path(path)
    if not path.exists():path=Path(str(path)+'.gz')
    opener=gzip.open if path.suffix=='.gz' else open
    with opener(path,'rt') as f:return list(csv.DictReader(f))


def audit(args):
    base,rn,ar,cs,os,ss=args; out=Path(base); sid=ar['session']; label=sid.replace('/','__')
    folder=out/f'round{rn}';start=time.perf_counter()
    with no_search() as attempts:
        catalog={}
        with gzip.open(folder/'sessions'/label/'model_catalog.jsonl.gz','rt') as f:
            for line in f:
                m=json.loads(line);require(model_id(m['model'])==m['model_hash'],'catalog identity');catalog[m['model_hash']]=m['model']
        archive=ROOT/f'results/round{rn}_projection/main_10s/raw.tar.gz'
        with tarfile.open(archive) as tar:
            initial=json.loads(tar.extractfile(f'raw/{sid}/initial.json').read())
        formula=out/'inputs/benchmarks'/ar['instance']
        metric=FrozenCoverage(formula.read_text());initial_state=metric.empty.copy()
        ids=set()
        for m in initial['prefix']+[initial['seed_model']]:
            if model_id(m) not in ids:
                initial_state=metric.apply(initial_state,m);ids.add(model_id(m))
        require(metric.export(initial_state)==initial['coverage'],'artifact initial state')
        masks={k:pack(metric,metric.apply(metric.empty.copy(),m)) for k,m in catalog.items()}
        init=pack(metric,initial_state);baseline=covered(init)
        original={int(x['position']):x for x in os}
        candidates={x['candidate_id']:x for x in cs}
        sorted_cs=sorted(cs,key=lambda c:(float(c['completion']),int(c['candidate_line'])))
        real=init
        for n,o in sorted(original.items()):
            c=candidates[o['candidate_id']]
            require(c['model_hash']==o['model_hash'] and c['origin']==o['origin'],'formal provenance')
            require(c['timely']=='True' and float(c['completion'])<=float(o['historical_seconds']),'formal availability')
            require(marginal(real,masks[o['model_hash']])==(int(o['gain']),int(o['observations'])),'artifact formal score')
            real=union(real,masks[o['model_hash']])
            require(state_hash(real)==o['state_sha256'],'artifact formal full-state hash')
        variant_groups=defaultdict(list)
        for row in ss:variant_groups[row['variant']].append(row)
        audited_slots=0
        for variant,rows in variant_groups.items():
            cap=int(variant.rsplit('_',1)[1]); policy=variant.rsplit('_',1)[0]
            pool=[];seen=set(ids);state=init;cursor=0;outputs=0
            for row in sorted(rows,key=lambda x:int(x['position'])):
                original_row=original[int(row['position'])];now=float(original_row['historical_seconds'])
                while cursor<len(sorted_cs) and float(sorted_cs[cursor]['completion'])<=now:
                    c=sorted_cs[cursor];cursor+=1
                    if c['timely']!='True' or (policy!='all' and c['origin'] not in ORDINARY):continue
                    if c['model_hash'] in seen or any(v['model_hash']==c['model_hash'] for v in pool):continue
                    pool.append(c)
                    if len(pool)>cap:pool.pop(0)
                choices=list(pool);orig=candidates[original_row['candidate_id']]
                if (orig['model_hash'] not in seen and (policy!='ordinary_strict' or orig['origin'] in ORDINARY)
                    and not any(c['model_hash']==orig['model_hash'] for c in choices)):
                    choices.append(orig)
                def rank(c):
                    gain,obs=marginal(state,masks[c['model_hash']])
                    return (-gain,-obs,-int(c['model_hash']==orig['model_hash']),float(c['completion']),int(c['candidate_line']),c['model_hash'])
                best=min(choices,key=rank) if choices else None
                require(row['candidate_id']==(best['candidate_id'] if best else ''),'independent FIFO/tie/source selection')
                if best:
                    key=best['model_hash']; require(key not in seen,'artifact output dedup')
                    gain,obs=marginal(state,masks[key]);outputs+=1
                    require((gain,obs)==(int(row['gain']),int(row['observations'])),'artifact marginal')
                    state=union(state,masks[key]);seen.add(key);pool=[c for c in pool if c['model_hash']!=key]
                require(covered(state)-baseline==int(row['cumulative']),'artifact cumulative')
                require(state_hash(state)==row['state_sha256'],'artifact simulated full-state hash')
                require(outputs==int(row['outputs']) and len(pool)<=cap,'artifact count/capacity')
                audited_slots+=1
        ordinary,projection,all_sources=init,init,init
        source_keys=defaultdict(set)
        for c in sorted_cs:
            if c['timely']!='True':continue
            key=c['model_hash'];mask=masks[key]
            source_keys[c['origin']].add(key)
            all_sources=union(all_sources,mask)
            if c['origin'] in ORDINARY:ordinary=union(ordinary,mask)
            else:projection=union(projection,mask)
        source_result={k:ar[k] for k in ('round','session','instance','seed','mode','snapshot')}
        source_result.update(initial_covered=baseline,total=metric.total,
            ordinary_distinct=len(source_keys['plain']|source_keys['extra_plain']),
            projection_distinct=len(source_keys['projection']),
            ordinary_unbounded_union_gain=covered(ordinary)-baseline,
            projection_unbounded_union_gain=covered(projection)-baseline,
            all_unbounded_union_gain=covered(all_sources)-baseline,
            projection_after_all_ordinary=covered(all_sources)-covered(ordinary),
            ordinary_after_all_projection=covered(all_sources)-covered(projection),
            observations_differential_models=len(masks),audited_original_slots=len(os),
            audited_simulated_slots=audited_slots,search_calls=len(attempts),
            independent_audit_seconds=time.perf_counter()-start)
        require(not attempts,'no search')
        return source_result


def main():
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('--jobs',type=int,default=6);a=p.parse_args()
    require(1<=a.jobs<=6,'worker bound');base=a.input.resolve();audits=read(base/'session_audit.csv');jobs=[]
    for rn in (10,11):
        by=[defaultdict(list) for _ in range(3)]
        for d,table in zip(by,('candidates','original_trajectory','simulated_trajectory')):
            for row in read(base/f'round{rn}'/(table+'.csv')):d[row['session']].append(row)
        for ar in audits:
            if int(ar['round'])==rn:jobs.append((str(base),rn,ar,*[d[ar['session']] for d in by]))
    with no_search(),ProcessPoolExecutor(max_workers=a.jobs) as pool:rows=list(pool.map(audit,jobs))
    write_csv(base/'source_union.csv',rows)
    dump(base/'ARTIFACT_AUDIT.json',dict(sessions=len(rows),all_passed=True,search_calls=0,
        original_slots=sum(r['audited_original_slots'] for r in rows),simulated_slots=sum(r['audited_simulated_slots'] for r in rows),
        script_sha256=file_hash(__file__),no_future_leakage=True,independent_list_fifo_replay=True,
        interpretation='all-source union is unbounded; not an equal-count or online performance result'))
    print(json.dumps(dict(sessions=len(rows),all_passed=True,search_calls=0)))
if __name__=='__main__':main()
