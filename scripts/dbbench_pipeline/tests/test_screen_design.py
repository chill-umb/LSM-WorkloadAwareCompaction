"""27_screen_design.py (support for D-17): the Monte Carlo against known
answers, the admissible grid (A-Impl-6), run counts on a tiny grid by hand,
the spread of one configuration, and one end-to-end report. Seeds are
fixed, so every result is deterministic."""
import contextlib
import csv
import importlib.util
import io
import json
import math
import random
import statistics
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import research_objective

PIPELINE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "screen_design", PIPELINE / "27_screen_design.py")
screen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screen)
CONTRACT, _ = research_objective.load_contract()
MIB = 1 << 20
SIZE = statistics.NormalDist().cdf


def p_drop(gap, rivals=1, runs=2, k=1.645, df=None, trials=40000, seed=1):
    return screen.drop_probability(gap, rivals, runs, k, df, trials,
                                   random.Random(seed))


class MonteCarloTest(unittest.TestCase):
    def test_zero_gap_drops_at_the_rules_size(self):
        # One rival, sigma known: P = P(Z > k) whatever n_s.
        for runs in (2, 3):
            with self.subTest(runs=runs):
                self.assertAlmostEqual(p_drop(0.0, runs=runs), 1 - SIZE(1.645),
                                       delta=0.006)

    def test_a_positive_gap_matches_the_closed_form(self):
        # D ~ N(-gap, 1/n) against k / sqrt(n): P = 1 - Phi(k + gap sqrt(n)).
        self.assertAlmostEqual(p_drop(0.5, k=1.0),
                               1 - SIZE(1.0 + 0.5 * math.sqrt(2)), delta=0.006)

    def test_a_large_gap_is_never_dropped(self):
        self.assertEqual(p_drop(6.0, rivals=3, k=3.0, df=31), 0.0)

    def test_a_point_far_above_its_rivals_is_dropped(self):
        self.assertGreater(p_drop(-8.0, k=3.0, df=31), 0.999)

    def test_more_rivals_drop_more(self):
        self.assertGreater(p_drop(0.0, rivals=3), p_drop(0.0, rivals=1) + 0.03)

    def test_an_estimated_sigma_drops_more_and_tends_to_the_known(self):
        known = p_drop(0.0, k=3.0)
        self.assertGreater(p_drop(0.0, k=3.0, df=3), known + 0.01)
        self.assertAlmostEqual(p_drop(0.0, k=3.0, df=100000), known, delta=0.002)

    def test_pooled_sigma_matches_the_t_tail(self):
        # One rival at gap 0 with sigma_d estimated on df degrees of freedom:
        # P = P(t_df > k), whatever n_s (the verifier's integration: 0.00264
        # at df 31, 0.00194 at df 62, for k = 3).
        for runs, df, exact in ((2, 31, 0.00264), (3, 62, 0.00194)):
            with self.subTest(df=df):
                self.assertAlmostEqual(p_drop(0.0, runs=runs, k=3.0, df=df,
                                              trials=200000), exact, delta=0.0004)

    def test_deterministic(self):
        self.assertEqual(p_drop(0.3, rivals=3, df=31, trials=2000),
                         p_drop(0.3, rivals=3, df=31, trials=2000))

    def test_boundaries_need_every_farther_gap_to_pass(self):
        entries = [{"gap_sd": g, "p_drop": p, "se": 0.001} for g, p in
                   ((-3, 0.99), (-2, 0.8), (-1, 0.95), (0, 0.2), (1, 0.005),
                    (2, 0.02), (3, 0.0))]
        self.assertEqual(screen.boundary(entries, +1, 0.01), (3, False))
        self.assertEqual(screen.boundary(entries, -1, 0.9), (3, False))
        entries[5]["p_drop"] = 0.0
        entries[1]["p_drop"] = 0.9
        self.assertEqual(screen.boundary(entries, +1, 0.01), (1, False))
        self.assertEqual(screen.boundary(entries, -1, 0.9), (1, True))
        self.assertEqual(screen.boundary(entries[2:4], +1, 0.01), (None, False))
        # Within 2 SE of the level, on either side of the boundary.
        entries[4]["p_drop"] = 0.0095
        self.assertEqual(screen.boundary(entries, +1, 0.01), (1, True))
        entries[4]["p_drop"], entries[3]["p_drop"] = 0.005, 0.0105
        self.assertEqual(screen.boundary(entries, +1, 0.01), (1, True))

    def test_chi_square_quantiles(self):
        for p, df, exact in ((0.975, 4, 11.1433), (0.025, 4, 0.484419),
                             (0.975, 1, 5.02389), (0.025, 30, 16.7908)):
            with self.subTest(p=p, df=df):
                self.assertAlmostEqual(screen.chi2_quantile(p, df), exact, places=3)

    def test_the_spread_carries_sigmas_interval(self):
        s = screen.spread([1.0, 2.0, 3.0, 4.0, 5.0])
        sd = statistics.stdev([1.0, 2.0, 3.0, 4.0, 5.0])
        low, high = s["sd_ci95"]
        # 4 df: sigma in [sd x 0.599, sd x 2.874].
        self.assertAlmostEqual(low / sd, math.sqrt(4 / 11.1433), places=4)
        self.assertAlmostEqual(high / sd, math.sqrt(4 / 0.484419), places=4)
        self.assertAlmostEqual(s["relative_sd_ci95"][1], high / 3.0)


class CostTest(unittest.TestCase):
    def test_admissible_grid(self):
        static = CONTRACT["static_class"]
        self.assertEqual(screen.admissible_points(static, 2 * MIB, 20),
                         [(2, 8), (4, 8), (2, 16), (4, 16), (8, 16),
                          (2, 32), (4, 32), (8, 32)])
        # A flush file just over 2 MiB takes trigger 8 out at base 16.
        self.assertNotIn((8, 16), screen.admissible_points(static, 2.1 * MIB, 20))
        # K_slow - 1 caps too.
        self.assertEqual(screen.admissible_points(static, 2 * MIB, 5),
                         [(2, 8), (4, 8), (2, 16), (4, 16), (2, 32), (4, 32)])

    def test_run_counts_by_hand(self):
        # A tiny grid: 4 configurations of which 2 native, 1 cross-T re-run.
        self.assertEqual(screen.design_runs(4, 2, 1, 5), 25)
        # Screen 2, half survive: 2 screened x 2 + 1 survivor x 3 + 3 x 5.
        self.assertEqual(screen.design_runs(4, 2, 1, 5, 2, 0.5), 22)
        # Natives screened too: 4 x 2 + 2 x 3 + 1 x 5.
        self.assertEqual(screen.design_runs(4, 2, 1, 5, 2, 0.5, True), 19)
        # Every configuration survives: the screen costs what no screen does.
        self.assertEqual(screen.design_runs(4, 2, 1, 5, 3, 1.0), 25)


PRICES = {"c_w": 1e-12, "c_f": 1e-9, "c_blk": 2e-9, "c_sk": 3e-9,
          "c_open": 4e-8,
          "c_s": CONTRACT["prices"]["storage_price_per_byte_second"]}


def rows(n=5, family="assoc"):
    return [{"workload_family": family, "experiment_fingerprint": "fp",
             "elapsed_seconds": "900", "sst_bytes_written": str(1e10 + 1e8 * i),
             "filter_probes": str(5e7 - 1e5 * i), "block_reading_probes": "2e7",
             "run_seeks": "1e6", "table_reopens": str(3e6 + 1e4 * i),
             "held_byte_operations": str(1e17 * (1 + i / 100))}
            for i in range(n)]


class NoiseTest(unittest.TestCase):
    def test_spread_of_costs_and_of_every_objective(self):
        noise = screen.workload_noise(rows(), CONTRACT, PRICES, 1000.0)
        writes = [1e10 + 1e8 * i for i in range(5)]
        self.assertAlmostEqual(noise["costs"]["C_W"]["sd"] / 1e-12,
                               statistics.stdev(writes), delta=1e-3)
        self.assertAlmostEqual(noise["counts"]["sst_bytes_written"]["relative_sd"],
                               statistics.stdev(writes) / statistics.fmean(writes))
        self.assertEqual(len(noise["J"]),
                         len(list(research_objective.objective_grid(CONTRACT))))
        costs = [research_objective.priced_costs(
            PRICES, 1000.0, *(float(r[c]) for c in screen.COUNTS)) for r in rows()]
        balanced = [sum(c) for c in costs]
        self.assertAlmostEqual(noise["J"]["J_balanced_cs1"]["sd"],
                               statistics.stdev(balanced), delta=1e-12)
        # How much more the hull rule lowers J than the shadow does:
        # sum_c beta_c sd(C_c) / sd(J), at least 1.
        sds = [statistics.stdev(c[k] for c in costs) for k in range(3)]
        self.assertAlmostEqual(noise["J"]["J_balanced_cs1"]["hull_shift_ratio"],
                               sum(sds) / statistics.stdev(balanced))
        read10 = [c[0] + 10 * c[1] + 2 * c[2] for c in costs]
        self.assertAlmostEqual(noise["J"]["J_read_b10_cs2"]["hull_shift_ratio"],
                               (sds[0] + 10 * sds[1] + 2 * sds[2]) /
                               statistics.stdev(read10))
        for s in noise["J"].values():
            self.assertGreaterEqual(s["hull_shift_ratio"], 1 - 1e-12)

    def test_without_prices_only_counts(self):
        self.assertEqual(set(screen.workload_noise(rows(), CONTRACT, None, None)),
                         {"counts"})

    def test_refusals(self):
        mixed = rows()
        mixed[1]["experiment_fingerprint"] = "other"
        for data, pattern in ((rows(2), "at least 3"),
                              (mixed, "not of one configuration")):
            with self.subTest(pattern), self.assertRaisesRegex(ValueError, pattern):
                screen.workload_noise(data, CONTRACT, PRICES, 1000.0)


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.tmp = Path(folder.name)
        self.summary = self.tmp / "summary.csv"
        with self.summary.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows()[0]))
            writer.writeheader()
            writer.writerows(rows())
        self.prices = self.tmp / "prices.json"
        record = {k: v for k, v in PRICES.items() if k != "c_s"}
        self.prices.write_text(json.dumps({
            "schema": 5, "kind": "final", **record,
            "reproducibility": {"passed": True, "tolerance":
                                CONTRACT["prices"]["reproducibility_tolerance"]},
            "price_per_core_second": CONTRACT["prices"]["price_per_core_second"],
            "reopen_timer": {"seconds_per_reopen": 8e-6}}))
        # 27 reads the live contract, which holds q-bar since 2026-10-02 and
        # refuses a --qbar that differs. These runs price with the stand-in
        # --qbar assoc=1000, so 27 sees the contract with q-bar unmeasured.
        contract, sha = research_objective.load_contract()
        unmeasured = json.loads(json.dumps(contract))
        unmeasured["reference_rate"]["ops_per_second"] = dict.fromkeys(
            contract["reference_rate"]["ops_per_second"])
        patch = mock.patch.object(screen.research_objective, "load_contract",
                                  return_value=(unmeasured, sha))
        patch.start()
        self.addCleanup(patch.stop)

    def run_tool(self, *extra):
        out = self.tmp / "report.json"
        argv = [str(self.summary), "--prices", str(self.prices), "--trials", "500",
                "--json", str(out), *extra]
        with contextlib.redirect_stdout(io.StringIO()) as text:
            screen.main(argv)
        return json.loads(out.read_text()), text.getvalue()

    def test_report(self):
        report, text = self.run_tool("--qbar", "assoc=1000")
        w = report["workloads"]["assoc"]
        # 8 admissible points x 4 profiles = 32 per cell, 3 cells; 2 cross-T
        # ratios x 4 configurations; five runs each.
        self.assertEqual(report["configurations_per_cell"], 32)
        self.assertEqual(w["designs"]["current"]["runs"], (96 + 8) * 5)
        self.assertEqual(w["designs"]["current"]["node_hours"], 520 * 900 / 3600)
        # 72 screened x 3 + 18 survivors x 2 + (24 natives + 8) x 5.
        self.assertEqual(w["designs"]["screen3_survivors0.25"]["runs"],
                         72 * 3 + 18 * 2 + 32 * 5)
        self.assertEqual(len(report["monte_carlo"]), 2 * 2 * 15)
        self.assertIn("J_read_b10_cs1", w["gaps"])
        self.assertEqual(set(report["gaps_sd"]["2"]),
                         {"safe", "safe_uncertain", "dropped_beyond",
                          "dropped_beyond_uncertain"})
        self.assertIn("dropped beyond", text)
        # Holes at C-1's minimum of 4 vertices and at 8.
        cell = report["monte_carlo"][9]
        self.assertEqual(cell["gap_sd"], 0)
        self.assertEqual(set(cell["p_any_hole"]), {"4", "8"})
        self.assertAlmostEqual(cell["p_any_hole"]["8"]["cell"],
                               1 - (1 - cell["p_drop"]) ** 8)
        self.assertAlmostEqual(cell["se"], math.sqrt(
            cell["p_drop"] * (1 - cell["p_drop"]) / 500))
        # Gaps also at the upper end of sigma's 95% interval (4 df).
        gap = w["gaps"]["J_read_b10_cs1"]["2"]["dropped_beyond"]
        self.assertAlmostEqual(gap["share_of_J_upper"] / gap["share_of_J"],
                               math.sqrt(4 / 0.484419), places=4)
        for caveat in ("best case", "T=10 run time"):
            self.assertIn(caveat, text)
        # Deterministic under the fixed seed.
        self.assertEqual(self.run_tool("--qbar", "assoc=1000")[0], report)

    def test_a_cell_does_not_depend_on_the_other_cells(self):
        # The verifier's case: adding --rivals 5 moved (3, 1, -2.5).
        def cell(report):
            return next(c["p_drop"] for c in report["monte_carlo"]
                        if (c["screen_runs"], c["rivals"], c["gap_sd"]) == (3, 1, -2.5))
        base = self.run_tool("--qbar", "assoc=1000", "--rivals", "1", "3")[0]
        more = self.run_tool("--qbar", "assoc=1000", "--rivals", "1", "3", "5")[0]
        self.assertEqual(cell(base), cell(more))

    def test_default_and_explicit_gaps_share_a_stream(self):
        # The default grid's 0 and an explicit 0.0 must seed one stream.
        def cells(report):
            return {(c["screen_runs"], c["rivals"]): c["p_drop"]
                    for c in report["monte_carlo"] if c["gap_sd"] == 0}
        default = self.run_tool("--qbar", "assoc=1000")[0]
        explicit = self.run_tool("--qbar", "assoc=1000", "--gaps", "0.0")[0]
        self.assertEqual(cells(default), cells(explicit))

    def test_pair_correlation_scales_the_gaps(self):
        # sigma_d = sigma sqrt(2(1 - rho)): sqrt 2 at rho 0, 1 at rho 0.5.
        def usd(rho):
            report = self.run_tool("--qbar", "assoc=1000",
                                   "--pair-correlation", str(rho))[0]
            return report["workloads"]["assoc"]["gaps"]["J_balanced_cs1"]["3"][
                "dropped_beyond"]["usd"]
        self.assertAlmostEqual(usd(0.5) / usd(0.0), 1 / math.sqrt(2))

    def test_q_bar_is_required_to_price(self):
        with self.assertRaisesRegex(SystemExit, "no q-bar"):
            self.run_tool()


if __name__ == "__main__":
    unittest.main()
