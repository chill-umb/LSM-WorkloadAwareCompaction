#!/usr/bin/env bash
# Tier 2 (plan §6.1): build the fork's own gtest files in a separate Debug
# tree, so RocksDB's asserts fire, and run them. The measured Release tree of
# 01 never carries test code. A listed test whose source does not exist yet is
# skipped; one that fails to build or run stops the script.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

# The plugin's tests (controller/tests, plan §6.2) join here with the plugin.
if [[ -d controller ]]; then
  echo "controller/ exists but its tests are not wired into 01b yet" \
       "(plan §3 and §6.2)." >&2
  exit 1
fi

targets=()
for source in $FORK_TEST_SOURCES; do
  if [[ -f "lib/rocksdb/$source" ]]; then
    targets+=("$(basename "$source" .cc)")
  else
    echo "[tier2] SKIP $source (not written yet)"
  fi
done
if (( ${#targets[@]} == 0 )); then
  echo "[tier2] no fork tests yet; nothing to build."
  exit 0
fi

# WITH_TESTS is honoured only for Debug builds (lib/rocksdb/CMakeLists.txt).
# WITH_ALL_TESTS stays at its default ON, so every registered test target is
# defined; only the fork's are built.
echo "[configure] $DBBENCH_TEST_BUILD_DIR (Debug, tests on)"
cmake -S lib/rocksdb -B "$DBBENCH_TEST_BUILD_DIR" \
  -DCMAKE_BUILD_TYPE=Debug \
  -DWITH_TESTS=ON \
  -DWITH_GFLAGS=ON \
  -DWITH_BENCHMARK_TOOLS=OFF \
  -DWITH_TOOLS=OFF \
  -DFAIL_ON_WARNINGS=OFF \
  -DPORTABLE="$ROCKSDB_PORTABLE"

echo "[build] ${targets[*]}"
cmake --build "$DBBENCH_TEST_BUILD_DIR" --target "${targets[@]}" \
  --parallel "$BUILD_JOBS"

for target in "${targets[@]}"; do
  echo "[tier2] running $target"
  "$DBBENCH_TEST_BUILD_DIR/$target"
done
echo "[tier2] passed: ${targets[*]}"
