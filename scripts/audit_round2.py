#!/usr/bin/env python3
"""Validate all new samples with a shared worker pool; no sampler generation."""
import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
import json
from pathlib import Path
from audit_experiments import audit_job

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("root",type=Path)
    p.add_argument("--jobs",type=int,default=15)
    a=p.parse_args()
    if not 1<=a.jobs<=15:p.error("jobs must be 1..15")
    work=[]
    for folder in sorted(a.root.iterdir()):
        if not folder.is_dir() or not (folder/"COMPLETE.json").exists():continue
        manifest=json.loads((folder/"manifest.json").read_text())
        for line in (folder/"runs.jsonl").read_text().splitlines():
            work.append((str(folder.resolve()),json.loads(line),manifest["variant"],manifest["seconds"],False))
    reports=defaultdict(list)
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:
        futures={pool.submit(audit_job,payload):payload[0] for payload in work}
        for future in as_completed(futures):
            reports[futures[future]].append(future.result())
    errors=[]
    for name,rows in reports.items():
        folder=Path(name);rows.sort(key=lambda row:(row["instance"],row["seed"]))
        (folder/"audit_summary.json").write_text(json.dumps(rows,indent=2)+"\n")
        fields=["instance","seed","variant","process_status","process_seconds","emitted_lines",
                "unique_valid_in_budget","covered","total","percent","filtered_samples","filtered_covered"]
        with (folder/"audit_summary.csv").open("w") as f:
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
            writer.writerows({key:row[key] for key in fields} for row in rows)
        for row in rows:
            if any(row["counts"][k] for k in ("invalid","unknown","malformed","coverage_mismatch","duplicate")):
                errors.append((name,row["instance"],row["counts"]))
    if errors:raise SystemExit("Audit failures retained: "+repr(errors))
    print(f"Audited {len(work)} runs in {len(reports)} batches; all valid, unique, original coverage matched")

if __name__=="__main__":main()
