#!/usr/bin/env bash
# One configuration file for the complete db_bench experiment pipeline.
# All sizes are bytes unless the variable name says otherwise.

# Workload matrix.
WORKLOAD_PROFILE="${WORKLOAD_PROFILE:-assoc-v1}"
WORKLOAD_SIZES_M="${WORKLOAD_SIZES_M:-10 20 30 40 50}"
SIZE_RATIOS="${SIZE_RATIOS:-2 6 10}"
EXPERIMENT_ARMS="${EXPERIMENT_ARMS:-regular rl}"
REPEATS="${REPEATS:-1}"
# calibration: oracle + provisional manifest + compact latency log
# holdout: oracle + calibrated manifest + non-mutating safety-shadow log
# experiment: ordinary paired/smoke runs with mandatory learner-health gating
RL_RUN_PHASE="${RL_RUN_PHASE:-experiment}"

# Pathway B1 --- the UDB `Assoc` column family of Cao et al., "Characterizing,
# Modeling, and Benchmarking RocksDB Key-Value Workloads at Facebook", FAST
# 2020. These are db_bench's own flag defaults for the same fit; the paper's
# appendix, the RocksDB wiki and its section 7.4 disagree at the second decimal
# and this picks the tool's values rather than silently choosing among them.
#
# 29% initial unique inserts, then 71% mixed operations. The load phase runs
# under native compaction and outside the measured window (rlsuspend +
# resetstats), so LOAD_PERCENT shapes the starting tree, not the measurement.
LOAD_PERCENT="${LOAD_PERCENT:-29}"
MIX_GET_RATIO="${MIX_GET_RATIO:-0.806}"
MIX_PUT_RATIO="${MIX_PUT_RATIO:-0.159}"
MIX_SEEK_RATIO="${MIX_SEEK_RATIO:-0.035}"
# Scan length is the generalized Pareto of ParetoCdfInversion, so SCAN_LENGTH is
# its `theta` --- the floor, not the typical length. At the fit below the median
# is ~27 entries and ~5% of draws exceed MIX_MAX_SCAN_LENGTH, which db_bench
# applies as a modulo rather than a clamp (db_bench_tool.cc:7357). Identical for
# every arm, so it cannot bias a paired difference.
SCAN_LENGTH="${SCAN_LENGTH:-0}"
ITER_K="${ITER_K:-2.517}"
ITER_SIGMA="${ITER_SIGMA:-14.236}"
MIX_MAX_SCAN_LENGTH="${MIX_MAX_SCAN_LENGTH:-10000}"

# Key skew. mixgraph only builds hot key ranges when at least one
# KEYRANGE_DIST_* is nonzero; all-zero yields uniform random keys, which is the
# pre-2026-09-20 `balanced-v1` family and is what made the workload
# near-garbage-free. Set WORKLOAD_SKEW=0 to restore it for the uniform control
# arms that criterion B-1 compares against.
# WORKLOAD_SKEW=2 is D-13's second Gate N2 workload, "YCSB-B operation mix
# with power-law key popularity" (never Zipfian or YCSB-B): keyrange_num 1 and
# no keyrange_dist, so mixgraph draws keys from f(x) = a x^b at KEY_DIST_A/B,
# Assoc's own key-hotness fit. It needs MIX_GET_RATIO=0.95 MIX_PUT_RATIO=0.05
# MIX_SEEK_RATIO=0, which 03 checks against the contract.
WORKLOAD_SKEW="${WORKLOAD_SKEW:-1}"
KEYRANGE_NUM="${KEYRANGE_NUM:-30}"          # skew-intensity axis; sweep {5, 30, 100}
KEYRANGE_DIST_A="${KEYRANGE_DIST_A:-14.18}"
KEYRANGE_DIST_B="${KEYRANGE_DIST_B:--2.917}"
KEYRANGE_DIST_C="${KEYRANGE_DIST_C:-0.0164}"
KEYRANGE_DIST_D="${KEYRANGE_DIST_D:--0.08082}"
KEY_DIST_A="${KEY_DIST_A:-0.002312}"
KEY_DIST_B="${KEY_DIST_B:-0.3467}"

# Value size for the mixed phase, also a generalized Pareto. VALUE_THETA is the
# floor and the distribution adds sigma/(1-k) = 34.5 bytes on top, so 925.5
# gives a measured mean of 960.4 --- the project's record size, kept so the
# level ladder, the populated depth and the T sweep stay comparable with the
# geometry every other constant is calibrated for. The paper's own theta is 0
# (mean ~34 bytes); departing from it is deliberate and must be reported as
# "Assoc key distribution and operation mix at the project's record size",
# never as the published value distribution.
#
# MIX_MAX_VALUE_SIZE is NOT cosmetic: db_bench applies it as `val_size %
# value_max` (db_bench_tool.cc:7316), so the 1024 default wraps the 6.85% of
# draws above it down to as little as 1 byte and pulls the mean to 890.2.
VALUE_THETA="${VALUE_THETA:-925.5}"
VALUE_K="${VALUE_K:-0.2615}"
VALUE_SIGMA="${VALUE_SIGMA:-25.45}"
MIX_MAX_VALUE_SIZE="${MIX_MAX_VALUE_SIZE:-65536}"

# Record and LSM geometry, matching the parameters historically used by
# run_vanilla_sweep.sh.
KEY_SIZE="${KEY_SIZE:-64}"
VALUE_SIZE="${VALUE_SIZE:-960}"
WRITE_BUFFER_SIZE="${WRITE_BUFFER_SIZE:-2097152}"        # 2 MiB
TARGET_FILE_SIZE="${TARGET_FILE_SIZE:-524288}"           # 512 KiB
MAX_BYTES_FOR_LEVEL_BASE="${MAX_BYTES_FOR_LEVEL_BASE:-16777216}"  # 16 MiB
# T=2 needs additional depth at the 50M scale; all arms use the same maximum
# so changing T does not silently change the available tree depth.
NUM_LEVELS="${NUM_LEVELS:-13}"
MAX_BACKGROUND_JOBS="${MAX_BACKGROUND_JOBS:-2}"
BLOCK_CACHE_SIZE="${BLOCK_CACHE_SIZE:-8388608}"          # 8 MiB
BLOCK_SIZE="${BLOCK_SIZE:-4096}"                         # 4 KiB data blocks
BLOOM_BITS="${BLOOM_BITS:-10}"
DISABLE_WAL="${DISABLE_WAL:-1}"
# Direct I/O for both the read path and background flush/compaction. Pinned OFF
# on measured evidence from the measurement node, 10M T=2: mixgraph ran at
# 2,750 ops/s with direct I/O (max_open_files=-1; 1,960 at 1000) against
# 58,332 ops/s buffered, a 21x penalty putting the Gate 1 sweep near 95 h.
# Buffered means the host page cache holds the ~3.7 GB database, so latency,
# runtime and stall figures are warm-cache and must be reported as such. Write,
# point-read and space amplification, per-level merge survival and the
# capacity-space curve are byte ratios set by tree shape, so they are
# unaffected. May be toggled to 1 for the final paper benchmark if the
# amplification results justify the cost; such runs carry fingerprint dio1 and
# cannot be pooled with dio0 runs. compaction_readahead_size stays at the
# pinned tree's 2 MB default either way.
USE_DIRECT_IO="${USE_DIRECT_IO:-0}"
# Per-level target multipliers (PATHWAYS Pathway A), passed to db_bench's
# --level_target_multipliers: ":"-separated, one entry per level, entry 0 = 1,
# each in [0.5, 2.0], never shrinking a level's target below the one above it.
# RocksDB rejects anything else at open. Empty means all 1. Example at
# num_levels=7, holding L5 at 2x:
#   LEVEL_TARGET_MULTIPLIERS="1:1:1:1:1:2:1"
LEVEL_TARGET_MULTIPLIERS="${LEVEL_TARGET_MULTIPLIERS:-}"
L0_COMPACTION_TRIGGER="${L0_COMPACTION_TRIGGER:-4}"
L0_SLOWDOWN_TRIGGER="${L0_SLOWDOWN_TRIGGER:-20}"
L0_STOP_TRIGGER="${L0_STOP_TRIGGER:-36}"
COMPACTION_PRIORITY="${COMPACTION_PRIORITY:-3}"  # RocksDB kMinOverlappingRatio
SOFT_PENDING_BYTES="${SOFT_PENDING_BYTES:-68719476736}"  # 64 GiB
HARD_PENDING_BYTES="${HARD_PENDING_BYTES:-137438953472}" # 128 GiB
OPEN_FILES="${OPEN_FILES:-1000}"

# Reproducibility and execution.
DBBENCH_SEED="${DBBENCH_SEED:-1}"
THREADS="${THREADS:-1}"
STATS_DUMP_PERIOD_SECONDS="${STATS_DUMP_PERIOD_SECONDS:-20}"
# Target microarchitecture for the measured RocksDB/db_bench build, passed to
# RocksDB's PORTABLE cache variable. RocksDB turns 0 into -march=native, which
# silently resolves to a generic or older microarchitecture when the compiler
# does not recognise the host CPU; naming the target turns that into a
# configure-time failure instead. The measurement nodes are Chameleon
# CHI@NCAR Zen 5, so znver5 is the default. Needs GCC 14.1+ or Clang 19+.
ROCKSDB_PORTABLE="${ROCKSDB_PORTABLE:-znver5}"
# CPU pinning for the measured processes, as taskset -c lists. db_bench and the
# Python controller must not share a last-level cache: a controller sharing L3
# with db_bench evicts its cache lines even from a core of its own. The
# measurement node is a single-socket, single-NUMA-node AMD EPYC 4545P with SMT
# off, 16 cores in two 8-core CCDs, so CPU N is physical core N and cores 0-7
# and 8-15 are separate L3 domains. Both sets apply to every arm, including
# `regular`, which starts no controller: an arm that had the controller's cores
# to itself would not be comparable with one that did not. Empty disables
# pinning. 03_run_experiments.sh validates the sets and records them per arm.
DBBENCH_CPUS="${DBBENCH_CPUS-0-7}"
CONTROLLER_CPUS="${CONTROLLER_CPUS-8}"
DBBENCH_BUILD_DIR="${DBBENCH_BUILD_DIR:-build-dbbench}"
PYTHON_VENV="${PYTHON_VENV:-.venv-dbbench}"
BUILD_JOBS="${BUILD_JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)}"

# Tests and the preflight (CLAUDE.md "Tests", plan §6). 01b builds the fork's
# own gtest files, listed here relative to lib/rocksdb, in a separate Debug
# tree so RocksDB's asserts fire; upstream test files are never listed. A
# listed file that does not exist yet is skipped.
FORK_TEST_SOURCES="${FORK_TEST_SOURCES:-db/level_target_multipliers_test.cc db/per_level_read_counters_test.cc db/rl_controller_host_test.cc}"
DBBENCH_TEST_BUILD_DIR="${DBBENCH_TEST_BUILD_DIR:-build-dbbench-debug}"
# Written by 13, checked by 03 before any run larger than
# PREFLIGHT_SHORT_RUN_MAX_M million operations (the preflight's own runs are
# 1M and 2M).
PREFLIGHT_MARKER="${PREFLIGHT_MARKER:-$DBBENCH_BUILD_DIR/PREFLIGHT_PASSED}"
PREFLIGHT_SHORT_RUN_MAX_M="${PREFLIGHT_SHORT_RUN_MAX_M:-2}"
# Reports and run outputs of the preflight's steps 3 and 4.
PREFLIGHT_WORK_DIR="${PREFLIGHT_WORK_DIR:-$DBBENCH_BUILD_DIR/preflight}"
# ACT-4 (PATHWAYS Pathway A §6): "stock" is upstream RocksDB 11.1.1, the
# fork's upstream base, with none of the fork's changes, built by 01c with 01
# and 02's flags. The patched db_bench at m = 1 must match it.
STOCK_ROCKSDB_COMMIT="${STOCK_ROCKSDB_COMMIT:-6cdeb9d9d0630763327f512e6255cab33f6834e7}"
STOCK_SOURCE_DIR="${STOCK_SOURCE_DIR:-build-dbbench-stock-src}"
STOCK_BUILD_DIR="${STOCK_BUILD_DIR:-build-dbbench-stock}"
# Pairs of the ACT-4 runs (1M operations, T=2 each). Five is 09's floor for a
# paired envelope; the evaluator reports "undecided" if the data need more.
PARITY_PAIRS="${PARITY_PAIRS:-5}"
# D-13's stall margin, reused as ACT-4's stall allowance: the patched binary's
# stall fraction may exceed stock's by at most this, as a paired bound.
PARITY_STALL_FRACTION_MARGIN="${PARITY_STALL_FRACTION_MARGIN:-0.02}"

# Programme 1 measured phase (PREREGISTRATION D-13 §6): after the load, the
# settle step waits for compaction and then holds h_w seconds with nothing
# due; every mixgraph operation is then scored, on native and static arms.
SETTLE_HOLD_SECONDS="${SETTLE_HOLD_SECONDS:-10}"
# Money prices (OBJ-2), written by 18_calibrate_prices.sh on the node. 03
# copies the file into every arm and records its hash in the fingerprint; an
# arm run before it exists is scored without J (04: "no prices").
PRICES_FILE="${PRICES_FILE:-$DBBENCH_BUILD_DIR/prices.json}"
# 18's tree: keys loaded, operations per read benchmark, and T.
PRICE_KEYS="${PRICE_KEYS:-1000000}"
PRICE_READS="${PRICE_READS:-1000000}"
PRICE_SIZE_RATIO="${PRICE_SIZE_RATIO:-10}"

# Output. Put DB_ROOT on the storage device being evaluated, not /tmp.
RUN_NAME="${RUN_NAME:-$(date +%Y%m%d-%H%M%S)}"
# CMP-8: comparisons pair runs of one session; recorded in metadata.env.
# Empty: 03 takes the session a resumed RESULTS_ROOT recorded, else RUN_NAME.
SESSION_ID="${SESSION_ID:-}"
RESULTS_ROOT="${RESULTS_ROOT:-results/dbbench_pipeline/$RUN_NAME}"
DB_ROOT="${DB_ROOT:-.dbbench_pipeline_dbs/$RUN_NAME}"
KEEP_DATABASES="${KEEP_DATABASES:-0}"
RESUME="${RESUME:-0}"
ALTERNATE_ARM_ORDER="${ALTERNATE_ARM_ORDER:-1}"

# Trigger-only protocol. This is intentionally not environment-overridable:
# the RL policy decides whether/which level may compact, while RocksDB's
# configured leveled picker chooses the input SST files.
readonly RL_PROTOCOL_VERSION=2
RL_DECISION_INTERVAL_MS="${RL_DECISION_INTERVAL_MS:-50}"
RL_OBSERVE_INTERVAL_MS="${RL_OBSERVE_INTERVAL_MS:-$RL_DECISION_INTERVAL_MS}"
RL_SOCKET_TIMEOUT_MS="${RL_SOCKET_TIMEOUT_MS:-250}"
RL_STRUCTURAL_DIRTY_DEADLINE_MS="${RL_STRUCTURAL_DIRTY_DEADLINE_MS:-250}"
# Minimum score at which a below-threshold compact action is offered by the
# Python mask and granted an optional token by the C++ bridge. One value feeds
# both sides so the learner's action set matches what admission will honour.
RL_OPTIONAL_MIN_SCORE="${RL_OPTIONAL_MIN_SCORE:-0.10}"
# Review decision 6: L0 stays non-deferring during bridge validation, matching
# native leveled behavior on the axis that drives stalls. Set to 1 only for a
# deliberate learned-L0 experiment.
RL_L0_ALLOW_DEFER="${RL_L0_ALLOW_DEFER:-0}"
# D-9 (2026-09-23). The LEARNED arms (rl, unconstrained_rl) may defer a due
# L0. The L0 trigger is the static class's main write/read lever, and under the
# posture above a learned policy could only ever compact L0 EARLIER than
# native, never later -- the write-costly direction, with the write-saving one
# withheld. prior_only and unconstrained_prior_only keep the posture above so
# the D-4/D-5 record stays comparable. On the enforced arm the guard's L0
# limits and its l0_slowdown term still bound the deferral; the unconstrained
# arm shows the lever's unbounded effect.
RL_L0_ALLOW_DEFER_LEARNED="${RL_L0_ALLOW_DEFER_LEARNED:-1}"
# D3a. A `defer` selected while a level was BELOW its trigger expressed no
# judgement about a due level, so it does not bind once the level crosses;
# the level is admitted under the kPosture reason and the transition stays
# valid for replay. Set to "defer" to restore the binding behaviour for the
# ablation. RocksDB already wakes its scheduler on the crossing, so this is
# what removes trigger latency rather than merely shortening it.
RL_CROSSING_POSTURE="${RL_CROSSING_POSTURE:-compact}"
# D6. Seconds over which exploration anneals to its floor, expressed as a
# fraction of expected run duration. A decision-count schedule has been
# invalidated by every repair to the observation cadence so far; wall time is
# invariant to it. 0 keeps the legacy step schedule.
RL_EXPLORATION_ANNEAL_FRACTION="${RL_EXPLORATION_ANNEAL_FRACTION:-0.3333}"
# Fallback when the paired regular arm has not run yet. ALTERNATE_ARM_ORDER
# puts the regular arm second on odd pairs, so this is taken on roughly half of
# all pairs and is not an edge case.
RL_EXPECTED_RUN_SECONDS_PER_MOP="${RL_EXPECTED_RUN_SECONDS_PER_MOP:-30}"
# Measured admission-latency budget (H_i + epsilon). Zero measures and reports
# epsilon without failing on it; set a bound once a run has established the
# distribution on the target machine.
RL_EPSILON_BOUND_MS="${RL_EPSILON_BOUND_MS:-0}"
# Space budget rung the run executes (P0-6 ladder 0 / 0.02 / 0.05 / 0.10).
# Stamped into metadata.env so 07_evaluate_paired.py scores the cell at the
# rung it ran, and passed to the learner so its space hinge is trained
# against the bound it will be judged at. A rung is a separate run of the
# learned arms; regular and prior_only are shared across rungs (Gate 3b).
SPACE_RELATIVE_MARGIN="${SPACE_RELATIVE_MARGIN:-0.02}"
POLICY_SEED_BASE="${POLICY_SEED_BASE:-10000}"
BASELINE_SLO_DIR="${BASELINE_SLO_DIR:-baseline_slo}"
RL_REQUIRE_BASELINE_SLO="${RL_REQUIRE_BASELINE_SLO:-1}"

# db_bench flags every measured run shares: geometry and instruments
# (DBBENCH_COMMON), and the workload's mix, sizes and skew (DBBENCH_WORKLOAD).
# 03 and the preflight's native-parity runs (22) both build their commands
# from these, so ACT-4 is judged at the experiments' geometry. Upstream flags
# only: ACT-4's stock binary must accept every one. 03 validates the skew
# settings before calling this.
dbbench_shared_flags() {
  DBBENCH_COMMON=(
    --threads="$THREADS"
    --key_size="$KEY_SIZE"
    --value_size="$VALUE_SIZE"
    --disable_wal="$DISABLE_WAL"
    --use_direct_reads="$USE_DIRECT_IO"
    --use_direct_io_for_flush_and_compaction="$USE_DIRECT_IO"
    --compression_type=none
    --write_buffer_size="$WRITE_BUFFER_SIZE"
    --target_file_size_base="$TARGET_FILE_SIZE"
    --max_bytes_for_level_base="$MAX_BYTES_FOR_LEVEL_BASE"
    --level_compaction_dynamic_level_bytes=false
    --num_levels="$NUM_LEVELS"
    --max_background_jobs="$MAX_BACKGROUND_JOBS"
    --open_files="$OPEN_FILES"
    --cache_size="$BLOCK_CACHE_SIZE"
    --block_size="$BLOCK_SIZE"
    --bloom_bits="$BLOOM_BITS"
    --soft_pending_compaction_bytes_limit="$SOFT_PENDING_BYTES"
    --hard_pending_compaction_bytes_limit="$HARD_PENDING_BYTES"
    --statistics
    --histogram
    --perf_level=1
    --stats_dump_period_sec="$STATS_DUMP_PERIOD_SECONDS"
  )
  DBBENCH_WORKLOAD=(
    --mix_get_ratio="$MIX_GET_RATIO"
    --mix_put_ratio="$MIX_PUT_RATIO"
    --mix_seek_ratio="$MIX_SEEK_RATIO"
    --value_theta="$VALUE_THETA" --value_k="$VALUE_K" --value_sigma="$VALUE_SIGMA"
    --mix_max_value_size="$MIX_MAX_VALUE_SIZE"
    --iter_theta="$SCAN_LENGTH" --iter_k="$ITER_K" --iter_sigma="$ITER_SIGMA"
    --mix_max_scan_len="$MIX_MAX_SCAN_LENGTH"
  )
  if [[ "$WORKLOAD_SKEW" == "1" ]]; then
    DBBENCH_WORKLOAD+=(
      --keyrange_num="$KEYRANGE_NUM"
      --keyrange_dist_a="$KEYRANGE_DIST_A"
      --keyrange_dist_b="$KEYRANGE_DIST_B"
      --keyrange_dist_c="$KEYRANGE_DIST_C"
      --keyrange_dist_d="$KEYRANGE_DIST_D"
      --key_dist_a="$KEY_DIST_A"
      --key_dist_b="$KEY_DIST_B"
    )
  elif [[ "$WORKLOAD_SKEW" == "2" ]]; then
    # keyrange_dist_* stay at db_bench's default 0, so no prefix modelling.
    DBBENCH_WORKLOAD+=(--keyrange_num=1 --key_dist_a="$KEY_DIST_A"
                       --key_dist_b="$KEY_DIST_B")
  else
    DBBENCH_WORKLOAD+=(--keyrange_num=1)
  fi
}
