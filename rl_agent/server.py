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
# Serializes multi-level message handling across client reconnects/connections
# so the shared processor's prev-state bookkeeping stays consistent.
_ml_lock = threading.Lock()


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
    done = bool(msg.get("done", False))
    try:
        g_pending = float(msg.get("pending_compaction_bytes", 0) or 0)
    except (TypeError, ValueError):
        g_pending = 0.0

    actions = []
    handled = []
    # The processor is shared across connections (so reconnects don't reset
    # normalizer scales / prev-state), and _ml_lock keeps its prev-state
    # bookkeeping consistent. It is held only around the processor calls, not
    # around action selection: the forward passes are per-agent work and
    # holding one global lock across all of them made the response latency
    # scale with the number of populated levels.
    with _ml_lock:
        decisions = ml_processor.process(msg)

    for d in decisions:
        agent = pool.get(d.level)
        # dt_discount, not dt_seconds: a level that emptied and came back spans
        # the whole gap, which is what the SMDP discount has to see.
        action = agent.observe(d.state, d.reward, done, d.valid_actions,
                               d.prior, d.dt_discount, d.executed_action)
        actions.append(action)
        handled.append((d, action, agent))

    with _ml_lock:
        for d, action, _agent in handled:
            ml_processor.advance(d, action, g_pending)

    # Compact separators are load-bearing: the C++ ParseIntArrayField needle
    # requires "actions":[ with no whitespace. json.dumps' default ": " made
    # every response unparseable -> silent fallback to leveled (2026-07-19 bug).
    response = json.dumps({"actions": actions}, separators=(",", ":")) + "\n"
    conn.sendall(response.encode("utf-8"))

    # Everything below is off the response path.
    for d, action, agent in handled:
        diagnostics = {
            "level": d.level,
            "n_step": config.N_STEP,
            "credit_horizon_ms": config.CREDIT_HORIZON_MS,
            "credit_lag_s": agent.last_credit_lag,
            "finalized_returns": agent.last_returns,
            "dt_seconds": d.dt_seconds,
            "dt_discount": d.dt_discount,
            "chosen_action": action,
            # executed_action is the outcome of the PREVIOUS decision, so it
            # pairs with prev_chosen_action, not with chosen_action.
            "executed_action": d.executed_action,
            "prev_chosen_action": d.prev_chosen_action,
            "prev_action_overridden": d.raw.get("prev_action_overridden"),
            "defer_count": d.raw.get("defer_count"),
            "analytic_advantage": agent.last_prior_advantage,
            "residual_advantage": agent.last_residual_advantage,
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
            diagnostics=diagnostics,
        )
        agent.request_training()


def handle_client(conn: socket.socket, agent: DQNAgent, tracker: MetricsTracker, io_log,
                  pool: "multilevel.AgentPool" = None,
                  ml_processor: "multilevel.MultiLevelProcessor" = None) -> None:
    """Handle one persistent C++ client connection."""
    buf = ""
    processor = StateRewardProcessor()
    if ml_processor is None:
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

                response = json.dumps({"action": action}, separators=(",", ":")) + "\n"
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
        # A disconnect ends the episode. Without this, every agent's open
        # credit windows are dropped, which on a run this short is a
        # meaningful slice of the collected experience.
        if pool is not None:
            for level in pool.levels():
                pool.get(level).flush_pending()
        agent.flush_pending()
        conn.close()


def run_server(socket_path: str, agent: DQNAgent, tracker: MetricsTracker, io_log,
               pool: "multilevel.AgentPool" = None,
               ml_processor: "multilevel.MultiLevelProcessor" = None) -> None:
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
            args=(conn, agent, tracker, io_log, pool, ml_processor),
            daemon=True,
            name="rl-client",
        )
        t.start()


def main() -> None:
    if config.SEED:
        import random as _random
        import numpy as _np
        import torch as _torch
        _random.seed(config.SEED)
        _np.random.seed(config.SEED)
        _torch.manual_seed(config.SEED)
        print(f"[server] seeded with RL_SEED={config.SEED}")

    agent = DQNAgent()

    # Resuming is opt-in. The headline experiment is a cold online run: the
    # research claim is adaptation with no prior workload knowledge, so loading
    # weights trained on the same workload would answer a different question.
    if (config.RESUME or config.EVAL_MODE) and os.path.exists(config.MODEL_SAVE_PATH):
        try:
            agent.load(config.MODEL_SAVE_PATH)
            print(f"[server] loaded checkpoint from {config.MODEL_SAVE_PATH} (step={agent.step})")
        except Exception as exc:
            print(f"[server] could not load checkpoint: {exc}", file=sys.stderr)

    print(f"[server] exploration={config.EXPLORATION} "
          f"decay_steps={config.EXPLORATION_DECAY_STEPS} "
          f"prior={'on' if config.ANALYTIC_PRIOR else 'off'} "
          f"credit_horizon_ms={config.CREDIT_HORIZON_MS} "
          f"gamma_per_sec={config.GAMMA_PER_SEC} "
          f"eval_mode={config.EVAL_MODE}")

    pool = multilevel.AgentPool()
    # One processor for the whole server lifetime: client reconnects must not
    # reset adaptive normalizer scales or per-level prev-state bookkeeping.
    ml_processor = multilevel.MultiLevelProcessor()
    tracker = MetricsTracker()
    io_log = open(config.IO_LOG_PATH, "a")
    run_server(config.SOCKET_PATH, agent, tracker, io_log, pool, ml_processor)


if __name__ == "__main__":
    main()
