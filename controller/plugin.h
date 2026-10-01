// The controller behind the C entry points (plan §3; PATHWAYS G-iv, A-Impl-5,
// A-Impl-8). One thread polls the host every 2 ms. Each poll attributes the
// reads, operations and jobs since the last one to every level's open
// interval; then each level whose decision is due (every N_j / k operations,
// none during a write stop) relaxes, is observed, masked, priced by the prior
// and decides. The decisions update the controller's target values, and the
// targets reach the host in one Apply, at most one per
// setoptions_min_interval_ms (A-Impl-5): changes that fall due sooner are
// merged into the next Apply. Hold-only never calls Apply, except for a
// fallback away from non-native values. While a change is held, the logged
// state describes the targets, not what RocksDB uses (log.h).
//
// Fallback (A-Impl-8): an invalid config or host options, a failed Apply, a
// failed log write or an exception applies m = 1 and the configured trigger,
// logs why, and stops deciding. Attribution continues, so the transition log
// still covers the measured phase (OBJ-1). No exception leaves the plugin.
#pragma once

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "actions.h"
#include "attribution.h"
#include "config.h"
#include "log.h"
#include "masks.h"
#include "rocksdb/rl_controller_host.h"
#include "state.h"

namespace rlc {

using ROCKSDB_NAMESPACE::RLControllerHost;

class Controller {
 public:
  // Reads the config and the host's options, opens the logs and registers
  // the job callback; no thread yet. `clock` returns steady-clock
  // microseconds; tests pass their own.
  Controller(RLControllerHost* host, const std::string& config_path,
             std::function<uint64_t()> clock = nullptr);
  ~Controller();
  Controller(const Controller&) = delete;
  Controller& operator=(const Controller&) = delete;

  void Start();  // the polling thread
  // One poll. Public so tests can drive the controller without the thread.
  void Step() noexcept;
  // Idempotent: removes the callback, joins the thread, attributes the last
  // segment and closes every open interval.
  void Stop() noexcept;

  bool fallback() const { return fallback_; }

 private:
  // An open decision interval of one level.
  struct Interval {
    uint64_t id = 0;  // 0: opened at start, without a decision
    uint64_t start_op = 0;
    Agent agent = Agent::kInterior;
    std::vector<double> state;
    Mask mask{};
    Values b{};
    bool has_action = false;
    Action action = Action::kHold;
    bool valid = false;
    std::string invalid = "opened at start";
  };

  void Poll(bool decide);
  // The levels due this poll decide and update the targets.
  std::vector<DecisionRecord> Decide(
      const ROCKSDB_NAMESPACE::RLTreeSnapshot& snapshot,
      const ROCKSDB_NAMESPACE::RLOpCounts& ops,
      const std::vector<ROCKSDB_NAMESPACE::RLLevelReadCounts>& reads,
      uint64_t now);
  // Sends the targets to the host if they differ from what it last accepted
  // and the cap allows. Returns false, with the reason, if the host refused
  // them.
  bool Flush(uint64_t op, uint64_t now, bool new_changes, std::string* error);
  // Writes the level's transition and opens its next interval.
  void CloseInterval(int level, const View& v, Agent agent,
                     const std::vector<double>& state, const Mask& mask,
                     const Values& b, uint64_t op, uint64_t id, bool has_action,
                     Action action);
  void EnterFallback(const std::string& reason);
  void FallbackNoThrow(const std::string& reason) noexcept;
  void Invalidate(const std::string& reason);
  void CheckLogs();
  void Run() noexcept;

  RLControllerHost* const host_;
  std::function<uint64_t()> now_;
  ROCKSDB_NAMESPACE::RLHostOptions options_;
  int n_ = 0;
  Config cfg_;
  LogFile decisions_;
  LogFile transitions_;

  // Control state, the values it wants, and what the host last accepted.
  std::vector<LevelControl> controls_;  // [0] unused
  L0Control l0_;
  std::vector<double> m_target_;
  int k0_target_ = 0;
  std::vector<double> m_applied_;
  int k0_applied_ = 0;
  // The SetOptions cap: the last Apply's time, and the decisions whose
  // changes have not reached the host yet.
  bool applied_once_ = false;
  uint64_t last_apply_us_ = 0;
  uint64_t pending_first_id_ = 0;  // 0: nothing pending
  uint64_t last_id_ = 0;

  Stats stats_;
  OpClock clock_;
  std::vector<LevelParts> parts_;
  std::vector<Interval> intervals_;
  std::vector<uint64_t> last_decision_op_;
  uint64_t next_id_ = 1;
  bool flush_size_logged_ = false;

  // The previous poll's counters and sampled state, for the next segment.
  ROCKSDB_NAMESPACE::RLOpCounts prev_ops_;
  std::vector<ROCKSDB_NAMESPACE::RLLevelReadCounts> prev_reads_;
  int prev_running_ = -1;
  bool prev_l0_due_ = false;
  int prev_k0_all_ = 0;
  std::vector<double> prev_held_;
  std::shared_ptr<const ROCKSDB_NAMESPACE::RLTreeSnapshot> last_snapshot_;

  bool fallback_ = false;
  bool draining_ = false;
  bool stopped_ = false;

  std::mutex queue_mu_;
  std::vector<ROCKSDB_NAMESPACE::RLJobRecord> queue_;
  std::atomic<bool> lost_jobs_{false};

  std::mutex stop_mu_;
  std::condition_variable stop_cv_;
  bool stop_ = false;
  std::thread thread_;
};

}  // namespace rlc
