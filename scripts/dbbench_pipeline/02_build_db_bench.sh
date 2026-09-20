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

# -march=znver4/znver5 both enable AVX-512. The preflight in 01 only proves the
# compiler *accepts* the flag; this proves the flag *reached* the binary, by
# counting opcodes that exist only in AVX-512 (mask-register moves, compress,
# ternary logic). Zero means the build silently fell back to a generic target
# and every later measurement would be on the wrong microarchitecture.
avx512_count="$(objdump -d "$DBBENCH_BUILD_DIR/db_bench" | grep -cE 'kmov|vpcompress|vpternlog' || true)"
case "$ROCKSDB_PORTABLE" in
  znver4|znver5)
    (( avx512_count > 0 )) || {
      echo "db_bench contains no AVX-512 instructions but ROCKSDB_PORTABLE=$ROCKSDB_PORTABLE." >&2
      echo "The march did not reach the binary; check the CMake cache and CXX." >&2
      exit 1
    }
    ;;
esac
printf 'avx512_instruction_count=%s\n' "$avx512_count" >> "$DBBENCH_BUILD_DIR/build_provenance.env"
echo "[build] AVX-512 instructions in db_bench: $avx512_count"

echo
echo "db_bench built: $DBBENCH_BUILD_DIR/db_bench"
echo "Next: scripts/dbbench_pipeline/03_run_experiments.sh"

