// The plugin's configuration: one flat JSON object, read at create time.
// Every value is required (the rule thresholds only when their rule is on);
// there are no defaults. A missing, invalid or unknown value is an error,
// and the plugin then runs in fallback (A-Impl-8).
#pragma once

#include <cstdint>
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
// §2's exploratory track; remote-inference (Gate N6) is not built. turn is
// the turn learner (turn.h; ~/latex/2026-11-07-rl-design).
enum class Mode { kHoldOnly, kRules, kPriorOnly, kLearned, kLearned2, kTurn };

// The turn mode's settings (turn.h), from the config's "turn_*" keys; every
// one is required in mode "turn" and refused in any other.
struct TurnConfig {
  double turn_ops = 0;      // operations per turn, any kind
  int acting = 0;           // acting levels, L0 included (n + 1 in the design)
  int arch = 0;             // 0: factored (one agent), 1: VDN (one per level)
  double kappa = 0;         // anchor decay constant, in turns
  // The decay's feature flag (turn_decay; DECAY, set per run): on, anchors
  // decay toward native (DecayAnchors); off, they stay where the agent's
  // presses left them (DecayStep).
  bool decay = true;
  // The L0 prior's feature flag (turn_prior; PRIOR, set per run): on, L0's
  // greedy choice is the net's score minus the October L0 formula's change
  // of one turn's cost (L0TurnPrior, over the forecast window), in the
  // reward's units (the weights file's spread); the trainer learns the net
  // as that correction. Off, the net's scores alone.
  bool prior = false;
  // The L0 bill's feature flag (turn_l0_bill; L0_BILL, set per run): on,
  // every turn line also carries L0's raw read counts over the turn it
  // closes ("l0_read"), which the trainer prices as L0's own bill. Off, no
  // such block: the line is as before.
  bool l0_bill = false;
  // The compact floor's feature flag (turn_compact_floor; COMPACT_FLOOR, set
  // per run): on, L0's mask blocks compact while L0's anchor rounds to the
  // lowest K rung (compact there sends K = max(1, n0), below the ladder,
  // with no rung change). Off, compact is allowed whenever L0 has a file.
  bool compact_floor = false;
  // The compact step's feature flag (turn_compact_step; optional, 0 or 1;
  // absent: 0, as before). On, L0's compact only steps
  // the K anchor one rung down: it sends no one-turn K = max(1, n0), so
  // RocksDB merges by the new rung's trigger and K never leaves the ladder.
  // L0's compact is then allowed whenever its anchor is above the lowest
  // rung, with or without a file in L0 (as expand needs none). Off, compact
  // also merges at once (K = max(1, n0) for the turn) and needs a file.
  // Levels below L0 are unchanged.
  bool compact_step = false;
  // The level clock (turn_periods; optional, ":"-separated, one whole number
  // per acting level; absent: empty, every level decides every turn, as
  // before). Level i decides only at the turns t with t % periods[i] ==
  // LevelPhase(periods[i]); at every other turn its mask is hold alone, so
  // it keeps its setting for the whole period (a hold). 0: the level never
  // decides. A level whose period is not 1 is "slow": it has no defer (a
  // one-turn override cannot be judged over a hold), and it explores by
  // itself at its decision turns, linear from explore_start to explore_end
  // over its first slow_explore_decisions decisions
  // (turn_slow_explore_decisions, required with turn_periods). L0's period
  // is 1. Needs turn_decay 0, so that hold keeps the setting.
  std::vector<int> periods;
  int slow_explore_decisions = 0;
  double margin = 0;        // epsilon of the encoding
  double sigma_max = 0;     // compact's score cap (4/3)
  double glide_share = 0;   // R_i's share of the slot's merge bytes (1/2)
  int forecast_turns = 0;   // turns behind the rate forecasts (8)
  double guard_owed_bytes = 0;     // no anchor drop above this owed work
  int guard_l0_flushes = 0;        // ... or within this many flushes of slowdown
  int safety_l0_flushes = 0;       // no defer/expand within this many
  double safety_pending_bytes = 0; // no deep defer/expand above this
  int k_defer_max = 0;      // defer's highest L0 trigger (18)
  double renew_band = 0;    // see Press
  int max_topups = 0;       // defer raises within a turn (2)
  double explore_start = 0, explore_end = 0;
  double explore_turns = 0;  // linear from start to end over this many turns
  std::vector<double> k_rungs;  // ascending, whole files, K_cfg among them
  std::vector<double> m_rungs;  // ascending, 1 among them, inside [0.5, 2]
};

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

// One scheduled change of hold-only: at the first poll at or after at_op,
// apply m (one value per level, L0's 1) and k0 (0: keep the L0 trigger in
// effect).
struct ScheduledChange {
  uint64_t at_op = 0;
  std::vector<double> m;
  int k0 = 0;
};

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
  // learned2 (learned2.h; owner, 2026-11-05): a "window" line every
  // window_ops operations; an ensemble of `ensemble` models per level, a
  // greedy move only with vote_share of them behind it; after a change, the
  // agent's level (L1 for L0) must take settle_turnovers of its capacity in
  // inflow before the agent decides again, and every decision needs
  // measure_turnovers of inflow and min_decision_ops operations since the
  // last one.
  double window_ops = 0;
  int ensemble = 0;
  double vote_share = 0;
  double settle_turnovers = 0;
  double measure_turnovers = 0;
  double min_decision_ops = 0;
  // learned2's noise fix (owner, 2026-11-05: "fix the noise issue. Go with
  // merge cycle aligned windows"; all optional, the defaults keep the
  // fixed windows above):
  //   window_cycles > 0: a window closes at the first poll after that many
  //     non-trivial L0->L1 merges have ended in it, once it holds
  //     window_min_ops operations, or at window_ops operations, whichever
  //     comes first; every decision is then made at a window boundary, so a
  //     change there leaves no window half under each setting;
  //   min_effect: a move also needs its mean predicted cost (log J per
  //     operation) at least this far below hold's;
  //   deep_frozen: the level agents (L1 and down) only hold;
  //   k_trials: the L0 agent alternates K between its incumbent and one
  //     neighbour in blocks of at least trial_block_ops operations; from
  //     trial_min_blocks blocks on, at each block's end, the ensemble judges
  //     the pair (F2TrialVerdict over the last trial_recent windows' inputs)
  //     and the agent adopts the neighbour, rejects it, or after
  //     trial_max_blocks gives up; once both neighbours have failed since
  //     the last adoption it holds for trial_rest_ops. Needs window_cycles.
  int window_cycles = 0;
  double window_min_ops = 0;
  double min_effect = 0;
  bool deep_frozen = false;
  bool k_trials = false;
  double trial_block_ops = 0;
  int trial_min_blocks = 0;
  int trial_max_blocks = 0;
  double trial_rest_ops = 0;
  int trial_recent = 0;
  // learned2's input set (after the input study
  // ~/node_ops/reports/2026-11-06-inputstudy.md; optional, absent = 1):
  // 1 = the 12 inputs of F2FeatureNames before; 2 = those 12 and six of the
  // tree's state (learned2.h kF2Up0Pending..kF2Stall).
  int f2_inputs = 1;
  // Turn mode (turn.h). It also needs seed, weights_path, push_interval_ms
  // (the least time between two checks of the weights file, which happen
  // at turn boundaries only) and weights_required; explore is refused (the
  // turn's own rate, turn_explore_*, replaces it). cost_model must be 2 and
  // m_min, m_max the fork's 0.5 and 2: compact and defer use the whole range.
  TurnConfig turn;
  // Hold-only only (owner, 2026-10-07: the step-response test of the
  // settling condition C3): at the first poll at or after operation
  // apply_at_op, apply apply_m (one value per level, ":"-separated in the
  // config, L0's 1) and apply_k0 (0: keep the L0 trigger in effect) once,
  // then hold them as hold-only holds any values. 0: never (ARCH-5's arm).
  uint64_t apply_at_op = 0;
  std::vector<double> apply_m;
  int apply_k0 = 0;
  // Hold-only only (owner, 2026-10-21: the K-step batch's multi-change
  // runs): apply_schedule, ";"-separated "op|m|k0" entries with increasing
  // ops, each applied once at the first poll at or after its op. The
  // apply_at_op form above is a schedule of one; the two are exclusive.
  std::vector<ScheduledChange> schedule;
  // The per-level read log's flag (level_reads; LEVEL_READS, set per run;
  // owner, 2026-11-10), optional in every mode (absent: 0) and 1 only in
  // hold-only and turn. On, every turn line carries "level_reads", each level's eight read
  // counters over the turn it closes; hold-only writes a "level_reads" line
  // per level_reads_ops operations from its start (cut as turns are, at the
  // first poll past each boundary) and a last, shorter one at the drain.
  // Logging only: no decision reads it. level_reads_ops is required in
  // hold-only with level_reads 1 and refused anywhere else.
  bool level_reads = false;
  uint64_t level_reads_ops = 0;
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
// slowdown trigger (A-Impl-6). In mode "turn" the K rungs must also hold the
// configured trigger, and the top rung and turn_k_defer_max stay below the
// slowdown trigger.
bool CheckConfigAgainstHost(const Config& config, int l0_trigger,
                            int l0_slowdown_trigger, std::string* error);

}  // namespace rlc
