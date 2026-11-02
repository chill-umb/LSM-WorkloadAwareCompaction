#!/bin/bash
# r7 part j, second form (2026-10-28): rem at the before-stretch's own L0 merge speed (not Win.lam), and the k1t/k2t
# transitions start from the same state (L0 drops the in-flight files at rem; k2t's L1 score holds its output).
# Step 1 identity (jid2_*, must equal adopted_*); step 2 trial (j2_*). Worker 3. Last line J2_RUN_EXIT=<failed steps>.
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1 R7_QRMODE=cp R7_QRTIME=begin
fail=0
SCORE=own R7_PARTS=d,p,c,o,w,s,r timeout 1800 python3 eval_r7_multi.py ~/clean/multi jid2_multi_rows.jsonl 8 > jid2_multi.log 2> jid2_multi.err || fail=$((fail+1))
SCORE=own R7SW=on R7_PARTS=d,p,c,o,w,s,r timeout 1800 python3 eval_levels_r7.py ~/clean/multi jid2_lv_rows.jsonl 8 > jid2_levels.log 2> jid2_levels.err || fail=$((fail+1))
R7_PARTS=d,p,c,w timeout 1800 python3 eval_r7_single.py ~/clean/dev jid2_single_rows.jsonl 8 > jid2_single.log 2> jid2_single.err || fail=$((fail+1))
for k in multi levels single; do
  if diff <(grep -v '^Rows' adopted_$k.log) <(grep -v '^Rows' jid2_$k.log) > /dev/null; then echo "identity $k: SAME"; else echo "identity $k: DIFFERENT"; fail=$((fail+1)); fi
done
SCORE=own R7_PARTS=d,p,c,o,w,s,r,j timeout 1800 python3 eval_r7_multi.py ~/clean/multi j2_multi_rows.jsonl 8 > j2_multi.log 2> j2_multi.err || fail=$((fail+1))
SCORE=own R7SW=on R7_PARTS=d,p,c,o,w,s,r,j timeout 1800 python3 eval_levels_r7.py ~/clean/multi j2_lv_rows.jsonl 8 > j2_levels.log 2> j2_levels.err || fail=$((fail+1))
R7_PARTS=d,p,c,w,j timeout 1800 python3 eval_r7_single.py ~/clean/dev j2_single_rows.jsonl 8 > j2_single.log 2> j2_single.err || fail=$((fail+1))
grep -q '^R7M_EXIT=0$' j2_multi.log || fail=$((fail+1))
grep -q '^R7S_EXIT=0$' j2_single.log || fail=$((fail+1))
echo "J2_RUN_EXIT=$fail"
