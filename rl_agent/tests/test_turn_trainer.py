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
            "mid_applies": mid, "inflow": [1 << 20, 4 << 20, 2 << 20, 0, 0, 0]}
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


@unittest.skipIf(torch is None, "needs torch")
class RewardOptionsTest(unittest.TestCase):
    """The reward options (optional settings; defaults = the reward as before):
    shaping 0 drops the owed-work term; merge_charge "rate" replaces the merges
    that finished in a turn by each level's last rate_turns turns' merge price
    per byte landed there, times the bytes landed this turn; anything else is
    refused."""
    def transition(self, d0, d1, J=3.0, changes=1):
        return TT.Transition(t=1, x0=[], m0=[], a=[], x1=[], m1=[], d0=d0, d1=d1, J=J,
                             changes=changes)

    def test_shaping_off_leaves_the_cost_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            for flag, has_phi in ((1, True), (0, False)):
                tr = make(tmp, shaping=flag)
                tr.p = [2.0, 1.0, 0, 0, 0, 0]
                tr.jbar, tr.spread, tr.gamma, tr.c_set = -10.0, 4.0, 0.9, 0.5
                r = tr.reward(self.transition([3, 1, 0, 0, 0, 0], [1, 2, 0, 0, 0, 0], J=12.0, changes=2))
                cost = (-12.0 - 0.5 * 2 + 10.0) / 4.0
                shaping = (0.9 * -(2 * 1 + 1 * 2) - -(2 * 3 + 1 * 1)) / 4.0
                self.assertAlmostEqual(r, cost + shaping if has_phi else cost, places=12)
                tr.log.close()

    def test_rate_charges_merges_per_byte_landed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr = make(tmp, merge_charge="rate", rate_turns=2)
            line = lambda inflow: TT.Turn(t=1, op0=0, op1=1, final=False, x=[], masks=[], actions=[],
                                          owed=[], set=0, mid_applies=0, explored=-1, eps=0.0,
                                          inflow=inflow)
            # turn 1: no history -> each level keeps its own merges' price: J unchanged
            self.assertAlmostEqual(tr.charged_j(line([10.0, 4.0]), {"J": 100.0, "merges": {0: [30.0, 1], 1: [8.0, 1]}}), 100.0)
            # turn 2: L0 rate 30/10 per byte, L1 8/4; landed 5 and 0 -> 15 + 0 replace 0 finished merges
            self.assertAlmostEqual(tr.charged_j(line([5.0, 0.0]), {"J": 50.0, "merges": {}}), 50.0 + 15.0)
            # turn 3: window = turns 1-2: L0 (30 + 0) / 15, L1 8 / 4; landed 3 and 2; a 40 L1 merge finished
            self.assertAlmostEqual(tr.charged_j(line([3.0, 2.0]), {"J": 70.0, "merges": {1: [40.0, 1]}}),
                                   70.0 - 40.0 + 30.0 / 15.0 * 3.0 + 8.0 / 4.0 * 2.0)
            # turn 4: window = turns 2-3 only (rate_turns 2): L0 0 / 8, L1 40 / 2
            self.assertAlmostEqual(tr.charged_j(line([1.0, 1.0]), {"J": 20.0, "merges": {}}),
                                   20.0 + 0.0 + 40.0 / 2.0 * 1.0)
            # a line without inflow (an older log) keeps the evaluator's J
            self.assertAlmostEqual(tr.charged_j(line([]), {"J": 9.0, "merges": {0: [5.0, 1]}}), 9.0)
            tr.log.close()

    def test_defaults_and_bad_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr = make(tmp)
            self.assertTrue(tr.shaping)
            self.assertEqual(tr.merge_charge, "done")
            tr.log.close()
            with self.assertRaises(ValueError):
                make(tmp, merge_charge="later")
            with self.assertRaises(ValueError):
                make(tmp, merge_charge="rate", rate_turns=0)

    def test_end_to_end_with_rate_no_shaping_and_16_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr = make(tmp, merge_charge="rate", shaping=0, n_step=16, rate_turns=4)
            write(tr.args.host_log, host_log())
            lines = [{"type": "turn_start", "arch": "factored", "acting": ACTING,
                      "features": TW.feature_names(ACTING)}]
            lines += [turn_line(t, action="compact" if t % 2 else "hold") for t in range(TURNS)]
            lines.append({"type": "stop"})
            write(tr.args.decisions, lines)
            tr.step()
            tr.finish("test")
            log = [json.loads(line) for line in Path(tr.args.log).read_text().splitlines()]
            turns = [r for r in log if r["type"] == "turn"]
            self.assertTrue(turns)
            self.assertTrue(all("J_reward" in r for r in turns))
            self.assertTrue(any(abs(r["J_reward"] - r["J"]) > 1e-12 for r in turns))
            self.assertTrue(any(r["type"] == "push" for r in log))
            # no shaping: the reward is the cost part alone
            for r in turns:
                self.assertTrue(math.isfinite(r["r"]))


@unittest.skipIf(torch is None, "needs torch")
class OneActingLevelTest(unittest.TestCase):
    """turn_acting 1 (L0 the only agent, every deeper level held native): the trainer reads the L0-only inputs,
    learns, and pushes a one-level net (both arms), with the reward options on."""
    def test_l0_only_trains_and_pushes(self):
        for arch in ("vdn", "factored"):
            with tempfile.TemporaryDirectory() as tmp:
                args = type("A", (), {})()
                for name in ("decisions", "host_log", "log", "summary", "weights"):
                    setattr(args, name, str(Path(tmp) / f"{name}.out"))
                cfg = fx.plugin_config(); cfg.update({"mode": "turn", "turn_arch": arch, "turn_acting": 1})
                tr = TT.TurnTrainer(args, settings(merge_charge="rate", shaping=0, n_step=16), cfg)
                write(args.host_log, host_log())
                names = TW.feature_names(1)
                lines = [{"type": "turn_start", "arch": arch, "acting": 1, "features": names}]
                for t in range(TURNS):
                    line = turn_line(t, action="expand" if t % 3 else "hold")
                    line["x"] = {n: 0.01 * ((i * 7 + t) % 13) for i, n in enumerate(names)}
                    lines.append(line)
                lines.append({"type": "stop"})
                write(args.decisions, lines)
                tr.step()
                tr.finish("test")
                log = [json.loads(line) for line in Path(args.log).read_text().splitlines()]
                self.assertTrue(any(r["type"] == "train" for r in log), arch)
                self.assertTrue(any(r["type"] == "push" for r in log), arch)
                self.assertTrue(Path(args.weights).stat().st_size > 0)


def l0_read(t):
    """A turn line's "l0_read" (controller/plugin.cc JsonL0Read), varying by turn."""
    return {"probes": 700 + 10 * t, "filter_passes": 30 + t, "filter_hits": 9, "seeks": 50 + 2 * t,
            "get_reopens": 2, "iter_reopens": t % 3, "reopen_nanos": 12345, "hidden_steps": 40 + t,
            "l1_hidden_steps": 60}


@unittest.skipIf(torch is None, "needs torch")
class L0BillTest(unittest.TestCase):
    """turn_l0_bill 1 (L0_BILL; L0 alone): the reward's J is L0's own bill, beta_r times the line's
    L0 read counts at cost model 2's base prices plus the L0 merges that finished in the turn, each at
    its whole price (merge_prices), with no shaping; "J" stays the evaluator's. 0 (or absent) leaves
    everything as it was, l0_read blocks or not; acting other than 1, or merge_charge "rate", is
    refused, and a line without the block under the flag stops the trainer."""
    BETA_R = 2.5

    def config(self, bill=None, acting=1, prior=0):
        cfg = fx.plugin_config()
        cfg.update({"mode": "turn", "turn_arch": "vdn", "turn_acting": acting, "beta_r": self.BETA_R,
                    "turn_prior": prior})
        if bill is not None:
            cfg["turn_l0_bill"] = bill
        return cfg

    def trainer(self, tmp, cfg, **changes):
        args = type("A", (), {})()
        for name in ("decisions", "host_log", "log", "summary", "weights"):
            setattr(args, name, str(Path(tmp) / f"{name}.out"))
        return TT.TurnTrainer(args, settings(**changes), cfg)

    def run_trainer(self, tmp, cfg, blocks=True, prior=False, **changes):
        tr = self.trainer(tmp, cfg, **changes)
        write(tr.args.host_log, host_log())
        lines = [{"type": "turn_start", "arch": "vdn", "acting": 1, "features": TW.feature_names(1)}]
        for t in range(TURNS):
            line = turn_line(t, set_=1 if t % 4 == 0 else 0, mid=1 if t % 6 == 0 else 0,
                             action="compact" if t % 2 else "expand")
            if blocks:
                line["l0_read"] = l0_read(t)
            if prior:
                line["prior"] = [0.0, 3.0 * (t % 2), 0.0, -2.0]
            lines.append(line)
        lines.append({"type": "stop"})
        write(tr.args.decisions, lines)
        tr.step()
        self.assertTrue(tr.done())
        tr.finish("test")
        log = [json.loads(x) for x in Path(tr.args.log).read_text().splitlines()]
        return tr, log

    def test_the_bill_follows_its_formula(self):
        cfg = self.config(bill=1)
        pricer = L2.HostLogPricer(from_plugin_config(cfg))
        for r in host_log():
            pricer.add(r)
        c = cfg
        for prior in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                tr, log = self.run_trainer(tmp, cfg, prior=prior, shaping=1)
                self.assertFalse(tr.shaping)              # the flag drops the shaping
                turns = [r for r in log if r["type"] == "turn"]
                self.assertEqual([r["t"] for r in turns], list(range(1, TURNS - 1)))
                for r in turns:
                    n = l0_read(r["t"])
                    read = self.BETA_R * (c["c_f"] * n["probes"] + c["c_blk"] * n["filter_passes"] +
                                          c["c_sk"] * n["seeks"] +
                                          c["c_open"] * (n["get_reopens"] + n["iter_reopens"]) +
                                          c["c_st"] * (n["hidden_steps"] + n["l1_hidden_steps"]))
                    self.assertAlmostEqual(r["bill_read"], read, delta=1e-12 * read)
                    b = pricer.bounds(r["op0"], r["op1"])
                    merge = TT.merge_prices(pricer, *b)[0][0]   # this turn's one L0 merge, whole
                    self.assertGreater(merge, 0)
                    self.assertAlmostEqual(r["bill_merge"], merge, delta=1e-12 * merge)
                    self.assertAlmostEqual(r["J_reward"], read + merge, delta=1e-12 * (read + merge))
                    want = pricer.price(*b)["J"]               # "J" stays the evaluator's
                    self.assertAlmostEqual(r["J"], want, delta=1e-12 * abs(want))
                    self.assertTrue(math.isfinite(r["r"]) and math.isfinite(r["td"]))
                # The centring and the spread are the bill's.
                bills = [-r["J_reward"] for r in turns[:5]]
                push = next(r for r in log if r["type"] == "push")
                self.assertAlmostEqual(push["jbar"], sum(bills) / 5, delta=1e-9 * abs(push["jbar"]))
                self.assertTrue(any(r["type"] == "train" for r in log))

    def test_the_reward_has_no_shaping_under_the_flag(self):
        names = TW.feature_names(1)
        with tempfile.TemporaryDirectory() as tmp:
            tr = self.trainer(tmp, self.config(bill=1), shaping=1)
            tr.p = [2.0, 1.0, 0, 0, 0, 0]
            tr.jbar, tr.spread, tr.gamma, tr.c_set = -10.0, 4.0, 0.9, 0.5
            t = TT.Transition(t=1, x0=[0.0] * len(names), m0=[[True] * 4], a=[0],
                              x1=[0.0] * len(names), m1=[[True] * 4], d0=[3, 1, 0, 0, 0, 0],
                              d1=[1, 2, 0, 0, 0, 0], J=12.0, changes=2)
            self.assertAlmostEqual(tr.reward(t), (-12.0 - 0.5 * 2 + 10.0) / 4.0, places=12)
            tr.log.close()

    def test_flag_0_leaves_the_reward_identical(self):
        """No key, key 0, and key 0 with l0_read blocks on the lines: the same log, record for record,
        and no bill fields."""
        logs = []
        for bill, blocks in ((None, False), (0, False), (0, True)):
            with tempfile.TemporaryDirectory() as tmp:
                tr, log = self.run_trainer(tmp, self.config(bill=bill), blocks=blocks)
                self.assertFalse(tr.l0_bill)
                self.assertTrue(tr.shaping)
                logs.append(log)
        self.assertTrue(any(r["type"] == "train" for r in logs[0]))
        for log in logs:
            for r in log:
                self.assertNotIn("bill_read", r)
                self.assertNotIn("bill_merge", r)
            turns = [r for r in log if r["type"] == "turn"]
            self.assertTrue(all(r["J_reward"] == r["J"] for r in turns))
        self.assertEqual(logs[1], logs[0])
        self.assertEqual(logs[2], logs[0])

    def test_acting_other_than_1_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            for acting in (2, 5):
                with self.assertRaisesRegex(ValueError, "turn_acting 1"):
                    self.trainer(tmp, self.config(bill=1, acting=acting))
            self.trainer(tmp, self.config(bill=0, acting=5)).log.close()

    def test_rate_with_the_flag_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "rate"):
                self.trainer(tmp, self.config(bill=1), merge_charge="rate")
            tr = self.trainer(tmp, self.config(bill=1), merge_charge="done")
            self.assertTrue(tr.l0_bill)
            tr.log.close()
            self.trainer(tmp, self.config(bill=0), merge_charge="rate").log.close()

    def test_a_line_without_the_block_stops_the_trainer(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                self.run_trainer(tmp, self.config(bill=1), blocks=False)

    def test_the_read_cost_prices_each_count(self):
        base = {"probe": 1.0, "block": 10.0, "seek": 100.0, "reopen": 1000.0, "step": 1e4}
        counts = {"probes": 1, "filter_passes": 2, "filter_hits": 99, "seeks": 3, "get_reopens": 4,
                  "iter_reopens": 5, "reopen_nanos": 999, "hidden_steps": 6, "l1_hidden_steps": 7}
        self.assertEqual(TT.l0_read_cost(base, counts), 1 + 20 + 300 + 9000 + 130000)
        del counts["seeks"]
        with self.assertRaises(KeyError):
            TT.l0_read_cost(base, counts)
