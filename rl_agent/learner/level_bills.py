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
gradient step).

With the trainer settings' level_bill2 1 (absent or 0: everything above, as
before) the bills change:
* L0 learns from bill N, as turn_trainer.py's l0_bill_z 1 with
  l0_bill_z_l3 0 builds it (turn_l0_bill 1 there): L0's reads (its counts
  from the turn line's "level_reads" block, the very counts the plugin's
  "l0_read" would carry) and the merges out of L0, the merges out of L1-L3
  as paid, and the change of the debt ledgers E_1, E_2 (debt_bill.py, L0's
  bytes carried, no L3 ledger). The plugin config keeps turn_l0_bill 0 (the
  block would repeat level_reads' L0 entries; the plugin logs it only).
  The "turn" lines carry bill N's fields as turn_trainer writes them
  (bill_read, bill_merge, bill_deep, bill_deep_levels, bill_debt,
  bill_debt_levels, debt, tree, debt_l3 0) and "l0_bill": "N".
* Each slow level j (period above 1; j >= 2) is paid design 2's bill
  (level_bill2.py), summed over the turns of its hold; level_bill2_variant
  picks which parts ("f", the default: R + M + dD + F; "d" R + M; "e"
  R + M + dD; "g" f with L_j's reads priced at target). The targets in force
  during a turn are the line's "C" times the "applied_m" of the line that
  opened the turn. Every priced turn adds a "bill2_tree" line (the rebuilt
  sizes, the targets, the merges' flows and the prices of the day) and one
  "bill2" line per slow level (every part, the ledgers' amounts and
  balances, the re-base at a target change, the fill charge); "hold" lines
  carry the hold's summed parts R, Rg, M, dD, F and its bill.

With the trainer settings' level_bill3 1 (absent or 0: everything above, as
before; not with level_bill2 1) L0 is paid bill N as with level_bill2 1, and
every level j >= 1 whose period is 1 learns: it decides every turn as L0
does and learns from one example per turn (a "hold" of one turn: the state
and its action at a line, the turn's bill, the state at the next line),
paid design 3 repaired (level_bill3.py, Bill3r: R + M + dA1 + dA2 + dA4, the
copies formula, Y = 32). Levels with period 0 never decide. A level with a
period above 1 (2026-11-21, batch lb8) decides at its clock turns and learns
once per hold as a slow level does: Bill3r is still computed every turn, the
hold is paid its sum over the hold's turns, and the return runs over
slow_n_step holds discounted by gamma^period. The decided size C_j* of the turn a line closes is the line's
"C" times its "rung_m" (the rung chosen at the line before or earlier); the
targets in force are design 2's. Every priced turn adds a "bill3_tree" line
(the rebuilt sizes, the targets, the decided sizes, the merges' flows, the
reads, the hidden-step means and the price vectors mixed, merge, push, the
"full" flags and n) and one "bill3" line per learning level (every part,
account, price and input of the bill). With level_bill3_out_floor 1 (only
with level_bill3 1; absent or 0: as before) the read account's spread term
divides by max(out, f) instead of out (level_bill3.py, batch lb4). With
level_bill3_a4 "st" (only with level_bill3 1, not with the floor; absent or
"copies": as before) the read account is the steal / trap form
(level_bill3.py ST_A, batch lb5); it reads each turn's filter hits and seeks.
With level_bill3_a4 "none" (only with level_bill3 1, not with the floor) there is no read account: A4 is 0 on
every turn and the hidden scan steps are paid as they arrive, in R (2026-11-21).
Three more settings of level_bill3 1 (level_bill3.py, after lb5's cause study; each absent: as before):
level_bill3_st_trap w (a number >= 0, only with level_bill3_a4 "st": the trap part's weight, 0 = no credit for a
raise), level_bill3_owed "capped" (the ledgers and the room never count a size above a level's normal x1 target, the
line's "C"; "decided" or absent: as before), level_bill3_probe 1 (only with level_bill3_a4 "st": the probe account A5
at the plugin config's price of one filter probe, beta_r c_f; 0 or absent: none) and level_bill3_a3 1 (the merge account A3; 0 or absent: none). With any of
them the bill3 lines carry the option's keys and bill3_tree the normal targets "Cn".
level_bill3_pay (only with level_bill3 1; 2026-11-21, batch lb9) picks what a level's hold is paid, the reward's bill;
the bill3 lines and every account are computed and logged as before:
  "bill"  (or absent) Bill3r, as before.
  "plain" the level's own R + M: the reads and the merges of its own scope as they were paid, no account.
  "wide"  L1's R + M for every level (the reads of L1 .. L_n and the merges out of L0 .. L_(n-1)): every deep level
          is paid the same money. L1 must be a learning level.
With "plain" or "wide" a "hold" line carries "pay", its "bill" is the paid amount and "bill3r" the Bill3r sum."""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

import torch

from learner import debt_bill as DB
from learner import learned2 as L2
from learner import level_bill2 as LB2
from learner import level_bill3 as LB3
from learner import turn_ledger as TL
from learner.prices import l0_read_cost
from learner.turn_trainer import Running, Transition, TurnTrainer, reward_scale

cm = L2.cm
PAY_FORMS = ("bill", "plain", "wide")      # level_bill3_pay: what a level's hold is paid


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


def level_flows(pricer: "L2.HostLogPricer", lo: int, hi: int, merges: dict, n: int) -> tuple:
    """(paid, moved, fed) per start level 0 .. n-1 of the turn whose host-log
    records lie strictly between lo and hi: the merges' whole price and the
    bytes fed into them (merge_prices' map `merges`: source + overlap, moves
    left out), and the bytes every job that ended took out of its start level
    (moves without rewriting included; written_bill.turn_row's rule, over
    every level)."""
    paid = [float(merges.get(i, [0.0, 0.0])[0]) for i in range(n)]
    fed = [float(merges.get(i, [0.0, 0.0])[1]) for i in range(n)]
    moved = [0.0] * n
    i0 = bisect.bisect_right(pricer.job_idx, lo)
    i1 = bisect.bisect_left(pricer.job_idx, hi)
    for i in pricer.job_idx[i0:i1]:
        r = pricer.recs[i]
        if r.get("type") != "job_end":
            continue
        a = int(r["start_level"])
        b = int(r.get("output_level", a + 1))
        if b <= a or not 0 <= a < n:
            continue
        moved[a] += float(r["s"])
    return paid, moved, fed


def merge_sources(pricer: "L2.HostLogPricer", lo: int, hi: int, n: int) -> list:
    """Per start level 0 .. n-1: the source bytes of the merges that end in
    the window (merge_prices' job set: trivial moves and flushes left out),
    the denominator of Bill3r's merge price."""
    out = [0.0] * n
    i0 = bisect.bisect_right(pricer.job_idx, lo)
    i1 = bisect.bisect_left(pricer.job_idx, hi)
    for job in pricer.jobs[i0:i1]:
        if job.kind in ("l0", "deep") and 0 <= int(job.level) < n:
            out[int(job.level)] += float(job.s)
    return out


def targets(C: list, m: list) -> list:
    """The targets in force: the line's "C" (L0's 0) times the multipliers."""
    return [float(c) * (float(m[i]) if i >= 1 and i < len(m) else 1.0) for i, c in enumerate(C)]


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
        # Design 2 (level_bill2 1): L0 on bill N, the slow levels on design 2.
        self.b2 = None
        on = settings.get("level_bill2", 0)
        variant = settings.get("level_bill2_variant")
        problem = None
        if type(on) is not int or on not in (0, 1):
            problem = f"level_bill2 is 0 or 1, not {on!r}"
        elif variant is not None and on != 1:
            problem = "level_bill2_variant picks design 2's parts: it needs level_bill2 1"
        elif on == 1 and (variant or LB2.DEFAULT_VARIANT) not in LB2.VARIANTS:
            problem = f"level_bill2_variant is d, e, f or g, not {variant!r}"
        elif on == 1:
            slow = [j for j in range(1, self.acting) if self.periods[j] > 1]
            if any(j < 2 for j in slow):
                problem = "design 2 bills levels 2 and deeper: L1 cannot be a slow level with level_bill2 1"
            elif not slow:
                problem = "level_bill2 1 needs a slow level (a period above 1)"
        if problem:
            self.log.close()
            raise ValueError(problem)
        if on == 1:
            self.b2_variant = variant or LB2.DEFAULT_VARIANT
            self.b2 = LB2.Bill2Run(slow, self.b2_variant)
            self.b2_turns = {j: {} for j in slow}           # level -> turn -> parts
            self.applied = {}                               # turn -> the line's applied_m
            # Bill N for L0: bill Z's ledgers without the L3 ledger, L0's bytes carried.
            self.debt = DB.DebtLedger(carry_l0=True, l3=False)
        # Design 3 repaired (level_bill3 1): L0 on bill N, every level j >= 1 with period 1 on Bill3r.
        self.b3 = None
        on3 = settings.get("level_bill3", 0)
        floor3 = settings.get("level_bill3_out_floor", 0)
        a4 = settings.get("level_bill3_a4", "copies")
        owed3 = settings.get("level_bill3_owed", "decided")
        a3 = settings.get("level_bill3_a3", 0)
        trap3 = settings.get("level_bill3_st_trap")
        probe3 = settings.get("level_bill3_probe", 0)
        pay3 = settings.get("level_bill3_pay", "bill")
        problem = None
        if type(on3) is not int or on3 not in (0, 1):
            problem = f"level_bill3 is 0 or 1, not {on3!r}"
        elif type(floor3) is not int or floor3 not in (0, 1):
            problem = f"level_bill3_out_floor is 0 or 1, not {floor3!r}"
        elif floor3 == 1 and on3 != 1:
            problem = "level_bill3_out_floor 1 is a setting of level_bill3 1"
        elif not isinstance(a4, str) or a4 not in LB3.A4_FORMS:
            problem = f"level_bill3_a4 is one of {LB3.A4_FORMS}, not {a4!r}"
        elif a4 != "copies" and on3 != 1:
            problem = f"level_bill3_a4 {a4!r} is a setting of level_bill3 1"
        elif a4 != "copies" and floor3 == 1:
            problem = f"level_bill3_a4 {a4!r} has no spread half: level_bill3_out_floor 1 does not apply"
        elif not isinstance(owed3, str) or owed3 not in LB3.OWED_FORMS:
            problem = f"level_bill3_owed is one of {LB3.OWED_FORMS}, not {owed3!r}"
        elif type(a3) is not int or a3 not in (0, 1):
            problem = f"level_bill3_a3 is 0 or 1, not {a3!r}"
        elif type(probe3) is not int or probe3 not in (0, 1):
            problem = f"level_bill3_probe is 0 or 1, not {probe3!r}"
        elif probe3 == 1 and (on3 != 1 or a4 != "st"):
            problem = "level_bill3_probe 1 is a setting of level_bill3 1 with level_bill3_a4 'st'"
        elif (owed3 != "decided" or a3 == 1 or trap3 is not None) and on3 != 1:
            problem = "level_bill3_owed, level_bill3_a3 and level_bill3_st_trap are settings of level_bill3 1"
        elif trap3 is not None and (isinstance(trap3, bool) or not isinstance(trap3, (int, float))
                                    or not math.isfinite(trap3) or trap3 < 0 or a4 != "st"):
            problem = f"level_bill3_st_trap is a number >= 0 with level_bill3_a4 'st', not {trap3!r}"
        elif not isinstance(pay3, str) or pay3 not in PAY_FORMS:
            problem = f"level_bill3_pay is one of {PAY_FORMS}, not {pay3!r}"
        elif pay3 != "bill" and on3 != 1:
            problem = f"level_bill3_pay {pay3!r} is a setting of level_bill3 1"
        elif on3 == 1 and self.b2 is not None:
            problem = "level_bill2 1 and level_bill3 1 are two bills for the same levels: pick one"
        elif on3 == 1:
            learning = [j for j in range(1, self.acting) if self.periods[j] >= 1]
            if not learning:
                problem = "level_bill3 1 needs a level j >= 1 that decides (a period of 1 or more)"
            elif pay3 == "wide" and 1 not in learning:
                problem = "level_bill3_pay 'wide' pays L1's reads and merges: L1 must be a learning level"
        if problem:
            self.log.close()
            raise ValueError(problem)
        if on3 == 1:
            self.b3 = LB3.Bill3Run(learning, out_floor=floor3 == 1, a4_form=a4, owed=owed3, a3=a3 == 1,
                                   st_trap=None if trap3 is None else float(trap3),
                                   probe_price=(self.pricer.p.beta_r * self.pricer.p.v2.base["probe"]
                                                if probe3 == 1 else None))
            self.b3_new = owed3 != "decided" or a3 == 1 or trap3 is not None or probe3 == 1
            self.b3_levels = learning
            self.b3_pay = pay3
            self.b3_turns = {j: {} for j in learning}      # level -> turn -> parts
            self.applied = {}                               # turn -> the line's applied_m
            self.rungs = {}                                 # turn -> the line's rung_m
            self.debt = DB.DebtLedger(carry_l0=True, l3=False)

    def learns(self, j: int) -> bool:
        """Level j learns from holds: a slow level, or (level_bill3 1) a level
        j >= 1 that decides every turn (one-turn holds)."""
        return self.periods[j] > 1 or (self.b3 is not None and self.periods[j] == 1)

    def on_turn_record(self, record: dict) -> None:
        reads, decided = record.get("level_reads"), record.get("decided")
        self.extra[int(record["t"])] = (reads if isinstance(reads, dict) else None,
                                        [bool(v) for v in decided]
                                        if isinstance(decided, list) else None)
        if self.b2 is not None:
            m = record.get("applied_m")
            self.applied[int(record["t"])] = ([float(v) for v in m] if isinstance(m, list) else None)
        if self.b3 is not None:
            m, r = record.get("applied_m"), record.get("rung_m")
            self.applied[int(record["t"])] = ([float(v) for v in m] if isinstance(m, list) else None)
            self.rungs[int(record["t"])] = ([float(v) for v in r] if isinstance(r, list) else None)

    # ------------------------------------------------------------- design 2
    def line_targets(self, line) -> list:
        """The targets in force from `line` on: its "C" times its applied_m."""
        m = self.applied.get(line.t)
        if m is None or line.C is None:
            raise SystemExit(f"turn {line.t} lacks applied_m or C: design 2 (level_bill2 1) "
                             "needs the targets in force")
        return targets(line.C, m)

    def start_tree(self, line) -> None:
        super().start_tree(line)
        if self.b2 is not None:
            C = line.C
            if len(C) < 3 or not C[1] > 0:
                raise SystemExit(f"turn {line.t}'s C has no L1 and L2 targets: design 2 needs T")
            self.b2.start(line.B, self.line_targets(line), C[2] / C[1])
        if self.b3 is not None:
            C = line.C
            if len(C) < 3 or not C[1] > 0:
                raise SystemExit(f"turn {line.t}'s C has no L1 and L2 targets: Bill3r needs T")
            self.b3.start(line.B, self.line_targets(line), self.decided_sizes(line), C[2] / C[1],
                          Cn=[float(v) for v in C])

    # ------------------------------------------------------------- design 3 repaired
    def decided_sizes(self, line) -> dict:
        """C_j* of each learning level for the turn `line` closes: the line's
        "C" times its "rung_m" (the rung chosen at the line before or
        earlier; DECAY 0)."""
        r = self.rungs.get(line.t)
        if r is None or line.C is None:
            raise SystemExit(f"turn {line.t} lacks rung_m or C: Bill3r (level_bill3 1) "
                             "needs the decided sizes")
        return {j: float(line.C[j]) * float(r[j]) for j in self.b3_levels}

    def bill3_turn(self, prev, line, price: dict, reads: dict) -> None:
        """Bill3r's parts of the turn `line` closes, for every learning level;
        after debt_change, whose rebuilt sizes are the turn's end."""
        lo, hi = price["bounds"]
        n = len(self.sizes.sizes)
        paid, moved, fed = level_flows(self.pricer, lo, hi, price.get("merges", {}), n)
        mergeout = merge_sources(self.pricer, lo, hi, n)
        R, H = TL.read_cells(self.pricer.p, reads)
        C = self.line_targets(prev)
        cs = self.decided_sizes(line)
        qs = {}
        if self.b3.a4_form == "st":       # the steal / trap read account reads the filter hits and seeks too
            qs = {"q": [float(v) for v in reads["filter_hits"]], "s": [float(v) for v in reads["seeks"]]}
        Cn = [float(v) for v in line.C]
        out, st = self.b3.add(line.t, self.sizes.sizes, C, cs, paid, moved, fed, mergeout, R, H, Cn=Cn, **qs)
        self.emit({"type": "bill3_tree", "t": line.t, "B": list(self.b3.B), "C": C,
                   "m": self.applied[prev.t], "rung_m": self.rungs[line.t],
                   "cstar": {str(j): v for j, v in cs.items()}, "paid": paid, "moved": moved,
                   "fed": fed, "mergeout": mergeout, "R": R, "H": H, "hm": st["hm"],
                   "mixed": st["mixed"], "merge": st["merge"], "push": st["push"],
                   "full": [int(v) for v in st["full"]], "n": st["n"], "w": st["w"],
                   **({"Cn": Cn} if self.b3_new else {})})
        for j, parts in out.items():
            self.b3_turns[j][line.t] = parts
            rec = {"type": "bill3", "t": line.t, "level": j, **parts}
            if not math.isfinite(rec["tm"]):
                rec["tm"] = None                 # the move never ends (f = 0): Yeff = 0
            self.emit(rec)

    def hold_bill3(self, j: int, a: int, b: int) -> dict | None:
        """Level j's Bill3r parts summed over the turns [a, b); None when a
        turn of the span was not priced."""
        turns = self.b3_turns[j]
        if b <= a or any(t not in turns for t in range(a, b)):
            return None
        out = {k: sum(turns[t][k] for t in range(a, b))
               for k in ("R", "M", "dA1", "dA2", "dA4", "rb_sum", "bill") + (("dA3",) if self.b3.a3 else ())
               + (("dA5",) if self.b3.probe_price is not None else ())}
        out["bill3"] = "r"
        if self.b3_pay != "bill":
            # The hold is paid money only: its own scope's R + M ("plain") or L1's ("wide"); no account.
            src = turns if self.b3_pay == "plain" else self.b3_turns[1]
            if any(t not in src for t in range(a, b)):
                return None
            out["bill3r"] = out["bill"]
            out["bill"] = sum(src[t]["R"] + src[t]["M"] for t in range(a, b))
            out["pay"] = self.b3_pay
        return out

    def bill2_turn(self, prev, line, price: dict, reads: dict) -> None:
        """Design 2's parts of the turn `line` closes, for every slow level;
        after debt_change, whose rebuilt sizes are the turn's end."""
        lo, hi = price["bounds"]
        n = len(self.sizes.sizes)
        paid, moved, fed = level_flows(self.pricer, lo, hi, price.get("merges", {}), n)
        R, H = TL.read_cells(self.pricer.p, reads)
        C = self.line_targets(prev)
        out = self.b2.add(line.t, self.sizes.sizes, C, paid, moved, fed, R, H)
        pr = self.b2.prices
        self.emit({"type": "bill2_tree", "t": line.t, "B": list(self.b2.B), "C": C,
                   "m": self.applied[prev.t], "paid": paid, "moved": moved, "fed": fed,
                   "R": R, "H": H, "p": list(self.b2.p), "own": list(pr.own), "w": pr.w})
        for j, parts in out.items():
            self.b2_turns[j][line.t] = parts
            self.emit({"type": "bill2", "t": line.t, "level": j, "variant": self.b2_variant, **parts})

    def hold_bill2(self, j: int, a: int, b: int) -> dict | None:
        """Level j's design-2 parts summed over the turns [a, b); None when a
        turn of the span was not priced."""
        turns = self.b2_turns[j]
        if b <= a or any(t not in turns for t in range(a, b)):
            return None
        out = {k: sum(turns[t][k] for t in range(a, b)) for k in ("R", "Rg", "M", "dD", "F", "bill")}
        out["bill2"] = self.b2_variant
        return out

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
            if not self.learns(j) or not decided[j]:
                continue
            d = self.open[j]
            if d is not None:
                if self.b3 is not None:
                    parts = self.hold_bill3(j, d.t + 1, line.t + 1)
                else:
                    parts = (sf_bill(self.rows, j, d.t + 1, line.t + 1) if self.b2 is None
                             else self.hold_bill2(j, d.t + 1, line.t + 1))
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
        if self.b2 is not None or self.b3 is not None:
            # Bill N's parts first: its sizes and ledgers take every priced turn, in order.
            p = self.pricer.p
            bill_n = (p.beta_r * l0_read_cost(p.v2.base, TL.l0_counts(reads)),
                      float(price.get("merges", {}).get(0, [0.0, 0.0])[0]),
                      self.deep_merges(price), self.debt_change(line, price))
            if self.b2 is not None:
                self.bill2_turn(prev, line, price, reads)
            else:
                self.bill3_turn(prev, line, price, reads)
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
        if self.b2 is None and self.b3 is None:
            read = p.beta_r * l0_read_cost(p.v2.base, TL.l0_counts(reads))
            J = read + row.M[0]
        else:
            read, merge0, deep, debt = bill_n
            J = read + merge0 + sum(deep) + sum(debt)
        tr = Transition(t=line.t, x0=prev.x, m0=prev.masks, a=prev.actions, x1=line.x,
                        m1=line.masks, d0=prev.owed, d1=line.owed, J=J,
                        changes=prev.set + line.mid_applies)
        td = self.l0_td(tr)
        record = {"type": "turn", "t": line.t, "op0": line.op0, "op1": line.op1,
                  "ops": price["ops"], "secs": price["secs"], "J": price["J"],
                  "J_reward": tr.J, "C_W": price["C_W"], "C_R": price["C_R"],
                  "C_S": price["C_S"], "jobs": price["jobs"], "changes": tr.changes,
                  "phi0": 0.0, "phi1": 0.0, "r": self.reward(tr), "td": td,
                  "actions": tr.a, "explored": prev.explored, "eps": prev.eps,
                  "final": int(line.final)}
        if self.b2 is None and self.b3 is None:
            record.update(l0_bill_read=read, l0_bill_merge=row.M[0])
        else:
            record.update(bill_read=read, bill_merge=merge0, bill_deep=sum(deep), bill_deep_levels=deep,
                          bill_debt=sum(debt), bill_debt_levels=debt, debt=list(self.debt.balances),
                          tree=self.tree_record(), debt_l3=0, l0_bill="N")
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

    def finish(self, why: str) -> int:
        record = {"type": "levels", "periods": self.periods, "slow_n_step": self.slow_n,
                  "holds": [len(h) for h in self.holds], "steps": self.level_steps,
                  "centre": self.centre, "spread": self.lspread}
        if self.b2 is not None:
            record.update(bill2=self.b2_variant, bill2_turns=self.b2.turns,
                          bill2_raises={j: b.raises for j, b in self.b2.bills.items()},
                          bill2_D_start={j: b.D_start for j, b in self.b2.bills.items()})
        if self.b3 is not None:
            record.update(bill3="r", bill3_Y=self.b3.Y, bill3_turns=self.b3.turns,
                          **({"bill3_out_floor": 1} if self.b3.out_floor else {}),
                          **({"bill3_a4": "st", "bill3_st": {"a": (LB3.ST_A if self.b3.st_trap is None
                                                                   else dict(LB3.ST_A, trap=self.b3.st_trap)),
                                                             "tau0": LB3.ST_TAU0,
                                                             "hold": LB3.ST_HOLD}}
                             if self.b3.a4_form == "st" else {}),
                          **({"bill3_a4": "none"} if self.b3.a4_form == "none" else {}),
                          **({"bill3_st_trap": self.b3.st_trap} if self.b3.st_trap is not None else {}),
                          **({"bill3_owed": self.b3.owed} if self.b3.owed != "decided" else {}),
                          **({"bill3_a3": 1} if self.b3.a3 else {}),
                          **({"bill3_pay": self.b3_pay} if self.b3_pay != "bill" else {}),
                          **({"bill3_probe_price": self.b3.probe_price} if self.b3.probe_price is not None else {}),
                          bill3_levels=self.b3_levels,
                          bill3_raises={j: b.raises for j, b in self.b3.bills.items()},
                          bill3_room_opens={j: b.opens for j, b in self.b3.bills.items()},
                          bill3_room_adds={j: b.adds for j, b in self.b3.bills.items()},
                          bill3_A1_start={j: b.A1_start for j, b in self.b3.bills.items()})
        self.emit(record)
        return super().finish(why)
