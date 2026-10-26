#!/usr/bin/env python3
import unittest
from highdiv_gap_common import *
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,SearchForbidden,z3
from prepare_highdiv_complementarity_gap import layout_for,pack_state

def row(i,z,o,t=0,seed=0):return dict(index=i,model_id=str(i),obs=(z,o),completion=t,seed=seed)
def assignment(**values):return [dict(name=k,sort='Bool' if type(v)is bool else 'Int',value=v if type(v)is bool else str(v)) for k,v in values.items()]

class Regression(unittest.TestCase):
    def test_first_observation_pair(self):
        a=(3,0);b=(0,3)
        self.assertEqual(covered(a)|covered(b),0);self.assertEqual(covered(add(a,b)),3)
        rows=[row(1,*a),row(2,*b)];ids,meta=select_path(rows,False)
        self.assertEqual(selection_bounds(rows,ids,2,False,meta['pair_exact'])[1:3],(2,2))
    def test_short_circuit_and_low64(self):
        with no_search():
            metric=FrozenCoverage('(declare-const b Bool)(declare-const x Int)(assert (or b (= x 1)))')
            a=metric.apply(metric.empty,assignment(b=True,x=-1));b=metric.apply(metric.empty,assignment(b=False,x=1))
            xi=next(n.get_id() for n in metric.nodes if str(n)=='x')
            self.assertEqual(a[xi][:2],(0,0));self.assertNotEqual(b[xi][:2],(0,0))
            self.assertEqual(metric.empty[xi][:2],(0,0))
            q=FrozenCoverage('(declare-const x Int)(assert (= x x))')
            s=q.apply(q.empty,assignment(x=-(1<<70)+3));x=next(n for n in q.nodes if str(n)=='x')
            self.assertEqual(s[x.get_id()][0],3);self.assertEqual(int(s[x.get_id()][1]),(1<<64)-4)
    def test_duplicate_and_future(self):
        rows=[row(1,1,0,1),row(1,1,0,2),row(2,0,1,31)]
        self.assertEqual(len(pool_at(rows,30)),1);self.assertEqual(covered(prefix(pool_at(rows,30),10)),0)
        self.assertEqual(len(pool_at(rows,900)),2)
    def test_seed_separation(self):
        rows=[row(1,1,0,1,0),row(2,0,1,1,1)]
        self.assertEqual(covered(union(r['obs'] for r in rows)),1)
        for seed in [0,1]:self.assertEqual(covered(union(r['obs'] for r in rows if r['seed']==seed)),0)
        with self.assertRaises(ValueError):select_path(rows,False)
    def test_conditional_pair_bound_is_not_whole_pool_bound(self):
        self.assertEqual(selection_verdict(4,4,10,5),'proved_same_k_insufficient')
        self.assertEqual(selection_verdict(4,4,4,5),'proved_pool_insufficient')
    def test_incomplete_and_unknown_bounds(self):
        self.assertEqual(arrival_class(None,None,10,10),('right_censored_900','right_censored_900'))
        rows=[row(1,3,0),row(2,0,1),row(3,0,2)]
        s,lb,ub,status=selection_bounds(rows,[0,1],2,False,False)
        self.assertEqual((lb,ub),(1,2));self.assertIn('unknown',status)
        self.assertIsNone(quantile([],0.5))
    def test_full_states_independent(self):
        a=(1,0);b=add(a,(0,1));c=add(a,(2,0))
        self.assertEqual(a,(1,0));self.assertEqual(covered(b),1);self.assertEqual(covered(c),0)
    def test_no_search(self):
        with no_search():
            with self.assertRaises(SearchForbidden):z3.Solver().check()
    def test_observation_partition(self):
        layout=[dict(offset=0,width=1,kind='Boolean'),dict(offset=1,width=1,kind='Boolean')]
        ev,nodes=observation_events(layout,(1,0),(0,3))
        self.assertEqual(ev,dict(first_visit=2,first_single_side=0,complete_opposite=1));self.assertEqual(nodes,1)
if __name__=='__main__':unittest.main()
