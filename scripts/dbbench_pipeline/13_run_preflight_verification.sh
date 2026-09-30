#!/usr/bin/env bash
# Tier 3, the preflight (plan §6.4; CLAUDE.md "Tests"). Runs the checks in
# order and writes the marker PREFLIGHT_PASSED, bound to the db_bench, plugin
# and code hashes, which 03_run_experiments.sh requires before any long run.
#
#   1 rebuild the Release db_bench (and the plugin)   4 parity, ACT-4 and ARCH-5
#   2 tier 1 and tier 2 suites                        5 rules-mode smoke
#   3 ACT-1 on the real binary                        6 learner smoke
#
# A step whose component does not exist yet is skipped and recorded as skipped;
# a run whose arms need that step is then refused by 03. A step whose component
# exists but whose check is not wired in here fails, so nothing passes by
# omission. Wire each step in the same change that builds its component.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

if [[ "${CONFIRM_PREFLIGHT_VERIFICATION:-}" != "YES" ]]; then
  echo "This rebuilds db_bench, builds the Debug test tree and runs the" >&2
  echo "preflight; the marker goes to $PREFLIGHT_MARKER." >&2
  echo "Set CONFIRM_PREFLIGHT_VERIFICATION=YES to start." >&2
  exit 2
fi

if [[ "$PYTHON_VENV" = /* ]]; then
  PYTHON="$PYTHON_VENV/bin/python"
else
  PYTHON="$PROJECT_ROOT/$PYTHON_VENV/bin/python"
fi
[[ -x "$PYTHON" ]] || { echo "Missing $PYTHON; run step 00 first." >&2; exit 1; }
DB_BENCH="$DBBENCH_BUILD_DIR/db_bench"

passed=()
skipped=()
pass() { echo "[step $1] PASS"; passed+=("$1"); }
skip() { echo "[step $1] SKIP: $2"; skipped+=("$1=$2"); }
unwired() {
  echo "[step $1] FAIL: $2 exists, but this step is not wired into 13 yet" \
       "(plan §6.4)." >&2
  exit 1
}

# A stale marker must not outlive a failed preflight.
rm -f "$PREFLIGHT_MARKER"

echo "=== step 1: rebuild the Release db_bench ==="
if [[ -d controller ]]; then unwired 1 "controller/ (the plugin build)"; fi
"$PIPELINE_DIR/01_build_rocksdb.sh"
"$PIPELINE_DIR/02_build_db_bench.sh"
echo "db_bench sha256: $(sha256sum "$DB_BENCH" | awk '{print $1}')"
pass 1

echo "=== step 2: tier 1 and tier 2 suites ==="
"$PIPELINE_DIR/run_python_tests.sh"
"$PIPELINE_DIR/01b_build_test_trees.sh"
pass 2

echo "=== step 3: ACT-1 on the real binary ==="
"$PYTHON" "$PIPELINE_DIR/20_check_actuation.py" --db-bench "$DB_BENCH" \
  --work-dir "$PREFLIGHT_WORK_DIR/act1" \
  --output "$PREFLIGHT_WORK_DIR/act1_report.json"
pass 3

echo "=== step 4: parity at 1M, T=2 ==="
# ACT-4: the patched binary at m = 1 against stock RocksDB. ARCH-5 (the
# plugin in hold-only mode against native) joins here with the plugin.
if [[ -d controller ]]; then unwired 4 "controller/ (ARCH-5 parity)"; fi
"$PIPELINE_DIR/01c_build_stock_db_bench.sh"
parity=0
"$PIPELINE_DIR/22_check_native_parity.sh" || parity=$?
if (( parity == 2 )); then
  echo "[step 4] FAIL: ACT-4 undecided; the paired spread needs more pairs" \
       "than PARITY_PAIRS=$PARITY_PAIRS (see required_pairs in" \
       "$PREFLIGHT_WORK_DIR/act4/act4_report.json)." >&2
fi
(( parity == 0 )) || exit 1
pass 4

echo "=== steps 5 and 6: rules-mode and learner smoke ==="
if [[ -d controller ]]; then unwired 5 "controller/"; fi
skip 5 "no controller/ plugin (plan §3)"
skip 6 "no controller/ plugin (plan §3)"

echo "=== step 7: write the marker ==="
"$PYTHON" "$PIPELINE_DIR/preflight_marker.py" write \
  --marker "$PREFLIGHT_MARKER" --db-bench "$DB_BENCH" \
  --passed "${passed[@]}" --skipped "${skipped[@]}"
echo "passed: ${passed[*]}"
echo "skipped: ${skipped[*]:-none}"
echo "03 accepts a long run only when every step its arms need passed:"
echo "static arms need 1-4, rules also 5, learned arms 1-6."
