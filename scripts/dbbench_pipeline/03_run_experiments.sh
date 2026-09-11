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
  if [[ "$EXPERIMENT_ARMS" == "regular" ]]; then
    PYTHON="$(command -v python3)"
  else
    echo "Missing $PYTHON; run step 00 first." >&2
    exit 1
  fi
}
if [[ "$EXPERIMENT_ARMS" != "regular" ]]; then
"$PYTHON" -c 'import numpy, torch' >/dev/null || {
  echo "The pipeline Python environment does not contain numpy and torch." >&2
  exit 1
}
fi
DBBENCH_SHA256="$(sha256sum "$DB_BENCH" | awk '{print $1}')"
# Written by 01_build_rocksdb.sh. A build directory predating it records the
# architecture as unrecorded rather than blocking the run.
BUILD_PROVENANCE="$(dirname "$DB_BENCH")/build_provenance.env"
ROCKSDB_PORTABLE_BUILT="unrecorded"
BUILD_CXX_VERSION="unrecorded"
if [[ -f "$BUILD_PROVENANCE" ]]; then
  ROCKSDB_PORTABLE_BUILT="$(awk -F= '/^rocksdb_portable=/ {print $2}' "$BUILD_PROVENANCE")"
  BUILD_CXX_VERSION="$(awk -F= '/^build_cxx=/ {sub(/^build_cxx=/, ""); print}' "$BUILD_PROVENANCE")"
fi
RESEARCH_OBJECTIVE_SHA256="$(sha256sum config/research_objective_contract.v2.json | awk '{print $1}')"

# Expand a taskset -c list ("0-7,12") into one CPU number per line.
expand_cpu_list() {
  local part start end
  [[ -n "$1" ]] || return 0
  for part in ${1//,/ }; do
    if [[ "$part" =~ ^([0-9]+)-([0-9]+)$ ]]; then
      start="${BASH_REMATCH[1]}"; end="${BASH_REMATCH[2]}"
      (( start <= end )) || { echo "Inverted CPU range: $part" >&2; return 1; }
      seq "$start" "$end"
    elif [[ "$part" =~ ^[0-9]+$ ]]; then
      echo "$part"
    else
      echo "Malformed CPU list element: $part" >&2
      return 1
    fi
  done
}

# CPUs sharing a last-level cache with this one. Empty when the kernel exports
# no L3 index, which is not an error.
l3_siblings() {
  local index level
  for index in /sys/devices/system/cpu/cpu"$1"/cache/index*; do
    [[ -r "$index/level" && -r "$index/shared_cpu_list" ]] || continue
    level="$(<"$index/level")"
    [[ "$level" == 3 ]] || continue
    expand_cpu_list "$(<"$index/shared_cpu_list")"
    return 0
  done
}

# How many distinct last-level caches the machine exposes. Zero when it
# exposes none, in which case no cache claim can be checked either way.
count_l3_domains() {
  local cpu
  for cpu in $(expand_cpu_list "$(</sys/devices/system/cpu/online)"); do
    l3_siblings "$cpu" | sort -n | tr '\n' ','
    echo
  done | sort -u | grep -c . || echo 0
}

DBBENCH_LAUNCHER=()
CONTROLLER_LAUNCHER=()
if [[ -n "$DBBENCH_CPUS" || -n "$CONTROLLER_CPUS" ]]; then
  command -v taskset >/dev/null || {
    echo "CPU pinning was requested but taskset is not installed." >&2
    echo "Install util-linux, or clear DBBENCH_CPUS and CONTROLLER_CPUS." >&2
    exit 1
  }
  [[ -n "$DBBENCH_CPUS" && -n "$CONTROLLER_CPUS" ]] || {
    echo "Pin both DBBENCH_CPUS and CONTROLLER_CPUS, or neither." >&2
    echo "Pinning one leaves the other free to run on its cores." >&2
    exit 1
  }
  dbbench_expanded="$(expand_cpu_list "$DBBENCH_CPUS")" || exit 1
  controller_expanded="$(expand_cpu_list "$CONTROLLER_CPUS")" || exit 1
  online_expanded="$(expand_cpu_list "$(</sys/devices/system/cpu/online)")" || exit 1
  mapfile -t dbbench_cpu_set <<< "$dbbench_expanded"
  mapfile -t controller_cpu_set <<< "$controller_expanded"
  online_cpus=" $(tr '\n' ' ' <<< "$online_expanded")"
  for cpu in "${dbbench_cpu_set[@]}" "${controller_cpu_set[@]}"; do
    [[ "$cpu" =~ ^[0-9]+$ ]] || {
      echo "Unusable CPU set: $DBBENCH_CPUS / $CONTROLLER_CPUS" >&2
      exit 1
    }
    [[ "$online_cpus" == *" $cpu "* ]] || {
      echo "CPU $cpu is not online; check lscpu -e before pinning." >&2
      exit 1
    }
  done
  # Overlapping sets time-share a core, which is the interference the pinning
  # exists to remove.
  for cpu in "${controller_cpu_set[@]}"; do
    for other in "${dbbench_cpu_set[@]}"; do
      (( cpu != other )) || {
        echo "DBBENCH_CPUS and CONTROLLER_CPUS both contain CPU $cpu." >&2
        exit 1
      }
    done
  done
  # Distinct cores are not enough when they share a last-level cache: the
  # controller then evicts db_bench cache lines from a core of its own. Only
  # refuse when the machine actually offers a disjoint choice.
  shared_l3=""
  for cpu in "${dbbench_cpu_set[@]}"; do
    dbbench_l3=" $(l3_siblings "$cpu" | tr '\n' ' ')"
    for other in "${controller_cpu_set[@]}"; do
      [[ "$dbbench_l3" != *" $other "* ]] || shared_l3="$cpu/$other"
    done
  done
  if [[ -n "$shared_l3" ]]; then
    l3_domains="$(count_l3_domains)"
    if (( l3_domains > 1 )); then
      echo "CPUs ${shared_l3%%/*} and ${shared_l3##*/} share a last-level cache," >&2
      echo "so the controller would evict db_bench cache lines from its own core." >&2
      echo "This machine exposes $l3_domains L3 domains; pick the two sets from" >&2
      echo "different ones. Inspect:" >&2
      echo "  cat /sys/devices/system/cpu/cpu0/cache/index3/shared_cpu_list" >&2
      exit 1
    fi
    echo "[pinning] this machine exposes one L3 domain, so db_bench and the" >&2
    echo "[pinning] controller share it. Cache interference is not eliminated." >&2
  fi
  DBBENCH_LAUNCHER=(taskset -c "$DBBENCH_CPUS")
  CONTROLLER_LAUNCHER=(taskset -c "$CONTROLLER_CPUS")
  echo "[pinning] db_bench CPUs $DBBENCH_CPUS, controller CPUs $CONTROLLER_CPUS"
fi

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
case "$RL_RUN_PHASE" in
  calibration|holdout)
    [[ "$EXPERIMENT_ARMS" == "oracle" ]] || {
      echo "RL_RUN_PHASE=$RL_RUN_PHASE requires EXPERIMENT_ARMS=oracle" >&2
      exit 1
    }
    ;;
  experiment) ;;
  *) echo "Unsupported RL_RUN_PHASE: $RL_RUN_PHASE" >&2; exit 1 ;;
esac
SLO_DRIVEN_MATRIX=0
for arm in $EXPERIMENT_ARMS; do
  if [[ "$arm" == "prior_only" || "$arm" == "rl" ||
        "$arm" == "unconstrained_rl" ]]; then
    SLO_DRIVEN_MATRIX=1
  fi
done
if [[ "$RL_RUN_PHASE" == "calibration" || "$RL_RUN_PHASE" == "holdout" ]]; then
  SLO_DRIVEN_MATRIX=1
fi
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
  printf 'RL_RUN_PHASE=%q\n' "$RL_RUN_PHASE"
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
  --level_compaction_dynamic_level_bytes=false
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
  local manifest_path="$5" fingerprint="$6" anneal_seconds="${7:-0}"
  SERVER_SOCKET="/tmp/dbbench_rl_${USER:-u}_$$_${policy_seed}.sock"
  rm -f "$SERVER_SOCKET"
  env \
    RL_COMPACTION_SOCKET_PATH="$SERVER_SOCKET" \
    RL_MODEL_SAVE_PATH="$result_dir/model.pt" \
    RL_METRICS_LOG_PATH="$result_dir/metrics.jsonl" \
    RL_IO_LOG_PATH="$result_dir/io.jsonl" \
    RL_SERVER_SUMMARY_PATH="$result_dir/server_summary.json" \
    RL_SEED="$policy_seed" \
    RL_DECISION_INTERVAL_MS="$RL_DECISION_INTERVAL_MS" \
    RL_OBSERVE_INTERVAL_MS="$RL_OBSERVE_INTERVAL_MS" \
    RL_EXPLORATION_DECAY_STEPS="$decay_steps" \
    RL_EXPLORATION_ANNEAL_SECONDS="$anneal_seconds" \
    RL_EVAL_MODE="$eval_mode" \
    RL_BASELINE_SLO_PATH="$manifest_path" \
    RL_EXPERIMENT_FINGERPRINT="$fingerprint" \
    RL_OPTIONAL_MIN_SCORE="$RL_OPTIONAL_MIN_SCORE" \
    OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    ${CONTROLLER_LAUNCHER[@]+"${CONTROLLER_LAUNCHER[@]}"} \
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
  local manifest_path="$BASELINE_SLO_DIR/$WORKLOAD_PROFILE/$size_label/T${ratio}/baseline_slo.json"
  local manifest_sha256=""
  if (( SLO_DRIVEN_MATRIX )); then
    [[ -f "$manifest_path" ]] || {
      echo "Missing required baseline manifest: $manifest_path" >&2
      exit 1
    }
    manifest_sha256="$(sha256sum "$manifest_path" | awk '{print $1}')"
  fi
  if [[ -f "$result_dir/COMPLETED" && "$RESUME" == "1" ]]; then
    if (( SLO_DRIVEN_MATRIX )); then
      local recorded_manifest_sha256
      recorded_manifest_sha256="$(sed -n 's/^baseline_slo_sha256=//p' \
        "$result_dir/metadata.env" | tail -n 1)"
      if [[ "$recorded_manifest_sha256" != "$manifest_sha256" ]]; then
        echo "Cannot resume $result_dir against a different baseline manifest." >&2
        echo "recorded: ${recorded_manifest_sha256:-missing}" >&2
        echo "current:  $manifest_sha256" >&2
        exit 1
      fi
    fi
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
  local anneal_seconds=0 anneal_source="none"
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
    # D6. Exploration anneals on wall time, not decision count.
    #
    # The previous formula encoded 0.11 decisions per 1000 operations, measured
    # when the observation rate was 2.6/s. The Phase 1a repair raised that to
    # ~19.95/s at a 50 ms cadence, so the derived schedule annealed 5.5x to 27x
    # too fast -- exploration reached its floor within the first few percent of
    # a run instead of the first third. Deriving a NEW decision count from the
    # current cadence would repeat the defect the next time the cadence moves.
    #
    # Duration comes from the paired regular arm when it has already run;
    # 03 appends elapsed_seconds to metadata.env only after an arm completes,
    # and ALTERNATE_ARM_ORDER puts the regular arm second on odd pairs, so the
    # fallback is taken on roughly half of all pairs by design.
    local paired_regular="$RESULTS_ROOT/$size_label/T${ratio}/${repeat_path}regular/metadata.env"
    local expected_seconds="" anneal_source="expected_per_mop"
    if [[ -f "$paired_regular" ]]; then
      expected_seconds="$(sed -n 's/^elapsed_seconds=//p' "$paired_regular" | tail -n 1)"
      [[ -z "$expected_seconds" ]] || anneal_source="paired_regular_arm"
    fi
    if [[ -z "$expected_seconds" ]]; then
      expected_seconds=$(( size_m * RL_EXPECTED_RUN_SECONDS_PER_MOP ))
    fi
    anneal_seconds="$(awk -v s="$expected_seconds" -v f="$RL_EXPLORATION_ANNEAL_FRACTION" \
      'BEGIN { v = s * f; if (v < 1) v = 1; printf "%.3f", v }')"
    # Retained so the step-based ablation remains reachable; it is inert
    # whenever anneal_seconds is positive.
    decay_steps=20
  fi

  local fingerprint
  local manifest_env_path=""
  [[ ! -f "$manifest_path" ]] || manifest_env_path="$manifest_path"
  local effective_l0_compaction="$L0_COMPACTION_TRIGGER"
  local effective_l0_slowdown="$L0_SLOWDOWN_TRIGGER"
  local effective_l0_stop="$L0_STOP_TRIGGER"
  local effective_priority="$COMPACTION_PRIORITY"
  local manifest_fingerprint=""
  if (( SLO_DRIVEN_MATRIX )) && [[ ! -f "$manifest_path" ]]; then
    echo "Missing required baseline manifest: $manifest_path" >&2
    exit 1
  fi
  if (( SLO_DRIVEN_MATRIX )) && [[ -f "$manifest_path" ]]; then
    local manifest_values=()
    mapfile -t manifest_values < <(
      "$PYTHON" - "$manifest_path" "$size_m" "$ratio" "$RL_RUN_PHASE" <<'PY'
import json
import sys

path, expected_size, expected_ratio, phase = sys.argv[1:]
manifest = json.load(open(path, encoding="utf-8"))
if manifest.get("schema_version") != 2 or \
        manifest.get("metric_definitions_version") != "trigger-v2-logical-v2":
    raise SystemExit(f"unsupported baseline manifest schema: {path}")
calibrated = manifest.get("guard_calibrated") is True
if phase == "calibration" and calibrated:
    raise SystemExit(f"calibration requires a provisional manifest: {path}")
if phase in ("holdout", "experiment") and not calibrated:
    raise SystemExit(f"{phase} requires a calibrated live guard: {path}")
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
  fingerprint="${WORKLOAD_PROFILE}:${size_label}:T${ratio}:k${KEY_SIZE}:v${VALUE_SIZE}:wb${WRITE_BUFFER_SIZE}:sst${TARGET_FILE_SIZE}:block${BLOCK_SIZE}:l1${MAX_BYTES_FOR_LEVEL_BASE}:levels${NUM_LEVELS}:l0-${effective_l0_compaction}-${effective_l0_slowdown}-${effective_l0_stop}:pri${effective_priority}:load${LOAD_PERCENT}:mix${MIX_GET_RATIO}-${MIX_PUT_RATIO}-${MIX_SEEK_RATIO}:scan${SCAN_LENGTH}-${MIX_MAX_SCAN_LENGTH}:cache${BLOCK_CACHE_SIZE}:bloom${BLOOM_BITS}:bg${MAX_BACKGROUND_JOBS}:threads${THREADS}:wal${DISABLE_WAL}:dynamic0:soft${SOFT_PENDING_BYTES}:hard${HARD_PENDING_BYTES}:binary${DBBENCH_SHA256}:objective${RESEARCH_OBJECTIVE_SHA256}"
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
      "$manifest_env_path" "$fingerprint" "$anneal_seconds"
  fi

  command=(
    ${DBBENCH_LAUNCHER[@]+"${DBBENCH_LAUNCHER[@]}"}
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
    printf 'rl_run_phase=%s\n' "$RL_RUN_PHASE"
    printf 'rl_file_picker=rocksdb_native\n'
    printf 'rl_exploration_decay_steps=%s\n' "$decay_steps"
    printf 'experiment_fingerprint=%s\n' "$fingerprint"
    printf 'dbbench_sha256=%s\n' "$DBBENCH_SHA256"
    printf 'rocksdb_portable=%s\n' "$ROCKSDB_PORTABLE_BUILT"
    printf 'build_cxx=%s\n' "$BUILD_CXX_VERSION"
    printf 'dbbench_cpus=%s\n' "${DBBENCH_CPUS:-unpinned}"
    printf 'controller_cpus=%s\n' "${CONTROLLER_CPUS:-unpinned}"
    printf 'research_objective_sha256=%s\n' "$RESEARCH_OBJECTIVE_SHA256"
    printf 'level_compaction_dynamic_level_bytes=false\n'
    printf 'max_bytes_for_level_base=%s\n' "$MAX_BYTES_FOR_LEVEL_BASE"
    printf 'baseline_level_base_scale=%s\n' "${BASELINE_LEVEL_BASE_SCALE:-1}"
    printf 'num_levels=%s\n' "$NUM_LEVELS"
    printf 'level0_file_num_compaction_trigger=%s\n' "$effective_l0_compaction"
    printf 'level0_slowdown_writes_trigger=%s\n' "$effective_l0_slowdown"
    printf 'level0_stop_writes_trigger=%s\n' "$effective_l0_stop"
    printf 'compaction_priority=%s\n' "$effective_priority"
    printf 'baseline_slo_path=%s\n' "$manifest_path"
    printf 'baseline_slo_sha256=%s\n' "$manifest_sha256"
    printf 'rl_trigger_oracle=%s\n' "$oracle"
    printf 'rl_safety_enforcement=%s\n' "$safety_enforcement"
    printf 'rl_optional_min_score=%s\n' "$RL_OPTIONAL_MIN_SCORE"
    printf 'rl_l0_allow_defer=%s\n' "$RL_L0_ALLOW_DEFER"
    printf 'rl_crossing_posture=%s\n' "$RL_CROSSING_POSTURE"
    printf 'rl_exploration_anneal_seconds=%s\n' "$anneal_seconds"
    printf 'rl_exploration_anneal_source=%s\n' "$anneal_source"
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
  local trigger_trace_path="$result_dir/trigger_trace.jsonl"
  local latency_window_log=""
  local safety_shadow_log=""
  local oracle_manifest_env_path=""
  if [[ "$RL_RUN_PHASE" == "calibration" ]]; then
    trigger_trace_path=""
    latency_window_log="$result_dir/latency_windows.jsonl"
    oracle_manifest_env_path="$manifest_env_path"
  elif [[ "$RL_RUN_PHASE" == "holdout" ]]; then
    trigger_trace_path=""
    safety_shadow_log="$result_dir/safety_shadow.jsonl"
    oracle_manifest_env_path="$manifest_env_path"
  fi
  start_ns="$(date +%s%N)"
  set +e
  if (( uses_server )); then
    env RL_COMPACTION_SOCKET_PATH="$SERVER_SOCKET" \
      RL_COMPACTION_SOCKET_TIMEOUT_MS="$RL_SOCKET_TIMEOUT_MS" \
      RL_PRESSURE_EPISODE_LOG="$result_dir/pressure_episodes.jsonl" \
      RL_TRIGGER_TRACE_PATH="$trigger_trace_path" \
      RL_LATENCY_WINDOW_LOG="$latency_window_log" \
      RL_SAFETY_SHADOW_LOG="$safety_shadow_log" \
      RL_EXPERIMENT_FINGERPRINT="$fingerprint" \
      RL_BASELINE_SLO_PATH="$manifest_env_path" \
      RL_BASELINE_SLO_SHA256="$manifest_sha256" \
      RL_REQUIRE_BASELINE_SLO="$RL_REQUIRE_BASELINE_SLO" \
      RL_SAFETY_ENFORCEMENT="$safety_enforcement" \
      RL_STRUCTURAL_DIRTY_DEADLINE_MS="$RL_STRUCTURAL_DIRTY_DEADLINE_MS" \
      RL_OPTIONAL_MIN_SCORE="$RL_OPTIONAL_MIN_SCORE" \
      RL_L0_ALLOW_DEFER="$RL_L0_ALLOW_DEFER" \
      RL_CROSSING_POSTURE="$RL_CROSSING_POSTURE" \
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
      RL_TRIGGER_TRACE_PATH="$trigger_trace_path" \
      RL_LATENCY_WINDOW_LOG="$latency_window_log" \
      RL_SAFETY_SHADOW_LOG="$safety_shadow_log" \
      RL_BASELINE_SLO_PATH="$oracle_manifest_env_path" \
      RL_BASELINE_SLO_SHA256="$manifest_sha256" \
      RL_REQUIRE_BASELINE_SLO=0 \
      RL_EXPERIMENT_FINGERPRINT="$fingerprint" \
      RL_OPTIONAL_MIN_SCORE="$RL_OPTIONAL_MIN_SCORE" \
      RL_L0_ALLOW_DEFER="$RL_L0_ALLOW_DEFER" \
      RL_CROSSING_POSTURE="$RL_CROSSING_POSTURE" \
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
  if (( uses_server )); then
    set +e
    "$PYTHON" "$PIPELINE_DIR/10_validate_learning_health.py" \
      --summary "$result_dir/server_summary.json" --arm "$arm" \
      --output "$result_dir/learning_health.json" \
      > "$result_dir/learning_health.log" 2>&1
    status=$?
    set -e
    if (( status != 0 )); then
      touch "$result_dir/FAILED_LEARNING_HEALTH"
      echo "Learning-health gate failed; preserving result and DB: $result_dir" >&2
      sed -n '1,120p' "$result_dir/learning_health.log" >&2
      exit 5
    fi
  fi
  printf 'elapsed_seconds=%.6f\n' "$(( end_ns - start_ns ))e-9" \
    >> "$result_dir/metadata.env"
  cp "$db_dir/LOG" "$result_dir/rocksdb_LOG.txt" 2>/dev/null || true
  "$PYTHON" "$PIPELINE_DIR/compaction_measurements.py" \
    "$result_dir/rocksdb_LOG.txt" --num-levels "$NUM_LEVELS" \
    --output "$result_dir/compaction_measurements.json" || {
      echo "Incomplete Gate-0 measurements; keeping database and logs: $result_dir" >&2
      exit 6
    }
  local before after
  before="$(sst_bytes "$db_dir")"

  # This process is outside the measured phase. It supplies the physical size
  # of the same data after garbage removal; its I/O is not included in WAF.
  set +e
  ${DBBENCH_LAUNCHER[@]+"${DBBENCH_LAUNCHER[@]}"} \
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
