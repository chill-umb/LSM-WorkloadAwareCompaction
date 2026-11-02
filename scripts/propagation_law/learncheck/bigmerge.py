#!/usr/bin/env python3
"""The big-merge guess (owner 2026-10-27: "Test the big merge guess"). Operator, 2026-10-27, written before any of
these numbers was seen (the learnability check's result, which raised the guess, was seen).

Guess: in busy trees the law's deep-level errors jump from window to window because merges move a level's bytes in
lumps, so the window a lump lands in decides the error.

Four tests on the 675 single-change runs (windows 1-6 after X) and their settings' native runs:
T1 one big merge? Bytes one average merge pushes out of deep level i in a window (s / merges), against the limit for
   level i's bytes (and, per op, for its outflow). One merge can decide a window only if it exceeds the limit.
T2 timing or run level? Split each missed window's error r_k into the run's mean over windows 1-6 and the rest. A
   timing miss: the run's mean error is within the limit (the law is right on average, the window is off).
T3 in phase? Each run's window pattern (windows 1-6, a fitted straight line removed) of level bytes and outflow:
   correlation between the three original native repeats (same tree, same seed, run back to back), between the twin
   of record (the 2026-10-24 rerun) and original repeat 1, and between each step run and its twin of record.
T4 no-change ceiling. Each original repeat scored as a "step" that changes nothing (forecast 0) against the twin of
   record, with the scorer's D-33 measure and limits; original repeat 1 against repeat 2 as the in-phase control.
   What a perfect law could reach window by window.

Verdicts, set before seeing them (busy trees W3-W5, levels L2 and deeper):
- one big merge: confirmed if one average merge exceeds the level-bytes limit in at least half the windows; else
  "many small merges".
- timing: confirmed if at least 2/3 of the misses are timing misses.
- phase: confirmed if the repeats correlate (median >= 0.7) while step vs twin of record does not (median < 0.3).
- measurement ceiling: the window-by-window test cannot reach 90% if the no-change ceiling is under 90%.

Usage: bigmerge.py   (prints markdown; BIGMERGE_EXIT=0 at the end)
"""
import json
import math
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import learncheck as lc  # noqa: E402
from score_k_v1 import FILE_BYTES, delta, qv  # noqa: E402

KC = lc.KC
BUSY = ("W3", "W4", "W5")
DEEP = ("L2", "L3+", "last")


def group(w):
    return "busy (W3-W5)" if w in BUSY else "calm (W1-W2)"


def pattern(d, q, i):
    v = [lc.safe(qv, d["windows"][k], q, i) for k in range(1, 7)]
    if None in v:
        return None
    mx, my = 3.5, sum(v) / 6
    sl = sum((x - mx) * (y - my) for x, y in zip(range(1, 7), v)) / 17.5
    return [y - (my + sl * (x - mx)) for x, y in zip(range(1, 7), v)]


def corr(a, b):
    if a is None or b is None:
        return None
    sa, sb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (sa * sb) if sa > 0 and sb > 0 else None


def med(v):
    v = [x for x in v if x is not None]
    return f"{st.median(v):.2f}" if v else "–"


def load(p):
    return json.loads(p.read_text()) if p.exists() else None


def main():
    _, cells = lc.collect()
    m = lc.m
    lim = {}                                   # (lab, q, i) -> limit with allowance, from the scorer's cells
    for c in cells:
        lim[(c["run"].rsplit("-K", 1)[0], c["q"], c["i"])] = c["lim"]

    # T2: timing vs run-level misses
    ser = defaultdict(dict)
    for c in cells:
        ser[(c["run"], c["q"], c["i"])][c["k"]] = c
    t2 = defaultdict(lambda: [0, 0, 0])        # misses, timing misses, cells
    for s in ser.values():
        if len(s) < 6:
            continue
        mr = sum(c["r"] for c in s.values()) / len(s)
        for c in s.values():
            key = (group(c["w"]), c["band"], c["q"])
            t2[key][2] += 1
            if abs(c["r"]) > c["lim"]:
                t2[key][0] += 1
                t2[key][1] += abs(mr) <= c["lim"]

    # T1, T3, T4 per setting
    t1 = defaultdict(lambda: [[], [], []])      # merges per window, size ratio, flow ratio
    t3 = defaultdict(lambda: [[], [], []])      # repeats, rerun vs original, step vs twin of record
    t4 = defaultdict(lambda: [0, 0, 0, 0])      # no-change hits, cells, control hits, control cells
    for w in lc.WS:
        for t in range(2, 11):
            for l1 in (64, 128, 256):
                lab = f"{w}-T{t}-l1{l1}"
                rec = load(KC / f"data/twins_rec/{lab}-r1.json")
                if rec is None:
                    continue
                orig = [load(KC / f"data/twins/{lab}-r{r}.json") for r in (1, 2, 3)]
                s = m.KeepNoise(KC / "data/twins_rec", lab)
                steps = [load(KC / f"data/steps/{lab}-K{k}.json") for k in m.KS[l1]]
                steps = [x for x in steps if x]
                deepest = rec["windows"][-1]["deepest"]
                for i in range(2, deepest + 1):
                    b = lc.band(i, deepest)
                    for q, extra in (("size", FILE_BYTES), ("f", 0.0), ("x", 0.0)):
                        if q != "size" and i == deepest:
                            continue
                        key = (group(w), b, q)
                        po = [pattern(o, q, i) for o in orig]
                        pr = pattern(rec, q, i)
                        t3[key][0] += [corr(po[0], po[1]), corr(po[0], po[2]), corr(po[1], po[2])]
                        t3[key][1].append(corr(pr, po[0]))
                        t3[key][2] += [corr(pattern(x, q, i), pr) for x in steps]
                        thr = lc.safe(s.thr, q, i)
                        if thr is None:
                            continue
                        for k in range(1, 7):
                            for o in orig:
                                d = lc.safe(delta, o, rec, q, i, k)
                                if d is not None:
                                    t4[key][0] += abs(d) <= thr + extra
                                    t4[key][1] += 1
                            d = lc.safe(delta, orig[0], orig[1], q, i, k)
                            if d is not None:
                                t4[key][2] += abs(d) <= thr + extra
                                t4[key][3] += 1
                    if i == deepest:
                        continue
                    ls, lf = lim.get((lab, "size", i)), lim.get((lab, "f", i))
                    for x in steps:
                        for k in range(1, 7):
                            L = x["windows"][k]["levels"]
                            if i >= len(L) or not L[i]["merges"]:
                                continue
                            per = L[i]["s"] / L[i]["merges"]
                            t1[(group(w), b)][0].append(L[i]["merges"])
                            if ls:
                                t1[(group(w), b)][1].append(per / ls)
                            if lf:
                                t1[(group(w), b)][2].append(per / x["windows"][k]["ops"] / lf)

    out = ["# The big-merge guess", ""]
    out += ["## T1: can one merge decide a window? (step runs, merges out of the level)", "",
            "| Trees | Level | Windows | Merges per window (median) | One merge / bytes limit (median) | "
            "Share > 1 | One merge / outflow limit (median) | Share > 1 |", "|---|---|---|---|---|---|---|---|"]
    for g in ("busy (W3-W5)", "calm (W1-W2)"):
        for b in ("L2", "L3+"):
            mg, rs, rf = t1[(g, b)]
            out.append(f"| {g} | {b} | {len(mg)} | {med(mg)} | {med(rs)} | "
                       f"{lc.pct(sum(v > 1 for v in rs), len(rs))} | {med(rf)} | {lc.pct(sum(v > 1 for v in rf), len(rf))} |")
    out += ["", "## T2: are the misses timing (the run's mean error within the limit)?", "",
            "| Trees | Level | Quantity | Windows | Misses | Timing misses |", "|---|---|---|---|---|---|"]
    for g in ("busy (W3-W5)", "calm (W1-W2)"):
        for b in DEEP:
            for q in ("size", "f", "x"):
                n, tm, c = t2[(g, b, q)]
                if c:
                    out.append(f"| {g} | {b} | {lc.QNAME[q]} | {c} | {lc.pct(n, c)} | {lc.pct(tm, n)} |")
    out += ["", "## T3: are the window patterns in phase? (median correlation, straight line removed)", "",
            "| Trees | Level | Quantity | Original repeats | Rerun twin vs original | Step vs twin of record |",
            "|---|---|---|---|---|---|"]
    for g in ("busy (W3-W5)", "calm (W1-W2)"):
        for b in DEEP:
            for q in ("size", "f", "x"):
                a, r, sv = t3[(g, b, q)]
                if a or sv:
                    out.append(f"| {g} | {b} | {lc.QNAME[q]} | {med(a)} | {med(r)} | {med(sv)} |")
    out += ["", "## T4: what a perfect law could reach window by window (forecast 0, no change)", "",
            "| Trees | Level | Quantity | Original vs twin of record | Original r1 vs r2 (control) |",
            "|---|---|---|---|---|"]
    for g in ("busy (W3-W5)", "calm (W1-W2)"):
        for b in DEEP:
            for q in ("size", "f", "x"):
                h, n, hc, nc = t4[(g, b, q)]
                if n:
                    out.append(f"| {g} | {b} | {lc.QNAME[q]} | {lc.pct(h, n)} ({n}) | {lc.pct(hc, nc)} ({nc}) |")
    print("\n".join(out))
    print("BIGMERGE_EXIT=0")


if __name__ == "__main__":
    main()
