#!/usr/bin/env python3
"""Calibrate executable live-latency limits from compact oracle telemetry."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import tempfile
from collections import deque
from pathlib import Path

from slo_statistics import TOLERANCE_CONFIDENCE, tolerance_bound


OPS = ("get", "scan", "write")
BUCKETS = 64
# Every key RLSafetyController::Parse (lib/rocksdb/db/compaction/
# rl_safety_manifest.cc) reads from the manifest. Its field readers take the
# FIRST textual occurrence of `"key"` anywhere in the file, not the top-level
# one, so each of these must occur exactly once in the serialized manifest or
# the C++ side silently reads a nested value. A nested "schema_version": 1 in
# the D-12 trajectory block did exactly that: the manifest was rejected and the
# arm ran under the all-due fallback. Keep in lockstep with Parse.
CPP_FIRST_MATCH_KEYS = (
    "schema_version", "metric_definitions_version", "experiment_fingerprint",
    "guard_calibrated", "guard_minimum_samples", "guard_rolling_window_count",
    "guard_hysteresis_enter_windows", "guard_hysteresis_exit_windows",
    "guard_p95_method", "allowed_physical_sst_bytes",
    "allowed_pending_debt_ratio",
    "guard_get_latency_avg_ns_limit", "guard_get_latency_p95_ns_limit",
    "guard_scan_latency_avg_ns_limit", "guard_scan_latency_p95_ns_limit",
    "guard_write_latency_avg_ns_limit", "guard_write_latency_p95_ns_limit",
    "level_limits",
)
MARGIN = 1.02
DEFINITIONS = "trigger-v2-logical-v3"


def nonnegative_json_integer(value, context: str) -> int:
    # bool is an int subclass in Python; accepting true as a sample count would
    # let malformed JSON survive every later range check.
    if type(value) is not int or value < 0:
        raise ValueError(f"{context}: expected a non-negative JSON integer")
    return value


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(errors="replace").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def bucket_upper_bound(bucket: int) -> int:
    return (1 << 64) - 1 if bucket >= 63 else (1 << (bucket + 1)) - 1


def histogram_percentile(buckets: list[int], probability: float) -> int:
    count = sum(buckets)
    if count == 0:
        return 0
    rank = math.ceil(count * probability)
    seen = 0
    for index, value in enumerate(buckets):
        seen += value
        if seen >= rank:
            return bucket_upper_bound(index)
    raise ValueError("histogram rank exceeds its sample count")


def parse_operation(record: dict, operation: str, path: Path, line: int) -> dict:
    value = record.get(operation)
    if not isinstance(value, dict):
        raise ValueError(f"{path}:{line}: missing {operation} object")
    try:
        count = nonnegative_json_integer(
            value["count"], f"{path}:{line}:{operation}.count")
        sum_ns = nonnegative_json_integer(
            value["sum_ns"], f"{path}:{line}:{operation}.sum_ns")
        raw_buckets = value["buckets"]
        if not isinstance(raw_buckets, list):
            raise ValueError("buckets is not an array")
        buckets = [nonnegative_json_integer(
            item, f"{path}:{line}:{operation}.buckets[{index}]")
            for index, item in enumerate(raw_buckets)]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path}:{line}: malformed {operation} telemetry") from exc
    histogram_count = sum(buckets)
    if len(buckets) != BUCKETS or histogram_count != count:
        raise ValueError(
            f"{path}:{line}: invalid {operation} counters "
            f"(count={count}, histogram_count={histogram_count}, "
            f"buckets={len(buckets)})"
        )
    return {"count": count, "sum_ns": sum_ns, "buckets": buckets}


def run_totals(path: Path) -> tuple[dict[str, list[int]], int]:
    """Whole-run count and latency sum per operation, in the telemetry's own
    unit (PREREGISTRATION D-10).

    The reward's latency hinge compares the frame's telemetry average against
    a limit, and the formal `*_latency_avg_ns_limit` comes from db_bench's
    histogram, a different instrument: on `Assoc` at 10M the two disagree by
    19x on scans and 9x on writes (get agrees to 1%). These totals give the
    baseline's average as THIS instrument measures it, so the reward's hinge
    and its limit share a unit. Windows before the first Get are the tail of
    the bulk load (the controller is suspended during it and mixgraph issues
    Gets from its first operation) and are skipped.
    """
    totals = {operation: [0, 0] for operation in OPS}
    windows = 0
    started = False
    with path.open(errors="strict") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if not started and int(record["get"]["count"]) > 0:
                started = True
            if not started:
                continue
            windows += 1
            for operation in OPS:
                totals[operation][0] += int(record[operation]["count"])
                totals[operation][1] += int(record[operation]["sum_ns"])
    return totals, windows


def since_warmup_trajectory(
    paths: list[Path], warmup_seconds: float, resolution_seconds: float
) -> tuple[list[float], dict[str, list[float]], int]:
    """The baseline's cumulative average latency per operation SINCE the
    multiplier warm-up, on an elapsed-time grid (PREREGISTRATION D-12).

    The reward's latency multiplier moves on the run-to-date average against
    the whole-run limit above. That average is poisoned by the first 1-2 s
    after `rlresume`: the tree the suspended controller inherits from the bulk
    load holds L0 at 16-22 files, above the slowdown trigger, and the write
    controller stalls writes at 15-32x the limit. The calibration arms carry
    the same transient, so measured this way the BASELINE's own cumulative
    write latency sits above its limit until 120-150 s of a 160 s run, and any
    policy reads a positive slack for most of the run (history 14.23). The
    slack is therefore measured from the end of the warm-up, against the
    baseline's cumulative from the same instant at the same elapsed time.

    Elapsed time is measured from the first window carrying a Get, as
    `run_totals` does; windows before `warmup_seconds` are skipped. At each
    grid point the runs are pooled count-weighted over their cumulative totals
    as of that time (a run that has ended contributes its final total), so the
    last point is the pooled since-warm-up average over every run.
    """
    per_run = []
    end = 0.0
    for path in paths:
        t0 = None
        rows: list[tuple[float, dict[str, int], dict[str, int]]] = []
        count = {operation: 0 for operation in OPS}
        total = {operation: 0 for operation in OPS}
        with path.open(errors="strict") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                if t0 is None:
                    if int(record["get"]["count"]) > 0:
                        t0 = int(record["time_micros"])
                    else:
                        continue
                elapsed = (int(record["time_micros"]) - t0) / 1e6
                if elapsed < warmup_seconds:
                    continue
                for operation in OPS:
                    count[operation] += int(record[operation]["count"])
                    total[operation] += int(record[operation]["sum_ns"])
                rows.append((elapsed, dict(count), dict(total)))
        if not rows:
            raise ValueError(
                f"{path}: no telemetry windows after the {warmup_seconds} s warm-up")
        per_run.append(([row[0] for row in rows], rows))
        end = max(end, rows[-1][0])
    grid: list[float] = []
    series: dict[str, list[float]] = {operation: [] for operation in OPS}
    points = []
    t = warmup_seconds + resolution_seconds
    while t < end:
        points.append(t)
        t += resolution_seconds
    points.append(end)
    for t in points:
        pooled_count = {operation: 0 for operation in OPS}
        pooled_total = {operation: 0 for operation in OPS}
        for times, rows in per_run:
            index = bisect.bisect_right(times, t + 1e-9) - 1
            if index < 0:
                continue
            for operation in OPS:
                pooled_count[operation] += rows[index][1][operation]
                pooled_total[operation] += rows[index][2][operation]
        if any(pooled_count[operation] <= 0 for operation in OPS):
            continue
        grid.append(round(t, 6))
        for operation in OPS:
            series[operation].append(pooled_total[operation] / pooled_count[operation])
    if not grid:
        raise ValueError("no pooled telemetry after the warm-up")
    return grid, series, len(per_run)


def aggregate(samples: deque[dict]) -> tuple[int, int, list[int]]:
    count = sum(sample["count"] for sample in samples)
    sum_ns = sum(sample["sum_ns"] for sample in samples)
    buckets = [0] * BUCKETS
    for sample in samples:
        for index, value in enumerate(sample["buckets"]):
            buckets[index] += value
    return count, sum_ns, buckets


def read_run(
    path: Path,
    fingerprint: str,
    rolling: int,
    minimum_samples: int,
) -> tuple[dict[str, list[float]], int]:
    windows = {operation: deque(maxlen=rolling) for operation in OPS}
    values = {
        f"{operation}_{statistic}": []
        for operation in OPS
        for statistic in ("avg", "p95")
    }
    interval_count = 0
    previous_time = None
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
                timestamp = nonnegative_json_integer(
                    record["time_micros"], f"{path}:{line_number}:time_micros")
                interval = nonnegative_json_integer(
                    record["interval_micros"],
                    f"{path}:{line_number}:interval_micros")
            except (KeyError, ValueError) as exc:
                raise ValueError(
                    f"{path}:{line_number}: malformed interval metadata") from exc
            if interval == 0 or (
                previous_time is not None and timestamp <= previous_time
            ):
                raise ValueError(
                    f"{path}:{line_number}: non-positive interval or "
                    "non-monotonic timestamp")
            previous_time = timestamp
            interval_count += 1
            for operation in OPS:
                windows[operation].append(
                    parse_operation(record, operation, path, line_number)
                )

            # First sample covers [1, rolling], second covers
            # [rolling+1, 2*rolling], and so on. The runtime still classifies
            # every rolling frame; this subsampling only avoids pretending its
            # highly overlapping calibration observations are independent.
            if interval_count < rolling or interval_count % rolling:
                continue
            for operation in OPS:
                count, sum_ns, buckets = aggregate(windows[operation])
                histogram_count = sum(buckets)
                if count < minimum_samples or histogram_count < minimum_samples:
                    continue
                values[f"{operation}_avg"].append(sum_ns / count)
                values[f"{operation}_p95"].append(
                    float(histogram_percentile(buckets, 0.95))
                )
    if interval_count == 0:
        raise ValueError(f"empty calibration log: {path}")
    return values, interval_count


def check_cpp_first_match_keys(value: dict) -> None:
    """Refuse a manifest the C++ parser would mis-read (see CPP_FIRST_MATCH_KEYS)."""
    text = json.dumps(value, indent=2, sort_keys=True)
    repeated = {key: text.count(f'"{key}"') for key in CPP_FIRST_MATCH_KEYS}
    bad = {key: count for key, count in repeated.items() if count != 1}
    if bad:
        raise SystemExit(
            "manifest would be mis-read by rl_safety_manifest.cc, which takes "
            f"the first textual match of each key; occurrence counts: {bad}")


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection-manifest", required=True, type=Path)
    parser.add_argument("--calibration-results", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--accept-selection-sha256", action="append", default=[],
        help="D-11: a selection-manifest SHA-256 the calibration runs may "
             "have recorded instead of the current one. Only for a selection "
             "manifest regenerated with the SAME selected options (the "
             "fingerprint check below still binds); recorded in the output.")
    parser.add_argument(
        "--warmup-seconds", type=float, default=30.0,
        help="D-12: the reward's multiplier warm-up (RL_LAMBDA_WARMUP_SECONDS); "
             "the latency reference trajectory starts here and the reward "
             "refuses a manifest whose warm-up differs from its own.")
    parser.add_argument(
        "--trajectory-resolution-seconds", type=float, default=1.0)
    args = parser.parse_args()
    if args.warmup_seconds < 0.0 or args.trajectory_resolution_seconds <= 0.0:
        raise SystemExit("invalid trajectory configuration")

    selection_bytes = args.selection_manifest.read_bytes()
    manifest = json.loads(selection_bytes)
    selection_sha256 = hashlib.sha256(selection_bytes).hexdigest()
    if (
        manifest.get("schema_version") != 2
        or manifest.get("metric_definitions_version") != DEFINITIONS
        or manifest.get("guard_calibrated") is not False
    ):
        raise SystemExit("selection manifest is not an uncalibrated schema-v2 manifest")
    if args.output.resolve() == args.selection_manifest.resolve():
        raise SystemExit("calibration output must not overwrite the selection manifest")

    fingerprint = str(manifest.get("experiment_fingerprint", ""))
    options = manifest.get("selected_baseline_options", {})
    try:
        size_m = int(options["size_millions"])
        ratio = int(options["size_ratio"])
        rolling = int(manifest["guard_rolling_window_count"])
        minimum_samples = int(manifest["guard_minimum_samples"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit("selection manifest lacks executable guard metadata") from exc
    if not fingerprint or rolling <= 0 or minimum_samples <= 0 or args.repeats <= 0:
        raise SystemExit("invalid calibration configuration")

    cell = args.calibration_results / f"{size_m}M" / f"T{ratio}"
    run_dirs = sorted(cell.glob("repeat-*/oracle"))
    if len(run_dirs) != args.repeats:
        raise SystemExit(
            f"expected exactly {args.repeats} oracle calibration runs in {cell}; "
            f"found {len(run_dirs)}"
        )

    combined = {
        f"{operation}_{statistic}": []
        for operation in OPS
        for statistic in ("avg", "p95")
    }
    run_metadata = []
    workload_seeds = set()
    telemetry_totals = {operation: [0, 0] for operation in OPS}
    telemetry_windows = 0
    log_paths: list[Path] = []
    for run_dir in run_dirs:
        if not (run_dir / "COMPLETED").exists():
            raise SystemExit(f"incomplete calibration run: {run_dir}")
        metadata = read_env(run_dir / "metadata.env")
        if metadata.get("experiment_fingerprint") != fingerprint:
            raise SystemExit(f"calibration fingerprint mismatch: {run_dir}")
        accepted_shas = {selection_sha256, *args.accept_selection_sha256}
        if (
            metadata.get("arm") != "oracle"
            or metadata.get("rl_run_phase") != "calibration"
            or metadata.get("rl_safety_enforcement") != "0"
            or metadata.get("baseline_slo_sha256") not in accepted_shas
        ):
            raise SystemExit(f"not an oracle calibration run: {run_dir}")
        try:
            workload_seed = int(metadata["dbbench_seed"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(
                f"missing calibration workload seed: {run_dir}"
            ) from exc
        if workload_seed in workload_seeds:
            raise SystemExit(
                f"duplicate calibration workload seed {workload_seed}: {run_dir}"
            )
        workload_seeds.add(workload_seed)
        log_path = run_dir / "latency_windows.jsonl"
        try:
            values, interval_count = read_run(
                log_path, fingerprint, rolling, minimum_samples
            )
        except (OSError, UnicodeError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        for name, samples in values.items():
            combined[name].extend(samples)
        try:
            totals, windows = run_totals(log_path)
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
            raise SystemExit(f"{log_path}: cannot total telemetry: {exc}") from exc
        for operation in OPS:
            telemetry_totals[operation][0] += totals[operation][0]
            telemetry_totals[operation][1] += totals[operation][1]
        telemetry_windows += windows
        log_paths.append(log_path)
        run_metadata.append(
            {
                "directory": str(run_dir),
                "interval_count": interval_count,
                "dbbench_seed": workload_seed,
            }
        )

    estimators = {}
    for operation in OPS:
        for statistic in ("avg", "p95"):
            name = f"{operation}_{statistic}"
            bound, metadata = tolerance_bound(combined[name])
            if bound is None or not math.isfinite(bound) or bound <= 0:
                raise SystemExit(
                    f"insufficient calibration samples for {name}: {metadata}"
                )
            key = f"guard_{operation}_latency_{statistic}_ns_limit"
            manifest[key] = (
                int(math.ceil(MARGIN * bound))
                if statistic == "p95"
                else MARGIN * bound
            )
            estimators[name] = metadata

    # D-10: the latency averages in the telemetry's own unit, for the reward.
    # Same 2% margin as the formal limits (P0-4); the formal limits stay and
    # remain what the evaluator scores.
    telemetry_reference = {}
    for operation in OPS:
        count, sum_ns = telemetry_totals[operation]
        if count <= 0 or sum_ns <= 0:
            raise SystemExit(
                f"no {operation} telemetry in the calibration windows")
        average = sum_ns / count
        manifest[f"{operation}_latency_avg_ns_telemetry_reference"] = average
        manifest[f"{operation}_latency_avg_ns_telemetry_limit"] = MARGIN * average
        telemetry_reference[operation] = {"count": count, "sum_ns": sum_ns,
                                          "average_ns": average}

    # D-12: the since-warm-up trajectory the latency multiplier's slack is
    # measured against, and its end point as the steady reference. The
    # whole-run reference above is unchanged: it still prices the reward's
    # latency TERM, whose run sum is the evaluator's constraint.
    try:
        grid, series, trajectory_runs = since_warmup_trajectory(
            log_paths, args.warmup_seconds, args.trajectory_resolution_seconds)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"cannot build the latency trajectory: {exc}") from exc
    manifest["latency_reference_trajectory"] = {
        # NOT "schema_version": rl_safety_manifest.cc reads every key by its
        # FIRST textual match, and sort_keys puts this block ahead of the
        # top-level "schema_version": 2, so a nested copy made the C++ side
        # read 1 and reject the manifest (D-12 smoke, 2026-09-23).
        "trajectory_schema_version": 1,
        "warmup_seconds": args.warmup_seconds,
        "resolution_seconds": args.trajectory_resolution_seconds,
        "elapsed_seconds": grid,
        "cumulative_avg_ns": series,
        "runs": trajectory_runs,
        "definition": (
            "cumulative average latency per operation over the oracle "
            "calibration windows at or after warmup_seconds of elapsed time "
            "since the first Get window, pooled count-weighted over the runs "
            "as of each grid point (D-12)"),
    }
    for operation in OPS:
        manifest[f"{operation}_latency_avg_ns_telemetry_steady_reference"] = (
            series[operation][-1])

    manifest["guard_calibrated"] = True
    manifest["guard_calibration"] = {
        "latency_trajectory": {"warmup_seconds": args.warmup_seconds,
                               "resolution_seconds": args.trajectory_resolution_seconds,
                               "grid_points": len(grid)},
        "telemetry_reference": {"windows": telemetry_windows,
                                "operations": telemetry_reference},
        "accepted_selection_manifest_sha256": sorted(args.accept_selection_sha256),
        "source_arm": "oracle",
        "selection_manifest_sha256": selection_sha256,
        "repeats": args.repeats,
        "rolling_window_count": rolling,
        "sample_stride_windows": rolling,
        "confidence_target": TOLERANCE_CONFIDENCE,
        "margin": MARGIN,
        "workload_seeds": sorted(workload_seeds),
        "runs": run_metadata,
        "estimators": estimators,
    }
    check_cpp_first_match_keys(manifest)
    atomic_json(args.output, manifest)
    print(f"calibrated manifest: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
