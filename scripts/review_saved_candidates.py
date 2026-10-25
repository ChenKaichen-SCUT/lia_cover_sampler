#!/usr/bin/env python3
"""Round 12: evaluate saved assignments only. No sampler or search invocation.

All identities and availability come from hash-bound historical files. Packed
observations are produced by the frozen Manual evaluator, never by Z3 solving.
Each simulated commit is independently re-evaluated with Manual.
"""
import argparse
from collections import Counter, OrderedDict, defaultdict
from concurrent.futures import ProcessPoolExecutor
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import tarfile
import tempfile
import time

from reevaluate_round6_saved_candidates import (
    FrozenCoverage, no_search, require, model_id, model_key, file_hash,
    dump, write_csv, object_hash, sha, EvidenceError)
from audit_projection_inputs import typed_tokens

ROOT = Path(__file__).resolve().parents[1]
BASE = '8937f66dc45502a3c04e8c77bcc5b02c73a1c82b'
PREFIXES = (1, 2, 5, 10, 50, 100)
CAPACITIES = (4, 8)
ORDINARY = {'plain', 'extra_plain'}
MASK = (1 << 64) - 1


def lines(path):
    return [json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()]


def union(a, b):
    return a[0] | b[0], a[1] | b[1]


def covered(a):
    return (a[0] & a[1]).bit_count()


def marginal(a, b):
    both = union(a, b)
    return (covered(both) - covered(a),
            (b[0] & ~a[0]).bit_count() + (b[1] & ~a[1]).bit_count())


def first_observed(a, b):
    return ((b[0] | b[1]) & ~(a[0] | a[1])).bit_count()


def pack(metric, state):
    zero = one = 0
    for i, n in enumerate(metric.nodes):
        a, b, _ = state[n.get_id()]
        require(0 <= int(a) <= MASK and 0 <= int(b) <= MASK, 'Manual mask range')
        one |= int(a) << (64 * i)
        zero |= int(b) << (64 * i)
    return zero, one


def state_hash(state):
    return object_hash([hex(state[0]), hex(state[1])])


def is_timely(c, write, budget):
    require(c['validated'] <= write['completion'], 'validation after checkpoint')
    return bool(write['timely'] and write['completion'] <= min(write['deadline'], budget))


def compare_at(state, a, b):
    da = (union(state, a)[0] & union(state, a)[1]) & ~(state[0] & state[1])
    db = (union(state, b)[0] & union(state, b)[1]) & ~(state[0] & state[1])
    ga, oa = marginal(state, a)
    gb, ob = marginal(state, b)
    joint = covered(union(union(state, a), b)) - covered(state)
    overlap = (da & db).bit_count()
    return dict(plain_gain=ga, extra_gain=gb, plain_observations=oa,
                extra_observations=ob, plain_first_observed_bits=first_observed(state, a),
                extra_first_observed_bits=first_observed(state, b),
                covered_overlap=overlap, plain_exclusive=(da & ~db).bit_count(),
                extra_exclusive=(db & ~da).bit_count(),
                new_observation_overlap=((a[0] & b[0]) & ~state[0]).bit_count()
                  + ((a[1] & b[1]) & ~state[1]).bit_count(),
                joint_gain=joint, first_observation_pairing=joint - ga - gb + overlap,
                plain_after_extra=marginal(union(state, b), a)[0],
                extra_after_plain=marginal(union(state, a), b)[0])


class CandidatePool:
    """FIFO is updated at each arrival, never by future scores or duplicate refresh."""
    def __init__(self, capacity, policy, initial, seen):
        require(capacity in CAPACITIES, 'frozen capacity')
        require(policy in ('all', 'ordinary_retention', 'ordinary_strict'), 'pool policy')
        self.capacity, self.policy = capacity, policy
        self.state, self.seen = initial, set(seen)
        self.pool = OrderedDict()
        self.admissions = self.evictions = self.duplicate_arrivals = 0
        self.score_calls = self.selections = self.reused = self.outputs = 0
        self.peak_size = 0
        self.maintenance_seconds = self.scoring_seconds = 0.
        self.observation_build_charge_seconds = 0.
        self.lifecycle = []

    def admit(self, c):
        start = time.perf_counter()
        try:
            if not c['timely'] or (self.policy != 'all' and c['origin'] not in ORDINARY):
                return
            key = c['model_hash']
            if key in self.seen or key in self.pool:
                self.duplicate_arrivals += 1
                return
            # Offline observation computation is shared, but its measured cost is
            # charged each time a bounded online pool would need to rebuild it.
            self.observation_build_charge_seconds += c.get('observation_build_seconds', 0.)
            self.pool[key] = c
            self.admissions += 1
            self.lifecycle.append(dict(event='admit', candidate_id=c['id'], model_hash=key,
                                       seconds=c['completion'], source=c['origin']))
            if len(self.pool) > self.capacity:
                old_key, old = self.pool.popitem(last=False)
                self.evictions += 1
                self.lifecycle.append(dict(event='fifo_evict', candidate_id=old['id'],
                                           model_hash=old_key, seconds=c['completion'], source=old['origin']))
            self.peak_size = max(self.peak_size, len(self.pool))
        finally:
            self.maintenance_seconds += time.perf_counter() - start

    def select(self, original, slot_time):
        start = time.perf_counter()
        choices = dict(self.pool)
        allowed_original = (original is not None and original['timely']
                            and original['completion'] <= slot_time
                            and original['model_hash'] not in self.seen
                            and (self.policy != 'ordinary_strict' or original['origin'] in ORDINARY))
        if allowed_original:
            if original['model_hash'] not in choices:
                self.observation_build_charge_seconds += original.get('observation_build_seconds', 0.)
            # Preserve source of an earlier retained occurrence if same identity.
            choices.setdefault(original['model_hash'], original)
        self.maintenance_seconds += time.perf_counter() - start
        start = time.perf_counter()
        ranks = []
        for c in choices.values():
            require(c['timely'] and c['completion'] <= slot_time, 'future candidate leaked')
            gain, obs = marginal(self.state, c['mask'])
            ranks.append(((-gain, -obs,
                           -int(original is not None and c['model_hash'] == original['model_hash']),
                           c['completion'], c['line'], c['model_hash']), c, gain, obs))
        self.score_calls += len(ranks)
        best = min(ranks, key=lambda x: x[0]) if ranks else None
        self.scoring_seconds += time.perf_counter() - start
        if best is None:
            return None
        _, c, gain, obs = best
        start = time.perf_counter()
        was_retained = c['model_hash'] in self.pool
        is_original = original is not None and c['model_hash'] == original['model_hash']
        self.state = union(self.state, c['mask'])
        self.seen.add(c['model_hash'])
        self.pool.pop(c['model_hash'], None)
        self.outputs += 1
        self.selections += was_retained
        self.reused += not is_original
        self.maintenance_seconds += time.perf_counter() - start
        return dict(candidate=c, gain=gain, observations=obs, from_pool=was_retained,
                    changed_choice=not is_original)


def git_bytes(commit, relative):
    return subprocess.run(['git', 'show', f'{commit}:{relative}'], cwd=ROOT,
                          check=True, capture_output=True).stdout


def historical_tree():
    paths = ['results/round10_projection/main_10s', 'results/round11_projection/main_10s']
    raw = subprocess.run(['git', 'ls-tree', '-r', BASE, '--', *paths], cwd=ROOT,
                         check=True, capture_output=True).stdout
    # Check every versioned historical artifact against the frozen Git object.
    verified = {}
    for line in raw.decode().splitlines():
        meta, path = line.split('\t')
        content = (ROOT / path).read_bytes()
        blob = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
        require(blob == meta.split()[2], 'historical artifact differs: ' + path)
        verified[path] = sha(content)
    return verified


def prepare_inputs(out, temporary):
    manifest = dict(base=BASE, protocol_sha256=file_hash(ROOT / 'docs/第十二轮离线实验协议.md'),
                    history=historical_tree(), rounds={}, copied_inputs={}, source_evidence=[])
    jobs = []
    for round_no in (10, 11):
        base = ROOT / f'results/round{round_no}_projection/main_10s'
        plan = json.loads((base / 'plan.json').read_text())
        archive = json.loads((base / 'archive.json').read_text())
        done = json.loads((base / 'COMPLETE.json').read_text())
        runs = lines(base / 'runs.jsonl')
        require(file_hash(base / 'plan.json') == done['plan_sha256'], 'plan binding')
        require(file_hash(base / 'runs.jsonl') == done['journal_sha256'], 'runs binding')
        require(len(runs) == len(plan['tasks']), 'planned sessions')
        require(file_hash(base / 'raw.tar.gz') == archive['sha256'], 'archive digest')
        stage = Path(temporary) / f'round{round_no}'
        contents = {}
        with tarfile.open(base / 'raw.tar.gz') as tar:
            for member in tar:
                if not member.isfile():
                    continue
                relative = Path(member.name)
                require(not relative.is_absolute() and '..' not in relative.parts, 'unsafe archive member')
                data = tar.extractfile(member).read()
                require(member.name not in contents, 'duplicate archive member')
                contents[member.name] = sha(data)
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        require(contents == archive['files'], 'all archive members')
        # Check generating source at its own commit, not the latest working tree.
        for path, digest in plan['hashes'].items():
            p = Path(path)
            if str(p).startswith(str(ROOT)) and '/build/' not in path:
                relative = str(p.relative_to(ROOT))
                require(sha(git_bytes(plan['commit'], relative)) == digest,
                        'historical source/input Git binding: ' + relative)
                manifest['source_evidence'].append(dict(round=round_no, path=relative,
                                                        commit=plan['commit'], sha256=digest))
        groups = defaultdict(list)
        for r in runs:
            rd = stage / r['output']
            initial = json.loads((rd / 'initial.json').read_text())
            rr = dict(r)
            for key in ('formula', 'prefix_values', 'seed_values'):
                original = Path(r[key])
                relative = original.relative_to(ROOT)
                data = git_bytes(BASE, str(relative))
                require(sha(data) == plan['hashes'][str(original)], 'snapshot input hash')
                copied = out / 'inputs' / relative
                copied.parent.mkdir(parents=True, exist_ok=True)
                copied.write_bytes(data)
                rr[key] = str(copied.resolve())
                manifest['copied_inputs'][str(copied.relative_to(out))] = sha(data)
            template = initial['seed_model']
            prefix = [typed_tokens(s.split(), template) for s in Path(rr['prefix_values']).read_text().splitlines()]
            seed = [typed_tokens(s.split(), template) for s in Path(rr['seed_values']).read_text().splitlines()]
            require(prefix == initial['prefix'] and seed == [initial['seed_model']], 'snapshot literal assignments')
            require(initial['seed'] == r['seed'], 'solver seed binding')
            groups[r['state'], r['seed']].append((r['mode'], r['wave'], r['group_id'],
                object_hash(dict(prefix=prefix, seed=seed, coverage=initial['coverage']))))
            jobs.append((round_no, str(stage), rr, plan['source_sha256'], str(out.resolve()),
                         str(base.relative_to(ROOT)), archive['sha256']))
        for group in groups.values():
            expected = {'plain_only', 'plain_extra', 'projection_fixed' if round_no == 11 else 'projection_escape'}
            require({x[0] for x in group} == expected and len(group) == 3, 'complete triple')
            require(len({x[1:] for x in group}) == 1, 'paired initial state and wave')
        manifest['rounds'][str(round_no)] = dict(archive=str((base / 'raw.tar.gz').relative_to(ROOT)),
                archive_sha256=archive['sha256'], members=contents, sessions=len(runs), groups=len(groups),
                historical_code_commit=plan['commit'], source_sha256=plan['source_sha256'])
    frozen = Path(os.environ['HIGHDIV_ROOT']) / 'HighDiv/my_scripts/calc_metric.py'
    dst = out / 'inputs/frozenHighDiv/HighDiv/my_scripts/calc_metric.py'
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(frozen, dst)
    manifest['copied_inputs'][str(dst.relative_to(out))] = file_hash(dst)
    manifest['python_version'] = __import__('sys').version
    manifest['z3_version'] = __import__('z3').get_full_version()
    dump(out / 'input_manifest.json', manifest)
    return jobs, manifest


def review_session(args):
    round_no, staged, r, source, output, historical_base, archive_sha = args
    start = time.perf_counter()
    label = r['id'].replace('/', '__')
    out = Path(output) / f'round{round_no}' / 'sessions' / label
    out.mkdir(parents=True, exist_ok=False)
    context = dict(round=round_no, session=r['id'], instance=r['instance'], seed=r['seed'],
                   mode=r['mode'], snapshot=r['state'], archive_sha256=archive_sha,
                   formula_sha256=file_hash(r['formula']))
    try:
        with no_search() as searches:
            result = _review(args, out, context)
            require(not searches, 'no search calls')
        result.update(status='passed', offline_seconds=time.perf_counter() - start, search_calls=0)
    except Exception as e:
        result = dict(context, status='evidence_error', error=repr(e),
                      offline_seconds=time.perf_counter() - start)
        dump(out / 'exception.json', result)
        raise
    dump(out / 'AUDIT.json', result)
    return result


def _review(args, out, ctx):
    round_no, staged, r, source, output, historical_base, archive_sha = args
    if round_no == 10:
        from audit_projection_pairs import audit_job
    else:
        from audit_projection_fix import audit_job
    t = time.perf_counter()
    original_audit = audit_job((staged, r, source))
    legacy_seconds = time.perf_counter() - t
    saved_audit = json.loads((ROOT / historical_base / 'audit' / (r['id'].replace('/', '__') + '.json')).read_text())
    require(original_audit == saved_audit, 'frozen existing audit exact reproduction')
    d = Path(staged) / r['output']
    initial = json.loads((d / 'initial.json').read_text())
    summary = json.loads((d / 'summary.json').read_text())
    candidates, queries, cycles, models, commits = [lines(d / name) for name in (
        'candidates.jsonl', 'queries.jsonl', 'cycles.jsonl', 'models.jsonl', 'commits.jsonl')]
    receipts = {w['id']: w for w in commits}
    writes = {(cy['cycle'], w['id']): w for cy in cycles for w in cy['writes']}
    t = time.perf_counter()
    metric = FrozenCoverage(Path(r['formula']).read_text())
    evaluator_initialization_seconds = time.perf_counter() - t
    state = metric.empty.copy()
    initial_ids = set()
    for m in initial['prefix'] + [initial['seed_model']]:
        key = model_id(m)
        if key not in initial_ids:
            state = metric.apply(state, m)
            initial_ids.add(key)
    require(metric.export(state) == initial['coverage'], 'initial full observation state')
    initial_state = state.copy()
    initial_mask = pack(metric, state)
    initial_count = covered(initial_mask)
    cache = {}
    cache_cost = {}
    catalog = {}
    by_cycle = defaultdict(list)
    query_index = defaultdict(list)
    for i, q in enumerate(queries):
        query_index[q['cycle'], q['origin'], q['version']].append((i + 1, q))
    for i, c in enumerate(candidates):
        key = model_id(c['model'])
        if key not in cache:
            t = time.perf_counter()
            cache[key] = pack(metric, metric.apply(metric.empty.copy(), c['model']))
            cache_cost[key] = time.perf_counter() - t
            catalog[key] = c['model']
        ql = query_index[c['cycle'], c['origin'], c['version']]
        require(len(ql) == 1 and ql[0][1]['evidence']['raw_status'] == 'sat', 'unique raw SAT association')
        w = writes[c['cycle'], c['id']]
        c.update(model_hash=key, mask=cache[key], line=i + 1,
                 timely=is_timely(c, w, r['seconds']), completion=w['completion'],
                 checkpoint_deadline=w['deadline'], query_line=ql[0][0],
                 query_sha256=object_hash(ql[0][1]), checkpoint_sha256=object_hash(w),
                 observation_build_seconds=cache_cost[key])
        by_cycle[c['cycle']].append(c)
    slots, states, candidate_rows, original_rows = [], [initial_mask], [], []
    formal_identity_positions = defaultdict(list)
    for m in models:
        w = receipts[m['id']]
        if not (w['timely'] and w['completion'] <= min(w['deadline'], r['seconds'])):
            continue
        matches = [c for c in by_cycle[m['cycle']] if c['model_hash'] == model_id(m['model']) and c['origin'] == m['origin']]
        require(len(matches) == 1 and matches[0]['timely'], 'unique timely formal candidate')
        c = matches[0]
        require(c['completion'] <= w['completion'], 'candidate available at formal slot')
        before = pack(metric, state)
        new = metric.apply(state, m['model'])
        after = pack(metric, new)
        require(after == union(before, c['mask']), 'Manual/packed original full-state differential')
        gain, obs = marginal(before, c['mask'])
        require((gain, obs) == (m['gain'], m['observations']), 'formal gain/observations')
        slot = dict(index=len(slots) + 1, time=w['completion'], cycle=m['cycle'], candidate=c)
        slots.append(slot)
        formal_identity_positions[c['model_hash']].append(slot['index'])
        original_rows.append(dict(ctx, variant='original', position=slot['index'],
            outputs=slot['index'], historical_seconds=slot['time'], cycle=m['cycle'],
            candidate_id=c['id'], model_hash=c['model_hash'], origin=c['origin'],
            candidate_completion=c['completion'], query_sha256=c['query_sha256'],
            gain=gain, observations=obs, cumulative=covered(after) - initial_count,
            covered=covered(after), state_sha256=state_hash(after), first_observed_bits=first_observed(before, c['mask'])))
        state = new
        states.append(after)
    require(metric.export(state) == summary['coverage'], 'final full observation state')
    require(len(slots) == summary['formal_count'], 'all formal positions')
    require(sum(x['gain'] for x in original_rows) == summary['gain'], 'formal telescope')
    winner_cycle = {s['cycle']: s for s in slots}
    events, discarded = [], []
    for c in candidates:
        base = states[c['version']]
        gain, obs = marginal(base, c['mask'])
        require((gain, obs) == (c['gain'], c['observations']), 'all historical candidate scores')
        winner = winner_cycle.get(c['cycle'])
        selected = winner is not None and winner['candidate']['id'] == c['id']
        internal_duplicate = not c['formal_duplicate'] and not c['new_to_supply']
        if not c['timely']:
            reason = 'late_candidate'
        elif c['formal_duplicate']:
            reason = 'formal_duplicate'
        elif selected:
            reason = 'formal_output'
        elif internal_duplicate and c['origin'] == 'projection' and round_no == 11:
            reason = 'internal_duplicate_rejected'
        elif winner:
            if c['origin'] == 'plain' and winner['candidate']['origin'] == 'projection':
                reason = 'ordinary_replaced_by_projection'
            elif c['origin'] == 'projection' and winner['candidate']['origin'] in ORDINARY:
                reason = 'projection_lost_to_ordinary'
            elif c['origin'] in ORDINARY and winner['candidate']['origin'] in ORDINARY:
                reason = 'ordinary_lost_to_ordinary'
            else:
                reason = 'not_selected'
        else:
            reason = 'no_timely_formal_commit'
        c.update(reason=reason, selected=selected, internal_duplicate=internal_duplicate)
        after = states[c['version'] + 1] if winner else base
        require(not winner or winner['index'] == c['version'] + 1, 'version to output position')
        row = dict(ctx, candidate_id=c['id'], candidate_line=c['line'], cycle=c['cycle'],
            version=c['version'], origin=c['origin'], model_hash=c['model_hash'],
            query_line=c['query_line'], query_sha256=c['query_sha256'],
            checkpoint_sha256=c['checkpoint_sha256'], validated_seconds=c['validated'],
            completion=c['completion'], checkpoint_deadline=c['checkpoint_deadline'],
            timely=c['timely'], formal_duplicate=c['formal_duplicate'], internal_duplicate=internal_duplicate,
            selected=selected, ever_formally_output=c['model_hash'] in formal_identity_positions,
            reason=reason, gain=gain, observations=obs, first_observed_bits=first_observed(base, c['mask']),
            winner_id=winner['candidate']['id'] if winner else '',
            after_winner_gain=marginal(after, c['mask'])[0],
            after_winner_observations=marginal(after, c['mask'])[1],
            final_remaining_gain=marginal(states[-1], c['mask'])[0],
            frozen_state_sha256=state_hash(base),
            raw_candidates_sha256=r['hashes']['candidates.jsonl'])
        candidate_rows.append(row)
        if c['timely'] and not c['formal_duplicate'] and not selected:
            discarded.append((c, row, c['version'] + int(winner is not None)))
    for cy in cycles:
        cc = by_cycle[cy['cycle']]
        p = next((c for c in cc if c['origin'] == 'plain'), None)
        e = next((c for c in cc if c['origin'] != 'plain'), None)
        if p is None or e is None:
            continue
        b = states[cy['version_before']]
        win = winner_cycle.get(cy['cycle'])
        values = compare_at(b, p['mask'], e['mask'])
        require(values['joint_gain'] == values['plain_gain'] + values['extra_after_plain'], 'joint telescope')
        require(values['joint_gain'] == values['extra_gain'] + values['plain_after_extra'], 'reverse joint telescope')
        events.append(dict(ctx, cycle=cy['cycle'], version=cy['version_before'],
            plain_id=p['id'], extra_id=e['id'], plain_model_hash=p['model_hash'],
            extra_model_hash=e['model_hash'], extra_origin=e['origin'],
            plain_timely=p['timely'], extra_timely=e['timely'],
            selected_id=win['candidate']['id'] if win else '',
            selected_origin=win['candidate']['origin'] if win else '',
            strict_extra_winner=bool(win and win['candidate']['id']==e['id'] and e['gain']>p['gain']),
            **values))
    # Evaluate EVERY later historical state. Emit changes and last point only;
    # residual can increase as the real stream first observes a complementary bit.
    residual = []
    residual_evaluations = 0
    for c, row, start_index in discarded:
        last_value = None
        first_zero = None
        for j in range(start_index, len(states)):
            val = marginal(states[j], c['mask'])
            residual_evaluations += 1
            if val[0] == 0 and first_zero is None:
                first_zero = j
            if val != last_value or j == len(states) - 1:
                residual.append(dict(ctx, candidate_id=c['id'], model_hash=c['model_hash'], origin=c['origin'],
                    reason=c['reason'], formal_position=j,
                    historical_seconds=slots[j-1]['time'] if j else 0,
                    remaining_gain=val[0], remaining_observations=val[1],
                    remaining_first_observed_bits=first_observed(states[j], c['mask']),
                    final_point=j == len(states)-1))
            last_value = val
        row['first_zero_remaining_position'] = first_zero
    sources = []
    for origin in ('plain', 'extra_plain', 'projection', 'ordinary_combined', 'all'):
        selected_discards = [(c, rr, j) for c, rr, j in discarded if origin == 'all'
                            or c['origin'] == origin or (origin == 'ordinary_combined' and c['origin'] in ORDINARY)]
        unique = {}
        for c, rr, j in sorted(selected_discards, key=lambda x: (x[0]['completion'], x[0]['line'])):
            unique.setdefault(c['model_hash'], (c, rr, j))
        combined = states[-1]
        for c, _, _ in unique.values():
            combined = union(combined, c['mask'])
        all_src = [c for c in candidates if origin == 'all' or c['origin'] == origin
                   or (origin == 'ordinary_combined' and c['origin'] in ORDINARY)]
        sources.append(dict(ctx, origin=origin, candidate_occurrences=len(all_src),
            timely_candidate_occurrences=sum(c['timely'] for c in all_src),
            late_occurrences=sum(not c['timely'] for c in all_src),
            formal_duplicate_occurrences=sum(c['formal_duplicate'] for c in all_src),
            internal_duplicate_occurrences=sum(c['internal_duplicate'] for c in all_src),
            discarded_occurrences=len(selected_discards), discarded_distinct=len(unique),
            never_formally_output_distinct=sum(k not in formal_identity_positions for k in unique),
            positive_after_winner_distinct=len({c['model_hash'] for c, rr, _ in selected_discards if rr['after_winner_gain']>0}),
            positive_at_end_distinct=sum(marginal(states[-1], c['mask'])[0]>0 for c, _, _ in unique.values()),
            final_union_remaining_gain=covered(combined)-covered(states[-1])))
    query_rows = []
    for i, q in enumerate(queries):
        linked = [c for c in by_cycle[q['cycle']] if c['query_line'] == i+1]
        require(len(linked) == int(q['evidence']['raw_status'] == 'sat'), 'saved SAT complete')
        query_rows.append(dict(ctx, query_line=i+1, query_sha256=object_hash(q),
            cycle=q['cycle'], version=q['version'], origin=q['origin'], status=q['status'],
            outcome='saved_candidate' if linked else 'query_no_model',
            candidate_id=linked[0]['id'] if linked else '',
            query_seconds=q['query_seconds'], **q['evidence']))
    sim_rows, sim_summary, sim_source, lifecycles = [], [], [], []
    variants = [(policy, cap) for policy in ('all', 'ordinary_retention') for cap in CAPACITIES]
    if r['mode'].startswith('projection'):
        variants += [('ordinary_strict', cap) for cap in CAPACITIES]
    arrivals = sorted(candidates, key=lambda c: (c['completion'], c['line']))
    for policy, cap in variants:
        name = f'{policy}_{cap}'
        pool = CandidatePool(cap, policy, initial_mask, initial_ids)
        manual_state = initial_state.copy()
        cursor = 0
        verify_seconds = 0.
        cumulative_gain = 0
        by_source = defaultdict(lambda: Counter())
        for slot in slots:
            while cursor < len(arrivals) and arrivals[cursor]['completion'] <= slot['time']:
                pool.admit(arrivals[cursor])
                cursor += 1
            before = pool.state
            pick = pool.select(slot['candidate'], slot['time'])
            if pick:
                c = pick['candidate']
                t = time.perf_counter()
                manual_state = metric.apply(manual_state, c['model'])
                require(pack(metric, manual_state) == pool.state, 'simulated full-state differential')
                verify_seconds += time.perf_counter() - t
                cumulative_gain += pick['gain']
                by_source[c['origin']]['outputs'] += 1
                by_source[c['origin']]['gain'] += pick['gain']
                if not c['selected']:
                    by_source[c['origin']]['rescued_discarded_outputs'] += 1
                    by_source[c['origin']]['rescued_discarded_gain'] += pick['gain']
                if c['reason'] == 'ordinary_replaced_by_projection':
                    by_source[c['origin']]['rescued_replaced_plain_outputs'] += 1
                    by_source[c['origin']]['rescued_replaced_plain_gain'] += pick['gain']
                if c['reason'] == 'projection_lost_to_ordinary':
                    by_source[c['origin']]['rescued_losing_projection_outputs'] += 1
                    by_source[c['origin']]['rescued_losing_projection_gain'] += pick['gain']
            require(cumulative_gain == covered(pool.state) - initial_count, 'simulation telescope')
            sim_rows.append(dict(ctx, variant=name, position=slot['index'], outputs=pool.outputs,
                historical_seconds=slot['time'], cycle=slot['cycle'],
                candidate_id=c['id'] if pick else '', model_hash=c['model_hash'] if pick else '',
                origin=c['origin'] if pick else '', candidate_completion=c['completion'] if pick else '',
                query_sha256=c['query_sha256'] if pick else '',
                gain=pick['gain'] if pick else 0, observations=pick['observations'] if pick else 0,
                first_observed_bits=first_observed(before, c['mask']) if pick else 0,
                cumulative=cumulative_gain, covered=covered(pool.state),
                state_sha256=state_hash(pool.state), changed_choice=pick['changed_choice'] if pick else False,
                from_pool=pick['from_pool'] if pick else False, reason=c['reason'] if pick else 'empty_slot',
                pool_size=len(pool.pool), original_candidate_id=slot['candidate']['id']))
        sim_summary.append(dict(ctx, variant=name, capacity=cap, policy=policy,
            original_outputs=len(slots), outputs=pool.outputs, equal_count=pool.outputs==len(slots),
            gain=cumulative_gain, original_gain=summary['gain'],
            gain_difference=cumulative_gain-summary['gain'] if pool.outputs==len(slots) else None,
            admissions=pool.admissions, fifo_evictions=pool.evictions,
            duplicate_arrivals=pool.duplicate_arrivals, peak_size=pool.peak_size,
            selected_from_pool=pool.selections, changed_choices=pool.reused,
            pool_utilization=pool.selections/pool.admissions if pool.admissions else 0,
            changed_choice_fraction=pool.reused/len(slots) if slots else 0,
            scoring_calls=pool.score_calls, scoring_seconds=pool.scoring_seconds,
            maintenance_seconds=pool.maintenance_seconds,
            observation_build_charge_seconds=pool.observation_build_charge_seconds,
            charged_pool_seconds=pool.scoring_seconds+pool.maintenance_seconds+pool.observation_build_charge_seconds,
            independent_verification_seconds=verify_seconds,
            historical_process_seconds=r['process_seconds'], historical_cpu_seconds=r['cpu_seconds'],
            historical_all_query_seconds=sum(q['query_seconds'] for q in queries),
            historical_extra_seconds=summary['extra_spent'],
            unprocessed_after_final_slot=len(arrivals)-cursor))
        for origin in ('plain', 'extra_plain', 'projection'):
            sim_source.append(dict(ctx, variant=name, origin=origin,
                **{k:by_source[origin][k] for k in ('outputs','gain','rescued_discarded_outputs',
                'rescued_discarded_gain','rescued_replaced_plain_outputs','rescued_replaced_plain_gain',
                'rescued_losing_projection_outputs','rescued_losing_projection_gain')}))
        lifecycles.extend(dict(ctx, variant=name, **e) for e in pool.lifecycle)
    with gzip.open(out / 'model_catalog.jsonl.gz', 'wt') as f:
        for key, model in catalog.items():
            f.write(json.dumps(dict(model_hash=key, model=model), separators=(',', ':'))+'\n')
    for name, rows in (('candidates', candidate_rows), ('selection_events', events),
        ('residual_changes', residual), ('sources', sources), ('queries', query_rows),
        ('original_trajectory', original_rows), ('simulated_trajectory', sim_rows),
        ('pool_sessions', sim_summary), ('pool_sources', sim_source), ('pool_lifecycle', lifecycles)):
        write_csv(out / (name + '.csv'), rows)
    return dict(ctx, original_outputs=len(slots), original_gain=summary['gain'], total=metric.total,
        initial_covered=initial_count, seed_added=initial['seed_added'],
        candidates=len(candidates), distinct_saved_models=len(cache),
        late_candidates=sum(not c['timely'] for c in candidates),
        discarded=len(discarded), missing_or_ambiguous_records=0,
        historical_process_seconds=r['process_seconds'], historical_query_seconds=sum(q['query_seconds'] for q in queries),
        historical_extra_seconds=summary['extra_spent'],
        legacy_audit_seconds=legacy_seconds, legacy_audit_validated_models=original_audit['validated_models'],
        original_full_state_commits=len(slots), simulated_full_state_commits=sum(s['outputs'] for s in sim_summary),
        cached_observation_models=len(cache), cached_observation_seconds=sum(cache_cost.values()),
        evaluator_initialization_seconds=evaluator_initialization_seconds,
        residual_state_evaluations=residual_evaluations,
        source_sha256=source, final_state_sha256=state_hash(states[-1]))


def combine(out, audits):
    tables = ('candidates','selection_events','residual_changes','sources','queries',
              'original_trajectory','simulated_trajectory','pool_sessions','pool_sources','pool_lifecycle')
    for round_no in (10, 11):
        dest = out / f'round{round_no}'
        for name in tables:
            with (dest / (name + '.csv')).open('w') as f:
                writer = None
                for path in sorted((dest / 'sessions').glob('*/'+name+'.csv')):
                    with path.open() as src:
                        reader = csv.DictReader(src)
                        if not reader.fieldnames:
                            continue
                        if writer is None:
                            writer = csv.DictWriter(f, reader.fieldnames, lineterminator='\n')
                            writer.writeheader()
                        for row in reader:
                            writer.writerow(row)
        # Leave per-session AUDIT and model catalog; consolidated CSVs carry ids.
        for path in (dest / 'sessions').glob('*/*.csv'):
            path.unlink()
    write_csv(out / 'session_audit.csv', audits)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--jobs', type=int, default=6)
    args = p.parse_args()
    require(1 <= args.jobs <= 6, 'at most six offline workers')
    out = args.output.resolve()
    require(not out.exists(), 'new independent output directory required')
    for n in (10, 11):
        history = ROOT / f'results/round{n}_projection'
        require(not out.is_relative_to(history), 'historical directories are read-only')
    out.mkdir(parents=True)
    start = time.perf_counter()
    with no_search() as searches, tempfile.TemporaryDirectory(prefix='round12-offline-') as temp:
        jobs, manifest = prepare_inputs(out, temp)
        dump(out / 'STARTED.json', dict(base=BASE, script_sha256=file_hash(__file__),
              protocol_sha256=manifest['protocol_sha256'], jobs=args.jobs, sessions=len(jobs)))
        audits = []
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            for a in pool.map(review_session, jobs):
                audits.append(a)
                print(json.dumps({k:a[k] for k in ('round','session','status','candidates','original_outputs','offline_seconds')}), flush=True)
        combine(out, audits)
        require(historical_tree() == manifest['history'], 'historical artifacts changed during review')
        require(not searches, 'search attempts forbidden')
        dump(out / 'COMPLETE.json', dict(sessions=len(audits), all_passed=True, search_calls=0,
            historical_files_unchanged=True, elapsed_seconds=time.perf_counter()-start,
            original_full_state_commits=sum(a['original_full_state_commits'] for a in audits),
            simulated_full_state_commits=sum(a['simulated_full_state_commits'] for a in audits),
            distinct_model_observations=sum(a['cached_observation_models'] for a in audits),
            script_sha256=file_hash(__file__), protocol_sha256=manifest['protocol_sha256']))

if __name__ == '__main__':
    main()
