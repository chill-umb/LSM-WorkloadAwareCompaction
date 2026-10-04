// The config: a strict flat-JSON parser (every key read as a whole key, never
// by its first textual match; CLAUDE.md "Repo gotchas"), required bounds
// without defaults, and the refused modes.
#include "config.h"

#include <set>

#include "gtest/gtest.h"
#include "test_util.h"

namespace rlc {
namespace {

using test::BaseConfig;
using test::ToJson;

std::string Error(const std::map<std::string, std::string>& values) {
  Config c;
  std::string error;
  EXPECT_FALSE(ParseConfig(ToJson(values), &c, &error));
  return error;
}

bool Contains(const std::string& text, const std::string& part) {
  return text.find(part) != std::string::npos;
}

TEST(Config, AValidConfigParses) {
  auto values = BaseConfig("/tmp/x");
  values["mode"] = "\"rules\"";
  values["rules"] = "\"yield_slot, garbage_hold\"";
  values["rule_garbage_drop"] = "0.2";
  Config c;
  std::string error;
  ASSERT_TRUE(ParseConfig(ToJson(values), &c, &error)) << error;
  EXPECT_EQ(c.bounds.m_min, 0.5);
  EXPECT_EQ(c.bounds.m_max, 2.0);
  EXPECT_EQ(c.bounds.k0_min, 2);
  EXPECT_EQ(c.bounds.k0_cap, 8);
  EXPECT_EQ(c.bounds.epsilon, 0.05);
  EXPECT_EQ(c.bounds.phi_min, 0.6);
  EXPECT_EQ(c.bounds.alpha, 1.25);
  EXPECT_EQ(c.bounds.kappa_d, 1);
  EXPECT_EQ(c.bounds.kappa_a, 4);
  EXPECT_EQ(c.mode, Mode::kRules);
  EXPECT_EQ(c.rules, std::vector<std::string>({"yield_slot", "garbage_hold"}));
  EXPECT_EQ(c.decision_log, "/tmp/x/decisions.jsonl");
}

// The preflight's smoke config as scripts/dbbench_pipeline/plugin_config.py
// composes it (its test_plugin_config.py keeps the fixture equal to that
// output): the pipeline's composer and this parser agree on every key.
TEST(Config, ThePipelinesSmokeConfigParses) {
  const std::string path =
      std::string(RL_CONTROLLER_FIXTURES) + "/smoke_config.json";
  Config c;
  std::string error;
  ASSERT_TRUE(LoadConfig(path, &c, &error)) << error;
  EXPECT_EQ(c.mode, Mode::kRules);
  EXPECT_EQ(c.rules.size(), 5u);
  // The pipeline's default L0 triggers: compaction 4, slowdown 20.
  EXPECT_TRUE(CheckConfigAgainstHost(c, 4, 20, &error)) << error;
}

TEST(Config, EveryBoundIsRequired) {
  for (const char* key :
       {"m_min", "m_max", "k0_min", "k0_cap", "epsilon", "phi_min", "alpha",
        "kappa_d", "kappa_a", "k", "b_max", "c_s", "q_bar", "mode"}) {
    auto values = BaseConfig("/tmp/x");
    values.erase(key);
    EXPECT_TRUE(Contains(Error(values), std::string("missing \"") + key))
        << key;
  }
}

TEST(Config, InvalidValuesAreRejectedNotClamped) {
  const std::vector<std::pair<std::string, std::string>> bad = {
      {"m_min", "0"},      {"m_min", "1.1"},  {"m_max", "0.9"},
      {"epsilon", "0"},    {"alpha", "1"},    {"kappa_a", "0"},
      {"k0_min", "1"},     {"k0_min", "2.5"}, {"k0_cap", "1"},
      {"k", "0"},          {"c_s", "0"},      {"beta_r", "-1"},
      {"phi_min", "0.52"},  // below m_min * (1 + epsilon) = 0.525
      {"m_max", "\"2\""}};
  for (const auto& [key, value] : bad) {
    auto values = BaseConfig("/tmp/x");
    values[key] = value;
    EXPECT_FALSE(Error(values).empty()) << key << " = " << value;
  }
}

TEST(Config, TheParserIsStrict) {
  Config c;
  std::string error;
  const std::string base = ToJson(BaseConfig("/tmp/x"));
  const std::string body = base.substr(1, base.size() - 2);
  for (const std::string& text : std::vector<std::string>{
           "{" + body + ", \"m_min\": 0.5}",   // duplicate
           "{" + body + ", \"surprise\": 1}",  // unknown key
           "{" + body + ", \"nested\": {\"m_min\": 0.5}}",
           "{" + body + ", \"flag\": true}", "{" + body + "} trailing",
           "{" + body + ",}", "[1]", ""}) {
    EXPECT_FALSE(ParseConfig(text, &c, &error)) << text.substr(0, 40);
  }
  for (const char* number : {"01", "1.", ".5", "+1", "NaN", "1e999", "0x10"}) {
    auto values = BaseConfig("/tmp/x");
    values["alpha"] = number;
    EXPECT_FALSE(Error(values).empty()) << number;
  }
  // A key is matched whole: a longer key that contains a known one is
  // unknown, not read as the known one.
  auto values = BaseConfig("/tmp/x");
  values["m_min_old"] = "0.9";
  EXPECT_TRUE(Contains(Error(values), "unknown key \"m_min_old\""));
}

TEST(Config, StringsAndNumbers) {
  std::map<std::string, JsonValue> out;
  std::string error;
  ASSERT_TRUE(ParseFlatJson(
      "{\"a\": \"x\\u0041\\\"\\n\", \"b\": -1.5e2, \"c\": 0}", &out, &error))
      << error;
  EXPECT_EQ(out["a"].text, "xA\"\n");
  EXPECT_EQ(out["b"].number, -150);
  EXPECT_FALSE(out["b"].is_string);
  EXPECT_FALSE(ParseFlatJson("{\"a\": \"\\u00e9\"}", &out, &error));
}

// \u takes exactly four hex digits and a printable ASCII code: strtol's
// leniency (spaces, signs) and NUL or control characters are refused.
TEST(Config, UnicodeEscapesAreExact) {
  std::map<std::string, JsonValue> out;
  std::string error;
  for (const char* escape : {"\\u 07f", "\\u+07f", "\\u-001", "\\u0000",
                             "\\u001f", "\\u007f", "\\u12", "\\u007g"}) {
    out.clear();
    EXPECT_FALSE(
        ParseFlatJson(std::string("{\"a\": \"") + escape + "\"}", &out, &error))
        << escape;
  }
  out.clear();
  ASSERT_TRUE(ParseFlatJson("{\"a\": \"\\u0041\\u007e\\u007E\"}", &out, &error))
      << error;
  EXPECT_EQ(out["a"].text, "A~~");
}

TEST(Config, StringsMustBeValidUtf8) {
  std::map<std::string, JsonValue> out;
  std::string error;
  for (const char* bad : {"\xff", "\xc3\x28", "\xc0\xaf", "\xed\xa0\x80",
                          "\xf4\x90\x80\x80", "\xe2\x82", "\x7f"}) {
    out.clear();
    EXPECT_FALSE(
        ParseFlatJson(std::string("{\"a\": \"") + bad + "\"}", &out, &error));
  }
  out.clear();
  ASSERT_TRUE(ParseFlatJson(
      "{\"a\": \"caf\xc3\xa9 \xe2\x82\xac \xf0\x9f\x98\x80\"}", &out, &error))
      << error;
  EXPECT_EQ(out["a"].text, "caf\xc3\xa9 \xe2\x82\xac \xf0\x9f\x98\x80");
}

// D-21: the controller prices reopens at stage 18's measured c_open. A
// config without it is refused, never given a default.
TEST(Config, TheReopenPriceIsRequiredAndPositive) {
  auto values = BaseConfig("/tmp/x");
  values.erase("c_open");
  EXPECT_TRUE(Contains(Error(values), "missing \"c_open\""));
  for (const char* bad : {"0", "-1e-12", "\"1e-11\""}) {
    values["c_open"] = bad;
    EXPECT_FALSE(Error(values).empty()) << bad;
  }
  values["c_open"] = "1.25e-11";
  Config c;
  std::string error;
  ASSERT_TRUE(ParseConfig(ToJson(values), &c, &error)) << error;
  EXPECT_EQ(c.c_open, 1.25e-11);
}

TEST(Config, TheSetOptionsCapIsRequiredAndPositive) {
  auto values = BaseConfig("/tmp/x");
  values.erase("setoptions_min_interval_ms");
  EXPECT_TRUE(
      Contains(Error(values), "missing \"setoptions_min_interval_ms\""));
  values["setoptions_min_interval_ms"] = "0";
  EXPECT_TRUE(Contains(Error(values), "setoptions_min_interval_ms > 0"));
  values["setoptions_min_interval_ms"] = "10";  // D-18's draft value
  Config c;
  std::string error;
  ASSERT_TRUE(ParseConfig(ToJson(values), &c, &error)) << error;
  EXPECT_EQ(c.setoptions_min_interval_ms, 10);
}

// The fork refuses multipliers outside [0.5, 2.0]; a config wider than that
// is refused at create instead of failing every Apply mid-run.
TEST(Config, BoundsMustLieWithinTheForks) {
  auto values = BaseConfig("/tmp/x");
  values["m_min"] = "0.4";
  values["phi_min"] = "0.6";
  EXPECT_TRUE(Contains(Error(values), "[0.5, 2.0]"));
  values = BaseConfig("/tmp/x");
  values["m_max"] = "2.5";
  EXPECT_TRUE(Contains(Error(values), "[0.5, 2.0]"));
}

TEST(Config, RemoteInferenceIsRefusedAndTheLearnerModesNeedCostModel2) {
  auto values = BaseConfig("/tmp/x");
  values["mode"] = "\"remote-inference\"";
  EXPECT_TRUE(Contains(Error(values), "not built")) << Error(values);
  for (const char* mode : {"prior-only", "learned"}) {
    values = BaseConfig("/tmp/x");
    values["mode"] = std::string("\"") + mode + "\"";
    EXPECT_TRUE(Contains(Error(values), "needs cost_model 2")) << mode;
  }
  values = BaseConfig("/tmp/x");
  values["mode"] = "\"magic\"";
  EXPECT_TRUE(Contains(Error(values), "unknown mode"));
}

TEST(Config, RulesNeedTheirThresholds) {
  auto values = BaseConfig("/tmp/x");
  values["mode"] = "\"rules\"";
  EXPECT_TRUE(Contains(Error(values), "rules mode needs"));
  values["rules"] = "\"yield_slot,unknown\"";
  EXPECT_TRUE(Contains(Error(values), "unknown rule"));
  values["rules"] = "\"yield_slot,yield_slot\"";
  EXPECT_TRUE(Contains(Error(values), "listed twice"));
  // An empty name from a stray comma is an unknown rule, not ignored.
  for (const char* stray : {"\"yield_slot,\"", "\",yield_slot\"",
                            "\"yield_slot,,garbage_hold\"", "\"\""}) {
    values["rules"] = stray;
    EXPECT_TRUE(Contains(Error(values), "unknown rule \"\"")) << stray;
  }
  values["rules"] = "\"l0_early\"";
  EXPECT_TRUE(Contains(Error(values), "rule_l0_early_read_ratio"));
  values["rules"] = "\"neighbour_release\"";
  values["rule_release_fill"] = "1.5";
  EXPECT_TRUE(Contains(Error(values), "rule_release_fill"));
}

TEST(Config, LogPathsAreReadEvenWhenLaterValuesFail) {
  auto values = BaseConfig("/tmp/x");
  values.erase("m_min");
  Config c;
  std::string error;
  EXPECT_FALSE(ParseConfig(ToJson(values), &c, &error));
  EXPECT_EQ(c.decision_log, "/tmp/x/decisions.jsonl");
  EXPECT_EQ(c.transition_log, "/tmp/x/transitions.jsonl");
}

TEST(Config, CheckedAgainstTheHost) {
  Config c;
  c.bounds = test::TestBounds();  // [2, 8]
  std::string error;
  EXPECT_TRUE(CheckConfigAgainstHost(c, 4, 20, &error));
  EXPECT_FALSE(CheckConfigAgainstHost(c, 9, 20, &error));  // trigger outside
  EXPECT_FALSE(CheckConfigAgainstHost(c, 4, 8, &error));   // cap >= K_slow
  EXPECT_TRUE(CheckConfigAgainstHost(c, 4, 9, &error));
}

TEST(Config, MissingFile) {
  Config c;
  std::string error;
  EXPECT_FALSE(LoadConfig("/nonexistent/config.json", &c, &error));
  EXPECT_TRUE(Contains(error, "cannot read"));
}

// Cost model 2's prices and the learner modes' keys (D-23 §3(d): required,
// no defaults).
std::map<std::string, std::string> V2Config(const std::string& dir) {
  auto values = BaseConfig(dir);
  values["cost_model"] = "2";
  for (const char* key : {"c_cr", "c_ib", "lambda"}) values[key] = "0";
  for (const char* key : {"c_job_flush", "c_job_l0", "c_job_deep",
                          "c_job_move", "c_st", "c_mt", "c_get0", "c_sc0",
                          "c_put", "p_dev"}) {
    values[key] = "1.5";
  }
  values["n_win"] = "1000";
  for (const char* x : kStepTypeNames) {
    values[std::string("kappa_b_") + x] = "1e-9";
    for (const char* kind : kJobKindNames) {
      values[std::string("kappa_j_") + x + "_" + kind] = "0";
    }
  }
  return values;
}

TEST(Config, CostModel2ParsesWithEveryPrice) {
  Config c;
  std::string error;
  auto values = V2Config("/tmp/x");
  values["c_job_deep"] = "3.25";
  values["kappa_j_probe_deep"] = "0.5";
  ASSERT_TRUE(ParseConfig(ToJson(values), &c, &error)) << error;
  EXPECT_EQ(c.cost_model, 2);
  EXPECT_EQ(c.v2.job[kDeepMerge], 3.25);
  EXPECT_EQ(c.v2.kappa_j[kProbe][kDeepMerge], 0.5);
  EXPECT_EQ(c.v2.kappa_b[kPut], 1e-9);
  EXPECT_EQ(c.v2.n_win, 1000);
  EXPECT_EQ(c.BasePrice(kStep), 1.5);
  EXPECT_EQ(c.JobPrice(kMove), 1.5);
}

bool IsCostModel2KeyForTest(const std::string& key) {
  static const std::set<std::string> kPrices = {
      "c_cr", "c_job_flush", "c_job_l0", "c_job_deep", "c_job_move", "c_st",
      "c_ib", "c_mt",        "c_get0",   "c_sc0",      "c_put",      "p_dev",
      "lambda", "n_win"};
  return kPrices.count(key) > 0 || key.rfind("kappa_b_", 0) == 0 ||
         key.rfind("kappa_j_", 0) == 0;
}

TEST(Config, EveryCostModel2PriceIsRequiredAndChecked) {
  const auto base = V2Config("/tmp/x");
  for (const auto& entry : base) {
    const std::string& key = entry.first;
    if (!IsCostModel2KeyForTest(key)) continue;
    auto values = base;
    values.erase(key);
    EXPECT_TRUE(Contains(Error(values), "missing \"" + key + "\"")) << key;
    values = base;
    values[key] = "-1";
    EXPECT_FALSE(Error(values).empty()) << key;
  }
  auto values = base;
  values["c_st"] = "0";  // a step price must be positive
  EXPECT_TRUE(Contains(Error(values), "c_st > 0"));
  values = base;
  values["n_win"] = "10.5";
  EXPECT_TRUE(Contains(Error(values), "integer"));
  // Cost model 1 refuses cost model 2's keys rather than ignoring them.
  values = base;
  values["cost_model"] = "1";
  EXPECT_TRUE(Contains(Error(values), "needs cost_model 2"));
  values = base;
  values["cost_model"] = "3";
  EXPECT_TRUE(Contains(Error(values), "cost_model 1 or 2"));
}

TEST(Config, TheLearnerModesNeedTheirKeysAndOthersRefuseThem) {
  auto values = V2Config("/tmp/x");
  values["mode"] = "\"prior-only\"";
  EXPECT_TRUE(Contains(Error(values), "missing \"explore\""));
  values["explore"] = "0.1";
  values["seed"] = "7";
  Config c;
  std::string error;
  ASSERT_TRUE(ParseConfig(ToJson(values), &c, &error)) << error;
  EXPECT_EQ(c.mode, Mode::kPriorOnly);
  values["explore"] = "1";
  EXPECT_TRUE(Contains(Error(values), "explore < 1"));
  values["explore"] = "0.1";
  values["mode"] = "\"learned\"";
  EXPECT_TRUE(Contains(Error(values), "missing \"weights_path\""));
  values["weights_path"] = "\"/tmp/x/weights.bin\"";
  values["push_interval_ms"] = "500";
  values["weights_required"] = "1";
  ASSERT_TRUE(ParseConfig(ToJson(values), &c, &error)) << error;
  EXPECT_EQ(c.mode, Mode::kLearned);
  EXPECT_TRUE(c.weights_required);
  EXPECT_EQ(c.push_interval_ms, 500);
  values["mode"] = "\"rules\"";
  values["rules"] = "\"yield_slot\"";
  EXPECT_TRUE(Contains(Error(values), "are for prior-only and learned"));
  values.erase("explore");
  values.erase("seed");
  EXPECT_TRUE(Contains(Error(values), "weights keys are for learned"));
}

}  // namespace
}  // namespace rlc
