// Cost model 2's per-job quantities (PATHWAYS D §1 as amended 2026-10-03,
// D-23; H §6), computed as scripts/dbbench_pipeline/cost_model_v2.py computes
// them, so the plugin's job records and the evaluator use one estimator
// (ARCH-7):
//
//   tau_job = c_job[kind] + c_cr (S + O) + c_w X    (a trivial move: c_job)
//   I       = q_bar sum_x rho_x (kappa_B_x Y + kappa_J_x,kind t_job)
//   Y = X + lambda (S + O) (0 for a move), t_job = tau_job / p_dev,
//   rho_x   = base price of x * steps of type x in the window / its operations
//
// A job's window is its own operations if it served at least n_win;
// otherwise it starts at the last counter sample at or before n_end - n_win
// (the controller's start at the earliest). The plugin's samples are its
// polls' StepCounts(), every 2 ms; the evaluator's are the host log's snap
// records every n_str operations. The two differ by at most a poll's
// operations at a short job's window start (the interim interface §2.6).
#pragma once

#include <array>
#include <cstdint>
#include <deque>

#include "config.h"
#include "rocksdb/rl_controller_host.h"

namespace rlc {

using StepArray = std::array<double, kNumStepTypes>;

// Step counts by type from the host's cumulative counters (cost_model_v2's
// TYPE_TICKERS): step = returned + hidden entries; memtable = Gets + scans;
// iterator blocks have no counter on the interim binary (0).
StepArray TypeCounts(const ROCKSDB_NAMESPACE::RLStepCounts& s);
// The operation count the counters themselves give: Puts + Gets + scans.
uint64_t StepOps(const ROCKSDB_NAMESPACE::RLStepCounts& s);

struct StepSample {
  uint64_t op = 0;
  ROCKSDB_NAMESPACE::RLStepCounts steps;
};

// The counter samples of the last operations, oldest first, and the floor
// (the controller's start), which no window starts before.
class StepRing {
 public:
  void Reset(const StepSample& floor);
  // Adds a sample if its operation count advanced; drops samples a window
  // can no longer start at (more than keep_ops before the newest), keeping
  // one at or before that point.
  void Add(const StepSample& sample, uint64_t keep_ops);
  // The last sample with op <= target, or the floor.
  const StepSample& StartFor(uint64_t target) const;
  const StepSample& floor() const { return floor_; }
  size_t size() const { return samples_.size(); }

 private:
  StepSample floor_;
  std::deque<StepSample> samples_;
};

// The window of a job that ended at end.op with counters end.steps.
// has_begin: its begin record (begin.op, begin.steps) arrived after the
// controller started.
struct Window {
  uint64_t start_op = 0;
  double ops = 0;     // its length in operations
  StepArray counts{};  // steps of each type inside it
  bool own = false;   // its own operations (it served at least n_win)
};
Window JobWindow(bool has_begin, const StepSample& begin,
                 const StepSample& end, const StepRing& ring, int n_win);

int KindOf(bool flush, bool trivial, int start_level);

double JobTau(const Config& cfg, int kind, double s, double o, double x);
double JobBytes(const Config& cfg, int kind, double s, double o, double x);
// rho: quiet money per operation of each type over the job's window.
StepArray Rho(const Config& cfg, const StepArray& counts, double ops);
// The interference charge's read and write parts, in money.
void Interference(const Config& cfg, int kind, double s, double o, double x,
                  const StepArray& rho, double* read, double* write);

}  // namespace rlc
