#!/usr/bin/env python3
"""The collapse test (PATHWAYS G §4, PROP-1, Gate N1): which interior levels
may share one value function with the reference level.

Per level, on its own clock, from static runs' measured phases (host log
job records; the event log's compaction_release occupancy for fills):
  a turnover of level i ends each time the net bytes landing in it (X - O
  per merge from i-1, S per trivial move) add up to another C_i; lengths
  are counted in operations (G-i).
Per turnover, the compared statistics:
  fill_sampled        fill B_i/C_i at k evenly spaced points of the turnover
  fill_at_release     fill at each release from level i
  released            bytes released from level i, in units of C_i
  passthrough_overlap (1 - xi)(rho + o) = sum X over merges / sum S
  rho_tilde           xi + (1 - xi) rho = (moved + sum (X - O)) / sum S
  inflow_ratio        l_i: this turnover's inflow rate over the run's mean
and, bounded rather than compared, the slot wait omega_i per release: from
the later of the level becoming due and its previous job ending, to the job
starting, in operations, in units of the decision interval N_i/k.

A level joins the pool only if, for every compared statistic, the 90%
moving-block-bootstrap intervals of its differences from the reference in
mean, 10th and 90th percentile lie inside +-margin, and the upper 95% bound
on its mean omega is below omega_max. Blocks are whole turnovers, resampled
within each run. Every value the test uses (reference level, margins,
omega_max, block length, k, n_min, replicates, seed) comes from a config file
fixed in advance (PATHWAYS §0.6 item 7); none has a default here.
A Kolmogorov-Smirnov distance is reported, not judged.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import re
import statistics
from pathlib import Path

import host_log

STATISTICS = ("fill_sampled", "fill_at_release", "released",
              "passthrough_overlap", "rho_tilde", "inflow_ratio")
FILL_STATISTICS = ("fill_sampled", "fill_at_release")
SUMMARIES = ("mean", "p10", "p90")
EVENT = re.compile(r"EVENT_LOG_v1 (\{.*\})")


# --- statistics of resampled turnovers --------------------------------------

def percentile(values: list[float], q: float) -> float:
    """Linear interpolation between order statistics (numpy's default)."""
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarize(turnovers: list[dict], stat: str) -> dict[str, float] | None:
    values = [v for t in turnovers for v in t.get(stat, ())]
    if not values:
        return None
    return {"mean": statistics.fmean(values), "p10": percentile(values, 0.1),
            "p90": percentile(values, 0.9)}


def block_resample(runs: list[list[dict]], block: int,
                   rng: random.Random) -> list[dict]:
    """Moving-block bootstrap within each run: blocks of `block` consecutive
    turnovers with uniform starts, concatenated to the run's length."""
    out = []
    for run in runs:
        n = len(run)
        b = min(block, n)
        drawn: list[dict] = []
        while len(drawn) < n:
            start = rng.randrange(n - b + 1)
            drawn.extend(run[start:start + b])
        out.extend(drawn[:n])
    return out


def shifted(runs: list[list[dict]], stat: str, by: float) -> list[list[dict]]:
    return [[{**t, stat: [v + by for v in t.get(stat, ())]} for t in run]
            for run in runs]


def compare(candidate: list[list[dict]], reference: list[list[dict]],
            config: dict, rng: random.Random) -> dict:
    """The equivalence test of every statistic in config["margins"]."""
    margins = config["margins"]
    diffs = {(s, q): [] for s in margins for q in SUMMARIES}
    for _ in range(config["replicates"]):
        a = block_resample(candidate, config["block_length"], rng)
        b = block_resample(reference, config["block_length"], rng)
        for stat in margins:
            sa, sb = summarize(a, stat), summarize(b, stat)
            if sa is None or sb is None:
                continue
            for q in SUMMARIES:
                diffs[stat, q].append(sa[q] - sb[q])
    result = {}
    for stat, margin in margins.items():
        intervals = {}
        for q in SUMMARIES:
            values = diffs[stat, q]
            intervals[q] = ([percentile(values, 0.05), percentile(values, 0.95)]
                            if values else None)
        result[stat] = {
            "margin": margin, "intervals": intervals,
            "passed": all(iv is not None and -margin <= iv[0] and iv[1] <= margin
                          for iv in intervals.values())}
    return {"statistics": result,
            "passed": all(r["passed"] for r in result.values())}


def omega_upper_bound(runs: list[list[dict]], config: dict,
                      rng: random.Random) -> float | None:
    """Upper 95% bootstrap bound on the mean wait per release."""
    means = []
    for _ in range(config["replicates"]):
        waits = [w for t in block_resample(runs, config["block_length"], rng)
                 for w in t.get("omega", ())]
        if waits:
            means.append(statistics.fmean(waits))
    return percentile(means, 0.95) if means else None


def ks_distance(a: list[float], b: list[float]) -> float:
    a, b = sorted(a), sorted(b)
    return max(abs(bisect.bisect_right(a, x) / len(a) -
                   bisect.bisect_right(b, x) / len(b)) for x in a + b)


def ks_upper_bound(a: list[float], b: list[float], replicates: int,
                   rng: random.Random) -> float:
    """Upper 95% bootstrap bound on the KS distance: reported, never judged
    (it has no scale, and between identical distributions its median is
    0.43 at 30 turnovers)."""
    return percentile([ks_distance(rng.choices(a, k=len(a)),
                                   rng.choices(b, k=len(b)))
                       for _ in range(replicates)], 0.95)


def pseudo_level_rates(reference: list[list[dict]], n: int, config: dict,
                       trials: int, rng: random.Random) -> dict:
    """The n_min simulation: two pseudo-levels of n turnovers each, drawn
    from the reference's own turnovers. Returns how often they pass as they
    are (must be >= 0.8) and with one statistic shifted to its margin (each
    must be <= 0.05)."""
    runs = [run for run in reference if run]

    def level():
        # n turnovers in blocks of consecutive ones from the reference's runs.
        drawn: list[dict] = []
        while len(drawn) < n:
            run = rng.choice(runs)
            b = min(config["block_length"], len(run))
            start = rng.randrange(len(run) - b + 1)
            drawn.extend(run[start:start + b])
        return [drawn[:n]]

    def pair():
        return level(), level()

    same = sum(compare(*pair(), config, rng)["passed"]
               for _ in range(trials)) / trials
    shifted_rates = {}
    for stat, margin in config["margins"].items():
        passed = 0
        for _ in range(trials):
            a, b = pair()
            passed += compare(a, shifted(b, stat, margin), config, rng)["passed"]
        shifted_rates[stat] = passed / trials
    return {"n": n, "pass_rate_identical": same,
            "pass_rate_shifted": shifted_rates,
            "sufficient": same >= 0.8 and
                          max(shifted_rates.values()) <= 0.05}


# --- turnovers from a run's logs ---------------------------------------------

def op_at(clock: list[tuple[int, int]], t_us: int) -> float:
    """Operations served by steady-clock time t_us, interpolated between
    the host log's (t_us, op) records."""
    times = [t for t, _ in clock]
    i = bisect.bisect_right(times, t_us)
    if i == 0:
        return float(clock[0][1])
    if i == len(clock):
        return float(clock[-1][1])
    (t0, op0), (t1, op1) = clock[i - 1], clock[i]
    return op0 + (op1 - op0) * (t_us - t0) / (t1 - t0) if t1 > t0 else float(op1)


def release_fills(events: list[dict]) -> dict[int, list[float]]:
    """Per job id, every level's fill B/C_nominal at its release."""
    out = {}
    for e in events:
        if e.get("event") == "compaction_release":
            out[int(e["job"])] = [b / c if c else math.nan for b, c in
                                  zip(e["occupancy_bytes"],
                                      e["nominal_target_bytes"])]
    return out


def level_turnovers(records: list[dict], fills: dict[int, list[float]],
                    level: int, target: float,
                    k: int) -> tuple[list[dict], int]:
    """Level `level`'s turnovers in the measured phase (measure_start to
    drain_start; the drain is not the workload's dynamics), and how many
    were empty: one job landing 2 C_i or more ends two turnovers at the same
    operation, and the empty one is dropped, not scored. Each turnover also
    carries the (c3) diagnostics, reported but never compared: its job count
    and the gaps between its jobs, in turnovers."""
    at = host_log.stamp_index(records)
    window = records[at["measure_start"]:at["drain_start"] + 1]
    clock = [(r["t_us"], r["op"]) for r in window if "t_us" in r and "op" in r]
    begins = {r["job"]: r for r in window if r.get("type") == "job_begin"}
    ends = [r for r in window if r.get("type") == "job_end" and r["ok"] == 1
            and r["job"] in begins]
    first = window[0]["op"]

    boundaries, landed = [first], 0.0
    for r in ends:
        if r["output_level"] == level and r["start_level"] == level - 1:
            landed += r["s"] if r["trivial"] else r["x"] - r["o"]
            while landed >= target * len(boundaries):
                boundaries.append(r["op"])
    if len(boundaries) < 2:
        return [], 0
    mean_ops = (boundaries[-1] - first) / (len(boundaries) - 1)

    # Fill at an operation: the latest release snapshot at or before it.
    snapshots = sorted((begins[j]["op"], f[level]) for j, f in fills.items()
                       if j in begins)
    snapshot_ops = [op for op, _ in snapshots]

    def fill(op):
        i = bisect.bisect_right(snapshot_ops, op)
        return snapshots[i - 1][1] if i else None

    # Ordered by start; two jobs may start at one operation count while a
    # write stop holds the client, so the steady clock breaks the tie.
    releases = sorted(((begins[r["job"]], r) for r in ends
                       if r["start_level"] == level),
                      key=lambda pair: (pair[0]["op"], pair[0]["t_us"]))
    # G §3: a wait runs from the later of the level becoming due and its
    # previous job ending: the latest of its jobs to end before this start,
    # on the steady clock (operation counts can stand still in a write
    # stop). A job still running when this one starts is not waited for.
    ended = sorted((e["t_us"], e["op"]) for _, e in releases)
    ended_t = [t for t, _ in ended]

    def previous_end(begin):
        i = bisect.bisect_left(ended_t, begin["t_us"])
        return ended[i - 1][1] if i else None

    turnovers, empty = [], 0
    for lo, hi in zip(boundaries, boundaries[1:]):
        if hi == lo:
            empty += 1
            continue
        mine = [(b, e) for b, e in releases if lo <= b["op"] < hi]
        s_all = sum(e["s"] for _, e in mine)
        merges = [e for _, e in mine if not e["trivial"]]
        moved = s_all - sum(e["s"] for e in merges)
        starts = [b["op"] for b, _ in mine]
        turnover = {
            "fill_sampled": [f for f in (fill(lo + (hi - lo) * j / k)
                                         for j in range(k)) if f is not None],
            "fill_at_release": [fills[b["job"]][level] for b, _ in mine
                                if b["job"] in fills],
            "released": [s_all / target],
            "inflow_ratio": [mean_ops / (hi - lo)],
            "omega": [],
            "jobs": [len(mine)],
            "job_gap": [(b - a) / mean_ops for a, b in zip(starts, starts[1:])],
        }
        if s_all:
            turnover["passthrough_overlap"] = [sum(e["x"] for e in merges) / s_all]
            turnover["rho_tilde"] = [(moved + sum(e["x"] - e["o"] for e in merges))
                                     / s_all]
        for begin, _ in mine:
            if begin["due_since_us"]:
                ready = op_at(clock, begin["due_since_us"])
                before = previous_end(begin)
                if before is not None:
                    ready = max(ready, before)
                turnover["omega"].append((begin["op"] - ready) * k / mean_ops)
        turnovers.append(turnover)
    return turnovers, empty


def run_geometry(run: Path) -> dict:
    values = dict(line.split("=", 1) for line in
                  (run / "metadata.env").read_text().splitlines() if "=" in line)
    sst = re.search(r":sst(\d+):", values["experiment_fingerprint"])
    return {"base": float(values["max_bytes_for_level_base"]),
            "ratio": float(values["size_ratio"]),
            "sst": float(sst.group(1)) if sst else math.nan,
            "fingerprint": values["experiment_fingerprint"]}


def read_run(run: Path, levels: list[int], k: int):
    """One run's geometry and, per level, (turnovers, empty turnovers)."""
    records = host_log.load(run / host_log.FILE_NAME)
    problems = host_log.check(records)
    if problems:
        raise ValueError(f"{run}: host log: {'; '.join(problems)}")
    events = []
    with (run / "rocksdb_LOG.txt").open(errors="replace") as handle:
        for line in handle:
            if "compaction_release" in line and (m := EVENT.search(line)):
                events.append(json.loads(m.group(1)))
    geometry = run_geometry(run)
    fills = release_fills(events)
    return geometry, {level: level_turnovers(
        records, fills, level,
        geometry["base"] * geometry["ratio"] ** (level - 1), k)
        for level in levels}


REQUIRED = ("reference_level", "margins", "omega_max", "block_length", "k",
            "n_min", "replicates", "seed")


def validate_config(config: dict) -> None:
    """The preregistered values: every compared statistic has a positive
    margin (the test requires every one to pass), and the counts are
    positive integers (a zero block length would never end a resample)."""
    missing = [key for key in REQUIRED if key not in config]
    if missing:
        raise ValueError(f"admission config lacks {missing}")
    if set(config["margins"]) != set(STATISTICS):
        raise ValueError(f"margins must name exactly {list(STATISTICS)}")
    for key in ("k", "block_length", "replicates", "n_min", "reference_level"):
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} must be a positive integer, got {value!r}")
    for name, value in (("omega_max", config["omega_max"]),
                        *config["margins"].items()):
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value) or value <= 0):
            raise ValueError(f"{name} must be positive, got {value!r}")


# --- membership ----------------------------------------------------------------

def membership(per_level: dict[int, list[list[dict]]], config: dict,
               geometry: dict) -> dict:
    """Pool membership for one (workload, T) cell."""
    rng = random.Random(config["seed"])
    reference = config["reference_level"]
    ref_runs = per_level[reference]
    levels = {}
    for level, runs in sorted(per_level.items()):
        count = sum(len(r) for r in runs)
        entry = {"turnovers": count, "c3_diagnostics": {
            stat: (statistics.fmean(values) if (values := [
                v for r in runs for t in r for v in t.get(stat, ())]) else None)
            for stat in ("jobs", "job_gap")}}
        target = geometry["base"] * geometry["ratio"] ** (min(level, reference) - 1)
        floor = geometry["sst"] / target
        small = [s for s in FILL_STATISTICS
                 if config["margins"].get(s, math.inf) < floor]
        if small:
            raise ValueError(f"fill margins {small} below the file granularity "
                             f"{floor:.4f} of level {min(level, reference)}")
        omega = omega_upper_bound(runs, config, rng)
        entry["omega_upper_95"] = omega
        entry["omega_passed"] = omega is not None and omega < config["omega_max"]
        if level != reference:
            if count < config["n_min"] or sum(map(len, ref_runs)) < config["n_min"]:
                entry.update(decision="undecided",
                             reason=f"fewer than n_min={config['n_min']} turnovers")
                levels[level] = entry
                continue
            entry["comparison"] = compare(runs, ref_runs, config, rng)
            entry["ks_upper_95"] = {
                stat: ks_upper_bound(
                    [v for r in runs for t in r for v in t.get(stat, ())],
                    [v for r in ref_runs for t in r for v in t.get(stat, ())],
                    config["replicates"], rng)
                for stat in config["margins"]
                if any(t.get(stat) for r in runs for t in r)
                and any(t.get(stat) for r in ref_runs for t in r)}
            entry["decision"] = ("admitted" if entry["comparison"]["passed"]
                                 and entry["omega_passed"] else "refused")
        else:
            entry["decision"] = "reference" if entry["omega_passed"] else "refused"
        levels[level] = entry
    pool = [l for l, e in levels.items() if e["decision"] in ("reference", "admitted")]
    return {"levels": levels, "pool": pool if len(pool) >= 2 else [],
            "pool_note": None if len(pool) >= 2 else
            "a pool needs at least two admitted levels (G §4 scope decision)"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", type=Path, nargs="+",
                        help="static arm result directories of one (workload, T)")
    parser.add_argument("--config", type=Path, required=True,
                        help="the preregistered admission values")
    parser.add_argument("--levels", type=int, nargs="+", required=True,
                        help="candidate interior levels, reference included")
    parser.add_argument("--simulate-n-min", type=int, nargs="*",
                        help="run the n_min simulation at these turnover counts")
    parser.add_argument("--trials", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    try:
        validate_config(config)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    if config["reference_level"] not in args.levels:
        raise SystemExit("--levels must include the reference level")
    per_level = {level: [] for level in args.levels}
    empty = {level: 0 for level in args.levels}
    fingerprints, geometry = set(), None
    for run in args.runs:
        try:
            geometry, turnovers = read_run(run, args.levels, config["k"])
        except ValueError as error:
            raise SystemExit(str(error)) from error
        fingerprints.add(geometry["fingerprint"])
        for level, (t, dropped) in turnovers.items():
            per_level[level].append(t)
            empty[level] += dropped
    # G §4: one static configuration, m = 1, per (workload, T, K0); seeds
    # are not in the fingerprint, so its runs share one.
    if len(fingerprints) != 1:
        raise SystemExit(f"runs of several configurations: {sorted(fingerprints)}")
    fingerprint = fingerprints.pop()
    if ":ltm" in fingerprint:
        raise SystemExit("the admission test reads m = 1 runs only")
    report = {"schema_version": 1, "experiment_fingerprint": fingerprint,
              "config": config, "empty_turnovers": empty,
              **membership(per_level, config, geometry)}
    if args.simulate_n_min:
        rng = random.Random(config["seed"])
        report["n_min_simulation"] = [
            pseudo_level_rates(per_level[config["reference_level"]], n, config,
                               args.trials, rng)
            for n in args.simulate_n_min]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"pool: {report['pool']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
