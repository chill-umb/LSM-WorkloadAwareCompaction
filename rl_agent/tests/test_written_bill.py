"""written_bill.py: the merge steps below L0 charged when the bytes are written (trainer settings
l0_bill_written, written_turns), and the trainer's use of it."""

import json
import tempfile
import unittest
from pathlib import Path

from learner import learned2 as L2
from learner import turn_trainer as TT
from learner import turn_weights as TW
from learner import written_bill as WB
from learner.prices import from_plugin_config
from tests import test_turn_trainer as base


def row(flush=100.0, puts=10.0, src=(100.0, 80.0, 40.0, 20.0), drop=(20.0, 40.0, 20.0, 0.0),
        paid=(50.0, 160.0, 120.0, 30.0)):
    return WB.Row(flush=flush, puts=puts, src=list(src), drop=list(drop), paid=list(paid))


ALL_DUE = [True] * 4
NONE_DUE = [False] * 4


class CellsTest(unittest.TestCase):
    def test_a_steady_turn_follows_the_formula(self):
        cells = WB.WrittenCells(4).add(row(), NONE_DUE)
        # written 100; shares 1, 0.8, 0.8 * 0.5, 0.4 * 0.5; prices 0.5, 2, 3, 1.5
        for got, want in zip(cells, (50.0, 160.0, 120.0, 30.0)):
            self.assertAlmostEqual(got, want, places=9)

    def test_written_counts_this_turns_keys_at_the_windows_bytes_per_key(self):
        w = WB.WrittenCells(4)
        w.add(row(flush=0.0, puts=10.0), NONE_DUE)          # no flush finished in this turn
        cells = w.add(row(flush=300.0, puts=20.0), NONE_DUE)
        # 300 bytes / 30 keys in the window, 20 keys now: 200 written
        self.assertAlmostEqual(cells[0], 200.0 * 0.5, places=9)

    def test_old_turns_leave_the_window(self):
        w = WB.WrittenCells(2)
        w.add(row(paid=(500.0, 0.0, 0.0, 0.0)), NONE_DUE)
        w.add(row(), NONE_DUE)
        cells = w.add(row(), NONE_DUE)                       # the dear turn is out
        self.assertAlmostEqual(cells[0], 50.0, places=9)
        self.assertRaises(ValueError, WB.WrittenCells, 0)

    def test_a_step_that_never_ran(self):
        quiet = row(src=(100.0, 80.0, 0.0, 0.0), drop=(20.0, 40.0, 0.0, 0.0), paid=(50.0, 160.0, 0.0, 0.0))
        # Room at L2 and L3: no charge.
        cells = WB.WrittenCells(4).add(quiet, [True, True, False, False])
        self.assertEqual(cells[2:], [0.0, 0.0])
        # No room: the price of the step above (2 per byte), down the chain; rho of a silent step is 1.
        cells = WB.WrittenCells(4).add(quiet, ALL_DUE)
        self.assertAlmostEqual(cells[2], 100.0 * 0.4 * 2.0, places=9)
        self.assertAlmostEqual(cells[3], 100.0 * 0.4 * 2.0, places=9)
        # L2 has room but L3 does not: L3 borrows L2's current price, which is zero.
        cells = WB.WrittenCells(4).add(quiet, [True, True, False, True])
        self.assertEqual(cells[2:], [0.0, 0.0])

    def test_a_starved_step_keeps_its_last_paid_price(self):
        w = WB.WrittenCells(1)
        w.add(row(), ALL_DUE)                                # step 2 paid 3 per byte, rho 0.5
        starved = row(src=(100.0, 80.0, 0.0, 20.0), drop=(20.0, 40.0, 0.0, 0.0), paid=(50.0, 160.0, 0.0, 30.0))
        cells = w.add(starved, ALL_DUE)
        self.assertAlmostEqual(cells[2], 100.0 * 0.4 * 3.0, places=9)
        self.assertAlmostEqual(cells[3], 100.0 * 0.2 * 1.5, places=9)   # the old rho of step 2 stays
        self.assertEqual(w.add(starved, [True, True, False, True])[2], 0.0)   # room again: free

    def test_trivial_moves_are_bytes_at_no_cost(self):
        w = WB.WrittenCells(1)
        free = row(src=(100.0, 80.0, 40.0, 20.0), drop=(20.0, 40.0, 0.0, 0.0), paid=(50.0, 160.0, 0.0, 0.0))
        cells = w.add(free, ALL_DUE)                         # bytes left L2 and L3, nothing paid
        self.assertEqual(cells[2:], [0.0, 0.0])
        # Then starved with no room and never a paid merge: the step above's price, not the zero.
        starved = row(src=(100.0, 80.0, 0.0, 0.0), drop=(20.0, 40.0, 0.0, 0.0), paid=(50.0, 160.0, 0.0, 0.0))
        self.assertAlmostEqual(w.add(starved, ALL_DUE)[2], 100.0 * 0.4 * 2.0, places=9)

    def test_no_keys_no_charge(self):
        self.assertEqual(WB.WrittenCells(4).add(row(puts=0.0), ALL_DUE), [0.0] * 4)


class RowTest(unittest.TestCase):
    def test_the_row_of_a_window(self):
        recs = [
            {"type": "snap"},
            {"type": "flush_end", "x": 70},
            {"type": "job_end", "start_level": 0, "output_level": 1, "trivial": 0, "s": 100, "o": 300, "x": 380},
            {"type": "job_end", "start_level": 2, "output_level": 3, "trivial": 1, "s": 40, "o": 0, "x": 0},
            {"type": "job_end", "start_level": 2, "output_level": 3, "trivial": 0, "s": 60, "o": 90, "x": 120},
            {"type": "job_end", "start_level": 4, "output_level": 5, "trivial": 0, "s": 9, "o": 9, "x": 9},
            {"type": "snap"},
            {"type": "flush_end", "x": 1000},              # after hi: the next turn's
        ]
        r = WB.turn_row(recs, [1, 2, 3, 4, 5, 7], 0, 6, {0: [5.0, 400.0], 2: [7.0, 150.0]}, 12)
        self.assertEqual((r.flush, r.puts), (70.0, 12.0))
        self.assertEqual(r.src, [100.0, 0.0, 100.0, 0.0])    # the trivial move's bytes count
        self.assertEqual(r.drop, [20.0, 0.0, 30.0, 0.0])     # and drop nothing
        self.assertEqual(r.paid, [5.0, 0.0, 7.0, 0.0])


class TrainerTest(base.L0BillTest):
    """l0_bill_written 1: the reward's J = L0's bill + cells 1-3, with the cells rebuilt here from the
    same host log; 0 or absent: no field and the bill as before; without turn_l0_bill: refused."""

    def test_the_bill_adds_the_deep_cells(self):
        cfg = self.config(bill=1)
        pricer = L2.HostLogPricer(from_plugin_config(cfg))
        for r in base.host_log():
            pricer.add(r)
        with tempfile.TemporaryDirectory() as tmp:
            tr, log = self.run_trainer(tmp, cfg, l0_bill_written=1, written_turns=3)
            turns = [r for r in log if r["type"] == "turn"]
            self.assertEqual([r["t"] for r in turns], list(range(1, base.TURNS - 1)))
            lines = {r["t"]: r for r in (json.loads(x) for x in Path(tr.args.decisions).read_text().splitlines())
                     if r.get("type") == "turn"}
            want = WB.WrittenCells(3)
            for r in turns:
                lo, hi = pricer.bounds(r["op0"], r["op1"])
                puts = (TT.cm._counts(pricer.recs[hi], pricer.names)["put"] -
                        TT.cm._counts(pricer.recs[lo], pricer.names)["put"])
                cells = want.add(WB.turn_row(pricer.recs, pricer.job_idx, lo, hi,
                                             TT.merge_prices(pricer, lo, hi), puts),
                                 [v > 0 for v in lines[r["t"]]["owed"]])
                self.assertEqual(len(r["bill_cells"]), 4)
                for got, exp in zip(r["bill_cells"], cells):
                    self.assertAlmostEqual(got, exp, delta=1e-12 * max(1.0, abs(exp)))
                deep = sum(cells[1:])
                self.assertAlmostEqual(r["bill_written"], deep, delta=1e-12 * max(1.0, deep))
                self.assertAlmostEqual(r["J_reward"], r["bill_read"] + r["bill_merge"] + deep,
                                       delta=1e-12 * r["J_reward"])
            self.assertGreater(sum(r["bill_cells"][0] for r in turns), 0)   # the fixture's L0 merges

    def test_off_or_absent_leaves_the_bill_alone(self):
        cfg = self.config(bill=1)
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _, plain = self.run_trainer(a, cfg)
            tr, off = self.run_trainer(b, cfg, l0_bill_written=0)
            self.assertIsNone(tr.written)
            self.assertEqual(plain, off)
            self.assertFalse(any("bill_written" in r for r in off))

    def test_without_the_l0_bill_it_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self.trainer(tmp, self.config(bill=0), l0_bill_written=1)


if __name__ == "__main__":
    unittest.main()
