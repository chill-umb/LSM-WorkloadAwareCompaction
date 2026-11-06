// The learned2 mode (owner, 2026-11-05: fixes F1-F5 of the upper-level
// learner; design ~/node_ops/workers/2026-11-05-learnfix/design_learned2.md,
// kept out of PATHWAYS until data proves it):
//   F1 persistent actions: hold, down (K - 1, or m / alpha), up (K + 1, or
//      m * alpha); a value stays until the agent's next move, nothing relaxes;
//   F2 a decision only after the agent's last change has settled and a
//      measurement stretch has passed; an action's value is the predicted
//      settled cost of the setting it leads to (no bootstrapped targets);
//   F3 a non-hold greedy move only when at least vote_share of the ensemble
//      puts it below hold; otherwise hold (exploration aside);
//   F4 one clean input vector (F2FeatureNames), never NaN, never clipped;
//   F5 every agent predicts the same quantity: log J per operation of the
//      whole tree, priced by the trainer from the host log as 04 prices it.
// Slots of the action arrays: 0 hold, 1 down, 2 up, 3 never allowed.
#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <vector>

#include "actions.h"
#include "masks.h"
#include "mlp.h"

namespace rlc {

constexpr int kF2Levels = 6;  // log2 m_1 .. m_6 are inputs
// Feature indices.
constexpr int kF2PutShare = 0, kF2GetShare = 1, kF2ScanShare = 2,
              kF2LogH = 3, kF2LogOp = 4, kF2LogK = 5, kF2LogM1 = 6;
// Input set 2 (config.h f2_inputs; after the input study
// ~/node_ops/reports/2026-11-06-inputstudy.md) appends six inputs of the
// tree's state, in the study's order ("six"):
constexpr int kF2Up0Pending = 12, kF2Dn0Pending = 13, kF2TopIsL0 = 14,
              kF2PrevDeepBpo = 15, kF2FposPerGet = 16, kF2Stall = 17;

// The input set in force for the process (1 or 2; anything else counts as
// 1). The plugin sets it from its config before it reads any weights; the
// weights file's feature hash and input count follow F2FeatureNames().
void SetF2Inputs(int version);
int F2Inputs();
const std::vector<std::string>& F2FeatureNames();
const char* F2ActionName(Action action);

// Input set 2's six values for a window, all from before the window or its
// first poll (live, the run's own past only):
//   up0_x_pending / dn0_x_pending: the window is the first after K went up /
//     down (k_window above / below k_prev, the previous window's K), times
//     ln(1 + RocksDB's pending compaction bytes at the window's start /
//     C_1); else 0;
//   top_is_l0: L0's score at the start (its files / k_window) is at least
//     every deeper level's score but the last's (deep_max_score): 1, else 0;
//   prev_deep_bpo: ln(1 + bytes read by non-trivial merges from L1 and
//     deeper (s + o) that ended in the previous window / its operations);
//   fpos_per_get: the previous window's data-block probes past the filter
//     (BLOOM_FILTER_FULL_POSITIVE) per point lookup, 0 without lookups;
//   stall: the share of the previous window's flush and compaction ends at
//     which writes were slowed or stopped, 0 without any.
struct F2State {
  double up0_x_pending = 0, dn0_x_pending = 0, top_is_l0 = 0,
         prev_deep_bpo = 0, fpos_per_get = 0, stall = 0;
};
F2State F2StateInputs(int k_prev, int k_window, double pending_bytes,
                      double base_bytes, int l0_files, double deep_max_score,
                      double prev_deep_bytes, double prev_ops,
                      double prev_block_probes, double prev_gets,
                      double prev_stall_ends, double prev_ends);

// The input vector: the last window's operation shares, live SST bytes, the
// operation count, the L0 trigger K and the multipliers m[1..6] (m[0] unused;
// absent levels count as 1); under input set 2 also `state`'s six values.
std::vector<double> F2Features(double put_share, double get_share,
                               double scan_share, double live_bytes,
                               uint64_t op, int K,
                               const std::vector<double>& m,
                               const F2State& state = F2State());

struct F2Choice {
  Action action = Action::kHold;
  std::string reason;  // explore, cold, hold, confident, gate
  std::array<double, kNumActions> mean{};  // mean predicted cost per slot
  int votes = 0, need = 0, members = 0;
  Action greedy = Action::kHold;
};

// own: the feature index of the deciding agent's setting; after[slot]: that
// feature's value after the slot's action (unused where the mask forbids).
// models: the ensemble (output 0 is the predicted cost, lower is better).
// min_effect: a confident move also needs mean[move] <= mean[hold] -
// min_effect (reason "small" when the vote passes but the gain is smaller).
F2Choice F2Decide(const std::vector<const Model*>& models,
                  const std::vector<double>& x, int own,
                  const std::array<double, kNumActions>& after,
                  const Mask& mask, double explore, double vote_share,
                  double u_explore, double u_pick, double min_effect = 0);

// The K trial's judgement (the noise fix, config.h k_trials): per member,
// the mean over the inputs xs of the predicted cost with the agent's own
// feature at v_cand minus at v_inc (log J per operation; negative: the
// candidate is cheaper). verdict +1: the candidate is cheaper with at least
// ceil(vote_share E) members behind it and a mean effect <= -min_effect;
// -1: dearer by the same rule; 0: neither (or no models or inputs).
struct F2Verdict {
  int verdict = 0;
  double effect = 0;  // mean over members
  int cheaper = 0, dearer = 0, need = 0, members = 0;
};
F2Verdict F2TrialVerdict(const std::vector<const Model*>& models,
                         const std::vector<std::vector<double>>& xs, int own,
                         double v_inc, double v_cand, double vote_share,
                         double min_effect);

}  // namespace rlc
