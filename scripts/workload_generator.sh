#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

usage() {
  cat <<'USAGE'
Usage:
  scripts/generate_workload.sh <workload_spec_path>

Generates one workload from a JSON spec and stores it in the repository's
workloads/ directory.

Output filename:
  workloads/workload_<workload size>_<workload type>.txt

Examples:
  scripts/generate_workload.sh workload_specs/1M/mixed/w_l0_1m.spec.json
  scripts/generate_workload.sh workload_specs/1M/write-only/w_l0_1m_write_only.spec.json

Environment overrides:
  TECTONIC_BIN       default: ./bin/tectonic-cli
USAGE
}

if [[ $# -ne 1 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  if [[ $# -eq 1 && ( "${1:-}" == "-h" || "${1:-}" == "--help" ) ]]; then
    exit 0
  fi
  exit 1
fi

SPEC_PATH="$1"
TECTONIC_BIN="${TECTONIC_BIN:-./bin/tectonic-cli}"
OUTPUT_DIR="workloads"

if [[ ! -f "$SPEC_PATH" ]]; then
  echo "Workload spec not found: $SPEC_PATH" >&2
  exit 1
fi

if [[ ! -x "$TECTONIC_BIN" ]]; then
  echo "Tectonic binary missing or not executable: $TECTONIC_BIN" >&2
  echo "Build it first with: scripts/build_rocksdb.sh" >&2
  exit 1
fi

metadata="$(
  python3 - "$SPEC_PATH" <<'PY'
import json
import re
import sys
from pathlib import Path

spec_path = Path(sys.argv[1])
parts = spec_path.parts

size = None
workload_type = None

if "workload_specs" in parts:
    idx = parts.index("workload_specs")
    if len(parts) > idx + 2:
        size = parts[idx + 1]
        workload_type = parts[idx + 2]

with spec_path.open("r", encoding="utf-8") as f:
    spec = json.load(f)

counts = {
    "inserts": 0,
    "updates": 0,
    "point_queries": 0,
    "range_queries": 0,
}

def walk(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in counts and isinstance(value, dict):
                counts[key] += int(value.get("op_count", 0))
            walk(value)
    elif isinstance(node, list):
        for item in node:
            walk(item)

walk(spec)
total_ops = sum(counts.values())

def compact_count(value):
    if value <= 0:
        return "unknown"
    if value % 1_000_000 == 0:
        return f"{value // 1_000_000}M"
    if value % 1_000 == 0:
        return f"{value // 1_000}k"
    return str(value)

if not size or size == "misc":
    size = compact_count(total_ops)

valid_types = {"mixed", "read-only", "write-only"}
if workload_type not in valid_types:
    read_ops = counts["point_queries"] + counts["range_queries"]
    write_ops = counts["inserts"] + counts["updates"]
    if read_ops > 0 and write_ops > 0:
        workload_type = "mixed"
    elif read_ops > 0:
        workload_type = "read-only"
    elif write_ops > 0:
        workload_type = "write-only"
    else:
        workload_type = "unknown"

safe_size = re.sub(r"[^A-Za-z0-9._-]+", "_", size)
safe_type = re.sub(r"[^A-Za-z0-9._-]+", "_", workload_type)
print(f"{safe_size}\t{safe_type}\t{total_ops}")
PY
)"

IFS=$'\t' read -r workload_size workload_type total_ops <<<"$metadata"
OUTPUT_PATH="${OUTPUT_DIR}/workload_${workload_size}_${workload_type}.txt"

mkdir -p "$OUTPUT_DIR"

echo "[workload] spec: $SPEC_PATH"
echo "[workload] operations: $total_ops"
echo "[workload] output: $OUTPUT_PATH"
"$TECTONIC_BIN" generate -w "$SPEC_PATH" -o "$OUTPUT_PATH"

echo
echo "Generated workload: $OUTPUT_PATH"
