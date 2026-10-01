#!/usr/bin/env python3
"""ACT-4: the patched db_bench at m = 1 against stock RocksDB (PATHWAYS
Pathway A §6; plan §6.4 step 4). Reads the runs 22_check_native_parity.sh
wrote, <work>/pair-NN/{stock,patched}/.

"Stock" is upstream RocksDB with none of the fork's changes (config.sh
STOCK_ROCKSDB_COMMIT), so every quantity here comes from an instrument both
binaries have: db_bench's own lines, upstream tickers, the internal-stats
stall line and the LOG's event log and level summaries. The limits are the
2026-08-22 gate's (formerly stage 09's). Adapted to what stock can show:

- Whole run, load included: both binaries run the same benchmark sequence,
  and stock has no phase stamps.
- Point probes are `bloom.filter.useful + bloom.filter.full.positive`, one
  tick per filter check. The fork's `point.sst.probe` is checked equal to
  that sum on every patched run (probe_identity), so this is the quantity that gate
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

With --arms native hold --criterion ARCH-5 the same checks judge ARCH-5
(PATHWAYS H §9, plan §6.4 step 4): the patched binary's native arm as the
reference against the controller plugin in hold-only mode, both with the host
log. "stock" and "patched" below are then the reference and the tested arm.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from pathlib import Path

import host_log
from pipeline_stats import envelope_verdict

# The parity envelope of the 2026-08-22 gate, moved here from the retired
# 09_evaluate_oracle_parity.py on 2026-10-02 with its values unchanged. ACT-4
# and ARCH-5 (PATHWAYS A §6, H §9) are judged against it.
AMPLIFICATION_LIMIT = 0.05      # relative, two-sided
L0_L1_INPUT_LIMIT = 0.10        # relative, two-sided
PENDING_DEBT_LIMIT = 0.05       # relative, one-sided
SCORE_GROWTH_LIMIT = 1.0        # normalized, see maximum_score_growth

EVENT = re.compile(r"EVENT_LOG_v1 (\{.*\})")
LEVEL_SUMMARY = re.compile(
    r"max score ([0-9.eE+-]+), estimated pending compaction bytes (\d+)")


def relative(candidate: float, baseline: float) -> float:
    if baseline == 0:
        return 0.0 if candidate == 0 else math.inf
    return candidate / baseline - 1.0


def maximum_score_growth(facts: list[tuple[int, dict, dict]]):
    """Return one paired, worst-level score statistic per repeat.

    The old check treated every per-repeat maximum as a deterministic
    invariant. Maxima are deliberately noisy, however, and adding repeats made
    that rule *more* likely to fail. Normalize each shared level's oracle-minus-
    regular growth by the preregistered allowance, then take the worst level in
    that repeat. A one-sided paired confidence envelope can now ask whether the
    worst per-repeat growth is below 1 without averaging levels as though they
    were independent observations.
    """
    values = []
    details = []
    unexercised = []
    for repeat, regular, oracle in facts:
        baseline_levels = set(regular["max_score_by_level"])
        comparable = []
        for level, oracle_max in sorted(oracle["max_score_by_level"].items()):
            if level not in baseline_levels:
                unexercised.append({"repeat": repeat, "level": level,
                                    "oracle_max_score": oracle_max})
                continue
            baseline_max = regular["max_score_by_level"][level]
            allowance = max(0.05 * baseline_max, 0.10)
            normalized = (oracle_max - baseline_max) / allowance
            comparable.append({
                "repeat": repeat,
                "level": level,
                "oracle": oracle_max,
                "regular": baseline_max,
                "absolute_allowance": allowance,
                "normalized_growth": normalized,
            })
        if comparable:
            worst = max(comparable, key=lambda item: item["normalized_growth"])
            values.append(worst["normalized_growth"])
            details.append(worst)
    return values, details, unexercised


def log_facts(directory: Path) -> dict:
    """From rocksdb_LOG.txt: the tree-wide maximum score and pending bytes of
    the level summaries, and the mean input of L0->L1 compactions."""
    path = directory / "rocksdb_LOG.txt"
    if not path.exists():
        raise SystemExit(f"missing {path}")
    text = path.read_text(errors="replace")
    summaries = [(float(score), int(debt))
                 for score, debt in LEVEL_SUMMARY.findall(text)]
    events = []
    for match in EVENT.finditer(text):
        try:
            events.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            pass
    finishes = {int(item["job"]): item for item in events
                if item.get("event") == "compaction_finished"}
    l0_l1_inputs = []
    for item in events:
        if item.get("event") != "compaction_started":
            continue
        levels = sorted(int(key[7:]) for key in item if key.startswith("files_L"))
        if not levels:
            continue
        output = finishes.get(int(item["job"]), {}).get("output_level")
        if levels[0] == 0 and output == 1:
            l0_l1_inputs.append(float(item.get("input_data_size", 0)))
    return {
        "max_score": max((item[0] for item in summaries), default=0.0),
        "max_pending_bytes": max((item[1] for item in summaries), default=0),
        "mean_l0_l1_input_bytes": (
            statistics.fmean(l0_l1_inputs) if l0_l1_inputs else math.nan),
    }


ARMS = ("stock", "patched")
TICKER = re.compile(r"^(rocksdb\.[\w.\-]+) COUNT : (\d+)", re.M)
MIX = re.compile(r"\( Gets:(\d+) Puts:(\d+) Seek:(\d+)(?: ScanEntries:\d+)?, "
                 r"reads (\d+) in (\d+) found")
BENCH = re.compile(r"^(filluniquerandom|mixgraph)\s+:\s+[\d.]+ micros/op "
                   r"(\d+) ops/sec ([\d.]+) seconds", re.M)
STALL = re.compile(r"^Cumulative stall: (\d+):(\d+):([\d.]+) H:M:S", re.M)
IDENTITY_TICKERS = ("rocksdb.bytes.written", "rocksdb.number.keys.written")
HOST_LOG = host_log.FILE_NAME
LEVEL_TICKERS = host_log.LEVEL_TICKERS


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
    for match in EVENT.finditer(log_text):
        try:
            event = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if event.get("event") == "table_file_creation":
            sst_bytes += int(event.get("file_size", 0))
    facts = log_facts(run_dir)

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
        "host_log_problems": (host_log.problems(run_dir / HOST_LOG)
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
             minimum_pairs: int, criterion: str = "ACT-4") -> dict:
    """pairs: (pair number, reference facts, tested facts): stock and
    patched for ACT-4, native and hold for ARCH-5."""
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
        return [relative(float(patched[metric]), float(stock[metric]))
                for _, stock, patched in pairs]

    envelope("write_amplification", rel("write_amplification"),
             AMPLIFICATION_LIMIT, True)
    envelope("point_read_amplification", rel("point_read_amplification"),
             AMPLIFICATION_LIMIT, True)
    envelope("mean_l0_l1_input_size", rel("mean_l0_l1_input_bytes"),
             L0_L1_INPUT_LIMIT, True)
    envelope("maximum_pending_debt", rel("max_pending_bytes"),
             PENDING_DEBT_LIMIT, False)
    growth, worst, _ = maximum_score_growth(
        [(pair, {"max_score_by_level": {"tree": stock["max_score"]}},
          {"max_score_by_level": {"tree": patched["max_score"]}})
         for pair, stock, patched in pairs])
    envelope("maximum_score", growth, SCORE_GROWTH_LIMIT, False)
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
        "criterion": criterion, "verdict": verdict, "pairs": len(pairs),
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
                        help="the 2026-08-22 gate's floor for a paired envelope")
    parser.add_argument("--arms", nargs=2, default=list(ARMS),
                        metavar=("REFERENCE", "TESTED"))
    parser.add_argument("--criterion", default="ACT-4",
                        choices=("ACT-4", "ARCH-5"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    pairs = []
    for pair_dir in sorted(args.work.glob("pair-*")):
        arms = [collect(pair_dir / arm) for arm in args.arms]
        pairs.append((int(pair_dir.name.split("-")[1]), *arms))
    report = evaluate(pairs, args.stall_fraction_margin, args.minimum_pairs,
                      args.criterion)
    report["arms"] = list(args.arms)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    print(f"[{args.criterion}] {report['verdict']}: failed "
          f"{report['failed_checks']}, undecided {report['undecided_checks']}")
    return {"passed": 0, "failed": 1, "undecided": 2}[report["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
