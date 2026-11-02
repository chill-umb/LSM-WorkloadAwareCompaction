import json, sys
from collections import defaultdict
for fn in sys.argv[1:]:
    agg = defaultdict(lambda: [0, 0])
    for line in open(fn):
        r = json.loads(line)
        if r.get("missing"): continue
        for c in r["changes"]:
            d = c["q"].get("4")
            if not d or d["meas"] is None or d["lim"] is None or d.get("r7") is None: continue
            agg[r["w"]][0] += abs(d["meas"] - d["r7"]) <= d["lim"]
            agg[r["w"]][1] += 1
    print(fn, " ".join(f"{w} {h}/{n} (bar {0.9 * n:.1f})" for w, (h, n) in sorted(agg.items())))
