"""PREREGISTRATION D-31's step runs and the K-step batch's (owner,
2026-10-21): step_plan.py's list, vectors, schedules and twin checks, and
34_step_chain.sh with a stand-in 03 that records what it was asked to run and
writes the run's metadata.env as 03 does. Checks the order (controls,
x0.5/x2, x0.75/x1.5, two-level on W1/W2 only), what each run gets (hold arm,
diagnostic, placeholders, the change at X, the twin's tree), that a missing
or unfit twin skips its (workload, T) while the chain goes on, that a resumed
chain runs only what is left, a failed run and a failed check are logged, and
that a run without a preflight stops the chain; for the K kinds, every L1's
tree, twin, trigger targets, k0 bound and seeded schedule. Also that the real
03 and plugin_config take a step run's settings."""
import random
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import plugin_config
import step_plan
from tests.test_run_experiments import FIXTURE as RUN_FIXTURE, run03

PIPELINE = Path(__file__).resolve().parents[1]
X = "10000000"

# A stand-in 03: one line per call, then 03's result layout (REPEATS=1: no
# repeat level) with the metadata.env keys the chain checks.
STUB_03 = """#!/usr/bin/env bash
set -eu
echo "$SUITE_WORKLOAD|$SIZE_RATIOS|$EXPERIMENT_ARMS|$DIAGNOSTIC_RUN|$PLUGIN_PLACEHOLDERS|$REPEATS|$SESSION_ID|$TREE_DIR|${HOLD_APPLY_AT_OP:-}|${HOLD_APPLY_M:-}|${HOLD_START_M:-}|$L0_COMPACTION_TRIGGER|$MAX_BYTES_FOR_LEVEL_BASE|$WORKLOAD_SIZES_M|${HOLD_APPLY_K0:-}|${HOLD_APPLY_SCHEDULE//|//}|${HOLD_K0_CAP:-}|$RESULTS_ROOT" >> "$STUB_CALLS"
[[ "${HOLD_APPLY_M:-}" != "${STUB_FAIL_M:-none}" ]] || exit 1
[[ "${STUB_EXIT:-0}" == 0 ]] || exit "$STUB_EXIT"
d="$RESULTS_ROOT/${WORKLOAD_SIZES_M}M/T$SIZE_RATIOS/hold"
mkdir -p "$d"
identity="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["identity"])' "$TREE_DIR/tree.json")"
apply=""
[[ -z "${HOLD_APPLY_AT_OP:-}" ]] || apply="$HOLD_APPLY_AT_OP|$HOLD_APPLY_M|${HOLD_APPLY_K0:-}"
[[ -z "${HOLD_APPLY_SCHEDULE:-}" ]] || apply="$HOLD_APPLY_SCHEDULE"
[[ "${HOLD_APPLY_M:-}" != "${STUB_WRONG_M:-none}" ]] || apply="1|wrong|"
{
  echo "dbbench_seed=1"
  echo "experiment_fingerprint=step:40M:binary$STUB_BINARY:objectivex"
  echo "tree_sha256=$identity"
  echo "hold_apply=$apply"
} > "$d/metadata.env"
touch "$d/COMPLETED"
"""


def fs_type(path: Path) -> str:
    return subprocess.run(["stat", "-f", "-c", "%T", str(path)],
                          capture_output=True, text=True).stdout.strip()


def tree_json(identity: str, deepest: int) -> dict:
    rows = [[level, 3 if 1 <= level <= deepest else 0, 1] for level in range(7)]
    return {"identity": identity, "load": {"levels_files_mb": rows}}


class StepPlanTest(unittest.TestCase):
    def test_d31_counts(self):
        # (128 MiB, K = 4): deepest L6 at T 3, L4 at T 6 and 9 (D-31 §2).
        rows = step_plan.steps({3: 6, 6: 4, 9: 4}, ["W1", "W2", "W3", "W4", "W5"],
                               {"control", "single", "two"}, 13)
        kinds = [r[0] for r in rows]
        self.assertEqual((len(rows), kinds.count("control"), kinds.count("single"),
                          kinds.count("two")), (299, 15, 220, 64))
        self.assertEqual({r[1] for r in rows if r[0] == "two"}, {"W1", "W2"})
        self.assertEqual(len({r[1:4] for r in rows}), 299)

    def test_order_and_vectors(self):
        rows = step_plan.steps({3: 4, 6: 3}, ["W1", "W3"], {"control", "single", "two"}, 6)
        phases = [r[0] for r in rows]
        self.assertEqual(phases, sorted(phases, key=["control", "single", "two"].index))
        self.assertEqual([r[1:4] for r in rows[:4]],
                         [("W1", 3, "control"), ("W3", 3, "control"),
                          ("W1", 6, "control"), ("W3", 6, "control")])
        singles = [r[3] for r in rows if r[0] == "single" and r[1] == "W1" and r[2] == 3]
        # x0.5 and x2 at every level first, then x0.75 and x1.5.
        self.assertEqual(singles, ["L1x0.5", "L1x2", "L2x0.5", "L2x2", "L3x0.5", "L3x2",
                                   "L1x0.75", "L1x1.5", "L2x0.75", "L2x1.5", "L3x0.75", "L3x1.5"])
        two = [r[3:] for r in rows if r[0] == "two" and r[2] == 6]
        self.assertEqual(two, [("L1x2-L2x2", "1:2:2:1:1:1"), ("L1x0.5-L2x0.5", "1:0.5:0.5:1:1:1"),
                               ("L1x2-L2x0.5", "1:2:0.5:1:1:1"), ("L1x0.5-L2x2", "1:0.5:2:1:1:1")])
        self.assertEqual(rows[0][4], "-")
        self.assertEqual(step_plan.apply_vector({2: "0.75"}, 4), "1:1:0.75:1")
        with self.assertRaises(ValueError):
            step_plan.apply_vector({4: "2"}, 4)

    def test_vectors_pass_plugin_config(self):
        # Each step's HOLD_APPLY_M is a scheduled change plugin_config takes.
        for _, _, _, sid, vector in step_plan.steps({3: 6}, ["W1"], {"single", "two"}, 13):
            change = plugin_config.scheduled_change("hold", True, int(X), vector, None)
            self.assertEqual(change, {"apply_at_op": int(X), "apply_m": vector}, sid)

    def test_k_batch_counts_and_order(self):
        # The owner's grid: W1-W5 x T 2..10 x L1 64/128/256; 4/5/6 targets.
        rows = step_plan.k_steps(list(range(2, 11)), [64, 128, 256],
                                 ["W1", "W2", "W3", "W4", "W5"], {"k0", "multi"},
                                 int(X), 40_000_000, 13)
        kinds = [r[0] for r in rows]
        self.assertEqual((kinds.count("k0"), kinds.count("multi")), (675, 135))
        self.assertEqual(len({r[1:5] for r in rows}), 810)
        first = rows[:6]
        self.assertEqual([r[1:5] for r in first],
                         [("W1", 2, 64, "K2"), ("W1", 2, 64, "K3"), ("W1", 2, 64, "K5"),
                          ("W1", 2, 64, "K8"), ("W1", 2, 64, "Kmulti"), ("W2", 2, 64, "K2")])
        ones = ":".join(["1"] * 13)
        self.assertEqual(first[0][5:], (ones, "2", "-"))
        self.assertEqual(first[4][5:7], ("-", "-"))
        self.assertEqual(step_plan.K_TARGETS,
                         {64: (2, 3, 5, 8), 128: (2, 3, 6, 9, 12), 256: (2, 3, 8, 12, 16, 19)})
        only = step_plan.k_steps([3], [128], ["W1"], {"k0"}, int(X), 40_000_000, 13)
        self.assertEqual([r[4] for r in only], ["K2", "K3", "K6", "K9", "K12"])

    def test_multi_schedules(self):
        ops = 40_000_000
        for w in ("W1", "W2", "W3", "W4", "W5"):
            for t in range(2, 11):
                for l1, targets in step_plan.K_TARGETS.items():
                    with self.subTest(w=w, t=t, l1=l1):
                        text = step_plan.multi_schedule(w, t, l1, int(X), ops, 13)
                        # The same on every node and every call.
                        self.assertEqual(text, step_plan.multi_schedule(w, t, l1, int(X), ops, 13))
                        plugin_config.check_schedule(text)
                        entries = [e.split("|") for e in text.split(";")]
                        at = [int(e[0]) for e in entries]
                        ks = [int(e[2]) for e in entries]
                        self.assertEqual(at[0], int(X))
                        self.assertLessEqual(at[-1], ops - 2_000_000)
                        self.assertTrue({e[1] for e in entries} == {":".join(["1"] * 13)})
                        self.assertTrue(set(ks) <= set(targets) | {4})
                        self.assertTrue(all(a != b for a, b in zip([4] + ks, ks)))
                        if w == "W5":
                            forced = {12_833_333, 13_833_333, 26_166_666, 27_166_666}
                            self.assertTrue(forced <= set(at))
                            self.assertTrue(all(b - a >= 500_000 for a, b in zip(at, at[1:])))
                        else:
                            gaps = {b - a for a, b in zip(at, at[1:])}
                            self.assertTrue(gaps <= set(step_plan.MULTI_GAPS), gaps)
        # Different settings draw different schedules.
        self.assertNotEqual(step_plan.multi_schedule("W1", 3, 128, int(X), ops, 13),
                            step_plan.multi_schedule("W1", 3, 64, int(X), ops, 13))

    def test_k_rows_pass_plugin_config(self):
        for row in step_plan.k_steps([3], [64, 256], ["W1", "W5"], {"k0", "multi"},
                                     int(X), 40_000_000, 13):
            _, _, _, _, sid, vector, k0, schedule = row
            if schedule != "-":
                change = plugin_config.scheduled_change("hold", True, None, None, None, schedule)
                self.assertEqual(change, {"apply_schedule": schedule}, sid)
            else:
                change = plugin_config.scheduled_change("hold", True, int(X), vector, int(k0))
                self.assertEqual(change["apply_k0"], int(k0), sid)

    def test_deepest_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tree.json"
            path.write_text(json.dumps(tree_json("a", 4)))
            self.assertEqual(step_plan.deepest_level(path), 4)
            path.write_text(json.dumps(tree_json("a", 1)))
            with self.assertRaises(ValueError):
                step_plan.deepest_level(path)

    def test_twin_and_run_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / "tree.json").write_text(json.dumps(tree_json("t" * 64, 4)))
            twin = {"dbbench_seed": "1", "tree_sha256": "t" * 64,
                    "experiment_fingerprint": f"w1-assoc:40M:binary{'b' * 64}:objective1"}
            self.assertEqual(step_plan.twin_problems(twin, tree, "1", "b" * 64, "w1-assoc"), [])
            bad = step_plan.twin_problems(dict(twin, dbbench_seed="2", tree_sha256="u"),
                                          tree, "1", "c" * 64, "w2-powerlaw")
            self.assertEqual([p.split(":")[0] for p in bad], ["tree", "seed", "binary", "workload"])
            run = dict(twin, hold_apply=f"{X}|1:2:1|")
            self.assertEqual(step_plan.run_problems(run, twin, f"{X}|1:2:1|"), [])
            self.assertEqual([p.split(":")[0] for p in
                              step_plan.run_problems(dict(run, dbbench_seed="3"), twin, "")],
                             ["dbbench_seed", "hold_apply"])

    def test_real_03_takes_a_step_run(self):
        # A hold-only matrix with placeholders reaches the preflight check at
        # full length (exit 7); with another arm beside it, it is refused.
        base = dict(DIAGNOSTIC_RUN="1", PLUGIN_PLACEHOLDERS="1", WORKLOAD_SIZES_M="29",
                    LOAD_PERCENT="10", CONTROLLER_PLUGIN=str(RUN_FIXTURE / "run.log"))
        with tempfile.TemporaryDirectory() as tmp:
            ran = run03(Path(tmp), EXPERIMENT_ARMS="hold", HOLD_APPLY_AT_OP=X,
                        HOLD_APPLY_M="1:0.5:1:1", PREFLIGHT_MARKER=str(Path(tmp) / "none"), **base)
            self.assertEqual(ran.returncode, 7, ran.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            ran = run03(Path(tmp), EXPERIMENT_ARMS="native hold",
                        PREFLIGHT_MARKER=str(Path(tmp) / "none"), **base)
            self.assertEqual(ran.returncode, 1)
            self.assertIn("smoke runs only", ran.stderr)


class StepChainTest(unittest.TestCase):
    def setUp(self):
        if fs_type(Path.home()) == "tmpfs":
            self.skipTest("no non-tmpfs directory for the results root")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        nvme = tempfile.TemporaryDirectory(dir=Path.home(), prefix=".step-chain-")
        self.addCleanup(nvme.cleanup)
        self.nvme = Path(nvme.name).resolve()
        build = self.root / "build"
        build.mkdir()
        (build / "db_bench").write_text("#!/usr/bin/env bash\nexit 0\n")
        (build / "db_bench").chmod(0o755)
        venv = self.root / "venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python").symlink_to(sys.executable)
        stub = self.root / "stub03.sh"
        stub.write_text(STUB_03)
        stub.chmod(0o755)
        self.binary = subprocess.run(
            [sys.executable, str(PIPELINE / "preflight_marker.py"), "identity",
             "--db-bench", str(build / "db_bench")],
            capture_output=True, text=True, check=True).stdout.strip()
        # T 3: deepest L3 (steps at L1, L2); T 6: deepest L2 (L1 only).
        for t, deepest in ((3, 3), (6, 2)):
            tree = self.tree(t)
            (tree / "db").mkdir(parents=True)
            (tree / "db" / "CURRENT").write_text("MANIFEST-000003\n")
            (tree / "COMPLETED").touch()
            (tree / "tree.json").write_text(json.dumps(tree_json(f"{t}" * 64, deepest)))
        # W1's twins ran here (33's layout); W3's on another node (STEP_TWIN_ROOT).
        for t in (3, 6):
            self.twin("W1", t, here=True)
            self.twin("W3", t)
        self.calls = self.root / "calls.txt"
        self.env = {
            **os.environ, "NVME": str(self.nvme), "ALLOW_ROOT_DISK": "1",
            "MIN_FREE_GB": "1", "STEP_MIN_FREE_GB": "1",
            "DBBENCH_BUILD_DIR": str(build), "PYTHON_VENV": str(self.root / "venv"),
            "STUB_CALLS": str(self.calls), "STUB_BINARY": self.binary,
            "DBBENCH_CPUS": "", "CONTROLLER_CPUS": "", "NUM_LEVELS": "6",
            "SUITE_PRELOAD_KEYS": "1000", "SUITE_RUN_OPERATIONS_M": "40",
            "STEP_T": "3 6", "STEP_WORKLOADS": "W1 W3",
            "STEP_RUN_EXPERIMENTS": str(stub), "STEP_EVALUATE": "0"}

    def tree(self, t: int, l1: int = 128) -> Path:
        return self.nvme / "suite-trees" / f"T{t}-l1{l1}-k4-n1000-s1000"

    def twin(self, w: str, t: int, here: bool = False, seed: str = "1",
             l1: int = 128) -> Path:
        profile = {"W1": "w1-assoc", "W3": "w3-update-heavy"}[w]
        if here:
            d = (self.nvme / f"suite-{w}" / f"T{t}-l1{l1}-k4" / "40M" / f"T{t}" /
                 "repeat-01" / "native")
        elif l1 == 128:
            d = self.nvme / "step-twins" / w / f"T{t}"
        else:
            d = self.nvme / "step-twins" / w / f"T{t}" / f"l1{l1}"
        identity = str(t) * 64 if l1 == 128 else f"{t}{l1}" * 16
        d.mkdir(parents=True, exist_ok=True)
        (d / "metadata.env").write_text(
            f"dbbench_seed={seed}\ntree_sha256={identity}\n"
            f"experiment_fingerprint={profile}:40M:T{t}:binary{self.binary}:objective1\n")
        return d / "metadata.env"

    def chain(self, *args, **overrides):
        return subprocess.run(["bash", str(PIPELINE / "34_step_chain.sh"), *args],
                              env={**self.env, **overrides}, capture_output=True, text=True)

    def called(self):
        return [c.split("|") for c in self.calls.read_text().splitlines()]

    def k_setup(self, l1s=(64, 128)):
        """Trees and twins at every L1 of the K kinds' test: W1's twins here,
        W3's on another node (at 128 MiB in D-31's layout, else per L1)."""
        for t in (3, 6):
            for l1 in l1s:
                if l1 == 128:
                    continue
                tree = self.tree(t, l1)
                (tree / "db").mkdir(parents=True)
                (tree / "db" / "CURRENT").write_text("MANIFEST-000003\n")
                (tree / "COMPLETED").touch()
                (tree / "tree.json").write_text(json.dumps(tree_json(f"{t}{l1}" * 16, 3)))
                self.twin("W1", t, here=True, l1=l1)
                self.twin("W3", t, l1=l1)

    def test_plan(self):
        planned = self.chain("plan")
        self.assertEqual(planned.returncode, 0, planned.stderr)
        # Controls 4; singles W1 and W3: (2 + 1) levels x 4 each; two-level:
        # W1 at T 3, one pair x 4.
        self.assertIn("# 32 runs at 128:4, change at operation 10000000", planned.stdout)
        self.assertEqual(planned.stdout.splitlines()[0], "control W1 T3 control -")
        self.assertFalse(self.calls.exists())

    def test_the_chain(self):
        ran = self.chain()
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertEqual(ran.stdout.strip().splitlines()[-1], "STEP_CHAIN_EXIT=0")
        calls = self.called()
        self.assertEqual(len(calls), 32)
        for c in calls:
            w, t, arm, diag, placeholders, repeats, session, tree = c[:8]
            self.assertEqual((arm, diag, placeholders, repeats, session),
                             ("hold", "1", "1", "1", f"step-{w}"))
            self.assertEqual(tree, str(self.tree(int(t))))
            self.assertEqual((c[10], c[11], c[12], c[13]), ("", "4", str(128 * 1048576), "40"))
        controls = calls[:4]
        self.assertEqual([(c[0], c[1], c[8], c[9]) for c in controls],
                         [("W1", "3", "", ""), ("W3", "3", "", ""), ("W1", "6", "", ""), ("W3", "6", "", "")])
        self.assertEqual({c[8] for c in calls[4:]}, {X})
        self.assertEqual((calls[4][0], calls[4][1], calls[4][9]), ("W1", "3", "1:0.5:1:1:1:1"))
        two = [c for c in calls if len([v for v in c[9].split(":") if v != "1"]) == 2]
        self.assertEqual({c[0] for c in two}, {"W1"})
        self.assertEqual(len(two), 4)
        done = list((self.nvme / "step-W3" / "T6-l1128-k4").glob("*/40M/T6/hold/COMPLETED"))
        self.assertEqual(sorted(p.parts[-5] for p in done),
                         sorted(["control", "L1x0.5", "L1x2", "L1x0.75", "L1x1.5"]))

    def test_missing_twin_then_resume(self):
        (self.nvme / "step-twins" / "W3" / "T6" / "metadata.env").unlink()
        ran = self.chain()
        self.assertEqual(ran.returncode, 5, ran.stdout + ran.stderr)
        self.assertIn("W3/T6: twin missing", ran.stdout)
        self.assertEqual(len(self.called()), 32 - 5)
        self.twin("W3", 6)
        self.calls.unlink()
        again = self.chain(RESUME="1")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual({(c[0], c[1]) for c in self.called()}, {("W3", "6")})
        self.assertEqual(len(self.called()), 5)

    def test_a_twin_that_does_not_fit(self):
        self.twin("W1", 3, here=True, seed="2")
        ran = self.chain(STEP_WORKLOADS="W1", STEP_T="3")
        self.assertEqual(ran.returncode, 5)
        self.assertIn("W1/T3: twin does not fit: seed", ran.stdout)
        self.assertFalse(self.calls.exists())

    def test_failures_are_logged_and_the_chain_goes_on(self):
        ran = self.chain(STUB_FAIL_M="1:2:1:1:1:1", STUB_WRONG_M="1:0.5:1:1:1:1")
        self.assertEqual(ran.returncode, 5, ran.stderr)
        self.assertEqual(len(self.called()), 32)
        self.assertIn("W1/T3 L1x2 FAILED (exit 1)", ran.stdout)
        self.assertIn("W1/T3 L1x0.5 does not match its twin or plan: hold_apply", ran.stdout)

    def test_no_preflight_stops_the_chain(self):
        ran = self.chain(STUB_EXIT="7")
        self.assertEqual(ran.returncode, 1)
        self.assertIn("no matching preflight", ran.stderr)
        self.assertEqual(len(self.called()), 1)

    def test_the_k_kinds(self):
        self.k_setup()
        env = dict(STEP_KINDS="k0 multi", STEP_L1="64 128")
        planned = self.chain("plan", **env)
        self.assertEqual(planned.returncode, 0, planned.stderr)
        # 2 T x 2 workloads x (4 targets at 64, 5 at 128, + 1 multi each).
        self.assertIn("# 44 runs at 64:4 128:4, change at operation 10000000", planned.stdout)
        self.assertEqual(planned.stdout.splitlines()[0], "k0 W1 T3 l164 K2")
        ran = self.chain(**env)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        calls = self.called()
        self.assertEqual(len(calls), 44)
        ones = ":".join(["1"] * 6)
        for c in calls:
            w, t = c[0], int(c[1])
            l1 = int(c[12]) // 1048576
            self.assertEqual(c[7], str(self.tree(t, l1)))
            self.assertEqual(c[11], "4")
            self.assertEqual(c[16], "19")  # every K target is taken
            self.assertEqual(Path(c[17]).parent.name, f"T{t}-l1{l1}-k4")
            if c[15]:  # multi: the schedule alone (its "|" logged as "/")
                self.assertEqual((c[8], c[9], c[14]), ("", "", ""))
                self.assertEqual(c[15], step_plan.multi_schedule(
                    w, t, l1, int(X), 40_000_000, 6).replace("|", "/"))
            else:      # k0: every multiplier 1, the trigger at X
                self.assertEqual((c[8], c[9]), (X, ones))
                self.assertIn(int(c[14]), step_plan.K_TARGETS[l1])
        self.assertEqual(sorted(c[14] for c in calls if c[0] == "W3" and c[1] == "6"
                                and c[12] == str(64 * 1048576)), ["", "2", "3", "5", "8"])
        done = list((self.nvme / "step-W3" / "T6-l164-k4").glob("*/40M/T6/hold/COMPLETED"))
        self.assertEqual(sorted(p.parts[-5] for p in done), ["K2", "K3", "K5", "K8", "Kmulti"])

    def test_a_k_twin_is_per_l1(self):
        self.k_setup()
        (self.nvme / "step-twins" / "W3" / "T6" / "l164" / "metadata.env").unlink()
        ran = self.chain(STEP_KINDS="k0", STEP_L1="64 128", STEP_WORKLOADS="W3", STEP_T="6")
        self.assertEqual(ran.returncode, 5, ran.stdout + ran.stderr)
        self.assertIn("W3/T6/l164: twin missing", ran.stdout)
        self.assertEqual({c[12] for c in self.called()}, {str(128 * 1048576)})

    def test_d31_kinds_stay_at_128(self):
        planned = self.chain("plan", STEP_L1="64 128")
        self.assertNotEqual(planned.returncode, 0)
        self.assertIn("D-31's kinds run at 128 MiB only", planned.stderr)

    def test_a_fresh_chain_refuses_old_results(self):
        (self.nvme / "step-W1").mkdir()
        ran = self.chain()
        self.assertEqual(ran.returncode, 1)
        self.assertIn("step-W1 exists", ran.stderr)


if __name__ == "__main__":
    unittest.main()
