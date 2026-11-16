"""Bill Z's debt ledgers (trainer settings l0_bill_z 1; turn_trainer.py).

Bill Z of turn t, in J:

    Z(t) = L0's bill (turn_l0_bill: its reads and the merges out of L0)
           + the merges out of L1, L2 and L3 that finished in t, each at its
             whole price (l0_bill_deep's part)
           + D(t) - D(t-1),      D = D_1 + D_2 + D_3

Each D is a ledger of money owed for work the tree has put off, in the same
money as the merge prices. What is owed at the end of a turn, from the
level sizes B (bytes per level) and the turn line's targets C, every B taken
as max(0, B):

    carry = B_0;  for i = 1, 2, 3:  e_i = max(0, B_i + carry - C_i), carry = e_i
    E_i   = e_i when level i+1 holds bytes, else 0   (nothing below: nothing owed)
    W_3   = E_3 (1 + B_4 / (B_3 + E_2)), 0 when B_3 + E_2 is 0
            (RocksDB's own estimate of the merge input that clears E_3)

The price of the day, updated each turn before the ledgers use it (running
sums with a half-life of `half_life` turns, lam = 0.5^(1 / half_life)):

    own_i = paid_i / moved_i, i = 0..3: the merges out of L_i that finished,
            at their whole price, over the bytes they took out of L_i (files
            moved down without rewriting included); refreshed while the byte
            sum is above 1e6, else its last value (none before)
    p_1   = own_1, else own_0, else 0;   p_2 = own_2, else p_1
    p_in  = (paid_1 + paid_2 + paid_3) / the bytes fed into those merges
            (source + overlap, moves without rewriting left out), while that
            byte sum is above 1e6; before it ever is, L0's paid_0 / fed_0,
            taken once, when L0's byte sum first passes 1e6; else the last
            value (0 at first)

The ledger rule, for (X, p) = (E_1, p_1), (E_2, p_2), (W_3, p_in), each with
its own balance D and its own previous X:

    X grew:    D += p (X - X_prev)   (new debt is booked at the day's price)
    X shrank:  D *= X / X_prev       (released at the balance's average booked
                                      price; standing debt is never re-priced)

What is owed at start() is never booked, and the debt term is not
discounted.

Without L0's carry (carry_l0 False; the trainer settings' l0_bill_z_carry 0,
"option 1" of 2026-11-16) the carry into level 1 is 0:

    e_1 = max(0, B_1 - C_1);  then e_2, e_3, E_i and W_3 as above

so a flush books nothing while its bytes wait in L0; L0's merge is charged
as paid when it finishes, and the growth of L1 (and below) it causes is
booked in that same turn. Prices and the ledger rule are unchanged. Its
offline replay is ~/node_ops/diag/2026-11-16-billz-opt1/ (RULES.md).

The sizes B are rebuilt from the host log's finished jobs (TreeSizes), from
the first turn line's "B" on: each flush adds its bytes to L0, and each job
that ends takes its source bytes out of its start level and puts its output
less the overlap it consumed (plus, for a move without rewriting, the files
it moved) into its output level. Later lines' "B" is not used: the plugin
logs a level's bytes less the bytes of merges in flight, so a level's bytes
would leave B when a merge starts, while the bill charges the merge when it
finishes; rebuilt, bytes move between levels only when a job ends, in the
turn whose bill holds its price (finished: the pricer's own job set).

The offline replay of the same form is ~/node_ops/diag/2026-11-15-billz/
(RULES2.md z2n and RULES4.md; bill Z is z4n; its sizes are billz.py's
deltas() and Arm.S). Pure: no trainer, torch or file dependency."""

from __future__ import annotations

import bisect
import math

STEPS = 4            # merge start levels 0..3 whose prices are tracked
LEDGERS = 3          # D_1, D_2, D_3
MIN_BYTES = 1e6      # a running byte sum above this refreshes its price
TREE_LEVELS = 5      # the levels owed() reads, L0..L4


def _numbers(values, n: int, what: str) -> list:
    """The first n entries of `values` as floats; missing entries are 0
    (a tree with fewer levels); anything not a finite number is refused."""
    out = []
    for i in range(n):
        v = values[i] if i < len(values) else 0.0
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError(f"{what}[{i}] is not a finite number: {v!r}")
        out.append(float(v))
    return out


def _flows(values, what: str) -> list:
    """paid, moved or fed: exactly one finite number per start level 0..3."""
    if len(values) != STEPS:
        raise ValueError(f"{what} has {len(values)} entries, not {STEPS} (start levels 0..3)")
    return _numbers(values, STEPS, what)


def owed(B, C, carry_l0: bool = True) -> list:
    """[E_1, E_2, W_3] of the tree with level sizes B and targets C (bytes;
    index = level, levels 0..4 used, missing levels empty). carry_l0 False:
    L0's bytes carry nothing into level 1 (option 1)."""
    b = [max(0.0, v) for v in _numbers(B, 5, "B")]
    c = _numbers(C, 4, "C")
    E = [0.0] * 4
    carry = b[0] if carry_l0 else 0.0
    for i in (1, 2, 3):
        e = max(0.0, b[i] + carry - c[i])
        carry = e
        E[i] = e if b[i + 1] > 0 else 0.0
    size3 = b[3] + E[2]
    w3 = E[3] * (1.0 + b[4] / size3) if size3 > 0 else 0.0
    return [E[1], E[2], w3]


def finished(recs: list, job_idx: list, after: int, before: int) -> tuple:
    """(flush bytes, jobs) of the host-log records that `job_idx` indexes
    strictly between record indices `after` and `before`: the arguments of
    TreeSizes.apply. `job_idx` is HostLogPricer's index (every flush_end and
    every successful job_end of the measured phase), the job set
    merge_prices and written_bill.turn_row count, so a job's bytes move in
    the turn whose bill prices it. jobs: (start_level, output_level, s, o,
    x, s when the job moved files without rewriting them, else 0)."""
    i0 = bisect.bisect_right(job_idx, after)
    i1 = bisect.bisect_left(job_idx, before)
    flush, jobs = 0.0, []
    for i in job_idx[i0:i1]:
        r = recs[i]
        if r.get("type") == "flush_end":
            flush += float(r.get("x", 0))
            continue
        if r.get("type") != "job_end":
            continue
        a = int(r["start_level"])
        s = float(r.get("s", 0))
        trivial = r.get("trivial") in (True, 1, "1", "true")
        jobs.append((a, int(r.get("output_level", a + 1)), s, float(r.get("o", 0)),
                     float(r.get("x", 0)), s if trivial else 0.0))
    return flush, jobs


class TreeSizes:
    """Bytes per level of one run, rebuilt from the finished jobs (billz.py
    deltas()): from a turn line's "B", as many levels as it gives."""

    def __init__(self, B):
        self.sizes = _numbers(B, len(B), "B")

    def apply(self, flush_bytes, jobs) -> None:
        """One stretch of the host log: L0 gains the flushes' bytes; each
        job (start_level, output_level, source, overlap, output,
        trivial_source) takes `source` out of its start level and puts
        output - overlap + trivial_source into its output level. A level
        beyond the list is left out."""
        n = len(self.sizes)
        d = [0.0] * n
        f = _numbers([flush_bytes], 1, "flush_bytes")[0]
        if n:
            d[0] += f
        for job in jobs:
            if len(job) != 6:
                raise ValueError(f"a job is (start, output, source, overlap, output bytes, "
                                 f"trivial source), not {job!r}")
            a, b = int(job[0]), int(job[1])
            s, o, x, ts = _numbers(job[2:], 4, "job")
            if 0 <= a < n:
                d[a] -= s
            if 0 <= b < n:
                d[b] += x - o + ts
        self.sizes = [c + v for c, v in zip(self.sizes, d)]


class DebtLedger:
    """The three ledgers of one run; start() once, then add() the turns in
    order. A turn that was never added (a dropped line) is no gap in the
    books: the next add() books the change of what is owed since the last
    tree it saw, at that turn's prices."""

    def __init__(self, half_life: int = 64, carry_l0: bool = True):
        if isinstance(half_life, bool) or not isinstance(half_life, (int, float)) \
                or not math.isfinite(half_life) or half_life <= 0:
            raise ValueError(f"half_life must be a number above 0, not {half_life!r}")
        if type(carry_l0) is not bool:
            raise ValueError(f"carry_l0 is True or False, not {carry_l0!r}")
        self.half_life = half_life
        self.carry_l0 = carry_l0        # False: L0's bytes owe nothing (option 1)
        self.lam = 0.5 ** (1.0 / half_life)
        self.P = [0.0] * STEPS          # price as paid of the merges out of L_i, running
        self.M = [0.0] * STEPS          # bytes those merges took out of L_i, running
        self.own = [None] * STEPS       # own_i; None until first set
        self.P_in = self.M_in = 0.0     # merges out of L1-L3: price, bytes fed, running
        self.P_0 = self.M_0 = 0.0       # merges out of L0: price, bytes fed, running
        self.p_in = 0.0
        self.prices = [0.0] * LEDGERS   # [p_1, p_2, p_in] the last add() used
        self.balances = [0.0] * LEDGERS # [D_1, D_2, D_3] after the last add()
        self.prev = None                # [E_1, E_2, W_3] at start() or the last add()
        self.turns = 0                  # add() calls so far

    def start(self, B, C) -> None:
        """The tree before the first billed turn: what it owes becomes the
        previous amounts; nothing is booked."""
        self.prev = owed(B, C, self.carry_l0)

    def add(self, B, C, paid, moved, fed) -> list:
        """One turn: B, C the tree at its end; per start level 0..3 the merges
        out of it that finished in the turn: paid (their whole price), moved
        (bytes taken out of the level, moves without rewriting included), fed
        (bytes fed into them, source + overlap, moves left out). Returns
        [dD_1, dD_2, dD_3], each ledger's D(t) - D(t-1)."""
        if self.prev is None:
            raise RuntimeError("DebtLedger.add before start: the starting tree is not known")
        paid, moved, fed = _flows(paid, "paid"), _flows(moved, "moved"), _flows(fed, "fed")
        now = owed(B, C, self.carry_l0)
        lam = self.lam
        for i in range(STEPS):
            self.P[i] = lam * self.P[i] + paid[i]
            self.M[i] = lam * self.M[i] + moved[i]
            if self.M[i] > MIN_BYTES:
                self.own[i] = self.P[i] / self.M[i]
        own = self.own
        p1 = own[1] if own[1] is not None else own[0] if own[0] is not None else 0.0
        p2 = own[2] if own[2] is not None else p1
        self.P_in = lam * self.P_in + paid[1] + paid[2] + paid[3]
        self.M_in = lam * self.M_in + fed[1] + fed[2] + fed[3]
        self.P_0 = lam * self.P_0 + paid[0]
        self.M_0 = lam * self.M_0 + fed[0]
        if self.M_in > MIN_BYTES:
            self.p_in = self.P_in / self.M_in
        elif self.p_in == 0.0 and self.M_0 > MIN_BYTES:
            self.p_in = self.P_0 / self.M_0
        self.prices = [p1, p2, self.p_in]
        changes = []
        for k in range(LEDGERS):
            x, prev, before = now[k], self.prev[k], self.balances[k]
            if x > prev:
                self.balances[k] = before + self.prices[k] * (x - prev)
            elif prev > 0:
                self.balances[k] = before * (x / prev)
            changes.append(self.balances[k] - before)
        self.prev = now
        self.turns += 1
        return changes
