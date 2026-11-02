#!/bin/bash
# r7 part q (the brake pieces predict the change from the run's own before-stretch, not from the old-K forecast path)
# on top of the adopted d,p,c (operator, 2026-10-28), worker 3. Multi (S1), single (S1s), levels against r5 and dpc.
# Last line R7Q_RUN_EXIT=<failed steps>.
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1
fail=0
R7_PARTS=d,p,c,q timeout 1800 python3 eval_r7_multi.py ~/clean/multi r7dpcq_multi_rows.jsonl 8 > r7dpcq_multi.log 2> r7dpcq_multi.err || fail=$((fail+1))
R7_PARTS=d,p,c,q timeout 1800 python3 eval_r7_single.py ~/clean/dev r7dpcq_single_rows.jsonl 8 > r7dpcq_single.log 2> r7dpcq_single.err || fail=$((fail+1))
R7SW=on R7_PARTS=d,p,c,q timeout 1800 python3 eval_levels_r7.py ~/clean/multi r7dpcq_lv_rows.jsonl 8 > r7dpcq_levels.log 2> r7dpcq_levels.err || fail=$((fail+1))
python3 ../r6/compare_levels_r6.py ../r5/r5_lv_rows.jsonl dpc=r7dpc_lv_rows.jsonl dpcq=r7dpcq_lv_rows.jsonl > r7q_lv_compare.log 2>&1 || fail=$((fail+1))
grep -q '^R7M_EXIT=0$' r7dpcq_multi.log || fail=$((fail+1))
grep -q '^R7S_EXIT=0$' r7dpcq_single.log || fail=$((fail+1))
echo "R7Q_RUN_EXIT=$fail"
