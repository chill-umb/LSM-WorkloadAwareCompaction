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


class MultiHeadDQN(nn.Module):
    """Shared trunk, one output head per LSM level.

    Why pool. Each level agent sees only its own decisions: measured across
    five 5M runs, that is 550-615 transitions per level for a whole run (and
    110-120 at 1M). An independent network per level therefore fits a Q
    function from a few hundred samples, five times over, while the thing being
    learned — how LSM pressure maps to the value of compacting — is largely the
    same function at every level. Sharing the trunk gives that representation
    every level's experience (~2750 transitions per 5M run instead of ~550),
    and leaves the per-level differences to the heads.

    What stays per level. The heads. Level 0 is genuinely a different regime
    from the rest: its runs overlap, so its files are probed by every lookup
    and its deferral is priced differently. The trunk is deliberately NOT given
    the level index — that is what forces it to learn the level-agnostic part
    and keeps the specialisation in the heads, where it can be inspected.

    Why the output is shared_head + level_head, not just level_head. Every head
    has to be zero-initialised to keep the analytic prior intact at step 0, and
    a zero head maps any trunk output to zero. With per-level heads alone, a
    level that has not yet produced transitions of its own emits exactly zero
    however well the trunk has been trained by the other levels — the trunk is
    shared but its benefit is not, which defeats the purpose. A shared head
    receives gradient from every level's transitions, so a level inherits the
    pooled estimate and its own head only has to learn the difference.

    Prior compatibility. Both heads are zero-initialised by the caller, so
    f_theta(s,a) = 0 at step 0 for every level regardless of what the trunk
    does, and Q(s,a) = b(s,a) — the analytic policy — exactly as with the
    independent networks.
    """

    def __init__(self, state_dim: int, hidden_dim: int, action_dim: int,
                 num_levels: int, dueling: bool = False):
        super().__init__()
        self.dueling = dueling
        self.action_dim = action_dim
        self.num_levels = num_levels
        self.trunk = nn.Sequential(
            nn.Linear(state_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
        )
        # Trained by every level's transitions.
        self.shared_head = nn.Linear(hidden_dim, action_dim)
        # One flat layer holding every level's correction, so a batch mixing
        # levels is one matmul plus a gather rather than a Python loop.
        self.advantage_head = nn.Linear(hidden_dim, num_levels * action_dim)
        if dueling:
            self.shared_value_head = nn.Linear(hidden_dim, 1)
            self.value_head = nn.Linear(hidden_dim, num_levels)

    def forward(self, x: torch.Tensor, levels: torch.Tensor) -> torch.Tensor:
        """x: (B, state_dim); levels: (B,) int64 -> (B, action_dim)."""
        h = self.trunk(x)
        a = self.advantage_head(h).view(-1, self.num_levels, self.action_dim)
        index = levels.view(-1, 1, 1).expand(-1, 1, self.action_dim)
        a = self.shared_head(h) + a.gather(1, index).squeeze(1)
        if not self.dueling:
            return a
        v = self.shared_value_head(h) + self.value_head(h).gather(
            1, levels.view(-1, 1))
        return v + a - a.mean(dim=-1, keepdim=True)


class ParametricCandidateDQN(nn.Module):
    """Protocol-v3 residual scorer over variable candidate sets.

    The state and candidate encoders are shared, while every source level has
    an independent scoring head. There is intentionally no globally shared
    action head: a gradient for L1 cannot directly alter L0's action scores.
    Heads are zero-initialized so the cold-start Q values are exactly the
    analytic prior supplied by the controller.
    """

    def __init__(self, state_dim: int, candidate_dim: int, hidden_dim: int,
                 num_levels: int):
        super().__init__()
        self.num_levels = num_levels
        self.state_encoder = nn.Sequential(
            nn.Linear(state_dim, hidden_dim), nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim), nn.ReLU(),
        )
        self.candidate_encoder = nn.Sequential(
            nn.Linear(candidate_dim, hidden_dim), nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim), nn.ReLU(),
        )
        self.level_heads = nn.ModuleList(
            nn.Linear(2 * hidden_dim, 1) for _ in range(num_levels)
        )
        for head in self.level_heads:
            nn.init.zeros_(head.weight)
            nn.init.zeros_(head.bias)

    def forward(self, state: torch.Tensor, candidates: torch.Tensor) -> torch.Tensor:
        """Score padded actions.

        state: (B, state_dim), candidates: (B, levels, actions, candidate_dim)
        returns residual scores (B, levels, actions). Validity is deliberately
        external so callers can use -inf masks for selection and bootstrap.
        """
        batch, levels, actions, _ = candidates.shape
        if levels > self.num_levels:
            raise ValueError("candidate tensor exceeds configured level heads")
        state_h = self.state_encoder(state)
        candidate_h = self.candidate_encoder(
            candidates.reshape(batch * levels * actions, -1)
        ).reshape(batch, levels, actions, -1)
        outputs = []
        for level in range(levels):
            state_level = state_h[:, None, :].expand(-1, actions, -1)
            joint = torch.cat((state_level, candidate_h[:, level]), dim=-1)
            outputs.append(self.level_heads[level](joint).squeeze(-1))
        return torch.stack(outputs, dim=1)
