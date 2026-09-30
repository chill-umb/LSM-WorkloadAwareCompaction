#!/usr/bin/env bash
# Tier 1 Python suites (plan §6.1): stdlib unittest, run before every commit
# and by the preflight. A suite with no tests yet is reported, not failed
# (unittest exits 5 for "no tests ran" from Python 3.12 on).
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

if [[ "$PYTHON_VENV" = /* ]]; then
  PYTHON="$PYTHON_VENV/bin/python"
else
  PYTHON="$PROJECT_ROOT/$PYTHON_VENV/bin/python"
fi
if [[ ! -x "$PYTHON" ]]; then
  echo "[tier1] no venv at $PYTHON_VENV; using $(command -v python3)" >&2
  PYTHON="$(command -v python3)"
fi

status=0
for suite in rl_agent scripts/dbbench_pipeline; do
  set +e
  "$PYTHON" -m unittest discover -s "$suite/tests" -t "$suite"
  code=$?
  set -e
  case "$code" in
    0) echo "[tier1] $suite/tests: passed" ;;
    5) echo "[tier1] $suite/tests: no tests yet" ;;
    *) echo "[tier1] $suite/tests: FAILED (exit $code)" >&2; status=1 ;;
  esac
done
exit "$status"
