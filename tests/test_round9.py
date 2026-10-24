import json,os,subprocess,tempfile,unittest,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from audit_round9 import audit
class Round9Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.d=Path(self.tmp.name)
  self.binary=Path(os.environ.get('LIA_MACRO_RETRY_PROBE',ROOT/'build/round9/release/macro_retry_probe'))
  if not self.binary.exists():self.skipTest('retry probe not built')
 def run_snapshot(self,text,mode,prefix,seed,seconds=.25):
  f=self.d/'f.smt2';f.write_text(text);pv=self.d/'prefix.values';pv.write_text(prefix);sv=self.d/'seed.values';sv.write_text(seed);out=self.d/mode
  subprocess.run([str(self.binary),str(f),mode,str(seconds),'0',str(out),str(pv),str(sv)],check=True,capture_output=True,timeout=20)
  initial=json.loads((out/'initial.json').read_text());rec=dict(output=mode,hashes={},exitcode=0,watchdog=False,id=mode,instance='synthetic',state='synthetic',mode=mode,seed=0,split='synthetic',kind='mechanism',stage='B',formula=str(f),seconds=seconds)
  r=audit((str(self.d),rec,initial['source_sha256']));self.assertEqual(r['status'],'passed');return out,r
 def test_transparent_fallback_and_full_state(self):
  for mode in ('plain_fixed','macro_budget_fixed','macro_fair_retry'):
   out,r=self.run_snapshot('(declare-const x Int)(assert (>= x 0))',mode,'0\n','1\n')
   self.assertFalse(r['summary']['macro_constructed']);self.assertEqual(r['summary']['macro_engine_seconds'],0)
   self.assertTrue(all(q['origin']=='exploration' for row in map(json.loads,(out/'rounds.jsonl').read_text().splitlines()) for q in row['broad_queries']))
 def test_grouped_negative_bigint_and_live_coverage(self):
  f='(declare-const x Int)(declare-const y Int)(assert (or (and (= x (- 18446744073709551617))(= y 0))(and (= x 18446744073709551617)(= y 7))(and (= x 2)(= y 3))))'
  for mode in ('plain_fixed','macro_budget_fixed','macro_fair_retry'):
   _,r=self.run_snapshot(f,mode,'-18446744073709551617 0\n','-18446744073709551617 0\n',.3)
   self.assertGreater(r['gain'],0);self.assertEqual(sum(x['gain'] for x in r['curve']),r['gain'])
 def test_seed_submitted_and_unsubmitted(self):
  f='(declare-const x Int)(assert (or (= x 0)(= x 1)(= x 2)))'
  _,a=self.run_snapshot(f,'plain_fixed','0\n','1\n');_,b=self.run_snapshot(f,'macro_fair_retry','0\n1\n','1\n')
  self.assertEqual(a['base_covered'],b['base_covered']);self.assertGreater(a['base_covered'],0)
 def test_no_initial_budget_no_free_model(self):
  f=self.d/'f.smt2';f.write_text('(declare-const x Int)(assert (= x 0))');out=self.d/'none'
  subprocess.run([str(self.binary),str(f),'micro_macro','0.000001','0',str(out)],check=True,capture_output=True)
  s=json.loads((out/'summary.json').read_text());self.assertEqual(s['formal_count'],0);self.assertFalse(s['macro_constructed'])
 def test_gate_requires_repeatable_macro_gain(self):
  from analyze_round9 import gate
  rows=[dict(state=str(state),seed=seed,mode=mode,gain=0,macro_positive_outputs=0) for state in (1,10) for seed in (0,1,2) for mode in ('plain_fixed','macro_budget_fixed','macro_fair_retry')]
  self.assertFalse(gate(rows)['enter_C'])
  for r in rows:
   if r['mode']=='macro_fair_retry' and r['seed']==0:r.update(gain=100,macro_positive_outputs=1)
  self.assertFalse(gate(rows)['enter_C']) # two snapshots are not two seeds
  for r in rows:
   if r['mode']=='macro_fair_retry' and r['seed']==1:r.update(gain=1,macro_positive_outputs=1)
  self.assertTrue(gate(rows)['enter_C'])
 def test_independent_diagnostic_semantics(self):
  binary=Path(os.environ.get('LIA_MACRO_DIAGNOSTIC_PROBE',self.binary.with_name('macro_diagnostic_probe')))
  f=self.d/'diag.smt2';f.write_text('(declare-const x Int)(declare-const y Int)(assert (and (= (+ x y) 10)(or (= x 0)(= x 10))))');out=self.d/'diag'
  subprocess.run([str(binary),str(f),'0','1','50','0',str(out)],check=True,capture_output=True)
  s=json.loads((out/'summary.json').read_text());rec=dict(output='diag',hashes={},exitcode=0,watchdog=False,id='diag',stage='A',group=0,alternative=1,group_node=s['group_node'],alternative_node=s['alternative_node'],timeout_ms=50,formula=str(f))
  result=audit((str(self.d),rec,s['source_sha256']));self.assertEqual(result['validated_models'],1)
if __name__=='__main__':unittest.main()
