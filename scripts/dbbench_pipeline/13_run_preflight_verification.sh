#!/usr/bin/env bash
# Tier 3, the preflight (plan §6.4; CLAUDE.md "Tests"). Runs the checks in
# order and writes the marker PREFLIGHT_PASSED, bound to the db_bench, plugin
# and code hashes, which 03_run_experiments.sh requires before any long run.
#
#   1 rebuild the Release db_bench (and the plugin)   4 parity, ACT-4 and ARCH-5,
#   2 tier 1 and tier 2 suites                          and the evaluator smoke
#   3 ACT-1 on the real binary                        5 rules-mode smoke
#                                                     6 learner smoke
#
# A step whose component does not exist yet is skipped and recorded as skipped;
# a run whose arms need that step is then refused by 03. A step whose component
# exists but whose check is not wired in here fails, so nothing passes by
# omission. Wire each step in the same change that builds its component.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

if [[ "${CONFIRM_PREFLIGHT_VERIFICATION:-}" != "YES" ]]; then
  echo "This rebuilds db_bench, builds the Debug test tree and runs the" >&2
  echo "preflight; the marker goes to $PREFLIGHT_MARKER." >&2
  echo "Set CONFIRM_PREFLIGHT_VERIFICATION=YES to start." >&2
  exit 2
fi

if [[ "$PYTHON_VENV" = /* ]]; then
  PYTHON="$PYTHON_VENV/bin/python"
else
  PYTHON="$PROJECT_ROOT/$PYTHON_VENV/bin/python"
fi
[[ -x "$PYTHON" ]] || { echo "Missing $PYTHON; run step 00 first." >&2; exit 1; }
DB_BENCH="$DBBENCH_BUILD_DIR/db_bench"

passed=()
skipped=()
pass() { echo "[step $1] PASS"; passed+=("$1"); }
skip() { echo "[step $1] SKIP: $2"; skipped+=("$1=$2"); }
unwired() {
  echo "[step $1] FAIL: $2 exists, but this step is not wired into 13 yet" \
       "(plan §6.4)." >&2
  exit 1
}

# A stale marker must not outlive a failed preflight.
rm -f "$PREFLIGHT_MARKER"

echo "=== step 1: rebuild the Release db_bench and the plugin ==="
"$PIPELINE_DIR/01_build_rocksdb.sh"
"$PIPELINE_DIR/02_build_db_bench.sh"
echo "db_bench sha256: $(sha256sum "$DB_BENCH" | awk '{print $1}')"
if [[ -d controller ]]; then
  # The plugin needs only the fork's headers, so it has its own small tree.
  # Step 7 binds its hash into the marker, and 03 checks it.
  cmake -S controller -B "$CONTROLLER_BUILD_DIR" -DCMAKE_BUILD_TYPE=Release
  cmake --build "$CONTROLLER_BUILD_DIR" --target rl_controller \
    --parallel "$BUILD_JOBS"
  echo "plugin sha256: $(sha256sum "$CONTROLLER_PLUGIN" | awk '{print $1}')"
fi
pass 1

echo "=== step 2: tier 1 and tier 2 suites ==="
"$PIPELINE_DIR/run_python_tests.sh"
"$PIPELINE_DIR/01b_build_test_trees.sh"
pass 2

echo "=== step 3: ACT-1 on the real binary ==="
"$PYTHON" "$PIPELINE_DIR/20_check_actuation.py" --db-bench "$DB_BENCH" \
  --work-dir "$PREFLIGHT_WORK_DIR/act1" \
  --output "$PREFLIGHT_WORK_DIR/act1_report.json"
pass 3

echo "=== step 4: parity at 1M, T=2 ==="
# ACT-4: the patched binary at m = 1 against stock RocksDB. ARCH-5: the
# plugin in hold-only mode against the patched binary's native arm, the same
# pairs and checks (22 with PARITY_CHECK=arch5).
"$PIPELINE_DIR/01c_build_stock_db_bench.sh"
parity_checks=(act4)
[[ ! -d controller ]] || parity_checks+=(arch5)
for check in "${parity_checks[@]}"; do
  parity=0
  PARITY_CHECK="$check" "$PIPELINE_DIR/22_check_native_parity.sh" || parity=$?
  if (( parity == 2 )); then
    echo "[step 4] FAIL: $check undecided; the paired spread needs more pairs" \
         "than PARITY_PAIRS=$PARITY_PAIRS (see required_pairs in" \
         "$PREFLIGHT_WORK_DIR/$check/${check}_report.json)." >&2
  fi
  (( parity == 0 )) || exit 1
done
# The evaluator (Gate N0 item 5): one 03 native arm at 1M, T=2, run exactly
# as a Gate N2 arm (settle, host log, stamps) and scored by 04, whose
# self-checks must pass. 04 exits non-zero if it refuses the arm.
EVALUATOR_RESULTS="$PREFLIGHT_WORK_DIR/evaluator"
rm -rf "$EVALUATOR_RESULTS" "$DB_ROOT/preflight-evaluator"
CONFIRM_EXPERIMENTS=YES EXPERIMENT_ARMS=native WORKLOAD_SIZES_M=1 \
  SIZE_RATIOS=2 REPEATS=1 RESUME=0 RESULTS_ROOT="$EVALUATOR_RESULTS" \
  DB_ROOT="$DB_ROOT/preflight-evaluator" "$PIPELINE_DIR/03_run_experiments.sh"
"$PYTHON" "$PIPELINE_DIR/04_generate_graphs.py" \
  --results "$EVALUATOR_RESULTS" --summary-only
"$PYTHON" - "$EVALUATOR_RESULTS/graphs/summary.csv" <<'PY'
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1])))
if len(rows) != 1 or rows[0]["settle_ok"] != "1":
    raise SystemExit("[step 4] FAIL: the native arm was not scored as a "
                     "Programme 1 arm (no settled measured phase)")
print(f"[step 4] evaluator: {rows[0]['measured_operations']} operations, "
      f"throughput {float(rows[0]['throughput_ops_per_second']):.0f} ops/s, "
      f"stall fraction {float(rows[0]['stall_fraction']):.4f}")
PY
pass 4

echo "=== step 5: rules-mode smoke at 1M, T=2 ==="
# One 03 rules arm with every rule on, its config completed by smoke
# placeholders (the D-18 bounds and the Gate N3 values are not decided yet,
# and prices may not exist). 03 itself runs 28 on it (plugin started and
# stopped, complete logs, no masked action, no fallback); here 28 also
# requires at least one requested change, and ACT-3: 99% of changes in the
# published score within one control interval. 04 must score the arm as a
# Programme 1 arm. Then the fallback (A-Impl-8) end to end: a rules arm whose
# bounds the plugin refuses (epsilon 2) must run to the drain in fallback,
# which 03's own check refuses (exit 8) and 28 --expect-fallback accepts.
# The fallback on a bad weights file joins with the learned mode (step 6).
if [[ ! -d controller ]]; then
  skip 5 "no controller/ plugin (plan §3)"
else
  SMOKE_RESULTS="$PREFLIGHT_WORK_DIR/rules_smoke"
  rm -rf "$SMOKE_RESULTS" "$DB_ROOT/preflight-rules-smoke"
  CONFIRM_EXPERIMENTS=YES EXPERIMENT_ARMS=rules WORKLOAD_SIZES_M=1 \
    SIZE_RATIOS=2 REPEATS=1 RESUME=0 PLUGIN_PLACEHOLDERS=1 \
    RESULTS_ROOT="$SMOKE_RESULTS" DB_ROOT="$DB_ROOT/preflight-rules-smoke" \
    "$PIPELINE_DIR/03_run_experiments.sh"
  smoke_arm="$SMOKE_RESULTS/1M/T2/rules"
  "$PYTHON" "$PIPELINE_DIR/28_check_plugin_run.py" \
    --stdout "$smoke_arm/run.log" --decisions "$smoke_arm/decisions.jsonl" \
    --transitions "$smoke_arm/transitions.jsonl" --mode rules \
    --min-changes 1 --output "$PREFLIGHT_WORK_DIR/rules_smoke_report.json"

  FALLBACK_RESULTS="$PREFLIGHT_WORK_DIR/rules_fallback"
  rm -rf "$FALLBACK_RESULTS" "$DB_ROOT/preflight-rules-fallback"
  mkdir -p "$FALLBACK_RESULTS"
  echo '{"epsilon": 2.0}' > "$FALLBACK_RESULTS/refused_bounds.json"
  fallback=0
  CONFIRM_EXPERIMENTS=YES EXPERIMENT_ARMS=rules WORKLOAD_SIZES_M=1 \
    SIZE_RATIOS=2 REPEATS=1 RESUME=0 PLUGIN_PLACEHOLDERS=1 \
    ACTION_BOUNDS_FILE="$FALLBACK_RESULTS/refused_bounds.json" \
    RESULTS_ROOT="$FALLBACK_RESULTS/results" \
    DB_ROOT="$DB_ROOT/preflight-rules-fallback" \
    "$PIPELINE_DIR/03_run_experiments.sh" || fallback=$?
  fallback_arm="$FALLBACK_RESULTS/results/1M/T2/rules"
  (( fallback == 8 )) || {
    echo "[step 5] FAIL: the refused-config arm ended with exit $fallback," \
         "not 03's plugin-check refusal (8)" >&2
    exit 1
  }
  "$PYTHON" "$PIPELINE_DIR/28_check_plugin_run.py" \
    --stdout "$fallback_arm/run.log" \
    --decisions "$fallback_arm/decisions.jsonl" \
    --transitions "$fallback_arm/transitions.jsonl" --mode rules \
    --expect-fallback --output "$PREFLIGHT_WORK_DIR/rules_fallback_report.json"
  rm -rf "$DB_ROOT/preflight-rules-fallback"
  "$PYTHON" "$PIPELINE_DIR/04_generate_graphs.py" \
    --results "$SMOKE_RESULTS" --summary-only
  "$PYTHON" - "$SMOKE_RESULTS/graphs/summary.csv" <<'PY'
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1])))
if len(rows) != 1 or rows[0]["settle_ok"] != "1":
    raise SystemExit("[step 5] FAIL: the rules arm was not scored as a "
                     "Programme 1 arm (no settled measured phase)")
print(f"[step 5] evaluator: {rows[0]['measured_operations']} operations, "
      f"throughput {float(rows[0]['throughput_ops_per_second']):.0f} ops/s")
PY
  pass 5
fi
skip 6 "no learned mode yet (plan §7 step 10)"

echo "=== step 7: write the marker ==="
# The plugin built in step 1 is bound into the marker (plan §6.4 step 7);
# without controller/ the marker records it as absent, as before.
plugin_args=()
if [[ -d controller ]]; then plugin_args=(--plugin "$CONTROLLER_PLUGIN"); fi
"$PYTHON" "$PIPELINE_DIR/preflight_marker.py" write \
  --marker "$PREFLIGHT_MARKER" --db-bench "$DB_BENCH" "${plugin_args[@]}" \
  --passed "${passed[@]}" --skipped "${skipped[@]}"
echo "passed: ${passed[*]}"
echo "skipped: ${skipped[*]:-none}"
echo "03 accepts a long run only when every step its arms need passed:"
echo "static arms need 1-4, rules also 5, learned arms 1-6."
