#!/usr/bin/env bash
# The controller's unit tests on this machine (plan §6.2; owner-approved
# 2026-10-01). Compiles only controller/ and the fork's bundled gtest against
# the fork's headers: it never builds or links RocksDB. The node builds the
# same tests in a Debug CMake tree (01b_build_test_trees.sh).
#
#   controller/tests/run_local.sh [gtest flags, e.g. --gtest_filter=Masks*]
#
# CONTROLLER_TEST_OUT  build output (default ${TMPDIR:-/tmp}/rl-controller-tests)
# ROCKSDB_DIR          the fork's source tree, read only (default lib/rocksdb)
set -Eeuo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
controller="$(cd "$here/.." && pwd)"
root="$(cd "$controller/.." && pwd)"
rocksdb="${ROCKSDB_DIR:-$root/lib/rocksdb}"
out="${CONTROLLER_TEST_OUT:-${TMPDIR:-/tmp}/rl-controller-tests}"
gtest="$rocksdb/third-party/gtest-1.8.1/fused-src"
mkdir -p "$out"

# This checkout's own include dir first, so a header added here wins over
# ROCKSDB_DIR's copy.
includes=(-I"$root/lib/rocksdb/include" -I"$rocksdb/include" -I"$controller")
common=(-std=c++20 -g -O1 -pthread)
warnings=(-Wall -Wextra -Wshadow -Werror)

if [[ ! -f "$out/gtest.a" || "$gtest/gtest/gtest-all.cc" -nt "$out/gtest.a" ]]; then
  echo "[build] gtest"
  g++ "${common[@]}" -isystem "$gtest" -c "$gtest/gtest/gtest-all.cc" -o "$out/gtest-all.o"
  g++ "${common[@]}" -isystem "$gtest" -c "$gtest/gtest/gtest_main.cc" -o "$out/gtest_main.o"
  ar rcs "$out/gtest.a" "$out/gtest-all.o" "$out/gtest_main.o"
fi

echo "[build] librl_controller.so"
g++ "${common[@]}" "${warnings[@]}" -fPIC -shared "${includes[@]}" \
  -Wl,--version-script="$controller/rl_controller.map" \
  "$controller"/*.cc -o "$out/librl_controller.so"
# The library exports exactly its two C entry points.
exported="$(nm -D --defined-only "$out/librl_controller.so" | awk '$2 ~ /[TWDBVR]/ {print $3}' | sort | tr '\n' ' ')"
if [[ "$exported" != "rl_controller_create rl_controller_destroy " ]]; then
  echo "[build] librl_controller.so exports more than its entry points: $exported" >&2
  exit 1
fi

echo "[build] rl_controller_tests"
g++ "${common[@]}" "${warnings[@]}" "${includes[@]}" -isystem "$gtest" \
  -DRL_CONTROLLER_FIXTURES="\"$here/fixtures\"" \
  "$controller"/*.cc "$here"/*_test.cc "$out/gtest.a" -o "$out/rl_controller_tests"

echo "[run] rl_controller_tests"
"$out/rl_controller_tests" "$@"
