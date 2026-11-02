#!/bin/bash
# r7 part j (2026-10-28, owner: "go with #8a W5"): #8a/#8b start from the state when the L0 merge running at the
# change installs (forward_r7.inflight_state). Step 1, identity: the adopted parts with the edited code must give the
# adopted_* logs exactly (jid_*). Step 2, trial: the adopted parts plus j (j_*), multi + levels + single.
# Worker 3, from this folder. Last line J_RUN_EXIT=<failed steps>.
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1 R7_QRMODE=cp R7_QRTIME=begin
fail=0
SCORE=own R7_PARTS=d,p,c,o,w,s,r timeout 1800 python3 eval_r7_multi.py ~/clean/multi jid_multi_rows.jsonl 8 > jid_multi.log 2> jid_multi.err || fail=$((fail+1))
SCORE=own R7SW=on R7_PARTS=d,p,c,o,w,s,r timeout 1800 python3 eval_levels_r7.py ~/clean/multi jid_lv_rows.jsonl 8 > jid_levels.log 2> jid_levels.err || fail=$((fail+1))
R7_PARTS=d,p,c,w timeout 1800 python3 eval_r7_single.py ~/clean/dev jid_single_rows.jsonl 8 > jid_single.log 2> jid_single.err || fail=$((fail+1))
for k in multi levels single; do
  if diff <(grep -v '^Rows' adopted_$k.log) <(grep -v '^Rows' jid_$k.log) > /dev/null; then echo "identity $k: SAME"; else echo "identity $k: DIFFERENT"; fail=$((fail+1)); fi
done
SCORE=own R7_PARTS=d,p,c,o,w,s,r,j timeout 1800 python3 eval_r7_multi.py ~/clean/multi j_multi_rows.jsonl 8 > j_multi.log 2> j_multi.err || fail=$((fail+1))
SCORE=own R7SW=on R7_PARTS=d,p,c,o,w,s,r,j timeout 1800 python3 eval_levels_r7.py ~/clean/multi j_lv_rows.jsonl 8 > j_levels.log 2> j_levels.err || fail=$((fail+1))
R7_PARTS=d,p,c,w,j timeout 1800 python3 eval_r7_single.py ~/clean/dev j_single_rows.jsonl 8 > j_single.log 2> j_single.err || fail=$((fail+1))
grep -q '^R7M_EXIT=0$' j_multi.log || fail=$((fail+1))
grep -q '^R7S_EXIT=0$' j_single.log || fail=$((fail+1))
echo "J_RUN_EXIT=$fail"
