#!/usr/bin/env python3
"""Independent learners with one bill per level, for a turn run whose plugin
has the level clock (controller/config.h turn_periods; the plan is
docs/INDEPENDENT_LEARNERS_PLAN.md sections 4 and 5).

Each acting level has its own network (the VDN arm's), its own replay, its
own reward centre and spread, its own optimiser and target: a level's loss
moves only its own network.

* L0 (period 1) learns once per turn from L0's own bill, as turn_l0_bill
  does: beta_r times L0's read counts of the turn at cost model 2's base
  prices plus every L0 merge that finished in the turn, whole. The counts
  come from the turn line's "level_reads" block (level_reads 1), so
  turn_l0_bill stays 0.
* A slow level j (period above 1) learns once per hold: the state and its
  action at a decision line d, the bill over the turns d+1 .. d' of the hold
  (d' its next decision line), the state at d'. The bill is the plan's
  three-part bill with the merge parts booked as owed work, bytes counted
  when written (sf_bill):
      B_j = A_{j-1} + A_j + R_j + H_j + H_{j+1}
  Its return runs over slow_n_step holds (1, or 2 to judge a move over two
  holds), discounted by gamma^period per hold.

Trainer settings (optional): slow_n_step 1, slow_batch 16,
slow_steps_per_turn 1, slow_min_replay 4.

Telemetry lines in --log, beside the turn trainer's: "hold" (one slow level's
priced hold, with the bill's parts) and "train_level" (one slow level's
gradient step)."""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

import torch

from learner import learned2 as L2
from learner import turn_ledger as TL
from learner.prices import l0_read_cost
from learner.turn_trainer import Running, Transition, TurnTrainer, reward_scale

cm = L2.cm


# --------------------------------------------------------------- the rows
@dataclass
class Row:
    """One turn's ledger and byte flows; lists are per level."""
    F: float        # bytes the flushes wrote
    P: float        # keys written (puts)
    s_out: list     # bytes that left level k downward
    g_in: list      # bytes dropped by the jobs whose output level is k
    M: list         # merges from level k that finished, as paid (betas applied)
    R: list         # reads at level k
    H: list         # hidden scan steps at level k


def window_row(pricer: "L2.HostLogPricer", lo: int, hi: int, block: dict) -> Row:
    """The row of the window whose host-log records are (lo, hi)
    (HostLogPricer.bounds) and whose read counters are `block`."""
    R, H = TL.read_cells(pricer.p, block)
    n = len(R)
    M = TL.merge_cells(pricer, lo, hi, n)
    jobs, _, _ = TL._job_windows(pricer, lo, hi)
    i0 = bisect.bisect_right(pricer.job_idx, lo)
    F, s_out, g_in = 0.0, [0.0] * n, [0.0] * n
    for i, job in enumerate(jobs):
        rec = pricer.recs[pricer.job_idx[i0 + i]]
        if job.kind == "flush":
            F += float(rec["x"])
            continue
        a, b = int(rec["start_level"]), int(rec["output_level"])
        trivial = bool(rec["trivial"])
        s = float(rec["s"])
        o = 0.0 if trivial else float(rec["o"])
        x = s if trivial else float(rec["x"])
        for k in range(a, min(b, n)):
            s_out[k] += s
        if 0 <= b < n:
            g_in[b] += s + o - x
    counts = lambda i: cm._counts(pricer.recs[i], pricer.names)["put"]  # noqa: E731
    return Row(F=F, P=float(counts(hi) - counts(lo)), s_out=s_out, g_in=g_in, M=M, R=R, H=H)


# --------------------------------------------------------------- the bill
def _sum(rows: dict, a: int, b: int, value) -> float:
    return sum(value(rows[t]) for t in range(a, b) if t in rows)


def sf_cell(rows: dict, k: int, a: int, b: int) -> float:
    """The owed cell of merge step k (L_k to L_k+1) over the turns [a, b),
    bytes counted when written. `rows` maps a turn to its Row; every turn of
    [a, b) must be there (the look-backs use the turns that are)."""
    n_levels = len(rows[a].M)
    if not 0 <= k < n_levels:
        return 0.0
    n = b - a
    first = min(rows)
    t0 = max(first, b - 4 * n)
    puts = _sum(rows, t0, b, lambda r: r.P)
    written = (_sum(rows, a, b, lambda r: r.P) * _sum(rows, t0, b, lambda r: r.F) / puts
               if puts > 0 else 0.0)
    owed = written - _sum(rows, a, b, lambda r: sum(r.g_in[:k + 1]))
    moved = _sum(rows, a, b, lambda r: r.s_out[k])
    paid = _sum(rows, a, b, lambda r: r.M[k])
    if moved >= owed:
        return paid * max(owed, 0.0) / moved if moved > 0 else 0.0
    hi, price = a, 0.0
    while hi > first:
        lo = max(first, hi - n)
        before = _sum(rows, lo, hi, lambda r: r.s_out[k])
        if before > 0:
            price = _sum(rows, lo, hi, lambda r: r.M[k]) / before
            break
        hi = lo
    return paid + price * (owed - moved)


def sf_bill(rows: dict, j: int, a: int, b: int) -> dict | None:
    """Level j's bill over the turns [a, b) and its parts; None when a turn
    of the span was not priced."""
    if b <= a or any(t not in rows for t in range(a, b)):
        return None
    n_levels = len(rows[a].M)
    cell = lambda v, k: _sum(rows, a, b, lambda r: v(r)[k]) if 0 <= k < n_levels else 0.0  # noqa: E731
    parts = {"A_in": sf_cell(rows, j - 1, a, b), "A_out": sf_cell(rows, j, a, b),
             "R": cell(lambda r: r.R, j), "H": cell(lambda r: r.H, j),
             "H_below": cell(lambda r: r.H, j + 1)}
    parts["bill"] = sum(parts.values())
    return parts


# --------------------------------------------------------------- the trainer
@dataclass
class Hold:
    """One slow level's macro transition."""
    t0: int          # the decision line
    t1: int          # the next decision line; the hold is the turns t0+1 .. t1
    x0: list
    m0: list         # the level's own mask at t0
    a: int
    x1: list
    m1: list
    bill: float


def parse_periods(value) -> list:
    return [int(v) for v in str(value).split(":")]


class LevelBillsTrainer(TurnTrainer):
    def __init__(self, args, settings: dict, plugin_cfg: dict):
        super().__init__(args, settings, plugin_cfg)
        self.periods = parse_periods(plugin_cfg["turn_periods"])
        problem = None
        if self.arch != "vdn":
            problem = "the level bills need turn_arch vdn (one network per level)"
        elif self.l0_bill:
            problem = "the level bills build L0's bill themselves: turn_l0_bill must be 0"
        elif int(plugin_cfg.get("level_reads", 0)) != 1:
            problem = "the level bills need level_reads 1 (every level's read counts)"
        elif self.merge_charge != "done":
            problem = "the level bills charge each merge as it finishes: merge_charge done"
        elif len(self.periods) != self.acting or self.periods[0] != 1:
            problem = (f"turn_periods {self.periods} does not fit {self.acting} acting "
                       "levels with L0's period 1")
        if problem:
            self.log.close()
            raise ValueError(problem)
        self.shaping = False
        self.slow_n = int(settings.get("slow_n_step", 1))
        self.slow_batch = int(settings.get("slow_batch", 16))
        self.slow_steps = int(settings.get("slow_steps_per_turn", 1))
        self.slow_min = int(settings.get("slow_min_replay", 4))
        if min(self.slow_n, self.slow_batch, self.slow_min) < 1 or self.slow_steps < 0:
            self.log.close()
            raise ValueError("slow_n_step, slow_batch, slow_min_replay >= 1; slow_steps_per_turn >= 0")
        lr = float(settings["lr"])
        self.opts = [torch.optim.Adam(net.parameters(), lr=lr) for net in self.model.nets]
        self.rows = {}                                     # turn -> Row
        self.extra = {}                                    # turn -> (level_reads, decided)
        self.open = [None] * self.acting                   # a slow level's open decision line
        self.holds = [[] for _ in range(self.acting)]      # each slow level's replay
        self.b_stats = [Running(1) for _ in range(self.acting)]
        self.centre = [0.0] * self.acting
        self.lspread = [1.0] * self.acting
        self.level_steps = [0] * self.acting

    def on_turn_record(self, record: dict) -> None:
        reads, decided = record.get("level_reads"), record.get("decided")
        self.extra[int(record["t"])] = (reads if isinstance(reads, dict) else None,
                                        [bool(v) for v in decided]
                                        if isinstance(decided, list) else None)

    # ------------------------------------------------------------- scaling
    def freeze(self) -> None:
        super().freeze()
        for j in range(1, self.acting):
            if self.b_stats[j].n:
                self.centre[j], self.lspread[j] = reward_scale(self.b_stats[j])

    def hold_reward(self, j: int, h: Hold) -> float:
        return (-h.bill - self.centre[j]) / self.lspread[j]

    # ------------------------------------------------------------- one level's Q
    def level_q(self, net, level: int, xs: torch.Tensor) -> torch.Tensor:
        return net.scores(xs)[:, level, :]

    def level_target(self, level: int, xs: torch.Tensor, masks: torch.Tensor) -> torch.Tensor:
        """Q_target(s', a*) of one level, a* the online net's best allowed action."""
        with torch.no_grad():
            online = self.level_q(self.model, level, xs).masked_fill(~masks, float("-inf"))
            a_star = online.argmax(-1, keepdim=True)
            return self.level_q(self.target, level, xs).gather(-1, a_star).squeeze(-1)

    def level_step(self, level: int, x0, a, y) -> dict:
        """One gradient step on one level's network toward the targets y."""
        q = self.level_q(self.model, level, x0).gather(-1, a.unsqueeze(-1)).squeeze(-1)
        loss = torch.nn.functional.huber_loss(q, y, delta=float(self.s["huber"]))
        self.opts[level].zero_grad()
        loss.backward()
        norm = torch.nn.utils.clip_grad_norm_(self.model.nets[level].parameters(),
                                              float(self.s["grad_clip"]))
        self.opts[level].step()
        tau = float(self.s["tau"])
        with torch.no_grad():
            for pt, po in zip(self.target.nets[level].parameters(),
                              self.model.nets[level].parameters()):
                pt.mul_(1 - tau).add_(po, alpha=tau)
        self.level_steps[level] += 1
        return {"loss": loss.item(), "q": q.detach().mean().item(), "y": y.mean().item(),
                "grad_norm": float(norm)}

    # ------------------------------------------------------------- L0
    def l0_td(self, tr: Transition) -> float:
        x0, x1 = self.tensors([tr.x0]), self.tensors([tr.x1])
        with torch.no_grad():
            q = float(self.level_q(self.model, 0, x0)[0, tr.a[0]])
        boot = float(self.level_target(0, x1, self.masks([tr.m1[0]]))[0])
        return self.reward(tr) + self.gamma * boot - q

    def train_step(self, eligible: list) -> dict:
        """L0's step: n-step double DQN on its own network and its own bill."""
        batch = [eligible[self.rng.randrange(len(eligible))] for _ in range(int(self.s["batch"]))]
        g = self.gamma
        returns = [sum(g ** k * self.reward(self.replay[i + k]) for k in range(n)) for i, n in batch]
        last = [self.replay[i + n - 1] for i, n in batch]
        first = [self.replay[i] for i, _ in batch]
        y = (torch.tensor(returns, dtype=torch.float64) +
             torch.tensor([g ** n for _, n in batch], dtype=torch.float64) *
             self.level_target(0, self.tensors([tr.x1 for tr in last]),
                               self.masks([tr.m1[0] for tr in last])))
        r = self.level_step(0, self.tensors([tr.x0 for tr in first]),
                            torch.tensor([tr.a[0] for tr in first], dtype=torch.long), y)
        self.steps += 1
        return r

    # ------------------------------------------------------------- slow levels
    def slow_eligible(self, j: int) -> list:
        """Indices of level j's holds with slow_n_step contiguous holds from there."""
        hs, n = self.holds[j], self.slow_n
        return [i for i in range(len(hs) - n + 1)
                if all(hs[i + k].t0 == hs[i + k - 1].t1 for k in range(1, n))]

    def slow_step(self, j: int, eligible: list) -> dict:
        hs, n = self.holds[j], self.slow_n
        g = self.gamma ** self.periods[j]
        batch = [eligible[self.rng.randrange(len(eligible))] for _ in range(self.slow_batch)]
        returns = [sum(g ** k * self.hold_reward(j, hs[i + k]) for k in range(n)) for i in batch]
        last = [hs[i + n - 1] for i in batch]
        first = [hs[i] for i in batch]
        y = (torch.tensor(returns, dtype=torch.float64) + g ** n *
             self.level_target(j, self.tensors([h.x1 for h in last]),
                               self.masks([h.m1 for h in last])))
        return self.level_step(j, self.tensors([h.x0 for h in first]),
                               torch.tensor([h.a for h in first], dtype=torch.long), y)

    def close_holds(self, line, decided: list) -> None:
        """At a slow level's decision line: its open hold is priced and
        stored, and the line opens the next one."""
        for j in range(1, self.acting):
            if self.periods[j] <= 1 or not decided[j]:
                continue
            d = self.open[j]
            if d is not None:
                parts = sf_bill(self.rows, j, d.t + 1, line.t + 1)
                if parts is not None and math.isfinite(parts["bill"]):
                    h = Hold(t0=d.t, t1=line.t, x0=d.x, m0=d.masks[j], a=d.actions[j],
                             x1=line.x, m1=line.masks[j], bill=parts["bill"])
                    self.emit({"type": "hold", "level": j, "t0": h.t0, "t1": h.t1, "a": h.a,
                               "explored": d.explored, "r": self.hold_reward(j, h),
                               "n": len(self.holds[j]) + 1, **parts})
                    self.holds[j].append(h)
                    self.b_stats[j].add([-h.bill])
            self.open[j] = line if len(line.actions) == self.acting else None

    # ------------------------------------------------------------- one turn
    def learn(self, prev, line, price: dict) -> None:
        reads, decided = self.extra.pop(line.t, (None, None))
        if reads is None or decided is None:
            raise SystemExit(f"turn {line.t} lacks level_reads or decided: the level bills "
                             "need level_reads 1 and turn_periods")
        b = self.pricer.bounds(line.op0, line.op1)
        row = window_row(self.pricer, b[0], b[1], reads)
        self.rows[line.t] = row
        self.priced += 1
        self.close_holds(line, decided)
        for j in range(1, self.acting):
            eligible = self.slow_eligible(j) if len(self.holds[j]) >= self.slow_min else []
            if eligible and self.slow_steps > 0:
                for _ in range(self.slow_steps):
                    r = self.slow_step(j, eligible)
                self.emit({"type": "train_level", "level": j, "t": line.t,
                           "n": len(self.holds[j]), "steps": self.level_steps[j], **r})
        if len(prev.actions) != self.acting:     # the earlier line chose nothing
            return
        p = self.pricer.p
        read = p.beta_r * l0_read_cost(p.v2.base, TL.l0_counts(reads))
        tr = Transition(t=line.t, x0=prev.x, m0=prev.masks, a=prev.actions, x1=line.x,
                        m1=line.masks, d0=prev.owed, d1=line.owed, J=read + row.M[0],
                        changes=prev.set + line.mid_applies)
        td = self.l0_td(tr)
        self.emit({"type": "turn", "t": line.t, "op0": line.op0, "op1": line.op1,
                   "ops": price["ops"], "secs": price["secs"], "J": price["J"],
                   "J_reward": tr.J, "C_W": price["C_W"], "C_R": price["C_R"],
                   "C_S": price["C_S"], "jobs": price["jobs"], "changes": tr.changes,
                   "phi0": 0.0, "phi1": 0.0, "r": self.reward(tr), "td": td,
                   "actions": tr.a, "explored": prev.explored, "eps": prev.eps,
                   "final": int(line.final), "l0_bill_read": read, "l0_bill_merge": row.M[0]})
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

    def finish(self, why: str) -> int:
        self.emit({"type": "levels", "periods": self.periods, "slow_n_step": self.slow_n,
                   "holds": [len(h) for h in self.holds], "steps": self.level_steps,
                   "centre": self.centre, "spread": self.lspread})
        return super().finish(why)
