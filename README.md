# LSM Workload Aware Compaction

This project develops a **workload-aware compaction trigger policy** for LSM-tree based storage engines (RocksDB). Rather than relying on static, threshold-based compaction triggers, we use a **reinforcement learning agent** that observes the current workload pattern and LSM-tree state to decide when and how to trigger compaction — with the goal of reducing write amplification, read amplification, and space amplification across diverse workloads.

For the complete project description, implementation history, corrected and
invalidated results, current protocol-v3 architecture, and remaining validation
work, see
[`PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md`](PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md).

## Usage

All common tasks are handled by `scripts/manage.sh`:

```bash
./scripts/manage.sh <command> [args]
```

| Command | Description |
|---|---|
| `setup` | Run first-time setup (Install dependencies and initialize submodules)|
| `build` | Build the project |
| `run <spec> <style>` | Generate a workload and run the experiment |
| `clear` | Delete build artifacts, database, and output files |

---

## Dependencies

`setup.sh` installs these automatically on Linux and macOS. On other platforms, install them manually:

| Dependency | Linux (apt) | macOS (Homebrew) |
|---|---|---|
| C++ build tools | `build-essential` | Xcode Command Line Tools |
| CMake | `cmake` | `cmake` |
| GFlags | `libgflags-dev` | `gflags` |
| Git | `git` | `git` |
| Curl | `curl` | `curl` |
| Rust (nightly) | via `rustup` | via `rustup` |

---

## Running Experiments

This repository uses the [RocksDB-Wrapper](https://github.com/SSD-Brandeis/RocksDB-Wrapper), designed to facilitate database operations, workload generation, and performance testing. It leverages [RocksDB-SSD](https://github.com/SSD-Brandeis/RocksDB-SSD) for storage and [Tectonic](https://github.com/SSD-Brandeis/Tectonic) for workload generation.

### Wrapper Parameters

```
RocksDB_parser.

  OPTIONS:

      This group is all exclusive:
        -d[d], --destroy=[d]              Destroy and recreate the database
                                          [def: 1]
        
        --cc=[cc]                         Clear system cache [def: 1]
        
        -T[T], --size_ratio=[T]           The size ratio of the LSM-tree 
                                          [def:10]
        
        -P[P], --buffer_size_in_pages=[P] Number of pages in memory buffer
                                          [def: 512]
        
        -B[B], --entries_per_page=[B]     Number of entries per page [def: 4]
        
        -E[E], --entry_size=[E]           Size of one entry (bytes) 
                                          [def: 1024B]
        
        -M[M], --memory_size=[M]           Memory buffer size (bytes) [def: 16MB]
        
        -f[file_to_memtable_size_ratio],
        --file_to_memtable_size_ratio=[file_to_memtable_size_ratio]
                                          Ratio between files and memtable
                                          [def: 1]
        
        -F[file_size],
        --file_size=[file_size]           Target SST file size in bytes
                                          [def: memory buffer size]

        --level_base=[level_base]         Maximum bytes for the first leveled
                                          level [def: target file size]
        
        -c[compaction_pri],
        --compaction_pri=[compaction_pri] [Compaction priority: 
                                           1 for kMinOverlappingRatio,
                                           2 for kByCompensatedSize,
                                           3 for kOldestLargestSeqFirst,
                                           4 for kOldestSmallestSeqFirst;
                                           def: 1]
        
        -C[compaction_style],
        --compaction_style=[compaction_style]
                                          [Compaction style:
                                           1 for kCompactionStyleLevel,
                                           2 for kCompactionStyleUniversal,
                                           3 for kCompactionStyleFIFO,
                                           4 for kCompactionStyleNone;
                                           def: 1]
        
        -b[bits_per_key],
        --bits_per_key=[bits_per_key]     The number of bits per key assigned to
                                          Bloom filter [def: 10]
        
        --bb=[bb]                         Block cache size in MB [def: 8 MB]
        
        --perf=[enable_perf_iostat]       Enable RocksDB's internal Perf and
                                          IOstat [def: 0]
        
        --iostat=[enable_iostat]          Enable RocksDB's internal IOstat
                                          [def: 0]
        
        --stat=[enable_rocksdb_stats]     Enable RocksDB's internal RocksDB
                                          stats [def: 0]
        
        --progress=[show_progress_bar]    Shows progress bar [def: 0]
        
        -V[verbosity],
        --verbosity=[verbosity]           The verbosity level of execution
                                          [0,1,2; def: 0]
        
        --peroptime=[peroptime]           Enable timing for every individual
                                          operation [def: 0]
        
        --totaltime=[totaltime]           Enable timing for the total workload
                                          duration [def: 0]
        
        --lowpri=[low_pri]                Set the priority of write requests (0
                                          means compactions aren't prioritized)
                                          [def: 1]
```
---

### Example

```bash
./bin/db_runner --file_size 524288 --level_base 16777216 \
  --size_ratio 20 --peroptime 1
```

This example runs the experiment with:

* SST file target size = 512 KiB
* First-level size target = 16 MiB
* Size ratio = 20
* Per-operation timing enabled

### Candidate-aware protocol v3

The current controller selects an exact SST candidate under measured space and
latency envelopes. The reproducible workflow is:

```bash
scripts/run_baseline_grid.sh
RL_BASELINE_SLO_PATH=results/baseline_grid/<run>/baseline_slo.json \
  scripts/run_repeated_experiment.sh
scripts/evaluate_paired.py --results results/repeated/<workload>
```

The independent L0 options are `--l0_compaction_trigger`,
`--l0_slowdown_trigger`, and `--l0_stop_trigger`; `-T` retains the historical
coupled defaults for old commands. See
[`docs/candidate_aware_protocol_v3.md`](docs/candidate_aware_protocol_v3.md) for
the protocol, attribution, reward, safety mask, and acceptance criteria.

### Scaled db_bench and RL sweep

The clean, db_bench-only 10M–50M workflow is under
[`scripts/dbbench_pipeline/`](scripts/dbbench_pipeline/README.md). It contains
separate numbered scripts for dependencies, the RocksDB build, the db_bench
build, experiments, and graphs. Both regular and RL arms use db_bench's
internal seeded generator; no workload files are needed.
