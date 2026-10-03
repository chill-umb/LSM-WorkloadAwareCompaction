"""PREREGISTRATION D-22: the archived price trees (price_trees.py, 29), one
measurement session of 18 on them, and 18's compare of two sessions.

The synthetic trees obey D-15's model exactly, as in test_prices.py, at the
six ratios: every Get pays 2 us of overhead, t_f per filter probe and 1 us
per block read; every seek 3 us plus t_sk per run; in the capped arm, 10 us
per table reopen on half the probes (the seek_reopen time on half the run
seeks). Each (set, round) can carry its own t_f, so the session's price is
the median of nine known values.
"""
import copy
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import plugin_config
import preflight_marker
import price_trees
import research_objective
from tests.test_prices import (CONTRACT, CORE, DB_OPEN, FAMILIES, OPS, REOPEN,
                               SHA, TIMER, calibrate, stdout, write_row)

PIPELINE = Path(__file__).resolve().parents[1]
REPO = PIPELINE.parents[1]
FAKE = PIPELINE / "tests" / "fixtures" / "chain" / "fake_db_bench.py"
_, CONTRACT_SHA = research_objective.load_contract()
TREE_SET = "c" * 64
TOLERANCE = CONTRACT["prices"]["reproducibility_tolerance"]
# (filter probes per missing Get, per found Get, run seeks per seek) by T.
DEPTHS = {2: (9, 8, 10), 3: (7.5, 6.8, 8.5), 4: (6.5, 6, 7.5),
          6: (5, 4.5, 6), 8: (4.5, 4, 5.5), 10: (4, 3.5, 5)}
# Nine (set, round) values of t_f: median 0.5 us; sets' medians 0.45, 0.55
# and 0.52 us.
T_F = [0.40e-6, 0.45e-6, 0.50e-6, 0.55e-6, 0.60e-6, 0.48e-6,
       0.52e-6, 0.47e-6, 0.53e-6]
AGE = " ".join(price_trees.AGE_FLAGS)


def tree(ratio, t_f=0.5e-6, t_sk=2e-6, seek_reopen=REOPEN):
    """One tree's three benchmarks in both arms (test_prices.tree with its
    costs as arguments)."""
    miss, found, runs = DEPTHS[ratio]
    miss_blocks, found_blocks = 0.01 * miss, 1 + 0.01 * (found - 1)

    def timed(n):
        return {"read_table_reopen": n, "read_table_reopen_nanos": TIMER * 1e9 * n}

    arms = {}
    for arm in calibrate.ARMS:
        share = 0.0 if arm == "all_open" else 0.5
        arms[arm] = {
            "readmissing": stdout(
                "readmissing", 2e-6 + t_f * miss + 1e-6 * miss_blocks
                + REOPEN * share * miss,
                point_sst_probe=miss, bloom_filter_full_positive=miss_blocks,
                no_file_opens=DB_OPEN + share * miss, **timed(share * miss)),
            "readrandom": stdout(
                "readrandom", 2e-6 + t_f * found + 1e-6 * found_blocks
                + REOPEN * share * found,
                point_sst_probe=found, bloom_filter_full_positive=found_blocks,
                no_file_opens=DB_OPEN + share * found, **timed(share * found)),
            "seekrandom": stdout(
                "seekrandom", 3e-6 + t_sk * runs + seek_reopen * share * runs,
                sorted_run_seek=runs, no_file_opens=DB_OPEN + share * runs,
                **timed(share * runs)),
        }
    return arms


def put(work: Path, key: str, text: str, open_files: int, age: str = AGE) -> None:
    out = work / key
    out.mkdir(parents=True, exist_ok=True)
    (out / "stdout.txt").write_text(text)
    (out / "command.txt").write_text(
        f"db_bench --open_files={open_files} {age} --benchmarks={key.rsplit('/', 1)[1]}")


def session_dir(work: Path, t_f=T_F, seek_reopen=REOPEN, boot="boot-a") -> Path:
    """A session's folder as 18_calibrate_prices.sh writes it."""
    (work).mkdir(parents=True)
    (work / "archive.json").write_text(json.dumps(
        {"archive": "/archive", "tree_set_sha256": TREE_SET}))
    (work / "machine.json").write_text(json.dumps({"boot_id": boot, "kernel": "k"}))
    for b, t in price_trees.all_trees():
        for phase in ("unpacked", "after"):
            path = work / price_trees.tree(b, t) / f"{phase}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"manifest_sha256": TREE_SET, "ok": True,
                                        "problems": []}))
    lines = []
    for i, (b, k) in enumerate((b, k) for b in price_trees.SETS
                               for k in range(1, 4)):
        if k == 1:
            lines += [f"s{b}/T{t}/warmup" for t in price_trees.SIZE_RATIOS]
        for t in price_trees.ROUNDS[k - 1]:
            for bench, text in tree(t, t_f=t_f[i])["all_open"].items():
                key = calibrate.round_key(b, k, t, bench)
                put(work, key, text, -1)
                lines.append(key)
        if b == price_trees.COPEN_SET and k == 3:
            for t in price_trees.COPEN_RATIOS:
                arms = tree(t, seek_reopen=seek_reopen)
                for r in range(1, calibrate.REPEATS + 1):
                    for bench in calibrate.READS:
                        for arm in (calibrate.ARMS if r % 2 else calibrate.ARMS[::-1]):
                            key = calibrate.copen_key(t, r, arm, bench)
                            put(work, key, arms[arm][bench],
                                1000 if arm == "capped" else -1)
                            lines.append(key)
    (work / "runs.log").write_text("\n".join(lines) + "\n")
    return work


def summary(path: Path, sha: str = SHA) -> Path:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(write_row()))
        writer.writeheader()
        writer.writerows(write_row(i, family, dbbench_sha256=sha)
                         for family in FAMILIES for i in range(5))
    return path


class Args:
    def __init__(self, session="A", summaries=()):
        self.tree_set_sha256 = TREE_SET
        self.db_bench_sha256 = SHA
        self.session = session
        self.write_summary = list(summaries)


def make_session(root: Path, name="A", **kwargs) -> dict:
    rows = [write_row(i, f) for f in FAMILIES for i in range(5)]
    t_w = calibrate.write_runs(rows, SHA, FAMILIES)
    return calibrate.session(session_dir(root / name, **kwargs), rows, t_w,
                             CONTRACT, CONTRACT_SHA, Args(name))


def scaled(record: dict, name: str, factor: float) -> dict:
    """record with one tested price, and every value behind it, scaled."""
    out = copy.deepcopy(record)
    if name == "reopen_timer":
        timer = out["reopen_timer"]
        timer["values"] = [v * factor for v in timer["values"]]
        timer["seconds_per_reopen"] *= factor
    else:
        out["core_seconds_per_unit"][name] *= factor
        spread = out["core_seconds_spread"][name]
        spread["values"] = [v * factor for v in spread["values"]]
    return out


def other_session(record: dict, name="B", boot="boot-b") -> dict:
    out = copy.deepcopy(record)
    out["session"] = name
    out["machine"]["boot_id"] = boot
    return out


class Ratio(unittest.TestCase):
    def assertRatio(self, value, expected, msg=None):
        self.assertAlmostEqual(value / expected, 1.0, places=6, msg=msg)


class TreesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.build = self.root / "build"
        self.archive = self.root / "archive"
        self.archive.mkdir()
        for b, t in price_trees.all_trees():
            folder = self.build / price_trees.tree(b, t)
            folder.mkdir(parents=True)
            (folder / "000010.sst").write_text(f"sst {b} {t}")
            (folder / "MANIFEST-000005").write_text("m")
            (folder / "LOG").write_text("log")
            (self.archive / price_trees.archive(b, t)).write_bytes(b"tgz %d %d" % (b, t))
        self.identity = price_trees.write_manifest(self.build, self.archive)
        self.manifest = price_trees.read_manifest(self.archive / price_trees.MANIFEST)

    def test_the_rounds_rotate_as_d22_says(self):
        self.assertEqual(price_trees.ROUNDS, ((2, 3, 4, 6, 8, 10),
                                              (4, 6, 8, 10, 2, 3),
                                              (8, 10, 2, 3, 4, 6)))
        for order in price_trees.ROUNDS:
            self.assertEqual(sorted(order), list(price_trees.SIZE_RATIOS))
        done = subprocess.run([sys.executable, str(PIPELINE / "price_trees.py"),
                               "rounds"], capture_output=True, text=True)
        self.assertEqual(done.stdout.splitlines(),
                         ["2 3 4 6 8 10", "4 6 8 10 2 3", "8 10 2 3 4 6"])

    def test_the_manifest_lists_every_file_and_archive(self):
        self.assertEqual(len(self.manifest), 18 * 3 + 18)
        self.assertEqual(self.identity, hashlib.sha256(
            (self.archive / price_trees.MANIFEST).read_bytes()).hexdigest())
        archives = price_trees.read_manifest(self.archive / price_trees.ARCHIVES)
        self.assertEqual(sorted(archives), sorted(
            price_trees.archive(b, t) for b, t in price_trees.all_trees()))
        self.assertEqual(self.manifest["s2/T6/000010.sst"],
                         hashlib.sha256(b"sst 2 6").hexdigest())

    def test_an_unpacked_tree_must_match_file_for_file(self):
        self.assertEqual(price_trees.check_tree(self.manifest, self.build, "s1/T2"), [])
        (self.build / "s1/T2/000010.sst").write_text("changed")
        (self.build / "s1/T2/extra").write_text("x")
        (self.build / "s1/T2/LOG").unlink()
        self.assertEqual(sorted(price_trees.check_tree(self.manifest, self.build, "s1/T2")),
                         ["s1/T2/000010.sst: changed", "s1/T2/LOG: missing",
                          "s1/T2/extra: not in the manifest"])

    def test_after_the_reads_only_the_ssts_must_be_unchanged(self):
        # Opening a database writes a new MANIFEST, OPTIONS and LOG.
        (self.build / "s3/T10/LOG").write_text("reopened")
        (self.build / "s3/T10/OPTIONS-000099").write_text("o")
        (self.build / "s3/T10/MANIFEST-000005").unlink()
        self.assertEqual(price_trees.check_tree(self.manifest, self.build, "s3/T10",
                                                after=True), [])
        self.assertTrue(price_trees.check_tree(self.manifest, self.build, "s3/T10"))
        # A compaction during the reads writes a new SST.
        (self.build / "s3/T10/000099.sst").write_text("new")
        self.assertEqual(price_trees.check_tree(self.manifest, self.build, "s3/T10",
                                                after=True),
                         ["s3/T10/000099.sst: not in the manifest"])
        (self.build / "s3/T10/000099.sst").unlink()
        (self.build / "s3/T10/000010.sst").write_text("rewritten")
        self.assertEqual(price_trees.check_tree(self.manifest, self.build, "s3/T10",
                                                after=True),
                         ["s3/T10/000010.sst: changed"])
        (self.build / "s3/T10/000010.sst").unlink()
        self.assertEqual(price_trees.check_tree(self.manifest, self.build, "s3/T10",
                                                after=True),
                         ["s3/T10/000010.sst: missing"])

    def test_a_trivial_move_during_the_reads_is_refused(self):
        # A trivial move changes no SST, only the MANIFEST; the reads' LOG
        # shows it. The archived LOG, renamed LOG.old.* on open, is not read.
        tree = self.build / "s1/T6"
        (tree / "LOG").rename(tree / "LOG.old.1700000000")
        (tree / "LOG").write_text('EVENT_LOG_v1 {"job": 3, "event": "trivial_move"}\n')
        self.assertEqual(price_trees.check_tree(self.manifest, self.build, "s1/T6",
                                                after=True),
                         ["s1/T6/LOG: trivial_move during the reads"])
        (tree / "LOG").write_text("stats dump only\n")
        (tree / "LOG.old.1700000001").write_text(
            '{"event": "flush_started"} {"event": "compaction_started"}\n')
        self.assertEqual(price_trees.check_tree(self.manifest, self.build, "s1/T6",
                                                after=True),
                         ["s1/T6/LOG.old.1700000001: flush_started, "
                          "compaction_started during the reads"])

    def test_the_check_command_writes_its_report(self):
        report = self.root / "report.json"
        (self.build / "s1/T4/000077.sst").write_text("x")
        done = subprocess.run(
            [sys.executable, str(PIPELINE / "price_trees.py"), "check",
             "--manifest", str(self.archive / price_trees.MANIFEST),
             "--root", str(self.build), "--tree", "s1/T4", "--after",
             "--report", str(report)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 1)
        written = json.loads(report.read_text())
        self.assertEqual((written["ok"], written["phase"], written["manifest_sha256"]),
                         (False, "after", self.identity))

    def test_a_malformed_manifest_is_refused(self):
        bad = self.root / "bad.sha256"
        bad.write_text("nothex  s1/T2/x\n")
        with self.assertRaisesRegex(ValueError, "not a sha256sum line"):
            price_trees.read_manifest(bad)

    def test_age_flags_and_settle_and_levels(self):
        self.assertTrue(price_trees.age_flags_present(f"db_bench {AGE} --x=1"))
        self.assertFalse(price_trees.age_flags_present("db_bench --ttl_seconds=0"))
        self.assertFalse(price_trees.age_flags_present(
            "db_bench --ttl_seconds=00 --periodic_compaction_seconds=0"))
        self.assertTrue(price_trees.settled("x\nRL_SETTLED ok=1 wait_micros=5\n"))
        self.assertFalse(price_trees.settled("RL_SETTLED ok=0 wait_micros=5\n"))
        self.assertFalse(price_trees.settled("no settle line\n"))
        table = ("\nLevel Files Size(MB)\n--------------------\n"
                 "  0        2        5\n  1        4       16\n  2       30      160\n\n")
        self.assertEqual(price_trees.levels("noise" + table),
                         {"0": {"files": 2, "mb": 5}, "1": {"files": 4, "mb": 16},
                          "2": {"files": 30, "mb": 160}})


class SessionTest(Ratio):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_a_price_is_the_median_of_nine_rounds(self):
        record = make_session(self.root)
        self.assertEqual(record["schema"], 5)
        self.assertEqual(record["kind"], "session")
        self.assertRatio(record["core_seconds_per_unit"]["c_f"], 0.5e-6)
        spread = record["core_seconds_spread"]["c_f"]
        self.assertEqual(len(spread["values"]), 9)
        for got, wanted in zip(spread["values"], T_F):
            self.assertRatio(got, wanted)
        self.assertRatio(spread["min"], 0.40e-6)
        self.assertRatio(spread["max"], 0.60e-6)
        for b, wanted in (("1", 0.45e-6), ("2", 0.55e-6), ("3", 0.52e-6)):
            self.assertRatio(record["per_set_median"]["c_f"][b], wanted, b)
        # The block read and the seek do not move with t_f.
        self.assertRatio(record["core_seconds_per_unit"]["c_blk"], 1e-6)
        self.assertRatio(record["core_seconds_per_unit"]["c_sk"], 2e-6)
        self.assertEqual(len(record["core_seconds_spread"]["c_sk"]["values"]), 9)
        self.assertRatio(record["c_f"], 0.5e-6 * CORE)
        # c_open and the timer's reference: five repeats of the c_open block.
        self.assertRatio(record["core_seconds_per_unit"]["c_open"], REOPEN)
        self.assertEqual(len(record["core_seconds_spread"]["c_open"]["values"]), 5)
        self.assertRatio(record["reopen_timer"]["seconds_per_reopen"], TIMER)
        self.assertEqual(record["open_files"], {"capped": 1000, "all_open": -1})
        self.assertEqual(record["machine"]["boot_id"], "boot-a")
        self.assertEqual(record["tree_set_sha256"], TREE_SET)
        self.assertEqual(len(record["stdout_sha256"]), 9 * 18 + 3 * 5 * 3 * 2)
        # A session's prices are provisional (D-22 j).
        self.assertFalse(research_objective.prices_final(record, CONTRACT))

    def test_each_round_reports_its_diagnostics(self):
        record = make_session(self.root)
        first = record["rounds"]["s1/round1"]
        # On the exact model every residual is zero and the overhead is
        # 2 us. Fitted on probes alone, each Get benchmark's slope also
        # carries the block reads that move with its probes: 0.01 per probe
        # (false positives) at 1 us each.
        self.assertRatio(first["readmissing_alone"], 0.40e-6 + 0.01e-6)
        self.assertRatio(first["readrandom_alone"], 0.40e-6 + 0.01e-6)
        self.assertRatio(first["get_overhead"], 2e-6)
        self.assertEqual(len(first["residuals"]), 18)
        for name, value in first["residuals"].items():
            self.assertAlmostEqual(value, 0.0, places=12, msg=name)

    def test_a_tree_off_the_line_shows_in_its_residual(self):
        work = session_dir(self.root / "A")
        # T=3's readrandom: above the found probes' mean, so its own slope rises.
        key = calibrate.round_key(2, 3, 3, "readrandom")
        text = (work / key / "stdout.txt").read_text()
        seconds = text.split(" seconds ")[0].rsplit(" ", 1)[1]
        slower = f"{float(seconds) + 0.3:.6f}"
        (work / key / "stdout.txt").write_text(text.replace(
            f" {seconds} seconds", f" {slower} seconds"))
        rows = [write_row(i, f) for f in FAMILIES for i in range(5)]
        record = calibrate.session(work, rows, calibrate.write_runs(rows, SHA, FAMILIES),
                                   CONTRACT, CONTRACT_SHA, Args())
        residuals = record["rounds"]["s2/round3"]["residuals"]
        worst = max(residuals, key=lambda k: abs(residuals[k]))
        self.assertEqual(worst, "T3/readrandom")
        self.assertGreater(record["rounds"]["s2/round3"]["readrandom_alone"],
                           record["rounds"]["s2/round3"]["readmissing_alone"])

    def test_the_seek_based_c_open_is_reported(self):
        # Seeks' reopens at 11 us, Gets' at 10 us: D-20 priced seeks at the
        # Gets' value; D-22 (e) reports the seeks' own beside it.
        record = make_session(self.root, seek_reopen=11e-6)
        seek = record["copen_block"]["seek_c_open"]
        self.assertRatio(seek["median"], 11e-6)
        self.assertEqual(len(seek["values"]), 5)
        self.assertEqual(seek["values"][0]["run_seeks_per_seek"],
                         {"capped": [10, 6, 5], "all_open": [10, 6, 5]})
        self.assertRatio(record["core_seconds_per_unit"]["c_open"], REOPEN)

    def test_the_three_tree_bridge_is_reported(self):
        record = make_session(self.root)
        bridge = record["copen_block"]["three_tree_fit"]
        self.assertEqual(len(bridge["values"]), 5)
        for key, wanted in (("c_f", 0.5e-6), ("c_blk", 1e-6), ("c_sk", 2e-6)):
            self.assertRatio(bridge["median"][key], wanted, key)

    def test_runs_out_of_d22s_order_are_refused(self):
        work = session_dir(self.root / "A")
        lines = (work / "runs.log").read_text().splitlines()
        # Round 2 of set 1 visiting T=2 first, as round 1 does.
        i = lines.index(calibrate.round_key(1, 2, 4, "readmissing"))
        j = lines.index(calibrate.round_key(1, 2, 2, "readmissing"))
        lines[i:i + 3], lines[j:j + 3] = lines[j:j + 3], lines[i:i + 3]
        (work / "runs.log").write_text("\n".join(lines) + "\n")
        with self.assertRaisesRegex(ValueError, "D-22 c's order"):
            calibrate.check_order(lines)

    def test_a_tree_that_changed_is_refused(self):
        for phase, edit, message in (
                ("after", {"ok": False, "problems": ["s2/T8/000099.sst: not in "
                                                     "the manifest"]}, "000099"),
                ("unpacked", {"manifest_sha256": "d" * 64}, "another archive"),
                ("after", None, "no after check")):
            with self.subTest(phase=phase, message=message):
                work = session_dir(self.root / message)
                path = work / "s2/T8" / f"{phase}.json"
                if edit is None:
                    path.unlink()
                else:
                    report = json.loads(path.read_text())
                    report.update(edit)
                    path.write_text(json.dumps(report))
                with self.assertRaisesRegex(ValueError, message):
                    calibrate.tree_checks(work, TREE_SET)

    def test_a_run_without_the_age_flags_is_refused(self):
        work = session_dir(self.root / "A")
        put(work, calibrate.round_key(3, 2, 8, "seekrandom"),
            tree(8)["all_open"]["seekrandom"], -1, age="--ttl_seconds=0")
        with self.assertRaisesRegex(ValueError, "D-22 a'"):
            calibrate.arm_open_files(calibrate.session_runs(work, "command.txt"))

    def test_another_archive_is_refused(self):
        work = session_dir(self.root / "A")
        args = Args()
        args.tree_set_sha256 = "e" * 64
        with self.assertRaisesRegex(ValueError, "D-22 i"):
            calibrate.session(work, [], [1e-9], CONTRACT, CONTRACT_SHA, args)

    def test_round_arms_are_all_open_and_copen_arms_named(self):
        self.assertEqual(calibrate.run_arm("s1/round2/T4/readrandom"), "all_open")
        self.assertEqual(calibrate.run_arm("s1/copen/T6/r3/capped/seekrandom"), "capped")
        keys = calibrate.run_keys()
        self.assertEqual(len(keys), 9 * 18 + 90)
        # The c_open block runs after set 1's three rounds, before set 2.
        first_copen = next(i for i, k in enumerate(keys) if "/copen/" in k)
        self.assertEqual(keys[first_copen - 1], "s1/round3/T6/seekrandom")
        self.assertEqual(keys[first_copen + 90], "s2/round1/T2/readmissing")
        # Arm order alternates by repeat, within each benchmark.
        self.assertEqual(keys[first_copen:first_copen + 2],
                         ["s1/copen/T2/r1/capped/readmissing",
                          "s1/copen/T2/r1/all_open/readmissing"])
        self.assertEqual(keys[first_copen + 6:first_copen + 8],
                         ["s1/copen/T2/r2/all_open/readmissing",
                          "s1/copen/T2/r2/capped/readmissing"])


class CompareTest(Ratio):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            cls.a = make_session(Path(tmp))

    def compare(self, b, a=None):
        return calibrate.compare(a or self.a, b, CONTRACT, CONTRACT_SHA, ("1", "2"))

    def test_the_tolerance_at_its_edges(self):
        # |B - A| <= 0.03 (A + B) / 2: B up to A x 1.030457, down to A x 0.970443.
        for factor, passes in ((1.0304, True), (1.0306, False),
                               (0.9705, True), (0.9703, False), (1.0, True)):
            with self.subTest(factor=factor):
                self.assertEqual(research_objective.reproducible(
                    100.0, 100.0 * factor, TOLERANCE), passes)
                record = self.compare(scaled(other_session(self.a), "c_sk", factor))
                self.assertEqual(record["reproducibility"]["prices"]["c_sk"]["passed"],
                                 passes)
                self.assertEqual(record["reproducibility"]["passed"], passes)
                self.assertEqual(record["kind"], "final" if passes else "failed")

    def test_every_tested_price_can_fail_the_test(self):
        for name in calibrate.TESTED:
            with self.subTest(name=name):
                record = self.compare(scaled(other_session(self.a), name, 0.95))
                test = record["reproducibility"]["prices"]
                self.assertFalse(test[name]["passed"])
                self.assertTrue(all(t["passed"] for n, t in test.items() if n != name))
                self.assertFalse(research_objective.prices_final(record, CONTRACT))

    def test_a_pass_pools_both_sessions(self):
        b = scaled(scaled(other_session(self.a), "c_f", 1.02), "reopen_timer", 0.99)
        record = self.compare(b)
        self.assertTrue(record["reproducibility"]["passed"])
        self.assertTrue(research_objective.prices_final(record, CONTRACT))
        self.assertEqual(len(record["core_seconds_spread"]["c_f"]["values"]), 18)
        self.assertEqual(len(record["core_seconds_spread"]["c_open"]["values"]), 10)
        self.assertEqual(len(record["reopen_timer"]["values"]), 10)
        # The median of 18: A's nine and B's nine (2% higher).
        pooled = sorted(T_F + [v * 1.02 for v in T_F])
        self.assertRatio(record["core_seconds_per_unit"]["c_f"],
                         (pooled[8] + pooled[9]) / 2)
        self.assertRatio(record["c_f"], record["core_seconds_per_unit"]["c_f"] * CORE)
        self.assertRatio(record["core_seconds_spread"]["c_f"]["max"], 0.60e-6 * 1.02)
        self.assertEqual(sorted(record["sessions"]), ["A", "B"])
        self.assertEqual(record["tree_set_sha256"], TREE_SET)
        self.assertEqual({r["check"] for r in record["qbar_reopen_checks"]["assoc"]},
                         {"held"})
        self.assertEqual(research_objective.checked_prices(record, CONTRACT)["c_f"],
                         record["c_f"])

    def test_sessions_that_cannot_be_compared_are_refused(self):
        b = other_session(self.a)
        cases = [(other_session(self.a, boot="boot-a"), "no reboot"),
                 (other_session(self.a, name="A"), "both records are session"),
                 ({**b, "db_bench_sha256": "f" * 64}, "db_bench_sha256"),
                 ({**b, "tree_set_sha256": "f" * 64}, "tree_set_sha256"),
                 ({**b, "kind": "final"}, "one session's record"),
                 ({**b, "schema": 4}, "one session's record"),
                 ({**b, "research_objective_sha256": "f" * 64}, "another contract")]
        for record, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    self.compare(record)

    def test_the_compare_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.json").write_text(json.dumps(self.a))
            (root / "b.json").write_text(json.dumps(other_session(self.a)))
            (root / "c.json").write_text(json.dumps(
                scaled(other_session(self.a), "c_blk", 1.05)))
            script = str(PIPELINE / "18_calibrate_prices.py")
            out = root / "prices.json"

            def run(b, *extra):
                return subprocess.run([sys.executable, script, "compare",
                                       str(root / "a.json"), str(root / b),
                                       "--output", str(out), *extra],
                                      capture_output=True, text=True, cwd=PIPELINE)
            done = run("b.json")
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("PASSED at 3%", done.stdout)
            self.assertEqual(json.loads(out.read_text())["kind"], "final")
            again = run("c.json")
            self.assertEqual(again.returncode, 2)
            self.assertIn("exists", again.stderr)
            failed = run("c.json", "--replace")
            self.assertEqual(failed.returncode, 1)
            self.assertIn("c_blk", failed.stdout)
            self.assertIn("FAILED", failed.stdout)
            self.assertEqual(json.loads(out.read_text())["kind"], "failed")


class Schema5Test(unittest.TestCase):
    """D-22 (j): only a final file prices an official run."""

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            cls.session = make_session(Path(tmp))
        cls.final = calibrate.compare(cls.session, other_session(cls.session),
                                      CONTRACT, CONTRACT_SHA, ("1", "2"))
        cls.failed = calibrate.compare(
            cls.session, scaled(other_session(cls.session), "c_f", 1.2),
            CONTRACT, CONTRACT_SHA, ("1", "2"))
        cls.schema4 = {**{k: cls.final[k] for k in research_objective.DEVICE_PRICES},
                       "schema": 4, "price_per_core_second": CORE,
                       "reopen_timer": cls.final["reopen_timer"]}

    def test_final_prices_price_any_run(self):
        self.assertEqual(research_objective.PRICES_SCHEMA, 5)
        research_objective.checked_prices(self.final, CONTRACT)

    def test_provisional_prices_need_a_diagnostic_run(self):
        for name, record in (("schema 4", self.schema4), ("session", self.session),
                             ("failed", self.failed)):
            with self.subTest(name=name):
                self.assertFalse(research_objective.prices_final(record, CONTRACT))
                with self.assertRaisesRegex(ValueError, "provisional prices"):
                    research_objective.checked_prices(record, CONTRACT)
                research_objective.checked_prices(record, CONTRACT,
                                                  provisional_ok=True)

    def test_a_pass_at_another_tolerance_is_not_final(self):
        record = copy.deepcopy(self.final)
        record["reproducibility"]["tolerance"] = 0.05
        self.assertFalse(research_objective.prices_final(record, CONTRACT))

    def test_older_schemas_are_refused_even_for_diagnostics(self):
        for schema in (1, 2, 3):
            with self.assertRaisesRegex(ValueError, "per core-second"):
                research_objective.checked_prices({**self.schema4, "schema": schema},
                                                  CONTRACT, provisional_ok=True)

    def test_the_plugin_config_takes_provisional_prices_only_when_diagnostic(self):
        def compose(prices, **flags):
            return plugin_config.compose(
                "hold", "read", "assoc", bounds=plugin_config.PLACEHOLDERS,
                settings=plugin_config.PLACEHOLDERS, contract=CONTRACT,
                prices=prices, admission={"k": 3}, decision_log="d",
                transition_log="t", **flags)
        config, _ = compose(self.final)
        self.assertEqual(config["c_f"], self.final["c_f"])
        with self.assertRaisesRegex(ValueError, "provisional prices"):
            compose(self.session)
        self.assertEqual(compose(self.session, diagnostic=True)[0]["c_f"],
                         self.session["c_f"])
        self.assertEqual(compose(self.schema4, placeholders=True)[0]["c_f"],
                         self.schema4["c_f"])


def executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


class StagesTest(unittest.TestCase):
    """29 and 18 end to end on a copy of the pipeline, with the stand-in
    db_bench (tests/fixtures/chain/fake_db_bench.py)."""

    SCRUBBED = ("PRICE_TREES", "PRICE_TREES_SHA256", "PRICE_SESSION",
                "PRICE_SESSIONS_DIR", "DB_ROOT", "DBBENCH_BUILD_DIR",
                "PREFLIGHT_WORK_DIR", "PRICES_FILE", "BUILD_ATTEMPTS",
                "FAKE_UNSETTLED_ONCE", "FAKE_COMPACT_ON_READ", "DIAGNOSTIC_RUN")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "repo"
        shutil.copytree(PIPELINE, self.root / "scripts" / "dbbench_pipeline",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(REPO / "config", self.root / "config")
        self.db_bench = self.root / "build-dbbench" / "db_bench"
        executable(self.db_bench,
                   f'#!/usr/bin/env bash\nexec "{sys.executable}" "{FAKE}" "$@"\n')
        self.venv = base / "venv"
        executable(self.venv / "bin" / "python",
                   f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n')
        self.archive = base / "archive"
        self.dbs = base / "dbs"
        self.markers = base / "markers"
        self.markers.mkdir()
        identity = preflight_marker.db_bench_identity(self.db_bench)
        self.summary = summary(base / "summary.csv", identity)

    def run_stage(self, script: str, *args, **overrides) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in self.SCRUBBED}
        env.update({"DBBENCH_CPUS": "", "CONTROLLER_CPUS": "",
                    "PYTHON_VENV": str(self.venv), "MIN_FREE_GB_BUILD": "0",
                    "MIN_FREE_GB_ARCHIVE": "0", "MIN_FREE_GB_SESSION": "0",
                    **overrides})
        return subprocess.run(
            ["bash", str(self.root / "scripts" / "dbbench_pipeline" / script), *args],
            cwd=self.root, env=env, capture_output=True, text=True, timeout=600)

    def build(self, **overrides) -> str:
        built = self.run_stage("29_build_price_trees.sh", CONFIRM_PRICE_TREES="YES",
                               PRICE_TREES=str(self.archive),
                               DB_ROOT=str(self.dbs), **overrides)
        self.assertEqual(built.returncode, 0, built.stdout[-3000:] + built.stderr[-3000:])
        return built.stdout.split("tree-set identity ")[1].split()[0]

    def session(self, name: str, identity: str, **overrides):
        return self.run_stage("18_calibrate_prices.sh", str(self.summary),
                              CONFIRM_PRICE_CALIBRATION="YES",
                              PRICE_TREES=str(self.archive),
                              PRICE_TREES_SHA256=identity, PRICE_SESSION=name,
                              DB_ROOT=str(self.dbs), **overrides)

    def test_build_two_sessions_and_compare(self):
        identity = self.build(FAKE_UNSETTLED_ONCE=str(self.markers))
        record = json.loads((self.archive / "build_record.json").read_text())
        self.assertEqual(record["tree_set_sha256"], identity)
        self.assertEqual(len(record["trees"]), 18)
        # The first build of s1/T2 did not settle: discarded, rebuilt, kept.
        self.assertEqual([a["settle_ok"] for a in record["trees"]["s1/T2"]["attempts"]],
                         [False, True])
        self.assertEqual(record["trees"]["s2/T8"]["levels"]["1"], {"files": 1, "mb": 0})
        self.assertEqual(record["trees"]["s2/T8"]["sst_files"], 2)
        for name, built in record["trees"].items():
            self.assertTrue(price_trees.age_flags_present(built["command"]), name)
        self.assertEqual(len(list(self.archive.glob("*.tgz"))), 18)
        self.assertEqual(price_trees.identity(self.archive / "MANIFEST.sha256"), identity)
        # The archive unpacks to the manifest's paths, time stamps kept.
        listed = subprocess.run(["tar", "-tvzf", str(self.archive / "s3-T6.tgz")],
                                capture_output=True, text=True).stdout
        self.assertIn("s3/T6/000010.sst", listed)
        self.assertIn(" 0/0 ", listed)

        done = self.session("A", identity)
        self.assertEqual(done.returncode, 0, done.stdout[-3000:] + done.stderr[-3000:])
        sessions = self.root / "build-dbbench" / "price-sessions"
        a = json.loads((sessions / "A" / "session.json").read_text())
        self.assertEqual((a["schema"], a["kind"], a["tree_set_sha256"]),
                         (5, "session", identity))
        for key, seconds in (("c_f", 0.5e-6), ("c_blk", 1e-6), ("c_sk", 2e-6),
                             ("c_open", 10e-6)):
            self.assertAlmostEqual(a["core_seconds_per_unit"][key] / seconds, 1.0,
                                   places=6, msg=key)
        self.assertAlmostEqual(a["reopen_timer"]["seconds_per_reopen"] / 8e-6, 1.0,
                               places=6)
        self.assertAlmostEqual(a["copen_block"]["seek_c_open"]["median"] / 10e-6, 1.0,
                               places=6)
        commands = list((sessions / "A").rglob("command.txt"))
        self.assertEqual(len(commands), 9 * 18 + 90 + 18)
        for command in commands:
            self.assertTrue(price_trees.age_flags_present(command.read_text()), command)
        self.assertEqual(a["machine"]["boot_id"], price_trees.machine("")["boot_id"])
        # Each set's unpacked trees are removed once checked.
        self.assertFalse((self.dbs / "price-trees" / "A").exists())

        again = self.session("A", identity)
        self.assertEqual(again.returncode, 2)
        self.assertIn("never measured over another", again.stderr)

        done = self.session("B", identity)
        self.assertEqual(done.returncode, 0, done.stderr[-3000:])
        b_path = sessions / "B" / "session.json"
        script = str(self.root / "scripts" / "dbbench_pipeline" / "18_calibrate_prices.py")
        prices = self.root / "build-dbbench" / "prices.json"
        compare = [sys.executable, script, "compare", str(sessions / "A" / "session.json"),
                   str(b_path), "--output", str(prices)]
        same_boot = subprocess.run(compare, capture_output=True, text=True)
        self.assertNotEqual(same_boot.returncode, 0)
        self.assertIn("no reboot", same_boot.stderr)
        b = json.loads(b_path.read_text())
        b["machine"]["boot_id"] = "after-a-reboot"
        b_path.write_text(json.dumps(b))
        compared = subprocess.run(compare, capture_output=True, text=True)
        self.assertEqual(compared.returncode, 0, compared.stderr)
        final = json.loads(prices.read_text())
        self.assertEqual(final["kind"], "final")
        self.assertTrue(research_objective.prices_final(final, CONTRACT))

    def test_a_compaction_during_the_reads_is_refused(self):
        identity = self.build()
        done = self.session("A", identity, FAKE_COMPACT_ON_READ="1")
        self.assertEqual(done.returncode, 1)
        self.assertIn("000099.sst: not in the manifest", done.stderr)
        sessions = self.root / "build-dbbench" / "price-sessions"
        self.assertFalse((sessions / "A" / "session.json").exists())
        # The tree stays for inspection.
        self.assertTrue((self.dbs / "price-trees" / "A" / "s1" / "T2" /
                         "000099.sst").exists())

    def test_the_archive_must_be_the_recorded_one(self):
        identity = self.build()
        done = self.session("A", "0" * 64)
        self.assertEqual(done.returncode, 1)
        self.assertIn("D-22 i", done.stderr)
        tgz = self.archive / "s2-T4.tgz"
        tgz.write_bytes(tgz.read_bytes() + b"\0")
        done = self.session("B", identity)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("s2-T4.tgz", done.stdout + done.stderr)
        self.assertFalse((self.root / "build-dbbench" / "price-sessions" / "B" /
                          "session.json").exists())

    def test_29_refusals(self):
        self.archive.mkdir()
        cases = [({}, 2, "never rebuilt in place"),
                 ({"PRICE_TREES": str(self.root / "archive")}, 2,
                  "inside the repository"),
                 ({"CONFIRM_PRICE_TREES": "no"}, 2, "CONFIRM_PRICE_TREES=YES")]
        for overrides, code, message in cases:
            with self.subTest(message=message):
                env = {"CONFIRM_PRICE_TREES": "YES", "PRICE_TREES": str(self.archive),
                       "DB_ROOT": str(self.dbs), **overrides}
                done = self.run_stage("29_build_price_trees.sh", **env)
                self.assertEqual(done.returncode, code)
                self.assertIn(message, done.stderr)


if __name__ == "__main__":
    unittest.main()
