#!/usr/bin/env python3
"""Bind a passed preflight to the exact binaries and code (plan §6.4).

`13_run_preflight_verification.sh` writes the marker once its steps pass;
`03_run_experiments.sh` checks it before any long run and refuses to start
when the db_bench binary, the plugin or the code under `CODE_DIRS` has changed
since, or when a step the run's arms depend on was skipped.

Code is hashed as git sees the working tree (tracked plus untracked,
non-ignored files, with their current contents), so an uncommitted edit after
the preflight is caught too.

It also owns the db_bench identity (`db_bench_identity`), the one value every
stage records or compares as "the binary": the marker's `db_bench_sha256`,
03's `dbbench_sha256` and fingerprint segment `binary<sha>`, 18's prices, 22's
parity runs and gate_n2_plan's check. Bash stages get it from

  preflight_marker.py identity --db-bench <path>

which prints the 64-hex value, so it is computed identically everywhere.
"""
import argparse
import hashlib
import json
import os
import re
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


# --- The db_bench identity -------------------------------------------------
# 01/02 build db_bench as a small executable linked against the shared
# librocksdb.so.<N> in the same build directory (RUNPATH), and every line of
# the fork's RocksDB code is in that library. A file hash of db_bench alone
# missed a fork change on 2026-10-02 (31e087505 -> 8e903efd9 left it at
# a8e9329d7cdd...). The identity is therefore the program as the loader runs
# it: one sha256 over db_bench's bytes and the bytes of every RocksDB shared
# library the dynamic loader resolves for it. A db_bench with RocksDB linked
# in statically has no such library, and its identity is the same rule over
# its own bytes alone. System libraries (libc, libstdc++, gflags) are left
# out, as before.
LDD = "ldd"
ROCKSDB_LIBRARY = re.compile(r"librocksdb[^/\s]*\.so(\.[0-9]+)*")
IDENTITY_SCHEME = "db_bench executable + loaded librocksdb, v1"


class IdentityError(ValueError):
    """The db_bench identity cannot be computed: the loader cannot resolve a
    RocksDB library db_bench needs, or ldd fails. Never guessed around."""


def loaded_libraries(executable, pattern=ROCKSDB_LIBRARY, ldd=None):
    """Sorted (name, path) of each shared library whose name fully matches
    `pattern` that the dynamic loader resolves for `executable`, as `ldd`
    (the loader itself, in this process's environment) reports it. A file
    that is not ELF (a stand-in script in the tests) and a static executable
    load none. Raises IdentityError when a matching library is not found or
    ldd fails."""
    executable = Path(executable)
    with open(executable, "rb") as handle:
        if handle.read(4) != b"\x7fELF":
            return []
    command = [ldd or LDD, str(executable)]
    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              env={**os.environ, "LC_ALL": "C"})
    except OSError as error:
        raise IdentityError(f"cannot run {command[0]} on {executable}: "
                            f"{error}") from error
    if done.returncode != 0:
        # glibc's ldd says this, exit 1, for a non-PIE static executable.
        if "not a dynamic executable" in done.stderr:
            return []
        raise IdentityError(f"{command[0]} {executable} exited "
                            f"{done.returncode}: {done.stderr.strip()}")
    found = []
    for line in done.stdout.splitlines():
        line = line.strip()
        name, arrow, target = line.partition(" => ")
        if not arrow:  # the vDSO, the loader, or a library named by path
            target = line
            name = Path(line.rsplit(" (", 1)[0]).name
        if not pattern.fullmatch(name):
            continue
        target = target.rsplit(" (0x", 1)[0].strip()
        if target == "not found" or not target:
            raise IdentityError(f"{executable} needs {name}, which the dynamic "
                                "loader cannot find (ldd: not found)")
        if not Path(target).is_file():
            raise IdentityError(f"{executable} loads {name} from {target}, "
                                "which is not a file")
        found.append((name, target))
    return sorted(found)


def db_bench_identity_parts(db_bench, ldd=None):
    """What `db_bench_identity` hashes: ("executable", path, sha256) and one
    (name, path, sha256) per RocksDB library the loader resolves."""
    parts = [("executable", str(db_bench), file_sha256(db_bench))]
    for name, path in loaded_libraries(db_bench, ldd=ldd):
        parts.append((name, path, file_sha256(path)))
    return parts


def db_bench_identity(db_bench, ldd=None):
    """The identity of the db_bench program as loaded (executable plus the
    RocksDB shared library it runs with): sha256 over IDENTITY_SCHEME, the
    executable's sha256 and each loaded librocksdb's name and sha256, in
    name order, one per line. Paths are not hashed, only bytes. Stored under
    the existing names `db_bench_sha256` / `dbbench_sha256` and in the
    fingerprint's `binary<sha>` segment, so their format is unchanged."""
    return identity_of(db_bench_identity_parts(db_bench, ldd=ldd))


def identity_of(parts):
    """The identity of `db_bench_identity_parts`' output (see there)."""
    lines = [IDENTITY_SCHEME] + [f"{name} {sha}" for name, _, sha in parts]
    return hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest()


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
    """`db_bench_sha256` is db_bench's identity as loaded (executable plus
    its librocksdb, `db_bench_identity`), so a rebuilt fork library is
    refused like a rebuilt executable. `plugin` is the controller library's
    path when controller/ exists (13 and 03 pass it then), else None:
    "absent". A path that names no file hashes as "missing", so a deleted
    plugin never matches a marker. The plugin needs no RocksDB library (it
    reaches the host only through rl_controller_host.h's interface), so its
    file hash is its whole identity."""
    if not plugin:
        plugin_hash = "absent"
    elif Path(plugin).is_file():
        plugin_hash = file_sha256(plugin)
    else:
        plugin_hash = "missing"
    hashes = {
        "db_bench_sha256": db_bench_identity(db_bench),
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
    parser.add_argument("action", choices=("write", "check", "identity"),
                        help="identity: print db_bench's identity as loaded "
                             "(executable plus its librocksdb) and exit")
    parser.add_argument("--root", default=Path(__file__).resolve().parents[2])
    parser.add_argument("--marker", help="required by write and check")
    parser.add_argument("--db-bench", required=True)
    parser.add_argument("--explain", action="store_true",
                        help="identity: also list what is hashed, on stderr")
    parser.add_argument("--plugin")
    parser.add_argument("--passed", nargs="*", type=int, default=[])
    parser.add_argument("--skipped", nargs="*", default=[],
                        help="STEP=REASON pairs")
    parser.add_argument("--arms", default="",
                        help="space-separated arms of the run being checked")
    args = parser.parse_args(argv)
    if args.action != "identity" and not args.marker:
        parser.error(f"{args.action} needs --marker")

    try:
        if args.action == "identity":
            parts = db_bench_identity_parts(args.db_bench)
            if args.explain:
                for name, path, sha in parts:
                    print(f"  {name} {path} sha256 {sha}", file=sys.stderr)
            print(identity_of(parts))
            return 0
        hashes = current_hashes(args.root, args.db_bench, args.plugin)
    except IdentityError as error:
        print(f"preflight_marker.py: cannot identify db_bench "
              f"{args.db_bench}: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"preflight_marker.py: {error}", file=sys.stderr)
        return 1
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
