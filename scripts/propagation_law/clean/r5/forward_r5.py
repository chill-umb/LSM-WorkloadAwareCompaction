#!/usr/bin/env python3
"""[clean/r5, 2026-10-28 (owner: "First add k2t's #4 term as r5"): r4 + k2t's #4 option (K2T_Q=2,3,4: the L1
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
sys.path.insert(0, "/home/cc/node_ops/diag/kcase/clean/r5")
import law_r5 as m  # noqa: E402  (r5: byte-identical copy of r4/law_r4.py)
import brake_r5 as brake  # noqa: E402  (r5: byte-identical copy of r4/brake_r4.py)
import drain_order  # noqa: E402
import l0_schedule_r5 as l0_schedule  # noqa: E402  (r5: byte-identical copy of r4/l0_schedule_r4.py)

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


def path_checks(xs, cyc0, run_rs, l1, t, k_from, k2, w0s_path, snap_path, measured_first, peak):
    """score_run's #8 (OWN_X) and #7 (ROLL) blocks, from the pre-change window xs (its cycles record cyc0)."""
    out = {}
    w0 = m.Win(xs, l1, t, cyc0, run_rs, kin=k_from)
    x0 = xs
    mf = measured_first
    if w0.ok and mf is not None:
        n_x = xs["levels"][0]["files_end"]
        kh2 = min(k2, w0.B / w0.F0)
        L = xs["levels"]
        sig_x = [L[i]["bytes_end"] / w0.S[i] for i in range(1, w0.dp)]
        smax = max([1.0] + sig_x)
        tau_job = w0.lam * xs["merge_read"] / xs["merges_timed"] if xs["merges_timed"] else 0.0
        if not w0.busy:
            ke2 = min(k2, w0.cap)
            files, d = max(n_x, ke2), max(0, ke2 - n_x) * w0.tF
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
            sc = sorted(((L[i]["bytes_end"] / w0.S[i], i) for i in range(1, w0.dp)), reverse=True)
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
        out["8a"] = dict(hit=abs(mf["after_x"] - d) <= w0.tF + tau_job + 0.1 * d, meas=mf["after_x"], pred=d,
                         allow=w0.tF + tau_job + 0.1 * d)
        out["8b"] = dict(hit=abs(mf["files_in"] - files) <= 1, meas=mf["files_in"], pred=files)
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
    return l0_schedule.Schedule(Path(ev_path), X, n_x, F0x, end)


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
    if om5 and w.grow is not None:                 # om5: bytes arriving in L1+ from X to the window's middle
        w.fwd_inflow = sum(w.grow[1:w.dp + 1]) * max(0.0, (lo + hi) / 2 - pre["hi"])
    p = w.predict(k_to, pre)
    pr = {"2": p["dn0"], "3": p["dbatch"], "4": p["dw0"], "5": p["dom"].get(1), "5b": p["dx"].get(1)}
    ke_s = ke_t = None
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
        if not all(c is None or c >= b for c in (fc.t4, fc.t2)):
            cuts = sorted({a, b} | {c for c in (fc.t4, fc.t2) if c is not None and a < c < b})
            out = {q: 0.0 for q in ("2", "3", "4", "5", "5b")}
            w._k2t_pieces = []
            for p_, q_ in zip(cuts, cuts[1:]):
                tm, wt = (p_ + q_) / 2, (q_ - p_) / (b - a)
                braked = any(c is not None and tm >= c for c in (fc.t4, fc.t2))
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
    if add5b is not None and pr.get("5b") is not None:
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
    integ2 = seg(0.0, d, n_x)
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
    tr = transition(w, p, pre, pc, snap_path, lo - X, hi - X)
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
import fill_r5 as fillm  # noqa: E402  (r5: byte-identical copy of r4/fill_r4.py, the per-cycle closed form)

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
    r0 = r0_kept(pre)
    b1 = w.l0s + w.l0o
    cap = None
    if K2T_CAP == "brake" and fc is not None and fc.t2 is not None:
        t2 = fc.t2
        cap = (lambda t: fc.path(t, "2")[1] if t >= t2 else None)
    lo_r, hi_r = lo - X, hi - X
    x2 = w._x3[2] if getattr(w, "_x3", None) else (getattr(w, "l0a", None) or 0.0)
    seq = fillm.fill(F0, tF, S, w.om, dp, kh2, z, h, b1, r0, lam_f, lam_s, n1, d, hi_r, cap=cap,
                     size0=(w.size if K2T_OM else None), x_brake=x2)
    if not seq:
        return None
    hr = dict(h, tau=h["tau_run"]) if h.get("tau_run") else h      # files flushed during the merge proper (no purge)
    am = [x2 if br else fillm.a_of(hr, b1, nk, nk * F0 + uk * S[1]) for bk, nk, ak, uk, br in seq]
    seq = [(bk, nk, ak, uk) for bk, nk, ak, uk, br in seq]
    n_x = L[0]["files_end"]

    def ref3(t):                                   # r3's K' files per merge at op X + t (its piece's own value)
        if not pieces:
            return nst
        pc_ = next((x for x in pieces if x[0] <= t < x[1]), pieces[-1] if t >= pieces[-1][1] else pieces[0])
        p_, q_, tm, used = pc_
        if used and any(c is not None and tm >= c for c in (fc.t4, fc.t2)):
            return fc.path(tm, "2")[1]
        return nst

    def seg(t0, t1, a0):                           # integral of a0 + (t - t0)/tF over [t0, t1] within the window
        u0, u1 = max(t0, lo_r), min(t1, hi_r)
        if u1 <= u0:
            return 0.0
        return (u1 - u0) * (a0 + ((u0 - t0) + (u1 - t0)) / (2 * tF))
    integ2 = seg(0.0, d, n_x)
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
    tr = transition2(w, p, pre, pc, w0s_path, fc, X, lo, hi, getattr(w, "_k2t_pieces", None))
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
