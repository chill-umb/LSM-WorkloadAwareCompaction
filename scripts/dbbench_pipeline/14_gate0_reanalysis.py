#!/usr/bin/env python3
"""Gate-0 re-analysis of existing paired artifacts (PATHWAYS Gate 0, items 3-4).

Item 3, A-0: split each arm's write-byte excess over its paired `regular` run
into D_depth (bytes written at levels `regular` never populated) and D_eager
(excess at levels both populated). Flush bytes count at L0.

Item 4: flow space ratio S_flow = user bytes written / live logical bytes,
cumulative garbage g_flow = 1 - 1/S_flow, and the Theorem B.1 elision ceiling
at eta_min = 1/S_flow and the measured populated depth.

Inputs are the pipeline's summary.csv (04_generate_graphs.py) for the exact
byte tickers and amplifications, and each arm's run.log under the standard
<results>/<size>M/T<ratio>/repeat-NN/<arm>/ layout for the per-level table, so
the analysis runs on artifacts copied off-box without COMPLETED markers.

Per-level write bytes are exact when the arm kept rocksdb_LOG.txt: flush
ticker at L0 plus `compaction_finished` output bytes per output level. The
historical 10M arms retain only run.log, whose final `** Compaction Stats
[default] **` table prints Write(GB) at 0.1 GB resolution, so those per-level
figures carry a +/-0.05 GB quantisation error; the sum is reconciled against
the exact byte tickers and the residual is reported. Measured per-level eta
needs the merge_schema_version 1 events that only the rebuilt binary writes; it
is reported absent here.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
import math
from pathlib import Path
import re

from pipeline_stats import ci95

GIB = 1024 ** 3
LEVEL_ROW = re.compile(r"^\s*L(\d+)\s+(\d+)/\d+\s+([\d.]+)\s+([KMGT]?B)\s+(.*)$")
QUANTISATION_BYTES = 0.05 * GIB
PREREGISTERED_REPEATS = 5


def parse_level_stats(text: str) -> dict[int, dict]:
    """Last per-level compaction-stats block of a db_bench `stats` dump."""
    blocks = [m.end() for m in re.finditer(r"\*\* Compaction Stats \[default\] \*\*", text)]
    for start in reversed(blocks):
        lines = text[start:].splitlines()
        header = next((l for l in lines[:3] if l.strip().startswith("Level")), None)
        if header is None:
            continue
        names = header.split()
        # "Size" prints as two tokens; the remaining columns are single tokens.
        tail = names[names.index("Score"):]
        columns = {name: i for i, name in enumerate(tail)}
        for key in ("Write(GB)", "Moved(GB)", "Comp(cnt)"):
            if key not in columns:
                raise ValueError(f"compaction stats table lacks {key}")
        levels = {}
        for line in lines[1:]:
            match = LEVEL_ROW.match(line)
            if not match:
                if levels and not line.strip():
                    break
                continue
            rest = match.group(5).split()
            levels[int(match.group(1))] = {
                "files": int(match.group(2)),
                "write_bytes": float(rest[columns["Write(GB)"]]) * GIB,
                "moved_bytes": float(rest[columns["Moved(GB)"]]) * GIB,
                "compactions": int(rest[columns["Comp(cnt)"]]),
            }
        if levels:
            return levels
    raise ValueError("no per-level compaction stats table")


EVENT = re.compile(r"EVENT_LOG_v1 (\{.*\})")


def event_level_writes(log: Path, flush_bytes: float) -> dict[int, float] | None:
    """Exact bytes written into each level, or None when no event log exists."""
    if not log.exists():
        return None
    writes = defaultdict(float)
    writes[0] += flush_bytes
    with log.open(errors="replace") as handle:
        for line in handle:
            if "EVENT_LOG_v1" not in line:
                continue
            match = EVENT.search(line)
            if not match:
                continue
            event = json.loads(match.group(1))
            if event.get("event") == "compaction_finished":
                writes[int(event["output_level"])] += float(event["total_output_size"])
    return dict(writes)


def populated(levels: dict[int, dict]) -> set[int]:
    # A level counts as populated if it ever received bytes, by write or move,
    # or holds files at the settled end of the run.
    return {i for i, s in levels.items()
            if s["files"] or s["write_bytes"] or s["moved_bytes"]}


def decompose(regular: dict[int, dict], arm: dict[int, dict], exact: bool) -> dict:
    shared = populated(regular)
    depth_levels = sorted(set(arm) - shared)
    depth = sum(arm[i]["write_bytes"] for i in depth_levels)
    eager = sum(arm.get(i, {}).get("write_bytes", 0.0) -
                regular.get(i, {}).get("write_bytes", 0.0) for i in shared)
    regular_total = sum(s["write_bytes"] for s in regular.values())
    arm_total = sum(s["write_bytes"] for s in arm.values())
    excess = arm_total - regular_total
    if not math.isclose(depth + eager, excess, rel_tol=1e-9, abs_tol=1.0):
        raise ValueError("D_depth + D_eager does not reconcile with the excess")
    return {"regular_populated_levels": sorted(shared),
            "depth_levels": depth_levels,
            "regular_write_bytes": regular_total, "arm_write_bytes": arm_total,
            "excess_bytes": excess, "excess_relative": excess / regular_total,
            "d_depth_bytes": depth, "d_eager_bytes": eager,
            "d_depth_fraction": depth / excess if excess > 0 else None,
            "quantisation_bytes": 0.0 if exact else
            QUANTISATION_BYTES * (len(regular) + len(arm))}


def flow(row: dict, levels: dict[int, dict]) -> dict:
    written = float(row["user_write_bytes"])
    live = float(row["live_logical_bytes"])
    if not (written > 0 and live > 0):
        raise ValueError("flow ratio needs positive written and live bytes")
    s_flow = written / live
    eta_min = 1.0 / s_flow
    stages = len(populated(levels)) - 1  # fanout terms i = 0..L-1 in Theorem A.2
    ceiling = None
    if stages >= 1 and eta_min < 1.0:
        ceiling = 1.0 - (1.0 - eta_min ** stages) / (stages * (1.0 - eta_min))
    return {"s_flow": s_flow, "g_flow": 1.0 - eta_min, "eta_min": eta_min,
            "space_amplification": float(row["space_amplification"]),
            "populated_levels": stages + 1, "merge_stages": stages,
            "b1_ceiling_relative_w_minus_1": ceiling,
            "b1_small_garbage_approximation": (stages - 1) * (1.0 - eta_min) / 2
            if stages >= 1 else None}


def interval(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return ci95(values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results", type=Path,
                        help="experiment root holding <size>M/T<ratio>/repeat-NN/<arm>/")
    parser.add_argument("--summary", type=Path,
                        help="summary.csv from 04_generate_graphs.py (default: results/graphs/summary.csv)")
    parser.add_argument("--baseline-arm", default="regular")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = args.summary or args.results / "graphs" / "summary.csv"

    runs = defaultdict(dict)  # (size, ratio, repeat) -> arm -> record
    missing_logs = []
    with summary.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit(f"{summary}: no arms")
    for row in rows:
        directory = (args.results / f"{int(row['size_millions'])}M" /
                     f"T{int(row['size_ratio'])}" / f"repeat-{int(row['repeat']):02d}" /
                     row["arm"])
        log = directory / "run.log"
        if not log.exists():
            missing_logs.append(str(directory))
            continue
        text = log.read_text(errors="replace")
        levels = parse_level_stats(text)
        exact = event_level_writes(directory / "rocksdb_LOG.txt",
                                   float(row["flush_write_bytes"]))
        if exact is not None:
            for level, written in exact.items():
                levels.setdefault(level, {"files": 0, "moved_bytes": 0.0,
                                          "compactions": 0})["write_bytes"] = written
            for level in set(levels) - set(exact):
                levels[level]["write_bytes"] = 0.0
        table = sum(s["write_bytes"] for s in levels.values())
        tickers = float(row["flush_write_bytes"]) + float(row["compaction_write_bytes"])
        key = (int(row["size_millions"]), int(row["size_ratio"]), int(row["repeat"]))
        if row["arm"] in runs[key]:
            raise ValueError(f"duplicate arm in {directory}")
        runs[key][row["arm"]] = {
            "row": row, "levels": levels, "directory": str(directory),
            "write_bytes_source": "event_log_exact" if exact is not None else "stats_table_0.1GB",
            "table_write_bytes": table, "ticker_write_bytes": tickers,
            "table_ticker_residual": (table - tickers) / tickers if tickers else None}

    cells = {}
    for (size, ratio, repeat), arms in sorted(runs.items()):
        base = arms.get(args.baseline_arm)
        if base is None:
            continue
        cell = cells.setdefault(f"{size}M-T{ratio}", {
            "size_millions": size, "size_ratio": ratio, "repeats": {},
            "arms": defaultdict(lambda: {"per_repeat": {}})})
        cell["repeats"][repeat] = {
            "baseline_directory": base["directory"],
            "baseline_flow": flow(base["row"], base["levels"]),
            "baseline_table_ticker_residual": base["table_ticker_residual"]}
        for arm, record in arms.items():
            if arm == args.baseline_arm:
                continue
            cell["arms"][arm]["per_repeat"][repeat] = {
                "directory": record["directory"],
                "table_ticker_residual": record["table_ticker_residual"],
                "write_bytes_source": record["write_bytes_source"],
                **decompose(base["levels"], record["levels"],
                            exact=(record["write_bytes_source"] == base["write_bytes_source"]
                                   == "event_log_exact")),
                "flow": flow(record["row"], record["levels"])}

    for cell in cells.values():
        n = len(cell["repeats"])
        cell["partial"] = n < PREREGISTERED_REPEATS
        flows = [r["baseline_flow"] for r in cell["repeats"].values()]
        cell["baseline_flow_summary"] = {
            key: interval([f[key] for f in flows if f[key] is not None])
            for key in ("s_flow", "g_flow", "space_amplification",
                        "b1_ceiling_relative_w_minus_1", "populated_levels")}
        cell["measured_eta"] = "absent: historical logs predate merge_schema_version 1"
        for arm, block in cell["arms"].items():
            reps = block["per_repeat"]
            block["pairs"] = len(reps)
            block["summary"] = {
                key: interval([r[key] for r in reps.values() if r[key] is not None])
                for key in ("excess_bytes", "excess_relative", "d_depth_bytes",
                            "d_eager_bytes", "d_depth_fraction")}
            block["depth_levels_added"] = sorted(
                {lvl for r in reps.values() for lvl in r["depth_levels"]})
            block["worst_table_ticker_residual"] = max(
                (abs(r["table_ticker_residual"]) for r in reps.values()
                 if r["table_ticker_residual"] is not None), default=None)
        cell["arms"] = dict(cell["arms"])

    report = {
        "schema_version": 1, "results": str(args.results),
        "summary": str(summary), "baseline_arm": args.baseline_arm,
        "arms_without_run_log": missing_logs,
        "definitions": {
            "d_depth": "arm write bytes at levels the paired regular run never populated",
            "d_eager": "arm minus regular write bytes at levels regular populated",
            "populated": "files at settled end, or any bytes written or moved in",
            "write_bytes_source": "rocksdb_LOG.txt compaction_finished output bytes plus flush ticker when present; else final db_bench stats table at 0.1 GB resolution; flush at L0",
            "s_flow": "rocksdb.bytes.written / rocksdb.estimate-live-data-size",
            "b1_ceiling": "1 - (1 - eta^L)/(L (1 - eta)), eta = 1/S_flow, L = merge stages = populated levels - 1",
        },
        "cells": cells,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    for name, cell in cells.items():
        base = cell["baseline_flow_summary"]
        print(f"{name}: S_flow {base['s_flow']['mean']:.3f} g_flow {base['g_flow']['mean']:.3f} "
              f"L {base['populated_levels']['mean']:.0f} "
              f"B.1 ceiling {base['b1_ceiling_relative_w_minus_1']['mean']:.3f}"
              f"{' (partial)' if cell['partial'] else ''}")
        for arm, block in cell["arms"].items():
            s = block["summary"]
            frac = s["d_depth_fraction"]
            print(f"  {arm:16s} pairs {block['pairs']} excess {s['excess_relative']['mean']:+.3f} "
                  f"D_depth {s['d_depth_bytes']['mean'] / GIB:+.2f} GiB "
                  f"D_eager {s['d_eager_bytes']['mean'] / GIB:+.2f} GiB "
                  f"depth share {frac['mean'] if frac['n'] else float('nan'):.3f} "
                  f"levels {block['depth_levels_added']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
