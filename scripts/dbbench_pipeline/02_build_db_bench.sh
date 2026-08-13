#!/usr/bin/env bash
# Build db_bench from the RocksDB configuration created by step 01.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

if [[ ! -f "$DBBENCH_BUILD_DIR/CMakeCache.txt" ]]; then
  echo "Missing $DBBENCH_BUILD_DIR/CMakeCache.txt" >&2
  echo "Run scripts/dbbench_pipeline/01_build_rocksdb.sh first." >&2
  exit 1
fi

cmake --build "$DBBENCH_BUILD_DIR" \
  --target db_bench \
  --parallel "$BUILD_JOBS"

if [[ ! -x "$DBBENCH_BUILD_DIR/db_bench" ]]; then
  echo "Expected executable missing: $DBBENCH_BUILD_DIR/db_bench" >&2
  exit 1
fi

echo
echo "db_bench built: $DBBENCH_BUILD_DIR/db_bench"
echo "Next: scripts/dbbench_pipeline/03_run_experiments.sh"

