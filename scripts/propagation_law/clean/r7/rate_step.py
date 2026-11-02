"""The write rate since a write-rate step (r7 part y). Inputs: the run's own past before X only. From sub3_rate's rate_v.py, without its walk."""
import bisect

N_SLOW = 20


def fast_rate(fb, flush_ends, l0m, xs, X, n_end=None):
    """write keys/s while L0 < 20, over the flush intervals in [xs, X) (fb: [op, t_us, keys] at every memtable switch; the L0
    count from the flush ends and the installed L0 merges, all before X). None with no such interval."""
    ks = [r[0] for r in fb]
    i0, i1 = bisect.bisect_left(ks, xs), bisect.bisect_left(ks, X)
    tk = tt = 0.0
    off = 0                                             # L0's files from before the first record: the count is exact at X
    if n_end is not None:
        off = n_end - (bisect.bisect_right(flush_ends, X) - sum(n for e, n in l0m if e <= X))

    def nfiles(op):
        return off + bisect.bisect_right(flush_ends, op) - sum(n for e, n in l0m if e <= op)
    for a, b in zip(fb[i0:i1], fb[i0 + 1:i1]):
        if max(nfiles(a[0]), nfiles(b[0])) < N_SLOW:
            tk += b[2] - a[2]
            tt += b[1] - a[1]
    return tk / (tt / 1e6) if tt > 0 else None

def inputs(w, cyc, last_l0=None):
    """(M keys per flush, rs slowed keys/s, tau s per L0 merge, v worker bytes/s), the hold record's where it has them. A stretch
    whose only L0 merge began before it has no timing of its own: the last finished merge's seconds at its bytes (last_l0)."""
    h = w.hold
    if h is not None:
        return h["M"], h["rs"], h["tau"], h["vr"], h["rf"]
    keys = cyc["keys_fast"] + cyc["keys_slow"] + cyc["keys_stop"]
    if not keys or not cyc["flushes"] or not (cyc["job_l0"] or last_l0):
        return None
    M = keys / cyc["flushes"]
    rs = cyc["keys_slow"] / cyc["t_slow"] if cyc["t_slow"] > 1.0 else 8388608 * M / w.F0    # the delayed write rate (8 MiB/s) in keys
    rf = cyc["keys_fast"] / cyc["t_fast"] if cyc["t_fast"] > 0 else None
    b1 = w.l0s + w.l0o
    tau = (cyc["sec_l0"] + cyc["gap_l0"]) / cyc["job_l0"] if cyc["job_l0"] else last_l0[0] * b1 / last_l0[1]
    return M, rs, tau, cyc["v_rest"], rf
