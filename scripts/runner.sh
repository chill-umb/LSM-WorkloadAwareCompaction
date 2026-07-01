#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# Edit this file before a batch run.
#
# MODE:
#   single - run one leveled-vs-RL experiment. experiment_runner.sh creates plots.
#   sweep  - run one experiment per parameter value, then create sweep plots.
MODE="${MODE:-single}"

# Setup stages. On Chameleon, set RUN_DEPS=1 the first time on a fresh node.
RUN_DEPS="${RUN_DEPS:-0}"
RUN_BUILD="${RUN_BUILD:-0}"
RUN_WORKLOAD_GEN="${RUN_WORKLOAD_GEN:-0}"

# Common workload and output configuration.
WORKLOAD_SPEC="${WORKLOAD_SPEC:-workload_specs/1M/mixed/w_l0_1m.spec.json}"
WORKLOAD_PATH="${WORKLOAD_PATH:-workloads/workload_1M_mixed.txt}"
RESULTS_ROOT="${RESULTS_ROOT:-results/1M/manual_run}"

# Build configuration.
BUILD_JOBS="${BUILD_JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)}"

# Single-run RL configuration.
SINGLE_RESULTS_DIR="${SINGLE_RESULTS_DIR:-${RESULTS_ROOT}/single}"
SINGLE_RL_ARGS=(
  --rl-epsilon-decay-steps "${RL_EPSILON_DECAY_STEPS:-2000}"
)

# Sweep configuration.
# PARAM_FLAG is the experiment_runner.sh option being varied.
# PARAM_NAME is the x-axis/metadata name used by plot_dqn_parameter_sweep.py.
# PARAM_DIR_PREFIX controls result folder names.
PARAM_FLAG="${PARAM_FLAG:---rl-epsilon-decay-steps}"
PARAM_NAME="${PARAM_NAME:-RL_EPSILON_DECAY_STEPS}"
PARAM_DIR_PREFIX="${PARAM_DIR_PREFIX:-eps}"
PARAM_VALUES="${PARAM_VALUES:-100 200 400 800 1500 2000}"
SWEEP_RESULTS_DIR="${SWEEP_RESULTS_DIR:-${RESULTS_ROOT}/sweep}"

sanitize_value() {
  local value="$1"
  value="${value//\//_}"
  value="${value// /_}"
  echo "$value"
}

run_optional_setup() {
  if [[ "$RUN_DEPS" == "1" ]]; then
    ./scripts/dependency_installer.sh
  fi

  if [[ "$RUN_BUILD" == "1" ]]; then
    ./scripts/artifact_builder.sh --jobs "$BUILD_JOBS"
  fi

  if [[ "$RUN_WORKLOAD_GEN" == "1" ]]; then
    ./scripts/workload_generator.sh "$WORKLOAD_SPEC"
  fi
}

run_single() {
  ./scripts/experiment_runner.sh \
    --workload "$WORKLOAD_PATH" \
    --results-dir "$SINGLE_RESULTS_DIR" \
    "${SINGLE_RL_ARGS[@]}"
}

run_sweep() {
  for value in $PARAM_VALUES; do
    safe_value="$(sanitize_value "$value")"
    run_dir="${SWEEP_RESULTS_DIR}/${PARAM_DIR_PREFIX}_${safe_value}"

    ./scripts/experiment_runner.sh \
      --workload "$WORKLOAD_PATH" \
      "$PARAM_FLAG" "$value" \
      --results-dir "$run_dir"
  done

  python3 scripts/plot_dqn_parameter_sweep.py \
    --sweep-root "$SWEEP_RESULTS_DIR" \
    --parameter-name "$PARAM_NAME" \
    --out-dir "${SWEEP_RESULTS_DIR}/sweep_analysis"
}

run_optional_setup

case "$MODE" in
  single)
    run_single
    ;;
  sweep)
    run_sweep
    ;;
  *)
    echo "Unknown MODE: $MODE" >&2
    echo "Use MODE=single or MODE=sweep." >&2
    exit 1
    ;;
esac
