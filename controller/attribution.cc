#include "attribution.h"

#include <algorithm>

namespace rlc {

using ROCKSDB_NAMESPACE::RLJobRecord;

namespace {

bool InRange(int level, const std::vector<LevelParts>& parts) {
  return level >= 0 && level < static_cast<int>(parts.size());
}

}  // namespace

void AttributeSegment(const Segment& seg, std::vector<LevelParts>* parts) {
  const double ops = static_cast<double>(seg.ops.total());
  for (LevelParts& p : *parts) {
    p.ops += ops;
    p.gets += static_cast<double>(seg.ops.keys_read);
    p.scans += static_cast<double>(seg.ops.seeks);
    p.writes += static_cast<double>(seg.ops.keys_written);
    p.user_bytes += static_cast<double>(seg.ops.bytes_written);
    if (seg.slot_level >= 0) p.busy_ops += ops;
  }
  for (size_t i = 0; i < parts->size() && i < seg.held.size(); ++i) {
    (*parts)[i].held_byte_ops += seg.held[i] * ops;
  }
  const size_t levels = std::min(parts->size(), seg.reads.size());
  double placed_hidden = 0;
  for (size_t i = 0; i < levels; ++i) {
    const auto& r = seg.reads[i];
    LevelParts& p = (*parts)[i];
    p.probes += static_cast<double>(r.probes);
    p.fp_reads += static_cast<double>(r.filter_passes) -
                  static_cast<double>(r.filter_hits);
    p.seeks += static_cast<double>(r.seeks);
    p.hit_reads += static_cast<double>(r.filter_hits);
    p.reopens += static_cast<double>(r.get_reopens) +
                 static_cast<double>(r.iter_reopens);
    const double hidden = static_cast<double>(r.hidden_steps);
    placed_hidden += hidden;
    (*parts)[i == 0 ? 0 : i - 1].hidden_steps += hidden;
  }
  const auto& st = seg.steps;
  double l0_probes = 0, l0_block = 0, l0_seeks = 0, l0_reopens = 0,
         l0_hidden = 0;
  if (!seg.reads.empty()) {
    const auto& l0 = seg.reads[0];
    l0_probes = static_cast<double>(l0.probes);
    l0_block = static_cast<double>(l0.filter_passes);
    l0_seeks = static_cast<double>(l0.seeks);
    l0_reopens = static_cast<double>(l0.get_reopens) +
                 static_cast<double>(l0.iter_reopens);
    l0_hidden = static_cast<double>(l0.hidden_steps) +
                (seg.reads.size() > 1
                     ? static_cast<double>(seg.reads[1].hidden_steps)
                     : 0.0);
  }
  for (LevelParts& p : *parts) {
    p.fg_probes += static_cast<double>(st.probes);
    p.fg_block_probes += static_cast<double>(st.block_probes);
    p.fg_run_seeks += static_cast<double>(st.run_seeks);
    p.fg_reopens += static_cast<double>(st.reopens);
    p.fg_nexts_found += static_cast<double>(st.nexts_found);
    p.fg_iter_skips += static_cast<double>(st.iter_skips);
    p.memtable_hidden +=
        std::max(0.0, static_cast<double>(st.iter_skips) - placed_hidden);
    p.k0_ops += static_cast<double>(seg.k0) * ops;
    p.l0_probes += l0_probes;
    p.l0_block_probes += l0_block;
    p.l0_seeks += l0_seeks;
    p.l0_reopens += l0_reopens;
    p.l0_hidden += l0_hidden;
  }
  if (!seg.l0_due || seg.slot_level < 1 || !InRange(seg.slot_level, *parts) ||
      seg.reads.empty() || seg.k0 <= seg.K0) {
    return;
  }
  const double share = static_cast<double>(seg.k0 - seg.K0) / seg.k0;
  const auto& l0 = seg.reads[0];
  const double probes = share * static_cast<double>(l0.probes);
  const double fp = share * (static_cast<double>(l0.filter_passes) -
                             static_cast<double>(l0.filter_hits));
  const double seeks = share * static_cast<double>(l0.seeks);
  const double reopens = share * (static_cast<double>(l0.get_reopens) +
                                  static_cast<double>(l0.iter_reopens));
  const double hidden = share * l0_hidden;
  LevelParts& from = (*parts)[0];
  from.slot_out_probes += probes;
  from.slot_out_fp_reads += fp;
  from.slot_out_seeks += seeks;
  from.slot_out_reopens += reopens;
  from.slot_out_hidden += hidden;
  LevelParts& to = (*parts)[seg.slot_level];
  to.slot_in_probes += probes;
  to.slot_in_fp_reads += fp;
  to.slot_in_seeks += seeks;
  to.slot_in_reopens += reopens;
  to.slot_in_hidden += hidden;
}

void AttributeJobEnd(const RLJobRecord& job, std::vector<LevelParts>* parts) {
  if (!job.ok) return;
  const double x = static_cast<double>(job.x);
  if (job.kind == RLJobRecord::Kind::kFlushEnd) {
    if (InRange(0, *parts)) {
      (*parts)[0].write_bytes += x;
      (*parts)[0].inflow_bytes += x;
      (*parts)[0].jobs_flush += 1;
    }
    return;
  }
  if (job.kind != RLJobRecord::Kind::kCompactionEnd) return;
  if (InRange(job.start_level, *parts)) {
    LevelParts& p = (*parts)[job.start_level];
    if (job.trivial) {
      p.jobs_move += 1;
    } else {
      p.write_bytes += x;
      p.read_bytes +=
          static_cast<double>(job.s) + static_cast<double>(job.o);
      (job.start_level == 0 ? p.jobs_l0 : p.jobs_deep) += 1;
    }
  }
  if (job.output_level != job.start_level &&
      InRange(job.output_level, *parts)) {
    (*parts)[job.output_level].inflow_bytes +=
        job.trivial ? static_cast<double>(job.s)
                    : x - static_cast<double>(job.o);
  }
}

void AttributeWait(int level, double wait_ops, std::vector<LevelParts>* parts) {
  if (!InRange(level, *parts)) return;
  (*parts)[level].wait_ops += wait_ops;
  (*parts)[level].waits += 1;
}

}  // namespace rlc
