#!/bin/bash
# r7 evaluation (operator, 2026-10-28; owner: "First investigate #2 ... fix this"), worker 3, from this folder.
# S0: r7 off must reproduce r5's levels log exactly; then r7 on the multi runs (#2-#5b, #7, #8a, #8b; S1 r7 off = r5),
# the single runs (#2-#5b forward; S1s) and every level (#6, #10). Last line R7_RUN_EXIT=<failed steps>.
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1
fail=0
md5sum brake_r7.py ../r5/brake_r5.py l0_schedule_r7.py ../r5/l0_schedule_r5.py fill_r7.py ../r5/fill_r5.py > r7_md5.log
R7SW=off timeout 1800 python3 eval_levels_r7.py ~/clean/multi r7off_lv_rows.jsonl 8 > r7off_levels.log 2> r7off_levels.err || fail=$((fail+1))
diff <(grep -v '^Rows:\|EXIT' ../r5/r5_levels.log) <(grep -v '^Rows:\|EXIT' r7off_levels.log) > r7off_levels.diff
rc=$?; echo "OFFDIFF_RC=$rc" >> r7off_levels.diff; [[ $rc == 0 ]] || fail=$((fail+1))
timeout 1800 python3 eval_r7_multi.py ~/clean/multi r7_multi_rows.jsonl 8 > r7_multi.log 2> r7_multi.err || fail=$((fail+1))
timeout 1800 python3 eval_r7_single.py ~/clean/dev r7_single_rows.jsonl 8 > r7_single.log 2> r7_single.err || fail=$((fail+1))
R7SW=on timeout 1800 python3 eval_levels_r7.py ~/clean/multi r7_lv_rows.jsonl 8 > r7_levels.log 2> r7_levels.err || fail=$((fail+1))
python3 ../r6/compare_levels_r6.py ../r5/r5_lv_rows.jsonl r7=r7_lv_rows.jsonl > r7_lv_compare.log 2>&1 || fail=$((fail+1))
grep -q '^R7M_EXIT=0$' r7_multi.log || fail=$((fail+1))
grep -q '^R7S_EXIT=0$' r7_single.log || fail=$((fail+1))
echo "R7_RUN_EXIT=$fail"
