"""Cost model v2 (PATHWAYS D §1 and D §2 as amended 2026-10-03;
PREREGISTRATION D-23 §1, D-24 §1-§2 and §4) over one run's measured phase.

J_beta keeps its form beta_W C_W + beta_R C_R + beta_S C_S. Here:

- C_W = sum over jobs of tau_job + c_put Puts + I_wr, where a job's price is
  tau_job = c_job[kind] + c_cr (S + O) + c_w X (a trivial move: c_job alone),
  kinds flush / l0 (merges sourced at L0) / deep / move;
- C_R = the quiet read steps (filter probes, block-reading probes, run seeks,
  reopens) + c_st (returned + hidden scan steps) + c_ib iterator blocks
  + (c_mt + c0_get) Gets + (c_mt + c0_sc) scans + I_rd;
- C_S is unchanged: (c_s / q-bar) times the held byte-operations.

Interference: each job carries one charge at its completion,
    I = q-bar sum_x rho_x (kappaB_x Y + kappaJ_x,kind t_job),
with Y = X + lambda (S + O), t_job = tau_job / p_dev, and rho_x the quiet
cost per operation of the type-x steps over the job's window W (D §1): its
own operations if it served at least n_win, otherwise from the last counter
snapshot at or before n_end - n_win (the phase start at the earliest). The
read types' part is I_rd, the Put inserts' part I_wr.

Two estimators, by what the binary records (D-24 §4):
- exact: host log schema 3 (job and flush records with the fg counters, snap
  records): every window from the log;
- mean field: schema 2 (no job-boundary counters): rho_x is the phase's mean
  for every job. Sums over jobs then need only per-kind totals.

Report-only diagnostics (exploratory track, D-24 §1 item 4):
- OBJ-7: per kind, measured job time over priced device time;
- OBJ-6: the physical interference sum_x s_x c0_x R_x(own operations), with
  s_x = kappaJ_x,kind + kappaB_x Y / span; and s_open, its reopen share, for
  D-24 item 3's reopen check against c0_open (1 + s_open);
- OBJ-9: the share of hidden steps the per-level counters place (schema 3).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

KINDS = ("flush", "l0", "deep", "move")
READ_TYPES = ("probe", "block", "seek", "reopen", "step", "iblock", "memtable",
              "get0", "scan0")
WRITE_TYPES = ("put",)
STEP_TYPES = READ_TYPES + WRITE_TYPES
BASE_PRICE = {"probe": "c_f", "block": "c_blk", "seek": "c_sk",
              "reopen": "c_open", "step": "c_st", "iblock": "c_ib",
              "memtable": "c_mt", "get0": "c_get0", "scan0": "c_sc0",
              "put": "c_put"}
# Each step type's count as a sum of tickers (D §1's metrics table; the host
# log's fg list). Iterator blocks have no counter on the interim binary.
TYPE_TICKERS = {
    "probe": ("rocksdb.point.sst.probe",),
    "block": ("rocksdb.bloom.filter.full.positive",),
    "seek": ("rocksdb.sorted.run.seek",),
    "reopen": ("rocksdb.read.table.reopen",),
    "step": ("rocksdb.number.db.next.found", "rocksdb.number.iter.skip"),
    "iblock": (),
    "memtable": ("rocksdb.number.keys.read", "rocksdb.number.db.seek"),
    "get0": ("rocksdb.number.keys.read",),
    "scan0": ("rocksdb.number.db.seek",),
    "put": ("rocksdb.number.keys.written",),
}
STEP_TICKERS = tuple(sorted({t for ts in TYPE_TICKERS.values() for t in ts}))
PRICES_SCHEMA = 6


def type_counts(tickers: dict) -> dict[str, float]:
    """Step counts by type from a ticker-name map (cumulative)."""
    missing = [t for t in STEP_TICKERS if t not in tickers]
    if missing:
        raise KeyError("no " + ", ".join(missing))
    return {x: float(sum(tickers[t] for t in ts))
            for x, ts in TYPE_TICKERS.items()}


def difference(before: dict, after: dict) -> dict[str, float]:
    return {x: after[x] - before[x] for x in STEP_TYPES}


@dataclass(frozen=True)
class Prices:
    """A schema-6 prices record's cost-model-2 prices, in money."""
    base: dict  # step type -> money per step (quiet)
    job: dict  # kind -> money per job
    c_w: float
    c_cr: float
    p_dev: float  # money per core-second
    lam: float
    kappa_b: dict  # step type -> slowdown per byte/s (s/B)
    kappa_j: dict  # step type -> kind -> slowdown per running job
    n_win: int


def _number(value, name: str, positive: bool) -> float:
    if (not isinstance(value, (int, float)) or isinstance(value, bool) or
            not math.isfinite(value) or value < 0 or
            (positive and value == 0)):
        raise ValueError(f"{name} must be a {'positive' if positive else 'non-negative'}"
                         f" number; got {value!r}")
    return float(value)


def load_prices(record: dict) -> Prices:
    """Cost model 2's prices from a schema-6 record; ValueError otherwise.
    Every price is required (D-23 §3(d)); c_cr, c_ib and every kappa may be
    0; the job prices, c_w and the quiet read prices must be positive."""
    if record.get("schema") != PRICES_SCHEMA or record.get("cost_model") != 2:
        raise ValueError("not a schema-6, cost-model-2 prices record")
    zero_ok = {"c_cr", "c_ib"}
    base = {x: _number(record.get(key), key, key not in zero_ok)
            for x, key in BASE_PRICE.items()}
    jobs = record.get("job_prices")
    if not isinstance(jobs, dict):
        raise ValueError("no job_prices")
    job = {kind: _number(jobs.get(kind), f"job_prices.{kind}", True)
           for kind in KINDS}
    kappa = record.get("kappa")
    if not isinstance(kappa, dict):
        raise ValueError("no kappa")
    kb, kj = kappa.get("B"), kappa.get("J")
    if not isinstance(kb, dict) or not isinstance(kj, dict):
        raise ValueError("kappa needs B and J")
    kappa_b = {x: _number(kb.get(x), f"kappa.B.{x}", False) for x in STEP_TYPES}
    kappa_j = {}
    for x in STEP_TYPES:
        row = kj.get(x)
        if not isinstance(row, dict):
            raise ValueError(f"kappa.J.{x} must map the job kinds")
        kappa_j[x] = {k: _number(row.get(k), f"kappa.J.{x}.{k}", False)
                      for k in KINDS}
    n_win = record.get("n_win")
    if not isinstance(n_win, int) or isinstance(n_win, bool) or n_win <= 0:
        raise ValueError(f"n_win must be a positive integer; got {n_win!r}")
    return Prices(base=base, job=job,
                  c_w=_number(record.get("c_w"), "c_w", True),
                  c_cr=_number(record.get("c_cr"), "c_cr", False),
                  p_dev=_number(record.get("price_per_core_second"),
                                "price_per_core_second", True),
                  lam=_number(kappa.get("lambda"), "kappa.lambda", False),
                  kappa_b=kappa_b, kappa_j=kappa_j, n_win=n_win)


@dataclass
class Job:
    """One job completing in the measured phase."""
    kind: str
    level: int  # start level; -1 for a flush
    s: float = 0.0
    o: float = 0.0
    x: float = 0.0
    n_begin: Optional[int] = None
    n_end: Optional[int] = None
    span_s: Optional[float] = None  # its measured wall span
    fg_begin: Optional[dict] = None  # step counts by type at its begin record
    fg_end: Optional[dict] = None


def job_price(p: Prices, job: Job) -> float:
    """tau_job in money (Lemma D.18): a trivial move is its job price alone."""
    if job.kind == "move":
        return p.job["move"]
    return p.job[job.kind] + p.c_cr * (job.s + job.o) + p.c_w * job.x


def job_bytes(p: Prices, job: Job) -> float:
    """Y = X + lambda (S + O); a trivial move moves no bytes."""
    return 0.0 if job.kind == "move" else job.x + p.lam * (job.s + job.o)


def charge(p: Prices, rate: float, job: Job, rho: dict) -> tuple[float, float]:
    """(read part, write part) of the job's interference charge, in money,
    with rho the window's quiet cost per operation by step type."""
    t_job = job_price(p, job) / p.p_dev
    y = job_bytes(p, job)

    def part(types):
        return rate * sum(rho[x] * (p.kappa_b[x] * y +
                                    p.kappa_j[x][job.kind] * t_job)
                          for x in types)
    return part(READ_TYPES), part(WRITE_TYPES)


def rho_of(p: Prices, counts: dict, operations: float) -> dict[str, float]:
    if operations <= 0:
        return {x: 0.0 for x in STEP_TYPES}
    return {x: p.base[x] * counts[x] / operations for x in STEP_TYPES}


def window(job: Job, snaps: list, floor: tuple, n_win: int):
    """The job's window counts and length (D §1). `snaps` is [(op, counts)]
    in op order; `floor` is the phase start's (op, counts)."""
    if (job.n_begin is not None and job.fg_begin is not None and
            job.n_end - job.n_begin >= n_win):
        return difference(job.fg_begin, job.fg_end), job.n_end - job.n_begin
    target = job.n_end - n_win
    start = floor
    for op, counts in snaps:
        if op > target:
            break
        if op >= start[0]:
            start = (op, counts)
    return difference(start[1], job.fg_end), job.n_end - start[0]


def evaluate(p: Prices, rate: float, c_s: float, jobs: list[Job],
             phase_counts: dict, operations: float, held_byte_ops: float,
             exact: bool, snaps: Optional[list] = None,
             floor: Optional[tuple] = None) -> dict:
    """C_W, C_R, C_S of one run under cost model 2, their parts, and the
    report-only diagnostics. `phase_counts` are the measured phase's step
    counts by type and `operations` its operations; with `exact`, every job
    needs n_end and fg_end, and `snaps` and `floor` give the windows."""
    if exact and floor is None:
        raise ValueError("exact windows need the phase start's counters")
    mean_rho = rho_of(p, phase_counts, operations)
    out = {f"jobs_{k}": 0 for k in KINDS}
    job_part = read_bytes = intf_read = intf_write = 0.0
    physical = s_open_reopens = 0.0
    excluded_physical = 0
    spans = {k: [0.0, 0.0] for k in KINDS}  # measured s, priced s
    for job in jobs:
        out[f"jobs_{job.kind}"] += 1
        tau = job_price(p, job)
        job_part += tau
        if job.kind != "move":
            read_bytes += job.s + job.o
        if exact:
            counts, length = window(job, snaps or [], floor, p.n_win)
            rho = rho_of(p, counts, length)
        else:
            rho = mean_rho
        r, w = charge(p, rate, job, rho)
        intf_read += r
        intf_write += w
        if job.span_s is not None:
            spans[job.kind][0] += job.span_s
            spans[job.kind][1] += tau / p.p_dev
        # OBJ-6: the physical slowdown over the job's own operations.
        own = None
        if job.span_s and job.n_begin is not None and job.n_end is not None:
            if exact and job.fg_begin is not None:
                own = difference(job.fg_begin, job.fg_end)
            elif not exact and operations > 0:
                share = (job.n_end - job.n_begin) / operations
                own = {x: phase_counts[x] * share for x in STEP_TYPES}
        if own is None:
            excluded_physical += 1
            continue
        v = job_bytes(p, job) / job.span_s
        slow = {x: p.kappa_j[x][job.kind] + p.kappa_b[x] * v for x in STEP_TYPES}
        physical += sum(slow[x] * p.base[x] * own[x] for x in STEP_TYPES)
        s_open_reopens += slow["reopen"] * own["reopen"]
    read_base = sum(p.base[x] * phase_counts[x]
                    for x in ("probe", "block", "seek", "reopen"))
    scan_steps = p.base["step"] * phase_counts["step"]
    iblocks = p.base["iblock"] * phase_counts["iblock"]
    memtable = p.base["memtable"] * phase_counts["memtable"]
    fixed_read = (p.base["get0"] * phase_counts["get0"] +
                  p.base["scan0"] * phase_counts["scan0"])
    put_part = p.base["put"] * phase_counts["put"]
    c_w_total = job_part + put_part + intf_write
    c_r_total = read_base + scan_steps + iblocks + memtable + fixed_read + intf_read
    out.update(
        C_W=c_w_total, C_R=c_r_total, C_S=c_s / rate * held_byte_ops,
        job_part=job_part, compaction_bytes_read=read_bytes,
        put_part=put_part, read_base_part=read_base,
        scan_step_part=scan_steps, iterator_block_part=iblocks,
        memtable_part=memtable, fixed_read_part=fixed_read,
        interference_read=intf_read, interference_write=intf_write,
        interference_physical=physical,
        physical_jobs_excluded=excluded_physical,
        s_open=(s_open_reopens / phase_counts["reopen"]
                if phase_counts["reopen"] > 0 else 0.0),
        interference_estimator="exact" if exact else "mean field")
    for kind in KINDS:
        measured, priced = spans[kind]
        out[f"job_time_ratio_{kind}"] = (measured / priced if priced > 0
                                         else math.nan)
    return out


def _counts(record: dict, names: list) -> dict[str, float]:
    if record.get("type") == "stamp":
        return type_counts(record["tickers"])
    return type_counts(dict(zip(names, record["fg"])))


def jobs_from_host_log(records: list[dict], lo: int, hi: int,
                       flush_bytes: dict, flush_spans_s: dict) -> list[Job]:
    """The jobs whose end record lies strictly between records[lo] (the
    measure_start stamp) and records[hi] (drain_end), charged at completion.
    Compactions: job_end with ok = 1, joined to its job_begin. Flushes: in
    schema 3 each flush_end, joined to its flush_begin; before it, each
    flush's H sample, with its bytes and span from the event log
    (flush_bytes, flush_spans_s by job id). A failed job is not charged."""
    schema = records[0].get("schema", 0)
    names = list(records[0].get("fg", []))
    begins, flush_begins = {}, {}
    for r in records:
        if r.get("type") == "job_begin":
            begins[r["job"]] = r
        elif r.get("type") == "flush_begin":
            flush_begins[r["job"]] = r
    jobs = []
    for r in records[lo + 1:hi]:
        kind = r.get("type")
        if kind == "job_end" and r.get("ok") == 1:
            b = begins.get(r["job"])
            trivial = bool(r["trivial"])
            jobs.append(Job(
                kind="move" if trivial else
                ("l0" if r["start_level"] == 0 else "deep"),
                level=r["start_level"], s=float(r["s"]), o=float(r["o"]),
                x=0.0 if trivial else float(r["x"]),
                n_begin=b["op"] if b else None, n_end=r["op"],
                span_s=(r["t_us"] - b["t_us"]) / 1e6 if b else None,
                fg_begin=_counts(b, names) if b and schema >= 3 else None,
                fg_end=_counts(r, names) if schema >= 3 else None))
        elif kind == "flush_end" and schema >= 3:
            b = flush_begins.get(r["job"])
            jobs.append(Job(
                kind="flush", level=-1, x=float(r["x"]),
                n_begin=b["op"] if b else None, n_end=r["op"],
                span_s=(r["t_us"] - b["t_us"]) / 1e6 if b else None,
                fg_begin=_counts(b, names) if b else None,
                fg_end=_counts(r, names)))
        elif kind == "h" and r.get("cause") == "flush" and schema < 3:
            jobs.append(Job(kind="flush", level=-1,
                            x=float(flush_bytes.get(r["job"], 0.0)),
                            n_end=r["op"],
                            span_s=flush_spans_s.get(r["job"])))
    return jobs


def snapshots(records: list[dict]) -> list[tuple]:
    """Every snap record's (op, step counts), in file (and op) order."""
    names = list(records[0].get("fg", []))
    return [(r["op"], _counts(r, names)) for r in records
            if r.get("type") == "snap"]


def phase_floor(start: dict) -> tuple:
    """The measure_start stamp as the earliest window start."""
    return start["op"], type_counts(start["tickers"])
