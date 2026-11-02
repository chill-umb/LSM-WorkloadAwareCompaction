#!/bin/bash
# r7 part o (operator, 2026-10-28): under SCORE=own only the K' path's brake crossing cuts the window, and braked
# pieces predict the change from the run's own before-stretch (no old-K forecast). On top of the adopted d,p,c.
# Multi (#2-#5b, S1), every level (#6, #10) against own r7, and single (D-33, for information). Worker 3, this folder.
# Last line OWNO_RUN_EXIT=<failed steps>.
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1
fail=0
SCORE=own R7_PARTS=d,p,c,o timeout 1800 python3 eval_r7_multi.py ~/clean/multi owno_multi_rows.jsonl 8 > owno_multi.log 2> owno_multi.err || fail=$((fail+1))
SCORE=own R7SW=on R7_PARTS=d,p,c,o timeout 1800 python3 eval_levels_r7.py ~/clean/multi owno_lv_rows.jsonl 8 > owno_levels.log 2> owno_levels.err || fail=$((fail+1))
python3 ../r6/compare_levels_r6.py own_r5_lv_rows.jsonl r7=own_r7_lv_rows.jsonl r7o=owno_lv_rows.jsonl > owno_lv_compare.log 2>&1 || fail=$((fail+1))
R7_PARTS=d,p,c,o timeout 1800 python3 eval_r7_single.py ~/clean/dev owno_single_rows.jsonl 8 > owno_single.log 2> owno_single.err || fail=$((fail+1))
grep -q '^R7M_EXIT=0$' owno_multi.log || fail=$((fail+1))
echo "OWNO_RUN_EXIT=$fail"
