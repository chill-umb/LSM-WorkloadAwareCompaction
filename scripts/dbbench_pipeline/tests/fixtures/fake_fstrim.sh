#!/usr/bin/env bash
# Stand-in for "sudo -n fstrim -v" in the tests (tests/__init__.py makes it
# SSD_TRIM_COMMAND): never touches a disk. $1 = the mount point. Appends the
# mount point to SSD_TRIM_LOG when set; fails when SSD_TRIM_FAIL=1.
[[ -z "${SSD_TRIM_LOG:-}" ]] || echo "$1" >> "$SSD_TRIM_LOG"
if [[ "${SSD_TRIM_FAIL:-0}" == 1 ]]; then
  echo "fstrim: $1: the stand-in was told to fail" >&2
  exit 1
fi
echo "$1: 0 B (0 bytes) trimmed (stand-in)"
