#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

usage() {
  cat <<'USAGE'
Usage:
  scripts/run_all_dqn_sweeps.sh

Runs DQN hyperparameter sweeps on one workload. Each sweep uses the same folder
shape as the epsilon-decay sweep:

  <RESULTS_ROOT>/<sweep_name>/sweep/<prefix>_<value>/
    leveled/
    rl/
    comparison/
  <RESULTS_ROOT>/<sweep_name>/sweep/sweep_analysis/

After each sweep finishes, line graphs are generated with
scripts/plot_dqn_parameter_sweep.py.

Environment overrides:
  WORKLOAD_PATH       default: workloads/workload_1M_mixed.txt
  RESULTS_ROOT        default: results/1M/dqn_hyperparameter_sweeps
  SKIP_COMPLETED      default: 1
  PYTHON_BIN          default: .venv/bin/python3 if present, else python3
  MPLCONFIGDIR        default: /tmp/lsm-matplotlib-cache

Examples:
  scripts/run_all_dqn_sweeps.sh

  WORKLOAD_PATH=workloads/workload_1M_write-only.txt \
  RESULTS_ROOT=results/1M/write_only_dqn_sweeps \
  scripts/run_all_dqn_sweeps.sh
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -gt 0 ]]; then
  echo "Unexpected argument: $1" >&2
  usage >&2
  exit 1
fi

if [[ -x ".venv/bin/python3" ]]; then
  DEFAULT_PYTHON=".venv/bin/python3"
else
  DEFAULT_PYTHON="python3"
fi

WORKLOAD_PATH="${WORKLOAD_PATH:-workloads/workload_1M_mixed.txt}"
RESULTS_ROOT="${RESULTS_ROOT:-results/1M/dqn_hyperparameter_sweeps}"
SKIP_COMPLETED="${SKIP_COMPLETED:-1}"
PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_PYTHON}"
MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/lsm-matplotlib-cache}"
export MPLCONFIGDIR

require_file() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    echo "Required path missing: $path" >&2
    exit 1
  fi
}

sanitize_value() {
  local value="$1"
  value="${value//\//_}"
  value="${value// /_}"
  echo "$value"
}

run_one_config() {
  local run_dir="$1"
  shift

  if [[ "$SKIP_COMPLETED" == "1" &&
        -s "${run_dir}/leveled/experiment_metrics.json" &&
        -s "${run_dir}/rl/experiment_metrics.json" &&
        -s "${run_dir}/comparison/comparison_summary.json" ]]; then
    echo "[skip] completed run: $run_dir"
    return 0
  fi

  ./scripts/experiment_runner.sh \
    --workload "$WORKLOAD_PATH" \
    --results-dir "$run_dir" \
    "$@"
}

plot_sweep() {
  local sweep_dir="$1"
  local parameter_name="$2"

  "$PYTHON_BIN" scripts/plot_dqn_parameter_sweep.py \
    --sweep-root "${sweep_dir}/sweep" \
    --parameter-name "$parameter_name" \
    --out-dir "${sweep_dir}/sweep/sweep_analysis"
}

run_single_flag_sweep() {
  local sweep_name="$1"
  local parameter_name="$2"
  local dir_prefix="$3"
  local flag="$4"
  local values="$5"
  local sweep_dir="${RESULTS_ROOT}/${sweep_name}"

  echo
  echo "== Sweep: ${sweep_name} (${parameter_name}) =="
  echo "Values: ${values}"

  for value in $values; do
    local safe_value
    safe_value="$(sanitize_value "$value")"
    local run_dir="${sweep_dir}/sweep/${dir_prefix}_${safe_value}"

    echo
    echo "[run] ${parameter_name}=${value}"
    run_one_config "$run_dir" "$flag" "$value"
  done

  echo
  echo "[plot] ${sweep_name}"
  plot_sweep "$sweep_dir" "$parameter_name"
}

run_batch_size_sweep() {
  local sweep_name="batch_size"
  local parameter_name="RL_BATCH_SIZE"
  local dir_prefix="batch"
  local values="16 32 64 128 256"
  local sweep_dir="${RESULTS_ROOT}/${sweep_name}"

  echo
  echo "== Sweep: ${sweep_name} (${parameter_name}) =="
  echo "Values: ${values}"

  for value in $values; do
    local safe_value
    safe_value="$(sanitize_value "$value")"
    local run_dir="${sweep_dir}/sweep/${dir_prefix}_${safe_value}"

    echo
    echo "[run] ${parameter_name}=${value}; RL_MIN_REPLAY_SIZE=${value}"
    run_one_config \
      "$run_dir" \
      --rl-batch-size "$value" \
      --rl-min-replay-size "$value"
  done

  echo
  echo "[plot] ${sweep_name}"
  plot_sweep "$sweep_dir" "$parameter_name"
}

require_file "$WORKLOAD_PATH"
mkdir -p "$RESULTS_ROOT" "$MPLCONFIGDIR"

echo "[setup] workload: $WORKLOAD_PATH"
echo "[setup] results root: $RESULTS_ROOT"
echo "[setup] skip completed: $SKIP_COMPLETED"

run_single_flag_sweep \
  "gamma" \
  "RL_GAMMA" \
  "gamma" \
  "--rl-gamma" \
  "0.80 0.90 0.95 0.99"

run_single_flag_sweep \
  "learning_rate" \
  "RL_LEARNING_RATE" \
  "lr" \
  "--rl-learning-rate" \
  "0.0001 0.0003 0.001 0.003 0.01"

run_batch_size_sweep

run_single_flag_sweep \
  "replay_buffer_size" \
  "RL_REPLAY_BUFFER_SIZE" \
  "replay" \
  "--rl-replay-buffer-size" \
  "1000 5000 10000 50000 100000 200000"

run_single_flag_sweep \
  "target_update_interval" \
  "RL_TARGET_UPDATE_INTERVAL" \
  "target" \
  "--rl-target-update-interval" \
  "25 50 100 200 500 1000"

run_single_flag_sweep \
  "epsilon_end" \
  "RL_EPSILON_END" \
  "epsend" \
  "--rl-epsilon-end" \
  "0.01 0.05 0.10 0.20"

run_single_flag_sweep \
  "hidden_dim" \
  "RL_HIDDEN_DIM" \
  "hidden" \
  "--rl-hidden-dim" \
  "32 64 128 256"

run_single_flag_sweep \
  "train_steps_per_observation" \
  "RL_TRAIN_STEPS_PER_OBSERVATION" \
  "trainsteps" \
  "--rl-train-steps-per-observation" \
  "1 2 4 8"

run_single_flag_sweep \
  "normalizer_decay" \
  "RL_NORMALIZER_DECAY" \
  "normdecay" \
  "--rl-param" \
  "RL_NORMALIZER_DECAY=0.90 RL_NORMALIZER_DECAY=0.95 RL_NORMALIZER_DECAY=0.99 RL_NORMALIZER_DECAY=0.995 RL_NORMALIZER_DECAY=0.999"

# Run epsilon decay last, as requested.
run_single_flag_sweep \
  "epsilon_decay" \
  "RL_EPSILON_DECAY_STEPS" \
  "eps" \
  "--rl-epsilon-decay-steps" \
  "$(seq 100 100 3000)"

echo
echo "All DQN sweeps complete."
echo "Results root: $RESULTS_ROOT"
