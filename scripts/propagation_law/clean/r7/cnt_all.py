#!/usr/bin/env python3
"""Exact multi-run hit counts per check and workload from eval_r7_multi rows files (operator, 2026-10-28): #2-#5b
(|meas - r7| <= lim) and the path checks #7, #8a, #8b; then, against the first file, the changes whose verdict
flipped. Usage: cnt_all.py ROWS_A ROWS_B ..."""
import json
import sys
from collections import defaultdict

QS, PS = ("2", "3", "4", "5", "5b"), ("7", "8a", "8b")


def hits(fn):
    h = {}
    for ln in open(fn):
        r = json.loads(ln)
        if r.get("missing"):
            continue
        for c in r["changes"]:
            for q in QS:
                d = c["q"].get(q)
                if d and d["meas"] is not None and d["lim"] is not None and d.get("r7") is not None:
                    h[(r["w"], q, r["name"], c["j"])] = abs(d["meas"] - d["r7"]) <= d["lim"]
            for q in PS:
                v = c["path"].get(q)
                if v is not None:
                    h[(r["w"], q, r["name"], c["j"])] = bool(v)
    return h


H = [hits(fn) for fn in sys.argv[1:]]
print("| # | W | " + " | ".join(sys.argv[1:]) + " | bar |")
print("|---|---|" + "---|" * len(H) + "---|")
for q in QS + PS:
    for w in ("W1", "W2", "W3", "W4", "W5"):
        cells, n = [], 0
        for h in H:
            v = [x for k, x in h.items() if k[0] == w and k[1] == q]
            n = len(v)
            cells.append(f"{sum(v)}/{n}")
        print(f"| {q} | {w} | " + " | ".join(cells) + f" | {0.9 * n:.1f} |")
for i in range(1, len(H)):
    flips = defaultdict(lambda: [0, 0])
    for k, v in H[i].items():
        a = H[0].get(k)
        if a is not None and a != v:
            flips[(k[1], k[0])][0 if v else 1] += 1
    print(f"\nFlips {sys.argv[i + 1]} vs {sys.argv[1]} (+gained / -lost): " +
          ", ".join(f"#{q} {w} +{g}/-{l}" for (q, w), (g, l) in sorted(flips.items())))
