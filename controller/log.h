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

constexpr int kLogSchema = 2;

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
};
std::string DecisionLine(const DecisionRecord& r);

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
  bool valid = true;
  std::string invalid;  // why not, when not valid
};
std::string TransitionLine(const TransitionRecord& r);

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
