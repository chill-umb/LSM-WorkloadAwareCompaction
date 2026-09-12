#!/usr/bin/env python3
"""Preregistered tuned-leveled selection and baseline_slo.json generation."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path

from slo_statistics import (
    TOLERANCE_CONFIDENCE,
    censored_tolerance_bound,
)


MIN_EPISODES = 299
MARGIN = 1.02
METRIC_VERSION = "trigger-v2-logical-v2"


def load_graph_module(script: Path):
    spec = importlib.util.spec_from_file_location("dbbench_graphs", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def quantile(values: list[float], probability: float) -> float:
    ordered = sorted(v for v in values if math.isfinite(v))
    if not ordered:
        return math.nan
    position = (len(ordered) - 1) * probability
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def optional_quantile(values: list[float], probability: float):
    return quantile(values, probability) if values else None


def finite_mean(rows: list[dict], key: str) -> float:
    values = [float(row[key]) for row in rows
              if math.isfinite(float(row.get(key, math.nan)))]
    if not values:
        raise SystemExit(f"selected baseline has no finite {key}")
    return statistics.fmean(values)


def read_env(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text(errors="replace").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def collect_episodes(run_dirs: list[Path]) -> list[dict]:
    episodes = []
    for run_dir in run_dirs:
        path = run_dir / "pressure_episodes.jsonl"
        if not path.exists():
            continue
        for line_number, line in enumerate(path.read_text().splitlines(), 1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_number}: {exc}") from exc
            if not isinstance(record, dict):
                raise SystemExit(f"{path}:{line_number}: episode is not an object")
            version = record.get("schema_version")
            if version == 1:
                # Pre-flush logs silently drop every episode still open at a
                # phase boundary, which is exactly the upper tail these limits
                # are estimated from. Calibrating from one would produce limits
                # that look principled and are systematically too tight.
                raise SystemExit(
                    f"{path}:{line_number}: episode schema_version 1 predates "
                    "the open-episode flush; re-run the baseline sweep with a "
                    "rebuilt library before generating a manifest")
            if type(version) is not int or version != 2:
                raise SystemExit(
                    f"{path}:{line_number}: unsupported episode schema {version!r}")
            if type(record.get("level")) is not int or record["level"] < 0:
                raise SystemExit(f"{path}:{line_number}: invalid episode level")
            if type(record.get("truncated")) is not bool:
                raise SystemExit(
                    f"{path}:{line_number}: truncated is not a JSON Boolean")
            for name in (
                "duration_micros", "max_score",
                "integrated_excess_score_micros", "max_pending_debt_ratio",
            ):
                value = record.get(name)
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or value < 0
                ):
                    raise SystemExit(
                        f"{path}:{line_number}: invalid episode {name}")
            episodes.append(record)
    return episodes


def parse_levels(fingerprint: str) -> int:
    match = re.search(r":levels(\d+):", fingerprint)
    if not match:
        raise SystemExit("selected fingerprint has no level count")
    return int(match.group(1))


def parse_fingerprint_options(fingerprint: str) -> dict[str, object]:
    """Recover every experiment option encoded in the canonical identity."""
    pattern = re.compile(
        r"^([A-Za-z0-9_.-]+):(\d+)M:T(\d+):k(\d+):v(\d+):wb(\d+):"
        r"sst(\d+):block(\d+):l1(\d+):levels(\d+):"
        r"l0-(\d+)-(\d+)-(\d+):pri(\d+):load(\d+):"
        r"mix([0-9.]+)-([0-9.]+)-([0-9.]+):scan(\d+)-(\d+):"
        r"cache(\d+):bloom(\d+):bg(\d+):threads(\d+):wal([01]):"
        r"dio([01])(?::cap([0-9.x]+))?:dynamic([01]):soft(\d+):hard(\d+):"
        r"binary([0-9a-f]{64}):objective([0-9a-f]{64})$"
    )
    match = pattern.fullmatch(fingerprint)
    if not match:
        raise SystemExit(f"malformed experiment fingerprint: {fingerprint}")
    names = (
        "workload_profile", "size_millions", "size_ratio", "key_size", "value_size",
        "write_buffer_size", "target_file_size", "block_size",
        "max_bytes_for_level_base", "num_levels",
        "level0_file_num_compaction_trigger",
        "level0_slowdown_writes_trigger", "level0_stop_writes_trigger",
        "compaction_priority", "load_percent", "mix_get_ratio",
        "mix_put_ratio", "mix_seek_ratio", "scan_length",
        "mix_max_scan_length", "block_cache_size", "bloom_bits",
        "max_background_jobs", "threads", "disable_wal", "use_direct_io",
        "static_capacity_scales",
        "level_compaction_dynamic_level_bytes",
        "soft_pending_compaction_bytes_limit",
        "hard_pending_compaction_bytes_limit",
        "dbbench_sha256", "research_objective_sha256",
    )
    OPAQUE = ("workload_profile", "static_capacity_scales", "dbbench_sha256",
              "research_objective_sha256")
    values: list[object] = list(match.groups())
    for index, name in enumerate(names):
        if name == "static_capacity_scales":
            # Absent means the run predates the knob, which is no expansion.
            values[index] = values[index] or "off"
            continue
        if name in OPAQUE:
            continue
        if name in ("mix_get_ratio", "mix_put_ratio", "mix_seek_ratio"):
            values[index] = float(values[index])
        else:
            values[index] = int(values[index])
    return dict(zip(names, values))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-results", required=True, type=Path)
    parser.add_argument("--workload-profile", default="balanced-v1")
    parser.add_argument("--size-millions", required=True, type=int)
    parser.add_argument("--size-ratio", required=True, type=int)
    parser.add_argument("--minimum-repeats", type=int, default=3)
    parser.add_argument(
        "--level-base-bytes", type=int, default=16777216,
        help="consider only configurations at this max_bytes_for_level_base. "
             "The level-base scale axis exists to calibrate the capacity-space "
             "curve; it is not a comparator axis, and letting the selection "
             "range over it would silently redefine the tuned baseline.")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    graph = load_graph_module(Path(__file__).with_name("04_generate_graphs.py"))
    grouped: dict[str, list[dict]] = defaultdict(list)
    run_dirs: dict[str, list[Path]] = defaultdict(list)
    for completed in args.baseline_results.glob("**/COMPLETED"):
        directory = completed.parent
        row = graph.collect_arm(directory)
        if row is None or row["arm"] != "regular":
            continue
        if (int(row["size_millions"]) != args.size_millions or
                int(row["size_ratio"]) != args.size_ratio):
            continue
        metadata = read_env(directory / "metadata.env")
        if metadata.get("workload_profile", "balanced-v1") != args.workload_profile:
            continue
        fingerprint = metadata.get("experiment_fingerprint", "")
        if not fingerprint:
            raise SystemExit(f"missing experiment_fingerprint in {directory}")
        grouped[fingerprint].append(row)
        run_dirs[fingerprint].append(directory)
    if not grouped:
        raise SystemExit("no matching regular baseline arms")
    incomplete = {fingerprint: len(rows) for fingerprint, rows in grouped.items()
                  if len(rows) < args.minimum_repeats}
    if incomplete:
        details = ", ".join(
            f"{fingerprint} ({count})"
            for fingerprint, count in sorted(incomplete.items()))
        raise SystemExit(
            f"baseline grid is incomplete; need {args.minimum_repeats} "
            f"repeats per configuration: {details}")

    summaries = []
    for fingerprint, rows in grouped.items():
        summaries.append({
            "fingerprint": fingerprint,
            "repeats": len(rows),
            "elapsed_seconds": finite_mean(rows, "elapsed_seconds"),
            "space_amplification": finite_mean(rows, "space_amplification"),
            "write_amplification": finite_mean(rows, "write_amplification"),
            "scan_amplification": finite_mean(rows, "scan_amplification"),
            "sorted_run_seeks_per_scan": finite_mean(
                rows, "sorted_run_seeks_per_scan"),
        })
    summaries = [item for item in summaries
                 if parse_fingerprint_options(item["fingerprint"])
                 ["max_bytes_for_level_base"] == args.level_base_bytes]
    if not summaries:
        raise SystemExit(
            f"no configuration at max_bytes_for_level_base="
            f"{args.level_base_bytes}; the comparator grid is missing")
    min_space = min(item["space_amplification"] for item in summaries)
    eligible = [item for item in summaries
                if item["space_amplification"] <= MARGIN * min_space]
    fastest = min(item["elapsed_seconds"] for item in eligible)
    runtime_ties = [item for item in eligible
                    if item["elapsed_seconds"] <= 1.01 * fastest]
    selected = min(runtime_ties, key=lambda item: (
        item["write_amplification"], item["scan_amplification"],
        item["sorted_run_seeks_per_scan"], item["fingerprint"]))
    fingerprint = selected["fingerprint"]
    selected_options = parse_fingerprint_options(fingerprint)
    if selected_options["workload_profile"] != args.workload_profile:
        raise SystemExit("selected baseline workload profile mismatch")
    selected_rows = grouped[fingerprint]
    episodes = collect_episodes(run_dirs[fingerprint])
    by_level: dict[int, list[dict]] = defaultdict(list)
    expected_source_levels = max(1, parse_levels(fingerprint) - 1)
    for episode in episodes:
        level = episode["level"]
        if level >= expected_source_levels:
            raise SystemExit(
                f"episode level {level} is outside the selected geometry")
        by_level[level].append(episode)

    level_limits = []
    distributions = []
    completed_debt_values = []
    censored_debt_values = []
    for level in range(expected_source_levels):
        records = by_level[level]
        complete = [item for item in records if not item.get("truncated")]
        censored = [item for item in records if item.get("truncated")]
        durations = [float(item["duration_micros"]) for item in records]
        scores = [float(item["max_score"]) for item in records]
        debts = [float(item["max_pending_debt_ratio"]) for item in records]
        # Diagnostic quantiles below span every record, censored included, and
        # are reported for continuity with earlier manifests. They are NOT the
        # exported limits -- those come from the censoring-aware bounds.
        pressures = [float(item["integrated_excess_score_micros"])
                     for item in records]
        completed_debt_values.extend(
            float(item["max_pending_debt_ratio"]) for item in complete)
        censored_debt_values.extend(
            float(item["max_pending_debt_ratio"]) for item in censored)
        # All three episode maxima are lower bounds when an episode is
        # truncated: duration and integrated pressure can keep accumulating,
        # and a later score can exceed the maximum observed so far. No
        # distribution-free upper tolerance bound follows from such a lower
        # bound, so censored_tolerance_bound marks that level uncalibrated.
        due_bound, due_meta = censored_tolerance_bound(
            [float(item["duration_micros"]) for item in complete],
            [float(item["duration_micros"]) for item in censored])
        pressure_bound, pressure_meta = censored_tolerance_bound(
            [float(item["integrated_excess_score_micros"]) for item in complete],
            [float(item["integrated_excess_score_micros"]) for item in censored])
        score_bound, score_meta = censored_tolerance_bound(
            [float(item["max_score"]) for item in complete],
            [float(item["max_score"]) for item in censored])
        # A level is calibrated only if every limit it exports rests on a real
        # tolerance bound. Mixing one estimated limit with two bootstrap caps
        # and labelling the level "calibrated" is the failure mode the plan
        # warns about, so the weakest of the three decides.
        calibrated = None not in (due_bound, pressure_bound, score_bound)
        if calibrated:
            due_limit = max(1, round(MARGIN * due_bound))
            pressure_limit = max(1.0, MARGIN * pressure_bound)
            score_limit = max(1.10, MARGIN * score_bound)
        elif level == 0:
            due_limit, pressure_limit, score_limit = 1, 1.0, 1.0
        else:
            due_limit, pressure_limit, score_limit = 1_000_000, 250_000.0, 1.25
        level_limits.append({
            "level": level,
            "calibrated": calibrated,
            "episode_count": len(records),
            "due_age_limit_micros": due_limit,
            "pressure_limit_score_micros": pressure_limit,
            "score_limit": score_limit,
        })
        distributions.append({
            "level": level,
            "episode_count": len(records),
            "completed_episode_count": len(complete),
            "censored_episode_count": len(censored),
            "duration_micros_q50": optional_quantile(durations, 0.50),
            # Reported for continuity with earlier manifests. These are
            # interpolated sample quantiles, NOT the exported limits: the
            # limits come from the tolerance bounds recorded below, which state
            # the coverage and confidence the sample actually supports.
            "duration_micros_q99": optional_quantile(durations, 0.99),
            "max_score_q99": optional_quantile(scores, 0.99),
            "integrated_pressure_q99": optional_quantile(pressures, 0.99),
            "pending_debt_ratio_q99": optional_quantile(debts, 0.99),
            # Kept out of `level_limits` deliberately: the C++ manifest reader
            # is a substring scanner over each level object, so nested metadata
            # there would couple correctness to key ordering.
            "limit_estimators": {
                "due_age": due_meta,
                "integrated_pressure": pressure_meta,
                "max_score": score_meta,
                "uncalibrated_bootstrap": not calibrated,
                "confidence_target": TOLERANCE_CONFIDENCE,
                "margin": MARGIN,
            },
        })

    # Pending debt is also the episode maximum observed so far, hence it has
    # the same censoring contract as the per-level maxima above.
    debt_bound, debt_meta = censored_tolerance_bound(
        completed_debt_values, censored_debt_values)
    refs = {
        metric: finite_mean(selected_rows, metric) for metric in (
            "get_latency_avg_us", "get_latency_p95_us",
            "scan_latency_avg_us", "scan_latency_p95_us",
            "write_latency_avg_us", "write_latency_p95_us",
        )
    }
    physical_reference = finite_mean(selected_rows, "sst_bytes_before")
    manifest = {
        "schema_version": 2,
        "metric_definitions_version": METRIC_VERSION,
        "experiment_fingerprint": fingerprint,
        "selection_rule": {
            "space_filter": "mean space amplification <= 1.02 * minimum",
            "primary": "lowest mean runtime",
            "runtime_tie": "within 1%; lower WAF, then lower scan amplification",
            "inspected_rl_results": False,
        },
        # These are executable options, not merely a prose record. The final
        # paired runner loads the independently tuned trigger/priority values
        # for both arms and then reconstructs this fingerprint before launch.
        "selected_baseline_options": {
            **selected_options,
            "fingerprint": fingerprint,
        },
        # Phase one deliberately contains no executable latency guard. The
        # guard calibrator copies this immutable selection/objective manifest,
        # adds statistically supported guard_* limits, and flips this flag.
        "guard_calibrated": False,
        "guard_minimum_samples": MIN_EPISODES,
        "guard_rolling_window_count": 20,
        "guard_hysteresis_enter_windows": 3,
        "guard_hysteresis_exit_windows": 3,
        "guard_p95_method": "merged_log2_histogram",
        "expected_physical_sst_bytes": round(physical_reference),
        "allowed_physical_sst_bytes": round(MARGIN * physical_reference),
        "allowed_pending_debt_ratio": (
            max(0.50, MARGIN * debt_bound) if debt_bound is not None else 0.50),
        "allowed_pending_debt_ratio_estimator": debt_meta,
        "get_latency_avg_ns_reference": refs["get_latency_avg_us"] * 1000,
        "get_latency_avg_ns_limit": MARGIN * refs["get_latency_avg_us"] * 1000,
        "get_latency_p95_ns_reference": round(refs["get_latency_p95_us"] * 1000),
        "get_latency_p95_ns_limit": round(MARGIN * refs["get_latency_p95_us"] * 1000),
        "scan_latency_avg_ns_reference": refs["scan_latency_avg_us"] * 1000,
        "scan_latency_avg_ns_limit": MARGIN * refs["scan_latency_avg_us"] * 1000,
        "scan_latency_p95_ns_reference": round(refs["scan_latency_p95_us"] * 1000),
        "scan_latency_p95_ns_limit": round(MARGIN * refs["scan_latency_p95_us"] * 1000),
        "write_latency_avg_ns_reference": refs["write_latency_avg_us"] * 1000,
        "write_latency_avg_ns_limit": MARGIN * refs["write_latency_avg_us"] * 1000,
        "write_latency_p95_ns_reference": round(refs["write_latency_p95_us"] * 1000),
        "write_latency_p95_ns_limit": round(MARGIN * refs["write_latency_p95_us"] * 1000),
        "operation_progress_envelopes": {
            key: {"minimum": min(float(row[key]) for row in selected_rows),
                  "maximum": max(float(row[key]) for row in selected_rows)}
            for key in ("get_operations", "put_operations", "scan_operations")
        },
        "metric_definitions": {
            "write_amplification": "(flush bytes + compaction bytes written) / user logical bytes written",
            "point_read_amplification": "logical SST probes / point Get",
            "scan_amplification": "(returned entries + internal skipped entries) / returned entries",
            "space_amplification": "physical SST bytes / live logical bytes",
            "rolling_p95": "p95 of the merged 64-bucket log2 histogram over the rolling window",
        },
        "level_limits": level_limits,
        "episode_distributions": distributions,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    report = args.output.with_name("baseline_frontier.csv")
    with report.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(sorted(summaries, key=lambda item: item["fingerprint"]))
    print(f"selected: {fingerprint}")
    print(f"manifest: {args.output}")
    print(f"frontier: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
