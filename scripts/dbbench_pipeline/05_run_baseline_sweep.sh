#!/usr/bin/env bash
# Run the preregistered regular-leveled grid used to choose the comparator.
# This script does not build anything.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

if [[ "${CONFIRM_BASELINE_SWEEP:-}" != "YES" ]]; then
  echo "Review the baseline grid, then set CONFIRM_BASELINE_SWEEP=YES." >&2
  exit 2
fi

BASELINE_RESULTS_ROOT="${BASELINE_RESULTS_ROOT:-results/baseline_sweep}"
BASELINE_DB_ROOT="${BASELINE_DB_ROOT:-.dbbench_pipeline_dbs/baseline_sweep}"
BASELINE_REPEATS="${BASELINE_REPEATS:-3}"
BASELINE_L0_COMPACTION_TRIGGERS="${BASELINE_L0_COMPACTION_TRIGGERS:-4 8}"
BASELINE_L0_SLOWDOWN_TRIGGERS="${BASELINE_L0_SLOWDOWN_TRIGGERS:-20 24}"
BASELINE_L0_STOP_TRIGGERS="${BASELINE_L0_STOP_TRIGGERS:-36 40}"
BASELINE_COMPACTION_PRIORITIES="${BASELINE_COMPACTION_PRIORITIES:-1 3}"

mkdir -p "$BASELINE_RESULTS_ROOT" "$BASELINE_DB_ROOT"
for ratio in $SIZE_RATIOS; do
  for compact_trigger in $BASELINE_L0_COMPACTION_TRIGGERS; do
    for slowdown_trigger in $BASELINE_L0_SLOWDOWN_TRIGGERS; do
      for stop_trigger in $BASELINE_L0_STOP_TRIGGERS; do
        if (( slowdown_trigger < compact_trigger ||
              stop_trigger < slowdown_trigger )); then
          continue
        fi
        for priority in $BASELINE_COMPACTION_PRIORITIES; do
          config_id="T${ratio}-l0-${compact_trigger}-${slowdown_trigger}-${stop_trigger}-pri${priority}"
          result_root="$BASELINE_RESULTS_ROOT/$config_id"
          db_root="$BASELINE_DB_ROOT/$config_id"
          echo "[baseline] $config_id"
          WORKLOAD_SIZES_M="$WORKLOAD_SIZES_M" \
          SIZE_RATIOS="$ratio" \
          EXPERIMENT_ARMS=regular \
          REPEATS="$BASELINE_REPEATS" \
          L0_COMPACTION_TRIGGER="$compact_trigger" \
          L0_SLOWDOWN_TRIGGER="$slowdown_trigger" \
          L0_STOP_TRIGGER="$stop_trigger" \
          COMPACTION_PRIORITY="$priority" \
          RESULTS_ROOT="$result_root" \
          DB_ROOT="$db_root" \
          RESUME="${RESUME:-0}" \
          RL_REQUIRE_BASELINE_SLO=0 \
          CONFIRM_EXPERIMENTS=YES \
          "$PIPELINE_DIR/03_run_experiments.sh"
        done
      done
    done
  done
done

echo "Baseline grid complete: $BASELINE_RESULTS_ROOT"
echo "Select each workload/T comparator with 06_select_baseline_slo.py."
