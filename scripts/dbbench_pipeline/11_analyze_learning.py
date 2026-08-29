#!/usr/bin/env python3
"""Does the learned residual do anything, and does more data help?

summary.csv answers "is the arm faster/smaller". It cannot answer "did the
agent learn", because a policy that never moves off its analytic prior produces
a perfectly respectable summary row. This reads the per-decision metrics log
instead and reports, per workload size and per level, the four quantities that
distinguish learning from its absence:

  samples    decisions the level's head actually received. The binding
             constraint on every previous result was sample budget, not
             algorithm.
  td_trend   mean TD loss over the last quarter divided by the first quarter.
             <1 converging, >1 diverging. Deep levels diverged at 5M in an
             earlier audit, so this is the first thing to look at.
  residual   mean |residual advantage| / mean |analytic advantage| over the last
             quarter. Near zero means the residual never left its zero
             initialisation and Q == the analytic prior.
  flips      fraction of decisions where prior+residual picks a different action
             than the prior alone. This is the only one that speaks to
             BEHAVIOUR: a residual can be large and still never change an
             argmax, in which case the learning is real but inert.

A learner that is working shows samples rising with size, td_trend at or below
1, residual growing away from zero, and a non-trivial flip rate. Flat residual
and zero flips across the whole ladder means the pipeline is running an
analytic prior with extra steps.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def advantage(value) -> float:
    """Prior/residual are logged per action; reduce to compact-minus-defer."""
    if value is None:
        return math.nan
    if isinstance(value, (list, tuple)):
        if len(value) < 2:
            return math.nan
        return float(value[1]) - float(value[0])
    return float(value)


def tail(values: list, fraction: float = 0.25) -> list:
    if not values:
        return []
    count = max(1, int(len(values) * fraction))
    return values[-count:]


def head(values: list, fraction: float = 0.25) -> list:
    if not values:
        return []
    count = max(1, int(len(values) * fraction))
    return values[:count]


def finite_mean(values: list) -> float:
    usable = [v for v in values if v is not None and math.isfinite(v)]
    return statistics.fmean(usable) if usable else math.nan


def read_arm(metrics_path: Path, stride: int = 1) -> dict[int, dict]:
    """Summarise one arm's per-decision metrics log.

    The log carries one fat record per level per decision -- 31 state features
    plus the raw observation, reward components and Q values -- and reaches
    hundreds of MB on the larger workloads. Reading it whole and json.loads-ing
    every record dominated this script's runtime, so the file is streamed and
    `stride` allows parsing only every Nth record. Every line is still counted,
    so `samples` reports the true decision count regardless of stride; only the
    series used for trends and ratios is subsampled, which is harmless for
    quantities that are means, trends or rates.
    """
    by_level: dict[int, dict[str, list]] = defaultdict(
        lambda: {"loss": [], "prior": [], "residual": [], "action": [],
                 "epsilon": [], "override": [], "reward": []})
    counted: dict[int, int] = defaultdict(int)
    per_level_seen: dict[int, int] = defaultdict(int)
    health = None
    health_path = metrics_path.with_name("server_summary.json")
    try:
        health = json.loads(health_path.read_text())
        if health.get("schema_version") != 1:
            health = None
    except (OSError, json.JSONDecodeError):
        health = None
    with metrics_path.open(errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            marker = line.find('"level":')
            level_hint = None
            if marker != -1:
                digits = line[marker + 8:marker + 20].strip().split(",")[0]
                if digits.lstrip("-").isdigit():
                    level_hint = int(digits)
            if level_hint is not None:
                counted[level_hint] += 1
                per_level_seen[level_hint] += 1
            if (stride > 1 and level_hint is not None and
                    per_level_seen[level_hint] % stride):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            level = record.get("level")
            if level is None:
                continue
            if level_hint is None:
                counted[int(level)] += 1
                per_level_seen[int(level)] += 1
            bucket = by_level[int(level)]
            if record.get("loss") is not None:
                bucket["loss"].append(float(record["loss"]))
            bucket["prior"].append(advantage(record.get("analytic_advantage")))
            bucket["residual"].append(advantage(record.get("residual_advantage")))
            bucket["action"].append(int(record.get("action", 0)))
            if record.get("epsilon") is not None:
                bucket["epsilon"].append(float(record["epsilon"]))
            if record.get("override_rate_100") is not None:
                bucket["override"].append(float(record["override_rate_100"]))
            bucket["reward"].append(float(record.get("reward", 0.0)))

    summary = {}
    for level, bucket in sorted(by_level.items()):
        losses = bucket["loss"]
        first = finite_mean(head(losses))
        last = finite_mean(tail(losses))
        priors = tail(bucket["prior"])
        residuals = tail(bucket["residual"])
        prior_scale = finite_mean([abs(v) for v in priors])
        residual_scale = finite_mean([abs(v) for v in residuals])

        flips = 0
        compared = 0
        for prior_value, residual_value in zip(bucket["prior"], bucket["residual"]):
            if not (math.isfinite(prior_value) and math.isfinite(residual_value)):
                continue
            compared += 1
            if (prior_value > 0) != (prior_value + residual_value > 0):
                flips += 1

        summary[level] = {
            "samples": counted.get(level, len(bucket["action"])),
            "parsed": len(bucket["action"]),
            "gradient_steps": (
                int(health["train_steps"]) if health is not None else len(losses)
            ),
            "gradient_steps_exact": 1.0 if health is not None else 0.0,
            "finalized_transitions": (
                int(health.get("finalized_transitions", 0))
                if health is not None else math.nan
            ),
            "replay_size": (
                int(health.get("replay_size", 0))
                if health is not None else math.nan
            ),
            "td_first_quarter": first,
            "td_last_quarter": last,
            "td_trend": (last / first if first and math.isfinite(first)
                         and first != 0 and math.isfinite(last) else math.nan),
            "prior_scale": prior_scale,
            "residual_scale": residual_scale,
            "residual_over_prior": (residual_scale / prior_scale
                                    if prior_scale and math.isfinite(prior_scale)
                                    and prior_scale != 0 else math.nan),
            "argmax_flip_rate": flips / compared if compared else math.nan,
            "compact_rate": finite_mean(bucket["action"]),
            "final_epsilon": bucket["epsilon"][-1] if bucket["epsilon"] else math.nan,
            "final_override_rate": (bucket["override"][-1]
                                    if bucket["override"] else math.nan),
            "mean_reward_last_quarter": finite_mean(tail(bucket["reward"])),
        }
    return summary


def fmt(value, spec="8.3f") -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return f"{'-':>{int(spec.split('.')[0])}}"
    return f"{value:{spec}}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results_root", type=Path)
    parser.add_argument("--arm", default="rl")
    parser.add_argument(
        "--stride", type=int, default=1,
        help="parse only every Nth record (default 1 = all). Trends, ratios "
             "and rates are unaffected by uniform subsampling; `samples` still "
             "counts every decision. Use 10-50 for a fast first look at the "
             "larger workloads.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    # Key by (size, ratio) and accumulate every repeat. Keying by size alone
    # silently kept only the last matching path, which with 3 ratios x 10
    # repeats discarded 29 runs out of 30.
    collected: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for metrics_path in sorted(args.results_root.glob(f"*M/T*/**/{args.arm}/metrics.jsonl")):
        if not (metrics_path.parent / "COMPLETED").exists():
            continue
        size_millions = None
        size_ratio = None
        for part in metrics_path.parts:
            if part.endswith("M") and part[:-1].isdigit():
                size_millions = int(part[:-1])
            elif part.startswith("T") and part[1:].isdigit():
                size_ratio = int(part[1:])
        if size_millions is None or size_ratio is None:
            continue
        print(f"  reading {metrics_path.parent.relative_to(args.results_root)}",
              file=sys.stderr, flush=True)
        collected[(size_millions, size_ratio)].append(
            read_arm(metrics_path, args.stride))
    if not collected:
        raise SystemExit(
            f"no completed '{args.arm}' arms with metrics.jsonl under {args.results_root}")

    # Average each scalar diagnostic across repeats, per level. Concatenating
    # the raw series instead would splice unrelated runs together and corrupt
    # the TD trend, which is measured within a run.
    runs: dict[tuple[int, int], dict] = {}
    repeat_counts: dict[tuple[int, int], int] = {}
    for key, per_run in sorted(collected.items()):
        repeat_counts[key] = len(per_run)
        levels = sorted({level for run in per_run for level in run})
        merged = {}
        for level in levels:
            present = [run[level] for run in per_run if level in run]
            merged[level] = {
                field: finite_mean([entry[field] for entry in present])
                for field in present[0]
            }
            merged[level]["repeats"] = len(present)
        runs[key] = merged

    print(f"arm: {args.arm}   (values are means across repeats)")
    exact = all(
        s.get("gradient_steps_exact", 0.0) == 1.0
        for run in runs.values() for s in run.values()
    )
    print("gradient-step source: " +
          ("exact server summaries" if exact else
           "APPROXIMATE sticky-loss records for at least one legacy run"))
    print()
    print("  size   T  lvl  rpt   samples   grad     td_1q     td_4q  td_trend  "
          "resid/prior  flip_rate  compact  final_eps")
    print("  " + "-" * 118)
    for (size_m, ratio) in sorted(runs):
        for level, s in sorted(runs[(size_m, ratio)].items()):
            print(f"  {size_m:4d}M {ratio:3d} {level:4d} {int(s['repeats']):4d} "
                  f"{s['samples']:9.0f} "
                  f"{s['gradient_steps']:6.0f} "
                  f"{fmt(s['td_first_quarter'], '9.4f')} "
                  f"{fmt(s['td_last_quarter'], '9.4f')} "
                  f"{fmt(s['td_trend'], '9.3f')} "
                  f"{fmt(s['residual_over_prior'], '12.4f')} "
                  f"{fmt(s['argmax_flip_rate'], '10.4f')} "
                  f"{fmt(s['compact_rate'], '8.3f')} "
                  f"{fmt(s['final_epsilon'], '10.4f')}")
        print()

    print("Read it like this:")
    print("  td_trend      > 1 on a level means its value function diverged.")
    print("  resid/prior  ~ 0 across the ladder means the residual never left")
    print("                 zero-init: Q equals the analytic prior and the DQN")
    print("                 is decorative.")
    print("  flip_rate    ~ 0 means learning never changes a decision, even if")
    print("                 resid/prior is large. This is the behavioural test.")
    print("  final_eps     should sit at its floor; if it is still near 1.0 the")
    print("                 wall-clock anneal did not take (see D6).")
    print("  samples       should scale with size. If it does not, the sample")
    print("                 budget is the constraint, not the algorithm.")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(
            {f"{k[0]}M_T{k[1]}": {str(lv): sv for lv, sv in v.items()}
             for k, v in runs.items()}, indent=2, sort_keys=True) + "\n")
        print()
        print(f"json: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
