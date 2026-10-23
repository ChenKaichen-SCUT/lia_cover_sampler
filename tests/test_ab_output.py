import hashlib,json,os,subprocess,tempfile,unittest
from pathlib import Path
from test_suite import ROOT
from validation import original_formula,verify
from coverage_differential_test import differential,load_metric
BIN=Path(os.environ.get('LIA_AB_BINARY',ROOT/'build/ab_sampler'))
REPLAY=BIN.with_name('ab_replay')
FROZEN=ROOT/'build/round2/control_ab_fixed/lia_cover_sampler'
FORMULA=''.join(f'(declare-const x{i} Int)(assert (and (<= -1 x{i})(<= x{i} 0)))' for i in range(6))

class ABOutputIsolation(unittest.TestCase):
    def run_sampler(self,binary,formula,out,seed=0,polish=None):
        cmd=[str(binary),'-i',str(formula),'-o',str(out),'-n','16','-t','10','-s',str(seed),'--mode','debug']
        if polish is not None:cmd+=['--output-polish','true' if polish else 'false']
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        return [json.loads(s) for s in (out/'models.jsonl').read_text().splitlines()]
    def test_frozen_source_and_disabled_adapter_decisions(self):
        manifest=json.loads((ROOT/'vendor/frozen_ab/manifest.json').read_text())
        for path,sha in manifest['files'].items():
            self.assertEqual(hashlib.sha256((ROOT/'vendor/frozen_ab'/path).read_bytes()).hexdigest(),sha)
        self.assertEqual(hashlib.sha256(FROZEN.read_bytes()).hexdigest(),manifest['frozen_binary_sha256'])
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);formula=tmp/'input.smt2';formula.write_text(FORMULA)
            for seed in (0,1,2):
                old=self.run_sampler(FROZEN,formula,tmp/f'old-{seed}',seed)
                new=self.run_sampler(BIN,formula,tmp/f'new-{seed}',seed,False)
                self.assertEqual([(r['assignment'],r['category'],r['covered']) for r in old],
                                 [(r['assignment'],r['category'],r['covered']) for r in new])
                old_stats=json.loads((tmp/f'old-{seed}/summary.json').read_text())['statistics']['counts']
                new_stats=json.loads((tmp/f'new-{seed}/summary.json').read_text())['statistics']['counts']
                new_stats.pop('raw_generator_isolation_checks',None)
                self.assertEqual(old_stats,new_stats)
    def test_enabled_polish_keeps_generator_state_and_valid_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);formula=tmp/'input.smt2';formula.write_text(FORMULA)
            out=tmp/'polish';rows=self.run_sampler(BIN,formula,out,0,True)
            raw=[json.loads(s) for s in (out/'raw_generator.jsonl').read_text().splitlines()]
            self.assertEqual(rows[0]['assignment'],raw[0]['assignment'])
            self.assertTrue(all(verify(original_formula(formula),r['assignment'])=='valid' for r in rows))
            differential(formula,[r['assignment'] for r in rows],BIN.parent/'lia_cover_sampler')
            summary=json.loads((out/'summary.json').read_text())
            self.assertGreater(summary['statistics']['counts'].get('raw_generator_isolation_checks',0),0)
            self.assertEqual(summary['raw_generator_samples'],len(raw));self.assertEqual(summary['emitted_samples'],len(rows))
            self.assertEqual(summary['output_statistics']['counts']['first_raw_emitted_without_polish'],1)
            self.assertLessEqual(summary['extra_seconds'],summary['extra_allowance_seconds']+summary['extra_budget_overrun_seconds']+1e-6)
            self.assertFalse(any(k.startswith('line_') for k in summary.get('refinement_statistics',{}).get('counts',{})))
    def test_fixed_raw_prefix_preservation_across_filenames(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);formula=tmp/'input.smt2';formula.write_text(FORMULA)
            raw=self.run_sampler(FROZEN,formula,tmp/'source',2)
            values=tmp/'values';values.write_text(''.join(' '.join(r['value'] for r in m['assignment'])+'\n' for m in raw))
            times=tmp/'times';times.write_text(''.join(str(i+1)+'\n' for i in range(len(raw))))
            results=[]
            for label in ('original','renamed'):
                file=tmp/(label+'.smt2');file.write_text(FORMULA);out=tmp/label
                r=subprocess.run([str(REPLAY),str(file),str(values),str(times),str(out),'3'],capture_output=True,text=True,timeout=30)
                self.assertEqual(r.returncode,0,r.stdout+r.stderr)
                proof=[json.loads(s) for s in (out/'prefix_preservation.jsonl').read_text().splitlines()]
                self.assertEqual(len(proof),len(raw));self.assertTrue(all(p['observations_preserved'] and p['emitted_covered']>=p['raw_covered'] for p in proof))
                results.append(json.loads((out/'summary.json').read_text())['output_statistics']['counts'])
            # Timed polish may legitimately choose different candidates, especially
            # under ASan. Pure filename-independent gate inputs are tested in C++.
            self.assertEqual(len(results),2)
