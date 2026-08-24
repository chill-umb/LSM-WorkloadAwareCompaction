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

import matplotlib.pyplot as plt


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
                          live_logical_bytes: float) -> dict[str, float]:
    """Formal metric definitions shared by collection and unit tests."""
    return {
        "write_amplification": divide(
            flush_bytes + compact_bytes, user_write_bytes),
        "point_read_amplification": divide(point_probes, gets),
        "scan_amplification": divide(
            scan_returned + scan_skips, scan_returned),
        "sorted_run_seeks_per_scan": divide(sorted_run_seeks, scans),
        "space_amplification": divide(
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
            "count": count,
            "avg": divide(total, count),
        }
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
    drain_compaction_bytes = 0.0
    workload_compaction_bytes = 0.0
    drain_compaction_seconds = 0.0
    workload_compaction_seconds = 0.0
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
            else:
                workload_compaction_bytes += output
                workload_compaction_seconds += seconds
    return {
        "drain_seconds": drain_seconds,
        "drain_pending_bytes_before": float(sum(before)) if before else math.nan,
        "drain_pending_bytes_after": float(sum(after)) if after else math.nan,
        "drain_compaction_write_bytes": drain_compaction_bytes,
        "workload_compaction_write_bytes": workload_compaction_bytes,
        "drain_compaction_seconds": drain_compaction_seconds,
        "workload_compaction_seconds": workload_compaction_seconds,
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

    flush_bytes = tickers.get("rocksdb.flush.write.bytes", 0.0)
    compact_bytes = tickers.get("rocksdb.compact.write.bytes", 0.0)
    user_write_bytes = tickers.get("rocksdb.bytes.written", 0.0)
    point_probes = tickers.get("rocksdb.point.sst.probe", 0.0)
    scan_skips = tickers.get("rocksdb.number.iter.skip", 0.0)
    sorted_run_seeks = tickers.get("rocksdb.sorted.run.seek", 0.0)
    before = number(sizes.get("sst_bytes_before_full_compaction"))
    after = number(sizes.get("sst_bytes_after_full_compaction"))
    live_logical_bytes = properties.get(
        "rocksdb.estimate-live-data-size", math.nan)
    amplification = amplification_metrics(
        flush_bytes=flush_bytes, compact_bytes=compact_bytes,
        user_write_bytes=user_write_bytes, point_probes=point_probes,
        gets=gets, scan_returned=scan_returned, scan_skips=scan_skips,
        sorted_run_seeks=sorted_run_seeks, scans=scans,
        physical_sst_bytes=before, live_logical_bytes=live_logical_bytes)

    get_latency = histograms.get("rocksdb.db.get.micros", {})
    scan_latency = histograms.get("rocksdb.db.seek.micros", {})
    write_latency = histograms.get("rocksdb.db.write.micros", {})
    write_stall = histograms.get("rocksdb.db.write.stall", {})
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
        "elapsed_seconds": number(metadata.get("elapsed_seconds")),
        **amplification,
        "stall_seconds": tickers.get("rocksdb.stall.micros", 0.0) / 1e6,
        "stall_events": write_stall.get("count", 0.0),
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
    for completed in results.glob("*M/T*/**/COMPLETED"):
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

    graph_grid(rows, output, [
        ("write_amplification", "Write amplification", "Physical / logical bytes"),
        ("point_read_amplification", "Point-read amplification", "SST probes / Get"),
        ("scan_amplification", "Scan amplification", "Visited / returned entries"),
        ("space_amplification", "Space amplification", "SST / live logical bytes"),
        ("sorted_run_seeks_per_scan", "Sorted-run seeks", "Seeks / scan"),
        ("stall_seconds", "Write stalls", "Seconds"),
    ], "amplification_and_stalls.png", (2, 3))

    graph_grid(rows, output, [
        ("get_latency_avg_us", "Get average", "Microseconds"),
        ("scan_latency_avg_us", "Scan average", "Microseconds"),
        ("write_latency_avg_us", "Write average", "Microseconds"),
        ("get_latency_p95_us", "Get p95", "Microseconds"),
        ("scan_latency_p95_us", "Scan p95", "Microseconds"),
        ("write_latency_p95_us", "Write p95", "Microseconds"),
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
