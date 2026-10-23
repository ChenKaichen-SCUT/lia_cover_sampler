"""Cold-budget and observation-preserving score contracts for mechanism replay."""
import json,os,subprocess,tempfile,unittest
from pathlib import Path
from test_suite import ROOT
from validation import original_formula,verify
from coverage_differential_test import load_metric

PROBE=Path(os.environ.get('LIA_PREPARATION_PROBE',ROOT/'build/round5/release/preparation_probe'))
FROZEN=PROBE.with_name('preparation_probe_frozen')

class PreparationProbeContract(unittest.TestCase):
    def invoke(self,folder,mode,budget,seed=0,binary=PROBE,label='run'):
        formula=folder/'input.smt2'
        if not formula.exists():
            formula.write_text(''.join(f'(declare-const x{i} Int)(assert (and (<= -2 x{i})(<= x{i} 2)))' for i in range(6)))
            (folder/'prefix.values').write_text('-2 -2 -2 -2 -2 -2\n')
            (folder/'seed.values').write_text('0 0 0 0 0 0\n')
        out=folder/(label+'.json')
        r=subprocess.run([str(binary),str(formula),str(folder/'prefix.values'),str(folder/'seed.values'),mode,str(budget),str(seed),str(out)],capture_output=True,text=True,timeout=20)
        self.assertEqual(r.returncode,0,r.stderr)
        return json.loads(out.read_text())

    def test_instrumented_old_matches_frozen_operation_path(self):
        self.assertTrue(FROZEN.exists(),'frozen regression binary required in this environment')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            for seed in (0,1,2):
                a=self.invoke(p,'old',5,seed,FROZEN,f'frozen-{seed}')
                b=self.invoke(p,'old',5,seed,PROBE,f'old-{seed}')
                for key in ('input_gain','proposed_gain','proposed_candidate','valid_candidates','internal_moves','attempted_steps'):
                    self.assertEqual(a[key],b[key],key)

    def test_initialization_and_late_fallback_are_in_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            for mode in ('old','reuse'):
                r=self.invoke(Path(tmp),mode,1e-9,label=mode)
                self.assertTrue(r['setup_exhausted'])
                self.assertFalse(r['completed_in_budget'])
                self.assertEqual(r['valid_candidates'],0)
                self.assertEqual(r['accepted_extra_coverage'],0)
                self.assertEqual(r['extra_per_full_second'],0)
                self.assertEqual(r['candidate'],r['input_model'])
                self.assertGreaterEqual(r['full_seconds'],r['setup_seconds'])
                self.assertGreater(r['formula_initialization_seconds'],0)

    def test_gain_subtracts_input_and_retains_original_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            for mode in ('old','reuse'):
                r=self.invoke(p,mode,5,label=mode)
                metric=load_metric();stat=metric.WireCoverageStatistics();ev=metric.ManualSatisfiesMetric((p/'input.smt2').read_text(),stat)
                self.assertTrue(ev.count_sample([(f'x{i}',-2) for i in range(6)]))
                before=stat._storage.copy();covered=lambda:sum(stat.node_count(*s) for s in stat._storage.values())
                base=covered()
                def sample(rows):return [(x['name'],int(x['value'])) for x in rows]
                self.assertTrue(ev.count_sample(sample(r['input_model'])))
                raw=stat._storage.copy();raw_gain=covered()-base
                stat._storage=before.copy();self.assertTrue(ev.count_sample(sample(r['candidate'])))
                gain=covered()-base
                self.assertGreater(raw_gain,0)
                self.assertEqual(r['input_gain'],raw_gain)
                self.assertEqual(r['accepted_extra_coverage'],gain-raw_gain)
                for old,new in zip(raw.values(),stat._storage.values()):
                    self.assertEqual(int(old[0])&~int(new[0]),0)
                    self.assertEqual(int(old[1])&~int(new[1]),0)
                self.assertEqual(verify(original_formula(p/'input.smt2'),r['candidate']),'valid')
                self.assertEqual(r['valid_candidates'],r['statistics']['counts'].get('unique_candidates_scored',0))

if __name__=='__main__':unittest.main()
