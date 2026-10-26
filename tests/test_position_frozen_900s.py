"""No performance sampling: pure schedule/statistic regressions and fake fixtures."""
import csv,json,tempfile,unittest,subprocess
from collections import Counter
from pathlib import Path
from unittest.mock import patch
from run_position_frozen_900s import task_matrix,memory_capacity,GiB,SEEDS
from audit_position_frozen_900s import resource_fields,audit_resources
from analyze_position_frozen_900s import exact_auc,early_decomposition,paired_stats,decision_for,longest_plateau,zero_segment,analyze
from highdiv_gap_common import add,covered,categories,dump_gz
from reevaluate_round6_saved_candidates import dump,write_csv
ROOT=Path(__file__).resolve().parents[1]

class PositionFrozen900(unittest.TestCase):
    def test_matrix_smt_balance_rotation_and_new_seeds(self):
        workers=[[i*13+2,i*13+11] for i in range(7)];inputs=[dict(name=n,path=n) for n in ['random','real']];jobs=task_matrix(inputs,workers,7);physical={c:i for i,cs in enumerate(workers) for c in cs}
        self.assertEqual(len(jobs),28);self.assertEqual({r['seed'] for r in jobs},set(range(3,10)));self.assertEqual({r['batch'] for r in jobs},{0,1})
        self.assertEqual(len({(r['formula'],r['seed'],r['configuration']) for r in jobs}),28)
        for batch in [0,1]:
            rs=[r for r in jobs if r['batch']==batch];self.assertEqual(len({r['core'] for r in rs}),14);counts=Counter((physical[r['core']],r['configuration']) for r in rs);self.assertEqual(set(counts.values()),{1});self.assertEqual(len(counts),14)
            for group in {r['group'] for r in rs}:self.assertEqual(len({physical[r['core']] for r in rs if r['group']==group}),2)
        self.assertNotEqual(jobs[0]['core'],jobs[14]['core']);self.assertTrue(all(r['seconds']==900 for r in jobs))
    def test_preflight_memory_fallback_is_symmetric(self):
        self.assertEqual(memory_capacity(18*GiB,200*1024**2,7)[0],7);self.assertEqual(memory_capacity(8*GiB,200*1024**2,7)[0],2)
        with self.assertRaises(AssertionError):memory_capacity(3*GiB,1,7)
        jobs=task_matrix([dict(name=n,path=n) for n in ['random','real']],[[i*2,i*2+1] for i in range(7)],2)
        self.assertTrue(all(sum(r['batch']==b for r in jobs)<=4 for b in range(7)))
    def test_auc_all_events_same_timestamp_and_no_candidate(self):
        rows=[dict(completion=.5,gain=0,covered=0),dict(completion=2,gain=3,covered=3),dict(completion=2,gain=4,covered=7),dict(completion=7,gain=1,covered=8)]
        self.assertEqual(exact_auc(rows,10),59);self.assertEqual(exact_auc([],10),0)
        with self.assertRaises(ValueError):exact_auc(rows,6)
    def test_early_difference_decomposition_and_future_exclusion(self):
        f=[dict(index=1,completion=1,covered=0),dict(index=2,completion=2,covered=4)]
        p=[dict(index=1,completion=.5,covered=0),dict(index=2,completion=1.5,covered=3),dict(index=3,completion=3,covered=8)]
        self.assertEqual(early_decomposition(f,p,2)['delta'],-1);r=early_decomposition(f,p,4);self.assertEqual((r['common_prefix_quality'],r['arrival_count_component'],r['delta']),(-1,5,4))
    def test_empty_missing_prefix_and_zero_are_distinct(self):
        r=paired_stats([0,None,2,-1]);self.assertEqual((r['pairs'],r['missing'],r['wins'],r['ties'],r['losses']),(3,1,1,1,1))
        self.assertIsNone(paired_stats([None])['median'])
    def test_new_endpoint_rule_does_not_reuse_old_early_gate(self):
        rows=[]
        for f in ['a','b']:
            for metric,k in [('time',900),('time',10),('time',30),('time',60),('prefix',10)]:
                values=[1]*5+[-1,0] if k==900 else [-1]*4+[1]*3;rows.append(dict(formula=f,metric=metric,checkpoint=k,**paired_stats(values)))
        d=decision_for(rows,['a','b'],{});self.assertTrue(d['long_budget_signal_both_formulas']);self.assertEqual(d['classification'],'long_budget_gain_with_early_risk');self.assertFalse(d['automatic_next_experiment'])
        rows[0].update(paired_stats([1,-1,-1,-1,-1,-1,-1]));self.assertFalse(decision_for(rows,['a','b'],{})['long_budget_signal_both_formulas'])
    def test_first_observation_pairing_and_disjoint_bit_categories(self):
        f,p=(1,0),(0,1);self.assertEqual(covered(f)|covered(p),0);self.assertEqual(covered(add(f,p)),1)
        layout=[dict(offset=0,width=64,kind='integer_variable'),dict(offset=64,width=1,kind='Boolean')];c=categories(layout,1|(1<<8)|(1<<32)|(1<<64));self.assertEqual(sum(c.values()),4);self.assertEqual(len(c),4)
    def test_zero_segments_and_inclusive_terminal_plateau(self):
        rows=[dict(index=i+1,completion=t,gain=g,new_observations=o) for i,(t,g,o) in enumerate([(1,0,1),(2,0,0),(3,2,1),(4,0,0)])]
        self.assertEqual(zero_segment(rows,'gain')['length'],2);self.assertEqual(longest_plateau(rows,10),7);self.assertEqual(longest_plateau([],900),900)
    def test_gnu_resource_units(self):
        r=resource_fields('User time (seconds): 1.5\nSystem time (seconds): 0.2\nElapsed (wall clock) time (h:mm:ss or m:ss): 15:00.10\nMaximum resident set size (kbytes): 100\nExit status: 0\n');self.assertAlmostEqual(r['wall_seconds'],900.1);self.assertEqual(r['max_rss_kib'],100)
    def test_resource_audit_matrix_barrier_and_real_affinity_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);jobs=task_matrix([dict(name=n,path=n) for n in ['random','real']],[[i*2,i*2+1] for i in range(7)],7)
            plan=dict(jobs=jobs,cpu_topology=[dict(cpu=i,package=0,physical_core=i//2) for i in range(16)],supervisor_cpu=14,reserved_cpu=15,total_sessions=28,concurrent_pair_groups=7,max_concurrent_sessions=14)
            for j in jobs:
                d=out/'runs'/f"{j['group']:02d}_{j['configuration']}";(d/'raw').mkdir(parents=True);start=(1000+j['batch']*902)*10**9
                dump(d/'spec.json',j);dump(d/'gate.json',dict(start_monotonic_ns=start,exec_monotonic_ns=start+10**6));dump(d/'ready.json',dict(affinity=[j['core']]))
                dump(d/'process_result.json',dict(start_monotonic_ns=start,returncode=0,resource_stop=False,hard_kill=False,process_wall_seconds=900));dump(d/'raw/initial.json',dict(test_query_limit=2**32-1,mode=j['configuration']))
                (d/'resource.txt').write_text('User time (seconds): 800\nSystem time (seconds): 2\nElapsed (wall clock) time (h:mm:ss or m:ss): 15:00.00\nMaximum resident set size (kbytes): 100\nExit status: 0\n')
                (d/'resource_samples.jsonl').write_text(json.dumps(dict(processes=[dict(Name='native_delivery',Threads='1')]))+'\n')
            self.assertTrue(audit_resources(out,plan)['passed'])
            dump(d/'ready.json',dict(affinity=[14]))
            with self.assertRaises(ValueError):audit_resources(out,plan)
    def test_synthetic_complete_analysis_and_plot_pipeline(self):
        # These are explicitly fabricated unit-test rows, never experimental
        # models. Production requires an independently audited 28-run matrix.
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'case';out.mkdir();(root/'docs').mkdir();inputs=[dict(name=n) for n in ['random.smt2','real.smt2']];layout=[dict(node=0,offset=0,width=1,kind='Boolean',expression='b')];plan=dict(inputs=inputs,seeds=SEEDS,max_concurrent_sessions=14,worker_physical_cores=[[i*2,i*2+1] for i in range(7)],supervisor_cpu=14,reserved_cpu=15);dump(out/'plan.json',plan);audits=[];resources=[]
            for seed in SEEDS:
                for formula in ['random.smt2','real.smt2']:
                    for mode in ['first_immediate','position_immediate']:
                        name=f'{seed}_{formula}_{mode}';d=out/'audit'/name;d.mkdir(parents=True);raw=out/'runs'/name/'raw';raw.mkdir(parents=True);t=2 if mode=='first_immediate' else 1
                        rows=[dict(index=1,generation_index=1,query=0,completion=.5,ready=.5,wait=0,gain=0,covered=0,new_observations=1,observations=1),dict(index=2,generation_index=2,query=1,completion=t,ready=t,wait=0,gain=1,covered=1,new_observations=1,observations=2)]
                        for f in ['delivery','generation']:write_csv(d/(f+'.csv'),rows)
                        dump(d/'layout.json',layout);dump(d/'coverage_final.json',dict(states=[['1','1']]));dump_gz(d/'models.jsonl.gz',[dict(index=1,zero='0x1',one='0x0'),dict(index=2,zero='0x0',one='0x1')])
                        dump(raw/'summary.json',dict(stats=dict(seconds=dict(check_including_hook=.1)),queries=2,mechanism_seconds=0,delivery_management_seconds=0,terminal='deadline'));(raw/'queries.jsonl').write_text('')
                        audits.append(dict(run=name,group=seed,formula=formula,seed=seed,configuration=mode,total=1,counts=dict(sat=2),tail=0,tail_covered_gap=0))
                        resources.append(dict(run=name,process_wall_seconds=900,user_seconds=.1,system_seconds=.01,max_rss_kib=100))
            dump(out/'audit_summary.json',dict(passed=True,runs=audits,resources=dict(all_pairs_synchronized=True)));write_csv(out/'resource_audit.csv',resources)
            with patch('analyze_position_frozen_900s.ROOT',root),patch('analyze_position_frozen_900s.load_history',return_value=(1,1,[])):analyze(out)
            with (out/'analysis/paired_summary.csv').open() as file:rows=list(csv.DictReader(file))
            r=next(r for r in rows if r['metric']=='auc');self.assertEqual(float(r['median']),1);r=next(r for r in rows if r['metric']=='prefix' and r['checkpoint']=='1000');self.assertEqual(int(r['missing']),7)
            p=subprocess.run(['python3',str(ROOT/'scripts/plot_position_frozen_900s.py'),str(out)],capture_output=True,text=True);self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(len(list((out/'figures').glob('*.png'))),4)
