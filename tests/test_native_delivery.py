"""Small correctness/compatibility tests; never counted as performance evidence."""
import json,os,subprocess,tempfile,time,unittest,shutil,gzip,itertools
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BIN=Path(os.environ.get('LIA_NATIVE_DELIVERY_BINARY',ROOT/'build/native_delivery/release/native_delivery_sampler'))
OLD=ROOT/'build/native_feedback/release/native_feedback_sampler'
MODES=('first_immediate','first_batch','position_immediate','position_batch')
TEXT=''.join(f'(declare-const x{i} Int)' for i in range(6))+'(assert (and '+''.join(f'(or (= x{i} 0)(= x{i} 7))' for i in range(6))+'))'
@unittest.skipUnless(BIN.exists(),'native delivery build not configured')
class NativeDelivery(unittest.TestCase):
    def run_mode(self,text,mode,seed=0,limit=12,seconds=20,expired=False,binary=BIN):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);d=Path(tmp.name);f=d/'input.smt2';f.write_text(text);env=dict(os.environ,NATIVE_TEST_MAX_QUERIES=str(limit))
        if expired:env['BOFILL_START_MONO_NS']=str(time.monotonic_ns()-10**9)
        p=subprocess.run([str(binary),str(f),mode,str(seconds),str(seed),str(d/'raw')],env=env,capture_output=True,text=True,timeout=30)
        self.assertEqual(p.returncode,0,p.stderr);return d
    def rows(self,d,name):return [json.loads(l) for l in (d/'raw'/name).read_text().splitlines()]
    def test_generation_independent_of_delivery(self):
        for prefix in ['first','position']:
            a=self.run_mode(TEXT,prefix+'_immediate',limit=40);b=self.run_mode(TEXT,prefix+'_batch',limit=40)
            self.assertEqual([r['model'] for r in self.rows(a,'candidates.jsonl')],[r['model'] for r in self.rows(b,'candidates.jsonl')])
            self.assertEqual([(r['query'],r['candidate_generation_index']) for r in self.rows(a,'blocks.jsonl')],[(r['query'],r['candidate_generation_index']) for r in self.rows(b,'blocks.jsonl')])
            def actions(d):return [(p['position']['target'],p['decision'].get('action'),p['decision'].get('action_draw'),p['decision'].get('cache_index')) for p in self.rows(d,'positions.jsonl')]
            self.assertEqual(actions(a),actions(b));self.assertEqual(len(self.rows(a,'queries.jsonl')),40)
            self.assertNotEqual([r['candidate_generation_index'] for r in self.rows(a,'deliveries.jsonl')],[r['candidate_generation_index'] for r in self.rows(b,'deliveries.jsonl')])
            for d in [a,b]:
                s=json.loads((d/'raw/summary.json').read_text());self.assertEqual(s['generation_coverage'],s['coverage']);self.assertEqual(s['undelivered_count'],0)
    def test_first_compatible_with_frozen_entry(self):
        self.assertTrue(OLD.exists(),'frozen binary evidence missing')
        for seed in [0,1]:
            old=self.run_mode(TEXT,'native_random_one',seed=seed,binary=OLD);new=self.run_mode(TEXT,'first_immediate',seed=seed)
            self.assertEqual([r['model'] for r in self.rows(old,'models.jsonl')],[r['model'] for r in self.rows(new,'deliveries.jsonl')])
            def actions(d):return [(q['evidence']['raw_status'],q['evidence']['granted_timeout_ms'],q['decision'].get('action'),q['decision'].get('action_draw'),q['decision'].get('ordinal')) for q in self.rows(d,'queries.jsonl')]
            self.assertEqual(actions(old),actions(new))
    def test_independent_dual_audit_and_position_stream(self):
        from audit_native_delivery_results import one_run
        from reevaluate_round6_saved_candidates import dump,file_hash
        for mode in MODES:
            d=self.run_mode(TEXT,mode,limit=70);out=d/'experiment';run=out/'runs'/'case';run.mkdir(parents=True);(out/'inputs').mkdir();f=d/'input.smt2';shutil.copyfile(f,out/'inputs'/f.name);shutil.move(d/'raw',run/'raw')
            dump(out/'plan.json',dict(inputs=[dict(name=f.name,sha256=file_hash(f))]));dump(run/'spec.json',dict(configuration=mode,formula=f.name,seed=0,group=0,seconds=20))
            r=one_run((str(out),'case'));self.assertEqual(r['generated'],64);self.assertEqual(r['delivered'],64);self.assertEqual(r['tail'],0)
    def test_expired_no_search_or_output(self):
        for mode in MODES:
            d=self.run_mode('(assert true)',mode,seconds=.05,expired=True)
            self.assertEqual(self.rows(d,'queries.jsonl'),[]);self.assertEqual(self.rows(d,'deliveries.jsonl'),[])
    def test_short_circuit_negative_low64_and_zero_gain(self):
        from audit_native_delivery_results import one_run
        from reevaluate_round6_saved_candidates import dump,file_hash
        text='(declare-const x Int)(assert (and (or (= x (- 18446744073709551617))(= x 18446744073709551618)) (or true (= (+ x 42) 9)) (= (+ x x) (* 2 x))))'
        for mode in MODES:
            d=self.run_mode(text,mode);out=d/'audit_case';run=out/'runs'/'case';run.mkdir(parents=True);(out/'inputs').mkdir();shutil.copyfile(d/'input.smt2',out/'inputs/input.smt2');shutil.move(d/'raw',run/'raw')
            dump(out/'plan.json',dict(inputs=[dict(name='input.smt2',sha256=file_hash(d/'input.smt2'))]));dump(run/'spec.json',dict(configuration=mode,formula='input.smt2',seed=0,group=0,seconds=20))
            r=one_run((str(out),'case'));self.assertTrue(r['passed']);self.assertEqual(r['generated'],2)
    def test_actual_later_positions_and_unique_opportunity(self):
        d=self.run_mode(TEXT,'position_immediate',limit=33);ps=self.rows(d,'positions.jsonl');reached=[p for p in ps if p['decision']['selected']]
        self.assertTrue(any(p['position']['target']>1 for p in reached))
        self.assertTrue(all(p['hook'].get('selected',0)<=1 and p['hook'].get('flips',0)<=1 for p in ps))
        self.assertTrue(all(p['position']['selected_rank']==p['position']['target'] for p in reached))
    def test_read_only_historical_fragment_and_fifo_reference(self):
        from reevaluate_round6_saved_candidates import FrozenCoverage,no_search
        source=ROOT/'results/highdiv_complementarity_gap/observations/00_native_random_one/models.jsonl.gz'
        with gzip.open(source,'rt') as f:rows=[json.loads(l) for l in itertools.islice(f,40)]
        formula=ROOT/'results/native_frozen_900s/inputs/SMT_random_LIA_ex14600_2600_100.smt2'
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);values=Path(tmp.name)/'values.txt';values.write_text('\n'.join(' '.join(str(v['value']) for v in r['model']) for r in rows)+'\n')
        replay=BIN.parent/'bounded_delivery_replay';p=subprocess.run([str(replay),str(formula),str(values)],capture_output=True,text=True,check=True)
        records=[json.loads(l) for l in p.stdout.splitlines()];batches=[r for r in records if r['type']=='batch'];deliveries=[r for r in records if r['type']=='delivery']
        with no_search():
            m=FrozenCoverage(formula.read_text());fifo=m.empty.copy();greedy=m.empty.copy();done=set()
            for b in batches:
                ds=[r for r in deliveries if r['batch']==b['id']];self.assertEqual(set(b['members']),{r['source'] for r in ds});self.assertFalse(done&set(b['members']));done.update(b['members'])
                for i in sorted(b['members']):fifo=m.apply(fifo,rows[i-1]['model'])
                for r in ds:
                    before=m.count(greedy);greedy=m.apply(greedy,rows[r['source']-1]['model']);self.assertEqual(m.count(greedy),r['covered']);self.assertEqual(m.count(greedy)-before,r['gain'])
                self.assertEqual(fifo,greedy)
            self.assertEqual(len(done),40)
