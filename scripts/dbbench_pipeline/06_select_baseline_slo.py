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
    GUARD_TARGET_OVERRIDE_FRACTION,
    censored_tolerance_bound,
    frame_simulated_limits,
)


MIN_EPISODES = 299
MARGIN = 1.02
# v3 (2026-09-20): write amplification and stalls cover the measured phase
# only (db_bench resets statistics after the bulk load), sorted-run seeks
# count runs rather than table seeks, and the manifest carries the formal
# objective references (W, S, seeks, stall fraction, avg/p99 latency) the
# learner's constrained reward is trained against. Not poolable with v2.
METRIC_VERSION = "trigger-v2-logical-v3"


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


def collect_episodes(run_dirs: list[Path]) -> list[list[dict]]:
    """One episode list per run; the frame replay needs runs kept apart."""
    runs = []
    for run_dir in run_dirs:
        path = run_dir / "pressure_episodes.jsonl"
        if not path.exists():
            continue
        episodes: list[dict] = []
        runs.append(episodes)
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
    return runs


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
        r"mix([0-9.]+)-([0-9.]+)-([0-9.]+):scan(\d+)-(\d+)"
        r"(?::skew(\d+)-([0-9.]+))?:"
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
        "mix_max_scan_length", "keyrange_num", "value_theta",
        "block_cache_size", "bloom_bits",
        "max_background_jobs", "threads", "disable_wal", "use_direct_io",
        "static_capacity_scales",
        "level_compaction_dynamic_level_bytes",
        "soft_pending_compaction_bytes_limit",
        "hard_pending_compaction_bytes_limit",
        "dbbench_sha256", "research_objective_sha256",
    )
    OPAQUE = ("workload_profile", "static_capacity_scales", "dbbench_sha256",
              "research_objective_sha256")
    # Absent means the run predates the B1 skew knob, which is the uniform
    # family at keyrange_num 1 and a fixed value size.
    SKEW_DEFAULTS = {"keyrange_num": "1", "value_theta": None}
    values: list[object] = list(match.groups())
    for index, name in enumerate(names):
        if name == "static_capacity_scales":
            # Absent means the run predates the knob, which is no expansion.
            values[index] = values[index] or "off"
            continue
        if name in SKEW_DEFAULTS:
            raw = values[index] or SKEW_DEFAULTS[name]
            values[index] = None if raw is None else (
                int(raw) if name == "keyrange_num" else float(raw))
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
    parser.add_argument(
        "--frame-interval-micros", type=int, default=50_000,
        help="controller observation cadence the due-age and pressure limits "
             "are replayed at; must equal RL_OBSERVE_INTERVAL_MS of the runs "
             "the manifest will guard (protocol v2 pins it to 50 ms).")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    graph = load_graph_module(Path(__file__).with_name("04_generate_graphs.py"))
    grouped: dict[str, list[dict]] = defaultdict(list)
    run_dirs: dict[str, list[Path]] = defaultdict(list)
    for completed in args.baseline_results.glob("**/COMPLETED"):
        directory = completed.parent
        # collect_arm reads run.log and rocksdb_LOG.txt in full, so calling it
        # on every arm made one invocation scan the whole sweep and three size
        # ratios scan it three times. metadata.env is a few lines and carries
        # the same identity, so reject non-matching arms before that cost.
        # Absent keys fall through rather than filter, so nothing the previous
        # ordering would have kept is dropped here.
        marker = directory / "metadata.env"
        if marker.exists():
            preview = read_env(marker)
            if (preview.get("arm", "regular") != "regular"
                    or preview.get("size", f"{args.size_millions}M")
                    != f"{args.size_millions}M"
                    or preview.get("size_ratio", str(args.size_ratio))
                    != str(args.size_ratio)
                    or preview.get("workload_profile", args.workload_profile)
                    != args.workload_profile):
                continue
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
    # scan_amplification is withdrawn (P0-1, at its 1.0 floor); the scan
    # tie-break is sorted-run seeks.
    selected = min(runtime_ties, key=lambda item: (
        item["write_amplification"], item["sorted_run_seeks_per_scan"],
        item["fingerprint"]))
    fingerprint = selected["fingerprint"]
    selected_options = parse_fingerprint_options(fingerprint)
    if selected_options["workload_profile"] != args.workload_profile:
        raise SystemExit("selected baseline workload profile mismatch")
    selected_rows = grouped[fingerprint]
    episode_runs = collect_episodes(run_dirs[fingerprint])
    episodes = [episode for run in episode_runs for episode in run]
    by_level: dict[int, list[dict]] = defaultdict(list)
    expected_source_levels = max(1, parse_levels(fingerprint) - 1)
    for episode in episodes:
        level = episode["level"]
        if level >= expected_source_levels:
            raise SystemExit(
                f"episode level {level} is outside the selected geometry")
        by_level[level].append(episode)

    # Due age and pressure are compared against their limits on every
    # observation frame, so their limits are calibrated on the frame fraction
    # E-1 scores, not on the episode distribution (see frame_simulated_limits
    # for why the two differ by an order of magnitude). max_score keeps the
    # episode-maximum tolerance bound: the score trajectory inside an episode
    # is not logged, so it cannot be replayed per frame.
    # The exported floors go in here, not after: k is then chosen against the
    # limits the manifest actually carries, and predicted_override_fraction
    # describes those same limits. MARGIN is deliberately not passed -- the
    # target fraction already specifies how often the guard may fire, and a
    # margin on top is re-absorbed by the search (see frame_simulated_limits).
    frame_limits, frame_meta = frame_simulated_limits(
        episode_runs, expected_source_levels, args.frame_interval_micros,
        GUARD_TARGET_OVERRIDE_FRACTION, floors=(1, 1.0, 1.10))
    if frame_limits is None:
        raise SystemExit("no pressure episodes to replay; cannot calibrate")

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
        # and a later score can exceed the maximum observed so far.
        # censored_tolerance_bound charges every such episode to the tail when
        # choosing its order-statistic rank, so a level stays calibrated
        # through the one truncated record per phase that
        # CompactionPressureObserver::FlushOpenEpisodes is expected to emit,
        # and goes uncalibrated only when censoring is heavy enough to reach
        # the rank the bound needs.
        due_bound, pressure_bound, score_bound = frame_limits[level]
        due_frames = frame_meta["due_frames_per_level"][level]
        frame_level_meta = {
            "method": frame_meta["method"],
            "due_frame_count": due_frames,
            "exceedance_per_level": frame_meta["exceedance_per_level"],
        }
        due_meta = dict(frame_level_meta, bound_micros=due_bound)
        pressure_meta = dict(frame_level_meta, bound_score_micros=pressure_bound)
        score_meta = dict(frame_level_meta, bound_score=score_bound,
                          score_model=frame_meta["score_model"])
        # Every episode-derived quantity here is reported for continuity only.
        # The exported limits come from the frame replay, so a level is
        # calibrated exactly when the replay saw it due at least once.
        calibrated = due_frames > 0
        if calibrated:
            # Already margined and floored by frame_simulated_limits.
            due_limit = int(due_bound)
            pressure_limit = pressure_bound
            score_limit = score_bound
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
                # All three level limits now come from the frame replay, which
                # targets an override fraction directly and applies no margin.
                # TOLERANCE_CONFIDENCE and MARGIN still govern the debt bound
                # and the latency limits, so they are reported where they act,
                # not here where they would misdescribe these three.
                "target_override_fraction": GUARD_TARGET_OVERRIDE_FRACTION,
                "frame_interval_micros": args.frame_interval_micros,
            },
        })

    # Pending debt is also the episode maximum observed so far, hence it has
    # the same censoring contract as the per-level maxima above.
    debt_bound, debt_meta = censored_tolerance_bound(
        completed_debt_values, censored_debt_values)
    refs = {
        metric: finite_mean(selected_rows, metric) for metric in (
            "get_latency_avg_us", "get_latency_p95_us", "get_latency_p99_us",
            "scan_latency_avg_us", "scan_latency_p95_us", "scan_latency_p99_us",
            "write_latency_avg_us", "write_latency_p95_us",
            "write_latency_p99_us",
        )
    }
    physical_reference = finite_mean(selected_rows, "sst_bytes_before")
    # Formal objective references (PATHWAYS Pathway D): the tuned baseline's
    # whole-run constraint metrics, which the learner's hinge terms are
    # measured against at the arm's own T (P1-16). The stall reference is a
    # fraction of measured-phase wall time, the unit the controller observes.
    objective_refs = {
        "write_amplification_reference": finite_mean(
            selected_rows, "write_amplification"),
        "space_amplification_reference": finite_mean(
            selected_rows, "space_amplification"),
        "sorted_run_seeks_per_scan_reference": finite_mean(
            selected_rows, "sorted_run_seeks_per_scan"),
        "stall_fraction_reference": statistics.fmean(
            float(row["stall_seconds"]) / float(row["measured_phase_seconds"])
            for row in selected_rows),
    }
    manifest = {
        "schema_version": 2,
        "metric_definitions_version": METRIC_VERSION,
        "experiment_fingerprint": fingerprint,
        "selection_rule": {
            "space_filter": "mean space amplification <= 1.02 * minimum",
            "primary": "lowest mean runtime",
            "runtime_tie": "within 1%; lower WAF, then lower sorted-run seeks per scan",
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
        # p99 is the acceptance quantile (P0-4); p95 above stays for the guard.
        "get_latency_p99_ns_reference": round(refs["get_latency_p99_us"] * 1000),
        "get_latency_p99_ns_limit": round(MARGIN * refs["get_latency_p99_us"] * 1000),
        "scan_latency_p99_ns_reference": round(refs["scan_latency_p99_us"] * 1000),
        "scan_latency_p99_ns_limit": round(MARGIN * refs["scan_latency_p99_us"] * 1000),
        "write_latency_p99_ns_reference": round(refs["write_latency_p99_us"] * 1000),
        "write_latency_p99_ns_limit": round(MARGIN * refs["write_latency_p99_us"] * 1000),
        **objective_refs,
        "operation_progress_envelopes": {
            key: {"minimum": min(float(row[key]) for row in selected_rows),
                  "maximum": max(float(row[key]) for row in selected_rows)}
            for key in ("get_operations", "put_operations", "scan_operations")
        },
        "metric_definitions": {
            "measured_phase": "statistics reset after the bulk load (db_bench resetstats); byte and stall totals cover mixgraph plus the drain",
            "write_amplification": "(flush bytes + compaction bytes written) / user logical bytes written, measured phase",
            "point_read_amplification": "logical SST probes / point Get",
            "sorted_run_seeks_per_scan": "sorted runs opened per keyed scan seek (each L0 file, each non-empty deeper level once) / scans",
            "scan_amplification": "diagnostic only (P0-1): (returned entries + internal skipped entries) / returned entries",
            "space_amplification": "settled physical SST bytes / garbage-free SST bytes from the reference compaction, settled after the drain (amended 2026-09-21; was rocksdb.estimate-live-data-size)",
            "stall_fraction": "stall seconds / measured-phase wall seconds",
            "rolling_p95": "p95 of the merged 64-bucket log2 histogram over the rolling window",
        },
        "level_limits": level_limits,
        "episode_distributions": distributions,
        # Predicted E-1 statistic on the calibration runs themselves. Read
        # predicted_override_fraction before spending node time on a holdout.
        "guard_frame_simulation": frame_meta,
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
