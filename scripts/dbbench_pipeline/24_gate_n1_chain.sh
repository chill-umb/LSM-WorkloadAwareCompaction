#!/usr/bin/env bash
# The first node session (README "Node runbook", steps c and d.1-d.3), unattended:
#   1. the preflight (13), with PARITY_PAIRS=10 unless set;
#   2. per workload, Gate N1: three pilot native arms per T (PREREGISTRATION
#      D-16), 04, and the admission test (19) for T = 2, 6 and 10;
#   3. per workload, the q-bar arms at the run length Gate N1 picked (D-14 §2);
#   4. the device prices (18, D-15 §3), which need both workloads' q-bar arms.
# Each workload's chain runs as its own process, so a failure in one does not
# stop the other. The checks at the start fail in seconds, while someone is
# watching.
#   NVME=/mnt/nvme scripts/dbbench_pipeline/24_gate_n1_chain.sh
#   STOP_AFTER_N1=1   ends each workload after its admission test
#   RESUME=1          after a failed night: 03 skips finished arms
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
NIGHT_PARITY_PAIRS="${PARITY_PAIRS:-10}"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"
NVME="${NVME:-/mnt/nvme}"
if [[ "$PYTHON_VENV" = /* ]]; then
  PY="$PYTHON_VENV/bin/python"
else
  PY="$PROJECT_ROOT/$PYTHON_VENV/bin/python"
fi
POWERLAW=(WORKLOAD_SKEW=2 MIX_GET_RATIO=0.95 MIX_PUT_RATIO=0.05 MIX_SEEK_RATIO=0
          WORKLOAD_PROFILE=powerlaw-get95-v1)
stamp() { echo "=== $(date '+%F %T') $*"; }
fail() { echo "[24] $*" >&2; exit 1; }

# D-16 §6: a workload's run length is the longest of its three cells' rungs.
rung() {
  "$PY" - "$NVME/n1-$1" <<'PY'
import json, sys
from pathlib import Path
rungs = []
for t in (2, 6, 10):
    report = json.loads((Path(sys.argv[1]) / f"admission_T{t}.json").read_text())
    length = report["run_length"]
    if length["rung"] is None:
        sys.exit(f"T={t}: no rung is long enough for L{length['level']} "
                 "(D-16 §6): stop and report")
    rungs.append(length["rung"])
def mixgraph(r):
    total = r["size_millions"] * 10**6
    return total - total * r["load_percent"] // 100
best = max(rungs, key=mixgraph)
print(best["size_millions"], best["load_percent"])
PY
}

# One workload: Gate N1, then its q-bar arms. Run as a child process, so that
# set -e holds inside it and its failure is reported, not fatal to the other.
chain() {
  local w="$1" extra=() pids=() failed=0 T chosen size load
  [[ "$w" == powerlaw ]] && extra=("${POWERLAW[@]}")
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

  chosen="$(rung "$w")"
  read -r size load <<< "$chosen"
  stamp "$w: q-bar arms, five native at T=10, ${size}M at ${load}% load"
  env ${extra[@]+"${extra[@]}"} EXPERIMENT_ARMS=native SIZE_RATIOS=10 \
    WORKLOAD_SIZES_M="$size" LOAD_PERCENT="$load" REPEATS=5 \
    SESSION_ID="qbar-$w" RESULTS_ROOT="$NVME/qbar-$w" \
    DB_ROOT="$NVME/qbar-dbs/$w" CONFIRM_EXPERIMENTS=YES \
    "$PIPELINE_DIR/03_run_experiments.sh"
  "$PY" "$PIPELINE_DIR/04_generate_graphs.py" --results "$NVME/qbar-$w" --summary-only
  "$PY" - "$NVME/qbar-$w/graphs/summary.csv" "$w" <<'PY'
import csv, statistics, sys
rates = [float(r["throughput_ops_per_second"]) for r in csv.DictReader(open(sys.argv[1]))]
print(f"=== {sys.argv[2]}: q-bar candidate {statistics.fmean(rates):.1f} ops/s "
      f"(mean of {len(rates)} runs; record it by a dated amendment, D-14 §2)")
PY
}

if [[ "${1:-}" == workload ]]; then
  chain "$2"
  exit 0
fi

# Checks that fail in seconds.
[[ -x "$PY" ]] || fail "no Python at $PY; run 00_install_dependencies.sh"
mkdir -p "$NVME" && touch "$NVME/.write_test" && rm -f "$NVME/.write_test" ||
  fail "$NVME is not writable"
[[ "$(stat -f -c %T "$NVME")" != tmpfs ]] || fail "$NVME is tmpfs; use the NVMe device"
free_gb="$(df --output=avail -BG "$NVME" | tail -n 1 | tr -dc 0-9)"
(( free_gb >= ${MIN_FREE_GB:-60} )) ||
  fail "only ${free_gb} GB free on $NVME; about ${MIN_FREE_GB:-60} GB are needed"
if [[ "${RESUME:-0}" != 1 ]]; then
  for d in n1-assoc n1-powerlaw qbar-assoc qbar-powerlaw; do
    [[ ! -e "$NVME/$d" ]] || fail "$NVME/$d exists: move it away, or rerun with RESUME=1"
  done
fi
recorded="$(git ls-tree HEAD lib/rocksdb 2>/dev/null | awk '{print $3}' || true)"
if [[ -n "$recorded" &&
      "$(git -C lib/rocksdb rev-parse HEAD 2>/dev/null)" != "$recorded" ]]; then
  fail "lib/rocksdb is not at the recorded commit $recorded; run git submodule update --init --recursive"
fi
if [[ -n "$DBBENCH_CPUS" ]] && ! command -v taskset >/dev/null; then
  fail "taskset is not installed"
fi

stamp "preflight (13): builds, tiers 1 and 2, ACT-1, ACT-4 at $NIGHT_PARITY_PAIRS pairs, evaluator smoke"
CONFIRM_PREFLIGHT_VERIFICATION=YES PARITY_PAIRS="$NIGHT_PARITY_PAIRS" \
  DB_ROOT="$NVME/preflight-dbs" "$PIPELINE_DIR/13_run_preflight_verification.sh"

status=0
for w in assoc powerlaw; do
  "$BASH" "${BASH_SOURCE[0]}" workload "$w" ||
    { stamp "$w FAILED; the other workload goes on"; status=1; }
done
(( status == 0 )) || fail "stopping before the prices: a workload's chain failed"
if [[ "${STOP_AFTER_N1:-0}" == 1 ]]; then
  stamp "stopped after Gate N1"
  exit 0
fi

stamp "device prices (18, D-15 §3)"
CONFIRM_PRICE_CALIBRATION=YES DB_ROOT="$NVME/prices-db" \
  "$PIPELINE_DIR/18_calibrate_prices.sh" \
  "$NVME/qbar-assoc/graphs/summary.csv" "$NVME/qbar-powerlaw/graphs/summary.csv"
stamp "done"
