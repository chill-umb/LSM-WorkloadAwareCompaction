"""The cost ledger and the bills (learner/turn_ledger.py; the plan
docs/INDEPENDENT_LEARNERS_PLAN.md §4): on a hand-made host log whose costs
are worked out by hand below, the merge cells are each merge whole at its
start level (trivial moves and flushes left out) and equal the trainer's
merge_prices; the read cells price each level's counters; each bill preset
picks exactly its cells; at level 0 the three-part bill is the L0 bill as
built (turn_trainer.l0_bill_parts, prices.l0_read_cost); and the window's J
is the ledger's total plus the named left-out parts.

Hand prices (learner_fixtures.plugin_config, kappas 0 unless a test sets
them): c_f 100, c_blk 1000, c_sk 2000, c_open 5000, c_st 10, c_w 2, c_cr 0.5,
c_put 50, c_mt 20, c_get0 30, c_sc0 40, c_ib 0, c_s 0.001, q_bar 1000; job
prices flush 1000, l0 2000, deep 3000, move 400; betas w 3, r 2, s 1."""

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import torch
except ImportError:
    torch = None

if torch is not None:
    from learner import turn_trainer as TT  # noqa: E402
from learner import learned2 as L2  # noqa: E402
from learner import turn_ledger as TL  # noqa: E402
from learner.logs import KINDS, STEP_TYPES  # noqa: E402
from learner.prices import from_plugin_config, l0_read_cost  # noqa: E402
from tests import learner_fixtures as fx  # noqa: E402

FG = ["rocksdb.point.sst.probe", "rocksdb.bloom.filter.full.positive", "rocksdb.sorted.run.seek",
      "rocksdb.read.table.reopen", "rocksdb.number.db.next.found", "rocksdb.number.iter.skip",
      "rocksdb.number.keys.read", "rocksdb.number.db.seek", "rocksdb.number.keys.written",
      "rocksdb.read.table.reopen.nanos", "rocksdb.rl.scan.setup.nanos"]
H0 = 1e6            # held bytes, constant (no h record before op 2000)


def fg(op):
    """Tickers at op: probes op/10, filter positives op/100, seeks op/50, reopens op/500,
    returned steps op/4, hidden steps op/20, Gets op/2, scans op/100, Puts op/2."""
    return [op // 10, op // 100, op // 50, op // 500, op // 4, op // 20, op // 2, op // 100,
            op // 2, 0, 0]


def host_log():
    """Snaps every 100 ops to 3000; the drain at 3000. Window 1 = [0, 1000) maps to records
    measure_start .. the snap at 1100; window 2 = [1000, 2000) to the snaps at 1100 .. 2100.
    Window 1: an L0 merge (S 10, O 20, X 30) ends at 400, an L1 merge (S 5, O 50, X 55) at 600,
    a trivial move from L2 at 700, a flush (X 7) at 800. Window 2: an L2 merge (S 2, O 8, X 10)
    at 1500; an h record at 2050 (after window 2's own range starts, before its end)."""
    jobs = {  # end op -> (job, kind, start level, s, o, x, begin op)
        400: (1, "merge", 0, 10, 20, 30, 100),
        600: (2, "merge", 1, 5, 50, 55, 450),
        700: (3, "move", 2, 9, 0, 0, 650),
        800: (4, "flush", -1, 0, 0, 7, 750),
        1500: (5, "merge", 2, 2, 8, 10, 1200),
    }
    begins = {v[6]: v for v in jobs.values()}
    recs = [{"type": "header", "fg": FG},
            {"type": "stamp", "name": "measure_start", "op": 0, "t_us": 0, "h": H0,
             "tickers": dict(zip(FG, fg(0)))}]
    for op in range(50, 3001, 50):
        if op % 100 == 0:
            recs.append({"type": "snap", "op": op, "t_us": op * 10, "fg": fg(op)})
        if op in begins:
            job, kind, *_ = begins[op]
            recs.append({"type": "flush_begin" if kind == "flush" else "job_begin", "job": job,
                         "op": op, "t_us": op * 10, "fg": fg(op)})
        if op in jobs:
            job, kind, level, s, o, x, _ = jobs[op]
            if kind == "flush":
                recs.append({"type": "flush_end", "job": job, "op": op, "t_us": op * 10 + 5,
                             "x": x, "fg": fg(op)})
            else:
                recs.append({"type": "job_end", "job": job, "op": op, "t_us": op * 10 + 5,
                             "start_level": level, "trivial": int(kind == "move"), "s": s, "o": o,
                             "x": x, "ok": 1, "fg": fg(op)})
        if op == 2050:
            recs.append({"type": "h", "op": op, "h": 3 * H0})
    recs.append({"type": "stamp", "name": "drain_start", "op": 3000, "t_us": 30000,
                 "tickers": dict(zip(FG, fg(3000)))})
    return recs


def block():
    """Window 1's level_reads block, four levels."""
    return {"probes": [10, 20, 30, 0], "filter_passes": [1, 2, 3, 0], "filter_hits": [1, 1, 3, 0],
            "seeks": [4, 5, 6, 0], "get_reopens": [0, 1, 0, 0], "iter_reopens": [1, 0, 0, 0],
            "reopen_nanos": [9, 9, 0, 0], "hidden_steps": [7, 8, 9, 0]}


def config(kappas=False):
    cfg = fx.plugin_config()
    cfg.update(beta_w=3.0, beta_r=2.0, beta_s=1.0)
    for x in STEP_TYPES:
        cfg[f"kappa_b_{x}"] = 2e-3 if kappas else 0.0
        for kind in KINDS:
            cfg[f"kappa_j_{x}_{kind}"] = 0.0
    if kappas:
        cfg["kappa_j_probe_deep"] = 0.1
        cfg["kappa_j_put_l0"] = 0.05
        cfg["lambda"] = 0.25
    return cfg


def pricer(cfg=None):
    pr = L2.HostLogPricer(from_plugin_config(cfg or config()))
    for r in host_log():
        pr.add(r)
    return pr


def close(a, b, rel=1e-12):
    return abs(a - b) <= rel * max(abs(a), abs(b), 1.0)


# The hand prices of the window-1 merges: tau = c_job + c_cr (S + O) + c_w X, times beta_w 3.
M0 = 3 * (2000 + 0.5 * 30 + 2 * 30)          # 6225
M1 = 3 * (3000 + 0.5 * 55 + 2 * 55)          # 9412.5
M2_W2 = 3 * (3000 + 0.5 * 10 + 2 * 10)       # 9075, window 2
# Window 1's read cells, beta_r 2.
R = [2 * (100 * 10 + 1000 * 1 + 2000 * 4 + 5000 * 1),    # 30000
     2 * (100 * 20 + 1000 * 2 + 2000 * 5 + 5000 * 1),    # 38000
     2 * (100 * 30 + 1000 * 3 + 2000 * 6),               # 36000
     0.0]
H = [2 * 10 * 7, 2 * 10 * 8, 2 * 10 * 9, 0.0]           # 140, 160, 180, 0


class LedgerCellsTest(unittest.TestCase):

    def test_the_bounds_are_the_ones_worked_out(self):
        pr = pricer()
        lo, hi = pr.bounds(0, 1000)
        self.assertEqual((pr.recs[lo]["name"], pr.recs[hi]["op"]), ("measure_start", 1100))
        lo, hi = pr.bounds(1000, 2000)
        self.assertEqual((pr.recs[lo]["op"], pr.recs[hi]["op"]), (1100, 2100))

    def test_each_merge_is_booked_whole_at_its_start_level(self):
        pr = pricer()
        self.assertEqual(TL.merge_cells(pr, *pr.bounds(0, 1000), 4), [M0, M1, 0.0, 0.0])
        # The trivial move from L2 (window 1) is no merge; the L2 merge lands in window 2.
        self.assertEqual(TL.merge_cells(pr, *pr.bounds(1000, 2000), 4), [0.0, 0.0, M2_W2, 0.0])
        parts = TL.merges(pr, *pr.bounds(0, 1000))
        self.assertEqual([(m.level, m.tau, m.i_w, m.i_r) for m in parts],
                         [(0, 2075.0, 0.0, 0.0), (1, 3137.5, 0.0, 0.0)])

    def test_a_merge_past_the_levels_is_refused(self):
        pr = pricer()
        with self.assertRaises(ValueError):
            TL.merge_cells(pr, *pr.bounds(1000, 2000), 2)

    @unittest.skipIf(torch is None, "needs torch")
    def test_the_merge_cells_are_merge_prices_exactly(self):
        for kappas in (False, True):
            pr = pricer(config(kappas))
            for op0, op1 in ((0, 1000), (1000, 2000)):
                b = pr.bounds(op0, op1)
                want = TT.merge_prices(pr, *b)
                got = TL.merge_cells(pr, *b, 4)
                for level in range(4):
                    self.assertEqual(got[level], want.get(level, [0.0])[0], (kappas, op0, level))
            if kappas:      # the interference is really there
                m = TL.merges(pr, *pr.bounds(0, 1000))
                self.assertTrue(all(x.i_w > 0 and x.i_r > 0 for x in m))

    def test_the_read_cells_price_each_counter(self):
        pr = pricer()
        r, h = TL.read_cells(pr.p, block())
        self.assertEqual(r, R)
        self.assertEqual(h, H)

    def test_a_block_with_a_missing_or_short_counter_is_refused(self):
        pr = pricer()
        b = block()
        del b["seeks"]
        with self.assertRaises(KeyError):
            TL.read_cells(pr.p, b)
        b = block()
        b["probes"] = [1, 2]
        with self.assertRaises(ValueError):
            TL.read_cells(pr.p, b)

    def test_the_window_ledger_holds_all_three_cells(self):
        pr = pricer()
        led = TL.window_ledger(pr, *pr.bounds(0, 1000), block())
        self.assertEqual((led.M, led.R, led.H), ([M0, M1, 0.0, 0.0], R, H))
        self.assertEqual(led.cell(1, "R"), R[1])
        self.assertEqual(led.cell(-1, "M"), 0.0)       # outside the levels: nothing
        self.assertEqual(led.cell(4, "H"), 0.0)
        self.assertEqual(led.total(), M0 + M1 + sum(R) + sum(H))
        with self.assertRaises(ValueError):
            led.cell(0, "X")


class BillTest(unittest.TestCase):

    def test_the_three_part_preset_picks_exactly_its_cells(self):
        self.assertEqual(TL.three_part(1, 4), {(0, "M"): 1.0, (1, "M"): 1.0, (1, "R"): 1.0,
                                               (1, "H"): 1.0, (2, "H"): 1.0})
        self.assertEqual(TL.three_part(2, 4), {(1, "M"): 1.0, (2, "M"): 1.0, (2, "R"): 1.0,
                                               (2, "H"): 1.0, (3, "H"): 1.0})
        # Level 0: no M_{-1} (flushes are not charged).
        self.assertEqual(TL.three_part(0, 4), {(0, "M"): 1.0, (0, "R"): 1.0, (0, "H"): 1.0,
                                               (1, "H"): 1.0})
        # The last level has no level below.
        self.assertEqual(TL.three_part(3, 4), {(2, "M"): 1.0, (3, "M"): 1.0, (3, "R"): 1.0,
                                               (3, "H"): 1.0})

    def test_the_variant_adds_the_reads_below(self):
        w = TL.three_part(1, 4, reads_below=True)
        self.assertEqual(set(w) - set(TL.three_part(1, 4)), {(2, "R")})
        self.assertEqual(w[(2, "R")], 1.0)

    def test_the_neighbour_discount_fills_the_neighbours_other_cells(self):
        w = TL.three_part(1, 4, alpha=0.8)
        self.assertEqual(w, {(0, "M"): 1.0, (0, "R"): 0.8, (0, "H"): 0.8, (1, "M"): 1.0,
                             (1, "R"): 1.0, (1, "H"): 1.0, (2, "M"): 0.8, (2, "R"): 0.8,
                             (2, "H"): 1.0})
        self.assertEqual(TL.three_part(1, 4, reads_below=True, alpha=0.8)[(2, "R")], 1.0)
        with self.assertRaises(ValueError):
            TL.three_part(1, 4, alpha=1.5)
        with self.assertRaises(ValueError):
            TL.three_part(4, 4)

    def test_the_bills_by_hand(self):
        pr = pricer()
        led = TL.window_ledger(pr, *pr.bounds(0, 1000), block())
        # B_1 = M_0 + M_1 + R_1 + H_1 + H_2 = 6225 + 9412.5 + 38000 + 160 + 180
        self.assertEqual(TL.bill(led, TL.three_part(1, 4)), 53977.5)
        self.assertEqual(TL.bill(led, TL.three_part(1, 4, reads_below=True)), 53977.5 + 36000)
        # B_2 = M_1 + M_2 + R_2 + H_2 + H_3 = 9412.5 + 0 + 36000 + 180 + 0
        self.assertEqual(TL.bill(led, TL.three_part(2, 4)), 45592.5)
        parts = TL.bill_parts(led, TL.three_part(1, 4, alpha=0.5))
        self.assertEqual(parts[(0, "R")], 0.5 * R[0])
        self.assertEqual(parts[(2, "M")], 0.0)


class L0BillTest(unittest.TestCase):
    """At level 0 the three-part preset is the L0 bill as built: L0's counts and L1's hidden
    steps at prices.l0_read_cost, times beta_r, plus the L0 merges at their whole price."""

    def test_l0_counts_is_the_l0_read_form(self):
        self.assertEqual(TL.l0_counts(block()),
                         {"probes": 10, "filter_passes": 1, "filter_hits": 1, "seeks": 4,
                          "get_reopens": 0, "iter_reopens": 1, "reopen_nanos": 9,
                          "hidden_steps": 7, "l1_hidden_steps": 8})

    def test_the_read_part_is_l0_read_cost(self):
        for kappas in (False, True):
            pr = pricer(config(kappas))
            led = TL.window_ledger(pr, *pr.bounds(0, 1000), block())
            p = pr.p
            want = p.beta_r * l0_read_cost(p.v2.base, TL.l0_counts(block()))
            got = TL.bill(led, TL.three_part(0, 4)) - led.M[0]
            self.assertTrue(close(got, want), (got, want))
            self.assertEqual(want, R[0] + H[0] + H[1])        # 30300, by hand

    @unittest.skipIf(torch is None, "needs torch")
    def test_the_three_part_bill_at_level_0_is_l0_bill_parts(self):
        for kappas in (False, True):
            pr = pricer(config(kappas))
            stub = type("S", (), {"pricer": pr})()
            for op0, op1, blk in ((0, 1000, block()),
                                  (1000, 2000, {k: [2 * v for v in vs] for k, vs in block().items()})):
                b = pr.bounds(op0, op1)
                line = TT.Turn(t=1, op0=op0, op1=op1, final=False, x=[], masks=[], actions=[],
                               owed=[], set=0, mid_applies=0, explored=-1, eps=0.0,
                               l0_read=TL.l0_counts(blk))
                price = dict(pr.price(*b), merges=TT.merge_prices(pr, *b))
                read, merge = TT.TurnTrainer.l0_bill_parts(stub, line, price)
                led = TL.window_ledger(pr, *b, blk)
                self.assertEqual(merge, led.M[0])
                self.assertTrue(close(TL.bill(led, TL.three_part(0, 4)), read + merge),
                                (kappas, op0))


class LeftOutTest(unittest.TestCase):

    def test_the_left_out_parts_by_hand(self):
        pr = pricer()
        out = TL.left_out(pr, *pr.bounds(0, 1000), block())
        # Over records measure_start .. the snap at 1100: probes 110, filter positives 11,
        # seeks 22, reopens 2, returned steps 275, hidden steps 55, Gets 550, scans 11, Puts 550.
        want = {"flushes": 3 * (1000 + 2 * 7),
                "trivial_moves": 3 * 400,
                "puts": 3 * 50 * 550,
                "returned_steps": 2 * 10 * 275,
                "memtable_and_base": 2 * (20 * (550 + 11) + 30 * 550 + 40 * 11),
                "space": 1 * 0.001 / 1000 * H0 * 1100,
                # the block places probes 60, filter passes 6, seeks 15, reopens 2, hidden 24
                "reads_unplaced": 2 * (100 * 50 + 1000 * 5 + 2000 * 7 + 5000 * 0 + 10 * 31)}
        self.assertEqual(set(out), set(TL.LEFT_OUT))
        for key, value in want.items():
            self.assertTrue(close(out[key], value), (key, out[key], value))

    def test_the_window_j_is_the_ledger_plus_the_left_out_parts(self):
        pr = pricer()
        b = pr.bounds(0, 1000)
        led = TL.window_ledger(pr, *b, block())
        J = pr.price(*b)["J"]
        self.assertEqual(J, 318399.5)                         # by hand: 102379.5 + 214920 + 1100
        self.assertTrue(close(led.total() + sum(TL.left_out(pr, *b, block()).values()), J))
        for kappas in (False, True):
            pr = pricer(config(kappas))
            for op0, op1 in ((0, 1000), (1000, 2000)):
                b = pr.bounds(op0, op1)
                led = TL.window_ledger(pr, *b, block())
                total = led.total() + sum(TL.left_out(pr, *b, block()).values())
                J = pr.price(*b)["J"]
                self.assertTrue(close(total, J), (kappas, op0, total, J))

    def test_the_held_bytes_follow_the_h_records(self):
        pr = pricer()
        # Window 2: records 1100 .. 2100, h = H0 to op 2050 and 3 H0 after it.
        self.assertEqual(TL.held_byte_ops(pr, *pr.bounds(1000, 2000)), H0 * 950 + 3 * H0 * 50)
        self.assertTrue(math.isclose(pr.price(*pr.bounds(1000, 2000))["C_S"],
                                     0.001 / 1000 * (H0 * 950 + 3 * H0 * 50)))


if __name__ == "__main__":
    unittest.main()
