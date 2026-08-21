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


def read_arm(metrics_path: Path) -> dict[int, dict]:
    by_level: dict[int, dict[str, list]] = defaultdict(
        lambda: {"loss": [], "prior": [], "residual": [], "action": [],
                 "epsilon": [], "override": [], "reward": []})
    for line in metrics_path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        level = record.get("level")
        if level is None:
            continue
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
            "samples": len(bucket["action"]),
            "gradient_steps": len(losses),
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
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    runs = {}
    for metrics_path in sorted(args.results_root.glob(f"*M/T*/**/{args.arm}/metrics.jsonl")):
        if not (metrics_path.parent / "COMPLETED").exists():
            continue
        size_label = None
        for part in metrics_path.parts:
            if part.endswith("M") and part[:-1].isdigit():
                size_label = part
        if size_label is None:
            continue
        runs[int(size_label[:-1])] = read_arm(metrics_path)
    if not runs:
        raise SystemExit(
            f"no completed '{args.arm}' arms with metrics.jsonl under {args.results_root}")

    print(f"arm: {args.arm}")
    print()
    print("  size  lvl   samples   grad     td_1q     td_4q  td_trend  "
          "resid/prior  flip_rate  compact  final_eps")
    print("  " + "-" * 105)
    for size_m in sorted(runs):
        for level, s in sorted(runs[size_m].items()):
            print(f"  {size_m:4d}M {level:3d} {s['samples']:9d} "
                  f"{s['gradient_steps']:6d} "
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
            {str(k): {str(lv): sv for lv, sv in v.items()}
             for k, v in runs.items()}, indent=2, sort_keys=True) + "\n")
        print()
        print(f"json: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
