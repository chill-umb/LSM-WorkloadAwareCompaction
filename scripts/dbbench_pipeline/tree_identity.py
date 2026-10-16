#!/usr/bin/env python3
"""A preloaded tree's identity and record (PREREGISTRATION D-27 §4).

31_preload_tree.sh builds one tree per setting and records it here; every run
of 03 on that tree opens a copy, checked against the identity first.

The identity is the sha256 of the tree's file list with sizes and of the
MANIFEST that CURRENT names, over every file of the closed database except
RocksDB's info logs and its lock (LOG, LOG.old.*, LOCK), which a copy does not
carry. Data bytes are not hashed: the MANIFEST names every SST with its size,
so a copy with the same files, sizes and MANIFEST is the same tree, and the
check stays seconds long on a 20 GB tree.

  tree_identity.py identity <db>          print the identity
  tree_identity.py check <db> <sha256>    exit 1 unless it is the identity
  tree_identity.py record <tree dir> ...  write <tree dir>/tree.json (31)
  tree_identity.py match <tree dir> ...   check a run against the tree and
                                          print its keys, seed and identity (03)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
RECORD = "tree.json"
# Files a tree does not carry: the info logs RocksDB rotates on every open,
# and the lock file of an open database.
NOT_CARRIED = re.compile(r"^(LOG|LOG\.old\.\d+|LOCK)$")
# levelstats' table in db_bench's output (Level, Files, Size(MB)).
LEVEL_ROW = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s*$")


class TreeError(ValueError):
    """A tree that is not what it claims to be, or a run that does not fit
    it."""


def carried_files(db: Path) -> list[list]:
    """[name, size] of every file the tree carries, sorted by name. A
    subfolder is refused: RocksDB keeps one flat folder."""
    files = []
    for entry in sorted(Path(db).iterdir()):
        if entry.is_dir():
            raise TreeError(f"{entry} is a folder; a tree is one flat folder")
        if not NOT_CARRIED.match(entry.name):
            files.append([entry.name, entry.stat().st_size])
    return files


def manifest_sha256(db: Path) -> str:
    current = (Path(db) / "CURRENT").read_text().strip()
    if not re.fullmatch(r"MANIFEST-\d+", current):
        raise TreeError(f"CURRENT names {current!r}, not a MANIFEST")
    return hashlib.sha256((Path(db) / current).read_bytes()).hexdigest()


def identity(db: Path) -> str:
    files = carried_files(db)
    if not files:
        raise TreeError(f"{db} holds no database files")
    body = json.dumps({"files": files, "manifest": manifest_sha256(db)},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def load_statistics(host_log: Path, load_out: Path,
                    write_buffer_size: int) -> dict:
    """D-27 §4's load statistics, once per tree: time is the caller's; here
    the stalls, jobs and flushes from the load's host log (schema 3), the
    settle step's record, and levelstats' table from db_bench's output."""
    records = [json.loads(line) for line in host_log.read_text().splitlines()
               if line.strip()]
    settles = [r for r in records
               if r.get("type") == "stamp" and r.get("name") == "settle"]
    if len(settles) != 1 or settles[0].get("ok") != 1:
        raise TreeError(f"{host_log}: the load did not settle once, with ok=1")
    settle = settles[0]
    ends = [r for r in records if r.get("type") == "job_end" and r.get("ok") == 1]
    flushes = [r for r in records if r.get("type") == "flush_end"]
    largest = max((int(r["x"]) for r in flushes), default=0)
    levels = []
    in_table = False
    for line in load_out.read_text(errors="replace").splitlines():
        if line.startswith("Level Files Size(MB)"):
            in_table, levels = True, []
            continue
        match = LEVEL_ROW.match(line) if in_table else None
        if match:
            levels.append([int(v) for v in match.groups()])
        elif in_table and levels:
            in_table = False
    return {
        "operations": int(settle["op"]),
        "live_sst_bytes": int(settle["h"]),
        "stall_micros": int(settle["stall_micros"]),
        "settle_wait_micros": int(settle.get("wait_micros", 0)),
        "settle_hold_micros": int(settle.get("hold_micros", 0)),
        "flushes": len(flushes),
        "compactions": sum(1 for r in ends if not r["trivial"]),
        "trivial_moves": sum(1 for r in ends if r["trivial"]),
        "largest_flush_bytes": largest,
        # D-29 §3: a flush that fills the whole buffer is flagged.
        "largest_flush_full_buffer": int(largest >= write_buffer_size > 0),
        "levels_files_mb": levels,
    }


def record(tree: Path, keys: int, seed: int, dbbench_sha256: str,
           options: dict, seconds: float, host_log: Path,
           load_out: Path) -> dict:
    db = tree / "db"
    stats = load_statistics(host_log, load_out,
                            int(options.get("write_buffer_size", 0)))
    if stats["operations"] != keys:
        raise TreeError(f"the load wrote {stats['operations']} keys, not {keys}")
    files = carried_files(db)
    result = {
        "schema": SCHEMA,
        "identity": identity(db),
        "manifest_sha256": manifest_sha256(db),
        "files": len(files),
        "bytes": sum(size for _, size in files),
        "keys": keys,
        "seed": seed,
        "options": options,
        "dbbench_sha256": dbbench_sha256,
        "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "load": {"seconds": seconds, **stats},
    }
    (tree / RECORD).write_text(json.dumps(result, indent=2, sort_keys=True)
                               + "\n")
    return result


def match(tree: Path, dbbench_sha256: str, options: dict) -> dict:
    """The tree's record, after checking that the run names the tree's
    options and binary, and that the archive still has its identity."""
    try:
        saved = json.loads((tree / RECORD).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise TreeError(f"{tree / RECORD}: {error}") from error
    if saved.get("schema") != SCHEMA:
        raise TreeError(f"{tree / RECORD}: schema {saved.get('schema')!r}")
    problems = [f"{name} {value} (the tree's: {saved['options'].get(name)})"
                for name, value in sorted(options.items())
                if str(saved["options"].get(name)) != str(value)]
    if saved["dbbench_sha256"] != dbbench_sha256:
        problems.append(f"db_bench {dbbench_sha256} (the tree was built by "
                        f"{saved['dbbench_sha256']})")
    if problems:
        raise TreeError(f"the run does not fit the tree {tree}: "
                        + "; ".join(problems))
    found = identity(tree / "db")
    if found != saved["identity"]:
        raise TreeError(f"the archive {tree / 'db'} has identity {found}, "
                        f"not its recorded {saved['identity']}")
    return saved


def options_of(pairs: list[str]) -> dict:
    options = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep or not name:
            raise TreeError(f"--option wants name=value, got {pair!r}")
        options[name] = value
    return options


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("identity")
    p.add_argument("db", type=Path)
    p = sub.add_parser("check")
    p.add_argument("db", type=Path)
    p.add_argument("sha256")
    p = sub.add_parser("record")
    p.add_argument("tree", type=Path)
    p.add_argument("--keys", type=int, required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--dbbench-sha256", required=True)
    p.add_argument("--seconds", type=float, required=True)
    p.add_argument("--host-log", type=Path, required=True)
    p.add_argument("--load-out", type=Path, required=True)
    p.add_argument("--option", action="append", default=[])
    p = sub.add_parser("match")
    p.add_argument("tree", type=Path)
    p.add_argument("--dbbench-sha256", required=True)
    p.add_argument("--option", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        if args.action == "identity":
            print(identity(args.db))
        elif args.action == "check":
            found = identity(args.db)
            if found != args.sha256:
                print(f"tree_identity.py: {args.db} has identity {found}, "
                      f"not {args.sha256}", file=sys.stderr)
                return 1
        elif args.action == "record":
            saved = record(args.tree, args.keys, args.seed,
                           args.dbbench_sha256, options_of(args.option),
                           args.seconds, args.host_log, args.load_out)
            print(saved["identity"])
        else:
            saved = match(args.tree, args.dbbench_sha256,
                          options_of(args.option))
            print(saved["keys"])
            print(saved["seed"])
            print(saved["identity"])
    except (TreeError, OSError, KeyError) as error:
        print(f"tree_identity.py: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
