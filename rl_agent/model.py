import torch
import torch.nn as nn


class DQN(nn.Module):
    """MLP Q-network.

    With dueling=True the head decomposes into state-value V(s) and advantage
    A(s,a) streams, combined as Q = V + A - mean(A). This stabilizes learning
    when action values are close (common here: most steps, compacting or not
    barely changes immediate outcome), at the cost of a few extra parameters.

    The non-dueling layout (self.net) is kept identical to the original module
    so existing checkpoints still load when dueling is off.
    """

    def __init__(self, state_dim: int = 14, hidden_dim: int = 64,
                 action_dim: int = 2, dueling: bool = False):
        super().__init__()
        self.dueling = dueling
        if dueling:
            self.trunk = nn.Sequential(
                nn.Linear(state_dim, hidden_dim),
                nn.ReLU(),
                nn.Linear(hidden_dim, hidden_dim),
                nn.ReLU(),
            )
            self.value_head = nn.Linear(hidden_dim, 1)
            self.advantage_head = nn.Linear(hidden_dim, action_dim)
        else:
            self.net = nn.Sequential(
                nn.Linear(state_dim, hidden_dim),
                nn.ReLU(),
                nn.Linear(hidden_dim, hidden_dim),
                nn.ReLU(),
                nn.Linear(hidden_dim, action_dim),
            )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.dueling:
            h = self.trunk(x)
            v = self.value_head(h)
            a = self.advantage_head(h)
            return v + a - a.mean(dim=-1, keepdim=True)
        return self.net(x)
