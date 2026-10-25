import json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from audit_bofill_diagnosis import structure,affine
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search
class DiagnosisAudit(unittest.TestCase):
    def test_explicit_context_and_recovery_reference_accounting(self):
        with tempfile.TemporaryDirectory() as td,no_search() as calls:
            p=Path(td)/'f.smt2';p.write_text('(declare-const q Int)(declare-const y Int)(assert (and (>= q 0)(<= q 1)(= y (+ q 3))))')
            original=FrozenCoverage('(declare-const x Int)(declare-const y Int)(assert (and (or (= x 0)(= x 2))(= y (+ x 3))))')
            r=structure(p,original.root,[dict(original='x',expression='(* 2 q)'),dict(original='y',expression='y')])
            self.assertEqual(r['auxiliary_variables'],['q']);self.assertEqual(r['remaining_pivot_reasons']['eligible_pivot_occurrences'],1);self.assertEqual(len(r['remaining_eligible_pivots']),1)
            self.assertEqual(r['remaining_eligible_pivots'][0]['variable'],'y');self.assertFalse(calls)
    def test_structural_analysis_preserves_explicit_or(self):
        with tempfile.TemporaryDirectory() as td,no_search() as calls:
            text='(declare-const x Int)(assert (or (= x (- 18446744073709551617))(= x 18446744073709551617)))';p=Path(td)/'f.smt2';p.write_text(text);m=FrozenCoverage(text)
            r=structure(p,m.root,[dict(original='x',expression='x')]);self.assertEqual(r['or_count'],1);self.assertEqual(r['necessary_equality_graph'],[]);self.assertFalse(calls)
if __name__=='__main__':unittest.main()
