"""Bill Z (trainer settings l0_bill_z; learner/debt_bill.py): L0's bill plus the merges out of L1, L2
and L3 as paid (l0_bill_deep's part) plus the change of three debt ledgers, D_1 and D_2 on the bytes
L1 and L2 must still pass down, D_3 on the merge input L3 owes, all on level sizes rebuilt from the
host log's finished jobs. The ledgers and the sizes by hand, and the trainer's use of them."""

import json
import math
import tempfile
import unittest
from pathlib import Path

from learner import debt_bill as DB
from learner import learned2 as L2
from learner.prices import from_plugin_config
from tests import test_turn_trainer as base

try:
    from learner import turn_trainer as TT
except ImportError:          # no torch: the trainer's tests are skipped
    TT = None

MB = 1e6
BIG = 1e15                   # a target nothing reaches
NONE = [0.0] * 4


def tree(b0=0.0, b1=0.0, b2=0.0, b3=0.0, b4=0.0):
    return [b0, b1, b2, b3, b4, 0.0, 0.0]


def flows(l0=0.0, l1=0.0, l2=0.0, l3=0.0):
    return [l0, l1, l2, l3]


class OwedTest(unittest.TestCase):
    """What is owed at the end of a turn: bytes over target carried down, nothing at a level with
    nothing below it, and L3's in RocksDB's merge-input estimate."""

    def test_bytes_over_target_carry_down(self):
        C = [0.0, 10 * MB, 18 * MB, 25 * MB, BIG]
        B = tree(1 * MB, 10 * MB, 20 * MB, 30 * MB, 300 * MB)
        # carry 1; e_1 = 10 + 1 - 10 = 1; e_2 = 20 + 1 - 18 = 3; e_3 = 30 + 3 - 25 = 8
        E1, E2, W3 = DB.owed(B, C)
        self.assertAlmostEqual(E1, 1 * MB, delta=1e-6)
        self.assertAlmostEqual(E2, 3 * MB, delta=1e-6)
        self.assertAlmostEqual(W3, 8 * MB * (1 + 300 / 33), delta=1e-3)

    def test_nothing_below_owes_nothing_but_still_carries(self):
        C = [0.0, 10 * MB, 4 * MB, BIG, BIG]
        # L3 empty: E_2 = 0 although L2 is over target; L2 holds bytes, so E_1 counts.
        self.assertEqual(DB.owed(tree(0, 12 * MB, 5 * MB, 0, 0), C), [2 * MB, 0.0, 0.0])
        # L2 empty: E_1 = 0, but its excess is carried into L2's (which L3 now makes owed).
        C = [0.0, 10 * MB, 1 * MB, BIG, BIG]
        self.assertEqual(DB.owed(tree(0, 12 * MB, 0, 1, 0), C), [0.0, 1 * MB, 0.0])
        # L4 empty: L3 owes nothing, so no merge input either.
        C = [0.0, 10 * MB, 18 * MB, 25 * MB, BIG]
        self.assertEqual(DB.owed(tree(0, 10 * MB, 20 * MB, 30 * MB, 0), C)[2], 0.0)

    def test_without_l0s_carry_l0_owes_nothing(self):
        """Option 1 (carry_l0 False): the carry into L1 is 0, the rest as before."""
        C = [0.0, 10 * MB, 18 * MB, 25 * MB, BIG]
        B = tree(1 * MB, 10 * MB, 20 * MB, 30 * MB, 300 * MB)
        # e_1 = 10 - 10 = 0; e_2 = 20 + 0 - 18 = 2; e_3 = 30 + 2 - 25 = 7
        E1, E2, W3 = DB.owed(B, C, carry_l0=False)
        self.assertEqual(E1, 0.0)
        self.assertAlmostEqual(E2, 2 * MB, delta=1e-6)
        self.assertAlmostEqual(W3, 7 * MB * (1 + 300 / 32), delta=1e-3)
        # = the tree with L0 emptied, carried or not; L0's size changes nothing
        empty = tree(0, 10 * MB, 20 * MB, 30 * MB, 300 * MB)
        self.assertEqual(DB.owed(B, C, False), DB.owed(empty, C))
        self.assertEqual(DB.owed(B, C, False), DB.owed(tree(500 * MB, 10 * MB, 20 * MB, 30 * MB, 300 * MB), C, False))
        # and the default carries L0's bytes, as built
        self.assertEqual(DB.owed(B, C), DB.owed(B, C, True))
        self.assertNotEqual(DB.owed(B, C), DB.owed(B, C, False))

    def test_sizes_below_zero_count_as_empty_and_missing_levels_too(self):
        C = [0.0, 10 * MB, 4 * MB, BIG, BIG]
        self.assertEqual(DB.owed([-5 * MB, 12 * MB, 5 * MB, -1, 7], C), [2 * MB, 0.0, 0.0])
        self.assertEqual(DB.owed([0.0, 12 * MB, 5 * MB], C), [2 * MB, 0.0, 0.0])
        with self.assertRaises(ValueError):
            DB.owed([0.0, math.nan, 1.0, 1.0, 1.0], C)
        with self.assertRaises(ValueError):
            DB.owed(tree(), [0.0, None, 1.0, 1.0])


class LedgerTest(unittest.TestCase):
    """The ledger rule by hand. half_life 1 makes lam 1/2."""
    C = [0.0, 10 * MB, BIG, BIG, BIG]             # only L1 can owe (L2 holds a byte)

    def l1(self, size):
        return tree(0, size, 1.0, 0, 0)

    def test_growth_books_at_the_days_price_and_shrink_releases_at_the_booked_average(self):
        led = DB.DebtLedger(1)
        led.start(self.l1(10 * MB), self.C)                         # owes 0
        # turn 1: L1 merges cost 20 for 4 MB moved: own_1 = 5e-6; E_1 0 -> 2 MB books 10
        d = led.add(self.l1(12 * MB), self.C, flows(l1=20.0), flows(l1=4 * MB), flows(l1=4 * MB))
        self.assertEqual(led.prices[0], 20.0 / (4 * MB))
        self.assertAlmostEqual(d[0], 10.0, places=9)
        self.assertEqual(d[1:], [0.0, 0.0])
        # turn 2: P = 10 + 30, M = 2 MB + 2 MB: 1e-5; E_1 2 -> 4 MB books 20 more
        d = led.add(self.l1(14 * MB), self.C, flows(l1=30.0), flows(l1=2 * MB), flows(l1=2 * MB))
        self.assertAlmostEqual(led.prices[0], 1e-5, delta=1e-18)
        self.assertAlmostEqual(d[0], 20.0, places=9)
        self.assertAlmostEqual(led.balances[0], 30.0, places=9)
        # turn 3: the price triples (P = 20 + 100, M = 4 MB) and E_1 falls to a quarter: the balance
        # falls to a quarter (7.5), at its booked average, not re-priced at 3e-5
        d = led.add(self.l1(11 * MB), self.C, flows(l1=100.0), flows(l1=2 * MB), flows(l1=2 * MB))
        self.assertAlmostEqual(led.prices[0], 3e-5, delta=1e-18)
        self.assertAlmostEqual(d[0], -22.5, places=9)
        self.assertAlmostEqual(led.balances[0], 7.5, places=9)
        # turn 4: the price changes again, the amount owed does not: nothing moves
        d = led.add(self.l1(11 * MB), self.C, flows(l1=400.0), flows(l1=2 * MB), flows(l1=2 * MB))
        self.assertEqual(d, [0.0, 0.0, 0.0])
        self.assertAlmostEqual(led.balances[0], 7.5, places=9)
        # turn 5: cleared: the balance goes with it
        d = led.add(self.l1(9 * MB), self.C, NONE, NONE, NONE)
        self.assertAlmostEqual(d[0], -7.5, places=9)
        self.assertEqual(led.balances[0], 0.0)
        # turn 6: owed again, 1 MB, booked afresh at the day's price: turn 5's (P = 0.5 * 460 = 230,
        # M = 2 MB: 1.15e-4), kept because M has decayed to 1 MB, not above 1e6
        d = led.add(self.l1(11 * MB), self.C, NONE, NONE, NONE)
        self.assertAlmostEqual(led.prices[0], 230.0 / (2 * MB), delta=1e-15)
        self.assertAlmostEqual(d[0], 115.0, places=9)
        self.assertEqual(led.turns, 6)

    def test_the_starting_debt_is_never_booked(self):
        led = DB.DebtLedger(1)
        led.start(self.l1(15 * MB), self.C)                         # owes 5 MB from the start
        price = (flows(l1=10.0), flows(l1=2 * MB), flows(l1=2 * MB))  # 5e-6 per byte
        self.assertEqual(led.add(self.l1(15 * MB), self.C, *price), [0.0, 0.0, 0.0])
        self.assertEqual(led.add(self.l1(13 * MB), self.C, *price), [0.0, 0.0, 0.0])   # 0 x 3/5
        d = led.add(self.l1(14 * MB), self.C, *price)               # only the regrowth is booked
        self.assertAlmostEqual(d[0], led.prices[0] * 1 * MB, places=9)
        self.assertGreater(d[0], 0)
        self.assertEqual(led.balances, [d[0], 0.0, 0.0])

    def test_a_level_that_never_merged_borrows_the_price_above(self):
        C = [0.0, 10 * MB, 20 * MB, BIG, BIG]
        owe = lambda e1, e2: tree(0, 10 * MB + e1, 20 * MB + e2 - e1, 1.0, 0)   # E_1 = e1, E_2 = e2
        led = DB.DebtLedger(1)
        led.start(owe(0, 0), C)
        # Nothing merged anywhere: everything is free.
        self.assertEqual(led.add(owe(1 * MB, 1 * MB), C, NONE, NONE, NONE), [0.0, 0.0, 0.0])
        self.assertEqual(led.prices, [0.0, 0.0, 0.0])
        # A small L0 merge (under 1e6 bytes in the running sum) sets no price yet.
        led.add(owe(1 * MB, 1 * MB), C, flows(l0=1.0), flows(l0=0.5 * MB), flows(l0=0.5 * MB))
        self.assertEqual(led.prices[:2], [0.0, 0.0])
        # L0 merges (P = 0.5 + 8, M = 0.25 + 4 MB): L1 and L2 take L0's price.
        d = led.add(owe(2 * MB, 3 * MB), C, flows(l0=8.0), flows(l0=4 * MB), flows(l0=4 * MB))
        p0 = 8.5 / (4.25 * MB)
        self.assertAlmostEqual(led.prices[0], p0, delta=1e-18)
        self.assertEqual(led.prices[1], led.prices[0])
        self.assertAlmostEqual(d[0], p0 * 1 * MB, places=9)
        self.assertAlmostEqual(d[1], p0 * 2 * MB, places=9)
        # L1 merges at its own price: L2, still without merges, takes L1's (the nearest above).
        d = led.add(owe(3 * MB, 4 * MB), C, flows(l1=40.0), flows(l1=4 * MB), flows(l1=4 * MB))
        self.assertAlmostEqual(led.prices[0], 1e-5, delta=1e-18)
        self.assertEqual(led.prices[1], led.prices[0])
        self.assertAlmostEqual(d[1], 1e-5 * 1 * MB, places=9)
        # L2 merges: its own price.
        led.add(owe(3 * MB, 5 * MB), C, flows(l2=12.0), flows(l2=4 * MB), flows(l2=4 * MB))
        self.assertAlmostEqual(led.prices[1], 3e-6, delta=1e-18)

    def test_moves_without_rewriting_lower_the_price_and_count_as_a_price(self):
        """Files moved down without rewriting are bytes out of the level at no cost: a level whose
        only traffic is such moves has price 0 of its own, and does not borrow L0's."""
        led = DB.DebtLedger(1)
        led.start(self.l1(10 * MB), self.C)
        d = led.add(self.l1(12 * MB), self.C, flows(l0=8.0), flows(l0=4 * MB, l1=4 * MB), flows(l0=4 * MB))
        self.assertEqual(led.own[1], 0.0)
        self.assertEqual(led.prices[0], 0.0)
        self.assertEqual(d[0], 0.0)

    def test_l3s_merge_input_is_priced_per_byte_fed_and_falls_back_to_l0s(self):
        C = [0.0, 10 * MB, 18 * MB, 25 * MB, BIG]
        owe3 = lambda b3: tree(0, 10 * MB, 18 * MB, b3, 100 * MB)    # E_1 = E_2 = 0, E_3 = b3 - 25 MB
        w3 = lambda b3: (b3 - 25 * MB) * (1 + 100 * MB / b3)
        led = DB.DebtLedger(1)
        led.start(owe3(25 * MB), C)
        # L0 merges (8 for 4 MB fed): p_in takes L0's price.
        d = led.add(owe3(26 * MB), C, flows(l0=8.0), flows(l0=1 * MB), flows(l0=4 * MB))
        self.assertAlmostEqual(led.prices[2], 2e-6, delta=1e-18)
        self.assertAlmostEqual(d[2], 2e-6 * w3(26 * MB), places=6)
        # A dearer L0 turn does not move it: L0's price is taken once, while p_in is 0.
        before = led.balances[2]
        d = led.add(owe3(27 * MB), C, flows(l0=40.0), flows(l0=1 * MB), flows(l0=4 * MB))
        self.assertAlmostEqual(led.prices[2], 2e-6, delta=1e-18)
        self.assertAlmostEqual(d[2], 2e-6 * (w3(27 * MB) - w3(26 * MB)), places=6)
        # A merge out of L2 (10 for 2 MB fed; the 6 MB moved count in moved only): its own price.
        d = led.add(owe3(28 * MB), C, flows(l2=10.0), flows(l2=8 * MB), flows(l2=2 * MB))
        self.assertAlmostEqual(led.prices[2], 5e-6, delta=1e-18)
        self.assertAlmostEqual(d[2], 5e-6 * (w3(28 * MB) - w3(27 * MB)), places=6)
        self.assertAlmostEqual(led.balances[2], before + 2e-6 * (w3(27 * MB) - w3(26 * MB)) +
                               5e-6 * (w3(28 * MB) - w3(27 * MB)), places=6)
        # The L1-L3 byte sum decays under 1e6 (2 -> 1 -> 0.5 MB): p_in keeps its last value.
        led.add(owe3(28 * MB), C, NONE, NONE, NONE)
        led.add(owe3(28 * MB), C, flows(l0=100.0), flows(l0=4 * MB), flows(l0=4 * MB))
        self.assertAlmostEqual(led.prices[2], 5e-6, delta=1e-18)

    def test_without_l0s_carry_a_flush_books_nothing_and_l0s_merge_books_l1s_growth(self):
        """Option 1: bytes waiting in L0 owe nothing; when L0's merge puts them into L1 (over its
        target), L1's growth is booked in that turn, at the day's price. As built, the flush books."""
        C = [0.0, 10 * MB, BIG, BIG, BIG]
        price = (flows(l0=8.0, l1=20.0), flows(l0=4 * MB, l1=4 * MB), flows(l0=4 * MB, l1=4 * MB))
        led, old = DB.DebtLedger(1, carry_l0=False), DB.DebtLedger(1)
        self.assertEqual((led.carry_l0, old.carry_l0), (False, True))
        for x in (led, old):
            x.start(tree(0, 10 * MB, 1.0), C)
        # a flush of 3 MB: nothing owed without the carry; as built E_1 = 3 MB, booked at own_1 = 5e-6
        d, d_old = led.add(tree(3 * MB, 10 * MB, 1.0), C, *price), old.add(tree(3 * MB, 10 * MB, 1.0), C, *price)
        self.assertEqual(d, [0.0, 0.0, 0.0])
        self.assertAlmostEqual(d_old[0], 5e-6 * 3 * MB, places=9)
        # L0's merge moves the 3 MB into L1: now owed without the carry, booked at that turn's price
        # (P = 10 + 20, M = 2 + 4 MB: 5e-6); as built E_1 does not change, nothing moves
        d, d_old = led.add(tree(0, 13 * MB, 1.0), C, *price), old.add(tree(0, 13 * MB, 1.0), C, *price)
        self.assertAlmostEqual(led.prices[0], 30.0 / (6 * MB), delta=1e-18)
        self.assertAlmostEqual(d[0], led.prices[0] * 3 * MB, places=9)
        self.assertEqual(d_old, [0.0, 0.0, 0.0])
        # L1's merge clears it: both release what they booked
        d, d_old = led.add(tree(0, 10 * MB, 1.0), C, *price), old.add(tree(0, 10 * MB, 1.0), C, *price)
        self.assertAlmostEqual(d[0], -led.prices[0] * 3 * MB, places=9)
        self.assertAlmostEqual(d_old[0], -5e-6 * 3 * MB, places=9)
        self.assertEqual(led.balances, [0.0, 0.0, 0.0])
        # the prices are the same either way
        self.assertEqual(led.prices, old.prices)
        # what L0 holds at the start is never owed either
        led = DB.DebtLedger(1, carry_l0=False)
        led.start(tree(50 * MB, 10 * MB, 1.0), C)
        self.assertEqual(led.prev, [0.0, 0.0, 0.0])

    def test_bad_input_is_refused(self):
        with self.assertRaises(RuntimeError):
            DB.DebtLedger().add(tree(), self.C, NONE, NONE, NONE)    # no start
        for bad in (0, -1, math.inf, True, "64"):
            with self.subTest(half_life=bad), self.assertRaises(ValueError):
                DB.DebtLedger(bad)
        for bad in (0, 1, None, "False"):
            with self.subTest(carry_l0=bad), self.assertRaisesRegex(ValueError, "carry_l0"):
                DB.DebtLedger(64, carry_l0=bad)
        led = DB.DebtLedger()
        led.start(tree(), self.C)
        with self.assertRaises(ValueError):
            led.add(tree(), self.C, [0.0, 0.0, 0.0], NONE, NONE)    # three start levels
        with self.assertRaises(ValueError):
            led.add(tree(), self.C, NONE, [0.0, math.nan, 0.0, 0.0], NONE)
        self.assertEqual(DB.DebtLedger().lam, 0.5 ** (1 / 64))


class TreeSizesTest(unittest.TestCase):
    """Level sizes rebuilt from the finished jobs, as the replay's deltas(): L0 gains the flushes; a job
    takes its source out of its start level and puts output - overlap (+ the files it moved without
    rewriting) into its output level; a level beyond the list is left out."""

    def test_flushes_merges_and_moves(self):
        B = [10.0, 100.0, 1000.0]
        sizes = DB.TreeSizes(B)
        B[0] = 99.0                                     # a copy: the line's list is not shared
        sizes.apply(5.0, [])                            # a flush
        self.assertEqual(sizes.sizes, [15.0, 100.0, 1000.0])
        sizes.apply(0.0, [(0, 1, 4.0, 8.0, 11.0, 0.0)])                 # a merge, 4 + 8 -> 11
        self.assertEqual(sizes.sizes, [11.0, 103.0, 1000.0])
        sizes.apply(0.0, [(1, 2, 30.0, 0.0, 0.0, 30.0)])                # a move without rewriting
        self.assertEqual(sizes.sizes, [11.0, 73.0, 1030.0])
        sizes.apply(2.0, [(0, 1, 4.0, 8.0, 12.0, 0.0), (1, 2, 6.0, 9.0, 14.0, 0.0)])   # both in one call
        self.assertEqual(sizes.sizes, [9.0, 71.0, 1035.0])
        sizes.apply(0.0, [(0, 0, 4.0, 0.0, 3.0, 0.0)])                  # inside L0: 4 bytes in, 3 out
        self.assertEqual(sizes.sizes, [8.0, 71.0, 1035.0])

    def test_a_level_beyond_the_list_is_left_out(self):
        sizes = DB.TreeSizes([10.0, 100.0, 1000.0])
        sizes.apply(0.0, [(2, 3, 50.0, 70.0, 110.0, 0.0)])              # into L3, not tracked
        self.assertEqual(sizes.sizes, [10.0, 100.0, 950.0])
        sizes.apply(0.0, [(3, 4, 50.0, 70.0, 110.0, 0.0)])              # wholly below the list
        self.assertEqual(sizes.sizes, [10.0, 100.0, 950.0])

    def test_bad_input_is_refused(self):
        sizes = DB.TreeSizes([1.0, 2.0])
        with self.assertRaises(ValueError):
            sizes.apply(math.nan, [])
        with self.assertRaises(ValueError):
            sizes.apply(0.0, [(0, 1, 4.0, 8.0, 12.0)])
        with self.assertRaises(ValueError):
            sizes.apply(0.0, [(0, 1, 4.0, None, 12.0, 0.0)])
        with self.assertRaises(ValueError):
            DB.TreeSizes([1.0, math.inf])

    def test_finished_reads_the_indexed_records_between_two_others(self):
        recs = [{"type": "snap", "op": 0},
                {"type": "flush_end", "op": 1, "x": 5},
                {"type": "job_end", "op": 2, "start_level": 0, "output_level": 1, "trivial": 0, "s": 4,
                 "o": 8, "x": 11, "ok": 1},
                {"type": "snap", "op": 3},
                {"type": "job_end", "op": 4, "start_level": 1, "trivial": "1", "s": 30, "o": 0, "x": 0,
                 "ok": 1},
                {"type": "flush_end", "op": 5, "x": 7},
                {"type": "snap", "op": 6}]
        job_idx = [1, 2, 4, 5]
        self.assertEqual(DB.finished(recs, job_idx, 0, 3), (5.0, [(0, 1, 4.0, 8.0, 11.0, 0.0)]))
        self.assertEqual(DB.finished(recs, job_idx, 3, 6), (7.0, [(1, 2, 30.0, 0.0, 0.0, 30.0)]))
        self.assertEqual(DB.finished(recs, job_idx, 1, 6)[0], 7.0)        # strictly after record 1
        self.assertEqual(DB.finished(recs, job_idx, 6, 6), (0.0, []))
        for trivial in (True, 1, "true"):
            recs[4]["trivial"] = trivial
            self.assertEqual(DB.finished(recs, job_idx, 3, 6)[1][0][5], 30.0)
        recs[4]["trivial"] = 0
        self.assertEqual(DB.finished(recs, job_idx, 3, 6)[1][0][5], 0.0)


# --------------------------------------------------------------- the trainer
M = 1 << 20
TARGETS = [0.0, 100 * M, 300 * M, 900 * M, 2700 * M, 8100 * M, 24300 * M]
START = [20 * M, 110 * M, 360 * M, 950 * M, 2000 * M, 0, 0]     # line 0's tree
FLUSH = 20 * M


def targets(t):
    """Line t's C: L1's and L2's targets doubled at turns 10 and 11 (a multiplier change), so what is
    owed falls there with no merge to pay for it, and the bill goes below zero."""
    return [c * 2 if t in (10, 11) and i in (1, 2) else c for i, c in enumerate(TARGETS)]


def turn_jobs(t):
    """(start, output, s, o, x, trivial) of the jobs that end in turn t >= 1."""
    jobs = [(0, 1, 20 * M, 30 * M, 50 * M, 0)]
    if t % 3 == 0:
        jobs.append((1, 2, 60 * M, 120 * M, 180 * M, 0))
    if t % 4 == 1:
        jobs.append((2, 3, 80 * M, 160 * M, 240 * M, 0))
    if t % 4 == 3:
        jobs.append((2, 3, 20 * M, 0, 0, 1))            # a move without rewriting: no output bytes
    if t % 5 == 2:
        jobs.append((3, 4, 100 * M, 200 * M, 300 * M, 0))
    return jobs


def sizes_after(t, skip=()):
    """The tree after turn t by hand: line 0's, then each later turn's flush and jobs (but `skip`'s)."""
    out = list(START)
    for u in range(1, t + 1):
        if u in skip:
            continue
        out[0] += FLUSH
        for a, b, s, o, x, triv in turn_jobs(u):
            out[a] -= s
            out[b] += x - o + (s if triv else 0)
    return out


def in_flight(t):
    """The B the plugin logs at line t: the tree less the source bytes of the merges then running (the
    next turn's L1 and L2 merges have started), so it differs from the rebuilt sizes."""
    out = sizes_after(t)
    if (t + 1) % 3 == 0:
        out[1] -= 60 * M
    if (t + 1) % 4 == 1:
        out[2] -= 80 * M
    return out


def host_log():
    """Snaps every 500 operations; from turn 1 on, per turn a flush and the jobs turn_jobs(t) gives, all
    ending inside the turn; the drain at the last turn's end."""
    fg = base.fg
    recs = [{"type": "header", "fg": base.FG},
            {"type": "stamp", "name": "measure_start", "op": 0, "t_us": 0, "h": 1e9,
             "tickers": dict(zip(base.FG, fg(0)))}]
    job = 1
    for op in range(500, base.TURN * base.TURNS + 1, 500):
        recs.append({"type": "snap", "op": op, "t_us": op * 20, "fg": fg(op)})
        t, k = divmod(op, base.TURN)
        if t < 1:
            continue
        if k == 1000:
            recs.append({"type": "flush_begin", "job": job, "op": op, "t_us": op * 20, "fg": fg(op)})
            recs.append({"type": "flush_end", "job": job, "op": op + 1, "t_us": op * 20 + 900,
                         "x": FLUSH, "fg": fg(op + 1)})
            job += 1
        if k == 1500:
            for a, b, s, o, x, triv in turn_jobs(t):
                recs.append({"type": "job_begin", "job": job, "op": op, "t_us": op * 20, "fg": fg(op)})
                recs.append({"type": "job_end", "job": job, "op": op + 2000, "t_us": op * 20 + 40000,
                             "start_level": a, "output_level": b, "trivial": triv, "s": s, "o": o,
                             "x": x, "ok": 1, "fg": fg(op + 2000)})
                recs.append({"type": "h", "op": op + 2000, "h": 1e9 + op})
                job += 1
    recs.append({"type": "stamp", "name": "drain_start", "op": base.TURN * base.TURNS,
                 "t_us": base.TURN * base.TURNS * 20, "tickers": dict(zip(base.FG, fg(base.TURN * base.TURNS)))})
    return recs


def moved_bytes(pricer, lo, hi):
    """Bytes the merges that ended in the window took out of L0..L3, moves without rewriting
    included, read off the host log's records."""
    out = [0.0] * 4
    for r in pricer.recs[lo + 1:hi]:
        if r.get("type") == "job_end" and r.get("ok") in (True, 1) and r["start_level"] < 4 \
                and r.get("output_level", r["start_level"] + 1) > r["start_level"]:
            out[r["start_level"]] += r["s"]
    return out


ALL = frozenset(range(base.TURNS))


@unittest.skipIf(TT is None, "needs torch")
class BillZTrainerTest(base.L0BillTest):
    """l0_bill_z 1: J_reward = bill_read + bill_merge + the merges out of L1-L3 as paid + the debt
    ledgers' change, rebuilt here from the host log's jobs and each turn line's targets; the logged B
    counts only at the first line; a dropped line's jobs are taken at the next priced turn. 0 or
    absent: no field and the bill as before, B and C on the lines or not. Refused without
    turn_l0_bill, beside l0_bill_written 1 or l0_bill_deep 1, and for any value but 0 or 1; a first
    line without B and C, or a later one without C, stops the trainer."""

    def run_z(self, tmp, cfg, b_lines=ALL, c_lines=ALL, drop=(), log_host=None, **changes):
        tr = self.trainer(tmp, cfg, **changes)
        base.write(tr.args.host_log, log_host if log_host is not None else host_log())
        lines = [{"type": "turn_start", "arch": "vdn", "acting": 1, "features": base.TW.feature_names(1)}]
        for t in range(base.TURNS):
            line = base.turn_line(t, set_=1 if t % 4 == 0 else 0, mid=1 if t % 6 == 0 else 0,
                                  action="compact" if t % 2 else "expand")
            line["l0_read"] = base.l0_read(t)
            if t in b_lines:
                line["B"] = list(START) if t == 0 else in_flight(t)
            if t in c_lines:
                line["C"] = targets(t)
            if t in drop:                    # an empty turn: dropped, never priced
                line["op1"] = line["op0"]
            lines.append(line)
        lines.append({"type": "stop"})
        base.write(tr.args.decisions, lines)
        tr.step()
        self.assertTrue(tr.done())
        tr.finish("test")
        log = [json.loads(x) for x in Path(tr.args.log).read_text().splitlines()]
        return tr, log

    def rebuilt(self, cfg, turns, dropped=(), sizes=sizes_after, carry=True):
        """Per priced turn t: (paid, moved, fed, dD, balances, tree) from the host log, the trees by
        hand (`sizes`) and a fresh DebtLedger started from line 0's tree (carry False: option 1)."""
        pricer = L2.HostLogPricer(from_plugin_config(cfg))
        for r in host_log():
            pricer.add(r)
        led = DB.DebtLedger(64, carry_l0=carry)
        led.start(START, TARGETS)
        out = {}
        for t in range(1, base.TURNS):
            if t in dropped:
                continue
            b = pricer.bounds(t * base.TURN, (t + 1) * base.TURN)
            if b is None:
                break
            m = TT.merge_prices(pricer, *b)
            paid = [m.get(i, [0.0, 0.0])[0] for i in range(4)]
            fed = [m.get(i, [0.0, 0.0])[1] for i in range(4)]
            moved = moved_bytes(pricer, *b)
            d = led.add(sizes(t), targets(t), paid, moved, fed)
            out[t] = (paid, moved, fed, d, list(led.balances), sizes(t)[:5])
        self.assertEqual(sorted(out), [r["t"] for r in turns])
        return out

    def check_records(self, turns, want):
        for r in turns:
            paid, moved, fed, d, bal, tree_ = want[r["t"]]
            scale = max(1.0, sum(abs(v) for v in paid) + sum(abs(v) for v in d))
            self.assertEqual(r["tree"], tree_)
            for got, exp in zip(r["bill_deep_levels"], paid[1:]):
                self.assertAlmostEqual(got, exp, delta=1e-12 * scale)
            self.assertAlmostEqual(r["bill_deep"], sum(paid[1:]), delta=1e-12 * scale)
            self.assertEqual(len(r["bill_debt_levels"]), 3)
            for got, exp in zip(r["bill_debt_levels"], d):
                self.assertAlmostEqual(got, exp, delta=1e-12 * scale)
            self.assertAlmostEqual(r["bill_debt"], sum(d), delta=1e-12 * scale)
            for got, exp in zip(r["debt"], bal):
                self.assertAlmostEqual(got, exp, delta=1e-12 * max(1.0, abs(exp)))
            self.assertAlmostEqual(r["bill_merge"], paid[0], delta=1e-12 * scale)
            parts = r["bill_read"] + r["bill_merge"] + r["bill_deep"] + r["bill_debt"]
            self.assertAlmostEqual(r["J_reward"], parts, delta=1e-12 * (scale + r["bill_read"]))
            self.assertTrue(math.isfinite(r["r"]) and math.isfinite(r["td"]))

    def test_the_bill_follows_the_rebuilt_tree_not_the_logged_b(self):
        cfg = self.config(bill=1)
        with tempfile.TemporaryDirectory() as tmp:
            tr, log = self.run_z(tmp, cfg, l0_bill_z=1)
            self.assertIsNotNone(tr.debt)
            turns = [r for r in log if r["type"] == "turn"]
            self.assertEqual([r["t"] for r in turns], list(range(1, base.TURNS - 1)))
            want = self.rebuilt(cfg, turns)
            self.check_records(turns, want)
            # The pricer's job set: each turn's own merges, the move counted as moved, not fed.
            for t, (paid, moved, fed, _, _, _) in want.items():
                jobs = turn_jobs(t)
                self.assertEqual(moved, [sum(j[2] for j in jobs if j[0] == lv) for lv in range(4)])
                self.assertEqual(fed, [sum(j[2] + j[3] for j in jobs if j[0] == lv and not j[5])
                                       for lv in range(4)])
            # The logged B differs from the rebuilt tree, and a ledger fed it bills otherwise.
            logged = self.rebuilt(cfg, turns, sizes=in_flight)
            self.assertTrue(any(r["tree"] != in_flight(r["t"])[:5] for r in turns))
            self.assertTrue(any(abs(logged[t][3][k] - want[t][3][k]) > 1e-6 * (1 + abs(want[t][3][k]))
                                for t in want for k in range(3)))
            # Every ledger books and releases; the bill goes below zero in some turn and trains.
            for k in range(3):
                self.assertTrue(any(r["bill_debt_levels"][k] > 0 for r in turns), k)
                self.assertTrue(any(r["bill_debt_levels"][k] < 0 for r in turns), k)
            self.assertTrue(any(r["J_reward"] < 0 for r in turns))
            self.assertTrue(any(r["type"] == "train" for r in log))
            push = next(r for r in log if r["type"] == "push")
            self.assertAlmostEqual(push["jbar"], sum(-r["J_reward"] for r in turns[:5]) / 5,
                                   delta=1e-9 * max(1.0, abs(push["jbar"])))
            self.assertGreater(push["spread"], 0)

    def test_later_lines_b_is_not_read(self):
        cfg = self.config(bill=1)
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _, every = self.run_z(a, cfg, l0_bill_z=1)
            _, first = self.run_z(b, cfg, b_lines={0}, l0_bill_z=1)
            self.assertEqual(every, first)

    def test_a_dropped_lines_jobs_are_taken_at_the_next_priced_turn(self):
        """Mid-run (line 7) and right after the start (line 1, so the first priced turn begins past
        the first line's boundary): the gap's flush, merges and move are in the next turn's tree,
        though no bill prices them."""
        cfg = self.config(bill=1)
        for gap in (7, 1):
            with self.subTest(dropped=gap), tempfile.TemporaryDirectory() as tmp:
                tr, log = self.run_z(tmp, cfg, drop=(gap,), l0_bill_z=1)
                turns = [r for r in log if r["type"] == "turn"]
                self.assertEqual([r["t"] for r in turns], [t for t in range(1, base.TURNS - 1) if t != gap])
                self.assertEqual(tr.dropped, 2)
                want = self.rebuilt(cfg, turns, dropped=(gap,))
                self.check_records(turns, want)
                self.assertEqual(tr.debt.turns, len(turns))
                after = next(r for r in turns if r["t"] == gap + 1)
                self.assertEqual(after["tree"], sizes_after(gap + 1)[:5])
                self.assertNotEqual(after["tree"], sizes_after(gap + 1, skip=(gap,))[:5])
                self.assertEqual(tr.sizes.sizes, sizes_after(base.TURNS - 2))

    def test_the_debt_part_moves_the_reward(self):
        cfg = self.config(bill=1)
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _, deep = self.run_z(a, cfg, l0_bill_deep=1)
            _, z = self.run_z(b, cfg, l0_bill_z=1)
            d = {r["t"]: r for r in deep if r["type"] == "turn"}
            zt = [r for r in z if r["type"] == "turn"]
            self.assertTrue(any(r["bill_debt"] != 0 for r in zt))
            for r in zt:
                self.assertEqual(r["bill_deep_levels"], d[r["t"]]["bill_deep_levels"])
                self.assertAlmostEqual(r["J_reward"], d[r["t"]]["J_reward"] + r["bill_debt"],
                                       delta=1e-12 * (abs(r["J_reward"]) + abs(r["bill_debt"]) + 1.0))
                self.assertEqual(r["J"], d[r["t"]]["J"])        # the evaluator's J is untouched
                self.assertNotIn("tree", d[r["t"]])

    def test_off_or_absent_leaves_the_bill_alone(self):
        cfg = self.config(bill=1)
        logs = []
        for changes, lines in (({}, set()), ({}, ALL), ({"l0_bill_z": 0}, ALL)):
            with tempfile.TemporaryDirectory() as tmp:
                tr, log = self.run_z(tmp, cfg, b_lines=lines, c_lines=lines, log_host=base.host_log(),
                                     **changes)
                self.assertIsNone(tr.debt)
                self.assertIsNone(tr.sizes)
                logs.append(log)
        with tempfile.TemporaryDirectory() as tmp:
            _, plain = self.run_trainer(tmp, cfg)
        for log in logs:
            self.assertEqual(log, plain)
            self.assertFalse(any(k in r for r in log for k in ("bill_debt", "bill_debt_levels", "debt",
                                                                "tree", "bill_deep")))
        # And with l0_bill_deep 1 too: l0_bill_z 0 changes nothing.
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _, deep = self.run_trainer(a, cfg, l0_bill_deep=1)
            _, deep_z0 = self.run_z(b, cfg, log_host=base.host_log(), l0_bill_deep=1, l0_bill_z=0)
            self.assertEqual(deep, deep_z0)

    def test_without_the_l0_bill_it_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "turn_l0_bill 1"):
                self.trainer(tmp, self.config(bill=0), l0_bill_z=1)
            with self.assertRaisesRegex(ValueError, "turn_l0_bill 1"):
                self.trainer(tmp, self.config(bill=None), l0_bill_z=1)

    def test_beside_the_written_or_the_deep_bill_it_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "l0_bill_written"):
                self.trainer(tmp, self.config(bill=1), l0_bill_z=1, l0_bill_written=1)
            with self.assertRaisesRegex(ValueError, "already holds l0_bill_deep"):
                self.trainer(tmp, self.config(bill=1), l0_bill_z=1, l0_bill_deep=1)

    def test_bad_values_are_refused(self):
        for bad in (2, -1, 0.5, 1.0, "1", True, None):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(ValueError, "l0_bill_z is 0 or 1"):
                    self.trainer(tmp, self.config(bill=1), l0_bill_z=bad)

    def test_a_line_without_its_tree_stops_the_trainer(self):
        cfg = self.config(bill=1)
        for b_lines, c_lines, why in ((ALL - {0}, ALL, "no B and C"), (ALL, ALL - {0}, "no B and C"),
                                      (ALL, ALL - {5}, "no C")):
            with self.subTest(b=sorted(ALL - b_lines), c=sorted(ALL - c_lines)), \
                    tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(SystemExit, why):
                    self.run_z(tmp, cfg, b_lines=b_lines, c_lines=c_lines, l0_bill_z=1)

    def test_without_l0s_carry_the_ledgers_leave_l0_out(self):
        """l0_bill_z_carry 0 (option 1): the same bill with a ledger that leaves L0's bytes out; the
        merges as paid and the evaluator's J are unchanged, and every line says debt_carry 0."""
        cfg = self.config(bill=1)
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            tr, log = self.run_z(a, cfg, l0_bill_z=1, l0_bill_z_carry=0)
            _, built = self.run_z(b, cfg, l0_bill_z=1)
        self.assertFalse(tr.debt.carry_l0)
        turns = [r for r in log if r["type"] == "turn"]
        self.assertEqual([r["t"] for r in turns], list(range(1, base.TURNS - 1)))
        self.check_records(turns, self.rebuilt(cfg, turns, carry=False))
        self.assertTrue(all(r["debt_carry"] == 0 for r in turns))
        old = {r["t"]: r for r in built if r["type"] == "turn"}
        self.assertFalse(any("debt_carry" in r for r in old.values()))
        for r in turns:
            o = old[r["t"]]
            self.assertEqual((r["J"], r["bill_deep_levels"], r["bill_merge"], r["tree"]),
                             (o["J"], o["bill_deep_levels"], o["bill_merge"], o["tree"]))
        self.assertTrue(any(abs(r["bill_debt"] - old[r["t"]]["bill_debt"]) > 1e-9 for r in turns))

    def test_carry_1_or_absent_is_bill_z_as_built(self):
        cfg = self.config(bill=1)
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            tr, one = self.run_z(a, cfg, l0_bill_z=1, l0_bill_z_carry=1)
            _, absent = self.run_z(b, cfg, l0_bill_z=1)
        self.assertTrue(tr.debt.carry_l0)
        self.assertEqual(one, absent)
        # and without bill Z, carry 1 changes nothing either
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _, plain = self.run_trainer(a, cfg)
            _, plain1 = self.run_trainer(b, cfg, l0_bill_z_carry=1)
        self.assertEqual(plain, plain1)

    def test_carry_0_without_bill_z_or_bad_values_are_refused(self):
        for changes in ({}, {"l0_bill_z": 0}):
            with self.subTest(**changes), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(ValueError, "needs l0_bill_z 1"):
                    self.trainer(tmp, self.config(bill=1), l0_bill_z_carry=0, **changes)
        for bad in (2, -1, 0.0, 1.0, "0", True, False, None):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(ValueError, "l0_bill_z_carry is 0 or 1"):
                    self.trainer(tmp, self.config(bill=1), l0_bill_z=1, l0_bill_z_carry=bad)

    def test_lines_carry_their_tree(self):
        names = base.TW.feature_names(1)
        line = base.turn_line(3)
        self.assertIsNone(TT.parse_turn(line, names, 1).B)
        line.update(B=[1, 2.5, 0], C=[0, 10, 100])
        got = TT.parse_turn(line, names, 1)
        self.assertEqual((got.B, got.C), ([1.0, 2.5, 0.0], [0.0, 10.0, 100.0]))
        line["B"] = [1, None, 0]
        self.assertIsNone(TT.parse_turn(line, names, 1).B)


if __name__ == "__main__":
    unittest.main()
