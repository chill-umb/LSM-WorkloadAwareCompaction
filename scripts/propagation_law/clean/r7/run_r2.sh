#!/bin/bash
# r7 part r (operator, 2026-10-28; owner: "Start on #4 W5 busy"): #4's after-state at the run's most recent write rate
# (bytes flushed per op over the last R7_QR ops before the change), run's-own measure only. On top of the adopted
# d,p,c,o,w,s. Multi (SCORE=own) at R7_QR 500000 and 1000000 (rates timed on the flushes); levels at 500000. Worker 3. Last line R2_RUN_EXIT=.
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1
fail=0
SCORE=own R7_QR=500000 R7_PARTS=d,p,c,o,w,s,r timeout 1800 python3 eval_r7_multi.py ~/clean/multi r_multi_rows.jsonl 8 > r_multi.log 2> r_multi.err || fail=$((fail+1))
SCORE=own R7_QR=1000000 R7_PARTS=d,p,c,o,w,s,r timeout 1800 python3 eval_r7_multi.py ~/clean/multi r1m_multi_rows.jsonl 8 > r1m_multi.log 2> r1m_multi.err || fail=$((fail+1))
SCORE=own R7SW=on R7_QR=500000 R7_PARTS=d,p,c,o,w,s,r timeout 1800 python3 eval_levels_r7.py ~/clean/multi r_lv_rows.jsonl 8 > r_levels.log 2> r_levels.err || fail=$((fail+1))
diff <(grep -v "^Rows" adopted_levels.log) <(grep -v "^Rows" r_levels.log) > r_levels.diff; echo "levels diff lines: $(wc -l < r_levels.diff)"
grep -q '^R7M_EXIT=0$' r_multi.log || fail=$((fail+1))
echo "R2_RUN_EXIT=$fail"
