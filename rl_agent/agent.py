import math
import random
import time
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import threading
from collections import deque
from typing import Optional

import config
from model import DQN, MultiHeadDQN
from replay_buffer import ReplayBuffer, LevelBufferView

# One thread per agent is plenty: the nets are tiny (23->64->64->2) and the
# agents already run concurrently, so intra-op parallelism only adds contention
# and latency on the decision path.
torch.set_num_threads(1)


class SharedTrunk:
    """Trunk, optimizer, replay buffer and trainer thread shared by every level.

    One of these exists per run; each level's DQNAgent borrows it instead of
    building its own network. What that buys is sample efficiency, which is the
    binding constraint here: a level agent sees 550-615 decisions on a 5M run
    (110-120 at 1M), and an independent network per level fits a Q function
    from that alone. Pooling puts every level's transitions through the same
    trunk, so the shared representation trains on ~2750 samples per 5M run.

    Only the trunk is shared. Each level keeps its own output head, its own
    credit windows, and its own exploration schedule.
    """

    def __init__(self, state_dim: int, action_dim: int, num_levels: int,
                 name: str = "pooled"):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.num_levels = num_levels
        self.policy_net = MultiHeadDQN(state_dim, config.HIDDEN_DIM, action_dim,
                                       num_levels, dueling=config.DUELING).to(self.device)
        if config.ANALYTIC_PRIOR:
            DQNAgent._zero_final_layers(self.policy_net)
        self.target_net = MultiHeadDQN(state_dim, config.HIDDEN_DIM, action_dim,
                                       num_levels, dueling=config.DUELING).to(self.device)
        self.target_net.load_state_dict(self.policy_net.state_dict())
        self.target_net.eval()

        self.optimizer = optim.Adam(
            self.policy_net.parameters(),
            lr=config.LEARNING_RATE,
            weight_decay=config.RESIDUAL_WEIGHT_DECAY,
        )
        self.buffer = ReplayBuffer(config.REPLAY_BUFFER_SIZE)

        # Same lock discipline as DQNAgent, but now genuinely shared: every
        # level trains the same parameters, so one net lock protects them all.
        self._data_lock = threading.RLock()
        self._net_lock = threading.RLock()
        self._train_event = threading.Event()
        self._stop_event = threading.Event()
        self._train_idle = threading.Event()
        self._train_idle.set()
        self._started_at = time.monotonic()
        self.first_train_elapsed_seconds: Optional[float] = None
        self.trainer_error: Optional[str] = None
        self.last_loss: Optional[float] = None
        # Gradient steps taken across all levels — the number that says whether
        # pooling actually raised the learning budget.
        self.train_steps: int = 0

        self._trainer_thread: Optional[threading.Thread] = None
        if config.ASYNC_TRAINING and not config.EVAL_MODE:
            self._trainer_thread = threading.Thread(
                target=self._training_loop, daemon=True, name=f"{name}-trainer")
            self._trainer_thread.start()

    def buffer_view(self, level: int) -> LevelBufferView:
        return LevelBufferView(self.buffer, level)

    def request_training(self) -> None:
        if config.EVAL_MODE:
            return
        if config.ASYNC_TRAINING:
            self._train_idle.clear()
            self._train_event.set()
        else:
            for _ in range(config.TRAIN_STEPS_PER_OBSERVATION):
                self.train_step()

    def _training_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                self._train_event.wait(timeout=0.1)
                if self._stop_event.is_set():
                    break
                if not self._train_event.is_set():
                    continue
                self._train_event.clear()
                self._train_idle.clear()
                try:
                    for _ in range(config.TRAIN_STEPS_PER_OBSERVATION):
                        self.train_step()
                finally:
                    self._train_idle.set()
        except Exception as exc:  # noqa: BLE001 - persisted in health summary
            self.trainer_error = f"{type(exc).__name__}: {exc}"
            self._train_idle.set()
            self._stop_event.set()

    def _soft_update(self) -> None:
        tau = config.TARGET_TAU
        for target_param, param in zip(self.target_net.parameters(),
                                       self.policy_net.parameters()):
            target_param.data.mul_(1.0 - tau).add_(param.data, alpha=tau)

    def train_step(self) -> None:
        with self._data_lock:
            if len(self.buffer) < config.MIN_REPLAY_SIZE:
                return
            batch = self.buffer.sample(config.BATCH_SIZE)

        (states, actions, rewards, next_states, dones, priors, next_priors,
         discounts, levels) = batch

        s = torch.FloatTensor(states).to(self.device)
        a = torch.LongTensor(actions).to(self.device)
        r = torch.FloatTensor(rewards).to(self.device)
        s2 = torch.FloatTensor(next_states).to(self.device)
        d = torch.FloatTensor(dones).to(self.device)
        disc = torch.FloatTensor(discounts).to(self.device)
        lv = torch.LongTensor(levels).to(self.device)
        b = None if priors is None else torch.FloatTensor(priors).to(self.device)
        b2 = (None if next_priors is None
              else torch.FloatTensor(next_priors).to(self.device))

        with self._net_lock:
            self.policy_net.train()
            # The batch mixes levels; each sample is routed to its own head,
            # so one gradient step updates the trunk from every level at once.
            q_pred = self.policy_net(s, lv)
            if b is not None:
                q_pred = q_pred + b
            q_pred = q_pred.gather(1, a.unsqueeze(1)).squeeze(1)

            with torch.no_grad():
                q_next_policy = self.policy_net(s2, lv)
                q_next_target = self.target_net(s2, lv)
                if b2 is not None:
                    q_next_policy = q_next_policy + b2
                    q_next_target = q_next_target + b2
                if config.DOUBLE_DQN:
                    next_actions = q_next_policy.argmax(dim=1, keepdim=True)
                    q_next = q_next_target.gather(1, next_actions).squeeze(1)
                else:
                    q_next = q_next_target.max(dim=1).values
                q_target = r + disc * q_next * (1.0 - d)

            loss = nn.functional.mse_loss(q_pred, q_target)
            self.optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(self.policy_net.parameters(), max_norm=10.0)
            self.optimizer.step()
            if config.TARGET_TAU > 0.0:
                self._soft_update()
            self.train_steps += 1
            if self.first_train_elapsed_seconds is None:
                self.first_train_elapsed_seconds = (
                    time.monotonic() - self._started_at
                )

        self.last_loss = loss.item()

    def q_values(self, state: np.ndarray, level: int):
        with self._net_lock:
            self.policy_net.eval()
            with torch.no_grad():
                t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
                lv = torch.LongTensor([level]).to(self.device)
                return self.policy_net(t, lv).squeeze(0).cpu().numpy()

    def state_dict(self) -> dict:
        with self._net_lock:
            return {
                "policy_net": self.policy_net.state_dict(),
                "target_net": self.target_net.state_dict(),
                "optimizer": self.optimizer.state_dict(),
                "train_steps": self.train_steps,
            }

    def load_state_dict(self, ckpt: dict) -> None:
        with self._net_lock:
            self.policy_net.load_state_dict(ckpt["policy_net"])
            self.target_net.load_state_dict(ckpt["target_net"])
            self.optimizer.load_state_dict(ckpt["optimizer"])
            self.train_steps = ckpt.get("train_steps", 0)

    def quiesce(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._train_idle.wait(0.05) and not self._train_event.is_set():
                return True
        return not self._train_event.is_set() and self._train_idle.is_set()

    def health_snapshot(self) -> dict:
        with self._data_lock:
            replay_size = len(self.buffer)
        return {
            "replay_size": replay_size,
            "train_steps": self.train_steps,
            "first_train_elapsed_seconds": self.first_train_elapsed_seconds,
            "trainer_error": self.trainer_error,
            "last_loss": self.last_loss,
        }

    def close(self) -> None:
        self.quiesce()
        self._stop_event.set()
        self._train_event.set()
        if self._trainer_thread is not None:
            self._trainer_thread.join(timeout=2.0)


class DQNAgent:
    ACTION_NAMES = config.ACTION_NAMES

    def __init__(self, state_dim: int = None, action_dim: int = None,
                 save_path: str = None, name: str = "dqn",
                 shared: Optional[SharedTrunk] = None, level: int = 0):
        """
        state_dim/action_dim default to the legacy single-level (L0) shapes.
        save_path is where periodic checkpoints go; pass a per-level path when
        the agent belongs to a multi-level pool. name labels the trainer thread.

        `shared` opts this agent into the pooled learner: it then borrows the
        trunk, optimizer, replay buffer, locks and trainer thread instead of
        building its own, and `level` selects its output head. Everything above
        the network — credit windows, exploration schedule, action masking,
        diagnostics — is unchanged and stays per level.
        """
        self.state_dim = state_dim if state_dim is not None else config.STATE_DIM
        self.action_dim = action_dim if action_dim is not None else config.ACTION_DIM
        self.save_path = save_path if save_path is not None else config.MODEL_SAVE_PATH
        self.shared = shared
        self.level = level
        self.device = (shared.device if shared is not None
                       else torch.device("cuda" if torch.cuda.is_available()
                                         else "cpu"))

        if shared is not None:
            # Borrowed wholesale. The heads were zero-initialised when the
            # shared trunk was built, so this agent's step-0 policy is still
            # the analytic one.
            self.policy_net = shared.policy_net
            self.target_net = shared.target_net
            self.optimizer = shared.optimizer
            self.buffer = shared.buffer_view(level)
        else:
            self.policy_net = DQN(self.state_dim, config.HIDDEN_DIM, self.action_dim,
                                  dueling=config.DUELING).to(self.device)
            if config.ANALYTIC_PRIOR:
                # Zero the final layer(s) so f_theta(s,a) = 0 initially: with the
                # analytic prior enabled, the starting policy IS the analytic
                # policy, and learning only ever adds corrections. This is what
                # gives an online agent competence at step 0 without pre-training.
                self._zero_final_layers(self.policy_net)
            self.target_net = DQN(self.state_dim, config.HIDDEN_DIM, self.action_dim,
                                  dueling=config.DUELING).to(self.device)
            self.target_net.load_state_dict(self.policy_net.state_dict())
            self.target_net.eval()

            self.optimizer = optim.Adam(
                self.policy_net.parameters(),
                lr=config.LEARNING_RATE,
                weight_decay=config.RESIDUAL_WEIGHT_DECAY,
            )
            self.buffer = ReplayBuffer(config.REPLAY_BUFFER_SIZE)

        self.step: int = 0
        # Set on the first decision so the schedule measures time in the run,
        # not time since process start (the server outlives socket connects).
        self._anneal_start: Optional[float] = None
        self.epsilon: float = config.EPSILON_START
        self.temperature: float = config.BOLTZMANN_TEMP_START
        self._last_loss: Optional[float] = None
        self.last_q_values: Optional[np.ndarray] = None
        # Diagnostics: n-step return(s) finalized on the most recent observe();
        # analytic vs residual advantage of the most recent decision (for
        # calibration analysis when the prior is enabled).
        self.last_returns: list = []
        self.last_prior_advantage: Optional[float] = None
        self.last_residual_advantage: Optional[float] = None
        self.last_credit_lag: Optional[float] = None
        self.finalized_transitions: int = 0
        self.invalid_intervals: int = 0
        self.cleared_pending_windows: int = 0
        self.max_abs_residual_advantage: float = 0.0
        self.argmax_flip_count: int = 0
        self.argmax_comparison_count: int = 0
        self.train_steps: int = 0
        self.first_train_elapsed_seconds: Optional[float] = None
        self.trainer_error: Optional[str] = None
        self._started_at = time.monotonic()

        # Pending decisions awaiting their credit window. Each entry holds the
        # decision, its accumulated discounted return, the running discount
        # factor for the bootstrap term, and the wall-clock time it was made.
        self._pending: deque = deque()

        # Two locks, deliberately. observe() only touches bookkeeping, while
        # the trainer only touches the nets; sharing one lock (the previous
        # design) meant a decision response blocked behind a full minibatch
        # backward pass, which defeated the point of async training.
        #
        # Pooled agents take the SHARED locks: every level updates the same
        # parameters, so a private net lock would not actually protect them.
        # The data lock stays shared too, because it also guards the one
        # replay buffer they all push into.
        self._data_lock = (shared._data_lock if shared is not None
                           else threading.RLock())
        self._net_lock = (shared._net_lock if shared is not None
                          else threading.RLock())
        self._train_event = threading.Event()
        self._stop_event = threading.Event()
        self._train_idle = threading.Event()
        self._train_idle.set()
        self._trainer_thread: Optional[threading.Thread] = None

        # One trainer thread per pool, owned by the SharedTrunk. Starting one
        # per level would have five threads contending for the same net lock to
        # update the same weights.
        if (shared is None and config.ASYNC_TRAINING and not config.EVAL_MODE):
            self._trainer_thread = threading.Thread(
                target=self._training_loop,
                daemon=True,
                name=f"{name}-trainer",
            )
            self._trainer_thread.start()

    @staticmethod
    def _zero_final_layers(net) -> None:
        """Zero every output layer so f_theta(s,a) = 0 at step 0.

        Handles both layouts: DQN's plain MLP (whose last Linear is the output)
        and MultiHeadDQN's per-level heads. Zeroing the heads is enough for the
        shared trunk — whatever the trunk computes, a zero head maps it to
        zero, so the pooled agent starts as the analytic policy exactly like
        the independent ones do.
        """
        # Every output layer, by name — MultiHeadDQN has a shared head and a
        # per-level head (plus their value-stream twins under dueling), and
        # missing any one of them would leave f_theta(s,a) != 0 at step 0.
        heads = [module for name, module in net.named_children()
                 if name.endswith("_head")]
        layers = heads if heads else [net.net[-1]]
        for layer in layers:
            nn.init.zeros_(layer.weight)
            nn.init.zeros_(layer.bias)

    @property
    def last_loss(self) -> Optional[float]:
        """Pooled agents report the shared trunk's loss: there is one set of
        weights and one TD error, so a per-level copy would be the same number
        recorded five times under different names."""
        if self.shared is not None:
            return self.shared.last_loss
        return self._last_loss

    @last_loss.setter
    def last_loss(self, value: Optional[float]) -> None:
        self._last_loss = value

    # -- exploration -------------------------------------------------------

    def _anneal(self) -> None:
        """Anneal exploration. Callers must hold _data_lock.

        Wall-clock schedule when RL_EXPLORATION_ANNEAL_SECONDS is positive,
        otherwise the legacy decision-count schedule, retained as an explicit
        ablation rather than as a fallback anyone should rely on.

        Epsilon mode reads its own step schedule so that RL_EPSILON_DECAY_STEPS
        is not silently inert — several scripts still set it, and a knob that
        looks like it is matching exploration to the run budget while doing
        nothing is exactly how the original 2000-step defect survived.
        """
        if config.EXPLORATION_ANNEAL_SECONDS > 0.0:
            now = time.monotonic()
            if self._anneal_start is None:
                self._anneal_start = now
            frac = min(1.0, (now - self._anneal_start)
                       / config.EXPLORATION_ANNEAL_SECONDS)
        else:
            steps = (config.EPSILON_DECAY_STEPS
                     if config.EXPLORATION == "epsilon"
                     else config.EXPLORATION_DECAY_STEPS)
            frac = min(1.0, self.step / max(1, steps))
        self.epsilon = (config.EPSILON_START
                        + frac * (config.EPSILON_END - config.EPSILON_START))
        self.temperature = (config.BOLTZMANN_TEMP_START
                            + frac * (config.BOLTZMANN_TEMP_END
                                      - config.BOLTZMANN_TEMP_START))

    def _composed_q(self, state: np.ndarray, prior: Optional[np.ndarray]):
        """Returns (Q(s,.), f_theta(s,.)) where Q = b + f_theta."""
        if self.shared is not None:
            residual = self.shared.q_values(state, self.level)
        else:
            with self._net_lock:
                self.policy_net.eval()
                with torch.no_grad():
                    t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
                    residual = self.policy_net(t).squeeze(0).cpu().numpy()
        q_vals = residual.copy()
        if prior is not None:
            q_vals = q_vals + np.asarray(prior, dtype=np.float32)
        return q_vals, residual

    def select_action(self, state: np.ndarray, valid_actions=None,
                      prior: Optional[np.ndarray] = None) -> int:
        """Choose an action over the valid set.

        valid_actions: optional sequence of permitted action indices (action
        masking). None means all actions are valid.

        prior: optional per-action analytic bias b(s,.); selection uses
        Q(s,a) = b(s,a) + f_theta(s,a).

        Exploration defaults to Boltzmann sampling over the composed Q-values
        rather than uniform-random epsilon-greedy. Once the agent can defer
        compactions, a random action can degrade the database rather than
        merely waste I/O, so exploration has to be guided — and with the
        analytic prior it is guided by LSM physics from the very first step.
        """
        if valid_actions is None:
            valid_actions = range(self.action_dim)
        valid_actions = list(valid_actions)
        if len(valid_actions) == 1:
            self.last_q_values = None
            return valid_actions[0]

        q_vals, residual = self._composed_q(state, prior)
        self.last_q_values = q_vals
        if prior is not None and self.action_dim >= 2:
            self.last_prior_advantage = float(prior[1] - prior[0])
            self.last_residual_advantage = float(residual[1] - residual[0])
            with self._data_lock:
                self.max_abs_residual_advantage = max(
                    self.max_abs_residual_advantage,
                    abs(self.last_residual_advantage),
                )
                prior_action = max(valid_actions, key=lambda a: float(prior[a]))
                composed_action = max(
                    valid_actions, key=lambda a: float(q_vals[a])
                )
                self.argmax_comparison_count += 1
                if prior_action != composed_action:
                    self.argmax_flip_count += 1

        if config.EVAL_MODE:
            return max(valid_actions, key=lambda a: float(q_vals[a]))

        with self._data_lock:
            epsilon = self.epsilon
            temperature = self.temperature

        if config.EXPLORATION == "boltzmann":
            logits = np.array([q_vals[a] for a in valid_actions],
                              dtype=np.float64) / max(temperature, 1e-3)
            logits -= logits.max()
            weights = np.exp(logits)
            total = weights.sum()
            if not np.isfinite(total) or total <= 0.0:
                return max(valid_actions, key=lambda a: float(q_vals[a]))
            return int(np.random.choice(valid_actions, p=weights / total))

        if random.random() < epsilon:
            self.last_q_values = None
            return random.choice(valid_actions)
        return max(valid_actions, key=lambda a: float(q_vals[a]))

    # -- credit assignment -------------------------------------------------

    @staticmethod
    def _discount(dt_seconds: float) -> float:
        """Discount factor for one transition.

        Decision intervals vary, so a fixed per-step gamma discounts wall-clock
        time inconsistently — the process is a semi-MDP. Expressing the
        discount per second makes the horizon a physical quantity rather than
        an artefact of how often the picker happened to be called.
        """
        if config.GAMMA_PER_SEC > 0.0 and dt_seconds > 0.0:
            return float(config.GAMMA_PER_SEC ** dt_seconds)
        return config.GAMMA

    def _window_complete(self, entry: dict, now: float) -> bool:
        if config.CREDIT_HORIZON_MS > 0:
            return (now - entry["t0"]) * 1000.0 >= config.CREDIT_HORIZON_MS
        return entry["steps"] >= config.N_STEP

    def observe(self, state: np.ndarray, reward: float, done: bool,
                valid_actions=None, prior: Optional[np.ndarray] = None,
                dt_seconds: float = 0.0,
                executed_action: Optional[int] = None,
                transition_valid: bool = True) -> int:
        """
        Assemble transitions and return the next action for `state`.

        `reward` is the immediate reward for the interval that just elapsed
        (the outcome of the previous decision's first step). It extends the
        credit window of every pending decision; once a window is complete the
        transition (state, action, discounted return, bootstrap state) is
        pushed to the replay buffer.

        `executed_action` is what RocksDB actually did with the previous
        decision. It can differ from what was chosen — a safety guard or native
        admission outcome may have overridden it — and Q-learning is off-policy,
        so the transition must be keyed on the action that was executed.
        Otherwise every override becomes a mislabelled sample, and overrides
        cluster in exactly the high-pressure states that matter most.

        This method does not train synchronously. Call request_training() after
        the response has been sent to keep backpropagation off the decision
        path.
        """
        now = time.monotonic()
        with self._data_lock:
            self.last_returns = []

            # Fallback, stale-observation and safety-masked intervals have no
            # attributable counterfactual. Drop every credit window they
            # overlap; retaining even an older n-step window would leak the
            # invalid interval's global reward into replay.
            if not transition_valid:
                self.invalid_intervals += 1
                self.cleared_pending_windows += len(self._pending)
                self._pending.clear()

            # 0. Correct the previous decision's action to what was executed.
            if transition_valid and executed_action is not None and self._pending:
                self._pending[-1]["action"] = int(executed_action)

            # 1. This interval's reward extends every open credit window, with
            #    wall-clock discounting.
            step_discount = self._discount(dt_seconds)
            for entry in self._pending:
                entry["return"] += entry["discount"] * reward
                entry["discount"] *= step_discount
                entry["steps"] += 1

            # 2. Finalize windows that are complete. `state` is the bootstrap
            #    state for those decisions.
            while self._pending and self._window_complete(self._pending[0], now):
                entry = self._pending.popleft()
                self.buffer.push(entry["state"], entry["action"],
                                 entry["return"], state, False,
                                 entry["prior"], prior, entry["discount"])
                self.finalized_transitions += 1
                self.last_returns.append(entry["return"])
                self.last_credit_lag = now - entry["t0"]

            # 3. On episode end every pending decision becomes a Monte-Carlo
            #    return with the bootstrap masked off.
            if done:
                while self._pending:
                    entry = self._pending.popleft()
                    self.buffer.push(entry["state"], entry["action"],
                                     entry["return"], state, True,
                                     entry["prior"], prior, entry["discount"])
                    self.finalized_transitions += 1
                    self.last_returns.append(entry["return"])

            # 4. Choose this step's action and open its window, unless the
            #    episode just ended (a dangling decision would bleed into the
            #    next episode).
            action = self.select_action(state, valid_actions, prior)
            self._anneal()
            if not done:
                self._pending.append({
                    "state": state.copy(),
                    "action": action,
                    "prior": None if prior is None else np.asarray(prior).copy(),
                    "return": 0.0,
                    "discount": 1.0,
                    "steps": 0,
                    "t0": now,
                })
            self.step += 1
            step = self.step

        if config.TARGET_TAU <= 0.0 and step % config.TARGET_UPDATE_INTERVAL == 0:
            with self._net_lock:
                self.target_net.load_state_dict(self.policy_net.state_dict())

        if step % config.SAVE_INTERVAL == 0:
            self.save(self.save_path)

        return action

    def flush_pending(self, state: np.ndarray = None) -> int:
        """Finalize every open credit window as a terminal transition.

        Called when the client disconnects: without it the last decisions of a
        run are silently dropped, which on a short run is a meaningful slice of
        the data.
        """
        with self._data_lock:
            if not self._pending:
                return 0
            flushed = len(self._pending)
            bootstrap = state if state is not None else self._pending[-1]["state"]
            while self._pending:
                entry = self._pending.popleft()
                self.buffer.push(entry["state"], entry["action"],
                                 entry["return"], bootstrap, True,
                                 entry["prior"], None, entry["discount"])
                self.finalized_transitions += 1
            return flushed

    # -- training ----------------------------------------------------------

    def request_training(self) -> None:
        if self.shared is not None:
            # Every level's experience trains the shared trunk, so a decision
            # at any level is a training opportunity for all of them.
            self.shared.request_training()
            return
        if config.EVAL_MODE:
            return
        if config.ASYNC_TRAINING:
            self._train_idle.clear()
            self._train_event.set()
        else:
            for _ in range(config.TRAIN_STEPS_PER_OBSERVATION):
                self._train_step()

    def _training_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                self._train_event.wait(timeout=0.1)
                if self._stop_event.is_set():
                    break
                if not self._train_event.is_set():
                    continue
                self._train_event.clear()
                self._train_idle.clear()
                try:
                    for _ in range(config.TRAIN_STEPS_PER_OBSERVATION):
                        # The net lock is taken per gradient step, not around the
                        # whole batch, so action selection can interleave.
                        self._train_step()
                finally:
                    self._train_idle.set()
        except Exception as exc:  # noqa: BLE001 - persisted in health summary
            self.trainer_error = f"{type(exc).__name__}: {exc}"
            self._train_idle.set()
            self._stop_event.set()

    def _soft_update(self) -> None:
        tau = config.TARGET_TAU
        for target_param, param in zip(self.target_net.parameters(),
                                       self.policy_net.parameters()):
            target_param.data.mul_(1.0 - tau).add_(param.data, alpha=tau)

    def _train_step(self) -> None:
        if self.shared is not None:
            self.shared.train_step()
            return
        with self._data_lock:
            if len(self.buffer) < config.MIN_REPLAY_SIZE:
                return
            batch = self.buffer.sample(config.BATCH_SIZE)

        states, actions, rewards, next_states, dones, priors, next_priors, \
            discounts, _levels = batch

        s = torch.FloatTensor(states).to(self.device)
        a = torch.LongTensor(actions).to(self.device)
        r = torch.FloatTensor(rewards).to(self.device)
        s2 = torch.FloatTensor(next_states).to(self.device)
        d = torch.FloatTensor(dones).to(self.device)
        disc = torch.FloatTensor(discounts).to(self.device)
        b = None if priors is None else torch.FloatTensor(priors).to(self.device)
        b2 = (None if next_priors is None
              else torch.FloatTensor(next_priors).to(self.device))

        with self._net_lock:
            self.policy_net.train()

            # Q(s, a) = b(s, a) + f_theta(s, a)
            q_pred = self.policy_net(s)
            if b is not None:
                q_pred = q_pred + b
            q_pred = q_pred.gather(1, a.unsqueeze(1)).squeeze(1)

            # Target: G + discount * Q_target(s', a'*) * (1 - done), where the
            # discount was accumulated over the transition's real elapsed time.
            # Double DQN: select a'* with the policy net, evaluate it with the
            # target net to curb overestimation.
            with torch.no_grad():
                q_next_policy = self.policy_net(s2)
                q_next_target = self.target_net(s2)
                if b2 is not None:
                    q_next_policy = q_next_policy + b2
                    q_next_target = q_next_target + b2
                if config.DOUBLE_DQN:
                    next_actions = q_next_policy.argmax(dim=1, keepdim=True)
                    q_next = q_next_target.gather(1, next_actions).squeeze(1)
                else:
                    q_next = q_next_target.max(dim=1).values
                q_target = r + disc * q_next * (1.0 - d)

            loss = nn.functional.mse_loss(q_pred, q_target)
            self.optimizer.zero_grad()
            loss.backward()
            # gradient clip for stability
            nn.utils.clip_grad_norm_(self.policy_net.parameters(), max_norm=10.0)
            self.optimizer.step()

            if config.TARGET_TAU > 0.0:
                self._soft_update()

            self.train_steps += 1
            if self.first_train_elapsed_seconds is None:
                self.first_train_elapsed_seconds = (
                    time.monotonic() - self._started_at
                )

        self.last_loss = loss.item()

    # -- persistence -------------------------------------------------------

    # Lock order is always _data_lock then _net_lock (observe() nests them that
    # way); every other path must follow it or the two can deadlock.
    def save(self, path: str) -> None:
        with self._data_lock:
            state = {
                "step": self.step,
                "epsilon": self.epsilon,
                "temperature": self.temperature,
                "level": self.level,
                "shared_trunk": self.shared is not None,
            }
            if self.shared is not None:
                # The trunk and every head are one object, so each level's
                # checkpoint carries the whole thing plus its own bookkeeping.
                # Redundant across levels, but it keeps a checkpoint
                # self-contained and loadable on its own.
                state.update(self.shared.state_dict())
            else:
                with self._net_lock:
                    state["policy_net"] = self.policy_net.state_dict()
                    state["target_net"] = self.target_net.state_dict()
                    state["optimizer"] = self.optimizer.state_dict()
                    state["train_steps"] = self.train_steps
        torch.save(state, path)

    def load(self, path: str) -> None:
        ckpt = torch.load(path, map_location=self.device)
        if bool(ckpt.get("shared_trunk", False)) != (self.shared is not None):
            raise ValueError(
                f"checkpoint {path} was written with shared_trunk="
                f"{ckpt.get('shared_trunk', False)} but this agent has "
                f"shared_trunk={self.shared is not None}; the network shapes "
                f"differ, so loading it would silently mis-map the weights")
        with self._data_lock:
            self.step = ckpt["step"]
            self.epsilon = ckpt["epsilon"]
            self.temperature = ckpt.get("temperature", config.BOLTZMANN_TEMP_END)
            if self.shared is not None:
                self.shared.load_state_dict(ckpt)
            else:
                with self._net_lock:
                    self.policy_net.load_state_dict(ckpt["policy_net"])
                    self.target_net.load_state_dict(ckpt["target_net"])
                    self.optimizer.load_state_dict(ckpt["optimizer"])
                    self.train_steps = int(ckpt.get("train_steps", 0))

    def quiesce(self, timeout: float = 2.0) -> bool:
        if self.shared is not None:
            return self.shared.quiesce(timeout)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._train_idle.wait(0.05) and not self._train_event.is_set():
                return True
        return not self._train_event.is_set() and self._train_idle.is_set()

    def health_snapshot(self) -> dict:
        with self._data_lock:
            return {
                "level": self.level,
                "decisions": self.step,
                "finalized_transitions": self.finalized_transitions,
                "replay_size": len(self.buffer),
                "invalid_intervals": self.invalid_intervals,
                "cleared_pending_windows": self.cleared_pending_windows,
                "pending_windows": len(self._pending),
                "train_steps": (
                    self.shared.train_steps
                    if self.shared is not None
                    else self.train_steps
                ),
                "first_train_elapsed_seconds": (
                    self.shared.first_train_elapsed_seconds
                    if self.shared is not None
                    else self.first_train_elapsed_seconds
                ),
                "trainer_error": (
                    self.shared.trainer_error
                    if self.shared is not None
                    else self.trainer_error
                ),
                "max_abs_residual_advantage": self.max_abs_residual_advantage,
                "argmax_flip_count": self.argmax_flip_count,
                "argmax_comparison_count": self.argmax_comparison_count,
            }

    def close(self) -> None:
        if self.shared is None:
            self.quiesce()
        # A pooled agent has no thread of its own; the SharedTrunk owns the one
        # trainer and AgentPool closes it.
        self._stop_event.set()
        self._train_event.set()
        if self._trainer_thread is not None:
            self._trainer_thread.join(timeout=2.0)
