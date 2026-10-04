// The learned residual f_theta (+ delta_j) of Q = -b + f_theta + delta_j
// (PATHWAYS G §3, H §4, H §6; plan §3): a small ReLU MLP per model, in plain
// C++, read from a weights file the trainer (rl_agent/learner/weights.py)
// writes and renames into place atomically.
//
// File layout, little-endian:
//   "RLCW0001"                    8 bytes
//   version                       uint64, > 0, increasing with every push
//   payload length                uint64
//   checksum                      uint64, FNV-1a 64 of the payload
//   payload:
//     n_models                    uint32
//     per model:
//       agent                     uint32 (0 l0, 1 interior, 2 last)
//       level                     int32 (-1: every level of the agent)
//       features hash             uint64, FNV-1a 64 of the agent's feature
//                                 names joined by ','
//       n_in                      uint32 (= the agent's feature count)
//       clip                      float64 (> 0)
//       n_layers                  uint32 (>= 1)
//       per layer: out, in (uint32), weights out*in row-major, biases out
//                                 (float64); ReLU between layers, none after
//                                 the last, whose first 4 outputs are f's
//                                 per action (further outputs are the
//                                 trainer's, such as a value head)
//       has_delta                 uint32 (0 or 1); then a 4 x n_in linear
//                                 head and 4 biases, delta_j (pooled levels)
//
// Inputs: a NaN state value (not measured yet) becomes 0, and every value is
// clipped to [-clip, clip], in Python and C++ alike (ARCH-6).
#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <vector>

#include "actions.h"
#include "state.h"

namespace rlc {

uint64_t Fnv1a64(const void* data, size_t size);
// FNV-1a 64 of the agent's feature names joined by ','.
uint64_t FeaturesHash(Agent agent);

struct Layer {
  uint32_t out = 0, in = 0;
  std::vector<double> w;  // out * in, row-major
  std::vector<double> b;  // out
};

struct Model {
  Agent agent = Agent::kInterior;
  int level = -1;
  uint64_t features_hash = 0;
  uint32_t n_in = 0;
  double clip = 0;
  std::vector<Layer> layers;
  bool has_delta = false;
  std::vector<double> delta_w, delta_b;  // 4 * n_in, 4
};

struct Weights {
  uint64_t version = 0;
  std::vector<Model> models;
  // The model for (agent, level): an exact level first, then level -1.
  const Model* Find(Agent agent, int level) const;
};

enum class LoadStatus { kOk, kMissing, kInvalid };

// Reads only the header: the file's version, or kMissing / kInvalid.
LoadStatus PeekVersion(const std::string& path, uint64_t* version,
                       std::string* error);
// Reads and checks the whole file: magic, length, checksum, layout, and each
// model's feature hash and input count against this build's FeatureNames.
LoadStatus LoadWeights(const std::string& path, Weights* out,
                       std::string* error);

// f_theta(s) + delta_j(s) per action.
std::array<double, kNumActions> Forward(const Model& model,
                                        const std::vector<double>& state);

}  // namespace rlc
