#!/usr/bin/env bash
# The workload suite's native sweep (PREREGISTRATION D-29 §3; D-27),
# unattended. Per T, per setting (L1 base, L0 trigger): the setting's tree
# (31), then every workload's repeats on checked copies of it (03), then the
# next setting. A failed tree or workload is logged and the chain goes on
# (the owner's rule for overnight chains); a missing preflight stops it.
#
#   NVME=/mnt/nvme scripts/dbbench_pipeline/33_suite_chain.sh
#   33_suite_chain.sh plan        the steps, in order; runs nothing
#
# From config.sh, all overridable: SUITE_T and SUITE_SETTINGS (D-29 §3's
# grid; one node per T: SUITE_T=4), SUITE_WORKLOADS (the owner's plan of
# 2026-10-16: W1 W2), and the suite's run shape, geometry and memory cap.
# Also:
#   SUITE_REPEATS=3          repeats per workload and setting (D-29 §3)
#   SUITE_ARMS=native        the arms 03 runs on each tree
#   SUITE_KEEP_TREES=all     settings ("<L1 MiB>:<K>", or "all") whose trees
#                            keep their database after their runs. Every tree
#                            is kept while the propagation law is developed
#                            (owner, 2026-10-20), so any run can be replayed
#                            exactly from its MANIFEST; about 20 GB per tree.
#                            Once the law is frozen, 128:4 is enough (D-29
#                            §4's step runs start from those trees). A tree is
#                            kept whenever one of its workloads failed, so a
#                            rerun resumes on the same tree.
#   SUITE_MIN_FREE_GB=100    free space needed before each setting (a tree
#                            and W4's grown copy)
#   NVME, RESUME, ALLOW_ROOT_DISK as 24 and 25; DIAGNOSTIC_RUN as 03.
# Folders under NVME:
#   suite-trees/T<T>-l1<MiB>-k<K>-n<keys>-s<seed>/   31's trees
#   suite-<W>/T<T>-l1<MiB>-k<K>/                     03's results, session suite-<W>
#   suite-dbs/                                       the runs' copies (removed)
# Each workload's results are scored by 04 at the end. The last line is
# SUITE_CHAIN_EXIT=<0 everything ran, 5 something failed>.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"
STAGE=33
# shellcheck source=chain_common.sh
source "$PIPELINE_DIR/chain_common.sh"
NVME="$(realpath -m -- "$NVME")"
REPEATS_EACH="${SUITE_REPEATS:-3}"
ARMS="${SUITE_ARMS:-native}"
KEEP="${SUITE_KEEP_TREES:-all}"
MIN_FREE_GB="${SUITE_MIN_FREE_GB:-100}"
export MIN_FREE_GB

# Every preload and run of the suite has its geometry (D-29 §1).
GEOMETRY=(WRITE_BUFFER_SIZE="$SUITE_WRITE_BUFFER_SIZE"
          TARGET_FILE_SIZE="$SUITE_TARGET_FILE_SIZE"
          BLOCK_CACHE_SIZE="$SUITE_BLOCK_CACHE_SIZE"
          OPEN_FILES="$SUITE_OPEN_FILES" RUN_MEMORY_MAX="$SUITE_MEMORY_MAX")

setting_id() { echo "T$1-l1$2-k$3"; }  # T, L1 MiB, K
keep_tree() {  # $1="<L1 MiB>:<K>"
  [[ "$KEEP" == all || " $KEEP " == *" $1 "* ]]
}
# Every workload's runs at a setting completed (a resumed chain skips it,
# and does not need its tree, whose database may be gone).
setting_done() {  # $1=setting id
  local w found
  for w in $SUITE_WORKLOADS; do
    found="$(find "$NVME/suite-$w/$1" -name COMPLETED 2>/dev/null | wc -l)"
    (( found == REPEATS_EACH * $(wc -w <<< "$ARMS") )) || return 1
  done
}

case "${1:-}" in
  plan)
    trees=0 runs=0
    for T in $SUITE_T; do
      for setting in $SUITE_SETTINGS; do
        l1="${setting%%:*}" k="${setting##*:}"
        echo "tree $(setting_id "$T" "$l1" "$k"): $SUITE_PRELOAD_KEYS keys," \
             "then $SUITE_WORKLOADS x $REPEATS_EACH repeats of $ARMS," \
             "${SUITE_RUN_OPERATIONS_M}M operations each$(keep_tree "$setting" && echo '; tree kept')"
        trees=$(( trees + 1 ))
        runs=$(( runs + $(wc -w <<< "$SUITE_WORKLOADS") * REPEATS_EACH * $(wc -w <<< "$ARMS") ))
      done
    done
    echo "# $trees trees, $runs runs"
    exit 0
    ;;
  "") ;;
  *) fail "unknown argument $1; see the comment at the top" ;;
esac

# Checks that fail in seconds.
for value in "$REPEATS_EACH" "$MIN_FREE_GB"; do
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || fail "SUITE_REPEATS and SUITE_MIN_FREE_GB must be positive whole numbers"
done
for setting in $SUITE_SETTINGS; do
  [[ "$setting" =~ ^[1-9][0-9]*:[1-9][0-9]*$ ]] ||
    fail "SUITE_SETTINGS holds <L1 MiB>:<K> pairs; got $setting"
done
families=()
for w in $SUITE_WORKLOADS; do
  recipe="$(suite_workload_recipe "$w")" || fail "unknown workload $w (W1-W5)"
  read -r _ family _ <<< "$recipe"
  families+=("$family")
done
# D-29 §6: a long run of a family the contract does not list yet is refused
# by 03; refuse it here, before the first tree.
if (( SUITE_RUN_OPERATIONS_M > PREFLIGHT_SHORT_RUN_MAX_M )) && [[ "$DIAGNOSTIC_RUN" != 1 ]]; then
  PYTHONPATH="$PIPELINE_DIR" "$PY" - "${families[@]}" <<'PY' || fail "refusing to start"
import sys
import research_objective
contract, _ = research_objective.load_contract()
missing = [f for f in sys.argv[1:]
           if not research_objective.suite_workload_listed(contract, f)]
if missing:
    raise SystemExit(f"PREREGISTRATION D-29 §6: the contract does not list "
                     f"{', '.join(missing)} as suite workloads yet; that edit "
                     "needs the owner's go (or run DIAGNOSTIC_RUN=1).")
PY
fi
fresh=(suite-trees suite-dbs)
for w in $SUITE_WORKLOADS; do fresh+=("suite-$w"); done
node_checks "${fresh[@]}"

failed=()
stamp "suite: T $SUITE_T; settings $SUITE_SETTINGS; workloads $SUITE_WORKLOADS;" \
      "$REPEATS_EACH repeats of $ARMS; trees kept: $KEEP"
for T in $SUITE_T; do
  for setting in $SUITE_SETTINGS; do
    l1="${setting%%:*}" k="${setting##*:}"
    id="$(setting_id "$T" "$l1" "$k")"
    if setting_done "$id"; then
      stamp "$id: every run completed; skipped"
      continue
    fi
    free_gb="$(df --output=avail -BG "$NVME" | tail -n 1 | tr -dc 0-9)"
    if (( free_gb < MIN_FREE_GB )); then
      stamp "$id: only ${free_gb} GB free; stopping"
      failed+=("$id: disk")
      break 2
    fi
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
    all_ran=1
    for w in $SUITE_WORKLOADS; do
      stamp "$id: $w"
      status=0
      env "${GEOMETRY[@]}" CONFIRM_EXPERIMENTS=YES SUITE_WORKLOAD="$w" \
        TREE_DIR="$tree" EXPERIMENT_ARMS="$ARMS" SIZE_RATIOS="$T" \
        MAX_BYTES_FOR_LEVEL_BASE="$(( l1 * 1048576 ))" L0_COMPACTION_TRIGGER="$k" \
        WORKLOAD_SIZES_M="$SUITE_RUN_OPERATIONS_M" REPEATS="$REPEATS_EACH" \
        RESUME=1 SESSION_ID="suite-$w" RESULTS_ROOT="$NVME/suite-$w/$id" \
        DB_ROOT="$NVME/suite-dbs/$w/$id" "$PIPELINE_DIR/03_run_experiments.sh" ||
        status=$?
      (( status != 7 )) || fail "03 found no matching preflight; run 13 first"
      if (( status != 0 )); then
        stamp "$id: $w FAILED (exit $status); going on"
        failed+=("$id: $w")
        all_ran=0
      fi
    done
    if (( all_ran )) && ! keep_tree "$setting"; then
      # The tree's record and load logs stay; only its database goes.
      rm -rf "$tree/db"
      touch "$tree/DATABASE_REMOVED"
      stamp "$id: tree database removed (not in SUITE_KEEP_TREES)"
    fi
  done
done

for w in $SUITE_WORKLOADS; do
  [[ -d "$NVME/suite-$w" ]] || continue
  status=0
  "$PY" "$PIPELINE_DIR/04_generate_graphs.py" --results "$NVME/suite-$w" \
    --summary-only || status=$?
  if (( status == 3 )); then
    stamp "$w: 04 refused arms; see $NVME/suite-$w/graphs/refused_arms.json"
    failed+=("$w: refused arms")
  elif (( status != 0 )); then
    stamp "$w: 04 FAILED (exit $status)"
    failed+=("$w: 04")
  fi
done
if (( ${#failed[@]} )); then
  stamp "failed: ${failed[*]}"
  echo "SUITE_CHAIN_EXIT=5"
  exit 5
fi
stamp "done"
echo "SUITE_CHAIN_EXIT=0"
