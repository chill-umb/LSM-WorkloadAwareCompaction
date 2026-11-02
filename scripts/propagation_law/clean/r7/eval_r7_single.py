#!/usr/bin/env python3
"""[clean/r7 copy of r5/eval_r5_single.py, 2026-10-28: columns r5 = forward_r5.interval_preds_r5 (the baseline) and
r7 = forward_r7.interval_preds_r7 (r5 + r7: L0 merge start rate, brake, calm before-stretch). S1s: r7 off = r5 in every window; path checks identical.]
[clean/r5 copy of sub_k2t/eval_single_k2t.py, 2026-10-28: the "k2t" column is r5 = forward_r5.interval_preds_r5.]
k2t (2026-10-28): r3 against r3 + case 2's transition on the 675 single-change runs, scored as clean/r3/
eval_r3_single.py (forward from the last 5M ops, D-33 measure, limits and allowances as round 18, windows 1-6, bar =
0.9 x the no-change ceiling). Case 2 = K' < 4 (every single run starts from K = 4).
Self-check S1s: forward_k2t with k2t off returns exactly forward_r3.interval_preds_r3's dict in every window (both
modules side by side) and identical path checks. S2s (r3_single.log's r3 column) is checked by compare_k2t.py.
Usage: eval_single_k2t.py DEV_ROOT ROWS_OUT [NPROC]   (prints markdown; last line R7S_EXIT=0|1)"""
import json
import os
import sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
HERE = Path(__file__).resolve().parent
CLEAN = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(CLEAN))
sys.path.insert(0, "/home/cc/node_ops/diag/kcase/learncheck")
import forward_r7 as fk  # noqa: E402  (r7)
sys.path.insert(0, str(CLEAN / "r5"))
import forward_r5 as f3  # noqa: E402  (the baseline r5)
sys.path.insert(0, str(CLEAN / "r1"))
import eval_r1 as e1  # noqa: E402  (load, ceiling)
import learncheck as lc  # noqa: E402
from fwd_diag import c03_of  # noqa: E402

m = fk.m
QS = ("2", "3", "4", "5", "5b")
DEV = None


def one(a):
    w, t, l1, k2 = a
    pd = DEV / "pre5M"
    lab = f"{w}-T{t}-l1{l1}"
    name = f"{lab}-K{k2}"
    step = e1.load(m.KC / f"data/steps/{name}.json")
    pw = e1.load(pd / "windows_iv" / f"{name}.json")
    if step is None or pw is None:
        return None
    s = m.KeepNoise(m.KC / "data/twins_rec", lab)
    W, PW = step["windows"], pw["windows"]
    F00 = W[0]["flush_bytes"] / W[0]["flushes"]
    pc = e1.load(pd / "cycles_iv" / f"{name}.json")
    if pc:
        for k, c in enumerate(pc["windows"]):
            c["_median_files"] = fk.median_files(pc, k)
    cyc = pc["windows"][1] if pc else None
    rs = fk.own_rs(pc["windows"][:2]) if pc else None
    c03 = c03_of(pd / "c03_iv" / f"{name}.json", 1) if m.L0A == "3" else None
    F0x = PW[1]["flush_bytes"] / PW[1]["flushes"] if PW[1]["flushes"] else F00
    ev, X, n_x, end = m.EVENTS / f"{name}.json", W[1]["lo"], PW[1]["levels"][0]["files_end"], W[-1]["hi"] + 1
    s3 = f3.make_schedule(ev, X, n_x, F0x, end)
    sk = fk.make_schedule(ev, X, n_x, F0x, end)
    snap = pd / "snap_iv" / f"{name}.json"
    w0s = pd / "w0s_iv" / f"{name}.json"
    pargs = (PW[1], cyc, rs, l1, t, m.K0, k2, w0s, snap, step.get("first_l0_after_x"), W[1]["l0_peak"])
    pck, _ = fk.path_checks(*pargs, inflight=fk.inflight_l0(pd / "c03_iv" / f"{name}.json", X))   # r7 part j if on
    pcko, _ = fk.path_checks(*pargs)                                                                # part j off
    pc3, _ = f3.path_checks(*pargs)
    same_pc = json.dumps(pcko, sort_keys=True, default=str) == json.dumps(pc3, sort_keys=True, default=str)
    d8a, d8ao = pck.get("8a", {}).get("pred"), pcko.get("8a", {}).get("pred")
    out = []
    c3, co, cn = {}, {}, {}
    for k in range(1, len(W)):
        lo, hi, ops = W[k]["lo"], W[k]["hi"], W[k]["ops"]
        base = (PW[1], PW[0], cyc, rs, c03, l1, t, m.K0, k2)
        tail, tailo = (lo, hi, ops, F0x, X, d8a), (lo, hi, ops, F0x, X, d8ao)
        p3, _ = f3.interval_preds_r5(*base, s3, *tailo, c3, pc3, snap, w0s, r5="on")
        po, _ = fk.interval_preds_r7(*base, sk, *tailo, co, pcko, snap, w0s, r7="off")
        pn, _ = fk.interval_preds_r7(*base, sk, *tail, cn, pck, snap, w0s, r7="on")
        tr = (pn or {}).get("_k2t")
        row = dict(k=k, same=(po == p3), same_pc=same_pc, k2t=tr is not None,
                   tr=({kk: v for kk, v in tr.items() if kk != "seq"} if tr else None), q={})
        for qid, q, i, extra in fk.FAST:
            meas, thr = lc.safe(m.delta, step, s.twin, q, i, k), lc.safe(s.thr, q, i)
            if meas is None or thr is None:
                continue
            row["q"][qid] = (meas, thr + extra, (p3 or {}).get(qid), (pn or {}).get(qid))
        out.append(row)
    return dict(name=name, w=w, t=t, l1=l1, k2=k2, rows=out)


def main():
    global DEV
    DEV = Path(sys.argv[1])
    rows_out = Path(sys.argv[2])
    nproc = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    jobs = [(w, t, l1, k2) for w in lc.WS for t in range(2, 11) for l1 in (64, 128, 256) for k2 in m.KS[l1]]
    with Pool(nproc) as pool:
        res = [r for r in pool.map(one, jobs, chunksize=2) if r is not None]
    with open(rows_out, "w") as fh:
        for r in res:
            fh.write(json.dumps(r, default=str) + "\n")
    ceil = e1.ceiling()
    agg = defaultdict(lambda: [0, 0])
    gl = defaultdict(lambda: [0, 0])
    n = same = same_pc = napp = 0
    for r in res:
        w, cs = r["w"], ("2" if r["k2"] < m.K0 else "1")
        for row in r["rows"]:
            n += 1
            same += row["same"]
            same_pc += row["same_pc"]
            napp += row["k2t"]
            for qid, (meas, lim, p3, pn) in row["q"].items():
                hh = {}
                for v, pv in (("r5", p3), ("r7", pn)):
                    if pv is None:
                        continue
                    hh[v] = abs(meas - pv) <= lim
                    kk = 1 if row["k"] == 1 else "2-6"
                    for key in ((v, qid, w, kk), (v, qid, w, kk, cs)):
                        agg[key][0] += hh[v]
                        agg[key][1] += 1
                if len(hh) == 2:
                    kk = 1 if row["k"] == 1 else "2-6"
                    gl[(qid, w, kk)][0] += hh["r7"] and not hh["r5"]
                    gl[(qid, w, kk)][1] += hh["r5"] and not hh["r7"]

    def pct(h, n_):
        return f"{100 * h / n_:.0f}%" if n_ else "–"

    def bar(q, w):
        h, n_ = ceil[(q, w)]
        return 0.9 * 100 * h / n_

    def vd(key, q, w):
        h, n_ = agg[key]
        return "–" if not n_ else ("ok" if 100 * h / n_ >= bar(q, w) - 1e-9 else "FAIL")

    L = ["# r7 (r5 + r7: L0 merge start rate, brake, calm before-stretch) on the 675 single-change runs", "",
         f"S1s: r7 off = forward_r5.interval_preds_r5 in {same} of {n} windows: {'PASS' if same == n else 'FAIL'}.",
         f"S1s: path checks identical in {same_pc} of {n} windows: {'PASS' if same_pc == n else 'FAIL'}.",
         f"Windows where the transition applies: {napp} of {n}.", "",
         "| # | W | window 1: r5 → r7 | verdict | windows 2-6: r5 → r7 | verdict | bar |",
         "|---|---|---|---|---|---|---|"]
    for q in QS:
        for w in lc.WS:
            L.append(f"| {q} | {w} | {pct(*agg[('r5', q, w, 1)])} → {pct(*agg[('r7', q, w, 1)])} | "
                     f"{vd(('r5', q, w, 1), q, w)} → {vd(('r7', q, w, 1), q, w)} | "
                     f"{pct(*agg[('r5', q, w, '2-6')])} → {pct(*agg[('r7', q, w, '2-6')])} | "
                     f"{vd(('r5', q, w, '2-6'), q, w)} → {vd(('r7', q, w, '2-6'), q, w)} | {bar(q, w):.0f} |")
    L += ["", "## Case 2 (K' < 4) only: r5 → r7 (n)", "",
          "| # | W | window 1 | windows 2-6 |", "|---|---|---|---|"]
    for q in QS:
        for w in lc.WS:
            a1, a2 = ("r5", q, w, 1, "2"), ("r5", q, w, "2-6", "2")
            b1, b2 = ("r7", q, w, 1, "2"), ("r7", q, w, "2-6", "2")
            L.append(f"| {q} | {w} | {pct(*agg[a1])} → {pct(*agg[b1])} ({agg[a1][1]}) | "
                     f"{pct(*agg[a2])} → {pct(*agg[b2])} ({agg[a2][1]}) |")
    L += ["", "## Hits gained / lost (r7 vs r5)", "", "| # | window | " + " | ".join(lc.WS) + " |",
          "|---|---|" + "---|" * len(lc.WS)]
    for q in QS:
        for kk in (1, "2-6"):
            L.append(f"| {q} | {kk} | " + " | ".join(f"+{gl[(q, w, kk)][0]}/−{gl[(q, w, kk)][1]}" for w in lc.WS)
                     + " |")
    print("\n".join(L))
    print(f"R7S_EXIT={0 if same == n and same_pc == n else 1}")


if __name__ == "__main__":
    main()
