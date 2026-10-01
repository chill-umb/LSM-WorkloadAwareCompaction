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

enum class Mode { kHoldOnly, kRules };

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
  // Prices (D §1): per byte written, filter probe, block-reading probe and
  // run seek, per byte held per second; and the reference operation rate.
  double c_w = 0, c_f = 0, c_blk = 0, c_sk = 0, c_s = 0;
  double q_bar = 0;
  std::string decision_log;
  std::string transition_log;

  bool HasRule(const std::string& name) const;
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
