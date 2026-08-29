#!/usr/bin/env bash
# Cloud-only, staged verification before launching the full multi-day matrix.
#
# Default sequence:
#   1. 1M/T2 regular-vs-oracle bridge regression (3 pairs).
#   2. 5M/T2 narrowed baseline, 3+3 live-guard protocol, 1 four-arm repeat.
#   3. 10M/T2 narrowed baseline, 3+3 live-guard protocol, 3 four-arm repeats.
#
# This script never builds RocksDB/db_bench. It requires freshly rebuilt cloud
# binaries and invokes only the existing experiment/analysis entry points.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

PREFLIGHT_ROOT="${PREFLIGHT_ROOT:-/mnt/nvme/lsm-guardfix-verification}"
PREFLIGHT_RESULTS_ROOT="${PREFLIGHT_RESULTS_ROOT:-$PREFLIGHT_ROOT/results}"
PREFLIGHT_DB_ROOT="${PREFLIGHT_DB_ROOT:-$PREFLIGHT_ROOT/databases}"
PREFLIGHT_MANIFEST_ROOT="${PREFLIGHT_MANIFEST_ROOT:-$PREFLIGHT_ROOT/manifests}"
PREFLIGHT_WORKLOAD_PROFILE="${PREFLIGHT_WORKLOAD_PROFILE:-balanced-v1}"
PREFLIGHT_CELLS_M="${PREFLIGHT_CELLS_M:-5 10}"
PREFLIGHT_RUN_ORACLE="${PREFLIGHT_RUN_ORACLE:-1}"
PREFLIGHT_ORACLE_REPEATS="${PREFLIGHT_ORACLE_REPEATS:-3}"
PREFLIGHT_FINAL_REPEATS_5M="${PREFLIGHT_FINAL_REPEATS_5M:-1}"
PREFLIGHT_FINAL_REPEATS_10M="${PREFLIGHT_FINAL_REPEATS_10M:-3}"
PREFLIGHT_MIN_TRAIN_STEPS="${PREFLIGHT_MIN_TRAIN_STEPS:-100}"
PREFLIGHT_MIN_FINALIZED_TRANSITIONS="${PREFLIGHT_MIN_FINALIZED_TRANSITIONS:-320}"
PREFLIGHT_MAX_INVALID_FRACTION="${PREFLIGHT_MAX_INVALID_FRACTION:-0.01}"
PREFLIGHT_REQUIRE_10M_ACTION_FLIP="${PREFLIGHT_REQUIRE_10M_ACTION_FLIP:-1}"
PREFLIGHT_RESUME="${PREFLIGHT_RESUME:-0}"
PREFLIGHT_KEEP_DATABASES="${PREFLIGHT_KEEP_DATABASES:-0}"
PREFLIGHT_CALIBRATION_SEED_BASE="${PREFLIGHT_CALIBRATION_SEED_BASE:-1001}"
PREFLIGHT_HOLDOUT_SEED_BASE="${PREFLIGHT_HOLDOUT_SEED_BASE:-11001}"
PREFLIGHT_EXPERIMENT_SEED_BASE="${PREFLIGHT_EXPERIMENT_SEED_BASE:-20001}"

for flag in "$PREFLIGHT_RUN_ORACLE" "$PREFLIGHT_REQUIRE_10M_ACTION_FLIP" \
            "$PREFLIGHT_RESUME" "$PREFLIGHT_KEEP_DATABASES"; do
  [[ "$flag" =~ ^[01]$ ]] || {
    echo "Preflight Boolean settings must be 0 or 1; got: $flag" >&2
    exit 1
  }
done
for integer in "$PREFLIGHT_ORACLE_REPEATS" \
               "$PREFLIGHT_FINAL_REPEATS_5M" \
               "$PREFLIGHT_FINAL_REPEATS_10M" \
               "$PREFLIGHT_MIN_TRAIN_STEPS" \
               "$PREFLIGHT_MIN_FINALIZED_TRANSITIONS"; do
  [[ "$integer" =~ ^[1-9][0-9]*$ ]] || {
    echo "Preflight counts must be positive integers; got: $integer" >&2
    exit 1
  }
done

cell_count=0
estimated_operations_m=0
for size_m in $PREFLIGHT_CELLS_M; do
  case "$size_m" in
    5)
      final_repeats="$PREFLIGHT_FINAL_REPEATS_5M"
      ;;
    10)
      final_repeats="$PREFLIGHT_FINAL_REPEATS_10M"
      ;;
    *)
      echo "PREFLIGHT_CELLS_M supports only the staged 5M and 10M cells." >&2
      exit 1
      ;;
  esac
  cell_count=$(( cell_count + 1 ))
  # Six narrowed-baseline arms, six calibration/holdout oracle arms, and four
  # final arms per repeat.
  estimated_operations_m=$(( estimated_operations_m +
      size_m * (12 + 4 * final_repeats) ))
done
(( cell_count > 0 )) || {
  echo "PREFLIGHT_CELLS_M must contain at least one cell." >&2
  exit 1
}
if (( PREFLIGHT_RUN_ORACLE )); then
  estimated_operations_m=$(( estimated_operations_m +
      2 * PREFLIGHT_ORACLE_REPEATS ))
fi

if [[ "${CONFIRM_PREFLIGHT_VERIFICATION:-}" != "YES" ]]; then
  echo "This launches the staged cloud preflight verification." >&2
  echo "  oracle parity: $PREFLIGHT_RUN_ORACLE" >&2
  echo "  workload cells: $PREFLIGHT_CELLS_M (all T=2)" >&2
  echo "  results: $PREFLIGHT_RESULTS_ROOT" >&2
  echo "  databases: $PREFLIGHT_DB_ROOT" >&2
  echo "  approximately ${estimated_operations_m}M benchmark operations" >&2
  echo "Set CONFIRM_PREFLIGHT_VERIFICATION=YES after reviewing these paths." >&2
  exit 2
fi

if [[ "$PYTHON_VENV" = /* ]]; then
  PREFLIGHT_PYTHON="$PYTHON_VENV/bin/python"
else
  PREFLIGHT_PYTHON="$PROJECT_ROOT/$PYTHON_VENV/bin/python"
fi
if [[ "$DBBENCH_BUILD_DIR" = /* ]]; then
  PREFLIGHT_DB_BENCH="$DBBENCH_BUILD_DIR/db_bench"
else
  PREFLIGHT_DB_BENCH="$PROJECT_ROOT/$DBBENCH_BUILD_DIR/db_bench"
fi
[[ -x "$PREFLIGHT_PYTHON" ]] || {
  echo "Missing pipeline Python: $PREFLIGHT_PYTHON" >&2
  exit 1
}
[[ -x "$PREFLIGHT_DB_BENCH" ]] || {
  echo "Missing cloud db_bench: $PREFLIGHT_DB_BENCH" >&2
  echo "Rebuild the modified cloud checkout before running this preflight." >&2
  exit 1
}
"$PREFLIGHT_PYTHON" - "$PREFLIGHT_MAX_INVALID_FRACTION" <<'PY'
import sys

value = float(sys.argv[1])
if not 0.0 <= value <= 1.0:
    raise SystemExit("PREFLIGHT_MAX_INVALID_FRACTION must be in [0, 1]")
PY

mkdir -p "$PREFLIGHT_RESULTS_ROOT" "$PREFLIGHT_DB_ROOT" \
         "$PREFLIGHT_MANIFEST_ROOT/selection" \
         "$PREFLIGHT_MANIFEST_ROOT/final"

run_oracle_regression() {
  local result_root="$PREFLIGHT_RESULTS_ROOT/oracle-1m"
  local database_root="$PREFLIGHT_DB_ROOT/oracle-1m"
  local report="$result_root/graphs/oracle-parity.json"

  echo
  echo "=== Stage 1: 1M/T2 oracle bridge regression ==="
  WORKLOAD_PROFILE="$PREFLIGHT_WORKLOAD_PROFILE" \
  WORKLOAD_SIZES_M=1 SIZE_RATIOS=2 \
  EXPERIMENT_ARMS="regular oracle" \
  REPEATS="$PREFLIGHT_ORACLE_REPEATS" \
  RL_RUN_PHASE=experiment \
  RESULTS_ROOT="$result_root" DB_ROOT="$database_root" \
  RESUME="$PREFLIGHT_RESUME" KEEP_DATABASES="$PREFLIGHT_KEEP_DATABASES" \
  CONFIRM_EXPERIMENTS=YES \
    "$PIPELINE_DIR/03_run_experiments.sh"

  "$PIPELINE_DIR/04_generate_graphs.sh" --results "$result_root"
  set +e
  "$PREFLIGHT_PYTHON" "$PIPELINE_DIR/09_evaluate_oracle_parity.py" \
    "$result_root/graphs/summary.csv" \
    --size-millions 1 --size-ratio 2 \
    --minimum-pairs "$PREFLIGHT_ORACLE_REPEATS" \
    --minimum-envelope-pairs "$PREFLIGHT_ORACLE_REPEATS" \
    --admission-latency-limit-micros 5000 \
    --output "$report"
  local evaluator_status=$?
  set -e
  if (( evaluator_status != 0 && evaluator_status != 2 )); then
    echo "Oracle regression failed; inspect $report" >&2
    exit 1
  fi
  "$PREFLIGHT_PYTHON" - "$report" <<'PY'
import json
import sys

path = sys.argv[1]
report = json.load(open(path, encoding="utf-8"))
failed = report.get("failed_checks", [])
if failed:
    raise SystemExit(f"oracle regression has failed checks: {failed}")
print(
    "oracle regression:", report.get("verdict"),
    "(no failed checks; undecided envelopes are allowed at this stage)",
)
PY
}

screen_learning_health() {
  local result_root="$1" size_m="$2" repeats="$3" require_flip="$4"
  local output="$result_root/learning-health-screen.json"
  "$PREFLIGHT_PYTHON" - "$result_root" "$size_m" "$repeats" \
    "$PREFLIGHT_MIN_TRAIN_STEPS" \
    "$PREFLIGHT_MIN_FINALIZED_TRANSITIONS" \
    "$PREFLIGHT_MAX_INVALID_FRACTION" "$require_flip" "$output" <<'PY'
import json
import os
import sys
import tempfile
from pathlib import Path

(root_arg, size_arg, repeats_arg, steps_arg, finalized_arg,
 invalid_arg, require_flip_arg, output_arg) = sys.argv[1:]
root = Path(root_arg)
size_m = int(size_arg)
expected_repeats = int(repeats_arg)
minimum_steps = int(steps_arg)
minimum_finalized = int(finalized_arg)
maximum_invalid_fraction = float(invalid_arg)
require_flip = bool(int(require_flip_arg))
output = Path(output_arg)

failures = []
runs = {}
for arm in ("prior_only", "unconstrained_rl", "rl"):
    paths = sorted(root.glob(f"{size_m}M/T2/**/{arm}/learning_health.json"))
    if len(paths) != expected_repeats:
        failures.append(
            f"{arm}: expected {expected_repeats} health reports, found {len(paths)}"
        )
    runs[arm] = []
    for path in paths:
        report = json.loads(path.read_text())
        summary = report.get("server_summary") or {}
        decisions = int(summary.get("decisions", 0))
        invalid = int(summary.get("invalid_intervals", 0))
        invalid_fraction = invalid / decisions if decisions else None
        item = {
            "directory": str(path.parent),
            "passed": report.get("passed") is True,
            "decisions": decisions,
            "finalized_transitions": int(
                summary.get("finalized_transitions", 0)
            ),
            "replay_size": int(summary.get("replay_size", 0)),
            "train_steps": int(summary.get("train_steps", 0)),
            "max_abs_residual_advantage": float(
                summary.get("max_abs_residual_advantage", 0.0)
            ),
            "argmax_flip_count": int(summary.get("argmax_flip_count", 0)),
            "argmax_comparison_count": int(
                summary.get("argmax_comparison_count", 0)
            ),
            "invalid_intervals": invalid,
            "invalid_fraction": invalid_fraction,
            "trainer_error": summary.get("trainer_error"),
        }
        runs[arm].append(item)
        if not item["passed"]:
            failures.append(f"{arm}: hard learning-health gate failed at {path.parent}")
        if not (path.parent / "COMPLETED").exists():
            failures.append(f"{arm}: missing COMPLETED at {path.parent}")
        if arm == "prior_only":
            continue
        if item["train_steps"] < minimum_steps:
            failures.append(
                f"{arm}: only {item['train_steps']} optimizer steps at {path.parent}; "
                f"meaningful-screen minimum is {minimum_steps}"
            )
        if item["finalized_transitions"] < minimum_finalized:
            failures.append(
                f"{arm}: only {item['finalized_transitions']} finalized transitions "
                f"at {path.parent}; minimum is {minimum_finalized}"
            )
        if item["max_abs_residual_advantage"] <= 1e-8:
            failures.append(f"{arm}: residual remained zero at {path.parent}")
        if arm == "rl" and (
            invalid_fraction is None
            or invalid_fraction > maximum_invalid_fraction
        ):
            failures.append(
                f"rl: invalid interval fraction {invalid_fraction} exceeds "
                f"{maximum_invalid_fraction} at {path.parent}"
            )

if require_flip:
    constrained_flips = sum(item["argmax_flip_count"] for item in runs["rl"])
    if constrained_flips == 0:
        failures.append(
            "rl: zero prior-vs-learned argmax flips across the 10M checkpoint"
        )

report = {
    "schema_version": 1,
    "size_millions": size_m,
    "size_ratio": 2,
    "expected_repeats": expected_repeats,
    "thresholds": {
        "minimum_train_steps_per_learned_run": minimum_steps,
        "minimum_finalized_transitions_per_learned_run": minimum_finalized,
        "maximum_constrained_invalid_fraction": maximum_invalid_fraction,
        "require_constrained_action_flip": require_flip,
    },
    "runs": runs,
    "failures": failures,
    "passed": not failures,
}
output.parent.mkdir(parents=True, exist_ok=True)
with tempfile.NamedTemporaryFile(
    "w", encoding="utf-8", dir=output.parent, delete=False
) as handle:
    temporary = Path(handle.name)
    json.dump(report, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, output)
print(json.dumps(report, indent=2, sort_keys=True))
if failures:
    raise SystemExit(f"learning-health screen failed; inspect {output}")
PY
}

run_cell() {
  local size_m="$1" tag final_repeats require_flip
  case "$size_m" in
    5)
      tag="5m"
      final_repeats="$PREFLIGHT_FINAL_REPEATS_5M"
      require_flip=0
      ;;
    10)
      tag="10m"
      final_repeats="$PREFLIGHT_FINAL_REPEATS_10M"
      require_flip="$PREFLIGHT_REQUIRE_10M_ACTION_FLIP"
      ;;
  esac

  local baseline_results="$PREFLIGHT_RESULTS_ROOT/baseline-$tag"
  local baseline_databases="$PREFLIGHT_DB_ROOT/baseline-$tag"
  local selection_manifest="$PREFLIGHT_MANIFEST_ROOT/selection/$PREFLIGHT_WORKLOAD_PROFILE/${size_m}M/T2/baseline_slo.json"
  local final_manifest_root="$PREFLIGHT_MANIFEST_ROOT/final"
  local guard_results="$PREFLIGHT_RESULTS_ROOT/guard-$tag"
  local guard_databases="$PREFLIGHT_DB_ROOT/guard-$tag"
  local experiment_results="$PREFLIGHT_RESULTS_ROOT/experiment-$tag"
  local experiment_databases="$PREFLIGHT_DB_ROOT/experiment-$tag"

  echo
  echo "=== ${size_m}M/T2: narrowed tuned-baseline sweep ==="
  WORKLOAD_PROFILE="$PREFLIGHT_WORKLOAD_PROFILE" \
  WORKLOAD_SIZES_M="$size_m" SIZE_RATIOS=2 \
  RL_RUN_PHASE=experiment BASELINE_REPEATS=3 \
  BASELINE_L0_COMPACTION_TRIGGERS="4 8" \
  BASELINE_L0_SLOWDOWN_TRIGGERS=20 \
  BASELINE_L0_STOP_TRIGGERS=36 \
  BASELINE_COMPACTION_PRIORITIES=3 \
  BASELINE_RESULTS_ROOT="$baseline_results" \
  BASELINE_DB_ROOT="$baseline_databases" \
  RESUME="$PREFLIGHT_RESUME" KEEP_DATABASES="$PREFLIGHT_KEEP_DATABASES" \
  CONFIRM_BASELINE_SWEEP=YES \
    "$PIPELINE_DIR/05_run_baseline_sweep.sh"

  echo
  echo "=== ${size_m}M/T2: provisional schema-v2 manifest ==="
  "$PREFLIGHT_PYTHON" "$PIPELINE_DIR/06_select_baseline_slo.py" \
    --baseline-results "$baseline_results" \
    --workload-profile "$PREFLIGHT_WORKLOAD_PROFILE" \
    --size-millions "$size_m" --size-ratio 2 --minimum-repeats 3 \
    --output "$selection_manifest"

  echo
  echo "=== ${size_m}M/T2: three calibrations + three holdouts ==="
  WORKLOAD_PROFILE="$PREFLIGHT_WORKLOAD_PROFILE" \
  WORKLOAD_SIZES_M="$size_m" SIZE_RATIOS=2 \
  SELECTION_SLO_ROOT="$PREFLIGHT_MANIFEST_ROOT/selection" \
  FINAL_SLO_ROOT="$final_manifest_root" \
  GUARD_RESULTS_ROOT="$guard_results" GUARD_DB_ROOT="$guard_databases" \
  CALIBRATION_SEED_BASE="$PREFLIGHT_CALIBRATION_SEED_BASE" \
  HOLDOUT_SEED_BASE="$PREFLIGHT_HOLDOUT_SEED_BASE" \
  RESUME="$PREFLIGHT_RESUME" KEEP_DATABASES="$PREFLIGHT_KEEP_DATABASES" \
  CONFIRM_GUARD_PROTOCOL=YES \
    "$PIPELINE_DIR/06_run_guard_protocol.sh"

  echo
  echo "=== ${size_m}M/T2: four-arm learning checkpoint (${final_repeats} repeat(s)) ==="
  WORKLOAD_PROFILE="$PREFLIGHT_WORKLOAD_PROFILE" \
  WORKLOAD_SIZES_M="$size_m" SIZE_RATIOS=2 \
  EXPERIMENT_ARMS="regular prior_only unconstrained_rl rl" \
  REPEATS="$final_repeats" RL_RUN_PHASE=experiment \
  BASELINE_SLO_DIR="$final_manifest_root" \
  DBBENCH_SEED="$PREFLIGHT_EXPERIMENT_SEED_BASE" \
  RESULTS_ROOT="$experiment_results" DB_ROOT="$experiment_databases" \
  RESUME="$PREFLIGHT_RESUME" KEEP_DATABASES="$PREFLIGHT_KEEP_DATABASES" \
  CONFIRM_EXPERIMENTS=YES \
    "$PIPELINE_DIR/03_run_experiments.sh"

  screen_learning_health \
    "$experiment_results" "$size_m" "$final_repeats" "$require_flip"

  "$PREFLIGHT_PYTHON" "$PIPELINE_DIR/11_analyze_learning.py" \
    "$experiment_results" --arm rl --stride 10 \
    --output "$experiment_results/learning-rl.json"
  "$PREFLIGHT_PYTHON" "$PIPELINE_DIR/11_analyze_learning.py" \
    "$experiment_results" --arm unconstrained_rl --stride 10 \
    --output "$experiment_results/learning-unconstrained.json"
  "$PIPELINE_DIR/04_generate_graphs.sh" --results "$experiment_results"

  if (( final_repeats >= 2 )); then
    local acceptance="$experiment_results/graphs/acceptance-${size_m}M-T2.json"
    set +e
    "$PREFLIGHT_PYTHON" "$PIPELINE_DIR/07_evaluate_paired.py" \
      "$experiment_results/graphs/summary.csv" \
      --size-millions "$size_m" --size-ratio 2 \
      --minimum-pairs "$final_repeats" \
      --scan-objective sorted_run_seeks --output "$acceptance"
    local acceptance_status=$?
    set -e
    [[ -f "$acceptance" ]] || {
      echo "Performance screen did not produce $acceptance" >&2
      exit 1
    }
    if (( acceptance_status == 0 )); then
      echo "${size_m}M/T2 paired performance screen passed."
    else
      echo "${size_m}M/T2 paired performance screen did not pass at " \
           "${final_repeats} repeats; inspect $acceptance."
      echo "This is not treated as an implementation failure because the " \
           "short preflight may not decide the confidence bounds."
    fi
  fi
}

if (( PREFLIGHT_RUN_ORACLE )); then
  run_oracle_regression
fi
for size_m in $PREFLIGHT_CELLS_M; do
  run_cell "$size_m"
done

echo
echo "Preflight verification completed without a mechanical/learning failure."
echo "Results:   $PREFLIGHT_RESULTS_ROOT"
echo "Manifests: $PREFLIGHT_MANIFEST_ROOT/final"
echo "Review each learning-health-screen.json and the 10M acceptance report " \
     "before launching the full matrix."
