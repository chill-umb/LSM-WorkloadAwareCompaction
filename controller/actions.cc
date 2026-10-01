#include "actions.h"

#include <algorithm>
#include <cmath>

namespace rlc {

const char* ActionName(Action action) {
  switch (action) {
    case Action::kHold:
      return "hold";
    case Action::kCompact:
      return "compact";
    case Action::kDefer:
      return "defer";
    case Action::kExpand:
      return "expand";
  }
  return "unknown";
}

namespace {

// ponytail: the snap distance is fixed at epsilon / 10. If ACT-2 finds the
// relaxation calls too frequent, make it a preregistered tolerance.
double Toward(double value, double rest, double turnovers, double kappa,
              double snap) {
  if (turnovers <= 0) return value;
  const double v = rest + (value - rest) * std::exp(-turnovers / kappa);
  return std::fabs(v - rest) <= snap ? rest : v;
}

}  // namespace

LevelControl Act(const LevelControl& c, Action action, double phi,
                 const Bounds& b) {
  LevelControl r = c;
  switch (action) {
    case Action::kHold:
      break;
    case Action::kCompact:
      r.timing = phi / (c.anchor * (1 + b.epsilon));
      break;
    case Action::kDefer:
      r.timing = phi * (1 + b.epsilon) / c.anchor;
      break;
    case Action::kExpand:
      r.anchor = std::min(b.alpha * c.anchor, b.m_max);
      r.timing = std::max(1.0, phi * (1 + b.epsilon) / r.anchor);
      break;
  }
  return r;
}

LevelControl Relax(const LevelControl& c, double turnovers, const Bounds& b) {
  const double snap = b.epsilon / 10;
  return {Toward(c.anchor, 1, turnovers, b.kappa_a, snap),
          Toward(c.timing, 1, turnovers, b.kappa_d, snap)};
}

int L0Control::K0(const Bounds& b) const {
  const long k = std::lround(anchor + offset);
  return static_cast<int>(std::clamp<long>(k, b.k0_min, b.k0_cap));
}

L0Control ActL0(const L0Control& c, Action action, int k0, const Bounds& b) {
  L0Control r = c;
  switch (action) {
    case Action::kHold:
      break;
    case Action::kCompact:
      r.offset = std::max(b.k0_min, k0) - c.anchor;
      break;
    case Action::kDefer:
      r.offset = std::min(b.k0_cap, k0 + 1) - c.anchor;
      break;
    case Action::kExpand:
      r.anchor = std::min(c.anchor + 1, static_cast<double>(b.k0_cap));
      r.offset = std::max(0.0, std::min(b.k0_cap, k0 + 1) - r.anchor);
      break;
  }
  return r;
}

L0Control RelaxL0(const L0Control& c, double turnovers, int k0_configured,
                  const Bounds& b) {
  const double snap = b.epsilon / 10;
  return {Toward(c.anchor, k0_configured, turnovers, b.kappa_a, snap),
          Toward(c.offset, 0, turnovers, b.kappa_d, snap)};
}

}  // namespace rlc
