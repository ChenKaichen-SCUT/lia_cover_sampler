#!/usr/bin/env python3
"""Check and summarize the two independent 900-second comparison protocols.

Reads existing audits and raw output only; never invokes either sampler.
Requires extracted raw/ directories (see each batch's archive.json).
"""
import argparse
from bisect import bisect_right
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
BATCHES = (
    ("time900", "highdiv", "highdiv_time900", 4294967295),
    ("time900", "lia", "lia_time900", 4294967295),
    ("count1000_t900", "highdiv", "highdiv_count1000_t900", 1000),
    ("count1000_t900", "lia", "lia_count1000_t900", 1000),
)
SHORT_NAMES = (
    "Bromberger", "40-9", "CAV-019", "SMT_random", "SMT_real",
    "convert", "dillig", "pb2010", "prime_cone",
)


def check(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def write_csv(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=ROOT / "results")
    parser.add_argument("--output", type=Path, default=ROOT / "results/analysis_900s")
    parser.add_argument("--plots", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    definitions = json.loads((ROOT / "benchmarks/manifest.json").read_text())
    splits = {row["path"]: row["split"] for row in definitions}
    expected_keys = {(name, 0) for name in splits}
    build = json.loads((ROOT / "dependencies/sampler_build.json").read_text())
    baseline = json.loads((ROOT / "dependencies/highdiv_timing.json").read_text())
    expected_binary = {"highdiv": baseline["binary_sha256"], "lia": build["binary_sha256"]}
    data, endpoints, stages, contributions, evidence, hashes = {}, [], [], [], [], {}
    validation = Counter()
    differential_prefixes = 0
    for protocol, algorithm, directory, cap in BATCHES:
        folder = args.results / directory

        def read(path):
            value = path.read_bytes()
            hashes[str(path.relative_to(args.results))] = hashlib.sha256(value).hexdigest()
            return json.loads(value)

        manifest = read(folder / "manifest.json")
        complete = read(folder / "COMPLETE.json")
        journal = folder / "runs.jsonl"
        check(complete["manifest_sha256"] == digest(folder / "manifest.json"),
              f"{directory}: manifest changed")
        check(complete["journal_sha256"] == digest(journal), f"{directory}: journal changed")
        hashes[str(journal.relative_to(args.results))] = digest(journal)
        check(complete["runs"] == 9 and not complete["cancelled"], f"{directory}: incomplete batch")
        check(manifest["seconds"] == 900 and manifest["samples"] == cap and manifest["jobs"] == 12,
              f"{directory}: unexpected experiment budget")
        check(manifest["binary_sha256"] == expected_binary[algorithm], f"{directory}: binary changed")
        check(manifest["variant"] == ("highdiv" if algorithm == "highdiv" else "full"),
              f"{directory}: variant mismatch")
        jobs = {(row["instance"], row["seed"]): row for row in
                map(json.loads, journal.read_text().splitlines())}
        planned = {(row["instance"], row["seed"]): row for row in manifest["instances"]}
        check(set(jobs) == set(planned) == expected_keys, f"{directory}: case/seed mismatch")
        summaries = read(folder / "audit_summary.json")
        check(len(summaries) == 9, f"{directory}: missing audit")
        with (folder / "audit_summary.csv").open() as stream:
            csv_rows = {(row["instance"], int(row["seed"])): row for row in csv.DictReader(stream)}
        check(set(csv_rows) == expected_keys, f"{directory}: missing CSV rows")
        batch_counts = Counter()
        for report in summaries:
            key = report["instance"], report["seed"]
            job = jobs[key]
            for field, value in planned[key].items():
                check(job[field] == value, f"{directory}/{key}: planned job differs")
            check(job == read(folder / job["run"] / "process.json"),
                  f"{directory}/{key}: process record mismatch")
            check(job["input_sha256"] == digest(ROOT / "benchmarks" / key[0]),
                  f"{directory}/{key}: benchmark changed")
            detail = read(folder / "audit" / key[0] / f"seed-{key[1]}.json")
            check({k: v for k, v in detail.items() if k not in ("curve", "filtered_curve")} == report,
                  f"{directory}/{key}: audit summary differs")
            check(report["process_status"] == job["status"] and
                  report["process_seconds"] == job["elapsed"], f"{directory}/{key}: process mismatch")
            for field, value in csv_rows[key].items():
                check(str(report[field]) == value, f"{directory}/{key}: CSV {field} differs")
            counts = report["counts"]
            for field in ("invalid", "unknown", "malformed", "duplicate", "after_deadline",
                          "incomplete_lines", "coverage_mismatch"):
                check(counts[field] == 0, f"{directory}/{key}: nonzero {field}; inspect before comparing")
            check(counts["valid"] == report["emitted_lines"], f"{directory}/{key}: invalid line count")
            curve = detail["curve"]
            check(len(curve) == report["unique_valid_in_budget"] and len(curve) > 0,
                  f"{directory}/{key}: curve length differs")
            previous_seconds = previous_covered = 0
            for k, point in enumerate(curve, 1):
                check(point["k"] == k and point["total"] == report["total"],
                      f"{directory}/{key}: curve index or denominator differs")
                check(previous_seconds <= point["seconds"] <= 900 and
                      previous_covered <= point["covered"] <= point["total"],
                      f"{directory}/{key}: non-monotonic or out-of-budget curve")
                previous_seconds, previous_covered = point["seconds"], point["covered"]
            check(curve[-1]["covered"] == report["covered"], f"{directory}/{key}: endpoint differs")
            check(abs(100 * report["covered"] / report["total"] - report["percent"]) < 1e-10,
                  f"{directory}/{key}: percentage differs")
            raw = folder / job["run"]
            sample_file = raw / (key[0] + ".samples" if algorithm == "highdiv" else "models.jsonl")
            timing_file = raw / (key[0] + ".times.csv" if algorithm == "highdiv" else "emission.csv")
            times = {}
            with timing_file.open() as stream:
                for index, seconds in csv.reader(stream):
                    check(int(index) not in times, f"{directory}/{key}: duplicate timestamp")
                    times[int(index)] = float(seconds)
            sources, gains = Counter(), Counter()
            emitted = 0
            with sample_file.open() as stream:
                for index, line in enumerate(stream):
                    check(line.endswith("\n"), f"{directory}/{key}: truncated output")
                    if algorithm == "lia":
                        model = json.loads(line)
                        check(model["index"] == index, f"{directory}/{key}: non-contiguous model")
                        check(model["covered"] == curve[index]["covered"],
                              f"{directory}/{key}: raw coverage differs")
                        sources[model["category"]] += 1
                        gains[model["category"]] += model["new_coverage"]
                    else:
                        check(int(line.split(":", 1)[0]) == index,
                              f"{directory}/{key}: non-contiguous sample")
                    emitted += 1
            check(emitted == report["emitted_lines"], f"{directory}/{key}: raw line count differs")
            missing = set(range(emitted)) - set(times)
            check(len(missing) == counts["timestamp_missing"], f"{directory}/{key}: missing timestamp count")
            # A missing interior timestamp would need a fresh filtered metric audit.
            check(not missing or missing == {emitted - 1}, f"{directory}/{key}: interior timestamp gap")
            check(sorted(times) == list(range(len(curve))), f"{directory}/{key}: timestamp indices differ")
            check(all(times[i] == point["seconds"] for i, point in enumerate(curve)),
                  f"{directory}/{key}: timestamps differ from audited curve")
            differential_prefixes += report["differential"]["prefixes"]
            batch_counts.update(counts)
            validation.update(counts)
            data[protocol, algorithm, key] = detail
            endpoints.append(dict(protocol=protocol, algorithm=algorithm, instance=key[0],
                                  split=splits[key[0]], seed=key[1], samples=len(curve),
                                  covered=report["covered"], total=report["total"], percent=report["percent"],
                                  reached_1000=len(curve) >= 1000,
                                  seconds_to_1000=curve[999]["seconds"] if len(curve) >= 1000 else None,
                                  process_seconds=report["process_seconds"],
                                  last_sample_seconds=curve[-1]["seconds"],
                                  status=report["process_status"], timestamp_missing=len(missing)))
            for category, count in sorted(sources.items()):
                contributions.append(dict(protocol=protocol, instance=key[0], seed=key[1],
                                          category=category, samples=count, new_bits=gains[category]))
            if sources:
                check(sum(gains.values()) == report["covered"], f"{directory}/{key}: coverage gains differ")
            for phase, seconds in report.get("sampler", {}).get("statistics", {}).get("seconds", {}).items():
                stages.append(dict(protocol=protocol, instance=key[0], seed=key[1], phase=phase,
                                   seconds=seconds, checkpoint=report.get("statistics_partial", False)))
        evidence.append(dict(directory=directory, counts=dict(batch_counts),
                             manifest_sha256=digest(folder / "manifest.json"),
                             audit_summary_sha256=digest(folder / "audit_summary.json")))

    aggregate, paired, common_prefix, prefix_pairs, time_points = [], [], [], [], []
    keys = sorted(expected_keys)
    for protocol in ("time900", "count1000_t900"):
        for subset in ("all", "evaluation"):
            subset_keys = [key for key in keys if subset == "all" or splits[key[0]] == subset]
            for algorithm in ("highdiv", "lia"):
                rows = [r for r in endpoints if r["protocol"] == protocol and r["algorithm"] == algorithm
                        and (subset == "all" or r["split"] == subset)]
                aggregate.append(dict(protocol=protocol, algorithm=algorithm, subset=subset, runs=len(rows),
                                      mean_percent=mean(r["percent"] for r in rows),
                                      pooled_bit_percent=100 * sum(r["covered"] for r in rows) /
                                      sum(r["total"] for r in rows),
                                      total_samples=sum(r["samples"] for r in rows),
                                      median_samples=median(r["samples"] for r in rows),
                                      reached_1000=sum(r["reached_1000"] for r in rows),
                                      mean_process_seconds=mean(r["process_seconds"] for r in rows)))
            for k in (5, 10, 50, 100, 200, 500, 1000):
                pairs = []
                for key in subset_keys:
                    left = data[protocol, "highdiv", key]["curve"]
                    right = data[protocol, "lia", key]["curve"]
                    if min(len(left), len(right)) >= k:
                        pairs.append((left[k - 1], right[k - 1]))
                check(len(pairs) > 0, "no matched prefixes")
                prefix_pairs.append(dict(protocol=protocol, subset=subset, k=k, pairs=len(pairs),
                                         highdiv_percent=mean(100 * a["covered"] / a["total"] for a, _ in pairs),
                                         lia_percent=mean(100 * b["covered"] / b["total"] for _, b in pairs),
                                         highdiv_seconds=mean(a["seconds"] for a, _ in pairs),
                                         lia_seconds=mean(b["seconds"] for _, b in pairs),
                                         lia_wins=sum(b["covered"] > a["covered"] for a, b in pairs),
                                         ties=sum(b["covered"] == a["covered"] for a, b in pairs),
                                         lia_losses=sum(b["covered"] < a["covered"] for a, b in pairs)))
        for key in keys:
            left, right = (data[protocol, algorithm, key] for algorithm in ("highdiv", "lia"))
            check(left["total"] == right["total"], f"{key}: incompatible metric denominator")
            paired.append(dict(protocol=protocol, instance=key[0], split=splits[key[0]], seed=key[1],
                               highdiv_samples=len(left["curve"]), lia_samples=len(right["curve"]),
                               highdiv_percent=left["percent"], lia_percent=right["percent"],
                               delta_pp=right["percent"] - left["percent"],
                               delta_bits=right["covered"] - left["covered"],
                               highdiv_seconds=left["process_seconds"], lia_seconds=right["process_seconds"],
                               both_reached_1000=min(len(left["curve"]), len(right["curve"])) >= 1000))
            k = min(len(left["curve"]), len(right["curve"]))
            a, b = left["curve"][k - 1], right["curve"][k - 1]
            common_prefix.append(dict(protocol=protocol, instance=key[0], seed=key[1], k=k,
                                      highdiv_percent=100 * a["covered"] / a["total"],
                                      lia_percent=100 * b["covered"] / b["total"],
                                      highdiv_seconds=a["seconds"], lia_seconds=b["seconds"]))
    for algorithm in ("highdiv", "lia"):
        curves = [(key, data["time900", algorithm, key]["curve"]) for key in keys]
        stamps = {key: [p["seconds"] for p in curve] for key, curve in curves}
        for seconds in sorted({0, 1, 2, *range(5, 901, 5)}):
            for subset in ("all", "evaluation"):
                values, samples = [], []
                for key, curve in curves:
                    if subset != "all" and splits[key[0]] != subset:
                        continue
                    index = bisect_right(stamps[key], seconds) - 1
                    point = curve[index] if index >= 0 else None
                    values.append(100 * point["covered"] / point["total"] if point else 0)
                    samples.append(point["k"] if point else 0)
                time_points.append(dict(algorithm=algorithm, subset=subset, seconds=seconds,
                                        mean_percent=mean(values), total_samples=sum(samples)))
    for name, rows in (("endpoints", endpoints), ("aggregate", aggregate), ("paired_endpoints", paired),
                       ("paired_prefixes", prefix_pairs), ("common_prefix", common_prefix),
                       ("time_curves", time_points), ("stages", stages), ("contributions", contributions)):
        write_csv(args.output / f"{name}.csv", rows)
    summary = dict(aggregate=aggregate, paired_prefixes=prefix_pairs, validation=dict(validation),
                   differential_prefixes=differential_prefixes, runs=len(endpoints),
                   in_budget_samples=sum(r["samples"] for r in endpoints),
                   mean_definition="Unweighted arithmetic mean of per-instance wire coverage percentages",
                   fixed_count_caveat="All-case endpoints include timeouts; only matched prefixes are equal-count.")
    write_json(args.output / "summary.json", summary)
    write_json(args.output / "integrity.json", dict(
        checks="Manifest/journal hashes; input/binary identity; per-run/summary/CSV consistency; "
               "raw line indices/counts and emission timestamps; monotone audited prefixes; "
               "LIA raw per-sample coverage and contribution sums. No sampler or solver rerun.",
        batches=evidence, input_files_sha256=hashes))
    if args.plots:
        plot(args.output, endpoints, paired, time_points)
    print(json.dumps(summary, indent=2))


def plot(output, endpoints, paired, time_points):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = {"highdiv": "#555555", "lia": "#0072B2"}
    labels = {"highdiv": "HighDiv", "lia": "LIA Cover Sampler"}
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), layout="constrained")
    for algorithm in colors:
        points = [r for r in time_points if r["algorithm"] == algorithm and r["subset"] == "all"]
        axes[0].plot([r["seconds"] for r in points], [r["mean_percent"] for r in points],
                     label=labels[algorithm], color=colors[algorithm])
        rows = sorted((r for r in endpoints if r["protocol"] == "time900" and r["algorithm"] == algorithm),
                      key=lambda r: r["instance"])
        offset = -.18 if algorithm == "highdiv" else .18
        axes[1].barh([i + offset for i in range(9)], [r["samples"] for r in rows], .36,
                     label=labels[algorithm], color=colors[algorithm])
    axes[0].set(xlabel="Sampling wall-clock budget (s)", ylabel="Mean wire coverage (%)",
                title="Equal time: 9 instances, seed 0", xlim=(0, 900), ylim=(0, 65))
    axes[1].set(yticks=range(9), yticklabels=SHORT_NAMES, xlabel="Valid unique samples (log scale)",
                title="Samples retained by 900 s", xscale="log")
    axes[1].invert_yaxis()
    for ax in axes:
        ax.legend(fontsize=9)
        ax.grid(alpha=.2)
    for extension in ("png", "svg"):
        fig.savefig(output / f"time900_comparison.{extension}", dpi=180)
    plt.close(fig)
    rows = [r for r in paired if r["protocol"] == "count1000_t900" and r["both_reached_1000"]]
    short = dict(zip(sorted({r["instance"] for r in paired}), SHORT_NAMES))
    fig, axes = plt.subplots(1, 2, figsize=(12, 4), layout="constrained")
    for algorithm in colors:
        offset = -.18 if algorithm == "highdiv" else .18
        axes[0].barh([i + offset for i in range(len(rows))],
                     [r[f"{algorithm}_seconds"] for r in rows], .36,
                     label=labels[algorithm], color=colors[algorithm])
    axes[0].set(xlabel="Process wall time to finish (s, log scale)", xscale="log",
                title="Both algorithms completed 1,000 samples")
    axes[0].legend(fontsize=9)
    axes[1].barh(range(len(rows)), [r["delta_pp"] for r in rows], color=colors["lia"])
    axes[1].axvline(0, color="#555555", linewidth=.8)
    axes[1].set(xlabel="LIA minus HighDiv coverage (percentage points)",
                title="Coverage difference at exactly 1,000 samples")
    for ax in axes:
        ax.set_yticks(range(len(rows)), [short[r["instance"]] for r in rows])
        ax.invert_yaxis()
        ax.grid(alpha=.2)
    for extension in ("png", "svg"):
        fig.savefig(output / f"count1000_comparison.{extension}", dpi=180)
    plt.close(fig)


if __name__ == "__main__":
    main()
