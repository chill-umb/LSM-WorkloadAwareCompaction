#!/usr/bin/env python3
"""Forward mode of the frozen cases 1-2 law (round 18, v2/score_k_v2r18.py, unchanged) for the clean test on the 135
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
import score_k_v2r18 as m  # noqa: E402
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
