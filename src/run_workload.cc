#include "run_workload.h"

#include <chrono>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <tuple>

#include "config_options.h"
#include "utils.h"

std::string buffer_file = "workload.log";
std::string stats_file = "stats.log";

int runWorkload(std::unique_ptr<DBEnv> &env) {
  std::unique_ptr<DB> db;
  Options options;
  WriteOptions write_options;
  ReadOptions read_options;
  BlockBasedTableOptions table_options;
  FlushOptions flush_options;

  configOptions(env, &options, &table_options, &write_options, &read_options,
                &flush_options);

  const bool per_op_timer = env->is_per_op_timer;
  const bool total_timer = env->is_total_timer;

  std::shared_ptr<Buffer> buffer = std::make_unique<Buffer>(buffer_file);
  std::unique_ptr<Buffer> stats = std::make_unique<Buffer>(stats_file);

  if (env->IsDestroyDatabaseEnabled()) {
    DestroyDB(env->kDBPath, options);
    std::cerr << "Destroying database ... done" << std::endl;
  }

  PrintExperimentalSetup(env, buffer);

  Status s = DB::Open(options, env->kDBPath, &db);
  if (!s.ok())
    std::cerr << s.ToString() << std::endl;
  assert(s.ok());

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

      auto t0 = per_op_timer ? std::chrono::high_resolution_clock::now() : std::chrono::high_resolution_clock::time_point{};
      s = db->Put(write_options, key, value);
      if (per_op_timer) {
        auto duration = std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::high_resolution_clock::now() - t0);
        (*stats) << "InsertTime: " << duration.count() << std::endl;
        inserts_exec_time += duration.count();
      }
      break;
    }
      // [Update]
    case 'U': {
      std::string key, value;
      stream >> key >> value;

      auto t0 = per_op_timer ? std::chrono::high_resolution_clock::now() : std::chrono::high_resolution_clock::time_point{};
      s = db->Put(write_options, key, value);
      if (per_op_timer) {
        auto duration = std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::high_resolution_clock::now() - t0);
        (*stats) << "UpdateTime: " << duration.count() << std::endl;
        updates_exec_time += duration.count();
      }
      break;
    }
      // [PointDelete]
    case 'D': {
      std::string key;
      stream >> key;

      auto t0 = per_op_timer ? std::chrono::high_resolution_clock::now() : std::chrono::high_resolution_clock::time_point{};
      s = db->Delete(write_options, key);
      if (per_op_timer) {
        auto duration = std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::high_resolution_clock::now() - t0);
        (*stats) << "DeleteTime: " << duration.count() << std::endl;
        pdelete_exec_time += duration.count();
      }
      break;
    }
      // [ProbePointQuery]
    case 'P':
    case 'Q':
     { // for tectonic, point query is P insteat of Q
      std::string key, value;
      stream >> key;

      auto t0 = per_op_timer ? std::chrono::high_resolution_clock::now() : std::chrono::high_resolution_clock::time_point{};
      s = db->Get(read_options, key, &value);
      if (per_op_timer) {
        auto duration = std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::high_resolution_clock::now() - t0);
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
      auto t0 = per_op_timer ? std::chrono::high_resolution_clock::now() : std::chrono::high_resolution_clock::time_point{};

      for (it->Seek(start_key); it->Valid(); it->Next()) {
        if (it->key().ToString() >= end_key) {
          break;
        }
      }
      if (!it->status().ok()) {
        (*buffer) << it->status().ToString() << std::endl << std::flush;
      }
      if (per_op_timer) {
        auto duration = std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::high_resolution_clock::now() - t0);
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
      s = db->DeleteRange(write_options, start_key, end_key);
      break;
    }
    default:
      (*buffer) << "ERROR: Case match NOT found !!" << std::endl;
      break;
    }

    ith_op += 1;
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
    (*buffer) << "PointDelete Execution Time: " << pdelete_exec_time << std::endl;
    (*buffer) << "RangeQuery Execution Time: " << rq_exec_time << std::endl;
  }

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
              << (total_seconds % 3600) / 60 << "m " << total_seconds % 60 << "s "
              << std::endl;
  }
  return 0;
}