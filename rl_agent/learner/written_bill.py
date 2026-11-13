"""The deep part of L0's bill, charged when the bytes are written (trainer
settings l0_bill_written 1, written_turns; turn_trainer.py).

For merge step k (L_k to L_k+1), k = 0..3, and the turn t, with every ratio
taken over the last `turns` turns up to t:

    written   = keys written in t * (bytes flushed / keys written)
    rho_j     = 1 - dropped / source      over the merges that start at L_j
                (source = bytes taken from L_j, trivial moves included;
                dropped = source + overlap - output, a trivial move drops 0)
    share_k   = rho_0 * ... * rho_(k-1)
    price_k   = price as paid of the merges from L_k / bytes that left L_k
    cell_k    = written * share_k * price_k

A step with no bytes leaving L_k in the window: when the level has no room
(`due`: the controller's own owed work at that level is above 0) it keeps
the last price seen in a window with a paid merge, or, if it never had one,
takes the current price of the step above; when the level has room it costs
nothing. rho keeps its last value (1 before the first). No level size or
target enters as an amount.

L0's bill with the flag = L0's reads + L0's merges booked whole (as built)
+ cell_1 + cell_2 + cell_3. Offline study:
~/node_ops/diag/2026-11-13-cstep/RULE8.md (variant d)."""

from __future__ import annotations

import bisect
from collections import deque
from dataclasses import dataclass

STEPS = 4          # merge steps 0..3
DEEP = (1, 2, 3)   # the steps L0's bill adds


@dataclass
class Row:
    """One turn's flows; lists are per merge step (start level 0..3)."""
    flush: float    # bytes the flushes wrote
    puts: float     # keys written
    src: list       # bytes that left L_k (trivial moves included)
    drop: list      # bytes dropped by the merges from L_k
    paid: list      # price as paid of the merges from L_k that finished


def turn_row(recs: list, job_idx: list, lo: int, hi: int, merges: dict, puts: float) -> Row:
    """The row of the turn whose host-log records lie strictly between
    records lo and hi (HostLogPricer.bounds); `merges` is merge_prices' map
    {start level: [price, bytes]} of the same window."""
    flush, src, drop = 0.0, [0.0] * STEPS, [0.0] * STEPS
    i0 = bisect.bisect_right(job_idx, lo)
    i1 = bisect.bisect_left(job_idx, hi)
    for i in job_idx[i0:i1]:
        r = recs[i]
        if r.get("type") == "flush_end":
            flush += float(r["x"])
            continue
        a = int(r["start_level"])
        b = int(r.get("output_level", a + 1))
        if b <= a or a >= STEPS:
            continue
        s = float(r["s"])
        src[a] += s
        if not bool(r["trivial"]):
            drop[a] += s + float(r["o"]) - float(r["x"])
    paid = [float(merges.get(k, [0.0, 0.0])[0]) for k in range(STEPS)]
    return Row(flush=flush, puts=float(puts), src=src, drop=drop, paid=paid)


class WrittenCells:
    """The cells of the run so far; add() takes the turns in order."""

    def __init__(self, turns: int = 32):
        if turns < 1:
            raise ValueError("written_turns must be >= 1")
        self.rows = deque(maxlen=int(turns))
        self.rho = [None] * STEPS
        self.price = [None] * STEPS
        self.paid_price = [None] * STEPS

    def add(self, row: Row, due: list) -> list:
        """[cell_0 .. cell_3] of the turn `row`; due[k]: L_k has no room."""
        self.rows.append(row)
        flush = sum(r.flush for r in self.rows)
        puts = sum(r.puts for r in self.rows)
        written = row.puts * flush / puts if puts > 0 else 0.0
        moved = [sum(r.src[k] for r in self.rows) for k in range(STEPS)]
        for k in range(STEPS):
            if moved[k] > 0:
                paid = sum(r.paid[k] for r in self.rows)
                self.rho[k] = max(0.0, 1.0 - sum(r.drop[k] for r in self.rows) / moved[k])
                self.price[k] = paid / moved[k]
                if paid > 0:
                    self.paid_price[k] = self.price[k]
        cells, share, above = [], 1.0, 0.0
        for k in range(STEPS):
            if moved[k] > 0:
                p = self.price[k]
            elif k == 0 or not (k < len(due) and due[k]):
                p = 0.0
            elif self.paid_price[k] is not None:
                p = self.paid_price[k]
            else:
                p = above
            above = p
            cells.append(written * share * p)
            share *= self.rho[k] if self.rho[k] is not None else 1.0
        return cells
