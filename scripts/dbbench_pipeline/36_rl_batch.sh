#!/usr/bin/env bash
# learned2's diagnostic batch (owner, 2026-11-05: F1-F5, controller/learned2.h;
# exploratory, not a gate run). Per T: the setting's tree (31; a kept tree of
# this binary is reused), then for each priority mode and each workload one
# cold learned2 run (03) priced at beta* RL_BETA_STAR with the q-bar stand-in.
# A failed run is logged and the batch goes on (the owner's rule for long
# chains); a missing preflight stops it.
#
#   NVME=/mnt/nvme PRICES_FILE=<schema-6 prices> scripts/dbbench_pipeline/36_rl_batch.sh
#   36_rl_batch.sh plan        the runs, in order; runs nothing
#
#   RL_T="2"                    the size ratios, one node per T as a rule
#   RL_SETTING="128:4"          <L1 MiB>:<K>, the start setting
#   RL_MODES="read write"       CONTROLLER_OBJECTIVE_MODE per pass
#   RL_WORKLOADS="W1 W2 W3 W4 W5"
#   RL_BETA_STAR=2              a contract-reported beta*: diagnostic runs only
#   MACHINE_CAL=on              (owner, 2026-11-08) every run is priced with
#                               this machine's calibration, sealed by stage 38
#                               in MACHINE_CAL_DIR: its prices.machine.json,
#                               qbar.json (q-bar per workload) and
#                               turn_settings.machine.json (c_set). With it on,
#                               the batch refuses to start without a sealed
#                               calibration of this machine, and refuses
#                               PRICES_FILE, TURN_SETTINGS_FILE, RL_QBAR_FILE
#                               or RL_QBAR_STANDIN given beside it.
#                               MACHINE_CAL=off: the four below, as given.
#   RL_QBAR_STANDIN=50000       while the contract's q-bar is open (D-29 §6)
#   RL_QBAR_FILE=               a JSON object {"W1": ops/s, ...}: each
#                               workload's own stand-in, in place of
#                               RL_QBAR_STANDIN; it must name every workload
#   RL_REPEATS=1
#   RL_ARMS=learned2            03's arms per run, in order (e.g. "native turn
#                               turn_vdn": the turn arms beside a same-day
#                               native on the same tree)
#   RL_TAG=rl2                  the results' folder and session prefix (a new
#                               tag keeps an earlier batch's results apart,
#                               since RESUME skips completed runs)
#   RESUME, ALLOW_ROOT_DISK as 33; the suite's geometry from config.sh.
# Folders under NVME:
#   suite-trees/                31's trees (shared with 33)
#   <RL_TAG>-<mode>-<W>/T<T>-l1<MiB>-k<K>/   03's results, session <RL_TAG>-<mode>-<W>
#   suite-dbs/                  the runs' copies (removed)
# The last line is RL_BATCH_EXIT=<0 every run completed, 5 something failed>.
set -Eeuo pipefail
# What the caller gave, before config.sh's defaults fill the names in.
CALLER_FILES=()
for name in PRICES_FILE TURN_SETTINGS_FILE RL_QBAR_FILE RL_QBAR_STANDIN; do
  [[ -z "${!name:-}" ]] || CALLER_FILES+=("$name")
done
PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"
STAGE=36
# shellcheck source=chain_common.sh
source "$PIPELINE_DIR/chain_common.sh"
NVME="$(realpath -m -- "$NVME")"
RL_T="${RL_T:-2}"
RL_SETTING="${RL_SETTING:-128:4}"
RL_MODES="${RL_MODES:-read write}"
RL_WORKLOADS="${RL_WORKLOADS:-W1 W2 W3 W4 W5}"
RL_BETA_STAR="${RL_BETA_STAR:-2}"
RL_QBAR_STANDIN="${RL_QBAR_STANDIN:-50000}"
RL_QBAR_FILE="${RL_QBAR_FILE:-}"
RL_REPEATS="${RL_REPEATS:-1}"
RL_ARMS="${RL_ARMS:-learned2}"
RL_TAG="${RL_TAG:-rl2}"
MIN_FREE_GB="${MIN_FREE_GB:-100}"
export MIN_FREE_GB
GEOMETRY=(WRITE_BUFFER_SIZE="$SUITE_WRITE_BUFFER_SIZE"
          TARGET_FILE_SIZE="$SUITE_TARGET_FILE_SIZE"
          BLOCK_CACHE_SIZE="$SUITE_BLOCK_CACHE_SIZE"
          OPEN_FILES="$SUITE_OPEN_FILES" RUN_MEMORY_MAX="$SUITE_MEMORY_MAX")
l1="${RL_SETTING%%:*}" k="${RL_SETTING##*:}"
setting_id() { echo "T$1-l1$l1-k$k"; }
# This machine's calibration (stage 38), unless MACHINE_CAL=off.
case "$MACHINE_CAL" in
  on)
    (( ${#CALLER_FILES[@]} == 0 )) ||
      fail "${CALLER_FILES[*]} given, but MACHINE_CAL=on prices every run with this" \
           "machine's calibration ($MACHINE_CAL_DIR): drop them, or set MACHINE_CAL=off"
    status=0
    cal_env="$("$PY" "$PIPELINE_DIR/38_machine_calibration.py" status \
      --dir "$MACHINE_CAL_DIR" --db-root "$NVME")" || status=$?
    (( status == 0 )) ||
      fail "no usable calibration of this machine in $MACHINE_CAL_DIR (status $status):" \
           "run 38_calibrate_machine.sh once on this machine first, or set MACHINE_CAL=off"
    PRICES_FILE="$(sed -n 's/^PRICES_FILE=//p' <<< "$cal_env")"
    TURN_SETTINGS_FILE="$(sed -n 's/^TURN_SETTINGS_FILE=//p' <<< "$cal_env")"
    RL_QBAR_FILE="$(sed -n 's/^QBAR_FILE=//p' <<< "$cal_env")"
    MACHINE_CAL_ID="$(sed -n 's/^MACHINE_CAL_ID=//p' <<< "$cal_env")"
    [[ -n "$PRICES_FILE" && -n "$TURN_SETTINGS_FILE" && -n "$RL_QBAR_FILE" &&
       -n "$MACHINE_CAL_ID" ]] || fail "38's status named no files: $cal_env"
    ;;
  off) MACHINE_CAL_ID=off ;;
  *) fail "MACHINE_CAL=$MACHINE_CAL; it is on or off" ;;
esac
qbar_for() {  # $1 = workload: its q-bar stand-in
  if [[ -z "$RL_QBAR_FILE" ]]; then
    echo "$RL_QBAR_STANDIN"
    return 0
  fi
  "$PY" - "$RL_QBAR_FILE" "$1" <<'PY'
import json, math, sys
value = json.load(open(sys.argv[1])).get(sys.argv[2])
if isinstance(value, bool) or not isinstance(value, (int, float)) \
        or not math.isfinite(value) or value <= 0:
    sys.exit(f"{sys.argv[1]} has no positive q-bar for {sys.argv[2]}")
print(repr(float(value)))
PY
}
case "${1:-}" in
  plan)
    n=0
    for T in $RL_T; do
      echo "tree $(setting_id "$T"): reused if kept, else $SUITE_PRELOAD_KEYS keys"
      for mode in $RL_MODES; do
        for w in $RL_WORKLOADS; do
          echo "  $RL_ARMS $mode beta*=$RL_BETA_STAR q-bar $(qbar_for "$w") $w" \
               "$(setting_id "$T"): $RL_REPEATS x ${SUITE_RUN_OPERATIONS_M}M operations"
          n=$(( n + RL_REPEATS * $(wc -w <<< "$RL_ARMS") ))
        done
      done
    done
    echo "# $n runs"
    exit 0
    ;;
  "") ;;
  *) fail "unknown argument $1; see the comment at the top" ;;
esac
[[ "$RL_SETTING" =~ ^[1-9][0-9]*:[1-9][0-9]*$ ]] || fail "RL_SETTING is <L1 MiB>:<K>"
[[ "$RL_REPEATS" =~ ^[1-9][0-9]*$ ]] || fail "RL_REPEATS must be a positive whole number"
[[ "$RL_TAG" =~ ^[a-z][a-z0-9]*$ ]] || fail "RL_TAG is lower-case letters and digits"
for arm in $RL_ARMS; do
  [[ "$arm" =~ ^(native|learned2|turn|turn_vdn)$ ]] || fail "unknown arm $arm"
done
for mode in $RL_MODES; do
  [[ "$mode" == read || "$mode" == write || "$mode" == space || "$mode" == balanced ]] ||
    fail "unknown mode $mode"
done
for w in $RL_WORKLOADS; do
  suite_workload_recipe "$w" > /dev/null || fail "unknown workload $w (W1-W5)"
  qbar_for "$w" > /dev/null || fail "no q-bar for $w (RL_QBAR_FILE $RL_QBAR_FILE)"
done
"$PY" - "$PRICES_FILE" <<'PY' || fail "PRICES_FILE must be a schema-6 (cost model 2) record"
import json, sys
r = json.load(open(sys.argv[1]))
sys.exit(0 if r.get("schema") == 6 and r.get("cost_model") == 2 else 1)
PY
fresh=(suite-dbs)
for mode in $RL_MODES; do
  for w in $RL_WORKLOADS; do fresh+=("$RL_TAG-$mode-$w"); done
done
node_checks "${fresh[@]}"
failed=()
stamp "rl batch: arms $RL_ARMS; T $RL_T; setting $RL_SETTING; modes $RL_MODES; workloads" \
      "$RL_WORKLOADS; beta* $RL_BETA_STAR; q-bar ${RL_QBAR_FILE:-stand-in $RL_QBAR_STANDIN};" \
      "prices $PRICES_FILE; turn settings $TURN_SETTINGS_FILE; machine calibration $MACHINE_CAL_ID;" \
      "decay $DECAY"
for T in $RL_T; do
  id="$(setting_id "$T")"
  stamp "$id: tree"
  status=0
  tree_line="$(env "${GEOMETRY[@]}" CONFIRM_PRELOAD=YES SIZE_RATIOS="$T" \
    MAX_BYTES_FOR_LEVEL_BASE="$(( l1 * 1048576 ))" L0_COMPACTION_TRIGGER="$k" \
    PRELOAD_KEYS="$SUITE_PRELOAD_KEYS" TREE_ROOT="$NVME/suite-trees" \
    "$PIPELINE_DIR/31_preload_tree.sh" | tail -n 1)" || status=$?
  (( status != 7 )) || fail "31 found no matching preflight; run 13 first"
  if (( status != 0 )) || [[ "$tree_line" != TREE_DIR=* ]]; then
    stamp "$id: tree FAILED (exit $status); its runs are skipped"
    failed+=("$id: tree")
    continue
  fi
  tree="${tree_line#TREE_DIR=}"
  for mode in $RL_MODES; do
    for w in $RL_WORKLOADS; do
      free_gb="$(df --output=avail -BG "$NVME" | tail -n 1 | tr -dc 0-9)"
      if (( free_gb < MIN_FREE_GB )); then
        stamp "$id $mode $w: only ${free_gb} GB free; stopping"
        failed+=("$id: disk")
        break 3
      fi
      qbar="$(qbar_for "$w")"
      stamp "$id: $mode $w (q-bar $qbar)"
      status=0
      env "${GEOMETRY[@]}" CONFIRM_EXPERIMENTS=YES SUITE_WORKLOAD="$w" \
        TREE_DIR="$tree" EXPERIMENT_ARMS="$RL_ARMS" SIZE_RATIOS="$T" \
        MAX_BYTES_FOR_LEVEL_BASE="$(( l1 * 1048576 ))" L0_COMPACTION_TRIGGER="$k" \
        WORKLOAD_SIZES_M="$SUITE_RUN_OPERATIONS_M" REPEATS="$RL_REPEATS" \
        DIAGNOSTIC_RUN=1 CONTROLLER_OBJECTIVE_MODE="$mode" \
        CONTROLLER_BETA_STAR="$RL_BETA_STAR" CONTROLLER_QBAR_STANDIN="$qbar" \
        PRICES_FILE="$PRICES_FILE" TURN_SETTINGS_FILE="$TURN_SETTINGS_FILE" \
        MACHINE_CAL_ID="$MACHINE_CAL_ID" DECAY="$DECAY" \
        RESUME=1 SESSION_ID="$RL_TAG-$mode-$w" RESULTS_ROOT="$NVME/$RL_TAG-$mode-$w/$id" \
        DB_ROOT="$NVME/suite-dbs/$w/$id-$RL_TAG-$mode" "$PIPELINE_DIR/03_run_experiments.sh" ||
        status=$?
      (( status != 7 )) || fail "03 found no matching preflight; run 13 first"
      (( status != 9 )) || fail "03 could not TRIM the SSD before a run; see above"
      if (( status != 0 )); then
        stamp "$id: $mode $w FAILED (exit $status); going on"
        failed+=("$id: $mode $w")
      fi
    done
  done
done
# Each results folder scored by 04 (its summary.csv: WA, RA, SA, stalls).
for mode in $RL_MODES; do
  for w in $RL_WORKLOADS; do
    [[ -d "$NVME/$RL_TAG-$mode-$w" ]] || continue
    status=0
    "$PY" "$PIPELINE_DIR/04_generate_graphs.py" --results "$NVME/$RL_TAG-$mode-$w" \
      --summary-only || status=$?
    if (( status == 3 )); then
      stamp "$RL_TAG-$mode-$w: 04 refused arms; see its graphs/refused_arms.json"
      failed+=("$RL_TAG-$mode-$w: refused arms")
    elif (( status != 0 )); then
      stamp "$RL_TAG-$mode-$w: 04 FAILED (exit $status)"
      failed+=("$RL_TAG-$mode-$w: 04")
    fi
  done
done
if (( ${#failed[@]} )); then
  stamp "failed: ${failed[*]}"
  echo "RL_BATCH_EXIT=5"
  exit 5
fi
stamp "done"
echo "RL_BATCH_EXIT=0"
