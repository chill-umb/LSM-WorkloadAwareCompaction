#!/usr/bin/env bash
# The node's CPU and memory settings that a reboot loses, applied as root:
# SMT off, the performance governor on every core, transparent huge pages
# madvise (00_install_dependencies.sh gives the reason for each). 00 runs it
# when it sets a node up, and installs it as /usr/local/sbin/lsm-node-tuning,
# which lsm-node-tuning.service runs at every boot. Prints the state it
# leaves; exits 1 when a setting did not take.
#
# SETUP_SYSFS (default /sys) lets the tests point it at a fake tree.
set -uo pipefail

SYSFS="${SETUP_SYSFS:-/sys}"
CPU="$SYSFS/devices/system/cpu"
status=0

# The value a sysfs switch has selected: the bracketed one of a list such as
# "always [madvise] never", or the whole value.
selected() {
  local value
  value="$(<"$1")"
  if [[ "$value" == *"["*"]"* ]]; then
    value="${value#*[}"
    value="${value%%]*}"
  fi
  echo "$value"
}

smt="$CPU/smt/control"
if [[ -r "$smt" && "$(<"$smt")" == on ]]; then
  echo off > "$smt" || status=1
fi

# Online CPUs only: on a node with SMT on in firmware, the siblings that SMT
# off takes offline keep their cpufreq files, which then refuse reads and
# writes ("Device or resource busy").
governors=()
for governor in "$CPU"/cpu[0-9]*/cpufreq/scaling_governor; do
  [[ -e "$governor" && "$(cat "${governor%/cpufreq/*}/online" 2>/dev/null)" != 0 ]] &&
    governors+=("$governor")
done
for governor in "${governors[@]}"; do
  [[ "$(<"$governor")" == performance ]] || echo performance > "$governor" || status=1
done

thp="$SYSFS/kernel/mm/transparent_hugepage/enabled"
if [[ -r "$thp" && "$(selected "$thp")" != madvise ]]; then
  echo madvise > "$thp" || status=1
fi

echo "[tuning] SMT $(cat "$smt" 2>/dev/null || echo absent);" \
     "governor $( (( ${#governors[@]} )) && cat "${governors[@]}" | sort -u | paste -sd, || echo none);" \
     "THP $( [[ -r "$thp" ]] && selected "$thp" || echo absent)"
exit "$status"
