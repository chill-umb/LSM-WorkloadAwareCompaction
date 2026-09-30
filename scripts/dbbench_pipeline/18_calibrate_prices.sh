#!/usr/bin/env bash
# OBJ-2 price calibration (Gate N0 item 7) by PREREGISTRATION D-15 §3, on the
# node only, after the q-bar native arms (D-14 §2): their 04 summary.csv
# files are the arguments, and c_w comes from their own flush and compaction
# jobs. The read prices come from three trees at T = 2, 6 and 10, each loaded
# as the experiments load (filluniquerandom of D-16's load, then the settle
# step). On each, one unscored readrandom warms the page cache,
# then five interleaved repeats of readmissing, readrandom and seekrandom,
# one db_bench process each so its tickers are its own. 18_calibrate_prices.py
# turns them into PRICES_FILE, which 03 copies into every later arm and
# records in the fingerprint. Re-run on any hardware or binary change.
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
# D-15 §3a: every priced read runs on one client thread.
if [[ "$THREADS" != 1 ]]; then
  echo "THREADS=$THREADS; the read prices are one thread's time (D-15 §3a)." >&2
  exit 2
fi
if (( $# == 0 )); then
  echo "usage: $0 <q-bar summary.csv> [...]  (D-14 §2's native arms at T=10)" >&2
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
DB_BENCH_SHA256="$(sha256sum "$DB_BENCH" | awk '{print $1}')"
summaries=()
for summary in "$@"; do
  summaries+=(--write-summary "$summary")
done
# The write runs are checked first, so a wrong summary fails in seconds.
"$PYTHON" "$PIPELINE_DIR/18_calibrate_prices.py" --check-writes \
  "${summaries[@]}" --db-bench-sha256 "$DB_BENCH_SHA256"
launcher=()
if [[ -n "$DBBENCH_CPUS" ]]; then
  command -v taskset >/dev/null || { echo "taskset is not installed." >&2; exit 1; }
  launcher=(taskset -c "$DBBENCH_CPUS")
fi
ulimit -n "$(ulimit -Hn)" 2>/dev/null || true

WORK="$PREFLIGHT_WORK_DIR/prices"
rm -rf "$WORK" "$DB_ROOT/prices"
mkdir -p "$WORK" "$DB_ROOT/prices"
dbbench_shared_flags
load_ops="$(programme1_load_operations)"

run() {  # $1=T, $2=output directory, then db_bench flags
  local ratio="$1" out="$2"
  shift 2
  mkdir -p "$out"
  local command=(
    ${launcher[@]+"${launcher[@]}"} "$DB_BENCH"
    --num="$load_ops"
    --max_bytes_for_level_multiplier="$ratio"
    --level0_file_num_compaction_trigger="$L0_COMPACTION_TRIGGER"
    --level0_slowdown_writes_trigger="$L0_SLOWDOWN_TRIGGER"
    --level0_stop_writes_trigger="$L0_STOP_TRIGGER"
    --compaction_pri="$COMPACTION_PRIORITY"
    --rl_settle_hold_seconds="$SETTLE_HOLD_SECONDS"
    --compaction_style=0 --db="$DB_ROOT/prices/T$ratio" --seed="$DBBENCH_SEED"
    "${DBBENCH_COMMON[@]}" "$@"
  )
  printf '%q ' "${command[@]}" > "$out/command.txt"
  echo "[prices] ${out#"$WORK"/}"
  "${command[@]}" > "$out/stdout.txt" 2>&1 || {
    echo "[prices] failed; see $out/stdout.txt" >&2
    exit 1
  }
}

for ratio in 2 6 10; do
  run "$ratio" "$WORK/T$ratio/load" \
    --benchmarks=filluniquerandom,settle --use_existing_db=0
  run "$ratio" "$WORK/T$ratio/warmup" \
    --benchmarks=readrandom --reads="$PRICE_READS" --use_existing_db=1
  for repeat in 1 2 3 4 5; do
    for benchmark in readmissing readrandom seekrandom; do
      run "$ratio" "$WORK/T$ratio/r$repeat/$benchmark" \
        --benchmarks="$benchmark" --reads="$PRICE_READS" --seek_nexts=0 \
        --use_existing_db=1
    done
  done
  rm -rf "$DB_ROOT/prices/T$ratio"
done

"$PYTHON" "$PIPELINE_DIR/18_calibrate_prices.py" "$WORK" "${summaries[@]}" \
  --db-bench-sha256 "$DB_BENCH_SHA256" --output "$PRICES_FILE"
echo "[prices] wrote $PRICES_FILE"
