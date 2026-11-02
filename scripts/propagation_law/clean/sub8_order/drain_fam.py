#!/usr/bin/env python3
"""sub8_order (owner, 2026-10-31): #8's first-merge wait with a closed-form overlap curve P_i(m) instead of the per-file snapshot.
Same solver as v2/drain_order.wait (loop over levels, fixed bisection); the curve comes from level bytes at X only.

DRAIN_FAM=saw  : P_i(m) = B_{i+1} (1 - sqrt(1 - m/B_i)), the "greedy sawtooth" curve (see report).
DF_OM          : bytes (default, level mean = B_{i+1}/B_i) | past2 (mean = 2 x the stretch's omega_pushed)
DF_ARR         : 0 (default) no arrivals | 1 B_i' = B_i + pushed-in in the curve | mix two pools (absorbed bytes become cheap new files)
DF_TRIV        : 0 (default) | 1 share tau_i of the cheapest bytes costs nothing, tau_i = trivial bytes / bytes moved in the stretch
"""
import json
import math
import os

FAM = os.environ.get("DRAIN_FAM", "")
TRACE = os.environ.get("DRAIN_TRACE", "")
OM = os.environ.get("DF_OM", "bytes")
ARR = os.environ.get("DF_ARR", "0")
TRIV = os.environ.get("DF_TRIV", "0")
PASSES = int(os.environ.get("DF_PASSES", "2"))
BIS = int(os.environ.get("DF_BIS", "60"))


def trace(snap_path, sx, w0, xs, bE, n_x, kh2, lam_f, lam_s, t20):
    L = xs["levels"]
    rec = dict(snap=str(snap_path), sx=sx, S=w0.S, om=w0.om, n_x=n_x, kh2=kh2, tF=w0.tF, lam_f=lam_f, lam_s=lam_s, t20=t20, dp=w0.dp,
               bE=bE, l0s=w0.l0s, kin=w0.kin, ops=xs["ops"], F=[l["files_end"] for l in L], s=[l["s"] for l in L], o=[l["o"] for l in L],
               x=[l["x"] for l in L], fp=[l["f_per_op"] for l in L], oma=[l["omega"] for l in L], tb=[l["trivial_bytes"] for l in L])
    with open(f"{TRACE}.{os.getpid()}", "a") as f:
        f.write(json.dumps(rec) + "\n")


_F = float(FAM[1:]) / 100 if FAM.startswith("f") else 0.5


def saw(q):
    """Share of a level's overlap in its cheapest share q of bytes. saw: 1 - sqrt(1 - q) (age uniform, bytes by age). uni: q(1 + q)/2 (ratio uniform on [w/2, 3w/2])."""
    if q >= 1.0:
        return 1.0
    if FAM == "uni":
        return q * (1.0 + q) / 2
    if FAM == "saw":
        return 1.0 - math.sqrt(1.0 - q)
    if FAM == "mid":                                 # after seeing single-run #8b: halfway between saw and uni; no derivation of its own
        return (q * (1.0 + q) / 2 + 1.0 - math.sqrt(1.0 - q)) / 2
    if FAM == "mean":                                # sensitivity: level mean, no order
        return q
    if FAM == "half":                                # sensitivity: the floor alone
        return q / 2
    return q * (_F + (1.0 - _F) * q)                 # sensitivity: f<percent>, ratio uniform on [f w, (2-f) w]


def curve(i, B, mean, tau, inn=0.0):
    """P_i(m): overlap bytes of the cheapest m bytes of a level holding B bytes, level mean overlap per byte `mean`."""
    if B <= 0 or mean <= 0:
        return lambda m: 0.0
    O = mean * B
    if FAM in ("rollf", "rollfa"):                   # ratio uniform from the stretch's own pushed overlap per byte (floor) up: L(q) = q (phi + (1 - phi) q)
        rec = _CTX["L"][i]
        w = rec["omega_pushed"] if FAM == "rollf" else rec["omega"]
        phi = min(1.0, w / mean) if w is not None else 0.5

        def Pr(m):
            if m <= 0:
                return 0.0
            q = m / B
            return O * q * (phi + (1 - phi) * q) if q < 1 else O + (m - B) * mean
        return Pr

    def P(m):
        if m <= 0:
            return 0.0
        if tau > 0:                                  # zero-overlap bytes go first (trivial moves)
            m = max(0.0, m - tau * B)
            if m <= 0:
                return 0.0
            b = B * (1 - tau)
            if b <= 0:
                return m * mean
            return O * saw(m / b) + max(0.0, m - b) * mean
        return O * saw(m / B) + max(0.0, m - B) * mean
    return P


def mix_curve(B, mean, up_m, up_ovl):
    """Arrivals as two pools. A level gets up_m bytes that absorb A = up_ovl of its bytes: new bytes N = up_m + A hold A's overlap, so their
    mean is mean A/N; the old rest B - A keeps its curve. Cheapest m across both pools: equal-marginal threshold, fixed bisection."""
    A = min(up_ovl, B)
    N = up_m + A
    Br = B - A
    if N <= 0:
        return lambda m: mean * B * saw(m / B) if B > 0 else 0.0
    mn = mean * A / N

    def take(t, b, w):                               # bytes and overlap of a pool (bytes b, mean w) whose marginal is below t; marginal = w/(2 sqrt(1-q))
        if b <= 0 or w <= 0:
            return b, 0.0
        if t <= w / 2:
            return 0.0, 0.0
        q = (1 - (w / (2 * t)) ** 2) if FAM == "saw" else min(1.0, t / w - 0.5)   # marginal: w/(2 sqrt(1-q)) (saw), w (1/2 + q) (uni)
        return q * b, w * b * saw(q)

    def P(m):
        if m <= 0:
            return 0.0
        if m >= N + Br:
            return mn * N + mean * Br + (m - N - Br) * mean
        lo, hi = 0.0, 1.5 * max(mean, mn, 1e-9)      # uni: every byte's marginal is below 1.5 w
        if FAM == "saw":                             # saw's top marginal has no bound: a capped bracket
            for _ in range(40):
                if take(hi, N, mn)[0] + take(hi, Br, mean)[0] >= m:
                    break
                hi *= 2
        for _ in range(40):
            t = (lo + hi) / 2
            if take(t, N, mn)[0] + take(t, Br, mean)[0] < m:
                lo = t
            else:
                hi = t
        return take(hi, N, mn)[1] + take(hi, Br, mean)[1]
    return P


_CTX = {}


def ctx(xs, bE):
    """The change's own before-stretch record and level bytes at X (the deepest level's bytes and trivial shares are not in wait's arguments)."""
    _CTX["L"], _CTX["bE"] = xs["levels"], bE


def wait(sx, S, om, n_x, kh2, tF, lam_f, lam_s, t20, dp, snap=None, passes=None):
    """Operations from X to the first L0 merge (v2/drain_order.wait's solve, curve from level bytes). Signature = drain_order.wait's;
    installed over it when DRAIN_FAM is set, with DRAIN=flat so that snap is None and nothing reads a per-file snapshot."""
    assert snap is None
    lv = sorted(sx)
    L = _CTX["L"]
    bE = {i: sx[i] * S[i] for i in lv}
    bE[dp] = _CTX["bE"][dp]

    def mean(i):
        if OM == "past2":
            return 2 * om[i]
        return bE[i + 1] / bE[i] if bE[i] > 0 else 0.0

    def tau(i):
        if TRIV == "0":
            return 0.0
        tb, s = L[i]["trivial_bytes"] or 0.0, L[i]["s"] or 0.0
        return tb / (tb + s) if tb + s > 0 else 0.0

    def solve(P):
        def moved(s):
            m = {}
            for i in lv:
                own = max(0.0, sx[i] - s) * S[i]
                c = 0.0
                if i - 1 in sx:
                    u = min(sx[i], sx[i - 1])
                    if s < u:
                        c = max(0.0, m[i - 1] - moved_at(i - 1, u))
                m[i] = own + c
            return m

        cache = {}

        def moved_at(i, s):
            if (i, s) not in cache:
                cache[(i, s)] = moved(s)[i]
            return cache[(i, s)]

        def ops(R):
            return R * lam_f if R * lam_f <= t20 else t20 + (R - t20 / lam_f) * lam_s

        def gap(s):
            m = moved(s)
            R = sum(m[i] + P[i](m[i]) for i in lv)
            o = ops(R)
            return (n_x + o / tF) / kh2 - s, o, m

        top = max(sx.values())
        g, o, m = gap(top)
        if g >= 0:
            return 0.0, m, top
        lo, hi = 0.0, top
        if gap(lo)[0] < 0:
            g, o, m = gap(lo)
            return o, m, lo
        for _ in range(BIS):
            mid = (lo + hi) / 2
            if gap(mid)[0] >= 0:
                lo = mid
            else:
                hi = mid
        g, o, m = gap(lo)
        return o, m, lo

    P = {i: curve(i, bE[i], mean(i), tau(i)) for i in lv}
    P_prev = P
    d, m, s = solve(P)
    if ARR != "0":
        for _ in range(PASSES):
            P = {}
            for i in lv:
                up = m.get(i - 1, 0.0) if i > 1 else 0.0
                if ARR == "1":
                    Bp = bE[i] + up
                    mn = bE[i + 1] / Bp if Bp > 0 else 0.0
                    P[i] = curve(i, Bp, mn, tau(i))
                else:
                    uo = P_prev[i - 1](up) if i > 1 and i - 1 in lv else 0.0
                    P[i] = mix_curve(bE[i], mean(i), up, uo)
            P_prev = P
            d, m, s = solve(P)
    return d


if FAM:
    if os.environ.get("DRAIN") != "flat":
        raise SystemExit("DRAIN_FAM needs DRAIN=flat")
    import drain_order
    drain_order.wait = wait
