#!/usr/bin/env python3
"""Independent original-evaluator replay of online journals; never search."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tarfile
import time
import traceback

from reevaluate_round6_saved_candidates import (FrozenCoverage, no_search, require, model_key,
    model_id, file_hash, dump, write_csv, object_hash)

ROOT = Path(__file__).resolve().parents[1]
JOURNALS = ('initial.json', 'models.jsonl', 'commits.jsonl', 'checkpoints.jsonl', 'rounds.jsonl', 'summary.json')


def utc():
    return datetime.now(timezone.utc).isoformat()


def cpp_key(model):
    values = [str(int(v['value'])) for v in sorted(model, key=lambda v: (v['name'], v['sort'] == 'Bool'))]
    return ''.join(f'{len(v)}:{v}' for v in values)


def observations(metric, before, after):
    new = first = 0
    for node in metric.nodes:
        a, b = before[node.get_id()], after[node.get_id()]
        new += (int(b[0]) & ~int(a[0])).bit_count() + (int(b[1]) & ~int(a[1])).bit_count()
        first += ((int(b[0]) | int(b[1])) & ~(int(a[0]) | int(a[1]))).bit_count()
    return new, first


def target_hits(metric, single, targets):
    return sum(metric.target_state(single, t)['desired_seen'] for t in targets)


def remaining(metric, state, target):
    s = metric.target_state(state, target)
    return s['observed'] and not s['desired_seen']


def timely(completion, deadline):
    return type(completion) in (int, float) and math.isfinite(completion) and 0 <= completion <= deadline


class Raw:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.archive = None

    def read(self, relative):
        p = self.directory / relative
        if p.is_file():
            return p.read_bytes()
        if self.archive is None:
            self.archive = tarfile.open(self.directory / 'raw.tar.gz', 'r:gz')
        member = self.archive.getmember(relative)
        require(member.isfile(), 'non-file archive member')
        return self.archive.extractfile(member).read()

    def close(self):
        if self.archive:
            self.archive.close()


def parse_journals(raw, relative, hashes=None):
    from hashlib import sha256
    data = {}
    for name in JOURNALS:
        content = raw.read(f'{relative}/{name}')
        if hashes is not None:
            require(sha256(content).hexdigest() == hashes[name], f'journal hash mismatch: {name}')
        require(not content or content.endswith(b'\n'), f'incomplete journal line: {name}')
        data[name] = [json.loads(x) for x in content.splitlines()] if name.endswith('.jsonl') else json.loads(content)
    return data


def audit_journals(metric, saved, data, expected_source=None):
    initial, final = data['initial.json'], data['summary.json']
    require(initial['mode'] in ('global_pack', 'residual_cardinality'), 'unknown mode')
    if expected_source:
        require(initial['source_sha256'] == final['source_sha256'] == expected_source, 'source digest mismatch')
    require(initial['mode'] == final['mode'] and initial['rng_seed'] == final['rng_seed'], 'session identity mismatch')
    require([model_key(m) for m in initial['prefix']] == [model_key(m) for m in saved['prefix']], 'prefix not restored')
    require(model_key(initial['seed']) == model_key(saved['seed']), 'seed not restored')
    state = metric.empty.copy()
    emitted = set()
    for m in saved['prefix']:
        require(model_key(m) not in emitted, 'duplicate original prefix')
        emitted.add(model_key(m)); state = metric.apply(state, m)
    prefix_covered = metric.count(state)
    seed_added = model_key(saved['seed']) not in emitted
    # Always validate even if duplicate, but only count once.
    state = metric.apply(state, saved['seed']); emitted.add(model_key(saved['seed']))
    require(initial['seed_added'] == seed_added, 'incorrect seed membership')
    require(initial['coverage'] == metric.export(state), 'S0 full observations mismatch')
    require(initial['coverage_version'] == initial['new_outputs'] == 0, 'seed counted as new output')
    require(set(initial['dedup_keys']) == {cpp_key(m) for m in saved['prefix'] + [saved['seed']]}, 'dedup restoration mismatch')
    require(len(initial['dedup_keys']) == len(emitted), 'duplicate dedup keys')
    s0 = metric.count(state)
    require(initial['budget_seconds'] == final['budget_seconds'] > 0, 'budget mismatch')
    require(initial['initialization_seconds'] == final['initialization_seconds'], 'initialization cost mismatch')
    require(abs(initial['initialization_seconds']-sum(initial[k] for k in ('setup_seconds','formula_seconds','restore_seconds','engine_seconds'))) < 1e-8, 'initialization stages mismatch')
    rounds, models, receipts, checkpoints = (data[n] for n in ('rounds.jsonl', 'models.jsonl', 'commits.jsonl', 'checkpoints.jsonl'))
    require(len(rounds) == final['rounds'], 'missing round record')
    model_by_id = {m['model_id']: m for m in models}; receipt_by_id = {m['model_id']: m for m in receipts}
    require(len(model_by_id) == len(models) and len(receipt_by_id) == len(receipts), 'duplicate output identity')
    require(model_by_id.keys() == receipt_by_id.keys(), 'orphan output/receipt')
    cp_by_key = {(c['round'], c['query']): c for c in checkpoints}
    require(len(cp_by_key) == len(checkpoints), 'duplicate checkpoint identity')
    used_checkpoints, used_models = set(), set()
    outputs, round_rows, query_rows = [], [], []
    version = 0; marginal_sum = 0; stalled = 0; late_models = 0; prior_end = 0
    total_calls = 0; targets_attempted = 0; fallback_calls = 0
    costs = {}
    for index, r in enumerate(rounds):
        require(r['round'] == index, 'round order changed')
        before = metric.count(state)
        require(r['coverage_version_before'] == version and r['covered_before'] == before, 'stale online state')
        require(r['started_timestamp'] >= prior_end - 1e-8, 'round time overlap')
        require(0 < r['budget_seconds'] <= 1, 'round budget changed')
        require(abs(r['deadline_timestamp'] - min(initial['budget_seconds'], r['started_timestamp'] + r['budget_seconds'])) < 1e-8, 'deadline mismatch')
        require(r['full_seconds'] >= r['search_seconds'] >= 0, 'cost accounting mismatch')
        prior_end = r['started_timestamp'] + r['full_seconds']
        window = r['window']; require(len(window) <= 8, 'oversize window')
        require(len({tuple(t[:2]) for t in window}) == len(window), 'duplicate window bit')
        require(all(remaining(metric, state, t) for t in window), 'completed/first-observation target entered main window')
        require(0 <= r['target_calls'] <= 3 and 0 <= r['exploration_calls'] <= 1, 'query cap exceeded')
        require(len(r['queries']) == r['target_calls'] + r['exploration_calls'], 'query log mismatch')
        q_by_id = {q['query']: q for q in r['queries']}
        require(list(q_by_id) == list(range(1, len(q_by_id)+1)), 'query order')
        for q in r['queries']:
            require(q['coverage_version'] == version, 'query version')
            require(0 <= q['finished_round_seconds'] <= r['search_seconds']+1e-8, 'query completion outside search trace')
            require(all(t in window for t in q['encoded_window']), 'encoded target outside current window')
            require(len({tuple(t[:2]) for t in q['encoded_window']}) == len(q['encoded_window']), 'duplicate encoded target')
            if q['threshold_proof']:
                require(q['kind'] == 'residual_cardinality' and q['status'] == 'unsat', 'invalid threshold proof')
                require(q['proof_scope'] == 'original_formula_window_threshold', 'wrong proof scope')
            if q['kind'] == 'residual_cardinality':
                require(q['threshold'] >= 1, 'nonpositive threshold')
                if q['status'] in ('sat', 'unsat'):
                    require(q['encoding_complete'] and q['encoded_window'] == window, 'partial encoded threshold falsely completed')
            query_rows.append(dict(round=index, **q))
        timely_cps = []
        previous_gain = (-1, -1)
        for w in r['checkpoint_writes']:
            key = (index, w['query']); require(key in cp_by_key, 'missing checkpoint payload')
            require(key not in used_checkpoints, 'checkpoint reused'); used_checkpoints.add(key)
            cp = cp_by_key[key]; q = q_by_id[cp['query']]
            require(cp['coverage_version'] == version and cp['origin'] == q['kind'], 'checkpoint provenance/version mismatch')
            require(q['status'] == 'sat', 'checkpoint without SAT')
            require(model_key(cp['candidate']) not in emitted, 'checkpoint duplicate of formal state')
            require(cp['validated_timestamp'] <= w['completion_timestamp'], 'checkpoint timestamp reversed')
            require(w['completion_timestamp'] <= r['started_timestamp']+r['search_seconds']+1e-8, 'checkpoint completed after recorded search end')
            after = metric.apply(state, cp['candidate']); single = metric.apply(metric.empty, cp['candidate'])
            gain = metric.count(after)-before; new, first = observations(metric, state, after)
            hit = target_hits(metric, single, window)
            require((gain, new, hit) == (cp['gain'], cp['observations'], cp['window_hits']), 'checkpoint scores mismatch')
            require(hit <= gain, 'window hits exceed full marginal')
            if cp['origin'] == 'residual_cardinality':
                require(target_hits(metric, single, q['encoded_window']) >= q['threshold'], 'threshold witness fails exact Reach/bit evaluation')
            is_timely = timely(w['completion_timestamp'], r['deadline_timestamp'])
            require(w['timely'] == is_timely, 'checkpoint deadline misclassified')
            if is_timely:
                require((gain, new) > previous_gain, 'saved candidate is not an improvement')
                timely_cps.append(cp); previous_gain = (gain, new)
        if r['best_query'] is None:
            require(not timely_cps, 'timely best discarded')
        else:
            require(timely_cps and r['best_query'] == timely_cps[-1]['query'], 'retained witness differs from original best')
        gain = 0
        if r['model_id'] is not None:
            identity = r['model_id']; require(identity in model_by_id and identity not in used_models, 'missing/reused output')
            used_models.add(identity); m, receipt = model_by_id[identity], receipt_by_id[identity]
            require(m['round'] == receipt['round'] == index, 'output round mismatch')
            require(timely_cps and m['query'] == r['best_query'], 'output not retained candidate')
            require(model_key(m['model']) == model_key(timely_cps[-1]['candidate']), 'output/checkpoint model mismatch')
            require(receipt['completion_timestamp'] == r['completion_timestamp'], 'completion time mismatch')
            require(receipt['deadline_timestamp'] == r['deadline_timestamp'], 'receipt deadline mismatch')
            require(r['receipt_finished_timestamp'] >= receipt['completion_timestamp'], 'receipt precedes model')
            require(r['receipt_finished_timestamp'] <= r['started_timestamp']+r['full_seconds']+1e-8, 'receipt exceeds recorded round cost')
            is_timely = timely(receipt['completion_timestamp'], receipt['deadline_timestamp'])
            require(receipt['timely'] == is_timely and r['committed'] == is_timely, 'late output committed or timely output lost')
            if is_timely:
                require(model_key(m['model']) not in emitted, 'formal duplicate')
                after = metric.apply(state, m['model']); single = metric.apply(metric.empty, m['model'])
                gain = metric.count(after)-before; new, first = observations(metric, state, after)
                require((m['coverage_version_before'], m['coverage_version_after'], m['output_index']) == (version, version+1, len(outputs)+1), 'formal version/count mismatch')
                require((m['covered_before'], m['covered_after'], m['actual_marginal_gain']) == (before, metric.count(after), gain), 'formal coverage mismatch')
                require((m['new_observation_count'], m['first_observations'], m['window_hits']) == (new, first, target_hits(metric, single, window)), 'formal observations mismatch')
                require(m['candidate_origin'] == timely_cps[-1]['origin'], 'formal origin mismatch')
                state = after; emitted.add(model_key(m['model'])); version += 1; marginal_sum += gain
                outputs.append({k:v for k,v in m.items() if k != 'model'} | dict(candidate_sha256=model_id(m['model']), completion_timestamp=receipt['completion_timestamp'], cumulative_gain=marginal_sum, cumulative_gain_pp=100*marginal_sum/metric.total))
            else:
                late_models += 1
        else:
            require(not r['committed'], 'commit without output')
        stalled = 0 if gain else stalled+1
        require(stalled == r['stalled'], 'zero gain incorrectly resets stall')
        require(r['actual_marginal_gain'] == gain and r['covered_after'] == metric.count(state) and r['coverage_version_after'] == version, 'round commit accounting mismatch')
        counts = r['statistics']['counts']; stage = r['statistics']['seconds']
        total_calls += counts.get('global_calls', 0) + counts.get('exploration_calls', 0)
        targets_attempted += r['target_calls']; fallback_calls += r['exploration_calls']
        for k,v in stage.items(): costs[k] = costs.get(k, 0)+v
        round_rows.append({k:v for k,v in r.items() if k not in ('queries', 'assessments', 'checkpoint_writes', 'statistics')} | dict(window_size=len(window), cumulative_gain=marginal_sum))
    require(used_checkpoints == set(cp_by_key), 'unassociated checkpoint')
    require(used_models == set(model_by_id), 'unassociated output')
    require(final['coverage_version'] == version == final['outputs'] == len(outputs), 'final count mismatch')
    require(final['final_coverage'] == metric.export(state), 'final full 0/1 coverage mismatch')
    require(final['initial_covered'] == s0 and final['actual_gain'] == marginal_sum == metric.count(state)-s0, 'telescoping identity mismatch')
    require(final['session_seconds'] >= final['initialization_seconds'] + final['initial_evidence_seconds'] + final['sum_round_seconds'] + final['round_logging_seconds'] - 1e-7, 'missing full lifecycle cost')
    require(final['session_seconds'] >= prior_end - 1e-7, 'final timestamp predates round')
    return dict(passed=True, s0_covered=s0, seed_gain=s0-prefix_covered, seed_added=seed_added, total=metric.total,
        actual_gain=marginal_sum, gain_pp=100*marginal_sum/metric.total, outputs=outputs, rounds=round_rows, queries=query_rows,
        costs=costs, solver_calls=total_calls, target_attempts=targets_attempted, fallback_calls=fallback_calls,
        late_formal_models=late_models, full_session_seconds=final['session_seconds'], initialization_seconds=final['initialization_seconds'],
        terminal=final['terminal'], session_overrun_seconds=final['session_overrun_seconds'], overrun_rounds=final['overrun_rounds'],
        first_window=rounds[0]['window'] if rounds else None, source_sha256=initial['source_sha256'], no_search=True)


def audit_snapshot(args):
    snapshot, runs, input_dir, source = args
    start = time.monotonic(); raw = Raw(input_dir); result = []
    with no_search() as searches:
        metric = FrozenCoverage(Path(snapshot['formula']).read_text())
        saved = json.loads((Path(snapshot['folder'])/'assignments.json').read_text())
        # Bind the historical metric state as well as the typed saved inputs.
        historical = metric.empty.copy()
        for m in saved['prefix']: historical = metric.apply(historical, m)
        expected = json.loads((Path(snapshot['folder'])/'coverage.json').read_text())
        require(metric.export(historical) == expected, 'historical snapshot coverage mismatch')
        for run in runs:
            identity = {k:run[k] for k in ('session', 'snapshot', 'instance', 'prefix', 'seed', 'mode', 'output')}
            try:
                data = parse_journals(raw, run['output'], run.get('hashes'))
                audit = audit_journals(metric, saved, data, source)
                require(data['initial.json']['rng_seed'] == run['seed'] and data['initial.json']['mode'] == run['mode'], 'plan identity mismatch')
                require(data['initial.json']['budget_seconds'] == 10, 'formal session budget changed')
                audit['run_success'] = run['success']
                audit['process_wall_seconds'] = run.get('process_wall_seconds')
                audit['cpu_seconds'] = run.get('cpu_seconds')
                result.append(identity | audit)
            except Exception as exc:
                result.append(identity | dict(passed=False, actual_gain=0, gain_pp=0, outputs=[], error=str(exc), traceback=traceback.format_exc(), run_success=run['success']))
        require(not searches, 'audit attempted model generation')
    raw.close()
    return dict(snapshot=snapshot['id'], sessions=result, wall_seconds=time.monotonic()-start, all_passed=all(r['passed'] for r in result))


def run_audit(input_dir, output, jobs=6):
    input_dir, output = Path(input_dir), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    plan = json.loads((input_dir/'plan.json').read_text())
    timed = json.loads((input_dir/'TIMED_COMPLETE.json').read_text())
    runs = [json.loads(x) for x in (input_dir/'runs.jsonl').read_text().splitlines()]
    require(len(runs) == timed['sessions'] == plan['sessions'] == 36, 'incomplete session plan')
    require(len({r['session'] for r in runs}) == 36, 'duplicate session')
    for path, expected in plan['source_hashes'].items():
        require(file_hash(path) == expected, f'frozen input/source changed: {path}')
    started = utc(); require(started >= timed['finished_utc'], 'audit overlaps timed generation')
    results = []
    with ProcessPoolExecutor(max_workers=min(jobs, 6)) as pool:
        futures = [pool.submit(audit_snapshot, (s, [r for r in runs if r['snapshot']==s['id']], str(input_dir), plan['source_sha256'])) for s in plan['snapshots']]
        for future in as_completed(futures):
            a = future.result(); results.extend(a['sessions']); dump(output/a['snapshot']/'audit.json', a)
    pairs = []
    for s in plan['snapshots']:
        for seed in plan['seeds']:
            pair = [r for r in results if r['snapshot']==s['id'] and r['seed']==seed]
            ok = len(pair)==2 and all(r['passed'] for r in pair)
            same_state = ok and pair[0]['s0_covered']==pair[1]['s0_covered'] and pair[0]['total']==pair[1]['total']
            same_window = ok and pair[0]['first_window']==pair[1]['first_window']
            pairs.append(dict(snapshot=s['id'], seed=seed, same_start=same_state, same_first_window=same_window))
    passed = all(r['passed'] for r in results) and all(p['same_start'] and p['same_first_window'] for p in pairs)
    dump(output/'sessions.json', sorted(results, key=lambda x:x['session']))
    dump(output/'COMPLETE.json', dict(started_utc=started, finished_utc=utc(), after_timed=timed['finished_utc'], sessions=len(results), all_passed=passed, pairs=pairs, no_search=True,
        plan_sha256=file_hash(input_dir/'plan.json'), runs_sha256=file_hash(input_dir/'runs.jsonl'), files={str(f.relative_to(output)):file_hash(f) for f in sorted(output.rglob('*.json'))}))
    return passed


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--jobs', type=int, default=6); a = p.parse_args()
    if not run_audit(a.input, a.output, a.jobs): raise SystemExit(1)

if __name__ == '__main__': main()
