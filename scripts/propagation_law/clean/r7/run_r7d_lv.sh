#!/bin/bash
# r7 parts d,c and d,p,c on every level of the 135 multi runs, compared with r5 (operator, 2026-10-28), worker 3.
# Last line R7DLV_RUN_EXIT=<failed steps>.
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1
fail=0
for v in "dc:d,c" "dpc:d,p,c"; do
  R7SW=on R7_PARTS=${v#*:} timeout 1800 python3 eval_levels_r7.py ~/clean/multi r7${v%%:*}_lv_rows.jsonl 8 \
    > r7${v%%:*}_levels.log 2> r7${v%%:*}_levels.err || fail=$((fail+1))
done
python3 ../r6/compare_levels_r6.py ../r5/r5_lv_rows.jsonl dc=r7dc_lv_rows.jsonl dpc=r7dpc_lv_rows.jsonl \
  > r7d_lv_compare.log 2>&1 || fail=$((fail+1))
echo "R7DLV_RUN_EXIT=$fail"
