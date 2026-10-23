#!/usr/bin/env python3
"""Independent validation, frozen evaluator, and an explicitly reconstructed filter."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from validation import original_formula, variables, verify, decode_assignment
from coverage_differential_test import load_metric, differential


def read_complete(path):
    if not path.exists():
        return [], 0
    data = path.read_bytes()
    parts = data.splitlines(keepends=True)
    return [s.decode() for s in parts if s.endswith(b"\n")], sum(not s.endswith(b"\n") for s in parts)


def sampler_metadata(folder, job, allow_interrupted_summary=False):
    """A deadline kill can interrupt final metadata; never relax model auditing."""
    summary = folder / "summary.json"
    interrupted = None
    if summary.exists():
        try:
            return json.loads(summary.read_text()), False, None
        except json.JSONDecodeError as error:
            if not (allow_interrupted_summary and job.get("status") == "timeout_partial"
                    and job.get("returncode") == -9):
                raise
            interrupted = dict(reason="deadline_killed_during_summary_write", error=str(error),
                               bytes=summary.stat().st_size,
                               sha256=hashlib.sha256(summary.read_bytes()).hexdigest())
    checkpoint = folder / "progress.json"
    if checkpoint.exists():
        return json.loads(checkpoint.read_text()), True, interrupted
    return None, True, interrupted


def audit_job(payload):
    run_root, job, variant, deadline, *options = payload
    reconstruct_filter = options[0] if options else True
    allow_interrupted_summary = options[1] if len(options) > 1 else False
    run_root = Path(run_root)
    cached = run_root / "audit" / job["instance"] / f'seed-{job["seed"]}.json'
    if cached.exists():
        result = json.loads(cached.read_text())
        result.pop("curve")
        result.pop("filtered_curve")
        return result
    folder = run_root / job["run"]
    if not folder.is_dir():
        raise ValueError(f"missing raw run folder: {folder}")
    formula = ROOT / "benchmarks" / job["instance"]
    if hashlib.sha256(formula.read_bytes()).hexdigest() != job["input_sha256"]:
        raise ValueError("benchmark differs from the immutable run manifest")
    begin = time.perf_counter()
    root = original_formula(formula)
    decls = variables(root)
    metric = load_metric()
    class Incremental(metric.WireCoverageStatistics):
        def __init__(self):
            super().__init__()
            self.covered = 0
            self.seen = 0
        def evaluate_node(self, key, value):
            before = self._storage[key]
            super().evaluate_node(key, value)
            after = self._storage[key]
            self.covered += self.node_count(*after)-self.node_count(*before)
            self.seen += int(after[0]).bit_count()+int(after[1]).bit_count()-int(before[0]).bit_count()-int(before[1]).bit_count()
    stat = Incremental()
    evaluation = metric.ManualSatisfiesMetric(formula.read_text(), stat)
    total = sum(stat.node_total(k, v[2]) for k, v in stat._storage.items())
    filtering = Incremental() if reconstruct_filter else None
    filter_evaluation = metric.ManualSatisfiesMetric(formula.read_text(), filtering) if reconstruct_filter else None
    setup = time.perf_counter()-begin
    filtered_clock = setup
    assignments, times, broken = [], {}, 0
    names = {(str(e.decl().name()), str(e.sort())) for e in decls}
    if variant == "highdiv":
        data, broken = read_complete(folder / (formula.name+".samples"))
        for line in data:
            try:
                parsed = next(metric.parse_samples(io.StringIO(line)))
                # Do not silently complete partial baseline assignments.
                assignments.append({"assignment": [dict(name=k, sort="Int", value=str(v)) for k, v in parsed]})
            except Exception as e:
                assignments.append({"parse_error": str(e)})
        timing = folder / (formula.name+".times.csv")
    else:
        data, broken = read_complete(folder / "models.jsonl")
        for line in data:
            try:
                assignments.append(json.loads(line))
            except Exception as e:
                assignments.append({"parse_error": str(e)})
        timing = folder / "emission.csv"
    for line in read_complete(timing)[0]:
        i, t = line.strip().split(",")
        times[int(i)] = float(t)
    counts = dict(valid=0, invalid=0, unknown=0, malformed=0, duplicate=0, after_deadline=0,
                  timestamp_missing=0, incomplete_lines=broken, coverage_mismatch=0)
    seen, curve, filtered_curve, first_models, flat = set(), [], [], [], 0
    invalid_examples = []
    for index, row in enumerate(assignments):
        started = time.perf_counter()
        try:
            a = row["assignment"]
            values = decode_assignment(a, decls)
            verdict = verify(root, a, declarations=decls)
            counts[verdict] += 1
            if verdict != "valid":
                invalid_examples.append({"index": index, "status": verdict})
                continue
            key = tuple(sorted(values.items()))
            duplicate = key in seen
            counts["duplicate"] += int(duplicate)
            seen.add(key)
        except (ValueError, KeyError, TypeError) as e:
            counts["malformed"] += 1
            invalid_examples.append({"index": index, "status": str(e)})
            continue
        sample = [(name, value) for (name, _), value in values.items()]
        if not evaluation.count_sample(sample):
            raise AssertionError(f"frozen evaluator rejected validated sample {index}")
        if variant != "highdiv" and row["covered"] != stat.covered:
            counts["coverage_mismatch"] += 1
        if index not in times:
            counts["timestamp_missing"] += 1
            continue
        stamp = times[index]
        if stamp > deadline:
            counts["after_deadline"] += 1
            continue
        if len(first_models)<5:
            first_models.append(a)
        if not duplicate:
            curve.append(dict(k=len(curve)+1, seconds=stamp, covered=stat.covered, total=total))
        if not reconstruct_filter:
            continue
        # Score privately. Only commit accepted states to the filter.
        old_storage = filtering._storage.copy()
        old_covered, old_seen = filtering.covered, filtering.seen
        filter_evaluation.count_sample(sample)
        gain = filtering.covered-old_covered
        new_seen = filtering.seen-old_seen
        keep = not duplicate and (gain>0 or new_seen>0 or flat>=32)
        # Includes validation, original coverage computation and filtering overhead.
        # This is conservative serial replay accounting, not a second sampler run.
        filtered_clock = max(filtered_clock, stamp)+(time.perf_counter()-started)
        if keep and filtered_clock <= deadline:
            filtered_curve.append(dict(k=len(filtered_curve)+1, seconds=filtered_clock,
                                       covered=filtering.covered, total=total, raw_index=index))
            flat = 0
        else:
            filtering._storage = old_storage
            filtering.covered, filtering.seen = old_covered, old_seen
            flat += 1
    differential_result = None
    if first_models:
        differential_result = differential(formula, first_models,
                                           failure_dir=folder / "differential_failure")
    report = dict(instance=job["instance"], seed=job["seed"], variant=variant,
                  process_status=job["status"], process_seconds=job["elapsed"],
                  emitted_lines=len(assignments), counts=counts,
                  unique_valid_in_budget=len(curve), covered=curve[-1]["covered"] if curve else 0,
                  total=total, percent=100*curve[-1]["covered"]/total if curve else 0,
                  filtered_samples=len(filtered_curve),
                  filtered_covered=filtered_curve[-1]["covered"] if filtered_curve else 0,
                  filter_setup_seconds=setup, filter_reconstructed=reconstruct_filter,
                  differential=differential_result, invalid_examples=invalid_examples[:10])
    metadata, partial, interrupted = sampler_metadata(folder, job, allow_interrupted_summary)
    if metadata is not None:
        report["sampler"] = metadata
        if not partial and variant != "highdiv" and metadata["samples"] != len(assignments):
            raise ValueError(f"sampler count differs from complete model lines: {folder}")
        if partial:
            report["statistics_partial"] = True
    if interrupted is not None:
        report["interrupted_summary"] = interrupted
    auditdir = run_root / "audit" / job["instance"]
    auditdir.mkdir(parents=True, exist_ok=True)
    target = auditdir / f'seed-{job["seed"]}.json'
    target.write_text(json.dumps(dict(**report, curve=curve, filtered_curve=filtered_curve), indent=2)+"\n")
    print(f'{variant}: {job["instance"]} seed={job["seed"]}: {len(curve)} valid unique, '
          f'{report["percent"]:.3f}%, validation={counts}', flush=True)
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("run", type=Path)
    p.add_argument("--jobs", type=int, default=15)
    a = p.parse_args()
    manifest = json.loads((a.run / "manifest.json").read_text())
    jobs = [json.loads(s) for s in (a.run / "runs.jsonl").read_text().splitlines()]
    payloads = [(str(a.run.resolve()), j, manifest["variant"], manifest["seconds"]) for j in jobs]
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:
        reports = list(pool.map(audit_job, payloads))
    reports.sort(key=lambda r: (r["instance"], r["seed"]))
    (a.run / "audit_summary.json").write_text(json.dumps(reports, indent=2)+"\n")
    fields = ["instance", "seed", "variant", "process_status", "process_seconds", "emitted_lines",
              "unique_valid_in_budget", "covered", "total", "percent", "filtered_samples", "filtered_covered"]
    with (a.run / "audit_summary.csv").open("w") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows({k:r[k] for k in fields} for r in reports)
    if any(any(r["counts"][k] for k in ("invalid", "unknown", "malformed", "coverage_mismatch")) for r in reports):
        raise SystemExit("audit found invalid/incompatible output; retained all records")


if __name__ == "__main__":
    main()
