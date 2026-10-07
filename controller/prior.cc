#include "prior.h"

#include <algorithm>
#include <cmath>

namespace rlc {

namespace {

constexpr int kCompact = static_cast<int>(Action::kCompact);
constexpr int kDefer = static_cast<int>(Action::kDefer);
constexpr int kExpand = static_cast<int>(Action::kExpand);

double Known(double x) { return std::isfinite(x) ? x : 0.0; }

// values[i], or NaN when the View does not carry it.
double At(const std::vector<double>& values, int i) {
  return i >= 0 && i < static_cast<int>(values.size()) ? values[i] : kNaN;
}

// Cost model 2 (H §7 as amended 2026-10-03, D-23): the growth of level j's
// hidden-step charge when its held bytes grow by `held` (in C_j units), at
// c_st per step, over one turnover and in units of c_w C_j: the charge is
// assumed to grow in proportion to the level's bytes (0 until measured).
double HiddenGrowth(const View& v, int j, double held, const Config& cfg) {
  if (cfg.cost_model != 2 || !(held > 0)) return 0;
  const double per_op = Ratio(At(v.hidden, j), v.ops);
  return Known(cfg.beta_r * cfg.v2.c_st * per_op * Ratio(held, v.phi[j]) *
               v.N[j] / (cfg.c_w * v.C[j]));
}

// The actual charge (cfg.actual_charge; owner, 2026-10-06): level j's
// reward also pays the merges from level j - 1 into it and every level below
// it (rl_agent/learner/reward.py, actual_extra), so its prior prices what an
// action that changes the level's held bytes by delta (in C_j units: + held
// longer, - released early) does to them. Both terms over one turnover of
// level j, N_j operations, in units of c_w C_j; positive is worse than hold.
//
// (P1) The merges from level j - 1 read and write the bytes of level j they
// overlap, so they grow with level j's bytes. Each extra overlap byte is
// read (c_cr) and written (c_w) at beta_W, and moves the merge's
// interference by (1 + lambda) / Y(o_bar) of its charge per source byte (Y
// linear in the overlap, as the compact term scales it), at level j - 1's
// measured rho, overlap and interference. L1 takes L0's model (L0Prior):
// u / (K F) L0 merges per operation, each overlapping all of L1, so delta
// C_1 more bytes each. Deeper levels take Lemma D.8's, as the compact term
// does one level up: o = c_{j-1} T phi_j / phi_{j-1} per source byte of
// level j - 1, c_{j-1} = overlap / f_{j-1}, over its merged release in
// steady state, (1 - xi_{j-1}) C_{j-1} N_j / N_{j-1} bytes per turnover of
// level j. The number of merges does not move (H §7 (3)).
double InflowMerges(const View& v, int j, double delta, const Config& cfg) {
  if (j < 1 || j >= v.num_levels || delta == 0) return 0;
  const int up = j - 1;
  const double rho = Known(At(v.rho, up));
  const double o_bar = Known(At(v.overlap, up));
  const double y = (rho + o_bar) + cfg.v2.lambda * (1 + o_bar);
  const double intf = cfg.beta_r * Known(At(v.intf_rd, up)) +
                      cfg.beta_w * Known(At(v.intf_wr, up));
  const double per_byte = cfg.beta_w * (1 + cfg.v2.c_cr / cfg.c_w) +
                          Known(intf * Ratio(1 + cfg.v2.lambda, y));
  double extra = 0;  // extra overlap bytes per turnover of level j, / C_j
  if (up == 0) {
    const double merges = Ratio(Ratio(v.user_bytes, v.ops), v.K0 * v.F);
    extra = Known(At(v.N, j) * merges) * delta;
  } else {
    const double c = Ratio(At(v.overlap, up), Fanout(v, up));
    extra = Known((1 - Known(At(v.xi, up))) * Ratio(v.C[up], v.C[j]) *
                  Ratio(At(v.N, j), At(v.N, up)) * c * v.T *
                  Ratio(delta, At(v.phi, up)));
  }
  return per_byte * extra;
}

// (P2) What level j keeps does not land in level j + 1: its bytes change by
// -rho~_j delta C_j, and with them the hidden-step charge of level j + 1
// (the steps over level j + 2's entries, v.hidden[j + 1]). The same
// approximation as HiddenGrowth: that charge grows in proportion to the
// level's bytes, at c_st per step (0 until measured).
double HiddenBelow(const View& v, int j, double delta, const Config& cfg) {
  const int below = j + 1;
  if (below >= v.num_levels || delta == 0) return 0;
  const double landed = -Known(At(v.rho_tilde, j)) * delta * v.C[j];
  const double relative = Ratio(landed, At(v.phi, below) * v.C[below]);
  const double per_op = Ratio(At(v.hidden, below), v.ops);
  return Known(cfg.beta_r * cfg.v2.c_st * per_op * relative * At(v.N, j) /
               (cfg.c_w * v.C[j]));
}

Values Clip(Values b, double b_max) {
  for (double& x : b) x = std::clamp(Known(x), -b_max, b_max);
  return b;
}

// L0's reads per operation since the start, priced, its reopens (D-21)
// included.
double L0ReadCost(const View& v, const Config& cfg) {
  return Known(Ratio(v.probes[0] * cfg.c_f + v.fp_reads[0] * cfg.c_blk +
                         v.seeks[0] * cfg.c_sk +
                         (v.get_reopens[0] + v.iter_reopens[0]) * cfg.c_open,
                     v.ops));
}

// The mean of (k0 - K0)^+ / k0 over a job of job_ops operations starting now,
// L0 gaining one file per flush (the next one after the memtable fills).
double SlotShare(const View& v, double job_ops) {
  const double per_flush = Ratio(v.ops, v.flushes);
  if (!(per_flush > 0) || !(job_ops > 0)) return 0;
  const auto share = [&](double k) { return k > v.K0 ? (k - v.K0) / k : 0.0; };
  const double k_end =
      v.k0_all + std::floor(Known(v.mem_fill) + job_ops / per_flush);
  return (share(v.k0_all) + share(k_end)) / 2;
}

}  // namespace

Values LevelPrior(const View& v, int j, const LevelControl& c,
                  const Config& cfg) {
  const Bounds& b = cfg.bounds;
  const double phi = v.phi[j];
  const double m = c.m();
  const bool below = j + 1 < v.num_levels;
  const double sigma = Known(cfg.c_s * v.N[j] / (cfg.c_w * cfg.q_bar));
  Values out{};

  // Compact: bytes released before RocksDB would have released them, at the
  // overlap the level below gives now rather than at its target. Only the
  // merged share 1 - xi_j writes; trivially moved bytes write nothing
  // (Lemma D.7, G.2), and c_j is measured over merges.
  const double released =
      phi < m ? phi - Act(c, Action::kCompact, phi, b).m() : 0.0;
  const double f = Fanout(v, j);
  const double o_now = below ? Ratio(v.T * v.phi[j + 1], phi) : kNaN;
  const double job_ops = Known(v.job_ops[j]);
  const double merged = 1 - Known(v.xi[j]);
  const bool v2 = cfg.cost_model == 2;
  // Cost model 2: the extra overlap is read as well as written (c_cr), and
  // the merges' interference moves with their bytes Y = X + lambda (S + O)
  // per source byte, Y(o) = (rho_j + o) + lambda (1 + o), from the level's
  // realised charge per merged source byte. The read-cost timing part
  // (rho_now against the merges' mean, H §7 (2)) contributes 0 until the
  // read intensity reaches the prior.
  const double cr = v2 ? cfg.v2.c_cr / cfg.c_w : 0.0;
  const double extra = Known(Ratio(v.overlap[j], f) * (o_now - f));
  double interference = 0;
  if (v2) {
    const double rho = Known(v.rho[j]);
    const auto y = [&](double o) {
      return (rho + o) + cfg.v2.lambda * (1 + o);
    };
    const double o_bar = Known(v.overlap[j]);
    const double o_c = Known(Ratio(v.overlap[j], f) * o_now);
    const double per_byte = cfg.beta_r * Known(At(v.intf_rd, j)) +
                            cfg.beta_w * Known(At(v.intf_wr, j));
    interference =
        merged * released * per_byte * Known(Ratio(y(o_c), y(o_bar)) - 1);
  }
  out[kCompact] = cfg.beta_w * merged * released * (1 + cr) * extra +
                  interference +
                  cfg.beta_r * SlotShare(v, job_ops) * L0ReadCost(v, cfg) *
                      job_ops / (cfg.c_w * v.C[j]);

  // Defer: the garbage the held bytes keep, and the part of the later burst
  // the level below has no room for, rewritten from there.
  const double held = std::max(0.0, Act(c, Action::kDefer, phi, b).m() - m);
  const double garbage = std::isfinite(v.rho_tilde[j]) ? 1 - v.rho_tilde[j] : 0;
  const double headroom =
      below ? v.T * std::max(0.0, v.m[j + 1] - v.phi[j + 1]) : 0.0;
  const double overflow =
      std::max(0.0, Known(v.rho_tilde[j]) * held - headroom);
  const double rewrite = below ? Known(v.rho[j + 1] + v.overlap[j + 1]) : 0.0;
  // Cost model 2: the overflow is merged at level j + 1, read with its
  // overlap (c_cr), at that level's per-job price and interference per
  // merged source byte (H §7, deferral (4)).
  double below_merge = 0;
  if (v2 && below) {
    below_merge =
        cfg.beta_w * (cr * (1 + Known(v.overlap[j + 1])) +
                      Known(At(v.c_job, j + 1))) +
        cfg.beta_r * Known(At(v.intf_rd, j + 1)) +
        cfg.beta_w * Known(At(v.intf_wr, j + 1));
  }
  out[kDefer] = cfg.beta_s * sigma * garbage * held +
                cfg.beta_w * overflow * rewrite + overflow * below_merge +
                HiddenGrowth(v, j, held, cfg);

  // Expand: the space bound's growth, every added byte counted as held, and
  // the hidden-step growth as for a deferral.
  const double grown = std::max(0.0, Act(c, Action::kExpand, phi, b).m() - m);
  out[kExpand] =
      cfg.beta_s * sigma * grown + HiddenGrowth(v, j, grown, cfg);

  // The actual charge: what each action does to the merges into the level
  // and to the level below (P1, P2 above).
  if (v2 && cfg.actual_charge) {
    const auto pushed = [&](double delta) {
      return InflowMerges(v, j, delta, cfg) + HiddenBelow(v, j, delta, cfg);
    };
    out[kCompact] += pushed(-released);
    out[kDefer] += pushed(held);
    out[kExpand] += pushed(grown);
  }
  return Clip(out, cfg.b_max);
}

Values L0Prior(const View& v, const L0Control& c, const Config& cfg) {
  const Bounds& b = cfg.bounds;
  const double F = v.F;  // held fixed from the first flush (H §3)
  const double u = Ratio(v.user_bytes, v.ops);
  const double gets = Ratio(v.gets, v.ops);
  const double scans = Ratio(v.scans, v.ops);
  double per_get = 0, per_scan = 0;
  L0ReadPrices(v, cfg, &per_get, &per_scan);
  const double m1C1 = v.num_levels > 1 ? v.m[1] * v.C[1] : kNaN;
  // Cost model 2 adds each L0 merge's per-job price and the compaction
  // read of L1's overlap, 1 / (K Q_F) merges per operation (H §7, g_J); the
  // KF bytes every merge reads and writes are the same per operation at any
  // K and cancel from every difference.
  const double per_merge =
      cfg.cost_model == 2
          ? cfg.JobPrice(kL0Merge) + cfg.v2.c_cr * m1C1
          : 0.0;
  const auto write = [&](double K) {
    return cfg.beta_w * (cfg.c_w * m1C1 + per_merge) * u / (K * F);
  };
  const auto read = [&](double K) {
    return cfg.beta_r * (K / 2) * (gets * per_get + scans * per_scan);
  };
  const auto cost = [&](double K) { return write(K) + read(K); };
  // Over one turnover, N_0 operations, divided by c_w C_0.
  const double norm = Ratio(v.N[0], cfg.c_w * v.k0_cfg * F);
  const double K = v.K0;
  Values out{};
  const double compact = std::max(b.k0_min, v.k0);
  out[kCompact] = norm * (write(compact) - write(K) +
                          (v.running < 0 ? read(compact) - read(K) : 0.0));
  const double defer = std::min(b.k0_cap, v.k0 + 1);
  out[kDefer] = norm * (cost(defer) - cost(K));
  const double expand = ActL0(c, Action::kExpand, v.k0, b).K0(b);
  out[kExpand] = norm * (cost(expand) - cost(K));
  return Clip(out, cfg.b_max);
}

}  // namespace rlc
