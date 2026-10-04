"""The reward's pricing (plan §6.3 test_reward; PATHWAYS H §3, D §4,
Proposition D.16 as amended 2026-10-03): each job priced by the evaluator's
formulas, the level's charged reads with the slot moves and its hidden
steps, flushes' interference in the write-path bucket, shadowed garbage,
and the decomposition: the levels' charges plus the buckets equal 04's
C_W and C_R; and the one-step neighbour prediction."""

import math
import unittest

from learner import prices, reward
from learner.logs import STEP_TYPES

from tests import learner_fixtures as fx

cm = prices.cost_model_v2


class PricingTest(unittest.TestCase):
    def setUp(self):
        self.p = prices.from_plugin_config(fx.plugin_config())

    def test_the_plugin_config_gives_the_evaluators_prices(self):
        self.assertEqual(self.p.base("probe"), 100.0)
        self.assertEqual(self.p.base("step"), 10.0)
        self.assertEqual(self.p.v2.job["deep"], 3000.0)
        self.assertEqual(self.p.v2.kappa_j["probe"]["deep"], 0.1)
        bad = fx.plugin_config()
        bad["cost_model"] = 1
        with self.assertRaises(ValueError):
            prices.from_plugin_config(bad)
        bad = fx.plugin_config()
        del bad["c_st"]
        with self.assertRaises(ValueError):
            prices.from_plugin_config(bad)

    def test_a_job_is_priced_as_the_evaluator_prices_it(self):
        line = fx.job_line()
        tau, read, write = reward.job_charges(self.p, line)
        self.assertAlmostEqual(tau, 3000 + 0.5 * (2097152 + 1048576) +
                               2.0 * 3000000)
        job = cm.Job(kind="deep", level=2, s=2097152, o=1048576, x=3000000,
                     n_begin=101000, n_end=104000)
        rho = cm.rho_of(self.p.v2, {x: float(line["win"][x]) for x in STEP_TYPES},
                        3000.0)
        self.assertEqual((read, write), cm.charge(self.p.v2, 1000.0, job, rho))
        # By hand: q rho_x (kB Y + kJ t) for x = probe has the busy part.
        y = 3000000 + 0.25 * (2097152 + 1048576)
        t_job = tau / 1e9
        probe = 1000.0 * (100.0 * 600 / 3000) * (1e-9 * y + 0.1 * t_job)
        self.assertGreater(read, probe)
        self.assertAlmostEqual(write, 1000.0 * (50.0 * 1320 / 3000) * 1e-9 * y)

    def test_a_move_pays_its_job_price_and_carries_no_bytes(self):
        line = fx.job_line(kind="move", x=0, o=0)
        tau, read, write = reward.job_charges(self.p, line)
        self.assertEqual(tau, 400.0)
        self.assertEqual((read, write), (0.0, 0.0))  # Y = 0, kappa_J = 0

    def test_the_level_pays_its_charged_reads_hidden_steps_and_garbage(self):
        t = fx.interior_transition()
        c = t["cost"]
        lc = reward.level_cost(self.p, t, [])
        expected = (100 * (c["probes"] + c["slot_in_probes"]) +
                    1000 * c["fp_reads"] + 2000 * c["seeks"] +
                    5000 * (c["reopens"] + c["slot_in_reopens"]) +
                    10 * (c["hidden_steps"] + c["slot_in_hidden"]))
        self.assertAlmostEqual(lc.read, expected)
        self.assertAlmostEqual(lc.space, 0.001 / 1000 * (1 - 0.93) *
                               c["held_byte_ops"])
        self.assertEqual(lc.tau, 0)

    def test_a_flushs_interference_goes_to_the_write_path_bucket(self):
        t = fx.interior_transition(level=0, agent="l0")
        lc = reward.level_cost(self.p, t, [fx.flush_line()])
        self.assertAlmostEqual(lc.tau, 1000 + 2.0 * 1048576)
        self.assertEqual((lc.intf_read, lc.intf_write), (0, 0))
        self.assertGreater(lc.write_path_read, 0)
        self.assertGreater(lc.write_path_write, 0)
        # c^beta excludes the bucket.
        self.assertAlmostEqual(lc.weighted(self.p), lc.tau + lc.read + lc.space)

    def test_levels_plus_buckets_equal_the_evaluators_totals(self):
        """D.16: three levels' intervals over the same operations, their
        charged costs plus the buckets, against cost_model_v2.evaluate."""
        def parts(**kw):
            base = {k: 0.0 for k in fx.interior_transition()["cost"]}
            base.update(gets=600, scans=150, writes=250, ops=1000,
                        fg_nexts_found=900, memtable_hidden=7)
            base.update(kw)
            return base
        # Level 0 moves a third of its probes to level 2 (slot blocking).
        levels = [
            parts(probes=300, fp_reads=20, hit_reads=40, seeks=150, reopens=6,
                  hidden_steps=30, slot_out_probes=100, slot_out_hidden=10),
            parts(probes=200, fp_reads=10, hit_reads=30, seeks=150,
                  hidden_steps=20),
            parts(probes=100, fp_reads=5, hit_reads=20, seeks=150, reopens=2,
                  slot_in_probes=100, slot_in_hidden=10),
        ]
        jobs = [fx.job_line(level=2), fx.flush_line()]
        totals = {"W": 0.0, "R": 0.0}
        for level, c in enumerate(levels):
            t = fx.interior_transition(level=level, cost=c, rho_tilde=None)
            mine = [j for j in jobs if j["level"] == level]
            lc = reward.level_cost(self.p, t, mine)
            totals["W"] += lc.tau + lc.intf_write + lc.write_path_write
            totals["R"] += (lc.read + lc.intf_read + lc.write_path_read +
                            1000 * c["hit_reads"])
        b = reward.buckets(self.p, levels[0])
        totals["W"] += b["fixed_write"]
        totals["R"] += b["scan_base"] + b["memtable"] + b["fixed_read"]
        phase = {"probe": 600, "block": 125, "seek": 450, "reopen": 8,
                 "step": 900 + 50 + 7, "iblock": 0, "memtable": 750,
                 "get0": 600, "scan0": 150, "put": 250}
        ev = cm.evaluate(self.p.v2, 1000.0, 0.001,
                         [reward.job_of(j) for j in jobs], phase, 1000.0, 0.0,
                         exact=False)
        intf_r = sum(reward.job_charges(self.p, j)[1] for j in jobs)
        intf_w = sum(reward.job_charges(self.p, j)[2] for j in jobs)
        self.assertAlmostEqual(totals["W"],
                               ev["C_W"] - ev["interference_write"] + intf_w,
                               delta=1e-9 * ev["C_W"])
        self.assertAlmostEqual(totals["R"],
                               ev["C_R"] - ev["interference_read"] + intf_r,
                               delta=1e-9 * ev["C_R"])


class NeighbourTest(unittest.TestCase):
    def test_a_compaction_lands_its_merged_bytes_below(self):
        own = {"phi": 1.2, "rho": 0.9, "xi": 0.0}
        rel = reward.release("interior", own, "compact", 1.0, 1.2 / 1.05,
                             own_c=8.0, below_c=80.0)
        out_a = 1.2 - 1.2 / 1.05
        out_hold = 0.2  # due under hold too (score 1.2)
        self.assertAlmostEqual(rel.landed_below,
                               0.9 * (out_a - out_hold) * 8 / 80)
        below = {"phi": 0.5, "anchor": 1.0, "timing": 1.0, "score": 0.5,
                 "phi_up": 1.2, "m_up": 1.0}
        x_a, x_hold = reward.predict_neighbour("down", "interior", below, rel)
        self.assertAlmostEqual(x_a["phi"] - x_hold["phi"], rel.landed_below)
        self.assertAlmostEqual(x_a["phi_up"], 1.2 / 1.05)
        self.assertAlmostEqual(x_hold["phi_up"], 1.0)

    def test_a_deferral_keeps_the_bytes_the_hold_would_release(self):
        own = {"phi": 1.2, "rho": 1.0, "xi": 0.0}
        rel = reward.release("interior", own, "defer", 1.0, 1.26, 8.0, 80.0)
        self.assertAlmostEqual(rel.landed_below, -0.2 * 8 / 80)

    def test_the_upper_neighbour_sees_the_level_and_its_hidden_steps_leave(self):
        own = {"phi": 1.0, "rho": 1.0, "xi": 0.0}
        rel = reward.release("interior", own, "compact", 1.0, 0.8, 8.0, 80.0)
        above = {"phi_down": 1.0, "m_down": 1.0, "e_hd": 4.0}
        x_a, x_hold = reward.predict_neighbour("up", "interior", above, rel)
        self.assertAlmostEqual(x_a["phi_down"], 0.8)
        self.assertAlmostEqual(x_a["e_hd"], 4.0 * 0.8)  # M^hd: 20% left
        self.assertEqual(x_hold["e_hd"], 4.0)

    def test_l0s_merge_lands_its_files_in_l1(self):
        own = {"l0_fill": 0.75, "trigger": 1.0}
        rel = reward.release("l0", own, "compact", 4.0, 3.0, own_c=4.0,
                             below_c=8.0)
        # k0 = 3 files of F = 1: 3 bytes into C_1 = 8, where hold lands none.
        self.assertAlmostEqual(rel.landed_below, 3.0 / 8.0)
        self.assertEqual(rel.share_hold, 0.0)

    def test_unmeasured_inputs_release_nothing(self):
        rel = reward.release("interior", {"phi": None}, "compact", 1.0, 0.9,
                             8.0, 80.0)
        self.assertEqual(rel.landed_below, 0.0)
        self.assertTrue(math.isnan(rel.phi_after_a))


if __name__ == "__main__":
    unittest.main()
