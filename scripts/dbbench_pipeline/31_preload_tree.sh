#!/usr/bin/env bash
# One preloaded tree (PREREGISTRATION D-27 §1 and §4; D-29 §1): load
# PRELOAD_KEYS keys with filluniquerandom at PRELOAD_SEED under native
# compaction, settle (D-13 §6), close, and archive the closed database with
# its identity and load statistics. Every run of 03 at this setting, of every
# workload, repeat and arm, then opens a checked copy (TREE_DIR).
#
#   CONFIRM_PRELOAD=YES SIZE_RATIOS=4 MAX_BYTES_FOR_LEVEL_BASE=134217728 \
#     L0_COMPACTION_TRIGGER=4 scripts/dbbench_pipeline/31_preload_tree.sh
#
# The setting is SIZE_RATIOS (one T), MAX_BYTES_FOR_LEVEL_BASE and
# L0_COMPACTION_TRIGGER; the geometry, keys, seed and memory cap are
# config.sh's (a long tree, of more than PREFLIGHT_SHORT_RUN_MAX_M million
# keys, must have D-29 §1's, and a passed preflight). The tree goes to
#   $TREE_ROOT/T<T>-l1<MiB>-k<K>-n<keys>-s<seed>/
#     db/                 the closed database, without its info logs
#     tree.json           identity, options, binary, load statistics
#     load.out            db_bench's output; host_log_load.jsonl; LOG.load
#     COMPLETED
# and its last line of output is TREE_DIR=<that folder>. A completed tree is
# kept and reused (exit 0). A failed preload's folder is renamed
# <folder>.failed-<UTC time>, with its evidence, and the stage exits 1.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

[[ $# -eq 0 ]] || { echo "Unexpected argument: $1" >&2; exit 2; }
if [[ "${CONFIRM_PRELOAD:-}" != "YES" ]]; then
  echo "This loads and archives one tree; set CONFIRM_PRELOAD=YES." >&2
  exit 2
fi
refuse() { echo "$*" >&2; exit 1; }

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
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"
[[ -x "$DB_BENCH" ]] || refuse "Missing $DB_BENCH; run steps 01 and 02 first."
PRELOAD_KEYS="${PRELOAD_KEYS:-$SUITE_PRELOAD_KEYS}"

T="$SIZE_RATIOS"
K="$L0_COMPACTION_TRIGGER"
for value in "$T" "$K" "$PRELOAD_KEYS" "$PRELOAD_SEED" "$MAX_BYTES_FOR_LEVEL_BASE"; do
  [[ "$value" =~ ^[0-9]+$ ]] ||
    refuse "SIZE_RATIOS (one T), L0_COMPACTION_TRIGGER, PRELOAD_KEYS," \
           "PRELOAD_SEED and MAX_BYTES_FOR_LEVEL_BASE must be whole numbers;" \
           "got '$value'."
done
(( T > 1 && PRELOAD_KEYS > 0 )) || refuse "T must exceed 1 and PRELOAD_KEYS 0."
(( MAX_BYTES_FOR_LEVEL_BASE % 1048576 == 0 )) ||
  refuse "MAX_BYTES_FOR_LEVEL_BASE must be whole MiB (it names the tree)."
L1_MIB=$(( MAX_BYTES_FOR_LEVEL_BASE / 1048576 ))
# PATHWAYS A-Impl-6: K <= min(floor(C_1 / F), K_slow - 1), so the file count
# fires before the byte part of L0's score, and before the slowdown.
(( K >= 1 && K <= MAX_BYTES_FOR_LEVEL_BASE / WRITE_BUFFER_SIZE &&
   K <= L0_SLOWDOWN_TRIGGER - 1 && L0_SLOWDOWN_TRIGGER <= L0_STOP_TRIGGER )) ||
  refuse "L0 trigger $K is not admissible at L1 ${L1_MIB} MiB, buffer" \
         "$WRITE_BUFFER_SIZE and slowdown $L0_SLOWDOWN_TRIGGER (A-Impl-6:" \
         "K <= min(floor(C_1/F), K_slow - 1))."

LONG_TREE=0
(( PRELOAD_KEYS <= PREFLIGHT_SHORT_RUN_MAX_M * 1000000 )) || LONG_TREE=1
if (( LONG_TREE )); then
  # D-29 §1: the suite's preload, geometry and memory cap.
  for pair in PRELOAD_KEYS:SUITE_PRELOAD_KEYS \
      WRITE_BUFFER_SIZE:SUITE_WRITE_BUFFER_SIZE \
      TARGET_FILE_SIZE:SUITE_TARGET_FILE_SIZE \
      BLOCK_CACHE_SIZE:SUITE_BLOCK_CACHE_SIZE OPEN_FILES:SUITE_OPEN_FILES \
      RUN_MEMORY_MAX:SUITE_MEMORY_MAX; do
    name="${pair%%:*}" wanted="${pair##*:}"
    [[ "${!name}" == "${!wanted}" ]] ||
      refuse "A long tree has $name ${!wanted} (D-29 §1, config.sh's $wanted);" \
             "got '${!name}'."
  done
  plugin_args=()
  [[ ! -d controller ]] || plugin_args=(--plugin "$CONTROLLER_PLUGIN")
  "$PYTHON" "$PIPELINE_DIR/preflight_marker.py" check --marker "$PREFLIGHT_MARKER" \
    --db-bench "$DB_BENCH" ${plugin_args[@]+"${plugin_args[@]}"} --arms native \
    --suite || {
    echo "Refusing a ${PRELOAD_KEYS}-key tree without a matching preflight;" \
         "run 13_run_preflight_verification.sh first." >&2
    exit 7
  }
fi

DBBENCH_SHA256="$("$PYTHON" "$PIPELINE_DIR/preflight_marker.py" identity --db-bench "$DB_BENCH")"
[[ "$TREE_ROOT" = /* ]] || TREE_ROOT="$PROJECT_ROOT/$TREE_ROOT"
TREE="$TREE_ROOT/T${T}-l1${L1_MIB}-k${K}-n${PRELOAD_KEYS}-s${PRELOAD_SEED}"
if [[ -f "$TREE/COMPLETED" ]]; then
  # A completed tree is reused only by the binary that built it (03 checks
  # the rest when a run opens it).
  "$PYTHON" "$PIPELINE_DIR/tree_identity.py" match "$TREE" \
    --dbbench-sha256 "$DBBENCH_SHA256" > /dev/null ||
    refuse "$TREE exists and does not fit this db_bench; move it away."
  echo "[tree] reusing $TREE"
  echo "TREE_DIR=$TREE"
  exit 0
fi
[[ ! -e "$TREE" ]] ||
  refuse "A partial tree exists: $TREE. Inspect or remove it first."

strays="$(ps -eo pid=,comm= 2>/dev/null | awk -v self="$$" \
  '$1 != self && $2 == "db_bench" { print $1 }')"
if [[ -n "$strays" && "${ALLOW_CONCURRENT_RUNS:-0}" != 1 ]]; then
  refuse "Another db_bench is running (pid $strays); stop it, or set" \
         "ALLOW_CONCURRENT_RUNS=1."
fi
# Open files -1 keeps every table open (D-29 §1; memory: tmux gave 1,024).
if [[ "$OPEN_FILES" == -1 ]]; then
  ulimit -n "$(ulimit -Hn)" 2>/dev/null || true
  limit="$(ulimit -n)"
  [[ "$limit" == unlimited ]] || (( limit >= 100000 )) ||
    refuse "The open-file limit is $limit; open files -1 needs at least 100,000."
fi
memory_scope=()
if [[ -n "$RUN_MEMORY_MAX" ]]; then
  export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  memory_scope=(systemd-run --user --scope -q -p MemoryMax="$RUN_MEMORY_MAX" --)
fi
launcher=()
[[ -z "$DBBENCH_CPUS" ]] || launcher=(taskset -c "$DBBENCH_CPUS")

# Workload flags are left out: they change no option of the tree, which every
# workload shares (D-27 §1).
dbbench_shared_flags
mkdir -p "$TREE"
command=(
  ${memory_scope[@]+"${memory_scope[@]}"}
  ${launcher[@]+"${launcher[@]}"}
  "$DB_BENCH"
  --benchmarks=rlsuspend,filluniquerandom,settle,levelstats,stats
  --rl_host_log="$TREE/host_log_load.jsonl"
  --rl_settle_hold_seconds="$SETTLE_HOLD_SECONDS"
  --num="$PRELOAD_KEYS"
  --max_bytes_for_level_multiplier="$T"
  --level0_file_num_compaction_trigger="$K"
  --level0_slowdown_writes_trigger="$L0_SLOWDOWN_TRIGGER"
  --level0_stop_writes_trigger="$L0_STOP_TRIGGER"
  --compaction_pri="$COMPACTION_PRIORITY"
  --compaction_style=0
  --use_existing_db=0
  --stats_interval_seconds=10
  --db="$TREE/db"
  --seed="$PRELOAD_SEED"
  "${DBBENCH_COMMON[@]}"
)
printf '%q ' "${command[@]}" > "$TREE/command.txt"
printf '\n' >> "$TREE/command.txt"

failed() {  # keep the evidence under a new name; never delete it
  local kept="$TREE.failed-$(date -u +%Y%m%dT%H%M%SZ)"
  mv "$TREE" "$kept"
  echo "[tree] $1; kept as $kept" >&2
  exit 1
}

echo "[tree] loading $PRELOAD_KEYS keys at T=$T, L1 ${L1_MIB} MiB, K=$K," \
     "seed $PRELOAD_SEED into $TREE"
start_ns="$(date +%s%N)"
status=0
"${command[@]}" > "$TREE/load.out" 2>&1 || status=$?
end_ns="$(date +%s%N)"
(( status == 0 )) || failed "db_bench exited $status (see load.out)"
grep -q '^RL_SETTLED ok=1' "$TREE/load.out" || failed "the load did not settle"
! grep -qiE '^(put|get|seek|open) error:|IO error:|Corruption:' "$TREE/load.out" ||
  failed "db_bench reported an error"
# The archive carries no info log: each open starts its own.
mv "$TREE/db/LOG" "$TREE/LOG.load" 2>/dev/null || true
rm -f "$TREE"/db/LOG.old.*
seconds="$(awk -v a="$start_ns" -v b="$end_ns" 'BEGIN { printf "%.3f", (b - a) / 1e9 }')"
"$PYTHON" "$PIPELINE_DIR/tree_identity.py" record "$TREE" \
  --keys "$PRELOAD_KEYS" --seed "$PRELOAD_SEED" \
  --dbbench-sha256 "$DBBENCH_SHA256" --seconds "$seconds" \
  --host-log "$TREE/host_log_load.jsonl" --load-out "$TREE/load.out" \
  --option size_ratio="$T" --option write_buffer_size="$WRITE_BUFFER_SIZE" \
  --option target_file_size_base="$TARGET_FILE_SIZE" \
  --option max_bytes_for_level_base="$MAX_BYTES_FOR_LEVEL_BASE" \
  --option num_levels="$NUM_LEVELS" \
  --option level0_file_num_compaction_trigger="$K" \
  --option level0_slowdown_writes_trigger="$L0_SLOWDOWN_TRIGGER" \
  --option level0_stop_writes_trigger="$L0_STOP_TRIGGER" \
  --option compaction_pri="$COMPACTION_PRIORITY" \
  --option key_size="$KEY_SIZE" --option value_size="$VALUE_SIZE" \
  --option block_size="$BLOCK_SIZE" --option bloom_bits="$BLOOM_BITS" \
  --option block_cache_size="$BLOCK_CACHE_SIZE" --option open_files="$OPEN_FILES" \
  --option max_background_jobs="$MAX_BACKGROUND_JOBS" \
  --option memory_max="${RUN_MEMORY_MAX:-none}" > "$TREE/identity.txt" ||
  failed "the tree's record could not be written"
touch "$TREE/COMPLETED"
echo "[tree] identity $(<"$TREE/identity.txt"), ${seconds} s"
echo "TREE_DIR=$TREE"
