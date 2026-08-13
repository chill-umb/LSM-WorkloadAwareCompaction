# db_bench experiment pipeline

This directory is the complete workflow. It does not use Tectonic workload
files: `db_bench` generates each seeded workload internally, and both regular
and RL RocksDB receive the same workload parameters and seed.

Run the scripts in order from the repository root:

```bash
scripts/dbbench_pipeline/00_install_dependencies.sh
scripts/dbbench_pipeline/01_build_rocksdb.sh
scripts/dbbench_pipeline/02_build_db_bench.sh

DB_ROOT=/mnt/nvme/dbbench-databases \
RESULTS_ROOT=/mnt/nvme/dbbench-results \
CONFIRM_EXPERIMENTS=YES \
scripts/dbbench_pipeline/03_run_experiments.sh

scripts/dbbench_pipeline/04_generate_graphs.sh \
  --results /mnt/nvme/dbbench-results
```

Edit [config.sh](config.sh), or override its variables on the command line.
The defaults run 10M, 20M, 30M, 40M, and 50M total operations at `T=2,6,10`
for both regular leveled RocksDB and protocol-v3 RL: 30 experiment arms.

Each total is divided into a 29% `filluniquerandom` load and a 71% `mixgraph`
phase. The mixed phase is 52.1127% Gets, 15.4930% Puts, and 32.3944% scans.
Scans return exactly 32 records. Each measured process ends with
`waitforcompaction`, then a separate manual-compaction process supplies the
garbage-free space reference without contaminating measured write
amplification. Regular-first and RL-first ordering alternates across the
matrix to reduce systematic warm-machine bias.

Important limitations:

- The balanced workload's delete share is represented as Puts because
  `mixgraph` has no delete action.
- This is one run per arm. It generates descriptive graphs, not confidence
  intervals. Use repeats before making statistical claims.
- The RL safety mask defaults to off. Set `RL_SAFETY_MASK=1` only together with
  a valid `RL_BASELINE_SLO_PATH`.
- Put `DB_ROOT` on the actual device being evaluated. Do not use `/tmp` when it
  is backed by RAM.

To resume after interruption, use the same paths with `RESUME=1`. Only arms
with a `COMPLETED` marker are skipped; a partial arm must be inspected or
removed manually.
