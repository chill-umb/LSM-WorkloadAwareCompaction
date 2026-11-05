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

const std::vector<std::string>& F2FeatureNames();
const char* F2ActionName(Action action);

// The input vector: the last window's operation shares, live SST bytes, the
// operation count, the L0 trigger K and the multipliers m[1..6] (m[0] unused;
// absent levels count as 1).
std::vector<double> F2Features(double put_share, double get_share,
                               double scan_share, double live_bytes,
                               uint64_t op, int K,
                               const std::vector<double>& m);

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
F2Choice F2Decide(const std::vector<const Model*>& models,
                  const std::vector<double>& x, int own,
                  const std::array<double, kNumActions>& after,
                  const Mask& mask, double explore, double vote_share,
                  double u_explore, double u_pick);

}  // namespace rlc
