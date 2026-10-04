// The learned residual's forward pass and weights file (mlp.h; plan §6.2
// mlp_test): f per action equals the golden file rl_agent/tests/
// test_export.py writes for fixed weights, to 1e-9 (ARCH-6 at unit level);
// a bad magic, checksum or length, trailing bytes, or another agent's state
// inputs make the file invalid; a missing file is reported as missing.
#include "mlp.h"

#include <cmath>
#include <fstream>
#include <sstream>

#include "gtest/gtest.h"
#include "test_util.h"

namespace rlc {
namespace {

const std::string kBin = std::string(RL_CONTROLLER_FIXTURES) + "/mlp_golden.bin";
const std::string kTxt = std::string(RL_CONTROLLER_FIXTURES) + "/mlp_golden.txt";

Agent AgentOf(const std::string& name) {
  return name == "l0" ? Agent::kL0 : name == "last" ? Agent::kLast
                                                    : Agent::kInterior;
}

std::string ReadBytes(const std::string& path) {
  std::ifstream file(path, std::ios::binary);
  std::stringstream s;
  s << file.rdbuf();
  return s.str();
}

std::string Write(const std::string& bytes) {
  const std::string path = test::TempDir() + "/weights.bin";
  std::ofstream(path, std::ios::binary) << bytes;
  return path;
}

TEST(Mlp, ForwardMatchesThePythonGoldenFile) {
  Weights w;
  std::string error;
  ASSERT_EQ(LoadWeights(kBin, &w, &error), LoadStatus::kOk) << error;
  EXPECT_EQ(w.version, 3u);
  ASSERT_EQ(w.models.size(), 4u);
  std::ifstream cases(kTxt);
  int checked = 0;
  for (std::string line; std::getline(cases, line);) {
    std::istringstream in(line);
    std::string agent, token;
    int level = 0;
    size_t n = 0;
    in >> agent >> level >> n;
    std::vector<double> state(n);
    for (double& v : state) {
      in >> token;
      v = token == "nan" ? kNaN : std::stod(token);
    }
    in >> token;
    ASSERT_EQ(token, "|");
    const Model* model = w.Find(AgentOf(agent), level);
    ASSERT_NE(model, nullptr) << agent << " " << level;
    const auto f = Forward(*model, state);
    for (int a = 0; a < kNumActions; ++a) {
      in >> token;
      EXPECT_NEAR(f[a], std::stod(token), 1e-9) << agent << level << a;
    }
    ++checked;
  }
  EXPECT_EQ(checked, 4);
}

TEST(Mlp, ALevelsOwnModelWinsOverTheShared) {
  Weights w;
  std::string error;
  ASSERT_EQ(LoadWeights(kBin, &w, &error), LoadStatus::kOk) << error;
  EXPECT_EQ(w.Find(Agent::kInterior, 2)->level, 2);
  EXPECT_EQ(w.Find(Agent::kInterior, 3)->level, -1);
  EXPECT_TRUE(w.Find(Agent::kInterior, 3)->has_delta);
  EXPECT_EQ(Weights().Find(Agent::kL0, 0), nullptr);
}

TEST(Mlp, CorruptFilesAreInvalidAndAMissingOneIsMissing) {
  const std::string good = ReadBytes(kBin);
  Weights w;
  std::string error;
  uint64_t version = 0;
  ASSERT_EQ(PeekVersion(kBin, &version, &error), LoadStatus::kOk);
  EXPECT_EQ(version, 3u);
  EXPECT_EQ(LoadWeights(test::TempDir() + "/none.bin", &w, &error),
            LoadStatus::kMissing);
  EXPECT_EQ(PeekVersion(test::TempDir() + "/none.bin", &version, &error),
            LoadStatus::kMissing);

  std::string bad = good;
  bad[0] = 'X';
  EXPECT_EQ(LoadWeights(Write(bad), &w, &error), LoadStatus::kInvalid);
  EXPECT_NE(error.find("magic"), std::string::npos);

  bad = good;
  bad.back() = static_cast<char>(bad.back() ^ 1);
  EXPECT_EQ(LoadWeights(Write(bad), &w, &error), LoadStatus::kInvalid);
  EXPECT_NE(error.find("checksum"), std::string::npos);

  EXPECT_EQ(LoadWeights(Write(good.substr(0, good.size() - 9)), &w, &error),
            LoadStatus::kInvalid);
  EXPECT_NE(error.find("truncated"), std::string::npos);
  EXPECT_EQ(LoadWeights(Write(good.substr(0, 20)), &w, &error),
            LoadStatus::kInvalid);
  EXPECT_EQ(LoadWeights(Write(good + "x"), &w, &error), LoadStatus::kInvalid);
  EXPECT_NE(error.find("trailing"), std::string::npos);
  // A failed load leaves the weights it was given untouched.
  EXPECT_EQ(w.version, 0u);
}

TEST(Mlp, NaNIsZeroAndInputsAreClipped) {
  Model m;
  m.agent = Agent::kL0;
  m.n_in = 2;
  m.clip = 1.5;
  Layer layer;
  layer.out = 4;
  layer.in = 2;
  layer.w = {1, 0, 0, 1, 1, 1, -1, 0};
  layer.b = {0, 0, 0.5, 0};
  m.layers.push_back(layer);
  const auto f = Forward(m, {kNaN, 9});
  EXPECT_EQ(f[0], 0);
  EXPECT_EQ(f[1], 1.5);
  EXPECT_EQ(f[2], 2);
  EXPECT_EQ(f[3], 0);
}

}  // namespace
}  // namespace rlc
