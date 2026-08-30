"""Manifest-contract tests that do not require the PyTorch test environment."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402


class BaselineManifestContractTest(unittest.TestCase):
    def test_reward_keeps_formal_limits_separate_from_live_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "baseline_slo.json")
            manifest = {
                "schema_version": 2,
                "metric_definitions_version": "trigger-v2-logical-v2",
                "experiment_fingerprint": "fp",
                "guard_calibrated": True,
            }
            for operation in ("get", "scan", "write"):
                manifest[f"{operation}_latency_avg_ns_limit"] = 10.0
                manifest[f"{operation}_latency_p95_ns_limit"] = 20.0
                manifest[f"guard_{operation}_latency_avg_ns_limit"] = 110.0
                manifest[f"guard_{operation}_latency_p95_ns_limit"] = 220.0
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)

            with mock.patch.dict(os.environ, {
                "RL_BASELINE_SLO_PATH": path,
                "RL_EXPERIMENT_FINGERPRINT": "fp",
            }):
                limits = config._load_latency_limits()
            self.assertEqual(limits["get_latency_avg_ns_limit"], 10.0)
            self.assertEqual(limits["scan_latency_p95_ns_limit"], 20.0)

    def test_manifest_requires_the_fingerprint_used_by_cpp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "baseline_slo.json")
            manifest = {
                "schema_version": 2,
                "metric_definitions_version": "trigger-v2-logical-v2",
                "experiment_fingerprint": "fp",
                "guard_calibrated": True,
                **{
                    f"{prefix}{operation}_latency_{statistic}_ns_limit": 1.0
                    for prefix in ("", "guard_")
                    for operation in ("get", "scan", "write")
                    for statistic in ("avg", "p95")
                },
            }
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with mock.patch.dict(os.environ, {
                "RL_BASELINE_SLO_PATH": path,
                "RL_EXPERIMENT_FINGERPRINT": "",
            }):
                with self.assertRaises(RuntimeError):
                    config._load_latency_limits()


if __name__ == "__main__":
    unittest.main()
