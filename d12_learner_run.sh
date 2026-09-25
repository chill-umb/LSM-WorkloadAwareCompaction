#!/usr/bin/env bash
# D-12: the realigned learner on `Assoc` with the latency multiplier's slack
# measured since the warm-up against the baseline's own trajectory
# (PREREGISTRATION D-12, history 14.23). Same shape as the D-11 driver, whose
# smoke gate stopped on lambda_latency = 27.75: the run-to-date average carried
# the load's tail for the whole run, and so did the baseline's; that file
# stays as the D-11 record.
#
#   0  manifests        -- regenerate; the calibrator now also writes the
#                          since-warm-up latency trajectory (guard fields
#                          byte-identical, four fields added)
#   1  learner smoke    -- one `rl` arm at 10M/T=2; stop here if the learner is
#                          dead, a multiplier rails, lambda_latency ends above
#                          5, the reward's write accounting disagrees with the
#                          evaluator, or an early deep release shows the mask
#                          did not reach the plant
#   2  learner matrix   -- rl + unconstrained_rl, 10M x T=2/6/10 x 3 repeats
#   3  E-5              -- stage 17 on the enforced arms
#   4  score            -- D-12 predictions
#
# Python-only changes since the hull was measured, so no rebuild: binary
# 9b9321b1... and the 182-arm Hull-0 stand. Fresh result roots; the D-7 and
# D-11 arms stay as their own records and are never resumed into.
#
# Two corrections after the first smoke attempt (PREREGISTRATION D-12, "first
# smoke attempt"): the trajectory block's nested key no longer shadows the
# top-level schema_version the C++ parser reads by first match, and the reward
# leaves the resume-straddling first frame out of its run-to-date totals.
set -Eeuo pipefail
cd "$(dirname "$0")"
PIPE=scripts/dbbench_pipeline
PY=${PYTHON_BIN:-.venv-dbbench/bin/python}
[[ -x "$PY" ]] || PY=python3
LEARN=results/learner-assoc-d12
# The first smoke attempt (results/learner-smoke-d12, 2026-09-23) ran under a
# manifest the C++ parser rejected and is kept as that record; 03 refuses to
# reuse a failed arm's directory or database, so the retry has its own roots.
SMOKE=results/learner-smoke-d12-2
mkdir -p gate1

banner() { printf '\n==============================================================\n%s\n==============================================================\n' "$1"; }

banner "PHASE 0/4  manifests: regenerate with the since-warm-up latency trajectory (D-12)"
# Keep the D-11 manifests (the ones the D-11 smoke arm ran under) beside the
# pre-D-11 ones, as D-11 did; never overwrite an archived copy.
for T in 2 6 10; do
  mkdir -p "deprecated/pre-d12-2026-09-23/baseline_slo/assoc-v1/10M/T$T"
  [[ -f "deprecated/pre-d12-2026-09-23/baseline_slo/assoc-v1/10M/T$T/baseline_slo.json" ]] || \
    cp "baseline_slo/assoc-v1/10M/T$T/baseline_slo.json" "deprecated/pre-d12-2026-09-23/baseline_slo/assoc-v1/10M/T$T/"
done
# Stage 06 re-selects the comparator from the existing sweep under the
# corrected write, stall and latency definitions; the selected options must
# come out unchanged (else stop: the comparator moved and needs its own dated
# decision). The calibrator then re-runs over the existing calibration
# artifacts, accepting the selection hash those runs recorded. Manifests are
# gitignored data, so the node regenerates its own copies.
for T in 2 6 10; do
  final="baseline_slo/assoc-v1/10M/T$T/baseline_slo.json"
  sel="baseline_selection/assoc-v1/10M/T$T/baseline_slo.json"
  calib="results/guard/assoc-v1/calibration/10M/T$T"
  # The manifest hash the calibration arms ran under is read from the arms
  # themselves. The calibrator rewrites `selection_manifest_sha256` in the
  # final manifest to the NEW selection's hash, so reading it back from there
  # on a second pass handed it a hash no arm carries and it refused every cell
  # ("not an oracle calibration run", node, 2026-09-23). The repeats must agree.
  arm_sha="$("$PY" - "$calib" <<'PY0'
import sys, pathlib
shas = set()
for env in sorted(pathlib.Path(sys.argv[1]).glob("repeat-*/oracle/metadata.env")):
    kv = dict(line.rstrip("\n").split("=", 1) for line in open(env) if "=" in line)
    shas.add(kv["baseline_slo_sha256"])
if len(shas) != 1:
    raise SystemExit(f"calibration arms under {sys.argv[1]} carry {len(shas)} manifest hashes: {sorted(shas)}")
print(shas.pop())
PY0
)"
  # Snapshot the pre-D-12 selection once and never overwrite it on a re-run.
  [[ -f "$sel.pre-d11" ]] || cp "$sel" "$sel.pre-d11"
  # "The comparator did not move" is checked against the final manifest's own
  # selected options: the calibrator accepted those against these arms on every
  # earlier pass, so the check is the same on a first pass and on a re-run.
  ref="$(mktemp)"; cp "$final" "$ref"
  "$PY" "$PIPE/06_select_baseline_slo.py" --baseline-results results/baseline_sweep \
    --workload-profile assoc-v1 --size-millions 10 --size-ratio "$T" --output "$sel" | tail -1
  "$PY" - "$sel" "$ref" <<'PY0'
import json, sys
new, ref = (json.load(open(p)) for p in sys.argv[1:3])
a, b = ref["selected_baseline_options"], new["selected_baseline_options"]
if a != b:
    raise SystemExit("comparator selection CHANGED under the corrected evaluator: " + str({k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}))
print(f"  selected options unchanged; W ref {ref['write_amplification_reference']:.4f} -> {new['write_amplification_reference']:.4f}, stall ref {ref['stall_fraction_reference']:.5f} -> {new['stall_fraction_reference']:.5f}")
PY0
  rm -f "$ref"
  "$PY" "$PIPE/06_calibrate_live_guard.py" --selection-manifest "$sel" \
    --calibration-results results/guard/assoc-v1/calibration --repeats 3 \
    --accept-selection-sha256 "$arm_sha" --output "$final" | tail -1
done
sha256sum baseline_slo/assoc-v1/10M/T*/baseline_slo.json
"$PY" - <<'PY0'
import json
for T in (2, 6, 10):
    m = json.load(open(f"baseline_slo/assoc-v1/10M/T{T}/baseline_slo.json"))
    tr = m["latency_reference_trajectory"]
    print(f"T={T}: telemetry avg ns get={m['get_latency_avg_ns_telemetry_reference']:.0f} "
          f"scan={m['scan_latency_avg_ns_telemetry_reference']:.0f} write={m['write_latency_avg_ns_telemetry_reference']:.0f}"
          f" | since-{tr['warmup_seconds']:.0f}s trajectory: {len(tr['elapsed_seconds'])} points, steady "
          f"get={m['get_latency_avg_ns_telemetry_steady_reference']:.0f} scan={m['scan_latency_avg_ns_telemetry_steady_reference']:.0f} "
          f"write={m['write_latency_avg_ns_telemetry_steady_reference']:.0f}")
PY0

banner "PHASE 1/4  learner smoke: one rl arm at 10M/T=2"
WORKLOAD_SIZES_M=10 SIZE_RATIOS=2 \
EXPERIMENT_ARMS=rl REPEATS=1 \
BASELINE_SLO_DIR=baseline_slo \
RESULTS_ROOT="$SMOKE" \
DB_ROOT=.dbbench_pipeline_dbs/learner-smoke-d12-2 \
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
print("resume frame left out of the run-to-date totals:", json.dumps(cr.get("resume_frame_dropped")))
print("train_steps:", ss.get("train_steps"), " replay:", ss.get("replay_size"))
lam_max = 100.0
if lam["latency"] >= lam_max:
    raise SystemExit("lambda_latency railed at LAMBDA_MAX; D-8 prediction 1 fails on the smoke arm")
r = g.collect_arm(d)
w_eval = r["write_amplification"]; w_reward = cr.get("write_amplification_cumulative")
if r.get("write_amplification_source") != "measured_phase_event_log":
    raise SystemExit(f"evaluator fell back to whole-run tickers: {r.get('write_amplification_source')}")
if w_reward is None or abs(w_reward / w_eval - 1.0) > 0.005:
    raise SystemExit(f"D-12 prediction 1 FAILS on the smoke arm: reward W {w_reward} vs evaluator measured-phase W {w_eval:.3f}")
print(f"D-12 prediction 1 (smoke): reward W {w_reward:.4f} vs evaluator measured-phase W {w_eval:.4f} -> within 0.5%")
if max(lam.values()) >= lam_max:
    raise SystemExit(f"a multiplier railed at LAMBDA_MAX on the smoke arm: {lam}")
# D-12: what drove lambda_latency, and what the superseded whole-run form
# would have done on the same frames (offline, same update rule).
since = cr.get("latency_since_warmup_avg_ns") or {}; ref = cr.get("latency_reference_end_ns") or {}
print("latency slack form:", cr.get("latency_slack_form"),
      " since-warm-up / reference at end:", {k: round(since[k] / ref[k], 3) for k in since if since.get(k) and ref.get(k)})
lam_whole = 1.0; peak_whole = 1.0; t = 0.0; seen = set()
with (d / "io.jsonl").open() as fh:
    for line in fh:
        e = json.loads(line); c = e.get("reward_components") or {}
        if "lambda_latency" not in c or e["step"] in seen: continue
        seen.add(e["step"]); t += float(c.get("reward_dt", 0.0))
        if t >= 30.0:
            s = max(-1.0, min(1.0, float(c.get("latency_slack_whole_run", 0.0))))
            lam_whole = min(lam_max, max(0.0, lam_whole + 0.05 * s)); peak_whole = max(peak_whole, lam_whole)
print(f"lambda_latency under the superseded whole-run form, simulated offline: end {lam_whole:.2f}, peak {peak_whole:.2f} (recorded D-12 form: {lam['latency']:.2f})")
if lam["latency"] > 5.0:
    raise SystemExit(f"D-12 prediction 2 FAILS on the smoke arm: lambda_latency {lam['latency']:.2f} > 5")
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
    raise SystemExit(f"D-12 prediction 3 FAILS on the smoke arm: {early} deep releases below phi 0.95; the mask did not reach the plant")
print("D-12 prediction 3 (smoke): zero deep releases below phi 0.95")
PY

banner "PHASE 2/4  learner matrix: rl + unconstrained_rl, 3 ratios, 3 repeats"
WORKLOAD_SIZES_M=10 SIZE_RATIOS="2 6 10" \
EXPERIMENT_ARMS="rl unconstrained_rl" REPEATS=3 \
BASELINE_SLO_DIR=baseline_slo \
RESULTS_ROOT="$LEARN" \
DB_ROOT=.dbbench_pipeline_dbs/learner-assoc-d12 \
RESUME=1 CONFIRM_EXPERIMENTS=YES \
"$PIPE/03_run_experiments.sh"

banner "PHASE 3/4  E-5: conditional override rate on the enforced arms"
rc=0
"$PIPE/17_analyze_shadow_overrides.py" \
  "$LEARN"/10M/T*/repeat-*/rl \
  --manifest-root baseline_slo --output gate1/d12_conditional.json \
  > gate1/d12_conditional.txt 2>&1 || rc=$?
echo "  stage 17 exit=$rc (per-arm errors only; see gate1/d12_conditional.txt)"

banner "PHASE 4/4  score D-12 predictions"
"$PY" - <<'PY' | tee gate1/d12_verdict.txt
import importlib.util, json, statistics, sys
from collections import defaultdict
from pathlib import Path
P = Path("scripts/dbbench_pipeline"); sys.path.insert(0, str(P))
import frontier_analysis as fa
spec = importlib.util.spec_from_file_location("g", P / "04_generate_graphs.py")
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)

import os
LEARN = os.environ.get("D12_LEARN_ROOT", "results/learner-assoc-d12")
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
    # D-12: the superseded whole-run form simulated offline on the same frames,
    # and the since-warm-up end values against the trajectory's end.
    lam_whole = 1.0; t = 0.0; seen = set()
    with (d / "io.jsonl").open() as fh:
        for line in fh:
            e = json.loads(line); c = e.get("reward_components") or {}
            if "lambda_latency" not in c or e["step"] in seen: continue
            seen.add(e["step"]); t += float(c.get("reward_dt", 0.0))
            if t >= 30.0:
                s = max(-1.0, min(1.0, float(c.get("latency_slack_whole_run", 0.0))))
                lam_whole = min(LAMBDA_MAX, max(0.0, lam_whole + 0.05 * s))
    since = cr.get("latency_since_warmup_avg_ns") or {}; ref = cr.get("latency_reference_end_ns") or {}
    since_ratio = {k: since[k] / ref[k] for k in since if since.get(k) and ref.get(k)}
    return {"final": final, "peak": dict(peak), "w_reward": cr.get("write_amplification_cumulative"),
            "lam_lat_whole_run": lam_whole, "since_ratio": since_ratio,
            "l0_pro_files": mean(l0_pro_files), "l0_pro_n": len(l0_pro_files),
            "l0_due": l0_due, "l0_due_defer": l0_due_defer,
            "flip": (flips / comps) if comps else float("nan"),
            "lat_terms": {k: mean(v) for k, v in lat_terms.items()},
            "early_deep": early_deep_releases(d), "passed": bool(h.get("passed"))}

V = lambda ok: "PASS" if ok else "FAIL"
print("\nD-12: the realigned learner on Assoc, measured-phase evaluator")
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
        def interval(metric):
            iv = (c.get("intervals") or {}).get(metric)
            if iv: return iv
            # A single pair has no interval; report the mean difference alone.
            rel = [pol[s][0][metric] / twn[s][0][metric] - 1.0 for s in seeds]
            return {"mean": mean(rel), "lower": float("nan"), "upper": float("nan")}
        iw = interval("write_amplification"); ir = interval("point_read_amplification")
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
print("\nD-12 predictions")
p1 = all(i["w_reward"] is not None and abs(i["w_reward"] / i["w_eval"] - 1) <= 0.005 for i in per_arm)
print(f"1  reward W == evaluator measured-phase W within 0.5%, every arm:      {V(p1)}")
p2 = all(any(i["final"][k] < i["peak"].get(k, 0) - 1e-9 for k in i["final"]) for i in per_arm) \
     and all(max(i["peak"].values()) < LAMBDA_MAX for i in per_arm) \
     and all(i["final"]["latency"] <= 5.0 for i in per_arm)
print(f"2  no multiplier is a ratchet or rails; lambda_lat ends <= 5:         {V(p2)}")
p3 = all(i["early_deep"] == 0 for i in per_arm) and all(i["l0_pro_n"] == 0 or i["l0_pro_files"] >= 2.0 for i in per_arm)
print(f"3  zero early deep releases; L0 proactive compacts at >= 2 files:      {V(p3)}")
p4a = all(max(i["final"], key=i["final"].get) == "write" for i in rl)
p4b_cells = [i for i in rl if rows[(i["T"], "rl")]["dW"] <= 2.0]
p4b = all(i["final"]["write"] < i["peak"].get("write", 0) - 1e-9 for i in p4b_cells) if p4b_cells else True
print(f"4  lambda_W largest at end of every rl arm (D-8 p2): {V(p4a)};  falls where W ends inside 2%: {V(p4b)} ({len(p4b_cells)} arms)")
p5 = all(r["dL"] <= 0.5 for (T, arm), r in rows.items() if arm == "rl")
print(f"5  depth does not inflate on rl (dL <= 0.5) (D-7 p1 / D-8 p4):         {V(p5)}  "
      + str({T: round(r['dL'], 1) for (T, arm), r in rows.items() if arm == 'rl'}))
lim = {2: 6.0, 6: 10.0, 10: 6.0}
p6a = all(rows[(T, "rl")]["dW"] <= lim[T] for T in lim if (T, "rl") in rows)
p6b = all(rows[(T, "unconstrained_rl")]["W"] < rows[(T, "rl")]["W"] for T in lim if (T, "rl") in rows and (T, "unconstrained_rl") in rows)
print(f"6  rl measured dW <= +6/+10/+6% at T=2/6/10 (D-7: +24/+43/+13): {V(p6a)};  unconstrained_rl W < rl W every cell: {V(p6b)}")
p7 = all(rows[(T, "rl")]["dR"] <= 2.0 for T in (2, 10) if (T, "rl") in rows)
print(f"7  rl dR <= +2% at the trigger-2 cells (conditional on 5):            {V(p7)}")
print("8  E-5 above 1% in >= 2 cells (D-8 p5): see gate1/d12_conditional.txt")
p12 = all(i["lam_lat_whole_run"] > i["final"]["latency"] for i in per_arm)
print(f"12 whole-run-form lambda_lat (offline) exceeds the recorded since-warm-up one, every arm: {V(p12)}  "
      + str([round(i["lam_lat_whole_run"], 1) for i in per_arm]))
p13 = all(all(abs(r - 1.0) <= 0.10 for r in i["since_ratio"].values()) and len(i["since_ratio"]) == 3 for i in per_arm)
print(f"13 since-warm-up latency within +/-10% of the trajectory's end, every op, every arm: {V(p13)}  "
      + str([{k: round(v, 3) for k, v in i["since_ratio"].items()} for i in per_arm]))
p9a = all(i["l0_due_defer"] > 0 for i in rl)
frac = lambda xs: mean([i["l0_due_defer"] / i["l0_due"] for i in xs if i["l0_due"]])
p9b = frac(un) > frac(rl) if rl and un else False
print(f"9  rl defers a due L0 on > 0 frames: {V(p9a)};  unconstrained defers a larger share "
      f"({frac(un) if un else float('nan'):.3f} vs {frac(rl) if rl else float('nan'):.3f}): {V(p9b)}")
PY

echo
echo "DONE. Artifacts:"
echo "  $SMOKE/                        1 smoke arm"
echo "  $LEARN/                        18 learned arms"
echo "  gate1/d12_verdict.txt                         D-12 predictions 1-7, 9, 10"
echo "  gate1/d12_conditional.json/.txt               E-5 (D-12 prediction 8)"
