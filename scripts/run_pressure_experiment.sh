#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

usage() {
  cat <<'USAGE'
Usage:
  scripts/run_pressure_experiment.sh [results_root]

Pressure-validated RL experiment. The 1M-mixed workload under the tuned config
(bb=8G, bg=6) produces ZERO L0 pressure (max 1 L0 file, no stalls) — on such a
config the baseline is unbeatable by any trigger policy and an RL run is
decided before it starts. This recipe:

  1. Runs (and caches) ONLY the leveled baseline under a pressure-inducing
     config: constrained background jobs on a write-heavy workload.
  2. Runs scripts/check_pressure.py on the baseline. Aborts on verdict NONE
     (override with PRESSURE_FORCE=1).
  3. Runs the full paired experiment (leveled reused from cache; RL arm with
     an exploration schedule matched to the run's decision budget).

Environment overrides:
  WORKLOAD_PATH      default: workloads/workload_1M_write-only.txt
  WORKLOAD_SPEC      default: workload_specs/1M/write-only/w_l0_1m_write_only.spec.json
                     (generated automatically if WORKLOAD_PATH is missing)
  RESULTS_ROOT       default: results/1M/pressure_experiment  (or $1)
  DB_PATH            default: unset (db_runner default ./db); e.g. /mnt/nvme/rocksdb-data
  MAX_BG             default: 1     (the pressure knob: single compaction thread)
  BLOCK_CACHE_MB     default: 1024
  EPS_DECAY          default: 100   (RL exploration matched to ~300-400 decisions)
  TRAIN_STEPS        default: 4     (SGD steps per observation)
  SEED               default: 1
  PRESSURE_FORCE     default: 0     (1 = proceed even on verdict NONE)
  EXTRA_RL_ARGS      default: unset (extra experiment_runner args for the RL arm,
                     e.g. "--rl-param RL_ANALYTIC_PRIOR=1" on the prior branch)

Example:
  DB_PATH=/mnt/nvme/rocksdb-data scripts/run_pressure_experiment.sh
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

WORKLOAD_PATH="${WORKLOAD_PATH:-workloads/workload_1M_write-only.txt}"
WORKLOAD_SPEC="${WORKLOAD_SPEC:-workload_specs/1M/write-only/w_l0_1m_write_only.spec.json}"
RESULTS_ROOT="${1:-${RESULTS_ROOT:-results/1M/pressure_experiment}}"
DB_PATH="${DB_PATH:-}"
MAX_BG="${MAX_BG:-1}"
BLOCK_CACHE_MB="${BLOCK_CACHE_MB:-1024}"
EPS_DECAY="${EPS_DECAY:-100}"
TRAIN_STEPS="${TRAIN_STEPS:-4}"
SEED="${SEED:-1}"
PRESSURE_FORCE="${PRESSURE_FORCE:-0}"
EXTRA_RL_ARGS="${EXTRA_RL_ARGS:-}"

if [[ -x ".venv/bin/python3" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python3}"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

# Pressure config: single background compaction thread is the lever that
# recreates L0 backlog; modest block cache keeps memory realistic. Size ratio
# -T 4 fixes trigger=4, slowdown=3, stop=4 (see parse_arguments.h).
COMMON_ARGS="-T 4 -E 64 -d 1 --cc 0 --stat 1 --progress 1 --totaltime 1 --peroptime 0 --bb ${BLOCK_CACHE_MB} --max_background_jobs ${MAX_BG}"

BASELINE_CACHE="${RESULTS_ROOT}/_leveled_baseline"

# ---- 0. Workload ----
if [[ ! -f "$WORKLOAD_PATH" ]]; then
  echo "[workload] $WORKLOAD_PATH missing; generating from $WORKLOAD_SPEC"
  scripts/workload_generator.sh "$WORKLOAD_SPEC"
fi
if [[ ! -f "$WORKLOAD_PATH" ]]; then
  echo "Workload still missing after generation: $WORKLOAD_PATH" >&2
  exit 1
fi

db_args=()
[[ -n "$DB_PATH" ]] && db_args=(--db-path "$DB_PATH")

echo "[setup] results root:   $RESULTS_ROOT"
echo "[setup] workload:       $WORKLOAD_PATH"
echo "[setup] pressure knobs: max_background_jobs=$MAX_BG bb=${BLOCK_CACHE_MB}MB"
echo "[setup] rl knobs:       eps_decay=$EPS_DECAY train_steps=$TRAIN_STEPS seed=$SEED $EXTRA_RL_ARGS"

# ---- 1. Baseline only (cached) ----
echo
echo "== Step 1/3: leveled baseline (pressure probe) =="
DB_RUNNER_COMMON_ARGS="$COMMON_ARGS" \
scripts/experiment_runner.sh \
  --workload "$WORKLOAD_PATH" \
  --results-dir "${RESULTS_ROOT}/paired" \
  --reuse-leveled "$BASELINE_CACHE" \
  --leveled-only \
  "${db_args[@]}"

# ---- 2. Pressure precondition ----
echo
echo "== Step 2/3: pressure precondition =="
if ! "$PYTHON_BIN" scripts/check_pressure.py --leveled "$BASELINE_CACHE"; then
  if [[ "$PRESSURE_FORCE" == "1" ]]; then
    echo "[pressure] verdict NONE but PRESSURE_FORCE=1 — continuing anyway."
  else
    echo
    echo "Aborting: the baseline shows no pressure, so an RL run on this"
    echo "workload/config cannot demonstrate anything about the trigger."
    echo "Tighten the config (MAX_BG, buffer size, workload) or set"
    echo "PRESSURE_FORCE=1 to override."
    exit 1
  fi
fi

# ---- 3. Full paired run (leveled reused) ----
echo
echo "== Step 3/3: paired experiment (RL arm) =="
# shellcheck disable=SC2086
DB_RUNNER_COMMON_ARGS="$COMMON_ARGS" \
scripts/experiment_runner.sh \
  --workload "$WORKLOAD_PATH" \
  --results-dir "${RESULTS_ROOT}/paired" \
  --reuse-leveled "$BASELINE_CACHE" \
  --rl-epsilon-decay-steps "$EPS_DECAY" \
  --rl-train-steps-per-observation "$TRAIN_STEPS" \
  --rl-param "RL_SEED=${SEED}" \
  "${db_args[@]}" \
  $EXTRA_RL_ARGS

echo
echo "Pressure experiment complete."
echo "Comparison: ${RESULTS_ROOT}/paired/comparison"
echo "Convergence view:"
echo "  $PYTHON_BIN scripts/plot_convergence.py --runs ${RESULTS_ROOT}/paired/rl --labels rl --out-dir ${RESULTS_ROOT}/convergence"
