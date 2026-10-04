"""Masked double DQN per agent (PATHWAYS H §4; plan §4 agent.py):

    y = r + gamma Q_target(s', argmax_{a' in A(s')} Q_online(s', a')),
    Q(s, a) = -b(s, a) + f(s)[a],  gamma = exp(-dN / (n_H N_j)),

with Huber loss, a target network and gradient clipping. The argmax is taken
only over the actions allowed in s' (Proposition H.2; the audit counts any
breach, ARCH-2). The reward carries the neighbour charge (H §3), computed
when a transition is sampled, from the neighbours' current target networks.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field

import torch
import torch.nn.functional as F_

from .logs import key_agent_level
from .model import QNet
from .reward import NeighbourCase
from .weights import N_ACTIONS, WeightsFile

ECHO_VARIANTS = ("own", "full")


@dataclass
class Transition:
    key: str
    s: list
    a: int
    b: list
    s2: list
    mask2: list
    b2: list
    dn: float
    n_ops: float      # N_j, operations per turnover
    c_bytes: float    # C_j, the divisor
    cost: float       # c^beta_i in money
    neighbours: list = field(default_factory=list)


@dataclass(frozen=True)
class Settings:
    n_h: float            # discount horizon in turnovers (G-i)
    hidden: int
    layers: int
    clip: float
    lr: float
    batch: int
    target_every: int     # gradient steps between target copies
    huber: float
    grad_clip: float
    replay: int           # transitions kept per model
    min_replay: int       # transitions before a model trains
    echo: str             # "own" or "full" (H §3, §0.6 item 11)
    seed: int

    @staticmethod
    def from_dict(d: dict) -> "Settings":
        s = Settings(**{k: d[k] for k in Settings.__dataclass_fields__})
        if s.echo not in ECHO_VARIANTS:
            raise ValueError(f"echo must be one of {ECHO_VARIANTS}")
        if not (s.n_h > 0 and s.hidden > 0 and s.layers >= 1 and s.clip > 0 and
                s.lr > 0 and s.batch > 0 and s.target_every > 0 and
                s.replay >= s.batch and s.min_replay >= s.batch):
            raise ValueError("learner settings out of range")
        return s


class Model:
    def __init__(self, n_in: int, s: Settings):
        self.online = QNet(n_in, s.hidden, s.layers, s.clip)
        self.target = QNet(n_in, s.hidden, s.layers, s.clip)
        self.target.load_state_dict(self.online.state_dict())
        self.opt = torch.optim.Adam(self.online.parameters(), lr=s.lr)
        self.steps = 0


class Learner:
    def __init__(self, settings: Settings, c_w: float):
        self.s = settings
        self.c_w = c_w
        self.models: dict[str, Model] = {}
        self.names: dict[str, list] = {}   # agent -> feature names
        self.replay: dict[str, deque] = {}
        self.rng = random.Random(settings.seed)
        torch.manual_seed(settings.seed)
        self.audit_breaches = 0
        self.version = 0

    # -- data ---------------------------------------------------------------
    def note_names(self, agent: str, names: list) -> None:
        known = self.names.setdefault(agent, list(names))
        if known != list(names):
            raise ValueError(f"the {agent} state changed its inputs")

    def model(self, key: str, n_in: int) -> Model:
        if key not in self.models:
            self.models[key] = Model(n_in, self.s)
        return self.models[key]

    def add(self, t: Transition) -> None:
        self.model(t.key, len(t.s))
        self.replay.setdefault(t.key, deque(maxlen=self.s.replay)).append(t)

    # -- values -------------------------------------------------------------
    @torch.no_grad()
    def neighbour_value(self, case: NeighbourCase, x: list) -> float:
        """V_j(x) in reward units with neighbour j's target network: its
        value head (echo "own"), or max over its allowed actions of
        -b + f (echo "full", with the prior it had at the decision)."""
        model = self.models.get(case.key)
        if self.s.echo == "own":
            if model is None:
                return 0.0
            return float(model.target.v_own(torch.tensor([x]))[0])
        f = [0.0] * N_ACTIONS
        if model is not None:
            f = model.target.f(torch.tensor([x]))[0].tolist()
        return max(-case.prior[a] + f[a] for a in range(N_ACTIONS)
                   if case.mask[a])

    def neighbour_charge(self, t: Transition) -> float:
        """X_{i+1} + X_{i-1} in money; 0 on hold (no cases)."""
        total = 0.0
        for case in t.neighbours:
            if not (case.c_bytes and math.isfinite(case.c_bytes)):
                continue
            total += self.c_w * case.c_bytes * (
                self.neighbour_value(case, case.x_hold) -
                self.neighbour_value(case, case.x_action))
        return total

    def reward(self, t: Transition) -> tuple[float, float]:
        """(r with the neighbour charge, r_own without it)."""
        scale = self.c_w * t.c_bytes
        own = -t.cost / scale
        return own - self.neighbour_charge(t) / scale, own

    # -- training -----------------------------------------------------------
    def ready(self, key: str) -> bool:
        return len(self.replay.get(key, ())) >= self.s.min_replay

    def gamma(self, t: Transition) -> float:
        """exp(-dN / (n_H N_j)): the discount per operation of G-i."""
        return math.exp(-t.dn / (self.s.n_h * t.n_ops))

    @torch.no_grad()
    def targets(self, m: Model, batch: list):
        """(y, y_v, a*) for a batch: the masked double-DQN target, the value
        head's TD target on the attributed cost alone, and the argmax over
        the actions allowed in s'."""
        S2 = torch.tensor([t.s2 for t in batch], dtype=torch.float32)
        B2 = torch.tensor([t.b2 for t in batch], dtype=torch.float32)
        M2 = torch.tensor([t.mask2 for t in batch], dtype=torch.bool)
        gamma = torch.tensor([self.gamma(t) for t in batch])
        rewards = [self.reward(t) for t in batch]
        R = torch.tensor([r for r, _ in rewards])
        R_own = torch.tensor([r for _, r in rewards])
        q2 = (-B2 + m.online.f(S2)).masked_fill(~M2, -math.inf)
        a2 = q2.argmax(dim=1)
        self.audit_breaches += int((~M2.gather(1, a2[:, None])).sum())
        q2_target = (-B2 + m.target.f(S2)).gather(1, a2[:, None])[:, 0]
        return R + gamma * q2_target, R_own + gamma * m.target.v_own(S2), a2

    def train_step(self, key: str) -> dict:
        m = self.models[key]
        batch = self.rng.sample(list(self.replay[key]), self.s.batch)
        S = torch.tensor([t.s for t in batch], dtype=torch.float32)
        A = torch.tensor([t.a for t in batch])
        B = torch.tensor([t.b for t in batch], dtype=torch.float32)
        y, y_v, _ = self.targets(m, batch)
        out = m.online(S)
        q_sa = (-B + out[:, :N_ACTIONS]).gather(1, A[:, None])[:, 0]
        loss_q = F_.huber_loss(q_sa, y, delta=self.s.huber)
        loss_v = F_.huber_loss(out[:, N_ACTIONS], y_v, delta=self.s.huber)
        loss = loss_q + loss_v
        m.opt.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(m.online.parameters(), self.s.grad_clip)
        m.opt.step()
        m.steps += 1
        if m.steps % self.s.target_every == 0:
            m.target.load_state_dict(m.online.state_dict())
        td = (q_sa - y).detach()
        return {"loss": float(loss), "td_abs": float(td.abs().mean()),
                "q_abs_max": float(q_sa.detach().abs().max()),
                "target_mean": float(y.mean()),
                "nan": bool(torch.isnan(loss).item())}

    # -- export and checkpoints ----------------------------------------------
    def export(self) -> WeightsFile:
        self.version += 1
        w = WeightsFile(version=self.version)
        for key, m in sorted(self.models.items()):
            agent, level = key_agent_level(key)
            w.models.append(m.online.export(agent, level, self.names[agent]))
        return w

    def state_dict(self) -> dict:
        return {
            "version": self.version,
            "names": self.names,
            "models": {k: {"online": m.online.state_dict(),
                           "target": m.target.state_dict(),
                           "opt": m.opt.state_dict(), "steps": m.steps,
                           "n_in": m.online.hidden[0].in_features}
                       for k, m in self.models.items()},
            "replay": {k: list(v) for k, v in self.replay.items()},
            "rng": self.rng.getstate(),
        }

    def load_state_dict(self, d: dict) -> None:
        self.version = d["version"]
        self.names = d["names"]
        for key, md in d["models"].items():
            m = self.model(key, md["n_in"])
            m.online.load_state_dict(md["online"])
            m.target.load_state_dict(md["target"])
            m.opt.load_state_dict(md["opt"])
            m.steps = md["steps"]
        for key, items in d["replay"].items():
            self.replay[key] = deque(items, maxlen=self.s.replay)
        self.rng.setstate(d["rng"])
