"""The cost ledger and the per-level bills (docs/INDEPENDENT_LEARNERS_PLAN.md
§3 T2 and T3, §4). Plain functions, no torch.

The ledger of one window (a turn of the turn mode, or a hold-only
"level_reads" window) holds three cells per level l, in J's money with the
betas applied (cost model 2, the evaluator's prices):

  M_l  every merge that starts at L_l and finishes in the window, each whole:
       beta_w (tau_job + I_W) + beta_r I_R, exactly as turn_trainer's
       merge_prices prices it (trivial moves and flushes are not merges here);
  R_l  beta_r (c_f probes + c_blk filter_passes + c_sk seeks
       + c_open (get_reopens + iter_reopens)) of the level's read counters;
  H_l  beta_r c_st hidden_steps of the level's read counters.

The read counters are a "level_reads" block (scripts/dbbench_pipeline/
level_reads.py; controller/log.h): per counter, one whole number per level.

A bill is a weight table {(level, kind): weight}, kind "M", "R" or "H";
bill(ledger, weights) = sum of weight x cell. three_part(j) is the plan's
bill of level j, M_{j-1} + M_j + R_j + H_j + H_{j+1}; reads_below=True adds
R_{j+1}; alpha > 0 adds the other cells of levels j-1 and j+1 at that weight
(the neighbour discount, BILL_ALPHA). At j = 0 there is no M_{-1}, and the
three-part bill is the L0 bill as built (turn_trainer.l0_bill_parts:
beta_r prices.l0_read_cost of L0's counts and L1's hidden steps, plus the L0
merges).

left_out(...) names the parts of the window's J that no ledger cell holds, so
J = ledger total + the left-out parts (the plan's stage 0 rule A)."""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from . import learned2 as L2

cm = L2.cm

CELL_KINDS = ("M", "R", "H")
# The level_reads block's counters (level_reads.KEYS) that R prices, each at
# its cost model 2 step type (as prices.L0_READ_TYPES prices L0's).
R_TYPES = {"probes": "probe", "filter_passes": "block", "seeks": "seek",
           "get_reopens": "reopen", "iter_reopens": "reopen"}
H_KEY = "hidden_steps"
MERGE_KINDS = ("l0", "deep")
# The host log's tickers (cost_model_v2.TYPE_TICKERS) behind the parts that
# the ledger splits: "step" is returned + hidden scan steps.
RETURNED = "rocksdb.number.db.next.found"
HIDDEN = "rocksdb.number.iter.skip"


@dataclass(frozen=True)
class Merge:
    """One merge's charge in money, betas not applied."""
    level: int
    tau: float       # tau_job = c_job + c_cr (S + O) + c_w X
    i_w: float       # write interference
    i_r: float       # read interference


@dataclass
class Ledger:
    """One window's cells, one entry per level, betas applied."""
    M: list
    R: list
    H: list

    def cell(self, level: int, kind: str) -> float:
        if kind not in CELL_KINDS:
            raise ValueError(f"cell kind is M, R or H, not {kind!r}")
        row = getattr(self, kind)
        return row[level] if 0 <= level < len(row) else 0.0

    def total(self) -> float:
        return sum(self.M) + sum(self.R) + sum(self.H)


# --------------------------------------------------------------- merges
def _job_windows(pricer: "L2.HostLogPricer", lo: int, hi: int):
    """(jobs ending strictly between records lo and hi, snaps, floor),
    with the windows HostLogPricer.price and merge_prices use."""
    recs = pricer.recs
    fl = recs[pricer.ms]
    floor = (fl["op"], cm._counts(fl, pricer.names))
    i0 = bisect.bisect_right(pricer.job_idx, lo)
    i1 = bisect.bisect_left(pricer.job_idx, hi)
    s0 = bisect.bisect_left(pricer.snap_ops, fl["op"])
    s1 = bisect.bisect_right(pricer.snap_ops, recs[hi]["op"])
    snaps = L2._SnapList(pricer.snaps[s0:s1])
    snaps.ops = pricer.snap_ops[s0:s1]
    return pricer.jobs[i0:i1], snaps, floor


def _charge(pricer, job, snaps, floor) -> tuple:
    """(tau, write interference, read interference) of one job, money."""
    v2 = pricer.p.v2
    counts, length = cm.window(job, snaps, floor, v2.n_win)
    read, write = cm.charge(v2, pricer.p.q_bar, job, cm.rho_of(v2, counts, length))
    return cm.job_price(v2, job), write, read


def merges(pricer: "L2.HostLogPricer", lo: int, hi: int) -> list:
    """Every merge (kind l0 or deep) that ends in the window, in log order,
    with its charge parts."""
    jobs, snaps, floor = _job_windows(pricer, lo, hi)
    out = []
    for job in jobs:
        if job.kind not in MERGE_KINDS:
            continue
        tau, i_w, i_r = _charge(pricer, job, snaps, floor)
        out.append(Merge(level=int(job.level), tau=tau, i_w=i_w, i_r=i_r))
    return out


def merge_cells(pricer: "L2.HostLogPricer", lo: int, hi: int, n_levels: int) -> list:
    """M_l for l = 0 .. n_levels - 1: each merge whole, beta_w (tau + I_W) +
    beta_r I_R, summed per start level in log order (as merge_prices sums).
    A merge from a level at or past n_levels raises ValueError."""
    p = pricer.p
    out = [0.0] * n_levels
    for m in merges(pricer, lo, hi):
        if not 0 <= m.level < n_levels:
            raise ValueError(f"a merge from L{m.level}, past the {n_levels} levels")
        out[m.level] += p.beta_w * (m.tau + m.i_w) + p.beta_r * m.i_r
    return out


# --------------------------------------------------------------- reads
def read_cells(pricing, block: dict) -> tuple:
    """(R, H) per level from one "level_reads" block, betas applied. A block
    without one of the priced counters raises KeyError."""
    base = pricing.v2.base
    n = len(block[H_KEY])
    for key in R_TYPES:
        if len(block[key]) != n:
            raise ValueError(f"{key} has {len(block[key])} levels, {H_KEY} {n}")
    R = [pricing.beta_r * sum(base[x] * float(block[key][l]) for key, x in R_TYPES.items())
         for l in range(n)]
    H = [pricing.beta_r * base["step"] * float(block[H_KEY][l]) for l in range(n)]
    return R, H


def window_ledger(pricer: "L2.HostLogPricer", lo: int, hi: int, block: dict) -> Ledger:
    """The ledger of the window whose host-log records are (lo, hi)
    (HostLogPricer.bounds) and whose read counters are `block`."""
    R, H = read_cells(pricer.p, block)
    return Ledger(M=merge_cells(pricer, lo, hi, len(R)), R=R, H=H)


# --------------------------------------------------------------- bills
def three_part(j: int, n_levels: int, reads_below: bool = False,
               alpha: float = 0.0) -> dict:
    """The weight table of level j's three-part bill: (1) M_{j-1} + M_j,
    (2) R_j + H_j, (3) H_{j+1}; reads_below adds R_{j+1}; alpha gives the
    other cells of levels j-1 and j+1 that weight. Cells outside the levels
    are left out."""
    if not 0 <= j < n_levels:
        raise ValueError(f"level {j} is not one of {n_levels} levels")
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(f"alpha must be in [0, 1], not {alpha!r}")
    w = {}
    if alpha > 0:
        for level in (j - 1, j + 1):
            for kind in CELL_KINDS:
                w[(level, kind)] = alpha
    for cell in ((j - 1, "M"), (j, "M"), (j, "R"), (j, "H"), (j + 1, "H")):
        w[cell] = 1.0
    if reads_below:
        w[(j + 1, "R")] = 1.0
    return {c: v for c, v in w.items() if 0 <= c[0] < n_levels}


def bill_parts(ledger: Ledger, weights: dict) -> dict:
    """{(level, kind): weight x cell} over the table's cells."""
    return {(level, kind): w * ledger.cell(level, kind)
            for (level, kind), w in weights.items()}


def bill(ledger: Ledger, weights: dict) -> float:
    return sum(bill_parts(ledger, weights).values())


def l0_counts(block: dict) -> dict:
    """L0's counts of a level_reads block in turn_trainer's "l0_read" form
    (controller/plugin.cc JsonL0Read): entry 0 of each counter, and L1's
    hidden steps as l1_hidden_steps."""
    out = {key: block[key][0] for key in block}
    out["l1_hidden_steps"] = block[H_KEY][1] if len(block[H_KEY]) > 1 else 0
    return out


# --------------------------------------------------------------- left out
def _tickers(record: dict, names: list) -> dict:
    if record.get("type") == "stamp":
        return dict(record["tickers"])
    return dict(zip(names, record["fg"]))


def held_byte_ops(pricer: "L2.HostLogPricer", lo: int, hi: int) -> float:
    """The window's held byte-operations, as HostLogPricer.price sums them."""
    recs = pricer.recs
    a, b = recs[lo], recs[hi]
    k = bisect.bisect_right(pricer.h_idx, lo) - 1
    h0 = pricer.h[k][1] if k >= 0 else a.get("h", 0)
    samples = [(a["op"], h0)]
    k1 = bisect.bisect_left(pricer.h_idx, hi)
    samples += [pricer.h[i] for i in range(k + 1, k1)]
    ends = [op for op, _ in samples[1:]] + [b["op"]]
    return sum(h * (e - op) for (op, h), e in zip(samples, ends))


LEFT_OUT = ("flushes", "trivial_moves", "puts", "returned_steps",
            "memtable_and_base", "space", "reads_unplaced")


def left_out(pricer: "L2.HostLogPricer", lo: int, hi: int, block: dict) -> dict:
    """The parts of the window's J (HostLogPricer.price(lo, hi)) that no
    ledger cell holds, betas applied:
      flushes, trivial_moves  each whole with its interference;
      puts                    c_put per Put;
      returned_steps          c_st per scan step that returned a key;
      memtable_and_base       c_mt per Get and scan, c_get0, c_sc0, c_ib;
      space                   c_s / q_bar per held byte-operation;
      reads_unplaced          the host log's probes, filter passes, seeks,
                              reopens and hidden steps over the window less
                              the block's sums over the levels: hidden steps
                              in the memtables, and the reads between the
                              block's own edges (the plugin's polls) and the
                              host log's (its snaps).
    So J = window_ledger(...).total() + sum(left_out(...).values())."""
    p = pricer.p
    v2 = p.v2
    names = pricer.names
    a, b = pricer.recs[lo], pricer.recs[hi]
    cnt = cm.difference(cm._counts(a, names), cm._counts(b, names))
    ta, tb = _tickers(a, names), _tickers(b, names)
    out = {key: 0.0 for key in LEFT_OUT}
    jobs, snaps, floor = _job_windows(pricer, lo, hi)
    for job in jobs:
        if job.kind in MERGE_KINDS:
            continue
        tau, i_w, i_r = _charge(pricer, job, snaps, floor)
        key = "flushes" if job.kind == "flush" else "trivial_moves"
        out[key] += p.beta_w * (tau + i_w) + p.beta_r * i_r
    base = v2.base
    out["puts"] = p.beta_w * base["put"] * cnt["put"]
    out["returned_steps"] = p.beta_r * base["step"] * (tb[RETURNED] - ta[RETURNED])
    out["memtable_and_base"] = p.beta_r * (base["memtable"] * cnt["memtable"] +
                                           base["get0"] * cnt["get0"] +
                                           base["scan0"] * cnt["scan0"] +
                                           base["iblock"] * cnt["iblock"])
    out["space"] = p.beta_s * p.c_s / p.q_bar * held_byte_ops(pricer, lo, hi)
    placed = {x: 0.0 for x in ("probe", "block", "seek", "reopen")}
    for key, x in R_TYPES.items():
        placed[x] += float(sum(block[key]))
    unplaced = sum(base[x] * (cnt[x] - placed[x]) for x in placed)
    unplaced += base["step"] * ((tb[HIDDEN] - ta[HIDDEN]) - float(sum(block[H_KEY])))
    out["reads_unplaced"] = p.beta_r * unplaced
    return out
