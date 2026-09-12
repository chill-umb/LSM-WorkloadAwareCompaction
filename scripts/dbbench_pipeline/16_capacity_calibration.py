#!/usr/bin/env python3
"""Measure Delta S(s) with the per-level capacity actuator and derive s_max.

Gate 1's base-option sweep varies max_bytes_for_level_base, which moves L0's
byte trigger along with the deep-level targets, so it cannot bound the actuator
A-Impl-7 limits. These arms drive the actuator itself: levels 1..L-1 scaled,
L0 and the output-only final level pinned to 1.0.

Every arm's applied scale vector is read back from its Gate-0 release events and
compared with the vector its metadata requested. A request that never reached
MaxBytesForLevel produces a flat curve and an s_max that is wrong in the unsafe
direction, so a mismatch is a hard error rather than a warning.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import importlib.util
import json
from pathlib import Path
import statistics

from pipeline_stats import ci95, objective_verdict
from research_objective import load_contract, relative_difference


def uniform_scale(vector: list[float]) -> float:
    """The single interior scale of a uniform expansion vector."""
    if len(vector) < 3:
        raise ValueError("a capacity vector needs an interior")
    if vector[0] != 1.0 or vector[-1] != 1.0:
        raise ValueError("L0 and the final level must stay at 1.0")
    interior = set(vector[1:-1])
    if len(interior) != 1:
        raise ValueError(f"expected a uniform interior scale, got {sorted(interior)}")
    return interior.pop()


def requested_vector(metadata: dict, levels: int) -> list[float]:
    raw = metadata.get("static_capacity_scales", "none")
    if raw in ("", "none"):
        return [1.0] * levels
    return [float(part) for part in raw.split(",")]


def calibrate(groups: dict, margins: list[float], minimum_pairs: int) -> dict:
    """groups: {(ratio, scale): {seed: space_amplification}}."""
    report, s_max = {}, defaultdict(dict)
    ratios = sorted({ratio for ratio, _ in groups})
    for ratio in ratios:
        scales = {scale: samples for (r, scale), samples in groups.items() if r == ratio}
        if 1.0 not in scales:
            raise ValueError(f"T={ratio} has no s=1.0 control to pair against")
        baseline = scales[1.0]
        points = []
        for scale in sorted(scales):
            seeds = sorted(scales[scale].keys() & baseline.keys())
            if scale != 1.0 and len(seeds) < 2:
                raise ValueError(f"T={ratio} s={scale}: fewer than two paired seeds")
            differences = [relative_difference(scales[scale][s], baseline[s])
                           for s in seeds]
            checks = {str(m): objective_verdict(differences, m, minimum_pairs)
                      for m in margins}
            points.append({"scale": scale, "paired_seeds": seeds,
                           "mean_space_amplification":
                               statistics.fmean(scales[scale].values()),
                           "space_relative_ci95": ci95(differences) if differences
                               else None,
                           "budget_checks": checks})
        for margin in margins:
            feasible = [p["scale"] for p in points
                        if p["budget_checks"][str(margin)]["passed"] is True]
            s_max[str(ratio)][str(margin)] = max(feasible) if feasible else None
        report[str(ratio)] = points
    return {"curves": report, "s_max": {k: dict(v) for k, v in s_max.items()}}


def collect(root: Path, size: int) -> dict:
    spec = importlib.util.spec_from_file_location(
        "capacity_graphs", Path(__file__).with_name("04_generate_graphs.py"))
    graph = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(graph)
    groups, applied_log = defaultdict(dict), {}
    for marker in sorted(root.glob("**/COMPLETED")):
        directory = marker.parent
        row = graph.collect_arm(directory)
        if row is None or row["size_millions"] != size or row["arm"] != "regular":
            continue
        metadata = graph.read_env(directory / "metadata.env")
        levels = int(metadata["num_levels"])
        requested = requested_vector(metadata, levels)

        measurement = json.loads(
            (directory / "compaction_measurements.json").read_text())
        releases = measurement.get("releases") or []
        if not releases:
            raise ValueError(f"{directory}: no release events to verify against")
        seen = {tuple(r["capacity_scales"]) for r in releases}
        if len(seen) != 1:
            raise ValueError(f"{directory}: capacity vector changed mid-run: {seen}")
        applied = list(seen.pop())
        if [float(v) for v in applied] != requested:
            raise ValueError(
                f"{directory}: requested {requested} but RocksDB applied "
                f"{applied}. The expansion never reached MaxBytesForLevel; the "
                f"curve would be flat and s_max wrong in the unsafe direction.")

        scale = uniform_scale(requested)
        key = (int(row["size_ratio"]), scale)
        seed = int(row["dbbench_seed"])
        if seed in groups[key]:
            raise ValueError(f"duplicate seed {seed} for T={key[0]} s={scale}")
        groups[key][seed] = float(row["space_amplification"])
        applied_log[str(directory)] = {"scale": scale, "applied": applied}
    if not groups:
        raise ValueError(f"no completed regular arms below {root}")
    return dict(groups), applied_log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results", type=Path)
    parser.add_argument("--size-millions", type=int, default=10)
    parser.add_argument("--minimum-pairs", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    contract, objective = load_contract()
    margins = contract["constraints"]["space"]["relative_margin_axis"]
    groups, applied = collect(args.results, args.size_millions)
    result = calibrate(groups, margins, args.minimum_pairs)
    report = {"schema_version": 1, "research_objective_sha256": objective,
              "size_millions": args.size_millions,
              "actuator": "per-level capacity scales, levels 1..L-1",
              "verified_applied_vectors": applied, **result}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")

    for ratio, points in sorted(result["curves"].items()):
        for point in points:
            interval = point["space_relative_ci95"]
            band = ("baseline" if point["scale"] == 1.0 else
                    f"{interval['mean']*100:+.2f}% "
                    f"[{interval['lower']*100:+.2f}, {interval['upper']*100:+.2f}]")
            print(f"T={ratio:>2} s={point['scale']:<4} space {band}")
        print(f"T={ratio:>2} s_max per rung: {result['s_max'][ratio]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
