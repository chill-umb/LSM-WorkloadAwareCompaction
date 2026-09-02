#!/usr/bin/env bash
# Calibrate and run the preregistered read-heavy/write-heavy safety suites.
# This script invokes only experiment scripts; it never builds the binaries.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

if [[ "${CONFIRM_STRESS_SUITES:-}" != "YES" ]]; then
  echo "This runs two complete baseline grids and paired safety suites." >&2
  echo "Review this script, then set CONFIRM_STRESS_SUITES=YES." >&2
  exit 2
fi

STRESS_ROOT="${STRESS_ROOT:-results/stress_suites}"
STRESS_DB_ROOT="${STRESS_DB_ROOT:-.dbbench_pipeline_dbs/stress_suites}"
STRESS_SLO_ROOT="${STRESS_SLO_ROOT:-baseline_slo}"
STRESS_SELECTION_SLO_ROOT="${STRESS_SELECTION_SLO_ROOT:-baseline_selection}"
STRESS_BASELINE_REPEATS="${STRESS_BASELINE_REPEATS:-3}"
STRESS_FINAL_REPEATS="${STRESS_FINAL_REPEATS:-10}"

run_profile() {  # profile, get ratio, put ratio, seek ratio
  local profile="$1" get_ratio="$2" put_ratio="$3" seek_ratio="$4"
  local baseline_results="$STRESS_ROOT/$profile/baseline"
  local final_results="$STRESS_ROOT/$profile/final"

  echo "[stress baseline] $profile"
  WORKLOAD_PROFILE="$profile" \
  MIX_GET_RATIO="$get_ratio" MIX_PUT_RATIO="$put_ratio" \
  MIX_SEEK_RATIO="$seek_ratio" \
  BASELINE_REPEATS="$STRESS_BASELINE_REPEATS" \
  BASELINE_RESULTS_ROOT="$baseline_results" \
  BASELINE_DB_ROOT="$STRESS_DB_ROOT/$profile/baseline" \
  RESUME="${RESUME:-0}" CONFIRM_BASELINE_SWEEP=YES \
  "$PIPELINE_DIR/05_run_baseline_sweep.sh"

  for size_m in $WORKLOAD_SIZES_M; do
    for ratio in $SIZE_RATIOS; do
      "$PIPELINE_DIR/06_select_baseline_slo.py" \
        --baseline-results "$baseline_results" \
        --workload-profile "$profile" \
        --size-millions "$size_m" --size-ratio "$ratio" \
        --minimum-repeats "$STRESS_BASELINE_REPEATS" \
        --output "$STRESS_SELECTION_SLO_ROOT/$profile/${size_m}M/T${ratio}/baseline_slo.json"
    done
  done

  echo "[stress guard calibration + holdout] $profile"
  # The holdout is expected to fail while guard readiness is unresolved, and
  # under set -e that would abort the suite before the safety comparison this
  # script exists to run. 06_calibrate_live_guard.py writes the final manifest
  # before the holdout, so gate on the manifest instead of the exit code --
  # the same treatment 13_run_preflight_verification.sh applies.
  set +e
  WORKLOAD_PROFILE="$profile" \
  MIX_GET_RATIO="$get_ratio" MIX_PUT_RATIO="$put_ratio" \
  MIX_SEEK_RATIO="$seek_ratio" \
  SELECTION_SLO_ROOT="$STRESS_SELECTION_SLO_ROOT" \
  FINAL_SLO_ROOT="$STRESS_SLO_ROOT" \
  GUARD_RESULTS_ROOT="$STRESS_ROOT/guard" \
  GUARD_DB_ROOT="$STRESS_DB_ROOT/guard" \
  RESUME="${RESUME:-0}" CONFIRM_GUARD_PROTOCOL=YES \
  "$PIPELINE_DIR/06_run_guard_protocol.sh"
  local guard_status=$?
  set -e
  if (( guard_status != 0 )); then
    echo "[stress] $profile guard holdout not ready (exit $guard_status);" \
         "the safety comparison still runs against the calibrated manifest." >&2
  fi
  for size_m in $WORKLOAD_SIZES_M; do
    for ratio in $SIZE_RATIOS; do
      local m="$STRESS_SLO_ROOT/$profile/${size_m}M/T${ratio}/baseline_slo.json"
      [[ -f "$m" ]] || { echo "[stress] $profile: missing $m" >&2; return 1; }
    done
  done

  echo "[stress paired] $profile"
  WORKLOAD_PROFILE="$profile" \
  MIX_GET_RATIO="$get_ratio" MIX_PUT_RATIO="$put_ratio" \
  MIX_SEEK_RATIO="$seek_ratio" \
  EXPERIMENT_ARMS="regular rl" REPEATS="$STRESS_FINAL_REPEATS" \
  RL_RUN_PHASE=experiment \
  BASELINE_SLO_DIR="$STRESS_SLO_ROOT" \
  RESULTS_ROOT="$final_results" DB_ROOT="$STRESS_DB_ROOT/$profile/final" \
  RESUME="${RESUME:-0}" CONFIRM_EXPERIMENTS=YES \
  "$PIPELINE_DIR/03_run_experiments.sh"

  "$PIPELINE_DIR/04_generate_graphs.sh" --results "$final_results"
  for size_m in $WORKLOAD_SIZES_M; do
    for ratio in $SIZE_RATIOS; do
      "$PIPELINE_DIR/07_evaluate_paired.py" \
        "$final_results/graphs/summary.csv" \
        --size-millions "$size_m" --size-ratio "$ratio" \
        --minimum-pairs "$STRESS_FINAL_REPEATS" --safety-only \
        --output "$final_results/graphs/acceptance-${size_m}M-T${ratio}.json"
    done
  done
}

run_profile read-heavy-v1 0.85 0.05 0.10
run_profile write-heavy-v1 0.15 0.75 0.10

echo "Stress suites complete: $STRESS_ROOT"
