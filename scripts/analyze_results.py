#!/usr/bin/env python3
"""Reproduce tables and plots from independently audited online prefixes."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
METHODS = ["highdiv", "highdiv-filter", "full", "random", "no-cache", "global-only"]


def write_csv(path, rows):
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--results", type=Path, default=ROOT / "results")
    p.add_argument("--plots", action="store_true")
    a = p.parse_args()
    datasets = json.loads((ROOT / "benchmarks/manifest.json").read_text())
    split = {r["path"]: r["split"] for r in datasets}
    data, audits = {}, []
    for method in METHODS:
        if method == "highdiv-filter":
            continue
        run = a.results / ("baseline_once" if method == "highdiv" else f"final_{method}")
        summaries = json.loads((run / "audit_summary.json").read_text())
        audits.extend(summaries)
        for row in summaries:
            detail = json.loads((run / "audit" / row["instance"] / f'seed-{row["seed"]}.json').read_text())
            key = (row["instance"], row["seed"])
            data[method, key] = detail["curve"]
            if method == "highdiv":
                data["highdiv-filter", key] = detail["filtered_curve"]
    keys = sorted(key for method, key in data if method == "highdiv")
    output = a.results / "analysis"
    output.mkdir(exist_ok=True)
    endpoint, timeline, prefixes, paired, stages = [], [], [], [], []
    def at_time(curve, seconds):
        return next((r for r in reversed(curve) if r["seconds"] <= seconds), None)
    for method in METHODS:
        for instance, seed in keys:
            curve = data[method, (instance, seed)]
            last = curve[-1] if curve else None
            endpoint.append(dict(method=method, instance=instance, split=split[instance], seed=seed,
                                 samples=len(curve), covered=last["covered"] if last else 0,
                                 percent=100*last["covered"]/last["total"] if last else 0,
                                 last_seconds=last["seconds"] if last else None))
            for t in range(31):
                row = at_time(curve, t)
                timeline.append(dict(method=method, instance=instance, split=split[instance], seed=seed,
                                     seconds=t, percent=100*row["covered"]/row["total"] if row else 0,
                                     samples=row["k"] if row else 0))
            for k in [5, 10, 50, 100]:
                row = curve[k-1] if len(curve)>=k else None
                prefixes.append(dict(method=method, instance=instance, split=split[instance], seed=seed, k=k,
                                     reached=row is not None, percent=100*row["covered"]/row["total"] if row else None,
                                     seconds=row["seconds"] if row else None))
    for method in METHODS[1:]:
        for k in [5, 10, 50, 100]:
            for subset in ("all", "evaluation"):
                pairs = []
                for key in keys:
                    if subset != "all" and split[key[0]] != subset:
                        continue
                    left, right = data["highdiv", key], data[method, key]
                    if len(left)>=k and len(right)>=k:
                        l, r = left[k-1], right[k-1]
                        assert l["total"] == r["total"]
                        pairs.append((100*l["covered"]/l["total"], 100*r["covered"]/r["total"],
                                      l["seconds"], r["seconds"]))
                paired.append(dict(method=method, baseline="highdiv", split=subset, k=k, pairs=len(pairs),
                                   wins=sum(r[1]>r[0] for r in pairs), ties=sum(r[1]==r[0] for r in pairs),
                                   losses=sum(r[1]<r[0] for r in pairs),
                                   baseline_percent=mean(r[0] for r in pairs) if pairs else None,
                                   method_percent=mean(r[1] for r in pairs) if pairs else None,
                                   baseline_seconds=mean(r[2] for r in pairs) if pairs else None,
                                   method_seconds=mean(r[3] for r in pairs) if pairs else None))
    for audit in audits:
        stats = audit.get("sampler", {}).get("statistics", {})
        for phase, seconds in stats.get("seconds", {}).items():
            stages.append(dict(method=audit["variant"], instance=audit["instance"], seed=audit["seed"],
                               phase=phase, seconds=seconds, partial=audit.get("statistics_partial",False)))
    aggregate = []
    for subset in ("all", "evaluation"):
        for method in METHODS:
            rows = [r for r in endpoint if r["method"]==method and (subset=="all" or r["split"]==subset)]
            aggregate.append(dict(method=method, split=subset, runs=len(rows),
                                  mean_percent=mean(r["percent"] for r in rows),
                                  median_samples=median(r["samples"] for r in rows),
                                  total_samples=sum(r["samples"] for r in rows)))
    per_instance = []
    for item in datasets:
        row = dict(instance=item["path"], split=item["split"])
        for method in METHODS:
            values=[r for r in endpoint if r["instance"]==item["path"] and r["method"]==method]
            row[method+"_percent"]=mean(v["percent"] for v in values)
            row[method+"_samples"]=mean(v["samples"] for v in values)
        per_instance.append(row)
    for filename, rows in [("endpoints.csv",endpoint),("time_curves.csv",timeline),
                           ("prefixes.csv",prefixes),("paired_prefixes.csv",paired),
                           ("stages.csv",stages),("aggregate.csv",aggregate),("per_instance.csv",per_instance)]:
        write_csv(output / filename, rows)
    validation = {k: sum(r["counts"][k] for r in audits)
                  for k in ("valid","invalid","unknown","malformed","duplicate","coverage_mismatch",
                            "timestamp_missing","after_deadline","incomplete_lines")}
    (output / "validation.json").write_text(json.dumps(validation,indent=2)+"\n")
    (output / "summary.json").write_text(json.dumps(dict(aggregate=aggregate, paired=paired,
                                                         validation=validation),indent=2)+"\n")
    contributions = []
    for method in ("full", "random", "no-cache", "global-only"):
        for instance, seed in keys:
            counts, gains = Counter(), Counter()
            source = a.results / f"final_{method}" / "raw" / instance / f"seed-{seed}" / "models.jsonl"
            if not source.exists():
                continue
            for line in source.open():
                if not line.endswith("\n"):
                    continue
                row = json.loads(line)
                counts[row["category"]] += 1
                gains[row["category"]] += row["new_coverage"]
            for category, count in counts.items():
                contributions.append(dict(method=method, instance=instance, seed=seed, category=category,
                                          samples=count, new_bits=gains[category]))
    if contributions:
        write_csv(output / "contributions.csv", contributions)
    if a.plots:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        colors = ["#333333","#999999","#0072B2","#E69F00","#009E73","#CC79A7"]
        fig, ax = plt.subplots(figsize=(8,4.5), layout="constrained")
        for method, color in zip(METHODS,colors):
            means = [mean(r["percent"] for r in timeline if r["method"]==method and r["seconds"]==t)
                     for t in range(31)]
            label = "HighDiv + filter (replay estimate)" if method=="highdiv-filter" else method
            ax.plot(range(31),means,label=label,color=color,linestyle="--" if method=="highdiv-filter" else "-")
        ax.set(xlabel="Wall-clock budget (s)", ylabel="Mean original wire coverage (%)",
               title="9 sampled instances × 3 seeds; online prefixes")
        ax.grid(alpha=.2);ax.legend(fontsize=8)
        fig.savefig(output/"coverage_time.svg");fig.savefig(output/"coverage_time.png",dpi=180)
        plt.close(fig)
        fig, ax = plt.subplots(figsize=(8,4.2), layout="constrained")
        labels, left, right, sizes = [],[],[],[]
        for row in paired:
            if row["method"]=="full" and row["split"]=="all" and row["pairs"]:
                labels.append(str(row["k"]));left.append(row["baseline_percent"])
                right.append(row["method_percent"]);sizes.append(row["pairs"])
        x=list(range(len(labels)))
        ax.bar([i-.18 for i in x],left,.36,label="HighDiv",color="#555555")
        ax.bar([i+.18 for i in x],right,.36,label="full",color="#0072B2")
        ax.set_xticks(x,[f"k={k}\n{n} paired runs" for k,n in zip(labels,sizes)])
        ax.set(ylabel="Mean original wire coverage (%)",title="Only runs where BOTH methods reached k")
        ax.legend();ax.grid(axis="y",alpha=.2)
        fig.savefig(output/"fixed_samples.svg");fig.savefig(output/"fixed_samples.png",dpi=180)
    print(json.dumps(dict(aggregate=aggregate,validation=validation),indent=2))


if __name__ == "__main__":
    main()
