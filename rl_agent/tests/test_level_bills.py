"""The level bills (learner/level_bills.py; docs/INDEPENDENT_LEARNERS_PLAN.md
sections 4 and 5): the owed cell with bytes counted when written, by hand in
both of its branches; the bill's parts; and, on a synthetic clocked run, one
transition per turn for L0 from L0's own bill, one macro transition per hold
for each slow level with the bill recomputed from the host log, gradient
steps that move only the level's own network, and the refusals."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import torch
except ImportError:
    torch = None

if torch is not None:
    from learner import level_bills as LB  # noqa: E402
    from learner import turn_trainer as TT  # noqa: E402
from learner import learned2 as L2  # noqa: E402
from learner import turn_ledger as TL  # noqa: E402
from learner import turn_weights as TW  # noqa: E402
from learner.prices import from_plugin_config, l0_read_cost  # noqa: E402
from tests import test_turn_trainer as base  # noqa: E402

ACTING = 3
LEVELS = base.LEVELS
TURNS = base.TURNS
READ_KEYS = ("probes", "filter_passes", "filter_hits", "seeks", "get_reopens",
             "iter_reopens", "reopen_nanos", "hidden_steps")


def row(F=0.0, P=100.0, s_out=(0, 0, 0), g_in=(0, 0, 0), M=(0, 0, 0), R=(0, 0, 0), H=(0, 0, 0)):
    return LB.Row(F=F, P=P, s_out=list(s_out), g_in=list(g_in), M=list(M), R=list(R), H=list(H))


@unittest.skipIf(torch is None, "needs torch")
class OwedCellTest(unittest.TestCase):
    def rows(self):
        """Eight turns: flushes of 2000 bytes land on even turns only, puts
        are steady (10 bytes written per put); 1000 bytes leave L0 and 500
        leave L1 every turn; merges into L1 drop 250 bytes a turn."""
        return {t: row(F=2000.0 if t % 2 == 0 else 0.0, s_out=(1000, 500, 0),
                       g_in=(0, 250, 0), M=(50, 30, 0), R=(1, 7, 2), H=(0, 3, 1))
                for t in range(8)}

    def test_bytes_are_counted_when_written_not_when_a_flush_lands(self):
        rows = self.rows()
        # Hold [4, 8): 400 puts x (8000 flushed / 800 puts) = 4000 bytes
        # written, whether the hold holds two flushes or not.
        self.assertAlmostEqual(LB.sf_cell(rows, 0, 4, 8), 200.0)       # moved 4000 >= owed 4000
        self.assertAlmostEqual(LB.sf_cell(rows, 0, 4, 6), 100.0)       # one flush in it: 2000 written
        rows[5].F, rows[4].F = 2000.0, 0.0                             # the flush lands a turn later
        self.assertAlmostEqual(LB.sf_cell(rows, 0, 4, 8), 200.0)

    def test_a_level_that_moved_less_pays_the_rest_at_the_last_price(self):
        rows = self.rows()
        # Step 1 over [4, 8): owed 4000 - 1000 dropped above = 3000; moved
        # 2000; paid 120; the span before paid 120 for 2000 bytes (0.06 each).
        self.assertAlmostEqual(LB.sf_cell(rows, 1, 4, 8), 120 + 0.06 * 1000)
        # The look-back skips a span in which the level moved nothing.
        for t in range(2, 4):
            rows[t].s_out[1], rows[t].M[1] = 0.0, 0.0
        self.assertAlmostEqual(LB.sf_cell(rows, 1, 4, 6), 60 + 0.06 * 500)
        # No earlier span moved anything: the unpaid term is 0.
        for t in range(0, 2):
            rows[t].s_out[1], rows[t].M[1] = 0.0, 0.0
        self.assertAlmostEqual(LB.sf_cell(rows, 1, 4, 6), 60.0)

    def test_the_edges(self):
        rows = self.rows()
        self.assertEqual(LB.sf_cell(rows, -1, 4, 8), 0.0)              # no step above L0
        self.assertEqual(LB.sf_cell(rows, 3, 4, 8), 0.0)               # past the levels
        for t in rows:                                                 # more dropped than written
            rows[t].g_in[0] = 5000.0
        self.assertEqual(LB.sf_cell(rows, 0, 4, 8), 0.0)
        for t in rows:
            rows[t].s_out[2] = 0.0
        self.assertEqual(LB.sf_cell(rows, 2, 4, 8), 0.0)               # nothing owed, nothing moved

    def test_the_bill_is_its_five_parts(self):
        rows = self.rows()
        parts = LB.sf_bill(rows, 1, 4, 8)
        self.assertAlmostEqual(parts["A_in"], 200.0)
        self.assertAlmostEqual(parts["A_out"], 180.0)
        self.assertEqual((parts["R"], parts["H"], parts["H_below"]), (28, 12, 4))
        self.assertAlmostEqual(parts["bill"], 424.0)
        deepest = LB.sf_bill(rows, 2, 4, 8)
        self.assertEqual(deepest["H_below"], 0.0)
        del rows[6]
        self.assertIsNone(LB.sf_bill(rows, 1, 4, 8))                   # a turn was not priced
        self.assertIsNone(LB.sf_bill(rows, 1, 4, 4))


def host_log():
    """test_turn_trainer's host log with each job's output level."""
    recs = base.host_log()
    for r in recs:
        if r["type"] == "job_end":
            r["output_level"] = r["start_level"] + 1
    return recs


def decided(t):
    return [1, int(t % 2 == 1), int(t % 4 == 3)] + [1] * (LEVELS - 3)


def turn_line(t, final=False):
    names = TW.feature_names(ACTING)
    line = {"type": "turn", "t": t, "op0": t * base.TURN, "op1": (t + 1) * base.TURN,
            "final": int(final), "x": {n: 0.01 * ((i * 7 + t) % 13) for i, n in enumerate(names)},
            "masks": [[1, 1, 0, 1]] * LEVELS, "owed": [0.0] * LEVELS, "mid_applies": 0,
            "inflow": [0] * LEVELS, "decided": decided(t),
            "level_reads": {k: [(i + 1) * (t + 2) * (n + 1) for n in range(LEVELS)]
                            for i, k in enumerate(READ_KEYS)}}
    if not final:
        line.update({"action": ["compact" if t % 3 == 0 else "hold",
                                "expand" if t % 4 == 1 else "compact", "expand"] +
                               ["hold"] * (LEVELS - 3),
                     "set": 0, "explored": -1, "eps": 0.1})
    return line


def plugin_config(**changes):
    cfg = base.plugin_config("vdn")
    cfg.update({"turn_acting": ACTING, "turn_decay": 0, "level_reads": 1,
                "turn_periods": "1:2:4", "turn_slow_explore_decisions": 4,
                "beta_w": 10.0})
    cfg.update(changes)
    return cfg


def make(tmp, cfg=None, **changes):
    args = type("A", (), {})()
    for name in ("decisions", "host_log", "log", "summary", "weights"):
        setattr(args, name, str(Path(tmp) / f"{name}.out"))
    s = base.settings(slow_min_replay=2, slow_batch=4, **changes)
    return LB.LevelBillsTrainer(args, s, cfg or plugin_config())


def run(tmp, **changes):
    tr = make(tmp, **changes)
    base.write(tr.args.host_log, host_log())
    lines = [{"type": "turn_start", "arch": "vdn", "acting": ACTING,
              "features": TW.feature_names(ACTING)}]
    lines += [turn_line(t) for t in range(TURNS)] + [{"type": "stop"}]
    base.write(tr.args.decisions, lines)
    tr.step()
    assert tr.done()
    tr.finish("test")
    log = [json.loads(line) for line in Path(tr.args.log).read_text().splitlines()]
    return tr, log


@unittest.skipIf(torch is None, "needs torch")
class ClockedRunTest(unittest.TestCase):
    def pricer(self):
        pricer = L2.HostLogPricer(from_plugin_config(plugin_config()))
        for r in host_log():
            pricer.add(r)
        return pricer

    def test_l0_learns_every_turn_from_its_own_bill(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr, log = run(tmp)
            turns = [r for r in log if r["type"] == "turn"]
            # Line 0 closes the native turn; the last turn ends at the drain.
            self.assertEqual([r["t"] for r in turns], list(range(1, TURNS - 1)))
            pricer = self.pricer()
            for r in turns:
                lo, hi = pricer.bounds(r["op0"], r["op1"])
                block = turn_line(r["t"])["level_reads"]
                read = pricer.p.beta_r * l0_read_cost(pricer.p.v2.base, TL.l0_counts(block))
                merge = TL.merge_cells(pricer, lo, hi, LEVELS)[0]
                self.assertAlmostEqual(r["l0_bill_read"], read, delta=1e-12 * read)
                self.assertAlmostEqual(r["l0_bill_merge"], merge, delta=1e-12 * merge)
                self.assertAlmostEqual(r["J_reward"], read + merge, delta=1e-12 * (read + merge))
                self.assertGreater(merge, 0)
                want = pricer.price(lo, hi)
                self.assertAlmostEqual(r["J"], want["J"], delta=1e-12 * abs(want["J"]))
            self.assertEqual(len(tr.replay), TURNS - 2)
            self.assertGreater(tr.version, 0)
            w = TW.decode(Path(tr.args.weights).read_bytes())
            self.assertEqual((w.acting, len(w.nets)), (ACTING, ACTING))
            self.assertTrue(any(r["type"] == "train" for r in log))

    def test_a_slow_level_learns_once_per_hold_from_its_owed_work_bill(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr, log = run(tmp)
            holds = {j: [r for r in log if r["type"] == "hold" and r["level"] == j] for j in (1, 2)}
            # L1 decides at lines 1, 3, .., 17 (line 19 is past the drain), L2 at 3, 7, 11, 15.
            self.assertEqual([(r["t0"], r["t1"]) for r in holds[1]],
                             [(t, t + 2) for t in range(1, 16, 2)])
            self.assertEqual([(r["t0"], r["t1"]) for r in holds[2]], [(3, 7), (7, 11), (11, 15)])
            pricer = self.pricer()
            rows = {}
            for t in range(1, TURNS - 1):
                lo, hi = pricer.bounds(t * base.TURN, (t + 1) * base.TURN)
                rows[t] = LB.window_row(pricer, lo, hi, turn_line(t)["level_reads"])
            first = rows[1]
            # One flush of 1 MiB, 5000 puts, 4 MiB out of L0 and 2 MiB out of L2 per turn.
            self.assertEqual((first.F, first.P), (float(1 << 20), 5000.0))
            self.assertEqual(first.s_out[:3], [float(4 << 20), 0.0, float(2 << 20)])
            self.assertEqual(sum(first.g_in), 0.0)
            for j in (1, 2):
                for r in holds[j]:
                    want = LB.sf_bill(rows, j, r["t0"] + 1, r["t1"] + 1)
                    for key, value in want.items():
                        self.assertAlmostEqual(r[key], value, delta=1e-9 * max(1.0, abs(value)), msg=key)
                    span = range(r["t0"] + 1, r["t1"] + 1)
                    # By hand: the hold's written bytes are 1 MiB a turn. L0
                    # moved 4 MiB a turn, so L1 is billed a quarter of what
                    # the L0 merges cost; L1 itself moved nothing and has no
                    # earlier price; L2 moved 2 MiB a turn: half its merges.
                    if j == 1:
                        self.assertAlmostEqual(r["A_in"], sum(rows[t].M[0] for t in span) / 4,
                                               delta=1e-9 * r["A_in"])
                        self.assertEqual(r["A_out"], 0.0)
                    else:
                        self.assertEqual(r["A_in"], 0.0)
                        self.assertAlmostEqual(r["A_out"], sum(rows[t].M[2] for t in span) / 2,
                                               delta=1e-9 * r["A_out"])
                    self.assertEqual(r["a"], TW.ACTIONS.index(turn_line(r["t0"])["action"][j]))
            self.assertEqual([len(h) for h in tr.holds], [0, 8, 3])
            self.assertTrue(any(r["type"] == "train_level" and r["level"] == 1 for r in log))
            levels = [r for r in log if r["type"] == "levels"]
            self.assertEqual(levels[0]["holds"], [0, 8, 3])

    def test_a_levels_step_moves_only_its_own_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr, _ = run(tmp)

            def snapshot():
                return [[p.detach().clone() for p in net.parameters()] for net in tr.model.nets]

            def changed(before):
                return [any(not torch.equal(a, b) for a, b in zip(old, net.parameters()))
                        for old, net in zip(before, tr.model.nets)]

            before = snapshot()
            tr.slow_step(1, tr.slow_eligible(1))
            self.assertEqual(changed(before), [False, True, False])
            before = snapshot()
            tr.slow_step(2, tr.slow_eligible(2))
            self.assertEqual(changed(before), [False, False, True])
            before = snapshot()
            tr.train_step(tr.eligible(final=False))
            self.assertEqual(changed(before), [True, False, False])

    def test_two_hold_returns_need_two_holds_in_a_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr, log = run(tmp, slow_n_step=2)
            self.assertEqual(tr.slow_eligible(1), list(range(7)))
            self.assertEqual(tr.slow_eligible(2), [0, 1])
            kept = copy.copy(tr.holds[1])
            del tr.holds[1][3]                       # a hold that was not priced
            self.assertEqual(tr.slow_eligible(1), [0, 1, 3, 4, 5])
            tr.holds[1] = kept
            g = tr.gamma ** 2                        # L1's period is 2 turns
            h0, h1 = tr.holds[1][0], tr.holds[1][1]
            want = tr.hold_reward(1, h0) + g * tr.hold_reward(1, h1)
            tr.rng.randrange = lambda n: 0           # every draw is the first hold
            x1 = tr.tensors([h1.x1])
            boot = float(tr.level_target(1, x1, tr.masks([h1.m1]))[0])
            r = tr.slow_step(1, tr.slow_eligible(1))
            self.assertAlmostEqual(r["y"], want + g ** 2 * boot, places=9)
            self.assertTrue(any(r["type"] == "train_level" for r in log))

    def test_the_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            for changes in ({"turn_arch": "factored"}, {"level_reads": 0},
                            {"turn_periods": "1:2"}, {"turn_periods": "2:2:4"},
                            {"turn_l0_bill": 1}):
                with self.assertRaises(ValueError, msg=changes):
                    make(tmp, plugin_config(**changes))
            with self.assertRaises(ValueError):
                make(tmp, merge_charge="rate")
            # A turn line without the clock's flags or the read counts stops the trainer.
            tr = make(tmp)
            base.write(tr.args.host_log, host_log())
            line = turn_line(1)
            del line["decided"]
            base.write(tr.args.decisions, [turn_line(0), line])
            with self.assertRaises(SystemExit):
                tr.step()
            tr.log.close()


if __name__ == "__main__":
    unittest.main()
