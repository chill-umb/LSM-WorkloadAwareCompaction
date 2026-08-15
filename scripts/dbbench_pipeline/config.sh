#!/usr/bin/env bash
# One configuration file for the complete db_bench experiment pipeline.
# All sizes are bytes unless the variable name says otherwise.

# Workload matrix.
WORKLOAD_SIZES_M="${WORKLOAD_SIZES_M:-10 20 30 40 50}"
SIZE_RATIOS="${SIZE_RATIOS:-2 6 10}"

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
RL_ALLOW_DEFER="${RL_ALLOW_DEFER:-1}"
RL_MAX_DEFER_STEPS="${RL_MAX_DEFER_STEPS:-50}"
RL_MAX_DEFER_STEPS_L0="${RL_MAX_DEFER_STEPS_L0:-1}"
RL_DECISION_INTERVAL_MS="${RL_DECISION_INTERVAL_MS:-50}"
RL_OBSERVE_INTERVAL_MS="${RL_OBSERVE_INTERVAL_MS:-$RL_DECISION_INTERVAL_MS}"
RL_SOCKET_TIMEOUT_MS="${RL_SOCKET_TIMEOUT_MS:-250}"
POLICY_SEED_BASE="${POLICY_SEED_BASE:-10000}"
