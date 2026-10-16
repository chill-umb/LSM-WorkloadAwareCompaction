"""33_suite_chain.sh end to end with the real 31, 03 and 04 and one stand-in
db_bench: it loads a tiny tree when asked to fill, and otherwise replays the
evaluator fixture as test_run_experiments' stub does. Checks the order (each
setting's tree, then each workload's repeats on it), the folders and
sessions, which trees keep their database, that a failed workload is logged
while the chain goes on (and its setting's tree is kept), that a resumed
chain skips finished settings, and the refusals before any tree (D-29 §6's
contract listing, and a run without a preflight)."""
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
FIXTURE = PIPELINE / "tests" / "fixtures" / "evaluator" / "1M" / "T2" / "native"

STUB = f"""#!/usr/bin/env bash
for a in "$@"; do
  case "$a" in
    --help) echo "    -rl_host_log_stride (stub)"; exit 1 ;;
    --rl_host_log=*) log="${{a#*=}}" ;;
    --db=*) db="${{a#*=}}" ;;
    --num=*) num="${{a#*=}}" ;;
    --benchmarks=*) bench="${{a#*=}}" ;;
    --mix_get_ratio=*) get="${{a#*=}}" ;;
  esac
done
echo "$bench $get" >> "$STUB_CALLS"
[[ "$bench" == compact ]] && exit 0
if [[ "$bench" == *filluniquerandom* && "$bench" != *mixgraph* ]]; then
  mkdir -p "$db"
  echo MANIFEST-000003 > "$db/CURRENT"
  printf 'manifest-%s' "$num" > "$db/MANIFEST-000003"
  head -c 4096 /dev/zero > "$db/000009.sst"
  echo 'info log' > "$db/LOG"
  cat > "$log" <<JSON
{{"type":"header","schema":3,"num_levels":4,"fg":[],"stride":0}}
{{"type":"stamp","name":"settle","op":$num,"h":4096,"stall_micros":0,"ok":1,"wait_micros":5,"hold_micros":10000000}}
JSON
  echo "RL_SETTLED ok=1 wait_micros=5 hold_micros=10000000"
  exit 0
fi
if [[ -n "${{STUB_FAIL_GET:-}}" && "$get" == "$STUB_FAIL_GET" ]]; then
  echo "IO error: stub failure"
  exit 1
fi
mkdir -p "$db"
cp {shlex.quote(str(FIXTURE / "rocksdb_LOG.txt"))} "$db/LOG"
cp {shlex.quote(str(FIXTURE / "host_log.jsonl"))} "$log"
cat {shlex.quote(str(FIXTURE / "run.log"))}
"""


def fs_type(path: Path) -> str:
    return subprocess.run(["stat", "-f", "-c", "%T", str(path)],
                          capture_output=True, text=True).stdout.strip()


class SuiteChainTest(unittest.TestCase):
    def setUp(self):
        if fs_type(Path.home()) == "tmpfs":
            self.skipTest("no non-tmpfs directory for the results root")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        nvme = tempfile.TemporaryDirectory(dir=Path.home(), prefix=".suite-chain-")
        self.addCleanup(nvme.cleanup)
        self.nvme = Path(nvme.name).resolve()
        build = self.root / "build"
        build.mkdir()
        (build / "db_bench").write_text(STUB)
        (build / "db_bench").chmod(0o755)
        venv = self.root / "venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python").symlink_to(sys.executable)
        self.calls = self.root / "calls.txt"
        self.env = {
            **os.environ, "NVME": str(self.nvme), "ALLOW_ROOT_DISK": "1",
            "MIN_FREE_GB": "1", "SUITE_MIN_FREE_GB": "1",
            "DBBENCH_BUILD_DIR": str(build), "PYTHON_VENV": str(self.root / "venv"),
            "STUB_CALLS": str(self.calls), "DBBENCH_CPUS": "", "CONTROLLER_CPUS": "",
            "NUM_LEVELS": "4", "SUITE_T": "2 3", "SUITE_SETTINGS": "16:2 16:4",
            "SUITE_WORKLOADS": "W1 W2", "SUITE_REPEATS": "2",
            "SUITE_KEEP_TREES": "16:4", "SUITE_PRELOAD_KEYS": "1000",
            "SUITE_RUN_OPERATIONS_M": "1", "SUITE_WRITE_BUFFER_SIZE": "2097152",
            "SUITE_TARGET_FILE_SIZE": "524288", "SUITE_OPEN_FILES": "1000",
            "SUITE_MEMORY_MAX": "", "PRICES_FILE": str(self.root / "none.json"),
            "ALLOW_CONCURRENT_RUNS": "1"}

    def chain(self, *args, **overrides):
        return subprocess.run(
            ["bash", str(PIPELINE / "33_suite_chain.sh"), *args],
            env={**self.env, **overrides}, capture_output=True, text=True)

    def tree(self, setting):
        return self.nvme / "suite-trees" / f"{setting}-n1000-s1000"

    def test_plan(self):
        planned = self.chain("plan")
        self.assertEqual(planned.returncode, 0, planned.stderr)
        self.assertIn("# 4 trees, 16 runs", planned.stdout)
        self.assertIn("tree T2-l116-k4", planned.stdout)
        self.assertFalse(self.calls.exists())

    def test_the_sweep(self):
        ran = self.chain()
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertEqual(ran.stdout.strip().splitlines()[-1], "SUITE_CHAIN_EXIT=0")
        calls = self.calls.read_text().splitlines()
        # Per setting: its tree, then W1's two repeats (Assoc's 0.806), then
        # W2's (0.95), each run followed by its space measurement.
        fills = [i for i, c in enumerate(calls) if "filluniquerandom" in c]
        self.assertEqual(len(fills), 4)
        first = calls[fills[0] + 1:fills[1]]
        self.assertEqual([c.split()[-1] for c in first if "mixgraph" in c],
                         ["0.806", "0.806", "0.95", "0.95"])
        for w in ("W1", "W2"):
            for setting in ("T2-l116-k2", "T2-l116-k4", "T3-l116-k2", "T3-l116-k4"):
                cell = self.nvme / f"suite-{w}" / setting / "1M"
                done = list(cell.glob("T*/repeat-*/native/COMPLETED"))
                self.assertEqual(len(done), 2, (w, setting))
                metadata = (done[0].parent / "metadata.env").read_text()
                self.assertIn(f"session_id=suite-{w}", metadata)
                self.assertIn(f"tree_dir={self.tree(setting)}", metadata)
            self.assertTrue((self.nvme / f"suite-{w}" / "graphs" / "summary.csv").exists())
        # Only the kept setting's trees keep their database.
        for setting, kept in (("T2-l116-k2", False), ("T2-l116-k4", True)):
            self.assertEqual((self.tree(setting) / "db").exists(), kept)
            self.assertTrue((self.tree(setting) / "tree.json").exists())
        # A resumed chain skips the finished settings, trees and all.
        before = len(self.calls.read_text().splitlines())
        again = self.chain(RESUME="1")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(again.stdout.count("every run completed; skipped"), 4)
        self.assertEqual(len(self.calls.read_text().splitlines()), before)

    def test_a_failed_workload_is_logged_and_the_chain_goes_on(self):
        ran = self.chain(STUB_FAIL_GET="0.95", SUITE_T="2")
        self.assertEqual(ran.returncode, 5, ran.stdout + ran.stderr)
        self.assertIn("W2 FAILED", ran.stdout)
        for setting in ("T2-l116-k2", "T2-l116-k4"):
            cell = self.nvme / "suite-W1" / setting
            self.assertEqual(len(list(cell.glob("**/COMPLETED"))), 2)
            # Kept, so a rerun resumes W2 on the same tree.
            self.assertTrue((self.tree(setting) / "db").exists())

    def test_refusals_before_any_tree(self):
        # D-29 §6: the contract does not list the suite workloads yet.
        ran = self.chain(SUITE_RUN_OPERATIONS_M="40")
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("D-29 §6", ran.stderr)
        self.assertFalse(self.calls.exists())
        ran = self.chain(SUITE_WORKLOADS="W1 W9")
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("unknown workload W9", ran.stderr)

    def test_no_preflight_stops_the_chain(self):
        ran = self.chain(SUITE_RUN_OPERATIONS_M="40", DIAGNOSTIC_RUN="1",
                         SUITE_T="2", SUITE_SETTINGS="16:2",
                         PREFLIGHT_MARKER=str(self.root / "none"))
        self.assertNotEqual(ran.returncode, 0)
        self.assertNotEqual(ran.returncode, 5)
        self.assertIn("no matching preflight", ran.stderr)


if __name__ == "__main__":
    unittest.main()
