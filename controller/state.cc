#include "state.h"

#include <algorithm>
#include <cmath>
#include <iterator>

#include "jobs.h"

namespace rlc {

using ROCKSDB_NAMESPACE::RLLevelSnapshot;

double Ratio(double a, double b) { return b > 0 ? a / b : kNaN; }

void OpClock::Add(uint64_t t_micros, uint64_t op) {
  samples_.emplace_back(t_micros, op);
}

void OpClock::Trim(uint64_t now_micros, uint64_t keep_from_micros) {
  constexpr uint64_t kKeepMicros = 60 * 1000 * 1000;
  const uint64_t minute_ago =
      now_micros > kKeepMicros ? now_micros - kKeepMicros : 0;
  const uint64_t keep_from = keep_from_micros > 0
                                 ? std::min(keep_from_micros, minute_ago)
                                 : minute_ago;
  // Keep one sample at or before keep_from, so OpAt(keep_from) interpolates.
  while (samples_.size() > 2 && samples_[1].first <= keep_from) {
    samples_.pop_front();
  }
}

double OpClock::OpAt(uint64_t t) const {
  if (samples_.empty()) return 0;
  if (t <= samples_.front().first)
    return static_cast<double>(samples_.front().second);
  if (t >= samples_.back().first)
    return static_cast<double>(samples_.back().second);
  const auto after = std::upper_bound(
      samples_.begin(), samples_.end(), t,
      [](uint64_t time, const std::pair<uint64_t, uint64_t>& s) {
        return time < s.first;
      });
  const auto before = after - 1;
  const double span = static_cast<double>(after->first - before->first);
  const double part = static_cast<double>(t - before->first) / span;
  return static_cast<double>(before->second) +
         part * (static_cast<double>(after->second) -
                 static_cast<double>(before->second));
}

void Stats::Reset(int num_levels, const RLOpCounts& ops,
                  const std::vector<RLLevelReadCounts>& reads) {
  levels.assign(num_levels, LevelStats());
  flushes = 0;
  flush_bytes = 0;
  flush_file_bytes = 0;
  ops0 = ops;
  reads0 = reads;
  reads0.resize(num_levels);
  running_.clear();
  flushes_running_.clear();
  saw_compaction_ = false;
}

void Stats::OnJob(const RLJobRecord& job, const OpClock& clock,
                  std::vector<LevelParts>* parts) {
  const int n = static_cast<int>(levels.size());
  const bool in_range = job.start_level >= 0 && job.start_level < n;
  saw_compaction_ = saw_compaction_ ||
                    job.kind == RLJobRecord::Kind::kCompactionBegin ||
                    job.kind == RLJobRecord::Kind::kCompactionEnd;
  switch (job.kind) {
    case RLJobRecord::Kind::kFlushBegin: {
      // D-23 §3(a): a flush's begin record opens its window (the plugin
      // charges its interference to the write-path bucket). A failed flush
      // sends no end record, so a begin older than a minute is dropped.
      constexpr uint64_t kStaleMicros = 60 * 1000 * 1000;
      for (auto it = flushes_running_.begin(); it != flushes_running_.end();) {
        it = job.t_micros > it->second.t_micros + kStaleMicros
                 ? flushes_running_.erase(it)
                 : std::next(it);
      }
      flushes_running_[job.job_id] = job;
      break;
    }
    case RLJobRecord::Kind::kFlushEnd:
      flushes_running_.erase(job.job_id);
      if (job.ok) {
        flushes += 1;
        flush_bytes += static_cast<double>(job.x);
        if (n > 0) levels[0].inflow_bytes += static_cast<double>(job.x);
        if (flush_file_bytes == 0)
          flush_file_bytes = static_cast<double>(job.x);
      }
      break;
    case RLJobRecord::Kind::kCompactionBegin: {
      Running& r = running_[job.job_id];
      r.begin = job;
      if (in_range && job.due_since_micros > 0) {
        // G §3: the wait runs from the later of the level becoming due and
        // its previous job ending, to this job starting.
        const LevelStats& s = levels[job.start_level];
        const double from =
            clock.OpAt(std::max(job.due_since_micros, s.last_end_micros));
        r.wait_ops = std::max(0.0, static_cast<double>(job.op) - from);
      }
      break;
    }
    case RLJobRecord::Kind::kCompactionEnd: {
      const auto begin = running_.find(job.job_id);
      const bool matched = begin != running_.end();
      const uint64_t begin_op = matched ? begin->second.begin.op : 0;
      const double wait_ops = matched ? begin->second.wait_ops : -1;
      if (matched) running_.erase(begin);
      if (!in_range) break;
      if (wait_ops >= 0) AttributeWait(job.start_level, wait_ops, parts);
      LevelStats& s = levels[job.start_level];
      s.released = true;
      s.last_release_op = job.op;
      s.last_end_micros = job.t_micros;
      const bool out = job.output_level >= 0 && job.output_level < n &&
                       job.output_level != job.start_level;
      if (!job.ok || !out) break;
      if (job.trivial) {
        s.moves += 1;
        s.trivial_s += static_cast<double>(job.s);
        levels[job.output_level].inflow_bytes += static_cast<double>(job.s);
      } else {
        s.merges += 1;
        s.merged_s += static_cast<double>(job.s);
        s.merged_o += static_cast<double>(job.o);
        s.merged_x += static_cast<double>(job.x);
        levels[job.output_level].inflow_bytes +=
            static_cast<double>(job.x) - static_cast<double>(job.o);
        if (matched && job.op >= begin_op) {
          s.job_ops += static_cast<double>(job.op - begin_op);
          s.jobs += 1;
        }
      }
      break;
    }
  }
  if (job.kind != RLJobRecord::Kind::kCompactionBegin) {
    AttributeJobEnd(job, parts);
  }
}

int Stats::RunningLevel(int snapshot_level) const {
  if (!saw_compaction_) return snapshot_level;
  return running_.empty() ? -1 : running_.rbegin()->second.begin.start_level;
}

const RLJobRecord* Stats::RunningJob() const {
  return running_.empty() ? nullptr : &running_.rbegin()->second.begin;
}

const RLJobRecord* Stats::RunningFlush() const {
  return flushes_running_.empty() ? nullptr
                                  : &flushes_running_.rbegin()->second;
}

void Stats::AddCharge(int level, bool trivial, double read, double write) {
  if (level < 0 || level >= static_cast<int>(levels.size())) return;
  LevelStats& s = levels[level];
  (trivial ? s.move_intf_rd : s.merge_intf_rd) += read;
  (trivial ? s.move_intf_wr : s.merge_intf_wr) += write;
}

void FillCostModel2(View* v, const Stats& st,
                    const ROCKSDB_NAMESPACE::RLStepCounts& now,
                    const Config& cfg) {
  const int n = v->num_levels;
  for (auto* field : {&v->own_ops, &v->own_bytes, &v->own_intf_rd,
                      &v->own_intf_wr}) {
    field->assign(n, 0.0);
  }
  for (auto* field :
       {&v->c_job, &v->c_tm, &v->intf_rd, &v->intf_wr, &v->tm_rd, &v->tm_wr}) {
    field->assign(n, kNaN);
  }
  v->flush_running = st.RunningFlush() != nullptr;
  const RLJobRecord* job = st.RunningJob();
  v->slot_trivial = job != nullptr && job->trivial;
  const bool priced = cfg.cost_model == 2;
  if (job != nullptr && job->start_level >= 0 && job->start_level < n) {
    const int i = job->start_level;
    const double s = static_cast<double>(job->s);
    const double o = static_cast<double>(job->o);
    const uint64_t op_now = StepOps(now);
    const uint64_t op_begin = StepOps(job->steps);
    v->own_ops[i] =
        op_now >= op_begin ? static_cast<double>(op_now - op_begin) : 0.0;
    v->own_bytes[i] = s + o;
    if (priced) {
      // X is not known until the end: the level's measured rho (bytes
      // written per source byte, net of the overlap) predicts it.
      const double rho = std::isfinite(v->rho[i]) ? v->rho[i] : 1.0;
      const double x = job->trivial ? 0.0 : rho * s + o;
      const int kind = KindOf(false, job->trivial, i);
      StepArray counts = TypeCounts(now);
      const StepArray begin = TypeCounts(job->steps);
      for (int t = 0; t < kNumStepTypes; ++t) counts[t] -= begin[t];
      Interference(cfg, kind, s, o, x, Rho(cfg, counts, v->own_ops[i]),
                   &v->own_intf_rd[i], &v->own_intf_wr[i]);
    }
  }
  if (!priced) {
    for (auto* field : {&v->own_intf_rd, &v->own_intf_wr}) {
      field->assign(n, kNaN);
    }
    return;
  }
  for (int i = 0; i < n && i < static_cast<int>(st.levels.size()); ++i) {
    const LevelStats& s = st.levels[i];
    const double per_merge = Ratio(s.merged_s, s.merges);
    const double per_move = Ratio(s.trivial_s, s.moves);
    v->c_job[i] = Ratio(cfg.JobPrice(i == 0 ? kL0Merge : kDeepMerge),
                        cfg.c_w * per_merge);
    v->c_tm[i] = Ratio(cfg.JobPrice(kMove), cfg.c_w * per_move);
    v->intf_rd[i] = Ratio(s.merge_intf_rd, cfg.c_w * s.merged_s);
    v->intf_wr[i] = Ratio(s.merge_intf_wr, cfg.c_w * s.merged_s);
    v->tm_rd[i] = Ratio(s.move_intf_rd, cfg.c_w * s.trivial_s);
    v->tm_wr[i] = Ratio(s.move_intf_wr, cfg.c_w * s.trivial_s);
  }
}

View MakeView(const RLHostOptions& o, const RLTreeSnapshot& snap,
              const Stats& st, const RLOpCounts& ops,
              const std::vector<RLLevelReadCounts>& reads,
              const std::vector<double>& m, int K0) {
  View v;
  const int n = o.num_levels;
  v.num_levels = n;
  v.T = o.level_multiplier;
  v.k0_cfg = o.l0_trigger;
  v.k_slow = o.l0_slowdown_trigger;
  v.write_buffer = static_cast<double>(o.write_buffer_size);
  v.m = m;
  v.m.resize(n, 1.0);
  v.K0 = K0;
  v.op = ops.total();
  v.C.assign(n, 0);
  v.B.assign(n, 0);
  v.phi.assign(n, kNaN);
  v.score.assign(n, 0);
  for (int i = 0; i < n; ++i) {
    const RLLevelSnapshot l = i < static_cast<int>(snap.levels.size())
                                  ? snap.levels[i]
                                  : RLLevelSnapshot();
    v.B[i] =
        static_cast<double>(l.bytes - std::min(l.bytes_compacting, l.bytes));
    if (i == 0) {
      v.score[0] = l.score;
      v.k0_all = l.num_files;
      v.k0 = l.num_files - std::min(l.num_files_compacting, l.num_files);
      continue;
    }
    v.C[i] = static_cast<double>(o.base_level_bytes) * std::pow(v.T, i - 1);
    v.phi[i] = v.B[i] / v.C[i];
    v.score[i] = v.phi[i] / v.m[i];
    if (l.bytes > 0) v.last = i;
  }
  v.running = st.RunningLevel(snap.running_start_level);
  v.backlog = Ratio(static_cast<double>(snap.pending_compaction_bytes),
                    static_cast<double>(snap.live_sst_bytes));
  v.l0_slowdown = Ratio(v.k_slow - v.k0_all, v.k_slow);
  v.mem_fill =
      Ratio(static_cast<double>(snap.active_memtable_bytes), v.write_buffer);

  const auto since = [](uint64_t now, uint64_t start) {
    return now >= start ? static_cast<double>(now - start) : 0.0;
  };
  v.ops = since(ops.total(), st.ops0.total());
  v.gets = since(ops.keys_read, st.ops0.keys_read);
  v.scans = since(ops.seeks, st.ops0.seeks);
  v.writes = since(ops.keys_written, st.ops0.keys_written);
  v.user_bytes = since(ops.bytes_written, st.ops0.bytes_written);
  v.flushes = st.flushes;
  v.flush_bytes = st.flush_bytes;
  if (st.flush_file_bytes > 0) v.F = st.flush_file_bytes;

  for (auto* field : {&v.probes, &v.fp_reads, &v.hit_reads, &v.seeks,
                      &v.get_reopens, &v.iter_reopens, &v.rho, &v.xi,
                      &v.overlap, &v.rho_tilde, &v.inflow, &v.job_ops, &v.N,
                      &v.since_release, &v.hidden}) {
    field->assign(n, kNaN);
  }
  const auto raw_hidden = [&](int i) {
    return i < static_cast<int>(reads.size()) &&
                   i < static_cast<int>(st.reads0.size())
               ? since(reads[i].hidden_steps, st.reads0[i].hidden_steps)
               : 0.0;
  };
  for (int i = 0; i < n; ++i) {
    v.hidden[i] = i == 0 ? raw_hidden(0) + raw_hidden(1) : raw_hidden(i + 1);
  }
  // Until FillCostModel2: no job in flight, nothing priced.
  for (auto* field : {&v.own_ops, &v.own_bytes}) field->assign(n, 0.0);
  for (auto* field : {&v.own_intf_rd, &v.own_intf_wr, &v.c_job, &v.c_tm,
                      &v.intf_rd, &v.intf_wr, &v.tm_rd, &v.tm_wr}) {
    field->assign(n, kNaN);
  }
  for (int i = 0; i < n; ++i) {
    if (i < static_cast<int>(reads.size()) &&
        i < static_cast<int>(st.reads0.size())) {
      const RLLevelReadCounts& now = reads[i];
      const RLLevelReadCounts& start = st.reads0[i];
      v.probes[i] = since(now.probes, start.probes);
      v.hit_reads[i] = since(now.filter_hits, start.filter_hits);
      v.fp_reads[i] =
          since(now.filter_passes, start.filter_passes) - v.hit_reads[i];
      v.seeks[i] = since(now.seeks, start.seeks);
      v.get_reopens[i] = since(now.get_reopens, start.get_reopens);
      v.iter_reopens[i] = since(now.iter_reopens, start.iter_reopens);
    }
    if (i >= static_cast<int>(st.levels.size())) continue;
    const LevelStats& s = st.levels[i];
    v.rho[i] = Ratio(s.merged_x - s.merged_o, s.merged_s);
    v.overlap[i] = Ratio(s.merged_o, s.merged_s);
    v.xi[i] = Ratio(s.trivial_s, s.trivial_s + s.merged_s);
    v.rho_tilde[i] = s.merged_s > 0 ? v.xi[i] + (1 - v.xi[i]) * v.rho[i]
                                    : (s.trivial_s > 0 ? 1.0 : kNaN);
    v.inflow[i] = s.inflow_bytes;
    v.job_ops[i] = Ratio(s.job_ops, s.jobs);
    v.since_release[i] = s.released ? since(v.op, s.last_release_op) : v.ops;
    // N_i = C_i q / lambda_i; for L0, C_0 = K0_cfg F with F held fixed.
    v.N[i] = i == 0 ? Ratio(v.k0_cfg * v.F * v.ops, st.flush_bytes)
                    : Ratio(v.C[i] * v.ops, s.inflow_bytes);
  }
  return v;
}

double Fanout(const View& v, int level) {
  if (level == 0 && v.num_levels > 1) {
    return Ratio(v.m[1] * v.C[1], v.K0 * v.F);
  }
  if (level >= 1 && level <= v.last - 2)
    return v.T * v.m[level + 1] / v.m[level];
  if (level >= 1 && level == v.last - 1) {
    return Ratio(v.B[v.last], v.m[level] * v.C[level]);
  }
  return kNaN;
}

int QueuePosition(const View& v, int level, double score, double epsilon) {
  const double bar = std::max(score, 1 + epsilon);
  int count = 0;
  for (int i = 0; i < v.num_levels; ++i) {
    if (i != level && v.score[i] > bar) ++count;
  }
  return count;
}

double FalsePositiveRate(const View& v) {
  double fp = 0, misses = 0;
  for (size_t i = 0; i < v.probes.size(); ++i) {
    if (!std::isfinite(v.probes[i])) continue;
    fp += v.fp_reads[i];
    misses += v.probes[i] - v.hit_reads[i];
  }
  return Ratio(fp, misses);
}

void PriceRatios(const View& v, const Config& cfg, double* r_f, double* r_b,
                 double* r_sk, double* r_o) {
  // Per operation instead of per second: q_pt / lambda_1 is the same ratio.
  const double lambda1 = v.num_levels > 1 ? Ratio(v.inflow[1], v.ops) : kNaN;
  const double denominator = cfg.c_w * lambda1;
  *r_f = Ratio(Ratio(v.gets, v.ops) * cfg.c_f, denominator);
  *r_b = Ratio(Ratio(v.gets, v.ops) * cfg.c_blk, denominator);
  *r_sk = Ratio(Ratio(v.scans, v.ops) * cfg.c_sk, denominator);
  // Reopens are counted per operation (e^o_i), so q / lambda_1 = 1 / lambda_1.
  *r_o = Ratio(cfg.c_open, denominator);
}

void L0ReadPrices(const View& v, const Config& cfg, double* per_get,
                  double* per_scan) {
  const auto known = [](double x) { return std::isfinite(x) ? x : 0.0; };
  const bool l0 = !v.probes.empty();
  const double get_reopen =
      l0 ? known(Ratio(v.get_reopens[0], v.probes[0])) : 0.0;
  const double iter_reopen =
      l0 ? known(Ratio(v.iter_reopens[0], v.seeks[0])) : 0.0;
  *per_get =
      cfg.c_f + FalsePositiveRate(v) * cfg.c_blk + get_reopen * cfg.c_open;
  *per_scan = cfg.c_sk + iter_reopen * cfg.c_open;
}

const char* AgentName(Agent agent) {
  switch (agent) {
    case Agent::kL0:
      return "l0";
    case Agent::kInterior:
      return "interior";
    case Agent::kLast:
      return "last";
  }
  return "unknown";
}

namespace {

// H §2's inputs for the cost-model-2 terms (amended 2026-10-03, D-23), the
// same for every agent, after its own list.
const std::vector<std::string>& CostModel2Names() {
  static const std::vector<std::string> kNames = {
      "slot_move", "flush_running", "own_ops", "own_bytes", "own_intf_rd",
      "own_intf_wr", "rho0", "rho1", "rho0_kb", "rho1_kb", "rho0_kj",
      "rho1_kj", "e_hd", "R_st", "c_job", "c_tm", "intf_rd", "intf_wr",
      "tm_rd", "tm_wr"};
  return kNames;
}

std::vector<std::string> WithCostModel2(std::vector<std::string> names) {
  names.insert(names.end(), CostModel2Names().begin(),
               CostModel2Names().end());
  return names;
}

}  // namespace

const std::vector<std::string>& FeatureNames(Agent agent) {
  static const std::vector<std::string> kInterior = WithCostModel2({"phi",
                                                     "anchor",
                                                     "timing",
                                                     "score",
                                                     "since_release",
                                                     "phi_up",
                                                     "m_up",
                                                     "phi_down",
                                                     "m_down",
                                                     "burst",
                                                     "burst_absent",
                                                     "wait",
                                                     "busy_share",
                                                     "inflow_ratio",
                                                     "queue",
                                                     "slot_none",
                                                     "slot_above",
                                                     "slot_same",
                                                     "slot_below",
                                                     "backlog",
                                                     "l0_slowdown",
                                                     "fill_two_down",
                                                     "two_down_absent",
                                                     "last_room",
                                                     "rho",
                                                     "xi",
                                                     "overlap_c",
                                                     "pi",
                                                     "e_f",
                                                     "e_b",
                                                     "nu",
                                                     "e_o",
                                                     "sigma",
                                                     "R_f",
                                                     "R_b",
                                                     "R_sk",
                                                     "R_o",
                                                     "beta_w",
                                                     "beta_r",
                                                     "beta_s",
                                                     "l0_fill"});
  // The last-level agent also sees k0 / K0 (H §2 item 3).
  static const std::vector<std::string> kLast = WithCostModel2(
      {"fill", "headroom", "anchor", "timing", "burst", "queue", "slot_none",
       "slot_above", "slot_same", "slot_below", "backlog", "l0_slowdown",
       "l0_fill"});
  static const std::vector<std::string> kL0 = WithCostModel2({
      "l0_fill",       "anchor",
      "trigger",       "flush_ratio",
      "phi_1",         "get_write",
      "scan_write",    "R_f",
      "R_b",           "R_sk",
      "R_o",           "e_o",
      "beta_w",        "beta_r",
      "beta_s",        "busy_share",
      "queue",         "slot_none",
      "slot_same",     "slot_below",
      "backlog",       "l0_slowdown",
      "fill_two_down", "two_down_absent",
      "mem_fill"});
  switch (agent) {
    case Agent::kL0:
      return kL0;
    case Agent::kInterior:
      return kInterior;
    case Agent::kLast:
      return kLast;
  }
  return kInterior;
}

namespace {

// Where the running job's start level lies: none, above, same, below.
std::vector<double> SlotOneHot(int running, int level, bool with_above) {
  const int where = running < 0        ? 0
                    : running < level  ? 1
                    : running == level ? 2
                                       : 3;
  std::vector<double> hot = {where == 0 ? 1.0 : 0.0, where == 1 ? 1.0 : 0.0,
                             where == 2 ? 1.0 : 0.0, where == 3 ? 1.0 : 0.0};
  if (!with_above) hot.erase(hot.begin() + 1);
  return hot;
}

void Append(std::vector<double>* out, const std::vector<double>& more) {
  out->insert(out->end(), more.begin(), more.end());
}

// CostModel2Names()'s values for level i over its interval just closed (H §2
// items 1-5). L0's divisor is C_0 = K0_cfg F, its decision interval
// N_0 / K0_cfg, and its iteration input is per L0 file.
// v's vector at i, or `missing` when the View does not carry it.
double At(const std::vector<double>& values, int i, double missing) {
  return i >= 0 && i < static_cast<int>(values.size()) ? values[i] : missing;
}

std::vector<double> CostModel2Features(const View& v, int i,
                                       const LevelParts& p, const Config& cfg) {
  const bool l0 = i == 0;
  const double C = l0 ? v.k0_cfg * v.F : At(v.C, i, kNaN);
  const double per_turnover = l0 ? v.k0_cfg : cfg.k;
  const double interval = Ratio(At(v.N, i, kNaN), per_turnover);
  const bool priced = cfg.cost_model == 2;
  const auto price = [&](double x) { return priced ? x : kNaN; };
  // Read steps by type, L0's part and the rest (types as cost_model_v2's;
  // step = returned + hidden entries; every read step counts, the shared
  // buckets' included, H §2 item 3).
  StepArray all{}, zero{};
  all[kProbe] = p.fg_probes;
  all[kBlock] = p.fg_block_probes;
  all[kSeek] = p.fg_run_seeks;
  all[kReopen] = p.fg_reopens;
  all[kStep] = p.fg_nexts_found + p.fg_iter_skips;
  all[kMemtable] = p.gets + p.scans;
  all[kGet0] = p.gets;
  all[kScan0] = p.scans;
  zero[kProbe] = p.l0_probes;
  zero[kBlock] = p.l0_block_probes;
  zero[kSeek] = p.l0_seeks;
  zero[kReopen] = p.l0_reopens;
  zero[kStep] = p.l0_hidden;
  StepArray rest{};
  for (int x = 0; x < kNumStepTypes; ++x) {
    rest[x] = std::max(0.0, all[x] - zero[x]);
  }
  const StepArray rho0 = Rho(cfg, zero, p.k0_ops);
  const StepArray rho1 = Rho(cfg, rest, p.ops);
  const int kind = l0 ? kL0Merge : kDeepMerge;
  double plain0 = 0, plain1 = 0, kb0 = 0, kb1 = 0, kj0 = 0, kj1 = 0;
  for (int x = 0; x < kNumReadTypes; ++x) {
    plain0 += rho0[x];
    plain1 += rho1[x];
    kb0 += cfg.v2.kappa_b[x] * rho0[x];
    kb1 += cfg.v2.kappa_b[x] * rho1[x];
    kj0 += cfg.v2.kappa_j[x][kind] * rho0[x];
    kj1 += cfg.v2.kappa_j[x][kind] * rho1[x];
  }
  // G.2's units: per operation over c_w lambda_1 (lambda_1 per operation).
  const double lambda1 = v.num_levels > 1 ? Ratio(v.inflow[1], v.ops) : kNaN;
  const double unit = cfg.c_w * lambda1;
  const bool l0_seen = p.k0_ops > 0;
  const double hidden = p.hidden_steps - p.slot_out_hidden + p.slot_in_hidden;
  const double mean_k0 = Ratio(p.k0_ops, p.ops);
  return {v.slot_trivial ? 1.0 : 0.0,
          v.flush_running ? 1.0 : 0.0,
          Ratio(At(v.own_ops, i, 0.0), interval),
          Ratio(At(v.own_bytes, i, 0.0), C),
          price(Ratio(At(v.own_intf_rd, i, kNaN), cfg.c_w * C)),
          price(Ratio(At(v.own_intf_wr, i, kNaN), cfg.c_w * C)),
          price(l0_seen ? Ratio(plain0, unit) : kNaN),
          price(Ratio(plain1, unit)),
          price(l0_seen ? cfg.q_bar * kb0 / cfg.c_w : kNaN),
          price(cfg.q_bar * kb1 / cfg.c_w),
          price(l0_seen ? Ratio(cfg.q_bar * kj0, cfg.v2.p_dev) : kNaN),
          price(Ratio(cfg.q_bar * kj1, cfg.v2.p_dev)),
          l0 ? Ratio(Ratio(hidden, p.scans), mean_k0) : Ratio(hidden, p.scans),
          price(Ratio(Ratio(v.scans, v.ops) * cfg.v2.c_st, unit)),
          At(v.c_job, i, kNaN),
          At(v.c_tm, i, kNaN),
          At(v.intf_rd, i, kNaN),
          At(v.intf_wr, i, kNaN),
          At(v.tm_rd, i, kNaN),
          At(v.tm_wr, i, kNaN)};
}

}  // namespace

std::vector<double> LevelFeatures(const View& v, int j, bool last,
                                  const LevelControl& c,
                                  const LevelParts& interval, double wait_carry,
                                  const Config& cfg) {
  const double m = c.m();
  const double phi = v.phi[j];
  const double s = phi / m;
  const double queue = QueuePosition(v, j, s, cfg.bounds.epsilon);
  // G §3: the share of level j's capacity about to land from above, j >= 2.
  const double burst =
      j >= 2
          ? v.rho_tilde[j - 1] * std::max(0.0, v.phi[j - 1] - v.m[j - 1]) / v.T
          : 0.0;
  std::vector<double> f;
  if (last) {
    f = {s, m - phi, c.anchor, c.timing, burst, queue};
    Append(&f, SlotOneHot(v.running, j, true));
    Append(&f, {v.backlog, v.l0_slowdown, Ratio(v.k0, v.K0)});
    Append(&f, CostModel2Features(v, j, interval, cfg));
    return f;
  }
  const double N = v.N[j];
  const double wait =
      interval.waits > 0 ? interval.wait_ops / interval.waits : wait_carry;
  // L1's upper neighbour is L0: its fill against its trigger, multiplier 1.
  const double phi_up = j >= 2 ? v.phi[j - 1] : Ratio(v.k0, v.K0);
  const double m_up = j >= 2 ? v.m[j - 1] : 1.0;
  const bool two_down = j + 2 <= v.last;
  const int L = v.last;
  const double capacity = v.m[L] * v.C[L];
  double pi = 1;
  for (int k = 1; k < j; ++k) pi *= v.rho_tilde[k];
  double r_f = 0, r_b = 0, r_sk = 0, r_o = 0;
  PriceRatios(v, cfg, &r_f, &r_b, &r_sk, &r_o);
  f = {phi, c.anchor, c.timing, s, Ratio(v.since_release[j], N), phi_up, m_up,
       v.phi[j + 1], v.m[j + 1], burst,
       // G §3 defines the burst for j >= 2 only: L1's inflow is L0's
       // flushes, which its state reads through phi_up.
       j >= 2 ? 0.0 : 1.0, Ratio(wait, N / cfg.k),
       Ratio(interval.busy_ops, interval.ops),
       Ratio(Ratio(interval.inflow_bytes, interval.ops),
             Ratio(v.inflow[j], v.ops)),
       queue};
  Append(&f, SlotOneHot(v.running, j, true));
  Append(&f, {v.backlog,
              v.l0_slowdown,
              two_down ? v.phi[j + 2] / v.m[j + 2] : 0.0,
              two_down ? 0.0 : 1.0,
              Ratio(std::max(0.0, capacity - v.B[L]), capacity),
              v.rho[j],
              v.xi[j],
              v.overlap[j] / Fanout(v, j),
              pi,
              Ratio(v.probes[j], v.gets),
              Ratio(v.fp_reads[j], v.gets),
              Ratio(v.seeks[j], v.scans),
              Ratio(v.get_reopens[j] + v.iter_reopens[j], v.ops),
              cfg.c_s * N / (cfg.c_w * cfg.q_bar),
              r_f,
              r_b,
              r_sk,
              r_o,
              cfg.beta_w,
              cfg.beta_r,
              cfg.beta_s,
              Ratio(v.k0, v.K0)});
  Append(&f, CostModel2Features(v, j, interval, cfg));
  return f;
}

std::vector<double> L0Features(const View& v, const L0Control& c,
                               const LevelParts& interval, const Config& cfg) {
  double r_f = 0, r_b = 0, r_sk = 0, r_o = 0;
  PriceRatios(v, cfg, &r_f, &r_b, &r_sk, &r_o);
  const bool two_down = 2 <= v.last;
  std::vector<double> f = {
      Ratio(v.k0, v.K0),
      Ratio(c.anchor, v.k0_cfg),
      Ratio(v.K0, v.k0_cfg),
      Ratio(Ratio(interval.inflow_bytes, interval.ops),
            Ratio(v.flush_bytes, v.ops)),
      v.num_levels > 1 ? v.phi[1] : kNaN,
      Ratio(interval.gets, interval.writes),
      Ratio(interval.scans, interval.writes),
      r_f,
      r_b,
      r_sk,
      r_o,
      Ratio(v.get_reopens[0] + v.iter_reopens[0], v.ops),
      cfg.beta_w,
      cfg.beta_r,
      cfg.beta_s,
      Ratio(interval.busy_ops, interval.ops),
      static_cast<double>(QueuePosition(v, 0, v.score[0], cfg.bounds.epsilon))};
  Append(&f, SlotOneHot(v.running, 0, false));
  Append(&f, {v.backlog, v.l0_slowdown, two_down ? v.phi[2] / v.m[2] : 0.0,
              two_down ? 0.0 : 1.0, v.mem_fill});
  Append(&f, CostModel2Features(v, 0, interval, cfg));
  return f;
}

}  // namespace rlc
