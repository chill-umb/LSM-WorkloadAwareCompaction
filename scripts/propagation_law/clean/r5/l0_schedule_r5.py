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
