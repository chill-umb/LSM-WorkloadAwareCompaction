#!/usr/bin/env python3
"""Round 8 trial, rule (b) (owner 2026-10-25: "Go with b. follow RocksDB's least-overlap-first order on the per-file
snapshot at the change, with new files arriving during the drain"): the bytes the busy drain reads before the first
L0 merge after a change, from the step's own per-file snapshot at X (data/snap_x: each file's bytes and the files of
the level below its key range meets; D-34's inputs).

From RocksDB's code:
- kMinOverlappingRatio (version_set.cc SortFileByOverlappingRatio, used by LevelCompactionBuilder's file pick): a
  level's next merge takes its file with the least overlap per byte: the bytes of the level-below files whose key
  ranges meet it, over its size.
- A merge from level i writes its bytes and the overlapped level-(i+1) files out as new files over the same keys, and
  the overlapped files leave level i+1 (compaction_job.cc). So data pushed into a level arrives as new entries that
  absorb the files they overlap; an entry overlaps in the level below whatever its absorbed files overlapped. Its
  overlap per byte is lower than theirs (the same overlap below, more bytes): the cheap new files of d39.
- Which levels drain and how far: the T-K path's own walk (score_k_v2r7): the highest score first, a group at a
  common score s falls together, a group level re-pushes what a group level above pushes into it. Level i moves
  m_i(s) = (s_i(X) - s)^+ S_i + c_i(s); the walk reads sum_i [m_i + P_i(m_i)] bytes, P_i the overlap of level i's
  cheapest m_i bytes. ops = lambda x bytes (lambda_f until L0 holds 20 files, then lambda_s). L0 merges when its score
  (n_x + ops/t_F)/K' reaches s.
A formula on the snapshot, not a pick-by-pick replay (D-34): each level's entries are sorted once and summed; the
arrivals into a level are fixed by the pass before (pass 0: none), three passes.
mode "flat": P_i(m) = omega_i(window 0) x m, the law now (a self-check: it must give the T-K walk's own wait).
"""
import bisect
import json


class Snap:
    def __init__(self, path):
        d = json.load(open(path))
        lv = {int(k): v for k, v in d["levels"].items()}
        self.size = {i: {n: s for n, s, _ in rows} for i, rows in lv.items()}
        self.below = {i: {n: b for n, _, b in rows} for i, rows in lv.items()}

    def bytes(self, i):
        return sum(self.size.get(i, {}).values())

    def ovl(self, i, weights):
        """Bytes of level i+1 met by files of level i, each counted once at its highest weight."""
        best = {}
        for n, w in weights.items():
            for b in self.below.get(i, {}).get(n, ()):
                if w > best.get(b, 0.0):
                    best[b] = w
        nxt = self.size.get(i + 1, {})
        return sum(nxt.get(b, 0) * w for b, w in best.items())


class Pool:
    """Level i's entries, cheapest overlap per byte first, with running totals."""

    def __init__(self, entries):
        entries = [e for e in entries if e["size"] > 0]
        entries.sort(key=lambda e: e["ovl"] / e["size"])
        self.e, self.cs, self.co = entries, [0.0], [0.0]
        for e in entries:
            self.cs.append(self.cs[-1] + e["size"])
            self.co.append(self.co[-1] + e["ovl"])
        self.mean = self.co[-1] / self.cs[-1] if self.cs[-1] > 0 else 0.0

    def overlap(self, m):
        if m <= 0:
            return 0.0
        if m >= self.cs[-1]:
            return self.co[-1] + (m - self.cs[-1]) * self.mean
        k = bisect.bisect_right(self.cs, m) - 1
        e = self.e[k]
        return self.co[k] + e["ovl"] * (m - self.cs[k]) / e["size"]

    def taken(self, m):
        """[(entry, share pushed)] for the cheapest m bytes."""
        out, k = [], 0
        while m > 0 and k < len(self.e):
            e = self.e[k]
            f = min(1.0, m / e["size"])
            out.append((e, f))
            m -= e["size"] * f
            k += 1
        return out


def pool_of(snap, i, pushed):
    """Level i's entries at X plus the arrivals from level i-1's pushed entries (each with the share pushed)."""
    rem = {n: 1.0 for n in snap.size.get(i, {})}
    entries = []
    for up, f in pushed:
        want = {}
        for x, fx in up["xs"].items():
            for g in snap.below.get(i - 1, {}).get(x, ()):
                if g in rem:
                    want[g] = max(want.get(g, 0.0), f * fx)
        took = {}
        for g, w in want.items():
            a = min(rem[g], w)
            if a > 0:
                took[g] = a
                rem[g] -= a
        size = f * up["size"] + sum(a * snap.size[i][g] for g, a in took.items())
        entries.append(dict(size=size, ovl=snap.ovl(i, took), xs=took))
    for n, r in rem.items():
        if r > 0:
            entries.append(dict(size=r * snap.size[i][n], ovl=r * snap.ovl(i, {n: 1.0}), xs={n: r}))
    return Pool(entries)


def wait(sx, S, om, n_x, kh2, tF, lam_f, lam_s, t20, dp, snap=None, passes=None):
    """Operations from X to the first L0 merge. sx: {i: score at X} for i in 1..dp-1; om: window 0's omega."""
    lv = sorted(sx)
    if passes is None:
        passes = int(__import__("os").environ.get("DRAIN_PASSES", "3"))

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
        lo, hi = 0.0, top                                  # gap rises as s falls
        if gap(lo)[0] < 0:
            g, o, m = gap(lo)
            return o, m, lo
        for _ in range(60):
            mid = (lo + hi) / 2
            if gap(mid)[0] >= 0:
                lo = mid
            else:
                hi = mid
        g, o, m = gap(lo)
        return o, m, lo

    if snap is None:                                       # flat: window 0's omega
        P = {i: (lambda m, w=om[i]: w * m) for i in lv}
        return solve(P)[0]
    pushed = {i: [] for i in lv}
    d = 0.0
    for _ in range(passes + 1):
        pools = {}
        for i in lv:
            pools[i] = pool_of(snap, i, pushed.get(i - 1, []) if i > 1 else [])
        P = {i: pools[i].overlap for i in lv}
        d, m, s = solve(P)
        pushed = {i: pools[i].taken(m[i]) for i in lv}
    return d
