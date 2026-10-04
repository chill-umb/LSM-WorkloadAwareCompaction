#include "jobs.h"

namespace rlc {

using ROCKSDB_NAMESPACE::RLStepCounts;

StepArray TypeCounts(const RLStepCounts& s) {
  StepArray c{};
  c[kProbe] = static_cast<double>(s.probes);
  c[kBlock] = static_cast<double>(s.block_probes);
  c[kSeek] = static_cast<double>(s.run_seeks);
  c[kReopen] = static_cast<double>(s.reopens);
  c[kStep] = static_cast<double>(s.nexts_found) +
             static_cast<double>(s.iter_skips);
  c[kIBlock] = 0;
  c[kMemtable] = static_cast<double>(s.gets) + static_cast<double>(s.scans);
  c[kGet0] = static_cast<double>(s.gets);
  c[kScan0] = static_cast<double>(s.scans);
  c[kPut] = static_cast<double>(s.puts);
  return c;
}

uint64_t StepOps(const RLStepCounts& s) { return s.puts + s.gets + s.scans; }

void StepRing::Reset(const StepSample& floor) {
  floor_ = floor;
  samples_.clear();
}

void StepRing::Add(const StepSample& sample, uint64_t keep_ops) {
  if (!samples_.empty() && sample.op <= samples_.back().op) return;
  if (samples_.empty() && sample.op <= floor_.op) return;
  samples_.push_back(sample);
  const uint64_t newest = samples_.back().op;
  const uint64_t keep_from = newest > keep_ops ? newest - keep_ops : 0;
  while (samples_.size() > 1 && samples_[1].op <= keep_from) {
    samples_.pop_front();
  }
}

const StepSample& StepRing::StartFor(uint64_t target) const {
  const StepSample* start = &floor_;
  for (const StepSample& s : samples_) {
    if (s.op > target) break;
    if (s.op >= start->op) start = &s;
  }
  return *start;
}

namespace {

StepArray Difference(const StepArray& before, const StepArray& after) {
  StepArray d{};
  for (int x = 0; x < kNumStepTypes; ++x) d[x] = after[x] - before[x];
  return d;
}

}  // namespace

Window JobWindow(bool has_begin, const StepSample& begin,
                 const StepSample& end, const StepRing& ring, int n_win) {
  Window w;
  const StepArray end_counts = TypeCounts(end.steps);
  if (has_begin && end.op >= begin.op &&
      end.op - begin.op >= static_cast<uint64_t>(n_win)) {
    w.start_op = begin.op;
    w.ops = static_cast<double>(end.op - begin.op);
    w.counts = Difference(TypeCounts(begin.steps), end_counts);
    w.own = true;
    return w;
  }
  const uint64_t target =
      end.op > static_cast<uint64_t>(n_win) ? end.op - n_win : 0;
  const StepSample& start = ring.StartFor(target);
  w.start_op = start.op;
  w.ops = end.op >= start.op ? static_cast<double>(end.op - start.op) : 0;
  w.counts = Difference(TypeCounts(start.steps), end_counts);
  return w;
}

int KindOf(bool flush, bool trivial, int start_level) {
  if (flush) return kFlush;
  if (trivial) return kMove;
  return start_level == 0 ? kL0Merge : kDeepMerge;
}

double JobTau(const Config& cfg, int kind, double s, double o, double x) {
  if (kind == kMove) return cfg.JobPrice(kMove);
  return cfg.JobPrice(kind) + cfg.v2.c_cr * (s + o) + cfg.c_w * x;
}

double JobBytes(const Config& cfg, int kind, double s, double o, double x) {
  return kind == kMove ? 0.0 : x + cfg.v2.lambda * (s + o);
}

StepArray Rho(const Config& cfg, const StepArray& counts, double ops) {
  StepArray rho{};
  if (!(ops > 0)) return rho;
  for (int x = 0; x < kNumStepTypes; ++x) {
    rho[x] = cfg.BasePrice(x) * counts[x] / ops;
  }
  return rho;
}

void Interference(const Config& cfg, int kind, double s, double o, double x,
                  const StepArray& rho, double* read, double* write) {
  *read = *write = 0;
  if (cfg.cost_model != 2 || kind < 0 || kind >= kNumJobKinds) return;
  const double t_job = JobTau(cfg, kind, s, o, x) / cfg.v2.p_dev;
  const double y = JobBytes(cfg, kind, s, o, x);
  for (int t = 0; t < kNumStepTypes; ++t) {
    const double part =
        cfg.q_bar * rho[t] *
        (cfg.v2.kappa_b[t] * y + cfg.v2.kappa_j[t][kind] * t_job);
    (t < kNumReadTypes ? *read : *write) += part;
  }
}

}  // namespace rlc
