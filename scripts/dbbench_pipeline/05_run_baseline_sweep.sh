#!/usr/bin/env bash
# Run the preregistered regular-leveled grid used to choose the comparator.
# This script does not build anything.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
  shift
fi
[[ $# -eq 0 ]] || { echo "Usage: $0 [--dry-run]" >&2; exit 2; }
if (( ! DRY_RUN )) && [[ "${CONFIRM_BASELINE_SWEEP:-}" != "YES" ]]; then
  echo "Review the baseline grid, then set CONFIRM_BASELINE_SWEEP=YES." >&2
  exit 2
fi

BASELINE_RESULTS_ROOT="${BASELINE_RESULTS_ROOT:-results/baseline_sweep}"
BASELINE_DB_ROOT="${BASELINE_DB_ROOT:-.dbbench_pipeline_dbs/baseline_sweep}"
BASELINE_REPEATS="${BASELINE_REPEATS:-3}"
BASELINE_L0_COMPACTION_TRIGGERS="${BASELINE_L0_COMPACTION_TRIGGERS:-2 4 8 16}"
BASELINE_L0_SLOWDOWN_TRIGGERS="${BASELINE_L0_SLOWDOWN_TRIGGERS:-20}"
BASELINE_L0_STOP_TRIGGERS="${BASELINE_L0_STOP_TRIGGERS:-36}"
BASELINE_COMPACTION_PRIORITIES="${BASELINE_COMPACTION_PRIORITIES:-3}"
BASELINE_LEVEL_BASE_SCALES="${BASELINE_LEVEL_BASE_SCALES:-0.5 1 2}"
# Gate 1 is a 10M calibration, not the operational five-size matrix.
BASELINE_WORKLOAD_SIZES_M="${BASELINE_WORKLOAD_SIZES_M:-10}"

if (( ! DRY_RUN )); then
  mkdir -p "$BASELINE_RESULTS_ROOT" "$BASELINE_DB_ROOT"
fi
for ratio in $SIZE_RATIOS; do
  for compact_trigger in $BASELINE_L0_COMPACTION_TRIGGERS; do
    for slowdown_trigger in $BASELINE_L0_SLOWDOWN_TRIGGERS; do
      for stop_trigger in $BASELINE_L0_STOP_TRIGGERS; do
        if (( slowdown_trigger < compact_trigger ||
              stop_trigger < slowdown_trigger )); then
          continue
        fi
        for priority in $BASELINE_COMPACTION_PRIORITIES; do
         for base_scale in $BASELINE_LEVEL_BASE_SCALES; do
          base_bytes="$(awk -v base="$MAX_BYTES_FOR_LEVEL_BASE" -v scale="$base_scale" \
            'BEGIN { if (scale <= 0 || base <= 0) exit 1; printf "%.0f", base * scale }')"
          config_id="T${ratio}-l0-${compact_trigger}-${slowdown_trigger}-${stop_trigger}-pri${priority}-base${base_bytes}"
          result_root="$BASELINE_RESULTS_ROOT/$config_id"
          db_root="$BASELINE_DB_ROOT/$config_id"
          if (( DRY_RUN )); then
            printf '%s size_millions=%s repeats=%s base_scale=%s\n' \
              "$config_id" "$BASELINE_WORKLOAD_SIZES_M" "$BASELINE_REPEATS" "$base_scale"
            continue
          fi
          echo "[baseline] $config_id"
          WORKLOAD_SIZES_M="$BASELINE_WORKLOAD_SIZES_M" \
          SIZE_RATIOS="$ratio" \
          EXPERIMENT_ARMS=regular \
          REPEATS="$BASELINE_REPEATS" \
          L0_COMPACTION_TRIGGER="$compact_trigger" \
          L0_SLOWDOWN_TRIGGER="$slowdown_trigger" \
          L0_STOP_TRIGGER="$stop_trigger" \
          COMPACTION_PRIORITY="$priority" \
          MAX_BYTES_FOR_LEVEL_BASE="$base_bytes" \
          BASELINE_LEVEL_BASE_SCALE="$base_scale" \
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
done

(( ! DRY_RUN )) || exit 0

echo "Baseline grid complete: $BASELINE_RESULTS_ROOT"
echo "Select each workload/T comparator with 06_select_baseline_slo.py."
