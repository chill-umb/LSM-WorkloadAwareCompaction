#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

usage() {
  cat <<'USAGE'
Usage:
  scripts/experiment_runner.sh [options] [results_dir]

Runs the full L0 compaction experiment:
  1. Copy the configured workload to workload.txt.
  2. Run vanilla leveled RocksDB.
  3. Start the Python RL server.
  4. Run RL L0 compaction RocksDB.
  5. Copy metrics/logs into results.
  6. Generate comparison CSV/JSON/graphs.

Options:
  --results-dir PATH                 Result directory. Same as positional results_dir.
  --workload PATH                    Generated workload file path.
  --workload-label NAME              Label used for generated workload/result names.
  --result-group NAME                Result group used for default results path.
  --warmup-ops N                     Number of warmup operations ignored by metrics.
  --lsm-sample-interval N            LSM metrics sampling interval.
  --db-runner-bin PATH               RocksDB runner binary.
  --db-path PATH                     RocksDB data directory (passed as --db to db_runner;
                                     created if missing). E.g. /mnt/nvme/rocksdb-data.
  --reuse-leveled DIR                Reuse a cached leveled baseline instead of re-running it.
                                     If DIR holds a baseline it is copied in; otherwise the
                                     leveled run executes and its output is cached to DIR.
                                     Lets a sweep compute the (RL-independent) baseline once.
  --python-bin PATH                  Python interpreter for RL server/analysis.
  --db-runner-common-args "ARGS"     Common db_runner args used by leveled and RL runs.
  --rl-socket-timeout-ms N           RL socket request timeout for db_runner.

Common RL/DQN options:
  --rl-learning-rate VALUE           Sets RL_LEARNING_RATE.
  --rl-gamma VALUE                   Sets RL_GAMMA.
  --rl-batch-size N                  Sets RL_BATCH_SIZE.
  --rl-replay-buffer-size N          Sets RL_REPLAY_BUFFER_SIZE.
  --rl-min-replay-size N             Sets RL_MIN_REPLAY_SIZE.
  --rl-target-update-interval N      Sets RL_TARGET_UPDATE_INTERVAL.
  --rl-epsilon-start VALUE           Sets RL_EPSILON_START.
  --rl-epsilon-end VALUE             Sets RL_EPSILON_END.
  --rl-epsilon-decay-steps N         Sets RL_EPSILON_DECAY_STEPS.
  --rl-hidden-dim N                  Sets RL_HIDDEN_DIM.
  --rl-train-steps-per-observation N Sets RL_TRAIN_STEPS_PER_OBSERVATION.
  --rl-async-training 0|1            Sets RL_ASYNC_TRAINING.
  --rl-param NAME=VALUE              Export any additional RL_* parameter.
  -h, --help                         Show this help.

Environment overrides:
  WORKLOAD_PATH              default: workloads/workload_1M_mixed.txt
  WARMUP_OPS                 default: inferred from WORKLOAD_PATH when known
  LSM_SAMPLE_INTERVAL        default: 10000
  WORKLOAD_LABEL             default: derived from WORKLOAD_PATH
  DB_RUNNER_BIN              default: ./bin/db_runner
  DB_PATH                    default: unset (db_runner uses its built-in ./db)
  PYTHON_BIN                 default: .venv/bin/python3 if present, else python3
  RL_SOCKET_TIMEOUT_MS       default: 100
  DB_RUNNER_COMMON_ARGS      default: -T 4 -E 64 -d 1 --cc 0 --stat 1 --progress 1 --totaltime 1 --peroptime 0
  RL_*                       default: values in rl_agent/config.py

Example:
  scripts/experiment_runner.sh \
    --workload workloads/workload_1M_read-only.txt \
    --rl-epsilon-decay-steps 400 \
    --results-dir results/1M/read_heavy_1m_eps400_run_01

Sweep example:
  for steps in 100 200 400 800 1500 2000; do
    scripts/experiment_runner.sh \
      --workload workloads/workload_1M_mixed.txt \
      --rl-epsilon-decay-steps "$steps" \
      --results-dir "results/1M/epsilon_sweep/eps_${steps}"
  done
USAGE
}

timestamp="$(date +%Y%m%d_%H%M%S)"
WORKLOAD_PATH="${WORKLOAD_PATH:-workloads/workload_1M_mixed.txt}"
DB_RUNNER_BIN="${DB_RUNNER_BIN:-./bin/db_runner}"
DB_PATH="${DB_PATH:-}"
REUSE_LEVELED="${REUSE_LEVELED:-}"
LSM_SAMPLE_INTERVAL="${LSM_SAMPLE_INTERVAL:-10000}"
RL_SOCKET_TIMEOUT_MS="${RL_SOCKET_TIMEOUT_MS:-100}"
RESULTS_ROOT="${RESULTS_ROOT:-}"

infer_result_group() {
  local workload_path="$1"
  case "$workload_path" in
    *500k*|*500K*) echo "500k" ;;
    *1M*|*1m*) echo "1M" ;;
    *5M*|*5m*) echo "5M" ;;
    *) echo "misc" ;;
  esac
}

default_warmup_ops() {
  local workload_path="$1"
  case "$workload_path" in
    *500k*|*500K*) echo "75000" ;;
    *1M*|*1m*) echo "150000" ;;
    *5M*|*5m*) echo "750000" ;;
    *w_l0_10m*) echo "2000000" ;;
    *) echo "0" ;;
  esac
}

require_option_value() {
  local option="$1"
  local value="${2:-}"
  if [[ -z "$value" ]]; then
    echo "Missing value for $option" >&2
    exit 1
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --results-dir)
      require_option_value "$1" "${2:-}"
      RESULTS_ROOT="$2"
      shift 2
      ;;
    --workload)
      require_option_value "$1" "${2:-}"
      WORKLOAD_PATH="$2"
      shift 2
      ;;
    --workload-label)
      require_option_value "$1" "${2:-}"
      WORKLOAD_LABEL="$2"
      shift 2
      ;;
    --result-group)
      require_option_value "$1" "${2:-}"
      RESULT_GROUP="$2"
      shift 2
      ;;
    --warmup-ops)
      require_option_value "$1" "${2:-}"
      WARMUP_OPS="$2"
      shift 2
      ;;
    --lsm-sample-interval)
      require_option_value "$1" "${2:-}"
      LSM_SAMPLE_INTERVAL="$2"
      shift 2
      ;;
    --db-runner-bin)
      require_option_value "$1" "${2:-}"
      DB_RUNNER_BIN="$2"
      shift 2
      ;;
    --db-path)
      require_option_value "$1" "${2:-}"
      DB_PATH="$2"
      shift 2
      ;;
    --reuse-leveled)
      require_option_value "$1" "${2:-}"
      REUSE_LEVELED="$2"
      shift 2
      ;;
    --python-bin)
      require_option_value "$1" "${2:-}"
      PYTHON_BIN="$2"
      shift 2
      ;;
    --db-runner-common-args)
      require_option_value "$1" "${2:-}"
      DB_RUNNER_COMMON_ARGS="$2"
      shift 2
      ;;
    --rl-socket-timeout-ms)
      require_option_value "$1" "${2:-}"
      RL_SOCKET_TIMEOUT_MS="$2"
      shift 2
      ;;
    --rl-learning-rate)
      require_option_value "$1" "${2:-}"
      export RL_LEARNING_RATE="$2"
      shift 2
      ;;
    --rl-gamma)
      require_option_value "$1" "${2:-}"
      export RL_GAMMA="$2"
      shift 2
      ;;
    --rl-batch-size)
      require_option_value "$1" "${2:-}"
      export RL_BATCH_SIZE="$2"
      shift 2
      ;;
    --rl-replay-buffer-size)
      require_option_value "$1" "${2:-}"
      export RL_REPLAY_BUFFER_SIZE="$2"
      shift 2
      ;;
    --rl-min-replay-size)
      require_option_value "$1" "${2:-}"
      export RL_MIN_REPLAY_SIZE="$2"
      shift 2
      ;;
    --rl-target-update-interval)
      require_option_value "$1" "${2:-}"
      export RL_TARGET_UPDATE_INTERVAL="$2"
      shift 2
      ;;
    --rl-epsilon-start)
      require_option_value "$1" "${2:-}"
      export RL_EPSILON_START="$2"
      shift 2
      ;;
    --rl-epsilon-end)
      require_option_value "$1" "${2:-}"
      export RL_EPSILON_END="$2"
      shift 2
      ;;
    --rl-epsilon-decay-steps)
      require_option_value "$1" "${2:-}"
      export RL_EPSILON_DECAY_STEPS="$2"
      shift 2
      ;;
    --rl-hidden-dim)
      require_option_value "$1" "${2:-}"
      export RL_HIDDEN_DIM="$2"
      shift 2
      ;;
    --rl-train-steps-per-observation)
      require_option_value "$1" "${2:-}"
      export RL_TRAIN_STEPS_PER_OBSERVATION="$2"
      shift 2
      ;;
    --rl-async-training)
      require_option_value "$1" "${2:-}"
      export RL_ASYNC_TRAINING="$2"
      shift 2
      ;;
    --rl-param)
      require_option_value "$1" "${2:-}"
      if [[ "$2" != RL_*=* ]]; then
        echo "--rl-param must be of the form RL_NAME=value" >&2
        exit 1
      fi
      export "$2"
      shift 2
      ;;
    --*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      if [[ -n "$RESULTS_ROOT" ]]; then
        echo "Unexpected positional argument: $1" >&2
        usage >&2
        exit 1
      fi
      RESULTS_ROOT="$1"
      shift
      ;;
  esac
done

workload_filename="$(basename "$WORKLOAD_PATH")"
WORKLOAD_LABEL="${WORKLOAD_LABEL:-${workload_filename%.txt}}"
RESULT_GROUP="${RESULT_GROUP:-$(infer_result_group "$WORKLOAD_PATH")}"
WARMUP_OPS="${WARMUP_OPS:-$(default_warmup_ops "$WORKLOAD_PATH")}"
RESULTS_ROOT="${RESULTS_ROOT:-results/${RESULT_GROUP}/${WORKLOAD_LABEL}_${timestamp}}"
LEVELED_DIR="${RESULTS_ROOT}/leveled"
RL_DIR="${RESULTS_ROOT}/rl"
AGENT_DIR="${RL_DIR}/agent"
COMPARISON_DIR="${RESULTS_ROOT}/comparison"
WORKLOAD_DIR="${RESULTS_ROOT}/workloads"
RESULT_WORKLOAD_PATH="${WORKLOAD_DIR}/${WORKLOAD_LABEL}.txt"
SOCKET_PATH="${RL_COMPACTION_SOCKET_PATH:-/tmp/rl_compaction_${USER:-user}_${WORKLOAD_LABEL}_$$.sock}"

if [[ -x ".venv/bin/python3" ]]; then
  DEFAULT_PYTHON=".venv/bin/python3"
else
  DEFAULT_PYTHON="python3"
fi
PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_PYTHON}"

DB_RUNNER_COMMON_ARGS="${DB_RUNNER_COMMON_ARGS:--T 4 -E 64 -d 1 --cc 0 --stat 1 --progress 1 --totaltime 1 --peroptime 0}"

# Optional explicit RocksDB data directory (e.g. an NVMe mount). When set it is
# passed to db_runner as --db and created up front. Absolutized because
# db_runner is launched from a per-run working directory (see RUN_WORKDIR).
DB_PATH_ARGS=""
if [[ -n "$DB_PATH" ]]; then
  mkdir -p "$DB_PATH"
  DB_PATH_ARGS="--db $(cd "$DB_PATH" && pwd)"
fi

# db_runner reads a hardcoded "workload.txt" and writes hardcoded output files
# (experiment_metrics.json, lsm_metrics.jsonl, workload.log, stats.log) into its
# current directory. To make parallel runs safe, each invocation gets its own
# working directory holding its own workload copy; outputs land there and are
# then collected into the results dirs. Absolute binary path so the cd works.
DB_RUNNER_ABS="$(cd "$(dirname "$DB_RUNNER_BIN")" && pwd)/$(basename "$DB_RUNNER_BIN")"
RUN_WORKDIR="${RESULTS_ROOT}/.workdir"

server_pid=""

cleanup() {
  if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
    echo "[cleanup] stopping RL server pid=$server_pid"
    kill -INT "$server_pid" 2>/dev/null || true
    sleep 1
    if kill -0 "$server_pid" 2>/dev/null; then
      kill -TERM "$server_pid" 2>/dev/null || true
    fi
    wait "$server_pid" 2>/dev/null || true
  fi
  rm -f "$SOCKET_PATH"
  # Remove the transient per-run working directory (workload copy + raw output
  # files). The workload is also archived under results/.../workloads/ and the
  # collected metrics live in leveled/ and rl/, so nothing needed is lost.
  [[ -n "${RUN_WORKDIR:-}" ]] && rm -rf "$RUN_WORKDIR"
}
trap cleanup EXIT

require_file() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    echo "Required path missing: $path" >&2
    exit 1
  fi
}

prepare_run_workdir() {
  mkdir -p "$RUN_WORKDIR"
  cp "$WORKLOAD_PATH" "$RUN_WORKDIR/workload.txt"
  echo "[workload] private runtime copy: $RUN_WORKDIR/workload.txt"
}

copy_run_outputs() {
  local dest="$1"
  mkdir -p "$dest"
  cp "$RUN_WORKDIR/experiment_metrics.json" "$dest/"
  cp "$RUN_WORKDIR/lsm_metrics.jsonl" "$dest/"
  cp "$RUN_WORKDIR/workload.log" "$dest/" 2>/dev/null || true
  cp "$RUN_WORKDIR/stats.log" "$dest/" 2>/dev/null || true
}

wait_for_socket() {
  local socket_path="$1"
  local timeout_seconds="${2:-30}"
  local start
  start="$(date +%s)"

  while [[ ! -S "$socket_path" ]]; do
    sleep 0.25
    local now
    now="$(date +%s)"
    if (( now - start >= timeout_seconds )); then
      echo "Timed out waiting for RL socket: $socket_path" >&2
      echo "Check server log: ${AGENT_DIR}/server.log" >&2
      exit 1
    fi
  done
}

run_db_runner() {
  local compaction_style="$1"
  local label="$2"

  echo "[$label] running db_runner with compaction style ${compaction_style}"
  # Run inside the private workdir so it reads its own workload.txt and writes
  # its own output files, isolated from any concurrent run.
  # shellcheck disable=SC2086
  ( cd "$RUN_WORKDIR" && \
    LSM_METRICS_SAMPLE_INTERVAL="$LSM_SAMPLE_INTERVAL" \
    LSM_METRICS_WARMUP_OPS="$WARMUP_OPS" \
    "$DB_RUNNER_ABS" \
      -C "$compaction_style" \
      $DB_PATH_ARGS \
      $DB_RUNNER_COMMON_ARGS )
}

run_rl_db_runner() {
  echo "[rl] running db_runner with RL compaction"
  # shellcheck disable=SC2086
  ( cd "$RUN_WORKDIR" && \
    RL_COMPACTION_SOCKET_PATH="$SOCKET_PATH" \
    RL_COMPACTION_SOCKET_TIMEOUT_MS="$RL_SOCKET_TIMEOUT_MS" \
    LSM_METRICS_SAMPLE_INTERVAL="$LSM_SAMPLE_INTERVAL" \
    LSM_METRICS_WARMUP_OPS="$WARMUP_OPS" \
    "$DB_RUNNER_ABS" \
      -C 5 \
      $DB_PATH_ARGS \
      $DB_RUNNER_COMMON_ARGS )
}

print_rl_override() {
  local name="$1"
  if [[ -n "${!name:-}" ]]; then
    echo "[setup] ${name}=${!name}"
  fi
}

echo "[setup] results root: $RESULTS_ROOT"
echo "[setup] result group: $RESULT_GROUP"
echo "[setup] workload: $WORKLOAD_PATH"
echo "[setup] warmup ops: $WARMUP_OPS"
echo "[setup] db path: ${DB_PATH:-<db_runner default ./db>}"
for rl_name in \
  RL_LEARNING_RATE \
  RL_GAMMA \
  RL_BATCH_SIZE \
  RL_REPLAY_BUFFER_SIZE \
  RL_MIN_REPLAY_SIZE \
  RL_TARGET_UPDATE_INTERVAL \
  RL_EPSILON_START \
  RL_EPSILON_END \
  RL_EPSILON_DECAY_STEPS \
  RL_HIDDEN_DIM \
  RL_TRAIN_STEPS_PER_OBSERVATION \
  RL_ASYNC_TRAINING; do
  print_rl_override "$rl_name"
done
mkdir -p "$LEVELED_DIR" "$AGENT_DIR" "$COMPARISON_DIR" "$WORKLOAD_DIR"

require_file "$WORKLOAD_PATH"
require_file "$DB_RUNNER_BIN"

echo "[workload] using existing workload $WORKLOAD_PATH"
cp "$WORKLOAD_PATH" "$RESULT_WORKLOAD_PATH"
echo "[workload] archived workload: $RESULT_WORKLOAD_PATH"

# Stage a private runtime copy for this invocation (used by both the leveled
# and RL db_runner runs below).
prepare_run_workdir

if [[ -n "$REUSE_LEVELED" && -s "${REUSE_LEVELED}/experiment_metrics.json" ]]; then
  # The leveled (-C 1) baseline is independent of every RL_* knob, so a sweep
  # can run it once and reuse it. Copy the cached outputs into this run_dir so
  # the comparison, plots, and resume-detection all behave as if it ran here.
  echo "[leveled] reusing cached baseline from $REUSE_LEVELED"
  mkdir -p "$LEVELED_DIR"
  cp "${REUSE_LEVELED}/experiment_metrics.json" "$LEVELED_DIR/"
  cp "${REUSE_LEVELED}/lsm_metrics.jsonl" "$LEVELED_DIR/"
  cp "${REUSE_LEVELED}/workload.log" "$LEVELED_DIR/" 2>/dev/null || true
  cp "${REUSE_LEVELED}/stats.log" "$LEVELED_DIR/" 2>/dev/null || true
else
  echo "[leveled] starting vanilla leveled run"
  run_db_runner 1 "leveled"
  copy_run_outputs "$LEVELED_DIR"
  echo "[leveled] outputs copied to $LEVELED_DIR"
  if [[ -n "$REUSE_LEVELED" ]]; then
    echo "[leveled] caching baseline to $REUSE_LEVELED"
    copy_run_outputs "$REUSE_LEVELED"
  fi
fi

echo "[rl-server] starting Python RL server"
rm -f "$SOCKET_PATH"
# Glob covers the legacy checkpoint AND the per-level multi-level checkpoints
# (rl_compaction_model.l<i>.pt); a stale per-level file would otherwise be
# silently loaded into a "fresh" run.
rm -f "${AGENT_DIR}"/rl_compaction_model*.pt
rm -f "${AGENT_DIR}/rl_compaction_metrics.jsonl"
rm -f "${AGENT_DIR}/rl_compaction_io.jsonl"
rm -f "${AGENT_DIR}/server.log"

# The RL net is a tiny MLP: CPU inference is faster than GPU (no transfer
# overhead) and avoids many parallel runs contending for the same GPU. Default
# to CPU; set RL_CUDA_VISIBLE_DEVICES to opt back into a GPU.
# Thread caps: without them torch spawns one OpenMP team per process sized to
# the whole machine; with several servers in parallel that oversubscribes the
# cores and pushes response latency past the C++ socket timeout (broken pipes).
CUDA_VISIBLE_DEVICES="${RL_CUDA_VISIBLE_DEVICES:-}" \
OMP_NUM_THREADS="${RL_OMP_NUM_THREADS:-1}" \
MKL_NUM_THREADS="${RL_OMP_NUM_THREADS:-1}" \
RL_COMPACTION_SOCKET_PATH="$SOCKET_PATH" \
RL_MODEL_SAVE_PATH="${AGENT_DIR}/rl_compaction_model.pt" \
RL_METRICS_LOG_PATH="${AGENT_DIR}/rl_compaction_metrics.jsonl" \
RL_IO_LOG_PATH="${AGENT_DIR}/rl_compaction_io.jsonl" \
"$PYTHON_BIN" rl_agent/server.py >"${AGENT_DIR}/server.log" 2>&1 &
server_pid="$!"
wait_for_socket "$SOCKET_PATH" 30
echo "[rl-server] listening on $SOCKET_PATH"

echo "[rl] starting RL compaction run"
run_rl_db_runner
copy_run_outputs "$RL_DIR"
echo "[rl] outputs copied to $RL_DIR"

echo "[compare] generating comparison output"
"$PYTHON_BIN" scripts/compare_experiment_metrics.py \
  --baseline "$LEVELED_DIR" \
  --candidate "$RL_DIR" \
  --baseline-label leveled \
  --candidate-label rl \
  --out-dir "$COMPARISON_DIR"

expected_graphs=(
  "$COMPARISON_DIR/comparison_percent_delta.png"
  "$COMPARISON_DIR/comparison_latency_values.png"
  "$COMPARISON_DIR/comparison_key_metric_values.png"
  "$COMPARISON_DIR/comparison_l0_timeseries.png"
  "$COMPARISON_DIR/comparison_compaction_costs.png"
)

for graph in "${expected_graphs[@]}"; do
  if [[ ! -s "$graph" ]]; then
    echo "Expected graph missing or empty: $graph" >&2
    exit 1
  fi
done

echo
echo "Done."
echo "Results root: $RESULTS_ROOT"
echo "Leveled metrics: $LEVELED_DIR"
echo "RL metrics: $RL_DIR"
echo "RL agent logs: $AGENT_DIR"
echo "Comparison: $COMPARISON_DIR"
echo "Graphs:"
printf '  %s\n' "${expected_graphs[@]}"
