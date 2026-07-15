import random
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import threading
from collections import deque
from typing import Optional

import config
from model import DQN
from replay_buffer import ReplayBuffer


class DQNAgent:
    ACTION_NAMES = config.ACTION_NAMES

    def __init__(self, state_dim: int = None, action_dim: int = None,
                 save_path: str = None, name: str = "dqn"):
        """
        state_dim/action_dim default to the legacy single-level (L0) shapes.
        save_path is where periodic checkpoints go; pass a per-level path when
        the agent belongs to a multi-level pool. name labels the trainer thread.
        """
        self.state_dim = state_dim if state_dim is not None else config.STATE_DIM
        self.action_dim = action_dim if action_dim is not None else config.ACTION_DIM
        self.save_path = save_path if save_path is not None else config.MODEL_SAVE_PATH
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        self.policy_net = DQN(self.state_dim, config.HIDDEN_DIM, self.action_dim).to(self.device)
        self.target_net = DQN(self.state_dim, config.HIDDEN_DIM, self.action_dim).to(self.device)
        self.target_net.load_state_dict(self.policy_net.state_dict())
        self.target_net.eval()

        self.optimizer = optim.Adam(self.policy_net.parameters(), lr=config.LEARNING_RATE)
        self.buffer = ReplayBuffer(config.REPLAY_BUFFER_SIZE)

        self.step: int = 0
        self.epsilon: float = config.EPSILON_START
        self.last_loss: Optional[float] = None
        self.last_q_values: Optional[np.ndarray] = None
        # Diagnostics: n-step return(s) finalized on the most recent observe().
        self.last_returns: list = []

        # Pending decisions awaiting their n-step reward window. Each entry is
        # {"state": s, "action": a, "rewards": [r_0, r_1, ...]} in FIFO order,
        # so the oldest decision always fills its window first.
        self._pending: deque = deque()
        self._lock = threading.RLock()
        self._train_event = threading.Event()
        self._stop_event = threading.Event()
        self._trainer_thread: Optional[threading.Thread] = None

        if config.ASYNC_TRAINING:
            self._trainer_thread = threading.Thread(
                target=self._training_loop,
                daemon=True,
                name=f"{name}-trainer",
            )
            self._trainer_thread.start()

    def _decay_epsilon(self) -> None:
        frac = min(1.0, self.step / config.EPSILON_DECAY_STEPS)
        self.epsilon = config.EPSILON_START + frac * (config.EPSILON_END - config.EPSILON_START)

    def select_action(self, state: np.ndarray) -> int:
        if random.random() < self.epsilon:
            self.last_q_values = None
            return random.randint(0, self.action_dim - 1)
        self.policy_net.eval()
        with torch.no_grad():
            t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
            q_vals = self.policy_net(t).squeeze(0)
            self.last_q_values = q_vals.cpu().numpy()
            return int(q_vals.argmax().item())

    def _nstep_return(self, rewards: list) -> float:
        """Discounted sum of a decision's collected per-step rewards."""
        g = 0.0
        for k, r in enumerate(rewards):
            g += (config.GAMMA ** k) * r
        return g

    def observe(self, state: np.ndarray, reward: float, done: bool) -> int:
        """
        Assemble n-step transitions and return the next action for `state`.

        `reward` is the immediate reward for the interval that just elapsed
        (the outcome of the previous decision's first step). It is added to the
        window of every pending decision; once a decision has collected N_STEP
        rewards, its transition (state, action, n-step-return, bootstrap-state)
        is pushed to the replay buffer. With N_STEP = 1 this reduces exactly to
        the original one-step behavior.

        This method intentionally does not train synchronously. Call
        request_training() after the action response has been sent to RocksDB
        to keep minibatch backpropagation out of the socket response path.
        """
        with self._lock:
            self.last_returns = []

            # 1. This interval's reward extends the window of every pending
            #    decision that has not yet collected N_STEP rewards.
            for d in self._pending:
                if len(d["rewards"]) < config.N_STEP:
                    d["rewards"].append(reward)

            # 2. Finalize decisions whose window is full. `state` is the
            #    bootstrap state x_{t+n} for a decision made at time t.
            while self._pending and len(self._pending[0]["rewards"]) >= config.N_STEP:
                d = self._pending.popleft()
                g = self._nstep_return(d["rewards"])
                self.buffer.push(d["state"], d["action"], g, state, False)
                self.last_returns.append(g)

            # 3. On episode end, `state` is terminal: finalize every pending
            #    decision as a Monte-Carlo return with the bootstrap masked off.
            if done:
                while self._pending:
                    d = self._pending.popleft()
                    g = self._nstep_return(d["rewards"])
                    self.buffer.push(d["state"], d["action"], g, state, True)
                    self.last_returns.append(g)

            # 4. Choose this step's action. Open a reward window for it only if
            #    the episode continues; on a terminal step there is no future to
            #    accumulate, and a dangling decision would otherwise bleed into
            #    the next episode.
            action = self.select_action(state)
            self._decay_epsilon()
            if not done:
                self._pending.append({"state": state.copy(), "action": action, "rewards": []})
            self.step += 1

            if self.step % config.TARGET_UPDATE_INTERVAL == 0:
                self.target_net.load_state_dict(self.policy_net.state_dict())

            if self.step % config.SAVE_INTERVAL == 0:
                self.save(self.save_path)

            return action

    def request_training(self) -> None:
        if config.ASYNC_TRAINING:
            self._train_event.set()
        else:
            with self._lock:
                for _ in range(config.TRAIN_STEPS_PER_OBSERVATION):
                    self._train_step()

    def _training_loop(self) -> None:
        while not self._stop_event.is_set():
            self._train_event.wait(timeout=0.1)
            if self._stop_event.is_set():
                break
            if not self._train_event.is_set():
                continue
            self._train_event.clear()
            with self._lock:
                for _ in range(config.TRAIN_STEPS_PER_OBSERVATION):
                    self._train_step()

    def _train_step(self) -> None:
        if len(self.buffer) < config.MIN_REPLAY_SIZE:
            return

        self.policy_net.train()
        states, actions, rewards, next_states, dones = self.buffer.sample(config.BATCH_SIZE)

        s = torch.FloatTensor(states).to(self.device)
        a = torch.LongTensor(actions).to(self.device)
        r = torch.FloatTensor(rewards).to(self.device)
        s2 = torch.FloatTensor(next_states).to(self.device)
        d = torch.FloatTensor(dones).to(self.device)

        # Q(s, a)
        q_pred = self.policy_net(s).gather(1, a.unsqueeze(1)).squeeze(1)

        # n-step TD target: R^(n) + gamma^n * Q_target(s', a'*) * (1 - done),
        # where s' is the bootstrap state N_STEP decisions later and R^(n) is the
        # discounted reward already accumulated in the stored transition.
        # Double DQN: select a'* with the policy net, evaluate it with the target
        # net to curb overestimation. (Early-terminated transitions carry done=1,
        # which zeroes the bootstrap regardless of the gamma^n factor.)
        with torch.no_grad():
            if config.DOUBLE_DQN:
                next_actions = self.policy_net(s2).argmax(dim=1, keepdim=True)
                q_next = self.target_net(s2).gather(1, next_actions).squeeze(1)
            else:
                q_next = self.target_net(s2).max(dim=1).values
            q_target = r + (config.GAMMA ** config.N_STEP) * q_next * (1.0 - d)

        loss = nn.functional.mse_loss(q_pred, q_target)
        self.optimizer.zero_grad()
        loss.backward()
        # gradient clip for stability
        nn.utils.clip_grad_norm_(self.policy_net.parameters(), max_norm=10.0)
        self.optimizer.step()

        self.last_loss = loss.item()

    def save(self, path: str) -> None:
        with self._lock:
            torch.save(
                {
                    "policy_net": self.policy_net.state_dict(),
                    "target_net": self.target_net.state_dict(),
                    "optimizer": self.optimizer.state_dict(),
                    "step": self.step,
                    "epsilon": self.epsilon,
                },
                path,
            )

    def load(self, path: str) -> None:
        with self._lock:
            ckpt = torch.load(path, map_location=self.device)
            self.policy_net.load_state_dict(ckpt["policy_net"])
            self.target_net.load_state_dict(ckpt["target_net"])
            self.optimizer.load_state_dict(ckpt["optimizer"])
            self.step = ckpt["step"]
            self.epsilon = ckpt["epsilon"]

    def close(self) -> None:
        self._stop_event.set()
        self._train_event.set()
        if self._trainer_thread is not None:
            self._trainer_thread.join(timeout=2.0)
