#!/usr/bin/env python3
"""[clean/r1 copy of clean/forward.py, 2026-10-27 (AFTER SEEING; owner "go, adopt all three"): imports the law copy
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
sys.path.insert(0, "/home/cc/node_ops/diag/kcase/clean/r1")
import law_r1 as m  # noqa: E402  (round 18 + om5 switches + brake hook; see its header)
import brake  # noqa: E402  (sub_brake/brake.py, copied unchanged)
import drain_order  # noqa: E402
import l0_schedule  # noqa: E402

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

    if brk and w.busy and k_from == m.K0:          # brake: cut the window at the forecast crossings
        key = ("fc", k_to)
        fc = fc_cache.get(key)
        if fc is None:
            wb = m.Win(pre, l1, t, cyc_pre, rs_pre, prev, kin=k_from)   # no fwd_inflow: sub_brake's own forecast
            wb.c03 = c03_pre
            wb.predict(k_to, pre)
            fc = brake.Forecast(wb, pre, prev, k_to)
            fc_cache[key] = fc
        a, b = lo - X, hi - X
        if not all(c is None or c >= b for c in (fc.t4, fc.t2)):
            cuts = sorted({a, b} | {c for c in (fc.t4, fc.t2) if c is not None and a < c < b})
            out = {q: 0.0 for q in ("2", "3", "4", "5", "5b")}
            for p_, q_ in zip(cuts, cuts[1:]):
                tm, wt = (p_ + q_) / 2, (q_ - p_) / (b - a)
                braked = any(c is not None and tm >= c for c in (fc.t4, fc.t2))
                pp = brake.projected_predict(fc, pre, prev, cyc_pre, rs_pre, c03_pre, l1, t, k_to, tm, BRAKE_MODE) \
                    if braked else None
                src = pp if pp is not None else pr
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
