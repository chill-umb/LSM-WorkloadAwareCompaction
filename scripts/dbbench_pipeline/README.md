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
EXPERIMENT_ARMS="regular oracle" REPEATS=10 \
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
  --output baseline_selection/balanced-v1/10M/T2/baseline_slo.json
```

The selection file is deliberately provisional: its formal +2% objectives are
fixed, but `guard_calibrated` is false. After every size/T selection exists,
run three oracle calibration repeats and three independent oracle holdouts.
Only the compact latency-window and safety-shadow JSONL files are processed;
the large agent I/O, metrics, and RocksDB logs are not calibration inputs.

```bash
WORKLOAD_SIZES_M="10 20 30 40 50" SIZE_RATIOS="2 6 10" \
SELECTION_SLO_ROOT=baseline_selection FINAL_SLO_ROOT=baseline_slo \
GUARD_DB_ROOT=/mnt/nvme/guard-databases \
GUARD_RESULTS_ROOT=/mnt/nvme/guard-results \
CONFIRM_GUARD_PROTOCOL=YES \
scripts/dbbench_pipeline/06_run_guard_protocol.sh
```

The holdout gate must pass every repeat before a learned experiment is run.
Calibration and holdout use disjoint seed ranges; a failed holdout cannot be
used to loosen the guard without a new calibration version and fresh holdouts.
Each run records the SHA-256 of the exact manifest it used. Resume refuses to
mix completed arms from another manifest, and the holdout checks that every
shadow frame carries the calibrated manifest's hash rather than relying only on
the geometry fingerprint. Shadow schema 2 reports `would_override_frame`.
Known predicted overrides do not simulate replay invalidation; readiness still
independently requires no actual holdout intervention and at most a 1%
predicted-override fraction.

**Tests and the preflight** (CLAUDE.md "Tests", plan §6 of
`docs/IMPLEMENTATION_PLAN_PROGRAMME1.md`). No run larger than
`PREFLIGHT_SHORT_RUN_MAX_M` (2M) operations starts without a passed preflight:
`03_run_experiments.sh` checks the marker `PREFLIGHT_PASSED` and exits 7 when
the `db_bench` binary or the code in `rl_agent/`, `controller/` or this
directory has changed since, or when a step the run's arms need was skipped
(static arms need steps 1–4, `rules` also 5, learned arms 1–6).

```bash
scripts/dbbench_pipeline/run_python_tests.sh     # tier 1, seconds, before every commit
scripts/dbbench_pipeline/01b_build_test_trees.sh # tier 2, node: fork gtests in a Debug tree
CONFIRM_PREFLIGHT_VERIFICATION=YES \
  scripts/dbbench_pipeline/13_run_preflight_verification.sh  # tier 3, node: writes the marker
```

`13` rebuilds `db_bench` (step 1), runs tiers 1 and 2 (step 2), then ACT-1,
parity, rules-mode and learner smoke (steps 3–6). A step whose component does
not exist yet is skipped and recorded; one whose component exists but is not
wired into `13` fails. Wire each step in the change that builds its component.

- **Step 3, ACT-1** (`20_check_actuation.py`, seconds): builds a small tree,
  freezes it, and reopens it under several `level_target_multipliers`
  vectors and through the `setoptions` step. It checks the scores and the
  pending estimate against RocksDB's formulas, and that bad vectors are
  refused. Report: `$PREFLIGHT_WORK_DIR/act1_report.json`. The same report
  carries, under `settle`, two checks of the `settle` step (D-13 §6): it
  passes on a settled tree and writes a host log, and it refuses a due one.
- **Step 4, ACT-4** (about 10 minutes, plus one full build the first time):
  - `01c_build_stock_db_bench.sh` builds *stock* RocksDB, meaning upstream
    11.1.1 (`STOCK_ROCKSDB_COMMIT`), with 01 and 02's flags, into
    `build-dbbench-stock`.
  - `22_check_native_parity.sh` then runs `PARITY_PAIRS` pairs at 1M
    operations, T=2. One arm is stock; the other is the patched binary with
    every multiplier set to 1.
  - Both arms run 03's flags (`dbbench_shared_flags` in `config.sh`). They
    are judged on the 2026-08-22 gate's limits, taken from 09.
  - The patched arm also writes the host log (`--rl_host_log`), as every
    measured arm will, so parity includes its cost. Each patched log must be
    consistent with itself (`host_log_consistency`).
  - "Undecided" (too few pairs for the observed spread) fails the step.
    Raise `PARITY_PAIRS` and rerun.
- **Step 4, the evaluator smoke** (a few minutes; Gate N0 item 5): one `03`
  `native` arm at 1M operations, T=2, run exactly as a Gate N2 arm (settle,
  host log, stamps), then `04`. `04` refuses the arm, and the step fails, if
  its settle failed, its host log is inconsistent, or a self-check fails: the
  operations summed over the intervals between installs must equal what
  `mixgraph` served, and the last sampled H must equal the live SST bytes at
  the end of the drain. Output: `$PREFLIGHT_WORK_DIR/evaluator/`.

### Node runbook: the first node session

Everything below runs on the node from the repository root. Steps a–c need
no new code: if one fails, stop and report the output. Step d cannot start
in the first session; it says what must exist first.

**Unattended:** after step a, `24_gate_n1_chain.sh` runs step c and steps
d.1–d.3 for both workloads in one go: about 13–16 hours, up to about 20 if
the power law needs the (290, 1) rung (`STOP_AFTER_N1=1` stops after the
admission tests, about 5–8 hours). It first checks the results disk
(writable, not tmpfs, not the root filesystem unless `ALLOW_ROOT_DISK=1`,
`MIN_FREE_GB` free, default 60), the venv, the fork commit and leftover
result and database folders, so a setup mistake fails in seconds. Each
workload's chain runs as its own process, so one workload's failure leaves
the other to finish; the prices then wait. Run it in `tmux`:

```bash
NVME=/mnt/nvme scripts/dbbench_pipeline/24_gate_n1_chain.sh 2>&1 | tee ~/gate_n1.log
```

- `PARITY_PAIRS` defaults to 10 here.
- Do not `git pull`, or edit files under `scripts/dbbench_pipeline` or
  `rl_agent`, while it runs: the preflight marker hashes them, and every
  later `03` call refuses a changed tree (exit 7).
- An arm that does not settle makes `04` refuse it (exit 3), which ends
  that workload's chain, as D-13 §6 intends; the log names the arm.
- **After a failure,** find the failed arm in the log, delete its result
  folder and its database folder (03 keeps both on purpose), then rerun
  with `RESUME=1`. The rerun skips every finished arm but repeats the
  preflight and the admission tests.

a. Publish and fetch the code. On the machine that holds the commits, push
   the fork first, since the root records a fork commit:

   ```bash
   /usr/bin/git -C lib/rocksdb push origin rl-compaction-policy-new
   /usr/bin/git push origin dqn-poc-new
   ```

   On the node (a fresh clone needs `git clone` and `git checkout dqn-poc-new`
   first):

   ```bash
   git pull --ff-only
   git submodule update --init --recursive
   git -C lib/rocksdb log -1 --format=%H   # must equal: git ls-tree HEAD lib/rocksdb
   scripts/dbbench_pipeline/00_install_dependencies.sh   # fresh node only
   ```

b. Tier 2, the fork's gtests in a Debug tree:

   ```bash
   scripts/dbbench_pipeline/01b_build_test_trees.sh
   ```

   Expected last line:
   `[tier2] passed: level_target_multipliers_test per_level_read_counters_test rl_controller_host_test`.
   This is the first time the C++ of plan steps 3 and 4 is compiled and run.

c. The preflight, steps 1–4 (Release build, tiers 1–2, ACT-1, ACT-4, the
   evaluator smoke), and the marker. Put `DB_ROOT` on the NVMe device:

   ```bash
   CONFIRM_PREFLIGHT_VERIFICATION=YES DB_ROOT=/mnt/nvme/preflight-dbs \
     scripts/dbbench_pipeline/13_run_preflight_verification.sh
   ```

   Expected: `[step 1]` to `[step 4] PASS`, steps 5 and 6 `SKIP` (no plugin
   yet), and `preflight marker written: build-dbbench/PREFLIGHT_PASSED`.
   Reports: `build-dbbench/preflight/act1_report.json`,
   `build-dbbench/preflight/act4/act4_report.json`,
   `build-dbbench/preflight/evaluator/graphs/summary.csv`.

d. Gate N2, the static comparator. Four things must exist first, in this
   order:
   1. **Gate N1: pool membership and the run length** (PATHWAYS Gate N1,
      PREREGISTRATION D-16, `config/admission_test.json`). Three pilot
      `native` arms per workload and T at the default point, 29M operations
      at 10% load (2.9M keys loaded, 26.1M `mixgraph` operations), e.g. for
      `Assoc` (for the power law, add the flags of step 2 and use its own
      `SESSION_ID`, `RESULTS_ROOT` and `DB_ROOT`, e.g. `n1-powerlaw`):

      ```bash
      EXPERIMENT_ARMS=native SIZE_RATIOS="2 6 10" WORKLOAD_SIZES_M=29 \
        LOAD_PERCENT=10 REPEATS=3 SESSION_ID=gate-n1-assoc \
        RESULTS_ROOT=/mnt/nvme/n1-assoc DB_ROOT=/mnt/nvme/n1-dbs/assoc \
        CONFIRM_EXPERIMENTS=YES scripts/dbbench_pipeline/03_run_experiments.sh
      pids=()
      for T in 2 6 10; do
        python3 scripts/dbbench_pipeline/19_admission_test.py \
          /mnt/nvme/n1-assoc/29M/T$T/repeat-*/native \
          --config config/admission_test.json \
          --output /mnt/nvme/n1-assoc/admission_T$T.json &
        pids+=($!)
      done
      for pid in "${pids[@]}"; do wait "$pid" || echo "an admission run FAILED"; done
      ```

      Each report records the pool (`pool`), each level's decision, n_min
      from its rule (`n_min_rule_simulation`; roughly 12 CPU-minutes per
      grid value tried at T=2 and 47 at T=10, D-16 §5) and
      `run_length.rung`. **`<N2 size>` and its `LOAD_PERCENT` are the
      longest of the three rungs**: pass both to every later `03` call of
      that workload (03 refuses a long Programme 1 run with any other load).
      A level reported `undecided` is decided later on Gate N2's native arms
      at the default point (D-16 §5).
   2. **q̄ per workload** (PREREGISTRATION D-14 §2), recorded in
      `config/research_objective_contract.json` and in a dated amendment
      before any Θ_s run: the mean throughput of five `native` arms at T=10
      through `03`, e.g. for `Assoc`:

      ```bash
      EXPERIMENT_ARMS=native SIZE_RATIOS=10 WORKLOAD_SIZES_M=<N2 size> \
        LOAD_PERCENT=<N2 load %> \
        REPEATS=5 SESSION_ID=qbar-assoc RESULTS_ROOT=/mnt/nvme/qbar-assoc \
        DB_ROOT=/mnt/nvme/qbar-dbs/assoc CONFIRM_EXPERIMENTS=YES \
        scripts/dbbench_pipeline/03_run_experiments.sh
      scripts/dbbench_pipeline/04_generate_graphs.py \
        --results /mnt/nvme/qbar-assoc --summary-only
      ```

      q̄ is the mean of `throughput_ops_per_second` in its `summary.csv`.
      For the power-law workload add `WORKLOAD_SKEW=2 MIX_GET_RATIO=0.95
      MIX_PUT_RATIO=0.05 MIX_SEEK_RATIO=0 WORKLOAD_PROFILE=powerlaw-get95-v1`
      (03 refuses the power law under the Assoc profile).
   3. **The device prices** (OBJ-2, Gate N0 item 7; PREREGISTRATION D-15
      §3), about an hour: three settled trees at T = 2, 6 and 10 for the
      read prices, and the q̄ arms' own flushes and compactions for c_w, so
      it runs after step 2 and reads both workloads' `summary.csv`:

      ```bash
      CONFIRM_PRICE_CALIBRATION=YES DB_ROOT=/mnt/nvme/prices-db \
        scripts/dbbench_pipeline/18_calibrate_prices.sh \
        /mnt/nvme/qbar-assoc/graphs/summary.csv \
        /mnt/nvme/qbar-powerlaw/graphs/summary.csv
      ```

      It stops in seconds, before any node time, unless `THREADS` is 1 and
      the q̄ rows are settled `native` arms at T=10 on this binary, each
      given once, at least five of each workload. It writes
      `build-dbbench/prices.json`, which `03` copies into every later arm
      and records in the fingerprint; the medians and spreads are in it.
   4. **The two measured profiles** of Θ_s (D-14 §3), per grid point, from
      that point's `native` arms (run them first, in the same session):

      ```bash
      python3 scripts/dbbench_pipeline/23_static_profiles.py \
        /mnt/nvme/n2-assoc/T<T>-b<base>-k<K0>/<N2 size>M/T<T>/repeat-*/native \
        --output /mnt/nvme/n2-assoc/T<T>-b<base>-k<K0>/profiles.json
      ```

      It prints `STATIC_PROFILE_survival_weighted=…` and
      `STATIC_PROFILE_last_level_emptying=…` for the next `03` call, and
      records the inputs, the measured overlap constants and any clipping in
      `profiles.json`. `03` checks each vector as RocksDB will (one entry per
      level, entry 0 = 1, entries in [0.5, 2.0], no level's target below
      the one above it).

   Then, per workload and per point of the grid (T ∈ {2, 6, 10}; base
   ∈ {8, 16, 32} MiB; K0 ∈ {2, 4, 8}, skipping K0 above base / write buffer,
   A-Impl-6), in one session, each grid point with its own results and DB
   directories:

   ```bash
   EXPERIMENT_ARMS="native static:uniform_0_75 static:survival_weighted static:last_level_emptying" \
     STATIC_PROFILE_survival_weighted=<vector> \
     STATIC_PROFILE_last_level_emptying=<vector> \
     SIZE_RATIOS=<T> MAX_BYTES_FOR_LEVEL_BASE=<bytes> L0_COMPACTION_TRIGGER=<K0> \
     WORKLOAD_SIZES_M=<N2 size> LOAD_PERCENT=<N2 load %> REPEATS=5 SESSION_ID=n2-assoc \
     RESULTS_ROOT=/mnt/nvme/n2-assoc/T<T>-b<base>-k<K0> \
     DB_ROOT=/mnt/nvme/n2-dbs/assoc/T<T>-b<base>-k<K0> \
     CONFIRM_EXPERIMENTS=YES scripts/dbbench_pipeline/03_run_experiments.sh
   ```

   An arm whose tree does not settle after the load is marked `UNSETTLED`
   and the matrix goes on (D-13 §6); `04` lists it as refused.

   Score and build the hull per (workload, T):

   ```bash
   scripts/dbbench_pipeline/04_generate_graphs.py --results /mnt/nvme/n2-assoc --summary-only
   scripts/dbbench_pipeline/frontier_analysis.py /mnt/nvme/n2-assoc/graphs/summary.csv \
     --workload-profile assoc-v1 --size-millions <N2 size> --size-ratio 2 \
     --output /mnt/nvme/n2-assoc/frontier_T2.json
   ```

   `frontier_T2.json` holds the hull, θ*_β for every mode, β* and c_s scale,
   and β̄.

Finally run the selected regular, prior-only, unconstrained-learning ablation,
and constrained learned arms. The trigger and
priority settings are loaded from each selected manifest and applied identically
to every arm. The runner reconstructs the full experiment fingerprint and stops
before launching an arm if the remaining geometry does not match the manifest.

```bash
EXPERIMENT_ARMS="regular prior_only unconstrained_rl rl" REPEATS=10 \
RL_RUN_PHASE=experiment BASELINE_SLO_DIR=baseline_slo \
DB_ROOT=/mnt/nvme/dbbench-databases \
RESULTS_ROOT=/mnt/nvme/dbbench-results \
CONFIRM_EXPERIMENTS=YES scripts/dbbench_pipeline/03_run_experiments.sh

scripts/dbbench_pipeline/04_generate_graphs.sh \
  --results /mnt/nvme/dbbench-results
```

**Programme 1 evaluation** (plan §5; PATHWAYS D §1, C §4). `04` scores each
Programme 1 arm from its host log over the measured phase (the
`measure_start` to `drain_end` stamps): C_W from the event log's SST bytes in
that window (D-11), C_R from the tickers differenced between the stamps, C_S
from H times the operations served between installs, `J_<mode>_b<β*>_cs<s>`
for every mode, β* ∈ {2, 5, 10} and c_s scale s ∈ {0.5, 1, 2}, plus stall
seconds, the stall fraction and throughput over mixgraph's wall time. It
writes the refused arms to `graphs/refused_arms.json` and exits 3 if there
are any. `frontier_analysis.py` gives the hull, θ*_β and β̄; `07` compares
one arm with θ*_β:

```bash
scripts/dbbench_pipeline/07_evaluate_paired.py summary.csv \
  --policy-arm rules --size-millions <N> --size-ratio 2 --output paired.json
```

It reports CMP-3's paired interval on J(arm) − J(θ*), the D-13 stall rule,
regret (C.5) and, with several workloads in one summary, suite robustness
(CMP-7). `19_admission_test.py` is the collapse test of Gate N1 (G §4); its
values come from a preregistered config file.

`db_bench` and the Python controller are pinned to disjoint cores by
`DBBENCH_CPUS` and `CONTROLLER_CPUS` in `config.sh`, defaulting to `0-7` and
`8` for the single-socket 16-core EPYC 4545P measurement node, whose cores 0-7
and 8-15 are separate L3 domains. Both sets apply to every arm, `regular`
included, so no arm has the controller's cores to itself. `03` refuses to start
if the sets overlap, name an offline CPU, or share a last-level cache on a
machine that offers a disjoint choice, and records both in `metadata.env`.
Clear both variables to run unpinned. After the first pinned run, confirm
`server_summary.json` reports no watchdog expiry: a starved controller trips
the socket timeout, and a fallback frame is a hard-invalid interval that fails
the learner-health gate for reasons unrelated to the policy.

Every arm writes `compaction_measurements.json`: per source level, from the
host log's `job_end` records, ρ, o, η, ξ, ρ̃, dropped bytes and trivially
moved bytes, trivial moves excluded from ρ, o and η (PATHWAYS B §2 item 4).
The old stack's Gate 0 (historical): items 3 and 4, the A-0
write-excess decomposition and the flow-garbage elision ceiling, come from
`14_gate0_reanalysis.py`, which needs only `summary.csv` and each arm's
`run.log`; it uses `rocksdb_LOG.txt` for exact per-level bytes when the arm
kept it and otherwise falls back to the 0.1 GB stats table and reports the
quantisation.

```bash
scripts/dbbench_pipeline/14_gate0_reanalysis.py \
  /mnt/nvme/dbbench-results --output /mnt/nvme/dbbench-results/gate0_reanalysis.json
```

Sorted-run seeks is the strict scan-improvement objective because this
workload's scan-amplification baseline is already at its physical floor. Scan
amplification remains a 2% non-regression check. Every learned arm also has a
hard pre-completion schema-2 health gate: credit-assignment version 2, zero
protocol errors and hard-invalid reward intervals, balanced decision
accounting, at least 32 full-horizon replay transitions, replay warmup, a true
optimizer step, a nonzero residual, drained clients, quiesced training, zero
pending or unresolved decisions, and no trainer exception are required before
diagnostic full compaction and `COMPLETED`.

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
require a workload/configuration-matched, schema-v2, calibrated manifest by
default. Schema-v1 manifests are rejected.

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

**Programme 1's db_bench steps** (plan WP4; 03 adopts them with its Programme 1
arms, plan §5):

- `--rl_host_log=<path>` (needs `--statistics`) writes the host log, one JSON
  object per line (`lib/rocksdb/db/rl_controller_host.h`):
  - `h`: H, the live SST bytes, with the operation count after every flush and
    compaction;
  - `job_begin`/`job_end`: per compaction, the start and output level, S, O,
    X, the trivial-move flag and when the start level became due;
  - `stamp`: `settle`, `measure_start`, `drain_start` and `drain_end`, each
    with the operation count, H, cumulative stall micros, the per-level read
    counters and every ticker.
- `settle` runs `WaitForCompact` with flushes, then holds for
  `--rl_settle_hold_seconds` (10) with no compaction pending and L0 below its
  trigger. It prints `RL_SETTLED ok=<0|1> …`, and a failed hold ends the run.
- `mixgraph` prints `RL_MEASURE_START_OP <n_w>` before its first operation.

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
