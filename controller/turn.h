// The turn learner's pure parts (owner, 2026-11-07; the design is
// ~/latex/2026-11-07-rl-design/rl-design.tex): the operation clock's turns,
// capacity anchors on rung ladders and their decay, the four actions'
// encoding with the glide and the guards, the masks, the state inputs,
// RocksDB's owed-work estimate split by level, and the Q networks of both
// arms (the factored one and VDN). No host calls: the plugin (plugin.cc)
// fills a TurnTree at each turn boundary and applies what Encode returns.
//
// Units. A level's anchor is log2 of its capacity over native: log2(m_i) for
// level i >= 1, log2(K / K_cfg) for L0. Rungs are values (K files, or m).
// Levels are RocksDB's, 0..num_levels-1; "acting" levels are 0..acting-1,
// minus the deepest level with bytes, which never acts.
#pragma once

#include <array>
#include <cstdint>
#include <random>
#include <string>
#include <vector>

#include "actions.h"

namespace rlc {

// The turn mode's settings, TurnConfig, are in config.h (every one is
// required in mode "turn").

// --- Rungs and anchors -----------------------------------------------------

// A ladder in anchor units: log2(rung / native), ascending.
struct Ladder {
  std::vector<double> rungs;  // values
  std::vector<double> logs;   // log2(value / native)
  int native = 0;             // index of the native rung
  double native_value = 1;
};
// Fails (returns false) unless the rungs ascend, are positive and include
// `native_value`.
bool MakeLadder(const std::vector<double>& rungs, double native_value,
                Ladder* out, std::string* error);
// The rung an anchor rounds to: nearest in log2; a tie goes toward native.
int RungIndex(const Ladder& ladder, double anchor);

struct AnchorState {
  double a = 0;        // log2 of capacity over native
  // Level >= 1 only. The glide's target while a downward move releases its
  // backlog (0: no glide). A new drop waits until it is 0 (one drop at a
  // time).
  double glide_m = 0;
  // The target the anchor and glide gave last turn (0: none yet), so a
  // downward step of the decay is glided like a press.
  double last_m = 0;
};

// One turn of decay toward native, a <- a exp(-1/kappa); |a| < 1e-9 snaps to 0.
double Decay(double a, double kappa);

struct TurnTree;
// One turn of decay for every anchor, done first at each turn boundary. A
// step that lowers a target (an anchor above native crossing to a lower rung)
// goes through the guards: it waits this turn while RocksDB's pending
// estimate now passes guard_owed_bytes, or, below L0, while L0 is within
// guard_l0_flushes of its slowdown or the level's previous drop is still
// gliding. (Compact's drop tests the owed work at the new anchor instead;
// for the decay that could hold a deep level above native for good.) The
// step itself is then glided by Encode. A step that raises a target (an
// anchor below native) never waits: it makes no merge due. Returns per level
// whether the decay waited.
std::vector<bool> DecayAnchors(const TurnTree& t, const TurnConfig& cfg,
                               const Ladder& k_ladder, const Ladder& m_ladder,
                               std::vector<AnchorState>* anchors);
// The anchors' rule at each turn boundary, behind the decay's feature flag
// (turn_decay; DECAY, set per run). decay true: the decay
// rule, DecayAnchors. decay false: every anchor stays where the agent's
// presses left it, so hold keeps the setting and a level moves only when the
// agent presses it. Returns per level whether the decay waited (never, with
// the decay off).
std::vector<bool> DecayStep(bool decay, const TurnTree& t,
                            const TurnConfig& cfg, const Ladder& k_ladder,
                            const Ladder& m_ladder,
                            std::vector<AnchorState>* anchors);

// A press "up" (expand) or "down" (compact's anchor part) on an anchor.
// Renew: when the anchor rounds to a rung r on the pressed side of native and
// has decayed from r by more than renew_band of the way to the boundary with
// the rung toward native, it is reset to r: the rung, and so the setting,
// does not change. Otherwise the anchor moves to the next rung in the
// direction pressed (at the end of the ladder it is reset to that end).
// Returns the new anchor; *renew says which happened.
double Press(const Ladder& ladder, double a, bool up, double renew_band,
             bool* renew);

// --- The tree at a turn boundary -----------------------------------------

// What the plugin measured, in raw units. Vectors have num_levels entries.
struct TurnTree {
  int num_levels = 0;
  double T = 0;               // the size ratio
  std::vector<double> C;      // nominal targets C_i; C[0] = 0
  std::vector<double> B;      // bytes not being compacted
  std::vector<double> B_all;  // all bytes (owed work counts them all)
  std::vector<double> score;  // RocksDB's scores as last computed
  std::vector<double> m_applied;  // multipliers in effect; [0] = 1
  std::vector<double> file_bytes; // target output file size at each level
  int n0 = 0;                 // L0 files not being compacted
  int n0_all = 0;             // all L0 files
  int K_applied = 0;          // the trigger in effect
  int K_cfg = 0;              // the configured trigger (native)
  int K_slow = 0;             // L0 slowdown trigger
  int k_live_cap = 0;         // ceil(C_1 / F); 0 unknown
  int last = 0;               // the deepest level with bytes; 0 if none
  double pending = 0;         // RocksDB's pending compaction bytes
  double soft_limit = 0;      // soft_pending_compaction_bytes_limit
  // Forecasts for the coming turn from the last forecast_turns turns.
  double f_hat = 0;            // flushes expected
  std::vector<double> I_hat;   // net bytes expected to land, per level
  double X_bar = 0;            // slot's merge input bytes per turn (S + O)
  // The turn just closed, for the state.
  double put_share = 0, get_share = 0, scan_share = 0;
  double stall_share = 0;      // share of polls with writes slowed or stopped
  double busy_share = 0;       // share of polls with a merge running
  std::vector<double> probes_per_get;  // per level
};

// Level i acts this turn: 0 <= i < acting, it exists, and it is not the
// deepest level with bytes (L0 acts whenever there is a level below it).
bool Acting(const TurnTree& t, int acting, int level);

// RocksDB's EstimateCompactionBytesNeeded (version_set.cc:3690), split by
// the level whose merge each part is, at multipliers `m` (num_levels, [0] = 1)
// and L0 trigger K: d[0] = B_0 + B_1 when L0 is due; d[i] = excess_i
// (1 + B_{i+1} / size_i) when level i's size (its bytes plus the excess
// carried from above) passes m_i C_i and the level below has bytes. The sum
// is RocksDB's estimate at those settings.
std::vector<double> OwedWork(const TurnTree& t, const std::vector<double>& m,
                             int K);

// R_i: the bytes level i may release per turn when its target drops,
// glide_share X_bar / (1 + B_{i+1} / B_i), but at least one output file of
// the level below.
double GlideBudget(const TurnTree& t, const TurnConfig& cfg, int level);

// --- Masks and encoding -----------------------------------------------------

using TurnMask = std::array<bool, kNumActions>;

// What a level's anchor and rung give before this turn's actions.
struct TurnSettings {
  std::vector<double> m;  // num_levels, [0] = 1: rungs, glides, repairs
  int K = 0;
};
// The anchors rounded to rungs, K within the live cap, and the fork's order
// m_{i+1} T >= m_i repaired downward by raising deeper targets (raising a
// target never makes merge work due). The owed work of the shaping is
// measured at these settings.
TurnSettings RungSettings(const TurnTree& t, const Ladder& k_ladder,
                          const Ladder& m_ladder,
                          const std::vector<AnchorState>& anchors);
// L0's trigger at its anchor now and after an expand or a compact press, as
// Encode moves the anchor (Press) and RungSettings rounds it (rung, live
// cap); hold and defer keep the rung (their one-turn overrides aside). The
// L0 prior (turn_prior) prices these three.
struct L0Rungs {
  int now = 0, up = 0, down = 0;
};
L0Rungs L0RungMoves(const TurnTree& t, const Ladder& k_ladder, double anchor,
                    double renew_band);
// One turn of the glide at level i >= 1 toward the rung target m_rung: while
// m_rung is below last turn's target, the target is the lower of last turn's
// target and the fill, less R_i / C_i (never below m_rung). Each turn the
// level so releases its inflow, as native would, plus at most R_i of
// backlog; the target never rises, and the glide ends in about
// backlog / R_i turns whatever the inflow. Updates the anchor's glide_m and
// last_m; returns the level's target.
double GlideStep(const TurnTree& t, const TurnConfig& cfg, int level,
                 double m_rung, AnchorState* anchor);
// What holding everywhere would send: RungSettings with this turn's glide
// steps (on a copy of the anchors). Masks and the state use it.
TurnSettings AnchorSettings(const TurnTree& t, const TurnConfig& cfg,
                            const Ladder& k_ladder, const Ladder& m_ladder,
                            const std::vector<AnchorState>& anchors);

// Allowed actions per level (num_levels entries; non-acting levels hold
// only), at the anchor settings `base`.
std::vector<TurnMask> Masks(const TurnTree& t, const TurnConfig& cfg,
                            const Ladder& k_ladder, const Ladder& m_ladder,
                            const std::vector<AnchorState>& anchors,
                            const TurnSettings& base);

// What Encode did at one level.
struct TurnNote {
  bool renewed = false;
  bool moved = false;       // the anchor changed rung
  bool guard_blocked = false;  // compact's anchor drop blocked
  bool overridden = false;  // a one-turn override (compact-now or defer)
};

// One turn's encoding: applies each acting level's action to the anchors
// (already decayed for this turn) and returns the settings to send, with
// compact-now and defer overrides for this turn and every downward target
// through the glide. `actions` has num_levels entries (hold elsewhere).
// Updates anchors (their glide state too) and fills `notes`.
TurnSettings Encode(const TurnTree& t, const TurnConfig& cfg,
                    const Ladder& k_ladder, const Ladder& m_ladder,
                    const std::vector<Action>& actions,
                    std::vector<AnchorState>* anchors,
                    std::vector<TurnNote>* notes);

// A defer top-up inside a turn: the deferred level's new override if it is
// within margin / 2 of due, else the current one. For L0, k0 files now and
// the flushes still expected; for level i, B_i now and the inflow still
// expected. Returns false (and leaves the value) when the raise would leave
// the bounds: the defer is then broken and RocksDB compacts.
bool TopUpL0(int n0, double flushes_left, int cap, int k_defer_max, int* K);
bool TopUpLevel(double B, double C, double inflow_left, double margin,
                double m_below_T, double* m);

// --- State ------------------------------------------------------------------

// The state inputs, in this order: global (6), L0 (5), then levels
// 1..acting-1 (6 each); a level that does not exist or does not act is
// zeros. FeatureNames gives the names; TurnInputs the raw values.
std::vector<std::string> TurnFeatureNames(int acting);
// `base` is AnchorSettings (what holding sends), `rungs` RungSettings, and
// `owed` OwedWork at `rungs`. A level's backlog input is base.m - rungs.m:
// the share of C_i its glide has still to release.
std::vector<double> TurnInputs(const TurnTree& t, const TurnConfig& cfg,
                               const std::vector<AnchorState>& anchors,
                               const TurnSettings& base,
                               const TurnSettings& rungs,
                               const std::vector<double>& owed);
// Index of level `level`'s block in the inputs (L0: 6; level i: 11 + 6(i-1)).
int TurnBlockStart(int level);
int TurnBlockSize(int level);

// --- Networks ---------------------------------------------------------------

// The weights file the trainer writes (rl_agent/learner/turn_weights.py),
// little-endian:
//   "RLTW0002" (8 bytes), version uint64 > 0, payload length uint64,
//   checksum uint64 (FNV-1a 64 of the payload)
//   payload:
//     arch uint32 (0 factored, 1 vdn), acting uint32, n_in uint32,
//     features hash uint64 (FNV-1a 64 of TurnFeatureNames joined by ','),
//     clip float64 > 0, spread float64 > 0 (the reward's frozen spread: J
//     per reward unit, which the L0 prior divides by), mean float64[n_in],
//     scale float64[n_in] (> 0)
//     n_nets uint32 (factored 1, vdn acting)
//     per net: n_view uint32, view int32[n_view] (input index, -1 = zero),
//              n_layers uint32 >= 1, per layer out, in (uint32), weights
//              out*in row-major, biases out (float64); ReLU between layers
//   Factored: one net over every input, last layer 1 + 4 acting outputs (V,
//   then four advantages per acting level). VDN: one net per acting level,
//   four outputs each.
// Inputs are scaled (x - mean) / scale, then clipped to [-clip, clip].
struct TurnNet {
  struct Layer {
    uint32_t out = 0, in = 0;
    std::vector<double> w, b;
  };
  struct Net {
    std::vector<int32_t> view;
    std::vector<Layer> layers;
  };
  uint64_t version = 0;
  int arch = 0;
  int acting = 0;
  uint32_t n_in = 0;
  double clip = 0;
  double spread = 1;
  std::vector<double> mean, scale;
  std::vector<Net> nets;
};

enum class TurnLoad { kOk, kMissing, kInvalid };
TurnLoad PeekTurnVersion(const std::string& path, uint64_t* version,
                         std::string* error);
TurnLoad LoadTurnNet(const std::string& path, int arch, int acting,
                     TurnNet* out, std::string* error);
// Serialises a net (tests, and the C++/Python agreement check).
std::string SerializeTurnNet(const TurnNet& net);

// Four scores per acting level: the factored arm's advantages, or each VDN
// agent's values. Every score is 0 without a net (cold start: all tie).
std::vector<std::array<double, kNumActions>> TurnScores(
    const TurnNet* net, const std::vector<double>& inputs, int acting);

// The greedy action per level among its allowed ones; a tie goes to the
// lowest index, so hold (0) wins every tie it is in.
std::vector<Action> Greedy(
    const std::vector<std::array<double, kNumActions>>& scores,
    const std::vector<TurnMask>& masks);

// Exploration inside the policy: with probability eps, one level drawn
// uniformly from those with more than one allowed action takes an allowed
// action drawn uniformly. Returns that level, or -1.
int Explore(double eps, const std::vector<TurnMask>& masks,
            std::vector<Action>* actions, std::mt19937_64* rng);
// eps at turn t: linear from explore_start to explore_end over explore_turns.
double ExploreRate(const TurnConfig& cfg, double turn);

}  // namespace rlc
