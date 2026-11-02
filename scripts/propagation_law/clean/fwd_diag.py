#!/usr/bin/env python3
"""Why the forward mode misses on the open single-change runs (owner 2026-10-27: "Develop forward first").
Diagnostic, AFTER SEEING; operator, 2026-10-27. Three predictions of the change in each window k = 1..6 after X:
- F0: forward from the run's own window 0 (before X, K = 4): what the controller can do (forward.interval_preds);
- FT: forward from the twin's window k (the tree at K = 4 at the same time; diagnostic only, the twin is never a
  law input): no drift since window 0, and no transition either;
- R: round 18's reverse prediction from the run's own window k (learncheck's dump).
Reading: F0 bad but FT good -> the state drifted since window 0; FT and R bad in window 1 but good later -> the
window is still in transition (the law is a settled law); all bad everywhere -> the law itself.
Usage: fwd_diag.py   (prints markdown; FWDDIAG_EXIT=0)
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, "/home/cc/node_ops/diag/kcase/learncheck")
import forward as fw  # noqa: E402
import learncheck as lc  # noqa: E402

m = fw.m
KC = m.KC
QMAP = {("l0_files", 0): "2", ("files_in", 0): "3", ("x", 0): "4", ("omega", 1): "5", ("x", 1): "5b"}
C03T = KC / "diag_2026-10-25i/c03_out"


def cycles(name):
    p = m.CYC / f"{name}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    for k, c in enumerate(d["windows"]):
        c["_median_files"] = fw.median_files(d, k)
    return d


def c03_of(path, k):
    if not path.exists():
        return None
    name = path.stem
    old = m.C03
    try:
        m.C03 = path.parent
        return (m.c03_parts(name) or {}).get(k)
    finally:
        m.C03 = old


def main():
    _, cells = lc.collect()                       # reverse residuals in every window (round 18, self-checked)
    rev = {}
    for c in cells:
        qid = QMAP.get((c["q"], c["i"]))
        if qid:
            rev[(c["run"], c["k"], qid)] = abs(c["r"]) <= c["lim"]
    agg = defaultdict(lambda: [0, 0])             # (pred, qid, group, k) -> hits, n
    for w in lc.WS:
        g = "busy (W3-W5)" if w in ("W3", "W4", "W5") else "calm (W1-W2)"
        for t in range(2, 11):
            for l1 in (64, 128, 256):
                lab = f"{w}-T{t}-l1{l1}"
                s = m.KeepNoise(KC / "data/twins_rec", lab)
                tw = s.twin["windows"]
                tcd = cycles(f"{lab}-r1")
                trs = fw.own_rs(tcd["windows"]) if tcd else None
                for k2 in m.KS[l1]:
                    name = f"{lab}-K{k2}"
                    p = KC / f"data/steps/{name}.json"
                    if not p.exists():
                        continue
                    step = json.loads(p.read_text())
                    W = step["windows"]
                    cd = cycles(name)
                    cyc0 = cd["windows"][0] if cd else None
                    rs0 = fw.own_rs(cd["windows"][:1]) if cd else None
                    F0x = W[0]["flush_bytes"] / W[0]["flushes"]
                    sched = fw.make_schedule(m.EVENTS / f"{name}.json", W[1]["lo"], W[0]["levels"][0]["files_end"],
                                             F0x, W[-1]["hi"] + 1)
                    c03_0 = (m.c03_parts(name) or {}).get(0) if m.L0A == "3" else None
                    for k in range(1, len(W)):
                        lo, hi, ops = W[k]["lo"], W[k]["hi"], W[k]["ops"]
                        preds = {}
                        preds["F0"], _ = fw.interval_preds(W[0], None, cyc0, rs0, c03_0, l1, t, m.K0, k2, sched, lo,
                                                           hi, ops, F0x)
                        tc = tcd["windows"][k] if tcd and k < len(tcd["windows"]) else None
                        c03_t = c03_of(C03T / f"{lab}-r1.json", k) if m.L0A == "3" else None
                        preds["FT"], _ = fw.interval_preds(tw[k], tw[k - 1], tc, trs, c03_t, l1, t, m.K0, k2, sched,
                                                           lo, hi, ops, F0x)
                        for qid, q, i, extra in fw.FAST:
                            meas, thr = lc.safe(m.delta, step, s.twin, q, i, k), lc.safe(s.thr, q, i)
                            if meas is None or thr is None:
                                continue
                            for pn in ("F0", "FT"):
                                pr = preds[pn]
                                if pr is None or pr.get(qid) is None:
                                    continue
                                a = agg[(pn, qid, g, k)]
                                a[0] += abs(meas - pr[qid]) <= thr + extra
                                a[1] += 1
                            h = rev.get((name, k, qid))
                            if h is not None:
                                a = agg[("R", qid, g, k)]
                                a[0] += h
                                a[1] += 1
    L = ["# Why forward misses (AFTER SEEING): hits % by window after the change", "",
         "F0 = forward from the run's own window 0; FT = forward from the twin's window k (diagnostic); "
         "R = round 18 reverse from the run's own window k.", "",
         "| # | Trees | Prediction | " + " | ".join(f"w{k}" for k in range(1, 7)) + " |", "|---|---|---|" + "---|" * 6]
    for qid in ("2", "3", "4", "5", "5b"):
        for g in ("calm (W1-W2)", "busy (W3-W5)"):
            for pn in ("F0", "FT", "R"):
                cs = []
                for k in range(1, 7):
                    h, n = agg[(pn, qid, g, k)]
                    cs.append(f"{100 * h / n:.0f}%" if n else "–")
                L.append(f"| {qid} | {g} | {pn} | " + " | ".join(cs) + " |")
    print("\n".join(L))
    print("FWDDIAG_EXIT=0")


if __name__ == "__main__":
    main()
