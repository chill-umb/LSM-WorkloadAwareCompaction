#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

usage() {
  cat <<'USAGE'
Usage:
  scripts/build_rocksdb.sh [options]

Builds the RocksDB-based db_runner and the Tectonic workload generator.

Outputs:
  bin/db_runner
  bin/tectonic-cli

Options:
  --build-dir PATH       CMake build directory. Default: build
  --build-type TYPE      CMake build type. Default: Release
  --jobs N               Parallel build jobs. Default: detected CPU count
  --target NAME          CMake build target. Default: db_runner
  --skip-submodules      Do not run git submodule update --init --recursive.
  --clean                Remove the build directory before configuring.
  --cmake-arg ARG        Extra argument passed to cmake configure. Repeatable.
  -h, --help             Show this help.

Environment overrides:
  BUILD_DIR              Same as --build-dir.
  BUILD_TYPE             Same as --build-type.
  BUILD_JOBS             Same as --jobs.
  BUILD_TARGET           Same as --target.
  SKIP_SUBMODULES        Set to 1 to skip submodule update.

Examples:
  scripts/build_rocksdb.sh

  scripts/build_rocksdb.sh --jobs 32 --build-type Release

  scripts/build_rocksdb.sh --clean --cmake-arg -DJEMALLOC=OFF
USAGE
}

detect_jobs() {
  getconf _NPROCESSORS_ONLN 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 1
}

require_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required command missing: $command_name" >&2
    exit 1
  fi
}

require_option_value() {
  local option="$1"
  local value="${2:-}"
  if [[ -z "$value" ]]; then
    echo "Missing value for $option" >&2
    exit 1
  fi
}

BUILD_DIR="${BUILD_DIR:-build}"
BUILD_TYPE="${BUILD_TYPE:-Release}"
BUILD_JOBS="${BUILD_JOBS:-$(detect_jobs)}"
BUILD_TARGET="${BUILD_TARGET:-db_runner}"
SKIP_SUBMODULES="${SKIP_SUBMODULES:-0}"
CLEAN_BUILD=0
cmake_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --build-dir)
      require_option_value "$1" "${2:-}"
      BUILD_DIR="$2"
      shift 2
      ;;
    --build-type)
      require_option_value "$1" "${2:-}"
      BUILD_TYPE="$2"
      shift 2
      ;;
    --jobs)
      require_option_value "$1" "${2:-}"
      BUILD_JOBS="$2"
      shift 2
      ;;
    --target)
      require_option_value "$1" "${2:-}"
      BUILD_TARGET="$2"
      shift 2
      ;;
    --skip-submodules)
      SKIP_SUBMODULES=1
      shift
      ;;
    --clean)
      CLEAN_BUILD=1
      shift
      ;;
    --cmake-arg)
      require_option_value "$1" "${2:-}"
      cmake_args+=("$2")
      shift 2
      ;;
    --*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      echo "Unexpected positional argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

require_command git
require_command cmake
require_command cargo

if [[ "$SKIP_SUBMODULES" != "1" ]]; then
  echo "[submodules] updating recursive submodules"
  git submodule update --init --recursive
else
  echo "[submodules] skipped"
fi

if [[ "$CLEAN_BUILD" == "1" ]]; then
  echo "[clean] removing $BUILD_DIR"
  rm -rf "$BUILD_DIR"
fi

mkdir -p bin

echo "[configure] build dir: $BUILD_DIR"
echo "[configure] build type: $BUILD_TYPE"
cmake -S . -B "$BUILD_DIR" \
  -DCMAKE_BUILD_TYPE="$BUILD_TYPE" \
  "${cmake_args[@]}"

echo "[build] target: $BUILD_TARGET"
echo "[build] jobs: $BUILD_JOBS"
cmake --build "$BUILD_DIR" \
  --target "$BUILD_TARGET" \
  --parallel "$BUILD_JOBS"

if [[ "$BUILD_TARGET" == "db_runner" || "$BUILD_TARGET" == "all" ]]; then
  if [[ ! -x bin/db_runner ]]; then
    echo "Expected build output missing: bin/db_runner" >&2
    exit 1
  fi
  if [[ ! -x bin/tectonic-cli ]]; then
    echo "Expected build output missing: bin/tectonic-cli" >&2
    exit 1
  fi
fi

echo
echo "Build complete."
echo "db_runner: bin/db_runner"
echo "tectonic-cli: bin/tectonic-cli"
