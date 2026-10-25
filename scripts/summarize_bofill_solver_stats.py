#!/usr/bin/env python3
import json,re
from pathlib import Path
from reevaluate_round6_saved_candidates import write_csv
ROOT=Path(__file__).resolve().parents[1]
for experiment in ['first_sat_30s','first_sat_120s']:
    out=ROOT/'results/bofill_diagnosis'/experiment;plan=json.loads((out/'plan.json').read_text());rows=[]
    for r in plan['runs']:
        d=out/r['directory'];qs=list(map(json.loads,(d/'raw/queries.jsonl').read_text().splitlines()));last=qs[-1] if qs else {};resource=(d/'resource.txt').read_text()
        def field(name):
            match=re.search(r'^\s*'+re.escape(name)+r':\s*(.*)$',resource,re.M);return match.group(1) if match else None
        row={k:r[k] for k in ['formula','seed','mode','control']};row.update(query_count=len(qs),last_raw_status=last.get('evidence',{}).get('raw_status'),last_statistics_read_at=last.get('statistics_read_at'),user_cpu_seconds=field('User time (seconds)'),system_cpu_seconds=field('System time (seconds)'),max_rss_kib=field('Maximum resident set size (kbytes)'),exit_status=field('Exit status'))
        row.update({'z3_'+k:v for k,v in last.get('statistics',{}).items()});rows.append(row)
    write_csv(out/'analysis/final_solver_statistics.csv',rows)
