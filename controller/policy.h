// The decision modes (plan §3): hold-only (ACT-4, ARCH-5 parity), rules
// (Gate N3), and, from plan step 10, prior-only (Q = -b) and learned
// (Q = -b + f_theta + delta_j, mlp.h). Remote-inference (Gate N6) is not
// built; the config refuses it.
#pragma once

#include <string>

#include "config.h"
#include "masks.h"
#include "prior.h"
#include "rules.h"
#include "state.h"

namespace rlc {

struct Choice {
  Action action = Action::kHold;
  std::string reason;  // "hold-only", "rule:<name>", "rules:none"
};

// Never returns an action the mask forbids.
Choice DecideL0(const Config& cfg, const View& v, const L0Control& c,
                const Mask& mask, const LevelParts& interval);
Choice DecideLevel(const Config& cfg, const View& v, int level, bool last,
                   const Mask& mask);

// The learner modes (H §4, §5): with probability explore (u_explore < cfg's
// explore), an allowed action uniformly at random (u_pick); otherwise the
// allowed action with the largest Q, ties to the lowest index (hold first).
// Never a masked action (ARCH-2). `tag` names the source of Q in the
// reason: "prior", "learned" or "cold" (learned mode before any weights).
Choice DecideLearner(const Values& q, const Mask& mask, double explore,
                     double u_explore, double u_pick, const char* tag);

}  // namespace rlc
