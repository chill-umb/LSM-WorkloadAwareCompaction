"""Distribution-free statistics shared by SLO selection and calibration."""

from __future__ import annotations

import bisect
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
                           interval_micros: int, target_fraction: float,
                           floors: tuple[int, float, float] = (1, 1.0, 1.0)):
    """Per-level due-age and pressure limits calibrated on the frame fraction.

    The force condition in the picker compares a level's *current* due age
    and integrated pressure against its limit on every observation frame. A
    tolerance bound over completed episode durations answers a different
    question: frames sample due time, not episodes, so a rare long episode
    covers hundreds of consecutive frames while a short one covers one or
    none. Limits set at the episode p99 therefore fire on far more than 1% of
    frames (the inspection paradox), and that is the statistic E-1 scores.

    This replays every baseline run at the controller's cadence and calibrates
    all three per-level terms of that condition together, because a frame
    overrides if ANY of them trips: calibrating a subset only moves the firing
    onto the terms left out.

    For each frame and each level with an open episode, the due age is exact
    from the episode start. The score trajectory inside an episode is not
    logged and is modelled as a linear ramp from 1 to the episode's observed
    ``max_score``, with pressure the integral of that ramp,
    ``total * (age / duration) ** 2``, which reaches exactly the measured
    ``integrated_excess_score_micros`` at the episode's end.

    The ramp is measured, not assumed. For a linear ramp the peak excess is
    twice the mean, so ``(max_score - 1) / (integrated_excess / duration)``
    should be 2. Over 429,115 baseline episodes that ratio, weighted by the
    frames each episode covers, is 1.93 to 2.50 on every level of the tree.
    Short episodes read 1.0 because peak and mean coincide within one
    observation; the long episodes that carry the frames ramp.

    Two alternative models are reported but never used to choose limits:
    ``..._score_flat`` holds each episode at its mean excess, a lower bound,
    and ``..._score_upper`` holds it at ``max_score`` throughout, an upper
    bound. They bracket the ramp and their spread is the residual modelling
    risk in the prediction.

    Each level's limits are the frame quantiles at a common per-level
    exceedance k/N, and k is the largest count at which the fraction of frames
    where any level trips any term is still at or below target_fraction. Levels
    with no due frame keep limit 0 and are left to the caller's floors.

    Not modelled, because no per-frame record exists: ``global_debt_breach``,
    ``slo_force_due`` and ``l0_slowdown``. All three are global rather than
    per-level, so no per-level limit can offset them. Since shadow schema 3
    they are logged per frame on every guarded run, so the holdout report
    attributes them; the baseline sweep that feeds this replay still carries
    no per-frame record of them.

    The replay assumes the guard is memoryless: a frame overrides iff a term
    holds on that frame. That is the mechanism since PREREGISTRATION D-2
    (2026-09-20). Before it, a budget force was retained until the level was
    healthy, which this replay never modelled and which alone carried about
    half of E-1's measured rate.

    ``predicted_override_fraction`` is in-sample. Every limit here is a top
    order statistic of the training runs, so it describes those runs better
    than the next seed; ``predicted_override_fraction_leave_one_out`` refits
    k on all runs but one and scores the one left out, per run, and its mean
    is the number to compare with a holdout.

    `runs` holds one episode list per baseline run; frames are laid on the
    span from that run's first episode start to its last episode end, which
    under-counts the run's actuation frames slightly and so over-estimates
    the fraction. Truncated episodes contribute their observed span.

    The returned limits are the ones to export verbatim: ``floors`` (due age,
    pressure, score) are applied *inside* the search, so ``k`` is chosen
    against, and ``predicted_override_fraction`` describes, exactly the values
    the manifest will carry. Applying them afterwards in the caller would
    leave the prediction describing limits nothing enforces.

    No safety margin is applied, deliberately, unlike the tolerance bounds
    elsewhere in this module. ``target_fraction`` already specifies how often
    the guard may fire, and a margin on top is not a cushion: loosening every
    limit at a fixed ``k`` lowers the joint fraction, which lets the search
    accept a larger ``k``, which tightens the raw quantile until the target
    binds again. Measured, a 1.02 margin produced limits 0.67-0.85x the
    unmargined ones. Two knobs, one quantity; the target is the preregistered
    one, so it is the one that survives.

    One known difference from the holdout: it scores only frames after
    ``guard_ready``, while this replay scores every frame. The warm-up window
    is the more forced one, so the prediction errs high.
    """
    ages: list[list[int]] = [[] for _ in range(levels)]
    pressures: list[list[float]] = [[] for _ in range(levels)]
    scores: list[list[float]] = [[] for _ in range(levels)]
    peaks: list[list[float]] = [[] for _ in range(levels)]
    flats: list[list[float]] = [[] for _ in range(levels)]
    # (offset, length) of each run inside the concatenated frame arrays. A due
    # run is only meaningful within one run, and its severity is relative to
    # that run's own length, not to the pooled total.
    spans: list[tuple[int, int]] = []
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
            scores[level].extend([-1.0] * n)
            peaks[level].extend([-1.0] * n)
            flats[level].extend([-1.0] * n)
        for e in run:
            level = e["level"]
            duration = e["duration_micros"]
            if level >= levels or duration <= 0:
                continue
            start = e["start_micros"]
            total = e["integrated_excess_score_micros"]
            flat_score = 1.0 + total / duration
            peak_score = e["max_score"]
            k = -(-(start - t0) // interval_micros)
            while k < n:
                age = t0 + k * interval_micros - start
                if age > duration:
                    break
                elapsed = age / duration
                ages[level][base + k] = age
                # Integral of the ramp, hitting `total` exactly at age=duration.
                pressures[level][base + k] = total * elapsed * elapsed
                scores[level][base + k] = 1.0 + (peak_score - 1.0) * elapsed
                peaks[level][base + k] = peak_score
                flats[level][base + k] = flat_score
                k += 1
        spans.append((base, n))
        frame_count += n
    meta = {
        "method": "frame_simulated_quantile",
        "frame_interval_micros": interval_micros,
        "frame_count": frame_count,
        "run_count": sum(1 for run in runs if run),
        "target_override_fraction": target_fraction,
        "exceedance_per_level": None,
        "predicted_override_fraction": None,
        "predicted_override_fraction_leave_one_out": [],
        "predicted_override_fraction_leave_one_out_mean": None,
        "predicted_override_fraction_score_upper": None,
        "predicted_override_fraction_score_flat": None,
        "score_model": "linear_ramp_to_observed_max_score",
    }
    if frame_count == 0:
        return None, meta
    def sorted_sets(ranges):
        idx = [i for base, n in ranges for i in range(base, base + n)]
        return ([sorted(ages[l][i] for i in idx) for l in range(levels)],
                [sorted(pressures[l][i] for i in idx) for l in range(levels)],
                [sorted(scores[l][i] for i in idx) for l in range(levels)])

    def limit_for_count(values: list[float], k: int, floor: float):
        """Smallest limit v with count(x >= v) <= k, else one above the max.

        The picker compares with >=, and score is constant inside an episode,
        so the k-th largest value is shared by every frame of that episode.
        Indexing by rank alone would therefore admit a whole episode when it
        meant to admit k frames. Walk up to the next distinct value until the
        tail count actually fits.
        """
        n = len(values)
        above_max = max(floor, values[-1]) + 1.0
        if k <= 0 or n == 0:
            return above_max
        v = values[max(0, n - k)]
        while True:
            if n - bisect.bisect_left(values, v) <= k:
                return max(floor, v)
            j = bisect.bisect_right(values, v)
            if j >= n:
                return above_max
            v = values[j]

    age_floor, pressure_floor, score_floor = floors

    def limits_at(k: int, sets):
        """Exported limits at exceedance k: margin and floors already applied."""
        sorted_ages, sorted_pressures, sorted_scores = sets
        out = []
        for level in range(levels):
            age = limit_for_count(sorted_ages[level], k, 0.0)
            pressure = limit_for_count(sorted_pressures[level], k, 0.0)
            score = limit_for_count(sorted_scores[level], k, 1.0)
            out.append((max(age_floor, round(age)),
                        max(pressure_floor, pressure),
                        max(score_floor, score)))
        return out

    def joint_fraction(limits, ranges, score_source=scores) -> float:
        hits = 0
        total = 0
        for base, n in ranges:
            total += n
            for i in range(base, base + n):
                for level in range(levels):
                    age = ages[level][i]
                    if age < 0:
                        continue
                    age_limit, pressure_limit, score_limit = limits[level]
                    if (age >= age_limit
                            or pressures[level][i] >= pressure_limit
                            or score_source[level][i] >= score_limit):
                        hits += 1
                        break
        return hits / total if total else 0.0

    def search(ranges):
        """Largest k whose joint fraction over `ranges` meets the target.

        Larger k lowers every limit and can only raise the joint fraction, so
        bisect. Returns (limits, k); k is None when no per-level exceedance
        fits the target, i.e. every limit lands above its own maximum
        because a single sustained event covers more than target_fraction of
        the run and no per-frame threshold can both tolerate that event and
        fire inside it.
        """
        sets = sorted_sets(ranges)
        lo, hi = 0, int(target_fraction * sum(n for _, n in ranges))
        best = None
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if joint_fraction(limits_at(mid, sets), ranges) <= target_fraction:
                lo = mid
                best = mid
            else:
                hi = mid - 1
        return limits_at(best or 0, sets), best

    limits, best = search(spans)
    meta["exceedance_per_level"] = (
        0.0 if best is None else best / frame_count)
    meta["predicted_override_fraction"] = joint_fraction(limits, spans)
    loo = []
    if len(spans) >= 2:
        for i, held_out in enumerate(spans):
            train = spans[:i] + spans[i + 1:]
            fold_limits, _ = search(train)
            loo.append(joint_fraction(fold_limits, [held_out]))
    meta["predicted_override_fraction_leave_one_out"] = loo
    meta["predicted_override_fraction_leave_one_out_mean"] = (
        sum(loo) / len(loo) if loo else None)
    # Measure inertness from the outcome, not from the search path: a floor can
    # lift a limit above everything observed while the search still reports a
    # feasible k. A guard that would never fire on baseline-like behaviour
    # satisfies the target vacuously and is not a calibration success;
    # longest_due_run_fraction names the event driving it.
    meta["degenerate_inert_guard"] = (
        meta["predicted_override_fraction"] == 0.0
        and any(a >= 0 for level in range(levels) for a in ages[level]))
    meta["predicted_override_fraction_score_upper"] = joint_fraction(
        limits, spans, peaks)
    meta["predicted_override_fraction_score_flat"] = joint_fraction(
        limits, spans, flats)
    meta["due_frames_per_level"] = [
        sum(1 for a in ages[level] if a >= 0) for level in range(levels)]
    # The longest unbroken stretch of frames in which one level stays due. A
    # per-frame criterion counts such a stretch as that many violations, so
    # when it exceeds target_fraction * frame_count no limit can pass without
    # disabling the guard, however the limits are estimated.
    longest = 0
    longest_fraction = 0.0
    for level in range(levels):
        for base, n in spans:
            current = 0
            for i in range(base, base + n):
                current = current + 1 if ages[level][i] >= 0 else 0
                if current > longest:
                    longest = current
                longest_fraction = max(longest_fraction, current / n)
    meta["longest_due_run_frames"] = longest
    # Fraction of the run that contains it, not of the pooled frame count:
    # five runs each carrying one long backlog would otherwise each look five
    # times milder than they are.
    meta["longest_due_run_fraction"] = longest_fraction
    return limits, meta
