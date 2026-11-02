import json, sys
from collections import defaultdict
for fn in sys.argv[1:]:
    agg = defaultdict(lambda: [0, 0])
    for line in open(fn):
        r = json.loads(line)
        if r.get("missing"): continue
        for c in r["changes"]:
            for q in ("2", "3"):
                d = c["q"].get(q)
                if not d or d["meas"] is None or d["lim"] is None or d.get("r7") is None: continue
                agg[(q, r["w"])][0] += abs(d["meas"] - d["r7"]) <= d["lim"]
                agg[(q, r["w"])][1] += 1
    print(fn, {f"#{k[0]} {k[1]}": f"{h}/{n}" for k, (h, n) in sorted(agg.items())})
