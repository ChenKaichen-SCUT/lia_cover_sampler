import unittest
from analyze_native_delivery_results import gate_900
from run_native_delivery_pairs import tasks_for_stage,CONFIGS

def evidence(mode='position_immediate'):
    rows=[]
    for f in ['random','real']:
        for track,dimension,points in [('delivery','time',[30,60,120]),('generation','prefix',[10,100]),('delivery','prefix',[10,100])]:
            for k in points:rows.append(dict(formula=f,track=track,dimension=dimension,checkpoint=k,left=mode,right='first_immediate',pairs=3,wins=2,ties=1,losses=0,median_delta=1,total=100))
    return rows
class NativeDeliveryAnalysis(unittest.TestCase):
    def test_only_cross_formula_repeated_positive_signal_promotes(self):
        rows=evidence();self.assertTrue(gate_900(rows,CONFIGS,['random','real'])['enter_900'])
        next(r for r in rows if r['formula']=='real' and r['dimension']=='time' and r['checkpoint']==120)['wins']=1
        self.assertFalse(gate_900(rows,CONFIGS,['random','real'])['enter_900'])
    def test_incomplete_prefix_or_systematic_regression_stops(self):
        for field,value in [('pairs',2),('losses',2)]:
            rows=evidence();next(r for r in rows if r['track']=='generation' and r['checkpoint']==100)[field]=value
            self.assertFalse(gate_900(rows,CONFIGS,['random','real'])['enter_900'])
    def test_matrices_and_unused_long_seeds(self):
        inputs=[dict(name='random'),dict(name='real')]
        for stage,n in [('exposure',8),('screen',24),('long',8)]:
            groups=tasks_for_stage(inputs,stage,'position_immediate');self.assertTrue(all(len(g)==4 for g in groups));self.assertEqual(sum(map(len,groups)),n)
            if stage=='long':self.assertEqual({s for g in groups for _,s,_ in g},{3,4})
        with self.assertRaises(AssertionError):tasks_for_stage(inputs,'long')
