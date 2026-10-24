import json,os,subprocess,tempfile,unittest
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,model_key
class MacroOnlineTests(unittest.TestCase):
    def run_probe(self,formula,mode,seconds=.3):
        binary=Path(os.environ.get('LIA_MACRO_ONLINE_PROBE',ROOT/'build/round8/release/macro_online_probe'))
        if not binary.exists():self.skipTest('round8 binary unavailable')
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);d=Path(tmp.name);f=d/'input.smt2';f.write_text(formula);out=d/'run'
        subprocess.run([str(binary),str(f),mode,str(seconds),'0',str(out)],check=True,capture_output=True,timeout=30)
        return out
    def check_output(self,out,formula):
        summary=json.loads((out/'summary.json').read_text());receipts={r['id']:r for r in map(json.loads,(out/'commits.jsonl').read_text().splitlines())}
        with no_search():
            metric=FrozenCoverage(formula);state=metric.empty;seen=set();gains=0;count=0
            for m in map(json.loads,(out/'models.jsonl').read_text().splitlines()):
                r=receipts[m['id']]
                if not r['timely']:continue
                self.assertLessEqual(r['completion'],r['deadline']);self.assertNotIn(model_key(m['model']),seen);seen.add(model_key(m['model']))
                new=metric.apply(state,m['model']);self.assertEqual(metric.count(new)-metric.count(state),m['gain']);gains+=m['gain'];state=new;count+=1
            self.assertEqual(metric.export(state),summary['coverage']);self.assertEqual(count,summary['formal_count']);self.assertEqual(gains,summary['gain'])
        return summary
    def test_both_modules_even_with_positive_micro(self):
        f='(declare-const x Int)(assert (and (<= (- 100) x)(<= x 100)))'
        for mode in ('micro_plain','micro_macro'):
            out=self.run_probe(f,mode,.8);self.check_output(out,f)
            rounds=list(map(json.loads,(out/'rounds.jsonl').read_text().splitlines()))
            positive=[r for r in rounds if r['micro_best_gain'] and r['full_seconds']<r['budget']]
            self.assertTrue(positive);self.assertTrue(all(r['broad_calls']>0 for r in positive))
            self.assertTrue(all(r['broad_calls']<=2 and r['micro_calls']<=3 for r in rounds))
    def test_stall_does_not_stop_at_eight_and_no_or_fallback(self):
        f='(declare-const x Int)(assert (= x 0))'
        for mode in ('micro_plain','micro_macro'):
            out=self.run_probe(f,mode,.4);s=self.check_output(out,f);self.assertGreaterEqual(s['stall_events'],1);self.assertGreater(s['rounds'],8);self.assertLessEqual(s['rebuilds'],2);self.assertNotEqual(s['terminal'],'eight_rounds_without_coverage')
            self.assertEqual(s['formal_count'],1);self.assertEqual(s['gain'],0)
    def test_grouped_and_negative_large_boolean_differential(self):
        f='(declare-const b Bool)(declare-const x Int)(declare-const y Int)(assert (and (= b true)(or (and (= x (- 18446744073709551617))(= y 0))(and (= x 18446744073709551617)(= y 7)))))'
        out=self.run_probe(f,'micro_macro',.7);self.check_output(out,f)
        self.assertEqual(len(json.loads((out/'catalog.json').read_text())['groups']),1)
    def test_independent_checkpoint_and_competition_audit(self):
        from audit_macro_pairs import audit_job
        f="(declare-const x Int)(declare-const y Int)(assert (and (= (+ x y) 10)(or (= x 0)(= x 10))))"
        for mode in ("micro_plain","micro_macro"):
            out=self.run_probe(f,mode,.6); initial=json.loads((out/"initial.json").read_text())
            record=dict(output="run",hashes={},exitcode=0,watchdog=False,id=mode,instance="synthetic",state="synthetic",mode=mode,seed=0,split="synthetic",kind="online",formula=str(out.parent/"input.smt2"),seconds=.6)
            result=audit_job((str(out.parent),record,initial["source_sha256"]));self.assertEqual(result["status"],"passed")
    def test_no_model_budget_is_preserved(self):
        f='(declare-const x Int)(assert (= x 0))';out=self.run_probe(f,'micro_macro',.000001);s=json.loads((out/'summary.json').read_text());self.assertEqual(s['formal_count'],0);self.assertEqual(s['gain'],0);self.assertEqual(s['terminal'],'no_initial_model')
if __name__=='__main__':unittest.main()
