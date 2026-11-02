#!/usr/bin/env python3
"""r7 part zd (owner, 2026-11-02: "adopt order"; trial clean/sub8_order, report 2026-10-31-sub-8order.md). Multi-change runs only.
#8's wait for the first L0 merge after a K change, with no per-file snapshot: the overlap of a level's cheapest m bytes comes from
level bytes at the change. Same solver as v2/drain_order.wait; no loop over merges, flushes or time.

q = m / B_i. P_i(m) = B_{i+1} q (1 + q) / 2 for q < 1, then the level mean per byte (overlap per byte uniform on [w/2, 3w/2], w = B_{i+1} / B_i).
Arrivals: level i-1 pushes m_up with overlap A = P_{i-1}(m_up). Level i then has two pools with this curve: new files, N = m_up + A bytes,
mean w A / N; the old rest, B_i - A bytes, mean w. The cheapest m bytes across them are found by equal marginal cost.

Derived from RocksDB: the mean (compaction_outputs.cc ShouldStopBefore), the floor of half the mean (version_set.cc
SortFileByOverlappingRatio) and the arrivals (compaction_job.cc). NOT derived: the shape between floor and mean, chosen after seeing
scores (the derived sawtooth missed #3 W4 at 241). forward_r7 sets DRAIN=flat and R18S=1 with this part, so that nothing reads a snapshot.
"""
PASSES = 2
BIS = 60
_BE = {}


def saw(q):
    """Share of a level's overlap in its cheapest share q of bytes."""
    return 1.0 if q >= 1.0 else q * (1.0 + q) / 2


def curve(B, mean):
    """P_i(m): overlap bytes of the cheapest m bytes of a level holding B bytes, level mean overlap per byte `mean`."""
    if B <= 0 or mean <= 0:
        return lambda m: 0.0
    O = mean * B

    def P(m):
        return 0.0 if m <= 0 else O * saw(m / B) + max(0.0, m - B) * mean
    return P


def mix_curve(B, mean, up_m, up_ovl):
    """Arrivals as two pools: up_m bytes absorb A = up_ovl of the level's bytes; the new bytes N = up_m + A hold A's overlap."""
    A = min(up_ovl, B)
    N = up_m + A
    Br = B - A
    if N <= 0:
        return lambda m: mean * B * saw(m / B) if B > 0 else 0.0
    mn = mean * A / N

    def take(t, b, w):                               # bytes and overlap of a pool (bytes b, mean w) whose marginal w (1/2 + q) is below t
        if b <= 0 or w <= 0:
            return b, 0.0
        if t <= w / 2:
            return 0.0, 0.0
        q = min(1.0, t / w - 0.5)
        return q * b, w * b * saw(q)

    def P(m):
        if m <= 0:
            return 0.0
        if m >= N + Br:
            return mn * N + mean * Br + (m - N - Br) * mean
        lo, hi = 0.0, 1.5 * max(mean, mn, 1e-9)      # every byte's marginal is below 1.5 w
        for _ in range(40):
            t = (lo + hi) / 2
            if take(t, N, mn)[0] + take(t, Br, mean)[0] < m:
                lo = t
            else:
                hi = t
        return take(hi, N, mn)[1] + take(hi, Br, mean)[1]
    return P


def ctx(bE):
    """Level bytes at the change (the deepest level's are not in wait's arguments)."""
    _BE["bE"] = bE


def wait(sx, S, om, n_x, kh2, tF, lam_f, lam_s, t20, dp, snap=None):
    """Operations from X to the first L0 merge. Signature = drain_order.wait's, installed over it with part zd."""
    assert snap is None
    lv = sorted(sx)
    bE = {i: sx[i] * S[i] for i in lv}
    bE[dp] = _BE["bE"][dp]

    def mean(i):
        return bE[i + 1] / bE[i] if bE[i] > 0 else 0.0

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
            o = ops(sum(m[i] + P[i](m[i]) for i in lv))
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

    P = {i: curve(bE[i], mean(i)) for i in lv}
    P_prev = P
    d, m, s = solve(P)
    for _ in range(PASSES):
        P = {}
        for i in lv:
            up = m.get(i - 1, 0.0) if i > 1 else 0.0
            uo = P_prev[i - 1](up) if i > 1 and i - 1 in lv else 0.0
            P[i] = mix_curve(bE[i], mean(i), up, uo)
        P_prev = P
        d, m, s = solve(P)
    return d
