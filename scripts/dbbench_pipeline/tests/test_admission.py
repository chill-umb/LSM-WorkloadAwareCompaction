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
PREREGISTERED = PIPELINE.parents[1] / "config" / "admission_test.json"
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


class PreregisteredConfigTest(unittest.TestCase):
    """config/admission_test.json, PREREGISTRATION D-16."""

    def setUp(self):
        self.raw = json.loads(PREREGISTERED.read_text())

    def test_it_validates_at_every_t(self):
        for ratio in (2.0, 6.0, 10.0):
            config = adm.for_size_ratio(self.raw, ratio)
            adm.validate_config(config)
            # The fill margins clear the file granularity of L2 (G §4).
            floor = 512 * 2**10 / (16 * 2**20 * ratio)
            for stat in adm.FILL_STATISTICS:
                self.assertGreater(config["margins"][stat], floor)
        self.assertAlmostEqual(
            adm.for_size_ratio(self.raw, 10.0)["margins"]["passthrough_overlap"], 1.1)

    def test_every_rung_loads_2_9_million_keys(self):
        loads = {adm.load_operations(r) for r in self.raw["rungs"]}
        self.assertEqual(loads, {2_900_000})
        self.assertEqual(adm.mixgraph_operations(self.raw["pilot"]["rung"]),
                         26_100_000)

    def test_a_t_without_margins_is_refused(self):
        with self.assertRaisesRegex(ValueError, "T=14"):
            adm.for_size_ratio(self.raw, 14.0)

    def test_run_length_and_n_min_refusals(self):
        good = adm.for_size_ratio(self.raw, 2.0)
        rungs = good["rungs"]
        for bad, message in (
                ({"n_min": 30}, "exactly one of n_min"),
                ({"n_min_rule": {"grid": [40, 20], "trials": 10}}, "increasing"),
                ({"n_min_rule": {"grid": [20], "trials": 0}}, "trials"),
                ({"rungs": [rungs[0], {"size_millions": 10, "load_percent": 5}]},
                 "same number of keys"),
                ({"rungs": [rungs[1], rungs[0]]}, "lengthen"),
                ({"rungs": [{"size_millions": 10, "load_percent": 0}]}, "1-99")):
            with self.assertRaisesRegex(ValueError, message, msg=bad):
                adm.validate_config({**good, **bad})
        without = {k: v for k, v in good.items() if k != "rungs"}
        with self.assertRaisesRegex(ValueError, "together"):
            adm.validate_config(without)


class NMinRuleTest(unittest.TestCase):
    def test_the_smallest_sufficient_grid_value(self):
        seen = []

        def rates(reference, n, config, trials, rng):
            seen.append((n, trials))
            return {"n": n, "sufficient": n >= 40}
        with mock.patch.object(adm, "pseudo_level_rates", rates):
            n_min, tried = adm.choose_n_min(
                REFERENCE, {"n_min_rule": {"grid": [20, 30, 40, 60], "trials": 7}},
                random.Random(1))
        self.assertEqual(n_min, 40)
        self.assertEqual(seen, [(20, 7), (30, 7), (40, 7)])
        self.assertEqual(len(tried), 3)

    def test_no_sufficient_value_leaves_every_candidate_undecided(self):
        with mock.patch.object(adm, "pseudo_level_rates",
                               lambda *a: {"sufficient": False}):
            n_min, _ = adm.choose_n_min(
                REFERENCE, {"n_min_rule": {"grid": [20], "trials": 1}},
                random.Random(1))
        self.assertIsNone(n_min)
        settings = {**config(1.5), "reference_level": 2, "omega_max": 10.0,
                    "n_min": None, "seed": 1}
        result = adm.membership({2: WITH_OMEGA, 3: WITH_OMEGA}, settings, GEOMETRY)
        self.assertEqual(result["levels"][3]["decision"], "undecided")
        self.assertIn("no n_min", result["levels"][3]["reason"])


    def test_values_above_half_the_reference_are_not_tried(self):
        # 50 reference turnovers: 20 and 25 fit, 30 would overlap too much.
        seen = []

        def rates(reference, n, config, trials, rng):
            seen.append(n)
            return {"n": n, "sufficient": False}
        with mock.patch.object(adm, "pseudo_level_rates", rates):
            n_min, _ = adm.choose_n_min(
                [REFERENCE[0][:30], REFERENCE[0][30:50]],
                {"n_min_rule": {"grid": [20, 25, 30, 60], "trials": 1}},
                random.Random(1))
        self.assertEqual((n_min, seen), (None, [20, 25]))

    def test_a_reference_without_turnovers_has_no_n_min(self):
        self.assertEqual(adm.choose_n_min(
            [[], []], {"n_min_rule": {"grid": [20], "trials": 1}},
            random.Random(1)), (None, []))


class RunLengthTest(unittest.TestCase):
    RUNGS = [{"size_millions": 10, "load_percent": 29},
             {"size_millions": 29, "load_percent": 10},
             {"size_millions": 58, "load_percent": 5}]
    CONFIG = {"reference_level": 2, "n_turn": 10, "rungs": RUNGS}

    def per_level(self, **counts):
        return {int(level[1:]): [[{}] * n for n in runs]
                for level, runs in counts.items()}

    def test_the_deepest_level_not_refused_at_the_slowest_run(self):
        levels = {2: {"decision": "reference"}, 3: {"decision": "undecided"},
                  4: {"decision": "refused"}}
        # L3: 4 and 5 turnovers in 1M mixgraph operations each; the slower
        # run needs 2.5M for 10. L4 is refused, so it does not count.
        out = adm.run_length(levels, self.per_level(L2=(40, 40), L3=(4, 5),
                                                    L4=(1, 1)),
                             [1_000_000, 1_000_000], self.CONFIG)
        self.assertEqual(out["level"], 3)
        self.assertAlmostEqual(out["required_mixgraph_operations"], 2_500_000)
        self.assertEqual(out["rung"], self.RUNGS[0])  # 7.1M mixgraph ops

    def test_a_longer_rung_when_the_level_is_slow(self):
        levels = {2: {"decision": "reference"}, 3: {"decision": "admitted"}}
        out = adm.run_length(levels, self.per_level(L2=(9, 9), L3=(1, 2)),
                             [1_000_000, 1_000_000], self.CONFIG)
        self.assertEqual(out["rung"], self.RUNGS[1])  # 10M needed, 26.1M given

    def test_the_reference_sets_the_length_when_nothing_else_is_left(self):
        levels = {2: {"decision": "refused"}, 3: {"decision": "refused"}}
        out = adm.run_length(levels, self.per_level(L2=(20, 20), L3=(1, 1)),
                             [1_000_000, 1_000_000], self.CONFIG)
        self.assertEqual(out["level"], 2)

    def test_no_rung_long_enough(self):
        levels = {2: {"decision": "reference"}, 3: {"decision": "undecided"}}
        out = adm.run_length(levels, self.per_level(L2=(9, 9), L3=(0, 3)),
                             [1_000_000, 1_000_000], self.CONFIG)
        self.assertIsNone(out["rung"])
        self.assertIn("no rung", out["note"])


class SettledTreeTest(unittest.TestCase):
    TARGETS = [0, 10, 100, 1000, 10000]

    def test_first_release_at_or_after_n_w(self):
        events = [{"time_micros": t, "occupancy_bytes": o,
                   "nominal_target_bytes": self.TARGETS}
                  for t, o in ((5, [1, 1, 1, 1, 1]), (10, [0, 3, 2, 600, 0]),
                               (20, [0, 3, 2, 9, 4]))]
        self.assertEqual(adm.settled_tree(events, 10), (3, 0.6))
        self.assertIsNone(adm.settled_tree(events, 30))

    def test_candidates_follow_the_scope_decision(self):
        # L = 8 at T=2: L2..L6, and L7 only when L8 is near its target.
        self.assertEqual(adm.candidate_levels(8, 0.3, 2, 0.5), [2, 3, 4, 5, 6])
        self.assertEqual(adm.candidate_levels(8, 0.5, 2, 0.5),
                         [2, 3, 4, 5, 6, 7])
        # L = 4 at T=10 with L4 far below target: L2 alone.
        self.assertEqual(adm.candidate_levels(4, 0.06, 2, 0.5), [2])

    def test_a_tree_too_shallow_for_the_reference_is_refused(self):
        with self.assertRaisesRegex(ValueError, "too shallow"):
            adm.candidate_levels(3, 0.1, 2, 0.5)


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

    def run_dir(self, name, fingerprint="assoc-v1:1M:T2:k64:v960:sst10:fixture",
                drop=None):
        """A copy of the fixture. Its fingerprint gains an sst field (10 B
        files, 1% of C_1), which 19 needs for the granularity floor."""
        path = self.root / name
        shutil.copytree(self.FIXTURE, path)
        meta = path / "metadata.env"
        meta.write_text(meta.read_text().replace(
            "assoc-v1:1M:T2:k64:v960:fixture", fingerprint))
        if drop:
            log = path / "host_log.jsonl"
            log.write_text("".join(l for l in log.read_text().splitlines(True)
                                   if drop not in l))
        return path

    def main(self, *runs, levels=("1",)):
        argv = ["19", *map(str, runs), "--config", str(self.config),
                "--output", str(self.root / "out.json")]
        if levels:
            argv += ["--levels", *levels]
        with mock.patch("sys.argv", argv), \
                mock.patch("sys.stdout", io.StringIO()):
            return adm.main()

    def test_one_configuration_is_scored(self):
        self.assertEqual(self.main(self.run_dir("a"), self.run_dir("b")), 0)
        report = json.loads((self.root / "out.json").read_text())
        self.assertEqual(report["empty_turnovers"], {"1": 0})
        self.assertEqual(report["levels"]["1"]["turnovers"], 2)

    def test_d16_config_end_to_end(self):
        """Margins per T, n_min by its rule and the run-length rung. The
        fixture's L1 completes one turnover per run in 710 mixgraph
        operations, so 20,000 turnovers need 14.2M: the second rung."""
        raw = json.loads(PREREGISTERED.read_text())
        raw.update(reference_level=1, replicates=20, n_turn=20_000,
                   n_min_rule={"grid": [1], "trials": 2})
        self.config.write_text(json.dumps(raw))
        runs = [self.run_dir(name) for name in "ab"]
        for run in runs:
            meta = run / "metadata.env"
            meta.write_text(meta.read_text().replace(
                "load_operations=290", "load_operations=2900000"))
        self.assertEqual(self.main(*runs), 0)
        report = json.loads((self.root / "out.json").read_text())
        self.assertEqual(report["config"]["margins"]["passthrough_overlap"], 0.3)
        # The reference alone: nothing to decide, so no n_min simulation.
        self.assertIsNone(report["config"]["n_min"])
        self.assertNotIn("n_min_rule_simulation", report)
        length = report["run_length"]
        self.assertEqual(length["mixgraph_operations_per_run"], [710, 710])
        self.assertEqual(length["turnovers_per_run"], [1, 1])
        self.assertEqual(length["rung"], {"size_millions": 29, "load_percent": 10})
        # With L2 as a candidate the rule runs. L2 completes no turnover, so
        # it stays undecided, governs the length, and no rung reaches it.
        self.assertEqual(self.main(*runs, levels=("1", "2")), 0)
        report = json.loads((self.root / "out.json").read_text())
        self.assertEqual(len(report["n_min_rule_simulation"]), 1)
        self.assertEqual(report["levels"]["2"]["decision"], "undecided")
        length = report["run_length"]
        self.assertEqual((length["level"], length["required_mixgraph_operations"],
                          length["rung"]), (2, None, None))

    def released(self, name, occupancy, targets=(0, 1000, 2000, 4000)):
        """A fixture copy with one compaction_release after n_w (wall
        5.0e9), which fixes its settled tree."""
        path = self.run_dir(name)
        event = {"time_micros": 5000500000, "job": 99,
                 "event": "compaction_release", "occupancy_bytes": occupancy,
                 "nominal_target_bytes": list(targets)}
        with (path / "rocksdb_LOG.txt").open("a") as handle:
            handle.write("2026/09/30-10:00:02.500000 3 EVENT_LOG_v1 "
                         + json.dumps(event) + "\n")
        return path

    def derive(self, *runs):
        self.config.write_text(json.dumps({
            **ConfigTest.GOOD, "reference_level": 1,
            "last_level_near_target": 0.5}))
        self.main(*runs, levels=())
        return json.loads((self.root / "out.json").read_text())

    def test_candidates_derived_from_the_settled_tree(self):
        # L = 3; L3 at 300/4000 of its target: L2 is not a candidate.
        far = self.derive(self.released("a", [0, 500, 800, 300]))
        self.assertEqual(list(far["levels"]), ["1"])
        self.assertEqual(far["settled_depth"], 3)
        self.assertEqual(far["settled_tree_per_run"], [[3, 0.075]])
        # L3 at 2400/4000: L2 (= L-1) joins, and is undecided (no turnovers).
        near = self.derive(self.released("b", [0, 500, 800, 2400]))
        self.assertEqual(list(near["levels"]), ["1", "2"])
        self.assertEqual(near["levels"]["2"]["decision"], "undecided")

    def test_derivation_refusals(self):
        cases = (([self.released("d1", [0, 5, 8, 300]),
                   self.released("d2", [0, 5, 8, 300, 7], (0, 1, 2, 4, 8))],
                  "differ in depth"),
                 ([self.run_dir("n")], "no compaction_release after n_w"),

                 ([self.released("s", [0, 500], (0, 1000))], "too shallow"))
        for runs, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(SystemExit, message):
                    self.derive(*runs)

    def test_one_candidate_set_per_cell_from_the_mean_fill(self):
        # D-16 §3: runs on either side of 0.5 do not split the cell. L3 at
        # 0.075 and 0.6 of target: mean 0.34, so L2 is not a candidate; at
        # 0.45 and 0.6: mean 0.525, so it is.
        split = self.derive(self.released("m1", [0, 5, 8, 300]),
                            self.released("m2", [0, 5, 8, 2400]))
        self.assertEqual(list(split["levels"]), ["1"])
        self.assertAlmostEqual(split["last_level_mean_fill"], 0.3375)
        near = self.derive(self.released("m3", [0, 5, 8, 1800]),
                           self.released("m4", [0, 5, 8, 2400]))
        self.assertEqual(list(near["levels"]), ["1", "2"])

    def test_runs_03_did_not_complete_are_refused(self):
        unfinished = self.run_dir("u")
        (unfinished / "COMPLETED").unlink()
        unsettled = self.run_dir("v")
        (unsettled / "UNSETTLED").write_text("")
        for run in (unfinished, unsettled):
            with self.assertRaisesRegex(SystemExit, "not a completed"):
                self.main(run)

    def test_runs_loaded_otherwise_than_the_rungs_are_refused(self):
        raw = json.loads(PREREGISTERED.read_text())
        raw.update(reference_level=1)
        self.config.write_text(json.dumps(raw))
        with self.assertRaisesRegex(SystemExit, "load \\[290\\] keys"):
            self.main(self.run_dir("a"))

    def test_refusals(self):
        cases = (([self.run_dir("h", drop='"type":"header"')], "host log"),
                 ([self.run_dir("m", fingerprint="assoc-v1:1M:T2:sst10:dio0:ltm1x2")],
                  "m = 1"),
                 ([self.run_dir("x"), self.run_dir("y", fingerprint="other:sst10:")],
                  "several configurations"),
                 ([self.run_dir("s", fingerprint="assoc-v1:1M:T2:fixture")],
                  "no sst field"))
        for runs, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(SystemExit, message):
                    self.main(*runs)
        self.config.write_text(json.dumps({**ConfigTest.GOOD, "k": 0}))
        with self.assertRaisesRegex(SystemExit, "k must be"):
            self.main(self.run_dir("z"))


if __name__ == "__main__":
    unittest.main()
