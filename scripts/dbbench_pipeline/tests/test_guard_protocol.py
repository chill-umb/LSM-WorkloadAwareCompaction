import importlib.util
import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))


def load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, PIPELINE / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


calibration = load_script("guard_calibration", "06_calibrate_live_guard.py")
holdout = load_script("guard_holdout", "06_validate_guard_holdout.py")
learning = load_script("learning_analysis", "11_analyze_learning.py")
oracle_parity = load_script("oracle_parity", "09_evaluate_oracle_parity.py")
from slo_statistics import censored_tolerance_bound  # noqa: E402


class GuardCalibrationTest(unittest.TestCase):
    def test_histogram_percentile_uses_merged_population(self):
        buckets = [0] * 64
        buckets[5] = 190
        buckets[10] = 10
        self.assertEqual(
            calibration.histogram_percentile(buckets, 0.95),
            calibration.bucket_upper_bound(5),
        )

    def test_streaming_calibration_samples_disjoint_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "latency_windows.jsonl"
            buckets = [0] * 64
            buckets[6] = 300
            with path.open("w") as handle:
                for index in range(600):
                    operation = {
                        "count": 300,
                        "sum_ns": 30_000,
                        "buckets": buckets,
                    }
                    handle.write(json.dumps({
                        "schema_version": 1,
                        "experiment_fingerprint": "fp",
                        "time_micros": index * 50_000,
                        "interval_micros": 50_000,
                        "get": operation,
                        "scan": operation,
                        "write": operation,
                    }) + "\n")
            values, intervals = calibration.read_run(
                path, "fp", rolling=20, minimum_samples=299
            )
            self.assertEqual(intervals, 600)
            self.assertEqual(len(values["get_avg"]), 30)
            self.assertEqual(len(values["scan_p95"]), 30)

    def test_operation_count_must_match_histogram_total(self):
        record = {
            "get": {"count": 2, "sum_ns": 10, "buckets": [1] + [0] * 63},
        }
        with self.assertRaises(ValueError):
            calibration.parse_operation(record, "get", Path("telemetry"), 1)

    def test_right_censored_episode_cannot_claim_an_upper_bound(self):
        completed = [float(value) for value in range(1, 31)]
        bound, metadata = censored_tolerance_bound(completed, [5.0])
        self.assertIsNone(bound)
        self.assertEqual(metadata["method"],
                         "right_censored_bound_unavailable")
        self.assertIsNone(metadata["achieved_confidence"])

    def test_non_finite_censored_episode_cannot_be_silently_dropped(self):
        bound, metadata = censored_tolerance_bound(
            [float(value) for value in range(1, 31)], [float("nan")])
        self.assertIsNone(bound)
        self.assertEqual(
            metadata["method"], "invalid_non_finite_censored_samples")

    def test_non_finite_completed_episode_keeps_its_parse_failure(self):
        bound, metadata = censored_tolerance_bound(
            [1.0, float("nan")], [5.0])
        self.assertIsNone(bound)
        self.assertEqual(metadata["method"], "invalid_non_finite_samples")


class OracleScoreEnvelopeTest(unittest.TestCase):
    def test_each_repeat_contributes_only_its_worst_shared_level(self):
        facts = [
            (1,
             {"max_score_by_level": {1: 2.0, 2: 4.0}},
             {"max_score_by_level": {1: 2.05, 2: 4.3, 3: 1.2}}),
            (2,
             {"max_score_by_level": {1: 2.0, 2: 4.0}},
             {"max_score_by_level": {1: 1.9, 2: 4.1}}),
        ]
        values, details, unexercised = oracle_parity.maximum_score_growth(facts)
        # Repeat 1: L2 grows by .3 against a .2 allowance => 1.5, worse than
        # L1's .05/.10. Repeat 2: L2 grows by .1/.2 => .5.
        self.assertAlmostEqual(values[0], 1.5)
        self.assertAlmostEqual(values[1], 0.5)
        self.assertEqual([item["level"] for item in details], [2, 2])
        self.assertEqual(unexercised, [{
            "repeat": 1, "level": 3, "oracle_max_score": 1.2,
        }])


class GuardHoldoutTest(unittest.TestCase):
    def write_shadow(self, path: Path, invalid_at=None):
        with path.open("w") as handle:
            for index in range(500):
                invalid = index == invalid_at
                handle.write(json.dumps({
                    "schema_version": 2,
                    "experiment_fingerprint": "fp",
                    "baseline_slo_sha256": "hash",
                    "time_micros": index * 50_000,
                    "interval_micros": 50_000,
                    "guard_ready": True,
                    "actuation_frame": True,
                    "would_override_frame": invalid,
                    "reason_mask": 1 if invalid else 0,
                    "observed_levels": 12,
                    "enforcement_enabled": False,
                    "intervention_applied": False,
                }) + "\n")

    def test_healthy_holdout_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shadow.jsonl"
            self.write_shadow(path)
            report = holdout.validate_run(path, "fp", "hash")
            self.assertTrue(report["passed"], report)
            self.assertEqual(report["would_override_fraction"], 0.0)

    def test_frequent_known_overrides_fail_intrusiveness_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shadow.jsonl"
            with path.open("w") as handle:
                for index in range(500):
                    handle.write(json.dumps({
                        "schema_version": 2,
                        "experiment_fingerprint": "fp",
                        "baseline_slo_sha256": "hash",
                        "time_micros": index * 50_000,
                        "interval_micros": 50_000,
                        "guard_ready": True,
                        "actuation_frame": True,
                        "would_override_frame": index % 20 == 0,
                        "reason_mask": 64,
                        "observed_levels": 12,
                        "enforcement_enabled": False,
                        "intervention_applied": False,
                    }) + "\n")
            report = holdout.validate_run(path, "fp", "hash")
            self.assertFalse(report["passed"])
            self.assertFalse(report["checks"]["override_fraction"])

    def test_holdout_cannot_reuse_a_calibration_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "baseline_slo.json"
            output = root / "readiness.json"
            manifest.write_text(json.dumps({
                "schema_version": 2,
                "guard_calibrated": True,
                "experiment_fingerprint": "fp",
                "selected_baseline_options": {
                    "size_millions": 1,
                    "size_ratio": 2,
                },
                "guard_calibration": {
                    "workload_seeds": [11, 12, 13],
                },
            }))
            manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()
            for repeat, seed in enumerate((11, 21, 22), 1):
                run = (
                    root / "holdout" / "1M" / "T2"
                    / f"repeat-{repeat:02d}" / "oracle"
                )
                run.mkdir(parents=True)
                (run / "COMPLETED").touch()
                (run / "metadata.env").write_text(
                    "experiment_fingerprint=fp\n"
                    "arm=oracle\n"
                    "rl_run_phase=holdout\n"
                    "rl_safety_enforcement=0\n"
                    f"baseline_slo_sha256={manifest_sha256}\n"
                    f"dbbench_seed={seed}\n"
                )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(PIPELINE / "06_validate_guard_holdout.py"),
                    "--manifest", str(manifest),
                    "--holdout-results", str(root / "holdout"),
                    "--output", str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("reuses calibration workload seed", completed.stderr)


class LearningHealthGateTest(unittest.TestCase):
    def test_learned_arm_requires_real_optimizer_and_residual(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "server_summary.json"
            output = root / "health.json"
            summary.write_text(json.dumps({
                "schema_version": 2,
                "credit_assignment_version": 2,
                "eval_mode": False,
                "analytic_prior": True,
                "shared_trunk": True,
                "clients_drained": True,
                "training_quiesced": True,
                "pending_windows_at_shutdown": 0,
                "unresolved_decisions_at_shutdown": 0,
                "protocol_errors": 0,
                "reward_invalid_intervals": 0,
                "accepted_accounting_balanced": True,
                "proposal_accounting_balanced": True,
                "trainer_error": None,
                "finalized_transitions": 64,
                "full_horizon_transitions": 64,
                "replay_size": 64,
                "train_steps": 3,
                "max_abs_residual_advantage": 0.01,
                "argmax_comparison_count": 4,
            }))
            completed = subprocess.run(
                [sys.executable, str(PIPELINE / "10_validate_learning_health.py"),
                 "--summary", str(summary), "--arm", "rl",
                 "--output", str(output)],
                check=False, capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(json.loads(output.read_text())["passed"])

    def test_hard_reward_invalid_interval_fails_an_otherwise_healthy_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "server_summary.json"
            output = root / "health.json"
            summary.write_text(json.dumps({
                "schema_version": 2,
                "credit_assignment_version": 2,
                "eval_mode": False,
                "analytic_prior": True,
                "shared_trunk": True,
                "clients_drained": True,
                "training_quiesced": True,
                "pending_windows_at_shutdown": 0,
                "unresolved_decisions_at_shutdown": 0,
                "protocol_errors": 0,
                "reward_invalid_intervals": 1,
                "accepted_accounting_balanced": True,
                "proposal_accounting_balanced": True,
                "trainer_error": None,
                "full_horizon_transitions": 64,
                "replay_size": 64,
                "train_steps": 3,
                "max_abs_residual_advantage": 0.01,
                "argmax_comparison_count": 4,
            }))
            completed = subprocess.run(
                [sys.executable, str(PIPELINE / "10_validate_learning_health.py"),
                 "--summary", str(summary), "--arm", "rl",
                 "--output", str(output)],
                check=False, capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 1)
            report = json.loads(output.read_text())
            self.assertFalse(report["checks"]["no_reward_invalid_intervals"])
            self.assertFalse(report["passed"])

    def test_active_trainer_cannot_pass_completion_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "server_summary.json"
            output = root / "health.json"
            summary.write_text(json.dumps({
                "schema_version": 2,
                "credit_assignment_version": 2,
                "eval_mode": False,
                "analytic_prior": True,
                "shared_trunk": True,
                "clients_drained": True,
                "training_quiesced": False,
                "pending_windows_at_shutdown": 0,
                "unresolved_decisions_at_shutdown": 0,
                "protocol_errors": 0,
                "reward_invalid_intervals": 0,
                "accepted_accounting_balanced": True,
                "proposal_accounting_balanced": True,
                "trainer_error": None,
                "finalized_transitions": 64,
                "full_horizon_transitions": 64,
                "replay_size": 64,
                "train_steps": 3,
                "max_abs_residual_advantage": 0.01,
                "argmax_comparison_count": 4,
            }))
            completed = subprocess.run(
                [sys.executable, str(PIPELINE / "10_validate_learning_health.py"),
                 "--summary", str(summary), "--arm", "rl",
                 "--output", str(output)],
                check=False, capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 1)
            report = json.loads(output.read_text())
            self.assertFalse(report["checks"]["training_quiesced"])
            self.assertFalse(report["passed"])

    def test_frozen_scan_constraint_keeps_scan_amp_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "summary.csv"
            output = root / "acceptance.json"
            rows = []
            for repeat in range(1, 11):
                common = {
                    "size_millions": 1, "size_ratio": 2,
                    "repeat": repeat, "workload_profile": "balanced-v1",
                    "experiment_fingerprint": "fp", "dbbench_seed": repeat,
                    "baseline_slo_sha256": "hash",
                    "get_operations": 100, "put_operations": 100,
                    "scan_operations": 100, "user_write_bytes": 1000,
                    "space_amplification": 1.0,
                    "get_latency_avg_us": 10, "get_latency_p95_us": 20,
                    "scan_latency_avg_us": 10, "scan_latency_p95_us": 20,
                    "write_latency_avg_us": 10, "write_latency_p95_us": 20,
                    "get_latency_p99_us": 30, "scan_latency_p99_us": 30,
                    "write_latency_p99_us": 30,
                    "stall_seconds": 0, "stall_events": 0,
                }
                rows.append({**common, "arm": "regular",
                             "write_amplification": 2.0,
                             "point_read_amplification": 2.0,
                             "sorted_run_seeks_per_scan": 2.0,
                             "scan_amplification": 1.0})
                rows.append({**common, "arm": "rl",
                             "write_amplification": 1.9,
                             "point_read_amplification": 1.9,
                             "sorted_run_seeks_per_scan": 1.9,
                             "scan_amplification": 1.03})
            with summary.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            completed = subprocess.run(
                [sys.executable, str(PIPELINE / "07_evaluate_paired.py"),
                 str(summary), "--size-millions", "1", "--size-ratio", "2",
                 "--minimum-pairs", "10", "--scan-objective",
                 "sorted_run_seeks", "--space-margin", ".02", "--pilot",
                 "--output", str(output)],
                check=False, capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(output.read_text())
            self.assertNotIn("scan_amplification", report["checks"])
            self.assertIn("scan_amplification", report["diagnostics"])
            self.assertFalse(report["formal_acceptance"])

    def test_learning_analyzer_stride_is_per_level_and_steps_are_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metrics = root / "metrics.jsonl"
            with metrics.open("w") as handle:
                for _ in range(4):
                    for level in range(4):
                        handle.write(json.dumps({
                            "level": level, "loss": 1.0, "action": 0,
                            "reward": 0.0, "epsilon": 0.1,
                            "analytic_advantage": 0.2,
                            "residual_advantage": 0.01,
                            "override_rate_100": 0.0,
                        }) + "\n")
            metrics.with_name("server_summary.json").write_text(json.dumps({
                "schema_version": 2, "train_steps": 7,
                "finalized_transitions": 40, "replay_size": 40,
                "levels": {
                    str(level): {
                        "finalized_transitions": 10,
                        "accepted_decisions": 10,
                        "rejected_decisions": 0,
                        "override_relabels": 0,
                        "full_horizon_transitions": 10,
                        "boundary_truncated_transitions": 0,
                        "shutdown_terminal_transitions": 0,
                    } for level in range(4)
                },
            }))
            report = learning.read_arm(metrics, stride=4)
            self.assertEqual(set(report), {0, 1, 2, 3})
            for level in report.values():
                self.assertEqual(level["samples"], 4)
                self.assertEqual(level["parsed"], 1)
                self.assertEqual(level["gradient_steps"], 7)
                self.assertEqual(level["gradient_steps_exact"], 1.0)


if __name__ == "__main__":
    unittest.main()
