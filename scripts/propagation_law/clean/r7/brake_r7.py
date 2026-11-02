#!/usr/bin/env python3
"""[r7 part o, 2026-10-28 (adopted 12:12 UTC for the multi-change runs, SCORE=own; owner): crossings() -- only the K'
path's crossing cuts a window, and braked pieces predict from the run's own before-stretch (part q's form) with the
unbraked window's om5 inflow. Single-change runs (D-33, twin-differenced) keep d,p,c: their measure needs the old-K path.]
[clean/r2 copy of r1/brake.py, 2026-10-27: projected_predict takes kin, the old K (default K0 = r1 exactly).]
sub_brake (AFTER SEEING; subagent brake, 2026-10-27): forecast of the 64 GiB brake for the forward law, cases 1-2.

RocksDB (lib/rocksdb):
- version_set.cc:3656-3740 EstimateCompactionBytesNeeded: L0 due (files >= K or bytes >= base) -> L0 bytes + base
  level bytes; then each level from base down: size + carry above its target -> carry = the excess, est += carry x
  (next level's size / (size + carry in) + 1). Recomputed for every new version (version_set.cc:4035).
- column_family.cc:1010-1045 GetWriteStallConditionAndCause: est >= hard (128 GiB) -> stop; est >= soft (64 GiB) ->
  delayed, cause pending bytes (checked after the 20-file L0 slowdown).  column_family.cc:1133-1158: the delay token
  comes from SetupDelay (:872-933): the delayed rate is cut x0.8 at each recalculation where the estimate did not fall
  and raised x1.25 where it fell, so writes are paced to keep the estimate at 64 GiB (the brake holds it there).
The law's est() (score_k_v2r18.py:349-360) is the same sum with L0 due at N_b files.

Forecast (D-36: only the run's own recent windows; nothing from a twin or another run):
- the state at op X + t: the pre window's mean state (at X - L/2) poured forward, BK4' (data only moves down;
  score-first serving keeps it as high as the caps allow; Win.pour's rule), caps at the water level sigma S_i (L1's
  cap keeps its batch sawtooth, scaled with the batch), the last level takes the rest.
- the bytes poured: the window's own net growth per op (its levels' growth, end of window minus end of the window
  before), less the extra copies the receiving level drops as it fills (BRAKE_GROW=g7, default): the receiving
  level j's merges drop d_j(z) = d_j0 z / z_j0 bytes per op (window's own d_j0 = s + o - x of the merges into j):
  a merge into j reads o = omega s with omega proportional to size_j (G7, the law's own overlap rule) and drops the
  window's own share of what it reads; once full it keeps dropping at its cap size. g7c (BRAKE_GROW_NEXT=1, default):
  a deeper level that then receives drops what its pushed bytes find at the window's own hit rate per stored byte,
  c_hit = d_j0 / (f_in,j0 z_j0) (the same uniform key density G7 assumes). BRAKE_GROW=const: no such term.
- K = 4 path: sigma = N_fire / K^ (L0-bound) until est(state) >= 64 GiB; from then sigma_pend(t), the water level
  that holds the estimate at 64 GiB (bisection), N_b = sigma K^ (the law's pending-bound branch, §13.4).
- K' path: sigma' = the law's own L0-bound water level at K' from the pre window (the hold rule's fixed point with
  the brake switched off), N_b' = sigma' K^'; after its own crossing sigma'_pend(t), N_b' = sigma' K^'.
"""
import copy
import math
import os
import sys

HORIZON = 40_000_000
GROW = os.environ.get("BRAKE_GROW", "g7")     # g7 (default) | const
GROW_NEXT = os.environ.get("BRAKE_GROW_NEXT", "1") == "1"   # g7c (default 1; 0 = g7 alone, see Forecast.inflow)
STEP = 250_000


def lawmod(win):
    return sys.modules[type(win).__module__]


def l0bound_predict(win, k2, x0):
    """The law's prediction from win with the 64 GiB brake switched off (P_SOFT -> inf): its L0-bound after-state."""
    lm = lawmod(win)
    old = lm.P_SOFT
    try:
        lm.P_SOFT = float("inf")
        return win.predict(k2, x0)
    finally:
        lm.P_SOFT = old


class Forecast:
    def __init__(self, win, pre, prev, k2, L=None):
        self.w = w = win
        self.m = lm = lawmod(win)
        self.dp = w.dp
        self.L = L if L is not None else pre["ops"]
        Lv, Pv = pre["levels"], prev["levels"]
        self.g = max(0.0, (sum(Lv[i]["bytes_end"] for i in range(1, len(Lv)))
                           - sum(Pv[i]["bytes_end"] for i in range(1, len(Pv)))) / pre["ops"])
        self.drop = [((Lv[i]["s"] or 0) + (Lv[i]["o"] or 0) - (Lv[i]["x"] or 0)) / pre["ops"] for i in range(len(Lv))]
        self.kh = min(w.kin, w.B / w.F0)
        self.kh2 = min(k2, w.B / w.F0)
        self.sig = w.nfire / self.kh
        self.saw = max(0.0, w.size[1] - self.sig * w.B)
        self.e_now = lm.est(w.size, w.S, w.nfire * w.F0)
        self._inf = {}
        cp = self.caps(self.sig, self.kh)
        self.j0 = next((i for i in range(1, self.dp) if w.size[i] < cp[i] - 1.0), self.dp)
        self.c_hit = (self.drop[self.j0 - 1] / (w.f[self.j0 - 1] * w.size[self.j0])
                      if self.j0 < self.dp and w.f[self.j0 - 1] > 0 and w.size[self.j0] > 0 else None)
        p = l0bound_predict(win, k2, pre)
        self.nf2 = p.get("nb2") if p.get("nb2") is not None else w.nfire
        self.s2 = p.get("sig2") if p.get("sig2") is not None else self.sig
        self.t4 = self.cross(self.sig, self.kh)
        self.t2 = self.cross(self.s2, self.kh2)

    def caps(self, s, kh):
        w = self.w
        c = [0.0] + [s * w.S[i] for i in range(1, self.dp + 1)]
        c[1] += self.saw * (s * kh) / w.nfire
        return c

    def pour(self, inflow, caps):
        dp = self.dp
        z = list(self.w.size)
        avail = inflow
        for i in range(1, dp):
            if z[i] > caps[i]:
                avail += z[i] - caps[i]
                z[i] = caps[i]
        for i in range(1, dp):
            take = min(max(0.0, caps[i] - z[i]), max(0.0, avail))
            z[i] += take
            avail -= take
        z[dp] += avail
        return z

    def inflow(self, t, s, kh):
        """Net bytes added from X - L/2 to X + t along the L0-bound path at water level s (Euler, STEP ops)."""
        if GROW != "g7":
            return self.g * (t + self.L / 2)
        key = (round(s, 9), round(kh, 9))
        tab = self._inf.get(key)
        if tab is None:
            dp, caps = self.dp, self.caps(s, kh)
            tab, I = [0.0], 0.0
            for _ in range(int((HORIZON + self.L / 2) // STEP) + 2):
                z = self.pour(I, caps)
                j = next((i for i in range(1, dp) if z[i] < caps[i] - 1.0), dp)
                g = self.g
                jr = self.j0
                if jr < dp and self.w.size[jr] > 0 and self.drop[jr - 1] > 0:
                    # G7: the window's receiving level jr drops copies in proportion to its size (also once full)
                    g -= self.drop[jr - 1] * (z[jr] / self.w.size[jr] - 1)
                if GROW_NEXT and jr < j < dp:
                    # g7c: a deeper level now receives: its own measured drops scale with its size; with none
                    # measured, its pushed bytes find copies at the window's hit rate per stored byte (c_hit)
                    if self.drop[j - 1] > 0 and self.w.size[j] > 0:
                        g -= self.drop[j - 1] * (z[j] / self.w.size[j] - 1)
                    elif self.c_hit is not None:
                        f_in = max(0.0, self.w.f[jr - 1] - self.drop[jr - 1] * z[jr] / self.w.size[jr])
                        g -= f_in * self.c_hit * z[j]
                I += max(0.0, g) * STEP
                tab.append(I)
            self._inf[key] = tab
        x = (t + self.L / 2) / STEP
        k = max(0, min(int(x), len(tab) - 2))
        return tab[k] + (tab[k + 1] - tab[k]) * (x - k)

    def state(self, t, s, kh, s_path=None):
        """Projected mean sizes at op X + t, caps at water level s; the bytes poured follow the path's own L0-bound
        water level s_path (the inflow so far does not depend on where the brake put the levels)."""
        sp = s if s_path is None else s_path
        return self.pour(self.inflow(t, sp, kh), self.caps(s, kh))

    def e(self, t, s, kh, s_path=None):
        return self.m.est(self.state(t, s, kh, s_path), self.w.S, s * kh * self.w.F0)

    def cross(self, s, kh):
        """First t (ops after X, from -L/2) where the L0-bound path's estimate reaches 64 GiB; None past HORIZON."""
        P = self.m.P_SOFT
        lo = -self.L / 2
        if self.e(lo, s, kh) >= P:
            return lo
        hi = lo
        while self.e(hi, s, kh) < P:
            hi += 1_000_000
            if hi > HORIZON:
                return None
        a = hi - 1_000_000
        for _ in range(40):
            mid = (a + hi) / 2
            if self.e(mid, s, kh) >= P:
                hi = mid
            else:
                a = mid
        return hi

    def sig_pend(self, t, kh, s_max):
        """The water level that holds the estimate at 64 GiB at X + t (<= the path's L0-bound s_max)."""
        P = self.m.P_SOFT
        lo, hi = 0.25, s_max
        if self.e(t, hi, kh, s_max) < P:
            return hi
        for _ in range(50):
            mid = (lo + hi) / 2
            if self.e(t, mid, kh, s_max) < P:
                lo = mid
            else:
                hi = mid
        return lo

    def path(self, t, which):
        """(water level, files per L0 merge, braked?) of the K = 4 path ('4') or the K' path ('2') at X + t."""
        s, kh, tc = (self.sig, self.kh, self.t4) if which == "4" else (self.s2, self.kh2, self.t2)
        if tc is None or t < tc:
            return s, s * kh, False
        sp = self.sig_pend(t, kh, s)
        return sp, sp * kh, True


def crossings(fc):
    """The crossings that cut a window. r7 part o (SCORE=own, owner 2026-10-28: the law's after-state against the run's
    own next stretch): after the change the run follows K' only, so only the K' path's crossing (t2) cuts; the old-K
    path (t4) is the no-change forecast, which only a twin-differenced measure (D-33) compares with."""
    return (fc.t2,) if "o" in fc.m.R7_ACTIVE else (fc.t4, fc.t2)


def g_x(c3, tt, q):
    """L0A=3's g(tau) (score_k_v2r18.py, the x-rule): writes held during an L0 merge of tau seconds."""
    delayed = c3["r0"] <= 1.15 * 8388608 / q
    G_ = 1 / 0.64
    z = tt * c3["r0"] / c3["M"]
    return math.log(1 + (G_ - 1) * z) / math.log(G_) if delayed else z


def projected_predict(fc, pre, prev, cyc, rs, c03, l1, t, k2, tm, mode, kin=None):
    """The law's prediction at op X + tm from the K = 4 path's projected state (sizes poured, N_b = sigma K^).
    mode p2: the K' after-state forced to the forecast's K' path; p1: the law's own K' solution."""
    m = fc.m
    w0 = fc.w
    if "q" in m.R7_ACTIVE or "o" in m.R7_ACTIVE:   # r7 part q: the change from the run's own before-stretch (D-36: the
        w = m.Win(pre, l1, t, cyc, rs, prev, kin=m.K0 if kin is None else kin)   # law predicts the run's own move;
        if not w.ok or not w.busy:                 # the old-K forecast's drift is not the twin's)
            return None
        w.c03 = c03
        if "o" in m.R7_ACTIVE and getattr(fc, "_fwd", None) is not None:
            w.fwd_inflow = fc._fwd                 # r7 part o: om5's inflow, as the unbraked window's own prediction
        if "d" in m.R7_ACTIVE:
            w.r7 = m.R7_ACTIVE
            w._r7_piece = True
        s2, nf2, _ = fc.path(tm, "2")
        w._force2 = (nf2, s2)
        p = w.predict(k2, pre)
        if not p.get("busy"):
            return None
        return {"2": p["dn0"], "3": p["dbatch"], "4": p["dw0"], "5": p["dom"].get(1), "5b": p["dx"].get(1)}
    s4, nf4, b4 = fc.path(tm, "4")
    z = fc.state(tm, s4, fc.kh, fc.sig)
    x = copy.deepcopy(pre)
    for i in range(1, fc.dp + 1):
        x["levels"][i]["bytes_mean"] = z[i]
    x["l0l1"]["files_in_mean"] = nf4
    c = dict(cyc) if cyc else None
    if c is not None and b4:
        c["_median_files"] = nf4
    w = m.Win(x, l1, t, c, rs, prev, kin=m.K0 if kin is None else kin)   # r2: the no-change path runs at the old K
    if not w.ok or not w.busy:
        return None
    # the K = 4 path's own L0 merge at time t: N_b = nf4 files, L1 at sigma S_1 (BK3); its writes held (x-rule)
    if w.l0s:
        w.l0s *= nf4 / w0.nfire
    if w.l0o is not None:
        w.l0o *= s4 / fc.sig
    if c03 and c03.get("tau") and w0.l0s and w0.l0o is not None:
        b_pre = w0.l0s + w0.l0o
        b_4 = (w.l0s or 0.0) + (w.l0o or 0.0)
        tau4 = c03["tau"] * b_4 / b_pre
        g0 = g_x(c03, c03["tau"], w0.q)
        x4 = c03["x"] * g_x(c03, tau4, w0.q) / g0 if g0 > 0 else c03["x"]
        w.c03 = dict(c03, tau=tau4, x=x4)
        if "p" in m.R7_ACTIVE and "d" in m.R7_ACTIVE:  # r7 part p: both paths are braked here, merges begin at the cap
            w.r7, w._r7_piece = m.R7_ACTIVE, True
            cap = m.DELAY_CAP / w0.q
            if c03["r0"] > 1.15 * cap:                 # the pre window's merges began undelayed: the K path's x at the cap
                G_ = 1 / 0.64
                x4 = math.log(1 + (G_ - 1) * tau4 * cap / c03["M"]) / math.log(G_)
                w.c03 = dict(c03, tau=tau4, x=x4, r0=cap)
    else:
        w.c03 = c03
    if mode == "p2":
        s2, nf2, _ = fc.path(tm, "2")
        w._force2 = (nf2, s2)
    p = w.predict(k2, pre)
    if not p.get("busy"):
        return None
    return {"2": p["dn0"], "3": p["dbatch"], "4": p["dw0"], "5": p["dom"].get(1), "5b": p["dx"].get(1)}
