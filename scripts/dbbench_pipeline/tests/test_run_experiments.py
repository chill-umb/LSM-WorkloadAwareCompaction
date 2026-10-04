"""03 -> 04 wiring for the Programme 1 arms, with a stub db_bench that
replays the evaluator fixture: the settle step sits between the load and
rlresume, every arm writes the host log, each arm gets its own multiplier
vector, the fingerprint parses in fingerprint.py, 04 scores every arm, an
unsettled arm is reported without stopping the matrix, and 03 refuses what
RocksDB or the preregistration would refuse later."""
import csv
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

import fingerprint as select
import preflight_marker
import research_objective

PIPELINE = Path(__file__).resolve().parents[1]
FIXTURE = PIPELINE / "tests" / "fixtures" / "evaluator" / "1M" / "T2" / "native"
CONTRACT, _ = research_objective.load_contract()

# STUB_UNSETTLED=1 makes the stub fail its settle hold, as db_bench does.
STUB = f"""#!/usr/bin/env bash
for a in "$@"; do
  case "$a" in
    --help)
      # STUB_OLD=1: a binary before the interim instruments (D-23 §3(a)).
      [[ "${{STUB_OLD:-0}}" == 1 ]] || echo "    -rl_host_log_stride (stub)"
      exit 1 ;;
    --rl_host_log=*) log="${{a#*=}}" ;;
    --db=*) db="${{a#*=}}" ;;
    --benchmarks=*) bench="${{a#*=}}" ;;
  esac
done
[[ "$bench" == compact ]] && exit 0
mkdir -p "$db"
cp {shlex.quote(str(FIXTURE / "rocksdb_LOG.txt"))} "$db/LOG"
cp {shlex.quote(str(FIXTURE / "host_log.jsonl"))} "$log"
if [[ "${{STUB_UNSETTLED:-0}}" == 1 ]]; then
  echo "RL_SETTLED ok=0 wait_micros=1 hold_micros=0 reason=${{STUB_REASON:-compaction pending}}"
  exit 1
fi
cat {shlex.quote(str(FIXTURE / "run.log"))}
"""


def run03(root: Path, **overrides) -> subprocess.CompletedProcess:
    build = root / "build"
    if not build.exists():
        build.mkdir(parents=True)
        (build / "db_bench").write_text(STUB)
        (build / "db_bench").chmod(0o755)
    env = {**os.environ, "CONFIRM_EXPERIMENTS": "YES",
           "DBBENCH_BUILD_DIR": str(build), "EXPERIMENT_ARMS": "native",
           "NUM_LEVELS": "4", "WORKLOAD_SIZES_M": "1", "SIZE_RATIOS": "2",
           "REPEATS": "1", "RESULTS_ROOT": str(root / "results"),
           "DB_ROOT": str(root / "db"), "DBBENCH_CPUS": "",
           "CONTROLLER_CPUS": "", "PYTHON_VENV": str(root / "novenv"),
           "PRICES_FILE": str(FIXTURE / "prices.json"),
           "SESSION_ID": "test-session", "ALLOW_CONCURRENT_RUNS": "1",
           **overrides}
    return subprocess.run(["bash", str(PIPELINE / "03_run_experiments.sh")],
                          env=env, capture_output=True, text=True)


def run04(results: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(PIPELINE / "04_generate_graphs.py"), "--results",
         str(results), "--summary-only"], capture_output=True, text=True)


def summary(results: Path) -> dict[str, dict]:
    with (results / "graphs" / "summary.csv").open() as handle:
        return {r["arm"]: r for r in csv.DictReader(handle)}


class RunExperimentsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.ran = run03(root, EXPERIMENT_ARMS="native static:uniform_0_75 "
                        "static:held", STATIC_PROFILE_held="1:1:2:1")
        cls.cell = root / "results" / "1M" / "T2"
        cls.db_bench = root / "build" / "db_bench"
        cls.scored = run04(root / "results")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def command(self, arm):
        return (self.cell / arm / "command.txt").read_text()

    def metadata(self, arm):
        return dict(line.split("=", 1) for line in
                    (self.cell / arm / "metadata.env").read_text().splitlines())

    def test_03_and_04_succeed(self):
        self.assertEqual(self.ran.returncode, 0, self.ran.stderr)
        self.assertEqual(self.scored.returncode, 0, self.scored.stderr)

    def test_settle_between_the_load_and_rlresume_with_the_host_log(self):
        for arm in ("native", "static:uniform_0_75", "static:held"):
            command = self.command(arm)
            self.assertIn("filluniquerandom\\,settle\\,resetstats\\,rlresume"
                          "\\,mixgraph", command)
            self.assertIn(f"--rl_host_log={self.cell / arm}/host_log.jsonl",
                          command)
            self.assertIn("--rl_settle_hold_seconds=10", command)
            self.assertIn("--compaction_style=0", command)
            self.assertEqual(self.metadata(arm)["settle_hold_seconds"], "10")
            # D-23 §3(a): counter snapshots every n_str operations, and the
            # instrument subset in the run manifest (D-24 §2).
            self.assertIn("--rl_host_log_stride=100", command)
            self.assertEqual(self.metadata(arm)["rl_host_log_stride"], "100")
            self.assertEqual(self.metadata(arm)["instrument_subset"],
                             "interim-1")

    def test_a_binary_without_snapshots_needs_stride_0(self):
        # D-23 §3(a): a binary before the interim instruments cannot write
        # counter snapshots; 03 refuses a positive stride on it, and runs it
        # with stride 0, recording the subset it carries.
        with tempfile.TemporaryDirectory() as tmp:
            refused = run03(Path(tmp), STUB_OLD="1")
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("RL_HOST_LOG_STRIDE=0", refused.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            ran = run03(Path(tmp), STUB_OLD="1", RL_HOST_LOG_STRIDE="0")
            self.assertEqual(ran.returncode, 0, ran.stderr)
            arm = Path(tmp) / "results" / "1M" / "T2" / "native"
            self.assertNotIn("rl_host_log_stride",
                             (arm / "command.txt").read_text())
            self.assertIn("instrument_subset=d21",
                          (arm / "metadata.env").read_text())

    def test_each_arm_gets_its_own_multipliers(self):
        self.assertNotIn("level_target_multipliers", self.command("native"))
        self.assertIn("--level_target_multipliers=1:0.75:0.75:0.75",
                      self.command("static:uniform_0_75"))
        self.assertIn("--level_target_multipliers=1:1:2:1",
                      self.command("static:held"))

    def test_fingerprints_parse_in_06(self):
        native = select.parse_fingerprint_options(
            self.metadata("native")["experiment_fingerprint"])
        held = select.parse_fingerprint_options(
            self.metadata("static:held")["experiment_fingerprint"])
        self.assertEqual(native["level_target_multipliers"], "off")
        self.assertEqual(held["level_target_multipliers"], "1x1x2x1")
        self.assertEqual(native["settle_hold_seconds"], 10)
        self.assertEqual(len(native["prices_sha256"]), 64)
        # The binary is db_bench's identity as loaded, the value the marker,
        # 18 and gate_n2_plan compute (preflight_marker.db_bench_identity).
        identity = preflight_marker.db_bench_identity(self.db_bench)
        self.assertEqual(native["dbbench_sha256"], identity)
        self.assertEqual(self.metadata("native")["dbbench_sha256"], identity)
        # q-bar as the contract records it now (null until the amendment).
        self.assertEqual(native["reference_rate"],
                         research_objective.reference_rate(CONTRACT, "assoc"))

    def test_prices_are_copied_and_04_scores_every_arm(self):
        self.assertTrue((self.cell / "native" / "prices.json").exists())
        # The fixture's prices are final (D-22 f); the run is not diagnostic.
        self.assertEqual(self.metadata("native")["prices_status"], "final")
        self.assertEqual(self.metadata("native")["diagnostic_run"], "0")
        rows = summary(self.cell.parents[1])
        self.assertEqual(set(rows), {"native", "static:uniform_0_75",
                                     "static:held"})
        priced = research_objective.reference_rate(CONTRACT, "assoc") is not None
        for row in rows.values():
            self.assertEqual(row["session_id"], "test-session")
            self.assertEqual(row["settle_ok"], "1")
            self.assertEqual(float(row["held_byte_operations"]), 8_450_000)
            self.assertEqual(row["objective_status"],
                             "priced" if priced else "no reference rate")


class PowerLawTest(unittest.TestCase):
    def test_power_law_family(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ran = run03(root, WORKLOAD_SKEW="2", WORKLOAD_PROFILE="pow-test",
                        MIX_GET_RATIO="0.95", MIX_PUT_RATIO="0.05",
                        MIX_SEEK_RATIO="0")
            self.assertEqual(ran.returncode, 0, ran.stderr)
            cell = root / "results" / "1M" / "T2" / "native"
            command = (cell / "command.txt").read_text()
            for flag in ("--keyrange_num=1", "--key_dist_a=0.002312",
                         "--key_dist_b=0.3467"):
                self.assertIn(flag, command)
            self.assertNotIn("keyrange_dist", command)
            metadata = dict(line.split("=", 1) for line in
                            (cell / "metadata.env").read_text().splitlines())
            options = select.parse_fingerprint_options(
                metadata["experiment_fingerprint"])
            self.assertEqual((options["key_dist_a"], options["keyrange_num"]),
                             (0.002312, 1))
            self.assertEqual(metadata["workload_family"], "powerlaw_get95")
            self.assertEqual(run04(root / "results").returncode, 0)


class UnsettledTest(unittest.TestCase):
    def test_reported_and_the_matrix_goes_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ran = run03(root, EXPERIMENT_ARMS="native static:uniform_0_75",
                        STUB_UNSETTLED="1")
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertIn("[unsettled]", ran.stderr)
            cell = root / "results" / "1M" / "T2"
            for arm in ("native", "static:uniform_0_75"):
                self.assertTrue((cell / arm / "UNSETTLED").exists(), arm)
            self.assertIn("unsettled", (root / "results" / "arms.tsv").read_text())
            scored = run04(root / "results")
            self.assertNotEqual(scored.returncode, 0)
            self.assertIn("did not settle", scored.stderr)

    def test_l0_above_its_trigger_is_unsettled_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ran = run03(root, STUB_UNSETTLED="1",
                        STUB_REASON="L0 files 5 >= trigger 4")
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertTrue((root / "results" / "1M" / "T2" / "native" /
                             "UNSETTLED").exists())

    def test_a_failed_wait_is_a_failed_run_not_an_unsettled_tree(self):
        # WaitForCompact's own error (a full disk, say) says nothing about
        # the tree: the run fails as any db_bench error does.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ran = run03(root, STUB_UNSETTLED="1",
                        STUB_REASON="WaitForCompact IO error: No space left")
            self.assertEqual(ran.returncode, 4)
            cell = root / "results" / "1M" / "T2" / "native"
            self.assertFalse((cell / "UNSETTLED").exists())


class ResumeTest(unittest.TestCase):
    def test_a_resumed_matrix_keeps_its_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(run03(root).returncode, 0)
            other = run03(root, RESUME="1", SESSION_ID="other")
            self.assertEqual(other.returncode, 1)
            self.assertIn("session", other.stderr)
            # With no SESSION_ID the recorded one is taken; native is skipped.
            same = run03(root, RESUME="1", SESSION_ID="",
                         EXPERIMENT_ARMS="native static:uniform_0_75")
            self.assertEqual(same.returncode, 0, same.stderr)
            self.assertIn("[skip]", same.stdout)
            self.assertEqual(run04(root / "results").returncode, 0)
            rows = summary(root / "results")
            self.assertEqual({r["session_id"] for r in rows.values()},
                             {"test-session"})

    def test_a_resumed_matrix_keeps_its_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(run03(root).returncode, 0)
            config = root / "results" / "effective_config.env"
            lines = [("RESEARCH_OBJECTIVE_SHA256=" + "f" * 64
                      if line.startswith("RESEARCH_OBJECTIVE_SHA256=") else line)
                     for line in config.read_text().splitlines()]
            config.write_text("\n".join(lines) + "\n")
            other = run03(root, RESUME="1")
            self.assertEqual(other.returncode, 1)
            self.assertIn("another contract", other.stderr)

    def test_a_resumed_matrix_keeps_its_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(run03(root).returncode, 0)
            other = run03(root, RESUME="1", PRICES_FILE=str(root / "none.json"))
            self.assertEqual(other.returncode, 1)
            self.assertIn("prices", other.stderr)


class RefusalTest(unittest.TestCase):
    """Each refused before any run: the matrix would otherwise fail on the
    node, or run arms the preregistration does not allow."""

    CASES = (
        ({"EXPERIMENT_ARMS": "static:bad-name"}, "Unsupported"),
        ({"EXPERIMENT_ARMS": "static:held"}, "STATIC_PROFILE_held"),
        ({"LEVEL_TARGET_MULTIPLIERS": "1:1:1:1"}, "regular arm only"),
        ({"EXPERIMENT_ARMS": "static:held", "STATIC_PROFILE_held": "1:1:2"},
         "3 entries"),
        ({"EXPERIMENT_ARMS": "static:held", "STATIC_PROFILE_held": "1:1:3:1"},
         "[0.5, 2.0]"),
        ({"EXPERIMENT_ARMS": "static:held", "STATIC_PROFILE_held": "2:1:1:1"},
         "entry 0"),
        # At T=2, level 2's 0.5 * 2 = 1 is below level 1's 2.
        ({"EXPERIMENT_ARMS": "static:held", "STATIC_PROFILE_held": "1:2:0.5:1"},
         "level 2's target is below level 1's"),
        ({"EXPERIMENT_ARMS": "native static:uniform_1"}, "one configuration"),
        ({"WORKLOAD_SKEW": "2", "WORKLOAD_PROFILE": "pow-test"}, "MIX_GET"),
        ({"WORKLOAD_SKEW": "2", "MIX_GET_RATIO": "0.95", "MIX_PUT_RATIO": "0.05",
          "MIX_SEEK_RATIO": "0"}, "WORKLOAD_PROFILE"),
        ({"SETTLE_HOLD_SECONDS": "5"}, "preregisters"),
        ({"EXPERIMENT_ARMS": "native native"}, "twice"),
        ({"EXPERIMENT_ARMS": "static:held", "STATIC_PROFILE_held": "1:1:2:1",
          "SIZE_RATIOS": "2 6"}, "one per (T, size)"),
        ({"SESSION_ID": "a b"}, "SESSION_ID must be"),
        # D-16 §2: a long Programme 1 run loads 2.9M keys; 20M at 29% is 5.8M.
        ({"WORKLOAD_SIZES_M": "20"}, "Programme 1 arms must load 2900000"),
        # A learner arm prices with cost model 2 (D-23): schema-6 prices only.
        ({"EXPERIMENT_ARMS": "learned", "DIAGNOSTIC_RUN": "1",
          "CONTROLLER_PLUGIN": str(FIXTURE / "run.log")}, "schema 6"),
        ({"EXPERIMENT_ARMS": "learner"}, "Unsupported"),
    )

    def test_a_rung_passes_the_load_check(self):
        # 29M at 10% loads 2.9M keys, so 03 goes on to the preflight check.
        with tempfile.TemporaryDirectory() as tmp:
            ran = run03(Path(tmp), WORKLOAD_SIZES_M="29", LOAD_PERCENT="10",
                        PREFLIGHT_MARKER=str(Path(tmp) / "none"))
            self.assertEqual(ran.returncode, 7, ran.stderr)

    def test_refusals(self):
        for overrides, message in self.CASES:
            with self.subTest(overrides=overrides):
                with tempfile.TemporaryDirectory() as tmp:
                    ran = run03(Path(tmp), **overrides)
                    self.assertEqual(ran.returncode, 1, ran.stdout)
                    self.assertIn(message, ran.stderr)
                    self.assertFalse((Path(tmp) / "db").exists())


if __name__ == "__main__":
    unittest.main()
