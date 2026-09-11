"""Static W-R-S frontier measurements, with paired uncertainty and provenance.

The base-option sweep is a calibration proxy: it changes L0 as well as deep
targets. It cannot certify an independent capacity actuator's safety bound.
No interpolation, extrapolation, or space-to-survival inference is performed.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import importlib.util
import json
import math
from pathlib import Path
import re
import statistics

from pipeline_stats import ci95, critical_value, objective_verdict
from research_objective import load_contract, relative_difference

AXES = ("write_amplification", "point_read_amplification")


def dominates(left: dict, right: dict) -> bool:
    return (all(left[key] <= right[key] for key in AXES) and
            any(left[key] < right[key] for key in AXES))


def paired_comparison(left: dict[int, dict], right: dict[int, dict]) -> dict:
    seeds = sorted(left.keys() & right.keys())
    if len(seeds) < 2:
        return {"verdict": "undecidable", "paired_seeds": seeds}
    intervals = {axis: ci95([relative_difference(left[s][axis], right[s][axis])
                           for s in seeds]) for axis in AXES}
    if (all(intervals[a]["upper"] <= 0 for a in AXES) and
            any(intervals[a]["upper"] < 0 for a in AXES)):
        verdict = "dominates"
    elif any(intervals[a]["lower"] > 0 for a in AXES):
        verdict = "does_not_dominate"
    else:
        verdict = "undecidable"
    return {"verdict": verdict, "paired_seeds": seeds, "intervals": intervals}


def analyze_points(configs: dict[str, dict[int, dict]]) -> dict:
    points = {}
    for name, samples in configs.items():
        for row in samples.values():
            if any(not math.isfinite(row[k]) or row[k] <= 0
                   for k in (*AXES, "space_amplification")):
                raise ValueError(f"{name}: missing/invalid W-R-S metric")
        points[name] = {"means": {key: statistics.fmean(r[key] for r in samples.values())
                                  for key in (*AXES, "space_amplification")},
                        "intervals": {key: ci95([r[key] for r in samples.values()])
                                      for key in (*AXES, "space_amplification")},
                        "seeds": sorted(samples),
                        "runs": [r["result_directory"] for r in samples.values()]}
    hull = [name for name, p in points.items() if not any(
        dominates(q["means"], p["means"]) for other, q in points.items() if other != name)]
    comparisons = {name: {other: paired_comparison(configs[other], configs[name])
                         for other in configs if other != name}
                   for name in configs}
    top_up = {}
    for name in hull:
        point = points[name]
        needed = max(5, len(point["seeds"]))
        decidable = True
        for axis in AXES:
            spacings = [abs(point["means"][axis] - points[other]["means"][axis])
                        for other in hull if other != name]
            spacing = min(spacings, default=math.inf)
            values = [r[axis] for r in configs[name].values()]
            if len(values) < 2 or spacing == 0:
                decidable = False
                needed = None
                break
            deviation = statistics.stdev(values)
            # C-2 specifies full CI width, not its half width.
            count = next((n for n in range(max(5, len(values)), 201)
                          if 2 * critical_value(n) * deviation / math.sqrt(n)
                          < spacing / 2), None)
            if count is None:
                decidable = False
                needed = None
                break
            needed = max(needed, count)
            decidable &= count <= len(values)
        top_up[name] = {"suggested_total_repeats": needed,
                        "width_criterion_passed": decidable,
                        "reason": "C-2 full CI width < half inter-point spacing"}
    return {"points": points, "empirical_hull": hull,
            "paired_dominance": comparisons, "repeat_top_up": top_up,
            "c1_sampled": len(points) >= 12 and len(hull) >= 4,
            "c2_widths_passed": all(v["width_criterion_passed"] for v in top_up.values())}


def policy_positions(policy: dict[str, dict[int, dict]], configs: dict,
                     points: dict, hull: list[str]) -> dict:
    """C-3/C-4: classify each policy arm against the static hull."""
    positions = {}
    for name, samples in policy.items():
        means = {key: statistics.fmean(r[key] for r in samples.values())
                 for key in (*AXES, "space_amplification")}
        dominators = [h for h in hull if dominates(points[h]["means"], means)]
        positions[name] = {
            "means": means, "seeds": sorted(samples),
            "dominated_by": dominators,
            "verdict": "dominated" if dominators else "non_dominated",
            "against_hull_points": {h: paired_comparison(configs[h], samples)
                                    for h in hull}}
    return positions


def common_fingerprint(fingerprint: str) -> str:
    # These are the ONLY knobs deliberately varied by the Hull-0 sweep.
    return re.sub(r":(?:T\d+|l1\d+|l0-\d+-\d+-\d+|pri\d+)(?=:|$)",
                  "", fingerprint)


def collect_grid(root: Path, size: int, ratios: list[int],
                 arm: str = "regular") -> tuple[dict, dict]:
    spec = importlib.util.spec_from_file_location(
        "frontier_graphs", Path(__file__).with_name("04_generate_graphs.py"))
    graph = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(graph)
    _, objective_hash = load_contract()
    configs, measurements, identities = defaultdict(dict), {}, set()
    for marker in sorted(root.glob("**/COMPLETED")):
        directory = marker.parent
        row = graph.collect_arm(directory)
        if (row is None or row["size_millions"] != size or
                row["size_ratio"] not in ratios or row["arm"] != arm):
            continue
        metadata = graph.read_env(directory / "metadata.env")
        if (metadata.get("research_objective_sha256") != objective_hash or
                metadata.get("level_compaction_dynamic_level_bytes") != "false" or
                not metadata.get("dbbench_sha256")):
            raise ValueError(f"{directory}: missing P0/binary/static-ladder provenance")
        fingerprint = row["experiment_fingerprint"]
        if not fingerprint:
            raise ValueError(f"{directory}: missing configuration fingerprint")
        identities.add((common_fingerprint(fingerprint), metadata["dbbench_sha256"]))
        seed = int(row["dbbench_seed"])
        if seed in configs[fingerprint]:
            raise ValueError(f"duplicate seed in static configuration: {directory}")
        measurement = json.loads((directory / "compaction_measurements.json").read_text())
        if measurement.get("complete") is not True or not measurement.get("releases"):
            raise ValueError(f"{directory}: incomplete Gate-0 instrument")
        levels = measurement["views"]["whole_run"]["levels"]
        if len(levels) != int(metadata["num_levels"]):
            raise ValueError(f"{directory}: missing per-level survival")
        configs[fingerprint][seed] = row
        measurements[str(directory)] = {"metadata": metadata, "measurement": measurement}
    if not configs or len(identities) != 1:
        raise ValueError("empty sweep or incompatible workload/binary fingerprints")
    return dict(configs), measurements


def space_curves(configs: dict, measurements: dict, margins: list[float]) -> list:
    groups = defaultdict(dict)
    for config, samples in configs.items():
        metadata = measurements[next(iter(samples.values()))["result_directory"]]["metadata"]
        group = (metadata["size_ratio"],) + tuple(
            metadata[k] for k in ("level0_file_num_compaction_trigger",
                                  "level0_slowdown_writes_trigger",
                                  "level0_stop_writes_trigger",
                                  "compaction_priority"))
        scale = float(metadata["baseline_level_base_scale"])
        if scale in groups[group]:
            raise ValueError("duplicate base scale in curve")
        groups[group][scale] = config
    curves = []
    for group, scales in groups.items():
        if not {0.5, 1., 2.}.issubset(scales):
            raise ValueError("incomplete 0.5/1/2 base-scale curve")
        baseline = configs[scales[1.]]
        points = []
        for scale, name in sorted(scales.items()):
            sample = configs[name]
            seeds = sorted(sample.keys() & baseline.keys())
            if len(seeds) < 3:
                raise ValueError("space calibration requires three paired repeats")
            differences = [relative_difference(sample[s]["space_amplification"],
                                               baseline[s]["space_amplification"])
                           for s in seeds]
            points.append({"scale": scale, "configuration": name,
                           "paired_seeds": seeds, "space_relative_ci95": ci95(differences),
                           "budget_checks": {str(m): objective_verdict(differences, m, 3)
                                             for m in margins}})
        curves.append({"size_ratio": int(group[0]),
                       "l0_priority_options": group[1:], "points": points,
                       "measured_feasible_base_scales": {
                           str(m): [p["scale"] for p in points if p["scale"] >= 1 and
                                    p["budget_checks"][str(m)]["passed"] is True]
                           for m in margins},
                       "capacity_s_max": None,
                       "capacity_bound_status": "requires_matched_deep_capacity_calibration",
                       "reason": "base-option scale also changes L0; no transfer bound proved"})
    return curves


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--size-millions", type=int, default=10)
    parser.add_argument("--size-ratio", type=int, nargs="+", required=True,
                        help="one ratio for a per-T hull; several for the "
                             "cross-T pooled hull required by C-6")
    parser.add_argument("--policy-results", type=Path,
                        help="results root holding a policy arm to place "
                             "against the hull (C-3)")
    parser.add_argument("--policy-arm", default="prior_only")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    configs, measurements = collect_grid(
        args.results, args.size_millions, args.size_ratio)
    contract, fingerprint = load_contract()
    analysis = analyze_points(configs)
    policy = {}
    if args.policy_results:
        policy_configs, _ = collect_grid(
            args.policy_results, args.size_millions, args.size_ratio,
            arm=args.policy_arm)
        policy = policy_positions(policy_configs, configs, analysis["points"],
                                  analysis["empirical_hull"])
    report = {"schema_version": 1, "research_objective_sha256": fingerprint,
              "size_millions": args.size_millions,
              "size_ratios": args.size_ratio,
              "cross_t": len(args.size_ratio) > 1,
              "policy_arm": args.policy_arm if policy else None,
              "policy_positions": policy,
              **analysis,
              "space_curves": space_curves(configs, measurements,
                  contract["constraints"]["space"]["relative_margin_axis"]),
              "per_run_measurements": measurements,
              "formal_gate1_passed": False,
              "remaining_gates": ["fresh_prior_only_comparison", "matched_capacity_bound"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
