import random
import numpy as np
from collections import deque
from typing import Optional, Tuple


class ReplayBuffer:
    """Uniform replay over 9-tuples.

    Beyond the usual (s, a, r, s', done) each transition carries:

      reward              a COMPONENT VECTOR in lagrange.COMPONENTS order, not
                          a scalar (D-9). The scalar the learner trains on is
                          priced at sample time with the multipliers current
                          THEN, so every sample in a batch is priced under the
                          same objective. Stored scalars froze the multipliers
                          of the frame that produced them, and replay mixed
                          rewards from different lambda epochs.
      prior / next_prior  the analytic bias b(s,.) at the decision state and at
                          the bootstrap state. Q(s,a) = b(s,a) + f_theta(s,a),
                          so the TD target needs the bias at both ends; storing
                          it avoids recomputing raw observables at replay time.
      discount            the accumulated discount for the bootstrap term. With
                          wall-clock (SMDP) discounting this is not gamma^n —
                          decision intervals vary, so each transition carries
                          its own factor.
      level               which LSM level produced the transition. Only used by
                          the shared-trunk learner, which pools every level's
                          experience into one buffer and needs to route each
                          sample to its own output head. With independent
                          per-level networks it is a constant and ignored.
    """

    def __init__(self, capacity: int):
        self._buf: deque = deque(maxlen=capacity)

    def push(
        self,
        state: np.ndarray,
        action: int,
        reward: np.ndarray,
        next_state: np.ndarray,
        done: bool,
        prior: np.ndarray = None,
        next_prior: np.ndarray = None,
        discount: float = 1.0,
        level: int = 0,
    ) -> None:
        self._buf.append(
            (state, action, np.asarray(reward, dtype=np.float32), next_state,
             done, prior, next_prior, discount, level)
        )

    def sample(self, batch_size: int,
               price: Optional[np.ndarray] = None) -> Tuple[np.ndarray, ...]:
        """`price` is lagrange.MULTIPLIERS.price_vector() at sample time; the
        returned rewards are the stored component vectors priced by it."""
        batch = random.sample(self._buf, batch_size)
        (states, actions, rewards, next_states, dones, priors, next_priors,
         discounts, levels) = zip(*batch)

        def _stack(vectors):
            if any(v is None for v in vectors):
                return None
            return np.array(vectors, dtype=np.float32)

        components = np.array(rewards, dtype=np.float32)
        if price is None:
            raise ValueError("replay sampling needs the current price vector")
        priced = components @ np.asarray(price, dtype=np.float32)

        return (
            np.array(states, dtype=np.float32),
            np.array(actions, dtype=np.int64),
            priced.astype(np.float32),
            np.array(next_states, dtype=np.float32),
            np.array(dones, dtype=np.float32),
            _stack(priors),
            _stack(next_priors),
            np.array(discounts, dtype=np.float32),
            np.array(levels, dtype=np.int64),
        )

    def __len__(self) -> int:
        return len(self._buf)


class LevelBufferView:
    """Per-level facade over a shared buffer.

    DQNAgent.observe() calls `self.buffer.push(...)` without knowing whether it
    owns the buffer or shares it. This tags each push with the level so the
    credit-window logic in observe() needs no pooling-specific branch, and
    `len()` reports the POOLED size — the warmup threshold should be reached by
    the pool's experience, not by one level's share of it.
    """

    def __init__(self, buffer: ReplayBuffer, level: int):
        self._buffer = buffer
        self._level = level

    def push(self, *args, **kwargs) -> None:
        kwargs["level"] = self._level
        self._buffer.push(*args, **kwargs)

    def sample(self, batch_size: int, price: Optional[np.ndarray] = None):
        return self._buffer.sample(batch_size, price)

    def __len__(self) -> int:
        return len(self._buffer)
