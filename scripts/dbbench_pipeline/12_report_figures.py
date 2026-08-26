#!/usr/bin/env python3
"""Comparison graphs for the matrix summary.csv — PNG only.

One figure per metric: workload size on x, one line per arm, faceted by size
ratio. All four arms are plotted as series, including the leveled baseline, so
absolute levels and scaling behaviour are both visible.

Plus a trade-off plane (write amp against point-read amp) and a paired-difference
panel, which answer questions the per-metric lines cannot: which trade each arm
is making, and which differences are statistically decided.

Colors are a validated categorical palette; every series is legended and the
lines are direct-labelled where they separate.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_stats import ci95, required_pairs

SERIES = {
    "regular":          ("#2a78d6", "leveled baseline"),
    "prior_only":       ("#1baf7a", "analytic prior"),
    "rl":               ("#eb6834", "rl"),
    "unconstrained_rl": ("#4a3aa7", "rl (no SLO mask)"),
}
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#d8d7d2"

PRETTY = {
    "write_amplification":       "write amplification",
    "point_read_amplification":  "point-read amplification",
    "sorted_run_seeks_per_scan": "sorted-run seeks per scan",
    "space_amplification":       "space amplification",
    "elapsed_seconds":           "runtime (s)",
    "stall_seconds":             "stall time (s)",
}
DEFAULT_METRICS = ",".join(PRETTY)


def load(path: Path):
    rows = list(csv.DictReader(path.open(newline="")))
    if not rows or "arm" not in rows[0]:
        raise SystemExit(
            f"{path}: no 'arm' column. If the delimiters were stripped in "
            "transfer, re-copy the file (base64 or tar, not paste).")
    index = {(int(r["size_millions"]), int(r["size_ratio"]),
              r["arm"], int(r["repeat"])): r for r in rows}
    sizes = sorted({int(r["size_millions"]) for r in rows})
    ratios = sorted({int(r["size_ratio"]) for r in rows})
    arms = [a for a in SERIES if any(r["arm"] == a for r in rows)]
    return index, sizes, ratios, arms


def mean_at(index, size, ratio, arm, metric):
    values = []
    for repeat in range(1, 21):
        row = index.get((size, ratio, arm, repeat))
        if not row:
            continue
        try:
            value = float(row[metric])
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append(value)
    return statistics.fmean(values) if values else None


def paired(index, sizes, ratios, arm, metric, baseline):
    out = []
    for size in sizes:
        for ratio in ratios:
            for repeat in range(1, 21):
                a = index.get((size, ratio, arm, repeat))
                b = index.get((size, ratio, baseline, repeat))
                if not a or not b:
                    continue
                try:
                    x, y = float(a[metric]), float(b[metric])
                except (TypeError, ValueError):
                    continue
                if y and math.isfinite(x) and math.isfinite(y):
                    out.append(((size, ratio), x / y - 1.0))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("summary", type=Path)
    parser.add_argument("--outdir", type=Path, default=Path("report"))
    parser.add_argument("--baseline", default="regular")
    parser.add_argument("--metrics", default=DEFAULT_METRICS,
                        help="comma-separated metrics to graph")
    args = parser.parse_args()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": MUTED,
        "xtick.color": MUTED, "ytick.color": MUTED, "text.color": INK,
        "axes.spines.top": False, "axes.spines.right": False,
        "grid.color": GRID, "grid.linewidth": 0.6,
        "figure.dpi": 160, "savefig.dpi": 160,
    })

    index, sizes, ratios, arms = load(args.summary)
    compared = [a for a in arms if a != args.baseline]
    args.outdir.mkdir(parents=True, exist_ok=True)
    written = []

    # ---- one figure per metric: all arms, faceted by size ratio ----------
    for metric in [m for m in args.metrics.split(",") if m]:
        fig, axes = plt.subplots(1, len(ratios),
                                 figsize=(3.1 * len(ratios), 3.2),
                                 squeeze=False, sharey=True)
        drew = False
        for axis, ratio in zip(axes[0], ratios):
            for arm in arms:
                color, label = SERIES[arm]
                xs, ys = [], []
                for size in sizes:
                    value = mean_at(index, size, ratio, arm, metric)
                    if value is not None:
                        xs.append(size)
                        ys.append(value)
                if xs:
                    drew = True
                    axis.plot(xs, ys, marker="o", ms=5, lw=2, color=color,
                              label=label, markeredgecolor="#fcfcfb",
                              markeredgewidth=0.9, zorder=3)
            axis.set_xscale("log")
            axis.set_xticks(sizes, [f"{s}M" for s in sizes])
            axis.minorticks_off()
            axis.grid(True, alpha=0.6)
            axis.set_axisbelow(True)
            axis.set_title(f"T = {ratio}", fontsize=9.5, color=INK, loc="left")
            axis.set_xlabel("workload size")
        if not drew:
            plt.close(fig)
            continue
        axes[0][0].set_ylabel(PRETTY.get(metric, metric))
        handles, labels = axes[0][0].get_legend_handles_labels()
        legend = fig.legend(handles, labels, loc="lower center",
                            ncol=len(labels), frameon=False, fontsize=8.5,
                            bbox_to_anchor=(0.5, -0.02))
        for text in legend.get_texts():
            text.set_color(INK)
        fig.suptitle(PRETTY.get(metric, metric), x=0.02, ha="left",
                     fontsize=11, fontweight="semibold")
        fig.tight_layout(rect=(0, 0.07, 1, 0.92))
        path = args.outdir / f"metric_{metric}.png"
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        written.append(path)

    # ---- trade-off plane -------------------------------------------------
    fig, ax = plt.subplots(figsize=(6.2, 4.6))
    ax.axhline(0, color=GRID, lw=1, zorder=1)
    ax.axvline(0, color=GRID, lw=1, zorder=1)
    centroids = []
    for arm in compared:
        color, label = SERIES[arm]
        wa = paired(index, sizes, ratios, arm, "write_amplification", args.baseline)
        pr = paired(index, sizes, ratios, arm, "point_read_amplification", args.baseline)
        cells = sorted({c for c, _ in wa})
        px = [statistics.fmean([v for c, v in wa if c == cell]) * 100 for cell in cells]
        py = [statistics.fmean([v for c, v in pr if c == cell]) * 100 for cell in cells]
        if not px:
            continue
        ax.scatter(px, py, s=42, color=color, edgecolor="#fcfcfb", linewidth=1.2,
                   zorder=3, label=label)
        centroids.append((statistics.fmean(px), statistics.fmean(py), label))
    ax.set_xlabel(f"write amplification vs {SERIES[args.baseline][1]}  (%)")
    ax.set_ylabel(f"point-read amplification vs {SERIES[args.baseline][1]}  (%)")
    ax.set_title("Each point is one workload size × size ratio; "
                 "the objective is the lower-left quadrant",
                 color=MUTED, fontsize=8.5, loc="left", pad=10)
    ax.grid(True, alpha=0.6)
    ax.set_axisbelow(True)
    ax.margins(0.18)
    centroids.sort(key=lambda item: item[1])
    for rank, (cx, cy, label) in enumerate(centroids):
        ax.annotate(label, (cx, cy), textcoords="offset points",
                    xytext=(10, 9 + 11 * (rank % 2)), color=INK, fontsize=8.5,
                    fontweight="medium", annotation_clip=True,
                    bbox=dict(boxstyle="round,pad=0.18", fc="#fcfcfb",
                              ec="none", alpha=0.85))
    legend = ax.legend(loc="lower left", frameon=False, fontsize=8.5)
    for text in legend.get_texts():
        text.set_color(INK)
    fig.suptitle("Where each arm sits on the read/write trade-off",
                 x=0.02, ha="left", fontsize=11, fontweight="semibold")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    path = args.outdir / "tradeoff.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    written.append(path)

    # ---- paired differences with intervals ------------------------------
    fig, axes = plt.subplots(1, len(compared),
                             figsize=(3.1 * len(compared), 3.6),
                             sharex=True, squeeze=False)
    report = []
    for axis, arm in zip(axes[0], compared):
        color, label = SERIES[arm]
        means, los, his, names = [], [], [], []
        for metric in [m for m in args.metrics.split(",") if m]:
            values = [v for _, v in paired(index, sizes, ratios, arm, metric,
                                           args.baseline)]
            if not values:
                continue
            interval = ci95(values)
            need, _ = required_pairs(values, 0.0)
            means.append(interval["mean"] * 100)
            los.append((interval["mean"] - (interval["lower"] or 0)) * 100)
            his.append(((interval["upper"] or 0) - interval["mean"]) * 100)
            names.append(PRETTY.get(metric, metric))
            report.append((label, PRETTY.get(metric, metric), interval,
                           need, len(values)))
        axis.axvline(0, color=GRID, lw=1)
        axis.errorbar(means, range(len(names)), xerr=[los, his], fmt="o", ms=6,
                      color=color, ecolor=color, elinewidth=2, capsize=0,
                      markeredgecolor="#fcfcfb", markeredgewidth=1.1, zorder=3)
        axis.set_yticks(range(len(names)), names)
        axis.set_title(label, fontsize=9.5, loc="left", color=INK)
        axis.grid(True, axis="x", alpha=0.6)
        axis.set_axisbelow(True)
        axis.invert_yaxis()
        axis.set_xlabel(f"difference vs {SERIES[args.baseline][1]}  (%)")
    fig.suptitle("Paired difference with 95% interval — right of zero is worse",
                 x=0.02, ha="left", fontsize=11, fontweight="semibold")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    path = args.outdir / "paired_difference.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    written.append(path)

    # Plain-text numbers behind the figures. Two palette slots fall below 3:1
    # against the surface, which obliges a readable non-color view.
    summary_path = args.outdir / "paired_difference.txt"
    with summary_path.open("w") as handle:
        handle.write(f"paired differences vs {SERIES[args.baseline][1]}\n")
        handle.write(f"{'arm':20s}{'metric':28s}{'mean%':>9s}{'lo%':>9s}"
                     f"{'hi%':>9s}{'n':>5s}{'need':>7s}\n")
        for label, metric, interval, need, n in report:
            handle.write(
                f"{label:20s}{metric:28s}{interval['mean']*100:+9.2f}"
                f"{(interval['lower'] or 0)*100:+9.2f}"
                f"{(interval['upper'] or 0)*100:+9.2f}{n:5d}"
                f"{(need or '>200'):>7}\n")
    written.append(summary_path)

    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
