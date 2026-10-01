// The normalised state (PATHWAYS G §3 for interior levels, H §2 for L0 and
// the last level). The only place state is computed; every decision logs it.
// A value that cannot be measured yet is NaN (logged as null).
#pragma once

#include <cstdint>
#include <deque>
#include <limits>
#include <map>
#include <string>
#include <utility>
#include <vector>

#include "actions.h"
#include "attribution.h"
#include "config.h"
#include "rocksdb/rl_controller_host.h"

namespace rlc {

using ROCKSDB_NAMESPACE::RLHostOptions;
using ROCKSDB_NAMESPACE::RLJobRecord;
using ROCKSDB_NAMESPACE::RLLevelReadCounts;
using ROCKSDB_NAMESPACE::RLOpCounts;
using ROCKSDB_NAMESPACE::RLTreeSnapshot;

constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();

// a / b, or NaN unless b > 0.
double Ratio(double a, double b);

// Maps steady-clock times to operation counts by linear interpolation
// between the samples taken at every poll. Trim keeps at least the last
// minute, and back to `keep_from_micros` (the oldest due-since a slot wait may
// still start from), so a long wait is not clamped.
class OpClock {
 public:
  void Add(uint64_t t_micros, uint64_t op);
  void Trim(uint64_t now_micros, uint64_t keep_from_micros);
  double OpAt(uint64_t t_micros) const;
  size_t size() const { return samples_.size(); }

 private:
  std::deque<std::pair<uint64_t, uint64_t>> samples_;
};

// One level's totals since the controller started (n_w), from job records.
struct LevelStats {
  // Merges sourced here (trivial moves, failures and jobs whose output is
  // their own level excluded), and trivial moves sourced here.
  double merged_s = 0, merged_o = 0, merged_x = 0;
  double trivial_s = 0;
  double inflow_bytes = 0;       // net bytes landing here (L0: flushed bytes)
  double job_ops = 0, jobs = 0;  // operations served during those merges
  bool released = false;
  uint64_t last_release_op = 0;
  uint64_t last_end_micros = 0;
  // Mean slot wait per release, in operations, over the last interval with
  // a release (G §3 carries it forward).
  double wait_carry = kNaN;
};

class Stats {
 public:
  void Reset(int num_levels, const RLOpCounts& ops,
             const std::vector<RLLevelReadCounts>& reads);
  // Updates the totals and attributes the job to the open intervals. A job's
  // slot wait is measured when it starts and attributed when it ends, to the
  // interval of its release (G §3).
  void OnJob(const RLJobRecord& job, const OpClock& clock,
             std::vector<LevelParts>* parts);
  // Start level of the latest started job not yet ended; -1 if none. Until
  // the first compaction record arrives, `snapshot_level` (a job may have
  // started before the controller). After that the records alone decide: a
  // snapshot can show a job as running until the next score computation.
  int RunningLevel(int snapshot_level) const;

  std::vector<LevelStats> levels;
  double flushes = 0, flush_bytes = 0;
  // F, the flush file size: the first non-empty flush after n_w, then held
  // fixed, so C_0 = K0_cfg F is a constant divisor (H §3). 0 until then.
  double flush_file_bytes = 0;
  RLOpCounts ops0;
  std::vector<RLLevelReadCounts> reads0;

 private:
  struct Running {
    RLJobRecord begin;
    double wait_ops = -1;  // -1: no wait measured (not picked when due)
  };
  std::map<int, Running> running_;
  bool saw_compaction_ = false;
};

// Everything measured, in raw units, as of one poll. Levels are indexed
// 0..num_levels-1.
struct View {
  int num_levels = 0;
  double T = 0;
  int k0_cfg = 0;  // the configured L0 trigger
  int k_slow = 0;
  double write_buffer = 0;
  std::vector<double> C;      // nominal targets C_i; C[0] = 0
  std::vector<double> m;      // multipliers in effect; m[0] = 1
  std::vector<double> B;      // bytes not being compacted
  std::vector<double> phi;    // B_i / C_i; NaN at L0
  std::vector<double> score;  // phi / m for i >= 1; RocksDB's at L0
  int last = 0;               // L, the deepest level with bytes; 0 if none
  int k0 = 0;                 // L0 files not being compacted
  int k0_all = 0;             // all L0 files
  int K0 = 0;                 // the trigger in effect
  int running = -1;           // start level of the job holding the slot
  double backlog = kNaN;      // pending compaction bytes / H
  double l0_slowdown = kNaN;  // (K_slow - k0) / K_slow
  double mem_fill = kNaN;     // active memtable / write_buffer_size
  uint64_t op = 0;
  // Totals since the controller started.
  double ops = 0, gets = 0, scans = 0, writes = 0, user_bytes = 0;
  double flushes = 0, flush_bytes = 0;
  double F = kNaN;  // the fixed flush file size (Stats::flush_file_bytes)
  std::vector<double> probes, fp_reads, hit_reads, seeks;
  std::vector<double> rho;            // (X - O) / S over merges
  std::vector<double> xi;             // trivially moved share of bytes leaving
  std::vector<double> overlap;        // O / S over merges
  std::vector<double> rho_tilde;      // xi + (1 - xi) rho
  std::vector<double> inflow;         // net bytes landed
  std::vector<double> job_ops;        // mean operations served per merge
  std::vector<double> N;              // operations per turnover (§1.1)
  std::vector<double> since_release;  // operations since the last release
};

View MakeView(const RLHostOptions& options, const RLTreeSnapshot& snapshot,
              const Stats& stats, const RLOpCounts& ops,
              const std::vector<RLLevelReadCounts>& reads,
              const std::vector<double>& m, int K0);

// f_j of §1.1 at the multipliers in effect; NaN where undefined.
double Fanout(const View& v, int level);

// Due levels (L0 included), other than `level`, whose score exceeds
// max(score, 1 + epsilon) (G §3).
int QueuePosition(const View& v, int level, double score, double epsilon);

// The probability that a probed run without the key passes its filter
// (Lemma D.10's epsilon): false positives / (probes - hits), all levels.
double FalsePositiveRate(const View& v);

// The workload price ratios of G.2: R^f, R^b and R_sk.
void PriceRatios(const View& v, const Config& cfg, double* r_f, double* r_b,
                 double* r_sk);

enum class Agent { kL0, kInterior, kLast };
const char* AgentName(Agent agent);
const std::vector<std::string>& FeatureNames(Agent agent);

// Interior (G §3) or last-level (H §2) state of level >= 1, at control c.
// `interval` is the level's interval just closed; wait_carry its carried
// slot wait.
std::vector<double> LevelFeatures(const View& v, int level, bool last,
                                  const LevelControl& c,
                                  const LevelParts& interval, double wait_carry,
                                  const Config& cfg);

// L0's state (H §2), with v.K0 the trigger in effect.
std::vector<double> L0Features(const View& v, const L0Control& c,
                               const LevelParts& interval, const Config& cfg);

}  // namespace rlc
