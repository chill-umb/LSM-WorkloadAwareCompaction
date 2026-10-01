// Pathway A §2's actions, relaxation and anchor decay (ACT-5), and the L0
// analogues (plan §6.2 actions_test).
#include "actions.h"

#include <cmath>
#include <random>

#include "gtest/gtest.h"
#include "test_util.h"

namespace rlc {
namespace {

using test::TestBounds;

TEST(Actions, CompactAndDeferSetTheScore) {
  const Bounds b = TestBounds();
  for (double anchor : {1.0, 1.25, 2.0}) {
    for (double phi : {0.6, 0.9, 1.3, 1.9}) {
      const LevelControl c{anchor, 1.0};
      const LevelControl compact = Act(c, Action::kCompact, phi, b);
      const LevelControl defer = Act(c, Action::kDefer, phi, b);
      EXPECT_NEAR(phi / compact.m(), 1 + b.epsilon, 1e-12);
      EXPECT_NEAR(phi / defer.m(), 1 / (1 + b.epsilon), 1e-12);
      // Timing actions leave the capacity alone.
      EXPECT_EQ(compact.anchor, anchor);
      EXPECT_EQ(defer.anchor, anchor);
    }
  }
}

TEST(Actions, HoldChangesNothing) {
  const LevelControl c{1.5, 0.8};
  const LevelControl r = Act(c, Action::kHold, 1.1, TestBounds());
  EXPECT_EQ(r.anchor, c.anchor);
  EXPECT_EQ(r.timing, c.timing);
}

TEST(Actions, ExpandKeepsADeferredLevelDeferredAndCapsAtMMax) {
  const Bounds b = TestBounds();
  const double phi = 1.1;
  LevelControl c = Act({1.0, 1.0}, Action::kDefer, phi, b);
  for (int i = 0; i < 10; ++i) {
    c = Act(c, Action::kExpand, phi, b);
    EXPECT_LE(phi / c.m(), 1 / (1 + b.epsilon) + 1e-12) << "expand " << i;
    EXPECT_LE(c.anchor, b.m_max);
    EXPECT_GE(c.timing, 1.0);
  }
  EXPECT_EQ(c.anchor, b.m_max);
  // A level well below its anchor's reach gets d = 1 and the raised anchor.
  const LevelControl low = Act({1.0, 1.0}, Action::kExpand, 0.6, b);
  EXPECT_EQ(low.timing, 1.0);
  EXPECT_NEAR(low.anchor, b.alpha, 1e-12);
}

TEST(Actions, TimingRelaxesOverKappaD) {
  const Bounds b = TestBounds();
  const LevelControl c{1.0, 1.5};
  EXPECT_NEAR(Relax(c, b.kappa_d, b).timing - 1, 0.5 * std::exp(-1.0), 1e-12);
  EXPECT_EQ(Relax(c, b.kappa_d, b).anchor, 1.0);
  // Steps compose.
  EXPECT_NEAR(Relax(Relax(c, 0.3, b), 0.7, b).timing, Relax(c, 1.0, b).timing,
              1e-12);
  // No time, no change; long enough and it snaps to exactly 1, so the level
  // stops causing SetOptions calls.
  EXPECT_EQ(Relax(c, 0, b).timing, 1.5);
  EXPECT_EQ(Relax(c, 20 * b.kappa_d, b).timing, 1.0);
  // A compacted level relaxes back up the same way.
  EXPECT_NEAR(1 - Relax({1.0, 0.6}, 2 * b.kappa_d, b).timing,
              0.4 * std::exp(-2.0), 1e-12);
}

TEST(Actions, AnchorsReturnWithinEpsilonAfterFiveKappaA) {  // ACT-5
  const Bounds b = TestBounds();
  std::mt19937 rng(5);
  std::uniform_real_distribution<double> unit(0, 1);
  for (int trial = 0; trial < 1000; ++trial) {
    LevelControl c{1 + unit(rng) * (b.m_max - 1), 0.5 + unit(rng)};
    double left = 5 * b.kappa_a;
    while (left > 0) {
      const double step = std::min(left, unit(rng) * b.kappa_a);
      c = Relax(c, step, b);
      left -= step;
    }
    EXPECT_LE(std::fabs(c.anchor - 1), b.epsilon) << "trial " << trial;
  }
}

TEST(Actions, L0ActionsMoveTheTriggerWithinItsRange) {
  const Bounds b = TestBounds();  // [2, 8]
  const L0Control c{4, 0};
  EXPECT_EQ(c.K0(b), 4);
  EXPECT_EQ(ActL0(c, Action::kCompact, 3, b).K0(b), 3);  // due now at 3 files
  EXPECT_EQ(ActL0(c, Action::kCompact, 1, b).K0(b), 2);  // never below k0_min
  EXPECT_EQ(ActL0(c, Action::kDefer, 4, b).K0(b), 5);
  EXPECT_EQ(ActL0(c, Action::kDefer, 8, b).K0(b), 8);  // capped
  const L0Control expand = ActL0(c, Action::kExpand, 1, b);
  EXPECT_EQ(expand.anchor, 5);
  EXPECT_EQ(expand.K0(b), 5);
  // Expand keeps a deferred L0 not due: at 6 files the trigger stays >= 7.
  EXPECT_GE(ActL0(c, Action::kExpand, 6, b).K0(b), 7);
  EXPECT_EQ(ActL0({8, 0}, Action::kExpand, 1, b).anchor, 8);

  std::mt19937 rng(7);
  L0Control r = c;
  for (int i = 0; i < 10000; ++i) {
    const auto action = static_cast<Action>(rng() % kNumActions);
    r = ActL0(r, action, static_cast<int>(rng() % 12), b);
    r = RelaxL0(r, (rng() % 100) / 50.0, 4, b);
    ASSERT_GE(r.K0(b), b.k0_min);
    ASSERT_LE(r.K0(b), b.k0_cap);
    ASSERT_LE(r.anchor, b.k0_cap);
  }
}

TEST(Actions, L0RelaxesBackToTheConfiguredTrigger) {
  const Bounds b = TestBounds();
  const L0Control r = RelaxL0({8, 2}, 100, 4, b);
  EXPECT_EQ(r.anchor, 4);
  EXPECT_EQ(r.offset, 0);
  EXPECT_EQ(r.K0(b), 4);
  // One kappa_a turnover takes the anchor e^-1 of the way.
  EXPECT_NEAR(RelaxL0({8, 0}, b.kappa_a, 4, b).anchor, 4 + 4 * std::exp(-1.0),
              1e-12);
}

}  // namespace
}  // namespace rlc
