#!/usr/bin/env python3
"""The turn mode's trainer (controller/turn.h; the design is
~/latex/2026-11-07-rl-design/rl-design.tex, §7-§9). Tails the plugin's
decision log ("turn_start", "turn" and "stop" lines) and the fork's host
log, and for each turn line after the first:

* prices the turn it closes, [op0, op1), with cost model 2 on the host log
  exactly as 04 does (learned2.HostLogPricer), so J_t is the evaluator's J;
* builds the transition (s, a) of the line before -> (s', M(s')) of this
  one, with the SetOptions calls it caused (the earlier line's "set" and
  this one's "mid_applies") and both states' owed work (their "owed");
* scores it before learning from it (the prequential 1-step TD error);
* stores it, and takes steps_per_turn steps of 4-step double DQN on
  batches from the whole run once min_replay transitions are stored.

The reward (§7): r_t = (-J_t - c_set n_set - jbar) / spread
+ (gamma Phi(s') - Phi(s)) / spread, with Phi(s) = -sum_i p_i d_i(s), p_i the
run's realised merge price per merge input byte at level i. The input
scaling, jbar, spread and p are frozen between pushes; every
push_every_turns transitions they are refrozen and the weights are written
atomically (RLTW0001, turn_weights.py). Cold start: nothing is read from
another run.

With the plugin config's turn_l0_bill 1 (L0_BILL; L0 alone, turn_acting 1),
J_t in the reward is L0's own bill instead: beta_r times L0's read counts of
the turn (the plugin's "l0_read") at cost model 2's base prices, plus each
L0 merge that finished in the turn at its whole price (merge_prices), with
no shaping. The "turn" lines keep "J", the evaluator's, and add the bill's
parts "bill_read" and "bill_merge".

  python -m learner.turn_trainer --plugin-config CFG --decisions D.jsonl \\
      --host-log H.jsonl --weights W.bin --settings S.json \\
      --log turn_trainer.jsonl --summary turn_trainer_summary.json \\
      [--archive DIR]

Telemetry lines in --log: "turn" (the priced transition), "train" (one
gradient step's loss), "push", "stop". Run from rl_agent/ (or with rl_agent
on PYTHONPATH)."""

from __future__ import annotations

import argparse
import bisect
import copy
import json
import math
import random
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "learner"

import torch  # noqa: E402

from learner import learned2 as L2  # noqa: E402
from learner import turn_model as TM  # noqa: E402
from learner import turn_weights as TW  # noqa: E402
from learner.logs import LogTail  # noqa: E402
from learner.prices import from_plugin_config, l0_read_cost  # noqa: E402

cm = L2.cm

SETTINGS_KEYS = ("gamma", "n_step", "batch", "steps_per_turn", "min_replay",
                 "lr", "grad_clip", "huber", "tau", "push_every_turns", "clip",
                 "hidden_factored", "hidden_vdn", "layers", "c_set", "seed",
                 "torch_threads")


# --------------------------------------------------------------- statistics
class Running:
    """Welford's running mean and variance of a vector."""

    def __init__(self, n: int):
        self.n = 0
        self.mean = [0.0] * n
        self.m2 = [0.0] * n

    def add(self, x: list) -> None:
        self.n += 1
        for i, v in enumerate(x):
            if not math.isfinite(v):
                continue
            d = v - self.mean[i]
            self.mean[i] += d / self.n
            self.m2[i] += d * (v - self.mean[i])

    def std(self) -> list:
        return [math.sqrt(m / self.n) if self.n > 1 else 0.0 for m in self.m2]


def frozen_scaling(stats: Running) -> tuple:
    """(mean, scale): an input that has not varied keeps scale 1."""
    return list(stats.mean), [s if s > 1e-12 else 1.0 for s in stats.std()]


def reward_scale(stats: Running) -> tuple:
    """(jbar, spread) of -J: spread is the standard deviation, else |jbar|,
    else 1, so it is always positive."""
    mean = stats.mean[0]
    std = stats.std()[0]
    return mean, std if std > 0 else (abs(mean) if mean != 0 else 1.0)


def merge_prices(pricer: "L2.HostLogPricer", lo: int, hi: int) -> dict:
    """{start level: [price, S + O]} of the merges (trivial moves and flushes
    left out) that end in the window, each priced as cost model 2 prices it
    in J: beta_w (tau_job + its write interference) + beta_r (its read
    interference), with the exact windows HostLogPricer.price uses."""
    p = pricer.p
    recs = pricer.recs
    fl = recs[pricer.ms]
    floor = (fl["op"], cm._counts(fl, pricer.names))
    i0 = bisect.bisect_right(pricer.job_idx, lo)
    i1 = bisect.bisect_left(pricer.job_idx, hi)
    s0 = bisect.bisect_left(pricer.snap_ops, fl["op"])
    s1 = bisect.bisect_right(pricer.snap_ops, recs[hi]["op"])
    snaps = L2._SnapList(pricer.snaps[s0:s1])
    snaps.ops = pricer.snap_ops[s0:s1]
    out = {}
    for job in pricer.jobs[i0:i1]:
        if job.kind not in ("l0", "deep"):
            continue
        counts, length = cm.window(job, snaps, floor, p.v2.n_win)
        read, write = cm.charge(p.v2, p.q_bar, job, cm.rho_of(p.v2, counts, length))
        price = p.beta_w * (cm.job_price(p.v2, job) + write) + p.beta_r * read
        acc = out.setdefault(int(job.level), [0.0, 0.0])
        acc[0] += price
        acc[1] += job.s + job.o
    return out


# --------------------------------------------------------------- transitions
@dataclass
class Turn:
    """One "turn" line: the state at its boundary and what was chosen there."""
    t: int
    op0: int
    op1: int
    final: bool
    x: list
    masks: list      # acting levels, 4 flags each
    actions: list    # acting levels, action indices (empty when final)
    owed: list
    set: int
    mid_applies: int
    explored: int
    eps: float
    # The L0 prior the plugin logged (turn_prior; J per turn, one value per
    # action, lower better); zeros when it was off.
    prior: list = field(default_factory=lambda: [0.0] * TW.N_ACTIONS)
    # Bytes landed per level over the turn ("inflow"; empty in older logs):
    # merge_charge "rate" charges each level's merges per byte landed there.
    inflow: list = field(default_factory=list)
    # L0's raw read counts over the turn (turn_l0_bill's "l0_read"; None
    # when the plugin logged none).
    l0_read: dict | None = None


@dataclass
class Transition:
    t: int           # the turn the action governed (the later line's t)
    x0: list
    m0: list
    a: list
    x1: list
    m1: list
    d0: list
    d1: list
    J: float
    changes: int
    p0: list = field(default_factory=lambda: [0.0] * TW.N_ACTIONS)  # L0 prior at s
    p1: list = field(default_factory=lambda: [0.0] * TW.N_ACTIONS)  # and at s'


def parse_prior(value) -> list:
    """A turn line's "prior": four numbers, or zeros (null or absent)."""
    if isinstance(value, list) and len(value) == TW.N_ACTIONS:
        return [float(v) for v in value]
    return [0.0] * TW.N_ACTIONS


def parse_turn(r: dict, names: list, acting: int) -> Turn:
    x = [float(r["x"][n]) for n in names]
    masks = [[bool(v) for v in m] for m in r["masks"][:acting]]
    actions = [TW.ACTIONS.index(a) for a in r.get("action", [])[:acting]]
    return Turn(t=int(r["t"]), op0=int(r["op0"]), op1=int(r["op1"]),
                final=int(r.get("final", 0)) == 1, x=x, masks=masks,
                actions=actions, owed=[float(v) for v in r["owed"]],
                set=int(r.get("set", 0)), mid_applies=int(r.get("mid_applies", 0)),
                explored=int(r.get("explored", -1)), eps=float(r.get("eps", 0)),
                prior=parse_prior(r.get("prior")),
                inflow=[float(v) for v in r.get("inflow", [])],
                l0_read=r["l0_read"] if isinstance(r.get("l0_read"), dict) else None)



class TurnTrainer:
    def __init__(self, args, settings: dict, plugin_cfg: dict):
        self.args = args
        self.s = settings
        self.arch = plugin_cfg["turn_arch"]
        self.acting = int(plugin_cfg["turn_acting"])
        self.names = TW.feature_names(self.acting)
        self.pricer = L2.HostLogPricer(from_plugin_config(plugin_cfg))
        self.decisions = LogTail(args.decisions)
        self.host = LogTail(args.host_log)
        self.log = open(args.log, "a", encoding="ascii")
        torch.manual_seed(int(settings["seed"]))
        self.rng = random.Random(int(settings["seed"]))
        self.model = TM.make(self.arch, self.acting, int(settings["hidden_factored"]),
                             int(settings["hidden_vdn"]), int(settings["layers"]))
        self.target = copy.deepcopy(self.model)
        for param in self.target.parameters():
            param.requires_grad_(False)
        self.opt = torch.optim.Adam(self.model.parameters(), lr=float(settings["lr"]))
        self.gamma = float(settings["gamma"])
        self.c_set = float(settings["c_set"])
        # Reward options (optional keys; defaults = the reward as before).
        # shaping 0 drops the owed-work shaping term. merge_charge "rate"
        # charges merges as the L0 prior does, per byte as data lands: each
        # level's merges cost its last rate_turns turns' merge price per byte
        # landed there, times the bytes landed this turn ("done": as each
        # merge finishes, the evaluator's J).
        self.shaping = int(settings.get("shaping", 1)) == 1
        self.merge_charge = str(settings.get("merge_charge", "done"))
        if self.merge_charge not in ("done", "rate"):
            raise ValueError(f"merge_charge is done or rate, not {self.merge_charge!r}")
        self.rate_turns = int(settings.get("rate_turns", 32))
        if self.rate_turns < 1:
            raise ValueError("rate_turns must be >= 1")
        self.rate_hist = []          # (merge price per level, inflow per level), last rate_turns
        # The L0 bill (plugin config turn_l0_bill; absent in older configs: 0):
        # the reward's J is L0's own bill (l0_bill). L0 alone: deeper levels'
        # bills are not built. Each merge is charged whole as it finishes,
        # so merge_charge does not apply ("rate" would contradict it), and
        # there is no owed-work shaping. With turn_prior 1 too, the prior
        # (J per turn) forecasts this same L0 bill's change per move.
        self.l0_bill = int(plugin_cfg.get("turn_l0_bill", 0)) == 1
        if self.l0_bill:
            if self.acting != 1:
                raise ValueError(f"turn_l0_bill 1 is L0's bill alone: it needs turn_acting 1, "
                                 f"not {self.acting}")
            if self.merge_charge == "rate":
                raise ValueError(f"turn_l0_bill 1 charges each L0 merge whole as it finishes; "
                                 f"merge_charge {self.merge_charge!r} contradicts it")
            self.shaping = False
        n = len(self.names)
        self.x_stats = Running(n)
        self.j_stats = Running(1)
        self.merge = {}              # level -> [price, bytes] over the run
        self.mean, self.scale = [0.0] * n, [1.0] * n
        self.jbar, self.spread = 0.0, 1.0
        self.p = []                  # per level, frozen with the rest
        self.pending = []            # turn lines waiting for the host log
        self.prev = None             # the last turn line taken in
        self.replay = []
        self.version = 0
        self.stop_seen = False
        self.started = False
        self.lines = self.priced = self.dropped = self.steps = 0
        self.preq = []

    def emit(self, record: dict) -> None:
        self.log.write(json.dumps(record, separators=(",", ":")) + "\n")
        self.log.flush()

    # ------------------------------------------------------------- reading
    def ingest(self) -> None:
        for r in self.host.read():
            self.pricer.add(r)
        for r in self.decisions.read():
            t = r.get("type")
            if t == "turn_start":
                if list(r.get("features", [])) != self.names:
                    raise SystemExit("the plugin's turn inputs differ from the trainer's")
                if r.get("arch") != self.arch or int(r.get("acting", -1)) != self.acting:
                    raise SystemExit("the plugin's arch or acting levels differ")
                self.started = True
            elif t == "turn":
                self.pending.append(parse_turn(r, self.names, self.acting))
                self.lines += 1
            elif t == "stop":
                self.stop_seen = True

    def take_pending(self) -> list:
        """The turn lines whose turn the host log can price now, in order;
        a line it never will (past the drain, or the run ended) is dropped
        and breaks the chain of transitions."""
        new = []
        while self.pending:
            line = self.pending[0]
            if self.prev is None:          # the first line closes turn 0 (native)
                self.prev = self.pending.pop(0)
                self.observe(self.prev.x)
                continue
            if line.op1 <= line.op0:
                self.dropped += 1
                self.prev = self.pending.pop(0)
                continue
            b = self.pricer.bounds(line.op0, line.op1)
            if b is None:
                past_drain = (self.pricer.ds is not None and
                              line.op1 > self.pricer.recs[self.pricer.ds]["op"])
                # After the plugin's stop, the host log has its drain (or,
                # with no measured phase at all, never prices anything).
                if past_drain or (self.stop_seen and (self.pricer.ds is not None or
                                                      self.pricer.ms is None)):
                    self.dropped += 1
                    self.prev = self.pending.pop(0)
                    continue
                break
            price = self.pricer.price(*b)
            merges = merge_prices(self.pricer, *b)
            for level, (cost, nbytes) in merges.items():
                acc = self.merge.setdefault(level, [0.0, 0.0])
                acc[0] += cost
                acc[1] += nbytes
            new.append((self.prev, line, dict(price, merges=merges)))
            self.prev = self.pending.pop(0)
        return new

    def observe(self, x: list) -> None:
        self.x_stats.add(x)

    # ------------------------------------------------------------- reward
    def phi(self, d: list) -> float:
        return -sum(p * v for p, v in zip(self.p, d))

    def reward(self, tr: Transition) -> float:
        cost = (-tr.J - self.c_set * tr.changes - self.jbar) / self.spread
        if not self.shaping:
            return cost
        return cost + (self.gamma * self.phi(tr.d1) - self.phi(tr.d0)) / self.spread

    def charged_j(self, line: Turn, price: dict) -> float:
        """The J the reward uses. "done": the evaluator's J. "rate": J with
        the merges that finished in the turn replaced by, per level, the last
        rate_turns turns' merge price per byte landed there times the bytes
        landed this turn (a level with no landed bytes in that window keeps
        its own merges' price); causal: this turn is added afterwards."""
        J = float(price["J"])
        if self.merge_charge != "rate" or not line.inflow:
            return J
        merges = price.get("merges", {})
        done = sum(v[0] for v in merges.values())
        levels = set(merges) | set(range(len(line.inflow)))
        charge = 0.0
        for lv in levels:
            cost = sum(h[0].get(lv, 0.0) for h in self.rate_hist)
            landed = sum(h[1][lv] for h in self.rate_hist if lv < len(h[1]))
            now = line.inflow[lv] if lv < len(line.inflow) else 0.0
            charge += cost / landed * now if landed > 0 else merges.get(lv, [0.0])[0]
        self.rate_hist.append(({lv: v[0] for lv, v in merges.items()}, list(line.inflow)))
        del self.rate_hist[:-self.rate_turns]
        return J - done + charge

    def l0_bill_parts(self, line: Turn, price: dict) -> tuple:
        """(read part, merge part) of L0's bill over the turn `line` closes,
        in J: beta_r times L0's read counts of the turn at cost model 2's
        base prices (L0's slice of C_R's quiet reads), and the L0 merges that
        finished in the turn, each at its whole price (merge_prices: beta_w
        (job price + write interference) + beta_r read interference)."""
        if line.l0_read is None:
            raise SystemExit(f"turn {line.t} has no l0_read: the plugin ran without turn_l0_bill")
        p = self.pricer.p
        read = p.beta_r * l0_read_cost(p.v2.base, line.l0_read)
        merge = float(price.get("merges", {}).get(0, [0.0, 0.0])[0])
        return read, merge

    def freeze(self) -> None:
        """The scaling, the reward's centre and spread, and the merge prices
        used from here to the next push."""
        self.mean, self.scale = frozen_scaling(self.x_stats)
        if self.j_stats.n:
            self.jbar, self.spread = reward_scale(self.j_stats)
        pooled = [sum(v[0] for v in self.merge.values()),
                  sum(v[1] for v in self.merge.values())]
        fallback = pooled[0] / pooled[1] if pooled[1] > 0 else 0.0
        n_levels = len(self.replay[-1].d1) if self.replay else len(self.p)
        self.p = [(self.merge[i][0] / self.merge[i][1])
                  if i in self.merge and self.merge[i][1] > 0 else fallback
                  for i in range(n_levels)]

    # ------------------------------------------------------------- tensors
    def tensors(self, xs: list) -> torch.Tensor:
        x = torch.tensor(xs, dtype=torch.float64)
        mean = torch.tensor(self.mean, dtype=torch.float64)
        scale = torch.tensor(self.scale, dtype=torch.float64)
        return TM.scale_inputs(x, mean, scale, float(self.s["clip"]))

    @staticmethod
    def masks(ms: list) -> torch.Tensor:
        return torch.tensor(ms, dtype=torch.bool)

    def prior_q(self, ps: list) -> torch.Tensor:
        """The L0 prior in the reward's units [B, acting, 4]: the plugin's
        values (J per turn) over the frozen spread, as the plugin divides by
        its weights' spread; 0 at every other level. Q = net - prior: the net
        learns the correction (zeros when the prior was off)."""
        out = torch.zeros(len(ps), self.acting, TM.N_ACTIONS, dtype=torch.float64)
        out[:, 0, :] = torch.tensor(ps, dtype=torch.float64) / self.spread
        return out

    def bootstrap(self, xs: torch.Tensor, ms: torch.Tensor,
                  pq: torch.Tensor | None = None) -> torch.Tensor:
        """Q_target(s', a*) with a* the online net's greedy allowed choice,
        both less the L0 prior at s' (pq)."""
        with torch.no_grad():
            a_star = TM.greedy(self.model, xs, ms, pq)
            return TM.joint_prior(self.target, xs, a_star, ms, pq)

    def td_error(self, tr: Transition) -> float:
        x0, x1 = self.tensors([tr.x0]), self.tensors([tr.x1])
        with torch.no_grad():
            q = TM.joint_prior(self.model, x0, torch.tensor([tr.a]), self.masks([tr.m0]),
                               self.prior_q([tr.p0]))
        y = self.reward(tr) + self.gamma * float(
            self.bootstrap(x1, self.masks([tr.m1]), self.prior_q([tr.p1]))[0])
        return y - float(q[0])

    # ------------------------------------------------------------- learning
    def eligible(self, final: bool) -> list:
        """Replay indices with n_step contiguous transitions ahead (fewer at
        the run's end), and how many."""
        n_step = int(self.s["n_step"])
        out = []
        for i in range(len(self.replay)):
            n = 1
            while (n < n_step and i + n < len(self.replay) and
                   self.replay[i + n].t == self.replay[i].t + n):
                n += 1
            if n == n_step or final or i + n < len(self.replay):
                out.append((i, n))
        return out

    def train_step(self, eligible: list) -> dict:
        batch = [eligible[self.rng.randrange(len(eligible))]
                 for _ in range(int(self.s["batch"]))]
        g = self.gamma
        returns, boot_x, boot_m, boot_p, gammas = [], [], [], [], []
        for i, n in batch:
            returns.append(sum(g ** k * self.reward(self.replay[i + k]) for k in range(n)))
            last = self.replay[i + n - 1]
            boot_x.append(last.x1)
            boot_m.append(last.m1)
            boot_p.append(last.p1)
            gammas.append(g ** n)
        first = [self.replay[i] for i, _ in batch]
        x0 = self.tensors([tr.x0 for tr in first])
        a = torch.tensor([tr.a for tr in first], dtype=torch.long)
        m0 = self.masks([tr.m0 for tr in first])
        y = (torch.tensor(returns, dtype=torch.float64) +
             torch.tensor(gammas, dtype=torch.float64) *
             self.bootstrap(self.tensors(boot_x), self.masks(boot_m), self.prior_q(boot_p)))
        q = TM.joint_prior(self.model, x0, a, m0, self.prior_q([tr.p0 for tr in first]))
        loss = torch.nn.functional.huber_loss(q, y, delta=float(self.s["huber"]))
        self.opt.zero_grad()
        loss.backward()
        norm = torch.nn.utils.clip_grad_norm_(self.model.parameters(),
                                              float(self.s["grad_clip"]))
        self.opt.step()
        tau = float(self.s["tau"])
        with torch.no_grad():
            for pt, po in zip(self.target.parameters(), self.model.parameters()):
                pt.mul_(1 - tau).add_(po, alpha=tau)
        self.steps += 1
        return {"loss": loss.item(), "q": q.detach().mean().item(), "y": y.mean().item(),
                "grad_norm": float(norm)}

    def push(self) -> None:
        self.version += 1
        weights = TM.export(self.model, self.version, self.mean, self.scale,
                            float(self.s["clip"]), spread=self.spread)
        TW.write_atomic(weights, self.args.weights)
        # Every version, for the check of the plugin's scores (37's ARCH-6).
        archive = getattr(self.args, "archive", None)
        if archive:
            Path(archive).mkdir(parents=True, exist_ok=True)
            TW.write_atomic(weights, str(Path(archive) / f"turn.v{self.version}.bin"))
        self.emit({"type": "push", "version": self.version, "transitions": len(self.replay),
                   "jbar": self.jbar, "spread": self.spread, "p": self.p})

    def learn(self, prev: Turn, line: Turn, price: dict) -> None:
        bill = self.l0_bill_parts(line, price) if self.l0_bill else None
        J = sum(bill) if bill is not None else self.charged_j(line, price)
        tr = Transition(t=line.t, x0=prev.x, m0=prev.masks, a=prev.actions,
                        x1=line.x, m1=line.masks, d0=prev.owed, d1=line.owed,
                        J=J, changes=prev.set + line.mid_applies,
                        p0=prev.prior, p1=line.prior)
        self.priced += 1
        if len(tr.a) != self.acting:     # the earlier line chose nothing
            return
        td = self.td_error(tr)
        record = {"type": "turn", "t": line.t, "op0": line.op0, "op1": line.op1,
                  "ops": price["ops"], "secs": price["secs"], "J": price["J"], "J_reward": J,
                  "C_W": price["C_W"], "C_R": price["C_R"], "C_S": price["C_S"],
                  "jobs": price["jobs"], "changes": tr.changes,
                  "phi0": self.phi(tr.d0), "phi1": self.phi(tr.d1),
                  "r": self.reward(tr), "td": td, "actions": tr.a,
                  "explored": prev.explored, "eps": prev.eps, "final": int(line.final)}
        if bill is not None:
            record.update(bill_read=bill[0], bill_merge=bill[1])
        self.emit(record)
        if math.isfinite(td):
            self.preq.append(td)
        self.replay.append(tr)
        self.observe(tr.x1)
        self.j_stats.add([-tr.J])
        if len(self.replay) % int(self.s["push_every_turns"]) == 0:
            self.freeze()
            self.push()
        if len(self.replay) >= int(self.s["min_replay"]):
            eligible = self.eligible(final=False)
            if eligible:
                for _ in range(int(self.s["steps_per_turn"])):
                    r = self.train_step(eligible)
                self.emit({"type": "train", "t": line.t, "n": len(self.replay),
                           "steps": self.steps, **r})

    def step(self) -> None:
        self.ingest()
        for prev, line, price in self.take_pending():
            self.learn(prev, line, price)

    def done(self) -> bool:
        return self.stop_seen and not self.pending

    def finish(self, why: str) -> int:
        recent = self.preq[-50:]
        rms = math.sqrt(sum(e * e for e in recent) / len(recent)) if recent else math.nan
        self.emit({"type": "stop", "why": why, "lines": self.lines, "priced": self.priced,
                   "dropped": self.dropped, "transitions": len(self.replay),
                   "steps": self.steps, "version": self.version})
        summary = {"arch": self.arch, "lines": self.lines, "priced": self.priced,
                   "dropped": self.dropped, "transitions": len(self.replay),
                   "steps": self.steps, "weights_version": self.version,
                   "preq_rms_last50": rms, "why": why}
        Path(self.args.summary).write_text(json.dumps(summary, indent=1) + "\n")
        self.log.close()
        return 0


def load_settings(path: str) -> dict:
    raw = json.loads(Path(path).read_text())
    s = raw.get("trainer", raw)
    missing = [k for k in SETTINGS_KEYS if k not in s]
    if missing:
        raise ValueError(f"turn trainer settings lack {missing}")
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for name in ("plugin-config", "decisions", "host-log", "weights", "settings",
                 "log", "summary"):
        ap.add_argument(f"--{name}", required=True)
    ap.add_argument("--archive", help="a folder for every pushed version")
    ap.add_argument("--poll-s", type=float, default=0.25)
    ap.add_argument("--max-idle-s", type=float, default=600.0,
                    help="give up after this long with no new line and no stop")
    args = ap.parse_args(argv)
    settings = load_settings(args.settings)
    torch.set_num_threads(int(settings["torch_threads"]))
    plugin_cfg = json.loads(Path(args.plugin_config).read_text())
    if plugin_cfg.get("mode") != "turn":
        raise SystemExit("turn_trainer needs a turn plugin config")
    tr = TurnTrainer(args, settings, plugin_cfg)
    stopping = {"why": None}

    def stop(signum, frame):  # noqa: ARG001
        stopping["why"] = f"signal {signum}"

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    idle_since = time.monotonic()
    while stopping["why"] is None:
        before = (tr.decisions.offset, tr.host.offset)
        tr.step()
        if tr.done():
            return tr.finish("plugin stop")
        if (tr.decisions.offset, tr.host.offset) != before:
            idle_since = time.monotonic()
        elif time.monotonic() - idle_since > args.max_idle_s:
            return tr.finish("idle")
        time.sleep(args.poll_s)
    return tr.finish(stopping["why"])


if __name__ == "__main__":
    sys.exit(main())
