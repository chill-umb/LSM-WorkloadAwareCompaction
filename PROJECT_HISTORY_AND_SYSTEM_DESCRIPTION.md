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

The retired FLSM structural plan and the RusKey paper motivate an FLSM tree
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

### 5.4 Historical protocol-v3 prototype (retired)

A protocol-v3 prototype briefly let the controller name exact SST candidates: a
24-feature state, 18 features per candidate, up to eight candidates plus a defer
pseudo-candidate, validity masks, shared state/candidate encoders, per-level
scoring heads, and an exact-file action lease with its own completion
attribution. It was rejected on 2026-08-15 and removed from both the Python
controller and the RocksDB picker; the reasoning is in the 2026-08-15 timeline
entry and Section 10.5. None of those tensors, candidate actions, exact-file
validators, or model paths exist. **Do not reintroduce candidate or file-level
control** — RocksDB's sole authority over file selection is the project's
central invariant (Section 3.3).

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

## 7. Measurement and observability added to the project

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
| Gate launcher exited 0 after the first stage | A Gate 1 launcher wrapped stage 09 as `if ! cmd; then rc=$?`, to accept exit 2 ("undecided") as a pass. Inside that block `$?` is the status of the *negation*, always 0, so the guard read `rc=0`, failed its `-eq 2` test and ran `exit 0`. The script wrote `oracle_parity.json` and stopped silently before the sweep, with a success status. | Capture the status outside the negation: `rc=0; cmd \|\| rc=$?; [[ $rc -eq 0 \|\| $rc -eq 2 ]] \|\| exit $rc`. | Fixed 2026-09-21 before any sweep ran. The class matters more than the instance: a three-valued gate exit read through `if !` fails open, and a gate that never ran is indistinguishable from one that passed. |
| **Proactive band is relative, its value is absolute** | `RL_OPTIONAL_MIN_SCORE` is fixed at 0.10 against a score of `files / level0_file_num_compaction_trigger`. At a trigger of 2 the only below-threshold state is one file at score 0.5, permanently inside the band, so the prior fires a full L0-L1 merge on a single file (910 of 1,102 below-threshold compacts at 10M/T=2) for one run of relief it would have received one flush later anyway. | **None yet.** The change belongs in the prior's benefit term, not the threshold: a proactive action should require a minimum absolute run reduction. Pathway D, Gate 2. | Diagnosed 2026-09-20 (Section 14.10); causal confirmation run still owed. |

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
with T.** **Withdrawn 2026-09-19 (Section 14.9).** The comparison below is
against the `regular` arm alone. Measured against the Pareto hull of the static
configuration class, as C-3 requires, the prior is *dominated* at every ratio —
a single static configuration is better on write and point-read amplification
simultaneously. The figures below stand as measured; the conclusion drawn from
them does not. The paragraph is retained because it is what the 2026-09-03
matrix showed, and because the gap between the two readings is the point. Relative to `regular`, `prior_only` delivers -12.5%, -17.3% and
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

## 13. Verification

The project had Python and C++ test suites through 2026-09-13, when they were
removed: `rl_agent/tests/`, `scripts/dbbench_pipeline/tests/`, and the RL cases
inside the submodule's `compaction_picker_test.cc` and `version_set_test.cc`.
No test suite should be written. Correctness is established by the gates in
`docs/PATHWAYS.md` — oracle parity, the guard holdout, the hull criteria and the
paired acceptance evaluators — re-run on the measurement node, plus a local
`-fsyntax-only` check. The detailed verification records for the 2026-08-15
scope correction, the 2026-08-16 repair pass and the 2026-08-19 to 2026-08-26
window were removed with the suites; what survives is the coverage status in the
Section 14 tables and the dated Section 8 timeline entries.

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

### 14.7 Gate 1 executed, 2026-09-12

The first Hull-0 sweep ran on the Chameleon EPYC 4545P node: 12 static
configurations per size ratio (L0 trigger 2/4/8/16 x level-base scale
0.5/1/2, `compaction_pri` pinned to 3) at 10M for T = 2, 6 and 10, plus
`regular` cells at T = 14 and 20, three repeats to locate the hull and a
targeted top-up on retained points.

**C-1 passes.** Per-ratio hulls hold 12, 9 and 9 of 12 configurations. The
pooled cross-T hull holds 18 of 38, contributed T=2 9/12, T=6 6/12, T=10 2/12,
T=14 1/1, T=20 0/1. Extending the ratio axis above 10 adds no frontier, which
settles the question P1-14 added the cross-T cells to answer.

**C-2 verdict: fail, deferred.** It is recorded as failed rather than
reinterpreted, because the criterion had already been seen to fail; the reading
is revisited before the acceptance table is fixed, not now.
Two T=2 hull points, both at level-base scale 0.5, are unresolvable at any
repeat count up to 200: no achievable n fits the 95% interval inside half the
gap to the nearest hull neighbour. Three further points need 32, 70 and 126
repeats against 14, 14 and 3 present, which exceeds the spend limit of ten
additional arms per configuration. All hull points meet the five-repeat floor.
The pairs are recorded in `gate1/hull_indistinguishable.tsv`. This is a
measured property of the static configuration class, not an experiment failure,
but it does mean the criterion as written is unsatisfiable here.

The cause was checked rather than assumed. The two T=6 points at level-base
scale 0.5, L0 triggers 16 and 8, contain no outlier run: across 14 and 5
repeats their stall time spans 52 to 60 seconds and their sorted-run seeks 6.95
to 7.09. The two configurations differ by 0.0067 in write amplification, 0.080%
of the mean, against per-run standard deviations of 0.145% and 0.120%. The gap
between two distinct hull points is therefore 0.55 and 0.66 of a single
standard deviation, and resolving it to C-2's standard would take 207 and 143
repeats. The frontier is denser than the measurement resolves.

That same reproducibility is what gives the acceptance criteria their power,
since C-2 is the only criterion comparing hull points with one another. At five
repeats the full 95% interval is 0.30 to 0.36% of the mean on write
amplification and 1.30 to 2.85% on point-read; at ten repeats, 0.17 to 0.21%
and 0.75 to 1.64%. Against the frozen 2% margins, write amplification has about
six times the headroom at five repeats, while point-read is the binding axis:
the worse configuration exceeds a 2% margin at five repeats and falls inside it
at ten. This is the first measured justification for the preregistered ten
repeats, which until now rested on convention.

**C-5 is not satisfied.** The capacity-space curves are measured at every
ratio, but `capacity_s_max` is `None` throughout: the sweep varies
`max_bytes_for_level_base`, which moves L0's target as well as the deep-level
targets, so the curve cannot bound a per-level capacity actuator. C-5 selects
the (cell, rung) pairs for Gate 3b and must be closed before it.

**C-5 closed on 2026-09-13.** The per-level actuator was exposed statically
through `RL_STATIC_CAPACITY_SCALES`, applied in `PrepareForVersionAppend` after
the base ladder exists, with L0 and the output-only final level pinned to 1.0
and a controller-set vector always winning. Three repeats per (ratio, scale) at
10M, each arm's applied vector verified against its request from the Gate-0
release events. Measured against a garbage-free denominator, expansion to
s = 2.0 costs at most 1.9% of settled space and is negative at T = 2 and
T = 10, giving s_max = 2.0 at the 2% rung in all three cells. T = 6 is
genuinely non-monotone, +0.72% then +0.15% with non-overlapping intervals,
which does not affect the bound because s_max already requires every scale
below it to be affordable.

The calibration also exposed a defect in the space metric. The frozen
definition divides settled bytes by `estimate-live-data-size`, and that
estimate moved by up to 4.8% across nine configurations whose true live data
was identical at 3.02 GB, while settled bytes moved under 2%. The estimate is
sensitive to how data is distributed across levels, which is what the capacity
actuator changes and what the learner changes. Every space amplification number
in this project carries that sensitivity, including the frozen acceptance
constraint. Replacing the denominator with the measured garbage-free size is a
contract amendment and has not been taken.

**C-3 and C-6 are not evaluable.** Both require `prior_only`, which requires a
guard-calibrated manifest, which is the Pathway E-1 gate still failing.

**Three defects were found and fixed while running this gate**, all in
analysis rather than in the controller. `compaction_measurements.py` tested
`merge_success is not True`, but RocksDB's `JSONWriter` has no boolean
overload, so `status.ok()` reaches the log as the integer 1 and every
compaction was read as failed; the same defect silently misfiled drain-phase
compactions as workload phase. `frontier_analysis.py` embedded every run's full
Gate-0 payload in the hull report, producing 1.8 GB files, and demanded the
full 0.5/1/2 base-scale ladder from the cross-T cells, which are deliberately
run at scale 1 only. Stage 06's fingerprint parser also rejected the current
fingerprint outright, which would have blocked every manifest.

**The cost model is stale.** PATHWAYS budgets Gate 1 at 48 hours from the
2026-09-03 Zen 3 measurements. The per-arm `db_bench` phase here is 173.6 s at
10M T=2. The whole cost table and the lease split need recalibrating.

### 14.8 Guard calibration repaired, E-1 recorded failed, 2026-09-14

The guard holdout was diagnosed, three defects in the calibration chain were
fixed, and the gate still failed. E-1 is recorded as **failed**. The
consequence PATHWAYS attaches to that — cutting the guard from the paper — is
deferred pending a design decision. Full evidence is in `docs/PATHWAYS.md`
under Pathway E; this is the summary and what it costs.

**Three defects were real and are fixed.**

1. `censored_tolerance_bound` returned no bound whenever a single truncated
   episode appeared in a sample, which forced the hard-coded bootstrap caps
   Pathway E's implementation item 2 exists to remove. The behaviour was
   introduced by commit `f77bfb8`; an older manifest on the same workload shows
   the previous code calibrating through one truncated episode per level. The
   C++ side documents one truncated record per level per phase as *expected*
   (`compaction_pressure_observer.h:68`), so the rule fired on a condition the
   producer guarantees. It now charges each censored episode to the tail when
   choosing its order-statistic rank, which is distribution-free valid without a
   censoring model. Every populated level recalibrated: 8/8, 4/4 and 4/4 at
   T = 2, 6 and 10, against 5 of 12 before. `allowed_pending_debt_ratio` moved
   from the hard-coded 0.50 floor to a measured 4.141.
2. The per-level limits were calibrated on the wrong statistic. A tolerance
   bound over episode durations answers how many *episodes* are long; the
   picker tests a level's current due age on every 50 ms frame, and frames
   sample due time, so a rare long episode covers hundreds of consecutive
   frames. Due age, integrated pressure and score are now calibrated jointly by
   replaying every baseline run at the observation cadence, at the same
   override fraction E-1 scores. They must be joint because the force condition
   is a disjunction: an interim revision that converted only due age and
   pressure would have handed the entire override rate to the untouched score
   term.
3. Stage 06 called `collect_arm`, which reads each `run.log` and
   `rocksdb_LOG.txt` in full, on every arm before filtering by size ratio, so
   each of three invocations rescanned the whole sweep. It now pre-filters on
   `metadata.env`.

**The gate still fails, and the cause is measured.** At 10M/T=2, seeds
10001–10003, the override fraction is 0.363, 0.377 and 0.342 against a 0.01
limit and a 0.0099 prediction. Of 2,345 scored frames in the first run, 851
overrode and 850 had whole-tree debt at or above its limit; none overrode
without a level being due. Debt is a *global* term — one breach forces every
due level — and it was the only term left on an episode-derived bound after the
frame conversion.

**Recalibrating debt does not fix it.** The debt ratio does not have a tail to
place a quantile in; during the backlog it sits on a plateau, its p50 over due
frames 4.849 and its p90 through maximum all 4.899. Cross-validation across
seeds of the identical configuration — fitted on the three `oracle` calibration
runs, evaluated on the three `oracle` holdout runs — gives 0.0098 and 0.1804, an
18× transfer gap, both computed as upper bounds so no within-episode model can
rescue them. An interim diagnosis blaming the `regular`-to-`oracle` transfer is
wrong and is retracted.

**What the number measures.** Decomposed into maximal consecutive stretches the
override frames form exactly **one** event in each of the three runs, of 851,
891 and 806 frames. The guard engages once per run and stays engaged for about
36% of it, because the tree enters a single sustained backlog — debt plateaued
near 4.88, a deep level continuously due, consistent with the
`longest_due_run_fraction` of roughly 50% per run that the calibration reports,
and the same event behind the debt p99 of 4.141 and maximum of 9.485. How often
the guard engages is 1 and is exactly stable; how long it stays engaged is the
quantity E-1 thresholds, and it swings 0.342–0.377 between seeds and 18× under
cross-validation.

**Recorded as failed rather than reinterpreted.** An amendment to count override
events was considered and refused: the criterion had already been seen to fail,
one event per run was observed, and any bound set now would be fitted to that
observation. This follows the C-2 precedent in Section 14.7. Two instruments on
this gate were already amended after failures — E-2's denominator, and the
limits' unit — which argues for more caution here, not less.

**All three cells scored, 2026-09-17.** The T=6 and T=10 holdout runs had
completed but were never scored, because the validator exits non-zero on T=2 and
`set -Eeuo pipefail` aborts the loop. Scored from the existing artifacts:

| cell | override fraction | over limit | debt term | replay agreement |
| --- | --- | ---: | ---: | ---: |
| T=2 | 0.363, 0.377, 0.342 | 36x | 0.362 | 1.000 |
| T=6 | 0.142, 0.154, 0.149 | 15x | 0.142 | 0.908 |
| T=10 | 0.193, 0.189, 0.191 | 19x | **0.000** | 0.857 |

**The debt diagnosis above holds only at T=2 and T=6.** At T=10 the debt term
never fires — the tree is shallow and pending work never reaches the limit — yet
19% of frames still override, and the offline replay misses 14.4% of them, so
the cause is a term with no per-frame record. `l0_slowdown` is the leading
candidate and is unconfirmed, because L0 file counts are not logged anywhere.
Reason composition also varies by ratio: `kSLO` is roughly 2% of overrides at
T=2, 56% at T=6 and 25% at T=10. Three cells, three mechanisms, one of them
unidentified.

**Distortion of the arm under test is zero, measured.** Across all nine runs,
the number of override frames with no due level is zero. The holdout arm is
`oracle`, whose action is `score >= 1.0 ? compact : defer`, and the guard forces
only when `item.due`, which is the same predicate on the same snapshot. So every
override lands on a level the oracle had already chosen to compact, and
`revoke_optional` cannot fire because it requires the level not to be due. The
conditional override rate of Corollary E.2 is identically zero, which means
E-1's 0.14-0.38 measures agreement between the guard and its holdout arm rather
than the shield's influence on a policy.

**The pre-ready window is exactly the bulk load.** `filluniquerandom` reports
58.801 s and `guard_ready` arrives at +58.8 s; the load phase issues no Gets or
scans, so the latency histograms never reach the minimum sample count. E-1
already scores ready frames only, so the statistic is unaffected — but forcing
is not gated on readiness. `due_age`, `pressure`, `score` and `debt` evaluate
throughout, so a `prior_only` or `rl` arm running with enforcement enabled would
have its policy overridden for the first 29% of every run under no criterion.
That is a live behaviour of the learned arms and needs a ruling independently of
this gate.

**Retraction: the offline replay is reliable only for the debt term.** The
per-frame replay used to attribute overrides to individual terms of the force
condition was validated at T=2, where it reproduced the guard's classification
on all 2,345 scored frames with no false positives or negatives. That test
proved far less than it appeared to. At T=2 the debt term was true on 850 of the
851 override frames, and debt is read straight out of each episode's
`max_pending_debt_ratio` with no modelling at all. The exact agreement therefore
tested the one path that involves no inference, while the modelled per-level
paths -- due age, the linear-ramp score, and the ramp-integral pressure -- were
never exercised.

They fail once debt goes quiet. At T=6 the replay claims a per-level breach on
163 frames carrying `reason_mask: 0`, meaning the guard did not override at all;
at T=10 it misses 250 override frames, of which 182 carry `reason_mask: 2`
(`kBudget`, and therefore due age, pressure or score, since debt is zero there
and `l0_slowdown` never fires) and 68 carry `reason_mask: 64` (an SLO breach the
replay does not model). Wrong in both directions on the same terms, so this is
not a one-sided modelling error that a corrected exponent would fix. A sticky-
retention variant was tested and changed nothing, because the per-level
conditions fire at the end of due episodes and the level goes healthy
immediately after.

**Consequently, no per-level attribution from this replay should be used.** That
includes the due-age/pressure/score breakdown reported above and the
cross-validated figures for a score-only guard (0.0085-0.0195 held out). The
proposal to disable the time-based terms and rest the guard on `score` was built
on those figures and **is withdrawn** pending measurement from the guard's own
state.

What survives is everything read directly rather than reconstructed: the debt
values themselves; `l0_slowdown` never firing, established from the `lsm_state`
array that every `flush_finished` and `compaction_finished` event already
carries; every override landing on an already-due level in all nine runs; and
the `guard_ready` boundary coinciding with the end of `filluniquerandom`.

**Resolution path.** `rl_agent/server.py` writes `io.jsonl` per frame with
`"input": raw_state` -- the guard's own per-level view, including file counts --
and `"output": {"action": ...}`, the policy's chosen action. Replaying against
that removes the reconstruction from the loop and simultaneously supplies the
conditional override rate. The `oracle` arm does not query the server and so has
no such log, which is why the reconstruction existed. An
`unconstrained_prior_only` arm was added to `03_run_experiments.sh` on
2026-09-17 to close this: the analytic prior with the guard classifying but not
enforcing, writing `safety_shadow.jsonl` alongside `io.jsonl`. It is a shell
change only, so `dbbench_sha256` is unchanged and the Gate 1 hull stands.

**Gate 1 standing after this entry.** C-1 passes, C-5 is closed, **C-2 and E-1
are recorded failed**, and E-2 passes under an amended denominator. C-3 and C-6
remain unevaluable, because both need `prior_only`, which needs a
guard-calibrated manifest.

### 14.9 Gate 1 completed; E-1's conditional rate measured, 2026-09-19

The `unconstrained_prior_only` arm added on 2026-09-17 was run at 10M for
T = 2, 6 and 10, ten repeats per cell, thirty arms. It is the analytic prior
with the guard classifying but not enforcing, writing `safety_shadow.jsonl`
alongside `io.jsonl`. All thirty passed the learning-health gate, including
`eval_mode`, `zero_train_steps` and `zero_residual`, so the logged actions are
the frozen prior, and `no_reward_invalid_intervals` rules out fallback
contamination. Nothing was perturbed: `enforcement_enabled` is false and
`intervention_applied` is zero on every frame of every run.

**The guard changes almost nothing, and E-1 measures the wrong quantity.**

| cell | marginal override (E-1) | force changes an action | revoke changes an action |
| --- | ---: | ---: | ---: |
| T=2 | 0.3356 [0.321, 0.351] | 0.0009 | 0 |
| T=6 | 0.1567 [0.144, 0.173] | 0.0000 | 0 |
| T=10 | 0.1983 [0.189, 0.206] | 0.0000 | 0 |

E-1 fails by 34x, 16x and 20x. The conditional rate that Corollary E.2 asks
for instead passes its 1% limit by more than an order of magnitude in every
cell. Same guard, same frames, same runs.

The force branch can only change an action where the policy deferred a level
that was already due, and the prior defers a due level on 0.8% to 1.3% of due
decisions. **`revoke_optional` never fired at all**, which is established
rather than bounded: between 40% and 67% of ready frames contain no due level,
force requires a due level, so on those frames a revoke is the only thing that
could raise an override — and across roughly thirty thousand such frames in
thirty runs, not one did. The write trip is sticky at three windows in and
three out, so it could not have fired and avoided all of them. The latency
margins agree: the guard's write limits are 139.7 us average and 2.14 ms p95,
against a measured Put average of 12.8 us and a P99.99 of 433 us.

**The shield's write-side sensor reads the wrong variable.** Its only lever
against the prior is `revoke_optional`, gated by `prohibit_optional`, which is
driven by write *latency* (`rl_safety_manifest.cc:336`). The prior's write
latency has eleven times its headroom. The prior's actual problem is write
amplification at roughly +15%, which is the binding constraint of the whole
objective, and for which the shield has no input. It sits idle through exactly
the behaviour it exists to restrain. This is sharper than Pathway E's own
complaint, which is about the action set being asymmetric; the measurement says
the sensor is asymmetric too, and it connects Pathway E to Pathway D.

**Two corrections to Section 14.8.** Both came from generalising three T=2
runs.

1. *"Exactly one event per run" holds only at T=2.* Decomposed into maximal
   consecutive stretches, override frames form 1.3 events per run at T=2, but
   **22.2 at T=6 and 14.9 at T=10**. The single-sustained-backlog account of
   the statistic does not survive the other two ratios.
2. *Debt is not the dominant term.* Section 14.8 attributed T=2 almost entirely
   to the debt term, 850 of 851 frames. That figure came from each episode's
   `max_pending_debt_ratio`, a per-episode maximum. Read per frame, from the
   quantity `DebtRatioBreach` actually compares, debt is true on **11%** of
   override frames at T=2 and does not reach the top five at T=6 or T=10.
   `pressure` dominates in every cell. Between 42% and 53% of override frames
   have no instantaneous term true at all, which is consistent with the guard's
   sticky retention branch carrying the force.

**E-5 is satisfied** — the conditional override rate is now reported per cell.
**E-1 remains recorded as failed.** Nothing here changes that, and the
amendment question is unchanged: the criterion was seen to fail before any of
this was measured, which is the objection that kept C-2 and E-1 recorded as
failed in the first place.

**C-3 and C-6, and with them Gate 1, are now decided — both fail.**

Because the guard changes at most 0.09% of frames, `unconstrained_prior_only`
and `prior_only` are the same policy on this workload, so these runs stand in
for the `prior_only` arm the two criteria require. That substitution is now
evidenced rather than assumed, and it is what unblocks criteria that Section
14.7 recorded as unevaluable.

| cell | W | R | S | C-3 | dominator CI, hull minus policy |
| --- | ---: | ---: | ---: | --- | --- |
| T=2 | 9.5621 | 7.4761 | 2.1798 | **dominated** | W [-7.02%, -6.38%], R [-4.34%, -3.66%] |
| T=6 | 9.8464 | 5.2476 | 1.3370 | **dominated** | W [-9.05%, -8.57%], R [-3.72%, -0.92%] |
| T=10 | 10.4058 | 4.8861 | 1.2125 | **dominated** | W [-6.49%, -5.58%], R [-5.74%, -3.81%] |

One static configuration in each cell is better than the analytic prior on
write amplification **and** point-read amplification simultaneously, with both
paired intervals strictly below zero. **C-6 fails the same way**: the cross-T
pooled hull over T in {2, 6, 10, 14, 20} holds 18 of 38 points, reproducing
Section 14.7's figure exactly, and the policy is dominated within it.

**Consequently Finding 2 is withdrawn**, which is the consequence Pathway C
attaches to C-3. The prior's -12.5%, -17.3% and -19.3% point-read improvements
were measured against a single tuned baseline. Against the configuration class
they buy nothing: tuning the L0 trigger and the level base reaches a better
point on both axes at once. This is precisely what Pathway C and Proposition
C.1 were built to test, and it is the first quantitative demonstration in this
project that the single-baseline comparator overstates a result.

**What this implies for C-4.** Hull-s is built from the static class plus
capacity-expanded configurations, so its configuration set contains Hull-0's.
If a point of Hull-0 dominates a policy then either that point is in Hull-s or
something in Hull-s dominates it, and domination is transitive — so anything
dominated by Hull-0 is dominated by Hull-s. That applies to the prior directly.
It does not transfer to `rl`, which is a different policy once it carries a
capacity action, but it does fix the bar: Gate 3b must close a gap of 6 to 9%
on write and 4 to 6% on point-read, on both axes at once, against a comparator
at least as strong as the one the prior lost to.

**Scope.** This workload has almost no resident garbage, and Gate 0 measured
what that costs: the Theorem B.1 ceiling on W-1 is 79.5%, 39.9% and 36.3% at
T = 2, 6 and 10. Compaction's write-side benefit is dropping stale versions, so
where there are none, compacting more can only add write bytes. The negative
result above is specific to a near-garbage-free workload and should be reported
with that scope, not without it. Pathway B and Gate 4 are the test of whether
it generalises.

**Gate 1 standing.** C-1 passes. C-5 is closed at s_max = 2.0. **C-2, C-3 and
C-6 are recorded failed.** E-2 passes under the amended denominator, E-5 is
satisfied, and E-1 is recorded failed. Gate 1 is complete and not passed.

**Three instrument defects were found and fixed, all in analysis.**

1. `10_validate_learning_health.py` selected the frozen-learner arm family with
   an exact equality on `prior_only`, while `03_run_experiments.sh` selects it
   with a `*prior_only` glob. The new arm was rejected by `argparse`, and
   merely adding it to the choices would have routed it into the training
   branch and failed every run on `training_mode`. Both now use the glob.
2. `04_generate_graphs.py` read the settled SST total only from `sizes.env`,
   which `03` writes after the measured phase and which is therefore the one
   artifact an interrupted or partially archived arm can lack. It now falls
   back to `rocksdb.total-sst-files-size`, already parsed out of `run.log`.
   The two are the same number: checked across all 36 Gate-1 configurations
   against the node-computed values, relative error 0. There is no fallback for
   `sst_bytes_after_full_compaction`, and none is needed, because the frozen
   space definition does not use it.
3. `frontier_analysis.py` retained every run's parsed Gate-0 payload in order
   to read three small fields from it. The sweep's payloads total 7.5 GB across
   240 files, so a cross-T run exhausted memory and was killed. It now reduces
   each payload at load. Cross-T peak resident memory is 1.1 GB. This is the
   same payload that Section 14.7 records as having made the emitted report
   1.8 GB; that was trimmed in the output and left in memory.

**Stage 17 added.** `17_analyze_shadow_overrides.py` computes the marginal and
conditional override rates, the event decomposition, and the per-term
breakdown, from `safety_shadow.jsonl` joined to `io.jsonl`. It does not
reproduce the force condition — that is what the retracted replay did — and
every quantity it reports is read from a log. Two properties are worth knowing.
The join has no shared frame identifier, because the two logs carry different
clocks, so it is recovered from the frame duration, which both record and which
jitters per tick: the correct offset matches every frame and the next best
matches 13%. An earlier revision keyed on `observed_levels`, which is constant
for a whole run, so every offset scored perfectly and the search returned
whichever it tried first; a checksum that cannot discriminate must not report
confidence. And the counts of deferred-due and compacted-non-due decisions are
computed from `io.jsonl` alone, so they bound both shield directions whatever
the join does.

### 14.10 Policy contribution isolated; the proactive-band defect, 2026-09-20

Section 14.9 records `prior_only` as dominated by Hull-0 in all three cells.
That verdict stands, but it conflates two different failures: a trigger policy
that contributes nothing, and a trigger policy handed a base configuration
chosen by a rule optimising for something else. Stage 06 selects minimum-space
then lowest-runtime, a procedure `docs/PATHWAYS.md` Pathway C itself describes
as one that "never explores trading write bandwidth for reads" — so the prior
was placed at a point on the frontier that its own mechanism was not aimed at.

**The static twin.** Every configuration in the Gate 1 sweep is plain RocksDB,
including the exact configuration each prior arm ran on. Comparing the prior
against that twin — same L0 trigger, same level base, controller versus no
controller — measures the policy's contribution with the tuning question held
fixed. It is a different question from C-3 and does not re-score it.

Computed from the existing artifacts with `frontier_analysis.paired_comparison`,
the same instrument the hull report uses; policy minus twin, so negative means
the policy improved that axis.

| cell | config | pairs | write amplification | point-read amplification |
| --- | --- | ---: | ---: | ---: |
| 10M T=2 | L0 trigger 2, base 16 MiB | 5 | **+7.18%** [+6.81, +7.55] | **+4.17%** [+3.80, +4.53] |
| 10M T=6 | L0 trigger 4, base 16 MiB | 3 | +14.96% [+14.34, +15.58] | **-8.69%** [-11.35, -6.04] |
| 10M T=10 | L0 trigger 4, base 16 MiB | 5 | +9.34% [+8.65, +10.04] | **-6.11%** [-6.86, -5.36] |

Every interval excludes zero. At T=6 and T=10 the prior makes a real read/write
trade and loses the hull comparison because the static class reaches a better
trade without a controller. **At T=2 the prior is worse than doing nothing at
its own configuration, on both axes**, which no re-basing can repair. T=6 rests
on three pairs, the twin's repeat count, and is below the five-repeat floor.

**The mechanism at T=2 is the proactive band.** A below-threshold `compact`
action is offered whenever a level's score reaches `RL_OPTIONAL_MIN_SCORE`,
fixed at 0.10. L0's score is `files / level0_file_num_compaction_trigger`. At
T=2 the manifest selected trigger 2, so the only below-threshold state that
exists is one file at score 0.5 — permanently inside the band. From `io.jsonl`
at 10M/T=2, of 3,410 L0 decision frames the prior compacted 1,018 while due and
1,102 while below threshold, 910 of the latter with exactly one file in L0.
That is +58% more L0 compaction jobs than the twin (499 against 315) while
every deeper level is statistically identical (L1 3855/3919, L2 2476/2567,
L3 1470/1376).

A proactive L0 compaction pays the same L1 overlap read and write whatever the
L0 input, so its value is the number of sorted runs it removes. At trigger 4
compacting at one file holds L0 at 1 instead of 4 and removes three runs, which
is the measured read gain at T=6 and T=10. At trigger 2 it removes **one** run,
on a tree already nine levels deep, and buys that run one flush earlier than
native would have delivered it anyway — while paying a full L1 merge for half
the input. The extra traffic also pushes the tree one level deeper, maximum
populated depth 10 against the twin's 9, which adds probes. Both axes regress,
in the direction and roughly the magnitude measured.

**The defect is that the band is relative while its value is absolute.**
`RL_OPTIONAL_MIN_SCORE` is one constant shared by the C++ admission gate and
the Python action mask (closed 2026-08-16 as audited deviation 4), which keeps
the two sides consistent but leaves the threshold expressed in units of the
trigger. When the trigger is small the score band and the run reduction
decouple entirely. The prior's own cost model does not catch it either:
`multilevel.py` prices `work_now` as bytes rewritten per byte of progress,
correctly high here, but `readamp_relief` scales with L0's overlapping file
count without asking whether native RocksDB was about to remove the same run
one flush later. **No fix has been made.** The corresponding change belongs in
the prior's benefit term rather than the threshold — a proactive action should
require a minimum absolute run reduction — and lands with the Pathway D reward
work at Gate 2.

**Confirmation still owed.** The band's causal role is inferred from the frame
counts, not yet isolated. Re-running 10M/T=2 with `RL_OPTIONAL_MIN_SCORE` above
0.5, which empties the proactive band at trigger 2, is three repeats and about
fifteen minutes; if write and point-read both move toward the twin, the
diagnosis is confirmed. Two caveats on the frame analysis: it rests on one run
per cell, and a minority of frames report `files` and `score` inconsistently
(25 files at score 0.5, about 20 frames per run), which is snapshot skew
between the two reads and too small to move the counts but means the per-frame
join is not exact.

### 14.11 Objective-consistency audit and Gate 2 repairs, 2026-09-20

Every line of the control and measurement chains was audited against
`docs/PATHWAYS.md` before the programme is re-run from the start. The
measurement chain (tickers, `db_bench`, `04`, `07`) was contract-consistent;
the control chain was not. Findings and the repairs made the same day, all
recorded in PATHWAYS as P1c before any run:

- **The learner optimised the wrong function.** The live reward
  (`multilevel._global_reward`; `reward.py` was an unreachable v1 path)
  charged write amplification as a run-to-date ratio with no action
  gradient and no `W_base`, minimised space linearly (Finding 3), priced
  reads in trigger-relative units, and carried the withdrawn scan metric.
  Replaced by the Pathway D form: point probes per Get as a rate; hinges
  above the manifest references for W (10 s window), S (the rung), latency
  (avg and p99, previously p95), sorted-run seeks and stall fraction; dual
  ascent on the multipliers, logged per frame; shaping over absolute runs.
- **The prior's deep-level read term grew with fullness** — bytes merged per
  scan, the withdrawn metric — while `work_now` stayed flat, which is the
  top-of-tree eagerness A-0 attributes the whole prior write excess to. Deep
  relief is now zero, with a depth charge for output into an empty level. L0
  relief is net of what native would remove one flush later (minimum
  reduction two runs; the 14.10 mechanism), and the flush size is measured
  rather than read from the 16 MiB L1 target against a 2 MiB write buffer.
- **The controller acted during the bulk load** under an uncalibrated safety
  envelope; its load-phase bytes entered the paired W. `db_bench` now
  suspends control across the load (`rlsuspend`/`rlresume`,
  `ActionReason::kSuspended`) and resets statistics before `mixgraph`.
- **`sorted_run_seeks` counted table seeks**, so a level cut into more files
  read as more runs. It now counts one per L0 file and one per deeper level.
- **Cadence constants were sized for a dead 185-decision budget.** The
  normalizer froze 2.5 s in, before any read-path feature had a value, so
  the state carried no information about R. Freeze and seconds-since are
  wall-clock now.
- **Evaluator gaps.** `frontier_analysis` compared on (W, R) with no space
  bound, so a static point infeasible at the rung could dominate; a
  dominator must now be inside the policy's space bound (C-6 as written).
  `run_full_experiment.sh` never passed `--space-margin`, and `03` never
  stamped `space_relative_margin`, so the paired stage could not run.
  `11_analyze_learning` gains the D-2 return scale and the λ trajectories.
- **Dead code removed:** `reward.py`, `metric_formulas.py`, the v1 socket
  path and its stray `DQNAgent`, the per-level `_potential`/`_compute_reward`
  formula and its weights. Override replay wording reconciled: relabel and
  keep (P1c-21).

`docs/RESEARCH_OBJECTIVE_CONTRACT.md` was removed as stale; the JSON contract
remains the machine-readable block and was amended in place (P1c). RocksDB
moves to `6ad9f6b79` (parent `71627a1cd`, base `7ea2d73` preserved). No gate
was re-scored; Gate 1's recorded verdicts stand as history and are
superseded by the re-run.

### 14.12 Pre-Gate-2 artifacts deprecated, 2026-09-20

A lockstep defect left from the Gate 2 pass: `rl_safety_manifest.cc` still
required `metric_definitions_version` `trigger-v2-logical-v2` while
`03_run_experiments.sh`, both stage-06 generators and `rl_agent/config.py` had
moved to `v3`. The C++ `Parse` rejects the manifest, `Load` sets `invalid_`, and
`MarkRewardInvalid(kRejectedManifest)` follows, so every learned arm would have
completed its run and then failed `no_reward_invalid_intervals`. The `regular`
and experiment-phase `oracle` arms load no manifest and would have passed, so
the whole Hull-0 sweep could have finished before the defect surfaced. Fixed to
`v3`; both RL translation units pass `-fsyntax-only` against the legacy compile
database.

Every artifact produced before this rebuild is invalidated, by three changes at
once: a new `db_bench` binary, the in-place contract amendment, and a measured
phase that now excludes the bulk load. The first two change the experiment
fingerprint outright; the third changes what every byte ratio means, since the
`filluniquerandom` traffic is no longer counted. `SORTED_RUN_SEEK` also changed
units, and the latency constraint moved from p95 to p99.

`results/`, `gate1/`, `baseline_selection/` and the six `baseline_slo*` trees —
91 GB — were therefore moved to `deprecated/pre-gate2-2026-09-20/`, which
`.gitignore` excludes. **Structure is preserved**, so every path cited in this
document resolves by prefixing that directory: the Section 14.9 and 14.10
evidence is `deprecated/pre-gate2-2026-09-20/results/prior_shadow/`, and the
Section 14.7 hull is `deprecated/pre-gate2-2026-09-20/gate1/`. Nothing was
deleted. The Gate 1 hull, the guard calibration and the C-3 verdict all stand
as records of the binary that produced them and must be re-measured before any
criterion is evaluated against the rebuilt one.

### 14.13 Workload changed to UDB `Assoc`; PATHWAYS split, 2026-09-20

**The programme's workload is no longer uniform.** Every arm of every gate now
runs the skewed workload of Pathway B1 — the UDB `Assoc` column family of Cao et
al., FAST 2020 — and the uniform family that produced every result up to
2026-09-19 is retained as a named control (`WORKLOAD_SKEW=0`) rather than as the
measurement workload. B1 is marked Done in `docs/PATHWAYS.md`. B2 (phases) and
B3 (deletes) are not implemented and stay in Gate 2.

The reason is not that C-3 failed. It is that **the published plan ran Gate 1 on
`uniform` and Gate 4 on `skew`, which is an invalid comparison**: a hull measured
on one workload is not a comparator for a policy measured on another. PATHWAYS
already states the knob form of that rule and Gate 3c already concedes it for
capacity by re-measuring Hull-s; nobody had written down the workload form. The
second reason is that uniform random keys are not a workload anyone runs, which
is why almost no garbage accumulates and why Section 14.9 had to scope its
negative result as workload-specific. The C-3 failure is **not re-scored** and
the uniform result is not withdrawn; what changes is which workload the next
programme measures.

The decision, the predictions recorded before the run — including that the
uniform family will fail to reach write parity by Theorem B.1 at the Gate 0
ceilings of 79.5%, 39.9% and 36.3% at T = 2, 6 and 10 — and the falsification
condition are in `docs/PREREGISTRATION.md` as D-1.

**Two deliberate departures from the published fit.** `value_theta` is 925.5,
not the paper's 0, holding the mean value at 960 bytes so the level ladder, the
populated depth and the T sweep stay comparable with the geometry every other
constant is calibrated for; the paper's own fit means about 34 bytes and would
shrink the database roughly tenfold. This must be reported as the Assoc key
distribution and operation mix *at the project's record size*, never as the
published value distribution. And `mix_max_value_size` is raised from db_bench's
1024 default to 65536: db_bench applies it as `val_size % value_max`
(`db_bench_tool.cc:7316`), a wraparound rather than a clamp, so at the default
6.85% of draws wrap to as little as one byte and the measured mean falls to
890.2 instead of 960.4. Simulated over 500,000 draws before the flag was set.

Implementation is flags only — no C++. `WORKLOAD_SKEW`, `KEYRANGE_*`,
`KEY_DIST_*`, `VALUE_*` and `ITER_*` in `config.sh`; the flags, a conditional
`:skew<keyrange_num>-<value_theta>` fingerprint segment and the full fit in
`metadata.env` in `03_run_experiments.sh`; `parse_fingerprint_options` updated
in lockstep. The op mix moves from 0.521/0.155/0.324 to 0.806/0.159/0.035
Get/Put/Seek, so scans fall from a third of operations to 3.5% — the scan
objective rests on `sorted_run_seeks`, whose intervals should be expected to
widen accordingly, and a check that becomes undecidable must be reported as
undecidable rather than failed, per the Section 3.1 subsidiary decision.

**`docs/PATHWAYS.md` was split.** It had grown to 1,950 lines by accumulating
dated verdicts inside the pathway specifications — Pathway E carried 170 lines
of E-1 and E-5 results, Gate 1 carried 123 lines of its own outcome, and the
frozen-decisions register another 131. Those 424 lines moved to
`docs/PREREGISTRATION.md`, each leaving a one-line status and a pointer, and
PATHWAYS now holds theory, specification and done/not-done status only. The two
have different lifetimes: theory is amended when the theory changes, whereas a
dated decision is never edited after the run it governs.

`docs/PREREGISTRATION.md` is **tracked by git**, unlike the rest of `docs/`. A
preregistration record's whole value is that it is dated and unedited, and the
only durable proof of that is the commit. `docs/PATHWAYS.md` and
`docs/EXPERIMENTAL_SETUP.md` remain untracked and would still not survive a
clean checkout; that is unresolved and is flagged in `CLAUDE.md` rather than
worked around.

### 14.14 E-1 traced to a force latch; guard kept on the conditional rate, 2026-09-20

The guard's force condition was replayed offline from the per-frame level
state in `io.jsonl` over the thirty `unconstrained_prior_only` arms of Section
14.9, using the manifest the holdout ran under. At T=2 the replay reproduces
`safety_shadow.jsonl` on 99.8% of frames. Of the 0.32–0.35 override fraction,
0.15–0.16 in every run comes from frames on which **no force term holds**: the
`retain` rule in `ClassifyWorkerSafety` held a budget force until the level
was healthy, so L2 exceeding its score limit by 0.1% on one frame latched L2
open for its whole 40 s due run. The frame-simulated calibration has no
memory, which is why it predicted 0.99% for a mechanism producing 33%. About
half of the T=6 and T=10 overrides are `kSLO`, a global term with no per-frame
record. A split-half refit on real frames shows that without the latch a 1%
per-frame limit lands at 0.005–0.14 on held-out seeds, depending on whether
the held-out backlog is longer than the fitting seeds': a maximum statistic,
as Corollary E.2 said.

The latch is removed in both the shadow and enforcement paths, the shadow log
moves to schema 3 with the three global terms and the debt ratio per frame,
the holdout validator decomposes overrides by them, and the calibration
reports a leave-one-run-out prediction beside the in-sample one. The guard is
kept; the `Assoc` re-run is scored on E-5's conditional rate, with E-1's
marginal rate reported. Decision, evidence tables and predictions are
`docs/PREREGISTRATION.md` D-2. E-1's 2026-09-14 verdict stands.

### 14.15 Geometry probe passed; a suspension-diagnostics defect, and the `Assoc` oracle parity gate, 2026-09-21

Two runs on the new Chameleon node (EPYC 4545P, GCC 14.3.0, `-march=znver5`,
SMT off at nproc 16, `performance` governor, THP `[madvise]`), both at 1M/T=2.

**The node was prepared, and the preparation is part of the pipeline.**
`00_install_dependencies.sh` was rewritten to establish the measurement
environment rather than assume it: GCC 14 from `ppa:ubuntu-toolchain-r/test`
because 24.04 ships GCC 13 and `-march=znver5` needs 14.1+, SMT switched off so
that `DBBENCH_CPUS=0-7` names cores rather than sibling threads, the frequency
governor set to `performance`, transparent huge pages set to `madvise`, and an
opt-in `ISOLATE_OS_CPUS=1` cpuset confining systemd's `system.slice` to the
cores neither measured process uses. The cpuset is off by default and is not
recorded per arm, so it must not be enabled for part of a pooled set.
`02_build_db_bench.sh` now disassembles the binary and fails the build if it
contains no AVX-512 opcodes, because the preflight in `01` proves only that the
compiler *accepts* `-march=znver5`, not that the flag reached the object code --
a silent fallback to a generic target would move every measurement with no other
signal. `03_run_experiments.sh` records `cpu_governor` and `thp_enabled` per
arm, since both are runtime settings that a reboot resets. The controls are
written up in `docs/EXPERIMENTAL_SETUP.md` §1, §2 and §7.

**The geometry probe passed** (`results/probe-assoc-1m`). L0-L5 populated at
2/15/30/62/127/64 MB, 316 MB settled against 302 MB garbage-free, about 1042
bytes per record on disk. This is the check D-1's first deliberate departure
from the published fit was staked on: `value_theta` of 925.5 holds the mean
value at about 960 bytes, the level ladder therefore keeps the geometry every
other constant is calibrated for, and the populated depth scales to the L8-L9
expected at 10M, so the T sweep keeps its meaning. Had the probe come back
shallow, the departure would have been unjustified and the fit would have had
to be revisited before any gate ran.

**The first parity run failed one check, and the check was the defect.**
`results/oracle-parity-assoc`, ten pairs. Every behavioural check reproduced
the shape of the 2026-08-22 record -- write, point-read and seek envelopes
within about 1%, held gates serving 62-90 jobs -- and p50 due-to-admission
latency came in at 127 us against the 511 us of that record. What failed was
`observation_health`, which requires `skipped_ticks <= 1` per run: every run
reported 25 or 26.

The controller had not missed a tick. `WorkerLoop`'s suspended branch counted
each tick taken between `rlsuspend` and `rlresume` as a skipped tick, and those
ticks are idle by design -- the native picker owns compaction across the bulk
load and no frame is sent. The invariant reads `skipped_ticks` as the worker
failing to take a tick it should have taken, so the two meanings had been
conflated into one counter and the check failed by construction on every run
that used a bulk load, which since the 2026-09-20 rebuild is every run.

The same boundary produced a second artifact. `RecordDueAdmission` had no way
to tell a due episode that began under suspension from one the controller
itself was responsible for, so the first admission after `rlresume` recorded
the tail of the load as its own latency: a maximum near 950 ms on every run,
and `never_admitted` of 0-2. The two symptoms measure one window -- 25 ticks at
the 50 ms cadence is about 1.3 s, and the 950 ms maximum falls inside it.

**The fix** (submodule `25468bbaa`) draws the boundary explicitly.
`SetRLControlSuspended(false)` stamps `RLControlResumedMicros()` before the
flag drops, so any reader that sees control live also sees when it became
live; `RecordDueAdmission` and `ObserveDueEpisodeTransitions` ignore episodes
older than that instant; and suspended ticks go to their own
`rl_suspended_ticks_` counter, exported as `suspended_ticks=` in the
diagnostics line. Stage 09's `DIAGNOSTICS` regex spans the new field without
modification, and the field doubles as the cheapest proof of which binary
produced a run.

**The re-run passes.** `results/oracle-parity-assoc-2`, ten pairs on the
rebuilt binary (`binary9b9321b1...`, which pools with nothing earlier).
`failed_checks: []`, every decided check passed.

| Check | Result |
| --- | --- |
| `observation_health` | **passed** -- `skipped_ticks` 0 on all ten, `watchdog_expiries` 0 |
| write amp / point-read amp | **passed** as paired envelopes: +0.31% [-0.54, +1.16], +0.88% [-1.17, +2.92] |
| `mean_l0_l1_input_size` | **passed**, +0.16% [-0.34, +0.67] |
| `maximum_pending_debt` | **passed**, -0.33% [-0.89, +0.23] |
| `per_level_maximum_score` | **passed**, +0.35 normalized [0.06, 0.64] against a limit of 1.0 |
| decision rate, held-gate service, due-level authorization, workload identity | passed |
| `due_to_admission_latency` | p50 **127 us** on all ten runs; see the flag note below |
| `sorted_run_seeks_per_scan` | **insufficient_pairs** -- 18 required, 10 available |
| `stall_duration` | **no_allowance_configured**, as in 2026-08-22 |

Verdict `undecided` with `failed_checks: []`, which is the same shape the
2026-08-22 gate was recorded as passing under, and is the acceptance condition
the pipeline itself encodes: `run_full_experiment.sh:193` and
`13_run_preflight_verification.sh:154` both accept exit 0 and exit 2 and treat
anything else as a bridge that is not transparent.

The admission-latency result is the substantive one. Maximum fell from about
950 ms to 0.23-6.3 ms, a factor of roughly 150, while p50 held at 127 us -- the
p50 was always sound, because one contaminated sample per run cannot move a
median over fifty episodes, which is why the defect showed up in the maximum
and in `never_admitted` rather than in the statistic the gate scores.

`never_admitted` did not reach zero as predicted; it is 0-2 per run, seven
episodes across ten runs. This is not the controller holding a level closed.
The same report records `episodes_unmeasurable_between_ticks` of 43-65 per run
and `oracle_levels_due_only_in_episodes: [4]` in five of ten runs: most due
episodes open and close inside a single 50 ms tick, so the worker never samples
them. Stage 09 already separates that population deliberately -- demanding
authorization for a condition the controller never observed is not a defensible
check -- and the count sits inside `due_to_admission_latency`, which carries no
limit unless one is passed.

**A flag note that belongs in the record.** The gate was first invoked without
`--admission-latency-limit-micros 5000`, which `run_full_experiment.sh:191` and
`13_run_preflight_verification.sh:147` both pass, so
`due_to_admission_latency` returned `no_limit_configured` and was left
unscored, and `--minimum-pairs`/`--minimum-envelope-pairs` fell back to 5
rather than the suite's 10. The artifact was regenerated under the suite's
flags, which decides the check at 127 us against a 5000 us limit and leaves
`sorted_run_seeks_per_scan` and `stall_duration` as the only undecided ones.
That is not a threshold chosen after seeing the outcome: 5000 us is hard-coded
in both callers and is the limit the 2026-08-22 gate was scored under. The
verdict is unchanged either way -- `failed_checks` is empty under both
invocations -- but stage 09 run by hand must be given the flags the suite gives
it, or it silently scores less than the suite would.

**`decision_rate` is 16.6-17.0/s** against a 20/s target inside a +/-20% band,
down from the roughly 19.95/s this project has measured before. It passes with
4% of headroom. The rate is `queries / elapsed_seconds` and the controller
sends nothing across the suspended load, so a denominator that spans the whole
run dilutes it by about that much; the check is evaluated only by stage 09 and
cannot block the sweeps behind it. Noted, not chased.

With the numbers above recorded, `results/oracle-parity-assoc`'s twenty arm
directories are redundant. Unlike D-2's evidence, which is per-frame state that
cannot be summarised, this finding is the handful of summary numbers in
`oracle_parity.json` plus one `suspended_ticks=` token, all of which are here.
The deprecated tree under `deprecated/pre-gate2-2026-09-20/` is a separate
matter and stays.

**Two repairs made alongside.** `docs/PREREGISTRATION.md` was untracked through
four commits -- the `!` rule recorded in 14.13 had been written and lost, and
`.gitignore` carried a bare `/docs/*` with no re-includes, contradicting
`CLAUDE.md` -- so D-1 and D-2 had no commit date and therefore no claim to
being preregistrations at all. The rule is restored with a comment saying why
it must not be dropped again. **The commit proves the file existed on
2026-09-21, not on the 2026-09-20 its entries are dated**; the one-day gap is
not provable by git and is not claimed to be. It costs D-1 and D-2 nothing,
because the commit still precedes every run either governs -- the Hull-0 sweep,
the capacity calibration, the guard calibration and holdout, and every learned
arm are all still to run, and the oracle parity gate is an instrument check
rather than a predicted outcome -- but a reader who checks the dates should
find the discrepancy already noted here rather than discover it. And
`03_run_experiments.sh` opened
`safety_shadow.jsonl` only for `unconstrained_prior_only` and the holdout
phase, while D-2 scores E-5 on every learned arm; `prior_only` and `rl` now
open it too. Enforcement already runs the classifier on those arms, so this
records what the guard did rather than adding work to the decision path.

**The `docs/` tracking gap is closed.** 14.13 and `CLAUDE.md` both record that
`docs/PATHWAYS.md` and `docs/EXPERIMENTAL_SETUP.md` were untracked and would
not survive a clean checkout. That was true when written. Both were committed
on 2026-09-21 (`b253e85`), so `git ls-files docs/` now lists them alongside
`PREREGISTRATION.md`, the two design notes and the two PDFs, and the warning in
`CLAUDE.md` should lose its "Unresolved" flag.

It was closed twice over, and the second supersedes the first. The `!` rule
restored earlier the same day re-included that one file under a still-standing
`/docs/*`; `b253e85` then removed both the blanket rule and the re-include and
committed the directory, so nothing under `docs/` is ignored at all and no `!`
rule is needed. `CLAUDE.md` and `.gitignore` were updated to say so.

The mechanism is worth keeping, because it still governs `*.json`, `*.txt` and
`scripts/*`. `.gitignore` has no effect on a path already in the index, so a
file added once stays tracked however broad the rule above it; the rule's only
reach is files *created after it*. That is exactly why `PREREGISTRATION.md` --
new on 2026-09-20 -- went untracked through four commits while its older
neighbours, added before the rule, did not. A blanket ignore with `!`
re-includes makes new files invisible to `git add` with no warning, and the
only reliable signal is that a file you expect to see never appears in
`git status`.

### 14.16 Gate 1 re-measured on `Assoc`; C-2 partly passes and the grid is found to contain duplicates, 2026-09-21

The Hull-0 sweep, both top-up passes, the cross-T cells and the comparator
selection all ran on the rebuilt binary `9b9321b1…`. **182 `regular` arms**, one
binary and one objective hash throughout. The verdict, the per-criterion
reasoning and the evidence paths are `docs/PREREGISTRATION.md`, "Gate 1 — Hull-0
re-measured on `Assoc`"; this is the narrative and what it cost.

**C-1 passes at all three ratios** — 11, 7 and 8 hull points of 12 — and
membership is identical across extractions at 108, 170 and 182 arms. Seventy-four
additional arms moved no point on or off the frontier, which is the strongest
stability evidence this project has produced for the comparator.

**C-2 reaches 18 of 26 points, and T=6 passes completely.** That is the first
complete C-2 pass in the project's history; the uniform gate failed C-2 at every
ratio. The whole criterion reduces to the gap between neighbouring hull points
measured in standard deviations, and the threshold is 4.97 at five repeats
falling to 0.56 at two hundred. The median across the 26 points is **5.75**
against **0.55** for the pair the 2026-09-12 gate failed on: the `Assoc`
frontier is about ten times better separated relative to noise, because the
skewed workload makes the L0 trigger a stronger lever on both axes.

**Two of the remaining failures are not measurement failures at all — they are
the same configuration entered twice.** L0's score is the maximum of
`files / trigger` and `bytes / max_bytes_for_level_base`, so the byte branch
caps the *effective* trigger at roughly `base / L0 file size`; at base 8 MiB
against a 2 MiB write buffer that ceiling is about 4-5 files and triggers 8 and
16 are indistinguishable by construction. Measured, their L0 compaction job
counts are 107.7/107.7, 113.0/113.0 and 111.3/111.3 at T = 2, 6 and 10 —
**0.00% apart** — and their gap/σ is 0.03 and 0.04 against a wall at 0.56. The
grid sweeps trigger and level base as independent axes; they are not. This is
assumption A3' in `docs/PATHWAYS.md`, which nobody had connected to the grid
design, and it retrospectively explains the 2026-09-12 unresolvable pair, which
was also at scale 0.5. It should shape the Hull-s grid at Gate 3c.

**A methodological finding about the top-up itself.** The repeat targets are
computed from three-sample standard deviations and three-sample means, and both
are noisy; more data moves the standard deviation *and* the means, and the means
set the spacing that the interval is compared against. So a pilot-based
sample-size rule does not converge in one pass. `T=2` trigger 16 / base 16 MiB
was told it needed 9, was given 9, and came back needing 14. A second pass of
six arms was run for the four points within reach, three of which then passed.
The fourth — `T=2` trigger 16 / base 8 MiB — asked for 9, got 9, and came back
asking for 10 with its gap/σ having *fallen* from 3.28 to 2.97. A third arm was
inside the cumulative budget and **was deliberately not run**: a point that moves
backwards on the deciding statistic after being given what it asked for is not
under-sampled, and running until a criterion passes is how a preregistered
criterion stops meaning anything.

**The spend cap had to be scored by hand.** PATHWAYS preregisters ten additional
arms *per configuration*, while `15_top_up_hull.py` compares `target - have`
against `--add-cap` and therefore enforces it *per pass*. A second pass driven by
the wrapper would have looked compliant while putting one configuration at eleven
cumulative extra arms. The pass was selected to respect the cumulative reading,
and `05_run_baseline_sweep.sh` was driven directly so that `gate1/topup.tsv` and
`gate1/hull_indistinguishable.tsv` were not rewritten under a working cap that
is not the preregistered one.

**The cross-T cells reproduce and strengthen the uniform finding.** The pooled
hull over T in {2, 6, 10, 14, 20} holds 14 of 38 points, and **T=14 and T=20
contribute none**: both are dominated by a single T=10 configuration on write and
point-read simultaneously. On uniform, T=14 contributed one point and T=20 none.
Raising the size ratio past 10 buys no frontier, which is what P1-14 added these
cells to establish.

**E-2 passes at 100%** — 8/8, 4/4 and 4/4 populated level-cells, every one on a
real order-statistic tolerance bound at 0.99 coverage, with no bootstrap caps
anywhere. The selected comparators are trigger 2, trigger 4 and trigger 2 at
T = 2, 6 and 10, all at base 16 MiB.

**D-3's first prediction is confirmed and it changed a comparator.** Under the
corrected space denominator the stage-06 space filter admits all four triggers at
every ratio; under the superseded estimate it would have admitted two at T=2,
**one** at T=6 and four at T=10. At T=6 the estimate left a single survivor, so
the comparator was forced; with the filter no longer binding the runtime
tie-break selected trigger 4 instead of trigger 2. The prediction was committed
(`b5fd0ce`, 17:06 UTC) before stage 06 ran.

**What this does not establish.** No policy arm has run on `Assoc`. C-3, C-4 and
C-6 need `prior_only`, which needs a guard-calibrated manifest and therefore the
Pathway E protocol; C-5 needs stage 16 and the capacity vectors. Gate 1 has
produced a comparator, not a result about the controller.

### 14.17 Guard protocol run on `Assoc`; D-2 half-confirmed, and the calibration audited, 2026-09-22

The guard calibration and independent holdout ran on `Assoc` at 10M for
T = 2, 6 and 10, three repeats each, on binary `9b9321b1…`. The verdict, both
of D-2's predictions as scored, and the calibration audit are in
`docs/PREREGISTRATION.md`, "Guard protocol on `Assoc`"; this is the narrative.

**Half of D-2 is confirmed and half is falsified.** Removing the force latch did
what D-2 said it would: the marginal override rate roughly halved in every cell,
0.3356 → 0.1268, 0.1567 → 0.0760 and 0.1983 → 0.1235, comfortably under the 0.20
the entry predicted. What failed is the second prediction — the leave-one-out
estimate was supposed to bracket the holdout within a factor of three and misses
by 7.1×, 12.0× and 12.5×. D-2 wrote down what that means and it is recorded as
written: the calibration's transfer claim is wrong. Nothing was perturbed in
either direction; `actual_interventions` is zero on all nine holdout runs.

**E-5, the criterion D-2 made the decider, is still unmeasured**, and this
holdout cannot measure it. Its arm is `oracle`, whose rule is
`score >= 1 → compact`, while force requires `due` — the same predicate on the
same snapshot — so every force lands on a level the oracle was already
compacting and the conditional rate is zero by construction. That was already
established on 2026-09-17 and it has not changed. The guard's status on `Assoc`
therefore rests on a criterion that needs an arm capable of disagreeing with it.

**One long-open question closed, and a guess retracted.** Schema 3 logs the
three global terms per frame, so the T=10 mechanism is now read from a log
rather than reconstructed: `slo_force_due` carries 524 of 739 override frames,
71% of the total. Section 14.8 named `l0_slowdown` as the leading candidate for
the 14.4% of frames the offline replay could not attribute. That guess is wrong
and is retracted; `l0_slowdown` is a minor term in every cell.

**The audit found why the calibration misses, and it is structural.** The force
condition has six terms and `frame_simulated_limits` fits its 1% target over
three of them; the other three are global — one breach forces every due level in
the frame — and the calibration never sees them. Discounting those frames
entirely does not rescue it: the three terms it *does* calibrate still fire at
10.5×, 6.7× and 3.5× the budget. Two further findings explain that residue.
`due_age` and `pressure` only grow while a level is due, so they latch by
construction and D-2's removal of the explicit `retain` latch was necessarily
partial — the override frames form just 20, 15 and 8 events carrying 1272, 502
and 739 frames, a mean engagement of 3.2 to 4.6 seconds. And the instrument that
would be used to choose better limits cannot predict them: replaying the
exported limits on the holdout's own episodes under the calibration's own three
score models gives 0.017, 0.020 and 0.340 at T=2 against a measured 0.105,
a 20× bracket matching none of the three. The missing quantity is the per-frame
pressure and score trajectory, which the episode log does not carry.

**Measure-only, and why the limits were not touched.** The per-level limits are
manifest data, not code, and changing them voids no hull — the experiment
fingerprint carries no manifest hash. They were still left alone. Choosing new
limits after seeing 0.035–0.107 is fitting to the outcome, which is the
objection that kept C-2 and E-1 recorded failed, and the audit's fourth finding
independently shows there is no instrument to predict what new limits would do.
The missing per-frame state is already on the wire: `rl_agent/server.py:92`
logs `"input": raw_state` every frame and the protocol-v2 per-level state
carries `due_age_micros`, `pressure_score_micros`, `score` and `files`. The
`oracle` arm does not query the server, which is why this holdout has no such
log; any arm that does query it writes one, with no rebuild. That is the
resolution path Section 14.8 already wrote down and did not take on `Assoc`.

**Two instrumentation gaps and one defect.** `prohibit_optional` and a
release-frame flag are not logged, so an override frame with no global term true
conflates a per-level force, a release frame and a write-driven revoke; both are
C++ and both are deferred to Gate 2, since a rebuild voids the 182-arm hull.
The defect is older and is now fixed: `06_run_guard_protocol.sh` scored its
cells under `set -Eeuo pipefail`, so a non-zero exit at T=2 aborted the loop and
left T=6 and T=10 unscored. That is the same failure as 2026-09-14, which
Section 14.8 recorded *and stated the fix for* — "the validator is a scoring
step, so a failing cell is a result, not an error" — without the script ever
being changed. It cost the same two cells twice. The loop now scores every cell
and exits on the worst status at the end.

### 14.18 The prior repaired (D-4) and the L0 band isolated (D-5), 2026-09-22

Two changes and two arm sets, both Python-only, both on binary `9b9321b1…`, so
the 182-arm Hull-0 is untouched. The dated decisions, predictions and verdicts
are `docs/PREREGISTRATION.md` D-4 and D-5; this is the narrative.

**The prior was audited against PATHWAYS and found incoherent at deep levels.**
For levels >= 1 its benefit term was `fullness**2`, which authorised a
compaction at 0.82-0.92 of target where native RocksDB waits for 1.0. Below
score 1 a deep level stalls nothing -- the only deep stall path is pending
bytes against a 64 GiB soft limit on a 3 GB tree -- so the term was a "due
soon" signal, i.e. eagerness. Its cost term was inert for a different reason:
`NextLevelOverlapBytes` spans the whole level's key range, so overlap is about
T times bytes and `(1 + overlap/bytes) / size_ratio` clamps to 1.0 whenever the
level below is full. By A4 a deep level is one sorted run whatever its size, so
compacting it early buys nothing on R; by Theorem B.1 it forfeits the
overwrites a later merge would drop, and `Assoc` has them (measured eta 0.84,
0.86, 0.93 at L1). A-0 had already attributed the prior's entire write excess
to eagerness in every cell. D-4 replaced the term with the due indicator
`score >= 1` and dropped P1c-23's empty-level depth charge.

**D-4's mechanism is confirmed; one prediction failed on a mis-specified
proxy.** Against the same-configuration static twin at the programme's
comparators, deep levels became native: eta within 0.008, phi at release within
0.003 at T=2 and 0.001 at T=10, and **zero releases below due in about 20,000
merges**. At the trigger-2 cells the policy is now indistinguishable from doing
nothing (+0.09% and +0.10% W, +1.14% and +0.74% R), which closes the 14.10
defect where T=2 was worse than its twin on both axes. At T=6 it trades +2.07%
W for -5.69% R, against +14.96% / -8.69% for the same cell before the repair --
the write cost of the read gain fell about sevenfold. Prediction 1 failed at
T=6 only: L1's phi at release came in 0.062 below the twin's, just past the
0.05 tolerance, and in the direction of compacting *earlier*. It is recorded
failed and not reworded; the rule it was testing is established by the
below-due count, which the prediction did not use.

**The cause was a confound nobody had noticed in the comparator rule.** Stage
06 selects minimum-space, then fastest, then lower W. Since D-3 the space
filter admits all four triggers at every ratio, so runtime decides -- and at
T=6 triggers 2 and 4 came in **0.20% apart on three repeats each**, inside the
rule's own 1% tie band, with the tie going to trigger 4 on write
amplification. The prior's only remaining lever after D-4 is the
below-threshold L0 band, which exists only at trigger >= 3. So the programme's
comparators had measured the band at exactly one size ratio, and its presence
was perfectly confounded with T=6.

**D-5 ran the missing cells and the confound is resolved.** Twelve policy arms
at trigger 4 for T=2 and T=10, with six `oracle` calibration arms to produce
the trigger-4 manifests through the same chain the programme's cells used. The
band's magnitude is **1.33x, 1.32x, 1.33x** at T = 2, 6, 10 against 0.99x and
1.00x at trigger 2: it is a function of the trigger and essentially nothing
else. The phi gap reappears at both new cells (-0.037, -0.070), which is what
identifies it as a band artifact rather than a T=6 artifact. Deep releases
below due: 0 of 24,400 more, so the D-4 rule now rests on about 44,000 merges.

**Both predicted orderings failed, and the replacement is more useful than
either.** The read gain was predicted to scale with the band's headroom and the
write cost with L0's share of write bytes; neither holds. Both track
**populated depth**, in opposite directions -- a shallower tree gives L0 a
larger share of the probe path, while extra top-of-tree work in a deeper tree
is rewritten at every level it then crosses. The band therefore buys 3.35,
2.75 and 1.54 units of read per unit of write at T = 10, 6, 2. This was not
predicted and is recorded as a finding of the control. It bears on Gate 3b cell
selection: the top-of-tree lever is worth most where the tree is shallowest.

**What this settles for the programme.** At trigger 4 the paired upper bound on
delta-W is +3.92%, +3.43% and +2.96% at T = 2, 6, 10 against a 2%
non-inferiority margin, so **`prior_only` misses the write constraint at every
ratio**; at trigger 2 it passes trivially because the band does not exist and
the policy is native. The prior's only lever costs more write than the budget
allows, measured across three ratios rather than inferred from one.
`RL_PRIOR_MIN_RUN_REDUCTION` governs that trade and was **not** retuned:
choosing it after seeing the number it moves is the objection that kept C-2 and
E-1 recorded failed, and the preregistered mechanism for the trade is Pathway
D's write hinge and its multiplier, which act on `rl`.

**E-5 passed on the prior and the pass is vacuous.** The conditional override
rate is 0.0006 / 0.0000 / 0.0007 against a 1% limit, with enforcement live and
1351 / 520 / 744 interventions applied. Under D-4 a deep level compacts iff it
is due, which is the guard's own force predicate, and `RL_L0_ALLOW_DEFER=0`
promotes any due L0 defer -- so force cannot change the prior's action, by
construction. This is the same structural reason the `oracle` holdout could not
measure E-5 (14.17), reproduced on a second arm. **E-5 is decided on `rl`.**

**C-5 is closed on `Assoc`**: s_max = 2.0 at the 2% rung at all three ratios,
27 capacity arms with every applied vector verified against its request.

**The arms in full, `regular` against `prior_only`, paired on three seeds.**
Amplification rows only; the latency rows are recorded in
`docs/PREREGISTRATION.md` under the D-5 verdict, with the reason they cannot
yet be read as a controlled comparison.

| cell | arm | W | R | S | seeks/scan | stall s |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| T=2 trig 2 | regular | 8.010 | 4.610 | 1.0895 | 7.606 | 43.4 |
| | prior_only | 8.017 | 4.663 | 1.0861 | 7.582 | 43.8 |
| T=6 trig 4 | regular | 7.812 | 4.260 | 1.0412 | 5.380 | 44.4 |
| | prior_only | **7.974** | **4.018** | 1.0428 | **5.070** | 45.0 |
| T=10 trig 2 | regular | 10.513 | 3.016 | 1.0306 | 3.592 | 45.9 |
| | prior_only | 10.524 | 3.039 | 1.0314 | 3.611 | 46.1 |
| T=2 trig 4 | regular | 6.761 | 5.550 | 1.0835 | 9.181 | 45.5 |
| | prior_only | **6.960** | **5.298** | 1.0843 | **8.799** | 45.8 |
| T=10 trig 4 | regular | 8.742 | 4.004 | 1.0384 | 4.856 | 42.2 |
| | prior_only | **8.926** | **3.721** | 1.0388 | **4.700** | 42.9 |

Reads and sorted-run seeks move together everywhere, as they must -- both are
the L0 run count. Space is flat to within 0.31% in every cell, so the space
constraint is nowhere near binding for this policy; the write constraint is the
only one it misses.

**A get-latency result the control cells carry, and cannot yet settle.** At the
two D-5 cells `prior_only` shows get p99 of 88.4 us against the twin's 31.9 at
T=10 and 104.3 against 65.7 at T=2, while p50 and p95 are unchanged and
`unconstrained_prior_only` is identical -- so it is not the guard. T=6 runs the
same trigger and shows none of it. The leading explanation is that latency is a
warm-cache figure (direct I/O is pinned off per A-Impl-2) and the twins were
measured on 2026-09-21 while the control arms ran on 2026-09-22 after about
forty arms of I/O, so the two sides are not cache-equivalent. The amplification
table above is unaffected, being byte ratios. Six `regular` arms run in the same
session as the policy arms would settle it; until then no latency figure from
`results/band-control` belongs in an acceptance table.

**Two process defects, both recorded rather than quietly fixed.** D-4 and D-5
were committed *after* the arms that test them; file mtimes order the edits an
hour earlier, but mtimes prove nothing that survives a clone, so both entries
are weaker records than D-1 through D-3 and say so. And the D-5 scoring script
summed L0 jobs across a glob without dividing by repeat count; the T=2 twin
carries nine repeats against the policy's three, so the ratio printed as 0.44x
instead of 1.33x, which would have triggered D-5's falsification clause and
forced the withdrawal of D-4's reading. The T=10 twin has three repeats and was
unaffected, and the disagreement between the two cells is what exposed it. The
arms were never wrong; only the analysis was.

### 14.19 The first learned arms on `Assoc`; a railed latency multiplier, 2026-09-22

Eighteen learned arms at 10M x T = 2/6/10, three repeats, on binary
`9b9321b1...`, plus six `regular` arms closing the D-5 latency control and a
one-arm learner smoke test. Every arm passed the learning-health gate. The
dated predictions and the verdict are `docs/PREREGISTRATION.md` D-7 and D-8;
this is the narrative.

**The learner trains and it is worse than plain RocksDB.** Against the paired
`regular` arm it writes +6.1%, +13.2% and +4.6% more at T = 2, 6 and 10. Reads
are **worse** at T=2 (+3.6%) and T=10 (+6.0%) and better only at T=6
(-10.4%), which is the one comparator carrying L0 trigger 4 and therefore the
only cell where a below-threshold band exists at all (D-5). Populated depth
rose by two levels at T=2 and one at T=6, reproducing the shape of the
2026-09-03 uniform matrix (Section 10.7 Finding 3) on a different workload,
a different reward and a repaired prior.

**D-6 is confirmed and is not in question.** The space multiplier sat at
exactly 1.00 -- its initial value -- in all six arms, so the space hinge never
fired once. The repair that entry made works, and the depth growth above is
not the learner buying space.

**But the run does not test what it was written to test.** The latency
multiplier reached its cap of 100.0 in *every* arm, six to eleven times the
write multiplier, and per-frame latency excess was never zero across 2,231
frames -- p50 19.92. The learner spent the run chasing a constraint no policy
can satisfy.

The cause is a unit error of exactly the family this project has hit twice
before. The reward hinges a 50 ms window's quantile against a whole-run
quantile limit. For an average that is sound; for a p99 it is not, because the
write-latency distribution is extreme. The raw histogram reads count 2,900,000,
average 16.48 us, P50 0.51 us, **P99 2.33 us**, P99.9 2749 us, max 14,076 us:
97.8% of writes finish inside a microsecond and the top 0.1% run to
milliseconds, so the average sits *above* the P99. Over 2.9M samples the run
p99 is 2.33 us; over the ~500 writes in one frame the p99 is the fifth-largest
sample and lands in the tail constantly, against a manifest limit of 1.02 us.
Section 10.7's instrument problem 6 flagged the p99-below-average anomaly and
asked for the histogram to be verified rather than assumed -- it is now
verified as a real property of the distribution, not a parse defect. And this
is the same inspection-paradox error Section 14.8 diagnosed for the guard and
repaired with `frame_simulated_limits`; the reward never received that fix.

D-8 drops the windowed p99 terms from the hinge, keeps the averages, and logs
the p99 decomposition per operation so the next reading is measured rather than
inferred. P0-4 is untouched: average and p99 both remain acceptance metrics
scored at run end on whole-run statistics, where both sides are the same
quantity. D-7's verdict stands as measured; D-8 re-asks the depth question
under the repaired instrument, and says in advance that either answer is
informative.

**E-5 is measurable for the first time, and it fails.** Neither the `oracle`
holdout (14.17) nor `prior_only` (14.18) could produce a non-zero conditional
override rate, because force requires a due level and neither arm disagrees
with that predicate. `rl` defers 41-43% of due frames, so it can, and the
guard changes its action on 15%, 7% and 10% of the frames where it could --
against D-2's 1% decider. That is the criterion D-2 chose to replace E-1
with, failing by seven to fifteen times on the first arm capable of scoring
it. The caveat is the same as everything else in this run: the policy was
driven by the railed latency term, so the measurement diagnoses the
reward-and-guard pair rather than the guard alone.

**Two scoring defects in the session's own tooling, both caught before
anything was recorded.** The D-7 scorer read the multipliers and the argmax
flip rate from `io.jsonl`, whose `"input"` field is the per-level state, not
the globals; the multipliers live in `learning_health.json` under
`server_summary.constrained_reward`. It printed `nan` for two predictions and
scored one of them PASS on an empty set, because `all()` over a fully filtered
generator is true. Both were re-scored from the correct source. Separately, the
D-5 scorer had summed L0 jobs across a glob without dividing by repeat count
(14.18). Neither touched an arm; both would have produced a wrong verdict.

**Also closed here:** the six `regular` arms at the two band cells that D-5
left owing, which make the latency comparison at those cells same-session for
the first time.

### 14.20 The learner audited against the objective; D-9 realigns it, 2026-09-23

The whole RL architecture — action space, state, reward, credit, prior, loss
and the guard's interaction with it — was read against the constrained
objective and measured on the D-7 arms. The dated decisions and predictions
are `docs/PREREGISTRATION.md` D-9; this is the narrative. No arm has run under
D-9.

**Verdict of the audit: the architecture was a stall-and-latency-era
controller with a constrained reward attached, and it could not express the
objective.** Six findings, each measured rather than inferred.

1. **The write hinge measured the wrong quantity.** The reward compared a
   10 s exponentially weighted write ratio against the whole-run bound. The
   window is time-weighted, the criterion byte-weighted, and under `Assoc`'s
   stall fractions they differ by far more than the margin:

   | cell | run-level W (`rl`) | bound | window W median | frames with hinge > 0 |
   | --- | ---: | ---: | ---: | ---: |
   | T=2 | 8.40 | 8.17 | 9.25 | 94% |
   | T=6 | 8.94 | 7.97 | 12.86 | 100% |
   | T=10 | 11.08 | 10.70 | 15.76 | 99% |

   The gap holds in every decile of the run. This is the third instrument in
   the project to compare a per-frame statistic against a whole-run limit —
   after the guard's due-age limits (14.8) and the reward's latency p99
   (D-8) — and it was the one on the binding constraint.
2. **Every multiplier was a ratchet.** `_dual_ascent` added the hinge, never
   the signed slack, so a multiplier could only rise. In every D-7 arm each
   multiplier's final value was its run maximum; `lambda_space` sat at its
   initial 1.00 throughout. Pathway D's D-3 and D-4 criteria were reading a
   monotone counter.
3. **The action space was asymmetric in the wrong direction.** It withheld
   "compact L0 later" (`RL_L0_ALLOW_DEFER=0`, a bridge-validation posture from
   2026-08-16 never lifted) and offered "compact any level early" (an
   optional token at score $\ge 0.10$) and "compact L0 at one file". The
   residual re-learned both. Release occupancy $\varphi$ and merge survival
   $\eta$, D-7 `rl` repeat 1 against the static twin, workload phase, trivial
   moves excluded:

   | cell | level | rl releases | rl $\varphi$ median | rl below 0.95 | twin $\varphi$ | twin below 0.95 | rl $\eta$ | twin $\eta$ |
   | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
   | T=2 | L1 | 935 | 0.41 | 686 | 1.09 | 0 | 0.93 | 0.84 |
   | T=2 | L2 | 1079 | 0.38 | 940 | 1.03 | 0 | 0.89 | 0.76 |
   | T=2 | L3 | 927 | 0.51 | 803 | 1.01 | 0 | 0.79 | 0.77 |
   | T=6 | L2 | 2110 | 0.75 | 1166 | 1.03 | 0 | 0.88 | 0.88 |
   | T=10 | L1 | 2240 | 1.02 | 330 | 1.12 | 0 | 0.91 | 0.93 |

   At T=2 the learner compacted L1 and L2 at 40% of target on three quarters
   of its releases, each early merge dropped less garbage, and the bytes
   pushed down early populated L3 and L4 three to fifty times more often than
   in the twin. That — eagerness, not deferral — is the depth mechanism of
   D-7's +2 / +1 / +0.7 levels, and it is the behaviour D-4 had just removed
   from the prior. On L0 the same arm compacted at a mean of 1.1 files on
   16–26% of below-trigger frames, the 14.10 defect, this time chosen by the
   residual. Under the correct reward the best this action space could do was
   imitate native RocksDB.
4. **The state could not represent the constrained problem.** Of 31 features,
   none was write amplification, none a multiplier, none a garbage signal;
   eleven were stall-era pressure terms, one the withdrawn scan-work metric
   (a constant at its floor), one the space estimate D-3 retired. With
   multipliers moving and absent from the state, Q was fit to a moving target
   and replay mixed rewards priced under different $\lambda$.
5. **The horizon was shorter than the physics.** $\gamma$ = 0.95 per second
   weighs a cost 20 s away at 0.36 and 60 s away at 0.05; deferral looked
   free and early compaction cheap.
6. **The prior was a step-0 policy only.** Under D-7 the prior advantage
   averaged 0.6–0.7 against a residual advantage of 258–652, a 400-to-1
   ratio, because Q is in return units and the prior is a dimensionless
   $\pm 2$; the TD loss was squared on returns in the thousands.

**What D-9 changes**, all Python or runner environment, none touching the
binary, the contract or a manifest: the reward becomes a component vector in
the flow/level form (signed marginal terms for W, R, seeks and stall, hinges
for space bytes and the latency averages), the multipliers take signed steps
and replay is re-priced at sample time (`rl_agent/lagrange.py`), the D-4 rule
becomes an action mask, the L0 posture is lifted for the learned arms only,
the state gains the run-to-date and windowed W over bound, space bytes over
bound and the five multipliers (37 inputs), $\gamma$ becomes 0.98 per second
with an 8 s credit window, and the TD loss becomes Huber. Exercised offline on
a synthetic stream before commit: the run-to-date W identity is exact, the
marginal write sum reproduces the constraint total to 0.7%, the write
multiplier peaks where the run-to-date W crosses the bound and falls after,
and training runs. `d9_learner_run.sh` runs the smoke, the matrix and the
scorer.

**What this says about the record.** D-7 and the 2026-09-03 uniform matrix
have the same status: neither tested the objective, because the harness could
not express it. Their learner findings are findings about the harness. No
learned arm in the project's history has yet been evidential about whether a
learned trigger can beat native RocksDB on reads at write parity; D-9's run is
the first designed to be.

## 15. Current limitations and next work

**Written 2026-09-05; forward planning has since moved to `docs/PATHWAYS.md`,
which supersedes this list.** The earlier 2026-08-26 and pre-gate next-work
lists were removed on 2026-09-13 as dead planning. Their first steps had been
executed and passed: the learner reaches replay, the optimizer moves the
residual, accounting is exact, no hard-invalid interval contaminates a run, and
the 10M checkpoint produced learned action flips. The matrix in Section 10.7
then ran and failed, which is why the work below is no longer validation.

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
   amplification. **Qualified 2026-09-19 (Section 14.9):** that read advantage
   exists only against a single tuned baseline. Against the hull the prior is
   dominated, so recovering its read behaviour recovers a dominated point.
   Reward re-weighting alone can no longer produce an accepting result.
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

## 16. Guide to the existing documentation

All project-authored documents were read while producing this record. Use them
as follows.

| Document | What it contains | How to interpret it now |
| --- | --- | --- |
| `README.md` | Trigger-only synopsis, build entry points, geometry, and scaled-pipeline link. | Current entry page. |
| `docs/PATHWAYS.md` | Improvement pathways A-F, their proofs, per-pathway acceptance criteria, and the gated execution order. Theory and specification only since the 2026-09-20 split. | **Current forward plan.** Authoritative for the post-2026-09-05 objective, the two-hull comparator, and gate costs. Carries a `(Done)` marker per completed item. Untracked by git. |
| `docs/PREREGISTRATION.md` | The dated companion: decisions recorded before the runs they govern, the predictions made in advance, and the gate verdicts as measured. | **Tracked by git**, because a preregistration record is worth nothing without a commit date. New dated decisions go here, not in PATHWAYS. |
| `docs/multilevel_rl_design.md` | Protocol v2, multi-level architecture, parser failure, and 2026-08-01 rework. | Authoritative trigger-protocol reference. |
| `docs/physics_informed_rl_architecture.md` | Analytic prior plus learned residual rationale and equations. | Current trigger-model lineage. |
| `workload_specs/README.md` | Balanced-workload timing and generator/parser pitfalls. | Current workload-authoring evidence. |
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

Two findings survived that judgement. **One of them has since been withdrawn**
— see Section 14.9: against the configuration class rather than a single tuned
baseline, the prior is dominated at every ratio, and C-3 and C-6 both fail. The
sentence below is retained as the reading that stood until 2026-09-19. The
**analytic prior is a genuinely good read policy and improves as the size ratio
grows**:
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
