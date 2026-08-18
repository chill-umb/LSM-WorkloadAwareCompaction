#!/usr/bin/env python3
"""Shared paired-difference statistics for the db_bench pipeline evaluators.

`07_evaluate_paired.py` (the research acceptance criterion) and
`09_evaluate_oracle_parity.py` (the engineering gate that precedes it) must
judge paired differences with the same instrument. The gate has no business
using a weaker statistical test than the criterion it gates, and two
implementations of the same interval would eventually disagree.

The interval is the two-sided 95% Student-t interval on the paired differences.
`upper` is therefore a one-sided 97.5% bound, which is the conservative
direction; the acceptance rules in both evaluators are stated against it.
"""

from __future__ import annotations

import math
import statistics


# Two-sided 95% Student-t critical values, indexed by degrees of freedom.
# Above df=30 the normal approximation is used; the error there is under 1%.
T95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
    6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
    26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
}


def critical_value(n: int) -> float:
    return T95.get(n - 1, 1.96)


def ci95(values: list[float]) -> dict:
    n = len(values)
    mean = statistics.fmean(values)
    if n < 2:
        return {"n": n, "mean": mean, "lower": None, "upper": None}
    critical = critical_value(n)
    half = critical * statistics.stdev(values) / math.sqrt(n)
    return {"n": n, "mean": mean, "lower": mean - half,
            "upper": mean + half}


def required_pairs(values: list[float], limit: float,
                   maximum: int = 200):
    """Smallest pair count at which the interval stops straddling `limit`.

    A criterion of the form "the 95% bound is on one side of `limit`" is
    decidable only once the half-width is smaller than the distance from the
    observed mean to `limit`. Below that, the verdict is determined by which
    seeds were drawn rather than by the controller, and reporting either a pass
    or a fail is misleading.

    This answers "how many pairs would this quantity need, at the dispersion it
    actually exhibits, before the gate could decide it" — in whichever direction
    the data already point. It is deliberately not a fixed constant: setting the
    pair count by analogy with another criterion is how a gate ends up spending
    ten runs on a check that arithmetic already showed could not decide.

    Returns (n, reason). `n` is None when no affordable sample decides, with
    `reason` explaining why.
    """
    n0 = len(values)
    if n0 < 2:
        return None, "need at least two pairs to estimate dispersion"
    deviation = statistics.stdev(values)
    mean = statistics.fmean(values)
    if not math.isfinite(deviation) or not math.isfinite(mean):
        return None, "non-finite differences"
    if deviation == 0.0:
        return 2, "zero dispersion; any pair count decides"
    margin = abs(limit - mean)
    if margin == 0.0:
        return None, "observed mean sits exactly on the limit"
    for n in range(2, maximum + 1):
        if critical_value(n) * deviation / math.sqrt(n) <= margin:
            return n, "half-width separates the mean from the limit"
    return None, (f"more than {maximum} pairs; the effect size is small "
                  f"relative to the seed-to-seed spread")


def envelope_verdict(values: list[float], limit: float, minimum_pairs: int,
                     two_sided: bool = False) -> dict:
    """Judge a paired envelope, or decline to judge it.

    two_sided: the quantity must stay inside [-limit, +limit] (parity checks,
    where drifting either way is a finding). Otherwise only the upper side is
    constrained (regression checks).
    """
    interval = ci95(values)
    needed, reason = required_pairs(values, limit)
    result = {
        "kind": "paired_envelope",
        "limit": limit,
        "two_sided": two_sided,
        "ci95": interval,
        "per_repeat": values,
        "required_pairs": needed,
        "required_pairs_reason": reason,
        "minimum_pairs": minimum_pairs,
    }
    threshold = max(minimum_pairs, needed) if needed is not None else None
    if threshold is None or interval["n"] < threshold:
        result["verdict"] = "insufficient_pairs"
        result["passed"] = None
        return result
    inside = interval["upper"] <= limit
    if two_sided:
        inside = inside and interval["lower"] >= -limit
    result["verdict"] = "passed" if inside else "failed"
    result["passed"] = inside
    return result
