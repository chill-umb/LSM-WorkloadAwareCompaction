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
from pipeline_stats import ci95, objective_verdict
from research_objective import (DEFAULT_CONTRACT, load_contract, metric_specs,
                                relative_difference)


def f(row: dict, key: str) -> float:
    value = float(row[key])
    if not math.isfinite(value):
        raise SystemExit(f"non-finite {key} in {row.get('result_directory')}")
    return value


def relative(rl: float, baseline: float) -> float:
    return relative_difference(rl, baseline)


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
        help="check frozen constraints without requiring point-read improvement",
    )
    parser.add_argument(
        "--scan-objective",
        choices=("sorted_run_seeks",),
        default="sorted_run_seeks",
        help="compatibility flag; P0 freezes sorted-run seeks non-inferiority",
    )
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--space-margin", type=float, required=True,
                        help="one frozen relative space budget: 0, .02, .05, .10")
    parser.add_argument("--pilot", action="store_true",
                        help="retrospective evaluation; never formal acceptance")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.minimum_pairs < 2:
        parser.error("minimum-pairs must be at least two")
    contract, contract_hash = load_contract(args.contract)
    specs = metric_specs(contract, args.space_margin, args.safety_only)
    if args.baseline_arm != contract["baseline_arm"]:
        parser.error("baseline arm differs from frozen contract")

    grouped = defaultdict(dict)
    with args.summary.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if (int(row["size_millions"]) == args.size_millions and
                    int(row["size_ratio"]) == args.size_ratio):
                repeat = int(row.get("repeat", 1))
                if row["arm"] in grouped[repeat]:
                    raise SystemExit(f"duplicate arm/repeat {row['arm']}/{repeat}")
                grouped[repeat][row["arm"]] = row
    pairs = []
    for repeat, arms in sorted(grouped.items()):
        if args.baseline_arm in arms and args.rl_arm in arms:
            pairs.append((repeat, arms[args.baseline_arm], arms[args.rl_arm]))
        elif args.baseline_arm in arms or args.rl_arm in arms:
            raise SystemExit(f"repeat {repeat}: incomplete pair")
    if not pairs:
        raise SystemExit("no paired measurements")

    cell_fingerprints = set()
    cell_manifest_hashes = set()
    for repeat, baseline, rl in pairs:
        if not args.pilot:
            for row in (baseline, rl):
                if row.get("research_objective_sha256") != contract_hash:
                    raise SystemExit(f"repeat {repeat}: research objective mismatch")
                if f(row, "space_relative_margin") != args.space_margin:
                    raise SystemExit(f"repeat {repeat}: space budget mismatch")
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

    checks = {}
    for key, margin, strict in specs:
        try:
            differences = [relative(f(rl, key), f(base, key))
                           for _, base, rl in pairs]
        except ValueError as error:
            checks[key] = {"verdict": "undecidable", "passed": None,
                           "reason": str(error), "metric": key}
            continue
        checks[key] = {"metric": key, **objective_verdict(
            differences, margin, args.minimum_pairs, strict=strict)}
    failed = [key for key, check in checks.items()
              if check["verdict"] == "failed"]
    undecidable = [key for key, check in checks.items()
                   if check["verdict"] == "undecidable"]
    verdict = "failed" if failed else "undecidable" if undecidable else "passed"
    diagnostics = {}
    for key in ("stall_events", "scan_amplification", "write_latency_p95_us"):
        if all(base.get(key) not in (None, "") and rl.get(key) not in (None, "")
               for _, base, rl in pairs):
            diagnostics[key] = {"ci95_absolute_difference": ci95([
                f(rl, key) - f(base, key) for _, base, rl in pairs])}

    report = {
        "schema_version": 2,
        "size_millions": args.size_millions,
        "size_ratio": args.size_ratio,
        "pairs": len(pairs),
        "experiment_fingerprint": next(iter(cell_fingerprints)),
        "baseline_slo_sha256": next(iter(cell_manifest_hashes)),
        "scan_objective": args.scan_objective,
        "safety_only": args.safety_only,
        "research_objective_sha256": contract_hash,
        "space_relative_margin": args.space_margin,
        "pilot": args.pilot,
        "formal_acceptance": (not args.pilot and len(pairs) >= 10 and
                              verdict == "passed"),
        "checks": checks,
        "diagnostics": diagnostics,
        "verdict": verdict, "failed": failed, "undecidable": undecidable,
        "passed": {"passed": True, "failed": False}.get(verdict),
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    return {"passed": 0, "failed": 1, "undecidable": 3}[verdict]


if __name__ == "__main__":
    raise SystemExit(main())
