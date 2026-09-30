"""18_calibrate_prices.py: device seconds per unit from db_bench output,
money prices, and the OBJ-2 refusals (c_s <= 0, missing money prices)."""
import copy
import importlib.util
import unittest
from pathlib import Path

import research_objective

PIPELINE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "calibrate_prices", PIPELINE / "18_calibrate_prices.py")
calibrate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calibrate)
CONTRACT, _ = research_objective.load_contract()
INSTANCE = CONTRACT["prices"]["instance_price_per_device_second"]


def stdout(name, seconds, **tickers):
    """A db_bench process's output: the result line (format of db_bench
    11.1.1) and its STATISTICS block."""
    return (f"{name:<12} :       6.382 micros/op 156519 ops/sec {seconds} "
            "seconds 5000 operations;\nSTATISTICS:\n" +
            "".join(f"rocksdb.{k.replace('_', '.')} COUNT : {v}\n"
                    for k, v in tickers.items()))


RUNS = {
    "compact": stdout("compact", 2.0, compact_write_bytes=900_000_000,
                      flush_write_bytes=100_000_000),
    "readmissing": stdout("readmissing", 1.0, point_sst_probe=2_000_000),
    # 1.5 s: 3,000,000 probes at 0.5 us, then 1,000,000 block reads at 1 us.
    "readrandom": stdout("readrandom", 2.5, point_sst_probe=3_000_000,
                         bloom_filter_full_positive=1_000_000),
    "seekrandom": stdout("seekrandom", 4.0, sorted_run_seek=2_000_000),
}


class PricesTest(unittest.TestCase):
    def assertRatio(self, value, expected, msg=None):
        # Relative: these are 1e-9 s and 1e-13 USD, far below the default
        # absolute tolerance.
        self.assertAlmostEqual(value / expected, 1.0, places=9, msg=msg)

    def test_device_seconds(self):
        seconds = calibrate.device_seconds(RUNS)
        # t_w counts flush bytes too: 2 s over 1e9 bytes, not over 9e8.
        expected = {"c_w": 2e-9, "c_f": 5e-7, "c_blk": 1e-6, "c_sk": 2e-6}
        for key, value in expected.items():
            self.assertRatio(seconds[key], value, key)

    def test_money_prices(self):
        seconds = calibrate.device_seconds(RUNS)
        money = calibrate.prices(seconds, CONTRACT)
        for key in ("c_w", "c_f", "c_blk", "c_sk"):
            self.assertRatio(money[key], seconds[key] * INSTANCE, key)
        self.assertEqual(money["c_s"],
                         CONTRACT["prices"]["storage_price_per_byte_second"])

    def test_rejects_nonpositive_storage_price(self):
        for value in (0, -1e-17, None):
            contract = copy.deepcopy(CONTRACT)
            contract["prices"]["storage_price_per_byte_second"] = value
            with self.assertRaisesRegex(ValueError, "c_s"):
                calibrate.prices(calibrate.device_seconds(RUNS), contract)

    def test_rejects_missing_instance_price(self):
        contract = copy.deepcopy(CONTRACT)
        contract["prices"]["instance_price_per_device_second"] = None
        with self.assertRaisesRegex(ValueError, "price"):
            calibrate.prices(calibrate.device_seconds(RUNS), contract)

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

    def test_a_block_time_swallowed_by_probes_is_refused(self):
        runs = dict(RUNS, readrandom=stdout(
            "readrandom", 1.0, point_sst_probe=3_000_000,
            bloom_filter_full_positive=1_000_000))
        with self.assertRaisesRegex(ValueError, "t_blk"):
            calibrate.device_seconds(runs)

    def test_missing_result_line_is_refused(self):
        with self.assertRaisesRegex(ValueError, "seekrandom"):
            calibrate.device_seconds(dict(RUNS, seekrandom="STATISTICS:\n"))


if __name__ == "__main__":
    unittest.main()
