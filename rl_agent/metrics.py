import json
import time
from collections import deque
from typing import Optional
import threading
import config


class MetricsTracker:
    """Tracks per-step metrics and logs them to a JSONL file."""

    WINDOW = 100  # rolling window for averages

    def __init__(self):
        self._log_file = open(config.METRICS_LOG_PATH, "a")
        self._rewards: deque = deque(maxlen=self.WINDOW)
        self._losses: deque = deque(maxlen=self.WINDOW)
        self._actions: deque = deque(maxlen=self.WINDOW)
        self._l0_files: deque = deque(maxlen=self.WINDOW)
        self._pcb: deque = deque(maxlen=self.WINDOW)
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
        done: bool = False,
    ) -> None:
        with self._lock:
            self._rewards.append(reward)
            self._actions.append(action)
            if loss is not None:
                self._losses.append(loss)
            # state layout: [f0, delta_f0, score0, pcb, stall, bw] (all normalized)
            self._l0_files.append(state[0])
            self._pcb.append(state[3])

            action_name = {0: "do_nothing", 1: "compact_now", 2: "delay"}.get(action, "?")
            record = {
                "t": round(time.time() - self._start, 3),
                "step": step,
                "action": action,
                "action_name": action_name,
                "reward": round(reward, 4),
                "epsilon": round(epsilon, 4),
                "loss": round(loss, 6) if loss is not None else None,
                "avg_reward_100": round(sum(self._rewards) / len(self._rewards), 4),
                "avg_loss_100": round(sum(self._losses) / len(self._losses), 6) if self._losses else None,
                "q_values": [round(q, 4) for q in q_values] if q_values is not None else None,
                "f0_norm": round(state[0], 4),
                "delta_f0_norm": round(state[1], 4),
                "score0_norm": round(state[2], 4),
                "pcb_norm": round(state[3], 4),
                "stall_norm": round(state[4], 4),
                "bw_norm": round(state[5], 4),
                "done": done,
            }
            self._log_file.write(json.dumps(record) + "\n")
            self._log_file.flush()

            if step % 50 == 0:
                print(
                    f"[step={step:6d}] act={action_name:<12} "
                    f"r={reward:+.3f} avg_r={record['avg_reward_100']:+.3f} "
                    f"eps={epsilon:.3f} loss={record['avg_loss_100'] or 'N/A'}"
                )

    def close(self) -> None:
        with self._lock:
            self._log_file.close()
