#!/usr/bin/env python3
"""#3 and every other row against the adopted rows, counted afresh from the rows. Usage: cnt3.py ADOPTED ROWS [list]"""
import json,sys
from collections import defaultdict
A={r["name"]:r for r in map(json.loads,open(sys.argv[1]))}; B={r["name"]:r for r in map(json.loads,open(sys.argv[2]))}
BAR={"W1":.9,"W2":.9,"W3":.9,"W4":.9,"W5":.9}
g=defaultdict(lambda:[0,0,0,0,0]); 
def h(d): return d.get("r7") is not None and d["meas"] is not None and d["lim"] is not None and abs(d["meas"]-d["r7"])<=d["lim"]
for n,a in A.items():
    for ca,cb in zip(a["changes"],B[n]["changes"]):
        for q,da in ca["q"].items():
            db=cb["q"][q]
            if da["meas"] is None or da["lim"] is None or da.get("r7") is None: continue
            x,y=h(da),h(db); k=(q,a["w"],"up" if ca["k_to"]>ca["k_from"] else "down")
            for kk in (k,(q,a["w"],"all")):
                g[kk][0]+=1;g[kk][1]+=x;g[kk][2]+=y;g[kk][3]+=y and not x;g[kk][4]+=x and not y
        for q in ("7","8a","8b"):
            if ca["path"].get(q) is None and cb["path"].get(q) is None: continue
            kk=("p"+q,a["w"],"all"); g[kk][0]+=1;g[kk][1]+=bool(ca["path"].get(q));g[kk][2]+=bool(cb["path"].get(q));g[kk][3]+=bool(cb["path"].get(q)) and not ca["path"].get(q);g[kk][4]+=bool(ca["path"].get(q)) and not cb["path"].get(q)
print("#3: W n law new +gain -lost bar ok | up | down")
for w in ("W1","W2","W3","W4","W5"):
    x=g[("3",w,"all")];u=g[("3",w,"up")];d=g[("3",w,"down")]
    print(f"{w} {x[0]} {x[1]} {x[2]} +{x[3]} -{x[4]} bar {0.9*x[0]:.1f} {'PASS' if x[2]>=0.9*x[0] else 'FAIL'} | {u[1]}->{u[2]}/{u[0]} | {d[1]}->{d[2]}/{d[0]}")
mv=[(k,v) for k,v in sorted(g.items()) if k[2]=="all" and k[0]!="3" and (v[3] or v[4])]
print("other rows moved:", "none" if not mv else "")
for k,v in mv: print(f"  {k[0]} {k[1]} {v[1]}->{v[2]}/{v[0]} +{v[3]} -{v[4]}")
