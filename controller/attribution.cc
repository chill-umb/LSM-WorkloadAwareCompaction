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
  LevelParts& from = (*parts)[0];
  from.slot_out_probes += probes;
  from.slot_out_fp_reads += fp;
  from.slot_out_seeks += seeks;
  from.slot_out_reopens += reopens;
  LevelParts& to = (*parts)[seg.slot_level];
  to.slot_in_probes += probes;
  to.slot_in_fp_reads += fp;
  to.slot_in_seeks += seeks;
  to.slot_in_reopens += reopens;
}

void AttributeJobEnd(const RLJobRecord& job, std::vector<LevelParts>* parts) {
  if (!job.ok) return;
  const double x = static_cast<double>(job.x);
  if (job.kind == RLJobRecord::Kind::kFlushEnd) {
    if (InRange(0, *parts)) {
      (*parts)[0].write_bytes += x;
      (*parts)[0].inflow_bytes += x;
    }
    return;
  }
  if (job.kind != RLJobRecord::Kind::kCompactionEnd) return;
  if (!job.trivial && InRange(job.start_level, *parts)) {
    (*parts)[job.start_level].write_bytes += x;
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
