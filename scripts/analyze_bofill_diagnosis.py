#!/usr/bin/env python3
"""Descriptive paired analysis; right-censored failures are not dropped or called UNSAT."""
import argparse,json,statistics
from pathlib import Path
from reevaluate_round6_saved_candidates import dump,write_csv

def analyze(out):
    out=Path(out);plan=json.loads((out/'plan.json').read_text());rows=[];queries=[];counters=[]
    for spec in plan['runs']:
        raw=out/spec['directory']/'raw';s=json.loads((raw/'summary.json').read_text());p=json.loads((out/spec['directory']/'process_result.json').read_text());c=json.loads((raw/'structure.json').read_text());times=s['stats']['seconds'];counts=c['statistics']['counts']
        base={k:spec[k] for k in ['formula','mode','seed','control','seconds']}
        row=dict(**base,terminal=s['terminal'],success=s['terminal']=='SAT',first_sat_seconds=s['first_sat_seconds'] if s['first_sat_seconds']>=0 else None,first_legal_seconds=s['first_legal_seconds'] if s['first_legal_seconds']>=0 else None,completion=s['completion'] if s['completion']>=0 else None,process_wall_seconds=p['process_wall_seconds'],elapsed=s['elapsed'],cpu_seconds=s['cpu_seconds'],queries=s['queries'],check_seconds=times.get('check',0),compile_seconds=times['compile'],load_seconds=times['load'],structure_write_seconds=times['structure_write'],initialization_seconds=times['solver_initialization'],statistics_seconds=times.get('statistics',0),compiled_nodes=counts['compiled_dag_nodes'],solving_variables=counts['solving_variables'],eliminations=counts.get('unit_equation_eliminations',0),compile_stop=c['stop_reason'])
        rows.append(row)
        for q in map(json.loads,(raw/'queries.jsonl').read_text().splitlines()):
            queries.append(dict(**base,query=q['query'],start=q['start'],finished=q['finished'],**q['evidence'],statistics_seconds=q['statistics_seconds']))
            for key,value in q['statistics'].items():counters.append(dict(**base,query=q['query'],state=q['evidence']['raw_status'],read_at=q['statistics_read_at'],key=key,value=value))
    paired=[]
    for r in rows:
        if r['mode']=='plain':continue
        p=next(x for x in rows if (x['formula'],x['seed'],x['control'],x['mode'])==(r['formula'],r['seed'],r['control'],'plain'))
        comparable=r['success'] and p['success']
        paired.append(dict(formula=r['formula'],seed=r['seed'],control=r['control'],mode=r['mode'],variant_success=r['success'],plain_success=p['success'],first_legal_difference=r['first_legal_seconds']-p['first_legal_seconds'] if comparable else None,ratio=r['first_legal_seconds']/p['first_legal_seconds'] if comparable else None,relation=('slower' if r['first_legal_seconds']>p['first_legal_seconds'] else 'faster') if comparable else 'variant_failed' if p['success'] else 'plain_failed' if r['success'] else 'both_censored'))
    grouped=[]
    for key in sorted({(r['formula'],r['mode'],r['control']) for r in rows}):
        rs=[r for r in rows if (r['formula'],r['mode'],r['control'])==key];ts=[r['first_legal_seconds'] for r in rs if r['success']]
        grouped.append(dict(formula=key[0],mode=key[1],control=key[2],runs=len(rs),sat=len(ts),failures=len(rs)-len(ts),successful_first_legal_mean=statistics.mean(ts) if ts else None,successful_first_legal_median=statistics.median(ts) if ts else None,all_run_cost_mean=statistics.mean(r['elapsed'] for r in rs)))
    out.joinpath('analysis').mkdir(exist_ok=True)
    for name,data in [('sessions',rows),('queries',queries),('solver_statistics',counters),('paired_to_plain',paired),('grouped',grouped)]:write_csv(out/'analysis'/f'{name}.csv',data)
    dump(out/'analysis/summary.json',dict(sessions=rows,groups=grouped,paired=paired,failure_policy='right-censored; no invented first-SAT value; all failures kept in all-run costs'))
    print(json.dumps(grouped,indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('out');a=p.parse_args();analyze(a.out)
