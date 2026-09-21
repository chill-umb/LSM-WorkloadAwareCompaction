#!/usr/bin/env bash
# Write the live objective_alpha value that rl_agent/multilevel.py polls at
# runtime (RUNTIME_ALPHA_OBJECTIVE_PLAN.md). The write is atomic (temp file +
# rename into place) so the server's poller, which stat()s the destination
# path once per decision tick, never observes a half-written file.
#
# Usage: set_objective_alpha.sh <value in [0,1]> [path]
#   path defaults to $RL_ALPHA_CONTROL_FILE, which must also be what the
#   server was started with -- this script only writes the file, it does not
#   set the environment variable that tells the server to look at it.
set -Eeuo pipefail

VALUE="${1:?usage: set_objective_alpha.sh <value in [0,1]> [path]}"
DEST="${2:-${RL_ALPHA_CONTROL_FILE:-}}"

if [[ -z "$DEST" ]]; then
  echo "set_objective_alpha.sh: no destination path (pass one, or set RL_ALPHA_CONTROL_FILE)" >&2
  exit 1
fi

# Validate as a plain decimal in [0, 1] before touching the filesystem.
if ! [[ "$VALUE" =~ ^([0-1](\.[0-9]+)?|\.[0-9]+)$ ]]; then
  echo "set_objective_alpha.sh: '$VALUE' is not a decimal in [0, 1]" >&2
  exit 1
fi
python3 - "$VALUE" <<'EOF'
import sys
v = float(sys.argv[1])
if not (0.0 <= v <= 1.0):
    sys.exit(f"set_objective_alpha.sh: {v} is out of [0, 1]")
EOF

mkdir -p "$(dirname -- "$DEST")"
TMP="$(mktemp "${DEST}.XXXXXX")"
printf '{"objective_alpha": %s}\n' "$VALUE" > "$TMP"
mv -f -- "$TMP" "$DEST"
echo "set_objective_alpha.sh: wrote objective_alpha=$VALUE to $DEST"
