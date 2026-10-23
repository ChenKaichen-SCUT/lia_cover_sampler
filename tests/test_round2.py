import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from coverage_differential_test import differential
from validation import original_formula, verify

BIN = Path(os.environ.get("LIA_BINARY", ROOT / "build/lia_cover_sampler"))


class RoundTwoIntegration(unittest.TestCase):
    def test_profiles_preserve_exact_output_and_attribution(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            formula = base / "independent.smt2"
            formula.write_text("".join(
                f"(declare-const x{i} Int)(assert (and (<= -1 x{i}) (<= x{i} 0)))"
                for i in range(5)))
            root = original_formula(formula)
            for profile in ("schedule", "directions", "polish"):
                output = base / profile
                subprocess.run([str(BIN), "-i", str(formula), "-o", str(output),
                                "-n", "8", "-t", "0.5", "--profile", profile],
                               check=True, capture_output=True, timeout=5)
                rows = [json.loads(line) for line in (output / "models.jsonl").read_text().splitlines()]
                self.assertTrue(rows)
                previous = 0
                for row in rows:
                    self.assertEqual(verify(root, row["assignment"]), "valid")
                    self.assertEqual(row["covered"] - previous, row["final_emitted_gain"])
                    self.assertEqual(row["new_coverage"], row["final_emitted_gain"])
                    self.assertEqual(row["seed_gain_retained"] + row["refinement_added"],
                                     row["gain_after_refinement"])
                    self.assertEqual(row["seed_gain_retained"] + row["seed_gain_lost"],
                                     row["gain_before_refinement"])
                    self.assertEqual(row["gain_after_refinement"], row["final_emitted_gain"])
                    self.assertGreaterEqual(row["gain_after_refinement"], row["gain_before_refinement"])
                    previous = row["covered"]
                differential(formula, [row["assignment"] for row in rows], binary=BIN,
                             failure_dir=base / "failure")
                summary = json.loads((output / "summary.json").read_text())
                counts = summary["statistics"]["counts"]
                self.assertEqual(sum(value for name, value in counts.items()
                                     if name.startswith("action_") and name.endswith("_samples")),
                                 len(rows) - 1)
                self.assertIn("action_seconds", summary["statistics"])

    def test_independent_feature_switches(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            formula = base / "input.smt2"
            formula.write_text("(declare-const x Int)(assert (>= x 0))")
            output = base / "run"
            subprocess.run([str(BIN), "-i", str(formula), "-o", str(output),
                            "-n", "1", "-t", "0.2", "--disable", "quotas,fair,matching,polish,scheduler"],
                           check=True, capture_output=True)
            config = json.loads((output / "config.json").read_text())
            for name in ("quotas", "fair", "matching", "polish"):
                self.assertFalse(config[name])
            self.assertEqual(config["scheduler"], "legacy")


if __name__ == "__main__":
    unittest.main()
