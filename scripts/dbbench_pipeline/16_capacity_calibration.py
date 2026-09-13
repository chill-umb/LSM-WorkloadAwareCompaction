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


EXTRA = ("populated_levels", "sst_bytes_before", "live_logical_bytes",
         "sst_bytes_after")


def calibrate(groups: dict, margins: list[float], minimum_pairs: int) -> dict:
    """groups: {(ratio, scale): {seed: {"space": float, **EXTRA}}}."""
    report, s_max, s_max_measured = {}, defaultdict(dict), defaultdict(dict)
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
            differences = [relative_difference(scales[scale][s]["space"],
                                              baseline[s]["space"])
                           for s in seeds]
            # Same ratio with a measured denominator instead of RocksDB's
            # estimate-live-data-size. The estimate is shape sensitive, and
            # shape is precisely what the capacity actuator changes.
            measured = [relative_difference(scales[scale][s]["space_measured"],
                                            baseline[s]["space_measured"])
                        for s in seeds]
            checks = {str(m): objective_verdict(differences, m, minimum_pairs)
                      for m in margins}
            checks_measured = {str(m): objective_verdict(measured, m,
                                                         minimum_pairs)
                               for m in margins}
            points.append({"scale": scale, "paired_seeds": seeds,
                           "mean_space_amplification":
                               statistics.fmean(v["space"]
                                                for v in scales[scale].values()),
                           # Shown because a negative Delta S is usually the
                           # tree collapsing into fewer, better-merged levels
                           # rather than expansion being free.
                           **{f"mean_{key}": statistics.fmean(
                                  v[key] for v in scales[scale].values())
                              for key in EXTRA},
                           "space_relative_ci95": ci95(differences) if differences
                               else None,
                           "space_measured_relative_ci95": ci95(measured)
                               if measured else None,
                           "budget_checks": checks,
                           "budget_checks_measured": checks_measured})
        # s_i is bounded to [1, s_max], so an actuator may sit anywhere in that
        # interval: every measured scale up to s_max must be inside the budget,
        # not merely the largest one that happens to pass.
        for margin in margins:
            for field, table in (("budget_checks", s_max),
                                 ("budget_checks_measured", s_max_measured)):
                bound = None
                for point in points:
                    if point[field][str(margin)]["passed"] is not True:
                        break
                    bound = point["scale"]
                table[str(ratio)][str(margin)] = bound
        # A non-monotone curve makes "the largest affordable expansion"
        # meaningless, so it is reported rather than silently summarised.
        means = [p["space_relative_ci95"]["mean"] for p in points]
        measured_means = [p["space_measured_relative_ci95"]["mean"]
                          for p in points]
        def directional(series: list[float]) -> bool:
            rising = all(b >= a - 1e-12 for a, b in zip(series, series[1:]))
            falling = all(b <= a + 1e-12 for a, b in zip(series, series[1:]))
            return rising or falling

        monotone = directional(means)
        measured_monotone = directional(measured_means)
        report[str(ratio)] = {"points": points, "monotone": monotone,
                              "mean_delta_s": means,
                              "measured_monotone": measured_monotone,
                              "mean_delta_s_measured": measured_means}
    return {"curves": report,
            "s_max_estimated_denominator": {k: dict(v) for k, v in s_max.items()},
            "s_max": {k: dict(v) for k, v in s_max_measured.items()}}


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
        depths = [r["populated_levels"] for r in releases]
        after = float(row["sst_bytes_after"])
        if not after > 0:
            raise ValueError(f"{directory}: no garbage-free reference size")
        metrics = {
            "space": float(row["space_amplification"]),
            "space_measured": float(row["sst_bytes_before"]) / after,
            "populated_levels": float(max(depths)),
            "sst_bytes_before": float(row["sst_bytes_before"]),
            "live_logical_bytes": float(row["live_logical_bytes"]),
            "sst_bytes_after": after}
        # Fail here, naming the field, rather than inside fmean later.
        absent = [key for key in EXTRA if key not in metrics]
        if absent:
            raise ValueError(f"collected metrics are missing {absent}")
        groups[key][seed] = metrics
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

    for ratio, curve in sorted(result["curves"].items()):
        for point in curve["points"]:
            interval = point["space_relative_ci95"]
            band = ("baseline" if point["scale"] == 1.0 else
                    f"{interval['mean']*100:+.2f}% "
                    f"[{interval['lower']*100:+.2f}, {interval['upper']*100:+.2f}]")
            m = point["space_measured_relative_ci95"]
            band2 = ("baseline" if point["scale"] == 1.0 else
                     f"{m['mean']*100:+.2f}% "
                     f"[{m['lower']*100:+.2f}, {m['upper']*100:+.2f}]")
            print(f"T={ratio:>2} s={point['scale']:<4} "
                  f"estimated {band:<26} measured {band2:<26} "
                  f"depth {point['mean_populated_levels']:.1f}  "
                  f"compacted {point['mean_sst_bytes_after']/1e9:.2f} GB")
        print(f"T={ratio:>2} s_max (measured denominator): {result['s_max'][ratio]}"
              f"\n         s_max (estimate-live-data-size): "
              f"{result['s_max_estimated_denominator'][ratio]}"
              f"{'' if curve['monotone'] else '   NON-MONOTONE (estimated)'}"
              f"{'' if curve['measured_monotone'] else '   NON-MONOTONE (measured)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
