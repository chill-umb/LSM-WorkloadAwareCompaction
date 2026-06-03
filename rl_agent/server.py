"""
Unix domain socket server — receives compaction state from RocksDB C++ client,
returns RL action, and trains the DQN agent online.

Protocol (newline-delimited JSON):
  Request  (C++ → Python): {"f0":0.25,"df0":0.1,"s0":0.6,"pcb":0.15,"stall":0.0,"bw":0.05,"reward":0.0,"done":false}
  Response (Python → C++): {"action":1}

Actions: 0=do_nothing  1=compact_now  2=delay_one_turn
"""

import json
import os
import signal
import socket
import sys
import threading
import time
from typing import Optional

import numpy as np

import config
from agent import DQNAgent
from metrics import MetricsTracker


def _parse_state(msg: dict) -> np.ndarray:
    return np.array(
        [msg["f0"], msg["df0"], msg["s0"], msg["pcb"], msg["stall"], msg["bw"]],
        dtype=np.float32,
    )


_ACTION_NAMES = {0: "do_nothing", 1: "compact_now", 2: "delay"}
_io_log_lock = threading.Lock()


def _write_io_log(io_log, step: int, msg: dict, action: int) -> None:
    entry = {
        "ts": time.time(),
        "step": step,
        "input": {k: msg[k] for k in ("f0", "df0", "s0", "pcb", "stall", "bw", "reward", "done")},
        "output": {"action": action, "action_name": _ACTION_NAMES.get(action, "unknown")},
    }
    line = json.dumps(entry) + "\n"
    with _io_log_lock:
        io_log.write(line)
        io_log.flush()


def handle_client(conn: socket.socket, agent: DQNAgent, tracker: MetricsTracker, io_log) -> None:
    """Handle one persistent C++ client connection."""
    buf = ""
    try:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk.decode("utf-8", errors="replace")

            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                state = _parse_state(msg)
                reward: float = float(msg.get("reward", 0.0))
                done: bool = bool(msg.get("done", False))

                action = agent.observe(state, reward, done)

                _write_io_log(io_log, agent.step, msg, action)

                q_vals = agent.last_q_values.tolist() if agent.last_q_values is not None else None
                tracker.record(
                    step=agent.step,
                    state=state.tolist(),
                    action=action,
                    reward=reward,
                    epsilon=agent.epsilon,
                    loss=agent.last_loss,
                    q_values=q_vals,
                )

                print(f"[server] step={agent.step} action={_ACTION_NAMES.get(action, action)}")
                response = json.dumps({"action": action}) + "\n"
                conn.sendall(response.encode("utf-8"))
    except Exception as exc:
        print(f"[server] client error: {exc}", file=sys.stderr)
    finally:
        conn.close()


def run_server(socket_path: str, agent: DQNAgent, tracker: MetricsTracker, io_log) -> None:
    if os.path.exists(socket_path):
        os.unlink(socket_path)

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(socket_path)
    srv.listen(4)
    os.chmod(socket_path, 0o600)

    print(f"[server] listening on {socket_path}")
    print(f"[server] I/O log → {config.IO_LOG_PATH}")

    def _shutdown(sig, frame):
        print("[server] shutting down …")
        agent.save(config.MODEL_SAVE_PATH)
        tracker.close()
        io_log.close()
        srv.close()
        try:
            os.unlink(socket_path)
        except OSError:
            pass
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            break
        t = threading.Thread(
            target=handle_client,
            args=(conn, agent, tracker, io_log),
            daemon=True,
            name="rl-client",
        )
        t.start()


def main() -> None:
    agent = DQNAgent()

    if os.path.exists(config.MODEL_SAVE_PATH):
        try:
            agent.load(config.MODEL_SAVE_PATH)
            print(f"[server] loaded checkpoint from {config.MODEL_SAVE_PATH} (step={agent.step})")
        except Exception as exc:
            print(f"[server] could not load checkpoint: {exc}", file=sys.stderr)

    tracker = MetricsTracker()
    io_log = open(config.IO_LOG_PATH, "a")
    run_server(config.SOCKET_PATH, agent, tracker, io_log)


if __name__ == "__main__":
    main()
