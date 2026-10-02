#include "rules.h"

#include <algorithm>
#include <cmath>

namespace rlc {

namespace {

bool Allowed(const Mask& mask, Action action) {
  return mask[static_cast<int>(action)];
}

}  // namespace

int BestTrigger(const View& v, const LevelParts& interval, const Config& cfg) {
  const bool recent = interval.ops > 0;
  const double ops = recent ? interval.ops : v.ops;
  const double u = Ratio(recent ? interval.user_bytes : v.user_bytes, ops);
  const double gets = Ratio(recent ? interval.gets : v.gets, ops);
  const double scans = Ratio(recent ? interval.scans : v.scans, ops);
  const double F = v.F;
  double per_get = 0, per_scan = 0;
  L0ReadPrices(v, cfg, &per_get, &per_scan);
  if (v.num_levels < 2) return 0;
  // g(K) = A / K + B K, per operation; an L0 file's reads are priced with
  // their reopens (D-21).
  const double A = cfg.beta_w * cfg.c_w * u * v.m[1] * v.C[1] / F;
  const double B = cfg.beta_r / 2 * (gets * per_get + scans * per_scan);
  if (!(A > 0) || !std::isfinite(A) || !(B >= 0) || !std::isfinite(B)) return 0;
  const Bounds& b = cfg.bounds;
  if (B == 0) return b.k0_cap;
  const double star = std::sqrt(A / B);
  const auto g = [&](int K) { return A / K + B * K; };
  const auto admissible = [&](double k) {
    return static_cast<int>(std::clamp<double>(k, b.k0_min, b.k0_cap));
  };
  const int low = admissible(std::floor(star));
  const int high = admissible(std::ceil(star));
  return g(high) < g(low) ? high : low;
}

RuleChoice L0Rules(const View& v, const L0Control& c, const Mask& mask,
                   const LevelParts& interval, const Config& cfg) {
  if (cfg.HasRule("k0_tracking")) {
    const int star = BestTrigger(v, interval, cfg);
    if (star > 0) {
      if (star > std::lround(c.anchor) && Allowed(mask, Action::kExpand)) {
        return {Action::kExpand, "k0_tracking"};
      }
      if (star < v.K0 && v.k0 >= star && Allowed(mask, Action::kCompact)) {
        return {Action::kCompact, "k0_tracking"};
      }
    }
  }
  if (cfg.HasRule("l0_early") && v.running < 0 &&
      Ratio(interval.gets + interval.scans, interval.writes) >=
          cfg.rule_l0_early_read_ratio &&
      Allowed(mask, Action::kCompact)) {
    return {Action::kCompact, "l0_early"};
  }
  return {};
}

RuleChoice LevelRules(const View& v, int level, bool last, const Mask& mask,
                      const Config& cfg) {
  if (last) return {};
  if (cfg.HasRule("yield_slot") && v.k0 + 1 >= v.K0 &&
      Allowed(mask, Action::kDefer)) {
    return {Action::kDefer, "yield_slot"};
  }
  if (cfg.HasRule("garbage_hold") && std::isfinite(v.rho_tilde[level]) &&
      1 - v.rho_tilde[level] >= cfg.rule_garbage_drop &&
      Allowed(mask, Action::kDefer)) {
    return {Action::kDefer, "garbage_hold"};
  }
  if (cfg.HasRule("neighbour_release") && level + 1 <= v.last &&
      v.phi[level + 1] / v.m[level + 1] <= cfg.rule_release_fill &&
      Allowed(mask, Action::kCompact)) {
    return {Action::kCompact, "neighbour_release"};
  }
  return {};
}

}  // namespace rlc
