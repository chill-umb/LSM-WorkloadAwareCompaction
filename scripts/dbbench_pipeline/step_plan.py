#!/usr/bin/env python3
"""The step runs of PREREGISTRATION D-31 and of the K-step batch (owner,
2026-10-21) for 34_step_chain.sh: the ordered run list, each step's change,
and the checks that bind a step run to its twin (native repeat 1 at (L1, K =
4), same workload and T).

  step_plan.py plan --trees DIR --keys N --seed S --t "3 6 9" \
      --workloads "W1 ... W5" --kinds "control single two" --num-levels 13 \
      [--l1 "128"] [--at-op 10000000] [--operations 40000000]
      one tab-separated line per run, in order:
      <phase> <W> <T> <L1 MiB> <step id> <HOLD_APPLY_M> <HOLD_APPLY_K0>
      <HOLD_APPLY_SCHEDULE>, "-" for each one the run does not set (a
      control sets none), and "missing <T> <L1 MiB> <reason>" for a tree
      that cannot be read
  step_plan.py twin --twin METADATA_ENV --tree TREE_DIR --seed S \
      --binary SHA --profile NAME
      exit 0 if the twin fits (same tree, seed, binary, workload), else 1
      with the reasons on stderr
  step_plan.py run --run METADATA_ENV --twin METADATA_ENV --apply A
      exit 0 if a finished step run matches its twin and made the change A
      ("<op>|<HOLD_APPLY_M>|", or "" for a control), else 1

The step list (D-31 §2): at every non-deepest level j (1 .. d-1, d the deepest
non-empty level of the tree, from tree.json's load record) a single-level
step to m in {0.5, 2, 0.75, 1.5}; on W1 and W2 only, each neighbouring pair
(j, j+1) of non-deepest levels to (2, 2), (0.5, 0.5), (2, 0.5), (0.5, 2); and
one presence control (no change) per workload and T. Order on a node:
controls, then x0.5 and x2, then x0.75 and x1.5, then the two-level steps;
within a phase by T, workload, level, multiplier. D-31's kinds run at
(128 MiB, K = 4) only.

The K-step batch's kinds, at every L1 of --l1 (base K = 4, every multiplier
1): "k0", one change of the L0 trigger at X to each value of K_TARGETS[L1];
and "multi", one run per (workload, T, L1) whose trigger changes several
times on multi_schedule's seeded schedule. Order: by T, L1, workload, then
the k0 steps in K_TARGETS' order, then the multi run.
"""
import random
import argparse
import json
import re
import sys
from pathlib import Path

PHASES = (
    ("control", None),
    ("single", ("0.5", "2")),
    ("single", ("0.75", "1.5")),
    ("two", None),
)
PAIRS = (("2", "2"), ("0.5", "0.5"), ("2", "0.5"), ("0.5", "2"))
TWO_LEVEL_WORKLOADS = ("W1", "W2")
D31_KINDS = ("control", "single", "two")
K_KINDS = ("k0", "multi")
# The K-step batch's new triggers per L1 base (MiB), from K = 4: below 4,
# up to L0's byte cap (L0 is due at 5, 9 or about 17 files of 15.2 MiB,
# whatever K is), and one value above the cap, which should act as the cap.
K_TARGETS = {64: (2, 3, 5, 8), 128: (2, 3, 6, 9, 12),
             256: (2, 3, 8, 12, 16, 19)}
BASE_K = 4
# multi_schedule: the first change at X, then gaps of 1M to 5M operations on
# a 0.5M grid, none in the last 2M; W5 also changes 0.5M before and after
# each phase switch (D-29 §2: 13,333,333 operations per phase).
MULTI_GAPS = tuple(range(1_000_000, 5_000_001, 500_000))
MULTI_QUIET_END = 2_000_000
W5_SWITCHES = (13_333_333, 26_666_666)
W5_OFFSET = 500_000


def tree_dir(trees: Path, t: int, keys: int, seed: int, l1: int = 128) -> Path:
    """The (L1 MiB, K = 4) tree of T, as 31 names it."""
    return trees / f"T{t}-l1{l1}-k{BASE_K}-n{keys}-s{seed}"


def deepest_level(tree_json: Path) -> int:
    """The deepest level holding files when the tree was built."""
    rows = json.loads(tree_json.read_text())["load"]["levels_files_mb"]
    levels = [int(level) for level, files, _ in rows if int(files) > 0]
    if not levels or max(levels) < 2:
        raise ValueError(f"{tree_json}: no level below L1 holds files")
    return max(levels)


def apply_vector(changes: dict[int, str], num_levels: int) -> str:
    """HOLD_APPLY_M: one value per level, L0's 1, the changed levels' m."""
    values = ["1"] * num_levels
    for level, m in changes.items():
        if not 1 <= level < num_levels:
            raise ValueError(f"level {level} is outside 1..{num_levels - 1}")
        values[level] = m
    return ":".join(values)


def steps(depths: dict[int, int], workloads: list[str], kinds: set[str],
          num_levels: int) -> list[tuple[str, str, int, str, str]]:
    """(phase, workload, T, step id, HOLD_APPLY_M or "-") in D-31's order."""
    out = []
    for kind, ms in PHASES:
        if kind not in kinds:
            continue
        for t in sorted(depths):
            levels = range(1, depths[t])
            for w in workloads:
                if kind == "control":
                    out.append((kind, w, t, "control", "-"))
                elif kind == "single":
                    for j in levels:
                        for m in ms:
                            out.append((kind, w, t, f"L{j}x{m}",
                                        apply_vector({j: m}, num_levels)))
                elif w in TWO_LEVEL_WORKLOADS:
                    for j in levels:
                        if j + 1 not in levels:
                            continue
                        for a, b in PAIRS:
                            out.append((kind, w, t, f"L{j}x{a}-L{j + 1}x{b}",
                                        apply_vector({j: a, j + 1: b}, num_levels)))
    return out


def multi_schedule(w: str, t: int, l1: int, at_op: int, operations: int,
                   num_levels: int) -> str:
    """HOLD_APPLY_SCHEDULE of the (workload, T, L1) multi run: ";"-separated
    "op|m|k0" entries, every multiplier 1, each trigger drawn from
    K_TARGETS[l1] and BASE_K, never the one in effect. Seeded by the run's
    name, so every node plans the same schedule."""
    rng = random.Random(f"kmulti/{w}/T{t}/l1{l1}")
    times = [at_op]
    while times[-1] + MULTI_GAPS[0] <= operations - MULTI_QUIET_END:
        nxt = times[-1] + rng.choice(MULTI_GAPS)
        if nxt > operations - MULTI_QUIET_END:
            break
        times.append(nxt)
    if w == "W5":
        forced = [s + d for s in W5_SWITCHES for d in (-W5_OFFSET, W5_OFFSET)]
        times = sorted({x for x in times if all(abs(x - f) >= W5_OFFSET for f in forced)}
                       | set(forced))
    ones = apply_vector({}, num_levels)
    values = sorted(set(K_TARGETS[l1]) | {BASE_K})
    entries, k = [], BASE_K
    for at in times:
        k = rng.choice([v for v in values if v != k])
        entries.append(f"{at}|{ones}|{k}")
    return ";".join(entries)


def k_steps(ts: list[int], l1s: list[int], workloads: list[str], kinds: set[str],
            at_op: int, operations: int, num_levels: int) -> list[tuple]:
    """(phase, workload, T, L1, step id, HOLD_APPLY_M, HOLD_APPLY_K0,
    HOLD_APPLY_SCHEDULE) of the K-step batch, "-" where unset."""
    out = []
    ones = apply_vector({}, num_levels)
    for t in ts:
        for l1 in l1s:
            for w in workloads:
                if "k0" in kinds:
                    for k in K_TARGETS[l1]:
                        out.append(("k0", w, t, l1, f"K{k}", ones, str(k), "-"))
                if "multi" in kinds:
                    out.append(("multi", w, t, l1, "Kmulti", "-", "-",
                                multi_schedule(w, t, l1, at_op, operations, num_levels)))
    return out


def read_env(path: Path) -> dict[str, str]:
    """metadata.env's key=value lines (printf '%s' values, unquoted)."""
    out = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key] = value
    return out


def binary_of(env: dict[str, str]) -> str:
    """The binary identity in a run's fingerprint (its binary<sha> segment)."""
    found = re.search(r"(?:^|:)binary([0-9a-f]{64})(?::|$)", env.get("experiment_fingerprint", ""))
    return found.group(1) if found else ""


def twin_problems(twin: dict[str, str], tree: Path, seed: str, binary: str,
                  profile: str) -> list[str]:
    problems = []
    identity = json.loads((tree / "tree.json").read_text())["identity"]
    if twin.get("tree_sha256") != identity:
        problems.append(f"tree: the twin ran on {twin.get('tree_sha256')}, this tree is {identity}")
    if twin.get("dbbench_seed") != seed:
        problems.append(f"seed: the twin ran with {twin.get('dbbench_seed')}, a step runs with {seed}")
    if binary_of(twin) != binary:
        problems.append(f"binary: the twin ran on {binary_of(twin) or 'none'}, this db_bench is {binary}")
    fingerprint = twin.get("experiment_fingerprint", "")
    if fingerprint.split(":", 1)[0] != profile:
        problems.append(f"workload: the twin is {fingerprint.split(':', 1)[0] or 'none'}, not {profile}")
    return problems


def run_problems(run: dict[str, str], twin: dict[str, str], apply: str) -> list[str]:
    problems = []
    for key in ("tree_sha256", "dbbench_seed"):
        if run.get(key) != twin.get(key):
            problems.append(f"{key}: {run.get(key)} against the twin's {twin.get(key)}")
    if binary_of(run) != binary_of(twin):
        problems.append(f"binary: {binary_of(run)} against the twin's {binary_of(twin)}")
    if run.get("hold_apply") != apply:
        problems.append(f"hold_apply: {run.get('hold_apply')!r}, planned {apply!r}")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--trees", type=Path, required=True)
    plan.add_argument("--keys", type=int, required=True)
    plan.add_argument("--seed", type=int, required=True)
    plan.add_argument("--t", required=True)
    plan.add_argument("--workloads", required=True)
    plan.add_argument("--kinds", required=True)
    plan.add_argument("--num-levels", type=int, required=True)
    plan.add_argument("--l1", default="128",
                      help="L1 bases in MiB (D-31's kinds: 128 only)")
    plan.add_argument("--at-op", type=int, default=10_000_000)
    plan.add_argument("--operations", type=int, default=40_000_000)
    twin = sub.add_parser("twin")
    twin.add_argument("--twin", type=Path, required=True)
    twin.add_argument("--tree", type=Path, required=True)
    twin.add_argument("--seed", required=True)
    twin.add_argument("--binary", required=True)
    twin.add_argument("--profile", required=True)
    run = sub.add_parser("run")
    run.add_argument("--run", type=Path, required=True)
    run.add_argument("--twin", type=Path, required=True)
    run.add_argument("--apply", required=True)
    args = parser.parse_args(argv)

    if args.command == "plan":
        kinds = set(args.kinds.split())
        unknown = kinds - set(D31_KINDS) - set(K_KINDS)
        if unknown:
            parser.error(f"unknown kinds {sorted(unknown)}")
        l1s = [int(x) for x in args.l1.split()]
        if set(l1s) - set(K_TARGETS):
            parser.error(f"L1 bases must be among {sorted(K_TARGETS)}; got {l1s}")
        if kinds & set(D31_KINDS) and l1s != [128]:
            parser.error("D-31's kinds run at 128 MiB only")
        ts = [int(x) for x in args.t.split()]
        depths, readable = {}, []
        for t in ts:
            for l1 in l1s:
                tree = tree_dir(args.trees, t, args.keys, args.seed, l1)
                try:
                    if kinds & set(D31_KINDS):
                        depths[t] = deepest_level(tree / "tree.json")
                    else:
                        json.loads((tree / "tree.json").read_text())["identity"]
                    readable.append((t, l1))
                except (OSError, ValueError, KeyError) as error:
                    print(f"missing\t{t}\t{l1}\t{error}")
        for phase, w, t, sid, vector in steps(depths, args.workloads.split(), kinds,
                                              args.num_levels):
            print("\t".join(str(x) for x in (phase, w, t, 128, sid, vector, "-", "-")))
        rows = k_steps(ts, l1s, args.workloads.split(), kinds, args.at_op,
                       args.operations, args.num_levels)
        for row in rows:
            if (row[2], row[3]) in readable:
                print("\t".join(str(x) for x in row))
        return 0
    if args.command == "twin":
        problems = twin_problems(read_env(args.twin), args.tree, args.seed,
                                 args.binary, args.profile)
    else:
        problems = run_problems(read_env(args.run), read_env(args.twin), args.apply)
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
