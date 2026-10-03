#!/usr/bin/env python3
"""The archived price trees of PREREGISTRATION D-22 (a), (b), (c) and (i),
shared by 29_build_price_trees.sh, which builds and archives them, and
18_calibrate_prices.sh, which measures the read prices on them.

18 trees: T in SIZE_RATIOS, three builds of each; build b of every T forms
set b. Tree "s<b>/T<T>" is archived as "s<b>-T<T>.tgz", which unpacks to the
folder s<b>/T<T>. MANIFEST.sha256 (sha256sum's format, paths relative to the
unpack root) lists every file of every tree and every .tgz; ARCHIVES.sha256
repeats the .tgz lines, for checking a copy before anything is unpacked. The
tree-set identity is the sha256 of MANIFEST.sha256.

  rounds            the three rounds' visiting orders, one per line (D-22 c)
  manifest          write MANIFEST.sha256 and ARCHIVES.sha256; prints the identity
  identity          the sha256 of an archive's MANIFEST.sha256
  check             one unpacked tree against the manifest: every file before
                    the reads; with --after, every SST (D-22 c, i)
  levels            files and MB per level from a levelstats stdout
  build-record      build_record.json from 29's build folders
  machine           the machine's state at a session's start (D-22 c)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

SIZE_RATIOS = (2, 3, 4, 6, 8, 10)
SETS = (1, 2, 3)
# D-22 (c): each round visits the six trees once, in a rotated order, so
# slow drift within a set falls on every tree alike.
ROUNDS = ((2, 3, 4, 6, 8, 10), (4, 6, 8, 10, 2, 3), (8, 10, 2, 3, 4, 6))
# D-22 (c): the c_open block, D-20 §2(b)'s procedure on set 1's trees.
COPEN_SET = 1
COPEN_RATIOS = (2, 6, 10)
# D-22 (a'): age-based compaction off in every build and session command,
# or an archived tree opened 30 days after its build compacts on open.
AGE_FLAGS = ("--ttl_seconds=0", "--periodic_compaction_seconds=0")
MANIFEST = "MANIFEST.sha256"
ARCHIVES = "ARCHIVES.sha256"
BUILD_RECORD = "build_record.json"
LINE = re.compile(r"^([0-9a-f]{64})  (\S.*)$")
LEVEL = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s*$")
SETTLED = re.compile(r"^RL_SETTLED ok=(\d)", re.M)


def tree(build: int, ratio: int) -> str:
    return f"s{build}/T{ratio}"


def archive(build: int, ratio: int) -> str:
    return f"s{build}-T{ratio}.tgz"


def all_trees() -> list[tuple[int, int]]:
    return [(b, t) for b in SETS for t in SIZE_RATIOS]


def age_flags_present(command: str) -> bool:
    """Both of (a')'s flags, as whole words, in a recorded command."""
    words = command.split()
    return all(flag in words for flag in AGE_FLAGS)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_files(root: Path, name: str) -> dict[str, Path]:
    """Every file of one tree, keyed by its path relative to root. A tree
    holds regular files only; anything else is refused."""
    folder = root / name
    if not folder.is_dir():
        raise ValueError(f"{folder}: no such tree")
    files = {}
    for path in sorted(folder.rglob("*")):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError(f"{path}: not a regular file")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path
    return files


def hash_all(paths: dict[str, Path], workers: int = 8) -> dict[str, str]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(paths, pool.map(sha256_file, paths.values())))


def write_manifest(build_root: Path, archive_dir: Path) -> str:
    """MANIFEST.sha256 over every file of every tree under build_root and
    every .tgz in archive_dir; ARCHIVES.sha256 with the .tgz lines. Returns
    the tree-set identity."""
    files: dict[str, Path] = {}
    for b, t in all_trees():
        found = tree_files(build_root, tree(b, t))
        if not found:
            raise ValueError(f"{tree(b, t)}: an empty tree")
        files.update(found)
    archives = {archive(b, t): archive_dir / archive(b, t) for b, t in all_trees()}
    for name, path in archives.items():
        if not path.is_file():
            raise ValueError(f"{path}: missing")
    tree_lines = [f"{h}  {p}\n" for p, h in sorted(hash_all(files).items())]
    archive_lines = [f"{h}  {p}\n" for p, h in sorted(hash_all(archives).items())]
    (archive_dir / MANIFEST).write_text("".join(tree_lines + archive_lines))
    (archive_dir / ARCHIVES).write_text("".join(archive_lines))
    return identity(archive_dir / MANIFEST)


def identity(manifest: Path) -> str:
    return hashlib.sha256(manifest.read_bytes()).hexdigest()


def read_manifest(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        match = LINE.match(line)
        if not match:
            raise ValueError(f"{path}:{number}: not a sha256sum line")
        digest, name = match.groups()
        if name in entries:
            raise ValueError(f"{path}:{number}: {name} listed twice")
        entries[name] = digest
    return entries


def check_tree(manifest: dict[str, str], root: Path, name: str,
               after: bool = False) -> list[str]:
    """Problems of one unpacked tree against the manifest (D-22 c, i).
    Before the reads every file must match and no other file exist. After
    them, every SST must be present and unchanged and no other SST exist:
    opening a database writes new MANIFEST, OPTIONS and LOG files, never a
    new SST unless a compaction ran."""
    expected = {p: h for p, h in manifest.items() if p.startswith(name + "/")}
    if not expected:
        return [f"{name}: no files in the manifest"]
    present = tree_files(root, name)
    problems = []
    if after:
        problems += background_jobs(expected, present)
        expected = {p: h for p, h in expected.items() if p.endswith(".sst")}
        present = {p: f for p, f in present.items() if p.endswith(".sst")}
    problems += [f"{p}: missing" for p in sorted(set(expected) - set(present))]
    problems += [f"{p}: not in the manifest" for p in sorted(set(present) - set(expected))]
    shared = {p: present[p] for p in sorted(set(expected) & set(present))}
    problems += [f"{p}: changed" for p, h in hash_all(shared).items()
                 if h != expected[p]]
    return problems


# RocksDB's event log entries of background work. A trivial move changes no
# SST, only the MANIFEST, so the reads' own logs are searched for it too.
JOB_EVENTS = ('"event": "flush_started"', '"event": "compaction_started"',
              '"event": "trivial_move"')


def background_jobs(expected: dict[str, str], present: dict[str, Path]) -> list[str]:
    """Flushes, compactions or trivial moves in any log the reads wrote: a
    LOG file whose content is not one of the archived files' (the archived
    LOG holds the build's own jobs, and RocksDB renames it LOG.old.* when
    the tree is opened)."""
    archived = set(expected.values())
    problems = []
    for path, file in sorted(present.items()):
        if not file.name.startswith("LOG") or sha256_file(file) in archived:
            continue
        text = file.read_text(errors="replace")
        found = [event.split('"')[3] for event in JOB_EVENTS if event in text]
        if found:
            problems.append(f"{path}: {', '.join(found)} during the reads")
    return problems


def levels(stdout: str) -> dict[str, dict[str, int]]:
    """Files and MB per level from levelstats' table (the last one printed)."""
    blocks = stdout.split("Level Files Size(MB)")
    if len(blocks) < 2:
        raise ValueError("no levelstats table")
    out = {}
    for line in blocks[-1].splitlines()[2:]:
        match = LEVEL.match(line)
        if not match:
            break
        level, files, mb = (int(v) for v in match.groups())
        out[str(level)] = {"files": files, "mb": mb}
    if not out:
        raise ValueError("an empty levelstats table")
    return out


def settled(stdout: str) -> bool:
    """Whether a build's settle step ended ok=1 (D-22 a)."""
    outcomes = SETTLED.findall(stdout)
    return len(outcomes) == 1 and outcomes[0] == "1"


def build_record(archive_dir: Path, build_root: Path, db_bench_sha256: str,
                 tree_set: str) -> dict:
    """The build record's facts (D-22 b): the identity, each tree's settle
    attempts, files and bytes per level, and the building binary."""
    record = {"tree_set_sha256": tree_set, "db_bench_sha256": db_bench_sha256,
              "age_flags": list(AGE_FLAGS), "trees": {}}
    for b, t in all_trees():
        name = tree(b, t)
        logs = archive_dir / "build" / name
        attempts = sorted(logs.glob("attempt*"), key=lambda p: int(p.name[7:]))
        if not attempts:
            raise ValueError(f"{logs}: no build attempt")
        tried = []
        for attempt in attempts:
            stdout = (attempt / "stdout.txt").read_text(errors="replace")
            command = (attempt / "command.txt").read_text()
            if not age_flags_present(command):
                raise ValueError(f"{attempt}: built without {' '.join(AGE_FLAGS)} "
                                 "(D-22 a')")
            tried.append({"attempt": attempt.name, "settle_ok": settled(stdout)})
        if not tried[-1]["settle_ok"] or any(a["settle_ok"] for a in tried[:-1]):
            raise ValueError(f"{name}: the kept build is not the first that "
                             "settled ok=1")
        last = (attempts[-1] / "stdout.txt").read_text(errors="replace")
        files = tree_files(build_root, name)
        ssts = [f for p, f in files.items() if p.endswith(".sst")]
        record["trees"][name] = {
            "archive": archive(b, t), "attempts": tried,
            "levels": levels(last), "files": len(files),
            "bytes": sum(f.stat().st_size for f in files.values()),
            "sst_files": len(ssts),
            "sst_bytes": sum(f.stat().st_size for f in ssts),
            "command": (attempts[-1] / "command.txt").read_text(),
        }
    return record


def machine(cpus: str) -> dict:
    """Kernel, CPU governor of the pinned cores, free memory, uptime and
    boot id at a session's start (D-22 c); recorded, not controlled."""
    def read(path: str) -> str | None:
        try:
            return Path(path).read_text().strip()
        except OSError:
            return None

    def cpu_list(text: str) -> list[int]:
        out = []
        for part in filter(None, text.split(",")):
            lo, _, hi = part.partition("-")
            out += range(int(lo), int(hi or lo) + 1)
        return out

    cores = cpu_list(cpus) if cpus else list(range(os.cpu_count() or 1))
    meminfo = {}
    for line in (read("/proc/meminfo") or "").splitlines():
        key, _, value = line.partition(":")
        if value.strip().endswith("kB"):
            meminfo[key] = int(value.split()[0]) * 1024
    uptime = read("/proc/uptime")
    return {
        "kernel": platform.release(),
        "governor": {str(c): read(f"/sys/devices/system/cpu/cpu{c}/cpufreq/"
                                  "scaling_governor") for c in cores},
        "mem_available_bytes": meminfo.get("MemAvailable"),
        "mem_free_bytes": meminfo.get("MemFree"),
        "uptime_seconds": float(uptime.split()[0]) if uptime else None,
        "boot_id": read("/proc/sys/kernel/random/boot_id"),
        "smt_active": read("/sys/devices/system/cpu/smt/active"),
        "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("rounds")
    m = sub.add_parser("manifest")
    m.add_argument("--build-root", type=Path, required=True)
    m.add_argument("--archive-dir", type=Path, required=True)
    i = sub.add_parser("identity")
    i.add_argument("--archive-dir", type=Path, required=True)
    c = sub.add_parser("check")
    c.add_argument("--manifest", type=Path, required=True)
    c.add_argument("--root", type=Path, required=True)
    c.add_argument("--tree", required=True)
    c.add_argument("--after", action="store_true")
    c.add_argument("--report", type=Path, required=True)
    lv = sub.add_parser("levels")
    lv.add_argument("stdout", type=Path)
    br = sub.add_parser("build-record")
    br.add_argument("--archive-dir", type=Path, required=True)
    br.add_argument("--build-root", type=Path, required=True)
    br.add_argument("--db-bench-sha256", required=True)
    mc = sub.add_parser("machine")
    mc.add_argument("--cpus", default="")
    args = parser.parse_args()
    try:
        if args.command == "rounds":
            for order in ROUNDS:
                print(" ".join(map(str, order)))
        elif args.command == "manifest":
            print(write_manifest(args.build_root, args.archive_dir))
        elif args.command == "identity":
            print(identity(args.archive_dir / MANIFEST))
        elif args.command == "check":
            problems = check_tree(read_manifest(args.manifest), args.root,
                                  args.tree, args.after)
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps({
                "tree": args.tree, "phase": "after" if args.after else "unpacked",
                "manifest_sha256": identity(args.manifest),
                "ok": not problems, "problems": problems,
                "checked_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }, indent=2) + "\n")
            if problems:
                print(f"[price trees] {args.tree}: " + "; ".join(problems[:20]),
                      file=sys.stderr)
                return 1
        elif args.command == "levels":
            print(json.dumps(levels(args.stdout.read_text(errors="replace"))))
        elif args.command == "build-record":
            tree_set = identity(args.archive_dir / MANIFEST)
            record = build_record(args.archive_dir, args.build_root,
                                  args.db_bench_sha256, tree_set)
            (args.archive_dir / BUILD_RECORD).write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n")
            print(tree_set)
        elif args.command == "machine":
            print(json.dumps(machine(args.cpus), indent=2, sort_keys=True))
    except (ValueError, OSError) as error:
        print(f"[price trees] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
