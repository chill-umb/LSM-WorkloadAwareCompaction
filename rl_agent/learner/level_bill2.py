"""The level bill, design 2, for a slow deep level j >= 2 of a clocked turn
run (trainer settings level_bill2 1; level_bills.py). The specification is
~/node_ops/diag/2026-11-18-levelbill2/DESIGN.md; its offline replay (the
readings of ambiguous points) is RULES.md and levelbill2.py there; the
readings fixed for a live run are the lb2 kit's RULES.md
(~/node_ops/workers/2026-11-18-lb2/). Pure: no trainer, torch or file
dependency.

Bill2(j, t) = R(t) + M(t) + [D(t) - D(t-1)] + F(t), one turn t at a time:

  R  the priced reads of levels j-1, j, .., n in the turn: each level's
     probes, filter passes, seeks, reopens and hidden scan steps at cost
     model 2's base prices, beta_r applied (turn_ledger.read_cells' R + H).
     Variant g ("at target"): L_j's own part times clamp(C_j / B_j, 0.5, 2)
     (B_j = 0: 2).
  M  every merge out of L_(j-1) .. L_(n-1) that finished in the turn, each
     at its whole price (turn_trainer.merge_prices, as bill N).
  D  three ledgers of owed bytes, with B the level sizes at the end of the
     turn (taken as max(0, B)) and C the targets in force during it:
       E_(j-1) = max(0, B_(j-1) - C_(j-1))
       E_j     = max(0, B_j + E_(j-1) - C_j)
       E_(j+1) = max(0, B_(j+1) + E_j - C_(j+1)), 0 when no level below
                 L_(j+1) holds bytes
     each with bill N's rule at its level's price of the day p_i: X grew,
     D += p (X - X_prev); X shrank, D *= X / X_prev.
  F  the fill charge, after a raise of L_j's own target, until B_j first
     reaches C_j: the rise of B_j above its highest value since the raise,
     capped at C_j, times the chain price q_j + q_(j+1) + .. + q_k, k the
     deepest level such that each of L_(j+1) .. L_k reached its target at
     least once in the last 16 turns and k < n (never the last level that
     holds bytes); q = the prices of the day frozen at the turn of the raise.

The price of the day, after each turn's merges are added (running sums,
half-life 64 turns, lam = 0.5^(1/64)):
  own_i = (money of the merges out of L_i) / (bytes jobs took out of L_i,
          moves without rewriting included), while that byte sum is above
          1e6; else none
  w     = (money of the merges out of L1 and deeper) / (the bytes fed into
          them, source + overlap, moves left out), while that byte sum is
          above 1e6, else its last value; none before it first is (then 0)
  p_i   = own_i, else the size model w (1 + min(B_(i+1) / B_i, 2T))
          (B_i = 0: 2T; no level below: w)

A change of a target in force (a level's move, its glide, or a neighbour's
move) between two turns re-bases the ledgers at the turn boundary, outside
the flow: what is owed is recomputed on the sizes at the boundary with the
new targets and booked into (or released from) the balances by the ledger
rule at the price of the day at the boundary; the flow D(t) - D(t-1) then
holds only what the turn itself changed. The start is booked the same way:
what the first line's tree owes is put in the balances at the first priced
turn's prices, outside the flow.

Variants: d = R + M; e = d + dD; f = e + F (the default); g = f with R at
target. Every part is computed and logged whatever the variant; the variant
picks what the level is paid."""

from __future__ import annotations

import math
from collections import deque

VARIANTS = ("d", "e", "f", "g")
DEFAULT_VARIANT = "f"
HALF_LIFE = 64
MIN_BYTES = 1e6
FULL_LOOK = 16
REL = 1e-12          # a target "changed" when it moved by more than this share


def _get(values, i: int, default: float = 0.0) -> float:
    return float(values[i]) if 0 <= i < len(values) else default


def _finite_list(values, what: str) -> list:
    out = []
    for i, v in enumerate(values):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError(f"{what}[{i}] is not a finite number: {v!r}")
        out.append(float(v))
    return out


def owed2(B, C, j: int) -> list:
    """[E_(j-1), E_j, E_(j+1)] of sizes B (each taken as max(0, B)) and
    targets C; a target past the list is never reached (nothing owed)."""
    b = lambda i: max(0.0, _get(B, i))  # noqa: E731
    c = lambda i: _get(C, i, math.inf)  # noqa: E731
    e0 = max(0.0, b(j - 1) - c(j - 1))
    e1 = max(0.0, b(j) + e0 - c(j))
    below = any(b(k) > 0 for k in range(j + 2, len(B)))
    e2 = max(0.0, b(j + 1) + e1 - c(j + 1)) if below else 0.0
    return [e0, e1, e2]


def last_level(B) -> int:
    """n: the deepest level that holds bytes (0 when none does)."""
    return max((i for i, v in enumerate(B) if v > 0), default=0)


def changed(a: float, b: float) -> bool:
    return abs(a - b) > REL * max(abs(a), abs(b), 1.0)


class DayPrices:
    """The prices of the day of one run; add() the turns in order."""

    def __init__(self, n_levels: int, T: float, half_life: float = HALF_LIFE):
        if not (isinstance(T, (int, float)) and math.isfinite(T) and T > 0):
            raise ValueError(f"T must be a number above 0, not {T!r}")
        self.n = int(n_levels)
        self.T = float(T)
        self.lam = 0.5 ** (1.0 / half_life)
        self.Pp = [0.0] * self.n    # money of the merges out of L_i, running
        self.Mm = [0.0] * self.n    # bytes jobs took out of L_i, running
        self.own = [None] * self.n  # own_i of the last add (None: no price of its own)
        self.Pw = self.Fw = 0.0     # merges out of L1+: money and bytes fed, running
        self.w = None               # None until first defined
        self.turns = 0

    def add(self, paid, moved, fed) -> None:
        """One turn's merges per start level: their whole price, the bytes
        taken out of the level (moves included), the bytes fed (moves left
        out). Lists shorter than the levels are padded with 0."""
        paid, moved, fed = (_finite_list(v, k) for v, k in ((paid, "paid"), (moved, "moved"), (fed, "fed")))
        lam = self.lam
        for i in range(self.n):
            self.Pp[i] = lam * self.Pp[i] + _get(paid, i)
            self.Mm[i] = lam * self.Mm[i] + _get(moved, i)
            self.own[i] = self.Pp[i] / self.Mm[i] if self.Mm[i] > MIN_BYTES else None
        self.Pw = lam * self.Pw + sum(paid[1:])
        self.Fw = lam * self.Fw + sum(fed[1:])
        if self.Fw > MIN_BYTES:
            self.w = self.Pw / self.Fw
        self.turns += 1

    def vector(self, B) -> list:
        """p_i for every level, with the sizes B (the end of the turn)."""
        w = self.w if self.w is not None else 0.0
        out = []
        for i in range(self.n):
            if self.own[i] is not None:
                out.append(self.own[i])
                continue
            bi = max(0.0, _get(B, i))
            if i + 1 >= self.n:
                ratio = 0.0
            elif bi > 0:
                ratio = min(max(0.0, _get(B, i + 1)) / bi, 2 * self.T)
            else:
                ratio = 2 * self.T
            out.append(w * (1.0 + ratio))
        return out


class LevelBill2:
    """One level's design-2 bill: its ledgers and its fill charge."""

    def __init__(self, j: int):
        if isinstance(j, bool) or not isinstance(j, int) or j < 2:
            raise ValueError(f"design 2 is for a level j >= 2, not {j!r}")
        self.j = j
        self.lv = [j - 1, j, j + 1]
        self.prev = None            # [E_(j-1), E_j, E_(j+1)] after the last turn (or at the start)
        self.bal = [0.0] * 3        # the three balances
        self.booked = False         # the start booked (at the first add)
        self.D_start = 0.0
        self.fill_on = False
        self.h = 0.0
        self.q = None               # the frozen prices of the open fill
        self.t_raise = None
        self.raises = 0

    def start(self, B, C) -> None:
        self.prev = owed2(B, C, self.j)

    def _book(self, now: list, prices: list) -> list:
        changes = []
        for k in range(3):
            x, pv, before = now[k], self.prev[k], self.bal[k]
            if x > pv:
                self.bal[k] = before + prices[self.lv[k]] * (x - pv)
            elif pv > 0:
                self.bal[k] = before * (x / pv)
            changes.append(self.bal[k] - before)
        self.prev = list(now)
        return changes

    def add(self, t, B_prev, C_prev, B, C, p_before, p_now, R, H, paid, looks) -> dict:
        """One turn: B_prev, C_prev the sizes and targets at the turn's start
        (the last turn's end), B the sizes at its end, C the targets in force
        during it; p_before the prices of the day at the turn's start (None
        before the first turn), p_now after its merges; R, H the priced reads
        and hidden steps per level; paid the merges' money per start level;
        looks the (sizes, targets) of the last 16 turns, this one included
        (and the start's while fewer than 16). Returns the parts."""
        if self.prev is None:
            raise RuntimeError("LevelBill2.add before start")
        j, lv = self.j, self.lv
        p_move = p_before if p_before is not None else p_now
        if not self.booked:         # the start is booked, outside the flow
            self.bal = [p_now[lv[k]] * self.prev[k] if 0 <= lv[k] < len(p_now) else 0.0
                        for k in range(3)]
            self.D_start = sum(self.bal)
            self.booked = True
        rebase = [0.0, 0.0, 0.0]
        if any(changed(_get(C, i, math.inf), _get(C_prev, i, math.inf)) for i in lv):
            rebase = self._book(owed2(B_prev, C, j), p_move)
        raised = _get(C, j) > _get(C_prev, j) and changed(_get(C, j), _get(C_prev, j))
        if raised:                  # a raise of L_j's own target opens a fill
            self.raises += 1
            self.fill_on = _get(B_prev, j) < _get(C, j)
            self.h = _get(B_prev, j)
            self.q = list(p_move)
            self.t_raise = t
        dD = self._book(owed2(B, C, j), p_now)
        F = absorbed = 0.0
        chain = None
        if self.fill_on:
            n = last_level(B)
            chain = self.q[j]
            for i in range(j + 1, len(B)):
                if i >= n or not any(_get(Lb, i) >= _get(Lc, i, math.inf) for Lb, Lc in looks):
                    break
                chain += self.q[i]
            bj, cj = max(0.0, _get(B, j)), _get(C, j)
            absorbed = max(0.0, min(bj, cj) - self.h)
            self.h = max(self.h, bj)
            F = absorbed * chain
            if bj >= cj:
                self.fill_on = False
        reads = [_get(R, i) + _get(H, i) for i in range(max(len(R), len(H)))]
        Rsum = sum(reads[j - 1:])
        Rj = _get(reads, j)
        bj = max(0.0, _get(B, j))
        fac = min(2.0, max(0.5, _get(C, j) / bj)) if bj > 0 else 2.0
        M = sum(_get(paid, i) for i in range(j - 1, len(paid)))
        parts = {"R": Rsum, "Rj": Rj, "Rg": Rsum + (fac - 1.0) * Rj, "M": M,
                 "dD": sum(dD), "dD_levels": dD, "F": F, "rebase": rebase,
                 "E": list(self.prev), "D": list(self.bal), "fill": int(chain is not None),
                 "absorbed": absorbed, "chain": chain, "h": self.h if chain is not None else None,
                 "p": [_get(p_now, i) for i in lv], "C": [_get(C, i, math.inf) for i in lv],
                 "raised": int(raised)}
        return parts


def bill_of(parts: dict, variant: str) -> float:
    """The turn's bill under a variant (d, e, f or g)."""
    if variant == "d":
        return parts["R"] + parts["M"]
    if variant == "e":
        return parts["R"] + parts["M"] + parts["dD"]
    if variant == "f":
        return parts["R"] + parts["M"] + parts["dD"] + parts["F"]
    if variant == "g":
        return parts["Rg"] + parts["M"] + parts["dD"] + parts["F"]
    raise ValueError(f"the design-2 variant is d, e, f or g, not {variant!r}")


class Bill2Run:
    """Every design-2 level of one run: the shared prices of the day and the
    sizes' history, one LevelBill2 per level. start() once, then add() the
    priced turns in order."""

    def __init__(self, levels, variant: str = DEFAULT_VARIANT, half_life: float = HALF_LIFE):
        if variant not in VARIANTS:
            raise ValueError(f"the design-2 variant is d, e, f or g, not {variant!r}")
        self.variant = variant
        self.half_life = half_life
        self.bills = {int(j): LevelBill2(int(j)) for j in levels}
        self.prices = None
        self.B = self.C = None
        self.p = None
        self.looks = deque(maxlen=FULL_LOOK)
        self.start_look = None
        self.turns = 0

    def start(self, B, C, T: float) -> None:
        """The tree at the first line: sizes B and the targets in force C;
        T the tree's size ratio (the size model's)."""
        self.B = [max(0.0, v) for v in _finite_list(B, "B")]
        self.C = [float(v) for v in C]
        self.prices = DayPrices(len(self.B), T, self.half_life)
        self.start_look = (list(self.B), list(self.C))
        for bill in self.bills.values():
            bill.start(self.B, self.C)

    def add(self, t, B, C, paid, moved, fed, R, H) -> dict:
        """One priced turn t: B the sizes at its end, C the targets in force
        during it, the merges' flows per start level, the priced reads R and
        hidden steps H per level. Returns {j: parts with "bill"}."""
        if self.prices is None:
            raise RuntimeError("Bill2Run.add before start")
        B = [max(0.0, v) for v in _finite_list(B, "B")]
        C = [float(v) for v in C]
        p_before = self.p
        self.prices.add(paid, moved, fed)
        p_now = self.prices.vector(B)
        self.looks.append((B, C))
        looks = list(self.looks)
        if len(looks) < FULL_LOOK:
            looks.append(self.start_look)
        out = {}
        for j, bill in self.bills.items():
            parts = bill.add(t, self.B, self.C, B, C, p_before, p_now, R, H, paid, looks)
            parts["bill"] = bill_of(parts, self.variant)
            out[j] = parts
        self.B, self.C, self.p = B, C, p_now
        self.turns += 1
        return out
