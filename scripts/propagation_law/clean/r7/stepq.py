"""Part r's BIC step test, returning where the last step is (same arithmetic as forward_r7.step_rate_ratio, cp mode, begin).
Returns dict(ratio, xs, q_since, q_stretch, n_since, n, nsteps) or None."""
import math
import os
TRIM = os.environ.get("V_TRIM", "0") == "1"

def intervals(sched, pre, X):
    if getattr(sched, "fb_raw", None) is not None:
        ends, begs = sorted(sched.fl_raw), sorted(a for a, _ in sched.fb_raw)
        if len(ends) == len(begs):
            allf = [(b, e[1]) for b, e in zip(begs, ends)]
        else:
            allf = [(b, 1.0) for b in begs]
    else:
        allf = sorted(sched.fl_raw)
    fs = [(a, by) for a, by in allf if pre["lo"] <= a < X]
    return [(fs[k][0] - fs[k - 1][0], fs[k][1], fs[k][0]) for k in range(1, len(fs)) if fs[k][0] > fs[k - 1][0]]

def sse(seg):
    rs = [s[1] / s[0] for s in seg]
    mu = sum(rs) / len(rs)
    return sum((r - mu) ** 2 for r in rs)

def find(sched, pre, X):
    iv = intervals(sched, pre, X)
    if len(iv) < 2:
        return None
    seg, nsteps = iv, 0
    while len(seg) >= 4:
        n, s0 = len(seg), sse(seg)
        if s0 <= 0:
            break
        best = min(range(2, n - 1), key=lambda k: sse(seg[:k]) + sse(seg[k:]))
        s1 = sse(seg[:best]) + sse(seg[best:])
        if not (s1 <= 0 or n * math.log(s0 / s1) > 2 * math.log(n)):
            break
        seg = seg[best:]
        nsteps += 1
    rate = lambda s: sum(x[1] for x in s) / sum(x[0] for x in s)
    if TRIM and nsteps and len(seg) >= 3:
        return dict(ratio=rate(seg) / rate(iv), xs=seg[1][2] - seg[1][0], q_since=rate(seg[1:]), q_stretch=rate(iv), n_since=len(seg) - 1, n=len(iv), nsteps=nsteps)
    return dict(ratio=rate(seg) / rate(iv), xs=seg[0][2] - seg[0][0], q_since=rate(seg), q_stretch=rate(iv), n_since=len(seg), n=len(iv), nsteps=nsteps)
