#!/usr/bin/env python3
"""Are the propagation law's errors learnable by a deep-level agent? (owner's design, 2026-10-27; plan in the doc
"Deep-level learning from the propagation law", section "First check"). Operator, 2026-10-27, written before any
result was seen.

Data: the K batch's single-change runs (cases 1-2), the law at round 18 (v2/score_k_v2r18.py via score_dump.py),
window by window after the change (windows 1-6 of 5M ops; window 0 is before X).

Residual of a quantity q at level i in window k: r = measured change - the law's forecast change.
- D-33 measure (the scorer's): measured = (step_k - step_0) - (twin_k - twin_0); limit = the scorer's (thr + allowance).
- plain measure: measured = step_k - twin_k; limit = 3 sqrt2 x the 3 original repeats' spread of the value itself.
A window "hits" when |r| <= limit. The twin stands in for the no-action forecast the law does not have yet.

Tests (all on the same late windows k = 4..6 of each run that has an early window too):
1. Within a run (online, per-run offset): correction = the mean D-33 residual over the run's early windows (1..3);
   corrected late residual = r_k - correction (the run's window-0 term cancels, so this is the same on both measures).
   Also the online running mean (r_k - mean of r_1..r_k-1, k >= 2) for reference.
2. Across runs (pre-trained): leave one run out. Correction = the mean residual, in units of its own limit, of the
   other runs in the same group, x this run's limit; plain measure (the window-0 gap is random per run). Groups:
   (workload, quantity, level band, K up or down) as the plan says, and the same without the workload (general).

Verdict per quantity x level band, set before seeing any result:
- "law fine": the law already hits >= 90% of late windows (D-33).
- "learnable in a run": among late windows the law misses, the early correction brings at least half within the
  limit, and the corrected hit rate is >= 90%.
- "learnable across runs": the same with the leave-one-run-out correction (workload groups; general noted beside).
- "partly learnable": a correction brings at least half the misses within the limit but stays under 90%.
- "not learnable": neither correction brings half the misses within the limit.
Reads are logged per phase, not per window: not in this check.

Usage: learncheck.py OUT_DIR   (twins, steps: data/twins_rec, data/steps, as round 18)
"""
import json
import math
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import score_dump as m  # noqa: E402
from score_k_v1 import FILE_BYTES, delta, qv  # noqa: E402

KC = Path("/home/cc/node_ops/diag/kcase")
EARLY, LATE = (1, 2, 3), (4, 5, 6)
WS = ("W1", "W2", "W3", "W4", "W5")
QNAME = {"l0_files": "L0 files", "files_in": "files per L0 merge", "x": "bytes written/op", "omega": "overlap",
         "size": "level bytes", "f": "outflow/op"}


def band(i, deepest):
    if i == 0:
        return "L0"
    if i == 1:
        return "L1"
    if i == deepest:
        return "last"
    return "L2" if i == 2 else "L3+"


def safe(f, *a):
    try:
        return f(*a)
    except (IndexError, KeyError, TypeError, ZeroDivisionError, st.StatisticsError):
        return None


def thr_plain(s, q, i, cache):
    if (q, i) not in cache:
        reps = s._old.reps
        vs = []
        for w in range(1, len(reps[0]["windows"])):
            vals = [safe(qv, r["windows"][w], q, i) for r in reps]
            if None in vals:
                vs = None
                break
            vs.append(st.variance(vals))
        cache[(q, i)] = 3 * math.sqrt(2) * math.sqrt(sum(vs) / len(vs)) if vs else None
    return cache[(q, i)]


def plain(step, twin, q, i, k):
    a, b = safe(qv, step["windows"][k], q, i), safe(qv, twin["windows"][k], q, i)
    return None if a is None or b is None else a - b


def cells_of(rec, step, s, store, pcache):
    """Every (q, i) the scorer scores, with this window's forecast, both measures and both limits."""
    last = len(step["windows"]) - 1
    deepest = step["windows"][last]["deepest"]
    k = rec["k"]
    qs = [("l0_files", 0, rec["dn0"], 1.0), ("files_in", 0, rec["dbatch"], 1.0), ("x", 0, rec["dw0"], 0.0),
          ("omega", 1, rec["dom"].get(1), 0.0), ("x", 1, rec["dx"].get(1), 0.0)]
    for i in range(2, deepest + 1):
        qs.append(("size", i, rec["dsize"].get(i, 0.0), FILE_BYTES))
        if i < deepest:
            qs += [("x", i, rec["dx"].get(i, 0.0), 0.0), ("omega", i, rec["dom"].get(i, 0.0), 0.0)]
    sp = rec["sp"]
    for i in range(1, deepest):
        pr = rec["df"].get(i, 0.0)
        if store and rec["settled"] and k >= sp:            # round 9's storage term, as the scorer applies it
            tf = [step["windows"][j]["levels"][i]["f_per_op"] or 0.0 for j in range(sp, last + 1)
                  if i < len(step["windows"][j]["levels"])]
            if tf and sum(tf) / len(tf) > 0:
                pr += store
        qs.append(("f", i, pr, 0.0))
    out = []
    for q, i, pr, extra in qs:
        if pr is None:
            continue
        d33, pl = safe(delta, step, s.twin, q, i, k), plain(step, s.twin, q, i, k)
        thr, thp = safe(s.thr, q, i), thr_plain(s, q, i, pcache)
        if d33 is None or thr is None:
            continue
        out.append(dict(q=q, i=i, band=band(i, deepest), k=k, pred=pr, r=d33 - pr,
                        rp=None if pl is None or thp is None else pl - pr,
                        lim=thr + extra, limp=None if thp is None else thp + extra, busy=rec["busy"]))
    return out


def collect():
    tdir, sdir = KC / "data/twins_rec", KC / "data/steps"
    rows, cells = [], []
    for w in WS:
        for t in range(2, 11):
            for l1 in (64, 128, 256):
                lab = f"{w}-T{t}-l1{l1}"
                s = m.KeepNoise(tdir, lab)
                pcache = {}
                runs = {}
                for k2 in m.KS[l1]:
                    p = sdir / f"{lab}-K{k2}.json"
                    if not p.exists():
                        continue
                    step = json.loads(p.read_text())
                    n0 = len(m.DUMP)
                    res = m.score_run(step, s, l1, t, k2, None)
                    runs[f"K{k2}"] = (step, res)
                    run = f"{lab}-K{k2}"
                    rows.append(dict(run=run, w=w, l1=l1, K=k2, up=k2 > m.K0, res=res))
                    store = m.STORE.get(step["run"], 0.0)
                    for rec in m.DUMP[n0:]:
                        for c in cells_of(rec, step, s, store, pcache):
                            c.update(run=run, w=w, T=t, l1=l1, K=k2, up=k2 > m.K0, sp=res["_settle"],
                                     settled=res["_settled"])
                            cells.append(c)
                if l1 in m.CAP_PAIRS and all(k in runs for k in m.CAP_PAIRS[l1]):
                    ka, kb = m.CAP_PAIRS[l1]
                    res = m.cap_pair(runs[ka][0], runs[kb][0], runs[ka][1]["_settle"], runs[kb][1]["_settle"], s)
                    rows.append(dict(run=f"{lab}-{ka}={kb}", w=w, l1=l1, K=int(ka[1:]), up=True, res={"1": res}))
    return rows, cells


def pct(n, d):
    return f"{100 * n / d:.0f}%" if d else "–"


def analyse(cells):
    series = defaultdict(dict)                 # (run, q, i) -> {k: cell}
    for c in cells:
        series[(c["run"], c["q"], c["i"])][c["k"]] = c
    late = []                                  # late cells with their corrections
    for key, ser in series.items():
        early = [ser[k]["r"] for k in EARLY if k in ser]
        for k in LATE:
            if k not in ser or not early:
                continue
            c = ser[k]
            past = [ser[j]["r"] for j in range(1, k) if j in ser]
            c = dict(c, corr_in=c["r"] - sum(early) / len(early),
                     corr_on=c["r"] - sum(past) / len(past))
            late.append(c)
    # leave one run out, residual in units of its own limit (plain measure)
    for gkey in ("lw", "lg"):
        sums = defaultdict(lambda: [0.0, 0])
        per_run = defaultdict(lambda: [0.0, 0])
        for c in cells:
            if c["rp"] is None or not c["limp"]:
                continue
            g = (c["q"], c["band"], c["up"]) + ((c["w"],) if gkey == "lw" else ())
            z = c["rp"] / c["limp"]
            sums[g][0] += z
            sums[g][1] += 1
            per_run[(g, c["run"])][0] += z
            per_run[(g, c["run"])][1] += 1
        for c in late:
            if c["rp"] is None or not c["limp"]:
                c[gkey] = None
                continue
            g = (c["q"], c["band"], c["up"]) + ((c["w"],) if gkey == "lw" else ())
            a, n = sums[g]
            b, nb = per_run[(g, c["run"])]
            c[gkey] = c["rp"] - c["limp"] * (a - b) / (n - nb) if n > nb else None
    return late


def stats(cs):
    """Counts over a set of late cells."""
    n = len(cs)
    raw = sum(abs(c["r"]) <= c["lim"] for c in cs)
    pl = [c for c in cs if c["rp"] is not None and c["limp"]]
    rawp = sum(abs(c["rp"]) <= c["limp"] for c in pl)
    miss = [c for c in cs if abs(c["r"]) > c["lim"]]
    fix_in = sum(abs(c["corr_in"]) <= c["lim"] for c in miss)
    hit_in = sum(abs(c["corr_in"]) <= c["lim"] for c in cs)
    hit_on = sum(abs(c["corr_on"]) <= c["lim"] for c in cs)
    lw = [c for c in pl if c["lw"] is not None]
    lg = [c for c in pl if c["lg"] is not None]
    missp_w = [c for c in lw if abs(c["rp"]) > c["limp"]]
    missp_g = [c for c in lg if abs(c["rp"]) > c["limp"]]
    return dict(n=n, raw=raw, np=len(pl), rawp=rawp, miss=len(miss), fix_in=fix_in, hit_in=hit_in, hit_on=hit_on,
                nlw=len(lw), hit_lw=sum(abs(c["lw"]) <= c["limp"] for c in lw), miss_lw=len(missp_w),
                fix_lw=sum(abs(c["lw"]) <= c["limp"] for c in missp_w),
                nlg=len(lg), hit_lg=sum(abs(c["lg"]) <= c["limp"] for c in lg), miss_lg=len(missp_g),
                fix_lg=sum(abs(c["lg"]) <= c["limp"] for c in missp_g),
                left=st.median([abs(c["corr_in"]) / c["lim"] for c in miss if c["lim"] > 0])
                if any(c["lim"] > 0 for c in miss) else None)


def verdict(x):
    if not x["n"]:
        return "–"
    if x["raw"] >= 0.9 * x["n"]:
        return "law fine"
    if x["miss"] and x["fix_in"] >= 0.5 * x["miss"] and x["hit_in"] >= 0.9 * x["n"]:
        return "learnable in a run"
    if x["miss_lw"] and x["fix_lw"] >= 0.5 * x["miss_lw"] and x["hit_lw"] >= 0.9 * x["nlw"]:
        return "learnable across runs"
    if (x["miss"] and x["fix_in"] >= 0.5 * x["miss"]) or (x["miss_lw"] and x["fix_lw"] >= 0.5 * x["miss_lw"]):
        return "partly learnable"
    return "not learnable"


def main():
    odir = Path(sys.argv[1])
    odir.mkdir(parents=True, exist_ok=True)
    rows, cells = collect()
    ref = json.loads((KC / "v2/out_r18/runs.json").read_text())
    same = json.loads(json.dumps(rows, default=str)) == ref
    late = analyse(cells)
    (odir / "cells.json").write_text(json.dumps(late, default=str))
    keys = sorted({(c["q"], c["band"]) for c in late},
                  key=lambda x: (["L0", "L1", "L2", "L3+", "last"].index(x[1]), list(QNAME).index(x[0])))
    L = ["# Are the law's errors learnable? (round 18, cases 1-2)", "",
         f"Self-check, scores identical to v2/out_r18/runs.json: {'yes' if same else 'NO'}; "
         f"forecast failures: {len(m.DUMP_FAIL)}; late cells: {len(late)} from "
         f"{len({c['run'] for c in late})} runs.", "",
         "Late windows (4-6). Raw = the law as it stands; in-run = minus the run's early (1-3) mean error; online = "
         "minus the mean of all earlier windows; LOO = minus the other runs' mean error (same workload | general). "
         "Fixed = share of the law's misses the correction brings within the limit. Left = median corrected miss / "
         "limit.", "",
         "| Level | Quantity | Cells | Raw D-33 | Raw plain | In-run | Fixed | Left | Online | LOO wkld | Fixed | "
         "LOO general | Verdict |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    summ = {}
    for q, b in keys:
        cs = [c for c in late if c["q"] == q and c["band"] == b]
        x = stats(cs)
        v = verdict(x)
        summ[f"{b}/{q}"] = dict(x, verdict=v)
        L.append(f"| {b} | {QNAME[q]} | {x['n']} | {pct(x['raw'], x['n'])} | {pct(x['rawp'], x['np'])} | "
                 f"{pct(x['hit_in'], x['n'])} | {pct(x['fix_in'], x['miss'])} | "
                 f"{'–' if x['left'] is None else format(x['left'], '.1f')} | "
                 f"{pct(x['hit_on'], x['n'])} | {pct(x['hit_lw'], x['nlw'])} | {pct(x['fix_lw'], x['miss_lw'])} | "
                 f"{pct(x['hit_lg'], x['nlg'])} | {v} |")
    L += ["", "## By workload: raw D-33 -> in-run corrected (late windows)", "",
          "| Level | Quantity | " + " | ".join(WS) + " |", "|---|---|" + "---|" * len(WS)]
    for q, b in keys:
        row = []
        for w in WS:
            cs = [c for c in late if c["q"] == q and c["band"] == b and c["w"] == w]
            x = stats(cs) if cs else None
            row.append(f"{pct(x['raw'], x['n'])} -> {pct(x['hit_in'], x['n'])} ({x['n']})" if x else "–")
        L.append(f"| {b} | {QNAME[q]} | " + " | ".join(row) + " |")
    L += ["", "## Settled runs only (the law's own domain)", "",
          "| Level | Quantity | Cells | Raw D-33 | In-run | Fixed | LOO wkld | Verdict |", "|---|---|---|---|---|---|---|---|"]
    for q, b in keys:
        cs = [c for c in late if c["q"] == q and c["band"] == b and c["settled"] and c["k"] >= c["sp"]]
        if not cs:
            continue
        x = stats(cs)
        L.append(f"| {b} | {QNAME[q]} | {x['n']} | {pct(x['raw'], x['n'])} | {pct(x['hit_in'], x['n'])} | "
                 f"{pct(x['fix_in'], x['miss'])} | {pct(x['hit_lw'], x['nlw'])} | {verdict(x)} |")
    if m.DUMP_FAIL:
        L += ["", f"Forecast failures (first 10 of {len(m.DUMP_FAIL)}):"] + [f"- {f}" for f in m.DUMP_FAIL[:10]]
    (odir / "summary.md").write_text("\n".join(L) + "\n")
    (odir / "summary.json").write_text(json.dumps(dict(same=same, fails=len(m.DUMP_FAIL), rows=summ), indent=1,
                                                  default=str))
    print("\n".join(L))
    print(f"LEARNCHECK_EXIT={0 if same else 2}")


if __name__ == "__main__":
    main()
