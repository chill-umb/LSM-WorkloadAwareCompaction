#!/bin/bash
# r7 parts w and s (operator, 2026-10-28; owner chose calm #4 next). w: an L0 merge's written bytes include the copies
# it drops (forward_r7.merge_written); s: calm #4 against the run's own measured before-stretch (run's-own measure).
# Multi (SCORE=own): d,p,c,o,w,s and d,p,c,o,w; levels for d,p,c,o,w,s; single (D-33): d,p,c,w. Worker 3.
# Last line WS_RUN_EXIT=<failed steps>.
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1
fail=0
SCORE=own R7_PARTS=d,p,c,o,w,s timeout 1800 python3 eval_r7_multi.py ~/clean/multi ws_multi_rows.jsonl 8 > ws_multi.log 2> ws_multi.err || fail=$((fail+1))
SCORE=own R7_PARTS=d,p,c,o,w timeout 1800 python3 eval_r7_multi.py ~/clean/multi w_multi_rows.jsonl 8 > w_multi.log 2> w_multi.err || fail=$((fail+1))
SCORE=own R7SW=on R7_PARTS=d,p,c,o,w,s timeout 1800 python3 eval_levels_r7.py ~/clean/multi ws_lv_rows.jsonl 8 > ws_levels.log 2> ws_levels.err || fail=$((fail+1))
python3 ../r6/compare_levels_r6.py own_r5_lv_rows.jsonl r7o=adopted_lv_rows.jsonl r7ws=ws_lv_rows.jsonl > ws_lv_compare.log 2>&1 || fail=$((fail+1))
R7_PARTS=d,p,c,w timeout 1800 python3 eval_r7_single.py ~/clean/dev w_single_rows.jsonl 8 > w_single.log 2> w_single.err || fail=$((fail+1))
grep -q '^R7M_EXIT=0$' ws_multi.log || fail=$((fail+1))
grep -q '^R7S_EXIT=0$' w_single.log || fail=$((fail+1))
echo "WS_RUN_EXIT=$fail"
