"""Scheduler contract tests: synthetic group completion, no solver search."""
import os,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_soft_phase_pairs import execute_groups,slot_layout
class PairedQueue(unittest.TestCase):
    def simulate(self,count):
        running=set();seen=[];finished=[];max_active=0;polls={};snapshots=[]
        def launch(group,job,slot,cores):
            nonlocal max_active
            self.assertNotIn(slot,running);running.add(slot);max_active=max(max_active,len(running));seen.append((group,slot,tuple(cores)));polls[group]=0
            return dict(group=group,slot=slot)
        def collect(batch):
            g=batch['group'];polls[g]+=1
            # Group 1 frees a slot before group 0: queued group must refill it.
            return polls[g]>=({0:5,1:1}.get(g,3))
        def release(batch,abort=False):
            self.assertFalse(abort);running.remove(batch['slot']);finished.append(batch['group'])
        def progress(active,queued,n):snapshots.append((len(active),queued,n))
        with patch('run_soft_phase_pairs.time.sleep'):
            execute_groups(list(range(count)),[[i*3+j for j in range(3)] for i in range(5)],launch,collect,release,progress)
        self.assertFalse(running);self.assertEqual(sorted(finished),list(range(count)))
        return seen,max_active,snapshots
    def test_eighteen_sessions_fill_fifteen_slots_then_refill_complete_triplet(self):
        seen,maximum,states=self.simulate(6)
        self.assertEqual(maximum*3,15);self.assertEqual(seen[5][1],1)
        self.assertTrue(any(queued==1 for _,queued,_ in states))
    def test_six_exposure_sessions_use_only_two_triplets(self):
        seen,maximum,states=self.simulate(2);self.assertEqual(maximum*3,6)
    def test_machine_topology_fifteen_unique_logical_cpus_and_distinct_triplet_cores(self):
        allowed=os.sched_getaffinity(0)
        if len(allowed)<15:self.skipTest('runner host requires fifteen permitted logical CPUs')
        slots,topology,spare=slot_layout(allowed);cpus=[c for slot in slots for c in slot]
        self.assertEqual(len(cpus),15);self.assertEqual(len(set(cpus)),15)
        by_cpu={x['cpu']:(x['package'],x['physical_core']) for x in topology}
        for slot in slots:self.assertEqual(len({by_cpu[c] for c in slot}),3)
        self.assertFalse(set(spare)&set(cpus))
if __name__=='__main__':unittest.main()
