"""03's fingerprint and 06's parser must stay in lockstep (CLAUDE.md gotcha).

The fingerprint line is taken from 03's own source and expanded by bash, so a
new segment added to one side without the other fails here.
"""
import importlib.util
import re
import subprocess
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "select_baseline_slo", PIPELINE / "06_select_baseline_slo.py")
select = importlib.util.module_from_spec(spec)
spec.loader.exec_module(select)

SAMPLE = {
    "WORKLOAD_PROFILE": "assoc-v1", "size_label": "10M", "ratio": "2",
    "KEY_SIZE": "16", "VALUE_SIZE": "960", "WRITE_BUFFER_SIZE": "2097152",
    "TARGET_FILE_SIZE": "2097152", "BLOCK_SIZE": "4096",
    "MAX_BYTES_FOR_LEVEL_BASE": "16777216", "NUM_LEVELS": "7",
    "effective_l0_compaction": "4", "effective_l0_slowdown": "20",
    "effective_l0_stop": "36", "effective_priority": "3",
    "LOAD_PERCENT": "29", "MIX_GET_RATIO": "0.806", "MIX_PUT_RATIO": "0.159",
    "MIX_SEEK_RATIO": "0.035", "SCAN_LENGTH": "0",
    "MIX_MAX_SCAN_LENGTH": "10000", "SKEW_FINGERPRINT": ":skew30-925.5",
    "BLOCK_CACHE_SIZE": "8388608", "BLOOM_BITS": "10",
    "MAX_BACKGROUND_JOBS": "2", "THREADS": "1", "DISABLE_WAL": "0",
    "USE_DIRECT_IO": "0", "MULTIPLIER_FINGERPRINT": "",
    "POWER_FINGERPRINT": "", "SETTLE_FINGERPRINT": "", "QBAR_FINGERPRINT": "",
    "PRICES_FINGERPRINT": "",
    "SOFT_PENDING_BYTES": "68719476736", "HARD_PENDING_BYTES": "274877906944",
    "DBBENCH_SHA256": "a" * 64, "RESEARCH_OBJECTIVE_SHA256": "b" * 64,
}


def fingerprint_from_03(**overrides):
    source = (PIPELINE / "03_run_experiments.sh").read_text()
    line = re.search(r'^\s*fingerprint="[^\n]*"$', source, re.M).group(0)
    values = {**SAMPLE, **overrides}
    script = "".join(f"{k}='{v}'\n" for k, v in values.items())
    script += line.strip() + '\nprintf %s "$fingerprint"\n'
    return subprocess.run(["bash", "-euc", script], check=True,
                          capture_output=True, text=True).stdout


class FingerprintTest(unittest.TestCase):
    def test_unscaled_run_parses_without_ltm(self):
        options = select.parse_fingerprint_options(fingerprint_from_03())
        self.assertEqual(options["level_target_multipliers"], "off")
        self.assertEqual(options["size_ratio"], 2)
        self.assertEqual(options["keyrange_num"], 30)

    def test_multipliers_reach_the_fingerprint_and_parse(self):
        fingerprint = fingerprint_from_03(
            MULTIPLIER_FINGERPRINT=":ltm1x1x1x1x1x2x1")
        options = select.parse_fingerprint_options(fingerprint)
        self.assertEqual(options["level_target_multipliers"], "1x1x1x1x1x2x1")

    def test_old_capacity_runs_still_parse(self):
        fingerprint = fingerprint_from_03().replace(":dio0:", ":dio0:cap1x1.5x1:")
        options = select.parse_fingerprint_options(fingerprint)
        self.assertEqual(options["static_capacity_scales"], "1x1.5x1")
        self.assertEqual(options["level_target_multipliers"], "off")

    def test_older_runs_parse_without_programme1_segments(self):
        options = select.parse_fingerprint_options(fingerprint_from_03())
        for name in ("key_dist_a", "key_dist_b", "settle_hold_seconds",
                     "reference_rate", "prices_sha256"):
            self.assertIsNone(options[name], name)

    def test_programme1_segments_reach_the_fingerprint_and_parse(self):
        fingerprint = fingerprint_from_03(
            SKEW_FINGERPRINT="", POWER_FINGERPRINT=":pow0.002312-0.3467",
            MULTIPLIER_FINGERPRINT=":ltm1x0.75x0.75",
            SETTLE_FINGERPRINT=":settle10", QBAR_FINGERPRINT=":qbar58332.0",
            PRICES_FINGERPRINT=":prices" + "c" * 64)
        options = select.parse_fingerprint_options(fingerprint)
        self.assertEqual((options["key_dist_a"], options["key_dist_b"]),
                         (0.002312, 0.3467))
        self.assertEqual(options["keyrange_num"], 1)
        self.assertEqual(options["level_target_multipliers"], "1x0.75x0.75")
        self.assertEqual(options["settle_hold_seconds"], 10)
        self.assertEqual(options["reference_rate"], 58332.0)
        self.assertEqual(options["prices_sha256"], "c" * 64)


if __name__ == "__main__":
    unittest.main()
