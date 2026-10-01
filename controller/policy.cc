#include "policy.h"

namespace rlc {

namespace {

Choice FromRule(const RuleChoice& rule, const Mask& mask) {
  if (!mask[static_cast<int>(rule.action)]) {
    return {Action::kHold, std::string("masked:") + rule.rule};
  }
  if (rule.action == Action::kHold) return {Action::kHold, "rules:none"};
  return {rule.action, std::string("rule:") + rule.rule};
}

}  // namespace

Choice DecideL0(const Config& cfg, const View& v, const L0Control& c,
                const Mask& mask, const LevelParts& interval) {
  if (cfg.mode == Mode::kHoldOnly) return {Action::kHold, "hold-only"};
  return FromRule(L0Rules(v, c, mask, interval, cfg), mask);
}

Choice DecideLevel(const Config& cfg, const View& v, int level, bool last,
                   const Mask& mask) {
  if (cfg.mode == Mode::kHoldOnly) return {Action::kHold, "hold-only"};
  return FromRule(LevelRules(v, level, last, mask, cfg), mask);
}

}  // namespace rlc
