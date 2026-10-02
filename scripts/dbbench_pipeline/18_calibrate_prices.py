#!/usr/bin/env python3
"""Device prices (PATHWAYS OBJ-2, Gate N0 item 7) by PREREGISTRATION D-15 §3
as amended by D-20: the core-seconds one byte written, one filter probe, one
block-reading probe, one run seek and one table reopen take, times the
contract's price per core-second, with c_s from the contract's storage price.

Reads (18_calibrate_prices.sh wrote them): three settled trees at T = 2, 6
and 10, each with five repeats of readmissing, readrandom and seekrandom
(seek_nexts 0), one db_bench process each, every run in two arms:
"capped", with the experiments' open_files, and "all_open", with
open_files -1, so every table stays open and none is reopened. The arms'
order alternates by repeat. Under <work>/T<T>/r<repeat>/<arm>/<benchmark>/
(stdout.txt, command.txt). Per repeat:
  t_f, t_blk: one least-squares fit over the all-open arm's six
          (tree, benchmark) points of readmissing and readrandom,
            seconds per Get = overhead + t_f (filter probes per Get)
                              + t_blk (block-reading probes per Get),
          probes point.sst.probe, block reads bloom.filter.full.positive.
          readmissing's Gets end filter-rejected, so the trees' depths vary
          the probes; readrandom's read the block where the key is found,
          so on each tree the pair varies the block reads. The overhead
          (key generation, memtable and version lookup) is what every Get
          pays whatever the tree holds, so it is not a probe's price.
  t_sk  = least-squares slope, across the trees, of seconds per seek on run
          seeks per seek (sorted.run.seek) in the all-open arm's seekrandom
  t_open = the capped arm's extra seconds over the all-open arm's, summed
          over the six Get points, divided by its extra reopens
          (rocksdb.no.file.opens), summed the same way: a reopen's whole
          cost, the open() call, reading and parsing the table's footer,
          index and filter, and closing the table it evicts. The capped
          arm's reads pay it; D-15's fit charged it to the probes.
          The arms must read alike (probes, block reads), the capped arm
          must reopen at least MIN_REOPEN_GAP more tables per Get on every
          point, and the all-open arm must reopen almost none.
  reopen timer (D-21; not a price): the capped arm's reopen nanoseconds
          over its reopens, both the fork's own tickers (read.table.reopen
          and its .nanos), summed over the six Get points: the reference
          against which 04 checks every run's own time per reopen. Every
          capped Get point must time some reopens (a binary before D-21
          times none), and no more than it opened.
Writes: the q-bar native arms' rows of 04's summary.csv (D-14 §2), per run
  t_w   = sst_write_seconds / sst_bytes_written, the measured phase's own
          flush and compaction jobs. Each q-bar arm's own time per reopen is
          also reported against the reference, by the contract's
          reopen_time_check, for every workload before Gate N2.
Each price is the median of its per-repeat (or per-run) values, with their
min and max. Nothing is written when any value is not positive, a slope is
not identified or an arm is not what it claims. prices.json (schema 4) also
keeps the reopen timer's median, min and max, every read process's seconds
and tickers per operation and each tree's load command, so the fit can be
audited.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

import research_objective

RESULT = re.compile(r"^(\w+)\s+:.*? ([\d.]+) seconds (\d+) operations;", re.M)
TICKER = re.compile(r"^(rocksdb\.[\w.\-]+) COUNT : (\d+)", re.M)
READS = ("readmissing", "readrandom", "seekrandom")
GETS = ("readmissing", "readrandom")
# The experiments' open_files, and every table kept open (D-20).
ARMS = ("capped", "all_open")
SIZE_RATIOS = (2, 6, 10)
REPEATS = 5
MIN_WRITE_RUNS = 5
# Least spread, across the trees, of filter probes per Get (readmissing) or
# run seeks per seek: below one per operation a slope is not identified
# from timing noise. Least excess of readrandom's block reads per Get over
# readmissing's on each tree: the found key's block read.
MIN_SPAN = 1.0
MIN_BLOCK_GAP = 0.5
# D-20: the capped arm must reopen at least this many more tables per Get
# than the all-open arm on every (tree, Get benchmark), or t_open is not
# identified (2026-10-02's runs: 0.93 to 2.35). The all-open arm opens every
# table once when the DB opens (about 0.006 per operation at 1M reads), so
# more than this share of the capped arm's reopens means it did not keep
# the tables open. The two arms of a point must read alike: probes and block
# reads per Get within PAIR_TOLERANCE (relative) or PAIR_FLOOR (absolute).
MIN_REOPEN_GAP = 0.5
MAX_ALL_OPEN_SHARE = 0.1
PAIR_TOLERANCE, PAIR_FLOOR = 0.01, 0.005
OPEN_FILES = re.compile(r"--open_files=(-?\d+)")
PROBE, BLOCK = "rocksdb.point.sst.probe", "rocksdb.bloom.filter.full.positive"
SEEK = "rocksdb.sorted.run.seek"
FILE_OPENS = "rocksdb.no.file.opens"
REOPENS = research_objective.READ_REOPENS
REOPEN_NANOS = research_objective.READ_REOPEN_NANOS


def parse(text: str, benchmark: str) -> tuple[float, dict[str, float]]:
    """Seconds per operation and tickers per operation of one process."""
    results = [(float(s), int(n)) for name, s, n in RESULT.findall(text)
               if name == benchmark]
    tickers = {name: int(v) for name, v in TICKER.findall(text)}
    if len(results) != 1 or not tickers or results[0][1] <= 0:
        raise ValueError(f"{benchmark}: expected one result line and tickers")
    seconds, operations = results[0]
    return seconds / operations, {k: v / operations for k, v in tickers.items()}


def span(name: str, values: list[float]) -> None:
    if max(values) - min(values) < MIN_SPAN:
        raise ValueError(f"{name}: the trees span {max(values) - min(values):.3f} "
                         f"per operation, under {MIN_SPAN}; slope not identified")


def fit_gets(points: list[tuple[float, float, float]]) -> tuple[float, float]:
    """(t_f, t_blk) of y = a + t_f x + t_blk b by least squares over
    (x, b, y) points: the 2x2 normal equations on centred values."""
    n = len(points)
    mx, mb, my = (sum(p[i] for p in points) / n for i in range(3))
    dx = [p[0] - mx for p in points]
    db = [p[1] - mb for p in points]
    dy = [p[2] - my for p in points]
    sxx, sbb = sum(v * v for v in dx), sum(v * v for v in db)
    sxb = sum(u * v for u, v in zip(dx, db))
    sxy = sum(u * v for u, v in zip(dx, dy))
    sby = sum(u * v for u, v in zip(db, dy))
    det = sxx * sbb - sxb * sxb
    if not det > 1e-9 * sxx * sbb:
        raise ValueError("t_f, t_blk: probes and block reads move together; "
                         "not identified")
    return (sbb * sxy - sxb * sby) / det, (sxx * sby - sxb * sxy) / det


def marginal_times(trees: list[dict[str, str]]) -> dict[str, float]:
    """t_f, t_blk and t_sk from one repeat's all-open runs, where no table is
    reopened; trees: [{benchmark: stdout}]."""
    parsed = [{b: parse(tree[b], b) for b in READS} for tree in trees]
    span("t_f", [p["readmissing"][1].get(PROBE, 0.0) for p in parsed])
    for p in parsed:
        rr, rm = p["readrandom"][1], p["readmissing"][1]
        if rr.get(BLOCK, 0.0) - rm.get(BLOCK, 0.0) < MIN_BLOCK_GAP:
            raise ValueError(f"t_blk: readrandom reads under {MIN_BLOCK_GAP} "
                             "more blocks per Get than readmissing")
    t_f, t_blk = fit_gets([(t.get(PROBE, 0.0), t.get(BLOCK, 0.0), s)
                           for p in parsed for b in ("readmissing", "readrandom")
                           for s, t in [p[b]]])
    seeks = [p["seekrandom"] for p in parsed]
    span("t_sk", [t.get(SEEK, 0.0) for _, t in seeks])
    t_sk = statistics.linear_regression([t.get(SEEK, 0.0) for _, t in seeks],
                                        [s for s, _ in seeks]).slope
    return {"c_f": t_f, "c_blk": t_blk, "c_sk": t_sk}


def alike(name: str, capped: float, all_open: float) -> None:
    if abs(capped - all_open) > max(PAIR_TOLERANCE * abs(capped), PAIR_FLOOR):
        raise ValueError(f"t_open: the arms read differently ({name} "
                         f"{capped:.4f} capped, {all_open:.4f} all-open per Get)")


def reopen_time(trees: list[dict[str, dict[str, str]]]) -> float:
    """t_open from one repeat: the capped arm's extra seconds over the
    all-open arm's on the same Gets, divided by its extra reopens, each
    summed over the trees and Get benchmarks; trees: [{arm: {benchmark:
    stdout}}]."""
    extra_seconds = extra_reopens = 0.0
    for tree in trees:
        for b in GETS:
            (sc, tc), (sa, ta) = (parse(tree[arm][b], b) for arm in ARMS)
            alike("filter probes", tc.get(PROBE, 0.0), ta.get(PROBE, 0.0))
            alike("block reads", tc.get(BLOCK, 0.0), ta.get(BLOCK, 0.0))
            rc, ra = tc.get(FILE_OPENS, 0.0), ta.get(FILE_OPENS, 0.0)
            if ra > MAX_ALL_OPEN_SHARE * rc:
                raise ValueError(f"t_open: the all-open {b} reopened {ra:.4f} "
                                 f"tables per Get against {rc:.4f} capped; it did "
                                 "not keep the tables open")
            if rc - ra < MIN_REOPEN_GAP:
                raise ValueError(f"t_open: the capped {b} reopened only "
                                 f"{rc - ra:.3f} more tables per Get, under "
                                 f"{MIN_REOPEN_GAP}; not identified")
            extra_seconds += sc - sa
            extra_reopens += rc - ra
    return extra_seconds / extra_reopens


def reopen_timer(trees: list[dict[str, dict[str, str]]]) -> float:
    """D-21's reference from one repeat: the capped arm's reopen seconds,
    by the fork's timer, over its reopens, each summed over the trees and Get
    benchmarks; trees: [{arm: {benchmark: stdout}}]."""
    nanos = reopens = 0.0
    for tree in trees:
        for b in GETS:
            _, tickers = parse(tree["capped"][b], b)
            r, n = tickers.get(REOPENS, 0.0), tickers.get(REOPEN_NANOS, 0.0)
            if not (r > 0 and n > 0):
                raise ValueError(f"reopen timer: the capped {b} timed no "
                                 "reopens; this db_bench does not count them "
                                 "(D-21)")
            if r > tickers.get(FILE_OPENS, 0.0):
                raise ValueError(f"reopen timer: the capped {b} counted {r:.4f} "
                                 "reopens per Get, more than its "
                                 f"{tickers.get(FILE_OPENS, 0.0):.4f} opens")
            nanos += n
            reopens += r
    return nanos / reopens / 1e9


def read_repeat(trees: list[dict[str, dict[str, str]]]) -> dict[str, float]:
    """One repeat's t_f, t_blk, t_sk (all-open arm) and t_open (both arms);
    trees: [{arm: {benchmark: stdout}}]."""
    return {**marginal_times([tree["all_open"] for tree in trees]),
            "c_open": reopen_time(trees)}


def qbar_reopen_checks(rows: list[dict], reference: float,
                       contract: dict) -> dict[str, list[dict]]:
    """Each q-bar arm's own time per reopen against the reference, by the
    contract's reopen_time_check (D-21), per workload family. Reported, not
    refused: 04 refuses to price an arm whose check fails."""
    out: dict[str, list[dict]] = {}
    for row in rows:
        reopens = float(row.get("table_reopens") or "nan")
        seconds = float(row.get("reopen_seconds") or "nan")
        try:
            check, ratio = research_objective.reopen_check(
                contract, reference, reopens, seconds)
        except ValueError:
            check, ratio = "no reopen counters", math.nan
        out.setdefault(row["workload_family"], []).append(
            {"run": row.get("result_directory", "?"),
             "reopens": reopens if math.isfinite(reopens) else None,
             "ratio": ratio if math.isfinite(ratio) else None,
             "check": check})
    return out


def arm_open_files(commands: dict[str, str]) -> dict[str, int]:
    """Each arm's open_files, from every read run's command: named once, -1
    in every all-open run, one positive value in every capped run."""
    seen = {arm: set() for arm in ARMS}
    for name, command in commands.items():
        values = OPEN_FILES.findall(command)
        if len(values) != 1:
            raise ValueError(f"{name}: open_files named {len(values)} times, not once")
        seen[name.split("/")[2]].add(int(values[0]))
    if seen["all_open"] != {-1}:
        raise ValueError(f"all-open runs ran open_files {sorted(seen['all_open'])}, not -1")
    if len(seen["capped"]) != 1 or min(seen["capped"]) <= 0:
        raise ValueError(f"capped runs ran open_files {sorted(seen['capped'])}, "
                         "not one positive value")
    return {"capped": seen["capped"].pop(), "all_open": -1}


def write_runs(rows: list[dict], db_bench_sha256: str,
               families: list[str]) -> list[float]:
    """t_w per q-bar native arm (D-14 §2): settled, native, T=10, this
    binary, with the measured phase's job seconds and SST bytes; each run
    once, and at least MIN_WRITE_RUNS of every workload family."""
    def number(row: dict, key: str) -> float:
        return float(row.get(key) or "nan")

    runs, seen, per_family = [], set(), dict.fromkeys(families, 0)
    for row in rows:
        where = row.get("result_directory", "?")
        if where in seen:
            raise ValueError(f"{where}: given twice")
        seen.add(where)
        if row.get("workload_family") not in per_family:
            raise ValueError(f"{where}: workload family "
                             f"{row.get('workload_family')!r} is not one of {families}")
        per_family[row["workload_family"]] += 1
        if (row.get("arm") != "native" or number(row, "size_ratio") != 10 or
                number(row, "settle_ok") != 1):
            raise ValueError(f"{where}: not a settled native arm at T=10")
        if row.get("dbbench_sha256") != db_bench_sha256:
            raise ValueError(f"{where}: ran another db_bench binary")
        seconds = number(row, "sst_write_seconds")
        written = number(row, "sst_bytes_written")
        if not (math.isfinite(seconds) and seconds > 0 and
                math.isfinite(written) and written > 0):
            raise ValueError(f"{where}: no positive write seconds and bytes")
        runs.append(seconds / written)
    short = {f: n for f, n in per_family.items() if n < MIN_WRITE_RUNS}
    if short:
        raise ValueError(f"t_w: fewer than {MIN_WRITE_RUNS} write runs of "
                         f"{short}")
    return runs


def summarise(values: dict[str, list[float]]) -> tuple[dict, dict]:
    """Median core-seconds per unit, and each price's spread."""
    for name, series in values.items():
        if not series or not all(math.isfinite(v) and v > 0 for v in series):
            raise ValueError(f"{name}: a measured time per unit is not "
                             f"positive: {series}")
    return ({k: statistics.median(v) for k, v in values.items()},
            {k: {"min": min(v), "max": max(v), "values": v}
             for k, v in values.items()})


def prices(seconds: dict[str, float], contract: dict) -> dict:
    """Money prices (USD per unit); validated as OBJ-2 requires."""
    core = contract["prices"].get("price_per_core_second")
    money = {key: value * core if isinstance(core, (int, float)) else None
             for key, value in seconds.items()}
    return research_objective.validate_prices(money, contract)


def read_runs(work: Path, file: str) -> dict[str, str]:
    """One file of every read run, keyed T<T>/r<repeat>/<arm>/<benchmark>."""
    return {f"T{t}/r{r}/{a}/{b}":
            (work / f"T{t}" / f"r{r}" / a / b / file).read_text(errors="replace")
            for t in SIZE_RATIOS for r in range(1, REPEATS + 1)
            for a in ARMS for b in READS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("work", type=Path, nargs="?",
                        help="directory with T<T>/r<repeat>/<arm>/<benchmark>/"
                             "{stdout,command}.txt")
    parser.add_argument("--write-summary", type=Path, action="append",
                        required=True,
                        help="04 summary.csv of the q-bar native arms; repeatable")
    parser.add_argument("--db-bench-sha256", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check-writes", action="store_true",
                        help="only check the write runs, before the node time")
    args = parser.parse_args()
    contract, contract_hash = research_objective.load_contract()
    rows = []
    for path in args.write_summary:
        with path.open(newline="") as handle:
            rows += list(csv.DictReader(handle))
    t_w = write_runs(rows, args.db_bench_sha256,
                     sorted(contract["reference_rate"]["ops_per_second"]))
    if args.check_writes:
        print(f"t_w: {len(t_w)} runs, median {statistics.median(t_w):.4g} s/B")
        return 0
    if args.work is None or args.output is None:
        parser.error("the work directory and --output are required")
    stdouts = read_runs(args.work, "stdout.txt")
    open_files = arm_open_files(read_runs(args.work, "command.txt"))
    per_repeat = [read_repeat([{a: {b: stdouts[f"T{t}/r{r}/{a}/{b}"]
                                    for b in READS} for a in ARMS}
                               for t in SIZE_RATIOS])
                  for r in range(1, REPEATS + 1)]
    values = {"c_w": t_w, **{k: [repeat[k] for repeat in per_repeat]
                             for k in ("c_f", "c_blk", "c_sk", "c_open")}}
    timer = [reopen_timer([{a: {b: stdouts[f"T{t}/r{r}/{a}/{b}"]
                                for b in READS} for a in ARMS}
                           for t in SIZE_RATIOS])
             for r in range(1, REPEATS + 1)]
    reference = statistics.median(timer)
    checks = qbar_reopen_checks(rows, reference, contract)
    for family, runs in sorted(checks.items()):
        ratios = ", ".join(f"{run['ratio'] or math.nan:.3f} {run['check']}"
                           for run in runs)
        print(f"reopen check, {family} q-bar arms (own time per reopen over "
              f"stage 18's {reference * 1e6:.3f} us): {ratios}")
    per_operation = {}
    for name, text in stdouts.items():
        seconds, tickers = parse(text, name.rsplit("/", 1)[1])
        per_operation[name] = {"seconds": seconds, **{
            k: tickers.get(k, 0.0)
            for k in (PROBE, BLOCK, SEEK, FILE_OPENS, REOPENS, REOPEN_NANOS)}}
    load_commands = {f"T{t}": (args.work / f"T{t}" / "load" / "command.txt")
                     .read_text() for t in SIZE_RATIOS}
    seconds, spread = summarise(values)
    record = {
        "schema": research_objective.PRICES_SCHEMA,
        "method": "PREREGISTRATION D-15 §3, as amended by D-20 and D-21",
        "open_files": open_files,
        "currency": contract["prices"]["currency"],
        **prices(seconds, contract),
        "core_seconds_per_unit": seconds,
        "core_seconds_spread": spread,
        "reopen_timer": {"seconds_per_reopen": reference, "min": min(timer),
                         "max": max(timer), "values": timer},
        "qbar_reopen_checks": checks,
        "read_processes_per_operation": per_operation,
        "load_commands": load_commands,
        "price_per_core_second": contract["prices"]["price_per_core_second"],
        "research_objective_sha256": contract_hash,
        "db_bench_sha256": args.db_bench_sha256,
        "write_summaries_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in args.write_summary},
        "stdout_sha256": {name: hashlib.sha256(text.encode()).hexdigest()
                          for name, text in stdouts.items()},
        "measured_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
