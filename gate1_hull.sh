#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"

PY=.venv-dbbench/bin/python
say(){ printf '\n=== %s  %s\n' "$(date -Is)" "$*"; }

extract(){
  for T in 2 6 10; do
    "$PY" scripts/dbbench_pipeline/frontier_analysis.py results/baseline_sweep \
      --size-millions 10 --size-ratio "$T" --output "gate1/hull-T$T.json"
  done
}

say "re-score oracle parity with the suite's flags"
# stage 09 exits 2 for "undecided", which is a pass; anything else is fatal
rc=0
scripts/dbbench_pipeline/09_evaluate_oracle_parity.py \
  results/oracle-parity-assoc-2/graphs/summary.csv \
  --size-millions 1 --size-ratio 2 \
  --minimum-pairs 10 --minimum-envelope-pairs 10 \
  --admission-latency-limit-micros 5000 \
  --output results/oracle-parity-assoc-2/oracle_parity.json || rc=$?
[[ $rc -eq 0 || $rc -eq 2 ]] || exit $rc

say "Hull-0 sweep: 36 cells x 3 repeats at 10M"
CONFIRM_BASELINE_SWEEP=YES scripts/dbbench_pipeline/05_run_baseline_sweep.sh

mkdir -p gate1
say "extract per-T hulls"
extract

say "C-1 checkpoint: >= 4 of 12 on the hull, per T"
for T in 2 6 10; do
  n=$("$PY" -c "import json;print(len(json.load(open('gate1/hull-T$T.json'))['empirical_hull']))")
  echo "T=$T hull points: $n"
  [[ "$n" -ge 4 ]] || { echo "C-1 FAILS at T=$T - stopping"; exit 1; }
done

say "top-up to the C-2 repeat floor"
HULL_DIR=gate1 scripts/dbbench_pipeline/15_top_up_hull.sh --dry-run
CONFIRM_TOP_UP=YES HULL_DIR=gate1 scripts/dbbench_pipeline/15_top_up_hull.sh

say "re-extract hulls after top-up"
extract

say "cross-T regular cells, T=14 and T=20, trigger 4 / scale 1x"
CONFIRM_BASELINE_SWEEP=YES SIZE_RATIOS="14 20" \
  BASELINE_L0_COMPACTION_TRIGGERS=4 BASELINE_LEVEL_BASE_SCALES=1 \
  scripts/dbbench_pipeline/05_run_baseline_sweep.sh

"$PY" scripts/dbbench_pipeline/frontier_analysis.py results/baseline_sweep \
  --size-millions 10 --size-ratio 2 6 10 14 20 --output gate1/hull-crossT.json

say "done through step 6. Step 7 (capacity calibration) needs the STATIC_CAPACITY_SCALES vectors decided."