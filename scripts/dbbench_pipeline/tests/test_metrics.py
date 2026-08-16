#!/usr/bin/env python3
"""Synthetic checks for the experiment's formal amplification formulas."""

import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "04_generate_graphs.py"
SPEC = importlib.util.spec_from_file_location("dbbench_graphs", SCRIPT)
GRAPH = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(GRAPH)


class AmplificationMetricTest(unittest.TestCase):
    def test_synthetic_get_scan_write_and_space(self):
        metrics = GRAPH.amplification_metrics(
            # Logical bytes may include inserts, updates, and deletes; the
            # formula depends on their byte total, not their operation label.
            flush_bytes=30, compact_bytes=70, user_write_bytes=50,
            # These logical probes include Bloom rejections and cache hits.
            point_probes=8, gets=2,
            scan_returned=4, scan_skips=6,
            sorted_run_seeks=5, scans=2,
            physical_sst_bytes=120, live_logical_bytes=100,
        )
        self.assertEqual(metrics["write_amplification"], 2.0)
        self.assertEqual(metrics["point_read_amplification"], 4.0)
        self.assertEqual(metrics["scan_amplification"], 2.5)
        self.assertEqual(metrics["sorted_run_seeks_per_scan"], 2.5)
        self.assertEqual(metrics["space_amplification"], 1.2)

    def test_empty_operation_classes_are_not_reported_as_zero_cost(self):
        metrics = GRAPH.amplification_metrics(
            flush_bytes=1, compact_bytes=1, user_write_bytes=0,
            point_probes=0, gets=0, scan_returned=0, scan_skips=3,
            sorted_run_seeks=0, scans=0,
            physical_sst_bytes=0, live_logical_bytes=0,
        )
        for value in metrics.values():
            self.assertTrue(math.isnan(value))

    def test_drain_phase_is_separated_without_removing_it_from_totals(self):
        run_log = "\n".join((
            "RL_DRAIN_START_MICROS 1000000",
            "RL_DRAIN_DB_PENDING_BEFORE_BYTES 4096",
            "RL_DRAIN_DB_PENDING_AFTER_BYTES 0",
            "RL_DRAIN_END_MICROS 2500000",
        ))
        events = (
            {"job": 1, "event": "compaction_started", "rl_drain": False},
            {"job": 1, "event": "compaction_finished",
             "rl_drain": False, "total_output_size": 100,
             "compaction_time_micros": 200000},
            {"job": 2, "event": "compaction_started", "rl_drain": True},
            {"job": 2, "event": "compaction_finished",
             "rl_drain": True, "total_output_size": 300,
             "compaction_time_micros": 400000},
        )
        with tempfile.TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "LOG"
            log_path.write_text("\n".join(
                f"EVENT_LOG_v1 {json.dumps(event)}" for event in events))
            phase = GRAPH.parse_drain(run_log, log_path)
        self.assertEqual(phase["drain_seconds"], 1.5)
        self.assertEqual(phase["drain_pending_bytes_before"], 4096)
        self.assertEqual(phase["drain_pending_bytes_after"], 0)
        self.assertEqual(phase["workload_compaction_write_bytes"], 100)
        self.assertEqual(phase["drain_compaction_write_bytes"], 300)
        self.assertEqual(phase["workload_compaction_seconds"], 0.2)
        self.assertEqual(phase["drain_compaction_seconds"], 0.4)


if __name__ == "__main__":
    unittest.main()
