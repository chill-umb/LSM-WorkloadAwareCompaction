#!/usr/bin/env bash
# Execute regular and RL db_bench arms. db_bench generates all workloads; no
# workload file or preprocessing stage is involved.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'USAGE'
Usage:
  CONFIRM_EXPERIMENTS=YES scripts/dbbench_pipeline/03_run_experiments.sh

Configuration is read from scripts/dbbench_pipeline/config.sh and can be
overridden with environment variables. Supported arms are regular, oracle,
prior_only, rl, and unconstrained_rl.
USAGE
  exit 0
fi
[[ $# -eq 0 ]] || { echo "Unexpected argument: $1" >&2; exit 2; }
if [[ "${CONFIRM_EXPERIMENTS:-}" != "YES" ]]; then
  echo "This starts the configured experiment matrix." >&2
  echo "Review config.sh, then set CONFIRM_EXPERIMENTS=YES." >&2
  exit 2
fi

if [[ "$DBBENCH_BUILD_DIR" = /* ]]; then
  DB_BENCH="$DBBENCH_BUILD_DIR/db_bench"
else
  DB_BENCH="$PROJECT_ROOT/$DBBENCH_BUILD_DIR/db_bench"
fi
if [[ "$PYTHON_VENV" = /* ]]; then
  PYTHON="$PYTHON_VENV/bin/python"
else
  PYTHON="$PROJECT_ROOT/$PYTHON_VENV/bin/python"
fi
[[ -x "$DB_BENCH" ]] || {
  echo "Missing $DB_BENCH; run steps 01 and 02 first." >&2
  exit 1
}
[[ -x "$PYTHON" ]] || {
  echo "Missing $PYTHON; run step 00 first." >&2
  exit 1
}
"$PYTHON" -c 'import numpy, torch' >/dev/null || {
  echo "The pipeline Python environment does not contain numpy and torch." >&2
  exit 1
}

for integer in $WORKLOAD_SIZES_M $SIZE_RATIOS "$REPEATS"; do
  [[ "$integer" =~ ^[0-9]+$ ]] || {
    echo "Workload sizes and T values must be positive integers: $integer" >&2
    exit 1
  }
done
[[ "$WORKLOAD_PROFILE" =~ ^[A-Za-z0-9_.-]+$ ]] || {
  echo "WORKLOAD_PROFILE must contain only letters, digits, dot, dash, or underscore." >&2
  exit 1
}
for arm in $EXPERIMENT_ARMS; do
  case "$arm" in
    regular|oracle|prior_only|rl|unconstrained_rl) ;;
    *) echo "Unsupported experiment arm: $arm" >&2; exit 1 ;;
  esac
done
SLO_DRIVEN_MATRIX=0
for arm in $EXPERIMENT_ARMS; do
  if [[ "$arm" == "prior_only" || "$arm" == "rl" ||
        "$arm" == "unconstrained_rl" ]]; then
    SLO_DRIVEN_MATRIX=1
  fi
done
[[ "$RL_REQUIRE_BASELINE_SLO" =~ ^[01]$ ]] || {
  echo "RL_REQUIRE_BASELINE_SLO must be 0 or 1." >&2
  exit 1
}
[[ "$RL_STRUCTURAL_DIRTY_DEADLINE_MS" =~ ^[1-9][0-9]*$ ]] || {
  echo "RL_STRUCTURAL_DIRTY_DEADLINE_MS must be a positive integer." >&2
  exit 1
}
[[ "$RL_DECISION_INTERVAL_MS" =~ ^[1-9][0-9]*$ &&
   "$RL_OBSERVE_INTERVAL_MS" =~ ^[1-9][0-9]*$ ]] || {
  echo "RL decision and observation intervals must be positive integers." >&2
  exit 1
}
if [[ "$RL_OBSERVE_INTERVAL_MS" != "$RL_DECISION_INTERVAL_MS" ]]; then
  echo "Protocol v2 requires RL_OBSERVE_INTERVAL_MS to equal RL_DECISION_INTERVAL_MS." >&2
  exit 1
fi
[[ "$RL_L0_ALLOW_DEFER" =~ ^[01]$ ]] || {
  echo "RL_L0_ALLOW_DEFER must be 0 or 1." >&2
  exit 1
}
[[ "$RL_EPSILON_BOUND_MS" =~ ^[0-9]+$ ]] || {
  echo "RL_EPSILON_BOUND_MS must be a non-negative integer." >&2
  exit 1
}
"$PYTHON" - "$RL_OPTIONAL_MIN_SCORE" <<'PY'
import sys
value = float(sys.argv[1])
if not 0.0 <= value < 1.0:
    raise SystemExit(
        f"RL_OPTIONAL_MIN_SCORE must be in [0, 1); got {value}. "
        "At or above 1 every optional action would be reclassified as due "
        "work and the proactive band would be unreachable.")
PY
for ratio in $SIZE_RATIOS; do
  (( ratio > 1 )) || { echo "T must be greater than one: $ratio" >&2; exit 1; }
done
if (( SLO_DRIVEN_MATRIX )) && [[ "$RL_REQUIRE_BASELINE_SLO" == "1" ]]; then
  for size_m in $WORKLOAD_SIZES_M; do
    for ratio in $SIZE_RATIOS; do
      manifest_path="$BASELINE_SLO_DIR/$WORKLOAD_PROFILE/${size_m}M/T${ratio}/baseline_slo.json"
      [[ -f "$manifest_path" ]] || {
        echo "Missing calibrated manifest: $manifest_path" >&2
        echo "Run the baseline sweep and SLO selection steps first." >&2
        exit 1
      }
    done
  done
fi
[[ "$DISABLE_WAL" =~ ^[01]$ ]] || {
  echo "DISABLE_WAL must be 0 or 1; got: $DISABLE_WAL" >&2
  exit 1
}
(( LOAD_PERCENT > 0 && LOAD_PERCENT < 100 )) || {
  echo "LOAD_PERCENT must be between 1 and 99." >&2
  exit 1
}
if (( L0_SLOWDOWN_TRIGGER < L0_COMPACTION_TRIGGER ||
      L0_STOP_TRIGGER < L0_SLOWDOWN_TRIGGER )); then
  echo "Require compaction trigger <= slowdown trigger <= stop trigger." >&2
  exit 1
fi
"$PYTHON" - "$MIX_GET_RATIO" "$MIX_PUT_RATIO" "$MIX_SEEK_RATIO" <<'PY'
import sys
total = sum(map(float, sys.argv[1:]))
if abs(total - 1.0) > 1e-6:
    raise SystemExit(f"mix ratios must sum to one; got {total}")
PY

if [[ -e "$RESULTS_ROOT" && "$RESUME" != "1" ]]; then
  echo "Results already exist: $RESULTS_ROOT" >&2
  echo "Choose another RESULTS_ROOT or set RESUME=1." >&2
  exit 1
fi
mkdir -p "$RESULTS_ROOT" "$DB_ROOT"
RESULTS_ROOT="$(cd "$RESULTS_ROOT" && pwd)"
DB_ROOT="$(cd "$DB_ROOT" && pwd)"
case "$DB_ROOT" in
  /|"$PROJECT_ROOT"|"$PROJECT_ROOT"/)
    echo "Unsafe DB_ROOT: $DB_ROOT" >&2
    exit 1
    ;;
esac

strays="$(ps -eo pid=,comm=,args= 2>/dev/null | awk -v self="$$" '
  $1 == self { next }
  $2 == "db_bench" { print $1" "$2; next }
  $2 ~ /^python/ && $0 ~ /rl_agent\/server\.py/ { print $1" "$2" (RL server)" }
')"
if [[ -n "$strays" && "${ALLOW_CONCURRENT_RUNS:-0}" != "1" ]]; then
  echo "Another db_bench or RL server is running:" >&2
  echo "$strays" | sed 's/^/  /' >&2
  echo "Stop it, or deliberately set ALLOW_CONCURRENT_RUNS=1." >&2
  exit 3
fi

cp "$PIPELINE_DIR/config.sh" "$RESULTS_ROOT/config.sh"
{
  printf 'WORKLOAD_SIZES_M=%q\n' "$WORKLOAD_SIZES_M"
  printf 'WORKLOAD_PROFILE=%q\n' "$WORKLOAD_PROFILE"
  printf 'SIZE_RATIOS=%q\n' "$SIZE_RATIOS"
  printf 'EXPERIMENT_ARMS=%q\n' "$EXPERIMENT_ARMS"
  printf 'REPEATS=%q\n' "$REPEATS"
  printf 'LOAD_PERCENT=%q\n' "$LOAD_PERCENT"
  printf 'MIX_GET_RATIO=%q\n' "$MIX_GET_RATIO"
  printf 'MIX_PUT_RATIO=%q\n' "$MIX_PUT_RATIO"
  printf 'MIX_SEEK_RATIO=%q\n' "$MIX_SEEK_RATIO"
  printf 'SCAN_LENGTH=%q\n' "$SCAN_LENGTH"
  printf 'KEY_SIZE=%q\n' "$KEY_SIZE"
  printf 'VALUE_SIZE=%q\n' "$VALUE_SIZE"
  printf 'WRITE_BUFFER_SIZE=%q\n' "$WRITE_BUFFER_SIZE"
  printf 'TARGET_FILE_SIZE=%q\n' "$TARGET_FILE_SIZE"
  printf 'MAX_BYTES_FOR_LEVEL_BASE=%q\n' "$MAX_BYTES_FOR_LEVEL_BASE"
  printf 'NUM_LEVELS=%q\n' "$NUM_LEVELS"
  printf 'MAX_BACKGROUND_JOBS=%q\n' "$MAX_BACKGROUND_JOBS"
  printf 'BLOCK_CACHE_SIZE=%q\n' "$BLOCK_CACHE_SIZE"
  printf 'BLOCK_SIZE=%q\n' "$BLOCK_SIZE"
  printf 'BLOOM_BITS=%q\n' "$BLOOM_BITS"
  printf 'DISABLE_WAL=%q\n' "$DISABLE_WAL"
  printf 'L0_COMPACTION_TRIGGER=%q\n' "$L0_COMPACTION_TRIGGER"
  printf 'L0_SLOWDOWN_TRIGGER=%q\n' "$L0_SLOWDOWN_TRIGGER"
  printf 'L0_STOP_TRIGGER=%q\n' "$L0_STOP_TRIGGER"
  printf 'COMPACTION_PRIORITY=%q\n' "$COMPACTION_PRIORITY"
  printf 'DBBENCH_SEED=%q\n' "$DBBENCH_SEED"
  printf 'THREADS=%q\n' "$THREADS"
  printf 'ALTERNATE_ARM_ORDER=%q\n' "$ALTERNATE_ARM_ORDER"
  printf 'RL_PROTOCOL_VERSION=%q\n' "$RL_PROTOCOL_VERSION"
  printf 'RL_DECISION_INTERVAL_MS=%q\n' "$RL_DECISION_INTERVAL_MS"
  printf 'RL_OBSERVE_INTERVAL_MS=%q\n' "$RL_OBSERVE_INTERVAL_MS"
  printf 'RL_STRUCTURAL_DIRTY_DEADLINE_MS=%q\n' "$RL_STRUCTURAL_DIRTY_DEADLINE_MS"
  printf 'RL_OPTIONAL_MIN_SCORE=%q\n' "$RL_OPTIONAL_MIN_SCORE"
  printf 'RL_L0_ALLOW_DEFER=%q\n' "$RL_L0_ALLOW_DEFER"
  printf 'RL_EPSILON_BOUND_MS=%q\n' "$RL_EPSILON_BOUND_MS"
} > "$RESULTS_ROOT/effective_config.env"
INDEX="$RESULTS_ROOT/arms.tsv"
if [[ ! -f "$INDEX" ]]; then
  printf 'size\tT\trepeat\tarm\tstatus\tresult_directory\n' > "$INDEX"
fi

fd_hard="$(ulimit -Hn 2>/dev/null || echo 1024)"
[[ "$fd_hard" == "unlimited" ]] && fd_hard=65536
(( fd_hard > 65536 )) && fd_hard=65536
ulimit -n "$fd_hard" 2>/dev/null || true

COMMON=(
  --threads="$THREADS"
  --key_size="$KEY_SIZE"
  --value_size="$VALUE_SIZE"
  --disable_wal="$DISABLE_WAL"
  --compression_type=none
  --write_buffer_size="$WRITE_BUFFER_SIZE"
  --target_file_size_base="$TARGET_FILE_SIZE"
  --max_bytes_for_level_base="$MAX_BYTES_FOR_LEVEL_BASE"
  --num_levels="$NUM_LEVELS"
  --max_background_jobs="$MAX_BACKGROUND_JOBS"
  --open_files="$OPEN_FILES"
  --cache_size="$BLOCK_CACHE_SIZE"
  --block_size="$BLOCK_SIZE"
  --bloom_bits="$BLOOM_BITS"
  --soft_pending_compaction_bytes_limit="$SOFT_PENDING_BYTES"
  --hard_pending_compaction_bytes_limit="$HARD_PENDING_BYTES"
  --statistics
  --histogram
  --perf_level=1
  --stats_dump_period_sec="$STATS_DUMP_PERIOD_SECONDS"
)

sst_bytes() {  # $1=database directory
  find "$1" -maxdepth 1 -type f -name '*.sst' -printf '%s\n' 2>/dev/null |
    awk '{total += $1} END {printf "%.0f\n", total + 0}'
}

check_log() {  # $1=exit status, $2=log
  local status="$1" log="$2"
  if [[ "$status" -eq 0 ]] &&
      ! grep -qiE '^(put|get|seek|open) error:|IO error:|Corruption:' "$log"; then
    return 0
  fi
  echo "db_bench failed; inspect $log" >&2
  grep -iE 'error:|IO error:|Corruption:|Too many open files' "$log" |
    head -10 >&2 || true
  return 1
}

SERVER_PID=""
SERVER_SOCKET=""
stop_server() {
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    SERVER_PID=""
  fi
  [[ -z "$SERVER_SOCKET" ]] || rm -f "$SERVER_SOCKET"
  SERVER_SOCKET=""
}
trap stop_server EXIT
trap 'stop_server; exit 130' INT TERM

start_server() {  # result dir, policy seed, decay steps, eval, manifest, fingerprint
  local result_dir="$1" policy_seed="$2" decay_steps="$3" eval_mode="$4"
  local manifest_path="$5" fingerprint="$6"
  SERVER_SOCKET="/tmp/dbbench_rl_${USER:-u}_$$_${policy_seed}.sock"
  rm -f "$SERVER_SOCKET"
  env \
    RL_COMPACTION_SOCKET_PATH="$SERVER_SOCKET" \
    RL_MODEL_SAVE_PATH="$result_dir/model.pt" \
    RL_METRICS_LOG_PATH="$result_dir/metrics.jsonl" \
    RL_IO_LOG_PATH="$result_dir/io.jsonl" \
    RL_SEED="$policy_seed" \
    RL_DECISION_INTERVAL_MS="$RL_DECISION_INTERVAL_MS" \
    RL_OBSERVE_INTERVAL_MS="$RL_OBSERVE_INTERVAL_MS" \
    RL_EXPLORATION_DECAY_STEPS="$decay_steps" \
    RL_EVAL_MODE="$eval_mode" \
    RL_BASELINE_SLO_PATH="$manifest_path" \
    RL_EXPERIMENT_FINGERPRINT="$fingerprint" \
    RL_OPTIONAL_MIN_SCORE="$RL_OPTIONAL_MIN_SCORE" \
    OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    "$PYTHON" "$PROJECT_ROOT/rl_agent/server.py" \
    > "$result_dir/server.log" 2>&1 &
  SERVER_PID=$!
  for _ in $(seq 1 150); do
    [[ -S "$SERVER_SOCKET" ]] && return 0
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
      echo "RL server exited; inspect $result_dir/server.log" >&2
      return 1
    fi
    sleep 0.2
  done
  echo "RL server did not create $SERVER_SOCKET" >&2
  return 1
}

run_arm() {  # $1=size in millions, $2=T, $3=arm, $4=repeat
  local size_m="$1" ratio="$2" arm="$3" repeat="$4"
  local size_label="${size_m}M"
  local repeat_path=""
  if (( REPEATS > 1 )); then
    repeat_path="repeat-$(printf '%02d' "$repeat")/"
  fi
  local result_dir="$RESULTS_ROOT/$size_label/T${ratio}/${repeat_path}${arm}"
  local db_dir="$DB_ROOT/$size_label/T${ratio}/${repeat_path}${arm}"
  if [[ -f "$result_dir/COMPLETED" && "$RESUME" == "1" ]]; then
    echo "[skip] $size_label T=$ratio $arm"
    return
  fi
  if [[ -e "$result_dir" ]]; then
    echo "Partial result exists: $result_dir" >&2
    echo "Inspect or remove that arm before resuming." >&2
    exit 1
  fi
  mkdir -p "$result_dir" "$(dirname "$db_dir")"
  if [[ -e "$db_dir" ]]; then
    echo "Database path already exists: $db_dir" >&2
    exit 1
  fi
  mkdir -p "$db_dir"

  local total_ops=$(( size_m * 1000000 ))
  local load_ops=$(( total_ops * LOAD_PERCENT / 100 ))
  local mixed_ops=$(( total_ops - load_ops ))
  local style=0 policy_seed="null" decay_steps=0 uses_server=0 oracle=0
  local safety_enforcement=1
  local run_seed=$(( DBBENCH_SEED + repeat - 1 ))
  if [[ "$arm" != "regular" ]]; then
    style=4
  fi
  if [[ "$arm" == "oracle" ]]; then
    oracle=1
    safety_enforcement=0
  elif [[ "$arm" == "prior_only" || "$arm" == "rl" ||
          "$arm" == "unconstrained_rl" ]]; then
    uses_server=1
    [[ "$arm" != "unconstrained_rl" ]] || safety_enforcement=0
    policy_seed=$(( POLICY_SEED_BASE + repeat * 100000 + size_m * 100 + ratio ))
    local expected_decisions=$(( total_ops * 11 / 100000 ))
    decay_steps=$(( expected_decisions / 3 ))
    (( decay_steps < 20 )) && decay_steps=20
  fi

  local fingerprint
  local manifest_path="$BASELINE_SLO_DIR/$WORKLOAD_PROFILE/$size_label/T${ratio}/baseline_slo.json"
  local manifest_env_path=""
  [[ ! -f "$manifest_path" ]] || manifest_env_path="$manifest_path"
  local effective_l0_compaction="$L0_COMPACTION_TRIGGER"
  local effective_l0_slowdown="$L0_SLOWDOWN_TRIGGER"
  local effective_l0_stop="$L0_STOP_TRIGGER"
  local effective_priority="$COMPACTION_PRIORITY"
  local manifest_fingerprint=""
  if (( SLO_DRIVEN_MATRIX )) && [[ -f "$manifest_path" ]]; then
    local manifest_values=()
    mapfile -t manifest_values < <(
      "$PYTHON" - "$manifest_path" "$size_m" "$ratio" <<'PY'
import json
import sys

path, expected_size, expected_ratio = sys.argv[1:]
manifest = json.load(open(path, encoding="utf-8"))
if manifest.get("schema_version") != 1:
    raise SystemExit(f"unsupported baseline manifest schema: {path}")
options = manifest.get("selected_baseline_options", {})
required = (
    "size_millions", "size_ratio",
    "level0_file_num_compaction_trigger",
    "level0_slowdown_writes_trigger", "level0_stop_writes_trigger",
    "compaction_priority", "fingerprint",
)
missing = [name for name in required if name not in options]
if missing:
    raise SystemExit(f"manifest lacks executable selected options {missing}: {path}")
if int(options["size_millions"]) != int(expected_size) or \
        int(options["size_ratio"]) != int(expected_ratio):
    raise SystemExit(f"manifest workload/T mismatch: {path}")
for name in required[2:]:
    print(options[name])
PY
    )
    [[ "${#manifest_values[@]}" -eq 5 ]] || {
      echo "Could not load selected options from $manifest_path" >&2
      exit 1
    }
    effective_l0_compaction="${manifest_values[0]}"
    effective_l0_slowdown="${manifest_values[1]}"
    effective_l0_stop="${manifest_values[2]}"
    effective_priority="${manifest_values[3]}"
    manifest_fingerprint="${manifest_values[4]}"
  fi
  for value in "$effective_l0_compaction" "$effective_l0_slowdown" \
               "$effective_l0_stop" "$effective_priority"; do
    [[ "$value" =~ ^[0-9]+$ ]] || {
      echo "Manifest contains a non-integer compaction option: $value" >&2
      exit 1
    }
  done
  if (( effective_l0_slowdown < effective_l0_compaction ||
        effective_l0_stop < effective_l0_slowdown )); then
    echo "Invalid selected L0 trigger ordering in $manifest_path" >&2
    exit 1
  fi
  fingerprint="${WORKLOAD_PROFILE}:${size_label}:T${ratio}:k${KEY_SIZE}:v${VALUE_SIZE}:wb${WRITE_BUFFER_SIZE}:sst${TARGET_FILE_SIZE}:block${BLOCK_SIZE}:l1${MAX_BYTES_FOR_LEVEL_BASE}:levels${NUM_LEVELS}:l0-${effective_l0_compaction}-${effective_l0_slowdown}-${effective_l0_stop}:pri${effective_priority}:load${LOAD_PERCENT}:mix${MIX_GET_RATIO}-${MIX_PUT_RATIO}-${MIX_SEEK_RATIO}:scan${SCAN_LENGTH}-${MIX_MAX_SCAN_LENGTH}:cache${BLOCK_CACHE_SIZE}:bloom${BLOOM_BITS}:bg${MAX_BACKGROUND_JOBS}:threads${THREADS}:wal${DISABLE_WAL}"
  if [[ -n "$manifest_fingerprint" && "$fingerprint" != "$manifest_fingerprint" ]]; then
    echo "Current geometry does not match $manifest_path" >&2
    echo "expected: $manifest_fingerprint" >&2
    echo "current:  $fingerprint" >&2
    exit 1
  fi
  if (( uses_server )); then
    local eval_mode=0
    [[ "$arm" != "prior_only" ]] || eval_mode=1
    start_server "$result_dir" "$policy_seed" "$decay_steps" "$eval_mode" \
      "$manifest_env_path" "$fingerprint"
  fi

  command=(
    "$DB_BENCH"
    --benchmarks=filluniquerandom,mixgraph,waitforcompaction,levelstats,stats
    --num="$load_ops"
    --reads="$mixed_ops"
    --mix_get_ratio="$MIX_GET_RATIO"
    --mix_put_ratio="$MIX_PUT_RATIO"
    --mix_seek_ratio="$MIX_SEEK_RATIO"
    --value_theta="$VALUE_SIZE" --value_k=0 --value_sigma=0
    --iter_theta="$SCAN_LENGTH" --iter_k=0 --iter_sigma=0
    --mix_max_scan_len="$MIX_MAX_SCAN_LENGTH"
    --keyrange_num=1
    --max_bytes_for_level_multiplier="$ratio"
    --level0_file_num_compaction_trigger="$effective_l0_compaction"
    --level0_slowdown_writes_trigger="$effective_l0_slowdown"
    --level0_stop_writes_trigger="$effective_l0_stop"
    --compaction_pri="$effective_priority"
    --compaction_style="$style"
    --use_existing_db=0
    --stats_interval_seconds=10
    --db="$db_dir"
    --seed="$run_seed"
    "${COMMON[@]}"
  )
  printf '%q ' "${command[@]}" > "$result_dir/command.txt"
  printf '\n' >> "$result_dir/command.txt"
  {
    printf 'size=%s\n' "$size_label"
    printf 'workload_profile=%s\n' "$WORKLOAD_PROFILE"
    printf 'total_operations=%s\n' "$total_ops"
    printf 'load_operations=%s\n' "$load_ops"
    printf 'mixed_operations=%s\n' "$mixed_ops"
    printf 'size_ratio=%s\n' "$ratio"
    printf 'arm=%s\n' "$arm"
    printf 'dbbench_seed=%s\n' "$run_seed"
    printf 'repeat=%s\n' "$repeat"
    printf 'policy_seed=%s\n' "$policy_seed"
    printf 'rl_protocol_version=%s\n' "$RL_PROTOCOL_VERSION"
    printf 'rl_file_picker=rocksdb_native\n'
    printf 'rl_exploration_decay_steps=%s\n' "$decay_steps"
    printf 'experiment_fingerprint=%s\n' "$fingerprint"
    printf 'level0_file_num_compaction_trigger=%s\n' "$effective_l0_compaction"
    printf 'level0_slowdown_writes_trigger=%s\n' "$effective_l0_slowdown"
    printf 'level0_stop_writes_trigger=%s\n' "$effective_l0_stop"
    printf 'compaction_priority=%s\n' "$effective_priority"
    printf 'baseline_slo_path=%s\n' "$manifest_path"
    printf 'rl_trigger_oracle=%s\n' "$oracle"
    printf 'rl_safety_enforcement=%s\n' "$safety_enforcement"
    printf 'rl_optional_min_score=%s\n' "$RL_OPTIONAL_MIN_SCORE"
    printf 'rl_l0_allow_defer=%s\n' "$RL_L0_ALLOW_DEFER"
    printf 'rl_epsilon_bound_ms=%s\n' "$RL_EPSILON_BOUND_MS"
  } > "$result_dir/metadata.env"
  git rev-parse HEAD > "$result_dir/git_revision.txt" 2>/dev/null || true
  git -C lib/rocksdb rev-parse HEAD > "$result_dir/rocksdb_revision.txt" 2>/dev/null || true
  git status --short > "$result_dir/git_status.txt" 2>/dev/null || true
  git -C lib/rocksdb status --short \
    > "$result_dir/rocksdb_status.txt" 2>/dev/null || true
  git diff --binary > "$result_dir/project_worktree.patch" 2>/dev/null || true
  git -C lib/rocksdb diff --binary \
    > "$result_dir/rocksdb_worktree.patch" 2>/dev/null || true
  {
    printf 'uname=%s\n' "$(uname -a)"
    printf 'hostname=%s\n' "$(hostname)"
    printf 'db_bench=%s\n' "$DB_BENCH"
    printf 'python=%s\n' "$PYTHON"
    printf 'db_root=%s\n' "$DB_ROOT"
    printf 'results_root=%s\n' "$RESULTS_ROOT"
  } > "$result_dir/environment.env"

  echo "[run] $size_label T=$ratio $arm"
  local start_ns end_ns status
  start_ns="$(date +%s%N)"
  set +e
  if (( uses_server )); then
    env RL_COMPACTION_SOCKET_PATH="$SERVER_SOCKET" \
      RL_COMPACTION_SOCKET_TIMEOUT_MS="$RL_SOCKET_TIMEOUT_MS" \
      RL_PRESSURE_EPISODE_LOG="$result_dir/pressure_episodes.jsonl" \
      RL_TRIGGER_TRACE_PATH="$result_dir/trigger_trace.jsonl" \
      RL_EXPERIMENT_FINGERPRINT="$fingerprint" \
      RL_BASELINE_SLO_PATH="$manifest_env_path" \
      RL_REQUIRE_BASELINE_SLO="$RL_REQUIRE_BASELINE_SLO" \
      RL_SAFETY_ENFORCEMENT="$safety_enforcement" \
      RL_STRUCTURAL_DIRTY_DEADLINE_MS="$RL_STRUCTURAL_DIRTY_DEADLINE_MS" \
      RL_OPTIONAL_MIN_SCORE="$RL_OPTIONAL_MIN_SCORE" \
      RL_L0_ALLOW_DEFER="$RL_L0_ALLOW_DEFER" \
      RL_EPSILON_BOUND_MS="$RL_EPSILON_BOUND_MS" \
      "${command[@]}" > "$result_dir/run.log" 2>&1
    status=$?
  elif (( oracle )); then
    # The oracle opens every due level by construction, so the L0 posture is
    # already implied; pass it anyway so both arms record the same control
    # configuration.
    env RL_TRIGGER_ORACLE=1 RL_SAFETY_ENFORCEMENT=0 \
      RL_STRUCTURAL_DIRTY_DEADLINE_MS="$RL_STRUCTURAL_DIRTY_DEADLINE_MS" \
      RL_PRESSURE_EPISODE_LOG="$result_dir/pressure_episodes.jsonl" \
      RL_TRIGGER_TRACE_PATH="$result_dir/trigger_trace.jsonl" \
      RL_EXPERIMENT_FINGERPRINT="$fingerprint" \
      RL_OPTIONAL_MIN_SCORE="$RL_OPTIONAL_MIN_SCORE" \
      RL_L0_ALLOW_DEFER="$RL_L0_ALLOW_DEFER" \
      RL_EPSILON_BOUND_MS="$RL_EPSILON_BOUND_MS" \
      "${command[@]}" > "$result_dir/run.log" 2>&1
    status=$?
  else
    env RL_PRESSURE_EPISODE_LOG="$result_dir/pressure_episodes.jsonl" \
      "${command[@]}" > "$result_dir/run.log" 2>&1
    status=$?
  fi
  set -e
  end_ns="$(date +%s%N)"
  (( ! uses_server )) || stop_server
  check_log "$status" "$result_dir/run.log" || exit 4
  printf 'elapsed_seconds=%.6f\n' "$(( end_ns - start_ns ))e-9" \
    >> "$result_dir/metadata.env"
  cp "$db_dir/LOG" "$result_dir/rocksdb_LOG.txt" 2>/dev/null || true
  local before after
  before="$(sst_bytes "$db_dir")"

  # This process is outside the measured phase. It supplies the physical size
  # of the same data after garbage removal; its I/O is not included in WAF.
  set +e
  "$DB_BENCH" --benchmarks=compact --num="$load_ops" \
    --max_bytes_for_level_multiplier="$ratio" --compaction_style=0 \
    --level0_file_num_compaction_trigger="$effective_l0_compaction" \
    --level0_slowdown_writes_trigger="$effective_l0_slowdown" \
    --level0_stop_writes_trigger="$effective_l0_stop" \
    --compaction_pri="$effective_priority" \
    --use_existing_db=1 --db="$db_dir" "${COMMON[@]}" \
    > "$result_dir/compact.log" 2>&1
  status=$?
  set -e
  check_log "$status" "$result_dir/compact.log" || exit 4
  after="$(sst_bytes "$db_dir")"
  cp "$db_dir/LOG" "$result_dir/rocksdb_LOG_after_compact.txt" 2>/dev/null || true
  {
    printf 'sst_bytes_before_full_compaction=%s\n' "$before"
    printf 'sst_bytes_after_full_compaction=%s\n' "$after"
  } > "$result_dir/sizes.env"

  touch "$result_dir/COMPLETED"
  printf '%s\t%s\t%s\t%s\tcompleted\t%s\n' \
    "$size_label" "$ratio" "$repeat" "$arm" "$result_dir" >> "$INDEX"
  if [[ "$KEEP_DATABASES" != "1" ]]; then
    rm -rf "$db_dir"
  fi
}

echo "[matrix] sizes=$WORKLOAD_SIZES_M T=$SIZE_RATIOS arms=$EXPERIMENT_ARMS repeats=$REPEATS"
echo "[output] $RESULTS_ROOT"
echo "[db]     $DB_ROOT"
pair_index=0
read -r -a arm_list <<< "$EXPERIMENT_ARMS"
for repeat in $(seq 1 "$REPEATS"); do
  for size_m in $WORKLOAD_SIZES_M; do
    for ratio in $SIZE_RATIOS; do
      if [[ "$ALTERNATE_ARM_ORDER" == "1" ]] && (( pair_index % 2 == 1 )); then
        for (( index=${#arm_list[@]}-1; index>=0; --index )); do
          run_arm "$size_m" "$ratio" "${arm_list[index]}" "$repeat"
        done
      else
        for arm in "${arm_list[@]}"; do
          run_arm "$size_m" "$ratio" "$arm" "$repeat"
        done
      fi
      pair_index=$(( pair_index + 1 ))
    done
  done
done

echo
echo "Experiments complete: $RESULTS_ROOT"
echo "Generate graphs with:"
echo "  scripts/dbbench_pipeline/04_generate_graphs.sh --results '$RESULTS_ROOT'"
