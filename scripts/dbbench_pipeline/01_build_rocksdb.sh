#!/usr/bin/env bash
# Configure the modified RocksDB tree and build its static/shared libraries.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

SKIP_SUBMODULES="${SKIP_SUBMODULES:-0}"
if [[ "$SKIP_SUBMODULES" != "1" ]]; then
  echo "[submodules] initializing lib/rocksdb"
  git submodule update --init --recursive
fi

echo "[configure] $DBBENCH_BUILD_DIR"
cmake -S lib/rocksdb -B "$DBBENCH_BUILD_DIR" \
  -DCMAKE_BUILD_TYPE=Release \
  -DWITH_GFLAGS=ON \
  -DWITH_BENCHMARK_TOOLS=ON \
  -DWITH_TESTS=OFF \
  -DWITH_TOOLS=OFF \
  -DFAIL_ON_WARNINGS=OFF \
  -DPORTABLE=0

echo "[build] RocksDB libraries"
cmake --build "$DBBENCH_BUILD_DIR" \
  --target rocksdb rocksdb-shared \
  --parallel "$BUILD_JOBS"

echo
echo "RocksDB built. Next: scripts/dbbench_pipeline/02_build_db_bench.sh"

