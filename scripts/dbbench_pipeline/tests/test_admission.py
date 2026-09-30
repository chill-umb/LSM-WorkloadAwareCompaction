"""19_admission_test.py (PATHWAYS G §4): the n_min simulation's two
properties, the size of the every-statistic test, the interval levels, the
KS figure, the membership rules, the config refusals, and turnovers cut from
host-log job records."""
import importlib.util
import io
import json
import random
import shutil
import statistics
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PIPELINE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "admission_test", PIPELINE / "19_admission_test.py")
adm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adm)

_rng = random.Random(7)
# One run of 200 turnovers, three statistics with one N(0, 1) value each.
REFERENCE = [[{s: [_rng.gauss(0, 1)] for s in "abc"} for _ in range(200)]]
WITH_OMEGA = [[{**t, "omega": [0.1]} for t in REFERENCE[0]]]
GEOMETRY = {"base": 16 * 2**20, "ratio": 2.0, "sst": 512 * 2**10}


def config(margin, **extra):
    return {"margins": {s: margin for s in "abc"}, "block_length": 3,
            "replicates": 200, **extra}


class SimulationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enough = adm.pseudo_level_rates(REFERENCE, 80, config(1.5), 60,
                                            random.Random(1))

    def test_two_resamples_of_one_level_pass(self):
        self.assertGreaterEqual(self.enough["pass_rate_identical"], 0.8)

    def test_each_statistic_shifted_to_its_margin_rarely_passes(self):
        # Requiring every statistic to pass, the worst single-statistic
        # shift bounds the combined test's size (at most 5%).
        for stat, rate in self.enough["pass_rate_shifted"].items():
            self.assertLessEqual(rate, 0.05, stat)
        self.assertTrue(self.enough["sufficient"])

    def test_too_few_turnovers_are_not_sufficient(self):
        few = adm.pseudo_level_rates(REFERENCE, 40, config(1.0), 40,
                                     random.Random(1))
        self.assertLess(few["pass_rate_identical"], 0.8)
        self.assertFalse(few["sufficient"])

    def test_the_thresholds_are_exactly_0_8_and_0_05(self):
        def rates(identical_passes, shifted_passes):
            calls = iter([True] * identical_passes + [False] * (100 - identical_passes)
                         + [True] * shifted_passes + [False] * (300 - shifted_passes))
            with mock.patch.object(adm, "compare",
                                   lambda *a: {"passed": next(calls)}):
                return adm.pseudo_level_rates(REFERENCE, 10, config(1.0), 100,
                                              random.Random(1))
        self.assertTrue(rates(80, 5)["sufficient"])
        self.assertFalse(rates(79, 0)["sufficient"])
        self.assertFalse(rates(100, 6)["sufficient"])  # 6 of 100 on stat a


class CompareTest(unittest.TestCase):
    def test_identical_levels_pass_and_a_distant_one_fails(self):
        rng = random.Random(3)
        self.assertTrue(adm.compare(REFERENCE, REFERENCE, config(1.0), rng)["passed"])
        far = adm.shifted(REFERENCE, "b", 3.0)
        result = adm.compare(far, REFERENCE, config(1.0), rng)
        self.assertFalse(result["passed"])
        self.assertFalse(result["statistics"]["b"]["passed"])
        self.assertTrue(result["statistics"]["a"]["passed"])

    def test_intervals_are_ninety_percent(self):
        # Two N(0, 1) samples of 200, block length 1: the mean difference's
        # interval half-width over its standard error is 1.645 at 90% and
        # 1.28 at 80%.
        rng = random.Random(5)
        a = [[{"z": [rng.gauss(0, 1)]} for _ in range(200)]]
        b = [[{"z": [rng.gauss(0, 1)]} for _ in range(200)]]
        out = adm.compare(a, b, {"margins": {"z": 10.0}, "block_length": 1,
                                 "replicates": 2000}, random.Random(1))
        low, high = out["statistics"]["z"]["intervals"]["mean"]
        error = (statistics.variance([t["z"][0] for t in a[0]]) / 200 +
                 statistics.variance([t["z"][0] for t in b[0]]) / 200) ** 0.5
        self.assertGreater((high - low) / 2 / error, 1.46)
        self.assertLess((high - low) / 2 / error, 1.85)

    def test_block_resample_keeps_run_lengths(self):
        runs = [[{"i": [i]} for i in range(7)], [{"i": [i]} for i in range(3)]]
        out = adm.block_resample(runs, 4, random.Random(1))
        self.assertEqual(len(out), 10)
        # Blocks never cross runs: the last 3 come from the 3-long run whole.
        self.assertEqual([t["i"][0] for t in out[7:]], [0, 1, 2])

    def test_omega_bound_is_the_upper_95_not_the_mean(self):
        # Waits alternating 5 and 15 in blocks: mean 10, upper bound above 11.
        runs = [[{"omega": [5.0 if (i // 10) % 2 else 15.0]} for i in range(60)]]
        bound = adm.omega_upper_bound(runs, config(1.0, block_length=10),
                                      random.Random(1))
        self.assertGreater(bound, 11.0)


class KolmogorovSmirnovTest(unittest.TestCase):
    def test_reproduces_the_median_bound_at_30_turnovers(self):
        # The distance moves in steps of 1/30, so the median lands on 0.400,
        # 0.417 or 0.433 depending on the draw.
        rng = random.Random(1)
        bounds = [adm.ks_upper_bound([rng.random() for _ in range(30)],
                                     [rng.random() for _ in range(30)], 300, rng)
                  for _ in range(100)]
        self.assertAlmostEqual(statistics.median(bounds), 0.43, delta=0.04)


class MembershipTest(unittest.TestCase):
    def config(self, **extra):
        return {**config(1.5), "reference_level": 2, "omega_max": 10.0,
                "n_min": 150, "seed": 1, **extra}

    def test_pool_of_identical_levels(self):
        result = adm.membership({2: WITH_OMEGA, 3: WITH_OMEGA}, self.config(),
                                GEOMETRY)
        self.assertEqual(result["pool"], [2, 3])
        self.assertIn("ks_upper_95", result["levels"][3])

    def test_undecided_candidate_leaves_no_pool(self):
        result = adm.membership({2: WITH_OMEGA, 3: [WITH_OMEGA[0][:100]]},
                                self.config(), GEOMETRY)
        self.assertEqual(result["levels"][2]["decision"], "reference")
        self.assertEqual(result["levels"][3]["decision"], "undecided")
        self.assertEqual(result["pool"], [])

    def test_reference_below_n_min_decides_nothing(self):
        result = adm.membership({2: [WITH_OMEGA[0][:100]], 3: WITH_OMEGA},
                                self.config(), GEOMETRY)
        self.assertEqual(result["levels"][3]["decision"], "undecided")

    def test_slot_wait_above_its_bound_refuses(self):
        slow = [[{**t, "omega": [20.0]} for t in REFERENCE[0]]]
        result = adm.membership({2: WITH_OMEGA, 3: slow}, self.config(),
                                GEOMETRY)
        self.assertEqual(result["levels"][3]["decision"], "refused")

    def test_fill_margin_below_file_granularity_is_refused(self):
        # F_sst / C_2 = 512 KiB / 32 MiB = 0.0156.
        for stat in adm.FILL_STATISTICS:
            with self.assertRaisesRegex(ValueError, "granularity", msg=stat):
                adm.membership({2: REFERENCE, 3: REFERENCE},
                               self.config(margins={stat: 0.01}), GEOMETRY)


class ConfigTest(unittest.TestCase):
    GOOD = {"reference_level": 2, "margins": {s: 0.1 for s in adm.STATISTICS},
            "omega_max": 1.0, "block_length": 3, "k": 4, "n_min": 30,
            "replicates": 1000, "seed": 1}

    def test_valid_config(self):
        adm.validate_config(self.GOOD)

    def test_refusals(self):
        margins = dict(self.GOOD["margins"])
        del margins["rho_tilde"]
        for bad in ({"margins": margins}, {"block_length": 0},
                    {"k": 2.5}, {"replicates": True}, {"omega_max": 0},
                    {"omega_max": float("inf")},
                    {"margins": {**self.GOOD["margins"], "released": -1}},
                    {"margins": {**self.GOOD["margins"],
                                 "released": float("inf")}}):
            with self.assertRaises(ValueError, msg=bad):
                adm.validate_config({**self.GOOD, **bad})
        with self.assertRaisesRegex(ValueError, "seed"):
            adm.validate_config({k: v for k, v in self.GOOD.items() if k != "seed"})


def job(job_id, start, op, s, o, x, trivial=0, due=0, t_us=None):
    base = {"job": job_id, "start_level": start, "output_level": start + 1,
            "trivial": trivial, "s": s, "o": o, "due_since_us": due}
    t = op if t_us is None else t_us
    return [{"type": "job_begin", "t_us": t, "op": op, **base},
            {"type": "job_end", "t_us": t + 10, "op": op + 10, "x": x,
             "ok": 1, **base}]


def turnovers(first=(200, 700), extra=(), after=()):
    """Level 2 with C_2 = 1000 bytes and k = 2. Net bytes landing in L2:
    first[1] - first[0] (job 1, X - O), 600 (job 3, a trivial move), 900
    (job 5). L2 releases job 2 (merge, S 300, O 100, X 350, due at op 25)
    and job 4 (trivial, S 200, due at op 65). Operation counts equal t_us.
    `extra` records go in op order; `after` follows drain_start."""
    records = [{"type": "header"},
               {"type": "stamp", "name": "measure_start", "t_us": 0, "op": 0}]
    records += job(1, 1, 10, 600, *first)
    records += job(2, 2, 30, 300, 100, 350, due=25)
    records += job(3, 1, 50, 600, 0, 0, trivial=1)
    records += job(4, 2, 70, 200, 0, 0, trivial=1, due=65)
    records += list(extra)
    records += job(5, 1, 90, 1000, 100, 1000)
    records.sort(key=lambda r: r["op"] if "op" in r else -1)
    records.append({"type": "stamp", "name": "drain_start", "t_us": 120,
                    "op": 120})
    records += list(after)
    fills = {1: [0, 0, 0.3], 2: [0, 0, 0.8], 3: [0, 0, 0.7],
             4: [0, 0, 1.5], 5: [0, 0, 0.9]}
    return adm.level_turnovers(records, fills, 2, 1000.0, 2)


class TurnoverTest(unittest.TestCase):
    """Default: L2's turnovers end at ops 60 (500 + 600 landed) and 100."""

    def setUp(self):
        self.turnovers, self.empty = turnovers()

    def test_boundaries_and_per_turnover_statistics(self):
        first, second = self.turnovers
        self.assertEqual(self.empty, 0)
        self.assertEqual(first["released"], [0.3])
        self.assertAlmostEqual(first["passthrough_overlap"][0], 350 / 300)
        self.assertAlmostEqual(first["rho_tilde"][0], 250 / 300)
        self.assertAlmostEqual(first["inflow_ratio"][0], 50 / 60)
        self.assertEqual(second["released"], [0.2])
        self.assertEqual(second["passthrough_overlap"], [0.0])
        self.assertEqual(second["rho_tilde"], [1.0])
        self.assertAlmostEqual(second["inflow_ratio"][0], 1.25)
        self.assertEqual((first["jobs"], second["jobs"]), ([1], [1]))

    def test_boundaries_count_net_bytes(self):
        # Job 1 with O 500, X 900 lands 400; then 1000, 1900: one turnover.
        # Counting gross X (900, 1500, 2400) would give two.
        self.assertEqual(len(turnovers(first=(500, 900))[0]), 1)

    def test_fills(self):
        first, second = self.turnovers
        self.assertEqual(first["fill_at_release"], [0.8])
        self.assertEqual(second["fill_at_release"], [1.5])
        # Sampled at ops 0 (no snapshot yet) and 30; then at 60 and 80.
        self.assertEqual(first["fill_sampled"], [0.8])
        self.assertEqual(second["fill_sampled"], [0.7, 1.5])

    def test_slot_wait_in_decision_intervals(self):
        # Each waits 5 operations; N_2 = 50 operations, k = 2, so 0.2.
        first, second = self.turnovers
        self.assertAlmostEqual(first["omega"][0], 0.2)
        self.assertAlmostEqual(second["omega"][0], 0.2)

    def test_wait_starts_at_the_previous_job_end(self):
        # Job 6 from L2 is due at op 72, but job 4 runs until op 80; it
        # starts at 85: 5 operations of wait, not 13.
        second = turnovers(extra=job(6, 2, 85, 10, 0, 0, trivial=1, due=72))[0][1]
        self.assertAlmostEqual(second["omega"][1], 0.2)
        self.assertEqual(second["jobs"], [2])
        self.assertAlmostEqual(second["job_gap"][0], 15 / 50)

    def test_a_job_not_due_adds_no_wait(self):
        second = turnovers(extra=job(6, 2, 85, 10, 0, 0, trivial=1))[0][1]
        self.assertEqual(len(second["omega"]), 1)

    def test_jobs_starting_at_one_operation(self):
        # A write stop holds the client: job 4 (due at 65) and job 6 (due
        # at 66) both start at op 70, job 4 first on the steady clock. Job 4
        # is still running, so job 6 waits from its own due point: 4 ops.
        tied = job(6, 2, 70, 10, 0, 0, trivial=1, due=66, t_us=71)
        second = turnovers(extra=tied)[0][1]
        self.assertEqual(second["jobs"], [2])
        self.assertEqual([round(w, 9) for w in second["omega"]], [0.2, 0.16])

    def test_previous_end_is_found_on_the_steady_clock(self):
        # A write stop: job 7 ends at op 70 (t 75) and job 6, due at 66,
        # starts at op 70 (t 80). Job 7 ended first, so job 6 waited from
        # op 70 to op 70: no wait, not 4 operations.
        job7 = job(7, 2, 62, 10, 0, 0, trivial=1)
        job7[1].update(op=70, t_us=75)
        extra = job7 + job(6, 2, 70, 10, 0, 0, trivial=1, due=66, t_us=80)
        second = turnovers(extra=extra)[0][1]
        self.assertEqual([round(w, 9) for w in second["omega"]], [0.2, 0.0])

    def test_a_job_still_running_is_not_waited_for(self):
        # Job 6, due at op 72, starts at 75 while job 4 runs until 80 (two
        # slots): its wait is 3 ops from its due point, never negative.
        second = turnovers(extra=job(6, 2, 75, 10, 0, 0, trivial=1, due=72))[0][1]
        self.assertAlmostEqual(second["omega"][1], 3 * 2 / 50)

    def test_a_job_landing_two_targets_leaves_no_empty_turnover(self):
        # Job 7 lands 2000 at op 101, after job 5's 900 at op 100: the
        # cumulative 4000 crosses 3000 and 4000 at op 101, so the
        # boundaries are 60, 100, 101, 101 and one turnover is empty.
        records, empty = turnovers(extra=job(7, 1, 91, 2000, 0, 2000))
        self.assertEqual(empty, 1)
        self.assertTrue(all(t["inflow_ratio"][0] > 0 for t in records))

    def test_jobs_after_drain_start_are_ignored(self):
        self.assertEqual(len(turnovers(after=job(8, 1, 130, 5000, 0, 5000))[0]),
                         len(self.turnovers))


class MainTest(unittest.TestCase):
    """main() on copies of the evaluator fixture (L0 -> L1 jobs, C_1 of
    1000 bytes): one configuration, m = 1, consistent host logs only."""
    FIXTURE = PIPELINE / "tests" / "fixtures" / "evaluator" / "1M" / "T2" / "native"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / "admission.json"
        self.config.write_text(json.dumps({**ConfigTest.GOOD,
                                           "reference_level": 1}))

    def run_dir(self, name, fingerprint=None, drop=None):
        path = self.root / name
        shutil.copytree(self.FIXTURE, path)
        if fingerprint:
            meta = path / "metadata.env"
            meta.write_text(meta.read_text().replace(
                "assoc-v1:1M:T2:k64:v960:fixture", fingerprint))
        if drop:
            log = path / "host_log.jsonl"
            log.write_text("".join(l for l in log.read_text().splitlines(True)
                                   if drop not in l))
        return path

    def main(self, *runs):
        argv = ["19", *map(str, runs), "--config", str(self.config),
                "--levels", "1", "--output", str(self.root / "out.json")]
        with mock.patch("sys.argv", argv), \
                mock.patch("sys.stdout", io.StringIO()):
            return adm.main()

    def test_one_configuration_is_scored(self):
        self.assertEqual(self.main(self.run_dir("a"), self.run_dir("b")), 0)
        report = json.loads((self.root / "out.json").read_text())
        self.assertEqual(report["empty_turnovers"], {"1": 0})
        self.assertEqual(report["levels"]["1"]["turnovers"], 2)

    def test_refusals(self):
        cases = (([self.run_dir("h", drop='"type":"header"')], "host log"),
                 ([self.run_dir("m", fingerprint="assoc-v1:1M:T2:dio0:ltm1x2")],
                  "m = 1"),
                 ([self.run_dir("x"), self.run_dir("y", fingerprint="other")],
                  "several configurations"))
        for runs, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(SystemExit, message):
                    self.main(*runs)
        self.config.write_text(json.dumps({**ConfigTest.GOOD, "k": 0}))
        with self.assertRaisesRegex(SystemExit, "k must be"):
            self.main(self.run_dir("z"))


if __name__ == "__main__":
    unittest.main()
