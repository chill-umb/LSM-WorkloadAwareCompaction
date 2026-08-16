#!/usr/bin/env bash
# One configuration file for the complete db_bench experiment pipeline.
# All sizes are bytes unless the variable name says otherwise.

# Workload matrix.
WORKLOAD_PROFILE="${WORKLOAD_PROFILE:-balanced-v1}"
WORKLOAD_SIZES_M="${WORKLOAD_SIZES_M:-10 20 30 40 50}"
SIZE_RATIOS="${SIZE_RATIOS:-2 6 10}"
EXPERIMENT_ARMS="${EXPERIMENT_ARMS:-regular rl}"
REPEATS="${REPEATS:-1}"

# The 5M balanced workload expressed as db_bench phases:
#   29% initial unique inserts, followed by 71% mixed operations.
LOAD_PERCENT="${LOAD_PERCENT:-29}"
MIX_GET_RATIO="${MIX_GET_RATIO:-0.5211267606}"
MIX_PUT_RATIO="${MIX_PUT_RATIO:-0.1549295775}"
MIX_SEEK_RATIO="${MIX_SEEK_RATIO:-0.3239436620}"
SCAN_LENGTH="${SCAN_LENGTH:-32}"
MIX_MAX_SCAN_LENGTH="${MIX_MAX_SCAN_LENGTH:-10000}"

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
DBBENCH_BUILD_DIR="${DBBENCH_BUILD_DIR:-build-dbbench}"
PYTHON_VENV="${PYTHON_VENV:-.venv-dbbench}"
BUILD_JOBS="${BUILD_JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)}"

# Output. Put DB_ROOT on the storage device being evaluated, not /tmp.
RUN_NAME="${RUN_NAME:-$(date +%Y%m%d-%H%M%S)}"
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
# Measured admission-latency budget (H_i + epsilon). Zero measures and reports
# epsilon without failing on it; set a bound once a run has established the
# distribution on the target machine.
RL_EPSILON_BOUND_MS="${RL_EPSILON_BOUND_MS:-0}"
POLICY_SEED_BASE="${POLICY_SEED_BASE:-10000}"
BASELINE_SLO_DIR="${BASELINE_SLO_DIR:-baseline_slo}"
RL_REQUIRE_BASELINE_SLO="${RL_REQUIRE_BASELINE_SLO:-1}"
