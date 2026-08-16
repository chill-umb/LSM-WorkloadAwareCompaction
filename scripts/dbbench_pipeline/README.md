# db_bench experiment pipeline

This directory is the complete workflow. It does not use Tectonic workload
files: `db_bench` generates each seeded workload internally, and paired arms
receive identical workload parameters and seeds. The RL protocol is trigger-
only v2; RocksDB chooses every input SST.

Run the build scripts on the cloud machine from the repository root:

```bash
scripts/dbbench_pipeline/00_install_dependencies.sh
scripts/dbbench_pipeline/01_build_rocksdb.sh
scripts/dbbench_pipeline/02_build_db_bench.sh
```

First run the deterministic 1M/T=2 oracle parity gate. It needs no RL server or
SLO manifest:

```bash
WORKLOAD_SIZES_M=1 SIZE_RATIOS=2 \
EXPERIMENT_ARMS="regular oracle" REPEATS=3 \
DB_ROOT=/mnt/nvme/oracle-databases \
RESULTS_ROOT=/mnt/nvme/oracle-results \
CONFIRM_EXPERIMENTS=YES scripts/dbbench_pipeline/03_run_experiments.sh

scripts/dbbench_pipeline/04_generate_graphs.sh \
  --results /mnt/nvme/oracle-results
scripts/dbbench_pipeline/09_evaluate_oracle_parity.py \
  /mnt/nvme/oracle-results/graphs/summary.csv
```

After oracle parity passes, collect the independently controlled regular-
leveled grid. Review the grid variables in `05_run_baseline_sweep.sh`; the
defaults are intentionally a multi-dimensional sweep and can be expensive.

```bash
WORKLOAD_SIZES_M="10 20 30 40 50" SIZE_RATIOS="2 6 10" \
BASELINE_REPEATS=3 \
BASELINE_DB_ROOT=/mnt/nvme/baseline-databases \
BASELINE_RESULTS_ROOT=/mnt/nvme/baseline-results \
CONFIRM_BASELINE_SWEEP=YES \
scripts/dbbench_pipeline/05_run_baseline_sweep.sh
```

Select a comparator without inspecting RL output. Repeat this for each size/T
pair that will be evaluated:

```bash
scripts/dbbench_pipeline/06_select_baseline_slo.py \
  --baseline-results /mnt/nvme/baseline-results \
  --size-millions 10 --size-ratio 2 \
  --output baseline_slo/balanced-v1/10M/T2/baseline_slo.json
```

Finally run the selected regular, prior-only, unconstrained-learning ablation,
and constrained learned arms. The trigger and
priority settings are loaded from each selected manifest and applied identically
to every arm. The runner reconstructs the full experiment fingerprint and stops
before launching an arm if the remaining geometry does not match the manifest.

```bash
EXPERIMENT_ARMS="regular prior_only unconstrained_rl rl" REPEATS=10 \
DB_ROOT=/mnt/nvme/dbbench-databases \
RESULTS_ROOT=/mnt/nvme/dbbench-results \
CONFIRM_EXPERIMENTS=YES scripts/dbbench_pipeline/03_run_experiments.sh

scripts/dbbench_pipeline/04_generate_graphs.sh \
  --results /mnt/nvme/dbbench-results

scripts/dbbench_pipeline/07_evaluate_paired.py \
  /mnt/nvme/dbbench-results/graphs/summary.csv \
  --size-millions 10 --size-ratio 2 \
  --scan-objective amplification
```

After balanced acceptance, the read-heavy and write-heavy stress runner
performs separate tuned-baseline calibration for each workload identity and
then applies only the preregistered safety checks (2% space/latency upper bounds
and no paired stall increase):

```bash
STRESS_DB_ROOT=/mnt/nvme/stress-databases \
STRESS_ROOT=/mnt/nvme/stress-results \
CONFIRM_STRESS_SUITES=YES \
scripts/dbbench_pipeline/08_run_stress_suites.sh
```

Edit `config.sh`, or override variables on the command line. The default matrix
describes 10M, 20M, 30M, 40M, and 50M total operations at `T=2,6,10`.
`EXPERIMENT_ARMS` accepts `regular`, `oracle`, `prior_only`, `rl`, and
`unconstrained_rl`. The last arm disables the live safety mask and is an
ablation only; it is not the production policy.
`REPEATS` creates distinct repeat directories, pairs workload seeds, assigns
distinct policy seeds, and alternates arm order. Learned and prior-only arms
require a workload/configuration-matched manifest by default.

`WORKLOAD_PROFILE` is part of that identity. The fingerprint also includes the
load/mix proportions, scan settings, cache, Bloom filter, background jobs,
threads, WAL mode, and LSM geometry, so a manifest calibrated for the balanced
workload cannot silently protect a read-heavy or write-heavy run.
Protocol v2 combines observation, reward-interval closure, and action response
in one exchange, so the official runner requires
`RL_OBSERVE_INTERVAL_MS=RL_DECISION_INTERVAL_MS` (50 ms by default).

Each total is a 29% `filluniquerandom` load followed by 71% `mixgraph`. The
mixed phase is 52.1127% Gets, 15.4930% Puts, and 32.3944% scans; scans return 32
records. Records are 64-byte keys plus 960-byte values. The write buffer is
2 MiB, SST target 512 KiB, L1 target 16 MiB, data block 4 KiB, and WAL is
disabled. `waitforcompaction` settles measured debt and emits drain start/end,
pending-byte, compaction-byte, and compaction-time phase metrics; drain work
remains in authoritative WAF and runtime. A separate manual-compaction process
supplies a diagnostic garbage-free physical-size reference without entering
measured write amplification. Formal space amplification uses the measured
pre-compaction SST bytes divided by RocksDB's exported
`estimate-live-data-size`, not the post-compaction file size.

Important limitations:

- `mixgraph` represents the balanced workload's delete share as Puts because it
  has no delete action.
- The default is one repeat for operational safety. Use at least ten paired
  repeats and the preregistered confidence checks for final claims.
- Protocol v3 candidate/SST selection is retired. `RL_PROTOCOL_VERSION=2` is
  pinned and not environment-overridable.
- Put `DB_ROOT` on the storage device under evaluation, not a RAM-backed
  `/tmp`.

The shared score observer writes `pressure_episodes.jsonl` in regular and RL
arms. Compaction start and completion events record decision/eligibility
attribution and `rl_drain` phase identity. `summary.csv` separates
workload/drain compaction bytes and time while retaining total RocksDB counters.
`scripts/analyze_trigger_failure.py` gives a read-only source/output-level and
workload/drain report.

To resume, reuse the same paths with `RESUME=1`. Only arms with a `COMPLETED`
marker are skipped; inspect or remove a partial arm manually.
