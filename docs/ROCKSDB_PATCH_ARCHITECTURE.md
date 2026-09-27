# Controller Architecture — RocksDB Patch, C++ Actor, Python Learner

Version 2.1 · September 2026 · Supersedes version 2.0 (which lacked I/O-mode tracking and predated feasibility Revision 4) and version 1.0 (RocksDB patch only).

Companion to *Is an RL Compaction Trigger for RocksDB Worth It? — Revision 4* (the "feasibility doc"). Labels such as G7, E10, Def. 5.8, Thm. 5.9 and Cor. 4.11 refer to that document.

---

## 0. How to use this document (instructions for Claude Code)

1. **Order of work.** Implement milestone by milestone in the order of §17. A milestone may start only when the previous one builds and its listed tests pass.
2. **M0 comes first and changes no code.** Verify every item in §18 against the repository's pinned RocksDB version and record the result in `PATCH_NOTES.md`. RocksDB function names in this document were checked against RocksDB `main` in September 2026; line numbers are deliberately omitted.
3. **Deviations.** If the source differs from this document, make the smallest change that keeps every invariant in §2.4, and record the deviation in `PATCH_NOTES.md`. Never weaken an invariant to make code fit.
4. **Picker files are off limits.** Never modify `db/compaction/compaction_picker*.cc`. If a step seems to need it, stop and report.
5. **Defaults.** Every tunable value and its default is in the flags table (§13.1). Fixed design constants (for example the 1.5 in the safety gate, or the clip ranges in §8.4) appear inline and must be implemented exactly as written. Do not invent values. If a value you need is missing, stop and report.
6. **Symbols.** Every symbol used in a formula is defined in §8.4 or Appendix A. If you meet an undefined symbol, stop and report; do not guess.
7. **Commits.** Commit each milestone separately, with its tests passing in a debug build (assertions on).
8. **Conventions.** Use the repository's own formatter (`make format` if present), test framework (`DBTestBase`, `SyncPoint`, gtest) and option-registration patterns. Python code uses the standard library plus `numpy` (and `scipy`, for M8 only), with `pytest` for tests.

---

## 1. Goal, scope and non-goals

**Goal.** A controller that pursues the feasibility doc's phase-aware SLO (R5) by changing only the inputs RocksDB scores against (G7):

- the L0 trigger `level0_file_num_compaction_trigger` (k);
- a per-level multiplier mᵢ on the size level i is scored against.

RocksDB's own picker chooses every compaction. A supervisor inside RocksDB can override the controller on every rescore, to prevent L0 slowdowns and runaway debt.

**Components.**

- **(A) RocksDB core patch:** one option, one scoring change, one hook.
- **(B) C++ controller library,** linked only into `db_bench`: the mission recorder, phase detector, actor (which chooses settings), event listener, telemetry and socket channel.
- **(C) Python learner:** updates the learned corrections and the constraint prices, and pushes parameters to the actor.

**Non-goals.**

- No change to the compaction picker, file selection, stall triggers or flush logic.
- No support for `level_compaction_dynamic_level_bytes=true`, universal or FIFO compaction.
- Exactly one `db_bench` client thread when the controller is on.
- Nothing learned in one run is used in another (§2.2, O2).
- No deep neural network.
- The propagation law (§11.7) is optional (milestone M8).

---

## 2. Ground rules, decisions, invariants

### 2.1 Ground rules (from the feasibility doc)

| ID | Rule |
|---|---|
| G1 | Leveled compaction, file-level merges, `compaction_pri=kMinOverlappingRatio` |
| G2 | `level_compaction_dynamic_level_bytes=false` |
| G3 | L0 native except k |
| G4 | `mixgraph` workload, near-constant entry size (64 B key, ≈960 B value), no deletes |
| G5 | Paper mode: `use_direct_reads=true`, `use_direct_io_for_flush_and_compaction=true`, WAL on, sync off |
| G6 | The controller starts cold in every run |
| G7 | RocksDB picks every compaction; the controller acts only through k and mᵢ |

### 2.2 Project decisions

| ID | Decision |
|---|---|
| O1 | The objective is the phase-aware SLO box: in each phase, minimise the priority metric while the other metrics stay within (1+β) of a knee, and stall time stays under s_max. It replaces the fixed `WA ≤ 1.02 × native` constraint. |
| O2 | Cold runs only. No learned state crosses runs. Within a run, a recurring phase may reuse what that run has learned. |
| O3 | The learner decides which levels are worth learning (§10.6); there is no fixed per-T rule. |

### 2.3 Design decisions

| # | Decision | Reason |
|---|---|---|
| D1 | Settings-only control (G7). | The picker stays stock; the patch stays small. |
| D2 | mᵢ **scales the score**; it never rewrites the target. | `VersionStorageInfo::SetFinalized()` asserts in debug builds that targets never decrease with depth across non-empty levels, and target rewriting can trip it. The pending-bytes estimate and `FindMinimumEmptyLevelFitting` also read targets, so leaving targets native keeps RocksDB's stall protection seeing native debt. It is a one-line change. |
| D3 | L0 keeps its byte term, so k_eff = min(k, ⌈C₁/f₀⌉). | Static-mode L0 score is max(n₀/k, L0 bytes / `max_bytes_for_level_base`) (E10). Scaling the byte term would extend G3. |
| D4 | The client thread owns the operation clock, emits 1,000-operation sub-window records, and ends missions (including early cuts). | Keeps operation counts, bytes and thread-local read counters on the same boundaries; the clock keeps moving in read-only phases. |
| D5 | **C++ acts, Python learns.** The actor chooses settings in-process at mission boundaries using the latest parameter packet; Python only updates parameters. | Actions are settings that RocksDB applies at every rescore, so their timing is exact (Prop. 5.13). A slow or dead learner only freezes learning. This replaces v1's "Python returns actions". |
| D6 | The supervisor runs inside `ComputeCompactionScore`, under the DB mutex. | The L0 guard must react within one flush. |
| D7 | The controller library lives on the `db_bench` side, not in RocksDB core. | Keeps the core patch minimal, so B1 is fair. |
| D8 | Non-default multipliers are rejected when `dynamic_level_bytes=true` or the style is not leveled. | A B2 run must never carry multipliers unnoticed. |
| D9 | The SLO box is centred on the **reachable knee**: the knee of the controller's own static settings on the same geometry, measured offline in paper mode per operation mix (§16.1). | With T and base size fixed (G2, G7), B3's knee can lie outside what k and mᵢ reach while deep levels stay native, and a box around it would then be infeasible, making the constraint price diverge (feasibility §8.1). A knee that is itself a reachable static setting is feasible by construction. B3 remains the evaluation baseline. |
| D10 | One-epoch horizon (a contextual bandit): no bootstrapping. | An epoch is one turnover of the branch's level, so most of an action's effect lands inside it (Thm. 5.2). This avoids the instability that comes from combining a learned model, bootstrapped targets and off-policy data (Prop. 10.2), and the over-estimation of Prop. 10.3. The feasibility doc allows it (its §13, item 3). |
| D11 | Value = physics prior + a linear correction with uncertainty, kept separately for the read and write components. | About 20–70 decisions per branch per run cannot fit a large model (Thms. 5.2/5.4). Separate components stay comparable across phases whose priorities differ. |
| D12 | Phases are detected only from the operation mix, by CUSUM over 1,000-operation sub-windows, with an early mission cut on an alarm. | The mix is set by the workload alone; signals the controller changes (drops, cache, flushes) would make the detector react to its own actions. |

### 2.4 Invariants (tests enforce these)

- **I1 Stock equivalence.** With `level_target_multipliers` at its default and no supervisor installed, every compaction score, the pending-bytes estimate and every compaction decision are bit-identical to the unpatched build.
- **I2 Picker untouched.** RocksDB compacts the highest-scoring level with score ≥ 1 whose inputs are free, exactly as before.
- **I3 Targets untouched.** `MaxBytesForLevel()` returns native values in every mode.
- **I4 Locality.** mᵢ ≠ 1 changes only level i's score, by the factor 1/mᵢ.
- **I5 Supervisor safety.** The supervisor never blocks, never allocates on its hot path, performs no I/O, takes no lock other than the DB mutex it is called under, and never calls back into the DB.
- **I6 Learner isolation.** A late, crashed or misbehaving learner can only freeze learning: exploration switches off and the last valid parameters stay in use. Acting stays supervised and never waits on the learner.
- **I7 Cold runs.** Neither the actor nor the learner reads learned state produced by another run. The knee table and flags are inputs describing static configurations, not learned state.
- **I8 A fallback always exists.** Every mask leaves at least one allowed arm, and the rules in §10.7 always resolve to exactly one arm.
- **I9 Determinism.** Given the same mission records, parameter packets and `--rl_seed`, the actor makes identical decisions.

### 2.5 Deviations from the feasibility doc

| ID | Feasibility doc | This design | Status |
|---|---|---|---|
| V1 | Rev. 3 §8.1: the knee is on hull B3 | The reachable knee (D9) | Amended in Revision 4 (its §8.1) |
| V2 | Rev. 3 §13 item 1: learn "L1, possibly L2" | Learnability states (§10.6) | Amended in Revision 4 (its Prop. 5.21 and §13 item 1) |
| V3 | Rev. 3 §13 item 3: Double DQN or a contextual bandit | The contextual-bandit form with a model-based prior (D10, D11) | Consistent; Revision 4's §13 item 3 names it |
| V4 | Rev. 3 §13 item 5: `WA ≤ 1.02 × native` | The SLO box (O1) | Amended in Revision 4 (its §8.1 and §13 item 5) |
| V5 | Rev. 3 Thm. 4.2: 2% space budget | Space headroom derived from the box (§10.8) | Amended in Revision 4 (its Thm. 4.2 uses a headroom B) |
| V6 | §10.1(b): "native decision by default, deviate with small probability" | Confidence-based switching away from the baseline, plus gated, capped exploration (§10.7) | Consistent |
| V7 | M2: RA includes table opens | RA here is the data-block part, feasibility's RA_d; opens reported separately (§8.4) | Consistent; Revision 4 defines RA_d (its M2 and §3.4) |
| V8 | Rev. 4 §8.1: the knee is over the static settings of every controlled input | The knee sweep covers k × level-1 multipliers only (§16.1), because which levels are controlled is decided during the run | Approximation; open item (§19) |

---

## 3. System overview

```
 db_bench client thread (exactly 1)          RocksDB (patched)
 ┌──────────────────────────────┐             ┌────────────────────────────────────┐
 │ MixGraph op loop             │ Get/Put/Seek│ DBImpl::SetOptions → new Version,  │
 │  └ MissionRecorder::OnOp()   │───────────▶ │   rescore, schedule work           │
 │     every 1,000 ops:         │             │ VersionStorageInfo::               │
 │       SubWindow record ─┐    │             │   ComputeCompactionScore()         │
 │     at mission end:     │    │             │    ├ supervisor->Adjust() S1–S4    │
 │       MissionEnd record ┤    │             │    ├ L0 score uses k_eff           │
 │     early-cut flag ◀────┼──┐ │             │    └ Ln score ÷ m_eff (D2)         │
 └─────────────────────────┼──┼─┘             │ RlListener (flush/compaction/stall)│
          SPSC queue       ▼  │               └────────────────────────────────────┘
 ┌─────────────────────────────┴──┐ SetOptions         ▲ reads counters
 │ Controller thread               │───────────────────┘
 │  PhaseDetector  Actor  Telemetry│
 │  RlChannel ⇄ Python learner     │  Unix socket, length-prefixed JSON
 └─────────────────────────────────┘
```

### 3.1 Threads

- **Client.** Runs operations and counts them; pushes records into a single-producer/single-consumer queue; checks the early-cut flag after every operation. No I/O, no locks.
- **Controller.**
  - Consumes records; runs the phase detector on each `SubWindow`.
  - At each `MissionEnd`, runs the mission procedure (§3.2).
  - Owns telemetry and the socket.
  - Is the only caller of `SetOptions`.
- **RocksDB background threads.** Call the supervisor under the DB mutex and fire `RlListener` callbacks.

### 3.2 Mission procedure (controller thread, at each `MissionEnd`, in this order)

1. **Close the mission.** Read ticker deltas, per-level compaction-statistics deltas, DB properties and the supervisor and listener counters (§8.3).
2. Compute the mission's derived quantities (§8.4).
3. **Phase bookkeeping (§9.5).** Mark whether the mission contains a change point; attach the current phase ID, label, priority and knee.
4. **Epochs.** For each branch (§10.1), add the mission to its current epoch. Close the epoch if it has reached its length (§10.2), or if the phase-reaction rule (§9.6) ends it. On closing:
   - compute the measured components (§10.10);
   - decide whether the epoch is excluded from learning (§10.10);
   - update the noise samples (§10.6);
   - emit a `transition` message (§12.2).
5. **Parameters.** Swap in the newest valid parameter packet, if one arrived (§12.3–12.4).
6. **Learnability states.** Update the state of every branch whose epoch closed (§10.6).
7. **Space headroom.** If the phase changed, the set of controlled level branches changed, or a baseline changed, recompute the headroom and push `m_min` and `m_max` to the supervisor (§10.8).
8. **Choose arms.** For every branch starting a new epoch: build its state, compute prior and value, apply the masks, choose an arm (§10.7) and set E_b (§10.2).
9. **Apply settings.** Call `SetOptions` with the full current k and multiplier vector (§10.9).
10. **Report.** Write the telemetry line (§14) and send the `mission` message (§12.2).

During warm-up (the first `--rl_warm_missions` missions after `rlresume`), steps 4–9 run with every branch Observing (§10.6), and every transition is marked excluded with reason `warmup`. Warm-up missions and transitions go to telemetry only; nothing is sent to the learner until the `hello` exchange at the end of warm-up (§12.2).

---

## Part A — RocksDB core patch

## 4. Option `level_target_multipliers`

### 4.1 Definition (`include/rocksdb/advanced_options.h`, `AdvancedColumnFamilyOptions`)

```cpp
// Leveled compaction with static level targets only
// (level_compaction_dynamic_level_bytes = false).
// Level i (i >= 1) is scored against MaxBytesForLevel(i) * m[i] instead of
// MaxBytesForLevel(i). Level targets themselves are NOT changed.
// m[i] < 1 makes level i compact earlier; m[i] > 1 defers it.
// Entry 0 must be 1.0. Missing entries mean 1.0. Default: empty (all 1.0).
// Dynamically changeable through SetOptions().
std::vector<double> level_target_multipliers;
```

### 4.2 Plumbing (mirror `max_bytes_for_level_multiplier_additional`)

That option is registered in `options/cf_options.cc` as `OptionTypeInfo::Vector<int>(offsetof(struct MutableCFOptions, …), OptionVerificationType::kNormal, OptionTypeFlags::kMutable, {0, OptionType::kInt})`, stored in `MutableCFOptions` (`options/cf_options.h`), with accessor `MaxBytesMultiplerAdditional(level)`. Mirror it:

1. Add the field to `MutableCFOptions`, its constructor from `ColumnFamilyOptions`, and `MutableCFOptions::Dump`.
2. Add the accessor `double LevelTargetMultiplier(int level) const`, which returns 1.0 when `level` is out of range.
3. Register it as `OptionTypeInfo::Vector<double>` with element type `OptionType::kDouble`. M0 verifies that element type is supported, and which separator the vector parser uses.
4. Update the code path that copies mutable options back into `ColumnFamilyOptions`, following the int-vector option.
5. Add it to `options_settable_test` wherever `max_bytes_for_level_multiplier_additional` appears.

### 4.3 Validation

In the column-family validation function that runs both at `DB::Open` and in `SetOptions` (M0 identifies it), return `Status::InvalidArgument` when:

- the size exceeds `num_levels`;
- any entry is non-finite or below `1e-3`;
- entry 0 ≠ 1.0;
- any entry ≠ 1.0 while `level_compaction_dynamic_level_bytes=true` or `compaction_style != kCompactionStyleLevel` (D8).

### 4.4 Applying changes

`DB::SetOptions({{"level0_file_num_compaction_trigger", "<k>"}, {"level_target_multipliers", "<v>"}})`.

On `main`, `SetOptions` appends a new Version without a MANIFEST write (so scores are recomputed), installs a SuperVersion and schedules due compactions (`InstallSuperVersionForConfigChange` → `InstallSuperVersionAndScheduleWork`), and writes a new OPTIONS file.

M0 checks the pinned version. If that version does not recompute scores on `SetOptions`, add the minimal recompute-and-schedule call and record it.

---

## 5. Score scaling in `ComputeCompactionScore`

Changes apply only to `VersionStorageInfo::ComputeCompactionScore(const ImmutableOptions&, const MutableCFOptions&, …)`, and only to its leveled, static-target paths.

**Effective settings.**

- If no supervisor is installed:
  - `eff.k = mutable_cf_options.level0_file_num_compaction_trigger`;
  - `eff.m[j] = mutable_cf_options.LevelTargetMultiplier(j)`.
- Otherwise the supervisor fills `eff` (§6.3).

**Levels ≥ 1.**

```cpp
double m = eff.m[level];
if (m == 1.0) {
  score = static_cast<double>(level_bytes_no_compacting) /
          MaxBytesForLevel(level);                     // exact stock expression
} else {
  score = static_cast<double>(level_bytes_no_compacting) /
          (static_cast<double>(MaxBytesForLevel(level)) * m);
}
```

**L0.** Use `eff.k` in place of the option value in the file term. Leave the byte term `max(score, total_size / max_bytes_for_level_base)` unchanged (D3).

**Left native.** `EstimateCompactionBytesNeeded`, the write-stall logic and the compaction speed-up logic keep using the option k and native targets. The `internal_stats` "Score" column shows the scaled scores, which is intended.

---

## 6. Supervisor hook

### 6.1 Public interface (`include/rocksdb/compaction_score_supervisor.h`)

```cpp
namespace ROCKSDB_NAMESPACE {
constexpr int kSupervisorMaxLevels = 32;   // assert num_levels <= this

struct ScoreInputs {
  int num_levels;
  int l0_files;                                 // all L0 files (stall-logic count)
  uint64_t l0_bytes_not_compacting;
  const uint64_t* level_bytes_not_compacting;   // [num_levels]
  const uint64_t* level_bytes_being_compacted;  // [num_levels]
  const uint64_t* native_target;                 // MaxBytesForLevel(j), [num_levels]
  int k_setting;                                 // option value
  const double* m_setting;                       // option values, [num_levels]
  uint64_t pending_bytes_estimate;               // native targets (D2)
  uint64_t soft_pending_limit;
  int slowdown_trigger;                          // level0_slowdown_writes_trigger
};

struct ScoreOverrides {
  int k_eff;
  double m_eff[kSupervisorMaxLevels];
};

class CompactionScoreSupervisor {
 public:
  virtual ~CompactionScoreSupervisor() = default;
  // Called under the DB mutex, possibly several times per event and from
  // different background threads. Must obey invariant I5.
  virtual void Adjust(const ScoreInputs& in, ScoreOverrides* out) = 0;
};
}  // namespace ROCKSDB_NAMESPACE
```

### 6.2 Storage

- Add `std::shared_ptr<CompactionScoreSupervisor> compaction_score_supervisor = nullptr;` to `AdvancedColumnFamilyOptions`, so it flows to `ImmutableCFOptions`. It must be set before `DB::Open`.
- Follow the pattern of an existing non-serialized `shared_ptr` column-family option (for example `table_properties_collector_factories` or `sst_partitioner_factory`), including how `options_settable_test` treats such fields. M0 records which pattern was used.

### 6.3 Call site

When `immutable_options.compaction_score_supervisor != nullptr` and the column family is leveled with static targets:

1. Call `EstimateCompactionBytesNeeded(mutable_cf_options)` **before** the scoring loop. The stock code calls it at the end; calling it twice is harmless. Do this only on this branch (I1).
2. Fill `ScoreInputs`.
3. Initialise `ScoreOverrides` to the settings: `k_eff = k_setting`, `m_eff[j] = m_setting[j]`.
4. Call `Adjust`.
5. Score with the result.

When the supervisor is null, none of this runs.

### 6.4 `ScoreSupervisorImpl` (controller library; implements Def. 5.8)

**Configuration.**

- Fixed at construction: `k_min`, `k_max`, `mu0`, `theta`, `age_ops`, `sigma_age`, and a pointer to the operation clock (`std::atomic<uint64_t>*`, incremented by the client).
- Updatable at runtime through `std::atomic` fields, written by the controller thread and read under the DB mutex:
  - `mode ∈ {Observe, Enforce}`;
  - `m_min[j]`, `m_max[j]` for every j.
- Initial values, set at construction before `DB::Open`: `mode = Observe`, and `m_min[j] = m_max[j] = m_base,j`, the startup multiplier of level j (§10.1). So S1 and S4 cannot move any level until the controller pushes bounds (§10.8).

  A reader may see a mix of old and new values across different levels. That is harmless, because every value it can see is a valid bound.

**Rules**, evaluated for every level j ≥ 1. Each rule sets only inputs that no higher rule has set.

```text
m_c[j] = clamp(m_setting[j], m_min[j], m_max[j])
S1  L0 guard  if l0_files >= slowdown_trigger - mu0:
                 k_eff = k_min;  m_eff[j] = m_max[j] for all j                (all set)
S2  Debt      if pending_bytes_estimate >= theta * soft_pending_limit:
                 m_eff[j] = 1.0 for all j not yet set  (clamped to [m_min[j], m_max[j]])
S3  Ageing    if age_ops > 0: for j not yet set with waited_ops(j) >= age_ops:
                 m_eff[j] = min(m_c[j], bytes_nc[j] / (sigma_age * native_target[j]))
S4  Clamp     k_eff = clamp(k_setting, k_min, k_max) if not set;  m_eff[j] = m_c[j] if not set
```

For levels outside the controller's reach, the controller sets `m_min[j] = m_max[j] = m_setting[j]`, so S1 and S4 leave them unchanged.

**Observe mode.** Evaluate all rules and update all counters, but write `out` equal to the settings unchanged.

**Ageing timers (S3).** For each level, record the operation-clock value at which its pre-S3 score `bytes_nc / (native_target · m_c)` first reaches ≥ 1. Reset the timer when that score drops below 1, or when `level_bytes_being_compacted[j] > 0`.

**Integrals for measurement.** On each call, with `Δ = op_now − op_last`:

- add `l0_files_last · Δ`;
- add `(bytes_nc + bytes_being_compacted)_last[j] · Δ` for every level j;
- then store the current values as "last".

Repeated calls at the same `op_now` add nothing. The integrals, the "last" values and `op_last` are atomics with relaxed ordering. At each `MissionEnd` the controller reads them and extends each integral to the current operation count itself, adding `last · (op_now − op_last)`; this matters when no rescore happened recently, as in a read-only phase. It divides the difference of extended integrals by the operations in a window to get the operation-weighted averages n̄₀ and S̄_j. A read may mix fields from two neighbouring calls, which shifts an average by at most one rescore interval and is accepted.

**Counters** (atomics, read by the controller):

- times S1, S2 and S3 fired;
- `adjust_calls`;
- per level: `override_calls[j]`, the calls in which S1, S2 or S3 changed `m_eff[j]` away from `m_c[j]`;
- `override_calls_k`;
- the last `k_eff` and `m_eff[]`;
- the longest ageing wait per level;
- `cond_b_violations`: while S1 is active, the number of calls in which some level j ≥ 1 had score ≥ σ₀ = (slowdown_trigger − mu0)/k_min. This is Thm. 5.9(ii)(b).

---

## Part B — C++ controller library (`tools/rl_controller/`, linked only into `db_bench`)

## 7. Components and modes

| Component | Thread | Responsibility |
|---|---|---|
| `MissionRecorder` | client | Counts operations; emits `SubWindow` and `MissionEnd` records; honours the early-cut flag (§8.1). |
| `ScoreSupervisorImpl` | RocksDB background (under the mutex) | §6.4 |
| `RlListener` | RocksDB background | §8.2 |
| `PhaseDetector` | controller | §9 |
| `Actor` | controller | §10 |
| `RlChannel` | controller | Unix socket with length-prefixed JSON (§12). A JSON library may be vendored here (single-header, MIT), never in RocksDB core. |
| `TelemetryWriter` | controller | One JSON line per mission (§14); the only file I/O. |

**Modes** (`--rl_mode`).

| Mode | Supervisor | Actor | Learner | Purpose |
|---|---|---|---|---|
| `off` | not installed | not running | none | Arms A and B of the A/B check; static runs |
| `frozen_native` | Observe | computes everything, always applies the baseline arms | none (no socket) | Arm C, the B1 baseline; knee sweeps |
| `learn` | Enforce | full: gated exploration plus learning | required | Experiments |
| `greedy` | Enforce | as `learn`, with exploration off | required | Ablation: learning without exploration |

In `learn` and `greedy`, a failed socket connection at `rlresume`, or a failed `hello` handshake at the end of warm-up, aborts the run with an error. The run never silently falls back to native.

In `frozen_native`, every branch stays Observing: the promotion test is computed and logged, but never applied.

---

## 8. Measurement

### 8.1 `MissionRecorder`

- **Counting.** `OnOp(type)` with `type ∈ {Get, Put, Seek}` increments the per-type counters and the shared operation clock (relaxed ordering). Calls are inline, with no allocation. When `rl_mode == off` the recorder pointer is null and the hook costs one branch.
- **SubWindow.** Every `--rl_subwindow_ops` operations of the global operation count, push `{op_end, gets, puts, seeks}` for that sub-window. Sub-window boundaries depend only on the operation count, never on mission boundaries, so every sub-window has exactly n_sw operations.
- **MissionEnd.** When a mission reaches `--rl_mission_ops` operations, or when the early-cut flag is set:
  1. snapshot `get_perf_context()->level_to_perf_context` (cumulative) and compute per-level differences against the previous snapshot, for `bloom_filter_useful`, `bloom_filter_full_positive`, `bloom_filter_full_true_positive`, `user_key_return_count`, `block_cache_hit_count` and `block_cache_miss_count`;
  2. push `{mission_id, op_start, op_end, gets, puts, seeks, wall_ns, per_level_perf_deltas}`;
  3. clear the early-cut flag.

  Never call `PerfContext::Reset()` mid-run.
- **Queue.** A bounded lock-free single-producer/single-consumer queue of 65,536 records carries both record types in order. If it is ever full, the client spins until space frees and counts the spins (`queue_full_spins`, in telemetry). Correct counts take priority over timing; a non-zero count marks the run.
- **Requirements.** The client needs perf level ≥ 2 with `EnablePerLevelPerfContext()`; `db_bench`'s thread setup already does this when `--perf_level ≥ 2`. The controller requires `--perf_level=2` in every mode other than `off`.

### 8.2 `RlListener` (`EventListener`, atomics only)

- `OnFlushCompleted`: flush count.
- `OnCompactionBegin` / `OnCompactionCompleted`:
  - compactions per input level;
  - duration in operations and wall time;
  - bytes read and written;
  - flushes completed during the compaction.

  The last feeds `max_flushes_during_compaction`, the per-mission maximum used to check Thm. 5.9(ii)(a) and to calibrate `--rl_mu0` in paper mode (feasibility §9.2).
- `OnStallConditionsChanged`: stall transitions by cause.

### 8.3 Global statistics read at each `MissionEnd`

Per-mission values are deltas of cumulative counters since the previous `MissionEnd`.

- **Tickers:**
  - `NUMBER_KEYS_READ`, `NUMBER_KEYS_WRITTEN`, `NUMBER_DB_SEEK`, `BYTES_WRITTEN`;
  - `FLUSH_WRITE_BYTES`, `COMPACT_WRITE_BYTES`;
  - `MEMTABLE_HIT`, `STALL_MICROS`, `NO_FILE_OPENS`;
  - `BLOCK_CACHE_DATA_MISS`, `BLOCK_CACHE_DATA_HIT`.
- **Compaction statistics** per output level j, from `GetMapProperty(DB::Properties::kCFStats)`: `Rn`, `Rnp1`, `Moved`, `Write`, `KeyIn`, `KeyDrop`. M0 confirms the key names and units.
- **Properties:** `rocksdb.estimate-pending-compaction-bytes`, `rocksdb.total-sst-files-size`, `rocksdb.estimate-live-data-size`, `rocksdb.num-files-at-level0`.
- **Supervisor and listener counters**, and the integrals of §6.4.

### 8.4 Derived quantities

All definitions are over a **window**, a set of consecutive missions. Counts and bytes are summed over the window before any ratio is taken. The window for a branch is the missions of its last closed, non-excluded epoch; if none exists, the last non-warm-up mission. Where a formula needs the current trigger k_cur or a current multiplier, it uses the value in force during the window's last mission.

**Workload.**

| Symbol | Definition | If undefined |
|---|---|---|
| Gets, Puts, Seeks | client counts | — |
| U | `BYTES_WRITTEN` (user bytes) | — |
| mem | `MEMTABLE_HIT` / Gets | 0 if Gets = 0 |
| put share, seek share | Puts/ops, Seeks/ops | — |

**Level sizes and targets.**

| Symbol | Definition | If undefined |
|---|---|---|
| C_j | native target: C₁ = `max_bytes_for_level_base`, C_j = C_{j−1} · T · `additional[j−1]` (verified against `MaxBytesForLevel` in test C3) | — |
| S̄_j | operation-weighted average size of level j (supervisor integral ÷ window operations) | 0 |
| n̄₀ | operation-weighted average number of L0 files | 0 |
| f₀ | `FLUSH_WRITE_BYTES` / flush count | `write_buffer_size` |
| L_d | deepest level with S̄_j > 0, fixed at the end of warm-up for the rest of the run (a later change is logged as a warning only) | — |

**Merge flows and costs.**

| Symbol | Definition | If undefined |
|---|---|---|
| in_j | Rn_j + Moved_j (bytes entering level j in the window) | 0 |
| out_j | in_{j+1} | 0 |
| δ₀ | (Rnp1₁ / in₁) / (S̄₁ / (k_eff(k_cur) · f₀)); batch-based, Thm. 7.2(iii); clipped to [0.05, 1] | 1.0 if in₁ = 0 or S̄₁ = 0 |
| δ_{j−1}, j ≥ 2 | (Rnp1_j / in_j) / (S̄_j / S̄_{j−1}) (Cor. 5.19); clipped to [0.05, 1] | 0.5 if in_j = 0 or S̄_{j−1} = 0 |
| drop_j | KeyDrop_j / KeyIn_j | 0 |
| κ̂ | median of κ_j = ln((in_j/U) / (in_{j+1}/U)) / ln(S̄_j/S̄_{j−1}) over j ≥ 2 with S̄_j/S̄_{j−1} ≥ 1.5 and in_j, in_{j+1} > 0 (Cor. 5.19); clipped to [0, 2] | 0 |

**Reads.**

| Symbol | Definition | If undefined |
|---|---|---|
| h_j | `user_key_return_count_j` / Gets (share of Gets found at level j) | 0 |
| reach_j | 1 − mem − Σ_{i<j} h_i, clipped to [0, 1] (share of Gets that consult level j) | 0 |
| pass_j | reach_j − h_j, clipped to ≥ 0 (consult j and are not found there) | 0 |
| p_j | FP_j / (FP_j + useful_j), with FP_j = `full_positive_j` − `full_true_positive_j` | 0.01 |
| q_j | miss_j / (hit_j + miss_j), from the per-level perf block-cache counts | 1.0 |

**Global metrics per mission.**

| Symbol | Definition | If undefined |
|---|---|---|
| RA | Σ_j `block_cache_miss_count_j` / Gets: the data-block part RA_d of feasibility M2 | 0 |
| WA | (`FLUSH_WRITE_BYTES` + `COMPACT_WRITE_BYTES`) / U | 0 |
| SA | `total-sst-files-size` / `estimate-live-data-size` | — |
| stall | `STALL_MICROS` / (wall_ns / 1000) | — |

| opens | `NO_FILE_OPENS` / (Gets + Puts + Seeks) | 0 |

Table opens are reported per operation but not included in RA: RocksDB's counters attribute them neither to point lookups (seeks open tables too) nor to levels, and the controller's inputs change them mainly through the file count, which data size and file size largely fix (feasibility §3.4, M2). M0 verifies that the per-level perf counters count point lookups only.

---

## 9. Phase detector

### 9.1 Signal

For each sub-window, x = (put share, seek share). The get share is the remainder.

### 9.2 Per-phase reference

After a phase starts (at `rlresume` or at a change point), the first `--rl_cusum_est_subwindows` sub-windows (W_est) give each component c:

- the mean μ_c;
- the noise σ_c = max(the sample standard deviation, √(μ̃(1−μ̃)/n_sw)), where μ̃ = clip(μ_c, 0.01, 0.99) and n_sw = `--rl_subwindow_ops`.

The detector is armed after W_est sub-windows. μ_c and σ_c stay fixed until the next change point.

### 9.3 CUSUM (armed state; Prop. 8.1; Δ = `--rl_cusum_delta`)

```text
for each component c and sub-window x:
  S+_c = max(0, S+_c + (Δ/σ_c²)·(x_c − μ_c − Δ/2))
  S−_c = max(0, S−_c + (Δ/σ_c²)·(μ_c − x_c − Δ/2))
alarm if any statistic > H,  H = ln(--rl_cusum_arl)
```

The change point is the sub-window after the last one at which the alarming statistic was 0.

- **False alarms.** At most about one per `--rl_cusum_arl` sub-windows (Lorden [58]).
- **Delay.** Roughly H ÷ (Δ²/2σ²) sub-windows for a shift of Δ: about 2 sub-windows at the defaults.

### 9.4 On alarm

1. Set the client's early-cut flag, so the current mission ends at the next operation.
2. Record the change-point operation.
3. Open a **provisional** phase whose mean is the average of the sub-windows since the change point; label it and look up its knee (§9.5).
4. Reset all statistics. Re-estimate μ and σ from the sub-windows since the change point; re-arm once W_est of them exist.

### 9.5 Finalisation, identity, label

When the estimation window of a provisional phase completes:

- **Identity.** If its mean is within Δ of an entry in the run's phase library, on both components, reuse that entry's ID. Otherwise add a new entry.
- **Label**, from the mean put share P:
  - write-heavy if P ≥ `put_hi`;
  - read-heavy if P ≤ `put_lo`;
  - mixed otherwise.

  Within a phase, the label changes only if the running mean crosses a threshold by more than `--rl_phase_hyst`.
- **Priority:** write-heavy → write; read-heavy → read; mixed → balanced.
- **Knee.** Look up the nearest knee-table entry (§16.1) by Euclidean distance on (put share, seek share); record the distance.

The first phase is opened as provisional at `rlresume` and is finalised during warm-up (the startup check in §13.2 guarantees the warm-up is long enough).

While a phase is provisional, the actor uses the prices of the library entry within Δ of its current mean, if any, and 1.0 otherwise. A mission's phase ID is the ID in effect at its end.

Decisions at an early-cut boundary use the provisional phase's label and knee. Finalisation updates the label, knee and weights for later decisions; it never forces a new decision by itself.

### 9.6 Reaction rule

At an early-cut boundary, branch b closes its epoch early if and only if either:

- fewer than 3 phases have completed in this run, or
- the median length of the last 3 completed phases, in missions, is ≥ 2·E_b.

Otherwise the epoch continues. It will be excluded from learning because it straddles the change point (§10.10). Phase lengths count partial missions as op-count fractions.

### 9.7 Limits (documented behaviour)

- **Key-pattern changes go undetected.** A change in key popularity with the same operation mix is not a phase. It reaches the actor through the drop-rate and cache features.
- **A gradual ramp becomes a sequence of short phases**, one each time the mix drifts by about Δ. Label hysteresis prevents the label from flapping.

---

## 10. Actor

### 10.1 Branches, arms, baseline

**Branches.**

- **K** (the L0 trigger): active when at least 2 K arms remain after de-duplication.
- **L_i**: a candidate for every level i in `--rl_candidate_levels`, where `auto` means 1 … L_d − 1, and only if S̄_i > 0 at the end of warm-up. The branch set is fixed from then on.

The deepest non-empty level L_d is never a branch.

**K arms.**

- Take the values k ∈ `--rl_k_grid` with k_min ≤ k ≤ k_max, and add the baseline k₀ if it is absent.
- Merge values with the same k_eff(k) = min(k, ⌈C₁/f₀⌉), using f₀ from the warm-up window (E10). The arm containing k₀ is represented by k₀ itself, so the baseline applies exactly the startup setting; every other arm is represented by the smallest k with that k_eff.
- Arms are frozen at the end of warm-up and ordered by k_eff.

**L_i arms.** Factors a ∈ `--rl_m_grid` (which must contain 1), ordered, with absolute multiplier x = m_base,i · a.

**Baseline.**

- k₀ = the value of `--level0_file_num_compaction_trigger` at startup.
- m_base,i = the option value at startup (from `--level_target_multipliers`, default 1.0), or the propagation law's value when `--rl_law` is on (§11.7).

The baseline arm (k₀, or factor a = 1) is the arm used while Observing.

### 10.2 Epochs

An epoch is a run of whole missions during which a branch holds one arm. Its length E_b, in missions, is fixed at the epoch's start:

- K: E = 1.
- L_i: E = clip(round(x_new · C_i / in_i,pm), 1, `--rl_epoch_max`), where in_i,pm is the inflow to level i per mission over the window, and x_new is the chosen arm's absolute multiplier (the level's new target turnover). If in_i,pm = 0, E = `--rl_epoch_max`.

An epoch closes at the first mission boundary at which its operations reach E_b · `--rl_mission_ops`, or earlier by the reaction rule (§9.6). So an early-cut short mission counts only for its operations.

Branches decide only when their epoch closes. All branches deciding at the same boundary share one `SetOptions` call.

### 10.3 SLO cost

For phase p, with priority π_p and knee (R*_p, W*_p, S*_p):

| Priority | Weights (w_R, w_W) | Constraint priced | Backstop check |
|---|---|---|---|
| read | (1, λ_W,p) | WA_p ≤ (1+β) W*_p | WA |
| write | (λ_R,p, 1) | RA_p ≤ (1+β) R*_p | RA |
| balanced | (1, 1) | none | RA and WA |

- **Branch cost**, in dimensionless units: J_b = w_R · y^R_b / R*_p + w_W · y^W_b / W*_p. The measured components y are defined in §10.10.
- **Space** (SA ≤ (1+β) S*_p) is enforced through the headroom and the supervisor clamp (§10.8).
- **Stall** (≤ s_max) is enforced through the supervisor, the safety gate and mask M6. Stall time is not part of J.
- **Degenerate knee.** If R*_p ≤ 0 or W*_p ≤ 0, that component's weight is 0.
- **No knee table** (allowed only in `frozen_native`): R* = W* = 1, and neither the headroom nor the backstop is computed.
- **Prices before a packet.** Until a parameter packet provides a phase's prices, they are 1.0; without a learner (`frozen_native`) they stay 1.0.
- **Phase aggregates.** RA_p and WA_p are ratios of sums over all valid missions of phase ID p in this run (excluding warm-up and each mission that contains a change point). They are computed identically in C++ (§10.8) and Python (§11.5).

### 10.4 Physics prior

For each component c ∈ {R, W}, f^c_b(x) is the model's prediction of that component at arm x. Inputs come from the window (§8.4); x_cur is the arm in force during the window.

**K branch.**

```text
ke(x)      = min(x, ceil(C_1 / f0))                                  # E10
nbar0(x)   = nbar0 * (ke(x) + 1) / (ke(x_cur) + 1)
pass0      = clip(1 - mem - h_0, 0, 1)
fR_K(x)    = nbar0(x) * pass0 * p_0 * q_0 + h_0 * q_0                 # Thm. 3.2
fW_K(x)    = (in_1 / U) * (1 + delta_0 * Sbar_1 / (ke(x) * f0))        # Thm. 7.2(iii)
```

**L_i branch** (x is an absolute multiplier).

```text
S_i(x)    = Sbar_i * x / x_cur
out(x)    = out_i * (x / x_cur)^(-kappa)                              # Lemma 5.15 + (P3)
S_nx(x)   = Sbar_{i+1}                              if i+1 <  L_d
          = Sbar_{i+1} + Sbar_i - S_i(x)            if i+1 == L_d     # Cor. 4.6
S_up      = ke(k_cur) * f0 if i == 1 else Sbar_{i-1}
deep      = sum_{j >= i+2, j <= L_d} Write_j                          # 0 if i+2 > L_d
fW_i(x)   = [ in_i * (1 + delta_{i-1} * S_i(x) / S_up)                # merges into i  (eq. 1)
            + out(x) * (1 + delta_i * S_nx(x) / S_i(x))               # merges i -> i+1
            + deep * ratio(x) ] / U                                   # elision downstream (Thm. 4.9)
ratio(x)  = out(x) / out_i if out_i > 0 else 1
dh(x)     = clip( h_i * (S_i(x) / Sbar_i - 1), -h_i, h_{i+1} )
fR_i(x)   = [ (pass_i - dh(x)) * p_i + (h_i + dh(x)) ] * q_i
          + [  pass_{i+1} * p_{i+1} + (h_{i+1} - dh(x)) ] * q_{i+1}   # Cor. 4.11
```

**Guards.**

- If U = 0, then f^W ≡ 0. If Gets = 0, then f^R ≡ 0.
- If S̄_i = 0, the branch is not a candidate.
- If S_up = 0, drop the first term's overlap part: use in_i · 1.

**Check.** f^R_i(x) − f^R_i(x_cur) = −dh(x) · (p_i q_i + q_{i+1} − q_i), which is Cor. 4.11's per-lookup change. Test C4 verifies it.

**Anchored prior.** P^c_b(x) = ȳ^c_b + f^c_b(x) − f^c_b(x_cur), where ȳ^c_b is the component measured over the window (§10.10). So the prior equals the measurement at the current arm, and model error enters only through the predicted change.

**Prior uncertainty.** σ^c_P(x) = `prior_rel_sd` · |f^c_b(x) − f^c_b(x_cur)| + `prior_floor_c`.

### 10.5 Value and uncertainty

**Features.** φ(s) ∈ ℝ⁹, taken from the window at the epoch's start:

| # | Feature | K branch | L_i branch |
|---|---|---|---|
| 1 | bias | 1 | 1 |
| 2 | put share | same | same |
| 3 | seek share | same | same |
| 4 | drop rate | drop₁ | drop_i |
| 5 | own fullness, clipped to [0, 4] | n̄₀ / ke(k_cur) | S̄_i / (x_cur · C_i) |
| 6 | next fullness, clipped to [0, 4] | S̄₁ / (m₁ · C₁) | S̄_{i+1} / (m_{i+1} · C_{i+1}) |
| 7 | reach | reach₀ | reach_i |
| 8 | miss rate | q₀ | q_i |
| 9 | recency | min(missions since last alarm, 10)/10 | same |

**Residual.** For each (branch, arm, component), the latest parameter packet gives a mean μ^c_{b,a} ∈ ℝ⁹ and covariance Σ^c_{b,a}. Before any packet arrives, μ = 0 and Σ = 0.

**Value.** Lower is better; the priority weights and knee are those of the current phase.

```text
R_hat(a) = P^R_b(a) + mu^R_{b,a} · phi
W_hat(a) = P^W_b(a) + mu^W_{b,a} · phi
Q(a)     = w_R * R_hat(a) / R*  +  w_W * W_hat(a) / W*
V(a)     = (w_R/R*)^2 * (phi' Sigma^R_{b,a} phi + sigma^R_P(a)^2)
         + (w_W/W*)^2 * (phi' Sigma^W_{b,a} phi + sigma^W_P(a)^2)
```

A term whose weight is 0 (a degenerate knee, §10.3) is omitted from Q and V; it is never evaluated, so no division by R* ≤ 0 or W* ≤ 0 occurs.

**Comparing two arms.**

```text
Pr[Q(a) < Q(c) − margin] = Φ( (Q(c) − Q(a) − margin) / sqrt(V(a) + V(c)) )
```

Φ is the standard normal cumulative distribution. If V(a) + V(c) = 0, the probability is 1 when Q(c) − Q(a) − margin > 0 and 0 otherwise. Without a margin (as in the settling test), margin = 0.

### 10.6 Learnability states (O3)

Each branch is in exactly one state:

| State | Arm | Exploration | Transition data |
|---|---|---|---|
| **Observing** | baseline | none | Updates the baseline arm and the noise samples |
| **Learning** | chosen by §10.7 | gated, capped | Updates the arm used |
| **Settled** | the settled arm | none | Updates the arm used |

Every branch starts Observing after warm-up.

**Noise.** For each component c, σ^c_b is the pooled within-phase sample standard deviation of y^c_b over valid Observing epochs: deviations are taken from each phase ID's own mean. It is defined only once n ≥ `--rl_noise_min_samples` samples exist; otherwise σ_b = +∞.

The composite noise in the current phase's units is σ_b = (w_R/R*)·σ^R_b + (w_W/W*)·σ^W_b. This is an upper bound regardless of how the two components are correlated.

**Promotion test** (Observing → Learning; feasibility Proposition 5.21), evaluated at each valid closed Observing epoch:

```text
Delta_b  = max(0, Q(base) − min over allowed adjacent arms a of Q(a))      # posterior means;
                                                  # "allowed" = masks of §10.7 at this boundary
N_req    = 4 z² (sigma_b / Delta_b)²        (+inf if Delta_b = 0 or sigma_b = +inf)
M_rem    = max(0, rl_run_ops − ops_since_resume) / rl_mission_ops   if rl_run_ops > 0
         = rl_horizon_missions                                 otherwise
D_rem    = floor(M_rem / E_b)
promote if N_req <= gate_frac * D_rem
```

- z is `--rl_gate_z` (1.645, the one-sided 95% multiplier).
- E_b is the epoch length the branch would use at its baseline arm.
- N_req is the number of epochs needed to tell a gap of Δ_b apart from noise σ_b with that confidence.
- Promotion affects the next epoch.

**Settling test** (Learning → Settled), evaluated at each closed Learning epoch. Let a_best = argmin over allowed arms of Q.

- **(a) Confident.** If the minimum over other allowed arms a of Pr[Q(a_best) < Q(a)] ≥ `--rl_p_settle`, settle at a_best. If there is no other allowed arm, (a) does not apply.
- **(b) Out of time.** Otherwise, compute N_req with Δ̂ = |Q(a_best) − Q(base)|. If N_req > D_rem:
  - settle at a_best if Pr[Q(a_best) < Q(base) − Δ_min] ≥ `--rl_p_switch`;
  - otherwise settle at the baseline.

**Re-opening** (Settled → Learning). When a phase ID not seen before in this run is finalised and the promotion test passes for the new phase.

**Held arm.** If a held arm (Settled, or Learning without a switch) is removed by mask M2, the branch moves one arm toward the baseline, repeating until the arm is allowed.

**Reaching a settled arm.** Because of M3, a Settled branch whose settled arm is not adjacent to its current arm moves one step toward it per epoch.

### 10.7 Choosing an arm (branch starting a new epoch)

**Masks**, applied in this order to the candidate set:

| Mask | Rule |
|---|---|
| M1 | Arms are the frozen arm list (§10.1). |
| M2 | Range: K arms with k ∈ [k_min, k_max]; L_i arms with x ∈ [m_min,i, m_max,i] (§10.8). |
| M3 | One step: the current arm and its neighbours in the ordered list. |
| M4 | Drain (L_i, decreases only): allowed if (x_cur − x)·C_i ≤ max(out_i,pm, `target_file_size_base`) · E_new, where E_new is computed with x (Thm. 4.12). |
| M5 | Backstop (§10.8): while active, remove arms whose prior predicts an increase in the violated component relative to the current arm. |
| M6 | Stall: if the last mission's stall share > s_max, remove K arms with ke > ke(k_cur). |

**Guarantee (I8).** M3–M6 never remove the current arm. If M2 removes it, the candidate set becomes the single arm nearest to the current one in the direction of the baseline that passes M2. The baseline always passes M2, because m_min,i ≤ m_base,i ≤ m_max,i by construction (§10.8).

**Safety gate** (all must hold):

1. warm-up is over;
2. no supervisor rule fired in the last mission;
3. the pending estimate is ≤ 1.5 × the median of all post-warm-up missions' pending estimates;
4. n₀ at mission end is ≤ ke(k_cur) + 2;
5. the backstop is not active;
6. the last mission's stall share is ≤ s_max;
7. no alarm occurred in the last mission.

**Rule by state.**

- **Observing.** The baseline arm, or the M2 fallback.
- **Settled.** The settled arm, or the "held arm" fallback.
- **Learning:**
  1. **Greedy.** a* = argmin of Q over the candidate set. Switch to a* only if a* ≠ current and Pr[Q(a*) < Q(current) − Δ_min] ≥ `--rl_p_switch`. Otherwise keep the current arm.
  2. **Exploration.** Explore if all of these hold: mode is `learn`; the safety gate holds; the exploration token is free; the parameter packet is fresh (§12.4; no packet yet counts as stale); and a draw u ~ Uniform(0,1) < ε_b, where ε_b is `--rl_eps_k` for K and `--rl_eps_level` for L_i. To explore, draw Q̃(a) ~ N(Q(a), V(a)) for each candidate a ≠ current and pick the arm with the smallest Q̃. If no such candidate exists, skip. Otherwise mark the epoch explored and take the token. The token is released when that epoch closes.
- **Processing order.** Branches starting epochs at the same boundary are processed in descending order of Δ_b · D_rem, with ties broken in the order K, L1, L2, …. So the most valuable branch gets the first chance at the token.

**Randomness.** A `std::mt19937_64` seeded with `--rl_seed` is used only for exploration. For each Learning branch, in the processing order above, it draws u; if exploration then proceeds, it draws the Q̃ values in ascending arm order (I9).

### 10.8 Backstop and space headroom

**Backstop.** Active in phase p when, after at least 2 valid missions of p:

- read priority: WA_p > (1+β)(1 + `backstop_tol`) · W*_p;
- write priority: RA_p > (1+β)(1 + `backstop_tol`) · R*_p;
- balanced: either condition.

It deactivates when the metric returns within (1+β) · knee.

**Headroom.** Recomputed at each phase finalisation, whenever the set of level branches in Learning or Settled changes, and whenever a baseline m_base changes (§11.7):

```text
allowed_sst = (1 + beta) * S*_p * live_bytes             # live_bytes = estimate-live-data-size
headroom    = allowed_sst − total_sst_bytes
n_ctrl      = number of L_i branches in Learning or Settled
eps_i       = min(rl_eps_cap, max(0, headroom) / (n_ctrl * C_i))    for those branches (none if n_ctrl = 0)
m_max_i     = m_base,i + eps_i        for branches in Learning or Settled
            = m_base,i                otherwise (including non-candidate levels)
m_min_i     = min(rl_m_min, m_base,i)   for candidate levels
            = m_base,i                  for non-candidate levels
```

The controller writes `m_min[j]` and `m_max[j]` into the supervisor (§6.4). If the law (§11.7) would set m_base,i below `--rl_m_min`, it is clamped to `--rl_m_min` (Thm. 4.2).

### 10.9 Applying settings

At every `MissionEnd` in every mode other than `off`, call `SetOptions` once with:

- `level0_file_num_compaction_trigger` = the K branch's current k (the baseline k₀ while Observing or when the K branch is inactive);
- `level_target_multipliers` = a vector of length `num_levels`: entry 0 = 1.0, entry j = the current absolute multiplier of branch L_j, or m_base,j for levels without a branch.

Calling it every mission, even when nothing changed, gives every controller mode the same overhead (arm C fairness). Record the call's duration.

### 10.10 Measured components, exclusion, transitions

**Measured components** over the epoch's missions.

| Branch | y^R | y^W |
|---|---|---|
| K | `block_cache_miss_count_0` / Gets | Write₁ / U |
| L_i | (miss_i + miss_{i+1}) / Gets | Σ_{j ≥ i} Write_j / U |

y^W_K omits k's effect below L1. That is documented as a second-order approximation: the L0 band is ≤ 32 MiB against deeper levels of hundreds of MiB.

**Exclusion** (the epoch is logged but the learner skips it). Reasons, recorded in this priority order:

1. `warmup`;
2. `straddle`: a change point lies inside the epoch;
3. `override`: the branch's override counter (`override_calls_k` for K, `override_calls[i]` for L_i) divided by `adjust_calls`, both taken over the epoch, exceeds `--rl_override_exclude`;
4. `suspend`: `rlsuspend` occurred;
5. `baseline`: the branch's baseline changed during the epoch (§11.7);
6. `empty`: Gets = 0 and U = 0.

**Transition message** (§12.2), for every closed epoch after warm-up, including excluded ones (warm-up transitions go to telemetry only):

- branch, epoch ID, state, arm index, absolute setting, baseline;
- phase ID and priority;
- φ at the epoch's start;
- P^R and P^W at the epoch's start, for the arm used;
- y^R and y^W;
- the current σ^R_b and σ^W_b;
- whether the epoch was explored;
- excluded, and its reason;
- missions, op_start, op_end.

---

## Part C — Python learner

## 11. Learner (`tools/rl_controller/learner/`)

### 11.1 Role

- Consumes `hello`, `mission` and `transition` messages.
- Maintains the corrections and prices.
- Sends `params` packets.
- Never chooses actions.
- Holds no state across runs (I7): there is no option to load or save learned state, and the learner exits when its connection closes.

### 11.2 Correction model

For each (branch b, arm a, component c), a Gaussian linear model on φ:

```text
prior       : w ~ N(0, s_c² I),  s_c = resid_rel_sd * max(|y1^c_b|, 1e-6)
y1^c_b      : y^c of the branch's first valid Observing transition; s_c is fixed from then on
observation : r = y^c − P^c_b(a)  =  w · phi + noise,   noise ~ N(0, (sigma^c_b)²)
precision   : Lambda (9×9),  vector bvec (9),  mean mu = Lambda⁻¹ bvec,  covariance Sigma = Lambda⁻¹
initial     : Lambda = I / s_c²,  bvec = 0
update (only for the arm used, only for non-excluded transitions):
  Lambda <- g * Lambda + (1 − g) * I / s_c² + phi phi' / sigma²
  bvec   <- g * bvec   + phi r / sigma²
  g = rl_forget                       # older epochs count slightly less
```

- Until s_c is known for a branch, the learner sends μ = 0 and Σ = 0 for its arms, so the actor uses prior uncertainty only.
- σ^c_b comes from the transition message (C++ is its single source). If it is +∞ or 0, skip the update.
- Adding (1 − g)·I/s_c² keeps the precision at or above the prior's, so an arm that stops receiving data returns toward the prior.
- Solve with a Cholesky factorisation. On failure, drop the update and count it.

### 11.3 Excluded and Observing transitions

- Excluded transitions are ignored.
- Valid Observing transitions update the baseline arm, because they are genuine observations of it.

### 11.4 Why there is no bootstrapping (D10)

- The epoch equals the level's turnover, so an action's effect is mostly realised within its own epoch.
- The one-step transition cost is bounded by the one-step rule (M3) and the drain mask (M4).
- Bootstrapped targets would add variance, and bring in the combination of learned model, bootstrapped targets and off-policy data that Prop. 10.2 warns about, for little gain.

A later extension may enable a multi-epoch horizon only if telemetry shows spill-over: the arm at epoch t predicting the residual at epoch t+1 beyond what φ explains. That extension would require a masked argmax and adding the prior once. It is out of scope here.

### 11.5 Prices (per phase ID; O1)

For each `mission` message whose mission is valid (not warm-up, not containing a change point), add its raw sums (`gets`, `miss_blocks`, `user_bytes`, `written_bytes`) to phase p's totals; RA_p = Σ miss_blocks / Σ gets and WA_p = Σ written_bytes / Σ user_bytes (§10.3). Then:

```text
read priority :  gap = WA_p / ((1+beta) W*_p) − 1 ;  lambda_W,p <- clip(lambda_W,p + price_step·gap, 0, price_max)
write priority:  gap = RA_p / ((1+beta) R*_p) − 1 ;  lambda_R,p <- clip(lambda_R,p + price_step·gap, 0, price_max)
balanced      :  no update
initial lambda = 1.0 for each new phase ID;  a recurring phase ID reuses its prices.
```

A price rises while its constraint is violated and falls while there is slack. Because it moves at most `price_step·|gap|` per mission, it changes over tens of missions. It is never driven by the post-load backlog, which warm-up excludes.

### 11.6 Parameter packets

- Send one `params` packet after processing each `mission` message.
- `policy_version` increases by 1 per packet.
- `based_on_mission` is the ID of the last processed mission.
- Contents: every (branch, arm, component) μ and Σ, every phase's prices, and the law baselines when §11.7 is on.

### 11.7 Propagation law (optional; milestone M8; `--rl_law`)

Computed when phase ID p is finalised for the first time, and stored per phase ID. Whenever phase p is current, the learner includes p's stored baselines in its packets:

1. Take κ̂ and δ_j from the most recent valid mission window; S₀ = n̄₀ · f₀; D = Σ_{j≥1} S̄_j.
2. Solve Thm. 5.16 at the fixed point of Prop. 5.18 (bisection, as in the feasibility doc's numerical check) for sizes S*_j, j = 1 … L_d − 1.
3. Set m_base,j = S*_j / C_j, sent in the packet's `baselines`.

On receiving new baselines, the actor:

- moves each m_base,j toward its target by at most a factor of (1 + `--rl_law_step`) per mission; a decrease must also satisfy mask M4's drain condition with E_new = 1, otherwise it waits (Thm. 4.12);
- closes the current epoch of each affected L_j branch (excluded, reason `baseline`) and resets that branch to Observing.

The learner resets those branches' corrections to the prior.

With `--rl_law=off` (the default), no baselines are sent. Running with the law on and off, with paired seeds, is the propagation on/off comparison (feasibility §5.6, step 6).

---

## Part D — Interfaces

## 12. Contract

### 12.1 Transport

- Unix-domain `SOCK_STREAM` socket. Python is the server at `--rl_socket` and must be listening before `rlresume`; C++ connects at `rlresume`.
- Framing: a 4-byte big-endian length, then that many bytes of UTF-8 JSON.
- Every message carries `"schema": 2`. On a mismatch, C++ closes the connection and aborts the run.

### 12.2 Messages

| Type | Direction | When |
|---|---|---|
| `hello` | C++ → Py | at the end of warm-up, after arms and candidates are frozen |
| `hello_ack` | Py → C++ | once |
| `mission` | C++ → Py | every `MissionEnd` after warm-up, at step 10 |
| `transition` | C++ → Py | every closed epoch after warm-up (step 4) |
| `params` | Py → C++ | after each `mission` |
| `bye` | C++ → Py | at `rlsuspend` or shutdown |

Full examples are in Appendix B. Required fields:

- **`hello`:**
  - `run_id`, `git_commit`, `mode`, `num_levels`, `L_d`;
  - branches with ordered arm lists (absolute k for K; factors a for L_i, since x = m_base,i · a may change under the law) and baseline arm indices;
  - `feature_names` (the 9 features of §10.5);
  - native targets C_j, and the knee table (all entries);
  - `learner_cfg`, holding every learner-relevant flag: `beta`, `resid_rel_sd`, `forget`, `price_step`, `price_max`, `law`.
- **`mission`:**
  - `mission_id`, `op_start`, `op_end`, `valid`;
  - phase `{id, provisional, label, priority, knee:{R,W,S}}`;
  - global `{RA, WA, SA, stall}` and the raw sums behind them, `sums{gets, miss_blocks, user_bytes, written_bytes}` (miss_blocks = Σ_j `block_cache_miss_count_j`; written_bytes = `FLUSH_WRITE_BYTES` + `COMPACT_WRITE_BYTES`);
  - the mission's per-level inputs, used by the law (S̄_j, in_j, Rnp1_j, Write_j, KeyIn_j, KeyDrop_j), n̄₀, f₀;
  - branch states, arms, prices in use, supervisor and listener counters.
- **`transition`:** as in §10.10.
- **`params`:** `policy_version`, `based_on_mission`, `branches{b:{arms{idx:{R:{mu[9],cov[9][9]}, W:{...}}}}}`, `prices{phase_id:{lambda_R, lambda_W}}`, and an optional `baselines{level:m}`.

### 12.3 Validation

C++ rejects and counts a `params` packet if any of these hold:

- a dimension is not 9;
- a branch or arm is unknown;
- a value is not finite;
- a covariance is not symmetric, within 1e-9 relative;
- a covariance is not exactly zero and its Cholesky factorisation fails (an all-zero covariance is valid and means "no uncertainty data yet");
- a price is negative;
- `policy_version` is not greater than the last accepted one.

A rejected packet changes nothing.

### 12.4 Freshness and outages

- **Packet age** = the current mission ID − the packet's `based_on_mission`.
- **Fresh** means age ≤ `--rl_param_timeout_missions`.
- **Stale packet:** exploration is off; the actor continues with the last valid packet.
- **Socket error or closed peer:** exploration off for the rest of the run, telemetry flag `invalid_learner`, and acting continues (I6).
- **`frozen_native`** never opens a socket.

---

## 13. `db_bench` wiring (`tools/db_bench_tool.cc`)

### 13.1 Flags (the single source of defaults)

| Flag | Default | Symbol / meaning |
|---|---|---|
| `--rl_mode` | `off` | `off`, `frozen_native`, `learn`, `greedy` (§7) |
| `--rl_socket` | `""` | learner socket path (required in `learn` and `greedy`) |
| `--rl_telemetry` | `""` | telemetry path (required unless `off`) |
| `--rl_knee_table` | `""` | knee table JSON (required in `learn` and `greedy`; optional in `frozen_native`) |
| `--rl_seed` | `0` | actor RNG seed |
| `--rl_mission_ops` | `50000` | mission length in operations |
| `--rl_subwindow_ops` | `1000` | sub-window length, n_sw |
| `--rl_run_ops` | `0` | operations in the measured `mixgraph` phase; 0 = use `--rl_horizon_missions` |
| `--rl_horizon_missions` | `50` | rolling horizon for D_rem |
| `--rl_warm_missions` | `2` | warm-up after `rlresume` |
| `--rl_candidate_levels` | `auto` | `auto` = 1…L_d−1, or a comma list |
| `--rl_k_grid` | `2,4,8,16` | K arms before de-duplication |
| `--rl_k_min`, `--rl_k_max` | `2`, `16` | `k_min`, `k_max` (§6.4, §10.1); k_max must be < `level0_slowdown_writes_trigger` |
| `--rl_m_grid` | `0.5,0.75,1,1.5,2` | L_i arm factors; must contain 1 |
| `--rl_m_min` | `0.25` | lower bound on absolute multipliers |
| `--rl_eps_cap` | `3.0` | `rl_eps_cap`, upper bound on ε_i (§10.8) |
| `--rl_slo_beta` | `0.10` | β (`beta`) |
| `--rl_stall_max` | `0.01` | s_max, as a share of wall time (§10.3) |
| `--rl_phase_put_hi`, `--rl_phase_put_lo` | `0.5`, `0.2` | `put_hi`, `put_lo` (§9.5) |
| `--rl_phase_hyst` | `0.05` | label hysteresis |
| `--rl_cusum_delta` | `0.05` | Δ, the smallest shift of interest |
| `--rl_cusum_arl` | `1e6` | H = ln(ARL) |
| `--rl_cusum_est_subwindows` | `20` | W_est |
| `--rl_mu0` | `4` | μ₀ = `mu0` (S1, §6.4). **Placeholder:** set it above the paper-mode maximum of `max_flushes_during_compaction` (Thm. 5.9(ii)(a)); iteration-mode runs understate that count |
| `--rl_theta` | `0.5` | θ = `theta` (S2, §6.4) |
| `--rl_age_ops` | `0` | `age_ops` (S3, §6.4); S3 off when 0 |
| `--rl_sigma_age` | `4` | σ_age = `sigma_age` (S3, §6.4); must be < σ₀ = (slowdown − mu0)/k_min |
| `--rl_eps_k`, `--rl_eps_level` | `0.15`, `0.20` | exploration probability per epoch |
| `--rl_switch_margin` | `0.005` | Δ_min, in composite cost units (§10.6, §10.7) |
| `--rl_p_switch`, `--rl_p_settle` | `0.90`, `0.95` | confidence thresholds |
| `--rl_gate_z`, `--rl_gate_frac` | `1.645`, `0.5` | z and `gate_frac` of the promotion test (§10.6) |
| `--rl_noise_min_samples` | `3` | noise estimate minimum |
| `--rl_epoch_max` | `50` | maximum epoch length in missions |
| `--rl_prior_rel_sd` | `0.5` | `prior_rel_sd` (§10.4) |
| `--rl_prior_floor_R`, `--rl_prior_floor_W` | `0.001`, `0.05` | `prior_floor_c` for c = R (blocks/Get) and c = W (bytes/user byte) (§10.4) |
| `--rl_resid_rel_sd` | `0.05` | `resid_rel_sd` (§11.2) |
| `--rl_forget` | `0.98` | forgetting factor g (§11.2) |
| `--rl_price_step`, `--rl_price_max` | `0.5`, `10` | `price_step`, `price_max` (§11.5) |
| `--rl_backstop_tol` | `0.01` | `backstop_tol` (§10.8) |
| `--rl_override_exclude` | `0.2` | exclusion threshold for supervisor overrides |
| `--rl_param_timeout_missions` | `3` | packet freshness limit |
| `--rl_law` | `false` | propagation law (M8) |
| `--rl_law_step` | `0.25` | largest relative change of a law baseline per mission |
| `--level_target_multipliers` | `""` | static option value, e.g. `1:1:1.5` (separator per M0); sets the baseline m_base |
| `--mix_schedule` | `""` | ground-truth phase schedule, `OPS:get,put,seek;OPS:…` (cumulative operation thresholds); used only by the `mixgraph` generator and the telemetry header |

### 13.2 Startup checks when `rl_mode != off`

Fail fast unless all of these hold:

- `--threads=1`;
- `--perf_level=2`;
- `--statistics=1`;
- leveled compaction with `level_compaction_dynamic_level_bytes=false`;
- `--rl_m_grid` contains 1;
- `k_min ≤ k₀ ≤ k_max < level0_slowdown_writes_trigger`;
- when a knee table is given, its geometry (T, base size, `num_levels`, `write_buffer_size`, `target_file_size_base`) and its I/O mode (`use_direct_reads`, `use_direct_io_for_flush_and_compaction`, WAL enabled = not `--disable_wal`) equal the current run's;
- `--rl_warm_missions · --rl_mission_ops ≥ --rl_cusum_est_subwindows · --rl_subwindow_ops`, so the first phase is finalised during warm-up (§9.5);
- `--rl_socket` and `--rl_knee_table` are set in `learn` and `greedy`;
- `--rl_telemetry` is set;
- if `--rl_age_ops > 0`: `--rl_sigma_age < (level0_slowdown_writes_trigger − --rl_mu0) / --rl_k_min` (Thm. 5.9(iii)).

Also warn when a `--rl_k_grid` value exceeds ⌈`max_bytes_for_level_base`/`write_buffer_size`⌉ (E10).

### 13.3 Setup before `DB::Open`

Construct the operation clock, `ScoreSupervisorImpl` (in Observe mode, with the initial bounds of §6.4), and `RlListener`. Install the supervisor and listener in the options.

### 13.4 Benchmarks

**`rlresume`:**

- starts the controller thread;
- starts warm-up;
- initialises the phase detector;
- connects the socket (`learn` and `greedy`); `hello` follows at the end of warm-up;
- sets the supervisor to Enforce in `learn` and `greedy`, Observe in `frozen_native`.

**`rlsuspend`:**

- sets the baseline settings;
- sets the supervisor to Observe;
- closes open epochs, excluded with reason `suspend`;
- stops decisions;
- sends `bye`.

Telemetry continues.

**Intended sequence:** `filluniquerandom,waitforcompaction,resetstats,rlresume,mixgraph`.

### 13.5 Hooks

- **`MixGraph`:** after each Get, Put or Seek, call `recorder->OnOp(type)`.
- **`--mix_schedule`:** the `MixGraph` generator switches its get/put/seek probabilities at the listed cumulative operation counts. The detector never reads the schedule.

---

## 14. Telemetry (JSONL, `--rl_telemetry`)

**Header line:**

- `type:"header"`, `schema:2`;
- `run_id`, `git_commit`, `rocksdb_version`;
- all flags, including `--mix_schedule`;
- `io_mode{direct_reads, direct_io_flush_compaction, wal_enabled}`, and `paper_mode` = all three true;
- native targets, `L_d`, the frozen arm lists;
- the knee table.

**One line per mission**, carrying the `mission` message fields plus:

- **Action and parameter tracking:**
  - `setoptions_ns` and the applied values;
  - `packet{version, age, fresh, rejected_total}`;
  - `exploration{token_holder, explored_branch}`.
- **Per branch:**
  - `{state, arm, x, E, epoch_id, epoch_mission_index}`;
  - `prior{P_R, P_W}` for every candidate arm, and `Q`, `V`;
  - `gate{sigma_b, Delta_b, N_req, D_rem}`;
  - `masks_applied`.
- **Phase:**
  - `phase{alarm, change_point_op, provisional, finalised, knee_distance}`;
  - `backstop{active, metric}`;
  - `headroom{bytes, m_max[]}`.
- **Supervisor and listener:**
  - `supervisor{mode, s1, s2, s3, cond_b_violations, override_calls[], adjust_calls, k_eff, m_eff[]}`;
  - `listener{flushes, compactions_by_level, max_flushes_during_compaction, stall_transitions}`.
- **Flags:** `flags{invalid_learner}`.

---

## Part E — Verification and delivery

## 15. Tests

**Modes.** The unit tests (T, C, P) and I-1 to I-3 may run in iteration mode (direct I/O off, WAL off) to save time; they check correctness only. I-4, the knee sweep (§16.1), the calibration of `--rl_mu0`, and every number that feeds the paper require paper mode (G5). Telemetry records the mode (§14), so iteration-mode results cannot be mistaken for paper-mode ones.

### 15.1 RocksDB core (debug build; RocksDB test framework)

| ID | Test | Pass condition |
|---|---|---|
| T1 | Option parse, serialize and round-trip; `SetOptions` accepts valid vectors | Values survive the OPTIONS file and `GetOptions` |
| T2 | Validation rejects: too many entries, non-finite values, values < 1e-3, entry 0 ≠ 1, and ≠ 1 with dynamic or non-leveled styles | `InvalidArgument` at open and at `SetOptions` |
| T3 | Bit-identity (I1): option absent vs all 1.0 vs supervisor in Observe | Identical `CompactionScore(i)`, `CompactionScoreLevel(i)`, `estimated_compaction_needed_bytes()` |
| T4 | Locality (I4, I3): m₂ = 0.5 | Level 2's score doubles; other scores unchanged; `MaxBytesForLevel` unchanged |
| T5 | Non-monotone multipliers (m₁ = 4.5, m₂ = 0.4) over several flushes and compactions | No assertion failure |
| T6 | Rescore without writes: L1 just under target; `SetOptions` m₁ = 0.5 | An L1→L2 compaction runs (`TEST_WaitForCompact`) |
| T7 | Supervisor rules S1–S4 with a test supervisor | Effective values and scores as specified; Observe leaves scores equal to the settings-only scores; integrals and counters correct |
| T8 | No supervisor | Supervisor branch never entered (`SyncPoint`); T3 holds |
| T9 | Dynamic mode | Scoring identical to stock; non-default multipliers rejected |

### 15.2 Controller library (gtest; synthetic inputs, no DB unless stated)

| ID | Test | Pass condition |
|---|---|---|
| C1 | Recorder with a real DB and a short `mixgraph` | Sub-window and mission counts exact; the early cut ends the mission at the next operation; recorder sums equal the ticker sums |
| C2 | CUSUM (seeded; each sub-window's counts drawn as multinomial over n_sw = 1,000 operations): (a) 10⁵ stationary sub-windows at 0.159; (b) step 0.9→0.1; (c) step 0.159→0.209; (d) zigzag 0.8↔0.1 every 20 missions; (e) ramp 0.9→0.1 over 50 missions | (a) 0 alarms; (b) alarm ≤ 2 sub-windows after the change; (c) alarm ≤ 10 sub-windows; (d) every flip detected, recurring IDs reused; (e) labels change monotonically with no flapping |
| C3 | Derived quantities (§8.4) on fixtures, including every guard | Matching hand-computed values; C_j matches `MaxBytesForLevel` on a real DB |
| C4 | Prior (§10.4) golden fixtures | Hand-computed f values; the K de-duplication follows E10; the conservation case is used when i+1 = L_d; f^R_i(x) − f^R_i(x_cur) = −dh·(p_i q_i + q_{i+1} − q_i) to 1e-12 |
| C5 | Value and probability (§10.5) | V ≥ 0; the Φ formula, including the V = 0 case |
| C6 | Masks (§10.7) | Each mask alone; the current arm is never removed by M3–M6; the M2 fallback moves toward the baseline; the baseline always passes M2 |
| C7 | Learnability state machine (§10.6) with synthetic σ, Δ, D | Promotion exactly at N_req = gate_frac·D_rem; both settling paths; re-opening only on a new phase ID; σ = +∞ below the minimum sample count |
| C8 | Exploration | The gate blocks under each condition; the token is exclusive; token order by Δ_b·D_rem; RNG draw order deterministic |
| C9 | Headroom (§10.8) | m_max values; m_min ≤ m_base ≤ m_max; pushed to the supervisor |
| C10 | Packets (§12.3–12.4) | Every rejection rule; staleness switches exploration off; a socket error sets `invalid_learner` |
| C11 | Replay determinism (I9) | Recorded missions and packets replayed twice give identical decisions |
| C12 | `SetOptions` composition (§10.9) | Correct string format, entry 0 = 1, vector length = `num_levels` |

### 15.3 Learner (pytest)

| ID | Test | Pass condition |
|---|---|---|
| P1 | Update with g = 1 | Equals the closed-form ridge regression on the same data |
| P2 | Forgetting | Λ ≥ I/s_c² in the matrix sense after any sequence of updates |
| P3 | Prices | Sign, step, clipping, per-phase storage, reuse on a recurring ID, no update for balanced or invalid missions |
| P4 | Exclusion | Excluded transitions change nothing; Observing transitions update the baseline arm |
| P5 | Packets | Schema-valid; each covariance symmetric positive-definite or exactly zero; versions increase |
| P6 | Cold runs (I7) | No command-line or config option loads or saves learned state; the process exits when its connection closes |
| P7 (M8) | Law solver | Reproduces the feasibility doc's κ = 0 fixed point (sizes 19.5/95.0/462.7 MiB at S_L = 2,255 MiB; W = 13.745) within 0.1% |

### 15.4 Integration (release build)

| ID | Test | Pass condition |
|---|---|---|
| I-1 | 1M-operation `learn` run against the stub learner (§16.3) | Telemetry validates; mission count matches; no aborts |
| I-2 | `--mix_schedule` with a write → mixed → read schedule and a 4-phase zigzag | Detection delays and false alarms reported against the schedule; C2's bounds hold |
| I-3 | Kill the learner mid-run | Exploration off; `invalid_learner` set; acting continues; supervisor counters behave normally |
| I-4 | A/B equivalence (§16.2), in paper mode | As stated there |

---

## 16. Offline tools

### 16.1 Knee sweep (`tools/rl_controller/knee_sweep.py`)

For each operation mix in a list, and each static setting in the reachable grid (K arms × `--rl_m_grid` for level 1, other levels at baseline), run `db_bench` in `frozen_native` mode with:

- `--level0_file_num_compaction_trigger=<k>`;
- `--level_target_multipliers=<vector>`;
- the mix fixed.

Run the sweep in paper mode (G5). Take RA (RA_d), WA and SA from the telemetry (post-warm-up missions). For each mix, the knee is the setting that minimises R/R_min + W/W_min + S/S_min (feasibility §8.1). When runs will use the propagation law (`--rl_law=true`, M8), also include the law's geometry for that mix as a candidate setting, computed offline with the learner's solver.

**Output:** `{"geometry":{...}, "io_mode":{"direct_reads","direct_io_flush_compaction","wal_enabled"}, "grid":{...}, "entries":[{"put","seek","R","W","S","setting"}]}`.

Sweeping only K × level 1 is the reachable-knee approximation (D9); see §19.

### 16.2 A/B harness (`tools/rl_controller/ab_harness.py`)

**Arms:**

- **A:** stock build.
- **B:** patched, `--rl_mode=off`.
- **C:** patched, `--rl_mode=frozen_native`.

All arms use identical flags, including `--perf_level=2` and `--statistics=1`, and the same seeds.

**Procedure:**

1. Measure A-vs-A noise with at least 5 paired seeds, separately for each I/O mode used; margins from one mode never apply to the other, and only paper-mode margins support claims.
2. Fix an equivalence margin per metric (RA, WA, SA, throughput, p99 Get, p99 Put) at no less than the A-vs-A spread.
3. B and C pass when the confidence interval of the mean paired difference lies inside the margin for RA, WA and SA.

C's latency difference from A is the instrumentation overhead. **C is the B1 baseline.**

### 16.3 Stub learner (`tools/rl_controller/stub_learner.py`)

- Acknowledges `hello`.
- Answers every `mission` with a `params` packet containing μ = 0 and Σ = 0 for every arm, and λ = 1 for every phase.
- Logs everything.

---

## 17. Milestones

| M | Work | Done when |
|---|---|---|
| M0 | Read-only inventory: pinned version; the §18 checklist; search the fork for earlier RL code (`rlsuspend`, `rlresume`, scoring or picking hooks) and plan its removal or isolation | `PATCH_NOTES.md` complete |
| M1 | §4 option | T1, T2, T9-validation; existing option suites |
| M2 | §5 scoring | T3–T6; `version_set_test`, `compaction_picker_test`, `db_compaction_test` |
| M3 | §6 supervisor (interface, call site, `ScoreSupervisorImpl` with integrals, counters, runtime bounds) | T7, T8; T3 with Observe |
| M4 | Controller plumbing: recorder (sub-windows, early cut), listener, §8.3 reads, §8.4 derived quantities, telemetry, channel, §13 flags and checks, `--level_target_multipliers`, `--mix_schedule`, `frozen_native` mode, stub learner | C1, C3, C10, C12, I-1 (with a baseline-only actor) |
| M5 | §9 phase detector | C2, I-2 |
| M6 | §10 actor (prior, value, states, masks, exploration, backstop, headroom) in `learn` and `greedy` against the stub | C4–C9, C11, I-1, I-3 |
| M7 | §11.1–11.6 learner | P1–P6; I-1 against the real learner |
| M8 | §11.7 law (optional) | P7; a law on/off smoke run |
| M9 | §16.1 knee sweep and §16.2 A/B harness | Produces the knee table and the A/B table on a short configuration; I-4 |

---

## 18. M0 checklist (pinned version)

For each item record: confirmed / differs (how) / adaptation.

1. `ComputeCompactionScore`:
   - the leveled static L0 score is max(files/k, bytes/`max_bytes_for_level_base`);
   - levels ≥ 1 are scored as bytes-not-compacting / `MaxBytesForLevel`;
   - `EstimateCompactionBytesNeeded` is called at the end.
2. `SetOptions`: whether it appends a Version (with or without a MANIFEST write), recomputes scores, installs a SuperVersion, schedules work, and writes the OPTIONS file.
3. `SetFinalized()` asserts non-decreasing targets.
4. `GetBGJobLimits` gives exactly one compaction slot with `max_background_jobs=2`, including under speed-up.
5. Registration of `max_bytes_for_level_multiplier_additional`; support for `OptionTypeInfo::Vector<double>`; the vector separator.
6. The pattern for a non-serialized `shared_ptr` column-family option and its handling in `options_settable_test`.
7. The column-family validation function run by both `DB::Open` and `SetOptions`.
8. `PerfContextByLevel` fields. **That the per-level counters are updated by point lookups only.** That `db_bench` thread setup enables per-level perf context.
9. Tickers: `NUMBER_KEYS_READ`, `NUMBER_KEYS_WRITTEN`, `NUMBER_DB_SEEK`, `BYTES_WRITTEN`, `FLUSH_WRITE_BYTES`, `COMPACT_WRITE_BYTES`, `MEMTABLE_HIT`, `STALL_MICROS`, `NO_FILE_OPENS`, `BLOCK_CACHE_DATA_MISS`, `BLOCK_CACHE_DATA_HIT`. `kCFStats` key names and units for Rn, Rnp1, Moved, Write, KeyIn, KeyDrop.
10. Properties: `rocksdb.estimate-pending-compaction-bytes`, `rocksdb.total-sst-files-size`, `rocksdb.estimate-live-data-size`, `rocksdb.num-files-at-level0`.
11. The picker tries levels in decreasing score order and compacts the first with score ≥ 1 whose inputs are free.
12. `EventListener` callbacks: flush completed, compaction begin and completed, stall conditions changed.
13. How `db_bench` decides `mixgraph`'s total operation count (for `--rl_run_ops`), and where op types are drawn (for `--mix_schedule`).
14. The `db_bench` flags that set the I/O mode (`--use_direct_reads`, `--use_direct_io_for_flush_and_compaction`, `--disable_wal`), so telemetry and the knee-table check read the mode from the options actually in force.

---

## 19. Open items

- **Knee coverage.** The reachable knee sweeps K × level 1 only. At T=2, with more candidate levels, it is an approximation.
- **Feasibility doc amendments** V1, V2, V4 and V5 (§2.5) were made in Revision 4.
- **L0 byte-term multiplier.** Not implemented (D3).
- **Ageing (S3).** Implemented but off by default.
- **Multi-epoch horizon.** Out of scope (§11.4).
- **Multi-client runs.** Would need the operation clock moved to RocksDB tickers and per-thread perf aggregation.

---

## Appendix A — Symbol table

| Symbol | Meaning | Defined in |
|---|---|---|
| k, k₀, k_eff, ke(·) | L0 trigger, its baseline, effective trigger, effective-trigger function | §2.3 D3, §10.1, §10.4 |
| m_j, m_base,j, x, a | multiplier, its baseline, absolute arm value, arm factor | §4, §10.1 |
| m_min,j, m_max,j, ε_i | multiplier bounds, expansion allowance | §10.8 |
| C_j, S̄_j, n̄₀, f₀, L_d | native target, average level size, average L0 files, flush file size, deepest non-empty level | §8.4 |
| in_j, out_j, δ_j, drop_j, κ̂ | merge flows, density factor, drop rate, rewrite-gap tail | §8.4 |
| h_j, reach_j, pass_j, p_j, q_j, mem | hit share, reach, pass share, false-positive rate, miss rate, memtable share | §8.4 |
| RA, WA, SA, stall | global metrics per mission | §8.4 |
| RA_p, WA_p | phase aggregates | §10.3 |
| R*, W*, S*, β, s_max | knee values, SLO slack, stall cap | §10.3, §16.1 |
| π_p, w_R, w_W, λ_R,p, λ_W,p | priority, weights, prices | §10.3, §11.5 |
| y^R_b, y^W_b, ȳ^c_b | measured components, window average | §10.10, §10.4 |
| f^c_b, P^c_b, σ^c_P | model prediction, anchored prior, prior uncertainty | §10.4 |
| φ, μ^c_{b,a}, Σ^c_{b,a} | features, correction mean and covariance | §10.5, §11.2 |
| Q, V, Φ | value, variance, standard normal cumulative distribution | §10.5 |
| σ^c_b, σ_b, Δ_b, N_req, D_rem, M_rem, E_b | noise, composite noise, predicted gap, required and remaining decisions, epoch length | §10.2, §10.6 |
| Δ_min, ε_b, z | switch margin, exploration probability, promotion multiplier | §13.1 |
| μ_c, σ_c, S±_c, H, Δ | CUSUM mean, noise, statistics, threshold, shift | §9 |
| n_sw, W_est, μ̃ | sub-window length, estimation window, clipped mean | §9.2 |
| Gets, Puts, Seeks, U | client operation counts, user bytes written | §8.4 |
| Rn_j, Rnp1_j, Moved_j, Write_j, KeyIn_j, KeyDrop_j | compaction statistics for output level j | §8.3 |
| in_i,pm, out_i,pm | in_i and out_i per mission over the window | §10.2, §10.7 |
| x_cur, k_cur | multiplier and trigger in force during the window's last mission | §8.4, §10.4 |
| S_i(x), out(x), S_nx(x), S_up, deep, ratio(x), dh(x), nbar0(x), pass0 | prior intermediates | §10.4 |
| J_b | branch cost | §10.3 |
| E_new | epoch length the candidate arm would get | §10.7 |
| y1^c_b, s_c, g, Λ, bvec, gap | learner prior scale, forgetting factor, precision, precision-weighted sum, constraint gap | §11.2, §11.5 |

## Appendix B — Message examples

```json
{"type":"hello","schema":2,"run_id":"r-001","git_commit":"abc123","mode":"learn",
 "num_levels":13,"L_d":4,
 "branches":{"K":{"arms":[2,4,8],"baseline":1},
             "L1":{"arms":[0.5,0.75,1,1.5,2],"baseline":2},
             "L2":{"arms":[0.5,0.75,1,1.5,2],"baseline":2},
             "L3":{"arms":[0.5,0.75,1,1.5,2],"baseline":2}},
 "feature_names":["bias","put_share","seek_share","drop","full_own","full_next","reach","miss","recency"],
 "native_targets":[0,16777216,167772160,1677721600,16777216000],
 "knee_table":{"io_mode":{"direct_reads":true,"direct_io_flush_compaction":true,"wal_enabled":true},
               "entries":[{"put":0.159,"seek":0.035,"R":1.10,"W":14.2,"S":1.30,"setting":{"k":4,"m1":1.5}}]},
 "learner_cfg":{"beta":0.10,"resid_rel_sd":0.05,"forget":0.98,"price_step":0.5,"price_max":10,"law":false}}

{"type":"hello_ack","schema":2,"learner_version":"0.1"}

{"type":"transition","schema":2,"branch":"L1","epoch_id":12,"state":"Learning","arm":3,
 "x":1.5,"baseline":1.0,"phase_id":2,"priority":"read",
 "phi":[1,0.159,0.035,0.08,0.97,1.02,0.97,0.99,1.0],
 "P_R":0.0412,"P_W":14.61,"y_R":0.0405,"y_W":14.48,"sigma_R":0.0011,"sigma_W":0.21,
 "explored":true,"excluded":false,"reason":null,"missions":2,"op_start":1200000,"op_end":1300000}

{"type":"params","schema":2,"policy_version":57,"based_on_mission":57,
 "branches":{"L1":{"arms":{"3":{"R":{"mu":[0,0,0,0,0,0,0,0,0],"cov":[[...9x9...]]},
                               "W":{"mu":[...],"cov":[[...]]}}}}},
 "prices":{"2":{"lambda_R":1.0,"lambda_W":1.35}}}
```

These examples are illustrative, not literal. `native_targets` is shortened to 5 of its `num_levels` entries, and `[...]` marks elided arrays; real messages carry every entry.

In the `hello` example, K lists only [2, 4, 8] because, with base 16 MiB and f₀ ≈ 2 MiB, k = 16 merges into k_eff = 8 (E10). L-branch arms are factors a.