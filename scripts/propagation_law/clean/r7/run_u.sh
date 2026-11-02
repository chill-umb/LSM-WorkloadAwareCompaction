#!/bin/bash
# r7 part u (2026-10-28): calm #5b from each L0 merge's L1 -> L2 burst on the new-K schedule, against the run's own
# before-stretch (multi-change runs only: the run's-own measure's form, like part s). Step 1 identity with u off (uid_*,
# must equal adopted_*); step 2 trial (u_*). Worker 3. Last line U_RUN_EXIT=<failed steps>.
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1 R7_QRMODE=cp R7_QRTIME=begin
fail=0
SCORE=own R7_PARTS=d,p,c,o,w,s,r,j timeout 1800 python3 eval_r7_multi.py ~/clean/multi uid_multi_rows.jsonl 8 > uid_multi.log 2> uid_multi.err || fail=$((fail+1))
SCORE=own R7SW=on R7_PARTS=d,p,c,o,w,s,r,j timeout 1800 python3 eval_levels_r7.py ~/clean/multi uid_lv_rows.jsonl 8 > uid_levels.log 2> uid_levels.err || fail=$((fail+1))
R7_PARTS=d,p,c,w,j timeout 1800 python3 eval_r7_single.py ~/clean/dev uid_single_rows.jsonl 8 > uid_single.log 2> uid_single.err || fail=$((fail+1))
for k in multi levels single; do
  if diff <(grep -v '^Rows' adopted_$k.log) <(grep -v '^Rows' uid_$k.log) > /dev/null; then echo "identity $k: SAME"; else echo "identity $k: DIFFERENT"; fail=$((fail+1)); fi
done
SCORE=own R7_PARTS=d,p,c,o,w,s,r,j,u timeout 1800 python3 eval_r7_multi.py ~/clean/multi u_multi_rows.jsonl 8 > u_multi.log 2> u_multi.err || fail=$((fail+1))
SCORE=own R7SW=on R7_PARTS=d,p,c,o,w,s,r,j,u timeout 1800 python3 eval_levels_r7.py ~/clean/multi u_lv_rows.jsonl 8 > u_levels.log 2> u_levels.err || fail=$((fail+1))
grep -q '^R7M_EXIT=0$' u_multi.log || fail=$((fail+1))
echo "U_RUN_EXIT=$fail"
