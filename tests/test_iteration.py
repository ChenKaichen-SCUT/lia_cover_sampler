from pathlib import Path
import json,sys,tempfile,unittest,subprocess
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from run_iteration import digest,write
import audit_iteration
from audit_experiments import sampler_metadata

class IterationEvidence(unittest.TestCase):
    def test_deadline_interrupted_summary_preserves_checkpoint_and_strict_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);summary=folder/'summary.json';summary.write_text('')
            write(folder/'progress.json',dict(samples=3,covered=12))
            killed=dict(status='timeout_partial',returncode=-9)
            metadata,partial,reason=sampler_metadata(folder,killed,True)
            self.assertEqual(metadata['samples'],3);self.assertTrue(partial)
            self.assertEqual(reason['reason'],'deadline_killed_during_summary_write')
            self.assertEqual(summary.read_bytes(),b'')
            with self.assertRaises(json.JSONDecodeError):sampler_metadata(folder,killed)
            with self.assertRaises(json.JSONDecodeError):sampler_metadata(folder,dict(status='ok',returncode=0),True)
            (folder/'progress.json').write_text('{')
            with self.assertRaises(json.JSONDecodeError):sampler_metadata(folder,killed,True)
            write(summary,dict(samples=5,covered=20))
            self.assertEqual(sampler_metadata(folder,killed,True),(dict(samples=5,covered=20),False,None))

    def test_audit_cache_invalidates_data_and_report_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);models=root/'models';models.write_text('original')
            job=dict(instance='fixture',seed=0,run='raw')
            report=root/'audit/fixture/seed-0.json';calls=[]
            expected=dict(counts={k:0 for k in ('invalid','unknown','malformed','duplicate','coverage_mismatch','after_deadline','timestamp_missing')})
            def perform(payload):
                calls.append(1);report.parent.mkdir(parents=True,exist_ok=True)
                write(report,expected);return dict(expected)
            def existing(payload):
                if report.exists():return json.loads(report.read_text())
                return perform(payload)
            with patch.object(audit_iteration,'bindings',side_effect=lambda *args:{'models':digest(models)}),patch.object(audit_iteration,'audit_job',side_effect=existing):
                audit_iteration.audit_one((str(root),job,1))
                self.assertTrue(audit_iteration.audit_one((str(root),job,1))[2]['cache_hit'])
                models.write_text('changed')
                self.assertFalse(audit_iteration.audit_one((str(root),job,1))[2]['cache_hit'])
                report.write_text('{"tampered":true}')
                self.assertFalse(audit_iteration.audit_one((str(root),job,1))[2]['cache_hit'])
                self.assertEqual(len(calls),3)
                self.assertTrue(list(report.parent.glob('*.stale-*')))

    def test_final_without_promotion_cannot_start_a_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);write(root/'configs.json',{'candidate':{'binary':sys.executable,'args':[]}})
            write(root/'prefix.json',dict(passed=True,code_path_stop_only=True,source_hashes={}))
            result=subprocess.run([sys.executable,str(ROOT/'scripts/run_iteration.py'),'--stage','final',
                '--configs',str(root/'configs.json'),'--prefix-audit',str(root/'prefix.json'),
                '--output',str(root/'must_not_exist')],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('promotion checklist required',result.stderr)
            self.assertFalse((root/'must_not_exist').exists())
