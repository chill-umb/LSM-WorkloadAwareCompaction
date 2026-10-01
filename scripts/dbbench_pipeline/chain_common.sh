# Shared by the unattended node drivers 24 and 25. Sourced after config.sh,
# from the repository root, with STAGE set to the driver's number.
# shellcheck shell=bash
NVME="${NVME:-/mnt/nvme}"
if [[ "$PYTHON_VENV" = /* ]]; then
  PY="$PYTHON_VENV/bin/python"
else
  PY="$PROJECT_ROOT/$PYTHON_VENV/bin/python"
fi
# D-13 §3's second workload; Assoc is config.sh's default.
POWERLAW=(WORKLOAD_SKEW=2 MIX_GET_RATIO=0.95 MIX_PUT_RATIO=0.05 MIX_SEEK_RATIO=0
          WORKLOAD_PROFILE=powerlaw-get95-v1)
stamp() { echo "=== $(date '+%F %T') $*"; }
fail() { echo "[$STAGE] $*" >&2; exit 1; }

# Checks that fail in seconds, while someone is watching. $@: folders under
# NVME a fresh night must not find (03 refuses an existing results root, and
# a failed arm's database); none are checked under RESUME=1.
node_checks() {
  local d free_gb recorded
  [[ -x "$PY" ]] || fail "no Python at $PY; run 00_install_dependencies.sh"
  mkdir -p "$NVME" && touch "$NVME/.write_test" && rm -f "$NVME/.write_test" ||
    fail "$NVME is not writable"
  [[ "$(stat -L -f -c %T "$NVME")" != tmpfs ]] || fail "$NVME is tmpfs; use the NVMe device"
  free_gb="$(df --output=avail -BG "$NVME" | tail -n 1 | tr -dc 0-9)"
  (( free_gb >= ${MIN_FREE_GB:-60} )) ||
    fail "only ${free_gb} GB free on $NVME; about ${MIN_FREE_GB:-60} GB are needed"
  if [[ "${RESUME:-0}" != 1 ]]; then
    for d in "$@"; do
      [[ ! -e "$NVME/$d" ]] || fail "$NVME/$d exists: move it away, or rerun with RESUME=1"
    done
  fi
  # The measurements are of the disk NVME is on. An unmounted /mnt/nvme is a
  # plain folder on the root disk, so that is refused unless meant.
  echo "[$STAGE] results disk: $(df --output=source,fstype,target "$NVME" | tail -n 1)"
  # -L: a symlinked NVME is judged by the disk it points to.
  if [[ "$(stat -L -c %d "$NVME")" == "$(stat -c %d /)" && "${ALLOW_ROOT_DISK:-0}" != 1 ]]; then
    fail "$NVME is on the root filesystem: is the NVMe mounted? If the root disk" \
         "is the device to measure, rerun with ALLOW_ROOT_DISK=1"
  fi
  if git rev-parse --verify -q HEAD >/dev/null 2>&1; then
    recorded="$(git ls-tree HEAD lib/rocksdb | awk '{print $3}')"
    [[ -n "$recorded" ]] || fail "cannot read the recorded lib/rocksdb commit"
    [[ "$(git -C lib/rocksdb rev-parse HEAD 2>/dev/null)" == "$recorded" ]] ||
      fail "lib/rocksdb is not at the recorded commit $recorded; run git submodule update --init --recursive"
  else
    echo "[$STAGE] not a git checkout with commits; the fork commit is not checked"
  fi
  if [[ -n "$DBBENCH_CPUS" ]] && ! command -v taskset >/dev/null; then
    fail "taskset is not installed"
  fi
}
