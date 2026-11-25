"""Design 3 repaired of the level bill (learner/level_bill3.py; level_bills.py
with the trainer settings' level_bill3 1). By hand: the two prices and the
push price (mixed vs merge, the "full and not the last level" rule, the rest
of the trip), the ledgers with a level's own decision booked in the flow
and a neighbour's target change re-based outside it, the room credit A2
with its cap f Y and a second raise adding room, the copies formula A4
(cover alone when G = 0, the spread's sign when G > 0, 0 once B_j holds
C_j*, 0 with no scans), L1's rules, and the telescoping identity on a
synthetic run. On a synthetic run whose deep levels decide every turn: L0's
bill is turn_trainer's bill N to the last bit, every learning level's bill3
lines are Bill3r recomputed here from the host log, the hand-rebuilt sizes,
the lines' applied multipliers and rungs, each one-turn hold is paid its
turn's bill, the levels train, the option off (or 0) leaves the plain and
the design-2 logs byte for byte as before, and the refusals."""

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import torch
except ImportError:
    torch = None

from learner import level_bill3 as LB3  # noqa: E402

if torch is not None:
    from learner import level_bills as LB  # noqa: E402
    from learner import turn_trainer as TT  # noqa: E402
    from learner import learned2 as L2  # noqa: E402
    from learner import turn_ledger as TL  # noqa: E402
    from learner import turn_weights as TW  # noqa: E402
    from learner.prices import from_plugin_config  # noqa: E402
    from tests import test_debt_bill as dbt  # noqa: E402
    from tests import test_level_bill2 as lb2t  # noqa: E402
    from tests import test_level_bills as lbt  # noqa: E402
    from tests import test_turn_trainer as base  # noqa: E402

C5 = [0.0, 100.0, 300.0, 900.0, 2700.0, 8100.0]
MIXED = [1.0, 1.0, 2.0, 3.0, 4.0, 5.0]
MERGE = [10.0, 10.0, 20.0, 30.0, 40.0, 50.0]
Z6 = [0.0] * 6


def state(full, n, mixed=MIXED, merge=MERGE):
    full = [bool(v) for v in full]
    return {"mixed": list(mixed), "merge": list(merge), "push": LB3.push_vector(mixed, merge, full, n),
            "full": full, "n": n}


def step(b, B_prev, C_prev, cs_prev, B, C, cs, st_prev, st, reads=Z6, paid=Z6, moved=Z6,
         hm=Z6, h_old=Z6, dt=0):
    return b.add(B_prev, C_prev, cs_prev, B, C, cs, st_prev, st, reads, paid, moved, hm, h_old, dt)


class PricesTest(unittest.TestCase):
    def test_mixed_merge_and_the_size_model(self):
        pr = LB3.Prices3(5, 2.0)
        B = [0, 100, 300, 900, 50]
        # L1: 4e6 bytes taken out, 2e6 of them by merges (the rest plain moves), merge money 10.
        pr.add([0, 10, 0, 0, 0], [0, 4e6, 0, 0, 0], [0, 5e6, 0, 0, 0], [0, 2e6, 0, 0, 0])
        mixed, merge = pr.vectors(B)
        self.assertAlmostEqual(mixed[1], 10 / 4e6)          # design 2's price: plain moves in the bytes
        self.assertAlmostEqual(merge[1], 10 / 2e6)          # merges' own bytes only
        w = 10 / 5e6
        self.assertAlmostEqual(mixed[2], w * (1 + 3))        # no price of its own: the size model
        self.assertAlmostEqual(merge[2], w * (1 + 3))        # "no merge yet": the same size model
        self.assertAlmostEqual(merge[3], w * (1 + 50 / 900))
        # Plain moves only (no merge bytes): mixed has a price, merge falls back to the size model.
        pr2 = LB3.Prices3(4, 2.0)
        pr2.add([0, 0, 0, 0], [0, 3e6, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0])
        mixed, merge = pr2.vectors([0, 100, 100, 100])
        self.assertEqual(mixed[1], 0.0)
        self.assertEqual(merge[1], 0.0)                      # w not yet defined: 0
        self.assertIsNone(pr2.own_merge[1])
        with self.assertRaises(ValueError):
            pr2.add([0] * 4, [0] * 4, [0] * 4, [0, math.nan, 0, 0])

    def test_push_is_merge_only_into_a_full_level_that_is_not_the_last(self):
        looks = [([0, 100, 300, 900, 50], C5[:5])]
        full = LB3.full_flags(looks, 5)
        self.assertEqual(full, [True, True, True, True, False])
        mixed, merge = [1.0] * 5, [2.0] * 5
        # n = 4: L4 is the last level holding bytes, so pushing out of L3 is mixed (bytes may drop free).
        self.assertEqual(LB3.push_vector(mixed, merge, full, 4), [2.0, 2.0, 2.0, 1.0, 1.0])
        # n = 3 (L3 the last): out of L2 mixed as well.
        self.assertEqual(LB3.push_vector(mixed, merge, full, 3), [2.0, 2.0, 1.0, 1.0, 1.0])
        # A level below with room: mixed.
        self.assertEqual(LB3.push_vector(mixed, merge, [True, True, False, True, False], 4),
                         [2.0, 1.0, 2.0, 1.0, 1.0])
        # Full = at its target in force once in the looks (each look its own targets).
        two = looks + [([0, 50, 300, 800, 50], [0, 40, 400, 900, 2700])]
        self.assertEqual(LB3.full_flags(two, 5), [True, True, True, True, False])
        self.assertEqual(LB3.full_flags(two[1:], 5), [True, True, False, False, False])

    def test_rest_of_the_trip(self):
        push = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        full = [True, True, True, True, False, True]
        self.assertEqual(LB3.rest(push, full, 5, 2), 3.0 + 4.0)      # stops at L4 (room)
        self.assertEqual(LB3.rest(push, full, 3, 2), 3.0)            # never the last level (n = 3)
        self.assertEqual(LB3.rest(push, full, 5, 4), 0.0)
        self.assertEqual(LB3.rest(push, [True] * 6, 6, 1), 2 + 3 + 4 + 5 + 6.0)


class LedgerTest(unittest.TestCase):
    B = [0, 100, 300, 900, 1000, 0]                  # n = 4

    def test_owed(self):
        self.assertEqual(LB3.owed3([0, 150, 380, 950, 1000, 0], C5, 300.0, 2), [50.0, 130.0, 180.0])
        self.assertEqual(LB3.owed3([0, 150, 380, 950, 1000, 0], C5, 400.0, 2), [50.0, 30.0, 80.0])
        self.assertEqual(LB3.owed3([0, 150, 380, 950, 0, 0], C5, 300.0, 2), [50.0, 130.0, 0.0])
        # L1: E_1 and E_2 only, no pile above (L0 has no size target).
        self.assertEqual(LB3.owed3([900, 150, 380, 950, 1000, 0], C5, 100.0, 1), [50.0, 130.0])
        self.assertEqual(LB3.owed3([900, 150, 380, 0, 0, 0], C5, 100.0, 1), [50.0, 0.0])

    def test_own_decision_in_the_flow_neighbour_rebased_outside(self):
        st = state([1, 1, 1, 1, 0, 0], 4)
        self.assertEqual(st["push"], [10.0, 10.0, 20.0, 3.0, 4.0, 5.0])
        b = LB3.LevelBill3(2)
        b.start(self.B, C5, 300.0)
        r = step(b, self.B, C5, 300.0, self.B, C5, 300.0, None, st)
        self.assertEqual((r["A1"], r["dA1"], r["rb"], b.A1_start), (0.0, 0.0, [0.0] * 3, 0.0))
        self.assertEqual(r["led_p"], [10.0, 20.0, 3.0])
        # Own lowering C_2* 300 -> 150, sizes unchanged: E = [0, 150, 150] booked IN the flow.
        r = step(b, self.B, C5, 300.0, self.B, C5, 150.0, st, st)
        self.assertEqual(r["rb"], [0.0] * 3)
        self.assertEqual(r["E"], [0.0, 150.0, 150.0])
        self.assertAlmostEqual(r["dA1"], 20 * 150 + 3 * 150)
        self.assertAlmostEqual(r["dec1"], 20 * 150 + 3 * 150)
        self.assertAlmostEqual(r["bill"], r["dA1"] + r["dA2"] + r["dA4"])
        # L3's target in force halves (a neighbour): re-based outside the flow at the turn before's prices.
        b = LB3.LevelBill3(2)
        b.start(self.B, C5, 300.0)
        step(b, self.B, C5, 300.0, self.B, C5, 300.0, None, st)
        c3 = list(C5)
        c3[3] = 450.0
        before = state([1, 1, 1, 1, 0, 0], 4, mixed=[1, 1, 2, 7, 4, 5])
        r = step(b, self.B, C5, 300.0, self.B, c3, 300.0, before, st)
        self.assertEqual(r["rb"], [0.0, 0.0, 7.0 * 450])
        self.assertEqual(r["dA1"], 0.0)
        self.assertEqual(r["dec1"], 0.0)
        # A move two levels below does not touch the ledgers; L_j's own target in force neither.
        c4 = list(c3)
        c4[4] = 5400.0
        c4[2] = 150.0
        r = step(b, self.B, c3, 300.0, self.B, c4, 300.0, st, st)
        self.assertEqual((r["rb"], r["dA1"]), ([0.0] * 3, 0.0))

    def test_rest_of_the_trip_price_and_the_start(self):
        B = [0, 100, 400, 900, 2700, 10]             # n = 5, L4 full
        st = state([1, 1, 1, 1, 1, 0], 5)
        self.assertEqual(st["push"][3], 30.0)        # into L4: full and not the last level
        self.assertEqual(st["push"][4], 4.0)         # into L5: the last level
        b = LB3.LevelBill3(2)
        b.start(B, C5, 300.0)                        # E = [0, 100, 100] at the start
        r = step(b, B, C5, 300.0, B, C5, 300.0, None, st)
        self.assertEqual(r["led_p"], [10.0, 20.0, 30.0 + 4.0])
        self.assertEqual(b.A1_start, 20.0 * 100 + 34.0 * 100)
        self.assertEqual(r["dA1"], 0.0)              # the start is outside the flow
        # The pushed bytes leave: the ledgers give back at the booked average.
        B2 = [0, 100, 350, 950, 2700, 10]
        r = step(b, B, C5, 300.0, B2, C5, 300.0, st, st)
        self.assertEqual(r["E"], [0.0, 50.0, 100.0])
        self.assertAlmostEqual(r["dA1"], -20.0 * 50)


class RoomTest(unittest.TestCase):
    st = state([1, 1, 1, 1, 0, 0], 4)                # push = [10, 10, 20, 3, 4, 5]

    def run_turns(self, b, turns):
        """turns: (B_2 at the end, C_2*, bytes into L2 this turn); returns the parts per turn."""
        B = [0, 100, 300, 900, 1000, 0]
        cs = 300.0
        out = []
        for bj, c, inflow in turns:
            B2 = list(B)
            B2[2] = bj
            moved = [0.0, inflow, 0, 0, 0, 0]
            out.append(step(b, B, C5, cs, B2, C5, c, self.st, self.st, moved=moved))
            B, cs = B2, c
        return out

    def test_capped_credit_then_charged_back_and_a_second_raise_adds_room(self):
        b = LB3.LevelBill3(2)
        b.start([0, 100, 300, 900, 1000, 0], C5, 300.0)
        q = 20.0 + 3.0                                 # push_2 + rest(3): L3 full, above n = 4
        r = self.run_turns(b, [(300, 300, 5), (310, 600, 5), (320, 600, 5), (500, 600, 5),
                               (500, 849, 1000), (600, 849, 1000), (900, 849, 1000), (950, 849, 1000)])
        self.assertEqual((r[0]["X"], r[0]["A2"], r[0]["room"]), (0.0, 0.0, 0))
        # The raise: h = 300 (the boundary's B_2), then 310; X = 290 but f Y = 5 x 32 = 160.
        self.assertEqual((r[1]["raised"], r[1]["opened"], r[1]["X"], r[1]["X_eff"]), (1, 1, 290.0, 160.0))
        self.assertAlmostEqual(r[1]["q"], q)
        self.assertAlmostEqual(r[1]["A2"], -q * 160)
        self.assertAlmostEqual(r[1]["dA2"], -q * 160)
        # X still above f Y: the account does not move.
        self.assertEqual(r[2]["X"], 280.0)
        self.assertAlmostEqual(r[2]["dA2"], 0.0, delta=1e-9)
        # X below f Y: charged back in proportion.
        self.assertAlmostEqual(r[3]["X_eff"], 100.0)
        self.assertAlmostEqual(r[3]["A2"], -q * 100)
        # A second raise while the room is open adds its room; h and c0 are kept (nothing reset).
        self.assertEqual((r[4]["raised"], r[4]["opened"], r[4]["h"], r[4]["c0"]), (1, 0, 500.0, 300.0))
        self.assertAlmostEqual(r[4]["X"], 349.0)
        self.assertAlmostEqual(r[4]["X_eff"], 349.0)                  # f grew: no cap now
        self.assertAlmostEqual(r[4]["dA2"], -q * 249)
        self.assertEqual((b.raises, b.opens, b.adds), (2, 1, 1))
        self.assertAlmostEqual(r[5]["A2"], -q * 249)
        # The level fills past C_j*: the room closes, the credit is given back whole.
        self.assertEqual((r[6]["X"], r[6]["A2"], r[6]["room"]), (0.0, 0.0, 1))
        self.assertEqual((r[7]["room"], r[7]["X"]), (0, 0.0))

    def test_a_lowering_takes_room_away_and_no_room_when_the_level_holds_it(self):
        b = LB3.LevelBill3(2)
        b.start([0, 100, 300, 900, 1000, 0], C5, 300.0)
        r = self.run_turns(b, [(300, 600, 100), (300, 424, 100)])
        self.assertAlmostEqual(r[0]["A2"], -23.0 * 300)
        self.assertAlmostEqual(r[1]["A2"], -23.0 * 124)
        b = LB3.LevelBill3(2)
        b.start([0, 100, 700, 900, 1000, 0], C5, 300.0)
        B = [0, 100, 700, 900, 1000, 0]
        r = step(b, B, C5, 300.0, B, C5, 600.0, self.st, self.st, moved=[0, 100, 0, 0, 0, 0])
        self.assertEqual((r["raised"], r["X"], r["A2"], r["room"]), (1, 0.0, 0.0, 1))
        self.assertFalse(b.room)


class CopiesTest(unittest.TestCase):
    st = state([1, 1, 1, 1, 0, 0], 4)

    def a4(self, bj, cs, hm, h_old=None, dt=64, inflow=10.0, outflow=20.0, j=2, out_floor=False):
        b = LB3.LevelBill3(j, out_floor=out_floor)
        B = [0, 100, 300, 900, 1000, 0]
        b.start(B, C5, cs)
        B2 = list(B)
        B2[j] = bj
        moved = [0.0] * 6
        moved[j - 1], moved[j] = inflow, outflow
        return step(b, B2, C5, cs, B2, C5, cs, self.st, self.st, moved=moved, hm=hm,
                    h_old=h_old if h_old is not None else hm, dt=dt)

    def test_cover_alone_when_copies_do_not_spread(self):
        hm = [0, 0, 1.0, 2.0, 0.5, 0]
        r = self.a4(300.0, 600.0, hm)                       # a raise: rho 2, dB 300, tm 30, Yeff 17
        self.assertEqual((r["G"], r["rho"], r["dB"], r["tm"], r["Yeff"]), (0.0, 2.0, 300.0, 30.0, 17.0))
        self.assertAlmostEqual(r["dr"], (1.0 + 2.0) * 1.0)
        self.assertAlmostEqual(r["A4"], 17 * 3.0)
        self.assertGreater(r["A4"], 0)                      # a raise costs reads
        r = self.a4(300.0, 150.0, hm)                       # a lowering saves them
        self.assertAlmostEqual(r["dr"], 3.0 * (0.5 - 1.0))
        self.assertLess(r["A4"], 0)
        # A long move: tm = 640 / 10 = 64 > Y: Yeff = Y^2 / (2 tm) = 8.
        r = self.a4(300.0, 940.0, hm)
        self.assertEqual((r["tm"], r["Yeff"]), (64.0, 8.0))
        self.assertAlmostEqual(r["rho"], 2.0)               # clamped

    def test_spread_when_copies_still_spread_below(self):
        hm, old = [0, 0, 1.0, 2.0, 3.0, 0], [0, 0, 1.0, 1.0, 1.0, 0]
        G = (5.0 - 2.0) / 64                                # levels 3 .. n = 4
        r = self.a4(300.0, 600.0, hm, old)
        self.assertAlmostEqual(r["G"], G)
        self.assertAlmostEqual(r["spread"], G / 20.0 * 300)
        self.assertAlmostEqual(r["dr"], 3.0 * 1.0 - G / 20.0 * 300)
        self.assertGreater(r["dr"], 0)                       # little spread per byte sent down: cover wins
        r = self.a4(300.0, 600.0, hm, old, outflow=1.0)
        self.assertAlmostEqual(r["dr"], 3.0 * 1.0 - G * 300)
        self.assertLess(r["dr"], 0)                          # spread outweighs cover: the raise saves reads
        r = self.a4(300.0, 150.0, hm, old, outflow=1.0)
        self.assertAlmostEqual(r["dr"], 3.0 * -0.5 + G * 150)
        self.assertGreater(r["dr"], 0)                       # a lowering spreads copies faster: dearer reads
        self.assertLess(r["spread"], 0)
        # A fall below is no spread (G >= 0); no bytes leaving: no spread term.
        self.assertEqual(self.a4(300.0, 600.0, old, hm)["G"], 0.0)
        self.assertEqual(self.a4(300.0, 600.0, hm, old, outflow=0.0)["spread"], 0.0)
        # Fewer turns of history: the rise over the turns there are; none: 0.
        self.assertAlmostEqual(self.a4(300.0, 600.0, hm, old, dt=3)["G"], 1.0)
        self.assertEqual(self.a4(300.0, 600.0, hm, old, dt=0)["G"], 0.0)

    def test_out_floor_divides_the_spread_by_max_of_out_and_inflow(self):
        hm, old = [0, 0, 1.0, 2.0, 3.0, 0], [0, 0, 1.0, 1.0, 1.0, 0]
        G = (5.0 - 2.0) / 64
        # Off (lb3): a level sending almost nothing down blows the spread up; nothing sent: no spread.
        r = self.a4(300.0, 600.0, hm, old, outflow=1e-6)
        self.assertAlmostEqual(r["spread"], G / 1e-6 * 300, delta=1e-6 * G / 1e-6 * 300)
        self.assertNotIn("den", r)
        # On: the floor is the inflow f (10 per turn here).
        for outflow, den in ((1e-6, 10.0), (0.0, 10.0), (1.0, 10.0), (20.0, 20.0)):
            r = self.a4(300.0, 600.0, hm, old, outflow=outflow, out_floor=True)
            self.assertAlmostEqual(r["den"], den)
            self.assertAlmostEqual(r["spread"], G / den * 300)
            self.assertAlmostEqual(r["dr"], 3.0 * 1.0 - G / den * 300)
        # Same as off whenever out is at least f.
        a = self.a4(300.0, 150.0, hm, old, outflow=20.0)
        b = self.a4(300.0, 150.0, hm, old, outflow=20.0, out_floor=True)
        self.assertEqual({k: v for k, v in b.items() if k != "den"}, a)
        # Bounded: |Yeff x spread| <= Y^2 G for every move size and outflow.
        for cs in (150.0, 212.0, 424.0, 600.0, 940.0, 3000.0):
            for outflow in (0.0, 1e-9, 0.5, 10.0, 50.0):
                r = self.a4(300.0, cs, hm, old, outflow=outflow, out_floor=True)
                self.assertLessEqual(abs(r["Yeff"] * r["spread"]), LB3.Y ** 2 * G * (1 + 1e-12), (cs, outflow))
        # No inflow and nothing sent down: no spread, and the move never ends (Yeff 0).
        r = self.a4(300.0, 600.0, hm, old, inflow=0.0, outflow=0.0, out_floor=True)
        self.assertEqual((r["den"], r["spread"], r["A4"]), (0.0, 0.0, 0.0))

    def test_zero_once_the_level_holds_its_size_and_with_no_scans(self):
        hm, old = [0, 0, 1.0, 2.0, 3.0, 0], [0, 0, 1.0, 1.0, 1.0, 0]
        r = self.a4(600.0, 600.0, hm, old)
        self.assertEqual((r["rho"], r["dB"], r["tm"], r["dr"], r["A4"]), (1.0, 0.0, 0.0, 0.0, 0.0))
        near = [abs(self.a4(bj, 600.0, hm, old)["A4"]) for bj in (300.0, 450.0, 550.0, 590.0, 600.0)]
        self.assertEqual(near, sorted(near, reverse=True))
        # W2-like: no hidden steps anywhere: A4 = 0 whatever the decided size.
        for cs in (150.0, 300.0, 600.0):
            self.assertEqual(self.a4(300.0, cs, Z6, Z6)["A4"], 0.0)
        # No inflow: the move never ends, Yeff = 0.
        r = self.a4(300.0, 600.0, hm, old, inflow=0.0)
        self.assertEqual((r["tm"], r["Yeff"], r["A4"]), (math.inf, 0.0, 0.0))


class L1Test(unittest.TestCase):
    def test_l1_reads_from_l1_merges_from_l0_two_ledgers_rebased_by_l2(self):
        st = state([1, 1, 1, 1, 0, 0], 4)
        b = LB3.LevelBill3(1)
        B = [50, 150, 300, 900, 1000, 0]
        b.start(B, C5, 100.0)                        # E_1 = 50, E_2 = 50
        reads = [1.0, 2.0, 4.0, 8.0, 16.0, 32.0]
        paid = [3.0, 5.0, 7.0, 0, 0, 0]
        r = step(b, B, C5, 100.0, B, C5, 100.0, None, st, reads=reads, paid=paid,
                 moved=[40.0, 30.0, 0, 0, 0, 0], hm=[1.0, 2.0, 4.0, 0, 0, 0])
        self.assertEqual(r["led_lv"], [1, 2])
        self.assertEqual(r["led_p"], [10.0, 20.0 + 3.0])
        self.assertEqual(b.A1_start, 10 * 50 + 23 * 50)
        self.assertEqual(r["R"], 2 + 4 + 8 + 16 + 32.0)             # levels 1 .. n: not L0's
        self.assertEqual(r["M"], 3 + 5 + 7.0)                        # merges out of L0 .. L_(n-1)
        self.assertEqual((r["f"], r["out"]), (40.0, 30.0))           # into L1 = out of L0
        self.assertEqual((r["h_j"], r["h_j1"]), (2.0, 4.0))
        # L1's own target in force (its glide) and L0 do not re-base; L2's target does.
        c1 = list(C5)
        c1[1] = 70.0
        c1[0] = 5.0
        r = step(b, B, C5, 100.0, B, c1, 100.0, st, st)
        self.assertEqual((r["rb"], r["dA1"]), ([0.0, 0.0], 0.0))
        c2 = list(c1)
        c2[2] = 200.0
        r = step(b, B, c1, 100.0, B, c2, 100.0, st, st)
        self.assertEqual(r["E"], [50.0, 150.0])
        self.assertEqual(r["rb"], [0.0, 23.0 * 100])
        # Its own raise: room booked at push_1 + rest(2).
        r = step(b, B, c2, 100.0, B, c2, 200.0, st, st, moved=[40.0, 30.0, 0, 0, 0, 0])
        self.assertEqual((r["raised"], r["X"]), (1, 50.0))
        self.assertAlmostEqual(r["q"], 10.0 + 20.0 + 3.0)
        with self.assertRaises(ValueError):
            LB3.LevelBill3(0)


class IdentityTest(unittest.TestCase):
    def test_sum_of_bills_is_paid_plus_accounts_plus_rebases(self):
        n_lv = 7
        run = LB3.Bill3Run([1, 2, 3, 4], 32)
        C = [0.0, 100.0, 300.0, 900.0, 2700.0, 8100.0, 24300.0]
        B = [30.0, 110.0, 290.0, 950.0, 2600.0, 400.0, 0.0]
        cs = {1: 100.0, 2: 300.0, 3: 900.0, 4: 2700.0}
        run.start(B, C, cs, 3.0)
        rows = {j: [] for j in cs}
        rungs = (0.5, 0.7071067811865476, 1.0, 1.4142135623730951, 2.0)
        for t in range(1, 161):
            k = (t * 7919) % 101
            B = [max(0.0, b + ((k % 13) - 6) * (1 + i) * 3.0) for i, b in enumerate(B)]
            B[5] = max(B[5], 1.0)
            m = [1.0] * n_lv
            for i in (1, 2, 3, 4):
                m[i] = rungs[(t // (5 + i) + i) % 5]
            Cin = [c * (m[i] if i > 0 else 1.0) * (1.0 if t % 9 else 0.9) for i, c in enumerate(C)]
            cs = {j: C[j] * rungs[(t // (3 + j) + 2 * j) % 5] for j in cs}
            paid = [((k + i) % 5) * 0.3 for i in range(n_lv)]
            moved = [((k * (i + 2)) % 17) * 2e5 for i in range(n_lv)]
            fed = [v * 1.5 for v in moved]
            mergeout = [v * 0.6 for v in moved]
            R = [((k + 3 * i) % 7) * 0.01 for i in range(n_lv)]
            H = [((k + i) % 4) * 0.002 * t for i in range(n_lv)]
            out, _ = run.add(t, B, Cin, cs, paid, moved, fed, mergeout, R, H)
            for j, parts in out.items():
                rows[j].append(parts)
        for j, bill in run.bills.items():
            r = rows[j]
            lhs = sum(x["bill"] for x in r) + sum(x["rb_sum"] for x in r)
            rhs = sum(x["R"] + x["M"] for x in r) + r[-1]["A"] - bill.A1_start
            self.assertAlmostEqual(lhs, rhs, delta=1e-9 * max(1.0, sum(abs(x["bill"]) for x in r)), msg=j)
            self.assertTrue(any(x["raised"] for x in r))
            self.assertTrue(any(x["rb_sum"] != 0 for x in r), j)
            self.assertTrue(any(x["dA2"] != 0 for x in r) and any(x["dA4"] != 0 for x in r))
            self.assertTrue(all(math.isfinite(x["bill"]) for x in r))
            self.assertAlmostEqual(r[-1]["A1"], bill.A1_start + sum(x["rb_sum"] + x["dA1"] for x in r),
                                   delta=1e-9 * max(1.0, abs(r[-1]["A1"])))


class Lb5FixesTest(unittest.TestCase):
    """The three options after lb5's cause study: st_trap, owed "capped", the merge account A3."""
    B = [0.0, 100.0, 300.0, 900.0, 2700.0, 0.0]
    C = [0.0, 100.0, 300.0, 900.0, 2700.0, 8100.0]
    HM = [0.0, 1.0, 2.0, 4.0, 8.0, 0.5]
    PRE = (HM, [1.0, 1.0, 2.0, 4.0, 2.0, 0.0], [3.0, 10.0, 10.0, 9.0, 6.0, 1.0])

    def test_without_the_trap_a_raise_costs_a_lowering_saves_and_a_round_trip_is_not_paid(self):
        st_ = state([False, True, True, True, False, False], 4)
        rates = {}
        for trap in (None, 0.0):
            b = LB3.LevelBill3(2, a4_form="st", st_trap=trap)
            b.start(self.B, self.C, 300.0)
            seq = [300.0, 300.0 * RUNGS[3], 300.0]            # up one rung, then back
            got, prev = [], 300.0
            for cs in seq:
                r = b.add(self.B, self.C, prev, self.B, self.C, cs, st_, st_, Z6, Z6, Z6, self.HM, self.HM, 64, self.PRE)
                got.append(r["st_rate"])
                prev = cs
            rates[trap] = got
        up = LB3.st_parts(*self.PRE, 2, RUNGS[3])
        down = LB3.st_parts(*self.PRE, 2, 1 / RUNGS[3])
        own = lambda p: LB3.ST_A["own"] * p["own"] + LB3.ST_A["steal"] * p["steal"]  # noqa: E731
        self.assertAlmostEqual(rates[0.0][1], own(up))
        self.assertAlmostEqual(rates[0.0][2], own(down))
        self.assertGreater(rates[0.0][1], 0)                  # a raise is booked as a cost
        self.assertLess(rates[0.0][2], 0)                     # a lowering as a saving
        self.assertGreater(rates[0.0][1] + rates[0.0][2], 0)  # up and back nets a small charge, never a credit
        self.assertAlmostEqual(rates[None][1], own(up) + LB3.ST_A["trap"] * up["trap"])   # the default keeps the trap
        self.assertLess(rates[None][1] + rates[None][2], 0)   # ... and pays the round trip (lb5's flipping)
        for bad in ({"a4_form": "copies", "st_trap": 0.0}, {"a4_form": "st", "st_trap": -1.0},
                    {"a4_form": "st", "st_trap": math.inf}, {"owed": "nope"}):
            with self.assertRaises(ValueError, msg=bad):
                LB3.LevelBill3(2, **bad)

    def test_owed_capped_gives_no_credit_above_the_normal_size_and_is_the_old_ledger_below_it(self):
        st_ = state([False, True, True, True, False, False], 4)
        b = LB3.LevelBill3(2, owed="capped")
        with self.assertRaises(ValueError):
            b.start(self.B, self.C, 300.0)                    # needs the normal targets
        b.start(self.B, self.C, 300.0, self.C)
        # A raise from x1 to x2 (target in force too): nothing is booked, no room opens.
        C2 = list(self.C)
        C2[2] = 600.0
        r = b.add(self.B, self.C, 300.0, self.B, C2, 600.0, st_, st_, Z6, Z6, Z6, Z6, Z6, 0, Cn=self.C)
        self.assertEqual((r["dA1"], r["dA2"], r["rb_sum"], r["dec1"], r["room"], r["raised"], r["bill"]),
                         (0.0, 0.0, 0.0, 0.0, 0, 0, 0.0))
        self.assertEqual((r["owed"], r["cstar"], r["cstar_led"]), ("capped", 600.0, 300.0))
        # The level fills to 400: the 100 bytes above its normal size are owed (the clean-up will merge them),
        # carried on to L3, which is at its normal size already.
        B2 = list(self.B)
        B2[2] = 400.0
        r = b.add(self.B, C2, 600.0, B2, C2, 600.0, st_, st_, Z6, Z6, Z6, Z6, Z6, 0, Cn=self.C)
        price = b.ledger_prices(st_)
        self.assertEqual(r["E"], [0.0, 100.0, 100.0])
        self.assertAlmostEqual(r["dA1"], 100.0 * price[1] + 100.0 * price[2])
        self.assertEqual(r["dA2"], 0.0)
        # The default books the same raise as a credit at once (what the clean-up takes back).
        d = LB3.LevelBill3(2)
        d.start(B2, self.C, 300.0)
        r = d.add(B2, self.C, 300.0, B2, C2, 600.0, st_, st_, Z6, Z6, Z6, Z6, Z6, 0)
        self.assertLess(r["bill"], 0)
        # At or below the normal size the capped ledger is the old one, part for part: a lowering books its
        # push-down at once, the raise back its refund and room.
        old, cap = LB3.LevelBill3(2), LB3.LevelBill3(2, owed="capped")
        old.start(self.B, self.C, 300.0)
        cap.start(self.B, self.C, 300.0, self.C)
        seq = [(300.0, 150.0), (150.0, 150.0), (150.0, 300.0), (300.0, 300.0)]
        Bs = [self.B, [0.0, 100.0, 200.0, 1000.0, 2700.0, 0.0], [0.0, 100.0, 200.0, 1000.0, 2700.0, 0.0],
              [0.0, 100.0, 260.0, 1000.0, 2700.0, 0.0]]
        Bp = self.B
        moved = [0.0, 50.0, 60.0, 0.0, 0.0, 0.0]
        seen = []
        for (cp, cn), Bn in zip(seq, Bs):
            Cp, Cc = list(self.C), list(self.C)
            Cp[2], Cc[2] = cp, cn
            x = old.add(Bp, Cp, cp, Bn, Cc, cn, st_, st_, Z6, Z6, moved, Z6, Z6, 0)
            y = cap.add(Bp, Cp, cp, Bn, Cc, cn, st_, st_, Z6, Z6, moved, Z6, Z6, 0, Cn=self.C)
            for k in ("bill", "dA1", "dA2", "rb_sum", "E", "room", "raised"):
                self.assertEqual(x[k], y[k], k)
            seen.append(x["bill"])
            Bp = Bn
        self.assertGreater(seen[0], 0)                        # the lowering's push-down, booked at the decision
        self.assertLess(seen[2], 0)                           # the raise back to normal: its room

    def test_the_merge_account_books_the_size_ratio_rule_and_releases_it(self):
        st_ = state([False, True, True, True, False, False], 4)
        b = LB3.LevelBill3(2, a3=True)
        b.start(self.B, self.C, 300.0)
        paid = [0.0, 6.0, 4.0, 0.0, 0.0, 0.0]                 # merges out of L1 and out of L2
        fed = [0.0, 30.0, 40.0, 0.0, 0.0, 0.0]
        src = [0.0, 10.0, 10.0, 0.0, 0.0, 0.0]                # 2/3 and 3/4 of the bytes fed are the level below's
        kw = dict(fed=fed, mergeout=src)
        with self.assertRaises(ValueError):
            b.add(self.B, self.C, 300.0, self.B, self.C, 300.0, st_, st_, Z6, paid, Z6, Z6, Z6, 0)
        for _ in range(3):
            r = b.add(self.B, self.C, 300.0, self.B, self.C, 300.0, st_, st_, Z6, paid, Z6, Z6, Z6, 0, **kw)
            self.assertEqual((r["dA3"], r["a3_rate"]), (0.0, 0.0))
        m_in, m_out = 6.0 * 2 / 3, 4.0 * 3 / 4                # constant series: the running means are the values
        rho = RUNGS[4]
        r = b.add(self.B, self.C, 300.0, self.B, self.C, 600.0, st_, st_, Z6, paid, Z6, Z6, Z6, 0, **kw)
        self.assertAlmostEqual(r["m_in"], m_in)
        self.assertAlmostEqual(r["m_out"], m_out)
        want = m_in * (rho - 1) + m_out * (1 / rho - 1)       # dearer merges into the level, cheaper out of it
        self.assertAlmostEqual(r["a3_rate"], want)
        self.assertAlmostEqual(r["A3"], want * LB3.ST_B[LB3.ST_HOLD])
        self.assertAlmostEqual(r["bill"], r["M"] + r["dA3"])
        self.assertAlmostEqual(r["A"], r["A1"] + r["A2"] + r["A4"] + r["A3"])
        total = r["dA3"]
        for age in range(1, LB3.ST_HOLD + 3):
            r = b.add(self.B, self.C, 600.0, self.B, self.C, 600.0, st_, st_, Z6, paid, Z6, Z6, Z6, 0, **kw)
            total += r["dA3"]
        self.assertEqual(r["A3"], 0.0)
        self.assertAlmostEqual(total, 0.0, delta=1e-9)        # everything booked is released
        # With equal money into and out of the level, a move either way is a charge (evenly spaced sizes are best).
        for rho in (RUNGS[1], RUNGS[3]):
            self.assertGreater(5.0 * (rho - 1) + 5.0 * (1 / rho - 1), 0)
        self.assertNotIn("dA3", LB3.LevelBill3(2).__dict__)

    def test_the_probe_account_books_one_filter_probe_per_get_that_moves_a_level(self):
        st_ = state([False, True, True, True, False, False], 4)
        b = LB3.LevelBill3(2, a4_form="st", probe_price=0.25)
        b.start(self.B, self.C, 300.0)
        r = b.add(self.B, self.C, 300.0, self.B, self.C, 300.0, st_, st_, Z6, Z6, Z6, self.HM, self.HM, 64, self.PRE)
        self.assertEqual((r["dA5"], r["a5_rate"]), (0.0, 0.0))
        r = b.add(self.B, self.C, 300.0, self.B, self.C, 600.0, st_, st_, Z6, Z6, Z6, self.HM, self.HM, 64, self.PRE)
        want = -(2.0 - 1.0) * self.PRE[1][2] * 0.25           # a raise: 2 gets per turn found one level earlier
        self.assertAlmostEqual(r["a5_rate"], want)
        self.assertAlmostEqual(r["A5"], want * LB3.ST_B[LB3.ST_HOLD])
        self.assertAlmostEqual(r["dA5"], r["A5"])
        self.assertAlmostEqual(r["A"], r["A1"] + r["A2"] + r["A4"] + r["A5"])
        total = r["dA5"]
        r = b.add(self.B, self.C, 600.0, self.B, self.C, 300.0, st_, st_, Z6, Z6, Z6, self.HM, self.HM, 64, self.PRE)
        self.assertAlmostEqual(r["a5_rate"], -(0.5 - 1.0) * self.PRE[1][2] * 0.25)   # the lowering: a cost
        total += r["dA5"]
        for _ in range(LB3.ST_HOLD + 3):
            r = b.add(self.B, self.C, 300.0, self.B, self.C, 300.0, st_, st_, Z6, Z6, Z6, self.HM, self.HM, 64, self.PRE)
            total += r["dA5"]
        self.assertEqual(r["A5"], 0.0)
        self.assertAlmostEqual(total, 0.0, delta=1e-9)
        plain = LB3.LevelBill3(2, a4_form="st")
        plain.start(self.B, self.C, 300.0)
        r = plain.add(self.B, self.C, 300.0, self.B, self.C, 600.0, st_, st_, Z6, Z6, Z6, self.HM, self.HM, 64, self.PRE)
        self.assertNotIn("dA5", r)
        for bad in ({"a4_form": "copies", "probe_price": 0.1}, {"a4_form": "st", "probe_price": -1.0}):
            with self.assertRaises(ValueError, msg=bad):
                LB3.LevelBill3(2, **bad)

    def test_the_identity_holds_with_every_option(self):
        run = LB3.Bill3Run([1, 2, 3], 32, a4_form="st", owed="capped", a3=True, st_trap=0.0, probe_price=0.003)
        C = [0.0, 100.0, 300.0, 900.0, 2700.0, 8100.0]
        B = [30.0, 110.0, 290.0, 950.0, 2600.0, 400.0]
        cs = {1: 100.0, 2: 300.0, 3: 900.0}
        run.start(B, C, cs, 3.0, Cn=C)
        rows = {j: [] for j in cs}
        for t in range(1, 161):
            k = (t * 7919) % 101
            B = [max(0.0, b + ((k % 13) - 6) * (1 + i) * 3.0) for i, b in enumerate(B)]
            B[5] = max(B[5], 1.0)
            cs = {j: C[j] * RUNGS[(t // (3 + j) + 2 * j) % 5] for j in cs}
            Cin = [C[i] * (cs[i] / C[i] if i in cs else 1.0) for i in range(6)]
            paid = [((k + i) % 5) * 0.3 for i in range(6)]
            moved = [((k * (i + 2)) % 17) * 2e5 for i in range(6)]
            R = [((k + 3 * i) % 7) * 0.01 for i in range(6)]
            H = [((k + i) % 4) * 0.002 * t for i in range(6)]
            q = [1.0 + ((k + i) % 3) for i in range(6)]
            s_ = [10.0, 10.0, 10.0, 9.0, 6.0, 1.0]
            out, _ = run.add(t, B, Cin, cs, paid, moved, [v * 1.5 for v in moved], [v * 0.6 for v in moved], R, H,
                             q=q, s=s_, Cn=C)
            for j, parts in out.items():
                rows[j].append(parts)
        for j, bill in run.bills.items():
            r = rows[j]
            self.assertTrue(all(x["cstar_led"] <= C[j] for x in r) and any(x["cstar"] > C[j] for x in r))
            self.assertTrue(any(x["dA3"] != 0 for x in r) and any(x["dA4"] != 0 for x in r))
            self.assertTrue(any(x["dA5"] != 0 for x in r))
            lhs = sum(x["bill"] + x["rb_sum"] for x in r)
            rhs = sum(x["R"] + x["M"] for x in r) + r[-1]["A"] - bill.A1_start
            self.assertAlmostEqual(lhs, rhs, delta=1e-9 * max(1.0, sum(abs(x["bill"]) for x in r)), msg=j)


class NoReadAccountTest(unittest.TestCase):
    """The read account's form "none" (level_bill3_a4 "none", 2026-11-21): nothing is booked for the hidden steps."""

    def rows(self, **options):
        run = LB3.Bill3Run([1, 2, 3], 32, owed="capped", a3=True, **options)
        C = [0.0, 100.0, 300.0, 900.0, 2700.0, 8100.0]
        B = [30.0, 110.0, 290.0, 950.0, 2600.0, 400.0]
        cs = {1: 100.0, 2: 300.0, 3: 900.0}
        run.start(B, C, cs, 3.0, Cn=C)
        rows = {j: [] for j in cs}
        for t in range(1, 161):
            k = (t * 7919) % 101
            B = [max(0.0, b + ((k % 13) - 6) * (1 + i) * 3.0) for i, b in enumerate(B)]
            B[5] = max(B[5], 1.0)
            cs = {j: C[j] * RUNGS[(t // (3 + j) + 2 * j) % 5] for j in cs}
            Cin = [C[i] * (cs[i] / C[i] if i in cs else 1.0) for i in range(6)]
            paid = [((k + i) % 5) * 0.3 for i in range(6)]
            moved = [((k * (i + 2)) % 17) * 2e5 for i in range(6)]
            R = [((k + 3 * i) % 7) * 0.01 for i in range(6)]
            H = [((k + i) % 4) * 0.002 * t for i in range(6)]
            qs = {}
            if options.get("a4_form") == "st":
                qs = {"q": [1.0 + ((k + i) % 3) for i in range(6)], "s": [10.0, 10.0, 10.0, 9.0, 6.0, 1.0]}
            out, _ = run.add(t, B, Cin, cs, paid, moved, [v * 1.5 for v in moved], [v * 0.6 for v in moved], R, H,
                             Cn=C, **qs)
            for j, parts in out.items():
                rows[j].append(parts)
        return run, rows

    def test_nothing_is_booked_and_the_other_accounts_are_untouched(self):
        run, none = self.rows(a4_form="none")
        _, st = self.rows(a4_form="st", st_trap=0.0)
        for j, bill in run.bills.items():
            r, w = none[j], st[j]
            self.assertTrue(any(x["dA4"] != 0 for x in w))          # the same moves do book with the steal / trap form
            self.assertTrue(any(x["cover"] != 0 for x in r))        # the copies formula's inputs are still logged
            for x, y in zip(r, w):
                self.assertEqual((x["dA4"], x["A4"], x["a4"]), (0.0, 0.0, "none"))
                self.assertFalse(any(k.startswith("st_") for k in x))
                for key in ("R", "M", "dA1", "dA2", "dA3", "A1", "A2", "A3", "rb_sum"):
                    self.assertEqual(x[key], y[key], (j, key))
                self.assertAlmostEqual(x["bill"], x["R"] + x["M"] + x["dA1"] + x["dA2"] + x["dA3"])
                self.assertAlmostEqual(x["bill"], y["bill"] - y["dA4"])
                self.assertAlmostEqual(x["A"], x["A1"] + x["A2"] + x["A3"])
            lhs = sum(x["bill"] + x["rb_sum"] for x in r)
            rhs = sum(x["R"] + x["M"] for x in r) + r[-1]["A"] - bill.A1_start
            self.assertAlmostEqual(lhs, rhs, delta=1e-9 * max(1.0, sum(abs(x["bill"]) for x in r)), msg=j)

    def test_the_steal_trap_options_are_refused_and_no_filter_counts_are_needed(self):
        for bad in ({"st_trap": 0.0}, {"probe_price": 0.1}):
            with self.assertRaises(ValueError, msg=bad):
                LB3.LevelBill3(2, a4_form="none", **bad)
        b = LB3.LevelBill3(2, a4_form="none")
        B = [0.0, 100.0, 300.0, 900.0, 0.0, 0.0]
        C = [0.0, 100.0, 300.0, 900.0, 2700.0, 0.0]
        st_ = state([False, True, True, False, False, False], 3)
        b.start(B, C, 300.0)
        r = b.add(B, C, 300.0, B, C, 600.0, st_, st_, Z6, Z6, Z6, [1.0] * 6, [1.0] * 6, 64)   # a raise, no "pre"
        self.assertEqual((r["dA4"], r["A4"]), (0.0, 0.0))
        self.assertGreater(r["cover"], 0)


# --------------------------------------------------------------- the trainer
ACT = 4
NL = 7
PERIODS = "1:1:1:1"
RUNGS = (0.5, 0.7071067811865476, 1.0, 1.4142135623730951, 2.0)


class StealTrapReadAccountTest(unittest.TestCase):
    """The read account's steal / trap form (level_bill3_a4 "st", batch lb5)."""

    def test_parts_by_hand(self):
        hm = [0.0, 1.0, 2.0, 4.0, 8.0, 0.5]
        qm = [1.0, 1.0, 2.0, 4.0, 2.0, 0.0]
        sm = [3.0, 10.0, 10.0, 9.0, 6.0, 1.0]           # L1 10 seeks: L1..L4 full (>= 5), L5 not: Lf = 4
        p = LB3.st_parts(hm, qm, sm, 2, 2.0)
        w = 4.0 / 10.0
        self.assertEqual(p["Lf"], 4)
        self.assertAlmostEqual(p["w"], w)
        self.assertAlmostEqual(p["own"], 2.0)
        self.assertAlmostEqual(p["steal"], (1 - w) * 4.0)
        self.assertAlmostEqual(p["trap"], -w * 0.5 * (4.0 + 8.0))
        self.assertEqual(p["last"], 0.0)
        lo = LB3.st_parts(hm, qm, sm, 2, 0.5)            # a lowering: no trap
        self.assertAlmostEqual(lo["own"], -1.0)
        self.assertAlmostEqual(lo["steal"], -(1 - w) * 2.0)
        self.assertEqual(lo["trap"], 0.0)
        last = LB3.st_parts(hm, qm, sm, 4, 0.5)          # the last full level
        self.assertEqual((last["own"], last["steal"], last["trap"]), (0.0, 0.0, 0.0))
        self.assertAlmostEqual(last["last"], 8.0 * (2.0 - 1.0))
        self.assertAlmostEqual(LB3.st_parts(hm, qm, sm, 4, 2.0)["last"], -4.0)
        none = LB3.st_parts([0.0] * 6, [0.0] * 6, [0.0] * 6, 2, 2.0)   # no scans, no reads
        self.assertEqual([none[k] for k in ("own", "steal", "trap", "last", "w")], [0.0] * 5)

    def test_the_account_books_the_rate_over_the_hold_and_releases_it(self):
        b = LB3.LevelBill3(2, a4_form="st")
        B = [0.0, 100.0, 300.0, 900.0, 2700.0, 0.0]
        C = [0.0, 100.0, 300.0, 900.0, 2700.0, 8100.0]
        st_ = state([False, True, True, True, False, False], 4)
        hm = [0.0, 1.0, 2.0, 4.0, 8.0, 0.5]
        pre = (hm, [1.0, 1.0, 2.0, 4.0, 2.0, 0.0], [3.0, 10.0, 10.0, 9.0, 6.0, 1.0])
        b.start(B, C, 300.0)
        want_rate = sum(LB3.ST_A[k] * LB3.st_parts(*pre, 2, 2.0)[k] for k in LB3.ST_A)
        cs_prev, A4s, rates = 300.0, [], []
        for t in range(LB3.ST_HOLD + 5):
            cs = 600.0 if t >= 1 else 300.0
            r = b.add(B, C, cs_prev, B, C, cs, st_, st_, Z6, Z6, Z6, hm, hm, 64, pre)
            A4s.append(r["A4"])
            rates.append(r["st_rate"])
            self.assertEqual(r["a4"], "st")
            cs_prev = cs
        self.assertEqual(A4s[0], 0.0)
        self.assertAlmostEqual(rates[1], want_rate)
        self.assertEqual([x for i, x in enumerate(rates) if i != 1], [0.0] * (len(rates) - 1))
        self.assertAlmostEqual(A4s[1], want_rate * LB3.ST_B[LB3.ST_HOLD])           # booked at the decision
        for age in range(1, LB3.ST_HOLD):                                            # released as it arrives
            self.assertAlmostEqual(A4s[1 + age], want_rate * (LB3.ST_B[LB3.ST_HOLD] - LB3.ST_B[age + 1]))
            self.assertAlmostEqual(A4s[age] - A4s[1 + age], want_rate * (1 - math.exp(-age / LB3.ST_TAU0)))
        self.assertEqual(A4s[LB3.ST_HOLD:], [0.0] * 5)
        self.assertEqual(b.st_moves, [])
        with self.assertRaises(ValueError):
            LB3.LevelBill3(2, a4_form="nope")
        nopre = LB3.LevelBill3(2, a4_form="st")
        nopre.start(B, C, 300.0)
        with self.assertRaises(ValueError):             # the form needs the means before the turn
            nopre.add(B, C, 300.0, B, C, 600.0, st_, st_, Z6, Z6, Z6, hm, hm, 64)

    def test_default_has_no_steal_trap_keys(self):
        b = LB3.LevelBill3(2)
        B = [0.0, 100.0, 300.0, 900.0, 0.0, 0.0]
        C = [0.0, 100.0, 300.0, 900.0, 2700.0, 0.0]
        st_ = state([False, True, True, False, False, False], 3)
        b.start(B, C, 300.0)
        r = b.add(B, C, 300.0, B, C, 600.0, st_, st_, Z6, Z6, Z6, [1.0] * 6, [1.0] * 6, 64)
        self.assertFalse(any(k.startswith("st_") or k == "a4" for k in r))


def rung_of(t):
    """Line t's rung_m: L1 up at line 4, L2 down at line 7 and up at 12, L3 up twice (6, 9), down at 15."""
    m = [1.0] * NL
    if t >= 4:
        m[1] = RUNGS[3]
    if 7 <= t < 12:
        m[2] = RUNGS[1]
    if t >= 12:
        m[2] = RUNGS[4]
    if t >= 6:
        m[3] = RUNGS[3]
    if t >= 9:
        m[3] = RUNGS[4]
    if t >= 15:
        m[3] = RUNGS[2]
    return m


def applied_of(t):
    """Line t's applied_m (in force from the next turn): the rungs, a lowering glided over two lines."""
    m = rung_of(t + 1) if t + 1 < base.TURNS else rung_of(t)
    m = list(m)
    if t == 6:
        m[2] = 0.85
    if t == 14:
        m[3] = 1.7
    return m


def turn_line3(t, final=False):
    line = lb2t.turn_line(t, acting=ACT, final=final)
    line["decided"] = [1] * NL
    line["applied_m"] = applied_of(t)
    line["rung_m"] = rung_of(t)
    if not final:
        line["action"] = ["compact" if t % 3 == 0 else "hold", "expand" if t % 2 else "hold",
                          "compact" if t % 4 == 1 else "defer", "expand" if t % 5 else "compact"] + \
                         ["hold"] * (NL - ACT)
    return line


def plugin_config3(**changes):
    cfg = {"turn_periods": PERIODS}
    cfg.update(changes)
    return lb2t.plugin_config(**cfg)


def run3(tmp, cfg=None, lines=None, **changes):
    s = base.settings(slow_min_replay=2, slow_batch=4, **changes)
    tr = LB.LevelBillsTrainer(lb2t.args_in(tmp), s, cfg or plugin_config3())
    base.write(tr.args.host_log, dbt.host_log())
    recs = [{"type": "turn_start", "arch": "vdn", "acting": ACT, "features": TW.feature_names(ACT)}]
    recs += lines if lines is not None else [turn_line3(t) for t in range(base.TURNS)]
    recs.append({"type": "stop"})
    base.write(tr.args.decisions, recs)
    tr.step()
    assert tr.done()
    tr.finish("test")
    return tr, [json.loads(x) for x in Path(tr.args.log).read_text().splitlines()]


def merge_source_bytes(pricer, lo, hi):
    out = [0.0] * NL
    for r in pricer.recs[lo + 1:hi]:
        if r.get("type") == "job_end" and r.get("ok") in (True, 1) and not r.get("trivial"):
            out[r["start_level"]] += r["s"]
    return out


@unittest.skipIf(torch is None, "needs torch")
class EveryTurnTrainerTest(unittest.TestCase):
    def test_l0_is_paid_bill_n_exactly(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _, log = run3(a, level_bill3=1)
            want = {r["t"]: r for r in lb2t.run_bill_n(b) if r["type"] == "turn"}
            turns = [r for r in log if r["type"] == "turn"]
            self.assertEqual([r["t"] for r in turns], sorted(want))
            for r in turns:
                for key in ("bill_read", "bill_merge", "bill_deep", "bill_deep_levels", "bill_debt",
                            "bill_debt_levels", "debt", "tree", "debt_l3", "J_reward", "J"):
                    self.assertEqual(r[key], want[r["t"]][key], (r["t"], key))
                self.assertEqual(r["l0_bill"], "N")

    def recompute(self, out_floor=False, a4_form="copies", **new):
        cfg = plugin_config3()
        pricer = L2.HostLogPricer(from_plugin_config(cfg))
        for r in dbt.host_log():
            pricer.add(r)
        run = LB3.Bill3Run([1, 2, 3], out_floor=out_floor, a4_form=a4_form, **new)
        cs0 = {j: dbt.TARGETS[j] * rung_of(0)[j] for j in (1, 2, 3)}
        Cn = [float(v) for v in dbt.TARGETS]
        run.start(dbt.START, LB.targets(dbt.TARGETS, applied_of(0)), cs0, 3.0, Cn=Cn)
        out = {}
        for t in range(1, base.TURNS):
            b = pricer.bounds(t * base.TURN, (t + 1) * base.TURN)
            if b is None:
                break
            m = TT.merge_prices(pricer, *b)
            paid = [m.get(i, [0.0, 0.0])[0] for i in range(NL)]
            fed = [m.get(i, [0.0, 0.0])[1] for i in range(NL)]
            R, H = TL.read_cells(pricer.p, lb2t.reads(t))
            cs = {j: dbt.TARGETS[j] * rung_of(t)[j] for j in (1, 2, 3)}
            qs = {}
            if a4_form == "st":
                rd = lb2t.reads(t)
                qs = {"q": [float(v) for v in rd["filter_hits"]], "s": [float(v) for v in rd["seeks"]]}
            out[t] = run.add(t, dbt.sizes_after(t), LB.targets(dbt.TARGETS, applied_of(t - 1)), cs, paid,
                             lb2t.moved_all(pricer, *b), fed, merge_source_bytes(pricer, *b), R, H, Cn=Cn, **qs)[0]
        return out

    def test_every_level_is_paid_bill3r_and_learns_from_one_turn_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr, log = run3(tmp, level_bill3=1)
            want = self.recompute()
            lines = [r for r in log if r["type"] == "bill3"]
            self.assertEqual(sorted({r["t"] for r in lines}), sorted(want))
            self.assertEqual(sorted({r["level"] for r in lines}), [1, 2, 3])
            for r in lines:
                w = want[r["t"]][r["level"]]
                for key, value in w.items():
                    got = r[key]
                    if key == "tm" and not math.isfinite(value):
                        self.assertIsNone(got)
                    elif isinstance(value, list):
                        for a, b in zip(got, value):
                            self.assertAlmostEqual(a, b, delta=1e-9 * max(1.0, abs(b)), msg=key)
                    elif isinstance(value, float):
                        self.assertAlmostEqual(got, value, delta=1e-9 * max(1.0, abs(value)),
                                               msg=(r["t"], r["level"], key))
                    else:
                        self.assertEqual(got, value, (r["t"], r["level"], key))
            trees = {r["t"]: r for r in log if r["type"] == "bill3_tree"}
            for t, r in trees.items():
                self.assertEqual(r["B"], [max(0.0, v) for v in dbt.sizes_after(t)])
                self.assertEqual(r["C"], LB.targets(dbt.TARGETS, applied_of(t - 1)))
                self.assertEqual(r["cstar"], {str(j): dbt.TARGETS[j] * rung_of(t)[j] for j in (1, 2, 3)})
            by = {(r["t"], r["level"]): r for r in lines}
            # The decisions show where they should: L1's raise at turn 4, L2's lowering at 7, L3's raises.
            self.assertEqual(by[(4, 1)]["raised"], 1)
            self.assertNotEqual(by[(7, 2)]["dec1"], 0.0)
            self.assertEqual(by[(9, 3)]["raised"], 1)
            self.assertTrue(any(v != 0 for v in by[(7, 1)]["rb"]))             # L2 is L1's neighbour
            # One hold per level and turn, paid that turn's bill; the levels train.
            holds = [r for r in log if r["type"] == "hold"]
            for j in (1, 2, 3):
                hj = [h for h in holds if h["level"] == j]
                self.assertEqual([h["t1"] for h in hj], [h["t0"] + 1 for h in hj])
                self.assertEqual(len(hj), len(want) - 1)
                for h in hj:
                    self.assertAlmostEqual(h["bill"], by[(h["t1"], j)]["bill"],
                                           delta=1e-12 * max(1.0, abs(h["bill"])))
                    self.assertEqual(h["bill3"], "r")
                self.assertTrue(any(r["type"] == "train_level" and r["level"] == j for r in log))
            self.assertEqual(len(tr.holds[0]), 0)
            levels = next(r for r in log if r["type"] == "levels")
            self.assertEqual((levels["bill3"], levels["bill3_levels"]), ("r", [1, 2, 3]))
            # The identity per level over the run.
            for j in (1, 2, 3):
                rs = [by[(t, j)] for t in sorted(want)]
                lhs = sum(x["bill"] + x["rb_sum"] for x in rs)
                rhs = sum(x["R"] + x["M"] for x in rs) + rs[-1]["A"] - levels["bill3_A1_start"][str(j)]
                self.assertEqual(levels["bill3_A1_start"][str(j)], tr.b3.bills[j].A1_start)
                self.assertAlmostEqual(lhs, rhs, delta=1e-9 * max(1.0, sum(abs(x["bill"]) for x in rs)))

    def test_out_floor_lines_are_bill3r_with_the_floor(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr, log = run3(tmp, level_bill3=1, level_bill3_out_floor=1)
            want = self.recompute(out_floor=True)
            lines = [r for r in log if r["type"] == "bill3"]
            self.assertEqual(sorted({r["t"] for r in lines}), sorted(want))
            for r in lines:
                for key, value in want[r["t"]][r["level"]].items():
                    got = r[key]
                    if key == "tm" and not math.isfinite(value):
                        self.assertIsNone(got)
                    elif isinstance(value, list):
                        for a, b in zip(got, value):
                            self.assertAlmostEqual(a, b, delta=1e-9 * max(1.0, abs(b)), msg=key)
                    elif isinstance(value, float):
                        self.assertAlmostEqual(got, value, delta=1e-9 * max(1.0, abs(value)),
                                               msg=(r["t"], r["level"], key))
                    else:
                        self.assertEqual(got, value, (r["t"], r["level"], key))
                self.assertEqual(r["den"], max(r["out"], r["f"]))
            self.assertTrue(tr.b3.out_floor)
            levels = next(r for r in log if r["type"] == "levels")
            self.assertEqual(levels["bill3_out_floor"], 1)
            # The trainer with the floor 0 writes what it writes without the setting.
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            tr_a, _ = run3(a, level_bill3=1)
            tr_b, _ = run3(b, level_bill3=1, level_bill3_out_floor=0)
            self.assertEqual(Path(tr_a.args.log).read_bytes(), Path(tr_b.args.log).read_bytes())
            self.assertEqual(Path(tr_a.args.weights).read_bytes(), Path(tr_b.args.weights).read_bytes())
            self.assertFalse(tr_b.b3.out_floor)

    def test_steal_trap_lines_are_bill3r_with_that_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr, log = run3(tmp, level_bill3=1, level_bill3_a4="st")
            want = self.recompute(a4_form="st")
            lines = [r for r in log if r["type"] == "bill3"]
            self.assertEqual(sorted({r["t"] for r in lines}), sorted(want))
            for r in lines:
                for key, value in want[r["t"]][r["level"]].items():
                    got = r[key]
                    if key == "tm" and not math.isfinite(value):
                        self.assertIsNone(got)
                    elif isinstance(value, list):
                        for a, b in zip(got, value):
                            self.assertAlmostEqual(a, b, delta=1e-9 * max(1.0, abs(b)), msg=key)
                    elif isinstance(value, float):
                        self.assertAlmostEqual(got, value, delta=1e-9 * max(1.0, abs(value)),
                                               msg=(r["t"], r["level"], key))
                    else:
                        self.assertEqual(got, value, (r["t"], r["level"], key))
                self.assertEqual(r["a4"], "st")
            by = {(r["t"], r["level"]): r for r in lines}
            ts = sorted(want)
            # Every decided-size change books a rate; the account is the open moves' rest; the identity holds.
            for j in (1, 2, 3):
                moves = []
                for t in ts:
                    x = by[(t, j)]
                    if t - 1 in want and x["cstar"] != by[(t - 1, j)]["cstar"] if (t - 1, j) in by else False:
                        self.assertNotEqual(x["st_w"], None)
                        moves.append([x["st_rate"], 0])
                    A4 = sum(r_ * (LB3.ST_B[LB3.ST_HOLD] - LB3.ST_B[min(a_ + 1, LB3.ST_HOLD)]) for r_, a_ in moves)
                    moves = [[r_, a_ + 1] for r_, a_ in moves if a_ + 1 < LB3.ST_HOLD]
                    self.assertAlmostEqual(x["A4"], A4, delta=1e-9 * max(1.0, abs(A4)), msg=(t, j))
                rs = [by[(t, j)] for t in ts]
                self.assertTrue(any(x["st_rate"] != 0.0 for x in rs), j)
            levels = next(r for r in log if r["type"] == "levels")
            self.assertEqual(levels["bill3_a4"], "st")
            self.assertEqual(levels["bill3_st"]["hold"], LB3.ST_HOLD)
            for j in (1, 2, 3):
                rs = [by[(t, j)] for t in ts]
                lhs = sum(x["bill"] + x["rb_sum"] for x in rs)
                rhs = sum(x["R"] + x["M"] for x in rs) + rs[-1]["A"] - levels["bill3_A1_start"][str(j)]
                self.assertAlmostEqual(lhs, rhs, delta=1e-9 * max(1.0, sum(abs(x["bill"]) for x in rs)))
        # "copies" written out is the trainer without the setting, byte for byte.
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            tr_a, _ = run3(a, level_bill3=1)
            tr_b, _ = run3(b, level_bill3=1, level_bill3_a4="copies")
            self.assertEqual(Path(tr_a.args.log).read_bytes(), Path(tr_b.args.log).read_bytes())
            self.assertEqual(Path(tr_a.args.weights).read_bytes(), Path(tr_b.args.weights).read_bytes())

    def test_the_three_options_lines_are_bill3r_with_them(self):
        cfg0 = plugin_config3()
        price = float(cfg0["beta_r"]) * float(cfg0["c_f"])
        new = dict(owed="capped", a3=True, st_trap=0.0, probe_price=price)
        with tempfile.TemporaryDirectory() as tmp:
            tr, log = run3(tmp, level_bill3=1, level_bill3_a4="st", level_bill3_owed="capped", level_bill3_a3=1,
                           level_bill3_st_trap=0, level_bill3_probe=1)
            self.assertAlmostEqual(tr.b3.probe_price, price)
            want = self.recompute(a4_form="st", **new)
            lines = [r for r in log if r["type"] == "bill3"]
            self.assertEqual(sorted({r["t"] for r in lines}), sorted(want))
            for r in lines:
                for key, value in want[r["t"]][r["level"]].items():
                    got = r[key]
                    if key == "tm" and not math.isfinite(value):
                        self.assertIsNone(got)
                    elif isinstance(value, list):
                        for a, b in zip(got, value):
                            self.assertAlmostEqual(a, b, delta=1e-9 * max(1.0, abs(b)), msg=key)
                    elif isinstance(value, float):
                        self.assertAlmostEqual(got, value, delta=1e-9 * max(1.0, abs(value)),
                                               msg=(r["t"], r["level"], key))
                    else:
                        self.assertEqual(got, value, (r["t"], r["level"], key))
                self.assertEqual(r["owed"], "capped")
                self.assertLessEqual(r["cstar_led"], dbt.TARGETS[r["level"]])
                self.assertIn("dA3", r)
                self.assertIn("dA5", r)
            self.assertTrue(any(r["a3_rate"] != 0 for r in lines))
            tree = [r for r in log if r["type"] == "bill3_tree"]
            self.assertTrue(all(r["Cn"] == [float(v) for v in dbt.TARGETS] for r in tree))
            holds = [r for r in log if r["type"] == "hold" and r.get("bill3") == "r"]
            self.assertTrue(holds and all("dA3" in h and "dA5" in h for h in holds))
            by = {(r["t"], r["level"]): r for r in lines}
            for h in holds:                                   # a one-turn hold is paid its turn's bill
                self.assertAlmostEqual(h["bill"], by[(h["t1"], h["level"])]["bill"])
            levels = next(r for r in log if r["type"] == "levels")
            self.assertEqual((levels["bill3_owed"], levels["bill3_a3"], levels["bill3_st_trap"]), ("capped", 1, 0.0))
            self.assertEqual(levels["bill3_st"]["a"]["trap"], 0.0)       # the weights in use
            self.assertEqual(levels["bill3_st"]["a"]["own"], LB3.ST_A["own"])
            ts = sorted(want)
            for j in (1, 2, 3):
                rs = [by[(t, j)] for t in ts]
                lhs = sum(x["bill"] + x["rb_sum"] for x in rs)
                rhs = sum(x["R"] + x["M"] for x in rs) + rs[-1]["A"] - levels["bill3_A1_start"][str(j)]
                self.assertAlmostEqual(lhs, rhs, delta=1e-9 * max(1.0, sum(abs(x["bill"]) for x in rs)))
        # The options written out at their defaults are the trainer without them, byte for byte.
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            tr_a, _ = run3(a, level_bill3=1, level_bill3_a4="st")
            tr_b, _ = run3(b, level_bill3=1, level_bill3_a4="st", level_bill3_owed="decided", level_bill3_a3=0)
            self.assertEqual(Path(tr_a.args.log).read_bytes(), Path(tr_b.args.log).read_bytes())
            self.assertEqual(Path(tr_a.args.weights).read_bytes(), Path(tr_b.args.weights).read_bytes())

    def test_no_read_account_lines_are_bill3r_without_it(self):
        new = dict(owed="capped", a3=True)
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as tmp_st:
            tr, log = run3(tmp, level_bill3=1, level_bill3_a4="none", level_bill3_owed="capped", level_bill3_a3=1)
            want = self.recompute(a4_form="none", **new)
            lines = [r for r in log if r["type"] == "bill3"]
            self.assertEqual(sorted({r["t"] for r in lines}), sorted(want))
            for r in lines:
                for key, value in want[r["t"]][r["level"]].items():
                    got = r[key]
                    if key == "tm" and not math.isfinite(value):
                        self.assertIsNone(got)
                    elif isinstance(value, list):
                        for a, b in zip(got, value):
                            self.assertAlmostEqual(a, b, delta=1e-9 * max(1.0, abs(b)), msg=key)
                    elif isinstance(value, float):
                        self.assertAlmostEqual(got, value, delta=1e-9 * max(1.0, abs(value)),
                                               msg=(r["t"], r["level"], key))
                    else:
                        self.assertEqual(got, value, (r["t"], r["level"], key))
                self.assertEqual((r["a4"], r["dA4"], r["A4"]), ("none", 0.0, 0.0))
                self.assertFalse(any(k.startswith("st_") for k in r))
            holds = [r for r in log if r["type"] == "hold" and r.get("bill3") == "r"]
            by = {(r["t"], r["level"]): r for r in lines}
            self.assertTrue(holds)
            for h in holds:                                   # a one-turn hold is paid its turn's bill
                self.assertAlmostEqual(h["bill"], by[(h["t1"], h["level"])]["bill"])
            levels = next(r for r in log if r["type"] == "levels")
            self.assertEqual(levels["bill3_a4"], "none")
            self.assertNotIn("bill3_st", levels)
            self.assertTrue(all(n > 0 for n in levels["steps"][1:ACT]))      # the levels train
            # The same lines with the steal / trap account: only the read account's change differs.
            _, log_st = run3(tmp_st, level_bill3=1, level_bill3_a4="st", level_bill3_owed="capped",
                             level_bill3_a3=1, level_bill3_st_trap=0)
            st = {(r["t"], r["level"]): r for r in log_st if r["type"] == "bill3"}
            self.assertEqual(sorted(st), sorted(by))
            self.assertTrue(any(r["dA4"] != 0 for r in st.values()))
            for key, r in by.items():
                self.assertAlmostEqual(r["bill"], st[key]["bill"] - st[key]["dA4"],
                                       delta=1e-9 * max(1.0, abs(r["bill"])), msg=key)

    def test_a_slow_level_is_paid_bill3r_summed_over_its_hold(self):
        # 2026-11-21 (batch lb8): level_bill3 1 takes periods above 1. L1 decides every turn, L2 and L3 every 4.
        def line(t):
            ln = turn_line3(t)
            ln["decided"] = [1, 1] + [1 if t % 4 == 3 else 0] * 2 + [1] * (NL - ACT)
            return ln
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            tr, log = run3(a, cfg=plugin_config3(turn_periods="1:1:4:4"), level_bill3=1,
                           lines=[line(t) for t in range(base.TURNS)])
            _, every = run3(b, level_bill3=1)
            # The per-turn bill does not know the clock: the same lines as with every level at period 1.
            key = lambda r: (r["t"], r["level"])  # noqa: E731
            self.assertEqual(sorted((r for r in log if r["type"] == "bill3"), key=key),
                             sorted((r for r in every if r["type"] == "bill3"), key=key))
            by = {key(r): r for r in log if r["type"] == "bill3"}
            holds = [r for r in log if r["type"] == "hold"]
            for j in (2, 3):
                hj = [h for h in holds if h["level"] == j]
                self.assertTrue(hj)
                for h in hj:
                    self.assertEqual((h["t0"] % 4, h["t1"] - h["t0"]), (3, 4))
                    for part in ("bill", "R", "M", "dA1", "dA2", "dA4"):
                        want = sum(by[(t, j)][part] for t in range(h["t0"] + 1, h["t1"] + 1))
                        self.assertAlmostEqual(h[part], want, delta=1e-9 * max(1.0, abs(want)), msg=(j, part))
                self.assertEqual(len(tr.holds[j]), len(hj))
            h1 = [h for h in holds if h["level"] == 1]
            self.assertEqual([h["t1"] - h["t0"] for h in h1], [1] * len(h1))
            levels = next(r for r in log if r["type"] == "levels")
            self.assertEqual((levels["periods"], levels["bill3_levels"]), ([1, 1, 4, 4], [1, 2, 3]))
            self.assertTrue(any(r["type"] == "train_level" and r["level"] == 2 for r in log))

    def test_pay_plain_and_wide_pay_money_only_and_leave_the_lines_alone(self):
        # 2026-11-21 (batch lb9): level_bill3_pay picks what a hold is paid; Bill3r is still computed and logged.
        opts = dict(level_bill3=1, level_bill3_a4="none", level_bill3_owed="capped", level_bill3_a3=1)
        key = lambda r: (r["t"], r["level"])  # noqa: E731
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, \
                tempfile.TemporaryDirectory() as c, tempfile.TemporaryDirectory() as d:
            tr0, log0 = run3(a, **opts)
            tr_same, _ = run3(b, level_bill3_pay="bill", **opts)
            # "bill" is the old trainer byte for byte.
            self.assertEqual(Path(tr0.args.log).read_bytes(), Path(tr_same.args.log).read_bytes())
            self.assertEqual(Path(tr0.args.weights).read_bytes(), Path(tr_same.args.weights).read_bytes())
            lines0 = sorted((r for r in log0 if r["type"] == "bill3"), key=key)
            by = {key(r): r for r in lines0}
            self.assertTrue(any(r["dA1"] != 0 or r["dA3"] != 0 for r in lines0))      # the accounts do move here
            for pay, tmp in (("plain", c), ("wide", d)):
                tr, log = run3(tmp, level_bill3_pay=pay, **opts)
                # The per-turn lines and the tree lines do not know what is paid.
                self.assertEqual(sorted((r for r in log if r["type"] == "bill3"), key=key), lines0)
                self.assertEqual([r for r in log if r["type"] == "bill3_tree"],
                                 [r for r in log0 if r["type"] == "bill3_tree"])
                holds = [r for r in log if r["type"] == "hold"]
                self.assertEqual(sorted({h["level"] for h in holds}), [1, 2, 3])
                differs = 0
                for h in holds:
                    own = by[(h["t1"], h["level"])]
                    src = own if pay == "plain" else by[(h["t1"], 1)]
                    want = src["R"] + src["M"]
                    self.assertAlmostEqual(h["bill"], want, delta=1e-9 * max(1.0, abs(want)), msg=(pay, key(own)))
                    self.assertAlmostEqual(h["bill3r"], own["bill"], delta=1e-9 * max(1.0, abs(own["bill"])))
                    self.assertEqual((h["pay"], h["bill3"]), (pay, "r"))
                    for part in ("R", "M", "dA1", "dA2", "dA3", "dA4"):          # the hold's parts stay its own
                        self.assertAlmostEqual(h[part], own[part], delta=1e-9 * max(1.0, abs(own[part])))
                    differs += abs(h["bill"] - h["bill3r"]) > 1e-12
                self.assertGreater(differs, 0)
                # The reward is the paid amount: the trainer's stored holds carry it.
                for j in (1, 2, 3):
                    hj = [h for h in holds if h["level"] == j]
                    self.assertEqual([x.bill for x in tr.holds[j]], [h["bill"] for h in hj])
                levels = next(r for r in log if r["type"] == "levels")
                self.assertEqual(levels["bill3_pay"], pay)
                self.assertTrue(all(n > 0 for n in levels["steps"][1:ACT]))      # the levels train
                if pay == "wide":                                                 # every level is paid the same money
                    paid = {}
                    for h in holds:
                        paid.setdefault(h["t1"], set()).add(round(h["bill"], 15))
                    self.assertTrue(all(len(v) == 1 for v in paid.values()))
                    l2 = [h for h in holds if h["level"] == 2]
                    self.assertTrue(any(abs(h["bill"] - (h["R"] + h["M"])) > 1e-12 for h in l2))
            self.assertNotIn("bill3_pay", next(r for r in log0 if r["type"] == "levels"))
            self.assertFalse(any("pay" in r or "bill3r" in r for r in log0 if r["type"] == "hold"))

    def test_pay_plain_on_a_slow_level_is_summed_over_its_hold(self):
        def line(t):
            ln = turn_line3(t)
            ln["decided"] = [1, 1] + [1 if t % 4 == 3 else 0] * 2 + [1] * (NL - ACT)
            return ln
        for pay in ("plain", "wide"):
            with tempfile.TemporaryDirectory() as a:
                tr, log = run3(a, cfg=plugin_config3(turn_periods="1:1:4:4"), level_bill3=1, level_bill3_pay=pay,
                               lines=[line(t) for t in range(base.TURNS)])
                by = {(r["t"], r["level"]): r for r in log if r["type"] == "bill3"}
                hj = [h for h in log if h["type"] == "hold" and h["level"] in (2, 3)]
                self.assertTrue(hj)
                for h in hj:
                    self.assertEqual(h["t1"] - h["t0"], 4)
                    src = h["level"] if pay == "plain" else 1
                    want = sum(by[(t, src)]["R"] + by[(t, src)]["M"] for t in range(h["t0"] + 1, h["t1"] + 1))
                    self.assertAlmostEqual(h["bill"], want, delta=1e-9 * max(1.0, abs(want)), msg=(pay, h["level"]))

    def test_a_level_with_period_0_never_learns(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr, log = run3(tmp, cfg=plugin_config3(turn_periods="1:1:0:1"), level_bill3=1)
            self.assertEqual(sorted({r["level"] for r in log if r["type"] == "bill3"}), [1, 3])
            self.assertFalse(any(r["type"] == "hold" and r["level"] == 2 for r in log))
            self.assertEqual(tr.b3_levels, [1, 3])

    def test_off_or_zero_is_the_old_trainer_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            tr_a, _ = lbt.run(a)
            tr_b, _ = lbt.run(b, level_bill3=0)
            self.assertEqual(Path(tr_a.args.log).read_bytes(), Path(tr_b.args.log).read_bytes())
            self.assertEqual(Path(tr_a.args.weights).read_bytes(), Path(tr_b.args.weights).read_bytes())
            self.assertIsNone(tr_b.b3)
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            tr_a, _ = lb2t.run_clocked(a, level_bill2=1)
            tr_b, _ = lb2t.run_clocked(b, level_bill2=1, level_bill3=0)
            self.assertEqual(Path(tr_a.args.log).read_bytes(), Path(tr_b.args.log).read_bytes())
            self.assertEqual(Path(tr_a.args.weights).read_bytes(), Path(tr_b.args.weights).read_bytes())
            log = [json.loads(x) for x in Path(tr_b.args.log).read_text().splitlines()]
            self.assertFalse(any(r["type"] in ("bill3", "bill3_tree") for r in log))

    def test_the_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            for changes in ({"level_bill3": 2}, {"level_bill3": True},
                            {"level_bill3": 1, "level_bill2": 1},
                            {"level_bill3": 1, "level_bill3_out_floor": 2},
                            {"level_bill3": 1, "level_bill3_out_floor": True},
                            {"level_bill3_out_floor": 1}, {"level_bill3": 0, "level_bill3_out_floor": 1},
                            {"level_bill3": 1, "level_bill3_a4": "nope"}, {"level_bill3": 1, "level_bill3_a4": 1},
                            {"level_bill3_a4": "st"}, {"level_bill3": 0, "level_bill3_a4": "st"},
                            {"level_bill3": 1, "level_bill3_a4": "st", "level_bill3_out_floor": 1},
                            {"level_bill3_a4": "none"}, {"level_bill3": 0, "level_bill3_a4": "none"},
                            {"level_bill3": 1, "level_bill3_a4": "none", "level_bill3_out_floor": 1},
                            {"level_bill3": 1, "level_bill3_a4": "none", "level_bill3_st_trap": 0},
                            {"level_bill3": 1, "level_bill3_a4": "none", "level_bill3_probe": 1},
                            {"level_bill3": 1, "level_bill3_owed": "nope"}, {"level_bill3_owed": "capped"},
                            {"level_bill3": 1, "level_bill3_a3": 2}, {"level_bill3": 1, "level_bill3_a3": True},
                            {"level_bill3_a3": 1}, {"level_bill3": 0, "level_bill3_st_trap": 0},
                            {"level_bill3": 1, "level_bill3_probe": 1}, {"level_bill3_probe": 1},
                            {"level_bill3": 1, "level_bill3_a4": "st", "level_bill3_probe": 2},
                            {"level_bill3": 1, "level_bill3_st_trap": 0},
                            {"level_bill3": 1, "level_bill3_a4": "st", "level_bill3_st_trap": -1},
                            {"level_bill3": 1, "level_bill3_a4": "st", "level_bill3_st_trap": True},
                            {"level_bill3": 1, "level_bill3_pay": "nope"}, {"level_bill3": 1, "level_bill3_pay": 1},
                            {"level_bill3_pay": "plain"}, {"level_bill3": 0, "level_bill3_pay": "wide"}):
                with self.assertRaises(ValueError, msg=changes):
                    LB.LevelBillsTrainer(lb2t.args_in(tmp), base.settings(**changes), plugin_config3())
            # No level that decides (a period above 1 is allowed since 2026-11-21, batch lb8).
            for periods in ("1:0:0:0",):
                with self.assertRaises(ValueError, msg=periods):
                    LB.LevelBillsTrainer(lb2t.args_in(tmp), base.settings(level_bill3=1),
                                         plugin_config3(turn_periods=periods))
            with self.assertRaises(ValueError):
                LB.LevelBillsTrainer(lb2t.args_in(tmp), base.settings(level_bill3=1),
                                     plugin_config3(turn_l0_bill=1))
            # "wide" pays L1's money: L1 must learn.
            with self.assertRaises(ValueError):
                LB.LevelBillsTrainer(lb2t.args_in(tmp), base.settings(level_bill3=1, level_bill3_pay="wide"),
                                     plugin_config3(turn_periods="1:0:1:1"))
            LB.LevelBillsTrainer(lb2t.args_in(tmp), base.settings(level_bill3=1, level_bill3_pay="plain"),
                                 plugin_config3(turn_periods="1:0:1:1")).log.close()
            # A line without rung_m stops the trainer.
            tr = LB.LevelBillsTrainer(lb2t.args_in(tmp), base.settings(level_bill3=1), plugin_config3())
            base.write(tr.args.host_log, dbt.host_log())
            first = turn_line3(0)
            del first["rung_m"]
            base.write(tr.args.decisions, [first, turn_line3(1)])
            with self.assertRaises(SystemExit):
                tr.step()
            tr.log.close()


if __name__ == "__main__":
    unittest.main()
