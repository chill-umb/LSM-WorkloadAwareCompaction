#!/bin/bash
# r7 part r, cp mode (owner 2026-10-28: "Try a principled version"): the write rate since the last BIC-accepted step in
# the last stretch's flush intervals. On top of d,p,c,o,w,s. Multi (SCORE=own), levels, steps seen. Worker 3.
# Last line RCP_RUN_EXIT=<failed steps>.
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1 R7_QRMODE=cp
fail=0
SCORE=own R7_PARTS=d,p,c,o,w,s,r timeout 1800 python3 eval_r7_multi.py ~/clean/multi rcp_multi_rows.jsonl 8 > rcp_multi.log 2> rcp_multi.err || fail=$((fail+1))
SCORE=own R7SW=on R7_PARTS=d,p,c,o,w,s,r timeout 1800 python3 eval_levels_r7.py ~/clean/multi rcp_lv_rows.jsonl 8 > rcp_levels.log 2> rcp_levels.err || fail=$((fail+1))
diff <(grep -v "^Rows" adopted_levels.log) <(grep -v "^Rows" rcp_levels.log) > rcp_levels.diff; echo "levels diff lines: $(wc -l < rcp_levels.diff)"
R7_PARTS=d,p,c,o,w,s,r timeout 900 python3 ../diag4c/steps_seen.py > ../diag4c/steps_seen.log 2>&1 || fail=$((fail+1))
grep -q '^R7M_EXIT=0$' rcp_multi.log || fail=$((fail+1))
echo "RCP_RUN_EXIT=$fail"
