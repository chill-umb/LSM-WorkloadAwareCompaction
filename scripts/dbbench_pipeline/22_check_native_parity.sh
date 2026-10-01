#!/usr/bin/env bash
# ACT-4 (PATHWAYS Pathway A §6; plan §6.4 step 4): PARITY_PAIRS pairs of runs
# at 1M operations, T=2, of stock RocksDB (01c) and the patched db_bench with
# every level target multiplier set to 1, then 22_check_native_parity.py.
# Both arms run the same flags as 03 (dbbench_shared_flags), native leveled
# compaction, and the same upstream benchmark sequence; the explicit flush
# makes the fork's waitforcompaction (which flushes) and stock's (which does
# not) end on the same tree. The patched arm also writes the host log, as
# every measured arm will, so its cost is inside the parity checks. Arm order
# alternates by pair. Exits with the evaluator's code: 0 passed, 1 failed,
# 2 undecided.
#
# PARITY_CHECK=arch5 runs ARCH-5 (PATHWAYS H §9) the same way on the patched
# binary alone: the native arm (the patched arm above) against the controller
# plugin in hold-only mode from n_w to the drain, judged by the same checks.
# Each hold run must also pass 28_check_plugin_run.py: the plugin started,
# logged completely and never called SetOptions. Its config is composed with
# smoke placeholders and no prices file, since hold-only only logs those
# values, so a prices file it does not need cannot fail the check.
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
PARITY_CHECK="${PARITY_CHECK:-act4}"
case "$PARITY_CHECK" in
  act4) ARMS=(stock patched); CRITERION=ACT-4 ;;
  arch5) ARMS=(native hold); CRITERION=ARCH-5 ;;
  *) echo "PARITY_CHECK must be act4 or arch5: $PARITY_CHECK" >&2; exit 1 ;;
esac
declare -A BINARY=(
  [stock]="$STOCK_BUILD_DIR/db_bench"
  [patched]="$DBBENCH_BUILD_DIR/db_bench"
  [native]="$DBBENCH_BUILD_DIR/db_bench"
  [hold]="$DBBENCH_BUILD_DIR/db_bench"
)
for arm in "${ARMS[@]}"; do
  [[ -x "${BINARY[$arm]}" ]] || {
    echo "Missing ${BINARY[$arm]}; run 01c (stock) and 01-02 (patched)." >&2
    exit 1
  }
done
if [[ "$PARITY_CHECK" == arch5 ]]; then
  [[ "$CONTROLLER_PLUGIN" = /* ]] && PLUGIN_PATH="$CONTROLLER_PLUGIN" \
    || PLUGIN_PATH="$PROJECT_ROOT/$CONTROLLER_PLUGIN"
  [[ -f "$PLUGIN_PATH" ]] || {
    echo "Missing $PLUGIN_PATH; 13's step 1 builds the plugin." >&2
    exit 1
  }
fi
launcher=()
if [[ -n "$DBBENCH_CPUS" ]]; then
  command -v taskset >/dev/null || { echo "taskset is not installed." >&2; exit 1; }
  launcher=(taskset -c "$DBBENCH_CPUS")
fi

# open_files=1000 needs more descriptors than the usual soft limit (as 03).
ulimit -n "$(ulimit -Hn)" 2>/dev/null || true

WORK="$PREFLIGHT_WORK_DIR/$PARITY_CHECK"
DB_DIR="$DB_ROOT/preflight-$PARITY_CHECK"
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
  order=("${ARMS[@]}")
  (( pair % 2 )) || order=("${ARMS[1]}" "${ARMS[0]}")
  for arm in "${order[@]}"; do
    dir="$WORK/pair-$(printf '%02d' "$pair")/$arm"
    mkdir -p "$dir"
    extra=()
    [[ "$arm" == stock ]] || extra=(--level_target_multipliers="$ones"
                                    --rl_host_log="$dir/host_log.jsonl")
    if [[ "$arm" == hold ]]; then
      "$PYTHON" "$PIPELINE_DIR/plugin_config.py" --arm hold \
        --objective-mode read --family assoc \
        --bounds "$ACTION_BOUNDS_FILE" --settings "$CONTROLLER_RULES_FILE" \
        --prices "$WORK/no_prices.json" --admission config/admission_test.json \
        --decision-log "$dir/decisions.jsonl" \
        --transition-log "$dir/transitions.jsonl" \
        --output "$dir/plugin_config.json" --placeholders
      extra+=(--rl_plugin="$PLUGIN_PATH"
              --rl_plugin_config="$dir/plugin_config.json")
    fi
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
    echo "[$CRITERION] pair $pair: $arm"
    status=0
    "${command[@]}" > "$dir/stdout.txt" 2>&1 || status=$?
    cp "$DB_DIR/LOG" "$dir/rocksdb_LOG.txt" 2>/dev/null || true
    {
      printf 'arm=%s\npair=%s\nseed=%s\nexit_code=%s\n' "$arm" "$pair" "$seed" "$status"
      printf 'db_bench_sha256=%s\n' "$(sha256sum "${BINARY[$arm]}" | awk '{print $1}')"
    } > "$dir/metadata.env"
    rm -rf "$DB_DIR"
    (( status == 0 )) || {
      echo "[$CRITERION] $arm run of pair $pair exited $status; see $dir/stdout.txt" >&2
      exit 1
    }
    if [[ "$arm" == hold ]]; then
      "$PYTHON" "$PIPELINE_DIR/28_check_plugin_run.py" \
        --stdout "$dir/stdout.txt" --decisions "$dir/decisions.jsonl" \
        --transitions "$dir/transitions.jsonl" --mode hold-only \
        --output "$dir/plugin_check.json" || exit 1
    fi
  done
done

"$PYTHON" "$PIPELINE_DIR/22_check_native_parity.py" "$WORK" \
  --arms "${ARMS[@]}" --criterion "$CRITERION" \
  --stall-fraction-margin "$PARITY_STALL_FRACTION_MARGIN" \
  --output "$WORK/${PARITY_CHECK}_report.json"
