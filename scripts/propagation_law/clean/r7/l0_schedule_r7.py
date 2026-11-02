#!/usr/bin/env python3
"""[clean/r3, 2026-10-27 (owner: "Add the rolling fit to r3"): ROLL_L = 5M by default; ROLL_L = 0 is v2/l0_schedule.py
exactly (self-checked). Earlier note: ROLL_L (ops) > 0 fits the flush line, the flush length and the merge speed over
the last ROLL_L ops before X only (rolling, D-36) instead of everything since op 1M; ROLL_L=0 = v2/l0_schedule.py.]
Round 9 (owner 2026-10-25: "Accept both together"): L0's merge schedule after a change in a calm tree, shared by
#4's whole-merge rule and #10's L0 storage term (subagents 4 and 10, checked by the operator; report sections "#4 ..."
and "#10 ..."). Ported from diag_2026-10-25/agent4/a05_fix.py (FLUSH=line, SIGMA=0, PER_BYTES=law).

From RocksDB's code: an L0 -> L1 merge starts at the flush that brings L0's score max(n/K, L0 bytes/B) to 1, i.e. at
K_e = min(K, ceil(B/F0)) files (version_set.cc ComputeCompactionScore), and takes every L0 file present
(GetOverlappingL0Files); one merge runs at a time; the files leave L0 when it ends.
Inputs, all from the step run before X (data/l0_events/<run>.json: flush begins and ends, L0 merge records):
- flush j after X ends at line(i_last + j) + d: the straight line through the memtable switches (flush begins) from
  op 1M to X, plus the mean flush length before X;
- the first merge at X if n_x >= K_e (all n_x files), else at flush K_e - n_x; then one every K_e flushes; a merge
  due while the slot is busy starts when it frees, with every file then present;
- a merge of n files lasts r_tau (n F0 + o_bar) ops (r_tau: ops per byte read by the L0 merges before X, o_bar their
  mean L1 bytes read).
"""
import bisect
import json
import math
import os

ROLL_L = int(os.environ.get("ROLL_L", "5000000"))   # r3: rolling fit over the last 5M ops (0 = round 9 as built)


def _fit(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sl = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    return sl, my - sl * mx


class Schedule:
    def __init__(self, path, X, n_x, F0, end):
        ex = json.load(open(path))
        f = [a for a, _ in ex["flushes"] if a < X]
        jobs = [j for j in ex["l0jobs"] if not j[5] and j[0] is not None and j[1] < X and j[6] != 0]
        if ROLL_L > 0:
            rj = [j for j in jobs if j[1] >= X - ROLL_L]
            jobs = rj if rj else jobs[len(jobs) // 2:] or jobs
        else:
            jobs = jobs[len(jobs) // 2:] or jobs
        s, o = sum(j[2] for j in jobs), sum(j[3] for j in jobs)
        self.o_bar, self.r_tau = o / len(jobs), sum(j[1] - j[0] for j in jobs) / (s + o)
        lo_fit = max(1_000_000, X - ROLL_L) if ROLL_L > 0 else 1_000_000
        bb = [a for a, _ in ex["flush_begins"] if lo_fit <= a < X]
        if len(bb) < 3:
            bb = [a for a, _ in ex["flush_begins"] if 1_000_000 <= a < X][-3:]
        sl, c = _fit(list(range(len(bb))), bb)
        fe = [a for a, _ in ex["flushes"]]
        durs = []
        for a, _ in ex["flush_begins"]:
            i = bisect.bisect_left(fe, a)
            if i < len(fe) and lo_fit <= a < X:
                durs.append(fe[i] - a)
        d_end = sum(durs) / len(durs)
        self.flushes, j = [], 0
        while True:
            j += 1
            mu = sl * (len(bb) - 1 + j) + c + d_end
            if mu >= end:
                break
            self.flushes.append(mu)
        self.X, self.n_x, self.F0, self.f_last = X, n_x, F0, f[-1]
        self.sl, self.c0, self.d_end, self.L, self.end = sl, c, d_end, len(bb), end
        self.jend = len(self.flushes) + 1          # flush numbers 1 .. jend - 1 exist
        self._pc = {}
        self.fl_raw = ex["flushes"]                # r7 part r: [(end op, bytes)], read by forward_r7.recent_q only
        self.fb_raw = ex["flush_begins"]           # r7 part r: [(memtable switch op, ...)], forward_r7.step_rate_ratio

    def merges(self, ke):
        """[(end op, files)] of the L0 -> L1 merges after X with trigger K_e = ke."""
        tau = lambda n: self.r_tau * (n * self.F0 + self.o_bar)
        ms, L0, busy = [], self.n_x, self.X
        if L0 >= ke:
            ms.append((self.X + tau(L0), L0))
            busy, L0 = self.X + tau(L0), 0
        for mu in self.flushes:
            if mu < self.X:
                continue
            while L0 >= ke and busy <= mu:
                ms.append((busy + tau(L0), L0))
                busy += tau(L0)
                L0 = 0
            L0 += 1
            if L0 >= ke and busy <= mu:
                ms.append((mu + tau(L0), L0))
                busy, L0 = mu + tau(L0), 0
        return ms

    def window_bytes(self, ke, lo, hi, F0w, B):
        """#4: bytes the L0 -> L1 merges ending in [lo, hi) write, n F0 + B each (the law's bytes per merge)."""
        return sum(n * F0w + B for e, n in self.merges(ke) if lo <= e < hi)

    def files_at(self, ke, op):
        """#10: L0's files at op: n_x + flushes ended - files of the merges ended (a running merge's files count)."""
        fl = sum(1 for mu in self.flushes if self.X <= mu <= op)
        return self.n_x + fl - sum(n for e, n in self.merges(ke) if e <= op)

    # ---- r7 part zc (trial, 2026-10-31): the same schedule with no loop over merges or flushes. Flush ends are a straight line, so counts and sums over them are arithmetic. A
    # merge of n files lasts tau(n). Once merges start at a flush with K_e files and tau(K_e) <= K_e flush spacings, they repeat every K_e flushes: one arithmetic piece. Before
    # that, or if a merge outlasts its K_e flushes, up to P exact steps, then one level (n, every tau(n) ops) to the end. ----
    def _mu(self, j):
        return self.sl * (self.L - 1 + j) + self.c0 + self.d_end

    def _j_ge(self, op):
        """The first flush number whose end is at or after op."""
        return max(1, math.ceil((op - self.c0 - self.d_end) / self.sl - (self.L - 1) - 1e-12))

    def pieces(self, ke, P=8):
        """[(files, first end op, ops between ends, merges)]."""
        if ke in self._pc:
            return self._pc[ke]
        tau = lambda n: self.r_tau * (n * self.F0 + self.o_bar)
        out, L0, busy, j = [], self.n_x, self.X, self._j_ge(self.X)
        done = False
        if L0 >= ke:
            out.append((L0, self.X + tau(L0), 0.0, 1))
            busy, L0 = self.X + tau(L0), 0
        for _ in range(P):
            jb = min(self._j_ge(busy), self.jend)
            if jb > j:
                L0, j = L0 + jb - j, jb
            if j >= self.jend:
                done = True
                break
            if L0 >= ke:                                # the slot frees with K_e or more files present
                out.append((L0, busy + tau(L0), 0.0, 1))
                busy, L0 = busy + tau(L0), 0
                continue
            js = j + ke - L0 - 1                        # the flush that brings L0 to K_e
            if js >= self.jend:
                done = True
                break
            e = self._mu(js) + tau(ke)
            out.append((ke, e, 0.0, 1))
            busy, L0, j = e, 0, js + 1
            if tau(ke) <= ke * self.sl:                 # from here every K_e flushes
                cnt = (self.jend - 1 - js) // ke
                if cnt > 0:
                    out.append((ke, e + ke * self.sl, ke * self.sl, cnt))
                done = True
                break
        if not done and out:                            # merges outlast their flushes: one level to the end
            n = out[-1][0]
            de = tau(n)
            cnt = int(max(self.end - busy, 0.0) // de)
            if cnt > 0:
                out.append((n, busy + de, de, cnt))
        self._pc[ke] = out
        return out

    @staticmethod
    def _range(e0, de, cnt, a, b, left_open=False, right_closed=False):
        """k0, k1 with e0 + k de in the interval, k0 <= k < k1, 0 <= k < cnt."""
        if de <= 0:
            e = e0
            ok = (e > a if left_open else e >= a) and (e <= b if right_closed else e < b)
            return (0, 1) if ok and cnt else (0, 0)
        k0 = 0 if a == -math.inf else math.ceil((a - e0) / de)
        if left_open and a != -math.inf and e0 + k0 * de <= a:
            k0 += 1
        k1 = math.ceil((b - e0) / de)
        if right_closed and e0 + k1 * de <= b:
            k1 += 1
        return max(k0, 0), min(k1, cnt)

    def sum_f(self, ke, lo, hi, f):
        """Sum of f(n) over the merges ending in [lo, hi)."""
        tot = 0.0
        for n, e0, de, cnt in self.pieces(ke):
            k0, k1 = self._range(e0, de, cnt, lo, hi)
            if k1 > k0:
                tot += (k1 - k0) * f(n)
        return tot

    def count_n(self, ke, lo, hi):
        """(merges, their files) ending in [lo, hi)."""
        m_, t_ = 0, 0
        for n, e0, de, cnt in self.pieces(ke):
            k0, k1 = self._range(e0, de, cnt, lo, hi)
            if k1 > k0:
                m_, t_ = m_ + k1 - k0, t_ + (k1 - k0) * n
        return m_, t_

    def window_bytes_c(self, ke, lo, hi, F0w, B):
        return self.sum_f(ke, lo, hi, lambda n: n * F0w + B)

    def files_at_c(self, ke, op):
        fl = max(0, min(self.jend, self._first_after(op)) - self._j_ge(self.X))
        done = 0
        for n, e0, de, cnt in self.pieces(ke):
            k0, k1 = self._range(e0, de, cnt, -math.inf, op, right_closed=True)
            if k1 > k0:
                done += (k1 - k0) * n
        return self.n_x + fl - done

    def _first_after(self, op):
        """The first flush number whose end is after op."""
        j = self._j_ge(op)
        return j + 1 if self._mu(j) <= op else j

    def mean_files_c(self, ke, lo, hi):
        """L0's mean files over (lo, hi): files at lo, plus each flush's and merge's share of the stretch."""
        j0, j1 = max(self._j_ge(self.X), self._first_after(lo)), min(self._j_ge(hi), self.jend)
        tot = 0.0
        if j1 > j0:                                     # flushes j0 .. j1 - 1: sum (hi - mu_j), arithmetic
            k = j1 - j0
            tot += k * (hi - self._mu(j0)) - self.sl * k * (k - 1) / 2
        for n, e0, de, cnt in self.pieces(ke):
            k0, k1 = self._range(e0, de, cnt, lo, hi, left_open=True)
            if k1 > k0:
                k = k1 - k0
                tot -= n * (k * (hi - e0 - k0 * de) - de * k * (k - 1) / 2)
        return self.files_at_c(ke, lo) + tot / (hi - lo)
