import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))
from pipeline_stats import objective_verdict
from research_objective import load_contract, metric_specs


class ObjectiveTest(unittest.TestCase):
    def test_each_boundary_and_underpowered_interval(self):
        for strict, margin in ((False, .02), (False, 0), (True, 0)):
            for values, expected in (([margin - .01] * 10, "passed"),
                                     ([margin + .01] * 10, "failed"),
                                     ([margin - .2, margin + .2] * 5,
                                      "undecidable")):
                self.assertEqual(objective_verdict(
                    values, margin, 10, strict=strict)["verdict"], expected)
        self.assertEqual(objective_verdict([0.] * 10, 0, 10)["verdict"], "passed")
        self.assertEqual(objective_verdict([0.] * 10, 0, 10, strict=True)
                         ["verdict"], "failed")
        self.assertEqual(objective_verdict([-.1] * 5, 0, 10, strict=True)
                         ["verdict"], "undecidable")

    def run_evaluator(self, mutation=None, extra=()):
        contract, fingerprint = load_contract()
        specs = metric_specs(contract, .02)
        rows = []
        for repeat in range(1, 11):
            common = dict(size_millions=10, size_ratio=2, repeat=repeat,
                          dbbench_seed=repeat, workload_profile="test",
                          experiment_fingerprint="same-cell",
                          baseline_slo_sha256="same-manifest",
                          research_objective_sha256=fingerprint,
                          space_relative_margin=.02,
                          get_operations=100, put_operations=100,
                          scan_operations=100, user_write_bytes=1000)
            base = {**common, **{key: 10 for key, _, _ in specs}, "arm": "regular"}
            learned = {**base, "arm": "rl", "point_read_amplification": 9,
                       "write_amplification": 10.1}
            if mutation:
                mutation(base, learned, repeat)
            rows.extend((base, learned))
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.csv"
            with summary.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            result = subprocess.run([
                sys.executable, str(PIPELINE / "07_evaluate_paired.py"),
                str(summary), "--size-millions", "10", "--size-ratio", "2",
                "--space-margin", ".02", *extra], capture_output=True, text=True)
            return result, json.loads(result.stdout) if result.stdout else None

    def test_write_noninferiority_is_relative_and_tail_is_p99(self):
        result, report = self.run_evaluator()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(report["formal_acceptance"])
        self.assertIn("write_latency_p99_us", report["checks"])
        self.assertNotIn("write_latency_p95_us", report["checks"])
        self.assertAlmostEqual(report["checks"]["write_amplification"]
                               ["ci95"]["mean"], .01)

    def test_wide_latency_is_undecidable(self):
        def mutate(base, learned, repeat):
            learned["write_latency_p99_us"] *= .7 if repeat % 2 else 1.3
        result, report = self.run_evaluator(mutate)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(report["checks"]["write_latency_p99_us"]["verdict"],
                         "undecidable")
        self.assertFalse(report["formal_acceptance"])

    def test_one_increased_stall_pair_does_not_override_paired_envelope(self):
        def mutate(base, learned, repeat):
            learned["stall_seconds"] = 10.01 if repeat == 1 else 8
        result, report = self.run_evaluator(mutate)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(report["checks"]["stall_seconds"]["passed"])

    def test_missing_objective_fails_closed_but_pilot_is_labelled(self):
        def mutate(base, learned, repeat):
            base["research_objective_sha256"] = ""
            learned["research_objective_sha256"] = ""
        result, _ = self.run_evaluator(mutate)
        self.assertNotEqual(result.returncode, 0)
        result, report = self.run_evaluator(mutate, ["--pilot"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(report["formal_acceptance"])

    def test_changed_contract_rejected(self):
        contract, _ = load_contract()
        contract["constraints"]["write"]["relative_margin"] = .5
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_text(json.dumps(contract))
            with self.assertRaises(ValueError):
                load_contract(path)


if __name__ == "__main__":
    unittest.main()
