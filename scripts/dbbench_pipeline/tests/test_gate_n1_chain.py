"""24_gate_n1_chain.sh end to end on a copy of the pipeline, with a stand-in
db_bench (tests/fixtures/chain/fake_db_bench.py) and a stand-in preflight
that writes the marker as 13 does. Every other stage is the real one: 03 and
its marker and load checks, 04, 19 with the committed admission config, the
run-length choice, the q-bar arms and one session of 18 on 29's archive
(PREREGISTRATION D-22; none without PRICE_TREES). Checks the whole night, one
workload failing while the other finishes, STOP_AFTER_N1, and the checks
that fail before the preflight starts."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import preflight_marker

PIPELINE = Path(__file__).resolve().parents[1]
REPO = PIPELINE.parents[1]
FAKE = PIPELINE / "tests" / "fixtures" / "chain" / "fake_db_bench.py"
STUB_PREFLIGHT = """#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/../.."
echo "stub preflight PARITY_PAIRS=$PARITY_PAIRS"
"$PYTHON_VENV/bin/python" scripts/dbbench_pipeline/preflight_marker.py write \\
  --marker build-dbbench/PREFLIGHT_PASSED --db-bench build-dbbench/db_bench \\
  --passed 1 2 3 4 --skipped "5=no plugin" "6=no plugin"
"""


def executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


def fs_type(path: Path) -> str:
    return subprocess.run(["stat", "-f", "-c", "%T", str(path)],
                          capture_output=True, text=True).stdout.strip()


class ChainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # 24 refuses a tmpfs results root, and /tmp often is one.
        if fs_type(Path.home()) == "tmpfs":
            self.skipTest("no non-tmpfs directory for the results root")
        self.nvme_tmp = tempfile.TemporaryDirectory(dir=Path.home(),
                                                    prefix=".chain-test-")
        self.addCleanup(self.nvme_tmp.cleanup)
        self.nvme = Path(self.nvme_tmp.name)
        self.root = Path(self.tmp.name) / "repo"
        shutil.copytree(PIPELINE, self.root / "scripts" / "dbbench_pipeline",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(REPO / "config", self.root / "config")
        shutil.copy(REPO / ".gitignore", self.root / ".gitignore")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        executable(self.root / "scripts" / "dbbench_pipeline" /
                   "13_run_preflight_verification.sh", STUB_PREFLIGHT)
        executable(self.root / "build-dbbench" / "db_bench",
                   f'#!/usr/bin/env bash\nexec "{sys.executable}" "{FAKE}" "$@"\n')
        self.venv = Path(self.tmp.name) / "venv"
        executable(self.venv / "bin" / "python",
                   f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n')

    # Variables of 24 and config.sh that would point the copy at real paths
    # or change its behaviour if exported where the test runs.
    SCRUBBED = ("RESUME", "STOP_AFTER_N1", "PARITY_PAIRS", "ALLOW_ROOT_DISK",
                "FAKE_FAIL_WORKLOAD", "DBBENCH_BUILD_DIR", "PREFLIGHT_MARKER",
                "PREFLIGHT_WORK_DIR", "PRICES_FILE", "DB_ROOT", "RESULTS_ROOT",
                "SESSION_ID", "KEEP_DATABASES", "FAKE_SURVIVAL_BASES",
                "QBAR_ONLY", "QBAR_TAG", "PRICE_TREES", "PRICE_TREES_SHA256",
                "PRICE_SESSION", "PRICE_SESSIONS_DIR", "DIAGNOSTIC_RUN",
                "FAKE_UNSETTLED_ONCE", "FAKE_COMPACT_ON_READ", "BUILD_ATTEMPTS",
                "MIN_FREE_GB_BUILD", "MIN_FREE_GB_ARCHIVE", "MIN_FREE_GB_SESSION")

    def run24(self, cwd=None, script=None, **overrides) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in self.SCRUBBED}
        env.update({"NVME": str(self.nvme), "MIN_FREE_GB": "0",
                    "MIN_FREE_GB_SESSION": "0",
                    "DBBENCH_CPUS": "", "CONTROLLER_CPUS": "",
                    "PYTHON_VENV": str(self.venv), "ALLOW_CONCURRENT_RUNS": "1",
                    "ALLOW_ROOT_DISK": "1", **overrides})
        script = script or str(self.root / "scripts" / "dbbench_pipeline" /
                               "24_gate_n1_chain.sh")
        return subprocess.run(["bash", script], cwd=cwd, env=env,
                              capture_output=True, text=True, timeout=600)

    def admission(self, workload, ratio):
        return json.loads((self.nvme / f"n1-{workload}" /
                           f"admission_T{ratio}.json").read_text())

    def test_the_whole_night(self):
        ran = self.run24()
        self.assertEqual(ran.returncode, 0, ran.stdout[-3000:] + ran.stderr[-3000:])
        self.assertIn("stub preflight PARITY_PAIRS=10", ran.stdout)
        for workload in ("assoc", "powerlaw"):
            for ratio in (2, 6, 10):
                report = self.admission(workload, ratio)
                self.assertEqual(list(report["levels"]), ["2"])
                self.assertEqual(report["settled_depth"], 4)
        # 20 turnovers of L2 in 26.1M operations need 13.05M: (29, 10).
        # The power law's 6 need 43.5M: (58, 5).
        self.assertTrue((self.nvme / "qbar-assoc" / "29M" / "T10").is_dir())
        self.assertTrue((self.nvme / "qbar-powerlaw" / "58M" / "T10").is_dir())
        self.assertEqual(ran.stdout.count("q-bar candidate"), 2)
        # D-22: no archive named, so no price session and no prices.
        self.assertIn("no price session: PRICE_TREES is not set", ran.stdout)
        self.assertFalse((self.root / "build-dbbench" / "prices.json").exists())
        self.assertFalse((self.root / "build-dbbench" / "price-sessions").exists())

    def build_price_trees(self) -> tuple[Path, str]:
        """29's archive, built by the stand-in db_bench (D-22 a, b)."""
        archive = Path(self.tmp.name) / "price-trees"
        env = {k: v for k, v in os.environ.items() if k not in self.SCRUBBED}
        env.update({"DBBENCH_CPUS": "", "CONTROLLER_CPUS": "",
                    "PYTHON_VENV": str(self.venv), "CONFIRM_PRICE_TREES": "YES",
                    "PRICE_TREES": str(archive), "MIN_FREE_GB_BUILD": "0",
                    "MIN_FREE_GB_ARCHIVE": "0",
                    "DB_ROOT": str(self.nvme / "price-build-db")})
        built = subprocess.run(
            ["bash", str(self.root / "scripts" / "dbbench_pipeline" /
                         "29_build_price_trees.sh")],
            cwd=self.root, env=env, capture_output=True, text=True, timeout=600)
        self.assertEqual(built.returncode, 0, built.stderr[-3000:])
        return archive, built.stdout.split("tree-set identity ")[1].split()[0]

    def test_a_failing_workload_leaves_the_other_to_finish(self):
        for failing, other in (("powerlaw", "assoc"), ("assoc", "powerlaw")):
            with self.subTest(failing=failing):
                nvme = self.nvme / f"fail-{failing}"
                nvme.mkdir()
                ran = self.run24(NVME=str(nvme), FAKE_FAIL_WORKLOAD=failing)
                self.assertEqual(ran.returncode, 1)
                self.assertIn(f"{failing} FAILED", ran.stdout)
                self.assertIn("stopping before the prices", ran.stderr)
                self.assertTrue((nvme / f"qbar-{other}" / "graphs" /
                                 "summary.csv").exists())
                self.assertFalse((nvme / f"qbar-{failing}").exists())
                self.assertFalse((self.root / "build-dbbench" / "prices.json").exists())

    def test_stop_after_gate_n1_started_from_another_folder(self):
        # The workload chains are 24 re-run by path after it cds to the repo
        # root, so a relative start from elsewhere must still find it.
        ran = self.run24(cwd=self.root.parent,
                         script="repo/scripts/dbbench_pipeline/24_gate_n1_chain.sh",
                         STOP_AFTER_N1="1")
        self.assertEqual(ran.returncode, 0, ran.stderr[-3000:])
        self.assertIn("stopped after Gate N1", ran.stdout)
        self.assertEqual(self.admission("powerlaw", 10)["run_length"]["rung"],
                         {"size_millions": 58, "load_percent": 5})
        self.assertFalse((self.nvme / "qbar-assoc").exists())

    def test_qbar_only_reruns_the_qbar_arms_and_the_prices(self):
        # D-21: after a binary change, Gate N1's reports are reused and only
        # the q-bar arms and the prices run again, into new folders.
        ran = self.run24(STOP_AFTER_N1="1")
        self.assertEqual(ran.returncode, 0, ran.stderr[-3000:])
        pilots = self.nvme / "n1-assoc" / "graphs" / "summary.csv"
        before = pilots.stat().st_mtime_ns
        archive, identity = self.build_price_trees()
        ran = self.run24(QBAR_ONLY="1", QBAR_TAG="d21", PRICE_TREES=str(archive),
                         PRICE_TREES_SHA256=identity)
        self.assertEqual(ran.returncode, 0, ran.stdout[-3000:] + ran.stderr[-3000:])
        self.assertIn("stub preflight", ran.stdout)
        self.assertNotIn("Gate N1 pilots", ran.stdout)
        self.assertEqual(pilots.stat().st_mtime_ns, before)
        self.assertTrue((self.nvme / "qbar-assoc-d21" / "29M" / "T10").is_dir())
        self.assertTrue((self.nvme / "qbar-powerlaw-d21" / "58M" / "T10").is_dir())
        self.assertFalse((self.nvme / "qbar-assoc").exists())
        # q-bar is not offered for recording again: it stays as recorded.
        self.assertNotIn("q-bar candidate", ran.stdout)
        self.assertEqual(ran.stdout.count("q-bar stays as recorded"), 2)
        # One session of D-22 (c), named after the tag; no prices file until
        # a second session and 18's compare (D-22 f).
        self.assertFalse((self.root / "build-dbbench" / "prices.json").exists())
        prices = json.loads((self.root / "build-dbbench" / "price-sessions" /
                             "qbar-d21" / "session.json").read_text())
        self.assertEqual((prices["schema"], prices["kind"]), (5, "session"))
        self.assertEqual(prices["tree_set_sha256"], identity)
        self.assertEqual(prices["open_files"], {"capped": 1000, "all_open": -1})
        # D-21: the fake times every reopen at 8 us, in the price runs and in
        # the q-bar arms alike, so every q-bar arm's check holds.
        self.assertAlmostEqual(prices["reopen_timer"]["seconds_per_reopen"]
                               / 8e-6, 1.0, places=6)
        for family in ("assoc", "powerlaw_get95"):
            self.assertEqual({r["check"] for r in
                              prices["qbar_reopen_checks"][family]}, {"held"})
        for key, seconds in (("c_f", 0.5e-6), ("c_blk", 1e-6), ("c_sk", 2e-6),
                             ("c_open", 10e-6)):
            self.assertAlmostEqual(prices["core_seconds_per_unit"][key] / seconds,
                                   1.0, places=6, msg=key)
        # 18 and 03 record one identity: db_bench as loaded.
        self.assertEqual(prices["db_bench_sha256"], preflight_marker.db_bench_identity(
            self.root / "build-dbbench" / "db_bench"))
        self.assertEqual(len(prices["write_summaries_sha256"]), 2)
        self.assertTrue(all("-d21" in path for path in prices["write_summaries_sha256"]))
        self.assertIn("reopen check, assoc q-bar arms", ran.stdout)

    def test_qbar_only_refusals(self):
        cases = [({"QBAR_ONLY": "1"}, "needs QBAR_TAG"),
                 ({"QBAR_ONLY": "1", "QBAR_TAG": "a/b"}, "needs QBAR_TAG"),
                 ({"QBAR_ONLY": "2"}, "QBAR_ONLY must be 0 or 1"),
                 ({"QBAR_ONLY": "1", "QBAR_TAG": "d21"}, "no Gate N1 run length")]
        for overrides, message in cases:
            with self.subTest(message=message):
                ran = self.run24(**overrides)
                self.assertEqual(ran.returncode, 1)
                self.assertIn(message, ran.stderr)
                self.assertNotIn("stub preflight", ran.stdout)
        (self.nvme / "qbar-assoc-d21").mkdir()
        ran = self.run24(QBAR_ONLY="1", QBAR_TAG="d21")
        self.assertEqual(ran.returncode, 1)
        self.assertIn("qbar-assoc-d21 exists", ran.stderr)
        # D-22 (i): a price session only on the recorded archive.
        archive, identity = self.build_price_trees()
        for overrides, message in (
                ({"PRICE_TREES": str(archive), "PRICE_TREES_SHA256": "0" * 64},
                 "not the archive's tree-set identity"),
                ({"PRICE_TREES": str(self.nvme)}, "is not 29's archive")):
            with self.subTest(message=message):
                ran = self.run24(QBAR_ONLY="1", QBAR_TAG="d22", **overrides)
                self.assertEqual(ran.returncode, 1)
                self.assertIn(message, ran.stderr)
                self.assertNotIn("stub preflight", ran.stdout)

    def test_checks_fail_before_the_preflight(self):
        leftovers = {"n1-assoc": self.nvme / "a", "n1-dbs": self.nvme / "b"}
        for name, nvme in leftovers.items():
            (nvme / name).mkdir(parents=True)
        cases = [({"NVME": str(leftovers["n1-assoc"])}, "n1-assoc exists"),
                 ({"NVME": str(leftovers["n1-dbs"])}, "n1-dbs exists"),
                 ({"MIN_FREE_GB": "999999"}, "GB free"),
                 ({"PYTHON_VENV": str(Path(self.tmp.name) / "none")}, "no Python")]
        if os.stat(self.nvme).st_dev == os.stat("/").st_dev:
            cases.append(({"ALLOW_ROOT_DISK": "0"}, "on the root filesystem"))
        for overrides, message in cases:
            with self.subTest(message=message):
                ran = self.run24(**overrides)
                self.assertEqual(ran.returncode, 1)
                self.assertIn(message, ran.stderr)
                self.assertNotIn("stub preflight", ran.stdout)


if __name__ == "__main__":
    unittest.main()
