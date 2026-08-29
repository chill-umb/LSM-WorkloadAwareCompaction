#!/usr/bin/env bash
# Run the preregistered 3-run calibration + 3-run independent oracle holdout.
# This script invokes experiment code only; it never builds RocksDB/db_bench.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

if [[ "${CONFIRM_GUARD_PROTOCOL:-}" != "YES" ]]; then
  echo "This launches three oracle calibration and three holdout runs per cell." >&2
  echo "Review the roots/seeds, then set CONFIRM_GUARD_PROTOCOL=YES." >&2
  exit 2
fi

CALIBRATION_REPEATS=3
HOLDOUT_REPEATS=3
SELECTION_SLO_ROOT="${SELECTION_SLO_ROOT:-baseline_selection}"
FINAL_SLO_ROOT="${FINAL_SLO_ROOT:-baseline_slo}"
GUARD_RESULTS_ROOT="${GUARD_RESULTS_ROOT:-results/live_guard}"
GUARD_DB_ROOT="${GUARD_DB_ROOT:-.dbbench_pipeline_dbs/live_guard}"
CALIBRATION_SEED_BASE="${CALIBRATION_SEED_BASE:-$DBBENCH_SEED}"
HOLDOUT_SEED_BASE="${HOLDOUT_SEED_BASE:-$(( DBBENCH_SEED + 10000 ))}"

calibration_results="$GUARD_RESULTS_ROOT/$WORKLOAD_PROFILE/calibration"
holdout_results="$GUARD_RESULTS_ROOT/$WORKLOAD_PROFILE/holdout"

echo "[guard calibration] profile=$WORKLOAD_PROFILE repeats=$CALIBRATION_REPEATS"
EXPERIMENT_ARMS=oracle REPEATS="$CALIBRATION_REPEATS" \
RL_RUN_PHASE=calibration DBBENCH_SEED="$CALIBRATION_SEED_BASE" \
BASELINE_SLO_DIR="$SELECTION_SLO_ROOT" \
RESULTS_ROOT="$calibration_results" \
DB_ROOT="$GUARD_DB_ROOT/$WORKLOAD_PROFILE/calibration" \
RESUME="${RESUME:-0}" CONFIRM_EXPERIMENTS=YES \
"$PIPELINE_DIR/03_run_experiments.sh"

for size_m in $WORKLOAD_SIZES_M; do
  for ratio in $SIZE_RATIOS; do
    selection="$SELECTION_SLO_ROOT/$WORKLOAD_PROFILE/${size_m}M/T${ratio}/baseline_slo.json"
    final="$FINAL_SLO_ROOT/$WORKLOAD_PROFILE/${size_m}M/T${ratio}/baseline_slo.json"
    "$PIPELINE_DIR/06_calibrate_live_guard.py" \
      --selection-manifest "$selection" \
      --calibration-results "$calibration_results" \
      --repeats "$CALIBRATION_REPEATS" --output "$final"
  done
done

echo "[guard holdout] profile=$WORKLOAD_PROFILE repeats=$HOLDOUT_REPEATS"
EXPERIMENT_ARMS=oracle REPEATS="$HOLDOUT_REPEATS" \
RL_RUN_PHASE=holdout DBBENCH_SEED="$HOLDOUT_SEED_BASE" \
BASELINE_SLO_DIR="$FINAL_SLO_ROOT" \
RESULTS_ROOT="$holdout_results" \
DB_ROOT="$GUARD_DB_ROOT/$WORKLOAD_PROFILE/holdout" \
RESUME="${RESUME:-0}" CONFIRM_EXPERIMENTS=YES \
"$PIPELINE_DIR/03_run_experiments.sh"

for size_m in $WORKLOAD_SIZES_M; do
  for ratio in $SIZE_RATIOS; do
    manifest="$FINAL_SLO_ROOT/$WORKLOAD_PROFILE/${size_m}M/T${ratio}/baseline_slo.json"
    output="$holdout_results/readiness-${size_m}M-T${ratio}.json"
    "$PIPELINE_DIR/06_validate_guard_holdout.py" \
      --manifest "$manifest" --holdout-results "$holdout_results" \
      --repeats "$HOLDOUT_REPEATS" --output "$output"
  done
done

echo "Guard calibration and independent holdout passed: $WORKLOAD_PROFILE"
