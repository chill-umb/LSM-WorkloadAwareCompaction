"""Stage 38, one machine's calibration (owner, 2026-11-08):
38_machine_calibration.py's fits (the read prices through stage 18's own
round_record, q-bar from 04's summaries, c_set through 37's c_set, the
compose outputs), the machine base through stage 30, 36's per-workload
q-bar file, and the two shell stages' refusals and dry runs."""

import csv
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import cost_model_v2 as cm
import price_trees
from tests.test_cost_model_v2 import record as schema6
from tests.test_price_sessions import tree

PIPELINE = Path(__file__).resolve().parents[1]
REPO = PIPELINE.parents[1]


def _module(name, file):
    spec = importlib.util.spec_from_file_location(name, PIPELINE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m38 = _module("machine_calibration", "38_machine_calibration.py")
stage30 = _module("provisional_prices_v2_38", "30_provisional_prices_v2.py")
CORE = 1.5e-5
# Three rounds' t_f: median 0.5 us. tree() reads blocks at 1 us each and
# seeks at t_sk = 2 us per run.
ROUND_T_F = (0.6e-6, 0.4e-6, 0.5e-6)


def write_rounds(root: Path, t_fs=ROUND_T_F, skip=None) -> Path:
    for k, t_f in enumerate(t_fs, 1):
        for ratio in price_trees.SIZE_RATIOS:
            arm = tree(ratio, t_f=t_f)["all_open"]
            for bench, text in arm.items():
                if skip == (k, ratio, bench):
                    continue
                d = root / f"round{k}" / f"T{ratio}" / bench
                d.mkdir(parents=True)
                (d / "stdout.txt").write_text(text)
    return root


def base_record(**changes):
    """A stage-18 schema-4 base: core-seconds and money for every read price."""
    seconds = {"c_f": 9e-7, "c_blk": 9e-6, "c_sk": 9e-6, "c_open": 1e-5,
               "c_w": 1.5e-9}
    r = {"schema": 4, "price_per_core_second": CORE, "currency": "USD",
         "core_seconds_per_unit": dict(seconds),
         "core_seconds_spread": {k: {"min": v, "max": v, "values": [v]}
                                 for k, v in seconds.items()},
         **{k: v * CORE for k, v in seconds.items()},
         "c_s": 3e-17, "reopen_timer": {"seconds_per_reopen": 8e-6},
         "db_bench_sha256": "a" * 64, "measured_utc": "2026-10-03T11:42:33+00:00"}
    r.update(changes)
    return r


def summary(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = ["arm", "settle_ok", "throughput_ops_per_second",
            "measured_operations", "size_ratio", "result_directory"]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for row in rows:
            w.writerow({**{"measured_operations": "1000000", "size_ratio": "3",
                           "result_directory": "/r/native"}, **row})
    return path


def native(rate, **extra):
    return {"arm": "native", "settle_ok": "1",
            "throughput_ops_per_second": str(rate), **extra}


def turn_result(root: Path, apply_us, options_bytes=5000.0) -> tuple[Path, Path]:
    root.mkdir(parents=True)
    lines = [{"type": "apply", "ok": 1, "apply_us": us} for us in apply_us]
    lines += [{"type": "apply", "ok": 0, "apply_us": 1e9},   # refused: not counted
              {"type": "turn", "t": 1}]
    (root / "decisions.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in lines))
    report = root.parent / f"{root.name}_report.json"
    report.write_text(json.dumps({"checks": {"c_set": {
        "options_bytes": options_bytes}}}))
    return root, report


def priced6(per_unit, **changes):
    r = schema6(**changes)
    r["core_seconds_per_unit"] = dict(per_unit)
    return r


class ReadsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_round_by_stage_18_and_the_median(self):
        fit = m38.fit_reads(m38.read_rounds(write_rounds(self.root / "reads")))
        self.assertEqual(len(fit["rounds"]), 3)
        self.assertAlmostEqual(fit["core_seconds"]["c_f"] / 0.5e-6, 1, places=6)
        self.assertAlmostEqual(fit["core_seconds"]["c_blk"] / 1e-6, 1, places=6)
        self.assertAlmostEqual(fit["core_seconds"]["c_sk"] / 2e-6, 1, places=6)
        self.assertEqual(sorted(round(v, 12) for v in fit["spread"]["c_f"]["values"]),
                         sorted(round(v, 12) for v in ROUND_T_F))

    def test_a_missing_run_is_refused(self):
        write_rounds(self.root / "reads", skip=(2, 6, "seekrandom"))
        with self.assertRaisesRegex(ValueError, "round2/T6/seekrandom"):
            m38.read_rounds(self.root / "reads")
        with self.assertRaisesRegex(ValueError, "no round"):
            m38.read_rounds(self.root / "empty")

    def test_the_machine_base_replaces_only_the_read_prices(self):
        fit = m38.fit_reads(m38.read_rounds(write_rounds(self.root / "reads")))
        base = base_record()
        out = m38.machine_base(base, fit, "b" * 64)
        self.assertEqual(out["schema"], 4)
        for key in m38.READ_PRICES:
            self.assertEqual(out["core_seconds_per_unit"][key], fit["core_seconds"][key])
            self.assertAlmostEqual(out[key], fit["core_seconds"][key] * CORE)
        for key in ("c_open", "c_w", "c_s", "reopen_timer"):
            self.assertEqual(out[key], base[key])
        self.assertEqual(out["core_seconds_per_unit"]["c_open"], 1e-5)
        self.assertEqual(out["db_bench_sha256"], "b" * 64)
        mc = out["machine_calibration"]
        self.assertEqual(len(mc["rounds"]), 3)
        self.assertIn("c_open", mc["carried_from_base"])
        self.assertNotIn("qbar_reopen_checks", mc["carried_from_base"])
        self.assertEqual(base["c_f"], 9e-7 * CORE)   # the input is not changed

    def test_the_base_must_be_stage_18s(self):
        fit = m38.fit_reads(m38.read_rounds(write_rounds(self.root / "reads")))
        with self.assertRaisesRegex(ValueError, "schema 4 or 5"):
            m38.machine_base(base_record(schema=6), fit, "b" * 64)
        with self.assertRaisesRegex(ValueError, "core-second"):
            m38.machine_base(base_record(price_per_core_second=None), fit, "b" * 64)

    def test_stage_30_takes_the_machine_base(self):
        fit = m38.fit_reads(m38.read_rounds(write_rounds(self.root / "reads")))
        base = m38.machine_base(base_record(), fit, "b" * 64)
        cal = {"source": "test", "core_seconds": {
            **{k: 1e-7 for k in stage30.NEW_PRICES}, "c_ib": 0.0,
            **{k: 1e-3 for k in stage30.JOB_KEYS}},
            "kappa": {"lambda": 0.0, "B": {x: 0.0 for x in cm.STEP_TYPES},
                      "J": {x: {k: 0.1 for k in cm.KINDS} for x in cm.STEP_TYPES},
                      "basis": "test"}}
        out = stage30.build(base, [cal], False, 1000, 100)
        self.assertEqual(out["schema"], 6)
        self.assertAlmostEqual(out["c_f"], fit["core_seconds"]["c_f"] * CORE)
        self.assertAlmostEqual(out["c_w"], 1e-7 * CORE)   # the job fit's, not the base's
        self.assertIn("machine_calibration", out)

    def test_the_reads_command(self):
        write_rounds(self.root / "reads")
        (self.root / "base.json").write_text(json.dumps(base_record()))
        args = [str(PIPELINE / "38_machine_calibration.py"), "reads",
                "--runs", str(self.root / "reads"), "--base", str(self.root / "base.json"),
                "--db-bench-sha256", "c" * 64, "--out", str(self.root / "out.json")]
        done = subprocess.run(["python3", *args], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads((self.root / "out.json").read_text())
        self.assertAlmostEqual(out["core_seconds_per_unit"]["c_sk"] / 2e-6, 1, places=6)
        (self.root / "base.json").write_text(json.dumps(base_record(schema=6)))
        done = subprocess.run(["python3", *args], capture_output=True, text=True)
        self.assertEqual(done.returncode, 1)
        self.assertIn("refused", done.stderr)


class NativesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_q_bar_is_the_settled_natives_throughput(self):
        s = summary(self.root / "W1.csv", [
            native(61234.5, result_directory="/d/W1/native"),
            {"arm": "turn", "settle_ok": "1", "throughput_ops_per_second": "1"}])
        table = m38.qbar_table({"W1": s})
        self.assertEqual(table["W1"]["ops_per_second"], 61234.5)
        self.assertEqual(table["W1"]["result_directory"], "/d/W1/native")

    def test_unsettled_doubled_or_empty_natives_are_refused(self):
        for rows, msg in (([native(5e4, settle_ok="0")], "0 settled"),
                          ([native(5e4), native(6e4)], "2 settled"),
                          ([native(0)], "not positive"),
                          ([native("nan")], "not positive")):
            s = summary(self.root / "x.csv", rows)
            with self.assertRaisesRegex(ValueError, msg):
                m38.native_row(s)

    def test_native_arguments(self):
        self.assertEqual(m38.parse_native(["W2=a.csv"]), {"W2": Path("a.csv")})
        for bad in (["W6=a"], ["W1"], ["W1=a", "W1=b"]):
            with self.assertRaises(ValueError):
                m38.parse_native(bad)


class CsetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_both_arms_calls_pooled_at_this_machines_prices(self):
        a, ra = turn_result(self.root / "turn", [100, 200, 300], options_bytes=4000)
        b, rb = turn_result(self.root / "turn_vdn", [400, 500], options_bytes=6000)
        prices = schema6(price_per_core_second=2.0, c_w=1e-3)
        out = m38.measure_c_set([a, b], [ra, rb], prices, None)
        self.assertEqual(out["calls"], 5)
        self.assertEqual(out["median_us"], 300)
        self.assertEqual(out["options_bytes"], 6000)
        self.assertAlmostEqual(out["c_set"], 300e-6 * 2.0 + 6000 * 1e-3)
        given = m38.measure_c_set([a], [], prices, 100.0)
        self.assertAlmostEqual(given["c_set"], 200e-6 * 2.0 + 100 * 1e-3)
        no_bytes = m38.measure_c_set([a], [], prices, None)
        self.assertAlmostEqual(no_bytes["c_set"], 200e-6 * 2.0)

    def test_no_accepted_call_is_refused(self):
        a, _ = turn_result(self.root / "turn", [])
        with self.assertRaisesRegex(ValueError, "no accepted SetOptions"):
            m38.measure_c_set([a], [], schema6(), None)


class ComposeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        r = self.root
        self.summaries = [f"W1={summary(r / 'W1.csv', [native(60000)])}",
                          f"W2={summary(r / 'W2.csv', [native(190000)])}"]
        (r / "prices.json").write_text(json.dumps(
            priced6({"c_f": 2e-7, "job_l0": 6e-4}, price_per_core_second=2.0, c_w=1e-3)))
        ref = priced6({"c_f": 1e-7, "job_l0": 5e-4, "c_mt": 5e-7})
        ref["kappa"]["J"]["probe"]["l0"] = 0.4
        (r / "ref.json").write_text(json.dumps(ref))
        settings = json.loads((REPO / "config" / "turn_settings.json").read_text())
        (r / "turn_settings.json").write_text(json.dumps(settings))
        self.turn = turn_result(r / "smoke" / "turn", [100, 300])
        (r / "jobs.json").write_text(json.dumps(
            {"l0_merge_span_ops_percentiles": {"1": 800.0, "5": 1200.0, "50": 9000.0},
             "c_cr_combined": False}))

    def tearDown(self):
        self.tmp.cleanup()

    def run38(self, *extra):
        r = self.root
        args = ["python3", str(PIPELINE / "38_machine_calibration.py"), "compose"]
        for s in self.summaries:
            args += ["--native", s]
        args += ["--prices", str(r / "prices.json"), "--reference", str(r / "ref.json"),
                 "--turn-settings", str(r / "turn_settings.json"),
                 "--jobs", str(r / "jobs.json"), "--setting", "T3 128:4",
                 "--out-dir", str(r / "out"), *extra]
        return subprocess.run(args, capture_output=True, text=True)

    def test_every_output(self):
        done = self.run38("--turn-result", str(self.turn[0]),
                          "--turn-report", str(self.turn[1]))
        self.assertEqual(done.returncode, 0, done.stderr)
        out = self.root / "out"
        self.assertEqual(json.loads((out / "qbar.json").read_text()),
                         {"W1": 60000.0, "W2": 190000.0})
        settings = json.loads((out / "turn_settings.machine.json").read_text())
        self.assertAlmostEqual(settings["trainer"]["c_set"], 200e-6 * 2.0 + 5000 * 1e-3)
        self.assertIn("c_set measured", settings["status"])
        cal = json.loads((out / "calibration.json").read_text())
        self.assertAlmostEqual(cal["prices"]["c_f"]["ratio"], 2.0)
        self.assertAlmostEqual(cal["prices"]["job_l0"]["ratio"], 1.2)
        self.assertIsNone(cal["prices"]["c_mt"]["core_seconds"])
        self.assertNotIn("ratio", cal["prices"]["c_mt"])
        self.assertEqual(cal["kappa"]["J.probe"]["reference"]["l0"], 0.4)
        self.assertEqual(cal["l0_merge_span_ops_percentiles"]["5"], 1200.0)
        self.assertEqual(cal["setting"], "T3 128:4")
        report = (out / "report.md").read_text()
        self.assertIn("| W2 | 190,000 |", report)
        self.assertIn("| c_f |", report)

    def test_without_the_turn_smoke_c_set_is_left_as_it_was(self):
        done = self.run38()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("c_set not measured", done.stdout)
        settings = json.loads((self.root / "out" / "turn_settings.machine.json").read_text())
        self.assertEqual(settings["trainer"]["c_set"], 0.0)

    def test_prices_must_be_schema_6(self):
        (self.root / "prices.json").write_text(json.dumps(base_record()))
        done = self.run38()
        self.assertEqual(done.returncode, 1)
        self.assertIn("schema-6", done.stderr)


def stub_db_bench(root: Path) -> Path:
    build = root / "build"
    build.mkdir()
    exe = build / "db_bench"
    exe.write_text("#!/bin/sh\necho '  --rl_host_log_stride (stride)'\n")
    exe.chmod(0o755)
    return build


class ShellStagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def sh(self, script, *args, **env):
        full = {**os.environ, **env}
        return subprocess.run([str(PIPELINE / script), *args], capture_output=True,
                              text=True, env=full, cwd=REPO)

    def test_38_plan_and_refusals(self):
        done = self.sh("38_calibrate_machine.sh", "plan", CAL_T="4",
                       CAL_OPS="W1:20 W4:12", CAL_OUT=str(self.root / "cal"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("W1 native, T4 setting 128:4, 20M operations", done.stdout)
        self.assertIn("W4 native, T4 setting 128:4, 12M operations", done.stdout)
        self.assertNotIn("W2", done.stdout)
        self.assertFalse((self.root / "cal").exists())
        for env, msg in (({}, "CAL_T"), ({"CAL_T": "4", "CAL_OPS": "W7:5"}, "CAL_OPS"),
                         ({"CAL_T": "4", "CAL_PARTS": "natives sing"}, "unknown part"),
                         ({"CAL_T": "4", "CAL_READ_ROUNDS": "4"}, "CAL_READ_ROUNDS")):
            done = self.sh("38_calibrate_machine.sh", "plan", **env)
            self.assertEqual(done.returncode, 5, (env, done.stderr))
            self.assertIn(msg, done.stderr)
        done = self.sh("38_calibrate_machine.sh", CAL_T="4", CAL_OUT=str(self.root / "cal"))
        self.assertEqual(done.returncode, 2)
        self.assertIn("CONFIRM_MACHINE_CALIBRATION", done.stderr)
        done = self.sh("38_calibrate_machine.sh", CAL_T="4", CONFIRM_MACHINE_CALIBRATION="YES",
                       NVME=str(self.root / "db"), BASE_PRICES_FILE=str(self.root / "none"),
                       CAL_OUT=str(self.root / "cal"))
        self.assertEqual(done.returncode, 5)
        self.assertIn("BASE_PRICES_FILE", done.stderr)

    def test_38b_dry_run_puts_the_suite_memtable_on_the_memtable_runs_only(self):
        build = stub_db_bench(self.root)
        out = self.root / "cost"
        done = self.sh("38b_cost_model_runs.sh", CALIB_DRY_RUN="1", OUT=str(out),
                       DBS=str(self.root / "dbs"), CALIB_REPEATS="1",
                       DBBENCH_BUILD_DIR=str(build),
                       CALIB_MT_WRITE_BUFFER="16777216", CALIB_MTFILL_KEYS="7680")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("55 runs", done.stdout)
        self.assertTrue(done.stdout.rstrip().endswith("CALIB_RUNS_EXIT=0"))

        def flags(label):
            text = (out / "runs" / label / "command.txt").read_text()
            return [f for f in text.split() if f.startswith("--write_buffer_size=")
                    or f.startswith("--writes=")]

        self.assertEqual(flags("r1-quiet-mtfill")[-1], "--write_buffer_size=16777216")
        self.assertIn("--writes=7680", flags("r1-quiet-mtfill"))
        self.assertEqual(flags("r1-quiet-mtempty")[-1], "--write_buffer_size=16777216")
        self.assertEqual(flags("r1-quiet-hit"), ["--write_buffer_size=2097152"])
        done = self.sh("38b_cost_model_runs.sh", CALIB_DRY_RUN="1", OUT=str(self.root / "c2"),
                       DBS=str(self.root / "dbs"), DBBENCH_BUILD_DIR=str(build),
                       CALIB_MT_WRITE_BUFFER="16M")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("CALIB_MT_WRITE_BUFFER", done.stderr)

    def test_36_takes_each_workloads_q_bar_from_the_file(self):
        q = self.root / "qbar.json"
        q.write_text(json.dumps({"W1": 61000.5, "W3": 95000}))
        done = self.sh("36_rl_batch.sh", "plan", RL_QBAR_FILE=str(q),
                       RL_WORKLOADS="W1 W3", RL_MODES="write", RL_T="3")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("q-bar 61000.5 W1 T3", done.stdout)
        self.assertIn("q-bar 95000.0 W3 T3", done.stdout)
        done = self.sh("36_rl_batch.sh", "plan", RL_WORKLOADS="W1", RL_T="3")
        self.assertIn("q-bar 50000 W1", done.stdout)
        done = self.sh("36_rl_batch.sh", RL_QBAR_FILE=str(q), RL_WORKLOADS="W1 W2",
                       NVME=str(self.root / "db"))
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("no q-bar for W2", done.stderr + done.stdout)
        self.assertFalse((self.root / "db" / "suite-dbs").exists())


class SandboxTest(unittest.TestCase):
    """38's reads part and its read fit through the shell stage, on a copy of
    the pipeline with the stand-in db_bench (fixtures/chain/fake_db_bench.py:
    0.5 us a filter probe, 1 us a block read, 2 us a run seek)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.base = base
        self.repo = base / "repo"
        shutil.copytree(PIPELINE, self.repo / "scripts" / "dbbench_pipeline",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(REPO / "config", self.repo / "config")
        fake = PIPELINE / "tests" / "fixtures" / "chain" / "fake_db_bench.py"
        for path, text in (
                (self.repo / "build-dbbench" / "db_bench",
                 f'#!/usr/bin/env bash\nexec "{sys.executable}" "{fake}" "$@"\n'),
                (base / "venv" / "bin" / "python",
                 f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n')):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            path.chmod(0o755)
        (base / "base.json").write_text(json.dumps(base_record()))
        (base / "ref.json").write_text(json.dumps(priced6({"c_f": 1e-7})))

    def test_reads_then_a_fit_without_the_cost_runs(self):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CAL_", "PRICE", "DBBENCH_", "PREFLIGHT_"))}
        env.update(DBBENCH_CPUS="", CONTROLLER_CPUS="", PYTHON_VENV=str(self.base / "venv"),
                   CONFIRM_MACHINE_CALIBRATION="YES", NVME=str(self.base / "db"),
                   CAL_T="3", CAL_OUT=str(self.base / "cal"), CAL_PARTS="reads fit",
                   CAL_OPS="W1:25", BASE_PRICES_FILE=str(self.base / "base.json"),
                   PRICES_FILE=str(self.base / "ref.json"))
        done = subprocess.run(
            ["bash", str(self.repo / "scripts" / "dbbench_pipeline" / "38_calibrate_machine.sh")],
            cwd=self.repo, env=env, capture_output=True, text=True, timeout=600)
        cal = self.base / "cal"
        # The cost part was not asked for, so the fit stops at the foreground
        # fit, after the read prices, and the stage says so.
        self.assertEqual(done.returncode, 5, done.stdout[-3000:] + done.stderr[-3000:])
        self.assertIn("fit FAILED", done.stdout)
        self.assertTrue(done.stdout.rstrip().endswith("MACHINE_CAL_EXIT=5"))
        self.assertTrue((cal / "reads" / "DONE").exists())
        self.assertFalse((self.base / "db" / "machine-cal-dbs" / "price-trees").exists())
        for ratio in price_trees.SIZE_RATIOS:
            self.assertTrue((cal / "reads" / "build" / f"T{ratio}" / "BUILT").exists())
            command = (cal / "reads" / "round1" / f"T{ratio}" / "readmissing" /
                       "command.txt").read_text()
            self.assertIn("--open_files=-1", command)
            self.assertIn(f"--max_bytes_for_level_multiplier={ratio}", command)
        self.assertFalse((cal / "reads" / "round4").exists())
        out = json.loads((cal / "base.machine.json").read_text())
        for key, value in (("c_f", 0.5e-6), ("c_blk", 1e-6), ("c_sk", 2e-6)):
            self.assertAlmostEqual(out["core_seconds_per_unit"][key] / value, 1, places=6)
        self.assertIn("db_bench_identity=", (cal / "setup.env").read_text())
        self.assertTrue((cal / "machine.json").exists())


if __name__ == "__main__":
    unittest.main()
