"""Small model-set and full journal regressions for the new separate executable."""
import json,os,shutil,subprocess,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BIN=Path(os.environ.get('LIA_NATIVE_FEEDBACK_BINARY',ROOT/'build/native_feedback/release/native_feedback_sampler'))
MODES=('plain','catalog_random_one','native_random_one','native_feedback_one')
@unittest.skipUnless(BIN.exists(),'native feedback build not configured')
class NativeFeedback(unittest.TestCase):
    def run_mode(self,text,mode,seconds=2,expired=False):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);d=Path(tmp.name);f=d/'input.smt2';f.write_text(text)
        env=dict(os.environ)
        if expired:env['BOFILL_START_MONO_NS']=str(time.monotonic_ns()-10**9)
        result=subprocess.run([str(BIN),str(f),mode,str(seconds),'0',str(d/'out')],env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr);return d/'out',f
    def test_full_finite_set_and_single_slot(self):
        from test_bofill import Bofill
        text='(declare-const x Int)(declare-const y Int)(assert (and (or (= x 0)(= x 7)) (or (= y 0)(= y 7))))'
        identities=[]
        for mode in MODES:
            raw,f=self.run_mode(text,mode);Bofill.audit(self,raw,text)
            ms=list(map(json.loads,(raw/'models.jsonl').read_text().splitlines()));qs=list(map(json.loads,(raw/'queries.jsonl').read_text().splitlines()))
            self.assertEqual(len(ms),4);self.assertFalse(qs[0]['decision']['selected'])
            self.assertTrue(all(q['hook'].get('selected',0)<=1 and q['hook'].get('flips',0)<=1 for q in qs))
            identities.append({json.dumps(m['model'],sort_keys=True) for m in ms})
            costs=list(map(json.loads,(raw/'costs.jsonl').read_text().splitlines()));s=json.loads((raw/'summary.json').read_text())
            self.assertEqual(len(costs),len(qs));self.assertAlmostEqual(sum(c['full_cycle_seconds'] for c in costs)+s['final_tail_seconds'],s['elapsed'])
            fs=list(map(json.loads,(raw/'feedback.jsonl').read_text().splitlines()))
            self.assertEqual(len(fs),sum(q['decision']['selected'] for q in qs))
            self.assertTrue(all(fb['unselected_action_reward'] is None and fb['unselected_action_cost'] is None for fb in fs))
        self.assertTrue(all(x==identities[0] for x in identities))
    def test_expired_never_searches_or_trains(self):
        for mode in MODES:
            raw,f=self.run_mode('(assert true)',mode,.02,True)
            self.assertEqual((raw/'queries.jsonl').read_text(),'');self.assertEqual((raw/'feedback.jsonl').read_text(),'')
    def test_no_supported_decision_no_feedback(self):
        for text in ('(assert false)','(declare-const x Int)(assert (= x 0))'):
            raw,f=self.run_mode(text,'native_feedback_one')
            self.assertEqual((raw/'feedback.jsonl').read_text(),'')
    def test_negative_large_and_short_circuit_coverage(self):
        from test_bofill import Bofill
        text='(declare-const x Int)(assert (and (or (= x (- 18446744073709551617))(= x 18446744073709551618)) (or true (= (+ x 42) 9)) (= (+ x x) (* 2 x))))'
        for mode in MODES:
            raw,f=self.run_mode(text,mode);Bofill.audit(self,raw,text)
    def test_fixed_operation_limit_has_no_extra_queries(self):
        text='(declare-const x Int)(assert (>= x 0))'
        old=os.environ.get('NATIVE_TEST_MAX_QUERIES');os.environ['NATIVE_TEST_MAX_QUERIES']='3'
        try:
            for mode in MODES:
                raw,f=self.run_mode(text,mode)
                self.assertEqual(json.loads((raw/'summary.json').read_text())['queries'],3)
        finally:
            if old is None:os.environ.pop('NATIVE_TEST_MAX_QUERIES',None)
            else:os.environ['NATIVE_TEST_MAX_QUERIES']=old
    def test_independent_feedback_audit(self):
        from audit_native_feedback_results import one_run
        from reevaluate_round6_saved_candidates import dump,file_hash
        text='(declare-const x Int)(declare-const y Int)(assert (and (or (= x 0)(= x 7)) (or (= y 0)(= y 7))))'
        for mode in MODES:
            raw,f=self.run_mode(text,mode);out=raw.parent/'experiment';run=out/'runs'/'case';run.mkdir(parents=True);(out/'inputs').mkdir()
            shutil.copyfile(f,out/'inputs'/f.name);shutil.move(raw,run/'raw')
            dump(out/'plan.json',dict(inputs=[dict(name=f.name,sha256=file_hash(f))]))
            dump(run/'spec.json',dict(configuration=mode,formula=f.name,seed=0,group=0,seconds=2))
            dump(run/'process_result.json',dict(process_wall_seconds=2,cleanup_after_deadline_seconds=0,returncode=0,termination_sent_seconds=None))
            result=one_run((str(out),'case'));self.assertEqual(result['distinct'],4)

    def test_saved_feedback_unknown_duplicate_late_and_unrealized(self):
        """Synthetic journals exercise result handling, never claim a new experiment."""
        from audit_native_feedback_results import one_run,value
        from reevaluate_round6_saved_candidates import FrozenCoverage,dump,file_hash,no_search,EvidenceError
        import z3
        text='(declare-const x Int)(declare-const y Int)(assert (and (or (= x 0)(= x 7)) (or (= y 0)(= y 7))))'
        raw,f=self.run_mode(text,'native_random_one')
        read=lambda name:list(map(json.loads,(raw/(name+'.jsonl')).read_text().splitlines()))
        queries=read('queries');selected=next(q['query'] for q in queries if q['decision']['selected'])
        for kind in ('unknown','duplicate','late','flip_not_realized'):
            with self.subTest(kind=kind):
                out=raw.parent/kind;run=out/'runs'/'case';run.mkdir(parents=True);(out/'inputs').mkdir()
                shutil.copyfile(f,out/'inputs'/f.name);shutil.copytree(raw,run/'raw');dest=run/'raw'
                dump(out/'plan.json',dict(inputs=[dict(name=f.name,sha256=file_hash(f))]))
                dump(run/'spec.json',dict(configuration='native_random_one',formula=f.name,seed=0,group=0,seconds=2))
                dump(run/'process_result.json',dict(process_wall_seconds=2.1,cleanup_after_deadline_seconds=.1,returncode=0,termination_sent_seconds=None))
                rows={name:[row for row in read(name) if row['query']<=selected] for name in ('queries','candidates','models','commits','feedback','costs')}
                q=rows['queries'][-1];fb=rows['feedback'][-1];cost=rows['costs'][-1]
                self.assertEqual(fb['query'],selected)
                if kind=='unknown':
                    q['evidence']['raw_status']='unknown';q['evidence']['control_classification']='explicit_local_timeout'
                    q.update(valid=False,emitted=False,final_truth=None,completion=-1,gain=0,observations=0)
                    fb.update(unknown=True,valid=False,timely=False,final_truth=None,gain=0,reward=0)
                    for name in ('candidates','models','commits'):rows[name]=[r for r in rows[name] if r['query']<selected]
                elif kind=='duplicate':
                    rows['candidates'][-1]['model']=rows['models'][0]['model']
                    q.update(duplicate=True,emitted=False,completion=-1,gain=0,observations=0)
                    fb.update(duplicate=True,timely=False,gain=0,reward=0)
                    for name in ('models','commits'):rows[name]=[r for r in rows[name] if r['query']<selected]
                elif kind=='late':
                    q.update(emitted=False,completion=2.01,gain=0,observations=0,finished=2.02)
                    rows['commits'][-1].update(timely=False,completion=2.01)
                    fb.update(timely=False,gain=0,reward=0,label_finalized_at=2.03,training_finished_at=2.031)
                    cost.update(end=2.03,full_cycle_seconds=2.03-cost['start']);fb['full_cycle_seconds']=cost['full_cycle_seconds']
                with no_search():
                    metric=FrozenCoverage(text);state=metric.empty.copy()
                    if kind!='unknown':
                        candidate=rows['candidates'][-1];atom=next(a for a in read('atoms') if a['cache_index']==q['decision']['cache_index'])
                        expression=z3.parse_smt2_string('(assert '+atom['expression']+')',decls={str(e.decl().name()):e for e in metric.decls},ctx=metric.root.ctx)[0]
                        truth=bool(value(expression,{v['name']:int(v['value']) for v in candidate['model']}));q['final_truth']=truth;fb['final_truth']=truth
                        if kind=='flip_not_realized':
                            q['decision'].update(action='FlipOnce',native_truth=truth,desired_truth=not truth,action_draw=.75)
                            q['decision']['features'][4]=1 if truth else -1;q['hook']['flips']=1;fb['action']='FlipOnce'
                    accepted={r['query'] for r in rows['commits'] if r['timely']}
                    for m in rows['models']:
                        if m['query'] in accepted:state=metric.apply(state,m['model'])
                    summary=json.loads((raw/'summary.json').read_text());summary.update(formal_count=len(accepted),coverage=metric.export(state),marginal_sum=metric.count(state),queries=len(rows['queries']))
                cost['gain']=q['gain'];summary['cycle_cost_sum']=sum(c['full_cycle_seconds'] for c in rows['costs'])
                summary['elapsed']=cost['end']+.002;summary['final_tail_seconds']=.002
                for name,rs in rows.items():(dest/(name+'.jsonl')).write_text(''.join(json.dumps(r)+'\n' for r in rs))
                dump(dest/'summary.json',summary);result=one_run((str(out),'case'))
                self.assertEqual(result['distinct'],len(accepted))
                if kind=='flip_not_realized':
                    self.assertGreater(result['native_counters']['unrealized'],0)
                    fb['action']='KeepNative';(dest/'feedback.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows['feedback']))
                    with self.assertRaises(EvidenceError):one_run((str(out),'case'))

    def test_warmup_and_window_eviction_independent_replay(self):
        from audit_native_feedback_results import one_run
        from reevaluate_round6_saved_candidates import dump,file_hash
        text=''.join(f'(declare-const x{i} Int)(assert (or (= x{i} 0)(= x{i} 7)))' for i in range(9))
        old=os.environ.get('NATIVE_TEST_MAX_QUERIES');os.environ['NATIVE_TEST_MAX_QUERIES']='280'
        try:raw,f=self.run_mode(text,'native_feedback_one',seconds=30)
        finally:
            if old is None:os.environ.pop('NATIVE_TEST_MAX_QUERIES',None)
            else:os.environ['NATIVE_TEST_MAX_QUERIES']=old
        out=raw.parent/'experiment';run=out/'runs'/'case';run.mkdir(parents=True);(out/'inputs').mkdir()
        shutil.copyfile(f,out/'inputs'/f.name);shutil.move(raw,run/'raw')
        dump(out/'plan.json',dict(inputs=[dict(name=f.name,sha256=file_hash(f))]))
        dump(run/'spec.json',dict(configuration='native_feedback_one',formula=f.name,seed=0,group=0,seconds=30))
        dump(run/'process_result.json',dict(process_wall_seconds=30,cleanup_after_deadline_seconds=0,returncode=0,termination_sent_seconds=None))
        result=one_run((str(out),'case'))
        self.assertEqual(result['distinct'],280);self.assertGreater(result['native_counters']['selected'],256)
        self.assertGreater(result['native_counters']['exploitation'],0)
if __name__=='__main__':unittest.main()
