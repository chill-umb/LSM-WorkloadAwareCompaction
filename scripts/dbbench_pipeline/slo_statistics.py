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
    """Upper tolerance bound that stays valid under right censoring.

    A truncated episode reports a lower bound on its eventual duration,
    integrated pressure, and maximum, so its true value is unknown. Ranking it
    beside completed episodes understates the tail, and raising the bound to
    the largest observed lower bound carries no distribution-free guarantee
    either, because the unseen tail past the censoring point can be arbitrarily
    large.

    Charging every censored episode to that tail does carry one. The worst case
    for an upper bound is that all of them exceed every completed observation;
    then the combined sample's r-th smallest value *is* the completed sample's
    r-th smallest, for any rank r <= len(completed). So the rank is chosen over
    the full sample size and read off the completed sample, and the guarantee
    holds however large the unseen tail turns out to be.

    Returning no bound at all is not the conservative alternative: callers fall
    back to hard-coded caps far tighter than any calibrated limit, which makes
    the safety guard fire more, not less.

    With no censoring at all this reduces exactly to ``tolerance_bound``. As
    censoring grows it walks the same TOLERANCE_COVERAGES ladder that a small
    sample does: once the 0.99 rank runs past the end of the completed sample
    it falls back to 0.90 coverage, and it returns no bound only when even the
    weakest rung is out of reach. Read ``achieved_coverage`` before comparing
    two limits -- a 0.90 bound sits materially lower than a 0.99 one, so a
    heavily censored level yields a *tighter* limit rather than a missing one.
    """
    _, completed_meta = tolerance_bound(completed)
    finite_censored = [v for v in censored if math.isfinite(v)]
    invalid_censored = len(censored) - len(finite_censored)
    meta = dict(completed_meta)
    meta["completed_count"] = len(completed)
    meta["censored_count"] = len(finite_censored)
    meta["invalid_censored_count"] = invalid_censored
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
    ordered = sorted(completed)
    if not ordered:
        return None, meta
    ranked_over = len(ordered) + len(finite_censored)
    for coverage in TOLERANCE_COVERAGES:
        rank, confidence = smallest_valid_rank(
            ranked_over, coverage, TOLERANCE_CONFIDENCE
        )
        if rank is None or rank > len(ordered):
            continue
        meta.update(
            {
                "method": "censored_order_statistic_tolerance_bound",
                "achieved_coverage": coverage,
                "achieved_confidence": confidence,
                "order_statistic_rank": rank,
                "ranked_over_count": ranked_over,
            }
        )
        return ordered[rank - 1], meta
    meta["method"] = "censoring_exceeds_tail_budget"
    meta["achieved_coverage"] = None
    meta["achieved_confidence"] = None
    meta["order_statistic_rank"] = None
    meta["ranked_over_count"] = ranked_over
    return None, meta
