#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

usage() {
  cat <<'USAGE'
Usage:
  scripts/run_l0_10m_experiments.sh [results_dir]

Runs the full L0 compaction experiment:
  1. Generate the configured workload.
  2. Run vanilla leveled RocksDB.
  3. Start the Python RL server.
  4. Run RL L0 compaction RocksDB.
  5. Copy metrics/logs into results.
  6. Generate comparison CSV/JSON/graphs.

Environment overrides:
  WARMUP_OPS                 default: 2000000
  LSM_SAMPLE_INTERVAL        default: 10000
  SPEC_PATH                  default: workloads/w_l0_10m.spec.json
  WORKLOAD_LABEL             default: derived from SPEC_PATH
  TECTONIC_BIN               default: ./bin/tectonic-cli
  DB_RUNNER_BIN              default: ./bin/db_runner
  PYTHON_BIN                 default: .venv/bin/python3 if present, else python3
  RL_SOCKET_TIMEOUT_MS       default: 100
  DB_RUNNER_COMMON_ARGS      default: -T 4 -E 64 -d 1 --cc 0 --stat 1 --progress 1 --totaltime 1 --peroptime 0

Example:
  scripts/run_l0_10m_experiments.sh results/l0_500k_run_01
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

timestamp="$(date +%Y%m%d_%H%M%S)"
SPEC_PATH="${SPEC_PATH:-workloads/w_l0_10m.spec.json}"
TECTONIC_BIN="${TECTONIC_BIN:-./bin/tectonic-cli}"
DB_RUNNER_BIN="${DB_RUNNER_BIN:-./bin/db_runner}"
WARMUP_OPS="${WARMUP_OPS:-2000000}"
LSM_SAMPLE_INTERVAL="${LSM_SAMPLE_INTERVAL:-10000}"
RL_SOCKET_TIMEOUT_MS="${RL_SOCKET_TIMEOUT_MS:-100}"
spec_filename="$(basename "$SPEC_PATH")"
WORKLOAD_LABEL="${WORKLOAD_LABEL:-${spec_filename%.spec.json}}"
RESULTS_ROOT="${1:-results/${WORKLOAD_LABEL}_${timestamp}}"
LEVELED_DIR="${RESULTS_ROOT}/leveled"
RL_DIR="${RESULTS_ROOT}/rl"
AGENT_DIR="${RL_DIR}/agent"
COMPARISON_DIR="${RESULTS_ROOT}/comparison"
WORKLOAD_DIR="${RESULTS_ROOT}/workloads"
SOCKET_PATH="${RL_COMPACTION_SOCKET_PATH:-/tmp/rl_compaction_${USER:-user}_${WORKLOAD_LABEL}_$$.sock}"

if [[ -x ".venv/bin/python3" ]]; then
  DEFAULT_PYTHON=".venv/bin/python3"
else
  DEFAULT_PYTHON="python3"
fi
PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_PYTHON}"

DB_RUNNER_COMMON_ARGS="${DB_RUNNER_COMMON_ARGS:--T 4 -E 64 -d 1 --cc 0 --stat 1 --progress 1 --totaltime 1 --peroptime 0}"

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
}
trap cleanup EXIT

require_file() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    echo "Required path missing: $path" >&2
    exit 1
  fi
}

copy_run_outputs() {
  local dest="$1"
  mkdir -p "$dest"
  cp experiment_metrics.json "$dest/"
  cp lsm_metrics.jsonl "$dest/"
  cp workload.log "$dest/" 2>/dev/null || true
  cp stats.log "$dest/" 2>/dev/null || true
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
  # shellcheck disable=SC2086
  LSM_METRICS_SAMPLE_INTERVAL="$LSM_SAMPLE_INTERVAL" \
  LSM_METRICS_WARMUP_OPS="$WARMUP_OPS" \
  "$DB_RUNNER_BIN" \
    -C "$compaction_style" \
    $DB_RUNNER_COMMON_ARGS
}

run_rl_db_runner() {
  echo "[rl] running db_runner with RL compaction"
  # shellcheck disable=SC2086
  RL_COMPACTION_SOCKET_PATH="$SOCKET_PATH" \
  RL_COMPACTION_SOCKET_TIMEOUT_MS="$RL_SOCKET_TIMEOUT_MS" \
  LSM_METRICS_SAMPLE_INTERVAL="$LSM_SAMPLE_INTERVAL" \
  LSM_METRICS_WARMUP_OPS="$WARMUP_OPS" \
  "$DB_RUNNER_BIN" \
    -C 5 \
    $DB_RUNNER_COMMON_ARGS
}

echo "[setup] results root: $RESULTS_ROOT"
mkdir -p "$LEVELED_DIR" "$AGENT_DIR" "$COMPARISON_DIR" "$WORKLOAD_DIR"

require_file "$SPEC_PATH"
require_file "$TECTONIC_BIN"
require_file "$DB_RUNNER_BIN"

echo "[workload] generating workload from $SPEC_PATH"
"$TECTONIC_BIN" generate -w "$SPEC_PATH" -o workload.txt
cp workload.txt "${WORKLOAD_DIR}/${WORKLOAD_LABEL}.workload.txt"
cp "$SPEC_PATH" "${WORKLOAD_DIR}/"

echo "[leveled] starting vanilla leveled run"
run_db_runner 1 "leveled"
copy_run_outputs "$LEVELED_DIR"
echo "[leveled] outputs copied to $LEVELED_DIR"

echo "[rl-server] starting Python RL server"
rm -f "$SOCKET_PATH"
rm -f "${AGENT_DIR}/rl_compaction_model.pt"
rm -f "${AGENT_DIR}/rl_compaction_metrics.jsonl"
rm -f "${AGENT_DIR}/rl_compaction_io.jsonl"
rm -f "${AGENT_DIR}/server.log"

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
