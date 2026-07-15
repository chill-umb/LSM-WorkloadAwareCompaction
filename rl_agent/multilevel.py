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

import os
import threading
from typing import Dict, List, Optional, Tuple

import numpy as np

import config
from agent import DQNAgent


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
    "default_needed": 0.0,
    "is_last": 0.0,
}

# Steps-since-compaction saturates at this many decisions.
_STEPS_SINCE_SCALE = 50.0
# A score below this at decision time marks a triggered compaction as
# "unnecessary" when it produced no relief.
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
    but with caller-chosen keys, so per-level scales don't collide)."""

    def __init__(self, decay: float = config.NORMALIZER_DECAY):
        self.decay = decay
        self.scales: Dict[str, float] = {}

    def observe(self, key: str, value: float) -> None:
        value = _positive(value)
        previous = self.scales.get(key, 1.0)
        self.scales[key] = max(1.0, value, previous * self.decay)

    def normalize(self, key: str, value: float) -> float:
        return _clamp(value / max(1.0, self.scales.get(key, 1.0)))


class LevelDecision:
    """One level's processed slice of a v2 message."""

    __slots__ = ("level", "raw", "state", "reward", "components")

    def __init__(self, level: int, raw: dict, state: np.ndarray,
                 reward: float, components: dict):
        self.level = level
        self.raw = raw
        self.state = state
        self.reward = reward
        self.components = components


class MultiLevelProcessor:
    """Per-connection: parses v2 messages, encodes per-level states, and
    computes per-level rewards from consecutive observations."""

    def __init__(self):
        self.scales = _AdaptiveScales()
        self._prev_raw: Dict[int, dict] = {}
        self._prev_action: Dict[int, int] = {}
        self._steps_since_compaction: Dict[int, int] = {}

    # -- parsing --------------------------------------------------------

    def _parse_globals(self, msg: dict) -> dict:
        g = {k: _as_float(msg.get(k, d)) for k, d in GLOBAL_DEFAULTS.items()}
        g["done"] = 1.0 if g["done"] else 0.0
        return g

    def _parse_level(self, entry: dict) -> dict:
        raw = {k: _as_float(entry.get(k, d)) for k, d in LEVEL_DEFAULTS.items()}
        raw["default_needed"] = 1.0 if raw["default_needed"] else 0.0
        raw["is_last"] = 1.0 if raw["is_last"] else 0.0
        return raw

    # -- feature helpers --------------------------------------------------

    @staticmethod
    def _fullness(raw: dict, g: dict) -> float:
        level = int(raw["level"])
        if level == 0:
            return _clamp(raw["files"] / max(1.0, g["l0_compaction_trigger"]))
        return _clamp(raw["bytes"] / max(1.0, raw["target_bytes"]))

    @staticmethod
    def _next_fullness(raw: dict) -> float:
        if raw["is_last"]:
            return 0.0
        return _clamp(raw["next_level_bytes"] / max(1.0, raw["next_level_target_bytes"]))

    # -- encoding ---------------------------------------------------------

    def _encode(self, raw: dict, g: dict) -> np.ndarray:
        level = int(raw["level"])
        key = f"l{level}"
        s = self.scales
        s.observe(f"{key}.files", raw["files"])
        s.observe(f"{key}.bytes", raw["bytes"])
        s.observe(f"{key}.bytes_in", raw["bytes_in"])
        bytes_out = raw["bytes_read_out"] + raw["bytes_written_out"]
        s.observe(f"{key}.bytes_out", bytes_out)
        events = raw["compactions_from"] + raw["compactions_scheduled"]
        s.observe(f"{key}.events", events)
        s.observe(f"{key}.next_files", raw["next_level_files"])
        s.observe(f"{key}.overlap", raw["overlap_bytes"])
        s.observe("global.pending", g["pending_compaction_bytes"])

        steps_since = self._steps_since_compaction.get(level, 0)
        is_l0 = level == 0

        values: List[float] = [
            _clamp(raw["score"] / 2.0),
            self._fullness(raw, g),
            s.normalize(f"{key}.files", raw["files"]),
            s.normalize(f"{key}.bytes", raw["bytes"]),
            s.normalize(f"{key}.bytes_in", raw["bytes_in"]),
            s.normalize(f"{key}.bytes_out", bytes_out),
            s.normalize(f"{key}.events", events),
            _clamp(steps_since / _STEPS_SINCE_SCALE),
            self._next_fullness(raw),
            _clamp(raw["next_level_score"] / 2.0),
            s.normalize(f"{key}.next_files", raw["next_level_files"]),
            _clamp(raw["overlap_bytes"] / max(1.0, raw["bytes"]) / 4.0),
            s.normalize(f"{key}.overlap", raw["overlap_bytes"]),
            1.0 if g["stall_count"] > 0 else 0.0,
            1.0 if g["stop_count"] > 0 else 0.0,
            s.normalize("global.pending", g["pending_compaction_bytes"]),
            raw["default_needed"],
            _clamp(raw["files"] / max(1.0, g["l0_slowdown_trigger"])) if is_l0 else 0.0,
            _clamp(raw["files"] / max(1.0, g["l0_stop_trigger"])) if is_l0 else 0.0,
        ]
        return np.array(values, dtype=np.float32)

    # -- reward -----------------------------------------------------------

    def _compute_reward(self, raw: dict, g: dict) -> Tuple[float, dict]:
        level = int(raw["level"])
        prev = self._prev_raw.get(level)
        if prev is None:
            return 0.0, {"initial_observation": 1.0}
        prev_action = self._prev_action.get(level)

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

        bytes_out = raw["bytes_read_out"] + raw["bytes_written_out"]
        io = self.scales.normalize(f"l{level}.bytes_out", bytes_out)
        event = 1.0 if (raw["compactions_from"] > 0
                        or raw["compactions_scheduled"] > 0) else 0.0
        stall = 1.0 if g["stall_count"] > 0 else 0.0
        stop = 1.0 if g["stop_count"] > 0 else 0.0

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

        components = {
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
        return reward, components

    # -- public API -------------------------------------------------------

    def process(self, msg: dict) -> List[LevelDecision]:
        """Parse a v2 message into per-level decisions, in request order."""
        g = self._parse_globals(msg)
        decisions: List[LevelDecision] = []
        for entry in msg.get("levels", []):
            if not isinstance(entry, dict):
                continue
            raw = self._parse_level(entry)
            level = int(raw["level"])
            state = self._encode(raw, g)
            reward, components = self._compute_reward(raw, g)

            if raw["compactions_from"] > 0 or raw["compactions_scheduled"] > 0:
                self._steps_since_compaction[level] = 0
            else:
                self._steps_since_compaction[level] = (
                    self._steps_since_compaction.get(level, 0) + 1)

            decisions.append(LevelDecision(level, raw, state, reward, components))
        return decisions

    def advance(self, decision: LevelDecision, action: int,
                g_pending: float) -> None:
        raw = dict(decision.raw)
        raw["_global_pending"] = g_pending
        self._prev_raw[decision.level] = raw
        self._prev_action[decision.level] = int(action)


def _level_save_path(level: int) -> str:
    root, ext = os.path.splitext(config.MODEL_SAVE_PATH)
    return f"{root}.l{level}{ext or '.pt'}"


class AgentPool:
    """Shared across connections: one lazily-created DQNAgent per level."""

    def __init__(self):
        self._agents: Dict[int, DQNAgent] = {}
        self._lock = threading.Lock()

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
                )
                if os.path.exists(path):
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
