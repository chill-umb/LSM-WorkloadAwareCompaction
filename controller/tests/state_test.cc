// The normalised state (G §3, H §2): level-free units, queue position,
// backlog over H, the absent flag, and the statistics behind the level
// terms (plan §6.2 state_test).
#include "state.h"

#include <cmath>

#include "gtest/gtest.h"
#include "test_util.h"

namespace rlc {
namespace {

using test::FakeHost;
using test::kMiB;
using test::SetPhi;
using test::TestConfig;
using test::TestView;

double Feature(Agent agent, const std::vector<double>& values,
               const std::string& name) {
  const auto& names = FeatureNames(agent);
  for (size_t i = 0; i < names.size(); ++i) {
    if (names[i] == name) return values[i];
  }
  ADD_FAILURE() << "no feature " << name;
  return kNaN;
}

TEST(State, FeatureNamesMatchTheValues) {
  const View v = TestView();
  const Config cfg = TestConfig();
  EXPECT_EQ(LevelFeatures(v, 2, false, {}, {}, kNaN, cfg).size(),
            FeatureNames(Agent::kInterior).size());
  EXPECT_EQ(LevelFeatures(v, 5, true, {}, {}, kNaN, cfg).size(),
            FeatureNames(Agent::kLast).size());
  EXPECT_EQ(L0Features(v, {4, 0}, {}, cfg).size(),
            FeatureNames(Agent::kL0).size());
}

// Two levels whose raw ratios are the same, and whose counts differ by the
// level clock's factor, have the same features, except the level terms that
// legitimately depend on depth: the survival product pi and the holding
// price sigma.
TEST(State, SameRatiosAtTwoLevelsGiveTheSameFeatures) {
  const double T = 2;
  View v = TestView(8, T);
  for (int i = 1; i < 8; ++i) SetPhi(&v, i, 0.8);
  for (int i = 1; i < 8; ++i) v.since_release[i] = 1000 * std::pow(T, i - 1);
  LevelParts at2;
  at2.ops = 5000;
  at2.busy_ops = 2000;
  at2.inflow_bytes = v.inflow[2] / v.ops * 5000 * 1.3;
  at2.wait_ops = 300;
  at2.waits = 2;
  LevelParts at3 = at2;
  at3.ops *= T;
  at3.busy_ops *= T;
  at3.inflow_bytes = v.inflow[3] / v.ops * at3.ops * 1.3;
  at3.wait_ops *= T;
  const Config cfg = TestConfig();
  const LevelControl c{1.25, 0.9};
  const auto f2 = LevelFeatures(v, 2, false, c, at2, kNaN, cfg);
  const auto f3 = LevelFeatures(v, 3, false, c, at3, kNaN, cfg);
  const auto& names = FeatureNames(Agent::kInterior);
  for (size_t i = 0; i < names.size(); ++i) {
    if (names[i] == "pi" || names[i] == "sigma") continue;
    // Not measured at either level (cost model 2's prices under cost model
    // 1): NaN at both is the same input.
    if (std::isnan(f2[i]) && std::isnan(f3[i])) continue;
    EXPECT_NEAR(f2[i], f3[i], 1e-12) << names[i];
  }
  EXPECT_NEAR(Feature(Agent::kInterior, f3, "sigma"),
              T * Feature(Agent::kInterior, f2, "sigma"), 1e-12);
  EXPECT_NEAR(Feature(Agent::kInterior, f2, "inflow_ratio"), 1.3, 1e-12);
  EXPECT_NEAR(Feature(Agent::kInterior, f2, "busy_share"), 0.4, 1e-12);
  // 150 operations per release over an interval of N_2 / k = 40000 / 4.
  EXPECT_NEAR(Feature(Agent::kInterior, f2, "wait"), 150.0 / 10000, 1e-12);
}

// D-21: R^o = c_open / (c_w lambda_1) per operation, and e^o_j the level's
// reopens per operation, so that R^o e^o_j is the level's reopen cost in
// the units of R^f e^f_j.
TEST(State, ReopensEnterAsARateAndAPriceRatio) {
  View v = TestView();
  v.get_reopens[2] = 2000;
  v.iter_reopens[2] = 1000;
  v.get_reopens[0] = 400;
  v.iter_reopens[0] = 100;
  const Config cfg = TestConfig();
  double r_f = 0, r_b = 0, r_sk = 0, r_o = 0;
  PriceRatios(v, cfg, &r_f, &r_b, &r_sk, &r_o);
  const double lambda1 = v.inflow[1] / v.ops;
  EXPECT_NEAR(r_o, cfg.c_open / (cfg.c_w * lambda1), 1e-9);
  EXPECT_NEAR(r_f, 0.5 * cfg.c_f / (cfg.c_w * lambda1), 1e-9);
  const auto f = LevelFeatures(v, 2, false, {}, {}, kNaN, cfg);
  EXPECT_NEAR(Feature(Agent::kInterior, f, "e_o"), 0.03, 1e-12);
  EXPECT_NEAR(Feature(Agent::kInterior, f, "R_o"), r_o, 1e-12);
  EXPECT_EQ(Feature(Agent::kInterior,
                    LevelFeatures(v, 3, false, {}, {}, kNaN, cfg), "e_o"),
            0);
  const auto l0 = L0Features(v, {4, 0}, {}, cfg);
  EXPECT_NEAR(Feature(Agent::kL0, l0, "e_o"), 0.005, 1e-12);
  EXPECT_NEAR(Feature(Agent::kL0, l0, "R_o"), r_o, 1e-12);
}

TEST(State, QueuePositionCountsLevelsRocksDBWouldTryFirst) {
  View v = TestView();
  v.score = {1.5, 1.2, 0.5, 1.1, 0.2, 0.9};
  const double eps = 0.05;
  EXPECT_EQ(QueuePosition(v, 2, 0.5, eps), 3);  // L0, L1, L3
  EXPECT_EQ(QueuePosition(v, 1, 1.2, eps), 1);  // L0
  EXPECT_EQ(QueuePosition(v, 0, 1.5, eps), 0);
  // After "compact" level 2 scores 1 + eps: still behind the same three.
  EXPECT_EQ(QueuePosition(v, 2, 1 + eps, eps), 3);
  // A level at 1.04 is due but not ahead of anything at 1 + eps.
  v.score = {1.04, 1.04, 0.5, 0.5, 0.5, 0.5};
  EXPECT_EQ(QueuePosition(v, 2, 0.5, eps), 0);
}

TEST(State, SlotRelationAndBacklogAndTwoDown) {
  const Config cfg = TestConfig();
  View v = TestView(6);
  v.last = 4;
  v.running = 1;
  auto f = LevelFeatures(v, 2, false, {}, {}, kNaN, cfg);
  EXPECT_EQ(Feature(Agent::kInterior, f, "slot_above"), 1);
  EXPECT_EQ(Feature(Agent::kInterior, f, "slot_none"), 0);
  EXPECT_EQ(Feature(Agent::kInterior, f, "backlog"), 0.1);
  // j + 2 = L: the last level's fill; j + 2 > L: 0 and the absent flag.
  SetPhi(&v, 4, 0.3);
  EXPECT_NEAR(Feature(Agent::kInterior,
                      f = LevelFeatures(v, 2, false, {}, {}, kNaN, cfg),
                      "fill_two_down"),
              0.3, 1e-12);
  EXPECT_EQ(Feature(Agent::kInterior, f, "two_down_absent"), 0);
  f = LevelFeatures(v, 3, false, {}, {}, kNaN, cfg);
  EXPECT_EQ(Feature(Agent::kInterior, f, "fill_two_down"), 0);
  EXPECT_EQ(Feature(Agent::kInterior, f, "two_down_absent"), 1);
  EXPECT_NEAR(Feature(Agent::kInterior, f, "last_room"), 0.7, 1e-12);
  // The last-level agent sees its fill and headroom.
  f = LevelFeatures(v, 4, true, {1.25, 1.0}, {}, kNaN, cfg);
  EXPECT_NEAR(Feature(Agent::kLast, f, "fill"), 0.3 / 1.25, 1e-12);
  EXPECT_NEAR(Feature(Agent::kLast, f, "headroom"), 1.25 - 0.3, 1e-12);
}

TEST(State, MakeViewReadsTheSnapshotAndTheTotals) {
  FakeHost host;
  host.SetLevel(1, 0.8);
  host.SetLevel(3, 0.4);
  host.snapshot->levels[1].bytes_compacting =
      static_cast<uint64_t>(0.2 * 8 * kMiB);
  host.snapshot->levels[0].num_files = 5;
  host.snapshot->levels[0].num_files_compacting = 2;
  host.snapshot->live_sst_bytes = 3000;
  host.snapshot->pending_compaction_bytes = 300;
  host.snapshot->active_memtable_bytes = 256 * 1024;
  Stats stats;
  stats.Reset(5, host.ops, host.reads);
  host.AddOps(600, 400);
  host.reads[2].probes = 900;
  host.reads[2].filter_passes = 30;
  host.reads[2].filter_hits = 20;
  host.reads[2].get_reopens = 45;
  host.reads[2].iter_reopens = 7;
  host.reads[2].reopen_nanos = 520000;
  const View v = MakeView(host.options, *host.snapshot, stats, host.ops,
                          host.reads, {1, 2, 1, 1, 1}, 4);
  // Bytes being compacted are excluded (to within the whole-byte rounding).
  EXPECT_NEAR(v.phi[1], 0.6, 1e-6);
  EXPECT_NEAR(v.score[1], 0.3, 1e-6);
  EXPECT_EQ(v.last, 3);
  EXPECT_EQ(v.k0, 3);
  EXPECT_EQ(v.k0_all, 5);
  EXPECT_NEAR(v.backlog, 0.1, 1e-12);
  EXPECT_NEAR(v.mem_fill, 0.25, 1e-12);
  EXPECT_NEAR(v.l0_slowdown, 15.0 / 20, 1e-12);
  EXPECT_EQ(v.ops, 1000);
  EXPECT_EQ(v.gets, 600);
  EXPECT_EQ(v.fp_reads[2], 10);
  EXPECT_EQ(v.get_reopens[2], 45);  // D-21
  EXPECT_EQ(v.iter_reopens[2], 7);
  EXPECT_EQ(v.get_reopens[1], 0);
  EXPECT_NEAR(v.C[3], 32 * kMiB, 1e-6);
  // Unmeasured: nothing released, nothing flushed.
  EXPECT_TRUE(std::isnan(v.N[0]));
  EXPECT_TRUE(std::isnan(v.rho[1]));
  host.snapshot->live_sst_bytes = 0;
  EXPECT_TRUE(std::isnan(MakeView(host.options, *host.snapshot, stats, host.ops,
                                  host.reads, {1, 1, 1, 1, 1}, 4)
                             .backlog));
}

TEST(State, StatsFromJobRecords) {
  FakeHost host;
  Stats stats;
  stats.Reset(5, host.ops, host.reads);
  OpClock clock;
  clock.Add(1000, 0);
  clock.Add(2000, 100);
  std::vector<LevelParts> parts(5);
  // Before any compaction record, a job the snapshot shows is believed.
  EXPECT_EQ(stats.RunningLevel(3), 3);
  stats.OnJob(FakeHost::Flush(0), clock, &parts);
  EXPECT_EQ(stats.RunningLevel(3), 3);

  // A merge L1 -> L2 picked 50 operations after L1 became due.
  auto begin = FakeHost::Compaction(false, 7, 1, 100, 200, 0);
  begin.op = 150;
  begin.due_since_micros = 2000;
  stats.OnJob(begin, clock, &parts);
  EXPECT_EQ(stats.RunningLevel(3), 1);
  // G §3: the wait belongs to the interval of the release, so it is
  // attributed when the job ends, not when it starts.
  EXPECT_EQ(parts[1].waits, 0);
  auto end = FakeHost::Compaction(true, 7, 1, 100, 200, 270);
  end.op = 250;
  stats.OnJob(end, clock, &parts);
  EXPECT_EQ(stats.RunningLevel(3), -1);  // records now decide
  EXPECT_EQ(parts[1].waits, 1);
  EXPECT_EQ(parts[1].wait_ops, 50);
  // A trivial move L2 -> L3 and a flush.
  auto trivial = FakeHost::Compaction(true, 8, 2, 50, 0, 0);
  trivial.trivial = true;
  trivial.op = 260;
  stats.OnJob(trivial, clock, &parts);
  stats.OnJob(FakeHost::Flush(64), clock, &parts);
  stats.OnJob(FakeHost::Flush(256), clock, &parts);

  host.AddOps(300, 0);
  const View v = MakeView(host.options, *host.snapshot, stats, host.ops,
                          host.reads, {1, 1, 1, 1, 1}, 4);
  EXPECT_NEAR(v.rho[1], 0.7, 1e-12);      // (270 - 200) / 100
  EXPECT_NEAR(v.overlap[1], 2.0, 1e-12);  // 200 / 100
  EXPECT_EQ(v.xi[1], 0);
  EXPECT_NEAR(v.rho_tilde[1], 0.7, 1e-12);
  EXPECT_EQ(v.job_ops[1], 100);
  EXPECT_EQ(v.xi[2], 1);  // trivial moves only: excluded from rho
  EXPECT_TRUE(std::isnan(v.rho[2]));
  EXPECT_EQ(v.rho_tilde[2], 1);
  EXPECT_EQ(v.inflow[2], 70);
  EXPECT_EQ(v.inflow[3], 50);
  EXPECT_EQ(v.inflow[0], 320);
  EXPECT_NEAR(v.N[2], v.C[2] * 300 / 70, 1e-6);
  // H §3: F is the first non-empty flush after the start, then held fixed,
  // however later flushes differ (the running mean here is 320 / 3);
  // N_0 = K0_cfg F ops / flushed bytes.
  EXPECT_EQ(v.F, 64);
  EXPECT_NEAR(v.N[0], 4 * 64 * 300.0 / 320, 1e-9);
  EXPECT_EQ(v.since_release[1], 50);  // released at operation 250
  // Writes go to the source level, trivial moves write nothing.
  EXPECT_EQ(parts[1].write_bytes, 270);
  EXPECT_EQ(parts[2].write_bytes, 0);
  EXPECT_EQ(parts[0].write_bytes, 320);
}

TEST(State, TheBurstIsFlaggedAbsentAtL1) {
  const Config cfg = TestConfig();
  const View v = TestView();
  const auto l1 = LevelFeatures(v, 1, false, {}, {}, kNaN, cfg);
  const auto l2 = LevelFeatures(v, 2, false, {}, {}, kNaN, cfg);
  EXPECT_EQ(Feature(Agent::kInterior, l1, "burst"), 0);
  EXPECT_EQ(Feature(Agent::kInterior, l1, "burst_absent"), 1);
  EXPECT_EQ(Feature(Agent::kInterior, l2, "burst_absent"), 0);
}

TEST(State, AWaitIsCarriedOnlyFromAnIntervalWithARelease) {
  FakeHost host;
  Stats stats;
  stats.Reset(5, host.ops, host.reads);
  OpClock clock;
  clock.Add(1000, 0);
  clock.Add(2000, 100);
  std::vector<LevelParts> parts(5);
  auto begin = FakeHost::Compaction(false, 7, 2, 100, 200, 0);
  begin.op = 160;
  begin.due_since_micros = 1500;  // operation 50
  stats.OnJob(begin, clock, &parts);
  // The job has started but not released: the interval has no wait.
  EXPECT_EQ(parts[2].waits, 0);
  std::vector<LevelParts> next(5);
  auto end = FakeHost::Compaction(true, 7, 2, 100, 200, 250);
  end.op = 400;
  stats.OnJob(end, clock, &next);
  EXPECT_EQ(next[2].waits, 1);
  EXPECT_EQ(next[2].wait_ops, 110);
}

TEST(State, OpClockInterpolatesAndClamps) {
  OpClock clock;
  EXPECT_EQ(clock.OpAt(5), 0);
  clock.Add(100, 10);
  clock.Add(200, 30);
  clock.Add(400, 30);
  EXPECT_EQ(clock.OpAt(50), 10);
  EXPECT_EQ(clock.OpAt(150), 20);
  EXPECT_EQ(clock.OpAt(300), 30);
  EXPECT_EQ(clock.OpAt(500), 30);
}

// A slot wait can start at a due-since older than a minute (L3 at T = 10
// turns over in about 230 s): samples are kept back to it.
TEST(State, OpClockKeepsSamplesBackToTheOldestDueSince) {
  constexpr uint64_t kSecond = 1000 * 1000;
  OpClock bounded;
  OpClock kept;
  for (uint64_t t = 0; t <= 300; ++t) {
    bounded.Add(t * kSecond, t * 10);
    bounded.Trim(t * kSecond, 0);
    kept.Add(t * kSecond, t * 10);
    kept.Trim(t * kSecond, 100 * kSecond);  // a level due since 100 s
  }
  EXPECT_EQ(bounded.OpAt(100 * kSecond), 2400);  // clamped to 240 s
  EXPECT_EQ(kept.OpAt(100 * kSecond), 1000);
  EXPECT_EQ(kept.OpAt(150 * kSecond), 1500);
  EXPECT_LT(kept.size(), 205u);  // nothing older than needed
}

}  // namespace
}  // namespace rlc
