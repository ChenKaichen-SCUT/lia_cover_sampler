"""Resident lifecycle and delivered checkpoint contracts with the frozen metric."""
import json,os,subprocess,tempfile,unittest,hashlib,sys
from pathlib import Path
from test_suite import ROOT
from coverage_differential_test import load_metric
from validation import original_formula,verify

PROBE=Path(os.environ.get('LIA_COVERAGE_BLOCK_PROBE',str(Path(os.environ.get('LIA_BINARY',ROOT/'build/lia_cover_sampler')).with_name('coverage_block_probe'))))

class CoverageBlockContract(unittest.TestCase):
    def invoke(self,p,mode,budget):
        f=p/'input.smt2'
        if not f.exists():
            f.write_text(''.join(f'(declare-const x{i} Int)(assert (>= x{i} 0))' for i in range(8)))
            (p/'prefix.values').write_text('0 0 0 0 0 0 0 0\n')
            (p/'seed.values').write_text('0 0 0 0 0 0 0 0\n')
        output=p/(mode+'.json')
        r=subprocess.run([str(PROBE),str(f),str(p/'prefix.values'),str(p/'seed.values'),mode,str(budget),'0',str(output)],capture_output=True,text=True,timeout=30)
        self.assertEqual(r.returncode,0,r.stderr)
        checkpoints=[json.loads(x) for x in Path(str(output)+'.checkpoints.jsonl').read_text().splitlines()]
        return json.loads(output.read_text()),checkpoints

    def test_shared_targets_lifecycle_and_exact_checkpoint_gain(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);runs={}
            for mode in ('global','block'):
                data,checkpoints=self.invoke(p,mode,2);runs[mode]=data
                self.assertEqual(len(data['actions']),3)
                self.assertGreater(data['initialization_seconds'],0)
                self.assertGreaterEqual(data['session_seconds'],data['initialization_seconds']+data['sum_action_seconds'])
                metric=load_metric();stat=metric.WireCoverageStatistics();ev=metric.ManualSatisfiesMetric((p/'input.smt2').read_text(),stat)
                self.assertTrue(ev.count_sample([(f'x{i}',0) for i in range(8)]));storage=stat._storage.copy()
                covered=lambda:sum(stat.node_count(*s) for s in stat._storage.values())
                baseline=covered();positive=0
                for action in data['actions']:
                    r=action['result'];self.assertLessEqual(len(r['queries']),6)
                    writes=action['checkpoint_writes'];records=[c for c in checkpoints if c['action']==action['action']]
                    self.assertEqual(len(writes),len(records))
                    timely=[]
                    for w,c in zip(writes,records):
                        self.assertEqual(w['timely'],w['finished_seconds']<=2)
                        self.assertEqual(verify(original_formula(p/'input.smt2'),c['candidate']),'valid')
                        self.assertNotEqual(c['candidate'],data['input_model'])
                        stat._storage=storage.copy()
                        self.assertTrue(ev.count_sample([(x['name'],int(x['value'])) for x in c['candidate']]))
                        self.assertEqual(covered()-baseline,c['gain'])
                        if w['timely']:timely.append(c)
                    if r['candidate']:
                        self.assertTrue(timely);self.assertEqual(r['candidate'],timely[-1]['candidate'])
                        self.assertEqual(r['gain'],timely[-1]['gain'])
                        self.assertLessEqual(r['checkpoint_seconds'],2);positive+=r['gain']>0
                    else:self.assertEqual(r['gain'],0)
                self.assertGreater(positive,0)
            self.assertEqual(runs['global']['snapshot'],runs['block']['snapshot'])
            self.assertEqual([a['result']['primary'] for a in runs['global']['actions']],[a['result']['primary'] for a in runs['block']['actions']])

    def test_exhausted_action_does_not_recharge_initialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            for mode in ('global','block'):
                data,checkpoints=self.invoke(Path(tmp),mode,1e-9)
                self.assertEqual(checkpoints,[])
                self.assertGreater(data['initialization_seconds'],data['sum_action_seconds'])
                for a in data['actions']:
                    self.assertIsNone(a['result']['candidate']);self.assertEqual(a['result']['queries'],[])
                    self.assertEqual(a['result']['gain'],0)

    def test_independent_session_auditor_and_corruption(self):
        sys.path.insert(0,str(ROOT/'scripts'))
        from run_coverage_block_pairs import audit
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);data,_=self.invoke(p,'block',.06)
            (p/'coverage.json').write_text(json.dumps(data['snapshot']))
            (p/'assignments.json').write_text(json.dumps(dict(prefix=[data['input_model']],seed=data['input_model'])))
            sha=lambda f:hashlib.sha256(Path(f).read_bytes()).hexdigest()
            snapshot=dict(id=tmp,folder=tmp,formula=str(p/'input.smt2'),input_sha256=sha(p/'input.smt2'))
            for name,field in [('prefix.values','prefix_sha256'),('seed.values','seed_sha256'),('coverage.json','coverage_sha256'),('assignments.json','assignments_sha256')]:snapshot[field]=sha(p/name)
            row=dict(output='block.json',mode='block',seed=0,repeat=0,success=True,probe_sha256=sha(p/'block.json'),checkpoints_sha256=sha(p/'block.json.checkpoints.jsonl'))
            result=audit((snapshot,[row],str(p)));self.assertTrue(result['all_passed'],str(result))
            data['actions'][0]['result']['gain']+=1
            (p/'block.json').write_text(json.dumps(data)+'\n');row['probe_sha256']=sha(p/'block.json')
            self.assertFalse(audit((snapshot,[row],str(p)))['all_passed'])

if __name__=='__main__':unittest.main()
