#include "run_workload.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <sstream>
#include <tuple>
#include <vector>

#include "config_options.h"
#include "db/compaction/rl_compaction_telemetry.h"
#include "event_listners.h"
#include "utils.h"

std::string buffer_file = "workload.log";
std::string stats_file = "stats.log";
std::string experiment_metrics_file = "experiment_metrics.json";
std::string lsm_metrics_file = "lsm_metrics.jsonl";

namespace {

struct OperationMetrics {
  uint64_t count = 0;
  uint64_t total_ns = 0;
  std::vector<uint64_t> latencies_ns;

  void Add(uint64_t ns) {
    ++count;
    total_ns += ns;
    latencies_ns.push_back(ns);
  }

  double AverageNs() const {
    return count == 0 ? 0.0 : static_cast<double>(total_ns) / count;
  }

  uint64_t PercentileNs(double percentile) const {
    if (latencies_ns.empty()) {
      return 0;
    }
    std::vector<uint64_t> sorted = latencies_ns;
    std::sort(sorted.begin(), sorted.end());
    const size_t index = std::min(
        sorted.size() - 1,
        static_cast<size_t>(std::ceil(percentile * sorted.size())) - 1);
    return sorted[index];
  }

  uint64_t P95Ns() const { return PercentileNs(0.95); }
  uint64_t P99Ns() const { return PercentileNs(0.99); }
};

struct WorkloadMetrics {
  OperationMetrics inserts;
  OperationMetrics updates;
  OperationMetrics deletes;
  OperationMetrics range_deletes;
  OperationMetrics gets;
  OperationMetrics scans;

  // Logical work, collected around the individual foreground operations so
  // warmup and the final live-data census cannot leak into the measurement.
  uint64_t user_logical_bytes_written = 0;
  uint64_t point_sst_probes = 0;
  uint64_t scan_returned_entries = 0;
  uint64_t scan_internal_entries_skipped = 0;
  uint64_t scan_sorted_run_seeks = 0;
  uint64_t empty_scans = 0;

  uint64_t WriteCount() const {
    return inserts.count + updates.count + deletes.count + range_deletes.count;
  }

  uint64_t ReadCount() const { return gets.count + scans.count; }

  uint64_t WriteTotalNs() const {
    return inserts.total_ns + updates.total_ns + deletes.total_ns +
           range_deletes.total_ns;
  }

  uint64_t ReadTotalNs() const { return gets.total_ns + scans.total_ns; }
};

struct TickerSnapshot {
  uint64_t stall_micros = 0;
  uint64_t write_stall_count = 0;
  uint64_t compact_read_bytes = 0;
  uint64_t compact_write_bytes = 0;
  uint64_t flush_write_bytes = 0;
  uint64_t point_sst_probes = 0;
  uint64_t iterator_internal_skips = 0;
  uint64_t sorted_run_seeks = 0;
};

struct FinalTreeStats {
  uint64_t total_sst_bytes = 0;
  uint64_t live_logical_bytes = 0;
  uint64_t live_entries = 0;
};

size_t MetricsSampleInterval() {
  const char *env = std::getenv("LSM_METRICS_SAMPLE_INTERVAL");
  if (env == nullptr) {
    return 1000;
  }
  long value = std::strtol(env, nullptr, 10);
  return value > 0 ? static_cast<size_t>(value) : 1000;
}

uint64_t MetricsWarmupOperations() {
  const char *env = std::getenv("LSM_METRICS_WARMUP_OPS");
  if (env == nullptr) {
    return 0;
  }
  long long value = std::strtoll(env, nullptr, 10);
  return value > 0 ? static_cast<uint64_t>(value) : 0;
}

uint64_t GetIntPropertyOrZero(DB *db, const std::string &name) {
  uint64_t value = 0;
  db->GetIntProperty(name, &value);
  return value;
}

uint64_t TickerCount(const Options &options, Tickers ticker) {
  return options.statistics ? options.statistics->getTickerCount(ticker) : 0;
}

uint64_t HistogramCount(const Options &options, Histograms histogram) {
  if (!options.statistics) {
    return 0;
  }
  HistogramData data;
  options.statistics->histogramData(histogram, &data);
  return data.count;
}

uint64_t CounterDelta(uint64_t current, uint64_t baseline) {
  return current >= baseline ? current - baseline : 0;
}

TickerSnapshot CaptureTickerSnapshot(const Options &options) {
  TickerSnapshot snapshot;
  snapshot.stall_micros = TickerCount(options, STALL_MICROS);
  snapshot.write_stall_count = HistogramCount(options, WRITE_STALL);
  snapshot.compact_read_bytes = TickerCount(options, COMPACT_READ_BYTES);
  snapshot.compact_write_bytes = TickerCount(options, COMPACT_WRITE_BYTES);
  snapshot.flush_write_bytes = TickerCount(options, FLUSH_WRITE_BYTES);
  snapshot.point_sst_probes = TickerCount(options, POINT_SST_PROBE);
  snapshot.iterator_internal_skips = TickerCount(options, NUMBER_ITER_SKIP);
  snapshot.sorted_run_seeks = TickerCount(options, SORTED_RUN_SEEK);
  return snapshot;
}

ExperimentTelemetrySnapshot
TelemetryDelta(const ExperimentTelemetrySnapshot &current,
               const ExperimentTelemetrySnapshot &baseline) {
  ExperimentTelemetrySnapshot delta;
  delta.flushed_bytes =
      CounterDelta(current.flushed_bytes, baseline.flushed_bytes);
  delta.compaction_bytes_read = CounterDelta(current.compaction_bytes_read,
                                             baseline.compaction_bytes_read);
  delta.compaction_bytes_written = CounterDelta(
      current.compaction_bytes_written, baseline.compaction_bytes_written);
  delta.compactions_completed = CounterDelta(current.compactions_completed,
                                             baseline.compactions_completed);
  delta.l0_compactions_completed = CounterDelta(
      current.l0_compactions_completed, baseline.l0_compactions_completed);
  delta.stall_events =
      CounterDelta(current.stall_events, baseline.stall_events);
  delta.stop_events = CounterDelta(current.stop_events, baseline.stop_events);
  delta.stall_duration_micros = CounterDelta(
      current.stall_duration_micros, baseline.stall_duration_micros);
  return delta;
}

TickerSnapshot TickerDelta(const TickerSnapshot &current,
                           const TickerSnapshot &baseline) {
  TickerSnapshot delta;
  delta.stall_micros =
      CounterDelta(current.stall_micros, baseline.stall_micros);
  delta.write_stall_count =
      CounterDelta(current.write_stall_count, baseline.write_stall_count);
  delta.compact_read_bytes =
      CounterDelta(current.compact_read_bytes, baseline.compact_read_bytes);
  delta.compact_write_bytes =
      CounterDelta(current.compact_write_bytes, baseline.compact_write_bytes);
  delta.flush_write_bytes =
      CounterDelta(current.flush_write_bytes, baseline.flush_write_bytes);
  delta.point_sst_probes =
      CounterDelta(current.point_sst_probes, baseline.point_sst_probes);
  delta.iterator_internal_skips = CounterDelta(
      current.iterator_internal_skips, baseline.iterator_internal_skips);
  delta.sorted_run_seeks =
      CounterDelta(current.sorted_run_seeks, baseline.sorted_run_seeks);
  return delta;
}

double AverageForCombined(uint64_t total_ns, uint64_t count) {
  return count == 0 ? 0.0 : static_cast<double>(total_ns) / count;
}

uint64_t P95ForCombined(const std::vector<const OperationMetrics *> &metrics) {
  std::vector<uint64_t> all;
  for (const auto *metric : metrics) {
    all.insert(all.end(), metric->latencies_ns.begin(),
               metric->latencies_ns.end());
  }
  if (all.empty()) {
    return 0;
  }
  std::sort(all.begin(), all.end());
  const size_t index = std::min(
      all.size() - 1,
      static_cast<size_t>(std::ceil(0.95 * all.size())) - 1);
  return all[index];
}

uint64_t PercentileForCombined(
    const std::vector<const OperationMetrics *> &metrics, double percentile) {
  std::vector<uint64_t> all;
  for (const auto *metric : metrics) {
    all.insert(all.end(), metric->latencies_ns.begin(),
               metric->latencies_ns.end());
  }
  if (all.empty()) return 0;
  std::sort(all.begin(), all.end());
  const size_t index = std::min(
      all.size() - 1,
      static_cast<size_t>(std::ceil(percentile * all.size())) - 1);
  return all[index];
}

double SafeRatio(uint64_t numerator, uint64_t denominator) {
  return denominator == 0
             ? 0.0
             : static_cast<double>(numerator) /
                   static_cast<double>(denominator);
}

void WriteOperationSummary(std::ofstream &out, const char *name,
                           const OperationMetrics &metric,
                           bool trailing_comma) {
  out << "    \"" << name << "\": {"
      << "\"count\": " << metric.count << ", "
      << "\"avg_ns\": " << std::fixed << std::setprecision(2)
      << metric.AverageNs() << ", "
      << "\"p95_ns\": " << metric.P95Ns() << ", "
      << "\"p99_ns\": " << metric.P99Ns() << "}";
  if (trailing_comma) {
    out << ",";
  }
  out << "\n";
}

// Result of draining outstanding compaction work before the DB is closed.
// Reported so that deferred-but-unpaid work is visible rather than silently
// discounted from the compaction totals.
struct DrainStats {
  bool enabled = false;
  bool ok = false;
  uint64_t wall_ns = 0;
  uint64_t pending_bytes_before = 0;
  uint64_t pending_bytes_after = 0;
  uint64_t compaction_bytes_read = 0;
  uint64_t compaction_bytes_written = 0;
  uint64_t compactions_completed = 0;
};

bool DrainBeforeClose() {
  const char *env = std::getenv("LSM_DRAIN_BEFORE_CLOSE");
  if (env == nullptr || *env == '\0') return true;
  return !(env[0] == '0' && env[1] == '\0');
}

void WriteLsmSample(DB *db, std::ofstream &out, uint64_t op_index,
                    uint64_t elapsed_ns, uint64_t warmup_operations,
                    const char *phase_override = nullptr) {
  ColumnFamilyMetaData metadata;
  db->GetColumnFamilyMetaData(&metadata);

  uint64_t l0_files = 0;
  uint64_t l0_size = 0;
  if (!metadata.levels.empty()) {
    l0_files = metadata.levels[0].files.size();
    l0_size = metadata.levels[0].size;
  }

  // Per-level shape of the tree at this instant. Empty levels are included so
  // the series shows how deep the tree actually grows over the run, and so a
  // level that drains to empty is visibly distinct from one that never
  // existed.
  uint64_t levels_nonempty = 0;
  int deepest_level = -1;
  for (const auto &level : metadata.levels) {
    if (!level.files.empty()) {
      ++levels_nonempty;
      deepest_level = level.level;
    }
  }

  ExperimentTelemetrySnapshot telemetry = GetExperimentTelemetrySnapshot();
  const bool warmup = warmup_operations > 0 && op_index <= warmup_operations;
  const uint64_t measured_op_index =
      op_index > warmup_operations ? op_index - warmup_operations : 0;
  out << "{"
      << "\"op_index\":" << op_index << ","
      << "\"measured_op_index\":" << measured_op_index << ","
      << "\"warmup_ops\":" << warmup_operations << ","
      << "\"phase\":\""
      << (phase_override ? phase_override : (warmup ? "warmup" : "measured"))
      << "\","
      << "\"elapsed_ns\":" << elapsed_ns << ","
      << "\"l0_files\":" << l0_files << ","
      << "\"l0_size_bytes\":" << l0_size << ","
      << "\"levels_nonempty\":" << levels_nonempty << ","
      << "\"deepest_level\":" << deepest_level << ",";
  out << "\"levels\":[";
  for (size_t i = 0; i < metadata.levels.size(); ++i) {
    const auto &level = metadata.levels[i];
    if (i > 0) out << ",";
    out << "{\"level\":" << level.level << ",\"files\":" << level.files.size()
        << ",\"size_bytes\":" << level.size << "}";
  }
  out << "],";
  out
      << "\"pending_compaction_bytes\":"
      << GetIntPropertyOrZero(db, "rocksdb.estimate-pending-compaction-bytes")
      << ","
      << "\"running_compactions\":"
      << GetIntPropertyOrZero(db, "rocksdb.num-running-compactions") << ","
      << "\"compaction_pending\":"
      << GetIntPropertyOrZero(db, "rocksdb.compaction-pending") << ","
      << "\"flushed_bytes\":" << telemetry.flushed_bytes << ","
      << "\"compaction_bytes_read\":" << telemetry.compaction_bytes_read << ","
      << "\"compaction_bytes_written\":" << telemetry.compaction_bytes_written
      << ","
      << "\"l0_compactions_completed\":" << telemetry.l0_compactions_completed
      << ","
      << "\"stall_events\":" << telemetry.stall_events << ","
      << "\"stop_events\":" << telemetry.stop_events << ","
      << "\"stall_duration_micros\":" << telemetry.stall_duration_micros
      << "}\n";
  out.flush();
}

FinalTreeStats MeasureFinalTree(DB *db) {
  FinalTreeStats result;
  ColumnFamilyMetaData metadata;
  db->GetColumnFamilyMetaData(&metadata);
  for (const auto &level : metadata.levels) {
    result.total_sst_bytes += level.size;
  }

  ReadOptions options;
  options.fill_cache = false;
  options.total_order_seek = true;
  std::unique_ptr<Iterator> it(db->NewIterator(options));
  for (it->SeekToFirst(); it->Valid(); it->Next()) {
    ++result.live_entries;
    result.live_logical_bytes += it->key().size() + it->value().size();
  }
  if (!it->status().ok()) {
    std::cerr << "Final live-data scan failed: " << it->status().ToString()
              << std::endl;
  }
  return result;
}

void WriteExperimentSummary(const WorkloadMetrics &metrics,
                            uint64_t measured_ns, uint64_t total_wall_ns,
                            uint64_t executed_operations,
                            uint64_t warmup_operations,
                            const ExperimentTelemetrySnapshot &warmup_telemetry,
                            const TickerSnapshot &warmup_tickers,
                            const TickerSnapshot &final_tickers,
                            const Options &options, const DBEnv &env,
                            const DrainStats &drain,
                            const FinalTreeStats &tree) {
  std::ofstream out(experiment_metrics_file);
  const double measured_seconds = static_cast<double>(measured_ns) / 1e9;
  const double total_wall_seconds = static_cast<double>(total_wall_ns) / 1e9;
  const uint64_t write_count = metrics.WriteCount();
  const uint64_t read_count = metrics.ReadCount();
  const uint64_t measured_operations = write_count + read_count;
  const uint64_t write_p95 =
      P95ForCombined({&metrics.inserts, &metrics.updates, &metrics.deletes,
                      &metrics.range_deletes});
  const uint64_t write_p99 = PercentileForCombined(
      {&metrics.inserts, &metrics.updates, &metrics.deletes,
       &metrics.range_deletes},
      0.99);
  const uint64_t read_p95 = P95ForCombined({&metrics.gets, &metrics.scans});
  const uint64_t read_p99 =
      PercentileForCombined({&metrics.gets, &metrics.scans}, 0.99);
  const ExperimentTelemetrySnapshot telemetry =
      TelemetryDelta(GetExperimentTelemetrySnapshot(), warmup_telemetry);
  const TickerSnapshot tickers = TickerDelta(final_tickers, warmup_tickers);
  // RocksDB's byte tickers are the authoritative physical byte counts. The
  // listener's flush size is reconstructed from table properties and omits
  // small format metadata, so using it would bias WAF downward.
  const uint64_t total_sst_bytes_written =
      tickers.flush_write_bytes + tickers.compact_write_bytes;
  const double write_amplification = SafeRatio(
      total_sst_bytes_written, metrics.user_logical_bytes_written);
  const double point_read_amplification =
      SafeRatio(metrics.point_sst_probes, metrics.gets.count);
  const uint64_t scan_work = metrics.scan_returned_entries +
                             metrics.scan_internal_entries_skipped;
  const double scan_amplification =
      SafeRatio(scan_work, metrics.scan_returned_entries);
  const double sorted_run_seeks_per_scan =
      SafeRatio(metrics.scan_sorted_run_seeks, metrics.scans.count);
  const double space_amplification =
      SafeRatio(tree.total_sst_bytes, tree.live_logical_bytes);

  out << "{\n";
  out << "  \"total_operations\": " << measured_operations << ",\n";
  out << "  \"measured_operations\": " << measured_operations << ",\n";
  out << "  \"executed_operations\": " << executed_operations << ",\n";
  out << "  \"warmup_operations\": " << warmup_operations << ",\n";
  out << "  \"total_wall_time_ns\": " << total_wall_ns << ",\n";
  out << "  \"total_wall_time_seconds\": " << std::fixed << std::setprecision(6)
      << total_wall_seconds << ",\n";
  out << "  \"total_time_ns\": " << measured_ns << ",\n";
  out << "  \"total_time_seconds\": " << std::fixed << std::setprecision(6)
      << measured_seconds << ",\n";
  out << "  \"write_throughput_ops_per_sec\": "
      << (measured_seconds > 0
              ? static_cast<double>(write_count) / measured_seconds
              : 0.0)
      << ",\n";
  out << "  \"write_latency\": {\"count\": " << write_count << ", \"avg_ns\": "
      << AverageForCombined(metrics.WriteTotalNs(), write_count)
      << ", \"p95_ns\": " << write_p95 << ", \"p99_ns\": " << write_p99
      << "},\n";
  out << "  \"read_latency\": {\"count\": " << read_count << ", \"avg_ns\": "
      << AverageForCombined(metrics.ReadTotalNs(), read_count)
      << ", \"p95_ns\": " << read_p95 << ", \"p99_ns\": " << read_p99
      << "},\n";
  out << "  \"operation_latency\": {\n";
  WriteOperationSummary(out, "insert", metrics.inserts, true);
  WriteOperationSummary(out, "update", metrics.updates, true);
  WriteOperationSummary(out, "delete", metrics.deletes, true);
  WriteOperationSummary(out, "range_delete", metrics.range_deletes, true);
  WriteOperationSummary(out, "get", metrics.gets, true);
  WriteOperationSummary(out, "scan", metrics.scans, false);
  out << "  },\n";
  out << "  \"amplification\": {\n"
      << "    \"write\": {\"value\": " << write_amplification
      << ", \"flush_plus_compaction_bytes_written\": "
      << total_sst_bytes_written << ", \"user_logical_bytes_written\": "
      << metrics.user_logical_bytes_written << "},\n"
      << "    \"point_read\": {\"value\": " << point_read_amplification
      << ", \"logical_sst_probes\": " << metrics.point_sst_probes
      << ", \"point_reads\": " << metrics.gets.count << "},\n"
      << "    \"scan\": {\"value\": " << scan_amplification
      << ", \"returned_entries\": " << metrics.scan_returned_entries
      << ", \"internal_entries_skipped\": "
      << metrics.scan_internal_entries_skipped
      << ", \"empty_scans\": " << metrics.empty_scans
      << ", \"sorted_run_seeks\": " << metrics.scan_sorted_run_seeks
      << ", \"sorted_run_seeks_per_scan\": "
      << sorted_run_seeks_per_scan << "},\n"
      << "    \"space\": {\"value\": " << space_amplification
      << ", \"total_sst_bytes\": " << tree.total_sst_bytes
      << ", \"live_logical_bytes\": " << tree.live_logical_bytes
      << ", \"live_entries\": " << tree.live_entries << "}\n"
      << "  },\n";
  out << "  \"selected_options\": {"
      << "\"size_ratio\": " << env.size_ratio << ", "
      << "\"l0_compaction_trigger\": "
      << env.level0_file_num_compaction_trigger << ", "
      << "\"l0_slowdown_trigger\": "
      << env.level0_slowdown_writes_trigger << ", "
      << "\"l0_stop_trigger\": " << env.level0_stop_writes_trigger << ", "
      << "\"compaction_priority\": " << env.compaction_pri << "},\n";
  // Drain accounting. `compaction_bytes_*` in the telemetry block below
  // already INCLUDE the drain, which is the point: it is the total work the
  // data required, not the subset that happened to finish before the workload
  // ended. The fields here expose how much of that was outstanding debt.
  out << "  \"drain\": {"
      << "\"enabled\": " << (drain.enabled ? "true" : "false") << ", "
      << "\"ok\": " << (drain.ok ? "true" : "false") << ", "
      << "\"wall_time_seconds\": " << std::fixed << std::setprecision(6)
      << (static_cast<double>(drain.wall_ns) / 1e9) << ", "
      << "\"pending_bytes_before\": " << drain.pending_bytes_before << ", "
      << "\"pending_bytes_after\": " << drain.pending_bytes_after << ", "
      << "\"compaction_bytes_read\": " << drain.compaction_bytes_read << ", "
      << "\"compaction_bytes_written\": " << drain.compaction_bytes_written
      << ", "
      << "\"compactions_completed\": " << drain.compactions_completed << "},\n";
  out << "  \"telemetry\": {"
      << "\"flushed_bytes\": " << telemetry.flushed_bytes << ", "
      << "\"compaction_bytes_read\": " << telemetry.compaction_bytes_read
      << ", "
      << "\"compaction_bytes_written\": " << telemetry.compaction_bytes_written
      << ", "
      << "\"compactions_completed\": " << telemetry.compactions_completed
      << ", "
      << "\"l0_compactions_completed\": " << telemetry.l0_compactions_completed
      << ", "
      << "\"stall_events\": " << telemetry.stall_events << ", "
      << "\"stop_events\": " << telemetry.stop_events << ", "
      << "\"stall_duration_micros\": "
      << telemetry.stall_duration_micros << "},\n";
  out << "  \"rocksdb_tickers\": {"
      << "\"stall_micros\": " << tickers.stall_micros << ", "
      << "\"write_stall_count\": " << tickers.write_stall_count << ", "
      << "\"compact_read_bytes\": " << tickers.compact_read_bytes << ", "
      << "\"compact_write_bytes\": " << tickers.compact_write_bytes << ", "
      << "\"flush_write_bytes\": " << tickers.flush_write_bytes << ", "
      << "\"point_sst_probes\": " << tickers.point_sst_probes << ", "
      << "\"iterator_internal_skips\": "
      << tickers.iterator_internal_skips << ", "
      << "\"sorted_run_seeks\": " << tickers.sorted_run_seeks << "},\n";
  out << "  \"metric_definitions\": {\n"
      << "    \"write_amplification\": \"(flush bytes + compaction bytes written) / user logical bytes written\",\n"
      << "    \"point_read_amplification\": \"logical SST probes selected by the file picker per point read, including Bloom-filtered probes\",\n"
      << "    \"scan_amplification\": \"(returned entries + internal entries skipped) / returned entries; zero when no entry was returned\",\n"
      << "    \"scan_sorted_run_seeks\": \"user-iterator seeks issued to SST sorted-run iterators per scan\",\n"
      << "    \"space_amplification\": \"settled total SST bytes / live logical key-and-value bytes\",\n"
      << "    \"latency_p99\": \"diagnostic only; acceptance uses average and p95\",\n"
      << "    \"physical_file_reads\": \"diagnostic only and never used as the logical read-amplification objective\"\n"
      << "  }\n";
  out << "}\n";
}

} // namespace

int runWorkload(std::unique_ptr<DBEnv> &env) {
  std::unique_ptr<DB> db;
  Options options;
  WriteOptions write_options;
  ReadOptions read_options;
  BlockBasedTableOptions table_options;
  FlushOptions flush_options;

  configOptions(env, &options, &table_options, &write_options, &read_options,
                &flush_options);
  ResetExperimentTelemetry();

  const bool per_op_timer = env->is_per_op_timer;
  const bool total_timer = env->is_total_timer;
  const size_t metrics_sample_interval = MetricsSampleInterval();
  const uint64_t warmup_operations = MetricsWarmupOperations();

  std::shared_ptr<Buffer> buffer = std::make_unique<Buffer>(buffer_file);
  std::unique_ptr<Buffer> stats = std::make_unique<Buffer>(stats_file);
  std::ofstream lsm_metrics(lsm_metrics_file);

  if (env->IsDestroyDatabaseEnabled()) {
    DestroyDB(env->kDBPath, options);
    std::cerr << "Destroying database ... done" << std::endl;
  }

  PrintExperimentalSetup(env, buffer);

  Status s = DB::Open(options, env->kDBPath, &db);
  // assert() is compiled out in release builds, so a failed open used to fall
  // straight through to WriteLsmSample(db.get(), ...) with a null db and
  // segfault. A --db path whose parent directory does not exist is enough to
  // trigger it, which is a confusing way to learn about a typo.
  if (!s.ok() || db == nullptr) {
    std::cerr << "Failed to open database at '" << env->kDBPath
              << "': " << s.ToString() << std::endl;
    return -1;
  }

  WriteLsmSample(db.get(), lsm_metrics, 0, 0, warmup_operations);

  // Clearing the system cache
  if (env->clear_system_cache) {
#ifdef __linux__
    std::cerr << "Clearing system cache ...";
    std::cerr << system("sudo sh -c 'echo 3 >/proc/sys/vm/drop_caches'")
              << " done" << std::endl;
#endif
  }

  std::ifstream workload_file;
  workload_file.open("workload.txt");
  assert(workload_file);

  size_t total_operations = 0;
  if (env->IsShowProgressEnabled()) {
    std::string line;
    while (std::getline(workload_file, line)) {
      ++total_operations;
    }
  }

  workload_file.clear();
  workload_file.seekg(0, std::ios::beg);

  unsigned long inserts_exec_time = 0, updates_exec_time = 0, pq_exec_time = 0,
                pdelete_exec_time = 0, rq_exec_time = 0;
  auto exec_start = std::chrono::high_resolution_clock::now();
  auto measurement_start = exec_start;
  bool measurement_started = warmup_operations == 0;
  ExperimentTelemetrySnapshot warmup_telemetry;
  TickerSnapshot warmup_tickers;
  WorkloadMetrics workload_metrics;

  if (env->IsPerfStatEnabled())
    rocksdb::get_perf_context()->Reset();
  if (env->IsIOStatEnabled())
    rocksdb::get_iostats_context()->Reset();

  if (measurement_started) {
    warmup_telemetry = GetExperimentTelemetrySnapshot();
    warmup_tickers = CaptureTickerSnapshot(options);
  }

  std::string line;
  unsigned long ith_op = 0;
  while (std::getline(workload_file, line)) {
    if (line.empty())
      break;
    bool is_last_line = (workload_file.peek() == EOF);

    std::istringstream stream(line);
    // Read the WHOLE operation token, not a single character. Range queries are
    // emitted as `SC <start_key> <scan_length>`: extracting one char left "C"
    // in the stream, so every scan took "C" as its start key and the intended
    // start key as its end bound — seeking to a fixed point and iterating about
    // a third of the database (measured: 48,913 Next() calls for a requested
    // length of 50). The requested length was never read at all. That is why
    // range queries appeared to cost ~4 ms against a 5 us point lookup and
    // swamped every workload they appeared in.
    std::string operation_token;
    stream >> operation_token;
    const char operation = operation_token.empty() ? '\0' : operation_token[0];
    const uint64_t next_op_index = ith_op + 1;
    const bool collect_operation_metrics = next_op_index > warmup_operations;

    switch (operation) {
      // [Insert]
    case 'I': {
      std::string key, value;
      stream >> key >> value;

      auto t0 = std::chrono::high_resolution_clock::now();
      s = db->Put(write_options, key, value);
      auto duration = std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::high_resolution_clock::now() - t0);
      ROCKSDB_NAMESPACE::RLCompactionTelemetry::Get()
          .RecordForegroundOperation(
              ROCKSDB_NAMESPACE::RLCompactionTelemetry::ForegroundOperation::
                  kWrite,
              duration.count(), key.size() + value.size());
      if (collect_operation_metrics) {
        workload_metrics.inserts.Add(duration.count());
        workload_metrics.user_logical_bytes_written += key.size() + value.size();
      }
      if (per_op_timer) {
        (*stats) << "InsertTime: " << duration.count() << std::endl;
        if (collect_operation_metrics) {
          inserts_exec_time += duration.count();
        }
      }
      break;
    }
      // [Update]
    case 'U': {
      std::string key, value;
      stream >> key >> value;

      auto t0 = std::chrono::high_resolution_clock::now();
      s = db->Put(write_options, key, value);
      auto duration = std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::high_resolution_clock::now() - t0);
      ROCKSDB_NAMESPACE::RLCompactionTelemetry::Get()
          .RecordForegroundOperation(
              ROCKSDB_NAMESPACE::RLCompactionTelemetry::ForegroundOperation::
                  kWrite,
              duration.count(), key.size() + value.size());
      if (collect_operation_metrics) {
        workload_metrics.updates.Add(duration.count());
        workload_metrics.user_logical_bytes_written += key.size() + value.size();
      }
      if (per_op_timer) {
        (*stats) << "UpdateTime: " << duration.count() << std::endl;
        if (collect_operation_metrics) {
          updates_exec_time += duration.count();
        }
      }
      break;
    }
      // [PointDelete]
    case 'D': {
      std::string key;
      stream >> key;

      auto t0 = std::chrono::high_resolution_clock::now();
      s = db->Delete(write_options, key);
      auto duration = std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::high_resolution_clock::now() - t0);
      ROCKSDB_NAMESPACE::RLCompactionTelemetry::Get()
          .RecordForegroundOperation(
              ROCKSDB_NAMESPACE::RLCompactionTelemetry::ForegroundOperation::
                  kWrite,
              duration.count(), key.size());
      if (collect_operation_metrics) {
        workload_metrics.deletes.Add(duration.count());
        workload_metrics.user_logical_bytes_written += key.size();
      }
      if (per_op_timer) {
        (*stats) << "DeleteTime: " << duration.count() << std::endl;
        if (collect_operation_metrics) {
          pdelete_exec_time += duration.count();
        }
      }
      break;
    }
      // [ProbePointQuery]
    case 'P':
    case 'Q': { // for tectonic, point query is P insteat of Q
      std::string key, value;
      stream >> key;

      const uint64_t probes_before = TickerCount(options, POINT_SST_PROBE);
      auto t0 = std::chrono::high_resolution_clock::now();
      s = db->Get(read_options, key, &value);
      auto duration = std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::high_resolution_clock::now() - t0);
      ROCKSDB_NAMESPACE::RLCompactionTelemetry::Get()
          .RecordForegroundOperation(
              ROCKSDB_NAMESPACE::RLCompactionTelemetry::ForegroundOperation::
                  kGet,
              duration.count());
      if (collect_operation_metrics) {
        workload_metrics.gets.Add(duration.count());
        workload_metrics.point_sst_probes += CounterDelta(
            TickerCount(options, POINT_SST_PROBE), probes_before);
      }
      if (per_op_timer) {
        (*stats) << "GetTime: " << duration.count() << std::endl;
        if (collect_operation_metrics) {
          pq_exec_time += duration.count();
        }
      }
      break;
    }
      // [ScanRangeQuery]
    case 'S': {
      std::string start_key, bound;
      stream >> start_key >> bound;

      // The generator emits `SC <start_key> <scan_length>` — the third field is
      // a COUNT, not an end key. Comparing it as a key string (the previous
      // behaviour) is always true for a 16-char alphanumeric key against a
      // decimal like "0", so every range query broke on its first key and
      // degenerated into a bare Seek with the requested length ignored.
      //
      // Accept both forms: an all-digit bound is a scan length, anything else
      // is an end key, so hand-written workloads keep working.
      const bool bound_is_length =
          !bound.empty() &&
          bound.find_first_not_of("0123456789") == std::string::npos;
      uint64_t scan_length = 0;
      if (bound_is_length) {
        scan_length = std::strtoull(bound.c_str(), nullptr, 10);
      }

      ReadOptions scan_read_options = ReadOptions(read_options);
      scan_read_options.total_order_seek = true;
      Iterator *it = db->NewIterator(scan_read_options);
      assert(it->status().ok());
      const uint64_t skips_before = TickerCount(options, NUMBER_ITER_SKIP);
      const uint64_t seeks_before = TickerCount(options, SORTED_RUN_SEEK);
      auto t0 = std::chrono::high_resolution_clock::now();

      uint64_t scanned = 0;
      for (it->Seek(start_key); it->Valid(); it->Next()) {
        if (bound_is_length) {
          if (scanned >= scan_length) {
            break;
          }
          ++scanned;
        } else if (it->key().ToString() >= bound) {
          break;
        }
        if (!bound_is_length) ++scanned;
      }
      if (!it->status().ok()) {
        (*buffer) << it->status().ToString() << std::endl << std::flush;
      }
      auto duration = std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::high_resolution_clock::now() - t0);
      if (per_op_timer) {
        (*stats) << "ScanTime: " << duration.count() << std::endl;
        if (collect_operation_metrics) {
          rq_exec_time += duration.count();
        }
      }
      delete it;
      const uint64_t scan_skips = CounterDelta(
          TickerCount(options, NUMBER_ITER_SKIP), skips_before);
      const uint64_t scan_seeks =
          CounterDelta(TickerCount(options, SORTED_RUN_SEEK), seeks_before);
      ROCKSDB_NAMESPACE::RLCompactionTelemetry::Get()
          .RecordForegroundOperation(
              ROCKSDB_NAMESPACE::RLCompactionTelemetry::ForegroundOperation::
                  kScan,
              duration.count(), /*logical_write_bytes=*/0, scanned, scan_skips,
              scan_seeks);
      if (collect_operation_metrics) {
        workload_metrics.scans.Add(duration.count());
        workload_metrics.scan_returned_entries += scanned;
        workload_metrics.scan_internal_entries_skipped += scan_skips;
        workload_metrics.scan_sorted_run_seeks += scan_seeks;
        if (scanned == 0) ++workload_metrics.empty_scans;
      }
      break;
    }
    // [RangeDelete]
    case 'R': {
      std::string start_key, end_key;
      stream >> start_key >> end_key;
      auto t0 = std::chrono::high_resolution_clock::now();
      s = db->DeleteRange(write_options, start_key, end_key);
      auto duration = std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::high_resolution_clock::now() - t0);
      ROCKSDB_NAMESPACE::RLCompactionTelemetry::Get()
          .RecordForegroundOperation(
              ROCKSDB_NAMESPACE::RLCompactionTelemetry::ForegroundOperation::
                  kWrite,
              duration.count(), start_key.size() + end_key.size());
      if (collect_operation_metrics) {
        workload_metrics.range_deletes.Add(duration.count());
        workload_metrics.user_logical_bytes_written +=
            start_key.size() + end_key.size();
      }
      if (per_op_timer) {
        (*stats) << "RangeDeleteTime: " << duration.count() << std::endl;
      }
      break;
    }
    default:
      (*buffer) << "ERROR: Case match NOT found !!" << std::endl;
      break;
    }

    ith_op += 1;
    if (!measurement_started && ith_op >= warmup_operations) {
      measurement_start = std::chrono::high_resolution_clock::now();
      warmup_telemetry = GetExperimentTelemetrySnapshot();
      warmup_tickers = CaptureTickerSnapshot(options);
      measurement_started = true;
    }
    auto elapsed_ns =
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::high_resolution_clock::now() - exec_start)
            .count();
    if (ith_op % metrics_sample_interval == 0 || ith_op == warmup_operations ||
        is_last_line) {
      WriteLsmSample(db.get(), lsm_metrics, ith_op, elapsed_ns,
                     warmup_operations);
    }
    UpdateProgressBar(env, ith_op, total_operations,
                      (int)total_operations * 0.02);
    if (is_last_line)
      break;
  }

#ifdef PROFILE
  if (env->verbosity > Verbosity::NO_PRINTS)
    (*buffer) << "=====================" << std::endl;
  LogTreeState(db.get(), buffer, env);
  // LogRocksDBStatistics(db, options, buffer);
#endif // PROFILE

  auto exec_end = std::chrono::high_resolution_clock::now();
  auto total_exec_time = std::chrono::duration_cast<std::chrono::nanoseconds>(
                             exec_end - exec_start)
                             .count();
  auto measured_exec_time =
      measurement_started
          ? std::chrono::duration_cast<std::chrono::nanoseconds>(
                exec_end - measurement_start)
                .count()
          : 0;
  if (!measurement_started) {
    warmup_telemetry = GetExperimentTelemetrySnapshot();
    warmup_tickers = CaptureTickerSnapshot(options);
  }

  if (per_op_timer || total_timer)
    (*buffer) << "=====================" << std::endl;
  if (total_timer) {
    (*buffer) << "Workload Execution Time: " << total_exec_time << std::endl;
    (*buffer) << "Measured Execution Time: " << measured_exec_time << std::endl;
    (*buffer) << "Warmup Operations: " << warmup_operations << std::endl;
  }
  if (per_op_timer) {
    (*buffer) << "Inserts Execution Time: " << inserts_exec_time << std::endl;
    (*buffer) << "Updates Execution Time: " << updates_exec_time << std::endl;
    (*buffer) << "PointQuery Execution Time: " << pq_exec_time << std::endl;
    (*buffer) << "PointDelete Execution Time: " << pdelete_exec_time
              << std::endl;
    (*buffer) << "RangeQuery Execution Time: " << rq_exec_time << std::endl;
  }

  // ---------------------------------------------------------------------
  // Drain outstanding compaction work before measuring.
  //
  // Closing the DB with compactions still queued abandons that work, and the
  // telemetry never counts it. A policy that defers compaction then appears to
  // have *saved* the I/O it merely postponed past the finish line — measured
  // at 269 MB of "avoided" writes against 329 MB of unpaid debt on the 5M run.
  // Draining first makes the compaction totals reflect work actually required
  // by the data, so leveled and RL are compared at the same tree state.
  //
  // Set LSM_DRAIN_BEFORE_CLOSE=0 to reproduce the old (unsound) accounting.
  DrainStats drain;
  drain.enabled = DrainBeforeClose();
  drain.pending_bytes_before =
      GetIntPropertyOrZero(db.get(), "rocksdb.estimate-pending-compaction-bytes");
  if (drain.enabled) {
    const ExperimentTelemetrySnapshot before = GetExperimentTelemetrySnapshot();
    const auto drain_start = std::chrono::high_resolution_clock::now();

    // Hand compaction back to the leveled picker for the drain. WaitForCompact
    // only waits for work that is already queued, and under the RL policy
    // nothing is queued unless the policy asks for it — so a deferring policy
    // would otherwise "drain" while leaving its debt outstanding.
    ROCKSDB_NAMESPACE::SetRLDrainMode(true);

    WaitForCompactOptions wait_options;
    wait_options.flush = true;
    Status drain_status = db->WaitForCompact(wait_options);
    if (!drain_status.ok()) {
      std::cerr << "WaitForCompact failed: " << drain_status.ToString()
                << std::endl;
    }
    drain.ok = drain_status.ok();

    drain.wall_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
                        std::chrono::high_resolution_clock::now() - drain_start)
                        .count();
    const ExperimentTelemetrySnapshot after = GetExperimentTelemetrySnapshot();
    drain.compaction_bytes_read =
        after.compaction_bytes_read - before.compaction_bytes_read;
    drain.compaction_bytes_written =
        after.compaction_bytes_written - before.compaction_bytes_written;
    drain.compactions_completed =
        after.compactions_completed - before.compactions_completed;
    drain.pending_bytes_after = GetIntPropertyOrZero(
        db.get(), "rocksdb.estimate-pending-compaction-bytes");
    ROCKSDB_NAMESPACE::SetRLDrainMode(false);

    // Final tree state, after the debt is paid: this is the sample the two
    // arms are legitimately comparable at.
    WriteLsmSample(db.get(), lsm_metrics, ith_op, total_exec_time,
                   warmup_operations, "drained");
  }

  // Freeze foreground/RocksDB ticker totals before the live-data census. The
  // census is deliberately outside the measured workload and must not inflate
  // scan-work diagnostics.
  const TickerSnapshot final_tickers = CaptureTickerSnapshot(options);
  const FinalTreeStats final_tree = MeasureFinalTree(db.get());
  WriteExperimentSummary(workload_metrics, measured_exec_time, total_exec_time,
                         ith_op, warmup_operations, warmup_telemetry,
                         warmup_tickers, final_tickers, options, *env, drain,
                         final_tree);

  if (!s.ok())
    std::cerr << s.ToString() << std::endl;
  assert(s.ok());
  s = db->Close();
  if (!s.ok())
    std::cerr << s.ToString() << std::endl;
  assert(s.ok());

  PrintRocksDBPerfStats(env, buffer, options);
  table_options.block_cache.reset();
  options.table_factory.reset();

  // flush final stats and delete ptr
  buffer->flush();
  stats->flush();
  if (total_timer) {
    long long total_seconds = total_exec_time / 1e9;
    std::cerr << "\nExperiment completed in " << total_seconds / 3600 << "h "
              << (total_seconds % 3600) / 60 << "m " << total_seconds % 60
              << "s " << std::endl;
  }
  return 0;
}
