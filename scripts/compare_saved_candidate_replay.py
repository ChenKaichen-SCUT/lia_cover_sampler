#!/usr/bin/env python3
"""Compare independent offline replays, excluding only new measurement timings."""
import argparse,csv,gzip,json
from pathlib import Path
from audit_saved_candidate_results import read
from reevaluate_round6_saved_candidates import require,dump,file_hash

POOL_TIMINGS={'scoring_seconds','maintenance_seconds','observation_build_charge_seconds',
              'charged_pool_seconds','independent_verification_seconds'}
AUDIT_TIMINGS={'offline_seconds','legacy_audit_seconds','cached_observation_seconds',
               'evaluator_initialization_seconds'}
TABLES=('candidates','selection_events','residual_changes','sources','queries',
        'original_trajectory','simulated_trajectory','pool_sessions','pool_sources','pool_lifecycle')

def main():
    p=argparse.ArgumentParser();p.add_argument('original',type=Path);p.add_argument('replay',type=Path);a=p.parse_args()
    counts={}
    for rn in (10,11):
        for name in TABLES:
            x,y=(read(b/f'round{rn}'/(name+'.csv')) for b in (a.original,a.replay))
            omitted=POOL_TIMINGS if name=='pool_sessions' else set()
            require([{k:v for k,v in r.items() if k not in omitted} for r in x]==
                    [{k:v for k,v in r.items() if k not in omitted} for r in y],f'round{rn}/{name}: non-timing replay mismatch')
            counts[f'round{rn}/{name}']=len(x)
        for path in (a.original/f'round{rn}/sessions').glob('*/model_catalog.jsonl.gz'):
            other=a.replay/path.relative_to(a.original)
            require(gzip.decompress(path.read_bytes())==gzip.decompress(other.read_bytes()),'model catalog replay mismatch')
    x,y=(read(b/'session_audit.csv') for b in (a.original,a.replay))
    require([{k:v for k,v in r.items() if k not in AUDIT_TIMINGS} for r in x]==
            [{k:v for k,v in r.items() if k not in AUDIT_TIMINGS} for r in y],'audit replay mismatch')
    for name in ('prefixes','pairs','paired_summary'):
        require(read(a.original/(name+'.csv'))==read(a.replay/(name+'.csv')),name+' exact replay')
    require(json.loads((a.original/'decision.json').read_text())==json.loads((a.replay/'decision.json').read_text()),'decision exact replay')
    dump(a.original/'REPLAY_AUDIT.json',dict(all_passed=True,sessions=len(x),table_rows=counts,
        ignored_new_timing_columns=sorted(POOL_TIMINGS|AUDIT_TIMINGS),
        historical_query_costs_compared=True,main_results_not_replaced=True,
        second_run=json.loads((a.replay/'COMPLETE.json').read_text()),
        script_sha256=file_hash(__file__)))
    print(json.dumps(dict(all_passed=True,sessions=len(x),compared_rows=sum(counts.values()))))
if __name__=='__main__':main()
