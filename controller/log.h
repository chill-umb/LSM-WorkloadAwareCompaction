// The decision and transition logs (A-Impl-8, H §6; plan §3): one JSON
// object per line, flushed as written. rl_agent reads them
// (rl_agent/tests/test_log_format.py parses the golden file log_test.cc
// writes).
//
// decision    one per level decision: requested and effective values, the
//             action, its reason, the mask and prior_cost for every action
// transition  one per closed interval of a level: state, mask, prior_cost,
//             action, cost parts, next state and mask, Delta N, and whether
//             the interval may be replayed
// prior_cost is the prior b of H §7 in cost units: lower is better (prior.h).
//
// The plugin also writes these to the decision log:
//   start       the config's bounds and the values in effect
//   flush_size  F, the first flush after the start, and C_0 = K0_cfg F
//   apply       one SetOptions call: the vector, K0, ok, and first_id..last_id
//               of the decisions it carries; after an accepted call, whether
//               the snapshot read next already carried those values (seen)
//               and its generation (ACT-3)
//   apply_held  a new change held back by the SetOptions cap
//   repair      a level raised so targets do not shrink going down
//   drain, fallback, stop
//
// The SetOptions cap (setoptions_min_interval_ms). A decision's "effective"
// is the value in effect after its poll; a change held by the cap reaches
// RocksDB at a later poll, in the apply line whose first_id..last_id covers
// it. While a change is held, the state, score and queue position the
// plugin logs describe its target values, not the values RocksDB is using
// (ACT-3 and the trainer must join decisions to apply lines). A held change
// overwritten by a later decision before any apply never reaches RocksDB:
// its decision line keeps requested != effective, and its transition is
// still logged.
//
// Schema 2 (2026-10-01): "b" renamed "prior_cost"; interior state gains
// "burst_absent"; cost parts gain "held_byte_ops"; flush_size and apply_held
// lines are new.
// Schema 4 (2026-10-04, D-23/D-24, plan step 10): cost parts gain the job
// counts by kind, read_bytes, the hidden steps and their slot moves, the
// global foreground steps, memtable_hidden, k0_ops and L0's raw reads; every
// state gains H §2's cost-model-2 inputs (state.h); a transition gains
// next_prior_cost, the divisor C and turnover N of its level, its value
// before and after the action, and the neighbours' states, masks and
// prior_cost at the decision (H §3's neighbour charge); a decision gains its
// weights version and, in the learner modes, q; and the transition log gains
// one "job" line per completed job (H §6, ARCH-7, ARCH-8), written before
// the transition that closes its interval.
#pragma once

#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

#include "attribution.h"
#include "masks.h"
#include "prior.h"
#include "state.h"

namespace rlc {

constexpr int kLogSchema = 4;

// The shortest decimal that reads back as the same double; null if not
// finite.
std::string JsonNumber(double value);
std::string JsonString(const std::string& text);

class JsonLine {
 public:
  explicit JsonLine(const char* type);
  JsonLine& Num(const char* key, double value);
  JsonLine& Uint(const char* key, uint64_t value);
  JsonLine& Str(const char* key, const std::string& value);
  JsonLine& Null(const char* key);
  JsonLine& Nums(const char* key, const std::vector<double>& values);
  JsonLine& NumsOrNull(const char* key, const std::vector<double>& values);
  JsonLine& Flags(const char* key, const Mask& mask);
  // {"name": value, ...}; null when values is empty.
  JsonLine& Features(const char* key, const std::vector<std::string>& names,
                     const std::vector<double>& values);
  JsonLine& Parts(const char* key, const LevelParts& parts);
  std::string str() const { return s_ + "}"; }

 private:
  void Key(const char* key);
  std::string s_;
};

struct DecisionRecord {
  uint64_t id = 0;
  int level = 0;
  Agent agent = Agent::kInterior;
  uint64_t op = 0;
  uint64_t t_us = 0;
  std::string mode;
  Action action = Action::kHold;
  std::string reason;
  Mask mask{};
  Values b{};
  // m_i, or K_0 at L0: before, after the action, and as applied.
  double old_value = 0, requested = 0, effective = 0;
  // After the action: anchor and timing, or at L0 anchor and offset.
  double anchor = 0, timing = 0;
  // The weights version the decision used (0: none, Q = -b); and Q for
  // every action in the learner modes (empty otherwise: null).
  uint64_t weights = 0;
  std::vector<double> q;
};
std::string DecisionLine(const DecisionRecord& r);

// A neighbour's view at a level's decision (H §3): its agent, state, mask
// and prior_cost now. Empty state: no neighbour (null).
struct NeighbourView {
  Agent agent = Agent::kInterior;
  std::vector<double> state;
  Mask mask{};
  Values b{};
  double c_bytes = kNaN;  // its divisor C_j (L0: C_0 = K0_cfg F)
};

struct TransitionRecord {
  int level = 0;
  // The decision that opened the interval; 0 for the interval opened when
  // the controller started, which has no state or action.
  uint64_t id = 0;
  uint64_t start_op = 0, end_op = 0;
  Agent agent = Agent::kInterior;
  std::vector<double> state;  // empty: null
  Mask mask{};
  Values b{};
  bool has_action = false;
  Action action = Action::kHold;
  LevelParts parts;
  // g_i = (1 - rho~_i) B_i, the shadowed garbage (D §4), at the close.
  double b_bytes = kNaN, rho_tilde = kNaN;
  Agent next_agent = Agent::kInterior;
  std::vector<double> next_state;  // empty: null (closed without a decision)
  Mask next_mask{};
  Values next_b{};
  // The level's divisor C_i (L0: C_0 = K0_cfg F) and operations per
  // turnover N_i at the close; m_i (L0: K_0) before and after the action
  // that opened the interval; and the neighbours above and below at it.
  double c_bytes = kNaN, n_ops = kNaN;
  double value_before = kNaN, value_after = kNaN;
  NeighbourView up, down;
  bool valid = true;
  std::string invalid;  // why not, when not valid
};
std::string TransitionLine(const TransitionRecord& r);

// One completed job (H §6): charged at its completion to `level`'s open
// interval `interval` (a flush: L0, its interference to the write-path
// bucket), with its window's step counts by type, unpriced; the trainer and
// the evaluator price them alike.
struct JobLineRecord {
  int level = 0;
  uint64_t interval = 0;
  int kind = 0;
  int job_id = 0;
  int start_level = -1, output_level = 0;
  double s = 0, o = 0, x = 0;
  bool has_begin = false;
  uint64_t n_begin = 0, n_end = 0;
  uint64_t win_start = 0;
  double win_ops = 0;
  bool win_own = false;
  std::vector<double> win_counts;  // kNumStepTypes, cost_model_v2's order
  // Decision points of the charged level strictly inside (n_begin, n_end)
  // (ARCH-8); 0 without a begin record.
  uint64_t decision_points = 0;
};
std::string JobLine(const JobLineRecord& r);

class LogFile {
 public:
  LogFile() = default;
  ~LogFile();
  LogFile(const LogFile&) = delete;
  LogFile& operator=(const LogFile&) = delete;

  // Creates or truncates `path`.
  bool Open(const std::string& path, std::string* error);
  // Appends the line and a newline, and flushes. A no-op when not open.
  void Write(const std::string& line);
  bool failed() const { return failed_; }
  bool open() const { return file_ != nullptr; }

 private:
  FILE* file_ = nullptr;
  bool failed_ = false;
};

}  // namespace rlc
