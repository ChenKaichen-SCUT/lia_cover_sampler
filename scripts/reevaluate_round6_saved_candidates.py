#!/usr/bin/env python3
"""Offline re-evaluation of retained round-six candidates. Never synthesize models.

All coverage branches start from independent copies of the frozen evaluator's
complete state. Query/optimizer entry points are disabled throughout the audit.
The input run is read-only; output must be a new directory outside that run.
"""
import argparse
from collections import Counter
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tarfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
import z3
from coverage_differential_test import load_metric
from validation import decode_assignment, nodes_preorder, variables

FROZEN_COMMIT = '13ed0d7c8640b8ed7ff31d3fac5e828675dc53f7'


class EvidenceError(ValueError):
    pass


class SearchForbidden(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def object_hash(value):
    return sha(canonical(value))


def file_hash(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else sha(f.read())


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def write_csv(path, rows, default_fields=()):
    fields = list(dict.fromkeys(k for row in rows for k in row)) or list(default_fields)
    with Path(path).open('w', newline='') as f:
        writer = csv.DictWriter(f, fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


@contextmanager
def no_search():
    """Fail closed even if an imported helper accidentally requests a search."""
    attempts = []
    def forbidden(*args, **kwargs):
        attempts.append('forbidden_search')
        raise SearchForbidden('offline review forbids solver/optimizer/fixedpoint search')
    with ExitStack() as stack:
        for cls, names in ((z3.Solver, ('check', 'consequences', 'cube')),
                           (z3.Optimize, ('check',)),
                           (z3.Fixedpoint, ('query', 'query_from_lvl'))):
            for name in names:
                if hasattr(cls, name):
                    stack.enter_context(patch.object(cls, name, forbidden))
        for module in (z3, z3.z3, z3.z3core):
            for name in ('Z3_solver_check', 'Z3_solver_check_assumptions',
                         'Z3_optimize_check', 'Z3_fixedpoint_query',
                         'Z3_fixedpoint_query_relations'):
                if hasattr(module, name):
                    stack.enter_context(patch.object(module, name, forbidden))
        yield attempts


def model_key(model):
    return tuple(sorted((v['name'], v['sort'], str(int(v['value']))
                         if v['sort'] == 'Int' else str(v['value'])) for v in model))


def model_id(model):
    return object_hash(model_key(model)) if model is not None else None


class FrozenCoverage:
    """Only the frozen manual evaluator parses/evaluates original AST nodes."""
    def __init__(self, formula_text):
        metric = load_metric()
        self.stat = metric.WireCoverageStatistics()
        self.evaluator = metric.ManualSatisfiesMetric(formula_text, self.stat)
        self.root = metric.z3.And(self.evaluator._solver.assertions())
        self.nodes = nodes_preorder(self.root)
        self.decls = variables(self.root)
        self.empty = self.stat._storage.copy()
        self.total = sum(self.stat.node_total(n.get_id(), self.empty[n.get_id()][2]) for n in self.nodes)

    def apply(self, state, model):
        # No verify() helper: its fallback can call solver.check(). Typed complete
        # substitution is unnecessary here because the frozen evaluator checks F.
        values = decode_assignment(model, self.decls)
        require(len({name for name, _ in values}) == len(values), 'ambiguous metric variable names')
        self.stat._storage = state.copy()
        valid = self.evaluator.count_sample([(name, value) for (name, _), value in values.items()])
        require(bool(valid), 'saved model violates original formula')
        result = self.stat._storage.copy()
        require(set(result) == set(self.empty), 'AST node storage changed')
        return result

    def count(self, state):
        return sum(self.stat.node_count(*s) for s in state.values())

    def export(self, state):
        return dict(covered=self.count(state), total=self.total,
                    states=[[str(int(state[n.get_id()][1])), str(int(state[n.get_id()][0]))]
                            for n in self.nodes])

    def state_id(self, state):
        return object_hash(self.export(state))

    def target_state(self, state, target):
        node, bit, desired = target
        require(0 <= node < len(self.nodes) and 0 <= bit < 64 and type(desired) is bool,
                'invalid saved target')
        one, zero, sort = state[self.nodes[node].get_id()]
        require(sort == 'int' or bit == 0, 'invalid Boolean target bit')
        mask = 1 << bit
        return dict(observed=bool((int(one) | int(zero)) & mask),
                    desired_seen=bool(int(one if desired else zero) & mask))


def compare_models(metric, base, seed, candidate):
    """Recompute h by actually committing m, including first-observation pairing."""
    before_id = metric.state_id(base)
    count = metric.count(base)
    after_seed = metric.apply(base, seed)
    gm = metric.count(after_seed) - count
    if candidate is None:
        return dict(g_m=gm, g_x=0, candidate_minus_seed=None, candidate_vs_seed='no_witness',
                    seed_fallback_quality_upper_bound=gm, h_x=0, combined_gain=gm,
                    old_outside_seed_coverage=0, first_observation_pairing=0,
                    candidate_in_after_seed=None, identity_verified=True)
    after_x = metric.apply(base, candidate)
    combined = metric.apply(after_seed, candidate)
    gx = metric.count(after_x) - count
    hx = metric.count(combined) - metric.count(after_seed)
    # Descriptive old column is independently recomputed only for comparison.
    old_outside = 0
    for k in base:
        old_cov = int(base[k][0]) & int(base[k][1])
        m_new = (int(after_seed[k][0]) & int(after_seed[k][1])) & ~old_cov
        x_new = (int(after_x[k][0]) & int(after_x[k][1])) & ~old_cov
        old_outside += (x_new & ~m_new).bit_count()
    joint = metric.count(combined) - count
    require(joint == gm + hx, 'seed/residual identity failed')
    require(hx >= old_outside, 'residual cannot lose an independently new bit')
    require(metric.state_id(base) == before_id, 'input coverage was mutated')
    return dict(g_m=gm, g_x=gx, candidate_minus_seed=gx-gm,
                candidate_vs_seed='better' if gx > gm else 'equal' if gx == gm else 'worse',
                seed_fallback_quality_upper_bound=max(gm, gx), h_x=hx, combined_gain=joint,
                old_outside_seed_coverage=old_outside, first_observation_pairing=hx-old_outside,
                candidate_in_after_seed=None, identity_verified=True)


def ordered_union(metric, base, existing, candidates):
    """One retained witness per action, in original order; duplicate steps are zero."""
    current = base.copy()
    seen = set(existing)
    start = metric.count(base)
    rows = []
    for candidate in candidates:
        state_before = metric.state_id(current)
        previous = metric.count(current)
        duplicate = candidate is not None and model_key(candidate) in seen
        if candidate is not None and not duplicate:
            current = metric.apply(current, candidate)
            seen.add(model_key(candidate))
        gain = metric.count(current) - previous
        rows.append(dict(marginal=gain, cumulative=metric.count(current)-start,
                         duplicate=duplicate, appended=candidate is not None and not duplicate,
                         state_before=state_before, state_after=metric.state_id(current)))
    union = metric.count(current)-start
    require(sum(r['marginal'] for r in rows) == union, 'ordered union identity failed')
    return rows, union


def retained_checkpoint(action, records):
    """Select only the ORIGINAL retained candidate; never maximize a new metric."""
    result = action['result']
    writes = action['checkpoint_writes']
    require(len(records) == len(writes), 'checkpoint/write count mismatch')
    timely = []
    previous = -1.0
    for i, (c, w) in enumerate(zip(records, writes)):
        require(c['action'] == action['action'] and c['index'] == w['index'] == i,
                'checkpoint index/action mismatch')
        validated, finished = c['validated_seconds'], w['finished_seconds']
        require(all(math.isfinite(t) for t in (validated, finished)), 'nonfinite checkpoint time')
        require(previous <= validated <= finished <= result['full_seconds'], 'checkpoint chronology mismatch')
        require(type(w['timely']) is bool and w['timely'] == (finished <= action['budget_seconds']),
                'checkpoint timely flag inconsistent with actual finish time')
        previous = finished
        if w['timely']:
            timely.append((c, w))
    if result['candidate'] is None:
        require(result['gain'] == 0 and not timely, 'missing originally retained timely witness')
        return None
    require(bool(timely), 'retained candidate has no timely checkpoint')
    c, w = timely[-1]
    require(model_key(c['candidate']) == model_key(result['candidate']), 'retained candidate mismatch')
    require(c['gain'] == result['gain'], 'retained gain mismatch')
    require(abs(result['checkpoint_seconds']-w['finished_seconds']) <= 1e-11,
            'retained checkpoint completion mismatch')
    return c, w


def witness_source(action, checkpoint, mode):
    """Unique sequential query interval, not merely 'some earlier SAT' or fallback."""
    unknown = dict(candidate_source='unknown', witness_query=None, witness_query_sha256=None,
                   witness_query_seconds=None, preceding_query_seconds=None,
                   source_association='not uniquely established')
    if checkpoint is None:
        return dict(unknown, candidate_source='no_witness', source_association='no witness')
    c, w = checkpoint
    queries = action['result']['queries']
    finished = [q['finished'] for q in queries]
    if not all(math.isfinite(t) and t >= 0 for t in finished):
        return unknown
    if any(b <= a for a, b in zip(finished, finished[1:])):
        return unknown
    intervals = []
    for i, q in enumerate(queries):
        end = finished[i+1] if i+1 < len(queries) else float('inf')
        if q['finished'] <= c['validated_seconds'] <= w['finished_seconds'] < end:
            intervals.append(i)
    if len(intervals) != 1 or queries[intervals[0]]['status'] != 'sat':
        return unknown
    i = intervals[0]
    q = queries[i]
    if q['restricted']:
        if q['block_size'] >= action['result']['variables']:
            return unknown
        source = 'restricted_query'
    elif q['block_size'] != action['result']['variables']:
        return unknown
    elif mode == 'global':
        source = 'direct_global_query'
    else:
        released = any(r['reason'].startswith('global_fallback:') and r['query'] <= i
                       for r in action['result']['releases'])
        source = 'global_fallback' if released else 'unrestricted_query'
    return dict(candidate_source=source, witness_query=i+1, witness_query_sha256=object_hash(q),
                witness_query_seconds=q['seconds'],
                preceding_query_seconds=sum(p['seconds'] for p in queries[:i]),
                source_association='unique sequential query/checkpoint interval; no causal attribution')


class Inputs:
    def __init__(self, run):
        self.run = Path(run).resolve()
        self.hashes = {}
        self.original_hashes = {}
        self.raw = {}
        self.archive_count = 0
        self.old_root = ROOT

    def path(self, recorded):
        p = Path(recorded)
        if p.is_absolute() and p.is_relative_to(self.old_root):
            return ROOT / p.relative_to(self.old_root)
        return p

    def read(self, path, expected=None):
        path = Path(path).resolve()
        data = path.read_bytes()
        actual = sha(data)
        if expected:
            require(actual == expected, 'file hash mismatch: '+str(path))
        self.hashes[str(path)] = actual
        return data

    def json(self, path, expected=None):
        return json.loads(self.read(path, expected))

    def raw_read(self, relative, expected=None):
        require(relative in self.raw, 'raw member missing from verified archive: '+relative)
        data = self.raw[relative]
        if expected:
            require(sha(data) == expected, 'raw member hash mismatch: '+relative)
        disk = self.run / relative
        if disk.exists():
            require(self.read(disk, sha(data)) == data, 'raw directory/archive mismatch')
        self.hashes['archive:'+relative] = sha(data)
        return data

    def preflight(self):
        done = self.json(self.run/'COMPLETE.json')
        require(done['all_passed'] and done['sessions'] == 72 and done['actions'] == 216,
                'original experiment incomplete')
        for name, expected in done['bindings'].items():
            self.read(self.run/name, expected)
        plan = self.json(self.run/'plan.json')
        source_key = next(p for p in plan['source_hashes'] if p.endswith('/scripts/run_coverage_block_pairs.py'))
        self.old_root = Path(source_key).parents[1]
        require(plan['source_commit'].startswith('41227d1') and plan['budget_seconds'] == .06,
                'unexpected original experiment')
        for recorded, expected in plan['source_hashes'].items():
            p = self.path(recorded)
            # Executables are provenance only and are never executed. Missing old
            # build/dependency installations do not prevent reading an archive.
            if p.is_file():
                self.read(p, expected)
        audit = self.json(self.run/'AUDIT_COMPLETE.json')
        require(audit['all_passed'], 'original audit incomplete')
        for name, expected in audit['audit_files_sha256'].items():
            self.read(self.run/name, expected)
        archive = self.json(self.run/'archive.json')
        self.read(self.run/archive['archive'], archive['sha256'])
        with tarfile.open(self.run/archive['archive']) as t:
            for m in t:
                if m.isfile():
                    require(m.name not in self.raw, 'duplicate archive member')
                    self.raw[m.name] = t.extractfile(m).read()
        require({n:sha(b) for n,b in self.raw.items()} == archive['files'], 'archive member manifest mismatch')
        self.archive_count = len(self.raw)
        # Protect the entire original result/report trees, not only consumed rows.
        for folder in (ROOT/'results/round6_block', ROOT/'results/round6_dev'):
            for p in folder.rglob('*'):
                if p.is_file():
                    self.original_hashes[str(p)] = file_hash(p)
        for p in [ROOT/'docs/第六轮实验报告.md', ROOT/'docs/第六轮机制实验协议.md',
                  ROOT/'src/coverage_block_generator.cpp', ROOT/'src/global.cpp']:
            self.original_hashes[str(p)] = file_hash(p)
        rows = [json.loads(line) for line in self.read(self.run/'runs.jsonl').splitlines()]
        require(len(rows) == 72 and len({r['output'] for r in rows}) == 72, 'session manifest count/identity')
        return plan, rows


def restore_snapshot(inputs, snapshot):
    folder = inputs.path(snapshot['folder'])
    formula = inputs.path(snapshot['formula'])
    text = inputs.read(formula, snapshot['input_sha256']).decode()
    for name, key in (('prefix.values','prefix_sha256'), ('seed.values','seed_sha256'),
                      ('assignments.json','assignments_sha256'), ('coverage.json','coverage_sha256')):
        inputs.read(folder/name, snapshot[key])
    saved = inputs.json(folder/'assignments.json')
    metric = FrozenCoverage(text)
    def values_line(model):
        values = decode_assignment(model, metric.decls)
        return ' '.join(str(values[str(v.decl().name()),str(v.sort())]).lower() for v in metric.decls)
    require((folder/'prefix.values').read_text().splitlines() == [values_line(m) for m in saved['prefix']],
            'typed prefix/values mismatch')
    require((folder/'seed.values').read_text().splitlines() == [values_line(saved['seed'])], 'typed seed/values mismatch')
    require(len(saved['prefix']) == snapshot['prefix'], 'prefix length mismatch')
    state = metric.empty.copy()
    emitted = set()
    for model in saved['prefix']:
        require(model_key(model) not in emitted, 'duplicate formal prefix')
        state = metric.apply(state, model)
        emitted.add(model_key(model))
    require(metric.export(state) == inputs.json(folder/'coverage.json'), 'full seen0/seen1 snapshot mismatch')
    after_seed = metric.apply(state, saved['seed'])
    return metric, state, after_seed, emitted, saved['seed']


def action_costs(action):
    result = action['result']
    costs = dict(original_full_action_seconds=result['full_seconds'],
                 original_status=result['status'], original_query_attempts=len(result['queries']),
                 all_query_elapsed_seconds=sum(q['seconds'] for q in result['queries']),
                 query_statuses=json.dumps(dict(Counter(q['status'] for q in result['queries'])), sort_keys=True),
                 original_statistics_json=json.dumps(action['statistics'], sort_keys=True),
                 per_query_phase_costs='unknown: only per-action stages and per-query total were logged',
                 direct_seed_full_cost_seconds=None)
    for name, seconds in action['statistics']['seconds'].items():
        costs['original_stage_'+name] = seconds
    return costs


def run_review(run, output):
    run, output = Path(run).resolve(), Path(output).resolve()
    require(not output.is_relative_to(run) and not run.is_relative_to(output), 'output overlaps original run')
    require(not output.is_relative_to(ROOT/'results/round6_block') and
            not output.is_relative_to(ROOT/'results/round6_dev'), 'output must be independent of original evidence')
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    inputs = Inputs(run)
    seed_rows, residual_rows, union_rows, totals, query_rows, checkpoint_rows, issues, restored = [], [], [], [], [], [], [], []
    def issue(scope, error, **identity):
        issues.append(dict(scope=scope, error=str(error), **identity))
    try:
        with no_search() as attempts:
            plan, runs = inputs.preflight()
            metric_path = Path(os.environ.get('HIGHDIV_ROOT', ROOT.parent/'HighDiv-master'))/'HighDiv/my_scripts/calc_metric.py'
            inputs.read(metric_path)
            for snapshot in plan['snapshots']:
                metric, base, after_seed, emitted, seed = restore_snapshot(inputs, snapshot)
                seed_key = model_key(seed)
                seed_obs = metric.apply(metric.empty, seed)
                after_seen = emitted | {seed_key}
                restored_id = object_hash(snapshot['id'])[:16]
                restoration = dict(snapshot=snapshot['id'], formula_sha256=snapshot['input_sha256'],
                    S=metric.export(base), S_prime=metric.export(after_seed), seed_id=model_id(seed),
                    seed_already_in_S=seed_key in emitted, prefix_model_ids=sorted(object_hash(k) for k in emitted),
                    dedup_set_sha256=object_hash(sorted(emitted)), seed_gain=metric.count(after_seed)-metric.count(base))
                dump(output/'restored'/restored_id/'states.json', restoration)
                restored.append(dict(snapshot=snapshot['id'], file='restored/'+restored_id+'/states.json',
                                     sha256=object_hash(restoration), seed_already_in_S=seed_key in emitted))
                selected = sorted((r for r in runs if r['snapshot']==snapshot['id']),
                                  key=lambda r:(r['seed'],r['repeat'],r['mode']))
                require(len(selected)==12, 'snapshot session count')
                for row in selected:
                    session_id = row['output']
                    common = dict(snapshot=snapshot['id'], instance=snapshot['instance'], prefix=snapshot['prefix'],
                        solver_seed=row['seed'], repeat=row['repeat'], mode=row['mode'], session=session_id,
                        formula_sha256=snapshot['input_sha256'], prefix_sha256=snapshot['prefix_sha256'],
                        seed_sha256=snapshot['seed_sha256'], snapshot_states_sha256=snapshot['coverage_sha256'],
                        assignments_sha256=snapshot['assignments_sha256'], plan_sha256=file_hash(run/'plan.json'),
                        run_manifest_row_sha256=object_hash(row), session_output_sha256=row['probe_sha256'],
                        checkpoints_sha256=row['checkpoints_sha256'], seed_id=model_id(seed),
                        seed_already_in_S=seed_key in emitted, formal_dedup_sha256=restoration['dedup_set_sha256'],
                        original_process_wall_seconds=row['process_wall_seconds'], original_process_cpu_seconds=row['cpu_seconds'])
                    local_rows = []
                    candidates = []
                    session_error = False
                    try:
                        require(row['success'], 'original session unsuccessful')
                        data = json.loads(inputs.raw_read(row['output'], row['probe_sha256']))
                        cp_bytes = inputs.raw_read(row['output']+'.checkpoints.jsonl', row['checkpoints_sha256'])
                        require(not cp_bytes or cp_bytes.endswith(b'\n'), 'incomplete checkpoint tail')
                        checkpoints = [json.loads(line) for line in cp_bytes.splitlines()]
                        require(data['snapshot']==metric.export(base) and model_key(data['input_model'])==seed_key,
                                'actual session input differs from restored snapshot')
                        require(data['input_gain']==restoration['seed_gain'], 'recorded seed gain mismatch')
                        require(data['mode']==row['mode'] and data['rng_seed']==row['seed'], 'session mode/seed mismatch')
                        require(data['source_sha256']==plan['source_sha256'], 'measured source identity mismatch')
                        require([a['action'] for a in data['actions']]==[0,1,2], 'original action order/count')
                        require(all(c['action'] in (0,1,2) for c in checkpoints), 'orphan checkpoint action')
                        for key in ('formula_initialization_seconds','snapshot_restore_seconds','engine_initialization_seconds',
                                    'initialization_seconds','session_seconds','sum_action_seconds','action_evidence_seconds'):
                            common['original_'+key] = data[key]
                        require(abs(data['initialization_seconds']-sum(data[k] for k in
                            ('formula_initialization_seconds','snapshot_restore_seconds','engine_initialization_seconds')))<1e-8,
                            'initialization accounting mismatch')
                        require(abs(sum(a['result']['full_seconds'] for a in data['actions'])-data['sum_action_seconds'])<1e-8,
                                'full action cost accounting mismatch')
                        require(data['session_seconds']+1e-8>=data['initialization_seconds']+data['sum_action_seconds'],
                                'session cost excludes initialization/actions')
                    except (EvidenceError, KeyError, ValueError, OSError) as e:
                        issue('session', e, **common)
                        data = dict(actions=[dict(action=i) for i in range(3)])
                        session_error = True
                    for action in data['actions']:
                        identity = dict(common, action=action['action'], action_record_sha256=object_hash(action))
                        try:
                            require(not session_error, 'session evidence unresolved')
                            require(action['budget_seconds']==plan['budget_seconds'] and
                                    action['target_seed']==3*row['seed']+action['action'], 'action budget/target seed mismatch')
                            records = [c for c in checkpoints if c['action']==action['action']]
                            retained = retained_checkpoint(action, records)
                            for c,w in zip(records,action['checkpoint_writes']):
                                # Audit every saved record, but NEVER use an intermediate
                                # record for scores, unions, or replacement selection.
                                c_state = metric.apply(base,c['candidate'])
                                require(metric.count(c_state)-metric.count(base)==c['gain'], 'checkpoint coverage mismatch')
                                checkpoint_rows.append(dict(identity, index=c['index'], candidate_id=model_id(c['candidate']),
                                    record_sha256=object_hash(c), validated_seconds=c['validated_seconds'],
                                    write_finished_seconds=w['finished_seconds'], timely=w['timely'],
                                    original_retained=retained is not None and c['index']==retained[0]['index']))
                            candidate = action['result']['candidate']
                            if candidate is not None:
                                require(model_key(candidate) not in emitted and model_key(candidate)!=seed_key,
                                        'original candidate violates formal/seed dedup contract')
                            values = compare_models(metric,base,seed,candidate)
                            require(values['g_x']==action['result']['gain'], 'original g_x changed')
                            values['candidate_in_after_seed'] = candidate is not None and model_key(candidate) in after_seen
                            if values['candidate_in_after_seed']:
                                require(values['h_x']==0, 'duplicate after seed has residual gain')
                            source = witness_source(action,retained,row['mode'])
                            if candidate is not None and source['candidate_source']=='unknown':
                                issue('source_association', 'final witness query cannot be uniquely identified', **identity)
                            c_obs = metric.apply(metric.empty,candidate) if candidate is not None else None
                            if source['witness_query'] is not None:
                                q = action['result']['queries'][source['witness_query']-1]
                                require(all(metric.target_state(c_obs,t)['desired_seen'] for t in q['targets']),
                                        'uniquely associated query targets not observed in witness')
                            primary = action['result']['primary']
                            target_values = dict(primary_json=json.dumps(primary), primary_seed_observed=None,
                                primary_seed_satisfied=None, primary_missing_in_S=None, primary_missing_after_seed=None,
                                primary_candidate_satisfied=None)
                            if primary:
                                require(len(primary)==1, 'expected one primary target')
                                t = primary[0]
                                target_values.update(primary_seed_observed=metric.target_state(seed_obs,t)['observed'],
                                    primary_seed_satisfied=metric.target_state(seed_obs,t)['desired_seen'],
                                    primary_missing_in_S=not metric.target_state(base,t)['desired_seen'],
                                    primary_missing_after_seed=not metric.target_state(after_seed,t)['desired_seen'],
                                    primary_candidate_satisfied=metric.target_state(c_obs,t)['desired_seen'] if c_obs is not None else None)
                                require(target_values['primary_missing_in_S'], 'saved primary was not missing in original S')
                            item = dict(identity, target_seed=action['target_seed'], budget_seconds=action['budget_seconds'],
                                evaluation_status='verified', witness_status='timely_witness' if retained else 'no_witness',
                                candidate_id=model_id(candidate), checkpoint_index=retained[0]['index'] if retained else None,
                                checkpoint_record_sha256=object_hash(retained[0]) if retained else None,
                                checkpoint_write_finished_seconds=retained[1]['finished_seconds'] if retained else None,
                                checkpoint_write_sha256=object_hash(retained[1]) if retained else None,
                                candidate_state_sha256=metric.state_id(c_obs) if c_obs is not None else None,
                                **source, **values, **target_values, **action_costs(action))
                            for i,q in enumerate(action['result']['queries']):
                                query_rows.append(dict(identity, query=i+1, query_sha256=object_hash(q),
                                    status=q['status'], restricted=q['restricted'], block_size=q['block_size'],
                                    elapsed_seconds=q['seconds'], finished_seconds=q['finished'],
                                    delivered_witness_query=source['witness_query']==i+1,
                                    per_query_stage_costs='unknown', query_json=json.dumps(q,sort_keys=True)))
                        except (EvidenceError, KeyError, ValueError, TypeError) as e:
                            issue('action',e,**identity)
                            candidate = None
                            item = dict(identity,evaluation_status='unresolved',witness_status='unresolved',
                                        candidate_id=None,g_m=None,g_x=None,h_x=None)
                            if 'result' in action:
                                item.update(action_costs(action))
                        local_rows.append(item)
                        candidates.append(candidate)
                        seed_rows.append(item)
                        residual_rows.append(dict(item))
                    if all(r['evaluation_status']=='verified' for r in local_rows):
                        u_rows, u = ordered_union(metric,base,emitted,candidates)
                        up_rows, up = ordered_union(metric,after_seed,after_seen,candidates)
                        independent = sum(r['g_x'] for r in local_rows)
                        residual_sum = sum(r['h_x'] for r in local_rows)
                        for item,a,b in zip(local_rows,u_rows,up_rows):
                            union_rows.append(dict(item,union_marginal=a['marginal'],residual_union_marginal=b['marginal'],
                                cumulative_U=a['cumulative'],cumulative_U_prime=b['cumulative'],
                                duplicate_from_S=a['duplicate'],duplicate_from_S_prime=b['duplicate'],appended=a['appended'],
                                union_before_sha256=a['state_before'],union_after_sha256=a['state_after'],
                                residual_union_before_sha256=b['state_before'],residual_union_after_sha256=b['state_after'],
                                session_U=u,session_U_prime=up,session_sum_g_x=independent,session_sum_h_x=residual_sum))
                        totals.append(dict(common,evaluation_status='verified',actions=3,
                            candidate_ids=json.dumps([r['candidate_id'] for r in local_rows]),
                            witness_sources=json.dumps([r['candidate_source'] for r in local_rows]),
                            timely_witnesses=sum(x is not None for x in candidates),
                            distinct_candidates=sum(r['appended'] for r in u_rows),
                            cross_action_duplicates=sum(r['duplicate'] for r in u_rows),
                            no_witness=sum(x is None for x in candidates),U=u,U_prime=up,
                            sum_g_x=independent,sum_h_x=residual_sum,U_minus_sum_g_x=u-independent,
                            U_prime_minus_sum_h_x=up-residual_sum,
                            g_m=restoration['seed_gain'],combined_seed_and_union=restoration['seed_gain']+up,
                            union_identity_verified=True,residual_union_identity_verified=True))
                    else:
                        union_rows.extend(dict(r,union_marginal=None,residual_union_marginal=None,session_U=None,
                                               session_U_prime=None) for r in local_rows)
                        totals.append(dict(common,evaluation_status='unresolved',U=None,U_prime=None))
            require(len(seed_rows)==216 and len(totals)==72, 'incomplete expected action/session inventory')
            require(not attempts, 'search attempted during offline review')
        for path, expected in inputs.original_hashes.items():
            require(file_hash(path)==expected, 'original evidence mutated: '+path)
        write_csv(output/'seed_comparison.csv',seed_rows)
        write_csv(output/'residual_after_seed.csv',residual_rows)
        write_csv(output/'session_union.csv',union_rows)
        write_csv(output/'session_totals.csv',totals)
        write_csv(output/'query_costs.csv',query_rows)
        write_csv(output/'checkpoint_audit.csv',checkpoint_rows)
        write_csv(output/'anomalies.csv',issues,('scope','error','session','action'))
        dump(output/'details.json',dict(actions=seed_rows,sessions=totals,union_actions=union_rows))
        complete = all(r['evaluation_status']=='verified' for r in seed_rows)
        dump(output/'audit.json',dict(frozen_commit=FROZEN_COMMIT,definition='对已保存候选的离线重新评价',
            created_utc=datetime.now(timezone.utc).isoformat(),actions=len(seed_rows),sessions=len(totals),
            all_records_verified=complete,anomalies=len(issues),no_witness=sum(r['witness_status']=='no_witness' for r in seed_rows),
            unresolved=sum(r['evaluation_status']!='verified' for r in seed_rows),restored_snapshots=restored,
            verified_archive_members=inputs.archive_count,no_search_attempts=True,guarded_solver_optimizer_fixedpoint=True,
            source_hashes=inputs.hashes,original_files_unchanged=inputs.original_hashes,
            source_code_sha256=file_hash(__file__),wall_seconds=time.monotonic()-started,
            original_decision='STOP; unchanged',new_experiments=False,
            analysis_cost_not_generation_cost=True,
            output_hashes={str(p.relative_to(output)):file_hash(p) for p in sorted(output.rglob('*')) if p.is_file()}))
        require(complete, 'unresolved records: see anomalies.csv; no complete-data conclusion allowed')
    except Exception as e:
        issue('fatal',e)
        write_csv(output/'anomalies.csv',issues,('scope','error','session','action'))
        dump(output/'FAILED.json',dict(error=str(e),no_new_search=True))
        raise
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,default=ROOT/'results/round6_block/main_60ms')
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    destination = run_review(args.run,args.output)
    print(json.dumps(dict(output=str(destination),actions=216,sessions=72,no_search=True)))
