#!/usr/bin/env python3
"""Device prices (PATHWAYS OBJ-2, Gate N0 item 7; PREREGISTRATION D-13 §1):
the device time of one byte written, one filter probe, one block-reading
probe and one run seek, times the contract's instance price, with c_s from
the contract's storage price.

Reads the runs 18_calibrate_prices.sh wrote, one process per benchmark on
one settled tree, so each process's tickers are that benchmark's own:
  compact      t_w   = seconds / SST bytes written (compaction + flush)
  readmissing  t_f   = seconds / filter probes (point.sst.probe); every Get
                       misses, so almost every probe is filter-rejected
  readrandom   t_blk = (seconds - t_f * probes) / block-reading probes
                       (bloom.filter.full.positive)
  seekrandom   t_sk  = seconds / run seeks (sorted.run.seek), seek_nexts 0
Each time includes the benchmark's per-operation overhead (key generation,
memtable lookup, version lookup), charged to the operation it measures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import research_objective

BENCH = re.compile(r"^(\w+)\s+:\s+[\d.]+ micros/op \d+ ops/sec ([\d.]+) seconds",
                   re.M)
TICKER = re.compile(r"^(rocksdb\.[\w.\-]+) COUNT : (\d+)", re.M)
BENCHMARKS = ("compact", "readmissing", "readrandom", "seekrandom")


def parse(text: str, benchmark: str) -> tuple[float, dict[str, int]]:
    """The benchmark's seconds and the process's tickers."""
    seconds = [float(s) for name, s in BENCH.findall(text) if name == benchmark]
    tickers = {name: int(v) for name, v in TICKER.findall(text)}
    if len(seconds) != 1 or not tickers:
        raise ValueError(f"{benchmark}: expected one result line and tickers")
    return seconds[0], tickers


def positive(name: str, numerator: float, denominator: float) -> float:
    if denominator <= 0 or numerator <= 0:
        raise ValueError(f"{name}: cannot measure a positive time per unit "
                         f"({numerator} s over {denominator} units)")
    return numerator / denominator


def device_seconds(runs: dict[str, str]) -> dict[str, float]:
    """Seconds per unit from each benchmark's stdout."""
    s, t = parse(runs["compact"], "compact")
    t_w = positive("t_w", s, t.get("rocksdb.compact.write.bytes", 0) +
                   t.get("rocksdb.flush.write.bytes", 0))
    s, t = parse(runs["readmissing"], "readmissing")
    t_f = positive("t_f", s, t.get("rocksdb.point.sst.probe", 0))
    s, t = parse(runs["readrandom"], "readrandom")
    t_blk = positive("t_blk", s - t_f * t.get("rocksdb.point.sst.probe", 0),
                     t.get("rocksdb.bloom.filter.full.positive", 0))
    s, t = parse(runs["seekrandom"], "seekrandom")
    t_sk = positive("t_sk", s, t.get("rocksdb.sorted.run.seek", 0))
    return {"c_w": t_w, "c_f": t_f, "c_blk": t_blk, "c_sk": t_sk}


def prices(seconds: dict[str, float], contract: dict) -> dict:
    """Money prices (USD per unit); validated as OBJ-2 requires."""
    instance = contract["prices"].get("instance_price_per_device_second")
    money = {key: value * instance if isinstance(instance, (int, float))
             else None for key, value in seconds.items()}
    return research_objective.validate_prices(money, contract)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("work", type=Path,
                        help="directory with <benchmark>/stdout.txt")
    parser.add_argument("--db-bench-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    contract, contract_hash = research_objective.load_contract()
    runs = {name: (args.work / name / "stdout.txt").read_text(errors="replace")
            for name in BENCHMARKS}
    seconds = device_seconds(runs)
    record = {
        "schema": 1, "currency": contract["prices"]["currency"],
        **prices(seconds, contract),
        "device_seconds_per_unit": seconds,
        "instance_price_per_device_second":
            contract["prices"]["instance_price_per_device_second"],
        "research_objective_sha256": contract_hash,
        "db_bench_sha256": args.db_bench_sha256,
        "stdout_sha256": {name: hashlib.sha256(text.encode()).hexdigest()
                          for name, text in runs.items()},
        "measured_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
