#include "masks.h"

#include <algorithm>
#include <cmath>

namespace rlc {

bool Admissible(const std::vector<double>& m, double T, const Bounds& b) {
  if (m.empty() || m[0] != 1.0) return false;
  for (size_t i = 1; i < m.size(); ++i) {
    if (!std::isfinite(m[i]) || m[i] < b.m_min || m[i] > b.m_max) return false;
    if (i + 1 < m.size() && m[i + 1] * T < m[i]) return false;
  }
  return true;
}

Mask LevelMask(const std::vector<double>& m, int level, bool last,
               const LevelControl& c, double phi, double T, const Bounds& b) {
  const double s = phi / c.m();
  const auto keeps_order = [&](Action action) {
    const double v = Act(c, action, phi, b).m();
    if (!(v >= b.m_min && v <= b.m_max)) return false;
    if (level + 1 < static_cast<int>(m.size()) && m[level + 1] * T < v) {
      return false;
    }
    return level - 1 < 1 || v * T >= m[level - 1];
  };
  Mask mask{};
  mask[static_cast<int>(Action::kHold)] = true;
  if (!last) {
    mask[static_cast<int>(Action::kCompact)] =
        s < 1 && phi >= b.phi_min && keeps_order(Action::kCompact);
    mask[static_cast<int>(Action::kDefer)] = s >= 1 - b.epsilon &&
                                             phi * (1 + b.epsilon) <= b.m_max &&
                                             keeps_order(Action::kDefer);
  }
  mask[static_cast<int>(Action::kExpand)] =
      c.anchor < b.m_max && keeps_order(Action::kExpand);
  return mask;
}

Mask L0Mask(const L0Control& c, int k0, int K0, const Bounds& b) {
  Mask mask{};
  mask[static_cast<int>(Action::kHold)] = true;
  mask[static_cast<int>(Action::kCompact)] = k0 >= b.k0_min && k0 < K0;
  mask[static_cast<int>(Action::kDefer)] = k0 >= K0 && k0 + 1 <= b.k0_cap;
  mask[static_cast<int>(Action::kExpand)] = c.anchor < b.k0_cap;
  return mask;
}

std::vector<int> Repair(std::vector<double>* m, double T, const Bounds& b) {
  std::vector<int> changed;
  if (m->empty()) return changed;
  (*m)[0] = 1.0;
  for (size_t i = 1; i < m->size(); ++i) {
    double v = std::clamp((*m)[i], b.m_min, b.m_max);
    if (i >= 2 && v * T < (*m)[i - 1]) {
      v = (*m)[i - 1] / T;
      while (v * T < (*m)[i - 1]) v = std::nextafter(v, b.m_max * 2);
    }
    if (v != (*m)[i]) {
      (*m)[i] = v;
      changed.push_back(static_cast<int>(i));
    }
  }
  return changed;
}

Action PickAllowed(const Mask& mask, double u) {
  const int allowed =
      static_cast<int>(std::count(mask.begin(), mask.end(), true));
  int pick = std::min(static_cast<int>(u * allowed), allowed - 1);
  for (int a = 0; a < kNumActions; ++a) {
    if (mask[a] && pick-- == 0) return static_cast<Action>(a);
  }
  return Action::kHold;
}

}  // namespace rlc
