// Turn mode in the plugin (plugin.cc TurnPoll, TurnBoundary, TurnTopUps,
// TurnWeights; config.cc's "turn_*" keys): the config's checks, a read-only
// stream that still closes turns and decays, a renew that makes no Apply,
// one Apply per changed vector, a defer's top-up and a broken defer, a
// weights file of the other arch, the drain's final line, and the scores of
// the Python trainer's weights (rl_agent/tests/test_turn_weights.py writes
// the golden files).
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

#include "gtest/gtest.h"
#include "plugin.h"
#include "test_util.h"
#include "turn.h"

namespace rlc {
namespace {

using test::BaseConfig;
using test::FakeHost;
using test::ReadLines;
using test::TempDir;
using test::WriteConfig;

constexpr int kActing = 5;

const char* const kTurnKeys[] = {
    "turn_ops",          "turn_acting",           "turn_arch",
    "turn_kappa",        "turn_margin",           "turn_sigma_max",
    "turn_glide_share",  "turn_forecast_turns",   "turn_guard_owed_bytes",
    "turn_guard_l0_flushes", "turn_safety_l0_flushes",
    "turn_safety_pending_bytes", "turn_k_defer_max", "turn_renew_band",
    "turn_max_topups",   "turn_explore_start",    "turn_explore_end",
    "turn_explore_turns", "turn_k_rungs",         "turn_m_rungs",
    "turn_decay",        "turn_prior"};

// A net of one layer with zero weights: every score is a bias, 1 for
// `action` at `level` and 0 elsewhere, so greedy picks it wherever allowed.
TurnNet BiasNet(uint64_t version, int arch, int level, Action action) {
  TurnNet net;
  net.version = version;
  net.arch = arch;
  net.acting = kActing;
  net.n_in = static_cast<uint32_t>(TurnFeatureNames(kActing).size());
  net.clip = 5;
  net.mean.assign(net.n_in, 0.0);
  net.scale.assign(net.n_in, 1.0);
  const int nets = arch == 0 ? 1 : kActing;
  for (int k = 0; k < nets; ++k) {
    TurnNet::Net n;
    for (uint32_t j = 0; j < net.n_in; ++j) n.view.push_back(static_cast<int32_t>(j));
    TurnNet::Layer l;
    l.out = arch == 0 ? 1 + 4 * kActing : 4;
    l.in = net.n_in;
    l.w.assign(static_cast<size_t>(l.out) * l.in, 0.0);
    l.b.assign(l.out, 0.0);
    if (level >= 0) {
      if (arch == 0) {
        l.b[1 + 4 * level + static_cast<int>(action)] = 1;
      } else if (k == level) {
        l.b[static_cast<int>(action)] = 1;
      }
    }
    n.layers = {l};
    net.nets.push_back(n);
  }
  return net;
}

// Written beside and renamed into place, as the trainer writes it.
void WriteNet(const std::string& path, const TurnNet& net) {
  const std::string tmp = path + ".tmp";
  {
    std::ofstream out(tmp, std::ios::binary);
    out << SerializeTurnNet(net);
  }
  std::filesystem::rename(tmp, path);
}

// The value of `key` in a log line: a number, a flat array of numbers or of
// strings (enough for the plugin's own lines).
double Number(const std::string& line, const std::string& key) {
  const size_t at = line.find("\"" + key + "\":");
  if (at == std::string::npos) return NAN;
  return std::strtod(line.c_str() + at + key.size() + 3, nullptr);
}
bool Has(const std::string& line, const std::string& key) {
  return line.find("\"" + key + "\":") != std::string::npos;
}
std::string Body(const std::string& line, const std::string& key) {
  const size_t at = line.find("\"" + key + "\":[");
  if (at == std::string::npos) return "";
  const size_t start = at + key.size() + 4;
  return line.substr(start, line.find(']', start) - start);
}
std::vector<double> Array(const std::string& line, const std::string& key) {
  std::vector<double> out;
  std::stringstream in(Body(line, key));
  for (std::string item; std::getline(in, item, ',');) {
    out.push_back(std::strtod(item.c_str(), nullptr));
  }
  return out;
}
std::vector<std::string> Names(const std::string& line, const std::string& key) {
  std::vector<std::string> out;
  std::stringstream in(Body(line, key));
  for (std::string item; std::getline(in, item, ',');) {
    out.push_back(item.substr(1, item.size() - 2));
  }
  return out;
}
std::string Text(const std::string& line, const std::string& key) {
  const size_t at = line.find("\"" + key + "\":\"");
  if (at == std::string::npos) return "";
  const size_t start = at + key.size() + 4;
  return line.substr(start, line.find('"', start) - start);
}

class TurnTest : public testing::Test {
 protected:
  void SetUp() override { dir_ = TempDir(); }

  std::map<std::string, std::string> Values(
      std::map<std::string, std::string> changes = {}) {
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
    values["mode"] = "\"turn\"";
    values["seed"] = "1";
    values["weights_path"] = "\"" + weights() + "\"";
    values["push_interval_ms"] = "1";
    values["weights_required"] = "0";
    values["turn_ops"] = "10000";
    values["turn_acting"] = "5";
    values["turn_arch"] = "\"factored\"";
    values["turn_kappa"] = "50";
    values["turn_decay"] = "1";
    values["turn_prior"] = "0";
    values["turn_margin"] = "0.1";
    values["turn_sigma_max"] = "1.3333333333333333";
    values["turn_glide_share"] = "0.5";
    values["turn_forecast_turns"] = "8";
    values["turn_guard_owed_bytes"] = "17179869184";
    values["turn_guard_l0_flushes"] = "4";
    values["turn_safety_l0_flushes"] = "2";
    values["turn_safety_pending_bytes"] = "34359738368";
    values["turn_k_defer_max"] = "18";
    values["turn_renew_band"] = "0.5";
    values["turn_max_topups"] = "2";
    // No exploration unless a test asks: choices come from the test's net.
    values["turn_explore_start"] = "0";
    values["turn_explore_end"] = "0";
    values["turn_explore_turns"] = "200";
    values["turn_k_rungs"] = "\"2:3:4:6:8\"";
    values["turn_m_rungs"] =
        "\"0.5:0.7071067811865476:1:1.4142135623730951:2\"";
    for (const auto& change : changes) values[change.first] = change.second;
    return values;
  }
  std::string Config(std::map<std::string, std::string> changes = {}) {
    return WriteConfig(dir_, Values(std::move(changes)));
  }
  std::string weights() const { return dir_ + "/weights.bin"; }

  // L0 with 3 files, L1 and L2 near due, L3 half full, L4 the last level.
  static void Tree(FakeHost* host) {
    host->SetL0(3);
    host->SetLevel(1, 0.97);
    host->SetLevel(2, 0.97);
    host->SetLevel(3, 0.5);
    host->SetLevel(4, 0.3);
  }
  // 2,500 operations; with `writes`, a flush of `flush_bytes` and an L0->L1
  // merge that lands 1 MiB net at L1.
  void Ops(FakeHost* host, bool writes, uint64_t flush_bytes = 1024 * 1024) {
    if (!writes) {
      host->AddOps(2500, 0);
      return;
    }
    host->AddOps(1000, 1500);
    host->Job(FakeHost::Flush(flush_bytes));
    host->Job(FakeHost::Compaction(false, next_job_, 0, 1, 0, 0));
    host->Job(FakeHost::Compaction(true, next_job_++, 0, 2 << 20, 1 << 20,
                                   2 << 20));
  }
  void Run(Controller* controller, FakeHost* host, int steps, bool writes) {
    for (int i = 0; i < steps; ++i) {
      Ops(host, writes);
      controller->Step();
      clock_us_ += 200000;
    }
  }
  std::vector<std::string> Lines(const std::string& type) const {
    std::vector<std::string> out;
    for (const std::string& l : ReadLines(dir_ + "/decisions.jsonl")) {
      if (l.find("{\"type\":\"" + type + "\"") == 0) out.push_back(l);
    }
    return out;
  }

  std::string dir_;
  uint64_t clock_us_ = 1000000;
  int next_job_ = 10;
};

TEST_F(TurnTest, TheConfigNeedsEveryTurnKeyAndOtherModesRefuseThem) {
  rlc::Config c;
  std::string error;
  ASSERT_TRUE(LoadConfig(Config(), &c, &error)) << error;
  EXPECT_EQ(c.mode, Mode::kTurn);
  EXPECT_DOUBLE_EQ(c.turn.turn_ops, 10000);
  EXPECT_EQ(c.turn.acting, 5);
  EXPECT_EQ(c.turn.arch, 0);
  EXPECT_EQ(c.turn.max_topups, 2);
  EXPECT_TRUE(c.turn.decay);
  EXPECT_FALSE(c.turn.prior);
  EXPECT_DOUBLE_EQ(c.turn.guard_owed_bytes, 16.0 * (1ull << 30));
  EXPECT_EQ(c.turn.k_rungs, (std::vector<double>{2, 3, 4, 6, 8}));
  ASSERT_EQ(c.turn.m_rungs.size(), 5u);
  EXPECT_DOUBLE_EQ(c.turn.m_rungs[1], std::sqrt(0.5));
  EXPECT_TRUE(CheckConfigAgainstHost(c, 4, 20, &error)) << error;
  ASSERT_TRUE(LoadConfig(Config({{"turn_arch", "\"vdn\""}}), &c, &error)) << error;
  EXPECT_EQ(c.turn.arch, 1);
  ASSERT_TRUE(LoadConfig(Config({{"turn_decay", "0"}}), &c, &error)) << error;
  EXPECT_FALSE(c.turn.decay);
  ASSERT_TRUE(LoadConfig(Config({{"turn_prior", "1"}}), &c, &error)) << error;
  EXPECT_TRUE(c.turn.prior);

  for (const char* key : kTurnKeys) {
    auto values = Values();
    values.erase(key);
    EXPECT_FALSE(LoadConfig(WriteConfig(dir_, values), &c, &error)) << key;
    EXPECT_NE(error.find(key), std::string::npos) << key << ": " << error;
  }
  const std::map<std::string, std::string> bad[] = {
      {{"turn_arch", "\"dqn\""}},
      {{"turn_ops", "999"}},
      {{"turn_ops", "10000.5"}},
      {{"turn_acting", "0"}},
      {{"turn_margin", "0"}},
      {{"turn_sigma_max", "1.05"}},
      {{"turn_glide_share", "0"}},
      {{"turn_forecast_turns", "0"}},
      {{"turn_renew_band", "1.5"}},
      {{"turn_decay", "2"}},
      {{"turn_decay", "-1"}},
      {{"turn_decay", "0.5"}},
      {{"turn_prior", "2"}},
      {{"turn_prior", "-1"}},
      {{"turn_explore_start", "1"}},
      {{"turn_k_rungs", "\"2:4:3\""}},
      {{"turn_k_rungs", "\"2:3.5:4\""}},
      {{"turn_k_rungs", "4"}},
      {{"turn_m_rungs", "\"0.5:0.75:2\""}},     // no native rung
      {{"turn_m_rungs", "\"0.5:1:2.5\""}},      // past the fork's bound
      {{"explore", "0.1"}},                     // turn mode has its own rate
      {{"m_max", "1.9"}},                       // the fork's bounds only
      {{"weights_path", "\"\""}},
  };
  for (const auto& change : bad) {
    EXPECT_FALSE(LoadConfig(Config(change), &c, &error))
        << change.begin()->first << " = " << change.begin()->second;
  }
  auto values = Values();
  values.erase("seed");
  EXPECT_FALSE(LoadConfig(WriteConfig(dir_, values), &c, &error));

  // Another mode refuses every turn key.
  for (const char* key : kTurnKeys) {
    auto other = BaseConfig(dir_);
    other[key] = Values()[key];
    EXPECT_FALSE(LoadConfig(WriteConfig(dir_, other), &c, &error)) << key;
    EXPECT_NE(error.find("is for turn"), std::string::npos) << error;
  }

  // Against the host: the K ladder holds the configured trigger, and no
  // trigger the mode sends reaches the slowdown trigger.
  ASSERT_TRUE(LoadConfig(Config({{"turn_k_rungs", "\"2:3:6:8\""}}), &c, &error));
  EXPECT_FALSE(CheckConfigAgainstHost(c, 4, 20, &error));
  ASSERT_TRUE(LoadConfig(Config({{"turn_k_defer_max", "20"}}), &c, &error));
  EXPECT_FALSE(CheckConfigAgainstHost(c, 4, 20, &error));
  ASSERT_TRUE(LoadConfig(Config({{"turn_k_rungs", "\"2:3:4:6:8:24\""}}), &c,
                         &error));
  EXPECT_FALSE(CheckConfigAgainstHost(c, 4, 20, &error));
}

TEST_F(TurnTest, AReadOnlyStreamStillClosesTurnsAndDecays) {
  // v1 compacts L1 at the first boundary; v2 holds everywhere after it.
  WriteNet(weights(), BiasNet(1, 0, 1, Action::kCompact));
  FakeHost host;
  Tree(&host);
  {
    Controller controller(&host, Config({{"turn_kappa", "2"}}),
                          [this] { return clock_us_; });
    ASSERT_FALSE(controller.fallback());
    Run(&controller, &host, 4, /*writes=*/false);
    WriteNet(weights(), BiasNet(2, 0, -1, Action::kHold));
    Run(&controller, &host, 36, /*writes=*/false);
    EXPECT_FALSE(controller.fallback());
  }
  const auto turns = Lines("turn");
  ASSERT_EQ(turns.size(), 10u);  // 100,000 operations, every 10,000
  for (size_t t = 0; t < turns.size(); ++t) {
    const std::string& line = turns[t];
    EXPECT_EQ(Number(line, "t"), static_cast<double>(t));
    EXPECT_EQ(Number(line, "op1"), 10000.0 * (t + 1)) << line;
    EXPECT_EQ(Number(line, "op1") - Number(line, "op0"), 10000.0);
    EXPECT_EQ(Number(line, "get_share"), 1) << line;
    EXPECT_EQ(Number(line, "flushes"), 0);
    EXPECT_EQ(Number(line, "final"), 0);
    const auto action = Names(line, "action");
    ASSERT_EQ(action.size(), 5u);
    EXPECT_EQ(action[1], t == 0 ? "compact" : "hold") << line;
    // L1's anchor went one rung down at turn 0 and decays from there every
    // turn, a <- a exp(-1/kappa), with no write and no flush.
    const double expected = t == 0 ? 0.0 : -0.5 * std::exp(-static_cast<double>(t) / 2);
    EXPECT_NEAR(Array(line, "anchors")[1], expected, 1e-12) << line;
  }
  EXPECT_NEAR(Array(turns[0], "anchors_after")[1], -0.5, 1e-12);
  EXPECT_EQ(Number(turns[1], "weights"), 2);
  // Compact-now sent L1 below its fill, then the glide and the decay took it
  // back to native.
  EXPECT_LT(Array(turns[0], "sent_m")[1], 0.97);
  EXPECT_EQ(Array(turns.back(), "applied_m")[1], 1.0);
  EXPECT_GE(host.Applies(), 2u);
}

TEST_F(TurnTest, WithTheDecayOffHoldKeepsTheSetting) {
  // As above, with turn_decay 0 (DECAY=0): L1 compacts at turn 0, then holds.
  WriteNet(weights(), BiasNet(1, 0, 1, Action::kCompact));
  FakeHost host;
  Tree(&host);
  {
    Controller controller(&host,
                          Config({{"turn_kappa", "2"}, {"turn_decay", "0"}}),
                          [this] { return clock_us_; });
    ASSERT_FALSE(controller.fallback());
    Run(&controller, &host, 4, /*writes=*/false);
    WriteNet(weights(), BiasNet(2, 0, -1, Action::kHold));
    Run(&controller, &host, 36, /*writes=*/false);
    EXPECT_FALSE(controller.fallback());
  }
  const auto starts = Lines("turn_start");
  ASSERT_EQ(starts.size(), 1u);
  EXPECT_EQ(Number(starts[0], "decay"), 0);
  const auto turns = Lines("turn");
  ASSERT_EQ(turns.size(), 10u);
  // One rung down at turn 0, and there it stays, bit for bit: no decay,
  // never a wait.
  const double pressed = Array(turns[0], "anchors_after")[1];
  EXPECT_NEAR(pressed, -0.5, 1e-12);
  EXPECT_EQ(Array(turns[0], "anchors")[1], 0.0);
  for (size_t t = 0; t < turns.size(); ++t) {
    const std::string& line = turns[t];
    EXPECT_EQ(Names(line, "action")[1], t == 0 ? "compact" : "hold") << line;
    if (t > 0) {
      EXPECT_EQ(Array(line, "anchors")[1], pressed) << line;
    }
    EXPECT_EQ(Array(line, "anchors_after")[1], pressed) << line;
    for (double w : Array(line, "decay_waited")) EXPECT_EQ(w, 0) << line;
  }
  // The glide takes L1 down to the lower rung, and holding keeps it there.
  EXPECT_NEAR(Array(turns.back(), "applied_m")[1], std::sqrt(0.5), 1e-12);
  EXPECT_NEAR(Array(turns.back(), "sent_m")[1], std::sqrt(0.5), 1e-12);
}

TEST_F(TurnTest, ARenewMakesNoApplyAndEachChangeIsOneApply) {
  WriteNet(weights(), BiasNet(1, 0, 1, Action::kExpand));
  FakeHost host;
  Tree(&host);
  {
    Controller controller(&host, Config(), [this] { return clock_us_; });
    Run(&controller, &host, 24, /*writes=*/true);
    EXPECT_FALSE(controller.fallback());
  }
  const auto turns = Lines("turn");
  ASSERT_EQ(turns.size(), 6u);
  // Expand: 1 -> sqrt 2 -> 2; at the top every press renews the anchor.
  EXPECT_EQ(Array(turns[0], "sent_m")[1], std::sqrt(2.0));
  EXPECT_EQ(Array(turns[1], "sent_m")[1], 2.0);
  double sets = 0, mids = 0;
  std::vector<double> applied = {1, 1, 1, 1, 1};
  for (size_t t = 0; t < turns.size(); ++t) {
    const std::string& line = turns[t];
    EXPECT_EQ(Names(line, "action")[1], "expand") << line;
    const double set = Number(line, "set");
    sets += set;
    mids += Number(line, "mid_applies");
    // One Apply exactly when the vector changed, carrying it.
    const std::vector<double> sent = Array(line, "sent_m");
    EXPECT_EQ(set, sent != applied ? 1 : 0) << line;
    EXPECT_EQ(Array(line, "applied_m"), sent) << line;
    applied = sent;
    if (t >= 2) {
      EXPECT_EQ(Array(line, "renewed")[1], 1) << line;
      EXPECT_EQ(Array(line, "moved")[1], 0) << line;
      EXPECT_EQ(set, 0) << line;
      EXPECT_NEAR(Array(line, "anchors_after")[1], 1.0, 1e-12) << line;
    }
  }
  EXPECT_EQ(sets, 2);
  EXPECT_EQ(mids, 0);
  EXPECT_EQ(host.Applies(), 2u);
}

TEST_F(TurnTest, ADeferIsToppedUpAndABrokenDeferIsLogged) {
  WriteNet(weights(), BiasNet(1, 0, 0, Action::kDefer));
  FakeHost host;
  Tree(&host);
  {
    Controller controller(&host, Config(), [this] { return clock_us_; });
    // Flushes of 512 KiB: F = 0.5 MiB, so the live cap is ceil(8 / 0.5) = 16.
    for (int i = 0; i < 4; ++i) {
      Ops(&host, true, 512 * 1024);
      controller.Step();
      clock_us_ += 200000;
    }
    // The boundary deferred L0: K = n0 + ceil(f_hat) + 1 = 3 + 4 + 1.
    ASSERT_EQ(Lines("turn").size(), 1u);
    EXPECT_EQ(Number(Lines("turn")[0], "sent_k"), 8);
    // Within one file of due: raised by the flushes still expected (4 - 1).
    host.SetL0(7);
    Ops(&host, true, 512 * 1024);
    controller.Step();
    clock_us_ += 200000;
    // Then a raise past the live cap: the defer is broken.
    host.SetL0(15);
    Ops(&host, true, 512 * 1024);
    controller.Step();
    clock_us_ += 200000;
    EXPECT_FALSE(controller.fallback());
  }
  const auto topups = Lines("turn_topup");
  ASSERT_EQ(topups.size(), 1u);
  EXPECT_EQ(Number(topups[0], "level"), 0);
  EXPECT_EQ(Number(topups[0], "old"), 8);
  EXPECT_EQ(Number(topups[0], "new"), 11);
  const auto broken = Lines("defer_broken");
  ASSERT_EQ(broken.size(), 1u);
  EXPECT_EQ(Number(broken[0], "level"), 0);
  EXPECT_EQ(Text(broken[0], "reason"), "bounds");
  // The top-up reached RocksDB at its own poll.
  ASSERT_GE(host.applied.size(), 2u);
  EXPECT_EQ(host.applied[0].second, 8);
  EXPECT_EQ(host.applied[1].second, 11);
}

TEST_F(TurnTest, AWeightsFileOfTheOtherArchFallsBack) {
  WriteNet(weights(), BiasNet(1, /*arch=*/1, 1, Action::kCompact));
  FakeHost host;
  Tree(&host);
  Controller controller(&host, Config(), [this] { return clock_us_; });
  Run(&controller, &host, 4, /*writes=*/true);
  EXPECT_TRUE(controller.fallback());
  const auto fallback = Lines("fallback");
  ASSERT_EQ(fallback.size(), 1u);
  EXPECT_NE(Text(fallback[0], "reason").find("arch"), std::string::npos)
      << fallback[0];
  EXPECT_TRUE(Lines("turn").empty());
  EXPECT_EQ(host.Applies(), 0u);
}

TEST_F(TurnTest, TheVdnArmLoadsItsOwnNets) {
  WriteNet(weights(), BiasNet(1, /*arch=*/1, 1, Action::kExpand));
  FakeHost host;
  Tree(&host);
  {
    Controller controller(&host, Config({{"turn_arch", "\"vdn\""}}),
                          [this] { return clock_us_; });
    Run(&controller, &host, 4, /*writes=*/true);
    EXPECT_FALSE(controller.fallback());
  }
  const auto loaded = Lines("turn_weights");
  ASSERT_EQ(loaded.size(), 1u);
  EXPECT_EQ(Text(loaded[0], "arch"), "vdn");
  EXPECT_EQ(Number(loaded[0], "nets"), 5);
  EXPECT_EQ(Names(Lines("turn")[0], "action")[1], "expand");
}

TEST_F(TurnTest, ColdItHoldsAndTheDrainWritesTheFinalState) {
  FakeHost host;
  Tree(&host);
  {
    Controller controller(&host, Config(), [this] { return clock_us_; });
    Run(&controller, &host, 9, /*writes=*/true);
    host.SetDraining();
    Run(&controller, &host, 3, /*writes=*/true);
  }
  ASSERT_EQ(Lines("turn_weights_missing").size(), 1u);
  const auto turns = Lines("turn");
  ASSERT_EQ(turns.size(), 3u);
  for (size_t t = 0; t < 2; ++t) {
    for (const std::string& a : Names(turns[t], "action")) EXPECT_EQ(a, "hold");
    EXPECT_EQ(Number(turns[t], "set"), 0);
    EXPECT_EQ(Number(turns[t], "weights"), 0);
  }
  // The drain's line: the turn that ran into the drain, its state, no choice.
  const std::string& last = turns.back();
  EXPECT_EQ(Number(last, "final"), 1);
  EXPECT_EQ(Number(last, "op0"), 20000);
  EXPECT_EQ(Number(last, "op1"), 25000);
  EXPECT_TRUE(Has(last, "x"));
  EXPECT_TRUE(Has(last, "owed"));
  EXPECT_FALSE(Has(last, "action"));
  EXPECT_EQ(host.Applies(), 0u);
}

// turn_prior 1, cold (no net: every score 0): L0's greedy choice is the
// October L0 formula's cheapest move over the forecast window, logged with
// its prior and spread 1; the net's scores stay 0. Per step 1,000 probes at
// every level, 10 false positives (1%). Writes (1,500 of 2,500 per step, a
// 1 MiB flush): K* ~ 15, so L0 expands from 4 to 6 and on. Reads (2,400 of
// 2,500, 100 writes, a 1 MiB flush): g(K) = 327.68 / K + 52.8 K per
// operation (u = 40.96 B, m_1 C_1 = 8 MiB, a Get's L0 file 100 + 0.01 x 1000),
// K* = 2.49: from 4 compact (to 3), then at 3 both moves cost more, so hold.
// Off: no prior, L0 holds.
TEST_F(TurnTest, TheL0PriorStartsL0FromTheFormula) {
  const auto probes = [](FakeHost* host) {
    std::lock_guard<std::mutex> lock(host->mu);
    for (RLLevelReadCounts& r : host->reads) {
      r.probes += 1000;
      r.filter_passes += 10;
    }
  };
  const auto run = [&](const std::string& config, bool writes) {
    FakeHost host;
    Tree(&host);
    Controller controller(&host, config, [this] { return clock_us_; });
    for (int i = 0; i < 9; ++i) {
      if (writes) {
        Ops(&host, true);
      } else {
        host.AddOps(2400, 100);
        host.Job(FakeHost::Flush(1024 * 1024));
      }
      probes(&host);
      controller.Step();
      clock_us_ += 200000;
    }
  };
  run(Config({{"turn_prior", "1"}}), true);
  auto turns = Lines("turn");
  ASSERT_GE(turns.size(), 2u);
  for (const std::string& line : turns) {
    const std::vector<double> prior = Array(line, "prior");
    ASSERT_EQ(prior.size(), 4u) << line;
    EXPECT_EQ(prior[0], 0);
    EXPECT_EQ(prior[2], 0);
    EXPECT_LT(prior[3], 0) << line;   // expand cheaper
    EXPECT_GT(prior[1], 0) << line;   // compact dearer
    EXPECT_EQ(Number(line, "prior_spread"), 1);
    EXPECT_EQ(Names(line, "greedy")[0], "expand") << line;
    for (double score : Array(line, "scores")) EXPECT_EQ(score, 0);
  }

  std::remove((dir_ + "/decisions.jsonl").c_str());
  run(Config({{"turn_prior", "1"}}), false);
  turns = Lines("turn");
  ASSERT_GE(turns.size(), 2u);
  const auto g = [](double K) {
    return (8.0 * 1024 * 1024 + 50) * 40.96 / (K * 1024 * 1024) + 52.8 * K;
  };
  std::vector<double> prior = Array(turns[0], "prior");
  ASSERT_EQ(prior.size(), 4u) << turns[0];
  EXPECT_EQ(Number(turns[0], "rung_k"), 4);
  EXPECT_NEAR(prior[1], 10000 * (g(3) - g(4)), 1e-6 * 10000 * g(4));
  EXPECT_NEAR(prior[3], 10000 * (g(6) - g(4)), 1e-6 * 10000 * g(4));
  EXPECT_LT(prior[1], 0);   // compact cheaper
  EXPECT_GT(prior[3], 0);   // expand dearer
  EXPECT_EQ(Names(turns[0], "greedy")[0], "compact");
  for (size_t t = 1; t < turns.size(); ++t) {
    prior = Array(turns[t], "prior");
    EXPECT_EQ(Number(turns[t], "rung_k"), 3) << turns[t];
    EXPECT_NEAR(prior[1], 10000 * (g(2) - g(3)), 1e-6 * 10000 * g(3));
    EXPECT_GT(prior[1], 0) << turns[t];   // at K* ~ 2.5 both moves cost more
    EXPECT_GT(prior[3], 0) << turns[t];
    EXPECT_EQ(Names(turns[t], "greedy")[0], "hold") << turns[t];
  }

  std::remove((dir_ + "/decisions.jsonl").c_str());
  run(Config(), true);
  turns = Lines("turn");
  ASSERT_GE(turns.size(), 2u);
  for (const std::string& line : turns) {
    EXPECT_NE(line.find("\"prior\":null"), std::string::npos) << line;
    EXPECT_EQ(Names(line, "greedy")[0], "hold") << line;
  }
}

// The turn arm's config as plugin_config.py composes it from the shipped
// settings (scripts/dbbench_pipeline/tests/test_plugin_config.py writes it):
// the composer and the plugin stay in lockstep.
TEST(TurnConfigFixture, TheComposedTurnConfigIsAccepted) {
  Config c;
  std::string error;
  ASSERT_TRUE(LoadConfig(std::string(RL_CONTROLLER_FIXTURES) + "/turn_config.json",
                         &c, &error))
      << error;
  EXPECT_EQ(c.mode, Mode::kTurn);
  EXPECT_EQ(c.turn.arch, 0);
  EXPECT_DOUBLE_EQ(c.turn.turn_ops, 64000);
  EXPECT_EQ(c.turn.acting, 5);
  EXPECT_EQ(c.turn.k_rungs, (std::vector<double>{2, 3, 4, 6, 8}));
  // The pipeline's default L0 triggers: compaction 4, slowdown 20.
  EXPECT_TRUE(CheckConfigAgainstHost(c, 4, 20, &error)) << error;
}

// The golden files of rl_agent/tests/test_turn_weights.py: weights of both
// arms and, per case, the inputs and the scores its forward pass computes.
TEST(TurnGolden, ScoresMatchThePythonForwardPass) {
  const std::string dir = RL_CONTROLLER_FIXTURES;
  std::ifstream cases(dir + "/turn_golden.txt");
  ASSERT_TRUE(cases.good());
  int checked = 0;
  for (std::string line; std::getline(cases, line);) {
    std::istringstream in(line);
    std::string arch_name, token;
    size_t n = 0;
    in >> arch_name >> n;
    const int arch = arch_name == "vdn" ? 1 : 0;
    std::vector<double> x(n);
    for (double& v : x) {
      in >> token;
      v = token == "nan" ? NAN : std::stod(token);
    }
    in >> token;
    ASSERT_EQ(token, "|");
    TurnNet net;
    std::string error;
    ASSERT_EQ(LoadTurnNet(dir + "/turn_golden_" + arch_name + ".bin", arch,
                          kActing, &net, &error),
              TurnLoad::kOk)
        << error;
    EXPECT_DOUBLE_EQ(net.spread, 2.5);  // the reward spread rides along
    const auto scores = TurnScores(&net, x, kActing);
    for (int level = 0; level < kActing; ++level) {
      for (int a = 0; a < kNumActions; ++a) {
        double want = 0;
        ASSERT_TRUE(static_cast<bool>(in >> want));
        EXPECT_NEAR(scores[level][a], want, 1e-9 * (1 + std::fabs(want)))
            << arch_name << " level " << level << " action " << a;
      }
    }
    ++checked;
  }
  EXPECT_EQ(checked, 6);
}

}  // namespace
}  // namespace rlc
