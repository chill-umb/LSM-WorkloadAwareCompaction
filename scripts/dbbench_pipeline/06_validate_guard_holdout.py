#!/usr/bin/env python3
"""Validate live-guard readiness on oracle runs never used for calibration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path


MAX_OVERRIDE_FRACTION = 0.01


def json_boolean(record: dict, name: str, context: str) -> bool:
    value = record.get(name)
    if type(value) is not bool:
        raise ValueError(f"{context}: {name} is not a JSON Boolean")
    return value


def nonnegative_json_integer(record: dict, name: str, context: str) -> int:
    value = record.get(name)
    if type(value) is not int or value < 0:
        raise ValueError(
            f"{context}: {name} is not a non-negative JSON integer")
    return value


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(errors="replace").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def validate_run(path: Path, fingerprint: str, manifest_sha256: str) -> dict:
    ready = False
    elapsed = 0
    actuation_frames = 0
    override_frames = 0
    actual_interventions = 0
    reason_counts: dict[str, int] = {}
    previous_time = None

    with path.open(errors="strict") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
            if record.get("schema_version") != 2:
                raise ValueError(f"{path}:{line_number}: unsupported schema")
            if record.get("experiment_fingerprint") != fingerprint:
                raise ValueError(f"{path}:{line_number}: fingerprint mismatch")
            if record.get("baseline_slo_sha256") != manifest_sha256:
                raise ValueError(
                    f"{path}:{line_number}: baseline manifest mismatch")
            context = f"{path}:{line_number}"
            interval = nonnegative_json_integer(
                record, "interval_micros", context)
            nonnegative_json_integer(record, "observed_levels", context)
            timestamp = nonnegative_json_integer(record, "time_micros", context)
            reason_mask = nonnegative_json_integer(record, "reason_mask", context)
            enforcement_enabled = json_boolean(
                record, "enforcement_enabled", context)
            intervention_applied = json_boolean(
                record, "intervention_applied", context)
            guard_ready = json_boolean(record, "guard_ready", context)
            actuation_frame = json_boolean(record, "actuation_frame", context)
            would_override = json_boolean(
                record, "would_override_frame", context)
            if interval == 0 or (
                previous_time is not None and timestamp <= previous_time
            ):
                raise ValueError(f"{path}:{line_number}: invalid shadow counters")
            previous_time = timestamp
            elapsed += interval
            if enforcement_enabled:
                raise ValueError(f"{path}:{line_number}: holdout enforcement was enabled")
            if intervention_applied:
                actual_interventions += 1

            if not guard_ready:
                continue
            if not ready:
                ready = True
            if not actuation_frame:
                continue

            actuation_frames += 1
            reason = str(reason_mask)
            if would_override:
                override_frames += 1
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
    override_fraction = (
        override_frames / actuation_frames if actuation_frames else None
    )
    checks = {
        "guard_became_ready": ready,
        "has_actuation_frames": actuation_frames > 0,
        "override_fraction": (
            override_fraction is not None
            and override_fraction <= MAX_OVERRIDE_FRACTION
        ),
        "no_actual_intervention": actual_interventions == 0,
    }
    return {
        "path": str(path),
        "actuation_frames": actuation_frames,
        "would_override_frames": override_frames,
        "would_override_fraction": override_fraction,
        "actual_interventions": actual_interventions,
        "reason_mask_counts": reason_counts,
        "checks": checks,
        "passed": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--holdout-results", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if (
        manifest.get("schema_version") != 2
        or manifest.get("guard_calibrated") is not True
    ):
        raise SystemExit("holdout requires a calibrated schema-v2 manifest")
    fingerprint = str(manifest.get("experiment_fingerprint", ""))
    options = manifest.get("selected_baseline_options", {})
    try:
        size_m = int(options["size_millions"])
        ratio = int(options["size_ratio"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit("manifest lacks workload/T options") from exc
    try:
        calibration_seeds = {
            int(value)
            for value in manifest["guard_calibration"]["workload_seeds"]
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(
            "manifest lacks calibration workload-seed provenance"
        ) from exc
    if len(calibration_seeds) != args.repeats:
        raise SystemExit(
            f"expected {args.repeats} distinct calibration workload seeds"
        )

    cell = args.holdout_results / f"{size_m}M" / f"T{ratio}"
    run_dirs = sorted(cell.glob("repeat-*/oracle"))
    if len(run_dirs) != args.repeats:
        raise SystemExit(
            f"expected exactly {args.repeats} oracle holdout runs in {cell}; "
            f"found {len(run_dirs)}"
        )

    runs = []
    holdout_seeds = set()
    for run_dir in run_dirs:
        if not (run_dir / "COMPLETED").exists():
            raise SystemExit(f"incomplete holdout run: {run_dir}")
        metadata = read_env(run_dir / "metadata.env")
        if (
            metadata.get("experiment_fingerprint") != fingerprint
            or metadata.get("arm") != "oracle"
            or metadata.get("rl_run_phase") != "holdout"
            or metadata.get("rl_safety_enforcement") != "0"
            or metadata.get("baseline_slo_sha256") != manifest_sha256
        ):
            raise SystemExit(f"holdout metadata mismatch: {run_dir}")
        try:
            workload_seed = int(metadata["dbbench_seed"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(f"missing holdout workload seed: {run_dir}") from exc
        if workload_seed in holdout_seeds:
            raise SystemExit(
                f"duplicate holdout workload seed {workload_seed}: {run_dir}"
            )
        if workload_seed in calibration_seeds:
            raise SystemExit(
                f"holdout reuses calibration workload seed {workload_seed}: "
                f"{run_dir}"
            )
        holdout_seeds.add(workload_seed)
        try:
            run = validate_run(
                run_dir / "safety_shadow.jsonl", fingerprint, manifest_sha256)
        except (OSError, UnicodeError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        run["dbbench_seed"] = workload_seed
        runs.append(run)

    report = {
        "schema_version": 2,
        "experiment_fingerprint": fingerprint,
        "baseline_slo_sha256": manifest_sha256,
        "size_millions": size_m,
        "size_ratio": ratio,
        "calibration_workload_seeds": sorted(calibration_seeds),
        "holdout_workload_seeds": sorted(holdout_seeds),
        "thresholds": {
            "maximum_would_override_fraction": MAX_OVERRIDE_FRACTION,
        },
        "runs": runs,
        "passed": all(run["passed"] for run in runs),
    }
    atomic_json(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
