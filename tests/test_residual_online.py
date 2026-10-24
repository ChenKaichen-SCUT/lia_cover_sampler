"""Whole-session contracts and independent journal audit; synthetic inputs only."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from audit_residual_online import (Raw,parse_journals,audit_journals,observations,remaining,target_hits,timely)
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,EvidenceError
BIN=Path(os.environ.get('LIA_RESIDUAL_ONLINE_PROBE',ROOT/'build/round7/release/residual_online_probe'))


class ResidualOnlineContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(); cls.folder=Path(cls.temp.name)
        cls.formula=''.join(f'(declare-const x{i} Int)(assert (and (<= -3 x{i})(<= x{i} 3)))' for i in range(3))
        (cls.folder/'f.smt2').write_text(cls.formula)
        (cls.folder/'prefix.values').write_text('0 0 0\n')
        (cls.folder/'seed.values').write_text('1 0 0\n')
        cls.saved=dict(prefix=[[dict(name=f'x{i}',sort='Int',value='0') for i in range(3)]],
            seed=[dict(name=f'x{i}',sort='Int',value=str(int(i==0))) for i in range(3)])
        cls.data={}
        for mode in ('global_pack','residual_cardinality'):
            command=[str(BIN),str(cls.folder/'f.smt2'),str(cls.folder/'prefix.values'),str(cls.folder/'seed.values'),mode,'1','0',str(cls.folder/mode)]
            result=subprocess.run(command,capture_output=True,text=True,timeout=10)
            if result.returncode:raise AssertionError(result.stderr)
            raw=Raw(cls.folder); cls.data[mode]=parse_journals(raw,mode);raw.close()

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def evaluate(self,data):
        with no_search() as attempts:
            result=audit_journals(FrozenCoverage(self.formula),self.saved,data)
            self.assertEqual(attempts,[])
        return result

    def test_online_models_independently_match_frozen_evaluator(self):
        for mode,data in self.data.items():
            audit=self.evaluate(data)
            self.assertTrue(audit['passed']);self.assertTrue(audit['outputs'])
            self.assertEqual(sum(x['actual_marginal_gain'] for x in audit['outputs']),audit['actual_gain'])
            self.assertGreater(audit['seed_gain'],0)
            self.assertEqual(data['initial.json']['new_outputs'],0)
            for r in data['rounds.jsonl']:
                self.assertLessEqual(r['target_calls'],3)
                self.assertLessEqual(r['exploration_calls'],1)

    def test_shared_first_window_and_state(self):
        a,b=self.data.values()
        self.assertEqual(a['initial.json']['coverage'],b['initial.json']['coverage'])
        self.assertEqual(a['rounds.jsonl'][0]['window'],b['rounds.jsonl'][0]['window'])

    def test_mutated_marginal_is_rejected(self):
        data=copy.deepcopy(self.data['global_pack']);data['models.jsonl'][0]['actual_marginal_gain']+=1
        with self.assertRaises(EvidenceError):self.evaluate(data)

    def test_late_output_cannot_be_backdated_to_checkpoint(self):
        data=copy.deepcopy(self.data['residual_cardinality']);receipt=data['commits.jsonl'][0]
        receipt['completion_timestamp']=receipt['deadline_timestamp']+1
        with self.assertRaises(EvidenceError):self.evaluate(data)

    def test_stale_version_is_rejected(self):
        data=copy.deepcopy(self.data['global_pack']);data['models.jsonl'][0]['coverage_version_before']+=1
        with self.assertRaises(EvidenceError):self.evaluate(data)

    def test_missing_checkpoint_is_rejected(self):
        data=copy.deepcopy(self.data['residual_cardinality']);data['checkpoints.jsonl'].pop(0)
        with self.assertRaises(EvidenceError):self.evaluate(data)

    def test_duplicate_output_identity_is_rejected(self):
        data=copy.deepcopy(self.data['global_pack']);data['models.jsonl'].append(copy.deepcopy(data['models.jsonl'][0]))
        with self.assertRaises(EvidenceError):self.evaluate(data)

    def test_initialization_is_inside_tiny_budget(self):
        for mode in self.data:
            destination=self.folder/(mode+'-tiny')
            r=subprocess.run([str(BIN),str(self.folder/'f.smt2'),str(self.folder/'prefix.values'),str(self.folder/'seed.values'),mode,'1e-9','0',str(destination)],capture_output=True,text=True,timeout=10)
            self.assertEqual(r.returncode,0,r.stderr)
            raw=Raw(self.folder);data=parse_journals(raw,destination.name);raw.close();a=self.evaluate(data)
            self.assertEqual(a['actual_gain'],0);self.assertFalse(a['outputs'])
            self.assertGreater(a['initialization_seconds'],1e-9)
            self.assertGreater(a['session_overrun_seconds'],0)

    def test_already_submitted_seed_counted_once(self):
        prefix=self.folder/'prefix-with-seed.values';prefix.write_text('0 0 0\n1 0 0\n')
        destination=self.folder/'already-submitted'
        r=subprocess.run([str(BIN),str(self.folder/'f.smt2'),str(prefix),str(self.folder/'seed.values'),'global_pack','1e-9','0',str(destination)],capture_output=True,text=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr)
        initial=json.loads((destination/'initial.json').read_text())
        self.assertFalse(initial['seed_added']);self.assertEqual(initial['coverage'],self.data['global_pack']['initial.json']['coverage'])
        self.assertEqual(initial['new_outputs'],0)

    def test_no_overwrite_or_malformed_input(self):
        command=[str(BIN),str(self.folder/'f.smt2'),str(self.folder/'prefix.values'),str(self.folder/'seed.values'),'global_pack','1','0',str(self.folder/'global_pack')]
        before=(self.folder/'global_pack'/'summary.json').read_bytes()
        self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)
        self.assertEqual(before,(self.folder/'global_pack'/'summary.json').read_bytes())
        bad=self.folder/'bad.values';bad.write_text('0 0\n');command[3]=str(bad);command[-1]=str(self.folder/'bad-model')
        self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)
        command[3]=str(self.folder/'seed.values');command[5]='nan';command[-1]=str(self.folder/'bad-budget')
        self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)

    def test_first_observation_pairing_and_independent_state_copies(self):
        formula='(declare-const b Bool)(declare-const x Int)(assert (or b (>= x 0)))'
        model=lambda b,x:[dict(name='b',sort='Bool',value=b),dict(name='x',sort='Int',value=str(x))]
        with no_search():
            metric=FrozenCoverage(formula);base=metric.apply(metric.empty,model(True,0))
            original=metric.export(base);x=next(i for i,n in enumerate(metric.nodes) if str(n)=='x')
            self.assertFalse(remaining(metric,base,[x,0,True]))
            first=metric.apply(base,model(False,0));second=metric.apply(first,model(False,1))
            alone=metric.apply(base,model(False,1))
            self.assertEqual(metric.export(base),original)
            self.assertTrue(remaining(metric,first,[x,0,True]))
            self.assertFalse(remaining(metric,second,[x,0,True]))
            self.assertGreater(metric.count(second),metric.count(alone))
            self.assertGreaterEqual(observations(metric,base,first)[1],64)

    def test_deadline_predicate_rejects_nonfinite_and_negative(self):
        self.assertTrue(timely(1,1));self.assertFalse(timely(1.01,1))
        self.assertFalse(timely(float('nan'),1));self.assertFalse(timely(-1,1))

if __name__=='__main__':unittest.main()
