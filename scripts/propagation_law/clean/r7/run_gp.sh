#!/bin/bash
# r7 part g's rate timed on whole pours (2026-10-29, owner: "replace part g's rate with Pw in the law, then rerun the
# full scoring on worker 3, with the same identity and no-side-effect checks as before"). Step 1 keeps the adopted logs
# and rows (part g at 02:51) as adopted_g1_*; step 2 builds ~/clean/multi/pour/ (build_pour_r7.py, rebuild error 0);
# step 3 identity with R7_GRATE=stretch (gpid_*, must equal adopted_g1_*); step 4 run_adopted.sh (R7_GRATE=pour, the
# default; adopted_*); step 5 single runs and multi #2-#8b unchanged, cnt_all on the multi rows (cnt_gp.log), the
# levels diff (gp_levels.diff). Worker 3. Last line GP_RUN_EXIT=<failed steps>.
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1 R7_QRMODE=cp R7_QRTIME=begin
fail=0
A=d,p,c,o,w,s,r,j,u,e,g
for k in multi levels single; do cp -p adopted_$k.log adopted_g1_$k.log; done
for k in multi lv single; do cp -p adopted_${k}_rows.jsonl adopted_g1_${k}_rows.jsonl; done
python3 build_pour_r7.py ~/clean/multi 12 > build_pour.log 2>&1
tail -2 build_pour.log
grep -q '^BUILD_POUR_EXIT=0$' build_pour.log || fail=$((fail+1))
R7_GRATE=stretch SCORE=own R7_PARTS=$A timeout 1800 python3 eval_r7_multi.py ~/clean/multi gpid_multi_rows.jsonl 8 > gpid_multi.log 2> gpid_multi.err || fail=$((fail+1))
R7_GRATE=stretch SCORE=own R7SW=on R7_PARTS=$A timeout 1800 python3 eval_levels_r7.py ~/clean/multi gpid_lv_rows.jsonl 8 > gpid_levels.log 2> gpid_levels.err || fail=$((fail+1))
for k in multi levels; do
  if diff <(grep -v '^Rows' adopted_g1_$k.log) <(grep -v '^Rows' gpid_$k.log) > /dev/null; then echo "identity $k: SAME"; else echo "identity $k: DIFFERENT"; fail=$((fail+1)); fi
done
bash run_adopted.sh > run_adopted.out 2>&1
tail -1 run_adopted.out
grep -q '^ADOPTED_RUN_EXIT=0$' run_adopted.out || fail=$((fail+1))
for k in single multi; do
  if diff <(grep -v '^Rows' adopted_g1_$k.log) <(grep -v '^Rows' adopted_$k.log) > /dev/null; then echo "$k unchanged: SAME"; else echo "$k unchanged: DIFFERENT"; fail=$((fail+1)); fi
done
python3 cnt_all.py adopted_g1_multi_rows.jsonl adopted_multi_rows.jsonl > cnt_gp.log 2>&1 || fail=$((fail+1))
diff adopted_g1_levels.log adopted_levels.log > gp_levels.diff
echo "levels lines changed: $(grep -c '^>' gp_levels.diff)"
echo "GP_RUN_EXIT=$fail"
