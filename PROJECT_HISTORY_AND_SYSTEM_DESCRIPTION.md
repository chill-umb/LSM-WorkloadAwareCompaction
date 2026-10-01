# LSM Workload-Aware Compaction: Project Description, History, and Status

**Document status:** consolidated record of the RL compaction-trigger work,
June 2026 to 2026-09-23.

> **Condensed on 2026-10-01 at the owner's request.** Repeated explanations,
> narration and tables of intermediate numbers were removed. Every section
> number other files cite is kept, with the facts they cite. The full text is
> `git show cb45743:PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md`. Section 12 was
> removed on 2026-09-13 and its number is not reused. The forward plan is
> `docs/PATHWAYS.md`; dated decisions and verdicts are in
> `docs/PREREGISTRATION.md`. Section 18.1 holds the programme summary moved
> here from PATHWAYS Appendix R.

**Status in brief, newest first.**

- **2026-09-23 (14.24):** the first run with a correct harness (D-12). The
  learned trigger loses to or ties native RocksDB everywhere. No new direction
  has been chosen.
- **2026-09-20 (14.13):** the workload changed from uniform keys to UDB `Assoc`.
- **2026-09-19 (14.9):** Gate 1 complete and not passed. The prior is dominated
  by the static hull, and 10.7 Finding 2 is withdrawn.
- **2026-09-05 (10.7, 3.1):** the learner trains; the 10M matrix fails every
  cell on write amplification; the objective becomes a constrained one.
- **2026-08-26 (10.6):** the 2026-08-24 matrix recorded zero gradient steps,
  so every `rl` arm ran the prior.
- **2026-08-22 (Section 8):** oracle parity passes (verdict `undecided`,
  `failed: []`).
- **2026-08-15 (Section 8):** scope correction. The project builds an RL
  compaction **trigger**, not a file picker. Protocol v3 (exact-SST choice) was
  rejected and removed; any mention of it is historical.

## 1. Executive description

This is a research system for online, workload-aware compaction control in
RocksDB, an LSM-tree engine. Writes are flushed into sorted SST files and merged
down ever larger levels by compactions, which drop stale data and cut the
sorted runs a read must check, at the cost of rewriting data. Native RocksDB
times compactions with static thresholds and per-level scores. The project asks
whether a controller that learns online can time them better.

The design is a trigger-only, physics-informed, two-action DQN that starts
cold. For each level it returns `defer` or `compact`. A due `compact` opens that
level's gate for the control interval, so RocksDB may run several native
compactions. A below-threshold ("proactive") `compact` grants one optional
token. Neither names an SST: RocksDB's native priority picks the files, and its
own machinery runs the compaction.

## 2. The underlying LSM-tree problem

L0 files overlap, so a lookup may check several L0 runs. The size ratio `T`
(`max_bytes_for_level_multiplier`) sets capacity growth between levels.
Compacting early cuts runs and stale data but can rewrite data before garbage
builds up; compacting late saves writes for a while but keeps more runs and can
stall writes. The project learns only timing and level.

| Metric | Definition |
| --- | --- |
| Write amplification | `(flush bytes + compaction bytes written) / user logical bytes written` |
| Point-read amplification | logical SST probes per point read, including probes a Bloom filter rejects |
| Scan amplification | `(returned entries + internal entries skipped) / returned entries` |
| Space amplification | settled total SST bytes / live logical key-and-value bytes |

Sorted-run seeks per scan are reported separately; physical reads are
diagnostics only.

## 3. Research objective, constraints, and non-goals

### 3.1 Formal objective

**2026-09-05 preregistered amendment (authoritative).** Recorded before any run
of the `docs/PATHWAYS.md` programme. The strict requirement that write,
point-read *and* scan amplification each strictly improve against a tuned
baseline is **withdrawn as infeasible** (Section 10.7 Finding 4; PATHWAYS
Theorems A.2 and B.1). In a leveled tree the read/write trade is intrinsic, the
comparator sits near the frontier, fanout conservation forbids beating the
uniform tree at fixed depth, and elision below parity is capped by resident
garbage, which this workload barely has. The replacement:

```text
minimise    point-read amplification R(pi)
subject to  W(pi)    <= W_base           (parity, beta = 0)
            S(pi)    <= S_bound          (2% over baseline)
            lat(pi)  <= lat_bound        (2%, Get / scan / aggregate write)
            stall(pi) <= stall_base
```

- **Write is parity, not a budget.** A `beta` sweep is exposition only.
- **The comparator is a class**: the Pareto hull of static configurations over
  `(W, R)`. Hull-0 (`s = 1`) judges arms without a capacity action; Hull-s,
  which adds capacity-expanded configurations, judges arms with one.
- **Subsidiary decisions.** (1) `scan_amplification` sits at its floor of 1.0,
  so the scan objective is `sorted_run_seeks`. (2) The `all(delta <= 0)` stall
  test is retired, because it failed a 5.4% mean improvement at T=10 when one
  pair of ten rose; the paired-envelope form (14.2) replaces it. (3) A latency
  check whose interval is wider than its 2% limit is **undecidable**, never
  failed; five pairs give +/-20-30%. Cells carrying a latency claim must run
  the preregistered ten repeats. (4) Space enters the reward only as a hinge
  above the manifest limit, not as something to minimise.
- **Left open:** the write p95-below-average anomaly (10.7 problem 6; to be
  resolved before any latency figure appears in a submitted table), and
  whether to claim write parity or improvement (before Gate 4).

**Superseded criteria (to 2026-09-05):** a tuned baseline chosen before any
policy result (minimum space, then fastest within 2% of it), with ten paired
repeats, 95% intervals for point-read, scan and write amplification each
strictly below zero, at most 2% space and latency regression, and no stall rise.

### 3.2 Hard online-learning constraint

Every run starts from scratch. The baseline SLO manifest holds measurements and
limits, not weights. The prior is code, and residual heads start at zero.

### 3.3 What remains RocksDB's responsibility

The controller chooses timing and a source level, never a source SST. RocksDB
keeps snapshot correctness, clean-cut expansion, overlap discovery, conflict
detection, merge semantics, tombstone and version elision, source-file priority
and selection, output-level choice, output creation and scheduling, and
explicit maintenance (manual, marked, periodic, TTL, drain).

## 4. Research lineage

The bundled RusKey (online RL, FLSM) and Vertiorizon (vertical against
horizontal growth) papers motivated the work; neither is implemented, nor are
forecast scheduling, offline pretraining or certified shielding. The comparator
is leveled RocksDB with `--compaction_pri=3` (`kMinOverlappingRatio`).

## 5. Repository and runtime architecture

### 5.1 Components

`db_bench` drives the modified RocksDB, whose `RLCompactionPicker` keeps
per-level gates and leaves file choice to native code. It talks over a Unix
socket (newline-delimited JSON) to `rl_agent/server.py`, which runs the
protocol-v2 processor: the analytic prior plus a residual DQN. `lib/rocksdb/`
is the fork and `scripts/dbbench_pipeline/` the numbered workflow; `include/`,
`src/`, `lib/tectonic/` and `workload_specs/` are the legacy Tectonic workflow.

### 5.2 The database mutex constraint

Early versions called Python from `NeedsCompaction()` while holding the DB
mutex. The picker now has a worker thread. Under the mutex it only publishes a
snapshot and reads an earlier response. Messages carry `interval_micros`.

### 5.3 Current trigger-only protocol-v2 contract

A request carries global and per-level state. The response is an ordered array
of `0 = defer` / `1 = authorize`, and never names a file. Due authorizations
become an allowed-source-level mask inside the native leveled builder, which
keeps its score order, `FilesByCompactionPri`, clean-cut expansion and overlap
and conflict checks. A below-threshold authorization calls the native
forced-level path once. The pipeline and the C++ producer are pinned to v2.

### 5.4 Historical protocol-v3 prototype (retired)

Protocol v3 let the controller name exact SSTs. It was rejected on 2026-08-15
and removed. **Do not reintroduce candidate or file-level control** (3.3).

### 5.7 Authority and bypass reasons

The superseded picker used global booleans (`parent_pick_allowed_`,
`DeferralExhausted()`), so permission for one level could leak into the parent
picker and compact another. Authority is now level-scoped, with one of six
reasons:

| Code | Reason | Meaning |
| ---: | --- | --- |
| 0 | policy | Exact candidate selected by the controller. |
| 1 | budget | That level exhausted its permitted deferral budget. |
| 2 | maintenance | Manual, marked, periodic, TTL, blob-GC, or equivalent required maintenance. |
| 3 | emergency | A local hard safety condition requires progress. |
| 4 | fallback | The server is unavailable or the response cannot be used. |
| 5 | drain | End-of-run debt settlement. |

Budget, emergency and fallback go through a forced-level path. Only maintenance
and drain may use the ordinary parent picker. Later entries add further
reasons, among them `kSLO` (14.8), `kPosture` (2026-08-16, the L0 posture and
the D3a crossing rule) and `kSuspended` (2026-09-20, the bulk load; 14.11).

## 6. Current protocol-v2 learning and safety system

As built on 2026-08-16. The reward, state, mask and horizon were later replaced
by the Pathway D form (14.11) and by D-9 (14.20).

### 6.1 Two-action residual model

A shared trunk feeds one two-action head per level. The residual layers start
at zero, so a cold start is exactly the prior:
`Q_i(s, a) = analytic_prior_i(s, a) + learned_residual_i(s, a)`.

### 6.2 Physics-informed trigger prior

Per level, the advantage of compacting is stall urgency plus read exposure
times expected run relief, minus normalized merge work and a premature-overlap
penalty. A deep level's relief was weighted by fullness, which was later found
to be eagerness (D-4, 14.18).

### 6.3 Cooperative whole-tree reward

Every level decision in a frame gets the same global reward, so moving bytes
down one level cannot fake relief:

```text
reward = gamma(dt) * Phi(next_tree) - Phi(current_tree)
       - dt * (write_amp_cost + point_probe_cost + scan_work_cost + latency_budget_cost)
```

`Phi` is the negative whole-tree cost built from point probes, scan work and
seeks, physical/live space, pending debt, stalls and run terms. So the reward
priced write amplification, probes, scan work and latency, and treated space as
a quantity to minimise, while the criteria treat it as a bound (3.1 decision 4;
10.7 Finding 3). Write amplification is the run-to-date ratio. Latency cost is
the excess above the manifest's average and p95 limits.

### 6.4 Deterministic trigger-only safety mask

With a calibrated `baseline_slo.json`, the controller tracks rolling latency
and space windows. A space or read breach forces due gates open; a write breach
revokes optional work; an unclear breach falls back to native due eligibility.
A due level is also forced open when its due age, pressure, score or debt
reaches the baseline envelope. `unconstrained_rl` and `oracle` run without the
mask. The mask is not a latency guarantee.

## 7. Measurement and observability added to the project

The fork counts logical point probes (Bloom rejections included), iterator
skips, sorted-run seeks and stall time. Each arm and repeat keeps its own
directory of command, seeds, revisions, logs, decisions and metrics.

## 8. Full project timeline

Later dated entries are in Section 14.

- **June-July 2026:** an L0 proof of concept, then multi-level control and the
  prior-plus-residual design (protocol v2). A parallel sweep fell back silently
  on a JSON whitespace bug, so **all 81 runs are invalid**.
- **2026-08-01 to 08-06:** a rework of 23 defects (socket work off the mutex,
  real bounded deferral, wall-clock credit). A scan parser bug had made scans
  ~240x too costly, so **all pre-fix scan results are invalid**.
- **2026-08-15:** one 1M/T=2 smoke pair, regular against v3: runtime +31%,
  write amplification 4.55 -> 12.46 (+174%), probes/read +65%, stalls +97%.
  v3 was removed; authority now goes through native `PickCompactionFromLevel`.
- **2026-08-16:** held gates. A `compact` had acted as a one-use pulse, and
  deferral blinded the only observation site. Per-level due gates now stay
  open while score >= 1, and optional tokens are one-shot.
- **2026-08-22:** after the D1-D7 fixes (Section 9), **oracle parity passes**
  at ten 1M/T2 pairs (verdict `undecided`, `failed: []`: eleven of thirteen
  checks pass, none fail). Due-to-admission p50 is 511 us against a
  5000 us limit, 65x below the ~33 ms before D3a. The 144-arm matrix followed
  (10.6).

## 9. Defect and fix catalogue

| Area | Defect | Fix and status |
| --- | --- | --- |
| Control path | `compact_now` only woke the scheduler; silent fallback; whitespace JSON (81 runs); Python under the DB mutex. | Forced level pick, explicit fallback, tolerant parser, worker thread. Fixed. |
| Authority | Due levels compacted despite `defer`; a global unlock for one level could compact another; sticky force flags. | Held per-level gates, per-level reasons, atomic frames. Fixed 2026-08-16. |
| Reward mechanics | Rates summed per decision; source-only relief; drained levels lost credit; phantom terminal reward near `+2.109`. | Integrate over `dt`, cooperative tree cost, zero states. Fixed. |
| Learning setup | Too few samples per deep agent; random residual at cold start; dead read features. | Shared trunk, zero residual, logical probe telemetry. Fixed. |
| Workload and hygiene | Scan parser; zero pending limits; orphan processes; single-run claims. | Fixed 2026-08-02; preflight, paired seeds, ten repeats. |
| Oracle gate D1-D7 | Instrument asymmetry (D1); lost final episodes (D2); a level crossing between ticks kept the old `kDefer` (D3, the one controller defect); undecidable `all()` checks (D4); stall allowance tied to the interval (D5); a dead exploration constant (D6); a biased latency estimator (D7). | Fixed 2026-08-19 to 08-30 (stall allowance still to be derived); D3a gives 511 us p50. |
| Censored episodes | Truncated episodes were pooled into the tolerance bound. | Refused 2026-08-30; charged to the tail 2026-09-14 (14.8). |
| Learner never trained | Zero gradient steps; the first override repair cleared every overlapping 4 s window. | Credit schema v2 (14.3); trained 2026-09-01 (14.4). |
| Gate launcher | `if ! cmd; then rc=$?` reads 0, so a three-valued gate exit failed open. | `rc=0; cmd \|\| rc=$?`. Fixed 2026-09-21. |
| Proactive band | `RL_OPTIONAL_MIN_SCORE` is relative to the trigger; at trigger 2 the prior merged single-file L0. | Diagnosed 14.10; prior fixed 14.11; band isolated 14.18. |

Later defects are in their own entries: the manifest-version lockstep (14.12),
the force latch (14.14), suspended ticks (14.15), duplicate grid points
(14.16), the hinge instruments (14.20-14.23), the evaluator scoring the bulk
load (14.22), and the C++ parser's first-match rule (14.23).

## 10. Experimental record and interpretation

Early results, briefly. The June L0 runs proved the control path could act, not
that the policy was better. All 81 July multi-level runs are invalid. A valid
ten-pair 5M comparison (2026-08-06) cut compaction bytes by 75.04 MB (95% CI
[-100.74, -46.04]; lower in 9 of 10 pairs) but raised point probes about 18%
and scan latency about 27%, failing the objective. The rework changelog
retracted several early claims, including a 5.6 s "RL overhead" that vanished
under instrumentation. The 2026-08-15 v3 smoke pair is the only v3 run, a
negative engineering result.

### 10.6 The 2026-08-24 paired matrix, and the training defect it exposed

1M/5M/10M/20M x T=2/6/10 x four arms x 3 repeats = 144 arms. Pooled over 36
paired differences against the leveled baseline:

| Arm | write amp | point-read amp | seeks/scan | space amp | runtime | stall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `prior_only` | +0.11% | +0.72% | +0.50% | -0.18% | +3.57% | +5.74% |
| `rl` | **+0.92%** [+0.4, +1.4] | +0.92% [-0.4, +2.2] | +0.70% [-0.4, +1.8] | +0.98% [-1.2, +3.1] | **+3.99%** [+2.3, +5.7] | **+10.56%** [+7.1, +14.0] |
| `unconstrained_rl` | **+18.94%** [+16.2, +21.7] | **-11.82%** [-14.9, -8.8] | **-11.95%** [-15.0, -8.9] | +3.46% [+0.6, +6.3] | +6.34% | +7.99% |

It fails the criteria; pooling makes the intervals too tight, so read them as
direction only. **None of it tests a learned policy**: zero gradient steps on
all 37 active level-cells, a residual identically 0.000000, a flip rate of 0.0.
Every `rl` arm ran the prior. Two findings survive: the prior over-compacts
(11.8% fewer probes and 12.0% fewer seeks for 18.9% more write, the same sign in
all twelve cells), and the live SLO mask largely replaces the policy.

### 10.7 The 2026-09-03 trained-learner matrix at 10M

10M x T=2/6/10 x {`regular`, `prior_only`, `unconstrained_rl`, `rl`} x 5 paired
repeats (`suite-20260902-193415`; five repeats rather than ten, so
directional). **The first matrix in which the learner trained** (85,740 and
92,592 full-horizon transitions; flip rates 0.42-0.73). It **fails in all three
cells**.

**Finding 1: write amplification is the universal blocker.** Every RL-family
arm writes more than the baseline in every cell: `prior_only`
+18.3%/+15.3%/+13.9%, `rl` +15.9%/+22.9%/+9.8%, `unconstrained_rl` up to
+50.7% at T=6.

**Finding 2 (withdrawn 2026-09-19, Section 14.9).** Against `regular` alone,
`prior_only` gave -12.5%, -17.3% and -19.3% point-read amplification at
T=2/6/10. Against the hull it is dominated at every ratio.

**Finding 3: the learned residual trades reads for space.** Against
`prior_only`, `rl` is 16.2%, 7.5% and 11.8% worse on point reads and 29.0%,
9.9% and 4.5% better on space. It defers top-of-tree compaction, and the tree
grows deeper:

| cell | L0 compact rate, prior_only -> rl | populated depth, prior_only -> rl |
| --- | --- | --- |
| 10M T=2 | 0.18 -> 0.06 | L0-L8 -> L0-L11 (+3) |
| 10M T=6 | 0.24 -> 0.15 | L0-L4 -> L0-L5 (+1) |
| 10M T=10 | 0.29 -> 0.17 | L0-L3 -> L0-L4 (+1) |
| 20M T=2 | 0.24 -> 0.06 | L0-L9 -> L0-L10 (+1) |

The rates appear to be selected actions (L0 runs with `RL_L0_ALLOW_DEFER=0`); the 20M
row is from the partially completed 20M cells. More levels put more runs on the read path; deeper data drops more stale
versions in larger bottom merges (the space gain); bytes crossing more levels
are rewritten more often (the write cost).

**Finding 4: the criteria may be infeasible as written.** `rl` passes both read
criteria at T=10 and still fails on write. This led to the 3.1 amendment.

**Instrument problems.** (1) Latency bounds are undecidable at five pairs; (2)
`all(delta <= 0)` stall checks fail on a 5.4% mean improvement; (3) space fails
at T=10 on a -0.07% mean; (4) `scan_amplification` sits at its floor; (5)
`unconstrained_rl` at T=6 reaches write amplification 12.56, unexplained; (6)
`write_latency_p95_us` (~5.6 us) is below `write_latency_avg_us` (~42 us), so
verify the histogram (verified real in 14.19).

## 11. Current db_bench experiment pipeline

`scripts/dbbench_pipeline/README.md` is the operational reference.

### 11.1 Matrix and workload

Each arm runs `filluniquerandom -> mixgraph -> waitforcompaction -> levelstats
-> stats`; control suspension and `resetstats` were added on 2026-09-20 (14.11,
14.22). The uniform workload was replaced by UDB `Assoc` on 2026-09-20 (14.13).
Storage: keys 64 B, values 960 B; write buffer 2 MiB; target SST 512 KiB; L1
base 16 MiB; 13 levels, so varying T cannot vary maximum depth; WAL off; L0
compact/slow/stop 4/20/36; decision and observation interval 50/50 ms.

### 11.3 Pairing and output behavior

Arms share a `db_bench` seed and alternate order; the runner needs
`CONFIRM_EXPERIMENTS=YES` and resumes only arms with a `COMPLETED` marker.
`waitforcompaction` is inside the measured run, so a deferring policy cannot
hide unfinished work. Formal space amplification is pre-reference SST bytes
over `rocksdb.estimate-live-data-size` (defect: 14.7). `11_analyze_learning.py`
reports per-level gradient steps and flip rates, which `summary.csv` cannot.

### 11.6 As-run configuration, 2026-08-22 to 2026-08-24

The 10.6 matrix used sizes 1/5/10/20M, a 6-configuration sweep and three
repeats. `level_compaction_dynamic_level_bytes` is **off** for every arm:
db_bench's flag defaults to `false`, though the library default is `true`.
Level targets are therefore static, `Li = 16 MiB x T^(i-1)`.

## 13. Verification

The Python and C++ test suites were removed on 2026-09-13. The rule recorded
that day: no test suite should be written; correctness rests on the PATHWAYS
gates re-run on the node, plus a local `-fsyntax-only` check.

## 14. Implementation status after restoring trigger-only scope

On 2026-08-16 the trigger-only rebuild existed in source only.

### 14.1 Status update, 2026-08-26

Built and gate-verified (oracle parity; `no_new_oracle_stop_event` removed
because it counted repeated warnings, not stops). The DQN had never trained:
**the blocking defect**.

### 14.2 Source-audit correction, 2026-08-30

Re-auditing the C++/Python repair found seven unsound points in the gate and
the picker, among them a one-sided `required_pairs` and pairing keyed by
geometry rather than the exact manifest.
Fixes: the worst shared level per pair as one **paired-envelope** observation,
and one manifest SHA-256 binding calibration, holdout, resume and final cell.

### 14.3 Replay-starvation diagnosis and credit protocol repair, 2026-08-31

A 5M/T2 run made 89,652 decisions and put **zero transitions in replay**:
`DQNAgent.observe()` called `_pending.clear()` on any rejected interval. Credit
schema v2 relabels known overrides and keeps them off-policy; only
socket/query fallback, watchdog fallback, malformed protocol, rejected
manifests and unknown ownership mark a frame hard-invalid.

### 14.4 Learner liveness proved; startup ownership repaired, 2026-09-01

The first schema-2 5M/T2 run trained (57,160 optimizer steps; replay
starvation and zero-gradient learning are fixed) but exited 5, failing its
health gate on one hard-invalid `unknown_control_ownership` frame. It remains
failed evidence and must not be relabelled, resumed, or used as the 10M
checkpoint. The cause, a startup compaction admitted before the first Python
frame, led to explicit `bootstrap`, `active` and `fallback` controller states.

### 14.5 Learner verified on hardware; first trained matrix, 2026-09-05

A 1M/5M/10M preflight passed every learned arm with exact C++/Python
agreement. Learner health at 10M: `residual_over_prior` 3.5-7.0 per level
(`residual_scale` 0.50-4.13 against `prior_scale` 0.34-0.49), TD loss
14,226 -> 6,136, and `max_abs_residual_advantage` 1012-1102, a 300-600x tail.
**Do not read `max_abs_residual_advantage` as a scale**: it is a run maximum
and once gave a false 500x divergence reading. Ten paired repeats and the
read-heavy/write-heavy safety suites were not run. The matrix is 10.7.

### 14.6 Gate 0 executed on the 2026-09-03 artifacts, 2026-09-11

New events give per-job bytes and per-level occupancy at release, from which
`compaction_measurements.py` derives merge survival `eta` and release occupancy
`phi_j`. The A-0 decomposition is in 18.1: the prior's write excess is entirely
eagerness. Theorem B.1 ceilings on W-1 from the `regular` arms: 79.5%, 39.9%
and 36.3% at 10M T=2/6/10, and 81.8% at 20M T=2.

### 14.7 Gate 1 executed, 2026-09-12

Hull-0 on the EPYC 4545P node: 12 static configurations per ratio (L0 trigger
2/4/8/16 x level-base scale 0.5/1/2) at 10M for T = 2, 6, 10, plus T = 14, 20.

- **C-1 passes**: hulls of 12, 9 and 9 of 12; ratios above 10 add nothing.
- **C-2: fail, deferred**, recorded failed rather than reinterpreted. Two T=2 points at scale 0.5 are unresolvable up
  to 200 repeats, and three more need 32, 70 and 126, beyond the ten-arm spend
  limit. The frontier is denser than the measurement resolves.
- **Ten repeats justified.** At five repeats the 95% interval is 0.30-0.36% of
  the mean on write and 1.30-2.85% on point-read; at ten, 0.17-0.21% and
  0.75-1.64%. (No longer holds as stated; 14.22.)
- **C-5 closed 2026-09-13**: a static per-level actuator applied in
  `PrepareForVersionAppend` gives s_max = 2.0 at the 2% rung in every cell.
- **Space-metric defect.** `estimate-live-data-size` moved by up to 4.8%
  across nine configurations with identical 3.02 GB of live data.
- **Analysis defect.** `JSONWriter` writes `status.ok()` as `1`, so
  `merge_success is not True` read every compaction as failed. Fixed.

### 14.8 Guard calibration repaired, E-1 recorded failed, 2026-09-14

Three defects were fixed. (1) `censored_tolerance_bound` gave no bound when any
episode was truncated; it now charges censored episodes to the tail. (2) **The
per-level limits used the wrong statistic.** A bound over episode durations
counts long *episodes*, but the picker tests due age on every 50 ms frame, and
one long episode covers hundreds of frames. Due age, pressure and score are now
calibrated jointly by replaying baseline runs at the observation cadence. (3)
Stage 06 pre-filters arms.

**The gate still fails**: at 10M/T=2 the override fraction is 0.363, 0.377 and
0.342 against a 0.01 limit and a 0.0099 prediction. Read from the three T=2
runs, the override frames formed one sustained event per run; in the first run
850 of 851 override frames had whole-tree debt at or above its limit (both
corrected in 14.9). **E-1 is
recorded failed**;
an amendment to count events was refused because the criterion had already
been seen to fail. T=6 and T=10 were scored on 2026-09-17 (0.14-0.15 and
0.19), having gone unscored because the validator exits non-zero at T=2 and
`set -Eeuo pipefail` aborted the loop. At T=10 the offline replay misses 14.4%
of overrides, and `l0_slowdown` was named the leading unconfirmed candidate
(retracted in 14.17). `kSLO` is roughly 2% of overrides at T=2, 56% at T=6
and 25% at T=10. Forcing is not gated on `guard_ready`, which arrives at
the end of the 58.8 s bulk load (29% of a run). The offline replay is reliable
only for the debt term. An `unconstrained_prior_only` arm was added.

### 14.9 Gate 1 completed; E-1's conditional rate measured, 2026-09-19

Thirty `unconstrained_prior_only` arms. The marginal override fraction (E-1) is
0.3356, 0.1567 and 0.1983 at T = 2, 6, 10, failing by 34x, 16x and 20x, yet
force changes an action on at most 0.0009 of frames and `revoke_optional`
never fired. The shield's write lever reads write *latency* (limit 139.7 us
against a measured 12.8 us), while the prior's real problem is write
*amplification*. Corrections to 14.8: override events are 1.3 per run at T=2
but 22.2 at T=6 and 14.9 at T=10, and debt is true on only 11% of T=2 override
frames. **E-5 satisfied; E-1 failed.**

**C-3 and C-6 fail.** In every cell one static configuration beats the prior
on write and point-read at once, with both paired intervals below zero; Gate 3b
would have to close 6-9% on write and 4-6% on point-read. **10.7 Finding 2 is
withdrawn**: the single-baseline comparator overstated the prior. The result
is scoped to a near-garbage-free workload. **Gate 1 is complete and not
passed** (standing in 18.1).

### 14.10 Policy contribution isolated; the proactive-band defect, 2026-09-20

The static twin (same L0 trigger and level base, no controller), compared with
`frontier_analysis.paired_comparison`, separates the policy from stage 06's
choice of base configuration. Policy minus twin:

| cell | config | pairs | write amplification | point-read amplification |
| --- | --- | ---: | ---: | ---: |
| 10M T=2 | L0 trigger 2, base 16 MiB | 5 | **+7.18%** [+6.81, +7.55] | **+4.17%** [+3.80, +4.53] |
| 10M T=6 | L0 trigger 4, base 16 MiB | 3 | +14.96% [+14.34, +15.58] | **-8.69%** [-11.35, -6.04] |
| 10M T=10 | L0 trigger 4, base 16 MiB | 5 | +9.34% [+8.65, +10.04] | **-6.11%** [-6.86, -5.36] |

**At T=2 the prior is worse than doing nothing, on both axes.** The cause is
the proactive band: `compact` is offered below threshold from score
`RL_OPTIONAL_MIN_SCORE` = 0.10, and L0's score is `files / trigger`, so at
trigger 2 one file (score 0.5) always sits in the band. At 10M/T=2 the prior
made 1,102 below-threshold L0 compactions, 910 on a single file: 499 L0 jobs
against the twin's 315. A proactive L0 merge is worth the runs it removes:
three at trigger 4, but **one** at trigger 2, one flush before native would
remove it, for a full L1 merge. **The band is relative while its value is
absolute**; a proactive action should require a minimum absolute run reduction.

### 14.11 Objective-consistency audit and Gate 2 repairs, 2026-09-20

The control chain was not consistent with the contract. Fixes, recorded as
P1c: the Pathway D reward (probes per Get; hinges on W, space, latency (avg
and p99, previously p95), seeks and stall; dual ascent); no deep-level
eagerness in the prior, and L0 relief net of what native removes one flush
later (minimum two runs), with the flush size measured rather than read from
the 16 MiB L1 target; control
suspended across the bulk load (`rlsuspend`/`rlresume`, `kSuspended`);
`sorted_run_seeks` counting one per L0 file and one per deeper level;
`reward.py` deleted. RocksDB moved to `6ad9f6b79` (base `7ea2d73` kept).

### 14.12 Pre-Gate-2 artifacts deprecated, 2026-09-20

A metric-definitions version mismatch in the C++ manifest parser was fixed.
Every earlier artifact is invalidated by the new binary, the in-place contract
amendment and a measured phase that excludes the bulk load; the Gate 1 hull,
the guard calibration and the C-3 verdict stand as records of the binary that
produced them and must be re-measured. All earlier artifacts (91 GB) moved to `deprecated/pre-gate2-2026-09-20/`, with
structure kept: the 14.9 and 14.10 evidence is under `results/prior_shadow/`,
the 14.7 hull under `gate1/`.

### 14.13 Workload changed to UDB `Assoc`; PATHWAYS split, 2026-09-20

Every gate now runs UDB `Assoc` (Cao et al., FAST 2020; Pathway B1), with
uniform keys kept as a control (`WORKLOAD_SKEW=0`): a hull measured on one
workload cannot judge a policy on another, and uniform keys leave almost no
garbage. The C-3 failure is not re-scored and the uniform result is not
withdrawn. Two departures from the published fit (D-1): `value_theta` 925.5 keeps
the mean value at 960 bytes, and `mix_max_value_size` is 65536 because db_bench
wraps values. The op mix becomes 0.806/0.159/0.035 Get/Put/Seek. Dated
verdicts moved from PATHWAYS to `docs/PREREGISTRATION.md`.

### 14.14 E-1 traced to a force latch; guard kept on the conditional rate, 2026-09-20

Of the 0.32-0.35 override fraction at T=2, 0.15-0.16 in every run came from
frames with **no force term true**: the `retain` rule latched a level open
until it was healthy. The latch was removed, and the guard is kept, scored on
E-5 (D-2). E-1's 2026-09-14 verdict stands.

### 14.15 Geometry probe passed; a suspension-diagnostics defect, and the `Assoc` oracle parity gate, 2026-09-21

The new node (EPYC 4545P, GCC 14.3.0, `-march=znver5`, SMT off) is prepared by
`00`, and `02` fails any build without AVX-512 opcodes. The 1M `Assoc` geometry
probe passed. **The first parity run failed `observation_health`** with 25-26
`skipped_ticks` per run: ticks taken while suspended across the bulk load were
counted as skipped, and the first admission after `rlresume` recorded the
load's tail (~950 ms) as its latency. **Fix** (submodule `25468bbaa`):
`rlresume` stamps `RLControlResumedMicros()`, older episodes are ignored, and
suspended ticks get their own `suspended_ticks=` counter. **The re-run passes**
(binary `9b9321b1…`): verdict `undecided`, `failed_checks: []`
(`sorted_run_seeks_per_scan` insufficient pairs, `stall_duration` no
allowance), the shape the pipeline accepts; p50 admission 127 us, maximum
0.23-6.3 ms. Run stage 09 by hand only with the suite's flags, including
`--admission-latency-limit-micros 5000`. `docs/PREREGISTRATION.md` had been
untracked through four commits, so its commit proves 2026-09-21, not the
2026-09-20 its entries carry. It costs D-1 and D-2 nothing, because the commit
still precedes every run either governs. All of `docs/` was committed in
`b253e85`.

### 14.16 Gate 1 re-measured on `Assoc`; C-2 partly passes and the grid is found to contain duplicates, 2026-09-21

182 `regular` arms on binary `9b9321b1…`. C-1 passes and C-2 reaches 18 of 26
points (18.1); T=6 passes C-2 completely, the first full pass. Two failures are
**one configuration entered twice**: L0's score is the larger of
`files / trigger` and `bytes / max_bytes_for_level_base`, so at base 8 MiB
triggers 8 and 16 are identical (L0 jobs 0.00% apart), which is PATHWAYS
assumption A3'. One point moved backwards after getting its requested repeats,
and no further arm was run. `15_top_up_hull.py` caps spend per pass rather
than per configuration, so the cap was scored by hand. **D-3's first
prediction is confirmed and it changed a comparator**: under the corrected
space denominator the runtime tie-break selected trigger 4 instead of trigger 2
at T=6.

### 14.17 Guard protocol run on `Assoc`; D-2 half-confirmed, and the calibration audited, 2026-09-22

Removing the latch roughly halved the marginal rate (to 0.1268, 0.0760 and
0.1235), under the 0.20 D-2 predicted. D-2's second prediction is falsified:
the leave-one-out estimate missed the holdout by 7.1x, 12.0x and 12.5x. E-5 cannot be measured on `oracle`. At T=10 `slo_force_due` carries 71%
of overrides, so **14.8's `l0_slowdown` guess is retracted**. Three of the six
force terms are global, and `due_age` and `pressure` grow only while a level is
due, so they latch by construction. The limits were not refitted after seeing
the result. `06_run_guard_protocol.sh` had again aborted under
`set -Eeuo pipefail`, the failure 14.8 recorded with the fix "the validator is
a scoring step, so a failing cell is a result, not an error"; it now scores
every cell.

### 14.18 The prior repaired (D-4) and the L0 band isolated (D-5), 2026-09-22

The prior's deep-level benefit (`fullness**2`) authorised compaction at
0.82-0.92 of target. D-4 replaced it with `score >= 1`: **zero releases below
due in about 20,000 merges**. At the trigger-2 cells the policy became
indistinguishable from native (+0.09% and +0.10% W, whole-run; re-scored in
14.22), and at T=6 it trades +2.07% W for -5.69% R, against +14.96% / -8.69%
before. D-4's prediction 1 failed at T=6 only: L1's phi at release came in
0.062 below the twin's, past the 0.05 tolerance. D-5 ran trigger 4 at T=2 and
T=10: the band costs **1.32-1.33x** L0 jobs at every ratio. **Both predicted
orderings failed**: read gain and write cost track populated depth, and the
band buys 3.35, 2.75 and 1.54 units of read per unit of write at T = 10, 6, 2.
At trigger 4 the paired upper bound on delta-W is +3.92%, +3.43% and +2.96%,
so **`prior_only` misses the 2% write constraint at every ratio**. E-5 passes
on the prior, but vacuously. D-4 and D-5 were committed after their arms ran,
so they are weaker records.

### 14.19 The first learned arms on `Assoc`; a railed latency multiplier, 2026-09-22

The D-7 learned arms wrote +6.1%, +13.2% and +4.6% more than `regular`
(whole-run; re-scored in 14.22), read worse at T=2 (+3.6%) and T=10 (+6.0%)
and better only at T=6 (-10.4%), and populated depth rose by two levels at
T=2 and one at T=6. D-6 is confirmed: the space multiplier sat at 1.00 in all
six arms. **The latency multiplier hit its cap of 100.0 in every arm.** The
write-latency histogram (2.9M samples, average 16.48 us, **P99 2.33 us**) has
its average above its P99, so **10.7 instrument problem 6 is a real property
of the distribution**. Over the ~500 writes in a 50 ms frame the p99 lands in
the tail constantly, against a 1.02 us limit: the inspection-paradox error
14.8 fixed for the guard with `frame_simulated_limits`. D-8 dropped the
windowed p99 hinges (not the real cause; 14.21). **E-5 fails** on `rl`, which
defers 41-43% of due frames (figures in 18.1).

### 14.20 The learner audited against the objective; D-9 realigns it, 2026-09-23

The architecture was a stall-and-latency-era controller with a constrained
reward attached, and could not express the objective. Six measured findings:

1. **The write hinge measured the wrong quantity**: a 10 s time-weighted window
   against a byte-weighted whole-run bound.

   | cell | run-level W (`rl`) | bound | window W median | frames with hinge > 0 |
   | --- | ---: | ---: | ---: | ---: |
   | T=2 | 8.40 | 8.17 | 9.25 | 94% |
   | T=6 | 8.94 | 7.97 | 12.86 | 100% |
   | T=10 | 11.08 | 10.70 | 15.76 | 99% |

2. **Every multiplier was a ratchet**: `_dual_ascent` added the hinge, never
   the signed slack.
3. **The action space was asymmetric the wrong way.** It withheld "compact L0
   later" (`RL_L0_ALLOW_DEFER=0`) and offered "compact any level early" and
   "compact L0 at one file", and the residual re-learned both. D-7 `rl`
   repeat 1 against the static twin (workload phase, trivial moves excluded;
   the twin released nothing below 0.95):

   | cell | level | rl releases | rl $\varphi$ median | rl below 0.95 | twin $\varphi$ | rl $\eta$ | twin $\eta$ |
   | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
   | T=2 | L1 | 935 | 0.41 | 686 | 1.09 | 0.93 | 0.84 |
   | T=2 | L2 | 1079 | 0.38 | 940 | 1.03 | 0.89 | 0.76 |
   | T=2 | L3 | 927 | 0.51 | 803 | 1.01 | 0.79 | 0.77 |
   | T=6 | L2 | 2110 | 0.75 | 1166 | 1.03 | 0.88 | 0.88 |
   | T=10 | L1 | 2240 | 1.02 | 330 | 1.12 | 0.91 | 0.93 |

   At T=2 the learner compacted L1 and L2 at about 40% of target. That
   eagerness, not deferral, was D-7's depth mechanism. On L0 it compacted at a
   mean of 1.1 files on 16-26% of below-trigger frames, the 14.10 defect.
4. **The state could not represent the problem.** Of 31 features none was write
   amplification, a multiplier or a garbage signal, and eleven were stall-era
   pressure terms. With multipliers moving and absent from the state, replay
   mixed rewards priced under different $\lambda$.
5. **The horizon was too short.** $\gamma$ = 0.95 per second weighs a cost
   20 s away at 0.36 and 60 s away at 0.05, so deferral looked free and early
   compaction cheap.
6. **The prior mattered only at step 0**: prior advantage 0.6-0.7 against a
   residual of 258-652.

**D-9** (Python and runner only): a component-vector reward (signed marginal
terms for W, R, seeks and stall; hinges for space bytes and the latency
averages); signed multiplier steps, with replay re-priced at sample time
(`rl_agent/lagrange.py`); the D-4 rule as an action mask; the L0 posture lifted
for learned arms; 37 state inputs; $\gamma$ 0.98 per second with an 8 s credit
window; Huber loss. D-7 and the 2026-09-03 matrix tested the harness, not the
objective.

### 14.21 D-9's smoke gate stops on the multiplier instrument; D-10 repairs it, 2026-09-23

The smoke arm stopped the matrix with the latency multiplier at 100.0, though
the arm itself was the best learned arm so far. Three faults: (1) **two
instruments, one hinge.** Frame latencies come from C++ telemetry and limits
from db_bench's histogram, and for scans they differ 19-25x, so the scan hinge
read +18 on every frame; the D-7 latency excess was `scan_avg` all along, and
D-8's prediction 3 is falsified at 0%. (2) **Two denominators**: the evaluator
counted WriteBatch framing (1,054 against 1,024.4 bytes per Put), so the
reward's run-to-date W read 2.9% above the evaluator's and D-9's prediction 1
failed by exactly that. (3) **The backlog transient**: run-to-date W was 17
over the first 16 s against a bound of 8.2. D-10 adds telemetry-unit latency
references, treats latency averages as flows, adds the framing and holds
multipliers still for 30 s. This was the **fifth** instrument to compare a
per-frame or one-instrument statistic with a whole-run or other-instrument
limit. The first question for any new term: are its value and its limit the
same quantity, from the same instrument?

### 14.22 The evaluator was scoring the bulk load; D-11 corrects it and re-scores the `Assoc` record, 2026-09-23

**`resetstats` never reset the tickers.** It calls `DB::ResetStats`, which
clears internal stats only; `rocksdb.number.keys.written` reads 4,029,089 (the
2.9M load plus 1.13M Puts). So every ticker-based write amplification, stall
time and write latency from 2026-09-20 to 2026-09-23 covered the whole run,
and the load's own scatter rivals the policy effect. Re-scored measured-phase
write: the prior at trigger 2, +2.75% and +0.45% (recorded +0.09%, +0.10%); the
band, +14.4% and +6.6% (recorded +2.9%, +2.1%); the D-7 learner,
+24/+43/+13%. The evaluator now reads physical bytes from the event log inside
`[RL_CONTROL_RESUMED_MICROS, RL_DRAIN_END_MICROS]`, user bytes as the ticker
less the load's exact bytes (64 + 960 + 16 framing per record), stalls from
`Cumulative stall`, and latency from db_bench's per-benchmark histograms;
reward and evaluator then agree to 0.015%. Comparators did not move. The
re-extracted hulls still pass C-1 (8 / 7 / 8 points of 12), but **C-2 at T=6
flips to fail**: honest write scatter is three to nine times larger, and the
14.7 ten-repeat justification no longer holds as stated. The cross-T pooled
hull keeps 14 of 38 points, but only 9 are the same points. Oracle parity
still passes. The "native at trigger 2" reading of the prior is withdrawn. The
2% write margin, as scored, had been about 7% of the measured phase. Every
earlier learner or prior verdict that cites a write figure is a whole-run
figure and is superseded by the D-11 table. This was the sixth instrument
defect, and the first in the evaluator.

### 14.23 The latency multiplier read the load's tail; D-12 measures its slack from the warm-up against the baseline's own trajectory, 2026-09-23

The D-11 smoke stopped with `lambda_latency` at **27.75** against a limit of 5,
yet the arm's *window* write latency sat **16-19% under** its limit from 10 s
on. The run-to-date average carried the first two seconds after `rlresume`:
the tree inherited from the bulk load held L0 at 16 (D-10 arm) or 22 (D-11
arm) files against a slowdown trigger of 20, and writes stalled at 15x and 32x
the limit. **The
baseline reads the same way**: the calibration arms' cumulative write latency
crosses its limit only at **120-150 s of a 160 s run**, so any policy reads a
positive slack for most of the run. This was the sixth instrument to compare
unequal quantities, in a new variant: same metric and instrument, different
elapsed time. **D-12** measures the slack from the end of the 30 s warm-up,
against the baseline's own cumulative over the same span, from a trajectory
written into the manifest; the priced term is unchanged. The first D-12 smoke
was invalid twice. `rl_safety_manifest.cc` reads each key by its **first
textual match**, so a nested `schema_version` read as 1 and the guard rejected
the manifest; and the first frame after `rlresume` carried 14,100 load writes,
so the reward now leaves it out. The seventh instance of the family, and the
first in the reward's own accounting.

### 14.24 D-12 scored: the first evidential learner run, and the learned trigger does not beat native RocksDB, 2026-09-23

**The harness is right**: reward and evaluator write agree within 0.095%, no
deep level was released early, and the tree was no deeper than its twin. **The
policy loses or ties everywhere** against same-seed twins (figures in 18.1).
Against the static frontier it is dominated at T=2, undominated but inside the
frontier's bend at T=6, and at T=10 undominated only because it is its own
twin. At T=10 it is the first learned arm to pass the 2% write margin, by doing
what native does. Across size ratios the T=2 learner is beaten outright by two
static T=6 configurations. Realignment cut the D-7 write excess from
+24/+43/+13% to +7/+10/-1%. The answer at three repeats: a learned trigger
alone does not buy point reads at write parity on this workload.

**Corrected the same day** (`docs/AUDIT_2026-09-23_D12_AND_PATHWAY_A.md`, at
`cb45743`). The T=2 write excess falls in the first 30 s, before training:
exploration while the backlog drained turned native's free file moves into
merges. Part of the deficit is session drift (+3-4% W). The audit's wider
finding is that at the trigger-2 comparators no static setting reads less
within +2% write, so the objective has almost no room there, and Pathway A's
only read lever, removing a level, is static. Exploration is the proximate flaw; 19-39%
of answers are rejected as stale, but that did not cause the failures. Options
weighed: a slow settings tuner (recommended), model-based planning, fixing the
DQN, and a C++ port (not recommended). **No direction has been chosen.**

## 15. Current limitations and next work

Written 2026-09-05; `docs/PATHWAYS.md` supersedes it as the forward plan.

1. ~~Decide the objective.~~ Done 2026-09-05 (Section 3.1).
2. Make space a hinge in the reward. Qualified 2026-09-19 (14.9): re-weighting
   alone can no longer produce an accepting result.
3. Address write amplification directly, or accept it as the cost.
4. Resolve the 10.7 instrument problems; repair guard calibration separately.
5. Then run ten repeats, the stress suites and the full frontier.

Open at the last entry (2026-09-23): the architecture choice (14.24); the
post-load transient that the guard limits and the write and scan multipliers
still carry; a trajectory reference for W, recorded but not taken; and a C-2
top-up on the measured write axis (26 arms, about 1.3 h), left as its own
decision. The stress suites are listed as not run (14.5).

## 16. Guide to the existing documentation

`docs/PATHWAYS.md` is the forward plan (tracked since 2026-09-21; 14.15).
`docs/PREREGISTRATION.md` holds dated decisions, advance predictions and
verdicts. `scripts/dbbench_pipeline/README.md` explains how to run the
pipeline. Files this record names that no longer exist can be read at commit
`cb45743`.

## 17. Glossary

- **Level permit:** held trigger eligibility for one level over a control
  interval; due permits can schedule repeatedly, optional permits once.
- **Sorted run:** an independently searchable sorted sequence; each L0 file is
  one.

## 18. Bottom line

The learner that never trained (2026-08-24 to 2026-09-01) was repaired. The
first trained matrix (2026-09-03) was negative: **no arm improved write
amplification in any cell**, and the claim that the prior was the strongest
read policy was **withdrawn on 2026-09-19** (14.9). The objective became constrained on 2026-09-05 (3.1).
After the instrument repairs of 14.20-14.23, the first evidential learner run
(14.24) found that a learned trigger alone does not buy point reads at write
parity on `Assoc`. The lasting gain is a measurement and control path that
shows what an RL decision actually did, with RocksDB keeping sole authority
over file selection.

### 18.1 Programme summary (moved from PATHWAYS Appendix R, 2026-10-01)
*Editor's note: 14.22 later re-scored the `Assoc` figures on the measured phase (C-1 re-extracted as 8/7/8 of 12; the Gate 0 and repaired-prior write figures quoted here are whole-run, D-12's are measured-phase); D-12's "same-session twins" ran days before the learner arms, and PREREGISTRATION's D-12 results attribute part of the T=2 result to that session drift (see also 14.24); "the history document" and "§0.6" in the block below mean this file and PATHWAYS §0.6.*

Dated verdicts are in `docs/PREREGISTRATION.md`; narratives are in the history
document (§§10.7, 14.7–14.24).

**Gate 0 (complete).** A-0 decomposition of the write excess, paired means over
five repeats (uniform workload, 2026-09-11):

| cell | arm | excess | depth part (GiB) | eagerness part (GiB) | depth share |
| --- | --- | ---: | ---: | ---: | ---: |
| 10M T=2 | `prior_only` | +18.2% | 0.00 | +5.24 | 0.00 |
| | `rl` | +15.8% | +1.08 | +3.48 | 0.22 |
| | `unconstrained_rl` | +16.0% | +0.76 | +3.84 | 0.18 |
| 10M T=6 | `prior_only` | +15.2% | 0.00 | +4.92 | 0.00 |
| | `rl` | +22.8% | +0.68 | +6.68 | 0.14 |
| | `unconstrained_rl` | +50.6% | +0.28 | +16.08 | 0.02 |
| 10M T=10 | `prior_only` | +13.7% | 0.00 | +4.98 | 0.00 |
| | `rl` | +9.7% | 0.00 | +3.52 | 0.00 |
| | `unconstrained_rl` | +13.4% | 0.00 | +4.86 | 0.00 |
| 20M T=2 | `prior_only` | +15.2% | 0.00 | +10.12 | 0.00 |
| | `rl` | +10.0% | +2.00 | +4.62 | 0.35 |
| | `unconstrained_rl` | +16.7% | +1.76 | +9.32 | 0.18 |

**Gate 1 (partial).** Uniform workload (2026-09-19, superseded): C-1 passed;
C-2, C-3 and C-6 failed — the prior was dominated by one static point on both
$W$ and $R$ at every $T$, and Finding 2 was withdrawn. C-5 closed at
$s_{\max} = 2.0$ (27 capacity arms, 2026-09-22). `Assoc`: C-1 passed (11, 7
and 8 hull points); C-2 partial (18 of 26 points decidable); comparators trigger
2/4/2 at a 16 MiB base; the cross-$T$ hull holds 14 of 38 points. The repaired
prior on `Assoc` (D-4, D-5) is native at deep levels and uses only the L0 band:
$\Delta W$ = +2.9/+2.1/+2.1% for $\Delta R$ = −4.5/−5.7/−7.1% at $T$ = 2/6/10
against same-configuration twins — the L0 lever of Proposition D.11 at work.

**Pathway D statuses (constrained objective).** D-7: the learner wrote more with
a multiplier railed at its cap. D-8 to D-11 repaired reward, state, action mask
and measurement. D-12 (first valid instrument run): against same-session twins,
T=2 +6.6% $W$ / +3.0% $R$; T=6 +9.7% / −7.1%, via L0 early-compaction lock-in;
T=10 −0.6% / +1.1%, native behaviour. The audit (2026-09-23) attributes the T=2
result to start-up exploration during the backlog plus session drift, not to a
learned deferral lever.

**Pathway E.** E-1 recorded failed; E-2 passed under the populated-level
denominator; E-5 failed on `rl` (15, 7 and 10 times the limit at T = 2, 6, 10),
with the post-load transient identified by the audit.

**Retired criteria** (their records stand; they are not scored again): A-0 to A-5,
B-2, C-3, C-4, C-5, the Pathway D criteria D-1 to D-5 of 2026-09-11, and Gates 1
to 6 of 2026-09-11 (Gate 0 complete, Gate 1 partial, Gates 2–6 never run).

**Frozen preregistered decisions.** P0, P1, P1b and P1c in
`docs/PREREGISTRATION.md` remain the record of the 2026-09-11 programme. Items
this fork supersedes are listed in §0.6 and need dated amendments there.