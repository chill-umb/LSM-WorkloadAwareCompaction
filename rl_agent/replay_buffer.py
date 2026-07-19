import random
import numpy as np
from collections import deque
from typing import Optional, Tuple


class ReplayBuffer:
    """Experience replay. Transitions optionally carry per-action analytic
    prior vectors b(s,·) and b(s',·) so training can compose
    Q(s,a) = b(s,a) + f_theta(s,a) without recomputing the prior (the prior
    needs raw observables that are not part of the encoded state)."""

    def __init__(self, capacity: int):
        self._buf: deque = deque(maxlen=capacity)

    def push(
        self,
        state: np.ndarray,
        action: int,
        reward: float,
        next_state: np.ndarray,
        done: bool,
        prior: Optional[np.ndarray] = None,
        next_prior: Optional[np.ndarray] = None,
    ) -> None:
        self._buf.append((state, action, reward, next_state, done, prior, next_prior))

    def sample(
        self, batch_size: int
    ) -> Tuple[np.ndarray, ...]:
        batch = random.sample(self._buf, batch_size)
        states, actions, rewards, next_states, dones, priors, next_priors = zip(*batch)
        action_dim_zeros = None

        def stack_priors(vecs):
            nonlocal action_dim_zeros
            out = []
            for v in vecs:
                if v is None:
                    if action_dim_zeros is None:
                        # infer width from any present prior; fall back to 2
                        present = next((p for p in priors + next_priors if p is not None), None)
                        width = len(present) if present is not None else 2
                        action_dim_zeros = np.zeros(width, dtype=np.float32)
                    out.append(action_dim_zeros)
                else:
                    out.append(v)
            return np.array(out, dtype=np.float32)

        return (
            np.array(states, dtype=np.float32),
            np.array(actions, dtype=np.int64),
            np.array(rewards, dtype=np.float32),
            np.array(next_states, dtype=np.float32),
            np.array(dones, dtype=np.float32),
            stack_priors(priors),
            stack_priors(next_priors),
        )

    def __len__(self) -> int:
        return len(self._buf)
