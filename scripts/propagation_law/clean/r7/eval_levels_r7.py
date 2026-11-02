#!/usr/bin/env python3
"""[clean/r7 copy of r5/eval_levels_r5.py, 2026-10-28: r7 in place of r5 (R7SW=on, default) or r5 itself (R7SW=off,
the self-check: its log must equal r5_levels.log); the "r3" labels below mean the law scored.]
[clean/r5 copy of r4/eval_levels_r4.py, 2026-10-28: r5 in place of r4; the "r3" labels below mean r5.]
[clean/r4 copy of multi/eval_levels.py, 2026-10-28: r4 in place of r3; the "r3" labels below mean r4.]
Every level's change on the 135 multi-change runs (operator, 2026-10-27; owner: "of course we will do the L2+
comparisons"; the law must give each level L1..L_n a change, zero where it says the level stays the same).

For each applied change j (K_{j-1} -> K_j at X_j), r3 forward (as eval_multi.py) gives Win.predict's settled change
for the whole tree from the run's own pre-change window; per level i >= 1 it is scored on:
- size  (bytes_mean): predicted size2[i] - size[i]; allowance one file (FILE_BYTES), as round 18's #6;
- x     (bytes written per op by merges out of level i): predicted dx[i]; i < deepest;
- omega (overlap per byte pushed out of level i): predicted dom[i] (0 where the law gives none); i < deepest;
- f     (bytes flowing out of level i per op): predicted df[i] (+ r3's storage term on L1); i < deepest (#10).
Measured change, limits (pooled over the run's changes), ceiling and bar exactly as eval_multi.py. Level 1's omega
and x are #5 and #5b (r3's interval predictions, as eval_multi); deeper levels have no interval rules.
Reference column "zero": a prediction of no change everywhere.
Usage: eval_levels_r7.py MULTI_ROOT ROWS_OUT [NPROC]   (prints markdown; LEVELS_R7_EXIT=0)"""
import json
import os
import math
import statistics as st
import sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

CLEAN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CLEAN / "r7"))
sys.path.insert(0, str(CLEAN))
sys.path.insert(0, "/home/cc/node_ops/diag/kcase/learncheck")
import forward_r7 as f3  # noqa: E402  (r7; name kept so the body is unchanged)
from fwd_diag import c03_of  # noqa: E402
from score_k_v1 import qv  # noqa: E402

m = f3.m
WS = ("W1", "W2", "W3", "W4", "W5")
QL = (("size", "size"), ("x", "bytes written"), ("omega", "rewrite per byte pushed"), ("f", "flow out"))
ROOT = None
SCORE = os.environ.get("SCORE", "d33")   # own (owner, 2026-10-28): the run's own change, no twin; bar a plain 90%
R7SW = os.environ.get("R7SW", "on")


def load(p):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else None


def val(win, q, i):
    if i >= len(win["levels"]):
        return None
    return qv(win, q, i)


def dq(wins, q, i, a, b):
    x, y = val(wins[a], q, i), val(wins[b], q, i)
    return None if x is None or y is None else x - y


def one(name):
    meta = load(ROOT / "meta" / f"{name}.json")
    S = load(ROOT / "win" / f"{name}.json")
    T = load(ROOT / "twin" / f"{meta['lab']}.json")
    R = [load(ROOT / "rep" / f"{meta['lab']}-r{r}.json") for r in (1, 2, 3)]
    if S is None or T is None or None in R:
        return None
    first_by = S.get("first_l0_by_win") or []
    S, T, R = S["windows"], T["windows"], [r["windows"] for r in R]
    pc = load(ROOT / "cyc" / f"{name}.json")
    if pc:
        for k, c in enumerate(pc["windows"]):
            c["_median_files"] = f3.median_files(pc, k)
    l1, t, ap = meta["l1"], meta["t"], meta["applies"]
    n = len(ap)
    nlev = max(len(w_["levels"]) for w_ in S)
    lims = {}
    for q, _ in QL:
        for i in range(1, nlev):
            vs = []
            for j in range(n):
                d = [dq(r, q, i, j + 2, j + 1) for r in R]
                if None not in d:
                    vs.append(st.variance(d))
            extra = m.FILE_BYTES if q == "size" else 0.0
            lims[(q, i)] = (3 * math.sqrt(2) * math.sqrt(sum(vs) / len(vs)) + extra) if vs else None
    F00 = S[0]["flush_bytes"] / S[0]["flushes"]
    out = dict(name=name, w=meta["w"], t=t, l1=l1, changes=[])
    for j in range(n):
        p_, a = j + 1, j + 2
        X, k_to = ap[j]
        k_from = 4 if j == 0 else ap[j - 1][1]
        pre, prev = S[p_], S[p_ - 1]
        lo, hi, ops = S[a]["lo"], S[a]["hi"], S[a]["ops"]
        F0x = pre["flush_bytes"] / pre["flushes"] if pre["flushes"] else F00
        sched = f3.make_schedule(ROOT / "ev" / f"{name}.json", X, pre["levels"][0]["files_end"], F0x, S[-1]["hi"] + 1)
        cyc = pc["windows"][p_] if pc else None
        rs = f3.own_rs(pc["windows"][:p_ + 1]) if pc else None
        c03 = c03_of(ROOT / "c03" / f"{name}.json", p_) if m.L0A == "3" else None
        f = first_by[a] if a < len(first_by) else None
        mf = dict(after_x=f["after"], files_in=f["files_in"]) if f else None
        snap = ROOT / "snap" / f"{name}-c{j}.json"
        pc1, _ = f3.path_checks(pre, cyc, rs, l1, t, k_from, k_to, ROOT / "w0s" / f"{name}-c{j}.json", snap, mf,
                                S[a]["l0_peak"], inflight=f3.inflight_l0(ROOT / "c03" / f"{name}.json", X))
        d8a = pc1.get("8a", {}).get("pred")
        pr, w = f3.interval_preds_r7(pre, prev, cyc, rs, c03, l1, t, k_from, k_to, sched, lo, hi, ops, F0x, X, d8a,
                                     {}, pc1, snap, ROOT / "w0s" / f"{name}-c{j}.json", r7=R7SW)
        row = dict(j=j, k_from=k_from, k_to=k_to, ops=ops, busy=(pr or {}).get("_busy"),
                   deepest=S[a]["deepest"], nopred=pr is None, lv={})
        if pr is not None:
            p = w.predict(k_to, pre)                 # the settled change r3's window used (w carries its fwd_inflow)
            st_ = pr.get("_stor")
            deepest = S[a]["deepest"]
            for i in range(1, deepest + 1):
                for q, _ in QL:
                    if q != "size" and i >= deepest:
                        continue
                    if q == "size":
                        pv = p["size2"][i] - w.size[i] if i < len(p["size2"]) and i < len(w.size) else 0.0
                    elif q == "x":
                        pv = pr.get("5b") if i == 1 else p["dx"].get(i, 0.0)
                    elif q == "omega":
                        pv = pr.get("5") if i == 1 else p["dom"].get(i, 0.0)
                    else:
                        pv = p["df"].get(i, 0.0) + (st_["df1"] if (i == 1 and st_) else 0.0)
                    ds, dt = dq(S, q, i, a, p_), dq(T, q, i, a, p_)
                    lim = lims.get((q, i))
                    if SCORE == "own":
                        if ds is None or lim is None or pv is None:
                            continue
                        row["lv"][f"{q}{i}"] = dict(meas=ds, pred=pv, lim=lim, ceil=[True] * len(R),
                                                    last=(i == deepest))
                        continue
                    if ds is None or dt is None or lim is None or pv is None:
                        continue
                    ceil = [abs(dq(r, q, i, a, p_) - dt) <= lim for r in R if dq(r, q, i, a, p_) is not None]
                    row["lv"][f"{q}{i}"] = dict(meas=ds - dt, pred=pv, lim=lim, ceil=ceil,
                                                last=(i == deepest))
        out["changes"].append(row)
    return out


def main():
    global ROOT
    ROOT = Path(sys.argv[1])
    rows_out = Path(sys.argv[2])
    nproc = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    names = sorted(p.stem for p in (ROOT / "meta").glob("*.json"))
    with Pool(nproc) as pool:
        res = [r for r in pool.map(one, names, chunksize=1) if r is not None]
    with open(rows_out, "w") as fh:
        for r in res:
            fh.write(json.dumps(r, default=str) + "\n")
    agg = defaultdict(lambda: [0, 0])

    def add(k, h):
        agg[k][0] += h
        agg[k][1] += 1

    for r in res:
        w = r["w"]
        for c in r["changes"]:
            case = "1" if c["k_to"] > c["k_from"] else "2"
            for key, d in c["lv"].items():
                q, i = key.rstrip("0123456789"), int(key[len(key.rstrip("0123456789")):])
                lvl = "last" if d["last"] else f"L{i}"
                for h in d["ceil"]:
                    add(("ceil", q, lvl, w), h)
                add(("r3", q, lvl, w), abs(d["meas"] - d["pred"]) <= d["lim"])
                add(("zero", q, lvl, w), abs(d["meas"]) <= d["lim"])
                add(("r3c", q, lvl, w, case), abs(d["meas"] - d["pred"]) <= d["lim"])
                add(("r3", "all", lvl, w), abs(d["meas"] - d["pred"]) <= d["lim"])
                add(("zero", "all", lvl, w), abs(d["meas"]) <= d["lim"])
                for h in d["ceil"]:
                    add(("ceil", "all", lvl, w), h)

    def pct(k):
        h, n_ = agg[k]
        return f"{100 * h / n_:.0f}" if n_ else "–"

    def rate(k):
        h, n_ = agg[k]
        return 100 * h / n_ if n_ else None

    levels = sorted({k[2] for k in agg if k[0] == "r3"}, key=lambda s: (s == "last", int(s[1:]) if s != "last" else 99))
    L = ["# Every level's change, r3 forward, on the 135 multi-change runs", "",
         "Cell: r3 hits % / no-change guess hits % [bar = 0.9 x ceiling]; ✓ = r3 passes the bar. "
         "\"last\" = each run's deepest level (size only).", ""]
    for q, qn in (("all", "all four quantities pooled"),) + QL:
        L += [f"## {qn}", "", "| level | " + " | ".join(WS) + " |", "|---|" + "---|" * len(WS)]
        for lvl in levels:
            cells = []
            for w in WS:
                x, c = rate(("r3", q, lvl, w)), rate(("ceil", q, lvl, w))
                if x is None:
                    cells.append("–")
                    continue
                ok = c is not None and x >= 0.9 * c - 1e-9
                cells.append(f"{x:.0f} / {pct(('zero', q, lvl, w))} [{0.9 * c:.0f}]{' ✓' if ok else ''} "
                             f"(n {agg[('r3', q, lvl, w)][1]})")
            L.append(f"| {lvl} | " + " | ".join(cells) + " |")
        L.append("")
    L += ["## By case (r3 hits %: case 1 raised / case 2 lowered), sizes", "", "| level | " + " | ".join(WS) + " |",
          "|---|" + "---|" * len(WS)]
    for lvl in levels:
        L.append(f"| {lvl} | " + " | ".join(f"{pct(('r3c', 'size', lvl, w, '1'))} / {pct(('r3c', 'size', lvl, w, '2'))}"
                                            for w in WS) + " |")
    print("\n".join(L))
    print("LEVELS_R7_EXIT=0")


if __name__ == "__main__":
    main()
