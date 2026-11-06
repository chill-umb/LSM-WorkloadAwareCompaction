// learned2 (learned2.h; owner, 2026-11-05): F1 persistent moves, F2 settle
// and measure before deciding, F3 the ensemble's vote, F4 the clean inputs,
// and the window lines the trainer prices (F5).
#include <algorithm>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <sstream>

#include "gtest/gtest.h"
#include "learned2.h"
#include "mlp.h"
#include "plugin.h"
#include "test_util.h"

namespace rlc {
namespace {

using test::BaseConfig;
using test::CountType;
using test::FakeHost;
using test::ReadLines;
using test::TempDir;
using test::WriteConfig;

// A one-layer model: output 0 = slope * x[own] + bias, the other three 0.
Model Linear(int level, int own, double slope, double bias = 0) {
  Model m;
  m.agent = Agent::kF;
  m.level = level;
  m.n_in = static_cast<uint32_t>(F2FeatureNames().size());
  m.features_hash = FeaturesHash(Agent::kF);
  m.clip = 1e6;
  Layer l;
  l.out = kNumActions;
  l.in = m.n_in;
  l.w.assign(l.out * l.in, 0.0);
  l.b.assign(l.out, 0.0);
  l.w[own] = slope;
  l.b[0] = bias;
  m.layers.push_back(l);
  return m;
}

std::vector<double> SomeInputs() {
  std::vector<double> m = {1, 1, 1, 1, 1};
  return F2Features(0.2, 0.7, 0.1, 2e10, 1000000, 4, m);
}

Mask AllMoves() { return Mask{true, true, true, false}; }

TEST(Learned2, TheInputsAreTheTwelveCleanFeatures) {
  EXPECT_EQ(F2FeatureNames().size(), 12u);
  EXPECT_EQ(FeatureNames(Agent::kF), F2FeatureNames());
  EXPECT_STREQ(AgentName(Agent::kF), "f");
  // rl_agent/tests/test_learned2.py checks the same hash from Python.
  EXPECT_EQ(FeaturesHash(Agent::kF), 0x4d7a0f34e38d4d67ull);
  const std::vector<double> m = {1, 2, 0.5};  // levels 3..6 absent: m = 1
  const auto x = F2Features(0.25, NAN, 0.25, 1e10, 999, 8, m);
  ASSERT_EQ(x.size(), 12u);
  EXPECT_DOUBLE_EQ(x[kF2PutShare], 0.25);
  EXPECT_DOUBLE_EQ(x[kF2GetShare], 0.0);  // NaN never reaches the model
  EXPECT_DOUBLE_EQ(x[kF2LogH], 10.0);
  EXPECT_DOUBLE_EQ(x[kF2LogOp], 3.0);
  EXPECT_DOUBLE_EQ(x[kF2LogK], 3.0);
  EXPECT_DOUBLE_EQ(x[kF2LogM1], 1.0);
  EXPECT_DOUBLE_EQ(x[kF2LogM1 + 1], -1.0);
  for (int i = 2; i < kF2Levels; ++i) EXPECT_DOUBLE_EQ(x[kF2LogM1 + i], 0.0);
  for (double v : x) EXPECT_TRUE(std::isfinite(v));
}

TEST(Learned2, ActionsAreNamedHoldDownUp) {
  EXPECT_STREQ(F2ActionName(Action::kHold), "hold");
  EXPECT_STREQ(F2ActionName(Action::kCompact), "down");
  EXPECT_STREQ(F2ActionName(Action::kDefer), "up");
}

TEST(Learned2, WithoutModelsItHoldsUnlessItExplores) {
  const auto x = SomeInputs();
  const std::array<double, kNumActions> after = {0, 1, 3, 0};
  F2Choice c = F2Decide({}, x, kF2LogK, after, AllMoves(), 0.1, 0.9, 0.5, 0.3);
  EXPECT_EQ(c.action, Action::kHold);
  EXPECT_EQ(c.reason, "cold");
  c = F2Decide({}, x, kF2LogK, after, AllMoves(), 0.1, 0.9, 0.05, 0.3);
  EXPECT_EQ(c.action, Action::kCompact);
  EXPECT_EQ(c.reason, "explore");
  c = F2Decide({}, x, kF2LogK, after, AllMoves(), 0.1, 0.9, 0.05, 0.9);
  EXPECT_EQ(c.action, Action::kDefer);
}

TEST(Learned2, ExplorationNeverTakesAForbiddenMove) {
  const auto x = SomeInputs();
  const std::array<double, kNumActions> after = {0, 1, 3, 0};
  const Mask only_up = {true, false, true, false};
  for (double u = 0; u < 1; u += 0.05) {
    const F2Choice c =
        F2Decide({}, x, kF2LogK, after, only_up, 0.5, 0.9, 0.0, u);
    EXPECT_EQ(c.action, Action::kDefer);
  }
  const Mask hold_only = {true, false, false, false};
  const F2Choice c =
      F2Decide({}, x, kF2LogK, after, hold_only, 0.99, 0.9, 0.0, 0.5);
  EXPECT_EQ(c.action, Action::kHold);
}

TEST(Learned2, AConfidentEnsembleMovesAndASplitOneHolds) {
  const auto x = SomeInputs();  // log2 K = 2
  const std::array<double, kNumActions> after = {0, 1, 3, 0};  // K 2 or 8
  // Cost rises with K in every member: down is greedy and unanimous.
  std::vector<Model> members;
  for (int e = 0; e < 10; ++e) members.push_back(Linear(0, kF2LogK, 1 + e));
  std::vector<const Model*> ptrs;
  for (const Model& m : members) ptrs.push_back(&m);
  F2Choice c = F2Decide(ptrs, x, kF2LogK, after, AllMoves(), 0, 0.9, 0.5, 0.5);
  EXPECT_EQ(c.action, Action::kCompact);
  EXPECT_EQ(c.reason, "confident");
  EXPECT_EQ(c.votes, 10);
  EXPECT_EQ(c.need, 9);
  EXPECT_LT(c.mean[1], c.mean[0]);
  EXPECT_TRUE(std::isnan(c.mean[3]));
  // Two members of ten disagree: one so strongly that the mean prefers up;
  // then less strongly, so the mean prefers down but only 8 of 10 agree.
  members[0] = Linear(0, kF2LogK, -100);
  members[1] = Linear(0, kF2LogK, -0.5);
  c = F2Decide(ptrs, x, kF2LogK, after, AllMoves(), 0, 0.9, 0.5, 0.5);
  EXPECT_EQ(c.greedy, Action::kDefer);  // -100 dominates the mean
  members[0] = Linear(0, kF2LogK, -5);
  c = F2Decide(ptrs, x, kF2LogK, after, AllMoves(), 0, 0.9, 0.5, 0.5);
  EXPECT_EQ(c.greedy, Action::kCompact);
  EXPECT_EQ(c.votes, 8);
  EXPECT_EQ(c.action, Action::kHold);
  EXPECT_EQ(c.reason, "gate");
  // A flat ensemble: hold is greedy.
  for (Model& m : members) m = Linear(0, kF2LogK, 0, 1);
  c = F2Decide(ptrs, x, kF2LogK, after, AllMoves(), 0, 0.9, 0.5, 0.5);
  EXPECT_EQ(c.action, Action::kHold);
  EXPECT_EQ(c.reason, "hold");
}

TEST(Learned2, AConfidentMoveAlsoNeedsTheMinimumEffect) {
  // The noise fix (owner, 2026-11-05): a unanimous vote for a gain smaller
  // than min_effect holds ("small").
  const auto x = SomeInputs();  // log2 K = 2
  const std::array<double, kNumActions> after = {0, 1, 3, 0};
  std::vector<Model> members;
  for (int e = 0; e < 10; ++e) members.push_back(Linear(0, kF2LogK, 0.01));
  std::vector<const Model*> ptrs;
  for (const Model& m : members) ptrs.push_back(&m);
  // Down saves 0.01 x (2 - 1) = 0.01 in every member.
  F2Choice c =
      F2Decide(ptrs, x, kF2LogK, after, AllMoves(), 0, 0.9, 0.5, 0.5, 0.02);
  EXPECT_EQ(c.greedy, Action::kCompact);
  EXPECT_EQ(c.votes, 10);
  EXPECT_EQ(c.action, Action::kHold);
  EXPECT_EQ(c.reason, "small");
  c = F2Decide(ptrs, x, kF2LogK, after, AllMoves(), 0, 0.9, 0.5, 0.5, 0.005);
  EXPECT_EQ(c.action, Action::kCompact);
  EXPECT_EQ(c.reason, "confident");
}

TEST(Learned2, TheTrialVerdictNeedsTheVoteAndTheMinimumEffect) {
  const std::vector<std::vector<double>> xs = {SomeInputs(), SomeInputs()};
  const double k4 = 2, k5 = std::log2(5.0);
  std::vector<Model> members;
  for (int e = 0; e < 10; ++e) members.push_back(Linear(0, kF2LogK, -1));
  std::vector<const Model*> ptrs;
  for (const Model& m : members) ptrs.push_back(&m);
  // Cost falls with K: K = 5 is cheaper by log2(5/4) = 0.32 in every member.
  F2Verdict v = F2TrialVerdict(ptrs, xs, kF2LogK, k4, k5, 0.9, 0.01);
  EXPECT_EQ(v.verdict, 1);
  EXPECT_EQ(v.cheaper, 10);
  EXPECT_EQ(v.need, 9);
  EXPECT_NEAR(v.effect, -std::log2(1.25), 1e-12);
  // The same pair judged the other way round: the candidate is dearer.
  v = F2TrialVerdict(ptrs, xs, kF2LogK, k5, k4, 0.9, 0.01);
  EXPECT_EQ(v.verdict, -1);
  EXPECT_EQ(v.dearer, 10);
  // Too small an effect: no verdict either way.
  v = F2TrialVerdict(ptrs, xs, kF2LogK, k4, k5, 0.9, 0.5);
  EXPECT_EQ(v.verdict, 0);
  // Two members of ten disagree: 8 < 9, no verdict.
  members[0] = Linear(0, kF2LogK, 1);
  members[1] = Linear(0, kF2LogK, 1);
  v = F2TrialVerdict(ptrs, xs, kF2LogK, k4, k5, 0.9, 0.01);
  EXPECT_EQ(v.cheaper, 8);
  EXPECT_EQ(v.verdict, 0);
  // No models or no inputs: no verdict.
  EXPECT_EQ(F2TrialVerdict({}, xs, kF2LogK, k4, k5, 0.9, 0).verdict, 0);
  EXPECT_EQ(F2TrialVerdict(ptrs, {}, kF2LogK, k4, k5, 0.9, 0).verdict, 0);
}

TEST(Learned2, FindAllReturnsEveryModelOfTheLevelInFileOrder) {
  Weights w;
  w.models.push_back(Linear(0, kF2LogK, 1));
  w.models.push_back(Linear(1, kF2LogM1, 2));
  w.models.push_back(Linear(0, kF2LogK, 3));
  Model shared = Linear(-1, kF2LogK, 4);
  w.models.push_back(shared);
  const auto l0 = w.FindAll(Agent::kF, 0);
  ASSERT_EQ(l0.size(), 2u);
  EXPECT_EQ(l0[0], &w.models[0]);
  EXPECT_EQ(l0[1], &w.models[2]);
  EXPECT_EQ(w.FindAll(Agent::kF, 1).size(), 1u);
  EXPECT_TRUE(w.FindAll(Agent::kF, 2).empty());  // no level -1 fallback
  EXPECT_TRUE(w.FindAll(Agent::kL0, 0).empty());
}

// The weights file of mlp.h's layout, written here byte by byte.
void WriteWeights(const std::string& path, uint64_t version,
                  const std::vector<Model>& models) {
  std::string body;
  const auto put = [&body](const void* p, size_t n) {
    body.append(static_cast<const char*>(p), n);
  };
  const uint32_t n = static_cast<uint32_t>(models.size());
  put(&n, 4);
  for (const Model& m : models) {
    const uint32_t agent = 3;
    const int32_t level = m.level;
    put(&agent, 4);
    put(&level, 4);
    put(&m.features_hash, 8);
    put(&m.n_in, 4);
    put(&m.clip, 8);
    const uint32_t layers = static_cast<uint32_t>(m.layers.size());
    put(&layers, 4);
    for (const Layer& l : m.layers) {
      put(&l.out, 4);
      put(&l.in, 4);
      put(l.w.data(), 8 * l.w.size());
      put(l.b.data(), 8 * l.b.size());
    }
    const uint32_t has_delta = 0;
    put(&has_delta, 4);
  }
  const uint64_t length = body.size();
  const uint64_t checksum = Fnv1a64(body.data(), body.size());
  std::ofstream out(path, std::ios::binary);
  out.write("RLCW0001", 8);
  out.write(reinterpret_cast<const char*>(&version), 8);
  out.write(reinterpret_cast<const char*>(&length), 8);
  out.write(reinterpret_cast<const char*>(&checksum), 8);
  out.write(body.data(), static_cast<std::streamsize>(body.size()));
}

TEST(Learned2, AnFAgentWeightsFileLoads) {
  const std::string dir = TempDir();
  std::vector<Model> models;
  for (int e = 0; e < 3; ++e) models.push_back(Linear(0, kF2LogK, 1 + e));
  WriteWeights(dir + "/w.bin", 7, models);
  Weights w;
  std::string error;
  ASSERT_EQ(LoadWeights(dir + "/w.bin", &w, &error), LoadStatus::kOk) << error;
  EXPECT_EQ(w.version, 7u);
  ASSERT_EQ(w.FindAll(Agent::kF, 0).size(), 3u);
  const auto x = SomeInputs();
  EXPECT_DOUBLE_EQ(Forward(*w.FindAll(Agent::kF, 0)[1], x)[0], 2 * x[kF2LogK]);
}

class Learned2Test : public testing::Test {
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
    values["mode"] = "\"learned2\"";
    values["explore"] = "0";
    values["seed"] = "1";
    values["weights_path"] = "\"" + dir_ + "/weights.bin\"";
    values["push_interval_ms"] = "1";
    values["weights_required"] = "0";
    values["window_ops"] = "1000";
    values["ensemble"] = "10";
    values["vote_share"] = "0.9";
    values["settle_turnovers"] = "1";
    values["measure_turnovers"] = "1";
    values["min_decision_ops"] = "1000";
    for (const auto& change : changes) values[change.first] = change.second;
    return WriteConfig(dir_, values);
  }

  // As learner_test.cc's: one turnover's inflow at every level per call.
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

  std::vector<std::string> Lines() { return ReadLines(dir_ + "/decisions.jsonl"); }
  static std::vector<std::string> OfType(const std::vector<std::string>& lines,
                                         const std::string& type) {
    std::vector<std::string> out;
    for (const std::string& l : lines) {
      if (l.find("{\"type\":\"" + type + "\"") == 0) out.push_back(l);
    }
    return out;
  }
  static std::string Text(const std::string& line, const std::string& key) {
    const size_t at = line.find("\"" + key + "\":\"");
    if (at == std::string::npos) return "";
    const size_t start = at + key.size() + 4;
    return line.substr(start, line.find('"', start) - start);
  }
  static double Number(const std::string& line, const std::string& key) {
    const size_t at = line.find("\"" + key + "\":");
    if (at == std::string::npos) return NAN;
    return std::strtod(line.c_str() + at + key.size() + 3, nullptr);
  }

  std::string dir_;
  uint64_t clock_us_ = 1000000;
};

TEST_F(Learned2Test, TheConfigNeedsItsKeysAndOtherModesRefuseThem) {
  rlc::Config c;
  std::string error;
  ASSERT_TRUE(LoadConfig(Config(), &c, &error)) << error;
  EXPECT_EQ(c.mode, Mode::kLearned2);
  EXPECT_EQ(c.ensemble, 10);
  EXPECT_DOUBLE_EQ(c.window_ops, 1000);
  EXPECT_FALSE(LoadConfig(Config({{"vote_share", "0.5"}}), &c, &error));
  EXPECT_FALSE(LoadConfig(Config({{"min_decision_ops", "999"}}), &c, &error));
  auto values = BaseConfig(dir_);
  values["window_ops"] = "1000";
  EXPECT_FALSE(LoadConfig(WriteConfig(dir_, values), &c, &error));
  EXPECT_NE(error.find("is for learned2"), std::string::npos) << error;
}

TEST_F(Learned2Test, ColdItWritesWindowsAndHoldsWithoutApplying) {
  FakeHost host;
  {
    Controller controller(&host, Config(), [this] { return clock_us_; });
    ASSERT_FALSE(controller.fallback());
    Serve(&host);
    for (int i = 0; i < 6; ++i) {
      controller.Step();
      clock_us_ += 200000;
      Turnover(&host);
    }
    controller.Step();
    EXPECT_FALSE(controller.fallback());
  }
  const auto lines = Lines();
  const auto windows = OfType(lines, "window");
  EXPECT_GE(windows.size(), 5u);
  for (const std::string& w : windows) {
    EXPECT_NE(w.find("\"log2_k\":2"), std::string::npos) << w;
    EXPECT_NE(w.find("\"changed\":0"), std::string::npos) << w;
  }
  const auto decisions = OfType(lines, "decision");
  ASSERT_FALSE(decisions.empty());
  for (const std::string& d : decisions) {
    EXPECT_EQ(Text(d, "agent"), "f");
    EXPECT_EQ(Text(d, "action"), "hold");
    EXPECT_EQ(Text(d, "reason"), "cold");
  }
  EXPECT_EQ(OfType(lines, "f2").size(), decisions.size());
  EXPECT_EQ(host.Applies(), 0u);
}

TEST_F(Learned2Test, AMoveIsAppliedAndTheAgentWaitsForItToSettle) {
  FakeHost host;
  {
    // Every decision explores; a change must take two turnovers of inflow
    // before its agent decides again.
    Controller controller(
        &host, Config({{"explore", "0.999"}, {"settle_turnovers", "2"}}),
        [this] { return clock_us_; });
    Serve(&host);
    for (int i = 0; i < 8; ++i) {
      controller.Step();
      clock_us_ += 200000;
      Turnover(&host);
    }
    controller.Step();
  }
  const auto lines = Lines();
  const auto f2 = OfType(lines, "f2");
  ASSERT_FALSE(f2.empty());
  std::map<int, std::vector<double>> ops_by_level;
  for (const std::string& d : f2) {
    EXPECT_EQ(Text(d, "reason"), "explore") << d;
    EXPECT_NE(Text(d, "move"), "hold") << d;
    ops_by_level[static_cast<int>(Number(d, "level"))].push_back(Number(d, "op"));
  }
  // One turnover of inflow arrives per step, so a moved agent skips a step.
  for (const auto& entry : ops_by_level) {
    for (size_t i = 1; i < entry.second.size(); ++i) {
      EXPECT_GE(entry.second[i] - entry.second[i - 1], 2 * 100000.0)
          << "level " << entry.first;
    }
  }
  EXPECT_GT(host.Applies(), 0u);
  // A window in which a setting changed says so, and its moved agent is
  // not settled in it.
  bool saw_changed = false;
  for (const std::string& w : OfType(lines, "window")) {
    saw_changed = saw_changed || w.find("\"changed\":1") != std::string::npos;
  }
  EXPECT_TRUE(saw_changed);
}

TEST_F(Learned2Test, TheL0MoveStaysWithinTheLiveCap) {
  FakeHost host;
  {
    Controller controller(&host, Config({{"explore", "0.999"}}),
                          [this] { return clock_us_; });
    Serve(&host);
    for (int i = 0; i < 30; ++i) {
      controller.Step();
      clock_us_ += 200000;
      Turnover(&host);
    }
  }
  for (const std::string& d : OfType(Lines(), "f2")) {
    if (Number(d, "level") != 0) continue;
    const double k = Number(d, "new");
    EXPECT_GE(k, 2) << d;
    EXPECT_LE(k, 8) << d;
  }
}

// The noise fix (owner, 2026-11-05): windows cut at L0 merges, K trials,
// frozen level agents.
std::map<std::string, std::string> CycleTrials() {
  return {{"window_ops", "10000000"},      {"min_decision_ops", "10000000"},
          {"window_cycles", "1"},          {"window_min_ops", "0"},
          {"min_effect", "0.01"},          {"deep_frozen", "1"},
          {"k_trials", "1"},               {"trial_block_ops", "150000"},
          {"trial_min_blocks", "2"},       {"trial_max_blocks", "4"},
          {"trial_rest_ops", "0"}};
}

TEST_F(Learned2Test, TheNoiseFixKeysAreCheckedAndOptional) {
  rlc::Config c;
  std::string error;
  ASSERT_TRUE(LoadConfig(Config(), &c, &error)) << error;
  EXPECT_EQ(c.window_cycles, 0);
  EXPECT_FALSE(c.k_trials);
  EXPECT_FALSE(c.deep_frozen);
  ASSERT_TRUE(LoadConfig(Config(CycleTrials()), &c, &error)) << error;
  EXPECT_EQ(c.window_cycles, 1);
  EXPECT_TRUE(c.k_trials);
  EXPECT_TRUE(c.deep_frozen);
  EXPECT_DOUBLE_EQ(c.min_effect, 0.01);
  EXPECT_EQ(c.trial_recent, 20);
  auto no_cycles = CycleTrials();
  no_cycles["window_cycles"] = "0";
  EXPECT_FALSE(LoadConfig(Config(no_cycles), &c, &error));
  EXPECT_NE(error.find("k_trials needs window_cycles"), std::string::npos) << error;
  auto lone_trial_key = std::map<std::string, std::string>{{"trial_rest_ops", "5"}};
  EXPECT_FALSE(LoadConfig(Config(lone_trial_key), &c, &error));
  EXPECT_NE(error.find("needs k_trials 1"), std::string::npos) << error;
  auto too_few = CycleTrials();
  too_few["trial_min_blocks"] = "1";
  EXPECT_FALSE(LoadConfig(Config(too_few), &c, &error));
  auto values = BaseConfig(dir_);
  values["window_cycles"] = "1";
  EXPECT_FALSE(LoadConfig(WriteConfig(dir_, values), &c, &error));
  EXPECT_NE(error.find("is for learned2"), std::string::npos) << error;
}

TEST_F(Learned2Test, CycleWindowsCloseAtTheL0MergesAndDecideOnlyThere) {
  FakeHost host;
  {
    auto keys = CycleTrials();
    keys["k_trials"] = "0";
    for (const char* k : {"trial_block_ops", "trial_min_blocks",
                          "trial_max_blocks", "trial_rest_ops"}) {
      keys.erase(k);
    }
    keys["window_cycles"] = "2";
    keys["deep_frozen"] = "0";
    keys["explore"] = "0.999";
    keys["min_decision_ops"] = "10000000";
    Controller controller(&host, Config(keys), [this] { return clock_us_; });
    Serve(&host);
    for (int i = 0; i < 9; ++i) {
      controller.Step();
      clock_us_ += 200000;
      Turnover(&host);  // 100k operations and one L0 merge
    }
    controller.Step();
  }
  const auto lines = Lines();
  const auto windows = OfType(lines, "window");
  // Nine merges after the first window opened: four windows of two.
  ASSERT_EQ(windows.size(), 4u);
  std::vector<double> ends;
  for (const std::string& w : windows) {
    EXPECT_EQ(Text(w, "cut"), "cycle") << w;
    EXPECT_EQ(Number(w, "merges"), 2) << w;
    EXPECT_EQ(Number(w, "op1") - Number(w, "op0"), 200000) << w;
    EXPECT_EQ(Number(w, "changed"), 0) << w;
    ends.push_back(Number(w, "op1"));
  }
  // Every decision at a window's end.
  for (const std::string& d : OfType(lines, "f2")) {
    EXPECT_NE(std::find(ends.begin(), ends.end(), Number(d, "op")), ends.end())
        << d;
  }
}

TEST_F(Learned2Test, ATrialAlternatesKAndGivesUpWithoutAModel) {
  FakeHost host;
  {
    auto keys = CycleTrials();
    keys["explore"] = "0.999";  // ignored by the trial and the frozen levels
    keys["window_ops"] = "200000";
    keys["min_decision_ops"] = "200000";
    Controller controller(&host, Config(keys), [this] { return clock_us_; });
    Serve(&host);
    for (int i = 0; i < 16; ++i) {
      controller.Step();
      clock_us_ += 200000;
      Turnover(&host);
    }
    controller.Step();
  }
  const auto lines = Lines();
  std::vector<std::string> reasons;
  std::vector<double> ks;
  for (const std::string& d : OfType(lines, "f2")) {
    if (Number(d, "level") != 0) {
      EXPECT_EQ(Text(d, "reason"), "frozen") << d;
      EXPECT_EQ(Text(d, "move"), "hold") << d;
      continue;
    }
    reasons.push_back(Text(d, "reason"));
    ks.push_back(Number(d, "new"));
  }
  // Up first, blocks of two windows, no model: inconclusive after four
  // blocks; then a block at K = 4 and the other way.
  const std::vector<std::string> want_reasons = {
      "trial", "back", "trial", "back", "inconclusive", "trial", "back"};
  const std::vector<double> want_ks = {5, 4, 5, 4, 4, 3, 4};
  ASSERT_GE(reasons.size(), want_reasons.size());
  for (size_t i = 0; i < want_reasons.size(); ++i) {
    EXPECT_EQ(reasons[i], want_reasons[i]) << i;
    EXPECT_EQ(ks[i], want_ks[i]) << i;
  }
  EXPECT_EQ(OfType(lines, "trial").size(), reasons.size());
  // Switches come at window boundaries: no window is half under each K, and
  // the L0 agent counts as settled in every one.
  for (const std::string& w : OfType(lines, "window")) {
    EXPECT_EQ(Number(w, "changed"), 0) << w;
    EXPECT_NE(w.find("\"settled\":[1,"), std::string::npos) << w;
  }
  // Only K moved.
  for (const std::string& d : OfType(lines, "decision")) {
    if (Number(d, "level") != 0) {
      EXPECT_EQ(Text(d, "action"), "hold") << d;
    }
  }
  EXPECT_GT(host.Applies(), 0u);
}

TEST_F(Learned2Test, AConfidentTrialAdoptsTheNeighbourAndGoesOn) {
  // Every L0 member says the cost falls with K.
  std::vector<Model> models;
  for (int e = 0; e < 10; ++e) models.push_back(Linear(0, kF2LogK, -1));
  WriteWeights(dir_ + "/weights.bin", 1, models);
  FakeHost host;
  {
    auto keys = CycleTrials();
    keys["window_ops"] = "200000";
    keys["min_decision_ops"] = "200000";
    Controller controller(&host, Config(keys), [this] { return clock_us_; });
    Serve(&host);
    for (int i = 0; i < 12; ++i) {
      controller.Step();
      clock_us_ += 200000;
      Turnover(&host);
    }
    controller.Step();
  }
  std::vector<std::string> reasons;
  std::vector<double> ks;
  for (const std::string& d : OfType(Lines(), "f2")) {
    if (Number(d, "level") != 0) continue;
    reasons.push_back(Text(d, "reason"));
    ks.push_back(Number(d, "new"));
  }
  // 4 -> 5 (trial), back to 4, judged after two blocks: adopt 5; a block
  // at 5, then on upwards: 6.
  const std::vector<std::string> want_reasons = {"trial", "back", "adopt",
                                                 "trial"};
  const std::vector<double> want_ks = {5, 4, 5, 6};
  ASSERT_GE(reasons.size(), want_reasons.size());
  for (size_t i = 0; i < want_reasons.size(); ++i) {
    EXPECT_EQ(reasons[i], want_reasons[i]) << i;
    EXPECT_EQ(ks[i], want_ks[i]) << i;
  }
  bool judged = false;
  for (const std::string& t : OfType(Lines(), "trial")) {
    if (Text(t, "reason") != "adopt") continue;
    judged = true;
    EXPECT_EQ(Number(t, "judged"), 1) << t;
    EXPECT_EQ(Number(t, "cheaper"), 10) << t;
    // Slope -1 on log2 K: the effect is -log2(cand / inc).
    EXPECT_NEAR(Number(t, "effect"),
                -std::log2(Number(t, "k_cand") / Number(t, "k_inc")), 1e-9)
        << t;
  }
  EXPECT_TRUE(judged);
}

// Input set 2 (config.h f2_inputs; the input study's
// "six"). Every test that switches the set puts it back.
struct InputSet {
  explicit InputSet(int v) { SetF2Inputs(v); }
  ~InputSet() { SetF2Inputs(1); }
};

TEST(Learned2, InputSet2AppendsTheSixStateInputs) {
  {
    InputSet two(2);
    const auto& names = F2FeatureNames();
    ASSERT_EQ(names.size(), 18u);
    const std::vector<std::string> six = {"up0_x_pending", "dn0_x_pending",
                                          "top_is_l0",     "prev_deep_bpo",
                                          "fpos_per_get",  "stall"};
    EXPECT_EQ(std::vector<std::string>(names.begin() + 12, names.end()), six);
    EXPECT_EQ(names[kF2Up0Pending], "up0_x_pending");
    EXPECT_EQ(names[kF2Stall], "stall");
    EXPECT_EQ(FeatureNames(Agent::kF), names);
    // rl_agent/tests/test_learned2.py checks the same hash from Python.
    EXPECT_EQ(FeaturesHash(Agent::kF), 0x3ae249d6de64b390ull);
    F2State s;
    s.up0_x_pending = 1.5;
    s.dn0_x_pending = NAN;
    s.top_is_l0 = 1;
    s.prev_deep_bpo = 7;
    s.fpos_per_get = 1.1;
    s.stall = 0.25;
    const std::vector<double> m = {1, 2};
    const auto x = F2Features(0.5, 0.5, 0, 1e10, 999, 8, m, s);
    ASSERT_EQ(x.size(), 18u);
    EXPECT_DOUBLE_EQ(x[kF2LogK], 3.0);
    EXPECT_DOUBLE_EQ(x[kF2LogM1], 1.0);
    EXPECT_DOUBLE_EQ(x[kF2Up0Pending], 1.5);
    EXPECT_DOUBLE_EQ(x[kF2Dn0Pending], 0.0);  // NaN never reaches the model
    EXPECT_DOUBLE_EQ(x[kF2TopIsL0], 1.0);
    EXPECT_DOUBLE_EQ(x[kF2PrevDeepBpo], 7.0);
    EXPECT_DOUBLE_EQ(x[kF2FposPerGet], 1.1);
    EXPECT_DOUBLE_EQ(x[kF2Stall], 0.25);
  }
  EXPECT_EQ(F2FeatureNames().size(), 12u);
  EXPECT_EQ(FeaturesHash(Agent::kF), 0x4d7a0f34e38d4d67ull);
  F2State s;
  s.stall = 1;
  EXPECT_EQ(F2Features(0.5, 0.5, 0, 1e10, 999, 8, {1}, s).size(), 12u);
  SetF2Inputs(7);  // anything else is set 1
  EXPECT_EQ(F2Inputs(), 1);
}

TEST(Learned2, TheStateInputsFollowTheirDefinitions) {
  const double B = 8.0 * 1024 * 1024;
  // K went up: ln(1 + pending / C_1) in up0; down: in dn0; else neither.
  const F2State up = F2StateInputs(4, 5, 2 * B, B, 0, 0.5, 0, 0, 0, 0, 0, 0);
  EXPECT_DOUBLE_EQ(up.up0_x_pending, std::log1p(2.0));
  EXPECT_DOUBLE_EQ(up.dn0_x_pending, 0);
  const F2State down = F2StateInputs(4, 3, 2 * B, B, 0, 0.5, 0, 0, 0, 0, 0, 0);
  EXPECT_DOUBLE_EQ(down.up0_x_pending, 0);
  EXPECT_DOUBLE_EQ(down.dn0_x_pending, std::log1p(2.0));
  const F2State same = F2StateInputs(4, 4, 2 * B, B, 0, 0.5, 0, 0, 0, 0, 0, 0);
  EXPECT_DOUBLE_EQ(same.up0_x_pending + same.dn0_x_pending, 0);
  // No previous window, no C_1, or no estimate: nothing.
  EXPECT_DOUBLE_EQ(
      F2StateInputs(0, 4, 2 * B, B, 0, 0.5, 0, 0, 0, 0, 0, 0).up0_x_pending, 0);
  EXPECT_DOUBLE_EQ(
      F2StateInputs(4, 5, 2 * B, 0, 0, 0.5, 0, 0, 0, 0, 0, 0).up0_x_pending, 0);
  EXPECT_DOUBLE_EQ(
      F2StateInputs(4, 5, NAN, B, 0, 0.5, 0, 0, 0, 0, 0, 0).up0_x_pending, 0);
  // L0's files / K against the highest deeper score.
  EXPECT_DOUBLE_EQ(
      F2StateInputs(4, 4, 0, B, 3, 0.97, 0, 0, 0, 0, 0, 0).top_is_l0, 0);
  EXPECT_DOUBLE_EQ(
      F2StateInputs(4, 3, 0, B, 3, 0.97, 0, 0, 0, 0, 0, 0).top_is_l0, 1);
  // The previous window's activity.
  const F2State act = F2StateInputs(4, 4, 0, B, 0, 1, 1e8, 1e5, 30, 20, 1, 4);
  EXPECT_DOUBLE_EQ(act.prev_deep_bpo, std::log1p(1000.0));
  EXPECT_DOUBLE_EQ(act.fpos_per_get, 1.5);
  EXPECT_DOUBLE_EQ(act.stall, 0.25);
  const F2State none = F2StateInputs(4, 4, 0, B, 0, 1, 0, 0, 30, 0, 3, 0);
  EXPECT_DOUBLE_EQ(none.prev_deep_bpo, 0);
  EXPECT_DOUBLE_EQ(none.fpos_per_get, 0);  // no lookups
  EXPECT_DOUBLE_EQ(none.stall, 0);         // no flush or compaction ended
}

TEST(Learned2, ASet2WeightsFileLoadsOnlyUnderSet2) {
  const std::string dir = TempDir();
  {
    InputSet two(2);
    std::vector<Model> models;
    for (int e = 0; e < 2; ++e) models.push_back(Linear(0, kF2Stall, 1 + e));
    WriteWeights(dir + "/w.bin", 3, models);
    Weights w;
    std::string error;
    ASSERT_EQ(LoadWeights(dir + "/w.bin", &w, &error), LoadStatus::kOk) << error;
    ASSERT_EQ(w.FindAll(Agent::kF, 0).size(), 2u);
    F2State s;
    s.stall = 0.5;
    const auto x = F2Features(0.2, 0.7, 0.1, 2e10, 1000000, 4, {1}, s);
    EXPECT_DOUBLE_EQ(Forward(*w.FindAll(Agent::kF, 0)[1], x)[0], 1.0);
  }
  Weights w;
  std::string error;
  EXPECT_EQ(LoadWeights(dir + "/w.bin", &w, &error), LoadStatus::kInvalid);
  EXPECT_NE(error.find("other state inputs"), std::string::npos) << error;
}

TEST_F(Learned2Test, TheInputSetKeyIsOptionalAndChecked) {
  rlc::Config c;
  std::string error;
  ASSERT_TRUE(LoadConfig(Config(), &c, &error)) << error;
  EXPECT_EQ(c.f2_inputs, 1);
  ASSERT_TRUE(LoadConfig(Config({{"f2_inputs", "2"}}), &c, &error)) << error;
  EXPECT_EQ(c.f2_inputs, 2);
  EXPECT_FALSE(LoadConfig(Config({{"f2_inputs", "3"}}), &c, &error));
  EXPECT_NE(error.find("f2_inputs 1 or 2"), std::string::npos) << error;
  auto values = BaseConfig(dir_);
  values["f2_inputs"] = "2";
  EXPECT_FALSE(LoadConfig(WriteConfig(dir_, values), &c, &error));
  EXPECT_NE(error.find("is for learned2"), std::string::npos) << error;
}

TEST_F(Learned2Test, Set2WindowsCarryTheTreeStateOfTheirStart) {
  const double B = 8.0 * 1024 * 1024;  // the fake host's C_1
  FakeHost host;
  {
    auto keys = CycleTrials();
    keys["window_ops"] = "200000";
    keys["min_decision_ops"] = "200000";
    keys["f2_inputs"] = "2";
    Controller controller(&host, Config(keys), [this] { return clock_us_; });
    EXPECT_EQ(F2Inputs(), 2);
    {
      std::lock_guard<std::mutex> lock(host.mu);
      host.snapshot->pending_compaction_bytes = static_cast<uint64_t>(3 * B);
      host.snapshot->write_delayed = true;
    }
    Serve(&host);
    for (int i = 0; i < 16; ++i) {
      controller.Step();
      clock_us_ += 200000;
      {
        std::lock_guard<std::mutex> lock(host.mu);
        host.steps.gets += 60000;
        host.steps.block_probes += 120000;
      }
      Turnover(&host);  // one window: 100k operations, one L0 merge
    }
    controller.Step();
  }
  SetF2Inputs(1);
  const auto lines = Lines();
  const auto windows = OfType(lines, "window");
  ASSERT_GE(windows.size(), 10u);
  const double pending = std::log1p(3.0);
  // Each turnover ends merges from L1, L2 and L3 reading 16, 32, 64 MiB.
  const double deep = 112.0 * 1024 * 1024;
  int ups = 0, downs = 0, tops = 0;
  for (size_t i = 0; i < windows.size(); ++i) {
    const std::string& w = windows[i];
    const double k = Number(w, "k");
    const double k_prev = i == 0 ? 4 : Number(windows[i - 1], "k");
    EXPECT_NEAR(Number(w, "up0_x_pending"), k > k_prev ? pending : 0, 1e-9) << w;
    EXPECT_NEAR(Number(w, "dn0_x_pending"), k < k_prev ? pending : 0, 1e-9) << w;
    ups += k > k_prev ? 1 : 0;
    downs += k < k_prev ? 1 : 0;
    // L0 holds 3 files; L1 and L2 score 0.97.
    const bool top = 3.0 / k >= 0.97;
    EXPECT_EQ(Number(w, "top_is_l0"), top ? 1 : 0) << w;
    tops += top ? 1 : 0;
    if (i == 0) {  // no window before the first
      EXPECT_EQ(Number(w, "prev_deep_bpo"), 0) << w;
      EXPECT_EQ(Number(w, "fpos_per_get"), 0) << w;
      EXPECT_EQ(Number(w, "stall"), 0) << w;
      continue;
    }
    const double ops =
        Number(windows[i - 1], "op1") - Number(windows[i - 1], "op0");
    EXPECT_NEAR(Number(w, "prev_deep_bpo"), std::log1p(deep / ops), 1e-9) << w;
    EXPECT_NEAR(Number(w, "fpos_per_get"), 2.0, 1e-12) << w;
    EXPECT_NEAR(Number(w, "stall"), 1.0, 1e-12) << w;
  }
  EXPECT_GT(ups, 0);
  EXPECT_GT(downs, 0);
  EXPECT_GT(tops, 0);
  // The decisions read the last closed window's six values.
  for (const std::string& d : OfType(lines, "f2")) {
    EXPECT_NE(d.find("\"stall\":"), std::string::npos) << d;
  }
}

}  // namespace
}  // namespace rlc
