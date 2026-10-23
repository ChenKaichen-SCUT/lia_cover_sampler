#!/usr/bin/env python3
"""Check raw/formal state separation and observation containment on completed output transactions."""
import argparse,json,sys,time,resource,shutil
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
from run_iteration import ROOT,digest,write
from audit_experiments import read_complete
sys.path.insert(0,str(ROOT/'python'))
from validation import original_formula,variables,verify
from coverage_differential_test import load_metric

def records(path,interrupted):
    lines,broken=read_complete(path)
    if broken and not interrupted:raise ValueError('Incomplete record without a recorded deadline interruption: '+str(path))
    # A killed write is evidence of an unfinished transaction, not an emitted model.
    # Complete malformed records must still fail JSON parsing.
    return [json.loads(s) for s in lines],broken

def one(payload):
    root,name,job=payload;root=Path(root);folder=root/name/job['run'];formula=ROOT/'benchmarks'/job['instance']
    start=time.monotonic();cpu=time.process_time()
    public_audit=json.loads((root/name/'audit'/job['instance']/f'seed-{job["seed"]}.json').read_text())
    assert not any(v for k,v in public_audit['counts'].items() if k not in ('valid','incomplete_lines'))
    interrupted=job['status']=='timeout_partial'
    emitted,broken=records(folder/'models.jsonl',interrupted)
    raw,raw_incomplete=records(folder/'raw_generator.jsonl',interrupted)
    transactions,transaction_incomplete=records(folder/'output_transactions.jsonl',interrupted)
    assert broken==public_audit['counts']['incomplete_lines']
    assert len(emitted)==public_audit['unique_valid_in_budget']
    if broken:assert len(raw)>len(transactions), 'Incomplete output cannot belong to a completed transaction'
    assert [r['raw_generator_index'] for r in transactions]==list(range(len(transactions)))
    assert len(transactions)<=len(raw)
    assert all(0<=r['raw_generator_index']<len(raw) for r in emitted)
    assert [r['raw_generator_index'] for r in emitted]==sorted(r['raw_generator_index'] for r in emitted)
    complete=max((r['raw_generator_index'] for r in transactions),default=-1)
    metric=load_metric();a=metric.WireCoverageStatistics();b=metric.WireCoverageStatistics()
    ae=metric.ManualSatisfiesMetric(formula.read_text(),a);be=metric.ManualSatisfiesMetric(formula.read_text(),b)
    old=original_formula(formula);decls=variables(old);formal_keys={json.dumps(r['assignment'],sort_keys=True) for r in emitted};raw_keys=set();j=0
    def sample(x):return [(v['name'],v['value'] if v['sort']=='Bool' else int(v['value'])) for v in x]
    for index,r in enumerate(raw):
        assert r['raw_generator_index']==index
        key=json.dumps(r['assignment'],sort_keys=True);assert key not in raw_keys;raw_keys.add(key)
        if key not in formal_keys:assert verify(old,r['assignment'],declarations=decls)=='valid'
        assert ae.count_sample(sample(r['assignment']))
        assert sum(a.node_count(*s) for s in a._storage.values())==r['raw_generator_covered']
        while j<len(emitted) and emitted[j]['raw_generator_index']<=index:
            assert be.count_sample(sample(emitted[j]['assignment']));j+=1
        if index<=complete:
            assert len(a._storage)==len(b._storage)
            for x,y in zip(a._storage.values(),b._storage.values()):
                assert not(int(x[0])&~int(y[0])) and not(int(x[1])&~int(y[1]))
            assert transactions[index]['emitted_samples']==j
            assert transactions[index]['emitted_covered']==sum(b.node_count(*s) for s in b._storage.values())
    assert j==len(emitted)
    last=transactions[-1] if transactions else {}
    cost_ok=last.get('extra_seconds',0)<=.06+.05*last.get('ab_main_seconds',0)+.01
    result=dict(configuration=name,instance=job['instance'],seed=job['seed'],raw_models=len(raw),emitted_models=len(emitted),
        raw_covered=raw[-1]['raw_generator_covered'] if raw else 0,emitted_covered=public_audit['covered'],total=public_audit['total'],
        completed_raw_transactions=complete+1,pending_raw_models=len(raw)-complete-1,
        incomplete_formal_lines=broken,incomplete_raw_lines=raw_incomplete,incomplete_transaction_lines=transaction_incomplete,
        all_raw_valid=True,processed_prefix_observations_preserved=True,
        completed_cost_within_allowance=cost_ok,cost=last,wall_seconds=time.monotonic()-start,cpu_seconds=time.process_time()-cpu,
        hashes={f:digest(folder/f) for f in ('models.jsonl','raw_generator.jsonl','output_transactions.jsonl')},
        checker_sha256=digest(__file__),formal_audit_sha256=digest(root/name/'audit'/job['instance']/f'seed-{job["seed"]}.json'))
    target=folder/'state_audit.json'
    if target.exists():shutil.copy2(target,target.with_name(target.name+'.previous-'+digest(target)[:12]))
    write(target,result);return result

def main():
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--jobs',type=int,default=12);a=p.parse_args()
    root=a.root.resolve();plan=json.loads((root/'plan.json').read_text());jobs=[]
    for name in plan['configs']:
        jobs.extend((str(root),name,json.loads(s)) for s in (root/name/'runs.jsonl').read_text().splitlines())
    start=time.monotonic();rows=[]
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:
        for future in as_completed([pool.submit(one,j) for j in jobs]):rows.append(future.result())
    write(root/'STATE_AUDIT_COMPLETE.json',dict(all_valid=True,all_processed_prefixes_preserved=True,rows=rows,
        wall_seconds=time.monotonic()-start,cpu_seconds=sum(r['cpu_seconds'] for r in rows)))
    print('raw/formal state audit passed',len(rows))
if __name__=='__main__':main()
