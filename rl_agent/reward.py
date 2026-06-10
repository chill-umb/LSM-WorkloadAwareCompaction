from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

import config


RAW_DEFAULTS = {
    "l0_files": 0.0,
    "l0_size_bytes": 0.0,
    "l0_score": 0.0,
    "l0_delay_trigger_count": 0.0,
    "l0_compaction_trigger": 1.0,
    "l0_slowdown_trigger": 1.0,
    "l0_stop_trigger": 1.0,
    "pending_compaction_bytes": 0.0,
    "flushed_bytes": 0.0,
    "compaction_bytes_read": 0.0,
    "compaction_bytes_written": 0.0,
    "compactions_completed": 0.0,
    "l0_compactions_completed": 0.0,
    "l0_compactions_scheduled": 0.0,
    "stall_count": 0.0,
    "stop_count": 0.0,
    "default_l0_compaction_needed": 0.0,
    "done": 0.0,
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _positive(value: float) -> float:
    return max(0.0, value)


def _safe_denominator(value: float) -> float:
    return max(1.0, value)


def _as_float(value) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class AdaptiveNormalizer:
    decay: float = config.NORMALIZER_DECAY
    scales: Dict[str, float] = field(default_factory=dict)

    def update(self, raw: Dict[str, float]) -> None:
        l0_floor = max(
            raw["l0_compaction_trigger"],
            raw["l0_slowdown_trigger"],
            raw["l0_stop_trigger"],
            raw["l0_files"],
            1.0,
        )
        self._update_scale("l0_files", l0_floor)
        self._update_scale("l0_size_bytes", raw["l0_size_bytes"])
        self._update_scale("pending_compaction_bytes", raw["pending_compaction_bytes"])
        self._update_scale("flushed_bytes", raw["flushed_bytes"])
        self._update_scale("compaction_bytes_read", raw["compaction_bytes_read"])
        self._update_scale("compaction_bytes_written", raw["compaction_bytes_written"])
        self._update_scale(
            "compaction_bytes_total",
            raw["compaction_bytes_read"] + raw["compaction_bytes_written"],
        )
        self._update_scale(
            "l0_compaction_events",
            raw["l0_compactions_completed"] + raw["l0_compactions_scheduled"],
        )

    def normalize(self, key: str, value: float) -> float:
        return _clamp(value / _safe_denominator(self.scales.get(key, 1.0)))

    def _update_scale(self, key: str, value: float) -> None:
        value = _positive(value)
        previous = self.scales.get(key, 1.0)
        self.scales[key] = max(1.0, value, previous * self.decay)


class StateRewardProcessor:
    def __init__(self):
        self.normalizer = AdaptiveNormalizer()
        self._prev_raw: Optional[Dict[str, float]] = None
        self._prev_action: Optional[int] = None

    def process(
        self, msg: Dict[str, object]
    ) -> Tuple[Dict[str, float], np.ndarray, float, Dict[str, float]]:
        raw = self._parse_raw(msg)
        self.normalizer.update(raw)
        state = self._encode(raw)
        reward, components = self._compute_reward(raw)
        return raw, state, reward, components

    def advance(self, raw: Dict[str, float], action: int) -> None:
        self._prev_raw = dict(raw)
        self._prev_action = int(action)

    def _parse_raw(self, msg: Dict[str, object]) -> Dict[str, float]:
        raw = {key: _as_float(msg.get(key, default)) for key, default in RAW_DEFAULTS.items()}
        raw["default_l0_compaction_needed"] = 1.0 if raw["default_l0_compaction_needed"] else 0.0
        raw["done"] = 1.0 if raw["done"] else 0.0
        return raw

    def _encode(self, raw: Dict[str, float]) -> np.ndarray:
        trigger = _safe_denominator(raw["l0_compaction_trigger"])
        slowdown = _safe_denominator(raw["l0_slowdown_trigger"])
        stop = _safe_denominator(raw["l0_stop_trigger"])
        l0_events = raw["l0_compactions_completed"] + raw["l0_compactions_scheduled"]
        values: List[float] = [
            self.normalizer.normalize("l0_files", raw["l0_files"]),
            self.normalizer.normalize("l0_size_bytes", raw["l0_size_bytes"]),
            _clamp(raw["l0_score"]),
            _clamp(raw["l0_delay_trigger_count"] / stop),
            _clamp(raw["l0_files"] / trigger),
            _clamp(raw["l0_files"] / slowdown),
            _clamp(raw["l0_files"] / stop),
            self.normalizer.normalize(
                "pending_compaction_bytes", raw["pending_compaction_bytes"]
            ),
            self.normalizer.normalize("flushed_bytes", raw["flushed_bytes"]),
            self.normalizer.normalize("compaction_bytes_read", raw["compaction_bytes_read"]),
            self.normalizer.normalize(
                "compaction_bytes_written", raw["compaction_bytes_written"]
            ),
            self.normalizer.normalize("l0_compaction_events", l0_events),
            1.0 if raw["stall_count"] > 0 or raw["stop_count"] > 0 else 0.0,
            raw["default_l0_compaction_needed"],
        ]
        return np.array(values, dtype=np.float32)

    def _compute_reward(self, raw: Dict[str, float]) -> Tuple[float, Dict[str, float]]:
        if self._prev_raw is None:
            return 0.0, {"initial_observation": 1.0}

        prev = self._prev_raw
        prev_action = self._prev_action
        trigger = _safe_denominator(raw["l0_compaction_trigger"])
        slowdown = _safe_denominator(raw["l0_slowdown_trigger"])
        stop = _safe_denominator(raw["l0_stop_trigger"])

        l0_growth = _clamp(_positive(raw["l0_files"] - prev["l0_files"]) / stop)
        l0_relief = _clamp(_positive(prev["l0_files"] - raw["l0_files"]) / stop)
        pending_growth = self.normalizer.normalize(
            "pending_compaction_bytes",
            _positive(raw["pending_compaction_bytes"] - prev["pending_compaction_bytes"]),
        )
        pending_relief = self.normalizer.normalize(
            "pending_compaction_bytes",
            _positive(prev["pending_compaction_bytes"] - raw["pending_compaction_bytes"]),
        )
        l0_pressure = _clamp(raw["l0_files"] / trigger)
        slowdown_pressure = _clamp(raw["l0_files"] / slowdown)
        stop_pressure = _clamp(raw["l0_files"] / stop)
        pending_pressure = self.normalizer.normalize(
            "pending_compaction_bytes", raw["pending_compaction_bytes"]
        )
        compaction_io = self.normalizer.normalize(
            "compaction_bytes_total",
            raw["compaction_bytes_read"] + raw["compaction_bytes_written"],
        )
        compaction_event = 1.0 if (
            raw["l0_compactions_completed"] > 0 or raw["l0_compactions_scheduled"] > 0
        ) else 0.0
        stall = 1.0 if raw["stall_count"] > 0 else 0.0
        stop_event = 1.0 if raw["stop_count"] > 0 else 0.0

        unnecessary_compaction = 0.0
        if prev_action == 1 and prev["l0_files"] < prev["l0_compaction_trigger"] and l0_relief == 0.0:
            unnecessary_compaction = 1.0

        late_no_compaction = 0.0
        if prev_action == 0 and (raw["default_l0_compaction_needed"] > 0.0 or stall or stop_event):
            late_no_compaction = 1.0

        pressure_relief = _clamp((l0_relief + pending_relief) / 2.0)
        reward = (
            -config.REWARD_L0_PRESSURE * l0_pressure
            - config.REWARD_SLOWDOWN_PRESSURE * slowdown_pressure
            - config.REWARD_STOP_PRESSURE * stop_pressure
            - config.REWARD_L0_GROWTH * l0_growth
            - config.REWARD_PENDING_PRESSURE * pending_pressure
            - config.REWARD_PENDING_GROWTH * pending_growth
            - config.REWARD_STALL * stall
            - config.REWARD_STOP * stop_event
            - config.REWARD_COMPACTION_IO * compaction_io
            - config.REWARD_COMPACTION_EVENT * compaction_event
            - config.REWARD_UNNECESSARY_COMPACTION * unnecessary_compaction
            - config.REWARD_LATE_NO_COMPACTION * late_no_compaction
            + config.REWARD_PRESSURE_RELIEF * pressure_relief
        )
        reward = _clamp(reward, -1.0, 1.0)

        components = {
            "l0_pressure": l0_pressure,
            "slowdown_pressure": slowdown_pressure,
            "stop_pressure": stop_pressure,
            "l0_growth": l0_growth,
            "l0_relief": l0_relief,
            "pending_pressure": pending_pressure,
            "pending_growth": pending_growth,
            "pending_relief": pending_relief,
            "compaction_io": compaction_io,
            "compaction_event": compaction_event,
            "stall": stall,
            "stop": stop_event,
            "unnecessary_compaction": unnecessary_compaction,
            "late_no_compaction": late_no_compaction,
            "pressure_relief": pressure_relief,
        }
        return reward, components
