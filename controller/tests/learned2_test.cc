// learned2 (learned2.h; owner, 2026-11-05): F1 persistent moves, F2 settle
// and measure before deciding, F3 the ensemble's vote, F4 the clean inputs,
// and the window lines the trainer prices (F5).
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
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

}  // namespace
}  // namespace rlc
