"""The turn trainer (learner/turn_trainer.py; the design's §7 and §8): on a
synthetic host log and decision log it prices every turn as the evaluator's
pricer does, joins each turn line to the one before it, counts the
SetOptions calls a choice caused, prices the owed work per level, pushes the
weights every push_every_turns transitions, and trains once min_replay are
stored; the shaping telescopes along a path; the reward follows its formula;
and the 4-step return stops at a gap in the turns."""

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

if torch is not None:
    from learner import turn_trainer as TT  # noqa: E402
from learner import learned2 as L2  # noqa: E402
from learner import turn_weights as TW  # noqa: E402
from learner.prices import from_plugin_config  # noqa: E402
from tests import learner_fixtures as fx  # noqa: E402

FG = ["rocksdb.point.sst.probe", "rocksdb.bloom.filter.full.positive", "rocksdb.sorted.run.seek",
      "rocksdb.read.table.reopen", "rocksdb.number.db.next.found", "rocksdb.number.iter.skip",
      "rocksdb.number.keys.read", "rocksdb.number.db.seek", "rocksdb.number.keys.written",
      "rocksdb.read.table.reopen.nanos", "rocksdb.rl.scan.setup.nanos"]
TURN = 10000
TURNS = 20
LEVELS = 6
ACTING = 5


def fg(op):
    return [op, op // 10, 0, 0, 0, 0, op // 2, 0, op // 2, 0, 0]


def host_log():
    """Snaps every 500 operations; per turn a flush, an L0 merge and a deep
    merge from L2; the drain at the last turn's end."""
    recs = [{"type": "header", "fg": FG},
            {"type": "stamp", "name": "measure_start", "op": 0, "t_us": 0, "h": 1e9,
             "tickers": dict(zip(FG, fg(0)))}]
    job = 1
    for op in range(500, TURN * TURNS + 1, 500):
        recs.append({"type": "snap", "op": op, "t_us": op * 20, "fg": fg(op)})
        k = op % TURN
        if k == 1000:
            recs.append({"type": "flush_begin", "job": job, "op": op, "t_us": op * 20, "fg": fg(op)})
            recs.append({"type": "flush_end", "job": job, "op": op + 1, "t_us": op * 20 + 900,
                         "x": 1 << 20, "fg": fg(op + 1)})
            job += 1
        if k == 1500:
            for level, (s, o, x) in ((0, (4 << 20, 8 << 20, 12 << 20)),
                                     (2, (2 << 20, 20 << 20, 22 << 20))):
                recs.append({"type": "job_begin", "job": job, "op": op, "t_us": op * 20,
                             "fg": fg(op)})
                recs.append({"type": "job_end", "job": job, "op": op + 2000,
                             "t_us": op * 20 + 40000, "start_level": level, "trivial": 0,
                             "s": s, "o": o, "x": x, "ok": 1, "fg": fg(op + 2000)})
                recs.append({"type": "h", "op": op + 2000, "h": 1e9 + op})
                job += 1
    recs.append({"type": "stamp", "name": "drain_start", "op": TURN * TURNS,
                 "t_us": TURN * TURNS * 20, "tickers": dict(zip(FG, fg(TURN * TURNS)))})
    return recs


def turn_line(t, owed=None, set_=0, mid=0, action="hold", final=False):
    names = TW.feature_names(ACTING)
    x = {n: 0.01 * ((i * 7 + t) % 13) for i, n in enumerate(names)}
    line = {"type": "turn", "t": t, "op0": t * TURN, "op1": (t + 1) * TURN,
            "final": int(final), "x": x, "masks": [[1, 1, 1, 1]] * LEVELS,
            "owed": owed if owed is not None else [1e6 * (t % 3), 0, 5e6, 0, 0, 0],
            "mid_applies": mid}
    if not final:
        line.update({"action": [action] + ["hold"] * (LEVELS - 1), "set": set_,
                     "explored": -1, "eps": 0.1})
    return line


def settings(**changes):
    s = {"gamma": 0.99, "n_step": 4, "batch": 8, "steps_per_turn": 2, "min_replay": 6,
         "lr": 1e-3, "grad_clip": 10.0, "huber": 1.0, "tau": 0.01, "push_every_turns": 5,
         "clip": 5.0, "hidden_factored": 16, "hidden_vdn": 8, "layers": 2, "c_set": 0.5,
         "seed": 1, "torch_threads": 1}
    s.update(changes)
    return s


def plugin_config(arch):
    cfg = fx.plugin_config()
    cfg.update({"mode": "turn", "turn_arch": arch, "turn_acting": ACTING})
    return cfg


def make(tmp, arch="factored", **changes):
    args = type("A", (), {})()
    for name in ("decisions", "host_log", "log", "summary", "weights"):
        setattr(args, name, str(Path(tmp) / f"{name}.out"))
    return TT.TurnTrainer(args, settings(**changes), plugin_config(arch))


def write(path, records):
    Path(path).write_text("".join(json.dumps(r) + "\n" for r in records))


@unittest.skipIf(torch is None, "needs torch")
class EndToEndTest(unittest.TestCase):
    def run_trainer(self, tmp, arch):
        tr = make(tmp, arch)
        write(tr.args.host_log, host_log())
        lines = [{"type": "turn_start", "arch": arch, "acting": ACTING,
                  "features": TW.feature_names(ACTING)}]
        for t in range(TURNS):
            lines.append(turn_line(t, set_=1 if t % 4 == 0 else 0, mid=1 if t % 6 == 0 else 0,
                                   action="compact" if t % 2 else "hold"))
        lines.append({"type": "stop"})
        write(tr.args.decisions, lines)
        tr.step()
        self.assertTrue(tr.done())
        tr.finish("test")
        log = [json.loads(line) for line in Path(tr.args.log).read_text().splitlines()]
        return tr, lines, log

    def test_prices_every_turn_joins_lines_pushes_and_trains(self):
        for arch in ("factored", "vdn"):
            with tempfile.TemporaryDirectory() as tmp:
                tr, lines, log = self.run_trainer(tmp, arch)
                turns = [r for r in log if r["type"] == "turn"]
                # Line 0 closes the native turn 0; the last turn ends at the
                # drain, past which no snap prices it.
                self.assertEqual([r["t"] for r in turns], list(range(1, TURNS - 1)), arch)
                self.assertEqual(tr.dropped, 1)
                pricer = L2.HostLogPricer(from_plugin_config(plugin_config(arch)))
                for r in host_log():
                    pricer.add(r)
                by_t = {line["t"]: line for line in lines if line["type"] == "turn"}
                for r in turns:
                    want = pricer.price(*pricer.bounds(r["op0"], r["op1"]))
                    self.assertAlmostEqual(r["J"], want["J"], delta=1e-12 * abs(want["J"]))
                    self.assertGreater(r["J"], 0)
                    # The calls the choice caused: its boundary's and this turn's.
                    self.assertEqual(r["changes"], by_t[r["t"] - 1]["set"] +
                                     by_t[r["t"]]["mid_applies"])
                    self.assertEqual(r["actions"][0], 1 if (r["t"] - 1) % 2 else 0)
                    self.assertTrue(math.isfinite(r["r"]) and math.isfinite(r["td"]))
                pushes = [r for r in log if r["type"] == "push"]
                self.assertEqual([r["transitions"] for r in pushes], [5, 10, 15])
                self.assertTrue(any(r["type"] == "train" for r in log))
                w = TW.decode(Path(tr.args.weights).read_bytes())
                self.assertEqual((w.version, w.arch), (3, TW.ARCHS[arch]))
                # Merge prices: L0 and L2 merged; L1 has none and takes the pooled
                # ratio; each is positive.
                self.assertEqual(len(tr.p), LEVELS)
                self.assertGreater(tr.p[0], 0)
                self.assertGreater(tr.p[2], 0)
                pooled = (sum(v[0] for v in tr.merge.values()) /
                          sum(v[1] for v in tr.merge.values()))
                self.assertAlmostEqual(tr.p[1], pooled)
                summary = json.loads(Path(tr.args.summary).read_text())
                self.assertEqual(summary["transitions"], TURNS - 2)
                self.assertEqual(summary["arch"], arch)

    def test_a_plugin_with_other_inputs_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr = make(tmp)
            write(tr.args.decisions, [{"type": "turn_start", "arch": "factored",
                                       "acting": ACTING, "features": ["x"]}])
            with self.assertRaises(SystemExit):
                tr.step()
            tr.log.close()


@unittest.skipIf(torch is None, "needs torch")
class RewardTest(unittest.TestCase):
    def transition(self, t, d0, d1, J=0.0, changes=0):
        names = TW.feature_names(ACTING)
        return TT.Transition(t=t, x0=[0.0] * len(names), m0=[[True] * 4] * ACTING,
                             a=[0] * ACTING, x1=[0.0] * len(names),
                             m1=[[True] * 4] * ACTING, d0=d0, d1=d1, J=J, changes=changes)

    def test_the_shaping_telescopes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr = make(tmp)
            tr.p = [2.0, 1.5, 0.25, 0.1, 0.0, 0.0]
            tr.jbar, tr.spread = 0.0, 1.0
            path = [[1e3 * ((k * 5) % 7), 2e3 * (k % 3), 4e3 * k, 0, 0, 0] for k in range(9)]
            for gamma in (1.0, 0.99):
                tr.gamma = gamma
                total = sum(gamma ** k * tr.reward(self.transition(k, path[k], path[k + 1]))
                            for k in range(len(path) - 1))
                T = len(path) - 1
                self.assertAlmostEqual(total, gamma ** T * tr.phi(path[T]) - tr.phi(path[0]),
                                       delta=1e-9 * abs(tr.phi(path[0])))
            tr.log.close()

    def test_the_reward_follows_its_formula(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr = make(tmp)
            tr.p = [2.0, 1.0, 0, 0, 0, 0]
            tr.jbar, tr.spread, tr.gamma, tr.c_set = -10.0, 4.0, 0.9, 0.5
            r = tr.reward(self.transition(1, [3, 1, 0, 0, 0, 0], [1, 2, 0, 0, 0, 0],
                                          J=12.0, changes=2))
            phi0, phi1 = -(2 * 3 + 1 * 1), -(2 * 1 + 1 * 2)
            want = (-12.0 - 0.5 * 2 + 10.0) / 4.0 + (0.9 * phi1 - phi0) / 4.0
            self.assertAlmostEqual(r, want, places=12)
            tr.log.close()

    def test_the_return_stops_at_a_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr = make(tmp)
            d = [0.0] * LEVELS
            tr.replay = [self.transition(t, d, d) for t in (1, 2, 3, 5, 6, 7, 8, 9)]
            got = dict(tr.eligible(final=False))
            self.assertEqual(got[0], 3)      # 1, 2, 3, then the gap
            self.assertEqual(got[1], 2)
            self.assertEqual(got[3], 4)      # 5..8
            self.assertNotIn(5, got)         # 7, 8, 9: wait for a fourth
            final = dict(tr.eligible(final=True))
            self.assertEqual(final[7], 1)
            tr.log.close()


@unittest.skipIf(torch is None, "needs torch")
class PriorTest(unittest.TestCase):
    """turn_prior: the trainer reads each line's L0 prior (zeros when off),
    puts it in the reward's units (over the frozen spread, as the plugin
    divides by its weights' spread), learns Q = net - prior, and pushes its
    spread in the weights file."""
    def test_lines_carry_the_prior_or_zeros(self):
        names = TW.feature_names(ACTING)
        line = turn_line(3)
        self.assertEqual(TT.parse_turn(line, names, ACTING).prior, [0.0] * 4)
        line["prior"] = None
        self.assertEqual(TT.parse_turn(line, names, ACTING).prior, [0.0] * 4)
        line["prior"] = [0, 1.5, 0, -2]
        self.assertEqual(TT.parse_turn(line, names, ACTING).prior, [0.0, 1.5, 0.0, -2.0])

    def test_prior_q_is_l0_only_over_the_spread(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr = make(tmp, "vdn")
            tr.spread = 4.0
            pq = tr.prior_q([[0, 8, 0, -4], [0, 0, 0, 0]])
            self.assertEqual(tuple(pq.shape), (2, ACTING, 4))
            self.assertEqual(pq[0, 0].tolist(), [0.0, 2.0, 0.0, -1.0])
            self.assertEqual(float(pq[:, 1:].abs().sum()), 0.0)
            tr.log.close()

    def test_a_cold_net_is_valued_at_minus_the_prior(self):
        """Cold (every net output 0): Q(s, a) = -p0[a] / spread (factored:
        centred over the allowed actions), and the bootstrap takes s' at its
        cheapest allowed L0 action."""
        names = TW.feature_names(ACTING)
        for arch in ("factored", "vdn"):
            with tempfile.TemporaryDirectory() as tmp:
                tr = make(tmp, arch)
                tr.jbar, tr.spread, tr.gamma, tr.c_set = 0.0, 2.0, 0.9, 0.0
                tr.p = [0.0] * LEVELS
                d = [0.0] * LEVELS
                masks = [[True] * 4] * ACTING
                tr0 = TT.Transition(t=1, x0=[0.0] * len(names), m0=masks, a=[3] + [0] * (ACTING - 1),
                                    x1=[0.0] * len(names), m1=masks, d0=d, d1=d, J=6.0, changes=0,
                                    p0=[0.0, 4.0, 0.0, -6.0], p1=[0.0, 2.0, 0.0, -8.0])
                r = tr.reward(tr0)
                if arch == "vdn":
                    q, boot = 6.0 / 2, 8.0 / 2
                else:   # centred over the four allowed actions
                    q = -(-6.0 - (-2.0) / 4) / 2
                    boot = -(-8.0 - (-6.0) / 4) / 2
                self.assertAlmostEqual(tr.td_error(tr0), r + 0.9 * boot - q, places=12)
                no_prior = TT.Transition(**{**tr0.__dict__, "p0": [0.0] * 4, "p1": [0.0] * 4})
                self.assertAlmostEqual(tr.td_error(no_prior), r, places=12)
                tr.log.close()

    def test_the_push_carries_the_spread(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr = make(tmp, "vdn")
            tr.spread = 3.25
            tr.push()
            self.assertEqual(TW.decode(Path(tr.args.weights).read_bytes()).spread, 3.25)
            tr.log.close()


if __name__ == "__main__":
    unittest.main()
