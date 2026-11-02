#!/usr/bin/env python3
"""r7 part g, pour form (2026-10-29, owner: "replace part g's rate with Pw in the law"): each multi-change run's pour
ends into every level, the input law_r7's part g reads (forward_r7.make_schedule attaches them to the run's schedule).
RocksDB pushes a level down only while it is over its target (static targets S_i = B T^(i-1); dynamic level bytes off),
so a pour of level D-1 into D is the run of D-1 -> D jobs from the first after D-1 went over its target to the one that
leaves it under; it ends at that job's op and carries its jobs' intake into D (x - o, or s for a trivial move). Level
sizes are rebuilt from the run's host log in log order (flush: L0 += x; merge from i: L_i -= s, L_i+1 += x - o;
trivial: L_i -= s, L_i+1 += s) from the first stretch's end bytes; self-check: every later stretch's end bytes, every
level. As clean/diag5/g6pour.py. Writes MULTI_ROOT/pour/<name>.json = {"ends": {D: [[op, bytes], ...]}, "err": x}.
Usage: build_pour_r7.py MULTI_ROOT [NPROC]   (prints BUILD_POUR_EXIT=<runs with a rebuild error>)"""
import json
import sys
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(sys.argv[1])
MIB = 1 << 20


def one(name):
    meta = json.loads((ROOT / "meta" / f"{name}.json").read_text())
    W = json.loads((ROOT / "win" / f"{name}.json").read_text())["windows"]
    B, t = meta["l1"] * MIB, meta["t"]
    L = [lv["bytes_end"] for lv in W[0]["levels"]]
    cap = [0.0] + [B * t ** (i - 1) for i in range(1, len(L))]
    cur, ends, k, err = [0.0] * len(L), {}, 1, 0.0
    with open(Path(meta["run"]) / "host_log.jsonl") as fh:
        for ln in fh:
            if '"type":"flush_end"' in ln:
                r = json.loads(ln)
                op, sl, ol, rm, add = r["op"], -1, 0, 0, r["x"]
            elif '"type":"job_end"' in ln:
                r = json.loads(ln)
                if r.get("ok", 1) not in (True, 1, "true", "1"):
                    continue
                op, sl, ol, rm = r["op"], r["start_level"], r["output_level"], r["s"]
                add = r["s"] if r["trivial"] else r["x"] - r["o"]
            else:
                continue
            if op < W[0]["hi"]:
                continue
            while k < len(W) and op >= W[k]["hi"]:
                for i, lv in enumerate(W[k]["levels"]):
                    err = max(err, abs(L[i] - lv["bytes_end"]) / max(lv["bytes_end"], MIB))
                k += 1
            if sl >= 0:
                L[sl] -= rm
            L[ol] += add
            if sl >= 1 and ol == sl + 1:
                cur[ol] += add
                if L[sl] < cap[sl]:
                    ends.setdefault(str(ol), []).append([op, cur[ol]])
                    cur[ol] = 0.0
    (ROOT / "pour").mkdir(exist_ok=True)
    (ROOT / "pour" / f"{name}.json").write_text(json.dumps(dict(ends=ends, err=err)))
    return name, err


names = sorted(p.stem for p in (ROOT / "meta").glob("*.json"))
with Pool(int(sys.argv[2]) if len(sys.argv) > 2 else 12) as pool:
    res = pool.map(one, names, chunksize=1)
bad = [n for n, e in res if e > 0]
print(f"runs {len(res)}, largest rebuild error {max(e for _, e in res):.2e}, runs with an error {len(bad)}")
print(f"BUILD_POUR_EXIT={len(bad)}")
