"""The host log db_bench writes with --rl_host_log (plan WP4). Its records
are documented in lib/rocksdb/db/rl_controller_host.h: header, h (H with the
operation count after every flush and compaction install), job_begin and
job_end (per compaction: levels, trivial flag, S, O, X, due-since) and stamp
(a named phase point with the operation count, H, the stall counter, the
per-level read counters and every ticker). Schema 2 (D-21) adds three
counters to each level's row: Get reopens, iterator reopens and their time.
Schema 3 (PREREGISTRATION D-23 §3(a), D-24 §2; the interim binary) adds a
fourth, the hidden steps that live at the level; the header names the
foreground-step counters "fg" and the snapshot stride; job_begin and job_end
carry fg; flush_begin and flush_end records (flush_end with the flush's bytes
"x" and file count) carry fg; and snap records (op and fg) follow every
stride operations. A phased workload (PREREGISTRATION D-29 §2, W5) adds a
phase_start stamp, with the phase that starts, at every switch.
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
# Schema 3: kind 7, the hidden steps per level. A memtable's and a reverse
# step's are not counted per level, so the levels sum to at most the ticker.
HIDDEN, HIDDEN_TICKER = 7, "rocksdb.number.iter.skip"
ROW_WIDTH = {1: 4, 2: 7, 3: 8}
# Records that carry the fg counters (schema 3).
FG_RECORDS = ("job_begin", "job_end", "flush_begin", "flush_end", "snap")


def fg_names(records: list[dict]) -> list[str]:
    """The header's fg ticker names; empty before schema 3."""
    return list(records[0].get("fg", [])) if records else []


def fg_of(record: dict, names: list[str]) -> list[int]:
    """A record's fg counters, in the header's order: its own list, or, for a
    stamp, the same tickers read from its ticker map."""
    if record.get("type") == "stamp":
        return [int(record["tickers"][name]) for name in names]
    return [int(v) for v in record["fg"]]


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines()
            if line.strip()]


PHASE_START = "phase_start"


def stamp_key(record: dict) -> str:
    """A stamp's name, or for a phased workload's switch (PREREGISTRATION
    D-29 §2, W5) "phase_start:<phase>": db_bench stamps phase_start, with the
    phase that starts, at every switch."""
    if record["name"] == PHASE_START:
        return f"{PHASE_START}:{record.get('phase')}"
    return record["name"]


def stamp_index(records: list[dict]) -> dict[str, int]:
    """Position of each stamp by stamp_key. A repeated key is an error: the
    phase it marks would be ambiguous."""
    found: dict[str, int] = {}
    for index, record in enumerate(records):
        if record.get("type") == "stamp":
            key = stamp_key(record)
            if key in found:
                raise ValueError(f"stamp {key} appears twice")
            found[key] = index
    return found


def phase_bounds(records: list[dict], at: dict[str, int]
                 ) -> list[tuple[int, int, int]]:
    """W5's phases as (phase, first record, last record) indices: phase 1
    from measure_start, each later one from its phase_start, each to the next
    phase's start and the last to drain_end, as the whole run is measured.
    Empty for a run with one phase."""
    starts = sorted((int(key.split(":", 1)[1]), index)
                    for key, index in at.items()
                    if key.startswith(PHASE_START + ":"))
    if not starts:
        return []
    marks = [(1, at["measure_start"])] + starts
    ends = [index for _, index in starts] + [at["drain_end"]]
    return [(phase, first, last) for (phase, first), last in zip(marks, ends)]


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
    problems += _check_phases(records)
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
            if width >= 7:
                sums += [(REOPEN_TICKERS[0], [REOPENS, REOPENS + 1]),
                         (REOPEN_TICKERS[1], [REOPEN_NANOS])]
            for ticker, kinds in sums:
                total = sum(row[k] for row in end["levels"] for k in kinds)
                if total != end["tickers"].get(ticker):
                    problems.append(f"levels sum {total} != {ticker} "
                                    f"{end['tickers'].get(ticker)}")
            if width >= 8:
                hidden = sum(row[HIDDEN] for row in end["levels"])
                if hidden > end["tickers"].get(HIDDEN_TICKER, -1):
                    problems.append(f"levels' hidden steps {hidden} > "
                                    f"{HIDDEN_TICKER} "
                                    f"{end['tickers'].get(HIDDEN_TICKER)}")
        samples = [r for r in records if r.get("type") == "h"]
        if not samples or samples[-1]["h"] != end["h"]:
            problems.append("last H sample != live SST bytes at drain_end")
    begins = sum(r.get("type") == "job_begin" for r in records)
    ends = sum(r.get("type") == "job_end" for r in records)
    if begins != ends:
        problems.append(f"{begins} jobs began, {ends} ended")
    if records and records[0].get("schema", 0) >= 3:
        problems += _check_schema3(records)
    # A listener registered twice writes every record twice and keeps the
    # counts above equal, so each job's records are checked one by one. (A job
    # id is unique within one DB with one column family and no atomic flush,
    # as the pipeline runs; atomic flush over several would repeat one.)
    seen: set[tuple] = set()
    repeated = []
    for r in records:
        if (r.get("type") in ("job_begin", "job_end", "h", "flush_begin",
                              "flush_end") and "job" in r):
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


def _check_phases(records: list[dict]) -> list[str]:
    """W5's phase_start stamps (D-29 §2): phases 2, 3, ... in that order,
    each after measure_start and before drain_start (mixgraph switches
    phase only while it runs)."""
    at = stamp_index(records)
    phases = [(int(key.split(":", 1)[1]) if key.split(":", 1)[1].isdigit()
               else -1, index) for key, index in at.items()
              if key.startswith(PHASE_START + ":")]
    if not phases:
        return []
    phases.sort(key=lambda item: item[1])
    if [p for p, _ in phases] != list(range(2, len(phases) + 2)):
        return [f"phase_start stamps for phases {[p for p, _ in phases]}, "
                f"not 2..{len(phases) + 1} in order"]
    if not ("measure_start" in at and "drain_start" in at and
            at["measure_start"] < phases[0][1] and
            phases[-1][1] < at["drain_start"]):
        return ["phase_start stamps outside measure_start..drain_start"]
    return []


def _check_schema3(records: list[dict]) -> list[str]:
    """Schema 3: the header names fg and the stride; every record that
    carries fg carries as many as the header names, and they never decrease
    in file order (they are read under the log's mutex); every flush_end has
    its flush_begin before it (a flush_begin alone is a failed flush), and
    every flush's H sample comes right after its flush_end."""
    problems = []
    header = records[0]
    names = fg_names(records)
    if not names or not isinstance(header.get("stride"), int):
        return ["schema 3 header without fg names or stride"]
    last = None
    for r in records:
        if r.get("type") not in FG_RECORDS:
            continue
        fg = r.get("fg")
        if not isinstance(fg, list) or len(fg) != len(names):
            problems.append(f"{r.get('type')} record without {len(names)} fg "
                            "counters")
            break
        if last is not None and any(b < a for a, b in zip(last, fg)):
            problems.append(f"fg counters decreased at a {r['type']} record")
            break
        last = fg
    begun: set = set()
    previous = None
    for r in records:
        kind = r.get("type")
        if kind == "flush_begin":
            begun.add(r["job"])
        elif kind == "flush_end" and r["job"] not in begun:
            problems.append(f"flush {r['job']} ended without a flush_begin")
        elif (kind == "h" and r.get("cause") == "flush" and
              (previous is None or previous.get("type") != "flush_end" or
               previous.get("job") != r.get("job"))):
            problems.append(f"flush {r.get('job')}'s H sample does not follow "
                            "its flush_end")
        previous = r
    return problems


def problems(path: Path) -> list[str]:
    try:
        records = load(path)
    except (OSError, json.JSONDecodeError) as error:
        return [f"unreadable: {error}"]
    return check(records)
