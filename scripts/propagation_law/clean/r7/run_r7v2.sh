#!/bin/bash
# r7 with parts a (start rate, L0 delay zone), b (brake: zone from 0 files), c (calm #2 against the measured
# before-stretch) (operator, 2026-10-28), worker 3, from this folder. The first run (part a alone) is kept as r7a_*.
# S0: r7 off = r5's levels log; full r7 on multi (S1), single (S1s), levels; multi for the part subsets.
# Last line R7V2_RUN_EXIT=<failed steps>.
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1
fail=0
R7SW=off timeout 1800 python3 eval_levels_r7.py ~/clean/multi r7off_lv_rows.jsonl 8 > r7off_levels.log 2> r7off_levels.err || fail=$((fail+1))
diff <(grep -v '^Rows:\|EXIT' ../r5/r5_levels.log) <(grep -v '^Rows:\|EXIT' r7off_levels.log) > r7off_levels.diff
rc=$?; echo "OFFDIFF_RC=$rc" >> r7off_levels.diff; [[ $rc == 0 ]] || fail=$((fail+1))
timeout 1800 python3 eval_r7_multi.py ~/clean/multi r7_multi_rows.jsonl 8 > r7_multi.log 2> r7_multi.err || fail=$((fail+1))
timeout 1800 python3 eval_r7_single.py ~/clean/dev r7_single_rows.jsonl 8 > r7_single.log 2> r7_single.err || fail=$((fail+1))
R7SW=on timeout 1800 python3 eval_levels_r7.py ~/clean/multi r7_lv_rows.jsonl 8 > r7_levels.log 2> r7_levels.err || fail=$((fail+1))
python3 ../r6/compare_levels_r6.py ../r5/r5_lv_rows.jsonl r7=r7_lv_rows.jsonl > r7_lv_compare.log 2>&1 || fail=$((fail+1))
for v in "ab:a,b" "ac:a,c" "c:c"; do
  R7_PARTS=${v#*:} timeout 1800 python3 eval_r7_multi.py ~/clean/multi r7${v%%:*}_multi_rows.jsonl 8 \
    > r7${v%%:*}_multi.log 2> r7${v%%:*}_multi.err || fail=$((fail+1))
done
for f in r7_multi.log r7ab_multi.log r7ac_multi.log r7c_multi.log; do grep -q '^R7M_EXIT=0$' $f || fail=$((fail+1)); done
grep -q '^R7S_EXIT=0$' r7_single.log || fail=$((fail+1))
echo "R7V2_RUN_EXIT=$fail"
