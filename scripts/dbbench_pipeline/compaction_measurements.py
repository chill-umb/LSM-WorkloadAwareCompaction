"""Gate-0 measurements from non-trivial SST merges and admission snapshots.

Survival is output SST bytes / input SST bytes, not cache-dependent physical
reads. It is measured, never inferred from settled space amplification. Missing
release snapshots in historical logs are reported explicitly, not reconstructed.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re

EVENT = re.compile(r"EVENT_LOG_v1 (\{.*\})")


def events(path: Path):
    with path.open(errors="replace") as handle:
        for line_number, line in enumerate(handle, 1):
            if "EVENT_LOG_v1" not in line:
                continue
            match = EVENT.search(line)
            if not match:
                raise ValueError(f"{path}:{line_number}: malformed event record")
            yield json.loads(match.group(1))


def empty() -> dict:
    return {"jobs": 0, "input_bytes": 0, "output_bytes": 0, "eta": None}


def nonnegative_int(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"expected nonnegative integer, got {value!r}")
    return value


def release_snapshot(event: dict, num_levels: int) -> dict:
    required = ("release_micros", "source_level", "output_level",
                "rl_decision_id", "effective_action", "rl_override_reason",
                "capacity_generation", "occupancy_bytes", "nominal_target_bytes")
    if event.get("release_schema_version") != 1 or any(
            key not in event for key in required):
        raise ValueError("release event lacks the Gate-0 snapshot")
    occupancy = event["occupancy_bytes"]
    targets = event["nominal_target_bytes"]
    if len(occupancy) != num_levels or len(targets) != num_levels:
        raise ValueError("release snapshot geometry mismatch")
    for value in (*occupancy, *targets, event["release_micros"],
                  event["rl_decision_id"], event["capacity_generation"]):
        nonnegative_int(value)
    source = nonnegative_int(event["source_level"])
    if source >= num_levels or event["output_level"] >= num_levels:
        raise ValueError("release source/output level outside tree")
    if any(target <= 0 for target in targets[1:]):
        raise ValueError("missing nominal deep-level target")
    phi = [size / target if target else None
           for size, target in zip(occupancy, targets)]
    return {**event, "phi": phi, "kappa": phi[source],
            "populated_levels": sum(size > 0 for size in occupancy),
            "deepest_populated_level": max(
                (i for i, size in enumerate(occupancy) if size), default=-1)}


def analyze(path: Path, num_levels: int) -> dict:
    if num_levels < 2:
        raise ValueError("leveled measurements require at least two levels")
    views = {phase: {"global": empty(), "levels": {
        str(level): empty() for level in range(num_levels)}}
        for phase in ("workload", "drain", "whole_run")}
    releases, started, completed = {}, {}, set()
    snapshots = []
    excluded_moves = missing_merges = 0
    for event in events(path):
        kind = event.get("event")
        job = event.get("job")
        if job is None:
            continue
        # Job IDs are allocated process-wide; retain CF identity in snapshots.
        if kind == "compaction_release":
            if job in releases:
                raise ValueError(f"duplicate release for job {job}")
            snapshot = release_snapshot(event, num_levels)
            releases[job] = snapshot
            snapshots.append(snapshot)
        elif kind == "compaction_started":
            started[job] = event
        elif kind == "trivial_move":
            excluded_moves += 1
            completed.add(job)
        elif kind == "compaction_finished":
            if job in completed:
                raise ValueError(f"duplicate completion for job {job}")
            completed.add(job)
            if event.get("merge_schema_version") != 1:
                missing_merges += 1
                continue
            if event.get("merge_success") is not True:
                raise ValueError(f"unsuccessful merge job {job}")
            level = nonnegative_int(event["source_level"])
            if level >= num_levels:
                raise ValueError("merge source outside tree")
            read = nonnegative_int(event["merge_input_bytes"])
            written = nonnegative_int(event["merge_output_bytes"])
            if not read:
                raise ValueError("non-trivial merge has no input")
            # Do not clamp eta: compression/table metadata can make it exceed 1.
            phase = "drain" if event.get("rl_drain") is True else "workload"
            for view in (phase, "whole_run"):
                for bucket in (views[view]["global"], views[view]["levels"][str(level)]):
                    bucket["jobs"] += 1
                    bucket["input_bytes"] += read
                    bucket["output_bytes"] += written
    for view in views.values():
        for bucket in (view["global"], *view["levels"].values()):
            if bucket["input_bytes"]:
                bucket["eta"] = bucket["output_bytes"] / bucket["input_bytes"]
        for key in ("jobs", "input_bytes", "output_bytes"):
            if sum(b[key] for b in view["levels"].values()) != view["global"][key]:
                raise ValueError("per-level/global accounting mismatch")
    missing_releases = sorted(completed - releases.keys())
    unfinished = sorted((releases.keys() | started.keys()) - completed)
    return {"schema_version": 1, "source": str(path), "num_levels": num_levels,
            "survival_definition": "nontrivial_sst_output_bytes/input_bytes",
            "phase_definition": "phase_at_completion", "views": views,
            "releases": snapshots, "excluded_trivial_moves": excluded_moves,
            "missing_merge_measurements": missing_merges,
            "missing_release_jobs": missing_releases, "unfinished_jobs": unfinished,
            "complete": bool(completed) and not (
                missing_merges or missing_releases or unfinished)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--num-levels", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = analyze(args.log, args.num_levels)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
