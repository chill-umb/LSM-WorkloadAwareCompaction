# Patch notes

Companion to `docs/ROCKSDB_PATCH_ARCHITECTURE.md` (v2.1, "the architecture doc"). It records what milestone M0 found in the pinned RocksDB version and every deviation from the architecture doc. The architecture doc's RocksDB names were checked against RocksDB `main` (September 2026); this file is the record for the version we actually build.

## 1. Pinned version

| Item | Value |
|---|---|
| RocksDB version | 11.1.1 |
| Stock commit | `6cdeb9d9d0630763327f512e6255cab33f6834e7` ("Update HISTORY.md and version for 11.1.1", 2026-04-10) |
| Fork and branch | `chill-umb/rocksdb`, branch `user-facing-slo-rocksdb`, created from the stock commit |
| Root repo branch | `user-facing-slo`, created from `main` (`c25851b`), whose submodule pointer is the same stock commit |

The stock commit is the "unpatched build" of invariant I1 and arm A of the A/B harness (§16.2).

## 2. Earlier RL code (M0: "plan its removal or isolation")

The earlier controller (per-level DQN trigger, `kCompactionStyleRL`, `compaction_picker_rl.*`, `rlsuspend`/`rlresume` in `db_bench`) exists only on older branches: in the fork from `4398ed337` (2026-06-04) onward, for example `wt-pathways-consistency`, and in the root repo on `dqn-poc-new`. The fork's commit `7ea2d73` already contains it and is not a stock baseline.

**Plan: isolation by branching.** Both new branches start from stock, so none of that code is present and nothing needs removing. No picker file is touched (architecture §0, rule 4).

## 3. Deviations from the architecture doc

| ID | Architecture doc | This project | Reason |
|---|---|---|---|
| P1 | Python learner and tools under `tools/rl_controller/` (§11, §16) | Root repo `rl_controller/`: `learner/`, `knee_sweep.py`, `ab_harness.py`, `stub_learner.py`. The C++ controller library stays in `lib/rocksdb/tools/rl_controller/` | The RocksDB fork holds C++ only (owner decision, 2026-09-27) |
| P2 | `knee_sweep.py` is milestone M9 (§17) | Written in M4, with a **trial knee sweep** (full grid, paper mode, short runs) on the node right after M4 | Bugs are found in trials, never in the long sweep (owner decision, 2026-09-27) |
| P3 | I-1 needs a knee table in `learn` mode (§13.2), but the real table exists only after M9 | I-1 (M4, M6, M7) and I-3 (M6) each run twice: with a hand-written **fixture knee table** (placeholder R/W/S; geometry and I/O mode matching the test run), and with the trial knee table from P2 | Dual verification; the fixture is a test input, never used for a result (owner decision, 2026-09-27) |
| P4 | — | Every long node run is preceded by a short trial with the same script, flags and mode, e.g. a trial A/B before the real A/B | Owner decision, 2026-09-27 |
| P5 | M9 = knee sweep and A/B harness, order unstated (§17) | M9 order: trial A/B → real A/B and I-4 → real knee sweep → `--rl_mu0` calibrated from the knee sweep's telemetry (`max_flushes_during_compaction`) | Prove equivalence to stock before spending the long sweep; μ₀ needs no extra runs |
| P6 | Tests pass "in a debug build" (§0 rule 7) | Python tests (P1–P7 and the extra checks in P7) run locally and must pass before any node run; C++ tests (T, C, I) are built and run on the node | This machine does not build RocksDB (CLAUDE.md) |
| P7 | Test list of §15 | Adds local `pytest` checks for the knee-picking maths in `knee_sweep.py` and the equivalence statistics in `ab_harness.py` | Both feed long node runs (P4) |
| P8 | Supervisor field in `AdvancedColumnFamilyOptions` (§6.2) | **Pending owner decision.** Proposed (W3, §4.6): in `ColumnFamilyOptions` after `sst_partitioner_factory`, mirroring `compaction_thread_limiter` | No `AdvancedColumnFamilyOptions(const Options&)` edit, and its settable-test exclusion is appended last. The doc's placement also works (§4.6) |

## 4. M0 checklist (architecture §18) against 11.1.1

Sources: Stage 0 worker reports W1–W5 and verifier reports V1–V5 (2026-09-27). Where a verifier corrected a worker, the correction is used. Paths are relative to `lib/rocksdb/`; `DBB` is `tools/db_bench_tool.cc`. Nothing in RocksDB was built or run. V1 and V3 ran syntax-only checks; V3 also compiled and ran a standalone check of `std::to_string`/`std::stod`, and V5 ran its arithmetic offline in Python. A later verifier (V7) checked this section and §5–§7; its corrections are applied.

Status words: **confirmed** (the doc is right on 11.1.1), **partly** (the core is right, a detail differs), **differs** (the doc is wrong here). Terms: a *Version* is RocksDB's list of SST files per level; a *SuperVersion* bundles the current Version, memtables and options for reads and writes; a *compensated* size is a file size RocksDB inflates for files full of deletes. With no deletes (G4) it equals the real size (`db/version_set.cc:3558-3591`).

### 4.1 Item 1 — `ComputeCompactionScore`
- **Status:** confirmed.
- **Evidence:** static L0 = max(n/k, L0 bytes / `max_bytes_for_level_base`) (`db/version_set.cc:3860-3862, 3901-3905`), where n counts only L0 files not being compacted (3797-3805); levels ≥ 1 = compensated bytes of non-compacting files / `MaxBytesForLevel` (3918-3920); `EstimateCompactionBytesNeeded` is the last statement (3984) and overwrites its result on every path, so an extra early call is harmless.
- **Notes:** no ×10 scale on static paths (only dynamic, 3898-3900). The loop stops at level `num_levels−2` (3783), so the last level is never scored. The L0 branch is shared with universal compaction.
- **Adaptation:** substitute k_eff only when `compaction_style_ == kCompactionStyleLevel && !level_compaction_dynamic_level_bytes`; scale by m only at 3918-3920. Reword §5 "Left native": those readers use the *option* k, which the controller itself sets through `SetOptions`; none of them reads the supervisor's k_eff or any mᵢ, although their values move with the level sizes that mᵢ produces (§6.8).

### 4.2 Item 2 — `SetOptions`
- **Status:** confirmed.
- **Evidence:** every call, even with unchanged values, appends a Version with no MANIFEST write (`db/db_impl/db_impl.cc:1328, 1341`), rescores it in `AppendVersion` (`version_set.cc:5797-5801`), installs a SuperVersion and schedules due compactions (`db_impl.cc:1360` → `db_impl_compaction_flush.cc:4919, 4943-4944`), and writes an OPTIONS file (`db_impl.cc:1362-1363`).
- **Notes:** a job starts only if the single compaction slot is free (`db_impl_compaction_flush.cc:3103-3114`). The OPTIONS work (file write and fsync, read-back verify, rename, directory fsync, delete oldest: two fsyncs in all) runs with the DB mutex *released* (`db_impl.cc:5528-5561`; V3 corrected W3). Each call takes the DB mutex three times (`GetDBOptions` 4673, the main block, `RenameTempFileToOptionsFile` 5653), and the client's next read takes it once more, ticking `NUMBER_SUPERVERSION_ACQUIRES` (`column_family.cc:1386-1390`); this is part of the C − A overhead. A failed OPTIONS write returns an error although the new options are already live (1417-1420). Each call re-runs the write-stall logic (`db/column_family.cc:1430-1437`; §6.1). Parse and validate are all-or-nothing, so the two-key call is atomic. `SanitizeOptions` is not called (`column_family.cc:1665`), so k = 0, negative k and k > slowdown are accepted.
- **Adaptation:** none in the core; no recompute call is needed. Controller-side handling is in §5.

### 4.3 Item 3 — `SetFinalized()` target assertion
- **Status:** confirmed.
- **Evidence:** `version_set.cc:4250-4282`: debug builds only, leveled only, levels `base_level()`…`num_levels−2`, empty levels skipped. It runs on every `AppendVersion` (5798-5804).
- **Adaptation:** none; m never touches targets (D2, I3).

### 4.4 Item 4 — `GetBGJobLimits` with `max_background_jobs=2`
- **Status:** confirmed, on a configuration condition.
- **Evidence:** `db_impl_compaction_flush.cc:3117-3146` gives 1 flush slot and 1 compaction slot when `max_background_compactions` and `max_background_flushes` are −1, and speed-up cannot raise it past 1. Thread pools come out LOW = 1, HIGH = 1, BOTTOM = 0 (`db/db_impl/db_impl_open.cc:78-84`). `max_subcompactions=1` forms no subcompactions (`db/compaction/compaction.cc:932-934`); `kRoundRobin` cannot add a thread with one slot either (`compaction_job.cc:461-471`; V2 corrected W2).
- **Adaptation:** check the computed limit, not the knobs (§5). `max_background_jobs=3` would give 2 slots under speed-up.

### 4.5 Item 5 — registration, `Vector<double>`, separator
- **Status:** partly. Registration, `Vector<double>` support and the separator are as the doc says; its step list is incomplete.
- **Evidence:** registered exactly as §4.2 says (`options/cf_options.cc:525-530`). `Vector<T>` is a generic template (`include/rocksdb/utilities/options_type.h:415-443`); `kDouble` elements parse with `std::stod`, print with `std::to_string` (6 decimals) and compare within 1e-5 (`options/options_helper.cc:611-613, 688-690, 1302-1304`). No option in the tree uses `Vector<double>`; a syntax-only instantiation passed (V3; not linked). The separator is `':'` (`options_type.h:420`). An empty token is rejected; an empty vector is not written to the OPTIONS file.
- **Differs:** the doc omits `AdvancedColumnFamilyOptions(const Options&)`, which copies fields one by one (`options/options.cc:42-126`). Step 1's `MutableCFOptions::Dump` cannot be mirrored literally, because the int vector prints with `%d` into `char buf[10]` (`cf_options.cc:1216-1229`).
- **Notes:** the OPTIONS file stores 6 decimals, so on a reload path `1.0000004` becomes exactly 1.0 and flips the `m == 1.0` branch (V3 risk 7). This is moot while `--options_file` is forbidden (§5).
- **Adaptation:** §5, M1.

### 4.6 Item 6 — non-serialized `shared_ptr` option
- **Status:** differs.
- **Evidence:** both doc examples are `Customizable` and serialized by name (`cf_options.cc:871-877, 900-903`). The matching pattern (plain class, no type-map entry, never serialized, set in code before Open) is `compaction_thread_limiter`: field `include/rocksdb/options.h:340`, constructor copy `cf_options.cc:1062`, copy-back `options_helper.cc:347`, excluded at `options/options_settable_test.cc:554-555`. Nothing reaches `ImmutableCFOptions` by itself: its constructor lists members (`cf_options.cc:1034-1069`). All 13 non-test `ComputeCompactionScore` call sites pass `cfd->ioptions()`, a picker's reference to it, or `Compaction::immutable_options()`, so the pointer reaches every scoring call: `version_set.cc:5798`; `db_impl.cc:5134, 7014`; `db_impl_experimental.cc:48`; `compaction_picker.cc:754, 952`; `compaction_picker_level.cc:585`; `compaction_picker_universal.cc:834`; `db_impl_compaction_flush.cc:1658, 4027, 4593, 4701, 5073` (V1 C2, V3 M8).
- **Adaptation:** mirror `compaction_thread_limiter`; that is the pattern this project uses. Where the field is declared is open (§3 P8): W3 proposes `ColumnFamilyOptions` after `sst_partitioner_factory`; the doc's `AdvancedColumnFamilyOptions` (§6.2) also works. A `SetOptions` naming the key returns InvalidArgument. The pointer survives `SetOptions`, because `ioptions_` is `const` (`db/column_family.h:643`).

### 4.7 Item 7 — validation run by Open and `SetOptions`
- **Status:** confirmed.
- **Evidence:** `ColumnFamilyData::ValidateOptions` (`column_family.cc:1487-1648`). Open reaches it via `db_impl_open.cc:2420` → 218; `SetOptions` via `db_impl.cc:1333-1334` → `column_family.cc:1662-1667`. It also runs in `CreateColumnFamily` and `SetDBOptions`, and has no check on the int vector or the dynamic flag today.
- **Notes:** Open validates the user's *unsanitized* options, `SetOptions` sanitized ones. Sanitizing only raises `num_levels` and only clears the dynamic flag (`column_family.cc:274-286, 386-401`), so Open is the stricter side. Read-only, secondary and follower opens skip validation (not used by db_bench).
- **Adaptation:** append the §4.3 checks before the final `return s;` (there is no early `return OK`).

### 4.8 Item 8 — `PerfContextByLevel`
- **(a) Fields — confirmed.** `include/rocksdb/perf_context.h:33-53` has the six fields plus `get_from_table_nanos`. Map entries appear on first use and are never removed (`monitoring/perf_context_imp.h:91-97`), so a missing level means 0.
- **(b) Point lookups only — differs.** Bloom counters and `user_key_return_count` are Get/MultiGet only (`table/block_based/block_based_table_reader.cc:2333-2399, 2644`; `version_set.cc:2823`). `block_cache_hit_count` and `block_cache_miss_count` count *every* block-cache lookup on the thread (`block_based_table_reader.cc:273, 335`), including each data block an iterator's `Seek`/`Next` enters (`block_based_table_iterator.cc:466-470`). With `cache_index_and_filter_blocks=false`, index and filter blocks never reach these counters. Bloom and cache counters use the level a table was opened at (`block_based_table_reader.h:676-678`); `user_key_return_count` uses the current level (§6.6).
- **(c) db_bench enables it — partly.** `ThreadBody` calls `EnablePerLevelPerfContext()` unconditionally (`DBB:4120`); counting happens only with `--perf_level ≥ 2` (`perf_context_imp.h:87-90`). Each benchmark runs in a new thread, so mixgraph's map starts empty, and main-thread benchmarks such as `rlresume` have no map.
- **Adaptation:** fix the §8.1 wording; decide §6.3 (W4, V4 and V5 all recommend excluding Seeks from the per-level counts); snapshot only on the mixgraph thread (§5).

### 4.9 Item 9 — tickers and `kCFStats`
- **Tickers — confirmed, with these meanings.** All are cumulative since open, and `resetstats` does not reset them (`db_impl.cc:4792-4800`).
  - `NUMBER_KEYS_READ` +1 per Get (`db_impl.cc:2699`); `MEMTABLE_HIT` Get-only; `NUMBER_DB_SEEK` +1 per Seek (`db/db_iter.cc:1719`).
  - `BYTES_WRITTEN` is the serialized WriteBatch: key + value + 16 B per Put at 64/960 B (`db/write_batch.cc:862-870`), +15 B when a value is under 128 B.
  - `FLUSH_WRITE_BYTES` is device bytes (padding and MANIFEST included), ticked twice per flush: the SST before install (`db/flush_job.cc:1141`), the MANIFEST remainder after (338).
  - `COMPACT_WRITE_BYTES` ticks during a compaction in ~1 MiB chunks (`compaction_job.cc:2365`); trivial moves (a file relinked to the next level without rewriting) tick nothing.
  - `STALL_MICROS` covers slowdowns and stops, recorded when the stall ends, on the client thread (`db_impl_write.cc:2282`).
  - `NO_FILE_OPENS` counts every table-reader open on any thread (`db/table_cache.cc:113, 124`); `BLOCK_CACHE_DATA_{MISS,HIT}` include compaction lookups: telemetry only.
  - WA's numerator never includes the MANIFEST bytes of compactions or trivial moves, while `FLUSH_WRITE_BYTES` includes the flush's (V4 #45). WAL bytes are outside WA; `WAL_FILE_BYTES` exists if device-level WA is ever wanted.
- **`kCFStats` — differs (names and units).** Keys are `compaction.L<j>.RnGB`, `Rnp1GB`, `MovedGB`, `WriteGB`, `KeyIn`, `KeyDrop` (`db/internal_stats.cc:1767-1786`); bytes are GiB printed as `%f`, about 1 KiB resolution, harmless because window sums telescope. Row j is the *output* level (`compaction_job.cc:1154-1156`). The L0 row mixes flushes and intra-L0 compactions, and flushes never set `KeyDrop`. A level's keys exist only while it has compaction time or files (`internal_stats.cc:1826-1827`), so a key can vanish. Values land when a compaction installs; `ResetStats` and any DB reopen clear them.
- **Adaptation:** §5, M4 measurement.

### 4.10 Item 10 — properties
- **Status:** partly.
- **Evidence:** `estimate-pending-compaction-bytes` is the current Version's estimate (`internal_stats.cc:1450-1456`), but its value moves with the option k and with m (§6.8). `total-sst-files-size` sums files of *every* live Version, including a running compaction's inputs (`version_set.cc:7902-7918`); `live-sst-files-size` is the current Version only. `estimate-live-data-size` is confirmed. `num-files-at-level0` is a string property only: `GetIntProperty` returns false (`internal_stats.cc:459-461`).
- **Adaptation:** read `num-files-at-level<N>` as a string; SA's numerator is §6.10.

### 4.11 Item 11 — picker order
- **Status:** partly.
- **Evidence:** scores are sorted in `ComputeCompactionScore` (`version_set.cc:3956-3969`); a compaction is scheduled only if some score ≥ 1 (`db/compaction/compaction_picker_level.cc:39-43`); the pick loop walks scores in descending order and stops below 1 (204-257). But every L0 pick tries `PickSizeBasedIntraL0Compaction` (822, 931-983) before the normal L0→L1 pick. It reads the option k and can choose L0→L0. Only the L0 trivial-move check (819) runs earlier, and it needs L0 files that do not overlap L1. With one slot, size-based intra-L0 is the only live L0→L0 path; the "L0 compaction running" branch and the base-level skip cannot occur (V1 C1).
- **Adaptation:** picker none. Reword I2: "in decreasing score order, the first level with score ≥ 1 for which the stock picker can form a compaction, including the stock L0 special cases" (§6.5).

### 4.12 Item 12 — `EventListener` callbacks
- **Status:** partly.
- **Evidence:** `OnFlushCompleted`, `OnCompactionBegin`, `OnCompactionCompleted`, `OnStallConditionsChanged` (`include/rocksdb/listener.h:634, 675, 690, 809`), all called without the DB mutex. Trivial moves fire Begin and Completed with zero bytes. `input_file_infos[i].level` is an input index (0 = start level), not an LSM level (`db_impl_compaction_flush.cc:4847-4850`).
- **Notes:** missing for §8.2: start/end timestamps (only `elapsed_micros`), duration in operations, flushes during a compaction, and the stall *cause* (`WriteStallInfo` holds only the old and new condition, `listener.h:207-215`). The stall callback runs on whichever thread installed the SuperVersion, including the controller inside its own `SetOptions` (`db_impl.cc:1378`).
- **Adaptation:** §5, `RlListener`.

### 4.13 Item 13 — mixgraph operation count and type draw
- **Status:** count confirmed; type draw differs.
- **Evidence:** the count is `reads_` = `--reads`, or `--num` if negative (`DBB:3599`); the loop runs exactly that many ops per thread (7202-7203, 2815-2833) when `--duration=0`. `--duration>0` overrides it; `--mix_accesses` is unused. The type is `query.GetType(rand_v)` (7222), with thresholds from `QueryDecider::Initiate` (6975-6987). With uniform keys `rand_v` is the key id, so the key fixes the type (§6.2). `Initiate` only appends (6984-6985) and cannot be re-run for a new mix.
- **Hooks:** beside the three `FinishedOps` calls (`DBB:7279` Get, 7304 Put, 7332 Seek). The Seek one runs after the whole `Next()` scan.
- **Adaptation:** §5, M4 db_bench.

### 4.14 Item 14 — I/O-mode flags
- **Status:** partly.
- **Evidence:** the direct-I/O flags land in the DB options (`DBB:4461-4463`) and read back with `GetDBOptions()`. `--disable_wal` and `--sync` live in `Benchmark::write_options_` (`DBB:3611-3614`), in no `Options` struct. The WAL is never written with direct I/O (`env/fs_posix.cc:923-927`).
- **Adaptation:** read direct I/O from `GetDBOptions()` and WAL/sync from `write_options_`. Telemetry describes paper mode as "direct SST I/O, buffered WAL, sync off".

## 5. Implementation requirements found in M0

Things later code must do that the architecture doc does not say. Each line cites the source that forces it.

**M1 — option `level_target_multipliers`**
- [ ] Copy the field in `UpdateColumnFamilyOptions` (pattern `options_helper.cc:272-275`) **and** in `MutableCFOptions(const ColumnFamilyOptions&)` (pattern `cf_options.h:143-144`); without either, `SetOptions` returns OK and the value never goes live (`column_family.cc:1653-1671`).
- [ ] Copy it in `AdvancedColumnFamilyOptions(const Options&)` (`options.cc:70-71`) without the int vector's padding (122-125): empty must mean all 1.0.
- [ ] Register with `OptionTypeFlags::kMutable`, or `SetOptions` refuses it (`column_family.cc:1656`).
- [ ] `LevelTargetMultiplier(level)` returns 1.0 for `level < 0` as well; the int accessor has no such guard (`cf_options.h:260-266`).
- [ ] `MutableCFOptions::Dump` prints with `%g` into a buffer larger than `char buf[10]` (`cf_options.cc:1216-1229`).
- [ ] Validate at the end of `ColumnFamilyData::ValidateOptions` with `std::isfinite` on every entry, entry 0 included; `std::stod` accepts `inf` and `nan` (`util/string_util.cc:398-403`).
- [ ] Empty and all-1.0 vectors pass for every style and dynamic setting (`db_options_test` sends every mutable option, `db/db_options_test.cc:66-77`); never randomize non-1.0 values in `RandomInitCFOptions` (`test_util/testutil.cc:394-398`).
- [ ] Declare the field right after the int vector (`advanced_options.h:682`, `cf_options.h:319`) and add both `options_settable_test` exclusion entries in field-offset order (`options_settable_test.cc:522-524, 742-744`); out of order, its `memset` overruns (46-61). The test runs only in a GCC debug build.
- [ ] An M1 read-back test: `SetOptions` with a non-default vector, then with k alone, then `GetOptions()` (pattern `options/options_test.cc:98, 244-247`); no upstream test catches a missing copy (V3 M3).
- [ ] Run `-fsyntax-only` on `options/cf_options.cc` early: this is the tree's first `Vector<double>`.
- [ ] Add no k checks to `ColumnFamilyData::ValidateOptions`. Open validates unsanitized options (§4.7), and stock accepts k = 0 and k > slowdown there because `SanitizeOptions` repairs them afterwards (`column_family.cc:323-350`). Upstream tests rely on this (`db/db_compaction_test.cc:1434` k = 100, `db/db_test.cc:5065` k = 1024, `db/db_universal_compaction_test.cc:108` k = 0), and `db_compaction_test` is part of M2's done criterion. The controller range-checks k instead (M4).

**M2 — scoring**
- [ ] Guard the k_eff substitution to leveled static mode; the L0 branch is shared with universal (`version_set.cc:3856-3862`).
- [ ] Keep the exact `m == 1.0` test, so empty and all-1.0 vectors score identically (I1).
- [ ] m for the last level is inert (`version_set.cc:3783`); telemetry must not report an effect for it, or for levels deeper than L_d.

**M3 — supervisor**
- [ ] No `dynamic_cast` or `typeid` anywhere in `tools/rl_controller/`: RocksDB's `USE_RTTI=AUTO` builds the release tree with `-fno-rtti` and the debug tree with RTTI, so such code would pass its debug tests and fail to compile in the release `db_bench` (B.1 build report, open question 10).
- [ ] Mirror `compaction_thread_limiter`: `ImmutableCFOptions` member and constructor copy (`cf_options.cc:1034-1069`), copy-back (`options_helper.cc:347`), no type-map entry. Placement per §3 P8: either `AdvancedColumnFamilyOptions` (doc §6.2; then also copy it in `AdvancedColumnFamilyOptions(const Options&)` and insert its exclusion at the matching offset), or after `sst_partitioner_factory` (`options.h:349`; exclusion appended last, `options_settable_test.cc:556-557`).
- [ ] Sum per-level bytes in a separate pre-pass into stack arrays, only on the supervisor branch; the stock loop sums inside scoring (`version_set.cc:3910-3920`).
- [ ] Fill `m_setting` into a local `double[kSupervisorMaxLevels]` via `LevelTargetMultiplier(j)`, never `vector.data()` (an empty vector gives nullptr).
- [ ] (W1; which count S1 uses is open, §6.9) `ScoreInputs` carries both L0 counts, all files (`l0_delay_trigger_count`, `version_set.cc:5087`) and non-compacting files (3797-3805), plus a new `level_bytes_being_compacted` sum with its byte unit documented.
- [ ] After `Adjust`: clamp k_eff ≥ 1, require every m_eff finite and ≥ 1e-3, skip S3 when `bytes_nc == 0`; a NaN score silently fails `>= 1` in release (`compaction_picker_level.cc:40, 211`).
- [ ] Return InvalidArgument at Open when `num_levels > kSupervisorMaxLevels`; an `assert` alone overflows `m_eff[]` in release.
- [ ] `Adjust` handles all-zero inputs (new DB, `version_set.cc:7878-7886`) and `op_now == 0` during Open recovery, before the controller thread exists (`db/version_edit_handler.cc:538, 812`).
- [ ] Op-clock lifetime: `Adjust` also runs during DB close (the final flush at `db_impl.cc:489-491`, which runs only with unpersisted data, i.e. with the WAL off; in-flight compactions in every mode), so the supervisor shares ownership of the clock, or the DB is deleted before the controller and the clock.
- [ ] Treat `soft_pending_limit == 0` as "S2 off"; it happens when both pending limits are 0 (`column_family.cc:359-367`).
- [ ] Count or document the periodic rescore: db_bench's default TTL (sanitized to 30 days, `column_family.cc:416-424`) makes the periodic trigger task rescore, and that task runs every 600 s, the `stats_dump_period_sec`/`stats_persist_period_sec` default (`db_impl.cc:7010-7014, 824-859`).
- [ ] Make a null supervisor visible: print it in `ColumnFamilyOptions::Dump`, or check `adjust_calls > 0` at the first `MissionEnd`.
- [ ] Optional: clamp against the supervisor's own atomics, not `in.k_setting`/`in.m_setting`; the rescore after a failed compaction may carry pre-`SetOptions` options (`db_impl_compaction_flush.cc:4593`). Once `RlListener` is installed, `NotifyOnCompactionBegin` releases the DB mutex around callbacks (1813), which slightly widens that window; the supervisor still always runs under the mutex.

**M4 — db_bench wiring**
- [ ] `rlresume`/`rlsuspend` as main-thread branches before `DBB:3922`, like `waitforcompaction` (3838-3839); a worker-thread benchmark would shift mixgraph's seed (`seed_base + total_thread_count_`, 2800).
- [ ] Recorder hooks beside `FinishedOps` (`DBB:7279, 7304, 7332`), with the recorder's own op counter (`Duration::ops_` is private, 2839).
- [ ] If §6.3 option 1 is chosen: `DisablePerLevelPerfContext()` before `NewIterator` (`DBB:7309`), `EnablePerLevelPerfContext()` after `delete` (7330) and before `OnOp`, identically in every arm and in the knee sweep.
- [ ] `--mix_schedule`: a fresh `QueryDecider` at each threshold (`DBB:6984-6985`), switched just before 7222; check `range_ == 1000` for each mix; clamp u ≥ 1/N or forbid a get share of 0, since u = 0 gives `0·log 0` in the value and scan formulas; pass exactly 3 ratios, since mixgraph runs only types 0–2 and a 4th type would be counted by `Duration` but run nothing, with no `FinishedOps` (V5 M3).
- [ ] `--level_target_multipliers` parses doubles separated by `':'`, not with db_bench's comma `std::stoi` parser (`DBB:9242-9251` turns "1.5" into 1).
- [ ] Install the supervisor, op clock and `RlListener` once, with a guard: `Open` runs twice (`DBB:3591, 3947`) and appends `listener_` each time (5103).
- [ ] Read direct I/O from `GetDBOptions()` and WAL/sync from `write_options_` (`DBB:3611-3614`); echo `write_options_` in telemetry.
- [ ] The actor formats values with `%.17g`, so `SetOptions` reproduces the exact double.

**M4 — controller and `SetOptions`**
- [ ] After a non-OK `SetOptions`, read `GetOptions()`: an IOError from OPTIONS persistence means applied (`db_impl.cc:5556, 1417-1420`); InvalidArgument means nothing changed.
- [ ] Validate every k and m before sending; `SetOptions` accepts k = 0, negative k and k > slowdown. This is where the k range check lives (not in the core validator, M1).
- [ ] After a supervisor mode flip or a bounds push with no `SetOptions` behind it (`rlresume`, `rlsuspend`), call `SetOptions`: the supervisor only acts at a rescore.
- [ ] Join the controller thread before the DB is closed or deleted; `CloseHelper` waits for no in-flight `SetOptions` (`db_impl.cc:526`).
- [ ] Log `rocksdb.actual-delayed-write-rate` and `rocksdb.is-write-stopped` before and after each `SetOptions` (`internal_stats.cc:1488-1503`).
- [ ] Watch `rocksdb.background-errors`: `SetOptions` keeps succeeding while a background error stops all scheduling (`db_impl_compaction_flush.cc:3030-3041`), and a skip-MANIFEST `SetOptions` clears `VersionSet::io_status_` and the quarantine list left by an earlier failed MANIFEST write (`version_set.cc:6271-6283`).

**M4 — measurement and telemetry**
- [ ] Per-level perf snapshot only on the mixgraph thread, from the raw `level_to_perf_context` pointer (`ToString`/`Reset` skip the map while disabled, `perf_context.cc:260, 288`); the first mission diffs against zero, never against an `rlresume` baseline. Iterate over all map keys and log any that is not a real level (`UINT32_MAX` = a reader opened with level −1, `db/table_cache.h:121, 160, 196, 277`).
- [ ] Ticker and `kCFStats` baselines at `rlresume`; a negative delta means a reset, and that mission is excluded.
- [ ] Parse `kCFStats` values as `double` (`strtod`) × 2³⁰; carry a vanished key's last value forward, never read it as 0 (V4 #62); never use drop_0.
- [ ] Read `num-files-at-level<N>` with `GetProperty` and parse the string.
- [ ] If §6.7 option 1 is chosen: mem = (found Gets − Σⱼ `user_key_return_count`ⱼ) / Gets and U = Σ serialized Put sizes, both on the client thread (found = `s.ok()`, `DBB:7272`), with `NUMBER_KEYS_READ` − Gets logged as a lag monitor.
- [ ] Use window sums only for `COMPACT_WRITE_BYTES`, flush bytes and `kCFStats` flows; they tick on different clocks. Cross-check `kCFStats` `Write` against the listener's exact `CompactionJobInfo.stats.total_output_bytes` (window sums only).
- [ ] Never use `Stalls(count)` or `cf-write-stall-stats` as a stall metric (each controller call while delayed adds one); use `STALL_MICROS`.
- [ ] Label as scaled the scores in the LOG, `compaction-pending` and the event log's `"score"` (`internal_stats.cc:1279, 1803-1804`; `compaction_job.cc:2795`).
- [ ] Log the SST file count per mission (the table cache holds at most 990 readers, §6.11).

**M4 — `RlListener`**
- [ ] Stamp its own wall clock and op clock at Begin and Completed, paired by `job_id`; with one slot, one in-flight record plus a trivial-move flag suffices.
- [ ] Count compactions per input level from `base_input_level` (`db_impl_compaction_flush.cc:4829`), never from `input_file_infos[i].level`.
- [ ] Count trivial moves separately (`num_input_files_trivially_moved > 0`).
- [ ] Flushes during a compaction: snapshot an atomic flush counter at Begin, diff at Completed.
- [ ] Stall cause from `kCFStats` cause × condition count deltas (`db/write_stall_stats.cc`); these count stall-logic runs spent in a state, not transitions. They also rise by one for every controller `SetOptions` made while delayed or stopped (§6.1). These are the counters the measurement list forbids as a stall metric, so use them only to name the cause of a transition reported by `OnStallConditionsChanged`, never as counts.
- [ ] Never block on a lock the controller holds around `SetOptions`: the stall callback can run inside that call.
- [ ] Log L0→L0 compactions (start level 0, output level 0) for §6.5.

**Startup checks (additions to §13.2, when `rl_mode != off`)**
- [ ] `k_min ≥ 1`.
- [ ] Exactly one column family; db_bench copies the supervisor pointer into every column family (`DBB:5173`).
- [ ] No `--options_file`, which drops pre-installed objects and flag defaults (`DBB:4394-4395`); read effective options after Open, never `FLAGS_*` (the library default is `dynamic=true`, `advanced_options.h:665`).
- [ ] Computed `GetBGJobLimits(f, c, j, true).max_compactions == 1` from `GetDBOptions()` (`db_impl_compaction_flush.cc:3117-3146`).
- [ ] `compaction_pri == kMinOverlappingRatio` (G1; not needed for the one-slot rule).
- [ ] `--duration=0`, and `--rl_run_ops` is 0 or equals `reads_` (`DBB:3599, 2821-2828`).
- [ ] Statistics present with a stats level above `kExceptTickers`; `--perf_level=2`; built with `WITH_PERF_CONTEXT` and `WITH_IOSTATS_CONTEXT` on (`CMakeLists.txt:360-368`), or the counters read zero without error.
- [ ] A block cache exists: `cache_size > 0` (`DBB:3234-3236`).
- [ ] Effective `max_open_files == 1000`, read from `GetDBOptions()`: `SanitizeOptions` clips it to the soft `ulimit -n` (`db/db_impl/db_impl_open.cc:53-58`).
- [ ] `--use_existing_db=false` (if true, `filluniquerandom` is skipped with only a stdout note); log the mean mixgraph Put value (`total_val_size/puts` from mixgraph's message line) to catch missing `--value_*` flags (stock ≈36 B).
- [ ] `cache_index_and_filter_blocks=false`, no partitioned index or filters (`include/rocksdb/table.h:174-175`), `compression_max_dict_bytes=0`.
- [ ] A filter exists: `bloom_bits > 0` (−1, the default, means none, `DBB:5048-5058`), `optimize_filters_for_hits=false` (`version_set.cc:2780-2781`), and no prefix extractor with `whole_key_filtering=false`.
- [ ] `RlListener` appears exactly once in `options.listeners`.
- [ ] `--db` set explicitly (the default is under the test directory, `DBB:9326-9330`) and `--seed` non-zero (0 is clock-based, `DBB:9297-9304`).
- [ ] The knee-table fingerprint also pins the key- and value-size flags (`BYTES_WRITTEN` framing is +16 or +15 B per Put).

## 6. Open design questions for the owner

Findings that change the design or the measurement. None is decided here.

### 6.1 `SetOptions` every mission cuts the delayed write rate (arms A and B vs arm C). Needed by M4 and the A/B harness
- **Found.** Each `SetOptions` installs a new SuperVersion, which re-runs the write-stall logic (`column_family.cc:1430-1437`; RocksDB's comment warns it "treats it as further slowing down is needed"). With 20–33 L0 files at the call, the delayed write rate is multiplied by 0.8 (0.6 at 34–35 files), whatever k and m are sent. The cut persists and is only undone by later recoveries: ×1.4 on leaving the delay, ×1.25 when debt falls (`column_family.cc:852-913, 1186-1195`; V2 §2). Each such call also adds 1 to a stall counter. Arms A (stock build) and B (patched, `off`) never call `SetOptions`; arm C (`frozen_native`) calls it every mission, about 142 times per run (7.1M ops ÷ 50,000, warm-up missions included), plus one at `rlresume` (§5 requires a `SetOptions` call after a mode flip). Each call also writes an OPTIONS file with two fsyncs on the measured device and about 70 LOG lines. Those fsyncs may force a filesystem journal commit that also writes back other dirty data, such as the buffered WAL (sync off); that depends on the filesystem and must be measured in the trial A/B (V2 M3). Unchanged-value calls do not change compaction choices in this workload (the TTL boost starts at ttl/2, `db/compaction/file_pri.h:54`; V2 M11), so C − A is overhead plus these rate cuts.
- **Size (arithmetic, not measured).** With a 2 MiB memtable and about 7.8 MiB of user writes per mission (feasibility §2), stock RocksDB already re-runs the stall logic at about 4 memtable switches, about 4 flush installs and every compaction install per mission (`column_family.cc:1430-1432`). One extra call per mission adds roughly one cut per eight or more stock ones (order 10%), not the doubling V2 estimated for a 64 MiB memtable. It happens only while n₀ ≥ 20.
- **Why it matters.** §16.2 books C − A as instrumentation overhead, but this part is a behaviour change that lands on stall time and throughput, which O1 constrains.
- **Options.** (1) Skip the call when nothing changed, or defer it while `actual-delayed-write-rate > 0` or `is-write-stopped = 1`; this conflicts with §10.9 "every mission". (2) Arm B issues the same unchanged-value calls, and the behaviour change is read from C − B. Arm A cannot: stock `db_bench` never calls `SetOptions` periodically, so giving A these calls would make it a patched build, against §1 and arch §16.2. (3) Accept, and name it as a known overhead component.
- **Recommendation (V2, adapted):** avoid the extra cuts or make the baseline pay them too, i.e. (1) or (2); V2 proposed the matching calls for arm A, which §6.13 shows is impossible, so (2) uses arm B, and log both delay properties around every call either way.

### 6.2 mixgraph fixes each key's operation type. Needed before any paper run and for `--mix_schedule`
- **Found.** One random number sets the key, the op type, the Put value size and the scan length (`DBB:7206-7222, 7283, 7317`). With uniform keys it *is* the key id, so the type is set by `id mod 1000`: remainders 0–805 are only ever read, 806–964 only ever written, 965–999 only ever start a Seek (V5 checked all 2.9M ids).
- **Consequences.** Gets never read a key rewritten in the measured phase, so they always find the load's version. Only 461,100 ids (15.9%) are ever rewritten, about 2.45 times each, with memoryless gaps (mean ≈450 MiB of user writes). Feasibility P3's power-law gap model and the Cor. 5.19 κ check therefore fail by construction (V5 M5; inferred from source and arithmetic, not run). A `--mix_schedule` switch moves the thresholds, so the set of written keys changes at every phase boundary.
- **Why it matters.** Read-side quantities (h_j, cache behaviour) and κ̂ reflect a generator artefact; the doc assumes independent per-op draws (§13.5).
- **Options.** (A) Keep stock mixgraph and say so in the paper. (B) Draw the type from an independent `thread->rand.Next()` in every arm, baseline included; this shifts the whole op sequence, so all arms must use one binary (V5 M3). (C) Another key model (§6.4).
- **Recommendation:** none beyond "a written decision, identical in every arm" (W5, V5).

### 6.3 Read cost RA would include Seek/Next scans. Needed by M4
- **Found.** The per-level `block_cache_miss_count` behind RA and q_j also counts every data block a Seek and its `Next()` loop enter (§4.8). At stock scan lengths V5 estimates about 5 block lookups per op from scans against 0.8–1 from Gets, so RA would mostly measure scans. Scan blocks served by readahead still count as misses without being device reads. Scans also fill the 8 MiB cache (`fill_cache=true`, `DBB:3617`); one p99 scan is about the whole cache.
- **Why it matters.** RA is the read metric of the SLO box and of the knee table, and q_j feeds the prior.
- **Options.** (1) Switch per-level counting off during each Seek (§5): RA becomes Get data-block misses (feasibility RA_d), and scans' cache pollution stays in as a real workload effect. (2) Swap `level_to_perf_context` to a second map during Seeks, giving a separate per-level scan account at the same O(1) cost. (3) Keep scans in RA.
- **Recommendation (W4, V4, V5):** (1), in every arm and in the knee sweep; (2) if scan I/O is wanted as its own metric.

### 6.4 Workload parameters still unknown. Needed before trial runs
- **Found.** Not fixed by feasibility §2 or by the source:
  - **Key distribution.** Stock is uniform. G4 says "parameterised from Cao et al." with no values, while Thm. 7.2(iii) and assumption row 9 assume uniform keys. The Cao power-law path may produce negative ids, i.e. keys outside the loaded range (`DBB:7219`; read, not run).
  - **Scan length** (`--iter_theta/k/sigma`, `--mix_max_scan_len`). At stock values the mean is **≈557 `Next()` calls per Seek** (median 27, p90 1,470, p99 8,306; 12% ≥ 1,000), about 19.5 entries scanned per op. Scan length falls with the key id, so 80% of scanned entries come from Seeks starting in the lowest 10% of ids (V5 M4).
  - **`--seed`** (0 is clock-based and changes load order, op stream and block-cache hash), **`--db`** (must be on the measured device), **`--cache_type`** (stock `hyper_clock_cache`; feasibility says only "8 MiB").
- **Why it matters.** At stock scan lengths scans probably dominate device reads (§6.3); the key model decides which levels Gets hit and the rewrite gaps (§6.2).
- **Options.** A fixed scan length L via `--iter_theta=L --iter_k=0 --iter_sigma=0 --mix_max_scan_len>L`, or stock; uniform or Cao keys; any fixed non-zero seed; LRU or HyperClock cache.
- **Recommendation:** decide each explicitly and pin it in the knee-table fingerprint.

### 6.5 L0→L0 compactions versus the prior's L0→L1 model. Needed by M6
- **Found.** Every L0 pick tries `PickSizeBasedIntraL0Compaction` (`compaction_picker_level.cc:822, 931-983`) before the normal L0→L1 pick; only the L0 trivial-move check (819) comes earlier. It merges L0 files into one L0 file instead of into L1 when n₀ ≥ max(2, option k) and L1 holds more than 2·max(10, T) × the L0 bytes: 20× for T ≤ 10, 28× and 40× at T = 14 and 20. With one slot, size-based intra-L0 is the only live L0→L0 path. m₁ > 1 lets L1 grow and makes it likelier.
- **In the paper geometry** (arithmetic from that formula and feasibility §2, flush files f₀ ≈ 2 MiB): at k = 2, L0 holds at least about 4 MiB at pick time (more if the slot was busy when L0 became due, which raises the bar). With T ≤ 10 the switch therefore fires once L1 > 80 MiB, i.e. 10×, 5× and 2.5× L1's native target at base 8, 16 and 32 MiB; in the T = 14 and T = 20 cells the bar is 112 and 160 MiB. At k = 4 and T ≤ 10 the bar is 160 MiB (20×, 10×, 5×). V1's "m₁ ≳ 20" assumed L0 at its byte cap. At base 32 MiB and small k, an L1 held at about 2.5× its target (a large multiplier, or L1 overshooting while the one slot is busy with L0) can switch L0 to L0→L0.
- **Related (V1).** k arms merged because they share k_eff = min(k, ⌈C₁/f₀⌉) are not behaviour-identical: the option k also sets this path's minimum file count.
- **Why it matters.** The physics prior (§10.4) assumes every L0 trigger produces an L0→L1 merge; an L0→L0 merge costs writes and reads differently.
- **Options.** (1) Keep m₁ below the geometry's threshold through `m_max`. (2) Model L0→L0 in the prior. (3) Accept, and measure it with the L0→L0 log (§5) and the knee sweep.
- **Recommendation (W2, V1):** at least (3); the prior should not assume L0→L1 always.

### 6.6 Per-level Bloom and cache counts can be charged to the wrong level. Needed by M6
- **Found.** The per-level Bloom and block-cache counters use the level a table was opened at (`block_based_table_reader.h:676-678`). A trivially moved file keeps that level until its reader is evicted, because the table cache is keyed by file number (`db/table_cache.cc:198-201`). `user_key_return_count` uses the current level. Moves during the load carry over into mixgraph, even though `resetstats` erases them from `kCFStats`.
- **Mitigating fact.** The table cache holds 990 readers (`db_impl.cc:233-235`: `max_open_files` − 10, provided the soft `ulimit -n` is at least 1000, since `SanitizeOptions` clips `max_open_files` to it, `db_impl_open.cc:53-58`) against about 5,660 SSTs (feasibility E5), so readers are evicted often and reopen at their current level. The bias should be short-lived, but it has not been measured.
- **Why it matters.** p_j and q_j shift toward shallower levels and disagree with h_j at the same level; the RA sum is unaffected.
- **Options.** (1) Count trivial moves in the LOG during the load and mixgraph, and accept if rare. (2) Track moved files through the listener (file numbers and new level from `output_file_infos`). (3) Core fix: refresh the reader's level on a trivial move (enlarges the patch).
- **Recommendation (V4):** (1) first, else (2). W4's fix, flagging windows with Moved_j > 0, was refuted: staleness is persistent state, not a per-window event.

### 6.7 Ticker reads slip across mission boundaries. Needed by M4
- **Found.** The controller reads tickers after it dequeues `MissionEnd`, while the client keeps running. Client-thread tickers (`NUMBER_KEYS_READ`, `MEMTABLE_HIT`, `BYTES_WRITTEN`, `NUMBER_KEYS_WRITTEN`, `NUMBER_DB_SEEK`, `STALL_MICROS`) therefore include some of the next mission's ops, while the per-level perf snapshot is exact. mem and reach_j mix the two clocks. Reading tickers on the client takes a lock (`monitoring/statistics.cc:425-428`).
- **Why it matters.** Per-mission mem, U and stall shift slightly. Window sums telescope, so the error does not grow with the window.
- **Options.** (1) Compute mem and U exactly on the client (§5). (2) Read tickers on the client at `MissionEnd`, paying the lock on the hot path. (3) Accept.
- **Recommendation (V4):** (1), plus `NUMBER_KEYS_READ − Gets` as a lag monitor.

### 6.8 Supervisor rule S2 is inert, and the debt signal moves with k and m. Needed by M3
- **Found.** S2 fires at θ × soft limit = 0.5 × 64 GiB = 32 GiB, while the DB is about 2.8 GiB (feasibility E11), so it never fires. The pending-bytes estimate is also not "native debt": it adds the L0 and Lbase bytes once the L0 file count reaches the *option* k, which can cascade (`version_set.cc:3633-3667`), and it counts levels held above native target by m > 1 (3667-3684). While S1 forces k_eff = k_min below the option k, L0 is scored due but not yet counted as debt.
- **Why it matters.** If a lower limit made S2 live, it would toggle as n₀ crosses the option k and reset m for reasons k created. Those overrides would feed the §10.10 override exclusion, correlated with the K arm.
- **Options.** (1) Keep S2 as a documented no-op safeguard at these limits. (2) Lower the soft limit (changes stall behaviour against feasibility §2). (3) Give S2 an input the controller does not move.
- **Recommendation:** none chosen (V2 flags the confound; V1 advises keeping the default limits).

### 6.9 Which L0 count S1 and the Thm. 5.9 check use. Needed by M3
- **Found.** S1 tests `l0_files`, all L0 files, the count the stall logic uses (`column_family.cc:1039, 1098`). The score's n counts only files not being compacted (`version_set.cc:3797-3805`). The two differ while an L0→L1 compaction runs, and `cond_b_violations` compares scores (non-compacting count) with σ₀ (derived from the stall count).
- **Options.** Stall count for S1 (it guards the stall trigger) with the mixed σ₀ comparison documented, or one count for both. W1 recommends passing both counts in `ScoreInputs` (§5).

### 6.10 SA numerator. Needed by M4
- **Found.** `total-sst-files-size` includes files kept alive only by older Versions, such as a running compaction's inputs, so SA spikes during long compactions. `live-sst-files-size` covers the current Version only (§4.10).
- **Options.** Keep the doc's `total-sst-files-size`, or switch to `live-sst-files-size`. Record the choice.

### 6.11 Table-cache reopens are a read cost outside RA. Needed by M4
- **Found.** With 990 reader slots (`max_open_files` − 10, if the soft `ulimit -n` ≥ 1000; §6.6) and about 5,660 SSTs, Gets reopen tables on the client thread. Each reopen reads the footer, index and filter from the device, and none of that enters RA, because index and filter blocks sit outside the cache. `NO_FILE_OPENS` then measures this read cost as well as new-file churn (V4 correction 3; feasibility E5).
- **Options.** Keep "opens" as a separate metric outside the SLO box (the doc's choice, §8.4), or fold it into read cost. Log the file count per mission either way.

### 6.12 Smaller decisions
- **Override-ratio bias.** Each `SetOptions` adds one `Adjust` call at the mission boundary, and the 600 s periodic rescore adds more, so the §10.10 override ratio leans toward boundary states (V2 M10, V1 C3). Options: exclude controller-thread and periodic calls from the ratio, or accept.
- **p_j fallback.** §8.4 uses 0.01 when a level had no filter probes; for a level with *no filter* the truth is 1. The startup checks remove the no-filter case; decide whether "no probes" should also use 1.
- **Last-level m.** Validation may accept or reject a non-1.0 entry for level `num_levels−1`, which is never scored; either keeps I1.
- **Latency includes controller work.** `--histogram` times the gap between `FinishedOps` calls (`DBB:2415-2428`), so recorder and `MissionEnd` work on the client thread lands in every mode except `off` (V5 M8). Decide whether latency comparisons of C against A and B accept this.

### 6.13 Arm A cannot run the paper command line. Needed by the A/B harness (M9)
- **Found.** The stock binary exits on `rlresume` (`DBB:3922-3924`) and on any unknown flag (`--rl_*`, `--level_target_multipliers`, `--mix_schedule`), because gflags rejects them (V5 §3, row 65). So arm A's benchmark list must drop `rlresume` and its flags must drop the new ones, which breaks arch §16.2's "all arms use identical flags". mixgraph's seed stays aligned only because `rlresume` runs on the main thread (V5 M6).
- **Why it matters.** The A/B stock-equivalence check (I-4) and every A-based overhead figure compare runs whose command lines differ.
- **Options.** (1) Arm A runs the stripped command line, recorded as a deviation from §16.2. (2) The harness pairs arm B (patched, `off`) with arm C for overhead and behaviour, and uses arm A only for the stock-equivalence check against B.
- **Recommendation:** none in the reports (raised by V7).

## 7. db_bench configuration facts

Paper-mode draft command line for one run (W5), with V5's corrections applied. Markers: **[feas§2]** feasibility §2 table; **[G*]** feasibility ground rule; **[src]** default or behaviour checked in the source; **[doc]** architecture doc; **UNKNOWN** not settled. V5 checked that every flag exists with this spelling and that no value contradicts feasibility §2. On the stock binary the list stops at `rlresume` with "unknown benchmark", after the full load (`DBB:3922-3924`), and the new flags are rejected, so this line cannot be given to arm A as written (§6.13).

```
--benchmarks=filluniquerandom,waitforcompaction,resetstats,rlresume,mixgraph  [doc §13.4; rlresume needs the patch]
--db=UNKNOWN                         path on the measured device (default is under /tmp)   §6.4
--seed=UNKNOWN                       fixed and non-zero (0 = clock)                          §6.4
--use_existing_db=false              [src default]
--threads=1                          [feas§2] (not enforced by db_bench for mixgraph)
--num=2900000                        [feas§2] load size and mixgraph key space
--reads=7100000                      [feas§2] mixgraph op count (if omitted: 2.9M)
--duration=0                         [src default] required, else --reads is ignored
--key_size=64                        [G4]
--value_size=960 --value_size_distribution_type=fixed   [G4] load values only
--value_theta=960 --value_k=0 --value_sigma=0           [src DBB:6937] exactly 960 B mixgraph Puts (stock ≈36 B)
--mix_max_value_size=1024            [src default] ≥ 960 suffices (wrap test is strict >)
--mix_get_ratio=0.806 --mix_put_ratio=0.159 --mix_seek_ratio=0.035   [feas§2] thresholds 806/965/1000
--key_dist_a --key_dist_b --keyrange_dist_a..d --keyrange_num = UNKNOWN   stock 0/0 = uniform   §6.2, §6.4
--iter_theta --iter_k --iter_sigma --mix_max_scan_len = UNKNOWN   stock 0/2.517/14.236, 10000   §6.4
--use_direct_reads=true --use_direct_io_for_flush_and_compaction=true   [G5]
--disable_wal=false --sync=false     [G5] (the WAL stays buffered I/O)
--statistics=1 --perf_level=2        [doc §13.2]
--histogram=1                        [src DBB:433] needed for p95/p99 (V5)
--compaction_style=0 --compaction_pri=3                  [G1] leveled, kMinOverlappingRatio
--level_compaction_dynamic_level_bytes=false             [G2]
--num_levels=13 --write_buffer_size=2097152 --target_file_size_base=524288   [feas§2]
--max_compaction_bytes=13107200                          [feas§2] 12.5 MiB
--max_bytes_for_level_base=8388608|16777216|33554432     [feas§2] varied per cell
--max_bytes_for_level_multiplier=2|6|10 (14, 20 in two cells)   [feas§2] varied per cell
--level0_file_num_compaction_trigger=2|4|8|16            [feas§2] k₀, varied per cell
--level0_slowdown_writes_trigger=20 --level0_stop_writes_trigger=36   [feas§2]
--max_background_jobs=2 --subcompactions=1               [feas§2]
--soft_pending_compaction_bytes_limit=68719476736 --hard_pending_compaction_bytes_limit=137438953472   [feas§2]
--bloom_bits=10                      [feas§2] (default −1 = no filter)
--block_size=4096 --cache_size=8388608 --cache_index_and_filter_blocks=false --open_files=1000   [feas§2]
--cache_type=UNKNOWN                 stock hyper_clock_cache   §6.4
--compression_type=none              [feas§2] (with the default --compression_manager=none)
--level_target_multipliers=…         new flag, ':'-separated (§5); --rl_* and --mix_schedule per doc §13.1
```

Notes:
- Write every flag as `--flag=value`. A bool written `--disable_wal false` silently sets it true, because db_bench ignores leftover arguments (V5 risk 12).
- Values waiting on §6 decisions: the key-distribution and scan-length flags (§6.2, §6.4), `--seed`, `--db` and `--cache_type` (§6.4), and `--mix_schedule`'s meaning (§6.2). §6.3 changes the binary, not the flags.
- `--max_bytes_for_level_multiplier_additional` is omitted; if given, it needs exactly 13 integer entries (`DBB:4816-4826`).
- `waitforcompaction` sleeps 5 s, waits for background work, does not flush the memtable, and carries on after a background error (`DBB:8878-8899`).
- `resetstats` clears `kCFStats` only; the final `STATISTICS:` dump includes the load.
- Derived sizes (feasibility §2): about 2,832 MiB of data in about 5,660 SSTs of 512 KiB; about 7.8 MiB of user writes per 50,000-op mission.
