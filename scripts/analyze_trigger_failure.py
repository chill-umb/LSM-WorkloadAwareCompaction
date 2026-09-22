#!/usr/bin/env python3
"""Read-only diagnosis report for regular/RL RocksDB experiment logs."""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path


EVENT = re.compile(r"EVENT_LOG_v1 (\{.*\})")
LEVEL_SUMMARY = re.compile(
    r"max score ([0-9.eE+-]+), estimated pending compaction bytes (\d+)"
)
TRIVIAL_MOVE_SUMMARY = re.compile(
    r"(\d{4}/\d{2}/\d{2}-\d{2}:\d{2}:\d{2})\.\d+.*?"
    r"Moved #(\d+) files to level-(\d+) (\d+) bytes OK"
)


def percentile(values: list[float], probability: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def events(path: Path) -> list[dict]:
    result = []
    for line in path.read_text(errors="replace").splitlines():
        match = EVENT.search(line)
        if not match:
            continue
        try:
            result.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            pass
    return result


def source_level(start: dict) -> int | None:
    levels = [int(key[7:]) for key in start if key.startswith("files_L")]
    return min(levels) if levels else None


def analyze_arm(directory: Path) -> dict:
    log_path = directory / "rocksdb_LOG.txt"
    if not log_path.exists():
        raise SystemExit(f"missing {log_path}")
    text = log_path.read_text(errors="replace")
    parsed = events(log_path)
    starts = {int(item["job"]): item for item in parsed
              if item.get("event") == "compaction_started"}
    finishes = {int(item["job"]): item for item in parsed
                if item.get("event") == "compaction_finished"}

    jobs = []
    for job_id, start in starts.items():
        finish = finishes.get(job_id, {})
        src = source_level(start)
        output = finish.get("output_level")
        files = sum(len(value) for key, value in start.items()
                    if key.startswith("files_L") and isinstance(value, list))
        jobs.append({
            "job": job_id,
            "time_micros": int(start.get("time_micros", 0)),
            "source_level": src,
            "output_level": output,
            "input_bytes": int(start.get("input_data_size", 0)),
            "output_bytes": int(finish.get("total_output_size", 0)),
            "input_files": files,
            "score": float(start.get("score", 0.0)),
            "reason": start.get("compaction_reason", "unknown"),
            "decision_id": int(start.get("rl_decision_id", 0)),
            "decision_generation": int(
                start.get("rl_decision_generation", 0)),
            "eligibility_generation": int(
                start.get("rl_eligibility_generation", 0)),
            "override_reason": int(start.get("rl_override_reason", 0)),
            # Completion-side phase is authoritative for jobs that were
            # already running when final drain began. Older logs have only the
            # start-side field, which remains a useful fallback.
            "drain": finish.get("rl_drain", start.get("rl_drain")),
            "compaction_time_micros": int(
                finish.get("compaction_time_micros", 0)),
            "completed": bool(finish),
        })

    by_pair: dict[tuple, dict] = defaultdict(
        lambda: {"jobs": 0, "input_bytes": 0, "output_bytes": 0})
    by_source = Counter()
    by_reason = Counter()
    phase = Counter()
    phase_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"jobs": 0, "input_bytes": 0, "output_bytes": 0,
                 "compaction_time_micros": 0})
    second = Counter()
    files_second = Counter()
    for job in jobs:
        pair = (job["source_level"], job["output_level"])
        aggregate = by_pair[pair]
        aggregate["jobs"] += 1
        aggregate["input_bytes"] += job["input_bytes"]
        aggregate["output_bytes"] += job["output_bytes"]
        by_source[job["source_level"]] += 1
        by_reason[job["reason"]] += 1
        drain_marker = job["drain"]
        phase_name = ("drain" if drain_marker in (True, 1, "true", "1") else
                      "workload" if drain_marker in (False, 0, "false", "0")
                      else "unmarked")
        phase[phase_name] += 1
        phase_totals[phase_name]["jobs"] += 1
        phase_totals[phase_name]["input_bytes"] += job["input_bytes"]
        phase_totals[phase_name]["output_bytes"] += job["output_bytes"]
        phase_totals[phase_name]["compaction_time_micros"] += job[
            "compaction_time_micros"]
        bucket = job["time_micros"] // 1_000_000
        second[bucket] += 1
        files_second[bucket] += job["input_files"]

    summaries = [(float(score), int(debt))
                 for score, debt in LEVEL_SUMMARY.findall(text)]
    trivial_moves = [
        {"second": second, "files": int(files), "output_level": int(level),
         "bytes": int(bytes_)}
        for second, files, level, bytes_ in TRIVIAL_MOVE_SUMMARY.findall(text)
    ]
    trivial_by_second = Counter()
    trivial_files_by_second = Counter()
    for move in trivial_moves:
        trivial_by_second[move["second"]] += 1
        trivial_files_by_second[move["second"]] += move["files"]
    input_sizes = [job["input_bytes"] for job in jobs]
    decisions = defaultdict(lambda: {"jobs": 0, "input_bytes": 0,
                                     "output_bytes": 0})
    for job in jobs:
        key = (job["decision_id"], job["decision_generation"],
               job["source_level"], job["override_reason"])
        entry = decisions[key]
        entry["jobs"] += 1
        entry["input_bytes"] += job["input_bytes"]
        entry["output_bytes"] += job["output_bytes"]

    return {
        "directory": str(directory),
        "compaction_jobs_started": len(jobs),
        "compaction_jobs_completed": sum(job["completed"] for job in jobs),
        "input_files": sum(job["input_files"] for job in jobs),
        "input_bytes": sum(input_sizes),
        "output_bytes": sum(job["output_bytes"] for job in jobs),
        "input_size_bytes": {
            "mean": statistics.fmean(input_sizes) if input_sizes else None,
            "p50": percentile(input_sizes, 0.50) if input_sizes else None,
            "p95": percentile(input_sizes, 0.95) if input_sizes else None,
            "maximum": max(input_sizes, default=0),
        },
        "maximum_compaction_score": max((x[0] for x in summaries), default=0),
        "maximum_pending_compaction_bytes": max(
            (x[1] for x in summaries), default=0),
        "busiest_second_jobs": max(second.values(), default=0),
        "busiest_second_input_files": max(files_second.values(), default=0),
        "trivial_move_jobs": len(trivial_moves),
        "trivial_move_files": sum(move["files"] for move in trivial_moves),
        "trivial_move_bytes": sum(move["bytes"] for move in trivial_moves),
        "busiest_second_trivial_move_jobs": max(
            trivial_by_second.values(), default=0),
        "busiest_second_trivial_move_files": max(
            trivial_files_by_second.values(), default=0),
        "jobs_by_source_level": {str(k): v for k, v in sorted(by_source.items())},
        "jobs_and_bytes_by_source_output": {
            f"L{src}->L{out}": value
            for (src, out), value in sorted(
                by_pair.items(), key=lambda item: str(item[0]))
        },
        "jobs_by_reason": dict(sorted(by_reason.items())),
        "jobs_by_phase": dict(phase),
        "phase_totals": dict(sorted(phase_totals.items())),
        "decision_intervals": [
            {"decision_id": key[0], "decision_generation": key[1],
             "source_level": key[2], "override_reason": key[3], **value}
            for key, value in sorted(decisions.items())
        ],
        "phase_note": ("drain markers unavailable in this historical log"
                       if phase.get("unmarked") else
                       "workload and final drain are separated by rl_drain"),
    }


def markdown(report: dict) -> str:
    lines = ["# Trigger failure log analysis", ""]
    for arm, data in report["arms"].items():
        sizes = data["input_size_bytes"]
        lines.extend([
            f"## {arm}", "",
            f"- Compaction jobs: {data['compaction_jobs_started']} started / "
            f"{data['compaction_jobs_completed']} completed",
            f"- Input files: {data['input_files']}",
            f"- Compaction input bytes: {data['input_bytes']}",
            f"- Input-size mean/p50/p95: {sizes['mean']} / "
            f"{sizes['p50']} / {sizes['p95']} bytes",
            f"- Maximum score: {data['maximum_compaction_score']}",
            f"- Maximum pending debt: {data['maximum_pending_compaction_bytes']} bytes",
            f"- Busiest second: {data['busiest_second_jobs']} jobs, "
            f"{data['busiest_second_input_files']} input files",
            f"- Trivial moves: {data['trivial_move_jobs']} jobs / "
            f"{data['trivial_move_files']} files / "
            f"{data['trivial_move_bytes']} bytes",
            f"- Busiest trivial-move second: "
            f"{data['busiest_second_trivial_move_jobs']} jobs / "
            f"{data['busiest_second_trivial_move_files']} files",
            f"- Phase accounting: {data['jobs_by_phase']} "
            f"({data['phase_note']})", "",
            f"- Phase bytes/time: {data['phase_totals']}", "",
            "Source/output totals:", "",
        ])
        for pair, values in data["jobs_and_bytes_by_source_output"].items():
            lines.append(f"- {pair}: {values}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path,
                        help="directory containing regular/ and rl/")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    report = {"schema_version": 1, "arms": {}}
    for arm in ("regular", "rl"):
        directory = args.results / arm
        if directory.exists():
            report["arms"][arm] = analyze_arm(directory)
    if not report["arms"]:
        raise SystemExit(f"no regular/ or rl/ below {args.results}")
    rendered = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered + "\n")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(markdown(report) + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
