"""Frozen validation controller/report regressions; no new solver search."""
import json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from run_native_frozen_900s import jobs,resource_victim,copy_highdiv,verify_frozen
from analyze_native_frozen_900s import analyze,report,paired
from reevaluate_round6_saved_candidates import dump,write_csv

class NativeFrozen900(unittest.TestCase):
    def test_exact_authorized_matrix_no_highdiv(self):
        matrix=jobs([dict(name=n,path=n) for n in ('random','real')])
        self.assertEqual(len(matrix),8)
        self.assertEqual({(r['formula'],r['seed'],r['configuration']) for r in matrix},{(f,s,c) for f in ('random','real') for s in (0,1) for c in ('plain','native_random_one')})
        self.assertTrue(all(r['seconds']==900 for r in matrix));self.assertEqual(len({r['group'] for r in matrix}),4)
    def test_resource_guard_selects_whole_pair_and_tolerates_exited_wrapper(self):
        active=[dict(spec=dict(group=i//2),termination=None,p=SimpleNamespace(pid=i)) for i in range(4)]
        samples={i:[dict(VmRSS='200 kB' if i<2 else '20 kB')] for i in range(4)};samples[0].append(dict(VmRSS=None))
        self.assertEqual(resource_victim(active,samples),0)
        for r in active[:2]:r['termination']=1
        self.assertEqual(resource_victim(active,samples),1)
    def test_frozen_identity_and_historical_evidence(self):
        evidence=verify_frozen();self.assertIn('src/native_phase_feedback.cpp',evidence)
        with tempfile.TemporaryDirectory() as tmp:
            proof=copy_highdiv(Path(tmp));self.assertEqual(len(proof),4)
            ref=json.loads((Path(tmp)/'historical_highdiv.json').read_text())
            self.assertFalse(ref['same_condition_paired']);self.assertEqual([r['covered'] for r in ref['rows']],[13504,7639])
    def test_missing_prefix_not_zero_or_deleted(self):
        rows=[dict(formula='f',seed=0,configuration=c,prefix=100,complete=ok,covered=v,total=64,arrival_seconds=t,run=c) for c,ok,v,t in [('plain',True,9,1),('native_random_one',False,None,None)]]
        result=paired(rows,'prefix');self.assertEqual(len(result),1);self.assertFalse(result[0]['complete']);self.assertIsNone(result[0]['delta_bits'])
    def test_empty_and_zero_gain_sessions_stay_in_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);inputs=[dict(name=n) for n in ['SMT_random_fixture','SMT_real_fixture']];runs=[]
            for job in jobs([dict(i,path=i['name']) for i in inputs]):
                name=str(job['group'])+job['configuration'];d=out/'runs'/name;raw=d/'raw';raw.mkdir(parents=True);ad=out/'audit'/name;ad.mkdir(parents=True)
                dump(d/'process_result.json',dict(resource_stop=False))
                (raw/'costs.jsonl').write_text('');(raw/'queries.jsonl').write_text('')
                write_csv(ad/'timeline.csv',[],['index','completion','gain','covered','coverage','observed'])
                runs.append(dict(formula=job['formula'],seed=job['seed'],configuration=job['configuration'],run=name,seconds=900,total=64,covered=0,coverage=0,distinct=0,positive=0,
                    process_wall_seconds=900,cleanup_seconds=0,returncode=0,terminal='deadline',first_model_seconds=None,longest_no_growth_seconds=900,native_counters={},query_counts=None,
                    mechanism_seconds=0,mechanism_stop='',cycle_cost_sum=0,tail_cost=900,excluded={},costs=None))
            dump(out/'plan.json',dict(inputs=inputs));dump(out/'audit_summary.json',dict(passed=True,runs=runs))
            dump(out/'historical_highdiv.json',dict(rows=[dict(formula=i['name'],seed=0,seconds=900,covered=1,total=64,coverage=1/64,distinct=2,curve=[dict(k=1,seconds=1,covered=0,total=64),dict(k=2,seconds=2,covered=1,total=64)]) for i in inputs]))
            data=analyze(out);report(out,data)
            self.assertEqual(len(data[0]),8);self.assertTrue(all(r['pairs']==2 and r['ties']==2 for r in data[3] if r['dimension']=='time_seconds'))
            self.assertTrue(all(r['pairs']==0 and r['incomplete']==2 and r['mean_delta_bits'] is None for r in data[3] if r['dimension']=='prefix'))
            text=(out/'报告.md').read_text();self.assertIn('没有修改算法参数',text);self.assertIn('不能回答超过900秒',text)
            self.assertEqual(json.loads((out/'analysis/summary.json').read_text())['highdiv_new_runs'],0)
if __name__=='__main__':unittest.main()
