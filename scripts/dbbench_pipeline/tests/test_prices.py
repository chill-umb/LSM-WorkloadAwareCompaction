"""18_calibrate_prices.py (PREREGISTRATION D-15 §3 as amended by D-20):
marginal read times from three trees' all-open runs, the reopen time from
the two arms, the write time from the q-bar native arms, money prices at the
price per core-second, and every refusal.

The synthetic trees obey the model exactly: every Get pays 2 us of overhead,
0.5 us per filter probe and 1 us per block read; every seek 3 us plus 2 us
per run; and in the capped arm, 10 us per table reopen, on half the probes
and run seeks. So the fit must return t_f = 0.5 us, t_blk = 1 us,
t_sk = 2 us and t_open = 10 us.
"""
import copy
import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import research_objective

PIPELINE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "calibrate_prices", PIPELINE / "18_calibrate_prices.py")
calibrate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calibrate)
CONTRACT, _ = research_objective.load_contract()
CORE = CONTRACT["prices"]["price_per_core_second"]
OPS = 1_000_000
SHA = "b" * 64


def stdout(name, per_op, **per_op_tickers):
    """One process's output: db_bench 11's result line and its tickers."""
    seconds = per_op * OPS
    return (f"{name:<12} : {per_op * 1e6:11.3f} micros/op 1 ops/sec "
            f"{seconds:.6f} seconds {OPS} operations; (1 of 1 found)\n"
            "STATISTICS:\n" +
            "".join(f"rocksdb.{k.replace('_', '.')} COUNT : {round(v * OPS)}\n"
                    for k, v in per_op_tickers.items()))


def get_seconds(probes, blocks):
    return 2e-6 + 0.5e-6 * probes + 1e-6 * blocks


REOPEN = 10e-6
DB_OPEN = 0.006   # tables opened per operation when the DB opens, untimed
TIMER = 8.3e-6    # the fork's timer of one reopen (D-21), short of c_open


def tree(missing_probes, found_probes, runs, reopen_share=0.5, timed=True):
    """One tree's three benchmarks in both arms. A missing key reads a block
    only on a 1% filter false positive; a present key reads the block it is
    found in. The capped arm reopens a table on reopen_share of its probes
    and run seeks; the all-open arm never does. With timed, the fork counts
    and times the reads' reopens (D-21) at TIMER each."""
    missing_blocks = 0.01 * missing_probes
    found_blocks = 1 + 0.01 * (found_probes - 1)

    def reopened(n):
        return ({"read_table_reopen": n, "read_table_reopen_nanos": TIMER * 1e9 * n}
                if timed else {})

    arms = {}
    for arm in calibrate.ARMS:
        share = 0.0 if arm == "all_open" else reopen_share
        arms[arm] = {
            "readmissing": stdout(
                "readmissing", get_seconds(missing_probes, missing_blocks) +
                REOPEN * share * missing_probes,
                point_sst_probe=missing_probes,
                bloom_filter_full_positive=missing_blocks,
                no_file_opens=DB_OPEN + share * missing_probes,
                **reopened(share * missing_probes)),
            "readrandom": stdout(
                "readrandom", get_seconds(found_probes, found_blocks) +
                REOPEN * share * found_probes,
                point_sst_probe=found_probes,
                bloom_filter_full_positive=found_blocks,
                no_file_opens=DB_OPEN + share * found_probes,
                **reopened(share * found_probes)),
            "seekrandom": stdout(
                "seekrandom", 3e-6 + 2e-6 * runs + REOPEN * share * runs,
                sorted_run_seek=runs, no_file_opens=DB_OPEN + share * runs,
                **reopened(share * runs)),
        }
    return arms


def replaced(trees, arm, benchmark, text_of):
    """trees with one arm's benchmark replaced by text_of(tree, i)."""
    out = copy.deepcopy(trees)
    for i, t in enumerate(out):
        t[arm][benchmark] = text_of(trees[i], i)
    return out


# T = 2, 6 and 10: the deeper the tree, the more probes and runs.
TREES = [tree(9, 8, 10), tree(5, 4.5, 6), tree(4, 3.5, 5)]


FAMILIES = ["assoc", "powerlaw_get95"]


def write_row(i=0, family="assoc", **extra):
    # 20000 reopens at the timer's 8.3 us: the reopen check holds (D-21).
    row = {"arm": "native", "size_ratio": "10", "settle_ok": "1",
           "dbbench_sha256": SHA, "sst_write_seconds": "2.0",
           "sst_bytes_written": "1000000000", "workload_family": family,
           "table_reopens": "20000", "reopen_seconds": repr(20000 * TIMER),
           "result_directory": f"/r/{family}/{i}"}
    row.update(extra)
    return row


def write_rows(per_family=5):
    """Five q-bar arms of each workload family, as D-14 §2 runs them."""
    return [write_row(i, family) for family in FAMILIES
            for i in range(per_family)]


class PricesTest(unittest.TestCase):
    def assertRatio(self, value, expected, msg=None):
        # Relative: these are 1e-7 s and 1e-12 USD, far below the default
        # absolute tolerance.
        self.assertAlmostEqual(value / expected, 1.0, places=6, msg=msg)

    def test_marginal_read_times(self):
        seconds = calibrate.read_repeat(TREES)
        for key, value in {"c_f": 0.5e-6, "c_blk": 1e-6, "c_sk": 2e-6,
                           "c_open": 10e-6}.items():
            self.assertRatio(seconds[key], value, key)

    def test_the_overhead_is_not_charged_to_the_probes(self):
        # The draft's average divided readmissing's whole time by its probes:
        # (2 + 4.5 + 0.09) us over 9 probes, 0.73 us, not 0.5.
        self.assertLess(calibrate.read_repeat(TREES)["c_f"], 0.51e-6)

    def test_the_reopens_are_not_charged_to_the_probes(self):
        # D-15's fit on the capped runs alone (run 3, 2026-10-02) charged
        # each probe its share of reopens: 0.5 + 0.5 x 10 us, not 0.5.
        capped = calibrate.marginal_times([t["capped"] for t in TREES])
        self.assertRatio(capped["c_f"], 5.5e-6)
        self.assertRatio(calibrate.read_repeat(TREES)["c_f"], 0.5e-6)

    def test_the_reopen_timer_is_the_capped_gets_time_per_reopen(self):
        # D-21's reference: the fork's own timer, not c_open's marginal time.
        self.assertRatio(calibrate.reopen_timer(TREES), TIMER)
        self.assertRatio(calibrate.read_repeat(TREES)["c_open"], REOPEN)

    def test_a_binary_without_the_reopen_timer_is_refused(self):
        trees = [tree(9, 8, 10, timed=False), tree(5, 4.5, 6, timed=False),
                 tree(4, 3.5, 5, timed=False)]
        with self.assertRaisesRegex(ValueError, "timed no reopens.*D-21"):
            calibrate.reopen_timer(trees)

    def test_timed_reopens_beyond_the_opens_are_refused(self):
        def more(t, i):
            return t["capped"]["readmissing"].replace(
                "rocksdb.no.file.opens COUNT : 4506000",
                "rocksdb.no.file.opens COUNT : 4000000")
        trees = replaced(TREES, "capped", "readmissing", more)
        with self.assertRaisesRegex(ValueError, "more than its"):
            calibrate.reopen_timer(trees)

    def test_qbar_arms_are_checked_against_the_reference(self):
        rows = [write_row(0),
                write_row(1, table_reopens="20000",
                          reopen_seconds=repr(20000 * TIMER * 1.2)),
                write_row(2, table_reopens="500",
                          reopen_seconds=repr(500 * TIMER)),
                write_row(3, family="powerlaw_get95", table_reopens="",
                          reopen_seconds="")]
        checks = calibrate.qbar_reopen_checks(rows, TIMER, CONTRACT)
        self.assertEqual([r["check"] for r in checks["assoc"]],
                         ["held", "does not hold", "too few reopens"])
        self.assertAlmostEqual(checks["assoc"][1]["ratio"], 1.2)
        self.assertEqual(checks["powerlaw_get95"],
                         [{"run": "/r/powerlaw_get95/3", "reopens": None,
                           "ratio": None, "check": "no reopen counters"}])

    def test_an_all_open_arm_that_reopens_is_refused(self):
        # open_files -1 did not take: the all-open arm reopens as the capped.
        trees = replaced(TREES, "all_open", "readmissing",
                         lambda t, i: t["capped"]["readmissing"])
        with self.assertRaisesRegex(ValueError, "did not keep the tables open"):
            calibrate.reopen_time(trees)

    def test_too_few_reopens_do_not_identify_t_open(self):
        # 0.05 of 3.5 to 9 probes: 0.18 to 0.45 more reopens per Get.
        trees = [tree(9, 8, 10, 0.05), tree(5, 4.5, 6, 0.05),
                 tree(4, 3.5, 5, 0.05)]
        with self.assertRaisesRegex(ValueError, "t_open.*not identified"):
            calibrate.read_repeat(trees)

    def test_arms_that_read_differently_are_refused(self):
        # The all-open readrandom of a deeper tree: more probes per Get.
        trees = replaced(TREES, "all_open", "readrandom",
                         lambda t, i: tree(9, 8.5, 10)["all_open"]["readrandom"])
        with self.assertRaisesRegex(ValueError, "read differently.*filter probes"):
            calibrate.reopen_time(trees)

    def test_open_files_is_named_once_with_each_arms_value(self):
        good = {"T2/r1/capped/readmissing": "db_bench --open_files=1000 --x=1",
                "T2/r1/all_open/readmissing": "db_bench --open_files=-1 --x=1"}
        self.assertEqual(calibrate.arm_open_files(good),
                         {"capped": 1000, "all_open": -1})
        for name, command, message in (
                ("T2/r1/all_open/readmissing", "db_bench --x=1", "0 times"),
                ("T2/r1/all_open/readmissing",
                 "--open_files=1000 --open_files=-1", "2 times"),
                ("T2/r1/all_open/readmissing", "--open_files=1000", "not -1"),
                ("T2/r1/capped/readmissing", "--open_files=-1", "one positive"),
                ("T6/r1/capped/readmissing", "--open_files=500", "one positive")):
            commands = dict(good)
            commands[name] = command
            with self.assertRaisesRegex(ValueError, message, msg=command):
                calibrate.arm_open_files(commands)

    def test_write_time_per_run(self):
        runs = calibrate.write_runs(write_rows(), SHA, FAMILIES)
        self.assertEqual(len(runs), 10)
        self.assertRatio(runs[0], 2e-9)

    def test_median_and_spread(self):
        seconds, spread = calibrate.summarise(
            {"c_w": [1.0, 3.0, 2.0, 5.0, 4.0], "c_f": [2.0, 1.0, 3.0]})
        self.assertEqual(seconds, {"c_w": 3.0, "c_f": 2.0})
        self.assertEqual((spread["c_w"]["min"], spread["c_w"]["max"]), (1.0, 5.0))

    def test_money_prices_are_per_core_second(self):
        prices = CONTRACT["prices"]
        self.assertAlmostEqual(
            prices["price_per_core_second"] * prices["instance_cores"],
            prices["instance_price_per_second"])
        money = calibrate.prices({"c_w": 2e-9, "c_f": 5e-7, "c_blk": 1e-6,
                                  "c_sk": 2e-6, "c_open": 1e-5}, CONTRACT)
        self.assertRatio(money["c_f"], 5e-7 * CORE)
        self.assertRatio(money["c_open"], 1e-5 * CORE)
        self.assertEqual(money["c_s"], prices["storage_price_per_byte_second"])

    def test_trees_of_one_depth_do_not_identify_a_slope(self):
        flat = [tree(4, 3.5, 5), tree(4.5, 4, 5.5), tree(4, 3.5, 5)]
        with self.assertRaisesRegex(ValueError, "t_f.*not identified"):
            calibrate.read_repeat(flat)

    def test_seeks_of_one_depth_do_not_identify_a_slope(self):
        trees = replaced(TREES, "all_open", "seekrandom",
                         lambda t, i: tree(4, 3.5, 5)["all_open"]["seekrandom"])
        with self.assertRaisesRegex(ValueError, "t_sk.*not identified"):
            calibrate.read_repeat(trees)

    def test_readrandom_must_read_the_found_block(self):
        trees = replaced(TREES, "all_open", "readrandom",
                         lambda t, i: t["all_open"]["readmissing"].replace(
                             "readmissing", "readrandom"))
        with self.assertRaisesRegex(ValueError, "t_blk"):
            calibrate.read_repeat(trees)

    def test_a_nonpositive_repeat_is_refused(self):
        with self.assertRaisesRegex(ValueError, "c_sk.*not positive"):
            calibrate.summarise({"c_sk": [2e-6, -1e-7, 2e-6]})

    def test_a_faster_deeper_tree_is_refused(self):
        # Deeper trees taking less time per seek: a negative slope.
        runs = (10, 6, 5)
        trees = replaced(TREES, "all_open", "seekrandom",
                         lambda t, i: t["all_open"]["seekrandom"].replace(
                             f"{(3e-6 + 2e-6 * runs[i]) * OPS:.6f} seconds",
                             f"{(30e-6 - 2e-6 * runs[i]) * OPS:.6f} seconds"))
        seconds = calibrate.read_repeat(trees)
        self.assertLess(seconds["c_sk"], 0)
        with self.assertRaisesRegex(ValueError, "c_sk"):
            calibrate.summarise({"c_sk": [seconds["c_sk"]]})

    def test_write_runs_are_refused_unless_qbar_native_arms(self):
        cases = {"arm": ("static:uniform_0_75", "settled native arm at T=10"),
                 "size_ratio": ("2", "settled native arm at T=10"),
                 "settle_ok": ("nan", "settled native arm at T=10"),
                 "dbbench_sha256": ("c" * 64, "another db_bench"),
                 "sst_write_seconds": ("nan", "write seconds"),
                 "sst_bytes_written": ("0", "write seconds")}
        cases["workload_family"] = ("uniform", "not one of")
        for field, (value, message) in cases.items():
            rows = write_rows()
            rows[3][field] = value
            with self.assertRaisesRegex(ValueError, message, msg=field):
                calibrate.write_runs(rows, SHA, FAMILIES)

    def test_fewer_than_five_write_runs_of_a_family_are_refused(self):
        rows = write_rows()[:-1]
        with self.assertRaisesRegex(ValueError, "fewer than 5.*powerlaw_get95"):
            calibrate.write_runs(rows, SHA, FAMILIES)

    def test_a_run_given_twice_is_refused(self):
        rows = write_rows() + [write_row(0, "assoc")]
        with self.assertRaisesRegex(ValueError, "given twice"):
            calibrate.write_runs(rows, SHA, FAMILIES)

    def test_rejects_nonpositive_storage_price(self):
        for value in (0, -1e-17, None):
            contract = copy.deepcopy(CONTRACT)
            contract["prices"]["storage_price_per_byte_second"] = value
            with self.assertRaisesRegex(ValueError, "c_s"):
                calibrate.prices({"c_w": 1e-9, "c_f": 1e-9, "c_blk": 1e-9,
                                  "c_sk": 1e-9, "c_open": 1e-9}, contract)

    def test_rejects_missing_core_price(self):
        contract = copy.deepcopy(CONTRACT)
        contract["prices"]["price_per_core_second"] = None
        with self.assertRaisesRegex(ValueError, "price"):
            calibrate.prices({"c_w": 1e-9, "c_f": 1e-9, "c_blk": 1e-9,
                              "c_sk": 1e-9, "c_open": 1e-9}, contract)

    def test_rejects_missing_device_price(self):
        with self.assertRaisesRegex(ValueError, "c_sk"):
            research_objective.validate_prices(
                {"c_w": 1e-9, "c_f": 1e-9, "c_blk": 1e-9}, CONTRACT)
        with self.assertRaisesRegex(ValueError, "c_open"):
            research_objective.validate_prices(
                {"c_w": 1e-9, "c_f": 1e-9, "c_blk": 1e-9, "c_sk": 1e-9}, CONTRACT)

    def test_rejects_non_numeric_or_infinite_prices(self):
        for value in (True, float("inf"), float("nan"), "1e-9"):
            with self.assertRaisesRegex(ValueError, "c_w", msg=repr(value)):
                research_objective.validate_prices(
                    {"c_w": value, "c_f": 1e-9, "c_blk": 1e-9, "c_sk": 1e-9,
                     "c_open": 1e-9}, CONTRACT)

    def test_main_reads_the_layout_18_sh_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            open_files = {"capped": "1000", "all_open": "-1"}
            for ratio, trees in zip(calibrate.SIZE_RATIOS, TREES):
                for repeat in range(1, calibrate.REPEATS + 1):
                    for arm, runs in trees.items():
                        for name, text in runs.items():
                            out = (root / "work" / f"T{ratio}" / f"r{repeat}" /
                                   arm / name)
                            out.mkdir(parents=True)
                            (out / "stdout.txt").write_text(text)
                            (out / "command.txt").write_text(
                                f"db_bench --open_files={open_files[arm]} "
                                f"--benchmarks={name}")
                load = root / "work" / f"T{ratio}" / "load"
                load.mkdir()
                (load / "command.txt").write_text(f"db_bench T{ratio}")
            summary = root / "summary.csv"
            with summary.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(write_row()))
                writer.writeheader()
                writer.writerows(
                    write_row(i, family, sst_write_seconds=str(i + 1))
                    for family in FAMILIES for i in range(5))
            script = str(PIPELINE / "18_calibrate_prices.py")
            check = subprocess.run(
                [sys.executable, script, "--check-writes", "--write-summary",
                 str(summary), "--db-bench-sha256", SHA],
                capture_output=True, text=True, cwd=PIPELINE)
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertIn("10 runs", check.stdout)
            done = subprocess.run(
                [sys.executable, script, str(root / "work"), "--write-summary",
                 str(summary), "--db-bench-sha256", SHA,
                 "--output", str(root / "prices.json")],
                capture_output=True, text=True, cwd=PIPELINE)
            self.assertEqual(done.returncode, 0, done.stderr)
            record = json.loads((root / "prices.json").read_text())
        self.assertEqual(record["schema"], 4)
        self.assertEqual(record["schema"], research_objective.PRICES_SCHEMA)
        # D-21: the timer's reference, and every q-bar arm checked against it.
        self.assertRatio(record["reopen_timer"]["seconds_per_reopen"], TIMER)
        self.assertEqual(len(record["reopen_timer"]["values"]),
                         calibrate.REPEATS)
        self.assertEqual(research_objective.reopen_reference(record),
                         record["reopen_timer"]["seconds_per_reopen"])
        for family in FAMILIES:
            self.assertEqual([r["check"] for r in
                              record["qbar_reopen_checks"][family]],
                             ["held"] * 5)
        self.assertIn("reopen check, assoc q-bar arms", done.stdout)
        self.assertEqual(record["open_files"], {"capped": 1000, "all_open": -1})
        self.assertRatio(record["c_w"], 3e-9 * CORE)
        self.assertRatio(record["c_f"], 0.5e-6 * CORE)
        self.assertRatio(record["c_blk"], 1e-6 * CORE)
        self.assertRatio(record["c_sk"], 2e-6 * CORE)
        self.assertRatio(record["c_open"], 10e-6 * CORE)
        for key in ("c_f", "c_open"):
            self.assertEqual(len(record["core_seconds_spread"][key]["values"]),
                             calibrate.REPEATS)
        self.assertEqual(len(record["stdout_sha256"]), 90)
        self.assertEqual(record["load_commands"]["T6"], "db_bench T6")
        per_op = record["read_processes_per_operation"]
        first = per_op["T2/r1/capped/readmissing"]
        self.assertAlmostEqual(first["rocksdb.point.sst.probe"], 9)
        self.assertAlmostEqual(first["rocksdb.no.file.opens"], DB_OPEN + 4.5)
        self.assertAlmostEqual(first["rocksdb.read.table.reopen"], 4.5)
        self.assertAlmostEqual(first["rocksdb.read.table.reopen.nanos"],
                               4.5 * TIMER * 1e9)
        self.assertAlmostEqual(
            per_op["T2/r1/all_open/readmissing"]["rocksdb.no.file.opens"], DB_OPEN)

    def test_missing_result_line_is_refused(self):
        trees = replaced(TREES[:1], "all_open", "seekrandom",
                         lambda t, i: "STATISTICS:\n") + TREES[1:]
        with self.assertRaisesRegex(ValueError, "seekrandom"):
            calibrate.read_repeat(trees)


if __name__ == "__main__":
    unittest.main()
