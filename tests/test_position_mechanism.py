import tempfile,unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from diagnose_position_mechanism import (History,marginal,phase,future_windows,
    matched_difference,distribution,structure,no_search,FrozenCoverage,model_id,object_hash,z3,parse_atom)
from audit_native_feedback_results import affine,value

class PositionMechanism(unittest.TestCase):
    def row(self,**kw):
        r=dict(phase='30-120',covered_bin=4,grant_ms=2000,prior_zero_bin='0',K=1,gain=0,positive=0,
            full_cycle_seconds=2.,status='unknown',delivered=False,first_observations=0,check_seconds=1.8,hook_seconds=.01,preparation_seconds=.1,
            phase_retained=None,changed_variables=None,changed_groups=None,changed_equation_neighborhoods=None,conflicts_at_selection=0,next1_gain=None,next5_gain=None,next5_seconds=None)
        r.update(kw);return r
    def test_first_observation_pairing_and_immutable_state(self):
        base=(0,0);s,g,n=marginal(base,(1,0));self.assertEqual((s,g,n),((1,0),0,1));self.assertEqual(base,(0,0))
        s2,g,n=marginal(s,(0,1));self.assertEqual((s2,g,n),((1,1),1,0));self.assertEqual(s,(1,0))
    def test_unvisited_not_zero(self):
        self.assertEqual(marginal((0,0),(0,8))[1:],(0,1))
    def test_duplicate_observation_zero(self):
        self.assertEqual(marginal((3,1),(2,1))[1:],(0,0))
    def test_one_model_cannot_provide_both_sides(self):
        with self.assertRaises(ValueError):marginal((0,0),(1,1))
    def test_half_open_phase_boundaries(self):
        self.assertEqual([phase(t) for t in [0,29.99,30,120,300,600,900]],['0-30','0-30','30-120','120-300','300-600','600-900','600-900'])
    def test_history_excludes_current_and_future(self):
        h=History();self.assertTrue(h.before('a',['x'],[0])['group_novel64']);h.append('a',['x'],[0]);r=h.before('b',['x'],[0])
        self.assertTrue(r['atom_novel64']);self.assertFalse(r['variable_novel64']);self.assertFalse(r['group_novel64'])
    def test_bounded_history_expiration(self):
        h=History();h.append('a',['x'],[0])
        for i in range(64):h.append(str(i),['y'],[1])
        r=h.before('a',['x'],[0]);self.assertTrue(r['group_novel64']);self.assertEqual(r['new_groups'],0);self.assertFalse(r['atom_first'])
    def test_followup_censoring_not_zero_fill(self):
        rows=[self.row(gain=i) for i in range(6)];future_windows(rows)
        self.assertEqual(rows[0]['next5_gain'],15);self.assertIsNone(rows[1]['next5_gain']);self.assertIsNone(rows[-1]['next1_gain'])
    def test_common_strata_exclude_unmatched_extreme(self):
        rows=[self.row(K=1,gain=1),self.row(K=2,gain=3),self.row(K=2,gain=1000,covered_bin=99)]
        r=matched_difference(rows,lambda r:r['K']==2,lambda r:r['K']==1,'gain');self.assertEqual(r['delta'],2);self.assertEqual(r['weight'],1)
    def test_absent_comparison_is_unknown(self):
        r=matched_difference([self.row()],lambda r:r['K']==2,lambda r:r['K']==1,'gain');self.assertIsNone(r['delta'])
    def test_failed_queries_kept_in_denominator(self):
        rows=[self.row(),self.row(status='sat',gain=2,delivered=True)];d=distribution(rows)
        self.assertEqual(d['mean_gain'],1);self.assertEqual(d['positive_rate'],.5);self.assertEqual(d['unknown_rate'],.5);self.assertEqual(d['total_seconds'],4)
    def test_exact_linear_dependencies_cancel(self):
        x,y=z3.Ints('x y');_,terms=affine(x-x+3*y,{x.get_id():0,y.get_id():1});self.assertEqual(terms,{1:3})
    def test_phase_value_exact_unbounded_integer(self):
        x=z3.Int('x');self.assertFalse(value(z3.Not(x>2**100),{'x':2**100+1}))
    def test_atom_parser_uses_frozen_private_context(self):
        ctx=z3.Context();x=z3.Int('x',ctx=ctx)
        with no_search():
            e=parse_atom('(<= x 0)',{'x':x},ctx)
            self.assertTrue(value(e,{'x':-1}));self.assertEqual(e.ctx,ctx)
    def test_local_equations_not_global_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'case.smt2';f.write_text('(declare-const x Int)(declare-const y Int)(declare-const z Int)(assert (and (= x y) (or (= z x) (= z 5))))')
            s=structure(f,[]);self.assertIn(['x','y'],s['global_equation_groups']);self.assertIn(['z'],s['global_equation_groups'])
    def test_search_guard(self):
        with no_search() as attempts:
            with self.assertRaises(RuntimeError):z3.Solver().check()
            self.assertEqual(len(attempts),1)
    def test_model_identity_order_independent_atom_formula_bound(self):
        a=[dict(name='x',sort='Int',value='1'),dict(name='y',sort='Int',value='2')]
        self.assertEqual(model_id(a),model_id(a[::-1]));self.assertNotEqual(object_hash(dict(formula='a',expression='(<= x 0)')),object_hash(dict(formula='b',expression='(<= x 0)')))

if __name__=='__main__':unittest.main()
