// Gate N3's rules (rules.h) and the two built modes (policy.h).
#include "rules.h"

#include "gtest/gtest.h"
#include "policy.h"
#include "test_util.h"

namespace rlc {
namespace {

using test::SetPhi;
using test::TestBounds;
using test::TestConfig;
using test::TestView;

// D.11 at TestView's rates: g(K) = 3276.8 / K + 128 K per operation, so K*
// = sqrt(25.6) = 5.06 and g(5) < g(6).
TEST(Rules, BestTriggerIsD11sBestAdmissibleInteger) {
  View v = TestView();
  const Config cfg = TestConfig();
  EXPECT_EQ(BestTrigger(v, {}, cfg), 5);
  // Scan-heavy: K* = 0.57, clamped to k0_min.
  v.scans = 1e6;
  EXPECT_EQ(BestTrigger(v, {}, cfg), 2);
  // No reads at all: the cap.
  v.scans = 0;
  v.gets = 0;
  EXPECT_EQ(BestTrigger(v, {}, cfg), 8);
  // Nothing flushed yet, so F is not fixed: unknown.
  v = TestView();
  v.flushes = 0;
  v.flush_bytes = 0;
  v.F = kNaN;
  EXPECT_EQ(BestTrigger(v, {}, cfg), 0);
  // D.11's F is the fixed flush size, not the running mean.
  v = TestView();
  v.flush_bytes = 80 * test::kMiB;
  EXPECT_EQ(BestTrigger(v, {}, cfg), 5);
  // L0's last interval, when it served operations, sets the rates.
  v = TestView();
  LevelParts interval;
  interval.ops = 1000;
  interval.user_bytes = 409.6 * 1000;
  interval.gets = 500;
  interval.scans = 100 * 100;  // 10 scans per operation
  EXPECT_EQ(BestTrigger(v, interval, cfg), 2);
}

TEST(Rules, K0TrackingMovesTheTriggerTowardKStar) {
  const Bounds b = TestBounds();
  const Config cfg = TestConfig(Mode::kRules, {"k0_tracking"});
  View v = TestView();  // K* = 5, trigger 4, k0 = 2
  const L0Control c{4, 0};
  EXPECT_EQ(L0Rules(v, c, L0Mask(c, v.k0, v.K0, b), {}, cfg).action,
            Action::kExpand);
  // K* = 2 below the trigger 4 with k0 = 2: L0 is made due now.
  v.scans = 1e6;
  EXPECT_EQ(L0Rules(v, c, L0Mask(c, v.k0, v.K0, b), {}, cfg).action,
            Action::kCompact);
  // K* = 4 = the trigger: hold. 0.5 * 112 + s * 2000 = 409.6 gives K* = 4.
  v.scans = 17680;
  EXPECT_EQ(L0Rules(v, c, L0Mask(c, v.k0, v.K0, b), {}, cfg).action,
            Action::kHold);
}

TEST(Rules, L0EarlyNeedsAnIdleSlotAndHeavyReads) {
  const Bounds b = TestBounds();
  const Config cfg = TestConfig(Mode::kRules, {"l0_early"});  // ratio >= 1
  View v = TestView();
  const L0Control c{4, 0};
  const Mask mask = L0Mask(c, v.k0, v.K0, b);
  LevelParts interval;
  interval.gets = 900;
  interval.scans = 100;
  interval.writes = 500;
  EXPECT_EQ(L0Rules(v, c, mask, interval, cfg).action, Action::kCompact);
  EXPECT_STREQ(L0Rules(v, c, mask, interval, cfg).rule, "l0_early");
  v.running = 1;  // the slot is busy
  EXPECT_EQ(L0Rules(v, c, mask, interval, cfg).action, Action::kHold);
  v.running = -1;
  interval.writes = 2000;  // reads light
  EXPECT_EQ(L0Rules(v, c, mask, interval, cfg).action, Action::kHold);
}

Mask MaskAt(const View& v, int level) {
  return LevelMask(v.m, level, level == v.last, {}, v.phi[level], v.T,
                   TestBounds());
}

TEST(Rules, YieldSlotDefersWhileL0IsDueOrOneFlushShort) {
  const Config cfg = TestConfig(Mode::kRules, {"yield_slot"});
  View v = TestView();
  SetPhi(&v, 2, 0.97);
  v.k0 = 3;  // trigger 4: one flush short
  EXPECT_EQ(LevelRules(v, 2, false, MaskAt(v, 2), cfg).action, Action::kDefer);
  v.k0 = 2;
  EXPECT_EQ(LevelRules(v, 2, false, MaskAt(v, 2), cfg).action, Action::kHold);
  // Far from due, deferring is masked anyway.
  v.k0 = 5;
  SetPhi(&v, 2, 0.5);
  EXPECT_EQ(LevelRules(v, 2, false, MaskAt(v, 2), cfg).action, Action::kHold);
}

TEST(Rules, GarbageHoldDefersWhileMergesDropEnough) {
  const Config cfg = TestConfig(Mode::kRules, {"garbage_hold"});  // >= 0.2
  View v = TestView();
  SetPhi(&v, 2, 0.97);
  v.rho_tilde[2] = 0.7;
  EXPECT_EQ(LevelRules(v, 2, false, MaskAt(v, 2), cfg).action, Action::kDefer);
  v.rho_tilde[2] = 0.9;
  EXPECT_EQ(LevelRules(v, 2, false, MaskAt(v, 2), cfg).action, Action::kHold);
  v.rho_tilde[2] = kNaN;  // not measured
  EXPECT_EQ(LevelRules(v, 2, false, MaskAt(v, 2), cfg).action, Action::kHold);
}

TEST(Rules, NeighbourReleaseCompactsIntoAnEmptyLevel) {
  const Config cfg = TestConfig(Mode::kRules, {"neighbour_release"});  // 0.3
  View v = TestView();
  SetPhi(&v, 2, 0.8);
  SetPhi(&v, 3, 0.2);
  EXPECT_EQ(LevelRules(v, 2, false, MaskAt(v, 2), cfg).action,
            Action::kCompact);
  SetPhi(&v, 3, 0.4);
  EXPECT_EQ(LevelRules(v, 2, false, MaskAt(v, 2), cfg).action, Action::kHold);
  // Below phi_min compacting is masked.
  SetPhi(&v, 2, 0.5);
  SetPhi(&v, 3, 0.1);
  EXPECT_EQ(LevelRules(v, 2, false, MaskAt(v, 2), cfg).action, Action::kHold);
}

TEST(Rules, OrderAndTheLastLevel) {
  const Config cfg =
      TestConfig(Mode::kRules, {"neighbour_release", "yield_slot"});
  View v = TestView();
  SetPhi(&v, 2, 0.97);  // both compact and defer allowed
  SetPhi(&v, 3, 0.1);
  v.k0 = 3;
  const Choice choice = DecideLevel(cfg, v, 2, false, MaskAt(v, 2));
  EXPECT_EQ(choice.action, Action::kDefer);  // yield_slot comes first
  EXPECT_EQ(choice.reason, "rule:yield_slot");
  EXPECT_EQ(LevelRules(v, 5, true, MaskAt(v, 5), cfg).action, Action::kHold);
  EXPECT_EQ(DecideLevel(cfg, v, 4, false, MaskAt(v, 4)).reason, "rules:none");
}

TEST(Policy, HoldOnlyAlwaysHolds) {
  const Config cfg = TestConfig(Mode::kHoldOnly, {"yield_slot"});
  View v = TestView();
  SetPhi(&v, 2, 0.97);
  v.k0 = 3;
  const Choice choice = DecideLevel(cfg, v, 2, false, MaskAt(v, 2));
  EXPECT_EQ(choice.action, Action::kHold);
  EXPECT_EQ(choice.reason, "hold-only");
  const L0Control c{4, 0};
  EXPECT_EQ(DecideL0(cfg, v, c, L0Mask(c, 2, 4, TestBounds()), {}).action,
            Action::kHold);
}

}  // namespace
}  // namespace rlc
