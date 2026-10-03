#!/usr/bin/env python3
"""Device prices (PATHWAYS OBJ-2, Gate N0 item 7) by PREREGISTRATION D-15 §3
as amended by D-20, D-21 and D-22: the core-seconds one byte written, one
filter probe, one block-reading probe, one run seek and one table reopen
take, times the contract's price per core-second, with c_s from the
contract's storage price.

  check-writes  only the write runs, before the node time
  session       one session's record from 18_calibrate_prices.sh's runs
  compare A B   D-22 (f)'s test of two sessions, and the prices file

Reads, one session (18_calibrate_prices.sh wrote them, D-22 c): 29's 18
archived trees, T in price_trees.SIZE_RATIOS, sets 1 to 3, each run one
db_bench process so its tickers are its own. Under <work>/:
  s<b>/round<k>/T<T>/<benchmark>/   three rounds per set, all-open
          (open_files -1), so no table is ever reopened; readmissing,
          readrandom and seekrandom (seek_nexts 0). Per (set, round):
  t_f, t_blk: one least-squares fit over the twelve (tree, benchmark)
          points of readmissing and readrandom,
            seconds per Get = overhead + t_f (filter probes per Get)
                              + t_blk (block-reading probes per Get),
          probes point.sst.probe, block reads bloom.filter.full.positive.
          readmissing's Gets end filter-rejected, so the trees' depths vary
          the probes; readrandom's read the block where the key is found,
          so on each tree the pair varies the block reads. The overhead
          (key generation, memtable and version lookup) is what every Get
          pays whatever the tree holds, so it is not a probe's price.
  t_sk  = least-squares slope, across the six trees, of seconds per seek on
          run seeks per seek (sorted.run.seek)
          A session's price is the median of its nine (set, round) values.
  s1/copen/T<T>/r<repeat>/<arm>/<benchmark>/   the c_open block, D-20
          §2(b)'s procedure on set 1's trees at T = 2, 6 and 10: five
          repeats, every run in two arms, "capped" at the experiments'
          open_files and "all_open", their order alternating by repeat:
  t_open = the capped arm's extra seconds over the all-open arm's, summed
          over the six Get points, divided by its extra reopens
          (rocksdb.no.file.opens), summed the same way: a reopen's whole
          cost, the open() call, reading and parsing the table's footer,
          index and filter, and closing the table it evicts.
          The arms must read alike (probes, block reads), the capped arm
          must reopen at least MIN_REOPEN_GAP more tables per Get on every
          point, and the all-open arm must reopen almost none.
  reopen timer (D-21; not a price): the capped arm's reopen nanoseconds
          over its reopens, both the fork's own tickers (read.table.reopen
          and its .nanos), summed over the six Get points: the reference
          against which 04 checks every run's own time per reopen. Every
          capped Get point must time some reopens (a binary before D-21
          times none), and no more than it opened.
          Each is the median of the five repeats.
  machine.json, archive.json, runs.log, s<b>/T<T>/{unpacked,after}.json:
          the machine at the start, the archive, the order the runs ran
          in, and each tree's checks against the manifest (D-22 i).
Reported, not refused (D-22 e): each Get benchmark's slope fitted alone,
  each tree's residual from the fitted lines, D-15 §3's three-tree values on
  the c_open block's all-open runs, and a seek-based c_open from its
  seekrandom runs.
Writes: the q-bar native arms' rows of 04's summary.csv (D-14 §2), per run
  t_w   = sst_write_seconds / sst_bytes_written, the measured phase's own
          flush and compaction jobs. Each q-bar arm's own time per reopen is
          also reported against the reference, by the contract's
          reopen_time_check, for every workload before Gate N2.
Nothing is written when any value is not positive, a slope is not
identified, an arm is not what it claims, a tree changed or the runs did
not follow D-22 (c). A session's record (schema 5, kind "session") holds
provisional prices. compare writes the prices file (kind "final" when the
test passed, else "failed"): each price the median of both sessions'
values, with their min and max.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

import price_trees
import research_objective

RESULT = re.compile(r"^(\w+)\s+:.*? ([\d.]+) seconds (\d+) operations;", re.M)
TICKER = re.compile(r"^(rocksdb\.[\w.\-]+) COUNT : (\d+)", re.M)
READS = ("readmissing", "readrandom", "seekrandom")
GETS = ("readmissing", "readrandom")
# The experiments' open_files, and every table kept open (D-20).
ARMS = ("capped", "all_open")
SIZE_RATIOS = price_trees.SIZE_RATIOS
SETS = price_trees.SETS
ROUNDS = price_trees.ROUNDS
COPEN_SET = price_trees.COPEN_SET
COPEN_RATIOS = price_trees.COPEN_RATIOS
REPEATS = 5
# D-22 (f): the prices two sessions must reproduce.
TESTED = ("c_f", "c_blk", "c_sk", "c_open", "reopen_timer")
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


def run_arm(name: str) -> str:
    """The arm of a read run by its key: every round runs all-open; the
    c_open block's key names its arm."""
    parts = name.split("/")
    return parts[4] if parts[1] == "copen" else "all_open"


def arm_open_files(commands: dict[str, str]) -> dict[str, int]:
    """Each arm's open_files, from every read run's command: named once, -1
    in every all-open run, one positive value in every capped run; and
    D-22 (a')'s two flags in every one."""
    seen = {arm: set() for arm in ARMS}
    for name, command in commands.items():
        if not price_trees.age_flags_present(command):
            raise ValueError(f"{name}: ran without "
                             f"{' '.join(price_trees.AGE_FLAGS)} (D-22 a')")
        values = OPEN_FILES.findall(command)
        if len(values) != 1:
            raise ValueError(f"{name}: open_files named {len(values)} times, not once")
        seen[run_arm(name)].add(int(values[0]))
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
            raise ValueError(f"{where}: ran another db_bench binary "
                             f"({row.get('dbbench_sha256')}, not "
                             f"{db_bench_sha256}; executable plus librocksdb)")
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




def round_key(build: int, k: int, ratio: int, benchmark: str) -> str:
    return f"s{build}/round{k}/T{ratio}/{benchmark}"


def copen_key(ratio: int, repeat: int, arm: str, benchmark: str) -> str:
    return f"s{COPEN_SET}/copen/T{ratio}/r{repeat}/{arm}/{benchmark}"


def run_keys() -> list[str]:
    """Every read run of a session, in the order 18 runs them."""
    keys = []
    for b in SETS:
        keys += [round_key(b, k, t, bench) for k, order in enumerate(ROUNDS, 1)
                 for t in order for bench in READS]
        if b == COPEN_SET:
            keys += [copen_key(t, r, a, bench) for t in COPEN_RATIOS
                     for r in range(1, REPEATS + 1) for bench in READS
                     for a in (ARMS if r % 2 else ARMS[::-1])]
    return keys


def session_runs(work: Path, file: str) -> dict[str, str]:
    """One file of every read run, keyed by its folder under work."""
    return {key: (work / key / file).read_text(errors="replace")
            for key in run_keys()}


def check_order(lines: list[str]) -> None:
    """D-22 (c): the scored runs ran set by set, each set's rounds in their
    rotated orders, then the c_open block after set 1's rounds; runs.log
    lists every run's folder in the order it ran (warm-ups among them)."""
    scored = [line for line in lines if "/warmup" not in line]
    if scored != run_keys():
        first = next((i for i, (a, b) in enumerate(zip(scored, run_keys()))
                      if a != b), min(len(scored), len(run_keys())))
        raise ValueError(f"runs.log: run {first + 1} is "
                         f"{scored[first] if first < len(scored) else 'missing'}, "
                         f"not {run_keys()[first] if first < len(run_keys()) else 'none'}"
                         " (D-22 c's order)")


def tree_checks(work: Path, tree_set: str) -> None:
    """D-22 (i): every tree checked against this archive's manifest when
    unpacked and after its set's last read, both clean."""
    for b, t in price_trees.all_trees():
        for phase in ("unpacked", "after"):
            path = work / price_trees.tree(b, t) / f"{phase}.json"
            if not path.is_file():
                raise ValueError(f"{path}: no {phase} check (D-22 i)")
            report = json.loads(path.read_text())
            if report.get("manifest_sha256") != tree_set:
                raise ValueError(f"{path}: checked against another archive "
                                 f"({report.get('manifest_sha256')}, D-22 i)")
            if report.get("ok") is not True:
                raise ValueError(f"{path}: {report.get('problems')} (D-22 i)")


def gets_line(points: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    """(t_f, t_blk, overhead) of the shared fit over (x, b, y) points."""
    t_f, t_blk = fit_gets(points)
    n = len(points)
    return (t_f, t_blk, sum(p[2] for p in points) / n
            - t_f * sum(p[0] for p in points) / n
            - t_blk * sum(p[1] for p in points) / n)


def round_record(trees: dict[int, dict[str, str]]) -> dict:
    """One (set, round): t_f, t_blk and t_sk (marginal_times' checks and
    fits), and D-22 (e)'s diagnostics: each Get benchmark's slope fitted
    alone (seconds per Get on filter probes per Get) and each tree's
    residual from the fitted lines; trees: {T: {benchmark: stdout}}."""
    values = marginal_times(list(trees.values()))
    parsed = {t: {b: parse(tree[b], b) for b in READS}
              for t, tree in trees.items()}
    t_f, t_blk, overhead = gets_line(
        [(tk.get(PROBE, 0.0), tk.get(BLOCK, 0.0), sec)
         for p in parsed.values() for b in GETS for sec, tk in [p[b]]])
    seeks = statistics.linear_regression(
        [p["seekrandom"][1].get(SEEK, 0.0) for p in parsed.values()],
        [p["seekrandom"][0] for p in parsed.values()])
    alone = {f"{b}_alone": statistics.linear_regression(
        [p[b][1].get(PROBE, 0.0) for p in parsed.values()],
        [p[b][0] for p in parsed.values()]).slope for b in GETS}
    residuals = {}
    for t, p in parsed.items():
        for b in GETS:
            sec, tk = p[b]
            residuals[f"T{t}/{b}"] = sec - (overhead + t_f * tk.get(PROBE, 0.0)
                                            + t_blk * tk.get(BLOCK, 0.0))
        sec, tk = p["seekrandom"]
        residuals[f"T{t}/seekrandom"] = sec - (seeks.intercept
                                               + seeks.slope * tk.get(SEEK, 0.0))
    return {**values, **alone, "get_overhead": overhead,
            "seek_intercept": seeks.intercept, "residuals": residuals}


def seek_reopen_time(trees: list[dict[str, dict[str, str]]]) -> dict:
    """D-22 (e): a seek-based c_open from one repeat's seekrandom runs, the
    capped arm's extra seconds over the all-open arm's, summed over the
    trees, over its extra table opens summed the same way; beside each
    arm's run seeks per seek, which should match. Reported, not refused."""
    extra_seconds = extra_opens = 0.0
    seeks = {arm: [] for arm in ARMS}
    for tree in trees:
        (sc, tc), (sa, ta) = (parse(tree[arm]["seekrandom"], "seekrandom")
                              for arm in ARMS)
        extra_seconds += sc - sa
        extra_opens += tc.get(FILE_OPENS, 0.0) - ta.get(FILE_OPENS, 0.0)
        seeks["capped"].append(tc.get(SEEK, 0.0))
        seeks["all_open"].append(ta.get(SEEK, 0.0))
    return {"seconds_per_reopen": (extra_seconds / extra_opens
                                   if extra_opens > 0 else None),
            "run_seeks_per_seek": seeks}


def copen_block(stdouts: dict[str, str]) -> dict:
    """The c_open block (D-20 §2(b) on set 1's trees at T = 2, 6 and 10):
    t_open and the reopen timer per repeat, and D-22 (e)'s bridge and
    seek-based c_open."""
    repeats = [[{a: {b: stdouts[copen_key(t, r, a, b)] for b in READS}
                 for a in ARMS} for t in COPEN_RATIOS]
               for r in range(1, REPEATS + 1)]
    bridge = []
    for trees in repeats:
        try:
            bridge.append(marginal_times([tree["all_open"] for tree in trees]))
        except ValueError as error:
            bridge.append({"refused": str(error)})
    fitted = [v for v in bridge if "refused" not in v]
    seek = [seek_reopen_time(trees) for trees in repeats]
    seek_values = [v["seconds_per_reopen"] for v in seek
                   if v["seconds_per_reopen"] is not None]
    return {
        "c_open": [reopen_time(trees) for trees in repeats],
        "reopen_timer": [reopen_timer(trees) for trees in repeats],
        "three_tree_fit": {
            "method": "D-15 §3 on the c_open block's all-open runs (D-22 e)",
            "values": bridge,
            "median": ({k: statistics.median(v[k] for v in fitted)
                        for k in ("c_f", "c_blk", "c_sk")} if fitted else None)},
        "seek_c_open": {
            "values": seek,
            "median": statistics.median(seek_values) if seek_values else None},
    }


def per_operation(stdouts: dict[str, str]) -> dict[str, dict]:
    out = {}
    for name, text in stdouts.items():
        seconds, tickers = parse(text, name.rsplit("/", 1)[1])
        out[name] = {"seconds": seconds, **{
            k: tickers.get(k, 0.0)
            for k in (PROBE, BLOCK, SEEK, FILE_OPENS, REOPENS, REOPEN_NANOS)}}
    return out


def spread(series: list[float]) -> dict:
    return {"min": min(series), "max": max(series), "values": series}


def session(work: Path, rows: list[dict], t_w: list[float], contract: dict,
            contract_hash: str, args) -> dict:
    """One session's record (D-22 c, d, e, i): schema 5, kind "session",
    provisional prices."""
    archive = json.loads((work / "archive.json").read_text())
    if archive.get("tree_set_sha256") != args.tree_set_sha256:
        raise ValueError(f"the session read archive {archive.get('tree_set_sha256')}, "
                         f"not {args.tree_set_sha256} (D-22 i)")
    tree_checks(work, args.tree_set_sha256)
    check_order((work / "runs.log").read_text().split())
    stdouts = session_runs(work, "stdout.txt")
    open_files = arm_open_files(session_runs(work, "command.txt"))
    rounds = {f"s{b}/round{k}": round_record(
                  {t: {bench: stdouts[round_key(b, k, t, bench)] for bench in READS}
                   for t in SIZE_RATIOS})
              for b in SETS for k in range(1, len(ROUNDS) + 1)}
    block = copen_block(stdouts)
    values = {"c_w": t_w,
              **{k: [r[k] for r in rounds.values()] for k in ("c_f", "c_blk", "c_sk")},
              "c_open": block["c_open"]}
    seconds, _ = summarise(values)
    timer = block["reopen_timer"]
    reference = statistics.median(timer)
    if not (math.isfinite(reference) and reference > 0):
        raise ValueError(f"reopen timer: {timer}")
    per_set = {k: {str(b): statistics.median(rounds[f"s{b}/round{r}"][k]
                                             for r in range(1, len(ROUNDS) + 1))
                   for b in SETS} for k in ("c_f", "c_blk", "c_sk")}
    reopen_runs = [{"run": row.get("result_directory", "?"),
                    "workload_family": row["workload_family"],
                    "table_reopens": row.get("table_reopens"),
                    "reopen_seconds": row.get("reopen_seconds")} for row in rows]
    return {
        "schema": research_objective.PRICES_SCHEMA,
        "kind": "session",
        "session": args.session,
        "method": "PREREGISTRATION D-15 §3, as amended by D-20, D-21 and D-22",
        "open_files": open_files,
        "currency": contract["prices"]["currency"],
        **prices(seconds, contract),
        "core_seconds_per_unit": seconds,
        "core_seconds_spread": {k: spread(v) for k, v in values.items()},
        "per_set_median": per_set,
        "rounds": rounds,
        "copen_block": block,
        "reopen_timer": {"seconds_per_reopen": reference, **spread(timer)},
        "qbar_reopen_runs": reopen_runs,
        "qbar_reopen_checks": qbar_reopen_checks(rows, reference, contract),
        "read_processes_per_operation": per_operation(stdouts),
        "machine": json.loads((work / "machine.json").read_text()),
        "tree_set_sha256": args.tree_set_sha256,
        "archive": archive.get("archive"),
        "price_per_core_second": contract["prices"]["price_per_core_second"],
        "research_objective_sha256": contract_hash,
        "db_bench_sha256": args.db_bench_sha256,
        "write_summaries_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in args.write_summary},
        "stdout_sha256": {name: hashlib.sha256(text.encode()).hexdigest()
                          for name, text in stdouts.items()},
        "measured_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def session_value(record: dict, name: str) -> tuple[float, list[float]]:
    """A session's price of one tested quantity and the values behind it."""
    if name == "reopen_timer":
        timer = record["reopen_timer"]
        return timer["seconds_per_reopen"], timer["values"]
    return (record["core_seconds_per_unit"][name],
            record["core_seconds_spread"][name]["values"])


def compare(a: dict, b: dict, contract: dict, contract_hash: str,
            hashes: tuple[str, str]) -> dict:
    """D-22 (f): two sessions' test and the prices file. Each tested price
    passes when |B - A| <= tolerance (A + B) / 2; on a pass of all five the
    prices are final, each the median of both sessions' values."""
    for name, record in (("A", a), ("B", b)):
        if (record.get("schema") != research_objective.PRICES_SCHEMA or
                record.get("kind") != "session"):
            raise ValueError(f"{name}: not one session's record (schema "
                             f"{research_objective.PRICES_SCHEMA}, kind session)")
        if record.get("research_objective_sha256") != contract_hash:
            raise ValueError(f"{name}: measured under another contract")
    if a["session"] == b["session"]:
        raise ValueError(f"both records are session {a['session']!r}")
    for key in ("db_bench_sha256", "tree_set_sha256", "price_per_core_second",
                "open_files"):
        if a.get(key) != b.get(key):
            raise ValueError(f"the sessions differ in {key}: one binary, one "
                             "archive and one set of q-bar arms (D-22 f)")
    # The q-bar summaries by content: a path may be spelt two ways.
    if (sorted(a["write_summaries_sha256"].values()) !=
            sorted(b["write_summaries_sha256"].values())):
        raise ValueError("the sessions read different q-bar summaries: one set "
                         "of q-bar arms (D-22 f)")
    if a["core_seconds_spread"]["c_w"] != b["core_seconds_spread"]["c_w"]:
        raise ValueError("the sessions' c_w differ on the same q-bar arms")
    boot = [r.get("machine", {}).get("boot_id") for r in (a, b)]
    if None in boot or boot[0] == boot[1]:
        raise ValueError(f"no reboot between the sessions (boot ids {boot}; "
                         "D-22 f)")
    tolerance = contract["prices"]["reproducibility_tolerance"]
    test, pooled = {}, {}
    for name in TESTED:
        (va, sa), (vb, sb) = session_value(a, name), session_value(b, name)
        test[name] = {"A": va, "B": vb, "relative": (vb - va) / ((va + vb) / 2),
                      "passed": research_objective.reproducible(va, vb, tolerance)}
        pooled[name] = sa + sb
    passed = all(t["passed"] for t in test.values())
    values = {"c_w": a["core_seconds_spread"]["c_w"]["values"],
              **{k: pooled[k] for k in ("c_f", "c_blk", "c_sk", "c_open")}}
    seconds, spreads = summarise(values)
    reference = statistics.median(pooled["reopen_timer"])
    checks: dict[str, list[dict]] = {}
    for run in a["qbar_reopen_runs"]:
        row = {"result_directory": run["run"], "workload_family": run["workload_family"],
               "table_reopens": run["table_reopens"], "reopen_seconds": run["reopen_seconds"]}
        for family, found in qbar_reopen_checks([row], reference, contract).items():
            checks.setdefault(family, []).extend(found)
    return {
        "schema": research_objective.PRICES_SCHEMA,
        "kind": "final" if passed else "failed",
        "method": "PREREGISTRATION D-15 §3, as amended by D-20, D-21 and D-22",
        "open_files": a["open_files"],
        "currency": contract["prices"]["currency"],
        **prices(seconds, contract),
        "core_seconds_per_unit": seconds,
        "core_seconds_spread": spreads,
        "reopen_timer": {"seconds_per_reopen": reference, **spread(pooled["reopen_timer"])},
        "qbar_reopen_checks": checks,
        "reproducibility": {"tolerance": tolerance, "passed": passed,
                            "rule": "|B - A| <= tolerance * (A + B) / 2 (D-22 f)",
                            "prices": test},
        "sessions": {r["session"]: {
            "sha256": h, "measured_utc": r["measured_utc"], "machine": r["machine"],
            "core_seconds_per_unit": r["core_seconds_per_unit"],
            "reopen_timer": r["reopen_timer"], "per_set_median": r["per_set_median"],
            "spread": {k: r["core_seconds_spread"][k]
                       for k in ("c_f", "c_blk", "c_sk", "c_open")},
            # D-22 (e), (j): every (set, round)'s slopes fitted alone and
            # residuals, and the c_open block's bridge and seek-based c_open.
            "rounds": r["rounds"], "copen_block": r["copen_block"]}
            for r, h in ((a, hashes[0]), (b, hashes[1]))},
        "tree_set_sha256": a["tree_set_sha256"],
        "price_per_core_second": contract["prices"]["price_per_core_second"],
        "research_objective_sha256": contract_hash,
        "db_bench_sha256": a["db_bench_sha256"],
        "write_summaries_sha256": a["write_summaries_sha256"],
        "measured_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def write(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check-writes", "session"):
        p = sub.add_parser(name)
        p.add_argument("--write-summary", type=Path, action="append",
                       required=True,
                       help="04 summary.csv of the q-bar native arms; repeatable")
        p.add_argument("--db-bench-sha256", required=True,
                       help="db_bench's identity as loaded (executable plus "
                            "librocksdb; preflight_marker.py identity), the "
                            "value 03 records as each arm's dbbench_sha256")
    s = sub.choices["session"]
    s.add_argument("work", type=Path,
                   help="the session's folder, as 18_calibrate_prices.sh writes it")
    s.add_argument("--tree-set-sha256", required=True)
    s.add_argument("--session", required=True)
    s.add_argument("--output", type=Path, required=True)
    c = sub.add_parser("compare")
    c.add_argument("a", type=Path, help="session A's session.json")
    c.add_argument("b", type=Path, help="session B's session.json")
    c.add_argument("--output", type=Path, required=True)
    c.add_argument("--replace", action="store_true",
                   help="replace an existing output file")
    args = parser.parse_args()
    try:
        return run(parser, args)
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        print(f"[prices] refused: {error}", file=sys.stderr)
        return 1


def run(parser: argparse.ArgumentParser, args) -> int:
    contract, contract_hash = research_objective.load_contract()
    if args.command == "compare":
        if args.output.exists() and not args.replace:
            parser.error(f"{args.output} exists; keep it under another name "
                         "first, or pass --replace")
        raw = [path.read_bytes() for path in (args.a, args.b)]
        record = compare(*(json.loads(r) for r in raw), contract, contract_hash,
                         tuple(hashlib.sha256(r).hexdigest() for r in raw))
        write(args.output, record)
        for name, t in record["reproducibility"]["prices"].items():
            print(f"{name:<13} A {t['A']:.4g}  B {t['B']:.4g}  "
                  f"{t['relative']:+.2%}  {'pass' if t['passed'] else 'FAIL'}")
        for name, summary in record["sessions"].items():
            print(f"session {name} per-set medians: " + "; ".join(
                f"{k} " + ", ".join(f"{b}: {v:.4g}" for b, v in sets.items())
                for k, sets in summary["per_set_median"].items()))
        passed = record["reproducibility"]["passed"]
        print(f"D-22 (f) {'PASSED' if passed else 'FAILED'} at "
              f"{record['reproducibility']['tolerance']:.0%}: "
              f"{'final' if passed else 'no price is final'}; wrote {args.output}")
        return 0 if passed else 1
    rows = []
    for path in args.write_summary:
        with path.open(newline="") as handle:
            rows += list(csv.DictReader(handle))
    t_w = write_runs(rows, args.db_bench_sha256,
                     sorted(contract["reference_rate"]["ops_per_second"]))
    if args.command == "check-writes":
        print(f"t_w: {len(t_w)} runs, median {statistics.median(t_w):.4g} s/B")
        return 0
    record = session(args.work, rows, t_w, contract, contract_hash, args)
    for family, runs in sorted(record["qbar_reopen_checks"].items()):
        ratios = ", ".join(f"{run['ratio'] or math.nan:.3f} {run['check']}"
                           for run in runs)
        print(f"reopen check, {family} q-bar arms (own time per reopen over "
              f"this session's {record['reopen_timer']['seconds_per_reopen'] * 1e6:.3f}"
              f" us): {ratios}")
    write(args.output, record)
    summary = {k: record["core_seconds_per_unit"][k] for k in research_objective.DEVICE_PRICES}
    print(json.dumps({"session": args.session, "core_seconds_per_unit": summary,
                      "per_set_median": record["per_set_median"],
                      "reopen_timer": record["reopen_timer"]["seconds_per_reopen"],
                      "seek_c_open": record["copen_block"]["seek_c_open"]["median"]},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
