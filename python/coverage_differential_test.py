"""Audit every bit/state and the exact evaluation event order against frozen HighDiv."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from validation import nodes_preorder, variables

ROOT = Path(__file__).resolve().parents[1]


def load_metric():
    path = Path(os.environ.get("HIGHDIV_ROOT", ROOT.parent / "HighDiv-master")) / "HighDiv/my_scripts/calc_metric.py"
    expected = json.loads((ROOT / "dependencies/source_audit.json").read_text())[
        "HighDiv/my_scripts/calc_metric.py"]["sha256"]
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise RuntimeError("metric source differs from the frozen evaluator")
    spec = importlib.util.spec_from_file_location("frozen_highdiv_metric", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def differential(formula, assignments, binary=None, failure_dir=None):
    metric = load_metric()
    class Traced(metric.WireCoverageStatistics):
        def __init__(self):
            super().__init__()
            self.events = []
        def evaluate_node(self, node, value):
            self.events.append(node)
            super().evaluate_node(node, value)
    stats = Traced()
    evaluator = metric.ManualSatisfiesMetric(Path(formula).read_text(), stats)
    root = metric.z3.And(evaluator._solver.assertions())
    nodes = nodes_preorder(root)
    indices = {n.get_id(): i for i, n in enumerate(nodes)}
    decls = variables(root)
    lines = []
    for a in assignments:
        mapping = {(v["name"], v["sort"]): v["value"] for v in a}
        lines.append(" ".join(str(mapping[str(v.decl().name()), str(v.sort())]).lower() for v in decls))
    with tempfile.TemporaryDirectory() as tmp:
        values = Path(tmp) / "values.txt"
        values.write_text("\n".join(lines)+"\n")
        cmd = [str(binary or ROOT / "build/lia_cover_sampler"), "-i", str(formula),
               "-o", tmp, "--audit-values", str(values)]
        try:
            results = subprocess.run(cmd, text=True, capture_output=True, check=True, timeout=60)
            internal = [json.loads(line) for line in results.stdout.splitlines()]
            if len(internal) != len(assignments):
                raise AssertionError("different number of prefixes")
            for idx, (assignment, actual) in enumerate(zip(assignments, internal)):
                stats.events.clear()
                model = [(v["name"], v["value"] if v["sort"] == "Bool" else int(v["value"])) for v in assignment]
                if not evaluator.count_sample(model):
                    raise AssertionError("oracle rejected input")
                storage = [stats._storage[n.get_id()] for n in nodes]
                states = [[str(int(zero)), str(int(one))] for one, zero, _ in storage]
                covered = sum(stats.node_count(*s) for s in storage)
                total = sum(stats.node_total(n.get_id(), s[2]) for n, s in zip(nodes, storage))
                events = [indices[n] for n in stats.events]
                visited_nodes = set(stats.events)
                visited = [int(n.get_id() in visited_nodes) for n in nodes]
                expected = dict(covered=covered, total=total, states=states, events=events, visited=visited)
                if actual != expected:
                    raise AssertionError(f"prefix {idx+1} mismatch: " +
                                         ", ".join(k for k in expected if actual[k] != expected[k]))
        except Exception:
            dest = Path(failure_dir or ROOT / "results/differential_failure")
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(formula, dest / "input.smt2")
            shutil.copyfile(values, dest / "values.txt")
            (dest / "models.json").write_text(json.dumps(assignments, indent=2))
            (dest / "command.json").write_text(json.dumps(cmd))
            raise
    return {"prefixes": len(assignments), "nodes": len(nodes), "covered": covered, "total": total}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("formula")
    p.add_argument("models")
    a = p.parse_args()
    assignments = [json.loads(line)["assignment"] for line in Path(a.models).read_text().splitlines()]
    print(json.dumps(differential(a.formula, assignments)))
