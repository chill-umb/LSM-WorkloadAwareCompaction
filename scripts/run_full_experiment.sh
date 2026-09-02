#!/usr/bin/env bash
# Full preregistered experiment suite: oracle bridge gate -> per-cell tuned
# baseline + SLO manifest -> balanced paired matrix at ten repeats -> formal
# acceptance -> read/write-heavy safety suites.
#
# Never builds RocksDB or db_bench; run 01_/02_ first.
#
#   ./run_full_experiment.sh
#
# TO RESUME after a crash, reboot or kill, pass the SAME root back:
#   SUITE_ROOT=results/suite-20260904-101500 \
#   ./run_full_experiment.sh
# Every stage runs with RESUME=1, so completed arms are skipped. Without
# SUITE_ROOT a fresh root is created and all work is repeated.
set -u

# Locate the project root by walking up to the pipeline marker, so this script
# works whether it sits at the repo root or under scripts/.
_dir="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PROJECT_ROOT=""
while [[ "$_dir" != "/" ]]; do
  if [[ -f "$_dir/scripts/dbbench_pipeline/config.sh" ]]; then PROJECT_ROOT="$_dir"; break; fi
  _dir="$(dirname "$_dir")"
done
[[ -n "$PROJECT_ROOT" ]] || {
  echo "Cannot find scripts/dbbench_pipeline/config.sh above $(readlink -f "$0")" >&2
  exit 1; }
cd "$PROJECT_ROOT"
P="scripts/dbbench_pipeline"
source "$P/config.sh"

SUITE_REPEATS="${SUITE_REPEATS:-10}"
SUITE_CELLS="${SUITE_CELLS:-10:2 20:2 10:6 20:6 10:10 20:10}"
# Note "-" not ":-": an explicitly empty SUITE_EXTRA_CELLS must disable the
# optional ladder rather than fall back to the default.
SUITE_EXTRA_CELLS="${SUITE_EXTRA_CELLS-30:2 30:6 30:10}"
SUITE_STRESS_SIZE="${SUITE_STRESS_SIZE:-10}"
SUITE_SEED="${SUITE_SEED:-40001}"
# db_bench writes ~1 KB records; a 30M arm is ~30 GB and 03 KEEPS the database
# when the learner gate fails (03_run_experiments.sh:647), so failures
# accumulate. Refuse to start a cell that could fill the device.
SUITE_MIN_FREE_GB="${SUITE_MIN_FREE_GB:-120}"

if [[ "$PYTHON_VENV" = /* ]]; then PY="$PYTHON_VENV/bin/python"
else PY="$PROJECT_ROOT/$PYTHON_VENV/bin/python"; fi
if [[ "$DBBENCH_BUILD_DIR" = /* ]]; then BENCH="$DBBENCH_BUILD_DIR/db_bench"
else BENCH="$PROJECT_ROOT/$DBBENCH_BUILD_DIR/db_bench"; fi
[[ -x "$PY"    ]] || { echo "Missing venv python: $PY (run 00_)" >&2; exit 1; }
[[ -x "$BENCH" ]] || { echo "Missing db_bench: $BENCH (run 01_ and 02_)" >&2; exit 1; }
for src in lib/rocksdb/db/compaction/compaction_picker_rl.cc rl_agent/agent.py; do
  [[ "$BENCH" -nt "$src" ]] || { echo "db_bench is older than $src - rebuild" >&2; exit 1; }
done

for spec in $SUITE_CELLS $SUITE_EXTRA_CELLS; do
  [[ "$spec" =~ ^[0-9]+:[0-9]+$ ]] || {
    echo "Bad cell spec '$spec'; want size:ratio (e.g. 10:2)" >&2; exit 1; }
done
[[ "$SUITE_REPEATS" =~ ^[1-9][0-9]*$ ]] || { echo "SUITE_REPEATS must be > 0" >&2; exit 1; }

SR="${SUITE_ROOT:-$PROJECT_ROOT/results/suite-$(date +%Y%m%d-%H%M%S)}"
[[ "$SR" = /* ]] || SR="$PROJECT_ROOT/$SR"
RESUMING=0; [[ -d "$SR" ]] && RESUMING=1
MANI="$SR/manifests/final"; SEL="$SR/manifests/selection"
mkdir -p "$SR" "$MANI" "$SEL" || exit 1
S="$SR/STAGES.txt"; touch "$S"

log(){ echo "[$(date -u +'%m-%d %H:%M:%S')] $*" | tee -a "$SR/driver.log"; }

stage(){  # name, logfile, cmd...
  local n="$1" l="$2"; shift 2
  log "START $n"
  ( "$@" ) >> "$SR/$l" 2>&1
  local rc=$?
  printf '%-26s exit=%-4s %s\n' "$n" "$rc" "$l" >> "$S"
  log "END   $n exit=$rc"
  return $rc
}

# 03_run_experiments.sh:173 refuses to start while any stray db_bench or RL
# server is alive. A timed-out or crashed stage leaves exactly that, which
# would cascade into every later cell aborting. Reap between cells only, when
# nothing of ours should legitimately be running.
reap_orphans(){
  local found=0
  for pat in "$BENCH" "$PROJECT_ROOT/rl_agent/server.py"; do
    local pids; pids="$(pgrep -u "$(id -u)" -f -- "$pat" 2>/dev/null || true)"
    [[ -z "$pids" ]] && continue
    found=1; log "reaping stray: $pat -> $pids"
    kill -TERM $pids 2>/dev/null; sleep 10
    kill -KILL $pids 2>/dev/null || true
  done
  (( found )) && sleep 5
  return 0
}

free_gb(){ df -BG --output=avail "$1" 2>/dev/null | tail -1 | tr -dc '0-9'; }
disk_ok(){
  local avail; avail="$(free_gb "$SR")"
  [[ -z "$avail" ]] && return 0
  (( avail >= SUITE_MIN_FREE_GB )) && return 0
  log "SKIP $1 - only ${avail}GB free, need ${SUITE_MIN_FREE_GB}GB"
  log "   (03 preserves databases when the learner gate fails; prune $SR/db)"
  return 1
}

{ echo "root HEAD:    $(git rev-parse HEAD 2>/dev/null)"
  echo "rocksdb HEAD: $(git -C lib/rocksdb rev-parse HEAD 2>/dev/null)"
  echo "resuming:     $RESUMING  root=$SR"
  echo "repeats:      $SUITE_REPEATS"
  echo "cells:        $SUITE_CELLS"
  echo "extra cells:  $SUITE_EXTRA_CELLS"
  git diff --stat; git -C lib/rocksdb diff --stat; } >> "$SR/provenance.log" 2>&1
log "suite root $SR (resuming=$RESUMING)"

# --------------------------------------------------------------- one cell ---
cell(){
  local sz="$1" T="$2" tag="${1}M-T${2}"
  disk_ok "$tag" || return 1
  reap_orphans
  local manifest="$MANI/$WORKLOAD_PROFILE/${sz}M/T${T}/baseline_slo.json"

  if [[ ! -f "$manifest" ]]; then
    stage "sweep-$tag" "sweep-$tag.log" env \
      WORKLOAD_PROFILE="$WORKLOAD_PROFILE" WORKLOAD_SIZES_M="$sz" SIZE_RATIOS="$T" \
      RL_RUN_PHASE=experiment BASELINE_REPEATS=3 \
      BASELINE_L0_COMPACTION_TRIGGERS="4 8" BASELINE_L0_SLOWDOWN_TRIGGERS=20 \
      BASELINE_L0_STOP_TRIGGERS=36 BASELINE_COMPACTION_PRIORITIES=3 \
      BASELINE_RESULTS_ROOT="$SR/baseline/$tag" BASELINE_DB_ROOT="$SR/db/baseline/$tag" \
      RESUME=1 KEEP_DATABASES=0 CONFIRM_BASELINE_SWEEP=YES \
      bash "$P/05_run_baseline_sweep.sh" || { reap_orphans; return 2; }

    stage "select-$tag" "select-$tag.log" "$PY" "$P/06_select_baseline_slo.py" \
      --baseline-results "$SR/baseline/$tag" --workload-profile "$WORKLOAD_PROFILE" \
      --size-millions "$sz" --size-ratio "$T" --minimum-repeats 3 \
      --output "$SEL/$WORKLOAD_PROFILE/${sz}M/T${T}/baseline_slo.json" || return 3

    # The guard HOLDOUT is expected to fail (known 3.8-6.0% predicted override
    # against a 1% limit). 06_calibrate_live_guard.py writes the final manifest
    # before the holdout runs, so check for the file, not the exit code.
    stage "guard-$tag" "guard-$tag.log" env \
      WORKLOAD_PROFILE="$WORKLOAD_PROFILE" WORKLOAD_SIZES_M="$sz" SIZE_RATIOS="$T" \
      SELECTION_SLO_ROOT="$SEL" FINAL_SLO_ROOT="$MANI" \
      GUARD_RESULTS_ROOT="$SR/guard/$tag" GUARD_DB_ROOT="$SR/db/guard/$tag" \
      CALIBRATION_SEED_BASE=1001 HOLDOUT_SEED_BASE=11001 \
      RESUME=1 KEEP_DATABASES=0 CONFIRM_GUARD_PROTOCOL=YES \
      bash "$P/06_run_guard_protocol.sh"
    reap_orphans
    [[ -f "$manifest" ]] || { log "ABORT cell $tag: no final manifest"; return 4; }
  fi

  stage "matrix-$tag" "matrix-$tag.log" env \
    WORKLOAD_SIZES_M="$sz" SIZE_RATIOS="$T" \
    EXPERIMENT_ARMS="regular prior_only unconstrained_rl rl" \
    REPEATS="$SUITE_REPEATS" RL_RUN_PHASE=experiment \
    BASELINE_SLO_DIR="$MANI" DBBENCH_SEED="$SUITE_SEED" \
    RESULTS_ROOT="$SR/experiment" DB_ROOT="$SR/db/experiment" \
    RESUME=1 KEEP_DATABASES=0 CONFIRM_EXPERIMENTS=YES \
    bash "$P/03_run_experiments.sh"
  local mrc=$?
  reap_orphans
  (( mrc == 0 )) || { log "cell $tag matrix exit=$mrc; evaluating what completed"; }

  stage "graphs-$tag" "graphs-$tag.log" \
    bash "$P/04_generate_graphs.sh" --results "$SR/experiment" || return 6
  stage "paired-$tag" "paired-$tag.log" "$PY" "$P/07_evaluate_paired.py" \
    "$SR/experiment/graphs/summary.csv" --size-millions "$sz" --size-ratio "$T" \
    --minimum-pairs "$SUITE_REPEATS" --scan-objective sorted_run_seeks \
    --output "$SR/acceptance-$tag.json"
  stage "learn-$tag" "learn-$tag.log" "$PY" "$P/11_analyze_learning.py" \
    "$SR/experiment" --arm rl --stride 10 --output "$SR/learning-rl-$tag.json"
  return 0
}

# ------------------------------------- 1: oracle bridge gate (1M/T2, cheap) ---
if [[ ! -f "$SR/oracle-parity.json" ]]; then
  reap_orphans
  stage oracle-run oracle-run.log env \
    WORKLOAD_PROFILE="$WORKLOAD_PROFILE" WORKLOAD_SIZES_M=1 SIZE_RATIOS=2 \
    EXPERIMENT_ARMS="regular oracle" REPEATS=10 RL_RUN_PHASE=experiment \
    RESULTS_ROOT="$SR/oracle" DB_ROOT="$SR/db/oracle" \
    RESUME=1 KEEP_DATABASES=0 CONFIRM_EXPERIMENTS=YES \
    bash "$P/03_run_experiments.sh"
  reap_orphans
  stage oracle-graphs oracle-graphs.log \
    bash "$P/04_generate_graphs.sh" --results "$SR/oracle"
  stage oracle-gate oracle-gate.log "$PY" "$P/09_evaluate_oracle_parity.py" \
    "$SR/oracle/graphs/summary.csv" --size-millions 1 --size-ratio 2 \
    --minimum-pairs 10 --minimum-envelope-pairs 10 \
    --admission-latency-limit-micros 5000 --output "$SR/oracle-parity.json"
  grc=$?
  # 0 = passed, 2 = undecided with no failed checks. Anything else means the
  # bridge is not transparent, and days of matrix behind it are uninterpretable.
  if (( grc != 0 && grc != 2 )); then
    log "ABORT: oracle parity gate exit=$grc - inspect $SR/oracle-parity.json"
    exit 10
  fi
else
  log "oracle gate already present; skipping"
fi

# --------------------------------------- 2: balanced matrix, priority order ---
for spec in $SUITE_CELLS; do
  IFS=: read -r sz T <<< "$spec"; cell "$sz" "$T"
done

# -------------------------------------------------- 3: safety stress suites ---
if disk_ok "stress suites"; then
  reap_orphans
  stage stress stress.log env \
    WORKLOAD_SIZES_M="$SUITE_STRESS_SIZE" SIZE_RATIOS=2 \
    STRESS_ROOT="$SR/stress" STRESS_DB_ROOT="$SR/db/stress" \
    STRESS_SLO_ROOT="$SR/stress-manifests" \
    STRESS_SELECTION_SLO_ROOT="$SR/stress-selection" \
    STRESS_BASELINE_REPEATS=3 STRESS_FINAL_REPEATS="$SUITE_REPEATS" \
    RESUME=1 KEEP_DATABASES=0 CONFIRM_STRESS_SUITES=YES \
    bash "$P/08_run_stress_suites.sh"
  reap_orphans
fi

# ------------------------------------------- 4: optional deeper size ladder ---
for spec in $SUITE_EXTRA_CELLS; do
  IFS=: read -r sz T <<< "$spec"; cell "$sz" "$T"
done

# ------------------------------------------------------------- presentation ---
if [[ -f "$SR/experiment/graphs/summary.csv" ]]; then
  stage figures figures.log "$PY" "$P/12_report_figures.py" \
    "$SR/experiment/graphs/summary.csv" --outdir "$SR/figures"
fi

reap_orphans
log "SUITE DONE - $(free_gb "$SR")GB free"
echo; cat "$S"
echo; echo "Results:     $SR"
echo "Acceptance:  $SR/acceptance-*.json"
echo "Oracle gate: $SR/oracle-parity.json"
echo "Resume with: SUITE_ROOT=$SR $0"
