#!/usr/bin/env python3
"""Paired evaluation of one arm against the static comparator (PATHWAYS
Pathway C §4, Global acceptance; PREREGISTRATION D-13 §8).

From 04's summary.csv, for every workload in it at one size and T, and for
every (mode, beta*, c_s scale) the contract reports:
  - CMP-3: the paired 95% interval of J_beta(policy) - J_beta(theta*),
    theta* = the static configuration minimising J_beta (frontier_analysis),
    paired by seed within one session (CMP-8);
  - the stall rule on the same pairs: the upper bound of the stall-fraction
    difference at most delta_stall, and the lower bound of the relative
    throughput difference at least -delta_thr;
  - regret, Definition C.5: mean J(policy) / mean J(theta*) - 1;
  - suite robustness, CMP-7, across the workloads: the policy's worst regret
    below the best worst-case regret of any one static configuration.

Bounds are the ends of pipeline_stats.ci95, the two-sided 95% Student-t
interval, so each is a one-sided 97.5% bound: the conservative reading of
D-13's "upper 95% paired bound".
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

import frontier_analysis
import research_objective
from pipeline_stats import ci95

# Pairs must match on these, besides the seed.
PAIRED = ("session_id", "workload_profile", "dbbench_sha256", "prices_sha256",
          "reference_rate", "research_objective_sha256", "settle_hold_seconds",
          "get_operations", "put_operations", "scan_operations")


def stall_rule(pairs: list[tuple[dict, dict]], rule: dict) -> dict:
    """D-13 §8 on (policy, comparator) row pairs."""
    stall = ci95([float(p["stall_fraction"]) - float(c["stall_fraction"])
                  for p, c in pairs])
    throughput = ci95([research_objective.relative_difference(
        float(p["throughput_ops_per_second"]),
        float(c["throughput_ops_per_second"])) for p, c in pairs])
    if stall["upper"] is None:
        return {"stall_fraction_difference": stall,
                "throughput_relative_difference": throughput,
                "passed": None, "reason": "needs at least two pairs"}
    stall_ok = stall["upper"] <= rule["stall_fraction_margin"]
    throughput_ok = throughput["lower"] >= -rule["throughput_relative_margin"]
    return {"stall_fraction_difference": stall,
            "throughput_relative_difference": throughput,
            "stall_passed": stall_ok, "throughput_passed": throughput_ok,
            "passed": stall_ok and throughput_ok}


def regret(policy_j: float, comparator_j: float) -> float:
    return policy_j / comparator_j - 1.0


def suite_robustness(policy: dict[str, float],
                     static: dict[str, dict[str, float]]) -> dict:
    """Definition C.5. policy: {workload: mean J}; static: {config key:
    {workload: mean J}}. theta*(w) is the least J among the static
    configurations at w; only configurations measured on every workload
    compete for the min-max."""
    workloads = sorted(policy)
    best = {w: min(j[w] for j in static.values() if w in j) for w in workloads}
    policy_worst = max(regret(policy[w], best[w]) for w in workloads)
    complete = {key: j for key, j in static.items()
                if all(w in j for w in workloads)}
    static_worst = {key: max(regret(j[w], best[w]) for w in workloads)
                    for key, j in complete.items()}
    minimax = min(static_worst.values()) if static_worst else None
    return {"workloads": workloads, "policy_worst_regret": policy_worst,
            "best_static_worst_regret": minimax,
            # Not measured on every workload, so not one static setting
            # across the suite: e.g. a profile measured per workload.
            "excluded_configurations": sorted(set(static) - set(complete)),
            "best_static": (min(static_worst, key=static_worst.get)
                            if static_worst else None),
            "suite_robust": (policy_worst < minimax
                             if minimax is not None else None)}


def config_key(row: dict) -> str:
    """A static configuration across workloads: its fingerprint without the
    workload's own segments (profile, mix, skew or power law, and q-bar,
    which is fixed per workload)."""
    tail = row["experiment_fingerprint"].split(":", 1)[1]
    return ":".join(part for part in tail.split(":")
                    if not part.startswith(("mix", "skew", "pow", "qbar")))


def evaluate(rows: list[dict], policy_arm: str, size_millions: int,
             size_ratio: int, contract: dict) -> dict:
    by_workload = defaultdict(list)
    for row in rows:
        if (int(row["size_millions"]) == size_millions and
                int(row["size_ratio"]) == size_ratio):
            by_workload[row["workload_profile"]].append(row)
    results, suite_inputs = {}, defaultdict(lambda: ([], {}))
    for workload, selected in sorted(by_workload.items()):
        mine = [r for r in selected if r["arm"] == policy_arm]
        policy = {int(r["dbbench_seed"]): r for r in mine}
        if not policy:
            continue
        if len(policy) != len(mine):
            raise ValueError(f"{workload}: {policy_arm} repeats a seed")
        if any(r["objective_status"] != "priced" for r in policy.values()):
            raise ValueError(f"{workload}: {policy_arm} has unpriced runs")
        configs = frontier_analysis.static_configurations(
            selected, workload, {size_ratio}, size_millions)
        if not configs:
            raise ValueError(f"{workload}: no static arms to compare with")
        points = {n: frontier_analysis.mean_costs(s) for n, s in configs.items()}
        cells = []
        for item in frontier_analysis.comparators(points, contract):
            column, star = item["column"], configs[item["theta_star"]]
            seeds = sorted(policy.keys() & star.keys())
            pairs = [(policy[s], star[s]) for s in seeds]
            for p, c in pairs:
                unmatched = [k for k in PAIRED if p[k] != c[k]]
                if unmatched:
                    raise ValueError(f"seed {p['dbbench_seed']}: {policy_arm} "
                                     f"and theta* differ in {unmatched}")
            differences = [float(p[column]) - float(c[column]) for p, c in pairs]
            interval = ci95(differences) if differences else None
            policy_j = statistics.fmean(float(r[column]) for r in policy.values())
            cells.append({
                **{k: item[k] for k in ("mode", "beta_star", "cs_scale",
                                        "theta_star", "ties")},
                "pairs": len(pairs),
                "unpaired_seeds": sorted(policy.keys() ^ star.keys()),
                "cmp3_difference": interval,
                "cmp3_gain": (interval["upper"] < 0
                              if interval and interval["upper"] is not None
                              else None),
                "stall_rule": (stall_rule(pairs, contract["stall_rule"])
                               if pairs else None),
                "regret": regret(policy_j, item["J"]),
            })
            key = (item["mode"], item["beta_star"], item["cs_scale"])
            suite_inputs[key][0].append((workload, policy_j))
            statics = suite_inputs[key][1]
            for name, samples in configs.items():
                row = next(iter(samples.values()))
                statics.setdefault(config_key(row), {})[workload] = (
                    research_objective.j_beta(
                        points[name], research_objective.mode_weights(
                            contract, item["mode"], item["beta_star"]),
                        item["cs_scale"]))
        results[workload] = cells
    suite = []
    for (mode, beta, scale), (policy_js, statics) in suite_inputs.items():
        if len(policy_js) > 1:
            suite.append({"mode": mode, "beta_star": beta, "cs_scale": scale,
                          **suite_robustness(dict(policy_js), statics)})
    return {"per_workload": results, "suite_robustness": suite}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("summary", type=Path, help="04's summary.csv")
    parser.add_argument("--policy-arm", required=True)
    parser.add_argument("--size-millions", type=int, required=True)
    parser.add_argument("--size-ratio", type=int, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with args.summary.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    contract, contract_hash = research_objective.load_contract()
    report = {"schema_version": 3, "research_objective_sha256": contract_hash,
              "policy_arm": args.policy_arm,
              "size_millions": args.size_millions,
              "size_ratio": args.size_ratio,
              **evaluate(rows, args.policy_arm, args.size_millions,
                         args.size_ratio, contract)}
    if not report["per_workload"]:
        raise SystemExit(f"no {args.policy_arm} runs selected")
    rendered = json.dumps(report, indent=2, sort_keys=True,
                          allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
