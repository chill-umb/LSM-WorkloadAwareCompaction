"""calibration_v2.py: the one-session calibrations of cost model 2
(PREREGISTRATION D-24 §2; D-23 §4), on synthetic runs whose prices are
known: the job fit keeps c_cr only when a garbage-heavy neighbour
identifies it (D-24 item 11), the foreground fit recovers c_put, c_mt,
c0_get, c_st, c_ib and c0_sc, and the kappa fit recovers kappa^B, kappa^J
and lambda."""

import json
import math
import random
import tempfile
import unittest
from pathlib import Path

import calibration_v2 as cal
import cost_model_v2 as cm

C_F, C_BLK, C_SK = 0.2e-6, 2.4e-6, 1.8e-6


def job(kind, s=0.0, o=0.0, x=0.0, noise=0.0, flush_rate=1.2e-9):
    price = {"flush": 2.5e-3, "l0": 1.5e-3, "deep": 3.1e-3, "move": 2.9e-3}[kind]
    rate = flush_rate if kind == "flush" else 1.2e-9
    span = price + (0 if kind == "move" else 0.4e-9 * (s + o) + rate * x)
    return cm.Job(kind, 0, s=s, o=o, x=x, span_s=span + noise)


class JobFitTest(unittest.TestCase):
    def jobs(self, garbage, flushes=True, flush_rate=1.2e-9):
        rng = random.Random(7)
        out = []
        for _ in range(200):
            x = rng.uniform(1e6, 3e7)
            out.append(job("deep", s=x * 1.127 * 0.6, o=x * 1.127 * 0.4, x=x,
                           noise=rng.gauss(0, 1e-5)))
            out.append(job("l0", s=x, o=x * 0.127, x=x, noise=rng.gauss(0, 1e-5)))
        for _ in range(50):
            if flushes:
                out.append(job("flush", x=rng.uniform(1e6, 3e6),
                               noise=rng.gauss(0, 1e-5), flush_rate=flush_rate))
            out.append(job("move", noise=rng.gauss(0, 1e-5)))
        if garbage:  # merges that read far more than they write
            for _ in range(100):
                x = rng.uniform(1e5, 1e6)
                out.append(job("deep", s=x * 10, o=x * 5, x=x,
                               noise=rng.gauss(0, 1e-5)))
        return out

    def test_a_garbage_heavy_neighbour_identifies_c_cr(self):
        fit = cal.fit_jobs(self.jobs(garbage=True))
        self.assertFalse(fit["c_cr_combined"])
        core = fit["core_seconds"]
        self.assertAlmostEqual(0.4e-9, core["c_cr"], delta=0.02e-9)
        self.assertAlmostEqual(1.2e-9, core["c_w"], delta=0.05e-9)
        self.assertAlmostEqual(3.1e-3, core["job_deep"], delta=0.05e-3)
        self.assertAlmostEqual(2.9e-3, core["job_move"], delta=0.05e-3)

    def test_without_it_the_fit_is_combined(self):
        # Merges only, reading 1.127 times what they write: rank-deficient.
        fit = cal.fit_jobs(self.jobs(garbage=False, flushes=False))
        self.assertTrue(fit["c_cr_combined"])
        self.assertEqual(0.0, fit["core_seconds"]["c_cr"])
        # c_w then prices a written byte with the 1.127 read for it.
        self.assertAlmostEqual(1.2e-9 + 0.4e-9 * 1.127,
                               fit["core_seconds"]["c_w"], delta=0.03e-9)


class JobFitNegativeTest(JobFitTest.__bases__[0]):
    def test_a_negative_c_cr_is_not_kept(self):
        # Flushes costlier per byte than merges, as on the d21id arms (A6):
        # the separate fit's c_cr goes negative, and the combined one is used.
        jobs = JobFitTest.jobs(JobFitTest(), garbage=False, flush_rate=3e-9)
        fit = cal.fit_jobs(jobs)
        self.assertLess(fit["separate"]["c_cr"], 0)
        self.assertTrue(fit["c_cr_combined"])


STDOUT = """Initializing RocksDB Options from the specified file
seekrandom   :       5.000 micros/op 200000 ops/sec 2.000 seconds 400000 operations; (found)
rocksdb.point.sst.probe COUNT : 10
rocksdb.number.db.seek COUNT : 400000
rocksdb.db.seek.micros P50 : 2.0 P95 : 3.0 P99 : 4.0 P100 : 9.0 COUNT : 400000 SUM : 800000
PERF_CONTEXT:
user_key_comparison_count = 5, get_from_memtable_time = 70, get_from_memtable_count = 7
"""


class ParseTest(unittest.TestCase):
    def test_stdout(self):
        row = cal.parse_stdout(STDOUT)
        self.assertEqual(("seekrandom", 400000, 2.0), (row["bench"], row["ops"],
                                                       row["seconds"]))
        self.assertEqual(5.0, row["micros_per_op"])
        self.assertEqual(10, row["tickers"]["rocksdb.point.sst.probe"])
        self.assertEqual((400000, 800000), row["hist"]["rocksdb.db.seek.micros"])
        self.assertEqual(70, row["perf"]["get_from_memtable_time"])
        with self.assertRaises(ValueError):
            cal.parse_stdout("nothing here")

    def test_neighbour_load(self):
        # Steady clock 1,000 = wall 1,000,000. A merge from steady 1,000 to
        # 3,000 us writes 2,000 bytes and reads 4,000; a flush from 2,000 to
        # 2,500 writes 500. Window: wall 1,001,000 to 1,003,000 (2 ms).
        records = [
            {"type": "header", "t_us": 1000, "wall_us": 1_000_000},
            {"type": "job_begin", "job": 1, "t_us": 1000},
            {"type": "flush_begin", "job": 2, "t_us": 2000},
            {"type": "flush_end", "job": 2, "t_us": 2500, "x": 500},
            {"type": "job_end", "job": 1, "t_us": 3000, "ok": 1, "trivial": 0,
             "start_level": 1, "s": 3000, "o": 1000, "x": 2000},
        ]
        # The merge (wall 1,000,000 to 1,002,000) overlaps half the window,
        # so half its bytes count; the flush lies inside it.
        load = cal.neighbour_load(records, 1_001_000, 1_003_000)
        self.assertAlmostEqual(0.5, load["busy_deep"])
        self.assertAlmostEqual(0.25, load["busy_flush"])
        self.assertAlmostEqual((1000 + 500) / 0.002, load["x_rate"])
        self.assertAlmostEqual(2000 / 0.002, load["read_rate"])


def row(kind, arm="quiet", tree="b4k", nexts=0, per=1e-6, ops=100000, **extra):
    """A synthetic run whose per-operation times follow known prices."""
    t = {"rocksdb.number.db.seek": 0, "rocksdb.number.keys.read": 0}
    hist, perf = {}, {}
    micros = per * 1e6
    if kind in ("hit", "miss"):
        probes, blocks = (3.0, 1.0) if kind == "hit" else (3.0, 0.02)
        t.update({"rocksdb.number.keys.read": ops,
                  "rocksdb.point.sst.probe": int(probes * ops),
                  "rocksdb.bloom.filter.full.positive": int(blocks * ops)})
        get = 0.4e-6 + 0.05e-6 + C_F * probes + C_BLK * blocks
        hist["rocksdb.db.get.micros"] = (ops, int(get * per / 1e-6 * ops * 1e6))
        perf = {"get_from_memtable_time": int(0.05e-6 * 1e9 * ops),
                "get_from_memtable_count": ops}
    elif kind == "memtable":
        perf = {"get_from_memtable_time": int(0.22e-6 * 1e9 * ops),
                "get_from_memtable_count": ops}
    elif kind == "put":
        hist["rocksdb.db.write.micros"] = (ops, int(0.5e-6 * per / 1e-6 * ops * 1e6))
    elif kind == "seek":
        block_entries = 32 if tree == "b4k" else 128
        steps = nexts * 1.2
        seek_blocks, iblocks = 3.0, steps / block_entries
        t.update({"rocksdb.number.db.seek": ops,
                  "rocksdb.sorted.run.seek": 3 * ops,
                  "rocksdb.number.db.next.found": int(nexts * ops),
                  "rocksdb.number.iter.skip": int(nexts * 0.2 * ops),
                  "rocksdb.block.cache.data.hit": int((seek_blocks + iblocks) * ops),
                  "rocksdb.rl.scan.setup.count": ops,
                  "rocksdb.rl.scan.setup.nanos": int(0.8e-6 * 1e9 * ops),
                  "rocksdb.rl.scan.teardown.nanos": int(0.2e-6 * 1e9 * ops)})
        wall = (2e-6 + 0.3e-6 * steps + 1.5e-6 * iblocks) * per / 1e-6
        micros = wall * 1e6
        hist["rocksdb.db.seek.micros"] = (ops, int((0.7e-6 + C_SK * 3) * ops * 1e6))
    out = {"bench": kind, "micros_per_op": micros, "seconds": 1.0, "ops": ops,
           "tickers": t, "hist": hist, "perf": perf, "kind": kind, "arm": arm,
           "tree": tree, "nexts": str(nexts)}
    out.update(extra)
    return out


class ForegroundTest(unittest.TestCase):
    BASE = {"c_f": C_F, "c_blk": C_BLK, "c_sk": C_SK}

    def quiet_rows(self):
        rows = [row("hit"), row("miss"), row("memtable"), row("put")]
        for tree in ("b4k", "b16k"):
            for nexts in (0, 16, 64):
                rows.append(row("seek", tree=tree, nexts=nexts))
        return rows

    def test_quiet_prices(self):
        fg = cal.fit_foreground(self.quiet_rows(), self.BASE)["core_seconds"]
        self.assertAlmostEqual(0.5e-6, fg["c_put"], delta=1e-9)
        self.assertAlmostEqual(0.22e-6, fg["c_mt"], delta=1e-9)
        self.assertAlmostEqual(0.4e-6, fg["c_get0"], delta=0.01e-6)
        self.assertAlmostEqual(0.3e-6, fg["c_st"], delta=0.01e-6)
        self.assertAlmostEqual(1.5e-6, fg["c_ib"], delta=0.05e-6)
        self.assertAlmostEqual(0.7e-6 + 1.0e-6, fg["c_sc0"], delta=0.01e-6)

    def test_kappa_and_lambda(self):
        rows = self.quiet_rows()
        rng = random.Random(3)
        truth = {"probe": (1.2e-9, 0.0), "block": (0.9e-9, 0.02),
                 "seek": (0.8e-9, 0.0), "step": (0.5e-9, 0.01),
                 "put": (0.3e-9, 0.0)}
        lam = 0.5
        specs = [("miss", 0, "probe"), ("hit", 0, "block"),
                 ("seek", 0, "seek"), ("seek", 64, "step"), ("put", 0, "put")]
        for kind, nexts, x_type in specs:
            for x_rate in (5e6, 20e6, 40e6, 80e6):
                for read_rate in (1.1 * x_rate, 4 * x_rate):
                    busy = rng.uniform(0.3, 1.8)
                    kb, kj = truth[x_type]
                    s = kb * (x_rate + lam * read_rate) + kj * busy
                    rows.append(row(kind, arm="neighbour", nexts=nexts,
                                    per=1e-6 * (1 + s),
                                    neighbour={"x_rate": x_rate,
                                               "read_rate": read_rate,
                                               "busy_flush": 0.0,
                                               "busy_l0": busy / 2,
                                               "busy_deep": busy / 2,
                                               "busy_move": 0.0}))
        kappa = cal.fit_kappa(rows)
        self.assertEqual(lam, kappa["lambda"])
        for x_type, (kb, kj) in truth.items():
            self.assertAlmostEqual(kb, kappa["B"][x_type], delta=0.05 * kb)
            self.assertAlmostEqual(kj, kappa["J"][x_type]["l0"],
                                   delta=0.002 + 0.05 * kj)
        self.assertEqual(kappa["B"]["block"], kappa["B"]["reopen"])
        self.assertIn("stand-in: block", kappa["basis"])
        prices = {"schema": 6, "cost_model": 2}
        self.assertTrue(set(cm.STEP_TYPES) <= set(kappa["B"]))
        del prices


class CommandsTest(unittest.TestCase):
    def test_rows_reads_every_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "nb.jsonl"
            log.write_text(json.dumps({"type": "header", "t_us": 0,
                                       "wall_us": 0}) + "\n")
            for i, arm in enumerate(("quiet", "nb16")):
                run = root / f"r{i}"
                run.mkdir()
                (run / "stdout.txt").write_text(STDOUT)
                env = f"kind=seek\narm={arm}\ntree=b4k\nnexts=0\n" \
                      "start_wall_us=0\nend_wall_us=1000\n"
                if arm != "quiet":
                    env += f"neighbour_host_log={log}\n"
                (run / "run.env").write_text(env)
            args = type("A", (), {"runs": root})
            self.assertEqual(0, cal.rows_command(args))
            rows = [json.loads(line) for line in
                    (root / "rows.jsonl").read_text().splitlines()]
            self.assertEqual(2, len(rows))
            self.assertIn("neighbour", rows[1])
            self.assertNotIn("neighbour", rows[0])


if __name__ == "__main__":
    unittest.main()
