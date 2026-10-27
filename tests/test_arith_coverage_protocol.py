"""Protocol regressions: fixed six physical CPUs, gates, and offline-only analysis."""
import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from run_arith_coverage import STAGES,CONFIGS,topology
from analyze_arith_coverage import summaries
class Protocol(unittest.TestCase):
    def test_frozen_sizes(self):
        self.assertEqual([len(s)*len(c)*len(i) for _,s,c,i in STAGES.values()],[6,12,84])
        self.assertEqual([v[0] for v in STAGES.values()],[10,30,120])
        sets=[set(v[1]) for v in STAGES.values()]
        self.assertEqual(sum(map(len,sets)),len(set.union(*sets)))
    def test_physical_separation(self):
        workers,supervisor,rows=topology();ids={r['cpu']:(r['package'],r['physical_core']) for r in rows}
        self.assertEqual(len(workers),6);self.assertEqual(len({ids[c] for c in workers+[supervisor]}),7)
    def test_zero_and_losses_retained(self):
        r=summaries([0,0,-9,1,2,3,4]);self.assertEqual((r['n'],r['wins'],r['ties'],r['losses'],r['worst']),(7,4,2,1,-9))
if __name__=='__main__':unittest.main()
