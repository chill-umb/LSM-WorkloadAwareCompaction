"""Range layer R0 + R1 (v1 §3.7) with band widths, round 5 (owner 2026-10-24: "go ahead with R0 for deep-level sizes
in busy trees"). Operator, 2026-10-24.

U'(a): bytes per op of distinct keys at age a (R1: f_i = U'(Δ_i)); piecewise power law through knots (Δ_k, f_k).
(R0)  size_i = s_{i-1} + U(Δ_i) - U(Δ_{i-1}),   s_i = Δ_i·f_i   (stale copies: entries whose newer copy sits above)
Summed over the pushing levels 1..m and the level below them (j = m+1, whose own edge does not move):
      Σ_{i=1..j} size_i = Σ_{i=0..m} s_i + U(Δ_j) - U(Δ_0)
so a change of the pushing levels' residences changes the tree's total bytes by Δ[Σ s_i - U(Δ_0)]: the stale copies
held. Data only moves down, so that change lands in level j.
"""
import math


def seg_int(a, b, x0, f0, e):
    """∫_a^b f0·(t/x0)^(-e) dt."""
    if b <= a:
        return 0.0
    la, lb = math.log(a / x0), math.log(b / x0)
    if abs(1 - e) < 1e-9:
        return f0 * x0 * (lb - la)
    return f0 * x0 * (math.exp((1 - e) * lb) - math.exp((1 - e) * la)) / (1 - e)


class Curve:
    """U' through knots (x_k, f_k), x increasing; flat below the first knot; the last segment's power law above."""

    def __init__(self, xs, fs):
        self.x, self.f = list(xs), list(fs)
        self.e = []
        for k in range(len(xs) - 1):
            self.e.append(math.log(fs[k] / fs[k + 1]) / math.log(xs[k + 1] / xs[k]) if fs[k + 1] < fs[k] else 0.0)
        self.e_tail = self.e[-1] if self.e else 0.0

    def seg(self, a):
        """(x0, f0, e) of the segment holding age a."""
        if a <= self.x[0]:
            return self.x[0], self.f[0], 0.0
        for k in range(len(self.x) - 1):
            if a <= self.x[k + 1]:
                return self.x[k], self.f[k], self.e[k]
        return self.x[-1], self.f[-1], self.e_tail

    def d(self, a):
        x0, f0, e = self.seg(a)
        return f0 * (a / x0) ** (-e)

    def U(self, a, b):
        """U(b) - U(a)."""
        if b < a:
            return -self.U(b, a)
        cuts = [a] + [x for x in self.x if a < x < b] + [b]
        tot = 0.0
        for lo, hi in zip(cuts, cuts[1:]):
            x0, f0, e = self.seg((lo + hi) / 2)
            tot += seg_int(lo, hi, x0, f0, e)
        return tot


def bisect_log(g, lo, hi, it=80):
    """Root of an increasing g on [lo, hi] in log space; None when g(lo) > 0 or g(hi) < 0."""
    if g(lo) > 0 or g(hi) < 0:
        return None
    a, b = math.log(lo), math.log(hi)
    for _ in range(it):
        m = (a + b) / 2
        if g(math.exp(m)) > 0:
            b = m
        else:
            a = m
    return math.exp((a + b) / 2)


def twin_bands(d0, f0, sizes, flows):
    """Solve the twin's band widths Δ_1..Δ_m from R0 with its measured sizes and flows (U' power law between
    consecutive knots). Returns the knots [Δ_0..Δ_m] or None."""
    xs, fs = [d0], [f0]
    for size, f in zip(sizes, flows):
        if f <= 0 or size <= 0:
            return None
        xp, fp = xs[-1], fs[-1]
        sp = xp * fp

        def g(x):
            e = math.log(fp / f) / math.log(x / xp) if f < fp else 0.0
            if f >= fp:
                return sp + f * (x - xp) - size
            return sp + seg_int(xp, x, xp, fp, e) - size
        x = bisect_log(g, xp * (1 + 1e-9), xp * 1e9)
        if x is None:
            return None
        xs.append(x)
        fs.append(f)
    return xs, fs


def step_bands(cv, d0, sizes):
    """At new sizes of the pushing levels, solve the band widths Δ'_1..Δ'_m on the twin's curve (R0); flows by R1."""
    if d0 <= 0:
        return None
    xs = [d0]
    for size in sizes:
        xp = xs[-1]
        sp = xp * cv.d(xp)
        if size <= sp:
            return None
        x = bisect_log(lambda x: sp + cv.U(xp, x) - size, xp * (1 + 1e-12), xp * 1e9)
        if x is None:
            return None
        xs.append(x)
    return xs, [cv.d(x) for x in xs]


def stale(cv, xs):
    """Σ_{i=0..m} s_i - U(Δ_0) up to a constant (U measured from the first knot)."""
    return sum(x * cv.d(x) for x in xs) - cv.U(cv.x[0], xs[0])


class Bands:
    """The twin window's range layer: knots Δ_0..Δ_m (L0: N̄_0·t_F, then the pushing levels 1..m), the curve U', and
    the age span W of level j = m+1 (the level below the pushing ones: still filling, or the last level). Level j's
    oldest data is fixed by history, so its span is W = A_end - A_m with A_m = Δ_0 + ... + Δ_m; solved from its size.
          size_j = s_m + U(W) - U(Δ_m)
    A change of the pushing levels' widths changes the bytes held in levels 1..j by
          Δtotal = Σ_{i=0..m} [Δs_i - U'(W)·ΔΔ_i] - ΔU(Δ_0)
    (no overwrites, U' flat: Δtotal = -ΔL0 bytes, byte conservation)."""

    def __init__(self, d0, f0, sizes, flows, size_j):
        sol = twin_bands(d0, f0, sizes, flows)
        self.sizes = list(sizes)
        self.ok = sol is not None and size_j > 0
        if not self.ok:
            return
        self.xs, fs = sol
        self.cv = Curve(self.xs, fs)
        xm = self.xs[-1]
        sm = xm * self.cv.d(xm)
        self.W = bisect_log(lambda x: sm + self.cv.U(xm, x) - size_j, xm * (1 + 1e-12), xm * 1e12) \
            if size_j > sm else None
        self.ok = self.W is not None

    def change(self, d0b, sizes2, uw=None, form="r0"):
        """(Δtotal over levels 1..j, new flows f'_1..f'_m) at the new L0 width and pushing-level sizes; None if R0
        has no solution there. uw: level j's growth per op (U'(W)); None: the curve's tail at W.
        form "r0": the full sum; "delay": level j sees the same stream ΔA_m later, Δsize_j = -U'(W)·ΔA_m."""
        sol = step_bands(self.cv, d0b, sizes2)
        if sol is None:
            return None
        xs2, fs2 = sol
        uw = self.cv.d(self.W) if uw is None else uw
        if form == "delay":
            dA = sum(x2 - x for x, x2 in zip(self.xs, xs2))
            return -uw * dA + sum(sizes2[i] - self.sizes[i] for i in range(len(sizes2))), fs2[1:]
        tot = sum(x2 * self.cv.d(x2) - x * self.cv.d(x) - uw * (x2 - x) for x, x2 in zip(self.xs, xs2))
        tot -= self.cv.U(self.xs[0], xs2[0])
        return tot, fs2[1:]


def alpha(cv, d_prev, d_i):
    """§3.4's pick factor with arrivals replacing copies inside the level (v1 §3.4 item 8), from the range layer: a key
    interval restarts empty when pushed and fills as Φ(t) = f_prev·min(t, Δ_{i-1}) + U(t) - U(Δ_{i-1}) (an entry that
    reached level i cannot be caught for Δ_{i-1}: any newer copy written sooner caught it above); it is pushed after
    P = Δ_i. α = mean/peak of Φ over [0, P]. No overwrites (U' flat): α = 1/2."""
    if d_i <= 0:
        return None
    f_prev = cv.d(d_prev)

    def phi(t):
        return f_prev * min(t, d_prev) + (cv.U(d_prev, t) if t > d_prev else 0.0)
    n = 200
    tot = 0.0
    for k in range(n):                              # midpoint rule, enough for a smooth concave curve
        tot += phi(d_i * (k + 0.5) / n)
    peak = phi(d_i)
    return (tot / n) / peak if peak > 0 else None
