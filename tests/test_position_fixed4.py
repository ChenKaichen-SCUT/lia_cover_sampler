import unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
from collections import Counter
from run_position_fixed4 import slot_assignments,task_matrix,CONFIGS,SEEDS
from analyze_position_fixed4 import paired_summary,prefix_at,gain_bin
class FixedFourthProtocol(unittest.TestCase):
    def test_exact_matrix_and_resources(self):
        cores=[[2*i,2*i+1] for i in range(8)]
        jobs=task_matrix([dict(name='a',path='a'),dict(name='b',path='b')],cores)
        self.assertEqual(len(jobs),42);self.assertEqual(Counter(r['batch'] for r in jobs),{0:15,1:15,2:12})
        self.assertEqual({r['seed'] for r in jobs},set(range(10,17)))
        for batch in range(3):
            rs=[r for r in jobs if r['batch']==batch];self.assertEqual(len({r['core'] for r in rs}),len(rs));self.assertNotIn(15,{r['core'] for r in rs})
            self.assertEqual(set(Counter(r['configuration'] for r in rs).values()),{5 if batch<2 else 4})
        for group in range(14):
            rs=[r for r in jobs if r['group']==group];self.assertEqual({r['configuration'] for r in rs},set(CONFIGS));self.assertEqual(len({r['core']//2 for r in rs}),3)
    def test_missing_prefix_is_not_terminal_or_zero(self):
        self.assertIsNone(prefix_at([dict(covered=99)],1000))
        r=paired_summary([dict(difference=None),dict(difference=0),dict(difference=-4),dict(difference=2)])
        self.assertEqual((r['total_pairs'],r['complete_pairs'],r['incomplete_pairs']),(4,3,1));self.assertEqual((r['wins'],r['ties'],r['losses']),(1,1,1));self.assertEqual(r['maximum_regression'],4)
        self.assertIsNone(paired_summary([dict(difference=None)])['mean'])
    def test_gain_bin_boundaries(self):
        self.assertEqual([gain_bin(x) for x in [0,1,2,3,9,10,99,100,1000]],['0','1–2','1–2','3–9','3–9','10–99','10–99','100+','100+'])
    def test_report_full_matrix_preserves_all_unattained_pairs(self):
        import analyze_position_fixed4 as analysis
        from reevaluate_round6_saved_candidates import dump,write_csv
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'results';out.mkdir();(root/'docs').mkdir();runs=[];resources=[];costs=[];initial=[]
            for seed in SEEDS:
                for f,formula in enumerate(['random.smt2','real.smt2']):
                    group=2*(seed-10)+f;initial.append(dict(group=group,formula=formula,seed=seed,first_models_equal=True,prefirst_status_grants_equal=True))
                    for c,mode in enumerate(CONFIGS):
                        name=f'{group:02d}_{mode}';raw=out/'runs'/name/'raw';raw.mkdir(parents=True);audit=out/'audit'/name;audit.mkdir(parents=True)
                        gain=3+c;r=dict(run=name,group=group,formula=formula,seed=seed,configuration=mode,total=100,covered=gain,counts=dict(sat=2,unsat=1));runs.append(r)
                        dump(raw/'initial.json',{});dump(raw/'summary.json',{})
                        (raw/'positions.jsonl').write_text('\n'.join(json.dumps(dict(position=dict(target=1,eligible=bool(q),target_arrived=bool(q)),decision=dict(selected=bool(q),action='KeepNative'))) for q in range(2))+'\n')
                        write_csv(audit/'delivery.csv',[dict(index=q+1,generation_index=q+1,query=q,model_id=str(q),completion=q+1.,ready=q+1.,wait=0,gain=gain if q else 0,covered=gain if q else 0,new_observations=2,observations=2*(q+1)) for q in range(2)])
                        resources.append(dict(run=name,process_wall_seconds=120,user_seconds=2,system_seconds=1,max_rss_kib=100))
                        costs.append(dict(run=name,mechanism_seconds=.1,mechanism_stop='',stop_at_seconds=-1,stop_query=4294967295,eligible=2,enabled_checks=2,target_arrived=1,selected=1,flip=0,not_reached=1,disabled=1,scan_attempts=4,feature_seconds=.01,statistics_capture_seconds=.01,diagnostic_journal_seconds=.01,arrival_rate_all=1/3,selection_rate_all=1/3,flip_rate_all=0,arrival_rate_eligible=.5))
            dump(out/'audit_summary.json',dict(passed=True,runs=runs));dump(out/'plan.json',dict(inputs=[dict(name='random.smt2'),dict(name='real.smt2')]))
            for name,rows in [('resource_audit.csv',resources),('intervention_costs.csv',costs),('initialization_pairs.csv',initial)]:write_csv(out/name,rows)
            with patch.object(analysis,'ROOT',root):analysis.analyze(out)
            result=json.loads((out/'analysis_summary.json').read_text());self.assertEqual(result['sessions'],42)
            self.assertTrue(all(r['attained']==0 and r['unattained']==7 for r in result['first_1000']))
            rows=analysis.csvrows(out/'paired_differences.csv');unattained=[r for r in rows if r['kind']=='prefix' and r['point']=='1000']
            self.assertEqual(len(unattained),42);self.assertTrue(all(r['difference']=='' for r in unattained))
