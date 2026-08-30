#!/usr/bin/env python3
"""Calibrate executable live-latency limits from compact oracle telemetry."""

from __future__ import annotations

import argparse
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
MARGIN = 1.02
DEFINITIONS = "trigger-v2-logical-v2"


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
    if len(buckets) != BUCKETS or sum(buckets) != count:
        raise ValueError(f"{path}:{line}: invalid {operation} counters")
    return {"count": count, "sum_ns": sum_ns, "buckets": buckets}


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
    args = parser.parse_args()

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
    for run_dir in run_dirs:
        if not (run_dir / "COMPLETED").exists():
            raise SystemExit(f"incomplete calibration run: {run_dir}")
        metadata = read_env(run_dir / "metadata.env")
        if metadata.get("experiment_fingerprint") != fingerprint:
            raise SystemExit(f"calibration fingerprint mismatch: {run_dir}")
        if (
            metadata.get("arm") != "oracle"
            or metadata.get("rl_run_phase") != "calibration"
            or metadata.get("rl_safety_enforcement") != "0"
            or metadata.get("baseline_slo_sha256") != selection_sha256
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

    manifest["guard_calibrated"] = True
    manifest["guard_calibration"] = {
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
    atomic_json(args.output, manifest)
    print(f"calibrated manifest: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
