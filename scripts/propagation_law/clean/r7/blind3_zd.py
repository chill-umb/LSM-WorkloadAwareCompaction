#!/usr/bin/env python3
"""Does any prediction read the future? (operator check, 2026-10-31.) For every change, predict twice with the same code: once as the
scorer does, once with everything after X removed from the inputs: next stretch reduced to its lo/hi/ops, no measured first merge, the
run's ev, c03, pour and ft records cut at X (the merge running at X keeps its start and bytes, its end set to X+1). Any difference is a read
of the future. Usage: blind3.py MULTI_ROOT TMP NPROC   (last line BLIND_EXIT=0)"""
import json, os, shutil, sys
from collections import Counter
from multiprocessing import Pool
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent), "/home/cc/node_ops/diag/kcase/learncheck"]
import forward_r7 as fk
from fwd_diag import c03_of
m = fk.m
ROOT = TMP = None
QS = ("2", "3", "4", "5", "5b", "8a", "8b")
def load(p): return json.loads(Path(p).read_text())
def cut_list(v, X):
    return [x for x in v if (x[0] if isinstance(x, (list, tuple)) else x) < X]
def blinded(name, j, X, base):
    d = base / f"{name}-{j}"
    for sub in ("ev", "c03", "pour"): (d / sub).mkdir(parents=True, exist_ok=True)
    (d / "ft").mkdir(exist_ok=True)
    ev = load(ROOT / "ev" / f"{name}.json")
    begs = cut_list(ev["flush_begins"], X)
    ev["flushes"] = sorted(ev["flushes"])[:len(begs)]          # the flush begun before X keeps its pairing
    ev["flush_begins"] = begs
    ev["l0jobs"] = [([a, min(b, X + 1)] + list(r)) for a, b, *r in ev["l0jobs"] if a < X]
    (d / "ev" / f"{name}.json").write_text(json.dumps(ev))
    c = load(ROOT / "c03" / f"{name}.json")
    ms = []
    for q in c["merges"]:
        if q["op"] >= X: continue
        if q["end_op"] >= X:
            q = dict(q, end_op=X + 1, end_t=q["t"] + 1, inside=[])
        ms.append(q)
    c["merges"] = ms
    (d / "c03" / f"{name}.json").write_text(json.dumps(c))
    pp = ROOT / "pour" / f"{name}.json"
    if pp.exists():
        po = load(pp)
        po["ends"] = {k: cut_list(v, X) for k, v in po["ends"].items()}
        (d / "pour" / f"{name}.json").write_text(json.dumps(po))
    ftp = HERE / "ft" / f"{name}.json"
    if ftp.exists():
        ft = load(ftp); ft["fb"] = cut_list(ft["fb"], X)
        (d / "ft" / f"{name}.json").write_text(json.dumps(ft))
    return d
def predict(name, W, first_by, pc, l1, t, ap, j, root, mf_on, a_lo_hi):
    p_, a = j + 1, j + 2
    X, k_to = ap[j]; k_from = 4 if j == 0 else ap[j - 1][1]
    pre, prev = W[p_], W[p_ - 1]
    lo, hi, ops = a_lo_hi
    F00 = W[0]["flush_bytes"] / W[0]["flushes"]
    F0x = pre["flush_bytes"] / pre["flushes"] if pre["flushes"] else F00
    cyc = pc["windows"][p_]; rs = fk.own_rs(pc["windows"][:p_ + 1])
    c03 = c03_of(root / "c03" / f"{name}.json", p_) if m.L0A == "3" else None
    f = first_by[a] if (mf_on and a < len(first_by)) else None
    mf = dict(after_x=f["after"], files_in=f["files_in"]) if f else None
    w0s, snap = ROOT / "w0s" / f"{name}-c{j}.json", ROOT / "snap" / f"{name}-c{j}.json"
    jf = fk.inflight_l0(root / "c03" / f"{name}.json", X)
    pc1, _ = fk.path_checks(pre, cyc, rs, l1, t, k_from, k_to, w0s, snap, mf, W[a]["l0_peak"] if mf_on else 0, inflight=jf)
    s = fk.make_schedule(root / "ev" / f"{name}.json", X, pre["levels"][0]["files_end"], F0x, W[-1]["hi"] + 1)
    pr, w = fk.interval_preds_r7(pre, prev, cyc, rs, c03, l1, t, k_from, k_to, s, lo, hi, ops, F0x, X, pc1.get("8a", {}).get("pred"), {}, pc1, snap, w0s, r7="on")
    pr = dict(pr or {})                       # sub8_order: the first merge's predictions too
    pr["8a"], pr["8b"] = pc1.get("8a", {}).get("pred"), pc1.get("8b", {}).get("pred")
    return pr
def one(name):
    meta = load(ROOT / "meta" / f"{name}.json"); Wd = load(ROOT / "win" / f"{name}.json"); W = Wd["windows"]
    pc = load(ROOT / "cyc" / f"{name}.json")
    for k, c in enumerate(pc["windows"]): c["_median_files"] = fk.median_files(pc, k)
    out = []; base = TMP / f"{os.getpid()}"
    orig = fk.__file__
    for j, (X, _) in enumerate(meta["applies"]):
        a = j + 2
        args = (name, W, Wd.get("first_l0_by_win") or [], pc, meta["l1"], meta["t"], meta["applies"], j)
        lhs = (W[a]["lo"], W[a]["hi"], W[a]["ops"])
        fk.__file__ = orig
        p1 = predict(*args, ROOT, True, lhs)
        d = blinded(name, j, X, base)
        fk.__file__ = str(d / "forward_r7.py")
        Wb = list(W); Wb[a:] = [dict(lo=x["lo"], hi=x["hi"], ops=x["ops"]) for x in W[a:]]
        args2 = (name, Wb, [], pc, meta["l1"], meta["t"], meta["applies"], j)
        p2 = predict(*args2, d, False, lhs)
        fk.__file__ = orig
        shutil.rmtree(d, ignore_errors=True)
        for q in QS:
            x, y = (p1 or {}).get(q), (p2 or {}).get(q)
            same = (x is None and y is None) or (x is not None and y is not None and abs(x - y) < 1e-9)
            out.append((meta["w"], q, same, name, j, x, y))
    return out
def main():
    global ROOT, TMP
    ROOT, TMP = Path(sys.argv[1]), Path(sys.argv[2]); TMP.mkdir(parents=True, exist_ok=True)
    with Pool(int(sys.argv[3])) as pool: res = pool.map(one, sorted(p.stem for p in (ROOT / "meta").glob("*.json")), chunksize=1)
    g = Counter(); bad = []
    for r in res:
        for w, q, same, name, j, x, y in r:
            g[(q, w, same)] += 1
            if not same: bad.append((name, j, q, x, y))
    print("| q | W | same | differ |")
    for q in QS:
        for w in ("W1", "W2", "W3", "W4", "W5"):
            print(f"| {q} | {w} | {g[(q, w, True)]} | {g[(q, w, False)]} |")
    json.dump(bad, open("blind3_bad.json", "w"))
    for b in bad[:40]: print("DIFF", b)
    print(f"total differ {len(bad)}"); print("BLIND_EXIT=0")
if __name__ == "__main__": main()
