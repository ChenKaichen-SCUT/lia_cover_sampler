import csv,unittest,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from analyze_plateaus import diagnose
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search
class AnalysisTests(unittest.TestCase):
    def test_unobserved_pair_is_not_single_model_gap(self):
        self.assertEqual(diagnose([[0,0]],[[1,1]],{})['reachability'],'REACHABILITY_UNKNOWN')
    def test_opposite_observation_has_exact_saved_witness(self):
        w={0:[dict(mode='saved',index=7,model_hash='hash',zero='0',one='4')]};r=diagnose([[4,0]],[[4,4]],w)
        self.assertEqual(r['known_missing_bits'],1);self.assertEqual((r['witness_bit'],r['witness_value']),(2,1));self.assertEqual(r['witness_index'],7)
    def test_no_known_gap_is_unknown_not_saturated(self):
        self.assertEqual(diagnose([[1,1]],[[1,1]],{})['reachability'],'REACHABILITY_UNKNOWN')
    def test_saved_early_partition_identity(self):
        p=ROOT/'results/round8_analysis/early_samples.csv'
        if not p.exists():self.skipTest('saved analysis unavailable')
        for r in csv.DictReader(p.open()):
            gain=int(r['gain']);self.assertEqual(gain,sum(int(r[k]) for k in ('boolean','integer_variable','integer_constant','arithmetic_expression')))
            self.assertEqual(gain,int(r['boolean'])+sum(int(r[k]) for k in ('low','mid','high')))
            self.assertEqual(int(r['new_observations']),int(r['first_observations'])+gain)
if __name__=='__main__':unittest.main()
