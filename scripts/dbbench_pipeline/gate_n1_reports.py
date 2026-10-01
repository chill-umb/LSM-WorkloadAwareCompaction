#!/usr/bin/env python3
"""What Gate N1's admission reports (19, admission_T<T>.json) fix for Gate N2
(PREREGISTRATION D-16 §6, D-19). Run as a script, prints a workload's run length,
"<size in millions> <load percent>" (24 and 25); 26_record_qbar.py imports it."""

from __future__ import annotations

import json
import sys
from pathlib import Path

N1_SIZE_RATIOS = (2, 6, 10)


def report(n1_dir: Path, t: int) -> dict:
    path = Path(n1_dir) / f"admission_T{t}.json"
    if not path.exists():
        raise ValueError(f"no Gate N1 report {path}: run 24 first")
    return json.loads(path.read_text())


def mixgraph_operations(rung: dict) -> int:
    total = rung["size_millions"] * 10**6
    return total - total * rung["load_percent"] // 100


def workload_rung(n1_dir: Path) -> tuple[int, int]:
    """D-16 §6: the longest of the workload's three cells' rungs, so C-6
    compares J_beta across T over the same operations."""
    rungs = []
    for t in N1_SIZE_RATIOS:
        length = report(n1_dir, t).get("run_length")
        if not isinstance(length, dict) or "rung" not in length:
            raise ValueError(f"Gate N1 report T={t} has no run_length: rerun 19")
        if length["rung"] is None:
            raise ValueError(f"T={t}: no rung is long enough for "
                             f"L{length['level']} (D-16 §6): stop and report")
        rungs.append(length["rung"])
    best = max(rungs, key=mixgraph_operations)
    return best["size_millions"], best["load_percent"]


if __name__ == "__main__":
    try:
        print(*workload_rung(Path(sys.argv[1])))
    except ValueError as error:
        raise SystemExit(str(error)) from error
