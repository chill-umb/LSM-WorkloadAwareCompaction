# LSM Workload-Aware Compaction: Complete Project Description, History, and Status

**Document status:** consolidated project record

**Repository state inspected:** 2026-08-26 (Asia/Dhaka)

**Scope:** root repository, modified RocksDB submodule, Python RL controller,
workload tooling, experiment pipelines, project-authored Markdown documents,
the two bundled research papers, and the relevant upstream RocksDB and Tectonic
documentation

> **2026-09-05 status correction (read first; supersedes the 2026-08-26 block
> below).** **The learner now trains.** Credit-assignment schema v2 and the
> bootstrap/fallback state repair were verified on hardware at 1M, 5M and 10M,
> with the C++ and Python sides agreeing to the decision. That closes the
> blocker which had made every earlier arm labelled `rl` a synonym for the
> analytic prior. The first trained-learner matrix — 10M x T=2/6/10, five
> paired repeats, four arms — is recorded in Section 10.7. It **fails** the
> Section 3.1 criteria in every cell, and the blocker is **write
> amplification**: no arm improves it anywhere, at any size ratio. Two findings
> survive. First, the analytic prior is the strongest read policy and improves
> as T grows (-12.5%, -17.3%, -19.3% point-read amplification at T=2/6/10).
> Second, the learned residual defers top-of-tree compaction, deepens the tree
> by one to three levels, and trades part of that read advantage for space
> amplification that the criteria only require it not to regress. Sections
> 10.7, 14.5, 15 and 18 carry the detail. **The objective decision that failure
> forced has since been made and preregistered:** see the amendment at the head
> of Section 3.1, and `docs/PATHWAYS.md` for the proofs, gates and acceptance
> criteria that follow from it.
>
> **2026-08-26 status correction (read first).** The trigger bridge has now
> been built, executed, and gated on the cloud machine. Two facts supersede the
> "unvalidated prototype" framing that dominates the rest of this document.
> First, **the oracle parity gate passes**: at ten paired 1M/T2 repeats, eleven
> of thirteen checks pass and none fail, including the event-time due-to-admission
> latency the D3a fix targets. Phase 1b is discharged for every criterion that is
> decidable at that sample size. Second, **the learned residual never trained**:
> across a full 1/5/10/20M x T=2/6/10 matrix the DQN recorded zero gradient steps
> and a residual identically equal to zero, so every arm labelled `rl` executed
> the analytic prior. No result in this project has yet tested a learned policy.
> Sections 8, 9, 10.6, 11.5, 13.5, 14.1, 15 and 18 carry the detail; earlier
> statements about pending compilation and pending gates are historical.
>
> **2026-08-15 scope correction (authoritative):** the project implements an
> RL compaction **trigger**, not an RL file-picking policy. Protocol v3's
> candidate-aware/exact-SST controller was an experimental scope deviation. It
> produced a poor 1M/T=2 smoke result and was then rejected and removed. The
> active protocol is trigger-only v2: the learner returns compact/defer per
> level, and RocksDB's native `LevelCompactionPicker` selects all files. Any
> later statement in this document calling protocol v3, candidate selection,
> exact-file leases, a parametric candidate DQN, or its SLO mask "current"
> should be read as a historical description of the 2026-08-12 prototype. This
> correction supersedes those labels while preserving the details as project
> history.

This document explains what the project is, why it exists, how the current
system works, how the implementation evolved, what was fixed, what was added,
which measurements are trustworthy, and what remains incomplete. It is intended
to be the first document a new contributor reads and the historical record used
to interpret the older documents in `docs/`.

The repository contains several generations of design documentation. Those
documents accurately describe the system at the time they were written, but a
statement such as "the RL controller selects an SST" describes only the retired
protocol-v3 prototype, not the current implementation. This record therefore
uses the following
labels:

- **Current:** implemented in the working tree inspected on 2026-08-16.
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

The present design is a **trigger-only, physics-informed, two-action DQN**. It
starts cold, with no pretrained weights or checkpoint. The controller observes
global and per-level tree/workload metrics and returns `defer` or `compact` for
each reported level. A due `compact` response opens a level-scoped gate for the
control interval, allowing RocksDB to schedule zero, one, or several native
compactions until that level becomes healthy. A proactive below-threshold
response grants only one optional scheduling token. Neither form contains an
SST identity. RocksDB's configured native compaction priority selects the
source SSTs and its ordinary leveled-compaction machinery performs clean-cut
expansion, overlap discovery, conflict checks, merge semantics, output
formation, and background execution.

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

The exact-candidate code was removed after the failed smoke result. The scaled
pipeline now supports regular, deterministic trigger-oracle, analytic-prior-
only, unconstrained learned, and constrained learned-trigger arms with paired
repeats. It includes a tuned leveled grid, preregistered SLO-manifest selector,
oracle-parity evaluator, paired confidence evaluator, and separately calibrated
read-heavy/write-heavy stress runner. These source changes have not been built
or executed in this workspace at the user's request. Consequently, the repaired
trigger controller remains an implemented but unvalidated research prototype,
not yet an experimentally accepted final policy.

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

### 2.2 Why trigger timing and native file choice matter

Compacting early can reduce the run count and remove obsolete data, improving
reads and space. It can also rewrite data before enough garbage or useful
overlap has accumulated, increasing write amplification. Compacting late can
save write bandwidth in the short term, but retain more runs, expand debt,
increase lookup/scan work, and eventually stall writes. File choice matters
because two files at the same level can have very different overlap, deletion
density, compensated size, and projected effect on the output level.

The project intentionally learns only the timing/level decision. File choice
still matters, but the experiment holds that mechanism to RocksDB's mature,
configured `CompactionPri` heuristic. This isolates the research variable: the
RL action is level-scoped `defer`/`compact`, never an SST identity.

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

> **2026-09-05 preregistered amendment (authoritative; supersedes the strict
> three-way criterion below).** Recorded **before** any run of the programme in
> `docs/PATHWAYS.md`, and specifically before Gate 1. The strict Pareto
> requirement — write, point-read *and* scan amplification each strictly
> improved against a tuned baseline — is **withdrawn as infeasible**, on the
> grounds set out in Section 10.7 Finding 4 and proved in `docs/PATHWAYS.md`
> Theorems A.2 and B.1: in a leveled LSM tree the read/write trade is intrinsic,
> the comparator is selected to sit near the frontier, fanout conservation
> forbids any expansion profile beating the uniform tree at fixed depth, and
> elision below parity is capped by resident garbage, which this workload has
> almost none of.
>
> **The replacement objective is constrained, not scalarised:**
>
> ```text
> minimise    point-read amplification R(pi)
> subject to  W(pi)    <= W_base           (parity, beta = 0)
>             S(pi)    <= S_bound          (2% over baseline)
>             lat(pi)  <= lat_bound        (2%, Get / scan / aggregate write)
>             stall(pi) <= stall_base
> ```
>
> The write constraint is **parity, not a budget**. Theorem A.2 says geometry
> cannot go below parity and Theorem B.1 caps elision at the resident-garbage
> fraction, so parity is the defensible target; any `beta > 0` would concede
> headroom the theory says is not needed. A `beta` sweep is reported as
> exposition of the frontier's shape, never as the acceptance criterion.
>
> **The comparator becomes a class, not a point.** A single tuned baseline is
> replaced by the Pareto hull of the static configuration class over `(W, R)`
> (`docs/PATHWAYS.md` Pathway C, Proposition C.1). Two hulls are preregistered
> and are not interchangeable: **Hull-0** at `s = 1` is the comparator for arms
> without a capacity action, and **Hull-s**, which adds statically
> capacity-expanded configurations, is the comparator for any arm carrying one.
> Comparing a policy against a class denied a knob the policy has — or given one
> the policy lacks — is invalid in either direction.
>
> **Four subsidiary decisions, recorded here at the same time.**
>
> 1. **Scan objective.** `scan_amplification` sits at its mathematical floor of
>    1.0 and passes vacuously, so it is withdrawn as an acceptance metric. The
>    scan objective rests on `sorted_run_seeks` alone. This resolves the
>    preregistered scan-sensitivity choice left open since the repair plan.
> 2. **Stall test form.** The `all(delta <= 0)` form for `stall_events` and
>    `stall_seconds` is retired — it failed a 5.4% mean improvement at T=10
>    because one pair of ten increased. It is replaced by the paired-envelope
>    form already adopted for `per_level_maximum_score` in Section 14.2.
> 3. **Latency decidability.** Five pairs give confidence intervals spanning
>    +/-20-30% against a 2% limit. Latency checks whose interval is wider than
>    the limit are reported as **undecidable**, never as failed. Cells carrying a
>    latency claim must run the preregistered ten repeats.
> 4. **Space enters as a bound, not a minimand.** `Phi` currently treats space as
>    a quantity to minimise while the criteria treat it as a bound; Section 10.7
>    Finding 3 measures the learner spending reads on space it earns no credit
>    for. Space enters the reward only as a hinge penalty above the manifest
>    limit.
>
> **Still open, and blocking the gate named against each.** The
> `write_latency_p95_us` below `write_latency_avg_us` anomaly (Section 10.7,
> instrument problem 6) must be resolved before any latency figure appears in a
> submitted table. Whether the paper claims write parity or write improvement
> selects between two different acceptance sets and must be recorded before
> Gate 4.
>
> The criteria below are retained as the record of what was preregistered from
> the project's start until 2026-09-05, and as the standard the Section 10.7
> matrix was judged against. They are no longer the acceptance criteria.

The formal comparator is a **preregistered, workload-specific tuned leveled
baseline**, not the historical `T=10` configuration and not whichever regular
arm happens to look best after seeing RL results. Baseline selection is supposed
to occur before inspecting trigger-policy results:

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

The current controller can select compaction timing and a source level. It
cannot select a source SST. It does not replace:

- RocksDB's snapshot/sequence-number correctness;
- clean-cut expansion across files sharing user-key boundaries;
- overlap discovery;
- conflict detection with running compactions;
- compaction input iteration and merge semantics;
- tombstone/version elision rules;
- RocksDB's source-file priority and selection;
- output level choice for the native leveled compaction;
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
FLSM, it gives an external learner authority only over the leveled trigger.
The physics-informed prior similarly combines known
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
many tombstones more attractive. The trigger-only controller reuses this
heuristic unchanged rather than learning a second file-selection policy.

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
  ├── telemetry and per-level trigger state
  ├── held due gates and one-shot optional authorization
  └── native RocksDB file selection and compaction execution
             |
        Unix socket, newline-delimited JSON
             |
             v
Python server
  └── protocol-v2 multi-level processor
       ├── analytic trigger prior
       ├── two-action residual DQN
       └── replay/training
             |
             v
metrics, raw logs, policy logs, summaries, and graphs
```

The important directories are:

- `include/` and `src/`: the C++ workload wrapper, option parsing, listeners,
  measurement, and experiment JSON;
- `lib/rocksdb/`: the modified RocksDB checkout containing the trigger picker,
  client, telemetry, statistics, `db_bench`, and C++ tests;
- `rl_agent/`: protocol-v2 Python state, reward, model, replay, server, logging,
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

### 5.3 Current trigger-only protocol-v2 contract

One request contains global tree/foreground measurements and aggregate
per-level state. The response is only an ordered array of binary actions:

```text
0 = defer / do nothing
1 = authorize a compaction from this level
```

No request contains pickable SST candidates and no response contains file
numbers. Due authorizations are passed as an allowed-source-level mask into the
native leveled builder, which preserves RocksDB's current score ordering,
`FilesByCompactionPri`, clean-cut expansion, overlap checks, and conflict rules.
A below-threshold optional authorization invokes the same native forced-level
entry point once. The pipeline pins protocol v2 and the C++ producer is
hard-coded to v2, so setting a protocol-v3 environment variable cannot restore
exact-file selection.

### 5.4–5.8 Historical protocol-v3 prototype (retired)

The subsections below preserve the exact-candidate prototype implemented on
2026-08-12. They are not descriptions of active code after the 2026-08-15 scope
correction.

### 5.4 Historical protocol-v3 observation

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

### 5.5 Historical non-mutating candidate preview

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

### 5.6 Historical response and exact-file action lease

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

### 5.7 Authority and bypass reasons

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

### 5.8 Historical exact-file completion attribution

RocksDB stores decision ID, snapshot epoch, source file, and override reason on
the `Compaction` object. Per-level state in the next observation reports the
previous scheduling result and completion result, including the completed
decision/file identity. This separates four facts that older code conflated:

1. what Python requested;
2. what the picker attempted;
3. whether scheduling succeeded;
4. what compaction actually completed.

## 6. Current protocol-v2 learning and safety system

### 6.1 Two-action residual model

Each action-bearing source level has the same fixed action set:

```text
0 = defer / keep the level's policy gate closed
1 = compact / open the level's due gate or grant one optional token
```

The active model uses a shared state-encoding trunk with a separate two-action
output head for each level. The trunk pools scarce online samples without
erasing L0/deep-level differences; each level retains its own credit window,
exploration state, and output head. An independent-network ablation is retained.
The final residual layers start at zero, so cold-start behavior is exactly the
analytic prior:

```text
Q_i(state, action) = analytic_prior_i(state, action)
                   + learned_residual_i(state, action)
```

No checkpoint is loaded in the formal experiment. Replay stores the selected
binary trigger action, its analytic bias, actual elapsed-time discount, next
state, and transition-valid flag. Fallback, maintenance, stale-structure,
safety-masked, and otherwise unattributable intervals are excluded.

### 6.2 Physics-informed trigger prior

For each level, the prior estimates the advantage of opening its trigger over
deferring it from aggregate state only. In simplified form:

```text
advantage =
    w_stall * urgency
  + w_read  * read_exposure * expected_run_relief
  - w_work  * normalized_merge_work
  - w_early * premature_overlap_penalty
```

L0 urgency uses file count relative to the native compact/slow thresholds;
deep-level urgency grows superlinearly with bytes divided by target bytes. Read
exposure is derived from current Gets, scans, and per-level Get hits, so a
write-only interval cannot claim read relief. L0 run relief grows with its
overlapping file count; a deeper level is one sorted run, with relief weighted
by fullness. Merge work uses source bytes plus next-level overlap for L0 and a
marginal per-native-compaction overlap estimate for deeper levels. RocksDB—not
the prior—chooses which concrete files realize that work.

### 6.3 Cooperative whole-tree reward

Older designs assigned an independent potential to each source level. That
could reward moving bytes out of one level while ignoring the same bytes added
to its output level, or lose the benefit when an emptied level disappeared.
The active v2 learner therefore assigns the same global transition reward to
every level decision in one response frame:

```text
reward =
  gamma(dt) * Phi(next_tree) - Phi(current_tree)
  - dt * (write_amp_cost + point_probe_cost
          + scan_work_cost + latency_budget_cost)
```

`Phi` is the negative whole-tree cost built from measured logical point probes,
scan internal work and sorted-run seeks, physical/live space, pending debt,
stall duration, and level-invariant structural run terms. The final output-only bottom
level is exported as global state even though it has no policy head. This keeps
output growth in the cost and prevents a penultimate-to-bottom move from
manufacturing relief. Empty action-bearing levels remain zero states so genuine
run removal receives credit.

Write amplification is accumulated as the formal run-to-date ratio. Thus a
telemetry window containing compaction writes but no foreground Put is not
mistaken for free I/O. Latency cost is dimensionless excess above the manifest's
Get, scan, and aggregate-write average/p95 limits, not raw milliseconds.

### 6.4 Deterministic trigger-only safety mask

With a valid workload-specific `baseline_slo.json`, the controller tracks
rolling latency/space windows with a minimum sample count and three-window
hysteresis. Its actions remain trigger-only:

- **Space breach:** force open due gates so deferral cannot retain additional
  physical debt.
- **Read average/p95 breach:** force open responsible due gates; if level
  responsibility is ambiguous, conservatively open every due level.
- **Write average/p95 breach:** revoke and prohibit optional below-threshold
  compactions while retaining mandatory due/safety work.
- **Simultaneous read/write breach, missing or mismatched manifest, or an
  unattributable breach:** expose tuned native due eligibility for the known
  responsible level, or all due levels when responsibility is unknown.

Independent safeguards force a due level open when its wall-clock due age,
zero-order-held excess pressure, instantaneous score, or normalized debt reaches
the selected baseline envelope. A stale structural cache has a bounded dirty
deadline; a miss opens all currently due levels, suppresses optional work, and
invalidates affected transitions until the cache converges.

The final pipeline requires a matching manifest for learned/prior-only arms by
default, loads its selected L0 thresholds and compaction priority for all
compared arms, and passes the full workload/geometry fingerprint to C++ and
Python. `unconstrained_rl` disables the live SLO mask as an ablation; `oracle`
also disables it so bridge parity is measured against native triggering. The
mask reduces online violations but is not a mathematical latency guarantee;
the paired acceptance test remains authoritative.

### 6.5 Retired candidate-model note

The protocol-v3 prototype used 24 state features, 18 features per candidate,
up to eight SST candidates plus a defer pseudo-candidate, validity masks, shared
state/candidate encoders, and separate per-level scoring heads. Replay stored
chosen and next candidate sets. Those details are retained in sections 5.4–5.8
and `docs/candidate_aware_protocol_v3.md` solely as history. None of those
tensors, candidate actions, exact-file validators, or model paths are active.

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

The scaled pipeline implements this per-arm artifact separation and supports
paired repeats, distinct policy seeds, shared workload seeds, and alternating
arm order. Its safe operational default remains one repeat, so final claims
must explicitly request the preregistered repeat count.

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

### 2026-08-12: candidate-aware protocol-v3 prototype

The working tree temporarily introduced a candidate-aware redesign:

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

This work was present before the 2026-08-15 correction. It is retained here as
history and is no longer present in the active implementation.

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

### 2026-08-15: exact-file policy rejected; trigger-only scope restored

A completed 1M/T=2 smoke pair showed that both arms ran, but protocol v3 was
clearly harmful:

| Metric | Regular | Candidate v3 | Change |
| --- | ---: | ---: | ---: |
| Runtime | 29.97 s | 39.23 s | +31% |
| Write amplification | 4.55 | 12.46 | +174% |
| Compaction bytes written | 1.48 GB | 4.77 GB | +223% |
| Point probes/read | 5.57 | 9.17 | +65% |
| Sorted-run seeks/scan | 6.50 | 9.29 | +43% |
| Stall duration | 5.06 s | 9.95 s | +97% |

The server reported no fallback and small socket overhead, while the RocksDB
log reported 11 epoch validation failures among 58 actuations. This isolated
the result from a silent-fallback explanation and exposed problems inherent to
the exact-candidate formulation: action multiplicity favored compaction,
candidate I/O was insufficiently priced, and a whole-tree epoch made file
decisions stale.

The more important conclusion was one of project scope: this repository was
intended to improve RocksDB's compaction **trigger**, not replace its file
picker. The response was therefore architectural, not a retuning attempt:

- remove the candidate controller, parametric candidate model/configuration,
  candidate tests, and protocol-v3 server path;
- remove candidate fields and exact-file parsing from the C++ wire protocol;
- remove non-mutating candidate-preview and exact-file picker APIs from the
  modified RocksDB checkout;
- make every authorization level-scoped and route it through
  `PickCompactionFromLevel`, which uses RocksDB's configured native priority;
- hard-pin the experiment pipeline and C++ producer to protocol v2;
- replace exact-file tests with tests proving native file-priority selection,
  level isolation, current-state selection, fallback scope, maintenance
  attribution, budget exhaustion, and exactly-once level actuation.

### 2026-08-16: trigger bridge repaired around held gates

The first restored trigger-only 1M/T=2 result was still substantially worse
than regular leveled RocksDB. Log analysis showed that a compact response was
being treated as a one-use scheduling pulse while RocksDB could service many
native jobs between observations. Deferral also suppressed
`NeedsCompaction()`, which was the only observation publication site, creating
a self-blinding control loop.

The approved repair therefore changed the bridge, not RocksDB's file policy:

- one atomic response frame now holds independent due gates per level;
- due gates remain open for repeated native picks while the current score is
  at least one; proactive below-threshold gates retain one token;
- the native leveled builder accepts only an allowed-source-level mask and
  keeps its score ordering, `FilesByCompactionPri`, clean-cut expansion,
  overlap, and conflict logic;
- a DB-owned asynchronous coordinator coalesces immutable snapshot refreshes
  and eligibility/retry wakes without carrying its queue lock into the DB
  mutex;
- a shared per-column-family pressure observer captures score transitions for
  both regular and RL runs and integrates `max(score - 1, 0)` with a
  zero-order hold;
- safety uses wall-clock due age, integrated pressure, score/debt caps, a
  measured-response watchdog, and a fingerprinted latency/space manifest;
- fallback, stale structural advice, safety overrides, and unattributable
  intervals are excluded from replay;
- compaction event logs now identify decision, eligibility interval, bypass
  reason, and final-drain phase without naming an SST in the policy protocol;
- the learner uses one cooperative whole-tree reward based on logical probes,
  scan work, physical/live space, debt, stalls, latency, and write I/O.

The numbered pipeline gained repeat-aware regular/oracle/prior-only/learned
arms, baseline episode capture, an independently controlled tuned leveled grid,
and preregistered `baseline_slo.json` selection. These changes are present in
source but are deliberately unbuilt and unexecuted in this workspace; cloud
build, deterministic oracle parity, safety smoke, and final repeated acceptance
remain required.

The completed source pass added the following concrete pieces:

- a DB-owned asynchronous control coordinator with per-column-family
  registration generations, deferred snapshot refresh, scheduling/retry wakes,
  coalescing, queue-delay telemetry, and two-phase detach/worker stop;
- an event-driven pressure observer shared by regular and RL column families,
  using zero-order hold for due age, maximum score, integrated excess pressure,
  and normalized debt episode export;
- immutable structural snapshots with source/built generations, dirty-age
  tracking, coalesced refresh, build-latency telemetry, and a 250 ms default
  stale-structure deadline that conservatively opens due gates;
- held per-level due gates, one-shot optional tokens, monotonic blocked-pick
  backoff, current-score reclassification, generation-safe outcomes, and an
  allowed-level adapter inside RocksDB's native leveled builder;
- one-to-many decision/eligibility attribution on compaction start and
  completion, explicit maintenance/fallback/drain reasons, and invalid replay
  marking for overridden intervals;
- generation-safe result attribution, so a native attempt admitted by an old
  frame cannot mutate the outcome fields of a newer response; fallback and
  synchronous forced-open paths now export their actual effective action and
  reason rather than leaving stale policy outcomes behind;
- removal of the last sticky parent-bypass handoff: maintenance, drain, and
  server-unavailable fallback are classified from current state in the actual
  picker invocation, so one scheduler call cannot consume another call's
  authority or reason;
- first-pass oracle gate installation before background scheduling, plus
  protocol-v2 cadence enforcement at one observation per actuation so neither
  selected actions nor physical-cost intervals are silently discarded;
- per-episode (rather than process-lifetime) pressure exported to the live
  safety mask, downstream due-level support at L0 slowdown/stop, and exact
  integer preservation for 64-bit decision and structural generations;
- output-only bottom-level state and a cooperative global reward that charges
  both source relief and destination growth;
- exact `db_bench` space-amplification inputs from pre-reference-compaction SST
  bytes and `rocksdb.estimate-live-data-size`, plus explicit drain start/end,
  pending-debt, compaction-byte, and compaction-time phase fields;
- a full workload/geometry fingerprint, automatic application of selected
  baseline options to every arm, Student-t repeat error bars, formal paired CI
  evaluation, deterministic oracle-parity evaluation, the unconstrained learner
  ablation, and read-heavy/write-heavy stress-suite orchestration.

No RocksDB library, `db_bench` binary, C++ test, or experiment was built or run
for this source pass. Those are cloud validation gates, not facts that should be
inferred from the presence of the implementation.

### 2026-08-16: closing the audited deviations from the repair plan

A conformance review of the implementation against
`TRIGGER_CONTROLLER_REPAIR_PLAN.md` found the design essentially complete but
seven points where the code did not match the approved plan. All seven were
closed in source:

1. **Score-seam audit (plan §7.3, decision 18).** The plan asked for a named
   `RecomputeActiveCompactionScoreAndObserve` seam plus a static audit of every
   direct `ComputeCompactionScore()` call. The implementation instead places the
   observer hook *inside* `ComputeCompactionScore` and attaches the observer only
   to the active version (`ColumnFamilyData::SetCurrent`, cleared in
   `VersionSet::AppendVersion`), which no call site can bypass. That is stronger
   than the wrapper but produced no evidence. `NeedsCompaction()` now runs a
   continuous audit: it compares each level's observer-held score with the score
   admission is actually deciding against and exports
   `pressure_checks`/`pressure_divergences`/`pressure_max_divergence_milli`. A
   bypassing path shows up as a nonzero divergence count during any run rather
   than requiring a manual call-site sweep.
2. **L0 posture (decision 6).** L0 previously deferred like any other level.
   `RL_L0_ALLOW_DEFER` now defaults to `0`, holding L0 non-deferring during
   bridge validation as approved. An overridden L0 defer is attributed to the
   new `kPosture` reason and marked overridden, but its transition stays valid
   for replay: the posture is a fixed part of the environment rather than a
   reactive safety intervention, and the learner already keys its sample on the
   executed action.
3. **Absolute debt cap (plan §7.5).** The fixed 10 GiB `kPcbHardCap` is removed.
   Both the synchronous admission path and the worker safety evaluation now use
   one normalized `pending_bytes / live_logical_bytes` guard against the
   manifest limit, or the configured bootstrap cap when uncalibrated.
4. **Two minimum-compact-score thresholds.** The C++ optional-token gate
   defaulted to 0.5 while the Python action mask used 0.10, so a band of offered
   actions could never be admitted. Both sides now read one
   `RL_OPTIONAL_MIN_SCORE`, exported by the pipeline and recorded per arm.
5. **Quantile policy (plan §7.4, decision 16).** The manifest generator used a
   bare interpolated `Q99` above `N=299`. It now computes a distribution-free
   upper tolerance bound from an order statistic, records the coverage,
   confidence and rank actually achieved, falls back to 90% coverage when the
   episode count cannot support 99%, and leaves the level on an explicitly
   uncalibrated bootstrap cap when it can support neither. A level is marked
   calibrated only when *all three* of its limits rest on a real bound.
6. **Dropped observation fields (plan §11.2, §10.6).** `observation_micros`,
   `structural_snapshot_age_micros` and `score_event_generation` reached the
   wire but were discarded by the Python parser; they are now parsed, with
   generations preserved as exact integers. Per-level `jobs_scheduled`,
   `jobs_completed`, `decision_to_first_schedule_micros`, `trivial_move_jobs`
   and `trivial_move_bytes` are new on both sides. Trivial moves are counted at
   compaction completion, so a level drained cheaply by moves is no longer
   indistinguishable from one drained by rewrites.
7. **`epsilon` (plan §7.8.1 R2, test 28).** The admission-latency budget was
   never measured, leaving `H_i + epsilon` untestable. All four terms are now
   instrumented — score-event publication, worker tick, control-queue delay and
   scheduler admission — exported individually and as a maximum, with
   `RL_EPSILON_BOUND_MS` counting violations when a bound is configured.

Verification performed for this pass: every modified translation unit passes a
real-flags `-fsyntax-only` compile, all pipeline shell scripts pass `bash -n`,
the tolerance-bound estimator reproduces the plan's own arithmetic (N=299 is
exactly where the maximum becomes a 99%/95% bound), 73 of 75 Python unit tests
pass, and all 7 Unix-socket protocol-v2 tests pass. Full compilation, linking
and every experiment gate remain outstanding.

**Two pre-existing Python test failures are unresolved and are not caused by
this pass.** `test_cost_half_scales_with_the_interval_it_covers` and
`test_deep_level_read_cost_scales_with_how_full_the_level_is` encode the
superseded per-level potential reward. The cooperative whole-tree reward
introduced earlier on 2026-08-16 deliberately changed both behaviours: shaping
is now the standard SMDP form `Phi_prev - gamma(dt) Phi_next`, which does vary
with the interval when the state is unchanged, and a non-empty deep level
contributes a fixed structural run rather than a fullness-scaled read cost, with
byte-proportional cost carried by the measured scan and space terms instead.
Whether the tests or the reward should change is a research decision that has
not been made; the tests were deliberately left failing rather than rewritten to
match the code, because a suite edited to agree with its implementation stops
being evidence about it. This must be resolved before Phase 4.

### 2026-08-18 to 2026-08-19: the oracle-gate defect analysis and its fixes

The first execution of the repaired bridge produced a gate reporting
`passed: false` on four checks. Analysis (`ORACLE_GATE_FIX_PLAN.md`, Revision 3)
decomposed those four into seven defects, of which **only one was in the
controller**. The rest were defects in how the controller was being measured,
and two of them were manufacturing false failures.

| | Defect | Nature |
| --- | --- | --- |
| D1 | `per_level_maximum_score` compared the oracle's trigger-trace maxima against the baseline's *episode* maxima. A regular arm constructs `LevelCompactionPicker` and cannot write a trace at any setting, so a populated-but-never-due level got a fabricated baseline of 0 and a limit of `max(1.05 x 0, 0.10)`. | Instrument asymmetry; worsens as more levels stay populated-but-not-due at larger scales |
| D2 | `CompactionPressureObserver` exported an episode only on a due->healthy transition, so every level still due at a phase boundary lost that episode -- the exact upper tail `06_select_baseline_slo.py` estimates its tolerance bounds from. | Silent data loss in both arms; corrupts manifest calibration |
| D3 | Nothing acted on the due edge. A level crossing between two ticks carried the previous frame's `kDefer`, so trigger latency tracked the decision interval. | The only genuine controller defect |
| D4 | Envelope criteria were gated with `all()` over three repeats on quantities whose seed-to-seed spread exceeds their effect size. | Methodology; the gate could not decide |
| D5 | The stall allowance was derived from the observation period, so shortening the decision interval tightened the tolerance at the same moment it improved the controller. | Methodology; the two runs were not judged by the same yardstick |
| D6 | The exploration schedule was derived from `11/100000` -- 0.11 decisions per 1000 operations, measured when the observation rate was 2.6/s and invalidated by the Phase 1a repair that raised it to ~20/s. | Configuration; would mis-anneal every learned arm |
| D7 | The due->eligible latency estimator had no episode-end bound, treated a gate held open from an earlier decision as instant authorization, and dropped unauthorized episodes without counting them. | Instrument; it is the measurement D3's magnitude claim rested on |

Fixes landed in four stages, ordered so each one changed what the remaining work
was. Stage 1 was evaluator-only and needed no rebuild.

- **D1** the gate compares per-level maxima drawn from the pressure episode log,
  which *both* arms write through the same exporter. Levels the baseline never
  exercised are reported separately as informational output rather than compared
  against a fabricated zero.
- **D2** `CompactionPressureObserver` gained `FlushOpenEpisodes` and a
  process-wide registry so the workload/drain boundary and process teardown both
  close open episodes; the episode record moved to `schema_version` 2 carrying
  `truncated` and `phase`.
- **D3a** the crossing posture. A `defer` selected while a level was *below* its
  trigger expressed no judgement about a due level, so it no longer binds once
  the level crosses; the level is admitted under `ActionReason::kPosture`.
- **D4/D5** invariant-versus-envelope split, paired confidence bounds sharing one
  instrument with `07_evaluate_paired.py`, a per-metric `required_pairs` computed
  from observed dispersion, and a stall allowance that is no longer a function of
  the decision interval.
- **D6** exploration anneals on wall time.
- **D7** the estimator gained an episode-end bound, a gate-transition test, and
  exported denominators; a C++ event-time histogram was added because the trace
  is written once per worker tick and therefore cannot express an acceptance
  threshold below one tick.

**Stage 5 (D3b, the due-edge wake) was deliberately not built.** It must land
alone after the gate passes under D3a so a regression is attributable, and its
rate budget cannot be sized without the measured due-crossing rate.

Two implementation deviations from the plan as written are recorded in
`ORACLE_GATE_FIX_PLAN.md` Section 10.2. D3a is a permit promotion rather than the
predicate change the plan specified, because `RecordSuccessfulSchedule` validates
`eligibility_generation` and a still-closed permit fails attribution downstream.
D6 anneals linearly in wall time rather than on the "half-life" the plan
proposed, because an exponential half-life never reaches a floor and the plan's
own acceptance check is stated as reaching one.

### 2026-08-22: the oracle parity gate passes

Built and executed on a fresh cloud machine (single 447 GB SSD, root filesystem
shared with the OS -- absolute runtimes are therefore not comparable with the
earlier NVMe-backed runs, though paired differences remain valid).

Ten paired 1M/T2 regular/oracle repeats. **Verdict `undecided`, `failed: []`** --
eleven of thirteen checks pass and none fail.

| Check | Result |
| --- | --- |
| `due_to_admission_latency` | **passed** -- p50 511 us against a 5000 us limit, at the 50 ms cadence |
| `per_level_maximum_score` | **passed** at ten pairs, having failed 2 of 3 at three pairs |
| `mean_l0_l1_input_size` | **passed**, mean -0.41%, CI [-1.09, +0.28] |
| write amp / point-read amp / seeks | **passed** as paired envelopes: +0.01%, -0.40%, -0.33% |
| `maximum_pending_debt` | passed, +0.57% |
| decision rate, observation health, held-gate service, due-level authorization, workload identity | passed |
| `no_new_oracle_stop_event` | **insufficient_pairs** -- `required_pairs` returns null (">200 pairs; the effect size is small relative to the seed-to-seed spread") |
| `stall_duration` | **no_allowance_configured** -- the D5 allowance must come from the baseline sweep's dispersion, which had not yet run |

The latency result is the substantive one. Against the pre-fix trace-derived
median of ~33 ms at the same 50 ms cadence, and ~6.5 ms at a 10 ms cadence, D3a
brings due-to-admission latency to 511 us **at the original cadence** -- a 65x
reduction, and better than the 10 ms configuration achieved, without paying its
socket and CPU cost. That is the outcome D3b was designed to produce, obtained
from the cheaper semantic fix.

`per_level_maximum_score` passing at ten pairs also settles an open question:
its failures at three pairs moved between L1 and L2 across configurations, which
is what noise on a maximum statistic looks like. It survives as an all-repeats
invariant and does not need reclassifying.

### 2026-08-22 to 2026-08-24: preregistered decisions and the paired matrix

Recorded before any RL arm ran (`ORACLE_GATE_FIX_PLAN.md` Section 11):

- **`compaction_pri` fixed at 3 (`kMinOverlappingRatio`), not swept.** It selects
  which file inside an eligible level compacts first, which is outside the
  trigger-only scope; the runner applies it identically to every arm and folds it
  into the shared fingerprint, so it cannot produce a paired difference in either
  direction. This is a deliberate deviation from the sweep specified in
  `TRIGGER_CONTROLLER_REPAIR_PLAN.md` Section 3.1 step 1.
- **`level0_slowdown_writes_trigger` fixed at 20 and `level0_stop_writes_trigger`
  at 36, not swept.** Live on this workload but the proposed ranges span 11-20%
  of their own scale, a 4x multiplier on the sweep for a perturbation.
- **`level0_file_num_compaction_trigger` remains swept over {4, 8}** -- it is the
  static analogue of the research variable.
- **Workload sizes 1M, 5M, 10M, 20M** on a total-time budget, with 1M and 5M
  retained as low anchors and not expected to discriminate any trigger policy.

The sweep therefore ran 6 configurations (3 size ratios x 2 L0 triggers) x 4
sizes x 3 repeats, and the paired matrix ran 4 arms x 12 cells x 3 repeats = 144
arms. Manifests were generated for all twelve size/ratio combinations.

### 2026-08-24 to 2026-08-26: the matrix result, and what it does not test

See Section 10.6. The headline is that **the learner never trained**, so the
matrix measures the analytic prior with and without the live SLO mask, and not a
learned policy at all.

### 2026-09-01: the learner trains for the first time

Credit-assignment schema v2 was verified on the cloud machine and the
bootstrap/fallback state model was repaired (Section 14.4). A staged 1M/5M/10M
preflight then passed every learner-health gate at two scales, with exact
C++/Python accounting agreement: `response_acknowledgements x levels ==
accepted_decisions` and `stale_response_rejections x levels ==
rejected_decisions` on all four learned arms. Zero hard-invalid intervals, zero
watchdog expiries, zero protocol mismatches, zero bootstrap failures. The
blocker standing since 2026-08-24 is closed.

Two source fixes made during that pass are recorded in Section 14.5: an
accounting identity that omitted a legitimate sink, and a watchdog that could
not distinguish a busy structural-refresh path from a dead server.

### 2026-09-03 to 2026-09-05: the first trained-learner matrix

10M operations at T=2, 6 and 10; five paired repeats; `regular`, `prior_only`,
`unconstrained_rl` and `rl`. See Section 10.7. Every cell fails acceptance, but
for the first time the failure describes a policy that actually learned rather
than a closed-form heuristic wearing the learner's name.

## 9. Defect and fix catalogue

The following table consolidates the failure modes documented across the
technical spec, validity review, multi-level design, rework changelog, session
context, system guide, workload notes, and current code.

| Area | Failure or risk | Correction | Current status |
| --- | --- | --- | --- |
| L0 actuation | `compact_now` only woke the scheduler; the normal picker could still choose nothing. | Force a pick from the authorized level, then invoke RocksDB's native file picker. | Fixed in trigger-only v2. |
| Fallback | Server failure could be silent, making a leveled run look like RL. | Explicit availability/fallback result, logging, cumulative counter, invalid replay transition. | Fixed. |
| Protocol JSON | Whitespace in Python output broke the C++ parser and invalidated 81 runs. | Compact output plus tolerant parsing and compatibility socket tests. | Fixed; old runs invalidated. |
| Epoch identity | Converting a 64-bit exact-file epoch through float rounded it and made responses stale. | Exact integer parsing was implemented. | Historical v3-only defect; exact-file epochs are no longer action validators. |
| DB mutex | Socket/inference under the mutex blocked RocksDB and confounded runtime. | Snapshot/worker architecture; socket work off mutex. | Fixed. |
| Deferral authority | Due levels could compact through normal policy despite a defer action. | Held per-level closed/open gates with wall-clock/pressure safety overrides. | Fixed. |
| Cross-level authority | A global parent unlock for one level could schedule a different level. | Per-level reason and forced-level path; parent only for maintenance/drain. | Fixed and tested in trigger-only v2. |
| Exhaustion | Global `DeferralExhausted()` could authorize unrelated work. | Per-level wall-clock/pressure safety opens only the responsible gate. | Replaced by the 2026-08-16 held-gate design. |
| Sticky actions | Boolean force state could survive too long, vanish too early, or be reused. | Atomic response frames with decision and stable eligibility generations; due gates are deliberately held and optional tokens are one-shot. | Replaced by the 2026-08-16 held-gate design. |
| Stale candidate | A missing/blocked exact-file action could not survive ordinary tree changes. | Exact-file action mechanism removed; RocksDB selects from current state at actuation. | Retired with protocol v3. |
| Decision attribution | Requested action was confused with executed/scheduled action. | Record request, scheduling result, completion result, decision, epoch, and reason. | Fixed. |
| Safety override replay | Samples were labeled with the selected action even when a guard executed another action. The first repair then cleared every overlapping four-second window for any rejected or overridden frame. | Credit-assignment schema v2 acknowledges decisions by exact ID, relabels known overrides to the executed action, rejects only an uninstalled proposal, and reserves hard boundaries for uncontrolled attribution loss. | Repaired in source 2026-08-31; cloud validation pending. |
| Parent compactions | Maintenance work could be attributed to the learner. | Explicit bypass reasons; maintenance/drain preserve the installed ID and publish compact, while uncontrolled native fallback clears the ID and raises a hard attribution boundary. | Updated for credit schema v2. |
| Interval semantics | Counter deltas covered variable time but were used as if fixed-rate samples. | Carry `interval_micros`; calculate rates and gamma from real elapsed time. | Fixed. |
| Decision-density reward | Rate costs were summed per decision, so faster polling changed return. | Integrate over `dt`; test return invariance. | Fixed. |
| Reward/prior scale | Reward dominated the analytic prior and destabilized TD learning. | Rescale/integrate reward; monitor return/prior/TD distributions. | Fixed for v2 and reflected in v3 design. |
| Source-only reward | Moving bytes into the next level manufactured apparent relief. | One cooperative global tree cost charges source relief and output growth. | Implemented in current v2 learner; cloud tests pending. |
| Empty level credit | A drained level disappeared and received no run-removal benefit. | Preserve zero states/global tree representation; explicit run-removal prior. | Fixed and tested. |
| Terminal reward | A zero-filled terminal message produced a phantom reward near `+2.109`. | Terminal state finalizes pending credit without manufacturing state relief. | Fixed and tested. |
| Reappearing level | A level that emptied and returned used the wrong time gap/discount. | Preserve real gap and SMDP discount. | Fixed and tested. |
| End-of-run debt | A run could finish before its compactions, making delayed policy look cheap. | Drain pending work and account for total and phase-separated drain bytes/time. | Fixed in source: `db_bench` marks drain start/end and debt before/after; compaction completion events identify workload/drain phase. |
| Credit horizon | Fixed n-step counts represented wildly different wall time. | Wall-clock credit horizon; terminal flush. | Fixed in current v2; the later v3 variant is retired. |
| Sample budget | Replay/training/exploration thresholds exceeded decisions available in short workloads. | Recalibrate warmup, batch, exploration, normalization, and training cadence. | Fixed for short v2 runs; each new workload scale still needs budget auditing. |
| Shared learning | Independent deep agents saw too few samples. | Shared trunk with separate level heads; independent ablation retained. | Fixed in current v2. |
| Head isolation | A global action head could erase level-specific behavior. | Separate per-level heads in the current shared-trunk v2 learner. | Fixed in source; execution pending for this pass. |
| Cold start | A random residual could override known LSM behavior before learning. | Zero-initialize residual so step-zero policy equals analytic prior. | Fixed and tested. |
| Whole-level action cost | Prior priced one compaction as rewriting the complete level. | Trigger-only v2 prices marginal overlap and projected I/O without selecting a file. | Fixed. |
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
| Single-run claims | One lucky seed was treated as a result. | Repeat-aware paired seeds and alternating order; final acceptance requires at least ten paired repeats and confidence intervals. | Runner implemented; final experiment not run. |
| Build parity check | A grep-based parity check was incorrectly treated as proof of equivalent binaries/options. | Record commands/options/revisions and perform behavioral/control-path checks. | Methodology correction. |
| Script sprawl | Multiple overlapping runners and plotters caused confusion and unsafe reuse. | Replace the current use case with a numbered `db_bench` pipeline. | Current working-tree cleanup. |
| Gate instrument asymmetry (D1) | The per-level score check compared the oracle's trigger trace against the baseline's episode log; a regular arm cannot write a trace, so a never-due level got a fabricated baseline of zero. | Compare only quantities both arms produce; report baseline-unexercised levels separately. | Fixed 2026-08-19; verified against the recorded runs. |
| Lost final episodes (D2) | Episodes were exported only on due->healthy, so any level still due at a phase boundary was dropped -- the tail the manifest's tolerance bounds are estimated from. | `FlushOpenEpisodes` plus a process-wide registry; episode `schema_version` 2 with `truncated`/`phase`. | Fixed in source 2026-08-19; measured effect on this workload is small (one truncated record across six arm-runs). |
| Censored episodes ranked as samples | The first D2 design pooled truncated episodes into the order-statistic bound. A truncated record is a *lower bound*, so pooling biases the distribution downward and tightens the limit -- the same direction as the defect being repaired. | The 2026-08-19 repair still raised a completed-sample bound to a censored lower bound, which was not a valid upper tolerance bound. The 2026-08-30 audit now refuses calibration when any relevant episode is right-censored unless a censoring model is preregistered. | Corrected 2026-08-30; cloud regeneration and holdout remain pending. |
| Trigger latency tracked the decision interval (D3) | A level crossing between ticks carried the previous frame's `kDefer`, so a due level waited up to one interval for authorization. | D3a: a below-threshold `defer` no longer binds across the crossing; the level is admitted under `kPosture` with the transition kept replay-valid. | Fixed; gate-verified 2026-08-22 at 511 us p50 against ~33 ms pre-fix. |
| Undecidable gate criteria (D4) | `all()` over three repeats on quantities whose seed spread exceeds their effect size. | Invariant/envelope split, paired confidence bounds, per-metric `required_pairs` from observed dispersion, explicit `insufficient_pairs`. The later audit also removed `Stopping writes` log-line counts as a gate because RocksDB emits them on condition recalculation, not only on stop transitions. | Corrected 2026-08-30. Stall duration still needs a preregistered allowance. |
| Tolerance keyed to a tuning knob (D5) | The stall allowance was derived from the observation period, so tuning the controller tightened its own yardstick. | `--stall-allowance-seconds`, preregistered from the baseline sweep's dispersion, with no default. | Fixed 2026-08-19; the allowance itself is still to be derived. |
| Dead exploration constant (D6) | The decay schedule encoded 0.11 decisions per 1000 operations, measured before the Phase 1a repair raised the rate ~8x. | Anneal on wall time (`RL_EXPLORATION_ANNEAL_SECONDS`); the step schedule is retained as an explicit ablation. | Fixed 2026-08-19. Verified in the matrix: final epsilon sat at its 0.05 floor. |
| Biased latency estimator (D7) | No episode-end bound (an unauthorized episode inherited a later one's timestamp), a held-open gate read as instant authorization, and dropped episodes were not counted. | Bound the window, require a gate transition, export the denominators, and add a C++ event-time histogram because the trace is tick-quantised. | Fixed 2026-08-19. ~20% of episodes at 50 ms were opening and closing between two ticks, previously invisible. |
| Binomial overflow in the tolerance bound | `math.comb(n, i)` reaches ~1e600 for n in the low thousands; multiplying by a float raises `OverflowError`. The search was also O(n^2) over candidate ranks. | Log-space PMF plus a single incremental pass (`smallest_valid_rank`). | Pre-existing; exposed at 5M and fixed 2026-08-24. n=299 remains exactly where the maximum becomes a 99%/95% bound. |
| Graph generator coupling | `06_select_baseline_slo.py` loads `04_generate_graphs.py` only for `collect_arm`, but that module imported matplotlib at top level, so manifest generation failed on any interpreter without it. | Import matplotlib lazily inside the two functions that draw. | Fixed 2026-08-24. |
| **Learner never trained** | Zero gradient steps and an identically zero residual across the earlier matrix. A later 5M/T2 unconstrained diagnostic produced 89,652 decisions and zero replay transitions because one rejected interval cleared every overlapping four-second window. | Credit-assignment schema v2 separates C++ installation acknowledgement from reward validity, retains known overrides off-policy, terminalizes valid prefixes only at a hard attribution boundary, and enforces explicit accounting. | Root cause diagnosed and repaired in source 2026-08-31; fresh cloud learner-health validation remains blocking. |

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
It failed the project objective. It motivated improved trigger rewards and
constraints; it does not justify transferring RocksDB file selection to RL.

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

### 10.5 Rejected candidate-aware smoke result

The 1M/T=2 result in the 2026-08-15 timeline is the only recorded protocol-v3
run. It is a negative engineering result, not an accepted policy comparison.
Protocol v3 was removed after it increased write amplification, logical read
work, stalls, latency, and runtime. No future experiment in the current project
should label exact-SST selection as the RL arm.

### 10.6 The 2026-08-24 paired matrix, and the training defect it exposed

**Configuration.** 1M/5M/10M/20M x T=2/6/10 x {`regular`, `prior_only`,
`unconstrained_rl`, `rl`} x 3 repeats = 144 arms. Manifests generated for all
twelve cells from the narrowed 6-configuration sweep. Paired seeds, alternating
arm order.

**Result against the acceptance criteria: fails.** Pooled over 36 paired
differences, relative to the leveled baseline:

| Arm | write amp | point-read amp | seeks/scan | space amp | runtime | stall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `prior_only` | +0.11% | +0.72% | +0.50% | -0.18% | +3.57% | +5.74% |
| `rl` | **+0.92%** [+0.4, +1.4] | +0.92% [-0.4, +2.2] | +0.70% [-0.4, +1.8] | +0.98% [-1.2, +3.1] | **+3.99%** [+2.3, +5.7] | **+10.56%** [+7.1, +14.0] |
| `unconstrained_rl` | **+18.94%** [+16.2, +21.7] | **-11.82%** [-14.9, -8.8] | **-11.95%** [-15.0, -8.9] | +3.46% [+0.6, +6.3] | +6.34% | +7.99% |

The criteria in Section 3.1 require the interval for write, point-read *and* scan
amplification each strictly below zero, space within 2%, and no stall increase.
`rl` has write amplification significantly *above* zero, stalls up 10.6%, and
runtime up 4.0%.

Pooling across heterogeneous cells makes these intervals tighter than the
per-cell picture warrants -- runtime ranges from -5% at 1M/T2 to +12% at 20M/T6 --
so the pooled bounds should be read as direction, not precision. At three repeats
most per-cell differences are individually undecidable.

**But none of this tests a learned policy.** `11_analyze_learning.py` reports,
for all 37 active level-cells across all twelve configurations:

- **zero gradient steps**, at 523 to 61,597 decisions per level;
- **residual advantage identically 0.000000** wherever the analytic prior was
  non-zero -- the residual head never left its zero initialisation;
- **argmax flip rate 0.0** -- learning changed no decision anywhere.

So `Q = analytic_prior + 0`, and every arm labelled `rl` executed
`argmax_a b(s,a)`: the same closed-form heuristic `prior_only` runs.

A third, independent confirmation comes from the summary metrics rather than the
learning logs. `prior_only` and `rl` both run with the SLO mask on and both run
the pure prior, so their only mechanical difference is exploration --
`prior_only` is in eval mode with none, `rl` anneals to a 0.05 temperature floor.
Their gap is +0.8pp write amplification and +4.8pp stalls, which is what
exploration costs when nothing is learned from the samples it generates. A
trained residual would have moved `rl` away from `prior_only` in some other
direction.

**What the matrix does establish.** Two findings survive, provided the arms are
labelled by what they are rather than as "RL":

1. **The analytic prior is systematically biased toward over-compaction.**
   Unmasked, it buys 11.8% fewer point probes and 12.0% fewer sorted-run seeks
   for 18.9% more write amplification, consistent in sign across all twelve
   cells. That is a directional error, not noise, and it is precisely what the
   learned residual exists to correct.
2. **The live SLO mask substantially replaces the policy rather than trimming
   it.** `rl` and `unconstrained_rl` run the same policy; the mask alone
   collapses an ~19%/-12% excursion to within ~1% of baseline on every
   amplification metric.

**Leading hypothesis for the training defect**, not yet confirmed:
`ForceOpenLevel` sets `transition_valid = false` for *every* level in the frame,
because the reward is a whole-tree quantity and one forced compaction makes the
whole frame unattributable. Invalid transitions are excluded from replay. Most
levels in the generated manifests came back `calibrated: false` and therefore
enforce the hard-coded bootstrap caps rather than measured limits, which would
make the mask fire often. If it fires often enough, replay never accumulates a
valid transition and no gradient step ever happens -- the mask suppressing both
the policy and the learning meant to improve it.

The alternative -- that training ran but the diagnostics failed to record it --
is weakly supported, because a trained residual would still change behaviour and
`rl`'s deviation from `prior_only` is exactly exploration-shaped. The two are
separated by one command on any `rl` arm: `grep -c '"loss": null' metrics.jsonl`
against a non-null count, plus `slo_masked_windows` and the count of
`"prev_transition_valid": false` in `io.jsonl`.

### 10.7 The 2026-09-03 trained-learner matrix at 10M

**Configuration.** 10M x T=2/6/10 x {`regular`, `prior_only`,
`unconstrained_rl`, `rl`} x 5 paired repeats = 60 arms, from suite root
`suite-20260902-193415`. Run on a fresh Chameleon zen3 node after the previous
node's root filesystem failed mid-lease (Section 14.5). Per-cell manifests were
regenerated from the narrowed 6-configuration sweep. **Five repeats rather than
the preregistered ten** is a deliberate lease-budget deviation, so these cells
are directional evidence, not formal acceptance.

**This is the first matrix in the project's history in which the learner
trained.** At 10M the learned arms recorded 85,740 and 92,592 full-horizon
transitions, 199,856 and 170,560 optimizer steps, nonzero residuals, and
argmax flip rates of 0.42-0.73 per level, against exactly zero on every earlier
matrix.

**Arm means.**

| metric | T | regular | prior_only | unconstrained_rl | rl |
| --- | --- | ---: | ---: | ---: | ---: |
| write amplification | 2 | 7.4199 | 8.7761 | 8.6098 | 8.5971 |
| | 6 | 8.3344 | 9.6093 | 12.5624 | 10.2394 |
| | 10 | 9.3553 | 10.6569 | 10.6242 | 10.2734 |
| point-read amplification | 2 | 7.4635 | **6.5314** | 7.4563 | 7.5868 |
| | 6 | 5.2982 | **4.3808** | 4.6027 | 4.7095 |
| | 10 | 4.5234 | **3.6512** | 3.8158 | 4.0805 |
| sorted-run seeks/scan | 2 | 9.6286 | **8.4995** | 10.1409 | 10.1694 |
| | 6 | 6.0491 | **5.0203** | 5.7345 | 5.7905 |
| | 10 | 5.5040 | **4.3427** | 4.7391 | 5.1487 |
| space amplification | 2 | 2.2710 | 2.2836 | 1.8171 | **1.6225** |
| | 6 | 1.2614 | 1.3706 | 1.2391 | **1.2346** |
| | 10 | 1.1655 | 1.2196 | 1.1865 | **1.1647** |
| stall seconds | 2 | 147.09 | 152.13 | 151.06 | 150.55 |
| | 6 | 148.55 | 153.46 | 152.88 | 154.33 |
| | 10 | 157.01 | 160.89 | 161.92 | 163.59 |

**Result against the acceptance criteria: fails in all three cells.** The
per-cell failing checks are: T=2 write amplification, point-read amplification,
scan objective, stall seconds, and four latency bounds; T=6 write amplification,
scan objective, stall seconds, and five latency bounds; T=10 write
amplification, space amplification, stall events, stall seconds, and six
latency bounds.

**Finding 1: write amplification is the universal blocker.** Every RL-family arm
compacts more than the tuned leveled baseline in every cell — `prior_only`
+18.3%/+15.3%/+13.9%, `rl` +15.9%/+22.9%/+9.8%, and `unconstrained_rl` reaching
+50.7% at T=6. Section 3.1 requires this interval strictly below zero. It is
above zero everywhere, for every arm, at every size ratio. No other criterion
matters until this one moves.

**Finding 2: the analytic prior is the strongest read policy, and it improves
with T.** Relative to `regular`, `prior_only` delivers -12.5%, -17.3% and
-19.3% point-read amplification and -11.7%, -17.0% and -21.1% sorted-run seeks
at T=2, 6 and 10, while its write penalty *shrinks* from +18.3% to +13.9%. At
T=10 that is roughly 20% fewer probes and seeks for roughly 14% more write
bytes. The T=2 figures reproduce the 2026-08-24 pooled matrix (-11.8%, -12.0%,
+18.9%) closely enough to serve as an independent replication on different
hardware and a different binary.

**Finding 3: the learned residual trades reads for space.** Against
`prior_only`, `rl` is 16.2%, 7.5% and 11.8% *worse* on point-read amplification
while improving space amplification by 29.0%, 9.9% and 4.5%. At T=10 space is
already near its floor at 1.165 and the learner still spends reads on it.

**The mechanism, from per-level compaction rates.** The learner defers
top-of-tree compaction and lets the tree grow deeper:

| cell | L0 compact rate, prior_only -> rl | populated depth, prior_only -> rl |
| --- | --- | --- |
| 10M T=2 | 0.18 -> 0.06 | L0-L8 -> L0-L11 (+3) |
| 10M T=6 | 0.24 -> 0.15 | L0-L4 -> L0-L5 (+1) |
| 10M T=10 | 0.29 -> 0.17 | L0-L3 -> L0-L4 (+1) |
| 20M T=2 | 0.24 -> 0.06 | L0-L9 -> L0-L10 (+1) |

One learned behaviour explains all three amplification results. More populated
levels means more sorted runs on the read path, which is the point-read and
seek regression. Data settling deeper is merged in larger bottom-level batches
that drop more stale versions, which is the space gain. Bytes crossing more
levels are rewritten more times, which is the extra write amplification —
deferring at the top does not reduce write work, it relocates and increases it.
The read damage tracks the depth increase: T=2 gains three levels and loses
16.2% of reads, while T=6 and T=10 gain one and lose 7.5% and 11.8%. The prior
does the opposite, front-loading compaction (L2 rates of 0.30, 0.42, 0.51) to
keep the tree shallow, and that shallowness is its read advantage.

Two caveats on those rates. They appear to be *selected* actions; L0 runs with
`RL_L0_ALLOW_DEFER=0`, so C++ promotes L0 defers to compact under `kPosture`,
and adding back the observed ~0.05 L0 override rate still leaves `rl` below the
prior. And the 20M row is from the partially completed 20M cells, included
because it shows the mechanism is not specific to the 10M scale.

**Finding 4: the criteria may be structurally infeasible as written.** Section
3.1 demands a strict Pareto improvement on write, point-read *and* scan
amplification simultaneously, against a baseline deliberately tuned to sit near
the frontier. In a leveled LSM tree, compacting less improves write
amplification and worsens reads. `rl` passes both read criteria at T=10 and
still fails the cell on write amplification. Whether the objective should
become a constrained one — improve reads subject to a bounded write-amplification
budget — is a preregistration decision that has not been made, and it
determines whether this experiment can succeed at all.

**Instrument problems exposed by this matrix.** These affect the verdict and
should be resolved before the criteria are applied again.

1. **The latency bounds are undecidable at five pairs.** All four read-latency
   checks fail in all three cells, but at T=2 their confidence intervals span
   +/-20-30% against a 2% limit. Some are genuine — T=10 shows a +14.6%
   Get-average regression on the mean — and the report does not distinguish
   undecidable from regressed.
2. **`stall_events` and `stall_seconds` use `all(delta <= 0)`.** At T=10
   `stall_events` is 5.4% better on the mean (96,639 against 102,107) and still
   fails because one pair increased. This is the same brittleness class the D4
   analysis corrected for other checks.
3. **`space_amplification` fails at T=10 on a -0.07% mean**, purely from
   interval width.
4. **`scan_amplification` sits at its mathematical floor** and passes
   vacuously, so the scan objective rests entirely on `sorted_run_seeks`. The
   preregistered scan-objective choice noted in Section 15 now has to be made.
5. **`unconstrained_rl` at T=6 reaches write amplification 12.56**, +50.7%
   against baseline and far above `rl`'s 10.24. The live SLO mask is doing
   substantially more work in that cell than elsewhere, and this is not yet
   explained.
6. **`write_latency_p95_us` (~5.6 us) is below `write_latency_avg_us`
   (~42 us).** That requires an extreme heavy tail, which ~150 s of stalls in a
   ~1,200 s run can produce, but the histogram parsing should be verified
   rather than assumed.

## 11. Current db_bench experiment pipeline

### 11.1 Matrix and workload

The operational default matrix is:

- total operations: `10M`, `20M`, `30M`, `40M`, `50M`;
- size ratios: `T=2`, `T=6`, `T=10`;
- arms: regular leveled RocksDB and constrained RL trigger-only protocol v2;
- total arms: `5 × 3 × 2 = 30`.

The same runner also accepts the deterministic oracle, prior-only trigger,
and unconstrained learned ablation. Those arms are enabled explicitly for the
gated evaluation sequence rather than added to the safe default 30-arm run.

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
not operation-identical. There is one run per arm by default. With multiple
repeats, the graph generator aggregates means and Student-t 95% error bars;
formal conclusions use paired-seed differences from the evaluator rather than
unpaired plot error bars.

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
| Protocol | 2 (fixed) |
| Deferral safety | Manifest-calibrated due-age/pressure/score/debt limits; no production decision-count budget |
| Decision/observation interval | 50 / 50 ms |
| File picker | RocksDB native `kMinOverlappingRatio` in both arms |

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

`waitforcompaction` is inside the measured command sequence and settles trigger
debt. Drain wall time, pending bytes before/after, and compaction bytes/time are
reported separately, while authoritative WAF and runtime retain the drain so a
deferring policy cannot hide unfinished work. After that measured sequence, the
script runs an explicit full compaction only as a diagnostic garbage-free size
reference; its I/O is not included in measured WAF.

Formal space amplification is the pre-reference-compaction total SST bytes
divided by `rocksdb.estimate-live-data-size` printed by the measured `stats`
phase. The post-full-compaction size remains diagnostic and is not used as the
live-logical denominator.

### 11.4 Graph output

The graph script parses RocksDB tickers, properties, event logs, and histograms
and writes a repeat-aware summary CSV plus figures covering:

- write amplification;
- point-read amplification;
- scan amplification and sorted-run seeks;
- formal space amplification;
- stall seconds;
- Get, scan, and write average/p95 latency;
- elapsed runtime.

The CSV additionally retains diagnostic p99 latency, stall-event count, live
logical bytes, workload/drain compaction bytes and time, drain duration and
pending debt, seeds, workload profile, full experiment fingerprint, and result
directory.

The default remains one repeat for operational safety. Set `REPEATS` to the
preregistered count before asserting final 95% confidence criteria.

### 11.5 Scripts added 2026-08-19 to 2026-08-26

| File | Purpose |
| --- | --- |
| `pipeline_stats.py` | `ci95`, `required_pairs`, `envelope_verdict`. One instrument shared by `07_evaluate_paired.py` and `09_evaluate_oracle_parity.py`, so the engineering gate cannot use a weaker test than the research criterion it precedes. |
| `10_run_scaling_smoke.sh` | Workload-size ladder at one size ratio, launching `03` once per size so a failure late in the ladder does not discard the earlier rungs. Sets `RL_REQUIRE_BASELINE_SLO=0` and records in `scaling_smoke.env` that the mask ran uncalibrated. |
| `11_analyze_learning.py` | The learning diagnostics: per size/ratio/level, decisions, gradient steps, TD-loss trend, residual-to-prior magnitude, and argmax flip rate. This is the script that detected the training defect; `summary.csv` cannot, because a policy that never leaves its prior produces an ordinary-looking summary row. `--stride` subsamples the fat per-decision records for a fast first look. |
| `12_report_figures.py` | Presentation graphs: one PNG per metric with all arms plotted against workload size and faceted by size ratio, plus a read/write trade-off plane and a paired-difference panel with intervals. |

### 11.6 As-run configuration, 2026-08-22 to 2026-08-24

The matrix that produced Section 10.6 differed from the operational default
matrix in Section 11.1 in three preregistered ways, all recorded before any RL
arm ran:

- sizes `1 5 10 20` rather than `10 20 30 40 50`;
- the tuned sweep narrowed from 48 configurations to 6 (`compaction_pri` fixed
  at 3, slowdown/stop fixed at 20/36, L0 trigger swept over {4, 8});
- three repeats rather than the preregistered ten, so the paired intervals are
  wide and most per-cell differences are individually undecidable.

`level_compaction_dynamic_level_bytes` is **disabled** for every arm. The library
default is `true` (`include/rocksdb/advanced_options.h:670`) but db_bench's flag
defaults to `false` and is assigned unconditionally
(`tools/db_bench_tool.cc:923, 4514`), and the pipeline never passes it. Level
targets are therefore static: `L1 = 16 MiB`, `Li = 16 MiB x T^(i-1)`. Combined
with the measured SST bytes this puts the populated depth at roughly L0-L9/L10
for T=2 at 20M, and L0-L3/L4 for T=10 -- which is why `num_levels` is pinned at
13 for every arm and size, so varying the ratio cannot silently vary the
available depth. Any entry point other than db_bench would inherit the library
default of `true` and a different level ladder.

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
- v2 analytic-prior physics, read-path signals, reward scaling, lifecycle,
  executed-action credit, SMDP discount, shared trunks, exploration, target
  updates, checkpoints, and evaluation mode;
- protocol-v2 response ordering, parser shape, reconnect, and terminal credit;
- a socket assertion that the response never contains candidate file numbers.

The RocksDB compaction picker test file contains targeted trigger tests for:

1. an RL level trigger selects the same first source file as RocksDB's native
   `FilesByCompactionPri` order;
2. a level authorization grants no authority to another level;
3. a held due permit can schedule repeated native compactions;
4. actuation selects against current RocksDB state rather than an SST identity;
5. maintenance bypass is explicitly attributed;
6. unavailable-server fallback is level-scoped;
7. a below-threshold optional authorization actuates exactly once;
8. pressure integration uses a zero-order hold; and
9. worker ticks reuse the immutable structural snapshot;
10. observations use the current independently tuned L0 trigger/slow/stop
    options, including before the first pick;
11. required/missing manifests fail conservatively;
12. latency/space masks require three entry and three recovery windows;
13. L0 slowdown opens independently due supporting levels;
14. dirty structural deadlines are edge-counted; and
15. stale eligibility and decision generations cannot mutate a superseding
    response's gate, optional token, or outcome attribution.

### 13.2 Verification performed for the 2026-08-15 scope correction

- all shell files in `scripts/dbbench_pipeline/` passed `bash -n`;
- the graph script, agent, and Python tests passed bytecode compilation;
- 69 non-socket Python tests passed in the restricted environment;
- all 7 Unix-socket end-to-end protocol-v2 tests passed when rerun with local
  socket binding permitted, for 76 passing Python tests in total;
- the modified RocksDB library and `db_bench` rebuilt successfully after the
  exact-file APIs and candidate wire fields were removed;
- all 7 focused C++ trigger/native-picker tests passed in a Debug test build;
- the resulting production `librocksdb.so` exports the level-trigger picker
  and no exact-file or candidate-preview picker symbol.

### 13.3 Static verification for the 2026-08-16 repair pass

At the user's request, this pass did not build RocksDB or `db_bench` and did
not execute any C++, Python, socket, or experiment test. The non-executing
checks performed after the final edits were:

- AST parsing of all 11 changed/new Python source and test files;
- `bash -n` parsing of all eight numbered/configured pipeline shell scripts;
- root and RocksDB-submodule `git diff --check`;
- declaration/call-site searches for the generation-aware scheduling and
  completion signatures;
- source-registration checks for every new C++ translation unit; and
- absence checks for active protocol-v3, exact-file, candidate-picker, sticky
  parent-bypass, and decision-count deferral symbols.

These checks establish source consistency only. They are not evidence of C++
compilation, runtime correctness, oracle parity, or research acceptance.

### 13.4 End-to-end proofs still required

The current design also calls for deterministic short runs proving that:

- one held due decision can schedule multiple native compactions from its own
  level, while an optional decision schedules at most one;
- no deferred level compacts through another level's authorization;
- every completed compaction maps to a decision or explicit bypass reason;
- reconnect works under the real C++/Python process pair;
- the files in every RL-triggered compaction match RocksDB's native picker,
  with no file identity supplied by Python.

Source-level tests cover the core permit, native-priority, pressure-clock,
snapshot-cache, safety-manifest, and reward mechanics. The plan's complete
fake-clock, forced-interleaving, multi-CF lifecycle, TSan, and deterministic
end-to-end matrix is not yet fully implemented or executed; a fresh
trigger-only end-to-end report remains required.

### 13.5 Verification performed 2026-08-19 to 2026-08-26

Static, before the cloud build:

- every modified C++ translation unit passes `-fsyntax-only` under the exact
  flags in `build/compile_commands.json`;
- all pipeline shell scripts pass `bash -n`; all changed Python byte-compiles;
- `09_evaluate_oracle_parity.py` was run end to end against a synthetic fixture
  built to reproduce the reported symptoms. **D1's falsifiability condition
  holds**: the L5 entries disappear while the L2 repeat-3 entry survives at
  exactly the recorded numbers (oracle 5.66636, regular 5.25032, limit
  5.512836). D7 correctly excludes both trap episodes -- one authorized only by a
  gate held open under an earlier decision, one never authorized inside its own
  window -- and reports them as denominators rather than letting them contribute
  0 us and 1,020,000 us respectively;
- `censored_tolerance_bound` was checked to move a limit only upward;
- `06_select_baseline_slo.py` was executed end to end against a fixture after a
  `NameError` and an `OverflowError` were found by running it rather than
  compiling it.

Executed on the cloud machine:

- RocksDB and `db_bench` built; the binary was confirmed to contain the D3a
  instrumentation before any experiment ran;
- ten paired 1M/T2 oracle repeats, gate verdict `undecided` with no failures;
- the 6-configuration tuned sweep at four sizes and three repeats, and twelve
  manifests generated from it;
- the 144-arm paired matrix.

Still not executed: the C++ picker tests, the socket tests, and the `rl_agent`
Python suite. The last could not run locally either -- the system interpreter has
no `torch` and the project venv has no `pytest` -- so the "exactly two
pre-existing failures" condition remains unchecked for this pass.

## 14. Implementation status after restoring trigger-only scope

| Deliverable | Status on 2026-08-16 | Notes |
| --- | --- | --- |
| Independent size ratio and L0 thresholds | **Implemented** | Wrapper flags and direct `db_bench` flags exist. |
| WAF, point RA, scan RA/seeks, space, latency avg/p95/p99, stall duration | **Implemented in source** | Scaled pipeline uses logical probes/skips/seeks, exact pre-reference SST/live-data space inputs, stall time/events, and foreground histograms. |
| Distinct raw arm/repeat directories, commands, revisions, seeds | **Implemented in source** | The repeat loop pairs workload seeds and assigns distinct policy seeds; no new runs have been collected. |
| Tuned leveled grid and preregistered selection | **Implemented in source, not run** | `05_run_baseline_sweep.sh` controls T, all three L0 thresholds, and priority independently; selection never inspects RL. |
| `baseline_slo.json` generator | **Implemented in source, not calibrated** | `06_select_baseline_slo.py` consumes shared event-time pressure episodes and emits fingerprinted latency, space, debt, and per-level limits. |
| Per-level authority and explicit reasons | **Implemented** | Picker reasons and forced paths exist. |
| Held per-level trigger gates | **Implemented in C++ source** | A due compact response permits repeated native jobs; a below-threshold optional response remains one-shot. |
| Native RocksDB file selection | **Implemented in C++ source** | Due work uses an allowed-level adapter inside the native leveled builder; optional work uses native `PickCompactionFromLevel`. Exact-file APIs are absent. |
| Trigger-only protocol v2 | **Implemented and mandatory** | Python has no candidate controller; C++ emits v2; pipeline pins v2. |
| Two-action trigger DQN and analytic prior | **Implemented; prior tests predate this pass** | Per-level compact/defer learning remains online and cold-start; current modifications still need cloud execution. |
| C++ picker authority tests | **Implemented in source** | Native priority, cross-level isolation, held due gates, optional one-shot, pressure clocks, maintenance, fallback, and cached snapshots are covered; cloud execution is pending. |
| Protocol/reconnect tests | **Implemented in source** | Current socket tests cover v2 only. |
| Oracle parity evaluator | **Implemented in source, not run** | `09_evaluate_oracle_parity.py` checks workload identity, parity envelopes, held service, due authorization, and observation health. |
| Paired CI evaluator and stress runner | **Implemented in source, not run** | `07_evaluate_paired.py` enforces balanced criteria; `08_run_stress_suites.sh` calibrates each stress profile and runs safety-only acceptance. |
| Ten paired balanced repeats and formal CIs | **Not run for current trigger code** | Required for acceptance. |
| Read-heavy/write-heavy safety suites | **Not run for current trigger code** | Required for acceptance. |
| Full tuned frontier and trigger ablations | **Not run** | The regular frontier, analytic-prior-only, and learned trigger results remain to be produced. |

### 14.1 Status update, 2026-08-26

The table above described the state before anything had been built or run. This
supersedes the rows that have since changed.

| Deliverable | Status on 2026-08-26 |
| --- | --- |
| RocksDB and `db_bench` built with the trigger controller | **Done.** Binary confirmed to carry the D3a instrumentation before any experiment ran. |
| Oracle parity gate | **Passed** for every sound decidable criterion at ten pairs. `stall_duration` still awaits its preregistered allowance. The former `no_new_oracle_stop_event` check was removed after source inspection proved that it counted repeated condition-recalculation warnings rather than distinct stop transitions. |
| Held per-level trigger gates, native file selection, per-level authority | **Executed and gate-verified.** Write amp, point probes and sorted-run seeks all inside the parity envelope as paired confidence bounds, not eyeballed. |
| Trigger latency (D3a crossing posture) | **Executed.** 511 us p50 due-to-admission at a 50 ms cadence, against ~33 ms pre-fix. |
| Tuned leveled grid and `baseline_slo.json` | **Run**, at a deliberately narrowed 6-configuration grid. Most levels at 1M and 5M come back `calibrated: false` and fall to bootstrap caps. |
| Paired matrix with prior-only, unconstrained and constrained arms | **Run** at 1/5/10/20M x T=2/6/10 x 3 repeats. Fails the acceptance criteria, and does not test a learned policy. |
| Two-action trigger DQN | **Implemented but never trained.** Zero gradient steps, zero residual, across every configuration. This is the blocking defect. |
| C++ picker tests, socket tests, `rl_agent` suite | **Still not run.** |
| Ten paired balanced repeats and formal CIs | **Not run** -- the matrix used three. |
| Read-heavy / write-heavy safety suites | **Not run.** |

### 14.2 Source-audit correction, 2026-08-30

A fresh three-pair 1M/T2 cloud preflight exposed two failures in the oracle
gate. Re-auditing the complete C++/Python repair found that the run was useful
as a diagnostic, but the evaluator's conclusions were not all sound:

- the two-sided `required_pairs` calculation used only the positive boundary,
  so a negative mean could be called failed while its interval still crossed
  the negative boundary;
- `per_level_maximum_score` was still an all-repeat invariant over a noisy
  per-run maximum. More repeats increase the probability of seeing one extreme
  under that rule; the earlier ten-pair pass did not validate the test form;
- optional-revocation classification and enforcement used different permit
  snapshots without revalidation, and oracle shadow runs had no optional
  permit, so the holdout could not see optional actions a learned policy might
  choose;
- a new policy permit was queued before its frame's safety evaluation, leaving
  a scheduler-admission window in which optional work could start before
  revocation or due work could receive policy attribution before a force;
- a completed-sample order statistic raised to a censored observation's lower
  bound was described as a censoring-aware upper tolerance bound. It is not one
  without an additional censoring model;
- resume and paired evaluation were keyed only by workload geometry, not the
  exact calibrated manifest, so regenerated limits could be paired with stale
  holdouts or completed arms, and individually matched pairs from different
  manifests could be combined into one confidence interval;
- `no_new_oracle_stop_event` counted occurrences of RocksDB's `Stopping
  writes` warning. That warning is emitted whenever a still-stopped condition
  is recalculated, so the quantity was neither a stop-transition count nor a
  valid event-rate gate.

The corrected gate uses the nearest boundary for two-sided power, treats the
worst shared level in each paired repeat as one normalized confidence-envelope
observation, revalidates the live permit under the enforcement lock, models
optional/held/release frames in non-mutating shadow classification, refuses to
claim a distribution-free bound when an episode is right-censored, and binds
calibration, holdout, resume, and the entire final paired cell to one manifest
SHA-256; the oracle gate likewise refuses to combine different experiment
fingerprints across repeats. The picker also keeps newly installed
policy/posture permits ineligible and defers their scheduler wakes until the
same response frame has passed safety classification. The formal whole-run
latency objectives remain deliberately separate from the rolling `guard_*`
limits: the former train and judge the policy; the latter are an independently
calibrated safety instrument.

The oracle report retains the stop-warning count, the actual write-stall
histogram count, and stall seconds together as diagnostics. Only stall seconds
can receive a formal parity verdict, and only after its allowance is derived
from preregistered baseline dispersion rather than from the observation being
judged.

These are source-level corrections only. The current revision must be rebuilt
and rerun on the cloud machine before the oracle bridge, live guard, or learner
health is described as verified. The older ten-pair result remains evidence for
the older binary, not validation of this revision.

### 14.3 Replay-starvation diagnosis and credit protocol repair, 2026-08-31

A fresh 5M/T2 `unconstrained_rl` diagnostic ruled out the live guard as the
immediate cause of zero learning. It produced 89,652 per-level decisions and
52,748 prior/residual comparisons. Although safety enforcement was disabled,
25,216 intervals (28.1%) were marked invalid, 89,640 pending credit windows
were cleared, and zero transitions reached replay. Replay size, optimizer
steps, residual magnitude, and learned-versus-prior action flips therefore all
remained zero. This run is failed evidence under the old credit semantics and
must not be resumed or reclassified as a learned result.

The root cause was `DQNAgent.observe()`: one rejected interval called
`_pending.clear()`, conflating whether the newest response was installed with
whether rewards remained usable by older, already-accepted four-second
windows. A stale structural response rejects only the new proposal; it does not
retroactively erase the returns of earlier installed decisions. Likewise,
budget, emergency, SLO, posture, optional-revocation, forced-release, and
maintenance actions have known effective actions and are valid off-policy DQN
experience.

The active trigger protocol remains overall version 2 but now requires
`credit_assignment_version: 2` and an exact nonzero uint64 `decision_id` echoed
by Python. C++ reports the previous installed ID and effective action for every
level. Python confirms or rejects only the newest proposal, relabels known
overrides, extends only confirmed windows, and preserves the four-second SMDP
horizon. Socket/query fallback, watchdog fallback, malformed protocol,
rejected manifests, and unknown control ownership set a frame-level hard-invalid
reason mask. Their reward is excluded; preceding valid prefixes are stored as
terminal boundary-truncated samples so bootstrapping cannot cross the unknown
interval. Clean shutdown finalizes acknowledged partial windows and discards an
unacknowledged last proposal.

`server_summary.json` and `learning_health.json` are schema 2. They separate
accepted/rejected decisions, override relabels, full-horizon,
boundary-truncated, and shutdown-terminal transitions, unresolved proposals,
hard-invalid reasons, and both acceptance and proposal accounting invariants.
Learned arms require zero protocol/hard-invalid events, balanced and quiescent
shutdown accounting, at least 32 full-horizon transitions and replay entries,
an optimizer step, a nonzero residual, and a prior/residual comparison. The 5M
screen remains stronger at 320 full-horizon transitions and 100 optimizer
steps; only the 10M checkpoint requires a learned-versus-prior greedy-action
flip.

Guard methodology remains independent. Shadow records now say
`would_override_frame`, and known predicted overrides no longer simulate replay
clears. Valid-streak and replay-warmup acceptance tests were removed because
they encoded the obsolete blanket-invalidation model. The holdout still
requires no actual intervention and at most a 1% predicted override fraction.
The existing 3.8--6.0% result therefore remains failed; repairing replay does
not make the guard ready.

Local deterministic checks cover frequent proposal rejection, override
relabeling, hard boundaries, shutdown accounting, shared replay/training, exact
C++ uint64 response parsing, and optional-revoke/forced-release effective
actions. No local RocksDB or `db_bench` build was performed. A fresh cloud
build and the staged 5M/10M diagnostics remain required before any performance
matrix is authorized.

### 14.4 Learner liveness proved; startup ownership repaired, 2026-09-01

The first fresh schema-2 5M/T2 `unconstrained_rl` preflight exited 5, but it
resolved the original question. It produced 39,624 full-horizon transitions,
a replay size of 39,960, 57,160 optimizer steps, nonzero residual advantages,
49,866 prior/residual comparisons, and 21,360 greedy-action flips. Acceptance
and proposal accounting both balanced, with no unresolved or pending decision
at shutdown. Replay starvation and zero-gradient learning are therefore fixed.

The sole failed health check was one hard-invalid frame, reported for every
level as `unknown_control_ownership`. The RocksDB log isolated it to one
startup `LevelL0FilesNum` compaction with `rl_decision_id=0` and fallback
reason 4. All later diagnostics remained at exactly one hard-invalid frame,
while socket/query fallbacks, watchdog expiries, protocol mismatches, and
fallback frames remained zero. The cause was a state-model error: before the
asynchronous worker installed its first Python frame, `rl_available=false`
meant both "still bootstrapping" and "control failed," so an ordinary native
compaction was admitted under unknown ownership.

The picker now has three explicit controller states. `bootstrap` holds ordinary
policy gates closed while waiting for the first acknowledged, structurally
current response; known maintenance, emergency, SLO, drain, and structural
deadline paths remain available and explicitly attributed. Only a real
query/protocol/watchdog failure enters `fallback` and marks reward invalid. A
successfully installed response transitions to `active`; a later successful
response can recover `fallback` directly to `active` without pretending to
bootstrap again. Structurally stale but otherwise valid responses leave the
current state unchanged, so they reject only their proposal as required by
credit-assignment schema 2.

Diagnostics now report numeric `control_state` (0 bootstrap, 1 active, 2
fallback), bootstrap gate/picker checks, activation/failure counts, and
bootstrap duration. Regression cases cover clean closed-gate startup,
first-query failure into real native fallback, and fallback recovery. The
zero-hard-invalid health gate is intentionally unchanged: the first schema-2
run remains failed evidence and must not be relabelled, resumed, or used as the
10M checkpoint. A fresh cloud rebuild and 5M/T2 rerun are required.

### 14.5 Learner verified on hardware; first trained matrix, 2026-09-05

Two source defects were found and fixed while preparing the verification run,
both in code added by the 2026-08-31 credit repair.

1. **`accepted_accounting_balanced` was not an invariant.** The identity omitted
   `discarded_zero_credit_windows`. A window confirmed on the same frame that
   raised a hard boundary carries no reward interval, so it is correctly dropped
   rather than stored as a zero-return sample, but it was still an accepted
   decision. A single hard-invalid frame therefore reported an imbalance on a
   correctly handled boundary. Reproduced against the real agent, fixed, and
   re-verified.
2. **The response watchdog could not distinguish a busy structural-refresh path
   from a dead server.** `last_valid_response_micros_` advanced only when a frame
   was *installed*, while `AcceptResponseStructure` rejects structurally stale
   frames. During a compaction storm — the `waitforcompaction` drain in
   particular — many consecutive frames are rejected while Python is perfectly
   responsive, and the watchdog would eventually fire and mark the interval
   hard-invalid, failing the zero-tolerance health gate near-deterministically.
   A separate `last_server_response_micros_` now records server liveness as soon
   as a well-formed, decision-id-matched response arrives, before the structural
   test. Watchdog expiries were zero on every subsequent run.

**Verification.** A staged preflight ran the 1M mechanical smoke, then 5M and
10M cells. All learned arms passed. At 10M: 85,740 and 92,592 full-horizon
transitions, 199,856 and 170,560 optimizer steps, nonzero residuals, balanced
accounting, and zero hard-invalid intervals, watchdog expiries, protocol
mismatches and bootstrap failures. The C++ and Python sides agree exactly:
`response_acknowledgements x 12 == accepted_decisions` and
`stale_response_rejections x 12 == rejected_decisions` on every learned arm.

**Learner health at 10M is convergent, with a heavy tail.** Per level,
`residual_scale` is 0.50-4.13 against `prior_scale` 0.34-0.49, giving
`residual_over_prior` of 3.5-7.0; `td_trend` is 0.41-0.43, with TD loss falling
14,226 -> 6,136 over the run; `mean_reward_last_quarter` is stable at
approximately -0.20 across 5M and 10M. Two caveats. `max_abs_residual_advantage`
reaches 1012-1102 against a `residual_scale` of 1.5-3.4, a 300-600x tail, so
some states produce wild Q estimates even though the aggregate converges. And
absolute TD loss remains around 6,136, several times the observed return scale.

**Do not read `max_abs_residual_advantage` as a scale.** It is a run maximum
over every sample. Using it as one produced a false 500x-divergence reading
during this analysis, corrected only by the per-level `residual_scale` and
`td_trend` fields from `11_analyze_learning.py`. Note also that with
`SHARED_TRUNK` enabled, `gradient_steps`, `td_first_quarter`, `td_last_quarter`
and `mean_reward_last_quarter` are global rather than per level, so the
per-level TD comparison used in the 2026-08-06 analysis is no longer separable
from this output.

**Hardware.** The first verification node's root filesystem failed during the
2026-09-01 run: GCC aborted with `internal compiler error: Bus error` across
many unrelated translation units, and `/usr/bin/df`, `free`, `nproc`, `grep` and
`dmesg` all returned `Input/output error` when executed. That is mmap-backed
read failure, not a source defect. The node was released and the matrix was
rerun on a fresh zen3 node. Any build interrupted by SIGBUS must be discarded
rather than resumed, since partially written object files link cleanly.

**Status on 2026-09-05.**

| Deliverable | Status |
| --- | --- |
| Credit-assignment schema v2 | **Verified on hardware** at 1M, 5M and 10M |
| Learner reaches replay and trains | **Yes** — first time in project history |
| Bootstrap/fallback state model | **Verified**: zero bootstrap failures, zero watchdog expiries |
| Oracle parity gate, current binary | **Re-run 2026-09-03**; undecided verdict with no failed checks |
| Balanced matrix, 10M x T=2/6/10 | **Run** at five repeats. Fails acceptance in every cell on write amplification |
| Balanced matrix, 20M | Started; cut after the 10M finding was consistent across three ratios |
| Ten paired repeats and formal CIs | **Not run** — five used for lease budget |
| Guard holdout readiness | **Still failing**; unchanged by the learner repair |
| Read-heavy / write-heavy safety suites | **Not run** |
| C++ picker tests, socket tests | **Still not run** |

### 14.6 Gate 0 executed on the 2026-09-03 artifacts, 2026-09-11

`docs/PATHWAYS.md` Gate 0 has four items. Items 1 and 2 are instrumentation:
the RocksDB working tree now logs `merge_schema_version` 1 fields (input and
output SST bytes per job, source level) on `compaction_finished`, and a
`compaction_release` event under the DB mutex immediately before a job runs,
carrying per-level occupancy, nominal and effective targets, and the capacity
generation. `compaction_measurements.py` parses both into per-level merge
survival `eta` (trivial moves excluded, workload/drain/whole-run views) and
release-time `phi_j`; `03_run_experiments.sh` runs it on every arm and fails
the arm if the instrument is incomplete. These need the rebuilt binary; no
historical arm carries either event.

Items 3 and 4 were executed by `14_gate0_reanalysis.py` on suite
`suite-20260902-193415` (10M x T=2/6/10 at five pairs; 20M/T2 at five pairs;
20M/T6 at one pair). The historical arms retain only `run.log`, so per-level
write bytes come from the final stats table at 0.1 GB resolution; the summed
table agrees with the exact tickers to within 0.8% on every arm. The script
uses exact `compaction_finished` output bytes whenever an arm kept
`rocksdb_LOG.txt`, which every future arm does.

**A-0.** Excess is relative to the paired `regular` run; `D_depth` is bytes
written at levels `regular` never populated.

| cell | arm | excess | D_depth (GiB) | D_eager (GiB) | depth share | levels added |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 10M T=2 | prior_only | +18.2% | 0.00 | +5.24 | 0.00 | L9 (moves only) |
| | rl | +15.8% | +1.08 | +3.48 | 0.22 | L9-L12 |
| | unconstrained_rl | +16.0% | +0.76 | +3.84 | 0.18 | L9-L12 |
| 10M T=6 | prior_only | +15.2% | 0.00 | +4.92 | 0.00 | L5 (moves only) |
| | rl | +22.8% | +0.68 | +6.68 | 0.14 | L5-L6 |
| | unconstrained_rl | +50.6% | +0.28 | +16.08 | 0.02 | L5-L6 |
| 10M T=10 | prior_only | +13.7% | 0.00 | +4.98 | 0.00 | none |
| | rl | +9.7% | 0.00 | +3.52 | 0.00 | L5, below 0.05 GB |
| | unconstrained_rl | +13.4% | 0.00 | +4.86 | 0.00 | L5, below 0.05 GB |
| 20M T=2 | prior_only | +15.2% | 0.00 | +10.12 | 0.00 | L10 (moves only) |
| | rl | +10.0% | +2.00 | +4.62 | 0.35 | L10-L12 |
| | unconstrained_rl | +16.7% | +1.76 | +9.32 | 0.18 | L10-L12 |

The prior's excess is entirely `D_eager` in every cell, and the learner's
depth share never exceeds 0.35. This is the PATHWAYS expectation made
quantitative: Pathway A can remove at most the depth share, and A-2 remains a
joint A+D criterion.

**Item 4.** `S_flow = user_bytes_written / live_logical_bytes` from the
`regular` arm, `L` the number of merge stages (populated levels minus one).

| cell | S_flow | g_flow | resident S | L | Theorem B.1 ceiling on W-1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 10M T=2 | 2.553 | 0.608 | 2.271 | 8 | 79.5% |
| 10M T=6 | 1.503 | 0.335 | 1.261 | 4 | 39.9% |
| 10M T=10 | 1.422 | 0.297 | 1.166 | 4 | 36.3% |
| 20M T=2 | 2.561 | 0.609 | 2.250 | 9 | 81.8% |

These replace the resident-S table in PATHWAYS Theorem B.1, which was a lower
bound on the ceiling. They are strict upper bounds, not expectations; B-2 and
D-4 use them against the post-Gate-3b deficit.

## 15. Current limitations and next work

**Superseded 2026-09-05.** Steps 1 through 3 of the 2026-08-26 list below were
executed and passed: the learner reaches replay, the optimizer moves the
residual, accounting is exact, no hard-invalid interval contaminates a run, and
the 10M checkpoint produced learned action flips. The matrix in Section 10.7
then ran and failed. The ordering has therefore changed again, and the immediate
work is no longer validation.

1. ~~**Decide the objective.**~~ **Done, 2026-09-05.** The strict three-way
   criterion is withdrawn and replaced by the constrained objective and Pareto-hull
   comparator preregistered in the Section 3.1 amendment, with the supporting
   proofs, gates and acceptance criteria in `docs/PATHWAYS.md`. The write
   constraint is parity rather than a budget, on the strength of Theorems A.2
   and B.1. Recorded before any run of the new programme, as required.
2. **Re-weight the reward to match whatever objective is chosen.** `Phi` carries
   physical/live space as a term to minimise, while the criteria only require
   space not to regress by more than 2%. Section 10.7 Finding 3 shows the
   learner spending reads to buy space it receives no credit for — at T=10 it
   beat the space bound by 28 percentage points of unusable headroom. Space
   should enter as a hinge penalty above the manifest limit. This is expected to
   recover most of the prior's read advantage; it will **not** fix write
   amplification.
3. **Address write amplification directly, or accept it as the cost.** No arm
   improves it in any cell. Deferring top-of-tree compaction relocates write
   work deeper rather than removing it, and adds level crossings. If the
   objective stays as written, this is the criterion that fails, and no reward
   re-weighting reaches it.
4. **Resolve the instrument problems in Section 10.7** before applying the
   criteria again: the undecidable latency bounds, the `all(delta <= 0)` stall
   checks, the vacuous `scan_amplification` floor, and the unexplained
   `unconstrained_rl` write amplification at T=6.
5. **Repair and revalidate guard calibration separately.** Unchanged by the
   learner repair and still failing its preregistered threshold.
6. Only then re-run the matrix at the preregistered ten repeats, and only then
   the stress suites and the full frontier.

**Superseded 2026-08-26.** Steps 1 through 4 of the list below were executed;
the gate passed and the matrix ran. What that produced was not a policy result
but the discovery that the learner never trained, so the ordering has changed.

The immediate work is staged validation, not model tuning and not a full
performance matrix:

1. **Rebuild the startup-state repair only on the cloud machine and run a fresh
   5M/T2
   `unconstrained_rl` diagnostic with workload seed 20001.** Do not resume the
   failed old-credit or startup-contaminated run. Require schema/credit version
   2, zero hard-invalid
   intervals, balanced accounting, at least 320 full-horizon transitions, 100
   optimizer steps, and a nonzero residual.
2. **Run the matching constrained 5M/T2 diagnostic as mechanical validation.**
   It cannot establish guard readiness while the independent holdout remains
   above the 1% predicted-override limit.
3. **Generate the small learning-analysis JSONs and then run one 10M/T2
   checkpoint.** Require TD losses, nonzero residuals, uncontaminated protocol
   accounting, and at least one prior-versus-learned greedy-action flip at 10M.
4. **Repair and revalidate guard calibration separately.** The existing
   3.8--6.0% predicted intervention rate is still a methodology failure, even
   though those known interventions no longer starve replay.
5. **Only after learner health and guard readiness both pass, re-run the
   matrix** at the preregistered ten repeats and apply the paired evaluator.
6. Resolve the remaining preregistered methodology choices: the stall allowance
   and the final scan objective. The stop-warning check has been retired as an
   invalid instrument, and `per_level_maximum_score` is now a paired worst-level
   envelope rather than an all-repeat invariant.
7. Only after a learned policy is shown to act at all, revisit D3b (the due-edge
   wake), the stress suites, and the full frontier.

The original ordering, retained because steps 5 and 6 remain valid once learning
works:

1. build RocksDB and `db_bench` on the cloud machine and execute focused Python,
   C++, socket, lifecycle, and forced-interleaving tests;
2. run ten paired 1M/T2 regular/oracle repeats and require the oracle
   parity evaluator to pass;
3. generate each workload-specific tuned leveled frontier without examining RL
   trigger outcomes, then export and review its manifest;
4. run the balanced ten-pair prior-only, unconstrained, and constrained matrix;
5. audit compaction attribution and native inputs, then apply the formal paired
   evaluator and metric-feasibility gate;
6. only after balanced acceptance, run the separately calibrated read-heavy and
   write-heavy safety suites and report the complete frontier and ablations.

Other current caveats are:

- it substitutes Put operations for explicit deletes;
- it has one workload seed/repeat by default for operational safety, although
  the runner and evaluators support the required paired repeats;
- baseline scan amplification was exactly its mathematical floor of 1.0 in the
  motivating 1M/T2 evidence, so the preregistered scan-sensitivity decision in
  the repair plan must be resolved before claiming strict scan improvement;
- the shared foreground telemetry accumulator is process-wide; the supported
  `db_bench` experiment uses one user column family, while a multi-RL-CF
  experiment needs an additional attribution audit;
- the full 33-case repair-plan concurrency/fake-clock suite is not yet present;
  source-level coverage is not a substitute for the missing forced interleaving
  and end-to-end tests;
- the old deleted scripts remain visible only as Git history and compiled
  `__pycache__` remnants; bytecode files are not a supported experiment path;
- the repository and RocksDB submodule contain uncommitted working-tree changes,
  so every experiment must record both revisions and preferably a patch or clean
  commit identifying the exact code;
- current trigger-only performance is unvalidated at the 10M–50M scale.

## 16. Guide to the existing documentation

All project-authored documents were read while producing this record. Use them
as follows.

| Document | What it contains | How to interpret it now |
| --- | --- | --- |
| `README.md` | Trigger-only synopsis, build entry points, geometry, and scaled-pipeline link. | Current entry page. |
| `docs/PATHWAYS.md` | Improvement pathways A-E, their proofs, per-pathway acceptance criteria, and the gated execution order. | **Current forward plan.** Authoritative for the post-2026-09-05 objective, the two-hull comparator, and gate costs. Nothing in it has been executed. |
| `docs/rl_l0_compaction_technical_spec.md` | Original L0 acceptance/fix specification. | Historical requirements; most mechanics were implemented and later superseded. |
| `docs/rl_l0_compaction_change_summary.md` | First working L0 implementation and early observations. | Historical v1 record. |
| `docs/project_technical_overview.md` | Detailed June L0 architecture, files, parameters, artifacts, and early results. | Historical L0-only implementation; its non-file-selection boundary remains current. |
| `docs/rl_agent_structure.md` | Original 14-feature, two-action DQN and protocol. | Historical v1 internals. |
| `docs/poc_validity_review.md` | Four major validity risks. | Historical audit whose concerns drove the rework. |
| `docs/l0_reward_attribution_plan.md` | Proposed n-step/Double-DQN attribution correction. | Historical plan; wall-clock and executed-action attribution later went further. |
| `docs/multilevel_rl_design.md` | Protocol v2, multi-level architecture, parser failure, and 2026-08-01 rework. | Authoritative trigger-protocol reference. |
| `docs/physics_informed_rl_architecture.md` | Analytic prior plus learned residual rationale and equations. | Current trigger-model lineage. |
| `docs/research_overview_and_roadmap.md` | Research landscape, history through July, and six future directions. | Mix of history and proposals; file-selection proposals are outside current scope. |
| `docs/Refined spec Claude.md` | FLSM/bounded horizontal expansion phased plan. | Proposed long-term structural program, not present code. |
| `docs/REWORK_CHANGELOG.md` | Detailed Aug. 1–5 audit, fixes, measurements, and corrections. | Essential historical evidence; tick-latency and some conclusions are explicitly retracted/disabled. |
| `docs/context.md` | Aug. 5 session handoff and then-current diagnosis. | Historical snapshot superseded in part by the Aug. 6 reward diagnosis. |
| `docs/system_guide.md` | Most complete v2 system explanation, valid ten-pair result, and hard-won rules. | Primary trigger-only system reference. |
| `docs/candidate_aware_protocol_v3.md` | Concise record of the rejected v3 exact-file prototype and its smoke result. | Historical only; explicitly not an experiment guide. |
| `workload_specs/README.md` | Balanced-workload timing and generator/parser pitfalls. | Current workload-authoring evidence. |
| `ORACLE_GATE_FIX_PLAN.md` | Revision 3. The D1-D7 defect analysis, the fixes as built, two recorded deviations from the plan as written, the preregistered experiment-design decisions, and what the evidence does not establish. | Current; the authoritative record for everything after 2026-08-18. |
| `scripts/dbbench_pipeline/README.md` | Oracle gate, tuned sweep/manifest, paired 10M–50M workflow, evaluators, stress suites, geometry, and caveats. | Current operational path. |
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

- **Level permit:** held trigger eligibility for one source level over a control
  interval; due permits can schedule repeatedly and optional permits are
  one-shot. It contains no SST identity.
- **Analytic prior:** hand-derived LSM cost/value estimate added to the learned
  Q residual.
- **Candidate:** historical v3 term for a source SST action; not part of the
  active RL action space.
- **Clean cut:** expansion needed so a compaction boundary does not split files
  sharing the same user-key boundary.
- **Compaction debt:** pending background work that has not yet been paid.
- **Compaction priority:** RocksDB heuristic ordering files within an eligible
  level; distinct from the level score and L0 triggers.
- **Deferral:** explicit decision not to compact a due/available level yet.
- **Drain:** end-of-run completion of outstanding background work so policies
  are compared at a settled state.
- **Epoch:** structural snapshot identifier retained for observation and
  attribution; it does not bind an RL action to a file.
- **FLSM:** flexible LSM design from RusKey allowing variable run arrangements
  for cheaper policy transitions; proposed, not implemented here.
- **L0:** overlapping first disk level, whose run count directly affects point
  and scan search work and write stalls.
- **Logical probe:** consideration of an SST/table by the read path, including a
  Bloom-filter rejection; independent of whether a physical disk read occurs.
- **Parametric action DQN:** historical protocol-v3 model removed with
  candidate file selection.
- **Protocol v1:** historical L0-only state and binary action.
- **Protocol v2:** current batched per-level binary compact/defer protocol,
  with RocksDB choosing the files.
- **Protocol v3:** retired exact-candidate prototype; no longer executable.
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

**Updated 2026-09-05. The paragraphs below this block predate the first
trained-learner result and are retained as the record of what was true before
it.**

The blocker that defined this project from 2026-08-24 to 2026-09-01 is gone. The
learner reaches replay, the optimizer moves the residual, the accounting
balances exactly, and the C++ and Python sides agree to the decision. Every
statement in this document about zero gradient steps and an identically zero
residual is now historical.

What replaced it is a real result, and it is negative. Across 10M at T=2, 6 and
10, with five paired repeats per cell, **no arm improves write amplification in
any cell** — the analytic prior by +13.9% to +18.3%, the learned policy by +9.8%
to +22.9%. Section 3.1 requires that interval strictly below zero, so every cell
fails, and no reward re-weighting reaches it.

Two findings survive and are worth stating on their own terms. The **analytic
prior is a genuinely good read policy and improves as the size ratio grows**:
-12.5%, -17.3% and -19.3% point-read amplification at T=2, 6 and 10, with its
write penalty shrinking from +18.3% to +13.9%. At T=10 that is roughly a fifth
of the probes and seeks removed for roughly a seventh more write bytes — a
defensible trade under a constrained objective, though not under the one
preregistered. And the **learned residual has a coherent, identifiable
strategy**: it defers top-of-tree compaction, deepens the tree by one to three
levels, and thereby trades part of the prior's read advantage for space
amplification. That single behaviour explains all three amplification outcomes,
and it is rational for the reward it was given — `Phi` treats space as a cost to
minimise, while the criteria treat it as a bound not to exceed.

So the project's open question has moved up a level. It is no longer "does the
learner learn"; it does. It is whether the objective as preregistered is
achievable at all: a strict three-way Pareto improvement over a baseline tuned
to sit near the read/write frontier, in a structure where compacting less
improves writes and worsens reads. The next decision is a research one — keep
the objective and accept that write amplification is the wall, or restate it as
a constrained problem and re-run — and it must be recorded before another
matrix, not after seeing its results.

**That decision was taken on 2026-09-05, before any run of the successor
programme.** The strict three-way criterion is withdrawn as structurally
infeasible and replaced by a constrained objective — minimise point-read
amplification subject to write parity, and to the space, latency and stall
bounds — judged against the Pareto hull of the static configuration class rather
than a single tuned point. The amendment at the head of Section 3.1 is
authoritative; `docs/PATHWAYS.md` carries the proofs that make parity rather
than a budget the defensible write target, the two-hull comparator, the five
gates and their costs. Nothing in that programme has been executed, and its own
decisive question — whether giving the controller authority over level capacity
flattens the depth growth that Section 10.7 identified as the mechanism behind
every one of its amplification results — is answered at Gate 3b, not before.

The project has progressed through four distinct technical systems:

1. a RocksDB/Tectonic workload wrapper;
2. an L0-only binary DQN proof of concept;
3. a multi-level trigger/defer controller with physics-informed residual
   learning;
4. a short-lived candidate-aware protocol-v3 prototype, rejected after its
   smoke test, followed by restoration of trigger-only protocol v2.

The most important achievement is not a favorable benchmark number. It is that
the project repaired the measurement and control path sufficiently to know what
an RL decision actually did: the metrics now represent the intended
amplifications, the workload parser produces the intended scans, the controller
does not block under the DB mutex, authority is level-scoped, due actions are
held gates rather than undersupplied pulses, fallbacks and maintenance are
explicit, and RocksDB retains sole file-selection authority.

For the 2026-08-26 revision, that claim was no longer only an argument: ten
paired 1M/T2 repeats passed every then-decidable oracle-parity check, including
due-to-admission latency at 511 us against a 5000 us limit. The 2026-08-30 audit
subsequently corrected the two-sided power calculation, the stochastic
maximum-score test form, and several safety-shadow transitions. The older run
remains useful evidence for its binary, but **the current revision is not yet
gate-verified**. It must be rebuilt and rerun on the cloud machine before a
no-op policy can again be described as behaviourally transparent.

**The most important remaining fact has changed again.** The learner still has
no gate-valid cloud result, but learner liveness is now demonstrated. The first
schema-2 5M/T2 preflight reached 39,960 replay entries and 57,160 optimizer
steps with nonzero residuals and action flips. Its one formal failure was a
single startup compaction admitted before the first asynchronous control frame,
not replay starvation. The explicit bootstrap/active/fallback state repair now
closes that ownership gap in source; a fresh 5M rerun must confirm zero hard
invalid intervals before proceeding.

So the project now has three historical results and two independent validation
blockers. The results remain: the bridge was transparent for the older binary;
the analytic prior was directionally biased toward over-compaction, buying 12%
of reads with 19% of writes across all twelve cells; and the live SLO mask
substantially replaced that policy rather than trimming it. The blockers are a
fresh schema-v2 cloud learner-health run and guard holdout readiness below the
preregistered 1% predicted-override threshold. Passing one does not waive the
other.

The next credible milestone is the staged 5M/10M cloud validation, not the
multi-day matrix: prove that full-horizon transitions reach replay, optimizer
steps move the residual, accounting remains exact, no hard-invalid interval
contaminates the run, and a 10M checkpoint produces at least one learned action
flip. Only then, and only after the separate guard criterion passes, is another
performance experiment methodologically meaningful.
