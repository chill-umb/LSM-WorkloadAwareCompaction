# shellcheck shell=bash disable=SC2034
# Every default for the node build scripts, in one place. Each value can be
# overridden from the environment, e.g.
#   BUILD_JOBS=8 scripts/dbbench_pipeline/01_build.sh release
# Relative paths are relative to the repository root; every script cds there.

# RocksDB source tree to build. Point it at a separate checkout of the stock
# commit to build arm A of the A/B harness (architecture section 16.2). It must
# be its own git checkout (e.g. `git -C lib/rocksdb worktree add ...`), not a
# copied directory; 01_build.sh refuses a copy.
ROCKSDB_SRC="${ROCKSDB_SRC:-lib/rocksdb}"

# Out-of-tree CMake build directories. Both sit under build-dbbench/, which the
# root .gitignore already ignores.
RELEASE_BUILD_DIR="${RELEASE_BUILD_DIR:-build-dbbench/release}"
DEBUG_BUILD_DIR="${DEBUG_BUILD_DIR:-build-dbbench/debug}"

# CMake build type of the release tree (Release = -O3 -DNDEBUG). 01_build.sh
# refuses one that leaves assertions on, and pins the flags of Release only
# (RelWithDebInfo/MinSizeRel would use whatever the cache holds). The debug
# tree is always Debug: RocksDB's CMakeLists.txt only allows WITH_TESTS=ON with
# CMAKE_BUILD_TYPE=Debug.
RELEASE_BUILD_TYPE="${RELEASE_BUILD_TYPE:-Release}"

# Target microarchitecture, passed to RocksDB's PORTABLE cache variable (0 =
# -march=native, 1 = baseline, otherwise -march=<value>). RocksDB matches 0/1
# with unanchored regexes, so a value such as znver1 or NONE is misread;
# 01_build.sh refuses a build whose compile command lacks -march=<value>.
# -march=native silently resolves to an older target when the compiler does not
# know the host CPU; naming the target turns that into a build-time failure
# instead. The measurement nodes are Chameleon CHI@NCAR Zen 5, so znver5, which
# needs GCC 14.1+ or Clang 19+.
ROCKSDB_PORTABLE="${ROCKSDB_PORTABLE:-znver5}"

# C++ compiler. 00_install_dependencies.sh points `c++` at g++-$GCC_MAJOR when
# the installed default cannot target $ROCKSDB_PORTABLE.
CXX="${CXX:-c++}"
GCC_MAJOR="${GCC_MAJOR:-14}"

BUILD_JOBS="${BUILD_JOBS:-$(nproc)}"
PYTHON_VENV="${PYTHON_VENV:-.venv-dbbench}"
