#!/usr/bin/env python3
"""The static class's priced-cost frontier (PATHWAYS C.4, D.4, D.5).

From 04's summary.csv, the static arms (native and static:<profile>) of one
workload and size ratio (several ratios for the cross-T hull of C-6): each
configuration's mean priced costs over its seeds, (C_W, C_R, C_S);
  - the lower convex hull: the points that minimise J_beta for some beta > 0
    (Proposition D.5), split into vertices and points that are supported
    only on a face (a collinear tie);
  - theta*_beta, the configuration minimising J_beta, per mode, beta* and c_s
    scale (Proposition C.4 says it is on the hull; checked);
  - beta-bar per prioritised term (Proposition D.4): above it, J_beta picks
    the configuration with the least prioritised cost.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

import research_objective

COSTS = ("C_W", "C_R", "C_S")
PRIORITISED = {"write": 0, "read": 1, "space": 2}
# Rows of one hull must share these (binary, prices, q-bar, contract, h_w).
IDENTITY = ("workload_profile", "size_millions", "dbbench_sha256",
            "prices_sha256", "reference_rate", "research_objective_sha256",
            "settle_hold_seconds")
# A face region of the simplex of beta's thinner than this is a tie, not a
# vertex. The simplex has area 1/2, and costs are rescaled per axis first.
AREA_TOLERANCE = 1e-9


def is_static(arm: str) -> bool:
    return arm == "native" or arm.startswith("static:")


def static_configurations(rows: list[dict], workload: str, size_ratios,
                          size_millions: int) -> dict[str, dict[int, dict]]:
    """{fingerprint: {seed: row}} for the priced static arms selected."""
    configs: dict[str, dict[int, dict]] = defaultdict(dict)
    identities = set()
    for row in rows:
        if not (is_static(row["arm"]) and row["workload_profile"] == workload
                and int(row["size_ratio"]) in size_ratios
                and int(row["size_millions"]) == size_millions):
            continue
        if row["objective_status"] != "priced":
            raise ValueError(f"{row['result_directory']}: not priced "
                             f"({row['objective_status']})")
        identities.add(tuple(row[key] for key in IDENTITY))
        seed = int(row["dbbench_seed"])
        if seed in configs[row["experiment_fingerprint"]]:
            raise ValueError(f"{row['result_directory']}: duplicate seed")
        configs[row["experiment_fingerprint"]][seed] = row
    if len(identities) > 1:
        raise ValueError(f"static arms differ in {IDENTITY}: {identities}")
    return dict(configs)


def mean_costs(samples: dict[int, dict]) -> tuple[float, float, float]:
    means = tuple(statistics.fmean(float(r[key]) for r in samples.values())
                  for key in COSTS)
    if not all(math.isfinite(v) and v >= 0 for v in means):
        raise ValueError(f"non-finite or negative priced cost: {means}")
    return means


def _clip(polygon, a, b, c):
    """Sutherland-Hodgman: the part of `polygon` with a*u + b*v + c <= 0."""
    def side(p):
        return a * p[0] + b * p[1] + c

    out = []
    for i, current in enumerate(polygon):
        previous = polygon[i - 1]
        fc, fp = side(current), side(previous)
        if (fc <= 0) != (fp <= 0):
            t = fp / (fp - fc)
            out.append((previous[0] + t * (current[0] - previous[0]),
                        previous[1] + t * (current[1] - previous[1])))
        if fc <= 0:
            out.append(current)
    return out


def _area(polygon) -> float:
    return abs(sum(p[0] * q[1] - q[0] * p[1] for p, q in
                   zip(polygon, polygon[1:] + polygon[:1]))) / 2


def lower_hull(points: dict[str, tuple]) -> dict[str, list[str]]:
    """Which points minimise <beta, x> for some beta > 0 (Proposition D.5).

    Each point's set of such beta, on the simplex beta = (u, v, 1-u-v), is
    the triangle cut by one half-plane per other point. A region with area
    is a hull vertex; a region of area zero that still reaches the open
    simplex (an edge between two vertices) is a supported point that is not
    a vertex, i.e. a collinear or coplanar tie. Axes are rescaled first,
    which maps the hull to itself, so costs in very different units do not
    make the regions numerically thin."""
    scales = [max((abs(x[k]) for x in points.values()), default=1.0) or 1.0
              for k in range(3)]
    scaled = {name: tuple(x[k] / scales[k] for k in range(3))
              for name, x in points.items()}
    vertices, supported = [], []
    for name, x in scaled.items():
        region = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
        for other, y in scaled.items():
            if other == name or not region:
                continue
            d = [x[k] - y[k] for k in range(3)]
            region = _clip(region, d[0] - d[2], d[1] - d[2], d[2])
        if not region:
            continue
        if _area(region) > AREA_TOLERANCE:
            vertices.append(name)
            continue
        u = statistics.fmean(p[0] for p in region)
        v = statistics.fmean(p[1] for p in region)
        if u > 0 and v > 0 and u + v < 1:
            supported.append(name)
    return {"vertices": sorted(vertices), "supported_non_vertices": sorted(supported)}


def comparators(points: dict[str, tuple], contract: dict) -> list[dict]:
    """theta*_beta per (mode, beta*, c_s scale): the exact minimiser of J
    (the name breaks an exact tie), which Proposition D.5 puts on the hull.
    Every configuration within a relative 1e-12 of it is listed as a tie,
    for information only: a near-tie need not be on the hull."""
    out = []
    for mode, beta, scale in research_objective.objective_grid(contract):
        weights = research_objective.mode_weights(contract, mode, beta)
        values = {name: research_objective.j_beta(x, weights, scale)
                  for name, x in points.items()}
        star = min(values, key=lambda n: (values[n], n))
        best = values[star]
        ties = sorted(n for n, v in values.items()
                      if v - best <= 1e-12 * abs(best))
        ranked = sorted(values.values())
        out.append({"mode": mode, "beta_star": beta, "cs_scale": scale,
                    "column": research_objective.j_column(mode, beta, scale),
                    "theta_star": star, "ties": ties, "J": best,
                    "gap_to_next": (ranked[1] - best if len(ranked) > 1
                                    else None)})
    return out


def beta_bar(points: dict[str, tuple], mode: str, cs_scale: float):
    """Proposition D.4's bound M / Delta for the mode's prioritised cost P:
    Delta the least positive gap above min P, M the range of the sum of the
    other two costs. None when every point has the same P (any beta* is
    strict priority)."""
    k = PRIORITISED[mode]
    scaled = [(w, r, s * cs_scale) for w, r, s in points.values()]
    p_min = min(x[k] for x in scaled)
    gaps = [x[k] - p_min for x in scaled if x[k] > p_min]
    if not gaps:
        return None
    others = [sum(x) - x[k] for x in scaled]
    return (max(others) - min(others)) / min(gaps)


def analyze(configs: dict[str, dict[int, dict]], contract: dict) -> dict:
    points = {name: mean_costs(samples) for name, samples in configs.items()}
    hull = lower_hull(points)
    chosen = comparators(points, contract)
    on_hull = set(hull["vertices"]) | set(hull["supported_non_vertices"])
    for item in chosen:
        if item["theta_star"] not in on_hull:
            raise AssertionError(f"theta* {item['theta_star']} is off the hull "
                                 "(Proposition C.4); the hull code is wrong")
    headline = contract["objective"]["headline_beta_star"]
    bars = {mode: {f"cs{scale:g}": beta_bar(points, mode, scale)
                   for scale in research_objective.storage_scales(contract)}
            for mode in PRIORITISED}
    return {
        "points": {name: {"costs": dict(zip(COSTS, x)),
                          "seeds": sorted(configs[name]),
                          "arms": sorted({r["arm"] for r in configs[name].values()})}
                   for name, x in points.items()},
        "lower_hull": hull, "comparators": chosen, "beta_bar": bars,
        "headline_beta_star": headline,
        "headline_in_strict_priority_regime": {
            mode: {scale: (bar is None or headline > bar)
                   for scale, bar in by_scale.items()}
            for mode, by_scale in bars.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("summary", type=Path, help="04's summary.csv")
    parser.add_argument("--workload-profile", required=True)
    parser.add_argument("--size-millions", type=int, required=True)
    parser.add_argument("--size-ratio", type=int, nargs="+", required=True,
                        help="one ratio for a per-T hull; several for C-6")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.summary.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    contract, contract_hash = research_objective.load_contract()
    configs = static_configurations(rows, args.workload_profile,
                                    set(args.size_ratio), args.size_millions)
    if not configs:
        raise SystemExit("no priced static arms selected")
    report = {"schema_version": 2, "research_objective_sha256": contract_hash,
              "workload_profile": args.workload_profile,
              "size_millions": args.size_millions,
              "size_ratios": args.size_ratio,
              **analyze(configs, contract)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(f"hull vertices: {len(report['lower_hull']['vertices'])} of "
          f"{len(configs)} configurations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
