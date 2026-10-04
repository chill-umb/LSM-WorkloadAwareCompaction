"""frontier_analysis.py: the lower convex hull over (C_W, C_R, C_S) with a
collinear tie (C.4, D.5), theta*_beta per mode, and beta-bar (D.4)."""
import unittest

import frontier_analysis as fa
from tests.evaluation_rows import CONTRACT, row

# M lies on the segment AB, so it minimises J only where A and B tie
# (beta_W = beta_R): supported, not a vertex. C wins when space is cheap.
# D is dominated.
POINTS = {"A": (0.0, 2.0, 1.0), "B": (2.0, 0.0, 1.0), "M": (1.0, 1.0, 1.0),
          "C": (0.5, 0.5, 5.0), "D": (3.0, 3.0, 3.0)}


def chosen(mode, beta, scale=1.0):
    return next(c for c in fa.comparators(POINTS, CONTRACT)
                if (c["mode"], c["beta_star"], c["cs_scale"]) ==
                (mode, beta, scale))


class HullTest(unittest.TestCase):
    def test_vertices_and_the_collinear_tie(self):
        hull = fa.lower_hull(POINTS)
        self.assertEqual(hull["vertices"], ["A", "B", "C"])
        self.assertEqual(hull["supported_non_vertices"], ["M"])

    def test_hull_ignores_axis_units(self):
        scaled = {n: (w * 1e-3, r * 1e2, s * 1e-12)
                  for n, (w, r, s) in POINTS.items()}
        self.assertEqual(fa.lower_hull(scaled), fa.lower_hull(POINTS))

    def test_single_point_is_a_vertex(self):
        self.assertEqual(fa.lower_hull({"A": (1.0, 1.0, 1.0)})["vertices"],
                         ["A"])

    def test_space_only_vertex_in_real_units(self):
        # E wins only when space is dear. In 04's units (C_S ~ 1e-12 of the
        # others) its region is too thin to see without the axis rescaling.
        points = {**POINTS, "E": (3.0, 3.0, 0.1)}
        real = {n: (w * 1e-5, r * 1e-4, s * 2.6e-12)
                for n, (w, r, s) in points.items()}
        self.assertEqual(fa.lower_hull(real)["vertices"], ["A", "B", "C", "E"])

    def test_a_point_tied_only_at_a_zero_weight_is_not_supported(self):
        # P = (0, 3, 1) ties A only where beta_R = 0, which beta > 0 excludes.
        hull = fa.lower_hull({**POINTS, "P": (0.0, 3.0, 1.0)})
        self.assertNotIn("P", hull["vertices"] + hull["supported_non_vertices"])


class ComparatorTest(unittest.TestCase):
    def test_theta_star_per_mode(self):
        # read b10: W + 10R + S -> B = 3; write b10: 10W + R + S -> A = 3.
        self.assertEqual(chosen("read", 10)["theta_star"], "B")
        self.assertEqual(chosen("write", 10)["theta_star"], "A")
        # D-24 §2: every configuration ranked by J, lowest first.
        for item in fa.comparators(POINTS, CONTRACT):
            ranking = item["ranking"]
            self.assertEqual(ranking[0], [item["theta_star"], item["J"]])
            self.assertEqual(sorted(POINTS), sorted(n for n, _ in ranking))
            values = [v for _, v in ranking]
            self.assertEqual(values, sorted(values))
        self.assertEqual(chosen("read", 2)["J"], 3.0)

    def test_ties_are_listed(self):
        # space b10: W + R + 10S is 12 at A, B and M; balanced is 3 there.
        for mode, beta in (("space", 10), ("balanced", 1)):
            self.assertEqual(chosen(mode, beta)["ties"], ["A", "B", "M"])

    def test_storage_price_scale(self):
        # At 2c_s, space b10 weighs S by 20: A, B, M = 22, C = 101.
        self.assertEqual(chosen("space", 10, 2.0)["J"], 22.0)
        # At c_s/2, balanced weighs S by 0.5: C = 3.5, A = B = M = 2.5.
        self.assertEqual(chosen("balanced", 1, 0.5)["J"], 2.5)

    def test_theta_star_is_on_the_hull_in_every_mode(self):
        hull = fa.lower_hull(POINTS)
        on_hull = set(hull["vertices"]) | set(hull["supported_non_vertices"])
        for item in fa.comparators(POINTS, CONTRACT):
            self.assertIn(item["theta_star"], on_hull, item["column"])

    def test_a_near_tie_off_the_hull_is_never_theta_star(self):
        # "0N" sorts first and is 1e-13 above the balanced minimum, 3, but
        # M dominates it: theta* must be the exact minimiser.
        points = {**POINTS, "0N": (1.0, 1.0, 1.0 + 1e-13)}
        item = next(c for c in fa.comparators(points, CONTRACT)
                    if c["column"] == "J_balanced_cs1")
        self.assertEqual(item["theta_star"], "A")
        self.assertIn("0N", item["ties"])


class BetaBarTest(unittest.TestCase):
    def test_read_mode_value(self):
        # P = R: min 0 at B, next 0.5 at C, so Delta = 0.5; W + S ranges
        # from 1 (A) to 6 (D), so M = 5 and beta-bar = 10.
        self.assertEqual(fa.beta_bar(POINTS, "read", 1.0), 10.0)

    def test_storage_price_scale_enters_the_other_costs(self):
        # At 2c_s, W + 2S ranges from 2 (A) to 10.5 (C): M = 8.5, so 17.
        self.assertEqual(fa.beta_bar(POINTS, "read", 2.0), 17.0)
        # Space priority: P = 2S, min 2, next 6 (D), Delta = 4; W + R
        # ranges from 1 (C) to 6 (D): 5 / 4.
        self.assertEqual(fa.beta_bar(POINTS, "space", 2.0), 1.25)

    def test_above_beta_bar_the_least_prioritised_cost_wins(self):
        for mode, k in fa.PRIORITISED.items():
            for scale in (0.5, 1.0, 2.0):
                bar = fa.beta_bar(POINTS, mode, scale)
                weights = [1.0, 1.0, scale]
                weights[k] *= bar * 1.001
                best = min(POINTS, key=lambda n: sum(
                    w * x for w, x in zip(weights, POINTS[n])))
                least = min(p[k] * (scale if k == 2 else 1)
                            for p in POINTS.values())
                self.assertEqual(POINTS[best][k] * (scale if k == 2 else 1),
                                 least, (mode, scale))

    def test_equal_prioritised_costs_have_no_bound(self):
        flat = {"A": (1.0, 1.0, 1.0), "B": (2.0, 1.0, 3.0)}
        self.assertIsNone(fa.beta_bar(flat, "read", 1.0))


class SelectionTest(unittest.TestCase):
    def rows(self):
        return [row("native", "l0-4", 1, POINTS["A"]),
                row("static:uniform_0_75", "l0-4:ltm1x0.75", 1, POINTS["B"]),
                row("rules", "l0-4", 1, POINTS["C"]),
                row("native", "l0-4", 1, POINTS["A"], workload="other")]

    def test_selects_static_arms_of_one_cell(self):
        rows = self.rows() + [row("native", "l0-4", 1, POINTS["A"], ratio=10)]
        configs = fa.static_configurations(rows, "assoc-v1", {2}, 10)
        self.assertEqual(len(configs), 2)

    def test_a_configuration_is_its_mean_over_seeds(self):
        rows = [row("native", "l0-4", 1, (1.0, 1.0, 1.0)),
                row("native", "l0-4", 2, (1.0, 3.0, 1.0))]
        configs = fa.static_configurations(rows, "assoc-v1", {2}, 10)
        self.assertEqual(fa.mean_costs(next(iter(configs.values()))),
                         (1.0, 2.0, 1.0))

    def test_analyze_reports_hull_comparators_and_beta_bar(self):
        rows = [row("static:" + name, name, 1, x) for name, x in POINTS.items()]
        report = fa.analyze(fa.static_configurations(rows, "assoc-v1", {2}, 10),
                            CONTRACT)
        self.assertEqual(len(report["lower_hull"]["vertices"]), 3)
        self.assertEqual(report["beta_bar"]["read"]["cs2"], 17.0)
        # beta-bar is 10 for read and write at c_s, 2.5 for space: the
        # headline beta* = 10 is strictly above only the last.
        regime = report["headline_in_strict_priority_regime"]
        self.assertEqual((regime["read"]["cs1"], regime["write"]["cs1"],
                          regime["space"]["cs1"]), (False, False, True))

    def test_refuses_a_negative_or_missing_mean_cost(self):
        for costs in ((-1.0, 1.0, 1.0), (1.0, float("nan"), 1.0)):
            with self.assertRaises(ValueError, msg=costs):
                fa.mean_costs({1: row("native", "l0-4", 1, costs)})

    def test_refuses_unpriced_duplicate_or_mixed_runs(self):
        for bad in ({"objective_status": "no prices"},
                    {"experiment_fingerprint": "assoc-v1:10M:T2:l0-4:mix1-0-0"},
                    {"prices_sha256": "q" * 64}):
            rows = self.rows()
            rows[1] = {**rows[1], **bad}
            with self.assertRaises(ValueError, msg=bad):
                fa.static_configurations(rows, "assoc-v1", {2}, 10)


if __name__ == "__main__":
    unittest.main()
