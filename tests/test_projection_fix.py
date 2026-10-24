import json,os,subprocess,tempfile,unittest,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from audit_projection_fix import audit_job,timely,best_candidate
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search
from analyze_projection_fix import gate,MODES,PREFIXES
class ProjectionFixTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.d=Path(self.tmp.name)
  self.binary=Path(os.environ.get('LIA_PROJECTION_FIXED_PROBE',ROOT/'build/round11/release/projection_fixed_probe'))
  if not self.binary.exists():self.skipTest('projection probe not built')
 def run_snapshot(self,text,mode,prefix,seed,seconds=.2):
  f=self.d/'f.smt2';f.write_text(text);pv=self.d/'prefix.values';pv.write_text(prefix);sv=self.d/'seed.values';sv.write_text(seed);out=self.d/mode
  subprocess.run([str(self.binary),str(f),mode,str(seconds),'0',str(out),str(pv),str(sv)],check=True,capture_output=True,timeout=20)
  initial=json.loads((out/'initial.json').read_text());rec=dict(output=mode,hashes={},exitcode=0,watchdog=False,id=mode,instance='synthetic',state='synthetic',mode=mode,seed=0,split='synthetic',kind='mechanism',stage='B',formula=str(f),seconds=seconds)
  result=audit_job((str(self.d),rec,initial['source_sha256']));self.assertEqual(result['status'],'passed');return out,result
 def test_all_modes_independent_manual(self):
  f='(declare-const x Int)(declare-const y Int)(assert (and (= (+ x y) 10)(or (= x 0)(= x 10))))'
  for mode in MODES:
   _,r=self.run_snapshot(f,mode,'0 10\n','0 10\n');self.assertGreater(r['gain'],0)
 def test_seed_dedup_big_integers_and_short_circuit(self):
  f='(declare-const b Bool)(declare-const x Int)(assert (or b (= x (- 18446744073709551617))))'
  _,a=self.run_snapshot(f,'plain_only','false -18446744073709551617\n','true 18446744073709551617\n')
  _,b=self.run_snapshot(f,'projection_fixed','false -18446744073709551617\ntrue 18446744073709551617\n','true 18446744073709551617\n')
  self.assertEqual(a['base_covered'],b['base_covered'])
 def test_no_false_gain_no_hidden_zero(self):
  f='(declare-const x Int)(assert (and (>= x 0)(<= x 7)))'
  for mode in MODES:
   _,r=self.run_snapshot(f,mode,'0\n1\n2\n4\n','7\n');self.assertEqual(r['gain'],0);self.assertGreater(len(r['curve']),0);self.assertTrue(all(c['gain']==0 for c in r['curve']))
 def test_fixed_projection_no_free_candidate(self):
  for mode in MODES:
   _,r=self.run_snapshot('(declare-const x Int)(assert (= x 0))',mode,'0\n','0\n',.08);self.assertEqual(r['gain'],0);self.assertFalse(r['curve'])
 def test_late_and_failed_extra_keep_timely_ordinary(self):
  p=dict(gain=0,observations=3,origin='plain',timely=True,formal_duplicate=False,new_to_supply=True)
  x=dict(gain=100,observations=100,origin='projection',timely=False,formal_duplicate=False)
  self.assertIs(best_candidate([p,x]),p);self.assertIs(best_candidate([p]),p)
  self.assertFalse(timely(dict(timely=True,completion=.101,deadline=.1),1))
  same=dict(p,origin='projection');self.assertIs(best_candidate([same,p]),p)
  self.assertIs(best_candidate([dict(same,gain=100,new_to_supply=False),p]),p)
  self.assertEqual(best_candidate([p,dict(same,gain=1)])['origin'],'projection')
 def test_first_observation_pair_and_independent_copies(self):
  f='(declare-const b Bool)(declare-const x Int)(assert (or b (>= x 0)))'
  def m(b,x):return [dict(name='b',sort='Bool',value=b),dict(name='x',sort='Int',value=str(x))]
  with no_search():
   metric=FrozenCoverage(f);base=metric.apply(metric.empty.copy(),m(True,0));saved=metric.export(base)
   one=metric.apply(base,m(False,0));two=metric.apply(base,m(False,1));both=metric.apply(one,m(False,1))
   self.assertEqual(metric.export(base),saved);self.assertGreater(metric.count(both),max(metric.count(one),metric.count(two)))
 def test_component_saved_models_and_frozen_state(self):
  from audit_projection_component import audit_job as audit_component
  binary=Path(os.environ.get('LIA_PROJECTION_COMPONENT_PROBE',self.binary.with_name('projection_component_probe')))
  f=self.d/'c.smt2';f.write_text('(declare-const x Int)(declare-const y Int)(assert (and (>= x 0)(<= x 100)(>= y 0)(<= y 100)))');pv=self.d/'p.values';pv.write_text('0 0\n');sv=self.d/'s.values';sv.write_text('1 1\n')
  for mode in ('plain_extra','projection_fixed'):
   out=self.d/mode;subprocess.run([str(binary),str(f),mode,'.3','0',str(out),str(pv),str(sv)],check=True,capture_output=True)
   initial=json.loads((out/'initial.json').read_text());r=dict(output=mode,hashes={},exitcode=0,watchdog=False,id=mode,instance='synthetic',mode=mode,seed=0,formula=str(f),prefix_values=str(pv),seed_values=str(sv),seconds=.3)
   a=audit_component((str(self.d),r,initial['source_sha256']));self.assertEqual(a['status'],'passed');self.assertGreater(a['summary']['sat'],0)
   if mode=='projection_fixed':self.assertEqual(a['summary']['sat'],a['summary']['blocks'])
 def test_decision_never_automatically_runs_longer(self):
  from analyze_projection_fix import PREFIXES
  rows=[dict(instance=c,seed=s,mode=m,gain=100,supply_per_second=100,projection_positive_strict_winners=0,**{f'prefix_{p}':100 for p in PREFIXES}) for c in ('a','b','c') for s in (0,1) for m in MODES]
  self.assertFalse(gate(rows)['propose_30s_only'])
  for r in rows:
   if r['mode']=='projection_fixed' and r['instance'] in ('a','b'):r.update(gain=200,projection_positive_strict_winners=1)
  self.assertTrue(gate(rows)['propose_30s_only']);self.assertFalse(gate(rows)['enter_D'])
  for r in rows:
   if r['mode']=='projection_fixed' and r['instance']=='c':r['supply_per_second']=50
  self.assertFalse(gate(rows)['propose_30s_only'])
if __name__=='__main__':unittest.main()
