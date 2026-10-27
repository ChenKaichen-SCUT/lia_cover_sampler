"""Tiny fixed-query integration regression. Not an experimental trajectory."""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from audit_native_delivery_results import one_run
from reevaluate_round6_saved_candidates import file_hash
BIN=Path(os.environ.get('LIA_ARITH_COVERAGE_BINARY',ROOT/'build/arith_coverage/release/arith_coverage_sampler'))
@unittest.skipUnless(BIN.exists(),'independent arithmetic feature not built')
class Integration(unittest.TestCase):
    def test_off_matches_frozen_native_sequence(self):
        old=ROOT/'build/position_fixed4/release/native_delivery_sampler'
        if not old.exists():self.skipTest('frozen binary absent')
        with tempfile.TemporaryDirectory(prefix='arith-off-') as temp:
            root=Path(temp);formula=root/'formula.smt2';formula.write_text('(declare-const x Int)(declare-const y Int)(assert (and (or (= x 0) (= x 1) (= x 2)) (or (= y 0) (= y 1) (= y 2))))')
            for seed in [17,18]:
                traces=[]
                for label,binary,mode in [('old',old,'position_immediate'),('new',BIN,'phase_only')]:
                    dest=root/(label+str(seed));r=subprocess.run([str(binary),str(formula),mode,'10',str(seed),str(dest)],capture_output=True,text=True,env=dict(os.environ,NATIVE_TEST_MAX_QUERIES='8'),timeout=30);self.assertEqual(r.returncode,0,r.stderr)
                    models=[json.loads(l)['model'] for l in (dest/'deliveries.jsonl').read_text().splitlines()]
                    queries=[json.loads(l) for l in (dest/'positions.jsonl').read_text().splitlines()]
                    trace=[(q['position']['target'],q['position']['reached'],q['decision'].get('action'),q['decision'].get('ordinal'),q['decision'].get('native_truth'),q['decision'].get('action_draw')) for q in queries]
                    self.assertEqual(len(models),8);traces.append((models,trace))
                self.assertEqual(traces[0],traces[1],'off changed fixed-operation models or phase RNG')
    def test_dual_histories_and_blockers(self):
        with tempfile.TemporaryDirectory(prefix='arith-online-') as temp:
            out=Path(temp);(out/'inputs').mkdir();formula=out/'inputs/tiny.smt2';formula.write_text('(declare-const x Int)(declare-const y Int)(assert (and (= (+ x y) 255) (<= 0 x) (<= x 255) (<= 0 y) (<= y 255)))')
            (out/'plan.json').write_text(json.dumps(dict(kind='test',inputs=[dict(name=formula.name,sha256=file_hash(formula))])))
            for config in ['phase_only','arith_random','arith_coverage','arith_probe']:
                d=out/'runs'/config;d.mkdir(parents=True);spec=dict(formula=formula.name,seed=17,configuration=config,seconds=10,group=0);(d/'spec.json').write_text(json.dumps(spec))
                env=dict(os.environ,NATIVE_TEST_MAX_QUERIES='8');result=subprocess.run([str(BIN),str(formula),config,'10','17',str(d/'raw')],env=env,capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stderr);audited=one_run((str(out),config));self.assertTrue(audited['passed']);self.assertEqual(audited['delivered'],8)
                initial=json.loads((d/'raw/initial.json').read_text());self.assertEqual(initial['position_policy'],'random_supported_rank')
                rows=[json.loads(l) for l in (d/'raw/arithmetic.jsonl').read_text().splitlines()]
                if config=='phase_only':self.assertEqual(rows,[])
                else:
                    self.assertEqual(len(rows),7)
                    for q,r in enumerate(rows,1):
                        self.assertEqual(r['query_id'],q);self.assertEqual(r['coverage_version_before'],q);self.assertFalse(r['implementation_failure'])
                        b=r['backend'];self.assertLessEqual(b.get('pool_distinct',0),16)
                        if config=='arith_probe':self.assertFalse(b.get('committed_move',False))
                        if b.get('committed_move'):
                            self.assertTrue(b['pool_complete'])
                            if config=='arith_coverage':self.assertTrue(r['score_complete'])
if __name__=='__main__':unittest.main()
