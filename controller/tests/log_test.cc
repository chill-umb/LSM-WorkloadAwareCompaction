// Decision and transition lines (A-Impl-8, H §6). The lines built here must
// equal the golden file fixtures/log_golden.jsonl byte for byte, and
// rl_agent/tests/test_log_format.py parses the same file, so the C++ writer
// and the Python reader agree (plan §6.2 log_test). After an intended format
// change, regenerate with RL_CONTROLLER_REGEN_GOLDEN=1 and review the diff.
#include "log.h"

#include <cmath>
#include <cstdlib>
#include <fstream>
#include <sstream>

#include "gtest/gtest.h"
#include "test_util.h"

namespace rlc {
namespace {

std::vector<std::string> GoldenLines() {
  DecisionRecord d;
  d.id = 17;
  d.level = 2;
  d.agent = Agent::kInterior;
  d.op = 123456;
  d.t_us = 987654321;
  d.mode = "rules";
  d.action = Action::kDefer;
  d.reason = "rule:yield_slot";
  d.mask = {true, false, true, true};
  d.b = {0, 0.125, 5.18e-05, -0.25};
  d.old_value = 1;
  d.requested = 1.0185;
  d.effective = 1.0185;
  d.anchor = 1;
  d.timing = 1.0185;

  DecisionRecord l0 = d;
  l0.id = 18;
  l0.level = 0;
  l0.agent = Agent::kL0;
  l0.action = Action::kCompact;
  l0.reason = "rule:k0_tracking";
  // k0 = 3 below K0 = 4: defer would be a no-op and is masked (S3).
  l0.mask = {true, true, false, true};
  l0.b = {0, -0.5, 0.25, 1};
  l0.old_value = 4;
  l0.requested = 3;
  l0.effective = 3;
  l0.anchor = 4;
  l0.timing = -1;

  // An interior transition with a state that has unmeasured values.
  TransitionRecord t;
  t.level = 2;
  t.id = 9;
  t.start_op = 100000;
  t.end_op = 123456;
  t.agent = Agent::kInterior;
  t.state.assign(FeatureNames(Agent::kInterior).size(), 0.5);
  t.state[10] = kNaN;
  t.mask = {true, true, false, true};
  t.b = {0, -0.1, 0.2, 0.3};
  t.has_action = true;
  t.action = Action::kCompact;
  t.parts.write_bytes = 1048576;
  t.parts.probes = 900;
  t.parts.fp_reads = 9;
  t.parts.seeks = 300;
  t.parts.hit_reads = 150;
  t.parts.slot_in_probes = 10.5;
  t.parts.ops = 23456;
  t.parts.gets = 12000;
  t.parts.writes = 9000;
  t.parts.scans = 2456;
  t.parts.user_bytes = 9216000;
  t.parts.held_byte_ops = 13421772.8 * 23456;
  t.b_bytes = 13421772.8;
  t.rho_tilde = 0.93;
  t.next_agent = Agent::kInterior;
  t.next_state.assign(FeatureNames(Agent::kInterior).size(), 0.25);
  t.next_mask = {true, false, true, true};

  // L0's interval opened at start: no state, no action, not replayable.
  TransitionRecord first;
  first.level = 0;
  first.start_op = 100000;
  first.end_op = 110000;
  first.agent = Agent::kL0;
  first.parts.probes = 400;
  first.parts.slot_out_probes = 10.5;
  first.parts.ops = 10000;
  first.next_agent = Agent::kL0;
  first.next_state.assign(FeatureNames(Agent::kL0).size(), 1);
  first.next_mask = {true, true, true, false};
  first.valid = false;
  first.invalid = "opened at start";

  // The last level's interval closed at stop: no next state.
  TransitionRecord closed;
  closed.level = 4;
  closed.id = 20;
  closed.start_op = 120000;
  closed.end_op = 130000;
  closed.agent = Agent::kLast;
  closed.state.assign(FeatureNames(Agent::kLast).size(), 0.75);
  closed.mask = {true, false, false, true};
  closed.has_action = true;
  closed.action = Action::kHold;
  closed.next_agent = Agent::kLast;
  closed.valid = false;
  closed.invalid = "closed at stop";

  return {DecisionLine(d), DecisionLine(l0), TransitionLine(t),
          TransitionLine(first), TransitionLine(closed)};
}

TEST(Log, LinesMatchTheGoldenFile) {
  const std::string path =
      std::string(RL_CONTROLLER_FIXTURES) + "/log_golden.jsonl";
  const std::vector<std::string> lines = GoldenLines();
  if (std::getenv("RL_CONTROLLER_REGEN_GOLDEN") != nullptr) {
    std::ofstream out(path);
    for (const std::string& line : lines) out << line << "\n";
  }
  const std::vector<std::string> golden = test::ReadLines(path);
  ASSERT_EQ(golden.size(), lines.size()) << path;
  for (size_t i = 0; i < lines.size(); ++i) EXPECT_EQ(lines[i], golden[i]);
}

TEST(Log, NumbersRoundTripAndNonFiniteIsNull) {
  EXPECT_EQ(JsonNumber(0.1), "0.1");
  EXPECT_EQ(JsonNumber(3), "3");
  EXPECT_EQ(JsonNumber(-2.5e-7), "-2.5e-07");
  EXPECT_EQ(JsonNumber(kNaN), "null");
  EXPECT_EQ(JsonNumber(INFINITY), "null");
  for (double x : {1.0 / 3, 2.0 / 3 * 1e300, 0.1 + 0.2, 1.0185 * 1.05}) {
    EXPECT_EQ(std::strtod(JsonNumber(x).c_str(), nullptr), x);
  }
}

TEST(Log, StringsAreEscaped) {
  EXPECT_EQ(JsonString("a\"b\\c\nd\x01"), "\"a\\\"b\\\\c\\nd\\u0001\"");
}

TEST(Log, FileWritesLinesAndReportsFailure) {
  const std::string dir = test::TempDir();
  LogFile log;
  std::string error;
  ASSERT_TRUE(log.Open(dir + "/x.jsonl", &error));
  log.Write("{\"a\":1}");
  log.Write("{\"a\":2}");
  EXPECT_FALSE(log.failed());
  EXPECT_EQ(test::ReadLines(dir + "/x.jsonl"),
            std::vector<std::string>({"{\"a\":1}", "{\"a\":2}"}));
  LogFile bad;
  EXPECT_FALSE(bad.Open(dir + "/missing/x.jsonl", &error));
  bad.Write("ignored");  // not open: a no-op
}

}  // namespace
}  // namespace rlc
