from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_experiments import audit_job

class AuditPaths(unittest.TestCase):
    def test_missing_raw_directory_is_not_a_valid_zero_sample_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            job = dict(instance="unused.smt2", seed=0, run="not_created/../../raw")
            with self.assertRaisesRegex(ValueError, "missing raw run folder"):
                audit_job((temporary, job, "full", 60, False))
