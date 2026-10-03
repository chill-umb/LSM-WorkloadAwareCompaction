#!/usr/bin/env bash
# The first node session (README "Node runbook", steps c and d.1-d.3), unattended:
#   1. the preflight (13), with PARITY_PAIRS=10 unless set;
#   2. per workload, Gate N1: three pilot native arms per T (PREREGISTRATION
#      D-16), 04, and the admission test (19) for T = 2, 6 and 10;
#   3. per workload, the q-bar arms at the run length Gate N1 picked (D-14 §2);
#   4. one session of the device prices (18, D-15 §3, D-22 c) on 29's
#      archived trees, which needs both workloads' q-bar arms; only when
#      PRICE_TREES names the archive (with PRICE_TREES_SHA256, its identity;
#      PRICE_SESSION names the session, n1 or qbar-<tag> by default). The
#      prices are final only after a second session and 18's compare (D-22 f).
# Each workload's chain runs as its own process, so a failure in one does not
# stop the other. The checks at the start fail in seconds, while someone is
# watching.
#   NVME=/mnt/nvme scripts/dbbench_pipeline/24_gate_n1_chain.sh
#   STOP_AFTER_N1=1   ends each workload after its admission test
#   RESUME=1          after a failed night: 03 skips finished arms (delete a
#                     failed arm's result and database folders first)
#   ALLOW_ROOT_DISK=1 when NVME is meant to be on the root filesystem
#   QBAR_ONLY=1 QBAR_TAG=<tag>
#                     after a binary change (PREREGISTRATION D-21): reuse Gate
#                     N1's reports in n1-<workload>, run only the preflight,
#                     the q-bar arms (into qbar-<workload>-<tag>, so no earlier
#                     result is touched) and the prices. q-bar itself stays as
#                     recorded; the arms' mean is printed beside it
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
NIGHT_PARITY_PAIRS="${PARITY_PAIRS:-10}"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"
STAGE=24
# shellcheck source=chain_common.sh
source "$PIPELINE_DIR/chain_common.sh"

# D-16 §6: a workload's run length is the longest of its three cells' rungs.
rung() { "$PY" "$PIPELINE_DIR/gate_n1_reports.py" "$NVME/n1-$1"; }

QBAR_ONLY="${QBAR_ONLY:-0}"
QBAR_SUFFIX=""
if [[ "$QBAR_ONLY" == 1 ]]; then
  [[ "${QBAR_TAG:-}" =~ ^[A-Za-z0-9_]+$ ]] ||
    fail "QBAR_ONLY=1 needs QBAR_TAG (letters, digits, _), naming the new q-bar folders"
  QBAR_SUFFIX="-$QBAR_TAG"
elif [[ "$QBAR_ONLY" != 0 ]]; then
  fail "QBAR_ONLY must be 0 or 1"
fi

# One workload: Gate N1, then its q-bar arms. Run as a child process, so that
# set -e holds inside it and its failure is reported, not fatal to the other.
chain() {
  local w="$1" extra=() pids=() failed=0 T chosen size load
  [[ "$w" == powerlaw ]] && extra=("${POWERLAW[@]}")
  if [[ "$QBAR_ONLY" == 1 ]]; then
    qbar_arms "$w"
    return 0
  fi
  stamp "$w: Gate N1 pilots, native, T = 2/6/10, 3 repeats, 29M at 10% load"
  env ${extra[@]+"${extra[@]}"} EXPERIMENT_ARMS=native SIZE_RATIOS="2 6 10" \
    WORKLOAD_SIZES_M=29 LOAD_PERCENT=10 REPEATS=3 SESSION_ID="gate-n1-$w" \
    RESULTS_ROOT="$NVME/n1-$w" DB_ROOT="$NVME/n1-dbs/$w" CONFIRM_EXPERIMENTS=YES \
    "$PIPELINE_DIR/03_run_experiments.sh"
  "$PY" "$PIPELINE_DIR/04_generate_graphs.py" --results "$NVME/n1-$w" --summary-only

  stamp "$w: admission test (19), T = 2, 6 and 10 in parallel"
  for T in 2 6 10; do
    "$PY" "$PIPELINE_DIR/19_admission_test.py" "$NVME/n1-$w"/29M/T$T/repeat-*/native \
      --config config/admission_test.json \
      --output "$NVME/n1-$w/admission_T$T.json" \
      > "$NVME/n1-$w/admission_T$T.log" 2>&1 &
    pids+=($!)
  done
  for T in "${pids[@]}"; do wait "$T" || failed=1; done
  grep -H "pool:" "$NVME/n1-$w"/admission_T*.log || true
  if (( failed )); then
    tail -n 3 "$NVME/n1-$w"/admission_T*.log >&2 || true
    fail "$w: an admission run failed; see $NVME/n1-$w/admission_T*.log"
  fi
  [[ "${STOP_AFTER_N1:-0}" == 1 ]] && return 0
  qbar_arms "$w"
}

# The q-bar arms at the rung Gate N1 chose (D-14 §2), five native at T=10.
qbar_arms() {
  local w="$1" extra=() chosen size load
  [[ "$w" == powerlaw ]] && extra=("${POWERLAW[@]}")
  chosen="$(rung "$w")"
  read -r size load <<< "$chosen"
  stamp "$w: q-bar arms, five native at T=10, ${size}M at ${load}% load"
  env ${extra[@]+"${extra[@]}"} EXPERIMENT_ARMS=native SIZE_RATIOS=10 \
    WORKLOAD_SIZES_M="$size" LOAD_PERCENT="$load" REPEATS=5 \
    SESSION_ID="qbar-$w$QBAR_SUFFIX" RESULTS_ROOT="$NVME/qbar-$w$QBAR_SUFFIX" \
    DB_ROOT="$NVME/qbar-dbs$QBAR_SUFFIX/$w" CONFIRM_EXPERIMENTS=YES \
    "$PIPELINE_DIR/03_run_experiments.sh"
  "$PY" "$PIPELINE_DIR/04_generate_graphs.py" --results "$NVME/qbar-$w$QBAR_SUFFIX" \
    --summary-only
  PYTHONPATH="$PIPELINE_DIR" "$PY" - "$NVME/qbar-$w$QBAR_SUFFIX/graphs/summary.csv" \
    "$w" "$QBAR_ONLY" <<'PY'
import csv, statistics, sys
import research_objective
rates = [float(r["throughput_ops_per_second"]) for r in csv.DictReader(open(sys.argv[1]))]
mean = statistics.fmean(rates)
if sys.argv[3] == "1":
    family = {"assoc": "assoc", "powerlaw": "powerlaw_get95"}[sys.argv[2]]
    recorded = research_objective.reference_rate(
        research_objective.load_contract()[0], family)
    print(f"=== {sys.argv[2]}: these q-bar arms' mean {mean:.1f} ops/s "
          f"({len(rates)} runs); q-bar stays as recorded, {recorded:.1f} "
          f"({(mean / recorded - 1) * 100:+.2f}%; D-21)")
else:
    print(f"=== {sys.argv[2]}: q-bar candidate {mean:.1f} ops/s "
          f"(mean of {len(rates)} runs; record it by a dated amendment, D-14 §2)")
PY
}

if [[ "${1:-}" == workload ]]; then
  chain "$2"
  exit 0
fi

# D-22: a price session only on 29's archive, checked now, in seconds.
PRICE_SESSION="${PRICE_SESSION:-$([[ "$QBAR_ONLY" == 1 ]] && echo "qbar$QBAR_SUFFIX" || echo n1)}"
if [[ -n "${PRICE_TREES:-}" ]]; then
  [[ "$PRICE_SESSION" =~ ^[A-Za-z0-9_-]+$ ]] ||
    fail "PRICE_SESSION=$PRICE_SESSION: letters, digits, _ and - only"
  [[ -f "$PRICE_TREES/MANIFEST.sha256" && -f "$PRICE_TREES/build_record.json" ]] ||
    fail "PRICE_TREES=$PRICE_TREES is not 29's archive"
  [[ "$("$PY" "$PIPELINE_DIR/price_trees.py" identity --archive-dir "$PRICE_TREES")" \
     == "${PRICE_TREES_SHA256:-}" ]] ||
    fail "PRICE_TREES_SHA256 is not the archive's tree-set identity (D-22 i)"
  [[ "$("$PY" -c 'import json, sys
print(json.load(open(sys.argv[1]))["tree_set_sha256"])' "$PRICE_TREES/build_record.json")" \
     == "$PRICE_TREES_SHA256" ]] ||
    fail "build_record.json names another tree set than PRICE_TREES_SHA256 (D-22 i)"
  [[ ! -e "$PRICE_SESSIONS_DIR/$PRICE_SESSION" ]] ||
    fail "price session $PRICE_SESSION exists in $PRICE_SESSIONS_DIR; set PRICE_SESSION"
  [[ ! -e "$NVME/prices-db$QBAR_SUFFIX/price-trees/$PRICE_SESSION" ]] ||
    fail "$NVME/prices-db$QBAR_SUFFIX/price-trees/$PRICE_SESSION exists; set PRICE_SESSION"
fi
if [[ "$QBAR_ONLY" == 1 ]]; then
  node_checks "qbar-assoc$QBAR_SUFFIX" "qbar-powerlaw$QBAR_SUFFIX" "qbar-dbs$QBAR_SUFFIX"
  for w in assoc powerlaw; do
    rung "$w" >/dev/null || fail "QBAR_ONLY=1: no Gate N1 run length in $NVME/n1-$w"
  done
else
  node_checks n1-assoc n1-powerlaw qbar-assoc qbar-powerlaw n1-dbs qbar-dbs
fi

stamp "preflight (13): builds, tiers 1 and 2, ACT-1, ACT-4 at $NIGHT_PARITY_PAIRS pairs, evaluator smoke"
CONFIRM_PREFLIGHT_VERIFICATION=YES PARITY_PAIRS="$NIGHT_PARITY_PAIRS" \
  DB_ROOT="$NVME/preflight-dbs" "$PIPELINE_DIR/13_run_preflight_verification.sh"

status=0
for w in assoc powerlaw; do
  # By absolute path: the working folder is now the repo root.
  "$BASH" "$PIPELINE_DIR/24_gate_n1_chain.sh" workload "$w" ||
    { stamp "$w FAILED; the other workload goes on"; status=1; }
done
(( status == 0 )) || fail "stopping before the prices: a workload's chain failed"
if [[ "${STOP_AFTER_N1:-0}" == 1 ]]; then
  stamp "stopped after Gate N1"
  exit 0
fi

if [[ -z "${PRICE_TREES:-}" ]]; then
  stamp "no price session: PRICE_TREES is not set (D-22: 29 archives the" \
        "trees, then 18 runs sessions A and B, then compare)"
  stamp "done"
  exit 0
fi
stamp "device prices, session $PRICE_SESSION (18, D-15 §3, D-20, D-21, D-22)"
CONFIRM_PRICE_CALIBRATION=YES DB_ROOT="$NVME/prices-db$QBAR_SUFFIX" \
  PRICE_SESSION="$PRICE_SESSION" PRICE_TREES="$PRICE_TREES" \
  PRICE_TREES_SHA256="$PRICE_TREES_SHA256" \
  "$PIPELINE_DIR/18_calibrate_prices.sh" \
  "$NVME/qbar-assoc$QBAR_SUFFIX/graphs/summary.csv" \
  "$NVME/qbar-powerlaw$QBAR_SUFFIX/graphs/summary.csv"
stamp "done"
