#!/usr/bin/env bash
# The workload suite's generator check (PREREGISTRATION D-29 §1-§2; preflight
# step 7): end-to-end runs of the patched db_bench's suite options on small
# trees, against stock RocksDB (01c) where the options are off.
#
#   32_check_suite_generator.sh <patched db_bench> <stock db_bench> <work dir>
#
# 1. Off means unchanged: with every suite option off, the patched binary
#    serves the same Gets, Puts, Seeks and found reads as stock on Assoc,
#    and the same mix on the power law, where the key-sign fix (always on)
#    finds every key and stock about half.
# 2. Decoupling: with uniform keys, coupled Gets never hit a key the run
#    wrote (no memtable hit); decoupled they do; the mix and the result are
#    deterministic; decoupled sizes keep the value-size distribution.
# 3. W1-W5 run as D-29 §2 gives them: every Get finds its key; W3's Puts;
#    W4's inserts, after which the tree holds exactly the loaded keys plus the
#    inserts; W5's switches, at the right operations and stamped in the host
#    log.
# 4. Refusals: each bad combination exits non-zero with its message.
# Each check prints PASS or FAIL; the last line is SUITE_GENERATOR_FAILURES=<n>
# and the exit status is 0 only when n is 0. Small trees: 100,000 keys and
# 200,000 operations at the old geometry, so the whole check takes minutes.
set -uo pipefail

[[ $# -eq 3 ]] || { echo "Usage: $0 <patched db_bench> <stock db_bench> <work dir>" >&2; exit 2; }
PATCHED="$1" STOCK="$2" OUT="$3"
for binary in "$PATCHED" "$STOCK"; do
  [[ -x "$binary" ]] || { echo "Missing $binary" >&2; exit 2; }
done
PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"
ulimit -n "$(ulimit -Hn)" 2>/dev/null || true
LAUNCHER=()
[[ -z "$DBBENCH_CPUS" ]] || LAUNCHER=(taskset -c "$DBBENCH_CPUS")
NUM=100000
OPS=200000
failures=0

rm -rf "$OUT"
mkdir -p "$OUT"
echo "patched $PATCHED; stock $STOCK; open-file limit $(ulimit -n)"

COMMON=(--threads=1 --key_size=64 --value_size=960 --disable_wal=1
  --use_direct_reads=0 --use_direct_io_for_flush_and_compaction=0
  --compression_type=none --level_compaction_dynamic_level_bytes=false
  --num_levels=13 --max_background_jobs=2 --open_files=-1
  --block_size=4096 --bloom_bits=10 --statistics
  --level0_file_num_compaction_trigger=4
  --level0_slowdown_writes_trigger=20 --level0_stop_writes_trigger=36
  --compaction_pri=3 --compaction_style=0
  --write_buffer_size=2097152 --target_file_size_base=524288
  --max_bytes_for_level_base=16777216 --cache_size=1048576
  --max_bytes_for_level_multiplier=10)
VALUES=(--value_theta=925.5 --value_k=0.2615 --value_sigma=25.45
  --mix_max_value_size=65536 --iter_theta=0 --iter_k=2.517 --iter_sigma=14.236
  --mix_max_scan_len=10000)
ASSOC=(--mix_get_ratio=0.806 --mix_put_ratio=0.159 --mix_seek_ratio=0.035
  --keyrange_num=30 --keyrange_dist_a=14.18 --keyrange_dist_b=-2.917
  --keyrange_dist_c=0.0164 --keyrange_dist_d=-0.08082
  --key_dist_a=0.002312 --key_dist_b=0.3467)
POWER=(--keyrange_num=1 --key_dist_a=0.002312 --key_dist_b=0.3467)
UNIFORM=(--keyrange_num=1 --key_dist_a=0 --key_dist_b=0)
DEC=(--mixgraph_decouple_query=true --mixgraph_decouple_sizes=true)

check() {  # <name> <0 when it holds> <detail>
  if [[ "$2" == 0 ]]; then
    echo "PASS $1: $3"
  else
    echo "FAIL $1: $3"
    failures=$(( failures + 1 ))
  fi
}
summary() { grep -o '( Gets:.*' "$1" | head -n 1; }
field() { grep -o "$2:[0-9]*" "$1" | head -n 1 | cut -d: -f2; }
counts() {  # Gets, Puts, Seek and found reads: the draws, in either binary
  local found
  found="$(grep -o 'reads [0-9]* in [0-9]* found' "$1" | head -n 1)"
  printf '%s %s %s %s' "$(field "$1" Gets)" "$(field "$1" Puts)" \
    "$(field "$1" Seek)" "$found"
}
found_ratio() {
  grep -o 'reads [0-9]* in [0-9]* found' "$1" | head -n 1 |
    awk '{ if ($4 > 0) printf "%.4f", $2 / $4; else print "nan" }'
}
ticker() { grep -o "$2 COUNT : [0-9]*" "$1" | head -n 1 | awk '{print $4}'; }
near() {  # <value> <expected> <relative tolerance>: 0 when within
  awk -v v="$1" -v e="$2" -v t="$3" \
    'BEGIN { d = v - e; if (d < 0) d = -d; exit !(v != "" && d <= t * e) }'
}

# One tree per binary: stock has neither the fork's settle step nor its host
# log. Both load the same keys with the same seed.
${LAUNCHER[@]+"${LAUNCHER[@]}"} "$PATCHED" --benchmarks=rlsuspend,filluniquerandom,settle \
  --rl_settle_hold_seconds=2 --num="$NUM" --use_existing_db=0 \
  --db="$OUT/preload" --seed=2 "${COMMON[@]}" > "$OUT/preload.out" 2>&1
check preload_patched $? "filluniquerandom $NUM keys"
${LAUNCHER[@]+"${LAUNCHER[@]}"} "$STOCK" --benchmarks=filluniquerandom,waitforcompaction \
  --num="$NUM" --use_existing_db=0 --db="$OUT/preload_stock" --seed=2 \
  "${COMMON[@]}" > "$OUT/preload_stock.out" 2>&1
check preload_stock $? "filluniquerandom $NUM keys"

run() {  # <name> <binary> <flags...>: mixgraph on a fresh copy of its tree
  local name="$1" binary="$2" tree="$OUT/preload" host_log=()
  shift 2
  if [[ "$binary" == "$STOCK" ]]; then
    tree="$OUT/preload_stock"
  else
    host_log=(--rl_host_log="$OUT/$name.host_log.jsonl")
  fi
  rm -rf "$OUT/db_$name"
  cp -a "$tree" "$OUT/db_$name"
  ${LAUNCHER[@]+"${LAUNCHER[@]}"} "$binary" --benchmarks=mixgraph,stats ${host_log[@]+"${host_log[@]}"} \
    --num="$NUM" --reads="$OPS" --use_existing_db=1 --db="$OUT/db_$name" \
    --seed=301 "${COMMON[@]}" "${VALUES[@]}" "$@" > "$OUT/$name.out" 2>&1
  echo $? > "$OUT/$name.rc"
}
rc() { cat "$OUT/$1.rc"; }

# ---- 1. Off means unchanged
run assoc_stock "$STOCK" "${ASSOC[@]}"
run assoc_patched "$PATCHED" "${ASSOC[@]}"
a="$(counts "$OUT/assoc_stock.out")" b="$(counts "$OUT/assoc_patched.out")"
check off_assoc_equals_stock "$([[ "$(rc assoc_stock)$(rc assoc_patched)" == 00 &&
  "$a" == "$b" ]]; echo $?)" "stock [$a] patched [$b]"
run power_stock "$STOCK" --mix_get_ratio=0.95 --mix_put_ratio=0.05 --mix_seek_ratio=0 "${POWER[@]}"
run power_patched "$PATCHED" --mix_get_ratio=0.95 --mix_put_ratio=0.05 --mix_seek_ratio=0 "${POWER[@]}"
a="$(field "$OUT/power_stock.out" Gets) $(field "$OUT/power_stock.out" Puts)"
b="$(field "$OUT/power_patched.out" Gets) $(field "$OUT/power_patched.out" Puts)"
check off_power_mix_equals_stock "$([[ "$a" == "$b" && "$a" != " " ]]; echo $?)" \
  "Gets/Puts stock [$a] patched [$b]"
fp="$(found_ratio "$OUT/power_patched.out")" fs="$(found_ratio "$OUT/power_stock.out")"
check keysign_fix "$([[ "$fp" == 1.0000 ]] && awk -v x="$fs" 'BEGIN { exit !(x < 0.9) }'; echo $?)" \
  "found patched $fp, stock $fs"

# ---- 2. Decoupling
run uni_coupled "$PATCHED" --mix_get_ratio=0.806 --mix_put_ratio=0.159 --mix_seek_ratio=0.035 "${UNIFORM[@]}"
run uni_decoupled "$PATCHED" --mix_get_ratio=0.806 --mix_put_ratio=0.159 --mix_seek_ratio=0.035 "${UNIFORM[@]}" --mixgraph_decouple_query=true
mc="$(ticker "$OUT/uni_coupled.out" rocksdb.memtable.hit)"
md="$(ticker "$OUT/uni_decoupled.out" rocksdb.memtable.hit)"
check decouple_memtable_hits "$([[ "$(rc uni_coupled)$(rc uni_decoupled)" == 00 &&
  "$mc" == 0 && "${md:-0}" -gt 100 ]]; echo $?)" "memtable hits coupled $mc, decoupled $md"
g="$(field "$OUT/uni_decoupled.out" Gets)" p="$(field "$OUT/uni_decoupled.out" Puts)"
s="$(field "$OUT/uni_decoupled.out" Seek)"
check decouple_mix "$(near "$g" $(( OPS * 806 / 1000 )) 0.01 &&
  near "$p" $(( OPS * 159 / 1000 )) 0.03 && near "$s" $(( OPS * 35 / 1000 )) 0.08; echo $?)" \
  "Gets $g Puts $p Seek $s of $OPS"
run uni_decoupled_again "$PATCHED" --mix_get_ratio=0.806 --mix_put_ratio=0.159 --mix_seek_ratio=0.035 "${UNIFORM[@]}" --mixgraph_decouple_query=true
a="$(summary "$OUT/uni_decoupled.out")" b="$(summary "$OUT/uni_decoupled_again.out")"
check decouple_deterministic "$([[ -n "$a" && "$a" == "$b" ]]; echo $?)" "same seed, same summary"
avg_value() { grep -o 'avg size: [-0-9.na]* value' "$1" | head -n 1 | awk '{print $3}'; }
run w2_query_only "$PATCHED" --mix_get_ratio=0.95 --mix_put_ratio=0.05 --mix_seek_ratio=0 "${POWER[@]}" --mixgraph_decouple_query=true
run w2 "$PATCHED" --mix_get_ratio=0.95 --mix_put_ratio=0.05 --mix_seek_ratio=0 "${POWER[@]}" "${DEC[@]}"
v0="$(avg_value "$OUT/w2_query_only.out")" v1="$(avg_value "$OUT/w2.out")"
check sizes_value "$([[ "$v0" != "$v1" ]] && near "$v1" 960.3 0.01; echo $?)" \
  "W2 mean value: tied to the key $v0, decoupled $v1 (fitted mean 960.3)"

# ---- 3. W1-W5
run w1 "$PATCHED" "${ASSOC[@]}" "${DEC[@]}"
for w in w1 w2; do
  check "${w}_finds_its_keys" "$([[ "$(rc "$w")" == 0 && "$(found_ratio "$OUT/$w.out")" == 1.0000 ]]; echo $?)" \
    "found $(found_ratio "$OUT/$w.out")"
done
run w3 "$PATCHED" --mix_get_ratio=0.1 --mix_put_ratio=0.9 --mix_seek_ratio=0 "${POWER[@]}" "${DEC[@]}"
p="$(field "$OUT/w3.out" Puts)"
check w3 "$([[ "$(rc w3)" == 0 && "$(found_ratio "$OUT/w3.out")" == 1.0000 ]] &&
  near "$p" $(( OPS * 9 / 10 )) 0.01; echo $?)" "Puts $p of $OPS, found $(found_ratio "$OUT/w3.out")"
run w4 "$PATCHED" --mix_get_ratio=0.1 --mix_put_ratio=0 --mix_seek_ratio=0 --mix_insert_ratio=0.9 "${UNIFORM[@]}" "${DEC[@]}"
inserts="$(grep -o 'Inserts:[0-9]*' "$OUT/w4.out" | cut -d: -f2)"
p="$(field "$OUT/w4.out" Puts)"
check w4_inserts "$([[ "$(rc w4)" == 0 && "$p" == 0 ]] && near "${inserts:-0}" $(( OPS * 9 / 10 )) 0.01; echo $?)" \
  "Inserts $inserts Puts $p"
check w4_finds_its_keys "$([[ "$(found_ratio "$OUT/w4.out")" == 1.0000 ]]; echo $?)" \
  "found $(found_ratio "$OUT/w4.out")"
${LAUNCHER[@]+"${LAUNCHER[@]}"} "$PATCHED" --benchmarks=readseq --use_existing_db=1 --db="$OUT/db_w4" \
  --num="$NUM" --reads=1000000000 "${COMMON[@]}" > "$OUT/w4_readseq.out" 2>&1
keys="$(grep -o 'readseq *:.* [0-9]* operations' "$OUT/w4_readseq.out" | grep -o '[0-9]* operations' | cut -d' ' -f1)"
check w4_key_count "$([[ -n "$inserts" && "$keys" == $(( NUM + inserts )) ]]; echo $?)" \
  "keys after the run $keys = $NUM + $inserts"
check w4_summary_parses "$(grep -qE 'Gets:[0-9]+ Puts:[0-9]+ Seek:[0-9]+( ScanEntries:[0-9]+)?.*avg size: [-0-9.na]+ value, [-0-9.na]+ scan, Inserts:[0-9]+\)' "$OUT/w4.out"; echo $?)" \
  "$(summary "$OUT/w4.out")"
run w5 "$PATCHED" "${UNIFORM[@]}" "${DEC[@]}" --mix_phases="60000:0.1:0.9:0;60000:0.5:0.5:0;80000:0.9:0.1:0"
switches="$(grep -o 'RL_PHASE_START phase=[0-9]* op=[0-9]*' "$OUT/w5.out" | tr '\n' ' ')"
check w5_switches "$([[ "$(rc w5)" == 0 && "$switches" == "RL_PHASE_START phase=2 op=60000 RL_PHASE_START phase=3 op=120000 " ]]; echo $?)" \
  "$switches"
stamps="$(grep -o '"phase":[0-9]*' "$OUT/w5.host_log.jsonl" | tr '\n' ' ')"
check w5_stamps "$([[ "$stamps" == '"phase":2 "phase":3 ' ]]; echo $?)" "$stamps"
g="$(field "$OUT/w5.out" Gets)"
check w5_mix "$(near "$g" 108000 0.01; echo $?)" "Gets $g (expected 6,000 + 30,000 + 72,000)"

# ---- 4. Refusals
refuse() {  # <name> <message> <flags...>
  local name="$1" message="$2"
  shift 2
  run "$name" "$PATCHED" "$@"
  check "refuse_$name" "$([[ "$(rc "$name")" != 0 ]] && grep -q -- "$message" "$OUT/$name.out"; echo $?)" \
    "exit $(rc "$name"): $(grep -m1 -- "$message" "$OUT/$name.out")"
}
refuse phases_sum "must equal the run" "${UNIFORM[@]}" --mix_phases="1000:1:0:0"
refuse phases_bad "--mix_phases: phase 2" "${UNIFORM[@]}" --mix_phases="100000:1:0:0;100000:x:0:0"
refuse phases_with_insert_ratio "gives inserts per phase" "${UNIFORM[@]}" --mix_insert_ratio=0.5 --mix_phases="200000:1:0:0"
refuse insert_skewed "need uniform keys" "${POWER[@]}" --mix_get_ratio=0.1 --mix_insert_ratio=0.9
refuse insert_negative "must be >= 0" "${UNIFORM[@]}" --mix_insert_ratio=-0.5

rm -rf "$OUT"/db_* "$OUT/preload" "$OUT/preload_stock"
echo "SUITE_GENERATOR_FAILURES=$failures"
(( failures == 0 ))
