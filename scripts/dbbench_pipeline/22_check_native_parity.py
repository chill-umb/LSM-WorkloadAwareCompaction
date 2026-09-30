#!/usr/bin/env python3
"""ACT-4: the patched db_bench at m = 1 against stock RocksDB (PATHWAYS
Pathway A §6; plan §6.4 step 4). Reads the runs 22_check_native_parity.sh
wrote, <work>/pair-NN/{stock,patched}/.

"Stock" is upstream RocksDB with none of the fork's changes (config.sh
STOCK_ROCKSDB_COMMIT), so every quantity here comes from an instrument both
binaries have: db_bench's own lines, upstream tickers, the internal-stats
stall line and the LOG's event log and level summaries. The limits are the
2026-08-22 gate's, imported from 09. Adapted to what stock can show:

- Whole run, load included: both binaries run the same benchmark sequence,
  and stock has no phase stamps.
- Point probes are `bloom.filter.useful + bloom.filter.full.positive`, one
  tick per filter check. The fork's `point.sst.probe` is checked equal to
  that sum on every patched run (probe_identity), so this is the quantity 09
  judged.
- The maximum score is the LOG's tree-wide maximum. Stock writes no
  per-level episode log.
- Sorted-run seeks exist only in the fork, so they are reported, not judged.
- The stall allowance is on the stall fraction, stall seconds over the
  writing benchmarks' seconds, with the margin passed in (D-13's, from 13).

The patched arm runs as every measured arm will, with the host log on (plan
WP4), so the parity checks include its cost. Its log is checked against
itself on every patched run (host_log_consistency): header first, the
measure_start and drain_end stamps present, the per-level read counters
summing to their tickers at drain_end, every job that began ended, the last
H sample equal to drain_end's live SST bytes, and operation counts that never
decrease.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
from pathlib import Path

from pipeline_stats import envelope_verdict

_spec = importlib.util.spec_from_file_location(
    "oracle_parity", Path(__file__).with_name("09_evaluate_oracle_parity.py"))
parity = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(parity)

ARMS = ("stock", "patched")
TICKER = re.compile(r"^(rocksdb\.[\w.\-]+) COUNT : (\d+)", re.M)
MIX = re.compile(r"\( Gets:(\d+) Puts:(\d+) Seek:(\d+)(?: ScanEntries:\d+)?, "
                 r"reads (\d+) in (\d+) found")
BENCH = re.compile(r"^(filluniquerandom|mixgraph)\s+:\s+[\d.]+ micros/op "
                   r"(\d+) ops/sec ([\d.]+) seconds", re.M)
STALL = re.compile(r"^Cumulative stall: (\d+):(\d+):([\d.]+) H:M:S", re.M)
IDENTITY_TICKERS = ("rocksdb.bytes.written", "rocksdb.number.keys.written")
HOST_LOG = "host_log.jsonl"
# A stamp's levels[i] row order (db/rl_read_counters.h) and each kind's ticker.
LEVEL_TICKERS = ("rocksdb.point.sst.probe", "rocksdb.bloom.filter.full.positive",
                 "rocksdb.bloom.filter.full.true.positive",
                 "rocksdb.sorted.run.seek")


def host_log_problems(path: Path) -> list[str]:
    """What is wrong with one run's host log; empty when it is consistent."""
    try:
        records = [json.loads(line) for line in path.read_text().splitlines()
                   if line.strip()]
    except (OSError, json.JSONDecodeError) as error:
        return [f"unreadable: {error}"]
    problems = []
    if not records or records[0].get("type") != "header":
        problems.append("no header first")
    stamps = {r.get("name"): r for r in records if r.get("type") == "stamp"}
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


def collect(run_dir: Path) -> dict:
    """The parity quantities of one run, from stdout.txt and rocksdb_LOG.txt."""
    text = (run_dir / "stdout.txt").read_text(errors="replace")
    tickers = {name: int(value) for name, value in TICKER.findall(text)}
    mix = MIX.findall(text)
    benches = {name: (int(rate), float(seconds))
               for name, rate, seconds in BENCH.findall(text)}
    stalls = STALL.findall(text)
    missing = [what for what, ok in (
        ("tickers", bool(tickers)), ("mixgraph counts", bool(mix)),
        ("benchmark lines", set(benches) == {"filluniquerandom", "mixgraph"}),
        ("stall line", bool(stalls))) if not ok]
    if missing:
        raise ValueError(f"{run_dir}: missing {', '.join(missing)}")
    gets, puts, seeks, found, reads = map(int, mix[-1])
    hours, minutes, seconds = stalls[-1]
    stall_seconds = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    writing_seconds = sum(seconds for _, seconds in benches.values())

    log_text = (run_dir / "rocksdb_LOG.txt").read_text(errors="replace")
    sst_bytes = 0
    for match in parity.EVENT.finditer(log_text):
        try:
            event = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if event.get("event") == "table_file_creation":
            sst_bytes += int(event.get("file_size", 0))
    facts = parity.log_facts(run_dir)

    probes = (tickers.get("rocksdb.bloom.filter.useful", 0) +
              tickers.get("rocksdb.bloom.filter.full.positive", 0))
    user_bytes = tickers.get("rocksdb.bytes.written", 0)
    return {
        "workload": {"gets": gets, "puts": puts, "seeks": seeks,
                     "reads": reads, "found": found,
                     **{name: tickers.get(name) for name in IDENTITY_TICKERS}},
        "write_amplification": sst_bytes / user_bytes if user_bytes else math.nan,
        "point_read_amplification": probes / gets if gets else math.nan,
        "filter_checks": probes,
        "fork_point_probes": tickers.get("rocksdb.point.sst.probe"),
        "host_log_problems": (host_log_problems(run_dir / HOST_LOG)
                              if (run_dir / HOST_LOG).exists() else None),
        "fork_sorted_run_seeks_per_scan": (
            tickers["rocksdb.sorted.run.seek"] / seeks
            if seeks and "rocksdb.sorted.run.seek" in tickers else None),
        "stall_seconds": stall_seconds,
        "stall_fraction": stall_seconds / writing_seconds,
        "mixgraph_ops_per_second": benches["mixgraph"][0],
        "max_score": facts["max_score"],
        "max_pending_bytes": facts["max_pending_bytes"],
        "mean_l0_l1_input_bytes": facts["mean_l0_l1_input_bytes"],
    }


def evaluate(pairs: list[tuple[int, dict, dict]], stall_margin: float,
             minimum_pairs: int) -> dict:
    """pairs: (pair number, stock facts, patched facts)."""
    checks: dict[str, dict] = {}

    def invariant(name, ok, details):
        checks[name] = {"kind": "invariant", "passed": bool(ok),
                        "verdict": "passed" if ok else "failed",
                        "details": details}

    def envelope(name, values, limit, two_sided):
        if not values or not all(math.isfinite(v) for v in values):
            checks[name] = {"kind": "paired_envelope", "passed": False,
                            "verdict": "instrument_failed",
                            "per_repeat": values}
            return
        checks[name] = envelope_verdict(values, limit, minimum_pairs,
                                        two_sided=two_sided)

    mismatches = [{"pair": pair, "stock": stock["workload"],
                   "patched": patched["workload"]}
                  for pair, stock, patched in pairs
                  if stock["workload"] != patched["workload"]]
    invariant("paired_workload_identity", pairs and not mismatches, mismatches)
    unequal = [{"pair": pair, "point_sst_probe": patched["fork_point_probes"],
                "filter_checks": patched["filter_checks"]}
               for pair, _, patched in pairs
               if patched["fork_point_probes"] != patched["filter_checks"]]
    invariant("probe_identity", pairs and not unequal, unequal)
    broken = [{"pair": pair,
               "problems": patched["host_log_problems"] or ["no host log"]}
              for pair, _, patched in pairs
              if patched["host_log_problems"] != []]
    invariant("host_log_consistency", pairs and not broken, broken)

    def rel(metric):
        return [parity.relative(float(patched[metric]), float(stock[metric]))
                for _, stock, patched in pairs]

    envelope("write_amplification", rel("write_amplification"),
             parity.AMPLIFICATION_LIMIT, True)
    envelope("point_read_amplification", rel("point_read_amplification"),
             parity.AMPLIFICATION_LIMIT, True)
    envelope("mean_l0_l1_input_size", rel("mean_l0_l1_input_bytes"),
             parity.L0_L1_INPUT_LIMIT, True)
    envelope("maximum_pending_debt", rel("max_pending_bytes"),
             parity.PENDING_DEBT_LIMIT, False)
    growth, worst, _ = parity.maximum_score_growth(
        [(pair, {"max_score_by_level": {"tree": stock["max_score"]}},
          {"max_score_by_level": {"tree": patched["max_score"]}})
         for pair, stock, patched in pairs])
    envelope("maximum_score", growth, parity.SCORE_GROWTH_LIMIT, False)
    checks["maximum_score"]["by_pair"] = worst
    envelope("stall_fraction",
             [patched["stall_fraction"] - stock["stall_fraction"]
              for _, stock, patched in pairs], stall_margin, False)

    failed = sorted(name for name, check in checks.items()
                    if check["passed"] is False)
    undecided = sorted(name for name, check in checks.items()
                       if check["passed"] is None)
    verdict = "failed" if failed else "undecided" if undecided else "passed"
    return {
        "criterion": "ACT-4", "verdict": verdict, "pairs": len(pairs),
        "stall_fraction_margin": stall_margin,
        "failed_checks": failed, "undecided_checks": undecided,
        "checks": checks,
        "informational": {
            "mixgraph_throughput_ratio": [
                patched["mixgraph_ops_per_second"] /
                stock["mixgraph_ops_per_second"]
                for _, stock, patched in pairs],
            "patched_sorted_run_seeks_per_scan": [
                patched["fork_sorted_run_seeks_per_scan"]
                for _, _, patched in pairs],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("work", type=Path)
    parser.add_argument("--stall-fraction-margin", type=float, required=True)
    parser.add_argument("--minimum-pairs", type=int, default=5,
                        help="09's floor for a paired envelope")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    pairs = []
    for pair_dir in sorted(args.work.glob("pair-*")):
        arms = [collect(pair_dir / arm) for arm in ARMS]
        pairs.append((int(pair_dir.name.split("-")[1]), *arms))
    report = evaluate(pairs, args.stall_fraction_margin, args.minimum_pairs)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    print(f"[ACT-4] {report['verdict']}: failed {report['failed_checks']}, "
          f"undecided {report['undecided_checks']}")
    return {"passed": 0, "failed": 1, "undecided": 2}[report["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
