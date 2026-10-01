// The decision modes built so far (plan §3, §7 step 8): hold-only (ACT-4,
// ARCH-5 parity) and rules (Gate N3). Prior-only, learned and
// remote-inference are step 10; the config refuses them.
#pragma once

#include <string>

#include "config.h"
#include "masks.h"
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

}  // namespace rlc
