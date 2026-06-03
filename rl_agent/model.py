import torch
import torch.nn as nn


class DQN(nn.Module):
    def __init__(self, state_dim: int = 6, hidden_dim: int = 32, action_dim: int = 3):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, action_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)
