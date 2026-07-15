"""
Unix domain socket server — receives compaction state from RocksDB C++ client,
returns RL action(s), and trains the DQN agent(s) online.

Protocol (newline-delimited JSON):
  Legacy (single-level L0):
    Request  (C++ → Python): raw L0 state + telemetry deltas.
    Response (Python → C++): {"action":1}
  v2 (multi-level, one agent per level; detected by a "levels" array):
    Request  (C++ → Python): globals + [{"level":i, ...}, ...].
    Response (Python → C++): {"actions":[a_0, a_1, ...]} in request order.

Actions: 0=do_nothing  1=compact_now
"""

import json
import os
import signal
import socket
import sys
import threading
import time

import config
import multilevel
from agent import DQNAgent
from metrics import MetricsTracker
from reward import StateRewardProcessor


_ACTION_NAMES = config.ACTION_NAMES
_io_log_lock = threading.Lock()


def _write_io_log(
    io_log,
    step: int,
    raw_state: dict,
    reward: float,
    reward_components: dict,
    action: int,
    diagnostics: dict = None,
) -> None:
    entry = {
        "ts": time.time(),
        "step": step,
        "input": raw_state,
        "reward": reward,
        "reward_components": reward_components,
        "output": {"action": action, "action_name": _ACTION_NAMES.get(action, "unknown")},
        "diagnostics": diagnostics or {},
    }
    line = json.dumps(entry) + "\n"
    with _io_log_lock:
        io_log.write(line)
        io_log.flush()


def handle_multilevel_message(
    conn: socket.socket,
    msg: dict,
    ml_processor: "multilevel.MultiLevelProcessor",
    pool: "multilevel.AgentPool",
    tracker: MetricsTracker,
    io_log,
) -> None:
    """Process one v2 (multi-level) message: route each level's slice to its
    agent, reply with per-level actions in request order, then log and train."""
    decisions = ml_processor.process(msg)
    done = bool(msg.get("done", False))
    try:
        g_pending = float(msg.get("pending_compaction_bytes", 0) or 0)
    except (TypeError, ValueError):
        g_pending = 0.0

    actions = []
    handled = []
    for d in decisions:
        agent = pool.get(d.level)
        action = agent.observe(d.state, d.reward, done)
        actions.append(action)
        ml_processor.advance(d, action, g_pending)
        handled.append((d, action, agent))

    response = json.dumps({"actions": actions}) + "\n"
    conn.sendall(response.encode("utf-8"))

    # Everything below is off the response path.
    for d, action, agent in handled:
        diagnostics = {
            "level": d.level,
            "n_step": config.N_STEP,
            "finalized_returns": agent.last_returns,
        }
        _write_io_log(
            io_log, agent.step, {"level": d.level, **d.raw}, d.reward,
            d.components, action, diagnostics
        )
        q_vals = agent.last_q_values.tolist() if agent.last_q_values is not None else None
        tracker.record(
            step=agent.step,
            state=d.state.tolist(),
            action=action,
            reward=d.reward,
            epsilon=agent.epsilon,
            loss=agent.last_loss,
            q_values=q_vals,
            raw_state=d.raw,
            reward_components=d.components,
            done=done,
            level=d.level,
        )
        agent.request_training()


def handle_client(conn: socket.socket, agent: DQNAgent, tracker: MetricsTracker, io_log,
                  pool: "multilevel.AgentPool" = None) -> None:
    """Handle one persistent C++ client connection."""
    buf = ""
    processor = StateRewardProcessor()
    ml_processor = multilevel.MultiLevelProcessor()
    # Diagnostic: how many decisions elapse between a compact_now and the L0
    # compaction completion it triggers. The distribution of this lag tells us
    # how large N_STEP must be for the reward window to actually capture relief.
    last_compact_now_step: int = None
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

                if "levels" in msg and pool is not None:
                    handle_multilevel_message(
                        conn, msg, ml_processor, pool, tracker, io_log
                    )
                    continue

                raw_state, state, reward, reward_components = processor.process(msg)
                done: bool = bool(msg.get("done", False))

                action = agent.observe(state, reward, done)
                processor.advance(raw_state, action)

                response = json.dumps({"action": action}) + "\n"
                conn.sendall(response.encode("utf-8"))

                # Track compact_now -> completion lag for the N_STEP diagnostic.
                completion_lag = None
                if raw_state.get("l0_compactions_completed", 0) > 0 and last_compact_now_step is not None:
                    completion_lag = agent.step - last_compact_now_step
                if action == 1:
                    last_compact_now_step = agent.step
                diagnostics = {
                    "n_step": config.N_STEP,
                    "finalized_returns": agent.last_returns,
                    "completion_lag": completion_lag,
                }

                _write_io_log(
                    io_log, agent.step, raw_state, reward, reward_components, action, diagnostics
                )

                q_vals = agent.last_q_values.tolist() if agent.last_q_values is not None else None
                tracker.record(
                    step=agent.step,
                    state=state.tolist(),
                    action=action,
                    reward=reward,
                    epsilon=agent.epsilon,
                    loss=agent.last_loss,
                    q_values=q_vals,
                    raw_state=raw_state,
                    reward_components=reward_components,
                    done=done,
                )

                agent.request_training()

                print(f"[server] step={agent.step} action={_ACTION_NAMES.get(action, action)}")
    except Exception as exc:
        print(f"[server] client error: {exc}", file=sys.stderr)
    finally:
        conn.close()


def run_server(socket_path: str, agent: DQNAgent, tracker: MetricsTracker, io_log,
               pool: "multilevel.AgentPool" = None) -> None:
    if os.path.exists(socket_path):
        os.unlink(socket_path)

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(socket_path)
    srv.listen(4)
    os.chmod(socket_path, 0o600)

    print(f"[server] listening on {socket_path}")
    print(f"[server] I/O log → {config.IO_LOG_PATH}")

    def _shutdown(sig, frame):
        print("[server] shutting down …")
        agent.close()
        agent.save(config.MODEL_SAVE_PATH)
        if pool is not None:
            pool.close_all()
            pool.save_all()
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
            args=(conn, agent, tracker, io_log, pool),
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

    pool = multilevel.AgentPool()
    tracker = MetricsTracker()
    io_log = open(config.IO_LOG_PATH, "a")
    run_server(config.SOCKET_PATH, agent, tracker, io_log, pool)


if __name__ == "__main__":
    main()
