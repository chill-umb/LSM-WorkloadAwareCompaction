"""The host log db_bench writes with --rl_host_log (plan WP4). Its records
are documented in lib/rocksdb/db/rl_controller_host.h: header, h (H with the
operation count after every flush and compaction install), job_begin and
job_end (per compaction: levels, trivial flag, S, O, X, due-since) and stamp
(a named phase point with the operation count, H, the stall counter, the
per-level read counters and every ticker).
"""

from __future__ import annotations

import json
from pathlib import Path

FILE_NAME = "host_log.jsonl"
# A stamp's levels[i] row order (db/rl_read_counters.h) and each kind's ticker.
LEVEL_TICKERS = ("rocksdb.point.sst.probe", "rocksdb.bloom.filter.full.positive",
                 "rocksdb.bloom.filter.full.true.positive",
                 "rocksdb.sorted.run.seek")


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines()
            if line.strip()]


def stamp_index(records: list[dict]) -> dict[str, int]:
    """Position of each stamp by name. A repeated name is an error: the
    phase it marks would be ambiguous."""
    found: dict[str, int] = {}
    for index, record in enumerate(records):
        if record.get("type") == "stamp":
            if record["name"] in found:
                raise ValueError(f"stamp {record['name']} appears twice")
            found[record["name"]] = index
    return found


def check(records: list[dict]) -> list[str]:
    """What is wrong with one run's host log; empty when it is consistent:
    header first, the measure_start and drain_end stamps present, the
    per-level read counters summing to their tickers at drain_end, the last H
    sample equal to drain_end's live SST bytes, every job that began ended,
    and operation counts that never decrease."""
    problems = []
    if not records or records[0].get("type") != "header":
        problems.append("no header first")
    try:
        stamps = {name: records[i] for name, i in stamp_index(records).items()}
    except ValueError as error:
        return problems + [str(error)]
    problems += [f"no {name} stamp" for name in ("measure_start", "drain_end")
                 if name not in stamps]
    end = stamps.get("drain_end")
    if end:
        for kind, ticker in enumerate(LEVEL_TICKERS):
            total = sum(row[kind] for row in end["levels"])
            if total != end["tickers"].get(ticker):
                problems.append(f"levels sum {total} != {ticker} "
                                f"{end['tickers'].get(ticker)}")
        samples = [r for r in records if r.get("type") == "h"]
        if not samples or samples[-1]["h"] != end["h"]:
            problems.append("last H sample != live SST bytes at drain_end")
    begins = sum(r.get("type") == "job_begin" for r in records)
    ends = sum(r.get("type") == "job_end" for r in records)
    if begins != ends:
        problems.append(f"{begins} jobs began, {ends} ended")
    ops = [r["op"] for r in records if "op" in r]
    if any(later < earlier for earlier, later in zip(ops, ops[1:])):
        problems.append("operation count decreased")
    return problems


def problems(path: Path) -> list[str]:
    try:
        records = load(path)
    except (OSError, json.JSONDecodeError) as error:
        return [f"unreadable: {error}"]
    return check(records)
