#!/usr/bin/env python3
"""Score the K batch's single-step runs against predictions_v1.json (cases 1-2, frozen model v1), under the test
plan ~/node_ops/drafts/2026-10-22-case12-test-plan.md and the owner's rulings of 2026-10-22 (D-32 §6's settled rule;
the hit rule). Operator, 2026-10-22, written before any K-step run was measured.

Change measure (D-33 §2): Δ_q(w) = (step in w − step in the pre-change window) − (twin in w − twin in it).
Noise threshold (D-33 §2): 3·sqrt(2)·sqrt(mean over post windows of the variance, across the twin's 3 repeats, of
(repeat in w − repeat in the pre window)).
Settle point (D-32 §6): the first post window from which, at every level and in every later window, the mean bytes'
Δ is within its threshold of the last window's Δ. The last window -> "not settled": settled quantities are n/a.
Hit: |mean measured Δ over the settled windows − predicted Δ| <= threshold + the model's stated error (one file per
level, A6: 1 L0 file, or one 4 MiB target file of bytes; 10% on times, A8).
Pass: hits in >= 90% of the scored runs, in every workload separately.
Rows 2s and 3s (added before any step run was measured, after a smoke test on twin repeats): the same with no
one-file allowance, because for small K changes the allowance exceeds the predicted change itself. Reported beside
the approved rule, not instead of it.

Usage: score_k_v1.py PREDICTIONS.json TWINS_DIR STEPS_DIR OUT_DIR
"""
import json
import math
import statistics as st
import sys
from pathlib import Path

FILE_BYTES = 4 * (1 << 20)
WINDOW = 5_000_000
CAP_PAIRS = {64: ("K8", "K5"), 128: ("K12", "K9")}
# (id, name, layer, part)
QUANTITIES = [
    ("1", "above the cap = the cap", "level", "settled"),
    ("2", "mean L0 files", "level", "settled"),
    ("2s", "mean L0 files, noise only (no one-file allowance)", "level", "settled"),
    ("3", "files per L0->L1 merge", "level", "settled"),
    ("3s", "files per L0->L1 merge, noise only", "level", "settled"),
    ("4", "L0->L1 bytes written per op", "level", "settled"),
    ("5", "L1->L2 overlap (per byte pushed)", "level", "settled"),
    ("5b", "L1->L2 bytes written per op", "level", "settled"),
    ("5c", "L1 mean bytes", "level", "settled"),
    ("6", "L2 and below unchanged (bytes, writes, overlap)", "level", "settled"),
    ("7", "L0 peak under 20 files", "level", "settled"),
    ("8a", "first L0 merge after the change: when", "level", "path"),
    ("8b", "first L0 merge after the change: files", "level", "path"),
    ("9", "L0 settled within one L0 cycle", "level", "path"),
    ("10", "flows out of L1 and deeper unchanged", "key range", "settled"),
    ("11", "L0 checks per Get", "key range", "settled"),
]


def qv(win, q, i=0):
    if q == "size":
        return win["levels"][i]["bytes_mean"]
    if q == "l0_files":
        return win["levels"][0]["files_mean"]
    if q == "files_in":
        return win["l0l1"]["files_in_mean"]
    if q == "x":
        return win["levels"][i]["x_per_op"]
    if q == "omega":
        return win["levels"][i]["omega_pushed"]
    if q == "f":
        return win["levels"][i]["f_per_op"]
    if q == "ppg":
        return win["reads"]["probes_per_get"] if win["reads"] else None
    raise KeyError(q)


def delta(step, twin, q, i, w):
    vals = (qv(step["windows"][w], q, i), qv(step["windows"][0], q, i),
            qv(twin["windows"][w], q, i), qv(twin["windows"][0], q, i))
    if None in vals:
        return None
    a, b, c, d = vals
    return (a - b) - (c - d)


def threshold(reps, q, i):
    nwin = len(reps[0]["windows"])
    vs = []
    for w in range(1, nwin):
        d = []
        for r in reps:
            a, b = qv(r["windows"][w], q, i), qv(r["windows"][0], q, i)
            if a is None or b is None:
                return None
            d.append(a - b)
        vs.append(st.variance(d))
    return 3 * math.sqrt(2) * math.sqrt(sum(vs) / len(vs))


def within(x, thr):
    return x == 0 or (thr is not None and abs(x) < thr)


class Setting:
    def __init__(self, twins_dir, lab):
        self.reps = [json.loads((twins_dir / f"{lab}-r{r}.json").read_text()) for r in (1, 2, 3)]
        self.twin = self.reps[0]
        self._thr = {}

    def thr(self, q, i=0):
        if (q, i) not in self._thr:
            self._thr[(q, i)] = threshold(self.reps, q, i)
        return self._thr[(q, i)]


def settle_point(step, s):
    last = len(step["windows"]) - 1
    deepest = step["windows"][last]["deepest"]
    for w in range(1, last + 1):
        ok = True
        for i in range(deepest + 1):
            thr = s.thr("size", i)
            if thr is None:
                continue
            dl = delta(step, s.twin, "size", i, last)
            for k in range(w, last + 1):
                dk = delta(step, s.twin, "size", i, k)
                if dk is None or dl is None or not within(dk - dl, thr):
                    ok = False
                    break
            if not ok:
                break
        if ok:
            return w
    return last


def settled_mean(step, s, q, i, sp):
    last = len(step["windows"]) - 1
    vals = [delta(step, s.twin, q, i, w) for w in range(sp, last + 1)]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def hit(meas, pred, thr, extra=0.0):
    if meas is None or pred is None or thr is None:
        return None
    return abs(meas - pred) <= thr + extra


def score_run(step, s, pr):
    """{qid: dict(hit=True/False/None, ...)} for one step run."""
    last = len(step["windows"]) - 1
    deepest = step["windows"][last]["deepest"]
    sp = settle_point(step, s)
    settled = sp < last
    st_ = pr["settled"]
    out = dict(_settle=sp, _settled=settled, _ok=step["ok"])

    def one(qid, q, i, pred, extra=0.0):
        if not settled:
            out[qid] = dict(hit=None, why="not settled")
            return
        m, t = settled_mean(step, s, q, i, sp), s.thr(q, i)
        out[qid] = dict(hit=hit(m, pred, t, extra), meas=m, pred=pred, thr=t)

    one("2", "l0_files", 0, st_["l0_files"], 1.0)
    one("2s", "l0_files", 0, st_["l0_files"])
    one("3", "files_in", 0, st_["l0l1_files_in"], 1.0)
    one("3s", "files_in", 0, st_["l0l1_files_in"])
    one("4", "x", 0, st_["w0"])
    one("5", "omega", 1, st_["omega1"])
    one("5b", "x", 1, st_["w1"])
    one("5c", "size", 1, st_["l1_bytes"], FILE_BYTES)
    one("11", "ppg", 0, st_["probes_per_get"])

    # 6: every level 2..deepest: bytes (+ one file), bytes written per op, overlap; predicted 0
    if not settled:
        out["6"] = dict(hit=None, why="not settled")
        out["10"] = dict(hit=None, why="not settled")
    else:
        misses, scored = [], 0
        for i in range(2, deepest + 1):
            for q, extra in (("size", FILE_BYTES), ("x", 0.0), ("omega", 0.0)):
                if q != "size" and i == deepest:
                    continue
                h = hit(settled_mean(step, s, q, i, sp), 0.0, s.thr(q, i), extra)
                if h is None:
                    continue
                scored += 1
                if not h:
                    misses.append(f"{q}{i}")
        out["6"] = dict(hit=(not misses) if scored else None, misses=misses)
        misses, scored = [], 0
        for i in range(1, deepest):
            h = hit(settled_mean(step, s, "f", i, sp), 0.0, s.thr("f", i))
            if h is None:
                continue
            scored += 1
            if not h:
                misses.append(f"f{i}")
        out["10"] = dict(hit=(not misses) if scored else None, misses=misses)

    # 7: G9
    peak = max(step["windows"][w]["l0_peak"] for w in range(1, last + 1))
    if pr["g9"]["predicts_no_stall"]:
        out["7"] = dict(hit=peak <= 19, meas=peak, pred_peak=pr["g9"]["peak"])
    else:
        out["7"] = dict(hit=None, why="G9 condition fails", meas=peak, pred_peak=pr["g9"]["peak"])

    # 8: T-K
    n_x = step["windows"][0]["levels"][0]["files_end"]
    fl = pr["first_l0"]
    d = max(0, fl["K_e_new"] - n_x) * fl["t_F"]
    files_pred = max(n_x, fl["K_e_new"])
    m = step.get("first_l0_after_x")
    if m is None:
        out["8a"] = dict(hit=None, why="no L0 merge after X")
        out["8b"] = dict(hit=None, why="no L0 merge after X")
    else:
        out["8a"] = dict(hit=abs(m["after_x"] - d) <= fl["t_F"] + fl["tau_job"] + 0.1 * d,
                         meas=m["after_x"], pred=d, n_x=n_x)
        out["8b"] = dict(hit=abs(m["files_in"] - files_pred) <= 1, meas=m["files_in"], pred=files_pred)

    # 9: L0 within one cycle: window 1's Δ against the settled Δ
    if not settled:
        out["9"] = dict(hit=None, why="not settled")
    else:
        d1 = delta(step, s.twin, "l0_files", 0, 1)
        sm = settled_mean(step, s, "l0_files", 0, sp)
        t = s.thr("l0_files", 0)
        allow = abs(st_["l0_files"]) * min(1.0, pr["l0_cycle"] / WINDOW)
        out["9"] = dict(hit=hit(d1, sm, t, allow), meas=d1, settled=sm)
    return out


def cap_pair(a, b, sa, sb, s):
    """#1: two runs predicted equal (above the cap and at it): every quantity's settled Δ within noise."""
    last = len(a["windows"]) - 1
    if sa >= last or sb >= last:
        return dict(hit=None, why="not settled")
    sp = max(sa, sb)
    deepest = min(a["windows"][last]["deepest"], b["windows"][last]["deepest"])
    checks = [("l0_files", 0, 1.0), ("files_in", 0, 1.0), ("x", 0, 0.0), ("omega", 1, 0.0), ("x", 1, 0.0),
              ("ppg", 0, 0.0)] + [("size", i, FILE_BYTES) for i in range(deepest + 1)]
    misses = []
    for q, i, extra in checks:
        h = hit(settled_mean(a, s, q, i, sp), settled_mean(b, s, q, i, sp), s.thr(q, i), extra)
        if h is False:
            misses.append(f"{q}{i}")
    return dict(hit=not misses, misses=misses)


def main():
    preds = json.loads(Path(sys.argv[1]).read_text())
    tdir, sdir, odir = Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    odir.mkdir(parents=True, exist_ok=True)
    rows, missing = [], []
    for lab, sett in sorted(preds["settings"].items()):
        s = Setting(tdir, lab)
        w = lab.split("-")[0]
        l1 = int(lab.split("-l1")[1])
        runs = {}
        for k, pr in sett["steps"].items():
            p = sdir / f"{lab}-{k}.json"
            if not p.exists():
                missing.append(f"{lab}-{k}")
                continue
            step = json.loads(p.read_text())
            res = score_run(step, s, pr)
            runs[k] = (step, res)
            rows.append(dict(run=f"{lab}-{k}", w=w, l1=l1, K=pr["K"], up=pr["K"] > 4, res=res))
        if l1 in CAP_PAIRS and all(k in runs for k in CAP_PAIRS[l1]):
            ka, kb = CAP_PAIRS[l1]
            res = cap_pair(runs[ka][0], runs[kb][0], runs[ka][1]["_settle"], runs[kb][1]["_settle"], s)
            rows.append(dict(run=f"{lab}-{ka}={kb}", w=w, l1=l1, K=int(ka[1:]), up=True, res={"1": res}))
    (odir / "runs.json").write_text(json.dumps(rows, indent=1, default=str))

    ws = ("W1", "W2", "W3", "W4", "W5")
    lines = ["# Cases 1-2 test of the frozen model v1: results", "",
             f"Runs scored: {sum(1 for r in rows if '=' not in r['run'])}; missing: {len(missing)}; "
             f"not settled: {sum(1 for r in rows if '=' not in r['run'] and not r['res']['_settled'])}", "",
             "| # | Quantity | Layer | Part | " + " | ".join(ws) + " | K up | K down | Pass |",
             "|---|---|---|---|" + "---|" * (len(ws) + 3)]
    summary = {}
    for qid, name, layer, part in QUANTITIES:
        cells, ok = [], True
        for w in ws:
            sc = [r["res"][qid]["hit"] for r in rows if r["w"] == w and qid in r["res"]
                  and r["res"][qid]["hit"] is not None]
            if sc:
                frac = sum(sc) / len(sc)
                cells.append(f"{100 * frac:.0f}% ({len(sc)})")
                ok = ok and frac >= 0.9
            else:
                cells.append("–")
        ud = []
        for up in (True, False):
            sc = [r["res"][qid]["hit"] for r in rows if r["up"] == up and qid in r["res"]
                  and r["res"][qid]["hit"] is not None]
            ud.append(f"{100 * sum(sc) / len(sc):.0f}% ({len(sc)})" if sc else "–")
        any_scored = any(c != "–" for c in cells)
        verdict = ("PASS" if ok else "FAIL") if any_scored else "n/a"
        summary[qid] = verdict
        lines.append(f"| {qid} | {name} | {layer} | {part} | " + " | ".join(cells) + f" | {ud[0]} | {ud[1]} | "
                     f"{verdict} |")
    (odir / "summary.md").write_text("\n".join(lines) + "\n")
    (odir / "summary.json").write_text(json.dumps(dict(summary=summary, missing=missing), indent=1))
    print("\n".join(lines))
    if missing:
        print("missing:", missing[:20])


if __name__ == "__main__":
    main()
