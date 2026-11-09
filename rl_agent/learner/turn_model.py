"""The turn mode's two value functions in torch (the design's §8 and §9;
controller/turn.h TurnScores runs the exported weights):

* factored, one agent: a shared body feeds V(s) and four advantages per
  acting level, Q(s, a) = V + sum_i (A_i(a_i) - mean of A_i over the allowed
  actions);
* vdn, one agent per acting level: each a small net on its own view
  (turn_weights.vdn_view), Q_tot(s, a) = sum_i Q_i(o_i, a_i).

The output layers start at zero, so every action ties and the plugin holds
(cold start). Inputs reach the nets already scaled and clipped
(scale_inputs), as the plugin scales them."""

from __future__ import annotations

import torch

from . import turn_weights as TW

N_ACTIONS = TW.N_ACTIONS


def _mlp(n_in: int, hidden: int, layers: int, n_out: int) -> torch.nn.Sequential:
    mods, width = [], n_in
    for _ in range(layers):
        mods += [torch.nn.Linear(width, hidden), torch.nn.ReLU()]
        width = hidden
    out = torch.nn.Linear(width, n_out)
    torch.nn.init.zeros_(out.weight)
    torch.nn.init.zeros_(out.bias)
    mods.append(out)
    return torch.nn.Sequential(*mods).double()


def _linears(seq: torch.nn.Sequential) -> list:
    return [(m.weight.detach().double().tolist(), m.bias.detach().double().tolist())
            for m in seq if isinstance(m, torch.nn.Linear)]


class FactoredNet(torch.nn.Module):
    arch = "factored"

    def __init__(self, acting: int, hidden: int = 64, layers: int = 2):
        super().__init__()
        self.acting = acting
        self.n_in = len(TW.feature_names(acting))
        self.net = _mlp(self.n_in, hidden, layers, 1 + N_ACTIONS * acting)

    def heads(self, xs: torch.Tensor):
        """V [B] and the advantages [B, acting, 4]."""
        o = self.net(xs)
        return o[:, 0], o[:, 1:].reshape(-1, self.acting, N_ACTIONS)

    def scores(self, xs: torch.Tensor) -> torch.Tensor:
        """What the plugin's greedy choice compares: the advantages."""
        return self.heads(xs)[1]

    def joint(self, xs: torch.Tensor, actions: torch.Tensor,
              masks: torch.Tensor) -> torch.Tensor:
        """Q(s, a) [B]; actions [B, acting] long, masks [B, acting, 4] bool."""
        v, a = self.heads(xs)
        allowed = masks.double()
        mean = (a * allowed).sum(-1) / allowed.sum(-1).clamp(min=1)
        taken = a.gather(-1, actions.unsqueeze(-1)).squeeze(-1)
        return v + (taken - mean).sum(-1)

    def export_nets(self) -> list:
        return [TW.Net(list(range(self.n_in)), _linears(self.net))]


class VdnNet(torch.nn.Module):
    arch = "vdn"

    def __init__(self, acting: int, hidden: int = 32, layers: int = 2):
        super().__init__()
        self.acting = acting
        self.n_in = len(TW.feature_names(acting))
        self.views = [TW.vdn_view(level, acting) for level in range(acting)]
        # -1 (a zero pad) reads an appended column of zeros.
        for level, view in enumerate(self.views):
            self.register_buffer(
                f"idx{level}",
                torch.tensor([self.n_in if j < 0 else j for j in view], dtype=torch.long))
        self.nets = torch.nn.ModuleList(
            _mlp(len(view), hidden, layers, N_ACTIONS) for view in self.views)

    def scores(self, xs: torch.Tensor) -> torch.Tensor:
        """Each agent's values from its own view, [B, acting, 4]."""
        padded = torch.cat([xs, torch.zeros(xs.shape[0], 1, dtype=xs.dtype)], 1)
        return torch.stack([net(padded[:, getattr(self, f"idx{level}")])
                            for level, net in enumerate(self.nets)], 1)

    def joint(self, xs: torch.Tensor, actions: torch.Tensor,
              masks: torch.Tensor) -> torch.Tensor:
        """Q_tot(s, a) = sum_i Q_i(o_i, a_i) [B] (masks unused: no centring)."""
        del masks
        q = self.scores(xs)
        return q.gather(-1, actions.unsqueeze(-1)).squeeze(-1).sum(-1)

    def export_nets(self) -> list:
        return [TW.Net(list(view), _linears(net))
                for view, net in zip(self.views, self.nets)]


def make(arch: str, acting: int, hidden_factored: int = 64, hidden_vdn: int = 32,
         layers: int = 2) -> torch.nn.Module:
    if arch == "factored":
        return FactoredNet(acting, hidden_factored, layers)
    if arch == "vdn":
        return VdnNet(acting, hidden_vdn, layers)
    raise ValueError(f"turn_arch must be factored or vdn, not {arch!r}")


def scale_inputs(x: torch.Tensor, mean: torch.Tensor, scale: torch.Tensor,
                 clip: float) -> torch.Tensor:
    """As the plugin: (x - mean) / scale, a value that is not finite as 0,
    clipped to [-clip, clip]."""
    v = torch.where(torch.isfinite(x), (x - mean) / scale, torch.zeros_like(x))
    return v.clamp(-clip, clip)


def greedy(model: torch.nn.Module, xs: torch.Tensor,
           masks: torch.Tensor, prior: torch.Tensor | None = None) -> torch.Tensor:
    """Each level's best allowed action, ties to the lowest index (hold), as
    the plugin's Greedy: of the scores less the L0 prior when one is given
    (prior [B, acting, 4] in the reward's units, 0 but at L0). [B, acting]
    long."""
    s = model.scores(xs)
    if prior is not None:
        s = s - prior
    return s.masked_fill(~masks, float("-inf")).argmax(-1)


def prior_term(prior: torch.Tensor, actions: torch.Tensor, masks: torch.Tensor,
               arch: str) -> torch.Tensor:
    """What the L0 prior takes from Q(s, a) [B]. VDN: the taken actions'
    prior values summed; factored: each taken value less the mean over the
    allowed actions, as its advantages are centred."""
    taken = prior.gather(-1, actions.unsqueeze(-1)).squeeze(-1)
    if arch == "factored":
        allowed = masks.double()
        taken = taken - (prior * allowed).sum(-1) / allowed.sum(-1).clamp(min=1)
    return taken.sum(-1)


def joint_prior(model: torch.nn.Module, xs: torch.Tensor, actions: torch.Tensor,
                masks: torch.Tensor, prior: torch.Tensor | None = None) -> torch.Tensor:
    """Q(s, a) = the net's joint value less the L0 prior's term: the net
    learns the correction to the prior (controller/config.h turn_prior)."""
    q = model.joint(xs, actions, masks)
    return q if prior is None else q - prior_term(prior, actions, masks, model.arch)


def export(model: torch.nn.Module, version: int, mean: list, scale: list,
           clip: float, spread: float = 1.0) -> TW.TurnWeights:
    return TW.TurnWeights(version=version, arch=TW.ARCHS[model.arch],
                          acting=model.acting, clip=float(clip),
                          mean=[float(v) for v in mean],
                          scale=[float(v) for v in scale],
                          nets=model.export_nets(), spread=float(spread))
