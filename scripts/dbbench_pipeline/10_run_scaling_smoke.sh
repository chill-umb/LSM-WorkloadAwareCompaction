#!/usr/bin/env bash
# Scaling smoke test: does the learned residual do anything, and does it get
# better with more data?
#
# Runs a ladder of workload sizes at one size ratio with three arms:
#   regular     -- native leveled comparator
#   prior_only  -- the SAME analytic prior in eval mode: no training, no
#                  exploration. This is the control. If `rl` does not separate
#                  from it, the learning is inert regardless of how `rl`
#                  compares to `regular`.
#   rl          -- constrained learner
#
# Each size is launched as its own invocation of 03_run_experiments.sh so a
# failure at 60M does not discard 10M-50M, and RESUME=1 skips completed arms.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/config.sh"

SMOKE_SIZES_M="${SMOKE_SIZES_M:-1 5 10 20 30 40 50 60 70 80 90 100}"
SMOKE_RATIO="${SMOKE_RATIO:-2}"
SMOKE_ARMS="${SMOKE_ARMS:-regular prior_only rl}"
SMOKE_REPEATS="${SMOKE_REPEATS:-1}"
SMOKE_DB_ROOT="${SMOKE_DB_ROOT:-/mnt/nvme/smoke-databases}"
SMOKE_RESULTS_ROOT="${SMOKE_RESULTS_ROOT:-/mnt/nvme/smoke-results}"

if [[ "${CONFIRM_SCALING_SMOKE:-}" != "YES" ]]; then
  echo "Refusing to start. Set CONFIRM_SCALING_SMOKE=YES." >&2
  echo "  sizes:   $SMOKE_SIZES_M (M ops), T=$SMOKE_RATIO" >&2
  echo "  arms:    $SMOKE_ARMS" >&2
  echo "  results: $SMOKE_RESULTS_ROOT" >&2
  total=0
  for s in $SMOKE_SIZES_M; do total=$(( total + s )); done
  arms=$(echo "$SMOKE_ARMS" | wc -w)
  echo "  ~$(( total * arms ))M operations total across $arms arms" >&2
  echo "  at roughly 30 s per M ops that is ~$(( total * arms * 30 / 3600 )) hours" >&2
  exit 1
fi

mkdir -p "$SMOKE_RESULTS_ROOT"
started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

for size_m in $SMOKE_SIZES_M; do
  echo "=== ${size_m}M T=${SMOKE_RATIO} ==="
  # No manifest exists yet, so the live SLO mask is uncalibrated. That is
  # acceptable for a learning smoke test and NOT acceptable for an acceptance
  # run: it is recorded here so no later reader mistakes one for the other.
  WORKLOAD_SIZES_M="$size_m" \
  SIZE_RATIOS="$SMOKE_RATIO" \
  EXPERIMENT_ARMS="$SMOKE_ARMS" \
  REPEATS="$SMOKE_REPEATS" \
  RL_REQUIRE_BASELINE_SLO=0 \
  RESUME=1 \
  DB_ROOT="$SMOKE_DB_ROOT" \
  RESULTS_ROOT="$SMOKE_RESULTS_ROOT" \
  CONFIRM_EXPERIMENTS=YES \
    "$HERE/03_run_experiments.sh"
done

{
  printf 'started=%s\n' "$started"
  printf 'finished=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'sizes_m=%s\n' "$SMOKE_SIZES_M"
  printf 'size_ratio=%s\n' "$SMOKE_RATIO"
  printf 'arms=%s\n' "$SMOKE_ARMS"
  printf 'repeats=%s\n' "$SMOKE_REPEATS"
  printf 'baseline_slo_required=0\n'
  printf 'note=uncalibrated SLO mask; learning smoke test only, not an acceptance run\n'
} > "$SMOKE_RESULTS_ROOT/scaling_smoke.env"

echo
echo "Next:"
echo "  $HERE/04_generate_graphs.sh --results $SMOKE_RESULTS_ROOT"
echo "  $HERE/11_analyze_learning.py $SMOKE_RESULTS_ROOT"
