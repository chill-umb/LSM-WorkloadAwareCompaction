#!/usr/bin/env bash
# D-9: the realigned learner on `Assoc` (PREREGISTRATION D-9, history 14.20).
#
#   1  learner smoke    -- one `rl` arm at 10M/T=2; stop here if the learner is
#                          dead, a multiplier rails, the reward's write
#                          accounting disagrees with the evaluator, or an early
#                          deep release shows the mask did not reach the plant
#   2  learner matrix   -- rl + unconstrained_rl, 10M x T=2/6/10 x 3 repeats
#   3  E-5              -- stage 17 on the enforced arms
#   4  score            -- D-9 predictions 1-9, D-8 predictions 1-5
#
# Python-only changes since the hull was measured, so no rebuild: binary
# 9b9321b1... and the 182-arm Hull-0 stand. Fresh result roots: the D-7 arms
# in results/learner-assoc carry COMPLETED markers and must not be resumed
# into, and they stay as the D-7 record.
set -Eeuo pipefail
cd "$(dirname "$0")"
PIPE=scripts/dbbench_pipeline
PY=${PYTHON_BIN:-.venv-dbbench/bin/python}
[[ -x "$PY" ]] || PY=python3
LEARN=results/learner-assoc-d9
SMOKE=results/learner-smoke-d9
mkdir -p gate1

banner() { printf '\n==============================================================\n%s\n==============================================================\n' "$1"; }

banner "PHASE 1/4  learner smoke: one rl arm at 10M/T=2"
WORKLOAD_SIZES_M=10 SIZE_RATIOS=2 \
EXPERIMENT_ARMS=rl REPEATS=1 \
BASELINE_SLO_DIR=baseline_slo \
RESULTS_ROOT="$SMOKE" \
DB_ROOT=.dbbench_pipeline_dbs/learner-smoke-d9 \
RESUME=1 CONFIRM_EXPERIMENTS=YES \
"$PIPE/03_run_experiments.sh"

"$PY" - "$SMOKE" <<'PY'
# Gate the matrix on the smoke arm. Every check here is a D-9 plumbing
# prediction; a failure means the change did not reach the plant, and the
# seventy-minute matrix would measure a harness rather than a policy.
import importlib.util, json, sys
from pathlib import Path
P = Path("scripts/dbbench_pipeline"); sys.path.insert(0, str(P))
spec = importlib.util.spec_from_file_location("g", P / "04_generate_graphs.py")
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
root = Path(sys.argv[1])
d = next(iter(root.glob("**/rl")), None)
if d is None:
    raise SystemExit("smoke arm produced no rl result directory")
h = json.loads((d / "learning_health.json").read_text())
ss = h.get("server_summary", h)
errs = h.get("errors") or []
if errs or not h.get("passed", False):
    raise SystemExit(f"learner health FAILED: {errs}")
cr = ss["constrained_reward"]; lam = cr["lambda"]
print("final multipliers:", json.dumps(lam))
print("train_steps:", ss.get("train_steps"), " replay:", ss.get("replay_size"))
lam_max = 100.0
if lam["latency"] >= lam_max:
    raise SystemExit("lambda_latency railed at LAMBDA_MAX; D-8 prediction 1 fails on the smoke arm")
r = g.collect_arm(d)
w_eval = r["write_amplification"]; w_reward = cr.get("write_amplification_cumulative")
if w_reward is None or abs(w_reward / w_eval - 1.0) > 0.02:
    raise SystemExit(f"D-9 prediction 1 FAILS on the smoke arm: reward W {w_reward} vs evaluator W {w_eval:.3f}")
print(f"D-9 prediction 1 (smoke): reward W {w_reward:.4f} vs evaluator W {w_eval:.4f} -> within 2%")
cm = json.loads((d / "compaction_measurements.json").read_text())
truthy = lambda v: v in (True, 1, "1", "true")
early = 0
for rel in cm["releases"]:
    if truthy(rel.get("rl_drain")) or truthy(rel.get("rl_suspended")) or truthy(rel.get("trivial_move")):
        continue
    L = int(rel["source_level"])
    if L == 0: continue
    phi = rel["phi"]; phi = phi[L] if isinstance(phi, list) else phi
    if phi is not None and float(phi) < 0.95: early += 1
if early:
    raise SystemExit(f"D-9 prediction 3 FAILS on the smoke arm: {early} deep releases below phi 0.95; the mask did not reach the plant")
print("D-9 prediction 3 (smoke): zero deep releases below phi 0.95")
PY

banner "PHASE 2/4  learner matrix: rl + unconstrained_rl, 3 ratios, 3 repeats"
WORKLOAD_SIZES_M=10 SIZE_RATIOS="2 6 10" \
EXPERIMENT_ARMS="rl unconstrained_rl" REPEATS=3 \
BASELINE_SLO_DIR=baseline_slo \
RESULTS_ROOT="$LEARN" \
DB_ROOT=.dbbench_pipeline_dbs/learner-assoc-d9 \
RESUME=1 CONFIRM_EXPERIMENTS=YES \
"$PIPE/03_run_experiments.sh"

banner "PHASE 3/4  E-5: conditional override rate on the enforced arms"
rc=0
"$PIPE/17_analyze_shadow_overrides.py" \
  "$LEARN"/10M/T*/repeat-*/rl \
  --manifest-root baseline_slo --output gate1/d9_conditional.json \
  > gate1/d9_conditional.txt 2>&1 || rc=$?
echo "  stage 17 exit=$rc (per-arm errors only; see gate1/d9_conditional.txt)"

banner "PHASE 4/4  score D-9 predictions 1-9 and D-8 predictions 1-5"
"$PY" - <<'PY' | tee gate1/d9_verdict.txt
import importlib.util, json, statistics, sys
from collections import defaultdict
from pathlib import Path
P = Path("scripts/dbbench_pipeline"); sys.path.insert(0, str(P))
import frontier_analysis as fa
spec = importlib.util.spec_from_file_location("g", P / "04_generate_graphs.py")
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)

import os
LEARN = os.environ.get("D9_LEARN_ROOT", "results/learner-assoc-d9")
TWIN = {2: "T2-l0-2-20-36-pri3-base16777216",
        6: "T6-l0-4-20-36-pri3-base16777216",
        10: "T10-l0-2-20-36-pri3-base16777216"}
LAMBDA_MAX = 100.0
truthy = lambda v: v in (True, 1, "1", "true")
mean = lambda xs: statistics.fmean(xs) if xs else float("nan")

def arms(root, T, arm):
    out = {}
    for m in sorted(Path(root).glob("**/COMPLETED")):
        r = g.collect_arm(m.parent)
        if r and r["size_millions"] == 10 and r["size_ratio"] == T and r["arm"] == arm:
            out[int(r["dbbench_seed"])] = (r, m.parent)
    return out

def depth(d):
    cm = d / "compaction_measurements.json"
    if not cm.exists(): return float("nan")
    rel = json.loads(cm.read_text()).get("releases") or []
    return max((r.get("populated_levels", 0) for r in rel), default=float("nan"))

def early_deep_releases(d):
    cm = d / "compaction_measurements.json"
    if not cm.exists(): return None
    n = 0
    for rel in json.loads(cm.read_text()).get("releases") or []:
        if truthy(rel.get("rl_drain")) or truthy(rel.get("rl_suspended")) or truthy(rel.get("trivial_move")):
            continue
        L = int(rel["source_level"])
        if L == 0: continue
        phi = rel["phi"]; phi = phi[L] if isinstance(phi, list) else phi
        if phi is not None and float(phi) < 0.95: n += 1
    return n

def policy_arm(d):
    """Everything read from the learned arm's own logs, from the sources the
    D-7 scorer got wrong: final multipliers from learning_health.json, the
    per-frame trajectory and actions from io.jsonl."""
    h = json.loads((d / "learning_health.json").read_text()); ss = h.get("server_summary", h)
    cr = ss["constrained_reward"]; final = dict(cr["lambda"])
    peak = defaultdict(float); l0_pro_files = []; l0_due = l0_due_defer = 0
    lat_terms = defaultdict(list)
    with (d / "io.jsonl").open() as fh:
        for line in fh:
            e = json.loads(line); c = e.get("reward_components") or {}
            for k in ("write", "space", "latency", "scan", "stall"):
                v = c.get(f"lambda_{k}")
                if v is not None: peak[k] = max(peak[k], float(v))
            for k, v in (c.get("latency_terms") or {}).items():
                lat_terms[k].append(float(v))
            i = e["input"]
            if int(i["level"]) != 0: continue
            due = i["score"] >= 1.0; a = e["output"]["action"]
            if due:
                l0_due += 1
                if a == 0: l0_due_defer += 1
            elif a == 1:
                l0_pro_files.append(float(i["files"]))
    flips = ss.get("argmax_flip_count", 0); comps = ss.get("argmax_comparison_count", 0)
    return {"final": final, "peak": dict(peak), "w_reward": cr.get("write_amplification_cumulative"),
            "l0_pro_files": mean(l0_pro_files), "l0_pro_n": len(l0_pro_files),
            "l0_due": l0_due, "l0_due_defer": l0_due_defer,
            "flip": (flips / comps) if comps else float("nan"),
            "lat_terms": {k: mean(v) for k, v in lat_terms.items()},
            "early_deep": early_deep_releases(d), "passed": bool(h.get("passed"))}

V = lambda ok: "PASS" if ok else "FAIL"
print("\nD-9: the realigned learner on Assoc")
print("=" * 100)
rows = {}; per_arm = []
for T in (2, 6, 10):
    twn = arms(f"results/baseline_sweep/{TWIN[T]}", T, "regular")
    for arm in ("rl", "unconstrained_rl"):
        pol = arms(LEARN, T, arm)
        seeds = sorted(pol.keys() & twn.keys())
        if not seeds:
            print(f"T={T} {arm}: no paired seeds"); continue
        c = fa.paired_comparison({s: pol[s][0] for s in seeds}, {s: twn[s][0] for s in seeds})
        iw = c["intervals"]["write_amplification"]; ir = c["intervals"]["point_read_amplification"]
        info = [policy_arm(pol[s][1]) for s in seeds]
        for s, inf in zip(seeds, info):
            inf.update(T=T, arm=arm, seed=s, w_eval=pol[s][0]["write_amplification"])
            per_arm.append(inf)
        dL = mean([depth(pol[s][1]) for s in seeds]) - mean([depth(twn[s][1]) for s in seeds])
        rows[(T, arm)] = {"n": len(seeds), "dW": iw["mean"] * 100, "dWlo": iw["lower"] * 100, "dWhi": iw["upper"] * 100,
                          "dR": ir["mean"] * 100, "dRlo": ir["lower"] * 100, "dRhi": ir["upper"] * 100,
                          "W": mean([pol[s][0]["write_amplification"] for s in seeds]),
                          "R": mean([pol[s][0]["point_read_amplification"] for s in seeds]),
                          "dL": dL, "flip": mean([i["flip"] for i in info])}

print(f"{'cell':<22} {'n':>2} {'W':>7} {'R':>7} {'dL':>5} {'flip':>5} {'dW % [95% CI]':>24} {'dR % [95% CI]':>24}")
print("-" * 100)
for (T, arm), r in rows.items():
    print(f"T={T:<3} {arm:<16} {r['n']:>2} {r['W']:>7.3f} {r['R']:>7.3f} {r['dL']:>+5.1f} {r['flip']:>5.2f} "
          f"{r['dW']:+7.2f} [{r['dWlo']:+6.2f},{r['dWhi']:+6.2f}] {r['dR']:+7.2f} [{r['dRlo']:+6.2f},{r['dRhi']:+6.2f}]")

print("\nPer arm:")
print(f"{'cell':<22} {'seed':>6} {'W_eval':>7} {'W_rwd':>7} {'lamW fin/pk':>13} {'lamS':>6} {'lamL fin/pk':>13} "
      f"{'early':>5} {'L0 pro n/files':>14} {'L0 due def/due':>14}")
for i in per_arm:
    f, p = i["final"], i["peak"]
    w_rwd = "-" if i["w_reward"] is None else f"{i['w_reward']:.3f}"
    print(f"T={i['T']:<3} {i['arm']:<16} {i['seed']:>6} {i['w_eval']:>7.3f} {w_rwd:>7} "
          f"{f['write']:>6.2f}/{p.get('write',0):<6.2f} {f['space']:>6.2f} {f['latency']:>6.2f}/{p.get('latency',0):<6.2f} "
          f"{str(i['early_deep']):>5} {i['l0_pro_n']:>5}/{i['l0_pro_files']:<8.2f} {i['l0_due_defer']:>6}/{i['l0_due']:<7}")

rl = [i for i in per_arm if i["arm"] == "rl"]; un = [i for i in per_arm if i["arm"] == "unconstrained_rl"]
print("\nD-9 predictions")
p1 = all(i["w_reward"] is not None and abs(i["w_reward"] / i["w_eval"] - 1) <= 0.02 for i in per_arm)
print(f"1  reward W == evaluator W within 2%, every arm:                      {V(p1)}")
p2 = all(any(i["final"][k] < i["peak"].get(k, 0) - 1e-9 for k in i["final"]) for i in per_arm) \
     and all(i["peak"].get("latency", 0) < LAMBDA_MAX for i in per_arm)
print(f"2  no multiplier is a ratchet; lambda_lat never rails (D-8 p1):       {V(p2)}")
p3 = all(i["early_deep"] == 0 for i in per_arm) and all(i["l0_pro_n"] == 0 or i["l0_pro_files"] >= 2.0 for i in per_arm)
print(f"3  zero early deep releases; L0 proactive compacts at >= 2 files:      {V(p3)}")
p4a = all(max(i["final"], key=i["final"].get) == "write" for i in rl)
p4b_cells = [i for i in rl if rows[(i["T"], "rl")]["dW"] <= 2.0]
p4b = all(i["final"]["write"] < i["peak"].get("write", 0) - 1e-9 for i in p4b_cells) if p4b_cells else True
print(f"4  lambda_W largest at end of every rl arm (D-8 p2): {V(p4a)};  falls where W ends inside 2%: {V(p4b)} ({len(p4b_cells)} arms)")
p5 = all(r["dL"] <= 0.5 for (T, arm), r in rows.items() if arm == "rl")
print(f"5  depth does not inflate on rl (dL <= 0.5) (D-7 p1 / D-8 p4):         {V(p5)}  "
      + str({T: round(r['dL'], 1) for (T, arm), r in rows.items() if arm == 'rl'}))
lim = {2: 3.0, 6: 5.0, 10: 3.0}
p6a = all(rows[(T, "rl")]["dW"] <= lim[T] for T in lim if (T, "rl") in rows)
p6b = all(rows[(T, "unconstrained_rl")]["W"] < rows[(T, "rl")]["W"] for T in lim if (T, "rl") in rows and (T, "unconstrained_rl") in rows)
print(f"6  rl dW <= +3/+5/+3% at T=2/6/10: {V(p6a)};  unconstrained_rl W < rl W every cell: {V(p6b)}")
p7 = all(rows[(T, "rl")]["dR"] <= 2.0 for T in (2, 10) if (T, "rl") in rows)
print(f"7  rl dR <= +2% at the trigger-2 cells (conditional on 5):            {V(p7)}")
print("8  E-5 above 1% in >= 2 cells (D-8 p5): see gate1/d9_conditional.txt")
p9a = all(i["l0_due_defer"] > 0 for i in rl)
frac = lambda xs: mean([i["l0_due_defer"] / i["l0_due"] for i in xs if i["l0_due"]])
p9b = frac(un) > frac(rl) if rl and un else False
print(f"9  rl defers a due L0 on > 0 frames: {V(p9a)};  unconstrained defers a larger share "
      f"({frac(un) if un else float('nan'):.3f} vs {frac(rl) if rl else float('nan'):.3f}): {V(p9b)}")
share = []
for i in per_arm:
    lt = i["lat_terms"]; tot = sum(lt.values())
    if tot > 0 and "write_p99" in lt: share.append(lt["write_p99"] / tot)
print(f"\nD-8 p3, scored as a measurement of the D-7 instrument: write_p99 share of the "
      f"logged latency terms = {mean(share):.3f} (prediction: >= 0.90) -> {V(share and mean(share) >= 0.90)}")
PY

echo
echo "DONE. Artifacts:"
echo "  $SMOKE/                        1 smoke arm"
echo "  $LEARN/                        18 learned arms"
echo "  gate1/d9_verdict.txt                          D-9 predictions 1-7, 9; D-8 p3"
echo "  gate1/d9_conditional.json/.txt                E-5 (D-9 prediction 8)"
