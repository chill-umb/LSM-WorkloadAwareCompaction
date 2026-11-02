#!/bin/bash
# r7 part g (2026-10-29, owner: "Do it"): the deepest level's own intake (x - o + trivial bytes of the level above, per
# op of the before-stretch) over the shift between the stretches' midpoints; multi-change runs only. Step 1 keeps the
# adopted logs and rows as adopted_pre_g_*; step 2 identity with g off after the edits (gid_*, must equal
# adopted_pre_g_*); step 3 run_adopted.sh with g (adopted_*); step 4 single runs and multi #2-#8b unchanged, cnt_all on
# the multi rows (cnt_g.log). Worker 3. Last line G_RUN_EXIT=<failed steps>.
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1 R7_QRMODE=cp R7_QRTIME=begin
fail=0
A=d,p,c,o,w,s,r,j,u,e
for k in multi levels single; do cp -p adopted_$k.log adopted_pre_g_$k.log; done
for k in multi lv single; do cp -p adopted_${k}_rows.jsonl adopted_pre_g_${k}_rows.jsonl; done
SCORE=own R7_PARTS=$A timeout 1800 python3 eval_r7_multi.py ~/clean/multi gid_multi_rows.jsonl 8 > gid_multi.log 2> gid_multi.err || fail=$((fail+1))
SCORE=own R7SW=on R7_PARTS=$A timeout 1800 python3 eval_levels_r7.py ~/clean/multi gid_lv_rows.jsonl 8 > gid_levels.log 2> gid_levels.err || fail=$((fail+1))
for k in multi levels; do
  if diff <(grep -v '^Rows' adopted_pre_g_$k.log) <(grep -v '^Rows' gid_$k.log) > /dev/null; then echo "identity $k: SAME"; else echo "identity $k: DIFFERENT"; fail=$((fail+1)); fi
done
bash run_adopted.sh > run_adopted.out 2>&1
tail -1 run_adopted.out
grep -q '^ADOPTED_RUN_EXIT=0$' run_adopted.out || fail=$((fail+1))
for k in single multi; do
  if diff <(grep -v '^Rows' adopted_pre_g_$k.log) <(grep -v '^Rows' adopted_$k.log) > /dev/null; then echo "$k unchanged: SAME"; else echo "$k unchanged: DIFFERENT"; fail=$((fail+1)); fi
done
python3 cnt_all.py adopted_pre_g_multi_rows.jsonl adopted_multi_rows.jsonl > cnt_g.log 2>&1 || fail=$((fail+1))
diff adopted_pre_g_levels.log adopted_levels.log > g_levels.diff
echo "levels lines changed: $(grep -c '^>' g_levels.diff)"
echo "G_RUN_EXIT=$fail"
