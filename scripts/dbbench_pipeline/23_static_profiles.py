#!/usr/bin/env python3
"""Theta_s's two measured multiplier profiles (PATHWAYS C §1, Theorem A.2;
PREREGISTRATION D-13 §4 as fixed by D-14 §3), from the native arms of one
(workload, T, K0, base size) point, pooled over their repeats.

  last_level_emptying  m_{L-1} = 2, every other level 1.
  survival_weighted    Theorem A.2(ii): fanouts f_i = lambda / v_i for
                       i = 0..L-1, v_i the merged bytes leaving level i per
                       user byte over the measured phase (a_i - t_i),
                       lambda = (B_L / (K0 F) * prod v_i)^(1/L); then
                       m_1 = f_0 K0 F / C_1, m_{i+1} = f_i m_i / T, and the
                       levels from L down keep 1.

L (deepest populated level) and B_L (its bytes) are the settled tree's at
n_w: the first compaction_release snapshot after the measure_start stamp,
before which only flushes (into L0) change the tree. v_i and F (the mean
flush file size) are measured over mixgraph, measure_start to drain_start:
steady-state flows, without the drain. A common overlap constant cancels from the
optimum; the measured c_i = o_i / f_i are reported. Entries outside
[0.5, 2.0] are clipped and reported; a profile that would then shrink a
level's target below the level's above is refused.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from pathlib import Path

import compaction_measurements
import host_log

EVENT = re.compile(r"EVENT_LOG_v1 (\{.*\})")
# A-Impl-7's bounds, as ColumnFamilyData::ValidateOptions enforces them.
M_MIN, M_MAX = 0.5, 2.0


def read_env(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if "=" in line)


def measure_run(run: Path) -> dict:
    """One native run's inputs to the profiles."""
    meta = read_env(run / "metadata.env")
    records = host_log.load(run / host_log.FILE_NAME)
    problems = host_log.check(records)
    if problems:
        raise ValueError(f"{run}: host log: {'; '.join(problems)}")
    # Steady-state flows: mixgraph only (measure_start to drain_start). The
    # drain is a transient, and its last flush is partial.
    at = host_log.stamp_index(records)
    start_us = records[at["measure_start"]]["wall_us"]
    end_us = records[at["drain_start"]]["wall_us"]
    num_levels = int(meta["num_levels"])
    snapshot, flush_jobs, flush_sizes = None, set(), []
    with (run / "rocksdb_LOG.txt").open(errors="replace") as handle:
        for line in handle:
            if "EVENT_LOG_v1" not in line or not (m := EVENT.search(line)):
                continue
            event = json.loads(m.group(1))
            kind, when = event.get("event"), event.get("time_micros", -1)
            if kind == "flush_started":
                flush_jobs.add(event["job"])
            elif (kind == "table_file_creation" and event.get("job") in flush_jobs
                    and start_us <= when <= end_us):
                flush_sizes.append(event["file_size"])
            elif kind == "compaction_release" and when >= start_us and snapshot is None:
                snapshot = event["occupancy_bytes"]
    if snapshot is None or not flush_sizes:
        raise ValueError(f"{run}: no compaction or no flush in the measured phase")
    deepest = max((i for i, b in enumerate(snapshot) if b > 0), default=0)
    # compaction_measurements' measured view, ending at drain_start instead.
    cut = at["drain_start"]
    view = compaction_measurements.analyze(
        records[:cut] + [{**records[cut], "name": "drain_end"}],
        num_levels)["views"]["measured"]
    multipliers = meta.get("level_target_multipliers", "none")
    return {
        "fingerprint": meta["experiment_fingerprint"],
        "num_levels": num_levels, "T": float(meta["size_ratio"]),
        "K0": int(meta["level0_file_num_compaction_trigger"]),
        "C1": float(meta["max_bytes_for_level_base"]),
        "m": ([1.0] * num_levels if multipliers == "none"
              else [float(x) for x in multipliers.split(":")]),
        "L": deepest, "B_L": float(snapshot[deepest]),
        "F": statistics.fmean(flush_sizes),
        "user_bytes": float(view["user_bytes"] or 0),
        "s": [float(view["levels"][str(i)]["s_bytes"]) for i in range(num_levels)],
        "o": [float(view["levels"][str(i)]["o_bytes"]) for i in range(num_levels)],
    }


def pool(runs: list[dict]) -> dict:
    """Repeats of one configuration: byte totals summed, B_L and F averaged."""
    for key in ("fingerprint", "L"):
        if len({run[key] for run in runs}) != 1:
            raise ValueError(f"the runs differ in {key}: "
                             f"{sorted({str(run[key]) for run in runs})}")
    first = runs[0]
    return {**first,
            "B_L": statistics.fmean(run["B_L"] for run in runs),
            "F": statistics.fmean(run["F"] for run in runs),
            "user_bytes": sum(run["user_bytes"] for run in runs),
            "s": [sum(run["s"][i] for run in runs) for i in range(first["num_levels"])],
            "o": [sum(run["o"][i] for run in runs) for i in range(first["num_levels"])],
            "runs": len(runs)}


def fanouts(p: dict, m: list[float]) -> list[float]:
    """f_0..f_{L-1} of PATHWAYS §1.1 under multipliers m."""
    L, T, C1 = p["L"], p["T"], p["C1"]
    f = [m[1] * C1 / (p["K0"] * p["F"])]
    f += [T * m[i + 1] / m[i] for i in range(1, L - 1)]
    f.append(p["B_L"] / (m[L - 1] * C1 * T ** (L - 2)))
    return f


def check_ladder(m: list[float], T: float) -> None:
    for i in range(1, len(m) - 1):
        if m[i + 1] * T < m[i]:
            raise ValueError(f"level {i + 1}'s target would be smaller than "
                             f"level {i}'s ({m[i + 1]:g} * {T:g} < {m[i]:g})")


def vector(m: list[float]) -> str:
    return ":".join(f"{round(x, 4):g}" for x in m)


def survival_weighted(p: dict) -> dict:
    L, T, n = p["L"], p["T"], p["num_levels"]
    if L < 2:
        raise ValueError(f"L = {L}: no interior level to weight")
    if p["user_bytes"] <= 0:
        raise ValueError("no user bytes in the measured phase")
    v = [p["s"][i] / p["user_bytes"] for i in range(L)]
    empty = [i for i, x in enumerate(v) if x <= 0]
    if empty:
        raise ValueError(f"no merged bytes leave levels {empty}: "
                         "Theorem A.2(ii) has no finite optimum")
    lam = (p["B_L"] / (p["K0"] * p["F"]) * math.prod(v)) ** (1 / L)
    f = [lam / x for x in v]
    m = [1.0] * n
    m[1] = f[0] * p["K0"] * p["F"] / p["C1"]
    for i in range(1, L - 1):
        m[i + 1] = f[i] * m[i] / T
    clipped = {i: m[i] for i in range(1, n) if not M_MIN <= m[i] <= M_MAX}
    m = [1.0] + [min(max(x, M_MIN), M_MAX) for x in m[1:]]
    check_ladder(m, T)
    source = fanouts(p, p["m"])
    return {"vector": vector(m), "v": v, "lambda": lam, "fanouts": f,
            "clipped": {str(i): x for i, x in clipped.items()},
            "overlap_constants": [
                (p["o"][i] / p["s"][i]) / source[i] if p["s"][i] else None
                for i in range(L)]}


def last_level_emptying(p: dict) -> dict:
    L = p["L"]
    if L < 2:
        raise ValueError(f"L = {L}: no level above the last to hold")
    m = [1.0] * p["num_levels"]
    m[L - 1] = 2.0
    check_ladder(m, p["T"])
    return {"vector": vector(m), "held_level": L - 1}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", type=Path, nargs="+",
                        help="native arm result directories of one point")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        pooled = pool([measure_run(run) for run in args.runs])
    except ValueError as error:
        raise SystemExit(str(error)) from error
    # Each profile on its own: a refused one is recorded with its reason
    # (D-14 §3), and the other still computed.
    profiles = {}
    for name, compute in (("survival_weighted", survival_weighted),
                          ("last_level_emptying", last_level_emptying)):
        try:
            profiles[name] = compute(pooled)
        except ValueError as error:
            profiles[name] = {"refused": str(error)}
    # Merged bytes out of level L or below: the tree deepened during the
    # measured phase, and the settled L no longer describes it.
    deeper = [i for i in range(pooled["L"], pooled["num_levels"]) if pooled["s"][i]]
    report = {"schema_version": 1, "runs": [str(run) for run in args.runs],
              "inputs": {k: pooled[k] for k in (
                  "fingerprint", "L", "B_L", "F", "K0", "C1", "T",
                  "user_bytes", "s", "o")},
              "merges_below_L": deeper, **profiles}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    for name, profile in profiles.items():
        if "vector" in profile:
            print(f"STATIC_PROFILE_{name}={profile['vector']}")
        else:
            print(f"{name} refused: {profile['refused']}", file=sys.stderr)
    if deeper:
        print(f"warning: merges out of levels {deeper}, at or below L = "
              f"{pooled['L']}: the tree deepened", file=sys.stderr)
    return 0 if all("vector" in p for p in profiles.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
