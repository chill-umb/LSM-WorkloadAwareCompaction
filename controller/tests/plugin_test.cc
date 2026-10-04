// The controller against a fake host: fallback (A-Impl-8), hold-only making
// no SetOptions call, one batched Apply per poll (A-Impl-5), the drain, and
// the logs covering every level from start to stop (OBJ-1).
#include "plugin.h"

#include <cmath>
#include <cstdlib>
#include <new>

#include "gtest/gtest.h"
#include "test_util.h"

// Allocation failure on demand, for one thread: the job callback must not let
// a bad_alloc reach the host. Replaces the global operator new for this test
// binary; it behaves normally unless the flag is set.
namespace {
thread_local bool fail_allocations = false;
}  // namespace

// The replacement pairs malloc with free, which GCC cannot see through.
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wmismatched-new-delete"
void* operator new(std::size_t size) {
  if (fail_allocations) throw std::bad_alloc();
  if (void* p = std::malloc(size == 0 ? 1 : size)) return p;
  throw std::bad_alloc();
}
void operator delete(void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }
#pragma GCC diagnostic pop

namespace rlc {
namespace {

using test::BaseConfig;
using test::CountType;
using test::FakeHost;
using test::ReadLines;
using test::TempDir;
using test::WriteConfig;

class PluginTest : public testing::Test {
 protected:
  void SetUp() override { dir_ = TempDir(); }

  std::string Config(std::map<std::string, std::string> changes = {},
                     const std::vector<std::string>& drop = {}) {
    auto values = BaseConfig(dir_);
    for (const auto& change : changes) values[change.first] = change.second;
    for (const std::string& key : drop) values.erase(key);
    return WriteConfig(dir_, values);
  }

  // A settled tree: L1 and L2 near due (s = 0.97), L3 half full, L4 the
  // last level. Then 100k operations and enough jobs that every level's
  // turnover length N_j is known (inflow of one nominal level each, so N_j =
  // 100k and a decision falls due every 25k operations with k = 4).
  void Serve(FakeHost* host) {
    host->SetL0(3);
    host->SetLevel(1, 0.97);
    host->SetLevel(2, 0.97);
    host->SetLevel(3, 0.5);
    host->SetLevel(4, 0.3);
    Turnover(host);
  }

  // 100k more operations and one more nominal level of inflow at every
  // level, so N_j stays 100k and every level's decision falls due again.
  void Turnover(FakeHost* host) {
    host->AddOps(60000, 40000);
    for (int i = 0; i < 4; ++i) host->Job(FakeHost::Flush(1024 * 1024));
    for (int level = 0; level < 4; ++level) {
      const uint64_t target =
          level == 0 ? 8 * 1024 * 1024 : (8u << 20) << (level);
      host->Job(FakeHost::Compaction(false, next_job_, level, 1, 0, 0));
      host->Job(
          FakeHost::Compaction(true, next_job_++, level, target, 0, target));
    }
  }

  int next_job_ = 10;

  std::vector<std::string> Decisions() {
    return ReadLines(dir_ + "/decisions.jsonl");
  }
  std::vector<std::string> Transitions() {
    return ReadLines(dir_ + "/transitions.jsonl");
  }

  std::string dir_;
};

TEST_F(PluginTest, AnInvalidConfigFallsBackOnceAndSaysWhy) {
  FakeHost host;
  host.snapshot->level_target_multipliers = {1, 1.5, 1, 1, 1};
  {
    Controller controller(&host, Config({}, {"m_min"}));
    EXPECT_TRUE(controller.fallback());
    ASSERT_EQ(host.applied.size(), 1u);
    EXPECT_EQ(host.applied[0].first, std::vector<double>(5, 1.0));
    EXPECT_EQ(host.applied[0].second, 4);
    Serve(&host);
    controller.Step();
    EXPECT_EQ(host.applied.size(), 1u);  // no decisions in fallback
  }
  const auto lines = Decisions();
  ASSERT_EQ(CountType(lines, "fallback"), 1);
  for (const std::string& line : lines) {
    if (line.find("\"type\":\"fallback\"") != std::string::npos) {
      EXPECT_NE(line.find("missing \\\"m_min\\\""), std::string::npos) << line;
    }
  }
  EXPECT_EQ(CountType(lines, "decision"), 0);
  // Attribution still covers every level from start to stop.
  EXPECT_EQ(CountType(Transitions(), "transition"), 5);
}

TEST_F(PluginTest, AnUnbuiltModeFallsBack) {
  FakeHost host;
  Controller controller(&host, Config({{"mode", "\"remote-inference\""}}));
  EXPECT_TRUE(controller.fallback());
  controller.Stop();
  EXPECT_NE(Decisions()[1].find("not built"), std::string::npos);
}

TEST_F(PluginTest, HoldOnlyDecidesAndLogsButNeverCallsSetOptions) {
  FakeHost host;
  // Even under a static profile, which rules mode would let relax.
  host.snapshot->level_target_multipliers = {1, 1.5, 1, 1, 1};
  {
    Controller controller(&host, Config());
    EXPECT_FALSE(controller.fallback());
    Serve(&host);
    controller.Step();
    // N_j grows with the operations served: 160k / k = 40k per decision.
    host.AddOps(60000, 0);
    controller.Step();
  }
  EXPECT_TRUE(host.applied.empty());
  const auto decisions = Decisions();
  // L0 to L4 decide at 100k and again at 160k.
  EXPECT_EQ(CountType(decisions, "decision"), 10);
  EXPECT_EQ(CountType(decisions, "apply"), 0);
  EXPECT_EQ(CountType(decisions, "stop"), 1);
  // Per level: the start interval, one decision interval, the close.
  const auto transitions = Transitions();
  EXPECT_EQ(CountType(transitions, "transition"), 15);
  int replayable = 0;
  for (const std::string& line : transitions) {
    replayable += line.find("\"valid\":1") != std::string::npos ? 1 : 0;
  }
  EXPECT_EQ(replayable, 5);
}

TEST_F(PluginTest, L0DecidesOncePerFlushInteriorLevelsEveryNOverK) {
  // G-iv as amended 2026-10-02: L0's state changes only at flushes and L0
  // compactions, so it decides every N_0 / K0_cfg operations (one flush),
  // not N_0 / k. With k = 10 that is 25k operations here against 11.5k.
  FakeHost host;
  {
    Controller controller(&host, Config({{"k", "10"}}));
    Serve(&host);
    controller.Step();  // every level decides at 100k
    host.AddOps(15000, 0);
    controller.Step();  // 115k: L1-L4 due (N_j / 10 = 11.5k), L0 not (28.75k)
    host.AddOps(25000, 0);
    controller.Step();  // 140k: L0 due (40k since 100k >= 35k), L1-L4 too
  }
  int l0 = 0, others = 0;
  for (const std::string& line : Decisions()) {
    if (line.find("\"type\":\"decision\"") == std::string::npos) continue;
    (line.find("\"level\":0,") != std::string::npos ? l0 : others)++;
  }
  EXPECT_EQ(l0, 2);
  EXPECT_EQ(others, 12);
}

TEST_F(PluginTest, RulesBatchEveryChangeOfAPollIntoOneApply) {
  FakeHost host;
  Controller controller(
      &host, Config({{"mode", "\"rules\""}, {"rules", "\"yield_slot\""}}));
  Serve(&host);  // L0 at 3 files, trigger 4: one flush short
  controller.Step();
  // L1 and L2 defer (s = 0.97 -> 1 / 1.05); L3 (s = 0.5) and L4 (last) hold.
  ASSERT_EQ(host.applied.size(), 1u);
  const std::vector<double>& m = host.applied[0].first;
  EXPECT_NEAR(m[1], 0.97 * 1.05, 1e-6);
  EXPECT_NEAR(m[2], 0.97 * 1.05, 1e-6);
  EXPECT_EQ(m[3], 1.0);
  EXPECT_EQ(m[4], 1.0);
  EXPECT_EQ(host.applied[0].second, 4);
  // Nothing changes at the next poll: no call.
  controller.Step();
  EXPECT_EQ(host.applied.size(), 1u);
  controller.Stop();
  const auto decisions = Decisions();
  EXPECT_EQ(CountType(decisions, "apply"), 1);
  int deferred = 0;
  for (const std::string& line : decisions) {
    if (line.find("\"action\":\"defer\"") == std::string::npos) continue;
    ++deferred;
    EXPECT_NE(line.find("\"reason\":\"rule:yield_slot\""), std::string::npos);
  }
  EXPECT_EQ(deferred, 2);
}

// ACT-3's instrument: the apply line says whether the snapshot read after
// the call carried the values it sent.
TEST_F(PluginTest, AnApplyLineSaysWhetherTheNextSnapshotCarriedIt) {
  for (const bool unseen : {false, true}) {
    SCOPED_TRACE(unseen);
    dir_ = TempDir();
    FakeHost host;
    host.apply_unseen = unseen;
    const uint64_t generation = host.snapshot->generation;
    Controller controller(
        &host, Config({{"mode", "\"rules\""}, {"rules", "\"yield_slot\""}}));
    Serve(&host);
    controller.Step();
    controller.Stop();
    ASSERT_EQ(host.applied.size(), 1u);
    int applies = 0;
    for (const std::string& line : Decisions()) {
      if (line.find("\"type\":\"apply\"") == std::string::npos) continue;
      ++applies;
      EXPECT_NE(line.find(unseen ? "\"seen\":0" : "\"seen\":1"),
                std::string::npos)
          << line;
      EXPECT_NE(line.find("\"seen_generation\":" +
                          std::to_string(generation + (unseen ? 0 : 1))),
                std::string::npos)
          << line;
    }
    EXPECT_EQ(applies, 1);
  }
}

TEST_F(PluginTest, RulesModeRelaxesADeviationAtTheLevelsOwnCadence) {
  FakeHost host;
  host.snapshot->level_target_multipliers = {1, 1.5, 1, 1, 1};
  Controller controller(&host, Config({{"mode", "\"rules\""},
                                       {"rules", "\"garbage_hold\""},
                                       {"rule_garbage_drop", "0.9"}}));
  Serve(&host);  // one turnover since the start at every level
  controller.Step();
  ASSERT_EQ(host.applied.size(), 1u);
  EXPECT_NEAR(host.applied[0].first[1], 1 + 0.5 * std::exp(-1.0), 1e-12);
  EXPECT_EQ(host.applied[0].first[2], 1.0);
}

TEST_F(PluginTest, AFailedApplyFallsBack) {
  FakeHost host;
  Controller controller(
      &host, Config({{"mode", "\"rules\""}, {"rules", "\"yield_slot\""}}));
  host.fail_apply = true;
  Serve(&host);
  controller.Step();
  EXPECT_TRUE(controller.fallback());
  controller.Stop();
  const auto decisions = Decisions();
  EXPECT_EQ(CountType(decisions, "fallback"), 1);
  EXPECT_NE(decisions.back().find("\"fallback\":1"), std::string::npos);
}

TEST_F(PluginTest, TheDrainStopsDecisions) {
  FakeHost host;
  Controller controller(
      &host, Config({{"mode", "\"rules\""}, {"rules", "\"yield_slot\""}}));
  host.draining = true;
  Serve(&host);
  controller.Step();
  EXPECT_TRUE(host.applied.empty());
  EXPECT_FALSE(controller.fallback());
  controller.Stop();
  EXPECT_EQ(CountType(Decisions(), "drain"), 1);
  EXPECT_EQ(CountType(Decisions(), "decision"), 0);
}

TEST_F(PluginTest, NoDecisionDuringAWriteStop) {
  FakeHost host;
  Controller controller(
      &host, Config({{"mode", "\"rules\""}, {"rules", "\"yield_slot\""}}));
  host.snapshot->write_stopped = true;
  Serve(&host);
  controller.Step();
  EXPECT_TRUE(host.applied.empty());
  host.snapshot->write_stopped = false;
  controller.Step();
  EXPECT_EQ(host.applied.size(), 1u);
}

TEST_F(PluginTest, TheCEntryPointsStartAndStopTheThread) {
  FakeHost host;
  void* controller = rl_controller_create(&host, Config().c_str());
  ASSERT_NE(controller, nullptr);
  EXPECT_TRUE(host.HasCallback());
  rl_controller_destroy(controller);
  EXPECT_FALSE(host.HasCallback());
  EXPECT_EQ(rl_controller_create(nullptr, "x"), nullptr);
}

// A clock the test moves by hand, in microseconds.
struct FakeClock {
  uint64_t now = 1000000;
  std::function<uint64_t()> Fn() {
    return [this] { return now; };
  }
};

std::map<std::string, std::string> Rules(const std::string& interval_ms) {
  return {{"mode", "\"rules\""},
          {"rules", "\"yield_slot\""},
          {"setoptions_min_interval_ms", interval_ms}};
}

TEST_F(PluginTest, SetOptionsWaitsForTheCapAndMergesTheHeldChange) {
  FakeHost host;
  FakeClock clock;
  Controller controller(&host, Config(Rules("100")), clock.Fn());
  Serve(&host);  // L1 and L2 defer at the first poll
  controller.Step();
  ASSERT_EQ(host.applied.size(), 1u);
  // 40 ms later L3 is near due and defers too: its change is held.
  clock.now += 40000;
  host.SetLevel(3, 0.97);
  Turnover(&host);
  controller.Step();
  EXPECT_EQ(host.applied.size(), 1u);
  EXPECT_EQ(CountType(Decisions(), "apply_held"), 1);
  clock.now += 59999;  // just under 100 ms since the call
  controller.Step();
  EXPECT_EQ(host.applied.size(), 1u);
  // At 100 ms the held change goes, without a new decision.
  clock.now += 1;
  controller.Step();
  ASSERT_EQ(host.applied.size(), 2u);
  EXPECT_NEAR(host.applied[1].first[3], 0.97 * 1.05, 1e-6);
  EXPECT_EQ(host.applied[1].first[1], host.applied[0].first[1]);
}

// Two identical runs, one capped at 100 ms and one effectively uncapped:
// calls are never closer than the cap, and once the cap has passed both
// hosts hold the same values, so no change was lost by merging.
TEST_F(PluginTest, TheCapNeverLosesAChange) {
  FakeHost capped_host, free_host;
  FakeClock clock;
  const std::string capped_dir = TempDir();
  const std::string free_dir = TempDir();
  auto capped_config = BaseConfig(capped_dir);
  auto free_config = BaseConfig(free_dir);
  for (const auto& [key, value] : Rules("100")) capped_config[key] = value;
  for (const auto& [key, value] : Rules("0.001")) free_config[key] = value;
  Controller capped(&capped_host, WriteConfig(capped_dir, capped_config),
                    clock.Fn());
  Controller uncapped(&free_host, WriteConfig(free_dir, free_config),
                      clock.Fn());
  std::vector<uint64_t> call_times;
  for (int step = 0; step < 60; ++step) {
    for (FakeHost* host : {&capped_host, &free_host}) {
      if (step == 0) Serve(host);
      host->SetL0(step % 3 == 0 ? 1 : 3);  // yield_slot on and off
      host->SetLevel(1 + step % 3, 0.95 + 0.01 * (step % 4));
    }
    Turnover(&capped_host);
    Turnover(&free_host);
    const size_t before = capped_host.applied.size();
    capped.Step();
    uncapped.Step();
    if (capped_host.applied.size() > before) call_times.push_back(clock.now);
    clock.now += 7000;  // 7 ms per poll
  }
  ASSERT_GT(call_times.size(), 3u);
  for (size_t i = 1; i < call_times.size(); ++i) {
    EXPECT_GE(call_times[i] - call_times[i - 1], 100000u);
  }
  EXPECT_GT(free_host.applied.size(), capped_host.applied.size());
  clock.now += 100000;
  capped.Step();
  uncapped.Step();
  EXPECT_EQ(capped_host.applied.back(), free_host.applied.back());
  EXPECT_FALSE(capped.fallback());
}

TEST_F(PluginTest, HoldOnlyNeverCallsSetOptionsEvenOutsideItsBounds) {
  FakeHost host;
  // A static m_1 = 1.5 under bounds whose m_max is 1.25: rules mode would
  // repair it, hold-only must leave it alone (ARCH-5).
  host.snapshot->level_target_multipliers = {1, 1.5, 1, 1, 1};
  Controller controller(&host, Config({{"m_max", "1.25"}}));
  ASSERT_FALSE(controller.fallback());
  Serve(&host);
  controller.Step();
  Turnover(&host);
  controller.Step();
  EXPECT_TRUE(host.applied.empty());
  EXPECT_EQ(CountType(Decisions(), "repair"), 0);
  EXPECT_EQ(CountType(Decisions(), "decision"), 10);
}

TEST_F(PluginTest, ANonUnitAdditionalMultiplierFallsBack) {
  FakeHost host;
  host.options.level_multiplier_additional = {1, 2, 1, 1, 1};
  Controller controller(&host, Config(Rules("100")));
  EXPECT_TRUE(controller.fallback());
  controller.Stop();
  EXPECT_NE(Decisions()[1].find("multiplier_additional"), std::string::npos);
}

TEST_F(PluginTest, AnUnreadableConfigFallsBackWithoutACall) {
  FakeHost host;  // m = 1 and the configured trigger already in effect
  testing::internal::CaptureStderr();
  Controller controller(&host, dir_ + "/missing.json");
  const std::string err = testing::internal::GetCapturedStderr();
  EXPECT_TRUE(controller.fallback());
  EXPECT_TRUE(host.applied.empty());  // nothing to change, so no SetOptions
  EXPECT_NE(err.find("cannot read"), std::string::npos) << err;
}

TEST_F(PluginTest, AFailedLogWriteFallsBackAndSaysSoOnStderr) {
  FakeHost host;
  Controller controller(&host, Config({{"decision_log", "\"/dev/full\""}}));
  ASSERT_FALSE(controller.fallback());
  testing::internal::CaptureStderr();
  controller.Step();
  const std::string err = testing::internal::GetCapturedStderr();
  EXPECT_TRUE(controller.fallback());
  EXPECT_NE(err.find("a log write failed"), std::string::npos) << err;
}

TEST_F(PluginTest, AJobRecordThatCannotBeQueuedNeverReachesTheHost) {
  FakeHost host;
  Controller controller(&host, Config(Rules("100")));
  bool threw = false;
  fail_allocations = true;  // the queue's first push_back must allocate
  try {
    host.Job(FakeHost::Flush(1 << 20));
  } catch (...) {
    threw = true;
  }
  fail_allocations = false;
  EXPECT_FALSE(threw);
  // The lost record makes the attribution incomplete: the next poll falls
  // back.
  controller.Step();
  EXPECT_TRUE(controller.fallback());
  controller.Stop();
  bool said = false;
  for (const std::string& line : Decisions()) {
    said = said || line.find("a job record was lost") != std::string::npos;
  }
  EXPECT_TRUE(said);
}

// A slot wait may start at a due-since older than the clock's minute; its
// samples must survive until the job's begin record has been read, even when
// that record arrives a poll after the snapshot in which the level stopped
// being due (G §3).
TEST_F(PluginTest, ALongWaitIsNotUnderCounted) {
  FakeHost host;
  FakeClock clock;  // starts at 1 s
  Controller controller(&host, Config(), clock.Fn());
  constexpr uint64_t kSecond = 1000 * 1000;
  host.SetLevel(2, 1.2);
  host.snapshot->levels[2].due_since_micros = 5 * kSecond;  // op 400
  for (uint64_t s = 2; s <= 100; ++s) {
    clock.now = s * kSecond;
    host.AddOps(100, 0);
    controller.Step();
  }
  // The job is picked: the level is no longer due in the next snapshot, and
  // its begin record arrives only a poll later.
  host.snapshot->levels[2].due_since_micros = 0;
  clock.now = 101 * kSecond;
  host.AddOps(100, 0);
  controller.Step();
  auto begin = FakeHost::Compaction(false, 1, 2, 100, 100, 0);
  begin.due_since_micros = 5 * kSecond;
  begin.op = host.OpCounts().total();  // 10000
  begin.t_micros = 102 * kSecond;
  auto end = FakeHost::Compaction(true, 1, 2, 100, 100, 150);
  end.op = begin.op;
  end.t_micros = 102 * kSecond;
  host.Job(begin);
  host.Job(end);
  clock.now = 102 * kSecond;
  controller.Step();
  controller.Stop();
  // The level-2 interval closed at stop carries the wait: 10000 - 400.
  std::string line;
  for (const std::string& l : Transitions()) {
    if (l.find("\"level\":2,") != std::string::npos) line = l;
  }
  ASSERT_FALSE(line.empty());
  EXPECT_NE(line.find("\"wait_ops\":9600,\"waits\":1"), std::string::npos)
      << line;
}

TEST_F(PluginTest, AHostErrorNeverLeavesThePlugin) {
  FakeHost host;
  Controller controller(&host, Config(Rules("100")));
  host.throw_in_op_counts = true;
  testing::internal::CaptureStderr();
  controller.Step();  // must not throw
  controller.Stop();  // nor here, though the host still throws
  testing::internal::GetCapturedStderr();
  EXPECT_TRUE(controller.fallback());
}

}  // namespace
}  // namespace rlc
