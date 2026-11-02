#!/bin/bash
# Part zd adoption on w3: keep the previous rows, run the law, compare with the trial, off switch, no-snapshot proof, future check. Last line ZD_EXIT=.
cd ~/node_ops/diag/kcase/clean/r7; export PYTHONDONTWRITEBYTECODE=1; fail=0
for x in multi_rows.jsonl:jsonl lv_rows.jsonl:jsonl single_rows.jsonl:jsonl; do f=${x%%:*}; cp -p adopted_$f adopted_pre_zd_$f; done
for x in multi levels single; do cp -p adopted_$x.log adopted_pre_zd_$x.log; done
O=d,p,c,o,w,s,r,j,u,e,g,y,z,zr,zc; ENVS="SCORE=own R7SW=on R7_PC_NOMF=1 R7_QRMODE=cp R7_QRTIME=begin"; T=~/node_ops/diag/kcase/clean/sub8_order
echo "== 1 off switch: old parts on the edited code"
env $ENVS R7_PARTS=$O timeout 900 python3 eval_r7_multi.py ~/clean/multi zd_off.jsonl 12 > zd_off.log 2> zd_off.err; cmp zd_off.jsonl adopted_pre_zd_multi_rows.jsonl && echo OFF_SAME_MULTI || { echo OFF_DIFF_MULTI; fail=$((fail+1)); }
echo "== 2 adopted run"
./run_adopted.sh > zd_adopted.out 2>&1; tail -1 zd_adopted.out; grep -q '^ADOPTED_RUN_EXIT=0$' zd_adopted.out || fail=$((fail+1)); grep -E '^S1' adopted_multi.log | cut -c1-100
echo "== 3 same as the trial"
cmp adopted_multi_rows.jsonl $T/rs_order.jsonl && echo SAME_AS_TRIAL || { echo DIFF_FROM_TRIAL; fail=$((fail+1)); }
cmp adopted_single_rows.jsonl adopted_pre_zd_single_rows.jsonl && echo SINGLE_UNCHANGED || { echo SINGLE_MOVED; fail=$((fail+1)); }
cmp adopted_lv_rows.jsonl adopted_pre_zd_lv_rows.jsonl && echo LEVELS_ROWS_UNCHANGED || echo LEVELS_ROWS_MOVED; echo "levels log lines moved: $(diff adopted_pre_zd_levels.log adopted_levels.log | grep -c '^>')"
echo "== 4 no-snapshot proof"
( export PYTHONPATH=~/node_ops/diag/kcase/clean/sub8; env $ENVS R7_PARTS=$O,zd timeout 900 python3 eval_r7_multi.py ~/clean/multi zd_guard.jsonl 12 > zd_guard.log 2> zd_guard.err ); cmp zd_guard.jsonl adopted_multi_rows.jsonl && echo GUARD_SAME || { echo GUARD_DIFF; fail=$((fail+1)); }
echo "== 5 future check"
cp -p $T/blind3_v2.py blind3_zd.py
env $ENVS R7_PARTS=$O,zd timeout 2400 python3 blind3_zd.py ~/clean/multi /tmp/blind_zd 12 2> zd_blind.err | tail -48 > zd_blind.out; grep -E '\| (8a|8b) |total differ|BLIND_EXIT' zd_blind.out || fail=$((fail+1)); rm -rf /tmp/blind_zd
echo "== 6 scores"
python3 cnt_all.py adopted_pre_zd_multi_rows.jsonl adopted_multi_rows.jsonl | cut -c1-120
echo "ZD_EXIT=$fail"
