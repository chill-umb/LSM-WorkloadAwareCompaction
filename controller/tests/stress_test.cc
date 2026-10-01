// The concurrency contract (header "Threads"; verifier C3): the polling
// thread, job callbacks from several threads at once, a drain set mid-run,
// and destroy while callbacks are still firing. Meant to run under TSan and
// ASan as well as plainly.
#include <atomic>
#include <chrono>
#include <random>
#include <thread>

#include "gtest/gtest.h"
#include "plugin.h"
#include "test_util.h"

namespace rlc {
namespace {

using test::BaseConfig;
using test::FakeHost;
using test::ReadLines;
using test::TempDir;
using test::WriteConfig;

TEST(Stress, CallbacksDrainAndDestroyRunConcurrently) {
  const std::string dir = TempDir();
  auto values = BaseConfig(dir);
  values["mode"] = "\"rules\"";
  values["rules"] =
      "\"k0_tracking,l0_early,yield_slot,garbage_hold,neighbour_release\"";
  values["rule_l0_early_read_ratio"] = "0.5";
  values["rule_release_fill"] = "0.3";
  values["rule_garbage_drop"] = "0.2";
  values["setoptions_min_interval_ms"] = "1";
  const std::string config = WriteConfig(dir, values);

  for (int round = 0; round < 12; ++round) {
    FakeHost host;
    host.SetL0(3);
    for (int level = 1; level < 5; ++level) host.SetLevel(level, 0.97);
    void* controller = rl_controller_create(&host, config.c_str());
    ASSERT_NE(controller, nullptr);
    std::atomic<bool> stop{false};
    std::vector<std::thread> firers;
    for (int t = 0; t < 3; ++t) {
      firers.emplace_back([&, t] {
        std::mt19937 rng(t + 7 * round);
        int id = 1000 * t;
        while (!stop) {
          host.AddOps(500, 500);
          ROCKSDB_NAMESPACE::RLJobRecord r;
          const int kind = static_cast<int>(rng() % 3);
          if (kind == 0) {
            r = FakeHost::Flush(1 << 20);
          } else {
            r = FakeHost::Compaction(
                kind == 2, id + static_cast<int>(rng() % 5),
                static_cast<int>(rng() % 4), 8 << 20, 4 << 20, 11 << 20);
            r.op = host.OpCounts().total();
            r.due_since_micros = rng() % 2;
          }
          host.Job(r);
        }
      });
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(15 + round));
    const bool drain = round % 3 == 2;
    if (drain) {
      host.SetDraining();
    } else {
      // The controller really decides and applies under this load (a broken
      // Decide or Flush would leave the host untouched). Bounded wait.
      const auto deadline =
          std::chrono::steady_clock::now() + std::chrono::seconds(5);
      while (host.Applies() == 0 &&
             std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
      }
      EXPECT_GT(host.Applies(), 0u) << "round " << round;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    rl_controller_destroy(controller);  // callbacks still firing
    EXPECT_FALSE(host.HasCallback());
    stop = true;
    for (auto& firer : firers) firer.join();
    // The logs are complete: every level's interval was closed at stop.
    const auto decisions = ReadLines(dir + "/decisions.jsonl");
    ASSERT_FALSE(decisions.empty());
    EXPECT_EQ(decisions.back().find("{\"type\":\"stop\""), 0u) << round;
    EXPECT_EQ(test::CountType(ReadLines(dir + "/transitions.jsonl"),
                              "transition") >= 5,
              true);
  }
}

}  // namespace
}  // namespace rlc
