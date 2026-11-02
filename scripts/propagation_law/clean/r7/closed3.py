"""#3 in closed form (trial, 2026-10-31; owner: "try the closed-form #3"). No walk: no loop over merges or flushes, only over the tree's levels.
Cases 1 and 2. General RocksDB rules: L0's score N/K^ against the top level's (version_set.cc:3908-3910, 3961-3972), one slot, nothing below score 1,
writes slowed from 20 files (column_family.cc:1032-1036). Merges come every N flushes.

One cycle of N files, B = bytes the worker reads per flush (B_f below 20 files, B_s from 20):
  budget C(N) = min(N, 20) B_f + max(0, N - 20) B_s      need N R, R = F0 + S1/K^ + r0 F0 sum_G(1 + om_i)     D = bytes per unit of score of group G
  the top score moves by (N R - C(N)) / D per cycle, and N = K^ s. So:
  from 20 files up: N' - N* = rho (N - N*), rho = (R + D/K^) / (B_s + D/K^), N* = 20 + 20 (R - B_f) / (B_s - R)   (R > B_f; else it falls through 20)
  below 20 files:   N' = g N, g = 1 / (1 - K^ (R - B_f) / D); floors: score 1, and the score where the next level joins G (D, R change there).
Each piece is geometric, so its merges and flushes are closed forms; the stretch's flushes A close the sum. Mean = files / merges."""
import math
import os

N_SLOW = 20
Y_NS = os.environ.get("Y_NS", "bal")     # bal: N* from the balance | nb2: the law's settled level
Y_FILL = os.environ.get("Y_FILL", "1") == "1"     # 1: K lowered: L1 fills under the first pick's score, its own phase
Y_MAP = os.environ.get("Y_MAP", "0") == "1"       # 1: below 20, the exact next-pick map for the ratio
Y_JOIN = os.environ.get("Y_JOIN", "1") == "1"     # 1: the group grows as levels join (one piece per level) | 0: fixed at the first merge
Y_TRACE = os.environ.get("Y_TRACE")               # file: one line per piece (diagnosis only)


def tr(**k):
    if Y_TRACE:
        with open(f"{Y_TRACE}.{os.getpid()}", "a") as f:
            f.write(repr(k) + "\n")


def geo_sum(a, g, m):
    return a * m if abs(g - 1) < 1e-9 else a * (1 - g ** m) / (1 - g)


def geo_count(a, g, flushes):
    """m with sum_{k<m} a g^k = flushes."""
    if abs(g - 1) < 1e-9:
        return flushes / a
    x = 1 - flushes * (1 - g) / a
    if x <= 0:                                  # g < 1 and the sum never reaches it
        return float("inf")
    return math.log(x) / math.log(g)


def toward(N, ns, rho, left, stop):
    """N_k = ns + (N - ns) rho^k until the flushes run out or N reaches stop. (flushes used, merges, N at the end)."""
    a = N - ns
    if abs(a) < 1e-9 or rho <= 0:
        k = left / max(ns, 1.0)
        return left, k, ns
    kmax = math.log((stop - ns) / a) / math.log(rho) if (stop - ns) / a > 0 and abs(stop - N) > 1e-12 else math.inf
    if ns <= 0:
        k = geo_count(N, rho, left)
    else:                                       # k ns + a (1 - rho^k) / (1 - rho) = left: two passes from the far end close it
        k = left / max(N, ns)
        for _ in range(2):
            k = max(0.0, (left - a * (1 - rho ** k) / (1 - rho)) / ns)
    k = min(k, kmax)
    return k * ns + a * (1 - rho ** k) / (1 - rho), k, ns + a * rho ** k


def mean_files(F0, tF, S, om, om_at, drain_bytes, dp, K, z_x, r0, B_f, B_s, n1, A, nb2=None, size0=None, chi=1.0):
    """(mean files per merge over the stretch's A flushes after the first merge's begin, merges, pieces). chi: an L0 merge's read cost in the worker's
    time, against a byte of the other levels' merges (seconds per byte, L0 merges / the rest). A <= 0: the first merge alone."""
    if A <= 0:
        return n1, 1.0, 0
    s = n1 / K
    z = [0.0] + [min(z_x[i], s) for i in range(1, dp)] + [0.0]
    grp = {1} | {i for i in range(1, dp) if z[i] >= s - 1e-9}
    N, left, tot, m, pieces = float(n1), A + n1, 0.0, 0.0, 0   # the first merge's files: A + n1 closes the sum
    c1 = r0 * F0 / S[1]                                  # L1's score gained per file merged
    # K lowered: the first merge took what L0 held, above every level's score. The next pick is set by the real scores: top T = max(deepest levels' scores,
    # L1's after the merge), the group drains r = B_f / D per flush, a files flushed during the merge: N2 = K (T + r a) / (1 + K r).
    u0 = z_x[1]
    T = max([u0 + c1 * N] + [z_x[i] for i in range(2, dp)])
    if Y_FILL and T < s - 1e-9:
        z2 = [0.0] + [z_x[i] for i in range(1, dp)] + [0.0]
        z2[1] = u0 + c1 * N
        g2 = {i for i in range(1, dp) if z2[i] >= T - 1e-9}
        D2 = drain_bytes(S, om_at(om, size0, z2, S, dp) if size0 is not None else om, g2, dp)
        r = B_f / D2
        a = min(N, chi * (N * F0 + u0 * S[1]) / B_f)
        N2 = max(K * (T + r * a) / (1 + K * r), K)
        tot, m, left = N, 1.0, A                         # the first merge counted; A flushes for the rest
        tr(fill=1, K=K, n1=n1, N2=round(N2, 2), T=round(T, 2), a=round(a, 2), r=round(r, 4), D2=round(D2 / 1e6, 1), z2=[round(x, 2) for x in z2[1:dp]], A=round(A, 1), g2=sorted(g2))
        N = min(N2, float(n1))
        if left <= N:                                    # one more merge at most
            return (tot + (left if left > 0 else 0)) / (m + (left / N if left > 0 else 0)), m + (left / N if left > 0 else 0), 1
        s = N / K
        z = [0.0] + [min(z2[i], s) for i in range(1, dp)] + [0.0]
        grp = {1} | {i for i in range(1, dp) if z[i] >= s - 1e-9}
    for _ in range(2 * dp + 6):
        pieces += 1
        zc = list(z)
        for i in grp:
            zc[i] = N / K
        omc = om_at(om, size0, zc, S, dp) if size0 is not None else om
        D = drain_bytes(S, omc, grp, dp)
        chain, i = [], 1
        while i in grp and i < dp:
            chain.append(i)
            i += 1
        a_ = r0 * F0
        L0c = chi * (F0 + S[1] / K)
        Rin = sum(1 + omc[i] for i in chain[:-1])      # levels inside the chain: both ends at the water, overlap does not move with s
        W = omc[chain[-1]] * (N / K)                   # the chain's last level: overlap ~ 1 / s
        R = L0c + a_ * (Rin + 1 + W / (N / K))
        s_now, s_j, j_ = N / K, 1.0, None
        recv = max(grp) + 1 if max(grp) + 1 < dp else None
        if Y_JOIN:
            for i in range(1, dp):
                if i not in grp and z[i] < s_now:
                    kr = sum(S[g] for g in grp) / S[recv] if i == recv else 0.0
                    sj = s_now - (s_now - z[i]) / (1 + kr)
                    if sj > s_j:
                        s_j, j_ = sj, i
        floor_n = K * s_j
        if N >= N_SLOW:
            if B_s <= R:
                ns = 34.0
            elif R > B_f:
                ns = min(max(N_SLOW + N_SLOW * (R - B_f) / (B_s - R), N_SLOW), 34.0)
            else:
                ns = None                               # no balance at or above 20: falls through it
            if Y_NS == "nb2" and nb2 is not None:
                ns = nb2
            rho = min(max((R + D / K) / (B_s + D / K), 0.0), 0.98)
            if ns is None:
                used, k, N2 = toward(N, 0.0, rho, left, max(floor_n, N_SLOW - 1e-6))
            elif floor_n >= N and ns <= N:              # a level above holds it here
                used, k, N2 = left, left / N, N
            else:
                used, k, N2 = toward(N, ns, rho, left, max(floor_n, ns) if ns < N else ns)
        else:
            den = B_f - L0c - a_ * (Rin + 1)
            s_b = a_ * W / den if den > 0 else math.inf
            ns = K * s_b
            kap = K * (R - B_f) / D
            r_ = B_f / D                                # N' = N (1 + K c + K r alpha) / (1 + K r): the score at L0's next pick
            g_ = (1 + K * c1 + K * r_ * (L0c / B_f)) / (1 + K * r_)
            if Y_MAP:
                kap = 1 - 1 / g_ if g_ > 1 else kap
            if ns >= N_SLOW:                            # no balance below 20: rises to 20
                g = 1 / (1 - kap) if 0 < kap < 0.98 else 50.0
                k = min(geo_count(N, g, left), math.log(N_SLOW / N) / math.log(g))
                used, N2 = geo_sum(N, g, k), N * g ** k
                if N2 >= N_SLOW - 1e-6:
                    N2 = float(N_SLOW)
            elif ns > floor_n:                          # the balance below 20: geometric toward it
                step = N * kap / (1 - kap) if kap < 0.98 else (ns - N)
                rho = 1 - step / (ns - N) if abs(ns - N) > 1e-9 else 0.0
                rho = min(max(rho, 0.0), 0.98)
                used, k, N2 = toward(N, ns, rho, left, ns)
            else:                                       # falls to the floor: score 1, or a level joins
                if floor_n >= N - 1e-9:
                    if j_ is None:
                        used, k, N2 = left, left / N, N
                    else:
                        used, k, N2 = 0.0, 0.0, N
                else:
                    g = 1 / (1 - kap) if kap < 0.98 else 50.0
                    g = min(g, 0.999)
                    k = min(geo_count(N, g, left), math.log(floor_n / N) / math.log(g))
                    used, N2 = geo_sum(N, g, k), N * g ** k
        tr(K=K, N=round(N, 2), N2=round(N2, 2), k=round(k, 2), used=round(used, 1), left=round(left, 1), ns=ns if ns is None else round(ns, 2), grp=sorted(grp),
           R=round(R / 1e6, 2), L0c=round(L0c / 1e6, 2), W=round(W / 1e6, 2), D=round(D / 1e6, 1), B_f=round(B_f / 1e6, 2), B_s=round(B_s / 1e6, 2), c1=round(c1, 4), om=[round(x, 2) for x in omc[1:dp]], z=[round(x, 2) for x in z[1:dp]], floor=round(floor_n, 2))
        tot += used
        m += k
        left -= used
        N = N2
        if left <= 1e-6:
            break
        s_now = N / K
        for i in range(1, dp):
            if i not in grp and (z[i] >= s_now - 1e-9 or (i == j_ and s_now <= s_j + 1e-9)):
                grp.add(i)
    if left > 1e-6:
        tot += left
        m += left / max(N, 1.0)
    return tot / max(m, 1e-9), m, pieces


# ---- the two-number form (Y_FORM=pair, 2026-10-31; owner: "go, fix the lowered-trigger rise"). ----
# An L0 merge reads all of L1, so its length follows L1's score u, not K. State (N, u): files and L1's score at a pick. Per merge:
#   b = N F0 + u S1 bytes. Files flushed meanwhile, a: b / BL (fast) | (20 - N)(1 - e) + b / BLs once L0's count passes 20 (writes slowed, e = BL / BLs) | b / BLs from 20 files.
#   u1 = u + c1 N, T = max(u1, deep levels, 1). L0 on top at the end (a / K >= T): N' = a, u' = u1.
#   Else the top group drains r = B / D per flush (B = B_f below 20 files, B_s from 20) until L0's score meets it: N' = K (T + r a) / (1 + K r), or, crossing 20 in the gap,
#   N' = 20 + (K (T - r_f (20 - a)) - 20) / (1 + K r_s).
# Each of these is x' = M x + c with x = (N, u), so x_k and sum N_k have a closed form in M's two eigenvalues; a change of regime starts a new piece (at most PAIR_PIECES).
PAIR_PIECES = int(os.environ.get("Y_PIECES", "6"))
Y_P20 = int(os.environ.get("Y_P20", "1"))        # 0: stays at 20 files or more: the cycle balance | 1: the pair form there too
Y_BAND = float(os.environ.get("Y_BAND", "0.25"))  # a piece ends when N moves this far: the overlaps are re-read


def _pair_form(M, c, x0):
    """(x_k, sum_{i=1..k} N_i) of x' = M x + c, closed in M's eigenvalues."""
    (p, q), (s, t) = M
    if abs((1 - p) * (1 - t) - q * s) < 1e-9:
        p, q, s, t = p * (1 - 1e-6), q * (1 - 1e-6), s * (1 - 1e-6), t * (1 - 1e-6)
    det = (1 - p) * (1 - t) - q * s
    xs = (((1 - t) * c[0] + q * c[1]) / det, (s * c[0] + (1 - p) * c[1]) / det)
    y0 = (x0[0] - xs[0], x0[1] - xs[1])
    tr_, dt = p + t, p * t - q * s
    root = math.sqrt(max(tr_ * tr_ / 4 - dt, 0.0))
    l1, l2 = tr_ / 2 + root, tr_ / 2 - root
    my = (p * y0[0] + q * y0[1], s * y0[0] + t * y0[1])

    def at(k):
        if abs(l1 - l2) < 1e-9:
            return (l1 ** k * y0[0] + xs[0], l1 ** k * y0[1] + xs[1])
        f1, f2 = (l1 ** k - l2 ** k) / (l1 - l2), (l1 * l2 ** k - l2 * l1 ** k) / (l1 - l2)
        return (f1 * my[0] + f2 * y0[0] + xs[0], f1 * my[1] + f2 * y0[1] + xs[1])

    def geo(l, k):
        return float(k) if abs(1 - l) < 1e-9 else (1 - l ** k) / (1 - l)

    def total(k):                               # sum_{i=1..k} N_i = k xs + sum M^i y0
        if abs(l1 - l2) < 1e-9:
            return k * xs[0] + l1 * geo(l1, k) * y0[0]
        s1, s2 = l1 * geo(l1, k), l2 * geo(l2, k)
        return k * xs[0] + ((s1 - s2) * my[0] + (l1 * s2 - l2 * s1) * y0[0]) / (l1 - l2)

    def sums(k):
        """sum_{i=1..k} of (N_i, u_i, N_i^2, N_i u_i): x_i = xs + v1 l1^i + v2 l2^i, so each is a sum of geometric series."""
        la, lb = l1, l2
        if abs(la - lb) < 1e-9:
            la, lb = la + 1e-6, lb - 1e-6
        v1 = ((my[0] - lb * y0[0]) / (la - lb), (my[1] - lb * y0[1]) / (la - lb))
        v2 = ((la * y0[0] - my[0]) / (la - lb), (la * y0[1] - my[1]) / (la - lb))

        def g(l):
            return l * geo(l, k)

        def lin(i):
            return k * xs[i] + v1[i] * g(la) + v2[i] * g(lb)

        def quad(i, j):
            return (k * xs[i] * xs[j] + (xs[i] * v1[j] + xs[j] * v1[i]) * g(la) + (xs[i] * v2[j] + xs[j] * v2[i]) * g(lb)
                    + v1[i] * v1[j] * g(la * la) + v2[i] * v2[j] * g(lb * lb) + (v1[i] * v2[j] + v2[i] * v1[j]) * g(la * lb))
        return lin(0), lin(1), quad(0, 0), quad(0, 1)
    at.sums = sums
    return at, total


def mean_files_pair(F0, tF, S, om, om_at, drain_bytes, dp, K, z_x, r0, B_f, B_s, n1, A, nb2, size0, chi, wb, tf, ts, acc=None, wbr=None):
    """acc (a dict, optional): the sums #2 and #4 need, in closed form with the path: merges m, sum of N, u, N^2, N u, and of N x a (a: files flushed
    during the merge proper, seconds per byte wbr), the last merge's (N, u, a), and how the stretch ends (flushes left, the next merge's files)."""
    BL, BLs = tf / wb, ts / wb
    e = BL / BLs
    c1 = r0 * F0 / S[1]
    s0 = n1 / K
    z = [0.0] + [min(z_x[i], s0) for i in range(1, dp)] + [0.0]
    N, u, tot, m, left, pieces = float(n1), z[1], float(n1), 1.0, float(A), 0

    def am_of(n, u_):                           # a's coefficients for the merge proper
        wr = wbr if wbr else wb
        bl, bls = tf / wr, ts / wr
        if n >= N_SLOW:
            return F0 / bls, S[1] / bls, 0.0
        if n + (n * F0 + u_ * S[1]) / bl < N_SLOW:
            return F0 / bl, S[1] / bl, 0.0
        return -(1 - bl / bls) + F0 / bls, S[1] / bls, N_SLOW * (1 - bl / bls)

    def am_val(n, u_):
        x, y_, w_ = am_of(n, u_)
        return x * n + y_ * u_ + w_

    def add1(n, u_):
        if acc is not None:
            am = am_val(n, u_)
            acc.update(m=acc["m"] + 1, sN=acc["sN"] + n, su=acc["su"] + u_, sNN=acc["sNN"] + n * n, sNu=acc["sNu"] + n * u_, sNa=acc["sNa"] + n * am, last=(n, u_, am))
    if acc is not None:
        acc.update(m=0, sN=0.0, su=0.0, sNN=0.0, sNu=0.0, sNa=0.0, last=None, n1=float(n1), end=None)
    add1(float(n1), u)
    if A <= 0:
        if acc is not None:
            acc["end"] = (0.0, float(n1))
        return n1, 1.0, 0

    def a_class(n, u_):
        if n >= N_SLOW:
            return 2, F0 / BLs, S[1] / BLs, 0.0
        if n + (n * F0 + u_ * S[1]) / BL < N_SLOW:
            return 0, F0 / BL, S[1] / BL, 0.0
        return 1, -(1 - e) + F0 / BLs, S[1] / BLs, N_SLOW * (1 - e)

    def add(f, g, k=1.0):
        return (f[0] + k * g[0], f[1] + k * g[1], f[2] + k * g[2])

    def gap(a0, T0, u1, zs):
        """From a merge's install to L0's next pick, level by level: the top group drains until L0's score meets it, a level joins it, or L0 passes 20 files.
        Everything stays linear in (T0, a0). (N', (aT, aa, b0), events, final group, levels' scores after the gap)."""
        zz = list(zs)
        zz[1] = u1
        T, a = T0, a0
        fT, fa = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)
        grp = {i for i in range(1, dp) if zz[i] >= T - 1e-9}
        if not grp:
            return max(a, K), (0.0, 0.0, K), ("calm",), set(), zz
        ev = []
        for _ in range(3 * dp + 6):
            zc = list(zz)
            for i in grp:
                zc[i] = T
            D = drain_bytes(S, om_at(om, size0, zc, S, dp), grp, dp)
            r = (B_f if a < N_SLOW else B_s) / D
            n_meet = max((K * T - a) / (1 + K * r), 0.0)
            n_20 = N_SLOW - a if a < N_SLOW else math.inf
            recv = max(grp) + 1 if max(grp) + 1 < dp else None
            kr = sum(S[i] for i in grp) / S[recv] if recv is not None else 0.0
            w_j, j_ = 1.0, None
            for i in range(1, dp):
                if i not in grp:
                    w = (zz[i] + kr * T) / (1 + kr) if i == recv else zz[i]
                    if w < T and w > w_j:
                        w_j, j_ = w, i
            n_j = max((T - w_j) / r, 0.0)
            if n_meet <= n_20 and n_meet <= n_j:
                k_ = K / (1 + K * r)
                fn = add((k_ * fT[0], k_ * fT[1], k_ * fT[2]), fa, K * r / (1 + K * r))
                N_ = a + n_meet
                if recv is not None:
                    zz[recv] += kr * max(T - N_ / K, 0.0)
                for i in grp:
                    zz[i] = N_ / K
                return N_, fn, tuple(ev), grp, zz
            if n_20 <= n_j:
                T -= r * n_20
                if recv is not None:
                    zz[recv] += kr * r * n_20
                fT = add(add(fT, fa, r), (0.0, 0.0, -r * N_SLOW))
                a, fa = float(N_SLOW), (0.0, 0.0, float(N_SLOW))
                ev.append("20")
            elif j_ is None:                     # score 1: nothing is picked below it, L0 fills to K
                if recv is not None:
                    zz[recv] += kr * max(T - 1.0, 0.0)
                for i in grp:
                    zz[i] = 1.0
                return max(a + n_j, K), (0.0, 0.0, K), tuple(ev) + ("floor",), grp, zz
            else:
                if j_ == recv:
                    fw = ((kr * fT[0]) / (1 + kr), (kr * fT[1]) / (1 + kr), (zz[j_] + kr * fT[2]) / (1 + kr))
                else:
                    fw = (0.0, 0.0, zz[j_])
                fa = add(fa, add(fT, fw, -1.0), 1.0 / r)
                fT = fw
                a += n_j
                if recv is not None and j_ != recv:
                    zz[recv] += kr * (T - w_j)
                T = w_j
                zz[j_] = T
                grp = grp | {j_}
                ev.append(j_)
        return a, (0.0, 0.0, a), tuple(ev) + ("cap",), grp, zz

    def step(n, u_, zs, deep_top):
        """One merge from (n, u_). (key, a's class coefficients, N', u', gap form or None, final group, scores)."""
        th, aN, au, a0 = a_class(n, u_)
        a = aN * n + au * u_ + a0
        u1 = u_ + c1 * n
        deep = max([zs[i] for i in range(2, dp)] + [0.0])
        T0 = max(u1, n / K if deep_top else deep, 1.0) if not deep_top else max(n / K, u1, 1.0)
        if a / K >= T0:
            return (th, "A"), (aN, au, a0), a, u1, None, set(), zs
        N_, fg, ev, grp, zz = gap(a, T0, u1, zs)
        reg = "B1" if 1 in grp else ("Bd" if grp else "calm")
        return (th, reg, ev), (aN, au, a0), N_, (N_ / K if reg == "B1" else u1), fg, grp, zz

    while left > 1e-6 and pieces < PAIR_PIECES:
        pieces += 1
        deep = max([z[i] for i in range(2, dp)] + [0.0])
        key, (aN, au, a0), N1, u_1, fg, grp, zz = step(N, u, z, False)
        if Y_P20 == 0 and N >= N_SLOW and N1 >= N_SLOW:
            z_a = list(z)                       # stays at 20 files or more: the cycle balance (writes slowed)
            z_a[1] = max(u, z_a[1])
            mean, k, p2 = mean_files(F0, tF, S, om, om_at, drain_bytes, dp, K, z_a, r0, B_f, B_s, N, left - N, nb2, size0, chi)
            return (tot + mean * k) / (m + k), m + k, pieces + p2
        reg = key[1]
        tr(K=K, key=key, N=round(N, 2), u=round(u, 2), N1=round(N1, 2), u1=round(u_1, 2), left=round(left, 1), z=[round(x, 2) for x in z[1:dp]], grp=sorted(grp))
        if N1 >= left:                          # the stretch ends inside this merge
            if acc is not None:
                acc["end"] = (left, N1)
            return (tot + left) / (m + left / N1), m + left / N1, pieces
        tot, m, left = tot + N1, m + 1, left - N1
        add1(N1, u_1)
        if reg == "calm" or key[-1][-1:] == ("floor",) if reg != "A" else False:
            N, u = N1, u_1
            break
        if reg == "A":
            M, c = ((aN, au), (c1, 1.0)), (a0, 0.0)
        else:
            aT, aa, b0 = fg
            if reg == "B1":
                top_l1 = u + c1 * N >= deep
                p = (aT * c1 if top_l1 else 0.0) + aa * aN
                q = (aT if top_l1 else 0.0) + aa * au
                c0 = aa * a0 + b0 + (0.0 if top_l1 else aT * deep)
                M, c = ((p, q), (p / K, q / K)), (c0, c0 / K)
            else:
                M, c = ((aT / K + aa * aN, aa * au), (c1, 1.0)), (aa * a0 + b0, 0.0)
        s_start = N1 / K
        z = list(zz) if reg != "A" else z
        N, u = N1, u_1
        z[1] = u
        at, total = _pair_form(M, c, (N, u))    # the same regime, in closed form, while (N, u) stays on its side
        n_ref = N

        def same(k):
            n_, u_ = at(k)
            if n_ < K or abs(n_ / n_ref - 1) > Y_BAND or total(k) >= left:
                return False
            return step(n_, u_, z, reg == "Bd")[0] == key
        lo, hi = 0, 1
        while hi < 4096 and same(hi):
            lo, hi = hi, hi * 2
        while hi - lo > 1:
            mid = (lo + hi) // 2
            lo, hi = (mid, hi) if same(mid) else (lo, mid)
        if lo:
            used = total(lo)
            n_end, u_end = at(lo)
            tr(piece=reg, k=lo, used=round(used, 1), N_end=round(n_end, 2), u_end=round(u_end, 2))
            tot, m, left = tot + used, m + lo, left - used
            if acc is not None:
                sn, su_, snn, snu = at.sums(lo)
                x_, y_, w_ = am_of(*at(1))
                acc.update(m=acc["m"] + lo, sN=acc["sN"] + sn, su=acc["su"] + su_, sNN=acc["sNN"] + snn, sNu=acc["sNu"] + snu,
                           sNa=acc["sNa"] + x_ * snn + y_ * snu + w_ * sn, last=(n_end, u_end, am_val(n_end, u_end)))
            if reg != "A" and grp:
                recv = max(grp) + 1
                s_end = n_end / K
                pushed = (c1 * used * S[1] if 1 in grp else 0.0) - (s_end - s_start) * sum(S[i] for i in grp)
                if recv < dp and pushed > 0:
                    z[recv] += pushed / S[recv]
                for i in grp:
                    z[i] = s_end
            N, u = n_end, u_end
            z[1] = u
    if left > 1e-6:
        tot += left
        m += left / max(N, 1.0)
        if acc is not None:                     # the rest of the stretch at this level
            k_ = left / max(N, 1.0)
            am = am_val(N, u)
            acc.update(m=acc["m"] + k_, sN=acc["sN"] + k_ * N, su=acc["su"] + k_ * u, sNN=acc["sNN"] + k_ * N * N, sNu=acc["sNu"] + k_ * N * u, sNa=acc["sNa"] + k_ * N * am, last=(N, u, am))
            acc["end"] = (0.0, max(N, 1.0))
    elif acc is not None and acc["end"] is None:
        acc["end"] = (0.0, max(N, 1.0))
    return tot / max(m, 1e-9), m, pieces
