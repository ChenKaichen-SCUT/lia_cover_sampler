#!/usr/bin/env python3
"""Round-six fixed resident sessions; no end-to-end experiment or budget sweep."""
import argparse
from concurrent.futures import ThreadPoolExecutor,ProcessPoolExecutor,as_completed
from datetime import datetime,timezone
import hashlib,json,os,signal,subprocess,threading,time,traceback
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ENV={k:'1' for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','VECLIB_MAXIMUM_THREADS','BLIS_NUM_THREADS')}
BASE='5ed367ce6ed418b31dbf1547fb0af6022fb71618'
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,d):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def utc():return datetime.now(timezone.utc).isoformat()
def bindings(s):
    p=Path(s['folder']);d={str(p/name):s[key] for name,key in [('prefix.values','prefix_sha256'),('seed.values','seed_sha256'),('coverage.json','coverage_sha256'),('assignments.json','assignments_sha256')]}
    d[s['formula']]=s['input_sha256']
    assert all(digest(p)==h for p,h in d.items()),'frozen snapshot changed'
    return d
def session(pair,mode,order,binary,out,source):
    s=pair['snapshot'];folder=Path(s['folder'])
    p=out/'raw'/s['id']/f'seed-{pair["seed"]}'/f'repeat-{pair["repeat"]}'/(mode+'.json');p.parent.mkdir(parents=True,exist_ok=True)
    command=[str(binary),s['formula'],str(folder/'prefix.values'),str(folder/'seed.values'),mode,'0.06',str(pair['seed']),str(p)]
    r=dict(snapshot=s['id'],instance=s['instance'],prefix=s['prefix'],seed=pair['seed'],repeat=pair['repeat'],mode=mode,
        pair_index=pair['index'],order=order,command=command,output=str(p.relative_to(out)),success=False,started_utc=utc())
    start=time.monotonic();env=os.environ.copy();env.update(ENV);expired=threading.Event()
    try:
        with p.with_suffix('.stdout.log').open('wb') as stdout,p.with_suffix('.stderr.log').open('wb') as stderr:
            child=subprocess.Popen(command,stdout=stdout,stderr=stderr,env=env,start_new_session=True,stdin=subprocess.DEVNULL)
            def timeout():
                try:os.killpg(child.pid,signal.SIGKILL);expired.set()
                except ProcessLookupError:pass
            timer=threading.Timer(60,timeout);timer.start()
            try:_,status,usage=os.wait4(child.pid,0);child.returncode=os.waitstatus_to_exitcode(status)
            finally:timer.cancel()
        r.update(process_wall_seconds=time.monotonic()-start,cpu_seconds=usage.ru_utime+usage.ru_stime,exitcode=child.returncode,watchdog_timeout=expired.is_set())
        if child.returncode:raise RuntimeError('probe exit '+str(child.returncode))
        assert p.read_bytes().endswith(b'\n')
        d=json.loads(p.read_text());assert d['source_sha256']==source and d['mode']==mode and d['rng_seed']==pair['seed']
        assert len(d['actions'])==3
        r['summary']={k:v for k,v in d.items() if k not in ('snapshot','input_model','actions')}
        r['actions']=[dict(action=a['action'],target_seed=a['target_seed'],budget_seconds=a['budget_seconds'],
            result={k:v for k,v in a['result'].items() if k!='candidate'},statistics=a['statistics'],checkpoint_writes=a['checkpoint_writes']) for a in d['actions']]
        r.update(success=True,probe_sha256=digest(p),checkpoints_sha256=digest(str(p)+'.checkpoints.jsonl'))
    except Exception as e:r.update(error=str(e),traceback=traceback.format_exc(),process_wall_seconds=time.monotonic()-start)
    r['finished_utc']=utc();write(p.with_suffix('.run.json'),r);return r
def pair_run(args):
    pair,binary,out,source=args
    return [session(pair,m,i,binary,out,source) for i,m in enumerate(pair['order'])]
def key(m):return tuple(sorted((v['name'],v['sort'],str(v['value'])) for v in m))

def audit(args):
    s,rows,out=args;out=Path(out);started=time.monotonic();cpu=time.process_time()
    from snapshot_replay import oracle
    from validation import verify,nodes_preorder
    import z3
    bindings(s);root,decls,stat,ev,storage,expected,sample=oracle(s)
    ordered_nodes=nodes_preorder(z3.And(ev._solver.assertions()))
    saved=json.loads((Path(s['folder'])/'assignments.json').read_text());prefix={key(m) for m in saved['prefix']};seed_key=key(saved['seed'])
    def score(m):
        assert verify(root,m,declarations=decls)=='valid','original formula invalid'
        stat._storage=storage.copy();assert ev.count_sample(sample(m))
        gain=sum(stat.node_count(*v) for v in stat._storage.values())-expected['covered']
        observed=sum((int(a[0])&~int(b[0])).bit_count()+(int(a[1])&~int(b[1])).bit_count() for a,b in zip(stat._storage.values(),storage.values()))
        return gain,observed,[[int(stat._storage[n.get_id()][1]),int(stat._storage[n.get_id()][0])] for n in ordered_nodes]
    results=[]
    for r in rows:
        a=dict(output=r['output'],snapshot=s['id'],mode=r['mode'],seed=r['seed'],repeat=r['repeat'],passed=False)
        try:
            assert r['success'],r.get('error','failed session')
            p=out/r['output'];assert digest(p)==r['probe_sha256'] and digest(str(p)+'.checkpoints.jsonl')==r['checkpoints_sha256']
            d=json.loads(p.read_text());assert d['snapshot']==expected and key(d['input_model'])==seed_key
            assert d['input_gain']==score(d['input_model'])[0]
            assert d['session_seconds']+1e-9>=d['initialization_seconds']+d['sum_action_seconds']
            assert abs(d['initialization_seconds']-sum(d[k] for k in ('formula_initialization_seconds','snapshot_restore_seconds','engine_initialization_seconds')))<1e-8
            raw=Path(str(p)+'.checkpoints.jsonl').read_bytes();assert not raw or raw.endswith(b'\n')
            checkpoints=[json.loads(line) for line in raw.splitlines()]
            checked=[]
            for item in d['actions']:
                index=item['action'];result=item['result'];budget=item['budget_seconds'];assert budget==.06
                assert item['target_seed']==3*r['seed']+index and 0<=index<3
                records=[c for c in checkpoints if c['action']==index];writes=item['checkpoint_writes'];assert len(records)==len(writes)
                timely=[]
                for i,(c,w) in enumerate(zip(records,writes)):
                    assert c['index']==w['index']==i and w['timely']==(w['finished_seconds']<=budget)
                    assert c['validated_seconds']<=w['finished_seconds']+1e-9
                    assert key(c['candidate'])!=seed_key and key(c['candidate']) not in prefix
                    gain,observed,state=score(c['candidate']);assert gain==c['gain']
                    primary=result['primary'][0];n,bit,value=primary
                    assert n>=0 and state[n][int(value)]&(1<<bit),'primary Reach/bit not observed'
                    if w['timely']:timely.append((c,w,gain,observed))
                assert len(result['queries'])<=6 and result['expansions']<=3
                assert result['initial_block']<=result['variables'] and result['final_block']<=result['variables']
                for q in result['queries']:
                    assert len(q['targets'])<=3
                    if q['global_proof']:assert q['status']=='unsat' and not q['fixed_core'] and not q['exclusion_core']
                    if q['status'] in ('unknown','timeout','resource_exhausted','budget_exhausted'):assert not q['global_proof']
                if result['candidate'] is not None:
                    assert timely and key(result['candidate'])==key(timely[-1][0]['candidate'])
                    gain,observed,state=score(result['candidate']);assert gain==result['gain'] and observed==result['observations']
                    assert result['checkpoint_seconds']<=budget and result['checkpoint_seconds']>=timely[-1][1]['finished_seconds']-1e-9
                    assert key(result['candidate']) not in prefix and key(result['candidate'])!=seed_key
                else:assert result['gain']==0
                if result['first_positive_seconds'] is not None:assert 0<=result['first_positive_seconds']<=budget and result['gain']>0
                checked.append(dict(action=index,gain=result['gain'],delivered=result['candidate'] is not None,checkpoint_records=len(records),timely_checkpoints=len(timely)))
            assert len(checkpoints)==sum(x['checkpoint_records'] for x in checked)
            a.update(passed=True,actions=checked,probe_sha256=digest(p),checkpoints_sha256=digest(str(p)+'.checkpoints.jsonl'))
        except Exception as e:a.update(error=str(e),traceback=traceback.format_exc())
        results.append(a)
    return dict(snapshot=s['id'],sessions=results,all_passed=all(r['passed'] for r in results),wall_seconds=time.monotonic()-started,cpu_seconds=time.process_time()-cpu)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--jobs',type=int,default=15)
    args=p.parse_args();assert 1<=args.jobs<=15
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False);binary=args.binary.resolve()
    manifest=ROOT/'results/round3_snapshots/states/manifest.json';snapshots=[s for s in json.loads(manifest.read_text())['snapshots'] if s['prefix'] in (1,10)]
    assert len(snapshots)==6 and all(s['split']=='development' for s in snapshots)
    sources={str(manifest):digest(manifest),str(binary):digest(binary)}
    for s in snapshots:sources.update(bindings(s))
    cpp=sorted(list((ROOT/'src').glob('*.cpp'))+list((ROOT/'include').glob('*.hpp')))
    source=hashlib.sha256(''.join(digest(f) for f in cpp).encode()).hexdigest()
    support=[ROOT/'CMakeLists.txt',Path(__file__),ROOT/'scripts/report_coverage_block_pairs.py',ROOT/'docs/第六轮机制实验协议.md',ROOT/'scripts/snapshot_replay.py',ROOT/'python/validation.py',ROOT/'python/coverage_differential_test.py']
    metric=Path(os.environ.get('HIGHDIV_ROOT',ROOT.parent/'HighDiv-master'))/'HighDiv/my_scripts/calc_metric.py'
    assert digest(metric)==json.loads((ROOT/'dependencies/source_audit.json').read_text())['HighDiv/my_scripts/calc_metric.py']['sha256']
    sources.update({str(f):digest(f) for f in cpp+support+[metric]})
    pairs=[]
    for i,s in enumerate(snapshots):
        for seed in (0,1,2):
            for repeat in (0,1):pairs.append(dict(index=len(pairs),snapshot=s,seed=seed,repeat=repeat,order=['global','block'] if (i+seed+repeat)%2==0 else ['block','global']))
    plan=dict(created_utc=utc(),frozen_base=BASE,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),source_sha256=source,
        binary=str(binary),binary_sha256=digest(binary),source_hashes=sources,snapshots=snapshots,seeds=[0,1,2],repeats=2,actions_per_session=3,budget_seconds=.06,
        queries_per_action=6,max_pack=3,workers=args.jobs,environment=ENV,pairs=pairs,sessions=72,actions=216,
        lifecycle='one formula, restored prefix and Global engine per three-action session; no committed coverage changes',
        primary_units='54 snapshot × solver seed × action combinations, repeats averaged; correlated session actions not independent samples',
        no_automatic_followup=True)
    write(out/'plan.json',plan);start=time.monotonic();rows=[]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool,(out/'runs.jsonl').open('w') as log:
        futures=[pool.submit(pair_run,(pair,binary,out,source)) for pair in pairs]
        for future in as_completed(futures):
            for row in future.result():rows.append(row);log.write(json.dumps(row)+'\n');log.flush()
    timed=utc();write(out/'TIMED_COMPLETE.json',dict(finished_utc=timed,wall_seconds=time.monotonic()-start,cpu_seconds=sum(r.get('cpu_seconds',0) for r in rows),sessions=len(rows),all_successful=all(r['success'] for r in rows)))
    audit_start=utc();start=time.monotonic();audits=[]
    with ProcessPoolExecutor(max_workers=min(args.jobs,len(snapshots))) as pool:
        tasks=[pool.submit(audit,(s,[r for r in rows if r['snapshot']==s['id']],str(out))) for s in snapshots]
        for future in as_completed(tasks):
            a=future.result();audits.append(a);write(out/'audit'/a['snapshot']/'audit.json',a)
    passed=all(a['all_passed'] for a in audits)
    write(out/'AUDIT_COMPLETE.json',dict(started_utc=audit_start,after_timed=timed,all_passed=passed,wall_seconds=time.monotonic()-start,cpu_seconds=sum(a['cpu_seconds'] for a in audits),
        audit_files_sha256={str(f.relative_to(out)):digest(f) for f in sorted((out/'audit').glob('**/audit.json'))}))
    unchanged=all(digest(f)==h for f,h in sources.items())
    write(out/'COMPLETE.json',dict(all_passed=passed and unchanged,unchanged_sources=unchanged,sessions=len(rows),actions=216,
        bindings={n:digest(out/n) for n in ('plan.json','runs.jsonl','TIMED_COMPLETE.json','AUDIT_COMPLETE.json')},no_followup_started=True))
    from report_coverage_block_pairs import report
    summary=report(out);print(json.dumps(dict(output=str(out),all_passed=passed and unchanged,recommend_continue=summary['recommend_continue'])),flush=True)
    if not passed or not unchanged:raise SystemExit(1)
if __name__=='__main__':main()
