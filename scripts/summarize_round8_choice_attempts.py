#!/usr/bin/env python3
"""Read saved choice queries and formal catalog visits; never generate models."""
import argparse,collections,csv,json
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);a=p.parse_args();rows=[]
    for r in map(json.loads,(a.input/'runs.jsonl').read_text().splitlines()):
        if r['mode']!='micro_macro':continue
        d=a.input/r['output'];catalog=json.loads((d/'catalog_final.json').read_text());groups=catalog['groups'] if catalog else []
        queries=[q for rnd in map(json.loads,(d/'rounds.jsonl').read_text().splitlines()) for q in rnd['broad_queries'] if q['origin']=='macro_choice']
        counts=collections.Counter(tuple(x) for q in queries for x in q['choices']);n=sum(len(g['alternatives']) for g in groups)
        rows.append(dict(id=r['id'],instance=r['instance'],seed=r['seed'],catalog_groups=len(groups),catalog_alternatives=n,unique_queried_alternatives=len(counts),unqueried_alternatives=n-len(counts),formally_visited_alternatives=sum(x['visits']>0 for g in groups for x in g['alternatives']),formally_unvisited_alternatives=sum(x['visits']==0 for g in groups for x in g['alternatives']),query_calls=len(queries),max_queries_to_one_alternative=max(counts.values(),default=0),per_group_actual_mode_counts=json.dumps([g['visited_modes'] for g in groups])))
    with (a.input/'choice_attempt_summary.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
if __name__=='__main__':main()
