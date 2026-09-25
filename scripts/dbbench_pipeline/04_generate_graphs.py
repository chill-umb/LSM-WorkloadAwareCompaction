#!/usr/bin/env python3
"""Parse completed db_bench arms, write summary.csv, and generate PNG graphs."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
from pathlib import Path
from typing import Iterable, Optional



NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(errors="replace").splitlines():
        if "=" in raw:
            key, value = raw.split("=", 1)
            values[key] = value
    return values


def number(value: object, default: float = math.nan) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def divide(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0 else math.nan


def amplification_metrics(*, flush_bytes: float, compact_bytes: float,
                          user_write_bytes: float, point_probes: float,
                          gets: float, scan_returned: float,
                          scan_skips: float, sorted_run_seeks: float,
                          scans: float, physical_sst_bytes: float,
                          live_logical_bytes: float,
                          garbage_free_sst_bytes: float = math.nan
                          ) -> dict[str, float]:
    """Formal metric definitions shared by collection and unit tests."""
    # Space amplification divides settled SST bytes by the garbage-free size
    # the reference compaction measures, not by estimate-live-data-size
    # (contract amendment 2026-09-21, PREREGISTRATION D-3).
    # VersionStorageInfo::EstimateLiveDataSize (db/version_set.cc:5401) sums a
    # maximal set of files with no range overlap in a deeper level, so a file
    # holding live data that shadows the bottom level is dropped whole. Its own
    # comment says "the less compacted, the more optimistic (smaller) this
    # estimate is": measured across the 108 Assoc Hull-0 runs it discards 46%
    # of the live bytes at T=2 against 8% at T=10, while the garbage-free size
    # is constant to 0.006%. Both denominators are physical SST bytes, so the
    # units are unchanged and only the estimate is replaced. The old value
    # stays as space_amplification_estimate, and is the fallback for an arm
    # whose sizes.env never reached the archive.
    settled_denominator = (garbage_free_sst_bytes
                           if garbage_free_sst_bytes > 0
                           else live_logical_bytes)
    return {
        "write_amplification": divide(
            flush_bytes + compact_bytes, user_write_bytes),
        "point_read_amplification": divide(point_probes, gets),
        "scan_amplification": divide(
            scan_returned + scan_skips, scan_returned),
        "sorted_run_seeks_per_scan": divide(sorted_run_seeks, scans),
        "space_amplification": divide(
            physical_sst_bytes, settled_denominator),
        "space_amplification_estimate": divide(
            physical_sst_bytes, live_logical_bytes),
    }


def parse_tickers(text: str) -> dict[str, float]:
    result: dict[str, float] = {}
    pattern = re.compile(rf"^(rocksdb\.[\w.\-_]+) COUNT : ({NUMBER})", re.M)
    for name, value in pattern.findall(text):
        result[name] = float(value)
    return result


def parse_properties(text: str) -> dict[str, float]:
    result: dict[str, float] = {}
    pattern = re.compile(rf"^(rocksdb\.[\w.\-_]+):\s+({NUMBER})\s*$", re.M)
    for name, value in pattern.findall(text):
        result[name] = float(value)
    return result


def parse_histograms(text: str) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    pattern = re.compile(
        rf"^(rocksdb\.[\w.\-_]+) P50 : ({NUMBER}) P95 : ({NUMBER}) "
        rf"P99 : ({NUMBER}) P100 : ({NUMBER}) COUNT : ({NUMBER}) SUM : ({NUMBER})",
        re.M,
    )
    for match in pattern.finditer(text):
        count = float(match.group(6))
        total = float(match.group(7))
        result[match.group(1)] = {
            "p50": float(match.group(2)),
            "p95": float(match.group(3)),
            "p99": float(match.group(4)),
            "p100": float(match.group(5)),
            "count": count,
            "sum": total,
            "avg": divide(total, count),
        }
        quantiles = [float(match.group(i)) for i in range(2, 6)]
        if (any(not math.isfinite(v) or v < 0
                for v in [*quantiles, count, total]) or
                quantiles != sorted(quantiles) or
                count != int(count) or (count == 0 and total != 0)):
            raise ValueError(f"invalid histogram: {match.group(1)}")
    return result


def parse_mix_counts(text: str) -> tuple[float, float, float, float, float]:
    matches = list(
        re.finditer(
            rf"Gets:(\d+) Puts:(\d+) Seek:(\d+)(?: ScanEntries:(\d+))?"
            rf".*?avg size: ({NUMBER}) value, ({NUMBER}) scan",
            text,
        )
    )
    if not matches:
        return math.nan, math.nan, math.nan, math.nan, math.nan
    match = matches[-1]
    gets, puts, scans = (float(match.group(index)) for index in (1, 2, 3))
    average_scan_length = float(match.group(6))
    returned = (float(match.group(4)) if match.group(4) is not None
                else scans * average_scan_length)
    return gets, puts, scans, returned, average_scan_length


EVENT_LOG = re.compile(r"EVENT_LOG_v1 (\{.*\})")


# D-11 (2026-09-23): db_bench's `resetstats` calls DB::ResetStats, which clears
# RocksDB's INTERNAL stats only. The `Statistics` tickers and histograms the
# `stats` benchmark prints are cumulative since open and therefore include the
# bulk load, contrary to what P1c-19 recorded. The measured phase is recovered
# from what the run does print per phase: db_bench's own per-benchmark latency
# histograms after the mixgraph line, the internal-stats block's cumulative
# stall (reset), the RocksDB event log's flush and compaction outputs inside
# [RL_CONTROL_RESUMED_MICROS, RL_DRAIN_END_MICROS], and the load's user bytes,
# which are exact: fixed key and value sizes plus the 16-byte WriteBatch
# framing of one Put (12-byte header, type byte, two varint lengths).
WRITE_BATCH_FRAMING_BYTES = 16.0
STALL_LINE = re.compile(
    r"^Cumulative stall: (\d+):(\d+):(\d+(?:\.\d+)?) H:M:S", re.M)
PHASE_HIST_HEADER = re.compile(r"^Microseconds per (read|write|seek):\s*$", re.M)
BENCH_RESULT_LINE = re.compile(r"^([a-z_]+)\s+:\s+[\d.]+ micros/op", re.M)
# The first bucket prints with an inclusive "[" lower bound, the rest with "(".
HIST_BUCKET = re.compile(
    r"^[\(\[]\s*(-?\d+),\s*(-?\d+)\s*\]\s+(\d+)\s+[\d.]+%\s+[\d.]+%")


def parse_measured_stall_seconds(text: str) -> float:
    """Measured-phase stall from the internal-stats block, which resetstats
    does reset; the last occurrence is the `stats` dump after the drain."""
    matches = STALL_LINE.findall(text)
    if not matches:
        return math.nan
    hours, minutes, seconds = matches[-1]
    return int(hours) * 3600.0 + int(minutes) * 60.0 + float(seconds)


def _bucket_percentile(buckets: list[tuple[float, float, int]],
                       probability: float) -> float:
    """Linear interpolation inside the bucket, as HistogramImpl::Percentile."""
    total = sum(count for _, _, count in buckets)
    if total <= 0:
        return math.nan
    target = probability * total
    seen = 0
    for low, high, count in buckets:
        if seen + count >= target:
            if count <= 0:
                return float(high)
            return low + (high - low) * (target - seen) / count
        seen += count
    return float(buckets[-1][1])


def parse_phase_histograms(text: str) -> dict[str, dict[str, float]]:
    """db_bench's per-benchmark latency histograms for the mixgraph phase:
    the `Microseconds per read/write/seek` blocks that follow the mixgraph
    result line. These cover the measured phase only, unlike the
    rocksdb.db.*.micros statistics, and `seek` times the whole scan rather
    than the Seek call alone. p95 is not printed and is interpolated from the
    bucket rows the same way db_bench derives its printed percentiles."""
    result: dict[str, dict[str, float]] = {}
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        match = BENCH_RESULT_LINE.match(line)
        if match and match.group(1) == "mixgraph":
            start = index
    if start is None:
        return result
    names = {"read": "get", "write": "write", "seek": "scan"}
    index = start + 1
    while index < len(lines):
        line = lines[index]
        if BENCH_RESULT_LINE.match(line):
            break
        header = PHASE_HIST_HEADER.match(line)
        if not header:
            index += 1
            continue
        block = {"count": math.nan, "avg": math.nan, "p50": math.nan,
                 "p95": math.nan, "p99": math.nan, "p100": math.nan}
        buckets: list[tuple[float, float, int]] = []
        index += 1
        while index < len(lines) and not PHASE_HIST_HEADER.match(lines[index]) \
                and not BENCH_RESULT_LINE.match(lines[index]):
            item = lines[index]
            count_match = re.match(r"^Count: (\d+) Average: ([\d.]+)", item)
            minmax = re.match(r"^Min: ([\d.]+)\s+Median: ([\d.]+)\s+Max: ([\d.]+)", item)
            pct = re.match(r"^Percentiles: P50: ([\d.]+) P75: ([\d.]+) P99: ([\d.]+)", item)
            bucket = HIST_BUCKET.match(item)
            if count_match:
                block["count"] = float(count_match.group(1))
                block["avg"] = float(count_match.group(2))
            elif minmax:
                block["p50"] = float(minmax.group(2))
                block["p100"] = float(minmax.group(3))
            elif pct:
                block["p50"] = float(pct.group(1))
                block["p99"] = float(pct.group(3))
            elif bucket:
                buckets.append((float(bucket.group(1)), float(bucket.group(2)),
                                int(bucket.group(3))))
            elif item.strip() and not item.startswith("-"):
                break
            index += 1
        if buckets:
            block["p95"] = _bucket_percentile(buckets, 0.95)
        block["sum"] = block["avg"] * block["count"]
        result[names[header.group(1)]] = block
    return result


def parse_geometry(fingerprint: str) -> tuple[float, float]:
    """Key and value size from the fingerprint's `:k64:v960:` segment."""
    match = re.search(r":k(\d+):v(\d+):", fingerprint or "")
    if not match:
        return math.nan, math.nan
    return float(match.group(1)), float(match.group(2))


def parse_drain(text: str, log_path: Path) -> dict[str, float]:
    starts = [int(value) for value in re.findall(
        r"^RL_DRAIN_START_MICROS (\d+)$", text, re.M)]
    ends = [int(value) for value in re.findall(
        r"^RL_DRAIN_END_MICROS (\d+)$", text, re.M)]
    before = [int(value) for value in re.findall(
        r"^RL_DRAIN_DB_PENDING_BEFORE_BYTES (\d+)$", text, re.M)]
    after = [int(value) for value in re.findall(
        r"^RL_DRAIN_DB_PENDING_AFTER_BYTES (\d+)$", text, re.M)]
    drain_seconds = (max(0, ends[-1] - starts[0]) / 1e6
                     if starts and ends else math.nan)
    # Measured phase: from the controller's resume after the bulk load
    # (db_bench `rlresume`, printed for every arm) to the end of the drain.
    # It is the wall-time denominator of the stall fraction the learner's
    # stall hinge is trained against, so it must match the phase the reset
    # statistics cover.
    resumed = [int(value) for value in re.findall(
        r"^RL_CONTROL_RESUMED_MICROS (\d+)$", text, re.M)]
    measured_phase_seconds = (max(0, ends[-1] - resumed[-1]) / 1e6
                              if resumed and ends else math.nan)
    drain_compaction_bytes = 0.0
    workload_compaction_bytes = 0.0
    drain_compaction_seconds = 0.0
    workload_compaction_seconds = 0.0
    measured_flush_bytes = math.nan
    load_flush_bytes = math.nan
    if log_path.exists():
        events = []
        # rocksdb_LOG.txt runs to hundreds of MB per arm: two EVENT_LOG_v1
        # records per compaction plus a full stats block every
        # stats_dump_period_sec. Reading it whole and running a regex on every
        # line made graph generation dominate the pipeline. Stream the file and
        # reject non-event lines with a substring test, which is a C-level
        # search rather than the regex engine, before matching.
        with log_path.open(errors="replace") as handle:
            for raw in handle:
                if "EVENT_LOG_v1" not in raw:
                    continue
                match = EVENT_LOG.search(raw)
                if not match:
                    continue
                try:
                    events.append(json.loads(match.group(1)))
                except json.JSONDecodeError:
                    pass
        drain_jobs = {int(item["job"]) for item in events if "job" in item
                      if item.get("event") == "compaction_started" and
                      item.get("rl_drain") in (True, 1, "true", "1")}
        # D-11: flush bytes by phase. A flush's SST is its table_file_creation
        # event, joined on the job id of a flush_started event; the phase is
        # the event time against the resume and drain-end stamps db_bench
        # prints. Flushes outside the window (the load; the manual flush before
        # the reference compaction, after the stats dump) are not measured.
        flush_jobs = {int(item["job"]) for item in events if "job" in item
                      if item.get("event") == "flush_started"}
        if resumed and ends:
            measured_flush_bytes = 0.0
            load_flush_bytes = 0.0
            for item in events:
                if item.get("event") != "table_file_creation" or "job" not in item:
                    continue
                if int(item["job"]) not in flush_jobs:
                    continue
                when = int(item.get("time_micros", 0))
                size = float(item.get("file_size", 0))
                if when < resumed[-1]:
                    load_flush_bytes += size
                elif when <= ends[-1]:
                    measured_flush_bytes += size
        for item in events:
            if item.get("event") != "compaction_finished" or "job" not in item:
                continue
            output = float(item.get("total_output_size", 0))
            seconds = float(item.get("compaction_time_micros", 0)) / 1e6
            # New logs stamp the phase when the job completes. The start-side
            # fallback keeps phase parsing useful for older repaired logs.
            finished_in_drain = item.get("rl_drain") in (
                True, 1, "true", "1")
            if finished_in_drain or int(item["job"]) in drain_jobs:
                drain_compaction_bytes += output
                drain_compaction_seconds += seconds
            elif item.get("rl_suspended") in (True, 1, "true", "1"):
                # Bulk-load jobs under suspended control: outside the
                # measured phase, like the reset tickers.
                continue
            else:
                workload_compaction_bytes += output
                workload_compaction_seconds += seconds
    return {
        "drain_seconds": drain_seconds,
        "measured_phase_seconds": measured_phase_seconds,
        "drain_pending_bytes_before": float(sum(before)) if before else math.nan,
        "drain_pending_bytes_after": float(sum(after)) if after else math.nan,
        "drain_compaction_write_bytes": drain_compaction_bytes,
        "workload_compaction_write_bytes": workload_compaction_bytes,
        "drain_compaction_seconds": drain_compaction_seconds,
        "workload_compaction_seconds": workload_compaction_seconds,
        "measured_flush_write_bytes": measured_flush_bytes,
        "load_flush_write_bytes": load_flush_bytes,
    }


def collect_arm(run_dir: Path) -> Optional[dict[str, object]]:
    if not (run_dir / "COMPLETED").exists() or not (run_dir / "run.log").exists():
        return None
    metadata = read_env(run_dir / "metadata.env")
    sizes = read_env(run_dir / "sizes.env")
    text = (run_dir / "run.log").read_text(errors="replace")
    tickers = parse_tickers(text)
    properties = parse_properties(text)
    histograms = parse_histograms(text)
    gets, puts, scans, scan_returned, average_scan_length = parse_mix_counts(text)
    drain = parse_drain(text, run_dir / "rocksdb_LOG.txt")

    # Whole-run tickers (cumulative since open; they include the bulk load).
    flush_bytes = tickers.get("rocksdb.flush.write.bytes", 0.0)
    compact_bytes = tickers.get("rocksdb.compact.write.bytes", 0.0)
    user_write_bytes = tickers.get("rocksdb.bytes.written", 0.0)
    # D-11: the measured phase. Physical bytes from the event log inside the
    # resume..drain-end window; user bytes as the ticker less the load's exact
    # bytes (fixed record size plus WriteBatch framing).
    key_size, value_size = parse_geometry(metadata.get("experiment_fingerprint", ""))
    load_operations = number(metadata.get("load_operations"))
    load_user_write_bytes = (
        load_operations * (key_size + value_size + WRITE_BATCH_FRAMING_BYTES)
        if all(math.isfinite(v) for v in (load_operations, key_size, value_size))
        else math.nan)
    measured_user_write_bytes = (
        user_write_bytes - load_user_write_bytes
        if math.isfinite(load_user_write_bytes) else math.nan)
    measured_physical_write_bytes = (
        drain["measured_flush_write_bytes"]
        + drain["workload_compaction_write_bytes"]
        + drain["drain_compaction_write_bytes"])
    measured_write_ok = (math.isfinite(measured_physical_write_bytes)
                         and measured_physical_write_bytes > 0
                         and math.isfinite(measured_user_write_bytes)
                         and measured_user_write_bytes > 0)
    write_amplification_whole_run = divide(flush_bytes + compact_bytes,
                                           user_write_bytes)
    if measured_write_ok:
        write_flush_bytes = drain["measured_flush_write_bytes"]
        write_compact_bytes = (drain["workload_compaction_write_bytes"]
                               + drain["drain_compaction_write_bytes"])
        write_user_bytes = measured_user_write_bytes
        write_source = "measured_phase_event_log"
    else:
        write_flush_bytes, write_compact_bytes = flush_bytes, compact_bytes
        write_user_bytes = user_write_bytes
        write_source = "whole_run_tickers_fallback"
    point_probes = tickers.get("rocksdb.point.sst.probe", 0.0)
    scan_skips = tickers.get("rocksdb.number.iter.skip", 0.0)
    sorted_run_seeks = tickers.get("rocksdb.sorted.run.seek", 0.0)
    before = number(sizes.get("sst_bytes_before_full_compaction"))
    after = number(sizes.get("sst_bytes_after_full_compaction"))
    live_logical_bytes = properties.get(
        "rocksdb.estimate-live-data-size", math.nan)
    if not math.isfinite(before):
        # sizes.env is written after the measured phase, so it is the one
        # artifact an interrupted or partially archived arm can lack. The
        # settled SST total is already in run.log as a property, and it is the
        # same number: checked against the node-computed value on all 36
        # Gate-1 configurations, relative error 0. The full-compaction figure
        # has no such fallback, and since 2026-09-21 it is the space
        # denominator, so such an arm falls back to the estimate and reports
        # the weaker number under both keys rather than dropping out of the
        # hull on a non-finite S.
        before = properties.get("rocksdb.total-sst-files-size", math.nan)
    amplification = amplification_metrics(
        flush_bytes=write_flush_bytes, compact_bytes=write_compact_bytes,
        user_write_bytes=write_user_bytes, point_probes=point_probes,
        gets=gets, scan_returned=scan_returned, scan_skips=scan_skips,
        sorted_run_seeks=sorted_run_seeks, scans=scans,
        physical_sst_bytes=before, live_logical_bytes=live_logical_bytes,
        garbage_free_sst_bytes=after)

    # D-11: latency from db_bench's own mixgraph histograms (measured phase;
    # `seek` times the whole scan). The rocksdb.db.*.micros statistics are
    # whole-run for writes and time only the Seek call for scans; they remain
    # available as *_internal_* diagnostics.
    phase_histograms = parse_phase_histograms(text)
    internal_get = histograms.get("rocksdb.db.get.micros", {})
    internal_scan = histograms.get("rocksdb.db.seek.micros", {})
    internal_write = histograms.get("rocksdb.db.write.micros", {})
    if phase_histograms:
        get_latency = phase_histograms.get("get", {})
        scan_latency = phase_histograms.get("scan", {})
        write_latency = phase_histograms.get("write", {})
        latency_source = "db_bench_mixgraph_histograms"
    else:
        get_latency, scan_latency, write_latency = (
            internal_get, internal_scan, internal_write)
        latency_source = "rocksdb_statistics_histograms_fallback"
    write_stall = histograms.get("rocksdb.db.write.stall", {})
    measured_stall_seconds = parse_measured_stall_seconds(text)
    whole_run_stall_seconds = tickers.get("rocksdb.stall.micros", 0.0) / 1e6
    size_label = metadata.get("size", run_dir.parents[1].name)
    size_m = int(str(size_label).rstrip("Mm"))
    ratio = int(metadata.get("size_ratio", run_dir.parent.name.lstrip("T")))

    return {
        "workload_profile": metadata.get("workload_profile", "balanced-v1"),
        "size_millions": size_m,
        "size_ratio": ratio,
        "arm": metadata.get("arm", run_dir.name),
        "repeat": int(metadata.get("repeat", "1")),
        "dbbench_seed": int(metadata.get("dbbench_seed", "0")),
        "policy_seed": metadata.get("policy_seed", "null"),
        "experiment_fingerprint": metadata.get("experiment_fingerprint", ""),
        "baseline_slo_sha256": metadata.get("baseline_slo_sha256", ""),
        "research_objective_sha256": metadata.get("research_objective_sha256", ""),
        "space_relative_margin": metadata.get("space_relative_margin", ""),
        "elapsed_seconds": number(metadata.get("elapsed_seconds")),
        **amplification,
        "write_amplification_whole_run": write_amplification_whole_run,
        "write_amplification_source": write_source,
        "measured_physical_write_bytes": measured_physical_write_bytes,
        "measured_user_write_bytes": measured_user_write_bytes,
        "load_user_write_bytes": load_user_write_bytes,
        "stall_seconds": (measured_stall_seconds
                          if math.isfinite(measured_stall_seconds)
                          else whole_run_stall_seconds),
        "stall_seconds_source": ("measured_phase_internal_stats"
                                 if math.isfinite(measured_stall_seconds)
                                 else "whole_run_ticker_fallback"),
        "stall_seconds_whole_run": whole_run_stall_seconds,
        "stall_events": write_stall.get("count", 0.0),
        "latency_source": latency_source,
        "get_latency_internal_avg_us": internal_get.get("avg", math.nan),
        "scan_latency_internal_avg_us": internal_scan.get("avg", math.nan),
        "write_latency_internal_avg_us": internal_write.get("avg", math.nan),
        **drain,
        "get_latency_avg_us": get_latency.get("avg", math.nan),
        "get_latency_p95_us": get_latency.get("p95", math.nan),
        "get_latency_p99_us": get_latency.get("p99", math.nan),
        "scan_latency_avg_us": scan_latency.get("avg", math.nan),
        "scan_latency_p95_us": scan_latency.get("p95", math.nan),
        "scan_latency_p99_us": scan_latency.get("p99", math.nan),
        "write_latency_avg_us": write_latency.get("avg", math.nan),
        "write_latency_p95_us": write_latency.get("p95", math.nan),
        "write_latency_p99_us": write_latency.get("p99", math.nan),
        **{f"{operation}_latency_{field}" +
           ("_us" if field in ("p50", "p100", "sum") else ""):
           histogram.get(field, math.nan)
           for operation, histogram in (("get", get_latency),
                                        ("scan", scan_latency),
                                        ("write", write_latency))
           for field in ("p50", "p100", "count", "sum")},
        "write_p95_below_mean": (
            write_latency.get("p95", math.nan) <
            write_latency.get("avg", math.nan)),
        "write_stall_histogram_sum_us": write_stall.get("sum", math.nan),
        "get_operations": gets,
        "put_operations": puts,
        "scan_operations": scans,
        "average_scan_length": average_scan_length,
        "flush_write_bytes": flush_bytes,
        "compaction_write_bytes": compact_bytes,
        "user_write_bytes": user_write_bytes,
        "point_sst_probes": point_probes,
        "scan_internal_skips": scan_skips,
        "scan_returned_entries": scan_returned,
        "sst_bytes_before": before,
        "sst_bytes_after": after,
        "live_logical_bytes": live_logical_bytes,
        "result_directory": str(run_dir),
    }


def collect(results: Path) -> list[dict[str, object]]:
    rows = []
    # The leading **/ lets one results root cover a whole sweep, whose arms sit
    # under a per-configuration directory (<root>/<config_id>/10M/T2/...). It
    # still matches a single arm root, where ** contracts to nothing.
    for completed in results.glob("**/*M/T*/**/COMPLETED"):
        row = collect_arm(completed.parent)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda row: (str(row["workload_profile"]),
                               int(row["size_ratio"]), str(row["arm"]),
                               int(row["size_millions"]), int(row["repeat"])))
    return rows


def finite(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def plot_metric(ax: plt.Axes, rows: list[dict[str, object]], metric: str,
                title: str, ylabel: str) -> None:
    colors = {2: "#0072B2", 6: "#E69F00", 10: "#009E73"}
    markers = {"regular": "o", "oracle": "^", "prior_only": "D",
               "rl": "s", "unconstrained_rl": "v"}
    styles = {"regular": "-", "oracle": ":", "prior_only": "-.",
              "rl": "--", "unconstrained_rl": ":"}
    keys = sorted({(int(row["size_ratio"]), str(row["arm"])) for row in rows})
    for ratio, arm in keys:
        selected = [row for row in rows
                    if int(row["size_ratio"]) == ratio and row["arm"] == arm
                    and finite(row.get(metric))]
        if not selected:
            continue
        samples: dict[int, list[float]] = {}
        for row in selected:
            samples.setdefault(int(row["size_millions"]), []).append(
                float(row[metric]))
        x_values = sorted(samples)
        means = [statistics.fmean(samples[x]) for x in x_values]
        # Student-t 95% half-widths for the repeat counts used by the final
        # protocol. A single repeat is plotted without an error bar.
        t95 = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571,
               7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262,
               11: 2.228, 12: 2.201, 13: 2.179, 14: 2.160,
               15: 2.145, 16: 2.131, 17: 2.120, 18: 2.110,
               19: 2.101, 20: 2.093, 21: 2.086, 22: 2.080,
               23: 2.074, 24: 2.069, 25: 2.064, 26: 2.060,
               27: 2.056, 28: 2.052, 29: 2.048, 30: 2.045,
               31: 2.042}
        errors = []
        for x in x_values:
            values = samples[x]
            if len(values) < 2:
                errors.append(0.0)
            else:
                critical = t95.get(len(values), 1.96)
                errors.append(
                    critical * statistics.stdev(values) / math.sqrt(len(values)))
        ax.errorbar(
            x_values,
            means,
            yerr=errors,
            label=f"T={ratio} {arm}",
            color=colors.get(ratio),
            marker=markers.get(arm, "o"),
            linestyle=styles.get(arm, "-"),
            linewidth=1.8,
            capsize=3,
        )
    ax.set_title(title)
    ax.set_xlabel("Workload operations (millions)")
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.25)


def finish_figure(fig: plt.Figure, axes: Iterable[plt.Axes], path: Path) -> None:
    import matplotlib.pyplot as plt
    handles, labels = next(iter(axes)).get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="upper center", ncol=3,
                   bbox_to_anchor=(0.5, 1.02))
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def graph_grid(rows: list[dict[str, object]], output: Path,
               specifications: list[tuple[str, str, str]], filename: str,
               dimensions: tuple[int, int]) -> None:
    import matplotlib.pyplot as plt
    fig, axes_array = plt.subplots(*dimensions, figsize=(15, 8), squeeze=False)
    axes = list(axes_array.flat)
    for axis, (metric, title, ylabel) in zip(axes, specifications):
        plot_metric(axis, rows, metric, title, ylabel)
    for axis in axes[len(specifications):]:
        axis.set_visible(False)
    finish_figure(fig, axes, output / filename)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    results = args.results.resolve()
    output = (args.output or results / "graphs").resolve()
    rows = collect(results)
    if not rows:
        raise SystemExit(f"no completed arms found below {results}")
    output.mkdir(parents=True, exist_ok=True)

    fields = list(rows[0].keys())
    with (output / "summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    if args.summary_only:
        print(f"rows: {len(rows)}; csv: {output / 'summary.csv'}")
        return 0

    graph_grid(rows, output, [
        ("write_amplification", "Write amplification", "Physical / logical bytes"),
        ("point_read_amplification", "Point-read amplification", "SST probes / Get"),
        ("scan_amplification", "Scan amplification", "Visited / returned entries"),
        ("space_amplification", "Space amplification",
         "Settled SST / garbage-free SST bytes"),
        ("sorted_run_seeks_per_scan", "Sorted-run seeks", "Seeks / scan"),
        ("stall_seconds", "Write stalls", "Seconds"),
    ], "amplification_and_stalls.png", (2, 3))

    graph_grid(rows, output, [
        ("get_latency_avg_us", "Get average", "Microseconds"),
        ("scan_latency_avg_us", "Scan average", "Microseconds"),
        ("write_latency_avg_us", "Write average", "Microseconds"),
        ("get_latency_p99_us", "Get p99", "Microseconds"),
        ("scan_latency_p99_us", "Scan p99", "Microseconds"),
        ("write_latency_p99_us", "Write p99", "Microseconds"),
    ], "latency.png", (2, 3))

    graph_grid(rows, output, [
        ("elapsed_seconds", "End-to-end runtime", "Seconds"),
    ], "runtime.png", (1, 1))

    print(f"rows:   {len(rows)}")
    print(f"csv:    {output / 'summary.csv'}")
    print(f"graphs: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
