// Gate N3's hand-written rules (PATHWAYS "Execution order", Gate N3;
// Pathway A §4 (b), (c), (e), (f); Proposition D.11). Each is switched on by
// name in the config's "rules". A rule proposes an action only if the mask
// allows it; the first rule that proposes one wins, otherwise hold.
//
// L0, in order:
//   k0_tracking  track D.11's best admissible trigger K*: expand while the
//                anchor is below K*, compact once k0 >= K* while K* is below
//                the trigger in effect
//   l0_early     compact while the slot is idle and reads are heavy: (Gets +
//                scans) per write over L0's last interval at least
//                rule_l0_early_read_ratio
// Interior levels, in order:
//   yield_slot         defer while L0 is due or within one flush of due
//                      (k0 + 1 >= K0), so L0 does not wait for the slot
//   garbage_hold       defer while the level's merges drop a share
//                      1 - rho~ >= rule_garbage_drop
//   neighbour_release  compact while the level below is at most
//                      rule_release_fill of its target (overlap is cheap,
//                      Lemma D.8)
// The last level has no rule and holds.
#pragma once

#include "attribution.h"
#include "config.h"
#include "masks.h"
#include "state.h"

namespace rlc {

struct RuleChoice {
  Action action = Action::kHold;
  const char* rule = "none";
};

// D.11's best admissible trigger at L0's rates over `interval` (the run so
// far when the interval served nothing) and the configured prices; 0 when an
// input is not measured yet.
int BestTrigger(const View& v, const LevelParts& interval, const Config& cfg);

RuleChoice L0Rules(const View& v, const L0Control& c, const Mask& mask,
                   const LevelParts& interval, const Config& cfg);

RuleChoice LevelRules(const View& v, int level, bool last, const Mask& mask,
                      const Config& cfg);

}  // namespace rlc
