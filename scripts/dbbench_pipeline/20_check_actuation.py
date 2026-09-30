#!/usr/bin/env python3
"""ACT-1 on the Release db_bench (PATHWAYS Pathway A §6; plan §6.4 step 3).

The fork's gtest proves the level_target_multipliers option in a Debug build.
This repeats its checks on the binary that is measured, from the outside:

  l0_score_invariance     L0's score is the same under every vector
  scaled_scores           level i's score is bytes / (target_i * m_i)
  scaled_pending          the pending-compaction estimate uses scaled targets
  setoptions_recompute    SetOptions changes the scores at once, with no write
  all_ones_equals_absent  m = 1 everywhere is the option left empty
  refusals                bad vectors are refused at open and by SetOptions

It builds a small tree with native compaction, then freezes it
(disable_auto_compactions) and reopens it under each vector. The instruments
are db_bench's `stats` (per-level Score, printed to one decimal, and the exact
"Estimated pending compaction bytes"), `sstables` (exact file sizes) and the
DB's LOG. Expected values are recomputed here from the file sizes with
RocksDB's own formulas (VersionStorageInfo::ComputeCompactionScore and
EstimateCompactionBytesNeeded, static ladder); the pending estimate is exact,
so it pins every over-target level's scaled target to the byte.

A check whose tree cannot show the effect it tests fails as insensitive, so a
geometry change can never make a check pass by being blind.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

LEVELS = 5
RATIO = 2
BASE = 256 * 1024
L0_TRIGGER = 8
# Dyadic entries, so base * m is exact in any float width and the model's
# integer targets equal RocksDB's long double ones. Valid at T = 2: in range,
# and m[i+1] * T >= m[i] everywhere. It halves L1's target, doubles L3's.
VECTOR = (1.0, 0.5, 1.0, 2.0, 1.0)
ONES = (1.0,) * LEVELS

GEOMETRY = [
    "--compaction_style=0", "--level_compaction_dynamic_level_bytes=false",
    f"--num_levels={LEVELS}", f"--max_bytes_for_level_base={BASE}",
    f"--max_bytes_for_level_multiplier={RATIO}",
    "--write_buffer_size=131072", "--target_file_size_base=65536",
    f"--level0_file_num_compaction_trigger={L0_TRIGGER}",
    "--compaction_pri=3", "--compression_type=none", "--key_size=16",
    "--value_size=100", "--disable_wal=1", "--seed=1", "--threads=1",
]
FROZEN = ["--use_existing_db=1", "--disable_auto_compactions=1"]

# name -> (extra db_bench flags, text the refusal must carry). Messages are
# ColumnFamilyData::ValidateOptions's, and db_bench's own for a parse error.
SHRINKING = "1:2:0.5:1:1"  # 0.5 * T < 2: L2's target below L1's
REFUSALS = {
    "wrong_size": (["--level_target_multipliers=1:1:1"],
                   "needs one entry per level"),
    "entry0_not_1": (["--level_target_multipliers=2:1:1:1:1"], "must be 1.0"),
    "below_range": (["--level_target_multipliers=1:1:0.25:1:1"],
                    "is outside [0.5, 2.0]"),
    "above_range": (["--level_target_multipliers=1:1:1:1:3"],
                    "is outside [0.5, 2.0]"),
    "shrinking": ([f"--level_target_multipliers={SHRINKING}"],
                  "target smaller than level"),
    "dynamic_sizing": (["--level_target_multipliers=1:1:1:1:1",
                        "--level_compaction_dynamic_level_bytes=true"],
                       "requires level_compaction_dynamic_level_bytes = false"),
    "not_leveled": (["--level_target_multipliers=1:1:1:1:1",
                     "--compaction_style=1"],
                    "requires kCompactionStyleLevel"),
    "unparsable": (["--level_target_multipliers=1:x:1:1:1"],
                   "Invalid --level_target_multipliers"),
}
REFUSALS_EXPECTED = {name: text for name, (_, text) in REFUSALS.items()}
# The same shrinking vector sent through the `setoptions` step.
REFUSALS_EXPECTED["setoptions_shrinking"] = "target smaller than level"

STATS_ROW = re.compile(r"^\s*L(\d+)\s+\d+/\d+\s+[\d.]+\s+\S+\s+(\S+)", re.M)
PENDING = re.compile(r"^Estimated pending compaction bytes: (\d+)$", re.M)
SST_LEVEL = re.compile(r"^--- level (\d+) --- version# \d+")
SST_FILE = re.compile(r"^ (\d+):(\d+)\[")


def vector_arg(vector) -> str:
    return ":".join(f"{m:g}" for m in vector)


def parse_stats(text: str) -> dict:
    """Per-level Score strings and the pending estimate from one `stats`."""
    table = text.split("** Compaction Stats [default] **", 1)
    if len(table) < 2:
        raise ValueError("no compaction stats table")
    by_level = table[1].split("\n Sum ", 1)[0]
    pending = PENDING.search(text)
    if pending is None:
        raise ValueError("no pending compaction estimate")
    return {"scores": {int(level): score
                       for level, score in STATS_ROW.findall(by_level)},
            "pending": int(pending.group(1))}


def parse_sstables(text: str) -> dict[int, list[tuple[int, int]]]:
    """(file number, bytes) per level from one `sstables`."""
    files: dict[int, list[tuple[int, int]]] = {}
    level = None
    for line in text.splitlines():
        header = SST_LEVEL.match(line)
        if header:
            level = int(header.group(1))
            files[level] = []
            continue
        entry = SST_FILE.match(line)
        if entry and level is not None:
            files[level].append((int(entry.group(1)), int(entry.group(2))))
    if not files:
        raise ValueError("no sstables listing")
    return files


def targets(vector) -> list[int]:
    """MaxBytesForLevel per level, static ladder; L0's is never scaled."""
    base = [BASE] * LEVELS
    for level in range(2, LEVELS):
        base[level] = base[level - 1] * RATIO
    return [base[level] if level == 0 or vector[level] == 1.0
            else int(base[level] * vector[level]) for level in range(LEVELS)]


def level_bytes(files) -> list[int]:
    return [sum(size for _, size in files.get(level, []))
            for level in range(LEVELS)]


def model_scores(files, vector) -> dict[int, float]:
    """Scores of levels 0..L-2 (the last level has none). No file is being
    compacted in a frozen tree, and without deletions compensated size is the
    file size."""
    sizes = level_bytes(files)
    target = targets(vector)
    scores = {0: max(len(files.get(0, [])) / L0_TRIGGER, sizes[0] / BASE)}
    for level in range(1, LEVELS - 1):
        scores[level] = sizes[level] / target[level]
    return scores


def model_pending(files, vector) -> int:
    """VersionStorageInfo::EstimateCompactionBytesNeeded, static ladder."""
    sizes = level_bytes(files)
    target = targets(vector)
    l0_triggered = len(files.get(0, [])) >= L0_TRIGGER or sizes[0] >= BASE
    pending = sizes[0] if l0_triggered else 0
    carry = sizes[0] if l0_triggered else 0
    for level in range(1, LEVELS - 1):  # base_level() .. MaxInputLevel()
        size = sizes[level]
        if level == 1 and l0_triggered:
            pending += size
        size += carry
        carry = 0
        if size > target[level]:
            carry = size - target[level]
            below = sizes[level + 1]
            if below > 0:
                pending += int(float(carry) * (float(below) / float(size) + 1))
    return pending


def rendered(scores: dict[int, float], levels) -> dict[int, str]:
    """As `stats` prints them (%5.1f); Python and glibc round alike."""
    return {level: f"{scores[level]:.1f}" for level in levels}


def evaluate(obs: dict) -> dict:
    """The six ACT-1 checks from parsed observations.

    obs keys: absent, ones, vector, before, after -> {"stats", "sstables"};
    setoptions_log (LOG text after the SetOptions run); refusals ->
    {name: {"exit_code", "output"}}.
    """
    checks = {}

    def record(name, ok, details):
        checks[name] = {"passed": bool(ok), "details": details}

    files = obs["absent"]["sstables"]
    scored = [level for level in range(LEVELS - 1) if files.get(level)]
    frozen = all(obs[key]["sstables"] == files
                 for key in ("ones", "vector", "before", "after"))
    native = rendered(model_scores(files, ONES), scored)
    scaled = rendered(model_scores(files, VECTOR), scored)

    def printed(key):
        return {level: obs[key]["stats"]["scores"].get(level)
                for level in scored}

    record("tree_frozen", frozen and bool(scored),
           {"levels_with_files": scored})
    record("native_model",
           printed("absent") == native and
           obs["absent"]["stats"]["pending"] == model_pending(files, ONES),
           {"printed": printed("absent"), "model": native,
            "pending": obs["absent"]["stats"]["pending"],
            "model_pending": model_pending(files, ONES)})
    record("all_ones_equals_absent",
           obs["ones"]["stats"] == obs["absent"]["stats"],
           {"absent": obs["absent"]["stats"], "ones": obs["ones"]["stats"]})

    # A multiplier leaking into L0 (through max_bytes_for_level_base, A-Impl-1)
    # would rescale L0's size term; the tree must make that visible.
    sizes = level_bytes(files)
    count_term = len(files.get(0, [])) / L0_TRIGGER
    leaked = max(count_term, sizes[0] / (BASE * VECTOR[1]))
    l0 = [obs[key]["stats"]["scores"].get(0)
          for key in ("absent", "ones", "vector", "before", "after")]
    l0_sensitive = f"{leaked:.1f}" != native.get(0)
    record("l0_score_invariance",
           0 in scored and l0_sensitive and len(set(l0)) == 1 and
           l0[0] == native[0],
           {"printed": l0, "model": native.get(0),
            "if_m1_leaked": f"{leaked:.1f}", "sensitive": l0_sensitive})

    moved = [level for level in range(1, LEVELS - 1) if VECTOR[level] != 1.0]
    insensitive = [level for level in moved
                   if level not in scored or scaled[level] == native[level]]
    record("scaled_scores",
           not insensitive and printed("vector") == scaled,
           {"printed": printed("vector"), "model": scaled,
            "insensitive_levels": insensitive})

    pending_native = model_pending(files, ONES)
    pending_scaled = model_pending(files, VECTOR)
    record("scaled_pending",
           pending_scaled != pending_native and
           obs["vector"]["stats"]["pending"] == pending_scaled,
           {"printed": obs["vector"]["stats"]["pending"],
            "model": pending_scaled, "native_model": pending_native})

    log = obs["setoptions_log"]
    marker = log.find("succeeded, updated CF options")
    dumped = "level_target_multipliers: " + ":".join(f"{m:f}" for m in VECTOR)
    record("setoptions_recompute",
           obs["before"]["stats"] == obs["absent"]["stats"] and
           obs["after"]["stats"] == obs["vector"]["stats"] and
           marker >= 0 and dumped in log[marker:],
           {"before": obs["before"]["stats"], "after": obs["after"]["stats"],
            "log_shows_new_vector": marker >= 0 and dumped in log[marker:]})

    failures = {name: result for name, result in obs["refusals"].items()
                if result["exit_code"] == 0 or
                REFUSALS_EXPECTED[name] not in result["output"]}
    record("refusals", not failures and
           set(obs["refusals"]) == set(REFUSALS_EXPECTED),
           {"cases": sorted(obs["refusals"]),
            "failed": {name: result["output"][-400:]
                       for name, result in failures.items()}})
    return checks


def run(db_bench: Path, work: Path) -> dict:
    db = work / "db"
    for path in (db, work / "refused"):
        shutil.rmtree(path, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    def bench(name, *flags, db_dir=db, check=True):
        result = subprocess.run(
            [str(db_bench), *GEOMETRY, f"--db={db_dir}", *flags],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            errors="replace")
        (work / f"{name}.out").write_text(result.stdout)
        if check and result.returncode != 0:
            raise SystemExit(f"db_bench {name} exited {result.returncode}; "
                             f"see {work / (name + '.out')}")
        return result

    def observe(text):
        return {"stats": parse_stats(text), "sstables": parse_sstables(text)}

    # Native compaction lays out L1..L4, then flushes land in L0 unmerged.
    bench("build", "--benchmarks=fillrandom,waitforcompaction", "--num=30000",
          "--use_existing_db=0")
    bench("add_l0", "--benchmarks=overwrite,flush", "--num=2800", *FROZEN)
    obs = {}
    view = "--benchmarks=stats,sstables"
    obs["absent"] = observe(bench("absent", view, *FROZEN).stdout)
    obs["ones"] = observe(bench(
        "ones", view, *FROZEN,
        f"--level_target_multipliers={vector_arg(ONES)}").stdout)
    obs["vector"] = observe(bench(
        "vector", view, *FROZEN,
        f"--level_target_multipliers={vector_arg(VECTOR)}").stdout)
    text = bench("setoptions",
                 "--benchmarks=stats,sstables,setoptions,stats,sstables",
                 *FROZEN,
                 f"--setoptions=level_target_multipliers={vector_arg(VECTOR)}"
                 ).stdout
    parts = re.split(r"^setoptions\(.*$", text, maxsplit=1, flags=re.M)
    if len(parts) != 2:
        raise SystemExit(f"no setoptions line; see {work / 'setoptions.out'}")
    obs["before"], obs["after"] = observe(parts[0]), observe(parts[1])
    obs["setoptions_log"] = (db / "LOG").read_text(errors="replace")

    obs["refusals"] = {}
    for name, (flags, _) in REFUSALS.items():
        result = bench(f"refuse_{name}", "--benchmarks=stats",
                       "--use_existing_db=0", *flags, db_dir=work / "refused",
                       check=False)
        obs["refusals"][name] = {"exit_code": result.returncode,
                                 "output": result.stdout}
    result = bench("refuse_setoptions_shrinking", "--benchmarks=setoptions",
                   *FROZEN, f"--setoptions=level_target_multipliers={SHRINKING}",
                   check=False)
    obs["refusals"]["setoptions_shrinking"] = {
        "exit_code": result.returncode, "output": result.stdout}
    return obs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db-bench", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True,
                        help="scratch space; its db/ and refused/ are wiped")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    checks = evaluate(run(args.db_bench, args.work_dir))
    passed = all(check["passed"] for check in checks.values())
    report = {"criterion": "ACT-1", "passed": passed,
              "db_bench": str(args.db_bench), "checks": checks}
    rendered_report = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered_report)
    print(rendered_report, end="")
    for name, check in checks.items():
        print(f"[ACT-1] {name}: {'PASS' if check['passed'] else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
