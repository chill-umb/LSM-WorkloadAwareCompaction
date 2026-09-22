#!/usr/bin/env bash
# E-5 conditional-rate probe (all cells) -> verdict -> C-5 capacity calibration.
# Analysis-only stages never abort the chain: a failing cell is a result.
set -Eeuo pipefail
cd "$(dirname "$0")"
PIPE=scripts/dbbench_pipeline
mkdir -p gate1

cap_vector() {  # $1 = interior scale; L0 and the final level pinned to 1.0
  local s="$1" n=13 out="1.0" i
  for ((i = 1; i < n - 1; i++)); do out="$out,$s"; done
  printf '%s,1.0\n' "$out"
}

verify_applied() {  # $1 = results root, $2 = expected vector
  python3 - "$1" "$2" <<'PY'
import json, sys
from pathlib import Path
root, want = Path(sys.argv[1]), [float(v) for v in sys.argv[2].split(",")]
n = 0
for marker in sorted(root.glob("**/COMPLETED")):
    m = marker.parent / "compaction_measurements.json"
    if not m.exists():
        continue
    rel = json.loads(m.read_text()).get("releases") or []
    if not rel:
        raise SystemExit(f"{marker.parent}: no release events")
    got = [float(v) for v in rel[0]["capacity_scales"]]
    if got != want:
        raise SystemExit(
            f"{marker.parent}: requested {want} but RocksDB applied {got}.\n"
            "The expansion never reached MaxBytesForLevel; stop and fix the "
            "vector shape before burning the rest of the sweep.")
    n += 1
print(f"  capacity vector verified on {n} arm(s): {want}")
PY
}

echo "=============================================================="
echo "PHASE 1/4  prior_only + unconstrained_prior_only"
echo "           10M x T=2/6/10 x 3 repeats = 18 arms"
echo "  Written into the FINAL C-3 matrix root, not a probe root: seeds"
echo "  derive from the repeat index (03:566), so these become repeats"
echo "  01-03 of the eventual ten and are not discarded."
echo "  Both arms run in eval mode (03:688) - the learner never trains,"
echo "  so stage 13 is not a prerequisite. 03 validates learner health"
echo "  per arm (03:877), so a bad manifest fails on arm 1, not arm 18."
echo "=============================================================="
WORKLOAD_SIZES_M=10 SIZE_RATIOS="2 6 10" \
EXPERIMENT_ARMS="prior_only unconstrained_prior_only" \
REPEATS=3 \
BASELINE_SLO_DIR=baseline_slo \
RESULTS_ROOT=results/paired-assoc \
DB_ROOT=.dbbench_pipeline_dbs/paired-assoc \
RESUME="${RESUME:-1}" CONFIRM_EXPERIMENTS=YES \
"$PIPE/03_run_experiments.sh"

echo
echo "=============================================================="
echo "PHASE 2/4  Stage 17: marginal + conditional override rates"
echo "=============================================================="
s17_rc=0
"$PIPE/17_analyze_shadow_overrides.py" \
  results/paired-assoc/10M/T*/repeat-*/prior_only \
  results/paired-assoc/10M/T*/repeat-*/unconstrained_prior_only \
  --manifest-root baseline_slo \
  --output gate1/e5_conditional.json > gate1/e5_conditional.txt 2>&1 || s17_rc=$?
echo "  stage 17 exit=$s17_rc (non-zero only flags per-arm errors; see"
echo "  gate1/e5_conditional.txt). Scoring continues regardless."

python3 - <<'PY' | tee gate1/e5_verdict.txt
import json, re
from collections import defaultdict
LIMIT = 0.01
d = json.load(open("gate1/e5_conditional.json"))
agg = defaultdict(lambda: {"chg": 0, "scored": 0, "ovr": 0, "ready": 0,
                           "ub": 0, "interv": 0, "runs": 0, "err": []})
for r in d["arms"]:
    m = re.search(r"/T(\d+)/repeat-\d+/(\S+)$", r.get("result_dir", ""))
    if not m:
        continue
    a = agg[(int(m.group(1)), m.group(2))]
    a["runs"] += 1
    a["err"] += r.get("errors", [])
    a["interv"] += r.get("interventions_applied", 0)
    c = r.get("conditional") or {}
    a["chg"] += c.get("force_would_change_action", 0)
    a["scored"] += c.get("scored_ready_frames", 0)
    a["ub"] += c.get("revoke_upper_bound_frames", 0)
    f = r.get("frames") or {}
    ready = f.get("ready", 0) or 0
    # stage 17 reports the marginal rate as a fraction, not a count; recover
    # the count so repeats pool on frames rather than averaging fractions.
    a["ovr"] += round((r.get("marginal_override_fraction") or 0.0) * ready)
    a["ready"] += ready

print()
print("E-5 CONDITIONAL OVERRIDE RATE  (Corollary E.2; limit 1% per cell)")
print("=" * 94)
hdr = ("cell  arm                        runs    marginal   "
       "CONDITIONAL   upper-bnd  interv  verdict")
print(hdr); print("-" * 94)
verdicts = {}
for (T, arm) in sorted(agg):
    a = agg[(T, arm)]
    if not a["scored"]:
        print(f"T={T:<4} {arm:<26} {a['runs']:>4}    "
              f"{'no scored frames':<44} INDETERMINATE")
        verdicts[(T, arm)] = "INDETERMINATE"
        continue
    cond = a["chg"] / a["scored"]
    marg = a["ovr"] / a["ready"] if a["ready"] else float("nan")
    ub = (a["chg"] + a["ub"]) / a["scored"]
    ok = cond <= LIMIT
    v = "PASS" if ok else "FAIL"
    verdicts[(T, arm)] = v
    print(f"T={T:<4} {arm:<26} {a['runs']:>4}    {marg:>8.4f}   "
          f"{cond:>11.5f}   {ub:>9.5f}  {a['interv']:>6}  {v}")
print("-" * 94)
print("  marginal    = E-1, reported without a pass/fail (retired by D-2)")
print("  CONDITIONAL = E-5, the deciding criterion: force changes the")
print("                policy's action / frames where it could have")
print("  upper-bnd   = conditional + every kSLO frame (counts revokes AND")
print("                SLO-driven forces, so it is conservative)")
print()
dec = [(T, a) for (T, a) in verdicts if a == "prior_only"]
fails = [T for (T, a) in dec if verdicts[(T, a)] != "PASS"]
print("SCOPE: this covers the PRIOR only. D-2 requires E-5 on every learned")
print("arm, and `rl` defers due levels far more often than the prior does")
print("(history 10.7 Finding 3), so its conditional rate could be much")
print("larger. E-5 is not fully decided until `rl` runs.")
print()
print("VERDICT FOR prior_only (which runs with enforcement on):")
if not dec:
    print("  INDETERMINATE - no prior_only arm scored.")
elif not fails:
    print("  E-5 PASSES in every cell. prior_only is the prior, not a")
    print("  guard-modified policy, so C-3/C-4/C-6 may proceed on this")
    print("  binary at ten repeats.")
else:
    print(f"  E-5 FAILS at T={fails}. Report C-3 against")
    print("  unconstrained_prior_only with the conditional rate stated, and")
    print("  batch the guard's C++ change into Gate 2 with a hull re-measure.")
errs = {k: v["err"] for k, v in agg.items() if v["err"]}
if errs:
    print(); print("PER-ARM ERRORS:", json.dumps({str(k): v for k, v in errs.items()}, indent=2))
PY

echo
echo "=============================================================="
echo "PHASE 3/4  C-5 capacity sweep: s in {1.0, 1.5, 2.0}"
echo "           independent of the guard - regular arms, no manifest"
echo "=============================================================="
V15="$(cap_vector 1.5)"
echo "[3a] validation block: s=1.5 at T=2 only (3 arms), vector $V15"
STATIC_CAPACITY_SCALES="$V15" \
WORKLOAD_SIZES_M=10 SIZE_RATIOS="2" EXPERIMENT_ARMS="regular" REPEATS=3 \
RESULTS_ROOT=results/capacity/s1.5 DB_ROOT=.dbbench_pipeline_dbs/capacity/s1.5 \
RESUME=1 CONFIRM_EXPERIMENTS=YES "$PIPE/03_run_experiments.sh"
verify_applied results/capacity/s1.5 "$V15"

echo "[3b] s=1.5 remaining ratios"
STATIC_CAPACITY_SCALES="$V15" \
WORKLOAD_SIZES_M=10 SIZE_RATIOS="2 6 10" EXPERIMENT_ARMS="regular" REPEATS=3 \
RESULTS_ROOT=results/capacity/s1.5 DB_ROOT=.dbbench_pipeline_dbs/capacity/s1.5 \
RESUME=1 CONFIRM_EXPERIMENTS=YES "$PIPE/03_run_experiments.sh"
verify_applied results/capacity/s1.5 "$V15"

V20="$(cap_vector 2.0)"
echo "[3c] s=2.0, vector $V20"
STATIC_CAPACITY_SCALES="$V20" \
WORKLOAD_SIZES_M=10 SIZE_RATIOS="2 6 10" EXPERIMENT_ARMS="regular" REPEATS=3 \
RESULTS_ROOT=results/capacity/s2.0 DB_ROOT=.dbbench_pipeline_dbs/capacity/s2.0 \
RESUME=1 CONFIRM_EXPERIMENTS=YES "$PIPE/03_run_experiments.sh"
verify_applied results/capacity/s2.0 "$V20"

echo "[3d] s=1.0 control (capacity actuator off)"
WORKLOAD_SIZES_M=10 SIZE_RATIOS="2 6 10" EXPERIMENT_ARMS="regular" REPEATS=3 \
RESULTS_ROOT=results/capacity/s1.0 DB_ROOT=.dbbench_pipeline_dbs/capacity/s1.0 \
RESUME=1 CONFIRM_EXPERIMENTS=YES "$PIPE/03_run_experiments.sh"

echo
echo "=============================================================="
echo "PHASE 4/4  Stage 16: Delta S(s) and s_max  (C-5)"
echo "=============================================================="
c5_rc=0
"$PIPE/16_capacity_calibration.py" results/capacity \
  --size-millions 10 --minimum-pairs 3 \
  --output gate1/capacity_calibration.json || c5_rc=$?
echo "  stage 16 exit=$c5_rc"

python3 - <<'PY' | tee -a gate1/e5_verdict.txt
import json, os
p = "gate1/capacity_calibration.json"
if not os.path.exists(p):
    raise SystemExit("C-5: stage 16 produced no report; see the error above.")
d = json.load(open(p))
print()
print("C-5  s_max PER (CELL, RUNG)   [measured garbage-free denominator, D-3]")
print("=" * 70)
print(json.dumps(d.get("s_max", {}), indent=2))
print()
print("C-5 verdict: PASS when s_max is set (not null) at every ratio for the")
print("2% rung - that is the headline rung, and s_max feeds A-Impl-7 and the")
print("preregistered (cell, rung) rule that selects what Gate 3b runs.")
PY

echo
echo "DONE. Artifacts:"
echo "  gate1/e5_conditional.json   full stage-17 report"
echo "  gate1/e5_verdict.txt        E-5 + C-5 verdict tables"
echo "  gate1/capacity_calibration.json"
