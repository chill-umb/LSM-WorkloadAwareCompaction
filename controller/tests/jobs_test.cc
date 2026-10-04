// Cost model 2's per-job quantities (jobs.h): step counts by type, the
// window rule of D §1 and the prices, against hand-worked values that
// scripts/dbbench_pipeline/tests/test_cost_model_v2.py's formulas give.
#include "jobs.h"

#include "gtest/gtest.h"
#include "test_util.h"

namespace rlc {
namespace {

using test::RLStepCounts;

RLStepCounts Steps(uint64_t base) {
  RLStepCounts s;
  s.probes = 10 * base;
  s.block_probes = 2 * base;
  s.run_seeks = 3 * base;
  s.reopens = base / 10;
  s.nexts_found = 20 * base;
  s.iter_skips = 5 * base;
  s.gets = base / 2;
  s.scans = base / 4;
  s.puts = base / 4;
  return s;
}

Config V2Config() {
  Config c = test::TestConfig();
  c.cost_model = 2;
  c.v2.c_cr = 0.5;
  c.v2.job[kFlush] = 1000;
  c.v2.job[kL0Merge] = 2000;
  c.v2.job[kDeepMerge] = 3000;
  c.v2.job[kMove] = 400;
  c.v2.c_st = 10;
  c.v2.c_ib = 0;
  c.v2.c_mt = 20;
  c.v2.c_get0 = 30;
  c.v2.c_sc0 = 40;
  c.v2.c_put = 50;
  c.v2.p_dev = 1e9;  // money per core-second: prices are core-nanoseconds
  c.v2.lambda = 0.25;
  c.v2.n_win = 1000;
  for (int x = 0; x < kNumStepTypes; ++x) c.v2.kappa_b[x] = 1e-9;
  c.v2.kappa_j[kProbe][kDeepMerge] = 0.1;
  return c;
}

TEST(Jobs, TypeCountsFollowTheEvaluatorsTickers) {
  const StepArray c = TypeCounts(Steps(100));
  EXPECT_EQ(c[kProbe], 1000);
  EXPECT_EQ(c[kBlock], 200);
  EXPECT_EQ(c[kSeek], 300);
  EXPECT_EQ(c[kReopen], 10);
  EXPECT_EQ(c[kStep], 2500);  // returned + hidden
  EXPECT_EQ(c[kIBlock], 0);   // no counter on the interim binary
  EXPECT_EQ(c[kMemtable], 75);
  EXPECT_EQ(c[kGet0], 50);
  EXPECT_EQ(c[kScan0], 25);
  EXPECT_EQ(c[kPut], 25);
  EXPECT_EQ(StepOps(Steps(100)), 100u);
}

TEST(Jobs, ALongJobsWindowIsItsOwnOperations) {
  StepRing ring;
  ring.Reset({0, Steps(0)});
  const StepSample begin{1000, Steps(1000)};
  const StepSample end{2500, Steps(2500)};
  const Window w = JobWindow(true, begin, end, ring, 1000);
  EXPECT_TRUE(w.own);
  EXPECT_EQ(w.start_op, 1000u);
  EXPECT_EQ(w.ops, 1500);
  EXPECT_EQ(w.counts[kProbe], 15000);
}

TEST(Jobs, AShortJobsWindowStartsAtTheLastSampleBeforeNEndMinusNWin) {
  StepRing ring;
  ring.Reset({0, Steps(0)});
  for (uint64_t op = 100; op <= 3000; op += 100) {
    ring.Add({op, Steps(op)}, 4000);
  }
  // Ends at 2950 after 50 operations: n_end - n_win = 1950, so the window
  // starts at the sample at 1900, 1050 operations long.
  const Window w =
      JobWindow(true, {2900, Steps(2900)}, {2950, Steps(2950)}, ring, 1000);
  EXPECT_FALSE(w.own);
  EXPECT_EQ(w.start_op, 1900u);
  EXPECT_EQ(w.ops, 1050);
  EXPECT_EQ(w.counts[kSeek], 3 * 1050);
  // No begin record: the same rule.
  EXPECT_EQ(JobWindow(false, {}, {2950, Steps(2950)}, ring, 1000).start_op,
            1900u);
  // Never before the floor: early in the run the window starts there.
  StepRing early;
  early.Reset({500, Steps(500)});
  early.Add({600, Steps(600)}, 4000);
  const Window e =
      JobWindow(true, {650, Steps(650)}, {700, Steps(700)}, early, 1000);
  EXPECT_EQ(e.start_op, 500u);
  EXPECT_EQ(e.ops, 200);
  // A job that served nothing (in a stall or the drain) is charged over the
  // n_win operations before its end, as D §1 says.
  const Window stalled =
      JobWindow(true, {3000, Steps(3000)}, {3000, Steps(3000)}, ring, 1000);
  EXPECT_EQ(stalled.start_op, 2000u);
  EXPECT_EQ(stalled.ops, 1000);
}

TEST(Jobs, TheRingKeepsOneSampleBeforeItsHorizonAndSkipsStalls) {
  StepRing ring;
  ring.Reset({0, Steps(0)});
  for (uint64_t op = 100; op <= 10000; op += 100) {
    ring.Add({op, Steps(op)}, 1000);
    ring.Add({op, Steps(op)}, 1000);  // no operation served: not added
  }
  EXPECT_EQ(ring.size(), 11u);  // 9000 .. 10000
  EXPECT_EQ(ring.StartFor(9000).op, 9000u);
  EXPECT_EQ(ring.StartFor(8950).op, 0u);  // trimmed: the floor
}

TEST(Jobs, KindsAreTheEvaluators) {
  EXPECT_EQ(KindOf(true, false, -1), kFlush);
  EXPECT_EQ(KindOf(false, true, 0), kMove);
  EXPECT_EQ(KindOf(false, false, 0), kL0Merge);
  EXPECT_EQ(KindOf(false, false, 3), kDeepMerge);
}

TEST(Jobs, TauAndInterferenceMatchTheFormula) {
  const Config c = V2Config();
  // tau = c_job + c_cr (S + O) + c_w X; a move: c_job alone.
  EXPECT_DOUBLE_EQ(JobTau(c, kDeepMerge, 100, 50, 140), 3000 + 75 + 140);
  EXPECT_DOUBLE_EQ(JobTau(c, kMove, 100, 0, 0), 400);
  EXPECT_DOUBLE_EQ(JobBytes(c, kDeepMerge, 100, 50, 140), 140 + 0.25 * 150);
  EXPECT_DOUBLE_EQ(JobBytes(c, kMove, 100, 0, 0), 0);

  StepArray counts{};
  counts[kProbe] = 1000;
  counts[kPut] = 200;
  const StepArray rho = Rho(c, counts, 500);
  EXPECT_DOUBLE_EQ(rho[kProbe], 100.0 * 1000 / 500);
  EXPECT_DOUBLE_EQ(rho[kPut], 50.0 * 200 / 500);
  double read = 0, write = 0;
  Interference(c, kDeepMerge, 100, 50, 140, rho, &read, &write);
  const double y = 140 + 0.25 * 150;
  const double t = (3000 + 75 + 140) / 1e9;
  EXPECT_NEAR(read, c.q_bar * 200 * (1e-9 * y + 0.1 * t), 1e-12);
  EXPECT_NEAR(write, c.q_bar * 20 * (1e-9 * y), 1e-12);
  // Cost model 1 prices no job and charges no interference.
  Config v1 = test::TestConfig();
  Interference(v1, kDeepMerge, 100, 50, 140, rho, &read, &write);
  EXPECT_EQ(read, 0);
  EXPECT_EQ(write, 0);
  EXPECT_EQ(v1.BasePrice(kStep), 0);
  EXPECT_EQ(v1.BasePrice(kProbe), 100);
}

}  // namespace
}  // namespace rlc
