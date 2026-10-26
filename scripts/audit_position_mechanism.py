#!/usr/bin/env python3
"""Independent all-prefix replay and frozen-manual witness re-evaluation."""
import argparse,csv,json,time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,require,dump,file_hash
from prepare_highdiv_complementarity_gap import layout_for,pack_state
from highdiv_gap_common import load_gz,obs_id
from diagnose_position_mechanism import SOURCE,OUT,check_source,records,phase

def one(args):
    out,job=args;out=Path(out);dest=out/'runs'/job['run'];rows=load_gz(dest/'queries.jsonl.gz');saved=iter(records(SOURCE/'audit'/job['run']/'models.jsonl.gz'))
    with (dest/'positive_events.csv').open() as f:positive=list(csv.DictReader(f))
    selected={1}
    if positive:
        selected.add(int(max(positive,key=lambda r:int(r['gain']))['index']))
        selected.add(int(max(positive,key=lambda r:float(r['preceding_no_gain_seconds']))['index']))
    z=o=0;models=0;checks=[];prior_end=0.;cycle_total=0.
    with no_search() as attempts:
        metric=FrozenCoverage((out/'inputs'/job['formula']).read_text());layout=layout_for(metric)
        for i,r in enumerate(rows):
            require(r['query']==i and r['base_state_id']==obs_id((z,o)),'query-before full state')
            require(r['covered_before']==(z&o).bit_count(),'query-before coverage')
            require(r['phase']==phase(r['cycle_start']) and r['cycle_start']>=prior_end,'phase/chronology');prior_end=r['cycle_end'];cycle_total+=r['full_cycle_seconds']
            require(r['full_cycle_seconds']>=r['check_seconds'] and r['full_cycle_seconds']>=0,'full costs')
            if not r['delivered']:require(r['gain']==r['first_observations']==0,'failure has fictitious gain');continue
            m=next(saved);models+=1;require(m['query']==i and m['model_id']==r['model_id'] and r['delivery_time']<=900,'candidate association/deadline')
            mz,mo=int(m['zero'],16),int(m['one'],16);nz,no=z|mz,o|mo
            gain=(nz&no).bit_count()-(z&o).bit_count();first=((mz|mo)&~(z|o)).bit_count()
            require(gain==r['gain'] and first==r['first_observations'],'new observations vs completion')
            if models in selected:
                single=metric.apply(metric.empty,m['model']);require(pack_state(metric,layout,single)==(mz,mo),'frozen ManualSatisfiesMetric observation mismatch')
                checks.append(dict(index=models,query=i,model_id=m['model_id'],passed=True))
            z,o=nz,no
        require(next(saved,None) is None,'missed saved model')
        old=json.loads((SOURCE/'audit'/job['run']/'summary.json').read_text());require((z&o).bit_count()==old['covered'] and models==old['delivered'],'final audit differs')
        require(sum(r['gain'] for r in rows)==old['covered'],'telescoping')
        for i,r in enumerate(rows):
            for k in [1,5]:
                complete=i+k<len(rows);require(r[f'next{k}_complete']==complete,'future window censoring')
                require(r[f'next{k}_gain']==(sum(v['gain'] for v in rows[i+1:i+1+k]) if complete else None),'future gain window')
        require(not attempts,'audit search')
    result=dict(run=job['run'],passed=True,queries=len(rows),models=models,covered=(z&o).bit_count(),manual_witnesses=checks,search_calls=0)
    dump(out/'independent_audit'/(job['run']+'.json'),result)
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=OUT);p.add_argument('--jobs',type=int,default=15);a=p.parse_args();out=a.out.resolve();require(1<=a.jobs<=15,'workers');started=time.perf_counter();check_source(out)
    plan=json.loads((out/'plan.json').read_text())
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:results=list(pool.map(one,[(str(out),j) for j in plan['jobs']]))
    check_source(out);dump(out/'independent_audit.json',dict(passed=True,workers=a.jobs,seconds=time.perf_counter()-started,runs=results,search_calls=0,source_unchanged=True))
    print(json.dumps(dict(passed=True,runs=len(results),models=sum(r['models'] for r in results),manual_witnesses=sum(len(r['manual_witnesses']) for r in results))))

if __name__=='__main__':main()
