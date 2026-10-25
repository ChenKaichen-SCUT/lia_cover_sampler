"""Protocol orchestration and reports use synthetic records, never solver search."""
import copy,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from run_native_feedback_pairs import CONFIGS,slot_layout,execute_groups
from finish_native_feedback_round import exposure_gate
from reevaluate_round6_saved_candidates import dump,write_csv

class NativeProtocol(unittest.TestCase):
    def test_four_distinct_physical_cores_and_controller(self):
        def topology(p,*args,**kwargs):
            cpu=int(p.parts[-3][3:]);return '0' if p.name=='physical_package_id' else str(cpu%5)
        with patch.object(Path,'read_text',topology):
            slots,identities,spare=slot_layout(set(range(10)))
            self.assertEqual(len(slots),1);self.assertEqual(len(slots[0]),4);self.assertEqual(spare,[4])
            self.assertEqual(len({next(i['physical_core'] for i in identities if i['cpu']==c) for c in slots[0]}),4)
            with self.assertRaises(RuntimeError):slot_layout(set(range(4)))
    def test_group_barrier(self):
        events=[]
        def launch(g,job,slot,cores):events.append(('start',g));return dict(g=g,ticks=0)
        def collect(b):b['ticks']+=1;return b['ticks']==2
        def release(b,abort=False):events.append(('end',b['g']))
        execute_groups(list(range(6)),[[0,1,2,3]],launch,collect,release,lambda *a:None,interval=0)
        self.assertEqual(events,[(phase,g) for g in range(6) for phase in ('start','end')])
    def test_exposure_gate_ignores_coverage_but_requires_real_opportunities(self):
        runs=[dict(configuration=c,selected=2,outside_catalog=1,keep=1,flip=1,covered=0) for f in range(2) for c in CONFIGS]
        self.assertTrue(exposure_gate(runs,'frozen')['passed'])
        runs[2]['covered']=-10000;self.assertTrue(exposure_gate(runs,'frozen')['passed'])
        for field in ('selected','outside_catalog','keep','flip'):
            bad=copy.deepcopy(runs)
            for r in bad:r[field]=0
            self.assertFalse(exposure_gate(bad,'frozen')['passed'])
        self.assertFalse(exposure_gate(runs[:-1],'frozen')['passed'])
    def test_analysis_missing_prefixes_and_chinese_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for stage,seconds,seeds in [('exposure_30s',30,[0]),('screen_120s',120,[0,1,2])]:
                out=root/stage;out.mkdir();runs=[]
                for formula in ('SMT_random_fixture.smt2','SMT_real_fixture.smt2'):
                    for seed in seeds:
                        for config in CONFIGS:
                            name=formula+str(seed)+config;d=out/'audit'/name;d.mkdir(parents=True)
                            run=dict(formula=formula,seed=seed,configuration=config,group=seed,run=name,seconds=seconds,total=100,covered=0,coverage=0,
                                distinct=0,process_wall_seconds=seconds,first_model_seconds=None,longest_no_growth_seconds=seconds,query_counts=None,
                                native_counters={},mechanism_seconds=0,mechanism_stop='',feature_seconds=0,prediction_seconds=0,training_seconds=0,cycle_cost_sum=0,tail_cost=seconds)
                            runs.append(run);write_csv(d/'timeline.csv',[],['index','completion','gain','covered','total']);write_csv(d/'decision_audit.csv',[],['selected'])
                dump(out/'audit_summary.json',dict(passed=True,runs=runs));dump(out/'plan.json',dict(kind='exposure' if seconds==30 else 'screen'))
                subprocess.run([sys.executable,str(ROOT/'scripts/analyze_native_feedback_results.py'),str(out)],check=True,capture_output=True)
                results=json.loads((out/'analysis/summary.json').read_text())
                self.assertTrue(all(r['incomplete_pairs']==len(seeds) and r['mean_delta'] is None for r in results['paired_summary'] if r['dimension']=='prefix'))
            for passed in (False,True):
                dump(root/'exposure_gate.json',dict(passed=passed,checks={},selection_uses_coverage_results=False))
                report=root/'report.md'
                subprocess.run([sys.executable,str(ROOT/'scripts/report_native_feedback_results.py'),'--root',str(root),'--report',str(report)],check=True,capture_output=True)
                text=report.read_text();self.assertIn('未重跑 HighDiv',text)
                self.assertIn('当前证据不支持稳定的学习优势' if passed else '未启动24个120秒会话',text)
if __name__=='__main__':unittest.main()
