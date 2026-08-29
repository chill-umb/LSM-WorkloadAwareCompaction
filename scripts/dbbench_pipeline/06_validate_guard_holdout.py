#!/usr/bin/env python3
"""Validate live-guard readiness on oracle runs never used for calibration."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import deque
from pathlib import Path


MAX_OVERRIDE_FRACTION = 0.01
MIN_VALID_STREAK_SECONDS = 8.0
MIN_FINALIZED = 320
REPLAY_WARMUP = 32
FIRST_WARMUP_FRACTION = 0.20
CREDIT_HORIZON_MICROS = 4_000_000


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


def validate_run(path: Path, fingerprint: str) -> dict:
    ready = False
    elapsed = 0
    ready_elapsed = 0
    actuation_frames = 0
    override_frames = 0
    current_streak = 0
    longest_streak = 0
    pending: deque[tuple[int, int]] = deque()
    finalized = 0
    first_warmup_at = None
    actual_interventions = 0
    reason_counts: dict[str, int] = {}

    with path.open(errors="strict") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
            if record.get("schema_version") != 1:
                raise ValueError(f"{path}:{line_number}: unsupported schema")
            if record.get("experiment_fingerprint") != fingerprint:
                raise ValueError(f"{path}:{line_number}: fingerprint mismatch")
            try:
                interval = int(record["interval_micros"])
                observed_levels = int(record["observed_levels"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"{path}:{line_number}: malformed shadow frame") from exc
            if interval <= 0 or observed_levels < 0:
                raise ValueError(f"{path}:{line_number}: invalid shadow counters")
            elapsed += interval
            if record.get("enforcement_enabled") is not False:
                raise ValueError(f"{path}:{line_number}: holdout enforcement was enabled")
            if bool(record.get("intervention_applied", False)):
                actual_interventions += 1

            if not bool(record.get("guard_ready", False)):
                pending.clear()
                current_streak = 0
                continue
            if not ready:
                ready = True
                ready_elapsed = elapsed
            if not bool(record.get("actuation_frame", False)):
                continue

            actuation_frames += 1
            invalid = bool(record.get("would_invalidate_frame", False))
            reason = str(record.get("reason_mask", 0))
            if invalid:
                override_frames += 1
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
                pending.clear()
                current_streak = 0
                continue

            current_streak += interval
            longest_streak = max(longest_streak, current_streak)
            pending.append((elapsed, observed_levels))
            while pending and elapsed - pending[0][0] >= CREDIT_HORIZON_MICROS:
                _, count = pending.popleft()
                finalized += count
                if finalized >= REPLAY_WARMUP and first_warmup_at is None:
                    first_warmup_at = elapsed

    measured = elapsed - ready_elapsed if ready else 0
    override_fraction = (
        override_frames / actuation_frames if actuation_frames else None
    )
    first_warmup_fraction = (
        (first_warmup_at - ready_elapsed) / measured
        if first_warmup_at is not None and measured > 0
        else None
    )
    checks = {
        "guard_became_ready": ready,
        "has_actuation_frames": actuation_frames > 0,
        "override_fraction": (
            override_fraction is not None
            and override_fraction <= MAX_OVERRIDE_FRACTION
        ),
        "valid_streak": longest_streak >= MIN_VALID_STREAK_SECONDS * 1_000_000,
        "finalized_transitions": finalized >= MIN_FINALIZED,
        "replay_warmup_early": (
            first_warmup_fraction is not None
            and first_warmup_fraction <= FIRST_WARMUP_FRACTION
        ),
        "no_actual_intervention": actual_interventions == 0,
    }
    return {
        "path": str(path),
        "actuation_frames": actuation_frames,
        "would_override_frames": override_frames,
        "would_override_fraction": override_fraction,
        "longest_valid_streak_seconds": longest_streak / 1_000_000,
        "simulated_finalized_transitions": finalized,
        "first_replay_warmup_fraction": first_warmup_fraction,
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

    manifest = json.loads(args.manifest.read_text())
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
            run = validate_run(run_dir / "safety_shadow.jsonl", fingerprint)
        except (OSError, UnicodeError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        run["dbbench_seed"] = workload_seed
        runs.append(run)

    report = {
        "schema_version": 1,
        "experiment_fingerprint": fingerprint,
        "size_millions": size_m,
        "size_ratio": ratio,
        "calibration_workload_seeds": sorted(calibration_seeds),
        "holdout_workload_seeds": sorted(holdout_seeds),
        "thresholds": {
            "maximum_would_override_fraction": MAX_OVERRIDE_FRACTION,
            "minimum_valid_streak_seconds": MIN_VALID_STREAK_SECONDS,
            "minimum_simulated_finalized_transitions": MIN_FINALIZED,
            "replay_warmup": REPLAY_WARMUP,
            "maximum_first_warmup_fraction": FIRST_WARMUP_FRACTION,
        },
        "runs": runs,
        "passed": all(run["passed"] for run in runs),
    }
    atomic_json(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
