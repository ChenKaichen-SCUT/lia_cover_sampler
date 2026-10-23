"""Regression: a deadline can interrupt a formal JSONL write after its raw model."""
import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_ab_states import records

class InterruptedRecords(unittest.TestCase):
    def test_deadline_tail_never_becomes_a_model(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'models.jsonl'
            path.write_bytes(b'{"index":0}\n{"index":1,"assignment":[')
            self.assertEqual(records(path,True),([{'index':0}],1))
            with self.assertRaises(ValueError):records(path,False)
    def test_complete_malformed_line_is_not_hidden(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'models.jsonl';path.write_text('{"index":0}\nnot-json\n')
            with self.assertRaises(json.JSONDecodeError):records(path,True)
    def test_complete_records_need_no_interruption(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'models.jsonl';path.write_text('{"index":0}\n{"index":1}\n')
            self.assertEqual(records(path,False),([{'index':0},{'index':1}],0))
