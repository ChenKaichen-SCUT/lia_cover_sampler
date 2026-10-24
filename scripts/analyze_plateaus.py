#!/usr/bin/env python3
"""Native PlateauTracker replay and known-reachable evidence from saved models only."""
import argparse,csv,gzip,json,subprocess
from pathlib import Path
from collections import defaultdict
from reevaluate_round6_saved_candidates import dump,write_csv,require,file_hash
from analyze_early_coverage import ROOT,BATCHES

def diagnose(state,reference,witnesses):
    gaps=[]
    for n,((zero,one),(r0,r1)) in enumerate(zip(state,reference)):
        need0=one&~zero&r0;need1=zero&~one&r1
        if need0 or need1:gaps.append((n,need0,need1))
    if not gaps:return dict(reachability='REACHABILITY_UNKNOWN',known_missing_bits=0,witness_mode='',witness_index='',witness_hash='',witness_node='',witness_bit='',witness_value='')
    total=sum(a.bit_count()+b.bit_count() for n,a,b in gaps);n,a,b=gaps[0]
    w=next(w for w in witnesses[n] if int(w['zero'])&a or int(w['one'])&b);v=bool(int(w['one'])&b);mask=int(w['one'])&b if v else int(w['zero'])&a
    return dict(reachability='PLATEAU_WITH_KNOWN_REACHABLE_GAP',known_missing_bits=total,witness_mode=w['mode'],witness_index=w['index'],witness_hash=w['model_hash'],witness_node=n,witness_bit=(mask&-mask).bit_length()-1,witness_value=int(v))

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,default=ROOT/'results/round8_analysis');p.add_argument('--probe',type=Path,default=ROOT/'build/round8/release/choice_catalog_probe');a=p.parse_args();require((a.input/'COMPLETE.json').exists(),'finish replay first')
    cases=json.loads((ROOT/'benchmarks/manifest.json').read_text());segments=[];summaries=[]
    for case in cases:
        folder=a.input/'cases'/case['path'];reference=[list(map(int,x)) for x in json.loads((folder/'reference.json').read_text())['states']];witnesses=defaultdict(list)
        for w in json.loads((folder/'pool_witnesses.json').read_text()):witnesses[w['node']].append(w)
        for mode in BATCHES:
            with gzip.open(folder/(mode+'.samples.jsonl.gz'),'rt') as f:rows=[json.loads(x) for x in f]
            native='900\n'+''.join(f"{r['seconds']:.17g} {r['index']} {r['gain']} {int(r['new_mode'])} {mode}\n" for r in rows)
            result=json.loads(subprocess.check_output([str(a.probe),'--plateau'],input=native,text=True));dump(folder/(mode+'.plateaus.json'),result)
            with gzip.open(folder/(mode+'.updates.jsonl.gz'),'rt') as f:updates=iter([json.loads(x) for x in f])
            update=next(updates,None);state=[[0,0] for _ in reference];known_seconds=0;zero_modes=0
            for i,seg in enumerate(result['segments']):
                while update and update['index']<=seg['start_index']:
                    for n,z,o in update['changes']:state[n]=[int(z),int(o)]
                    update=next(updates,None)
                diagnosis=diagnose(state,reference,witnesses);duration=seg['end']-seg['start'];known_seconds+=duration*(diagnosis['reachability']=='PLATEAU_WITH_KNOWN_REACHABLE_GAP')
                section=rows[seg['start_index']:seg['end_index']];zero_changed=sum(r['mode_changed'] and not r['gain'] for r in section);zero_modes+=zero_changed
                segments.append(dict(instance=case['path'],mode=mode,segment=i,**{k:v for k,v in seg.items() if k!='costs'},duration=duration,
                    output_behavior='distinct_zero_gain_outputs' if seg['zero_outputs'] else 'no_zero_gain_formal_output',zero_gain_mode_changes=zero_changed,
                    recovery_mode_changed=section[-1]['mode_changed'] if section and not seg['right_censored'] else None,internal_costs='unknown',**diagnosis))
            tail=result['segments'][-1]
            summaries.append(dict(instance=case['path'],mode=mode,models=len(rows),positive_outputs=sum(r['gain']>0 for r in rows),zero_outputs=sum(r['gain']==0 for r in rows),
                zero_rate=sum(r['gain']==0 for r in rows)/len(rows),covered=rows[-1]['covered'],percent=100*rows[-1]['covered']/rows[-1]['total'],
                longest_zero_run=result['longest_zero_run'],longest_no_growth_seconds=result['longest_no_growth'],last_positive_index=result['last_positive_index'],last_positive_seconds=result['last_positive_time'],
                tail_seconds=tail['end']-tail['start'],tail_zero_outputs=tail['zero_outputs'],tail_right_censored=True,known_reachable_gap_seconds=known_seconds,known_reachable_gap_time_fraction=known_seconds/900,
                zero_gain_mode_changes=zero_modes,internal_failures='unknown',internal_duplicates='unknown',terminal='original_timeout_or_budget_cutoff'))
    write_csv(a.input/'plateau_segments.csv',segments);write_csv(a.input/'plateau_summary.csv',summaries)
    dump(a.input/'plateau_audit.json',dict(native_tracker_probe_sha256=file_hash(a.probe),runs=len(summaries),segments=len(segments),reference='all four validated historical formal streams; future information OFFLINE ONLY',search_calls=0))
if __name__=='__main__':main()
