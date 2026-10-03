#!/usr/bin/env bash
# OBJ-2 price calibration (Gate N0 item 7): one measurement session of
# PREREGISTRATION D-22 (c), on the node only, after the q-bar native arms
# (D-14 §2) and 29's archive. The arguments are the q-bar arms' 04
# summary.csv files; c_w comes from their own flush and compaction jobs.
#
# The read prices come from 29's 18 archived trees (T = 2, 3, 4, 6, 8 and 10,
# sets 1, 2 and 3), in set order. For each set its six trees are unpacked
# afresh into DB_ROOT and every file is checked against the manifest; each
# tree gets one unscored readrandom to warm the page cache; then three
# rounds, each visiting the six trees once in a rotated order (price_trees.py
# rounds), run readmissing, readrandom and seekrandom (seek_nexts 0) on each,
# one db_bench process each so its tickers are its own, all-open
# (open_files -1, D-20). After set 1's rounds, the c_open block runs D-20
# §2(b)'s procedure unchanged on set 1's trees at T = 2, 6 and 10: five
# repeats, every read benchmark in two arms, "capped" at the experiments'
# OPEN_FILES and "all_open", the arms' order alternating by repeat. After a
# set's last read, every SST of its trees must be unchanged and no other SST
# exist (D-22 i); its unpacked trees are then removed. Every command passes
# --ttl_seconds=0 --periodic_compaction_seconds=0 (D-22 a').
#
# 18_calibrate_prices.py session turns the runs into the session's record,
# $PRICE_SESSIONS_DIR/$PRICE_SESSION/session.json (schema 5, provisional).
# Two sessions on the same binary and archive, a reboot between them, give
# the final prices by "18_calibrate_prices.py compare A B" (D-22 f).
#
#   CONFIRM_PRICE_CALIBRATION=YES PRICE_TREES=<29's archive> \
#     PRICE_TREES_SHA256=<its identity, from the dated build record> \
#     PRICE_SESSION=A DB_ROOT=/mnt/nvme/prices-db \
#     scripts/dbbench_pipeline/18_calibrate_prices.sh <q-bar summary.csv>...
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

if [[ "${CONFIRM_PRICE_CALIBRATION:-}" != "YES" ]]; then
  echo "This measures one session of device prices on this machine (D-22 c)" \
       "into \$PRICE_SESSIONS_DIR/\$PRICE_SESSION." >&2
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
[[ "${PRICE_SESSION:-}" =~ ^[A-Za-z0-9_-]+$ ]] || {
  echo "PRICE_SESSION (letters, digits, _ and -) names this session (D-22 f)." >&2
  exit 2
}
[[ -n "${PRICE_TREES:-}" && -f "$PRICE_TREES/MANIFEST.sha256" &&
   -f "$PRICE_TREES/build_record.json" ]] || {
  echo "PRICE_TREES=${PRICE_TREES:-} is not 29's archive (MANIFEST.sha256," \
       "build_record.json)." >&2
  exit 2
}
PRICE_TREES="$(realpath "$PRICE_TREES")"
[[ "${PRICE_TREES_SHA256:-}" =~ ^[0-9a-f]{64}$ ]] || {
  echo "PRICE_TREES_SHA256 (the tree-set identity of the dated build record)" \
       "is not set (D-22 i)." >&2
  exit 2
}
if [[ "$PYTHON_VENV" = /* ]]; then
  PYTHON="$PYTHON_VENV/bin/python"
else
  PYTHON="$PROJECT_ROOT/$PYTHON_VENV/bin/python"
fi
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"
TREES_PY="$PIPELINE_DIR/price_trees.py"
# D-22 (i): the archive must be the one the build record names.
tree_set="$("$PYTHON" "$TREES_PY" identity --archive-dir "$PRICE_TREES")"
recorded="$("$PYTHON" -c 'import json, sys
print(json.load(open(sys.argv[1]))["tree_set_sha256"])' "$PRICE_TREES/build_record.json")"
if [[ "$tree_set" != "$PRICE_TREES_SHA256" || "$recorded" != "$PRICE_TREES_SHA256" ]]; then
  echo "the archive's tree-set identity $tree_set (build_record.json:" \
       "$recorded) is not PRICE_TREES_SHA256=$PRICE_TREES_SHA256 (D-22 i)." >&2
  exit 1
fi
WORK="$PRICE_SESSIONS_DIR/$PRICE_SESSION"
UNPACK="$DB_ROOT/price-trees/$PRICE_SESSION"
for path in "$WORK" "$UNPACK"; do
  if [[ -e "$path" ]]; then
    echo "$path exists; a session is never measured over another." >&2
    exit 2
  fi
done
DB_BENCH="$DBBENCH_BUILD_DIR/db_bench"
[[ -x "$DB_BENCH" ]] || { echo "Missing $DB_BENCH; run 01 and 02." >&2; exit 1; }
# The prices' db_bench_sha256: db_bench's identity as loaded (the executable
# plus its librocksdb), the value 03 records as each arm's dbbench_sha256.
DB_BENCH_SHA256="$("$PYTHON" "$PIPELINE_DIR/preflight_marker.py" identity --db-bench "$DB_BENCH")"
summaries=()
for summary in "$@"; do
  summaries+=(--write-summary "$summary")
done
# The write runs are checked first, so a wrong summary fails in seconds.
"$PYTHON" "$PIPELINE_DIR/18_calibrate_prices.py" check-writes \
  "${summaries[@]}" --db-bench-sha256 "$DB_BENCH_SHA256"
launcher=()
if [[ -n "$DBBENCH_CPUS" ]]; then
  command -v taskset >/dev/null || { echo "taskset is not installed." >&2; exit 1; }
  launcher=(taskset -c "$DBBENCH_CPUS")
fi
ulimit -n "$(ulimit -Hn)" 2>/dev/null || true
# The all-open runs hold every SST open: about 6,000 at T=2 (D-20).
if (( $(ulimit -n) < 16384 )); then
  echo "open-file limit $(ulimit -n) is under 16384; the all-open runs (D-20)" \
       "hold every SST open. Raise the hard limit (ulimit -Hn)." >&2
  exit 1
fi

# One set unpacked at a time: six trees of about 3.1 GB.
mkdir -p "$DB_ROOT"
db_free="$(df --output=avail -BG "$DB_ROOT" | tail -n 1 | tr -dc 0-9)"
(( db_free >= ${MIN_FREE_GB_SESSION:-25} )) || {
  echo "only ${db_free} GB free under DB_ROOT; one set needs about" \
       "${MIN_FREE_GB_SESSION:-25} GB (MIN_FREE_GB_SESSION)." >&2
  exit 1
}
# Every .tgz against the archive's own list before any run (D-22 b).
(cd "$PRICE_TREES" && sha256sum --quiet -c ARCHIVES.sha256) || {
  echo "an archive file differs from $PRICE_TREES/ARCHIVES.sha256 (D-22 b)" >&2
  exit 1
}
mkdir -p "$WORK" "$UNPACK"
"$PYTHON" "$TREES_PY" machine --cpus "$DBBENCH_CPUS" > "$WORK/machine.json"
printf '{"archive": "%s", "tree_set_sha256": "%s"}\n' "$PRICE_TREES" \
  "$tree_set" > "$WORK/archive.json"
dbbench_shared_flags
load_ops="$(programme1_load_operations)"
# D-20's two arms: the shared flags as the experiments run them, and the
# same with open_files -1, so every command names open_files once.
COMMON_CAPPED=("${DBBENCH_COMMON[@]}")
COMMON_ALL_OPEN=()
for flag in "${DBBENCH_COMMON[@]}"; do
  [[ "$flag" == --open_files=* ]] && flag=--open_files=-1
  COMMON_ALL_OPEN+=("$flag")
done
if [[ " ${COMMON_ALL_OPEN[*]} " != *" --open_files=-1 "* ]]; then
  echo "the shared db_bench flags name no --open_files; cannot build the" \
       "all-open arm (D-20)" >&2
  exit 1
fi
mapfile -t ROUND_ORDERS < <("$PYTHON" "$TREES_PY" rounds)
(( ${#ROUND_ORDERS[@]} == 3 )) || { echo "price_trees.py gave no three rounds" >&2; exit 1; }
ratios=(2 3 4 6 8 10)

run() {  # $1=set, $2=T, $3=arm (capped|all_open), $4=output directory, then flags
  local set="$1" ratio="$2" arm="$3" out="$4"
  shift 4
  local -n common="COMMON_${arm^^}"
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
    --ttl_seconds=0 --periodic_compaction_seconds=0
    --compaction_style=0 --db="$UNPACK/s$set/T$ratio" --seed="$DBBENCH_SEED"
    "${common[@]}" --use_existing_db=1 "$@"
  )
  printf '%q ' "${command[@]}" > "$out/command.txt"
  echo "${out#"$WORK"/}" >> "$WORK/runs.log"
  echo "[prices] ${out#"$WORK"/}"
  "${command[@]}" > "$out/stdout.txt" 2>&1 || {
    echo "[prices] failed; see $out/stdout.txt" >&2
    exit 1
  }
}
reads() {  # $1=benchmark
  echo --benchmarks="$1" --reads="$PRICE_READS" --seek_nexts=0
}

for set in 1 2 3; do
  echo "[prices] set $set: unpacking and checking against the manifest"
  for ratio in "${ratios[@]}"; do
    tar -xzf "$PRICE_TREES/s$set-T$ratio.tgz" -C "$UNPACK"
    "$PYTHON" "$TREES_PY" check --manifest "$PRICE_TREES/MANIFEST.sha256" \
      --root "$UNPACK" --tree "s$set/T$ratio" \
      --report "$WORK/s$set/T$ratio/unpacked.json"
  done
  # The unpacked bytes reach the disk before any timed read.
  sync
  for ratio in "${ratios[@]}"; do
    # shellcheck disable=SC2046
    run "$set" "$ratio" capped "$WORK/s$set/T$ratio/warmup" $(reads readrandom)
  done
  for round in 1 2 3; do
    for ratio in ${ROUND_ORDERS[round - 1]}; do
      for benchmark in readmissing readrandom seekrandom; do
        # shellcheck disable=SC2046
        run "$set" "$ratio" all_open "$WORK/s$set/round$round/T$ratio/$benchmark" \
          $(reads "$benchmark")
      done
    done
  done
  if (( set == 1 )); then
    for ratio in 2 6 10; do
      for repeat in 1 2 3 4 5; do
        # The arm order alternates by repeat, so slow drift cancels in c_open.
        if (( repeat % 2 )); then arms=(capped all_open); else arms=(all_open capped); fi
        for benchmark in readmissing readrandom seekrandom; do
          for arm in "${arms[@]}"; do
            # shellcheck disable=SC2046
            run "$set" "$ratio" "$arm" "$WORK/s$set/copen/T$ratio/r$repeat/$arm/$benchmark" \
              $(reads "$benchmark")
          done
        done
      done
    done
  fi
  # D-22 (i): no read changed a tree. The trees stay for inspection if one did.
  for ratio in "${ratios[@]}"; do
    "$PYTHON" "$TREES_PY" check --manifest "$PRICE_TREES/MANIFEST.sha256" \
      --root "$UNPACK" --tree "s$set/T$ratio" --after \
      --report "$WORK/s$set/T$ratio/after.json"
  done
  rm -rf "${UNPACK:?}/s$set"
done
rmdir "$UNPACK" 2>/dev/null || true

"$PYTHON" "$PIPELINE_DIR/18_calibrate_prices.py" session "$WORK" "${summaries[@]}" \
  --db-bench-sha256 "$DB_BENCH_SHA256" --tree-set-sha256 "$PRICE_TREES_SHA256" \
  --session "$PRICE_SESSION" --output "$WORK/session.json"
echo "[prices] session $PRICE_SESSION: $WORK/session.json (provisional until" \
     "18_calibrate_prices.py compare, D-22 f)"
