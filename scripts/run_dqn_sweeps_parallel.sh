#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

usage() {
  cat <<'USAGE'
Usage:
  scripts/run_dqn_sweeps_parallel.sh

Bounded-parallel version of run_all_dqn_sweeps.sh for many-core machines.
Runs the same one-parameter-at-a-time DQN sweeps and produces the identical
folder layout, so scripts/plot_dqn_parameter_sweep.py works unchanged.

How it parallelizes safely:
  * The leveled (-C 1) baseline is computed ONCE up front into a shared cache,
    then every RL config reuses it (--reuse-leveled).
  * Each config runs in its own db directory (mktemp under DB_PATH_BASE) so the
    per-run `-d 1` destroy never collides.
  * Up to JOBS configs run concurrently (xargs -P). Each config's stdout/stderr
    goes to <run_dir>.log.
  * Completed configs are skipped, so the whole sweep is resumable.

Tuning knobs (env):
  WORKLOAD_PATH     default: workloads/workload_1M_mixed.txt
  RESULTS_ROOT      default: results/1M/dqn_hyperparameter_sweeps_parallel
  JOBS              parallel configs at once. default: 6
  MAX_BG            RocksDB --max_background_jobs per run. default: 8
  BLOCK_CACHE_MB    RocksDB --bb (block cache MB) per run. default: 8192
  DB_PATH_BASE      parent dir for per-run db dirs. default: /mnt/nvme/rl_sweep_dbs
  WARMUP_OPS        forwarded to experiment_runner (inferred if unset)
  EXTRA_COMMON      extra db_runner args appended to the common args
  PYTHON_BIN        default: .venv/bin/python3 if present, else python3
  MPLCONFIGDIR      default: /tmp/lsm-matplotlib-cache

Sizing note: total compaction threads in flight ~= JOBS * MAX_BG. Keep that
under your core count (e.g. 6*8=48 on a 64-core box). Each run also holds a
BLOCK_CACHE_MB RocksDB cache + a CPU torch process in RAM.

Example (64-core box, 1M sweep):
  JOBS=6 MAX_BG=8 BLOCK_CACHE_MB=8192 DB_PATH_BASE=/mnt/nvme/rl_sweep_dbs \
  scripts/run_dqn_sweeps_parallel.sh
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
RESULTS_ROOT="${RESULTS_ROOT:-results/1M/dqn_hyperparameter_sweeps_parallel}"
JOBS="${JOBS:-6}"
MAX_BG="${MAX_BG:-8}"
BLOCK_CACHE_MB="${BLOCK_CACHE_MB:-8192}"
DB_PATH_BASE="${DB_PATH_BASE:-/mnt/nvme/rl_sweep_dbs}"
EXTRA_COMMON="${EXTRA_COMMON:-}"
PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_PYTHON}"
MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/lsm-matplotlib-cache}"
export MPLCONFIGDIR

SHARED_LEVELED="${SHARED_LEVELED:-${RESULTS_ROOT}/_leveled_baseline}"
# Progress bars are noise when interleaved across parallel jobs.
COMMON_ARGS="-T 4 -E 64 -d 1 --cc 0 --stat 1 --progress 0 --totaltime 1 --peroptime 0 --bb ${BLOCK_CACHE_MB} --max_background_jobs ${MAX_BG} ${EXTRA_COMMON}"

require_file() {
  if [[ ! -e "$1" ]]; then echo "Required path missing: $1" >&2; exit 1; fi
}

sanitize_value() {
  local v="$1"; v="${v//\//_}"; v="${v// /_}"; echo "$v"
}

# Sweep definitions: "name|prefix|param_name|flag|space-separated values".
# For --rl-param sweeps the value token is the full RL_NAME=value string, so the
# run-dir name still ends in the numeric value (plot script reads the trailing
# number).
SWEEPS=(
  "gamma|gamma|RL_GAMMA|--rl-gamma|0.80 0.90 0.95 0.99"
  "learning_rate|lr|RL_LEARNING_RATE|--rl-learning-rate|0.0001 0.0003 0.001 0.003 0.01"
  "replay_buffer_size|replay|RL_REPLAY_BUFFER_SIZE|--rl-replay-buffer-size|1000 5000 10000 50000 100000 200000"
  "target_update_interval|target|RL_TARGET_UPDATE_INTERVAL|--rl-target-update-interval|25 50 100 200 500 1000"
  "epsilon_end|epsend|RL_EPSILON_END|--rl-epsilon-end|0.01 0.05 0.10 0.20"
  "hidden_dim|hidden|RL_HIDDEN_DIM|--rl-hidden-dim|32 64 128 256"
  "train_steps_per_observation|trainsteps|RL_TRAIN_STEPS_PER_OBSERVATION|--rl-train-steps-per-observation|1 2 4 8"
  "normalizer_decay|normdecay|RL_NORMALIZER_DECAY|--rl-param|RL_NORMALIZER_DECAY=0.90 RL_NORMALIZER_DECAY=0.95 RL_NORMALIZER_DECAY=0.99 RL_NORMALIZER_DECAY=0.995 RL_NORMALIZER_DECAY=0.999"
  "n_step|nstep|RL_N_STEP|--rl-param|RL_N_STEP=1 RL_N_STEP=2 RL_N_STEP=3 RL_N_STEP=5 RL_N_STEP=8 RL_N_STEP=10"
  "double_dqn|doubledqn|RL_DOUBLE_DQN|--rl-param|RL_DOUBLE_DQN=0 RL_DOUBLE_DQN=1"
  "batch_size|batch|RL_BATCH_SIZE|--rl-batch-size|16 32 64 128 256"
  "epsilon_decay|eps|RL_EPSILON_DECAY_STEPS|--rl-epsilon-decay-steps|$(seq 100 100 3000 | tr '\n' ' ')"
)

require_file "$WORKLOAD_PATH"
require_file "scripts/experiment_runner.sh"
mkdir -p "$RESULTS_ROOT" "$MPLCONFIGDIR" "$DB_PATH_BASE"

echo "[setup] workload:      $WORKLOAD_PATH"
echo "[setup] results root:  $RESULTS_ROOT"
echo "[setup] parallelism:   JOBS=$JOBS  MAX_BG=$MAX_BG  (~$((JOBS * MAX_BG)) compaction threads in flight)"
echo "[setup] block cache:   ${BLOCK_CACHE_MB} MB per run"
echo "[setup] db base:       $DB_PATH_BASE"

# ---- 1. Baseline once (populates the shared leveled cache serially) ----
if [[ -s "${SHARED_LEVELED}/experiment_metrics.json" ]]; then
  echo "[baseline] reusing existing shared baseline at $SHARED_LEVELED"
else
  echo "[baseline] computing shared leveled baseline (once)"
  baseline_db="$(mktemp -d "${DB_PATH_BASE}/baseline.XXXXXX")"
  DB_RUNNER_COMMON_ARGS="$COMMON_ARGS" \
  ./scripts/experiment_runner.sh \
    --workload "$WORKLOAD_PATH" \
    --db-path "$baseline_db" \
    --reuse-leveled "$SHARED_LEVELED" \
    --results-dir "${RESULTS_ROOT}/_baseline_seed" \
    --rl-param "RL_EPSILON_DECAY_STEPS=100" >"${RESULTS_ROOT}/_baseline_seed.log" 2>&1
  rm -rf "$baseline_db"
  echo "[baseline] done -> $SHARED_LEVELED"
fi

# ---- 2. Emit the job list (run_dir<TAB>flag<TAB>value) ----
jobs_tsv="$(mktemp)"
trap 'rm -f "$jobs_tsv"' EXIT
for spec in "${SWEEPS[@]}"; do
  IFS='|' read -r name prefix _param flag values <<<"$spec"
  for value in $values; do
    safe="$(sanitize_value "$value")"
    run_dir="${RESULTS_ROOT}/${name}/sweep/${prefix}_${safe}"
    printf '%s\t%s\t%s\n' "$run_dir" "$flag" "$value" >>"$jobs_tsv"
  done
done
total="$(wc -l <"$jobs_tsv")"
echo "[dispatch] $total configs, up to $JOBS at a time"

# ---- 3. Per-config worker (exported for xargs) ----
run_one() {
  local rd flag val
  IFS=$'\t' read -r rd flag val <<<"$1"

  if [[ -s "${rd}/rl/experiment_metrics.json" && -s "${rd}/comparison/comparison_summary.json" ]]; then
    echo "[skip] ${rd}"
    return 0
  fi
  local dbdir
  dbdir="$(mktemp -d "${DB_PATH_BASE}/db.XXXXXX")"
  mkdir -p "$rd"
  echo "[start] ${rd}  (${flag} ${val})"
  if DB_RUNNER_COMMON_ARGS="$COMMON_ARGS" \
     ./scripts/experiment_runner.sh \
       --workload "$WORKLOAD_PATH" \
       --results-dir "$rd" \
       --db-path "$dbdir" \
       --reuse-leveled "$SHARED_LEVELED" \
       "$flag" "$val" >"${rd%/}.log" 2>&1; then
    echo "[done]  ${rd}"
  else
    echo "[FAIL]  ${rd} (see ${rd%/}.log)"
  fi
  rm -rf "$dbdir"
}
export -f run_one sanitize_value
export WORKLOAD_PATH SHARED_LEVELED DB_PATH_BASE COMMON_ARGS

# ---- 4. Run configs in parallel ----
xargs -P "$JOBS" -d '\n' -I LINE bash -c 'run_one "$@"' _ LINE <"$jobs_tsv"

# ---- 5. Plot each sweep (serial; plotting is cheap) ----
for spec in "${SWEEPS[@]}"; do
  IFS='|' read -r name _prefix param _flag _values <<<"$spec"
  sweep_dir="${RESULTS_ROOT}/${name}/sweep"
  [[ -d "$sweep_dir" ]] || continue
  echo "[plot] $name ($param)"
  "$PYTHON_BIN" scripts/plot_dqn_parameter_sweep.py \
    --sweep-root "$sweep_dir" \
    --parameter-name "$param" \
    --out-dir "${sweep_dir}/sweep_analysis" || echo "[plot] skipped $name (no completed runs?)"
done

echo
echo "Parallel DQN sweeps complete."
echo "Results root: $RESULTS_ROOT"
