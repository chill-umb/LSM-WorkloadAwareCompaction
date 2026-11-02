#!/bin/bash
# r7 part j, third form (2026-10-28): the second form plus #7 counting L0's files before the in-flight merge installs
# (the slowdown counts every L0 file). Step 1 identity (jid3_*, must equal adopted_*); step 2 trial (j3_*).
# Worker 3. Last line J3_RUN_EXIT=<failed steps>.
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1 R7_QRMODE=cp R7_QRTIME=begin
fail=0
SCORE=own R7_PARTS=d,p,c,o,w,s,r timeout 1800 python3 eval_r7_multi.py ~/clean/multi jid3_multi_rows.jsonl 8 > jid3_multi.log 2> jid3_multi.err || fail=$((fail+1))
SCORE=own R7SW=on R7_PARTS=d,p,c,o,w,s,r timeout 1800 python3 eval_levels_r7.py ~/clean/multi jid3_lv_rows.jsonl 8 > jid3_levels.log 2> jid3_levels.err || fail=$((fail+1))
R7_PARTS=d,p,c,w timeout 1800 python3 eval_r7_single.py ~/clean/dev jid3_single_rows.jsonl 8 > jid3_single.log 2> jid3_single.err || fail=$((fail+1))
for k in multi levels single; do
  if diff <(grep -v '^Rows' adopted_$k.log) <(grep -v '^Rows' jid3_$k.log) > /dev/null; then echo "identity $k: SAME"; else echo "identity $k: DIFFERENT"; fail=$((fail+1)); fi
done
SCORE=own R7_PARTS=d,p,c,o,w,s,r,j timeout 1800 python3 eval_r7_multi.py ~/clean/multi j3_multi_rows.jsonl 8 > j3_multi.log 2> j3_multi.err || fail=$((fail+1))
SCORE=own R7SW=on R7_PARTS=d,p,c,o,w,s,r,j timeout 1800 python3 eval_levels_r7.py ~/clean/multi j3_lv_rows.jsonl 8 > j3_levels.log 2> j3_levels.err || fail=$((fail+1))
R7_PARTS=d,p,c,w,j timeout 1800 python3 eval_r7_single.py ~/clean/dev j3_single_rows.jsonl 8 > j3_single.log 2> j3_single.err || fail=$((fail+1))
grep -q '^R7M_EXIT=0$' j3_multi.log || fail=$((fail+1))
grep -q '^R7S_EXIT=0$' j3_single.log || fail=$((fail+1))
echo "J3_RUN_EXIT=$fail"
