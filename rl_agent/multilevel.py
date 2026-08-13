"""
Multi-level RL compaction support (protocol v2).

One DQN agent per LSM level. Each level's agent observes its own level's
state, the next level's state (zeros for the last level), and global pressure
signals — per the per-level architecture: a compaction from level i lands in
level i+1, so the trigger decision needs visibility into the destination's
fullness and key-range overlap.

Protocol v2 (C++ -> Python), newline-delimited JSON:
  {"version": 2, <globals...>, "levels": [{"level": 0, ...}, ...]}
Response (Python -> C++):
  {"actions": [a_0, a_1, ...]}    # one per level entry, in request order
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
    # executed actions differ whenever a safety guard or the deferral bound
    # steps in, and training must key on what was executed.
    "prev_action_executed": 0.0,
    "prev_action_overridden": 0.0,
    "prev_compaction_picked": 0.0,
    "defer_count": 0.0,
    "default_needed": 0.0,
    "is_last": 0.0,
}

# Steps-since-compaction saturates at this many decisions.
_STEPS_SINCE_SCALE = 50.0
# L0 run count at which extra files stop adding meaningful probe cost. Small,
# because the first few overlapping runs are what hurt: going 1 -> 3 files
# triples L0's probe cost, while 20 -> 22 barely changes it.
_PROBE_SATURATION = 4.0
# Multiple of its own target at which a deep level's read cost saturates.
#
# Sized from the measured distribution (5M balanced, 5523 decisions): L1 runs
# at p50 1.50x target, p90 2.91x, max 7.68x and is over target 63% of the time,
# while L2/L3/L4 sit at 0.17x/0.10x/0.03x and never exceed target at all. A
# saturation of 4.0 keeps a usable gradient across L1's whole working range —
# clamping at 1.0 would flatten the term for the 63% of samples that matter
# most — while still registering the deep levels' smaller contributions
# instead of zeroing them.
_DEEP_READ_SATURATION = 4.0
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
    """Generic decaying-max normalizer (same math as reward.AdaptiveNormalizer
    but with caller-chosen keys, so per-level scales don't collide).

    Scales freeze after `config.NORM_FREEZE_AFTER` observations. A scale that
    keeps moving means the same raw observation encodes to a different feature
    vector over time, so replayed transitions were recorded against an encoding
    that no longer exists — fatal when the whole run supplies only a few
    thousand samples.
    """

    def __init__(self, decay: float = config.NORMALIZER_DECAY,
                 freeze_after: int = None):
        self.decay = decay
        self.freeze_after = (config.NORM_FREEZE_AFTER if freeze_after is None
                             else freeze_after)
        self.scales: Dict[str, float] = {}
        self.observations = 0
        self.frozen = False

    def tick(self) -> None:
        """Advance the observation counter; freezes scales once past the
        warmup budget. Called once per message, not once per key."""
        if self.frozen:
            return
        self.observations += 1
        if self.freeze_after and self.observations >= self.freeze_after:
            self.frozen = True

    def observe(self, key: str, value: float) -> None:
        if self.frozen:
            return
        value = _positive(value)
        previous = self.scales.get(key, 1.0)
        self.scales[key] = max(1.0, value, previous * self.decay)

    def normalize(self, key: str, value: float) -> float:
        return _clamp(value / max(1.0, self.scales.get(key, 1.0)))


class _RunningStandardizer:
    """Welford mean/variance, frozen after a warmup.

    Rewards are standardized so the learner sees a signal centred near zero
    with unit-ish scale. Subtracting a constant and rescaling leaves the
    optimal policy unchanged; freezing after warmup keeps the transformation
    stationary so replayed transitions stay comparable.
    """

    # Standardizing too early, or against too small a spread, turns numerical
    # noise into large rewards. An almost-idle deep level produces raw rewards
    # around 1e-3; without these guards they were being blown up to O(1) and
    # would have taught that level's agent from pure noise.
    MIN_SAMPLES = 30
    MIN_STD = 0.05

    def __init__(self, freeze_after: int = None):
        self.freeze_after = (config.NORM_FREEZE_AFTER if freeze_after is None
                             else freeze_after)
        self.count = 0
        self.mean = 0.0
        self._m2 = 0.0
        self.frozen = False

    def update(self, value: float) -> None:
        if self.frozen:
            return
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        self._m2 += delta * (value - self.mean)
        if self.freeze_after and self.count >= self.freeze_after:
            self.frozen = True

    @property
    def std(self) -> float:
        if self.count < 2:
            return 1.0
        return max(math.sqrt(self._m2 / (self.count - 1)), self.MIN_STD)

    def apply(self, value: float) -> float:
        self.update(value)
        if self.count < self.MIN_SAMPLES:
            return value
        return (value - self.mean) / self.std


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
                        threshold given current inflow (L0); superlinear
                        fullness for deeper levels (they trigger via score).
      readamp_relief    probe count: compacting L0 removes `files` sorted runs
                        from every lookup; a deeper level removes one run
                        (partial credit, weighted by fullness).
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

    # Natural capacity units. A flush file is ~one write-buffer, which equals
    # L1's target (= max_bytes_for_level_base) — available as L0's
    # next_level_target_bytes.
    if level == 0:
        flush_size = max(raw["next_level_target_bytes"],
                         bytes_i / max(raw["files"], 1.0), 1.0)
        cap_i = trigger * flush_size
        fullness = _clamp(raw["files"] / trigger)
        inflow_files = raw["bytes_in"] / flush_size
        stall_urgency = _clamp((raw["files"] + inflow_files) / slowdown)
        # Each L0 file is an extra sorted run on every lookup's probe path, so
        # relief scales with the run count rather than with fullness: at
        # trigger=10, two L0 files is 20% "full" but doubles L0's probe cost.
        runs_removed = _clamp(raw["files"] / _PROBE_SATURATION)
    else:
        cap_i = max(raw["target_bytes"], 1.0)
        raw_full = bytes_i / cap_i          # unclamped: deferral pushes past 1
        fullness = _clamp(raw_full)
        stall_urgency = fullness * fullness
        # A level below L0 is one sorted run whatever its size, so compacting
        # it removes at most one probe — but the relief is not just the probe,
        # it is the DATA a traversing read no longer has to merge, and that
        # keeps growing after the level passes its target.
        #
        # This previously read `0.25 * fullness` against a fullness clamped to
        # 1.0, so it was pinned at 0.25 for every state above target. Measured
        # on the 5M run, L1 is above target 63% of the time and reaches 7.68x,
        # so the prior could not distinguish a just-full L1 from one carrying
        # nearly eight times its budget — on the one axis that says compacting
        # it would buy read amplification back. That matters more than the same
        # flaw in Phi: the prior currently dominates Q (mean |0.300| against
        # returns of |0.125|), so it is what the policy actually follows.
        #
        # Deliberately identical at fullness = 1 (both give 0.25), so behaviour
        # at and below target is unchanged and only the overshoot range moves.
        runs_removed = _clamp(raw_full / _DEEP_READ_SATURATION)

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
                 "prev_chosen_action", "dt_discount")

    def __init__(self, level: int, raw: dict, state: np.ndarray,
                 reward: float, components: dict, valid_actions,
                 prior: Optional[np.ndarray], dt_seconds: float,
                 executed_action: Optional[int],
                 prev_chosen_action: Optional[int] = None,
                 dt_discount: float = 0.0):
        self.level = level
        self.raw = raw
        self.state = state
        self.reward = reward
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


class MultiLevelProcessor:
    """Parses v2 messages, encodes per-level states, and computes per-level
    rewards from consecutive observations. Shared across connections."""

    def __init__(self):
        self.scales = _AdaptiveScales()
        self._prev_raw: Dict[int, dict] = {}
        self._prev_potential: Dict[int, float] = {}
        self._steps_since_compaction: Dict[int, int] = {}
        self._standardizers: Dict[int, _RunningStandardizer] = {}
        # Wall-clock of each level's last appearance, so a level that drops out
        # of the message (because it emptied) and comes back is discounted over
        # the real elapsed time rather than over one telemetry window.
        self._last_seen: Dict[int, float] = {}

    # -- parsing --------------------------------------------------------

    def _parse_globals(self, msg: dict) -> dict:
        g = {k: _as_float(msg.get(k, d)) for k, d in GLOBAL_DEFAULTS.items()}
        g["done"] = 1.0 if g["done"] else 0.0
        return g

    def _parse_level(self, entry: dict) -> dict:
        raw = {k: _as_float(entry.get(k, d)) for k, d in LEVEL_DEFAULTS.items()}
        for flag in ("default_needed", "is_last", "prev_action_overridden",
                     "prev_compaction_picked"):
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

        steps_since = self._steps_since_compaction.get(level, 0)
        raw_fullness = self._raw_fullness(raw, g)
        l0_hit_fraction, file_reads_per_op = self._read_fractions(g)
        s.observe("global.file_reads_per_op", file_reads_per_op)

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
            _clamp(steps_since / _STEPS_SINCE_SCALE),
            _clamp(raw["defer_count"] / max(1.0, config.max_defer_steps(level))),
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
        ]
        return np.array(values, dtype=np.float32)

    # -- reward -----------------------------------------------------------

    def _potential(self, raw: dict, g: dict) -> Tuple[float, dict]:
        """Cost potential Phi(s) for one level: what this level currently costs
        the system, in stall risk, read amplification, and space.

        The L0 / deeper-level asymmetry is the physically important part. L0
        holds overlapping sorted runs, so every extra L0 file is probed by
        every lookup. A level below L0 is a single sorted run whatever its
        size, so holding it back does not add a probe.

        It does, however, add BYTES to a probe that already happens. A range
        scan opens a merging iterator across every level and has to merge
        whatever each one holds inside its key range, so a level sitting at
        3x its target makes every overlapping scan do roughly 3x that level's
        share of the work. That is a read cost, it is proportional to how full
        the level is, and it was previously priced at exactly zero for every
        level below L0.

        Measured consequence (5M balanced, 10 repeats): against leveled, the
        RL arm carried 0.70 vs 0.52 L0 runs and 3.23 vs 2.80 non-empty deep
        levels — 3.93 vs 3.33 probe units, +18%, against a measured +27% scan
        latency. Only the +0.18 at L0 was priced; the +0.43 at depth, which is
        72% of the excess, was invisible to the reward.

        Read cost is weighted by `read_exposure` at every level, so it is the
        term that vanishes on a write-only phase while stall and space remain.
        That weighting is what keeps it distinct from `space_overshoot`, which
        charges for bytes on disk whether or not anybody reads them, and only
        past the level's target.
        """
        level = int(raw["level"])
        raw_fullness = self._raw_fullness(raw, g)
        exposure = read_exposure(level, g)

        if level == 0:
            stall_risk = _clamp(
                (raw["files"] / max(1.0, g["l0_slowdown_trigger"])) ** 2)
            # Overlapping runs: the cost is the run count itself.
            read_amp = exposure * _clamp(
                raw["files"] / max(1.0, g["l0_compaction_trigger"]))
        else:
            stall_risk = _clamp(raw_fullness ** 2 / 4.0)
            # One sorted run, so the run count contributes nothing that varies
            # with the action; what varies is how much data a traversing read
            # has to merge.
            read_amp = exposure * _clamp(raw_fullness / _DEEP_READ_SATURATION)
        space_overshoot = _clamp(_positive(raw_fullness - 1.0))

        phi = (
            config.reward_weight("POTENTIAL_STALL", level) * stall_risk
            + config.reward_weight("POTENTIAL_READ", level) * read_amp
            + config.reward_weight("POTENTIAL_SPACE", level) * space_overshoot
        )
        return phi, {
            "phi": phi,
            "stall_risk": stall_risk,
            "read_amp": read_amp,
            "space_overshoot": space_overshoot,
        }

    def _compute_reward(self, raw: dict, g: dict,
                        stall_share: float) -> Tuple[float, dict]:
        """Potential difference plus time-integrated costs.

        r = -(Phi(s') - Phi(s)) - dt * (io + stalls + realized read amp)

        Potential-based shaping leaves the optimal policy unchanged while
        centring the signal near zero, which is what the old formulation —
        eleven always-on penalties summed and clamped to [-1, 1] — could not
        do: it was a near-constant negative offset whose clamp saturated in
        exactly the high-pressure states that mattered.

        The `dt` factor on the cost half is what makes a return independent of
        how fast decisions happen to arrive. The two halves are different kinds
        of quantity:

          * -(Phi(s') - Phi(s)) is a *difference*. Summed across a credit
            window it telescopes to Phi(start) - Phi(end), so its magnitude is
            bounded by the range of Phi no matter how many decisions the window
            contains.
          * io / stall / read-amp are *rates and state quantities* — they
            describe the system at an instant, not an amount accrued. Summing
            them over n decisions therefore grows linearly in n.

        Decisions do not arrive at a fixed cadence: measured over a 5M run the
        gap between them ranged 0.050s (p10) to 0.551s (p99) around a 0.129s
        mean, so a fixed 4000ms credit window contained anywhere from 7 to 80
        rewards. The cost half was being multiplied by that count, which made
        the regression target vary more than 10x within a single run for
        reasons that had nothing to do with the policy. Measured consequence:
        finalized returns averaged |9.46| against an analytic prior of |0.284|,
        so the prior contributed ~3% of Q, and the TD loss diverged within the
        run on L0, L2 and L3.

        SMDP discounting does not absorb this. GAMMA_PER_SEC=0.95 has a time
        constant of ~19.5s, so across a 4s window the discount only reaches
        0.81 and the sum still grows nearly linearly in the step count.

        Multiplying by dt turns that sum into a Riemann approximation of
        the integral of cost over the window, which depends on the window's
        duration (fixed, 4s) rather than on how finely it was sampled.
        """
        level = int(raw["level"])
        if config.REWARD_LEGACY:
            return self._compute_reward_legacy(raw, g)

        if g["done"]:
            # The terminal message is a synthetic marker: the picker sends
            # zeroed level states (it has no VersionStorageInfo to read in its
            # destructor). Running the potential difference against it makes
            # Phi collapse to zero, which reads as a huge burst of relief —
            # measured at +2.1 on L1, larger than the 95th percentile of every
            # real reward in the run. Because `done` finalises every open
            # credit window, that fiction was being paid to the last few
            # decisions of every agent. No interval elapsed, so no reward.
            return 0.0, {"terminal": 1.0}

        phi, phi_terms = self._potential(raw, g)
        prev_phi = self._prev_potential.get(level)
        if prev_phi is None:
            self._prev_potential[level] = phi
            return 0.0, {"initial_observation": 1.0, **phi_terms}
        self._prev_potential[level] = phi

        dt = self._dt_seconds(g)
        io_rate = self._rate(raw["bytes_read_out"] + raw["bytes_written_out"], dt)
        io = self.scales.normalize(f"l{level}.io_rate", io_rate)

        stall = 1.0 if g["stall_count"] > 0 else 0.0
        stop = 1.0 if g["stop_count"] > 0 else 0.0

        # Realised read-amplification cost, charged only where deferring can
        # actually create it.
        #
        # L0 holds overlapping runs, so every extra file is one more probe on
        # every lookup: the cost is linear in the RUN COUNT, not in fullness.
        # At trigger 10, two L0 files is 20% "full" but doubles L0's probe
        # cost, which is why this uses the same saturation constant as the
        # analytic prior rather than _fullness().
        #
        # A level below L0 is a single sorted run, so it adds no probe — but a
        # read that reaches it still merges whatever it holds, so the charge
        # scales with how full it is rather than being zero. Charging zero here
        # is what left 72% of the measured excess probe cost unpriced.
        #
        # This is NOT the old `non_last_read_fraction * fullness`, which
        # multiplied fullness by a quantity measured at a constant 0.978 and so
        # was a second space penalty wearing a read-amp label. The exposure
        # weighting is real and workload-dependent: it goes to zero on a
        # write-only phase, where space and stall costs remain.
        exposure = read_exposure(level, g)
        if level == 0:
            read_amp_cost = exposure * _clamp(raw["files"] / _PROBE_SATURATION)
        else:
            read_amp_cost = exposure * _clamp(
                self._raw_fullness(raw, g) / _DEEP_READ_SATURATION)

        # Retained safety signal, re-keyed from `default_needed` (RocksDB's own
        # trigger, which made the agent imitate the baseline) to an observed
        # stall while this level was being deferred.
        late = 1.0 if (stall or stop) and raw["defer_count"] > 0 else 0.0

        # Cost *rate*: what this level is costing the system per second, right
        # now. Integrated over the interval it covers, below.
        cost_rate = (
            config.reward_weight("COST_IO", level) * io
            + config.reward_weight("COST_STALL", level) * stall * stall_share
            + config.reward_weight("COST_STOP", level) * stop * stall_share
            + config.reward_weight("COST_READ_AMP", level) * read_amp_cost
            + config.reward_weight("LATE_NO_COMPACTION", level) * late
        )
        # A zero interval (first sample, or telemetry without interval_micros)
        # means no time passed, so no cost accrued — the shaping term still
        # applies because a state change was observed.
        shaping = -(phi - prev_phi)
        reward = shaping - cost_rate * dt

        # Read-path audit trail. The globals were never logged anywhere, so the
        # fact that `non_last_read_fraction` had been pinned at 1.00 for entire
        # runs was invisible until the state features were dumped and compared
        # after the fact. These are the derived quantities the read side of the
        # policy actually consumes, recorded next to the reward they produced.
        l0_hit_fraction, file_reads_per_op = self._read_fractions(g)
        read_audit = {
            "read_gets": g["keys_read"],
            "read_seeks": g["seeks"],
            "read_l0_hit_fraction": l0_hit_fraction,
            "read_file_reads_per_op": file_reads_per_op,
            "read_exposure_level": read_exposure(level, g),
        }

        components = {
            **phi_terms,
            **read_audit,
            "delta_phi": phi - prev_phi,
            "shaping": shaping,
            "compaction_io": io,
            "stall": stall,
            "stop": stop,
            "stall_share": stall_share,
            "read_amp_cost": read_amp_cost,
            "late_no_compaction": late,
            "cost_rate": cost_rate,
            "cost_integrated": cost_rate * dt,
            "reward_dt": dt,
            "reward_raw": reward,
        }

        if config.REWARD_STANDARDIZE:
            std = self._standardizers.setdefault(level, _RunningStandardizer())
            reward = std.apply(reward)
            components["reward_standardized"] = reward
        return reward, components

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
    def _stall_shares(levels: List[dict], g: dict) -> Dict[int, float]:
        """Split the global stall/stop penalty across levels.

        A stall is caused by whoever deferred; charging every agent the full
        global penalty (the previous behaviour) means each level's reward is
        dominated by a term its own action barely influences. Levels that
        deferred take the blame in proportion; with no deferrals at all, fall
        back to fullness weighting.
        """
        defers = {int(r["level"]): _positive(r["defer_count"]) for r in levels}
        total = sum(defers.values())
        if total > 0:
            return {lvl: d / total for lvl, d in defers.items()}

        fullness = {
            int(r["level"]): _positive(
                MultiLevelProcessor._raw_fullness(r, g)) for r in levels
        }
        total_fullness = sum(fullness.values())
        if total_fullness <= 0:
            # Nothing is under pressure anywhere, so no level is to blame.
            # Splitting the penalty equally would charge idle levels for a
            # stall they could not have caused.
            return {lvl: 0.0 for lvl in fullness}
        return {lvl: f / total_fullness for lvl, f in fullness.items()}

    # -- public API -------------------------------------------------------

    def process(self, msg: dict) -> List[LevelDecision]:
        """Parse a v2 message into per-level decisions, in request order."""
        g = self._parse_globals(msg)
        self.scales.tick()
        dt = self._dt_seconds(g)
        now = time.monotonic()

        parsed = []
        for entry in msg.get("levels", []):
            if not isinstance(entry, dict):
                continue
            parsed.append(self._parse_level(entry))
        shares = self._stall_shares(parsed, g)

        decisions: List[LevelDecision] = []
        for raw in parsed:
            level = int(raw["level"])
            state = self._encode(raw, g)
            reward, components = self._compute_reward(
                raw, g, shares.get(level, 0.0))

            if raw["compactions_from"] > 0 or raw["compactions_scheduled"] > 0:
                self._steps_since_compaction[level] = 0
            else:
                self._steps_since_compaction[level] = (
                    self._steps_since_compaction.get(level, 0) + 1)

            # compact_now stays available for a level RocksDB considers due,
            # even below the exploration floor — otherwise the mask would
            # silently remove the agent's ability to agree with the default.
            compact_allowed = raw["files"] > 0 and (
                raw["score"] >= config.ML_MIN_COMPACT_SCORE
                or raw["default_needed"] > 0.0
            )
            valid_actions = (0, 1) if compact_allowed else (0,)

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
                              dt_discount)
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
