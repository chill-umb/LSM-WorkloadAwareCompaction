"""The level bill, design 3 repaired ("Bill3r"), for a deep level j >= 1 of
a turn run whose deep levels decide every turn (trainer settings
level_bill3 1; level_bills.py). The specification is
~/node_ops/diag/2026-11-19-levelbill3r/DESIGN.md, which builds on design 3
(../2026-11-19-levelbill3/DESIGN.md, RULES.md and bill3core.py there) and on
design 2's prices (level_bill2.py); the readings of its open points are the
lb3 kit's RULES.md (~/node_ops/workers/2026-11-19-lb3/). Pure: no trainer,
torch or file dependency.

Bill3r(j, t) = R(t) + M(t) + [A(t) - A(t-1)],  A = A1 + A2 + A4

  R   the priced reads (each level's reads and hidden scan steps) of levels
      j-1 .. n in the turn; L1: levels 1 .. n.
  M   every merge out of L_(j-1) .. L_(n-1) that finished in the turn, at its
      whole price; L1: out of L0 .. L_(n-1).
  C_j* the size level j has decided on (its base target times its rung);
      the targets of the other levels are those in force during the turn.

Prices (running sums, half-life 64 turns):
  mixed_i  design 2's price of the day p_i (money of the merges out of L_i /
           every byte jobs took out of L_i, plain moves included; else the
           size model w (1 + min(B_(i+1) / B_i, 2T)))
  merge_i  money of the merges out of L_i / the source bytes of those merges
           (plain moves left out), while that byte sum is above 1e6; else the
           same size model
  push_i   merge_i when L_(i+1) is full (reached its target in force in one
           of the last 16 turns) and is not the last level that holds bytes;
           mixed_i otherwise
  rest(k)  push_k + push_(k+1) + .. while each of those levels is full and
           is above the last level that holds bytes

A1  owed bytes, bill N's ledger rule each (X grew: bal += price x rise;
    X shrank: bal *= X / X_prev):
      j >= 2: E_(j-1) = max(0, B_(j-1) - C_(j-1))      at push_(j-1)
              E_j     = max(0, B_j + E_(j-1) - C_j*)   at push_j
              E_(j+1) = max(0, B_(j+1) + E_j - C_(j+1)) at push_(j+1) + rest(j+2)
                        (0 when no level below L_(j+1) holds bytes)
      j = 1:  E_1     = max(0, B_1 - C_1*)              at push_1
              E_2     = max(0, B_2 + E_1 - C_2)         at push_2 + rest(3)
    A change of a neighbour's target in force (L_(j-1) or L_(j+1); L1: L2)
    re-bases the ledgers outside the flow (the boundary's sizes, the new
    neighbour targets, the old C_j*, the prices of the turn before); level
    j's own decision (a change of C_j*) is booked in the flow. The start is
    booked outside the flow at the first turn's prices.
A2  room still to fill after own raises (a credit, <= 0):
      X = max(0, C_j* - max(h, c0)); h = the highest B_j since the room
      opened (from the boundary's B_j); c0 = C_j* before the raise that opened
      it; a raise while the room is open adds its room; the room closes when
      X is 0. X_eff = min(X, f Y); ledger rule on X_eff at q = push_j +
      rest(j+1) of the turn it grows (A2 -= q x rise; A2 *= X_eff / prev).
A4  read cost still to come, the copies formula:
      rho = clamp(C_j* / B_j, 0.5, 2) (B_j = 0: 2); dB = C_j* - B_j;
      f, out = bytes per turn taken out of L_(j-1) / out of L_j by jobs going
      down (running means, half-life 16 turns); h_i = priced hidden scan
      steps at L_i per turn (mean of the last 8 turns); G = max(0, rise per
      turn of h_(j+1) + .. + h_n over the last 64 turns); tm = |dB| / f;
      Yeff = Y - tm / 2 if tm <= Y, else Y^2 / (2 tm);
      dr = (h_j + h_(j+1)) (rho - 1) - (G / out) dB;  A4 = Yeff dr.
    out_floor (trainer setting level_bill3_out_floor 1, batch lb4): the
      spread divides by max(out, f) instead of out. A raised level sends
      almost nothing down while it fills, so out falls towards 0 and the
      spread term blew up (lb3: single-turn jumps up to 4e7 turns of mean
      cost). f is the bytes entering the level per turn, which is what it
      sends down on average at its normal size; with it as the floor
      |Yeff x spread| <= Y^2 G.
Y = 32. A2 and A4 start at 0 (their first values enter the flow).

Over a run, per level: sum of the bills + sum of the re-bases = sum of
(R + M) + A(end) - A1(start).

Three options after batch lb5's cause study (2026-11-20; ~/node_ops/diag/2026-11-20-lb5cause/NOTES.md), each off by
default, so the bill of lb3 - lb5 is unchanged without them:
  st_trap w   (steal / trap form only) the trap part's weight; 0 takes the credit for a raise out. lb5: raises that
              stayed were booked as savings and the hidden steps rose; the trap part was the whole booked saving.
  owed "capped"  the ledgers and the room never count a size above a level's normal (x1) target: every target the
              ledgers use (C_j* and the neighbours' targets in force) and the room's C_j* is min(itself, Cn_i).
              The run's clean-up merges exactly the bytes above the normal sizes (lb5: 251 runs, ratio 1.00), so
              bytes a level holds above normal are owed, whatever its decided size: growing past normal earns no
              refund and no room credit, and those bytes are charged as they arrive. At or below normal everything
              is as before: a lowering books its push-down at once, a raise back towards normal its refund and room.
  a3          the merge account A3, the steal / trap form's twin for writes. Measured in lb5: the bytes of L_j a merge
              out of L_(j-1) rewrites follow L_j's size, the bytes of L_(j+1) a merge out of L_j rewrites follow
              B_(j+1) / B_j (+1% -> +1.07 .. +1.30%). A move of level j to rho = C_j*(t) / C_j*(t-1) changes the
              merge money per turn, once settled, by
                rate = m_in (rho - 1) + m_out (1 / rho - 1),
              m_in / m_out = the money per turn of the merges out of L_(j-1) / L_j that pays for rewriting the level
              below them (money x overlap bytes / bytes fed; running means, half-life 64 turns, before the move).
              Booked and released exactly as the steal / trap account's moves are (ST_TAU0, ST_HOLD).
  probe_price p  (steal / trap form only) the probe account A5. Measured in lb5: a get costs the same block reads
              wherever it is found (1.02-1.11 per get); the gets found at L_j follow L_j's bytes (+0.8 .. +1.7% per
              +1%); only the filter probes change. A move of level j to rho moves (rho - 1) q_j gets per turn between
              L_j and the level below it, each one filter probe (price p) less or more:
                rate = -(rho - 1) q_j p,   q_j = the 8-turn mean of the filter hits at L_j before the move.
              Booked and released as the other move accounts.

The read account's form "none" (trainer setting level_bill3_a4 "none", 2026-11-21): A4 is 0 on every turn, so the
bill is R + M + dA1 + dA2 (+ dA3 with a3). The hidden scan steps are then paid as they arrive, in R, and nothing is
booked for them at the decision. Offline (~/node_ops/diag/2026-11-20-closedform/harness): on lb5's W1 read runs the
bill without a read account is nearer the true cost than with the steal / trap account (whole-run error 0.72 against
2.66 points). The copies formula's inputs (cover, spread, dr, Yeff) are still logged."""

from __future__ import annotations

import math
from collections import deque

from learner import level_bill2 as LB2

Y = 32
HALF_LIFE = LB2.HALF_LIFE        # 64 turns: the prices' running sums
F_HALF_LIFE = 16                  # f and out
# The read account's steal / trap form (trainer setting level_bill3_a4 "st", batch lb5; fitted 2026-11-19 offline on
# the fixed-size runs dl1 and lb3's exploring moves, judged on dl2 and lb4: ~/node_ops/diag/2026-11-19-a4redesign).
# A move of level j to rho = C_j*(t) / C_j*(t-1) changes the hidden-step rate, once settled, by
#   rate = a_own h_j (rho-1) + a_steal (1-w) h_(j+1) (rho-1) - a_trap w (1-1/rho) sum_(i=j+1..Lf) h_i [rho > 1 only]
#   (j < Lf), or a_last h_j (1/rho - 1) for the last full level j = Lf,
# w = share of the filter hits at levels <= j, Lf = deepest level whose seeks are >= half of L1's, h, q, seeks the
# 8-turn means before the move. The change builds up as 1 - exp(-tau / tau0); the account holds the part still to
# come over ST_HOLD turns: A4 = sum over open moves of rate x (B(ST_HOLD) - B(age + 1)), B(n) = sum_(tau<n) b(tau).
ST_A = {"own": 1.716, "steal": 0.984, "trap": 1.903, "last": 0.173}
ST_TAU0 = 8.0
ST_HOLD = 128
ST_B = [0.0]
for _tau in range(ST_HOLD):
    ST_B.append(ST_B[-1] + 1.0 - math.exp(-_tau / ST_TAU0))
A4_FORMS = ("copies", "st", "none")
OWED_FORMS = ("decided", "capped")
H_TURNS = 8                       # h_i: mean of the last 8 turns
G_TURNS = 64                      # G: rise over the last 64 turns
MIN_BYTES = LB2.MIN_BYTES
FULL_LOOK = LB2.FULL_LOOK
REL = LB2.REL

_get = LB2._get
changed = LB2.changed
last_level = LB2.last_level


def _finite(values, what: str) -> list:
    return LB2._finite_list(values, what)


def st_parts(hm: list, qm: list, sm: list, j: int, rho: float) -> dict:
    """The steal / trap read account's parts of a move of level j to rho (unweighted; ST_A weighs them). hm, qm, sm:
    the 8-turn means of the priced hidden steps, the filter hits and the seeks per level before the move."""
    s1 = _get(sm, 1)
    full = [i for i in range(len(sm)) if s1 > 0 and _get(sm, i) >= 0.5 * s1]
    Lf = max(full) if full else len(hm) - 1
    tq = sum(qm)
    w = sum(_get(qm, i) for i in range(j + 1)) / tq if tq > 0 else 0.0
    hj, hj1 = _get(hm, j), _get(hm, j + 1)
    out = {"own": 0.0, "steal": 0.0, "trap": 0.0, "last": 0.0, "w": w, "Lf": Lf}
    if j >= Lf:
        out["last"] = hj * (1.0 / rho - 1.0)
    else:
        out["own"] = hj * (rho - 1.0)
        out["steal"] = (1.0 - w) * hj1 * (rho - 1.0)
        if rho > 1:
            out["trap"] = -w * (1.0 - 1.0 / rho) * sum(_get(hm, i) for i in range(j + 1, Lf + 1))
    return out


def owed3(B, C, cstar: float, j: int) -> list:
    """The ledgers' owed bytes of level j: [E_(j-1), E_j, E_(j+1)] for
    j >= 2, [E_1, E_2] for L1; sizes taken as max(0, B), C_j* for L_j, the
    targets in force C elsewhere (a target past the list is never reached)."""
    b = lambda i: max(0.0, _get(B, i))  # noqa: E731
    c = lambda i: _get(C, i, math.inf)  # noqa: E731
    if j == 1:
        e1 = max(0.0, b(1) - cstar)
        below = any(b(k) > 0 for k in range(3, len(B)))
        e2 = max(0.0, b(2) + e1 - c(2)) if below else 0.0
        return [e1, e2]
    e0 = max(0.0, b(j - 1) - c(j - 1))
    e1 = max(0.0, b(j) + e0 - cstar)
    below = any(b(k) > 0 for k in range(j + 2, len(B)))
    e2 = max(0.0, b(j + 1) + e1 - c(j + 1)) if below else 0.0
    return [e0, e1, e2]


def ledger_levels(j: int) -> list:
    return [1, 2] if j == 1 else [j - 1, j, j + 1]


def neighbours(j: int) -> list:
    """The levels whose target in force re-bases level j's ledgers."""
    return [2] if j == 1 else [j - 1, j + 1]


def full_flags(looks, n_levels: int) -> list:
    """Per level: B_i >= C_i (the target in force) in at least one look."""
    return [any(_get(Lb, i) >= _get(Lc, i, math.inf) for Lb, Lc in looks) for i in range(n_levels)]


def push_vector(mixed: list, merge: list, full: list, n: int) -> list:
    """push_i = merge_i when L_(i+1) is full and is above the last level that
    holds bytes (i + 1 < n), else mixed_i."""
    return [merge[i] if (i + 1 < n and i + 1 < len(full) and full[i + 1]) else mixed[i]
            for i in range(len(mixed))]


def rest(push: list, full: list, n: int, k: int) -> float:
    """push_k + push_(k+1) + .. while each level is full and above n."""
    s = 0.0
    for i in range(k, len(push)):
        if i >= n or not full[i]:
            break
        s += push[i]
    return s


class Prices3:
    """The two prices of every level for one run; add() the turns in order."""

    def __init__(self, n_levels: int, T: float, half_life: float = HALF_LIFE):
        self.day = LB2.DayPrices(n_levels, T, half_life)   # mixed_i and w (design 2's)
        self.n = int(n_levels)
        self.lam = self.day.lam
        self.Pm = [0.0] * self.n       # money of the merges out of L_i, running
        self.Sm = [0.0] * self.n       # source bytes of those merges, running
        self.own_merge = [None] * self.n

    def add(self, paid, moved, fed, mergeout) -> None:
        self.day.add(paid, moved, fed)
        paid, mergeout = _finite(paid, "paid"), _finite(mergeout, "mergeout")
        for i in range(self.n):
            self.Pm[i] = self.lam * self.Pm[i] + _get(paid, i)
            self.Sm[i] = self.lam * self.Sm[i] + _get(mergeout, i)
            self.own_merge[i] = self.Pm[i] / self.Sm[i] if self.Sm[i] > MIN_BYTES else None

    def size_model(self, B, i: int) -> float:
        """design 2's size model w (1 + min(B_(i+1) / B_i, 2T))."""
        w = self.day.w if self.day.w is not None else 0.0
        bi = max(0.0, _get(B, i))
        if i + 1 >= self.n:
            ratio = 0.0
        elif bi > 0:
            ratio = min(max(0.0, _get(B, i + 1)) / bi, 2 * self.day.T)
        else:
            ratio = 2 * self.day.T
        return w * (1.0 + ratio)

    def vectors(self, B) -> tuple:
        """(mixed, merge) per level, with the sizes B at the turn's end."""
        mixed = self.day.vector(B)
        merge = [self.own_merge[i] if self.own_merge[i] is not None else self.size_model(B, i)
                 for i in range(self.n)]
        return mixed, merge


class LevelBill3:
    """One level's Bill3r: its ledgers (A1), its room (A2), its read account (A4)."""

    def __init__(self, j: int, Y_turns: float = Y, out_floor: bool = False, a4_form: str = "copies",
                 owed: str = "decided", a3: bool = False, st_trap: float | None = None,
                 probe_price: float | None = None):
        if isinstance(j, bool) or not isinstance(j, int) or j < 1:
            raise ValueError(f"Bill3r is for a level j >= 1, not {j!r}")
        if a4_form not in A4_FORMS:
            raise ValueError(f"the read account's form is one of {A4_FORMS}, not {a4_form!r}")
        if owed not in OWED_FORMS:
            raise ValueError(f"the owed bytes' form is one of {OWED_FORMS}, not {owed!r}")
        if st_trap is not None and (a4_form != "st" or not math.isfinite(st_trap) or st_trap < 0):
            raise ValueError(f"st_trap is the steal / trap form's trap weight, a number >= 0, not {st_trap!r}")
        if probe_price is not None and (a4_form != "st" or not math.isfinite(probe_price) or probe_price < 0):
            raise ValueError(f"probe_price is the steal / trap form's probe account's price, >= 0, not {probe_price!r}")
        self.probe_price = None if probe_price is None else float(probe_price)
        self.a5_moves = []             # probe account: [rate, age] of the open moves
        self.A5 = 0.0
        self.owed = owed
        self.a3 = bool(a3)
        self.st_a = dict(ST_A)
        if st_trap is not None:
            self.st_a["trap"] = float(st_trap)
        self.a3_moves = []             # merge account: [rate, age] of the open moves
        self.A3 = 0.0
        self.lam64 = 0.5 ** (1.0 / HALF_LIFE)
        self.mi = self.mo = self.mw = 0.0
        self.j = j
        self.Y = float(Y_turns)
        self.out_floor = bool(out_floor)
        self.a4_form = a4_form
        self.st_moves = []             # steal / trap form: [rate, age] of the open moves
        self.lv = ledger_levels(j)
        self.prev = None
        self.bal = [0.0] * len(self.lv)
        self.booked = False
        self.A1_start = 0.0
        self.room = False
        self.h = self.c0 = 0.0
        self.Xe = 0.0
        self.A2 = 0.0
        self.A4 = 0.0
        self.lam16 = 0.5 ** (1.0 / F_HALF_LIFE)
        self.fs = self.os = self.fw = 0.0
        self.raises = self.opens = self.adds = 0

    def _capped(self, C, cstar: float, Cn) -> tuple:
        """owed "capped": the targets the ledgers and the room use, none above its level's normal target."""
        if self.owed != "capped":
            return C, cstar
        if Cn is None:
            raise ValueError("owed 'capped' needs the normal targets")
        n = max(len(C), len(Cn))
        return ([min(_get(C, i, math.inf), _get(Cn, i, math.inf)) for i in range(n)],
                min(cstar, _get(Cn, self.j, math.inf)))

    def start(self, B, C, cstar: float, Cn=None) -> None:
        C, cstar = self._capped(C, cstar, Cn)
        self.prev = owed3(B, C, cstar, self.j)

    def ledger_prices(self, st: dict) -> list:
        """The ledgers' prices of a turn's price state."""
        j, push, full, n = self.j, st["push"], st["full"], st["n"]
        if j == 1:
            return [_get(push, 1), _get(push, 2) + rest(push, full, n, 3)]
        return [_get(push, j - 1), _get(push, j), _get(push, j + 1) + rest(push, full, n, j + 2)]

    def _rule(self, bal: list, prev: list, now: list, prices: list) -> list:
        changes = []
        for k in range(len(now)):
            x, pv, before = now[k], prev[k], bal[k]
            if x > pv:
                bal[k] = before + prices[k] * (x - pv)
            elif pv > 0:
                bal[k] = before * (x / pv)
            changes.append(bal[k] - before)
            prev[k] = x
        return changes

    def add(self, B_prev, C_prev, cs_prev: float, B, C, cs: float, st_prev: dict, st: dict,
            reads: list, paid: list, moved: list, hm: list, h_old: list, dt: int, pre=None,
            Cn=None, fed=None, mergeout=None) -> dict:
        """One turn. B_prev, C_prev, cs_prev: the sizes at the turn's start,
        the targets in force and C_j* of the turn before; B, C, cs: the sizes
        at its end, the targets in force during it, C_j* during it; st_prev,
        st: the price states (mixed, merge, push, full, n) of the turn before
        (None at the first) and of this turn; reads: priced reads per level
        (reads + hidden steps); paid: merge money per start level; moved:
        bytes jobs took out of each level going down (plain moves included);
        hm: the 8-turn means of the priced hidden steps per level; h_old: the
        same dt turns earlier; pre (steal / trap form only): the 8-turn means
        (hidden steps, filter hits, seeks) before this turn; Cn (owed "capped" only): the
        normal targets; fed, mergeout (a3 only): the bytes fed into the merges out of
        each level and their source bytes. Returns the parts."""
        if self.prev is None:
            raise RuntimeError("LevelBill3.add before start")
        j, Yt = self.j, self.Y
        pr_now = self.ledger_prices(st)
        pr_move = self.ledger_prices(st_prev) if st_prev is not None else pr_now
        if not self.booked:            # the start, outside the flow
            self.bal = [pr_now[k] * self.prev[k] for k in range(len(self.lv))]
            self.A1_start = sum(self.bal)
            self.booked = True
        # ---- A1: a neighbour's target change re-bases outside the flow, then the flow
        rb = [0.0] * len(self.lv)
        cs_raw, cs_prev_raw = cs, cs_prev          # the read and merge accounts follow the decided size itself
        C, cs = self._capped(C, cs, Cn)            # the ledgers and the room: never above normal (owed "capped")
        C_prev, cs_prev = self._capped(C_prev, cs_prev, Cn)
        if any(changed(_get(C, i, math.inf), _get(C_prev, i, math.inf)) for i in neighbours(j)):
            rb = self._rule(self.bal, self.prev, owed3(B_prev, C, cs_prev, j), pr_move)
        dec = 0.0
        if changed(cs, cs_prev):       # the decision's part alone (for the log): on copies
            dec = sum(self._rule(list(self.bal), list(self.prev), owed3(B_prev, C, cs, j), pr_now))
        dA1 = self._rule(self.bal, self.prev, owed3(B, C, cs, j), pr_now)
        # ---- f, out: running means, half-life 16
        lam = self.lam16
        self.fs = lam * self.fs + _get(moved, j - 1)
        self.os = lam * self.os + _get(moved, j)
        self.fw = lam * self.fw + 1.0
        f, out = self.fs / self.fw, self.os / self.fw
        # ---- A2
        raised = cs > cs_prev and changed(cs, cs_prev)
        opened = 0
        if raised:
            self.raises += 1
            if self.room:
                self.adds += 1
            else:
                self.room, self.h, self.c0 = True, max(0.0, _get(B_prev, j)), cs_prev
                self.opens += 1
                opened = 1
        Bj = max(0.0, _get(B, j))
        if self.room:
            self.h = max(self.h, Bj)
            X = max(0.0, cs - max(self.h, self.c0))
        else:
            X = 0.0
        Xe = min(X, f * Yt)
        q = _get(st["push"], j) + rest(st["push"], st["full"], st["n"], j + 1)
        before = self.A2
        if Xe > self.Xe:
            self.A2 -= q * (Xe - self.Xe)
        elif self.Xe > 0:
            self.A2 *= Xe / self.Xe
        self.Xe = Xe
        dA2 = self.A2 - before
        room_now = int(self.room)
        if self.room and X <= 0:
            self.room = False
        # ---- A4: the copies formula
        cs_led, cs, cs_prev = cs, cs_raw, cs_prev_raw
        rho = min(2.0, max(0.5, cs / Bj)) if Bj > 0 else 2.0
        dB = cs - Bj
        n = st["n"]
        S_now = sum(_get(hm, i) for i in range(j + 1, n + 1))
        S_old = sum(_get(h_old, i) for i in range(j + 1, n + 1))
        G = max(0.0, (S_now - S_old) / dt) if dt > 0 else 0.0
        if dB == 0:
            tm = 0.0
        elif f > 0:
            tm = abs(dB) / f
        else:
            tm = math.inf
        if tm <= Yt:
            Yeff = Yt - tm / 2.0
        else:
            Yeff = Yt * Yt / (2.0 * tm) if math.isfinite(tm) else 0.0
        hj, hj1 = _get(hm, j), _get(hm, j + 1)
        cover = (hj + hj1) * (rho - 1.0)
        den = max(out, f) if self.out_floor else out
        spread = (G / den) * dB if den > 0 else 0.0
        dr = cover - spread
        A4 = Yeff * dr
        sp, rate = None, 0.0
        if self.a4_form == "st":       # the steal / trap form replaces the copies formula's account
            if pre is None:
                raise ValueError("the steal / trap read account needs the 8-turn means before the turn")
            if changed(cs, cs_prev) and cs_prev > 0 and cs > 0:
                sp = st_parts(pre[0], pre[1], pre[2], j, cs / cs_prev)
                rate = sum(self.st_a[k] * sp[k] for k in ST_A)
                self.st_moves.append([rate, 0])
            A4 = sum(r * (ST_B[ST_HOLD] - ST_B[min(age + 1, ST_HOLD)]) for r, age in self.st_moves)
            self.st_moves = [[r, age + 1] for r, age in self.st_moves if age + 1 < ST_HOLD]
        elif self.a4_form == "none":   # no read account: the hidden steps are paid as they arrive, in R
            A4 = 0.0
        dA4 = A4 - self.A4
        self.A4 = A4
        # ---- A3: the merge account (the steal / trap form's twin for writes)
        dA3, rate3, m_in, m_out = 0.0, 0.0, 0.0, 0.0
        if self.a3:
            if fed is None or mergeout is None:
                raise ValueError("the merge account needs the merges' fed and source bytes")
            m_in = self.mi / self.mw if self.mw > 0 else 0.0     # the means before this turn
            m_out = self.mo / self.mw if self.mw > 0 else 0.0
            if changed(cs, cs_prev) and cs_prev > 0 and cs > 0:
                rho3 = cs / cs_prev
                rate3 = m_in * (rho3 - 1.0) + m_out * (1.0 / rho3 - 1.0)
                self.a3_moves.append([rate3, 0])
            A3 = sum(r * (ST_B[ST_HOLD] - ST_B[min(age + 1, ST_HOLD)]) for r, age in self.a3_moves)
            self.a3_moves = [[r, age + 1] for r, age in self.a3_moves if age + 1 < ST_HOLD]
            dA3 = A3 - self.A3
            self.A3 = A3
            share = lambda i: (max(0.0, 1.0 - _get(mergeout, i) / _get(fed, i)) if _get(fed, i) > 0 else 0.0)  # noqa: E731
            self.mi = self.lam64 * self.mi + _get(paid, j - 1) * share(j - 1)
            self.mo = self.lam64 * self.mo + _get(paid, j) * share(j)
            self.mw = self.lam64 * self.mw + 1.0
        # ---- A5: the probe account (gets found one level earlier or later)
        dA5, rate5 = 0.0, 0.0
        if self.probe_price is not None:
            if pre is None:
                raise ValueError("the probe account needs the 8-turn means before the turn")
            if changed(cs, cs_prev) and cs_prev > 0 and cs > 0:
                rate5 = -(cs / cs_prev - 1.0) * _get(pre[1], j) * self.probe_price
                self.a5_moves.append([rate5, 0])
            A5 = sum(r * (ST_B[ST_HOLD] - ST_B[min(age + 1, ST_HOLD)]) for r, age in self.a5_moves)
            self.a5_moves = [[r, age + 1] for r, age in self.a5_moves if age + 1 < ST_HOLD]
            dA5 = A5 - self.A5
            self.A5 = A5
        # ---- R, M, the bill
        Rs = sum(_get(reads, i) for i in range(1 if j == 1 else j - 1, len(reads)))
        M = sum(_get(paid, i) for i in range(j - 1, len(paid)))
        A1 = sum(self.bal)
        bill = Rs + M + sum(dA1) + dA2 + dA4 + dA3 + dA5
        parts = {"R": Rs, "M": M, "dA1": sum(dA1), "dA1_levels": dA1, "dA2": dA2, "dA4": dA4,
                "A1": A1, "A2": self.A2, "A4": A4, "A": A1 + self.A2 + A4 + self.A3 + self.A5, "bill": bill,
                "rb": rb, "rb_sum": sum(rb), "dec1": dec, "E": list(self.prev), "bal": list(self.bal),
                "led_lv": list(self.lv), "led_p": pr_now,
                "p_mixed": [_get(st["mixed"], i) for i in self.lv],
                "p_merge": [_get(st["merge"], i) for i in self.lv],
                "p_push": [_get(st["push"], i) for i in self.lv],
                "X": X, "X_eff": Xe, "q": q, "h": self.h if room_now else None,
                "c0": self.c0 if room_now else None, "room": room_now, "raised": int(raised),
                "opened": opened, "f": f, "out": out, "G": G, "rho": rho, "dB": dB, "tm": tm,
                "Yeff": Yeff, "h_j": hj, "h_j1": hj1, "cover": cover, "spread": spread, "dr": dr,
                "cstar": cs, "cstar_prev": cs_prev, "C_j": _get(C, j, math.inf), "B_j": Bj, "n": n}
        if self.owed == "capped":
            parts["cstar_led"] = cs_led
        if self.out_floor:
            parts["den"] = den
        if self.owed != "decided":
            parts["owed"] = self.owed
        if self.probe_price is not None:
            parts.update({"dA5": dA5, "A5": self.A5, "a5_rate": rate5, "a5_open": len(self.a5_moves)})
        if self.a3:
            parts.update({"dA3": dA3, "A3": self.A3, "a3_rate": rate3, "a3_open": len(self.a3_moves),
                          "m_in": m_in, "m_out": m_out})
        if self.a4_form == "st":
            parts.update({"a4": "st", "st_rate": rate, "st_open": len(self.st_moves),
                          **{f"st_{k}": (sp[k] if sp is not None else 0.0) for k in ("own", "steal", "trap", "last")},
                          "st_w": sp["w"] if sp is not None else None, "st_Lf": sp["Lf"] if sp is not None else None})
        elif self.a4_form == "none":
            parts["a4"] = "none"
        return parts


class Bill3Run:
    """Every Bill3r level of one run: the shared prices, looks and hidden-step
    means, one LevelBill3 per level. start() once, then add() the priced
    turns in order."""

    def __init__(self, levels, Y_turns: float = Y, half_life: float = HALF_LIFE, out_floor: bool = False,
                 a4_form: str = "copies", owed: str = "decided", a3: bool = False, st_trap: float | None = None,
                 probe_price: float | None = None):
        self.Y = float(Y_turns)
        self.half_life = half_life
        self.out_floor = bool(out_floor)
        self.a4_form = a4_form
        self.owed = owed
        self.a3 = bool(a3)
        self.st_trap = st_trap
        self.probe_price = probe_price
        self.bills = {int(j): LevelBill3(int(j), Y_turns, out_floor, a4_form, owed, a3, st_trap, probe_price)
                      for j in levels}
        self.Q = deque(maxlen=H_TURNS)
        self.S = deque(maxlen=H_TURNS)
        self.prices = None
        self.B = self.C = self.cs = None
        self.st = None
        self.looks = deque(maxlen=FULL_LOOK)
        self.start_look = None
        self.H = deque(maxlen=H_TURNS)
        self.hm = deque(maxlen=G_TURNS + 1)
        self.turns = 0

    def start(self, B, C, cstar: dict, T: float, Cn=None) -> None:
        """The tree at the first line: sizes B, the targets in force C, and
        each level's decided size C_j* (a dict); T the size ratio; Cn (owed
        "capped" only): the normal (x1) targets."""
        self.B = [max(0.0, v) for v in _finite(B, "B")]
        self.C = [float(v) for v in C]
        self.cs = {j: float(cstar[j]) for j in self.bills}
        self.prices = Prices3(len(self.B), T, self.half_life)
        self.start_look = (list(self.B), list(self.C))
        for j, bill in self.bills.items():
            bill.start(self.B, self.C, self.cs[j], None if Cn is None else [float(v) for v in Cn])

    def add(self, t, B, C, cstar: dict, paid, moved, fed, mergeout, R, H, q=None, s=None, Cn=None) -> tuple:
        """One priced turn t: B the sizes at its end, C the targets in force
        during it, cstar each level's C_j* during it, the merges' flows per
        start level (money, bytes taken out with plain moves, bytes fed,
        merge source bytes), the priced reads R and hidden steps H per level;
        q, s (steal / trap form only): the turn's filter hits and seeks per
        level. Returns ({j: parts}, the turn's price state)."""
        if self.prices is None:
            raise RuntimeError("Bill3Run.add before start")
        pre = None
        if self.a4_form == "st":
            if q is None or s is None:
                raise ValueError("the steal / trap read account needs each turn's filter hits and seeks")
            q, s = _finite(q, "filter hits"), _finite(s, "seeks")
            mean = lambda dq, now: ([sum(x[i] for x in dq if i < len(x)) / len(dq) for i in range(len(now))]  # noqa: E731
                                    if dq else list(now))
            pre = (mean(self.H, H), mean(self.Q, q), mean(self.S, s))
            self.Q.append(list(q))
            self.S.append(list(s))
        B = [max(0.0, v) for v in _finite(B, "B")]
        C = [float(v) for v in C]
        cs = {j: float(cstar[j]) for j in self.bills}
        R, H = _finite(R, "R"), _finite(H, "H")
        self.prices.add(paid, moved, fed, mergeout)
        mixed, merge = self.prices.vectors(B)
        self.looks.append((B, C))
        looks = list(self.looks)
        if len(looks) < FULL_LOOK:
            looks.append(self.start_look)
        full = full_flags(looks, len(B))
        n = last_level(B)
        st = {"mixed": mixed, "merge": merge, "push": push_vector(mixed, merge, full, n),
              "full": full, "n": n}
        self.H.append(list(H))
        hm = [sum(h[i] for h in self.H if i < len(h)) / len(self.H) for i in range(len(H))]
        self.hm.append(hm)
        h_old, dt = self.hm[0], len(self.hm) - 1
        reads = [_get(R, i) + _get(H, i) for i in range(max(len(R), len(H)))]
        out = {}
        for j, bill in self.bills.items():
            out[j] = bill.add(self.B, self.C, self.cs[j], B, C, cs[j], self.st, st, reads, paid, moved,
                              hm, h_old, dt, pre, None if Cn is None else [float(v) for v in Cn],
                              _finite(fed, "fed") if self.a3 else None,
                              _finite(mergeout, "mergeout") if self.a3 else None)
        self.B, self.C, self.cs, self.st = B, C, cs, st
        self.turns += 1
        return out, dict(st, hm=hm, w=self.prices.day.w)
