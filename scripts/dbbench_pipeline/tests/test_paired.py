"""07_evaluate_paired.py: paired J_beta intervals against theta* (CMP-3),
the D-13 §8 stall rule, regret (C.5) and suite robustness (CMP-7)."""
import importlib.util
import unittest
from pathlib import Path

from tests.evaluation_rows import CONTRACT, row

PIPELINE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "evaluate_paired", PIPELINE / "07_evaluate_paired.py")
paired = importlib.util.module_from_spec(spec)
spec.loader.exec_module(paired)
RULE = CONTRACT["stall_rule"]

# Two static configurations and a policy, five seeds. In read priority
# (beta* = 10) S is 2.0 for X and 1.0 for Y and the policy, so theta* is Y
# (J = 1 + 10 + 1 = 12) and the policy is 1 + 10*0.9 + 1 = 11, with a
# per-seed wobble on R.
WOBBLE = (0.0, 0.01, -0.01, 0.02, -0.02)


def cell(policy_extra=None):
    rows = []
    for seed, wobble in enumerate(WOBBLE, 1):
        rows.append(row("native", "l0-4", seed, (1.0, 1.0, 2.0)))
        rows.append(row("static:y", "l0-8", seed, (1.0, 1.0, 1.0)))
        rows.append(row("rules", "l0-4:rules", seed, (1.0, 0.9 + wobble, 1.0),
                        **(policy_extra or {})))
    return rows


def evaluate(rows):
    return paired.evaluate(rows, "rules", 10, 2, CONTRACT)


def read_b10(report, workload="assoc-v1"):
    return next(c for c in report["per_workload"][workload]
                if (c["mode"], c["beta_star"], c["cs_scale"]) == ("read", 10, 1.0))


class PairedTest(unittest.TestCase):
    def test_cmp3_interval_against_theta_star(self):
        result = read_b10(evaluate(cell()))
        self.assertEqual(result["theta_star"], "assoc-v1:10M:T2:l0-8:mix1-0-0")
        self.assertEqual(result["pairs"], 5)
        self.assertAlmostEqual(result["cmp3_difference"]["mean"], -1.0)
        self.assertTrue(result["cmp3_gain"])

    def test_regret(self):
        self.assertAlmostEqual(read_b10(evaluate(cell()))["regret"], 11 / 12 - 1)

    def test_pairs_by_seed_not_by_position(self):
        # theta* carries each seed's wobble and is listed in reverse: only
        # pairing by seed cancels it, leaving a difference of exactly -1.
        rows = [r for r in cell() if r["arm"] != "static:y"]
        for seed, wobble in reversed(list(enumerate(WOBBLE, 1))):
            rows.append(row("static:y", "l0-8", seed, (1.0, 1.0 + wobble, 1.0)))
        interval = read_b10(evaluate(rows))["cmp3_difference"]
        self.assertAlmostEqual(interval["mean"], -1.0)
        self.assertLess(interval["upper"] - interval["lower"], 1e-9)

    def test_an_interval_crossing_zero_is_no_gain(self):
        rows = [r for r in cell() if r["arm"] != "rules"]
        for seed, wobble in enumerate(WOBBLE, 1):
            rows.append(row("rules", "l0-4:rules", seed,
                            (1.0, 1.0 + 5 * wobble, 1.0)))
        result = read_b10(evaluate(rows))
        self.assertLess(result["cmp3_difference"]["lower"], 0)
        self.assertGreater(result["cmp3_difference"]["upper"], 0)
        self.assertFalse(result["cmp3_gain"])

    # Written out here, not read from paired.PAIRED: a field dropped from
    # the code's list must fail this test (CMP-3, CMP-8, OBJ-2, OBJ-5, A8).
    REQUIRED = ("session_id", "workload_profile", "dbbench_sha256",
                "prices_sha256", "reference_rate", "research_objective_sha256",
                "settle_hold_seconds", "get_operations", "put_operations",
                "scan_operations")

    def test_pairs_must_match_on_every_paired_field(self):
        for field in self.REQUIRED:
            with self.subTest(field=field):
                rows = cell()
                rows[2] = {**rows[2], field: rows[2][field] + "x"}
                # The profile is also the grouping key: a policy row with
                # another profile finds no static arms of its own.
                message = "no static" if field == "workload_profile" else field
                with self.assertRaisesRegex(ValueError, message):
                    evaluate(rows)

    def test_one_pair_gives_no_verdict(self):
        rows = [r for r in cell() if r["dbbench_seed"] == "1"]
        result = read_b10(evaluate(rows))
        self.assertIsNone(result["cmp3_gain"])
        self.assertIsNone(result["stall_rule"]["passed"])

    def test_unpaired_seeds_are_listed(self):
        rows = [r for r in cell() if not (r["arm"] == "rules" and
                                          r["dbbench_seed"] == "5")]
        result = read_b10(evaluate(rows))
        self.assertEqual((result["pairs"], result["unpaired_seeds"]), (4, [5]))

    def test_refusals(self):
        unpriced = cell()
        unpriced[2] = {**unpriced[2], "objective_status": "no prices"}
        repeated = cell() + [cell()[2]]
        no_static = [r for r in cell() if r["arm"] == "rules"]
        for rows, message in ((unpriced, "unpriced"), (repeated, "repeats"),
                              (no_static, "no static")):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    evaluate(rows)


class StallRuleTest(unittest.TestCase):
    def rule(self, stall, throughput):
        rows = cell()
        for r in rows:
            if r["arm"] == "rules":
                i = int(r["dbbench_seed"]) - 1
                r["stall_fraction"] = str(0.01 + stall[i])
                r["throughput_ops_per_second"] = str(1000 * (1 + throughput[i]))
        return read_b10(evaluate(rows))["stall_rule"]

    def test_passes_inside_both_margins(self):
        result = self.rule((0.001, 0.002, 0.0, 0.001, 0.002),
                           (0.0, -0.001, 0.001, 0.0, -0.002))
        self.assertTrue(result["passed"])

    def test_fails_on_stalls(self):
        # Mean 3 points worse than theta*: the upper bound exceeds 2 points.
        result = self.rule((0.03, 0.031, 0.029, 0.03, 0.032), (0,) * 5)
        self.assertFalse(result["stall_passed"])
        self.assertFalse(result["passed"])

    def test_fails_when_the_bound_crosses_the_margin(self):
        # Mean 1.8 points, but spread enough that the upper bound passes 2.
        result = self.rule((0.0, 0.036, 0.018, 0.0, 0.036), (0,) * 5)
        self.assertLess(result["stall_fraction_difference"]["mean"], 0.02)
        self.assertFalse(result["stall_passed"])

    def test_fails_on_throughput(self):
        result = self.rule((0,) * 5, (-0.05, -0.04, -0.05, -0.06, -0.05))
        self.assertTrue(result["stall_passed"])
        self.assertFalse(result["throughput_passed"])
        self.assertFalse(result["passed"])

    def test_margins_come_from_d13(self):
        self.assertEqual((RULE["stall_fraction_margin"],
                          RULE["throughput_relative_margin"]), (0.02, 0.02))


class SuiteRobustnessTest(unittest.TestCase):
    def test_robust_without_winning_any_workload(self):
        # X is best on w1, Y on w2, each twice as costly on the other; the
        # policy is 20% above the best on both.
        static = {"X": {"w1": 10.0, "w2": 20.0}, "Y": {"w1": 20.0, "w2": 10.0}}
        result = paired.suite_robustness({"w1": 12.0, "w2": 12.0}, static)
        self.assertAlmostEqual(result["policy_worst_regret"], 0.2)
        self.assertAlmostEqual(result["best_static_worst_regret"], 1.0)
        self.assertTrue(result["suite_robust"])

    def test_only_configurations_on_every_workload_compete(self):
        # X was measured on w1 only (a profile measured per workload): it
        # is not one setting across the suite, so Y is the best one.
        static = {"X": {"w1": 10.0}, "Y": {"w1": 20.0, "w2": 10.0}}
        result = paired.suite_robustness({"w1": 12.0, "w2": 12.0}, static)
        self.assertEqual(result["best_static"], "Y")
        self.assertAlmostEqual(result["best_static_worst_regret"], 1.0)
        self.assertEqual(result["excluded_configurations"], ["X"])

    def test_not_robust_when_one_static_setting_suffices(self):
        static = {"X": {"w1": 10.0, "w2": 10.0}, "Y": {"w1": 20.0, "w2": 20.0}}
        result = paired.suite_robustness({"w1": 11.0, "w2": 10.0}, static)
        self.assertEqual(result["best_static"], "X")
        self.assertFalse(result["suite_robust"])

    def test_across_workloads_from_rows(self):
        rows = cell() + [dict(r, workload_profile="pow-v1",
                              experiment_fingerprint=r["experiment_fingerprint"]
                              .replace("assoc-v1", "pow-v1")
                              .replace(":mix1-0-0", ":mix0.95-0.05-0:pow1-2")
                              .replace(":l0-", ":qbar50:l0-"))
                         for r in cell()]
        report = evaluate(rows)
        suite = next(s for s in report["suite_robustness"]
                     if (s["mode"], s["beta_star"], s["cs_scale"]) ==
                     ("read", 10, 1.0))
        self.assertEqual(suite["workloads"], ["assoc-v1", "pow-v1"])
        # One configuration (Y) is theta* on both workloads, so the
        # best static worst-case regret is 0 and nothing beats it.
        self.assertEqual(suite["best_static_worst_regret"], 0.0)
        self.assertLess(suite["policy_worst_regret"], 0.0)
        self.assertTrue(suite["suite_robust"])


if __name__ == "__main__":
    unittest.main()
