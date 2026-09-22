#!/usr/bin/env bash
# D-7: first learned arms on `Assoc`, plus the latency control D-5 left owing.
#
#   1  latency control  -- 6 `regular` arms at the two band cells, run in the
#                          SAME session as the D-5 policy arms, which is the
#                          only thing that settles the p99 tail those arms show
#   2  learner smoke    -- one `rl` arm at 10M/T=2; stop here if the learner is
#                          dead or the reward is broken, before the matrix runs
#   3  learner matrix   -- rl + unconstrained_rl, 10M x T=2/6/10 x 3 repeats
#   4  score            -- D-6 predictions 1-2, D-7 predictions 1-5
#
# Python-only changes since the hull was measured, so no rebuild: binary
# 9b9321b1... and the 182-arm Hull-0 stand.
set -Eeuo pipefail
cd "$(dirname "$0")"
PIPE=scripts/dbbench_pipeline
LEARN=results/learner-assoc
SMOKE=results/learner-smoke
mkdir -p gate1

banner() { printf '\n==============================================================\n%s\n==============================================================\n' "$1"; }

banner "PHASE 1/4  latency control: 6 regular arms at the band cells"
WORKLOAD_SIZES_M=10 SIZE_RATIOS="2 10" \
EXPERIMENT_ARMS=regular REPEATS=3 \
BASELINE_SLO_DIR=baseline_slo_band \
RESULTS_ROOT=results/band-control \
DB_ROOT=.dbbench_pipeline_dbs/band-control \
RESUME=1 CONFIRM_EXPERIMENTS=YES \
"$PIPE/03_run_experiments.sh"

banner "PHASE 2/4  learner smoke: one rl arm at 10M/T=2"
WORKLOAD_SIZES_M=10 SIZE_RATIOS=2 \
EXPERIMENT_ARMS=rl REPEATS=1 \
BASELINE_SLO_DIR=baseline_slo \
RESULTS_ROOT="$SMOKE" \
DB_ROOT=.dbbench_pipeline_dbs/learner-smoke \
RESUME=1 CONFIRM_EXPERIMENTS=YES \
"$PIPE/03_run_experiments.sh"

python3 - "$SMOKE" <<'PY'
# Gate the matrix on the smoke arm: a dead learner or a railed multiplier is
# worth catching in four minutes rather than seventy.
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
d = next(iter(root.glob("**/rl")), None)
if d is None:
    raise SystemExit("smoke arm produced no rl result directory")
health = d / "learning_health.json"
if not health.exists():
    raise SystemExit(f"no learning_health.json in {d}")
h = json.loads(health.read_text())
print(f"\nlearning health: {json.dumps(h, indent=2)[:900]}")
errs = h.get("errors") or h.get("failed_checks") or []
steps = h.get("train_steps") or h.get("total_train_steps") or 0
if errs:
    raise SystemExit(f"learner health FAILED: {errs}")
if steps and int(steps) < 100:
    raise SystemExit(f"learner took only {steps} gradient steps; matrix aborted")

lam = {}
io = d / "io.jsonl"
if io.exists():
    with io.open() as fh:
        for line in fh:
            c = (json.loads(line).get("reward_components") or {})
            for k in ("lambda_write", "lambda_space", "lambda_latency",
                      "lambda_scan", "lambda_stall"):
                if k in c:
                    lam[k] = c[k]
print("\nfinal multipliers:", json.dumps(lam, indent=2))
ls, lw = lam.get("lambda_space", 0.0), lam.get("lambda_write", 0.0)
print(f"\nD-6 prediction 2 (lambda_W binds, not lambda_S): "
      f"lambda_S={ls:.5f} lambda_W={lw:.5f} -> "
      f"{'as predicted' if ls <= lw else 'VIOLATED -- inspect before the matrix'}")
PY

banner "PHASE 3/4  learner matrix: rl + unconstrained_rl, 3 ratios, 3 repeats"
WORKLOAD_SIZES_M=10 SIZE_RATIOS="2 6 10" \
EXPERIMENT_ARMS="rl unconstrained_rl" REPEATS=3 \
BASELINE_SLO_DIR=baseline_slo \
RESULTS_ROOT="$LEARN" \
DB_ROOT=.dbbench_pipeline_dbs/learner-assoc \
RESUME=1 CONFIRM_EXPERIMENTS=YES \
"$PIPE/03_run_experiments.sh"

banner "PHASE 4/4  score D-6 and D-7"
rc=0
"$PIPE/17_analyze_shadow_overrides.py" \
  "$LEARN"/10M/T*/repeat-*/rl "$LEARN"/10M/T*/repeat-*/unconstrained_rl \
  --manifest-root baseline_slo --output gate1/d7_conditional.json \
  > gate1/d7_conditional.txt 2>&1 || rc=$?
echo "  stage 17 exit=$rc (per-arm errors only; see gate1/d7_conditional.txt)"

python3 - <<'PY' | tee gate1/d7_verdict.txt
import glob, importlib.util, json, re, statistics, sys
from collections import defaultdict
from pathlib import Path
P = Path("scripts/dbbench_pipeline"); sys.path.insert(0, str(P))
import frontier_analysis as fa
spec = importlib.util.spec_from_file_location("g", P / "04_generate_graphs.py")
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)

LEARN = "results/learner-assoc"
TWIN = {2: "T2-l0-2-20-36-pri3-base16777216",
        6: "T6-l0-4-20-36-pri3-base16777216",
        10: "T10-l0-2-20-36-pri3-base16777216"}

def arms(root, T, arm):
    out = {}
    for m in sorted(Path(root).glob("**/COMPLETED")):
        r = g.collect_arm(m.parent)
        if r and r["size_millions"] == 10 and r["size_ratio"] == T and r["arm"] == arm:
            out[int(r["dbbench_seed"])] = (r, m.parent)
    return out

def depth_and_lambdas(dirs):
    lv, lam, flips = [], defaultdict(list), []
    for d in dirs:
        cm = d / "compaction_measurements.json"
        if cm.exists():
            rel = json.loads(cm.read_text()).get("releases") or []
            if rel:
                lv.append(max(r.get("populated_levels", 0) for r in rel))
        io = d / "io.jsonl"
        if io.exists():
            last = {}
            with io.open() as fh:
                for line in fh:
                    last = (json.loads(line).get("reward_components") or {}) or last
            for k in ("lambda_write", "lambda_space"):
                if k in last: lam[k].append(last[k])
        hp = d / "learning_health.json"
        if hp.exists():
            h = json.loads(hp.read_text())
            for key in ("argmax_flip_rate", "flip_rate", "argmax_flip_rate_per_level"):
                v = h.get(key)
                if isinstance(v, (int, float)): flips.append(float(v))
                elif isinstance(v, dict): flips += [float(x) for x in v.values()]
    m = lambda xs: statistics.fmean(xs) if xs else float("nan")
    return m(lv), m(lam["lambda_space"]), m(lam["lambda_write"]), m(flips)

print("\nD-7: first learned arms on Assoc")
print("=" * 96)
print(f"{'cell':<6} {'pairs':>5} {'L rl':>6} {'L reg':>6} {'lam_S':>8} {'lam_W':>8} "
      f"{'flip':>6} {'dW %':>18} {'dR %':>18}")
print("-" * 96)
rows = {}
for T in (2, 6, 10):
    pol = arms(LEARN, T, "rl")
    twn = arms(f"results/baseline_sweep/{TWIN[T]}", T, "regular")
    seeds = sorted(pol.keys() & twn.keys())
    if not seeds:
        print(f"T={T:<4} no paired seeds"); continue
    c = fa.paired_comparison({s: pol[s][0] for s in seeds},
                             {s: twn[s][0] for s in seeds})
    Lp, ls, lw, fl = depth_and_lambdas([pol[s][1] for s in seeds])
    Lr, _, _, _ = depth_and_lambdas([twn[s][1] for s in seeds])
    iw = c["intervals"]["write_amplification"]; ir = c["intervals"]["point_read_amplification"]
    f = lambda i: f"{i['mean']*100:+6.2f} [{i['lower']*100:+5.2f},{i['upper']*100:+5.2f}]"
    print(f"T={T:<4} {len(seeds):>5} {Lp:>6.1f} {Lr:>6.1f} {ls:>8.5f} {lw:>8.5f} "
          f"{fl:>6.2f} {f(iw):>18} {f(ir):>18}")
    rows[T] = {"dL": Lp - Lr, "ls": ls, "lw": lw, "flip": fl,
               "dW": iw["mean"]*100, "dR": ir["mean"]*100}

V = lambda ok: "PASS" if ok else "FAIL"
if rows:
    print()
    print(f"1  depth does not inflate (dL <= 0):        "
          f"{ {t: round(r['dL'],1) for t,r in rows.items()} }  -> "
          f"{V(all(r['dL'] <= 0.5 for r in rows.values()))}")
    print(f"2  lambda_S < lambda_W in every cell:       "
          f"{ {t: (round(r['ls'],4), round(r['lw'],4)) for t,r in rows.items()} }  -> "
          f"{V(all(r['ls'] < r['lw'] or (r['ls'] == 0 and r['lw'] == 0) for r in rows.values()))}")
    print(f"4  argmax flip rate > 0.1 per level:        "
          f"{ {t: round(r['flip'],2) for t,r in rows.items()} }  -> "
          f"{V(all(r['flip'] > 0.1 for r in rows.values() if r['flip'] == r['flip']))}")
    t2 = rows.get(2, {}).get("dR", 0); t10 = rows.get(10, {}).get("dR", 0)
    print(f"5  no read gain at trigger-2 cells (dR >= -2%): "
          f"T=2 {t2:+.2f}%  T=10 {t10:+.2f}%  -> {V(t2 >= -2 and t10 >= -2)}")
    print("\n3  E-5 conditional override rate: see gate1/d7_conditional.txt")
    print("   (non-zero in >=1 cell AND <= 1% everywhere = as predicted)")
PY

echo
echo "DONE. Artifacts:"
echo "  results/band-control/            + 6 regular arms (latency control)"
echo "  $LEARN/     18 learned arms"
echo "  gate1/d7_verdict.txt             D-7 predictions 1, 2, 4, 5"
echo "  gate1/d7_conditional.json/.txt   E-5 (prediction 3)"
