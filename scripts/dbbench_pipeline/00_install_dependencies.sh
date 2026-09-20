#!/usr/bin/env bash
# Prepare a fresh measurement node: toolchain, build dependencies, the Python
# environment, and the CPU topology the pinned runs assume. Ubuntu/Debian only;
# written against the Chameleon CHI@NCAR EPYC 4545P nodes on Ubuntu 24.04.
#
# Idempotent. Every step is either a check or a no-op when already applied, so
# it is safe to re-run on a node that has been prepared before.
#
# Steps, in order:
#   1. submodules             lib/rocksdb must be checked out before 01 builds
#   2. apt packages           compiler, CMake, gflags, compression libs, venv
#   3. GCC 14                 znver5 needs GCC 14.1+; 24.04 ships 13. Installed
#                             from ppa:ubuntu-toolchain-r/test and made the
#                             default c++ via update-alternatives, which is how
#                             the recorded builds got "14.3.0-12ubuntu1~24~ppa1".
#   4. SMT off                the 4545P is 16 cores / 32 threads. Every pinned
#                             run and the L3 check in 03 assume 16 logical CPUs
#                             (cores 0-7 and 8-15 are the two L3 domains).
#                             Runtime setting, lost on reboot.
#   5. OS cpuset (opt-in)     ISOLATE_OS_CPUS=1 confines systemd's system.slice
#                             to the cores neither db_bench nor the controller
#                             use. Off by default: it was never applied on the
#                             recorded runs (EXPERIMENTAL_SETUP section 13).
#   6. Python venv
#   7. topology report        what 03 will check, printed once here.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This script supports Ubuntu/Debian (apt-get) only." >&2
  exit 1
fi

sudo_prefix=()
if [[ "$(id -u)" -ne 0 ]]; then
  sudo_prefix=("${SUDO_COMMAND:-sudo}")
fi

echo "[git] checking out submodules"
git submodule update --init --recursive

echo "[system] installing compiler, CMake, gflags, and RocksDB dependencies"
"${sudo_prefix[@]}" apt-get update
"${sudo_prefix[@]}" apt-get install -y \
  build-essential \
  ca-certificates \
  cmake \
  git \
  libbz2-dev \
  libgflags-dev \
  liblz4-dev \
  libsnappy-dev \
  libzstd-dev \
  ninja-build \
  pkg-config \
  python3 \
  python3-pip \
  python3-venv \
  software-properties-common \
  util-linux \
  zlib1g-dev

# ROCKSDB_PORTABLE=znver5 needs GCC 14.1+ (01_build_rocksdb.sh preflights this).
# Ubuntu 24.04 ships GCC 13, so take 14 from the toolchain PPA and make it the
# default: 01 compiles with ${CXX:-c++}, and the alternatives link is what lets
# a plain `c++` resolve to 14 without every caller exporting CXX.
GCC_MAJOR="${GCC_MAJOR:-14}"
if ! c++ -march="$ROCKSDB_PORTABLE" -E -x c++ /dev/null >/dev/null 2>&1; then
  echo "[toolchain] c++ does not accept -march=$ROCKSDB_PORTABLE; installing GCC $GCC_MAJOR"
  "${sudo_prefix[@]}" add-apt-repository -y ppa:ubuntu-toolchain-r/test
  "${sudo_prefix[@]}" apt-get update
  "${sudo_prefix[@]}" apt-get install -y "gcc-$GCC_MAJOR" "g++-$GCC_MAJOR"
  for tool in gcc g++ cc c++; do
    case "$tool" in
      cc)  target="/usr/bin/gcc-$GCC_MAJOR" ;;
      c++) target="/usr/bin/g++-$GCC_MAJOR" ;;
      *)   target="/usr/bin/$tool-$GCC_MAJOR" ;;
    esac
    "${sudo_prefix[@]}" update-alternatives --install "/usr/bin/$tool" "$tool" "$target" 100
    "${sudo_prefix[@]}" update-alternatives --set "$tool" "$target"
  done
fi
c++ -march="$ROCKSDB_PORTABLE" -E -x c++ /dev/null >/dev/null 2>&1 || {
  echo "c++ still rejects -march=$ROCKSDB_PORTABLE after installing GCC $GCC_MAJOR:" >&2
  c++ --version | head -1 >&2
  exit 1
}
echo "[toolchain] $(c++ --version | head -1)"

# The pinned layout (DBBENCH_CPUS=0-7, CONTROLLER_CPUS=8) and the L3-domain
# check in 03 assume 16 logical CPUs on the 16-core 4545P, i.e. SMT off. It is
# a runtime switch on this node and does not survive a reboot; 03 re-validates
# the topology on every start, so a forgotten re-run fails loudly there.
SMT_CONTROL=/sys/devices/system/cpu/smt/control
if [[ -r "$SMT_CONTROL" ]]; then
  case "$(<"$SMT_CONTROL")" in
    on)
      echo "[cpu] SMT is on; switching it off"
      echo off | "${sudo_prefix[@]}" tee "$SMT_CONTROL" >/dev/null
      ;;
    off|forceoff|notsupported|notimplemented)
      echo "[cpu] SMT already off ($(<"$SMT_CONTROL"))"
      ;;
  esac
fi

# Frequency governor and transparent huge pages. Both are runtime settings and
# do not survive a reboot. `performance` holds every core at its top P-state so
# a run's latency does not include frequency ramp-up; cpupower is the clean
# interface and the sysfs write is the fallback when linux-tools is absent.
# THP `madvise` lets RocksDB's arena use huge pages where it asks for them
# without the kernel promoting every mapping behind its back, which is the
# state the recorded runs used. 03 records both per arm in metadata.env, from
# sysfs, so a forgotten re-run shows up in the run record rather than as an
# unexplained latency shift.
if compgen -G /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor >/dev/null; then
  "${sudo_prefix[@]}" cpupower frequency-set -g performance 2>/dev/null || \
    echo performance | "${sudo_prefix[@]}" tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor >/dev/null
  echo "[cpu] governor: $(sort -u /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor | tr '\n' ' ')"
else
  echo "[cpu] no cpufreq interface on this node; governor not set" >&2
fi
THP=/sys/kernel/mm/transparent_hugepage/enabled
if [[ -w "$THP" || -r "$THP" ]]; then
  echo madvise | "${sudo_prefix[@]}" tee "$THP" >/dev/null
  echo "[mm] transparent_hugepage: $(<"$THP")"
fi

# Optional. taskset pins the measured processes but does not keep the kernel or
# system daemons off their cores. This confines systemd's system.slice to the
# CPUs that are neither DBBENCH_CPUS nor CONTROLLER_CPUS, at runtime, without a
# reboot. It is opt-in and OFF by default because the recorded runs did not use
# it (EXPERIMENTAL_SETUP section 13) and pinning is not in the fingerprint, so
# enabling it must be recorded as a change to the setup. user.slice is
# deliberately left alone: the shell that launches 03 lives there, and a cpuset
# on it would make taskset -c 0-7 fail with EINVAL. Kernel threads are not in
# any cgroup and are unaffected; only isolcpus reaches those, and that needs a
# kernel command line and a reboot.
if [[ "${ISOLATE_OS_CPUS:-0}" == "1" ]]; then
  [[ -n "$DBBENCH_CPUS" && -n "$CONTROLLER_CPUS" ]] || {
    echo "ISOLATE_OS_CPUS=1 needs DBBENCH_CPUS and CONTROLLER_CPUS set." >&2
    exit 1
  }
  os_cpus="$(python3 - "$DBBENCH_CPUS" "$CONTROLLER_CPUS" <<'PY2'
import os, sys
def expand(spec):
    out = set()
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out.update(range(int(a), int(b or a) + 1))
    return out
taken = expand(sys.argv[1]) | expand(sys.argv[2])
free = sorted(set(range(os.cpu_count())) - taken)
if not free:
    raise SystemExit("no CPU left for the OS")
print(",".join(map(str, free)))
PY2
)"
  echo "[cpu] confining system.slice to CPUs $os_cpus (runtime only)"
  "${sudo_prefix[@]}" systemctl set-property --runtime system.slice "AllowedCPUs=$os_cpus"
fi

echo "[python] creating $PYTHON_VENV"
python3 -m venv "$PYTHON_VENV"
"$PYTHON_VENV/bin/python" -m pip install --upgrade pip setuptools wheel
"$PYTHON_VENV/bin/python" -m pip install -r rl_agent/requirements.txt matplotlib

echo
echo "[topology] $(nproc) logical CPUs; L3 domains (from sysfs, as 03 reads them):"
for cpu in /sys/devices/system/cpu/cpu[0-9]*; do
  for index in "$cpu"/cache/index*; do
    [[ -r "$index/level" && "$(<"$index/level")" == 3 ]] || continue
    cat "$index/shared_cpu_list"; break
  done
done | sort -u | sed 's/^/  L3 domain: CPUs /'

echo "[topology] pinned: db_bench=${DBBENCH_CPUS:-unpinned} controller=${CONTROLLER_CPUS:-unpinned}"
if [[ -n "$DB_ROOT" ]]; then
  db_parent="$(dirname "$DB_ROOT")"
  mkdir -p "$db_parent"
  fs="$(df --output=fstype "$db_parent" | tail -1)"
  [[ "$fs" != "tmpfs" ]] || echo "[disk] WARNING: DB_ROOT=$DB_ROOT is on tmpfs; put it on the device under test" >&2
  echo "[disk] DB_ROOT parent $db_parent ($fs, $(df -h --output=avail "$db_parent" | tail -1 | tr -d ' ') free)"
fi

echo
echo "Node prepared. Next: scripts/dbbench_pipeline/01_build_rocksdb.sh"
