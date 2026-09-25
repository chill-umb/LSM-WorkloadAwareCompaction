"""Lagrange multipliers of the constrained reward (PREREGISTRATION D-9).

One object per server, shared by the reward -- which moves each multiplier
once per frame on the SIGNED slack of its constraint -- and by the trainer,
which prices every replayed transition with the CURRENT multipliers.

Why a shared object rather than a scalar reward in the buffer. The reward is
r = shaping - objective - sum_x lambda_x * c_x, and lambda moves during the
run. A transition stored as one scalar froze the multipliers of the frame
that produced it, so replay mixed rewards priced under different lambda and
the TD target was inconsistent with the current objective (history 14.20).
The buffer now stores the component vector and the trainer prices it at
sample time, so every sample in a batch is priced under the same lambda.

Why signed. Dual ascent on an inequality constraint g(x) <= 0 is
lambda <- [lambda + eta * g(x)]^+; g is signed, so the multiplier FALLS when
the constraint has slack. The previous form fed the hinge [g]^+ instead, so
every multiplier was a ratchet: in the D-7 arms each one's final value was
its maximum and lambda_space never left its initial value. A ratchet cannot
plateau (Pathway D criterion D-3) except by the violation reaching exactly
zero, and cannot "return to zero" at all.
"""

from __future__ import annotations

import threading
from typing import Dict, Sequence

import numpy as np

import config

# Component order of every reward vector in the system. The scalar reward is
#   r = shaping - objective - sum over CONSTRAINTS of lambda_x * x
# so the pricing vector is [1, -1, -lambda_write, ..., -lambda_stall].
COMPONENTS: Sequence[str] = (
    "shaping", "objective", "write", "space", "latency", "scan", "stall")
CONSTRAINTS: Sequence[str] = ("write", "space", "latency", "scan", "stall")
COMPONENT_COUNT = len(COMPONENTS)
_CONSTRAINT_SLOTS = {name: COMPONENTS.index(name) for name in CONSTRAINTS}


def scalar_components(reward: float) -> np.ndarray:
    """A scalar reward (legacy path, terminal frames) as a component vector:
    it rides in the shaping slot, whose price is always 1."""
    vector = np.zeros(COMPONENT_COUNT, dtype=np.float64)
    vector[0] = float(reward)
    return vector


class LagrangeMultipliers:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: Dict[str, float] = {
            name: float(getattr(config, f"LAMBDA_{name.upper()}_INIT"))
            for name in CONSTRAINTS
        }
        self.updates = 0

    def update(self, name: str, slack: float) -> float:
        """lambda <- clip(lambda + LAMBDA_LR * slack, 0, LAMBDA_MAX), with
        `slack` SIGNED in the constraint's own unit (relative for the ratio
        constraints, absolute for the stall fraction)."""
        slack = float(slack)
        if not np.isfinite(slack):
            slack = 0.0
        # D-10: one step is at most LAMBDA_LR * LAMBDA_SLACK_CLIP, so a
        # run-to-date ratio read off a handful of early frames cannot move a
        # multiplier by tens in a few ticks.
        clip = float(config.LAMBDA_SLACK_CLIP)
        if clip > 0.0:
            slack = max(-clip, min(clip, slack))
        with self._lock:
            value = self._values[name] + config.LAMBDA_LR * slack
            value = min(config.LAMBDA_MAX, max(0.0, value))
            self._values[name] = value
            self.updates += 1
            return value

    def values(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._values)

    def price_vector(self) -> np.ndarray:
        """[1, -1, -lambda_write, -lambda_space, -lambda_latency,
        -lambda_scan, -lambda_stall], aligned with COMPONENTS."""
        with self._lock:
            price = np.empty(COMPONENT_COUNT, dtype=np.float32)
            price[0] = 1.0
            price[1] = -1.0
            for name, slot in _CONSTRAINT_SLOTS.items():
                price[slot] = -self._values[name]
            return price

    def price(self, components: np.ndarray) -> np.ndarray | float:
        """Scalar reward(s) for one component vector or a (B, K) batch."""
        components = np.asarray(components, dtype=np.float32)
        priced = components @ self.price_vector()
        return float(priced) if priced.ndim == 0 else priced


# One per process: the processor updates it, every trainer reads it.
MULTIPLIERS = LagrangeMultipliers()
