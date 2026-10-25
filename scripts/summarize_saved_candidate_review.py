#!/usr/bin/env python3
"""Frozen, session-paired descriptive analysis of Round 12 offline pools."""
import argparse
import csv
import gzip
import json
from collections import defaultdict
from pathlib import Path
import statistics

from review_saved_candidates import PREFIXES, CAPACITIES
from reevaluate_round6_saved_candidates import dump, write_csv, require, file_hash


def read(path):
    path = Path(path)
    if not path.exists():
        path = Path(str(path) + '.gz')
    with (gzip.open(path, 'rt') if path.suffix == '.gz' else path.open()) as f:
        return list(csv.DictReader(f))


def number(value):
    return None if value in ('', None) else float(value)


def gate(pairs):
    """R11 main confirmation, R10 never pooled as extra repeats."""
    final = {(r['instance'], r['seed'], r['capacity']):r for r in pairs
             if r['round']==11 and r['prefix']=='final' and r['contrast']=='all_minus_ordinary_retention'
             and r['mode']=='projection_fixed'}
    cases = sorted({k[0] for k in final})
    per_cap = []
    for cap in CAPACITIES:
        positive = []
        details = []
        for case in cases:
            ds = [final.get((case, seed, cap), {}).get('difference') for seed in (0,1)]
            other = [final.get((case, seed, 12-cap), {}).get('difference') for seed in (0,1)]
            early = []
            for prefix in (1,2,5,10):
                group = [r for r in pairs if r['round']==11 and r['instance']==case
                         and r['mode']=='projection_fixed' and r['capacity']==cap
                         and r['prefix']==str(prefix) and r['contrast']=='all_minus_ordinary_retention']
                if sum(r['difference'] is not None and r['difference']<0 for r in group)==2:
                    early.append(prefix)
            passes = all(x is not None and x>0 for x in ds) and all(x is not None and x>=0 for x in other) and not early
            if passes: positive.append(case)
            details.append(dict(instance=case, differences_seed_0_1=ds, other_capacity_differences=other,
                                repeated_early_regressions=early, passes=passes))
        per_cap.append(dict(capacity=cap, positive_formulas=positive, details=details))
    necessary = any(len(x['positive_formulas'])>=2 for x in per_cap)
    return dict(decision='Go_gate_requires_cost_review_only' if necessary else 'No-Go',
        necessary_gate_passed=necessary, by_capacity=per_cap,
        drop_projection_from_performance_mainline=not necessary,
        next_online_experiment_started=False,
        interpretation='offline saved-model potential only; no online wall-clock improvement established')


def main():
    p=argparse.ArgumentParser(); p.add_argument('input',type=Path); a=p.parse_args(); base=a.input
    require(json.loads((base/'COMPLETE.json').read_text())['all_passed'],'complete audit required')
    audit=read(base/'session_audit.csv')
    by_audit={(int(r['round']),r['session']):r for r in audit}
    pairs=[]; prefixes=[]; summaries=[]
    for rn in (10,11):
        folder=base/f'round{rn}'
        sessions=read(folder/'pool_sessions.csv')
        originals=read(folder/'original_trajectory.csv'); simulations=read(folder/'simulated_trajectory.csv')
        curves=defaultdict(dict)
        for r in originals+simulations:curves[r['session'],r['variant']][int(r['position'])]=r
        sess=defaultdict(dict)
        for s in sessions:sess[s['session']][s['variant']]=s
        for sid,variants in sess.items():
            ar=by_audit[rn,sid]; N=int(ar['original_outputs'])
            context={k:ar[k] for k in ('session','instance','seed','mode','snapshot')}
            context.update(round=rn,seed=int(context['seed']))
            for variant in ('original',*variants):
                row=variants.get(variant)
                if variant=='original':
                    summaries.append(dict(context,variant=variant,original_outputs=N,outputs=N,
                        equal_count=True,gain=int(ar['original_gain']),gain_difference=0,
                        historical_process_seconds=float(ar['historical_process_seconds']),
                        historical_all_query_seconds=float(ar['historical_query_seconds']),
                        historical_extra_seconds=float(ar['historical_extra_seconds']),
                        total=int(ar['total']),initial_covered=int(ar['initial_covered'])))
                else:
                    summaries.append(dict(row,round=rn,seed=int(row['seed']),
                        total=int(ar['total']),initial_covered=int(ar['initial_covered'])))
                for n in (*PREFIXES,'final'):
                    position=N if n=='final' else n
                    cr=curves[sid,variant].get(position)
                    exists=position==0 and n=='final' or cr is not None
                    actual=int(cr['outputs']) if cr else 0
                    complete=exists and actual==position
                    value=int(cr['cumulative']) if complete and cr else 0 if complete else None
                    prefixes.append(dict(context,variant=variant,prefix=str(n),position=position,
                        historical_position_exists=exists,outputs_at_position=actual if exists else None,
                        complete=complete,cumulative_gain=value,
                        covered=int(ar['initial_covered'])+value if value is not None else None,
                        denominator=int(ar['total']),
                        gain_percentage_points=100*value/int(ar['total']) if value is not None else None))
            lookup={(r['variant'],r['prefix']):r for r in prefixes if r['round']==rn and r['session']==sid}
            contrasts=[(variant,'original', 'pool_minus_original', int(variant.rsplit('_',1)[1])) for variant in variants]
            for cap in CAPACITIES:
                contrasts.append((f'all_{cap}',f'ordinary_retention_{cap}','all_minus_ordinary_retention',cap))
                if f'ordinary_strict_{cap}' in variants:
                    contrasts.append((f'all_{cap}',f'ordinary_strict_{cap}','all_minus_ordinary_strict',cap))
            for policy in ('all','ordinary_retention','ordinary_strict'):
                if f'{policy}_8' in variants:
                    contrasts.append((f'{policy}_8',f'{policy}_4','capacity_8_minus_4',0))
            for left,right,contrast,cap in contrasts:
                for n in (*PREFIXES,'final'):
                    x,y=lookup[left,str(n)],lookup[right,str(n)]
                    ds=x['cumulative_gain']-y['cumulative_gain'] if x['complete'] and y['complete'] else None
                    pairs.append(dict(context,contrast=contrast,left=left,right=right,capacity=cap,prefix=str(n),
                        left_complete=x['complete'],right_complete=y['complete'],
                        left_outputs=x['outputs_at_position'],right_outputs=y['outputs_at_position'],
                        left_gain=x['cumulative_gain'],right_gain=y['cumulative_gain'],difference=ds,
                        difference_percentage_points=100*ds/int(ar['total']) if ds is not None else None,
                        outcome='incomplete' if ds is None else 'win' if ds>0 else 'loss' if ds<0 else 'tie'))
        # Two targeted sets are selected by the user before analysis, not by gain.
        ev=read(folder/'selection_events.csv')
        focus=[r for r in ev if (rn==11 and 'Bromberger' in r['instance'] and r['extra_origin']=='projection' and r['strict_extra_winner']=='True')
               or (rn==11 and 'convert' in r['instance'] and r['extra_origin']=='projection')]
        if rn==11:
            require(sum('Bromberger' in r['instance'] for r in focus)==77,'77 predeclared strict winners')
            require(sum('convert' in r['instance'] for r in focus)==2,'two predeclared convert projections')
            write_csv(folder/'focus_77_plus_2.csv',focus)
    grouped=defaultdict(list)
    for r in pairs:
        grouped[tuple(r[k] for k in ('round','instance','mode','contrast','left','right','capacity','prefix'))].append(r)
    aggregates=[]
    for keys,rr in grouped.items():
        ds=[r['difference'] for r in rr if r['difference'] is not None]
        aggregates.append(dict(zip(('round','instance','mode','contrast','left','right','capacity','prefix'),keys),
            sessions=len(rr),paired_complete=len(ds),incomplete=len(rr)-len(ds),
            wins=sum(x>0 for x in ds),ties=sum(x==0 for x in ds),losses=sum(x<0 for x in ds),
            mean_difference=statistics.mean(ds) if ds else None,
            median_difference=statistics.median(ds) if ds else None,
            min_difference=min(ds) if ds else None,max_difference=max(ds) if ds else None))
    write_csv(base/'sessions.csv',summaries)
    write_csv(base/'prefixes.csv',prefixes)
    write_csv(base/'pairs.csv',pairs)
    write_csv(base/'paired_summary.csv',aggregates)
    decision=gate(pairs);dump(base/'decision.json',decision)
    dump(base/'ANALYZED.json',dict(script_sha256=file_hash(__file__),
        sessions=len(audit),session_variants=len(summaries),paired_rows=len(pairs),
        paired_unit='historical session within round and formula; prefixes are correlated',
        zero_and_incomplete_sessions_retained=True))
    print(json.dumps(decision,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
