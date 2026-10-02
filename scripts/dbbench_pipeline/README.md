# db_bench experiment pipeline

The numbered stages for Programme 1 (`docs/PATHWAYS.md`,
`docs/IMPLEMENTATION_PLAN_PROGRAMME1.md`). `db_bench` generates every seeded
workload itself. Build on the node from the repository root:

```bash
scripts/dbbench_pipeline/00_install_dependencies.sh
scripts/dbbench_pipeline/01_build_rocksdb.sh
scripts/dbbench_pipeline/02_build_db_bench.sh
```

The order of node work is the runbook below: preflight (13), Gate N1 (24),
then Gate N2 (25). The retired DQN programme's stages (05, 06's guard
protocol, 08, 10–12, 14, 15, 17) were removed on 2026-10-01, and
`06_calibrate_live_guard.py`, `06_select_baseline_slo.py`, `09`,
`slo_statistics.py` and `04_generate_graphs.sh` on 2026-10-02 (with D-19).
`03` still accepts its arms until plan step 12, but they can no longer get a
fresh guard manifest.

**Tests and the preflight** (CLAUDE.md "Tests", plan §6 of
`docs/IMPLEMENTATION_PLAN_PROGRAMME1.md`). No run larger than
`PREFLIGHT_SHORT_RUN_MAX_M` (2M) operations starts without a passed preflight:
`03_run_experiments.sh` checks the marker `PREFLIGHT_PASSED` and exits 7 when
the `db_bench` binary or the code in `rl_agent/`, `controller/` or this
directory has changed since, or when a step the run's arms need was skipped
(static arms need steps 1–4, `hold` and `rules` also 5, learned arms 1–6).

**Controller arms** (`hold`, `rules`; plan §7 step 8). `03` loads the plugin
(`--rl_plugin`) at the first mixgraph operation and destroys it after the
drain. `plugin_config.py` composes each arm's config from the D-18 bounds
(`ACTION_BOUNDS_FILE`), `b_max` and the Gate N3 rules
(`CONTROLLER_RULES_FILE`), the contract (beta for
`CONTROLLER_OBJECTIVE_MODE`, c_s, q-bar) and the prices file, and `03`
refuses the matrix while any of them is missing or null.
`28_check_plugin_run.py` then checks every controller run: the plugin
started and stopped, its logs are complete, no masked action was taken, no
fallback, hold-only made no `SetOptions` call, and ACT-3 (at least 99% of
the requested changes in the published score within one control interval,
the level's next decision). A run that fails is kept with
`FAILED_PLUGIN_CHECK`, and `03` exits 8. `PLUGIN_PLACEHOLDERS=1` fills missing
values with smoke values for the preflight's own runs (at most 2M
operations).

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
  refused. Native compaction's timing decides the tree's layout, so a tree
  on which some check could not see its effect (an empty L1, say) is built
  again with the next seed, up to 10; `build_attempts` in the report lists
  each, with its settle exit and whether its native scores matched the
  model. Report: `$PREFLIGHT_WORK_DIR/act1_report.json`. The same report
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
    are judged on the 2026-08-22 gate's limits (constants in 22).
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
  that workload's chain (D-13 §6 makes the arm invalid; stopping there is
  24's choice); the log names the arm.
- **After a failure,** find the failed arm in the log, delete its result
  folder and its database folder (03 keeps both on purpose), then rerun
  with `RESUME=1`. The rerun skips every finished arm but repeats the
  preflight and the admission tests.
- **After a binary change** (PREREGISTRATION D-21), the prices need q̄
  arms on the new binary, since `18` takes c_w only from arms of the binary
  it prices. `QBAR_ONLY=1 QBAR_TAG=<tag>` reuses Gate N1's reports in
  `n1-<workload>` and runs only the preflight, the q̄ arms (into
  `qbar-<workload>-<tag>`, `qbar-dbs-<tag>`; no earlier folder is touched)
  and the prices, about 4.5–5 hours. q̄ stays as recorded: the new arms'
  mean is printed beside it, and `18` prints each arm's reopen check (below).
  Move the old `build-dbbench/prices.json` aside first, so the preflight's
  smoke runs unpriced instead of refusing a stale file:

  ```bash
  NVME=/mnt/nvme QBAR_ONLY=1 QBAR_TAG=d21 \
    scripts/dbbench_pipeline/24_gate_n1_chain.sh 2>&1 | tee ~/qbar_d21.log
  ```

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

   Expected: `[step 1]` to `[step 5] PASS`, step 6 `SKIP` (no learned
   mode yet), and `preflight marker written: build-dbbench/PREFLIGHT_PASSED`.
   Step 4 runs ACT-4 and then ARCH-5 (`PARITY_CHECK=arch5`: the plugin in
   hold-only mode against the native arm); step 5 runs one `rules` arm at 1M
   with smoke placeholders, which must make at least one change and pass
   ACT-3, then one `rules` arm whose bounds the plugin refuses, which must
   run to the drain in fallback (03 exits 8 on it, by design).
   Reports: `build-dbbench/preflight/act1_report.json`,
   `build-dbbench/preflight/act4/act4_report.json`,
   `build-dbbench/preflight/arch5/arch5_report.json`,
   `build-dbbench/preflight/evaluator/graphs/summary.csv`,
   `build-dbbench/preflight/rules_smoke_report.json`,
   `build-dbbench/preflight/rules_fallback_report.json`.

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
      A level reported `undecided` is not pooled and is not decided later
      (D-19); the rung follows the deepest pooled level, or L2 when the cell
      has no pool.
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

      Record q̄ with `26_record_qbar.py`, after the q̄ arms and before any
      Θ_s arm. It refuses rows that are not what D-14 §2 describes (five
      scored `native` arms at T=10 without prices, one session and binary,
      the pipeline's default options and the workload's own fields, and the
      rung Gate N1 chose, read from the workload's `admission_T*.json` in
      its `--admission` folder) and a contract that already holds the value.
      It prints q̄ and a drafted dated record for PREREGISTRATION. `--write`
      amends the contract in place (the value, an `amendments` entry,
      `status_note`); commit it with the record.

      ```bash
      python3 scripts/dbbench_pipeline/26_record_qbar.py \
        --summary assoc=/mnt/nvme/qbar-assoc/graphs/summary.csv \
        --admission assoc=/mnt/nvme/n1-assoc \
        --summary powerlaw_get95=/mnt/nvme/qbar-powerlaw/graphs/summary.csv \
        --admission powerlaw_get95=/mnt/nvme/n1-powerlaw \
        --output ~/qbar_record.md --write
      ```
   3. **The device prices** (OBJ-2, Gate N0 item 7; PREREGISTRATION D-15
      §3 as amended by D-20), about 25 minutes: three settled trees at
      T = 2, 6 and 10 for the read prices, every read run twice, at the
      experiments' `open_files` and with every table open (so c_f, c_blk
      and c_sk exclude table reopens, and c_open prices them), and the q̄
      arms' own flushes and compactions for c_w, so it runs after step 2
      and reads both workloads' `summary.csv`:

      ```bash
      CONFIRM_PRICE_CALIBRATION=YES DB_ROOT=/mnt/nvme/prices-db \
        scripts/dbbench_pipeline/18_calibrate_prices.sh \
        /mnt/nvme/qbar-assoc/graphs/summary.csv \
        /mnt/nvme/qbar-powerlaw/graphs/summary.csv
      ```

      It stops in seconds, before any node time, unless `THREADS` is 1 and
      the q̄ rows are settled `native` arms at T=10 on this binary, each
      given once, at least five of each workload, and when the open-file
      limit is under 16,384. It writes `build-dbbench/prices.json` (schema
      4), which `03` copies into every later arm and records in the
      fingerprint; the medians and spreads are in it. Schema 4 (D-21) adds
      `reopen_timer`: the capped Gets' time per reopen by the fork's own
      timer (`rocksdb.read.table.reopen.nanos` over
      `rocksdb.read.table.reopen`), the reference for every run's reopen
      check, and `qbar_reopen_checks`, each q̄ arm's own time against it.
      `04` prices a run's reopens (the fork's count) only when its own time
      per reopen is within the contract's `reopen_time_check` tolerance
      (10%) of the reference; outside it, the arm's `objective_status` is
      "c_open does not hold" and that workload's c_open must be measured on
      it by a dated entry. Under 1,000 reopens the check does not apply.
      Run 18 before the preflight when one exists: the preflight's evaluator
      smoke prices its arm with this file, and `04` refuses a schema-2 or
      schema-3 one.
   4. **The two measured profiles** of Θ_s (D-14 §3), per grid point, from
      that point's `native` arms (run them first, in the same session). 25
      does this step. By hand, run it as 25 does, from the point's
      `<size>M/T<T>` folder with relative run paths, or 25 will refuse the
      `profiles.json` (it records the runs relative to that folder; list only
      the settled runs):

      ```bash
      cd /mnt/nvme/n2-assoc/T<T>-b<base>-k<K0>/<N2 size>M/T<T>
      python3 <repo>/scripts/dbbench_pipeline/23_static_profiles.py \
        repeat-*/native \
        --output /mnt/nvme/n2-assoc/T<T>-b<base>-k<K0>/profiles.json
      ```

      It prints `STATIC_PROFILE_survival_weighted=…` and
      `STATIC_PROFILE_last_level_emptying=…` for the next `03` call, and
      records the inputs, the measured overlap constants and any clipping in
      `profiles.json`. `03` checks each vector as RocksDB will (one entry per
      level, entry 0 = 1, entries in [0.5, 2.0], no level's target below
      the one above it).

   **Before the Θ_s runs, the repeat design (D-17).** `27_screen_design.py`
   reports, from the q̄ arms' `summary.csv` files and `prices.json`, the
   run-to-run noise of one configuration (with its 95% interval), a Monte
   Carlo of how often a screen of 2 or 3 runs per configuration would drop a
   true hull point or a clearly worse one, and runs and node-hours per
   design. Every assumption is a flag; `--help` lists them.

   ```bash
   python3 scripts/dbbench_pipeline/27_screen_design.py \
     /mnt/nvme/qbar-assoc/graphs/summary.csv /mnt/nvme/qbar-powerlaw/graphs/summary.csv \
     --prices build-dbbench/prices.json --json ~/screen_design.json
   ```

   Pass `--qbar assoc=<q̄> --qbar powerlaw_get95=<q̄>` until 26 has
   recorded q̄. 27 reads only those three files, so it can run off the node.

   **The preflight marker.** 25, 26, 27 and the changes that came with them
   live in `scripts/dbbench_pipeline`, which the marker hashes (untracked
   files included). Bringing them onto the node therefore changes the code
   hash: run a fresh preflight (13) before the first Θ_s arm, and never
   pull or copy them while 24 is running.

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

   **Unattended:** `25_gate_n2_chain.sh` runs all of the above for both
   workloads, each in its own process, at the run length in the Gate N1
   reports under `$NVME/n1-<workload>`. Give `NVME` as an absolute path (a
   relative one is read from the repository root, as in 24).
   `N2_RUN_LENGTH_<workload>="<size M> <load %>"` replaces the rung. Per T,
   it runs every point's `native`
   and `uniform_0_75` arms, repeat by repeat across the points, then 23 at
   each point, then the two measured profiles, each with its own point's
   vector. A profile 23 refuses
   (D-14 §3) is skipped and its reason printed (`=== <workload> <point>:
   refused …`, listed again at the end of the workload); any other failure
   of 23 stops that workload. It refuses without q̄ in the
   contract, without a schema-2 `build-dbbench/prices.json` measured on this
   `db_bench`, or
   without a preflight marker for this code (rerun 13 after any change), and
   runs the disk checks of 24. See the cost first; the listing runs nothing:

   ```bash
   NVME=/mnt/nvme scripts/dbbench_pipeline/25_gate_n2_chain.sh plan | grep '^#'
   NVME=/mnt/nvme scripts/dbbench_pipeline/25_gate_n2_chain.sh 2>&1 | tee ~/gate_n2.log
   ```

   - The full Θ_s is 480 runs per workload at five repeats (about 160 s per
     10M operations each). `N2_T=10` runs one T (a later run of other T
     values needs `RESUME=1`); `N2_WORKLOADS=assoc` one workload;
     `N2_REPEATS=2` or `3` a screen: every other arm runs that many, but
     the native arms still run five, since the measured profiles are
     computed from five (D-13 §5). That is the screen 27 costs.
   - The free-space check is 24's (`MIN_FREE_GB`, default 60), sized for
     Gate N1, not for Θ_s's results: size it from `du` of one arm under
     `$NVME/n1-<workload>` times the planned runs. A full disk fails the arm
     and stops the workload.
   - A named set instead of the sweep, for a top-up or the cross-T check
     (after T=10 is scored): `N2_CONFIGS=<file>`, one configuration per line,
     `<workload> <T> <base MiB> <K0> <profile> <repeats>`, repeats being the
     total wanted, e.g. `assoc 14 16 4 survival_weighted 5`. A measured
     profile at a point without `profiles.json` (every cross-T point) runs
     that point's five native arms first. At most one cross-T configuration
     per mode and workload (D-13 §4). A path is read from where 25 starts.
   - A point's `profiles.json` is written once, from at least five native
     runs, and records them; resumes and top-ups reuse its vectors, and 25
     stops if those runs have changed since. After a failure, delete the
     failed arm's result and database folders, then rerun with `RESUME=1`.

   Score and build the hull per (workload, T):

   ```bash
   scripts/dbbench_pipeline/04_generate_graphs.py --results /mnt/nvme/n2-assoc --summary-only
   scripts/dbbench_pipeline/frontier_analysis.py /mnt/nvme/n2-assoc/graphs/summary.csv \
     --workload-profile assoc-v1 --size-millions <N2 size> --size-ratio 2 \
     --output /mnt/nvme/n2-assoc/frontier_T2.json
   ```

   `frontier_T2.json` holds the hull, θ*_β for every mode, β* and c_s scale,
   and β̄.

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

Every default (geometry, record size, workload family, matrix) is in
`config.sh` and can be overridden from the environment. `REPEATS` creates
distinct repeat directories, pairs workload seeds and alternates arm order.

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
- Put `DB_ROOT` on the storage device under evaluation, not a RAM-backed
  `/tmp`.

To resume, reuse the same paths with `RESUME=1`. Only arms with a `COMPLETED`
marker are skipped; inspect or remove a partial arm manually.
