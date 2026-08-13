#include <iostream>

#include "args.hxx"
#include "db_env.h"

int parse_arguments(int argc, char *argv[], std::unique_ptr<DBEnv> &env) {
  args::ArgumentParser parser("RocksDB_parser.", "");
  args::Group group1(parser, "This group is all exclusive:",
                     args::Group::Validators::DontCare);

  args::ValueFlag<int> destroy_database_cmd(
      group1, "d", "Destroy and recreate the database [def: 1]",
      {'d', "destroy"});
  args::ValueFlag<int> clear_system_cache_cmd(
      group1, "cc", "Clear system cache [def: 1]", {"cc"});

  args::ValueFlag<int> size_ratio_cmd(
      group1, "T", "The size ratio of the LSM-tree [def: 10]",
      {'T', "size_ratio"});
  args::ValueFlag<int> num_levels_cmd(
      group1, "num_levels", "Number of LSM levels [def: 10]",
      {"num_levels"});
  args::ValueFlag<int> l0_compaction_trigger_cmd(
      group1, "l0_compaction_trigger",
      "Number of L0 files that makes compaction due [def: size_ratio]",
      {"l0_compaction_trigger"});
  args::ValueFlag<int> l0_slowdown_trigger_cmd(
      group1, "l0_slowdown_trigger",
      "Number of L0 files that slows writes [def: size_ratio - 1]",
      {"l0_slowdown_trigger"});
  args::ValueFlag<int> l0_stop_trigger_cmd(
      group1, "l0_stop_trigger",
      "Number of L0 files that stops writes [def: size_ratio]",
      {"l0_stop_trigger"});
  args::ValueFlag<int> buffer_size_in_pages_cmd(
      group1, "P", "Number of pages in memory buffer [def: 512]",
      {'P', "buffer_size_in_pages"});
  args::ValueFlag<int> entries_per_page_cmd(
      group1, "B", "Number of entries per page [def: 4]",
      {'B', "entries_per_page"});
  args::ValueFlag<int> entry_size_cmd(group1, "E",
                                      "Size of one entry (bytes) [def: 1024 B]",
                                      {'E', "entry_size"});
  args::ValueFlag<long> buffer_size_cmd(
      group1, "M", " Memory buffer size (bytes) [def: 16 MB]",
      {'M', "memory_size"});
  args::ValueFlag<int> file_to_memtable_size_ratio_cmd(
      group1, "file_to_memtable_size_ratio",
      "Ratio between files and memtable [def: 1]",
      {'f', "file_to_memtable_size_ratio"});
  args::ValueFlag<uint64_t> file_size_cmd(
      group1, "file_size",
      "Target size of one SST file in bytes [def: memory buffer size]",
      {'F', "file_size"});
  args::ValueFlag<uint64_t> level_base_cmd(
      group1, "level_base",
      "Maximum bytes for the first leveled level [def: target file size]",
      {"level_base"});
  args::ValueFlag<int> compaction_pri_cmd(
      group1, "compaction_pri",
      "[Compaction priority: 1 for kMinOverlappingRatio, 2 for "
      "kByCompensatedSize, 3 for kOldestLargestSeqFirst, 4 for "
      "kOldestSmallestSeqFirst; def: 1]",
      {'c', "compaction_pri"});
  args::ValueFlag<int> compaction_style_cmd(
      group1, "compaction_style",
      "[Compaction style: 1 for kCompactionStyleLevel, 2 for "
      "kCompactionStyleUniversal, 3 for kCompactionStyleFIFO, 4 for "
      "kCompactionStyleNone, 5 for kCompactionStyleRL (RL-governed L0, "
      "requires Python RL server at $RL_COMPACTION_SOCKET_PATH); def: 1]",
      {'C', "compaction_style"});
  args::ValueFlag<int> bits_per_key_cmd(
      group1, "bits_per_key",
      "The number of bits per key assigned to Bloom filter [def: 10]",
      {'b', "bits_per_key"});
  args::ValueFlag<int> block_cache_cmd(
      group1, "bb", "Block cache size in MB [def: 8 MB]", {"bb"});
  args::ValueFlag<int> enable_perf_cmd(
      group1, "enable_perf_iostat",
      "Enable RocksDB's internal Perf and IOstat [def: 0]", {"perf"});
  args::ValueFlag<int> enable_iostat_cmd(
      group1, "enable_iostat", "Enable RocksDB's internal IOstat [def: 0]",
      {"iostat"});
  args::ValueFlag<int> enable_rocksdb_stats_cmd(
      group1, "enable_rocksdb_stats",
      "Enable RocksDB's internal RocksDB stats [def: 0]", {"stat"});
  args::ValueFlag<int> show_progress_cmd(
      group1, "show_progress_bar", "Shows progress bar [def: 0]", {"progress"});
  args::ValueFlag<int> verbosity_cmd(
      group1, "verbosity", "The verbosity level of execution [0,1,2; def: 0]",
      {'V', "verbosity"});
  args::ValueFlag<int> enable_per_op_time_cmd(
      group1, "peroptime",
      "Enable timing for every individual operation [def: 0]", {"peroptime"});
  args::ValueFlag<int> enable_total_time_cmd(
      group1, "totaltime",
      "Enable timing for the total workload duration [def: 0]", {"totaltime"});

  args::ValueFlag<int> low_pri_cmd(
      group1, "low_pri",
      "Set the priority of write requests (0 means compactions aren't "
      "prioritized) [def: 1]",
      {"lowpri"});

  args::ValueFlag<std::string> db_path_cmd(
      group1, "db",
      "Path to the RocksDB data directory [def: ./db]", {"db"});

  args::ValueFlag<int> max_background_jobs_cmd(
      group1, "max_background_jobs",
      "Max concurrent background compaction/flush jobs [def: 1]",
      {"max_background_jobs"});

  args::ValueFlag<uint64_t> soft_pending_limit_mb_cmd(
      group1, "soft_pending_limit_mb",
      "Slow writes once pending compaction bytes exceed this many MB "
      "[def: 0 = disabled]",
      {"soft_pending_limit_mb"});
  args::ValueFlag<uint64_t> hard_pending_limit_mb_cmd(
      group1, "hard_pending_limit_mb",
      "Stop writes once pending compaction bytes exceed this many MB "
      "[def: 0 = disabled]",
      {"hard_pending_limit_mb"});

  try {
    parser.ParseCLI(argc, argv);
  } catch (args::Help &) {
    std::cout << parser;
    exit(0);
  } catch (args::ParseError &e) {
    std::cerr << e.what() << std::endl;
    std::cerr << parser;
    return 1;
  } catch (args::ValidationError &e) {
    std::cerr << e.what() << std::endl;
    std::cerr << parser;
    return 1;
  }

  env->SetDestroyDatabase(destroy_database_cmd
                              ? args::get(destroy_database_cmd)
                              : env->IsDestroyDatabaseEnabled());
  env->clear_system_cache = clear_system_cache_cmd
                                ? args::get(clear_system_cache_cmd)
                                : env->clear_system_cache;
  env->size_ratio =
      size_ratio_cmd ? args::get(size_ratio_cmd) : env->size_ratio;
  env->num_levels = num_levels_cmd ? args::get(num_levels_cmd) : env->num_levels;
  env->level0_slowdown_writes_trigger = env->size_ratio - 1;
  env->level0_stop_writes_trigger = env->size_ratio;
  env->level0_file_num_compaction_trigger = env->size_ratio;
  // Compatibility is intentionally ordered this way: -T continues to imply
  // the historical coupled values, while any explicit L0 option overrides
  // only its own threshold. Baseline sweeps can therefore vary all four knobs
  // independently without changing old command lines.
  if (l0_compaction_trigger_cmd) {
    env->level0_file_num_compaction_trigger =
        args::get(l0_compaction_trigger_cmd);
  }
  if (l0_slowdown_trigger_cmd) {
    env->level0_slowdown_writes_trigger = args::get(l0_slowdown_trigger_cmd);
  }
  if (l0_stop_trigger_cmd) {
    env->level0_stop_writes_trigger = args::get(l0_stop_trigger_cmd);
  }
  if (env->size_ratio <= 1 || env->num_levels < 2 ||
      env->level0_file_num_compaction_trigger == 0 ||
      env->level0_slowdown_writes_trigger == 0 ||
      env->level0_stop_writes_trigger == 0) {
    std::cerr << "size ratio must be > 1, num_levels must be >= 2, and L0 thresholds must be positive "
                 "(or negative where RocksDB supports disabling a threshold)"
              << std::endl;
    return 1;
  }

  env->buffer_size_in_pages = buffer_size_in_pages_cmd
                                  ? args::get(buffer_size_in_pages_cmd)
                                  : env->buffer_size_in_pages;
  env->entries_per_page = entries_per_page_cmd ? args::get(entries_per_page_cmd)
                                               : env->entries_per_page;
  env->entry_size =
      entry_size_cmd ? args::get(entry_size_cmd) : env->entry_size;
  env->verbosity = verbosity_cmd ? args::get(verbosity_cmd) : env->verbosity;
  env->is_per_op_timer = enable_per_op_time_cmd
                             ? args::get(enable_per_op_time_cmd)
                             : env->is_per_op_timer;
  env->is_total_timer = enable_total_time_cmd ? args::get(enable_total_time_cmd)
                                              : env->is_total_timer;
  env->SetBufferSize(buffer_size_cmd ? args::get(buffer_size_cmd) : 0);
  env->SetTargetFileSizeBase(file_size_cmd ? args::get(file_size_cmd) : 0);
  env->SetMaxBytesForLevelBase(level_base_cmd ? args::get(level_base_cmd) : 0);
  env->file_to_memtable_size_ratio =
      file_to_memtable_size_ratio_cmd
          ? args::get(file_to_memtable_size_ratio_cmd)
          : env->file_to_memtable_size_ratio;
  env->compaction_pri =
      compaction_pri_cmd ? args::get(compaction_pri_cmd) : env->compaction_pri;
  env->compaction_style = compaction_style_cmd ? args::get(compaction_style_cmd)
                                               : env->compaction_style;
  env->bits_per_key =
      bits_per_key_cmd ? args::get(bits_per_key_cmd) : env->bits_per_key;
  env->block_cache =
      block_cache_cmd ? args::get(block_cache_cmd) : env->block_cache;
  env->SetPerfStat(enable_perf_cmd ? args::get(enable_perf_cmd)
                                   : env->IsPerfStatEnabled());
  env->SetIOStat(enable_iostat_cmd ? args::get(enable_iostat_cmd)
                                   : env->IsIOStatEnabled());
  env->SetRocksDBStat(enable_rocksdb_stats_cmd
                          ? args::get(enable_rocksdb_stats_cmd)
                          : env->IsRocksDBStatEnabled());
  env->SetShowProgress(show_progress_cmd ? args::get(show_progress_cmd)
                                         : env->IsShowProgressEnabled());
  env->low_pri = low_pri_cmd ? args::get(low_pri_cmd) : env->low_pri;
  env->max_background_jobs = max_background_jobs_cmd
                                ? args::get(max_background_jobs_cmd)
                                : env->max_background_jobs;
  constexpr uint64_t kMiB = 1024ULL * 1024ULL;
  env->soft_pending_compaction_bytes_limit =
      soft_pending_limit_mb_cmd
          ? args::get(soft_pending_limit_mb_cmd) * kMiB
          : env->soft_pending_compaction_bytes_limit;
  env->hard_pending_compaction_bytes_limit =
      hard_pending_limit_mb_cmd
          ? args::get(hard_pending_limit_mb_cmd) * kMiB
          : env->hard_pending_compaction_bytes_limit;
  if (db_path_cmd) {
    DBEnv::kDBPath = args::get(db_path_cmd);
  }
  return 0;
}
