#!/usr/bin/env bash
# Configure the modified RocksDB tree and build its static/shared libraries.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

BUILD_CXX="${CXX:-c++}"
if [[ "$ROCKSDB_PORTABLE" != "0" && "$ROCKSDB_PORTABLE" != "1" ]]; then
  if ! "$BUILD_CXX" -march="$ROCKSDB_PORTABLE" -E -x c++ /dev/null \
      -o /dev/null >/dev/null 2>&1; then
    echo "$BUILD_CXX does not support -march=$ROCKSDB_PORTABLE." >&2
    "$BUILD_CXX" --version | head -1 >&2
    echo "znver5 requires GCC 14.1+ or Clang 19+; build-essential on Ubuntu" >&2
    echo "22.04 gives GCC 11 and on 24.04 GCC 13, neither of which knows it." >&2
    echo "Install a newer toolchain and set CXX, or re-run with an explicit" >&2
    echo "ROCKSDB_PORTABLE=znver4 and record the deviation in the run manifest." >&2
    exit 1
  fi
fi

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
  -DPORTABLE="$ROCKSDB_PORTABLE"

# Consumed by 03_run_experiments.sh so every arm records the architecture its
# binary was compiled for, not merely the binary's hash.
{
  printf 'rocksdb_portable=%s\n' "$ROCKSDB_PORTABLE"
  printf 'build_cxx=%s\n' "$("$BUILD_CXX" --version | head -1)"
  printf 'build_host_cpu=%s\n' \
    "$(lscpu 2>/dev/null | awk -F: '/^Model name/ {gsub(/^[ \t]+/, "", $2); print $2; exit}')"
} > "$DBBENCH_BUILD_DIR/build_provenance.env"

echo "[build] RocksDB libraries, -march=$ROCKSDB_PORTABLE"
cmake --build "$DBBENCH_BUILD_DIR" \
  --target rocksdb rocksdb-shared \
  --parallel "$BUILD_JOBS"

echo
echo "RocksDB built. Next: scripts/dbbench_pipeline/02_build_db_bench.sh"

