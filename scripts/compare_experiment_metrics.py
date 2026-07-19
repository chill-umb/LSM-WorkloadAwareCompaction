#!/usr/bin/env python3
"""Compare vanilla RocksDB and RL compaction experiment outputs."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


MetricSpec = Tuple[str, str, str]


EXPERIMENT_METRICS: List[MetricSpec] = [
    ("Total runtime seconds", "total_time_seconds", "lower"),
    ("Write throughput ops/s", "write_throughput_ops_per_sec", "higher"),
    ("Write avg latency ns", "write_latency.avg_ns", "lower"),
    ("Write p95 latency ns", "write_latency.p95_ns", "lower"),
    ("Read avg latency ns", "read_latency.avg_ns", "lower"),
    ("Read p95 latency ns", "read_latency.p95_ns", "lower"),
    ("Get avg latency ns", "operation_latency.get.avg_ns", "lower"),
    ("Get p95 latency ns", "operation_latency.get.p95_ns", "lower"),
    ("Scan avg latency ns", "operation_latency.scan.avg_ns", "lower"),
    ("Scan p95 latency ns", "operation_latency.scan.p95_ns", "lower"),
    ("Telemetry compaction read bytes", "telemetry.compaction_bytes_read", "lower"),
    ("Telemetry compaction written bytes", "telemetry.compaction_bytes_written", "lower"),
    ("RocksDB compact read bytes", "rocksdb_tickers.compact_read_bytes", "lower"),
    ("RocksDB compact write bytes", "rocksdb_tickers.compact_write_bytes", "lower"),
    ("L0 compactions completed", "telemetry.l0_compactions_completed", "context"),
    ("Stall micros", "rocksdb_tickers.stall_micros", "lower"),
    ("Write stall count", "rocksdb_tickers.write_stall_count", "lower"),
]

LSM_METRICS: List[MetricSpec] = [
    ("Avg L0 files", "avg_l0_files", "lower"),
    ("Max L0 files", "max_l0_files", "lower"),
    ("Final L0 files", "final_l0_files", "context"),
    ("Avg L0 size bytes", "avg_l0_size_bytes", "lower"),
    ("Max L0 size bytes", "max_l0_size_bytes", "lower"),
    ("Avg pending compaction bytes", "avg_pending_compaction_bytes", "lower"),
    ("Max pending compaction bytes", "max_pending_compaction_bytes", "lower"),
]

def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []

    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def measured_lsm_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not rows or not any("phase" in row for row in rows):
        return rows
    measured = [row for row in rows if row.get("phase") == "measured"]
    return measured or rows


def nested_get(data: Dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        current = current[part]
    return current


def pct_change(base: float, candidate: float) -> Optional[float]:
    if base == 0:
        if candidate == 0:
            return 0.0
        return None
    return (candidate - base) / base * 100.0


def pct_improvement(base: float, candidate: float, mode: str) -> Optional[float]:
    if mode == "context":
        return None

    if base == 0:
        if candidate == 0:
            return 0.0
        return None

    if mode == "higher":
        return (candidate - base) / base * 100.0
    if mode == "lower":
        return (base - candidate) / base * 100.0

    raise ValueError(f"unknown metric mode: {mode}")


def fmt_num(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if abs(value) >= 1_000_000:
            return f"{value:,.0f}"
        if abs(value) >= 1_000:
            return f"{value:,.2f}"
        return f"{value:.6g}"
    return str(value)


def fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}%"


def summarize_lsm(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    if not rows:
        return {
            "avg_l0_files": 0.0,
            "max_l0_files": 0.0,
            "final_l0_files": 0.0,
            "avg_l0_size_bytes": 0.0,
            "max_l0_size_bytes": 0.0,
            "final_l0_size_bytes": 0.0,
            "avg_pending_compaction_bytes": 0.0,
            "max_pending_compaction_bytes": 0.0,
            "final_pending_compaction_bytes": 0.0,
        }

    def values(key: str) -> List[float]:
        return [float(row.get(key, 0.0)) for row in rows]

    l0_files = values("l0_files")
    l0_size = values("l0_size_bytes")
    pending = values("pending_compaction_bytes")

    return {
        "avg_l0_files": sum(l0_files) / len(l0_files),
        "max_l0_files": max(l0_files),
        "final_l0_files": l0_files[-1],
        "avg_l0_size_bytes": sum(l0_size) / len(l0_size),
        "max_l0_size_bytes": max(l0_size),
        "final_l0_size_bytes": l0_size[-1],
        "avg_pending_compaction_bytes": sum(pending) / len(pending),
        "max_pending_compaction_bytes": max(pending),
        "final_pending_compaction_bytes": pending[-1],
    }


def collect_rows(
    base_exp: Dict[str, Any],
    candidate_exp: Dict[str, Any],
    base_lsm_summary: Dict[str, float],
    candidate_lsm_summary: Dict[str, float],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    for label, path, mode in EXPERIMENT_METRICS:
        base = float(nested_get(base_exp, path))
        candidate = float(nested_get(candidate_exp, path))
        rows.append(
            {
                "metric": label,
                "mode": mode,
                "baseline": base,
                "candidate": candidate,
                "candidate_change_pct": pct_change(base, candidate),
                "candidate_improvement_pct": pct_improvement(base, candidate, mode),
            }
        )

    for label, key, mode in LSM_METRICS:
        base = float(base_lsm_summary[key])
        candidate = float(candidate_lsm_summary[key])
        rows.append(
            {
                "metric": label,
                "mode": mode,
                "baseline": base,
                "candidate": candidate,
                "candidate_change_pct": pct_change(base, candidate),
                "candidate_improvement_pct": pct_improvement(base, candidate, mode),
            }
        )

    return rows


def print_table(rows: Iterable[Dict[str, Any]], baseline_label: str, candidate_label: str) -> None:
    table_rows = list(rows)
    metric_width = max(len("Metric"), *(len(row["metric"]) for row in table_rows))
    print(
        f"{'Metric':<{metric_width}}  "
        f"{baseline_label:>15}  "
        f"{candidate_label:>15}  "
        f"{'RL improvement':>15}  "
        f"{'Raw change':>12}"
    )
    print("-" * (metric_width + 65))
    for row in table_rows:
        print(
            f"{row['metric']:<{metric_width}}  "
            f"{fmt_num(row['baseline']):>15}  "
            f"{fmt_num(row['candidate']):>15}  "
            f"{fmt_pct(row['candidate_improvement_pct']):>15}  "
            f"{fmt_pct(row['candidate_change_pct']):>12}"
        )
    print(
        "\nNote: positive improvement means the candidate is better for lower/higher "
        "metrics. Context metrics only use raw change."
    )


def write_csv(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    fieldnames = [
        "metric",
        "mode",
        "baseline",
        "candidate",
        "candidate_change_pct",
        "candidate_improvement_pct",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def load_action_counts(candidate_dir: Path) -> Dict[str, Any]:
    """Action counts, total and per level. Multi-level runs log one io entry
    per level per decision; totals alone would conflate six agents' behavior."""
    rows = load_jsonl(candidate_dir / "agent" / "rl_compaction_io.jsonl")
    total: Counter[str] = Counter()
    by_level: Dict[str, Counter] = {}
    for row in rows:
        action = row.get("output", {}).get("action_name", "unknown")
        total[action] += 1
        level = row.get("input", {}).get("level")
        if level is None:
            level = row.get("diagnostics", {}).get("level")
        key = "L0" if level is None else f"L{int(level)}"
        by_level.setdefault(key, Counter())[action] += 1
    result: Dict[str, Any] = dict(total)
    result["by_level"] = {k: dict(v) for k, v in sorted(by_level.items())}
    return result


def import_matplotlib():
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        return plt
    except ImportError as exc:
        raise SystemExit(
            "matplotlib is required for graphs. Install it with: "
            "python3 -m pip install matplotlib"
        ) from exc


def plot_percent_deltas(
    plt: Any,
    out_path: Path,
    rows: List[Dict[str, Any]],
    baseline_label: str,
    candidate_label: str,
) -> None:
    labels: List[str] = []
    deltas: List[Optional[float]] = []
    modes: List[str] = []
    annotations: List[str] = []

    for row in rows:
        mode = row["mode"]
        labels.append(row["metric"])
        modes.append(mode)

        if mode == "context":
            value = row["candidate_change_pct"]
            deltas.append(value)
            annotations.append(
                f"raw {value:+.1f}%"
                if value is not None
                else f"n/a ({fmt_num(row['baseline'])} -> {fmt_num(row['candidate'])})"
            )
        else:
            value = row["candidate_improvement_pct"]
            deltas.append(value)
            annotations.append(
                f"{value:+.1f}%"
                if value is not None
                else f"n/a ({fmt_num(row['baseline'])} -> {fmt_num(row['candidate'])})"
            )

    plottable = [value for value in deltas if value is not None]
    if not plottable:
        return

    fig_height = max(8.0, 0.42 * len(labels) + 2.2)
    fig, ax = plt.subplots(figsize=(13, fig_height))
    y = list(range(len(labels)))
    plot_values = [0.0 if value is None else value for value in deltas]
    colors = []
    for value, mode in zip(deltas, modes):
        if value is None:
            colors.append("#8a8f98")
        elif mode == "context":
            colors.append("#526d8f")
        elif value >= 0:
            colors.append("#2f8f46")
        else:
            colors.append("#c23b3b")

    ax.barh(y, plot_values, color=colors)
    ax.axvline(0, color="#222222", linewidth=1)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    min_delta = min(plottable)
    max_delta = max(plottable)
    left_pad = max(10.0, abs(min_delta) * 0.18)
    right_pad = max(10.0, abs(max_delta) * 0.18)
    ax.set_xlim(min(min_delta - left_pad, -10.0), max(max_delta + right_pad, 10.0))
    ax.set_xlabel(
        f"{candidate_label} improvement over {baseline_label} (%); "
        "positive means better"
    )
    ax.set_title("Experiment Comparison: Percent Improvement and Context Deltas")
    ax.grid(True, axis="x", alpha=0.25)

    text_offset = max((max_delta - min_delta) * 0.015, 0.8)
    for idx, (value, annotation) in enumerate(zip(deltas, annotations)):
        plot_value = 0.0 if value is None else value
        align = "left" if plot_value >= 0 else "right"
        offset = text_offset if plot_value >= 0 else -text_offset
        ax.text(plot_value + offset, idx, annotation, va="center", ha=align, fontsize=8.5)

    from matplotlib.patches import Patch

    ax.legend(
        handles=[
            Patch(color="#2f8f46", label="Improvement"),
            Patch(color="#c23b3b", label="Regression"),
            Patch(color="#526d8f", label="Context raw change"),
            Patch(color="#8a8f98", label="Not percentage-comparable"),
        ],
        loc="upper right",
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def short_metric_label(metric: str) -> str:
    replacements = {
        "Write avg latency ns": "Write avg",
        "Write p95 latency ns": "Write p95",
        "Read avg latency ns": "Read avg",
        "Read p95 latency ns": "Read p95",
        "Get avg latency ns": "Get avg",
        "Get p95 latency ns": "Get p95",
        "Scan avg latency ns": "Scan avg",
        "Scan p95 latency ns": "Scan p95",
    }
    return replacements.get(metric, metric)


def compact_tick_label(value: float) -> str:
    abs_value = abs(value)
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:g}M"
    if abs_value >= 1_000:
        return f"{value / 1_000:g}K"
    return f"{value:g}"


def plot_latency_value_bars(
    plt: Any,
    out_path: Path,
    rows: List[Dict[str, Any]],
    baseline_label: str,
    candidate_label: str,
) -> None:
    latency_rows = [
        row
        for row in rows
        if row["metric"]
        in {
            "Write avg latency ns",
            "Write p95 latency ns",
            "Read avg latency ns",
            "Read p95 latency ns",
            "Get avg latency ns",
            "Get p95 latency ns",
            "Scan avg latency ns",
            "Scan p95 latency ns",
        }
    ]

    if not latency_rows:
        return

    labels = [short_metric_label(row["metric"]) for row in latency_rows]
    baseline_values = [float(row["baseline"]) for row in latency_rows]
    candidate_values = [float(row["candidate"]) for row in latency_rows]

    x = list(range(len(labels)))
    width = 0.36
    fig, ax = plt.subplots(figsize=(13, 6.8))
    ax.bar(
        [pos - width / 2 for pos in x],
        baseline_values,
        width,
        label=baseline_label,
        color="#a8cfe3",
        edgecolor="#2f2f2f",
        linewidth=0.8,
    )
    ax.bar(
        [pos + width / 2 for pos in x],
        candidate_values,
        width,
        label=candidate_label,
        color="#2c7fb8",
        edgecolor="#2f2f2f",
        linewidth=0.8,
    )

    from matplotlib.ticker import FuncFormatter

    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: compact_tick_label(value)))
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=18, ha="right")
    ax.set_ylabel("Latency (ns)")
    ax.set_title("Latency Value Comparison")
    ax.grid(True, axis="y", alpha=0.28)
    ax.legend()

    max_value = max(baseline_values + candidate_values)
    if max_value > 0:
        ax.set_ylim(0, max_value * 1.12)

    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def row_by_metric(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {row["metric"]: row for row in rows}


def add_grouped_value_panel(
    ax: Any,
    labels: List[str],
    baseline_values: List[float],
    candidate_values: List[float],
    baseline_label: str,
    candidate_label: str,
    ylabel: str,
    title: str,
) -> None:
    x = list(range(len(labels)))
    width = 0.36
    ax.bar(
        [pos - width / 2 for pos in x],
        baseline_values,
        width,
        label=baseline_label,
        color="#a8cfe3",
        edgecolor="#2f2f2f",
        linewidth=0.8,
    )
    ax.bar(
        [pos + width / 2 for pos in x],
        candidate_values,
        width,
        label=candidate_label,
        color="#2c7fb8",
        edgecolor="#2f2f2f",
        linewidth=0.8,
    )

    from matplotlib.ticker import FuncFormatter

    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: compact_tick_label(value)))
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=18, ha="right")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, axis="y", alpha=0.28)

    max_value = max(baseline_values + candidate_values) if labels else 0.0
    if max_value > 0:
        ax.set_ylim(0, max_value * 1.12)


def plot_key_metric_value_bars(
    plt: Any,
    out_path: Path,
    rows: List[Dict[str, Any]],
    baseline_label: str,
    candidate_label: str,
) -> None:
    by_metric = row_by_metric(rows)

    groups = [
        (
            "Latency",
            "Latency (ns)",
            [
                ("Write avg", "Write avg latency ns", 1.0),
                ("Write p95", "Write p95 latency ns", 1.0),
                ("Read avg", "Read avg latency ns", 1.0),
                ("Read p95", "Read p95 latency ns", 1.0),
                ("Get avg", "Get avg latency ns", 1.0),
                ("Get p95", "Get p95 latency ns", 1.0),
                ("Scan avg", "Scan avg latency ns", 1.0),
                ("Scan p95", "Scan p95 latency ns", 1.0),
            ],
        ),
        (
            "Runtime and Throughput",
            "Value",
            [
                ("Runtime s", "Total runtime seconds", 1.0),
                ("Write ops/s", "Write throughput ops/s", 1.0),
            ],
        ),
        (
            "Compaction I/O",
            "MiB / count",
            [
                ("Telemetry read MiB", "Telemetry compaction read bytes", 1.0 / (1024 * 1024)),
                ("Telemetry write MiB", "Telemetry compaction written bytes", 1.0 / (1024 * 1024)),
                ("RocksDB read MiB", "RocksDB compact read bytes", 1.0 / (1024 * 1024)),
                ("RocksDB write MiB", "RocksDB compact write bytes", 1.0 / (1024 * 1024)),
                ("L0 compactions", "L0 compactions completed", 1.0),
            ],
        ),
        (
            "L0 and Stall Pressure",
            "Files / MiB / ms / count",
            [
                ("Avg L0 files", "Avg L0 files", 1.0),
                ("Max L0 files", "Max L0 files", 1.0),
                ("Avg L0 MiB", "Avg L0 size bytes", 1.0 / (1024 * 1024)),
                ("Max L0 MiB", "Max L0 size bytes", 1.0 / (1024 * 1024)),
                ("Avg pending MiB", "Avg pending compaction bytes", 1.0 / (1024 * 1024)),
                ("Max pending MiB", "Max pending compaction bytes", 1.0 / (1024 * 1024)),
                ("Stall ms", "Stall micros", 1.0 / 1000.0),
                ("Write stalls", "Write stall count", 1.0),
            ],
        ),
    ]

    fig, axes = plt.subplots(len(groups), 1, figsize=(14, 18))
    if len(groups) == 1:
        axes = [axes]

    for ax, (title, ylabel, specs) in zip(axes, groups):
        labels: List[str] = []
        baseline_values: List[float] = []
        candidate_values: List[float] = []
        for label, metric, scale in specs:
            row = by_metric.get(metric)
            if row is None:
                continue
            labels.append(label)
            baseline_values.append(float(row["baseline"]) * scale)
            candidate_values.append(float(row["candidate"]) * scale)

        add_grouped_value_panel(
            ax,
            labels,
            baseline_values,
            candidate_values,
            baseline_label,
            candidate_label,
            ylabel,
            title,
        )

    axes[0].legend(loc="upper right")
    fig.suptitle("Key Metric Value Comparison", fontsize=15)
    fig.tight_layout(rect=(0, 0, 1, 0.985))
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def plot_l0_timeseries(
    plt: Any,
    out_path: Path,
    base_rows: List[Dict[str, Any]],
    candidate_rows: List[Dict[str, Any]],
    baseline_label: str,
    candidate_label: str,
) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
    series = [
        ("l0_files", "L0 files", lambda value: value),
        ("l0_size_bytes", "L0 size (MB)", lambda value: value / (1024 * 1024)),
        (
            "pending_compaction_bytes",
            "Pending compaction bytes (MB)",
            lambda value: value / (1024 * 1024),
        ),
    ]

    for ax, (key, ylabel, transform) in zip(axes, series):
        for rows, label in [(base_rows, baseline_label), (candidate_rows, candidate_label)]:
            x = [row.get("measured_op_index", row.get("op_index", 0)) for row in rows]
            y = [transform(float(row.get(key, 0.0))) for row in rows]
            ax.plot(x, y, label=label, linewidth=1.8)
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.25)

    axes[-1].set_xlabel("Measured operation index")
    axes[0].legend()
    fig.suptitle("L0 State Over Time")
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def plot_compaction_costs(
    plt: Any,
    out_path: Path,
    base_exp: Dict[str, Any],
    candidate_exp: Dict[str, Any],
    baseline_label: str,
    candidate_label: str,
) -> None:
    labels = [
        "Compact read MB",
        "Compact write MB",
        "Telemetry read MB",
        "Telemetry write MB",
        "L0 compactions",
    ]
    base_values = [
        nested_get(base_exp, "rocksdb_tickers.compact_read_bytes") / (1024 * 1024),
        nested_get(base_exp, "rocksdb_tickers.compact_write_bytes") / (1024 * 1024),
        nested_get(base_exp, "telemetry.compaction_bytes_read") / (1024 * 1024),
        nested_get(base_exp, "telemetry.compaction_bytes_written") / (1024 * 1024),
        nested_get(base_exp, "telemetry.l0_compactions_completed"),
    ]
    candidate_values = [
        nested_get(candidate_exp, "rocksdb_tickers.compact_read_bytes") / (1024 * 1024),
        nested_get(candidate_exp, "rocksdb_tickers.compact_write_bytes") / (1024 * 1024),
        nested_get(candidate_exp, "telemetry.compaction_bytes_read") / (1024 * 1024),
        nested_get(candidate_exp, "telemetry.compaction_bytes_written") / (1024 * 1024),
        nested_get(candidate_exp, "telemetry.l0_compactions_completed"),
    ]

    x = list(range(len(labels)))
    width = 0.38
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.bar([pos - width / 2 for pos in x], base_values, width, label=baseline_label)
    ax.bar([pos + width / 2 for pos in x], candidate_values, width, label=candidate_label)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.set_title("Compaction Cost Comparison")
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare experiment_metrics.json and lsm_metrics.jsonl from two runs."
    )
    parser.add_argument("--baseline", default="results/leveled", help="Baseline run directory")
    parser.add_argument(
        "--candidate",
        "--rl",
        default="results/rl",
        help="Candidate/RL run directory",
    )
    parser.add_argument("--baseline-label", default="leveled", help="Baseline label")
    parser.add_argument("--candidate-label", default="rl", help="Candidate label")
    parser.add_argument("--out-dir", default="results/comparison", help="Output directory")
    parser.add_argument("--no-plots", action="store_true", help="Skip PNG graph generation")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    baseline_dir = Path(args.baseline)
    candidate_dir = Path(args.candidate)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    base_exp = load_json(baseline_dir / "experiment_metrics.json")
    candidate_exp = load_json(candidate_dir / "experiment_metrics.json")
    base_lsm = load_jsonl(baseline_dir / "lsm_metrics.jsonl")
    candidate_lsm = load_jsonl(candidate_dir / "lsm_metrics.jsonl")
    base_lsm_measured = measured_lsm_rows(base_lsm)
    candidate_lsm_measured = measured_lsm_rows(candidate_lsm)

    base_lsm_summary = summarize_lsm(base_lsm_measured)
    candidate_lsm_summary = summarize_lsm(candidate_lsm_measured)
    rows = collect_rows(base_exp, candidate_exp, base_lsm_summary, candidate_lsm_summary)

    print_table(rows, args.baseline_label, args.candidate_label)
    write_csv(out_dir / "comparison_summary.csv", rows)

    summary = {
        "baseline_dir": str(baseline_dir),
        "candidate_dir": str(candidate_dir),
        "baseline_label": args.baseline_label,
        "candidate_label": args.candidate_label,
        "metrics": rows,
        "baseline_lsm_rows": len(base_lsm),
        "candidate_lsm_rows": len(candidate_lsm),
        "baseline_lsm_measured_rows": len(base_lsm_measured),
        "candidate_lsm_measured_rows": len(candidate_lsm_measured),
        "baseline_lsm_summary": base_lsm_summary,
        "candidate_lsm_summary": candidate_lsm_summary,
        "candidate_action_counts": load_action_counts(candidate_dir),
    }
    with (out_dir / "comparison_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
        f.write("\n")

    if not args.no_plots:
        os.environ.setdefault("MPLCONFIGDIR", "/tmp/lsm-matplotlib-cache")
        plt = import_matplotlib()
        plot_percent_deltas(
            plt,
            out_dir / "comparison_percent_delta.png",
            rows,
            args.baseline_label,
            args.candidate_label,
        )
        plot_latency_value_bars(
            plt,
            out_dir / "comparison_latency_values.png",
            rows,
            args.baseline_label,
            args.candidate_label,
        )
        plot_key_metric_value_bars(
            plt,
            out_dir / "comparison_key_metric_values.png",
            rows,
            args.baseline_label,
            args.candidate_label,
        )
        plot_l0_timeseries(
            plt,
            out_dir / "comparison_l0_timeseries.png",
            base_lsm_measured,
            candidate_lsm_measured,
            args.baseline_label,
            args.candidate_label,
        )
        plot_compaction_costs(
            plt,
            out_dir / "comparison_compaction_costs.png",
            base_exp,
            candidate_exp,
            args.baseline_label,
            args.candidate_label,
        )
        print(f"\nGraphs written to: {out_dir}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
