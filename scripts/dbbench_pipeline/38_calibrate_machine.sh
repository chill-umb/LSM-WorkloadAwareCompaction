#!/usr/bin/env bash
# Stage 38: one machine's calibration (owner, 2026-11-08): q-bar and every
# price that changes from machine to machine, measured once on the machine
# before its experiments, every price refitted (exploratory track: the prices
# are provisional, D-24 §2; q-bar is a stand-in while the contract's is
# open, D-29 §6). After the preflight (13), with nothing else running.
#
# Once in the machine's life (owner, 2026-11-08, second ruling): the results
# go to MACHINE_CAL_DIR (config.sh) and, when every part is done, are sealed
# there to this machine (38_machine_calibration.py seal: host, machine id and
# the drive under NVME, every output's sha256). Stage 36 then prices every
# run with them. Run again on a sealed machine, 38 says so and exits 0 having
# done nothing; a calibration sealed on another machine (a copied home) is
# refused. An unfinished calibration resumes: finished parts are skipped.
# The SSD is trimmed before every db_bench run here and in 03 and 38b.
#
# Parts, in order (CAL_PARTS names a subset; a finished part is skipped):
#   natives  per workload one native run (36: the machine's T at CAL_SETTING,
#            CAL_OPS million operations, by default the suite's full 40M run,
#            as the experiments run it): q-bar is its measured-phase
#            throughput (04), and its jobs are the job fit's reference runs
#   reads    D-15 §3's read prices (c_f, c_blk, c_sk) as stage 18 measures
#            them, cut to one set: six price trees built as 29 builds them
#            (T 2, 3, 4, 6, 8, 10), one unscored readrandom each, then
#            CAL_READ_ROUNDS rounds in price_trees.ROUNDS' orders of
#            readmissing, readrandom and seekrandom, all tables open
#   cost     38b (the October recipe, CAL_COST_REPEATS repeats): quiet reads,
#            Puts, scans and the memtable at the suite's write buffer, then
#            every reader beside a writing neighbour (kappa, lambda, c_cr)
#   fit      38 reads -> base.machine.json; calibration_v2 rows, foreground,
#            jobs (the natives plus the garbage neighbours' host logs); 30 ->
#            prices.machine.json; 38 compose -> qbar.json,
#            turn_settings.machine.json (c_set from the preflight's turn
#            smoke at this machine's prices), calibration.json, report.md;
#            then the seal (MACHINE_CALIBRATED), which needs c_set measured
# Not refitted: n_win (the interference window, kept at the base's; the L0
# merge spans are reported) and the money (core price, c_s: the contract).
#
#   CONFIRM_MACHINE_CALIBRATION=YES NVME=<db root> CAL_T=<the machine's T> \
#     BASE_PRICES_FILE=<stage 18's schema-4 base> PRICES_FILE=<schema-6 reference> \
#     scripts/dbbench_pipeline/38_calibrate_machine.sh
#   38_calibrate_machine.sh plan          the parts and runs; runs nothing
#
# Knobs: MACHINE_CAL_DIR (config.sh; CAL_OUT is gone: one place per
# machine), CAL_SETTING (128:4), CAL_OPS ("W1:40 W2:40 W3:40 W4:40 W5:40"),
# CAL_TAG (machinecal, 36's RL_TAG), CAL_READ_ROUNDS (3), CAL_COST_REPEATS
# (1), CAL_PARTS ("natives reads cost fit"); 36's ALLOW_ROOT_DISK,
# ACTION_BOUNDS_FILE, CONTROLLER_RULES_FILE pass through; SSD_TRIM and
# SSD_TRIM_COMMAND (config.sh). The last line is MACHINE_CAL_EXIT=<0 done or
# done before, 5 a part failed or the calibration is not this machine's>.
set -Eeuo pipefail
PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

stamp() { echo "=== $(date -u '+%F %T') UTC $*"; }
fail() { echo "[38] $*" >&2; echo "MACHINE_CAL_EXIT=5"; exit 5; }

if [[ "$PYTHON_VENV" = /* ]]; then PY="$PYTHON_VENV/bin/python"
else PY="$PROJECT_ROOT/$PYTHON_VENV/bin/python"; fi
[[ -x "$PY" ]] || PY="$(command -v python3)"
PYTHON="$PY"   # config.sh's programme1_load_operations reads it
DB_BENCH="$DBBENCH_BUILD_DIR/db_bench"
CAL_T="${CAL_T:-}"
CAL_SETTING="${CAL_SETTING:-128:4}"
CAL_OPS="${CAL_OPS:-W1:40 W2:40 W3:40 W4:40 W5:40}"
CAL_TAG="${CAL_TAG:-machinecal}"
CAL_READ_ROUNDS="${CAL_READ_ROUNDS:-3}"
CAL_COST_REPEATS="${CAL_COST_REPEATS:-1}"
CAL_PARTS="${CAL_PARTS:-natives reads cost fit}"
[[ -z "${CAL_OUT:-}" ]] ||
  fail "CAL_OUT is gone: a machine has one calibration, in MACHINE_CAL_DIR ($MACHINE_CAL_DIR)"
CAL_OUT="$MACHINE_CAL_DIR"
BASE_PRICES_FILE="${BASE_PRICES_FILE:-}"
[[ "$CAL_T" =~ ^[1-9][0-9]*$ ]] || fail "CAL_T (the machine's size ratio) is not set"
[[ "$CAL_SETTING" =~ ^[1-9][0-9]*:[1-9][0-9]*$ ]] || fail "CAL_SETTING is <L1 MiB>:<K>"
[[ "$CAL_TAG" =~ ^[a-z][a-z0-9]*$ ]] || fail "CAL_TAG is lower-case letters and digits"
[[ "$CAL_READ_ROUNDS" =~ ^[1-3]$ ]] || fail "CAL_READ_ROUNDS is 1 to 3 (price_trees.ROUNDS)"
[[ "$CAL_COST_REPEATS" =~ ^[1-9][0-9]*$ ]] || fail "CAL_COST_REPEATS is a positive whole number"
for part in $CAL_PARTS; do
  [[ "$part" =~ ^(natives|reads|cost|fit)$ ]] || fail "unknown part $part"
done
declare -A OPS=()
WORKLOADS=()
for pair in $CAL_OPS; do
  [[ "$pair" =~ ^(W[1-5]):([1-9][0-9]*)$ ]] || fail "CAL_OPS entry $pair is not W<n>:<millions>"
  OPS[${BASH_REMATCH[1]}]="${BASH_REMATCH[2]}"
  WORKLOADS+=("${BASH_REMATCH[1]}")
done
(( ${#WORKLOADS[@]} )) || fail "CAL_OPS names no workload"
has_part() { [[ " $CAL_PARTS " == *" $1 "* ]]; }
RATIOS=(2 3 4 6 8 10)   # price_trees.SIZE_RATIOS
READS_DIR="$CAL_OUT/reads"
COST_DIR="$CAL_OUT/cost"
DBS="${NVME:-}/machine-cal-dbs"
summary_of() { echo "$NVME/$CAL_TAG-read-$1/graphs/summary.csv"; }

if [[ "${1:-}" == plan ]]; then
  if [[ -f "$CAL_OUT/MACHINE_CALIBRATED" ]]; then
    echo "machine calibration -> $CAL_OUT: SEALED already; a run does nothing"
    exit 0
  fi
  echo "machine calibration -> $CAL_OUT (parts: $CAL_PARTS; then the seal)"
  if has_part natives; then
    for w in "${WORKLOADS[@]}"; do
      echo "  natives: $w native, T$CAL_T setting $CAL_SETTING, ${OPS[$w]}M operations (36, RL_TAG $CAL_TAG)"
    done
  fi
  has_part reads && echo "  reads: 6 price trees (T ${RATIOS[*]}), warm-up + $CAL_READ_ROUNDS rounds x 3 benchmarks"
  has_part cost && echo "  cost: 38b, $CAL_COST_REPEATS repeat(s), memtable at $SUITE_WRITE_BUFFER_SIZE bytes"
  has_part fit && echo "  fit: base.machine.json, cal_fg.json, cal_jobs.json, prices.machine.json, qbar.json, turn_settings.machine.json"
  exit 0
fi
[[ -z "${1:-}" ]] || fail "unknown argument $1; see the comment at the top"
[[ "${CONFIRM_MACHINE_CALIBRATION:-}" == YES ]] || {
  echo "This measures this machine's q-bar and prices (about 2.5 hours, nothing" \
       "else may run). Set CONFIRM_MACHINE_CALIBRATION=YES to start." >&2
  exit 2
}
[[ -n "${NVME:-}" ]] || fail "NVME (the database root, as 36's) is not set"
NVME="$(realpath -m -- "$NVME")"
DBS="$NVME/machine-cal-dbs"
# Once in the machine's life: sealed here already, nothing to do.
status=0
"$PY" "$PIPELINE_DIR/38_machine_calibration.py" status --dir "$CAL_OUT" --db-root "$NVME" \
  > /dev/null || status=$?
case "$status" in
  0) stamp "this machine was calibrated already ($CAL_OUT, sealed" \
           "$(sed -n 's/.*"sealed_utc": "\([^"]*\)".*/\1/p' "$CAL_OUT/MACHINE_CALIBRATED")):" \
           "a machine is calibrated once; nothing done"
     echo "MACHINE_CAL_EXIT=0"
     exit 0 ;;
  3) ;;
  4) fail "$CAL_OUT holds another machine's calibration (a copied home?): move it away" ;;
  6) fail "$CAL_OUT was sealed, then changed: inspect it; a machine is calibrated once" ;;
  *) fail "38_machine_calibration.py status failed (exit $status)" ;;
esac
[[ -f "$BASE_PRICES_FILE" ]] || fail "BASE_PRICES_FILE ($BASE_PRICES_FILE) is not a file"
[[ -f "$PRICES_FILE" ]] || fail "PRICES_FILE ($PRICES_FILE) is not a file"
"$PY" - "$BASE_PRICES_FILE" "$PRICES_FILE" <<'PY' || fail "BASE_PRICES_FILE must be schema 4 or 5 and PRICES_FILE schema 6"
import json, sys
base, ref = (json.load(open(p)) for p in sys.argv[1:3])
sys.exit(0 if base.get("schema") in (4, 5) and ref.get("schema") == 6 else 1)
PY
[[ "$THREADS" == 1 ]] || fail "THREADS=$THREADS; the prices are one thread's time (D-15 §3a)"
[[ -x "$DB_BENCH" ]] || fail "missing $DB_BENCH; run 01 and 02"
pgrep -x db_bench > /dev/null && fail "a db_bench is already running"
ulimit -n "$(ulimit -Hn)" 2>/dev/null || true
(( $(ulimit -n) >= 16384 )) || fail "open-file limit $(ulimit -n) is under 16384 (all-open reads)"
mkdir -p "$CAL_OUT" "$DBS"
DB_BENCH_SHA256="$("$PY" "$PIPELINE_DIR/preflight_marker.py" identity --db-bench "$DB_BENCH")"
{
  echo "started_utc=$(date -u '+%F %T')"
  echo "host=$(hostname)"
  echo "db_bench_identity=$DB_BENCH_SHA256"
  echo "root_head=$(git rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "fork_head=$(git -C lib/rocksdb rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "cal_t=$CAL_T setting=$CAL_SETTING ops=$CAL_OPS tag=$CAL_TAG"
  echo "read_rounds=$CAL_READ_ROUNDS cost_repeats=$CAL_COST_REPEATS parts=$CAL_PARTS"
  echo "base_prices=$BASE_PRICES_FILE reference_prices=$PRICES_FILE"
} >> "$CAL_OUT/setup.env"
"$PY" "$PIPELINE_DIR/price_trees.py" machine --cpus "$DBBENCH_CPUS" > "$CAL_OUT/machine.json" ||
  fail "price_trees.py machine failed"
"$PY" "$PIPELINE_DIR/38_machine_calibration.py" machine-key --db-root "$NVME" \
  > "$CAL_OUT/machine_key.json" || fail "38_machine_calibration.py machine-key failed"
launcher=()
[[ -z "$DBBENCH_CPUS" ]] || launcher=(taskset -c "$DBBENCH_CPUS")
failed=()
trim_now() {  # $1 = the file its line goes to; a failed TRIM stops the stage
  local line
  line="$(ssd_trim "$NVME")" || fail "SSD TRIM failed: $line"
  echo "$line" > "$1"
}

# --- natives: q-bar per workload, and the job fit's reference runs --------
if has_part natives; then
  for w in "${WORKLOADS[@]}"; do
    stamp "natives: $w, ${OPS[$w]}M operations"
    status=0
    # MACHINE_CAL=off: these runs measure the calibration, so they cannot
    # use it; 03 trims the SSD before each.
    env SUITE_RUN_OPERATIONS_M="${OPS[$w]}" NVME="$NVME" RESUME=1 RL_TAG="$CAL_TAG" \
      RL_T="$CAL_T" RL_SETTING="$CAL_SETTING" RL_MODES=read RL_WORKLOADS="$w" \
      RL_ARMS=native PRICES_FILE="$PRICES_FILE" MACHINE_CAL=off \
      "$PIPELINE_DIR/36_rl_batch.sh" >> "$CAL_OUT/natives.log" 2>&1 || status=$?
    if (( status != 0 )) || [[ ! -f "$(summary_of "$w")" ]]; then
      stamp "natives: $w FAILED (36 exit $status; see $CAL_OUT/natives.log)"
      failed+=("natives $w")
    fi
  done
fi

# --- reads: stage 18's read prices on one set of six trees ----------------
if has_part reads; then
  if [[ -f "$READS_DIR/DONE" ]]; then
    stamp "reads: done before; skipped"
  else
    dbbench_shared_flags
    load_ops="$(programme1_load_operations)"
    COMMON_ALL_OPEN=()
    for flag in "${DBBENCH_COMMON[@]}"; do
      [[ "$flag" == --open_files=* ]] && flag=--open_files=-1
      COMMON_ALL_OPEN+=("$flag")
    done
    tree_flags() {  # $1 = T: 29's build and 18's reads share these
      TREE=(--num="$load_ops" --max_bytes_for_level_multiplier="$1"
        --level0_file_num_compaction_trigger="$L0_COMPACTION_TRIGGER"
        --level0_slowdown_writes_trigger="$L0_SLOWDOWN_TRIGGER"
        --level0_stop_writes_trigger="$L0_STOP_TRIGGER"
        --compaction_pri="$COMPACTION_PRIORITY"
        --rl_settle_hold_seconds="$SETTLE_HOLD_SECONDS"
        --ttl_seconds=0 --periodic_compaction_seconds=0
        --compaction_style=0 --db="$DBS/price-trees/T$1" --seed="$DBBENCH_SEED")
    }
    reads_ok=1
    # db_bench creates only the database's own folder, not its parents
    # (the 2026-11-08 failure on every worker).
    mkdir -p "$READS_DIR" "$DBS/price-trees"
    for ratio in "${RATIOS[@]}"; do
      out="$READS_DIR/build/T$ratio"
      if [[ -f "$out/BUILT" ]]; then continue; fi
      mkdir -p "$out"
      rm -rf "${DBS:?}/price-trees/T$ratio"
      tree_flags "$ratio"
      trim_now "$out/trim.txt"
      stamp "reads: building T$ratio"
      status=0
      ${launcher[@]+"${launcher[@]}"} "$DB_BENCH" \
        --benchmarks=filluniquerandom,settle,levelstats --use_existing_db=0 \
        "${TREE[@]}" "${DBBENCH_COMMON[@]}" > "$out/stdout.txt" 2>&1 || status=$?
      if (( status == 0 )) && grep -q '^RL_SETTLED ok=1' "$out/stdout.txt" &&
         "$PY" "$PIPELINE_DIR/price_trees.py" levels "$out/stdout.txt" > "$out/levels.txt"; then
        touch "$out/BUILT"
      else
        stamp "reads: T$ratio did not build and settle (exit $status; $out)"
        reads_ok=0
        break
      fi
    done
    if (( reads_ok )); then
      sync
      read_run() {  # $1 = T, $2 = output dir, $3 = benchmark
        mkdir -p "$2"
        tree_flags "$1"
        trim_now "$2/trim.txt"
        local command=(${launcher[@]+"${launcher[@]}"} "$DB_BENCH" "${TREE[@]}"
          "${COMMON_ALL_OPEN[@]}" --use_existing_db=1 --benchmarks="$3"
          --reads="$PRICE_READS" --seek_nexts=0)
        printf '%q ' "${command[@]}" > "$2/command.txt"
        "${command[@]}" > "$2/stdout.txt" 2>&1
      }
      mapfile -t ROUND_ORDERS < <("$PY" "$PIPELINE_DIR/price_trees.py" rounds)
      for ratio in "${RATIOS[@]}"; do
        read_run "$ratio" "$READS_DIR/warmup/T$ratio" readrandom || reads_ok=0
      done
      for round in $(seq 1 "$CAL_READ_ROUNDS"); do
        stamp "reads: round $round (${ROUND_ORDERS[round - 1]})"
        for ratio in ${ROUND_ORDERS[round - 1]}; do
          for benchmark in readmissing readrandom seekrandom; do
            read_run "$ratio" "$READS_DIR/round$round/T$ratio/$benchmark" "$benchmark" ||
              { reads_ok=0; stamp "reads: T$ratio $benchmark failed"; }
          done
        done
      done
    fi
    if (( reads_ok )); then
      touch "$READS_DIR/DONE"
      rm -rf "${DBS:?}/price-trees"
    else
      failed+=("reads")
    fi
  fi
fi

# --- cost: the October recipe (38b) ----------------------------------------
if has_part cost; then
  if [[ -f "$COST_DIR/DONE" ]]; then
    stamp "cost: done before; skipped"
  else
    [[ -e "$COST_DIR" ]] && mv "$COST_DIR" "$COST_DIR.partial-$(date -u +%H%M%S)"
    stamp "cost: 38b, $CAL_COST_REPEATS repeat(s)"
    # A memtable half full at the suite's write buffer (the 2 MiB recipe's
    # 960 entries, scaled): what a Get searches in the experiments' runs.
    mtfill=$(( 960 * SUITE_WRITE_BUFFER_SIZE / 2097152 ))
    status=0
    env OUT="$COST_DIR" DBS="$DBS/cost" CALIB_REPEATS="$CAL_COST_REPEATS" \
      CALIB_MT_WRITE_BUFFER="$SUITE_WRITE_BUFFER_SIZE" CALIB_MTFILL_KEYS="$mtfill" \
      "$PIPELINE_DIR/38b_cost_model_runs.sh" > "$CAL_OUT/cost.log" 2>&1 || status=$?
    if (( status == 0 )) && grep -q '^CALIB_RUNS_EXIT=0' "$CAL_OUT/cost.log"; then
      touch "$COST_DIR/DONE"
    else
      stamp "cost: FAILED (exit $status; see $CAL_OUT/cost.log)"
      failed+=("cost")
    fi
  fi
fi

# --- fit -------------------------------------------------------------------
if has_part fit; then
  stamp "fit"
  fit_ok=1
  natives=()
  for w in "${WORKLOADS[@]}"; do natives+=(--native "$w=$(summary_of "$w")"); done
  "$PY" "$PIPELINE_DIR/38_machine_calibration.py" reads --runs "$READS_DIR" \
    --base "$BASE_PRICES_FILE" --db-bench-sha256 "$DB_BENCH_SHA256" \
    --out "$CAL_OUT/base.machine.json" || fit_ok=0
  if (( fit_ok )); then
    "$PY" "$PIPELINE_DIR/calibration_v2.py" rows --runs "$COST_DIR/runs" &&
    "$PY" "$PIPELINE_DIR/calibration_v2.py" foreground --runs "$COST_DIR/runs" \
      --base "$CAL_OUT/base.machine.json" --out "$CAL_OUT/cal_fg.json" > "$CAL_OUT/fit_fg.log" 2>&1 ||
      fit_ok=0
  fi
  if (( fit_ok )); then
    mapfile -t arm_dirs < <("$PY" "$PIPELINE_DIR/38_machine_calibration.py" arm-dirs "${natives[@]}")
    runs=()
    for d in "${arm_dirs[@]}"; do runs+=(--run "$d"); done
    neighbours=()
    for log in "$COST_DIR"/runs/r*-nbgarb-*/neighbour_host_log.jsonl; do
      [[ -f "$log" ]] && neighbours+=(--neighbour-log "$log")
    done
    (( ${#runs[@]} == ${#WORKLOADS[@]} )) || { stamp "fit: ${#runs[@]} native arm dirs"; fit_ok=0; }
    (( ${#neighbours[@]} )) || { stamp "fit: no garbage neighbour's host log"; fit_ok=0; }
    if (( fit_ok )); then
      "$PY" "$PIPELINE_DIR/calibration_v2.py" jobs "${runs[@]}" "${neighbours[@]}" \
        --out "$CAL_OUT/cal_jobs.json" > "$CAL_OUT/fit_jobs.log" 2>&1 || fit_ok=0
    fi
  fi
  if (( fit_ok )); then
    n_win="$("$PY" -c 'import json, sys; print(json.load(open(sys.argv[1]))["n_win"])' "$PRICES_FILE")"
    "$PY" "$PIPELINE_DIR/30_provisional_prices_v2.py" --base "$CAL_OUT/base.machine.json" \
      --calibration "$CAL_OUT/cal_jobs.json" --calibration "$CAL_OUT/cal_fg.json" \
      --n-win "$n_win" --out "$CAL_OUT/prices.machine.json" || fit_ok=0
  fi
  if (( fit_ok )); then
    turn=()
    for arm in turn turn_vdn; do
      dir="$PREFLIGHT_WORK_DIR/turn_smoke/results/2M/T2/$arm"
      [[ -f "$dir/decisions.jsonl" ]] && turn+=(--turn-result "$dir")
      report="$PREFLIGHT_WORK_DIR/turn_smoke_${arm}_report.json"
      [[ -f "$report" ]] && turn+=(--turn-report "$report")
    done
    [[ " ${turn[*]} " == *" --turn-result "* ]] ||
      stamp "fit: no preflight turn smoke under $PREFLIGHT_WORK_DIR; c_set not measured"
    "$PY" "$PIPELINE_DIR/38_machine_calibration.py" compose "${natives[@]}" \
      --prices "$CAL_OUT/prices.machine.json" --reference "$PRICES_FILE" \
      --turn-settings "$TURN_SETTINGS_FILE" ${turn[@]+"${turn[@]}"} \
      --jobs "$CAL_OUT/cal_jobs.json" --setting "T$CAL_T $CAL_SETTING" \
      --out-dir "$CAL_OUT" || fit_ok=0
  fi
  (( fit_ok )) || { stamp "fit FAILED"; failed+=("fit"); }
fi

if (( ${#failed[@]} )); then
  stamp "failed: ${failed[*]}"
  echo "MACHINE_CAL_EXIT=5"
  exit 5
fi
# The seal: only a calibration whose every part is done (in this call or an
# earlier one), and only with c_set measured (seal checks the fit's files).
missing=()
for w in "${WORKLOADS[@]}"; do [[ -f "$(summary_of "$w")" ]] || missing+=("natives $w"); done
[[ -f "$READS_DIR/DONE" ]] || missing+=(reads)
[[ -f "$COST_DIR/DONE" ]] || missing+=(cost)
if (( ${#missing[@]} )); then
  stamp "not sealed: not done yet: ${missing[*]} (CAL_PARTS=$CAL_PARTS); run them"
  echo "MACHINE_CAL_EXIT=5"
  exit 5
fi
seal_workloads=()
for w in "${WORKLOADS[@]}"; do seal_workloads+=(--workload "$w"); done
"$PY" "$PIPELINE_DIR/38_machine_calibration.py" seal --dir "$CAL_OUT" --db-root "$NVME" \
  "${seal_workloads[@]}" --db-bench-sha256 "$DB_BENCH_SHA256" \
  --root-head "$(git rev-parse HEAD 2>/dev/null || echo unknown)" \
  --fork-head "$(git -C lib/rocksdb rev-parse HEAD 2>/dev/null || echo unknown)" \
  --setting "T$CAL_T $CAL_SETTING" || {
    stamp "seal FAILED: the calibration is not this machine's until sealed"
    echo "MACHINE_CAL_EXIT=5"
    exit 5
  }
stamp "done and sealed: $CAL_OUT (prices.machine.json, qbar.json," \
      "turn_settings.machine.json, report.md); 36 now prices every run with it"
echo "MACHINE_CAL_EXIT=0"
