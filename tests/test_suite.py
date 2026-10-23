import io
import json
import math
import os
from pathlib import Path
import random
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from reference import witnesses
from validation import original_formula, verify, variables
from coverage_differential_test import differential, load_metric

BIN = Path(os.environ.get("LIA_BINARY", ROOT / "build/lia_cover_sampler"))
MATH = BIN.parent / "math_probe"


class MathTests(unittest.TestCase):
    def test_exact_counts_and_ranked_witnesses(self):
        rng = random.Random(1857)
        queries, answers = [], []
        for _ in range(8000):
            a = rng.randint(-40, 40)
            b = rng.randint(-200, 200) + rng.choice([0, 0, 2**100, -2**100])
            j = rng.choice([0, 1, 2, 3, 4, 8, 31, 63])
            desired = rng.randrange(2)
            lo = rng.randint(-40, 30)
            hi = lo+rng.randint(0, 50)
            hits = witnesses(a, b, j, desired, lo, hi)
            rank = rng.randrange(len(hits)) if hits else 0
            queries.append(f"hit {a} {b} {j} {desired} {lo} {hi} {rank}")
            answers.append(f"{len(hits)} {hits[rank] if hits else 'none'}")
        # Enormous intervals must take logarithmic work.
        queries += [f"hit 1 0 63 1 0 {2**128-1} 0",
                    f"hit -8 {-2**100} 63 0 {-2**90} {2**90-1} 0"]
        answers += [f"{2**127} {2**63}", f"{2**90} {-2**90}"]
        result = subprocess.run([MATH], input="\n".join(queries)+"\n", text=True,
                                capture_output=True, check=True, timeout=30)
        self.assertEqual(result.stdout.splitlines(), answers)

    def test_signed_division(self):
        queries, answers = [], []
        for a in range(-50, 51):
            for b in range(-12, 13):
                if b:
                    queries.append(f"div {a} {b}")
                    answers.append(f"{a//b} {-((-a)//b)}")
        result = subprocess.run([MATH], input="\n".join(queries)+"\n", text=True,
                                capture_output=True, check=True)
        self.assertEqual(result.stdout.splitlines(), answers)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def formula(self, text):
        p = self.base / "input.smt2"
        p.write_text(text)
        return p

    def run_sampler(self, formula, name="run", *extra):
        output = self.base / name
        subprocess.run([BIN, "-i", formula, "-o", output, "-n", "12", "-t", "0.8",
                        "--mode", "debug", *extra], capture_output=True, text=True, check=True,
                       timeout=10)
        rows = [json.loads(s) for s in (output / "models.jsonl").read_text().splitlines()]
        return output, rows

    def test_coverage_differential_test(self):
        p = self.formula("""(declare-const x Int)(declare-const y Int)(declare-const b Bool)
        (assert (and (or b (= x 0) (= (+ x y) 4))
         (>= (ite b (+ x y) (- y)) (- 100))
         (or (and (>= x 0) (<= x 100)) (and (< x 0) (= y 1)))))""")
        assignments = []
        for x, y, b in [(0, 1, False), (1, 3, False), (-3, 1, True), (2, 1, True),
                        (0, -5, True), (0, 1, False)]:
            assignments.append([dict(name="x", sort="Int", value=str(x)),
                                dict(name="y", sort="Int", value=str(y)),
                                dict(name="b", sort="Bool", value=b)])
        result = differential(p, assignments, binary=BIN, failure_dir=self.base / "failure")
        self.assertEqual(result["prefixes"], 6)
        self.assertGreater(result["covered"], 0)

    def test_outputs_valid_unique_complete_and_exact_prefix(self):
        p = self.formula("(declare-const x Int)(declare-const y Int)"
                         "(assert (= (+ x (* 2 y)) 10))(assert (and (>= y 0) (<= y 5)))")
        root = original_formula(p)
        for variant in ("full", "random", "no-cache", "global-only"):
            out, rows = self.run_sampler(p, variant, "--variant", variant)
            self.assertTrue(rows)
            self.assertLessEqual(len(rows), 6)
            self.assertEqual(len({json.dumps(r["assignment"], sort_keys=True) for r in rows}), len(rows))
            for row in rows:
                self.assertEqual(verify(root, row["assignment"]), "valid")
            differential(p, [r["assignment"] for r in rows], binary=BIN)

    def test_full_validator_rejects_missing_wrong_type_and_invalid(self):
        p = self.formula("(declare-const x Int)(declare-const b Bool)(assert (= x 2))(assert b)")
        root = original_formula(p)
        good = [dict(name="x", sort="Int", value="2"), dict(name="b", sort="Bool", value=True)]
        self.assertEqual(verify(root, good), "valid")
        with self.assertRaises(ValueError):
            verify(root, good[:1])
        with self.assertRaises(ValueError):
            verify(root, [good[0], dict(name="b", sort="Bool", value=1)])
        with self.assertRaises(ValueError):
            verify(root, [dict(name="x", sort="Int", value=2), good[1]])
        self.assertEqual(verify(root, [dict(name="x", sort="Int", value="3"), good[1]]), "invalid")
        self.assertEqual(verify(root, [good[0], dict(name="b", sort="Bool", value=False)]), "invalid")

    def test_special_names_and_legacy_limit(self):
        p = self.formula("(declare-const |x:y;z| Int)(declare-const b Bool)"
                         "(assert (= |x:y;z| 184467440737095516170))(assert (or b (not b)))")
        out, rows = self.run_sampler(p)
        self.assertTrue(rows)
        self.assertFalse(json.loads((out / "format.json").read_text())["legacy_compatible"])
        self.assertFalse(list(out.glob("*.samples")))
        for r in rows:
            self.assertEqual(verify(original_formula(p), r["assignment"]), "valid")
        m = load_metric()
        for line in ("0: b:true;\n", "0: x:y;z:1;\n"):
            with self.assertRaises((ValueError, IndexError)):
                list(m.parse_samples(io.StringIO(line)))

    def test_unsat_and_tiny_budget(self):
        p = self.formula("(declare-const x Int)(assert (> x 5))(assert (< x 0))")
        out, rows = self.run_sampler(p, "unsat")
        self.assertFalse(rows)
        self.assertEqual(json.loads((out / "summary.json").read_text())["status"], "unsat")
        out, rows = self.run_sampler(p, "tiny", "-t", "0.000001")
        self.assertFalse(rows)
        self.assertEqual(json.loads((out / "summary.json").read_text())["status"], "time_limit_without_model")

    def test_unsupported_local_operation_falls_back(self):
        p = self.formula("(declare-const x Int)(assert (= (mod x 3) 0))")
        out, rows = self.run_sampler(p)
        self.assertTrue(rows)
        self.assertFalse(json.loads((out / "format.json").read_text())["metric_compatible"])
        for r in rows:
            self.assertEqual(verify(original_formula(p), r["assignment"]), "valid")

    def test_interrupt_preserves_whole_lines_and_limit(self):
        p = self.formula("(declare-const x Int)(assert (>= x 0))")
        out = self.base / "interrupt"
        proc = subprocess.Popen([BIN, "-i", p, "-o", out, "-n", "1000000", "-t", "20"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(.15)
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=5)
        for line in (out / "models.jsonl").read_text().splitlines():
            self.assertEqual(verify(original_formula(p), json.loads(line)["assignment"]), "valid")
        self.assertEqual(json.loads((out / "summary.json").read_text())["status"], "interrupted")
        out, rows = self.run_sampler(p, "n1", "-n", "1")
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
