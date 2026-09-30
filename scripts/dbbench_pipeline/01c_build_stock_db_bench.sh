#!/usr/bin/env bash
# Build the "stock" db_bench of ACT-4 (PATHWAYS Pathway A §6): upstream
# RocksDB at STOCK_ROCKSDB_COMMIT, with none of the fork's changes, configured
# and built by 01 and 02 exactly as the patched binary is. The source is
# exported once from the submodule's own history; later calls reuse it and
# rebuild incrementally.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

stamp="$STOCK_SOURCE_DIR/.stock_commit"
if [[ -e "$STOCK_SOURCE_DIR" && ! -f "$stamp" ]]; then
  echo "$STOCK_SOURCE_DIR exists but was not exported by 01c; move it away." >&2
  exit 1
fi
if [[ -f "$stamp" && "$(<"$stamp")" != "$STOCK_ROCKSDB_COMMIT" ]]; then
  echo "[stock] $STOCK_SOURCE_DIR holds $(<"$stamp"); re-exporting"
  rm -rf "$STOCK_SOURCE_DIR" "$STOCK_BUILD_DIR"
fi
if [[ ! -f "$stamp" ]]; then
  echo "[stock] exporting RocksDB $STOCK_ROCKSDB_COMMIT to $STOCK_SOURCE_DIR"
  mkdir -p "$STOCK_SOURCE_DIR"
  git -C lib/rocksdb archive --format=tar "$STOCK_ROCKSDB_COMMIT" |
    tar -x -C "$STOCK_SOURCE_DIR"
  printf '%s\n' "$STOCK_ROCKSDB_COMMIT" > "$stamp"
fi

ROCKSDB_SOURCE_DIR="$STOCK_SOURCE_DIR" DBBENCH_BUILD_DIR="$STOCK_BUILD_DIR" \
  SKIP_SUBMODULES=1 "$PIPELINE_DIR/01_build_rocksdb.sh"
DBBENCH_BUILD_DIR="$STOCK_BUILD_DIR" "$PIPELINE_DIR/02_build_db_bench.sh"
printf 'rocksdb_commit=%s\n' "$STOCK_ROCKSDB_COMMIT" \
  >> "$STOCK_BUILD_DIR/build_provenance.env"
echo "[stock] db_bench sha256: $(sha256sum "$STOCK_BUILD_DIR/db_bench" | awk '{print $1}')"
