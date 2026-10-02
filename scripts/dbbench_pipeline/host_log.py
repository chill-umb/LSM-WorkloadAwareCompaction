"""The host log db_bench writes with --rl_host_log (plan WP4). Its records
are documented in lib/rocksdb/db/rl_controller_host.h: header, h (H with the
operation count after every flush and compaction install), job_begin and
job_end (per compaction: levels, trivial flag, S, O, X, due-since) and stamp
(a named phase point with the operation count, H, the stall counter, the
per-level read counters and every ticker). Schema 2 (D-21) adds three
counters to each level's row: Get reopens, iterator reopens and their time.
"""

from __future__ import annotations

import json
from pathlib import Path

FILE_NAME = "host_log.jsonl"
# A stamp's levels[i] row order (db/rl_read_counters.h) and each kind's ticker.
LEVEL_TICKERS = ("rocksdb.point.sst.probe", "rocksdb.bloom.filter.full.positive",
                 "rocksdb.bloom.filter.full.true.positive",
                 "rocksdb.sorted.run.seek")
# Schema 2 (D-21): kinds 4 and 5, Get and iterator reopens, sum to one ticker;
# kind 6 is their nanoseconds.
REOPENS, REOPEN_NANOS = 4, 6
REOPEN_TICKERS = ("rocksdb.read.table.reopen", "rocksdb.read.table.reopen.nanos")
ROW_WIDTH = {1: 4, 2: 7}


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
    per-level read counters (schema 1 or 2, each row as wide as its schema
    says) summing to their tickers at drain_end, the last H
    sample equal to drain_end's live SST bytes, every job that began ended,
    no job's begin, end or H sample written twice, and operation counts that
    never decrease."""
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
    width = ROW_WIDTH.get(records[0].get("schema")) if records else None
    if records and width is None:
        problems.append(f"host log schema {records[0].get('schema')!r} "
                        f"is not one of {sorted(ROW_WIDTH)}")
    if end and width:
        if any(len(row) != width for row in end["levels"]):
            problems.append(f"drain_end levels rows are not {width} wide")
        else:
            sums = [(LEVEL_TICKERS[kind], [kind]) for kind in range(4)]
            if width == 7:
                sums += [(REOPEN_TICKERS[0], [REOPENS, REOPENS + 1]),
                         (REOPEN_TICKERS[1], [REOPEN_NANOS])]
            for ticker, kinds in sums:
                total = sum(row[k] for row in end["levels"] for k in kinds)
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
    # A listener registered twice writes every record twice and keeps the
    # counts above equal, so each job's records are checked one by one. (A job
    # id is unique within one DB with one column family and no atomic flush,
    # as the pipeline runs; atomic flush over several would repeat one.)
    seen: set[tuple] = set()
    repeated = []
    for r in records:
        if r.get("type") in ("job_begin", "job_end", "h") and "job" in r:
            key = (r["type"], r.get("cause", ""), r["job"])
            if key in seen:
                repeated.append(key)
            seen.add(key)
    if repeated:
        kind, cause, job = repeated[0]
        problems.append(f"job {job} recorded twice ({kind} {cause}".rstrip()
                        + f"); {len(repeated)} repeated records in all")
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
