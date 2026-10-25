import json,os,subprocess,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BIN=Path(os.environ.get('LIA_SOFT_PHASE_BINARY',ROOT/'build/soft_phase/release/soft_phase_sampler'))
@unittest.skipUnless(BIN.exists(),'optional soft-phase binary not configured')
class SoftPhase(unittest.TestCase):
    def run_mode(self,text,mode,seconds=2,expired=False):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);d=Path(tmp.name);f=d/'input.smt2';f.write_text(text)
        env=dict(os.environ)
        if expired:env['BOFILL_START_MONO_NS']=str(time.monotonic_ns()-10**9)
        p=subprocess.run([str(BIN),str(f),mode,str(seconds),'0',str(d/'out')],env=env,capture_output=True,text=True,timeout=10)
        self.assertEqual(p.returncode,0,p.stderr)
        return d/'out',f
    def test_finite_all_models_full_state_and_first_plain(self):
        text='(declare-const x Int)(declare-const y Int)(assert (or (and (= x 0)(= y 0)) (and (= x 7)(= y 7))))'
        identities=[]
        from test_bofill import Bofill
        for mode in ['plain','random_soft','coverage_soft']:
            out,f=self.run_mode(text,mode);Bofill.audit(self,out,text)
            qs=list(map(json.loads,(out/'queries.jsonl').read_text().splitlines()));ms=list(map(json.loads,(out/'models.jsonl').read_text().splitlines()))
            self.assertEqual(len(ms),2);self.assertEqual(qs[0]['preferences'],[]);self.assertEqual(qs[0]['hook'],{})
            self.assertEqual(json.loads((out/'summary.json').read_text())['terminal'],'exhausted_full_original_models')
            identities.append({json.dumps(m['model'],sort_keys=True) for m in ms})
            if mode!='plain':
                self.assertTrue((out/'summaries.json').exists());self.assertTrue(all(q['hook'].get('applied',0)<=8 for q in qs))
        self.assertEqual(identities[0],identities[1]);self.assertEqual(identities[0],identities[2])
    def test_independent_conditional_audit(self):
        import shutil
        from reevaluate_round6_saved_candidates import dump,file_hash
        from audit_soft_phase_results import one_run
        text='(declare-const x Int)(declare-const y Int)(assert (and (or (and (= x 0)(= y 0)) (and (= x 7)(= y 7))) (or true (= (+ x 42) 9))))'
        for mode in ['random_soft','coverage_soft']:
            raw,f=self.run_mode(text,mode);out=raw.parent/'experiment';run=out/'runs'/'case';run.mkdir(parents=True);(out/'inputs').mkdir();shutil.copyfile(f,out/'inputs'/f.name);shutil.move(raw,run/'raw')
            dump(out/'plan.json',dict(inputs=[dict(name=f.name,sha256=file_hash(f))]))
            dump(run/'spec.json',dict(configuration=mode,formula=f.name,seed=0,group=0,seconds=2))
            dump(run/'process_result.json',dict(process_wall_seconds=2,cleanup_after_deadline_seconds=0,returncode=0,termination_sent_seconds=None))
            result=one_run((str(out),'case'));self.assertEqual(result['distinct'],2);self.assertGreater(result['subset_checks'],0)
    def test_expired_budget_never_checks(self):
        for mode in ['plain','random_soft','coverage_soft']:
            out,f=self.run_mode('(assert true)',mode,.02,True)
            self.assertEqual((out/'queries.jsonl').read_text(),'');self.assertFalse((out/'summaries.json').exists())
    def test_default_mode_is_plain(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);(d/'f').write_text('(assert true)')
            p=subprocess.run([str(BIN),str(d/'f'),'1','0',str(d/'out')],capture_output=True,text=True,timeout=5)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(json.loads((d/'out/initial.json').read_text())['mode'],'plain')
    def test_unsat_has_no_summaries(self):
        out,f=self.run_mode('(assert false)','coverage_soft')
        self.assertEqual(json.loads((out/'summary.json').read_text())['formal_count'],0)
        self.assertFalse((out/'summaries.json').exists())
    def test_negative_large_values_and_shared_short_circuit(self):
        from test_bofill import Bofill
        text='(declare-const x Int)(assert (and (or (= x (- 18446744073709551617))(= x 18446744073709551618)) (or true (= (+ x 42) 9)) (= (+ x x) (* 2 x))))'
        for mode in ['random_soft','coverage_soft']:
            out,f=self.run_mode(text,mode);Bofill.audit(self,out,text)
            summary=json.loads((out/'summaries.json').read_text())
            self.assertEqual(len(summary['atoms']),1)
if __name__=='__main__':unittest.main()
