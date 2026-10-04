// Shared fixtures for the controller's tests: explicit bounds and prices (the
// real values are PREREGISTRATION's), a synthetic View, a fake host, and
// config files in a temporary directory.
#pragma once

#include <unistd.h>

#include <cmath>
#include <filesystem>
#include <fstream>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "config.h"
#include "rocksdb/rl_controller_host.h"
#include "state.h"

namespace rlc {
namespace test {

using ROCKSDB_NAMESPACE::RLControllerHost;
using ROCKSDB_NAMESPACE::RLHostOptions;
using ROCKSDB_NAMESPACE::RLJobRecord;
using ROCKSDB_NAMESPACE::RLLevelReadCounts;
using ROCKSDB_NAMESPACE::RLLevelSnapshot;
using ROCKSDB_NAMESPACE::RLOpCounts;
using ROCKSDB_NAMESPACE::RLStepCounts;
using ROCKSDB_NAMESPACE::RLTreeSnapshot;

constexpr double kMiB = 1024.0 * 1024.0;

inline Bounds TestBounds() {
  Bounds b;
  b.m_min = 0.5;
  b.m_max = 2.0;
  b.k0_min = 2;
  b.k0_cap = 8;
  b.epsilon = 0.05;
  b.phi_min = 0.6;
  b.alpha = 1.25;
  b.kappa_d = 1;
  b.kappa_a = 4;
  return b;
}

// Prices in nanosecond-like units: a byte written 1, a filter probe 100, a
// block read 1000, a seek 2000.
inline Config TestConfig(Mode mode = Mode::kHoldOnly,
                         std::vector<std::string> rules = {}) {
  Config c;
  c.bounds = TestBounds();
  c.mode = mode;
  c.mode_name = mode == Mode::kRules ? "rules" : "hold-only";
  c.rules = std::move(rules);
  c.rule_l0_early_read_ratio = 1.0;
  c.rule_release_fill = 0.3;
  c.rule_garbage_drop = 0.2;
  c.k = 4;
  c.b_max = 1e9;
  c.beta_w = c.beta_r = c.beta_s = 1;
  c.c_w = 1;
  c.c_f = 100;
  c.c_blk = 1000;
  c.c_sk = 2000;
  c.c_open = 5000;
  c.c_s = 0.001;
  c.q_bar = 1000;
  c.setoptions_min_interval_ms = 100;
  return c;
}

inline void SetPhi(View* v, int level, double phi) {
  v->phi[level] = phi;
  v->B[level] = phi * v->C[level];
  v->score[level] = phi / v->m[level];
}

inline void SetM(View* v, int level, double m) {
  v->m[level] = m;
  v->score[level] = v->phi[level] / m;
}

// n levels, every level >= 1 at fill 0.5 and m = 1, with measured totals:
// 100k operations (50k Gets, 10k scans, 40k writes of 1 KiB), 40 flushes of
// 1 MiB, per level 30k probes, 300 false positives, 5k hits and 10k seeks,
// rho 0.9, xi 0.3, overlap 1.5, N_j = 20000 T^(j-1).
inline View TestView(int n = 6, double T = 2) {
  View v;
  v.num_levels = n;
  v.T = T;
  v.k0_cfg = 4;
  v.k_slow = 20;
  v.write_buffer = kMiB;
  v.C.assign(n, 0);
  v.m.assign(n, 1.0);
  v.B.assign(n, 0);
  v.phi.assign(n, kNaN);
  v.score.assign(n, 0);
  v.score[0] = 0.5;
  v.last = n - 1;
  v.k0 = 2;
  v.k0_all = 2;
  v.K0 = 4;
  v.running = -1;
  v.backlog = 0.1;
  v.l0_slowdown = 0.9;
  v.mem_fill = 0.5;
  v.op = 100000;
  v.ops = 100000;
  v.gets = 50000;
  v.scans = 10000;
  v.writes = 40000;
  v.user_bytes = 40000 * 1024.0;
  v.flushes = 40;
  v.flush_bytes = 40 * kMiB;
  v.F = kMiB;
  v.probes.assign(n, 30000);
  v.fp_reads.assign(n, 300);
  v.hit_reads.assign(n, 5000);
  v.seeks.assign(n, 10000);
  // No reopens unless a test sets them (D-21), so the prices are c_f's.
  v.get_reopens.assign(n, 0);
  v.iter_reopens.assign(n, 0);
  v.rho.assign(n, 0.9);
  v.xi.assign(n, 0.3);
  v.overlap.assign(n, 1.5);
  v.rho_tilde.assign(n, 0.3 + 0.7 * 0.9);
  v.job_ops.assign(n, 500);
  v.since_release.assign(n, 1000);
  v.N.assign(n, 0);
  v.inflow.assign(n, 0);
  v.N[0] = v.k0_cfg * v.F * v.ops / v.flush_bytes;
  v.inflow[0] = v.flush_bytes;
  for (int i = 1; i < n; ++i) {
    v.C[i] = 8 * kMiB * std::pow(T, i - 1);
    v.N[i] = 20000 * std::pow(T, i - 1);
    v.inflow[i] = v.C[i] * v.ops / v.N[i];
    SetPhi(&v, i, 0.5);
  }
  return v;
}

// A fresh directory, removed with its contents when the test binary exits.
inline std::string TempDir() {
  struct Registry {
    std::vector<std::filesystem::path> dirs;
    ~Registry() {
      std::error_code ignored;
      for (const auto& dir : dirs) std::filesystem::remove_all(dir, ignored);
    }
  };
  static Registry registry;
  static int counter = 0;
  const auto dir = std::filesystem::temp_directory_path() /
                   ("rl-controller-test-" + std::to_string(getpid()) + "-" +
                    std::to_string(counter++));
  std::filesystem::create_directories(dir);
  registry.dirs.push_back(dir);
  return dir.string();
}

inline std::vector<std::string> ReadLines(const std::string& path) {
  std::ifstream file(path);
  std::vector<std::string> lines;
  for (std::string line; std::getline(file, line);) lines.push_back(line);
  return lines;
}

inline int CountType(const std::vector<std::string>& lines,
                     const std::string& type) {
  int count = 0;
  for (const std::string& line : lines) {
    count += line.find("{\"type\":\"" + type + "\"") == 0 ? 1 : 0;
  }
  return count;
}

// A valid config as key -> JSON literal; tests override or drop keys.
inline std::map<std::string, std::string> BaseConfig(const std::string& dir) {
  return {{"m_min", "0.5"},
          {"m_max", "2.0"},
          {"k0_min", "2"},
          {"k0_cap", "8"},
          {"epsilon", "0.05"},
          {"phi_min", "0.6"},
          {"alpha", "1.25"},
          {"kappa_d", "1"},
          {"kappa_a", "4"},
          {"mode", "\"hold-only\""},
          {"k", "4"},
          {"b_max", "10"},
          {"beta_w", "1"},
          {"beta_r", "1"},
          {"beta_s", "1"},
          {"c_w", "1"},
          {"c_f", "100"},
          {"c_blk", "1000"},
          {"c_sk", "2000"},
          {"c_open", "5000"},
          {"c_s", "0.001"},
          {"q_bar", "1000"},
          {"setoptions_min_interval_ms", "100"},
          {"cost_model", "1"},
          {"decision_log", "\"" + dir + "/decisions.jsonl\""},
          {"transition_log", "\"" + dir + "/transitions.jsonl\""}};
}

inline std::string ToJson(const std::map<std::string, std::string>& values) {
  std::string text = "{";
  for (const auto& entry : values) {
    text += (text.size() > 1 ? ",\n \"" : "\"") + entry.first +
            "\": " + entry.second;
  }
  return text + "}";
}

inline std::string WriteConfig(
    const std::string& dir, const std::map<std::string, std::string>& values) {
  const std::string path = dir + "/config.json";
  std::ofstream(path) << ToJson(values);
  return path;
}

// Five levels, T = 2, C_1 = 8 MiB, write buffer 1 MiB, trigger 4, slowdown
// 20. Apply updates the snapshot's values in effect. Thread-safe: every host
// method and every mutator takes `mu`; a job callback runs under `cb_mu`,
// which SetJobCallback also takes, so replacing the callback waits for a call
// in flight. Tests that run no controller thread may touch fields directly.
class FakeHost : public RLControllerHost {
 public:
  FakeHost() {
    options.num_levels = 5;
    options.level_multiplier = 2;
    options.level_multiplier_additional.assign(5, 1);
    options.base_level_bytes = 8 * 1024 * 1024;
    options.write_buffer_size = 1024 * 1024;
    options.l0_trigger = 4;
    options.l0_slowdown_trigger = 20;
    options.l0_stop_trigger = 36;
    snapshot = std::make_shared<RLTreeSnapshot>();
    snapshot->levels.resize(5);
    snapshot->level_target_multipliers.assign(5, 1.0);
    snapshot->l0_trigger = 4;
    reads.resize(5);
  }

  // Level i >= 1 at fill phi (m = 1); L0 with `files` files.
  void SetLevel(int level, double phi) {
    std::lock_guard<std::mutex> lock(mu);
    const double target = 8 * kMiB * std::pow(2, level - 1);
    snapshot->levels[level].bytes = static_cast<uint64_t>(phi * target);
    snapshot->levels[level].score = phi;
  }
  void SetL0(int files) {
    std::lock_guard<std::mutex> lock(mu);
    snapshot->levels[0].num_files = files;
    snapshot->levels[0].score =
        static_cast<double>(files) / snapshot->l0_trigger;
  }
  void AddOps(uint64_t gets, uint64_t writes) {
    std::lock_guard<std::mutex> lock(mu);
    ops.keys_read += gets;
    ops.keys_written += writes;
    ops.bytes_written += writes * 1024;
  }
  void SetDraining() {
    std::lock_guard<std::mutex> lock(mu);
    draining = true;
  }
  size_t Applies() const {
    std::lock_guard<std::mutex> lock(mu);
    return applied.size();
  }
  // Fires the job callback, as RocksDB's background threads do.
  void Job(const RLJobRecord& job) {
    std::lock_guard<std::mutex> lock(cb_mu);
    if (callback) callback(job);
  }
  bool HasCallback() {
    std::lock_guard<std::mutex> lock(cb_mu);
    return static_cast<bool>(callback);
  }
  static RLJobRecord Compaction(bool end, int job_id, int start, uint64_t s,
                                uint64_t o, uint64_t x) {
    RLJobRecord r;
    r.kind = end ? RLJobRecord::Kind::kCompactionEnd
                 : RLJobRecord::Kind::kCompactionBegin;
    r.job_id = job_id;
    r.start_level = start;
    r.output_level = start + 1;
    r.s = s;
    r.o = o;
    r.x = end ? x : 0;
    return r;
  }
  static RLJobRecord Flush(uint64_t bytes) {
    RLJobRecord r;
    r.kind = RLJobRecord::Kind::kFlushEnd;
    r.output_level = 0;
    r.x = bytes;
    return r;
  }

  RLHostOptions Options() const override {
    std::lock_guard<std::mutex> lock(mu);
    return options;
  }
  std::shared_ptr<const RLTreeSnapshot> Snapshot() const override {
    std::lock_guard<std::mutex> lock(mu);
    return std::make_shared<RLTreeSnapshot>(*snapshot);
  }
  RLOpCounts OpCounts() const override {
    std::lock_guard<std::mutex> lock(mu);
    if (throw_in_op_counts) throw std::runtime_error("injected host error");
    return ops;
  }
  void ReadCounters(std::vector<RLLevelReadCounts>* out) const override {
    std::lock_guard<std::mutex> lock(mu);
    *out = reads;
  }
  RLStepCounts StepCounts() const override {
    std::lock_guard<std::mutex> lock(mu);
    return steps;
  }
  void SetJobCallback(std::function<void(const RLJobRecord&)> cb) override {
    std::function<void(const RLJobRecord&)> previous;
    {
      std::lock_guard<std::mutex> lock(cb_mu);  // waits for a call in flight
      previous = std::move(callback);
      callback = std::move(cb);
    }
  }  // `previous` is destroyed here, before returning
  bool Apply(const std::vector<double>& m, int k0,
             std::string* error) override {
    std::lock_guard<std::mutex> lock(mu);
    if (draining) {
      *error = "draining";
      return false;
    }
    if (fail_apply) {
      *error = "injected failure";
      return false;
    }
    applied.emplace_back(m, k0);
    if (!apply_unseen) {  // as RocksDB, which republishes before returning
      snapshot->level_target_multipliers = m;
      snapshot->l0_trigger = k0;
      ++snapshot->generation;
    }
    return true;
  }
  bool Draining() const override {
    std::lock_guard<std::mutex> lock(mu);
    return draining;
  }

  mutable std::mutex mu;
  std::mutex cb_mu;
  RLHostOptions options;
  std::shared_ptr<RLTreeSnapshot> snapshot;
  RLOpCounts ops;
  std::vector<RLLevelReadCounts> reads;
  RLStepCounts steps;
  bool draining = false;
  bool fail_apply = false;
  bool apply_unseen = false;  // accept an Apply but never publish it
  bool throw_in_op_counts = false;
  std::vector<std::pair<std::vector<double>, int>> applied;
  std::function<void(const RLJobRecord&)> callback;
};

}  // namespace test
}  // namespace rlc
