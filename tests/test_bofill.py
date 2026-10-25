import json, os, signal, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from process_ready import wait_for_complete_line,cleanup_process
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from audit_bofill_results import fingerprint, complete_lines, typed_highdiv, audit_run
from reevaluate_round6_saved_candidates import FrozenCoverage, no_search, model_id
BIN=Path(os.environ.get('LIA_BOFILL_BINARY',ROOT/'build/bofill/release/bofill_sampler'))
class Bofill(unittest.TestCase):
    def run_case(self,text,mode='choice_compiled',seconds=2,env=None):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);p=Path(tmp.name);(p/'f.smt2').write_text(text)
        q=subprocess.run([str(BIN),str(p/'f.smt2'),mode,str(seconds),'1',str(p/'out')],capture_output=True,text=True,timeout=12,env=env)
        self.assertEqual(q.returncode,0,q.stderr);return p/'out'
    def audit(self,d,text):
        with no_search():
            m=FrozenCoverage(text);s=m.empty.copy();ids=set();receipts={r['query']:r for r in map(json.loads,(d/'commits.jsonl').read_text().splitlines())}
            for row in map(json.loads,(d/'models.jsonl').read_text().splitlines()):
                self.assertTrue(receipts[row['query']]['timely']);key=model_id(row['model']);self.assertNotIn(key,ids);ids.add(key)
                previous=m.count(s);s=m.apply(s,row['model']);exported=m.export(s)
                self.assertEqual(row['gain'],m.count(s)-previous);self.assertEqual(row['state_fnv1a64'],fingerprint(exported['states']))
            summary=json.loads((d/'summary.json').read_text());self.assertEqual(summary['coverage'],m.export(s));self.assertEqual(summary['formal_count'],len(ids));return summary
    def test_complete_enumeration_both_modes_original_coverage(self):
        text='(declare-const x Int)(declare-const y Int)(declare-const b Bool)(assert (and (or (= x 0)(= x 18446744073709551617))(>= y (- 1))(<= y 1)(or b (= y 0))))'
        for mode in ['plain','simplify_only','eq_only','choice_only','full','choice_compiled','full_fallback']:
            s=self.audit(self.run_case(text,mode),text);self.assertEqual(s['terminal'],'exhausted_full_original_models');self.assertEqual(s['formal_count'],8)
    def test_empty_model(self):
        s=self.audit(self.run_case('(assert true)'),'(assert true)');self.assertEqual(s['formal_count'],1)
    def test_unsat(self):
        s=self.audit(self.run_case('(assert false)'),'(assert false)');self.assertEqual(s['formal_count'],0)
    def test_bounded_time_no_sample_cap(self):
        text='(declare-const x Int)(assert (>= x 0))';s=self.audit(self.run_case(text,seconds=.15),text);self.assertEqual(s['terminal'],'deadline');self.assertGreater(s['formal_count'],0)
    def test_expired_parent_no_check(self):
        env=dict(os.environ,BOFILL_START_MONO_NS=str(time.monotonic_ns()-10**9));d=self.run_case('(declare-const x Int)(assert (>= x 0))',seconds=.01,env=env)
        self.assertEqual((d/'queries.jsonl').read_text(),'');self.assertEqual(json.loads((d/'summary.json').read_text())['formal_count'],0)
    def test_external_cancel_retains_timely_models(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);text='(declare-const x Int)(assert (>= x 0))';(p/'f').write_text(text)
            q=subprocess.Popen([str(BIN),str(p/'f'),'plain','10','0',str(p/'out')],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            self.addCleanup(cleanup_process,q)
            wait_for_complete_line(q,p/'out/commits.jsonl')
            q.send_signal(signal.SIGTERM);stdout,stderr=q.communicate(timeout=10);self.assertEqual(q.returncode,0,stderr)
            self.assertEqual(self.audit(p/'out',text)['terminal'],'external_cancel')
    def test_highdiv_exact_parser_and_partial_line(self):
        i,m=typed_highdiv('0: x:-18446744073709551617;b:true;\n'.replace(': x',':x'),[('x','Int'),('b','Bool')]);self.assertEqual(i,0);self.assertEqual(m[1]['value'],'-18446744073709551617')
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'x';p.write_bytes(b'good\npartial');rows,issues=complete_lines(p);self.assertEqual(len(rows),1);self.assertEqual(issues[0]['kind'],'cutoff_tail')
    def test_missing_assignment_rejected(self):
        with self.assertRaises(ValueError):typed_highdiv('1:x:1;', [('x','Int'),('y','Int')])
    def test_audit_excludes_late_missing_and_duplicates(self):
        from reevaluate_round6_saved_candidates import dump, file_hash
        with tempfile.TemporaryDirectory() as t:
            out=Path(t);raw=out/'runs'/'case'/'raw';raw.mkdir(parents=True);(out/'inputs').mkdir()
            formula=out/'inputs'/'tiny.smt2';formula.write_text('(declare-const x Int)(assert (or (= x 0)(= x 1)))')
            dump(out/'plan.json',dict(inputs=[dict(name=formula.name,sha256=file_hash(formula))]))
            spec=dict(formula=formula.name,seed=0,configuration='HighDiv_release',group=0,seconds=1)
            dump(raw.parent/'spec.json',spec)
            dump(raw.parent/'process_result.json',dict(start_monotonic_ns=1000000000,process_wall_seconds=1.01,cleanup_after_deadline_seconds=.01,returncode=-15,termination_sent_seconds=1))
            (raw/'tiny.smt2.samples').write_text('0:x:0;\n1:x:0;\n2:x:1;\n3:x:1;\n4:x:')
            (raw/'tiny.smt2.times.csv').write_text('0,1100000000,1\n1,1200000000,1\n2,2100000000,1\n')
            result=audit_run((str(out),'case'))
            self.assertEqual(result['distinct'],1);self.assertEqual(result['covered'],0)
            self.assertEqual(result['excluded'],dict(duplicate=1,late=1,missing_timestamp=1,not_committed_or_cancelled=0))
            self.assertEqual(result['issues'][0]['kind'],'cutoff_tail')
if __name__=='__main__':unittest.main()
