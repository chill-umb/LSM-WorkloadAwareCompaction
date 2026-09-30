"""18_calibrate_prices.py (PREREGISTRATION D-15 §3): marginal read times
from three trees, the write time from the q-bar native arms, money prices at
the price per core-second, and every refusal.

The synthetic trees obey the model exactly: every Get pays 2 us of overhead,
0.5 us per filter probe and 1 us per block read; every seek 3 us plus 2 us
per run. So the fit must return t_f = 0.5 us, t_blk = 1 us, t_sk = 2 us.
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


def tree(missing_probes, found_probes, runs, scale=1.0):
    """One tree's three benchmarks. A missing key reads a block only on a
    1% filter false positive; a present key reads the block it is found in."""
    missing_blocks = 0.01 * missing_probes
    found_blocks = 1 + 0.01 * (found_probes - 1)
    return {
        "readmissing": stdout("readmissing",
                              scale * get_seconds(missing_probes, missing_blocks),
                              point_sst_probe=missing_probes,
                              bloom_filter_full_positive=missing_blocks),
        "readrandom": stdout("readrandom",
                             scale * get_seconds(found_probes, found_blocks),
                             point_sst_probe=found_probes,
                             bloom_filter_full_positive=found_blocks),
        "seekrandom": stdout("seekrandom", scale * (3e-6 + 2e-6 * runs),
                             sorted_run_seek=runs),
    }


# T = 2, 6 and 10: the deeper the tree, the more probes and runs.
TREES = [tree(9, 8, 10), tree(5, 4.5, 6), tree(4, 3.5, 5)]


FAMILIES = ["assoc", "powerlaw_get95"]


def write_row(i=0, family="assoc", **extra):
    row = {"arm": "native", "size_ratio": "10", "settle_ok": "1",
           "dbbench_sha256": SHA, "sst_write_seconds": "2.0",
           "sst_bytes_written": "1000000000", "workload_family": family,
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
        for key, value in {"c_f": 0.5e-6, "c_blk": 1e-6, "c_sk": 2e-6}.items():
            self.assertRatio(seconds[key], value, key)

    def test_the_overhead_is_not_charged_to_the_probes(self):
        # The draft's average divided readmissing's whole time by its probes:
        # (2 + 4.5 + 0.09) us over 9 probes, 0.73 us, not 0.5.
        self.assertLess(calibrate.read_repeat(TREES)["c_f"], 0.51e-6)

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
                                  "c_sk": 2e-6}, CONTRACT)
        self.assertRatio(money["c_f"], 5e-7 * CORE)
        self.assertEqual(money["c_s"], prices["storage_price_per_byte_second"])

    def test_trees_of_one_depth_do_not_identify_a_slope(self):
        flat = [tree(4, 3.5, 5), tree(4.5, 4, 5.5), tree(4, 3.5, 5)]
        with self.assertRaisesRegex(ValueError, "t_f.*not identified"):
            calibrate.read_repeat(flat)

    def test_seeks_of_one_depth_do_not_identify_a_slope(self):
        trees = [dict(t, seekrandom=tree(0, 0, 5)["seekrandom"]) for t in TREES]
        with self.assertRaisesRegex(ValueError, "t_sk.*not identified"):
            calibrate.read_repeat(trees)

    def test_readrandom_must_read_the_found_block(self):
        trees = [dict(t, readrandom=t["readmissing"].replace(
            "readmissing", "readrandom")) for t in TREES]
        with self.assertRaisesRegex(ValueError, "t_blk"):
            calibrate.read_repeat(trees)

    def test_a_nonpositive_repeat_is_refused(self):
        with self.assertRaisesRegex(ValueError, "c_sk.*not positive"):
            calibrate.summarise({"c_sk": [2e-6, -1e-7, 2e-6]})

    def test_a_faster_deeper_tree_is_refused(self):
        # Deeper trees taking less time per seek: a negative slope.
        trees = [dict(t, seekrandom=tree(0, 0, r)["seekrandom"].replace(
            f"{(3e-6 + 2e-6 * r) * OPS:.6f} seconds",
            f"{(30e-6 - 2e-6 * r) * OPS:.6f} seconds"))
            for t, r in zip(TREES, (10, 6, 5))]
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
                                  "c_sk": 1e-9}, contract)

    def test_rejects_missing_core_price(self):
        contract = copy.deepcopy(CONTRACT)
        contract["prices"]["price_per_core_second"] = None
        with self.assertRaisesRegex(ValueError, "price"):
            calibrate.prices({"c_w": 1e-9, "c_f": 1e-9, "c_blk": 1e-9,
                              "c_sk": 1e-9}, contract)

    def test_rejects_missing_device_price(self):
        with self.assertRaisesRegex(ValueError, "c_sk"):
            research_objective.validate_prices(
                {"c_w": 1e-9, "c_f": 1e-9, "c_blk": 1e-9}, CONTRACT)

    def test_rejects_non_numeric_or_infinite_prices(self):
        for value in (True, float("inf"), float("nan"), "1e-9"):
            with self.assertRaisesRegex(ValueError, "c_w", msg=repr(value)):
                research_objective.validate_prices(
                    {"c_w": value, "c_f": 1e-9, "c_blk": 1e-9, "c_sk": 1e-9},
                    CONTRACT)

    def test_main_reads_the_layout_18_sh_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for ratio, trees in zip(calibrate.SIZE_RATIOS, TREES):
                for repeat in range(1, calibrate.REPEATS + 1):
                    for name, text in trees.items():
                        out = root / "work" / f"T{ratio}" / f"r{repeat}" / name
                        out.mkdir(parents=True)
                        (out / "stdout.txt").write_text(text)
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
        self.assertEqual(record["schema"], 2)
        self.assertRatio(record["c_w"], 3e-9 * CORE)
        self.assertRatio(record["c_f"], 0.5e-6 * CORE)
        self.assertRatio(record["c_blk"], 1e-6 * CORE)
        self.assertRatio(record["c_sk"], 2e-6 * CORE)
        self.assertEqual(len(record["core_seconds_spread"]["c_f"]["values"]),
                         calibrate.REPEATS)
        self.assertEqual(len(record["stdout_sha256"]), 45)
        self.assertEqual(record["load_commands"]["T6"], "db_bench T6")
        first = record["read_processes_per_operation"]["T2/r1/readmissing"]
        self.assertAlmostEqual(first["rocksdb.point.sst.probe"], 9)
        self.assertIn("rocksdb.no.file.opens", first)

    def test_missing_result_line_is_refused(self):
        trees = [dict(TREES[0], seekrandom="STATISTICS:\n")] + TREES[1:]
        with self.assertRaisesRegex(ValueError, "seekrandom"):
            calibrate.read_repeat(trees)


if __name__ == "__main__":
    unittest.main()
