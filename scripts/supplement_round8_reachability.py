#!/usr/bin/env python3
"""Offline time-weighted reachable gaps, updating observations even on zero-gain output.
Original segment-start classification and all original experiment scores are retained.
"""
import argparse,json,csv
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,dump,write_csv
from analyze_plateaus import diagnose
from analyze_early_coverage import ROOT

def work(args):
    base,record=args;base=Path(base);d=base/record['output'];inp=json.loads((d/'initial.json').read_text());refdir=ROOT/'results/round8_analysis/cases'/record['instance'];reference=[list(map(int,x)) for x in json.loads((refdir/'reference.json').read_text())['states']];witnesses=defaultdict(list)
    for w in json.loads((refdir/'pool_witnesses.json').read_text()):witnesses[w['node']].append(w)
    receipts={x['id']:x for x in map(json.loads,(d/'commits.jsonl').read_text().splitlines())};models=[x for x in map(json.loads,(d/'models.jsonl').read_text().splitlines()) if receipts[x['id']]['timely']];intervals=[];known=0
    with no_search():
        metric=FrozenCoverage(Path(record['formula']).read_text());state=metric.empty.copy();start=0;index=0;covered=0
        for model in models+[None]:
            end=receipts[model['id']]['completion'] if model else record['seconds'];diagnosis=diagnose([[int(state[n.get_id()][1]),int(state[n.get_id()][0])] for n in metric.nodes],reference,witnesses);duration=end-start
            known+=duration*(diagnosis['reachability']=='PLATEAU_WITH_KNOWN_REACHABLE_GAP');intervals.append(dict(id=record['id'],instance=record['instance'],mode=record['mode'],seed=record['seed'],start=start,end=end,duration=duration,after_formal_index=index,covered=covered,**diagnosis))
            if model:state=metric.apply(state,model['model']);covered=metric.count(state);index+=1;start=end
    return dict(id=record['id'],instance=record['instance'],seed=record['seed'],mode=record['mode'],known_gap_seconds=known,known_gap_fraction=known/record['seconds'],definition='each interval uses full observations after preceding formal output; initial search starts empty'),intervals

def main():
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);a=p.parse_args();records=[json.loads(x) for x in (a.input/'runs.jsonl').read_text().splitlines()];rows=[];intervals=[]
    with ProcessPoolExecutor(max_workers=9) as pool:
        for r,rs in pool.map(work,[(str(a.input),r) for r in records]):rows.append(r);intervals+=rs
    write_csv(a.input/'reachability_time_summary.csv',rows);write_csv(a.input/'reachability_intervals.csv',intervals)
    dump(a.input/'reachability_supplement.json',dict(search_calls=0,original_scores_unchanged=True,original_segments_unchanged=True,reason='zero-gain initial model can change full observations and create a known reachable gap inside a no-growth segment'))
if __name__=='__main__':main()
