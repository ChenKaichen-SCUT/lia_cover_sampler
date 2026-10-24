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
    def test_zero_gain_seed_creates_known_gap(self):
        with no_search():
            metric=FrozenCoverage('(declare-const x Int)(assert (and (<= 0 x)(<= x 1)))')
            zero=[dict(name='x',sort='Int',value='0')];one=[dict(name='x',sort='Int',value='1')]
            seeded=metric.apply(metric.empty,zero);reference=metric.apply(seeded,one)
            self.assertEqual(metric.count(seeded),0)
            def export(s):return [list(map(int,x)) for x in metric.export(s)['states']]
            refs=export(reference);w={i:[dict(mode='saved',index=2,model_hash='one',zero=str(z),one=str(o))] for i,(z,o) in enumerate(refs)}
            self.assertEqual(diagnose(export(metric.empty),refs,w)['reachability'],'REACHABILITY_UNKNOWN')
            self.assertEqual(diagnose(export(seeded),refs,w)['reachability'],'PLATEAU_WITH_KNOWN_REACHABLE_GAP')
    def test_saved_early_partition_identity(self):
        p=ROOT/'results/round8_analysis/early_samples.csv'
        if not p.exists():self.skipTest('saved analysis unavailable')
        for r in csv.DictReader(p.open()):
            gain=int(r['gain']);self.assertEqual(gain,sum(int(r[k]) for k in ('boolean','integer_variable','integer_constant','arithmetic_expression')))
            self.assertEqual(gain,int(r['boolean'])+sum(int(r[k]) for k in ('low','mid','high')))
            self.assertEqual(int(r['new_observations']),int(r['first_observations'])+gain)
if __name__=='__main__':unittest.main()
