"""23_static_profiles.py (Theorem A.2(ii); PREREGISTRATION D-14 §3): the
survival-weighted and last-level-emptying profiles, their refusals, and the
inputs read from a run directory.

The worked case: L = 4, T = 2, K0 = 4, F = 2, C_1 = 16, B_L = 128, so the
fanouts at m = 1 are all 2 and their product B_L / (K0 F) = 16. With merged
bytes per user byte v = (1, 0.9, 0.8, 0.7), lambda = (16 * 0.504)^(1/4).
"""
import importlib.util
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PIPELINE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "static_profiles", PIPELINE / "23_static_profiles.py")
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)


def pooled(v=(1.0, 0.9, 0.8, 0.7), **extra):
    user = 1000.0
    s = [x * user for x in v] + [0.0, 0.0]
    return {"L": 4, "T": 2.0, "K0": 4, "F": 2.0, "C1": 16.0, "B_L": 128.0,
            "num_levels": 6, "m": [1.0] * 6, "user_bytes": user, "s": s,
            "o": [x * 0.5 for x in s], **extra}


class SurvivalWeightedTest(unittest.TestCase):
    def test_worked_case(self):
        p = pooled()
        result = sp.survival_weighted(p)
        lam = (16 * 0.504) ** 0.25
        self.assertAlmostEqual(result["lambda"], lam)
        # f_i v_i is the same at every level, and the product is fixed.
        for f, v in zip(result["fanouts"], result["v"]):
            self.assertAlmostEqual(f * v, lam)
        self.assertAlmostEqual(math.prod(result["fanouts"]), 16.0)
        m = [float(x) for x in result["vector"].split(":")]
        self.assertEqual(len(m), 6)
        self.assertAlmostEqual(m[1], round(lam * 8 / 16, 4))
        self.assertEqual(m[4:], [1.0, 1.0])
        # The multipliers give back the fanouts, the last one included.
        exact = [1.0, lam / 2, lam / 2 * (lam / 0.9) / 2, 0, 1.0, 1.0]
        exact[3] = exact[2] * (lam / 0.8) / 2
        for f, target in zip(sp.fanouts(p, exact), result["fanouts"]):
            self.assertAlmostEqual(f, target)
        self.assertEqual(result["clipped"], {})

    def test_levels_from_l_down_keep_one(self):
        # With B_L off the nominal C_L, a recursion run one level too far
        # would move m_4 away from 1.
        m = sp.survival_weighted(pooled(B_L=200.0))["vector"].split(":")
        self.assertEqual(m[4:], ["1", "1"])
        self.assertNotEqual(m[3], "1")

    def test_overlap_constants_are_reported(self):
        # o_i = 0.5 at every level; the fanouts at m = 1 are all 2.
        result = sp.survival_weighted(pooled())
        self.assertEqual(result["overlap_constants"], [0.25] * 4)

    def test_entries_outside_the_bounds_are_clipped(self):
        # A large B_L makes every fanout, and so m_1, large.
        result = sp.survival_weighted(pooled(B_L=128.0 * 50))
        m = [float(x) for x in result["vector"].split(":")]
        self.assertEqual(m[1], 2.0)
        self.assertIn("1", result["clipped"])

    def test_a_level_with_no_merges_has_no_optimum(self):
        with self.assertRaisesRegex(ValueError, r"levels \[2\]"):
            sp.survival_weighted(pooled(v=(1.0, 0.9, 0.0, 0.7)))

    def test_a_shrinking_ladder_after_clipping_is_refused(self):
        # Small v_0, v_1 make m_1 and m_2 large (clipped to 2), while
        # v_2 = 4 v_1 leaves f_2 = T m_3 / m_2 below 1: m_3 = 0.71 against
        # m_2 = 2, and 0.71 * 2 < 2.
        with self.assertRaisesRegex(ValueError, "level 3's target"):
            sp.survival_weighted(pooled(v=(0.05, 0.05, 0.2, 0.05)))

    def test_a_tree_without_interior_levels_is_refused(self):
        with self.assertRaisesRegex(ValueError, "L = 1"):
            sp.survival_weighted(pooled(L=1))


class LastLevelEmptyingTest(unittest.TestCase):
    def test_level_above_the_last_held_at_two(self):
        result = sp.last_level_emptying(pooled())
        self.assertEqual(result["vector"], "1:1:1:2:1:1")
        self.assertEqual(result["held_level"], 3)


class PoolTest(unittest.TestCase):
    def test_sums_bytes_and_averages_sizes(self):
        a = {**pooled(), "fingerprint": "x", "B_L": 100.0}
        b = {**pooled(), "fingerprint": "x", "B_L": 200.0}
        p = sp.pool([a, b])
        self.assertEqual(p["B_L"], 150.0)
        self.assertEqual(p["user_bytes"], 2000.0)
        self.assertEqual(p["s"][0], 2000.0)

    def test_refuses_different_configurations_or_depths(self):
        for other in ({"fingerprint": "y"}, {"L": 5}):
            with self.assertRaises(ValueError, msg=other):
                sp.pool([{**pooled(), "fingerprint": "x"},
                         {**pooled(), "fingerprint": "x", **other}])


def event(when, **fields):
    return f"2026/09/30-10:00:00 1 EVENT_LOG_v1 {json.dumps({'time_micros': when, **fields})}\n"


class MeasureRunTest(unittest.TestCase):
    """A run directory: a release before n_w (the load's) and after it; the
    first after n_w is the settled tree."""

    def test_reads_the_settled_tree_and_flush_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "metadata.env").write_text(
                "experiment_fingerprint=fp\nnum_levels=4\nsize_ratio=2\n"
                "level0_file_num_compaction_trigger=4\n"
                "max_bytes_for_level_base=1000\nlevel_target_multipliers=none\n")
            tickers = {"rocksdb.bytes.written": 0,
                       **{name: 0 for name in sp.host_log.LEVEL_TICKERS}}
            empty = [[0, 0, 0, 0]] * 4
            records = [
                {"type": "header"},
                {"type": "stamp", "name": "measure_start", "op": 0, "wall_us": 100,
                 "h": 0, "tickers": dict(tickers), "levels": empty},
                {"type": "job_begin", "job": 5, "op": 1, "start_level": 1,
                 "output_level": 2, "trivial": 0, "s": 300, "o": 600},
                {"type": "job_end", "job": 5, "op": 2, "start_level": 1,
                 "output_level": 2, "trivial": 0, "s": 300, "o": 600, "x": 800,
                 "ok": 1},
                {"type": "h", "op": 2, "h": 7},
                {"type": "stamp", "name": "drain_start", "op": 2, "wall_us": 190,
                 "h": 7, "tickers": {**tickers, "rocksdb.bytes.written": 1000},
                 "levels": empty},
                # A drain merge: outside mixgraph's flows.
                {"type": "job_begin", "job": 8, "op": 2, "start_level": 1,
                 "output_level": 2, "trivial": 0, "s": 5000, "o": 0},
                {"type": "job_end", "job": 8, "op": 2, "start_level": 1,
                 "output_level": 2, "trivial": 0, "s": 5000, "o": 0, "x": 5000,
                 "ok": 1},
                {"type": "h", "op": 2, "h": 7},
                {"type": "stamp", "name": "drain_end", "op": 2, "wall_us": 200,
                 "h": 7, "tickers": {**tickers, "rocksdb.bytes.written": 1000},
                 "levels": empty},
            ]
            (run / "host_log.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in records))
            (run / "rocksdb_LOG.txt").write_text(
                event(50, event="compaction_release", job=1,
                      occupancy_bytes=[0, 10, 0, 0]) +
                event(110, event="flush_started", job=4) +
                event(111, event="table_file_creation", job=4, file_size=90) +
                event(120, event="compaction_release", job=5,
                      occupancy_bytes=[5, 20, 40, 900]) +
                event(150, event="compaction_release", job=6,
                      occupancy_bytes=[0, 0, 0, 999]) +
                event(160, event="flush_started", job=7) +
                event(161, event="table_file_creation", job=7, file_size=110) +
                # The drain's partial flush: not a steady-state flush size.
                event(192, event="flush_started", job=9) +
                event(193, event="table_file_creation", job=9, file_size=3))
            m = sp.measure_run(run)
        self.assertEqual((m["L"], m["B_L"], m["F"]), (3, 900.0, 100.0))
        self.assertEqual((m["s"][1], m["o"][1], m["user_bytes"]), (300.0, 600.0, 1000.0))
        self.assertEqual(m["m"], [1.0] * 4)


class MainTest(unittest.TestCase):
    """A refusal of D-14 §3 is a report (25 skips the arm); any other failure
    writes none (25 stops the workload)."""

    def run_main(self, measured):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "profiles.json"
            argv = ["23", "run-1", "run-2", "--output", str(output)]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(sp, "measure_run", side_effect=measured):
                try:
                    code = sp.main()
                except SystemExit as exit:
                    code = str(exit)
            return code, (json.loads(output.read_text()) if output.exists() else None)

    def test_runs_that_disagree_on_depth_refuse_both_profiles(self):
        code, report = self.run_main([{**pooled(), "fingerprint": "x"},
                                      {**pooled(L=5), "fingerprint": "x"}])
        self.assertEqual(code, 1)
        self.assertIsNone(report["inputs"])
        self.assertEqual(report["runs"], ["run-1", "run-2"])
        for name in ("survival_weighted", "last_level_emptying"):
            self.assertIn("the runs differ in L", report[name]["refused"])

    def test_other_failures_write_no_report(self):
        for measured, message in (
                ([{**pooled(), "fingerprint": "x"}, {**pooled(), "fingerprint": "y"}],
                 "the runs differ in fingerprint"),
                (ValueError("run-2: host log: no drain_end stamp"), "no drain_end")):
            with self.subTest(message=message):
                code, report = self.run_main(measured)
                self.assertIn(message, code)
                self.assertIsNone(report)


if __name__ == "__main__":
    unittest.main()
