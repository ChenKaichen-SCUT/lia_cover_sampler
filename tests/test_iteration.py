from pathlib import Path
import json,sys,tempfile,unittest,subprocess
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from run_iteration import digest,write
import audit_iteration

class IterationEvidence(unittest.TestCase):
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
