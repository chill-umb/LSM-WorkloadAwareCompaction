# Learning Topics: Everything This Codebase Invented

**Purpose.** You know LSM trees. You do not know RocksDB internals or anything
this project added on top. This file is the complete inventory of *new* concepts,
mechanisms and vocabulary in this repository — the things you will not find in a
textbook or in upstream RocksDB docs.

**How to use it.** Pick a topic by its number. Ask for it by name. Each one gets
explained from first principles, with the actual code it lives in.

Topics are ordered so earlier ones support later ones. Reading order ≠ importance
order — the **Start here** group below is the minimum to understand any
conversation about this project.

> **Start here (the irreducible six):** 1.1, 1.2, 2.1, 3.1, 4.1, 6.1

---

## Part 0 — RocksDB background you're assumed not to have

These are not this project's inventions, but every later topic depends on them.

| # | Topic | One-line hook |
| --- | --- | --- |
| 0.1 | **Leveled compaction and the level score** | How RocksDB decides a level is "due": `score = bytes / target_bytes`, and score ≥ 1 means eligible. |
| 0.2 | **L0 is special** | Overlapping files, counted by *file number* not bytes, with three separate thresholds (trigger/slowdown/stop = 4/20/36 here). |
| 0.3 | **`CompactionPri` and `kMinOverlappingRatio`** | Which file inside a due level gets picked. Pinned to enum 3 here and never swept — deliberately outside the research variable. |
| 0.4 | **The DB mutex** | RocksDB's single global lock. The reason half this project's architecture exists. |
| 0.5 | **`db_bench`, `mixgraph`, `filluniquerandom`** | The benchmark tool that generates the workload; `waitforcompaction` and `levelstats` phases. |
| 0.6 | **Trivial move** | A compaction that just renames a file to the next level instead of rewriting it. Distorts every byte-based metric if not excluded. |
| 0.7 | **`level_compaction_dynamic_level_bytes`** | RocksDB's default-on adaptive level sizing, **pinned off** here (assumption A5). Changes the whole level ladder. |
| 0.8 | **Compensated size** | RocksDB inflating a file's apparent size when it holds many tombstones, to make it more attractive to compact. |

---

## Part 1 — The core research framing

| # | Topic | One-line hook |
| --- | --- | --- |
| 1.1 | **Trigger-only control** | The project's central invariant: RL says *when* and *which level*, never *which file*. Why this boundary is the whole design. |
| 1.2 | **The four amplification metrics, as defined here** | W, R, S and scan amp — with this project's *operational* definitions, which differ from the usual textbook ones. |
| 1.3 | **Logical vs physical read amplification** | Why a Bloom-filter rejection still counts as a probe, and why cache misses were rejected as the metric. |
| 1.4 | **Protocol v1 / v2 / v3** | The three generations of the C++↔Python contract. v3 (exact-file picking) was built, measured, and deleted. Understand why. |
| 1.5 | **The arms: `regular`, `oracle`, `prior_only`, `rl`, `unconstrained_rl`** | Five experimental conditions. Each isolates exactly one variable. |
| 1.6 | **The constrained objective (the 2026-09-05 amendment)** | From "improve all three amplifications" to "minimise R subject to W-parity". Why the first was proven infeasible. |
| 1.7 | **Preregistration, and why criteria are never amended after failure** | The C-2 and E-1 precedent. A methodological rule that has twice cost this project a passing grade on purpose. |
| 1.8 | **The research objective contract (`v3.json`)** | A frozen, hash-pinned JSON file that machine-encodes the acceptance rules. Why it is frozen and edited in place. |

---

## Part 2 — The C++ side (`lib/rocksdb/db/compaction/`)

| # | Topic | One-line hook |
| --- | --- | --- |
| 2.1 | **`RLCompactionPicker` and the worker-thread architecture** | The core class. Publishes a snapshot under the mutex, consumes a *previously computed* answer. Nothing slow under the lock. |
| 2.2 | **The allowed-source-level mask** | How a binary "compact" becomes real work without naming a file: a mask handed into RocksDB's native leveled builder. |
| 2.3 | **Held due gates vs one-shot optional tokens** | `PermitMode`: `kClosed`/`kDueOpen`/`kOptionalOpen`/`kForcedOpen`. A due gate serves *many* compactions; an optional token serves exactly one. |
| 2.4 | **`ActionReason` — the ten bypass reasons** | `kPolicy`, `kBudget`, `kMaintenance`, `kEmergency`, `kFallback`, `kDrain`, `kSLO`, `kManifest`, `kStaleStructure`, `kPosture`. Attribution is the whole point. |
| 2.5 | **`ControlState`: bootstrap / active / fallback** | Why "haven't started yet" had to be split from "control failed" — and the one hard-invalid frame that forced it. |
| 2.6 | **Generations: decision, eligibility, structural, retry** | Four separate monotone counters. How an old response is prevented from mutating a newer one's outcome. |
| 2.7 | **`RLStructuralSnapshot` and the stale-structure deadline** | An immutable copy of tree shape, with a 250 ms dirty deadline. What happens when it goes stale. |
| 2.8 | **`RLControlCoordinator`** | The DB-owned async coordinator: coalesced refreshes, scheduling/retry wakes, two-phase detach. Why it can't carry its queue lock into the DB mutex. |
| 2.9 | **`CompactionPressureObserver` and pressure episodes** | Event-driven score tracking with zero-order hold. Emits "episodes" — the raw material every calibration is built from. |
| 2.10 | **`FlushOpenEpisodes` and truncated episodes (defect D2)** | Episodes were only emitted on due→healthy, silently deleting the exact upper tail the calibration needed. |
| 2.11 | **`RLCompactionTelemetry`** | Rolling windows for flush/compaction/stall/latency, plus the `ForegroundOperation` split (Get/Scan/Write). |
| 2.12 | **`RLLatencyHistogram`** | In-process latency buckets used by the live guard — deliberately *not* sent over the socket. |
| 2.13 | **`RLStateV2` — the wire state** | Every field C++ sends Python, and why `interval_micros` is the most important one in the struct. |
| 2.14 | **`RLRewardInvalidReason` — the hard-invalid bitmask** | Five bits that mean "reward attribution is genuinely unavailable". Distinct from a known override. |
| 2.15 | **The `kPosture` reason and D3a (the crossing fix)** | A `defer` chosen while a level was *below* threshold expresses no opinion about a due level. Worth 511 µs vs 33 ms. |
| 2.16 | **The ε (epsilon) admission-latency decomposition** | Four instrumented terms from score-event publication to scheduler admission. |
| 2.17 | **Capacity expansion in `PrepareForVersionAppend`** | Pathway A's actuator: per-level capacity scales `s_i`, L0 and final level pinned. Controller vector beats static vector. |
| 2.18 | **Gate-0 instrumentation: `compaction_release` and `merge_schema_version`** | Two new event-log records that made η and φ measurable. |

---

## Part 3 — The Python agent (`rl_agent/`)

| # | Topic | One-line hook |
| --- | --- | --- |
| 3.1 | **Physics-informed prior + learned residual** | `Q = analytic_prior + learned_residual`, residual initialised to *exactly zero*. Cold start ≡ hand-written heuristic. |
| 3.2 | **`analytic_advantage` — the prior's four terms** | urgency, read exposure × run relief, merge work, premature-overlap penalty. Where each number comes from. |
| 3.3 | **`read_exposure`** | Why a write-only interval is structurally forbidden from claiming read relief. |
| 3.4 | **`MultiHeadDQN` and the shared trunk** | One encoder, one two-action head per level. Pools scarce samples without erasing L0-vs-deep differences. |
| 3.5 | **The cooperative whole-tree reward** | Every level in a frame gets the *same* reward. Why per-level potentials manufactured phantom relief. |
| 3.6 | **`Φ` (the potential) and SMDP shaping** | `γ(dt)·Φ(next) − Φ(now)`, with real elapsed time. The reward-scale disaster of 2026-08-06. |
| 3.7 | **Decision-density invariance** | Why rate costs must be integrated over `dt` — polling faster must not change total return. |
| 3.8 | **Credit-assignment schema v2** | `decision_id` echo, accept/reject, override relabelling, the 4-second horizon. The fix for the zero-gradient bug. |
| 3.9 | **Hard attribution boundaries and terminal truncation** | When credit *must* stop, and why the valid prefix is stored as terminal rather than discarded. |
| 3.10 | **`_AdaptiveScales` and `_RunningStandardizer`** | Online normalisation with a freeze point. Why freezing matters for a cold-start agent. |
| 3.11 | **Boltzmann exploration annealed on wall time (defect D6)** | The dead constant `0.11 decisions/kop` and why annealing moved to seconds. |
| 3.12 | **`AgentPool`, replay, Double DQN, Polyak targets** | The standard RL machinery, and the non-standard sizing (warmup 32, batch 32) forced by short runs. |
| 3.13 | **`learning_health.json` and the accounting identities** | `response_acks × levels == accepted_decisions`. An exact integer equality between two processes. |
| 3.14 | **Reading learner health correctly** | `residual_scale` vs `max_abs_residual_advantage` — the field that produced a false 500× divergence reading. |
| 3.15 | **The `RL_*` environment-variable configuration surface** | ~85 variables. There is no config file; the pipeline *is* the config. |

---

## Part 4 — The safety system ("the shield" / "the guard")

| # | Topic | One-line hook |
| --- | --- | --- |
| 4.1 | **`baseline_slo.json` — the SLO manifest** | Measured envelopes from a tuned baseline. Contains no learned weights — this is what keeps the "cold start" rule honest. |
| 4.2 | **`RLSafetyController` and the four breach classes** | space / read / write / simultaneous. Each maps to a *trigger-only* response. |
| 4.3 | **Rolling windows, minimum samples, three-window hysteresis** | Why a guard needs memory and a dead-band. |
| 4.4 | **The force condition is a disjunction** | due age **OR** integrated pressure **OR** score **OR** debt. The reason a partial recalibration handed 100% of the rate to one untouched term. |
| 4.5 | **`guard_ready` and the unscored window** | 28–33% of each run where the guard forces ~100% of frames and the validator doesn't look. Currently an open defect. |
| 4.6 | **Distribution-free upper tolerance bounds** | Order statistics instead of interpolated quantiles. Coverage, confidence, and achieved rank. |
| 4.7 | **Censored (truncated) episodes** | A truncated episode is a *lower bound*. Pooling it biases the limit in the same direction as the defect being fixed. |
| 4.8 | **The episode unit vs the frame unit** | A tolerance bound over episode durations answers a different question than the one the picker asks every 50 ms. |
| 4.9 | **Shadow classification and `would_override_frame`** | Measuring what the guard *would* do without perturbing the run. |
| 4.10 | **Guard calibration → holdout protocol** | Fit on one set of seeds, score on another. The 18× transfer gap this exposed. |
| 4.11 | **The plateau problem** | Debt has no tail: p50 = 4.849, p90 through max all = 4.899. Why no threshold placed on it can be stable. |
| 4.12 | **Bootstrap caps vs calibrated limits** | What the system does when it has no valid measurement, and why `calibrated: false` quietly wrecked the earlier matrix. |

---

## Part 5 — The experiment pipeline (`scripts/dbbench_pipeline/`)

| # | Topic | One-line hook |
| --- | --- | --- |
| 5.1 | **The numbered stage architecture (00–16)** | Why every repeated operation becomes a numbered stage and never a pasted heredoc. |
| 5.2 | **The experiment fingerprint** | A single string encoding workload + geometry. `fullmatch`-anchored parser; must stay in lockstep with stage 06. |
| 5.3 | **Paired seeds, alternating arm order, distinct policy seeds** | The pairing discipline, and what each element defends against. |
| 5.4 | **The tuned baseline sweep and preregistered selection (stage 05/06)** | Minimum-space → 2% band → lowest runtime → tie-breaks. Selection never inspects RL. |
| 5.5 | **The oracle parity gate (stage 09)** | A deterministic "perfect trigger" arm proving the *bridge* is faithful before any learning is tested. |
| 5.6 | **`pipeline_stats.py`: `ci95`, `required_pairs`, `envelope_verdict`** | One statistical instrument shared by the engineering gate and the research criterion, on purpose. |
| 5.7 | **Invariant vs envelope criteria (defect D4)** | Why `all(delta <= 0)` over repeats is a broken test form, and what replaced it. |
| 5.8 | **`insufficient_pairs` / `undecidable` as first-class verdicts** | A wide confidence interval is not a failure. Reporting it as one is a lie. |
| 5.9 | **Non-inferiority vs "CI contains zero"** | A CI containing zero can pass by lack of power. A-2 is written to forbid that. |
| 5.10 | **The drain phase (`waitforcompaction`)** | Settling debt before measuring, so a deferring policy can't hide unfinished work. |
| 5.11 | **The space-amplification denominator problem** | `estimate-live-data-size` swings 4.8% on identical data. Affects every space number, including the frozen constraint. |
| 5.12 | **`frontier_analysis.py` and the Pareto hull** | Hull₀ vs Hull-s, and why comparing a policy to a class denied its knob is invalid in both directions. |
| 5.13 | **`compaction_measurements.py`: η and φ_j** | Merge survival and release-time occupancy. The `JSONWriter` boolean trap that made every compaction read as failed. |
| 5.14 | **Learner preflight and staged verification (stage 13)** | 1M → 5M → 10M health gates before spending a lease on a matrix. |
| 5.15 | **Stress suites (stage 08)** | Read-heavy / write-heavy as *safety* tests, separately calibrated, with weaker acceptance. |
| 5.16 | **Resume, `COMPLETED` markers, manifest SHA-256 binding** | Why resume is keyed to the exact manifest hash and not just geometry. |

---

## Part 6 — The PATHWAYS programme (`docs/PATHWAYS.md`)

| # | Topic | One-line hook |
| --- | --- | --- |
| 6.1 | **The six pathways A–F** | Capacity co-design, workload realism, frontier comparator, constrained objective, shield symmetry, dynamic SLO. |
| 6.2 | **The PATHWAYS notation** | `s_i`, `φ_i`, `κ`, `η_i`, `f_i`, `S_flow`, `g_flow`, `D_depth`, `D_eager`, `Θ`, `π`. |
| 6.3 | **Write-accounting models M1 / M2 / M3** | Three literature conventions that do not share optima. This paper uses M3. |
| 6.4 | **Standing assumptions A1–A5** | Including A3′ (L0's byte-sensitive score branch) and A4 (one sorted run per level, with five exceptions). |
| 6.5 | **Theorem A.2 — fanout conservation** | Why no expansion profile beats the uniform tree at fixed depth. The proof that killed the original objective. |
| 6.6 | **Theorem B.1 — the elision ceiling** | Write savings below parity are capped by resident garbage, and this workload has almost none. |
| 6.7 | **Proposition C.1 — the hull as comparator** | Why one tuned baseline is not a defensible comparator. |
| 6.8 | **Corollary A.4 — M3's optimum differs** | The accounting convention is not cosmetic. |
| 6.9 | **A-0: the D_depth / D_eager decomposition** | Splitting excess writes into "wrote at new levels" vs "wrote more at existing levels". Bounds what Pathway A can possibly fix. |
| 6.10 | **The gate ladder: Gate 0 → 6** | Execution order designed to fail cheaply. Which gates need the node and which don't. |
| 6.11 | **The acceptance criteria A-1..A-5, B-1..B-5, C-1..C-6, D-1..D-5, E-1..E-5** | The full grid the paper is judged against. |
| 6.12 | **Budget rungs {0, 2, 5, 10}% and the (cell, rung) selection rule** | How Gate 3b decides what to actually run. |
| 6.13 | **The Lagrangian reward (Pathway D)** | Turning hard constraints into λ-weighted penalties, and what a diverging λ *proves*. |
| 6.14 | **The space hinge penalty** | Finding 3's fix: space should cost nothing until it exceeds the bound. |
| 6.15 | **Pathway F — phase-aware objective switching** | Programme 2. Per-phase vs whole-run objectives, the hindsight oracle, and the adaptivity gap `G`. |

---

## Part 7 — Project-specific methodology and hazards

| # | Topic | One-line hook |
| --- | --- | --- |
| 7.1 | **"A completed run is not evidence the policy acted"** | The 81-run invalid sweep. The origin of every attribution mechanism in the codebase. |
| 7.2 | **The catalogue of measurement defects** | Scan parser (240×), zero pending-byte limits (74×), orphan processes, grep-based "parity" checks. |
| 7.3 | **Direct I/O pinned off** | 2,750 vs 58,332 ops/s measured on the node — a 21× penalty, and a retracted cross-machine claim. |
| 7.4 | **Why this repo has no test suite** | Deliberate. Correctness is established by gates, not unit tests. Includes the two tests deleted rather than "fixed". |
| 7.5 | **The `docs/` gitignore hazard** | Current authority files exist only on disk and would not survive a clean checkout. |
| 7.6 | **Submodule discipline** | All C++ lives in `lib/rocksdb` (fork `chill-umb/rocksdb`, branch `rl-compaction-policy-new`); commit inside first, then bump the pointer. |
| 7.7 | **Known instrument traps** | `JSONWriter` has no bool overload; `QueryDecider::Initiate` appends without clearing; `repeat-NN/` only exists when `REPEATS > 1`. |
| 7.8 | **Node discipline** | Builds only on Chameleon; SIGBUS builds must be discarded, not resumed; lease budgets drive repeat counts. |

---

## Suggested learning paths

**"I just want to follow a conversation about this project"**
→ 1.1, 1.2, 1.5, 1.6, 3.1, 4.1

**"I want to understand the C++ control path"**
→ 0.1, 0.4, 2.1, 2.2, 2.3, 2.4, 2.6, 2.9

**"I want to understand the learning"**
→ 3.1, 3.2, 3.5, 3.6, 3.8, 3.14

**"I want to understand why results are trusted or rejected"**
→ 1.7, 5.3, 5.6, 5.7, 5.8, 5.9, 7.1, 7.2

**"I want to work on the current blocker (E-1)"**
→ 4.1, 4.4, 4.6, 4.7, 4.8, 4.10, 4.11, 4.5

**"I want to work on Gate 2 implementation"**
→ 2.17, 6.1, 6.5, 6.9, 6.13, 6.14, 7.7
