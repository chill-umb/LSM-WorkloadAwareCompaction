"""04 and the host log with PREREGISTRATION D-29's workloads, on the
evaluator fixture converted to host log schema 3: W4's inserts count as
operations served; the largest flush is reported and flagged when it fills
the write buffer (D-29 §3); and W5's phase_start stamps split the measured
phase into phases scored on their own, which add up to the run (D-29 §2)."""
import csv
import json
import math
import subprocess
import unittest
from pathlib import Path

import host_log
from tests.test_cost_model_v2 import rewrite, stage30, to_schema3
from tests.test_evaluator import Run, graphs

PIPELINE = Path(__file__).resolve().parents[1]


def schema3(run):
    sizes, _ = graphs.flush_jobs_from_events(
        graphs.read_events(run.dir / "rocksdb_LOG.txt"))
    rewrite(run, to_schema3(sizes))
    return sizes


def with_phases(switches):
    """Insert a phase_start stamp after the first H sample at or past each
    (phase, op): H as the last install left it, every ticker and the stall
    counter interpolated between measure_start and drain_end by operation."""
    def convert(records):
        stamps = {r["name"]: r for r in records if r.get("type") == "stamp"}
        first, last = stamps["measure_start"], stamps["drain_end"]
        span = last["op"] - first["op"]
        out, pending = [], list(switches)
        for r in records:
            out.append(r)
            if pending and r.get("type") == "h" and r["op"] >= pending[0][1]:
                phase, op = pending.pop(0)
                share = (op - first["op"]) / span

                def between(a, b):
                    return int(a + share * (b - a))
                out.append({
                    "type": "stamp", "name": "phase_start", "phase": phase,
                    "t_us": r["t_us"] + 1, "wall_us": first["wall_us"] + op,
                    "op": op, "h": r["h"],
                    "stall_micros": between(first["stall_micros"],
                                            last["stall_micros"]),
                    "levels": first["levels"],
                    "tickers": {k: between(v, last["tickers"][k])
                                for k, v in first["tickers"].items()}})
        return out
    return convert


class InsertTest(unittest.TestCase):
    def test_inserts_are_operations_served(self):
        run = Run()
        self.addCleanup(run.close)
        # 60 Puts and 50 inserts serve what 110 Puts served.
        run.edit("run.log", "Puts:110 ", "Puts:60 ")
        run.edit("run.log", "15.0 scan)", "15.0 scan, Inserts:50)")
        row = graphs.collect_arm(run.dir)
        self.assertEqual((row["put_operations"], row["insert_operations"]),
                         (60, 50))
        self.assertEqual(row["measured_operations"], 710)

    def test_none_outside_insert_mode(self):
        run = Run()
        self.addCleanup(run.close)
        self.assertEqual(graphs.collect_arm(run.dir)["insert_operations"], 0)


class FlushFlagTest(unittest.TestCase):
    def setUp(self):
        self.run = Run()
        self.addCleanup(self.run.close)
        sizes = schema3(self.run)
        # The measured phase's flushes: jobs 10 and 12 before the drain, 13
        # in it.
        self.largest = max(sizes[job] for job in (10, 12, 13))

    def row_with_buffer(self, buffer):
        self.run.edit("metadata.env", ":k64:v960:fixture",
                      f":k64:v960:wb{int(buffer)}:fixture")
        return graphs.collect_arm(self.run.dir)

    def test_a_flush_below_the_buffer(self):
        row = self.row_with_buffer(self.largest * 2)
        self.assertEqual(row["largest_flush_bytes"], self.largest)
        self.assertEqual(row["largest_flush_buffer_fraction"], 0.5)
        self.assertEqual(row["full_buffer_flush"], 0)

    def test_a_flush_that_fills_the_buffer_is_flagged(self):
        self.assertEqual(self.row_with_buffer(self.largest)["full_buffer_flush"], 1)

    def test_nan_before_schema_3(self):
        run = Run()
        self.addCleanup(run.close)
        row = graphs.collect_arm(run.dir)
        self.assertTrue(math.isnan(row["largest_flush_bytes"]))


class PhaseTest(unittest.TestCase):
    SWITCHES = ((2, 500), (3, 800))

    def setUp(self):
        self.run = Run()
        self.addCleanup(self.run.close)
        self.sizes = schema3(self.run)
        rewrite(self.run, with_phases(self.SWITCHES))
        self.records = host_log.load(self.run.dir / "host_log.jsonl")

    def test_the_log_checks_and_splits(self):
        self.assertEqual(host_log.check(self.records), [])
        at = host_log.stamp_index(self.records)
        self.assertIn("phase_start:2", at)
        bounds = host_log.phase_bounds(self.records, at)
        self.assertEqual([phase for phase, _, _ in bounds], [1, 2, 3])
        self.assertEqual(bounds[0][1], at["measure_start"])
        self.assertEqual(bounds[-1][2], at["drain_end"])

    def test_phases_are_scored_and_add_up_to_the_run(self):
        row = graphs.collect_arm(self.run.dir)
        phases = row["_phases"]
        self.assertEqual([p["phase_operations"] for p in phases], [210, 300, 200])
        self.assertEqual(sum(p["phase_held_byte_operations"] for p in phases),
                         row["held_byte_operations"])
        # Bytes where the host log puts the jobs: flush 10 and merge 11 in
        # phase 1, flush 12 in phase 2, the drain's flush 13 and merge 14 in
        # phase 3, as the drain is in the run.
        self.assertEqual([p["phase_flush_bytes"] for p in phases],
                         [self.sizes[10], self.sizes[12], self.sizes[13]])
        self.assertEqual([p["phase_compaction_bytes"] for p in phases],
                         [3000, 0, 1500])
        # Cost model 1 is a sum over the window, so the phases' costs are
        # the run's, split.
        self.assertEqual(row["objective_status"], "priced")
        for cost in ("C_W", "C_R", "C_S"):
            self.assertAlmostEqual(sum(p[cost] for p in phases), row[cost])
        self.assertFalse(math.isnan(phases[0]["J_balanced_cs1"]))
        self.assertEqual(sum(p["phase_filter_probes"] for p in phases),
                         row["filter_probes"])

    def test_04_writes_phases_csv(self):
        ran = subprocess.run(
            ["python3", str(PIPELINE / "04_generate_graphs.py"), "--results",
             str(self.run.root), "--summary-only"], capture_output=True, text=True)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        with (self.run.root / "graphs" / "phases.csv").open() as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([r["phase"] for r in rows], ["1", "2", "3"])
        self.assertEqual({r["arm"] for r in rows}, {"native"})
        with (self.run.root / "graphs" / "summary.csv").open() as handle:
            self.assertNotIn("_phases", next(csv.reader(handle)))

    def test_compaction_measurements_read_a_phased_log(self):
        ran = subprocess.run(
            ["python3", str(PIPELINE / "compaction_measurements.py"),
             str(self.run.dir / "host_log.jsonl"), "--num-levels", "2",
             "--output", str(self.run.root / "cm.json")],
            capture_output=True, text=True)
        self.assertEqual(ran.returncode, 0, ran.stderr)

    def test_faults_are_found(self):
        def phases_of(*switches):
            run = Run()
            self.addCleanup(run.close)
            schema3(run)
            rewrite(run, with_phases(switches))
            return host_log.check(host_log.load(run.dir / "host_log.jsonl"))
        self.assertIn("appears twice", " ".join(phases_of((2, 500), (2, 800))))
        self.assertIn("not 2..", " ".join(phases_of((3, 500), (2, 800))))
        self.assertIn("not 2..", " ".join(phases_of((3, 500))))


class PhaseCostModel2Test(unittest.TestCase):
    """Cost model 2 prices each phase over its own window (exact windows,
    schema 3), on an exploratory diagnostic run as D-24 §2 allows."""

    def test_every_phase_is_priced(self):
        run = Run()
        self.addCleanup(run.close)
        run.edit("metadata.env", "prices_sha256=",
                 "diagnostic_run=1\nprices_sha256=")
        (run.root / "EXPLORATORY").write_text("exploratory=1\n")
        base = json.loads((run.dir / "prices.json").read_text())
        cal = {"source": "test", "core_seconds": {
            "job_flush": 1e-3, "job_l0": 2e-3, "job_deep": 3e-3,
            "job_move": 4e-3, "c_w": 1e-9, "c_cr": 0.5e-9, "c_st": 1e-7,
            "c_ib": 0.0, "c_mt": 2e-7, "c_get0": 3e-7, "c_sc0": 4e-7,
            "c_put": 5e-7},
            "kappa": json.loads(json.dumps(stage30.PLACEHOLDER_KAPPA))}
        prices = run.root / "prices.v2.json"
        prices.write_text(json.dumps(stage30.build(base, [cal], False, 100, 10)))
        schema3(run)
        rewrite(run, with_phases(PhaseTest.SWITCHES))
        row = graphs.collect_arm(run.dir, reprice=prices)
        self.assertEqual((row["objective_status"], row["cost_model"]),
                         ("priced", 2))
        for phase in row["_phases"]:
            for cost in ("C_W", "C_R", "C_S", "J_balanced_cs1"):
                self.assertTrue(math.isfinite(phase[cost]), (phase["phase"], cost))
            self.assertGreater(phase["C_W"], 0)


if __name__ == "__main__":
    unittest.main()
