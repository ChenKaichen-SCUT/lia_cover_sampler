#!/usr/bin/env python3
"""Replay an automatic failure bundle or an immutable experiment run."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument("bundle", type=Path)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--binary", type=Path, default=ROOT / "build/lia_cover_sampler")
a = p.parse_args()
if a.output.exists():
    p.error("choose a new output directory")
if (a.bundle / "failure.json").exists():
    config = json.loads((a.bundle / "failure.json").read_text())
    formula = a.bundle / "input.smt2"
    command = [str(a.binary.resolve()), "-i", str(formula.resolve()), "-o", str(a.output.resolve()),
               "-s", str(config["seed"]), "-n", str(config["samples"]), "-t", str(config["seconds"]),
               "--variant", config["variant"], "--mode", "trace"]
    saved_config = a.bundle / "config.json"
    if saved_config.exists():
        settings = json.loads(saved_config.read_text())
        command += ["--profile", settings["profile"], "--scheduler", settings["scheduler"]]
        disabled = [name for name in ("quotas", "fair", "matching", "polish", "line", "repair")
                    if not settings[name]]
        if disabled:
            command += ["--disable", ",".join(disabled)]
        for flag, field in (("--direction-cap", "direction_cap"), ("--candidate-cap", "candidate_cap"),
                            ("--attempt-cap", "attempt_cap"), ("--polish-steps", "polish_steps")):
            command += [flag, str(settings[field])]
        command += ["--polish-ms", str(settings["polish_seconds"] * 1000)]
elif (a.bundle / "process.json").exists():
    config = json.loads((a.bundle / "process.json").read_text())
    command = config["command"]
    if Path(command[0]).name == "highdiv":
        p.error("HighDiv is frozen and must not be rerun; replay saved samples with audit_experiments.py")
    command[0] = str(a.binary.resolve())
    command[command.index("-i")+1] = str(ROOT / "benchmarks" / config["instance"])
    command[command.index("-o")+1] = str(a.output.resolve())
    command += ["--mode", "trace"]
else:
    p.error("no failure.json or process.json")
raise SystemExit(subprocess.call(command))
