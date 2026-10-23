import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

PATH=Path(__file__).resolve().parents[1]/'scripts/decompose_round4.py'
spec=importlib.util.spec_from_file_location('decompose_round4',PATH)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class DecompositionTests(unittest.TestCase):
    def test_dedup_uses_actual_raw_prefix_and_distinct_comparators(self):
        raw=[dict(covered=c,seconds=i) for i,c in enumerate([2,3,4])]
        formal=[dict(covered=3,seconds=0,raw_generator_index=0),dict(covered=6,seconds=3,raw_generator_index=2)]
        reference=[dict(covered=c,seconds=i) for i,c in enumerate([1,2,5])]
        row=m.prefix_rows(formal,raw,reference,10)[1]
        self.assertEqual((row['k'],row['raw_prefix'],row['skipped_raw_before_prefix']),(2,3,1))
        self.assertEqual((row['direct_bits'],row['sequence_bits'],row['total_bits_delta']),(2,2,4))
        self.assertEqual((row['same_raw_count_sequence_bits'],row['same_raw_count_total_bits']),(-1,1))

    def test_missing_reference_is_not_zero_or_extrapolated(self):
        raw=[dict(covered=2,seconds=0),dict(covered=3,seconds=1)]
        formal=[dict(covered=2,seconds=0,raw_generator_index=0),dict(covered=4,seconds=1,raw_generator_index=1)]
        row=m.prefix_rows(formal,raw,[dict(covered=1,seconds=0)],10)[1]
        self.assertFalse(row['reference_k_available']);self.assertEqual(row['direct_bits'],1)
        self.assertIsNone(row['sequence_bits']);self.assertIsNone(row['total_bits_delta'])
        self.assertFalse(row['identity_verified'])

    def test_truncated_tail_preserved_and_not_parsed(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'records.jsonl';p.write_bytes(b'{"x":1}\n{"x":2')
            rows,tail=m.read_records(p)
            self.assertEqual(rows,[dict(x=1)]);self.assertEqual(tail['incomplete_lines'],1)
            self.assertEqual(tail['incomplete_bytes'],6);self.assertEqual(tail['byte_offset'],8)
            self.assertEqual(len(tail['incomplete_sha256']),64)
            p.write_bytes(b'{"x":1}\n{bad}\n')
            with self.assertRaises(json.JSONDecodeError):m.read_records(p)

    def test_valid_json_without_newline_is_still_uncommitted(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'records.jsonl';p.write_bytes(b'{"x":1}')
            rows,tail=m.read_records(p)
            self.assertEqual(rows,[]);self.assertEqual(tail['incomplete_lines'],1)

    def test_integer_identity_not_rounded_percent_arithmetic(self):
        row=m.telescope(100000000000000003,100000000000000001,99999999999999999,100000000000000010)
        self.assertEqual((row['direct_bits'],row['sequence_bits'],row['total_bits_delta']),(2,2,4))
        self.assertTrue(row['identity_verified'])
        row=m.telescope(7,5,9,13)
        self.assertEqual((row['direct_bits'],row['sequence_bits'],row['total_bits_delta']),(2,-4,-2))

    def test_time_alignment_excludes_unemitted_late_raw(self):
        raw=[dict(covered=1,seconds=0),dict(covered=5,seconds=4),dict(covered=9,seconds=4.9)]
        formal=[dict(covered=2,seconds=1,raw_generator_index=0,transaction_complete=True),dict(covered=6,seconds=5.1,raw_generator_index=1,transaction_complete=False)]
        ref=[dict(covered=1,seconds=0),dict(covered=3,seconds=4.8)]
        row=m.time_rows(formal,raw,ref,10,5)[0]
        self.assertEqual((row['formal_prefix'],row['raw_prefix'],row['reference_prefix']),(1,1,2))
        self.assertEqual(row['raw_models_generated_by_time'],3)
        self.assertEqual((row['direct_bits'],row['sequence_bits'],row['total_bits_delta']),(1,-2,-1))

    def test_unknown_raw_reference_fails(self):
        with self.assertRaises(ValueError):
            m.prefix_rows([dict(raw_generator_index=1,covered=4,seconds=1)],[],[],10)


if __name__=='__main__':unittest.main()
