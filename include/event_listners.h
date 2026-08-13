#ifndef EVENT_LISTNER_H_
#define EVENT_LISTNER_H_

#include <condition_variable>
#include <cstdint>

#include <rocksdb/db.h>

#include "buffer.h"
#include "db_env.h"

using namespace rocksdb;

extern std::mutex mtx;
extern std::condition_variable cv;
extern bool compaction_complete;

struct ExperimentTelemetrySnapshot {
  uint64_t flushed_bytes = 0;
  uint64_t compaction_bytes_read = 0;
  uint64_t compaction_bytes_written = 0;
  uint64_t compactions_completed = 0;
  uint64_t l0_compactions_completed = 0;
  uint64_t stall_events = 0;
  uint64_t stop_events = 0;
  // Wall-clock time spent in either delayed or stopped write-stall state.
  // Includes an in-progress stall at snapshot time.
  uint64_t stall_duration_micros = 0;
};

void ResetExperimentTelemetry();
ExperimentTelemetrySnapshot GetExperimentTelemetrySnapshot();

/*
 * Wait for compactions that are running (or will run) to make the
 * LSM tree in its shape. Check `CompactionListner` for more details.
 */
void WaitForCompactions(DB *db);

/*
 * The compactions can run in background even after the workload is completely
 * executed so, we have to wait for them to complete. Compaction Listener gets
 * notified by the rocksdb API for every compaction that just finishes off.
 * After every compaction we check, if more compactions are required with
 * `WaitForCompaction` function, if not then it signals to close the db
 */
class CompactionsListner : public EventListener {
public:
  explicit CompactionsListner(std::unique_ptr<DBEnv> &env) : db_env(env) {}

  void OnCompactionBegin(DB *db, const CompactionJobInfo &ci) override;

  void OnCompactionCompleted(DB *db, const CompactionJobInfo &ci) override;

  void OnFlushCompleted(DB *db, const FlushJobInfo &fji) override;

  void OnStallConditionsChanged(const WriteStallInfo &info) override;

private:
  std::unique_ptr<DBEnv> &db_env;
};

class FlushListner : public EventListener {
public:
  explicit FlushListner(std::shared_ptr<Buffer> &buffer) { buffer_ = buffer; }
  void OnFlushCompleted(DB *db, const FlushJobInfo &fji) override;

private:
  std::shared_ptr<Buffer> buffer_;
};

#endif // EVENT_LISTNER_H_
