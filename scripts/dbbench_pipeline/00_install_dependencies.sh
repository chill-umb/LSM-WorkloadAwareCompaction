#!/usr/bin/env bash
# Set up a measurement node, or check one: the dependencies the builds and
# runs need, and the node settings the pinned runs assume. Ubuntu/Debian only;
# written against the Chameleon CHI@NCAR EPYC 4545P nodes on Ubuntu 24.04.
#
#   00_install_dependencies.sh          set the node up
#   00_install_dependencies.sh check    change nothing: report every item and
#                                       exit 1 if one is not ready (after a
#                                       reboot, or before a long run)
#
# Every item is checked first and changed only when it is not ready, so a
# re-run on a prepared node changes nothing. Setting up refuses while a
# db_bench, an RL server or a run driver (03, 13, 24, 25, 31, 33) is going:
# package installs and governor writes would land inside a measured run.
# Last line: SETUP_EXIT=<0 ready | 1 not ready>.
#
# Items, in order:
#   submodules    lib/rocksdb checked out before 01 builds. Only a missing
#                 submodule is initialised; a fork checked out at another
#                 commit than the recorded one is reported and left alone.
#   packages      compiler, CMake, gflags, compression libraries, venv; and
#                 the runbook's tools: tmux (unattended chains), pigz and
#                 rsync (node-to-node copies), zstd (old runs' compressed
#                 LOGs), jq. Only missing packages are installed, so a
#                 prepared node's versions do not move.
#   toolchain     znver5 needs GCC 14.1+; 24.04 ships 13. Installed from
#                 ppa:ubuntu-toolchain-r/test and made the default c++ via
#                 update-alternatives, which is how the recorded builds got
#                 "14.3.0-12ubuntu1~24~ppa1".
#   tuning        SMT off, the performance governor, THP madvise, applied now
#                 by node_tuning.sh (reasons below).
#   boot tuning   the same at every boot: node_tuning.sh installed as
#                 /usr/local/sbin/lsm-node-tuning, run by
#                 lsm-node-tuning.service. Before 2026-10-20 a reboot lost
#                 all three until 00 was run again.
#   open files    ~/.bashrc raises each shell's soft open-file limit to the
#                 hard one, as 03 does for itself: open files -1 (D-29 §1)
#                 keeps every table open, and a tmux server started at 1,024
#                 killed by-hand db_bench runs ("Too many open files").
#   linger        loginctl enable-linger, so the user's systemd manager
#                 outlives the login. 03's memory cap is a systemd-run --user
#                 scope (D-29 §1), which fails once the SSH session that
#                 started a chain has closed and the manager has stopped.
#   memory cap    a MemoryMax scope starts, with the memory controller
#                 delegated to the user (check only).
#   pinning       DBBENCH_CPUS and CONTROLLER_CPUS online and on different L3
#                 domains, as 03 requires (check only).
#   OS cpuset     opt-in, ISOLATE_OS_CPUS=1: systemd's system.slice confined
#                 to the CPUs neither db_bench nor the controller use. Off by
#                 default: the recorded runs never used it (EXPERIMENTAL_SETUP
#                 section 13). Runtime only.
#   venv          PYTHON_VENV with rl_agent/requirements.txt and matplotlib.
#   disk          the repository and NVME (when set) not on tmpfs (check only).
#   node_ops      ~/node_ops/{handoffs,reports,diag,drafts}, where CLAUDE.md
#                 puts handoffs and operator reports, outside the repository.
# A set-up node then gets a record (kernel, libraries, compiler, Python
# packages, settings, code and binary identity) in ~/node_ops/node-setup/, so
# two nodes can be compared with diff before their runs are pooled.
#
# SETUP_SYSFS, SETUP_ETC and SETUP_SBIN (default /sys, /etc, /usr/local/sbin)
# let the tests point the script at a fake tree.
set -Eeuo pipefail

MODE="${1:-setup}"
case "$MODE" in
  setup|check) ;;
  *) echo "usage: $0 [check]" >&2; exit 2 ;;
esac

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This script supports Ubuntu/Debian (apt-get) only." >&2
  exit 1
fi
trap 'echo "SETUP_EXIT=1 (failed at line $LINENO)"' ERR

SYSFS="${SETUP_SYSFS:-/sys}"
ETC="${SETUP_ETC:-/etc}"
SBIN="${SETUP_SBIN:-/usr/local/sbin}"
NODE_OPS_DIR="${NODE_OPS_DIR:-$HOME/node_ops}"
NODE_USER="$(id -un)"
GCC_MAJOR="${GCC_MAJOR:-14}"
CPU="$SYSFS/devices/system/cpu"
UNIT=lsm-node-tuning.service
if [[ "$PYTHON_VENV" = /* ]]; then
  VENV_PYTHON="$PYTHON_VENV/bin/python"
else
  VENV_PYTHON="$PROJECT_ROOT/$PYTHON_VENV/bin/python"
fi

SUDO=()
[[ "$(id -u)" -eq 0 ]] || SUDO=(sudo)
# needrestart's prompt on 24.04 would stop an unattended install.
APT=("${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get)

if [[ "$MODE" == setup && "${ALLOW_CONCURRENT_RUNS:-0}" != 1 ]]; then
  busy="$(ps -eo pid=,comm=,args= 2>/dev/null | awk -v self="$$" '
    $1 == self { next }
    $2 == "db_bench" { print; next }
    $0 ~ /rl_agent\/server\.py/ { print; next }
    $0 ~ /dbbench_pipeline\/(03|13|24|25|31|33)_[a-z0-9_]+\.sh/ { print }
  ')"
  if [[ -n "$busy" ]]; then
    echo "A run is going; setting the node up now would disturb it:" >&2
    echo "$busy" | sed 's/^/  /' >&2
    echo "Wait for it, or run '$0 check', which changes nothing." >&2
    exit 3
  fi
fi

# --- helpers -----------------------------------------------------------------

expand_cpus() {  # "0-2,5" -> 0 1 2 5, one per line
  local part parts=()
  IFS=, read -ra parts <<< "$1"
  for part in "${parts[@]}"; do
    if [[ "$part" =~ ^([0-9]+)-([0-9]+)$ ]]; then
      seq "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    elif [[ "$part" =~ ^[0-9]+$ ]]; then
      echo "$part"
    fi
  done
}

l3_of() {  # the CPUs sharing CPU $1's L3, as the kernel lists them
  local index
  for index in "$CPU/cpu$1"/cache/index*; do
    [[ -r "$index/level" && "$(<"$index/level")" == 3 ]] || continue
    cat "$index/shared_cpu_list"
    return 0
  done
}

l3_domains() {
  local cpu
  for cpu in $(expand_cpus "$(<"$CPU/online")"); do
    l3_of "$cpu"
  done | sort -u | paste -sd' '
}

selected() {  # as node_tuning.sh reads a sysfs switch
  local value
  value="$(<"$1")"
  if [[ "$value" == *"["*"]"* ]]; then
    value="${value#*[}"
    value="${value%%]*}"
  fi
  echo "$value"
}

PACKAGES=(build-essential ca-certificates cmake git libbz2-dev libgflags-dev
          liblz4-dev libsnappy-dev libzstd-dev ninja-build pkg-config python3
          python3-pip python3-venv software-properties-common util-linux
          zlib1g-dev
          jq pigz rsync tmux zstd)

missing_packages() {
  local package
  for package in "${PACKAGES[@]}"; do
    [[ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null)" == "install ok installed" ]] ||
      echo "$package"
  done
}

unit_text() {
  cat <<EOF
[Unit]
Description=LSM pipeline node settings: SMT off, performance governor, THP madvise (scripts/dbbench_pipeline/00_install_dependencies.sh)

[Service]
Type=oneshot
ExecStart=$SBIN/lsm-node-tuning
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
}

BASHRC_LINE='ulimit -n "$(ulimit -Hn)" 2>/dev/null  # 00_install_dependencies.sh: open files -1 (D-29 §1)'

# --- items: check_<item> prints what it found and fails when not ready;
# --- fix_<item> makes it ready --------------------------------------------------

check_submodules() {
  local status missing other
  status="$(git submodule status --recursive)" ||
    { echo "git submodule status failed"; return 1; }
  missing="$(awk '/^-/ { print $2 }' <<< "$status" | paste -sd' ')"
  other="$(awk '/^\+/ { print $2 }' <<< "$status" | paste -sd' ')"
  if [[ -n "$missing" ]]; then
    echo "not checked out: $missing"
    return 1
  fi
  echo "checked out${other:+ ($other at another commit than the recorded one; left alone)}"
}
fix_submodules() {
  local path
  for path in $(git submodule status --recursive | awk '/^-/ { print $2 }'); do
    git submodule update --init --recursive -- "$path"
  done
}

check_packages() {
  local missing
  missing="$(missing_packages | paste -sd' ')"
  [[ -z "$missing" ]] || { echo "missing: $missing"; return 1; }
  echo "${#PACKAGES[@]} installed"
}
fix_packages() {
  local missing
  mapfile -t missing < <(missing_packages)
  "${APT[@]}" update
  "${APT[@]}" install -y "${missing[@]}"
}

check_toolchain() {
  if ! c++ -march="$ROCKSDB_PORTABLE" -E -x c++ /dev/null >/dev/null 2>&1; then
    echo "c++ ($(c++ --version 2>/dev/null | head -1 || echo absent)) rejects -march=$ROCKSDB_PORTABLE"
    return 1
  fi
  c++ --version | head -1
}
# 01 compiles with ${CXX:-c++}; the alternatives link is what lets a plain
# c++ resolve to GCC 14 without every caller exporting CXX.
fix_toolchain() {
  local tool target
  "${SUDO[@]}" add-apt-repository -y ppa:ubuntu-toolchain-r/test
  "${APT[@]}" update
  "${APT[@]}" install -y "gcc-$GCC_MAJOR" "g++-$GCC_MAJOR"
  for tool in gcc g++ cc c++; do
    case "$tool" in
      cc)  target="/usr/bin/gcc-$GCC_MAJOR" ;;
      c++) target="/usr/bin/g++-$GCC_MAJOR" ;;
      *)   target="/usr/bin/$tool-$GCC_MAJOR" ;;
    esac
    "${SUDO[@]}" update-alternatives --install "/usr/bin/$tool" "$tool" "$target" 100
    "${SUDO[@]}" update-alternatives --set "$tool" "$target"
  done
}

# SMT: the pinned layout (DBBENCH_CPUS=0-7, CONTROLLER_CPUS=8) and 03's L3
# check assume 16 logical CPUs on the 16-core 4545P; with SMT on those names
# would select sibling threads. The governor: `performance` holds every core
# at its top P-state, so a run's latency does not include frequency ramp-up.
# THP `madvise` lets RocksDB's arena use huge pages where it asks for them
# without the kernel promoting every mapping, the state the recorded runs
# used. 03 records the governor and THP per arm in metadata.env.
check_tuning() {
  local smt governors thp bad=()
  smt="$(cat "$CPU/smt/control" 2>/dev/null || echo absent)"
  [[ "$smt" != on ]] || bad+=("SMT on")
  # Online CPUs only, as node_tuning.sh sets them.
  governors="$(for governor in "$CPU"/cpu[0-9]*/cpufreq/scaling_governor; do
                 [[ "$(cat "${governor%/cpufreq/*}/online" 2>/dev/null)" == 0 ]] || cat "$governor"
               done 2>/dev/null | sort -u | paste -sd, || true)"
  [[ -z "$governors" || "$governors" == performance ]] || bad+=("governor $governors")
  thp="$( [[ -r "$SYSFS/kernel/mm/transparent_hugepage/enabled" ]] &&
          selected "$SYSFS/kernel/mm/transparent_hugepage/enabled" || echo absent)"
  [[ "$thp" == madvise || "$thp" == absent ]] || bad+=("THP $thp")
  echo "SMT $smt, governor ${governors:-none (no cpufreq interface)}, THP $thp"
  (( ${#bad[@]} == 0 ))
}
fix_tuning() {
  "${SUDO[@]}" env SETUP_SYSFS="$SYSFS" bash "$PIPELINE_DIR/node_tuning.sh"
}

check_boot_tuning() {
  cmp -s "$PIPELINE_DIR/node_tuning.sh" "$SBIN/lsm-node-tuning" ||
    { echo "$SBIN/lsm-node-tuning missing or not this node_tuning.sh"; return 1; }
  cmp -s <(unit_text) "$ETC/systemd/system/$UNIT" ||
    { echo "$ETC/systemd/system/$UNIT missing or different"; return 1; }
  [[ "$(systemctl is-enabled "$UNIT" 2>/dev/null)" == enabled ]] ||
    { echo "$UNIT not enabled"; return 1; }
  echo "$UNIT enabled"
}
fix_boot_tuning() {
  "${SUDO[@]}" install -D -m 0755 "$PIPELINE_DIR/node_tuning.sh" "$SBIN/lsm-node-tuning"
  "${SUDO[@]}" mkdir -p "$ETC/systemd/system"
  unit_text | "${SUDO[@]}" tee "$ETC/systemd/system/$UNIT" >/dev/null
  "${SUDO[@]}" systemctl daemon-reload
  "${SUDO[@]}" systemctl enable "$UNIT"
}

check_open_files() {
  grep -qxF "$BASHRC_LINE" "$HOME/.bashrc" 2>/dev/null ||
    { echo "~/.bashrc does not raise the soft limit (this shell: $(ulimit -Sn))"; return 1; }
  echo "new shells raise the soft limit to the hard one (this shell: $(ulimit -Sn))"
}
fix_open_files() {
  printf '\n%s\n' "$BASHRC_LINE" >> "$HOME/.bashrc"
  echo "[open files] new shells only: restart any tmux server started before this"
}

check_linger() {
  [[ "$(loginctl show-user "$NODE_USER" -p Linger --value 2>/dev/null)" == yes ]] ||
    { echo "off: $NODE_USER's systemd manager stops when the last login closes"; return 1; }
  echo "on for $NODE_USER"
}
fix_linger() {
  "${SUDO[@]}" loginctl enable-linger "$NODE_USER"
}

check_memory_cap() {
  local uid controllers
  uid="$(id -u)"
  controllers="$SYSFS/fs/cgroup/user.slice/user-$uid.slice/user@$uid.service/cgroup.controllers"
  [[ " $(cat "$controllers" 2>/dev/null) " == *" memory "* ]] ||
    { echo "the memory controller is not delegated to $NODE_USER ($controllers)"; return 1; }
  XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$uid}" \
    systemd-run --user --scope -q -p MemoryMax=1G true >/dev/null 2>&1 ||
    { echo "systemd-run --user --scope -p MemoryMax=1G fails (is there a user manager?)"; return 1; }
  echo "a MemoryMax scope starts"
}

check_pinning() {
  local online cpu shared=""
  [[ -n "$DBBENCH_CPUS" || -n "$CONTROLLER_CPUS" ]] || { echo "unpinned"; return 0; }
  [[ -n "$DBBENCH_CPUS" && -n "$CONTROLLER_CPUS" ]] ||
    { echo "pin both DBBENCH_CPUS and CONTROLLER_CPUS, or neither"; return 1; }
  online=" $(expand_cpus "$(<"$CPU/online")" | paste -sd' ') "
  for cpu in $(expand_cpus "$DBBENCH_CPUS") $(expand_cpus "$CONTROLLER_CPUS"); do
    [[ "$online" == *" $cpu "* ]] || { echo "CPU $cpu is not online"; return 1; }
  done
  for cpu in $(expand_cpus "$CONTROLLER_CPUS"); do
    local siblings=" $(expand_cpus "$(l3_of "$cpu")" | paste -sd' ') " other
    for other in $(expand_cpus "$DBBENCH_CPUS"); do
      [[ "$siblings" != *" $other "* ]] || shared="$other/$cpu"
    done
  done
  local domains
  domains="$(l3_domains)"
  if [[ -n "$shared" && "$domains" == *" "* ]]; then
    echo "db_bench CPU ${shared%/*} and controller CPU ${shared#*/} share an L3 (domains: $domains)"
    return 1
  fi
  echo "db_bench $DBBENCH_CPUS, controller $CONTROLLER_CPUS; L3 domains ${domains:-none exported}"
}

os_cpus() {  # the online CPUs neither db_bench nor the controller use
  local taken cpu
  taken=" $(expand_cpus "$DBBENCH_CPUS,$CONTROLLER_CPUS" | paste -sd' ') "
  for cpu in $(expand_cpus "$(<"$CPU/online")"); do
    [[ "$taken" == *" $cpu "* ]] || echo "$cpu"
  done | paste -sd,
}
check_os_cpuset() {
  local want have
  [[ -n "$DBBENCH_CPUS" && -n "$CONTROLLER_CPUS" ]] ||
    { echo "ISOLATE_OS_CPUS=1 needs DBBENCH_CPUS and CONTROLLER_CPUS set"; return 1; }
  want="$(os_cpus)"
  [[ -n "$want" ]] || { echo "no CPU left for the OS"; return 1; }
  have="$(expand_cpus "$(systemctl show system.slice -p AllowedCPUs --value 2>/dev/null)" | paste -sd,)"
  [[ "$have" == "$want" ]] ||
    { echo "system.slice may use CPUs ${have:-all}, not $want"; return 1; }
  echo "system.slice confined to CPUs $want (runtime only)"
}
fix_os_cpuset() {
  "${SUDO[@]}" systemctl set-property --runtime system.slice "AllowedCPUs=$(os_cpus)"
}

check_venv() {
  "$VENV_PYTHON" -c 'import matplotlib, numpy, torch; print("numpy", numpy.__version__, "torch", torch.__version__, "matplotlib", matplotlib.__version__)' 2>/dev/null ||
    { echo "no venv at $PYTHON_VENV with numpy, torch and matplotlib"; return 1; }
}
fix_venv() {
  python3 -m venv "$PYTHON_VENV"
  "$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel
  "$VENV_PYTHON" -m pip install -r rl_agent/requirements.txt matplotlib
}

check_disk() {  # the repository, and NVME when it is set and exists
  local place note notes=()
  for place in "$PROJECT_ROOT" ${NVME:+"$NVME"}; do
    [[ -e "$place" ]] || continue
    [[ "$(stat -L -f -c %T "$place")" != tmpfs ]] || { echo "$place is on tmpfs"; return 1; }
    note="$place: $(df --output=source "$place" | tail -1)"
    note+=", $(df -h --output=avail "$place" | tail -1 | tr -d ' ') free"
    # The drivers refuse the root disk unless told it is the one measured.
    [[ "$(stat -L -c %d "$place")" != "$(stat -c %d /)" ]] ||
      note+=" (the root disk: drivers need ALLOW_ROOT_DISK=1)"
    notes+=("$note")
  done
  printf '%s; ' "${notes[@]}" | sed 's/; $//'
}

check_node_ops() {
  local dir missing=()
  for dir in handoffs reports diag drafts; do
    [[ -d "$NODE_OPS_DIR/$dir" ]] || missing+=("$dir")
  done
  (( ${#missing[@]} == 0 )) || { echo "$NODE_OPS_DIR lacks ${missing[*]}"; return 1; }
  echo "$NODE_OPS_DIR"
}
fix_node_ops() {
  mkdir -p "$NODE_OPS_DIR"/{handoffs,reports,diag,drafts}
}

NOT_READY=()
item() {  # $1=name, $2=function suffix, $3=1 when the item can be fixed here
  local name="$1" fn="$2" fixable="$3" found
  if found="$("check_$fn")"; then
    echo "[$name] ok${found:+: $found}"
    return 0
  fi
  if [[ "$MODE" == check || "$fixable" != 1 ]]; then
    echo "[$name] NOT READY${found:+: $found}"
    NOT_READY+=("$name")
    return 0
  fi
  echo "[$name] setting up${found:+ ($found)}"
  "fix_$fn"
  if found="$("check_$fn")"; then
    echo "[$name] set${found:+: $found}"
  else
    echo "[$name] STILL NOT READY${found:+: $found}" >&2
    NOT_READY+=("$name")
  fi
}

write_record() {
  local dir="$NODE_OPS_DIR/node-setup" file package
  mkdir -p "$dir"
  file="$dir/$(hostname)-$(date -u +%Y%m%dT%H%MZ).txt"
  {
    echo "host=$(hostname)"
    echo "date_utc=$(date -u '+%F %T')"
    echo "os=$(. /etc/os-release && echo "$PRETTY_NAME")"
    echo "kernel=$(uname -r)"
    echo "cpu=$(lscpu | sed -n 's/^Model name: *//p')"
    echo "cpus_online=$(<"$CPU/online")"
    echo "l3_domains=$(l3_domains)"
    echo "memory_kib=$(awk '/^MemTotal/ { print $2 }' /proc/meminfo)"
    echo "tuning=$(check_tuning || true)"
    echo "c++=$(c++ --version 2>/dev/null | head -1 || echo absent)"
    for package in libc6 python3.12 "gcc-$GCC_MAJOR" "g++-$GCC_MAJOR" cmake libgflags-dev \
                   libzstd-dev liblz4-dev libsnappy-dev zlib1g-dev libbz2-dev; do
      echo "deb:$package=$(dpkg-query -W -f='${Version}' "$package" 2>/dev/null || echo absent)"
    done
    { "$VENV_PYTHON" -m pip freeze 2>/dev/null || true; } | sed 's/^/pip:/'
    echo "repo_head=$(git rev-parse HEAD 2>/dev/null || echo none)"
    echo "fork_head=$(git -C lib/rocksdb rev-parse HEAD 2>/dev/null || echo none)"
    if [[ -x "$DBBENCH_BUILD_DIR/db_bench" ]]; then
      echo "db_bench_sha256=$(sha256sum "$DBBENCH_BUILD_DIR/db_bench" | cut -d' ' -f1)"
    else
      echo "db_bench_sha256=not built"
    fi
  } > "$file"
  echo "[record] $file (compare two nodes with diff)"
}

# --- the run -------------------------------------------------------------------

echo "[00] $MODE on $(hostname), $(date -u '+%F %T') UTC"
item submodules  submodules  1
item packages    packages    1
item toolchain   toolchain   1
item tuning      tuning      1
item "boot tuning" boot_tuning 1
item "open files" open_files 1
item linger      linger      1
item "memory cap" memory_cap 0
item pinning     pinning     0
[[ "${ISOLATE_OS_CPUS:-0}" != 1 ]] || item "OS cpuset" os_cpuset 1
item venv        venv        1
item disk        disk        0
item node_ops    node_ops    1

[[ "$MODE" != setup ]] || write_record

if (( ${#NOT_READY[@]} )); then
  echo
  echo "Not ready: ${NOT_READY[*]}"
  echo "SETUP_EXIT=1"
  exit 1
fi
echo
echo "Node ready. Next: 01_build_rocksdb.sh, 02_build_db_bench.sh,"
echo "01b_build_test_trees.sh, then the preflight (13): README's node runbook."
echo "SETUP_EXIT=0"
