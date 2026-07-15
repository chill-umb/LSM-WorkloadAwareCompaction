#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

usage() {
  cat <<'USAGE'
Usage:
  scripts/run_nstep_ablation.sh [results_root]

Runs the n-step reward-attribution ablation: for each N_STEP value and each
repeat, runs the paired leveled-vs-RL experiment, then compares each treatment
arm's RL run against the baseline arm's RL run (matched by repeat index).

Folder shape:
  <RESULTS_ROOT>/
    n1/rep1/{leveled,rl,comparison}/      # baseline arm (one-step, = old behavior)
    n1/rep2/...
    n5/rep1/{leveled,rl,comparison}/      # treatment arm
    n5/rep2/...
    ablation/n1_vs_n5/rep1/               # RL(n1) vs RL(n5) comparison
    ablation/n1_vs_n5/rep2/

N_STEP=1 reproduces the original one-step DQN, so n1 is the apples-to-apples
baseline for every other arm. Repeats are independent random trajectories (the
agent is not seeded), so treat them as a distribution, not point estimates.

Environment overrides:
  WORKLOAD_PATH      default: workloads/workload_1M_mixed.txt
  RESULTS_ROOT       default: results/1M/nstep_ablation
  N_STEP_VALUES      default: "1 5"           (space-separated; first is the baseline)
  REPEATS            default: 3
  DB_PATH            default: unset           (e.g. /mnt/nvme/rocksdb-data)
  DOUBLE_DQN         default: unset           (0|1; when set, forces RL_DOUBLE_DQN)
  EXTRA_RUNNER_ARGS  default: unset           (extra args passed to experiment_runner.sh)
  SKIP_COMPLETED     default: 1
  PYTHON_BIN         default: .venv/bin/python3 if present, else python3

Examples:
  DB_PATH=/mnt/nvme/rocksdb-data scripts/run_nstep_ablation.sh

  WORKLOAD_PATH=workloads/workload_5M_mixed.txt \
  N_STEP_VALUES="1 3 5 10" REPEATS=5 DB_PATH=/mnt/nvme/rocksdb-data \
  scripts/run_nstep_ablation.sh results/5M/nstep_ablation
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ -x ".venv/bin/python3" ]]; then
  DEFAULT_PYTHON=".venv/bin/python3"
else
  DEFAULT_PYTHON="python3"
fi

WORKLOAD_PATH="${WORKLOAD_PATH:-workloads/workload_1M_mixed.txt}"
RESULTS_ROOT="${1:-${RESULTS_ROOT:-results/1M/nstep_ablation}}"
N_STEP_VALUES="${N_STEP_VALUES:-1 5}"
REPEATS="${REPEATS:-3}"
DB_PATH="${DB_PATH:-}"
DOUBLE_DQN="${DOUBLE_DQN:-}"
EXTRA_RUNNER_ARGS="${EXTRA_RUNNER_ARGS:-}"
SKIP_COMPLETED="${SKIP_COMPLETED:-1}"
PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_PYTHON}"

require_file() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    echo "Required path missing: $path" >&2
    exit 1
  fi
}

# Common args forwarded to every experiment_runner.sh invocation.
runner_common_args() {
  local -a args=(--workload "$WORKLOAD_PATH")
  [[ -n "$DB_PATH" ]] && args+=(--db-path "$DB_PATH")
  [[ -n "$DOUBLE_DQN" ]] && args+=(--rl-param "RL_DOUBLE_DQN=$DOUBLE_DQN")
  # shellcheck disable=SC2206
  [[ -n "$EXTRA_RUNNER_ARGS" ]] && args+=($EXTRA_RUNNER_ARGS)
  printf '%s\n' "${args[@]}"
}

run_arm() {
  local n_step="$1"
  local rep="$2"
  local run_dir="$3"

  if [[ "$SKIP_COMPLETED" == "1" &&
        -s "${run_dir}/leveled/experiment_metrics.json" &&
        -s "${run_dir}/rl/experiment_metrics.json" ]]; then
    echo "[skip] completed: $run_dir"
    return 0
  fi

  local -a common
  mapfile -t common < <(runner_common_args)

  echo "[run] N_STEP=${n_step} rep=${rep} -> ${run_dir}"
  ./scripts/experiment_runner.sh \
    "${common[@]}" \
    --results-dir "$run_dir" \
    --rl-param "RL_N_STEP=${n_step}"
}

compare_arms() {
  local baseline_n="$1"
  local candidate_n="$2"
  local rep="$3"
  local baseline_rl="${RESULTS_ROOT}/n${baseline_n}/rep${rep}/rl"
  local candidate_rl="${RESULTS_ROOT}/n${candidate_n}/rep${rep}/rl"
  local out_dir="${RESULTS_ROOT}/ablation/n${baseline_n}_vs_n${candidate_n}/rep${rep}"

  if [[ ! -s "${baseline_rl}/experiment_metrics.json" || ! -s "${candidate_rl}/experiment_metrics.json" ]]; then
    echo "[compare] skip (missing metrics): n${baseline_n} vs n${candidate_n} rep${rep}"
    return 0
  fi

  mkdir -p "$out_dir"
  echo "[compare] n${baseline_n} vs n${candidate_n} rep${rep} -> ${out_dir}"
  "$PYTHON_BIN" scripts/compare_experiment_metrics.py \
    --baseline "$baseline_rl" \
    --candidate "$candidate_rl" \
    --baseline-label "n${baseline_n}" \
    --candidate-label "n${candidate_n}" \
    --out-dir "$out_dir"
}

require_file "$WORKLOAD_PATH"
require_file "scripts/experiment_runner.sh"
mkdir -p "$RESULTS_ROOT"

# First value is the baseline arm every other arm is compared against.
read -r -a N_ARR <<<"$N_STEP_VALUES"
BASELINE_N="${N_ARR[0]}"

echo "[setup] workload:      $WORKLOAD_PATH"
echo "[setup] results root:  $RESULTS_ROOT"
echo "[setup] N_STEP values: $N_STEP_VALUES (baseline n=${BASELINE_N})"
echo "[setup] repeats:       $REPEATS"
echo "[setup] db path:       ${DB_PATH:-<db_runner default ./db>}"
[[ -n "$DOUBLE_DQN" ]] && echo "[setup] RL_DOUBLE_DQN: $DOUBLE_DQN"

# 1. Run every arm x repeat.
for n_step in "${N_ARR[@]}"; do
  for rep in $(seq 1 "$REPEATS"); do
    run_arm "$n_step" "$rep" "${RESULTS_ROOT}/n${n_step}/rep${rep}"
  done
done

# 2. Compare each treatment arm against the baseline arm, matched by repeat.
for n_step in "${N_ARR[@]}"; do
  [[ "$n_step" == "$BASELINE_N" ]] && continue
  for rep in $(seq 1 "$REPEATS"); do
    compare_arms "$BASELINE_N" "$n_step" "$rep"
  done
done

echo
echo "n-step ablation complete."
echo "Per-arm results:   ${RESULTS_ROOT}/n<value>/rep<i>/"
echo "Ablation compares: ${RESULTS_ROOT}/ablation/n${BASELINE_N}_vs_n<value>/rep<i>/"
