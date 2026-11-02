#!/bin/bash
# r7 parts e, f (2026-10-28): busy #5 in the law's base form om_b (L2'/L2)(L1/L1') - om_b, without om5's override
# (om5's L2 baseline T2 is the old-K continuation at the window, the twin's stand-in; the run's-own measure differences
# the run's own before-stretch). e: #5 and #5b (dx uses dom); f: #5 only. Multi-change runs only. Step 1 identity
# with both off after the edits (eid_*, must equal adopted_*); step 2 trials (e_*, f_*). Worker 3.
# Last line E_RUN_EXIT=<failed steps>.
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1 R7_QRMODE=cp R7_QRTIME=begin
fail=0
A=d,p,c,o,w,s,r,j,u
SCORE=own R7_PARTS=$A timeout 1800 python3 eval_r7_multi.py ~/clean/multi eid_multi_rows.jsonl 8 > eid_multi.log 2> eid_multi.err || fail=$((fail+1))
SCORE=own R7SW=on R7_PARTS=$A timeout 1800 python3 eval_levels_r7.py ~/clean/multi eid_lv_rows.jsonl 8 > eid_levels.log 2> eid_levels.err || fail=$((fail+1))
R7_PARTS=d,p,c,w,j timeout 1800 python3 eval_r7_single.py ~/clean/dev eid_single_rows.jsonl 8 > eid_single.log 2> eid_single.err || fail=$((fail+1))
for k in multi levels single; do
  if diff <(grep -v '^Rows' adopted_$k.log) <(grep -v '^Rows' eid_$k.log) > /dev/null; then echo "identity $k: SAME"; else echo "identity $k: DIFFERENT"; fail=$((fail+1)); fi
done
for x in e f; do
  SCORE=own R7_PARTS=$A,$x timeout 1800 python3 eval_r7_multi.py ~/clean/multi ${x}_multi_rows.jsonl 8 > ${x}_multi.log 2> ${x}_multi.err || fail=$((fail+1))
  SCORE=own R7SW=on R7_PARTS=$A,$x timeout 1800 python3 eval_levels_r7.py ~/clean/multi ${x}_lv_rows.jsonl 8 > ${x}_levels.log 2> ${x}_levels.err || fail=$((fail+1))
  grep -q '^R7M_EXIT=0$' ${x}_multi.log || fail=$((fail+1))
done
echo "E_RUN_EXIT=$fail"
