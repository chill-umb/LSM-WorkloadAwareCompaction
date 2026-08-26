#!/usr/bin/env python3
"""Publication figures and a LaTeX table from a matrix summary.csv.

04_generate_graphs.py produces diagnostic panels -- every metric, every arm,
overlaid. This produces the two figures and one table that carry an argument:

  fig1  trade-off plane. Each arm's paired difference from `regular` in write
        amplification (x) against point-read amplification (y). The research
        objective lives in the lower-left quadrant, where both improve. An arm
        sitting lower-RIGHT is buying reads with writes, which is a finding
        about the policy rather than a win.
  fig2  paired relative difference per metric with 95% Student-t intervals,
        so a reader can see which differences are decided and which are not.
  table LaTeX, same numbers, for the paper.

Colors are the validated categorical slots (blue/orange/aqua/violet); every
series is direct-labeled as well as legended, because two of the slots sit
below 3:1 against the surface.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_stats import ci95, required_pairs

SERIES = {
    "regular":          ("#2a78d6", "regular"),
    "prior_only":       ("#1baf7a", "prior only"),
    "rl":               ("#eb6834", "rl"),
    "unconstrained_rl": ("#4a3aa7", "rl (no SLO mask)"),
}
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#d8d7d2"

METRICS = [
    ("write_amplification",        "write amp"),
    ("point_read_amplification",   "point-read amp"),
    ("sorted_run_seeks_per_scan",  "seeks / scan"),
    ("space_amplification",        "space amp"),
    ("elapsed_seconds",            "runtime"),
    ("stall_seconds",              "stall time"),
]


def load(path: Path):
    rows = list(csv.DictReader(path.open(newline="")))
    if not rows or "arm" not in rows[0]:
        raise SystemExit(
            f"{path}: not a well-formed summary.csv (no 'arm' column). "
            "If the delimiters were stripped in transfer, re-copy the file.")
    index = {}
    for row in rows:
        key = (int(row["size_millions"]), int(row["size_ratio"]),
               row["arm"], int(row["repeat"]))
        index[key] = row
    cells = sorted({(int(r["size_millions"]), int(r["size_ratio"]))
                    for r in rows})
    arms = [a for a in SERIES if any(r["arm"] == a for r in rows)]
    return index, cells, arms


def paired(index, cells, arm, metric, baseline="regular"):
    """Relative differences against the paired baseline arm, same seed."""
    out = []
    for cell in cells:
        for repeat in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
            a = index.get((*cell, arm, repeat))
            b = index.get((*cell, baseline, repeat))
            if not a or not b:
                continue
            try:
                x, y = float(a[metric]), float(b[metric])
            except (TypeError, ValueError):
                continue
            if y and math.isfinite(x) and math.isfinite(y):
                out.append((cell, x / y - 1.0))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("summary", type=Path)
    parser.add_argument("--outdir", type=Path, default=Path("report"))
    parser.add_argument("--baseline", default="regular")
    parser.add_argument(
        "--metrics",
        default="write_amplification,point_read_amplification",
        help="comma-separated metrics for the absolute-comparison figure")
    args = parser.parse_args()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": MUTED,
        "xtick.color": MUTED, "ytick.color": MUTED, "text.color": INK,
        "axes.spines.top": False, "axes.spines.right": False,
        "grid.color": GRID, "grid.linewidth": 0.6, "figure.dpi": 200,
    })

    index, cells, arms = load(args.summary)
    compared = [a for a in arms if a != args.baseline]
    cell_sizes = sorted({c[0] for c in cells})
    cell_ratios = sorted({c[1] for c in cells})
    args.outdir.mkdir(parents=True, exist_ok=True)

    # ---- fig 1: the trade-off plane ------------------------------------
    fig, ax = plt.subplots(figsize=(6.2, 4.6))
    ax.axhline(0, color=GRID, lw=1, zorder=1)
    ax.axvline(0, color=GRID, lw=1, zorder=1)
    centroids = []
    for arm in compared:
        color, label = SERIES[arm]
        wa = paired(index, cells, arm, "write_amplification", args.baseline)
        pr = paired(index, cells, arm, "point_read_amplification", args.baseline)
        px, py = [], []
        for cell in cells:
            cx = [v for c, v in wa if c == cell]
            cy = [v for c, v in pr if c == cell]
            if cx and cy:
                px.append(statistics.fmean(cx) * 100)
                py.append(statistics.fmean(cy) * 100)
        ax.scatter(px, py, s=42, color=color, edgecolor="#fcfcfb", linewidth=1.2,
                   zorder=3, label=label)
        if px:
            centroids.append((statistics.fmean(px), statistics.fmean(py),
                              label, color))
    ax.set_xlabel(f"write amplification vs {args.baseline}  (%)")
    ax.set_ylabel(f"point-read amplification vs {args.baseline}  (%)")
    ax.set_title("Each point is one workload size × size ratio",
                 color=MUTED, fontsize=8.5, loc="left", pad=10)
    ax.grid(True, axis="both", alpha=0.6)
    ax.set_axisbelow(True)
    ax.margins(0.18)
    # Direct labels are placed after the limits settle, fanned vertically so
    # arms that cluster near the origin do not overprint each other, and
    # clipped inside the axes so a long name cannot overflow the figure.
    centroids.sort(key=lambda item: item[1])
    for rank, (cx, cy, label, color) in enumerate(centroids):
        offset = (10, 9 + 11 * (rank % 2))
        ax.annotate(label, (cx, cy), textcoords="offset points", xytext=offset,
                    color=INK, fontsize=8.5, fontweight="medium",
                    annotation_clip=True,
                    bbox=dict(boxstyle="round,pad=0.18", fc="#fcfcfb",
                              ec="none", alpha=0.85))
    legend = ax.legend(loc="lower left", frameon=False, fontsize=8.5,
                       handletextpad=0.5, borderaxespad=0.2)
    for text in legend.get_texts():
        text.set_color(INK)
    fig.suptitle("Where each arm sits on the read/write trade-off",
                 x=0.02, ha="left", fontsize=11, fontweight="semibold")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(args.outdir / "fig1_tradeoff.pdf")
    fig.savefig(args.outdir / "fig1_tradeoff.png")
    plt.close(fig)

    # ---- fig 2: paired differences with intervals -----------------------
    fig, axes = plt.subplots(1, len(compared), figsize=(3.1 * len(compared), 3.6),
                             sharex=True, squeeze=False)
    rows_out = []
    for axis, arm in zip(axes[0], compared):
        color, label = SERIES[arm]
        means, los, his, names = [], [], [], []
        for key, pretty in METRICS:
            values = [v for _, v in paired(index, cells, arm, key, args.baseline)]
            if not values:
                continue
            interval = ci95(values)
            need, _ = required_pairs(values, 0.0)
            means.append(interval["mean"] * 100)
            los.append((interval["mean"] - (interval["lower"] or 0)) * 100)
            his.append(((interval["upper"] or 0) - interval["mean"]) * 100)
            names.append(pretty)
            rows_out.append((arm, pretty, interval, need, len(values)))
        y = range(len(names))
        axis.axvline(0, color=GRID, lw=1)
        axis.errorbar(means, list(y), xerr=[los, his], fmt="o", ms=6,
                      color=color, ecolor=color, elinewidth=2, capsize=0,
                      markeredgecolor="#fcfcfb", markeredgewidth=1.1, zorder=3)
        axis.set_yticks(list(y), names)
        axis.set_title(label, fontsize=9.5, loc="left", color=INK)
        axis.grid(True, axis="x", alpha=0.6)
        axis.set_axisbelow(True)
        axis.invert_yaxis()
    for axis in axes[0]:
        axis.set_xlabel(f"difference vs {args.baseline}  (%)")
    fig.suptitle("Paired difference with 95% interval — right of zero is worse",
                 x=0.02, ha="left", fontsize=11, fontweight="semibold")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(args.outdir / "fig2_paired.pdf")
    fig.savefig(args.outdir / "fig2_paired.png")
    plt.close(fig)

    # ---- fig 3: absolute levels, arms side by side ----------------------
    # figs 1-2 plot differences, which puts the baseline at the origin and
    # hides both its absolute level and how the metric scales with workload
    # size. This is the direct baseline-vs-arm comparison.
    show = [m for m in args.metrics.split(",") if m]
    pretty = dict(METRICS)
    fig, axes = plt.subplots(len(show), len(cell_ratios),
                             figsize=(2.6 * len(cell_ratios), 2.5 * len(show)),
                             squeeze=False, sharex=True)
    for row, metric in enumerate(show):
        row_axes = axes[row]
        for col, ratio in enumerate(cell_ratios):
            axis = row_axes[col]
            for arm in arms:
                color, label = SERIES[arm]
                xs, ys = [], []
                for size in cell_sizes:
                    values = []
                    for repeat in range(1, 11):
                        row_data = index.get((size, ratio, arm, repeat))
                        if not row_data:
                            continue
                        try:
                            value = float(row_data[metric])
                        except (TypeError, ValueError):
                            continue
                        if math.isfinite(value):
                            values.append(value)
                    if values:
                        xs.append(size)
                        ys.append(statistics.fmean(values))
                if xs:
                    axis.plot(xs, ys, marker="o", ms=5, lw=2, color=color,
                              label=label, markeredgecolor="#fcfcfb",
                              markeredgewidth=0.9, zorder=3)
            axis.set_xscale("log")
            axis.set_xticks(cell_sizes, [f"{s}M" for s in cell_sizes])
            axis.minorticks_off()
            axis.grid(True, alpha=0.6)
            axis.set_axisbelow(True)
            if row == 0:
                axis.set_title(f"T = {ratio}", fontsize=9.5, color=INK, loc="left")
            if col == 0:
                axis.set_ylabel(pretty.get(metric, metric))
            if row == len(show) - 1:
                axis.set_xlabel("workload size")
    handles, labels = axes[0][0].get_legend_handles_labels()
    legend = fig.legend(handles, labels, loc="lower center", ncol=len(labels),
                        frameon=False, fontsize=8.5, bbox_to_anchor=(0.5, -0.01))
    for text in legend.get_texts():
        text.set_color(INK)
    fig.suptitle("Absolute levels: every arm against the leveled baseline",
                 x=0.02, ha="left", fontsize=11, fontweight="semibold")
    fig.tight_layout(rect=(0, 0.05, 1, 0.94))
    fig.savefig(args.outdir / "fig3_absolute.pdf")
    fig.savefig(args.outdir / "fig3_absolute.png")
    plt.close(fig)

    # ---- LaTeX table -----------------------------------------------------
    lines = [
        r"\begin{tabular}{llrrrr}", r"\toprule",
        r"Arm & Metric & Mean (\%) & \multicolumn{2}{c}{95\% CI (\%)} & Pairs needed \\",
        r"\midrule",
    ]
    current = None
    for arm, pretty, interval, need, n in rows_out:
        name = SERIES[arm][1] if arm != current else ""
        current = arm
        lo = f"{interval['lower']*100:+.2f}" if interval["lower"] is not None else "--"
        hi = f"{interval['upper']*100:+.2f}" if interval["upper"] is not None else "--"
        needs = str(need) if need else r"$>$200"
        lines.append(f"{name} & {pretty} & {interval['mean']*100:+.2f} "
                     f"& {lo} & {hi} & {needs} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    (args.outdir / "table_paired.tex").write_text("\n".join(lines) + "\n")

    # ---- plain-text mirror (the contrast WARN's required relief) ---------
    with (args.outdir / "table_paired.txt").open("w") as handle:
        handle.write(f"paired differences vs {args.baseline}\n")
        handle.write(f"{'arm':18s}{'metric':18s}{'mean%':>9s}"
                     f"{'lo%':>9s}{'hi%':>9s}{'n':>5s}{'need':>7s}\n")
        for arm, pretty, interval, need, n in rows_out:
            handle.write(
                f"{SERIES[arm][1]:18s}{pretty:18s}{interval['mean']*100:+9.2f}"
                f"{(interval['lower'] or 0)*100:+9.2f}{(interval['upper'] or 0)*100:+9.2f}"
                f"{n:5d}{(need or '>200'):>7}\n")

    print(f"figures: {args.outdir}/fig1_tradeoff.{{pdf,png}}")
    print(f"         {args.outdir}/fig2_paired.{{pdf,png}}")
    print(f"         {args.outdir}/fig3_absolute.{{pdf,png}}")
    print(f"tables:  {args.outdir}/table_paired.{{tex,txt}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
