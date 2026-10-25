"""Offline literal models only: no C++ executable and no solver search."""
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from review_saved_candidates import (CandidatePool, compare_at, marginal, union,
    covered, pack, FrozenCoverage, no_search, is_timely, first_observed, model_id)


def candidate(name, zero=0, one=0, t=1., origin='plain', line=1, timely=True):
    return dict(id=name, model_hash=name, mask=(zero, one), completion=t,
                origin=origin, line=line, timely=timely)


def model(b, x):
    return [dict(name='b', sort='Bool', value=b), dict(name='x', sort='Int', value=str(x))]


class PoolTests(unittest.TestCase):
    def test_fifo_fixed_capacity_no_refresh(self):
        p = CandidatePool(4, 'all', (0, 0), set())
        for i in range(4): p.admit(candidate(str(i), t=i))
        p.admit(candidate('0', t=5))
        p.admit(candidate('4', t=6))
        self.assertEqual(list(p.pool), ['1','2','3','4'])
        self.assertEqual(p.evictions, 1)
        self.assertEqual(p.peak_size, 4)

    def test_capacity_eight(self):
        p = CandidatePool(8, 'all', (0, 0), set())
        for i in range(10): p.admit(candidate(str(i), t=i))
        self.assertEqual(list(p.pool), list(map(str, range(2,10))))
        self.assertEqual(p.evictions, 2)

    def test_no_candidates_no_invented_output(self):
        p = CandidatePool(4, 'all', (0, 0), set())
        self.assertIsNone(p.select(None, 2))
        self.assertEqual(p.outputs, 0)

    def test_seed_already_submitted_and_cross_action_duplicate(self):
        p = CandidatePool(4, 'all', (1,0), {'seed'})
        p.admit(candidate('seed', one=1))
        self.assertFalse(p.pool)
        x = candidate('x', one=1)
        p.admit(x)
        self.assertEqual(p.select(x, 2)['gain'], 1)
        p.admit(x)
        self.assertIsNone(p.select(x, 3))

    def test_late_checkpoint_not_admitted(self):
        w = dict(completion=1.1, deadline=1., timely=True)
        self.assertFalse(is_timely(dict(validated=.9), w, 10))
        p = CandidatePool(4, 'all', (7,0), set())
        p.admit(candidate('late', one=7, timely=False))
        self.assertIsNone(p.select(None, 10))

    def test_timely_witness_survives_later_unknown(self):
        p = CandidatePool(4, 'all', (1,0), set())
        x = candidate('timely', one=1, t=.1)
        p.admit(x)
        # A subsequent query without a model is no admission; no invalidation.
        self.assertEqual(p.select(None, 2)['candidate']['id'], 'timely')

    def test_future_candidate_fails_closed(self):
        p = CandidatePool(4, 'all', (1,0), set())
        p.admit(candidate('future', one=1, t=5))
        with self.assertRaises(ValueError): p.select(None, 4)

    def test_original_tie_priority(self):
        p = CandidatePool(4, 'all', (1,0), set())
        old = candidate('old', one=1, t=.1)
        original = candidate('original', one=1, t=.9)
        p.admit(old)
        self.assertEqual(p.select(original, 1)['candidate']['id'], 'original')

    def test_observations_tie_break(self):
        p = CandidatePool(4, 'all', (1,0), set())
        p.admit(candidate('observe_more', zero=2, one=1))
        original = candidate('original', one=1)
        self.assertEqual(p.select(original, 2)['candidate']['id'], 'observe_more')

    def test_rescore_after_output(self):
        p = CandidatePool(4, 'all', (1,0), set())
        a, b = candidate('a', one=1), candidate('b', one=1, t=2)
        p.admit(a); p.admit(b)
        self.assertEqual(p.select(a, 3)['gain'], 1)
        self.assertEqual(p.select(None, 4)['gain'], 0)

    def test_first_observation_pair_can_increase_residual(self):
        base = (0,0); a = (1,0); b = (0,1)
        self.assertEqual(marginal(base, b)[0], 0)
        self.assertEqual(marginal(union(base,a), b)[0], 1)
        q = compare_at(base,a,b)
        self.assertEqual(q['first_observation_pairing'], 1)
        self.assertEqual(q['joint_gain'], q['plain_gain']+q['extra_after_plain'])

    def test_no_future_source_relabel(self):
        p = CandidatePool(4, 'ordinary_retention', (1,0), set())
        p.admit(candidate('same', one=1, origin='projection', t=1))
        self.assertIsNone(p.select(None, 1.5))
        p.admit(candidate('same', one=1, origin='plain', t=2))
        self.assertEqual(p.select(None, 3)['candidate']['origin'], 'plain')

    def test_retention_and_strict_are_different_controls(self):
        x = candidate('projection', one=1, origin='projection')
        retain = CandidatePool(4, 'ordinary_retention', (1,0), set())
        strict = CandidatePool(4, 'ordinary_strict', (1,0), set())
        retain.admit(x); strict.admit(x)
        self.assertFalse(retain.pool); self.assertFalse(strict.pool)
        self.assertEqual(retain.select(x, 2)['gain'], 1)
        self.assertIsNone(strict.select(x, 2))

    def test_old_coverage_has_zero_gain(self):
        self.assertEqual(marginal((3,3),(1,2)), (0,0))

    def test_source_identity_does_not_change_fifo(self):
        p = CandidatePool(4, 'all', (1,0), set())
        p.admit(candidate('same', one=1, origin='projection', t=1))
        p.admit(candidate('same', one=1, origin='plain', t=2))
        self.assertEqual(p.select(None, 3)['candidate']['completion'], 1)


class ManualTests(unittest.TestCase):
    def test_complete_state_short_circuit_and_pairing(self):
        f = '(declare-const b Bool)(declare-const x Int)(assert (or b (>= x 0)))'
        with no_search() as attempts:
            metric = FrozenCoverage(f)
            base = metric.apply(metric.empty.copy(), model(True, 0))
            saved = metric.export(base)
            a, b = model(False,0), model(False,1)
            masks = [pack(metric, metric.apply(metric.empty.copy(), m)) for m in (a,b)]
            aa, bb = metric.apply(base,a), metric.apply(base,b)
            both = metric.apply(aa,b)
            self.assertEqual(metric.export(base), saved)
            self.assertEqual(pack(metric,both), union(union(pack(metric,base), masks[0]), masks[1]))
            self.assertGreater(metric.count(both), max(metric.count(aa),metric.count(bb)))
            self.assertGreater(first_observed(pack(metric,base), masks[0]), 0)
            self.assertFalse(attempts)

    def test_low64_exact_unbounded_assignment(self):
        f = '(declare-const b Bool)(declare-const x Int)(assert (or b (>= x (- 18446744073709551617))))'
        with no_search():
            metric = FrozenCoverage(f)
            x = model(False,-1); y = model(False,18446744073709551615)
            a = metric.apply(metric.empty.copy(),x); b = metric.apply(metric.empty.copy(),y)
            self.assertNotEqual(model_id(x),model_id(y))
            self.assertEqual(pack(metric,a), pack(metric,b))
            self.assertEqual(metric.count(a), 0)

    def test_illegal_and_incomplete_assignment_rejected_without_search(self):
        with no_search():
            metric = FrozenCoverage('(declare-const x Int)(assert (= x 1))')
            with self.assertRaises(ValueError): metric.apply(metric.empty.copy(), [dict(name='x',sort='Int',value='2')])
            with self.assertRaises(ValueError): metric.apply(metric.empty.copy(), [])

    def test_seed_zero_gain_and_unsubmitted_branch(self):
        with no_search():
            metric = FrozenCoverage('(declare-const b Bool)(declare-const x Int)(assert (or b (>= x 0)))')
            m = model(True,3)
            empty = metric.empty.copy(); state = metric.apply(empty,m)
            self.assertEqual(metric.count(state),0)
            self.assertEqual(metric.export(metric.apply(state,m)), metric.export(state))
            self.assertNotEqual(metric.export(empty), metric.export(state))


class FrozenDecisionTests(unittest.TestCase):
    def rows(self):
        return [dict(round=11,instance=c,seed=s,capacity=k,prefix='final',
                     mode='projection_fixed',contrast='all_minus_ordinary_retention',difference=1)
                for c in ('a','b') for s in (0,1) for k in (4,8)]

    def test_two_formulas_and_two_seeds_required(self):
        from summarize_saved_candidate_review import gate
        rows=self.rows()
        self.assertTrue(gate(rows)['necessary_gate_passed'])
        self.assertFalse(gate([r for r in rows if r['instance']=='a'])['necessary_gate_passed'])
        self.assertFalse(gate([r for r in rows if r['seed']==0])['necessary_gate_passed'])

    def test_round10_cannot_supply_missing_confirmation(self):
        from summarize_saved_candidate_review import gate
        rows=self.rows()
        for r in rows:
            if r['instance']=='b':r['round']=10
        self.assertEqual(gate(rows)['decision'],'No-Go')

    def test_incomplete_and_zero_not_positive(self):
        from summarize_saved_candidate_review import gate
        for value in (None,0,-1):
            rows=self.rows()
            for r in rows:
                if r['instance']=='b' and r['seed']==1:r['difference']=value
            self.assertEqual(gate(rows)['decision'],'No-Go')

    def test_repeated_early_regression_fails(self):
        from summarize_saved_candidate_review import gate
        rows=self.rows()
        rows += [dict(r,prefix='1',difference=-1) for r in rows]
        self.assertEqual(gate(rows)['decision'],'No-Go')

if __name__ == '__main__': unittest.main()
