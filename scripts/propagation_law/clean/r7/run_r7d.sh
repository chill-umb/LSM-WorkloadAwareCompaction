#!/bin/bash
# r7 part d (delayed L0 merges begin at the write controller's cap; the pre window's own regime keeps round 17's
# x-rule) and part p (d inside the brake forecast's braked pieces), each with part c (calm #2 against the measured
# before-stretch) (operator, 2026-10-28), worker 3, from this folder. Multi (S1) and single (S1s) for "d,c" and
# "d,p,c". Last line R7D_RUN_EXIT=<failed steps>.
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1
fail=0
for v in "dc:d,c" "dpc:d,p,c"; do
  R7_PARTS=${v#*:} timeout 1800 python3 eval_r7_multi.py ~/clean/multi r7${v%%:*}_multi_rows.jsonl 8 \
    > r7${v%%:*}_multi.log 2> r7${v%%:*}_multi.err || fail=$((fail+1))
  R7_PARTS=${v#*:} timeout 1800 python3 eval_r7_single.py ~/clean/dev r7${v%%:*}_single_rows.jsonl 8 \
    > r7${v%%:*}_single.log 2> r7${v%%:*}_single.err || fail=$((fail+1))
  grep -q '^R7M_EXIT=0$' r7${v%%:*}_multi.log || fail=$((fail+1))
  grep -q '^R7S_EXIT=0$' r7${v%%:*}_single.log || fail=$((fail+1))
done
echo "R7D_RUN_EXIT=$fail"
