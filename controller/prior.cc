#include "prior.h"

#include <algorithm>
#include <cmath>

namespace rlc {

namespace {

constexpr int kCompact = static_cast<int>(Action::kCompact);
constexpr int kDefer = static_cast<int>(Action::kDefer);
constexpr int kExpand = static_cast<int>(Action::kExpand);

double Known(double x) { return std::isfinite(x) ? x : 0.0; }

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
  out[kCompact] = cfg.beta_w * merged * released *
                      Known(Ratio(v.overlap[j], f) * (o_now - f)) +
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
  out[kDefer] =
      cfg.beta_s * sigma * garbage * held + cfg.beta_w * overflow * rewrite;

  // Expand: the space bound's growth, every added byte counted as held.
  out[kExpand] = cfg.beta_s * sigma *
                 std::max(0.0, Act(c, Action::kExpand, phi, b).m() - m);
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
  const auto write = [&](double K) {
    return cfg.beta_w * cfg.c_w * u * m1C1 / (K * F);
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
