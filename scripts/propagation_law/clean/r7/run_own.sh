#!/bin/bash
# Owner 2026-10-28: score the multi-change runs against the run's own after-state (no twin): SCORE=own, bar 90%.
# r5 (baseline) and the adopted r7 (d,p,c), multi (#2-#5b) and every level (#6, #10). Worker 3, from this folder.
# Last line OWN_RUN_EXIT=<failed steps>.
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1 SCORE=own R7_PARTS=d,p,c
fail=0
timeout 1800 python3 eval_r7_multi.py ~/clean/multi own_multi_rows.jsonl 8 > own_multi.log 2> own_multi.err || fail=$((fail+1))
R7SW=off timeout 1800 python3 eval_levels_r7.py ~/clean/multi own_r5_lv_rows.jsonl 8 > own_r5_levels.log 2> own_r5_levels.err || fail=$((fail+1))
R7SW=on timeout 1800 python3 eval_levels_r7.py ~/clean/multi own_r7_lv_rows.jsonl 8 > own_r7_levels.log 2> own_r7_levels.err || fail=$((fail+1))
python3 ../r6/compare_levels_r6.py own_r5_lv_rows.jsonl r7=own_r7_lv_rows.jsonl > own_lv_compare.log 2>&1 || fail=$((fail+1))
grep -q '^R7M_EXIT=0$' own_multi.log || fail=$((fail+1))
echo "OWN_RUN_EXIT=$fail"
