"""Design 2 of the level bill (learner/level_bill2.py; level_bills.py with the
trainer settings' level_bill2 1). By hand: the three ledgers and their rule,
the re-base at a move of a target (booked outside the flow, at the price of
the day at the boundary), the booked start, the size-model price and the
prices of the day, the fill charge with its frozen chain price, the "full"
rule over the last 16 turns and the last level left out, the variants'
switches. On a synthetic clocked run: L0's bill is turn_trainer's bill N to
the last bit, each slow level's bill2 lines are design 2 recomputed here from
the host log, the hand-rebuilt sizes and the lines' applied multipliers, each
hold is paid its turns' bills, the switches pick their parts, the option off
(or 0) leaves the log byte for byte as before, and the refusals."""

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

from learner import level_bill2 as LB2  # noqa: E402

if torch is not None:
    from learner import level_bills as LB  # noqa: E402
    from learner import turn_trainer as TT  # noqa: E402
    from learner import learned2 as L2  # noqa: E402
    from learner import turn_ledger as TL  # noqa: E402
    from learner import turn_weights as TW  # noqa: E402
    from learner.prices import from_plugin_config  # noqa: E402
    from tests import test_debt_bill as dbt  # noqa: E402
    from tests import test_level_bills as lbt  # noqa: E402
    from tests import test_turn_trainer as base  # noqa: E402

C5 = [0.0, 100.0, 300.0, 900.0, 2700.0, 8100.0]


def bill(j=2):
    return LB2.LevelBill2(j)


class OwedTest(unittest.TestCase):
    def test_the_three_ledgers(self):
        B = [0, 150, 380, 950, 1000, 0]
        # E_1 = 50; E_2 = 380 + 50 - 300 = 130; E_3 = 950 + 130 - 900 = 180 (L4 holds bytes)
        self.assertEqual(LB2.owed2(B, C5, 2), [50.0, 130.0, 180.0])
        # Nothing below L_(j+1): E_(j+1) = 0; E_(j-1) and E_j have no such condition.
        self.assertEqual(LB2.owed2([0, 150, 380, 950, 0, 0], C5, 2), [50.0, 130.0, 0.0])
        # Sizes below zero count as empty; levels past the lists hold nothing and owe nothing.
        self.assertEqual(LB2.owed2([0, -5, 380, 950, 1000], C5, 2), [0.0, 80.0, 130.0])
        self.assertEqual(LB2.owed2([0, 0, 0, 0, 3000], C5[:5], 4), [0.0, 300.0, 0.0])
        self.assertEqual(LB2.last_level([5, 0, 3, 0]), 2)
        self.assertEqual(LB2.last_level([0, 0]), 0)


class PricesTest(unittest.TestCase):
    def test_own_price_size_model_and_w(self):
        pr = LB2.DayPrices(4, 5.0)
        self.assertEqual(pr.vector([0, 100, 300, 0]), [0.0] * 4)      # no w yet: 0
        pr.add([0, 10, 0, 0], [0, 2e6, 0, 0], [0, 4e6, 0, 0])
        w = 10 / 4e6
        self.assertAlmostEqual(pr.w, w)
        self.assertAlmostEqual(pr.own[1], 10 / 2e6)
        self.assertIsNone(pr.own[2])
        p = pr.vector([0, 100, 300, 0])
        self.assertAlmostEqual(p[0], w * (1 + 10))        # empty level: ratio 2T
        self.assertAlmostEqual(p[1], 5e-6)                # its own
        self.assertAlmostEqual(p[2], w * (1 + 0))         # nothing below it
        self.assertAlmostEqual(p[3], w)                   # the last level: w
        self.assertAlmostEqual(pr.vector([0, 100, 3000, 900])[2], w * (1 + 0.3))
        self.assertAlmostEqual(pr.vector([0, 100, 30, 900])[2], w * (1 + 10))   # capped at 2T
        # A level that moved nothing for 64 turns (half-life) loses its own price; w keeps its last value.
        for _ in range(64):
            pr.add([0] * 4, [0] * 4, [0] * 4)
        self.assertIsNone(pr.own[1])
        for _ in range(100):
            pr.add([0] * 4, [0] * 4, [0] * 4)
        self.assertLess(pr.Fw, 1e6)
        self.assertAlmostEqual(pr.w, w)
        self.assertAlmostEqual(pr.vector([0, 100, 300, 0])[1], w * (1 + 3))
        # Moves without rewriting count as moved bytes (lower the own price), not as fed bytes.
        pr2 = LB2.DayPrices(3, 2.0)
        pr2.add([0, 0, 6], [0, 0, 3e6], [0, 0, 1.5e6])
        self.assertAlmostEqual(pr2.own[2], 2e-6)
        self.assertAlmostEqual(pr2.w, 4e-6)
        with self.assertRaises(ValueError):
            pr2.add([0, math.nan, 0], [0] * 3, [0] * 3)
        with self.assertRaises(ValueError):
            LB2.DayPrices(3, 0)


class LedgerTest(unittest.TestCase):
    P = [1.0, 1.0, 2.0, 3.0, 4.0, 5.0]

    def test_a_lowering_books_its_push_down_outside_the_flow_then_releases_it(self):
        b = bill()
        start = [0, 100, 300, 900, 1000, 0]
        b.start(start, C5)
        r1 = b.add(1, start, C5, start, C5, None, self.P, [0] * 6, [0] * 6, [0] * 6, [])
        self.assertEqual((r1["dD"], r1["rebase"], b.D_start), (0.0, [0.0] * 3, 0.0))
        low = list(C5)
        low[2] = 150.0
        # The move: E = [0, 150, 150] on the boundary's sizes, booked at the boundary's price (P), no flow.
        # The turn: 50 bytes pushed from L2 to L3, E = [0, 100, 150]: E_2's ledger releases a third.
        end = [0, 100, 250, 950, 1000, 0]
        later = [10.0] * 6                           # the price after the turn does not book a release
        r2 = b.add(2, start, C5, end, low, self.P, later, [0] * 6, [0] * 6, [0, 0, 40, 0, 0, 0], [])
        self.assertEqual(r2["rebase"], [0.0, 300.0, 450.0])
        self.assertEqual(r2["E"], [0.0, 100.0, 150.0])
        self.assertAlmostEqual(r2["dD"], -100.0)
        self.assertEqual(r2["dD_levels"], [0.0, -100.0, 0.0])
        self.assertEqual(r2["D"], [0.0, 200.0, 450.0])
        self.assertEqual(r2["M"], 40.0)
        self.assertEqual(r2["raised"], 0)
        self.assertEqual(r2["fill"], 0)
        # Growth books at the turn's own price.
        r3 = b.add(3, end, low, [0, 100, 260, 950, 1000, 0], low, later, later, [0] * 6, [0] * 6, [0] * 6, [])
        self.assertEqual(r3["rebase"], [0.0] * 3)
        self.assertAlmostEqual(r3["dD_levels"][1], 10.0 * 10)
        self.assertAlmostEqual(r3["dD_levels"][2], 10.0 * 10)

    def test_a_raise_releases_without_credit_and_a_neighbours_move_rebases_too(self):
        b = bill()
        over = [0, 100, 400, 900, 1000, 0]
        b.start(over, C5)
        r1 = b.add(1, over, C5, over, C5, None, self.P, [0] * 6, [0] * 6, [0] * 6, [])
        # The start is booked at the first turn's prices, outside the flow: E = [0, 100, 100].
        self.assertEqual(b.D_start, 2 * 100 + 3 * 100)
        self.assertEqual(r1["dD"], 0.0)
        high = list(C5)
        high[2] = 600.0
        r2 = b.add(2, over, C5, over, high, self.P, self.P, [0] * 6, [0] * 6, [0] * 6, [])
        self.assertEqual(r2["rebase"], [0.0, -200.0, -300.0])
        self.assertEqual((r2["dD"], r2["D"]), (0.0, [0.0, 0.0, 0.0]))
        self.assertEqual((r2["raised"], r2["fill"]), (1, 1))
        # L1's target (the level above) moves: L_j's ledgers re-base, no fill (not L_j's own raise).
        b2 = bill()
        b2.start(over, C5)
        b2.add(1, over, C5, over, C5, None, self.P, [0] * 6, [0] * 6, [0] * 6, [])
        c1 = list(C5)
        c1[1] = 50.0
        r = b2.add(2, over, C5, over, c1, self.P, self.P, [0] * 6, [0] * 6, [0] * 6, [])
        self.assertEqual(r["rebase"], [50.0, 100.0, 150.0])
        self.assertEqual((r["dD"], r["raised"], r["fill"]), (0.0, 0, 0))
        # A move of a level two below L_j does not touch its ledgers.
        b3 = bill()
        b3.start(over, C5)
        b3.add(1, over, C5, over, C5, None, self.P, [0] * 6, [0] * 6, [0] * 6, [])
        c4 = list(C5)
        c4[4] = 5400.0
        self.assertEqual(b3.add(2, over, C5, over, c4, self.P, self.P, [0] * 6, [0] * 6, [0] * 6, [])["rebase"],
                         [0.0] * 3)


class FillTest(unittest.TestCase):
    Q = [1.0, 1.0, 2.0, 3.0, 4.0, 5.0]

    def looks(self, *pairs):
        return [(list(b), list(c)) for b, c in pairs]

    def test_frozen_chain_through_full_levels_only(self):
        b = bill()
        B0 = [0, 100, 300, 900, 1000, 500]
        b.start(B0, C5)
        b.add(1, B0, C5, B0, C5, None, self.Q, [0] * 6, [0] * 6, [0] * 6, [(B0, C5)])
        high = list(C5)
        high[2] = 600.0
        full3 = [(B0, C5)]                               # L3 at its target, L4 has room
        B2 = [0, 100, 350, 900, 1000, 500]
        r = b.add(2, B0, C5, B2, high, self.Q, [9.0] * 6, [0] * 6, [0] * 6, [0] * 6, full3)
        self.assertEqual((r["raised"], r["fill"], r["absorbed"], r["chain"]), (1, 1, 50.0, 5.0))
        self.assertEqual(r["F"], 250.0)
        # Later prices do not move the frozen chain; a dip below the highest value absorbs nothing.
        B3 = [0, 100, 340, 900, 1000, 500]
        r = b.add(3, B2, high, B3, high, [9.0] * 6, [50.0] * 6, [0] * 6, [0] * 6, [0] * 6, full3)
        self.assertEqual((r["absorbed"], r["F"], r["chain"]), (0.0, 0.0, 5.0))
        # L4 reached its target in one of the looks: the chain goes on through it (L5 is the last level).
        B4 = [0, 100, 500, 900, 1000, 500]
        full34 = full3 + [([0, 100, 300, 900, 2700, 500], C5)]
        r = b.add(4, B3, high, B4, high, [50.0] * 6, [50.0] * 6, [0] * 6, [0] * 6, [0] * 6, full34)
        self.assertEqual((r["absorbed"], r["chain"], r["F"]), (150.0, 9.0, 1350.0))
        # The level reaches its target: capped at C_j, then the charge is off.
        B5 = [0, 100, 700, 900, 1000, 500]
        r = b.add(5, B4, high, B5, high, [50.0] * 6, [50.0] * 6, [0] * 6, [0] * 6, [0] * 6, full34)
        self.assertEqual((r["absorbed"], r["F"]), (100.0, 900.0))
        r = b.add(6, B5, high, [0, 100, 650, 900, 1000, 500], high, [50.0] * 6, [50.0] * 6,
                  [0] * 6, [0] * 6, [0] * 6, full34)
        self.assertEqual((r["fill"], r["F"]), (0, 0.0))

    def test_the_chain_never_takes_the_last_level_and_stops_at_room(self):
        b = bill()
        B0 = [0, 100, 300, 900, 2700, 0]                 # L4 is the last level that holds bytes
        b.start(B0, C5)
        b.add(1, B0, C5, B0, C5, None, self.Q, [0] * 6, [0] * 6, [0] * 6, [(B0, C5)])
        high = list(C5)
        high[2] = 600.0
        r = b.add(2, B0, C5, [0, 100, 310, 900, 2700, 0], high, self.Q, self.Q, [0] * 6, [0] * 6,
                  [0] * 6, [(B0, C5)])
        self.assertEqual((r["chain"], r["F"]), (2.0 + 3.0, 50.0))
        # No level below L_j full: the chain is q_j alone.
        b = bill()
        B0 = [0, 100, 300, 800, 1000, 500]
        b.start(B0, C5)
        b.add(1, B0, C5, B0, C5, None, self.Q, [0] * 6, [0] * 6, [0] * 6, [(B0, C5)])
        r = b.add(2, B0, C5, [0, 100, 310, 800, 1000, 500], high, self.Q, self.Q, [0] * 6, [0] * 6,
                  [0] * 6, [(B0, C5)])
        self.assertEqual((r["chain"], r["F"]), (2.0, 20.0))
        # A raise when the level already holds the new target: no fill.
        b = bill()
        B0 = [0, 100, 700, 900, 1000, 500]
        b.start(B0, C5)
        b.add(1, B0, C5, B0, C5, None, self.Q, [0] * 6, [0] * 6, [0] * 6, [(B0, C5)])
        r = b.add(2, B0, C5, B0, high, self.Q, self.Q, [0] * 6, [0] * 6, [0] * 6, [(B0, C5)])
        self.assertEqual((r["raised"], r["fill"], r["F"]), (1, 0, 0.0))

    def test_a_second_raise_freezes_again_and_restarts_from_the_boundary(self):
        b = bill()
        B0 = [0, 100, 300, 800, 1000, 500]
        b.start(B0, C5)
        b.add(1, B0, C5, B0, C5, None, self.Q, [0] * 6, [0] * 6, [0] * 6, [])
        c1 = list(C5)
        c1[2] = 424.0
        B2 = [0, 100, 400, 800, 1000, 500]
        b.add(2, B0, C5, B2, c1, self.Q, self.Q, [0] * 6, [0] * 6, [0] * 6, [])
        c2 = list(c1)
        c2[2] = 600.0
        Q2 = [7.0] * 6
        B3 = [0, 100, 450, 800, 1000, 500]
        r = b.add(3, B2, c1, B3, c2, Q2, self.Q, [0] * 6, [0] * 6, [0] * 6, [])
        self.assertEqual((r["raised"], r["chain"], r["absorbed"], r["F"]), (1, 7.0, 50.0, 350.0))
        self.assertEqual(b.raises, 2)


class FullWindowTest(unittest.TestCase):
    def test_full_means_at_target_once_in_the_last_16_turns_with_the_start_while_fewer(self):
        run = LB2.Bill2Run([2])
        C = [0, 100, 300, 900, 2700, 8100]
        start = [0, 100, 300, 900, 1000, 500]            # L3 full at the start only
        run.start(start, C, 3.0)
        zero = [0.0] * 6
        roomy = [0, 100, 300, 800, 1000, 500]
        for t in range(1, 5):
            run.add(t, roomy, C, [0, 1e3, 0, 0, 0, 0], [0, 2e6, 0, 0, 0, 0], [0, 4e6, 0, 0, 0, 0], zero, zero)
        high = list(C)
        high[2] = 600.0
        out = run.add(5, [0, 100, 310, 800, 1000, 500], high, zero, zero, zero, zero, zero)[2]
        p = run.bills[2].q
        self.assertAlmostEqual(out["chain"], p[2] + p[3])     # the start counts while fewer than 16 turns
        for t in range(6, 17):
            out = run.add(t, [0, 100, 310, 800, 1000, 500], high, zero, zero, zero, zero, zero)[2]
        self.assertEqual(len(run.looks), 16)
        self.assertAlmostEqual(out["chain"], p[2])             # 16 turns seen, none full: the start is out
        out = run.add(17, [0, 100, 320, 900, 1000, 500], high, zero, zero, zero, zero, zero)[2]
        self.assertAlmostEqual(out["chain"], p[2] + p[3])
        for t in range(18, 33):
            out = run.add(t, [0, 100, 330, 800, 1000, 500], high, zero, zero, zero, zero, zero)[2]
        self.assertAlmostEqual(out["chain"], p[2] + p[3])      # turn 17 is still among the last 16
        out = run.add(33, [0, 100, 340, 800, 1000, 500], high, zero, zero, zero, zero, zero)[2]
        self.assertAlmostEqual(out["chain"], p[2])


class SwitchTest(unittest.TestCase):
    PARTS = {"R": 1.0, "Rg": 1.5, "M": 10.0, "dD": -3.0, "F": 4.0}

    def test_the_variants_pick_their_parts(self):
        self.assertEqual(LB2.bill_of(self.PARTS, "d"), 11.0)
        self.assertEqual(LB2.bill_of(self.PARTS, "e"), 8.0)
        self.assertEqual(LB2.bill_of(self.PARTS, "f"), 12.0)
        self.assertEqual(LB2.bill_of(self.PARTS, "g"), 12.5)
        with self.assertRaises(ValueError):
            LB2.bill_of(self.PARTS, "x")
        with self.assertRaises(ValueError):
            LB2.Bill2Run([2], "h")
        with self.assertRaises(ValueError):
            LB2.LevelBill2(1)
        self.assertEqual(LB2.DEFAULT_VARIANT, "f")

    def test_reads_from_the_level_above_down_and_at_target(self):
        for v in LB2.VARIANTS:
            run = LB2.Bill2Run([3], v)
            C = [0, 100, 300, 900, 2700, 8100]
            B = [0, 100, 300, 450, 1000, 500]
            run.start(B, C, 3.0)
            R = [1, 2, 4, 8, 16, 32]
            H = [0, 1, 0, 1, 0, 1]
            out = run.add(1, B, C, [5, 6, 7, 8, 9, 0], [0] * 6, [0] * 6, R, H)[3]
            self.assertEqual(out["R"], 4 + 9 + 16 + 33)                 # levels 2..5
            self.assertEqual(out["Rj"], 9)
            self.assertEqual(out["Rg"], out["R"] + (2.0 - 1.0) * 9)     # C_3 / B_3 = 2
            self.assertEqual(out["M"], 7 + 8 + 9)                      # merges out of L2..L4
            self.assertEqual(out["bill"], LB2.bill_of(out, v))
            B2 = [0, 100, 300, 9000, 1000, 500]
            out = run.add(2, B2, C, [0] * 6, [0] * 6, [0] * 6, R, H)[3]
            self.assertEqual(out["Rg"], out["R"] + (0.5 - 1.0) * 9)    # clamped at 0.5
            out = run.add(3, [0, 100, 300, 0, 1000, 500], C, [0] * 6, [0] * 6, [0] * 6, R, H)[3]
            self.assertEqual(out["Rg"], out["R"] + 9)                  # empty: 2


# --------------------------------------------------------------- the trainer
ACT = 4
NL = 7
PERIODS = "1:0:2:4"


def m_of(t):
    """Line t's applied_m: L2 raised to x2 at line 5, L3 halved at line 9, L2 down to x0.5 at line 13."""
    m = [1.0] * NL
    if t >= 5:
        m[2] = 2.0
    if t >= 9:
        m[3] = 0.5
    if t >= 13:
        m[2] = 0.5
    return m


def decided(t):
    return [1, 0, int(t % 2 == 1), int(t % 4 == 3)] + [1] * (NL - ACT)


def reads(t):
    return {k: [(i + 1) * (t + 2) * (n + 1) for n in range(NL)] for i, k in enumerate(lbt.READ_KEYS)}


def turn_line(t, acting=ACT, final=False):
    names = TW.feature_names(acting)
    line = {"type": "turn", "t": t, "op0": t * base.TURN, "op1": (t + 1) * base.TURN,
            "final": int(final), "x": {n: 0.01 * ((i * 7 + t) % 13) for i, n in enumerate(names)},
            "masks": [[1, 1, 0, 1]] * NL, "owed": [0.0] * NL, "mid_applies": 0,
            "inflow": [0] * NL, "level_reads": reads(t), "B": dbt.START if t == 0 else dbt.in_flight(t),
            "C": dbt.TARGETS, "applied_m": m_of(t)}
    if acting > 1:
        line["decided"] = decided(t)
    if not final:
        line.update({"action": ["compact" if t % 3 == 0 else "hold", "hold",
                                "expand" if t % 4 == 1 else "compact", "expand"] + ["hold"] * (NL - ACT),
                     "set": 0, "explored": -1, "eps": 0.1})
    return line


def plugin_config(acting=ACT, **changes):
    cfg = base.plugin_config("vdn")
    cfg.update({"turn_acting": acting, "turn_decay": 0, "level_reads": 1, "beta_w": 10.0})
    if acting > 1:
        cfg.update({"turn_periods": PERIODS, "turn_slow_explore_decisions": 4})
    cfg.update(changes)
    return cfg


def args_in(tmp):
    args = type("A", (), {})()
    for name in ("decisions", "host_log", "log", "summary", "weights"):
        setattr(args, name, str(Path(tmp) / f"{name}.out"))
    return args


def run_clocked(tmp, cfg=None, **changes):
    tr = LB.LevelBillsTrainer(args_in(tmp), base.settings(slow_min_replay=2, slow_batch=4, **changes),
                              cfg or plugin_config())
    base.write(tr.args.host_log, dbt.host_log())
    lines = [{"type": "turn_start", "arch": "vdn", "acting": ACT, "features": TW.feature_names(ACT)}]
    lines += [turn_line(t) for t in range(base.TURNS)] + [{"type": "stop"}]
    base.write(tr.args.decisions, lines)
    tr.step()
    assert tr.done()
    tr.finish("test")
    return tr, [json.loads(x) for x in Path(tr.args.log).read_text().splitlines()]


def run_bill_n(tmp):
    """turn_trainer's bill N on the same logs: L0 alone, turn_l0_bill 1, l0_bill_z 1, l0_bill_z_l3 0,
    each line's l0_read = its level_reads block's L0 counts."""
    cfg = plugin_config(acting=1, turn_l0_bill=1)
    tr = TT.TurnTrainer(args_in(tmp), base.settings(l0_bill_z=1, l0_bill_z_l3=0), cfg)
    base.write(tr.args.host_log, dbt.host_log())
    lines = [{"type": "turn_start", "arch": "vdn", "acting": 1, "features": TW.feature_names(1)}]
    for t in range(base.TURNS):
        line = turn_line(t, acting=1)
        line["l0_read"] = TL.l0_counts(line["level_reads"])
        lines.append(line)
    lines.append({"type": "stop"})
    base.write(tr.args.decisions, lines)
    tr.step()
    tr.finish("test")
    return [json.loads(x) for x in Path(tr.args.log).read_text().splitlines()]


def moved_all(pricer, lo, hi):
    out = [0.0] * NL
    for r in pricer.recs[lo + 1:hi]:
        if r.get("type") == "job_end" and r.get("ok") in (True, 1) \
                and r.get("output_level", r["start_level"] + 1) > r["start_level"]:
            out[r["start_level"]] += r["s"]
    return out


@unittest.skipIf(torch is None, "needs torch")
class ClockedTrainerTest(unittest.TestCase):
    def test_l0_is_paid_bill_n_exactly(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _, log = run_clocked(a, level_bill2=1)
            want = {r["t"]: r for r in run_bill_n(b) if r["type"] == "turn"}
            turns = [r for r in log if r["type"] == "turn"]
            self.assertEqual([r["t"] for r in turns], sorted(want))
            for r in turns:
                w = want[r["t"]]
                for key in ("bill_read", "bill_merge", "bill_deep", "bill_deep_levels", "bill_debt",
                            "bill_debt_levels", "debt", "tree", "debt_l3", "J_reward", "J"):
                    self.assertEqual(r[key], w[key], (r["t"], key))
                self.assertEqual(r["l0_bill"], "N")
                self.assertNotIn("l0_bill_read", r)
            self.assertTrue(any(r["bill_debt"] != 0 for r in turns))

    def recompute(self, variant):
        """Design 2 per priced turn, from the host log, the sizes rebuilt by hand (test_debt_bill's
        sizes_after) and the targets the lines' applied_m put in force."""
        cfg = plugin_config()
        pricer = L2.HostLogPricer(from_plugin_config(cfg))
        for r in dbt.host_log():
            pricer.add(r)
        run = LB2.Bill2Run([2, 3], variant)
        run.start(dbt.START, LB.targets(dbt.TARGETS, m_of(0)), 3.0)
        out = {}
        for t in range(1, base.TURNS):
            b = pricer.bounds(t * base.TURN, (t + 1) * base.TURN)
            if b is None:
                break
            m = TT.merge_prices(pricer, *b)
            paid = [m.get(i, [0.0, 0.0])[0] for i in range(NL)]
            fed = [m.get(i, [0.0, 0.0])[1] for i in range(NL)]
            R, H = TL.read_cells(pricer.p, reads(t))
            out[t] = run.add(t, dbt.sizes_after(t), LB.targets(dbt.TARGETS, m_of(t - 1)), paid,
                             moved_all(pricer, *b), fed, R, H)
        return out

    def test_the_slow_levels_bills_are_design_2_and_holds_are_paid_their_turns(self):
        for variant in LB2.VARIANTS:
            with tempfile.TemporaryDirectory() as tmp:
                tr, log = run_clocked(tmp, level_bill2=1, level_bill2_variant=variant)
                want = self.recompute(variant)
                lines = [r for r in log if r["type"] == "bill2"]
                self.assertEqual(sorted({r["t"] for r in lines}), sorted(want))
                for r in lines:
                    w = want[r["t"]][r["level"]]
                    for key, value in w.items():
                        if isinstance(value, list):
                            for a, b in zip(r[key], value):
                                self.assertAlmostEqual(a, b, delta=1e-9 * max(1.0, abs(b)), msg=(variant, key))
                        elif isinstance(value, float):
                            self.assertAlmostEqual(r[key], value, delta=1e-9 * max(1.0, abs(value)),
                                                   msg=(variant, r["t"], r["level"], key))
                        else:
                            self.assertEqual(r[key], value, (variant, key))
                    self.assertEqual(r["variant"], variant)
                trees = {r["t"]: r for r in log if r["type"] == "bill2_tree"}
                for t, r in trees.items():
                    self.assertEqual(r["B"], [max(0.0, v) for v in dbt.sizes_after(t)])
                    self.assertEqual(r["C"], LB.targets(dbt.TARGETS, m_of(t - 1)))
                # The moves happened: L2's raise (fill), L3's halving (re-base) and L2's lowering.
                by = {(r["t"], r["level"]): r for r in lines}
                self.assertEqual(by[(6, 2)]["raised"], 1)
                self.assertGreater(by[(6, 2)]["fill"], 0)
                self.assertTrue(any(v != 0 for v in by[(10, 3)]["rebase"]))
                self.assertTrue(any(v != 0 for v in by[(10, 2)]["rebase"]))      # L3 is L2's L_(j+1)
                self.assertTrue(any(v != 0 for v in by[(14, 2)]["rebase"]))
                holds = [r for r in log if r["type"] == "hold"]
                self.assertEqual(sorted({r["level"] for r in holds}), [2, 3])
                for h in holds:
                    span = range(h["t0"] + 1, h["t1"] + 1)
                    self.assertAlmostEqual(h["bill"], sum(by[(t, h["level"])]["bill"] for t in span),
                                           delta=1e-9 * max(1.0, abs(h["bill"])))
                    for key in ("R", "M", "dD", "F"):
                        self.assertAlmostEqual(h[key], sum(by[(t, h["level"])][key] for t in span),
                                               delta=1e-9 * max(1.0, abs(h[key])))
                    self.assertEqual(h["bill2"], variant)
                self.assertEqual([len(x) for x in tr.holds], [0, 0, len([h for h in holds if h["level"] == 2]),
                                                              len([h for h in holds if h["level"] == 3])])
                levels = next(r for r in log if r["type"] == "levels")
                self.assertEqual(levels["bill2"], variant)
                self.assertTrue(any(r["type"] == "train_level" for r in log))

    def test_the_default_variant_is_f(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _, one = run_clocked(a, level_bill2=1)
            _, f = run_clocked(b, level_bill2=1, level_bill2_variant="f")
            self.assertEqual(one, f)

    def test_off_or_zero_is_the_old_trainer_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            tr_a, _ = lbt.run(a)
            tr_b, _ = lbt.run(b, level_bill2=0)
            self.assertEqual(Path(tr_a.args.log).read_bytes(), Path(tr_b.args.log).read_bytes())
            self.assertEqual(Path(tr_a.args.weights).read_bytes(), Path(tr_b.args.weights).read_bytes())
            self.assertIsNone(tr_b.b2)
            self.assertIsNone(tr_b.debt)
            log = [json.loads(x) for x in Path(tr_b.args.log).read_text().splitlines()]
            self.assertFalse(any(r["type"] in ("bill2", "bill2_tree") for r in log))
            self.assertTrue(all("l0_bill_read" in r for r in log if r["type"] == "turn"))

    def test_the_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            for changes in ({"level_bill2": 2}, {"level_bill2": True}, {"level_bill2_variant": "f"},
                            {"level_bill2": 1, "level_bill2_variant": "x"}):
                with self.assertRaises(ValueError, msg=changes):
                    LB.LevelBillsTrainer(args_in(tmp), base.settings(**changes), plugin_config())
            # A slow L1 (lbt's periods 1:2:4) cannot be paid design 2; nor a run with no slow level.
            with self.assertRaises(ValueError):
                LB.LevelBillsTrainer(args_in(tmp), base.settings(level_bill2=1), lbt.plugin_config())
            with self.assertRaises(ValueError):
                LB.LevelBillsTrainer(args_in(tmp), base.settings(level_bill2=1),
                                     plugin_config(turn_periods="1:0:1:0"))
            # The plugin's L0 bill block is refused as before (L0's counts come from level_reads).
            with self.assertRaises(ValueError):
                LB.LevelBillsTrainer(args_in(tmp), base.settings(level_bill2=1), plugin_config(turn_l0_bill=1))
            # A line without applied_m stops the trainer.
            tr = LB.LevelBillsTrainer(args_in(tmp), base.settings(level_bill2=1), plugin_config())
            base.write(tr.args.host_log, dbt.host_log())
            first = turn_line(0)
            del first["applied_m"]
            base.write(tr.args.decisions, [first, turn_line(1)])
            with self.assertRaises(SystemExit):
                tr.step()
            tr.log.close()


if __name__ == "__main__":
    unittest.main()
