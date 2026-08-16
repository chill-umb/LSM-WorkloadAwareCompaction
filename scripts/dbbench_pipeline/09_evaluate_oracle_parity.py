#!/usr/bin/env python3
"""Evaluate the deterministic trigger-oracle parity gate from section 14.2."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path


EVENT = re.compile(r"EVENT_LOG_v1 (\{.*\})")
LEVEL_SUMMARY = re.compile(
    r"max score ([0-9.eE+-]+), estimated pending compaction bytes (\d+)")
DIAGNOSTICS = re.compile(
    r"RL trigger diagnostics:.*?queries=(\d+).*?skipped_ticks=(\d+).*?"
    r"watchdog_expiries=(\d+)")


def relative(candidate: float, baseline: float) -> float:
    if baseline == 0:
        return 0.0 if candidate == 0 else math.inf
    return candidate / baseline - 1.0


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{number}: {exc}") from exc
    return records


def log_facts(directory: Path) -> dict:
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
    decision_jobs = Counter()
    for item in events:
        if item.get("event") != "compaction_started":
            continue
        levels = sorted(int(key[7:]) for key in item
                        if key.startswith("files_L"))
        if not levels:
            continue
        source = levels[0]
        output = finishes.get(int(item["job"]), {}).get("output_level")
        if source == 0 and output == 1:
            l0_l1_inputs.append(float(item.get("input_data_size", 0)))
        decision_id = int(item.get("rl_decision_id", 0))
        if decision_id:
            decision_jobs[(decision_id, source)] += 1
    diagnostic_matches = DIAGNOSTICS.findall(text)
    diagnostics = tuple(map(int, diagnostic_matches[-1])) \
        if diagnostic_matches else (0, 0, 0)
    episodes = read_jsonl(directory / "pressure_episodes.jsonl")
    max_score_by_level = defaultdict(float)
    for episode in episodes:
        level = int(episode["level"])
        max_score_by_level[level] = max(
            max_score_by_level[level], float(episode["max_score"]))
    trace = read_jsonl(directory / "trigger_trace.jsonl")
    # Episode logs are event-driven and can contain a complete due interval
    # between two worker trace ticks. Use them as the authoritative set of
    # levels that became due; the trace supplies authorization state.
    due_levels = {int(episode["level"]) for episode in episodes}
    authorized_levels = set()
    eligible_latency = []
    trace_by_level = defaultdict(list)
    for frame in trace:
        timestamp = int(frame.get("time_micros", 0))
        for level in frame.get("levels", []):
            index = int(level["level"])
            score = float(level.get("score", 0))
            max_score_by_level[index] = max(max_score_by_level[index], score)
            trace_by_level[index].append((timestamp, level))
            if score >= 1.0:
                due_levels.add(index)
                if int(level.get("gate_mode", 0)) != 0:
                    authorized_levels.add(index)
    for episode in episodes:
        level = int(episode["level"])
        start = int(episode["start_micros"])
        candidates = [timestamp for timestamp, state in trace_by_level[level]
                      if timestamp >= start and
                      int(state.get("gate_mode", 0)) != 0]
        if candidates:
            eligible_latency.append(min(candidates) - start)
    return {
        "max_score": max((item[0] for item in summaries), default=0.0),
        "max_pending_bytes": max((item[1] for item in summaries), default=0),
        "max_score_by_level": dict(max_score_by_level),
        "mean_l0_l1_input_bytes": (
            statistics.fmean(l0_l1_inputs) if l0_l1_inputs else math.nan),
        "held_decision_max_jobs": max(decision_jobs.values(), default=0),
        "queries": diagnostics[0],
        "skipped_ticks": diagnostics[1],
        "watchdog_expiries": diagnostics[2],
        "due_levels": sorted(due_levels),
        "zero_authorization_levels": sorted(due_levels - authorized_levels),
        "median_due_to_eligible_micros": (
            statistics.median(eligible_latency) if eligible_latency else math.nan),
        "stop_log_events": len(re.findall(r"Stopping writes", text)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("summary", type=Path)
    parser.add_argument("--size-millions", type=int, default=1)
    parser.add_argument("--size-ratio", type=int, default=2)
    parser.add_argument("--minimum-pairs", type=int, default=3)
    parser.add_argument("--observation-period-ms", type=float, default=50.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    grouped = defaultdict(dict)
    with args.summary.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if (int(row["size_millions"]) == args.size_millions and
                    int(row["size_ratio"]) == args.size_ratio):
                grouped[int(row["repeat"])][row["arm"]] = row
    pairs = [(repeat, arms["regular"], arms["oracle"])
             for repeat, arms in sorted(grouped.items())
             if "regular" in arms and "oracle" in arms]
    if len(pairs) < args.minimum_pairs:
        raise SystemExit(
            f"need {args.minimum_pairs} regular/oracle pairs; found {len(pairs)}")

    checks = {}
    passed = True

    def record(name: str, ok: bool, details: object) -> None:
        nonlocal passed
        checks[name] = {"passed": ok, "details": details}
        passed &= ok

    exact_keys = ("get_operations", "put_operations", "scan_operations",
                  "user_write_bytes", "dbbench_seed", "experiment_fingerprint")
    mismatches = []
    for repeat, regular, oracle in pairs:
        for key in exact_keys:
            if regular[key] != oracle[key]:
                mismatches.append({"repeat": repeat, "field": key,
                                   "regular": regular[key],
                                   "oracle": oracle[key]})
    record("paired_workload_identity", not mismatches, mismatches)

    for metric, limit in (
            ("write_amplification", 0.05),
            ("point_read_amplification", 0.05),
            ("sorted_run_seeks_per_scan", 0.05)):
        differences = [abs(relative(float(oracle[metric]), float(regular[metric])))
                       for _, regular, oracle in pairs]
        record(metric, all(value <= limit for value in differences), differences)

    facts = []
    for repeat, regular, oracle in pairs:
        facts.append((repeat,
                      log_facts(Path(regular["result_directory"])),
                      log_facts(Path(oracle["result_directory"]))))

    debt_regressions = [relative(o["max_pending_bytes"], r["max_pending_bytes"])
                        for _, r, o in facts]
    record("maximum_pending_debt", all(x <= 0.05 for x in debt_regressions),
           debt_regressions)

    score_failures = []
    for repeat, regular, oracle in facts:
        for level, oracle_max in oracle["max_score_by_level"].items():
            baseline_max = regular["max_score_by_level"].get(level, 0.0)
            limit = max(1.05 * baseline_max, baseline_max + 0.10)
            if oracle_max > limit:
                score_failures.append({"repeat": repeat, "level": level,
                                       "oracle": oracle_max,
                                       "regular": baseline_max, "limit": limit})
    record("per_level_maximum_score", not score_failures, score_failures)

    input_regressions = [abs(relative(o["mean_l0_l1_input_bytes"],
                                      r["mean_l0_l1_input_bytes"]))
                         for _, r, o in facts]
    record("mean_l0_l1_input_size",
           all(math.isfinite(x) and x <= 0.10 for x in input_regressions),
           input_regressions)

    quantum_seconds = args.observation_period_ms / 1000.0
    stall_excess = [float(oracle["stall_seconds"])
                    - float(regular["stall_seconds"])
                    for _, regular, oracle in pairs]
    record("stall_duration", all(x <= quantum_seconds for x in stall_excess),
           stall_excess)
    stop_deltas = [oracle["stop_log_events"] - regular["stop_log_events"]
                   for _, regular, oracle in facts]
    record("no_new_oracle_stop_event", all(value <= 0 for value in stop_deltas),
           stop_deltas)

    held = [oracle["held_decision_max_jobs"] for _, _, oracle in facts]
    record("held_gate_repeated_service", any(value > 1 for value in held), held)
    zero_authorization = [
        {"repeat": repeat, "levels": oracle["zero_authorization_levels"]}
        for repeat, _, oracle in facts if oracle["zero_authorization_levels"]]
    record("every_due_level_authorized", not zero_authorization,
           zero_authorization)

    median_latencies = [oracle["median_due_to_eligible_micros"]
                        for _, _, oracle in facts]
    record("due_to_eligible_latency",
           all(math.isfinite(value) and
               value < args.observation_period_ms * 1000
               for value in median_latencies), median_latencies)
    rates = [oracle_facts["queries"] / float(oracle_row["elapsed_seconds"])
             for (_, _, oracle_row), (_, _, oracle_facts)
             in zip(pairs, facts)]
    target_rate = 1000.0 / args.observation_period_ms
    record("decision_rate", all(0.8 * target_rate <= rate <= 1.2 * target_rate
                                for rate in rates), rates)
    skipped = [oracle["skipped_ticks"] for _, _, oracle in facts]
    watchdog = [oracle["watchdog_expiries"] for _, _, oracle in facts]
    record("observation_health", all(value <= 1 for value in skipped) and
           all(value == 0 for value in watchdog),
           {"skipped_ticks": skipped, "watchdog_expiries": watchdog})

    report = {"schema_version": 1, "pairs": len(pairs),
              "size_millions": args.size_millions,
              "size_ratio": args.size_ratio, "checks": checks,
              "passed": passed}
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
