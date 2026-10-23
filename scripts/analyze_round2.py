#!/usr/bin/env python3
"""Reproducible paired comparison from frozen audits; never runs a sampler."""
import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
from statistics import mean

ROOT=Path(__file__).resolve().parents[1]
PREFIXES=(2,5,10,50,100,1000)
TIMES=(1,5,10,30,60,120,300,900)

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path,data):path.write_text(json.dumps(data,indent=2,ensure_ascii=False)+"\n")
def csvfile(path,rows):
    if not rows:return
    with path.open("w") as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
def require(condition,message):
    if not condition:raise ValueError(message)

def analyze(source,output,baselines=True):
    source=source.resolve();output=output.resolve()
    output.mkdir(parents=True,exist_ok=True)
    splits={r["path"]:r["split"] for r in json.loads((ROOT/"benchmarks/manifest.json").read_text())}
    batches=[(folder.name.rsplit("_",1)[0],folder.name.rsplit("_",1)[1],folder)
             for folder in sorted(source.iterdir()) if (folder/"audit_summary.json").exists()]
    if baselines:
        for algorithm,old in (("old_lia","lia"),("highdiv","highdiv")):
            for protocol,suffix in (("time","time900"),("count","count1000_t900")):
                batches.append((algorithm,protocol,ROOT/"results"/(old+"_"+suffix)))
    data={};endpoints=[];prefixes=[];early=[];modules=[];origins=[];mechanisms=[]
    hashes={};validation=Counter();statistics_partial=0
    for algorithm,protocol,folder in batches:
        manifest=json.loads((folder/"manifest.json").read_text())
        complete=json.loads((folder/"COMPLETE.json").read_text())
        require(complete["manifest_sha256"]==digest(folder/"manifest.json"),"changed manifest")
        require(complete["journal_sha256"]==digest(folder/"runs.jsonl"),"changed journal")
        require(not complete["cancelled"],"cancelled batch")
        require(manifest["samples"]==(4294967295 if protocol=="time" else 1000),"wrong sample cap")
        planned={(j["instance"],j["seed"]):j for j in manifest["instances"]}
        jobs={(j["instance"],j["seed"]):j for j in map(json.loads,(folder/"runs.jsonl").read_text().splitlines())}
        require(set(planned)==set(jobs) and len(jobs)==complete["runs"],"incomplete batch")
        for p in [folder/"manifest.json",folder/"runs.jsonl",folder/"audit_summary.json"]:
            hashes[str(p.relative_to(ROOT))]=digest(p)
        summaries=json.loads((folder/"audit_summary.json").read_text())
        require(len(summaries)==len(jobs),"missing audit")
        for report in summaries:
            instance,seed=report["instance"],report["seed"];job=jobs[instance,seed]
            require(seed==0,"unpaired seed")
            require(job["status"] not in ("error","cancelled"),"failed run")
            require(all(job[k]==v for k,v in planned[instance,seed].items()),"plan/run mismatch")
            require(digest(ROOT/"benchmarks"/instance)==job["input_sha256"],"changed benchmark")
            detail_path=folder/"audit"/instance/f"seed-{seed}.json"
            hashes[str(detail_path.relative_to(ROOT))]=digest(detail_path)
            detail=json.loads(detail_path.read_text());curve=detail["curve"]
            require(all(detail[k]==v for k,v in report.items()),"audit summary mismatch")
            require(not any(report["counts"][k] for k in
                ("invalid","unknown","malformed","coverage_mismatch","duplicate","after_deadline")),"audit failure")
            validation.update(report["counts"])
            require(len(curve)==report["unique_valid_in_budget"],"curve length mismatch")
            previous_time=previous_coverage=0
            for k,point in enumerate(curve,1):
                require(point["k"]==k and point["total"]==report["total"],"curve denominator/index changed")
                require(previous_time<=point["seconds"]<=manifest["seconds"],"invalid emission time")
                require(previous_coverage<=point["covered"]<=report["total"],"invalid cumulative coverage")
                previous_time=point["seconds"];previous_coverage=point["covered"]
            require(previous_coverage==report["covered"],"endpoint mismatch")
            base=dict(algorithm=algorithm,protocol=protocol,instance=instance,split=splits[instance],seed=seed)
            endpoint=dict(**base,samples=len(curve),covered=report["covered"],total=report["total"],
                percent=report["percent"],reached_1000=len(curve)>=1000,
                seconds_1000=curve[999]["seconds"] if len(curve)>=1000 else None,
                last_output_seconds=curve[-1]["seconds"] if curve else None,
                process_seconds=job["elapsed"],process_status=job["status"])
            endpoints.append(endpoint)
            data[algorithm,protocol,instance]=dict(curve=curve,report=report,endpoint=endpoint)
            for k in PREFIXES:
                point=curve[k-1] if len(curve)>=k else None
                prefixes.append(dict(**base,k=k,complete=point is not None,
                    percent=100*point["covered"]/report["total"] if point else None,
                    seconds=point["seconds"] if point else None))
            for t in TIMES:
                if t>manifest["seconds"]:continue
                i=bisect_right([p["seconds"] for p in curve],t)
                early.append(dict(**base,seconds=t,samples=i,percent=100*curve[i-1]["covered"]/report["total"] if i else 0))
            if algorithm in ("old_lia","highdiv"):continue
            stats=report.get("sampler",{}).get("statistics",{});counts=stats.get("counts",{})
            partial=report.get("statistics_partial",False);statistics_partial+=partial
            for module,cost in stats.get("action_seconds",{}).items():
                calls=counts.get("action_"+module+"_calls",0)
                outputs=counts.get("action_"+module+"_samples",0)
                gain=counts.get("action_"+module+"_gain",0)
                modules.append(dict(**base,module=module,seconds=cost,calls=calls,samples=outputs,
                    covered=gain,bits_per_second=gain/cost if cost else 0,bits_per_sample=gain/outputs if outputs else 0,
                    unique_valid_candidates=counts.get("action_"+module+"_unique_valid_candidates",0),
                    partial=partial))
            mechanisms.append(dict(**base,partial=partial,counts=counts,
                stage_seconds=stats.get("seconds",{}),action_seconds=stats.get("action_seconds",{}),
                refinement_seconds=stats.get("refinement_seconds",0)))
            by_origin=defaultdict(Counter)
            times={int(i):float(t) for i,t in csv.reader((folder/job["run"]/"emission.csv").open())}
            with (folder/job["run"]/"models.jsonl").open() as f:
                for line in f:
                    if not line.endswith("\n"):continue
                    row=json.loads(line)
                    if row["index"] not in times or times[row["index"]]>manifest["seconds"]:continue
                    origin=row.get("seed_origin",row["category"])
                    x=by_origin[origin];x["samples"]+=1;x["final_gain"]+=row["new_coverage"]
                    if "seed_gain_retained" in row:
                        require(row["seed_gain_retained"]+row["refinement_added"]==row["final_emitted_gain"],
                                "refinement attribution duplicates coverage")
                        require(row["seed_gain_retained"]+row["seed_gain_lost"]==row["gain_before_refinement"],
                                "seed attribution mismatch")
                        for field in ("seed_gain_retained","refinement_added","seed_gain_lost","refinement_seconds"):
                            x[field]+=row[field]
                        x["refined_outputs"]+=bool(row["refinement_modules"])
            require(sum(v["final_gain"] for v in by_origin.values())==report["covered"],"provenance sum mismatch")
            for origin,c in by_origin.items():
                origins.append(dict(**base,origin=origin,samples=c["samples"],final_gain=c["final_gain"],
                    seed_gain_retained=c["seed_gain_retained"],refinement_added=c["refinement_added"],
                    seed_gain_lost=c["seed_gain_lost"],refinement_seconds=c["refinement_seconds"],
                    refined_outputs=c["refined_outputs"],attribution_available=algorithm!="control_ab"))
    summaries=[]
    for algorithm,protocol in sorted({(r["algorithm"],r["protocol"]) for r in endpoints}):
        for split in ("all","development","evaluation"):
            rows=[r for r in endpoints if r["algorithm"]==algorithm and r["protocol"]==protocol and
                  (split=="all" or r["split"]==split)]
            if not rows:continue
            summaries.append(dict(algorithm=algorithm,protocol=protocol,split=split,cases=len(rows),
                mean_percent=mean(r["percent"] for r in rows),samples=sum(r["samples"] for r in rows),
                reached_1000=sum(r["reached_1000"] for r in rows),
                mean_seconds_1000=mean(r["seconds_1000"] for r in rows if r["reached_1000"]) if any(r["reached_1000"] for r in rows) else None))
    pairs=[]
    comparisons=[]
    algorithms=sorted({r["algorithm"] for r in endpoints})
    if baselines:
        comparisons += [(a,ref) for a in algorithms if a not in ("highdiv","old_lia") for ref in ("old_lia","highdiv")]
    if "full" in algorithms:
        comparisons += [("full",ref) for ref in ("control_ab","schedule","directions","polish","line","no_line","no_polish")
                        if ref in algorithms]
    for algorithm,reference in comparisons:
        for protocol in ("time","count"):
            common=[i for i in splits if (algorithm,protocol,i) in data and (reference,protocol,i) in data]
            for split in ("all","development","evaluation"):
                instances=[i for i in common if split=="all" or splits[i]==split]
                for metric,k in [("endpoint",None)]+[("prefix",k) for k in PREFIXES]+[("time",t) for t in TIMES]:
                    values=[];a_values=[];b_values=[]
                    for instance in instances:
                        a=data[algorithm,protocol,instance];b=data[reference,protocol,instance]
                        def value(record):
                            curve=record["curve"];total=record["report"]["total"]
                            if metric=="endpoint":return record["report"]["percent"]
                            if metric=="prefix":return 100*curve[k-1]["covered"]/total if len(curve)>=k else None
                            # Time comparisons only when both experiment budgets reach k.
                            max_budget=900 if baselines else json.loads((source/"plan.json").read_text())["seconds"]
                            if k>max_budget:return None
                            index=bisect_right([p["seconds"] for p in curve],k)
                            return 100*curve[index-1]["covered"]/total if index else 0
                        va,vb=value(a),value(b)
                        if va is None or vb is None:continue
                        require(a["report"]["total"]==b["report"]["total"],"paired denominator mismatch")
                        values.append(va-vb);a_values.append(va);b_values.append(vb)
                    if values:
                        pairs.append(dict(algorithm=algorithm,reference=reference,protocol=protocol,split=split,
                            metric=metric,budget=k,paired_cases=len(values),eligible_cases=len(instances),
                            algorithm_mean=mean(a_values),reference_mean=mean(b_values),delta_pp=mean(values),
                            wins=sum(v>1e-10 for v in values),ties=sum(abs(v)<=1e-10 for v in values),
                            losses=sum(v< -1e-10 for v in values)))
    csvfile(output/"endpoints.csv",endpoints);csvfile(output/"prefixes.csv",prefixes)
    csvfile(output/"early_times.csv",early);csvfile(output/"modules.csv",modules)
    csvfile(output/"provenance.csv",origins);csvfile(output/"summary.csv",summaries)
    csvfile(output/"paired.csv",pairs);write(output/"mechanisms.json",mechanisms)
    write(output/"analysis.json",dict(summary=summaries,paired=pairs,validation=dict(validation),
        runs=len(endpoints),partial_statistics=statistics_partial,source_hashes=hashes,
        notes=["seed 0 only; no uncertainty estimate from one seed",
               "prefix comparison excludes either side not reaching k; no extrapolation",
               "endpoint count protocol means 1000 samples OR 900 seconds, not necessarily equal counts",
               "action parent seconds, stage seconds and refinement seconds must not be summed",
               "new runs share 12 slots; frozen baseline batches had at most 9 concurrent cases"]))
    return summaries

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("root",type=Path);p.add_argument("--output",type=Path,required=True)
    p.add_argument("--no-baselines",action="store_true")
    a=p.parse_args();result=analyze(a.root,a.output,not a.no_baselines)
    for row in result:
        if row["split"]=="all":print(row)
if __name__=="__main__":main()
