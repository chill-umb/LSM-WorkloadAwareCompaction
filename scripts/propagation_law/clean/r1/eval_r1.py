#!/usr/bin/env python3
"""r1: the three forward fixes together (AFTER SEEING; operator 2026-10-27, owner "go, adopt all three and run the
combined evaluation"). Scored like clean/dev_forward.py's F5M: inputs from the last 5M ops before X, D-33 measure,
limits and allowances as round 18, window by window k = 1..6, per workload, against 0.9 x the no-change ceiling
(clean/ceiling_fast.py's count, recomputed here).

Columns: off (all fixes off), om5, stor, brake (each alone), r1 (all three).
Self-checks, printed first:
  S1 off equals clean/forward.py's interval_preds in every window (exact dict equality), and path_checks equal;
  S2 off's hit rates equal dev_forward_byw.log's F5M rows;
  S3 each fix alone equals its subagent's own log (om5: sub_om5/compare_final.log treetwin2; stor:
     sub_stor/eval_full.log; brake: sub_brake/report_g7c.log p2);
  S4 the recomputed ceiling equals clean/ceiling_fast.log.
Usage: eval_r1.py DEV_ROOT ROWS_OUT   (prints markdown; R1_EXIT=0). Processes: R1_NPROC (default 8)."""
import json
import os
import re
import sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLEAN = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(CLEAN))
sys.path.insert(0, "/home/cc/node_ops/diag/kcase/learncheck")
import forward_r1 as fr  # noqa: E402
import forward as fw0  # noqa: E402  (round 18's forward module, for S1)
import learncheck as lc  # noqa: E402
from fwd_diag import c03_of  # noqa: E402

m = fr.m
KC = m.KC
WS = lc.WS
QS = ("2", "3", "4", "5", "5b")
VARS = {"off": (False, False, False), "om5": (True, False, False), "stor": (False, True, False),
        "brake": (False, False, True), "r1": (True, True, True)}
DEV = None


def load(p):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else None


def one_run(a):
    w, t, l1, k2 = a
    pd = DEV / "pre5M"
    lab = f"{w}-T{t}-l1{l1}"
    name = f"{lab}-K{k2}"
    step = load(KC / f"data/steps/{name}.json")
    pw = load(pd / "windows_iv" / f"{name}.json")
    if step is None or pw is None:
        return None
    s = m.KeepNoise(KC / "data/twins_rec", lab)
    W, PW = step["windows"], pw["windows"]
    F00 = W[0]["flush_bytes"] / W[0]["flushes"]
    pc = load(pd / "cycles_iv" / f"{name}.json")
    if pc:
        for k, c in enumerate(pc["windows"]):
            c["_median_files"] = fr.median_files(pc, k)
    cyc = pc["windows"][1] if pc else None
    rs = fr.own_rs(pc["windows"][:2]) if pc else None
    c03 = c03_of(pd / "c03_iv" / f"{name}.json", 1) if m.L0A == "3" else None
    F0x = PW[1]["flush_bytes"] / PW[1]["flushes"] if PW[1]["flushes"] else F00
    sched = fr.make_schedule(m.EVENTS / f"{name}.json", W[1]["lo"], PW[1]["levels"][0]["files_end"], F0x,
                             W[-1]["hi"] + 1)
    pargs = (PW[1], cyc, rs, l1, t, m.K0, k2, pd / "w0s_iv" / f"{name}.json", pd / "snap_iv" / f"{name}.json",
             step.get("first_l0_after_x"), W[1]["l0_peak"])
    pc1, _ = fr.path_checks(*pargs)
    pc0, _ = fw0.path_checks(*pargs)
    d8a = pc1.get("8a", {}).get("pred")
    X = W[1]["lo"]
    out = dict(run=name, w=w, t=t, l1=l1, k2=k2, path={q: pc1[q].get("hit") for q in ("7", "8a", "8b")},
               path_same=(pc1 == pc0), wins=[])
    caches = {v: {} for v in VARS}
    for k in range(1, len(W)):
        lo, hi, ops = W[k]["lo"], W[k]["hi"], W[k]["ops"]
        base, _ = fw0.interval_preds(PW[1], PW[0], cyc, rs, c03, l1, t, m.K0, k2, sched, lo, hi, ops, F0x)
        preds = {}
        for v, (o, st, b) in VARS.items():
            preds[v], _ = fr.interval_preds_r1(PW[1], PW[0], cyc, rs, c03, l1, t, m.K0, k2, sched, lo, hi, ops,
                                               F0x, X, d8a, caches[v], om5=o, stor=st, brk=b)
        row = dict(k=k, same=(base == preds["off"]), busy=(preds["off"] or {}).get("_busy"),
                   braked=bool((preds["r1"] or {}).get("_brake")), q={})
        for qid, q, i, extra in fr.FAST:
            meas, thr = lc.safe(m.delta, step, s.twin, q, i, k), lc.safe(s.thr, q, i)
            if meas is None or thr is None:
                continue
            row["q"][qid] = dict(meas=meas, lim=thr + extra,
                                 **{v: (pr or {}).get(qid) for v, pr in preds.items()})
        out["wins"].append(row)
    return out


def ceiling():
    """clean/ceiling_fast.py's count: each original repeat scored as a no-change step against the twin of record."""
    from bigmerge import load as bload
    h = defaultdict(lambda: [0, 0])
    for w in WS:
        for t in range(2, 11):
            for l1 in (64, 128, 256):
                lab = f"{w}-T{t}-l1{l1}"
                rec = bload(KC / f"data/twins_rec/{lab}-r1.json")
                orig = [bload(KC / f"data/twins/{lab}-r{r}.json") for r in (1, 2, 3)]
                if rec is None or None in orig:
                    continue
                s = m.KeepNoise(KC / "data/twins_rec", lab)
                for qid, q, i, extra in fr.FAST:
                    thr = lc.safe(s.thr, q, i)
                    if thr is None:
                        continue
                    for k in range(1, 7):
                        for o in orig:
                            d = lc.safe(m.delta, o, rec, q, i, k)
                            if d is not None:
                                h[(qid, w)][0] += abs(d) <= thr + extra
                                h[(qid, w)][1] += 1
    return h


def pct(h, n):
    return f"{100 * h / n:.0f}%" if n else "–"


def main():
    global DEV
    DEV = Path(sys.argv[1])
    rows_out = Path(sys.argv[2])
    jobs = [(w, t, l1, k2) for w in WS for t in range(2, 11) for l1 in (64, 128, 256) for k2 in m.KS[l1]]
    with Pool(min(8, int(os.environ.get("R1_NPROC", "8")))) as pool:
        res = [r for r in pool.map(one_run, jobs, chunksize=2) if r is not None]
    with open(rows_out, "w") as fh:
        for r in res:
            fh.write(json.dumps(r, default=str) + "\n")
    ceil = ceiling()

    agg = defaultdict(lambda: [0, 0])
    nwin = nsame = npath = npsame = 0
    for r in res:
        npath += 1
        npsame += r["path_same"]
        for q, h in r["path"].items():
            if h is not None:
                agg[("path", q, r["w"], 1)][0] += h
                agg[("path", q, r["w"], 1)][1] += 1
        for row in r["wins"]:
            nwin += 1
            nsame += row["same"]
            for qid, d in row["q"].items():
                for v in VARS:
                    if d.get(v) is None:
                        continue
                    hit = abs(d["meas"] - d[v]) <= d["lim"]
                    for kk in (row["k"], "all"):
                        a = agg[(v, qid, r["w"], kk)]
                        a[0] += hit
                        a[1] += 1

    def rate(v, qid, w, k):
        h, n = agg[(v, qid, w, k)]
        return 100 * h / n if n else None

    def cells(v, qid, w):
        return [pct(*agg[(v, qid, w, k)]) for k in range(1, 7)]

    L = ["# r1: the three forward fixes together (om5 + stor + brake), forward from the last 5M ops (AFTER SEEING)", ""]
    # ---- self-checks
    ok = True
    L.append("## Self-checks")
    s1 = nsame == nwin and npsame == npath
    ok &= s1
    L.append(f"- S1 all-off = forward.interval_preds: {nsame} of {nwin} windows identical; path checks identical in "
             f"{npsame} of {npath} runs: {'PASS' if s1 else 'FAIL'}")
    ref = {}
    for line in (CLEAN / "dev_forward_byw.log").read_text().splitlines():
        mm = re.match(r"\| (\S+) \| (W\d) \| F5M \| (.*) \|$", line)
        if mm:
            ref[(mm.group(1), mm.group(2))] = [c.strip() for c in mm.group(3).split("|")]
    bad = [(q, w) for q in QS for w in WS if ref.get((q, w)) != cells("off", q, w)]
    ok &= not bad
    L.append(f"- S2 off = dev_forward_byw.log F5M: {len(QS) * len(WS) - len(bad)} of {len(QS) * len(WS)} rows: "
             f"{'PASS' if not bad else 'FAIL ' + str(bad)}")
    # S3 per fix
    refs = {"om5": {}, "stor": {}, "brake": {}}
    for line in (CLEAN / "sub_om5/compare_final.log").read_text().splitlines():
        mm = re.match(r"#(\S+) (W\d): F5M \[.*?\]\s+tree \[.*?\]\s+treetwin2 \[(.*?)\]", line)
        if mm:
            refs["om5"][(mm.group(1), mm.group(2))] = [f"{int(x)}%" for x in mm.group(3).split(",")]
    for line in (CLEAN / "sub_stor/eval_full.log").read_text().splitlines():
        mm = re.match(r"\| (\S+) \| (W\d) \| stor \| (.*) \|$", line)
        if mm and mm.group(1) in QS:
            refs["stor"][(mm.group(1), mm.group(2))] = [c.strip() for c in mm.group(3).split("|")]
    for line in (CLEAN / "sub_brake/report_g7c.log").read_text().splitlines():
        mm = re.match(r"\| (\S+) \| (W\d) \| p2 \| (.*) \|$", line)
        if mm and mm.group(1) in QS:
            refs["brake"][(mm.group(1), mm.group(2))] = [c.strip() for c in mm.group(3).split("|")][:6]
    for v, rr in refs.items():
        badv = [(q, w, cells(v, q, w), want) for (q, w), want in rr.items() if cells(v, q, w) != want]
        ok &= not badv and bool(rr)
        L.append(f"- S3 {v} alone = its subagent's log: {len(rr) - len(badv)} of {len(rr)} rows: "
                 f"{'PASS' if not badv and rr else 'FAIL'}")
        for b in badv[:6]:
            L.append(f"  - {b[0]} {b[1]}: here {b[2]}, log {b[3]}")
    cref = {}
    lines = (CLEAN / "ceiling_fast.log").read_text().splitlines()
    for line in lines:
        mm = re.match(r"\| (\S+) \| (.*) \|$", line)
        if mm and mm.group(1) in QS:
            cref[mm.group(1)] = [c.strip() for c in mm.group(2).split("|")]
    badc = [q for q in QS if cref.get(q) != [lc.pct(*ceil[(q, w)]) for w in WS]]
    ok &= not badc
    L.append(f"- S4 ceiling = ceiling_fast.log: {'PASS' if not badc else 'FAIL ' + str(badc)}")
    L.append("")

    def bar(q, w):
        h, n = ceil[(q, w)]
        return 0.9 * 100 * h / n if n else None

    def verdict(v, q, w, k):
        x, b = rate(v, q, w, k), bar(q, w)
        if x is None or b is None:
            return "–"
        return "ok" if x >= b - 1e-9 else "FAIL"

    # ---- window 1
    L += ["## Window 1 (first 5M ops after the change), hits %", "",
          "| # | W | F5M | om5 | stor | brake | r1 | bar (0.9 x ceiling) | r1 verdict |",
          "|---|---|---|---|---|---|---|---|---|"]
    for q in QS:
        for w in WS:
            b = bar(q, w)
            L.append(f"| {q} | {w} | " + " | ".join(pct(*agg[(v, q, w, 1)]) for v in VARS)
                     + f" | {b:.0f} | {verdict('r1', q, w, 1)} |")
    # ---- windows 1-6
    L += ["", "## Windows 1-6, F5M -> r1 (hits %), and r1 pooled over windows 1-6 against the bar", "",
          "| # | W | " + " | ".join(f"w{k}" for k in range(1, 7)) + " | pooled r1 | bar | windows passing |",
          "|---|---|" + "---|" * 9]
    for q in QS:
        for w in WS:
            cs = []
            npass = 0
            for k in range(1, 7):
                a, b_ = pct(*agg[("off", q, w, k)]), pct(*agg[("r1", q, w, k)])
                vd = verdict("r1", q, w, k)
                npass += vd == "ok"
                cs.append(f"{a} → {b_}" + ("" if vd == "ok" else " ✗"))
            L.append(f"| {q} | {w} | " + " | ".join(cs)
                     + f" | {pct(*agg[('r1', q, w, 'all')])} | {bar(q, w):.0f} | {npass}/6 |")
    # ---- interactions
    L += ["", "## Do the fixes get in each other's way? Cells where r1 is more than 3 points below the best fix alone", ""]
    worse = []
    for q in QS:
        for w in WS:
            for k in list(range(1, 7)):
                best_v, best = None, -1
                for v in ("om5", "stor", "brake"):
                    x = rate(v, q, w, k)
                    if x is not None and x > best:
                        best_v, best = v, x
                x = rate("r1", q, w, k)
                if x is not None and best - x > 3:
                    worse.append(f"#{q} {w} w{k}: r1 {x:.0f}% vs {best_v} {best:.0f}%")
    L += [f"- {x}" for x in worse] or ["- none"]
    # ---- hits gained/lost
    L += ["", "## Hits gained / lost, r1 against F5M, windows 1-6", "", "| # | " + " | ".join(WS) + " |",
          "|---|" + "---|" * len(WS)]
    gl = defaultdict(lambda: [0, 0])
    for r in res:
        for row in r["wins"]:
            for q, d in row["q"].items():
                if d.get("off") is None or d.get("r1") is None:
                    continue
                h0 = abs(d["meas"] - d["off"]) <= d["lim"]
                h1 = abs(d["meas"] - d["r1"]) <= d["lim"]
                gl[(q, r["w"])][0] += h1 and not h0
                gl[(q, r["w"])][1] += h0 and not h1
    for q in QS:
        L.append(f"| {q} | " + " | ".join(f"+{gl[(q, w)][0]}/−{gl[(q, w)][1]}" for w in WS) + " |")
    # ---- path checks
    L += ["", "## Path checks (no fix touches them)", "", "| # | " + " | ".join(WS) + " |", "|---|" + "---|" * len(WS)]
    for q in ("7", "8a", "8b"):
        L.append(f"| {q} | " + " | ".join(pct(*agg[("path", q, w, 1)]) for w in WS) + " |")
    L += ["", f"Self-checks: {'ALL PASS' if ok else 'FAILED'}", f"Rows: {rows_out}"]
    print("\n".join(L))
    print(f"R1_EXIT={0 if ok else 3}")


if __name__ == "__main__":
    main()
