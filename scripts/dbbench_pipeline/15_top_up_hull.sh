#!/usr/bin/env bash
# Raise hull points to their required repeat count, skipping arms already done.
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

HULL_DIR="${HULL_DIR:-gate1}"
BASELINE_RESULTS_ROOT="${BASELINE_RESULTS_ROOT:-results/baseline_sweep}"
BASELINE_DB_ROOT="${BASELINE_DB_ROOT:-.dbbench_pipeline_dbs/baseline_sweep}"
TOPUP_TSV="$HULL_DIR/topup.tsv"

if [[ "$PYTHON_VENV" = /* ]]; then PYTHON="$PYTHON_VENV/bin/python"
else PYTHON="$PROJECT_ROOT/$PYTHON_VENV/bin/python"; fi
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"

"$PYTHON" "$PIPELINE_DIR/15_top_up_hull.py" --hull-dir "$HULL_DIR" \
  --output "$TOPUP_TSV" ${TOP_UP_ADD_CAP:+--add-cap "$TOP_UP_ADD_CAP"}

if (( DRY_RUN )); then
  echo "[dry-run] would run the configurations listed in $TOPUP_TSV"
  cat "$TOPUP_TSV"
  exit 0
fi
if [[ ! -s "$TOPUP_TSV" ]]; then
  echo "Nothing to top up."
  exit 0
fi
if [[ "${CONFIRM_TOP_UP:-}" != "YES" ]]; then
  echo "Review $TOPUP_TSV, then set CONFIRM_TOP_UP=YES." >&2
  exit 2
fi

while IFS=$'\t' read -r ratio trigger scale repeats; do
  [[ -n "$ratio" ]] || continue
  echo "[top-up] T=$ratio trigger=$trigger scale=$scale repeats=$repeats"
  RESUME=1 \
  BASELINE_REPEATS="$repeats" \
  SIZE_RATIOS="$ratio" \
  BASELINE_L0_COMPACTION_TRIGGERS="$trigger" \
  BASELINE_LEVEL_BASE_SCALES="$scale" \
  BASELINE_RESULTS_ROOT="$BASELINE_RESULTS_ROOT" \
  BASELINE_DB_ROOT="$BASELINE_DB_ROOT" \
  CONFIRM_BASELINE_SWEEP=YES \
  "$PIPELINE_DIR/05_run_baseline_sweep.sh" </dev/null
done < "$TOPUP_TSV"

echo "Top-up complete. Re-extract the hulls before reading C-2."
