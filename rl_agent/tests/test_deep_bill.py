"""The trainer settings' l0_bill_deep: L0's bill plus every merge out of L1, L2 and L3 that finished
in the turn, at its whole price (merge_prices), as L0's own merges are charged."""

import tempfile
import unittest

from learner import learned2 as L2
from learner import turn_trainer as TT
from learner.prices import from_plugin_config
from tests import test_turn_trainer as base


class DeepBillTest(base.L0BillTest):
    """l0_bill_deep 1: J_reward = bill_read + bill_merge + the merges out of L1-L3 as paid, rebuilt
    here from the same host log; 0 or absent: no field and the bill as before; refused without
    turn_l0_bill, beside l0_bill_written 1, and for any value but 0 or 1."""

    def test_the_bill_adds_the_merges_out_of_l1_to_l3(self):
        cfg = self.config(bill=1)
        pricer = L2.HostLogPricer(from_plugin_config(cfg))
        for r in base.host_log():
            pricer.add(r)
        with tempfile.TemporaryDirectory() as tmp:
            tr, log = self.run_trainer(tmp, cfg, l0_bill_deep=1)
            self.assertTrue(tr.deep_bill)
            turns = [r for r in log if r["type"] == "turn"]
            self.assertEqual([r["t"] for r in turns], list(range(1, base.TURNS - 1)))
            for r in turns:
                m = TT.merge_prices(pricer, *pricer.bounds(r["op0"], r["op1"]))
                want = [m.get(lv, [0.0, 0.0])[0] for lv in (1, 2, 3)]
                self.assertEqual(len(r["bill_deep_levels"]), 3)
                for got, exp in zip(r["bill_deep_levels"], want):
                    self.assertAlmostEqual(got, exp, delta=1e-12 * max(1.0, abs(exp)))
                self.assertAlmostEqual(r["bill_deep"], sum(want), delta=1e-12 * max(1.0, sum(want)))
                self.assertAlmostEqual(r["bill_merge"], m.get(0, [0.0, 0.0])[0],
                                       delta=1e-12 * max(1.0, r["bill_merge"]))
                self.assertAlmostEqual(r["J_reward"], r["bill_read"] + r["bill_merge"] + r["bill_deep"],
                                       delta=1e-12 * r["J_reward"])
            # The fixture's merges out of L2 are charged, and only there.
            self.assertGreater(sum(r["bill_deep_levels"][1] for r in turns), 0)
            self.assertEqual(sum(r["bill_deep_levels"][0] + r["bill_deep_levels"][2] for r in turns), 0)

    def test_the_deep_part_moves_the_reward(self):
        cfg = self.config(bill=1)
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _, plain = self.run_trainer(a, cfg)
            _, deep = self.run_trainer(b, cfg, l0_bill_deep=1)
            p = {r["t"]: r for r in plain if r["type"] == "turn"}
            charged = [r for r in deep if r["type"] == "turn" and r["bill_deep"] > 0]
            self.assertTrue(charged)
            for r in charged:
                self.assertAlmostEqual(r["J_reward"], p[r["t"]]["J_reward"] + r["bill_deep"],
                                       delta=1e-12 * r["J_reward"])
                self.assertEqual(r["J"], p[r["t"]]["J"])       # the evaluator's J is untouched

    def test_off_or_absent_leaves_the_bill_alone(self):
        cfg = self.config(bill=1)
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _, plain = self.run_trainer(a, cfg)
            tr, off = self.run_trainer(b, cfg, l0_bill_deep=0)
            self.assertFalse(tr.deep_bill)
            self.assertEqual(plain, off)
            self.assertFalse(any("bill_deep" in r for r in off))

    def test_without_the_l0_bill_it_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self.trainer(tmp, self.config(bill=0), l0_bill_deep=1)

    def test_beside_the_written_bill_it_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self.trainer(tmp, self.config(bill=1), l0_bill_deep=1, l0_bill_written=1)

    def test_bad_values_are_refused(self):
        for bad in (2, -1, 0.5, 1.0, "1", True, None):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError):
                    self.trainer(tmp, self.config(bill=1), l0_bill_deep=bad)


if __name__ == "__main__":
    unittest.main()
