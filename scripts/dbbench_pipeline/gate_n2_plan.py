#!/usr/bin/env python3
"""Gate N2's arms in run order, for 25_gate_n2_chain.sh (PATHWAYS C §1, §3;
PREREGISTRATION D-13 §4-5, D-14 §3, D-15 §3, D-19).

  plan <workload> ...   the steps 25 runs, one per line, then '#' lines with
                        the excluded points and the cost:
                          run <point> <T> <base MiB> <K0> <k> <arm>...
                            03 at that point with REPEATS=k: repeats 1..k of
                            each arm, the finished ones skipped (RESUME=1)
                          profiles <point> <T> <base MiB> <K0>
                            23 on the point's settled native runs
  profiles [--compute] <point folder> <T> <base MiB> <K0> <prefix> <size M>
                        the point's own vectors, STATIC_PROFILE_<name>=<vector>,
                        and "refused <name>: <why>" (23 refused it) or
                        "skipped <name>: <why>" (no profiles.json yet). With
                        --compute, runs 23 first if the point has none and
                        has the initial native runs; fails if 23 fails
  check --prices <file> --db-bench <file> [--exploratory] <workload>...
                        refuses unless q-bar is recorded for each workload and
                        the prices file is final (two of 18's sessions that
                        agreed, PREREGISTRATION D-22 f and j), measured on
                        this db_bench (its identity as loaded: executable
                        plus librocksdb, preflight_marker.db_bench_identity).
                        --exploratory (D-24 §2) also accepts provisional
                        prices; every other check stands
  explore-marker --prices <file> --db-bench <file> --session <id>
                 <workload root> [<dir>...]
                        D-24 §2's EXPLORATORY marker: written once at the
                        workload root (refused if one there records other
                        prices or another db_bench), then copied into each
                        <dir> and every result directory under the root
                        (03's results roots and its arm folders)

The formal sweep's results are $NVME/n2-<workload>, session n2-<workload>; an
exploratory screen's (D-24 §2) are $NVME/explore-n2-<workload>, session
explore-n2-<workload>, so the two are never pooled (plan --prefix).

Order within one (workload, T): every native and uniform_0_75 arm, repeat by
repeat across the points; then 23 at each point; then the measured profiles,
repeat by repeat across the points. Repeat-major order keeps two points' runs
of the same repeat (the same seed, the pair C-2 and CMP-3 difference) close
in time, so session drift cancels in the paired difference (C §3). Rounds
start at k = 2, because 03 lays out repeat folders only when REPEATS > 1.

A point with measured profiles runs at least D-13 §5's initial native runs,
in a screen too. Its profiles.json is computed once, from its settled native
runs, and never again: arms may have run with its vectors. It records the
runs it pooled (repeat-NN/native), and is refused if those are no longer
exactly the point's settled native runs up to the last of them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import preflight_marker
import research_objective

PIPELINE = Path(__file__).resolve().parent
MEASURED = ("survival_weighted", "last_level_emptying")
SECONDS_PER_10M_OPERATIONS = 160  # rough node cost of one run, for the listing
# Results folder and session prefixes: the formal sweep, and D-24 §2's
# exploratory screen, which is never pooled with it.
PREFIXES = ("n2", "explore-n2")
MARKER = "EXPLORATORY"
# What a resumed screen must share with the marker it started under.
MARKER_BINDING = ("prices_sha256", "db_bench_identity")


def family(contract: dict, workload: str) -> str:
    """The contract workload of one of 24's workload names, which are the
    first word of the contract's (assoc, powerlaw_get95)."""
    found = [f for f in contract["workloads"] if f.split("_")[0] == workload]
    if len(found) != 1:
        raise ValueError(f"workload {workload} is none of the contract's "
                         f"{list(contract['workloads'])}")
    return found[0]


def arm(profile: str) -> str:
    return "native" if profile == "uniform_1" else f"static:{profile}"


def point_id(t: int, base: int, k0: int) -> str:
    return f"T{t}-b{base}-k{k0}"


def k_cap(base_mib: int, write_buffer: int, slowdown: int) -> int:
    """A-Impl-6: K_cap = min(floor(C1 / F), K_slow - 1). F is read as the
    configured write buffer, the largest a flushed file can be, so K_cap is
    the smallest the byte branch of L0's score can make it; K_slow is the
    configured L0 slowdown trigger."""
    return min(base_mib * 2**20 // write_buffer, slowdown - 1)


def grid(sc: dict, ts: list[int], n: int, write_buffer: int, slowdown: int):
    """Theta_s at the given T values: (T, base, K0, profile, n) per
    configuration, and the (base, K0) points A-Impl-6 excludes, with why."""
    configs, excluded = [], []
    for base in sc["base_size_mib"]:
        cap = k_cap(base, write_buffer, slowdown)
        for k0 in sc["l0_trigger"]:
            if not 2 <= k0 <= cap:
                excluded.append(
                    f"base {base} MiB, K0 = {k0}: above K_cap = min(floor({base} MiB"
                    f" / {write_buffer / 2**20:g} MiB), {slowdown} - 1) = {cap} (A-Impl-6)")
    for t in ts:
        for base in sc["base_size_mib"]:
            for k0 in sc["l0_trigger"]:
                if 2 <= k0 <= k_cap(base, write_buffer, slowdown):
                    configs += [(t, base, k0, p, n) for p in sc["profiles"]]
    return configs, excluded


def read_configs(path: Path, workload: str, contract: dict, write_buffer: int,
                 slowdown: int) -> list[tuple]:
    """A named set of configurations, one per line:
         <workload> <T> <base MiB> <K0> <profile> <repeats>
    repeats is the total wanted, not the extra, so a rerun adds nothing.
    '#' starts a comment. Returns this workload's lines."""
    sc = contract["static_class"]
    cross_ts = sc["cross_t_check"]["size_ratios"]
    # D-13 §4: at T = 14 and 20, each mode's theta* at T = 10.
    most = len(contract["objective"]["modes"])
    ts = sc["size_ratios"] + cross_ts
    seen, cross, mine = set(), {}, []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        where = f"{path}:{number}"
        if len(fields) != 6:
            raise ValueError(f"{where}: want <workload> <T> <base MiB> <K0> "
                             f"<profile> <repeats>, got {line.strip()!r}")
        w, profile = fields[0], fields[4]
        try:
            t, base, k0, n = (int(fields[i]) for i in (1, 2, 3, 5))
            family(contract, w)
        except ValueError as error:
            raise ValueError(f"{where}: {error}") from None
        problem = (f"T = {t} is not in {ts}" if t not in ts
                   else f"base {base} MiB is not in {sc['base_size_mib']}"
                   if base not in sc["base_size_mib"]
                   else f"K0 = {k0} is not in {sc['l0_trigger']}"
                   if k0 not in sc["l0_trigger"]
                   else f"K0 = {k0} is not admissible at base {base} MiB (A-Impl-6)"
                   if k0 > k_cap(base, write_buffer, slowdown)
                   else f"profile {profile} is not in {list(sc['profiles'])}"
                   if profile not in sc["profiles"]
                   else "repeats must be at least 2 (03 lays out repeat "
                        "folders only when REPEATS > 1)" if n < 2
                   else "listed twice" if (w, t, base, k0, profile) in seen
                   else None)
        if problem:
            raise ValueError(f"{where}: {problem}")
        seen.add((w, t, base, k0, profile))
        if t in cross_ts:
            cross.setdefault(w, set()).add((base, k0, profile))
        if w == workload:
            mine.append((t, base, k0, profile, n))
    for w, configurations in cross.items():
        if len(configurations) > most:
            raise ValueError(f"{path}: {len(configurations)} cross-T configurations "
                             f"for {w}; D-13 §4 allows {most}, one per mode")
    return mine


def rounds(t: int, arms_at: dict) -> list[str]:
    top = max((n for arms in arms_at.values() for n in arms.values()), default=0)
    return [f"run {point_id(t, base, k0)} {t} {base} {k0} {k} "
            + " ".join(a for a, n in arms.items() if n >= k)
            for k in range(2, top + 1)
            for (base, k0), arms in arms_at.items()
            if any(n >= k for n in arms.values())]


def steps(configs: list[tuple], has_profiles, initial: int) -> list[str]:
    """has_profiles(point id): 23 already ran there, its vectors frozen.
    A measured profile at a point without profiles runs the point's initial
    native runs first (D-13 §5), also in a screen of fewer repeats: the
    screen is then 27's design, natives in full and the rest screened."""
    out = []
    for t in sorted({c[0] for c in configs}):
        first, measured = {}, {}
        for _, base, k0, profile, n in (c for c in configs if c[0] == t):
            if profile in MEASURED:
                measured.setdefault((base, k0), {})[arm(profile)] = n
                if has_profiles(point_id(t, base, k0)):
                    continue
                profile, n = "uniform_1", initial
            arms = first.setdefault((base, k0), {})
            arms[arm(profile)] = max(n, arms.get(arm(profile), 0))
        out += rounds(t, first)
        out += [f"profiles {point_id(t, base, k0)} {t} {base} {k0}" for base, k0 in measured]
        out += rounds(t, measured)
    return out


def plan(args) -> int:
    contract, _ = research_objective.load_contract()
    family(contract, args.workload)
    sc, initial = contract["static_class"], contract["repeats"]["initial"]
    n = args.repeats or initial
    if n < 2:
        raise ValueError("N2_REPEATS must be at least 2 (03 lays out repeat "
                         "folders only when REPEATS > 1)")
    if args.configs:
        configs, excluded = read_configs(args.configs, args.workload, contract,
                                         args.write_buffer, args.slowdown), []
    else:
        ts = [int(t) for t in args.t.split()] if args.t else sc["size_ratios"]
        wrong = [t for t in ts if t not in sc["size_ratios"]]
        if wrong:
            raise ValueError(f"N2_T {wrong} is not in Theta_s's {sc['size_ratios']}; "
                             "cross-T runs go through N2_CONFIGS")
        configs, excluded = grid(sc, ts, n, args.write_buffer, args.slowdown)
    # D-19: levels Gate N1 left undecided are not decided here (D-16 §5
    # withdrawn), so every native arm runs the initial repeats.
    root = args.nvme / f"{args.prefix}-{args.workload}"
    planned = steps(configs, lambda p: (root / p / "profiles.json").exists(),
                    initial)
    print("\n".join(planned))
    # Counted from the steps: each arm runs up to its last round's k.
    last = {}
    for step in planned:
        fields = step.split()
        if fields[0] == "run":
            for a in fields[6:]:
                last[fields[1], a] = int(fields[5])
    runs = {}
    for (point, _), k in last.items():
        runs[point.split("-")[0]] = runs.get(point.split("-")[0], 0) + k
    total = sum(runs.values())
    hours = total * SECONDS_PER_10M_OPERATIONS * args.size / 10 / 3600
    for line in excluded:
        print(f"# excluded: {line}")
    for t, count in sorted(runs.items(), key=lambda item: int(item[0][1:])):
        print(f"# {args.workload} {t}: {count} runs")
    print(f"# {args.workload}: {total} runs of {args.size}M, about {hours:.0f} h "
          f"at {SECONDS_PER_10M_OPERATIONS} s per 10M operations (finished arms "
          "are skipped; refused profiles are not run)")
    return 0


def settled_natives(folder: Path) -> list[Path]:
    return sorted(d for d in folder.glob("repeat-*/native") if (d / "COMPLETED").exists())


def repeat(run: Path) -> int:
    return int(run.parent.name.split("-")[1])


def point_profiles(args, initial: int):
    """The point's profiles.json, checked; or None and why there is none.
    Its runs are recorded as repeat-NN/native, relative to the point's
    <size>M/T<T> folder, so a moved or remounted NVME keeps them valid."""
    path = (args.point / "profiles.json").resolve()
    folder = args.point / f"{args.size}M" / f"T{args.t}"
    natives = [str(run.relative_to(folder)) for run in settled_natives(folder)]
    if not path.exists():
        if not args.compute:
            return None, "no profiles.json at this point"
        if len(natives) < initial:
            return None, (f"{len(natives)} settled native runs; the profiles wait "
                          f"for {initial} (D-13 §5)")
        done = subprocess.run([sys.executable, str(PIPELINE / "23_static_profiles.py"),
                               *natives, "--output", str(path)],
                              cwd=folder, capture_output=True, text=True)
        (args.point / "profiles.log").write_text(done.stdout + done.stderr)
        if not path.exists():
            # Not one of D-14 §3's refusals, which 23 records in the report.
            last = (done.stderr.strip().splitlines() or ["no output"])[-1]
            raise ValueError(f"23 failed at {args.point} (exit {done.returncode}; "
                             f"see profiles.log): {last}")
    report = json.loads(path.read_text())
    inputs = report["inputs"]  # None when 23 refused both (the runs' L differ)
    wanted = {"T": float(args.t), "K0": args.k0, "C1": float(args.base * 2**20)}
    if inputs is not None and ({k: inputs[k] for k in wanted} != wanted
                               or not inputs["fingerprint"].startswith(args.prefix)):
        raise ValueError(f"{path} was measured at {inputs['fingerprint']}, not at "
                         f"{wanted} and {args.prefix}")
    recorded = sorted(report["runs"])
    top = max(repeat(Path(run)) for run in recorded)
    current = sorted(run for run in natives if repeat(Path(run)) <= top)
    if recorded != current:
        raise ValueError(f"{path} was computed from {recorded}; the point's settled "
                         f"native runs up to repeat {top} are now {current}. Arms may "
                         "have run with its vectors, so it is not recomputed: decide "
                         "by hand")
    return report, None


def profiles(args) -> int:
    contract, _ = research_objective.load_contract()
    report, why = point_profiles(args, contract["repeats"]["initial"])
    for name in MEASURED:
        if report is None:
            print(f"skipped {name}: {why}")
        elif "vector" in report[name]:
            print(f"STATIC_PROFILE_{name}={report[name]['vector']}")
        else:
            print(f"refused {name}: {report[name]['refused']}")
    return 0


def check(args) -> int:
    contract, _ = research_objective.load_contract()
    problems = []
    for w in args.workloads:
        try:
            f = family(contract, w)
        except ValueError as error:
            problems.append(str(error))
            continue
        if research_objective.reference_rate(contract, f) is None:
            problems.append(f"q-bar for {w} ({f}) is not recorded in "
                            "config/research_objective_contract.json; D-13 §1 and "
                            "D-14 §2 freeze it before any Theta_s run")
    # db_bench as loaded (executable plus its librocksdb), as 18 records it.
    try:
        identity = preflight_marker.db_bench_identity(args.db_bench)
    except (OSError, preflight_marker.IdentityError) as error:
        identity = None
        problems.append(f"cannot identify db_bench {args.db_bench}: {error}")
    try:
        record = json.loads(args.prices.read_text())
        # D-24 §2: an exploratory screen runs on provisional prices, as
        # D-22 (j)'s diagnostic runs; the formal sweep only on final ones.
        research_objective.checked_prices(record, contract,
                                          provisional_ok=args.exploratory)
        if identity is not None and record.get("db_bench_sha256") != identity:
            raise ValueError(f"measured on db_bench {record.get('db_bench_sha256')}, "
                             f"not on {args.db_bench} ({identity}, executable "
                             "plus librocksdb); D-15 §3e re-measures them "
                             "after any binary change")
    except (OSError, ValueError, KeyError, AttributeError) as error:
        problems.append(f"prices {args.prices}: {error}; run 18's two "
                        "sessions and its compare (D-15 §3e, D-22 f)")
    if problems:
        raise ValueError("\n".join(problems))
    return 0


def read_marker(path: Path) -> dict:
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if "=" in line)


def explore_marker(args) -> int:
    """D-24 §2: every exploratory result directory carries the same
    EXPLORATORY file, saying what the runs are and what they were priced
    with. Written once per workload root; a resume under other prices or
    another db_bench is refused, so one screen has one of each."""
    contract, _ = research_objective.load_contract()
    record = json.loads(args.prices.read_text())
    current = {
        "prices_sha256": hashlib.sha256(args.prices.read_bytes()).hexdigest(),
        "db_bench_identity": preflight_marker.db_bench_identity(args.db_bench)}
    marker = args.root / MARKER
    if marker.exists():
        recorded = read_marker(marker)
        changed = [f"{key} {recorded.get(key)} -> {current[key]}"
                   for key in MARKER_BINDING if recorded.get(key) != current[key]]
        if changed:
            raise ValueError(f"{marker} records another screen ({'; '.join(changed)}); "
                             "resume it with its own prices file and db_bench, or "
                             "start a new screen in another folder")
    else:
        args.root.mkdir(parents=True, exist_ok=True)
        fields = {
            "exploratory": "1",
            "decision": "PREREGISTRATION D-24 §2",
            "use": "ranking only: no claim, no gate verdict; never pooled with "
                   "the gate runs",
            "session_id": args.session,
            "prices_file": str(args.prices.resolve()),
            **current,
            "prices_schema": str(record.get("schema")),
            "prices_status": ("final" if research_objective.prices_final(record, contract)
                              else "provisional"),
            "written_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        marker.write_text("".join(f"{k}={v}\n" for k, v in fields.items()))
    # 03's results roots hold effective_config.env, its arm folders
    # metadata.env; a partial arm has its metadata.env from the start.
    targets = {d.resolve() for d in args.dirs}
    targets |= {p.parent for name in ("metadata.env", "effective_config.env")
                for p in args.root.resolve().rglob(name)}
    for target in sorted(targets):
        target.mkdir(parents=True, exist_ok=True)
        if not (target / MARKER).exists():
            shutil.copyfile(marker, target / MARKER)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("workload")
    p.add_argument("--nvme", type=Path, required=True)
    p.add_argument("--size", type=int, required=True, help="run size, millions")
    p.add_argument("--repeats", type=int, help="default: the contract's initial")
    p.add_argument("--t", help="T values of the sweep, default Theta_s's")
    p.add_argument("--configs", type=Path, help="a named set instead of the sweep")
    p.add_argument("--prefix", choices=PREFIXES, default="n2",
                   help="results folder <nvme>/<prefix>-<workload> (default n2; "
                        "explore-n2 for D-24 §2's exploratory screen)")
    p.add_argument("--write-buffer", type=int, required=True)
    p.add_argument("--slowdown", type=int, required=True)
    v = sub.add_parser("profiles")
    v.add_argument("--compute", action="store_true")
    v.add_argument("point", type=Path)
    v.add_argument("t", type=int)
    v.add_argument("base", type=int)
    v.add_argument("k0", type=int)
    v.add_argument("prefix", help="<workload profile>:<size>M:T<T>:")
    v.add_argument("size", type=int)
    c = sub.add_parser("check")
    c.add_argument("--prices", type=Path, required=True)
    c.add_argument("--db-bench", type=Path, required=True)
    c.add_argument("--exploratory", action="store_true",
                   help="D-24 §2's screen: provisional prices accepted")
    c.add_argument("workloads", nargs="+")
    m = sub.add_parser("explore-marker")
    m.add_argument("--prices", type=Path, required=True)
    m.add_argument("--db-bench", type=Path, required=True)
    m.add_argument("--session", required=True)
    m.add_argument("root", type=Path, help="the screen's workload root")
    m.add_argument("dirs", type=Path, nargs="*", help="more folders to mark")
    args = parser.parse_args()
    try:
        return {"plan": plan, "profiles": profiles, "check": check,
                "explore-marker": explore_marker}[args.command](args)
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    raise SystemExit(main())
