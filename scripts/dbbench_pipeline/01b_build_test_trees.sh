#!/usr/bin/env bash
# Tier 2 (plan §6.1): build the fork's own gtest files in a separate Debug
# tree, so RocksDB's asserts fire, and run them. The measured Release tree of
# 01 never carries test code. A listed test whose source does not exist yet is
# skipped; one that fails to build or run stops the script. The controller
# plugin's tests (controller/tests, plan §6.2) build first, in their own
# Debug tree; they need only the fork's headers and bundled gtest.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

if [[ -d controller ]]; then
  echo "[configure] $CONTROLLER_TEST_BUILD_DIR (Debug, plugin tests)"
  cmake -S controller -B "$CONTROLLER_TEST_BUILD_DIR" \
    -DCMAKE_BUILD_TYPE=Debug \
    -DRL_CONTROLLER_TESTS=ON
  cmake --build "$CONTROLLER_TEST_BUILD_DIR" \
    --target rl_controller rl_controller_tests --parallel "$BUILD_JOBS"
  echo "[tier2] running rl_controller_tests"
  "$CONTROLLER_TEST_BUILD_DIR/rl_controller_tests"
  # rl_controller_host_test loads this Debug plugin into a real DB (plan
  # §6.2, "a plugin is loaded and unloaded"); without it that case fails.
  RL_TEST_PLUGIN="$(cd "$CONTROLLER_TEST_BUILD_DIR" && pwd)/librl_controller.so"
  export RL_TEST_PLUGIN
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
