// The plugin's configuration: one flat JSON object, read at create time.
// Every value is required (the rule thresholds only when their rule is on);
// there are no defaults. A missing, invalid or unknown value is an error,
// and the plugin then runs in fallback (A-Impl-8).
#pragma once

#include <map>
#include <string>
#include <vector>

namespace rlc {

// Pathway A §2, A-Impl-6 and A-Impl-7; fixed in advance (PREREGISTRATION).
struct Bounds {
  double m_min = 0;
  double m_max = 0;
  int k0_min = 0;
  int k0_cap = 0;
  double epsilon = 0;
  double phi_min = 0;
  double alpha = 0;
  double kappa_d = 0;  // timing factor relaxes over kappa_d turnovers
  double kappa_a = 0;  // anchors decay over kappa_a turnovers
};

// learned and prior-only are plan step 10, built for PREREGISTRATION D-24
// §2's exploratory track; remote-inference (Gate N6) is not built.
enum class Mode { kHoldOnly, kRules, kPriorOnly, kLearned };

// Cost model 2's step types and job kinds (PATHWAYS D §1 as amended
// 2026-10-03, D-23; scripts/dbbench_pipeline/cost_model_v2.py's STEP_TYPES
// and KINDS, in the same order). The read types come first; "put" is the
// one write type.
constexpr int kNumStepTypes = 10;
constexpr int kNumReadTypes = 9;
enum StepType {
  kProbe = 0, kBlock, kSeek, kReopen, kStep, kIBlock, kMemtable, kGet0,
  kScan0, kPut
};
extern const char* const kStepTypeNames[kNumStepTypes];
constexpr int kNumJobKinds = 4;
enum JobKind { kFlush = 0, kL0Merge, kDeepMerge, kMove };
extern const char* const kJobKindNames[kNumJobKinds];

// Cost model 2's prices beyond cost model 1's (D §1; the interim interface
// §5.2), in money, from a schema-6 price file. Every one is required when
// cost_model is 2, with no defaults (D-23 §3(d)); c_cr, c_ib and every kappa
// may be 0.
struct CostModel2 {
  double c_cr = 0;                // per compaction byte read, S + O
  double job[kNumJobKinds] = {};  // per job, by kind
  double c_st = 0, c_ib = 0;      // per scan step; per iterator block
  double c_mt = 0, c_get0 = 0, c_sc0 = 0, c_put = 0;
  double p_dev = 0;   // money per core-second (price_per_core_second)
  double lambda = 0;  // Y = X + lambda (S + O)
  // Slowdown per byte/s of job bytes (s/B), and per running job, by kind.
  double kappa_b[kNumStepTypes] = {};
  double kappa_j[kNumStepTypes][kNumJobKinds] = {};
  int n_win = 0;  // operations (D §1's averaging window)
};

// Gate N3's rules (rules.h), by config name.
extern const char* const kRuleNames[5];

// The multiplier bounds the fork's ColumnFamilyData::ValidateOptions
// hard-codes (lib/rocksdb/db/column_family.cc). Config bounds must lie inside.
extern const double kForkMultiplierMin;
extern const double kForkMultiplierMax;

struct Config {
  Bounds bounds;
  Mode mode = Mode::kHoldOnly;
  std::string mode_name;
  std::vector<std::string> rules;  // rules mode: the rules switched on
  double rule_l0_early_read_ratio = 0;
  double rule_release_fill = 0;
  double rule_garbage_drop = 0;
  double k = 0;  // decisions per turnover (G-iv)
  // A-Impl-5's cap: at most one Apply per this many milliseconds. Changes
  // that fall due sooner are merged into the next Apply.
  double setoptions_min_interval_ms = 0;
  double b_max = 0;  // the prior's clip (H §7)
  double beta_w = 0, beta_r = 0, beta_s = 0;
  // Prices (D §1): per byte written, filter probe, block-reading probe, run
  // seek and table reopen (D-21), per byte held per second; and the
  // reference operation rate.
  double c_w = 0, c_f = 0, c_blk = 0, c_sk = 0, c_open = 0, c_s = 0;
  double q_bar = 0;
  // 1: the prices above only. 2: also CostModel2's, which the state, the
  // prior and the per-job records use (H §2, H §7, H §6).
  int cost_model = 1;
  CostModel2 v2;
  // Prior-only and learned modes (H §5): with probability explore a
  // decision takes an allowed action uniformly at random (never a masked
  // one), drawn from a generator seeded with seed.
  double explore = 0;
  int seed = 0;
  // Learned mode (H §6, A-Impl-8): the weights file, polled every
  // push_interval_ms. Absent at the start: Q = -b until it appears (cold
  // start), unless weights_required, which then falls back.
  std::string weights_path;
  double push_interval_ms = 0;
  bool weights_required = false;
  std::string decision_log;
  std::string transition_log;

  bool HasRule(const std::string& name) const;
  // The quiet price of one step of type x (D §1): c_f, c_blk, c_sk, c_open
  // and cost model 2's; 0 for cost model 2's types under cost model 1.
  double BasePrice(int type) const;
  // The job price of the kind: 0 under cost model 1.
  double JobPrice(int kind) const;
};

struct JsonValue {
  bool is_string = false;
  std::string text;
  double number = 0;
};

// A flat JSON object whose values are strings or finite numbers. Rejects
// anything else: nesting, true/false/null, duplicate keys, trailing text.
bool ParseFlatJson(const std::string& text,
                   std::map<std::string, JsonValue>* out, std::string* error);

// The log paths are read first, so a config that fails later still names
// where its fallback is logged.
bool ParseConfig(const std::string& text, Config* config, std::string* error);
bool LoadConfig(const std::string& path, Config* config, std::string* error);

// The configured trigger must lie in [k0_min, k0_cap], and k0_cap below the
// slowdown trigger (A-Impl-6).
bool CheckConfigAgainstHost(const Config& config, int l0_trigger,
                            int l0_slowdown_trigger, std::string* error);

}  // namespace rlc
