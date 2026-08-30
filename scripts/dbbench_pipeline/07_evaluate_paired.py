#!/usr/bin/env python3
"""Apply the preregistered paired acceptance checks to summary.csv."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

# Shared with 09_evaluate_oracle_parity.py so the engineering gate and the
# research criterion cannot drift onto different instruments.
from pipeline_stats import ci95


def f(row: dict, key: str) -> float:
    value = float(row[key])
    if not math.isfinite(value):
        raise SystemExit(f"non-finite {key} in {row.get('result_directory')}")
    return value


def relative(rl: float, baseline: float) -> float:
    if baseline == 0:
        if rl == 0:
            return 0.0
        raise SystemExit("relative regression is undefined against zero baseline")
    return rl / baseline - 1.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("summary", type=Path)
    parser.add_argument("--size-millions", required=True, type=int)
    parser.add_argument("--size-ratio", required=True, type=int)
    parser.add_argument("--baseline-arm", default="regular")
    parser.add_argument("--rl-arm", default="rl")
    parser.add_argument("--minimum-pairs", type=int, default=10)
    parser.add_argument(
        "--safety-only", action="store_true",
        help="check only 2% space/latency bounds and no stall increase",
    )
    parser.add_argument(
        "--scan-objective",
        choices=("amplification", "sorted_run_seeks", "nonregression"),
        default="sorted_run_seeks",
        help="preregistered objective; scan amplification remains non-regression",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    grouped = defaultdict(dict)
    with args.summary.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if (int(row["size_millions"]) == args.size_millions and
                    int(row["size_ratio"]) == args.size_ratio):
                grouped[int(row.get("repeat", 1))][row["arm"]] = row
    pairs = []
    for repeat, arms in sorted(grouped.items()):
        if args.baseline_arm in arms and args.rl_arm in arms:
            pairs.append((repeat, arms[args.baseline_arm], arms[args.rl_arm]))
    if len(pairs) < args.minimum_pairs:
        raise SystemExit(f"need {args.minimum_pairs} pairs; found {len(pairs)}")

    cell_fingerprints = set()
    cell_manifest_hashes = set()
    for repeat, baseline, rl in pairs:
        if baseline.get("workload_profile") != rl.get("workload_profile"):
            raise SystemExit(f"repeat {repeat}: workload profile mismatch")
        if (not baseline.get("experiment_fingerprint") or
                baseline.get("experiment_fingerprint") != rl.get(
                    "experiment_fingerprint")):
            raise SystemExit(f"repeat {repeat}: experiment fingerprint mismatch")
        if (not baseline.get("baseline_slo_sha256") or
                baseline.get("baseline_slo_sha256") !=
                rl.get("baseline_slo_sha256")):
            raise SystemExit(f"repeat {repeat}: baseline SLO manifest mismatch")
        cell_fingerprints.add(baseline["experiment_fingerprint"])
        cell_manifest_hashes.add(baseline["baseline_slo_sha256"])
        if baseline.get("dbbench_seed") != rl.get("dbbench_seed"):
            raise SystemExit(f"repeat {repeat}: workload seed mismatch")
        for key in ("get_operations", "put_operations", "scan_operations",
                    "user_write_bytes"):
            if f(baseline, key) != f(rl, key):
                raise SystemExit(f"repeat {repeat}: unpaired {key}")
    if len(cell_fingerprints) != 1:
        raise SystemExit("paired cell mixes experiment fingerprints across repeats")
    if len(cell_manifest_hashes) != 1:
        raise SystemExit("paired cell mixes baseline SLO manifests across repeats")

    strict = {} if args.safety_only else {
        "write_amplification": "write_amplification",
        "point_read_amplification": "point_read_amplification",
    }
    scan_key = ("scan_amplification" if args.scan_objective != "sorted_run_seeks"
                else "sorted_run_seeks_per_scan")
    if not args.safety_only and args.scan_objective != "nonregression":
        strict["scan_objective"] = scan_key

    checks = {}
    passed = True
    for name, key in strict.items():
        baseline_values = [f(base, key) for _, base, _ in pairs]
        if name == "scan_objective" and key == "scan_amplification" and all(
                value <= 1.0 + 1e-12 for value in baseline_values):
            raise SystemExit(
                "scan amplification is at its physical floor; preregister "
                "a workload with headroom or another section-14.5 objective")
        differences = [f(rl, key) - f(base, key) for _, base, rl in pairs]
        interval = ci95(differences)
        ok = interval["upper"] is not None and interval["upper"] < 0.0
        checks[name] = {"kind": "strict_improvement", "metric": key,
                        "ci95_rl_minus_baseline": interval, "passed": ok}
        passed &= ok

    nonregression = [
        "space_amplification",
        "get_latency_avg_us", "get_latency_p95_us",
        "scan_latency_avg_us", "scan_latency_p95_us",
        "write_latency_avg_us", "write_latency_p95_us",
    ]
    if not args.safety_only and args.scan_objective in (
        "sorted_run_seeks", "nonregression"
    ):
        nonregression.append("scan_amplification")
    for key in nonregression:
        regressions = [relative(f(rl, key), f(base, key))
                       for _, base, rl in pairs]
        interval = ci95(regressions)
        ok = interval["upper"] is not None and interval["upper"] <= 0.02
        checks[key] = {"kind": "upper_relative_regression",
                       "limit": 0.02, "ci95": interval, "passed": ok}
        passed &= ok

    stall_deltas = [f(rl, "stall_seconds") - f(base, "stall_seconds")
                    for _, base, rl in pairs]
    stall_ok = all(delta <= 0.0 for delta in stall_deltas)
    checks["stall_seconds"] = {
        "kind": "no_paired_increase", "deltas": stall_deltas,
        "ci95": ci95(stall_deltas), "passed": stall_ok,
    }
    passed &= stall_ok

    stall_event_deltas = [f(rl, "stall_events") - f(base, "stall_events")
                          for _, base, rl in pairs]
    stall_event_ok = all(delta <= 0.0 for delta in stall_event_deltas)
    checks["stall_events"] = {
        "kind": "no_paired_increase", "deltas": stall_event_deltas,
        "ci95": ci95(stall_event_deltas), "passed": stall_event_ok,
    }
    passed &= stall_event_ok

    report = {
        "schema_version": 1,
        "size_millions": args.size_millions,
        "size_ratio": args.size_ratio,
        "pairs": len(pairs),
        "experiment_fingerprint": next(iter(cell_fingerprints)),
        "baseline_slo_sha256": next(iter(cell_manifest_hashes)),
        "scan_objective": args.scan_objective,
        "safety_only": args.safety_only,
        "checks": checks,
        "passed": passed,
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
