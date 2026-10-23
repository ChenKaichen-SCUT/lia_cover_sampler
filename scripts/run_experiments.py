#!/usr/bin/env python3
"""Strict-deadline experiments. Existing directories are immutable, never rerun."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--benchmarks", type=Path, default=ROOT / "benchmarks")
    p.add_argument("--binary", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--variant", choices=["highdiv", "full", "random", "no-cache", "global-only"], required=True)
    p.add_argument("--seeds", default="0,1,2")
    p.add_argument("--seconds", type=float, default=30)
    p.add_argument("--samples", type=int, default=1000)
    p.add_argument("--jobs", type=int, default=12)
    a = p.parse_args()
    if not 1 <= a.jobs <= 12 or a.seconds <= 0 or a.samples <= 0:
        p.error("jobs must be 1..12; seconds/samples must be positive")
    a.output = a.output.resolve()
    # The atomic mkdir is also the baseline-once lock. Interrupted batches remain locked.
    a.output.mkdir(parents=True, exist_ok=False)
    inputs = sorted(a.benchmarks.resolve().rglob("*.smt2"))
    if not inputs:
        p.error("no benchmark inputs")
    binary = a.binary.resolve()
    jobs = []
    for instance in inputs:
        for seed in map(int, a.seeds.split(",")):
            relative = instance.relative_to(a.benchmarks.resolve())
            run = a.output / "raw" / str(relative) / f"seed-{seed}"
            command = [str(binary), "-i", str(instance), "-o", str(run), "-n", str(a.samples),
                       "-t", str(a.seconds), "-s", str(seed)]
            if a.variant != "highdiv":
                command += ["--variant", a.variant]
            jobs.append(dict(instance=str(relative), input=str(instance), seed=seed,
                             input_sha256=digest(instance), run=str(run.relative_to(a.output)),
                             command=command))
    manifest = dict(created_utc=datetime.now(timezone.utc).isoformat(), platform=platform.platform(),
                    binary=str(binary), binary_sha256=digest(binary), jobs=a.jobs, seconds=a.seconds,
                    samples=a.samples, variant=a.variant, instances=jobs,
                    deadline="wall time from before Popen; SIGKILL at deadline; preserve complete lines",
                    threads_per_solver=1)
    (a.output / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    active, lock, stopped = set(), threading.Lock(), threading.Event()
    def cancel(signum, _frame):
        stopped.set()
        with lock:
            for process in active:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
    signal.signal(signal.SIGINT, cancel)
    signal.signal(signal.SIGTERM, cancel)
    def run(job):
        output = a.output / job["run"]
        output.mkdir(parents=True)
        if stopped.is_set():
            return dict(**job, status="cancelled", elapsed=0, returncode=None)
        env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
        with (output / "process.log").open("wb") as log:
            start = time.monotonic()
            with lock:
                process = subprocess.Popen(job["command"], stdout=log, stderr=subprocess.STDOUT,
                                           cwd=output, start_new_session=True, env=env)
                active.add(process)
            status = "ok"
            try:
                code = process.wait(timeout=max(.001, a.seconds-(time.monotonic()-start)))
                if code:
                    status = "error"
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                code = process.wait()
                status = "timeout_partial"
            finally:
                with lock:
                    active.discard(process)
            elapsed = time.monotonic()-start
        if stopped.is_set():
            status = "cancelled"
        result = dict(**job, status=status, elapsed=elapsed, returncode=code)
        (output / "process.json").write_text(json.dumps(result, indent=2)+"\n")
        print(f'{job["instance"]} seed={job["seed"]}: {status}, {elapsed:.3f}s', flush=True)
        return result
    results = []
    with ThreadPoolExecutor(max_workers=a.jobs) as pool, (a.output / "runs.jsonl").open("w") as journal:
        for future in as_completed([pool.submit(run, job) for job in jobs]):
            result = future.result()
            results.append(result)
            journal.write(json.dumps(result)+"\n")
            journal.flush()
    (a.output / "COMPLETE.json").write_text(json.dumps({
        "runs": len(results), "cancelled": stopped.is_set(),
        "manifest_sha256": digest(a.output / "manifest.json"),
        "journal_sha256": digest(a.output / "runs.jsonl")
    }, indent=2)+"\n")


if __name__ == "__main__":
    main()
