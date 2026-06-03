#include <filesystem>
#include <iomanip>
#include <iostream>

#include <rocksdb/db.h>
#include <rocksdb/iostats_context.h>
#include <rocksdb/perf_context.h>
#include <rocksdb/statistics.h>

#include "db_env.h"
#include "event_listners.h"

using namespace rocksdb;

template <typename T>
void PrintColumn(T value, int width, std::shared_ptr<Buffer> &buffer) {
  (*buffer) << std::setfill(' ') << std::setw(width) << value;
}

void PrintExperimentalSetup(std::unique_ptr<DBEnv> &env,
                            std::shared_ptr<Buffer> &buffer) {
  if (env->verbosity > Verbosity::NO_PRINTS) {
    constexpr int colWidth = 10;
    constexpr int smallColWidth = 4;

    PrintColumn("cmpt_sty", colWidth, buffer);  // Compaction style
    PrintColumn("cmpt_pri", colWidth, buffer);  // Compaction priority
    PrintColumn("T", smallColWidth, buffer);    // Size ratio
    PrintColumn("P", colWidth, buffer);         // Buffer size in pages
    PrintColumn("B", colWidth, buffer);         // Entries per page
    PrintColumn("E", colWidth, buffer);         // Entry size
    PrintColumn("M", colWidth, buffer);         // Buffer size
    PrintColumn("file_size", colWidth, buffer); // Target file size base
    PrintColumn("L1_size", colWidth, buffer);   // Max bytes for level base
    PrintColumn("blk_cch", colWidth, buffer);   // Block cache
    PrintColumn("BPK", colWidth, buffer);       // Bits per key
    (*buffer) << std::endl;

    PrintColumn(env->compaction_style, colWidth, buffer);
    PrintColumn(env->compaction_pri, colWidth, buffer);
    PrintColumn(env->size_ratio, smallColWidth, buffer);
    PrintColumn(env->buffer_size_in_pages, colWidth, buffer);
    PrintColumn(env->entries_per_page, colWidth, buffer);
    PrintColumn(env->entry_size, colWidth, buffer);
    PrintColumn(env->GetBufferSize(), colWidth, buffer);
    PrintColumn(env->GetTargetFileSizeBase(), colWidth, buffer);
    PrintColumn(env->GetMaxBytesForLevelBase(), colWidth, buffer);
    PrintColumn(env->block_cache, colWidth, buffer);
    PrintColumn(env->bits_per_key, colWidth, buffer);
    (*buffer) << std::endl;
  }
}

void PrintRocksDBPerfStats(std::unique_ptr<DBEnv> &env,
                           std::shared_ptr<Buffer> &buffer, Options options) {
  if (env->IsRocksDBStatEnabled()) {
    rocksdb::SetPerfLevel(rocksdb::PerfLevel::kDisable);
    (*buffer) << "\n[Rocksdb Stats]\n";

    const std::shared_ptr<Statistics> &stats = options.statistics;

    for (const auto &entry : TickersNameMap) {
      uint64_t value = stats->getTickerCount(entry.first);
      if (value > 0) {
        (*buffer) << entry.second << " COUNT: " << value << "\n";
      }
    }

    HistogramData data;
    for (const auto &entry : HistogramsNameMap) {
      stats->histogramData(entry.first, &data);

      if (data.count > 0) {
        (*buffer) << entry.second << " P50 : " << data.median
                  << " P95 : " << data.percentile95
                  << " P99 : " << data.percentile99 << " P100 : " << data.max
                  << " COUNT : " << data.count << " SUM : " << data.sum << "\n";
      }
    }

    // (*buffer) << options.statistics->ToString() << "\n";
    options.statistics.reset();
  }
  if (env->IsPerfStatEnabled()) {
    (*buffer) << "[Perf Context]\n";
    (*buffer) << rocksdb::get_perf_context()->ToString(true) << "\n";
  }
  if (env->IsIOStatEnabled()) {
    (*buffer) << "\n[IO Stats Context]\n";
    (*buffer) << rocksdb::get_iostats_context()->ToString(true) << "\n";
  }
}

void UpdateProgressBar(std::unique_ptr<DBEnv> &env, size_t current,
                       size_t total, size_t update_interval, size_t bar_width) {
  if (env->IsShowProgressEnabled() &&
      (current % update_interval == 0 || current == total)) {
    double progress = static_cast<double>(current) / total;
    size_t pos = static_cast<size_t>(bar_width * progress);

    std::cerr << "[";
    for (size_t i = 0; i < bar_width; ++i) {
      if (i < pos)
        std::cerr << "=";
      else if (i == pos)
        std::cerr << ">";
      else
        std::cerr << " ";
    }
    std::cerr << "] " << std::fixed << std::setprecision(2)
              << (progress * 100.0) << "%\r";
    std::cerr.flush();
  }
}

#ifdef PROFILE
void LogTreeState(rocksdb::DB *db, std::shared_ptr<Buffer> &buffer,
                  std::unique_ptr<DBEnv> &env) {
  // Wait for compactions and get live files
  {
    std::vector<std::string> live_files;
    uint64_t manifest_size;
    db->GetLiveFiles(live_files, &manifest_size, true /*flush_memtable*/);
    // WaitForCompactions(db);
  }

  if (env->verbosity > Verbosity::NO_PRINTS) {
    ColumnFamilyMetaData metadata;
    db->GetColumnFamilyMetaData(&metadata);
    std::stringstream cfd_details;

    cfd_details << "Column Family Name: " << metadata.name
                << ", Size: " << metadata.size
                << " bytes, Files Count: " << metadata.file_count << std::endl;

    cfd_details << "Level Stats:" << std::endl;
    for (const auto &level : metadata.levels) {
      cfd_details << "Level: " << level.level
                  << ", Files: " << level.files.size()
                  << ", Size: " << level.size << " bytes" << std::endl;
    }

    (*buffer) << cfd_details.str() << std::endl;
  }
}

// void LogRocksDBStatistics(rocksdb::DB *db, const rocksdb::Options &options,
//                           std::shared_ptr<Buffer> &buffer) {
//   auto printProperty = [&](const std::string &propertyName) {
//     std::string value;
//     bool status = db->GetProperty(propertyName, &value);
//     if (status) {
//       (*buffer) << propertyName << ": " << value << std::endl;
//     } else {
//       (*buffer) << "Error getting property " << propertyName << ": " <<
//       status
//                 << std::endl;
//     }
//   };
// }
#endif // PROFILE