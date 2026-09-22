#!/usr/bin/env bash
# Run the graph generator with the pipeline's Python environment.
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
[[ -x "$PYTHON" ]] || {
  echo "Missing $PYTHON; run step 00 first." >&2
  exit 1
}

exec "$PYTHON" "$PIPELINE_DIR/04_generate_graphs.py" "$@"

