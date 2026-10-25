import json,os,subprocess,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BIN=Path(os.environ.get('LIA_BOFILL_FIRST_SAT_PROBE',ROOT/'build/bofill_diagnosis/release/bofill_first_sat_probe'))
class FirstSat(unittest.TestCase):
    def run_probe(self,text,mode,control,seconds=.4,expired=False):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);d=Path(temp.name);(d/'f.smt2').write_text(text)
        env=dict(os.environ)
        if expired:env['BOFILL_START_MONO_NS']=str(time.monotonic_ns()-10**9)
        p=subprocess.run([str(BIN),str(d/'f.smt2'),mode,control,str(seconds),'0',str(d/'out')],env=env,capture_output=True,text=True,timeout=5)
        self.assertEqual(p.returncode,0,p.stderr);return d/'out'
    def test_all_mode_and_controller_pairs(self):
        for mode in ['plain','simplify_only','eq_only','choice_only','full']:
            for control in ['repeated_2s','continuous']:
                d=self.run_probe('(declare-const x Int)(assert (or (= x 0)(= x 2)))',mode,control)
                summary=json.loads((d/'summary.json').read_text());self.assertEqual(summary['terminal'],'SAT')
                r=json.loads((d/'receipt.json').read_text());self.assertTrue(r['timely']);self.assertLess(r['completion'],.4)
                q=json.loads((d/'queries.jsonl').read_text());self.assertTrue(q['statistics']);self.assertGreaterEqual(q['statistics_read_at'],r['completion'])
                self.assertLessEqual(q['evidence']['granted_timeout_ms'],400)
    def test_expired_session_never_checks(self):
        for control in ['repeated_2s','continuous']:
            d=self.run_probe('(assert true)','plain',control,expired=True);self.assertEqual((d/'queries.jsonl').read_text(),'');self.assertEqual(json.loads((d/'summary.json').read_text())['terminal'],'NO_SAT_WITHIN_BUDGET')
    def test_proved_unsat_separate_from_timeout(self):
        d=self.run_probe('(assert false)','full','continuous');self.assertEqual(json.loads((d/'summary.json').read_text())['terminal'],'UNSAT');self.assertFalse((d/'candidate.json').exists())
if __name__=='__main__':unittest.main()
