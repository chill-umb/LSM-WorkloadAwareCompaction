"""The reward of PATHWAYS H §3 (amended 2026-10-03, D-23):

    r_i = -(c^beta_i + X_{i+1} + X_{i-1}) / (c_w C_i),
    c^beta_i = beta_W sum tau_job + beta_R Rd_i + sum I^beta + beta_S (c_s/q) sum g_i,

priced from the plugin's counts (controller/log.h schema 4) with the
evaluator's formulas (cost_model_v2), so the trainer's totals and 04's agree
(ARCH-7). The six shared buckets (hit-read, reopen, scan-base, memtable,
write-path, fixed) go to no level and no reward.

The interim binary (D-24 §2, the interim interface §5.3) has no iterator-block
or heap counters, so Rd_i is the level's probes, false-positive block reads,
seeks and reopens, with the slot-blocking moves, plus the hidden steps charged
to it at c_st (one mean step price); returned steps go to scan-base.

The neighbour charge X_j = c_w C_j (V_j(x'_j | hold) - V_j(x'_j | a)) uses a
one-step prediction of the neighbour's state (predict_neighbour): the burst
level i releases or keeps (Theorem A.1) and, for the upper neighbour, its
hidden-step input moved by M^hd. X = 0 when a is hold.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from . import prices as prices_module
from .logs import ACTIONS, finite

cost_model_v2 = prices_module.cost_model_v2


def job_of(line: dict) -> "cost_model_v2.Job":
    return cost_model_v2.Job(
        kind=line["kind"], level=line["start_level"], s=float(line["s"]),
        o=float(line["o"]), x=float(line["x"]), n_begin=line["n_begin"],
        n_end=line["n_end"])


def job_charges(p: prices_module.Pricing, line: dict) -> tuple[float, float, float]:
    """(tau_job, interference read part, write part) in money for one job
    line, its window priced as cost_model_v2.evaluate prices it."""
    job = job_of(line)
    tau = cost_model_v2.job_price(p.v2, job)
    counts = {x: float(line["win"][x]) for x in cost_model_v2.STEP_TYPES}
    rho = cost_model_v2.rho_of(p.v2, counts, float(line["win_ops"]))
    read, write = cost_model_v2.charge(p.v2, p.q_bar, job, rho)
    return tau, read, write


@dataclass
class LevelCost:
    """One interval's priced cost at one level, in money, before beta."""
    tau: float = 0.0          # sum of tau_job of its jobs (flushes at L0)
    read: float = 0.0         # Rd_i
    intf_read: float = 0.0    # I_rd of its non-flush jobs
    intf_write: float = 0.0   # I_wr of its non-flush jobs
    space: float = 0.0        # (c_s / q) sum g_i, shadowed garbage
    # Flushes' interference: the write-path bucket, not the level's.
    write_path_read: float = 0.0
    write_path_write: float = 0.0
    jobs: int = 0

    def weighted(self, p: prices_module.Pricing) -> float:
        """c^beta_i."""
        return (p.beta_w * self.tau + p.beta_r * self.read +
                p.beta_r * self.intf_read + p.beta_w * self.intf_write +
                p.beta_s * self.space)


def charged(cost: dict, name: str) -> float:
    """A read count as charged: raw, less the slot moves out, plus in."""
    return cost[name] - cost[f"slot_out_{name}"] + cost[f"slot_in_{name}"]


def level_cost(p: prices_module.Pricing, transition: dict,
               jobs: list[dict]) -> LevelCost:
    cost = transition["cost"]
    out = LevelCost()
    out.read = (p.base("probe") * charged(cost, "probes") +
                p.base("block") * charged(cost, "fp_reads") +
                p.base("seek") * charged(cost, "seeks") +
                p.base("reopen") * charged(cost, "reopens") +
                p.base("step") * (cost["hidden_steps"] -
                                  cost["slot_out_hidden"] +
                                  cost["slot_in_hidden"]))
    rho_tilde = transition.get("rho_tilde")
    garbage = 1.0 - rho_tilde if finite(rho_tilde) else 0.0
    out.space = p.c_s / p.q_bar * max(0.0, garbage) * cost["held_byte_ops"]
    for line in jobs:
        tau, read, write = job_charges(p, line)
        out.tau += tau
        out.jobs += 1
        if line["kind"] == "flush":
            out.write_path_read += read
            out.write_path_write += write
        else:
            out.intf_read += read
            out.intf_write += write
    return out


def buckets(p: prices_module.Pricing, cost: dict) -> dict[str, float]:
    """The shared buckets' quiet parts over an interval, from any one level's
    record (each carries the global counts): money, read part unless named
    write. The hit-read bucket is c_blk times every level's hit_reads, and
    the write-path bucket the flushes' interference (LevelCost); the reopen
    bucket is empty (every Get and iterator reopen has a level, D-21)."""
    return {
        "scan_base": p.base("step") * cost["fg_nexts_found"],
        "memtable": (p.base("memtable") * (cost["gets"] + cost["scans"]) +
                     p.base("step") * cost["memtable_hidden"]),
        "fixed_read": (p.base("get0") * cost["gets"] +
                       p.base("scan0") * cost["scans"]),
        "fixed_write": p.base("put") * cost["writes"],
    }


# ---------------------------------------------------------------------------
# The one-step neighbour prediction (H §3).

def _rho_tilde(state: dict) -> float:
    rho, xi = state.get("rho"), state.get("xi")
    if finite(rho) and finite(xi):
        return xi + (1 - xi) * rho
    return 1.0


def _released(phi: float, m: float) -> float:
    return max(0.0, phi - m) if finite(phi) and finite(m) else 0.0


@dataclass
class Release:
    """What level i's action releases now, against hold, in the neighbour's
    units."""
    landed_below: float = 0.0   # share of C_{i+1} landing below
    phi_after_a: float = math.nan
    phi_after_hold: float = math.nan
    m_a: float = math.nan
    m_hold: float = math.nan
    share_a: float = 0.0        # share of level i's bytes moved down
    share_hold: float = 0.0


def release(own_agent: str, own_state: dict, action: str,
            value_before: float, value_after: float, own_c: float,
            below_c: Optional[float]) -> Release:
    """Level i's release under its action and under hold (Theorem A.1):
    compaction releases the level down to its target, so a level at fill phi
    releases (phi - m)^+ of C_i, of which rho~_i lands below; L0 releases
    its k0 files when k0 reaches the trigger."""
    r = Release()
    if own_agent == "l0":
        fill, trigger = own_state.get("l0_fill"), own_state.get("trigger")
        if not (finite(fill) and finite(value_before) and value_before > 0):
            return r
        k0 = fill * value_before  # files not being compacted
        k0_bytes = (fill * trigger * own_c) if finite(trigger) else math.nan
        due_hold = k0 >= value_before
        due_a = k0 >= value_after if finite(value_after) else due_hold
        if action in ("defer", "expand"):
            due_a = False
        r.share_a, r.share_hold = float(due_a), float(due_hold)
        if below_c and finite(k0_bytes):
            r.landed_below = (r.share_a - r.share_hold) * k0_bytes / below_c
        r.phi_after_a = 0.0 if due_a else fill
        r.phi_after_hold = 0.0 if due_hold else fill
        r.m_a = r.m_hold = 1.0
        return r
    phi = own_state.get("phi") if own_agent == "interior" else math.nan
    if own_agent == "last":
        fill = own_state.get("fill")
        anchor, timing = own_state.get("anchor"), own_state.get("timing")
        if finite(fill) and finite(anchor) and finite(timing):
            phi = fill * anchor * timing
    if not finite(phi):
        return r
    m_hold, m_a = value_before, value_after if action != "hold" else value_before
    out_a, out_hold = _released(phi, m_a), _released(phi, m_hold)
    r.m_a, r.m_hold = m_a, m_hold
    r.phi_after_a, r.phi_after_hold = phi - out_a, phi - out_hold
    r.share_a = out_a / phi if phi > 0 else 0.0
    r.share_hold = out_hold / phi if phi > 0 else 0.0
    if below_c and finite(own_c):
        r.landed_below = _rho_tilde(own_state) * (out_a - out_hold) * own_c / below_c
    return r


def _with(state: dict, **changes) -> dict:
    out = dict(state)
    for key, value in changes.items():
        if key in out:
            out[key] = value
    return out


def predict_neighbour(side: str, nbr_agent: str, nbr_state: dict,
                      rel: Release) -> tuple[dict, dict]:
    """(x' under the action, x' under hold) for the neighbour above ("up")
    or below ("down"). Everything the release does not move is held at its
    value now (H §3: the rest of items 2-5 at their current values)."""
    s = nbr_state
    if side == "down":
        def after(landed: float, phi_up: float, m_up: float) -> dict:
            if nbr_agent == "last":
                m = (s.get("anchor") or 1.0) * (s.get("timing") or 1.0)
                fill = s.get("fill")
                phi = fill * m + landed if finite(fill) else math.nan
                return _with(s, fill=phi / m if finite(phi) else fill,
                             headroom=m - phi if finite(phi) else s.get("headroom"))
            m = (s.get("anchor") or 1.0) * (s.get("timing") or 1.0)
            phi = s.get("phi")
            phi = phi + landed if finite(phi) else phi
            return _with(s, phi=phi, score=phi / m if finite(phi) else s.get("score"),
                         phi_up=phi_up, m_up=m_up)
        return (after(rel.landed_below, rel.phi_after_a, rel.m_a),
                after(0.0, rel.phi_after_hold, rel.m_hold))
    # Up: the neighbour sees level i through phi_down / m_down (L0: phi_1),
    # and its hidden-step charge over level i's entries leaves with them
    # (M^hd), assuming they spread over the level in proportion to bytes.
    def above(phi_after: float, m: float, share: float) -> dict:
        if nbr_agent == "l0":
            # L0's charge mixes L0's and L1's entries; without the split the
            # M^hd move is not applied (a stated approximation).
            return _with(s, phi_1=phi_after)
        e_hd = s.get("e_hd")
        return _with(s, phi_down=phi_after, m_down=m,
                     e_hd=e_hd * (1 - share) if finite(e_hd) else e_hd)
    return (above(rel.phi_after_a, rel.m_a, rel.share_a),
            above(rel.phi_after_hold, rel.m_hold, rel.share_hold))


def action_index(name: Optional[str]) -> int:
    return ACTIONS.index(name) if name in ACTIONS else 0


@dataclass
class NeighbourCase:
    """What the neighbour charge needs from one side at sample time."""
    key: str
    c_bytes: float
    mask: list
    prior: list
    x_action: list = field(default_factory=list)
    x_hold: list = field(default_factory=list)
