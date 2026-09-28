#!/usr/bin/env bash
# Prepare a Chameleon node (Ubuntu 22.04/24.04) to build RocksDB: apt packages,
# a C++ compiler that accepts -march=$ROCKSDB_PORTABLE, and the Python venv.
# Safe to re-run; the venv is rebuilt from scratch every time.
set -euo pipefail
trap 'echo "00_install_dependencies.sh: FAILED (exit $?) at line $LINENO: $BASH_COMMAND" >&2' ERR

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PIPELINE_DIR/../.."
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

command -v apt-get >/dev/null || { echo "This script supports Ubuntu (apt-get) only." >&2; exit 1; }
sudo=()
[[ "$(id -u)" -eq 0 ]] || sudo=(sudo)

# build-essential: gcc, g++, make, and objdump (01_build.sh checks the binary's
# instructions with it). cmake: RocksDB needs 3.12+. libgflags-dev: db_bench and
# every gtest binary need gflags. No compression libraries: RocksDB's CMake
# builds without snappy/lz4/zstd/zlib/bz2 by default (every WITH_* is OFF), so
# the build succeeds without them and runs must pass --compression_type=none.
echo "[apt] installing build dependencies"
"${sudo[@]}" apt-get update
"${sudo[@]}" apt-get install -y build-essential cmake git libgflags-dev python3 python3-venv

# Ubuntu 24.04's default GCC 13 does not know znver5. Install g++-$GCC_MAJOR and
# point `c++`/`cc` at it; CMake uses those names. 24.04 carries g++-14 in its
# own archive; the testing PPA (which can also upgrade the system libstdc++) is
# added only when apt has no candidate, as on 22.04.
march_ok() {
  [[ "$ROCKSDB_PORTABLE" == [01] ]] ||
    "$CXX" -march="$ROCKSDB_PORTABLE" -E -x c++ /dev/null -o /dev/null >/dev/null 2>&1
}
if ! march_ok; then
  echo "[toolchain] $CXX rejects -march=$ROCKSDB_PORTABLE; installing g++-$GCC_MAJOR"
  candidate="$(apt-cache policy "g++-$GCC_MAJOR" | awk '/Candidate:/ {print $2}')"
  if [[ -z "$candidate" || "$candidate" == "(none)" ]]; then
    echo "[toolchain] g++-$GCC_MAJOR is not in the archive; adding ppa:ubuntu-toolchain-r/test"
    "${sudo[@]}" apt-get install -y software-properties-common
    "${sudo[@]}" add-apt-repository -y ppa:ubuntu-toolchain-r/test
    "${sudo[@]}" apt-get update
  fi
  "${sudo[@]}" apt-get install -y "gcc-$GCC_MAJOR" "g++-$GCC_MAJOR"
  "${sudo[@]}" update-alternatives --install /usr/bin/c++ c++ "/usr/bin/g++-$GCC_MAJOR" 100
  "${sudo[@]}" update-alternatives --set c++ "/usr/bin/g++-$GCC_MAJOR"
  "${sudo[@]}" update-alternatives --install /usr/bin/cc cc "/usr/bin/gcc-$GCC_MAJOR" 100
  "${sudo[@]}" update-alternatives --set cc "/usr/bin/gcc-$GCC_MAJOR"
fi
if ! march_ok; then
  echo "$CXX still rejects -march=$ROCKSDB_PORTABLE: $("$CXX" --version | sed -n 1p)" >&2
  echo "znver5 needs GCC 14.1+ or Clang 19+. Set CXX to such a compiler, or set" >&2
  echo "ROCKSDB_PORTABLE=znver4 and record the deviation." >&2
  exit 1
fi
echo "[toolchain] $CXX: $("$CXX" --version | sed -n 1p), accepts -march=$ROCKSDB_PORTABLE"

# --clear: packages from an earlier venv of the same name (the old pipeline's
# .venv-dbbench held torch and matplotlib) must not survive.
echo "[python] $PYTHON_VENV with numpy and pytest"
# --clear empties whatever directory PYTHON_VENV names, so only ever clear a venv.
if [[ -L "$PYTHON_VENV" ]] || [[ -e "$PYTHON_VENV" && ! -f "$PYTHON_VENV/pyvenv.cfg" ]]; then
  echo "PYTHON_VENV=$PYTHON_VENV is a symlink, or exists and is not a venv (no pyvenv.cfg); refusing to clear it." >&2
  exit 1
fi
python3 -m venv --clear "$PYTHON_VENV"
"$PYTHON_VENV/bin/python" -m pip install numpy pytest

echo
echo "Node prepared. Next: bash scripts/dbbench_pipeline/01_build.sh release"
