"""Pure-data paired accounting checks; no development experiment is launched."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_coverage_block_pairs import analyze

def fixture(positive_cases):
    calls=[];sessions=[]
    for snapshot in range(6):
        case=snapshot//2
        for seed in range(3):
            for repeat in range(2):
                for mode in ('global','block'):
                    improved=mode=='block' and case in positive_cases
                    gain=2 if improved else 1
                    sessions.append(dict(mode=mode,audited=True,formula_initialization_seconds=.01,snapshot_restore_seconds=.01,
                        engine_initialization_seconds=.01,initialization_seconds=.03,session_seconds=.09,
                        process_wall_seconds=.1,cpu_seconds=.09,score_per_session_second=3*gain/.09,score_per_process_second=3*gain/.1))
                    for action in range(3):calls.append(dict(snapshot=str(snapshot),instance=str(case),seed=seed,repeat=repeat,action=action,mode=mode,
                        audited=True,primary='same target',gain=gain,full_seconds=.02,initialized_action_seconds=.03,
                        positive=True,delivered=True,action_efficiency=gain/.02,initialized_action_efficiency=gain/.03))
    return calls,sessions

class BlockPairAccounting(unittest.TestCase):
    def test_repeats_and_all_calls_preserved(self):
        c,s=fixture({0,1,2});r=analyze(c,s)
        self.assertEqual(len(r['pairs']),108);self.assertEqual(r['units'],54)
        self.assertEqual(r['mean_gain_delta'],1);self.assertTrue(r['recommend_continue'])
        c[0]['audited']=False;r=analyze(c,s)
        self.assertEqual(r['units'],54);self.assertEqual(r['valid_units'],53);self.assertFalse(r['recommend_continue'])

    def test_one_case_and_zero_results_do_not_promote(self):
        c,s=fixture({0});self.assertFalse(analyze(c,s)['recommend_continue'])
        for row in c:
            row.update(gain=0,positive=False,delivered=False,action_efficiency=0,initialized_action_efficiency=0)
        for row in s:row.update(score_per_session_second=0,score_per_process_second=0)
        r=analyze(c,s);self.assertFalse(r['recommend_continue'])
        self.assertEqual([m['no_candidate'] for m in r['modes']],[108,108])
        self.assertEqual(r['same_positive_coverage_units'],0)

    def test_mismatched_primary_invalidates_comparison(self):
        c,s=fixture({0,1,2});c[0]['primary']='different target'
        r=analyze(c,s);self.assertFalse(r['recommend_continue']);self.assertEqual(r['valid_units'],53)

if __name__=='__main__':unittest.main()
