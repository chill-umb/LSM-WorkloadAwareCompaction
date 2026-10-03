#!/usr/bin/env bash
# The archived price trees of PREREGISTRATION D-22 (a) and (b), on the node
# only. 18 trees: T = 2, 3, 4, 6, 8 and 10, three builds of each; build b of
# every T forms set b. Each is built as D-15 §3(b) builds a price tree:
# filluniquerandom of the Programme 1 load (--seed=1), the pipeline's default
# options, open_files as the experiments, then settle, then levelstats, with
# age-based compaction off (D-22 a': --ttl_seconds=0
# --periodic_compaction_seconds=0). The builds run one at a time, pinned as
# every run is. A build whose settle does not end ok=1 is discarded and
# rebuilt, at most BUILD_ATTEMPTS times, and every attempt is kept in the
# build record.
#
# Then each tree becomes one .tgz (names sorted, numeric owners, gzip -n,
# the files' own time stamps kept), MANIFEST.sha256 lists every file of
# every tree and every .tgz, ARCHIVES.sha256 the .tgz alone, and every .tgz
# is unpacked again and checked against the manifest. The tree-set identity,
# the sha256 of MANIFEST.sha256, is printed and written into build_record.json
# for the dated build record.
#
#   CONFIRM_PRICE_TREES=YES PRICE_TREES=~/node_ops/price_trees/<date> \
#     DB_ROOT=/mnt/nvme/price-build scripts/dbbench_pipeline/29_build_price_trees.sh
#
# PRICE_TREES is the archive, outside the repository; it must not exist yet.
# DB_ROOT holds the built trees, which stay after the archive is verified
# (the archive is the authority; delete them when the owner agrees).
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

if [[ "${CONFIRM_PRICE_TREES:-}" != "YES" ]]; then
  echo "This builds and archives D-22's 18 price trees (about 25 minutes)." >&2
  echo "Set CONFIRM_PRICE_TREES=YES to start." >&2
  exit 2
fi
if [[ "$THREADS" != 1 ]]; then
  echo "THREADS=$THREADS; the price trees load on one thread, as 18 reads them (D-15 §3a)." >&2
  exit 2
fi
[[ -n "${PRICE_TREES:-}" ]] || {
  echo "PRICE_TREES (the archive folder, outside the repository) is not set." >&2
  exit 2
}
PRICE_TREES="$(realpath -m "$PRICE_TREES")"
case "$PRICE_TREES/" in
  "$PROJECT_ROOT"/*)
    echo "PRICE_TREES=$PRICE_TREES is inside the repository; D-22 (b) keeps" \
         "the archive outside it." >&2
    exit 2 ;;
esac
if [[ -e "$PRICE_TREES" ]]; then
  echo "$PRICE_TREES exists; an archive is never rebuilt in place." >&2
  exit 2
fi
BUILD_ATTEMPTS="${BUILD_ATTEMPTS:-3}"
[[ "$BUILD_ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || {
  echo "BUILD_ATTEMPTS must be a positive integer." >&2
  exit 2
}
if [[ "$PYTHON_VENV" = /* ]]; then
  PYTHON="$PYTHON_VENV/bin/python"
else
  PYTHON="$PROJECT_ROOT/$PYTHON_VENV/bin/python"
fi
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"
TREES_PY="$PIPELINE_DIR/price_trees.py"
DB_BENCH="$DBBENCH_BUILD_DIR/db_bench"
[[ -x "$DB_BENCH" ]] || { echo "Missing $DB_BENCH; run 01 and 02." >&2; exit 1; }
DB_BENCH_SHA256="$("$PYTHON" "$PIPELINE_DIR/preflight_marker.py" identity --db-bench "$DB_BENCH")"
launcher=()
if [[ -n "$DBBENCH_CPUS" ]]; then
  command -v taskset >/dev/null || { echo "taskset is not installed." >&2; exit 1; }
  launcher=(taskset -c "$DBBENCH_CPUS")
fi
BUILD_ROOT="$DB_ROOT/price-build"
if [[ -e "$BUILD_ROOT" ]]; then
  echo "$BUILD_ROOT exists; remove it or choose another DB_ROOT." >&2
  exit 2
fi
# Free space: 18 trees of about 3.1 GB built (DB_ROOT), their .tgz files
# (the archive) and one tree unpacked at a time to verify.
free_gb() { df --output=avail -BG "$1" | tail -n 1 | tr -dc 0-9; }
mkdir -p "$DB_ROOT" "$(dirname "$PRICE_TREES")"
db_free="$(free_gb "$DB_ROOT")"
archive_free="$(free_gb "$(dirname "$PRICE_TREES")")"
if [[ "$(df --output=target "$DB_ROOT" | tail -n 1)" == \
      "$(df --output=target "$(dirname "$PRICE_TREES")" | tail -n 1)" ]]; then
  need=$(( ${MIN_FREE_GB_BUILD:-70} + ${MIN_FREE_GB_ARCHIVE:-60} ))
  (( db_free >= need )) || {
    echo "only ${db_free} GB free for the trees and the archive; about $need" \
         "GB are needed (MIN_FREE_GB_BUILD, MIN_FREE_GB_ARCHIVE)." >&2
    exit 1
  }
else
  (( db_free >= ${MIN_FREE_GB_BUILD:-70} )) || {
    echo "only ${db_free} GB free under DB_ROOT; about" \
         "${MIN_FREE_GB_BUILD:-70} GB are needed (MIN_FREE_GB_BUILD)." >&2
    exit 1
  }
  (( archive_free >= ${MIN_FREE_GB_ARCHIVE:-60} )) || {
    echo "only ${archive_free} GB free for the archive; about" \
         "${MIN_FREE_GB_ARCHIVE:-60} GB are needed (MIN_FREE_GB_ARCHIVE)." >&2
    exit 1
  }
fi
mkdir -p "$PRICE_TREES/build" "$BUILD_ROOT"
dbbench_shared_flags
load_ops="$(programme1_load_operations)"
ratios=(2 3 4 6 8 10)
sets=(1 2 3)
stamp() { echo "=== $(date -u '+%F %T') UTC $*"; }

stamp "building 18 price trees into $BUILD_ROOT (archive $PRICE_TREES)"
for set in "${sets[@]}"; do
  for ratio in "${ratios[@]}"; do
    name="s$set/T$ratio"
    attempt=0
    while :; do
      attempt=$((attempt + 1))
      out="$PRICE_TREES/build/$name/attempt$attempt"
      mkdir -p "$out"
      rm -rf "${BUILD_ROOT:?}/$name"
      command=(
        ${launcher[@]+"${launcher[@]}"} "$DB_BENCH"
        --benchmarks=filluniquerandom,settle,levelstats --use_existing_db=0
        --num="$load_ops"
        --max_bytes_for_level_multiplier="$ratio"
        --level0_file_num_compaction_trigger="$L0_COMPACTION_TRIGGER"
        --level0_slowdown_writes_trigger="$L0_SLOWDOWN_TRIGGER"
        --level0_stop_writes_trigger="$L0_STOP_TRIGGER"
        --compaction_pri="$COMPACTION_PRIORITY"
        --rl_settle_hold_seconds="$SETTLE_HOLD_SECONDS"
        --ttl_seconds=0 --periodic_compaction_seconds=0
        --compaction_style=0 --db="$BUILD_ROOT/$name" --seed="$DBBENCH_SEED"
        "${DBBENCH_COMMON[@]}"
      )
      printf '%q ' "${command[@]}" > "$out/command.txt"
      stamp "$name attempt $attempt"
      status=0
      "${command[@]}" > "$out/stdout.txt" 2>&1 || status=$?
      # Kept only if db_bench ended cleanly, settle said ok=1 and levelstats
      # printed its table (price_trees.build_record reads both again).
      if (( status == 0 )) && grep -q '^RL_SETTLED ok=1' "$out/stdout.txt" &&
         "$PYTHON" "$TREES_PY" levels "$out/stdout.txt" > /dev/null; then
        break
      fi
      echo "[price trees] $name attempt $attempt (exit $status) did not end" \
           "cleanly with settle ok=1 and levelstats; discarded (see" \
           "$out/stdout.txt)" >&2
      if (( attempt >= BUILD_ATTEMPTS )); then
        echo "[price trees] $name: $BUILD_ATTEMPTS attempts, none settled;" \
             "stopping" >&2
        exit 1
      fi
    done
  done
done

stamp "archiving: one .tgz per tree"
archive_one() {  # $1=set, $2=ratio
  tar --sort=name --numeric-owner --owner=0 --group=0 --format=gnu \
    -C "$BUILD_ROOT" -cf - "s$1/T$2" | gzip -n > "$PRICE_TREES/s$1-T$2.tgz"
}
pids=()
for set in "${sets[@]}"; do
  for ratio in "${ratios[@]}"; do
    archive_one "$set" "$ratio" &
    pids+=($!)
  done
  for pid in "${pids[@]}"; do wait "$pid"; done
  pids=()
done

stamp "manifest"
TREE_SET="$("$PYTHON" "$TREES_PY" manifest --build-root "$BUILD_ROOT" \
  --archive-dir "$PRICE_TREES")"

stamp "verifying: every .tgz against ARCHIVES.sha256, then unpacked against the manifest"
(cd "$PRICE_TREES" && sha256sum --quiet -c "ARCHIVES.sha256")
VERIFY="$DB_ROOT/price-verify"
rm -rf "$VERIFY"
mkdir -p "$VERIFY"
for set in "${sets[@]}"; do
  for ratio in "${ratios[@]}"; do
    tar -xzf "$PRICE_TREES/s$set-T$ratio.tgz" -C "$VERIFY"
    "$PYTHON" "$TREES_PY" check --manifest "$PRICE_TREES/MANIFEST.sha256" \
      --root "$VERIFY" --tree "s$set/T$ratio" \
      --report "$PRICE_TREES/build/s$set/T$ratio/verify.json"
    rm -rf "${VERIFY:?}/s$set/T$ratio"
  done
done
rm -rf "$VERIFY"

"$PYTHON" "$TREES_PY" build-record --archive-dir "$PRICE_TREES" \
  --build-root "$BUILD_ROOT" --db-bench-sha256 "$DB_BENCH_SHA256" >/dev/null
stamp "done"
echo "[price trees] tree-set identity $TREE_SET"
echo "[price trees] archive $PRICE_TREES ($(du -sh "$PRICE_TREES" | cut -f1));" \
     "build record $PRICE_TREES/build_record.json"
