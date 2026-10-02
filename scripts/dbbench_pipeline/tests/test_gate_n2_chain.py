"""25_gate_n2_chain.sh end to end on a copy of the pipeline, with the stand-in
db_bench of test_gate_n1_chain.py. Every other stage is the real one: 03 with
its marker, load and profile-vector checks, 23 and 04. Gate N1's admission
reports, q-bar, the prices and the preflight marker are written as a finished
Gate N1 night leaves them. Checks the plan against Theta_s and D-19; a
screen, then the sweep at one T (native before the measured profiles at each
point, one session, each vector at its own point, a refused profile skipped);
RESUME keeping a point's vectors; a top-up and the cross-T mode, started from
another folder; a failure of 23 stopping one workload while the other
finishes; and the refusals before any run."""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
REPO = PIPELINE.parents[1]
FAKE = PIPELINE / "tests" / "fixtures" / "chain" / "fake_db_bench.py"
MIB = 1 << 20
MEASURED = ("static:survival_weighted", "static:last_level_emptying")
# Theta_s's (base MiB, K0) points: K0 = 8 exceeds K_cap = 8 MiB / 2 MiB = 4.
POINTS = [(b, k) for b in (8, 16, 32) for k in (2, 4, 8) if (b, k) != (8, 8)]
# In the stand-in, 23 computes survival-weighted only at these bases.
SURVIVAL = f"{16 * MIB} {32 * MIB}"
ONES = ":".join(["1"] * 13)


def executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


def fs_type(path: Path) -> str:
    return subprocess.run(["stat", "-f", "-c", "%T", str(path)],
                          capture_output=True, text=True).stdout.strip()


def env_file(path: Path) -> dict:
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if "=" in line)


def arm_rows(root: Path) -> list[list[str]]:
    """03's arms.tsv, in run order: size, T, repeat, arm, status, folder."""
    return [line.split("\t") for line in
            (root / "arms.tsv").read_text().splitlines()[1:]]


def admission(size, load, undecided=(), n_min=None):
    """What 19 reports for one cell, as far as 25 reads it."""
    levels = {"2": {"decision": "reference"},
              **{str(level): {"decision": "undecided"} for level in undecided}}
    return {"levels": levels, "config": {"n_min": n_min, "n_turn": 10},
            "run_length": {"level": 2,
                           "rung": {"size_millions": size, "load_percent": load}}}


class GateN2ChainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # 25 refuses a tmpfs results root, and /tmp often is one.
        if fs_type(Path.home()) == "tmpfs":
            self.skipTest("no non-tmpfs directory for the results root")
        self.nvme_tmp = tempfile.TemporaryDirectory(dir=Path.home(),
                                                    prefix=".chain-test-")
        self.addCleanup(self.nvme_tmp.cleanup)
        self.nvme = Path(self.nvme_tmp.name).resolve()
        self.root = Path(self.tmp.name) / "repo"
        self.pipeline = self.root / "scripts" / "dbbench_pipeline"
        shutil.copytree(PIPELINE, self.pipeline,
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(REPO / "config", self.root / "config")
        shutil.copy(REPO / ".gitignore", self.root / ".gitignore")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        db_bench = self.root / "build-dbbench" / "db_bench"
        executable(db_bench,
                   f'#!/usr/bin/env bash\nexec "{sys.executable}" "{FAKE}" "$@"\n')
        self.venv = Path(self.tmp.name) / "venv"
        executable(self.venv / "bin" / "python",
                   f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n')
        # What the Gate N1 night, 18 and the q-bar amendment leave.
        self.contract = self.root / "config" / "research_objective_contract.json"
        self.frozen = self.contract.read_text()
        contract = json.loads(self.frozen)
        contract["reference_rate"]["ops_per_second"] = {
            "assoc": 62500.0, "powerlaw_get95": 62500.0}
        self.contract.write_text(json.dumps(contract, indent=2) + "\n")
        self.prices = {
            "schema": 3,
            "price_per_core_second": contract["prices"]["price_per_core_second"],
            "db_bench_sha256": hashlib.sha256(db_bench.read_bytes()).hexdigest(),
            "c_w": 1e-14, "c_f": 1e-11, "c_blk": 2e-11, "c_sk": 4e-11,
            "c_open": 2e-10}
        (self.root / "build-dbbench" / "prices.json").write_text(json.dumps(self.prices))
        for workload, rung in (("assoc", (29, 10)), ("powerlaw", (58, 5))):
            for t in (2, 6, 10):
                self.admission(workload, t, admission(*rung))
        self.write_marker()

    def admission(self, workload, t, report):
        path = self.nvme / f"n1-{workload}" / f"admission_T{t}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report))

    def write_marker(self):
        subprocess.run(
            [sys.executable, "scripts/dbbench_pipeline/preflight_marker.py", "write",
             "--marker", "build-dbbench/PREFLIGHT_PASSED",
             "--db-bench", "build-dbbench/db_bench", "--passed", "1", "2", "3", "4",
             "--skipped", "5=no plugin", "6=no plugin"],
            cwd=self.root, check=True, capture_output=True)

    # Variables of 25 and config.sh that would point the copy at real paths
    # or change its behaviour if exported where the test runs.
    SCRUBBED = ("RESUME", "ALLOW_ROOT_DISK", "DBBENCH_BUILD_DIR", "PREFLIGHT_MARKER",
                "PREFLIGHT_WORK_DIR", "PRICES_FILE", "DB_ROOT", "RESULTS_ROOT",
                "SESSION_ID", "KEEP_DATABASES", "FAKE_FAIL_WORKLOAD",
                "FAKE_SURVIVAL_BASES", "N2_CONFIGS", "N2_REPEATS", "N2_T",
                "N2_WORKLOADS", "N2_RUN_LENGTH_assoc", "N2_RUN_LENGTH_powerlaw",
                "WORKLOAD_SKEW", "WORKLOAD_PROFILE", "MIX_GET_RATIO",
                "MIX_PUT_RATIO", "MIX_SEEK_RATIO", "EXPERIMENT_ARMS",
                "MAX_BYTES_FOR_LEVEL_BASE", "L0_COMPACTION_TRIGGER")

    def run25(self, *args, cwd=None, script=None, **overrides):
        env = {k: v for k, v in os.environ.items() if k not in self.SCRUBBED}
        env.update({"NVME": str(self.nvme), "MIN_FREE_GB": "0",
                    "DBBENCH_CPUS": "", "CONTROLLER_CPUS": "",
                    "PYTHON_VENV": str(self.venv), "ALLOW_CONCURRENT_RUNS": "1",
                    "ALLOW_ROOT_DISK": "1", **overrides})
        script = script or str(self.pipeline / "25_gate_n2_chain.sh")
        return subprocess.run(["bash", script, *args], cwd=cwd, env=env,
                              capture_output=True, text=True, timeout=1800)

    def completed(self, workload="assoc") -> set:
        return {p.parent for p in (self.nvme / f"n2-{workload}").glob("**/COMPLETED")}

    def plan_steps(self, stdout, workload):
        section = stdout.split(f"# workload {workload}: ")[1].split("# workload ")[0]
        steps = [line.split() for line in section.splitlines()
                 if line.startswith(("run ", "profiles "))]
        last = {}
        for step in steps:
            if step[0] == "run":
                for arm in step[6:]:
                    last[step[1], arm] = int(step[5])
        return section, steps, last

    def test_the_plan_is_theta_s(self):
        ran = self.run25("plan")
        self.assertEqual(ran.returncode, 0, ran.stderr[-3000:])
        for workload, size in (("assoc", 29), ("powerlaw", 58)):
            section, steps, last = self.plan_steps(ran.stdout, workload)
            want = {(f"T{t}-b{b}-k{k}", arm): 5 for t in (2, 6, 10) for b, k in POINTS
                    for arm in ("native", "static:uniform_0_75", *MEASURED)}
            self.assertEqual(last, want)
            self.assertIn(f"# {workload}: 480 runs of {size}M", section)
            self.assertIn("# excluded: base 8 MiB, K0 = 8: above K_cap", section)
            # Per point: its native runs, then 23, then its measured profiles;
            # and T by T.
            for t in (2, 6, 10):
                for b, k in POINTS:
                    point = f"T{t}-b{b}-k{k}"
                    at = [i for i, s in enumerate(steps) if s[1] == point]
                    native = max(i for i in at if "native" in steps[i])
                    profiles = steps.index(["profiles", point, str(t), str(b), str(k)])
                    measured = min(i for i in at if set(MEASURED) & set(steps[i]))
                    self.assertLess(native, profiles)
                    self.assertLess(profiles, measured)
            ts = [int(s[2]) for s in steps]
            self.assertEqual(ts, sorted(ts))
        # A subset of the sweep's T values, one workload, and a screen: the
        # native arms still run five, since the profiles need them.
        some = self.run25("plan", N2_T="6 10", N2_WORKLOADS="assoc", N2_REPEATS="3")
        self.assertEqual(some.returncode, 0, some.stderr[-3000:])
        self.assertNotIn("powerlaw", some.stdout)
        section, steps, last = self.plan_steps(some.stdout, "assoc")
        self.assertEqual({(a, k) for (_, a), k in last.items()},
                         {("native", 5), ("static:uniform_0_75", 3), *(
                             (a, 3) for a in MEASURED)})
        self.assertEqual(sum(s[0] == "profiles" for s in steps), 16)
        self.assertIn("# assoc: 224 runs of 29M, about 29 h", section)

    def test_a_screen_costs_what_27_says(self):
        # 25's plan for a screen of the sweep is 27's screening phase: the
        # screened configurations at the screen's runs, the natives at five.
        spec = importlib.util.spec_from_file_location(
            "screen_design", self.pipeline / "27_screen_design.py")
        screen_design = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(screen_design)
        static = json.loads(self.contract.read_text())["static_class"]
        points = screen_design.admissible_points(static, 2 * MIB, 20)
        cells = len(static["size_ratios"])
        configs, natives = cells * len(points) * len(static["profiles"]), cells * len(points)
        for n in (2, 3):
            with self.subTest(screen=n):
                ran = self.run25("plan", N2_WORKLOADS="assoc", N2_REPEATS=str(n))
                self.assertEqual(ran.returncode, 0, ran.stderr[-3000:])
                _, _, last = self.plan_steps(ran.stdout, "assoc")
                self.assertEqual(sum(last.values()), screen_design.design_runs(
                    configs, natives, 0, 5, n, 0.0))

    def test_undecided_levels_change_nothing(self):
        # D-19: a level Gate N1 left undecided is not pooled and is not
        # decided on Gate N2, with or without an n_min, so every native arm
        # runs the initial five and nothing warns.
        for n_min in (80, None):
            with self.subTest(n_min=n_min):
                self.admission("assoc", 6, admission(29, 10, undecided=[3],
                                                     n_min=n_min))
                ran = self.run25("plan", N2_WORKLOADS="assoc")
                self.assertEqual(ran.returncode, 0, ran.stderr[-3000:])
                _, _, last = self.plan_steps(ran.stdout, "assoc")
                self.assertEqual({k for (p, a), k in last.items()
                                  if a == "native"}, {5})
                self.assertNotIn("undecided", ran.stdout + ran.stderr)
        # The plan no longer reads Gate N1's reports, so an override of the
        # run length needs none.
        shutil.rmtree(self.nvme / "n1-assoc")
        ran = self.run25("plan", N2_WORKLOADS="assoc", N2_RUN_LENGTH_assoc="58 5")
        self.assertEqual(ran.returncode, 0, ran.stderr[-3000:])
        self.assertIn("# workload assoc: 58M at 5% load", ran.stdout)

    def test_screen_sweep_resume_top_up_and_cross_t(self):
        screen = {"N2_T": "10", "N2_REPEATS": "2", "N2_WORKLOADS": "assoc",
                  "FAKE_SURVIVAL_BASES": SURVIVAL}
        ran = self.run25(**screen)
        self.assertEqual(ran.returncode, 0, ran.stdout[-3000:] + ran.stderr[-3000:])
        n2 = self.nvme / "n2-assoc"
        # The screen: five natives (the profiles need them), two of the rest.
        rows = arm_rows(n2 / "T10-b16-k4")
        self.assertEqual({a: sum(r[3] == a for r in rows) for a in {r[3] for r in rows}},
                         {"native": 5, "static:uniform_0_75": 2,
                          "static:survival_weighted": 2, "static:last_level_emptying": 2})
        measured = {p: p.read_text() for p in n2.glob("*/profiles.json")}
        self.assertEqual(len(measured), len(POINTS))

        # A second night finds the folders; RESUME=1 tops the screen up to five.
        again = self.run25(**{**screen, "N2_REPEATS": "5"})
        self.assertEqual(again.returncode, 1)
        self.assertIn("n2-assoc exists", again.stderr)
        sweep = {**screen, "N2_REPEATS": "5", "RESUME": "1"}
        ran = self.run25(**sweep)
        self.assertEqual(ran.returncode, 0, ran.stdout[-3000:] + ran.stderr[-3000:])
        self.assertEqual(sorted(p.name for p in n2.glob("T*")),
                         sorted(f"T10-b{b}-k{k}" for b, k in POINTS))
        five = ["1", "2", "3", "4", "5"]
        for b, k in POINTS:
            point = f"T10-b{b}-k{k}"
            root = n2 / point
            rows = arm_rows(root)
            arms = [row[3] for row in rows]
            want = {"native", "static:uniform_0_75", "static:last_level_emptying"}
            if b != 8:
                want.add("static:survival_weighted")
            self.assertEqual(set(arms), want, point)
            for arm in want:
                self.assertEqual(sorted(r[2] for r in rows if r[3] == arm), five)
            # Native first, then the measured profiles, in one session.
            self.assertLess(max(i for i, a in enumerate(arms) if a == "native"),
                            min(i for i, a in enumerate(arms) if a in MEASURED))
            profiles = json.loads((root / "profiles.json").read_text())
            self.assertEqual((profiles["inputs"]["K0"], profiles["inputs"]["C1"]),
                             (k, b * MIB))
            self.assertEqual(profiles["runs"], [f"repeat-0{r}/native" for r in five])
            for row in rows:
                meta = env_file(Path(row[5]) / "metadata.env")
                self.assertEqual(meta["session_id"], "n2-assoc")
                self.assertEqual(meta["level0_file_num_compaction_trigger"], str(k))
                self.assertEqual(meta["max_bytes_for_level_base"], str(b * MIB))
                if row[3] in MEASURED:
                    # The vector 23 measured at this point, and no other.
                    self.assertEqual(meta["level_target_multipliers"],
                                     profiles[row[3][7:]]["vector"])
            if b == 8:
                self.assertIn("no merged bytes leave levels",
                              profiles["survival_weighted"]["refused"])
                self.assertIn(f"=== assoc {point}: refused survival_weighted", ran.stdout)
                self.assertIn(f"    {point}: refused survival_weighted", ran.stdout)
        self.assertTrue((n2 / "graphs" / "summary.csv").exists())
        # Measured once, in the screen.
        self.assertEqual({p: p.read_text() for p in n2.glob("*/profiles.json")}, measured)

        # A point's vectors are never measured again: one is changed by hand,
        # and a resume leaves it, and the top-up below runs it.
        held = n2 / "T10-b16-k4" / "profiles.json"
        report = json.loads(held.read_text())
        report["last_level_emptying"]["vector"] = "1:1:1:1.5" + ONES[7:]
        held.write_text(json.dumps(report))
        changed = held.read_text()
        done = self.completed()
        resumed = self.run25(**sweep)
        self.assertEqual(resumed.returncode, 0, resumed.stderr[-3000:])
        self.assertIn("[skip]", resumed.stdout)
        self.assertEqual(self.completed(), done)
        self.assertEqual(held.read_text(), changed)

        # A named set, given by a path relative to where 25 starts, and 25 by
        # a relative path: a top-up at T=10 and a cross-T configuration at
        # T=14. It names no powerlaw configuration, and powerlaw is left alone.
        configs = Path(self.tmp.name) / "n2_configs.txt"
        configs.write_text("# workload T base K0 profile repeats\n"
                           "assoc 10 16 4 last_level_emptying 6\n"
                           "assoc 10 16 4 uniform_1 6\n"
                           "assoc 14 16 4 survival_weighted 2  # theta* of a mode\n")
        listed = self.run25(cwd=self.tmp.name,
                            script="repo/scripts/dbbench_pipeline/25_gate_n2_chain.sh",
                            N2_CONFIGS="n2_configs.txt", FAKE_SURVIVAL_BASES=SURVIVAL)
        self.assertEqual(listed.returncode, 0, listed.stdout[-3000:] + listed.stderr[-3000:])
        self.assertIn("powerlaw: nothing planned", listed.stdout)
        self.assertFalse((self.nvme / "n2-powerlaw").exists())
        top_up = n2 / "T10-b16-k4" / "29M" / "T10" / "repeat-06"
        cross = n2 / "T14-b16-k4" / "29M" / "T14"
        self.assertEqual(self.completed() - done, {
            top_up / "native", top_up / "static:last_level_emptying",
            *(cross / f"repeat-0{r}" / "native" for r in five),
            *(cross / f"repeat-0{r}" / "static:survival_weighted" for r in ("1", "2"))})
        self.assertEqual(
            env_file(top_up / "static:last_level_emptying" / "metadata.env")
            ["level_target_multipliers"], "1:1:1:1.5" + ONES[7:])
        profiles = json.loads((n2 / "T14-b16-k4" / "profiles.json").read_text())
        self.assertEqual(profiles["inputs"]["T"], 14)
        arms = [row[3] for row in arm_rows(n2 / "T14-b16-k4")]
        self.assertEqual(arms, ["native"] * 5 + ["static:survival_weighted"] * 2)
        self.assertEqual(
            env_file(cross / "repeat-01" / "static:survival_weighted" / "metadata.env")
            ["level_target_multipliers"], profiles["survival_weighted"]["vector"])

    def test_a_failure_of_23_stops_its_workload_only(self):
        # Not a refusal of D-14 §3 but a bug: assoc stops before its measured
        # profile runs; powerlaw, which needs no profile, finishes.
        executable(self.pipeline / "23_static_profiles.py",
                   "#!/usr/bin/env python3\nraise RuntimeError('a bug in 23')\n")
        self.write_marker()
        configs = Path(self.tmp.name) / "n2_configs.txt"
        configs.write_text("assoc 10 16 4 last_level_emptying 5\n"
                           "powerlaw 10 16 4 uniform_1 2\n")
        ran = self.run25(N2_CONFIGS=str(configs))
        self.assertEqual(ran.returncode, 1)
        self.assertIn("23 failed at", ran.stderr)
        self.assertIn("a bug in 23", ran.stderr)
        self.assertIn("assoc FAILED; the other workload goes on", ran.stdout)
        self.assertEqual({p.name for p in self.completed("assoc")}, {"native"})
        self.assertEqual(len(self.completed("powerlaw")), 2)
        self.assertTrue((self.nvme / "n2-powerlaw" / "graphs" / "summary.csv").exists())

    def test_refusals_before_any_run(self):
        draft = Path(self.tmp.name) / "draft_prices.json"
        draft.write_text(json.dumps({"schema": 1, "c_w": 1.0}))
        stale = Path(self.tmp.name) / "stale_prices.json"
        stale.write_text(json.dumps({**self.prices, "db_bench_sha256": "0" * 64}))
        five = Path(self.tmp.name) / "five.txt"
        five.write_text("".join(f"assoc 14 {b} {k} uniform_1 5\n" for b, k in POINTS[:5]))
        cases = [({"PRICES_FILE": str(Path(self.tmp.name) / "none.json")}, "run 18"),
                 ({"PRICES_FILE": str(draft)}, "schema 3"),
                 ({"PRICES_FILE": str(stale)}, "re-measures them after any binary change"),
                 ({"PREFLIGHT_MARKER": str(Path(self.tmp.name) / "none")},
                  "no preflight marker for this db_bench"),
                 ({"N2_CONFIGS": str(five)}, "D-13 §4 allows 4"),
                 ({"N2_CONFIGS": str(Path(self.tmp.name) / "none.txt")}, "none.txt"),
                 ({"N2_REPEATS": "1"}, "at least 2"),
                 ({"N2_T": "14"}, "cross-T runs go through N2_CONFIGS"),
                 ({"N2_RUN_LENGTH_assoc": "58"}, "N2_RUN_LENGTH_assoc must be")]
        for overrides, message in cases:
            with self.subTest(message=message):
                ran = self.run25(**overrides)
                self.assertEqual(ran.returncode, 1)
                self.assertIn(message, ran.stderr)
                self.assertFalse((self.nvme / "n2-assoc").exists())
        # Last: q-bar not yet measured, as the frozen contract had it until
        # it was recorded on 2026-10-02.
        contract = json.loads(self.frozen)
        contract["reference_rate"]["ops_per_second"] = {
            "assoc": None, "powerlaw_get95": None}
        self.contract.write_text(json.dumps(contract, indent=2) + "\n")
        ran = self.run25()
        self.assertEqual(ran.returncode, 1)
        self.assertIn("q-bar for assoc (assoc) is not recorded", ran.stderr)
        self.assertFalse((self.nvme / "n2-assoc").exists())

    def test_the_marker_is_checked_with_the_plugin(self):
        """With controller/ present, 13 binds the plugin's hash into the
        marker, and 25 checks it as 03 does: a marker with that plugin gets
        past the check (the start then stops at the prices), a marker
        without it is refused."""
        (self.root / "controller").mkdir()
        (self.root / "controller" / "plugin.cc").write_text("// stand-in\n")
        plugin = self.root / "build-controller" / "librl_controller.so"
        plugin.parent.mkdir()
        plugin.write_bytes(b"stand-in plugin")
        no_prices = {"PRICES_FILE": str(Path(self.tmp.name) / "none.json")}
        subprocess.run(
            [sys.executable, "scripts/dbbench_pipeline/preflight_marker.py", "write",
             "--marker", "build-dbbench/PREFLIGHT_PASSED",
             "--db-bench", "build-dbbench/db_bench",
             "--plugin", "build-controller/librl_controller.so",
             "--passed", "1", "2", "3", "4", "5", "--skipped", "6=no learner"],
            cwd=self.root, check=True, capture_output=True)
        ran = self.run25(**no_prices)
        self.assertEqual(ran.returncode, 1)
        self.assertNotIn("no preflight marker", ran.stderr)
        self.assertIn("run 18", ran.stderr)
        self.write_marker()  # as before the plugin: no plugin hash
        ran = self.run25(**no_prices)
        self.assertEqual(ran.returncode, 1)
        self.assertIn("no preflight marker for this db_bench", ran.stderr)

    def test_a_vector_is_used_only_at_its_own_point_and_runs(self):
        point = Path(self.tmp.name) / "nvme" / "T10-b16-k4"
        names = [f"repeat-0{r}/native" for r in range(1, 6)]
        for name in names:
            (point / "29M" / "T10" / name).mkdir(parents=True)
            (point / "29M" / "T10" / name / "COMPLETED").touch()
        profiles = point / "profiles.json"
        profiles.write_text(json.dumps({
            "runs": names,
            "inputs": {"T": 10.0, "K0": 4, "C1": float(16 * MIB),
                       "fingerprint": "assoc-v1:29M:T10:k24"},
            "survival_weighted": {"refused": "no merged bytes leave levels [0]"},
            "last_level_emptying": {"vector": "1:1:1:2:1"}}))

        def vectors(t=10, base=16, k0=4, prefix="assoc-v1:29M:T10:", *extra):
            return subprocess.run(
                [sys.executable, str(self.pipeline / "gate_n2_plan.py"), "profiles",
                 *extra, str(point), str(t), str(base), str(k0), prefix, "29"],
                capture_output=True, text=True)
        own = vectors()
        self.assertEqual(own.returncode, 0, own.stderr)
        self.assertEqual(own.stdout.splitlines(), [
            "refused survival_weighted: no merged bytes leave levels [0]",
            "STATIC_PROFILE_last_level_emptying=1:1:1:2:1"])
        for other in ((10, 16, 2), (10, 32, 4), (6, 16, 4, "assoc-v1:29M:T6:"),
                      (10, 16, 4, "powerlaw-get95-v1:58M:T10:")):
            with self.subTest(point=other):
                ran = vectors(*other)
                self.assertEqual(ran.returncode, 1)
                self.assertIn("was measured at", ran.stderr)
        # The runs are recorded relative to the point: a remounted or renamed
        # NVMe keeps the vectors valid.
        (Path(self.tmp.name) / "nvme").rename(Path(self.tmp.name) / "remounted")
        point = Path(self.tmp.name) / "remounted" / "T10-b16-k4"
        profiles = point / "profiles.json"
        moved = vectors()
        self.assertEqual(moved.returncode, 0, moved.stderr)
        self.assertEqual(moved.stdout, own.stdout)
        # A later native top-up leaves the vectors valid; a run they were
        # measured from that is gone is refused, not recomputed.
        (point / "29M" / "T10" / "repeat-06" / "native").mkdir(parents=True)
        (point / "29M" / "T10" / "repeat-06" / "native" / "COMPLETED").touch()
        self.assertEqual(vectors().returncode, 0)
        (point / "29M" / "T10" / names[2] / "COMPLETED").unlink()
        ran = vectors()
        self.assertEqual(ran.returncode, 1)
        self.assertIn("it is not recomputed", ran.stderr)
        # No profiles.json: a run step skips; the profiles step waits for five.
        profiles.unlink()
        (point / "29M" / "T10" / "repeat-06" / "native" / "COMPLETED").unlink()
        self.assertEqual(vectors().stdout.splitlines(), [
            f"skipped {name}: no profiles.json at this point"
            for name in ("survival_weighted", "last_level_emptying")])
        ran = vectors(10, 16, 4, "assoc-v1:29M:T10:", "--compute")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("4 settled native runs; the profiles wait for 5", ran.stdout)
        self.assertFalse(profiles.exists())


if __name__ == "__main__":
    unittest.main()
