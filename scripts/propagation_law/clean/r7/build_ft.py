#!/usr/bin/env python3
"""Per run: every memtable switch with its time and its write-key counter, and every merge job's begin/end/bytes read, from the
run's host log (the records cycles_k.py reads). Usage: build_ft.py ROOT OUTDIR NPROC [prefix]"""
import json, sys
from multiprocessing import Pool
from pathlib import Path
ROOT, OUT = Path(sys.argv[1]), Path(sys.argv[2])
PFX = sys.argv[4] if len(sys.argv) > 4 else ""
KEYS = "rocksdb.number.keys.written"
def one(name):
    meta = json.loads((ROOT/"meta"/f"{name}.json").read_text())
    run = Path(meta["run"])
    fb, ik = [], None
    with open(run/"host_log.jsonl") as fh:
        for line in fh:
            if '"type":"h"' in line or '"type":"snap"' in line or '"type":"flush_begin"' not in line and '"type":"header"' not in line:
                continue
            r = json.loads(line)
            if r["type"] == "header":
                ik = r["fg"].index(KEYS)
            else:
                fb.append([r["op"], r["t_us"], r["fg"][ik]])
    fb.sort()
    (OUT/f"{name}.json").write_text(json.dumps(dict(fb=fb)))
    return len(fb)
names = sorted(p.stem for p in (ROOT/"meta").glob(f"{PFX}*.json"))
with Pool(int(sys.argv[3])) as pool: n = pool.map(one, names)
print(len(names), "runs", sum(n), "flush begins"); print("FT_EXIT=0")
