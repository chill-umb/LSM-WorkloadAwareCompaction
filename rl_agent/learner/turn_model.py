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


def split_view(level: int, acting: int, summary: bool, l0_inputs: bool) -> list:
    """A split learner's view of one level: its VDN view, plus (summary) the
    anchor and owed work of every level two or more below it (the VDN view
    already holds the block just below), plus (l0_inputs, levels 2 and
    deeper) L0's file count and anchor."""
    ix = {n: i for i, n in enumerate(TW.feature_names(acting))}
    view = list(TW.vdn_view(level, acting))
    if summary:
        for k in range(level + 2, acting):
            view += [ix[f"l{k}_anchor"], ix[f"l{k}_owed"]]
    if l0_inputs and level >= 2:
        view += [ix["l0_files_k"], ix["l0_anchor"]]
    return view


class GroupNet(torch.nn.Module):
    """One learner of a split VDN: a small net per level of its group, each on
    its own view; the group's value is the sum of its levels' values."""

    def __init__(self, levels: list, views: list, n_in: int, hidden: int, layers: int):
        super().__init__()
        self.levels = list(levels)
        self.views = [list(v) for v in views]
        # -1 (a zero pad) reads an appended column of zeros.
        for k, view in enumerate(self.views):
            self.register_buffer(
                f"idx{k}", torch.tensor([n_in if j < 0 else j for j in view], dtype=torch.long))
        self.nets = torch.nn.ModuleList(
            _mlp(len(view), hidden, layers, N_ACTIONS) for view in self.views)

    def scores(self, xs: torch.Tensor) -> torch.Tensor:
        """[B, len(levels), 4]."""
        padded = torch.cat([xs, torch.zeros(xs.shape[0], 1, dtype=xs.dtype)], 1)
        return torch.stack([net(padded[:, getattr(self, f"idx{k}")])
                            for k, net in enumerate(self.nets)], 1)

    def joint(self, xs: torch.Tensor, actions: torch.Tensor) -> torch.Tensor:
        """Q_G(s, a) [B]; actions [B, len(levels)] long."""
        q = self.scores(xs)
        return q.gather(-1, actions.unsqueeze(-1)).squeeze(-1).sum(-1)


class SplitVdn(torch.nn.Module):
    """The VDN arm split into learners over groups of levels (each group its
    own nets, reward, horizon and bootstrap; turn_trainer trains each group on
    its own). To the plugin it is an ordinary VDN file: one net per acting
    level, each with its own view. groups: [{"levels": [...], "summary": bool,
    "l0_inputs": bool}, ...], together every acting level exactly once. Group
    k's nets start from seed + k."""
    arch = "vdn"

    def __init__(self, acting: int, groups: list, hidden: int = 32, layers: int = 2,
                 seed: int = 1):
        super().__init__()
        check_groups(groups, acting)
        self.acting = acting
        self.groups = torch.nn.ModuleList()
        for k, g in enumerate(groups):
            torch.manual_seed(int(seed) + k)
            views = [split_view(level, acting, bool(g.get("summary", False)),
                                bool(g.get("l0_inputs", False))) for level in g["levels"]]
            self.groups.append(GroupNet(g["levels"], views, len(TW.feature_names(acting)),
                                        hidden, layers))
        self.where = {}
        for k, g in enumerate(self.groups):
            for j, level in enumerate(g.levels):
                self.where[level] = (k, j)

    def scores(self, xs: torch.Tensor) -> torch.Tensor:
        """Every level's values [B, acting, 4], from its own group's net."""
        per = [g.scores(xs) for g in self.groups]
        return torch.stack([per[k][:, j] for k, j in
                            (self.where[level] for level in range(self.acting))], 1)

    def joint(self, xs: torch.Tensor, actions: torch.Tensor,
              masks: torch.Tensor) -> torch.Tensor:
        """The sum over every level (the logged TD error's team value)."""
        del masks
        q = self.scores(xs)
        return q.gather(-1, actions.unsqueeze(-1)).squeeze(-1).sum(-1)

    def export_nets(self) -> list:
        nets = []
        for level in range(self.acting):
            k, j = self.where[level]
            g = self.groups[k]
            nets.append(TW.Net(list(g.views[j]), _linears(g.nets[j])))
        return nets


def check_groups(groups: list, acting: int) -> None:
    """The groups cover every acting level exactly once."""
    if not isinstance(groups, list) or len(groups) < 2:
        raise ValueError("split needs a list of at least two groups")
    seen = []
    for g in groups:
        levels = g.get("levels") if isinstance(g, dict) else None
        if not isinstance(levels, list) or not levels or \
                not all(isinstance(v, int) and not isinstance(v, bool) for v in levels):
            raise ValueError(f"a split group needs a non-empty list of levels: {g!r}")
        seen += levels
    if sorted(seen) != list(range(acting)):
        raise ValueError(f"the split groups must cover levels 0..{acting - 1} exactly once, "
                         f"not {sorted(seen)}")


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
           masks: torch.Tensor) -> torch.Tensor:
    """Each level's best allowed action, ties to the lowest index (hold), as
    the plugin's Greedy. [B, acting] long."""
    s = model.scores(xs).masked_fill(~masks, float("-inf"))
    return s.argmax(-1)


def export(model: torch.nn.Module, version: int, mean: list, scale: list,
           clip: float) -> TW.TurnWeights:
    return TW.TurnWeights(version=version, arch=TW.ARCHS[model.arch],
                          acting=model.acting, clip=float(clip),
                          mean=[float(v) for v in mean],
                          scale=[float(v) for v in scale],
                          nets=model.export_nets())
