#include <atomic>
#include <chrono>
#include <iostream>

#include "event_listners.h"

std::mutex mtx;
std::condition_variable cv;
bool compaction_complete = false;

namespace {

struct ExperimentTelemetry {
  std::atomic<uint64_t> flushed_bytes{0};
  std::atomic<uint64_t> compaction_bytes_read{0};
  std::atomic<uint64_t> compaction_bytes_written{0};
  std::atomic<uint64_t> compactions_completed{0};
  std::atomic<uint64_t> l0_compactions_completed{0};
  std::atomic<uint64_t> stall_events{0};
  std::atomic<uint64_t> stop_events{0};
  std::atomic<uint64_t> stall_duration_micros{0};
  std::atomic<uint64_t> stall_started_micros{0};
};

ExperimentTelemetry telemetry;

uint64_t SteadyMicros() {
  return static_cast<uint64_t>(
      std::chrono::duration_cast<std::chrono::microseconds>(
          std::chrono::steady_clock::now().time_since_epoch())
          .count());
}

uint64_t ApproximateFlushBytes(const FlushJobInfo &fji) {
  const auto &props = fji.table_properties;
  uint64_t bytes = props.data_size + props.index_size + props.filter_size;
  if (bytes == 0) {
    bytes = props.raw_key_size + props.raw_value_size;
  }
  return bytes;
}

} // namespace

void ResetExperimentTelemetry() {
  telemetry.flushed_bytes = 0;
  telemetry.compaction_bytes_read = 0;
  telemetry.compaction_bytes_written = 0;
  telemetry.compactions_completed = 0;
  telemetry.l0_compactions_completed = 0;
  telemetry.stall_events = 0;
  telemetry.stop_events = 0;
  telemetry.stall_duration_micros = 0;
  telemetry.stall_started_micros = 0;
  compaction_complete = false;
}

ExperimentTelemetrySnapshot GetExperimentTelemetrySnapshot() {
  ExperimentTelemetrySnapshot snapshot;
  snapshot.flushed_bytes = telemetry.flushed_bytes.load();
  snapshot.compaction_bytes_read = telemetry.compaction_bytes_read.load();
  snapshot.compaction_bytes_written = telemetry.compaction_bytes_written.load();
  snapshot.compactions_completed = telemetry.compactions_completed.load();
  snapshot.l0_compactions_completed = telemetry.l0_compactions_completed.load();
  snapshot.stall_events = telemetry.stall_events.load();
  snapshot.stop_events = telemetry.stop_events.load();
  snapshot.stall_duration_micros = telemetry.stall_duration_micros.load();
  const uint64_t started = telemetry.stall_started_micros.load();
  if (started != 0) {
    const uint64_t now = SteadyMicros();
    if (now >= started) snapshot.stall_duration_micros += now - started;
  }
  return snapshot;
}

void WaitForCompactions(DB *db) {
  std::unique_lock<std::mutex> lock(mtx);
  uint64_t num_running_compactions;
  uint64_t pending_compaction_bytes;
  uint64_t num_pending_compactions;

  while (!compaction_complete) {
    // Check if there are ongoing or pending compactions
    db->GetIntProperty("rocksdb.num-running-compactions",
                       &num_running_compactions);
    db->GetIntProperty("rocksdb.estimate-pending-compaction-bytes",
                       &pending_compaction_bytes);
    db->GetIntProperty("rocksdb.compaction-pending", &num_pending_compactions);
    if (num_running_compactions == 0 && pending_compaction_bytes == 0 &&
        num_pending_compactions == 0) {
      break;
    }
    cv.wait_for(lock, std::chrono::milliseconds(10));
  }
}

void CompactionsListner::OnCompactionBegin(DB *db,
                                           const CompactionJobInfo &ci) {
#ifdef PROFILE
  if (db_env->verbosity > Verbosity::MEDIUM) {
    std::cout << "    ================> Before compaction <================"
              << std::flush;
    // This function is not supported by default
    // RocksDB, you have to implement it by youself
    // db->PrintFullTreeSummary();
  }
#endif // PROFILE
}

void CompactionsListner::OnCompactionCompleted(DB *db,
                                               const CompactionJobInfo &ci) {
  telemetry.compactions_completed.fetch_add(1);
  telemetry.compaction_bytes_read.fetch_add(ci.stats.total_input_bytes);
  telemetry.compaction_bytes_written.fetch_add(ci.stats.total_output_bytes);
  if (ci.base_input_level == 0) {
    telemetry.l0_compactions_completed.fetch_add(1);
  }

  std::lock_guard<std::mutex> lock(mtx);
  uint64_t num_running_compactions;
  uint64_t pending_compaction_bytes;
  uint64_t num_pending_compactions;
  db->GetIntProperty("rocksdb.num-running-compactions",
                     &num_running_compactions);
  db->GetIntProperty("rocksdb.estimate-pending-compaction-bytes",
                     &pending_compaction_bytes);
  db->GetIntProperty("rocksdb.compaction-pending", &num_pending_compactions);
  if (num_running_compactions == 0 && pending_compaction_bytes == 0 &&
      num_pending_compactions == 0) {
    compaction_complete = true;
  }
  cv.notify_one();
#ifdef PROFILE
  if (db_env->verbosity > Verbosity::MEDIUM) {
    std::cout << "    ================> After compaction <================"
              << std::flush;
    // This function is not supported by default
    // RocksDB, you have to implement it by youself
    // db->PrintFullTreeSummary();
  }
#endif // PROFILE
}

void CompactionsListner::OnFlushCompleted(DB *db, const FlushJobInfo &fji) {
  (void)db;
  telemetry.flushed_bytes.fetch_add(ApproximateFlushBytes(fji));
}

void CompactionsListner::OnStallConditionsChanged(const WriteStallInfo &info) {
  if (info.condition.cur != WriteStallCondition::kNormal) {
    telemetry.stall_events.fetch_add(1);
  }
  if (info.condition.cur == WriteStallCondition::kStopped) {
    telemetry.stop_events.fetch_add(1);
  }
  if (info.condition.prev == WriteStallCondition::kNormal &&
      info.condition.cur != WriteStallCondition::kNormal) {
    uint64_t expected = 0;
    telemetry.stall_started_micros.compare_exchange_strong(expected,
                                                            SteadyMicros());
  } else if (info.condition.prev != WriteStallCondition::kNormal &&
             info.condition.cur == WriteStallCondition::kNormal) {
    const uint64_t started = telemetry.stall_started_micros.exchange(0);
    const uint64_t now = SteadyMicros();
    if (started != 0 && now >= started) {
      telemetry.stall_duration_micros.fetch_add(now - started);
    }
  }
}
