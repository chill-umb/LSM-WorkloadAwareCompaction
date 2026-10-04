// The learner modes against a fake host (plan step 10; PATHWAYS H §4-§6):
// prior-only decides on Q = -b; learned starts cold (Q = -b), loads a newer
// weights version and logs it with every decision; an invalid weights file,
// or a missing one when required, falls back (A-Impl-8); exploration never
// takes a masked action (ARCH-2); cost model 2 writes a job line per
// completed job with its window, and the learner's transitions carry their
// neighbours (H §3).
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>

#include "gtest/gtest.h"
#include "mlp.h"
#include "plugin.h"
#include "test_util.h"

namespace rlc {
namespace {

using test::BaseConfig;
using test::CountType;
using test::FakeHost;
using test::ReadLines;
using test::RLStepCounts;
using test::TempDir;
using test::WriteConfig;

const std::string kGolden =
    std::string(RL_CONTROLLER_FIXTURES) + "/mlp_golden.bin";

class LearnerTest : public testing::Test {
 protected:
  void SetUp() override { dir_ = TempDir(); }

  std::string Config(std::map<std::string, std::string> changes = {}) {
    auto values = BaseConfig(dir_);
    values["cost_model"] = "2";
    for (const char* key : {"c_cr", "c_ib", "lambda"}) values[key] = "0";
    for (const char* key : {"c_job_flush", "c_job_l0", "c_job_deep",
                            "c_job_move", "c_st", "c_mt", "c_get0", "c_sc0",
                            "c_put"}) {
      values[key] = "50";
    }
    values["p_dev"] = "1e9";
    values["n_win"] = "1000";
    for (const char* x : kStepTypeNames) {
      values[std::string("kappa_b_") + x] = "1e-9";
      for (const char* kind : kJobKindNames) {
        values[std::string("kappa_j_") + x + "_" + kind] = "0";
      }
    }
    for (const auto& change : changes) values[change.first] = change.second;
    return WriteConfig(dir_, values);
  }
  std::string Learned(std::map<std::string, std::string> changes = {}) {
    changes.emplace("mode", "\"learned\"");
    changes.emplace("explore", "0");
    changes.emplace("seed", "1");
    changes.emplace("weights_path", "\"" + WeightsPath() + "\"");
    changes.emplace("push_interval_ms", "1");
    changes.emplace("weights_required", "0");
    return Config(changes);
  }
  std::string WeightsPath() const { return dir_ + "/weights.bin"; }

  // As plugin_test.cc's Serve: every level's decision falls due.
  void Serve(FakeHost* host) {
    host->SetL0(3);
    host->SetLevel(1, 0.97);
    host->SetLevel(2, 0.97);
    host->SetLevel(3, 0.5);
    host->SetLevel(4, 0.3);
    Turnover(host);
  }
  void Turnover(FakeHost* host) {
    host->AddOps(60000, 40000);
    {
      std::lock_guard<std::mutex> lock(host->mu);
      host->steps.gets = host->ops.keys_read;
      host->steps.puts = host->ops.keys_written;
      host->steps.probes = 3 * host->ops.keys_read;
    }
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
  static std::vector<std::string> OfType(const std::vector<std::string>& lines,
                                         const std::string& type) {
    std::vector<std::string> out;
    for (const std::string& l : lines) {
      if (l.find("{\"type\":\"" + type + "\"") == 0) out.push_back(l);
    }
    return out;
  }
  // A JSON array after "key":[ as numbers.
  static std::vector<double> Array(const std::string& line,
                                   const std::string& key) {
    std::vector<double> out;
    const size_t at = line.find("\"" + key + "\":[");
    if (at == std::string::npos) return out;
    std::stringstream s(line.substr(at + key.size() + 4));
    for (double v; s >> v;) {
      out.push_back(v);
      if (s.peek() == ']') break;
      s.ignore(1);
    }
    return out;
  }
  static std::string Text(const std::string& line, const std::string& key) {
    const size_t at = line.find("\"" + key + "\":\"");
    if (at == std::string::npos) return "";
    const size_t start = at + key.size() + 4;
    return line.substr(start, line.find('"', start) - start);
  }

  std::string dir_;
  uint64_t clock_us_ = 1000000;
};

int ActionIndex(const std::string& name) {
  for (int a = 0; a < kNumActions; ++a) {
    if (name == ActionName(static_cast<Action>(a))) return a;
  }
  return -1;
}

TEST_F(LearnerTest, PriorOnlyTakesTheBestAllowedActionByThePrior) {
  FakeHost host;
  {
    Controller controller(&host, Config({{"mode", "\"prior-only\""},
                                         {"explore", "0"},
                                         {"seed", "3"}}));
    ASSERT_FALSE(controller.fallback());
    Serve(&host);
    controller.Step();
  }
  const auto decisions = OfType(Decisions(), "decision");
  ASSERT_EQ(decisions.size(), 5u);
  for (const std::string& d : decisions) {
    const auto q = Array(d, "q");
    const auto b = Array(d, "prior_cost");
    const auto mask = Array(d, "mask");
    ASSERT_EQ(q.size(), 4u) << d;
    int best = 0;
    for (int a = 0; a < 4; ++a) {
      EXPECT_DOUBLE_EQ(q[a], -b[a]);
      if (mask[a] == 1 && q[a] > q[best]) best = a;
    }
    EXPECT_EQ(ActionIndex(Text(d, "action")), best) << d;
    EXPECT_EQ(Text(d, "reason"), "greedy:prior");
    EXPECT_NE(d.find("\"weights\":0"), std::string::npos);
  }
}

TEST_F(LearnerTest, LearnedStartsColdThenUsesANewerWeightsVersion) {
  FakeHost host;
  {
    Controller controller(&host, Learned(), [this] { return clock_us_; });
    ASSERT_FALSE(controller.fallback());
    Serve(&host);
    controller.Step();  // no weights yet: Q = -b
    std::filesystem::copy_file(kGolden, WeightsPath());
    clock_us_ += 10000;
    Turnover(&host);
    controller.Step();  // loads version 3, then decides with it
    EXPECT_FALSE(controller.fallback());
  }
  // For a cross-check by hand with the trainer and 21_check_learner.py.
  if (const char* keep = std::getenv("RL_CONTROLLER_KEEP_LOGS")) {
    std::filesystem::create_directories(keep);
    for (const char* name : {"decisions.jsonl", "transitions.jsonl",
                             "config.json"}) {
      std::filesystem::copy_file(
          dir_ + "/" + name, std::string(keep) + "/" + name,
          std::filesystem::copy_options::overwrite_existing);
    }
  }
  const auto lines = Decisions();
  EXPECT_EQ(CountType(lines, "weights_missing"), 1);
  ASSERT_EQ(CountType(lines, "weights"), 1);
  const auto decisions = OfType(lines, "decision");
  ASSERT_EQ(decisions.size(), 10u);
  // The golden file has a model for every agent here (L0, L2, the shared
  // interior one, the last level).
  for (size_t i = 0; i < decisions.size(); ++i) {
    const std::string& d = decisions[i];
    if (i < 5) {
      EXPECT_EQ(Text(d, "reason"), "greedy:cold");
      EXPECT_NE(d.find("\"weights\":0,"), std::string::npos);
    } else {
      EXPECT_EQ(Text(d, "reason"), "greedy:learned") << d;
      EXPECT_NE(d.find("\"weights\":3,"), std::string::npos);
    }
  }
}

TEST_F(LearnerTest, AnInvalidWeightsFileFallsBack) {
  FakeHost host;
  std::ofstream(WeightsPath(), std::ios::binary) << "RLCW0001garbage";
  Controller controller(&host, Learned(), [this] { return clock_us_; });
  Serve(&host);
  controller.Step();
  EXPECT_TRUE(controller.fallback());
  controller.Stop();
  EXPECT_EQ(CountType(Decisions(), "fallback"), 1);
  EXPECT_EQ(CountType(Decisions(), "decision"), 0);
}

TEST_F(LearnerTest, AMissingRequiredWeightsFileFallsBack) {
  FakeHost host;
  Controller controller(&host, Learned({{"weights_required", "1"}}),
                        [this] { return clock_us_; });
  Serve(&host);
  controller.Step();
  EXPECT_TRUE(controller.fallback());
  controller.Stop();
  const auto lines = Decisions();
  bool said = false;
  for (const std::string& l : lines) {
    said = said || l.find("weights required but missing") != std::string::npos;
  }
  EXPECT_TRUE(said);
}

TEST_F(LearnerTest, ExplorationNeverTakesAMaskedAction) {
  FakeHost host;
  {
    Controller controller(&host, Config({{"mode", "\"prior-only\""},
                                         {"explore", "0.95"},
                                         {"seed", "11"}}));
    Serve(&host);
    for (int i = 0; i < 40; ++i) {
      controller.Step();
      Turnover(&host);
    }
  }
  int explored = 0;
  for (const std::string& d : OfType(Decisions(), "decision")) {
    const auto mask = Array(d, "mask");
    const int a = ActionIndex(Text(d, "action"));
    ASSERT_GE(a, 0);
    EXPECT_EQ(mask[a], 1) << d;
    explored += Text(d, "reason") == "explore:prior" ? 1 : 0;
  }
  EXPECT_GT(explored, 100);
}

TEST_F(LearnerTest, CostModel2WritesAJobLinePerCompletedJob) {
  FakeHost host;
  {
    Controller controller(&host, Config(), [this] { return clock_us_; });
    Serve(&host);
    controller.Step();
    // A merge of L2 with counters at its begin and end records, long enough
    // to be its own window, and a failed one, which is charged nothing.
    auto begin = FakeHost::Compaction(false, 90, 2, 100, 50, 0);
    begin.op = host.ops.total();
    begin.steps.gets = 1000;
    begin.steps.puts = 1000;
    host.AddOps(3000, 1000);
    auto end = FakeHost::Compaction(true, 90, 2, 100, 50, 140);
    end.op = host.ops.total();
    end.steps.gets = 4000;
    end.steps.puts = 2000;
    end.steps.probes = 500;
    host.Job(begin);
    host.Job(end);
    auto failed = FakeHost::Compaction(true, 91, 3, 100, 50, 0);
    failed.ok = false;
    host.Job(failed);
    controller.Step();
  }
  const auto jobs = OfType(Transitions(), "job");
  // Serve's 4 flushes and 4 merges, and the merge of L2 above.
  ASSERT_EQ(jobs.size(), 9u);
  const std::string& last = jobs.back();
  EXPECT_NE(last.find("\"kind\":\"deep\""), std::string::npos) << last;
  EXPECT_NE(last.find("\"win_ops\":4000"), std::string::npos) << last;
  EXPECT_NE(last.find("\"win_own\":1"), std::string::npos);
  EXPECT_NE(last.find("\"probe\":500"), std::string::npos);
  EXPECT_NE(last.find("\"get0\":3000"), std::string::npos);
  EXPECT_NE(last.find("\"put\":1000"), std::string::npos);
  int flushes = 0;
  for (const std::string& j : jobs) {
    flushes += j.find("\"kind\":\"flush\"") != std::string::npos ? 1 : 0;
  }
  EXPECT_EQ(flushes, 4);
}

TEST_F(LearnerTest, TransitionsCarryTheNeighboursAndTheDivisor) {
  FakeHost host;
  {
    Controller controller(&host, Config({{"mode", "\"prior-only\""},
                                         {"explore", "0"},
                                         {"seed", "3"}}));
    Serve(&host);
    controller.Step();
    Turnover(&host);
    controller.Step();
  }
  int checked = 0;
  for (const std::string& t : OfType(Transitions(), "transition")) {
    if (t.find("\"valid\":1") == std::string::npos) continue;
    const bool l2 = t.find("\"level\":2,") != std::string::npos;
    if (!l2) continue;
    EXPECT_EQ(Text(t, "up_agent"), "interior");
    EXPECT_EQ(Text(t, "down_agent"), "interior");
    EXPECT_NE(t.find("\"C\":16777216"), std::string::npos) << t;
    EXPECT_NE(t.find("\"up_C\":8388608"), std::string::npos);
    EXPECT_NE(t.find("\"down_C\":33554432"), std::string::npos);
    EXPECT_EQ(t.find("\"next_prior_cost\":null"), std::string::npos);
    ++checked;
  }
  EXPECT_EQ(checked, 1);
}

}  // namespace
}  // namespace rlc
