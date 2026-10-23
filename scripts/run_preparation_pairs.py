#!/usr/bin/env python3
"""Bounded paired cold-snapshot mechanisms, followed by a separate audit barrier.

This never launches an online sampler, HighDiv, concurrency calibration, or a
longer-budget experiment. The output directory must be new.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
BASE='89e71b83adec3bdd065f7a00ef9d629cdf95c439'
THREAD_ENV={k:'1' for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','VECLIB_MAXIMUM_THREADS','BLIS_NUM_THREADS')}
CAPS=dict(candidates=24,batches=6,candidates_per_batch=4,attempts_per_batch=256,total_attempts=1536)
RULE=dict(unit='snapshot × seed; mean over the three paired repetitions',
    mean_cluster_delta_positive=True,median_cluster_delta_nonnegative=True,
    nonnegative_cluster_fraction_at_least=2/3,positive_development_cases_at_least=2,
    positive_snapshots_at_least=2,all_calls_independently_audited=True,
    primary_budget_seconds=.06,primary_snapshots=12,primary_seeds=[0,1,2],primary_repeats=3,
    no_automatic_end_to_end=True,no_automatic_longer_budget=True)


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def utc():return datetime.now(timezone.utc).isoformat()


def snapshot_hashes(snapshot):
    folder=Path(snapshot['folder'])
    expected={str(folder/f):snapshot[k] for f,k in (
        ('prefix.values','prefix_sha256'),('seed.values','seed_sha256'),
        ('coverage.json','coverage_sha256'),('assignments.json','assignments_sha256'))}
    expected[snapshot['formula']]=snapshot['input_sha256']
    for path,sha in expected.items():
        if digest(path)!=sha:raise ValueError('modified frozen snapshot: '+path)
    return expected


def call_probe(task,binary,budget,out,env,expected_source):
    snapshot=task['snapshot'];folder=Path(snapshot['folder'])
    path=out/'raw'/snapshot['id']/f'seed-{task["seed"]}'/f'repeat-{task["repeat"]}'/(task['mode']+'.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    command=[str(binary),snapshot['formula'],str(folder/'prefix.values'),str(folder/'seed.values'),
             task['mode'],str(budget),str(task['seed']),str(path)]
    row={k:v for k,v in task.items() if k!='snapshot'}
    row.update(snapshot=snapshot['id'],instance=snapshot['instance'],prefix=snapshot['prefix'],
        budget_seconds=budget,command=command,output=str(path.relative_to(out)),
        snapshot_hashes=snapshot_hashes(snapshot),started_utc=utc(),success=False)
    stdout=path.with_suffix('.stdout.log');stderr=path.with_suffix('.stderr.log')
    timed_out=threading.Event()
    started=time.monotonic()
    try:
        with stdout.open('wb') as sout,stderr.open('wb') as serr:
            proc=subprocess.Popen(command,stdout=sout,stderr=serr,stdin=subprocess.DEVNULL,env=env,start_new_session=True)
            def expire():
                try:os.killpg(proc.pid,signal.SIGKILL);timed_out.set()
                except ProcessLookupError:pass
            watchdog=threading.Timer(60,expire);watchdog.start()
            try:
                # wait4 binds CPU to this PID; process-wide child-rusage deltas
                # would double-count concurrent ThreadPool children.
                _,status,usage=os.wait4(proc.pid,0)
                proc.returncode=os.waitstatus_to_exitcode(status)
            finally:watchdog.cancel()
        row.update(process_wall_seconds=time.monotonic()-started,
            child_cpu_seconds=usage.ru_utime+usage.ru_stime,exitcode=proc.returncode,
            watchdog_timeout=timed_out.is_set(),finished_utc=utc())
        if proc.returncode:raise RuntimeError(f'probe exit {proc.returncode}; preserve stderr')
        data_bytes=path.read_bytes()
        if not data_bytes.endswith(b'\n') or len(data_bytes.splitlines())!=1:
            raise ValueError('probe output is missing, multiline, or has an incomplete tail')
        data=json.loads(data_bytes)
        if data['mode']!=task['mode'] or data['budget_seconds']!=budget:
            raise ValueError('mode/budget differs from frozen command')
        if data['source_sha256']!=expected_source:
            raise ValueError('probe was built from different source files')
        if 'rng_seed' in data and data['rng_seed']!=task['seed']:
            raise ValueError('probe RNG seed differs from command')
        row['probe']={k:v for k,v in data.items() if k not in ('candidate','proposed_candidate','input_model','snapshot')}
        row['full_probe_sha256']=digest(path);row['success']=True
    except Exception as error:
        row.setdefault('process_wall_seconds',time.monotonic()-started)
        row.update(error=str(error),traceback=traceback.format_exc())
    write(path.with_suffix('.run.json'),row)
    return row


def run_pair(payload):
    pair,binary,budget,out,env,source=payload;rows=[]
    for position,mode in enumerate(pair['order']):
        task=dict(snapshot=pair['snapshot'],seed=pair['seed'],repeat=pair['repeat'],
                  pair_index=pair['pair_index'],order_position=position,mode=mode)
        rows.append(call_probe(task,binary,budget,out,env,source))
    return rows


def assignment_key(model):
    return tuple(sorted((x['name'],x['sort'],str(x['value'])) for x in model))


def audit_snapshot_impl(payload):
    snapshot,rows,out=payload;out=Path(out)
    started,cpu=time.monotonic(),time.process_time()
    from snapshot_replay import oracle
    from validation import verify
    snapshot_hashes(snapshot)
    root,decls,stat,evaluation,storage,expected,sample=oracle(snapshot)
    saved=json.loads((Path(snapshot['folder'])/'assignments.json').read_text())
    prefix_keys={assignment_key(x) for x in saved['prefix']}
    def evaluate(model):
        if verify(root,model,declarations=decls)!='valid':raise ValueError('candidate invalid in original formula')
        stat._storage=storage.copy()
        if not evaluation.count_sample(sample(model)):raise ValueError('original metric rejected candidate')
        return sum(stat.node_count(*x) for x in stat._storage.values())-expected['covered'],stat._storage.copy()
    def preserves(raw_state,model_state):
        if list(raw_state)!=list(model_state):raise ValueError('original evaluator AST mismatch')
        return all(not(int(a[0])&~int(b[0])) and not(int(a[1])&~int(b[1]))
                   for a,b in zip(raw_state.values(),model_state.values()))
    audited=[]
    for row in rows:
        audit_start,audit_cpu=time.monotonic(),time.process_time()
        result=dict(snapshot=snapshot['id'],mode=row['mode'],seed=row['seed'],repeat=row['repeat'],
                    output=row['output'],passed=False)
        try:
            if not row['success']:raise ValueError('failed timed call: '+row.get('error','unknown'))
            path=out/row['output']
            if digest(path)!=row['full_probe_sha256']:raise ValueError('probe evidence changed')
            data=json.loads(path.read_text())
            assert data['snapshot']==expected
            assert data['prefix_covered']==expected['covered'] and data['total']==expected['total']
            assert assignment_key(data['input_model'])==assignment_key(saved['seed'])
            assert row['command'][6]==str(row['seed'])
            if 'rng_seed' in data:assert data['rng_seed']==row['seed']
            raw_gain,raw_state=evaluate(data['input_model'])
            proposed_gain,proposed_state=evaluate(data['proposed_candidate'])
            candidate_gain,candidate_state=evaluate(data['candidate'])
            assert raw_gain==data['input_gain'] and proposed_gain==data['proposed_gain']
            assert preserves(raw_state,proposed_state) and preserves(raw_state,candidate_state)
            assert assignment_key(data['input_model']) not in prefix_keys
            duplicate=assignment_key(data['proposed_candidate']) in prefix_keys
            assert duplicate==data['proposed_duplicate']
            assert data['proposed_changed']==(assignment_key(data['input_model'])!=assignment_key(data['proposed_candidate']))
            assert proposed_gain>=raw_gain
            assert data['preservation_qualified']+data['preservation_rejected']==data['valid_candidates']
            assert 0<=data['timely_valid_candidates']<=data['valid_candidates']<=CAPS['candidates']
            assert 0<=data['attempted_steps']<=CAPS['batches']
            counts=data['statistics'].get('counts',{})
            assert counts.get('candidate_attempts',0)<=CAPS['total_attempts']
            if row['mode']=='reuse' and not data['setup_exhausted']:
                assert counts.get('polish_total_attempt_limit')==CAPS['total_attempts']
            assert 0<=data['setup_seconds']<=data['full_seconds']+1e-9
            assert data['completed_in_budget']==(data['full_seconds']<=data['budget_seconds'])
            potential=0 if duplicate else proposed_gain-raw_gain
            accepted=potential if data['completed_in_budget'] else 0
            late=0 if data['completed_in_budget'] else potential
            assert accepted==data['accepted_extra_coverage'] and late==data['late_extra_coverage']
            if not data['completed_in_budget'] or duplicate:
                assert assignment_key(data['candidate'])==assignment_key(data['input_model'])
            else:assert assignment_key(data['candidate'])==assignment_key(data['proposed_candidate'])
            assert assignment_key(data['candidate']) not in prefix_keys
            assert candidate_gain-raw_gain==accepted
            assert abs(data['extra_per_full_second']-accepted/max(1e-9,data['full_seconds']))<1e-5
            result.update(passed=True,input_gain=raw_gain,proposed_gain=proposed_gain,
                actual_candidate_gain=candidate_gain,accepted_extra_coverage=accepted,
                late_extra_coverage=late,observations_preserved=True,actual_output_unique=True,
                full_probe_sha256=digest(path))
        except Exception as error:
            result.update(error=str(error),traceback=traceback.format_exc())
        result.update(audit_wall_seconds=time.monotonic()-audit_start,audit_cpu_seconds=time.process_time()-audit_cpu)
        audited.append(result)
    return dict(snapshot=snapshot['id'],calls=audited,wall_seconds=time.monotonic()-started,
                cpu_seconds=time.process_time()-cpu,all_passed=all(x['passed'] for x in audited))


def audit_snapshot(payload):
    started,cpu=time.monotonic(),time.process_time()
    try:return audit_snapshot_impl(payload)
    except Exception as error:
        snapshot,rows,_=payload
        return dict(snapshot=snapshot['id'],all_passed=False,
            wall_seconds=time.monotonic()-started,cpu_seconds=time.process_time()-cpu,
            calls=[dict(snapshot=snapshot['id'],mode=r['mode'],seed=r['seed'],repeat=r['repeat'],
                output=r['output'],passed=False,error='snapshot audit initialization failed: '+str(error),
                traceback=traceback.format_exc()) for r in rows])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--budget',type=float,default=.06)
    parser.add_argument('--jobs',type=int,default=15)
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--prefix',type=int)
    parser.add_argument('--snapshot-manifest',type=Path,default=ROOT/'results/round3_snapshots/states/manifest.json')
    args=parser.parse_args()
    if not 1<=args.jobs<=15:parser.error('workers must be between 1 and the user-authorized 15-worker limit')
    if args.repeats!=3:parser.error('this predeclared mechanism protocol requires exactly three repetitions')
    if args.budget not in (.06,.3):parser.error('only the primary symmetric 60 ms or separate symmetric 300 ms budgets are supported')
    if (args.budget==.06 and args.prefix is not None) or (args.budget==.3 and args.prefix!=1):
        parser.error('60 ms uses all existing snapshots; 300 ms requires --prefix 1')
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    binary=args.binary.resolve();manifest=args.snapshot_manifest.resolve()
    original=json.loads(manifest.read_text());snapshots=original['snapshots']
    if len(snapshots)!=12 or any(s['split']!='development' for s in snapshots):raise ValueError('expected the twelve existing development snapshots')
    if args.prefix is not None:snapshots=[s for s in snapshots if s['prefix']==args.prefix]
    if len(snapshots)!=(12 if args.budget==.06 else 3):raise ValueError('wrong number of fixed snapshots')
    sources={str(manifest):digest(manifest),str(binary):digest(binary)}
    for s in snapshots:sources.update(snapshot_hashes(s))
    cpp=sorted(list((ROOT/'src').glob('*.cpp'))+list((ROOT/'include').glob('*.hpp')))
    cmake_source_sha=hashlib.sha256(''.join(digest(f) for f in cpp).encode()).hexdigest()
    support=[ROOT/'CMakeLists.txt',ROOT/'docs/第五轮机制实验协议.md',Path(__file__),ROOT/'scripts/report_preparation_pairs.py',
        ROOT/'scripts/snapshot_replay.py',ROOT/'python/validation.py',ROOT/'python/coverage_differential_test.py',ROOT/'dependencies/source_audit.json']
    metric=Path(os.environ.get('HIGHDIV_ROOT',ROOT.parent/'HighDiv-master'))/'HighDiv/my_scripts/calc_metric.py'
    support.append(metric)
    if digest(metric)!=json.loads((ROOT/'dependencies/source_audit.json').read_text())['HighDiv/my_scripts/calc_metric.py']['sha256']:
        raise ValueError('frozen metric changed')
    sources.update({str(f):digest(f) for f in cpp+support})
    pairs=[]
    for i,s in enumerate(snapshots):
        for seed in (0,1,2):
            for repeat in range(args.repeats):
                order=['old','reuse'] if (i+seed+repeat)%2==0 else ['reuse','old']
                pairs.append(dict(pair_index=len(pairs),snapshot=s,seed=seed,repeat=repeat,order=order))
    plan=dict(created_utc=utc(),frozen_base=BASE,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        working_diff_sha256=hashlib.sha256(subprocess.check_output(['git','diff','HEAD','--','src','include','CMakeLists.txt'],cwd=ROOT)).hexdigest(),
        binary=str(binary),binary_sha256=digest(binary),source_files_sha256=cmake_source_sha,
        snapshot_manifest=str(manifest),snapshot_manifest_sha256=digest(manifest),
        budget_seconds=args.budget,role='primary_same_budget' if args.budget==.06 else 'separate_symmetric_longer_budget_diagnostic',
        jobs=args.jobs,repeats=args.repeats,seeds=[0,1,2],snapshots=snapshots,
        calls=len(pairs)*2,pairs=len(pairs),paired_order=pairs,environment=THREAD_ENV,
        source_hashes=sources,attempt_caps=CAPS,stability_rule=RULE,
        all_initialization_charged=True,audit_after_all_timed_calls=True,
        serialization_outside_algorithm=True,process_wall_also_reported=True,
        no_automatic_end_to_end=True,no_automatic_budget_followup=True)
    write(out/'plan.json',plan)
    env=os.environ.copy();env.update(THREAD_ENV)
    began=time.monotonic();rows=[]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool,(out/'runs.jsonl').open('w') as journal:
        futures=[pool.submit(run_pair,(p,binary,args.budget,out,env,cmake_source_sha)) for p in pairs]
        for future in as_completed(futures):
            for row in future.result():rows.append(row);journal.write(json.dumps(row)+'\n');journal.flush()
    timed_done=utc();timed_wall=time.monotonic()-began
    rows.sort(key=lambda r:(r['pair_index'],r['order_position']))
    write(out/'TIMED_COMPLETE.json',dict(finished_utc=timed_done,calls=len(rows),
        all_successful=all(r['success'] for r in rows),wall_seconds=timed_wall,
        child_cpu_seconds=sum(r.get('child_cpu_seconds',0) for r in rows),
        failed_calls=[r['output'] for r in rows if not r['success']],runs_sha256=digest(out/'runs.jsonl')))
    # Explicit barrier: no original-metric import or original-formula audit has
    # run concurrently with any timed child. Audit-only workers begin now.
    audit_started=utc();audit_wall=time.monotonic();audits=[]
    grouped={s['id']:[r for r in rows if r['snapshot']==s['id']] for s in snapshots}
    with ProcessPoolExecutor(max_workers=min(args.jobs,len(snapshots))) as pool:
        for future in as_completed([pool.submit(audit_snapshot,(s,grouped[s['id']],str(out))) for s in snapshots]):
            result=future.result();audits.append(result)
            write(out/'audit'/result['snapshot']/'audit.json',result)
    audited=[r for g in audits for r in g['calls']]
    audit_complete=dict(started_utc=audit_started,after_timed_complete_utc=timed_done,
        calls=len(audited),all_passed=all(r['passed'] for r in audited),
        wall_seconds=time.monotonic()-audit_wall,cpu_seconds=sum(r['cpu_seconds'] for r in audits),
        failed=[r for r in audited if not r['passed']],
        audit_files_sha256={str(p.relative_to(out)):digest(p) for p in sorted((out/'audit').glob('**/audit.json'))})
    write(out/'AUDIT_COMPLETE.json',audit_complete)
    unchanged=all(digest(f)==sha for f,sha in sources.items())
    write(out/'COMPLETE.json',dict(all_passed=audit_complete['all_passed'] and unchanged,
        calls=len(rows),pairs=len(pairs),unchanged_sources=unchanged,
        plan_sha256=digest(out/'plan.json'),runs_sha256=digest(out/'runs.jsonl'),
        timed_complete_sha256=digest(out/'TIMED_COMPLETE.json'),audit_complete_sha256=digest(out/'AUDIT_COMPLETE.json'),
        no_end_to_end_started=True,no_budget_followup_started=True))
    from report_preparation_pairs import report
    report(out)
    print(json.dumps(dict(output=str(out),calls=len(rows),all_passed=audit_complete['all_passed'] and unchanged)),flush=True)
    if not audit_complete['all_passed'] or not unchanged:raise SystemExit(1)


if __name__=='__main__':main()
