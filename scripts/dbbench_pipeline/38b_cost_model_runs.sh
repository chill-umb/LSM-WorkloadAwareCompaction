#!/usr/bin/env bash
# Stage 38b: cost model 2's one-session calibration runs, for the
# exploratory track (PREREGISTRATION D-24 §2: "kappa, the scan-step prices,
# c_mt, c_put and the fixed parts come from one-session diagnostics"; designs
# D-23 §4 and D-24 §1 items 1, 10, 11). Not a governed run: its prices are
# provisional. The operator's ~/node_ops/diag/calib_v2.sh of 2026-10-05/06
# (built on contention_reads.sh, 2026-10-03), moved into the pipeline on
# 2026-11-08 so that stage 38 runs it on every machine (owner: every price
# refitted per machine); the runs and their order are unchanged. Needs the
# interim binary (host log schema 3, the scan set-up timer): the neighbour's
# host log times its jobs, flushes included.
#
# Trees: two masters at T = 10 (Assoc's load), each loaded as stage 29 builds
# a price tree, then overwritten (CALIB_OVERWRITES keys, uniform) so that it
# holds garbage, then settled: b4k (data blocks of the shared flags' size)
# and b16k (--block_size=16384), so that c_st and c_ib are told apart.
# Every reader runs with every table open (stage 18's all-open arm), one
# thread, CALIB_DURATION seconds, on a fresh copy for anything that writes.
#
# Quiet arms (per repeat; perf level 1 unless said):
#   hit, miss       readrandom, key_size and key_size + 1 (misses reach the
#                   filters), on b4k; again at perf level 4 (memtable timer)
#   memtable        overwrite of CALIB_MEMTABLE_KEYS into a 1 GiB memtable, no
#                   flush, then readrandom, perf level 4: the repo fit's c_mt
#                   (its timer, on a memtable far fuller than the runs')
#   mtempty, mtfill D-23 §3(d)'s c_mt (added 2026-10-05): readrandom on b4k
#                   at the shared (experiments') write buffer, the memtable
#                   empty (mtempty) or holding CALIB_MTFILL_KEYS entries
#                   (mtfill: overwrite, no flush), perf level 1; mtfill again
#                   at perf level 4 (the timer cross-check). memstats prints
#                   the memtable's entries before the reads. With
#                   CALIB_MT_WRITE_BUFFER (bytes; stage 38 sets the suite's
#                   16 MiB) both run at that write buffer, so the memtable a
#                   Get searches is as large as the experiments' runs'.
#   put            fillrandom on an empty DB, compactions off, a large write
#                   buffer, rate CALIB_PUT_BPS: c_put (rocksdb.db.write.micros)
#   seek<n>         seekrandom --seek_nexts=n, n in CALIB_NEXTS, on b4k and
#                   b16k: c_st, c_ib, c0_sc (the scan set-up timer)
# Loaded arms: the reader kinds hit, miss, seek0, seek<last>, put on b4k,
# each beside a neighbour db_bench overwriting its own copy of b4k on the
# other cores, with its own host log, started CALIB_LEAD seconds before:
#   nb<r>     at r MB/s, r in CALIB_RATES (the writer-rate sweep, linearity)
#   nbbig     at CALIB_MID MB/s with write buffer and target files x4 (the
#             job-size sweep at a fixed byte rate: per byte or per job)
#   nb2j      at CALIB_HIGH MB/s with two compactions at once (additivity)
#   nbgarb    at CALIB_MID MB/s on CALIB_GARBAGE_KEYS keys only: merges that
#             read far more than they write (lambda; c_cr, D-24 item 11)
#   nbflush   at CALIB_MID MB/s, compactions off, a 256 MiB write buffer: the
#             flushes alone
#
# Afterwards (printed at the end):
#   calibration_v2.py rows --runs OUT
#   calibration_v2.py foreground --runs OUT --base <prices.json> --out OUT/cal_fg.json
#   calibration_v2.py jobs --run <reference arms> --neighbour-log OUT/.../nbgarb.../neighbour_host_log.jsonl --out OUT/cal_jobs.json
#   30_provisional_prices_v2.py --base <prices.json> --calibration OUT/cal_jobs.json --calibration OUT/cal_fg.json [--placeholders] --out <file>
#
# Usage (nothing in the repository may change while it runs):
#   OUT=<dir> DBS=<dir> scripts/dbbench_pipeline/38b_cost_model_runs.sh 2>&1 | tee <log>
#   CALIB_DRY_RUN=1 scripts/dbbench_pipeline/38b_cost_model_runs.sh   # print the commands only
# Knobs (prefixed, see memory diag-config-sh-name-clash): CALIB_REPEATS (2),
# CALIB_DURATION (20), CALIB_LEAD (10), CALIB_RATES ("4 8 16 32"), CALIB_MID
# (16), CALIB_HIGH (32), CALIB_NEXTS ("0 16 64"), CALIB_OVERWRITES
# (load/3), CALIB_GARBAGE_KEYS (load/50), CALIB_MEMTABLE_KEYS (200000),
# CALIB_MTFILL_KEYS (960), CALIB_MT_WRITE_BUFFER (empty: the shared write
# buffer), CALIB_PUT_BPS (20 MB/s), CALIB_READER_CPUS (0-3),
# CALIB_NEIGHBOUR_CPUS (4-7), OUT, DBS. The last line is CALIB_RUNS_EXIT=0.

set -Eeuo pipefail
PIPE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$PIPE/../.." && pwd)"
PROJECT_ROOT="$ROOT"
cd "$ROOT"
# shellcheck source=/dev/null
source "$PIPE/config.sh"

START="$(date -u +%F-%H%M)"
OUT="${OUT:-$HOME/node_ops/calib_v2/$START}"
DBS="${DBS:-$ROOT/db/calib-v2-dbs}"
DRY="${CALIB_DRY_RUN:-0}"
CALIB_REPEATS="${CALIB_REPEATS:-2}"
CALIB_DURATION="${CALIB_DURATION:-20}"
CALIB_LEAD="${CALIB_LEAD:-10}"
read -r -a RATES <<< "${CALIB_RATES:-4 8 16 32}"
CALIB_MID="${CALIB_MID:-16}"
CALIB_HIGH="${CALIB_HIGH:-32}"
read -r -a NEXTS <<< "${CALIB_NEXTS:-0 16 64}"
CALIB_MEMTABLE_KEYS="${CALIB_MEMTABLE_KEYS:-200000}"
# Half of the 1,921 entries a 2 MiB memtable holds when it flushes (median
# keys written between flushes, C3's native arms, Assoc T=10, 2026-10-05): a
# memtable fills at a steady rate, so a read finds it half full on average.
CALIB_MTFILL_KEYS="${CALIB_MTFILL_KEYS:-960}"
CALIB_MT_WRITE_BUFFER="${CALIB_MT_WRITE_BUFFER:-}"
CALIB_PUT_BPS="${CALIB_PUT_BPS:-20000000}"
CALIB_READER_CPUS="${CALIB_READER_CPUS:-0-3}"
CALIB_NEIGHBOUR_CPUS="${CALIB_NEIGHBOUR_CPUS:-4-7}"
MAX_FAILURES="${MAX_FAILURES:-3}"
CALIB_ONLY="${CALIB_ONLY:-}"
MASTERS="$DBS/masters"
WORK="$DBS/work"
WORK2="$DBS/work-neighbour"
MB=1000000

stamp() { echo "=== $(date -u '+%F %T') UTC $*"; }
fail() { echo "[calib_v2] $*" >&2; exit 1; }
run() {  # $1 = file for the command; the rest is the command
  local file="$1"; shift
  printf '%q ' "$@" > "$file"
  if [[ "$DRY" == 1 ]]; then
    echo "DRY: $(cat "$file")"
    return 0
  fi
  "$@"
}

if [[ "$PYTHON_VENV" = /* ]]; then PYTHON="$PYTHON_VENV/bin/python"
else PYTHON="$ROOT/$PYTHON_VENV/bin/python"; fi
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"
if [[ "$DBBENCH_BUILD_DIR" = /* ]]; then DB_BENCH="$DBBENCH_BUILD_DIR/db_bench"
else DB_BENCH="$ROOT/$DBBENCH_BUILD_DIR/db_bench"; fi

for n in CALIB_REPEATS CALIB_DURATION CALIB_LEAD CALIB_MID CALIB_HIGH CALIB_MEMTABLE_KEYS CALIB_MTFILL_KEYS CALIB_PUT_BPS; do
  [[ "${!n}" =~ ^[0-9]+$ ]] || fail "$n=${!n} is not a whole number"
done
[[ -z "$CALIB_MT_WRITE_BUFFER" || "$CALIB_MT_WRITE_BUFFER" =~ ^[1-9][0-9]*$ ]] ||
  fail "CALIB_MT_WRITE_BUFFER=$CALIB_MT_WRITE_BUFFER is not a byte count"
MT_BUFFER=()
[[ -z "$CALIB_MT_WRITE_BUFFER" ]] || MT_BUFFER=(--write_buffer_size="$CALIB_MT_WRITE_BUFFER")
[[ "$THREADS" == 1 ]] || fail "THREADS=$THREADS; the prices are one thread's time (D-15 §3a)"
[[ -x "$DB_BENCH" ]] || fail "missing $DB_BENCH"
if [[ "$DRY" != 1 ]]; then
  db_bench_help="$("$DB_BENCH" --help 2>&1 || true)"
  grep -q rl_host_log_stride <<<"$db_bench_help" ||
    fail "$DB_BENCH is not the interim binary (no --rl_host_log_stride)"
  [[ -e "$OUT" ]] && fail "$OUT exists: move it away first"
  pgrep -x db_bench >/dev/null && fail "a db_bench is already running"
  mkdir -p "$DBS"
  free_gb="$(df --output=avail -BG "$DBS" | tail -n 1 | tr -dc 0-9)"
  (( free_gb >= 40 )) || fail "only ${free_gb} GB free under $DBS; about 20 GB are used"
  ulimit -n "$(ulimit -Hn)" 2>/dev/null || true
  (( $(ulimit -n) >= 16384 )) || fail "open-file limit $(ulimit -n) is under 16384"
fi
command -v taskset >/dev/null || fail "taskset is not installed"
reader=(taskset -c "$CALIB_READER_CPUS")
neighbour=(taskset -c "$CALIB_NEIGHBOUR_CPUS")

dbbench_shared_flags
load_ops="${LOAD_OPS:-$(programme1_load_operations)}"
CALIB_OVERWRITES="${CALIB_OVERWRITES:-$((load_ops / 3))}"
CALIB_GARBAGE_KEYS="${CALIB_GARBAGE_KEYS:-$((load_ops / 50))}"
for want in --open_files= --key_size= --perf_level= --block_size=; do
  [[ " ${DBBENCH_COMMON[*]} " == *" $want"* ]] || fail "the shared flags name no $want"
done
# The tree's data block size (set by geometry). Fixed 2026-10-05: it used to
# be appended to GEOMETRY, before the shared --block_size, and gflags keep the
# last value, so the b16k tree had 4 KiB blocks. flags() now substitutes it.
TREE_BLOCK="$BLOCK_SIZE"
COMMON=()
for flag in "${DBBENCH_COMMON[@]}"; do
  [[ "$flag" == --open_files=* ]] && flag=--open_files=-1
  COMMON+=("$flag")
done
flags() {  # $1 = key size, $2 = perf level: fills FLAGS
  FLAGS=()
  local flag
  for flag in "${COMMON[@]}"; do
    case "$flag" in
      --key_size=*) flag="--key_size=$1" ;;
      --perf_level=*) flag="--perf_level=$2" ;;
      --block_size=*) flag="--block_size=$TREE_BLOCK" ;;
    esac
    FLAGS+=("$flag")
  done
}
geometry() {  # $1 = tree: the price-tree geometry every command shares
  GEOMETRY=(--num="$load_ops" --max_bytes_for_level_multiplier=10
    --level0_file_num_compaction_trigger="$L0_COMPACTION_TRIGGER"
    --level0_slowdown_writes_trigger="$L0_SLOWDOWN_TRIGGER"
    --level0_stop_writes_trigger="$L0_STOP_TRIGGER"
    --compaction_pri="$COMPACTION_PRIORITY"
    --rl_settle_hold_seconds="$SETTLE_HOLD_SECONDS"
    --ttl_seconds=0 --periodic_compaction_seconds=0
    --compaction_style=0 --seed="$DBBENCH_SEED")
  TREE_BLOCK="$BLOCK_SIZE"
  [[ "$1" == b16k ]] && TREE_BLOCK=16384
  return 0
}

mkdir -p "$OUT"
{
  echo "started_utc=$(date -u '+%F %T')"
  echo "db_bench_identity=$("$PYTHON" "$PIPE/preflight_marker.py" identity --db-bench "$DB_BENCH" 2>/dev/null || echo unknown)"
  echo "root_head=$(git -C "$ROOT" rev-parse HEAD)"
  echo "fork_head=$(git -C "$ROOT/lib/rocksdb" rev-parse HEAD)"
  echo "load_ops=$load_ops overwrites=$CALIB_OVERWRITES garbage_keys=$CALIB_GARBAGE_KEYS"
  echo "repeats=$CALIB_REPEATS duration_s=$CALIB_DURATION lead_s=$CALIB_LEAD"
  echo "mtfill_keys=$CALIB_MTFILL_KEYS mt_write_buffer=${CALIB_MT_WRITE_BUFFER:-shared}"
  echo "rates_mb_s=${RATES[*]} mid=$CALIB_MID high=$CALIB_HIGH nexts=${NEXTS[*]}"
  echo "reader_cpus=$CALIB_READER_CPUS neighbour_cpus=$CALIB_NEIGHBOUR_CPUS"
} > "$OUT/setup.env"

# 1. The two masters: load and settle (as stage 29), then overwrite and settle.
for tree in b4k b16k; do
  [[ -f "$MASTERS/$tree.BUILT" ]] && { stamp "tree $tree: reusing"; continue; }
  geometry "$tree"
  flags "$KEY_SIZE" 1
  out="$OUT/build/$tree"; mkdir -p "$out"
  [[ "$DRY" == 1 ]] || { rm -rf "${MASTERS:?}/$tree"; mkdir -p "$MASTERS"; }
  stamp "tree $tree: load"
  run "$out/load.command.txt" "${reader[@]}" "$DB_BENCH" \
    --benchmarks=filluniquerandom,settle,levelstats --use_existing_db=0 \
    "${GEOMETRY[@]}" --db="$MASTERS/$tree" "${FLAGS[@]}" > "$out/load.txt" 2>&1
  stamp "tree $tree: overwrite $CALIB_OVERWRITES keys"
  run "$out/overwrite.command.txt" "${reader[@]}" "$DB_BENCH" \
    --benchmarks=overwrite,settle,levelstats --use_existing_db=1 \
    --writes="$CALIB_OVERWRITES" "${GEOMETRY[@]}" --db="$MASTERS/$tree" \
    "${FLAGS[@]}" > "$out/overwrite.txt" 2>&1
  if [[ "$DRY" != 1 ]]; then
    grep -q '^RL_SETTLED ok=1' "$out/overwrite.txt" ||
      fail "tree $tree did not settle after its overwrites; see $out"
    cp "$out/overwrite.txt" "$MASTERS/$tree.BUILT"
  fi
done
[[ "$DRY" == 1 ]] || sync

# 2. The runs.
failures=0
started=0
skipped=0
neighbour_flags() {  # $1 = arm: fills NB with the neighbour's own flags
  local rate
  NB=(--benchmarks=overwrite --use_existing_db=1)
  # The rate arms are nb followed by digits only (nb2j is not a rate).
  if [[ "$1" =~ ^nb([0-9]+)$ ]]; then
    NB+=(--benchmark_write_rate_limit=$((BASH_REMATCH[1] * MB)))
    return 0
  fi
  case "$1" in
    nbbig) rate="$CALIB_MID"
      NB+=(--write_buffer_size=$((4 * WRITE_BUFFER_SIZE)) --target_file_size_base=$((4 * TARGET_FILE_SIZE))) ;;
    nb2j) rate="$CALIB_HIGH"; NB+=(--max_background_compactions=2) ;;
    nbgarb) rate="$CALIB_MID"; NB+=(--num="$CALIB_GARBAGE_KEYS") ;;
    nbflush) rate="$CALIB_MID"; NB+=(--disable_auto_compactions=1 --write_buffer_size=$((256 << 20))) ;;
    *) fail "unknown neighbour arm $1" ;;
  esac
  NB+=(--benchmark_write_rate_limit=$((rate * MB)))
}

run_one() {  # $1 = label, $2 = arm, $3 = kind, $4 = tree, $5 = nexts, $6 = perf level
  local label="$1" arm="$2" kind="$3" tree="$4" nexts="$5" perf="$6"
  # CALIB_ONLY (a bash regex on the label) re-runs a subset (2026-10-05).
  if [[ -n "$CALIB_ONLY" && ! "$label" =~ $CALIB_ONLY ]]; then
    skipped=$((skipped + 1))
    return 0
  fi
  started=$((started + 1))
  local out="$OUT/runs/$label" key="$KEY_SIZE" bench status=0 nb_pid="" db
  mkdir -p "$out"
  [[ "$kind" == miss ]] && key=$((KEY_SIZE + 1))
  geometry "$tree"
  flags "$key" "$perf"
  local measured=("${reader[@]}" "$DB_BENCH" "${GEOMETRY[@]}" "${FLAGS[@]}")
  db="$WORK"
  case "$kind" in
    hit|miss) measured+=(--use_existing_db=1 --benchmarks=readrandom
                         --reads=1000000000 --duration="$CALIB_DURATION") ;;
    seek) measured+=(--use_existing_db=1 --benchmarks=seekrandom
                     --seek_nexts="$nexts" --reads=1000000000
                     --duration="$CALIB_DURATION") ;;
    # overwrite, not fillrandom: db_bench skips fill benchmarks under
    # --use_existing_db=1, which left the memtable empty (fixed 2026-10-05).
    memtable) measured+=(--use_existing_db=1 --benchmarks=overwrite,readrandom
                         --writes="$CALIB_MEMTABLE_KEYS" --reads=2000000
                         --disable_auto_compactions=1
                         --write_buffer_size=$((1 << 30))) ;;
    # The shared write buffer stays: CALIB_MTFILL_KEYS fit in it unflushed.
    # MT_BUFFER comes after the shared flags, so its write buffer wins.
    mtempty) measured+=(--use_existing_db=1 --benchmarks=memstats,readrandom
                        --reads=2000000 --disable_auto_compactions=1
                        ${MT_BUFFER[@]+"${MT_BUFFER[@]}"}) ;;
    mtfill) measured+=(--use_existing_db=1 --benchmarks=overwrite,memstats,readrandom
                       --writes="$CALIB_MTFILL_KEYS" --reads=2000000
                       --disable_auto_compactions=1
                       ${MT_BUFFER[@]+"${MT_BUFFER[@]}"}) ;;
    put) db="$DBS/put"
         measured+=(--use_existing_db=0 --benchmarks=fillrandom
                    --disable_auto_compactions=1 --write_buffer_size=$((1 << 30))
                    --max_write_buffer_number=4 --writes=1000000000
                    --duration="$CALIB_DURATION"
                    --benchmark_write_rate_limit="$CALIB_PUT_BPS") ;;
  esac
  measured+=(--db="$db")
  if [[ "$DRY" != 1 ]]; then
    rm -rf "${WORK:?}" "${WORK2:?}" "${DBS:?}/put"
    [[ "$kind" == put ]] || cp -a "$MASTERS/$tree" "$WORK"
    [[ "$arm" == quiet ]] || cp -a "$MASTERS/b4k" "$WORK2"
    sync
  fi
  {
    echo "label=$label"; echo "arm=$arm"; echo "kind=$kind"; echo "tree=$tree"
    echo "nexts=$nexts"; echo "perf_level=$perf"
    [[ "$arm" == quiet ]] || echo "neighbour_host_log=$out/neighbour_host_log.jsonl"
  } > "$out/run.env"
  if [[ "$arm" != quiet ]]; then
    geometry b4k
    flags "$KEY_SIZE" 1
    neighbour_flags "$arm"
    local nb=("${neighbour[@]}" "$DB_BENCH" "${GEOMETRY[@]}" "${FLAGS[@]}" "${NB[@]}"
      --db="$WORK2" --duration=$((CALIB_DURATION + CALIB_LEAD + 30))
      --rl_host_log="$out/neighbour_host_log.jsonl" --rl_host_log_stride=0)
    if [[ "$DRY" == 1 ]]; then
      run "$out/neighbour.command.txt" "${nb[@]}"
    else
      printf '%q ' "${nb[@]}" > "$out/neighbour.command.txt"
      "${nb[@]}" > "$out/neighbour.txt" 2>&1 &
      nb_pid=$!
      sleep "$CALIB_LEAD"
      kill -0 "$nb_pid" 2>/dev/null || { wait "$nb_pid" || true; status=97; }
    fi
  fi
  if (( status == 0 )); then
    run "$out/command.txt" "${measured[@]}" > "$out/stdout.txt" 2>&1 || status=$?
    echo "end_wall_us=$(( $(date +%s%N) / 1000 ))" >> "$out/run.env"
    if [[ -n "$nb_pid" ]] && ! kill -0 "$nb_pid" 2>/dev/null; then
      echo "[calib_v2] $label: the neighbour ended before the reader" >&2
      status=98
    fi
  fi
  if [[ -n "$nb_pid" ]]; then
    kill -INT "$nb_pid" 2>/dev/null || true
    wait "$nb_pid" 2>/dev/null || true
  fi
  if (( status != 0 )); then
    failures=$((failures + 1))
    echo "[calib_v2] FAILED $label (exit $status); see $out" >&2
    (( failures < MAX_FAILURES )) || fail "$failures runs failed; stopping"
    return 0
  fi
  touch "$out/COMPLETED"
  echo "[calib_v2] $label done"
}

last_nexts="${NEXTS[${#NEXTS[@]}-1]}"
arms=(quiet)
for r in "${RATES[@]}"; do arms+=("nb$r"); done
arms+=(nbbig nb2j nbgarb nbflush)
for repeat in $(seq 1 "$CALIB_REPEATS"); do
  stamp "repeat $repeat"
  # Quiet first, then the loaded arms, the arm order rotated by repeat.
  run_one "r$repeat-quiet-hit" quiet hit b4k 0 1
  run_one "r$repeat-quiet-miss" quiet miss b4k 0 1
  run_one "r$repeat-quiet-hit-timer" quiet hit b4k 0 4
  run_one "r$repeat-quiet-miss-timer" quiet miss b4k 0 4
  run_one "r$repeat-quiet-memtable" quiet memtable b4k 0 4
  run_one "r$repeat-quiet-mtempty" quiet mtempty b4k 0 1
  run_one "r$repeat-quiet-mtfill" quiet mtfill b4k 0 1
  run_one "r$repeat-quiet-mtfill-timer" quiet mtfill b4k 0 4
  run_one "r$repeat-quiet-put" quiet put b4k 0 1
  for tree in b4k b16k; do
    for n in "${NEXTS[@]}"; do
      run_one "r$repeat-quiet-seek$n-$tree" quiet seek "$tree" "$n" 1
    done
  done
  loaded=("${arms[@]:1}")
  shift_by=$(( (repeat - 1) % ${#loaded[@]} ))
  loaded=("${loaded[@]:shift_by}" "${loaded[@]:0:shift_by}")
  for arm in "${loaded[@]}"; do
    run_one "r$repeat-$arm-hit" "$arm" hit b4k 0 1
    run_one "r$repeat-$arm-miss" "$arm" miss b4k 0 1
    run_one "r$repeat-$arm-seek0" "$arm" seek b4k 0 1
    run_one "r$repeat-$arm-seek$last_nexts" "$arm" seek b4k "$last_nexts" 1
    run_one "r$repeat-$arm-put" "$arm" put b4k 0 1
  done
done
expected=$(( CALIB_REPEATS * (15 + 5 * (${#arms[@]} - 1)) ))
(( started + skipped == expected )) ||
  fail "started $started runs of $expected: the matrix stopped early"
stamp "done; $started runs, $skipped skipped (CALIB_ONLY), $failures failed"
cat <<EOF
Next (exploratory track; prices provisional):
  $PYTHON $PIPE/calibration_v2.py rows --runs $OUT/runs
  $PYTHON $PIPE/calibration_v2.py foreground --runs $OUT/runs --base $PRICES_FILE --out $OUT/cal_fg.json
  $PYTHON $PIPE/calibration_v2.py jobs --run <reference arm dirs> \\
      $(printf -- '--neighbour-log %s ' "$OUT"/runs/r*-nbgarb-*/neighbour_host_log.jsonl) --out $OUT/cal_jobs.json
  $PYTHON $PIPE/30_provisional_prices_v2.py --base $PRICES_FILE --calibration $OUT/cal_jobs.json --calibration $OUT/cal_fg.json --out <prices.provisional.v2.json>
EOF
echo "CALIB_RUNS_EXIT=0"
