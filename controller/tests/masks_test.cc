// Pathway A §2's "allowed when" rules, the bounds and the ordering
// (A-Impl-6, A-Impl-7), and 10^5 random states in which no masked action is
// ever picked (plan §6.2 masks_test).
#include "masks.h"

#include <random>

#include "gtest/gtest.h"
#include "policy.h"
#include "test_util.h"

namespace rlc {
namespace {

using test::SetPhi;
using test::TestBounds;
using test::TestConfig;
using test::TestView;

constexpr int kHold = static_cast<int>(Action::kHold);
constexpr int kCompact = static_cast<int>(Action::kCompact);
constexpr int kDefer = static_cast<int>(Action::kDefer);
constexpr int kExpand = static_cast<int>(Action::kExpand);

Mask At(double phi, const LevelControl& c = {},
        std::vector<double> m = {1, 1, 1, 1, 1}, bool last = false) {
  m[2] = c.m();
  return LevelMask(m, 2, last, c, phi, 2, TestBounds());
}

TEST(Masks, HoldIsAlwaysAllowed) {
  for (double phi : {0.0, 0.5, 1.0, 3.0}) {
    EXPECT_TRUE(At(phi)[kHold]);
    EXPECT_TRUE(At(phi, {}, {1, 1, 1, 1, 1}, true)[kHold]);
  }
}

TEST(Masks, CompactNeedsScoreBelowOneAndFillAtLeastPhiMin) {
  EXPECT_TRUE(At(0.7)[kCompact]);
  EXPECT_TRUE(At(0.6)[kCompact]);    // phi = phi_min
  EXPECT_FALSE(At(0.59)[kCompact]);  // below phi_min
  EXPECT_FALSE(At(1.0)[kCompact]);   // s = 1: RocksDB compacts it anyway
}

TEST(Masks, DeferNeedsScoreNearOneAndRoomBelowMMax) {
  EXPECT_TRUE(At(0.96)[kDefer]);  // s >= 1 - eps
  EXPECT_FALSE(At(0.94)[kDefer]);
  EXPECT_TRUE(At(1.9)[kDefer]);    // 1.9 * 1.05 = 1.995 <= m_max
  EXPECT_FALSE(At(1.95)[kDefer]);  // 2.0475 > m_max
}

TEST(Masks, ExpandNeedsAnchorBelowMMax) {
  EXPECT_TRUE(At(0.5)[kExpand]);
  EXPECT_FALSE(At(0.5, {2.0, 0.5})[kExpand]);
  // Expanding a level fuller than m_max / (1 + eps) would leave the bounds.
  EXPECT_FALSE(At(1.95)[kExpand]);
}

TEST(Masks, LastLevelOnlyHoldsOrExpands) {
  const Mask mask = At(0.97, {}, {1, 1, 1, 1, 1}, true);
  EXPECT_TRUE(mask[kHold]);
  EXPECT_FALSE(mask[kCompact]);
  EXPECT_FALSE(mask[kDefer]);
  EXPECT_TRUE(mask[kExpand]);
}

TEST(Masks, NoResultLeavesTheBounds) {
  // At m = m_min, deferring at s = 0.952 would set m = 0.4998 < m_min.
  EXPECT_FALSE(At(0.476, {1.0, 0.5})[kDefer]);
  EXPECT_TRUE(At(0.49, {1.0, 0.5})[kDefer]);  // 0.5145
}

TEST(Masks, NoResultMakesTargetsShrinkGoingDown) {
  // T = 2 and level 1 at m = 2: level 2 may not go below 1.
  EXPECT_FALSE(At(0.7, {}, {1, 2.0, 1, 1, 1})[kCompact]);  // m = 0.667
  EXPECT_TRUE(At(0.7, {}, {1, 1.0, 1, 1, 1})[kCompact]);
  // Level 3 at 0.5 caps level 2 at 1: expanding level 2 to 1.25 is masked.
  EXPECT_FALSE(At(0.5, {}, {1, 1, 1, 0.5, 1})[kExpand]);
  EXPECT_TRUE(At(0.5, {}, {1, 1, 1, 1, 1})[kExpand]);
}

TEST(Masks, L0) {
  const Bounds b = TestBounds();  // [2, 8]
  const L0Control c{4, 0};
  // Compact: k0_min <= k0 < K0.
  EXPECT_TRUE(L0Mask(c, 2, 4, b)[kCompact]);
  EXPECT_FALSE(L0Mask(c, 1, 4, b)[kCompact]);
  EXPECT_FALSE(L0Mask(c, 4, 4, b)[kCompact]);  // already due
  // Defer: due, and room under the cap. One file short it would set the
  // trigger to k0 + 1 = K0, a no-op, so it is masked (D-18 item 8).
  EXPECT_FALSE(L0Mask(c, 3, 4, b)[kDefer]);
  EXPECT_TRUE(L0Mask(c, 4, 4, b)[kDefer]);
  EXPECT_TRUE(L0Mask(c, 5, 4, b)[kDefer]);
  EXPECT_FALSE(L0Mask(c, 2, 4, b)[kDefer]);  // would lower the trigger
  EXPECT_FALSE(L0Mask(c, 8, 8, b)[kDefer]);
  // Expand: anchor below the cap.
  EXPECT_TRUE(L0Mask(c, 2, 4, b)[kExpand]);
  EXPECT_FALSE(L0Mask({8, 0}, 2, 8, b)[kExpand]);
  EXPECT_TRUE(L0Mask(c, 0, 4, b)[kHold]);
}

TEST(Masks, AdmissibleAndRepair) {
  const Bounds b = TestBounds();
  std::vector<double> m = {1, 2.0, 0.9, 0.5, 1};
  EXPECT_FALSE(Admissible(m, 2, b));
  EXPECT_EQ(Repair(&m, 2, b), std::vector<int>({2}));
  EXPECT_EQ(m, std::vector<double>({1, 2.0, 1.0, 0.5, 1}));
  EXPECT_TRUE(Admissible(m, 2, b));

  // T = 3: 2/3 is not exact in binary; the repaired value passes the fork's
  // own comparison.
  std::vector<double> odd = {1, 2.0, 0.5, 0.5};
  Repair(&odd, 3, b);
  EXPECT_GE(odd[2] * 3, odd[1]);
  EXPECT_TRUE(Admissible(odd, 3, b));

  std::vector<double> wild = {0.7, 5, 0.1, 1};
  EXPECT_FALSE(Admissible(wild, 2, b));
  Repair(&wild, 2, b);
  EXPECT_EQ(wild[0], 1.0);
  EXPECT_TRUE(Admissible(wild, 2, b));
  EXPECT_FALSE(Admissible({1, std::nan("")}, 2, b));
}

TEST(Masks, PickAllowedIsUniformOverTheAllowedActions) {
  const Mask mask = {true, false, true, true};
  EXPECT_EQ(PickAllowed(mask, 0.0), Action::kHold);
  EXPECT_EQ(PickAllowed(mask, 0.34), Action::kDefer);
  EXPECT_EQ(PickAllowed(mask, 0.67), Action::kExpand);
  EXPECT_EQ(PickAllowed(mask, 0.99999), Action::kExpand);
  EXPECT_EQ(PickAllowed({true, false, false, false}, 0.5), Action::kHold);
}

// 10^5 random states: neither exploration's primitive nor the rules policy
// picks a masked action, and every allowed action keeps the vector
// admissible and the trigger in range.
TEST(Masks, RandomStatesNeverYieldAMaskedAction) {
  const Bounds b = TestBounds();
  const Config cfg =
      TestConfig(Mode::kRules, {"k0_tracking", "l0_early", "yield_slot",
                                "garbage_hold", "neighbour_release"});
  std::mt19937 rng(20261001);
  std::uniform_real_distribution<double> unit(0, 1);
  const double ratios[] = {2, 6, 10};
  for (int trial = 0; trial < 100000; ++trial) {
    const double T = ratios[trial % 3];
    View v = TestView(6, T);
    for (int i = 1; i < 6; ++i) {
      const double low = i == 1 ? b.m_min : std::max(b.m_min, v.m[i - 1] / T);
      v.m[i] = low + unit(rng) * (b.m_max - low);
    }
    Repair(&v.m, T, b);  // rounding at the lower edge
    for (int i = 1; i < 6; ++i) SetPhi(&v, i, unit(rng) * 2.2);
    ASSERT_TRUE(Admissible(v.m, T, b));
    v.k0 = static_cast<int>(rng() % 12);
    v.k0_all = v.k0;
    v.running = static_cast<int>(rng() % 7) - 1;
    for (double& r : v.rho_tilde) r = unit(rng);
    v.last = 1 + static_cast<int>(rng() % 5);

    const int level = 1 + static_cast<int>(rng() % v.last);
    const bool last = level == v.last;
    const double anchor = 1 + unit(rng) * (b.m_max - 1);
    const LevelControl c{anchor, v.m[level] / anchor};
    const Mask mask = LevelMask(v.m, level, last, c, v.phi[level], T, b);
    ASSERT_TRUE(mask[static_cast<int>(PickAllowed(mask, unit(rng)))]);
    ASSERT_TRUE(
        mask[static_cast<int>(DecideLevel(cfg, v, level, last, mask).action)]);
    for (int a = kCompact; a < kNumActions; ++a) {
      if (!mask[a]) continue;
      std::vector<double> m = v.m;
      m[level] = Act(c, static_cast<Action>(a), v.phi[level], b).m();
      ASSERT_TRUE(Admissible(m, T, b)) << "trial " << trial << " action " << a;
    }

    const L0Control l0{b.k0_min + unit(rng) * (b.k0_cap - b.k0_min),
                       unit(rng) * 4 - 2};
    v.K0 = l0.K0(b);
    const Mask l0_mask = L0Mask(l0, v.k0, v.K0, b);
    LevelParts interval;
    interval.ops = 1000;
    interval.gets = unit(rng) * 1000;
    interval.writes = unit(rng) * 1000;
    interval.user_bytes = interval.writes * 1024;
    ASSERT_TRUE(l0_mask[static_cast<int>(PickAllowed(l0_mask, unit(rng)))]);
    ASSERT_TRUE(l0_mask[static_cast<int>(
        DecideL0(cfg, v, l0, l0_mask, interval).action)]);
    for (int a = 0; a < kNumActions; ++a) {
      if (!l0_mask[a]) continue;
      const int K0 = ActL0(l0, static_cast<Action>(a), v.k0, b).K0(b);
      ASSERT_GE(K0, b.k0_min);
      ASSERT_LE(K0, b.k0_cap);
      // An allowed timing action changes the trigger.
      if (a == kCompact || a == kDefer) {
        ASSERT_NE(K0, v.K0) << "action " << a;
      }
    }
  }
}

}  // namespace
}  // namespace rlc
