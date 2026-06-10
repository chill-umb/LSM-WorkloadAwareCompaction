import random
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import threading
from typing import Optional

import config
from model import DQN
from replay_buffer import ReplayBuffer


class DQNAgent:
    ACTION_NAMES = config.ACTION_NAMES

    def __init__(self):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        self.policy_net = DQN(config.STATE_DIM, config.HIDDEN_DIM, config.ACTION_DIM).to(self.device)
        self.target_net = DQN(config.STATE_DIM, config.HIDDEN_DIM, config.ACTION_DIM).to(self.device)
        self.target_net.load_state_dict(self.policy_net.state_dict())
        self.target_net.eval()

        self.optimizer = optim.Adam(self.policy_net.parameters(), lr=config.LEARNING_RATE)
        self.buffer = ReplayBuffer(config.REPLAY_BUFFER_SIZE)

        self.step: int = 0
        self.epsilon: float = config.EPSILON_START
        self.last_loss: Optional[float] = None
        self.last_q_values: Optional[np.ndarray] = None

        self._prev_state: Optional[np.ndarray] = None
        self._prev_action: Optional[int] = None
        self._lock = threading.RLock()
        self._train_event = threading.Event()
        self._stop_event = threading.Event()
        self._trainer_thread: Optional[threading.Thread] = None

        if config.ASYNC_TRAINING:
            self._trainer_thread = threading.Thread(
                target=self._training_loop,
                daemon=True,
                name="dqn-trainer",
            )
            self._trainer_thread.start()

    def _decay_epsilon(self) -> None:
        frac = min(1.0, self.step / config.EPSILON_DECAY_STEPS)
        self.epsilon = config.EPSILON_START + frac * (config.EPSILON_END - config.EPSILON_START)

    def select_action(self, state: np.ndarray) -> int:
        if random.random() < self.epsilon:
            self.last_q_values = None
            return random.randint(0, config.ACTION_DIM - 1)
        self.policy_net.eval()
        with torch.no_grad():
            t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
            q_vals = self.policy_net(t).squeeze(0)
            self.last_q_values = q_vals.cpu().numpy()
            return int(q_vals.argmax().item())

    def observe(self, state: np.ndarray, reward: float, done: bool) -> int:
        """
        Store the transition (prev_state, prev_action, reward, state) and
        return the next action for `state`.

        This method intentionally does not train synchronously. Call
        request_training() after the action response has been sent to RocksDB
        to keep minibatch backpropagation out of the socket response path.
        """
        with self._lock:
            if self._prev_state is not None and self._prev_action is not None:
                self.buffer.push(self._prev_state, self._prev_action, reward, state, done)

            action = self.select_action(state)
            self._decay_epsilon()

            self._prev_state = state.copy()
            self._prev_action = action
            self.step += 1

            if self.step % config.TARGET_UPDATE_INTERVAL == 0:
                self.target_net.load_state_dict(self.policy_net.state_dict())

            if self.step % config.SAVE_INTERVAL == 0:
                self.save(config.MODEL_SAVE_PATH)

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

        # TD target: r + gamma * max_a' Q_target(s', a') * (1 - done)
        with torch.no_grad():
            q_next = self.target_net(s2).max(dim=1).values
            q_target = r + config.GAMMA * q_next * (1.0 - d)

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
