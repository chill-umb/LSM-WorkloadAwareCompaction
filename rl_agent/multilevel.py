"""
Multi-level RL compaction support (protocol v2).

One DQN agent per LSM level. Each level's agent observes its own level's
state, the next level's state (zeros for the last level), and global pressure
signals — per the per-level architecture: a compaction from level i lands in
level i+1, so the trigger decision needs visibility into the destination's
fullness and key-range overlap.

Protocol v2 (C++ -> Python), newline-delimited JSON:
  {"version": 2, "credit_assignment_version": 2, "decision_id": N,
   <globals...>, "levels": [{"level": 0, ...}, ...]}
Response (Python -> C++):
  {"decision_id": N, "actions": [a_0, a_1, ...]}
"""

from __future__ import annotations

import math
import os
import threading
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

import config
from agent import DQNAgent, SharedTrunk
from lagrange import MULTIPLIERS, COMPONENT_COUNT, scalar_components


CREDIT_ASSIGNMENT_VERSION = 2
UINT64_MAX = (1 << 64) - 1
REWARD_INVALID_REASON_NAMES = {
    0: "socket_or_query_fallback",
    1: "watchdog_native_fallback",
    2: "malformed_protocol",
    3: "rejected_manifest",
    4: "unknown_control_ownership",
}


def _exact_uint64(value, name: str, *, nonzero: bool) -> int:
    if type(value) is not int or value < (1 if nonzero else 0) \
            or value > UINT64_MAX:
        qualifier = "nonzero " if nonzero else ""
        raise ValueError(f"{name} must be an exact {qualifier}uint64")
    return value


def validate_protocol_message(msg: dict) -> tuple[int, int]:
    """Reject old/malformed credit semantics before any reward is consumed."""
    if type(msg) is not dict:
        raise ValueError("multi-level request must be a JSON object")
    if type(msg.get("version")) is not int or msg["version"] != 2:
        raise ValueError("trigger protocol version 2 is required")
    if (type(msg.get("credit_assignment_version")) is not int
            or msg["credit_assignment_version"] != CREDIT_ASSIGNMENT_VERSION):
        raise ValueError("credit_assignment_version 2 is required")
    decision_id = _exact_uint64(
        msg.get("decision_id"), "decision_id", nonzero=True)
    invalid_mask = _exact_uint64(
        msg.get("prev_reward_invalid_reason_mask"),
        "prev_reward_invalid_reason_mask", nonzero=False)
    levels = msg.get("levels")
    if type(levels) is not list or not levels:
        raise ValueError("levels must be a non-empty JSON array")
    seen_levels = set()
    for index, entry in enumerate(levels):
        if type(entry) is not dict:
            raise ValueError(f"levels[{index}] must be a JSON object")
        level = entry.get("level")
        if type(level) is not int or level < 0 or level in seen_levels:
            raise ValueError(f"levels[{index}].level is invalid or duplicated")
        seen_levels.add(level)
        _exact_uint64(entry.get("prev_decision_id"),
                      f"levels[{index}].prev_decision_id", nonzero=False)
        executed = entry.get("prev_action_executed")
        if type(executed) is not int or executed not in (0, 1):
            raise ValueError(
                f"levels[{index}].prev_action_executed must be 0 or 1")
        if type(entry.get("prev_action_overridden")) is not bool:
            raise ValueError(
                f"levels[{index}].prev_action_overridden must be Boolean")
    return decision_id, invalid_mask


GLOBAL_DEFAULTS = {
    "pending_compaction_bytes": 0.0,
    "flushed_bytes": 0.0,
    "compaction_bytes_read": 0.0,
    "compaction_bytes_written": 0.0,
    "compactions_completed": 0.0,
    "stall_count": 0.0,
    "stop_count": 0.0,
    "l0_compaction_trigger": 4.0,
    "l0_slowdown_trigger": 20.0,
    "l0_stop_trigger": 36.0,
    "l0_delay_trigger_count": 0.0,
    # Wall-clock span the deltas above cover. Without it every rate-like field
    # is a total over an unknown window.
    "interval_micros": 0.0,
    # Read-path telemetry (deltas over interval_micros).
    "keys_read": 0.0,
    "seeks": 0.0,
    "get_hit_l0": 0.0,
    "get_hit_l1": 0.0,
    "get_hit_l2_and_up": 0.0,
    "bloom_useful": 0.0,
    "non_last_level_read_count": 0.0,
    "last_level_read_count": 0.0,
    "user_logical_write_bytes": 0.0,
    "point_sst_probes": 0.0,
    "scan_returned_entries": 0.0,
    "scan_internal_skipped": 0.0,
    "scan_sorted_run_seeks": 0.0,
    "physical_sst_bytes": 0.0,
    "live_logical_bytes": 0.0,
    "output_only_level_files": 0.0,
    "output_only_level_bytes": 0.0,
    "output_only_level_target_bytes": 0.0,
    "stall_duration_micros": 0.0,
    "get_latency_count": 0.0,
    "get_latency_avg_ns": 0.0,
    "get_latency_p95_ns": 0.0,
    "scan_latency_count": 0.0,
    "scan_latency_avg_ns": 0.0,
    "scan_latency_p95_ns": 0.0,
    "write_latency_count": 0.0,
    "write_latency_avg_ns": 0.0,
    "write_latency_p95_ns": 0.0,
    # p99 is the formal latency constraint (P0-4); p95 remains for the guard.
    "get_latency_p99_ns": 0.0,
    "scan_latency_p99_ns": 0.0,
    "write_latency_p99_ns": 0.0,
    # Observation timing and generations. These are kept distinct on purpose:
    # `observation_micros` stamps when the overlay was built, the snapshot age
    # may legitimately be large on an idle tree, and only `dirty_age` bounds a
    # known-unpublished structural change. Collapsing them would hide a stale
    # view behind a healthy-looking number.
    "observation_micros": 0.0,
    "structural_snapshot_age_micros": 0.0,
    "structural_dirty_age_micros": 0.0,
    "structural_source_generation": 0.0,
    "structural_built_generation": 0.0,
    "score_event_generation": 0.0,
    "prev_reward_invalid_reason_mask": 0,
    "done": 0.0,
}

LEVEL_DEFAULTS = {
    "level": 0.0,
    "files": 0.0,
    "bytes": 0.0,
    "score": 0.0,
    "target_bytes": 0.0,
    "next_level_files": 0.0,
    "next_level_bytes": 0.0,
    "next_level_score": 0.0,
    "next_level_target_bytes": 0.0,
    "overlap_bytes": 0.0,
    "bytes_in": 0.0,
    "bytes_read_out": 0.0,
    "bytes_written_out": 0.0,
    "compactions_from": 0.0,
    "compactions_scheduled": 0.0,
    # Compactions this level's agent actually asked for, as opposed to ones the
    # parent leveled picker chose on its own.
    "compactions_forced": 0.0,
    # Outcome of the previous decision, reported by the picker. The chosen and
    # executed actions differ whenever a safety guard or native admission
    # outcome changes what reached the plant, and training must key on that.
    "prev_action_executed": 0.0,
    "prev_action_overridden": 0.0,
    "prev_compaction_picked": 0.0,
    "prev_decision_id": 0,
    "prev_snapshot_epoch": 0,
    "prev_scheduling_result": 0,
    "prev_completion_result": 0,
    "prev_completed_decision_id": 0,
    "prev_completed_decision_generation": 0,
    "prev_completed_eligibility_generation": 0,
    "prev_completed_override_reason": 0,
    "prev_override_reason": 0,
    "defer_count": 0.0,
    "default_needed": 0.0,
    "is_last": 0.0,
    "due_age_micros": 0.0,
    "pressure_score_micros": 0.0,
    "gate_open": 0.0,
    "gate_mode": 0.0,
    "jobs_attempted": 0.0,
    "jobs_blocked": 0.0,
    "jobs_scheduled": 0.0,
    "jobs_completed": 0.0,
    # How long this eligibility interval waited before the plant admitted
    # anything. A gate that opens and is never served looks identical to a
    # closed gate in the aggregate counters; this separates them.
    "decision_to_first_schedule_micros": 0.0,
    # Trivial moves drain a level at near-zero write amplification, so a
    # decision serviced by moves is not comparable to one serviced by
    # rewrites.
    "trivial_move_jobs": 0.0,
    "trivial_move_bytes": 0.0,
    "consecutive_blocked": 0.0,
    "in_backoff": 0.0,
}

# Seconds-since-compaction saturates here. Wall-clock, not a frame count: a
# compaction takes seconds and the frame cadence is 50 ms, so a 50-frame scale
# saturated 2.5 s in and could not distinguish an idle level from a busy one.
_SECONDS_SINCE_SCALE = 30.0
# L0 run count at which extra files stop adding meaningful probe cost. Small,
# because the first few overlapping runs are what hurt: going 1 -> 3 files
# triples L0's probe cost, while 20 -> 22 barely changes it. Absolute runs.
_PROBE_SATURATION = 4.0
# A score below this at decision time marks a triggered compaction as
# "unnecessary" when it produced no relief (legacy reward only).
_UNNECESSARY_SCORE_THRESHOLD = 0.75


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _positive(value: float) -> float:
    return max(0.0, value)


def _as_float(value) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class _AdaptiveScales:
    """Generic decaying-max normalizer with caller-chosen keys, so per-level
    scales don't collide.

    Scales freeze after `config.NORM_FREEZE_SECONDS` of controlled wall
    time. A scale that keeps moving means the same raw observation encodes to
    a different feature vector over time, so replayed transitions were
    recorded against an encoding that no longer exists.
    """

    def __init__(self, decay: float = config.NORMALIZER_DECAY,
                 freeze_seconds: float = None):
        self.decay = decay
        self.freeze_seconds = (config.NORM_FREEZE_SECONDS
                               if freeze_seconds is None else freeze_seconds)
        self.scales: Dict[str, float] = {}
        self.observed_seconds = 0.0
        self.frozen = False

    def tick(self, dt: float) -> None:
        """Advance the controlled-time clock by one telemetry window; freezes
        scales once past the warmup. Called once per message, not per key."""
        if self.frozen:
            return
        self.observed_seconds += max(0.0, dt)
        if self.freeze_seconds and self.observed_seconds >= self.freeze_seconds:
            self.frozen = True

    def observe(self, key: str, value: float) -> None:
        if self.frozen:
            return
        value = _positive(value)
        previous = self.scales.get(key, 1.0)
        self.scales[key] = max(1.0, value, previous * self.decay)

    def normalize(self, key: str, value: float) -> float:
        return _clamp(value / max(1.0, self.scales.get(key, 1.0)))


def read_exposure(level: int, g: dict) -> float:
    """Share of read operations that traverse `level`, in [0, 1].

    A point lookup descends until it finds the key, so L0 is probed by every
    lookup, L1 only by those that missed L0, and so on — the per-level hit
    counters give exactly that. Range scans open a merging iterator across all
    levels, so every seek is exposed to every level.

    This is what tells the prior whether compacting a level is worth anything.
    Removing a sorted run from a level nobody reads buys nothing; removing one
    from a level on the hot lookup path buys a probe on every read.
    """
    # Tolerate a partial globals dict: a message that carries no read telemetry
    # is indistinguishable from one with no read traffic, and both should mean
    # "no read-amplification benefit to claim".
    gets = _positive(g.get("keys_read", 0.0))
    seeks = _positive(g.get("seeks", 0.0))
    total = gets + seeks
    if total <= 0.0:
        # No read traffic at all (a write-only phase): compaction cannot be
        # justified by read amplification here.
        return 0.0
    hit_l0 = _positive(g.get("get_hit_l0", 0.0))
    hit_l1 = _positive(g.get("get_hit_l1", 0.0))
    if level <= 0:
        reaching = gets
    elif level == 1:
        reaching = _positive(gets - hit_l0)
    else:
        reaching = _positive(gets - hit_l0 - hit_l1)
    return _clamp((reaching + seeks) / total)


def analytic_advantage(raw: dict, g: dict) -> Tuple[float, dict]:
    """A_analytic(s): the physics-informed advantage of compact_now over
    do_nothing for one level, from raw observables. Each term maps to a named
    piece of LSM cost theory:

      stall_urgency     queueing: projected proximity to the write-slowdown
                        threshold given current inflow (L0). For a deeper
                        level it is the due indicator, score >= 1 -- the
                        predicate the native picker and the guard use
                        (PREREGISTRATION D-4).
      readamp_relief    probe count, in ABSOLUTE sorted runs: compacting L0
                        removes its files from every lookup's probe path, net
                        of the run native RocksDB would remove one flush
                        later and of the run the output creates if L1 was
                        empty. A deeper level is one run whatever its size
                        (A4), so it carries no read term at all.
      work_now          merge I/O: (bytes + next-level overlap) normalized by
                        the two levels' capacities.
      premature_penalty Bentley-Saxe amortization: overlap is re-paid per
                        compaction, so merging an underfull level with large
                        overlap wastes I/O vs waiting for it to fill
                        (overlap/bytes scaled by emptiness).

    Returns (advantage, term_breakdown). Weights are the tunable "physics
    constants" (RL_PRIOR_W_*); the learned residual corrects what they miss.
    """
    level = int(raw["level"])
    bytes_i = max(raw["bytes"], 1.0)
    overlap = raw["overlap_bytes"]
    trigger = max(g["l0_compaction_trigger"], 1.0)
    slowdown = max(g["l0_slowdown_trigger"], 1.0)

    # Capacity units. A flush file is measured, not assumed: the pipeline runs
    # a 2 MiB write buffer against a 16 MiB L1 target, so reading L1's target
    # as the flush size (the previous form) made L0's capacity 8x too large,
    # under-priced work_now ~3x and under-counted inflow 8x at exactly the
    # cell where the prior over-compacted single-file L0 (history 14.10).
    # The next level is empty when its file count is zero; compacting into it
    # populates a new level.
    creates_level = (not raw["is_last"]) and raw["next_level_files"] <= 0.0
    if level == 0:
        flush_size = max(bytes_i / max(raw["files"], 1.0), 1.0)
        cap_i = trigger * flush_size
        fullness = _clamp(raw["files"] / trigger)
        inflow_files = raw["bytes_in"] / flush_size
        stall_urgency = _clamp((raw["files"] + inflow_files) / slowdown)
        # Each L0 file is an extra sorted run on every lookup's probe path, so
        # relief scales with the ABSOLUTE run count, never with fullness: at
        # trigger=10, two L0 files is 20% "full" but doubles L0's probe cost.
        # Net of what the action really changes: the output run itself when
        # L1 was empty, and -- below the trigger -- the runs native RocksDB
        # would remove one flush later anyway. A proactive compaction that
        # cannot clear PRIOR_MIN_RUN_REDUCTION runs beyond that earns nothing.
        net_runs = raw["files"] - (1.0 if creates_level else 0.0)
        if raw["files"] < trigger:
            net_runs = _positive(net_runs - (config.PRIOR_MIN_RUN_REDUCTION - 1.0))
        runs_removed = _clamp(net_runs / _PROBE_SATURATION)
    else:
        cap_i = max(raw["target_bytes"], 1.0)
        fullness = _clamp(bytes_i / cap_i)
        # D-4 (2026-09-22): a level below L0 stalls nothing until RocksDB
        # scores it due. Below score 1 there is no stall mechanism at all --
        # the only deep-level stall path is pending bytes against a 64 GiB
        # soft limit, on a ~3 GB tree -- so the previous fullness**2 term was
        # a "due soon" signal, i.e. eagerness, and it authorised deep
        # compactions at 0.82-0.92 of target where native waits for 1.0.
        # A-0 measured the prior's entire write excess as exactly that.
        # Urgency is now the same predicate the native picker and the guard
        # use, so a deep level compacts if and only if RocksDB would.
        stall_urgency = 1.0 if raw["score"] >= 1.0 else 0.0
        # No read-side term. A deeper level is one sorted run whatever its
        # size (A4), so compacting it removes no probe. The P1c-23 depth
        # charge for output into an empty level is dropped: that output is
        # a trivial move, free on W; Corollary A.3 says holding a level back
        # to avoid depth is never write-profitable; and it never outweighed
        # the urgency term in any state (audit 2026-09-22).
        runs_removed = 0.0

    # Relief is only worth what the reads that traverse this level are worth.
    # Without this the prior valued compaction identically on a write-only and
    # a read-heavy workload, and in the low-pressure regime where it actually
    # operates the merge-cost term dominated, so it deferred by default.
    exposure = read_exposure(level, g)
    readamp_relief = runs_removed * exposure

    cap_next = max(raw["next_level_target_bytes"], cap_i)
    if level == 0 or not config.PRIOR_MARGINAL_WORK:
        # An L0 compaction merges every L0 run at once — they overlap, so the
        # whole level is the unit of work, measured against the two levels'
        # capacities.
        work_now = _clamp((bytes_i + overlap) / (cap_i + cap_next))
    else:
        # A deep-level compaction moves ONE file plus the next-level files it
        # overlaps (PickCompactionFromLevel, not the whole level), so the cost
        # of the action does not grow just because the level is fuller: a
        # fuller level holds proportionally more files of the same size.
        #
        # The right dimensionless cost is therefore the compaction's write
        # amplification — bytes rewritten per byte of progress, 1 + overlap
        # ratio — against the worst case set by the size ratio between the two
        # levels. Both numerator and denominator are per-compaction, so the
        # term keeps its magnitude instead of collapsing.
        #
        # Pricing the whole level instead made compaction look progressively
        # more expensive exactly as the level got more overfull: with realistic
        # file counts the advantage of compacting L1 FELL from +0.82 at target
        # to +0.58 at 3x, so the prior discouraged fixing the level that most
        # needed it. Measured on the 5M run L1 sits at 1.50x target (p50) and
        # is over target 63% of the time — parked in exactly the region that
        # error creates, and the source of 72% of the excess probe cost.
        size_ratio = max(cap_next / max(cap_i, 1.0), 2.0)
        work_now = _clamp((1.0 + overlap / bytes_i) / size_ratio)
    premature_penalty = _clamp(overlap / bytes_i / 4.0) * (1.0 - fullness)

    w_read = config.PRIOR_W_READ_L0 if level == 0 else config.PRIOR_W_READ
    adv = (
        config.PRIOR_W_STALL * stall_urgency
        + w_read * readamp_relief
        - config.PRIOR_W_WORK * work_now
        - config.PRIOR_W_PREMATURE * premature_penalty
    )
    adv = _clamp(adv, -config.PRIOR_CLAMP, config.PRIOR_CLAMP)
    terms = {
        "prior_stall_urgency": stall_urgency,
        "prior_readamp_relief": readamp_relief,
        "prior_read_exposure": exposure,
        "prior_runs_removed": runs_removed,
        "prior_work_now": work_now,
        "prior_premature_penalty": premature_penalty,
        "analytic_advantage": adv,
    }
    return adv, terms


class LevelDecision:
    """One level's processed slice of a v2 message."""

    __slots__ = ("level", "raw", "state", "reward", "components",
                 "valid_actions", "prior", "dt_seconds", "executed_action",
                 "prev_chosen_action", "dt_discount", "decision_id",
                 "prev_decision_id", "previous_overridden",
                 "reward_invalid_reason_mask", "reward_vector")

    def __init__(self, level: int, raw: dict, state: np.ndarray,
                 reward: float, components: dict, valid_actions,
                 prior: Optional[np.ndarray], dt_seconds: float,
                 executed_action: Optional[int],
                 prev_chosen_action: Optional[int] = None,
                 dt_discount: float = 0.0, decision_id: int = 0,
                 prev_decision_id: int = 0,
                 previous_overridden: bool = False,
                 reward_invalid_reason_mask: int = 0,
                 reward_vector: Optional[np.ndarray] = None):
        self.level = level
        self.raw = raw
        self.state = state
        # The scalar as priced at frame time -- for logs and diagnostics. The
        # agent trains on `reward_vector`, re-priced when the sample is drawn
        # (D-9, lagrange.py).
        self.reward = reward
        self.reward_vector = (reward_vector if reward_vector is not None
                              else scalar_components(reward))
        self.components = components
        # Action mask: compact_now is withheld from a level with nothing worth
        # compacting (no files, or score below the configured floor). A fresh
        # exploring agent would otherwise force pointless deep compactions,
        # whose I/O dominates early-run cost.
        self.valid_actions = valid_actions
        # Per-action analytic bias b(s,.) — None when the prior is disabled.
        self.prior = prior
        # Span of the telemetry window (global, from interval_micros). Used to
        # turn counter deltas into rates.
        self.dt_seconds = dt_seconds
        # Wall-clock seconds since THIS level was last observed, for SMDP
        # discounting. A level that holds no files is omitted from the message
        # entirely, so a level can vanish for many decisions and reappear; its
        # transition then spans the whole gap, not one telemetry window.
        self.dt_discount = dt_discount
        # What RocksDB actually did with the previous decision (None on the
        # first observation for this level), and what the agent had chosen
        # then. These two are the time-aligned pair: comparing the executed
        # outcome against the action chosen in *this* message would be off by
        # one decision and would misreport the override rate.
        self.executed_action = executed_action
        self.prev_chosen_action = prev_chosen_action
        self.decision_id = decision_id
        self.prev_decision_id = prev_decision_id
        self.previous_overridden = previous_overridden
        self.reward_invalid_reason_mask = reward_invalid_reason_mask


class MultiLevelProcessor:
    """Parses v2 messages, encodes per-level states, and computes per-level
    rewards from consecutive observations. Shared across connections."""

    def __init__(self):
        self.scales = _AdaptiveScales()
        self._prev_raw: Dict[int, dict] = {}
        self._seconds_since_compaction: Dict[int, float] = {}
        # Wall-clock of each level's last appearance, so a level that drops out
        # of the message (because it emptied) and comes back is discounted over
        # the real elapsed time rather than over one telemetry window.
        self._last_seen: Dict[int, float] = {}
        self._prev_tree_cost: Optional[float] = None
        # Exponentially weighted byte totals behind the windowed write
        # amplification the write hinge is measured on (config.WAF_WINDOW_SECONDS).
        self._ewma_physical_write_bytes = 0.0
        self._ewma_logical_write_bytes = 0.0
        # The Lagrange multipliers live in lagrange.MULTIPLIERS (D-9): the
        # reward moves them once per frame, the trainer prices replay with them.
        self._frames = 0
        # D-12 correction: what the resume-straddling first frame carried and
        # the reward left out, for the health summary.
        self._resume_frame_dropped: Dict[str, float] = {}
        # Run-to-date totals of every flow the constraints are ratios of. The
        # marginal terms and the multiplier slacks come from these, so the
        # reward's write, read, scan and stall accounting sums to the
        # evaluator's whole-run statistic rather than to a window estimate.
        self._cum = {"phys": 0.0, "log": 0.0, "probes": 0.0, "gets": 0.0,
                     "run_seeks": 0.0, "scans": 0.0, "stall": 0.0,
                     "elapsed": 0.0,
                     # D-10: per-operation latency totals, telemetry unit.
                     "get_count": 0.0, "get_sum": 0.0,
                     "scan_count": 0.0, "scan_sum": 0.0,
                     "write_count": 0.0, "write_sum": 0.0,
                     # D-12: the same totals from the end of the multiplier
                     # warm-up, which drive the latency multiplier's slack.
                     "get_since_count": 0.0, "get_since_sum": 0.0,
                     "scan_since_count": 0.0, "scan_since_sum": 0.0,
                     "write_since_count": 0.0, "write_since_sum": 0.0}
        # The constraint features of the state (D-9), written by
        # _global_reward for the frame being encoded.
        self._constraint_view: Dict[str, float] = {}

    # -- parsing --------------------------------------------------------

    def _parse_globals(self, msg: dict) -> dict:
        g = {k: _as_float(msg.get(k, d)) for k, d in GLOBAL_DEFAULTS.items()}
        # Generation equality is a correctness check, not a model feature.
        # Converting unrelated uint64 values through IEEE-754 can collapse
        # them to the same float and hide a superseded structural snapshot.
        for identifier in ("structural_source_generation",
                           "structural_built_generation",
                           "score_event_generation",
                           "prev_reward_invalid_reason_mask"):
            try:
                g[identifier] = int(msg.get(identifier, 0) or 0)
            except (TypeError, ValueError):
                g[identifier] = 0
        g["done"] = 1.0 if g["done"] else 0.0
        return g

    def _parse_level(self, entry: dict) -> dict:
        raw = {k: _as_float(entry.get(k, d)) for k, d in LEVEL_DEFAULTS.items()}
        # Attribution identifiers are diagnostics, not floating-point model
        # features. Preserve their exact JSON integer representation so a
        # 64-bit decision/generation can be joined to RocksDB event logs.
        for identifier in (
                "prev_decision_id", "prev_snapshot_epoch",
                "prev_scheduling_result", "prev_completion_result",
                "prev_completed_decision_id",
                "prev_completed_decision_generation",
                "prev_completed_eligibility_generation",
                "prev_completed_override_reason", "prev_override_reason"):
            try:
                raw[identifier] = int(entry.get(identifier, 0) or 0)
            except (TypeError, ValueError):
                raw[identifier] = 0
        for flag in ("default_needed", "is_last", "prev_action_overridden",
                     "prev_compaction_picked", "gate_open", "in_backoff"):
            raw[flag] = 1.0 if raw[flag] else 0.0
        return raw

    # -- feature helpers --------------------------------------------------

    @staticmethod
    def _fullness(raw: dict, g: dict) -> float:
        level = int(raw["level"])
        if level == 0:
            return _clamp(raw["files"] / max(1.0, g["l0_compaction_trigger"]))
        return _clamp(raw["bytes"] / max(1.0, raw["target_bytes"]))

    @staticmethod
    def _raw_fullness(raw: dict, g: dict) -> float:
        """Fullness without the [0,1] clamp — deferral lets a level legitimately
        exceed its target, and the overshoot is exactly what the space and
        stall terms need to see."""
        level = int(raw["level"])
        if level == 0:
            return raw["files"] / max(1.0, g["l0_compaction_trigger"])
        return raw["bytes"] / max(1.0, raw["target_bytes"])

    @staticmethod
    def _next_fullness(raw: dict) -> float:
        if raw["is_last"]:
            return 0.0
        return _clamp(raw["next_level_bytes"] / max(1.0, raw["next_level_target_bytes"]))

    @staticmethod
    def _dt_seconds(g: dict) -> float:
        micros = g["interval_micros"]
        if micros <= 0.0:
            return 0.0
        return micros / 1e6

    @staticmethod
    def _rate(value: float, dt: float) -> float:
        """Per-second rate. A zero interval (first sample) yields 0 rather than
        an infinite spike."""
        return value / dt if dt > 0.0 else 0.0

    @staticmethod
    def _read_fractions(g: dict) -> Tuple[float, float]:
        """(share of gets served from L0, physical file reads per read op).

        The second element used to be `non_last_level_read_count /
        (non_last + last)` — a ratio *between* two level classes, which
        measured p50 = 1.00 and mean 0.978 over an entire 5M run. It was a
        constant input to the network, and because it also served as the
        deep-level `read_pressure` in the reward, it silently turned that
        reward term into a second fullness penalty wearing a read-amp label.

        The magnitude those two counters carry is the useful part:
        `(non_last + last) / (gets + seeks)` is SST file reads per read
        operation, i.e. physical read amplification. Both tickers are
        incremented per file read in RecordIOStats
        (file/random_access_file_reader.cc), so this counts reads that missed
        the block cache — a low value on a fully cached database is a true
        statement about that configuration, not a dead signal.
        """
        gets = g["get_hit_l0"] + g["get_hit_l1"] + g["get_hit_l2_and_up"]
        l0_share = g["get_hit_l0"] / gets if gets > 0 else 0.0
        read_ops = g["keys_read"] + g["seeks"]
        file_reads = g["non_last_level_read_count"] + g["last_level_read_count"]
        reads_per_op = file_reads / read_ops if read_ops > 0 else 0.0
        return l0_share, reads_per_op

    # -- encoding ---------------------------------------------------------

    def _encode(self, raw: dict, g: dict) -> np.ndarray:
        level = int(raw["level"])
        key = f"l{level}"
        s = self.scales
        dt = self._dt_seconds(g)

        arrival_rate = self._rate(raw["bytes_in"], dt)
        io_rate = self._rate(raw["bytes_read_out"] + raw["bytes_written_out"], dt)
        event_rate = self._rate(
            raw["compactions_from"] + raw["compactions_scheduled"], dt)
        read_rate = self._rate(g["keys_read"] + g["seeks"], dt)

        s.observe(f"{key}.files", raw["files"])
        s.observe(f"{key}.bytes", raw["bytes"])
        s.observe(f"{key}.arrival_rate", arrival_rate)
        s.observe(f"{key}.io_rate", io_rate)
        s.observe(f"{key}.event_rate", event_rate)
        s.observe(f"{key}.next_files", raw["next_level_files"])
        s.observe(f"{key}.overlap", raw["overlap_bytes"])
        s.observe("global.pending", g["pending_compaction_bytes"])
        s.observe("global.read_rate", read_rate)

        seconds_since = self._seconds_since_compaction.get(level, 0.0)
        raw_fullness = self._raw_fullness(raw, g)
        l0_hit_fraction, file_reads_per_op = self._read_fractions(g)
        s.observe("global.file_reads_per_op", file_reads_per_op)
        due_age_s = raw["due_age_micros"] / 1e6
        pressure_s = raw["pressure_score_micros"] / 1e6
        s.observe(f"{key}.due_age", due_age_s)
        s.observe(f"{key}.pressure", pressure_s)
        s.observe(f"{key}.blocked", raw["jobs_blocked"])

        point_amp = (g["point_sst_probes"] / g["keys_read"]
                     if g["keys_read"] > 0 else 0.0)
        s.observe("global.point_amp", point_amp)
        # D-9: the constraint view _global_reward wrote for this frame. The
        # withdrawn scan-work metric (a constant at its floor) and the space
        # estimate on the denominator D-3 retired are no longer features.
        cv = self._constraint_view

        if level == 0:
            slowdown_pressure = _clamp(
                raw["files"] / max(1.0, g["l0_slowdown_trigger"]))
            stop_pressure = _clamp(raw["files"] / max(1.0, g["l0_stop_trigger"]))
        else:
            # Same meaning for deeper levels: proximity to trouble, expressed
            # as overshoot past the level's own target.
            slowdown_pressure = _clamp(raw_fullness - 1.0)
            stop_pressure = _clamp((raw_fullness - 1.0) / 2.0)

        values: List[float] = [
            _clamp(raw["score"] / config.SCORE_CLAMP),
            self._fullness(raw, g),
            s.normalize(f"{key}.files", raw["files"]),
            s.normalize(f"{key}.bytes", raw["bytes"]),
            s.normalize(f"{key}.arrival_rate", arrival_rate),
            s.normalize(f"{key}.io_rate", io_rate),
            s.normalize(f"{key}.event_rate", event_rate),
            _clamp(seconds_since / _SECONDS_SINCE_SCALE),
            s.normalize(f"{key}.due_age", due_age_s),
            s.normalize(f"{key}.pressure", pressure_s),
            raw["gate_open"],
            s.normalize(f"{key}.blocked", raw["jobs_blocked"]),
            raw["in_backoff"],
            self._next_fullness(raw),
            _clamp(raw["next_level_score"] / config.SCORE_CLAMP),
            s.normalize(f"{key}.next_files", raw["next_level_files"]),
            _clamp(raw["overlap_bytes"] / max(1.0, raw["bytes"]) / 4.0),
            s.normalize(f"{key}.overlap", raw["overlap_bytes"]),
            slowdown_pressure,
            stop_pressure,
            1.0 if g["stall_count"] > 0 else 0.0,
            1.0 if g["stop_count"] > 0 else 0.0,
            s.normalize("global.pending", g["pending_compaction_bytes"]),
            raw["default_needed"],
            s.normalize("global.read_rate", read_rate),
            l0_hit_fraction,
            s.normalize("global.file_reads_per_op", file_reads_per_op),
            s.normalize("global.point_amp", point_amp),
            1.0 if (g["structural_source_generation"]
                    != g["structural_built_generation"]) else 0.0,
            cv.get("write_cum_over_bound", 0.0),
            cv.get("write_window_over_bound", 0.0),
            cv.get("space_bytes_over_bound", 0.0),
            cv.get("lambda_write_norm", 0.0),
            cv.get("lambda_space_norm", 0.0),
            cv.get("lambda_latency_norm", 0.0),
            cv.get("lambda_scan_norm", 0.0),
            cv.get("lambda_stall_norm", 0.0),
        ]
        return np.array(values, dtype=np.float32)

    @staticmethod
    def _hinge(value: float, bound: float) -> float:
        """Relative excess of `value` over `bound`, zero inside the bound and
        zero when no bound is configured (unconstrained ablation)."""
        if bound <= 0.0:
            return 0.0
        return _positive(value / bound - 1.0)

    def reward_state(self) -> dict:
        """Multipliers and the bounds they enforce, for the health summary."""
        cum = dict(self._cum)
        return {
            "lambda": MULTIPLIERS.values(),
            "lambda_lr": config.LAMBDA_LR,
            "frames": self._frames,
            "cumulative": cum,
            "write_amplification_cumulative": (
                cum["phys"] / cum["log"] if cum["log"] > 0 else None),
            "point_read_amplification_cumulative": (
                cum["probes"] / cum["gets"] if cum["gets"] > 0 else None),
            "write_bound": config.WRITE_BOUND,
            "space_bytes_bound": config.SPACE_BYTES_BOUND,
            "scan_seeks_bound": config.SCAN_SEEKS_BOUND,
            "stall_fraction_bound": config.STALL_FRACTION_BOUND,
            "space_relative_margin": config.SPACE_RELATIVE_MARGIN,
            "constrained": bool(config.BASELINE_LIMITS),
            # D-12: what drove lambda_latency, and where the arm and the
            # baseline's trajectory ended, for the scorer.
            "resume_frame_dropped": dict(self._resume_frame_dropped),
            "latency_slack_form": ("since_warmup_vs_trajectory"
                                   if config.LATENCY_TRAJECTORY
                                   else "whole_run_vs_limit"),
            "latency_since_warmup_avg_ns": {
                op: (cum[f"{op}_since_sum"] / cum[f"{op}_since_count"]
                     if cum[f"{op}_since_count"] > 0 else None)
                for op in ("get", "scan", "write")},
            "latency_reference_end_ns": {
                op: config.latency_reference_ns(op, cum["elapsed"])
                for op in ("get", "scan", "write")},
        }

    def _global_reward(self, g: dict, levels: List[dict],
                       dt: float) -> Tuple[np.ndarray, dict]:
        """One cooperative reward for the physical tree: the constrained
        objective of PATHWAYS Pathway D as a COMPONENT VECTOR (D-9).

        Returns (components, details). In lagrange.COMPONENTS order, each
        already integrated over the frame:

            shaping    gamma^dt Phi(s_t) - Phi(s_{t-1}), Phi = -REWARD_STRUCTURAL_RUNS * runs
            objective  REWARD_READ * probes_t / G_bar                  R, Get-weighted
            write      (phys_t - W_bound * log_t) / L_bar               signed marginal, W units
            space      [bytes_t / bytes_bound - 1]^+ * dt               level hinge (D-6)
            latency    sum_op [avg_op / limit_op - 1]^+ * dt            level hinge (D-8)
            scan       (run_seeks_t - seeks_bound * scans_t) / S_bar    signed marginal
            stall      stall_seconds_t - stall_bound * dt               signed marginal

        The scalar the learner sees is r = shaping - objective - sum lambda_x x,
        priced by lagrange.MULTIPLIERS: here for the logs, and again at
        replay-sample time for training.

        Flows against levels. W, R, seeks per scan and the stall fraction are
        ratios of run totals, so a frame's honest share is its marginal
        contribution -- numerator minus bound times denominator -- divided by
        the run-to-date mean denominator rate, which puts it in the metric's
        own unit. Summed over the run that is (total - bound * total
        denominator) / mean rate: the constraint the evaluator scores, with no
        window bias. The 10 s windowed write ratio this replaces was
        time-weighted against a byte-weighted criterion; under Assoc's 26-46%
        stall fractions it read 13-61% above the run W on 94-100% of frames
        (history 14.20), so its hinge fired whatever the policy did. Space and
        the latency averages are levels -- a window value estimates the run
        value -- and keep the hinge.

        Every multiplier then takes one SIGNED step on its constraint's slack
        in its own unit (run-to-date for flows, window for levels), so it can
        fall when the constraint has slack.

        D-12: the latency multiplier's slack is the since-warm-up cumulative
        average against the baseline's own since-warm-up cumulative at the
        same elapsed time, not the whole-run cumulative against the whole-run
        limit. The first 1-2 s after `rlresume` stall writes at 15-32x the
        limit under the L0 backlog the suspended controller inherits from the
        load; the calibration arms carry the same transient, so under the
        whole-run form the baseline itself reads a positive slack until
        120-150 s of a 160 s run (history 14.23). The priced term is unchanged.
        """
        if g["done"]:
            # The shutdown message contains synthetic zero level states, not a
            # newly empty physical tree. It finalizes pending credit only.
            return (np.zeros(COMPONENT_COUNT, dtype=np.float64),
                    {"terminal": 1.0})
        self._frames += 1
        constrained = bool(config.BASELINE_LIMITS)
        # D-12 correction: the first frame straddles `rlresume` and can carry
        # load-phase writes the evaluator excludes (config.DROP_RESUME_FRAME).
        # Its flows enter no run-to-date total, no marginal term and no window
        # average; its elapsed time and its tree state still count.
        resume_frame = self._frames == 1 and config.DROP_RESUME_FRAME

        # -- run-to-date totals of every flow -------------------------------
        phys_t = (_positive(g["flushed_bytes"])
                  + _positive(g["compaction_bytes_written"]))
        log_t = _positive(g["user_logical_write_bytes"])
        # D-10/D-11: add the per-Put WriteBatch framing the evaluator's ticker
        # counts and the telemetry does not (config.WRITE_BATCH_OVERHEAD_BYTES,
        # 16 bytes), so the run-to-date W here is the evaluator's
        # measured-phase W.
        log_t += (config.WRITE_BATCH_OVERHEAD_BYTES
                  * _positive(g["write_latency_count"]))
        probes_t = _positive(g["point_sst_probes"])
        gets_t = _positive(g["keys_read"])
        run_seeks_t = _positive(g["scan_sorted_run_seeks"])
        scans_t = _positive(g["seeks"])
        stall_t = _positive(g["stall_duration_micros"]) / 1e6
        if resume_frame:
            self._resume_frame_dropped = {
                "physical_bytes": phys_t, "logical_bytes": log_t,
                "gets": gets_t, "scans": scans_t,
                "write_ops": _positive(g["write_latency_count"])}
            phys_t = log_t = probes_t = gets_t = run_seeks_t = scans_t = 0.0
            stall_t = 0.0
        cum = self._cum
        cum["phys"] += phys_t
        cum["log"] += log_t
        cum["probes"] += probes_t
        cum["gets"] += gets_t
        cum["run_seeks"] += run_seeks_t
        cum["scans"] += scans_t
        cum["stall"] += stall_t
        cum["elapsed"] += max(dt, 0.0)
        elapsed = max(cum["elapsed"], 1e-9)
        g_bar = cum["gets"] / elapsed          # Gets per second, run to date
        l_bar = cum["log"] / elapsed           # logical bytes per second
        s_bar = cum["scans"] / elapsed         # scans per second
        w_cum = cum["phys"] / cum["log"] if cum["log"] > 0 else 0.0
        seeks_cum = (cum["run_seeks"] / cum["scans"]
                     if cum["scans"] > 0 else 0.0)
        stall_cum = cum["stall"] / elapsed

        # -- objective: point probes per Get, Get-weighted --------------------
        # probes_t / G_bar integrates to R_run * elapsed (up to the running
        # mean), so the learner minimises the criterion's own quantity. The
        # window ratio probes_t / gets_t weighted every window equally
        # whatever its read count, and a stalled window has few reads.
        point_amp = probes_t / gets_t if gets_t > 0 else 0.0
        objective = config.REWARD_READ * (probes_t / g_bar if g_bar > 0 else 0.0)

        # -- shaping: absolute sorted runs a lookup can probe -----------------
        l0_runs = sum(raw["files"] for raw in levels
                      if int(raw["level"]) == 0)
        deep_runs = sum(1.0 for raw in levels
                        if int(raw["level"]) > 0 and raw["files"] > 0)
        if g["output_only_level_files"] > 0:
            deep_runs += 1.0
        sorted_runs = l0_runs + deep_runs
        tree_cost = config.REWARD_STRUCTURAL_RUNS * sorted_runs
        gamma_dt = (config.GAMMA_PER_SEC ** dt
                    if config.GAMMA_PER_SEC > 0.0 and dt > 0.0
                    else config.GAMMA)
        shaping = 0.0
        if self._prev_tree_cost is not None:
            # Phi(s) = -tree_cost(s): gamma(dt) Phi(next) - Phi(current).
            shaping = self._prev_tree_cost - gamma_dt * tree_cost
        self._prev_tree_cost = tree_cost

        # -- write: signed marginal against W_base (1 + delta_W) -------------
        # The 10 s window is kept as a diagnostic and as a state feature; it
        # no longer feeds the hinge (see the docstring).
        decay = (math.exp(-dt / config.WAF_WINDOW_SECONDS)
                 if config.WAF_WINDOW_SECONDS > 0.0 and dt > 0.0 else 1.0)
        self._ewma_physical_write_bytes = (
            self._ewma_physical_write_bytes * decay + phys_t)
        self._ewma_logical_write_bytes = (
            self._ewma_logical_write_bytes * decay + log_t)
        waf_window = (self._ewma_physical_write_bytes
                      / self._ewma_logical_write_bytes
                      if self._ewma_logical_write_bytes > 0 else 0.0)
        if constrained and config.WRITE_BOUND > 0.0 and l_bar > 0.0:
            write = (phys_t - config.WRITE_BOUND * log_t) / l_bar
            write_slack = (w_cum / config.WRITE_BOUND - 1.0
                           if cum["log"] > 0 else 0.0)
        else:
            write, write_slack = 0.0, 0.0

        # -- space: level hinge in settled physical bytes (D-6) --------------
        space_bytes = _positive(g["physical_sst_bytes"])
        space_excess = self._hinge(space_bytes, config.SPACE_BYTES_BOUND)
        space = space_excess * dt
        space_slack = (space_bytes / config.SPACE_BYTES_BOUND - 1.0
                       if config.SPACE_BYTES_BOUND > 0.0 else 0.0)
        # Diagnostic only: the depth-sensitive estimate D-3 replaced, kept so
        # a frame can be joined to the older logs.
        space_amp_estimate = (g["physical_sst_bytes"] / g["live_logical_bytes"]
                              if g["live_logical_bytes"] > 0 else 0.0)

        # -- latency: the averages as FLOWS, in the telemetry's unit (D-10) --
        # A run average is sum / count, a ratio of run totals, so a frame's
        # honest share is (sum_t - limit * count_t) over the run-to-date mean
        # count rate, in units of the limit; summed over the run that is the
        # relative excess of the run average, times elapsed time. The limit
        # is the baseline's average as measured by THIS instrument -- the C++
        # window telemetry, totalled by 06_calibrate_live_guard.py -- not
        # db_bench's histogram, which disagrees by 19x on scans and 9x on
        # writes. The D-9 smoke arm's scan hinge read +18 on every frame
        # against a run whose evaluator scan latency was 4% under its limit,
        # and lambda_latency railed on the first frames. The multiplier's
        # slack is the worst operation's signed run-to-date excess. p99 is
        # logged per operation as a raw window value and enters nothing
        # (D-8); D-8's attribution of the D-7 excess to write p99 was wrong
        # -- that hinge was zero on every frame -- the excess was scan_avg.
        latency = 0.0
        latency_slack = None
        latency_slack_whole_run = None
        latency_terms = {}
        latency_window_avg_ns = {}
        latency_window_p99_ns = {}
        latency_cumulative_avg_ns = {}
        latency_since_warmup_avg_ns = {}
        latency_reference_avg_ns = {}
        # D-12: the multiplier's slack is the since-warm-up cumulative against
        # the baseline's own since-warm-up cumulative at the same elapsed time
        # (config.LATENCY_TRAJECTORY). The load's tail -- the first 1-2 s after
        # `rlresume`, writes stalled at 15-32x the limit under an inherited
        # L0 backlog -- never enters it, and the baseline's own transient
        # cancels rather than being carried by the whole-run cumulative for
        # 120-150 s (history 14.23). The priced TERM below is unchanged. The
        # whole-run form is still computed and logged as
        # `latency_slack_whole_run` so the two can be compared offline.
        lambda_warm = cum["elapsed"] >= config.LAMBDA_WARMUP_SECONDS
        since_warmup = lambda_warm and bool(config.LATENCY_TRAJECTORY)
        for operation in ("get", "scan", "write"):
            count_t = _positive(g[f"{operation}_latency_count"])
            avg_t = _positive(g[f"{operation}_latency_avg_ns"])
            latency_window_p99_ns[operation] = g[f"{operation}_latency_p99_ns"]
            if count_t <= 0 or resume_frame:
                continue
            latency_window_avg_ns[operation] = avg_t
            cum[f"{operation}_count"] += count_t
            cum[f"{operation}_sum"] += avg_t * count_t
            cum_avg = cum[f"{operation}_sum"] / cum[f"{operation}_count"]
            latency_cumulative_avg_ns[operation] = cum_avg
            if since_warmup:
                cum[f"{operation}_since_count"] += count_t
                cum[f"{operation}_since_sum"] += avg_t * count_t
            limit = config.BASELINE_LIMITS.get(
                f"{operation}_latency_avg_ns_telemetry_limit", 0.0)
            reference = config.BASELINE_LIMITS.get(
                f"{operation}_latency_avg_ns_telemetry_reference", 0.0)
            if not constrained or limit <= 0.0:
                continue
            cbar = cum[f"{operation}_count"] / elapsed
            if cbar <= 0.0:
                continue
            term = (avg_t - limit) * count_t / (limit * cbar)
            latency_terms[f"{operation}_avg"] = term
            latency += term
            signed_whole_run = cum_avg / limit - 1.0
            latency_slack_whole_run = (
                signed_whole_run if latency_slack_whole_run is None
                else max(latency_slack_whole_run, signed_whole_run))
            if config.LATENCY_TRAJECTORY:
                if not since_warmup or cum[f"{operation}_since_count"] <= 0:
                    continue   # warm-up: the multiplier is held anyway
                since_avg = (cum[f"{operation}_since_sum"]
                             / cum[f"{operation}_since_count"])
                latency_since_warmup_avg_ns[operation] = since_avg
                trajectory_ns = config.latency_reference_ns(
                    operation, cum["elapsed"])
                latency_reference_avg_ns[operation] = trajectory_ns
                if trajectory_ns <= 0.0:
                    continue
                margin = limit / reference if reference > 0.0 else 1.0
                signed = since_avg / (margin * trajectory_ns) - 1.0
            else:
                signed = signed_whole_run
            latency_slack = (signed if latency_slack is None
                             else max(latency_slack, signed))
        if latency_slack is None:
            latency_slack = 0.0
        if latency_slack_whole_run is None:
            latency_slack_whole_run = 0.0
        latency_excess = _positive(latency_slack)

        # -- scan: signed marginal on sorted-run seeks per scan (P0-1) --------
        scan_seeks = run_seeks_t / scans_t if scans_t > 0 else 0.0
        if constrained and config.SCAN_SEEKS_BOUND > 0.0 and s_bar > 0.0:
            scan = (run_seeks_t - config.SCAN_SEEKS_BOUND * scans_t) / s_bar
            scan_slack = (seeks_cum / config.SCAN_SEEKS_BOUND - 1.0
                          if cum["scans"] > 0 else 0.0)
        else:
            scan, scan_slack = 0.0, 0.0

        # -- stall: signed marginal on the stall fraction (P0-2, zero margin),
        # only against a telemetry-unit reference (D-11; config.py) --------
        stall_fraction = stall_t / dt if dt > 0.0 else 0.0
        if constrained and config.STALL_TERM_ACTIVE:
            stall = stall_t - config.STALL_FRACTION_BOUND * dt
            stall_slack = stall_cum - config.STALL_FRACTION_BOUND
        else:
            stall, stall_slack = 0.0, 0.0

        # -- multipliers: one signed step each after the warm-up, then price --
        # D-10: the run-to-date ratios are dominated by the bulk load's
        # compaction backlog for the first tens of seconds (config.py,
        # LAMBDA_WARMUP_SECONDS); no multiplier acts on them until then.
        # `lambda_warm` was decided above, before the latency accumulators.
        if lambda_warm:
            lam = {
                "write": MULTIPLIERS.update("write", write_slack),
                "space": MULTIPLIERS.update("space", space_slack),
                "latency": MULTIPLIERS.update("latency", latency_slack),
                "scan": MULTIPLIERS.update("scan", scan_slack),
                "stall": MULTIPLIERS.update("stall", stall_slack),
            }
        else:
            lam = MULTIPLIERS.values()
        components = np.array(
            [shaping, objective, write, space, latency, scan, stall],
            dtype=np.float64)
        reward = float(MULTIPLIERS.price(components))
        cost_integrated = (objective + lam["write"] * write
                           + lam["space"] * space + lam["latency"] * latency
                           + lam["scan"] * scan + lam["stall"] * stall)
        cost_rate = cost_integrated / dt if dt > 0.0 else 0.0

        lambda_scale = max(config.LAMBDA_MAX, 1e-9)
        self._constraint_view = {
            "write_cum_over_bound": (
                _clamp(w_cum / config.WRITE_BOUND / 2.0)
                if config.WRITE_BOUND > 0.0 else 0.0),
            "write_window_over_bound": (
                _clamp(waf_window / config.WRITE_BOUND / 2.0)
                if config.WRITE_BOUND > 0.0 else 0.0),
            "space_bytes_over_bound": (
                _clamp(space_bytes / config.SPACE_BYTES_BOUND / 2.0)
                if config.SPACE_BYTES_BOUND > 0.0 else 0.0),
            "lambda_write_norm": lam["write"] / lambda_scale,
            "lambda_space_norm": lam["space"] / lambda_scale,
            "lambda_latency_norm": lam["latency"] / lambda_scale,
            "lambda_scan_norm": lam["scan"] / lambda_scale,
            "lambda_stall_norm": lam["stall"] / lambda_scale,
        }

        l0_hit_fraction, file_reads_per_op = self._read_fractions(g)
        return components, {
            "global_tree_cost": tree_cost,
            "global_shaping": shaping,
            "global_gamma_dt": gamma_dt,
            "sorted_runs": sorted_runs,
            "objective": objective,
            "point_probe_amplification": point_amp,
            "point_probe_amplification_cumulative": (
                cum["probes"] / cum["gets"] if cum["gets"] > 0 else 0.0),
            "write_amplification_window": waf_window,
            "write_amplification_cumulative": w_cum,
            "write_marginal": write,
            "write_slack": write_slack,
            # Hinge on the run-to-date ratio, for continuity with the D-7
            # readers; the reward's write term is `write_marginal`.
            "write_excess": _positive(write_slack),
            "space_physical_sst_bytes": space_bytes,
            "space_bytes_bound": config.SPACE_BYTES_BOUND,
            "space_amplification_estimate": space_amp_estimate,
            "space_excess": space_excess,
            "space_slack": space_slack,
            "sorted_run_seeks_per_scan": scan_seeks,
            "sorted_run_seeks_per_scan_cumulative": seeks_cum,
            "scan_marginal": scan,
            "scan_slack": scan_slack,
            "scan_excess": _positive(scan_slack),
            "latency_excess": latency_excess,
            "latency_slack": latency_slack,
            "latency_slack_whole_run": latency_slack_whole_run,
            "latency_since_warmup_avg_ns": latency_since_warmup_avg_ns,
            "latency_reference_avg_ns": latency_reference_avg_ns,
            "latency_terms": latency_terms,
            "latency_window_avg_ns": latency_window_avg_ns,
            "latency_window_p99_ns": latency_window_p99_ns,
            "latency_cumulative_avg_ns": latency_cumulative_avg_ns,
            "lambda_warm": lambda_warm,
            "write_logical_bytes_window": log_t,
            "stall_fraction": stall_fraction,
            "stall_fraction_cumulative": stall_cum,
            "stall_marginal": stall,
            "stall_slack": stall_slack,
            "stall_excess": _positive(stall_slack),
            "lambda_write": lam["write"],
            "lambda_space": lam["space"],
            "lambda_latency": lam["latency"],
            "lambda_scan": lam["scan"],
            "lambda_stall": lam["stall"],
            "constrained": constrained,
            "cost_rate": cost_rate,
            "cost_integrated": cost_integrated,
            "reward_components": [float(x) for x in components],
            "read_gets": gets_t,
            "read_seeks": scans_t,
            "read_l0_hit_fraction": l0_hit_fraction,
            "read_file_reads_per_op": file_reads_per_op,
            "reward_dt": dt,
            "reward_raw": reward,
        }

    def _compute_reward_legacy(self, raw: dict, g: dict) -> Tuple[float, dict]:
        """The pre-redesign reward, kept for a single-knob ablation
        (RL_REWARD_LEGACY=1)."""
        level = int(raw["level"])
        prev = self._prev_raw.get(level)
        if prev is None:
            return 0.0, {"initial_observation": 1.0}
        prev_action = prev.get("_action")

        score_pressure = _clamp(raw["score"])
        fullness = self._fullness(raw, g)
        prev_fullness = self._fullness(prev, g)
        if level == 0:
            growth = _clamp(_positive(raw["files"] - prev["files"])
                            / max(1.0, g["l0_stop_trigger"]))
        else:
            growth = _clamp(_positive(raw["bytes"] - prev["bytes"])
                            / max(1.0, raw["target_bytes"]))
        relief = _clamp(_positive(prev_fullness - fullness))

        pending_pressure = self.scales.normalize(
            "global.pending", g["pending_compaction_bytes"])
        prev_pending = prev.get("_global_pending", g["pending_compaction_bytes"])
        pending_growth = self.scales.normalize(
            "global.pending",
            _positive(g["pending_compaction_bytes"] - prev_pending))

        dt = self._dt_seconds(g)
        io = self.scales.normalize(
            f"l{level}.io_rate",
            self._rate(raw["bytes_read_out"] + raw["bytes_written_out"], dt))
        event = 1.0 if (raw["compactions_from"] > 0
                        or raw["compactions_scheduled"] > 0) else 0.0
        stall = 1.0 if g["stall_count"] > 0 else 0.0
        stop = 1.0 if g["stop_count"] > 0 else 0.0
        if config.ML_STALL_SCALE_BY_PRESSURE:
            stall *= fullness
            stop *= fullness

        unnecessary = 0.0
        if (prev_action == 1 and prev["score"] < _UNNECESSARY_SCORE_THRESHOLD
                and relief == 0.0):
            unnecessary = 1.0

        late = 0.0
        if prev_action == 0 and (raw["default_needed"] > 0.0 or stall or stop):
            late = 1.0

        reward = (
            -config.REWARD_L0_PRESSURE * score_pressure
            - config.REWARD_L0_GROWTH * growth
            - config.REWARD_PENDING_PRESSURE * pending_pressure
            - config.REWARD_PENDING_GROWTH * pending_growth
            - config.REWARD_STALL * stall
            - config.REWARD_STOP * stop
            - config.REWARD_COMPACTION_IO * io
            - config.REWARD_COMPACTION_EVENT * event
            - config.REWARD_UNNECESSARY_COMPACTION * unnecessary
            - config.REWARD_LATE_NO_COMPACTION * late
            + config.REWARD_PRESSURE_RELIEF * relief
        )
        reward = _clamp(reward, -1.0, 1.0)

        return reward, {
            "score_pressure": score_pressure,
            "growth": growth,
            "relief": relief,
            "pending_pressure": pending_pressure,
            "pending_growth": pending_growth,
            "compaction_io": io,
            "compaction_event": event,
            "stall": stall,
            "stop": stop,
            "unnecessary_compaction": unnecessary,
            "late_no_compaction": late,
        }

    @staticmethod
    def _compact_allowed(raw: dict) -> bool:
        """The action mask (D-9).

        `compact_now` always stays available for a level RocksDB considers
        due -- otherwise the mask would silently remove the agent's ability
        to agree with the default. Below due, the two moves the theory rules
        out are withheld rather than left for the residual to re-learn:

          * a level >= 1 below score 1 (A4: one sorted run whatever its size,
            so compacting it removes no probe; Theorem B.1: it forfeits the
            overwrites a later merge would drop). The D-7 arms released L1
            at 41% of target on three quarters of their L1 compactions;
          * L0 below its trigger unless the compaction removes at least
            PRIOR_MIN_RUN_REDUCTION runs net of the output run -- the same
            rule the prior prices relief by (P1c-23, history 14.10). The
            D-7 arms compacted L0 at a mean of 1.1 files on 16-26% of
            below-trigger frames.

        The mask is never looser than the C++ optional-token gate
        (RL_OPTIONAL_MIN_SCORE), which is the invariant the old floor kept.
        """
        if raw["files"] <= 0:
            return False
        if raw["score"] >= 1.0 or raw["default_needed"] > 0.0:
            return True
        if raw["score"] < config.ML_MIN_COMPACT_SCORE:
            return False
        if int(raw["level"]) == 0:
            if not config.MASK_L0_MIN_RUN_REDUCTION:
                return True
            creates_level = ((not raw["is_last"])
                             and raw["next_level_files"] <= 0.0)
            net_runs = raw["files"] - (1.0 if creates_level else 0.0)
            return net_runs >= config.PRIOR_MIN_RUN_REDUCTION
        return not config.MASK_DEEP_BELOW_DUE

    # -- public API -------------------------------------------------------

    def process(self, msg: dict) -> List[LevelDecision]:
        """Parse a v2 message into per-level decisions, in request order."""
        decision_id, invalid_mask = validate_protocol_message(msg)
        g = self._parse_globals(msg)
        dt = self._dt_seconds(g)
        self.scales.tick(dt)
        now = time.monotonic()

        parsed = []
        for entry in msg.get("levels", []):
            if not isinstance(entry, dict):
                continue
            parsed.append(self._parse_level(entry))
        if invalid_mask:
            # Re-anchor potential shaping at the known state on this boundary;
            # the excluded interval must never appear as a potential delta in
            # the following valid reward.
            self._prev_tree_cost = None
        global_vector, global_components = self._global_reward(g, parsed, dt)
        global_reward = float(MULTIPLIERS.price(global_vector))

        decisions: List[LevelDecision] = []
        for raw in parsed:
            level = int(raw["level"])
            state = self._encode(raw, g)
            if g["done"]:
                reward, components = 0.0, {"terminal": 1.0}
                vector = np.zeros(COMPONENT_COUNT, dtype=np.float64)
            elif config.REWARD_LEGACY:
                reward, components = self._compute_reward_legacy(raw, g)
                vector = scalar_components(reward)
            else:
                reward, components = global_reward, dict(global_components)
                vector = global_vector

            if raw["compactions_from"] > 0 or raw["compactions_scheduled"] > 0:
                self._seconds_since_compaction[level] = 0.0
            else:
                self._seconds_since_compaction[level] = (
                    self._seconds_since_compaction.get(level, 0.0) + dt)

            valid_actions = (0, 1) if self._compact_allowed(raw) else (0,)

            prior = None
            if config.ANALYTIC_PRIOR:
                adv, prior_terms = analytic_advantage(raw, g)
                prior = np.array([0.0, adv], dtype=np.float32)
                components.update(prior_terms)

            executed = None
            prev_chosen = None
            previous = self._prev_raw.get(level)
            if previous is not None:
                executed = int(raw["prev_action_executed"])
                prev_chosen = previous.get("_action")

            last_seen = self._last_seen.get(level)
            dt_discount = (now - last_seen) if last_seen is not None else dt
            self._last_seen[level] = now

            decisions.append(
                LevelDecision(level, raw, state, reward, components,
                              valid_actions, prior, dt, executed, prev_chosen,
                              dt_discount, decision_id,
                              int(raw["prev_decision_id"]),
                              bool(raw["prev_action_overridden"]),
                              invalid_mask, reward_vector=vector)
            )
        return decisions

    def advance(self, decision: LevelDecision, action: int,
                g_pending: float) -> None:
        raw = dict(decision.raw)
        raw["_global_pending"] = g_pending
        raw["_action"] = int(action)
        self._prev_raw[decision.level] = raw


def _level_save_path(level: int) -> str:
    root, ext = os.path.splitext(config.MODEL_SAVE_PATH)
    return f"{root}.l{level}{ext or '.pt'}"


class AgentPool:
    """Shared across connections: one lazily-created DQNAgent per level.

    With config.SHARED_TRUNK the pool also owns a single SharedTrunk that every
    level's agent borrows — one set of trunk weights, one optimizer, one replay
    buffer and one trainer thread — so a decision at any level contributes a
    training sample for all of them.
    """

    def __init__(self):
        self._agents: Dict[int, DQNAgent] = {}
        self._lock = threading.Lock()
        self._protocol_errors = 0
        self._reward_invalid_intervals = 0
        self._reward_invalid_reason_counts: Dict[str, int] = {}
        # Built eagerly: it is small, and creating it lazily inside get() would
        # put network construction on the decision path of whichever level
        # happened to appear first.
        self._shared: Optional[SharedTrunk] = (
            SharedTrunk(state_dim=config.ML_STATE_DIM,
                        action_dim=config.ACTION_DIM,
                        num_levels=config.ML_MAX_LEVELS)
            if config.SHARED_TRUNK else None)

    def get(self, level: int) -> DQNAgent:
        with self._lock:
            agent = self._agents.get(level)
            if agent is None:
                path = _level_save_path(level)
                agent = DQNAgent(
                    state_dim=config.ML_STATE_DIM,
                    action_dim=config.ACTION_DIM,
                    save_path=path,
                    name=f"dqn-l{level}",
                    shared=self._shared,
                    level=level,
                )
                # Resuming is opt-in: the headline experiment is a cold online
                # run, because the research claim is adaptation with no prior
                # workload knowledge.
                if (config.RESUME or config.EVAL_MODE) and os.path.exists(path):
                    try:
                        agent.load(path)
                        print(f"[pool] level {level}: loaded checkpoint "
                              f"{path} (step={agent.step})")
                    except Exception as exc:  # noqa: BLE001
                        print(f"[pool] level {level}: could not load "
                              f"checkpoint: {exc}")
                self._agents[level] = agent
            return agent

    def levels(self) -> List[int]:
        with self._lock:
            return sorted(self._agents.keys())

    def record_protocol_error(self) -> None:
        with self._lock:
            self._protocol_errors += 1

    def record_reward_invalid(self, reason_mask: int) -> None:
        if not reason_mask:
            return
        with self._lock:
            self._reward_invalid_intervals += 1
            for bit, name in REWARD_INVALID_REASON_NAMES.items():
                if reason_mask & (1 << bit):
                    self._reward_invalid_reason_counts[name] = (
                        self._reward_invalid_reason_counts.get(name, 0) + 1
                    )
            unknown = reason_mask & ~sum(
                1 << bit for bit in REWARD_INVALID_REASON_NAMES)
            if unknown:
                key = f"unknown_mask_{unknown}"
                self._reward_invalid_reason_counts[key] = (
                    self._reward_invalid_reason_counts.get(key, 0) + 1)

    def save_all(self) -> None:
        with self._lock:
            agents = dict(self._agents)
        for level, agent in agents.items():
            agent.save(_level_save_path(level))

    def close_all(self) -> None:
        with self._lock:
            agents = dict(self._agents)
        for agent in agents.values():
            agent.close()
        # The pooled trainer thread belongs to the pool, not to any one level,
        # so no agent's close() stops it.
        if self._shared is not None:
            self._shared.close()

    def train_steps(self) -> Optional[int]:
        """Gradient steps taken against the shared trunk, or None when levels
        train independently. This is the number that says whether pooling
        actually raised the learning budget."""
        return None if self._shared is None else self._shared.train_steps

    def quiesce_training(self, timeout: float = 5.0) -> bool:
        if self._shared is not None:
            return self._shared.quiesce(timeout)
        with self._lock:
            agents = list(self._agents.values())
        return all(agent.quiesce(timeout) for agent in agents)

    def health_summary(self) -> dict:
        with self._lock:
            agents = dict(self._agents)
            protocol_errors = self._protocol_errors
            reward_invalid_intervals = self._reward_invalid_intervals
            reward_invalid_reason_counts = dict(
                self._reward_invalid_reason_counts)
        levels = {
            str(level): agent.health_snapshot()
            for level, agent in sorted(agents.items())
        }
        snapshots = list(levels.values())
        if self._shared is not None:
            learner = self._shared.health_snapshot()
        else:
            errors = [item["trainer_error"] for item in snapshots
                      if item["trainer_error"]]
            first_times = [item["first_train_elapsed_seconds"]
                           for item in snapshots
                           if item["first_train_elapsed_seconds"] is not None]
            learner = {
                "replay_size": sum(item["replay_size"] for item in snapshots),
                "train_steps": sum(item["train_steps"] for item in snapshots),
                "first_train_elapsed_seconds": (
                    min(first_times) if first_times else None
                ),
                "trainer_error": "; ".join(errors) if errors else None,
                "last_loss": None,
            }
        return {
            "shared_trunk": self._shared is not None,
            "decisions": sum(item["decisions"] for item in snapshots),
            "selected_proposals": sum(
                item["selected_proposals"] for item in snapshots),
            "accepted_decisions": sum(
                item["accepted_decisions"] for item in snapshots),
            "rejected_decisions": sum(
                item["rejected_decisions"] for item in snapshots),
            "override_relabels": sum(
                item["override_relabels"] for item in snapshots),
            "full_horizon_transitions": sum(
                item["full_horizon_transitions"] for item in snapshots),
            "boundary_truncated_transitions": sum(
                item["boundary_truncated_transitions"] for item in snapshots),
            "shutdown_terminal_transitions": sum(
                item["shutdown_terminal_transitions"] for item in snapshots),
            "discarded_unconfirmed_windows": sum(
                item["discarded_unconfirmed_windows"] for item in snapshots),
            "unresolved_decisions_at_shutdown": sum(
                item["unresolved_decisions_at_shutdown"] for item in snapshots),
            "discarded_zero_credit_windows": sum(
                item["discarded_zero_credit_windows"] for item in snapshots),
            "finalized_transitions": sum(
                item["finalized_transitions"] for item in snapshots
            ),
            "replay_size": learner["replay_size"],
            "protocol_errors": protocol_errors,
            "reward_invalid_intervals": reward_invalid_intervals,
            "reward_invalid_reason_counts": reward_invalid_reason_counts,
            "pending_windows": sum(item["pending_windows"] for item in snapshots),
            "pending_confirmed_windows": sum(
                item["pending_confirmed_windows"] for item in snapshots),
            "pending_unconfirmed_windows": sum(
                item["pending_unconfirmed_windows"] for item in snapshots),
            "accepted_accounting_balanced": all(
                item["accepted_accounting_balanced"] for item in snapshots),
            "proposal_accounting_balanced": all(
                item["proposal_accounting_balanced"] for item in snapshots),
            "train_steps": learner["train_steps"],
            "first_train_elapsed_seconds": learner[
                "first_train_elapsed_seconds"
            ],
            "trainer_error": learner["trainer_error"],
            "max_abs_residual_advantage": max(
                (item["max_abs_residual_advantage"] for item in snapshots),
                default=0.0,
            ),
            "argmax_flip_count": sum(
                item["argmax_flip_count"] for item in snapshots
            ),
            "argmax_comparison_count": sum(
                item["argmax_comparison_count"] for item in snapshots
            ),
            "levels": levels,
        }
