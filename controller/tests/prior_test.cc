// The analytic prior b (H §7) on hand-worked cases: compaction at the
// current overlap plus the slot-blocking charge, deferral at garbage plus
// burst, expansion at the space bound, early L0 compaction, and clipping
// (plan §6.2 prior_test). The worked numbers use test_util.h's TestView and
// TestConfig.
#include "prior.h"

#include <cmath>

#include "gtest/gtest.h"
#include "test_util.h"

namespace rlc {
namespace {

using test::kMiB;
using test::SetPhi;
using test::TestConfig;
using test::TestView;

constexpr int kHold = static_cast<int>(Action::kHold);
constexpr int kCompact = static_cast<int>(Action::kCompact);
constexpr int kDefer = static_cast<int>(Action::kDefer);
constexpr int kExpand = static_cast<int>(Action::kExpand);

TEST(Prior, CompactIsPricedAtTheCurrentOverlap) {
  View v = TestView();
  SetPhi(&v, 2, 0.8);
  // Released early: 0.8 - 0.8 / 1.05 of C_2. Overlap constant 1.5 / f_2 =
  // 0.75, f_2 = T = 2.
  SetPhi(&v, 3, 0.2);  // now: T * 0.2 / 0.8 = 0.5 per source byte
  // Only the merged share 1 - xi = 0.7 writes (Lemma D.7, G.2).
  const double released = 0.8 * 0.05 / 1.05;
  EXPECT_NEAR(LevelPrior(v, 2, {}, TestConfig())[kCompact],
              0.7 * released * 0.75 * (0.5 - 2), 1e-12);
  SetPhi(&v, 3, 1.0);  // now: 2.5 per source byte
  EXPECT_NEAR(LevelPrior(v, 2, {}, TestConfig())[kCompact],
              0.7 * released * 0.75 * (2.5 - 2), 1e-12);
  EXPECT_EQ(LevelPrior(v, 2, {}, TestConfig())[kHold], 0);
  // A level that only moves files trivially writes nothing early.
  v.xi[2] = 1;
  EXPECT_EQ(LevelPrior(v, 2, {}, TestConfig())[kCompact], 0);
}

TEST(Prior, CompactPaysTheSlotBlockingChargeWhenL0FallsDue) {
  View v = TestView();
  SetPhi(&v, 2, 0.8);
  SetPhi(&v, 3, 0.8);  // o_now = T * 0.8 / 0.8 = f_2: no write term
  const double c16 = 16 * kMiB;
  // L0's priced reads per operation: (30000 * 100 + 300 * 1000 + 10000 *
  // 2000) / 100000 = 233.
  // L0 already due, 5 files over a trigger of 4, and no flush during the
  // job: share 1/5 throughout.
  v.k0_all = 5;
  EXPECT_NEAR(LevelPrior(v, 2, {}, TestConfig())[kCompact],
              0.2 * 233 * 500 / c16, 1e-12);
  // L0 at 3 files; a 5000-operation job sees two flushes (one every 2500):
  // share 0 at the start, 1/5 at the end, 1/10 on average.
  v.k0_all = 3;
  v.job_ops[2] = 5000;
  EXPECT_NEAR(LevelPrior(v, 2, {}, TestConfig())[kCompact],
              0.1 * 233 * 5000 / c16, 1e-12);
  // L0 far from due: no charge.
  v.k0_all = 1;
  EXPECT_NEAR(LevelPrior(v, 2, {}, TestConfig())[kCompact], 0, 1e-12);
}

TEST(Prior, DeferIsPricedByTheGarbageItKeepsAndTheBurstItBuilds) {
  View v = TestView();
  SetPhi(&v, 2, 0.97);
  // Held: 0.97 * 1.05 - 1; sigma_2 = 0.001 * 40000 / 1000; garbage 1 - 0.93.
  const double held = 0.97 * 1.05 - 1;
  const double garbage = 0.04 * 0.07 * held;
  EXPECT_NEAR(LevelPrior(v, 2, {}, TestConfig())[kDefer], garbage, 1e-12);
  // The level below is full: the burst overflows it and is rewritten there
  // at rho_3 + o_3 = 2.4.
  SetPhi(&v, 3, 1.0);
  EXPECT_NEAR(LevelPrior(v, 2, {}, TestConfig())[kDefer],
              garbage + 0.93 * held * 2.4, 1e-12);
}

TEST(Prior, ExpandIsPricedAtTheSpaceBound) {
  const View v = TestView();
  // m 1 -> alpha = 1.25, every added byte held: sigma_2 * 0.25.
  EXPECT_NEAR(LevelPrior(v, 2, {}, TestConfig())[kExpand], 0.04 * 0.25, 1e-12);
  // Deeper levels hold longer per turnover: T times the price.
  EXPECT_NEAR(LevelPrior(v, 3, {}, TestConfig())[kExpand], 0.08 * 0.25, 1e-12);
  // At m_max there is nothing to add.
  EXPECT_EQ(LevelPrior(v, 2, {2.0, 1.0}, TestConfig())[kExpand], 0);
}

TEST(Prior, ClippedToBMax) {
  View v = TestView();
  SetPhi(&v, 2, 0.8);
  SetPhi(&v, 3, 0.2);
  Config cfg = TestConfig();
  cfg.b_max = 0.005;
  const Values b = LevelPrior(v, 2, {}, cfg);
  EXPECT_EQ(b[kCompact], -0.005);
  EXPECT_EQ(b[kExpand], 0.005);
}

TEST(Prior, UnmeasuredInputsContributeNothing) {
  View v = TestView();
  for (auto* field : {&v.overlap, &v.rho_tilde, &v.N, &v.job_ops}) {
    field->assign(v.num_levels, kNaN);
  }
  SetPhi(&v, 2, 0.8);
  for (double b : LevelPrior(v, 2, {}, TestConfig())) EXPECT_EQ(b, 0);
}

// D.11 at TestView's rates: u = 409.6 bytes per operation, F = 1 MiB, m_1
// C_1 = 8 MiB, 0.5 Gets and 0.1 scans per operation, false-positive rate
// 300 / 25000, so g(K) = 3276.8 / K + 128 K, over N_0 = 10000 operations
// divided by c_w C_0 = 4 MiB.
TEST(Prior, L0EarlyCompactionTradesOverlapForReads) {
  View v = TestView();
  const Config cfg = TestConfig();
  const double norm = 10000 / (4 * kMiB);
  const auto g = [](double K) { return 3276.8 / K + 128 * K; };
  const L0Control c{4, 0};
  Values b = L0Prior(v, c, cfg);
  EXPECT_EQ(b[kHold], 0);
  // Compact at k0 = 2 with the slot idle: overlap written, reads saved.
  EXPECT_NEAR(b[kCompact], norm * (g(2) - g(4)), 1e-9);
  EXPECT_NEAR(b[kDefer], norm * (g(3) - g(4)), 1e-9);
  EXPECT_NEAR(b[kExpand], norm * (g(5) - g(4)), 1e-9);
  EXPECT_LT(b[kExpand], 0);  // K* = 5.06 at these prices
  // F is the fixed flush size (H §3), not the running mean: a run of larger
  // later flushes changes neither the unit nor the overlap term.
  View later = v;
  later.flush_bytes = 80 * kMiB;  // mean 2 MiB over 40 flushes
  later.N[0] = later.k0_cfg * later.F * later.ops / later.flush_bytes;
  const double later_norm = later.N[0] / (4 * kMiB);
  EXPECT_NEAR(L0Prior(later, c, cfg)[kDefer], later_norm * (g(3) - g(4)), 1e-9);
  // While the slot is busy, compacting early saves no reads yet.
  v.running = 2;
  EXPECT_NEAR(L0Prior(v, c, cfg)[kCompact], norm * (3276.8 / 2 - 3276.8 / 4),
              1e-9);
  // Scan-heavy (one scan per operation): early compaction pays.
  v.running = -1;
  v.scans = 100000;
  EXPECT_LT(L0Prior(v, c, cfg)[kCompact], 0);
}

}  // namespace
}  // namespace rlc
