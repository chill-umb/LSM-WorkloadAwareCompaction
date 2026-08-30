"""Distribution-free statistics shared by SLO selection and calibration."""

from __future__ import annotations

import math


TOLERANCE_CONFIDENCE = 0.95
TOLERANCE_COVERAGES = (0.99, 0.90)


def _binomial_pmf(n: int, i: int, probability: float) -> float:
    if probability >= 1.0:
        return 1.0 if i == n else 0.0
    return math.exp(
        math.lgamma(n + 1)
        - math.lgamma(i + 1)
        - math.lgamma(n - i + 1)
        + i * math.log(probability)
        + (n - i) * math.log1p(-probability)
    )


def order_statistic_confidence(n: int, rank: int, coverage: float) -> float:
    """Confidence that X_(rank) covers at least ``coverage`` of a distribution."""
    if not 1 <= rank <= n:
        return 0.0
    return min(1.0, sum(_binomial_pmf(n, i, coverage) for i in range(rank)))


def smallest_valid_rank(n: int, coverage: float, confidence: float):
    """Return the smallest order-statistic rank meeting ``confidence``."""
    total = 0.0
    for i in range(n):
        total += _binomial_pmf(n, i, coverage)
        if total >= confidence:
            return i + 1, min(total, 1.0)
    return None, min(total, 1.0)


def tolerance_bound(values: list[float]):
    """Return a distribution-free upper tolerance bound and its provenance."""
    invalid_count = sum(not math.isfinite(v) for v in values)
    ordered = sorted(v for v in values if math.isfinite(v))
    n = len(ordered)
    meta = {
        "sample_count": n,
        "invalid_sample_count": invalid_count,
        # Retain the old name for consumers of existing estimator metadata.
        "episode_count": n,
        "method": "insufficient_samples",
        "achieved_coverage": None,
        "achieved_confidence": None,
        "order_statistic_rank": None,
    }
    if invalid_count:
        meta["method"] = "invalid_non_finite_samples"
        return None, meta
    if n == 0:
        return None, meta
    for coverage in TOLERANCE_COVERAGES:
        rank, confidence = smallest_valid_rank(
            n, coverage, TOLERANCE_CONFIDENCE
        )
        if rank is None:
            continue
        meta.update(
            {
                "method": "order_statistic_tolerance_bound",
                "achieved_coverage": coverage,
                "achieved_confidence": confidence,
                "order_statistic_rank": rank,
            }
        )
        return ordered[rank - 1], meta
    return None, meta


def censored_tolerance_bound(completed: list[float], censored: list[float]):
    """Return a bound only when every episode used by it is complete.

    A truncated episode reports a lower bound on its eventual duration,
    integrated pressure, and maximum. Raising a completed-sample order
    statistic to the largest observed lower bound does not produce a valid
    distribution-free tolerance bound: the unknown tail beyond the censoring
    point can still be arbitrarily large. Without a preregistered censoring
    model, the conservative result is therefore uncalibrated.
    """
    bound, completed_meta = tolerance_bound(completed)
    finite_censored = [v for v in censored if math.isfinite(v)]
    invalid_censored = len(censored) - len(finite_censored)
    meta = dict(completed_meta)
    meta["completed_count"] = len(completed)
    meta["censored_count"] = len(finite_censored)
    meta["invalid_censored_count"] = invalid_censored
    meta["bound_raised_by_censored"] = False
    if invalid_censored:
        meta["method"] = "invalid_non_finite_censored_samples"
        meta["achieved_coverage"] = None
        meta["achieved_confidence"] = None
        meta["order_statistic_rank"] = None
        return None, meta
    if completed_meta["invalid_sample_count"]:
        # Preserve the more specific completed-sample parse failure instead of
        # relabelling it as ordinary right censoring below.
        return None, meta
    if finite_censored:
        meta["completed_sample_estimator"] = completed_meta
        meta["method"] = "right_censored_bound_unavailable"
        meta["achieved_coverage"] = None
        meta["achieved_confidence"] = None
        meta["order_statistic_rank"] = None
        return None, meta
    if bound is None:
        return None, meta
    return bound, meta
