"""2026-11-22: RocksDB's options.ttl is off by default (config.sh DBBENCH_TTL_SECONDS).

Past ttl/2 of a preloaded tree's age, RocksDB cuts merge outputs around old files and boosts them in the pick; the
11-04 trees crossed 15 days on 2026-11-19 and every run after merged differently (~/node_ops/diag/2026-11-22-ck4).
Every db_bench command built from dbbench_shared_flags gets --ttl_seconds=0 unless the setting says otherwise, and 03
refuses a value that is not seconds.
"""
import os
import subprocess
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
ROOT = PIPELINE.parents[1]


def shared_flags(**env):
    script = ('source "$1/config.sh" >/dev/null 2>&1; dbbench_shared_flags; '
              'printf "%s\\n" "${DBBENCH_COMMON[@]}"')
    base = {k: v for k, v in os.environ.items() if k != "DBBENCH_TTL_SECONDS"}
    base.update(PROJECT_ROOT=str(ROOT), **env)
    out = subprocess.run(["bash", "-c", script, "flags", str(PIPELINE)], env=base, check=True,
                         capture_output=True, text=True).stdout
    return [x for x in out.splitlines() if x.startswith("--ttl_seconds")]


class TtlFlagTest(unittest.TestCase):
    def test_off_by_default(self):
        self.assertEqual(shared_flags(), ["--ttl_seconds=0"])

    def test_empty_keeps_rocksdbs_default(self):
        self.assertEqual(shared_flags(DBBENCH_TTL_SECONDS=""), [])

    def test_a_value_is_passed(self):
        self.assertEqual(shared_flags(DBBENCH_TTL_SECONDS="86400"), ["--ttl_seconds=86400"])

    def test_03_refuses_a_value_that_is_not_seconds(self):
        source = (PIPELINE / "03_run_experiments.sh").read_text()
        start = source.index("# options.ttl (config.sh DBBENCH_TTL_SECONDS")
        block = source[start:source.index('TTL_FINGERPRINT=', start)]
        for value, code in (("0", 0), ("", 0), ("2592000", 0), ("1d", 1), ("-5", 1)):
            r = subprocess.run(["bash", "-c", f"DBBENCH_TTL_SECONDS='{value}'\n{block}"], capture_output=True, text=True)
            self.assertEqual(r.returncode, code, value)


if __name__ == "__main__":
    unittest.main()
