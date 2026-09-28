#!/usr/bin/env bash
# Build RocksDB binaries out of tree and record provenance next to each one.
#
#   01_build.sh release [target...]   default target db_bench; for experiment runs
#   01_build.sh debug <target>...     gtest binaries by name, assertions on, e.g.
#                                     01_build.sh debug version_set_test compaction_picker_test
#
# CMake, not RocksDB's Makefile, for both trees:
#   - The Makefile builds inside the source tree, and switching between release
#     (DEBUG_LEVEL=0) and debug needs a `make clean` in between
#     (lib/rocksdb/CLAUDE.md). CMake builds out of tree, so the release and
#     debug trees coexist and lib/rocksdb stays clean, which keeps the
#     rocksdb_dirty field below meaningful.
#   - CMake's Debug type is RocksDB's own test configuration: WITH_TESTS is only
#     allowed with it, NDEBUG stays undefined (assert and TEST_SYNC_POINT live)
#     and RTTI is on. Every test in CMakeLists.txt's TESTS list is its own
#     target, and its binary lands at the top of the build tree.
#   - PORTABLE=<march> is a CMake cache variable.
# The cost: a new .cc file must be registered in lib/rocksdb/CMakeLists.txt,
# which RocksDB's conventions require anyway (with src.mk, Makefile and BUCK).
# New tests go in the top-level TESTS list; a target defined in a subdirectory
# CMakeLists.txt lands in a subdirectory and is reported missing below.
#
# ROCKSDB_BUILD_SHARED=OFF links RocksDB statically, so a binary's sha256
# covers the engine code too, and a stock and a patched db_bench can never pick
# up each other's librocksdb.so.
#
# Heads-up for new code: RocksDB's USE_RTTI=AUTO builds the release tree with
# -fno-rtti and the debug tree with RTTI, so dynamic_cast/typeid in new code
# passes its debug tests and then fails to compile in the release db_bench.
set -euo pipefail
trap 'echo "01_build.sh: FAILED (exit $?) at line $LINENO: $BASH_COMMAND" >&2' ERR

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PIPELINE_DIR/../.."
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

usage="usage: $0 release [target...] | debug <test_target>..."
mode="${1:-}"
case "$mode" in
  release)
    shift
    build_dir="$RELEASE_BUILD_DIR"; build_type="$RELEASE_BUILD_TYPE"; with_tests=OFF
    (( $# )) || set -- db_bench
    ;;
  debug)
    shift
    build_dir="$DEBUG_BUILD_DIR"; build_type=Debug; with_tests=ON
    (( $# )) || { echo "$usage" >&2; echo "Name at least one test target, e.g. version_set_test." >&2; exit 2; }
    ;;
  *)
    echo "$usage" >&2; exit 2 ;;
esac
targets=("$@")

[[ -f "$ROCKSDB_SRC/CMakeLists.txt" ]] || {
  echo "$ROCKSDB_SRC is not checked out. Run: git submodule update --init lib/rocksdb" >&2
  exit 1
}
# For a plain directory inside another repo, git answers with the parent
# repo's commit and status, so the provenance would name the wrong source.
src_top="$(git -C "$ROCKSDB_SRC" rev-parse --show-toplevel)"
[[ "$src_top" == "$(cd "$ROCKSDB_SRC" && pwd -P)" ]] || {
  echo "$ROCKSDB_SRC is not its own git checkout (git resolves it to $src_top)." >&2
  echo "Use a git checkout or 'git -C lib/rocksdb worktree add <dir> <commit>', not a copy." >&2
  exit 1
}
cxx="$(command -v "$CXX")" || {
  echo "Compiler '$CXX' not found. Run 00_install_dependencies.sh or set CXX." >&2
  exit 1
}
if [[ "$ROCKSDB_PORTABLE" != [01] ]] &&
   ! "$cxx" -march="$ROCKSDB_PORTABLE" -E -x c++ /dev/null -o /dev/null >/dev/null 2>&1; then
  echo "$cxx does not support -march=$ROCKSDB_PORTABLE: $("$cxx" --version | sed -n 1p)" >&2
  echo "znver5 needs GCC 14.1+ or Clang 19+. Run 00_install_dependencies.sh or set CXX," >&2
  echo "or set ROCKSDB_PORTABLE=znver4 and record the deviation." >&2
  exit 1
fi
cxx_version="$("$cxx" --version | sed -n 1p)"
cxx_resolved="$(readlink -f "$cxx")"
# options_settable_test.cc wraps its whole body in #ifndef __clang__.
if [[ "$cxx_version" == *clang* && " ${targets[*]} " == *" options_settable_test "* ]]; then
  echo "options_settable_test compiles to zero tests under clang (options_settable_test.cc:35);" >&2
  echo "build the debug tree with GCC." >&2
  exit 1
fi

# CMake caches the compiler's identity and flag checks per build tree, and make
# does not rebuild objects when /usr/bin/c++ is re-pointed. A tree configured by
# another compiler, or not by this script, would mix old and new objects.
compiler_id="$cxx_resolved | $cxx_version"
stamp="$build_dir/compiler.id"
# Keyed on any content, not on CMakeCache.txt: deleting only the cache leaves the
# old objects behind.
if [[ -d "$build_dir" && -n "$(ls -A "$build_dir")" ]] && [[ ! -f "$stamp" || "$(<"$stamp")" != "$compiler_id" ]]; then
  echo "$build_dir was configured with a different compiler, or not by this script." >&2
  echo "  now: $compiler_id" >&2
  if [[ -f "$stamp" ]]; then echo "  was: $(<"$stamp")" >&2; fi
  echo "Start it fresh: rm -rf \"$build_dir\"" >&2
  exit 1
fi
mkdir -p "$build_dir"
printf '%s\n' "$compiler_id" > "$stamp"

# Source state is read before compiling, so it describes what was compiled.
# --untracked-files=all: a new, unadded .cc file counts even when the user's
# git config sets status.showUntrackedFiles=no.
rocksdb_commit="$(git -C "$ROCKSDB_SRC" rev-parse HEAD)"
rocksdb_status="$(git -C "$ROCKSDB_SRC" status --porcelain --untracked-files=all)"
if [[ -n "$rocksdb_status" ]]; then rocksdb_dirty=1; else rocksdb_dirty=0; fi
root_commit="$(git rev-parse HEAD)"

# A stale provenance file must never sit next to a freshly linked binary.
for t in "${targets[@]}"; do rm -f "$build_dir/$t.provenance"; done

# Every setting that changes the code or the tests is passed on every run, so
# neither a value left in CMakeCache.txt by an earlier configure nor CXXFLAGS/
# LDFLAGS in the environment (CMake seeds its cache from them) can leak in.
# The two per-type flag values are CMake's own GCC/Clang defaults.
# WITH_LIBURING=OFF: otherwise io_uring support depends on whether liburing-dev
# happens to be installed. FAIL_ON_WARNINGS=OFF: a warning that a newer GCC
# raises in upstream code must not stop the build.
echo "[configure] $build_dir ($build_type, -march=$ROCKSDB_PORTABLE, $cxx_version)"
cmake -S "$ROCKSDB_SRC" -B "$build_dir" \
  -DCMAKE_BUILD_TYPE="$build_type" \
  -DCMAKE_CXX_COMPILER="$cxx" \
  -DCMAKE_CXX_FLAGS= \
  "-DCMAKE_CXX_FLAGS_RELEASE=-O3 -DNDEBUG" \
  -DCMAKE_CXX_FLAGS_DEBUG=-g \
  -DCMAKE_EXE_LINKER_FLAGS= \
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  -DPORTABLE="$ROCKSDB_PORTABLE" \
  -DROCKSDB_BUILD_SHARED=OFF \
  -DWITH_GFLAGS=ON \
  -DWITH_TESTS="$with_tests" \
  -DWITH_LIBURING=OFF \
  -DWITH_SNAPPY=OFF -DWITH_LZ4=OFF -DWITH_ZLIB=OFF -DWITH_ZSTD=OFF -DWITH_BZ2=OFF \
  -DWITH_JEMALLOC=OFF -DWITH_ASAN=OFF -DWITH_TSAN=OFF -DWITH_UBSAN=OFF \
  -DASSERT_STATUS_CHECKED=OFF -DUSE_RTTI=AUTO \
  -DWITH_PERF_CONTEXT=ON -DWITH_IOSTATS_CONTEXT=ON \
  -DFAIL_ON_WARNINGS=OFF

# The exact compile command of one library source (every RocksDB library source
# gets the same flags), read from the configured tree, so a wrong setting fails
# before anything compiles.
compile_command="$(python3 - "$build_dir/compile_commands.json" <<'PY'
import json, sys
hits = [e["command"] for e in json.load(open(sys.argv[1]))
        if e["file"].endswith("/db/version_set.cc")]
if len(hits) != 1:
    sys.exit(f"expected one compile command for db/version_set.cc, found {len(hits)}")
print(hits[0])
PY
)"
case "$mode" in
  release) [[ "$compile_command" == *" -DNDEBUG "* ]] || {
             echo "Release tree is configured without -DNDEBUG (build type $build_type); not fit for measurement." >&2
             exit 1; } ;;
  debug)   [[ "$compile_command" != *" -DNDEBUG "* ]] || {
             echo "Debug tree is configured with -DNDEBUG: assertions and sync points would be off." >&2
             exit 1; } ;;
esac
# RocksDB matches PORTABLE with unanchored regexes (CMakeLists.txt:293,303), so
# a value such as "znver1" or "NONE" silently means baseline or native. Only
# the flag in the compile command proves which target was used.
[[ "$ROCKSDB_PORTABLE" == [01] || "$compile_command" == *" -march=$ROCKSDB_PORTABLE "* ]] || {
  echo "-march=$ROCKSDB_PORTABLE is not in the compile command; RocksDB read PORTABLE=$ROCKSDB_PORTABLE as 0 or 1." >&2
  exit 1
}

echo "[build] ${targets[*]} with $BUILD_JOBS jobs"
cmake --build "$build_dir" --parallel "$BUILD_JOBS" --target "${targets[@]}"

for t in "${targets[@]}"; do
  bin="$build_dir/$t"
  [[ -f "$bin" && -x "$bin" ]] || {
    echo "Build finished but the expected binary $bin is missing. Only targets defined in" >&2
    echo "$ROCKSDB_SRC/CMakeLists.txt itself (db_bench, the TESTS list) land at the build-tree root." >&2
    exit 1
  }
  sha256="$(sha256sum "$bin")"
  # The -march check above proves the target. This proves AVX-512 code
  # actually reached the binary (znver4 and znver5 both enable it; a znver4
  # build passes too). Zero AVX-512-only opcodes (mask moves, compress,
  # ternary logic) means the flag never reached code generation. Release only:
  # -O0 code may legitimately contain none.
  avx512=not_checked
  if [[ "$mode" == release && "$ROCKSDB_PORTABLE" == znver[45] ]]; then
    avx512="$(objdump -d "$bin" | awk '/kmov|vpcompress|vpternlog/ {n++} END {print n+0}')"
    (( avx512 > 0 )) || {
      echo "$bin has no AVX-512 instructions although ROCKSDB_PORTABLE=$ROCKSDB_PORTABLE." >&2
      exit 1
    }
  fi
  # binary_sha256 identifies this file only: RocksDB embeds a per-tree build
  # date, so two builds of the same source never hash equal.
  printf '%s=%q\n' \
    mode "$mode" \
    binary "$bin" \
    binary_sha256 "${sha256%% *}" \
    build_type "$build_type" \
    rocksdb_portable "$ROCKSDB_PORTABLE" \
    rocksdb_source "$ROCKSDB_SRC" \
    rocksdb_commit "$rocksdb_commit" \
    rocksdb_dirty "$rocksdb_dirty" \
    root_commit "$root_commit" \
    compiler "$cxx" \
    compiler_resolved "$cxx_resolved" \
    compiler_version "$cxx_version" \
    rocksdb_compile_command "$compile_command" \
    avx512_instruction_count "$avx512" \
    > "$bin.provenance"
  echo "[done] $bin (sha256 ${sha256%% *}, rocksdb $rocksdb_commit, dirty=$rocksdb_dirty)"
done
