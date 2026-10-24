"""Only synthetic formulas and hand-written models; no solver or sampler search."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from reevaluate_round6_saved_candidates import (
    EvidenceError, FrozenCoverage, Inputs, SearchForbidden, action_costs,
    compare_models, model_key, no_search, ordered_union, retained_checkpoint,
    witness_source,
)
import z3
from summarize_round6_saved_evaluation import comparison, moments, paired


def integer(x):
    return [dict(name='x', sort='Int', value=str(x))]


def branched(b, x):
    return [dict(name='b', sort='Bool', value=b), *integer(x)]


def query(finished, status='sat', restricted=False, block=1):
    return dict(finished=finished, seconds=.003, status=status,
                restricted=restricted, block_size=block, targets=[])


def action_record(checkpoints, retained, *, full=.08, queries=None, mode='global'):
    """Entries: candidate, original gain, validation time, flush completion time."""
    records, writes = [], []
    for i,(candidate,gain,validated,finished) in enumerate(checkpoints):
        records.append(dict(action=0,index=i,candidate=candidate,gain=gain,validated_seconds=validated))
        writes.append(dict(index=i,finished_seconds=finished,timely=finished<=.06))
    result = dict(candidate=None if retained is None else records[retained]['candidate'],
                  gain=0 if retained is None else records[retained]['gain'], full_seconds=full,
                  checkpoint_seconds=None if retained is None else writes[retained]['finished_seconds'],
                  queries=queries or [query(.01)], variables=1, releases=[], status='timeout')
    return dict(action=0,budget_seconds=.06,result=result,checkpoint_writes=writes,
                statistics=dict(seconds={'fixed_constraint_seconds':.005,'solver_check_seconds':.03})), records


class SavedEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.guard = no_search()
        self.attempts = self.guard.__enter__()
        self.addCleanup(self.guard.__exit__, None, None, None)
        self.metric = FrozenCoverage('(declare-const x Int)(assert (>= x 0))')
        self.zero = integer(0)
        self.base = self.metric.apply(self.metric.empty,self.zero)

    def test_uncommitted_seed_compares_single_output_slot(self):
        result=compare_models(self.metric,self.base,integer(7),integer(1))
        self.assertEqual((result['g_m'],result['g_x'],result['candidate_minus_seed']),(3,1,-2))
        self.assertEqual(result['candidate_vs_seed'],'worse')
        self.assertEqual(result['seed_fallback_quality_upper_bound'],3)

    def test_already_committed_seed_has_zero_gain(self):
        result=compare_models(self.metric,self.base,self.zero,integer(3))
        self.assertEqual((result['g_m'],result['g_x'],result['h_x']),(0,2,2))

    def test_uncommitted_seed_can_have_zero_gain(self):
        base=self.metric.apply(self.base,integer(3))
        result=compare_models(self.metric,base,integer(1),integer(4))
        self.assertEqual(result['g_m'],0)
        self.assertGreater(result['g_x'],0)

    def test_no_witness_is_zero_action_not_fabricated_model(self):
        result=compare_models(self.metric,self.base,integer(7),None)
        self.assertEqual((result['g_x'],result['h_x']),(0,0))
        self.assertIsNone(result['candidate_minus_seed'])
        self.assertEqual(result['candidate_vs_seed'],'no_witness')
        self.assertEqual(result['seed_fallback_quality_upper_bound'],3)

    def test_candidate_with_only_old_coverage(self):
        base=self.metric.apply(self.base,integer(3))
        result=compare_models(self.metric,base,integer(1),integer(2))
        self.assertEqual((result['g_x'],result['h_x']),(0,0))

    def test_seed_candidate_duplicate_has_zero_residual(self):
        result=compare_models(self.metric,self.base,integer(7),integer(7))
        self.assertEqual(result['g_x'],3)
        self.assertEqual(result['h_x'],0)

    def test_full_state_pairing_exceeds_old_outside_column(self):
        metric=FrozenCoverage('(declare-const b Bool)(declare-const x Int)(assert (or b (>= x 0)))')
        base=metric.apply(metric.empty,branched(True,0))
        result=compare_models(metric,base,branched(False,0),branched(False,1))
        self.assertEqual(result['old_outside_seed_coverage'],0)
        self.assertEqual(result['h_x'],1)
        self.assertEqual(result['first_observation_pairing'],1)
        self.assertEqual(result['combined_gain'],result['g_m']+result['h_x'])

    def test_target_uses_actual_short_circuit_observation(self):
        metric=FrozenCoverage('(declare-const b Bool)(declare-const x Int)(assert (or b (>= x 0)))')
        index=next(i for i,n in enumerate(metric.nodes) if str(n)=='x')
        unseen=metric.apply(metric.empty,branched(True,1))
        seen=metric.apply(metric.empty,branched(False,1))
        self.assertEqual(metric.target_state(unseen,(index,0,True)),dict(observed=False,desired_seen=False))
        self.assertEqual(metric.target_state(seen,(index,0,True)),dict(observed=True,desired_seen=True))

    def test_union_deduplicates_across_actions(self):
        rows,u=ordered_union(self.metric,self.base,{model_key(self.zero)},[integer(3),integer(3),None])
        self.assertEqual([r['marginal'] for r in rows],[2,0,0])
        self.assertTrue(rows[1]['duplicate'])
        self.assertFalse(rows[2]['appended'])
        self.assertEqual(u,2)

    def test_union_can_exceed_sum_of_independent_scores(self):
        candidates=[integer(0),integer(1),None]
        independent=sum(compare_models(self.metric,self.metric.empty,integer(0),c)['g_x'] for c in candidates)
        rows,u=ordered_union(self.metric,self.metric.empty,set(),candidates)
        self.assertEqual(independent,0)
        self.assertEqual(u,1)
        self.assertEqual(sum(r['marginal'] for r in rows),u)

    def test_union_can_be_less_than_sum_of_independent_scores(self):
        candidates=[integer(3),integer(1),integer(2)]
        independent=sum(compare_models(self.metric,self.base,self.zero,c)['g_x'] for c in candidates)
        _,u=ordered_union(self.metric,self.base,{model_key(self.zero)},candidates)
        self.assertEqual((independent,u),(4,2))

    def test_union_after_seed_updates_dedup_and_observations(self):
        seed=integer(3)
        sp=self.metric.apply(self.base,seed)
        rows,u=ordered_union(self.metric,sp,{model_key(self.zero),model_key(seed)},[seed,integer(4),integer(4)])
        self.assertEqual([r['marginal'] for r in rows],[0,1,0])
        self.assertEqual(u,1)

    def test_coverage_branches_are_independent(self):
        original=deepcopy(self.base)
        a=self.metric.apply(self.base,integer(1))
        b=self.metric.apply(self.base,integer(2))
        self.assertEqual(self.base,original)
        self.assertEqual(self.metric.count(a),1)
        self.assertEqual(self.metric.count(b),1)
        self.assertEqual(self.metric.count(self.metric.apply(a,integer(2))),2)
        self.assertEqual(self.metric.count(b),1)

    def test_low_64_bits_preserve_unbounded_negative_inputs(self):
        metric=FrozenCoverage('(declare-const x Int)(assert (= x x))')
        base=metric.apply(metric.empty,integer(0))
        state=metric.apply(base,integer(-(2**100)-1))
        self.assertEqual(metric.count(state),64)
        self.assertEqual(metric.total,self.metric.total-64)  # equality has no integer constant node

    def test_later_timeout_preserves_original_timely_checkpoint(self):
        a,records=action_record([(integer(1),1,.019,.02)],0,queries=[query(.01),query(.07,'timeout')])
        c,w=retained_checkpoint(a,records)
        self.assertEqual(c['candidate'],integer(1))
        self.assertEqual(w['finished_seconds'],.02)
        self.assertEqual(witness_source(a,(c,w),'global')['witness_query'],1)
        self.assertEqual(action_costs(a)['original_full_action_seconds'],.08)

    def test_late_checkpoint_is_excluded(self):
        a,records=action_record([(integer(1),1,.059,.061)],None)
        self.assertIsNone(retained_checkpoint(a,records))
        a['result'].update(candidate=integer(1),gain=1,checkpoint_seconds=.061)
        with self.assertRaises(EvidenceError):retained_checkpoint(a,records)

    def test_earlier_retained_witness_survives_late_better_model(self):
        a,records=action_record([(integer(1),1,.019,.02),(integer(7),3,.059,.061)],0)
        self.assertEqual(retained_checkpoint(a,records)[0]['index'],0)

    def test_cannot_reselect_checkpoint_after_new_evaluation(self):
        a,records=action_record([(integer(8),1,.019,.02),(integer(3),2,.039,.04)],1)
        self.assertGreater(compare_models(self.metric,self.base,integer(7),integer(8))['h_x'],
                           compare_models(self.metric,self.base,integer(7),integer(3))['h_x'])
        self.assertEqual(retained_checkpoint(a,records)[0]['candidate'],integer(3))

    def test_missing_or_corrupt_checkpoint_is_not_guessed(self):
        a,records=action_record([(integer(1),1,.019,.02)],0)
        with self.assertRaises(EvidenceError):retained_checkpoint(a,[])
        records[0]['candidate']=integer(2)
        with self.assertRaises(EvidenceError):retained_checkpoint(a,records)

    def test_fallback_after_witness_does_not_relabel_restricted_source(self):
        a,records=action_record([(integer(1),1,.019,.02)],0,
            queries=[query(.01,restricted=True,block=1),query(.05,status='timeout',block=2)])
        a['result'].update(variables=2,releases=[dict(query=1,reason='global_fallback:limit')])
        self.assertEqual(witness_source(a,retained_checkpoint(a,records),'block')['candidate_source'],'restricted_query')

    def test_unique_global_fallback_query(self):
        a,records=action_record([(integer(1),1,.039,.04)],0,
            queries=[query(.01,'unsat',True,1),query(.03,block=2)])
        a['result'].update(variables=2,releases=[dict(query=1,reason='global_fallback:limit')])
        source=witness_source(a,retained_checkpoint(a,records),'block')
        self.assertEqual(source['candidate_source'],'global_fallback')
        self.assertEqual(source['witness_query'],2)
        self.assertEqual(source['preceding_query_seconds'],.003)

    def test_nonunique_or_non_sat_source_is_unknown(self):
        a,records=action_record([(integer(1),1,.039,.04)],0,
            queries=[query(.01),query(.03,'unsat')])
        self.assertEqual(witness_source(a,retained_checkpoint(a,records),'global')['candidate_source'],'unknown')
        a['result']['queries']=[query(.01),query(.01)]
        self.assertEqual(witness_source(a,retained_checkpoint(a,records),'global')['candidate_source'],'unknown')

    def test_state_identity_and_original_denominator_are_preserved(self):
        before=self.metric.state_id(self.base)
        self.assertEqual(self.metric.export(self.base)['total'],130)
        compare_models(self.metric,self.base,integer(7),integer(1))
        self.assertEqual(self.metric.state_id(self.base),before)

    def test_hash_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'data';p.write_text('corrupt')
            with self.assertRaises(EvidenceError):Inputs(tmp).read(p,'0'*64)

    def test_guard_blocks_search_entry_points(self):
        # These calls fail before reaching Z3; no solver/optimizer search executes.
        for call in (z3.Solver().check,z3.Optimize().check):
            with self.assertRaises(SearchForbidden):call()
        self.assertEqual(len(self.attempts),2)

    def test_summary_keeps_failures_and_null_direct_comparisons(self):
        rows=[dict(g_x=4,candidate_minus_seed=2),dict(g_x=0,candidate_minus_seed=None)]
        result=moments(rows,('g_x','candidate_minus_seed'))
        self.assertEqual(result['g_x_mean'],2)
        self.assertEqual(result['g_x_count'],2)
        self.assertEqual(result['candidate_minus_seed_count'],1)

    def test_paired_repeats_are_averaged_before_win_counts(self):
        rows=[]
        for repeat in (0,1):
            for mode in ('global','block'):
                rows.append(dict(snapshot='s',solver_seed=0,repeat=repeat,mode=mode,session=mode+str(repeat),
                    seed_id='m',snapshot_states_sha256='state',U=2 if mode=='global' else (4 if repeat==0 else 0)))
        pairs,units=paired(rows,('U',),False)
        self.assertEqual((len(pairs),len(units)),(2,1))
        self.assertEqual(units[0]['U_delta'],0)
        self.assertEqual(comparison(units,('U',))[0]['tie'],1)

    def test_incomplete_pair_is_not_dropped(self):
        row=dict(snapshot='s',solver_seed=0,repeat=0,mode='global',session='s')
        with self.assertRaises(EvidenceError):paired([row],('U',),False)

    def test_action_pair_checks_original_target(self):
        rows=[]
        for repeat in (0,1):
            for mode in ('global','block'):
                rows.append(dict(snapshot='s',solver_seed=0,action=0,repeat=repeat,mode=mode,session=mode+str(repeat),
                    seed_id='m',snapshot_states_sha256='state',g_x=0,primary_json=mode,target_seed=0))
        with self.assertRaises(EvidenceError):paired(rows,('g_x',),True)


if __name__=='__main__':
    unittest.main()
