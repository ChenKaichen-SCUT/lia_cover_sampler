#!/usr/bin/env python3
"""Additional descriptive structure tables, using already saved observations only."""
import argparse,csv,json,time
from collections import Counter
from pathlib import Path
from highdiv_gap_common import *
from reevaluate_round6_saved_candidates import dump,write_csv
from analyze_highdiv_complementarity_gap import Table,changes
from prepare_highdiv_complementarity_gap import OUT

def describe(out):
    all_summary=[]
    for short in ['random','real']:
        dest=out/'analysis'/short;meta=json.loads((dest/'structure.json').read_text());jobnames=json.loads((dest/'complete.json').read_text())['runs']
        tab=Table(dest/'actual_transitions.csv.gz');details=[];summary=[];selected=load_gz(dest/'selected_subsets.jsonl.gz')
        # A fixed-first witness also certifies the less constrained free-first
        # problem. This does not change either frozen heuristic path.
        certificates=[]
        for s in selected:
            eligible=[r for r in selected if all(r[k]==s[k] for k in ['native_run','cutoff','k']) and (not s['keep_first'] or r['keep_first'])]
            best=max(eligible,key=lambda r:r['lower_bound'])
            pool_upper=max(r['upper_bound'] for r in selected if all(r[k]==s[k] for k in ['native_run','cutoff','k']))
            verdict=selection_verdict(best['lower_bound'],s['upper_bound'],pool_upper,s['highdiv_count'])
            certificates.append({k:s[k] for k in ['native_run','cutoff','k','keep_first','highdiv_count','upper_bound']}|dict(certified_lower_bound=best['lower_bound'],witness_path_keep_first=best['keep_first'],witness_state_id=best['state_id'],verdict=verdict))
        write_csv(dest/'selection_certificates.csv',certificates)
        for name in jobnames:
            rows=load_run(out/'observations'/name);state=(0,0);values=[];zero=[];sameobs=0;groups=meta['global_equation_groups'];groupof={v:i for i,g in enumerate(groups) for v in g}
            prev=None
            for r in rows:
                gm=gain_mask(state,r['obs']).bit_count();newobs=observed(add(state,r['obs']))-observed(state)
                changed=[]
                if prev:
                    before={v['name']:v['value'] for v in prev['model']};changed=[v['name'] for v in r['model'] if v['value']!=before[v['name']]]
                    values.append(len(changed))
                    if gm==0:zero.append(len(changed))
                    sameobs+=r['obs']==prev['obs']
                tab.put(dict(run=name,index=r['index'],model_id=r['model_id'],reference_model_id=prev['model_id'] if prev else '',completion=r['completion'],base_id=obs_id(state),covered_gain=gm,new_observations=newobs,changed_variables=len(changed) if prev else None,changed_names=json.dumps(changed),global_groups_changed=json.dumps(sorted({groupof[v] for v in changed})),same_observations_as_previous=(r['obs']==prev['obs']) if prev else False))
                state=add(state,r['obs']);prev=r
            rr=dict(run=name,models=len(rows),zero_gain_after_first=len(zero),adjacent_same_observation=sameobs,median_changed_variables=quantile(values,.5),max_changed_variables=max(values,default=0),zero_gain_median_changed_variables=quantile(zero,.5),zero_gain_max_changed_variables=max(zero,default=0),distinct_observation_sets=len({r['observation_id'] for r in rows}))
            summary.append(rr);all_summary.append(rr)
            # Four pre-defined representative selection conditions per native run.
            for s in selected:
                if s['native_run']!=name or s['k']!=10 or s['cutoff'] not in [30,900]:continue
                ids=s['selected_indices'];first,second=rows[ids[0]-1],rows[ids[1]-1]
                d=changes(meta,first,second);d.update(dict(run=name,cutoff=s['cutoff'],keep_first=s['keep_first'],k=10,subset_state_id=s['state_id'],first_index=first['index'],second_index=second['index'],pair_covered=covered(add(first['obs'],second['obs'])).bit_count(),pair_gain_mask=hex(covered(add(first['obs'],second['obs']))),latest_pair_seconds=max(first['completion'],second['completion'])))
                details.append(d)
        tab.close();write_csv(dest/'actual_transition_summary.csv',summary);dump_gz(dest/'selected_pair_structures.jsonl.gz',details)
    dump(out/'structure_complete.json',dict(runs=len(all_summary),search_calls=0))

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--out',type=Path,default=OUT);v=a.parse_args();describe(v.out)
