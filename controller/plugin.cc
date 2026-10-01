#include "plugin.h"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <exception>

#include "policy.h"
#include "prior.h"

namespace rlc {

using ROCKSDB_NAMESPACE::RLHostOptions;
using ROCKSDB_NAMESPACE::RLJobRecord;
using ROCKSDB_NAMESPACE::RLLevelReadCounts;
using ROCKSDB_NAMESPACE::RLOpCounts;
using ROCKSDB_NAMESPACE::RLTreeSnapshot;

namespace {

constexpr auto kPoll = std::chrono::milliseconds(2);

uint64_t SteadyMicros() {
  return static_cast<uint64_t>(
      std::chrono::duration_cast<std::chrono::microseconds>(
          std::chrono::steady_clock::now().time_since_epoch())
          .count());
}

uint64_t Minus(uint64_t a, uint64_t b) { return a >= b ? a - b : 0; }

RLOpCounts Delta(const RLOpCounts& now, const RLOpCounts& before) {
  RLOpCounts d;
  d.keys_written = Minus(now.keys_written, before.keys_written);
  d.keys_read = Minus(now.keys_read, before.keys_read);
  d.seeks = Minus(now.seeks, before.seeks);
  d.bytes_written = Minus(now.bytes_written, before.bytes_written);
  return d;
}

std::vector<RLLevelReadCounts> Delta(
    const std::vector<RLLevelReadCounts>& now,
    const std::vector<RLLevelReadCounts>& before) {
  std::vector<RLLevelReadCounts> d(now.size());
  for (size_t i = 0; i < now.size() && i < before.size(); ++i) {
    d[i].probes = Minus(now[i].probes, before[i].probes);
    d[i].filter_passes = Minus(now[i].filter_passes, before[i].filter_passes);
    d[i].filter_hits = Minus(now[i].filter_hits, before[i].filter_hits);
    d[i].seeks = Minus(now[i].seeks, before[i].seeks);
  }
  return d;
}

std::string CheckOptions(const RLHostOptions& o) {
  if (o.num_levels < 2) return "host: fewer than 2 levels";
  if (!(o.level_multiplier > 1)) {
    return "host: level multiplier T must exceed 1";
  }
  // The fork orders targets by T * additional[i]; the plugin's masks and
  // repair use T alone, so it runs only where the two agree.
  for (int additional : o.level_multiplier_additional) {
    if (additional != 1) {
      return "host: max_bytes_for_level_multiplier_additional must be all 1";
    }
  }
  if (o.base_level_bytes == 0) return "host: max_bytes_for_level_base is 0";
  if (o.write_buffer_size == 0) return "host: write_buffer_size is 0";
  return "";
}

// Bytes not being compacted per level, B_i.
std::vector<double> Held(const RLTreeSnapshot& snapshot, int n) {
  std::vector<double> held(n, 0);
  for (int i = 0; i < n && i < static_cast<int>(snapshot.levels.size()); ++i) {
    const auto& l = snapshot.levels[i];
    held[i] =
        static_cast<double>(l.bytes - std::min(l.bytes_compacting, l.bytes));
  }
  return held;
}

// The oldest due-since among due levels; 0 if none is due.
uint64_t OldestDueSince(const RLTreeSnapshot& snapshot) {
  uint64_t oldest = 0;
  for (const auto& l : snapshot.levels) {
    if (l.due_since_micros > 0 &&
        (oldest == 0 || l.due_since_micros < oldest)) {
      oldest = l.due_since_micros;
    }
  }
  return oldest;
}

}  // namespace

Controller::Controller(RLControllerHost* host, const std::string& config_path,
                       std::function<uint64_t()> clock)
    : host_(host), now_(clock ? std::move(clock) : SteadyMicros) {
  options_ = host_->Options();
  n_ = std::max(options_.num_levels, 1);
  std::string error;
  bool ok = LoadConfig(config_path, &cfg_, &error);
  if (ok) {
    error = CheckOptions(options_);
    ok = error.empty();
  }
  ok = ok && CheckConfigAgainstHost(cfg_, options_.l0_trigger,
                                    options_.l0_slowdown_trigger, &error);
  // The logs the config names are opened even when a later value was
  // invalid, so the fallback is logged where the run looks for it.
  for (auto* log : {&decisions_, &transitions_}) {
    const std::string& path =
        log == &decisions_ ? cfg_.decision_log : cfg_.transition_log;
    std::string log_error;
    if (!path.empty() && !log->Open(path, &log_error)) {
      if (ok) error = "log: " + log_error;
      ok = false;
    }
  }

  const RLOpCounts ops = host_->OpCounts();
  std::vector<RLLevelReadCounts> reads;
  host_->ReadCounters(&reads);
  reads.resize(n_);
  last_snapshot_ = host_->Snapshot();
  stats_.Reset(n_, ops, reads);
  clock_.Add(now_(), ops.total());
  prev_ops_ = ops;
  prev_reads_ = reads;
  parts_.assign(n_, LevelParts());
  intervals_.assign(n_, Interval());
  for (Interval& interval : intervals_) interval.start_op = ops.total();
  last_decision_op_.assign(n_, ops.total());
  prev_held_.assign(n_, 0);

  // Start from the values in effect, so the first Apply is not a jump.
  m_applied_.assign(n_, 1.0);
  k0_applied_ = options_.l0_trigger;
  if (last_snapshot_) {
    if (last_snapshot_->level_target_multipliers.size() ==
        static_cast<size_t>(n_)) {
      m_applied_ = last_snapshot_->level_target_multipliers;
    }
    if (last_snapshot_->l0_trigger > 0) {
      k0_applied_ = last_snapshot_->l0_trigger;
    }
    prev_running_ = last_snapshot_->running_start_level;
    if (!last_snapshot_->levels.empty()) {
      prev_l0_due_ = last_snapshot_->levels[0].score >= 1;
      prev_k0_all_ = last_snapshot_->levels[0].num_files;
    }
    prev_held_ = Held(*last_snapshot_, n_);
  }
  m_target_ = m_applied_;
  k0_target_ = k0_applied_;
  controls_.assign(n_, LevelControl());
  for (int i = 1; i < n_; ++i) controls_[i].timing = m_applied_[i];
  l0_.anchor = options_.l0_trigger;
  l0_.offset = k0_applied_ - options_.l0_trigger;

  const Bounds& b = cfg_.bounds;
  decisions_.Write(
      JsonLine("start")
          .Str("mode", cfg_.mode_name)
          .Uint("op", ops.total())
          .Uint("t_us", now_())
          .Uint("num_levels", static_cast<uint64_t>(n_))
          .Num("m_min", b.m_min)
          .Num("m_max", b.m_max)
          .Num("k0_min", b.k0_min)
          .Num("k0_cap", b.k0_cap)
          .Num("epsilon", b.epsilon)
          .Num("phi_min", b.phi_min)
          .Num("alpha", b.alpha)
          .Num("kappa_d", b.kappa_d)
          .Num("kappa_a", b.kappa_a)
          .Num("k", cfg_.k)
          .Num("setoptions_min_interval_ms", cfg_.setoptions_min_interval_ms)
          .Nums("m", m_applied_)
          .Num("k0", k0_applied_)
          .str());
  if (!ok) EnterFallback(error);
  // Runs on RocksDB's threads: nothing may escape into the host. A record
  // that cannot be queued (out of memory) is counted, and the next poll falls
  // back, since the attribution would then be incomplete.
  host_->SetJobCallback([this](const RLJobRecord& job) noexcept {
    try {
      std::lock_guard<std::mutex> lock(queue_mu_);
      queue_.push_back(job);
    } catch (...) {
      lost_jobs_.store(true, std::memory_order_relaxed);
    }
  });
}

Controller::~Controller() { Stop(); }

void Controller::Start() {
  thread_ = std::thread([this] { Run(); });
}

void Controller::Run() noexcept {
  // Step() catches everything it can; this is the last line of defence, so
  // nothing reaches std::terminate inside the host process.
  try {
    std::unique_lock<std::mutex> lock(stop_mu_);
    while (!stop_) {
      lock.unlock();
      Step();
      lock.lock();
      stop_cv_.wait_for(lock, kPoll, [this] { return stop_; });
    }
  } catch (...) {
    fprintf(stderr, "rl_controller: the polling thread stopped on an error\n");
  }
}

void Controller::Step() noexcept {
  try {
    Poll(true);
  } catch (const std::exception& e) {
    FallbackNoThrow(std::string("plugin error: ") + e.what());
  } catch (...) {
    FallbackNoThrow("plugin error");
  }
}

void Controller::Poll(bool decide) {
  const uint64_t now = now_();
  const RLOpCounts ops = host_->OpCounts();
  std::vector<RLLevelReadCounts> reads;
  host_->ReadCounters(&reads);
  reads.resize(n_);
  const std::shared_ptr<const RLTreeSnapshot> snapshot = host_->Snapshot();
  // The previous snapshot's due levels still count when trimming the op
  // clock: a job's begin record can arrive after the snapshot in which its
  // level stopped being due.
  const uint64_t previous_due =
      last_snapshot_ ? OldestDueSince(*last_snapshot_) : 0;
  if (snapshot) last_snapshot_ = snapshot;
  clock_.Add(now, ops.total());

  // The segment since the last poll, under the state sampled then.
  Segment segment;
  segment.ops = Delta(ops, prev_ops_);
  segment.reads = Delta(reads, prev_reads_);
  segment.slot_level = prev_running_;
  segment.l0_due = prev_l0_due_;
  segment.k0 = prev_k0_all_;
  segment.K0 = k0_applied_;
  segment.held = prev_held_;
  AttributeSegment(segment, &parts_);
  prev_ops_ = ops;
  prev_reads_ = reads;

  std::vector<RLJobRecord> jobs;
  {
    std::lock_guard<std::mutex> lock(queue_mu_);
    jobs.swap(queue_);
  }
  if (lost_jobs_.exchange(false, std::memory_order_relaxed)) {
    EnterFallback("a job record was lost in the job callback");
  }
  for (const RLJobRecord& job : jobs) stats_.OnJob(job, clock_, &parts_);
  // Trim only after this poll's records have read their waits (G §3), and
  // keep samples back to the oldest due-since either snapshot shows.
  const uint64_t current_due =
      last_snapshot_ ? OldestDueSince(*last_snapshot_) : 0;
  clock_.Trim(now, previous_due == 0  ? current_due
                   : current_due == 0 ? previous_due
                                      : std::min(previous_due, current_due));
  if (!flush_size_logged_ && stats_.flush_file_bytes > 0) {
    flush_size_logged_ = true;
    decisions_.Write(
        JsonLine("flush_size")
            .Uint("op", ops.total())
            .Uint("t_us", now)
            .Num("F", stats_.flush_file_bytes)
            .Num("C_0", options_.l0_trigger * stats_.flush_file_bytes)
            .str());
  }

  prev_running_ = stats_.RunningLevel(
      last_snapshot_ ? last_snapshot_->running_start_level : -1);
  if (last_snapshot_) {
    if (!last_snapshot_->levels.empty()) {
      prev_l0_due_ = last_snapshot_->levels[0].score >= 1;
      prev_k0_all_ = last_snapshot_->levels[0].num_files;
    }
    prev_held_ = Held(*last_snapshot_, n_);
  }

  if (!draining_ && host_->Draining()) {
    draining_ = true;
    decisions_.Write(
        JsonLine("drain").Uint("op", ops.total()).Uint("t_us", now).str());
    Invalidate("drain");
  }
  if (decide && !fallback_ && !draining_ && last_snapshot_) {
    std::vector<DecisionRecord> made;
    if (!last_snapshot_->write_stopped) {
      made = Decide(*last_snapshot_, ops, reads, now);
    }
    std::string error;
    const bool ok = Flush(ops.total(), now, !made.empty(), &error);
    for (DecisionRecord& r : made) {
      r.effective = r.level == 0 ? k0_applied_ : m_applied_[r.level];
      decisions_.Write(DecisionLine(r));
    }
    if (!ok) {
      if (host_->Draining()) {
        draining_ = true;
        decisions_.Write(
            JsonLine("drain").Uint("op", ops.total()).Uint("t_us", now).str());
        Invalidate("drain");
      } else {
        EnterFallback("Apply failed: " + error);
      }
    }
  }
  CheckLogs();
}

std::vector<DecisionRecord> Controller::Decide(
    const RLTreeSnapshot& snapshot, const RLOpCounts& ops,
    const std::vector<RLLevelReadCounts>& reads, uint64_t now) {
  const Bounds& b = cfg_.bounds;
  const bool hold_only = cfg_.mode == Mode::kHoldOnly;
  // The controller's own values: they reach RocksDB at the next Apply.
  View v =
      MakeView(options_, snapshot, stats_, ops, reads, m_target_, k0_target_);
  const uint64_t op = ops.total();
  std::vector<double> m = m_target_;
  std::vector<DecisionRecord> made;
  // Top-down, so each level's mask sees the decisions above it.
  for (int level = 0; level <= v.last && level < n_; ++level) {
    const double N = v.N[level];
    const double since =
        static_cast<double>(Minus(op, last_decision_op_[level]));
    // G-iv: N_j / k operations per decision, except L0, whose state changes
    // only at flushes and L0 compactions: it decides once per flush,
    // N_0 / K0_cfg operations.
    const double per_turnover = level == 0 ? v.k0_cfg : cfg_.k;
    if (!(N > 0) || !(per_turnover > 0) || since < N / per_turnover) continue;
    // Hold-only does not relax either: it never changes a value.
    const double turnovers = hold_only ? 0 : since / N;
    DecisionRecord r;
    r.id = next_id_++;
    r.level = level;
    r.op = op;
    r.t_us = now;
    r.mode = cfg_.mode_name;
    std::vector<double> state;
    Choice choice;
    if (level == 0) {
      const L0Control c = RelaxL0(l0_, turnovers, options_.l0_trigger, b);
      v.K0 = c.K0(b);
      r.agent = Agent::kL0;
      r.mask = L0Mask(c, v.k0, v.K0, b);
      r.b = L0Prior(v, c, cfg_);
      state = L0Features(v, c, parts_[0], cfg_);
      choice = DecideL0(cfg_, v, c, r.mask, parts_[0]);
      l0_ = ActL0(c, choice.action, v.k0, b);
      r.old_value = k0_applied_;
      r.requested = l0_.K0(b);
      r.anchor = l0_.anchor;
      r.timing = l0_.offset;
      v.K0 = l0_.K0(b);  // the levels below see the new trigger
    } else {
      const bool last = level == v.last;
      const LevelControl c = Relax(controls_[level], turnovers, b);
      m[level] = c.m();
      r.agent = last ? Agent::kLast : Agent::kInterior;
      r.mask = LevelMask(m, level, last, c, v.phi[level], v.T, b);
      r.b = LevelPrior(v, level, c, cfg_);
      state = LevelFeatures(v, level, last, c, parts_[level],
                            stats_.levels[level].wait_carry, cfg_);
      choice = DecideLevel(cfg_, v, level, last, r.mask);
      controls_[level] = Act(c, choice.action, v.phi[level], b);
      m[level] = controls_[level].m();
      r.old_value = m_applied_[level];
      r.requested = m[level];
      r.anchor = controls_[level].anchor;
      r.timing = controls_[level].timing;
    }
    r.action = choice.action;
    r.reason = choice.reason;
    // G §3: the mean wait per release, carried forward from the last
    // interval with a release (waits are attributed when a job ends).
    const LevelParts& closing = parts_[level];
    if (closing.waits > 0) {
      stats_.levels[level].wait_carry = closing.wait_ops / closing.waits;
    }
    CloseInterval(level, v, r.agent, state, r.mask, r.b, op, r.id, true,
                  choice.action);
    last_decision_op_[level] = op;
    made.push_back(r);
  }
  // Hold-only never repairs and never applies: it is native RocksDB under
  // whatever values it started with (ARCH-5), even ones its bounds refuse.
  if (made.empty() || hold_only) return made;

  const std::vector<double> before = m;
  for (int level : Repair(&m, v.T, b)) {
    controls_[level].timing = m[level] / controls_[level].anchor;
    decisions_.Write(JsonLine("repair")
                         .Uint("level", static_cast<uint64_t>(level))
                         .Uint("op", op)
                         .Num("old", before[level])
                         .Num("new", m[level])
                         .Str("reason", "targets must not shrink going down")
                         .str());
  }
  m_target_ = m;
  k0_target_ = l0_.K0(b);
  if (m_target_ != m_applied_ || k0_target_ != k0_applied_) {
    if (pending_first_id_ == 0) pending_first_id_ = made.front().id;
    last_id_ = made.back().id;
  }
  return made;
}

bool Controller::Flush(uint64_t op, uint64_t now, bool new_changes,
                       std::string* error) {
  if (cfg_.mode == Mode::kHoldOnly) return true;
  if (m_target_ == m_applied_ && k0_target_ == k0_applied_) {
    pending_first_id_ = 0;
    return true;
  }
  // A-Impl-5: at most one SetOptions per setoptions_min_interval_ms. Later
  // changes merge into the targets and go in the next call.
  const double interval_us = cfg_.setoptions_min_interval_ms * 1000;
  if (applied_once_ &&
      static_cast<double>(Minus(now, last_apply_us_)) < interval_us) {
    if (new_changes) {
      decisions_.Write(
          JsonLine("apply_held")
              .Uint("op", op)
              .Uint("t_us", now)
              .Uint("first_id", pending_first_id_)
              .Uint("last_id", last_id_)
              .Num("next_t_us",
                   static_cast<double>(last_apply_us_) + interval_us)
              .str());
    }
    return true;
  }
  bool ok = Admissible(m_target_, options_.level_multiplier, cfg_.bounds);
  // ACT-3: RocksDB recomputes every score before SetOptions returns
  // (A-Impl-4), so the snapshot read right after a call should carry the
  // values it sent.
  bool seen = false;
  uint64_t seen_generation = 0;
  if (!ok) {
    *error = "multipliers inadmissible after repair";
  } else {
    ok = host_->Apply(m_target_, k0_target_, error);
    applied_once_ = true;
    last_apply_us_ = now;
    if (ok) {
      const auto after = host_->Snapshot();
      seen_generation = after->generation;
      seen = after->level_target_multipliers == m_target_ &&
             after->l0_trigger == k0_target_;
    }
  }
  decisions_.Write(JsonLine("apply")
                       .Uint("op", op)
                       .Uint("t_us", now)
                       .Uint("first_id", pending_first_id_)
                       .Uint("last_id", last_id_)
                       .Nums("m", m_target_)
                       .Num("k0", k0_target_)
                       .Uint("ok", ok ? 1 : 0)
                       .Str("error", *error)
                       .Uint("seen", seen ? 1 : 0)
                       .Uint("seen_generation", seen_generation)
                       .str());
  if (ok) {
    m_applied_ = m_target_;
    k0_applied_ = k0_target_;
    pending_first_id_ = 0;
  }
  return ok;
}

void Controller::CloseInterval(int level, const View& v, Agent agent,
                               const std::vector<double>& state,
                               const Mask& mask, const Values& b, uint64_t op,
                               uint64_t id, bool has_action, Action action) {
  Interval& open = intervals_[level];
  TransitionRecord t;
  t.level = level;
  t.id = open.id;
  t.start_op = open.start_op;
  t.end_op = op;
  t.agent = open.agent;
  t.state = open.state;
  t.mask = open.mask;
  t.b = open.b;
  t.has_action = open.has_action;
  t.action = open.action;
  t.parts = parts_[level];
  if (level < static_cast<int>(v.B.size())) {
    t.b_bytes = v.B[level];
    t.rho_tilde = v.rho_tilde[level];
  }
  t.next_agent = agent;
  t.next_state = state;
  t.next_mask = mask;
  t.valid = open.valid && open.agent == agent;
  t.invalid = !open.valid ? open.invalid : t.valid ? "" : "agent changed";
  transitions_.Write(TransitionLine(t));

  parts_[level] = LevelParts();
  open = Interval();
  open.id = id;
  open.start_op = op;
  open.agent = agent;
  open.state = state;
  open.mask = mask;
  open.b = b;
  open.has_action = has_action;
  open.action = action;
  open.valid = has_action && !fallback_ && !draining_;
  open.invalid = open.valid  ? ""
                 : fallback_ ? "fallback"
                 : draining_ ? "drain"
                             : "no decision";
}

void Controller::Invalidate(const std::string& reason) {
  for (Interval& interval : intervals_) {
    if (interval.valid) {
      interval.valid = false;
      interval.invalid = reason;
    }
  }
}

void Controller::EnterFallback(const std::string& reason) {
  if (fallback_) return;
  fallback_ = true;
  const std::vector<double> ones(n_, 1.0);
  const int k0 = options_.l0_trigger;
  // No call when the fallback values are already in effect.
  const bool call = m_applied_ != ones || k0_applied_ != k0;
  std::string error;
  const bool applied = !call || host_->Apply(ones, k0, &error);
  if (applied) {
    m_applied_ = ones;
    k0_applied_ = k0;
    controls_.assign(n_, LevelControl());
    l0_ = L0Control{static_cast<double>(k0), 0};
  }
  m_target_ = m_applied_;
  k0_target_ = k0_applied_;
  pending_first_id_ = 0;
  decisions_.Write(JsonLine("fallback")
                       .Str("reason", reason)
                       .Uint("op", prev_ops_.total())
                       .Uint("t_us", now_())
                       .Uint("call", call ? 1 : 0)
                       .Uint("applied", applied ? 1 : 0)
                       .Str("error", error)
                       .str());
  // A lost or unwritable decision log must not hide the fallback.
  if (!decisions_.open() || decisions_.failed()) {
    fprintf(stderr, "rl_controller: fallback: %s\n", reason.c_str());
  }
  Invalidate("fallback");
}

void Controller::FallbackNoThrow(const std::string& reason) noexcept {
  try {
    EnterFallback(reason);
  } catch (...) {
    fprintf(stderr, "rl_controller: fallback failed: %s\n", reason.c_str());
  }
}

void Controller::CheckLogs() {
  if (decisions_.failed() || transitions_.failed()) {
    EnterFallback("a log write failed");
  }
}

void Controller::Stop() noexcept {
  if (stopped_) return;
  stopped_ = true;
  try {
    host_->SetJobCallback(nullptr);
    {
      std::lock_guard<std::mutex> lock(stop_mu_);
      stop_ = true;
    }
    stop_cv_.notify_all();
    if (thread_.joinable()) thread_.join();
  } catch (...) {
    fprintf(stderr, "rl_controller: error while stopping the thread\n");
  }
  try {
    Poll(false);
    const RLOpCounts ops = host_->OpCounts();
    std::vector<RLLevelReadCounts> reads;
    host_->ReadCounters(&reads);
    reads.resize(n_);
    const View v = last_snapshot_
                       ? MakeView(options_, *last_snapshot_, stats_, ops, reads,
                                  m_applied_, k0_applied_)
                       : View();
    for (int level = 0; level < n_; ++level) {
      Interval& open = intervals_[level];
      if (open.valid) {
        open.valid = false;
        open.invalid = "closed at stop";
      }
      CloseInterval(level, v, open.agent, {}, Mask{}, Values{}, ops.total(), 0,
                    false, Action::kHold);
    }
    decisions_.Write(JsonLine("stop")
                         .Uint("op", ops.total())
                         .Uint("t_us", now_())
                         .Uint("fallback", fallback_ ? 1 : 0)
                         .Uint("draining", draining_ ? 1 : 0)
                         .str());
  } catch (...) {
    fprintf(stderr, "rl_controller: error while closing the logs\n");
  }
}

}  // namespace rlc

extern "C" void* rl_controller_create(ROCKSDB_NAMESPACE::RLControllerHost* host,
                                      const char* config_path) {
  if (host == nullptr) return nullptr;
  try {
    auto controller =
        std::make_unique<rlc::Controller>(host, config_path ? config_path : "");
    controller->Start();
    return controller.release();
  } catch (...) {
    return nullptr;
  }
}

extern "C" void rl_controller_destroy(void* controller) {
  delete static_cast<rlc::Controller*>(controller);
}
