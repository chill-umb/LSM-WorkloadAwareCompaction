#!/usr/bin/env python3
"""k2t (2026-10-28): case 2's transition (K lowered, busy tree): the L0 merges from the first one after the change
until the tree has refilled. A per-cycle closed form, applied cycle by cycle (as k1t's transition is), from RocksDB's
code (lib/rocksdb):
- Scores: L0 = files not being compacted / K, or their bytes / the base level's target if larger: N / K^, K^ = min(K,
  B / F0) (version_set.cc:3846-3856, 3908-3910, 3953-3956); L1 and deeper = bytes not being compacted / target
  (:3961-3972, static targets). The levels are sorted by score and the picker takes the highest score >= 1
  (version_set.cc:4007-4020; compaction_picker_level.cc:238-271).
- One compaction slot (max_background_jobs = 2: 1 flush + 1 compaction, db_impl_compaction_flush.cc:3216-3217). The
  slot is held after a merge installs until its input files are deleted, and only then is the next merge picked
  (db_impl_compaction_flush.cc:3838-3870: PurgeObsoleteFiles, then bg_compaction_scheduled_--, then
  MaybeScheduleFlushOrCompaction). Flushes have their own slot and go on meanwhile.
- An L0 -> L1 merge takes every L0 file present and every L1 file they overlap (compaction_picker_level.cc:376-379 ->
  compaction_picker.cc:1298-1315; the L0 files span the key range, so all of L1: measured o = L1's bytes).
- Writes are delayed at >= 20 L0 files (column_family.cc:1032-1036); L0's count includes the files a running merge
  reads until it installs (version_set.cc:5156-5167).
After K is lowered (K^ -> K^' < K^), L0's score N/K^' jumps above L1's. Each L0 merge then reads all of L1, which
the merges themselves keep raising: it is long, and L0 refills toward 20 files while it runs. When it installs, L1
holds u + r0 N F0 / S1 (u: L1's score when the merge was picked; r0: the share of the L0 bytes L1 keeps) and L0 holds
the a files flushed during the merge and its purge. The next pick is L0 at once if a / K^' is above every other score;
otherwise the worker serves the top scores (L1, joined by the deeper levels its score falls to: score-first serving
keeps them in lockstep) until L0's score passes theirs. The group's score falls per flush by the bytes the worker reads
in one flush (t_F / lambda: fast below 20 L0 files, slowed at 20 and above) over the bytes it must move per unit of
score (D: each level's own target plus everything it receives from the group above, times 1 + its overlap; path_checks'
drain_bytes). That gives the next merge's files N_k+1 and L1's score at its pick. The cycle repeats from the first
merge (path_checks' #8a/#8b) until the merges reach the K' path's own level (no further rise: the gap passes 20
files and the slowed flushes give the worker enough to hold L1).
Inputs (D-36): only the pre-change window: its state at X (levels' bytes_end), its F0, t_F, overlaps, L0 merges' kept
share r0 and parts (hold record: seconds per L0 merge with its purge gap, keys per flush, fast and slowed key rates),
its fast/slowed worker speeds (w0s: ops per byte read by the other merges), path_checks' first merge, and the brake
forecast's K' path (already built from the pre window). No twin, no other run, no simulator: each step is closed form.
"""
import math

N_SLOW = 20


def a_of(h, b1, n, b):
    """Files flushed during an L0 merge of b bytes begun at n files, its purge gap included: the law's hold-based rule
    (law_k2t.py Win.predict, L0A branch "a_of"): the pre window's seconds per L0 merge (purge gap included) scaled by
    the bytes it reads; flushes at the fast key rate below 20 L0 files, at the slowed rate from 20."""
    tau = h["tau"] * b / b1
    if n >= N_SLOW:
        return tau * h["rs"] / h["M"]
    t20 = (N_SLOW - n) * h["M"] / h["rf"]
    if tau <= t20:
        return tau * h["rf"] / h["M"]
    return (N_SLOW - n) + (tau - t20) * h["rs"] / h["M"]


def drain_bytes(S, om, group, dp):
    """Bytes the worker must read to lower every level of the group by one unit of score (path_checks' drain_bytes)."""
    tot, carry = 0.0, 0.0
    for i in range(1, dp):
        if i in group:
            carry = S[i] + carry
            tot += carry * (1 + om[i])
        else:
            carry = 0.0
    return tot


def om_at(om, size0, z, S, dp):
    """Each level's overlap per byte pushed at scores z: the law's overlap rule (Win.predict's dom: omega_i scales with
    the level below's size over its own), from the pre window's omega and mean sizes size0."""
    out = list(om)
    for i in range(1, dp):
        if om[i] and size0[i] > 0 and size0[i + 1] > 0 and z[i] > 0:
            below = z[i + 1] * S[i + 1] if i + 1 < dp else size0[i + 1]
            out[i] = om[i] * (below / size0[i + 1]) * (size0[i] / (z[i] * S[i]))
    return out


def advance(zz, grp, s_top, dj, target, lam, S, om, tF, dp, size0):
    """The worker serves the top group for up to dj flushes, or until the group's score falls to target. The group loses
    tF / (lam D) of score per flush (D: path_checks' drain bytes at the group's overlaps); the level just below it
    receives what it pushes and rises by sum(S of the group) / S_next per unit of score lost; a level joins the group
    when the group's score falls to its own. Returns (zz, grp, s_top, flushes used, reached target)."""
    used = 0.0
    for _ in range(4 * dp + 8):
        for i in grp:
            zz[i] = s_top
        omc = om_at(om, size0, zz, S, dp) if size0 is not None else om
        r = tF / (lam * drain_bytes(S, omc, grp, dp))   # score lost per flush
        recv = max(grp) + 1 if max(grp) + 1 < dp else None
        k_rise = (sum(S[i] for i in grp) / S[recv]) if recv is not None else 0.0
        d_join = float("inf")
        for i in range(1, dp):
            if i not in grp and zz[i] < s_top:
                d_join = min(d_join, (s_top - zz[i]) / (r * (1 + (k_rise if i == recv else 0.0))))
        d_tgt = (s_top - target) / r if s_top > target else 0.0
        d = min(dj - used, d_join, d_tgt)
        if recv is not None:
            zz[recv] += r * d * k_rise
        s_top -= r * d
        used += d
        for i in grp:
            zz[i] = s_top
        if d == d_tgt:
            return zz, grp, s_top, used, True
        if used >= dj - 1e-12:
            return zz, grp, s_top, used, False
        grp = sorted(set(grp) | {i for i in range(1, dp) if zz[i] >= s_top - 1e-9})
    return zz, grp, s_top, used, False


def gap(z, a, kh2, S, om, tF, lam_f, lam_s, dp, whole=True, size0=None):
    """From the install of an L0 merge to the next L0 pick. Returns (N, z', flushes).
    L0 holds the a files flushed during the merge and its purge (a is a mean, kept fractional) and gains one at each
    flush. The worker serves the top group (advance); L0 is picked as soon as its score (files / K^') reaches the
    group's: at a flush when the group falls slowly, within the flush (at a job's end) when it falls fast."""
    a_i = a
    sc = sorted(((z[i], i) for i in range(1, dp)), reverse=True)
    zz = list(z)
    s_top = sc[0][0]
    grp = [i for i in range(1, dp) if zz[i] >= s_top - 1e-12]
    for n in range(0, 400):
        c = a_i + n
        if c / kh2 >= s_top:
            return c, zz, float(n)
        lam = lam_f if a_i + n < N_SLOW else lam_s
        zz, grp, s_top, used, hit = advance(zz, grp, s_top, 1.0, c / kh2, lam, S, om, tF, dp, size0)
        if hit:
            return c, zz, n + used
    return a_i + 400, zz, 400.0


def fill(F0, tF, S, om, dp, kh2, z_x, h, b1, r0, lam_f, lam_s, n1, d, span, cap=None, whole=True, maxm=400,
         size0=None, x_brake=None):
    """The K' path's L0 merges from X: [(begin op after X, files N, files flushed during it and its purge a, L1 score
    at the pick u)]. z_x: scores at X (index 1..dp-1); n1, d: path_checks' first merge; cap(t) -> the K' path's files
    per merge at op X + t (the brake forecast's K' path), or None."""
    z = list(z_x)
    for i in range(1, dp):                           # the group that drained before the first merge sits at N_1/K^'
        z[i] = min(z[i], n1 / kh2)
    N, b = n1, d
    seq = []
    while b < span and len(seq) < maxm:
        u = z[1]
        c = cap(b) if cap is not None else None
        if c is not None:
            # past the brake forecast's own K' crossing the 64 GiB rule delays writes whatever L0 holds: the K' path
            # is the brake's (its files per merge, the law's settled files flushed per merge x)
            seq.append((b, c, x_brake if x_brake is not None else 0.0, min(u, c / kh2), True))
            z[1] = min(u, c / kh2)
            b += c * tF
            N = c
            continue
        bk = N * F0 + u * S[1]
        a = a_of(h, b1, N, bk)
        seq.append((b, N, a, u, False))
        z[1] = u + r0 * N * F0 / S[1]
        n2, z, _ = gap(z, a, kh2, S, om, tF, lam_f, lam_s, dp, whole, size0)
        b += n2 * tF
        N = n2
    return seq
