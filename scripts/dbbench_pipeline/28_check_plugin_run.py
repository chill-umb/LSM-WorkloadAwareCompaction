#!/usr/bin/env python3
"""Checks one run of the controller plugin (plan §6.4 step 5; PATHWAYS
ACT-3, ARCH-2's "zero masked actions executed", ARCH-5, A-Impl-8). Reads
db_bench's output and the plugin's decision and transition logs.

  started    db_bench printed RL_PLUGIN_STARTED created=1, then
             RL_PLUGIN_STOPPED: the controller ran from n_w to the drain
  logs       every line of both logs parses (no truncated last line); the
             decision log starts with `start` and ends with `stop`; every
             level closed at least one interval in the transition log
  mode       the start line and every decision carry the expected mode
  masks      no decision took an action its own mask forbade
  ACT-3      of the changes the decisions requested (requested != old), at
             least 99% appeared in the published score within one control
             interval (PATHWAYS A §6): an accepted `apply` whose
             first_id..last_id covers the decision carried the requested
             value for that level, the snapshot read right after it had it
             (`seen`), and its op is no later than the level's next decision,
             one interval of N_j/k operations on (L0: one flush, N_0/K0_cfg,
             G-iv). A change held by the
             SetOptions cap past that, or overwritten before any call, fails.
             A level's last change with no accepted call after it at all,
             cut off by the drain, is reported as truncated and not judged;
             one that a later call left out fails. Needs at least
             --min-changes judged changes
  hold-only  no apply, apply_held or repair line: hold never calls
             SetOptions (ARCH-5)
  fallback   with --expect-fallback, a fallback line and a stop line saying
             fallback 1; otherwise neither

Exit 0 when every check passes, 1 otherwise; the report says which failed.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

ACTIONS = ("hold", "compact", "defer", "expand")  # the mask's order (log.h)
ACT3_SHARE = 0.99  # PATHWAYS A §6
STARTED = re.compile(r"^RL_PLUGIN_STARTED created=(\d)$", re.M)
STOPPED = re.compile(r"^RL_PLUGIN_STOPPED$", re.M)


def read_lines(path: Path) -> tuple[list[dict], list[str]]:
    """Parsed lines, and the problems: an unreadable file or a bad line."""
    if not path.exists():
        return [], [f"{path.name} missing"]
    lines, problems = [], []
    for number, text in enumerate(path.read_text().splitlines(), 1):
        try:
            line = json.loads(text)
        except json.JSONDecodeError:
            problems.append(f"{path.name} line {number} does not parse")
            continue
        if not isinstance(line, dict) or "type" not in line:
            problems.append(f"{path.name} line {number} has no type")
            continue
        lines.append(line)
    return lines, problems


def act3_changes(decisions: list[dict]) -> dict:
    """ACT-3 per requested change: faithful, late or lost, or truncated."""
    made = [line for line in decisions if line["type"] == "decision"]
    applies = [line for line in decisions if line["type"] == "apply"
               and line.get("ok") == 1 and line.get("seen") == 1]
    position = {id(line): index for index, line in enumerate(decisions)}
    accepted_at = [position[id(line)] for line in decisions
                   if line["type"] == "apply" and line.get("ok") == 1]

    def carried(apply, decision):
        value = (apply.get("k0") if decision["level"] == 0
                 else (apply.get("m") or [None] * (decision["level"] + 1))
                 [decision["level"]])
        return (isinstance(value, (int, float)) and
                apply.get("first_id", 0) <= decision["id"] <=
                apply.get("last_id", -1) and
                math.isclose(value, decision["requested"], rel_tol=1e-12))

    faithful, failed, truncated, by_level = 0, [], [], {}
    for index, decision in enumerate(made):
        if math.isclose(decision.get("requested", 0), decision.get("old", 0),
                        rel_tol=1e-12):
            continue
        deadline = next((later["op"] for later in made[index + 1:]
                         if later["level"] == decision["level"]), None)
        carrier = next((a for a in applies if carried(a, decision)), None)
        if carrier and (deadline is None or carrier["op"] <= deadline):
            faithful += 1
        elif deadline is None and not any(
                at > position[id(decision)] for at in accepted_at):
            # Cut off by the drain: no call at all came after it. A later
            # call that left it out lost it, which is a failure.
            truncated.append(decision["id"])
        else:
            failed.append(decision["id"])
            by_level[decision["level"]] = by_level.get(decision["level"], 0) + 1
    judged = faithful + len(failed)
    return {"changes": judged, "faithful": faithful,
            "share": faithful / judged if judged else None,
            "failed_ids": failed[:20], "failed_by_level": by_level,
            "truncated_ids": truncated}


def check(stdout: str, decisions: list[dict], transitions: list[dict],
          log_problems: list[str], mode: str, expect_fallback: bool,
          min_changes: int) -> dict:
    checks: dict[str, dict] = {}

    def record(name, problems, **details):
        checks[name] = {"passed": not problems, "problems": problems,
                        **details}

    started = STARTED.findall(stdout)
    record("started", [] if started == ["1"] and STOPPED.search(stdout) else
           [f"RL_PLUGIN_STARTED {started or 'missing'}, RL_PLUGIN_STOPPED "
            f"{'present' if STOPPED.search(stdout) else 'missing'}"])

    problems = list(log_problems)
    types = [line["type"] for line in decisions]
    if not types or types[0] != "start":
        problems.append("the decision log does not start with `start`")
    if not types or types[-1] != "stop":
        problems.append("the decision log does not end with `stop`")
    num_levels = decisions[0].get("num_levels") if types[:1] == ["start"] else None
    closed = {line.get("level") for line in transitions
              if line["type"] == "transition"}
    if isinstance(num_levels, int):
        unclosed = sorted(set(range(num_levels)) - closed)
        if unclosed:
            problems.append(f"levels with no transition: {unclosed}")
    else:
        problems.append("the start line has no num_levels")
    record("logs", problems, decision_lines=len(decisions),
           transition_lines=len(transitions))

    made = [line for line in decisions if line["type"] == "decision"]
    wrong_mode = sorted({line.get("mode") for line in made} - {mode})
    start_mode = decisions[0].get("mode") if types[:1] == ["start"] else None
    record("mode", ([f"start says {start_mode}"] if start_mode != mode else []) +
           ([f"decisions in modes {wrong_mode}"] if wrong_mode else []),
           decisions=len(made))

    masked = [line.get("id") for line in made
              if line.get("action") not in ACTIONS
              or not line.get("mask")
              or line["mask"][ACTIONS.index(line["action"])] != 1]
    record("masks", [f"decisions {masked[:10]} took a masked action"]
           if masked else [], masked_actions=len(masked))

    applies = [line for line in decisions if line["type"] == "apply"]
    accepted = [line for line in applies if line.get("ok") == 1]
    fidelity = act3_changes(decisions)
    held = sum(1 for line in decisions if line["type"] == "apply_held")
    act3 = []
    if fidelity["changes"] < min_changes:
        act3.append(f"{fidelity['changes']} judged changes, fewer than "
                    f"{min_changes}")
    if fidelity["share"] is not None and fidelity["share"] < ACT3_SHARE:
        act3.append(f"only {fidelity['faithful']} of {fidelity['changes']} "
                    f"changes reached the published score within one control "
                    f"interval ({fidelity['share']:.4f} < {ACT3_SHARE}); "
                    f"failed by level {fidelity['failed_by_level']}; {held} "
                    f"held by the SetOptions cap (a cap longer than a level's "
                    f"control interval makes ACT-3 fail by design); "
                    f"first ids {fidelity['failed_ids']}")
    record("act3", act3, **fidelity, held_calls=held,
           accepted_calls=len(accepted),
           refused_calls=len(applies) - len(accepted),
           calls_seen=sum(1 for line in accepted if line.get("seen") == 1))

    if mode == "hold-only":
        calls = [t for t in types if t in ("apply", "apply_held", "repair")]
        record("hold_only", [f"{len(calls)} apply, apply_held or repair "
                             "lines"] if calls else [])

    fallbacks = types.count("fallback")
    stop_fallback = decisions[-1].get("fallback") if types[-1:] == ["stop"] else None
    if expect_fallback:
        record("fallback", [] if fallbacks and stop_fallback == 1 else
               [f"expected a fallback: {fallbacks} fallback lines, stop "
                f"fallback {stop_fallback}"])
    else:
        record("fallback", [f"{fallbacks} fallback lines: " + "; ".join(
            str(line.get("reason")) for line in decisions
            if line["type"] == "fallback")] if fallbacks or stop_fallback else [])

    failed = sorted(name for name, c in checks.items() if not c["passed"])
    return {"mode": mode, "verdict": "failed" if failed else "passed",
            "failed_checks": failed, "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stdout", type=Path, required=True,
                        help="db_bench's output (03's run.log)")
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--transitions", type=Path, required=True)
    parser.add_argument("--mode", required=True,
                        choices=("hold-only", "rules", "prior-only", "learned"))
    parser.add_argument("--expect-fallback", action="store_true")
    parser.add_argument("--min-changes", type=int, default=0,
                        help="judged ACT-3 changes the run must contain")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    decisions, decision_problems = read_lines(args.decisions)
    transitions, transition_problems = read_lines(args.transitions)
    report = check(args.stdout.read_text(errors="replace"), decisions,
                   transitions, decision_problems + transition_problems,
                   args.mode, args.expect_fallback, args.min_changes)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    act3 = report["checks"]["act3"]
    print(f"[plugin run] {report['verdict']} ({args.mode}): failed "
          f"{report['failed_checks']}; ACT-3 {act3['faithful']}/"
          f"{act3['changes']} changes in time, "
          f"{len(act3['truncated_ids'])} cut off by the drain")
    return 0 if report["verdict"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
