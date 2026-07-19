#!/usr/bin/env python3
"""Aggregate and plot DQN parameter sweep experiment results."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


METRIC_PATHS: Sequence[Tuple[str, str, str]] = (
    ("total_runtime_seconds", "total_time_seconds", "lower"),
    ("write_throughput_ops_per_sec", "write_throughput_ops_per_sec", "higher"),
    ("write_avg_latency_ns", "write_latency.avg_ns", "lower"),
    ("write_p95_latency_ns", "write_latency.p95_ns", "lower"),
    ("read_avg_latency_ns", "read_latency.avg_ns", "lower"),
    ("read_p95_latency_ns", "read_latency.p95_ns", "lower"),
    ("insert_avg_latency_ns", "operation_latency.insert.avg_ns", "lower"),
    ("insert_p95_latency_ns", "operation_latency.insert.p95_ns", "lower"),
    ("update_avg_latency_ns", "operation_latency.update.avg_ns", "lower"),
    ("update_p95_latency_ns", "operation_latency.update.p95_ns", "lower"),
    ("get_avg_latency_ns", "operation_latency.get.avg_ns", "lower"),
    ("get_p95_latency_ns", "operation_latency.get.p95_ns", "lower"),
    ("scan_avg_latency_ns", "operation_latency.scan.avg_ns", "lower"),
    ("scan_p95_latency_ns", "operation_latency.scan.p95_ns", "lower"),
    ("telemetry_compaction_read_bytes", "telemetry.compaction_bytes_read", "lower"),
    ("telemetry_compaction_written_bytes", "telemetry.compaction_bytes_written", "lower"),
    ("l0_compactions_completed", "telemetry.l0_compactions_completed", "context"),
    ("stall_micros", "rocksdb_tickers.stall_micros", "lower"),
    ("write_stall_count", "rocksdb_tickers.write_stall_count", "lower"),
)


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


def nested_get(data: Dict[str, Any], path: str, default: float = 0.0) -> float:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    try:
        return float(current)
    except (TypeError, ValueError):
        return default


def pct_improvement(base: float, candidate: float, mode: str) -> Optional[float]:
    if mode == "context":
        return None
    if base == 0:
        return 0.0 if candidate == 0 else None
    if mode == "higher":
        return (candidate - base) / base * 100.0
    if mode == "lower":
        return (base - candidate) / base * 100.0
    raise ValueError(f"unknown mode: {mode}")


def parameter_from_dir(path: Path) -> Optional[float]:
    match = re.search(r"([-+]?\d+(?:\.\d+)?)$", path.name)
    if not match:
        return None
    value = float(match.group(1))
    return int(value) if value.is_integer() else value


def _rows_by_level(rows: List[Dict[str, Any]]) -> Dict[int, List[Dict[str, Any]]]:
    """Group metrics rows per agent. Multi-level runs tag each row with its
    level; legacy single-level rows carry level=None and ARE the L0 agent, so
    they merge into level 0."""
    by_level: Dict[int, List[Dict[str, Any]]] = {}
    for row in rows:
        level = row.get("level")
        key = 0 if level is None else int(level)
        by_level.setdefault(key, []).append(row)
    return by_level


def rl_agent_summary(run_dir: Path) -> Dict[str, float]:
    """Per-agent training/behavior summary.

    Multi-level runs interleave one metrics row per level per decision; naive
    whole-file aggregates mix six agents' streams (e.g. `epsilon_last` becomes
    whichever level logged last — typically the rarest deep level). The primary
    series here is the L0 agent — present at every decision point and the agent
    that drives stall behavior — with per-level columns alongside.
    """
    rows = load_jsonl(run_dir / "rl" / "agent" / "rl_compaction_metrics.jsonl")
    out: Dict[str, float] = {
        "rl_decision_steps": 0.0,   # L0 stream length ≈ number of decisions
        "rl_level_rows_total": float(len(rows)),
        "rl_levels_observed": 0.0,
        "epsilon_first": 0.0,
        "epsilon_last": 0.0,
        "epsilon_avg": 0.0,
        "reward_avg": 0.0,
        "reward_last": 0.0,
        "loss_last": 0.0,
        "rl_action_compact_now": 0.0,        # L0 agent only
        "rl_action_do_nothing": 0.0,         # L0 agent only
        "rl_action_compact_now_total": 0.0,  # all levels
        "rl_action_do_nothing_total": 0.0,   # all levels
    }
    if not rows:
        return out

    by_level = _rows_by_level(rows)
    out["rl_levels_observed"] = float(len(by_level))
    primary = by_level.get(0) or by_level[sorted(by_level)[0]]

    eps = [float(r.get("epsilon", 0.0)) for r in primary]
    rewards = [float(r.get("reward", 0.0)) for r in primary]
    losses = [float(r.get("loss", 0.0)) for r in primary if r.get("loss") is not None]
    out.update(
        rl_decision_steps=float(len(primary)),
        epsilon_first=eps[0],
        epsilon_last=eps[-1],
        epsilon_avg=sum(eps) / len(eps),
        reward_avg=sum(rewards) / len(rewards),
        reward_last=rewards[-1],
        loss_last=losses[-1] if losses else 0.0,
    )

    for level, level_rows in sorted(by_level.items()):
        counts: Counter[str] = Counter(
            r.get("action_name", "unknown") for r in level_rows
        )
        level_eps = [float(r.get("epsilon", 0.0)) for r in level_rows]
        level_rewards = [float(r.get("reward", 0.0)) for r in level_rows]
        out[f"l{level}_steps"] = float(len(level_rows))
        out[f"l{level}_epsilon_last"] = level_eps[-1]
        out[f"l{level}_reward_avg"] = sum(level_rewards) / len(level_rewards)
        out[f"l{level}_compact_now"] = float(counts.get("compact_now", 0))
        out[f"l{level}_do_nothing"] = float(counts.get("do_nothing", 0))
        out["rl_action_compact_now_total"] += counts.get("compact_now", 0)
        out["rl_action_do_nothing_total"] += counts.get("do_nothing", 0)
        if level == 0 or (0 not in by_level and level == sorted(by_level)[0]):
            out["rl_action_compact_now"] = float(counts.get("compact_now", 0))
            out["rl_action_do_nothing"] = float(counts.get("do_nothing", 0))
    return out


def collect_run(
    run_dir: Path,
    parameter_name: str,
    baseline_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    parameter_value = parameter_from_dir(run_dir)
    if parameter_value is None:
        return None

    leveled_path = (baseline_dir or run_dir / "leveled") / "experiment_metrics.json"
    rl_path = run_dir / "rl" / "experiment_metrics.json"
    if not leveled_path.exists() or not rl_path.exists():
        return None

    leveled = load_json(leveled_path)
    rl = load_json(rl_path)

    row: Dict[str, Any] = {
        "run_dir": str(run_dir),
        "baseline_dir": str(baseline_dir or run_dir / "leveled"),
        "parameter_name": parameter_name,
        "parameter_value": parameter_value,
    }

    for name, path, mode in METRIC_PATHS:
        base = nested_get(leveled, path)
        candidate = nested_get(rl, path)
        row[f"leveled_{name}"] = base
        row[f"rl_{name}"] = candidate
        improvement = pct_improvement(base, candidate, mode)
        row[f"rl_{name}_improvement_pct"] = improvement

    row.update(rl_agent_summary(run_dir))
    return row


def import_matplotlib():
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        return plt
    except ImportError as exc:
        raise SystemExit(
            "matplotlib is required for sweep graphs. Install it with: "
            "python3 -m pip install matplotlib"
        ) from exc


def write_csv(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        return
    # Union of keys across rows: per-level columns (l0_*, l1_*, ...) can vary
    # per run when trees reach different depths.
    fieldnames = list(rows[0].keys())
    seen = set(fieldnames)
    for row in rows[1:]:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval=0)
        writer.writeheader()
        writer.writerows(rows)


def plot_lines(
    plt: Any,
    out_path: Path,
    rows: List[Dict[str, Any]],
    title: str,
    ylabel: str,
    series: Sequence[Tuple[str, str]],
) -> None:
    x = [row["parameter_value"] for row in rows]
    fig, ax = plt.subplots(figsize=(10.5, 6.2))
    for key, label in series:
        y = [row.get(key, 0.0) for row in rows]
        color = "#1f77b4" if key.startswith("leveled_") else "#ff7f0e" if key.startswith("rl_") else None
        linestyle = "--" if "_p95_" in key else "-"
        marker = "s" if "_p95_" in key else "o"
        ax.plot(
            x,
            y,
            marker=marker,
            linewidth=2.0,
            linestyle=linestyle,
            color=color,
            label=label,
            zorder=2,
        )
    ax.set_title(title)
    ax.set_xlabel(rows[0]["parameter_name"])
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.28)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def plot_all(plt: Any, out_dir: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return

    plot_lines(
        plt,
        out_dir / "sweep_write_latency.png",
        rows,
        "Absolute Write Latency vs DQN Exploration Decay",
        "Absolute latency (ns)",
        (
            ("leveled_write_avg_latency_ns", "leveled write avg"),
            ("leveled_write_p95_latency_ns", "leveled write p95"),
            ("rl_write_avg_latency_ns", "RL write avg"),
            ("rl_write_p95_latency_ns", "RL write p95"),
        ),
    )
    plot_lines(
        plt,
        out_dir / "sweep_read_latency.png",
        rows,
        "Absolute Read Latency vs DQN Exploration Decay",
        "Absolute latency (ns)",
        (
            ("leveled_read_avg_latency_ns", "leveled read avg"),
            ("leveled_read_p95_latency_ns", "leveled read p95"),
            ("rl_read_avg_latency_ns", "RL read avg"),
            ("rl_read_p95_latency_ns", "RL read p95"),
        ),
    )
    plot_lines(
        plt,
        out_dir / "sweep_get_latency.png",
        rows,
        "Absolute Point Query Latency vs DQN Exploration Decay",
        "Absolute latency (ns)",
        (
            ("leveled_get_avg_latency_ns", "leveled get avg"),
            ("leveled_get_p95_latency_ns", "leveled get p95"),
            ("rl_get_avg_latency_ns", "RL get avg"),
            ("rl_get_p95_latency_ns", "RL get p95"),
        ),
    )
    plot_lines(
        plt,
        out_dir / "sweep_scan_latency.png",
        rows,
        "Absolute Scan Latency vs DQN Exploration Decay",
        "Absolute latency (ns)",
        (
            ("leveled_scan_avg_latency_ns", "leveled scan avg"),
            ("leveled_scan_p95_latency_ns", "leveled scan p95"),
            ("rl_scan_avg_latency_ns", "RL scan avg"),
            ("rl_scan_p95_latency_ns", "RL scan p95"),
        ),
    )
    plot_lines(
        plt,
        out_dir / "sweep_runtime_throughput.png",
        rows,
        "Absolute Runtime and Write Throughput vs DQN Exploration Decay",
        "Seconds / ops per sec",
        (
            ("leveled_total_runtime_seconds", "leveled runtime seconds"),
            ("rl_total_runtime_seconds", "RL runtime seconds"),
            ("leveled_write_throughput_ops_per_sec", "leveled write throughput"),
            ("rl_write_throughput_ops_per_sec", "RL write throughput"),
        ),
    )
    plot_lines(
        plt,
        out_dir / "sweep_compaction_cost.png",
        rows,
        "Absolute Compaction Cost vs DQN Exploration Decay",
        "Bytes / count",
        (
            ("leveled_telemetry_compaction_read_bytes", "leveled compact read bytes"),
            ("rl_telemetry_compaction_read_bytes", "RL compact read bytes"),
            ("leveled_l0_compactions_completed", "leveled L0 compactions"),
            ("rl_l0_compactions_completed", "RL L0 compactions"),
        ),
    )
    plot_lines(
        plt,
        out_dir / "sweep_rl_behavior.png",
        rows,
        "RL Behavior vs DQN Exploration Decay",
        "Count / epsilon",
        (
            ("rl_action_compact_now", "L0 compact_now actions"),
            ("rl_action_do_nothing", "L0 do_nothing actions"),
            ("rl_action_compact_now_total", "compact_now (all levels)"),
            ("epsilon_last", "L0 final epsilon"),
            ("reward_avg", "L0 average reward"),
        ),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sweep-root",
        required=True,
        help="Directory containing parameter run directories, such as eps_400 or gamma_0.95",
    )
    parser.add_argument(
        "--baseline-dir",
        help=(
            "Optional shared leveled baseline directory for legacy sweeps. "
            "By default, each parameter run uses its own leveled/ directory."
        ),
    )
    parser.add_argument(
        "--parameter-name",
        default="RL_EPSILON_DECAY_STEPS",
        help="Name to use for the x-axis and summary metadata",
    )
    parser.add_argument("--out-dir", help="Output directory. Defaults to <sweep-root>/sweep_analysis")
    parser.add_argument(
        "--leveled-baseline",
        choices=("first", "mean"),
        default="first",
        help="Deprecated compatibility option. Leveled values are now plotted per run.",
    )
    parser.add_argument("--no-plots", action="store_true", help="Only write CSV/JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sweep_root = Path(args.sweep_root)
    out_dir = Path(args.out_dir) if args.out_dir else sweep_root / "sweep_analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    baseline_dir: Optional[Path] = Path(args.baseline_dir) if args.baseline_dir else None

    rows = [
        row
        for row in (
            collect_run(path, args.parameter_name, baseline_dir)
            for path in sorted(sweep_root.iterdir())
            if path.is_dir()
        )
        if row is not None
    ]
    rows.sort(key=lambda row: row["parameter_value"])

    if not rows:
        raise SystemExit(f"No completed parameter runs found under {sweep_root}")

    write_csv(out_dir / "dqn_parameter_sweep_summary.csv", rows)
    with (out_dir / "dqn_parameter_sweep_summary.json").open("w", encoding="utf-8") as f:
        json.dump(
            {
                "sweep_root": str(sweep_root),
                "baseline_dir": str(baseline_dir) if baseline_dir else None,
                "parameter_name": args.parameter_name,
                "leveled_series": "shared_baseline" if baseline_dir else "per_run",
                "runs": rows,
            },
            f,
            indent=2,
            sort_keys=True,
        )
        f.write("\n")

    if not args.no_plots:
        os.environ.setdefault("MPLCONFIGDIR", "/tmp/lsm-matplotlib-cache")
        plt = import_matplotlib()
        plot_all(plt, out_dir, rows)

    print(f"Collected {len(rows)} runs")
    print(f"Summary CSV: {out_dir / 'dqn_parameter_sweep_summary.csv'}")
    print(f"Summary JSON: {out_dir / 'dqn_parameter_sweep_summary.json'}")
    if not args.no_plots:
        print(f"Plots: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
