// The controller behind the C entry points (plan §3; PATHWAYS G-iv, A-Impl-5,
// A-Impl-8). One thread polls the host every 2 ms. Each poll attributes the
// reads, operations and jobs since the last one to every level's open
// interval; then each level whose decision is due (every N_j / k operations,
// L0 once per flush, N_0 / K0_cfg; none during a write stop) relaxes, is observed, masked, priced by the prior
// and decides. The decisions update the controller's target values, and the
// targets reach the host in one Apply, at most one per
// setoptions_min_interval_ms (A-Impl-5): changes that fall due sooner are
// merged into the next Apply. Hold-only never calls Apply, except for a
// fallback away from non-native values. While a change is held, the logged
// state describes the targets, not what RocksDB uses (log.h).
//
// The learner modes (plan step 10): prior-only decides on Q = -b, learned on
// Q = -b + f_theta + delta_j from the weights file, re-read every
// push_interval_ms when its version grows; both explore with probability
// explore. Under cost model 2 every completed job gets a "job" line with its
// window's step counts (jobs.h), and in the learner modes each transition
// carries its neighbours' states at the decision (H §3).
//
// Turn mode (turn.h): the operation clock's turns of turn_ops operations.
// At the first poll past each boundary the plugin closes the turn, builds
// the tree, decays the anchors, computes the state and masks, loads a newer
// weights file, chooses (greedy, then one level explores), encodes, sends
// one Apply if the vector changed, and writes a "turn" line. Between
// boundaries only a deferred level's top-ups change a target. Turns keep
// closing during a write stop; at the drain a final "turn" line carries the
// state only.
//
// The per-level read log (level_reads, logging only): in turn mode every turn
// line carries each level's read counts over the turn; in hold-only a
// "level_reads" line closes every level_reads_ops operations, and a last one
// at the drain, so the lines tile the run from the start to the drain. Both
// read the counters the poll already reads, on this thread.
//
// Fallback (A-Impl-8): an invalid config or host options, a failed Apply, a
// failed log write, an invalid weights file (or a missing one when
// weights_required) or an exception applies m = 1 and the configured
// trigger, logs why, and stops deciding. Attribution continues, so the transition log
// still covers the measured phase (OBJ-1). No exception leaves the plugin.
#pragma once

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <random>
#include <string>
#include <thread>
#include <vector>

#include "actions.h"
#include "attribution.h"
#include "config.h"
#include "jobs.h"
#include "learned2.h"
#include "log.h"
#include "masks.h"
#include "mlp.h"
#include "rocksdb/rl_controller_host.h"
#include "state.h"
#include "turn.h"

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
  // What a decision opens a level's next interval with.
  struct Opening {
    Agent agent = Agent::kInterior;
    std::vector<double> state;
    Mask mask{};
    Values b{};
    uint64_t id = 0;
    bool has_action = false;
    Action action = Action::kHold;
    double value_before = kNaN, value_after = kNaN;
    NeighbourView up, down;
  };
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
    double value_before = kNaN, value_after = kNaN;
    NeighbourView up, down;
    bool valid = false;
    std::string invalid = "opened at start";
  };

  void Poll(bool decide);
  // The levels due this poll decide and update the targets.
  std::vector<DecisionRecord> Decide(
      const ROCKSDB_NAMESPACE::RLTreeSnapshot& snapshot,
      const ROCKSDB_NAMESPACE::RLOpCounts& ops,
      const std::vector<ROCKSDB_NAMESPACE::RLLevelReadCounts>& reads,
      const ROCKSDB_NAMESPACE::RLStepCounts& steps, uint64_t now);
  // A neighbour's state, mask and prior now, at its current control.
  NeighbourView Neighbour(const View& v, int level,
                          const std::vector<double>& m) const;
  // Cost model 2: the job's window, its "job" line and its charge's share
  // of the level's stats; before stats_.OnJob, so it lands in the interval
  // open at its completion.
  void RecordJob(const ROCKSDB_NAMESPACE::RLJobRecord& job);
  // Learned mode: re-reads the weights file when its version grew.
  void PollWeights(uint64_t op, uint64_t now);
  // learned2 (learned2.h): closes the open window once window_ops have
  // passed, writes its "window" line, and opens the next.
  void F2Window(const ROCKSDB_NAMESPACE::RLOpCounts& ops, uint64_t now);
  // learned2's L0 agent under k_trials (config.h), at a window boundary:
  // the trial's schedule and verdicts. Returns false when the agent makes
  // no decision at this boundary; else true, with *K the value it wants
  // and *reason one of trial (to the candidate), back (to the incumbent),
  // adopt, reject, inconclusive.
  struct F2TrialNote {
    int inc = 0, cand = 0, blocks = 0, number = 0;
    bool judged = false;
    F2Verdict verdict;
  };
  bool F2TrialStep(int* K, int k_min, int k_cap, uint64_t op,
                   std::string* reason, F2TrialNote* note);
  // learned2's decisions (F1-F5), in place of Decide's.
  std::vector<DecisionRecord> DecideLearned2(
      const ROCKSDB_NAMESPACE::RLTreeSnapshot& snapshot,
      const ROCKSDB_NAMESPACE::RLOpCounts& ops,
      const std::vector<ROCKSDB_NAMESPACE::RLLevelReadCounts>& reads,
      uint64_t now);
  // Net bytes landed so far in the level agent `agent` watches: its own,
  // L1's for L0.
  double F2Inflow(int agent) const;
  // The agent's last change has settled (or it never changed).
  bool F2Settled(int agent) const;
  // A change of the agent's setting: it must settle again, and the open
  // window is no longer clean.
  void F2Changed(int agent, uint64_t op);
  // Turn mode: the boundary's steps or, between boundaries, the defer
  // top-ups, then Flush. Returns false, with the reason, if an Apply failed.
  bool TurnPoll(const ROCKSDB_NAMESPACE::RLTreeSnapshot& snapshot,
                const ROCKSDB_NAMESPACE::RLOpCounts& ops,
                const std::vector<ROCKSDB_NAMESPACE::RLLevelReadCounts>& reads,
                uint64_t now, std::string* error);
  // Closes the open turn and, unless `final`, chooses and applies the next
  // turn's actions; writes the "turn" line either way.
  bool TurnBoundary(const ROCKSDB_NAMESPACE::RLTreeSnapshot& snapshot,
                    const ROCKSDB_NAMESPACE::RLOpCounts& ops,
                    const std::vector<ROCKSDB_NAMESPACE::RLLevelReadCounts>& reads,
                    uint64_t now, bool final, std::string* error);
  // The View the L0 prior reads (turn_prior): the tree and options now, the
  // settings in force, and every count summed over the forecast window
  // (turn_ring_, the last turn_forecast_turns turns, the one just closed
  // included); F is that window's flushed bytes per flush.
  View TurnWindowView(const ROCKSDB_NAMESPACE::RLTreeSnapshot& snapshot,
                      const ROCKSDB_NAMESPACE::RLOpCounts& ops,
                      const std::vector<ROCKSDB_NAMESPACE::RLLevelReadCounts>& reads) const;
  // A completed job's share of the open turn's activity.
  void TurnJob(const ROCKSDB_NAMESPACE::RLJobRecord& job);
  // Raises a deferred level's override when it nears due (at most
  // turn_max_topups a turn), or logs the defer broken. True if a target
  // changed.
  bool TurnTopUps(const ROCKSDB_NAMESPACE::RLTreeSnapshot& snapshot,
                  uint64_t op, uint64_t now);
  // A newer weights version, checked at a boundary at most once per
  // push_interval_ms; an invalid file (another arch, say) falls back.
  void TurnWeights(uint64_t op, uint64_t now);
  // The drain: its line, the intervals invalidated, the turn's final line.
  void EnterDrain(const ROCKSDB_NAMESPACE::RLOpCounts& ops,
                  const std::vector<ROCKSDB_NAMESPACE::RLLevelReadCounts>& reads,
                  uint64_t now);
  // Sends the targets to the host if they differ from what it last accepted
  // and the cap allows. Returns false, with the reason, if the host refused
  // them.
  bool Flush(uint64_t op, uint64_t now, bool new_changes, std::string* error);
  // Hold-only's next scheduled change (Config::schedule), once each.
  void ApplyScheduled(uint64_t op, uint64_t now);
  // Hold-only with level_reads (config.h): closes the open window
  // [lr_op0_, op) with its "level_reads" line and opens the next at op.
  void LevelReadsWindow(uint64_t op,
                        const std::vector<ROCKSDB_NAMESPACE::RLLevelReadCounts>& reads,
                        uint64_t now, bool final);
  // Writes the level's transition and opens its next interval.
  void CloseInterval(int level, const View& v, const Opening& next,
                     uint64_t op);
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
  size_t next_scheduled_ = 0;  // Config::schedule's changes made (or tried)
  uint64_t last_apply_us_ = 0;
  uint64_t pending_first_id_ = 0;  // 0: nothing pending
  uint64_t last_id_ = 0;

  Stats stats_;
  OpClock clock_;
  std::vector<LevelParts> parts_;
  std::vector<Interval> intervals_;
  std::vector<uint64_t> last_decision_op_;
  // Each level's recent decision points, for a job's count (ARCH-8).
  std::vector<std::deque<uint64_t>> decision_ops_;
  uint64_t next_id_ = 1;
  bool flush_size_logged_ = false;

  // Cost model 2: step-counter samples since the start, and the begin
  // records of running compactions and flushes by job id.
  StepRing ring_;
  ROCKSDB_NAMESPACE::RLStepCounts prev_steps_;
  std::map<int, StepSample> compaction_begins_, flush_begins_;
  // learned2: per agent (0 = L0), its last change and last decision.
  struct F2Agent {
    bool changed_once = false;
    uint64_t change_op = 0;
    double inflow_at_change = 0;
    uint64_t decision_op = 0;
    double inflow_at_decision = 0;
  };
  std::vector<F2Agent> f2_;
  std::vector<double> f2_C_;  // the nominal targets C_i, from the last view
  bool f2_window_open_ = false;
  uint64_t f2_window_op_ = 0;
  ROCKSDB_NAMESPACE::RLOpCounts f2_window_ops_;
  std::vector<double> f2_window_settled_;  // per agent, at the window's start
  bool f2_window_changed_ = false;
  bool f2_have_window_ = false;
  double f2_shares_[3] = {0, 0, 0};  // the last closed window's put, get, scan
  // The noise fix (config.h window_cycles, k_trials): non-trivial L0->L1
  // merges ended in the open window; a window closed at this poll; the last
  // windows' inputs (for the trial verdict); the L0 agent's trial.
  uint64_t f2_l0_merges_ = 0;
  bool f2_boundary_ = false;
  std::deque<std::vector<double>> f2_recent_x_;
  // Input set 2 (config.h f2_inputs, learned2.h F2StateInputs): the open
  // window's start (the K before it, RocksDB's pending compaction bytes, L0's
  // files, the highest deeper score), the activity of the window before it
  // and of the open one so far, the step counters at its start, and the last
  // closed window's six values (the decisions' inputs).
  struct F2Activity {
    double deep_bytes = 0;   // s + o of non-trivial merges from L1 down
    double ops = 0;
    double block_probes = 0;
    double gets = 0;
    double stall_ends = 0;   // flush and compaction ends with writes slowed
    double ends = 0;         // flush and compaction ends
  };
  int f2_k_prev_ = 0;
  double f2_start_pending_ = 0;
  int f2_start_l0_files_ = 0;
  double f2_start_deep_max_ = 0;
  F2Activity f2_prev_act_, f2_act_;
  ROCKSDB_NAMESPACE::RLStepCounts f2_window_steps_;
  F2State f2_last_state_;
  struct F2Trial {
    bool active = false;
    int inc = 0, cand = 0;   // the incumbent K and the neighbour on trial
    int blocks = 0;          // blocks completed in this trial
    bool on_cand = false;    // the running block uses the candidate
    uint64_t block_op = 0;   // the running block's first operation
    uint64_t wait_until = 0; // no new trial before this operation
    int last_dir = 0;        // the last trial's direction (+1 up, -1 down)
    bool last_adopted = false;
    int failed = 0;          // directions failed since the last adoption: 1 down, 2 up
    int number = 0;          // trials started
  };
  F2Trial f2_trial_;

  // Turn mode (turn.h).
  struct TurnActivity {      // one turn's measured activity
    double ops = 0;
    double puts = 0, gets = 0, scans = 0;
    double polls = 0, busy_polls = 0, stall_polls = 0;
    double flushes = 0;
    double merge_bytes = 0;  // S + O of the slot's non-trivial merges
    std::vector<double> inflow;  // net bytes landed, per level
    std::vector<double> probes;  // SST probes, per level
    // The L0 prior's window counts (turn_prior), per level as the View's.
    double user_bytes = 0;       // BYTES_WRITTEN
    std::vector<double> hit_reads, fp_reads, seeks, get_reopens, iter_reopens;
  };
  struct TurnDefer {         // a defer in force this turn
    bool active = false;
    int topups = 0;
    double flushes_expected = 0;  // L0: f_hat at the boundary
    double inflow_expected = 0;   // level i: I_hat at the boundary
  };
  Ladder turn_k_ladder_, turn_m_ladder_;
  std::vector<AnchorState> turn_anchors_;
  uint64_t turn_index_ = 0;    // turns closed
  uint64_t turn_op0_ = 0;      // the open turn's first operation
  uint64_t turn_next_op_ = 0;  // its boundary: the start plus whole turns
  uint64_t turn_start_op_ = 0;
  ROCKSDB_NAMESPACE::RLOpCounts turn_ops0_;
  std::vector<ROCKSDB_NAMESPACE::RLLevelReadCounts> turn_reads0_;
  TurnActivity turn_act_;
  std::deque<TurnActivity> turn_ring_;  // the last turn_forecast_turns
  std::vector<TurnDefer> turn_defer_;
  std::vector<uint64_t> turn_slow_decisions_;  // per level (turn_periods)
  TurnNet turn_net_;
  bool turn_have_net_ = false;
  bool turn_weights_checked_ = false;
  uint64_t turn_weights_check_us_ = 0;
  uint64_t applies_ok_ = 0;          // accepted Flush calls
  uint64_t turn_applies_at_line_ = 0;
  bool turn_final_written_ = false;

  // Hold-only's per-level read windows (level_reads): on only when the config
  // parsed with them, then through any fallback (they decide nothing); the
  // open window's first operation and counters, its boundary (the start plus
  // whole windows), and the windows closed.
  bool lr_windows_ = false;
  uint64_t lr_op0_ = 0;
  uint64_t lr_next_op_ = 0;
  uint64_t lr_index_ = 0;
  std::vector<ROCKSDB_NAMESPACE::RLLevelReadCounts> lr_reads0_;

  // The learner modes.
  std::mt19937_64 rng_;
  Weights weights_;
  bool weights_polled_ = false;
  uint64_t last_weights_poll_us_ = 0;

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
