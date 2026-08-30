#!/usr/bin/env python3
"""Evaluate the deterministic trigger-oracle parity gate from section 14.2.

See ORACLE_GATE_FIX_PLAN.md revision 2. Three properties of this evaluator are
deliberate and easy to undo by accident:

  D1  Per-level comparisons are drawn only from the pressure episode log, which
      BOTH arms write. The trigger trace exists only for RL-style pickers, so
      folding it into a compared quantity measures the instrument rather than
      the controller.
  D4  Quantities whose seed-to-seed spread exceeds their effect size are judged
      on a paired confidence bound and report `insufficient_pairs` rather than
      passing or failing on three repeats.
  D7  The trace-derived due-to-eligible latency is quantised to one worker tick
      and is reported as informational only. An acceptance threshold below one
      tick cannot be stated against it; use the event-time histogram exported
      by the picker instead.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from pipeline_stats import ci95, envelope_verdict

SCHEMA_VERSION = 2

EVENT = re.compile(r"EVENT_LOG_v1 (\{.*\})")
LEVEL_SUMMARY = re.compile(
    r"max score ([0-9.eE+-]+), estimated pending compaction bytes (\d+)")
DIAGNOSTICS = re.compile(
    r"RL trigger diagnostics:.*?queries=(\d+).*?skipped_ticks=(\d+).*?"
    r"watchdog_expiries=(\d+)")
# Event-time due->admission latency (D7 item 5), emitted by the picker's
# diagnostics line. Absent on binaries built before that instrumentation.
ADMISSION_LATENCY = re.compile(
    r"due_to_admission_micros_p50=(\d+).*?due_to_admission_micros_p90=(\d+)"
    r".*?due_to_admission_micros_max=(\d+).*?due_never_admitted=(\d+)")
POSTURE_ADMISSIONS = re.compile(r"posture_admissions=(\d+)")


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


def due_to_eligible(episodes: list[dict],
                    trace_by_level: dict[int, list[tuple[int, dict]]]) -> dict:
    """Latency from a level going due to a fresh authorization of that episode.

    Three corrections over the original implementation, all of which changed
    the number rather than merely tidying it (D7):

      1. The search is bounded by the episode's own end. Previously any later
         authorization on the same level could be credited to an episode that
         was never acted on at all.
      2. A gate already held open from an earlier decision no longer counts as
         instant authorization. The trace carries `decision_generation`, so a
         genuine transition is distinguishable from a continuing permit.
      3. Episodes with no authorization are counted instead of silently
         dropped, so the median has a visible denominator.

    What none of this can fix: the trace is written once per worker tick, so
    every sample here is quantised to the observation interval. The returned
    distribution is a cross-check, not a measurement of dispatch latency.
    """
    samples: list[int] = []
    considered = 0
    unauthorized = 0
    already_open = 0
    unmeasurable = 0
    for episode in episodes:
        level = int(episode["level"])
        start = int(episode["start_micros"])
        end = int(episode["end_micros"])
        entries = trace_by_level.get(level, [])
        window = [(index, timestamp, state)
                  for index, (timestamp, state) in enumerate(entries)
                  if start <= timestamp <= end]
        if not window:
            # The episode opened and closed between two ticks. The controller
            # never observed this due interval, so there is no latency to
            # attribute to it either way.
            unmeasurable += 1
            continue
        considered += 1
        authorized = next(((index, timestamp, state)
                           for index, timestamp, state in window
                           if int(state.get("gate_mode", 0)) != 0), None)
        if authorized is None:
            unauthorized += 1
            continue
        index, timestamp, state = authorized
        previous = entries[index - 1] if index > 0 else None
        if (previous is not None and
                int(previous[1].get("gate_mode", 0)) != 0 and
                int(previous[1].get("decision_generation", 0)) ==
                int(state.get("decision_generation", 0)) and
                previous[0] < start):
            # The permit predates the episode: nothing authorized this due
            # condition, it inherited an open gate. Counting it as zero latency
            # is what made the median optimistic.
            already_open += 1
            continue
        samples.append(timestamp - start)
    ordered = sorted(samples)

    def percentile(fraction: float):
        if not ordered:
            return math.nan
        position = min(len(ordered) - 1,
                       max(0, int(round(fraction * (len(ordered) - 1)))))
        return float(ordered[position])

    return {
        "median_due_to_eligible_micros": (
            statistics.median(ordered) if ordered else math.nan),
        "p90_due_to_eligible_micros": percentile(0.90),
        "max_due_to_eligible_micros": float(ordered[-1]) if ordered else math.nan,
        "episodes_considered": considered,
        "episodes_measured": len(ordered),
        "episodes_unauthorized": unauthorized,
        "episodes_gate_already_open": already_open,
        "episodes_unmeasurable_between_ticks": unmeasurable,
    }


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
    admission_matches = ADMISSION_LATENCY.findall(text)
    admission = tuple(map(int, admission_matches[-1])) \
        if admission_matches else None
    posture_matches = POSTURE_ADMISSIONS.findall(text)

    episodes = read_jsonl(directory / "pressure_episodes.jsonl")
    # D1: episodes are the only source of compared per-level quantities. Both
    # arms construct a CompactionPressureObserver and both write this log; the
    # trigger trace below is written only by RLCompactionPicker, so anything
    # folded in from it turns a controller comparison into an instrument
    # comparison.
    max_score_by_level: dict[int, float] = defaultdict(float)
    episode_levels = set()
    truncated_episodes = 0
    for episode in episodes:
        level = int(episode["level"])
        episode_levels.add(level)
        max_score_by_level[level] = max(
            max_score_by_level[level], float(episode["max_score"]))
        if episode.get("truncated"):
            truncated_episodes += 1

    trace = read_jsonl(directory / "trigger_trace.jsonl")
    due_levels = set()
    authorized_levels = set()
    trace_by_level: dict[int, list[tuple[int, dict]]] = defaultdict(list)
    for frame in trace:
        timestamp = int(frame.get("time_micros", 0))
        for level in frame.get("levels", []):
            index = int(level["level"])
            trace_by_level[index].append((timestamp, level))
            if float(level.get("score", 0)) >= 1.0:
                due_levels.add(index)
                if int(level.get("gate_mode", 0)) != 0:
                    authorized_levels.add(index)

    facts = {
        "max_score": max((item[0] for item in summaries), default=0.0),
        "max_pending_bytes": max((item[1] for item in summaries), default=0),
        "max_score_by_level": dict(max_score_by_level),
        "episode_levels": sorted(episode_levels),
        "episode_count": len(episodes),
        "truncated_episodes": truncated_episodes,
        "mean_l0_l1_input_bytes": (
            statistics.fmean(l0_l1_inputs) if l0_l1_inputs else math.nan),
        "held_decision_max_jobs": max(decision_jobs.values(), default=0),
        "held_decision_jobs": sorted(decision_jobs.values(), reverse=True)[:16],
        "held_decisions_serving_multiple": sum(
            1 for value in decision_jobs.values() if value > 1),
        "held_decisions_total": len(decision_jobs),
        "queries": diagnostics[0],
        "skipped_ticks": diagnostics[1],
        "watchdog_expiries": diagnostics[2],
        "posture_admissions": (
            int(posture_matches[-1]) if posture_matches else None),
        # D1 item 3: both sets now come from the trace, so a level appearing in
        # one and not the other is a fact about the controller.
        "due_levels": sorted(due_levels),
        "zero_authorization_levels": sorted(due_levels - authorized_levels),
        # Retained separately: a level whose entire due interval fell between
        # two ticks is real, but demanding authorization for a condition the
        # controller never sampled is not a defensible check.
        "levels_due_only_in_episodes": sorted(episode_levels - due_levels),
        "stop_log_events": len(re.findall(r"Stopping writes", text)),
    }
    facts.update(due_to_eligible(episodes, trace_by_level))
    if admission is not None:
        facts.update({
            "due_to_admission_micros_p50": admission[0],
            "due_to_admission_micros_p90": admission[1],
            "due_to_admission_micros_max": admission[2],
            "due_never_admitted": admission[3],
        })
    return facts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("summary", type=Path)
    parser.add_argument("--size-millions", type=int, default=1)
    parser.add_argument("--size-ratio", type=int, default=2)
    parser.add_argument("--minimum-pairs", type=int, default=3)
    parser.add_argument("--observation-period-ms", type=float, default=50.0)
    parser.add_argument(
        "--minimum-envelope-pairs", type=int, default=5,
        help="sanity floor on pair count for envelope checks. The binding "
             "constraint is normally the per-metric required_pairs; this only "
             "guards against a dispersion estimate drawn from too few samples "
             "to be worth a t-interval at all. Setting it to a large constant "
             "makes required_pairs decorative and spends runs on checks that "
             "were already decisive.")
    parser.add_argument(
        "--stall-allowance-seconds", type=float, default=None,
        help="absolute stall-duration allowance. Deliberately has no default: "
             "the previous behaviour derived it from the observation period, "
             "so tuning the controller tightened its own tolerance. Preregister "
             "this from the baseline sweep's own dispersion before quoting a "
             "verdict on stalls.")
    parser.add_argument(
        "--admission-latency-limit-micros", type=float, default=None,
        help="acceptance threshold for the event-time due->admission latency. "
             "Do not state this against the trace-derived figure, which is "
             "quantised to one observation period.")
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

    checks: dict[str, dict] = {}
    informational: dict[str, object] = {}

    def invariant(name: str, ok: bool, details: object) -> None:
        """A single violation is disqualifying, so every repeat must hold."""
        checks[name] = {"kind": "invariant", "verdict": "passed" if ok else "failed",
                        "passed": ok, "details": details}

    def envelope(name: str, values: list[float], limit: float,
                 two_sided: bool = False) -> None:
        checks[name] = envelope_verdict(values, limit,
                                        args.minimum_envelope_pairs,
                                        two_sided=two_sided)

    exact_keys = ("get_operations", "put_operations", "scan_operations",
                  "user_write_bytes", "dbbench_seed", "experiment_fingerprint")
    mismatches = []
    for repeat, regular, oracle in pairs:
        for key in exact_keys:
            if regular[key] != oracle[key]:
                mismatches.append({"repeat": repeat, "field": key,
                                   "regular": regular[key],
                                   "oracle": oracle[key]})
    invariant("paired_workload_identity", not mismatches, mismatches)
    identities = {
        (regular.get("workload_profile"),
         regular.get("experiment_fingerprint"))
        for _, regular, _ in pairs
    }
    invariant(
        "consistent_experiment_identity",
        len(identities) == 1 and all(profile and fingerprint
                                     for profile, fingerprint in identities),
        [{"workload_profile": profile, "experiment_fingerprint": fingerprint}
         for profile, fingerprint in sorted(
             identities, key=lambda item: (str(item[0]), str(item[1])))],
    )

    facts = []
    for repeat, regular, oracle in pairs:
        facts.append((repeat,
                      log_facts(Path(regular["result_directory"])),
                      log_facts(Path(oracle["result_directory"]))))

    # Parity envelopes. These are two-sided: the oracle drifting either way from
    # native leveled behaviour is the finding, not just drifting upward.
    for metric, limit in (
            ("write_amplification", 0.05),
            ("point_read_amplification", 0.05),
            ("sorted_run_seeks_per_scan", 0.05)):
        envelope(metric,
                 [relative(float(oracle[metric]), float(regular[metric]))
                  for _, regular, oracle in pairs],
                 limit, two_sided=True)

    envelope("mean_l0_l1_input_size",
             [relative(o["mean_l0_l1_input_bytes"], r["mean_l0_l1_input_bytes"])
              for _, r, o in facts],
             0.10, two_sided=True)
    envelope("maximum_pending_debt",
             [relative(o["max_pending_bytes"], r["max_pending_bytes"])
              for _, r, o in facts],
             0.05)

    stall_excess = [float(oracle["stall_seconds"]) - float(regular["stall_seconds"])
                    for _, regular, oracle in pairs]
    if args.stall_allowance_seconds is None:
        checks["stall_duration"] = {
            "kind": "paired_envelope",
            "verdict": "no_allowance_configured",
            "passed": None,
            "per_repeat": stall_excess,
            "ci95": ci95(stall_excess),
            "note": "pass --stall-allowance-seconds, preregistered from the "
                    "baseline sweep's dispersion; it must not be derived from "
                    "the observation period",
        }
    else:
        envelope("stall_duration", stall_excess, args.stall_allowance_seconds)
        checks["stall_duration"]["allowance_seconds"] = args.stall_allowance_seconds
    envelope("no_new_oracle_stop_event",
             [float(o["stop_log_events"] - r["stop_log_events"])
              for _, r, o in facts],
             0.0)

    # D1: compare only levels the baseline actually exercised. A level the
    # baseline never drove to due has no defined baseline maximum, and the old
    # `.get(level, 0.0)` turned that absence into a limit of 0.1 that any
    # populated level exceeds. This is an envelope rather than an all-repeat
    # invariant: it is a maximum of a stochastic trajectory, not a protocol
    # law. The worst level is selected within each paired repeat so levels are
    # not incorrectly counted as independent samples.
    score_values, score_details, unexercised = maximum_score_growth(facts)
    if score_values:
        envelope("per_level_maximum_score", score_values, 1.0)
    else:
        checks["per_level_maximum_score"] = {
            "kind": "paired_envelope",
            "verdict": "instrument_unavailable",
            "passed": None,
            "per_repeat": [],
        }
    checks["per_level_maximum_score"].update({
        "normalization": "(oracle - regular) / max(0.05 * regular, 0.10)",
        "worst_level_by_repeat": score_details,
        "comparable_repeat_count": len(score_values),
    })
    informational["baseline_unexercised_levels"] = unexercised

    held = [oracle["held_decision_max_jobs"] for _, _, oracle in facts]
    invariant("held_gate_repeated_service", any(value > 1 for value in held),
              held)
    # Reported because D3 moves this quantity in both directions: an event wake
    # lowers it by installing a new frame, while a crossing-posture admission
    # raises it by charging the job to the preceding decision. A reader
    # comparing gate outputs across stages must not read that as instability.
    informational["held_gate_distribution"] = [
        {"repeat": repeat,
         "max_jobs": oracle["held_decision_max_jobs"],
         "decisions_serving_multiple": oracle["held_decisions_serving_multiple"],
         "decisions_total": oracle["held_decisions_total"],
         "largest": oracle["held_decision_jobs"],
         "posture_admissions": oracle["posture_admissions"]}
        for repeat, _, oracle in facts]

    zero_authorization = [
        {"repeat": repeat, "levels": oracle["zero_authorization_levels"]}
        for repeat, _, oracle in facts if oracle["zero_authorization_levels"]]
    invariant("every_due_level_authorized", not zero_authorization,
              zero_authorization)

    rates = [oracle_facts["queries"] / float(oracle_row["elapsed_seconds"])
             for (_, _, oracle_row), (_, _, oracle_facts)
             in zip(pairs, facts)]
    target_rate = 1000.0 / args.observation_period_ms
    invariant("decision_rate",
              all(0.8 * target_rate <= rate <= 1.2 * target_rate
                  for rate in rates), rates)
    skipped = [oracle["skipped_ticks"] for _, _, oracle in facts]
    watchdog = [oracle["watchdog_expiries"] for _, _, oracle in facts]
    invariant("observation_health",
              all(value <= 1 for value in skipped) and
              all(value == 0 for value in watchdog),
              {"skipped_ticks": skipped, "watchdog_expiries": watchdog})

    # D7: the trace-derived latency is quantised to one observation period, so
    # it cannot carry an acceptance threshold below one tick. It is reported,
    # with its denominators, as a cross-check on the event-time histogram.
    informational["due_to_eligible_trace_quantised"] = {
        "observation_period_micros": args.observation_period_ms * 1000,
        "note": "quantised to one worker tick; informational only",
        "per_repeat": [
            {"repeat": repeat,
             "median_micros": oracle["median_due_to_eligible_micros"],
             "p90_micros": oracle["p90_due_to_eligible_micros"],
             "max_micros": oracle["max_due_to_eligible_micros"],
             "episodes_considered": oracle["episodes_considered"],
             "episodes_measured": oracle["episodes_measured"],
             "episodes_unauthorized": oracle["episodes_unauthorized"],
             "episodes_gate_already_open": oracle["episodes_gate_already_open"],
             "episodes_unmeasurable_between_ticks":
                 oracle["episodes_unmeasurable_between_ticks"]}
            for repeat, _, oracle in facts],
    }

    admission_p50 = [oracle.get("due_to_admission_micros_p50")
                     for _, _, oracle in facts]
    if any(value is None for value in admission_p50):
        checks["due_to_admission_latency"] = {
            "kind": "invariant",
            "verdict": "instrument_unavailable",
            "passed": None,
            "details": "binary predates the event-time due->admission histogram; "
                       "rebuild before stating a latency acceptance threshold",
        }
    elif args.admission_latency_limit_micros is None:
        checks["due_to_admission_latency"] = {
            "kind": "invariant",
            "verdict": "no_limit_configured",
            "passed": None,
            "details": {"p50": admission_p50,
                        "p90": [o["due_to_admission_micros_p90"]
                                for _, _, o in facts],
                        "max": [o["due_to_admission_micros_max"]
                                for _, _, o in facts],
                        "never_admitted": [o["due_never_admitted"]
                                           for _, _, o in facts]},
        }
    else:
        invariant("due_to_admission_latency",
                  all(value <= args.admission_latency_limit_micros
                      for value in admission_p50),
                  {"p50": admission_p50,
                   "limit_micros": args.admission_latency_limit_micros})

    informational["episode_log_health"] = [
        {"repeat": repeat,
         "regular_episodes": regular["episode_count"],
         "regular_truncated": regular["truncated_episodes"],
         "oracle_episodes": oracle["episode_count"],
         "oracle_truncated": oracle["truncated_episodes"],
         "oracle_levels_due_only_in_episodes":
             oracle["levels_due_only_in_episodes"]}
        for repeat, regular, oracle in facts]

    decided = [check for check in checks.values() if check["passed"] is not None]
    undecided = [name for name, check in checks.items()
                 if check["passed"] is None]
    failed = [name for name, check in checks.items() if check["passed"] is False]
    if failed:
        verdict = "failed"
    elif undecided:
        verdict = "undecided"
    else:
        verdict = "passed"

    report = {"schema_version": SCHEMA_VERSION, "pairs": len(pairs),
              "size_millions": args.size_millions,
              "size_ratio": args.size_ratio,
              "observation_period_ms": args.observation_period_ms,
              "stall_allowance_seconds": args.stall_allowance_seconds,
              "minimum_envelope_pairs": args.minimum_envelope_pairs,
              "checks": checks,
              "informational": informational,
              "undecided_checks": sorted(undecided),
              "failed_checks": sorted(failed),
              "decided_checks": len(decided),
              "verdict": verdict,
              # Retained for callers that predate the verdict field. A gate that
              # cannot decide is not a gate that passed.
              "passed": verdict == "passed"}
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    return {"passed": 0, "failed": 1, "undecided": 2}[verdict]


if __name__ == "__main__":
    raise SystemExit(main())
