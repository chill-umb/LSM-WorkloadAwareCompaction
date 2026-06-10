#include "run_workload.h"

#include <algorithm>
#include <chrono>
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

  uint64_t P95Ns() const {
    if (latencies_ns.empty()) {
      return 0;
    }
    std::vector<uint64_t> sorted = latencies_ns;
    std::sort(sorted.begin(), sorted.end());
    size_t index = static_cast<size_t>(0.95 * (sorted.size() - 1));
    return sorted[index];
  }
};

struct WorkloadMetrics {
  OperationMetrics inserts;
  OperationMetrics updates;
  OperationMetrics deletes;
  OperationMetrics range_deletes;
  OperationMetrics gets;
  OperationMetrics scans;

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
  size_t index = static_cast<size_t>(0.95 * (all.size() - 1));
  return all[index];
}

void WriteOperationSummary(std::ofstream &out, const char *name,
                           const OperationMetrics &metric,
                           bool trailing_comma) {
  out << "    \"" << name << "\": {"
      << "\"count\": " << metric.count << ", "
      << "\"avg_ns\": " << std::fixed << std::setprecision(2)
      << metric.AverageNs() << ", "
      << "\"p95_ns\": " << metric.P95Ns() << "}";
  if (trailing_comma) {
    out << ",";
  }
  out << "\n";
}

void WriteLsmSample(DB *db, std::ofstream &out, uint64_t op_index,
                    uint64_t elapsed_ns, uint64_t warmup_operations) {
  ColumnFamilyMetaData metadata;
  db->GetColumnFamilyMetaData(&metadata);

  uint64_t l0_files = 0;
  uint64_t l0_size = 0;
  if (!metadata.levels.empty()) {
    l0_files = metadata.levels[0].files.size();
    l0_size = metadata.levels[0].size;
  }

  ExperimentTelemetrySnapshot telemetry = GetExperimentTelemetrySnapshot();
  const bool warmup = warmup_operations > 0 && op_index <= warmup_operations;
  const uint64_t measured_op_index =
      op_index > warmup_operations ? op_index - warmup_operations : 0;
  out << "{"
      << "\"op_index\":" << op_index << ","
      << "\"measured_op_index\":" << measured_op_index << ","
      << "\"warmup_ops\":" << warmup_operations << ","
      << "\"phase\":\"" << (warmup ? "warmup" : "measured") << "\","
      << "\"elapsed_ns\":" << elapsed_ns << ","
      << "\"l0_files\":" << l0_files << ","
      << "\"l0_size_bytes\":" << l0_size << ","
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
      << "\"stop_events\":" << telemetry.stop_events << "}\n";
  out.flush();
}

void WriteExperimentSummary(const WorkloadMetrics &metrics,
                            uint64_t measured_ns, uint64_t total_wall_ns,
                            uint64_t executed_operations,
                            uint64_t warmup_operations,
                            const ExperimentTelemetrySnapshot &warmup_telemetry,
                            const TickerSnapshot &warmup_tickers,
                            const Options &options) {
  std::ofstream out(experiment_metrics_file);
  const double measured_seconds = static_cast<double>(measured_ns) / 1e9;
  const double total_wall_seconds = static_cast<double>(total_wall_ns) / 1e9;
  const uint64_t write_count = metrics.WriteCount();
  const uint64_t read_count = metrics.ReadCount();
  const uint64_t measured_operations = write_count + read_count;
  const uint64_t write_p95 =
      P95ForCombined({&metrics.inserts, &metrics.updates, &metrics.deletes,
                      &metrics.range_deletes});
  const uint64_t read_p95 = P95ForCombined({&metrics.gets, &metrics.scans});
  const ExperimentTelemetrySnapshot telemetry =
      TelemetryDelta(GetExperimentTelemetrySnapshot(), warmup_telemetry);
  const TickerSnapshot tickers =
      TickerDelta(CaptureTickerSnapshot(options), warmup_tickers);

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
      << ", \"p95_ns\": " << write_p95 << "},\n";
  out << "  \"read_latency\": {\"count\": " << read_count << ", \"avg_ns\": "
      << AverageForCombined(metrics.ReadTotalNs(), read_count)
      << ", \"p95_ns\": " << read_p95 << "},\n";
  out << "  \"operation_latency\": {\n";
  WriteOperationSummary(out, "insert", metrics.inserts, true);
  WriteOperationSummary(out, "update", metrics.updates, true);
  WriteOperationSummary(out, "delete", metrics.deletes, true);
  WriteOperationSummary(out, "range_delete", metrics.range_deletes, true);
  WriteOperationSummary(out, "get", metrics.gets, true);
  WriteOperationSummary(out, "scan", metrics.scans, false);
  out << "  },\n";
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
      << "\"stop_events\": " << telemetry.stop_events << "},\n";
  out << "  \"rocksdb_tickers\": {"
      << "\"stall_micros\": " << tickers.stall_micros << ", "
      << "\"write_stall_count\": " << tickers.write_stall_count << ", "
      << "\"compact_read_bytes\": " << tickers.compact_read_bytes << ", "
      << "\"compact_write_bytes\": " << tickers.compact_write_bytes << ", "
      << "\"flush_write_bytes\": " << tickers.flush_write_bytes << "}\n";
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
  if (!s.ok())
    std::cerr << s.ToString() << std::endl;
  assert(s.ok());

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
    char operation;
    stream >> operation;
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
      if (collect_operation_metrics) {
        workload_metrics.inserts.Add(duration.count());
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
      if (collect_operation_metrics) {
        workload_metrics.updates.Add(duration.count());
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
      if (collect_operation_metrics) {
        workload_metrics.deletes.Add(duration.count());
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

      auto t0 = std::chrono::high_resolution_clock::now();
      s = db->Get(read_options, key, &value);
      auto duration = std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::high_resolution_clock::now() - t0);
      if (collect_operation_metrics) {
        workload_metrics.gets.Add(duration.count());
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
      std::string start_key, end_key;
      stream >> start_key >> end_key;

      ReadOptions scan_read_options = ReadOptions(read_options);
      scan_read_options.total_order_seek = true;
      Iterator *it = db->NewIterator(scan_read_options);
      assert(it->status().ok());
      auto t0 = std::chrono::high_resolution_clock::now();

      for (it->Seek(start_key); it->Valid(); it->Next()) {
        if (it->key().ToString() >= end_key) {
          break;
        }
      }
      if (!it->status().ok()) {
        (*buffer) << it->status().ToString() << std::endl << std::flush;
      }
      auto duration = std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::high_resolution_clock::now() - t0);
      if (collect_operation_metrics) {
        workload_metrics.scans.Add(duration.count());
      }
      if (per_op_timer) {
        (*stats) << "ScanTime: " << duration.count() << std::endl;
        if (collect_operation_metrics) {
          rq_exec_time += duration.count();
        }
      }
      delete it;
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
      if (collect_operation_metrics) {
        workload_metrics.range_deletes.Add(duration.count());
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

  WriteExperimentSummary(workload_metrics, measured_exec_time, total_exec_time,
                         ith_op, warmup_operations, warmup_telemetry,
                         warmup_tickers, options);

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
