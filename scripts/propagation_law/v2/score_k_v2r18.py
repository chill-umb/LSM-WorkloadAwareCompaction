#!/usr/bin/env python3
"""Cases 1-2 test of the propagation law v2 (~/node_ops/drafts/2026-10-24-propagation-law-v2-cases12.md) on the K
batch's 675 single-step runs. Operator, 2026-10-24.

Same runs, change measure, noise rule, settled rule and hit rule as v1 (score_k_v1.py). Only the predictions change:
- inputs: the twin's own values in each window the measurement averages; the prediction is the mean of the
  per-window predictions over those windows (fix 3);
- calm windows: v1's K1-K3 with the integer cap K_e (G3); deeper levels unchanged (K4);
- busy windows: the water level σ* = N_fire/K̂ (L0-bound) or held at the 64 GiB estimate (pending-bound), BK1-BK6;
- reads: (C1)-(C3), every age-band edge moves with K (fix 2);
- path: L0's next merge comes when N/K̂' reaches the highest other score (T-K, generalised); a busy K raise that
  needs more than the throttle allows waits for the drain (§4.1).
Pinned (owner, 2026-10-24): flows below L1 (#10) stay predicted unchanged, as in v1.

Round 3 (owner 2026-10-24: "work out where the extra files come from; if a general RocksDB rule, update the formula";
"go ahead with fixes 1-2"), after seeing the runs:
- fix b, the hold rule (replaces round 2's one-file band): one always-busy worker, writes slowed to the delayed-rate
  cap at 20 L0 files, highest score first. Per L0 cycle the worker's time equals the cycle's, so the time L0 holds
  >= 20 files before its merge is H = [w r_s tau + (20 - a) M (w - 1/r_f)] / (1 - w r_s) (w: other merges' worker
  seconds per key, gaps included; tau: the L0 merge; a: files flushed during it; M: keys per file; r_s, r_f: keys/s
  slowed / not). Files at the merge = 20 + slowed flushes landing within H (the first at
  delta = (M - min(M, r_f d_f))/r_s + d_f, then every M/r_s). At K' the merge work scales with the model's own
  predicted reads per op; N' is the fixed point (the levels' sizes depend on N'/K̂').
- fix 1: the drain before the first L0 merge after a change runs at the worker's measured speed with the gaps
  between jobs counted, and data a draining level pushes into a draining level below must be pushed again.
- fix 2: L0-bound vs pending-bound by the model's own rule (§2.2: the pending estimate of the state vs 64 GiB),
  L0-bound needing the median L0 merge at >= 20 files; an L0-bound tree slows writes by definition.
Inputs from cycles_k.py (data/cycles/, the twin's repeat 1, same windows).

Round 4 (owner 2026-10-24: "yes to both": unpark flows below L1 for busy trees; apply the item-3 settle time):
- range layer R1 (v1 §3.7) in busy trees: a level passes on what no newer copy catches while it is there, so
  f_i = q·R(Δ_i) with Δ_i = size_i / f_i. R is read from the twin's own levels (its points (Δ_i, f_i/q), i >= 1,
  log-log between them); at K' each level's flow solves f' = q·R(size_i'/f'). The flows move the deeper writes, the
  hold rule's merge work and the read layer's residences.
- L0's settle time in a busy tree: the levels' refill (K lowered: the bytes the levels must add to reach the new caps,
  poured at the write bytes per op) or drain (K raised: the bytes above the new caps, pushed through every draining
  level, at the worker's slowed λ), in place of one L0 cycle.

Round 5 (owner 2026-10-24: "go ahead with R0 for deep-level sizes in busy trees, then rescore all 675 runs"):
- range layer R0 (v1 §3.7) in busy trees, with band widths (r0.py): the twin window's widths Δ_1..Δ_m of its pushing
  levels (L1 down, every level that pushes data on) are solved from R0 with their measured sizes and flows (U' a power
  law between knots, L0's knot at N̄_0·t_F); at the step's sizes (the fill, BK4') the widths are solved again on the
  twin's curve, and R1 gives the flows f'_i = U'(Δ'_i) (R0_FLOWS=1, default; 0 keeps round 4's size/f proxy).
- level j = m+1 below the pushing levels (still filling, or the last level), SIZE_RULE:
  "lag" (default): level j sees the same stream later. Level i holding Δsize_i more bytes delays it by Δsize_i/f_i
    operations, and in that time level j would have grown by min(u_j, f_i) per op (u_j: its own growth per op in the
    twin window; it cannot grow from level i's stream faster than that stream). Δsize_j = -Σ_{i=0..m} min(u_j, f_i)·
    Δsize_i/f_i (i = 0: L0's bytes). No overwrites: u_j = f_i, so it gives back the fill's byte conservation.
  "r0": v1's R0 for level j with its oldest data fixed: Δtotal(1..j) = Σ [Δs_i - U'(W)·ΔΔ_i] - ΔU(Δ_0), U'(W) from the
    curve's tail. Kept for the record: it misses 3-5x in W3 and breaks where levels empty stored data (W4).
  "pour": round 4 (the fill keeps the twin's total).

Round 6 (owner 2026-10-24: lag rule accepted; "fix L2's size and overlap next"):
- investigated, NOT adopted (MACH=1 to enable; default off = round 5): the hold rule's worker speeds at K' as the run's own: the twin window's merge seconds per key (w)
  and L0 job time (tau) scaled by the step run's own ÷ the twin's, both measured in window 0 (before X, same K, same
  work). The controller sees its own worker. Busy levels sit at N_fire/K̂ (x S_i) within 0-3% in twin and step, so a
  one-file miss in N_fire was L2's whole size miss; step workers were up to 1.8x slower than the twins'.

Round 7 (owner 2026-10-25: trial of both asked; then "Adopt the calm tree lag rule ... Drop the scoring fix").
Twins of record (D-35, 525947a): the rerun twins and their cycles, noise limits from the three original repeats
(data/twins).
- ADOPTED, CALM_LAG=1 (default; 0 = round 5): calm trees get round 5's lag rule for level j = m+1 (the level below the pushing levels, usually the
  last): it sees L0's stream later by the change in L0's bytes, Δsize_j = -min(u_j, f_0)·ΔN_0·F_0/f_0 (the calm law
  moves no other level's size, so the L1..m terms are 0). The levels below j are unchanged; flows stay pinned.
- DROPPED, MEASURE=hybrid (default d33 = D-33's measure; kept for the record): #6's level sizes. A level that pushes data on (f_i > 0 in every settled twin window, not the last
  level) is scored on the plain gap after the change, mean over the settled windows of (step - twin), against the
  same noise rule on the repeats' own values (3·√2·√(mean variance of the 3 repeats' sizes)). The last level and
  any level that pushes nothing keep D-33's change measure. The settle point is unchanged (the window-0 term cancels
  in it). Trial: W5 #6 71 -> 77% but W4 43 -> 35% (the plain gap's noise limit is ~half D-33's in W4).

Round 8 (owner 2026-10-25: "Go with b. follow RocksDB's least-overlap-first order on the per-file snapshot at the
change, with new files arriving during the drain"; then "Yes, adopt rule b as round 8"). ADOPTED: DRAIN=order is the
default (DRAIN=off gives round 7). DRAIN=order: the busy
first-L0-merge wait (T-K) reads the bytes rule (b) gives (v2/drain_order.py, the step's snapshot at X in
data/snap_x); DRAIN=flat: the same solver with window 0's omega (self-check: it must give round 7's waits).

Round 9 (owner 2026-10-25: "Accept both together"; subagents 4 and 10, checked by the operator), calm trees with
few flushes per window: L0 fills one file per flush and empties at K_e', so a window holds whole merges and L0 holds a
whole number of files at its ends. Both rules use L0's schedule after X from the step's own records before X
(v2/l0_schedule.py, inputs data/l0_events/). L0SCHED=0 gives round 8.
- #4 (L0 -> L1 bytes written per op): in a settled window w whose twin windows 0..w are all calm,
  dw0(w) = [bytes of the K' schedule's merges ending in w - the same for K = 4] / ops_w, n F0 + B per merge of n files.
- #10 (flows): when every twin window after X is calm with no write slowdown predicted, each level that pushes data
  on gets the L0 storage term dF_i += -F0 {[n'(end) - n'(start)] - [n(end) - n(start)]} / O: n, n' L0's files from
  the K = 4 and K' schedules at the settled span's two ends, O its ops (flow = bytes flushed - copies dropped - change
  in bytes stored above). W4's #10 misses (the twin of record's timing) are left as they are (owner).

Round 10 (owner 2026-10-25: "Adopt fixes A and B"; report "The remaining 9: first pass"). Both switches 0 give round 9.
- Fix A, L0MEAN=1: L0's mean files per window in calm trees from round 9's schedule (op-weighted mean of the K'
  schedule minus K = 4's) instead of the long-run change: a window holds whole merges, so its mean depends on where
  the L0 cycle falls in it. Used wherever the law needs L0's mean files: #2, #2s, and #11's probes (#9's allowance
  reads #2's prediction).
- Fix B, L0WHOLE=1: the first L0 merge after X in a busy tree takes a whole number of files, the first whole number at
  or above the T-K crossing (L0's score moves only when a flush ends); its time is unchanged.

Round 11 (owner 2026-10-25: "Adopt fix A2"). L0BATCH=0 gives round 10.
- Fix A2, L0BATCH=1: files per L0 merge per window in calm trees from round 9's schedule (mean files of the K'
  schedule's merges ending in the window minus K = 4's): when K is lowered while L0 holds n_x >= K_e' files, the
  first merge after X takes all n_x, and it can fall in a settled window. #3 and #3s.

Round 12 (owner 2026-10-25: "Adopt the read fix"; option (a) for #2s dropped, round 11's schedule kept). READFIX=0
gives round 11.
- #11's read layer: a level that pushes nothing in a window (every last level; in busy trees often the level below the
  busy ones) had an infinite band, which the G edges skipped, so every Get found in it and below sat at one age, the
  end of the band above; a K change that moved that edge outward sent all of them up a level (W3-T6-l164-K3: -0.62
  probes vs ~-0.05 measured). Now such a level's band is size / inflow (it keeps every byte that arrives), and a level
  with neither inflow nor outflow has its edge at infinity.

Round 13 (owner 2026-10-25: "Adopt (a) as round 13"; report "(a) W2: calm-tree middle levels and the last level").
MIDBURST=0 gives round 12.
- Calm trees, each level i that pushes data on (1..m): once per L0 cycle (C = K_e t_F ops) it sheds E_i = f_i C bytes,
  one file at a time from the highest score, on the one worker. It rises while level i-1 drains (L1 at once, when the L0
  merge installs) and falls while it drains (D_i = lam (s_i + o_i) C ops, the bytes its merges read), so its mean above
  its resting point is E_i (D_{i-1} + D_i) / 2C, linear in C: the change is f_i lam (rd_{i-1} + rd_i) (C' - C) / 2.
- The calm lag rule for level j = m+1 then takes these changes too (round 5's full lag rule; round 7 kept only L0's).

Round 14 (owner 2026-10-25: "Yes, adopt fixes 1+2 as round 14"; report "#8b, round 14 trial"). NEARSTOP=0 W0SPEED=0
gives round 13.
- Fix 1, NEARSTOP=1: L0 holds at most stop - 2 = 34 files. column_family.cc RecalculateWriteStallConditions marks
  L0 >= level0_stop_writes_trigger - 2 as near stop, and SetupDelay then cuts the delayed write rate x0.6 at every new
  version (floor 16 KB/s), so writes all but stop there (no run of the 1,350 goes past 34 files). Every use of the
  stop trigger (the first-merge cap, the hold rule's caps) takes 34.
- Fix 2, W0SPEED=1: the busy first-merge path's ops per merged byte, lam_f (L0 < 20) and lam_s (20 <= L0 < 34), from
  window 0's own other merges (not L0 -> L1, not trivial) begun in the same band: their operations / bytes read
  (s + o), each job charged from its begin to the next job's begin (data/w0_speeds/, from c01_w0_speeds.py). In the
  drain writes sit at the maximum delayed rate (each small merge lowers the debt, x1.25 per new version) and the
  worker reads faster when writes are slowed; round 13's lam (rs / kpo / v_rest) took the slowed rate mostly from L0
  merges and the merge speed mostly from full-speed time. A band with no such merges keeps round 13's value.

Round 15 (owner 2026-10-25: "Yes, go with (a). This was always supposed to be a Rolling case. We should never use
another native tree to calculate the propagation law."; D-36 draft ~/node_ops/drafts/2026-10-25-D-36-rolling-law-draft.md,
written before any rolling score). ROLL=0 gives round 14 (twin-fed).
- The twin is the scoring baseline only. No value from a native run enters a prediction.
- Settled checks (#1-#6, #10, #11): reverse prediction from the run's own window. Each settled window k starts from
  the step's own window k (running at K'), its own cycles window k and its growth from its own window k-1; the law
  predicts that window at K = 4 from the step's own state at X; predicted change (step - twin) = minus that. Win
  takes the K it runs at (kin); the calm and busy formulas used K = 4 before.
- Path checks (#7, #8a, #8b, #9): forward from the step's own window 0 (#8 already was); #7 from window 0 only.
- Gates (round 9-11's calm schedule, round 9's storage term): the step's own windows.
- Read curve (#11): the step's own whole-phase per-level counters, edges from its own windows (per-level counters
  exist only at the phase stamps; a per-window log is open).
- The slowed-rate fallback (a window with < 1 s slowed): the step's own windows 0..k.

Round 16 (owner 2026-10-26: "Adopt the busy-test fix as round 16"; report "Round 15's losses diagnosed").
BUSYTEST=marks gives round 15.
- §2.2's busy test, BUSYTEST=burst: a non-last level counts above its mark only past its calm mean overshoot (round
  13: f_i lam (rd_{i-1} + rd_i) C / 2, C = K_e t_F at the window's own K) plus one file. Round 15 marked a calm tree
  at a raised K busy because its middle levels sat further above their marks, which round 13's own calm rule predicts
  (W2 at 256 MiB, K raised: 14 runs, L2-L4 sizes with the wrong sign).

Round 17 (owner 2026-10-26: "Adopt both fixes as the next round"; report "Two subagent trials"). L0A=0 R18O= R18S=0
P7RULE=0 gives round 16.
- L0A=3 (busy trees, cases 1-2): L0's mean files = (Nb - 1)/2 + u + x + c; the change takes x' = x g(tau')/g(tau),
  tau' = tau b'/b (see the L0A comment below). Inputs: the step's own per-merge records (data/l0_merges, own runs only).
- R18O=sig: the merge bytes b' take L1 at the water level (BK3: L0 merges when its score meets L1's, so L1 holds
  sigma S_1): o' = o sigma'/sigma, in place of L1's mean-size ratio (which carries the batch just added).
- R18S=snap (#9, mainly case 1): after a K raise in a busy tree the batch relaxes, N_k+1 - N* = rho (N_k - N*), rho from
  the one worker's balance, the drain bytes from the step's own snapshot at X (round 8's walk); settle time = the
  larger of round 4's and this.
- P7RULE=first (#7, case 2): L0's count keeps a running merge's files until it installs (version_set.cc:5156-5167), one
  L0 merge at a time; writes are also predicted slowed when the first merge after X reaches 20 with the files that
  flush in during it: n1 + a0 (n1 F0 + o0 L1_X/L1_mean)/(s0 + o0) >= 20, a0 = tau0 r_f/M (own window 0, state at X).
- Kept for the record, off: L0A=1|2, R18C=1, R18S=1, P7RULE=settled|peak.

Round 18 (owner 2026-10-26: "Adopt R0NET and LAGR"; report "Double check of #1 W4, #5 W4, #10 W4, #6 W3/W4", subagents
c10 and c6). R0NET=0 LAGR=0 gives round 17.
- R0NET=1 (busy trees, cases 1-2): the range layer (R0/R1) is a steady-state relation, but a window's measured outflow
  also carries the change in bytes stored above the level (out = in - copies dropped - change stored; at the 64 GiB
  brake the deep levels drain stored data, f_i > q). The bands are fitted on the steady flows fn_i = q - copies dropped
  above i (own window), and the predicted flow is f_i + (fn'_i - fn_i): the K change moves only the dropped copies.
- LAGR=1 (busy trees, the lag rule for level j below the pushing levels): level i delays level j's stream by the change
  in how long a byte stays in level i, size'/f' - size/f (with the range layer's flows f'), not by its extra bytes over
  the old flow; only in a level that drops copies (in - out - kept > 2% of what arrives); else the plain delay.

Usage: score_k_v2r18.py [TWINS_DIR STEPS_DIR OUT_DIR]   (defaults: data/twins_rec, data/steps, v2/out_r18)
"""
import json
import math
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, "/home/cc/node_ops/diag/kcase")
sys.path.insert(0, "/home/cc/node_ops/diag/kcase/v2")
import drain_order  # noqa: E402
import l0_schedule  # noqa: E402
import r0  # noqa: E402
from score_k_v1 import (CAP_PAIRS, FILE_BYTES, WINDOW, Setting, cap_pair, delta, hit,  # noqa: E402
                        settle_point, settled_mean)

MIB, GIB = 1 << 20, 1 << 30
N_SLOW, P_SOFT = 20, 64 * GIB          # A12: level0_slowdown_writes_trigger, soft_pending_compaction_bytes_limit
NEARSTOP = __import__("os").environ.get("NEARSTOP", "1") == "1"   # round 14, fix 1 (0 = round 13)
L0_STOP = 36 - 2 if NEARSTOP else 36   # A12: level0_stop_writes_trigger 36; near stop at 34 (round 14)
W0SPEED = __import__("os").environ.get("W0SPEED", "1") == "1"     # round 14, fix 2 (0 = round 13)
KC = Path("/home/cc/node_ops/diag/kcase")
BUSYTEST = __import__("os").environ.get("BUSYTEST", "burst")   # round 16 (marks = round 15)
# Trial 2026-10-26 (owner: "Fix the busy tree's L0 file-count errors in W4"): L0A=1, busy trees' mean L0 files =
# (N - 1)/2 + a (a: files flushed during an L0 merge, the merged files counted until it installs), so the change is
# dN/2 + da; a' = a x (bytes an L0 merge reads at K') / (at K): N F0 plus the L1 bytes it overlaps (x L1's size
# change), the same ops per merged byte. L0A=0: round 16.
# L0A=2 (2026-10-26): a in operations. The merge lasts tau (s, the own cycles' L0 job time with its gap) x bytes'/bytes;
# writes run at r_f keys/s until L0 holds N_slow = 20 files, then at r_s (A12); a = keys written during it / keys per
# file. The change is a(N', B') - a(N, B), the formula at both states (its own bias cancels).
# L0A=3 (2026-10-26, owner: "Keep refining the a-rule"): counting, L0's op-weighted mean = (Nb - 1)/2 + u + x + c,
# Nb = sum N^2 / sum N over the window's L0 merges (a cycle's ops grow with its files), x = ops written during a merge
# / ops per memtable (M), u = the memtable's fill at the merge's begin (kept), c cancels. The change is
# (Nb' - Nb)/2 + (x' - x), Nb' = N' x Nb/N (the window's own spread kept), x' = x g(tau')/g(tau), tau' = tau x b'/b
# (an L0 merge reads at the same bytes/s at every K), g(t) = ln(1 + 0.5625 t r0/M)/ln 1.5625 while writes are delayed
# (r0 within 15% of the 8 MiB/s cap: x0.8 per version a flush installs, two per memtable, no other job runs to lift
# it), g(t) = t r0/M otherwise. r0 = the window's median write rate before its merges. Own windows only (data/l0_merges).
L0A = __import__("os").environ.get("L0A", "3")             # round 17 (0 = round 16)
# Trial 2026-10-26 (#7 in W5, case 2): P7RULE=peak. L0's count during an L0 -> L1 merge. RocksDB's slowdown test reads
# l0_delay_trigger_count = files_[0].size() (version_set.cc:5156-5167), every L0 file including those the running
# merge reads, which leave only when it installs; one slot, one L0 merge at a time (compaction_picker_level.cc:197),
# so nothing else leaves L0 meanwhile. Before the count
# reaches 20 nothing slows writes on L0's account (column_family.cc:1033-1036; 1199), so flushes land at the window's own
# rate with L0 < 20 (t_f = keys per flush / keys per second with L0 < 20). A merge that begins at n files and runs tau
# seconds reaches 20 iff n + tau / t_f >= 20 (BK2: peak = N_fire' + n_job, n_job counted at that rate). tau at K'
# scales with the merge's bytes (n F0 + the L1 bytes it overlaps; an L0 merge reads at the same bytes/s). Applied to
# the first merge after X (#8b's files, L1 at X) and to the law's settled merge at K' (its batch and L1 size).
# P7RULE=first (round 17) | settled: one part only. 0: round 16's #7.
P7RULE = __import__("os").environ.get("P7RULE", "first")   # round 17 (0 = round 16)
# Round 17 switches (sub-agent p11 trial, 2026-10-26; off = the L0A=3 trial exactly):
# R18O=sig: the x-rule's merge bytes at K' take L1's overlap at the water level (BK3: L0 merges when N/K^ meets L1's
#   score, so the merge reads L1 at sigma S_1): o' = o x sigma'/sigma, in place of L1's mean-size ratio (which carries
#   the batch sawtooth riding on top of sigma S_1).
# R18C=1: the pending test's state at another water level conserves bytes: what L1 and the water levels gain over the
#   own state at the merge (state(sig)) the last level gives up (data only move down; the inflow is the same).
R18O = __import__("os").environ.get("R18O", "sig")          # round 17 ("" = off)
R18C = __import__("os").environ.get("R18C", "0") == "1"
# R18S=1: #9's settle time in a busy tree after a K raise counts the batch's relaxation. The first L0 merge after X
#   takes N_1 files (T-K, 8b); every later cycle the one worker must, besides the batch's own work, lower the levels
#   at the water level by (N_k - N_k+1)/K^' of score, while L0 refills at the slowed rate. Worker bytes per cycle =
#   L0 merge b(N_k) + the batch pushed through the draining levels + D (N_k - N_k+1)/K^' = what it reads while N_k+1
#   files flush (tF/lam_s per slowed file), so N_k+1 - N* = rho (N_k - N*),
#   rho = (b' + F0 sum(1 + om_i) + D/K^') / (tF/lam_s + D/K^'),  b' = F0 + S_1/K^' (L1 overlap at sigma S_1),
#   D = drain bytes per unit of score of the draining group (T-K's drain_bytes). Settled at the first merge within one
#   file of N* (A6); its op after X = d + sum_{i=2..j} N_i tF. Window-0 (own) inputs only.
#   R18S=snap: D from the step's per-file snapshot at X in RocksDB's least-overlap-first order (round 8's drain bytes,
#   v2/drain_order.py): the bytes the walk reads to lower the group from N_1/K^' to N*/K^', per unit of score.
R18S = __import__("os").environ.get("R18S", "snap")         # round 17 (0 = off)
R18S = "" if R18S == "0" else R18S
# Round 18 (subagent c10 trial R0NET; see the docstring): the range layer on the steady flows. 0 = round 17.
R0NET = __import__("os").environ.get("R0NET", "1") == "1"
# Round 18 (subagent c6 trial LAGR; see the docstring): the lag rule's delay = the change in residence time. 0 = round 17.
LAGR = __import__("os").environ.get("LAGR", "1") == "1"
LAGR_DROP = float(__import__("os").environ.get("LAGR_DROP", "0.02"))   # a level drops copies: (in - out - kept)/in above this


def drain_R(sx, S, snap, s, passes=3):
    """Round 8's walk bytes (drain_order.solve's moved(s) and the snapshot pools) to lower the levels to score s."""
    lv = sorted(sx)

    def moved(P):
        m = {}

        def mv(s_):
            mm = {}
            for i in lv:
                own = max(0.0, sx[i] - s_) * S[i]
                c = 0.0
                if i - 1 in sx:
                    u = min(sx[i], sx[i - 1])
                    if s_ < u:
                        c = max(0.0, mm[i - 1] - mv(u)[i - 1])
                mm[i] = own + c
            return mm
        m = mv(s)
        return m
    pushed = {i: [] for i in lv}
    tot = 0.0
    for _ in range(passes + 1):
        pools = {i: drain_order.pool_of(snap, i, pushed.get(i - 1, []) if i > 1 else []) for i in lv}
        P = {i: pools[i].overlap for i in lv}
        m = moved(P)
        tot = sum(m[i] + P[i](m[i]) for i in lv)
        pushed = {i: pools[i].taken(m[i]) for i in lv}
    return tot
TRACE = __import__("os").environ.get("TRACE", "")          # r18 diag: print internals for runs whose path contains it
C03 = Path("/home/cc/node_ops/diag/kcase/data/l0_merges")   # the step runs' own per-merge records (no twin files)
DELAY_CAP = 8388608                     # db_bench --delayed_write_rate default: the write controller's max rate (B/s)


def c03_parts(name):
    """{window: own L0-merge parts} from c03_l0_merges.py's output for one run (None if absent)."""
    f = C03 / f"{name}.json"
    if not f.exists():
        return None
    W = {}
    for m in json.loads(f.read_text())["merges"]:
        if m["before"]:
            W.setdefault(m["w"], []).append(m)
    out = {}
    for k, ms in W.items():
        iv = sorted(b[1] - a[1] for m in ms for a, b in zip(m["before"] + m["inside"], (m["before"] + m["inside"])[1:]))
        rb = sorted((m["before"][-1][1] - m["before"][-2][1]) / ((m["before"][-1][0] - m["before"][-2][0]) / 1e6)
                    for m in ms if len(m["before"]) > 1 and m["before"][-1][0] > m["before"][-2][0])
        if not iv or not rb or iv[len(iv) // 2] <= 0:
            continue
        M = iv[len(iv) // 2]
        out[k] = dict(M=M, r0=rb[len(rb) // 2], tau=st.mean((m["end_t"] - m["t"]) / 1e6 for m in ms),
                      x=st.mean((m["end_op"] - m["op"]) / M for m in ms), s=[m["s"] for m in ms])
    return out
ROLL = __import__("os").environ.get("ROLL", "1") == "1"   # round 15: rolling, own windows (0 = round 14)
W0S = KC / "data/w0_speeds"            # c01_w0_speeds.py: window 0's jobs per L0 band (fast / slow / near) and kind
CYC = KC / "data/cycles_rec"           # cycles_k.py: L0 cycles, rates and speeds in time (twins of record)
NOISE = KC / "data/twins"              # the three original repeats: the noise limits (D-35 draft)
CALM_LAG = __import__("os").environ.get("CALM_LAG", "1") == "1"     # round 7, adopted
MEASURE = __import__("os").environ.get("MEASURE", "d33")       # d33 | hybrid (dropped, owner 2026-10-25)
DRAIN = __import__("os").environ.get("DRAIN", "order")        # order (rule b, round 8) | off (round 7) | flat (self-check)
SNAP = KC / "data/snap_x"
L0SCHED = __import__("os").environ.get("L0SCHED", "1") == "1"
L0MEAN = __import__("os").environ.get("L0MEAN", "1") == "1"     # round 10, fix A (0 = round 9)
L0WHOLE = __import__("os").environ.get("L0WHOLE", "1") == "1"   # round 10, fix B (0 = round 9)
L0BATCH = __import__("os").environ.get("L0BATCH", "1") == "1"   # round 11, fix A2 (0 = round 10)
READFIX = __import__("os").environ.get("READFIX", "1") == "1"   # round 12, read bands of levels that push nothing (0 = round 11)   # round 9
MIDBURST = __import__("os").environ.get("MIDBURST", "1") == "1"  # round 13: calm middle levels hold each cycle's drain (0 = round 12)
EVENTS = KC / "data/l0_events"
OWN_X = __import__("os").environ.get("OWN_X", "1") == "1"
R0_ON = __import__("os").environ.get("R0", "1") == "1"
SIZE_RULE = __import__("os").environ.get("SIZE_RULE", "lag")
MACH = __import__("os").environ.get("MACH", "0") == "1"     # off: not adopted (round 6 report)
MACH_MODE = __import__("os").environ.get("MACH_MODE", "wt")     # wt: w and tau; w: w only
HOLD_OFFSET = __import__("os").environ.get("HOLD_OFFSET", "1") == "1"
R0_FLOWS = __import__("os").environ.get("R0_FLOWS", "1") == "1"
KS = {64: (2, 3, 5, 8), 128: (2, 3, 6, 9, 12), 256: (2, 3, 8, 12, 16, 19)}
K0 = 4
WATER = 0.9                            # a level within 10% of the water level counts as at it (A6-style)

QUANTITIES = [
    ("1", "above the cap = the cap", "settled"),
    ("2", "mean L0 files", "settled"),
    ("2s", "mean L0 files, noise only", "settled"),
    ("3", "files per L0->L1 merge", "settled"),
    ("3s", "files per L0->L1 merge, noise only", "settled"),
    ("4", "L0->L1 bytes written per op", "settled"),
    ("5", "L1->L2 overlap (per byte pushed)", "settled"),
    ("5b", "L1->L2 bytes written per op", "settled"),
    ("6", "L2 and below (bytes, writes, overlap)", "settled"),
    ("7", "L0 reaches 20 files (writes slowed) or not", "settled"),
    ("8a", "first L0 merge after the change: when", "path"),
    ("8b", "first L0 merge after the change: files", "path"),
    ("9", "L0 settled within one L0 cycle", "path"),
    ("10", "flows out of L1 and deeper", "settled"),
    ("11", "probes per Get", "settled"),
]


def est(sizes, S, l0_bytes):
    """A13 (version_set.cc:3656-3740) with L0 due: sizes[1..L], targets S[1..L]."""
    e, carry = l0_bytes + sizes[1], l0_bytes
    for i in range(1, len(sizes) - 1):
        ls = sizes[i] + carry
        carry = 0.0
        if ls > S[i]:
            carry = ls - S[i]
            if sizes[i + 1] > 0:
                e += carry * (sizes[i + 1] / ls + 1)
    return e


class Win:
    """One twin window -> the model's inputs and state."""

    def __init__(self, x, l1, t, c=None, run_rs=None, prev=None, kin=K0):
        L, self.dp = x["levels"], x["deepest"]
        self.kin = kin                       # round 15: the K this window runs at (K = 4 for twin windows)
        # round 5: level j's growth per op in this window needs the previous window's end
        self.grow = [(L[i]["bytes_end"] - prev["levels"][i]["bytes_end"]) / x["ops"] for i in range(len(L))] \
            if prev is not None and x["ops"] else None
        self.hold = self.timed(x, c, run_rs) if c else None
        self.ok = bool(x["flushes"] and x["ops"] and x["merge_read"] and self.dp >= 2)
        if not self.ok:
            return
        self.B = l1 * MIB
        self.S = [0] + [self.B * t ** (i - 1) for i in range(1, self.dp + 1)]
        self.q = x["flush_bytes"] / x["ops"]
        self.F0 = x["flush_bytes"] / x["flushes"]
        self.tF = self.F0 / self.q
        self.lam = x["merge_ops"] / x["merge_read"]
        self.rho = x["merge_ops"] / x["ops"]
        self.size = [L[i]["bytes_mean"] for i in range(self.dp + 1)]
        self.f = [L[i]["f_per_op"] or 0.0 for i in range(self.dp + 1)]
        self.om = [L[i]["omega_pushed"] or 0.0 for i in range(self.dp + 1)]   # None: nothing pushed in the window
        self.fn, _dc = [], 0.0                    # round 18 R0NET: steady flows, q minus the copies dropped above i
        for i in range(self.dp + 1):
            self.fn.append(self.q - _dc)
            _dc += ((L[i]["s"] or 0) + (L[i]["o"] or 0) - (L[i]["x"] or 0)) / x["ops"]
        self.fr = [self.fn[i] if (R0NET and self.f[i] > 0 and self.fn[i] > 0) else self.f[i] for i in range(self.dp + 1)]
        self.xw = [L[i]["x_per_op"] or 0.0 for i in range(self.dp + 1)]
        self.rd = [(L[i]["s_per_op"] or 0.0) + (L[i]["o_per_op"] or 0.0) for i in range(self.dp + 1)]
        self.n0 = L[0]["files_mean"]
        self.nfire = x["l0l1"]["files_in_mean"]
        ll = x["l0l1"]
        self.l0a = (ll.get("tau_mean") or 0.0) / self.tF if ll.get("merges") else None     # trial L0A
        self.l0s = ll.get("s_mean")
        self.l0o = (L[0]["o_per_op"] or 0.0) * x["ops"] / ll["merges"] if ll.get("merges") else None
        self.cap = math.ceil(self.B / self.F0)
        if BUSYTEST == "burst":                # round 16: past the calm mean overshoot (round 13) plus one file
            c_ = min(self.kin, self.cap) * self.tF
            over = [self.f[i] * self.lam * ((self.rd[i - 1] if i > 1 else 0.0) + self.rd[i]) * c_ / 2
                    if self.f[i] > 0 else 0.0 for i in range(self.dp + 1)]
            self.busy = any(self.size[i] > self.S[i] + over[i] + FILE_BYTES for i in range(2, self.dp))
        else:
            self.busy = any(self.size[i] > self.S[i] + FILE_BYTES for i in range(2, self.dp))
        if self.busy and not self.nfire:
            self.ok = False

    @staticmethod
    def timed(x, c, run_rs):
        """The hold rule's inputs from the twin's cycles_k window c (same window as x), in keys and seconds."""
        keys = c["keys_fast"] + c["keys_slow"] + c["keys_stop"]
        if not keys or not c["flushes"] or not c["t_fast"] or not c["job_l0"]:
            return None
        rs = c["keys_slow"] / c["t_slow"] if c["t_slow"] > 1.0 else run_rs
        if not rs:
            return None
        L = x["levels"]
        h = dict(M=keys / c["flushes"], rs=rs, rf=c["keys_fast"] / c["t_fast"], df=c["d_f"] or 0.0,
                 w=(c["sec_rest"] + c["gap_rest"]) / keys, tau=(c["sec_l0"] + c["gap_l0"]) / c["job_l0"],
                 a=c["l0"]["a"] or 0.0, H=c["l0"]["hold"] if c["t_slow"] > 1.0 else None,
                 R0=L[0]["s_per_op"] + L[0]["o_per_op"],
                 Rr=sum(L[i]["s_per_op"] + L[i]["o_per_op"] for i in range(1, len(L))),
                 med=c.get("_median_files"), kpo=keys / (c["hi"] - c["lo"]),
                 vr=c["v_rest"])
        h["tau_run"] = c["sec_l0"] / c["job_l0"]       # P7RULE: an L0 merge's own seconds, begin to install (no gap)
        h["delta"] = (h["M"] - min(h["M"], h["rf"] * h["df"])) / h["rs"] + h["df"]
        h["ts"] = h["M"] / h["rs"]
        return h

    def hold_time(self, w, tau, a):
        h = self.hold
        return (w * h["rs"] * tau + (N_SLOW - a) * h["M"] * (w - 1 / h["rf"])) / (1 - w * h["rs"])

    def hold_files(self, n, nf, dR0, dRr):
        """Files at the L0 merge for a tree that fires at n files, from the hold rule (fix b). The twin's measured
        hold carries what the balance leaves out (the formula's own value at the twin is subtracted)."""
        h = self.hold
        mw, mt = getattr(self, "mach", (1.0, 1.0))
        w2 = mw * (h["w"] * (h["Rr"] + dRr) / h["Rr"] if h["Rr"] else h["w"])
        tau2 = mt * (h["tau"] * ((h["R0"] + dR0) / h["R0"]) * (n / nf) if h["R0"] else h["tau"])
        a2 = tau2 * h["rs"] / h["M"]
        if w2 * h["rs"] >= 1:
            return float(L0_STOP)
        H2 = self.hold_time(w2, tau2, a2)
        if h["H"] is not None and (HOLD_OFFSET or getattr(self, "mach", (1.0, 1.0)) == (1.0, 1.0)):
            H2 += h["H"] - self.hold_time(h["w"], h["tau"], h["a"])
        if H2 < h["delta"]:
            return float(N_SLOW)
        return float(min(L0_STOP, N_SLOW + int((H2 - h["delta"]) // h["ts"]) + 1))

    def flows2(self, size2):
        """Round 4, R1: each level's outflow at the new sizes, f' = q·R(size'/f'), R from the twin's own levels."""
        q = self.q
        fr = self.fr
        pts = sorted((self.size[i] / fr[i], fr[i] / q) for i in range(1, self.dp) if self.f[i] > 0)
        f2 = list(self.f)
        if len(pts) < 2:
            return f2

        def R(d):
            if d <= pts[0][0]:
                return pts[0][1] if d >= 0.5 * pts[0][0] else None
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                if d <= x1:
                    u = (math.log(d) - math.log(x0)) / (math.log(x1) - math.log(x0)) if x1 > x0 else 0.0
                    return math.exp(math.log(y0) + u * (math.log(y1) - math.log(y0)))
            return pts[-1][1] if d <= 2 * pts[-1][0] else None
        for i in range(1, self.dp):
            if self.f[i] <= 0 or size2[i] <= 0:
                continue
            lo, hi = math.log(fr[i] / 8), math.log(fr[i] * 8)   # f - q·R(size/f) rises with f
            ok = True
            for _ in range(40):
                mid = (lo + hi) / 2
                r = R(size2[i] / math.exp(mid))
                if r is None:
                    ok = False
                    break
                if math.exp(mid) > q * r:
                    hi = mid
                else:
                    lo = mid
            if ok:
                f2[i] = (self.f[i] + math.exp((lo + hi) / 2) - fr[i]) if R0NET else math.exp((lo + hi) / 2)
        return f2

    def dreads(self, f2, dom):
        """Change in other merges' reads per op: flows scale a level's reads, overlap changes add."""
        return sum(self.rd[i] * (f2[i] / self.f[i] - 1 if self.f[i] else 0.0) + f2[i] * dom.get(i, 0.0)
                   for i in range(1, self.dp))

    def bands(self):
        """Round 5: the twin window's range layer (r0.Bands), or None. Pushing levels 1..m: every level from L1 down
        that pushes data on; level j = m+1 below them takes the change in bytes held."""
        if not hasattr(self, "_bands"):
            self._bands = None
            m = 0
            while m + 1 < self.dp and self.f[m + 1] > 0:
                m += 1
            if m >= 1:
                b = r0.Bands(self.n0 * self.tF, self.q if R0NET else min(self.f[0], self.q), self.size[1:m + 1],
                             self.fr[1:m + 1],
                             self.size[m + 1])
                if b.ok:
                    self._bands = (b, m)
        return self._bands

    def r0_apply(self, size2, dn0, caps2=None):
        """Round 5: (size2 with level j's change by SIZE_RULE, R1 flows on R0 widths or None). Under "lag" the levels
        below j get only what level j holds above its new cap (caps2; the last level has none), not the fill's spill."""
        if not R0_ON:
            return size2, None
        m = 0
        while m + 1 < self.dp and self.f[m + 1] > 0:
            m += 1
        if m == 0:
            return size2, None
        j = m + 1
        z, f2 = list(size2), None
        bm = self.bands()
        ch = bm[0].change((self.n0 + dn0) * self.tF, size2[1:m + 1]) if bm else None
        if ch is not None:
            f2 = list(self.f)
            for i in range(1, m + 1):
                f2[i] = (self.f[i] + ch[1][i - 1] - self.fr[i]) if R0NET else ch[1][i - 1]
        if SIZE_RULE == "lag" and self.grow is not None:
            z = self.lag_sizes(size2, dn0, m, caps2, f2 if LAGR else None)
        elif SIZE_RULE == "r0" and ch is not None:
            z[j] = max(1.0, z[j] + ch[0] - sum(size2[i] - self.size[i] for i in range(1, j + 1)))
        return z, f2

    def lag_sizes(self, size2, dn0, m, caps2=None, f2p=None):
        """Round 5's lag rule for level j = m+1 (pushing levels 1..m above it); round 7 uses it in calm trees too."""
        j, z = m + 1, list(size2)
        u = max(0.0, self.grow[j])
        f0 = min(self.f[0], self.q)
        lag = min(u, f0) * dn0 * self.F0 / f0 if f0 > 0 else 0.0

        def delay(i):
            # round 18 LAGR (busy trees, with the range layer's flows): level i delays the stream by the change in how
            # long a byte stays in it, size'/f' - size/f (a byte leaves level i when a merge pushes its file on, after
            # the level's residence time), not by the extra bytes over the old flow; with no copies dropped f' = f
            if f2p is not None and f2p[i] > 0 and self.f[i] > 0:
                r1, r2 = self.size[i] / self.f[i], size2[i] / f2p[i]
                fin = self.f[i - 1] if i > 1 else min(self.f[0], self.q)
                # only a level that drops copies (bytes in - bytes out - bytes kept > 2% of what arrives) changes its
                # outflow when it holds data longer; with nothing dropped f' = f and the delay is the plain one
                if fin > 0 and (fin - self.f[i] - self.grow[i]) / fin > LAGR_DROP:
                    return r2 - r1
            return (size2[i] - self.size[i]) / self.f[i]
        lag += sum(min(u, self.f[i]) * delay(i) for i in range(1, m + 1))
        zj = max(1.0, self.size[j] - lag)
        if j < self.dp:
            spill = max(0.0, zj - caps2[j]) if caps2 is not None and zj > self.size[j] else 0.0
            z[j] = zj - spill
            z[j + 1] = self.size[j + 1] + spill
            for i in range(j + 2, self.dp + 1):
                z[i] = self.size[i]
        else:
            z[j] = zj
        return z

    def flows_at(self, size2, f_r0):
        return f_r0 if (R0_FLOWS and f_r0 is not None) else self.flows2(size2)

    def residences(self, size, f=None):
        f = f or self.f
        return [size[k] / f[k] if f[k] else float("inf") for k in range(self.dp + 1)]

    def pour(self, x0, caps):
        """BK4' (top-down fill): from the state at X, levels above their cap drain to it and every byte that
        arrived since X, plus what drained, settles in the highest level with room; the last level takes the rest.
        Data only moves down, and score-first serving keeps it as high as the caps allow."""
        dp = self.dp
        z = [0.0] + [x0["levels"][i]["bytes_end"] for i in range(1, dp + 1)]
        inflow = sum(self.size[1:dp + 1]) - sum(z[1:])
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

    def predict(self, k2, x0=None):
        """Settled Δ (K = kin -> k2) for this window, and the after-state for the read model."""
        B, q, F0 = self.B, self.q, self.F0
        p = dict(busy=self.busy)
        size2 = list(self.size)
        if not self.busy:                                   # calm: v1 K1-K3, K4
            ke, ke2 = min(self.kin, self.cap), min(k2, self.cap)
            kap = ke2 / ke
            d1 = self.f[1] * ke * F0 / q
            ratio = (2 * B + d1) / (2 * B + kap * d1)
            p.update(dn0=(ke2 - ke) * (0.5 + self.lam * q), dbatch=ke2 - ke,
                     dw0=(q * B / F0) * (1 / ke2 - 1 / ke), dom={1: self.om[1] * (ratio - 1)})
            R = q * (1 + B / (ke * F0)) + sum(self.f[i] * (1 + self.om[i]) for i in range(1, self.dp))
            R2 = R + q * B / F0 * (1 / ke2 - 1 / ke) + self.f[1] * p["dom"][1]
            p["stall"] = self.rho * R2 / R >= 1                # G9'
            p["nb2"] = None
            m = 0
            while m + 1 < self.dp and self.f[m + 1] > 0:
                m += 1
            if MIDBURST and m >= 1:
                # round 13: once per L0 cycle (C = K_e t_F ops) each pushing level i sheds E_i = f_i C bytes, one
                # file at a time, highest score first, on the one worker; it rises while level i-1 drains (L1: at once,
                # when the L0 merge installs) and falls while it drains (D_i = lam (s_i + o_i) C ops: the bytes its
                # merges read). Mean over the cycle above its resting point: E_i (D_{i-1} + D_i) / 2C, linear in C.
                c, c2 = ke * self.tF, ke2 * self.tF
                for i in range(1, m + 1):
                    size2[i] += self.f[i] * self.lam * ((self.rd[i - 1] if i > 1 else 0.0) + self.rd[i]) * (c2 - c) / 2
            if CALM_LAG and R0_ON and SIZE_RULE == "lag" and self.grow is not None:
                if m >= 1:
                    size2 = self.lag_sizes(size2, p["dn0"], m)    # with MIDBURST, the L1..m terms are no longer 0
        else:                                               # busy: BK1-BK6
            kh, kh2 = min(self.kin, B / F0), min(k2, B / F0)
            nf = self.nfire
            # fix 2: §2.2's own test. Pending-bound when the state's estimate is at 64 GiB; L0-bound when not and
            # the median L0 merge takes >= 20 files; else neither (busy levels, no throttle: hold the estimate)
            med = self.hold["med"] if self.hold and self.hold["med"] is not None else nf
            pend_now = est(self.size, self.S, nf * F0) >= P_SOFT or med < N_SLOW - 0.5
            sig = nf / kh
            water = [2 <= i < self.dp and self.size[i] >= WATER * sig * self.S[i] for i in range(self.dp + 1)]

            def state0(s):
                z = list(self.size)
                z[1] = s * self.S[1]
                for i in range(2, self.dp):
                    z[i] = self.size[i] * s / sig if water[i] else min(self.size[i], s * self.S[i])
                return z
            z_sig = state0(sig)

            def state(s):
                z = state0(s)
                if R18C:                 # r18: the last level holds the rest (change from the own merge-moment state)
                    z[self.dp] = max(1.0, self.size[self.dp] - sum(z[i] - z_sig[i] for i in range(1, self.dp)))
                return z

            def e_at(s, nb):
                return est(state(s), self.S, nb * F0)

            target = e_at(sig, nf) if pend_now else P_SOFT  # pending or neither: hold the estimate where it is now
            dw0 = (q * B / F0) * (1 / kh2 - 1 / kh)

            def after(s2, nf2):
                # BK4': caps at the water level (L1's adds its batch sawtooth, which scales with the batch); the step
                # and the twin are both poured from the state at X with the same inflow, and only their difference
                saw = max(0.0, self.size[1] - sig * B)
                caps = [0.0] + [sig * self.S[i] for i in range(1, self.dp + 1)]
                caps2 = [0.0] + [s2 * self.S[i] for i in range(1, self.dp + 1)]
                caps[1] += saw
                caps2[1] += saw * nf2 / nf
                if x0 is not None and x0["deepest"] <= self.dp:
                    z, z2 = self.pour(x0, caps), self.pour(x0, caps2)
                    size2 = [self.size[i] + z2[i] - z[i] for i in range(self.dp + 1)]
                else:
                    caps2 = None
                    size2 = state(s2)
                    size2[1] = self.size[1] + (s2 - sig) * B
                    size2[self.dp] = self.size[self.dp] - sum(size2[i] - self.size[i] for i in range(1, self.dp))
                size2 = [max(v, 1.0) for v in size2]
                size2, f_r0 = self.r0_apply(size2, (nf2 - nf) / 2, caps2)
                d1, d1b = self.f[1] * nf * F0 / q, self.f[1] * nf2 * F0 / q
                dom = {1: self.om[1] * (size2[2] / self.size[2]) * (sig * B + d1 / 2) / (s2 * B + d1b / 2)
                       - self.om[1]}
                for i in range(2, self.dp):
                    if self.size[i + 1] > 0 and size2[i] > 0:
                        dom[i] = self.om[i] * (size2[i + 1] / self.size[i + 1]) * (self.size[i] / size2[i]) - self.om[i]
                return size2, dom, f_r0

            # L0-bound after: fix b's hold rule gives the files per merge, a fixed point because the levels' sizes
            # (and so the merge work) depend on the water level n/K̂'. Without the twin's timings: v2's N_slow.
            nf2 = s2 = None
            if self.hold is None:
                n0 = nf if not pend_now else N_SLOW
                if e_at(n0 / kh2, n0) < target:
                    s2, nf2 = n0 / kh2, n0
                nf_l0 = n0
            else:
                g_prev = None
                for n in range(N_SLOW, L0_STOP + 1):
                    nf_l0 = n
                    if e_at(n / kh2, n) >= target:
                        break                                # the estimate binds first: pending-bound after
                    size_n, dom_n, fr_n = after(n / kh2, n)
                    dRr = self.dreads(self.flows_at(size_n, fr_n), dom_n)
                    g = self.hold_files(n, nf, dw0, dRr) - n
                    if g <= 0:
                        nf2 = n if g_prev is None or g == 0 else (n - 1) + g_prev / (g_prev - g)
                        s2 = nf2 / kh2
                        break
                    g_prev = g
                else:
                    nf2, s2 = float(L0_STOP), L0_STOP / kh2
            if nf2 is None:                                  # pending-bound after: bisection on the estimate
                lo, hi = 1.0, nf_l0 / kh2
                for _ in range(50):
                    mid = (lo + hi) / 2
                    if e_at(mid, mid * kh2) < target:
                        lo = mid
                    else:
                        hi = mid
                s2 = lo
                nf2 = min(s2 * kh2, nf_l0)
            size2, dom, f_r0 = after(s2, nf2)
            p["f2"] = self.flows_at(size2, f_r0)
            p["r0"] = f_r0 is not None
            da = 0.0
            if L0A != "0" and self.l0a is not None and self.l0s and self.l0o is not None and self.size[1] > 0:
                b1 = self.l0s + self.l0o
                b2 = nf2 * F0 + self.l0o * (s2 / sig if R18O == "sig" else size2[1] / self.size[1])
                if L0A == "1":
                    da = self.l0a * (b2 / b1 - 1)
                elif L0A == "3":
                    c3 = getattr(self, "c03", None)
                    if c3 and c3["tau"] > 0 and c3["s"]:
                        Nm = sum(v / F0 for v in c3["s"]) / len(c3["s"])
                        Nb = sum((v / F0) ** 2 for v in c3["s"]) / sum(v / F0 for v in c3["s"])
                        delayed = c3["r0"] <= 1.15 * DELAY_CAP / self.q
                        G_ = 1 / 0.64

                        def g_(tt):
                            z = tt * c3["r0"] / c3["M"]
                            return math.log(1 + (G_ - 1) * z) / math.log(G_) if delayed else z
                        tau2 = c3["tau"] * b2 / b1
                        x2 = c3["x"] * g_(tau2) / g_(c3["tau"]) if g_(c3["tau"]) > 0 else c3["x"]
                        self._x3 = (b2 / b1, tau2, x2, c3["x"], delayed, c3["tau"])
                        self._b3 = ((nf2 * F0 + self.l0o * s2 / sig) / b1, (nf2 * F0 + self.l0o * size2[1] / self.size[1]) / b1)
                        part = __import__("os").environ.get("L0A3PART", "x")   # "x" (default): the spread term changed nothing
                        da = ((x2 - c3["x"]) if part in ("both", "x") else 0.0) + \
                            ((nf2 - nf) * (Nb / Nm - 1) / 2 if part in ("both", "nb") else 0.0)
                elif self.hold is not None and self.hold["rf"] and self.hold["rs"] and self.hold["tau"]:
                    h = self.hold

                    def a_of(n, b):
                        tau = h["tau"] * b / b1                      # seconds: same merge speed
                        if n >= N_SLOW:
                            return tau * h["rs"] / h["M"]
                        t20 = (N_SLOW - n) * h["M"] / h["rf"]        # seconds until L0 holds 20 files
                        if tau <= t20:
                            return tau * h["rf"] / h["M"]
                        return (N_SLOW - n) + (tau - t20) * h["rs"] / h["M"]
                    da = a_of(nf2, b2) - a_of(nf, b1)
            p["_da"] = da
            p.update(dn0=(nf2 - nf) / 2 + da, dbatch=nf2 - nf, dw0=dw0, dom=dom,
                     stall=(nf2 >= N_SLOW - 0.5), sig=sig, sig2=s2, nb2=nf2, kh2=kh2)
        p["dsize"] = {i: size2[i] - self.size[i] for i in range(2, self.dp + 1)}
        f2 = p.get("f2") or list(self.f)                     # calm trees: flows stay (pinned, owner)
        p["f2"] = f2
        p["df"] = {i: f2[i] - self.f[i] for i in range(1, self.dp)}
        p["dx"] = {i: self.xw[i] * (f2[i] / self.f[i] - 1 if self.f[i] else 0.0) + f2[i] * p["dom"].get(i, 0.0)
                   for i in range(1, self.dp)}
        p["size2"] = size2
        return p


# ---- reads (fix 2) -------------------------------------------------------------------------------------------
def read_curve(twin, l1, t):
    """G(a): the share of Gets whose key's newest copy is younger than a (memtable included as a jump at 0), from the
    twin's whole-phase per-level read counters; edges from its op-weighted mean state. Linear between edges."""
    wh = twin["whole"]
    if not wh or not wh["gets"]:
        return None
    g = wh["gets"]
    h = [row[2] / g for row in wh["levels"]]
    e0 = wh["levels"][0][0] / g
    ws = [Win(x, l1, t) for x in twin["windows"]]
    ws = [w for w in ws if w.ok]
    if not ws:
        return None
    n0 = sum(w.n0 for w in ws) / len(ws)
    if n0 <= 0:
        return None
    reach = min(1.0, max(sum(h), e0 / n0 + 2 * h[0] / 3))          # 1 − h_m, from (C1) at the twin's own state
    hm = 1.0 - reach
    return dict(h=h, hm=hm)


def curve_of(run, l1, t):
    """G with its edges for one run (main's block, moved here for round 15: a twin's or the step's own)."""
    G = read_curve(run, l1, t)
    if G is not None:                           # the run's edges, op-weighted over its windows
        ws = [Win(x, l1, t) for x in run["windows"]]
        ws = [x for x in ws if x.ok]
        dp = max(x.dp for x in ws)
        n0 = sum(x.n0 for x in ws) / len(ws)
        tF = sum(x.tF for x in ws) / len(ws)
        edges = [n0 * tF]
        for k in range(1, dp + 1):
            r = [res_read(x, x.size, x.f)[k] for x in ws if x.dp >= k]
            if READFIX and r and not any(math.isfinite(v) for v in r):
                edges.append(float("inf"))
                continue
            r = [v for v in r if math.isfinite(v)]
            edges.append(edges[-1] + (sum(r) / len(r) if r else 0.0))
        G["edges"] = edges
    return G


def negate(p):
    """Round 15: a reverse prediction (K' -> 4) as the change step - twin (K' - 4); the K = 4 state kept for #11."""
    n = dict(p)
    for k in ("dn0", "dbatch", "dw0"):
        if n.get(k) is not None:
            n[k] = -n[k]
    for k in ("dom", "dsize", "df", "dx"):
        n[k] = {i: -v for i, v in p[k].items()}
    n["_size4"], n["_f4"] = p["size2"], p["f2"]
    return n


def res_read(w, size, f):
    """Round 12: each level's band width for the read layer. A level that pushes data on: size / outflow (as before).
    A level that pushes nothing keeps every byte that arrives, so its data spans the time it took to arrive:
    size / inflow (the flow out of the level above). Neither: unbounded (its edge at infinity)."""
    f = f or w.f
    if not READFIX:
        return w.residences(size, f)
    out = []
    for k in range(w.dp + 1):
        if f[k]:
            out.append(size[k] / f[k])
        elif k > 0 and f[k - 1]:
            out.append(size[k] / f[k - 1])
        else:
            out.append(float("inf"))
    return out


def total_probes(G, w, n0, res):
    """(C1)+(C2): probes per Get with L0 holding N(t) uniform on [0, 2·n0] and band edges A_i."""
    h, hm = G["h"], G["hm"]
    dp = w.dp
    A = [n0 * w.tF]
    for k in range(1, dp + 1):
        A.append(A[-1] + res[k])
    # G's points at the TWIN's edges are fixed by the counters; G itself does not move with K.
    pts = [(0.0, hm)]
    cum = hm
    for i in range(0, dp + 1):
        cum += h[i] if i < len(h) else 0.0
        pts.append((G["edges"][i], cum))

    def Gf(a):
        if READFIX and math.isinf(a):
            return pts[-1][1]
        for (a0, g0), (a1, g1) in zip(pts, pts[1:]):
            if a <= a1:
                if READFIX and math.isinf(a1):
                    return g0                      # round 12: flat up to an unbounded band's edge
                return g0 + (g1 - g0) * (a - a0) / (a1 - a0) if a1 > a0 else g1
        return pts[-1][1]
    nb = 2 * n0
    span = nb * w.tF
    steps = 400
    e0 = h0 = 0.0
    for j in range(steps):
        a0, a1 = span * j / steps, span * (j + 1) / steps
        dg = Gf(a1) - Gf(a0)
        x = (a0 + a1) / 2 / w.tF
        e0 += (x - x * x / (2 * nb)) * dg
        h0 += (1 - (a0 + a1) / 2 / span) * dg
    e0 += (1 - Gf(span)) * nb / 2
    probes = e0 + (1 - hm - h0)                             # P_1
    for i in range(2, dp + 1):
        probes += 1 - Gf(A[i - 1])
    return probes


# ---- scoring ---------------------------------------------------------------------------------------------------
def s_label(s):
    """The setting label of a Setting, from its twin's run path (.../suite-W3/T6-l1128-k4/...)."""
    parts = Path(s.twin["run"]).parts if "run" in s.twin else ()
    w = next((p[len("suite-"):] for p in parts if p.startswith("suite-")), None)
    tl = next((p for p in parts if p.startswith("T") and "-l1" in p), None)
    return f"{w}-{tl.rsplit('-k', 1)[0]}" if w and tl else ""


def lv(d, k, i):
    """Level i of window k, or None when the run has no such level there."""
    L = d["windows"][k]["levels"]
    return L[i] if i < len(L) else None


def size_at(d, k, i):
    x = lv(d, k, i)
    return x["bytes_mean"] if x else 0.0


class KeepNoise(Setting):
    """The twin of record (D-35 draft) with the three original repeats' noise limits."""

    def __init__(self, tdir, lab):
        super().__init__(tdir, lab)
        self._old = Setting(NOISE, lab)
        self._plain = {}

    def thr(self, q, i=0):
        return self._old.thr(q, i)

    def thr_plain(self, i):
        """D-33's noise rule on the repeats' own sizes (no window-0 subtraction): round 7's plain gap."""
        if i not in self._plain:
            reps = self._old.reps
            vs = [st.variance([size_at(r, w, i) for r in reps]) for w in range(1, len(reps[0]["windows"]))]
            self._plain[i] = 3 * math.sqrt(2) * math.sqrt(sum(vs) / len(vs))
        return self._plain[i]


def mean(v):
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else None


def twin_cycles(s):
    """The twin's (repeat 1) cycles_k windows, each with its median files per L0 merge, and the run's slowed write
    rate (keys/s with L0 >= 20 over the whole run: the delayed-rate cap, the same in every window)."""
    p = CYC / f"{s_label(s)}-r1.json"
    if not p.exists():
        return [], None
    d = json.loads(p.read_text())
    for k, c in enumerate(d["windows"]):
        n = sorted(cy["nfire"] for cy in d["cycles"] if cy["w"] == k)
        c["_median_files"] = n[len(n) // 2] if n else None
    ts = sum(c["t_slow"] for c in d["windows"])
    return d["windows"], (sum(c["keys_slow"] for c in d["windows"]) / ts if ts > 1.0 else None)


def score_run(step, s, l1, t, k2, G):
    last = len(step["windows"]) - 1
    sp = settle_point(step, s)
    settled = sp < last
    out = dict(_settle=sp, _settled=settled, _ok=step["ok"])
    twin = s.twin
    x0 = twin["windows"][0]
    cw, run_rs = twin_cycles(s)
    rp = Path(step["run"]).parts
    sc_p = CYC / f"{rp[-6][len('step-'):]}-{rp[-5].rsplit('-k', 1)[0]}-{rp[-4]}.json"
    sc_d = json.loads(sc_p.read_text()) if sc_p.exists() else None
    mach = (1.0, 1.0)
    if MACH and sc_d and cw:
        a, b = Win.timed(x0, cw[0], run_rs), Win.timed(step["windows"][0], sc_d["windows"][0], run_rs)
        if a and b and a["w"] > 0 and a["tau"] > 0:
            mach = (b["w"] / a["w"], b["tau"] / a["tau"] if MACH_MODE == "wt" else 1.0)
    out["_mach"] = mach
    if ROLL:                                 # round 15: the step's own windows, cycles and slowed rate
        x0 = step["windows"][0]
        own_c = sc_d["windows"] if sc_d else []
        for k, c in enumerate(own_c):
            n_ = sorted(cy["nfire"] for cy in sc_d["cycles"] if cy["w"] == k)
            c["_median_files"] = n_[len(n_) // 2] if n_ else None

        def own_rs(k):
            ts = sum(c["t_slow"] for c in own_c[:k + 1])
            return sum(c["keys_slow"] for c in own_c[:k + 1]) / ts if ts > 1.0 else None
        run_rs = own_rs(0)
        G = curve_of(step, l1, t)

    _rp3 = step["run"].split("/")
    own_c03 = c03_parts(f"{_rp3[-6][len('step-'):]}-{_rp3[-5].replace('-k4', '')}-{_rp3[-4]}") if L0A == "3" else None

    def mk(k):
        if ROLL:
            w = Win(step["windows"][k], l1, t, own_c[k] if k < len(own_c) else None, own_rs(k),
                    step["windows"][k - 1] if k > 0 else None, kin=k2 if k >= 1 else K0)
        else:
            w = Win(twin["windows"][k], l1, t, cw[k] if k < len(cw) else None, run_rs,
                    twin["windows"][k - 1] if k > 0 else None)
        w.mach = mach
        w.c03 = (own_c03 or {}).get(k) if ROLL else None
        w._k = k
        return w
    wins = [mk(w) for w in range(sp, last + 1)] if settled else []
    if ROLL:                                 # reverse: from the step's own window at K' to K = 4, negated
        preds = [(w, negate(w.predict(K0, x0))) for w in wins if w.ok]
        if __import__("os").environ.get("DUMPA") and step["run"].find("step-W4") >= 0:   # diag: per-window L0 parts
            for w, p in preds:
                print("DUMPA", step["run"].split("/")[-5], step["run"].split("/")[-4], w._k, "busy" if w.busy else "calm",
                      "own_nfire %.2f" % w.nfire, "own_n0 %.2f" % w.n0, "pred dn0 %+.2f dbatch %+.2f da %+.2f" % (
                          p["dn0"], p["dbatch"], -p.get("_da", 0.0)),
                      "x3 %s" % (",".join("%.2f" % v for v in getattr(w, "_x3", ())) if hasattr(w, "_x3") else "-"),
                      "meas dn0 %s dbatch %s" % tuple("%+.2f" % v if v is not None else "None" for v in (
                          delta(step, s.twin, "l0_files", 0, w._k), delta(step, s.twin, "files_in", 0, w._k))))
    else:
        preds = [(w, w.predict(k2, x0)) for w in wins if w.ok]

    # round 9: L0's schedule after X (calm trees), from the step's own records before X
    sched = None
    ev_p = EVENTS / sc_p.name
    if L0SCHED and settled and ev_p.exists():
        xw0 = step["windows"][0]
        F0x = xw0["flush_bytes"] / xw0["flushes"]
        sched = l0_schedule.Schedule(ev_p, step["windows"][1]["lo"], xw0["levels"][0]["files_end"], F0x,
                                     step["windows"][-1]["hi"] + 1)
        cap_x = math.ceil(l1 * MIB / F0x)
        ke_s, ke_t = min(k2, cap_x), min(K0, cap_x)
        calm_upto = []
        for j in range(last + 1):
            cj = Win(step["windows"][j] if ROLL else twin["windows"][j], l1, t)
            calm_upto.append((calm_upto[-1] if calm_upto else True) and cj.ok and not cj.busy)

    def dw0_r9(w, p):
        if sched is None or w.busy or not calm_upto[w._k]:
            return p["dw0"]
        a = step["windows"][w._k]
        return (sched.window_bytes(ke_s, a["lo"], a["hi"], w.F0, l1 * MIB)
                - sched.window_bytes(ke_t, a["lo"], a["hi"], w.F0, l1 * MIB)) / a["ops"]

    def mean_files(ke, lo, hi):
        """L0's op-weighted mean file count over [lo, hi) on the schedule (flush ends +1, merge ends -n)."""
        tot = 0.0
        for mu in sched.flushes:
            if mu >= sched.X and lo < mu < hi:
                tot += hi - mu
        for e, n in sched.merges(ke):
            if lo < e < hi:
                tot -= n * (hi - e)
        return sched.files_at(ke, lo) + tot / (hi - lo)

    def dn0_r10(w, p):
        """Round 10, fix A: L0's mean files per window from round 9's schedule (calm windows), the K' schedule's
        mean minus K = 4's; busy windows and runs without a schedule keep the long-run change."""
        if not L0MEAN or sched is None or w.busy or not calm_upto[w._k]:
            return p["dn0"]
        a = step["windows"][w._k]
        return mean_files(ke_s, a["lo"], a["hi"]) - mean_files(ke_t, a["lo"], a["hi"])

    def mean_batch(ke, lo, hi):
        """Mean files of the schedule's L0 merges ending in [lo, hi) (windows_k counts a merge in its end's window)."""
        ns = [n for e, n in sched.merges(ke) if lo <= e < hi]
        return sum(ns) / len(ns) if ns else None

    def dbatch_r11(w, p):
        """Round 11, fix A2: files per L0 merge per window from round 9's schedule (calm windows): the K' schedule's
        mean minus K = 4's, so the first merge after X takes all n_x files when n_x >= K_e'."""
        if not L0BATCH or sched is None or w.busy or not calm_upto[w._k]:
            return p["dbatch"]
        a = step["windows"][w._k]
        x, y = mean_batch(ke_s, a["lo"], a["hi"]), mean_batch(ke_t, a["lo"], a["hi"])
        return p["dbatch"] if x is None or y is None else x - y

    def one(qid, q, i, val, extra=0.0):
        if not settled or not preds:
            out[qid] = dict(hit=None, why="not settled")
            return
        m, th = settled_mean(step, s, q, i, sp), s.thr(q, i)
        pr = mean([val(w, p) for w, p in preds])
        out[qid] = dict(hit=hit(m, pr, th, extra), meas=m, pred=pr, thr=th)

    one("2", "l0_files", 0, dn0_r10, 1.0)
    one("2s", "l0_files", 0, dn0_r10)
    one("3", "files_in", 0, dbatch_r11, 1.0)
    one("3s", "files_in", 0, dbatch_r11)
    one("4", "x", 0, dw0_r9)
    one("5", "omega", 1, lambda w, p: p["dom"][1])
    one("5b", "x", 1, lambda w, p: p["dx"][1])

    if settled and preds:
        deepest = step["windows"][last]["deepest"]
        misses, scored, sizes = [], 0, []
        for i in range(2, deepest + 1):
            for q, extra in (("size", FILE_BYTES), ("x", 0.0), ("omega", 0.0)):
                if q != "size" and i == deepest:
                    continue
                held = False
                if q == "size":
                    pr = mean([p["dsize"].get(i, 0.0) for w, p in preds])
                    # round 3: no band for N_fire (fix b's hold rule predicts it); v1's one-file allowance only
                    extra = FILE_BYTES
                    tl = [lv(twin, k, i) for k in range(sp, last + 1)]
                    held = i < deepest and all(x and (x["f_per_op"] or 0) > 0 for x in tl)
                elif q == "x":
                    pr = mean([p["dx"].get(i, 0.0) for w, p in preds])
                else:
                    pr = mean([p["dom"].get(i, 0.0) for w, p in preds])
                if held and MEASURE == "hybrid":             # round 7: the plain gap after the change
                    gap = mean([size_at(step, k, i) - size_at(twin, k, i) for k in range(sp, last + 1)])
                    h = hit(gap, pr, s.thr_plain(i), extra)
                else:
                    h = hit(settled_mean(step, s, q, i, sp), pr, s.thr(q, i), extra)
                if h is None:
                    continue
                scored += 1
                if q == "size":
                    sizes.append((i, held, h))
                if not h:
                    misses.append(f"{q}{i}")
        out["6"] = dict(hit=(not misses) if scored else None, misses=misses, sizes=sizes)
        misses, scored = [], 0
        store = 0.0                                   # round 9: L0's storage term, every twin window after X calm
        if sched is not None:
            gate = True
            for k in range(1, last + 1):
                wk = mk(k)
                if not wk.ok:
                    gate = False
                    break
                pk = wk.predict(K0 if ROLL else k2, x0)
                gate = gate and not pk["busy"] and not pk["stall"]
            if gate:
                lo_op, hi_op = step["windows"][sp]["lo"], step["windows"][last]["hi"]
                O = sum(step["windows"][k]["ops"] for k in range(sp, last + 1))
                xl = (step if ROLL else twin)["windows"][last]
                dn = ((sched.files_at(ke_s, hi_op) - sched.files_at(ke_s, lo_op))
                      - (sched.files_at(ke_t, hi_op) - sched.files_at(ke_t, lo_op)))
                store = -dn * (xl["flush_bytes"] / xl["flushes"]) / O
                out["_store"] = store
        for i in range(1, deepest):
            src = step if ROLL else twin
            tf = [src["windows"][k]["levels"][i]["f_per_op"] or 0.0 for k in range(sp, last + 1)
                  if i < len(src["windows"][k]["levels"])]
            push = store != 0.0 and tf and sum(tf) / len(tf) > 0
            h = hit(settled_mean(step, s, "f", i, sp), mean([p["df"].get(i, 0.0) for w, p in preds]) + (store if push else 0.0),
                    s.thr("f", i))
            if h is None:
                continue
            scored += 1
            if not h:
                misses.append(f"f{i}")
        out["10"] = dict(hit=(not misses) if scored else None, misses=misses)
        if G is not None:
            vals = []
            for w, p in preds:
                res = res_read(w, w.size, w.f)
                if ROLL:                     # own state (K') minus the predicted K = 4 state
                    res4 = res_read(w, p["_size4"], p["_f4"])
                    own = total_probes(G, w, w.n0, res)
                    vals.append(own - total_probes(G, w, w.n0 - dn0_r10(w, p), res4))
                    continue
                res2 = res_read(w, p["size2"], p["f2"])
                before = total_probes(G, w, w.n0, res)
                after = total_probes(G, w, w.n0 + dn0_r10(w, p), res2)
                vals.append(after - before)
            pr = mean(vals)
            m, th = settled_mean(step, s, "ppg", 0, sp), s.thr("ppg", 0)
            out["11"] = dict(hit=hit(m, pr, th), meas=m, pred=pr, thr=th)
    else:
        for qid in ("6", "10", "11"):
            out[qid] = dict(hit=None, why="not settled")

    # 8: the first L0 merge after X, from the state at X (window 0's end). OWN_X (round 3, after seeing): the step
    # run's own window 0 (its tree and its worker's speeds just before the change, which is what the controller
    # sees when it acts; the twin may have run on another node, its worker up to a third faster); else the twin's.
    if OWN_X:
        xs = step["windows"][0]
        w0 = Win(xs, l1, t, sc_d["windows"][0] if sc_d else None, run_rs)
    else:
        xs, w0 = x0, mk(0)
    m = step.get("first_l0_after_x")
    if w0.ok and m is not None:
        n_x = step["windows"][0]["levels"][0]["files_end"]
        kh2 = min(k2, w0.B / w0.F0)
        L = xs["levels"]
        sig_x = [L[i]["bytes_end"] / w0.S[i] for i in range(1, w0.dp)]
        smax = max([1.0] + sig_x)
        n_t = math.ceil(kh2 * smax - 1e-9)
        tau_job = w0.lam * xs["merge_read"] / xs["merges_timed"] if xs["merges_timed"] else 0.0
        if not w0.busy:
            ke2 = min(k2, w0.cap)
            files, d = max(n_x, ke2), max(0, ke2 - n_x) * w0.tF
        else:
            # T-K, busy: L0's score (N_x + t/t_F)/K̂' rises with each flush while the worker serves the highest other
            # score, so the top group's common score falls at 1/(λ·Σ S_i(1 + ω_i)) per op. L0 merges where they meet.
            # λ is the slowed one once L0 holds 20 files (A12: writes slowed, far fewer operations per merged byte);
            # at 36 files writes stop, so no more operations pass.
            # Fix 1 (round 3): λ in time, operations per second over the worker's merge speed with the gaps between
            # jobs counted (window 0 of the twin); the drain pushes data through every draining level below it.
            h0 = w0.hold
            if h0 is not None and h0["vr"]:
                lam_f, lam_s = h0["rf"] / h0["kpo"] / h0["vr"], h0["rs"] / h0["kpo"] / h0["vr"]
            else:
                lam_f = lam_s = w0.lam
            w0p = W0S / f"{sc_p.stem}.json"
            if W0SPEED and w0p.exists():       # round 14, fix 2: window 0's other merges in the same band
                agg = json.loads(w0p.read_text())["agg"]
                sr, fr = agg.get("slow_rest"), agg.get("fast_rest")
                if sr and sr["read"] > 0 and sr["sec"] > 0:
                    lam_s = sr["ops"] / sr["read"]
                if fr and fr["read"] > 0:
                    lam_f = fr["ops"] / fr["read"]
            out["_lam_s"] = lam_s

            def drain_bytes(group):
                """Bytes the worker reads to lower every level in the group by one unit of score: a level pushes
                out its own S_i plus whatever the draining level just above pushed into it."""
                tot, carry = 0.0, 0.0
                for i in range(1, w0.dp):
                    if i in group:
                        carry = w0.S[i] + carry
                        tot += carry * (1 + w0.om[i])
                    else:
                        carry = 0.0
                return tot
            sc = sorted(((L[i]["bytes_end"] / w0.S[i], i) for i in range(1, w0.dp)), reverse=True)
            t20 = max(0.0, (N_SLOW - n_x) * w0.tF)
            t0, s_top, grp, j = 0.0, sc[0][0], [sc[0][1]], 1
            snap_p = SNAP / sc_p.name
            if DRAIN != "off" and (DRAIN == "flat" or snap_p.exists()):
                d = drain_order.wait({i: v for v, i in sc}, w0.S, w0.om, n_x, kh2, w0.tF, lam_f, lam_s, t20, w0.dp,
                                     drain_order.Snap(snap_p) if DRAIN == "order" else None)
                out["_drain"] = DRAIN
            while "_drain" not in out:
                lam = lam_f if t0 < t20 else lam_s
                r = 1.0 / (lam * drain_bytes(grp))
                tm = (s_top + r * t0 - n_x / kh2) / (1.0 / (w0.tF * kh2) + r)
                nxt = sc[j][0] if j < len(sc) else 1.0
                t_next = t0 + (s_top - nxt) / r
                t_end = min(t_next, t20) if t0 < t20 else t_next
                if tm <= t_end or (j >= len(sc) and t0 >= t20):
                    d = max(0.0, tm)
                    break
                s_top -= r * (t_end - t0)
                t0 = t_end
                if t_end == t_next and j < len(sc):
                    grp.append(sc[j][1])
                    j += 1
            d = min(d, max(0.0, (L0_STOP - n_x) * w0.tF))
            files = n_x + d / w0.tF
            if L0WHOLE:                     # round 10, fix B: whole files; L0's score moves only when a flush ends
                files = math.ceil(files - 1e-9)
        out["8a"] = dict(hit=abs(m["after_x"] - d) <= w0.tF + tau_job + 0.1 * d, meas=m["after_x"], pred=d)
        out["8b"] = dict(hit=abs(m["files_in"] - files) <= 1, meas=m["files_in"], pred=files)
        out["_cycle"] = (d + files * w0.tF) if w0.busy else min(k2, w0.cap) * w0.tF
    else:
        out["8a"] = out["8b"] = dict(hit=None, why="no merge after X or no state")

    # 7: writes slowed (L0 reaches 20 files) in any post window, or while L0 waits for its first merge after X
    if ROLL:                                 # round 15: forward from the step's own window 0
        pp = [w0.predict(k2, x0)] if w0.ok else []
    else:
        pw = [mk(w) for w in range(1, last + 1)]
        pp = [w.predict(k2, x0) for w in pw if w.ok]
    peak = max(step["windows"][w]["l0_peak"] for w in range(1, last + 1))
    if pp:
        first = out["8b"].get("pred")
        pred_stall = any(p["stall"] for p in pp) or (first is not None and first >= N_SLOW)
        p7 = {}
        if P7RULE != "0" and ROLL and w0.ok and w0.l0s and w0.l0o is not None and w0.size[1] > 0:
            # the window's own L0 merges: seconds per merge and flushes per second with L0 < 20 (own cycles); with
            # no cycles, a window whose L0 never reached 20 ran unslowed, so its flushes per merge in ops are the same
            h0 = w0.hold
            b0 = w0.l0s + w0.l0o
            if h0 is not None and h0.get("tau_run") and h0["rf"] and h0["M"]:
                a0 = h0["tau_run"] * h0["rf"] / h0["M"]      # flushes a window-0 merge would see at the L0 < 20 rate
            elif xs["l0_peak"] < N_SLOW and w0.l0a is not None:
                a0 = w0.l0a
            else:
                a0 = None
            if a0 is not None and b0 > 0:
                def peak_of(n, l1b):
                    return n + a0 * (n * w0.F0 + w0.l0o * l1b / w0.size[1]) / b0
                pk1 = pk2 = None
                if first is not None and P7RULE in ("peak", "first"):
                    pk1 = peak_of(first, xs["levels"][1]["bytes_end"])
                if pp and P7RULE in ("peak", "settled"):
                    p0 = pp[0]
                    n2 = p0["nb2"] if p0["busy"] else min(k2, w0.cap)
                    pk2 = peak_of(n2, p0["size2"][1])
                p7 = dict(a0=a0, pk1=pk1, pk2=pk2)
                pred_stall = pred_stall or any(v is not None and v >= N_SLOW for v in (pk1, pk2))
        out["7"] = dict(hit=(peak >= N_SLOW) == pred_stall, meas=peak, pred=pred_stall, **p7)

    # 9: L0 within one cycle (v1's rule, v2's predicted settled change and cycle)
    if settled and "2" in out and out["2"].get("pred") is not None and "_cycle" in out:
        d1 = delta(step, s.twin, "l0_files", 0, 1)
        sm = settled_mean(step, s, "l0_files", 0, sp)
        cyc = out["_cycle"]
        if w0.ok and w0.busy and pp and pp[0].get("sig2"):
            # round 4: a busy tree's L0 settles with its levels: the refill to the new caps (poured at the write
            # bytes per op) or the drain above them (pushed through every draining level at the slowed λ)
            s2, Lx = pp[0]["sig2"], xs["levels"]
            z = [Lx[i]["bytes_end"] for i in range(w0.dp + 1)]
            caps2 = [s2 * w0.S[i] for i in range(w0.dp)]
            fill = sum(max(0.0, caps2[i] - z[i]) for i in range(1, w0.dp))
            tot = carry = 0.0
            for i in range(1, w0.dp):
                carry = max(0.0, z[i] + carry - caps2[i])
                tot += carry * (1 + w0.om[i])
            cyc = max(cyc, fill / w0.q, out.get("_lam_s", w0.lam) * tot)
            if R18S and out.get("8b", {}).get("pred") is not None and pp[0].get("nb2") is not None:
                n1, nst, kh2_ = out["8b"]["pred"], pp[0]["nb2"], pp[0]["kh2"]
                lam_s9 = out.get("_lam_s", w0.lam)
                grp, carry = [], 0.0
                for i in range(1, w0.dp):
                    carry = max(0.0, z[i] + carry - caps2[i])
                    if carry > 0:
                        grp.append(i)
                D, cs, bump = 0.0, 0.0, 0.0
                for i in range(1, w0.dp):
                    if i in grp:
                        cs = w0.S[i] + cs
                        D += cs * (1 + w0.om[i])
                    else:
                        cs = 0.0
                i = 1
                while i in grp:                      # a byte the L0 merge drops in L1 is pushed through the group
                    bump += 1 + w0.om[i]
                    i += 1
                rho = None
                if R18S == "snap" and grp and n1 > nst + 1 and (SNAP / sc_p.name).exists():
                    sx9 = {i: Lx[i]["bytes_end"] / w0.S[i] for i in range(1, w0.dp)}
                    sn9 = drain_order.Snap(SNAP / sc_p.name)
                    s1_, ss_ = n1 / kh2_, nst / kh2_
                    if s1_ > ss_:
                        dR = drain_R(sx9, w0.S, sn9, ss_) - drain_R(sx9, w0.S, sn9, s1_)
                        if dR > 0:
                            D = dR / (s1_ - ss_)
                if grp and lam_s9 > 0 and n1 > nst + 1:
                    bpr = w0.F0 + w0.S[1] / kh2_
                    rho = (bpr + w0.F0 * bump + D / kh2_) / (w0.tF / lam_s9 + D / kh2_)
                    rho = min(max(rho, 0.0), 0.95)
                    nk, ops9, j = n1, out["8a"]["pred"], 1
                    while abs(nk - nst) > 1.0 and j < 60:
                        nk = nst + (nk - nst) * rho
                        ops9 += nk * w0.tF
                        j += 1
                    out["_relax"] = dict(rho=rho, n1=n1, nst=nst, merges=j, ops=ops9, bpr=bpr, bump=w0.F0 * bump, Dk=D / kh2_,
                                         slow_file=w0.tF / lam_s9, grp=grp)
                    cyc = max(cyc, ops9)
            out["_settle_ops"] = cyc
            if TRACE and TRACE in step["run"]:
                print("TRACE9", step["run"].split("/")[-5], step["run"].split("/")[-4], "sig", pp[0].get("sig"), "sig2", s2,
                      "z", [round(v / GIB, 3) for v in z], "caps2", [round(v / GIB, 3) for v in caps2],
                      "fill/q", round(fill / w0.q), "lam_s*tot", round(out.get("_lam_s", w0.lam) * tot), "_cycle", round(out["_cycle"]),
                      "lam_s", out.get("_lam_s"), "lam", w0.lam, "tot GB", tot / 1e9)
        allow = abs(out["2"]["pred"]) * min(1.0, cyc / WINDOW)
        out["9"] = dict(hit=hit(d1, sm, s.thr("l0_files", 0), allow), meas=d1, settled=sm)
    else:
        out["9"] = dict(hit=None, why="not settled")
    return out


def main():
    a = sys.argv[1:] or [str(KC / "data/twins_rec"), str(KC / "data/steps"), str(KC / "v2/out_r18")]
    tdir, sdir, odir = Path(a[0]), Path(a[1]), Path(a[2])
    odir.mkdir(parents=True, exist_ok=True)
    rows, missing = [], []
    for w in ("W1", "W2", "W3", "W4", "W5"):
        for t in range(2, 11):
            for l1 in (64, 128, 256):
                lab = f"{w}-T{t}-l1{l1}"
                s = KeepNoise(tdir, lab)
                G = None if ROLL else curve_of(s.twin, l1, t)   # round 15: each step's own curve (score_run)
                runs = {}
                for k2 in KS[l1]:
                    p = sdir / f"{lab}-K{k2}.json"
                    if not p.exists():
                        missing.append(f"{lab}-K{k2}")
                        continue
                    step = json.loads(p.read_text())
                    res = score_run(step, s, l1, t, k2, G)
                    runs[f"K{k2}"] = (step, res)
                    rows.append(dict(run=f"{lab}-K{k2}", w=w, l1=l1, K=k2, up=k2 > K0, res=res))
                if l1 in CAP_PAIRS and all(k in runs for k in CAP_PAIRS[l1]):
                    ka, kb = CAP_PAIRS[l1]
                    res = cap_pair(runs[ka][0], runs[kb][0], runs[ka][1]["_settle"], runs[kb][1]["_settle"], s)
                    rows.append(dict(run=f"{lab}-{ka}={kb}", w=w, l1=l1, K=int(ka[1:]), up=True, res={"1": res}))
    (odir / "runs.json").write_text(json.dumps(rows, indent=1, default=str))

    v1 = json.loads((KC / "v2/out_r17/runs.json").read_text())   # round 17 (comparison column)
    ws = ("W1", "W2", "W3", "W4", "W5")

    def cell(rr, qid, w):
        sc = [r["res"][qid]["hit"] for r in rr if r["w"] == w and qid in r["res"] and r["res"][qid]["hit"] is not None]
        return (sum(sc) / len(sc), len(sc)) if sc else (None, 0)

    lines = [f"# Cases 1-2 test of the propagation law v2, round 18 (R0NET={int(R0NET)}, LAGR={int(LAGR)}; L0A={L0A}, R18O={R18O or "-"}, R18S={R18S or "-"}, P7RULE={P7RULE}; BUSYTEST={BUSYTEST}, ROLL={int(ROLL)}; NEARSTOP={int(NEARSTOP)}, W0SPEED={int(W0SPEED)}; MIDBURST={int(MIDBURST)}, L0MEAN={int(L0MEAN)}, L0WHOLE={int(L0WHOLE)}, L0BATCH={int(L0BATCH)}, READFIX={int(READFIX)}): results", "",
             f"Runs scored: {sum(1 for r in rows if '=' not in r['run'])}; missing: {len(missing)}; "
             f"not settled: {sum(1 for r in rows if '=' not in r['run'] and not r['res']['_settled'])}", "",
             "Each cell: this scoring's hits % (runs scored) [round 17]. Pass = at least 90% in every workload.", "",
             "| # | Quantity | Part | " + " | ".join(ws) + " | Pass | Pass r17 |",
             "|---|---|---|" + "---|" * (len(ws) + 2)]
    summary = {}
    for qid, name, part in QUANTITIES:
        cells, ok2, ok1, any2 = [], True, True, False
        for w in ws:
            f2, n2 = cell(rows, qid, w)
            f1, _ = cell(v1, qid, w)
            if f2 is not None:
                any2 = True
                ok2 = ok2 and f2 >= 0.9
            if f1 is not None:
                ok1 = ok1 and f1 >= 0.9
            cells.append((f"{100 * f2:.0f}% ({n2})" if f2 is not None else "–")
                         + (f" [{100 * f1:.0f}%]" if f1 is not None else ""))
        summary[qid] = "PASS" if (ok2 and any2) else ("FAIL" if any2 else "n/a")
        lines.append(f"| {qid} | {name} | {part} | " + " | ".join(cells)
                     + f" | {summary[qid]} | {'PASS' if ok1 else 'FAIL'} |")
    (odir / "summary.md").write_text("\n".join(lines) + "\n")
    (odir / "summary.json").write_text(json.dumps(dict(summary=summary, missing=missing), indent=1))
    print("\n".join(lines))
    print(f"V2SCORE_EXIT={0 if not missing else 1}")


if __name__ == "__main__":
    main()
