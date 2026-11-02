#!/usr/bin/env python3
"""[clean/r7 copy of r5/eval_r5_multi.py, 2026-10-28: columns r5 = forward_r5.interval_preds_r5 (the baseline) and
r7 = forward_r7.interval_preds_r7 (r5 + r7: L0 merge start rate, brake, calm before-stretch). S1: r7 off = r5 in every change; path checks identical.]
[clean/r5 copy of sub_k2t/eval_multi_k2t.py, 2026-10-28: the "k2t" column is r5 = forward_r5.interval_preds_r5.]
k2t (2026-10-28): r3 against r3 + case 2's transition (forward_k2t, switch k2t) on the 135 multi-change runs.
Scoring is clean/multi/eval_multi.py's, unchanged: change j (K_{j-1} -> K_j at X_j) predicted from the window before
it, scored over [X_j, X_{j+1}); measured change = (step after - step before) - (twin after - twin before); limit =
3 sqrt2 sqrt(pooled variance over the run's changes of each original repeat's change) + the FAST allowance; ceiling =
the original repeats scored as no-change runs; bar = 0.9 x ceiling.
Self-checks:
  S1  forward_k2t with k2t off returns exactly forward_r3.interval_preds_r3's dict in every change (both modules
      imported side by side), and the path checks of both modules are identical;
  S2  the r3 column reproduces multi/m5.log's r3 column (all changes and by case) -- checked by compare_k2t.py.
Usage: eval_multi_k2t.py MULTI_ROOT ROWS_OUT [NPROC]   (prints markdown; last line R7M_EXIT=0|1)"""
import json
import math
import os
import statistics as st
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
import forward_r5 as f3  # noqa: E402  (the baseline r5, its own modules law_r5, brake_r5, l0_schedule_r5, fill_r5)
from fwd_diag import c03_of  # noqa: E402
from score_k_v1 import qv  # noqa: E402

m = fk.m
WS = ("W1", "W2", "W3", "W4", "W5")
QS = ("2", "3", "4", "5", "5b")
VARS = ("r5", "r7")
ROOT = None
# SCORE=own (owner, 2026-10-28: "At the moment when K is changed in the multi change run, we calculate the prediction for
# each level using the propagation law, and then we check the actual state of the tree"): measured = the run's own
# change (after - before), no twin; limit unchanged (the original repeats' noise); bar = a plain 90% (ceiling all hits).
# SCORE=d33 (default): the D-33 measure above.
SCORE = os.environ.get("SCORE", "d33")


def load(p):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else None


def dq(wins, q, i, a, b):
    x, y = qv(wins[a], q, i), qv(wins[b], q, i)
    return None if x is None or y is None else x - y


def strip(d):
    return d


def one(name):
    meta = load(ROOT / "meta" / f"{name}.json")
    Wd = load(ROOT / "win" / f"{name}.json")
    T = load(ROOT / "twin" / f"{meta['lab']}.json")
    R = [load(ROOT / "rep" / f"{meta['lab']}-r{r}.json") for r in (1, 2, 3)]
    if Wd is None or T is None or None in R:
        return dict(name=name, missing=True)
    W, T, R = Wd["windows"], T["windows"], [r["windows"] for r in R]
    first_by = Wd.get("first_l0_by_win") or []
    pc = load(ROOT / "cyc" / f"{name}.json")
    if pc:
        for k, c in enumerate(pc["windows"]):
            c["_median_files"] = fk.median_files(pc, k)
    l1, t, ap = meta["l1"], meta["t"], meta["applies"]
    n = len(ap)
    lims = {}
    for qid, q, i, extra in fk.FAST:
        vs = []
        for j in range(n):
            d = [dq(r, q, i, j + 2, j + 1) for r in R]
            if None not in d:
                vs.append(st.variance(d))
        lims[qid] = (3 * math.sqrt(2) * math.sqrt(sum(vs) / len(vs)) + extra) if vs else None
    out = dict(name=name, w=meta["w"], t=t, l1=l1, changes=[])
    F00 = W[0]["flush_bytes"] / W[0]["flushes"]
    ev = ROOT / "ev" / f"{name}.json"
    for j in range(n):
        p, a = j + 1, j + 2
        X, k_to = ap[j]
        k_from = 4 if j == 0 else ap[j - 1][1]
        pre, prev = W[p], W[p - 1]
        lo, hi, ops = W[a]["lo"], W[a]["hi"], W[a]["ops"]
        F0x = pre["flush_bytes"] / pre["flushes"] if pre["flushes"] else F00
        cyc = pc["windows"][p] if pc else None
        rs = fk.own_rs(pc["windows"][:p + 1]) if pc else None
        c03 = c03_of(ROOT / "c03" / f"{name}.json", p) if m.L0A == "3" else None
        f = first_by[a] if a < len(first_by) else None
        mf = dict(after_x=f["after"], files_in=f["files_in"]) if f else None
        w0s = ROOT / "w0s" / f"{name}-c{j}.json"
        snap = ROOT / "snap" / f"{name}-c{j}.json"
        jf = fk.inflight_l0(ROOT / "c03" / f"{name}.json", X)      # r7 part j (used only when R7_PARTS has j)
        pc1, _ = fk.path_checks(pre, cyc, rs, l1, t, k_from, k_to, w0s, snap, mf, W[a]["l0_peak"], inflight=jf)
        nomf_, fk.PC_NOMF = fk.PC_NOMF, False                      # the off state is the law before part y: no R7_PC_NOMF either
        pc1o, _ = fk.path_checks(pre, cyc, rs, l1, t, k_from, k_to, w0s, snap, mf, W[a]["l0_peak"])   # part j off
        fk.PC_NOMF = nomf_
        pc3, _ = f3.path_checks(pre, cyc, rs, l1, t, k_from, k_to, w0s, snap, mf, W[a]["l0_peak"])
        d8a, d8ao = pc1.get("8a", {}).get("pred"), pc1o.get("8a", {}).get("pred")
        s_k = fk.make_schedule(ev, X, pre["levels"][0]["files_end"], F0x, W[-1]["hi"] + 1)
        s_3 = f3.make_schedule(ev, X, pre["levels"][0]["files_end"], F0x, W[-1]["hi"] + 1)
        a3 = (pre, prev, cyc, rs, c03, l1, t, k_from, k_to, s_3, lo, hi, ops, F0x, X, d8ao)
        ak = (pre, prev, cyc, rs, c03, l1, t, k_from, k_to, s_k, lo, hi, ops, F0x, X, d8a)
        ako = (pre, prev, cyc, rs, c03, l1, t, k_from, k_to, s_k, lo, hi, ops, F0x, X, d8ao)
        r3 = f3.interval_preds_r5(*a3, {}, pc3, snap, w0s, r5="on")[0]
        off = fk.interval_preds_r7(*ako, {}, pc1o, snap, w0s, r7="off")[0]
        on = fk.interval_preds_r7(*ak, {}, pc1, snap, w0s, r7="on")[0]
        pr = {"r5": r3, "r7": on}
        tr = (on or {}).get("_k2t")
        row = dict(j=j, X=X, k_from=k_from, k_to=k_to, ops=ops, pre_ops=pre["ops"],
                   s1=(off == r3), s1pc=(json.dumps(pc1o, sort_keys=True, default=str)
                                         == json.dumps(pc3, sort_keys=True, default=str)),
                   busy=(r3 or {}).get("_busy"), braked=bool((r3 or {}).get("_brake")), k2t=tr is not None,
                   tr=({k: v for k, v in tr.items() if k != "seq"} if tr else None), seq=(tr or {}).get("seq"),
                   path={q: pc1.get(q, {}).get("hit") for q in ("7", "8a", "8b")}, q={})
        for qid, q, i, extra in fk.FAST:
            meas = None
            ds, dt = dq(W, q, i, a, p), dq(T, q, i, a, p)
            ceil = []
            if SCORE == "own":
                meas = ds
                if ds is not None and lims[qid] is not None:
                    ceil = [True] * len(R)
            else:
                if ds is not None and dt is not None:
                    meas = ds - dt
                for r in R:
                    dr = dq(r, q, i, a, p)
                    if dr is not None and dt is not None and lims[qid] is not None:
                        ceil.append(abs(dr - dt) <= lims[qid])
            row["q"][qid] = dict(meas=meas, lim=lims[qid], ceil=ceil, **{v: (pr[v] or {}).get(qid) for v in VARS})
        out["changes"].append(row)
    return out


def pct(h, n):
    return f"{100 * h / n:.0f}%" if n else "–"


def main():
    global ROOT
    ROOT = Path(sys.argv[1])
    rows_out = Path(sys.argv[2])
    nproc = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    names = sorted(p.stem for p in (ROOT / "meta").glob("*.json"))
    with Pool(nproc) as pool:
        res = pool.map(one, names, chunksize=1)
    with open(rows_out, "w") as fh:
        for r in res:
            fh.write(json.dumps(r, default=str) + "\n")
    res = [r for r in res if not r.get("missing")]
    agg = defaultdict(lambda: [0, 0])
    gl = defaultdict(lambda: [0, 0])

    def add(key, hit):
        agg[key][0] += hit
        agg[key][1] += 1

    for r in res:
        w = r["w"]
        for c in r["changes"]:
            for q, h in c["path"].items():
                if h is not None:
                    add(("path", q, w), h)
            groups = [("all",), ("case", "1 raised" if c["k_to"] > c["k_from"] else "2 lowered"),
                      ("tree", "busy" if c["busy"] else "calm"),
                      ("from", "from K=4" if c["k_from"] == 4 else "from K!=4"),
                      ("k2t", "transition applied" if c["k2t"] else "not applied")]
            for qid, d in c["q"].items():
                if d["meas"] is None or d["lim"] is None:
                    continue
                for h in d["ceil"]:
                    add(("ceil", qid, w), h)
                hits = {}
                for v in VARS:
                    if d.get(v) is None:
                        continue
                    hits[v] = abs(d["meas"] - d[v]) <= d["lim"]
                    for g in groups:
                        add((v, qid, w) + g, hits[v])
                if "r5" in hits and "r7" in hits:
                    cs = "1" if c["k_to"] > c["k_from"] else "2"
                    gl[(qid, w, cs)][0] += hits["r7"] and not hits["r5"]
                    gl[(qid, w, cs)][1] += hits["r5"] and not hits["r7"]

    def rate(key):
        h, n = agg[key]
        return 100 * h / n if n else None

    nchg = sum(len(r["changes"]) for r in res)
    s1 = sum(c["s1"] for r in res for c in r["changes"])
    s1pc = sum(c["s1pc"] for r in res for c in r["changes"])
    napp = sum(c["k2t"] for r in res for c in r["changes"])
    L = ["# r7 (r5 + r7: L0 merge start rate, brake, calm before-stretch) on the 135 multi-change runs", "",
         f"Runs scored: {len(res)}; changes: {nchg}; transition applied in {napp}.", "",
         f"S1: r7 off = forward_r5.interval_preds_r5 in {s1} of {nchg} changes: {'PASS' if s1 == nchg else 'FAIL'}.",
         f"S1: path checks identical (forward_r7 vs forward_r5) in {s1pc} of {nchg}: "
         f"{'PASS' if s1pc == nchg else 'FAIL'}.", "",
         "## All changes, hits % (changes scored)", "",
         "| # | W | r5 | r7 | n | ceiling | bar | r5 | r7 |", "|---|---|---|---|---|---|---|---|---|"]
    for q in QS:
        for w in WS:
            c = rate(("ceil", q, w))
            v = [rate(("r5", q, w, "all")), rate(("r7", q, w, "all"))]
            vd = ["–" if c is None or x is None else ("ok" if x >= 0.9 * c - 1e-9 else "FAIL") for x in v]
            L.append(f"| {q} | {w} | {pct(*agg[('r5', q, w, 'all')])} | {pct(*agg[('r7', q, w, 'all')])} | "
                     f"{agg[('r5', q, w, 'all')][1]} | {pct(*agg[('ceil', q, w)])} | "
                     + (f"{0.9 * c:.0f}" if c is not None else "–") + f" | {vd[0]} | {vd[1]} |")
    for dim, title, vals in (("case", "By case", ("1 raised", "2 lowered")),
                             ("tree", "By tree state (the law's busy test)", ("calm", "busy")),
                             ("from", "By the K before the change", ("from K=4", "from K!=4")),
                             ("k2t", "Where the transition applies", ("transition applied", "not applied"))):
        L += ["", f"## {title}: r5 → r7 hits % (n)", "", "| # | group | " + " | ".join(WS) + " |",
              "|---|---|" + "---|" * len(WS)]
        for q in QS:
            for g in vals:
                L.append(f"| {q} | {g} | " + " | ".join(
                    f"{pct(*agg[('r5', q, w, dim, g)])} → {pct(*agg[('r7', q, w, dim, g)])} "
                    f"({agg[('r5', q, w, dim, g)][1]})" for w in WS) + " |")
    L += ["", "## Hits gained / lost (r7 vs r5), by case", "", "| # | case | " + " | ".join(WS) + " |",
          "|---|---|" + "---|" * len(WS)]
    for q in QS:
        for cs in ("1", "2"):
            L.append(f"| {q} | {cs} | " + " | ".join(f"+{gl[(q, w, cs)][0]}/−{gl[(q, w, cs)][1]}" for w in WS) + " |")
    L += ["", "## Path checks per change (unchanged by r7)", "", "| # | " + " | ".join(WS) + " |",
          "|---|" + "---|" * len(WS)]
    for q in ("7", "8a", "8b"):
        L.append(f"| {q} | " + " | ".join(f"{pct(*agg[('path', q, w)])} ({agg[('path', q, w)][1]})" for w in WS)
                 + " |")
    L += ["", f"Rows: {rows_out}"]
    print("\n".join(L))
    print(f"R7M_EXIT={0 if s1 == nchg and s1pc == nchg else 1}")


if __name__ == "__main__":
    main()
