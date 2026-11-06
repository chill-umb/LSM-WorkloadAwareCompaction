#include "learned2.h"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <limits>

namespace rlc {

namespace {
std::atomic<int> g_f2_inputs{1};
}  // namespace

void SetF2Inputs(int version) { g_f2_inputs.store(version == 2 ? 2 : 1); }

int F2Inputs() { return g_f2_inputs.load(); }

const std::vector<std::string>& F2FeatureNames() {
  static const std::vector<std::string> kNames = {
      "put_share", "get_share", "scan_share", "log10_h", "log10_op", "log2_k",
      "log2_m1",   "log2_m2",   "log2_m3",    "log2_m4", "log2_m5",  "log2_m6"};
  static const std::vector<std::string> kNames2 = [] {
    std::vector<std::string> v = kNames;
    for (const char* name : {"up0_x_pending", "dn0_x_pending", "top_is_l0",
                             "prev_deep_bpo", "fpos_per_get", "stall"}) {
      v.push_back(name);
    }
    return v;
  }();
  return F2Inputs() == 2 ? kNames2 : kNames;
}

F2State F2StateInputs(int k_prev, int k_window, double pending_bytes,
                      double base_bytes, int l0_files, double deep_max_score,
                      double prev_deep_bytes, double prev_ops,
                      double prev_block_probes, double prev_gets,
                      double prev_stall_ends, double prev_ends) {
  const auto finite_or = [](double x, double fallback) {
    return std::isfinite(x) ? x : fallback;
  };
  F2State s;
  const double pending =
      base_bytes > 0
          ? finite_or(std::log1p(std::max(0.0, pending_bytes) / base_bytes), 0)
          : 0;
  if (k_prev > 0 && k_window > k_prev) s.up0_x_pending = pending;
  if (k_prev > 0 && k_window > 0 && k_window < k_prev) s.dn0_x_pending = pending;
  if (k_window > 0) {
    const double l0_score =
        static_cast<double>(l0_files) / static_cast<double>(k_window);
    s.top_is_l0 = l0_score >= finite_or(deep_max_score, 0) ? 1 : 0;
  }
  s.prev_deep_bpo = finite_or(
      std::log1p(std::max(0.0, prev_deep_bytes) / std::max(1.0, prev_ops)), 0);
  s.fpos_per_get =
      prev_gets > 0 ? finite_or(prev_block_probes / prev_gets, 0) : 0;
  s.stall = prev_ends > 0 ? finite_or(prev_stall_ends / prev_ends, 0) : 0;
  return s;
}

const char* F2ActionName(Action action) {
  switch (action) {
    case Action::kHold:
      return "hold";
    case Action::kCompact:
      return "down";
    case Action::kDefer:
      return "up";
    case Action::kExpand:
      return "unused";
  }
  return "unknown";
}

std::vector<double> F2Features(double put_share, double get_share,
                               double scan_share, double live_bytes,
                               uint64_t op, int K,
                               const std::vector<double>& m,
                               const F2State& state) {
  const auto finite_or = [](double x, double fallback) {
    return std::isfinite(x) ? x : fallback;
  };
  std::vector<double> x(F2FeatureNames().size(), 0.0);
  x[kF2PutShare] = finite_or(put_share, 0);
  x[kF2GetShare] = finite_or(get_share, 0);
  x[kF2ScanShare] = finite_or(scan_share, 0);
  x[kF2LogH] = live_bytes > 0 ? std::log10(live_bytes) : 0;
  x[kF2LogOp] = std::log10(static_cast<double>(op) + 1);
  x[kF2LogK] = K > 0 ? std::log2(static_cast<double>(K)) : 0;
  for (int i = 1; i <= kF2Levels; ++i) {
    const double mi = i < static_cast<int>(m.size()) ? m[i] : 1.0;
    x[kF2LogM1 + i - 1] = mi > 0 && std::isfinite(mi) ? std::log2(mi) : 0;
  }
  if (x.size() > static_cast<size_t>(kF2Stall)) {
    x[kF2Up0Pending] = finite_or(state.up0_x_pending, 0);
    x[kF2Dn0Pending] = finite_or(state.dn0_x_pending, 0);
    x[kF2TopIsL0] = finite_or(state.top_is_l0, 0);
    x[kF2PrevDeepBpo] = finite_or(state.prev_deep_bpo, 0);
    x[kF2FposPerGet] = finite_or(state.fpos_per_get, 0);
    x[kF2Stall] = finite_or(state.stall, 0);
  }
  return x;
}

F2Choice F2Decide(const std::vector<const Model*>& models,
                  const std::vector<double>& x, int own,
                  const std::array<double, kNumActions>& after,
                  const Mask& mask, double explore, double vote_share,
                  double u_explore, double u_pick, double min_effect) {
  F2Choice c;
  const double nan = std::numeric_limits<double>::quiet_NaN();
  c.mean.fill(nan);
  c.members = static_cast<int>(models.size());
  // Per member and slot: the predicted cost of the setting the slot leads to.
  std::vector<std::array<double, kNumActions>> cost(models.size());
  for (size_t e = 0; e < models.size(); ++e) {
    cost[e].fill(nan);
    for (int a = 0; a < kNumActions; ++a) {
      if (!mask[a]) continue;
      std::vector<double> xa = x;
      if (a != 0 && own >= 0 && own < static_cast<int>(xa.size())) {
        xa[own] = after[a];
      }
      cost[e][a] = Forward(*models[e], xa)[0];
    }
  }
  for (int a = 0; a < kNumActions; ++a) {
    if (!mask[a] || models.empty()) continue;
    double sum = 0;
    for (const auto& ce : cost) sum += ce[a];
    c.mean[a] = sum / static_cast<double>(models.size());
  }
  // Exploration first, so its draw does not depend on the model.
  std::vector<int> moves;
  for (int a = 1; a < kNumActions; ++a) {
    if (mask[a]) moves.push_back(a);
  }
  if (!moves.empty() && u_explore < explore) {
    size_t i = static_cast<size_t>(u_pick * static_cast<double>(moves.size()));
    if (i >= moves.size()) i = moves.size() - 1;
    c.action = static_cast<Action>(moves[i]);
    c.reason = "explore";
    return c;
  }
  if (models.empty() || !mask[0]) {
    c.action = Action::kHold;
    c.reason = models.empty() ? "cold" : "hold";
    return c;
  }
  int best = 0;
  for (int a = 1; a < kNumActions; ++a) {
    if (mask[a] && std::isfinite(c.mean[a]) && c.mean[a] < c.mean[best]) best = a;
  }
  c.greedy = static_cast<Action>(best);
  if (best == 0) {
    c.reason = "hold";
    return c;
  }
  for (const auto& ce : cost) c.votes += ce[best] < ce[0] ? 1 : 0;
  c.need = static_cast<int>(
      std::ceil(vote_share * static_cast<double>(models.size()) - 1e-9));
  if (c.need < 1) c.need = 1;
  if (c.votes < c.need) {
    c.reason = "gate";
  } else if (c.mean[best] > c.mean[0] - min_effect) {
    c.reason = "small";
  } else {
    c.action = c.greedy;
    c.reason = "confident";
  }
  return c;
}

F2Verdict F2TrialVerdict(const std::vector<const Model*>& models,
                         const std::vector<std::vector<double>>& xs, int own,
                         double v_inc, double v_cand, double vote_share,
                         double min_effect) {
  F2Verdict v;
  v.members = static_cast<int>(models.size());
  v.need = static_cast<int>(
      std::ceil(vote_share * static_cast<double>(models.size()) - 1e-9));
  if (v.need < 1) v.need = 1;
  if (models.empty() || xs.empty()) return v;
  double sum = 0;
  for (const Model* model : models) {
    double d = 0;
    for (const std::vector<double>& x : xs) {
      std::vector<double> a = x, b = x;
      if (own >= 0 && own < static_cast<int>(x.size())) {
        a[own] = v_cand;
        b[own] = v_inc;
      }
      d += Forward(*model, a)[0] - Forward(*model, b)[0];
    }
    d /= static_cast<double>(xs.size());
    sum += d;
    v.cheaper += d < 0 ? 1 : 0;
    v.dearer += d > 0 ? 1 : 0;
  }
  v.effect = sum / static_cast<double>(models.size());
  if (v.cheaper >= v.need && v.effect <= -min_effect) {
    v.verdict = 1;
  } else if (v.dearer >= v.need && v.effect >= min_effect) {
    v.verdict = -1;
  }
  return v;
}

}  // namespace rlc
