#!/usr/bin/env bash
# The step runs of PREREGISTRATION D-31 (D-29 §4) and of the K-step batch
# (owner, 2026-10-21), unattended. Each run is its twin -- native repeat 1 at
# (L1, K = 4), same workload and T, on the same kept tree, with the same seed
# and binary -- plus scheduled changes: D-31's kinds apply HOLD_APPLY_M at
# measured operation STEP_AT_OP (D-31 §1, X = 10M); "k0" applies a new L0
# trigger at X; "multi" applies a seeded schedule of triggers from X on. The
# run list and its order are step_plan.py's. A failed or skipped run is logged
# and the chain goes on; a missing preflight stops it.
#
#   NVME=/mnt/nvme scripts/dbbench_pipeline/34_step_chain.sh
#   34_step_chain.sh plan        the runs, in order; runs nothing
#
# Defaults are D-31's; a node takes its share with STEP_T and STEP_WORKLOADS:
#   STEP_T="3 6 9"                    size ratios
#   STEP_WORKLOADS="W1 W2 W3 W4 W5"   workloads (two-level steps: W1, W2 only)
#   STEP_KINDS="control single two"   which kinds run: D-31 §2's, or the
#                                     K-step batch's "k0" and "multi"
#   STEP_L1=128                       L1 bases in MiB (D-31's kinds: 128;
#                                     the K-step batch: "64 128 256")
#   STEP_AT_OP=10000000               X, in measured operations
#   STEP_K0_CAP=19                    the hold arm's k0_cap for k0 and multi
#                                     runs (HOLD_K0_CAP; below the slowdown
#                                     trigger 20), so every target is taken
#   STEP_TWIN_ROOT=$NVME/step-twins   the metadata.env of twins that ran on
#                                     another node, as
#                                     <W>/T<T>/l1<L1>/metadata.env (at 128 MiB
#                                     also D-31's <W>/T<T>/metadata.env); a
#                                     twin that ran here is read from
#                                     suite-<W>/ first
#   STEP_MIN_FREE_GB=100              free space needed before each run
#   NVME, RESUME, ALLOW_ROOT_DISK as 33. The suite's geometry, run size and
#   preload are 33's (config.sh SUITE_*).
# A (workload, T) whose twin is not found, or does not fit it (tree, seed,
# binary, workload: step_plan.py twin), is skipped and logged; a later run
# with RESUME=1 picks it up. Each finished run is checked against its twin and
# its planned change (step_plan.py run). Trees are never built here: a step
# runs only on its twin's tree. The hold arm's config takes the preflight's
# placeholders (03 allows them for a hold-only matrix: the hold-only plugin
# never acts, so they reach only its logs; the change itself is checked
# against the placeholder bounds [0.5, 2]); D-18's bounds and q-bar are not
# fixed yet.
# Folders under NVME:
#   suite-trees/T<T>-l1<L1>-k4-n<keys>-s<seed>/  the twins' trees (kept)
#   step-<W>/T<T>-l1<L1>-k4/<step id>/           03's results, session step-<W>
#   step-dbs/                                    the runs' copies (removed by 03)
# Each workload's results are scored by 04 at the end. The last line is
# STEP_CHAIN_EXIT=<0 everything ran, 5 something failed or was skipped>.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"
STAGE=34
# shellcheck source=chain_common.sh
source "$PIPELINE_DIR/chain_common.sh"
NVME="$(realpath -m -- "$NVME")"
STEP_T="${STEP_T:-3 6 9}"
STEP_WORKLOADS="${STEP_WORKLOADS:-W1 W2 W3 W4 W5}"
STEP_KINDS="${STEP_KINDS:-control single two}"
STEP_L1="${STEP_L1:-128}"
STEP_AT_OP="${STEP_AT_OP:-10000000}"
STEP_K0_CAP="${STEP_K0_CAP:-19}"
STEP_TWIN_ROOT="${STEP_TWIN_ROOT:-$NVME/step-twins}"
MIN_FREE_GB="${STEP_MIN_FREE_GB:-100}"
export MIN_FREE_GB
# Tests point these at a stand-in 03 and skip 04.
RUN_EXPERIMENTS="${STEP_RUN_EXPERIMENTS:-$PIPELINE_DIR/03_run_experiments.sh}"
EVALUATE="${STEP_EVALUATE:-1}"
K=4
if [[ "$DBBENCH_BUILD_DIR" = /* ]]; then
  DB_BENCH="$DBBENCH_BUILD_DIR/db_bench"
else
  DB_BENCH="$PROJECT_ROOT/$DBBENCH_BUILD_DIR/db_bench"
fi

# Every run has the suite's geometry (D-29 §1), as its twin had.
GEOMETRY=(WRITE_BUFFER_SIZE="$SUITE_WRITE_BUFFER_SIZE"
          TARGET_FILE_SIZE="$SUITE_TARGET_FILE_SIZE"
          BLOCK_CACHE_SIZE="$SUITE_BLOCK_CACHE_SIZE"
          OPEN_FILES="$SUITE_OPEN_FILES" RUN_MEMORY_MAX="$SUITE_MEMORY_MAX")

plan_rows() {
  "$PY" "$PIPELINE_DIR/step_plan.py" plan --trees "$NVME/suite-trees" \
    --keys "$SUITE_PRELOAD_KEYS" --seed "$PRELOAD_SEED" --t "$STEP_T" \
    --workloads "$STEP_WORKLOADS" --kinds "$STEP_KINDS" --num-levels "$NUM_LEVELS" \
    --l1 "$STEP_L1" --at-op "$STEP_AT_OP" --operations "$(( SUITE_RUN_OPERATIONS_M * 1000000 ))"
}
tree_of() { echo "$NVME/suite-trees/T$1-l1$2-k${K}-n${SUITE_PRELOAD_KEYS}-s${PRELOAD_SEED}"; }  # T, L1
# The twin's metadata.env: here (33's layout) first, then STEP_TWIN_ROOT.
twin_of() {  # $1=W $2=T $3=L1
  local here="$NVME/suite-$1/T$2-l1$3-k${K}/${SUITE_RUN_OPERATIONS_M}M/T$2/repeat-01/native/metadata.env"
  if [[ -f "$here" ]]; then echo "$here"
  elif [[ -f "$STEP_TWIN_ROOT/$1/T$2/l1$3/metadata.env" ]]; then echo "$STEP_TWIN_ROOT/$1/T$2/l1$3/metadata.env"
  elif [[ "$3" == 128 && -f "$STEP_TWIN_ROOT/$1/T$2/metadata.env" ]]; then
    echo "$STEP_TWIN_ROOT/$1/T$2/metadata.env"
  fi
  return 0
}

[[ "$STEP_AT_OP" =~ ^[1-9][0-9]*$ ]] || fail "STEP_AT_OP must be a positive whole number"
[[ "$STEP_K0_CAP" =~ ^[1-9][0-9]*$ ]] || fail "STEP_K0_CAP must be a positive whole number"
(( STEP_AT_OP < SUITE_RUN_OPERATIONS_M * 1000000 )) ||
  fail "STEP_AT_OP ($STEP_AT_OP) must fall inside the ${SUITE_RUN_OPERATIONS_M}M measured operations"
declare -A PROFILE
for w in $STEP_WORKLOADS; do
  recipe="$(suite_workload_recipe "$w")" || fail "unknown workload $w (W1-W5)"
  read -r PROFILE[$w] _ <<< "$recipe"
done
rows="$(plan_rows)" || fail "step_plan.py plan failed"
# A list, not a loop over stdin: 03 and what it starts must not read the rows.
ROWS=()
[[ -z "$rows" ]] || mapfile -t ROWS <<< "$rows"

case "${1:-}" in
  plan)
    for row in "${ROWS[@]}"; do
      IFS=$'\t' read -r phase w T l1 sid vector k0 schedule <<< "$row"
      if [[ "$phase" == missing ]]; then
        echo "T$w l1$T: no tree to plan from ($l1${sid:+ $sid})"
        continue
      fi
      case "$phase" in
        k0) echo "$phase $w T$T l1$l1 $sid" ;;
        multi) echo "$phase $w T$T l1$l1 $sid $(tr ';' '\n' <<< "$schedule" | wc -l) changes" ;;
        *) echo "$phase $w T$T $sid $vector" ;;
      esac
    done
    settings="" && for l1 in $STEP_L1; do settings+="${settings:+ }$l1:$K"; done
    echo "# $(grep -vc '^missing' <<< "$rows" || true) runs at $settings, change at operation $STEP_AT_OP"
    exit 0
    ;;
  "") ;;
  *) fail "unknown argument $1; see the comment at the top" ;;
esac

fresh=(step-dbs)
for w in $STEP_WORKLOADS; do fresh+=("step-$w"); done
node_checks "${fresh[@]}"
binary="$("$PY" "$PIPELINE_DIR/preflight_marker.py" identity --db-bench "$DB_BENCH")" ||
  fail "cannot read the identity of $DB_BENCH"

failed=()
declare -A TWIN_STATE
stamp "steps: T $STEP_T; workloads $STEP_WORKLOADS; kinds $STEP_KINDS; change at operation $STEP_AT_OP;" \
      "binary $binary"
for row in "${ROWS[@]}"; do
  IFS=$'\t' read -r phase w T l1 sid vector k0 schedule <<< "$row"
  if [[ "$phase" == missing ]]; then
    stamp "T$w l1$T: no tree to plan from; its steps are skipped"
    failed+=("T$w l1$T: tree")
    continue
  fi
  results="$NVME/step-$w/T$T-l1$l1-k${K}/$sid"
  run_dir="$results/${SUITE_RUN_OPERATIONS_M}M/T$T/hold"
  if [[ -f "$run_dir/COMPLETED" ]]; then
    continue
  fi
  tree="$(tree_of "$T" "$l1")"
  key="$w/T$T"
  [[ "$l1" == 128 ]] || key+="/l1$l1"
  if [[ -z "${TWIN_STATE[$key]:-}" ]]; then
    twin="$(twin_of "$w" "$T" "$l1")"
    if [[ -z "$twin" ]]; then
      TWIN_STATE[$key]="missing"
    elif [[ ! -f "$tree/COMPLETED" || ! -f "$tree/db/CURRENT" ]]; then
      TWIN_STATE[$key]="tree $tree has no database"
    elif problems="$("$PY" "$PIPELINE_DIR/step_plan.py" twin --twin "$twin" --tree "$tree" \
        --seed "$DBBENCH_SEED" --binary "$binary" --profile "${PROFILE[$w]}" 2>&1)"; then
      TWIN_STATE[$key]="ok $twin"
    else
      TWIN_STATE[$key]="does not fit: ${problems//$'\n'/; }"
    fi
    stamp "$key: twin ${TWIN_STATE[$key]}"
  fi
  if [[ "${TWIN_STATE[$key]}" != ok* ]]; then
    failed+=("$key $sid: twin ${TWIN_STATE[$key]%% *}")
    continue
  fi
  twin="${TWIN_STATE[$key]#ok }"
  free_gb="$(df --output=avail -BG "$NVME" | tail -n 1 | tr -dc 0-9)"
  if (( free_gb < MIN_FREE_GB )); then
    stamp "only ${free_gb} GB free; stopping"
    failed+=("disk")
    break
  fi
  # The change: none (a control), D-31's vector at X, a trigger at X (k0),
  # or a schedule (multi); 03 records it as hold_apply.
  change=(HOLD_APPLY_AT_OP= HOLD_APPLY_M= HOLD_APPLY_K0= HOLD_APPLY_SCHEDULE= HOLD_K0_CAP=) apply=""
  if [[ "$schedule" != - ]]; then
    change=(HOLD_APPLY_AT_OP= HOLD_APPLY_M= HOLD_APPLY_K0= HOLD_APPLY_SCHEDULE="$schedule"
            HOLD_K0_CAP="$STEP_K0_CAP") apply="$schedule"
  elif [[ "$vector" != - ]]; then
    [[ "$k0" != - ]] || k0=""
    change=(HOLD_APPLY_AT_OP="$STEP_AT_OP" HOLD_APPLY_M="$vector" HOLD_APPLY_K0="$k0"
            HOLD_APPLY_SCHEDULE= HOLD_K0_CAP="${k0:+$STEP_K0_CAP}") apply="$STEP_AT_OP|$vector|$k0"
  fi
  stamp "$key $sid ($phase)"
  status=0
  env "${GEOMETRY[@]}" "${change[@]}" HOLD_START_M= \
    CONFIRM_EXPERIMENTS=YES DIAGNOSTIC_RUN=1 PLUGIN_PLACEHOLDERS=1 EXPERIMENT_ARMS=hold \
    SUITE_WORKLOAD="$w" TREE_DIR="$tree" SIZE_RATIOS="$T" \
    MAX_BYTES_FOR_LEVEL_BASE="$(( l1 * 1048576 ))" L0_COMPACTION_TRIGGER="$K" \
    WORKLOAD_SIZES_M="$SUITE_RUN_OPERATIONS_M" REPEATS=1 RESUME=1 \
    SESSION_ID="step-$w" RESULTS_ROOT="$results" DB_ROOT="$NVME/step-dbs/$w/T$T-l1$l1/$sid" \
    "$RUN_EXPERIMENTS" < /dev/null || status=$?
  (( status != 7 )) || fail "03 found no matching preflight; run 13 first"
  if (( status != 0 )); then
    stamp "$key $sid FAILED (exit $status); going on"
    failed+=("$key $sid")
    continue
  fi
  if ! problems="$("$PY" "$PIPELINE_DIR/step_plan.py" run --run "$run_dir/metadata.env" \
      --twin "$twin" --apply "$apply" 2>&1)"; then
    stamp "$key $sid does not match its twin or plan: ${problems//$'\n'/; }"
    failed+=("$key $sid: check")
  fi
done

if (( EVALUATE )); then
  for w in $STEP_WORKLOADS; do
    [[ -d "$NVME/step-$w" ]] || continue
    status=0
    "$PY" "$PIPELINE_DIR/04_generate_graphs.py" --results "$NVME/step-$w" \
      --summary-only || status=$?
    if (( status != 0 )); then
      stamp "$w: 04 exit $status"
      failed+=("$w: 04")
    fi
  done
fi
if (( ${#failed[@]} )); then
  stamp "failed or skipped: ${failed[*]}"
  echo "STEP_CHAIN_EXIT=5"
  exit 5
fi
stamp "done"
echo "STEP_CHAIN_EXIT=0"
