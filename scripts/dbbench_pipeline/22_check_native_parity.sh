#!/usr/bin/env bash
# ACT-4 (PATHWAYS Pathway A §6; plan §6.4 step 4): PARITY_PAIRS pairs of runs
# at 1M operations, T=2, of stock RocksDB (01c) and the patched db_bench with
# every level target multiplier set to 1, then 22_check_native_parity.py.
# Both arms run the same flags as 03 (dbbench_shared_flags), native leveled
# compaction, and the same upstream benchmark sequence; the explicit flush
# makes the fork's waitforcompaction (which flushes) and stock's (which does
# not) end on the same tree. Arm order alternates by pair. Exits with the
# evaluator's code: 0 passed, 1 failed, 2 undecided.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

if [[ "$PYTHON_VENV" = /* ]]; then
  PYTHON="$PYTHON_VENV/bin/python"
else
  PYTHON="$PROJECT_ROOT/$PYTHON_VENV/bin/python"
fi
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"
declare -A BINARY=(
  [stock]="$STOCK_BUILD_DIR/db_bench"
  [patched]="$DBBENCH_BUILD_DIR/db_bench"
)
for arm in stock patched; do
  [[ -x "${BINARY[$arm]}" ]] || {
    echo "Missing ${BINARY[$arm]}; run 01c (stock) and 01-02 (patched)." >&2
    exit 1
  }
done
launcher=()
if [[ -n "$DBBENCH_CPUS" ]]; then
  command -v taskset >/dev/null || { echo "taskset is not installed." >&2; exit 1; }
  launcher=(taskset -c "$DBBENCH_CPUS")
fi

# open_files=1000 needs more descriptors than the usual soft limit (as 03).
ulimit -n "$(ulimit -Hn)" 2>/dev/null || true

WORK="$PREFLIGHT_WORK_DIR/act4"
DB_DIR="$DB_ROOT/preflight-act4"
rm -rf "$WORK" "$DB_DIR"
mkdir -p "$WORK" "$DB_ROOT"

total_ops=1000000
ratio=2
load_ops=$(( total_ops * LOAD_PERCENT / 100 ))
mixed_ops=$(( total_ops - load_ops ))
ones="$(printf '1:%.0s' $(seq "$NUM_LEVELS"))"
ones="${ones%:}"
dbbench_shared_flags

for (( pair = 1; pair <= PARITY_PAIRS; pair++ )); do
  seed=$(( DBBENCH_SEED + pair - 1 ))
  order=(stock patched)
  (( pair % 2 )) || order=(patched stock)
  for arm in "${order[@]}"; do
    dir="$WORK/pair-$(printf '%02d' "$pair")/$arm"
    mkdir -p "$dir"
    extra=()
    [[ "$arm" == patched ]] && extra=(--level_target_multipliers="$ones")
    command=(
      ${launcher[@]+"${launcher[@]}"}
      "${BINARY[$arm]}"
      --benchmarks=filluniquerandom,mixgraph,flush,waitforcompaction,stats
      --num="$load_ops"
      --reads="$mixed_ops"
      "${DBBENCH_WORKLOAD[@]}"
      --max_bytes_for_level_multiplier="$ratio"
      --level0_file_num_compaction_trigger="$L0_COMPACTION_TRIGGER"
      --level0_slowdown_writes_trigger="$L0_SLOWDOWN_TRIGGER"
      --level0_stop_writes_trigger="$L0_STOP_TRIGGER"
      --compaction_pri="$COMPACTION_PRIORITY"
      --compaction_style=0
      --use_existing_db=0
      --db="$DB_DIR"
      --seed="$seed"
      "${DBBENCH_COMMON[@]}"
      ${extra[@]+"${extra[@]}"}
    )
    printf '%q ' "${command[@]}" > "$dir/command.txt"
    echo "[ACT-4] pair $pair: $arm"
    status=0
    "${command[@]}" > "$dir/stdout.txt" 2>&1 || status=$?
    cp "$DB_DIR/LOG" "$dir/rocksdb_LOG.txt" 2>/dev/null || true
    {
      printf 'arm=%s\npair=%s\nseed=%s\nexit_code=%s\n' "$arm" "$pair" "$seed" "$status"
      printf 'db_bench_sha256=%s\n' "$(sha256sum "${BINARY[$arm]}" | awk '{print $1}')"
    } > "$dir/metadata.env"
    rm -rf "$DB_DIR"
    (( status == 0 )) || {
      echo "[ACT-4] $arm run of pair $pair exited $status; see $dir/stdout.txt" >&2
      exit 1
    }
  done
done

"$PYTHON" "$PIPELINE_DIR/22_check_native_parity.py" "$WORK" \
  --stall-fraction-margin "$PARITY_STALL_FRACTION_MARGIN" \
  --output "$WORK/act4_report.json"
