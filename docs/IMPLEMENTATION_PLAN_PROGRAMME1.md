# Implementation plan — Programme 1 (2026-09-29)

This turns `docs/PATHWAYS.md` (revision 2026-09-29, with the review fixes) into
a file-by-file build plan. "The review" is the 2026-09-29 theory review, removed
from the tree on 2026-10-01; read it with
`git show cb45743:docs/PATHWAYS_THEORY_REVIEW_2026-09-29.md`. PATHWAYS remains the authority on *what* the system
must do; this file says *where* each piece goes and *in what order*. Where
this plan and PATHWAYS disagree, PATHWAYS wins, and this file must be updated.

RocksDB paths are in the fork `lib/rocksdb` (branch `rl-compaction-policy-new`),
read at the recorded commit `25468bbaa`. The fork today adds about 6,350 lines
to upstream RocksDB 11.1.1 (`6cdeb9d9d`) across 40 files.

## 0. Decisions this plan rests on (owner, 2026-09-29)

1. **The controller is a separately loaded shared library** (`.so`) behind a
   small host interface in the fork. Changing the controller does not change
   `db_bench`'s hash, so the static comparator stays valid (PATHWAYS H §6).
2. **The old trigger-controller stack is deleted from the Programme 1 path.**
   - Deleted: compaction style 4, `RLCompactionPicker`, the socket client
     (protocol v2) and `RLControlCoordinator`.
   - The guard (`RLSafetyController`, `rl_safety_manifest`) is parked, unbuilt,
     for Programme 2.
   - Gate N6's "plugin versus socket" comparison uses the new plugin's
     remote-inference mode, not the old stack.
3. **Test suites, and a preflight before every long run** (owner,
   2026-09-29, reversing the 2026-09-13 "no tests" rule; CLAUDE.md "Tests").
   Every work package ships with its tests. A multi-hour node run starts only
   after a preflight marker that matches the exact binaries and code (§6).
4. **Level targets never shrink going down the tree** ($m_{i+1}T \ge m_i$).
   This is enforced by option validation and by the controller's masks
   (PATHWAYS A-Impl-7).

## 1. Architecture

```
db_bench (fork binary; hash unchanged by controller edits)
 ├─ RocksDB fork
 │    ├─ option level_target_multipliers  → capacity_scales_ → MaxBytesForLevel
 │    ├─ instruments: per-level read counters, per-job records, H samples
 │    └─ host interface (include/rocksdb/rl_controller_host.h)
 │          snapshot view · counters · job records · batched SetOptions
 ├─ --rl_host_log: host log written on EVERY arm (native and static included)
 └─ --rl_plugin=librl_controller.so (controller arms only)
        plugin thread: state → masks → Q = b + f_θ + δ_j → action → SetOptions
        modes: hold-only · rules · prior-only · learned · remote-inference
        writes: decision log + transition log      reads: weights file
                              │                          ▲
                              ▼                          │ every Δ_push
                 rl_agent/trainer.py (Python): rewards, Double DQN, export
```

Three rules carry over from CLAUDE.md:

- nothing slow runs under the DB mutex;
- the controller only sets triggers and multipliers, and RocksDB picks every
  file;
- every decision is logged with its requested and effective values.

## 2. RocksDB fork changes

### WP1 — `level_target_multipliers` (Gate N0 item 2)

| Change | Files |
| --- | --- |
| New option `std::vector<double> level_target_multipliers`, empty meaning all 1 | `include/rocksdb/advanced_options.h` |
| Mutable column-family option: struct field, string parsing as a vector of doubles (so `SetOptions` accepts `"1:1.5:0.8:…"`), copy into and out of `ColumnFamilyOptions` | `options/cf_options.{h,cc}`, `options/options_helper.cc` |
| Validation (rejected with an error, never clamped). Rejected when: the size differs from `num_levels`; entry 0 is not 1.0; an entry lies outside $[m_{\min}, m_{\max}]$ or is not finite; dynamic level sizing is on; the style is not `kCompactionStyleLevel`; a level target shrinks going down | `db/column_family.cc` (`ValidateOptions`; `SetOptions` calls it, not `SanitizeOptions`) |
| Apply: every new version copies the option into the existing `capacity_scales_`, which `MaxBytesForLevel` already multiplies in. The existing limits change: values below 1 are allowed, and the last level is un-pinned. L0 stays unscaled, as the code already does | `db/version_set.{h,cc}` (the capacity-state inheritance and `MaxBytesForLevel`) |
| Delete the `RL_STATIC_CAPACITY_SCALES` environment-variable path and the unused `SetCapacityScales()` | `db/version_set.{h,cc}` |
| `db_bench --level_target_multipliers=…` for static profiles in $\Theta_s$; a `setoptions` step (`--setoptions="k=v;…"`, one `DB::SetOptions` call) so ACT-1 can test the SetOptions path on the Release binary | `tools/db_bench_tool.cc` |

This needs no score code of its own. The score, and the pending-bytes
estimate (A-Impl-3), both read `MaxBytesForLevel`. `SetOptions` already
appends a new version and recomputes every score before it returns
(A-Impl-4, verified). The L0 trigger needs no fork change; the plugin clamps
it to $[2, K_{\text{cap}}]$, because `SetOptions` does not re-check
$K_0 \le K_{\text{slow}}$.

### WP2 — host interface (prerequisite of WP5)

New public header `include/rocksdb/rl_controller_host.h`, plus its
implementation `db/rl_controller_host.{h,cc}`. It exposes:

- **`Snapshot()`**, published at every score computation with no DB mutex
  held. It extends today's `CompactionPressureView`, which carries only score
  and "due since". Per level: bytes, bytes being compacted, target bytes,
  score and "due since". For the tree:
  - the score order;
  - the start level of the running compaction (−1 if the slot is idle);
  - the pending-compaction estimate;
  - live SST bytes $H$;
  - the active memtable's bytes;
  - the snapshot generation.
- **`OpCount()`**: the sum of the `NUMBER_KEYS_WRITTEN`, `NUMBER_KEYS_READ` and
  `NUMBER_DB_SEEK` tickers.
- **`ReadCounters()`**: the per-level counters of WP3, as an array.
- **Job records**, pushed to a callback on start and completion: start and
  output level, trivial flag, $S$, $O$, $X$, start and end operation count,
  and the level's "due since" at start. Hooked where the fork already hooks
  `NotifyOnCompactionCompleted`, plus the matching begin site.
- **`Apply(m, k0)`**: one `DB::SetOptions` call carrying both options. It is
  called from the plugin's thread and never under the DB mutex.
- **Plugin loading**: `dlopen` of `--rl_plugin`, and the C entry points
  `rl_controller_create(host, config_path)` and `rl_controller_destroy`.

### WP3 — instruments (Gate N0 item 3)

| Instrument | Site | Note |
| --- | --- | --- |
| Per-level filter probes | `Version::Get` loop, where `POINT_SST_PROBE` already ticks (`db/version_set.cc`), keyed by the FilePicker's current level | Must use the version's level. The table reader's own level is stale after a trivial move |
| Per-level filter passes, and hits | The filter outcome is written into `GetContext` (`rl_filter_passed`, `rl_filter_hit`) where `BLOOM_FILTER_FULL_POSITIVE` and `BLOOM_FILTER_FULL_TRUE_POSITIVE` tick in `BlockBasedTable`, and read after each `table_cache_->Get`. The hit is counted at the found level | False positives = passes − hits. The hit's read feeds the shared hit-read bucket (D §4) |
| Per-level seeks | At the same three sites `SORTED_RUN_SEEK` ticks. `TableCache::NewIterator` gives the table iterator the version's level (`InternalIteratorBase::SetReadCounterLevel`, a no-op except in `BlockBasedTableIterator`); `LevelIterator` uses its own | An iterator built outside a version has level −1 and is not counted |
| Per-job $S$, $O$, $X$, trivial flag, source level, due-since | `CompactionJobInfo` gains $S$, $O$ and the start level's due-since, filled from the `Compaction` in `BuildCompactionJobInfo`. The due-since is read before `PickCompaction`, whose score recompute ends the due episode. The host log is an `EventListener` on these | Excluding trivial moves from $\rho_i$, $o_i$ is then a flag, not an inference. WP2's job-record callback reuses them |
| Fix the dead trivial-move telemetry | `db/db_impl/db_impl_compaction_flush.cc`: pass `num_input_files_trivially_moved > 0` instead of `is_trivial_move()`, and the moved bytes (`CalculateTotalInputSize()`) for a trivial move | Review finding, Gate N0 item 3. Old stack only |
| $H$ with the operation count at every version install | Host log, after every flush and compaction install (the only installs that change $H$) | Written on every arm, native included |

Counters are process-wide relaxed atomics indexed `[level][kind]`
(`db/rl_read_counters.h`), following the fork's existing
`RLCompactionTelemetry` singleton. The workload has one client thread, so
contention is negligible. Their cost shows up in ACT-4 parity.

### WP4 — `db_bench` (Gate N0 items 4 and 5)

- **`--rl_host_log=<path>`**, on every arm. It writes H samples, job records,
  counter snapshots and the settle and phase stamps (`db/rl_controller_host.h`
  documents the records). It needs `--statistics`; a lost record ends the run.
- **Settle, then measure** (PATHWAYS H §5, PREREGISTRATION D-13).
  - A new `db_bench` step, `settle`, runs between the load and `mixgraph`.
    It calls `WaitForCompact` with flushes included, then holds for
    $h_w$ = 10 s (`--rl_settle_hold_seconds`), checking every 100 ms that
    every level's score stays below 1 and $k_0 < K_0$. The score check is
    RocksDB's `compaction-pending` property, which is also set by a file
    marked for compaction. It prints `RL_SETTLED` with the outcome and the
    time waited, and stamps the host log. The fork's `waitforcompaction` step
    is not reused, because it also enters the old stack's drain mode.
  - `mixgraph` prints `RL_MEASURE_START_OP` at its first operation ($n_w$),
    and the `measure_start` stamp snapshots the tickers and the stall
    counter there (`db.user_write_stall_micros`, the counter behind the
    internal-stats "Cumulative stall" line).
  - An arm whose hold fails is invalid (A8); `settle` ends the run.
  - The existing `rlsuspend`/`rlresume` steps stay only as long as the old
    evaluator needs them.
- **Drain** under fallback settings: the plugin stops, and the host applies
  $m \equiv 1$ and the configured $K_0$ before the existing drain.
- **Later, for Pathway B:**
  - the phase patch, clearing `QueryDecider`'s `type_` and `ratio_` at each
    phase;
  - deletes as operation index 3.

### WP10 — retire (after the plugin passes ACT-4 and ARCH-5)

Remove from the build, and from `src.mk`, `CMakeLists.txt`, the `Makefile`
and BUCK:

- `compaction_picker_rl.{h,cc}`, `rl_compaction_client.{h,cc}` and
  `rl_control_coordinator.{h,cc}`;
- `kCompactionStyleRL` and the allowed-source-level mask in
  `compaction_picker_level.{h,cc}`;
- the attach and detach hooks in `db_impl`.

The guard files are parked, meaning not compiled. Keep:

- `compaction_pressure_observer`, as the base of `Snapshot()`;
- the counters `POINT_SST_PROBE` and `SORTED_RUN_SEEK`;
- the telemetry parts WP3 reuses.

Every new or removed `.cc` must be registered in `src.mk`, `CMakeLists.txt`,
the `Makefile` and BUCK (`lib/rocksdb/CLAUDE.md`).

## 3. Controller plugin (new top-level `controller/`, Gate N0 item 6)

Built by its own CMake target into `librl_controller.so`, against the fork's
headers. Its SHA-256 goes in the fingerprint, separately from `db_bench`'s.

| File | Contents | PATHWAYS |
| --- | --- | --- |
| `plugin.cc` | Entry points, the decision thread, cadence per level every $N_j/k$ operations, L0 once per flush ($N_0/K_0^{\text{cfg}}$, G-iv as amended 2026-10-02) (nothing is due during a write stop), and batching changed values into one `Apply` | G-iv, A-Impl-5 |
| `actions.{h,cc}` | Per-level anchor $\bar m_i$ and timing factor $d_i$ with relaxation ($\kappa_d\tau_i$) and anchor decay ($\kappa_a\tau_i$), and the actions. **"Expand" keeps the level deferred**: $d_i \leftarrow \max\{1, \varphi_i(1+\epsilon)/\bar m_i^{\text{new}}\}$. The L0 actions work on $K_0$ | A §2, review fix 1 |
| `masks.{h,cc}` | "Allowed when" rules, bounds, $\varphi_{\min} \ge m_{\min}(1+\epsilon)$, defer only while $\varphi_i(1+\epsilon) \le m_{\max}$, targets never shrinking going down, $K_0 \in [2, K_{\text{cap}}]$ | A §2, A-Impl-6, A-Impl-7 |
| `state.{h,cc}` | The normalised state of G §3 and H §2, including queue position, backlog, the fill two levels down, the bottom level's room, the slot's idle flag, and the memtable's fill. **This is the only place state is computed**; it is logged | G §3, H §2 |
| `prior.{h,cc}` | Analytic prior $b$ over one turnover: compaction at the current overlap plus the slot-blocking charge; deferral by garbage and burst; expansion by the space bound; early L0 compaction. Clipped to $\pm b_{\max}$. **$b$ for every action is logged** at every decision, so Python never re-implements it | H §7 |
| `mlp.{h,cc}` | Forward pass of $f_\theta$ (shared) and $\delta_j$ (per-level, linear) in plain C++. Loads a versioned weights file (atomic rename plus checksum) every $\Delta_{\text{push}}$ | G §3, H §6 |
| `policy.{h,cc}` | Modes: **hold-only** (parity, ACT-4 and ARCH-5), **rules** (Gate N3), **prior-only**, **learned**, and **remote-inference** (Gate N6: the same policy, with Q computed by the trainer over a socket). Exploration departs from hold with probability $p_x$, with a floor, and never takes a masked action | H §5, Gate N3, Gate N6 |
| `rules.{h,cc}` | Gate N3's hand-written rules: $K_0$ tracking D.11, release on neighbour fill, hold for garbage, L0 early while the slot is idle, and interior levels yielding the slot | Gate N3 |
| `attribution.{h,cc}` | Per-interval, per-level cost parts, in counts and bytes: write bytes by source level, probes, false-positive reads, seeks, hit-read bucket, **slot-blocking re-attribution** (share $(k_0-K_0)^+/k_0$ of L0's reads, while L0 is due and a level-$i$ job holds the slot), $B_i$ and $\hat{\tilde\rho}_i$ for $g_i$, and operations served | D §4 |
| `log.{h,cc}` | Decision log (requested and effective values, reason, weight version) and transition log. One JSON line per level decision: state, mask, $b(\cdot)$ for every action, action, cost parts, next state, $\Delta N$ | A-Impl-8, H §6 |
| Fallback | Weights missing or invalid, or plugin error: $m \equiv 1$ and the configured $K_0$, logged as a fallback | A-Impl-8 |

## 4. Python changes (`rl_agent/`, Gate N0 item 6)

| File | Change |
| --- | --- |
| `trainer.py` (new, replaces `server.py`) | Tails the transition log; turns cost parts into rewards; trains; exports weights atomically every $\Delta_{\text{push}}$ with a version number. No request/response loop |
| `reward.py` (new) | $r_i = -(c^\beta_i + X_{i+1} + X_{i-1})/(c_wC_i)$. $c^\beta_i$ comes from the logged parts at money prices, with $\beta$ and $q/\bar q$ on the garbage term; the hit-read bucket is excluded. $X$ comes from the neighbour's value head and a one-step burst and overlap prediction. It implements both "echo" variants (§0.6 item 11). $C_0 = K_0^{\text{cfg}}F$ |
| `model.py` | Shared $f_\theta$ (a small MLP on the normalised state). Per-level $\delta_j$ as linear heads with ridge penalty $n_0$, weighted by turnover length (Lemma G.5). Separate models for L0, the last level and levels that failed admission. **Export in `mlp.cc`'s format** |
| `agent.py` | Double DQN with the **masked target** (the argmax is taken only over actions allowed in $s'$). Per-transition discount $\exp(-\Delta N/(n_HN_j))$. Target network, Huber loss, gradient clipping. Replay keeps only intervals with valid attribution. Remove the Boltzmann temperature and the per-second discount |
| `membership.py` (new) | PROP-1b: a one-step model per pooled level, and held-out residual intervals. A failing level leaves the pool at the next weight push, and the removal is logged |
| `remote_inference.py` (new, small) | The Gate N6 socket responder. It evaluates the same exported weights |
| `config.py` | Replace the 37 old state inputs (`ML_STATE_FIELDS`) with the G §3 and H §2 lists. Add $\beta$, prices, $\bar q$, $n_H$, $k$, $n_0$, $p_x$, $\Delta_{\text{push}}$, $\epsilon$, $\alpha$, $\kappa_d$, $\kappa_a$, the bounds, $\varphi_{\min}$ and $b_{\max}$ |
| `replay_buffer.py` | Scalar rewards only; drop the "price at sample time" vector path |
| Delete | `lagrange.py`; `multilevel.py` (the protocol v2 processor; its prior moves to `prior.cc`); `server.py`, after `remote_inference.py` exists |

## 5. Pipeline and evaluator (`scripts/dbbench_pipeline/`)

| Stage | Change | PATHWAYS |
| --- | --- | --- |
| `03_run_experiments.sh` | Arms: `native`, `static:<profile>` ($\Theta_s$), `learned`, `prior_only`, `rules`, and the ablations. New flags: multipliers, plugin, mode $\beta$, $\bar q$, prices. Same-session interleaved order, with a session id. The fingerprint gains mode, $\bar q$, prices, multiplier profile, plugin hash and $h_w$, emitted conditionally so older runs still pool; the regex in `fingerprint.py` must change in lockstep. The SLO manifest is no longer required for Programme 1 arms. **Built at step 5 for Gate N2:** `native` and `static:<profile>` (`uniform_1` = native, `uniform_0_75`, or a vector in `STATIC_PROFILE_<name>`), `settle` between the load and `rlresume`, `--rl_host_log` on every arm, the session id, the power-law family (`WORKLOAD_SKEW=2`, D-13 §3), and the segments `pow`, `settle`, `qbar` and `prices`. Mode, plugin and the controller arms come with step 8 | C §3, CMP-8, OBJ-5 |
| `04_generate_graphs.py` (`collect_arm`) | Measured phase from $n_w$ to the end of the drain, from the op stamps. $\mathcal C_W$ from event-log SST bytes (D-11's windowing); $\mathcal C_R$ from `POINT_SST_PROBE`, `rocksdb.bloom.filter.full.positive` and `SORTED_RUN_SEEK` differenced from the $n_w$ snapshot; $\mathcal C_S = (c_s/\bar q)\sum H \times$ operations from the host log. $J_\beta$ per mode and $\beta^\star \in \{2,5,10\}$, at $c_s$, $c_s/2$ and $2c_s$. Stall seconds are the stall counter differenced from `measure_start` to `drain_end`; the stall fraction and throughput divide by mixgraph's wall time (`measure_start` to `drain_start`; PREREGISTRATION D-14 §1, the span of $\bar q$). Self-checks of Gate N0 item 5, plus: the event log's compaction bytes in the window equal the host log's. An arm whose settle failed, whose host log is inconsistent or whose self-check fails is refused and listed, never scored | D §1, OBJ-6 |
| `07_evaluate_paired.py` | Paired intervals on $J_\beta$ differences (CMP-3), the stall rule, regret (C.5) and suite robustness (CMP-7) | Global acceptance |
| `frontier_analysis.py` | Lower convex hull over $(\mathcal C_W, \mathcal C_R, \mathcal C_S)$; $\theta^\star_\beta$ per mode; $\bar\beta$ | C.4, D.4 |
| `compaction_measurements.py` | $\rho_i$, $o_i$, $t_i$, $\xi_i$, $\eta_i$ and dropped bytes per source level, from host job records | B §2 item 4 |
| `18_calibrate_prices.{sh,py}` (new) | Core-seconds per byte written, per filter probe, per block read and per seek, converted at the price per core-second; $c_s$ from the storage price. Checks $c_s > 0$. Method fixed by PREREGISTRATION D-15 §3: $c_w$ from the $\bar q$ native arms' own flush and compaction jobs (`04`'s `sst_write_seconds`), the read prices as marginal times from least-squares fits across three settled trees at $T$ = 2, 6 and 10, five interleaved repeats of readmissing, readrandom and seekrandom, one `db_bench` process each; medians with their spread. Writes `PRICES_FILE` | OBJ-2 |
| `19_admission_test.py` (new) | The collapse test: per-turnover statistics, moving-block bootstrap, the every-statistic-must-pass test, margins, and the $n_{\min}$ simulation. Records pool membership per (workload, $T$). Reads host logs (job records) and the event log's `compaction_release` occupancy; every value it uses comes from `config/admission_test.json` (PREREGISTRATION D-16): margins per $T$, $n_{\min}$ by its simulation rule, candidates from L2 to the settled tree's $L-2$, plus $L-1$ when the last level holds half its target on the mean of the cell's runs, and the run-length rung per cell. Refuses runs `03` did not complete. `24_gate_n1_chain.sh` runs the preflight, Gate N1, the $\bar q$ arms and `18` unattended, each workload in its own process; `test_gate_n1_chain.py` runs it end to end with a stand-in `db_bench`. Gate N1 runs on pilot native arms (D-16); pre-host-log artifacts are out of scope | G §4, Gate N1 |
| `23_static_profiles.py` (new) | $\Theta_s$'s two measured profiles from `native` arms of one point (PREREGISTRATION D-14 §3): survival-weighted (Theorem A.2(ii), $f_i \propto 1/v_i$) and last-level-emptying, with the inputs, the measured $c_i$ and any clipping reported | C §1, A §3 |
| `20_check_actuation.py` (new) | The ACT-1 checks (§6) on the Release `db_bench`: a frozen tree reopened under several vectors, and `db_bench`'s new `setoptions` step. ACT-3 fidelity, from the host and decision logs, joins it with the plugin | ACT-1, ACT-3 |
| `01c_build_stock_db_bench.sh`, `22_check_native_parity.{sh,py}` (new) | Stock `db_bench` build; ACT-4 runs and evaluation (§6.4 step 4) | ACT-4 |
| `21_check_learner.py` (new) | ARCH-2 masked-target audit, ARCH-6 agreement between C++ and Python evaluation of the same weights, OBJ-1 and OBJ-4 identities | ARCH-2, ARCH-6, OBJ-1, OBJ-4 |
| `09_evaluate_oracle_parity.py` | Removed 2026-10-02: the parity envelope (limits, score growth, LOG facts) it defined for ACT-4 and ARCH-5 moved into `22_check_native_parity.py`, values unchanged | ACT-4, ARCH-5 |
| `01b_build_test_trees.sh` (new) | A Debug CMake tree of the fork with only the fork's own test targets, and the plugin's test target, so `assert`s fire | CLAUDE.md "Tests", tier 2 |
| `13_run_preflight_verification.sh` (reworked) | The preflight of §6.4. It writes `PREFLIGHT_PASSED` bound to the hashes, and `03` and every long-run driver refuse to start without a matching marker | CLAUDE.md "Tests", tier 3 |
| Removed 2026-10-01 | `05`, `06_run_guard_protocol.sh`, `06_validate_guard_holdout.py`, `08`, `10_run_scaling_smoke.sh`, `11`, `12`, `14`, `15`, `17`, `d*_learner_run.sh` and the other old root drivers. Also removed 2026-10-02 (with D-19): `06_calibrate_live_guard.py`, `slo_statistics.py`, `04_generate_graphs.sh`, `06_select_baseline_slo.py` (its fingerprint parser moved to `fingerprint.py`) and `09_evaluate_oracle_parity.py` (the parts ACT-4 uses moved into `22_check_native_parity.py`). `10_validate_learning_health.py` stays until step 12, with the old learner arms in `03` | — |

## 6. Tests, checks and the preflight

The aim: every defect that a multi-hour node run could expose is caught first,
by a suite that runs locally in seconds or on the node in minutes. **A work
package is done only when its tests are written and green.** Tests check the
code against PATHWAYS. The gates still decide the research claims.

### 6.1 Tiers

| Tier | Where, how long | What runs | Command |
| --- | --- | --- | --- |
| 1 | Laptop, seconds; before every commit | Python suites (stdlib `unittest`, no new dependency); `-fsyntax-only` on every changed C++ file | `scripts/dbbench_pipeline/run_python_tests.sh` (each suite runs as `python -m unittest discover -s <dir>/tests -t <dir>`, so tests import their code by module name; an empty suite is reported, not failed); `g++ -fsyntax-only` with flags from `build/compile_commands.json` |
| 2 | Node, minutes; after every fork or plugin change | Fork gtest files and plugin tests in a **Debug** tree (`01b_build_test_trees.sh`, `-DWITH_TESTS=ON`, fork test targets only), so RocksDB's `assert`s fire | the built test binaries, run by `01b` |
| 3 | Node, about 30–45 minutes; before any long run | The preflight (§6.4) | `13_run_preflight_verification.sh` |

The pipeline's Release build (`01`, `-DWITH_TESTS=OFF`) stays as it is. Tier 2
uses a separate build directory, so measured binaries never carry test code.

### 6.2 C++ suites

**Fork** (new files; upstream RocksDB test files unchanged; gtest 1.8.1 is
bundled at `third-party/gtest-1.8.1`; registered in `CMakeLists.txt`, `src.mk`,
`Makefile` and BUCK):

| File | Cases |
| --- | --- |
| `db/level_target_multipliers_test.cc` | **Setting the option.** `SetOptions` parses `"1:1.5:0.8:…"`. The OPTIONS file persists the vector and a reopen restores it. **Rejected:** wrong size; entry 0 not 1.0; NaN or out of bounds; dynamic level sizing; non-leveled style; targets that shrink going down (all at open and by `SetOptions`). **Effect.** `MaxBytesForLevel(i)` is scaled for $i \ge 1$, and L0 is unchanged. L0's score is identical under any vector. A level's score equals bytes / (base target × $m_i$). The pending-compaction estimate uses scaled targets. After a `SetOptions` with no writes, the score reflects the new vector. At $m \equiv 1$, scores equal those with the option absent. The `RL_STATIC_CAPACITY_SCALES` path is gone |
| `db/per_level_read_counters_test.cc` | A tree with keys placed at known levels (`CompactRange` to target levels). A Get for a key at level $j$ counts one probe at each covering level above $j$ and at $j$, one pass and one hit at $j$, and false positives only as passes without hits. After a **trivial move**, the file's reads count at its new level. A seek counts one per L0 file and one per level with a file at or after the seek key, and nothing again when the scan crosses a file boundary. Per-level sums equal the global tickers (`POINT_SST_PROBE`, `rocksdb.bloom.filter.full.positive`, `SORTED_RUN_SEEK`) |
| `db/rl_controller_host_test.cc` | Snapshot: per-level bytes, bytes being compacted, score order, the running job's start level (−1 when idle), the pending estimate, $H$ = `rocksdb.live-sst-files-size`. The operation count equals the operations issued. Job records: a merge's $S$, $O$, $X$ and source level match the `Compaction`'s inputs. A trivial move is flagged with its moved bytes (the telemetry fix). `Apply(m, k0)` is one `SetOptions` and is refused while holding the DB mutex. A plugin is loaded and unloaded |

**Plugin** (`controller/tests/`, gtest linked from the fork's bundled copy):

| File | Cases |
| --- | --- |
| `actions_test.cc` | "Compact" sets the score to $1+\epsilon$ and "defer" sets it to $1/(1+\epsilon)$. **"Expand" keeps a deferred level deferred** and caps at $m_{\max}$. The timing factor relaxes over $\kappa_d\tau_i$. Anchors return to within $\epsilon$ of 1 after $5\kappa_a\tau_i$ (ACT-5). The L0 actions move $K_0$ within $[2, K_{\text{cap}}]$ |
| `masks_test.cc` | Every "allowed when" rule; the bounds; $\varphi_{\min} \ge m_{\min}(1+\epsilon)$; no action whose result makes targets shrink going down. **Over 10⁵ random states, exploration never picks a masked action** |
| `state_test.cc` | Level-free units: the same raw ratios at two levels give the same features. Queue position, backlog over $H$, and the absent flag when $j+2 > L$ |
| `prior_test.cc` | Signs and magnitudes on hand-worked cases: compaction priced at the current overlap plus the slot-blocking charge; deferral at garbage plus burst; expansion at the space bound; early L0 compaction. Clipping at $\pm b_{\max}$ |
| `mlp_test.cc` | The forward pass equals a golden file written by `rl_agent/tests/test_export.py` for fixed weights, to 1e-6. A bad checksum or a truncated weights file triggers fallback; a reload is atomic |
| `attribution_test.cc` | On synthetic intervals, the slot-blocking split sums to 1 and totals are preserved (D.16). Hit reads go only to the bucket. Write bytes go to the source level |
| `log_test.cc` | Decision and transition lines parse in Python (`rl_agent/tests/test_log_format.py` reads the same golden file) |

### 6.3 Python suites

`rl_agent/tests/`:

| File | Cases |
| --- | --- |
| `test_reward.py` | The D.16 identity on synthetic logs: per-level charges plus the hit bucket equal the global cost. Slot blocking moves cost without changing the total. $\beta$ modes; money prices with $c_s > 0$ enforced; $q/\bar q$ on the garbage term; the constant divisor $C_0 = K_0^{\text{cfg}}F$; neighbour charges zero on hold; both echo variants |
| `test_agent.py` | **Masked target:** the argmax never lands on a masked action (ARCH-2). Discount $\exp(-\Delta N/(n_HN_j))$. The Double-DQN target uses the online argmax and the target value. Replay drops invalid-attribution and fallback intervals. At cold start $Q = b$ |
| `test_model.py` | $\delta_j$ shrinkage equals $n_j/(n_j+n_0)$ times the own-data least-squares fit on orthogonal features (Lemma G.5). Pooled and separate models are selected by membership |
| `test_export.py` | Weights export and re-import round-trip; writes the golden file `mlp_test.cc` checks (ARCH-6 at unit level) |
| `test_membership.py` | PROP-1b: a level whose held-out residual interval leaves $\pm\delta_{\text{kern}}$ is removed at the next push, and the removal is logged |
| `test_trainer.py` | Tails a growing log; pushes versioned weights atomically; survives a truncated last line |
| `test_log_format.py` | Parses the plugin's golden decision and transition lines |

`scripts/dbbench_pipeline/tests/`:

| File | Cases |
| --- | --- |
| `test_evaluator.py` | A synthetic run directory with a known answer. The measured phase is windowed from the $n_w$ op stamp to the end of the drain. An arm whose `RL_SETTLED` reports a failed hold is refused. $\mathcal C_W$ from SST bytes only (OPTIONS and MANIFEST writes excluded). $\mathcal C_R$ from tickers differenced at $n_w$. $\mathcal C_S$ from H samples × operations. $J_\beta$ per mode and $\beta^\star$, at $c_s$, $c_s/2$ and $2c_s$ |
| `test_paired.py` | Paired intervals; the stall rule passes and fails on constructed cases; regret (C.5); suite robustness |
| `test_frontier.py` | Lower hull on toy points, including collinear ties (C.4). $\theta^\star_\beta$ per mode. $\bar\beta$ (D.4) |
| `test_compaction_measurements.py` | $\rho_i$, $o_i$, $t_i$, $\xi_i$, $\eta_i$ and dropped bytes on synthetic job records; trivial moves excluded from $\rho$ and $o$ |
| `test_admission.py` | Two resamples of one level pass with probability at least 0.8; a statistic shifted to its margin passes with probability at most 0.05; the combined test's size is at most 5% (§4 of Pathway G). Reproduces the KS figure (median 0.43 at 30 turnovers). D-16: the committed config validates at every $T$ with fill margins above the file granularity; $n_{\min}$ is the smallest sufficient grid value, or every candidate is undecided; the run-length rung follows the deepest pooled level, or the reference when the cell has no pool (D-19), at the slowest run; the settled tree and the candidate set of G §4's scope decision; runs loaded otherwise than the rungs, runs `03` did not complete, fingerprints without an SST size, and runs whose trees differ are refused; end to end on the fixture |
| `test_prices.py` | D-15 §3 on synthetic trees with a known answer: the fit returns the marginal times, not the average with the overhead; $c_w$ per run from the write rows; medians and spreads; the per-core price; the layout `18.sh` writes, end to end. Refuses: slopes not identified, `readrandom` not reading the found block, non-positive times, write rows that are not the $\bar q$ native arms on this binary, a run given twice, fewer than five of a contract workload, $c_s \le 0$ and missing money prices. `04` refuses a `prices.json` that is not per core-second |
| `test_fingerprint.py` | `03 --print-fingerprint` output is accepted by `parse_fingerprint_options`, both with the new segments and without them (older runs still pool). Built as: 03's own fingerprint line, expanded by bash |
| `test_run_experiments.py` | `03` with a stub `db_bench` replaying the evaluator fixture, then `04`: settle between the load and `rlresume`, the host log on every arm, each arm's own multipliers, fingerprints that parse in `fingerprint.py`, prices copied, every arm scored; the power-law family; an unsettled arm marked and reported while the matrix goes on; a resumed matrix keeping its session and prices; and the refusals before any run (bad arm, missing or invalid profile vector, `native` with `static:uniform_1`, the power law's mix and profile, an $h_w$ other than D-13's) |

Fixtures live in `tests/fixtures/`. `.gitignore` hides `*.log`, `*.txt` and
`*.json` there (confirmed with `git check-ignore`), so add a
`!**/tests/fixtures/**` rule in the same commit as the first fixture.

### 6.4 Preflight (tier 3)

`13_run_preflight_verification.sh`, reworked. Each step must pass before the
next starts:

1. Rebuild the Release `db_bench` and the plugin; record their SHA-256.
2. Tier 1 and tier 2 suites, all green.
3. **ACT-1 on the real binary** (`20_check_actuation.py`): L0 score
   invariance, the refusals, the scaled pending estimate, recompute without
   writes. Beside it, WP4's `settle` step: it passes on a settled tree and
   writes the host log, and it refuses a due tree.
4. **Parity** at 1M operations, T=2, on the 2026-08-22 gate's limits: patched binary at
   $m \equiv 1$ against stock (ACT-4); plugin in hold-only mode against
   native (ARCH-5).
   - **Stock** means upstream RocksDB 11.1.1 (`6cdeb9d9d`) with none of the
     fork's changes, built by `01c` with 01 and 02's flags. It is not the
     fork's native arm, which is ARCH-5's reference. Only a stock binary can
     show what the fork's patches cost, including WP3's instruments.
   - Stock has no phase stamps, no `POINT_SST_PROBE` or `SORTED_RUN_SEEK`, and
     no episode log. `22_check_native_parity.{sh,py}` therefore adapts the old 09's
     checks to instruments both binaries have:
     - whole-run figures, with the same upstream benchmark sequence on both
       arms;
     - point probes as filter checks, proven equal to `POINT_SST_PROBE` on
       every patched run;
     - the LOG's tree-wide maximum score;
     - stall as a fraction of the writing time, at D-13's 2-point margin;
     - seeks reported only.
   - The patched arm writes the host log, as every measured arm will, so its
     cost is inside parity. Each patched log is checked against itself: the
     per-level read counters sum to their tickers, every job that began
     ended, the last H sample equals the live SST bytes at the end of the
     drain, and operation counts never decrease.
   - **The evaluator smoke** (added at step 6, 2026-09-30): after ACT-4, one
     `03` `native` arm at 1M operations, T=2, run exactly as a Gate N2 arm
     (settle, host log, stamps), scored by `04`. The Gate N0 item 5
     self-checks must pass, so `04` must not refuse the arm, and the arm
     must be scored as a Programme 1 arm. It sits in step 4 because it needs
     the binary ACT-4 has just cleared, and static arms need steps 1–4.
5. **Rules-mode smoke** at 1M operations: zero masked actions, ACT-3 fidelity,
   logs complete, fallback exercised once by an injected bad weights file.
6. **Learner smoke** at 2M operations, T=2, read priority:
   - the trainer receives transitions and at least three weight pushes
     reach the plugin;
   - ARCH-2 audit is 0 and ARCH-6 agreement is at least 99.9%;
   - OBJ-1 and OBJ-4 identities hold;
   - the Gate N0 item 5 evaluator self-checks pass;
   - no NaN, and the stall rule is computed.
7. Write `PREFLIGHT_PASSED` with the `db_bench` and plugin SHA-256, the
   git tree hashes of `rl_agent/`, `controller/` and
   `scripts/dbbench_pipeline/`, and the date.

`03_run_experiments.sh`, and every long-run driver, refuses to start unless
the marker matches all current hashes. The contract and fingerprint already
refuse stale binaries; the marker adds the code and the checks.

### 6.5 Gate checks that stay on the node

These measure the running system, so they live in gate stages as well as in
the preflight:

| Check | How |
| --- | --- |
| ACT-2 | Paired throughput, controller at hold-only against native |
| ACT-3, ACT-5 | Decision log against host snapshots |
| ACT-4, ARCH-5 | `22_check_native_parity.py` at full gate size |
| ARCH-6 | Q logged by C++ against Python recomputation, for every weight version |
| OBJ-1, OBJ-4 | Attribution log against event log and tickers |

## 7. Order of work

The learner comes last on purpose. Gates N2 and N3 need no learner, and
Gate N3 decides whether the stationary-workload claim survives before the
learner is built.

Every step's tests are written with its code and must be green before the
step counts as done. Long node runs (steps 7, 9 and 11) need a matching
preflight marker.

| Step | Work packages | Tests that must be green | Unlocks | Node needed |
| --- | --- | --- | --- | --- |
| 1 | PREREGISTRATION entries for everything Gate N2 uses: objective, money prices, $\bar q$, $\Theta_s$, the settle rule, stall margins | — | Gate N0 item 8 | No |
| 2 | Test infrastructure: `01b_build_test_trees.sh`, the `tests/` folders, the fixtures `.gitignore` rule, `13` reworked with steps that skip until their component exists | Suites discovered and run (empty is fine) | Every later step | Build |
| 3 | WP1 option, plus the `db_bench` multiplier flag | `level_target_multipliers_test`; preflight steps 3–4 | ACT-1, ACT-4 | Build and short runs |
| 4 | WP3 instruments and WP4 host log and stamps (same fork commits) | `per_level_read_counters_test`; the host-log parts of `rl_controller_host_test` | Gate N0 items 3–4 | Build |
| 5 | Evaluator: `04`, `07`, `frontier_analysis`, `compaction_measurements`; `18` prices; `19` admission | `test_evaluator`, `test_paired`, `test_frontier`, `test_compaction_measurements`, `test_prices`, `test_admission`, `test_fingerprint` | Gate N0 items 5 and 7, Gate N1 | Short (prices) |
| 6 | Preflight up to step 4 passes; marker written | Tiers 1–3 as built so far | Long runs of steps 7 | Yes (about 30 minutes) |
| 7 | Gate N2 static comparator runs | Marker matches | $\theta^\star_\beta$ per mode | **Yes (long)** |
| 8 | WP2 host interface; plugin in **hold-only** and **rules** modes | `rl_controller_host_test`, `actions_test`, `masks_test`, `state_test`, `prior_test`, `attribution_test`, `log_test`; preflight step 5 | ACT-3, ARCH-5, Gate N3 | Build and short runs |
| 9 | **Gate N3 runs, then verdict.** If no rule beats $\theta^\star_\beta$, narrow the claim before step 10 | Marker matches | — | **Yes (long)** |
| 10 | Plugin **learned**, **prior-only** and **remote** modes; trainer, reward, model, membership | `mlp_test`, all `rl_agent/tests`; full preflight including step 6 | Gate N4 | Build and short runs |
| 11 | Gate N4 onward | Marker matches | Gates N4–N6 | **Yes (long)** |
| 12 | WP10 retire the old stack | All tiers green after removal | — | Build |

Each fork change is committed in the submodule first, then the root pointer is
bumped (CLAUDE.md). The contract records the new fork revision and its parent.

## 8. Repository gotchas this plan hits

- **`.gitignore`** ignores `*.txt`, `*.json` and `scripts/*`, then
  re-includes paths with `!` rules. `git check-ignore` confirms that
  `controller/CMakeLists.txt` (rule `*.txt`, line 85) and
  `controller/config.json` (rule `*.json`, line 86) would be ignored, so both
  need `!` rules. New `scripts/dbbench_pipeline/*.py` stages are already
  re-included (line 70). Check `git status` after adding each file.
- **Fingerprint regex.** The fingerprint string in `03` and the regex in `fingerprint.py`
  must change in lockstep. New segments are emitted conditionally so existing
  runs still pool.
- **graphify** skips `lib/rocksdb/db/`, so search it directly.
- **Stale submodule tree.** Verify fork behaviour at the recorded commit, not
  in the working tree.

## 9. Open items this plan does not settle

- The hot-path cost of WP3's counters (two flags in `GetContext`, and up to
  three relaxed atomic adds per probe and one per seek) is judged by ACT-4.
  Settled 2026-09-30: the filter outcome reaches `Version::Get` through
  `GetContext`, and the seek level through `SetReadCounterLevel` (WP3).
- The transition log format (JSON lines) may prove too slow at L1's cadence.
  If it does, switch to a binary format; the fields stay the same.
- The Gate N0 per-level counters currently serve only controller arms and
  OBJ-4. Whether native arms also need them depends on whether Gate N1's
  statistics use read counts. As specified today, they don't.
