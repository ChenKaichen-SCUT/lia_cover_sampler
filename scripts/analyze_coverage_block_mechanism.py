#!/usr/bin/env python3
"""Read-only attribution of saved round-six witnesses; never query a solver."""
import argparse,collections,csv,hashlib,json,time
from pathlib import Path
from statistics import mean
from snapshot_replay import oracle

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p,d):Path(p).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def table(p,rows):
    with Path(p).open('w',newline='') as f:
        w=csv.DictWriter(f,list(dict.fromkeys(k for r in rows for k in r)));w.writeheader();w.writerows(rows)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--run',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    run=args.run.resolve();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    start=time.monotonic();cpu=time.process_time();plan=json.loads((run/'plan.json').read_text())
    done=json.loads((run/'COMPLETE.json').read_text());assert done['all_passed'] and all(sha(run/n)==h for n,h in done['bindings'].items())
    runs=[json.loads(l) for l in (run/'runs.jsonl').read_text().splitlines()];bindings={str(run/'COMPLETE.json'):sha(run/'COMPLETE.json'),str(Path(__file__).resolve()):sha(__file__)}
    rows=[];states=[]
    for s in plan['snapshots']:
        _,_,stat,ev,storage,expected,sample=oracle(s)
        base=[int(v[0])&int(v[1]) for v in storage.values()]
        def bits(model):
            stat._storage=storage.copy();assert ev.count_sample(sample(model))
            return [(int(v[0])&int(v[1]))&~b for v,b in zip(stat._storage.values(),base)]
        selected=[r for r in runs if r['snapshot']==s['id']]
        for r in selected:
            p=run/r['output'];assert sha(p)==r['probe_sha256'];bindings[str(p)]=sha(p)
            cp=Path(str(p)+'.checkpoints.jsonl');assert sha(cp)==r['checkpoints_sha256'];bindings[str(cp)]=sha(cp)
            d=json.loads(p.read_text());seed_bits=bits(d['input_model']);seed_gain=sum(b.bit_count() for b in seed_bits)
            records=[json.loads(l) for l in cp.read_text().splitlines()]
            for a in d['actions']:
                x=a['result'];qs=x['queries'];writes=a['checkpoint_writes'];action_records=[c for c in records if c['action']==a['action']]
                row=dict(snapshot=s['id'],instance=s['instance'],prefix=s['prefix'],seed=r['seed'],repeat=r['repeat'],action=a['action'],mode=r['mode'],
                    gain=x['gain'],input_seed_gain=seed_gain,delivered=x['candidate'] is not None,initial_block=x['initial_block'],final_block=x['final_block'],variables=x['variables'],
                    fallback=x['fallback_reason'],full_seconds=x['full_seconds'],branch_changes=x['branch_changes'],
                    fixed_core_queries=sum(bool(q['fixed_core']) for q in qs),global_target_proofs=sum(q['global_proof'] for q in qs),
                    query_count=len(qs),physical_checks=a['statistics']['counts'].get('global_calls',0),query_cap=len(qs)==6,
                    statuses=json.dumps(dict(collections.Counter(q['status'] for q in qs))),
                    seed_gain_retained=0,coverage_outside_seed=0,witness_query=None,witness_restricted=None,witness_block_size=None,
                    first_positive_seconds=x['first_positive_seconds'],checkpoint_seconds=x['checkpoint_seconds'])
                if x['candidate'] is not None:
                    cb=bits(x['candidate']);retained=sum((b&sb).bit_count() for b,sb in zip(cb,seed_bits));new=sum((b&~sb).bit_count() for b,sb in zip(cb,seed_bits))
                    assert retained+new==x['gain']
                    timely=[c for c,w in zip(action_records,writes) if w['timely']];last=timely[-1]
                    assert last['candidate']==x['candidate']
                    possible=[(i,q) for i,q in enumerate(qs) if q['status']=='sat' and q['finished']<=last['validated_seconds']]
                    assert possible;index,q=possible[-1]
                    row.update(seed_gain_retained=retained,coverage_outside_seed=new,witness_query=index+1,witness_restricted=q['restricted'],witness_block_size=q['block_size'])
                rows.append(row)
        for mode in ('global','block'):
            g=[r for r in rows if r['snapshot']==s['id'] and r['mode']==mode]
            state=dict(snapshot=s['id'],instance=s['instance'],prefix=s['prefix'],mode=mode,actions=len(g),mean_input_seed_gain=mean(r['input_seed_gain'] for r in g),
                mean_gain=mean(r['gain'] for r in g),mean_seed_gain_retained=mean(r['seed_gain_retained'] for r in g),mean_coverage_outside_seed=mean(r['coverage_outside_seed'] for r in g),
                delivered=sum(r['delivered'] for r in g),restricted_witnesses=sum(r['witness_restricted'] is True for r in g),
                unrestricted_witnesses=sum(r['witness_restricted'] is False for r in g),fallbacks=sum(bool(r['fallback']) for r in g),
                mean_initial_block=mean(r['initial_block'] for r in g),mean_final_block=mean(r['final_block'] for r in g),
                mean_full_seconds=mean(r['full_seconds'] for r in g),fixed_core_queries=sum(r['fixed_core_queries'] for r in g),
                global_target_proofs=sum(r['global_target_proofs'] for r in g),query_cap_actions=sum(r['query_cap'] for r in g),branch_changes=sum(r['branch_changes'] for r in g))
            states.append(state)
    table(out/'witnesses.csv',rows);table(out/'states.csv',states)
    dump(out/'summary.json',dict(states=states,wall_seconds=time.monotonic()-start,cpu_seconds=time.process_time()-cpu,
        source_hashes=bindings,witnesses_sha256=sha(out/'witnesses.csv'),states_sha256=sha(out/'states.csv'),no_solver_query=True,
        attribution='gain=covered bits shared with input seed marginal set + covered bits outside that set. This is descriptive overlap, not causal attribution; both parts count for a new sample.'))
    print(json.dumps(dict(actions=len(rows),states=len(states),no_solver_query=True)))
if __name__=='__main__':main()
