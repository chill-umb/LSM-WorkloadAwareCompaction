"""Distribution-free statistics shared by SLO selection and calibration."""

from __future__ import annotations

import math


TOLERANCE_CONFIDENCE = 0.95
TOLERANCE_COVERAGES = (0.99, 0.90)
# Pathway E-1: the guard holdout passes when at most this fraction of actuation
# frames would have been overridden. The due-age and pressure limits are
# calibrated against this same statistic (frame_simulated_limits) so that the
# manifest and the gate speak the same unit.
GUARD_TARGET_OVERRIDE_FRACTION = 0.01


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


def frame_simulated_limits(runs: list[list[dict]], levels: int,
                           interval_micros: int, target_fraction: float):
    """Per-level due-age and pressure limits calibrated on the frame fraction.

    The force condition in the picker compares a level's *current* due age
    and integrated pressure against its limit on every observation frame. A
    tolerance bound over completed episode durations answers a different
    question: frames sample due time, not episodes, so a rare long episode
    covers hundreds of consecutive frames while a short one covers one or
    none. Limits set at the episode p99 therefore fire on far more than 1% of
    frames (the inspection paradox), and that is the statistic E-1 scores.

    This replays every baseline run at the controller's cadence. For each
    frame and each level with an open episode, the due age is exact from the
    episode start. Pressure is the episode's integrated excess score scaled
    by elapsed fraction -- exact under a constant score, a first-order model
    otherwise, since the trajectory inside an episode is not logged. Each
    level's limit is the frame quantile at a common per-level exceedance k/N,
    and k is the largest count at which the fraction of frames where ANY level
    exceeds either limit is still at or below target_fraction. Levels with no
    due frame keep limit 0 and are left to the caller's floors.

    `runs` holds one episode list per baseline run; frames are laid on the
    span from that run's first episode start to its last episode end, which
    under-counts the run's actuation frames slightly and so over-estimates
    the fraction. Truncated episodes contribute their observed span.
    """
    ages: list[list[int]] = [[] for _ in range(levels)]
    pressures: list[list[float]] = [[] for _ in range(levels)]
    frame_count = 0
    for run in runs:
        if not run:
            continue
        t0 = min(e["start_micros"] for e in run)
        t1 = max(e["end_micros"] for e in run)
        n = int((t1 - t0) // interval_micros) + 1
        base = frame_count
        for level in range(levels):
            ages[level].extend([-1] * n)
            pressures[level].extend([-1.0] * n)
        for e in run:
            level = e["level"]
            duration = e["duration_micros"]
            if level >= levels or duration <= 0:
                continue
            start = e["start_micros"]
            total = e["integrated_excess_score_micros"]
            k = -(-(start - t0) // interval_micros)
            while k < n:
                age = t0 + k * interval_micros - start
                if age > duration:
                    break
                ages[level][base + k] = age
                pressures[level][base + k] = total * age / duration
                k += 1
        frame_count += n
    meta = {
        "method": "frame_simulated_quantile",
        "frame_interval_micros": interval_micros,
        "frame_count": frame_count,
        "run_count": sum(1 for run in runs if run),
        "target_override_fraction": target_fraction,
        "exceedance_per_level": None,
        "predicted_override_fraction": None,
    }
    if frame_count == 0:
        return None, meta
    sorted_ages = [sorted(a) for a in ages]
    sorted_pressures = [sorted(p) for p in pressures]

    def limits_at(k: int):
        return [(max(0, sorted_ages[level][frame_count - k]),
                 max(0.0, sorted_pressures[level][frame_count - k]))
                for level in range(levels)]

    def joint_fraction(limits) -> float:
        hits = 0
        for i in range(frame_count):
            for level in range(levels):
                age = ages[level][i]
                if age < 0:
                    continue
                if age >= limits[level][0] or pressures[level][i] >= limits[level][1]:
                    hits += 1
                    break
        return hits / frame_count

    # Larger k lowers every limit and can only raise the joint fraction, so
    # bisect for the largest k that still meets the target.
    lo, hi = 0, int(target_fraction * frame_count)
    best = None
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if joint_fraction(limits_at(mid)) <= target_fraction:
            lo = mid
            best = mid
        else:
            hi = mid - 1
    if best is None:
        # Even one permitted exceedance per level overshoots: return the
        # strictest limits (above every observed frame) and let the caller
        # decide whether a zero-exceedance manifest is acceptable.
        limits = [(max(0, sorted_ages[level][-1]) + 1,
                   max(0.0, sorted_pressures[level][-1]) + 1.0)
                  for level in range(levels)]
        meta["exceedance_per_level"] = 0.0
    else:
        limits = limits_at(best)
        meta["exceedance_per_level"] = best / frame_count
    meta["predicted_override_fraction"] = joint_fraction(limits)
    meta["due_frames_per_level"] = [
        sum(1 for a in ages[level] if a >= 0) for level in range(levels)]
    return limits, meta
