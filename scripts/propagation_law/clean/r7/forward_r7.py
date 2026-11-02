import os
#!/usr/bin/env python3
"""[clean/r7, 2026-10-28 (owner: "First investigate #2 ... fix this"): r5 + the L0 merge's start rate (law_r7.py's r7
block: start_rate, x_rule) in the settled x-rule and in merge_x (k1t's per-merge x). Modules copied with unique names:
law_r7 (= law_r5 + the r7 block), brake_r7, l0_schedule_r7, fill_r7 (byte-identical to r5's).
interval_preds_r7(..., r7="off") = interval_preds_r5.]
[clean/r5, 2026-10-28 (owner: "First add k2t's #4 term as r5"): r4 + k2t's #4 option (K2T_Q=2,3,4: the L1
bytes the window's L0 merges rewrite, against the law's settled K' rate). Modules copied with unique names: law_r5,
brake_r5, l0_schedule_r5, fill_r5 (byte-identical to r4's). interval_preds_r5(..., r5="off") = interval_preds_r4.]
[clean/r4, 2026-10-28 (owner: "Yes, adopt k2t (#2 and #3 as r4)"): r3 + subagent k2t's case-2 transition
(operator-verified; copied from sub_k2t/forward_k2t.py with unique module names: law_r4, brake_r4, l0_schedule_r4
byte-identical to r3's, fill_r4 = sub_k2t/fill_k2t.py). Adopted settings: K2T_GATE=l0, K2T_WIN=first,
K2T_CAP=brake, K2T_OM=1, K2T_Q=2,3 (the #4 option is not adopted). interval_preds_r4 = interval_preds_k2t with
k2t on; r4="off" gives r3 exactly.]
before the change (l0_schedule_r3.ROLL_L; 0 = r2 exactly).]
[clean/r2, 2026-10-27 (owner: general fixes go in): r1 + subagent k1t's case-1 transition (operator-verified;
copied from sub_k1t/forward_k1t.py) + the brake fix taking the old K (its gate and its no-change path; r1 assumed
K = 4, the only K the single-change runs start from). interval_preds_r2 = interval_preds_k1t with k1t on.]
[clean/r1 copy of clean/forward.py, 2026-10-27 (AFTER SEEING; owner "go, adopt all three"): imports the law copy
r1/law_r1.py and adds interval_preds_r1, the three forward fixes together (subagents om5, stor and brake). With all
three off, interval_preds_r1 is interval_preds; path_checks is unchanged (no fix touches it).]
Forward mode of the frozen cases 1-2 law (round 18, v2/score_k_v2r18.py, unchanged) for the clean test on the 135
multi-change runs (owner 2026-10-27: forward, fast quantities, committed D-entry). Operator, 2026-10-27.

Every prediction for a change K_from -> K_to comes from the run's own state just before the change (D-36 rolling):
the pre-change window (its windows_k record, its cycles_k record, its L0-merge parts), the per-file snapshot at the
change, the pre-change window's merge speeds by L0 band, and L0's schedule from the records before the change.
- path_checks: #7 (writes slowed), #8a (when the first L0 merge after the change begins), #8b (its files). Copied
  from score_run's OWN_X block and its ROLL #7 block, parameterised; the dry run checks they reproduce score_run.
- interval_preds: the law's change, K_from -> K_to, of #2 L0 mean files, #3 files per L0 merge, #4 L0 -> L1 bytes
  written per op, #5 L1 -> L2 overlap, #5b L1 -> L2 bytes written per op: Win(pre, kin=K_from).predict(K_to), with
  round 9-11's calm schedule rules over the post interval when the pre-change window is calm.
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, "/home/cc/node_ops/diag/kcase")
sys.path.insert(0, "/home/cc/node_ops/diag/kcase/v2")
sys.path.insert(0, "/home/cc/node_ops/diag/kcase/clean/r7")
import law_r7 as m  # noqa: E402  (r7: law_r5 + the r7 start-rate block)
import brake_r7 as brake  # noqa: E402  (r7: byte-identical copy of r5/brake_r5.py)
import drain_order  # noqa: E402
import l0_schedule_r7 as l0_schedule  # noqa: E402  (r7: byte-identical copy of r5/l0_schedule_r5.py)

N_SLOW, L0_STOP = m.N_SLOW, m.L0_STOP
FAST = [("2", "l0_files", 0, 1.0), ("3", "files_in", 0, 1.0), ("4", "x", 0, 0.0), ("5", "omega", 1, 0.0),
        ("5b", "x", 1, 0.0)]


def own_rs(cwins):
    """Keys/s while writes are slowed over the given cycles windows (score_run's own_rs)."""
    ts = sum(c["t_slow"] for c in cwins)
    return sum(c["keys_slow"] for c in cwins) / ts if ts > 1.0 else None


def median_files(cyc_doc, k):
    n_ = sorted(cy["nfire"] for cy in cyc_doc["cycles"] if cy["w"] == k)
    return n_[len(n_) // 2] if n_ else None


def inflight_l0(c03_path, X):
    """r7 part j: the L0 -> L1 merge running at the change X, as RocksDB knows it at X: its start op, its L0 files
    and the bytes it reads from L0 (s) and L1 (o), all fixed when the merge is picked. Its end op is used only to know
    that it is still running at X. None when no L0 merge runs at X."""
    p = Path(c03_path)
    if not p.exists():
        return None
    ms = json.loads(p.read_text())["merges"]
    for jb in ms:
        if jb["op"] < X < jb["end_op"]:
            done = [(q["op"], q["end_op"], q["s"] + q["o"]) for q in ms if q["end_op"] <= X]
            return dict(X=X, op=jb["op"], n=jb["n"], s=jb["s"], o=jb["o"], done=done)
    return None


def inflight_state(pre, w0, jf):
    """r7 part j (#8a, #8b; cases 1-2, calm and busy; operator 2026-10-28, after seeing W5's misses). RocksDB runs one
    compaction at a time, and L0's score counts only the files no merge holds (VersionStorageInfo::
    ComputeCompactionScore: num_sorted_runs skips being_compacted files; L1+ count level_bytes_no_compacting). An L0 ->
    L1 merge running at X therefore holds the slot until it installs, its n files do not count toward K', and its
    output lands in L1 when it installs. The law starts from that state: rem = the ops it still runs, at the ops per
    byte read of the before-stretch's own finished L0 merges (Win.lam when it has none: Win.lam pools every level's
    merges, and in busy trees the L0 merges run while writes are slowed, so they cover far fewer ops per byte); L0
    then holds n_X - n + rem / t_F files and L1 holds its bytes at X - o + x_hat, with x_hat = s + o - h (o n +
    s (n - 1) / 2) (part w's form for this merge's own s, o and n; h from the before-stretch's merges, 0 when it has
    none). Returns (rem, files at its end, L1 bytes added)."""
    done = [q for q in jf.get("done", ()) if q[0] >= pre["lo"]]
    lam0 = sum(e - b for b, e, _ in done) / sum(r for _, _, r in done) if done else w0.lam
    rem = max(0.0, jf["op"] + lam0 * (jf["s"] + jf["o"]) - jf["X"])
    L0 = pre["levels"][0]
    mp, fin = L0["merges"], pre["l0l1"]["files_in_mean"]
    h = 0.0
    if mp and fin and L0["s"] + L0["o"] > 0:
        s_f, o_m = L0["s"] / (mp * fin), L0["o"] / mp
        den = mp * (o_m * fin + s_f * fin * (fin - 1) / 2)
        h = (L0["s"] + L0["o"] - L0["x"]) / den if den > 0 else 0.0
    n, s, o = jf["n"], jf["s"], jf["o"]
    x_hat = max(o, s + o - h * (o * n + s * (n - 1) / 2))
    return rem, max(0.0, pre["levels"][0]["files_end"] - n) + rem / w0.tF, x_hat - o


def pre_merge_segs(pc, n_x, d):
    """L0's file count from X to the first merge after it (op d), as pieces (t0, t1, a0) of a0 + (t - t0) / t_F. r5:
    one piece from n_X. r7 part j (pc carries _j): the merge running at X still holds its files until it installs at
    rem, then L0 restarts from the files it left (path_checks' _j n)."""
    j = (pc or {}).get("_j")
    if not j:
        return [(0.0, d, n_x)]
    return [(0.0, j["rem"], n_x), (j["rem"], d, j["n"])]


PC_NOMF = os.environ.get("R7_PC_NOMF", "0") == "1"


def path_checks(xs, cyc0, run_rs, l1, t, k_from, k2, w0s_path, snap_path, measured_first, peak, inflight=None):
    """score_run's #8 (OWN_X) and #7 (ROLL) blocks, from the pre-change window xs (its cycles record cyc0).
    r7 part j (R7_PARTS has j, inflight = inflight_l0(...)): #8a/#8b start from the state when the L0 merge running at
    X installs (inflight_state); without it, or with no merge running at X, the blocks are r5's exactly."""
    out = {}
    w0 = m.Win(xs, l1, t, cyc0, run_rs, kin=k_from)
    x0 = xs
    mf = measured_first
    nomf = mf is None and PC_NOMF                  # the law's first merge must not depend on a next-stretch fact
    if nomf:
        mf = dict(after_x=0.0, files_in=0)
    if w0.ok and mf is not None:
        n_x = xs["levels"][0]["files_end"]
        kh2 = min(k2, w0.B / w0.F0)
        L = xs["levels"]
        bE = [L[i]["bytes_end"] for i in range(len(L))]
        rem = 0.0
        if inflight is not None and "j" in R7_PARTS:
            rem, n_x, dl1 = inflight_state(xs, w0, inflight)
            bE[1] += dl1
            out["_j"] = dict(rem=rem, n=n_x, dl1=dl1)
            if "y" in R7_PARTS:   # the running merge's files, fixed when it was picked
                out["_j"]["nin"] = inflight["n"]
        sig_x = [bE[i] / w0.S[i] for i in range(1, w0.dp)]
        smax = max([1.0] + sig_x)
        tau_job = w0.lam * xs["merge_read"] / xs["merges_timed"] if xs["merges_timed"] else 0.0
        if not w0.busy:
            ke2 = min(k2, w0.cap)
            files, d = max(n_x, ke2), max(0, ke2 - n_x) * w0.tF
            if "_j" in out and m.L0WHOLE:
                files = math.ceil(files - 1e-9)
        else:
            h0 = w0.hold
            if h0 is not None and h0["vr"]:
                lam_f, lam_s = h0["rf"] / h0["kpo"] / h0["vr"], h0["rs"] / h0["kpo"] / h0["vr"]
            else:
                lam_f = lam_s = w0.lam
            if m.W0SPEED and w0s_path is not None and Path(w0s_path).exists():
                agg = json.loads(Path(w0s_path).read_text())["agg"]
                sr, fr = agg.get("slow_rest"), agg.get("fast_rest")
                if sr and sr["read"] > 0 and sr["sec"] > 0:
                    lam_s = sr["ops"] / sr["read"]
                if fr and fr["read"] > 0:
                    lam_f = fr["ops"] / fr["read"]
            out["_lam_s"] = lam_s

            def drain_bytes(group):
                tot, carry = 0.0, 0.0
                for i in range(1, w0.dp):
                    if i in group:
                        carry = w0.S[i] + carry
                        tot += carry * (1 + w0.om[i])
                    else:
                        carry = 0.0
                return tot
            sc = sorted(((bE[i] / w0.S[i], i) for i in range(1, w0.dp)), reverse=True)
            t20 = max(0.0, (N_SLOW - n_x) * w0.tF)
            t0, s_top, grp, j = 0.0, sc[0][0], [sc[0][1]], 1
            if m.DRAIN != "off" and (m.DRAIN == "flat" or (snap_path is not None and Path(snap_path).exists())):
                d = drain_order.wait({i: v for v, i in sc}, w0.S, w0.om, n_x, kh2, w0.tF, lam_f, lam_s, t20, w0.dp,
                                     drain_order.Snap(Path(snap_path)) if m.DRAIN == "order" else None)
                out["_drain"] = m.DRAIN
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
            if m.L0WHOLE:
                files = math.ceil(files - 1e-9)
        d += rem
        out["8a"] = dict(hit=abs(mf["after_x"] - d) <= w0.tF + tau_job + 0.1 * d, meas=mf["after_x"], pred=d,
                         allow=w0.tF + tau_job + 0.1 * d)
        out["8b"] = dict(hit=abs(mf["files_in"] - files) <= 1, meas=mf["files_in"], pred=files)
        if nomf:                                   # nothing to score against: predictions only
            out["8a"].update(hit=None, meas=None)
            out["8b"].update(hit=None, meas=None)
        out["_cycle"] = (d + files * w0.tF) if w0.busy else min(k2, w0.cap) * w0.tF
    else:
        out["8a"] = out["8b"] = dict(hit=None, why="no merge after X or no state")

    pp = [w0.predict(k2, x0)] if w0.ok else []
    if pp:
        first = out["8b"].get("pred")
        pred_stall = any(p["stall"] for p in pp) or (first is not None and first >= N_SLOW)
        p7 = {}
        if m.P7RULE != "0" and m.ROLL and w0.ok and w0.l0s and w0.l0o is not None and w0.size[1] > 0:
            h0 = w0.hold
            b0 = w0.l0s + w0.l0o
            if h0 is not None and h0.get("tau_run") and h0["rf"] and h0["M"]:
                a0 = h0["tau_run"] * h0["rf"] / h0["M"]
            elif xs["l0_peak"] < N_SLOW and w0.l0a is not None:
                a0 = w0.l0a
            else:
                a0 = None
            if a0 is not None and b0 > 0:
                def peak_of(n, l1b):
                    return n + a0 * (n * w0.F0 + w0.l0o * l1b / w0.size[1]) / b0
                pk1 = pk2 = None
                if first is not None and m.P7RULE in ("peak", "first"):
                    pk1 = peak_of(first, xs["levels"][1]["bytes_end"])
                if pp and m.P7RULE in ("peak", "settled"):
                    p0 = pp[0]
                    n2 = p0["nb2"] if p0["busy"] else min(k2, w0.cap)
                    pk2 = peak_of(n2, p0["size2"][1])
                p7 = dict(a0=a0, pk1=pk1, pk2=pk2)
                pred_stall = pred_stall or any(v is not None and v >= N_SLOW for v in (pk1, pk2))
        if "_j" in out:          # r7 part j: the slowdown counts every L0 file (CalculateBaseBytes: files_[0].size(),
            # merged or not), so until the in-flight merge installs at rem L0 holds all of n_X plus rem / t_F flushes
            p7["pk0"] = xs["levels"][0]["files_end"] + out["_j"]["rem"] / w0.tF
            pred_stall = pred_stall or p7["pk0"] >= N_SLOW
        out["7"] = dict(hit=(peak >= N_SLOW) == pred_stall if peak is not None else None, meas=peak, pred=pred_stall,
                        **p7)
    return out, w0


def interval_preds(pre, prev, cyc_pre, rs_pre, c03_pre, l1, t, k_from, k_to, sched, lo, hi, ops, F0x):
    """The law's change K_from -> K_to over the post interval [lo, hi) for the FAST quantities, forward from pre."""
    w = m.Win(pre, l1, t, cyc_pre, rs_pre, prev, kin=k_from)
    w.c03 = c03_pre
    if not w.ok:
        return None, w
    p = w.predict(k_to, pre)
    pr = {"2": p["dn0"], "3": p["dbatch"], "4": p["dw0"], "5": p["dom"].get(1), "5b": p["dx"].get(1)}
    if sched is not None and not w.busy:          # round 9-11's calm schedule rules over the post interval
        cap_x = math.ceil(l1 * m.MIB / F0x)
        ke_s, ke_t = min(k_to, cap_x), min(k_from, cap_x)
        if m.L0SCHED:
            pr["4"] = (sched.window_bytes(ke_s, lo, hi, w.F0, l1 * m.MIB)
                       - sched.window_bytes(ke_t, lo, hi, w.F0, l1 * m.MIB)) / ops

        def mean_files(ke):
            tot = 0.0
            for mu in sched.flushes:
                if mu >= sched.X and lo < mu < hi:
                    tot += hi - mu
            for e, n in sched.merges(ke):
                if lo < e < hi:
                    tot -= n * (hi - e)
            return sched.files_at(ke, lo) + tot / (hi - lo)

        def mean_batch(ke):
            ns = [n for e, n in sched.merges(ke) if lo <= e < hi]
            return sum(ns) / len(ns) if ns else None
        if m.L0MEAN:
            pr["2"] = mean_files(ke_s) - mean_files(ke_t)
        if m.L0BATCH:
            x, y = mean_batch(ke_s), mean_batch(ke_t)
            if x is not None and y is not None:
                pr["3"] = x - y
    pr["_busy"] = w.busy
    return pr, w


def make_schedule(ev_path, X, n_x, F0x, end):
    if ev_path is None or not Path(ev_path).exists():
        return None
    s = l0_schedule.Schedule(Path(ev_path), X, n_x, F0x, end)
    pp = Path(ev_path).parent.parent / "pour" / Path(ev_path).name    # r7 part g: the run's pour ends (build_pour_r7)
    s.ev_path = Path(ev_path)                                  # r7 part v: where the run's other records are
    s.pour = json.loads(pp.read_text())["ends"] if pp.exists() else None
    return s


# ---- r1: the three forward fixes together (AFTER SEEING; owner 2026-10-27 "go, adopt all three") ----
# om5 (case 2 mainly; general): the pour's inflow in forward mode is the run's own L1..deepest growth per op over the
#   pre-change window x the ops from X to the predicted window's middle (Win.fwd_inflow), and #5's L2 baseline is the
#   law's own K = kin continuation at the window (OM5_TWIN). Both gate on Win.fwd_inflow, so they are on here and off
#   wherever fwd_inflow is unset (reverse mode, path_checks, the brake's projected states).
# stor (cases 1-2; general, conservation): what L0 and L1 hold more at an interval's end under K' than under K = 4 is not
#   passed on to L2: Δf_1 = -(r0 F_0 ΔΔN_0 + ΔΔL1)/ops, taken into #5b at the law's x_1 per byte pushed. Calm trees:
#   round 9's schedule in every interval; busy trees: the first interval only, L1 at its water-level cap at both K.
# brake (cases 1-2, only W4 reaches it; general): the window is cut at the forecast 64 GiB crossings of the K = 4 and K'
#   paths; a piece after either crossing takes the law's prediction from the K = 4 path's projected state, with the K'
#   after-state forced to the forecast K' path (p2).
# Composition: om5 sets the pre-window prediction (pieces before both crossings); brake replaces the braked pieces
# (their projected states carry their own inflow, so om5 stays off there); stor's #5b term is added last. The brake
# forecast is built from a Win without fwd_inflow, as in sub_brake, so it does not depend on the window.
m.OM5_INFLOW, m.OM5_TWIN = "tree", True
STOR_PARTS, STOR_TREES = ("l0", "l1"), ("calm", "busy")
BRAKE_MODE = "p2"


def merge_written(pre):
    """r7 part w (calm #4; cases 1-2, general; operator 2026-10-28, after seeing): the bytes an L0 -> L1 merge of n files
    writes, b(n) = n s_f + o_m - h (o_m n + s_f n (n - 1) / 2). RocksDB's merge keeps only a key's newest copy
    (CompactionIterator, no snapshots), so a merge drops the L1 copies its batch rewrites and the batch's own older
    copies; to first order in h, the chance that one flush rewrites a stored key (one h for L0 and L1: the uniform key
    density G7 assumes), the first grows as n and the second as n (n - 1) / 2. L1's bytes read per merge o_m do not
    move with n (an L0 merge reads the L1 that overlaps it, held at its mark in a calm tree). s_f (L0 bytes read per
    file merged), o_m and h come from the before-stretch's own merges, so b(n) gives its measured bytes at its own
    mean n. None when the before-stretch has no merge."""
    L0 = pre["levels"][0]
    mp, fin = L0["merges"], pre["l0l1"]["files_in_mean"]
    if not mp or not fin or L0["s"] + L0["o"] <= 0:
        return None
    s_f, o_m = L0["s"] / (mp * fin), L0["o"] / mp
    den = mp * (o_m * fin + s_f * fin * (fin - 1) / 2)
    h = (L0["s"] + L0["o"] - L0["x"]) / den if den > 0 else 0.0
    return lambda n: n * s_f + o_m - h * (o_m * n + s_f * n * (n - 1) / 2)


def l1_burst(pre, wb, dom1):
    """r7 part u (calm #5b; cases 1-2, general; operator 2026-10-28, after seeing W2's edge misses): the bytes the L1 ->
    L2 merges write after an L0 -> L1 merge of n files. In a calm tree L1 sits at its target between L0 merges: a merge
    of n files leaves L1 b(n) - o_m bytes above it (part w's b(n), less the L1 bytes it read), L1's score passes 1 and
    the one worker pushes that excess down before L0's next merge (score-first serving, static targets), writing x_1
    per byte pushed: the before-stretch's own L1 x / (s + trivial bytes), which carries its overlap and the copies it
    drops, plus the law's change of overlap dom1 at the new K. None without an L0 or L1 merge in the before-stretch."""
    L0, L1 = pre["levels"][0], pre["levels"][1]
    pushed = L1["s"] + L1["trivial_bytes"]
    if wb is None or not pushed or not L0["merges"]:
        return None
    kap = L1["x"] / pushed + dom1
    o_m = L0["o"] / L0["merges"]
    return lambda n: max(0.0, wb(n) - o_m) * kap


def r0_kept(pre):
    """Share of the bytes an L0 -> L1 merge reads from L0 that L1 keeps: (x_0 - o_0) / s_0, pre-change window."""
    L0 = pre["levels"][0]
    return (L0["x"] - L0["o"]) / L0["s"] if L0["s"] else 1.0


def l1_excess(sched, ke, w, r0, t_end):
    """Calm: L1's bytes above its resting point at op t_end on the schedule (sub_stor/stor.py, unchanged)."""
    mu = w.lam * (1.0 + w.om[1])
    exc, last = 0.0, sched.X
    for e, n in sched.merges(ke):
        if e > t_end:
            break
        exc = max(0.0, exc - (e - last) / mu) + r0 * n * w.F0
        last = e
    return max(0.0, exc - (t_end - last) / mu)


def storage(w, p, pr, pre, sched, lo, hi, ops, X, ke_s, ke_t, d8a):
    """sub_stor/stor.py's storage() at its defaults (STOR_BL1=cap, STOR_DRAIN=flat, STOR_PATH=1, parts l0 and l1)."""
    r0 = r0_kept(pre)
    out = dict(r0=r0)
    if not w.busy:
        if sched is None:
            return None
        dn = ((sched.files_at(ke_s, hi) - sched.files_at(ke_s, lo))
              - (sched.files_at(ke_t, hi) - sched.files_at(ke_t, lo))) if "l0" in STOR_PARTS else 0.0
        de = ((l1_excess(sched, ke_s, w, r0, hi) - l1_excess(sched, ke_s, w, r0, lo))
              - (l1_excess(sched, ke_t, w, r0, hi) - l1_excess(sched, ke_t, w, r0, lo))) if "l1" in STOR_PARTS else 0.0
        out.update(dl0_files=dn, dl0_b=r0 * dn * w.F0, dl1_b=de, frac=1.0)
    elif X is None or lo != X:                         # later intervals: both ends at random cycle points
        out.update(dl0_files=0.0, dl0_b=0.0, dl1_b=0.0, frac=0.0)
    else:
        saw = max(0.0, w.size[1] - p["sig"] * w.B)     # L1 at the water level at both K (BK3, BK4' caps)
        dl1 = (p["sig2"] - p["sig"]) * w.B + saw * (p["nb2"] / w.nfire - 1.0)
        frac = 1.0
        if dl1 < 0 and d8a is not None and d8a > 0:     # K raised: L1 drains before the first L0 merge
            frac = min(1.0, (hi - lo) / d8a)
        elif dl1 > 0 and w.q > 0:                       # K lowered: L1 fills with what arrives
            frac = min(1.0, (hi - lo) / (dl1 / w.q))
        dn = pr["2"] if "l0" in STOR_PARTS else 0.0
        dl1 = (dl1 if "l1" in STOR_PARTS else 0.0) * frac
        out.update(dl0_files=dn, dl0_b=r0 * dn * w.F0 * frac, dl1_b=dl1, frac=frac, dl1_full=dl1)
    out["df1"] = -(out["dl0_b"] + out["dl1_b"]) / ops
    return out


def interval_preds_r1(pre, prev, cyc_pre, rs_pre, c03_pre, l1, t, k_from, k_to, sched, lo, hi, ops, F0x, X, d8a,
                      fc_cache, om5=True, stor=True, brk=True):
    """interval_preds with the three fixes, each switchable (all off = interval_preds exactly). Returns (pr, w)."""
    w = m.Win(pre, l1, t, cyc_pre, rs_pre, prev, kin=k_from)
    w.c03 = c03_pre
    if not w.ok:
        return None, w
    if m.R7_ACTIVE:                                # r7: the parts in force (law_r7's block)
        w.r7 = m.R7_ACTIVE
    if "g" in m.R7_ACTIVE:                         # r7 part g: ops between the before- and after-stretch midpoints
        w.g_shift = (lo + hi - pre["lo"] - pre["hi"]) / 2
        w.g_pour = (getattr(sched, "pour", None) or {}).get(str(w.dp))   # and the run's pour ends into the deepest level
    if om5 and w.grow is not None:                 # om5: bytes arriving in L1+ from X to the window's middle
        w.fwd_inflow = sum(w.grow[1:w.dp + 1]) * max(0.0, (lo + hi) / 2 - pre["hi"])
    p = w.predict(k_to, pre)
    pr = {"2": p["dn0"], "3": p["dbatch"], "4": p["dw0"], "5": p["dom"].get(1), "5b": p["dx"].get(1)}
    if "f" in m.R7_ACTIVE and p.get("dom_b1") is not None:   # r7 part f: #5 in the base form (#5b keeps om5's)
        pr["5"] = p["dom_b1"]
    ke_s = ke_t = None
    if sched is not None and not w.busy:          # round 9-11's calm schedule rules over the post interval
        cap_x = math.ceil(l1 * m.MIB / F0x)
        ke_s, ke_t = min(k_to, cap_x), min(k_from, cap_x)
        zc = "zc" in m.R7_ACTIVE                       # r7 part zc: the schedule's sums with no loop over merges
        if m.L0SCHED:
            wbytes = sched.window_bytes_c if zc else sched.window_bytes
            pr["4"] = (wbytes(ke_s, lo, hi, w.F0, l1 * m.MIB)
                       - wbytes(ke_t, lo, hi, w.F0, l1 * m.MIB)) / ops
            wb = merge_written(pre) if "w" in getattr(w, "r7", ()) else None
            if wb is not None:                         # r7 part w: each merge's bytes with its dropped copies
                sum_wb = (lambda ke_: sched.sum_f(ke_, lo, hi, wb)) if zc else (lambda ke_: sum(wb(n) for e, n in sched.merges(ke_) if lo <= e < hi))
                x_s = sum_wb(ke_s) / ops
                if Z_CMP and zc:
                    closed3.tr(cmp="calm", X=X, ke=ke_s, c=sched.sum_f(ke_s, lo, hi, wb), w=sum(wb(n) for e, n in sched.merges(ke_s) if lo <= e < hi), mc=sched.mean_files_c(ke_s, lo, hi), mw=None, ops=ops,
                               pc=len(sched.pieces(ke_s)), nm=len(sched.merges(ke_s)), cntc=sched.count_n(ke_s, lo, hi)[0], cntw=sum(1 for e, n in sched.merges(ke_s) if lo <= e < hi))
                if "s" in w.r7:                        # r7 part s (run's-own measure): against the measured before-stretch
                    pr["4"] = x_s - pre["levels"][0]["x_per_op"]
                else:                                  # the twin-differenced measure: against the old-K schedule
                    pr["4"] = x_s - sum_wb(ke_t) / ops
                if "u" in w.r7:                        # r7 part u (calm #5b): each L0 merge's L1 -> L2 burst
                    bu = l1_burst(pre, wb, p["dom"].get(1, 0.0))
                    if bu is not None:
                        pr["_u5b"] = sum(bu(n) for e, n in sched.merges(ke_s) if lo <= e < hi) / ops \
                            - pre["levels"][1]["x_per_op"]

        def mean_files(ke):
            tot = 0.0
            for mu in sched.flushes:
                if mu >= sched.X and lo < mu < hi:
                    tot += hi - mu
            for e, n in sched.merges(ke):
                if lo < e < hi:
                    tot -= n * (hi - e)
            return sched.files_at(ke, lo) + tot / (hi - lo)

        def mean_batch(ke):
            ns = [n for e, n in sched.merges(ke) if lo <= e < hi]
            return sum(ns) / len(ns) if ns else None
        if m.L0MEAN:
            mfiles = (lambda ke_: sched.mean_files_c(ke_, lo, hi)) if "zc" in m.R7_ACTIVE else mean_files
            if Z_CMP and "zc" in m.R7_ACTIVE:
                closed3.tr(cmp="calm2", X=X, ke=ke_s, c=mfiles(ke_s), w=mean_files(ke_s))
            pr["2"] = mfiles(ke_s) - mfiles(ke_t)
            if "c" in getattr(w, "r7", ()):            # r7 part c: against the run's own measured before-stretch mean
                pr["2"] = mfiles(ke_s) - pre["levels"][0]["files_mean"]
        if m.L0BATCH:
            x, y = mean_batch(ke_s), mean_batch(ke_t)
            if x is not None and y is not None:
                pr["3"] = x - y
    pr["_busy"] = w.busy

    add5b = None                                   # stor: computed from the pre-window prediction, added last
    if stor and p["dx"].get(1) is not None and ("busy" if w.busy else "calm") in STOR_TREES:
        st = storage(w, p, pr, pre, sched, lo, hi, ops, X, ke_s, ke_t, d8a)
        pr["_stor"] = st
        f1 = w.f[1]
        if st is not None and f1:
            xreg = w.xw[1] / f1 + p["dom"].get(1, 0.0)     # the law's x_1 per byte pushed at the new state
            add5b = (-st["dl0_b"] / ops - st["dl1_b"] / ops) * xreg

    if brk and w.busy:                             # brake: cut the window at the forecast crossings (r2: from any K)
        key = ("fc", k_to)
        fc = fc_cache.get(key)
        if fc is None:
            wb = m.Win(pre, l1, t, cyc_pre, rs_pre, prev, kin=k_from)   # no fwd_inflow: sub_brake's own forecast
            wb.c03 = c03_pre
            wb.predict(k_to, pre)
            fc = brake.Forecast(wb, pre, prev, k_to)
            fc_cache[key] = fc
        a, b = lo - X, hi - X
        w._k2t_fc, w._k2t_pieces = fc, None          # k2t: the brake forecast and r3's pieces (no effect on pr)
        crs = brake.crossings(fc)                    # r7 part o: the K' path's crossing only
        fc._fwd = getattr(w, "fwd_inflow", None)
        if not all(c is None or c >= b for c in crs):
            cuts = sorted({a, b} | {c for c in crs if c is not None and a < c < b})
            out = {q: 0.0 for q in ("2", "3", "4", "5", "5b")}
            w._k2t_pieces = []
            for p_, q_ in zip(cuts, cuts[1:]):
                tm, wt = (p_ + q_) / 2, (q_ - p_) / (b - a)
                braked = any(c is not None and tm >= c for c in crs)
                pp = brake.projected_predict(fc, pre, prev, cyc_pre, rs_pre, c03_pre, l1, t, k_to, tm, BRAKE_MODE,
                                             kin=k_from) \
                    if braked else None
                src = pp if pp is not None else pr
                w._k2t_pieces.append((p_, q_, tm, pp is not None))
                for qid in out:
                    if out[qid] is None or src.get(qid) is None:
                        out[qid] = None
                    else:
                        out[qid] += wt * src[qid]
            for qid, v in out.items():
                if v is not None:
                    pr[qid] = v
            pr["_brake"] = True
    if pr.get("_u5b") is not None:                 # r7 part u: the bursts replace the settled #5b and stor's term
        pr["5b"] = pr["_u5b"]
    elif add5b is not None and pr.get("5b") is not None:
        pr["5b"] = pr["5b"] + add5b
    return pr, w


# ---- k1t: case 1's transition, the L0 merges between the first one after a K raise and the settled state ----
# (AFTER SEEING; subagent k1t, 2026-10-27. Target: case 1 (K raised), busy trees. General: RocksDB's own score rule,
# one compaction slot, the 20-file slowdown; no workload trait. Inputs: the run's own pre-change window, its cycles
# record, its L0-merge parts and its per-file snapshot at X, exactly the inputs path_checks and round 17's R18S use.)
# RocksDB (lib/rocksdb):
# - L0's score is the files not being compacted over K, or their bytes over the base level's target, whichever is
#   larger (version_set.cc:3846-3853, 3908-3910, 3953-3956): N / K^, K^ = min(K, B / F0). L1+ score = bytes not being
#   compacted / target (:3961-3971). Levels are served highest score first (:4007-4019; compaction_picker_level.cc:
#   238-271), on one compaction slot (max_background_jobs = 2: db_impl_compaction_flush.cc:3216-3217).
# - An L0 -> L1 merge takes every L0 file present (compaction_picker_level.cc:376-379); L0's count keeps them until it
#   installs (version_set.cc:5156-5167), and writes are delayed while that count is >= 20 (column_family.cc:1032-1036)
#   and all but stop at >= 34 (stop - 2: column_family.cc:1116-1120, the rate x0.6 at every new version, :904-913).
# - After K is raised (K^ -> K^'), the levels above their new caps (the group) are the top scores, so the one worker
#   drains them while L0 fills; L0 merges when N/K^' reaches the group's score (path_checks' #8a/#8b: N_1 files at d
#   ops). N_1 > N* (the law's settled files per merge, nb2), so the group is still above the settled water level
#   N*/K^': every later L0 merge also waits for the group's score, N_k = K^' s_k, while the group keeps draining.
#   Per cycle the worker reads, while the next N_k+1 files flush (the files above 20 are slowed: t_F/lam_s bytes each),
#   the L0 merge N_k b' (b' = F0 + S_1/K^': L1 at the group's score), the batch pushed through the group (F0 bump per
#   file) and the drain that lowers the group from N_k/K^' to N_k+1/K^' (D/K^' per file of score). Over the settled
#   balance: N_k+1 - N* = rho (N_k - N*), rho = (b' + F0 bump + D/K^') / (t_F/lam_s + D/K^'): round 17's R18S, already
#   in the law for #9's settle time (law_k1t.py score_run #9 block), D from the per-file snapshot at X (round 8's walk).
# Window [lo, hi) after X: the merges begin at b_1 = d, b_k+1 = b_k + N_k+1 t_F (a cycle flushes the next merge's
# files) and install x_k t_F later (x_k: files flushed during merge k, round 17's x-rule at the merge's bytes, 0 at
# the near stop). Window mean of files per merge (#3, counted at install, windows_k.py) and op-weighted mean L0 files
# (#2: L0 rises from n_x to N_1 before the first merge; in cycle k it holds N_k + j during the merge, then j, up to
# N_k+1) are compared with the settled cycle at N* (mean N*/2 + x*, the law's x2): the excess is added to r1's
# settled change. Calm trees (round 9's schedule) and case 2 are untouched.
import os  # noqa: E402

K1T_RHO = os.environ.get("K1T_RHO", "r18s")    # r18s: round 17's rho, D averaged over the walk (default) | zero
K1T_X = os.environ.get("K1T_X", "rule")       # rule: x-rule per merge, 0 at the near stop | const: x2 every merge
K1T_Q = os.environ.get("K1T_Q", "2,3").split(",")   # quantities that take the transition term
K1T_WIN = os.environ.get("K1T_WIN", "all")    # all: every window (the tail too) | first: the window holding X only


def relax_r18s(w, p, pre, n1, lam_s, snap_path):
    """Round 17's R18S rho (law score_run's #9 block), from the pre-change window (state at X: its bytes_end) and the
    law's prediction p from it. None where R18S itself gives none (no level above its new cap, n1 <= N* + 1)."""
    nst, s2, kh2 = p.get("nb2"), p.get("sig2"), p.get("kh2")
    if nst is None or s2 is None or not lam_s or lam_s <= 0:
        return None
    Lx = pre["levels"]
    z = [Lx[i]["bytes_end"] for i in range(w.dp + 1)]
    caps2 = [s2 * w.S[i] for i in range(w.dp)]
    grp, carry = [], 0.0
    for i in range(1, w.dp):
        carry = max(0.0, z[i] + carry - caps2[i])
        if carry > 0:
            grp.append(i)
    D, cs, bump = 0.0, 0.0, 0.0
    for i in range(1, w.dp):
        if i in grp:
            cs = w.S[i] + cs
            D += cs * (1 + w.om[i])
        else:
            cs = 0.0
    i = 1
    while i in grp:
        bump += 1 + w.om[i]
        i += 1
    if not grp or n1 <= nst + 1:
        return None
    if m.R18S == "snap" and snap_path is not None and Path(snap_path).exists():
        sx9 = {i: Lx[i]["bytes_end"] / w.S[i] for i in range(1, w.dp)}
        sn9 = drain_order.Snap(Path(snap_path))
        s1_, ss_ = n1 / kh2, nst / kh2
        if s1_ > ss_:
            dR = m.drain_R(sx9, w.S, sn9, ss_) - m.drain_R(sx9, w.S, sn9, s1_)
            if dR > 0:
                D = dR / (s1_ - ss_)
    bpr = w.F0 + w.S[1] / kh2
    rho = (bpr + w.F0 * bump + D / kh2) / (w.tF / lam_s + D / kh2)
    return dict(rho=min(max(rho, 0.0), 0.95), grp=grp, D=D, bump=bump, bpr=bpr, slow_file=w.tF / lam_s)


def merge_x(w, p, n, kh2):
    """Files flushed during an L0 merge of n files: round 17's x-rule (law L0A=3) at the merge's bytes, L1 at the
    group's score n/K^' (R18O); 0 at the near stop (L0 >= 34: writes all but stop). None without the x-rule's inputs."""
    x3, c3 = getattr(w, "_x3", None), getattr(w, "c03", None)
    if x3 is None or not c3 or not w.l0s or w.l0o is None:
        return None
    if K1T_X == "const":
        return x3[2]
    if n >= m.L0_STOP:
        return 0.0
    b1 = w.l0s + w.l0o
    sig = p["sig"]
    bn = n * w.F0 + w.l0o * (n / kh2) / sig
    tau_n = c3["tau"] * bn / b1
    if "d" in getattr(w, "r7", ()):                # r7 part d: delayed merges begin at the cap
        return m.x_rule_d(w, n, tau_n, getattr(w, "_r7_pend2", False))
    if "a" in getattr(w, "r7", ()):                # r7: the merge starts at r(n), not the pre window's r0
        return m.x_rule(w, n, tau_n)
    delayed = c3["r0"] <= 1.15 * m.DELAY_CAP / w.q
    G_ = 1 / 0.64

    def g_(tt):
        zz = tt * c3["r0"] / c3["M"]
        return math.log(1 + (G_ - 1) * zz) / math.log(G_) if delayed else zz
    g0 = g_(c3["tau"])
    return c3["x"] * g_(tau_n) / g0 if g0 > 0 else c3["x"]


def transition(w, p, pre, pc, snap_path, lo_r, hi_r):
    """The K-raise transition's excess over the settled cycle in the window [lo_r, hi_r) (ops after X): #3's mean files
    per merge and #2's op-weighted mean L0 files. None where it does not apply."""
    if not w.busy or pc is None:
        return None
    n1, d = (pc.get("8b") or {}).get("pred"), (pc.get("8a") or {}).get("pred")
    nst, kh2 = p.get("nb2"), p.get("kh2")
    if n1 is None or d is None or nst is None or kh2 is None:
        return None
    rl = relax_r18s(w, p, pre, n1, pc.get("_lam_s"), snap_path) if K1T_RHO == "r18s" else None
    rho = rl["rho"] if rl else 0.0
    tF = w.tF
    x2 = merge_x(w, p, nst, kh2) if K1T_X == "rule" else (w._x3[2] if getattr(w, "_x3", None) else None)
    if x2 is None:
        x2 = w._x3[2] if getattr(w, "_x3", None) else 0.0
    n_x = pre["levels"][0]["files_end"]
    # the K' path's merges from X: (begin op, files, files flushed during it)
    seq, b, nk = [], d, n1
    while b < hi_r and len(seq) < 2000:
        xk = merge_x(w, p, nk, kh2)
        seq.append((b, nk, x2 if xk is None else xk))
        nk = nst + (nk - nst) * rho
        b += nk * tF

    def seg(t0, t1, a0):                           # integral of a0 + (t - t0)/tF over [t0, t1] clipped to the window
        u0, u1 = max(t0, lo_r), min(t1, hi_r)
        if u1 <= u0:
            return 0.0
        return (u1 - u0) * (a0 + ((u0 - t0) + (u1 - t0)) / (2 * tF))
    # before the first merge L0 rises from n_x (exact: the window starts at X with n_x files); in cycle k (b_k to
    # b_k+1 = b_k + N_k+1 t_F) it holds N_k + j during the merge and j after it installs (j: files flushed since b_k),
    # mean N_k+1/2 + x_k N_k/N_k+1; a cycle cut by the window's edge counts at its mean (its phase there is not
    # predictable after several cycles; the settled reference is a cycle mean too)
    integ2 = sum(seg(a, b_, c) for a, b_, c in pre_merge_segs(pc, n_x, d))
    for j, (bk, nk_, xk) in enumerate(seq):
        nn = seq[j + 1][1] if j + 1 < len(seq) else nst + (nk_ - nst) * rho
        nxt = bk + nn * tF
        u0, u1 = max(bk, lo_r), min(nxt, hi_r)
        if u1 > u0:
            integ2 += (u1 - u0) * (nn / 2 + xk * nk_ / nn)
    ex2 = integ2 / (hi_r - lo_r) - (nst / 2 + x2)
    ins = [nk_ - nst for bk, nk_, xk in seq if lo_r <= bk + xk * tF < hi_r]
    ex3 = sum(ins) / len(ins) if ins else None
    return dict(ex2=ex2, ex3=ex3, rho=rho, n1=n1, nst=nst, d=d, x2=x2, nmerge=len(ins),
                relax=(rl is not None), seq=[(round(bk), round(nk_, 2)) for bk, nk_, xk in seq[:8]])


def interval_preds_k1t(pre, prev, cyc_pre, rs_pre, c03_pre, l1, t, k_from, k_to, sched, lo, hi, ops, F0x, X, d8a,
                       fc_cache, om5=True, stor=True, brk=True, k1t=None, pc=None, snap_path=None):
    """interval_preds_r1, plus (k1t on) the K-raise transition's excess for #2 and #3. k1t None or "off": r1's
    result object, unchanged."""
    pr, w = interval_preds_r1(pre, prev, cyc_pre, rs_pre, c03_pre, l1, t, k_from, k_to, sched, lo, hi, ops, F0x, X,
                              d8a, fc_cache, om5=om5, stor=stor, brk=brk)
    if not k1t or k1t == "off" or pr is None or k_to <= k_from or X is None or not w.busy:
        return pr, w
    if K1T_WIN == "first" and lo != X:
        return pr, w
    p = w.predict(k_to, pre)                       # the same prediction r1's window used (w carries its fwd_inflow)
    tr = (transition_closed if "zr" in m.R7_ACTIVE else transition)(w, p, pre, pc, snap_path, lo - X, hi - X)
    if Z_CMP and "zr" in m.R7_ACTIVE:
        tw = transition(w, p, pre, pc, snap_path, lo - X, hi - X)
        closed3.tr(cmp="up", X=X, kf=k_from, kt=k_to, c=None if tr is None else {k: tr[k] for k in ("ex2", "ex3", "nmerge")}, w=None if tw is None else {k: tw[k] for k in ("ex2", "ex3", "nmerge")})
    if tr is None:
        return pr, w
    pr["_k1t"] = tr
    if "3" in K1T_Q and tr["ex3"] is not None and pr.get("3") is not None:
        pr["3"] = pr["3"] + tr["ex3"]
    if "2" in K1T_Q and tr["ex2"] is not None and pr.get("2") is not None:
        pr["2"] = pr["2"] + tr["ex2"]
    return pr, w


def interval_preds_r3(pre, prev, cyc_pre, rs_pre, c03_pre, l1, t, k_from, k_to, sched, lo, hi, ops, F0x, X, d8a,
                      fc_cache, pc, snap_path):
    """r2: r1 (om5, stor, brake from any K) + the case-1 transition (k1t). pc = path_checks' output for this change."""
    return interval_preds_k1t(pre, prev, cyc_pre, rs_pre, c03_pre, l1, t, k_from, k_to, sched, lo, hi, ops, F0x, X,
                              d8a, fc_cache, k1t="on", pc=pc, snap_path=snap_path)


# ---- k2t: case 2's transition, the L0 merges from the first one after K is lowered until the tree has refilled ----
# (subagent k2t, 2026-10-28. Target: case 2 (K lowered), busy trees that are not pending-bound before the change.
# General: RocksDB's score rule, its one compaction slot held through the purge of a merge's inputs, the L0 merge
# reading all of L1, the 20-file slowdown, score-first serving of the deeper levels; no workload trait. Inputs: the
# run's own pre-change window (state at X, overlaps, L0-merge kept share, hold record, w0s worker speeds), path_checks'
# first merge and the brake forecast's K' path, all already built from that window. fill_k2t.py has the derivation.)
# After K is lowered (K^ -> K^' < K^), L0's score N/K^' passes L1's at every pick; each L0 merge reads all of L1 and
# raises it by r0 N F0, and L0 refills toward 20 files while it runs; the next merge comes when L0's score passes the
# top group's, which falls only by what the worker drains in the gap (L1 alone at first; the deeper levels join it as
# its score falls to theirs). The K' path's merges N_k therefore climb from #8b's N_1, cycle by cycle, until the gap
# passes 20 files (slowed flushes give the worker enough to hold L1) or, after the brake forecast's own K' crossing,
# the brake's K' level. #3 = mean files of the merges installing in the window, #2 = the op-weighted L0 count
# (k1t's cycle form: N_k + j during merge k, j after it installs). Each is compared with what r3 used for the K' path
# in the same window (the law's N* = nb2, or the brake's K' path in r3's braked pieces), and the difference is added.
import fill_r7 as fillm  # noqa: E402  (r7: byte-identical copy of r5/fill_r5.py, the per-cycle closed form)

K2T_CAP = os.environ.get("K2T_CAP", "brake")     # brake: cap at the brake forecast's K' path after its crossing | none
K2T_OM = os.environ.get("K2T_OM", "1") == "1"    # 1: overlaps follow the law's overlap rule as the levels move
K2T_Q = os.environ.get("K2T_Q", "2,3,4").split(",")   # r5: the #4 option on (r4: 2,3)
K2T_WIN = os.environ.get("K2T_WIN", "first")    # first: the interval that starts at the change only | all
K2T_GATE = os.environ.get("K2T_GATE", "l0")  # notpend: skip trees pending-bound before the change | l0: only
#                                                   the trees the law calls L0-bound (not pending, median merge >= 20)


def k2t_lams(w, w0s_path):
    """Ops per byte the worker reads with writes fast (L0 < 20) and slowed (L0 >= 20): path_checks' own rule (the
    hold record, overridden by the w0s speeds of the pre window's other merges)."""
    h0 = w.hold
    if h0 is not None and h0["vr"]:
        lam_f, lam_s = h0["rf"] / h0["kpo"] / h0["vr"], h0["rs"] / h0["kpo"] / h0["vr"]
    else:
        lam_f = lam_s = w.lam
    if m.W0SPEED and w0s_path is not None and Path(w0s_path).exists():
        agg = json.loads(Path(w0s_path).read_text())["agg"]
        sr, fr = agg.get("slow_rest"), agg.get("fast_rest")
        if sr and sr["read"] > 0 and sr["sec"] > 0:
            lam_s = sr["ops"] / sr["read"]
        if fr and fr["read"] > 0:
            lam_f = fr["ops"] / fr["read"]
    return lam_f, lam_s


def k2t_gate(w, k_from, k_to):
    if not w.busy or k_to >= k_from:
        return False
    if min(k_to, w.B / w.F0) >= min(k_from, w.B / w.F0) - 1e-9:     # K^ unchanged (both above B / F0)
        return False
    if K2T_GATE in ("notpend", "l0") and m.est(w.size, w.S, w.nfire * w.F0) >= m.P_SOFT:
        return False
    if K2T_GATE == "l0":                           # the law's own L0-bound test (Win.predict, fix 2): median merge
        med = w.hold["med"] if w.hold and w.hold["med"] is not None else w.nfire
        if med < m.N_SLOW - 0.5:
            return False
    return True


def transition2(w, p, pre, pc, w0s_path, fc, X, lo, hi, pieces):
    """The K-lower transition's excess over what r3 used for the K' path in [lo, hi): (ex2, ex3, internals) or None."""
    if pc is None or w.hold is None or not w.l0s or w.l0o is None:
        return None
    n1, d = (pc.get("8b") or {}).get("pred"), (pc.get("8a") or {}).get("pred")
    nst, kh2 = p.get("nb2"), p.get("kh2")
    if n1 is None or d is None or nst is None or kh2 is None:
        return None
    h = w.hold
    lam_f, lam_s = k2t_lams(w, w0s_path)
    dp, S, tF, F0 = w.dp, w.S, w.tF, w.F0
    L = pre["levels"]
    z = [0.0] + [L[i]["bytes_end"] / S[i] for i in range(1, dp + 1)]
    if pc.get("_j"):                               # r7 part j: L1 holds the in-flight merge's output once it installs
        z[1] += pc["_j"]["dl1"] / S[1]
    r0 = r0_kept(pre)
    b1 = w.l0s + w.l0o
    cap = None
    if K2T_CAP == "brake" and fc is not None and fc.t2 is not None:
        t2 = fc.t2
        cap = (lambda t: fc.path(t, "2")[1] if t >= t2 else None)
    lo_r, hi_r = lo - X, hi - X
    x2 = w._x3[2] if getattr(w, "_x3", None) else (getattr(w, "l0a", None) or 0.0)
    xb = x2                                        # r7 part d: the brake's merges are delayed, so begin at the cap
    if "d" in getattr(w, "r7", ()) and getattr(w, "_x3", None) and getattr(w, "c03", None):
        xb = m.x_rule_d(w, nst, w._x3[1], True)
    seq = fillm.fill(F0, tF, S, w.om, dp, kh2, z, h, b1, r0, lam_f, lam_s, n1, d, hi_r, cap=cap,
                     size0=(w.size if K2T_OM else None), x_brake=xb)
    if not seq:
        return None
    hr = dict(h, tau=h["tau_run"]) if h.get("tau_run") else h      # files flushed during the merge proper (no purge)
    am = [xb if br else fillm.a_of(hr, b1, nk, nk * F0 + uk * S[1]) for bk, nk, ak, uk, br in seq]
    seq = [(bk, nk, ak, uk) for bk, nk, ak, uk, br in seq]
    n_x = L[0]["files_end"]

    def ref3(t):                                   # r3's K' files per merge at op X + t (its piece's own value)
        if not pieces:
            return nst
        pc_ = next((x for x in pieces if x[0] <= t < x[1]), pieces[-1] if t >= pieces[-1][1] else pieces[0])
        p_, q_, tm, used = pc_
        if used and any(c is not None and tm >= c for c in brake.crossings(fc)):
            return fc.path(tm, "2")[1]
        return nst

    def seg(t0, t1, a0):                           # integral of a0 + (t - t0)/tF over [t0, t1] within the window
        u0, u1 = max(t0, lo_r), min(t1, hi_r)
        if u1 <= u0:
            return 0.0
        return (u1 - u0) * (a0 + ((u0 - t0) + (u1 - t0)) / (2 * tF))
    integ2 = sum(seg(a, b_, c) for a, b_, c in pre_merge_segs(pc, n_x, d))
    for j, (bk, nk, ak, uk) in enumerate(seq):
        nn = seq[j + 1][1] if j + 1 < len(seq) else nk
        nxt = bk + nn * tF
        u0, u1 = max(bk, lo_r), min(nxt, hi_r)
        if u1 > u0:
            integ2 += (u1 - u0) * (nn / 2 + am[j] * nk / nn)
    # r3's K' L0 level over the window, piece by piece (law: N*/2 + x2; its braked pieces: the brake's K' path)
    ref2 = 0.0
    if pieces:
        for p_, q_, tm, used in pieces:
            ref2 += (q_ - p_) * (ref3(tm) / 2 + x2)
    else:
        ref2 = (hi_r - lo_r) * (nst / 2 + x2)
    ex2 = (integ2 - ref2) / (hi_r - lo_r)
    ins = [(nk, bk + am[j] * tF) for j, (bk, nk, ak, uk) in enumerate(seq) if lo_r <= bk + am[j] * tF < hi_r]
    ex3 = sum(nk - ref3(ti) for nk, ti in ins) / len(ins) if ins else None
    # #4 (option): the L1 bytes the window's L0 merges rewrite, sum of u_k S_1 over its installs, per op, against the
    # law's settled K' rate q B / (K^' F0) (the kept L0 bytes are the same flushed bytes at K and K': they cancel)
    l1w = sum(uk * S[1] for j, (bk, nk, ak, uk) in enumerate(seq) if lo_r <= bk + am[j] * tF < hi_r)
    ex4 = l1w / (hi_r - lo_r) - w.q * w.B / (kh2 * F0)
    return dict(ex2=ex2, ex3=ex3, ex4=ex4, n1=n1, d=d, nst=nst, kh2=kh2, x2=x2, nmerge=len(ins), lam_f=lam_f, lam_s=lam_s,
                r0=r0, seq=[(round(bk), round(nk, 2), round(ak, 2)) for bk, nk, ak, uk in seq[:16]])


def interval_preds_k2t(pre, prev, cyc_pre, rs_pre, c03_pre, l1, t, k_from, k_to, sched, lo, hi, ops, F0x, X, d8a,
                       fc_cache, pc, snap_path, w0s_path=None, k2t=None, q=None):
    """interval_preds_r3, plus (k2t on) case 2's transition excess for #2 and #3. k2t None or "off": r3's result
    object, unchanged. q: the quantities that take the excess (None: K2T_Q)."""
    qs = K2T_Q if q is None else q
    pr, w = interval_preds_r3(pre, prev, cyc_pre, rs_pre, c03_pre, l1, t, k_from, k_to, sched, lo, hi, ops, F0x, X,
                              d8a, fc_cache, pc, snap_path)
    if not k2t or k2t == "off" or pr is None or X is None or not k2t_gate(w, k_from, k_to):
        return pr, w
    if K2T_WIN == "first" and lo != X:
        return pr, w
    p = w.predict(k_to, pre)                       # the same prediction r3's window used (w carries its fwd_inflow)
    fc = getattr(w, "_k2t_fc", None)
    tr = (transition2_closed if "z" in m.R7_ACTIVE else transition2)(w, p, pre, pc, w0s_path, fc, X, lo, hi, getattr(w, "_k2t_pieces", None))
    if Z_CMP and "z" in m.R7_ACTIVE:
        tw = transition2(w, p, pre, pc, w0s_path, fc, X, lo, hi, getattr(w, "_k2t_pieces", None))
        closed3.tr(cmp="down", X=X, kf=k_from, kt=k_to, c=None if tr is None else {k: tr[k] for k in ("ex2", "ex3", "ex4", "nmerge")}, w=None if tw is None else {k: tw[k] for k in ("ex2", "ex3", "ex4", "nmerge")}, q=w.q, B=w.B, F0=w.F0, kh2=p.get("kh2"))
    if tr is None:
        return pr, w
    pr["_k2t"] = tr
    if "3" in qs and tr["ex3"] is not None and pr.get("3") is not None:
        pr["3"] = pr["3"] + tr["ex3"]
    if "2" in qs and tr["ex2"] is not None and pr.get("2") is not None:
        pr["2"] = pr["2"] + tr["ex2"]
    if "4" in qs and tr["ex4"] is not None and pr.get("4") is not None:
        pr["4"] = pr["4"] + tr["ex4"]
    return pr, w


def interval_preds_r4(pre, prev, cyc_pre, rs_pre, c03_pre, l1, t, k_from, k_to, sched, lo, hi, ops, F0x, X, d8a,
                      fc_cache, pc, snap_path, w0s_path=None, r4="on"):
    """r4: r3 (om5, stor, brake from any K, k1t's case-1 transition, rolling calm L0 schedule) + k2t's case-2
    transition (#2, #3). w0s_path = the pre-change window's worker speeds; r4="off" returns r3's result unchanged."""
    return interval_preds_k2t(pre, prev, cyc_pre, rs_pre, c03_pre, l1, t, k_from, k_to, sched, lo, hi, ops, F0x, X,
                              d8a, fc_cache, pc, snap_path, w0s_path=w0s_path, k2t=("on" if r4 == "on" else "off"))


def interval_preds_r5(pre, prev, cyc_pre, rs_pre, c03_pre, l1, t, k_from, k_to, sched, lo, hi, ops, F0x, X, d8a,
                      fc_cache, pc, snap_path, w0s_path=None, r5="on"):
    """r5: r4 + k2t's #4 option (the transition excess on #4 too). r5="off" returns r4's result unchanged (q = 2,3)."""
    return interval_preds_k2t(pre, prev, cyc_pre, rs_pre, c03_pre, l1, t, k_from, k_to, sched, lo, hi, ops, F0x, X,
                              d8a, fc_cache, pc, snap_path, w0s_path=w0s_path, k2t="on",
                              q=(K2T_Q if r5 == "on" else ["2", "3"]))


R7_PARTS = tuple(x for x in os.environ.get("R7_PARTS", "a,b,c").split(",") if x)


def interval_preds_r7(pre, prev, cyc_pre, rs_pre, c03_pre, l1, t, k_from, k_to, sched, lo, hi, ops, F0x, X, d8a,
                      fc_cache, pc, snap_path, w0s_path=None, r7="on"):
    """r7: r5 with law_r7's start rate in the x-rule (settled and per merge). r7="off" returns r5's result unchanged
    (the window carries no Win.r7)."""
    old = m.R7_ACTIVE
    m.R7_ACTIVE = R7_PARTS if r7 == "on" else ()
    try:
        pr, w = interval_preds_r5(pre, prev, cyc_pre, rs_pre, c03_pre, l1, t, k_from, k_to, sched, lo, hi, ops, F0x, X,
                                  d8a, fc_cache, pc, snap_path, w0s_path=w0s_path, r5="on")
        if "r" in m.R7_ACTIVE and pr is not None and pr.get("4") is not None:   # r7 part r (run's-own measure only)
            rr = recent_q(sched, pre, X)
            if rr is not None:
                x_a = pre["levels"][0]["x_per_op"]
                pr["_qr"] = rr
                pr["4"] = (x_a + pr["4"]) * rr - x_a
                if UQR and pr.get("_u5b") is not None:   # part u's bursts follow the L0 merges, so the same rate
                    x_1 = pre["levels"][1]["x_per_op"]
                    pr["5b"] = (x_1 + pr["5b"]) * rr - x_1
                elif R5B and pr.get("5b") is not None:   # busy #5b at the same rate (L1 -> L2 bytes per op follow q)
                    x_1 = pre["levels"][1]["x_per_op"]
                    pr["5b"] = (x_1 + pr["5b"]) * rr - x_1
        if "y" in m.R7_ACTIVE and pr is not None and pr.get("3") is not None and X is not None:
            part_y(pr, w, pre, cyc_pre, pc, sched, X, lo, hi, k_from, k_to, F0x, l1, snap_path, w0s_path, recent_q(sched, pre, X))
        return pr, w
    finally:
        m.R7_ACTIVE = old


UQR = os.environ.get("R7_UQR", "1") == "1"      # part u: part r's write-rate ratio on the bursts too (adopted
#                                                 form, 2026-10-28 13:35 UTC; u_* logs: R7_UQR=0)
R5B = os.environ.get("R7_R5B", "1") == "1"      # part r on busy #5b too (adopted 2026-10-28 13:41 UTC; r5b_* logs)
QR_OPS = int(os.environ.get("R7_QR", "500000"))
QR_MODE = os.environ.get("R7_QRMODE", "cp")    # cp (default): the rate since the last step (BIC) | span: last QR_OPS
QR_TIME = os.environ.get("R7_QRTIME", "begin")  # begin (default): memtable switches time the intervals | end: flush ends


def step_rate_ratio(sched, pre, X):
    """r7 part r, cp mode (owner 2026-10-28: "Try a principled version"; no tuned span). The last stretch's flush
    intervals give a rate per interval. QR_TIME=begin (default): the ops between successive memtable switches (flush
    begins), each with the bytes of the flush that switch starts; a switch comes exactly when the memtable is full, so
    the spacing is the workload's write rate alone (a flush's end also moves with how long the flush takes, which
    swings with the write slowdowns in a busy tree). QR_TIME=end: flush ends. A step
    in the workload's write rate is a shift in their mean: the most significant split (each side >= 2 intervals) is
    kept when BIC prefers two means, n ln(SSE_0 / SSE_1) > 2 ln n (two more parameters: the split and the second mean),
    and the search repeats on the later part, so the rate used is the one since the last step. Rates are timed on the
    intervals, sum of bytes / sum of ops. Returns rate since the last step / rate over the stretch (1.0 with no step)."""
    if QR_TIME == "begin" and getattr(sched, "fb_raw", None) is not None:
        ends, begs = sorted(sched.fl_raw), sorted(a for a, _ in sched.fb_raw)
        if len(ends) == len(begs):                 # the k-th switch starts the k-th flush (both in op order)
            allf = [(b, e[1]) for b, e in zip(begs, ends)]
        else:                                      # unpaired: equal bytes per memtable (the rate is in the count)
            allf = [(b, 1.0) for b in begs]
    else:
        allf = sorted(sched.fl_raw)
    fs = [(a, by) for a, by in allf if pre["lo"] <= a < X]
    iv = [(fs[k][0] - fs[k - 1][0], fs[k][1]) for k in range(1, len(fs)) if fs[k][0] > fs[k - 1][0]]
    if len(iv) < 2:
        return None

    def sse(seg):
        rs = [b / d for d, b in seg]
        mu = sum(rs) / len(rs)
        return sum((r - mu) ** 2 for r in rs)
    seg = iv
    while len(seg) >= 4:
        n, s0 = len(seg), sse(seg)
        if s0 <= 0:
            break
        best = min(range(2, n - 1), key=lambda k: sse(seg[:k]) + sse(seg[k:]))
        s1 = sse(seg[:best]) + sse(seg[best:])
        if not (s1 <= 0 or n * math.log(s0 / s1) > 2 * math.log(n)):
            break
        seg = seg[best:]
    rate = lambda s: sum(b for d, b in s) / sum(d for d, b in s)
    return rate(seg) / rate(iv)


def recent_q(sched, pre, X):
    """r7 part r (#4; cases 1-2, calm and busy; operator 2026-10-28, after seeing): the write rate the law forecasts for
    the next stretch is the run's own most recent one, the bytes flushed per op over the last QR_OPS ops before the
    change (within the last stretch), not the stretch's mean. An L0 -> L1 merge's bytes per op scale with q at a given
    K (BK3: w_0 = q S_1 / (K^ F_0) + f_1, f_1 = q less the copies dropped), so the after-state's x is the law's at the
    stretch's q, times q_recent / q. D-36: only the run's own ops before the change. Under the twin-differenced measure
    (D-33) a change in the workload's write rate is in both runs at the same ops and cancels, so it is not used there.
    Both rates are timed on the flushes themselves, the bytes of the flushes after the first in a span over the ops from
    the first flush's end to the last's, so a steady rate gives a ratio of exactly 1 (a count of whole flushes in a
    fixed span moves by one flush in 2-5). Returns q_recent / q_stretch, or None with fewer than 2 flushes in a span."""
    if sched is None or X is None or getattr(sched, "fl_raw", None) is None:
        return None
    if QR_MODE == "cp":
        return step_rate_ratio(sched, pre, X)

    def rate(lo, hi):
        fs = sorted((a, by) for a, by in sched.fl_raw if lo <= a < hi)
        if len(fs) < 2 or fs[-1][0] <= fs[0][0]:
            return None
        return sum(by for a, by in fs[1:]) / (fs[-1][0] - fs[0][0])
    q_r, q_s = rate(max(pre["lo"], X - QR_OPS), X), rate(pre["lo"], X)
    return q_r / q_s if q_r and q_s else None
# ---- r7 part y (adopted 2026-10-31, owner; multi only): #3 in closed form, closed3.py. Cases 1 and 2. Calm trees: K_e files per merge; busy trees: K lowered, the two-number form; K raised, the cycle balance. ----
import closed3  # noqa: E402
import rate_step as rate_v  # noqa: E402  (the rate since a write-rate step)
import stepq  # noqa: E402
Y_OM = os.environ.get("Y_OM", "c")         # c: a level that pushed nothing: overlap = c x (below / own), c pooled from the last stretch | zero
Y_CHI = os.environ.get("Y_CHI", "1") == "1"     # 1: L0 merges cost their own seconds per byte | 0: as a rest byte
Y_STEP = os.environ.get("Y_STEP", "1") == "1"   # 1: rates since a write-rate step (part r's test) | 0: the last stretch's
Y_FORM = os.environ.get("Y_FORM", "pair_down")        # one: closed3.mean_files | pair: mean_files_pair below 20 files | pair_down: only when K is lowered


def y_overlaps(w, z, n1, kh2, snap_path):
    om = list(w.om)
    if Y_OM == "zero":
        return om
    sf = so = sr = 0.0
    for i in range(1, w.dp):
        if w.f[i] > 0 and w.rd[i] > 0 and w.size[i] > 0:
            sf += w.f[i]
            so += w.om[i] * w.f[i]
            sr += w.f[i] * w.size[i + 1] / w.size[i]
    c = so / sr if sr > 0 else 0.0
    s = n1 / kh2
    for i in range(1, w.dp):
        if not w.om[i]:
            own = max(z[i], s) * w.S[i] if i == 1 else max(min(z[i], s), 1e-9) * w.S[i]
            below = z[i + 1] * w.S[i + 1] if i + 1 < w.dp else w.size[i + 1]
            om[i] = c * below / own if own > 0 else 0.0
    return om


def part_y(pr, w, pre, cyc, pc, sched, X, lo, hi, k_from, k_to, F0x, l1, snap_path, w0s_path, qr):
    nf = pre["l0l1"]["files_in_mean"]
    if nf is None:
        return
    j_ = (pc or {}).get("_j")
    span = hi - X
    jr = j_ if (j_ and j_.get("nin") is not None and j_["rem"] < span) else None
    if not w.busy:                                 # calm: every merge takes K_e files, the first takes what L0 holds once the slot frees
        if not w.ok:
            return
        ke = min(k_to, math.ceil(l1 * m.MIB / F0x))
        n_x = pre["levels"][0]["files_end"] - (jr["nin"] if jr else 0)
        d0 = (jr["rem"] if jr else 0.0)
        if n_x >= ke:
            N1, d = n_x + d0 / w.tF, d0
        else:
            N1, d = ke, max(d0, (ke - n_x) * w.tF)
        A = (span - d) / w.tF
        tot, cnt = 0.0, 0.0
        if A >= 0:
            k = 1 + max(0.0, A) / ke
            k = math.floor(k)
            tot, cnt = N1 + (k - 1) * ke, k
        if jr:
            tot, cnt = tot + jr["nin"], cnt + 1
        if cnt:
            pr["3"] = tot / cnt - nf
            pr["_y"] = dict(mean=tot / cnt)
        return
    if pc is None:
        return
    n1, d = (pc.get("8b") or {}).get("pred"), (pc.get("8a") or {}).get("pred")
    p = w.predict(k_to, pre)
    kh2, nst = p.get("kh2"), p.get("nb2")
    if n1 is None or d is None or kh2 is None:
        return
    lam_f, lam_s = k2t_lams(w, w0s_path)
    tF, F0, S, dp = w.tF, w.F0, w.S, w.dp
    L = pre["levels"]
    z = [0.0] + [L[i]["bytes_end"] / S[i] for i in range(1, dp + 1)]
    if j_:
        z[1] += j_["dl1"] / S[1]
    r0 = r0_kept(pre)
    B_f, B_s = tF / lam_f, tF / lam_s
    rf_y, rs_y = (w.hold["rf"], w.hold["rs"]) if w.hold else (None, None)
    if Y_STEP and qr is not None and abs(qr - 1) >= 0.05 and sched is not None:
        rr = stepq.find(sched, pre, X)
        ftp = Path(__file__).resolve().parent / "ft" / f"{sched.ev_path.stem}.json"
        if rr is not None and ftp.exists():
            ft = json.loads(ftp.read_text())["fb"]
            c03 = json.loads((sched.ev_path.parent.parent / "c03" / f"{sched.ev_path.stem}.json").read_text())["merges"]
            fe = sorted(a for a, _ in sched.fl_raw if a < X)
            l0m = [(q_["end_op"], q_["n"]) for q_ in c03 if q_["end_op"] < X]
            done = [q_ for q_ in c03 if q_["end_op"] < X]
            last_l0 = ((done[-1]["end_t"] - done[-1]["t"]) / 1e6, done[-1]["s"] + done[-1]["o"]) if done else None
            ii = rate_v.inputs(w, cyc, last_l0)
            rf = rate_v.fast_rate(ft, fe, l0m, rr["xs"], X, L[0]["files_end"])
            if ii is not None and ii[3] and rf:
                M, rs, tau, v, _ = ii
                tF = F0 / rr["q_since"]
                B_f, B_s = v * M / rf, v * M / rs
                rf_y, rs_y = rf, rs
    om = y_overlaps(w, z, n1, kh2, snap_path)
    chi = 1.0
    if Y_CHI and w.hold is not None and w.l0s and w.l0o is not None and w.hold.get("vr"):
        chi = w.hold["tau"] * w.hold["vr"] / (w.l0s + w.l0o)
    a1 = 0.0
    h = w.hold
    if h is not None and w.l0s and w.l0o is not None:
        hr = dict(h, tau=h["tau_run"]) if h.get("tau_run") else h
        u = min(z[1], n1 / kh2)
        a1 = fillm.a_of(hr, w.l0s + w.l0o, n1, n1 * F0 + u * S[1])
    fc = getattr(w, "_k2t_fc", None)

    def totals(sp):
        """Files and merges from the first merge to op X + sp, in closed form."""
        A = (sp - d) / tF - a1
        tot, cnt = 0.0, 0.0
        # the brake forecast's K' path past its crossing: that level for the rest of the stretch (closed: flushes / level)
        if fc is not None and getattr(fc, "t2", None) is not None and d < fc.t2 < sp:
            c = fc.path((fc.t2 + sp) / 2, "2")[1]
            span2 = sp - fc.t2
            A = (fc.t2 - d) / tF - a1
            if c and c > 0:
                tot, cnt = span2 / tF, span2 / (c * tF)
        elif fc is not None and getattr(fc, "t2", None) is not None and fc.t2 <= d:
            c = fc.path((d + sp) / 2, "2")[1]
            if c and c > 0:
                return n1 + (sp - d) / tF, 1 + (sp - d) / (c * tF)
        if A >= 0 or A + a1 >= 0:
            use_pair = Y_FORM == "pair" or (Y_FORM == "pair_down" and k_to < k_from)
            closed3.tr(run=sched.ev_path.stem if sched is not None else None, X=X, kf=k_from, kt=k_to, n1=n1, d=round(d), A=round(A, 1), a1=round(a1, 2), chi=round(chi, 3), r0=round(r0, 3), tF=round(tF))
            if use_pair and h is not None and w.l0s and w.l0o is not None:
                wb = h["tau"] / (w.l0s + w.l0o)
                mean, k, pcs = closed3.mean_files_pair(F0, tF, S, om, fillm.om_at, fillm.drain_bytes, dp, kh2, z, r0, B_f, B_s, n1, A, nst, w.size, chi, wb, h["M"] / rf_y, h["M"] / rs_y)
            else:
                mean, k, pcs = closed3.mean_files(F0, tF, S, om, fillm.om_at, fillm.drain_bytes, dp, kh2, z, r0, B_f, B_s, n1, A, nst, w.size, chi)
            tot, cnt = tot + mean * k, cnt + k
        return tot, cnt
    tot, cnt = totals(span)
    if jr:
        tot, cnt = tot + jr["nin"], cnt + 1
    if lo - X > d:                                 # a stretch that starts later than the change: only its own merges
        t0, c0 = totals(lo - X)
        if jr and jr["rem"] < lo - X:
            t0, c0 = t0 + jr["nin"], c0 + 1
        tot, cnt = tot - t0, cnt - c0
    if cnt > 0:
        pr["3"] = tot / cnt - nf
        pr["_y"] = dict(mean=tot / cnt)


# ---- r7 part z (trial, 2026-10-31; owner: "replacing the per-merge calculation in #2 and #4 with the two number formula"): the K-lower transition from closed3's sums ----
Z_CMP = os.environ.get("Z_CMP", "0") == "1"     # 1: also run the per-merge forms, write both to the trace (diagnosis only)
Z_BR3 = os.environ.get("Z_BR3", "1") == "1"     # 1: the brake's level at three points of the stretch | 0: at its middle
Z_OM = os.environ.get("Z_OM", "w")              # w: the last stretch's overlaps, as transition2 | y: part y's (a level that pushed nothing)


def transition2_closed(w, p, pre, pc, w0s_path, fc, X, lo, hi, pieces):
    """transition2's (ex2, ex3, ex4) with no loop over merges or flushes: closed3.mean_files_pair's sums of the (files, L1 score) path. Cycle j, from merge j's begin to merge
    j+1's, lasts N_j+1 t_F and holds a mean L0 of N_j+1 / 2 + a_j N_j / N_j+1, so its integral is t_F (N_j+1^2 / 2 + a_j N_j); #4 sums u_j S_1 over the merges that install in the
    stretch. Past the brake forecast's K' crossing: one level for the rest of the stretch."""
    if pc is None or w.hold is None or not w.l0s or w.l0o is None:
        return None
    n1, d = (pc.get("8b") or {}).get("pred"), (pc.get("8a") or {}).get("pred")
    nst, kh2 = p.get("nb2"), p.get("kh2")
    if n1 is None or d is None or nst is None or kh2 is None:
        return None
    h = w.hold
    lam_f, lam_s = k2t_lams(w, w0s_path)
    dp, S, tF, F0 = w.dp, w.S, w.tF, w.F0
    L = pre["levels"]
    z = [0.0] + [L[i]["bytes_end"] / S[i] for i in range(1, dp + 1)]
    if pc.get("_j"):
        z[1] += pc["_j"]["dl1"] / S[1]
    r0 = r0_kept(pre)
    b1 = w.l0s + w.l0o
    lo_r, hi_r = lo - X, hi - X
    if d >= hi_r:
        return None
    x2 = w._x3[2] if getattr(w, "_x3", None) else (getattr(w, "l0a", None) or 0.0)
    xb = x2
    if "d" in getattr(w, "r7", ()) and getattr(w, "_x3", None) and getattr(w, "c03", None):
        xb = m.x_rule_d(w, nst, w._x3[1], True)
    om = w.om if Z_OM == "w" else y_overlaps(w, z, n1, kh2, None)
    t2 = fc.t2 if (K2T_CAP == "brake" and fc is not None and fc.t2 is not None and fc.t2 < hi_r) else None
    end_pair = hi_r if t2 is None else t2
    n_x = L[0]["files_end"]

    def seg(t0, t1, a0):
        u0, u1 = max(t0, lo_r), min(t1, hi_r)
        if u1 <= u0:
            return 0.0
        return (u1 - u0) * (a0 + ((u0 - t0) + (u1 - t0)) / (2 * tF))
    integ2 = sum(seg(a, b_, c) for a, b_, c in pre_merge_segs(pc, n_x, d))
    nmerge, sn, su = 0.0, 0.0, 0.0
    u_end = min(z[1], n1 / kh2)
    if d < end_pair:
        acc = {}
        wb, wbr = h["tau"] / b1, (h["tau_run"] if h.get("tau_run") else h["tau"]) / b1
        closed3.mean_files_pair(F0, tF, S, om, fillm.om_at, fillm.drain_bytes, dp, kh2, z, r0, tF / lam_f, tF / lam_s, n1, (end_pair - d) / tF, nst, (w.size if K2T_OM else None), 1.0,
                                wb, h["M"] / h["rf"], h["M"] / h["rs"], acc=acc, wbr=wbr)
        left, n_next = acc["end"]
        N_l, u_l, a_l = acc["last"]
        last_t = a_l * N_l if left > 0 else 0.0
        integ2 += tF * ((acc["sNN"] - acc["n1"] ** 2) / 2 + acc["sNa"] - last_t) + tF * left * (n_next / 2 + (a_l * N_l / n_next if left > 0 else 0.0))
        nmerge, sn, su = acc["m"], acc["sN"], acc["su"]
        if left > 0 and a_l > left:             # the last merge installs after the stretch ends
            nmerge, sn, su = nmerge - 1, sn - N_l, su - u_l
        elif left <= 0:                         # the rest at one level: a's share of a merge installs later
            f = min(a_l / max(N_l, 1.0), 1.0)
            nmerge, sn, su = nmerge - f, sn - f * N_l, su - f * u_l
        u_end = u_l
    if t2 is not None:                          # the brake's K' path: files per merge c, x_brake flushed during each
        t_b = max(t2, d)
        cs = [fc.path(t_, "2")[1] for t_ in (t_b, (t_b + hi_r) / 2, hi_r)] if Z_BR3 else [fc.path((t_b + hi_r) / 2, "2")[1]] * 3
        if all(c_ and c_ > 0 for c_ in cs):     # the level moves slowly along the brake's path: Simpson's rule over the stretch
            c = (cs[0] + 4 * cs[1] + cs[2]) / 6
            integ2 += (hi_r - t_b) * (c / 2 + xb)
            k_ = (hi_r - t_b) / tF * (1 / cs[0] + 4 / cs[1] + 1 / cs[2]) / 6
            nmerge, sn, su = nmerge + k_, sn + (hi_r - t_b) / tF, su + (hi_r - t_b) / tF * (min(u_end, cs[0] / kh2) / cs[0] + 4 * min(u_end, cs[1] / kh2) / cs[1] + min(u_end, cs[2] / kh2) / cs[2]) / 6

    def ref3(t):
        if not pieces:
            return nst
        pc_ = next((x for x in pieces if x[0] <= t < x[1]), pieces[-1] if t >= pieces[-1][1] else pieces[0])
        p_, q_, tm, used = pc_
        if used and any(c_ is not None and tm >= c_ for c_ in brake.crossings(fc)):
            return fc.path(tm, "2")[1]
        return nst
    ref2 = 0.0
    if pieces:
        for p_, q_, tm, used in pieces:
            ref2 += (q_ - p_) * (ref3(tm) / 2 + x2)
    else:
        ref2 = (hi_r - lo_r) * (nst / 2 + x2)
    ex2 = (integ2 - ref2) / (hi_r - lo_r)
    ref_mean = (sum((q_ - p_) * ref3(tm) for p_, q_, tm, used in pieces) / (hi_r - lo_r)) if pieces else nst
    ex3 = (sn - nmerge * ref_mean) / nmerge if nmerge > 0 else None
    ex4 = su * S[1] / (hi_r - lo_r) - w.q * w.B / (kh2 * F0)
    return dict(ex2=ex2, ex3=ex3, ex4=ex4, n1=n1, d=d, nst=nst, kh2=kh2, x2=x2, nmerge=nmerge, lam_f=lam_f, lam_s=lam_s, r0=r0, seq=None)


# ---- r7 part zr (trial, 2026-10-31): the K-raise transition with no loop over merges: N_k - N* = rho^(k-1) (N_1 - N*), so all its sums are geometric ----
def transition_closed(w, p, pre, pc, snap_path, lo_r, hi_r):
    if not w.busy or pc is None:
        return None
    n1, d = (pc.get("8b") or {}).get("pred"), (pc.get("8a") or {}).get("pred")
    nst, kh2 = p.get("nb2"), p.get("kh2")
    if n1 is None or d is None or nst is None or kh2 is None:
        return None
    rl = relax_r18s(w, p, pre, n1, pc.get("_lam_s"), snap_path) if K1T_RHO == "r18s" else None
    rho = rl["rho"] if rl else 0.0
    tF = w.tF
    x2 = merge_x(w, p, nst, kh2) if K1T_X == "rule" else (w._x3[2] if getattr(w, "_x3", None) else None)
    if x2 is None:
        x2 = w._x3[2] if getattr(w, "_x3", None) else 0.0
    n_x = pre["levels"][0]["files_end"]
    a_ = n1 - nst

    def xx(n):
        v = merge_x(w, p, n, kh2)
        return x2 if v is None else v
    x1, n2 = xx(n1), nst + a_ * rho
    x_2 = xx(n2)
    xs = xx(nst)
    slope = (x_2 - xs) / (n2 - nst) if abs(n2 - nst) > 1e-9 else 0.0   # the x-rule is near linear in N between N_2 and N*; the first merge has its own

    def N_at(k):
        return nst + a_ * rho ** (k - 1)

    def x_at(k):
        return x1 if k == 1 else xs + slope * (N_at(k) - nst)

    def geo(l, k):
        return float(k) if abs(1 - l) < 1e-12 else (1 - l ** k) / (1 - l)

    def s1(k):                                  # sum_{i=1..k} N_i
        return k * nst + a_ * geo(rho, k)

    def s2(k):                                  # sum_{i=1..k} N_i^2
        return k * nst * nst + 2 * nst * a_ * geo(rho, k) + a_ * a_ * geo(rho * rho, k)

    def begun(t):                               # merges begun before X + t: the largest J with d + t_F (s1(J) - N_1) < t
        if t <= d:
            return 0
        lo_k, hi_k = 1, 2
        while d + tF * (s1(hi_k) - n1) < t and hi_k < 1 << 22:
            lo_k, hi_k = hi_k, hi_k * 2
        while hi_k - lo_k > 1:
            mid = (lo_k + hi_k) // 2
            if d + tF * (s1(mid) - n1) < t:
                lo_k = mid
            else:
                hi_k = mid
        return lo_k

    def integ(t):                               # the integral of L0's files from the first merge's begin to X + t
        J = begun(t)
        if J == 0:
            return 0.0
        b_J = d + tF * (s1(J) - n1)
        nn = N_at(J + 1)
        whole = tF * ((s2(J) - n1 * n1) / 2)
        if J >= 2:                              # sum_{k=1..J-1} x_k N_k
            sN, sNN = s1(J - 1) - n1, s2(J - 1) - n1 * n1
            whole += tF * (x1 * n1 + (xs - slope * nst) * sN + slope * sNN)
        return whole + (t - b_J) * (nn / 2 + x_at(J) * N_at(J) / nn)

    def installed(t):                           # merges that install before X + t
        J = begun(t)
        if J and d + tF * (s1(J) - n1) + x_at(J) * tF >= t:
            J -= 1
        return J

    def seg(t0, t1, a0):
        u0, u1 = max(t0, lo_r), min(t1, hi_r)
        if u1 <= u0:
            return 0.0
        return (u1 - u0) * (a0 + ((u0 - t0) + (u1 - t0)) / (2 * tF))
    integ2 = sum(seg(a, b_, c) for a, b_, c in pre_merge_segs(pc, n_x, d)) + integ(hi_r) - integ(lo_r)
    ex2 = integ2 / (hi_r - lo_r) - (nst / 2 + x2)
    j0, j1 = installed(lo_r), installed(hi_r)
    ex3 = (s1(j1) - s1(j0)) / (j1 - j0) - nst if j1 > j0 else None
    return dict(ex2=ex2, ex3=ex3, rho=rho, n1=n1, nst=nst, d=d, x2=x2, nmerge=j1 - j0, relax=(rl is not None), seq=None)
