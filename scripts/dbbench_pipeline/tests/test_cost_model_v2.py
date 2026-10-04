"""Cost model 2 (PATHWAYS D §1 as amended 2026-10-03, PREREGISTRATION D-23;
D-24 §2 and §4): cost_model_v2's arithmetic, stage 30's schema-6 writer,
host log schema 3's checks, and 04's pricing with it, on the evaluator
fixture as schema 2 (mean field) and converted to schema 3 (exact windows)."""

import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path

import cost_model_v2 as cm
import host_log
from tests.test_evaluator import Run, graphs

PIPELINE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "provisional_prices_v2", PIPELINE / "30_provisional_prices_v2.py")
stage30 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage30)

FG_NAMES = ["rocksdb.point.sst.probe", "rocksdb.bloom.filter.full.positive",
            "rocksdb.sorted.run.seek", "rocksdb.read.table.reopen",
            "rocksdb.number.db.next.found", "rocksdb.number.iter.skip",
            "rocksdb.number.keys.read", "rocksdb.number.db.seek",
            "rocksdb.number.keys.written", "rocksdb.read.table.reopen.nanos",
            "rocksdb.rl.scan.setup.nanos"]


def record(**changes):
    """A schema-6 record with round prices."""
    r = {"schema": 6, "cost_model": 2, "price_per_core_second": 2.0,
         "c_w": 1.0, "c_cr": 0.5, "c_f": 1.0, "c_blk": 2.0, "c_sk": 3.0,
         "c_open": 4.0, "c_st": 5.0, "c_ib": 0.0, "c_mt": 6.0,
         "c_get0": 7.0, "c_sc0": 8.0, "c_put": 9.0,
         "job_prices": {"flush": 10.0, "l0": 20.0, "deep": 30.0, "move": 40.0},
         "kappa": {"lambda": 0.5,
                   "B": {x: 0.0 for x in cm.STEP_TYPES},
                   "J": {x: {k: 0.0 for k in cm.KINDS} for x in cm.STEP_TYPES}},
         "n_win": 10}
    r.update(changes)
    return r


def counts(**values):
    return {x: float(values.get(x, 0)) for x in cm.STEP_TYPES}


class ArithmeticTest(unittest.TestCase):
    def test_job_prices_by_kind(self):
        p = cm.load_prices(record())
        # 20 + 0.5 (2 + 4) + 1 * 5
        self.assertEqual(28.0, cm.job_price(p, cm.Job("l0", 0, s=2, o=4, x=5)))
        self.assertEqual(40.0, cm.job_price(p, cm.Job("move", 1, s=9, o=9)))
        self.assertEqual(13.0, cm.job_price(p, cm.Job("flush", -1, x=3)))
        self.assertEqual(0.0, cm.job_bytes(p, cm.Job("move", 1, s=9, x=9)))

    def test_charge_has_a_byte_and_a_busy_part(self):
        r = record()
        r["kappa"]["B"]["probe"] = 0.1
        r["kappa"]["J"]["put"]["l0"] = 0.2
        p = cm.load_prices(r)
        rho = {x: 0.0 for x in cm.STEP_TYPES}
        rho.update(probe=1.5, put=2.0)
        job = cm.Job("l0", 0, s=2, o=4, x=5)  # Y = 5 + 0.5 * 6 = 8, t = 28 / 2
        read, write = cm.charge(p, 3.0, job, rho)
        self.assertAlmostEqual(3.0 * 1.5 * 0.1 * 8, read)
        self.assertAlmostEqual(3.0 * 2.0 * 0.2 * 14, write)

    def test_windows(self):
        own = cm.Job("deep", 1, n_begin=100, n_end=120,
                     fg_begin=counts(probe=10), fg_end=counts(probe=30))
        snaps = [(90, counts(probe=5)), (96, counts(probe=8))]
        floor = (50, counts())
        c, n = cm.window(own, snaps, floor, 10)
        self.assertEqual((20.0, 20), (c["probe"], n))
        # Shorter than n_win: from the last snap at or before 105 - 10.
        short = cm.Job("deep", 1, n_begin=100, n_end=105,
                       fg_begin=counts(probe=10), fg_end=counts(probe=12))
        c, n = cm.window(short, snaps, floor, 10)
        self.assertEqual((7.0, 15), (c["probe"], n))
        # Before every snap: the phase start.
        early = cm.Job("deep", 1, n_end=55, fg_end=counts(probe=3))
        c, n = cm.window(early, snaps, floor, 10)
        self.assertEqual((3.0, 5), (c["probe"], n))

    def test_mean_field_totals(self):
        r = record()
        r["kappa"]["B"]["block"] = 0.01
        p = cm.load_prices(r)
        jobs = [cm.Job("l0", 0, s=2, o=4, x=5, n_begin=0, n_end=50, span_s=2.0),
                cm.Job("flush", -1, x=3, n_end=60, span_s=1.0),
                cm.Job("move", 1, n_begin=60, n_end=60, span_s=0.5)]
        phase = counts(probe=100, block=40, seek=10, reopen=5, step=50,
                       memtable=30, get0=20, scan0=10, put=70)
        out = cm.evaluate(p, rate=4.0, c_s=0.25, jobs=jobs, phase_counts=phase,
                          operations=100, held_byte_ops=8.0, exact=False)
        tau = 28 + 13 + 40
        rho_block = 2.0 * 40 / 100
        intf = 4.0 * rho_block * 0.01 * (8 + 3 + 0)
        self.assertAlmostEqual(tau, out["job_part"])
        self.assertAlmostEqual(intf, out["interference_read"])
        self.assertAlmostEqual(0.0, out["interference_write"])
        self.assertAlmostEqual(tau + 9 * 70, out["C_W"])
        read = 100 + 2 * 40 + 3 * 10 + 4 * 5 + 5 * 50 + 6 * 30 + 7 * 20 + 8 * 10
        self.assertAlmostEqual(read + intf, out["C_R"])
        self.assertAlmostEqual(0.25 / 4.0 * 8.0, out["C_S"])
        self.assertEqual((1, 1, 0, 1), tuple(out[f"jobs_{k}"] for k in
                                             ("flush", "l0", "deep", "move")))
        self.assertEqual(6.0, out["compaction_bytes_read"])
        # OBJ-7: measured over priced seconds (priced = tau / 2).
        self.assertAlmostEqual(2.0 / 14, out["job_time_ratio_l0"])
        self.assertTrue(math.isnan(out["job_time_ratio_deep"]))
        # OBJ-6 needs a job's own operations: the flush has none here.
        self.assertEqual(1, out["physical_jobs_excluded"])

    def test_malformed_prices_are_refused(self):
        for broken in (record(schema=5), record(cost_model=1),
                       record(c_st=0.0), record(n_win=0),
                       record(job_prices={"flush": 1.0}),
                       record(kappa={"lambda": 0, "B": {}, "J": {}})):
            with self.assertRaises(ValueError):
                cm.load_prices(broken)


class Stage30Test(unittest.TestCase):
    def base(self):
        return json.loads((PIPELINE / "tests" / "fixtures" / "evaluator" / "1M" /
                           "T2" / "native" / "prices.json").read_text())

    def test_calibrations_and_placeholders(self):
        base = self.base()
        a6 = {"source": "A6", "c_cr_combined": True,
              "core_seconds": {"job_flush": 2.5e-3, "job_l0": 1.5e-3,
                               "job_deep": 3.1e-3, "job_move": 2.9e-3,
                               "c_w": 1.6e-9, "c_cr": 0.0}}
        out = stage30.build(base, [a6], True, 1000, 100)
        core = base["price_per_core_second"]
        self.assertEqual(6, out["schema"])
        self.assertEqual("provisional", out["kind"])
        self.assertAlmostEqual(2.5e-3 * core, out["job_prices"]["flush"])
        self.assertAlmostEqual(1.6e-9 * core, out["c_w"])
        self.assertTrue(out["c_cr_combined"])
        self.assertEqual("A6", out["sources"]["job_l0"])
        self.assertEqual(sorted(stage30.PLACEHOLDERS) + ["kappa"],
                         sorted(out["placeholders"]))
        self.assertTrue(out["sources"]["c_st"].startswith("PLACEHOLDER"))
        self.assertEqual(base["c_f"], out["c_f"])  # stage 18's read prices
        self.assertEqual((1000, 100), (out["n_win"], out["n_str"]))
        cm.load_prices(out)
        # A later calibration overrides a placeholder key by key.
        later = {"source": "A5", "core_seconds": {"c_st": 1e-7}}
        out = stage30.build(base, [a6, later], True, 1000, 100)
        self.assertAlmostEqual(1e-7 * core, out["c_st"])
        self.assertNotIn("c_st", out["placeholders"])

    def test_missing_values_are_refused_without_placeholders(self):
        with self.assertRaises(ValueError):
            stage30.build(self.base(), [], False, 1000, 100)
        with self.assertRaises(ValueError):
            stage30.build({**self.base(), "schema": 6}, [], True, 1000, 100)


# --- the evaluator fixture, with the step tickers and as schema 3 ---------

PHASE_START = {"rocksdb.number.keys.written": 290, "rocksdb.number.keys.read": 0,
               "rocksdb.number.db.seek": 0, "rocksdb.number.db.next.found": 0,
               "rocksdb.number.iter.skip": 0, "rocksdb.rl.scan.setup.nanos": 0}
PHASE_END = {"rocksdb.number.keys.written": 400, "rocksdb.number.keys.read": 560,
             "rocksdb.number.db.seek": 40, "rocksdb.number.db.next.found": 600,
             "rocksdb.number.iter.skip": 60, "rocksdb.rl.scan.setup.nanos": 4000}


def rewrite(run, convert):
    path = run.dir / "host_log.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n"
                            for r in convert(records)))


def with_step_tickers(records):
    for r in records:
        if r["type"] == "stamp":
            r["tickers"].update(PHASE_START if r["op"] <= 290 else PHASE_END)
    return records


def to_schema3(sizes):
    """The fixture's log as the interim binary writes it: fg on job records,
    flush_begin and flush_end around each flush, a snap, hidden steps."""
    def convert(records):
        records = with_step_tickers(records)
        stamps = {r["name"]: r for r in records if r["type"] == "stamp"}
        first, last = stamps["measure_start"], stamps["drain_end"]

        def fg(op):
            share = min(max((op - 290) / 710, 0.0), 1.0)
            return [int(first["tickers"].get(n, 0) + share *
                        (last["tickers"].get(n, 0) - first["tickers"].get(n, 0)))
                    for n in FG_NAMES]
        out = []
        for r in records:
            if r["type"] == "header":
                r.update(schema=3, fg=FG_NAMES, stride=100)
            elif r["type"] == "stamp":
                r["levels"] = [row + [0] for row in r["levels"]]
            elif r["type"] in ("job_begin", "job_end"):
                r["fg"] = fg(r["op"])
            elif r["type"] == "h" and r["cause"] == "flush":
                begin_op = max(r["op"] - 50, out[-1].get("op", 0))
                out.append({"type": "flush_begin", "job": r["job"],
                            "t_us": r["t_us"] - 1000, "op": begin_op,
                            "fg": fg(begin_op)})
                out.append({"type": "flush_end", "job": r["job"],
                            "t_us": r["t_us"], "op": r["op"],
                            "x": int(sizes.get(r["job"], 0)), "files": 1,
                            "fg": fg(r["op"])})
            if r["type"] == "h" and r["cause"] == "compaction" and r["op"] == 500:
                out.append(r)
                out.append({"type": "snap", "t_us": r["t_us"] + 1, "op": 600,
                            "fg": fg(600)})
                continue
            out.append(r)
        return out
    return convert


class EvaluatorV2Test(unittest.TestCase):
    def setUp(self):
        self.run = Run()
        self.addCleanup(self.run.close)
        self.run.edit("metadata.env", "prices_sha256=",
                      "diagnostic_run=1\nprices_sha256=")
        (self.run.root / "EXPLORATORY").write_text("exploratory=1\n")
        base = json.loads((self.run.dir / "prices.json").read_text())
        cal = {"source": "test", "core_seconds": {
            "job_flush": 1e-3, "job_l0": 2e-3, "job_deep": 3e-3,
            "job_move": 4e-3, "c_w": 1e-9, "c_cr": 0.5e-9, "c_st": 1e-7,
            "c_ib": 0.0, "c_mt": 2e-7, "c_get0": 3e-7, "c_sc0": 4e-7,
            "c_put": 5e-7},
            "kappa": json.loads(json.dumps(stage30.PLACEHOLDER_KAPPA))}
        self.v2 = stage30.build(base, [cal], False, 100, 10)
        self.prices = self.run.root / "prices.v2.json"
        self.prices.write_text(json.dumps(self.v2))

    def row(self):
        return graphs.collect_arm(self.run.dir, reprice=self.prices)

    def check_parts(self, row):
        self.assertEqual("priced", row["objective_status"])
        self.assertEqual(2, row["cost_model"])
        self.assertAlmostEqual(row["C_W"], row["job_part"] + row["put_part"] +
                               row["interference_write"])
        self.assertAlmostEqual(row["C_R"], row["read_base_part"] +
                               row["scan_step_part"] + row["memtable_part"] +
                               row["fixed_read_part"] +
                               row["iterator_block_part"] +
                               row["interference_read"])
        self.assertGreater(row["interference_read"], 0)
        self.assertGreater(row["interference_write"], 0)
        # Measured phase: flushes 10, 12, 13; L0 merges 11 and 14.
        self.assertEqual((3, 2, 0, 0), tuple(row[f"jobs_{k}"] for k in
                                             ("flush", "l0", "deep", "move")))
        self.assertEqual(2000 + 1500 + 4000 + 1000, row["compaction_bytes_read"])
        self.assertEqual((110, 560, 40), (row["puts"], row["gets"], row["scans"]))
        self.assertEqual(self.run.root / "prices.v2.json", self.prices)
        self.assertTrue(row["reprice_sha256"])
        self.assertFalse(math.isnan(row["J_balanced_cs1"]))

    def test_schema2_prices_by_the_mean_field(self):
        rewrite(self.run, with_step_tickers)
        row = self.row()
        self.check_parts(row)
        self.assertEqual("mean field", row["interference_estimator"])
        self.assertIn("mean field (D-24 §4)", row["cost_terms"])
        self.assertIn("per-level scan attribution", row["cost_terms"])
        p = cm.load_prices(self.v2)
        job_part = (3 * p.job["flush"] + 2 * p.job["l0"] +
                    p.c_cr * 8500 + p.c_w * (3000 + 1500) +
                    p.c_w * sum(graphs.flush_jobs_from_events(
                        graphs.read_events(self.run.dir / "rocksdb_LOG.txt"))[0]
                        [job] for job in (10, 12, 13)))
        self.assertAlmostEqual(job_part, row["job_part"])

    def test_schema3_prices_by_exact_windows(self):
        sizes, _ = graphs.flush_jobs_from_events(
            graphs.read_events(self.run.dir / "rocksdb_LOG.txt"))
        rewrite(self.run, to_schema3(sizes))
        records = host_log.load(self.run.dir / "host_log.jsonl")
        self.assertEqual([], host_log.check(records))
        row = self.row()
        self.check_parts(row)
        self.assertEqual("exact", row["interference_estimator"])
        self.assertEqual(0.0, row["hidden_steps_level_share"])
        self.assertEqual(0, row["physical_jobs_excluded"])

    def test_reprice_refuses_a_run_that_is_not_exploratory(self):
        rewrite(self.run, with_step_tickers)
        (self.run.root / "EXPLORATORY").unlink()
        with self.assertRaises(graphs.InvalidArm):
            self.row()

    def test_cost_model_1_is_unchanged_without_a_schema_6_file(self):
        row = graphs.collect_arm(self.run.dir)
        self.assertEqual(1, row["cost_model"])
        self.assertTrue(math.isnan(row["job_part"]))


class HostLogSchema3Test(unittest.TestCase):
    def setUp(self):
        self.run = Run()
        self.addCleanup(self.run.close)
        sizes, _ = graphs.flush_jobs_from_events(
            graphs.read_events(self.run.dir / "rocksdb_LOG.txt"))
        rewrite(self.run, to_schema3(sizes))
        self.records = host_log.load(self.run.dir / "host_log.jsonl")

    def test_a_consistent_log_passes(self):
        self.assertEqual([], host_log.check(self.records))
        self.assertEqual(FG_NAMES, host_log.fg_names(self.records))

    def test_faults_are_found(self):
        def problems(edit):
            records = json.loads(json.dumps(self.records))
            edit(records)
            return " | ".join(host_log.check(records))
        snap = next(i for i, r in enumerate(self.records) if r["type"] == "snap")

        def decrease(records):
            records[snap]["fg"][0] = 0
        self.assertIn("fg counters decreased", problems(decrease))

        def drop_begin(records):
            records[:] = [r for r in records if not (
                r["type"] == "flush_begin" and r["job"] == 12)]
        self.assertIn("flush 12 ended without a flush_begin",
                      problems(drop_begin))

        def drop_end(records):
            records[:] = [r for r in records if not (
                r["type"] == "flush_end" and r["job"] == 12)]
        self.assertIn("flush 12's H sample does not follow", problems(drop_end))

        def short_fg(records):
            records[snap]["fg"] = records[snap]["fg"][:5]
        self.assertIn("without 11 fg counters", problems(short_fg))

        def hidden(records):
            end = next(r for r in records if r.get("name") == "drain_end")
            end["levels"][0][host_log.HIDDEN] = 61
        self.assertIn("hidden steps 61 >", problems(hidden))


if __name__ == "__main__":
    unittest.main()
