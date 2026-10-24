"""Freeze promotion logic before measuring the development snapshots."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from analyze_residual_online import analyze
from reevaluate_round6_saved_candidates import dump,file_hash

class ResidualAnalysisGates(unittest.TestCase):
    def fixture(self,p,only_one_case=False,regress_early=False):
        snapshots=[dict(id=f'{c}/prefix-{prefix}',instance=c,prefix=prefix) for c in ('a','b','c') for prefix in (1,10)]
        plan=dict(snapshots=snapshots,seeds=[0,1,2],workers=15,implementation_commit='test',source_sha256='test',frozen_base='b2f3047')
        dump(p/'plan.json',plan);(p/'runs.jsonl').write_text('{}\n');dump(p/'COMPLETE.json',dict(all_passed=True))
        sessions=[]
        for s in snapshots:
            for seed in (0,1,2):
                for mode in ('global_pack','residual_cardinality'):
                    better=mode=='residual_cardinality' and (not only_one_case or s['instance']=='a')
                    outputs=[]
                    for k in range(1,3):
                        value=k*(2 if better else 1)
                        if regress_early and mode=='residual_cardinality':value=k*.5
                        outputs.append(dict(output_index=k,actual_marginal_gain=value/k,cumulative_gain=value,cumulative_gain_pp=value,completion_timestamp=.1*k))
                    sessions.append(dict(session=f'{s["id"]}|{seed}|{mode}',snapshot=s['id'],instance=s['instance'],prefix=s['prefix'],seed=seed,mode=mode,
                        passed=True,run_success=True,actual_gain=4 if better else 2,gain_pp=4 if better else 2,outputs=outputs,rounds=[],queries=[],costs={},
                        full_session_seconds=1,process_wall_seconds=1.1,initialization_seconds=.01,total=100,s0_covered=0,terminal='test'))
        dump(p/'audit/sessions.json',sessions)
        dump(p/'audit/COMPLETE.json',dict(all_passed=True,plan_sha256=file_hash(p/'plan.json'),runs_sha256=file_hash(p/'runs.jsonl'),
            files={'sessions.json':file_hash(p/'audit/sessions.json')}))

    def test_two_cases_and_common_prefix_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);self.fixture(p,only_one_case=True)
            s=analyze(p,p/'analysis');self.assertFalse(s['recommend_continue']);self.assertFalse(s['gates']['two_cases_improved'])
            self.assertEqual(s['early_prefixes'][2]['n'],0)

    def test_endpoint_win_does_not_hide_early_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);self.fixture(p,regress_early=True)
            s=analyze(p,p/'analysis');self.assertFalse(s['recommend_continue']);self.assertTrue(s['gates']['two_cases_improved'])
            self.assertFalse(s['gates']['no_systematic_early_regression'])

    def test_all_frozen_conditions_allow_only_recommendation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);self.fixture(p)
            s=analyze(p,p/'analysis');self.assertTrue(s['recommend_continue']);self.assertTrue(s['no_followup_started'])
            self.assertEqual(s['endpoint_paired']['n'],18)
            self.assertEqual(len((p/'analysis/sessions.csv').read_text().splitlines()),37)

if __name__=='__main__':unittest.main()
