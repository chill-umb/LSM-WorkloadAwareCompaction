# LSM Workload Aware Compaction

This project develops a **workload-aware compaction trigger policy** for
LSM-tree based storage engines (RocksDB). Rather than relying only on static
thresholds, a **reinforcement learning agent** observes workload and tree state
and decides whether and which level should trigger compaction. RocksDB's native
compaction picker continues to choose the SST files. The goal is to reduce
write, read, and space amplification across diverse workloads.

For the complete project description, implementation history, corrected and
invalidated results, current trigger-only architecture, and remaining
validation work, see
[`PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md`](PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md).

## Current workflow

The supported experiment path is the numbered `db_bench` pipeline. There is
no workload-generation script: `db_bench` generates the seeded load and mixed
phases itself.

```bash
git submodule update --init --recursive
scripts/dbbench_pipeline/00_install_dependencies.sh
scripts/dbbench_pipeline/01_build_rocksdb.sh
scripts/dbbench_pipeline/02_build_db_bench.sh
```

Then follow the oracle parity gate, tuned regular-leveled sweep, manifest
selection, paired experiment, graph, and CI commands in
[`scripts/dbbench_pipeline/README.md`](scripts/dbbench_pipeline/README.md).
The experiment runner never builds the code and never deletes a partial result
directory automatically.

The default record is 64 bytes of key plus 960 bytes of value. It uses a 2 MiB
write buffer, 512 KiB target SST, 16 MiB L1 target, 4 KiB data block, 8 MiB
block cache, 13 configured levels, and disabled WAL. The matrix is 10M through
50M total operations and `T=2,6,10`; all values are overridable in
[`config.sh`](scripts/dbbench_pipeline/config.sh).

### Trigger-only RL compaction

The RL controller changes only the compaction trigger. For every observed
level it returns `defer` or `compact`; when it authorizes `compact`, RocksDB's
native leveled picker selects the input SSTs using the configured
`CompactionPri`, then performs its normal overlap expansion, correctness checks,
merge, output formation, and background execution.

The experiment path is deliberately pinned to protocol v2. Protocol v3's
exact-SST candidate selection was a rejected prototype and has been removed
from both the Python controller and the RocksDB picker. The protocol-v2 contract
and the archived protocol-v3 decision are recorded in
[`PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md`](PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md).

### Scaled db_bench and RL sweep

The clean, db_bench-only 10M–50M workflow is under
[`scripts/dbbench_pipeline/`](scripts/dbbench_pipeline/README.md). It contains
separate numbered scripts for dependencies, the RocksDB build, the db_bench
build, experiments, and graphs. Both regular and RL arms use db_bench's
internal seeded generator; no workload files are needed.
