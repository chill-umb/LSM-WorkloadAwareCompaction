"""The learned2 learner (controller/learned2.h; owner, 2026-11-05, fixes F1-F5):

* the plugin's 12 inputs (F4) and its action slots (0 hold, 1 down, 2 up);
* HostLogPricer: prices the plugin's "window" lines [op0, op1) on the host
  log the fork writes, exactly as 04's collect_arm and VB's winprice.py do
  (cost_model_v2.evaluate with exact windows, the interference look-back
  floored at the measured phase's start), so the learner's J is the
  evaluator's J (F5);
* Ensemble: per level, E small ReLU nets that predict log J per operation
  from the inputs, trained on bootstrap-weighted settled windows (F2, F3),
  exported to the weights file with a fixed input scaling folded into the
  first layer (no clipping in effect, F4).
Pure functions and small classes; trainer2.py wires them together."""

from __future__ import annotations

import bisect
import math
import random
from dataclasses import dataclass, field

from . import prices as prices_module
from .weights import ModelWeights, N_ACTIONS

cm = prices_module.cost_model_v2

F_NAMES = ["put_share", "get_share", "scan_share", "log10_h", "log10_op",
           "log2_k", "log2_m1", "log2_m2", "log2_m3", "log2_m4", "log2_m5",
           "log2_m6"]
F_HASH = 0x4D7A0F34E38D4D67   # controller/tests/learned2_test.cc pins the same
LOG2_K = F_NAMES.index("log2_k")
LOG2_M1 = F_NAMES.index("log2_m1")
SLOTS = ("hold", "down", "up", "unused")
# Fixed input scaling (offset, scale), folded into the exported first layer:
# shares as they are, log10 H about 10, log10 op about 6.5, log2 K about 2.
OFFSET = [0, 0, 0, 10.0, 6.5, 2.0, 0, 0, 0, 0, 0, 0]
SCALE = [1, 1, 1, 1.0, 1.0, 1.0, 1, 1, 1, 1, 1, 1]
EXPORT_CLIP = 1e6


def own_feature(level: int) -> int:
    """The feature index of the level agent's own setting (L0: K)."""
    return LOG2_K if level == 0 else LOG2_M1 + level - 1


# --------------------------------------------------------------- pricing
class _SnapList(list):
    ops = ()


_orig_window = cm.window


def _fast_window(job, snaps, floor, n_win):
    """cost_model_v2.window by bisection over a _SnapList; any other list
    takes the original linear scan (VB checked the two agree exactly)."""
    if not isinstance(snaps, _SnapList):
        return _orig_window(job, snaps, floor, n_win)
    if (job.n_begin is not None and job.fg_begin is not None and
            job.n_end - job.n_begin >= n_win):
        return cm.difference(job.fg_begin, job.fg_end), job.n_end - job.n_begin
    target = job.n_end - n_win
    start = floor
    i = bisect.bisect_right(snaps.ops, target) - 1
    if i >= 0 and snaps.ops[i] >= start[0]:
        start = snaps[i]
    return cm.difference(start[1], job.fg_end), job.n_end - start[0]


cm.window = _fast_window


class HostLogPricer:
    """The host log as it grows, and cost model 2 over any window of it."""

    def __init__(self, pricing: "prices_module.Pricing"):
        self.p = pricing
        self.names = None
        self.recs = []
        self.ms = None          # index of the measure_start stamp
        self.ds = None          # index of the drain_start stamp
        self.begins = {}
        self.fbegins = {}
        self.job_idx, self.jobs = [], []
        self.snap_ops, self.snaps, self.snap_idx = [], [], []
        self.h_idx, self.h = [], []

    def add(self, r: dict) -> None:
        i = len(self.recs)
        self.recs.append(r)
        t = r.get("type")
        if t == "header":
            self.names = list(r.get("fg", []))
            return
        if t == "stamp":
            if r.get("name") == "measure_start" and self.ms is None:
                self.ms = i
                self.h_idx.append(i)
                self.h.append((r["op"], r["h"]))
            elif r.get("name") == "drain_start" and self.ds is None:
                self.ds = i
            return
        if t == "h":
            self.h_idx.append(i)
            self.h.append((r["op"], r["h"]))
        elif t == "snap":
            self.snaps.append((r["op"], cm._counts(r, self.names)))
            self.snap_ops.append(r["op"])
            self.snap_idx.append(i)
        elif t == "job_begin":
            self.begins[r["job"]] = r
        elif t == "flush_begin":
            self.fbegins[r["job"]] = r
        elif self.ms is not None and t == "job_end" and r.get("ok") in (True, 1, "1", "true"):
            bb = self.begins.get(r["job"])
            triv = bool(r["trivial"])
            self.jobs.append(cm.Job(
                kind="move" if triv else ("l0" if r["start_level"] == 0 else "deep"),
                level=r["start_level"], s=float(r["s"]), o=float(r["o"]),
                x=0.0 if triv else float(r["x"]), n_begin=bb["op"] if bb else None,
                n_end=r["op"], span_s=(r["t_us"] - bb["t_us"]) / 1e6 if bb else None,
                fg_begin=cm._counts(bb, self.names) if bb else None,
                fg_end=cm._counts(r, self.names)))
            self.job_idx.append(i)
        elif self.ms is not None and t == "flush_end":
            bb = self.fbegins.get(r["job"])
            self.jobs.append(cm.Job(
                kind="flush", level=-1, x=float(r["x"]), n_begin=bb["op"] if bb else None,
                n_end=r["op"], span_s=(r["t_us"] - bb["t_us"]) / 1e6 if bb else None,
                fg_begin=cm._counts(bb, self.names) if bb else None,
                fg_end=cm._counts(r, self.names)))
            self.job_idx.append(i)

    def bounds(self, op0: int, op1: int):
        """Record indices (lo, hi) of the window, or None until the log has
        passed op1 (or the window lies outside the measured phase)."""
        if self.ms is None or self.names is None:
            return None
        if self.ds is not None and op1 > self.recs[self.ds]["op"]:
            return None
        k1 = bisect.bisect_left(self.snap_ops, op1)
        if k1 >= len(self.snap_ops):
            return None
        hi = self.snap_idx[k1]
        if op0 <= self.recs[self.ms]["op"]:
            lo = self.ms
        else:
            k0 = bisect.bisect_left(self.snap_ops, op0)
            lo = self.snap_idx[k0]
        return (lo, hi) if hi > lo else None

    def price(self, lo: int, hi: int) -> dict:
        recs, names = self.recs, self.names
        a, b = recs[lo], recs[hi]
        ca, cb = cm._counts(a, names), cm._counts(b, names)
        fl = recs[self.ms]
        cf = cm._counts(fl, names)
        counts = cm.difference(ca, cb)
        ops = b["op"] - a["op"]
        i0 = bisect.bisect_right(self.job_idx, lo)
        i1 = bisect.bisect_left(self.job_idx, hi)
        jobs = self.jobs[i0:i1]
        k = bisect.bisect_right(self.h_idx, lo) - 1
        h0 = self.h[k][1] if k >= 0 else a.get("h", 0)
        samples = [(a["op"], h0)]
        k1 = bisect.bisect_left(self.h_idx, hi)
        samples += [self.h[j] for j in range(k + 1, k1)]
        ends = [op for op, _ in samples[1:]] + [b["op"]]
        held = sum(h * (e - op) for (op, h), e in zip(samples, ends))
        s0 = bisect.bisect_left(self.snap_ops, fl["op"])
        s1 = bisect.bisect_right(self.snap_ops, b["op"])
        sl = _SnapList(self.snaps[s0:s1])
        sl.ops = self.snap_ops[s0:s1]
        res = cm.evaluate(self.p.v2, self.p.q_bar, self.p.c_s, jobs, counts, ops,
                          held, True, snaps=sl, floor=(fl["op"], cf))
        p = self.p
        J = p.beta_w * res["C_W"] + p.beta_r * res["C_R"] + p.beta_s * res["C_S"]
        return {"ops": ops, "C_W": res["C_W"], "C_R": res["C_R"], "C_S": res["C_S"],
                "J": J, "secs": (b["t_us"] - a["t_us"]) / 1e6, "jobs": len(jobs)}


# --------------------------------------------------------------- ensemble
@dataclass
class Sample:
    x: list
    y: float
    w: list     # one bootstrap weight per member (Poisson(1))
    op1: int


def poisson1(rng: random.Random) -> int:
    """A Poisson(1) draw (Knuth)."""
    limit, k, p = math.exp(-1.0), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def scaled(x: list) -> list:
    return [(v - o) / s for v, o, s in zip(x, OFFSET, SCALE)]


@dataclass
class Ensemble:
    """E members for one level; output 0 is log J per op minus y0."""
    members: int
    hidden: int
    layers: int
    lr: float
    huber: float
    seed: int
    nets: list = field(default_factory=list)
    opts: list = field(default_factory=list)
    samples: list = field(default_factory=list)
    y0: float = None
    rng: random.Random = None

    def __post_init__(self):
        import torch
        self.rng = random.Random(self.seed)
        g = torch.Generator().manual_seed(self.seed)
        for e in range(self.members):
            torch.manual_seed(self.seed * 1000 + e)
            mods, width = [], len(F_NAMES)
            for _ in range(self.layers):
                mods += [torch.nn.Linear(width, self.hidden), torch.nn.ReLU()]
                width = self.hidden
            mods.append(torch.nn.Linear(width, N_ACTIONS))
            net = torch.nn.Sequential(*mods).double()
            self.nets.append(net)
            self.opts.append(torch.optim.Adam(net.parameters(), lr=self.lr))
        self._g = g

    def add(self, x: list, y: float, op1: int) -> None:
        if self.y0 is None:
            self.y0 = y
        self.samples.append(Sample(list(x), y, [poisson1(self.rng) for _ in range(self.members)], op1))

    def predict(self, x: list) -> list:
        """Each member's prediction of log J (y0 added back)."""
        import torch
        if self.y0 is None:
            return []
        t = torch.tensor([scaled(x)], dtype=torch.float64)
        with torch.no_grad():
            return [float(net(t)[0, 0]) + self.y0 for net in self.nets]

    def train(self, steps: int, batch: int) -> dict:
        """`steps` Adam steps per member on its bootstrap-weighted samples;
        the mean training (Huber) loss of the last step and the ensemble
        mean's root-mean-square error on every sample."""
        import torch
        n = len(self.samples)
        X = torch.tensor([scaled(s.x) for s in self.samples], dtype=torch.float64)
        Y = torch.tensor([s.y - self.y0 for s in self.samples], dtype=torch.float64)
        losses = []
        for e, (net, opt) in enumerate(zip(self.nets, self.opts)):
            w = torch.tensor([float(s.w[e]) for s in self.samples], dtype=torch.float64)
            if float(w.sum()) <= 0:
                w = torch.ones(n, dtype=torch.float64)
            last = math.nan
            for _ in range(steps):
                idx = torch.multinomial(w, min(batch, n) if n > 1 else 1,
                                        replacement=True, generator=self._g)
                pred = net(X[idx])[:, 0]
                loss = torch.nn.functional.huber_loss(pred, Y[idx], delta=self.huber)
                opt.zero_grad()
                loss.backward()
                opt.step()
                last = float(loss)
            losses.append(last)
        with torch.no_grad():
            mean = torch.stack([net(X)[:, 0] for net in self.nets]).mean(0)
            rmse = float(torch.sqrt(torch.mean((mean - Y) ** 2)))
        return {"train_loss": sum(losses) / len(losses), "fit_rmse": rmse, "n": n}

    def export(self, level: int) -> list:
        """One ModelWeights per member, the input scaling folded in:
        W' = W / s and b' = b - W (o / s) on the first layer."""
        out = []
        for net in self.nets:
            linears = [m for m in net if hasattr(m, "weight")]
            layers = []
            for li, lin in enumerate(linears):
                W = lin.weight.detach().double().tolist()
                b = lin.bias.detach().double().tolist()
                if li == 0:
                    W2 = [[w / s for w, s in zip(row, SCALE)] for row in W]
                    b = [bi - sum(w * o / s for w, o, s in zip(row, OFFSET, SCALE))
                         for row, bi in zip(W, b)]
                    W = W2
                layers.append((W, b))
            out.append(ModelWeights(agent="f", level=level, names=list(F_NAMES),
                                    clip=EXPORT_CLIP, layers=layers))
        return out


def noise_floor(series: list) -> float:
    """sqrt(mean of squared consecutive differences / 2) over (key, y)
    pairs, counting only neighbours with the same key (same settings)."""
    d = [(y2 - y1) ** 2 for (k1, y1), (k2, y2) in zip(series, series[1:]) if k1 == k2]
    return math.sqrt(sum(d) / (2 * len(d))) if len(d) >= 3 else math.nan
