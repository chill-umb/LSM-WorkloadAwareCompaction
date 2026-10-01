#!/usr/bin/env python3
"""Bind a passed preflight to the exact binaries and code (plan §6.4).

`13_run_preflight_verification.sh` writes the marker once its steps pass;
`03_run_experiments.sh` checks it before any long run and refuses to start
when the db_bench binary, the plugin or the code under `CODE_DIRS` has changed
since, or when a step the run's arms depend on was skipped.

Code is hashed as git sees the working tree (tracked plus untracked,
non-ignored files, with their current contents), so an uncommitted edit after
the preflight is caught too.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
CODE_DIRS = ("rl_agent", "controller", "scripts/dbbench_pipeline")
# Preflight steps (plan §6.4): 1 build, 2 tiers 1-2, 3 ACT-1, 4 parity,
# 5 rules-mode smoke, 6 learner smoke.
STATIC_STEPS = {1, 2, 3, 4}
STATIC_ARMS = {"regular", "native"}


def required_steps(arms):
    """Steps a run needs: static arms 1-4, the plugin's hold and rules arms
    add 5, anything else 5-6."""
    need = set(STATIC_STEPS)
    for arm in arms:
        if arm in STATIC_ARMS or arm.startswith("static:"):
            continue
        need.add(5)
        if arm not in ("rules", "hold"):
            need.add(6)
    return need


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(root, rel):
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z",
         "--", rel],
        check=True, capture_output=True).stdout
    paths = sorted({p for p in listed.decode().split("\0") if p})
    if not paths:
        return "absent"
    digest = hashlib.sha256()
    for path in paths:
        full = Path(root) / path
        # A tracked file deleted from the working tree is still listed.
        content = file_sha256(full) if full.is_file() else "missing"
        digest.update(f"{path}\0{content}\0".encode())
    return digest.hexdigest()


def current_hashes(root, db_bench, plugin):
    """`plugin` is the controller library's path when controller/ exists
    (13 and 03 pass it then), else None: "absent". A path that names no file
    hashes as "missing", so a deleted plugin never matches a marker."""
    if not plugin:
        plugin_hash = "absent"
    elif Path(plugin).is_file():
        plugin_hash = file_sha256(plugin)
    else:
        plugin_hash = "missing"
    hashes = {
        "db_bench_sha256": file_sha256(db_bench),
        "plugin_sha256": plugin_hash,
    }
    for rel in CODE_DIRS:
        hashes[f"tree_sha256:{rel}"] = tree_sha256(root, rel)
    return hashes


def write_marker(marker, hashes, passed, skipped):
    record = {
        "schema": SCHEMA,
        "written_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hashes": hashes,
        "steps_passed": sorted(passed),
        "steps_skipped": {str(k): v for k, v in sorted(skipped.items())},
    }
    marker = Path(marker)
    temporary = marker.with_name(marker.name + ".tmp")
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, marker)


def read_marker(marker):
    try:
        return json.loads(Path(marker).read_text())
    except FileNotFoundError:
        return None


def problems(record, hashes, required):
    if record is None:
        return ["no preflight marker"]
    if record.get("schema") != SCHEMA:
        return [f"marker schema {record.get('schema')}, expected {SCHEMA}"]
    found = []
    recorded = record.get("hashes", {})
    for key, value in hashes.items():
        if recorded.get(key) != value:
            found.append(f"{key} changed since the preflight "
                         f"({recorded.get(key)} -> {value})")
    missing = sorted(set(required) - set(record.get("steps_passed", [])))
    if missing:
        found.append(f"preflight steps {missing} did not pass "
                     f"(skipped: {record.get('steps_skipped', {})})")
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("write", "check"))
    parser.add_argument("--root", default=Path(__file__).resolve().parents[2])
    parser.add_argument("--marker", required=True)
    parser.add_argument("--db-bench", required=True)
    parser.add_argument("--plugin")
    parser.add_argument("--passed", nargs="*", type=int, default=[])
    parser.add_argument("--skipped", nargs="*", default=[],
                        help="STEP=REASON pairs")
    parser.add_argument("--arms", default="",
                        help="space-separated arms of the run being checked")
    args = parser.parse_args(argv)

    hashes = current_hashes(args.root, args.db_bench, args.plugin)
    if args.action == "write":
        skipped = dict(item.split("=", 1) for item in args.skipped)
        write_marker(args.marker, hashes, set(args.passed),
                     {int(k): v for k, v in skipped.items()})
        print(f"preflight marker written: {args.marker}")
        return 0
    found = problems(read_marker(args.marker), hashes,
                     required_steps(args.arms.split()))
    for problem in found:
        print(f"preflight marker {args.marker}: {problem}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
