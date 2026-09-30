#!/usr/bin/env bash
# OBJ-2 price calibration (Gate N0 item 7), on the node only: one settled
# tree at the pipeline's geometry, then readmissing, readrandom, seekrandom
# and a full compact, each in its own db_bench process so its tickers are its
# own, then 18_calibrate_prices.py. Writes PRICES_FILE, which 03 copies into
# every arm and records in the fingerprint. Re-run on any hardware change.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

if [[ "${CONFIRM_PRICE_CALIBRATION:-}" != "YES" ]]; then
  echo "This measures device prices on this machine and writes $PRICES_FILE." >&2
  echo "Set CONFIRM_PRICE_CALIBRATION=YES to start." >&2
  exit 2
fi
if [[ "$PYTHON_VENV" = /* ]]; then
  PYTHON="$PYTHON_VENV/bin/python"
else
  PYTHON="$PROJECT_ROOT/$PYTHON_VENV/bin/python"
fi
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"
DB_BENCH="$DBBENCH_BUILD_DIR/db_bench"
[[ -x "$DB_BENCH" ]] || { echo "Missing $DB_BENCH; run 01 and 02." >&2; exit 1; }
launcher=()
if [[ -n "$DBBENCH_CPUS" ]]; then
  command -v taskset >/dev/null || { echo "taskset is not installed." >&2; exit 1; }
  launcher=(taskset -c "$DBBENCH_CPUS")
fi
ulimit -n "$(ulimit -Hn)" 2>/dev/null || true

WORK="$PREFLIGHT_WORK_DIR/prices"
DB_DIR="$DB_ROOT/prices"
rm -rf "$WORK" "$DB_DIR"
mkdir -p "$WORK" "$DB_ROOT"
dbbench_shared_flags

run() {  # $1=name, then db_bench flags
  local name="$1"
  shift
  mkdir -p "$WORK/$name"
  local command=(
    ${launcher[@]+"${launcher[@]}"} "$DB_BENCH"
    --num="$PRICE_KEYS"
    --max_bytes_for_level_multiplier="$PRICE_SIZE_RATIO"
    --level0_file_num_compaction_trigger="$L0_COMPACTION_TRIGGER"
    --level0_slowdown_writes_trigger="$L0_SLOWDOWN_TRIGGER"
    --level0_stop_writes_trigger="$L0_STOP_TRIGGER"
    --compaction_pri="$COMPACTION_PRIORITY"
    --compaction_style=0 --db="$DB_DIR" --seed="$DBBENCH_SEED"
    "${DBBENCH_COMMON[@]}" "$@"
  )
  printf '%q ' "${command[@]}" > "$WORK/$name/command.txt"
  echo "[prices] $name"
  "${command[@]}" > "$WORK/$name/stdout.txt" 2>&1 || {
    echo "[prices] $name failed; see $WORK/$name/stdout.txt" >&2
    exit 1
  }
}

run load --benchmarks=filluniquerandom,flush,waitforcompaction --use_existing_db=0
# Unscored: warms the page cache, so readmissing (measured first) does not
# pay the cold reads alone and leave t_blk's subtraction negative.
run warmup --benchmarks=readrandom --reads="$PRICE_READS" --use_existing_db=1
for benchmark in readmissing readrandom seekrandom; do
  run "$benchmark" --benchmarks="$benchmark" --reads="$PRICE_READS" \
    --seek_nexts=0 --use_existing_db=1
done
run compact --benchmarks=compact --use_existing_db=1
rm -rf "$DB_DIR"

"$PYTHON" "$PIPELINE_DIR/18_calibrate_prices.py" "$WORK" \
  --db-bench-sha256 "$(sha256sum "$DB_BENCH" | awk '{print $1}')" \
  --output "$PRICES_FILE"
echo "[prices] wrote $PRICES_FILE"
