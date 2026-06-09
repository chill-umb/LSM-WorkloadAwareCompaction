#include "run_workload.h"

#include <algorithm>
#include <chrono>
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

size_t MetricsSampleInterval() {
  const char *env = std::getenv("LSM_METRICS_SAMPLE_INTERVAL");
  if (env == nullptr) {
    return 1000;
  }
  long value = std::strtol(env, nullptr, 10);
  return value > 0 ? static_cast<size_t>(value) : 1000;
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
                    uint64_t elapsed_ns) {
  ColumnFamilyMetaData metadata;
  db->GetColumnFamilyMetaData(&metadata);

  uint64_t l0_files = 0;
  uint64_t l0_size = 0;
  if (!metadata.levels.empty()) {
    l0_files = metadata.levels[0].files.size();
    l0_size = metadata.levels[0].size;
  }

  ExperimentTelemetrySnapshot telemetry = GetExperimentTelemetrySnapshot();
  out << "{"
      << "\"op_index\":" << op_index << ","
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

void WriteExperimentSummary(const WorkloadMetrics &metrics, uint64_t total_ns,
                            const Options &options) {
  std::ofstream out(experiment_metrics_file);
  const double total_seconds = static_cast<double>(total_ns) / 1e9;
  const uint64_t write_count = metrics.WriteCount();
  const uint64_t read_count = metrics.ReadCount();
  const uint64_t write_p95 =
      P95ForCombined({&metrics.inserts, &metrics.updates, &metrics.deletes,
                      &metrics.range_deletes});
  const uint64_t read_p95 = P95ForCombined({&metrics.gets, &metrics.scans});
  const ExperimentTelemetrySnapshot telemetry =
      GetExperimentTelemetrySnapshot();

  out << "{\n";
  out << "  \"total_operations\": " << (write_count + read_count) << ",\n";
  out << "  \"total_time_ns\": " << total_ns << ",\n";
  out << "  \"total_time_seconds\": " << std::fixed << std::setprecision(6)
      << total_seconds << ",\n";
  out << "  \"write_throughput_ops_per_sec\": "
      << (total_seconds > 0 ? static_cast<double>(write_count) / total_seconds
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
      << "\"stall_micros\": " << TickerCount(options, STALL_MICROS) << ", "
      << "\"write_stall_count\": " << HistogramCount(options, WRITE_STALL)
      << ", "
      << "\"compact_read_bytes\": " << TickerCount(options, COMPACT_READ_BYTES)
      << ", "
      << "\"compact_write_bytes\": "
      << TickerCount(options, COMPACT_WRITE_BYTES) << ", "
      << "\"flush_write_bytes\": " << TickerCount(options, FLUSH_WRITE_BYTES)
      << "}\n";
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

  WriteLsmSample(db.get(), lsm_metrics, 0, 0);

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
  WorkloadMetrics workload_metrics;

  if (env->IsPerfStatEnabled())
    rocksdb::get_perf_context()->Reset();
  if (env->IsIOStatEnabled())
    rocksdb::get_iostats_context()->Reset();

  std::string line;
  unsigned long ith_op = 0;
  while (std::getline(workload_file, line)) {
    if (line.empty())
      break;
    bool is_last_line = (workload_file.peek() == EOF);

    std::istringstream stream(line);
    char operation;
    stream >> operation;

    switch (operation) {
      // [Insert]
    case 'I': {
      std::string key, value;
      stream >> key >> value;

      auto t0 = std::chrono::high_resolution_clock::now();
      s = db->Put(write_options, key, value);
      auto duration = std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::high_resolution_clock::now() - t0);
      workload_metrics.inserts.Add(duration.count());
      if (per_op_timer) {
        (*stats) << "InsertTime: " << duration.count() << std::endl;
        inserts_exec_time += duration.count();
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
      workload_metrics.updates.Add(duration.count());
      if (per_op_timer) {
        (*stats) << "UpdateTime: " << duration.count() << std::endl;
        updates_exec_time += duration.count();
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
      workload_metrics.deletes.Add(duration.count());
      if (per_op_timer) {
        (*stats) << "DeleteTime: " << duration.count() << std::endl;
        pdelete_exec_time += duration.count();
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
      workload_metrics.gets.Add(duration.count());
      if (per_op_timer) {
        (*stats) << "GetTime: " << duration.count() << std::endl;
        pq_exec_time += duration.count();
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
      workload_metrics.scans.Add(duration.count());
      if (per_op_timer) {
        (*stats) << "ScanTime: " << duration.count() << std::endl;
        rq_exec_time += duration.count();
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
      workload_metrics.range_deletes.Add(duration.count());
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
    auto elapsed_ns =
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::high_resolution_clock::now() - exec_start)
            .count();
    if (ith_op % metrics_sample_interval == 0 || is_last_line) {
      WriteLsmSample(db.get(), lsm_metrics, ith_op, elapsed_ns);
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

  auto total_exec_time =
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::high_resolution_clock::now() - exec_start)
          .count();

  if (per_op_timer || total_timer)
    (*buffer) << "=====================" << std::endl;
  if (total_timer)
    (*buffer) << "Workload Execution Time: " << total_exec_time << std::endl;
  if (per_op_timer) {
    (*buffer) << "Inserts Execution Time: " << inserts_exec_time << std::endl;
    (*buffer) << "Updates Execution Time: " << updates_exec_time << std::endl;
    (*buffer) << "PointQuery Execution Time: " << pq_exec_time << std::endl;
    (*buffer) << "PointDelete Execution Time: " << pdelete_exec_time
              << std::endl;
    (*buffer) << "RangeQuery Execution Time: " << rq_exec_time << std::endl;
  }

  WriteExperimentSummary(workload_metrics, total_exec_time, options);

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
