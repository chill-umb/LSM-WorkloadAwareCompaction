# LSM Workload-Aware Compaction: Complete Project Description, History, and Status

**Document status:** consolidated project record  
**Repository state inspected:** 2026-08-13 (Asia/Dhaka)  
**Scope:** root repository, modified RocksDB submodule, Python RL controller,
workload tooling, experiment pipelines, project-authored Markdown documents,
the two bundled research papers, and the relevant upstream RocksDB and Tectonic
documentation

This document explains what the project is, why it exists, how the current
system works, how the implementation evolved, what was fixed, what was added,
which measurements are trustworthy, and what remains incomplete. It is intended
to be the first document a new contributor reads and the historical record used
to interpret the older documents in `docs/`.

The repository contains several generations of design documentation. Those
documents accurately describe the system at the time they were written, but a
statement such as "the RL controller only controls L0" is no longer true of the
current protocol-v3 implementation. This record therefore uses the following
labels:

- **Current:** implemented in the working tree inspected on 2026-08-13.
- **Historical:** true of an earlier implementation or experiment.
- **Superseded:** deliberately replaced by a later design.
- **Proposed:** a research direction or implementation plan, not current code.
- **Unvalidated:** code exists, but the required end-to-end experiment has not
  established the claimed research outcome.
- **Invalidated:** a result was contaminated by a known defect and must not be
  used as evidence.

## 1. Executive description

This is a research system for **online, workload-aware compaction control in
RocksDB**. RocksDB is an LSM-tree storage engine: new writes first enter memory,
are flushed into sorted-string table (SST) files, and are later merged through
increasingly large disk levels. Those merges, called compactions, reclaim stale
versions and tombstones and reduce the number of sorted runs reads must inspect,
but they also consume background bandwidth and rewrite data.

Ordinary leveled RocksDB decides when and what to compact using static
thresholds, per-level scores, and a configured file-priority heuristic. This
project asks whether a controller that learns online from the actual workload
and tree state can choose better compaction opportunities. The desired outcome
is not merely lower runtime or fewer compactions. The formal objective is to
reduce all of the following without buying one improvement through an
unacceptable regression in another:

- write amplification;
- logical point-read amplification;
- scan work amplification;
- physical space amplification;
- Get, scan, and aggregate-write average and p95 latency;
- write-stall duration.

The present design is a **candidate-aware, physics-informed, parametric-action
DQN**. It starts cold, with no pretrained weights or checkpoint. RocksDB exposes
up to eight real, pickable SST candidates per level, including the exact
clean-cut expansion and next-level overlap RocksDB would use. The controller may
defer or select one exact candidate. A zero-initialized learned residual begins
on top of an analytic LSM cost prior, so the first decision is useful without
claiming any offline training. A versioned, single-use action lease binds the
decision to the tree snapshot and file number. If that candidate is stale or
blocked, RocksDB fails the action closed rather than silently compacting a
different file or level.

The system retains RocksDB's compaction correctness machinery. The RL policy
does **not** implement merge semantics, key-version dropping, output file
formation, snapshots, range-deletion handling, or background job execution.
RocksDB still owns those responsibilities.

The project currently has two experiment surfaces:

1. the repository's Tectonic-generated workload runner, which emits the most
   complete custom metric JSON; and
2. a clean, numbered `db_bench` pipeline for 10M, 20M, 30M, 40M, and 50M total
   operation workloads at size ratios `T=2`, `T=6`, and `T=10`, for both regular
   RocksDB and the RL compaction style.

The candidate-aware code and targeted tests exist. The final preregistered
candidate-aware evaluation does **not** yet exist: there is no current script
that performs the full tuned leveled grid, produces a workload-specific
`baseline_slo.json`, and runs at least ten paired repeats with confidence
interval acceptance checks. Consequently, the current controller is an
implemented research prototype, not yet an experimentally accepted final
policy.

## 2. The underlying LSM-tree problem

### 2.1 How the tree grows

An LSM tree buffers updates in a memtable. A full memtable is sorted and flushed
to an immutable SST. SSTs are organized into levels. Level 0 (L0) is special:
its files may overlap arbitrarily in key space, so a lookup can need to check
multiple L0 runs. Deeper leveled levels generally maintain non-overlapping files
within a level, but a compaction from level `i` must merge a source file and its
clean-cut source expansion with every overlapping file in level `i+1`.

The size ratio `T`, exposed as `max_bytes_for_level_multiplier` in RocksDB and
as `-T`/`--size_ratio` in the wrapper, controls the geometric capacity growth
between levels. `T` is independent of the L0 file-count thresholds in the
current wrapper. Historical commands coupled them; compatibility defaults still
derive the thresholds from `T` when the new flags are omitted.

The independently controllable wrapper options are:

- `--l0_compaction_trigger`: L0 file count that makes compaction due; historical
  compatibility default is `T`;
- `--l0_slowdown_trigger`: count that begins slowing writes; compatibility
  default is `T - 1`;
- `--l0_stop_trigger`: count that stops writes; compatibility default is `T`;
- `-T`/`--size_ratio`: capacity ratio for the leveled tree.

The scaled `db_bench` pipeline uses RocksDB's direct flag names and deliberately
sets the L0 trigger, slowdown, and stop thresholds to `4`, `20`, and `36` for
every value of `T`.

### 2.2 Why timing and file choice matter

Compacting early can reduce the run count and remove obsolete data, improving
reads and space. It can also rewrite data before enough garbage or useful
overlap has accumulated, increasing write amplification. Compacting late can
save write bandwidth in the short term, but retain more runs, expand debt,
increase lookup/scan work, and eventually stall writes. File choice matters
because two files at the same level can have very different overlap, deletion
density, compensated size, and projected effect on the output level.

This is why the current project no longer treats compaction as a level-only
binary decision. The candidate is part of the action.

### 2.3 The four amplification metrics

The current metric definitions are intentionally operational rather than vague:

| Metric | Definition | Why this definition is used |
| --- | --- | --- |
| Write amplification | `(flush bytes + compaction bytes written) / user logical bytes written` | Counts all SST construction work attributable to user writes. |
| Point-read amplification | logical SST/table probes per point read, including a probe rejected by a Bloom filter | Measures the logical run-search burden, not only physical cache misses. |
| Scan amplification | `(returned entries + internal entries skipped) / returned entries` | Prices iterator work hidden behind the returned result set. Empty-result scans report zero rather than divide by zero. |
| Space amplification | settled total SST bytes / live logical key-and-value bytes | Compares physical tree occupancy with the current live database contents. |

Sorted-run seeks per scan are reported separately. Physical file reads and
cache misses remain useful diagnostics, but they are not the logical read
amplification objective because the block cache and Bloom filters can change
physical I/O without changing the number of logical runs exposed by the tree.

## 3. Research objective, constraints, and non-goals

### 3.1 Formal objective

The formal comparator is a **preregistered, workload-specific tuned leveled
baseline**, not the historical `T=10` configuration and not whichever regular
arm happens to look best after seeing RL results. Baseline selection is supposed
to occur before inspecting candidate-aware results:

1. sweep independently controlled size ratio, L0 thresholds, and compaction
   priority;
2. find the minimum-space leveled configuration;
3. discard configurations whose space amplification is more than 2% above that
   minimum;
4. choose the lowest mean-runtime survivor;
5. treat runtime differences below 1% as a tie, broken first by lower write
   amplification and then by lower scan amplification;
6. export the selected settings and measured envelopes as
   `baseline_slo.json`.

The final balanced-workload acceptance criteria are:

- at least ten paired repeats with distinct seeds and alternating arm order;
- the 95% confidence interval for RL-minus-baseline point-read amplification is
  strictly below zero;
- the 95% confidence interval for RL-minus-baseline scan amplification is
  strictly below zero;
- the 95% confidence interval for RL-minus-baseline write amplification is
  strictly below zero;
- the upper 95% confidence bound on relative space regression is no more than
  2%;
- the upper 95% confidence bound on average and p95 latency regression is no
  more than 2% for Get, scan, and aggregate writes;
- stall duration does not increase.

Read-heavy and write-heavy stress suites are safety tests. They must meet the
same 2% space and latency bounds and introduce no new stalls; amplification does
not have to improve on both stress workloads.

### 3.2 Hard online-learning constraint

The policy must start from scratch for every experimental run. It may use a
baseline SLO manifest containing measurements, limits, definitions, and selected
RocksDB options, but that manifest is not a model and contains no learned
weights. The analytic prior is code derived from LSM mechanics, not a trained
checkpoint. Learned residual heads are initialized to zero.

Historical documents discuss checkpoint persistence and one roadmap considered
offline-RL pretraining. Those are not part of the current formal experiment.

### 3.3 What remains RocksDB's responsibility

The current controller can select a source SST and timing. It does not replace:

- RocksDB's snapshot/sequence-number correctness;
- clean-cut expansion across files sharing user-key boundaries;
- overlap discovery;
- conflict detection with running compactions;
- compaction input iteration and merge semantics;
- tombstone/version elision rules;
- output level choice for the selected leveled candidate;
- output SST creation and background scheduling;
- explicit maintenance such as manual, marked, periodic, TTL, or drain work.

### 3.4 Structural ideas that are not implemented

The `Refined spec Claude.md` plan and the RusKey paper motivate an FLSM tree
with multiple variable-sized runs per level and bounded horizontal expansion.
The Vertiorizon paper motivates adaptive combinations of vertical and horizontal
growth. This repository currently modifies RocksDB's leveled picker; it does not
implement an FLSM read path, a general horizontal-tiering substrate, or
Vertiorizon. Forecast-conditioned scheduling, an offline-pretrained policy,
certified/theorem-backed shielding, and a multi-agent bandwidth auction remain
research proposals.

## 4. Research lineage

Two papers are bundled in `docs/` and explain the broader research context.

### 4.1 RusKey and FLSM

“Learning to Optimize LSM-trees: Towards a Reinforcement Learning Based
Key-Value Store for Dynamic Workloads” presents RusKey. Its key ideas are online
RL for dynamic workloads, an FLSM structure that makes transitions among
compaction policies cheaper, and a level-based learning strategy supplemented
with white-box analysis to reduce the sample requirement.

This project adopts the online-adaptation motivation but takes a different
engineering path. Instead of first replacing RocksDB's level structure with
FLSM, it incrementally gives an external learner authority over RocksDB's real
leveled candidate picker. The physics-informed prior similarly combines known
LSM structure with learned residual behavior, but it is not a reimplementation
of RusKey's full system.

### 4.2 Vertiorizon and growth schemes

“How to Grow an LSM-tree? Towards Bridging the Gap Between Theory and Practice”
distinguishes vertical growth—fixed level capacities and a growing number of
levels—from horizontal growth—a fixed number of levels whose capacities grow.
It identifies read/write trade-off limitations in vertical growth and space
limitations in traditional horizontal leveling, proposes horizontal-tiering,
and combines the two directions in Vertiorizon.

This paper influenced the longer-term bounded-horizontal-expansion plan. It does
not describe code currently present in the repository.

### 4.3 Relevant RocksDB policy foundation

The regular comparator uses RocksDB's leveled compaction. The current scaled
pipeline sets direct `db_bench --compaction_pri=3`, which is RocksDB enum
`kMinOverlappingRatio`. RocksDB's own documentation describes this priority as
friendly to write amplification; compensated size also makes files containing
many tombstones more attractive. Candidate-aware protocol v3 exposes the
regular RocksDB priority rank rather than discarding this mature heuristic.

## 5. Repository and runtime architecture

### 5.1 Components

The project has five cooperating layers:

```text
workload source
  ├── Tectonic JSON specification -> generated operation file
  └── db_bench internal seeded generators
             |
             v
root wrapper (db_runner) or modified db_bench
             |
             v
modified RocksDB / RLCompactionPicker
  ├── telemetry and candidate preview
  ├── versioned action lease and exact-file validation
  └── normal RocksDB compaction execution
             |
        Unix socket, newline-delimited JSON
             |
             v
Python server
  ├── protocol-v2 compatibility processor
  └── protocol-v3 CandidateController
       ├── analytic prior
       ├── parametric residual DQN
       ├── replay/training
       └── optional SLO safety mask
             |
             v
metrics, raw logs, policy logs, summaries, and graphs
```

The important directories are:

- `include/` and `src/`: the C++ workload wrapper, option parsing, listeners,
  measurement, and experiment JSON;
- `lib/rocksdb/`: the modified RocksDB checkout containing the picker, client,
  telemetry, candidate preview, statistics, `db_bench`, and C++ tests;
- `rl_agent/`: v2 and v3 Python state, reward, model, replay, server, logging,
  and tests;
- `lib/tectonic/`: the Rust workload generator and its upstream usage docs;
- `workload_specs/`: current Tectonic specifications and the recorded workload
  construction lessons;
- `scripts/dbbench_pipeline/`: the current clean, numbered scaled experiment
  workflow;
- `docs/`: historical specifications, audits, roadmaps, system guides, protocol
  documentation, and research papers.

### 5.2 The database mutex constraint

Early versions queried Python synchronously from `NeedsCompaction()` while
RocksDB held its DB mutex. A socket delay therefore blocked unrelated database
progress and made policy overhead indistinguishable from storage behavior. The
current picker owns a worker thread. Under the mutex, the picker publishes a
snapshot and consumes a previously computed response; the socket round-trip,
JSON handling, and Python inference happen off the DB mutex.

Observation cadence and actuation cadence are explicit. Protocol messages carry
`interval_micros`, allowing rates and SMDP discounting to use real elapsed time
instead of assuming every decision is separated by a nominal fixed interval.

### 5.3 Current protocol-v3 observation

One request contains global tree/foreground measurements, per-level state, and
up to eight previewed candidates for each pickable level. The candidate fields
include:

- snapshot epoch and source file number;
- source and output level;
- source bytes and expanded clean-cut bytes;
- exact expanded source file numbers;
- exact next-level overlap file numbers and bytes;
- estimated total read and write bytes;
- overlap ratio;
- entry, deletion, and compensated-size statistics;
- projected source and output fullness;
- whether the source level becomes empty;
- RocksDB priority rank;
- current compaction-conflict status.

Global and level state also carry physical/live bytes, logical read and scan
work, foreground latency summaries and sample counts, pending debt, stall time,
due/default state, deferral count, and attribution for the previous scheduling
and completed compaction outcome.

The snapshot epoch is a structural hash of the version's levels, files, sizes,
and conflict state. Protocol-v3 parsing preserves it as an integer. This matters
because an earlier Python conversion through a floating-point value could round
large 64-bit epochs, making every otherwise valid action appear stale.

### 5.4 Non-mutating candidate preview

`LevelCompactionPicker::PreviewCompactionCandidates` evaluates real candidate
files without registering or scheduling a compaction. It uses the same clean-cut
expansion and overlap rules as the exact picker and exposes conflicts. Preview
data is thus actionable, not an approximate level aggregate.

The corresponding C++ test constructs a same-user-key boundary that forces
clean-cut expansion, previews the candidate, performs the exact-file pick, and
checks that expanded source files, overlap files, source bytes, and overlap
bytes match. This verifies picker construction, although a full experimental
comparison of estimated bytes with bytes reported by completed background
compactions is still required.

### 5.5 Response and action lease

The response contains `decision_id`, the unchanged `snapshot_epoch`, and arrays
of per-level actions and candidate file numbers in request order. Protocol v3
permits at most one candidate compaction in a decision.

An affirmative action becomes a single-use `ActionLease` containing:

- decision ID;
- snapshot epoch;
- selected source file number;
- level and score;
- action/override reason.

The lease is consumed before validation and cannot actuate twice. It expires on
successful scheduling, validation failure, or the next actuation. At pick time,
RocksDB recomputes the epoch and validates the file identity and conflicts. A
stale or missing candidate returns no compaction and never retargets another
file.

### 5.6 Authority and bypass reasons

The superseded picker used global booleans such as `parent_pick_allowed_` and a
global `DeferralExhausted()` answer. That allowed permission granted for one
level to leak into the ordinary parent picker, which could select another level.

Current authority is level-scoped and attributed with one of six reasons:

| Code | Reason | Meaning |
| ---: | --- | --- |
| 0 | policy | Exact candidate selected by the controller. |
| 1 | budget | That level exhausted its permitted deferral budget. |
| 2 | maintenance | Manual, marked, periodic, TTL, blob-GC, or equivalent required maintenance. |
| 3 | emergency | A local hard safety condition requires progress. |
| 4 | fallback | The server is unavailable or the response cannot be used. |
| 5 | drain | End-of-run debt settlement. |

Budget, emergency, and fallback are routed through a specific forced
level/candidate path. Only explicit maintenance and drain work may use the
ordinary parent picker. Cumulative fallback counters remain visible, and any
transition affected by fallback, a stale action, a scheduling failure, or
unattributable execution is excluded from replay.

### 5.7 Completion attribution

RocksDB stores decision ID, snapshot epoch, source file, and override reason on
the `Compaction` object. Per-level state in the next observation reports the
previous scheduling result and completion result, including the completed
decision/file identity. This separates four facts that older code conflated:

1. what Python requested;
2. what the picker attempted;
3. whether scheduling succeeded;
4. what compaction actually completed.

## 6. The current learning system

### 6.1 Parametric action space

Protocol v1 used a fixed two-action L0 network. Protocol v2 used per-level
`defer`/`compact` outputs but still let RocksDB choose the file. Protocol v3
represents every real SST candidate as an action and represents deferral as a
pseudo-candidate.

The current v3 tensors use:

- 24 global/state features;
- 18 features per candidate;
- up to 8 candidates per level, plus the defer slot;
- validity masks for padding, missing levels, file conflicts, and safety rules.

Replay entries store the chosen candidate feature vector and the next complete
valid candidate set, not merely a fixed action index. This is necessary because
file identities and the number of candidates change between observations.

### 6.2 Network structure

`ParametricCandidateDQN` has a shared state encoder, a shared candidate encoder,
and separate scoring heads for each level. There is no globally shared action
head. This allows levels to share statistical strength while retaining
different L0 and deep-level semantics. Tests confirm that backpropagating
through one level's head does not update another level's head.

The final residual layers are initialized to zero. Therefore, at step zero:

```text
Q(state, candidate) = analytic_prior(state, candidate)
                    + learned_residual(state, candidate)
                    = analytic_prior(state, candidate)
```

The v2 implementation also retains its shared-trunk/per-level-head model and
independent-network ablation path for protocol compatibility.

### 6.3 Physics-informed prior

The analytic prior estimates immediate candidate value from exact I/O and tree
effects. Its current terms favor:

- deletion/garbage reclamation;
- removal of a sorted run, especially emptying the source level;
- relief of source-level pressure;
- RocksDB's established priority rank.

It penalizes:

- estimated read and write bytes;
- overlap ratio;
- projected output-level overfullness or downstream tree growth.

This prior exists because an online storage run produces few expensive samples
and LSM compaction is not a physics-free action space. The learned residual is
responsible for correcting model mismatch, nonlinear interactions, cache/CPU
effects, and workload-specific behavior.

### 6.4 Global reward

Older designs assigned an independent potential to each source level. That
could reward moving bytes out of one level while ignoring the same bytes added
to its output level, or lose the benefit when a drained level disappeared from
the next message. Protocol v3 uses one global transition reward for the tree:

```text
reward =
  - delta(total_tree_cost)
  - integrated_write_amp_cost
  - integrated_point_probe_cost
  - integrated_scan_work_cost
  - latency_budget_cost
```

Potential shaping uses the transition-specific elapsed time:

```text
gamma(dt) * Phi(next_tree) - Phi(current_tree)
```

The tree cost includes measured logical point probes, scan internal work and
sorted-run seeks, physical/live space, pending debt, and stall duration. Empty
levels remain represented as zero state so removing the last run receives
credit. Tests assert that merely moving the same bytes between levels cannot
manufacture tree relief and that draining a source level receives run-removal
credit.

### 6.5 Deterministic SLO safety mask

If enabled with a valid workload-specific `baseline_slo.json`, the controller
tracks rolling latency/space windows with a minimum sample count and
three-window hysteresis. The 102% envelopes mean:

- **Space breach:** due levels may not defer further; choose the valid candidate
  with the highest garbage reclamation per projected write byte.
- **Read average/p95 breach:** prohibit deferrals that retain an extra run and
  favor the greatest measured read relief per byte.
- **Write average/p95 breach:** prohibit optional below-threshold compactions
  and cap projected candidate I/O.
- **Simultaneous read and write breach, or no valid exact candidate:** run the
  tuned leveled action for the specifically responsible level.

The mask is an online risk reducer, not a mathematical latency guarantee. Final
paired confidence intervals remain authoritative.

The new scaled `db_bench` pipeline defaults `RL_SAFETY_MASK=0` because it does
not generate an SLO manifest. Enabling the mask requires both
`RL_SAFETY_MASK=1` and an existing `RL_BASELINE_SLO_PATH`.

## 7. Measurement and observability added to the project

### 7.1 Wrapper experiment output

`src/run_workload.cc` now emits:

- separate insert, update, delete, range-delete, Get, and scan count, average,
  p95, and diagnostic p99 latency;
- combined write and read latency summaries;
- measured and executed operation counts, warmup count, measured time, and
  total wall time;
- user logical write bytes;
- flush and compaction read/write bytes;
- point SST probes;
- scan returned entries, internal skips, empty scans, and sorted-run seeks;
- settled SST bytes, live logical bytes, and live entry count;
- write, point-read, scan, and space amplification using the definitions in
  section 2.3;
- stall events, stop events, RocksDB stall microseconds, and listener-measured
  stall duration;
- selected size ratio, all three independent L0 thresholds, and compaction
  priority;
- drain duration, pending bytes before/after drain, and drain compaction bytes;
- embedded textual metric definitions.

Warmup operations execute and may train the controller but are excluded from
the measured foreground summaries. Final amplification accounts for drained
compaction work so a policy cannot look efficient merely by ending with
unsettled debt.

### 7.2 RocksDB statistics added

The RocksDB checkout adds or wires statistics for logical point SST probes,
iterator internal skips, sorted-run seeks, and stall time. Bloom-rejected probes
still count as logical probes. The block-based iterator records the scan-side
work needed for amplification calculations.

### 7.3 Raw artifacts and attribution logs

A valid experiment should preserve each arm and repeat in a distinct directory,
including:

- full command line and effective configuration;
- workload seed and independently varied policy seed;
- root and RocksDB git revisions;
- environment information;
- RocksDB `LOG`;
- raw workload/`db_bench` log;
- LSM and I/O traces;
- policy decisions, Q/prior/residual values, losses, and fallback state;
- final metrics and completion marker.

The scaled pipeline implements most of this per-arm artifact separation. It does
not currently run repeats.

## 8. Full project timeline

The commit log is sparse and contains several generic WIP messages, so this
timeline combines commit dates, document dates, file history, and the explicit
rework changelogs. Working-tree work after the current Git `HEAD` is labeled as
such.

### 2024: wrapper foundation

- **2024-03-17:** the repository was initialized around the RocksDB wrapper.
- **2024-10-24:** submodule/project structure and argument parsing were repaired
  and the README was updated.
- **2024-11-08:** RocksDB statistics output was added for `--stat 1`.

At this stage the project was a workload/measurement wrapper, not an RL
compaction controller.

### 2025: maintenance and research substrate

- **2025-03-01:** a new initial/maintenance sequence and minor follow-ups were
  committed. The repository continued to provide the wrapper and Tectonic-based
  workload substrate.

### February 2026: wrapper update

- **2026-02-14:** the wrapper was updated with a minor follow-up. This is the
  immediate code lineage on which the RL work was built.

### June 2026: L0 proof of concept

- **2026-06-03:** the RL setup and DQN prototype were introduced. RocksDB gained
  an RL compaction style and wrapper integration.
- **2026-06-04 to 2026-06-10:** the RocksDB side added the initial RL picker,
  telemetry/state transport, safety guardrails, and Python-side reward. Missing
  RocksDB files and submodule pointers were corrected.
- **2026-06-08:** the L0 technical specification recorded the eight essential
  gaps: effective forcing, explicit fallback, correct feature semantics,
  telemetry, reward, socket robustness, asynchronous training, and required
  metrics.
- **2026-06-10:** the L0 change summary described the first working end-to-end
  online system: `kCompactionStyleRL`, a functioning force-L0 path, Python
  reward/normalization, the removal of the artificial delay action, warmup-aware
  metrics, and paired comparison tooling.
- **2026-06-11:** the first experiment runner was added.
- **2026-06-16:** the technical overview documented the then-current L0-only
  architecture and early 1M/5M observations.

Important early fix: the first `compact_now` action only made
`NeedsCompaction()` return true and then delegated to the normal leveled picker.
If the normal score was below threshold, no L0 compaction was scheduled. The
force-L0 exact level path made the action real.

### July 2026: validity audit, attribution, and multi-level control

- **2026-07-01 to 2026-07-15:** experiment scripts were reorganized, build
  support and plotting/sweep tooling expanded, and the L0 agent structure was
  documented.
- **2026-07-15:** the PoC validity review identified delayed reward attribution
  as the highest-risk learning defect, followed by limited L0 semantics,
  training stability, and insufficient seed discipline.
- **2026-07-16:** a plan was written for n-step/windowed reward attribution and
  Double DQN. Multi-level agents, the `--db` flag, and parallel experiment
  support were added.
- **2026-07-19:** an end-to-end fault was fixed where RL was not being triggered.
  The multi-level architecture and physics-informed analytic-prior-plus-residual
  design were introduced.

The multi-level transition created protocol v2: one batched observation with
one level state per eligible level and one binary decision per level. Each level
had its own semantics/head, and the C++ side arbitrated affirmative decisions.

### July 2026: invalid parallel sweep and protocol lesson

The first large parallel sweep did not actually evaluate the intended policy.
Python emitted pretty/whitespace-separated JSON that the original C++ parser did
not tolerate. The picker silently fell back to leveled behavior. **All 81 runs
in that sweep are invalid as RL evidence.**

The fix was two-sided: Python emitted compact JSON, the C++ parser became
whitespace tolerant, and fallback/availability state became visible. The larger
lesson was that policy evaluation must first prove control-path attribution; a
successful workload exit is not evidence that RL acted.

### 2026-08-01 to 2026-08-04: control-flow and MDP rework

The rework audit catalogued 23 defects. The principal changes were:

- moving the socket/inference path off the DB mutex;
- giving the controller actual authority to defer a due level;
- bounding deferral, with a much tighter L0 budget than deep levels;
- preventing ordinary parent-picker fallthrough after a deferral;
- distinguishing selected from executed actions;
- recording parent/bypass compactions separately;
- adding fixed observation intervals and real `interval_micros`;
- adding point and scan telemetry;
- moving to a 23-feature v2 state without knowingly dead deep-level inputs;
- using time-discounted potential reward and wall-clock credit windows;
- adding a shared trunk with per-level heads;
- adding Boltzmann exploration, Polyak target updates, and a zero-residual
  analytic prior;
- repairing terminal and reappearing-level lifecycle behavior;
- draining compaction debt before close;
- expanding unit, socket, and deterministic end-to-end instrumentation.

The online sample budget was recalibrated for real runs: replay warmup was
reduced from 200 to 32, batch size from 64 to 32, exploration decay from roughly
1000 to 60 decisions, normalizer freeze from 200 to 50 samples, training work
was increased, and credit moved from a short decision count to an approximately
4-second wall-clock horizon. These values describe the v2 rework and remain
available for the v2 ablation; v3 has its own parametric replay path.

### 2026-08-02: workload and storage-configuration corrections

Two non-RL defects dominated earlier measurements:

1. **Scan parser bug.** The wrapper consumed only the first character of the
   Tectonic `SC...` record, leaving `C` to be parsed as the start key. Scans
   traversed roughly one-third of the database instead of their intended short
   range. An observed average of about 48,913 iterator `Next` calls per scan and
   approximately 4.03 ms per scan fell to about 16.3 microseconds for an intended
   50-entry scan after the parser accepted the complete range/bound. This is an
   approximately 240x correction. **All scan results produced by the broken
   parser are invalid.**
2. **Pending-compaction limit bug.** Setting RocksDB's soft/hard pending-byte
   limits to zero does not disable the limits; it creates immediate/permanent
   pressure. One observed configuration made inserts about 74x slower and total
   runtime about 7x worse. Experiments now use explicit large limits where the
   intent is to avoid this confound.

Workload construction also established that balance must be measured by elapsed
time, not operation count. In the valid 5M balanced workload, the approximate
time shares were 38.9% inserts, 24.6% scans, 17.0% updates, 10.7% Gets, and 8.9%
deletes. Empty Gets are deliberately retained because they exercise Bloom
filters and the read path differently from successful Gets.

### 2026-08-03 to 2026-08-05: experiment hygiene and tick-latency investigation

- Runs were alternated by arm order and repeated with paired seeds.
- A preflight check for orphan workload/server processes was added after stray
  processes contaminated five measurements.
- Bootstrap confidence intervals and per-pair differences replaced conclusions
  drawn from one run.
- A nonexistent DB parent path that previously reached an assertion/segfault in
  release builds was changed to fail with a clear error.
- A sticky force flag was built to stop a response from disappearing before
  actuation, but was marked unvalidated.
- Attempts to reduce tick latency exposed a global parent-unlock authority leak.
  A scoped variant was built and measured, but the tick-latency feature was
  disabled because it did not safely establish the intended semantics.

The 2026-08-05 context document attributed bimodal outcomes mainly to sample
budget/convergence after ruling out several machinery hypotheses. That diagnosis
was useful but incomplete and was superseded by the 2026-08-06 reward-scale
analysis.

### 2026-08-06: reward-scale diagnosis and stabilized v2 learner

The later system guide found that the observed return magnitude (about 9.46 in
one audit) was far larger than the analytic-prior scale (about 0.284). Rate-like
costs were summed once per decision, so changing decision density changed total
reward. Standardization then magnified the mismatch and TD values diverged.

The correction integrated rate terms over real `dt`, put reward and prior on a
compatible scale, and fixed discounting. Mean return moved to roughly 0.198 and
was nearly flat (within about 9%) across the examined decision densities. Median
TD error was reported around 0.0024 and p95 around 0.011 in the post-diagnosis
run. A second fix priced the marginal candidate-like work of one compaction
rather than treating `compact` as if it rewrote an entire level. Read features
that had collapsed to constants were replaced with signals having level- and
workload-dependent gradients.

The revised v2 system removed the earlier bimodal failure and saved compaction
work in 9 of 10 measured pairs. It still did not satisfy the research objective
because read amplification, scan latency, space, and/or runtime regressed.

### 2026-08-12: candidate-aware protocol v3

The current working tree introduced the major candidate-aware redesign:

- protocol-v3 candidate observations and epoch-bound responses;
- non-mutating clean-cut candidate preview;
- exact-file picking with no silent retargeting;
- 64-bit-safe snapshot-epoch parsing;
- versioned exactly-once action leases;
- explicit per-level policy, budget, maintenance, emergency, fallback, and
  drain reasons;
- completion attribution on the compaction object;
- cumulative fallback counters and replay exclusion;
- 24-state/18-candidate parametric DQN with masks and per-level heads;
- exact-candidate analytic prior;
- one global tree reward;
- an SLO-manifest safety mask with sample thresholds and hysteresis;
- metric-formula, model, mask, reward-conservation, socket, and C++ picker
  tests.

This work is present in the inspected working tree but is newer than the root
repository's current committed `HEAD`.

### 2026-08-13: clean scaled db_bench workflow

The previous collection of overlapping shell/Python scripts was removed from
the working tree and replaced for the current scaled use case by:

1. `00_install_dependencies.sh`;
2. `01_build_rocksdb.sh`;
3. `02_build_db_bench.sh`;
4. `03_run_experiments.sh`;
5. `04_generate_graphs.sh` plus `04_generate_graphs.py`;
6. one `config.sh` and a concise pipeline README.

This workflow uses `db_bench`'s internal generators, so it has no separate
workload-generation script.

## 9. Defect and fix catalogue

The following table consolidates the failure modes documented across the
technical spec, validity review, multi-level design, rework changelog, session
context, system guide, workload notes, and current code.

| Area | Failure or risk | Correction | Current status |
| --- | --- | --- | --- |
| L0 actuation | `compact_now` only woke the scheduler; the normal picker could still choose nothing. | Force a pick from the authorized level/file path. | Fixed; superseded by exact-file leases. |
| Fallback | Server failure could be silent, making a leveled run look like RL. | Explicit availability/fallback result, logging, cumulative counter, invalid replay transition. | Fixed. |
| Protocol JSON | Whitespace in Python output broke the C++ parser and invalidated 81 runs. | Compact output plus tolerant parsing and compatibility socket tests. | Fixed; old runs invalidated. |
| Epoch identity | Converting a 64-bit epoch through float rounded it and made responses stale. | Exact integer parsing and echoing. | Fixed in v3. |
| DB mutex | Socket/inference under the mutex blocked RocksDB and confounded runtime. | Snapshot/worker architecture; socket work off mutex. | Fixed. |
| Deferral authority | Due levels could compact through normal policy despite a defer action. | Explicit bounded consent/deferral authority. | Fixed. |
| Cross-level authority | A global parent unlock for one level could schedule a different level. | Per-level reason and forced path; parent only for maintenance/drain. | Fixed in v3; targeted C++ test exists. |
| Exhaustion | Global `DeferralExhausted()` could authorize unrelated work. | Exhausted level receives its own budget lease/path. | Fixed and tested. |
| Sticky actions | Boolean force state could survive too long, vanish too early, or be reused. | Versioned single-use lease consumed before validation. | Fixed and tested. |
| Stale candidate | A missing/blocked file might be silently replaced by another pick. | Exact epoch/file validation and fail-closed result. | Fixed and tested. |
| Decision attribution | Requested action was confused with executed/scheduled action. | Record request, scheduling result, completion result, decision, epoch, file, reason. | Fixed. |
| Safety override replay | Samples were labeled with the selected action even when a guard executed another action. | Key transition to executed action; v3 excludes invalid overrides/fallbacks. | Fixed. |
| Parent compactions | Maintenance work could be attributed to the learner. | Explicit bypass reasons and transition-valid bit. | Fixed. |
| Interval semantics | Counter deltas covered variable time but were used as if fixed-rate samples. | Carry `interval_micros`; calculate rates and gamma from real elapsed time. | Fixed. |
| Decision-density reward | Rate costs were summed per decision, so faster polling changed return. | Integrate over `dt`; test return invariance. | Fixed. |
| Reward/prior scale | Reward dominated the analytic prior and destabilized TD learning. | Rescale/integrate reward; monitor return/prior/TD distributions. | Fixed for v2 and reflected in v3 design. |
| Source-only reward | Moving bytes into the next level manufactured apparent relief. | One global tree cost charges source relief and output growth. | Fixed and tested in v3. |
| Empty level credit | A drained level disappeared and received no run-removal benefit. | Preserve zero states/global tree representation; explicit run-removal prior. | Fixed and tested. |
| Terminal reward | A zero-filled terminal message produced a phantom reward near `+2.109`. | Terminal state finalizes pending credit without manufacturing state relief. | Fixed and tested. |
| Reappearing level | A level that emptied and returned used the wrong time gap/discount. | Preserve real gap and SMDP discount. | Fixed and tested. |
| End-of-run debt | A run could finish before its compactions, making delayed policy look cheap. | Drain pending work and account for drain bytes/time. | Fixed in wrapper; `db_bench` uses `waitforcompaction` and a separate post-measurement full-compaction reference. |
| Credit horizon | Fixed n-step counts represented wildly different wall time. | Wall-clock credit horizon; terminal flush. | Fixed in v2; v3 uses direct candidate transition attribution. |
| Sample budget | Replay/training/exploration thresholds exceeded decisions available in short workloads. | Recalibrate warmup, batch, exploration, normalization, and training cadence. | Fixed for short v2 runs; each new workload scale still needs budget auditing. |
| Shared learning | Independent deep agents saw too few samples. | Shared trunk with separate level heads; independent ablation retained. | Fixed in v2; v3 uses shared encoders/separate heads. |
| Head isolation | A global action head could erase level-specific behavior. | Separate per-level scoring heads. | Fixed and tested in v3. |
| Cold start | A random residual could override known LSM behavior before learning. | Zero-initialize residual so step-zero policy equals analytic prior. | Fixed and tested. |
| Whole-level action cost | Prior priced one compaction as rewriting the complete level. | Use marginal overlap/I/O; v3 uses exact candidate estimates. | Fixed. |
| Dead read features | Several read terms were constant or zero for deep levels. | Logical probe/scan telemetry and depth/fullness gradients; feature-audit tests. | Fixed in code; final workload sensitivity remains to be evaluated. |
| Physical read proxy | Cache misses were mistaken for logical read amplification. | Count logical probes and scan work; retain physical reads only as diagnostics. | Fixed. |
| Scan parser | `SC` left `C` as start key and scanned a large fraction of the DB. | Parse full scan length/end bound correctly. | Fixed 2026-08-02; pre-fix scan results invalid. |
| Tectonic scan length | `scan_length` expression could panic. | Use `selectivity`, with the caveat that it scales with dataset size. | Workload-authoring rule. |
| Tectonic deletes | Deletes before their keys exist or in a separate incompatible phase are invalid. | Put `point_deletes` in a later group of the same populated section. | Workload-authoring rule. |
| Tectonic Zipf Gets | A Zipf point-query phase without populated keyspace is invalid. | Populate the same section/keyspace first. | Workload-authoring rule. |
| Tectonic naming | Generator allow-list/name inference depends on path and operation mix. | Use recognized locations/names and verify generated output. | Workload-authoring rule. |
| Pending limits | Zero soft/hard limits caused permanent pressure instead of disabling it. | Use explicit high limits when the experiment intends no pending-byte cap. | Fixed in current configs. |
| DB path | Missing DB parent could reach release-mode assertion/segfault. | Validate/create parent and emit explicit error. | Fixed. |
| Orphan processes | Five runs were contaminated by leftover workers/servers. | Preflight process check and deliberate concurrency override. | Fixed in current pipeline. |
| Arm order | Thermal/cache/order effects could masquerade as policy effects. | Pair workload seeds and alternate arm order. | Implemented; repeated protocol still required. |
| Single-run claims | One lucky seed was treated as a result. | At least ten paired repeats and confidence intervals. | Required but not implemented by current scaled pipeline. |
| Build parity check | A grep-based parity check was incorrectly treated as proof of equivalent binaries/options. | Record commands/options/revisions and perform behavioral/control-path checks. | Methodology correction. |
| Script sprawl | Multiple overlapping runners and plotters caused confusion and unsafe reuse. | Replace the current use case with a numbered `db_bench` pipeline. | Current working-tree cleanup. |

## 10. Experimental record and interpretation

### 10.1 Early L0 observations

The 2026-06 technical overview reported that the L0 policy sometimes improved
foreground write/Get/runtime/scan measurements on 1M and 5M workloads while
performing more L0 compactions and writing more compaction bytes. These results
proved the end-to-end path could act and learn, but did not establish a better
policy. Some early scan measurements also predate the parser fix and cannot be
used quantitatively.

### 10.2 Invalid parallel sweep

The 81-run multi-level sweep affected by the protocol parser/fallback defect is
invalid. It may be useful only as a postmortem demonstrating why fallback
attribution is required.

### 10.3 Valid v2 balanced-workload comparison

The 2026-08-06 system guide records a valid ten-pair v2 comparison on the
corrected 5M balanced workload. The reported means/differences were:

| Metric | Historical v2 result |
| --- | ---: |
| Baseline compaction bytes written | `715.01 ± 13.21 MB` |
| RL compaction bytes written | `639.97 MB` |
| Paired compaction-write difference | `-75.04 MB`, 95% CI `[-100.74, -46.04]` |
| Pairs with lower RL compaction write | `9 / 10` |
| RL compaction bytes read | `660.29 MB` |
| Paired compaction-read difference | `-85.88 MB` |
| Runtime difference | approximately `+10.02 s` for RL |
| Physical-space difference | approximately `+10.91 MB` for RL |
| L0 file-count difference | approximately `+0.8` |
| Get latency difference | approximately `+0.41` with CI crossing zero |
| Scan latency difference | approximately `+7.29` |
| Point probes | approximately `+18%` |
| Scan latency | approximately `+27%` |

The proper conclusion is narrow: v2 reliably saved compaction work after the
reward fixes, but purchased it with worse read behavior and other regressions.
It failed the project objective and motivated candidate-aware control, global
reward, and hard measured constraints.

### 10.4 Corrections and retractions

The rework changelog explicitly retracts or narrows several earlier narratives:

- a broken grep check did not establish configuration parity;
- five measurements were contaminated by an orphan process;
- degradation was over-attributed to the learner before workload/system defects
  were isolated;
- the analytic prior was incorrectly described as inherently deferral-biased;
- compaction savings were briefly called a policy win even though interval
  starvation was a confound;
- an estimated 5.6-second “RL machinery overhead” disappeared under direct
  instrumentation;
- the global parent-unlock leak was real but was not the main cause of the
  bimodality;
- a baseline tick artifact was considered, but reward scale and convergence
  were the better-supported explanation.

These corrections are part of the project record and should not be removed from
future summaries.

### 10.5 Candidate-aware results

There are no accepted protocol-v3 amplification/latency results in the current
documentation or working tree. Code completion and unit tests must not be
reported as performance success.

## 11. Current db_bench experiment pipeline

### 11.1 Matrix and workload

The default matrix is:

- total operations: `10M`, `20M`, `30M`, `40M`, `50M`;
- size ratios: `T=2`, `T=6`, `T=10`;
- arms: regular leveled RocksDB and RL protocol v3;
- total arms: `5 × 3 × 2 = 30`.

`db_bench` generates the database and workload internally. Each arm runs:

```text
filluniquerandom -> mixgraph -> waitforcompaction -> levelstats -> stats
```

The operation allocation approximates the corrected 5M balanced workload:

- 29% initial unique inserts;
- 71% mixed phase;
- mixed Get ratio `0.5211267606`;
- mixed Put ratio `0.1549295775`;
- mixed seek/scan ratio `0.3239436620`;
- fixed intended scan length `32`.

The scaled `db_bench` workload substitutes Put/update traffic for the Tectonic
workload's explicit deletes. It is therefore comparable in broad pressure mix,
not operation-identical. There is one run per arm by default, so the graphs are
descriptive and provide no confidence interval.

### 11.2 Current storage configuration

The pipeline defaults are:

| Setting | Value |
| --- | ---: |
| Key bytes | 64 |
| Value bytes | 960 |
| Write buffer | 2 MiB |
| Target SST | 512 KiB |
| L1 base capacity | 16 MiB |
| Levels | 13 |
| Background jobs | 2 |
| Block cache | 8 MiB |
| Bloom bits/key | 10 |
| Write-ahead log | Disabled in both arms |
| L0 compact/slow/stop | 4 / 20 / 36 |
| Compaction priority | direct RocksDB enum 3, `kMinOverlappingRatio` |
| Soft/hard pending-byte limits | 64 / 128 GiB |
| Threads | 1 |
| Workload seed | 1 |
| Protocol | 3 |
| L0/deep max deferral | 1 / 50 decisions |
| Decision/observation interval | 50 / 50 ms |
| Safety mask | off unless a valid SLO manifest is supplied |

Thirteen levels are used for every arm because `T=2` needs greater depth at the
50M scale; varying `T` must not silently vary maximum tree depth.

### 11.3 Pairing and output behavior

Regular and RL arms use the same `db_bench` seed. RL receives a distinct
deterministic policy seed for each size/`T` combination. Pair order alternates.
The script refuses to start unless the user explicitly sets
`CONFIRM_EXPERIMENTS=YES`, validates ratios and trigger ordering, checks for
stray `db_bench`/server processes, refuses unsafe DB roots, and does not overwrite
an existing result root. Resume skips only arms carrying a `COMPLETED` marker.

Each arm stores command, effective metadata, seeds, elapsed time, repository
revisions, raw run log, RocksDB logs, policy/server output for RL, and SST size
measurements. Database directories are removed after completion unless
`KEEP_DATABASES=1`.

After the measured phase, the script runs an explicit full compaction outside
the measurement window. The before/after SST sizes provide a garbage-free
physical reference for the pipeline's approximate space ratio; that full
compaction's I/O is not included in measured WAF. This ratio is useful for the
scaled graphs but is not identical to the wrapper's exact
`total SST bytes / live logical bytes` metric.

### 11.4 Graph output

The graph script parses RocksDB tickers and histograms and writes a summary CSV
plus figures covering:

- write amplification;
- point-read amplification;
- scan amplification and sorted-run seeks;
- approximate space amplification;
- stall seconds;
- Get, scan, and write average/p95 latency;
- elapsed runtime.

Because the default pipeline has no repeats, it must not be used to assert the
final 95% confidence criteria.

## 12. Tectonic workload system and lessons

Tectonic is a Rust workload generator embedded under `lib/tectonic`. A JSON spec
contains sections and ordered/concurrent groups, operation counts, key/value
expressions, and distributions. It can generate an operation file, execute
against supported databases, or benchmark generation. Supported operations
include inserts, updates/merges, point queries/deletes (including intentionally
empty variants), range queries/deletes, and sorted or mostly sorted inserts.

The current `workload_specs/README.md` is more than a syntax note; it preserves
constraints discovered through failed experiments:

- time balance matters more than operation-count balance;
- short-range scan output must be audited directly;
- `selectivity` is safer than the crashing `scan_length` expression but changes
  with dataset size;
- a delete phase must share a populated section/keyspace;
- Zipf point queries require a populated keyspace;
- the generator infers names from path and operation mix, so moving a spec can
  change allow-list behavior.

The wrapper accepts Tectonic operations, records warmup/measured phases, and can
produce the custom metrics unavailable from stock `db_bench`. The numbered
scaled pipeline intentionally avoids this generator to reduce operational
complexity for the current 10M–50M sweep.

## 13. Test and verification status

### 13.1 Tests present in the tree

Current Python tests cover:

- synthetic metric formulas, including Bloom outcomes, scans, writes/deletes,
  and empty scans;
- zero-initialized residuals and per-level head isolation;
- variable candidate counts, conflict masks, and at-most-one selection;
- stale/fallback replay exclusion;
- global reward conservation and level-drain credit;
- SLO minimum samples and three-window hysteresis;
- v2 analytic-prior physics, read-path signals, reward scaling, lifecycle,
  executed-action credit, SMDP discount, shared trunks, exploration, target
  updates, checkpoints, and evaluation mode;
- protocol v1/v2/v3 compatibility, response ordering, parser shape, reconnect,
  terminal credit, decision IDs, epoch binding, and single v3 action.

The RocksDB compaction picker test file contains eight targeted current tests:

1. candidate preview matches exact source/overlap files and bytes;
2. exact-file picking does not retarget a missing candidate;
3. cross-level lease failure grants no authority elsewhere;
4. exhausted deferral uses the responsible level's forced path;
5. a stale epoch fails closed and expires the lease;
6. maintenance bypass is explicitly attributed;
7. unavailable-server fallback is level-scoped;
8. a policy lease actuates exactly once.

### 13.2 Verification performed while writing this document

On 2026-08-13:

- all shell files in `scripts/dbbench_pipeline/` passed `bash -n`;
- the graph script and `rl_agent/*.py` passed Python bytecode compilation;
- 77 Python metric/model/reward/controller tests passed under the repository's
  existing `.venv` with PyTorch 2.12.0;
- the nine socket tests could not be executed in the restricted documentation
  environment because binding a Unix socket returned `EPERM`;
- the system Python lacked PyTorch, while the project virtual environment had
  it;
- the C++ picker tests were inspected but not rebuilt/executed during this
  documentation task.

These environment limitations do not convert unrun tests into passes. A normal
development machine should run the socket suite and compiled picker tests after
building the current RocksDB checkout.

### 13.3 End-to-end proofs still required

The plan also calls for deterministic short runs proving that:

- one decision schedules no more than one candidate;
- no deferred level compacts through another level's authorization;
- every completed compaction maps to a decision or explicit bypass reason;
- reconnect works under the real C++/Python process pair;
- previewed I/O estimates match completed compaction files/bytes.

Unit tests cover the core mechanics, but current documentation does not contain
a fresh protocol-v3 end-to-end run report satisfying all five proofs.

## 14. Implementation status against the candidate-aware plan

| Planned deliverable | Status on 2026-08-13 | Notes |
| --- | --- | --- |
| Independent size ratio and L0 thresholds | **Implemented** | Wrapper flags and direct `db_bench` flags exist. |
| WAF, point RA, scan RA/seeks, space, latency avg/p95/p99, stall duration | **Implemented** | Exact wrapper JSON; scaled pipeline parses corresponding RocksDB statistics with the space caveat above. |
| Distinct raw arm/repeat directories, commands, revisions, seeds | **Partially implemented** | Per-arm scaled output exists; there is no repeat loop in that pipeline. |
| Tuned leveled grid and preregistered selection | **Not currently available** | README/protocol docs reference scripts removed during cleanup. |
| `baseline_slo.json` generator | **Not currently available** | Safety consumer exists, producer script is absent from the current scripts directory. |
| Per-level authority and explicit reasons | **Implemented** | Picker reasons and forced paths exist. |
| Versioned exactly-once action leases | **Implemented and unit-tested in C++ source** | Compiled test was not rerun for this document. |
| Candidate preview and exact-file picker | **Implemented and unit-tested in C++ source** | Completion-byte experimental validation remains. |
| Protocol v3 with v2 compatibility | **Implemented** | Server dispatches both generations. |
| Parametric DQN, masks, candidate replay, separate level heads | **Implemented and Python-tested** | Cold zero residual is tested. |
| Exact-candidate analytic prior | **Implemented** | Research calibration still requires experiments. |
| One global tree reward with time shaping | **Implemented and Python-tested** | Fallback/stale transitions are excluded. |
| SLO safety mask | **Implemented and Python-tested** | Inactive in default scaled runs because there is no generated SLO. |
| C++ picker authority tests | **Implemented in source** | Eight focused tests present. |
| Protocol/reconnect tests | **Implemented in source** | Nine current socket tests; not runnable in this restricted session. |
| Ten paired balanced repeats and formal CIs | **Not run for v3** | Required for acceptance. |
| Read-heavy/write-heavy safety suites | **Not run for v3** | Required for acceptance. |
| Full frontier and all ablations | **Not run** | Trigger-only, prior-only, unconstrained candidate-aware, and constrained learner results remain to be produced. |

## 15. Current limitations and next work

The immediate research work is not another model redesign. It is completing the
measurement gate around the implemented design:

1. restore or rewrite a simple baseline-grid runner and SLO selector compatible
   with the cleaned script layout;
2. generate a workload-specific tuned leveled frontier without examining v3
   outcomes;
3. export and validate `baseline_slo.json`;
4. add repeats, paired workload seeds, distinct policy seeds, alternating order,
   and bootstrap/paired confidence intervals to the current experiment path;
5. run short control-path attribution tests before expensive sweeps;
6. verify preview estimates against completed compactions in real logs;
7. run the balanced acceptance matrix, then read-heavy/write-heavy safety
   suites;
8. report the leveled frontier, corrected trigger-only v2, candidate prior-only,
   unconstrained v3, and constrained v3—not only the best-looking arm.

Other current caveats are:

- the scaled pipeline's default run is unconstrained because its safety mask is
  off;
- its `space_amplification` is a before/after-full-compaction proxy, not the
  wrapper's exact live-logical formula;
- it substitutes Put operations for explicit deletes;
- it has one workload seed and one run by default;
- the root README still references `scripts/manage.sh` and baseline/repeated
  scripts that are absent from the current cleaned working tree;
- the old deleted scripts remain visible only as Git history and compiled
  `__pycache__` remnants; bytecode files are not a supported experiment path;
- the repository and RocksDB submodule contain uncommitted working-tree changes,
  so every experiment must record both revisions and preferably a patch or clean
  commit identifying the exact code;
- candidate-aware performance is unvalidated even though the mechanics are
  implemented.

## 16. Guide to the existing documentation

All project-authored documents were read while producing this record. Use them
as follows.

| Document | What it contains | How to interpret it now |
| --- | --- | --- |
| `README.md` | Wrapper synopsis, option list, v3 and scaled-pipeline links. | Entry page, but some command references are stale after script cleanup. |
| `docs/rl_l0_compaction_technical_spec.md` | Original L0 acceptance/fix specification. | Historical requirements; most mechanics were implemented and later superseded. |
| `docs/rl_l0_compaction_change_summary.md` | First working L0 implementation and early observations. | Historical v1 record. |
| `docs/project_technical_overview.md` | Detailed June L0 architecture, files, parameters, artifacts, and early results. | Historical; its L0-only/non-file-selection scope is no longer current. |
| `docs/rl_agent_structure.md` | Original 14-feature, two-action DQN and protocol. | Historical v1 internals. |
| `docs/poc_validity_review.md` | Four major validity risks. | Historical audit whose concerns drove the rework. |
| `docs/l0_reward_attribution_plan.md` | Proposed n-step/Double-DQN attribution correction. | Historical plan; wall-clock and executed-action attribution later went further. |
| `docs/multilevel_rl_design.md` | Protocol v2, multi-level architecture, parser failure, and 2026-08-01 rework. | Authoritative for v2 mechanics, not current v3 actions. |
| `docs/physics_informed_rl_architecture.md` | Analytic prior plus learned residual rationale and equations. | Current design lineage; exact v3 candidate prior is newer. |
| `docs/research_overview_and_roadmap.md` | Research landscape, history through July, and six future directions. | Mix of history and proposals; candidate file selection is now partially implemented. |
| `docs/Refined spec Claude.md` | FLSM/bounded horizontal expansion phased plan. | Proposed long-term structural program, not present code. |
| `docs/REWORK_CHANGELOG.md` | Detailed Aug. 1–5 audit, fixes, measurements, and corrections. | Essential historical evidence; tick-latency and some conclusions are explicitly retracted/disabled. |
| `docs/context.md` | Aug. 5 session handoff and then-current diagnosis. | Historical snapshot superseded in part by the Aug. 6 reward diagnosis. |
| `docs/system_guide.md` | Most complete v2 system explanation, valid ten-pair result, and hard-won rules. | Primary v2 reference; protocol v3 supersedes its trigger-only scope. |
| `docs/candidate_aware_protocol_v3.md` | Current v3 wire contract, lease, model, reward, mask, and intended gate. | Current design reference; its baseline/repeat script names are absent after cleanup. |
| `workload_specs/README.md` | Balanced-workload timing and generator/parser pitfalls. | Current workload-authoring evidence. |
| `scripts/dbbench_pipeline/README.md` | Numbered 10M–50M workflow and caveats. | Current operational path for the scaled single-run sweep. |
| `lib/tectonic/README.md`, `lib/tectonic/USAGE.md` | Upstream Tectonic build, commands, spec grammar, expressions, and operations. | Generator reference. |
| bundled RusKey PDF | Online RL and FLSM motivation. | Research inspiration, not implementation documentation. |
| bundled Vertiorizon PDF | Vertical/horizontal growth analysis and hybrid design. | Longer-term research inspiration. |

The 112 documentation files under `lib/rocksdb` are vendored upstream RocksDB
manuals, release notes, and blog posts. They define the engine this project
modifies but are not a history of this research repository. Particularly
relevant upstream facts—leveled compaction, `CompactionPri`,
`kMinOverlappingRatio`, statistics, and `db_bench` flag semantics—were checked
against the current checkout rather than copied wholesale into the project
timeline.

## 17. Glossary

- **Action lease:** versioned, single-use authorization to actuate one exact
  candidate for one decision.
- **Analytic prior:** hand-derived LSM cost/value estimate added to the learned
  Q residual.
- **Candidate:** a source SST plus RocksDB's required clean-cut source expansion
  and next-level overlap.
- **Clean cut:** expansion needed so a compaction boundary does not split files
  sharing the same user-key boundary.
- **Compaction debt:** pending background work that has not yet been paid.
- **Compaction priority:** RocksDB heuristic ordering files within an eligible
  level; distinct from the level score and L0 triggers.
- **Deferral:** explicit decision not to compact a due/available level yet.
- **Drain:** end-of-run completion of outstanding background work so policies
  are compared at a settled state.
- **Epoch:** hash identifying the structural RocksDB snapshot on which a
  candidate decision was made.
- **FLSM:** flexible LSM design from RusKey allowing variable run arrangements
  for cheaper policy transitions; proposed, not implemented here.
- **L0:** overlapping first disk level, whose run count directly affects point
  and scan search work and write stalls.
- **Logical probe:** consideration of an SST/table by the read path, including a
  Bloom-filter rejection; independent of whether a physical disk read occurs.
- **Parametric action DQN:** network scoring action feature vectors, allowing a
  changing set of SST candidates rather than fixed action IDs.
- **Protocol v1:** historical L0-only state and binary action.
- **Protocol v2:** historical/current-ablation batched per-level binary
  compact/defer protocol, with RocksDB choosing the file.
- **Protocol v3:** current exact-candidate, epoch-bound protocol.
- **Residual:** learned correction `f_theta` added to prior `b`.
- **SLO manifest:** measurements, options, and allowed latency/space envelopes
  from a preregistered tuned leveled baseline; contains no learned weights.
- **SMDP discount:** elapsed-time-aware discount appropriate when transitions
  have unequal durations.
- **Sorted run:** independently searchable sorted sequence; L0 files are
  separate overlapping runs.
- **T:** size/capacity ratio between adjacent levels.
- **Tectonic:** bundled Rust generator for explicit operation traces.

## 18. Bottom line

The project has progressed through four distinct technical systems:

1. a RocksDB/Tectonic workload wrapper;
2. an L0-only binary DQN proof of concept;
3. a multi-level trigger/defer controller with physics-informed residual
   learning;
4. the current candidate-aware, exact-SST, constrained protocol-v3 controller.

The most important achievement is not a favorable benchmark number. It is that
the project repaired the measurement and control path sufficiently to know what
an RL decision actually did: the metrics now represent the intended
amplifications, the workload parser produces the intended scans, the controller
does not block under the DB mutex, authority is level-scoped, one response can
actuate only one epoch-bound candidate, fallbacks and maintenance are explicit,
and invalid transitions are not learned from.

The most important remaining fact is equally clear: protocol v3 has not yet
passed the formal repeated evaluation. The next credible milestone is a
preregistered tuned baseline plus repeated, paired, constrained candidate-aware
results satisfying the amplification, space, latency, and stall criteria above.
