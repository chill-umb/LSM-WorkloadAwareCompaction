"""The learned residual f_theta of Q = -b + f_theta (PATHWAYS G §3, H §4) as a
small ReLU MLP per agent, plus a value head V_own fitted to the agent's
attributed cost alone (the neighbour charge's no-echo variant, H §3).

With no pool (D-19) every interior level has its own model, so the pooled
form's delta_j head is not used (the weights file can carry one). The output
layer starts at zero, so f = 0 and V_own = 0 at a cold start: Q = -b, the
prior (H §4). Inputs are prepared as mlp.cc prepares them (NaN -> 0, clip).
"""

from __future__ import annotations

import torch
from torch import nn

from .weights import N_ACTIONS, ModelWeights

N_OUT = N_ACTIONS + 1  # f per action, then V_own


class QNet(nn.Module):
    def __init__(self, n_in: int, hidden: int, layers: int, clip: float):
        super().__init__()
        self.clip = float(clip)
        widths = [n_in] + [hidden] * layers
        self.hidden = nn.ModuleList(
            nn.Linear(a, b) for a, b in zip(widths[:-1], widths[1:]))
        self.out = nn.Linear(widths[-1], N_OUT)
        nn.init.zeros_(self.out.weight)
        nn.init.zeros_(self.out.bias)

    def prepare(self, x: torch.Tensor) -> torch.Tensor:
        return torch.nan_to_num(x, nan=0.0, posinf=0.0,
                                neginf=0.0).clamp(-self.clip, self.clip)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.prepare(x)
        for layer in self.hidden:
            h = torch.relu(layer(h))
        return self.out(h)

    def f(self, x: torch.Tensor) -> torch.Tensor:
        return self.forward(x)[..., :N_ACTIONS]

    def v_own(self, x: torch.Tensor) -> torch.Tensor:
        return self.forward(x)[..., N_ACTIONS]

    def export(self, agent: str, level: int, names: list) -> ModelWeights:
        layers = []
        for layer in list(self.hidden) + [self.out]:
            w = layer.weight.detach().cpu().double().tolist()
            b = layer.bias.detach().cpu().double().tolist()
            layers.append((w, b))
        return ModelWeights(agent=agent, level=level, names=list(names),
                            clip=self.clip, layers=layers)
