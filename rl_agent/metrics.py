import json
import time
from collections import defaultdict, deque
from typing import Optional
import threading
import config


class MetricsTracker:
    """Tracks per-step metrics and logs them to a JSONL file.

    Rolling windows are kept per level so multi-level runs don't mix levels'
    reward/loss averages. Legacy single-level records use level=None and are
    tracked under the same key space (reported as level "L0" equivalent).
    """

    WINDOW = 100  # rolling window for averages

    def __init__(self):
        self._log_file = open(config.METRICS_LOG_PATH, "a")
        self._rewards = defaultdict(lambda: deque(maxlen=self.WINDOW))
        self._losses = defaultdict(lambda: deque(maxlen=self.WINDOW))
        self._actions = defaultdict(lambda: deque(maxlen=self.WINDOW))
        self._start = time.time()
        self._lock = threading.Lock()

    def record(
        self,
        step: int,
        state: list,
        action: int,
        reward: float,
        epsilon: float,
        loss: Optional[float],
        q_values: Optional[list],
        raw_state: Optional[dict] = None,
        reward_components: Optional[dict] = None,
        done: bool = False,
        level: Optional[int] = None,
    ) -> None:
        with self._lock:
            rewards = self._rewards[level]
            losses = self._losses[level]
            rewards.append(reward)
            self._actions[level].append(action)
            if loss is not None:
                losses.append(loss)

            action_name = config.ACTION_NAMES.get(action, "?")
            field_names = (
                config.ML_STATE_FIELDS if level is not None else config.STATE_FIELDS
            )
            state_features = {
                name: round(float(value), 6)
                for name, value in zip(field_names, state)
            }
            record = {
                "t": round(time.time() - self._start, 3),
                "step": step,
                "level": level,
                "n_step": config.N_STEP,
                "action": action,
                "action_name": action_name,
                "reward": round(reward, 4),
                "epsilon": round(epsilon, 4),
                "loss": round(loss, 6) if loss is not None else None,
                "avg_reward_100": round(sum(rewards) / len(rewards), 4),
                "avg_loss_100": round(sum(losses) / len(losses), 6) if losses else None,
                "q_values": [round(q, 4) for q in q_values] if q_values is not None else None,
                "state_features": state_features,
                "raw_state": raw_state or {},
                "reward_components": reward_components or {},
                "done": done,
            }
            self._log_file.write(json.dumps(record) + "\n")
            self._log_file.flush()

            if step % 50 == 0:
                tag = f"L{level}" if level is not None else "L0*"
                print(
                    f"[step={step:6d}|{tag:<4}] act={action_name:<12} "
                    f"r={reward:+.3f} avg_r={record['avg_reward_100']:+.3f} "
                    f"eps={epsilon:.3f} loss={record['avg_loss_100'] or 'N/A'}"
                )

    def close(self) -> None:
        with self._lock:
            self._log_file.close()
