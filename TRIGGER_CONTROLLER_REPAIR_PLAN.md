# Trigger-Only Compaction Controller Repair Plan

**Status:** Proposed design for review; no implementation is authorized by this
document.

**Prepared:** 2026-08-16

**Evidence set:** `results/logs/1M-T2/{regular,rl}`

**Revision 5, 2026-08-16.** Closes the remaining implementation blockers found
in the revision-4 review:

1. The synchronous `WakeHandle` is replaced by a DB-owned asynchronous
   `RLControlCoordinator`. Picker workers enqueue ID-and-generation requests and
   never wait for the DB mutex. Registrations have a separate column-family
   lifetime generation; creation, drop, ordinary final destruction, and DB
   shutdown all have explicit attach/invalidate/stop ordering.
2. The same coordinator owns a keyed timer queue for coalesced structural
   refreshes. A rate-limited change therefore has a concrete executor that will
   acquire the DB mutex, resolve the still-live column family, rebuild the latest
   state, and make `built_generation` converge to `source_generation` even after
   the plant becomes idle.
3. Due age and excess pressure now come from one shared, event-driven score
   observer used by regular and RL pickers. The observer uses zero-order hold at
   every active-score recomputation; the worker only extends the last exact
   state to `now`. All remaining trapezoidal language is removed.
4. `BLOCKED_OPEN` uses a monotonic deadline and an explicit retry wake rather
   than workload-dependent "scheduling quanta."
5. The phase gates, tests, telemetry, SLO mask, and feasibility rule are aligned
   with those mechanisms. Headroom is required for strict-improvement metrics;
   boundary feasibility, rather than two-sided headroom, is required for
   one-sided non-regression limits.

**Revision 4, 2026-08-16.** Incorporates reviewer findings against revision 3.
Four were blocking and are fixed here:

1. **Wakeup was armed only on the forced-open edge.** The same stranded-permit
   failure occurs for policy `DUE_OPEN`, optional-token grants, and fallback
   entry. Since the Phase 1b oracle *is* a policy that flips due levels to
   compact, deferring the wakeup to Phase 2 would have made Phase 1b's own
   parity gate unreachable. Section 7.8.2c now arms on **any**
   closed-to-eligible edge, with the aggregate-edge formulation and
   generation-based deduplication, and **the wake coordinator moves into Phase
   1b**. New tests 25 and 19.
2. **The clearable `std::function` was neither lifetime nor deadlock safe.**
   The picker destructor joins the worker; `~ColumnFamilyData` runs with the DB
   mutex held (`db/column_family.cc:743`) and `DBImpl` destroys `VersionSet`
   under it (`db/db_impl/db_impl.cc:697`), so a worker blocked acquiring that
   mutex inside a wake call deadlocks against its own join. Clearing a
   `std::function` concurrently with copying it is also a race, and `DBImpl`
   never constructs the picker — `ColumnFamilyData` does, at
   `db/column_family.cc:695`. Replaced with a `DBImpl`-owned refcounted
   `WakeHandle`: ID-and-generation requests, close-then-detach ordering, drain
   and join outside the mutex, explicit post-creation attachment. New
   `SyncPoint` test 26, because TSan cannot detect a deadlock.
3. **The rebuild limiter could lose the final tree update forever.** A change
   suppressed inside the limiter window, followed by idleness, is never
   published. The limiter must coalesce: dirty bit, `source_generation` /
   `built_generation`, one guaranteed deferred rebuild, and a stale view
   treated conservatively. Separately, the cache cannot be an `RLStateV2`,
   which mixes structural fields with per-decision execution outcomes
   (`compaction_picker_rl.cc:118` onward) that caching would freeze; split into
   `RLStructuralSnapshot` plus a per-observation overlay. Three distinct ages
   distinguished, with `dirty_age` replacing absolute snapshot age as the
   Phase 1a gate. New test 24.
4. **Trapezoidal integration is wrong for stepwise scores.** Compaction scores
   are piecewise constant; a 1-to-13 step charges roughly six spurious
   score-seconds under a trapezoid, and 13-to-1 errs the other way. Replaced
   with zero-order hold plus interval splitting at the change timestamp, which
   in turn constrains coalescing: accumulate at score-change hooks or preserve
   intermediate transitions. `epsilon` is now decomposed into tick, coalescing,
   queue and admission terms and must be measured. New tests 27 and 28.

Three further corrections: Phase 1a tests 13 and 23 contradicted each other and
test 17 exercised a Phase 2 watchdog (both fixed); Phase 1c had no mechanism for
collecting *baseline* episodes, since shadow clocks live in a picker that
regular leveled runs never construct, and its manifest now carries the original
SLO latency/space fields; and new section 14.5 adds a metric-sensitivity gate,
because baseline scan amplification is exactly 1.000 — the metric's floor — so
the existing "CI strictly below zero" criterion is unsatisfiable by
construction. Smaller fixes: duplicated table header in 3.2, `ScanEntries`
7,353,511 rather than 7,353,443, the `04_generate_graphs.py` metric definitions,
the observation-generating event population, and a falsification condition now
stated on realized service rather than decision rate.

**Revision 3, 2026-08-16.** Incorporated reviewer findings against revision 2.
Three were blocking:

1. **Forced-open could not make an idle scheduler run.** Revision 2's
   "preferred" design installed a permit and relied on "the next scheduler
   event", but the motivating case is an idle plant where no such event exists.
   A permit changes eligibility; it does not enqueue work.
   `RLCompactionPicker` holds no `DBImpl`, `ColumnFamilyData`, or mutex handle
   with which to schedule anything. **Section 7.8.2 gains part (c): an explicit,
   lifetime-safe scheduler wakeup** through `EnqueuePendingCompaction` and
   `MaybeScheduleFlushOrCompaction`, plus requirements R3 and R6, lock-order
   rules covering `snap_mu_` as well as `permit_mu_`, and acceptance test 19,
   which asserts a scheduling *attempt* rather than a changed permit bit. The
   worker-initiated pull is replaced by a cached immutable snapshot, because a
   full `BuildSnapshot` under the DB mutex every 50 ms is not bounded at
   50M/T2.
2. **The "956 trivial moves" figure was a double count.** RocksDB logs one line
   per file *and* one summary per job. Authoritative event-log counts are 233
   jobs / 860 files for the run and 205 jobs / 751 files in the busiest second,
   so `956 = 205 + 751` and `1093 = 233 + 860`. The original 205 was correct.
   The service-rate mismatch is about **315x**, not three orders of magnitude.
3. **The read-amplification figures were mislabelled.** `(non-last + last read
   bytes) / bytes.read` is a physical read-byte diagnostic, not the project's
   read amplification: the numerator counts all physical file reads including
   compaction, the denominator counts Get bytes. The project's definitions
   (`PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md` section 2.3) are logical. Point
   RA is probes/Get, 5.664 -> 8.556. Formal scan amplification is **1.000 in
   both arms**, since `rocksdb.number.iter.skip` is 0, so this run does not
   prove a scan-amplification regression.

Four further corrections: Phase 1a's predicted result was backwards and is
reversed (section 7.8.4, Phase 1a); Phase 1a/Phase 2 overlapped on `A_i`/`P_i`
and Phase 1a is now observation-only with shadow clocks; `N_min = 30` was far
too small for a `Q99` and `1.02` was misdescribed as the project's 2 percent
tolerance (section 7.4); the missing tuned-baseline prerequisite is now
**Phase 1c**. Evidence language in sections 3.2 and 3.8 was softened where it
outran what the data supports.

**Revision 2, 2026-08-16.** Review pass against the source and the evidence set.
All original code-level claims were verified as accurate. Changes in that
revision:

**Substantive additions:**

- **New section 3.8** documents a second, independent defect: snapshot
  publication has a single call site inside `NeedsCompaction()`, so deferring
  work suppresses the observations needed to revise or bound that deferral.
  Measured 2.6 observations/s against a nominal 20/s, with 694 of 769 worker
  ticks blind.
- **New section 7.8** specifies the required decoupling, with explicit
  requirements R1--R4, a two-part mechanism, and the argument for why it is a
  Phase 1 prerequisite rather than a later refinement.
- **Section 1** now lists four companion changes rather than three.
- **Phase 1 split into 1a and 1b** so that a parity failure is not ambiguous
  between trigger latency and gate semantics.

**Corrections to the original analysis:**

- **Section 3.2** did not report read amplification under the project's own
  defined formula, which is half the stated success criterion. Added, with the
  derivation, the scan-inclusive variant, the compaction-rate row that explains
  the rest of the table, and an explicit note that the two arms ran a
  byte-identical workload.
- **Section 3.4** used the agent's L1 action probability (0.56) as the
  arbitration win rate. Only one level wins per decision; the lease histogram
  gives 25/75 = 0.33, so the service rate is 0.65 ops/s, not 1.09, and the
  drain estimate is ~150 s, not ~90 s.
- **Section 3.4**'s trivial-move figure was briefly "corrected" to 956 in an
  earlier pass of this revision. That was wrong: it double counted per-file
  lines against job summaries. The original 205 was right. Authoritative counts
  from the event log are 233 jobs / 860 files for the run and 205 jobs / 751
  files in the busiest second, giving a service-rate mismatch of about **315x**,
  not three orders of magnitude.
- **Section 3.5** mean input per compaction corrected to 43.3 MB / 169.0 MB
  from the `L1` row, with the `Rnp1` and `W-Amp` rows added to isolate the
  mechanism.
- **Section 3.6** now scopes the `defer_count` reset defect to *policy*
  compacts only; the budget-forced path at `:435-439` is currently correct and
  must not be changed with it.
- **Section 5.3** flags that `H_stale = max(1 s, 20 x Delta)` is calibrated
  against the nominal interval, not the achieved 512 ms spacing, and would fire
  against a healthy server.
- **Section 7.3** notes that "invariant to policy query frequency" does not
  imply invariant to scheduler-observation frequency, which was the actual
  binding constraint.
- **Section 7.4** adds a minimum-episode requirement before a `Q99` envelope is
  used; L3 and L4 had two episodes each in the baseline.
- **Section 5.2** bounds `BLOCKED_OPEN`, which was previously defined but
  unbounded.

**Consequent updates:**

- Sections 10.1, 10.2, 10.3, 10.6, 10.7 (worker responsibilities, telemetry,
  lock ordering); 12.1 tests 13--18; 14.1 invariants 11--13; 14.2 and 14.3
  observation-health preconditions; 16.7 and 16.8 rejected approaches; 17 risk
  rows; 18 file list; 19 decisions 11--14; 20 definition of done.

## 1. Executive decision

The next change must repair the C++ trigger bridge before changing the DQN,
reward, prior, or workload. The present bridge interprets a binary
`compact` response as a one-use pulse that may schedule one compaction. That is
not the correct semantics for a trigger controller whose plant, RocksDB's
background scheduler, can perform many compaction scheduling operations between
two policy observations.

The repaired controller will interpret each per-level action as a **gate held
over a control interval**:

```text
defer   -> close this source level's policy gate
compact -> open this source level's policy gate
```

While a due level's gate is open, RocksDB may repeatedly schedule native
compactions from that level until its current compaction score falls below the
normal due threshold. Every individual call still goes through RocksDB's
configured `FilesByCompactionPri`, clean-cut expansion, overlap checks,
conflict checks, compaction construction, and background execution. The RL
controller never supplies or chooses an SST.

For a below-threshold, proactive `compact` action, the gate carries only one
optional token. This prevents one proactive action from draining an otherwise
healthy level.

Four other changes are required at the same time:

1. replace decision-count deferral with wall-clock and pressure-integrated
   deferral accounting;
2. choose among simultaneously authorized levels using RocksDB's current
   compaction-score ordering rather than a stale score or an emergency-first
   rule that can starve deeper levels;
3. attribute zero, one, or many native compactions to one level/action interval
   without turning any of those compactions into an RL file-picking action;
4. **decouple observation supply and safety-clock advance from
   `NeedsCompaction()`**, so that deferring work cannot suppress the very
   signal needed to revise or bound that deferral (section 3.8, section 7.8).

Item 4 is not an optimization. Held gates greatly reduce sensitivity to
observation rate, but they do not remove the closed loop: with the sensing path
unchanged, choosing `defer` still stops the scheduler from calling
`NeedsCompaction()`, which stops snapshot publication, which prevents both the
next policy response and any advance of the wall-clock safety state introduced
in section 7. Sustained deferral would then be terminated by the staleness
watchdog rather than by policy or safety, and the section 7 bound would not in
fact be invariant to anything. Item 4 is therefore a Phase 1 prerequisite, not
a later refinement.

No large experiment should run until an oracle policy that opens every level
RocksDB considers due reproduces ordinary leveled behavior within a predefined
parity envelope.

## 2. Scope and non-negotiable invariants

### 2.1 The policy controls only trigger eligibility

For every source level `i`, the policy action remains binary:

\[
a_i(k) \in \{0,1\},
\]

where decision index `k` is produced at wall-clock time `tau_k`:

- `a_i(k) = 0`: defer/close the policy gate for level `i`;
- `a_i(k) = 1`: compact/open the policy gate for level `i`.

The action contains no file number, key range, overlap set, or candidate rank.

### 2.2 RocksDB retains all file-selection authority

For due work, the bridge should pass an allowed-source-level mask into the
native leveled builder. The builder then performs its existing score ordering,
L0/base-level starvation prevention, and file selection while skipping only
levels whose trigger gates are closed. For a below-threshold optional token,
the bridge may call `PickCompactionFromLevel(...)` for that authorized source
level. Both paths must continue into native:

1. `FilesByCompactionPri(source_level)` ordering;
2. `NextCompactionIndex(source_level)` handling;
3. `being_compacted` and output-conflict checks;
4. clean-cut source expansion;
5. output-level overlap expansion;
6. trivial-move determination;
7. compaction registration and score recomputation.

The repair must not restore candidate previews, exact-file actions, candidate
encoders, or protocol v3.

### 2.3 Authorization may not leak across levels

Let `G_i(t)` be the effective gate for source level `i`. A policy-controlled
compaction from `i` is legal only if `G_i(t) = 1`. If level 1 is open and level
2 is deferred, a failed level-1 pick must not authorize level 2. Level 2 may be
tried only if it independently has policy or safety authorization.

### 2.4 Safety may override policy, but must be explicit

Maintenance, final drain, unavailable-server fallback, a hard L0 condition,
or exhausted deferral safety may override policy. Every override must carry an
explicit reason and must be excluded from policy replay where the recorded
action no longer caused the execution.

### 2.5 Control decisions are level signals, not file actions

One decision may lead to:

- zero compactions because the level became healthy or all native picks were
  temporarily blocked;
- one compaction;
- multiple compactions selected independently by RocksDB.

The multiplicity is plant behavior under one held trigger signal. It is not a
candidate action space.

## 3. What the 1M/T2 evidence proves

### 3.1 Configuration parity

The two arms used the same meaningful storage parameters except for compaction
style:

| Parameter | Regular | RL |
| --- | ---: | ---: |
| Number of levels | 13 | 13 |
| Write buffer | 2 MiB | 2 MiB |
| Target SST size | 512 KiB | 512 KiB |
| L1 target | 16 MiB | 16 MiB |
| Size ratio | 2 | 2 |
| L0 compact/slow/stop | 4/20/36 | 4/20/36 |
| Compaction priority | `kMinOverlappingRatio` | `kMinOverlappingRatio` |
| Compression | none | none |
| WAL | disabled | disabled |

The failure therefore cannot be explained by a different level count, write
buffer, target file size, L1 target, compression mode, or L0 threshold.

### 3.2 Measured regression

Using the counters in the supplied `run.log` files, and the formal project
metric definitions in `PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md` section 2.3:

| Metric | Regular | RL | RL change |
| --- | ---: | ---: | ---: |
| Write amplification | 4.332 | 9.504 | +119% |
| Point-read amplification, probes/Get | 5.664 | 8.556 | +51% |
| Scan amplification, `(returned + skipped)/returned` | 1.000 | 1.000 | none |
| Sorted-run seeks/scan (reported separately) | 6.731 | 8.740 | +30% |

Diagnostics, not amplification objectives:

| Diagnostic | Regular | RL | RL change |
| --- | ---: | ---: | ---: |
| Physical read bytes / Get bytes | 41.69 | 53.34 | +28% |
| Physical read bytes / (Get + iterator bytes) | 1.826 | 2.335 | +28% |
| Stall duration | 4.15 s | 10.97 s | +164% |
| Write p99 | 9.78 us | 1,966.62 us | +20,000% |
| Initial-load time | 5.63 s | 11.96 s | +112% |
| Scan p95 | 48.49 us | 72.12 us | +49% |
| Compactions/s | 4.46 | 0.65 | -85% |

**What this run does and does not prove.** Write amplification and point-read
amplification both regressed, and sorted-run search work rose 30%. That is
sufficient to fail the project's success criterion, which requires both
amplifications to fall.

It does **not** prove a formal scan-amplification regression.
`rocksdb.number.iter.skip` is 0 in both arms, so
`(7,353,511 + 0) / 7,353,511 = 1.000` on both sides. The returned-entry count
is `ScanEntries` from the mixgraph line, which is 7,353,511 and equals
`rocksdb.number.db.next`; `rocksdb.number.db.next.found` is 7,353,443 and is a
different quantity. The scan cost that did rise
is visible as sorted-run seeks and as scan p95 latency, which are reported
separately and are not the scan-amplification metric. Claiming a scan-amp
regression from this evidence would be unsupported.

The `Compactions/s` row is the one that explains the rest, and it is the
quantity the repair targets.

The two arms ran the same workload by construction: `rocksdb.bytes.written`
415,778,480, `rocksdb.bytes.read` 355,565,760, `rocksdb.db.iter.bytes.read`
7,765,273,600, 370,381 Gets, 229,832 Seeks, and 209 flushes in both. That rules
out workload composition as an explanation. It does not rule out operation
order, cache state, thermal, or scheduling noise, so these are single-run
figures and paired repeats remain required before any of them is quoted as a
result.

The WAF calculation is:

\[
\mathrm{WAF} =
\frac{B_{flush,written} + B_{compaction,written}}
     {B_{user,logical}}.
\]

For the RL arm:

\[
\mathrm{WAF}_{RL} =
\frac{413{,}830{,}498 + 3{,}537{,}612{,}147}
     {415{,}778{,}480}
= 9.504.
\]

For the regular arm:

\[
\mathrm{WAF}_{regular} =
\frac{413{,}818{,}970 + 1{,}387{,}150{,}596}
     {415{,}778{,}480}
= 4.332.
\]

The physical read-byte diagnostic is:

\[
\frac{B_{nonlast,read} + B_{last,read}}
     {B_{user,read}}.
\]

\[
\text{RL} = \frac{18{,}965{,}577{,}903 + 0}{355{,}565{,}760} = 53.34,
\qquad
\text{regular} = \frac{14{,}824{,}711{,}961 + 0}{355{,}565{,}760} = 41.69.
\]

**This ratio must not be labelled read amplification.** It is not
dimensionally coherent: the numerator counts physical file-read bytes from
every source, including compaction reads, while the denominator counts Get
bytes only. The variant that divides by `bytes.read + db.iter.bytes.read` is a
combined physical-I/O ratio, not scan amplification. Both are retained above as
diagnostics because they track the direction of the regression, and because
this formula was the one used in the earlier vanilla db_bench sweep analysis.
Neither is the project's amplification objective, and neither is what the
pipeline reports: `scripts/dbbench_pipeline/04_generate_graphs.py:120-122`
computes `point_read_amplification = point_probes / gets` and
`scan_amplification = (scan_returned + scan_skips) / scan_returned`, i.e. the
logical definitions.

`rocksdb.last.level.read.bytes` is zero in both arms because the configured
last level (L12 of 13) was never populated, so all physical reads are
classified non-last-level.

The project's authoritative definitions
(`PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md` section 2.3) are logical, not
physical, precisely so that block-cache and Bloom-filter effects cannot move
the objective without changing the number of runs the tree exposes:

- **point-read amplification** = logical SST/table probes per point read,
  including probes rejected by a Bloom filter, which is
  `rocksdb.point.sst.probe / gets` = 5.664 -> 8.556;
- **scan amplification** = `(returned entries + internal entries skipped) /
  returned entries` = `(7,353,511 + 0) / 7,353,511` = 1.000 in both arms.

### 3.3 The level score shows trigger starvation

For a non-L0 level `i`, RocksDB's size score is approximately

\[
s_i(t) = \frac{B_i(t)}{M_i(t)},
\]

where `B_i` is the level's bytes and `M_i` is its configured maximum. The level
is normally due when `s_i >= 1`.

At 24 seconds, the RL arm had:

```text
L0: 15 files, score 3.8
L1: 219.17 MB, score 13.7
L2: 68.69 MB, score 2.1
estimated pending compaction bytes: 851,618,935
```

With a 16 MiB L1 target,

\[
\frac{219.17}{16} \approx 13.70,
\]

matching the logged score. A score of 13.7 is not a small learned deferral. It
means the controller allowed roughly 12.7 L1 capacities of excess data to
remain unresolved.

At the corresponding regular checkpoint, L1 was about 15.69 MB with 27 files,
and the maximum live score was near 1. The regular arm's pending debt was about
92.6 MB rather than 851.6 MB.

### 3.4 Queueing model of the failure

Let

- `x_i(t) = max(0, B_i(t) - M_i(t))` be excess bytes at level `i`;
- `lambda_i(t)` be the byte-arrival rate into `i`;
- `mu_i(t)` be RocksDB's achievable service rate out of `i` when eligible;
- `u_i(t) in {0,1}` indicate whether the controller permits service.

A simple fluid approximation is:

\[
\frac{dx_i(t)}{dt} = \lambda_i(t) - u_i(t)\mu_i(t),
\quad x_i(t) > 0.
\]

For stability over a sufficiently long interval,

\[
\mathbb{E}[u_i(t)\mu_i(t)] > \mathbb{E}[\lambda_i(t)].
\]

The current bridge does not hold `u_i=1`. Instead, it emits at most one service
quantum per successful decision. If

- `q` is usable decision rate in decisions/second;
- `p_i` is the fraction of decisions that ultimately select level `i`;
- `c_i` is mean bytes removed or moved by one native compaction;

then current maximum service is approximately

\[
\mu_{pulse,i} = q p_i c_i.
\]

The supplied run recorded 75 actuations over approximately 38.5 seconds:

\[
q \approx \frac{75}{38.5} = 1.95\ \text{decisions/s}.
\]

`p_i` must be measured as the fraction of decisions in which level `i` actually
wins the single global authority, not as the agent's action probability for
that level. The server line `compact=0.56` at step 50 is the latter and is not
usable here, because `RunDecisionCycle` installs at most one lease per response
(`compaction_picker_rl.cc:483`) and explicitly demotes every other positive
level (`:476-481`). The correct figure comes from the lease histogram in
`rl/rocksdb_LOG.txt`, which records 53 leases over 75 actuations:

```text
25  level=1 reason=0 (policy)
14  level=0 reason=3 (emergency)
 7  level=0 reason=0 (policy)
 3  level=2 reason=0 (policy)
 3  level=0 reason=1 (budget)
 1  level=0 reason=4 (fallback)
```

Hence

\[
p_1 = \frac{25}{75} = 0.33,
\qquad
q p_1 \approx 1.95 \times 0.33 = 0.65
\]

level-1 scheduling operations per second. Note also that levels 3, 4 and 5
received zero leases for the entire run, and the final compaction statistics
confirm `Comp(cnt) = 0` for L2 through L5: everything below L1 was reachable
only by trivial move.

With a 512 KiB target file and typical trivial moves of one to four files, one
operation may service roughly 0.5--2 MiB. Removing approximately

`219 - 16 = 203 MB` of L1 excess can therefore require around 100 or more
native scheduling operations. At about 0.65 operations/s, this is on the order
of 150 seconds, roughly four times the entire run. By contrast, the native
final-drain path performed **205 trivial-move jobs within one logged second**
(21:01:14), moving 751 files. Over the whole run there were 233 such jobs
moving 860 files.

The correct unit for comparison against a scheduling rate is the **job**, not
the file and not the log line. RocksDB emits one `[...:1479] Moved #<file> to
level-N` line per file *and* one `[...:4430] Moved #<count> files to level-N`
summary per job, so a naive `grep -c "Moved #"` double counts:

```text
233 job summaries + 860 per-file lines = 1093   (not a move count)
205 job summaries + 751 per-file lines =  956   (not a move count)
```

The authoritative count is the event log, `"event": "trivial_move"`, which
yields 233 jobs for the run and 205 in the busiest second. Against the bridge's
0.65 scheduling operations/s:

\[
\frac{205}{0.65} \approx 315.
\]

RocksDB demonstrated a metadata-move scheduling rate roughly **315 times**
what the bridge admitted. That remains a severe actuator-rate mismatch, and it
is the correct magnitude to quote.

This is a classic actuator-rate mismatch: inference cadence throttles storage
service even after the policy has said `compact`.

### 3.5 Positive feedback into L0 write amplification

For an L0-to-L1 compaction, let:

- `X_0` be expanded L0 input bytes;
- `O_1` be exact overlapping L1 bytes;
- `G` be bytes removed by overwritten/deleted entries.

Ignoring small metadata overhead, physical output is approximately

\[
W_{0\rightarrow1} \approx X_0 + O_1 - G.
\]

If L1 is not drained, `O_1` grows. Each future L0 compaction therefore rewrites
more L1 data and occupies the only effective compaction worker longer. That in
turn reduces service available to drain L1, causing `O_1` to grow further.

The logs show exactly this feedback:

All figures below are the `L1` row of the final `Compaction Stats [default]`
table, i.e. compactions whose output level is L1:

| L0-to-L1 property | Regular | RL | Ratio |
| --- | ---: | ---: | ---: |
| Physical rewrites, `Comp(cnt)` | 26 | 20 | 0.77x |
| Total read, `Read(GB)` | 1.1 | 3.3 | 3.0x |
| Overlap read from L1, `Rnp1(GB)` | 0.7 | 3.0 | 4.3x |
| Mean input/compaction | 43.3 MB | 169.0 MB | 3.9x |
| Mean duration, `Avg(sec)` | 302 ms | 1,119 ms | 3.7x |
| Level write amplification, `W-Amp` | 2.8 | 8.5 | 3.0x |

The RL compactions were about four times larger and 3.7 times longer. Fewer
compactions did not mean less work; delayed compactions consolidated into much
more expensive rewrites. The `Rnp1` row isolates the mechanism: the extra bytes
are overlap re-read from an L1 that was never drained, and the single-level
`W-Amp` of 8.5 accounts for essentially the whole 2.19x whole-tree WAF
regression.

Two corroborating distribution statistics from the same run:

| Statistic | Regular | RL |
| --- | ---: | ---: |
| `numfiles.in.singlecompaction` p95 | 4.9 | 198 |
| `numfiles.in.singlecompaction` p100 | 128 | 550 |
| `compaction.times.micros` p50 | 20.9 ms | 1,030 ms |

### 3.6 The deferral counter can erase unresolved debt

Current control flow resets `defer_count[level]` when an agent requests
`compact`, before global arbitration. If another level wins, the first level is
marked overridden but its counter remains reset.

The reset is at `compaction_picker_rl.cc:440-442`:

```cpp
} else if (!level.default_needed || compact) {
  defer_count_[source_level].store(0, std::memory_order_relaxed);
}
```

Arbitration runs afterwards at `:456-481`, and the losing branch at `:476-481`
writes only `last_effective_action_` and `last_action_overridden_`. The counter
stays at zero.

Example:

```text
L0: emergency compact
L1: policy compact, score 8.0
```

The code resets L1's deferral count because L1 requested `compact`. L0 then wins
because emergency has a higher reason priority. L1 schedules nothing, but its
budget returns to zero. Repeating this sequence allows L1 to remain due
indefinitely without exhausting its deferral bound.

**Scope of the defect.** The reset applies only to *policy* compacts. The
budget-forced path at `:435-439` sets `compact = true` without touching
`defer_count_`, so a budget-forced level that loses arbitration retains its
exhausted count and re-forces on the next decision. That path is currently
correct and must not be "fixed" alongside the policy path. The repair replaces
the counter entirely (section 7.1), but any interim patch must preserve the
distinction.

Even without that reset bug, 50 nominal steps is not 2.5 seconds unless a
usable decision occurs every 50 ms. At the observed 1.95 decisions/s,

\[
\frac{50}{1.95} \approx 25.6\ \text{seconds}.
\]

Decision-count safety therefore has workload- and scheduler-dependent wall
time, exactly where a safety bound must not.

### 3.7 This is not an RL file-picker failure

The current C++ path forces only the source level and then calls native
`PickCompactionFromLevel`. The large `files_L1` arrays in the log are the
native clean-cut and overlap result after L1 was allowed to grow. No file
identity came from Python.

The server stayed available, protocol v2 was active, cumulative socket
fallbacks were zero, and there were no level-trigger scheduling-failure logs.
The failure is therefore in trigger semantics and service admission, not
candidate staleness or exact-file selection.

### 3.8 The sensing path is closed by the quantity it controls

Pulse semantics explain the service-rate cap. They do not explain why the
decision rate itself was 1.95/s against a configured 50 ms interval, which
should have produced roughly 20/s. That has a separate cause, and it must be
repaired in the same change.

The final diagnostic line reads:

```text
RL trigger diagnostics: protocol=2 queries=75 actuations=75 bypasses=1
skipped_ticks=694 cumulative_fallbacks=0 available=1 nc_calls=440
nc_total_ms=8 publish_calls=99 publish_total_ms=7
```

Over 38.4 seconds at a 50 ms observe interval the worker ticked
`75 + 694 = 769` times, which is the expected count. It obtained a usable
observation on 75 of them. **The remaining 694 ticks, 90 percent of the control
loop, ran blind** and were discarded at `compaction_picker_rl.cc:503-506`:

```cpp
if (!snapshot_valid_) {
  rl_skipped_ticks_.fetch_add(1, std::memory_order_relaxed);
  continue;
}
```

The reason is structural. `PublishSnapshot` has exactly one call site, inside
`NeedsCompaction` at `:609`, and is additionally rate limited to one
publication per `observe_interval_ms / 4` = 12.5 ms at `:182-188`.
`NeedsCompaction` is invoked from RocksDB's scheduling paths rather than on any
timer. The single call site of `PublishSnapshot` is verified; the complete set
of `NeedsCompaction` callers was not exhaustively enumerated for this document,
so the defensible statement is the narrower one: **snapshot supply is
scheduler-driven, was empirically sparse in this run, and can cease entirely
once the column family is dequeued.** The measured chain was 440 scheduler
calls producing 99 publications, or **2.6 observations/s against a nominal
20/s**.

The loop closes as follows:

```text
policy defers every level
  -> no lease installed
  -> NeedsCompaction() returns HasActiveLease() == false   (:638)
  -> RocksDB dequeues the column family and stops polling
  -> no NeedsCompaction call, therefore no PublishSnapshot
  -> snapshot_valid_ stays false
  -> worker skips its tick and the agent receives no observation
  -> the deferral cannot be revised on evidence
```

The controller's sensing is wired to its own actuation. Deferring blinds it,
and being blind keeps it deferring. This is a distinct defect from the
pulse/gate error and compounds it: the events able to generate an observation
were essentially the 209 flushes, 25 compactions, and 233 trivial-move jobs of
this run, so the observation rate was pinned to plant activity that the
controller was simultaneously suppressing.

Held gates reduce sensitivity to this, because an open gate lets RocksDB
self-schedule without asking. They do not remove it, because the closed-gate
case is exactly the case that produces no observations. Section 7.8 specifies
the required decoupling.

## 4. Correct control-theoretic interpretation

### 4.1 Zero-order hold, not an impulse

An online controller samples the plant at times `tau_k`. Standard sampled-data
semantics hold its chosen control until the next decision:

\[
u_i(t) = a_i(k),
\quad t \in [\tau_k, \tau_{k+1}).
\]

This is a zero-order hold. RocksDB may take multiple internal scheduling steps
during that interval. The current bridge instead behaves like an impulse:

\[
u_i(t) \approx \delta(t-\tau_k),
\]

with one compaction token at the decision instant. That converts model
inference rate into a hard cap on storage service rate.

The repair will implement zero-order-hold semantics with bounded staleness and
explicit safety overrides.

### 4.2 Why due and optional compactions need different semantics

Suppose a level has `s_i >= 1`. RocksDB already considers it due. A `compact`
action means "admit native service until the current due episode is cleared."
It should not mean "select one SST" or "perform one file compaction."

Suppose instead `s_i = 0.75`. A proactive `compact` action is optional work. If
that action opened an unlimited drain gate, RocksDB could repeatedly compact a
healthy level toward empty, producing severe over-compaction. Therefore:

\[
\text{compact semantics} =
\begin{cases}
\text{due catch-up gate}, & s_i \ge 1,\\
\text{one optional token}, & s_{min} \le s_i < 1,\\
\text{masked}, & s_i < s_{min}.
\end{cases}
\]

`s_min` remains the existing minimum compact score used by the policy mask.

## 5. Proposed per-level state machine

Each level will have a policy permit record, not a file/candidate lease.

### 5.1 Permit record

Conceptually:

```cpp
struct LevelPermit {
  uint64_t decision_id;
  uint64_t decision_generation;     // response-frame attribution
  uint64_t eligibility_generation;  // changes only when effective gate interval changes
  uint64_t installed_micros;
  PolicyAction action;          // defer or compact
  PermitMode mode;              // closed, due-catch-up, optional-one-shot
  ActionReason reason;
  bool optional_token_available;
  bool superseded;
};
```

The record deliberately contains no file number. A whole response frame should
be installed atomically so level 0 cannot observe decision `k+1` while level 1
still observes decision `k`.

The two generations are intentionally different. `decision_generation`
advances for every accepted response and is copied to jobs for attribution.
`eligibility_generation` advances only when the level's effective eligibility
interval changes—for example closed-to-open, open-to-closed, or a consumed
optional token being newly granted. The transition may be caused by an action,
current-score crossing, safety/fallback state, or token consumption. Backoff does
not close the underlying permit and therefore does not change this generation;
its attempts are distinguished by `retry_generation`. Repeating `compact` while
the same due gate remains open updates attribution but does not create a new
eligibility edge, duplicate wake, or invalidate a blocked retry timer.

`mode` records the initial interpretation for telemetry; it is not allowed to
freeze an observation-time score classification. At admission, a held compact
action is interpreted against the **current** score. If the current score is at
least 1, it is due catch-up even if the observation was below 1. If the current
score is below 1, it may execute only when the original response was granted an
optional token and that token remains valid and unconsumed. A response that was
due-only does not become permission for optional work merely because another
job made the level healthy before admission.

### 5.2 States

| State | Entry condition | Native scheduling permission | Exit condition |
| --- | --- | --- | --- |
| `HEALTHY_CLOSED` | `s_i < 1`, no optional compact | none | next decision or level becomes due |
| `OPTIONAL_OPEN` | compact chosen while `s_min <= s_i < 1` | one successful native compaction | successful schedule, superseding decision, or watchdog |
| `DUE_DEFERRED` | `s_i >= 1` and policy says defer | none unless safety overrides | compact decision, safety override, or `s_i < 1` |
| `DUE_OPEN` | `s_i >= 1` and policy says compact | repeated native compactions while current score is due | `s_i < 1`, new defer decision, or watchdog fallback |
| `FORCED_OPEN` | budget, emergency, or fallback | native due work, repeated as required | safety clears and level becomes healthy |
| `BLOCKED_OPEN` | an open level has native conflicts | no successful pick now; permission remains | conflict clears, new decision, safety change, or level becomes healthy |

`BLOCKED_OPEN` is a diagnostic condition, not permission for a different
deferred level.

**Bounding `BLOCKED_OPEN`.** Because `NeedsCompaction()` will report true while
an open level has due work, and `PickCompaction()` may then return null on a
conflict, this pair can be re-entered every time RocksDB drains
`unscheduled_compactions_`. The state must therefore be rate limited or it will
occupy background job slots without progress:

1. count consecutive blocked attempts per level;
2. after `K_blocked` consecutive failures, suppress that level's contribution
   to `NeedsCompaction()` until a monotonic deadline `backoff_until`, without
   closing its permit and without transferring authority to any other level;
3. reset the counter on a successful schedule from that level, on a relevant
   version/conflict change, or after the gate genuinely closes and later
   reopens. Do **not** reset it merely because a new decision frame repeats the
   same open action; otherwise a 50 ms policy stream can prevent a permanently
   blocked level from ever reaching `K_blocked`;
4. register a timed retry with the DB-owned control coordinator. When the
   deadline expires, the coordinator issues a new scheduling request if the
   permit is still open and current native work is still eligible. This retry
   has its own monotonically increasing `retry_generation`; it is not suppressed
   merely because the eligibility generation has already produced an earlier
   wake.
   Expiry permits one new native probe; another blocked result starts a new
   monotonic window rather than a scheduler-event loop;
5. export consecutive-blocked and suppressed-window counts in the section 10.6
   telemetry, because a level that is permanently blocked is indistinguishable
   from a level that is being starved unless the trace separates them.

Proposed bootstrap values are `K_blocked = 4` and `W_blocked = 100 ms`, expressed
as monotonic time rather than scheduling events. These are backoff parameters,
not safety parameters: wall-clock and pressure budgets continue to accrue, and
expiry itself is a wake source even on an otherwise idle plant. A relevant
version/conflict change may cancel the timer and request an earlier retry.

### 5.3 Permit lifetime

A policy frame remains current until one of these events:

1. the next valid policy response atomically supersedes it;
2. a response-staleness watchdog expires;
3. shutdown/final drain begins.

When the watchdog expires, the system must enter explicit native-leveled
fallback for due work. It must not keep a stale defer forever and must not keep
optional proactive permission forever.

Proposed bootstrap watchdog:

\[
H_{stale} = \max(1\ \text{second}, 20\Delta_{decision}),
\]

where `Delta_decision` is the configured nominal decision interval.

**This formula must not be adopted as written, and the watchdog must not be
implemented before section 7.8.** Two problems:

1. *It is calibrated against the nominal interval, not the achieved one.* With
   `Delta_decision = 50 ms` the expression yields 1 second, but the measured
   response spacing in the evidence set was `38.4 / 75 = 512 ms`. One second is
   therefore under two achieved intervals of margin, and the watchdog would
   fire routinely against a perfectly healthy server. The bound must be derived
   from measured spacing, for example
   `H_stale = max(1 s, 8 x median observed response spacing)`, recomputed from
   a live estimate rather than from configuration.

2. *Combined with the section 3.8 sensing loop, it makes sustained deferral
   structurally impossible.* If deferring every level suppresses observations,
   then no new response can arrive, so `H_stale` necessarily expires, so the
   controller enters native leveled fallback. A learned deferral policy would
   be switched off precisely for exercising deferral, and the fallback would be
   misread as server unavailability. This is why section 7.8 is a prerequisite:
   once observation supply is independent of gate state, watchdog expiry again
   means what it is supposed to mean, namely that the policy has genuinely
   stopped responding.

Expiration must fall back safely rather than silently close all work, and every
expiry must be counted and attributed in telemetry so that a run with a healthy
server and a nonzero watchdog count is treated as a defect rather than as
normal operation.

### 5.4 Scheduling eligibility

At a current tree version, define:

\[
E_i(t) = E_i^{policy}(t) \lor E_i^{forced}(t).
\]

Policy eligibility is:

\[
E_i^{policy}(t) =
\begin{cases}
1, & mode_i = DUE\_OPEN \land s_i(t) \ge 1,\\
1, & mode_i = OPTIONAL\_OPEN \land token_i = 1,\\
0, & \text{otherwise}.
\end{cases}
\]

Forced eligibility is true when an explicit safety/fallback rule has opened the
level and native due work remains.

`NeedsCompaction()` returns true if any authorized level has work, or if an
explicit maintenance/drain bypass is active.

### 5.5 Successful scheduling semantics

On a successful native pick from level `i`:

- attach decision ID/decision generation/reason attribution, plus eligibility
  generation for control diagnostics, to the `Compaction`;
- increment the decision-level scheduled job count;
- if mode is `OPTIONAL_OPEN`, consume its one token;
- if mode is `DUE_OPEN` or `FORCED_OPEN`, retain the gate so another native
  scheduling call can occur while `s_i >= 1`;
- recompute and use the current RocksDB score on the next call.

Do not reset due age merely because one compaction was scheduled. The due
episode ends only when the current score falls below 1.

### 5.6 Failed scheduling semantics

If the native allowed-level builder, or the optional forced-level path, returns
null because of current conflicts:

- record a blocked attempt;
- do not consume an optional token;
- do not clear a due permit;
- do not authorize a deferred level;
- continue to another level only if that other level independently belongs to
  the eligible set.

A tree change does not make a level permit "stale" in the former candidate
sense. Because there is no file identity, RocksDB simply evaluates the current
files on the next attempt.

## 6. Level arbitration

### 6.1 Why reason-first arbitration is wrong

The current implementation gives emergency L0 work priority over policy work
at deeper levels. In the observed state:

```text
L0 score = 3.8
L1 score = 13.7
L2 score = 2.1
```

Choosing L0 first rewrites a huge L1. Draining L1 first may reduce the overlap
and cost of the next L0 compaction. Repeated emergency-first selection therefore
creates the positive feedback described earlier.

Safety should decide **which gates must be open**. It should not blindly replace
RocksDB's score-based ordering among all open levels.

### 6.2 Native-score ordering among authorized levels

Let the currently eligible level set be

\[
\mathcal{E}(t) = \{i : E_i(t)=1\}.
\]

Use the current `VersionStorageInfo` compaction-score ranking, restricted to
that set. As a first-order description:

\[
i^*(t) = \arg\max_{i \in \mathcal{E}(t)} s_i(t).
\]

The actual implementation should not reproduce that loop in
`RLCompactionPicker`. It should add an allowed-level mask/predicate to the
native `LevelCompactionBuilder::SetupInitialFiles` path and let that path
iterate RocksDB's already sorted `CompactionScoreLevel(rank)` list. This is
important because native code has additional rules, including avoiding a
base-level compaction after a blocked L0-to-base pick so the latter is not
starved, and attempting legal intra-L0 work in specific cases. The mask changes
only whether a source level is eligible; it does not replace those rules.

If a native rule advances to another source level, that level must also be in
the allowed mask. A closed level is always skipped.

### 6.3 Hard-stop exception

An actual L0 stop condition may require immediate L0 progress. Even then, the
controller must respect RocksDB conflicts and compactions already in progress.
The hard-stop rule may place L0 first only when a valid L0 pick exists. It must
not repeatedly clear deeper-level accounting or close their permits.

The distinction is:

- **open due to emergency:** eligibility decision;
- **must be selected before every deeper level forever:** generally false.

### 6.4 Example

Assume:

```text
L0: score 3.8, forced open
L1: score 13.7, policy open
L2: score 2.1, policy deferred
```

Eligible set is `{L0,L1}`. Native-score restriction chooses L1. RocksDB selects
the L1 SST(s), possibly performs a trivial move to L2, recomputes scores, and
the scheduler may choose L1 again. L2 remains unauthorized despite being due.
After L1 falls below L0, native ordering selects L0. At no point does RL choose
an SST.

## 7. Correct bounded-deferral mathematics

### 7.1 Replace decision counts with wall-clock state

For each level, track a due episode:

\[
t_i^{due} = \inf\{t : s_i(t) \ge 1\}.
\]

Current due age is

\[
A_i(t) =
\begin{cases}
t-t_i^{due}, & s_i(t) \ge 1,\\
0, & s_i(t) < 1.
\end{cases}
\]

This age is reset only when the level becomes healthy. A compact request that
loses arbitration does not reset it. One scheduled compaction that leaves the
level due does not reset it.

### 7.2 Integrate excess pressure

Wall time alone treats score 1.01 and score 13.7 equally. Track integrated
excess:

\[
P_i(t) = \int_{t_i^{due}}^t \max(0, s_i(u)-1)\,du.
\]

Units are score-seconds. One second at score 1.1 adds 0.1; one second at score
13.7 adds 12.7. This forces much faster intervention under severe debt.

For L0, use file pressure relative to the ordinary trigger `C_0`:

\[
s_0(t) = \frac{N_0(t)}{C_0},
\]

and integrate `max(0, s_0-1)` the same way. Slowdown and stop thresholds remain
additional categorical safety conditions.

### 7.3 Forced-open condition

For each level, define calibrated limits:

- `H_i`: maximum due age;
- `J_i`: maximum integrated excess pressure;
- `S_i`: hard instantaneous score cap.

Policy deferral is overridden when

\[
A_i(t) \ge H_i
\lor P_i(t) \ge J_i
\lor s_i(t) \ge S_i.
\]

This makes the bound invariant to policy query frequency. It does **not**, by
itself, make the bound invariant to scheduler-observation frequency; see the
sampling requirement below.

**Integrate with a zero-order hold, not a trapezoid.** RocksDB compaction
scores are piecewise constant: they change discretely when the active
`VersionStorageInfo` score is recomputed after a tree or option transition, and
hold their value between those events. Those transitions include, but are not
limited to, flush, compaction registration/completion, ingestion, recovery,
manual operations, and dynamic option changes.
Trapezoidal integration assumes the score ramped linearly between samples and
therefore misattributes every step. If `s_i` jumps from 1 to 13 at `t_m`, the
trapezoid charges roughly six score-seconds across the preceding interval
during which the score was actually near 1; a 13-to-1 transition makes the
symmetric error in the other direction. Both corrupt exactly the quantity the
forced-open bound depends on.

For stepwise state the correct update holds the previous value across the
interval:

\[
P_i(t_m) = P_i(t_{m-1}) + (s_i(t_{m-1})-1)^+ \,(t_m - t_{m-1}),
\]

where `(x)^+ = max(0,x)`. When a structural update lands between worker ticks,
split the interval at the change timestamp rather than attributing the whole
interval to either value:

\[
\Delta P_i =
(s_{old}-1)^+ \,(t_{change}-t_{last})
+ (s_{new}-1)^+ \,(t_{tick}-t_{change}).
\]

**Use one shared post-score-recomputation observer.** Structural snapshot
coalescing must not determine pressure accuracy. A generic
`CompactionPressureObserver`, attached for both regular and RL pickers, observes
every accepted recomputation of the active `VersionStorageInfo` under the DB
mutex. Before replacing `s_old` with `s_new`, it integrates `s_old` up to the
event's monotonic timestamp, then records `s_new`. It also records the exact
below-1 to due and due to below-1 transitions used for `A_i` and baseline episode
distributions.

Production code must expose a single audited seam such as
`RecomputeActiveCompactionScoreAndObserve(cfd, vstorage, now)`. All direct
production calls that can change the active score must migrate to it; direct
`ComputeCompactionScore()` calls remain permissible only for isolated temporary
objects and tests that cannot change active scheduling state. The call-site
audit must cover version append/recovery, flush, registration, completion,
ingestion, manual compaction state changes, `SetOptions`, unregister/error
rollback, and any picker-side recomputation. A test-only sequence number detects
an active score change that was not observed.

The observer publishes a lightweight immutable clock state per level:

```text
score_at_event, event_micros, due_since_micros, pressure_at_event,
score_event_generation
```

The writer-side observer is per column family and is owned with its active
version state. RL workers receive only a refcounted immutable publication, never
the observer or Cfd itself; regular leveled runs use the same writer-side
observer for episode telemetry without starting an RL worker. CF registration
invalidation detaches further publications before the worker is stopped.

The worker does not numerically reintegrate cached structural samples. At time
`t` it derives

\[
P_i(t)=P_{i,event}+(s_{i,event}-1)^+(t-t_{i,event})
\]

and `A_i(t)=t-t_i^{due}` when a due episode is active. The next score event
commits the same extension before changing the held score. This makes pressure
exact even while a structural snapshot rebuild is coalesced.

Use a monotonic clock so NTP/wall-clock adjustments cannot make age negative.

**Sampling requirement.** This update must not be driven solely by
`NeedsCompaction()`. An earlier draft placed it in "the synchronous tree-state
path" on the argument that if no policy response arrives for a second, the next
scheduler observation still charges that second of pressure. That argument
covers a missing *policy response*; it does not cover a missing *scheduler
observation*, and section 3.8 shows the latter is the binding constraint. If
every level is deferred, `NeedsCompaction()` returns false, RocksDB stops
calling it, and both `A_i` and `P_i` freeze. The forced-open condition
`A_i >= H_i` would then never fire, because the clock that was supposed to
guarantee it stopped for the same reason the guarantee was needed.

That failure is the same defect class this section exists to remove: a safety
bound denominated in events whose wall-clock spacing is workload dependent. It
would simply have moved from decision counts down to scheduler-observation
counts.

Therefore score transitions must be captured by the event-driven observer, and
deadline evaluation must run from a source that ticks independently of plant
activity, per section 7.8. The zero-order-hold accumulator remains exact between
events because the worker extends the last held score to its current monotonic
time. A synchronous scheduler observation remains authoritative for admission,
but it is a validation point rather than the sole clock source.

This concern is not hypothetical for the target workload. The 1M/T2 mix is
about 52 percent point reads, 32 percent scans, and 15 percent writes. During a
read-dominated stretch, flushes and compactions are infrequent, so scheduler
observations are rarest exactly when a starved level's read amplification is
most damaging.

### 7.4 Calibration logic

The final limits should come from the preregistered tuned leveled baseline, not
from RL outcomes. For every level, collect the baseline distributions of:

- due-episode duration;
- maximum score within a due episode;
- integrated excess pressure;
- pending compaction bytes normalized by live bytes.

A defensible initial envelope is:

\[
H_i = 1.02\,Q_{99}(A_i^{baseline}),
\]

\[
J_i = 1.02\,Q_{99}(P_i^{baseline}),
\]

\[
S_i = \max(1.10, 1.02\,Q_{99}(s_i^{baseline})).
\]

The `1.02` factor is an engineering margin on the measured baseline envelope.
It is deliberately numerically equal to the project's 2% tolerance but is not
that tolerance and does not inherit its guarantee; see the note at the end of
this subsection. The `1.10` floor avoids triggering on floating-point noise
around 1.
If baseline data is temporarily unavailable during bridge development, use a
conservative bootstrap cap such as `S_i=1.25` for L1+ and no L0 deferral, label
it explicitly as a test configuration, and do not present it as the final
policy.

**Minimum sample count.** A 99th percentile is not estimable from a handful of
episodes, and deep levels are exactly where episodes are scarce. In the
evidence set the regular arm recorded `Comp(cnt)` of 2 for L3 and 2 for L4 over
the whole run; a `Q99` computed from two samples is the maximum, and
`1.02 x max` is an envelope with no headroom and no statistical meaning.

An earlier draft proposed `N_min = 30`. That is far too small. For `N`
independent episodes the probability of observing even one sample beyond the
true 99th percentile is `1 - 0.99^N`, so

\[
N = 30 \;\Rightarrow\; 1 - 0.99^{30} = 0.2603.
\]

Three quarters of the time, a 30-episode sample contains nothing from the tail
the envelope is supposed to bound. Merely reaching a 95 percent chance of one
tail observation requires

\[
N \ge \left\lceil \frac{\log 0.05}{\log 0.99} \right\rceil = 299,
\]

and seeing one tail sample is not the same as estimating the quantile with
useful precision. Adopt one of:

1. collect several hundred due episodes per level and use a distribution-free
   order-statistic tolerance bound, reporting the coverage and confidence
   actually achieved rather than a bare `Q99`;
2. calibrate a lower quantile, such as `Q90`, with a documented multiplier,
   until enough episodes exist to move up;
3. leave the level on the conservative bootstrap cap.

Whichever is used, record per level the episode count, the quantile actually
estimated, and the resulting bound, so a later result cannot silently claim a
`Q99`-derived envelope it never had.

**The `1.02` factor is an engineering margin, not the project's 2 percent
tolerance.** The project's 2 percent limit applies to observed space and
latency regressions against the baseline. Multiplying an internal compaction
score or a due-age quantile by 1.02 does not bound any externally observable
regression, because the map from score headroom to latency and space outcomes
is neither linear nor known. Name it a margin, and validate the resulting
envelope against SLO outcomes separately rather than assuming the 2 percent
figure carries across.

Because episode counts scale with workload size, expect L1 and L2 to calibrate
first while L3 and below require the larger runs. That is an acceptable staged
outcome provided the un-calibrated levels remain on the conservative bootstrap
cap rather than on an extrapolated one.

### 7.5 Global pending-debt guard

Raw pending bytes do not scale across 1M and 50M workloads. Normalize:

\[
R_{debt}(t) =
\frac{B_{pending}(t)}{\max(B_{live}(t),1)}.
\]

If this ratio exceeds its baseline envelope, open all currently due levels and
let native score ordering arbitrate. The current fixed 10 GiB emergency cap is
too large to protect a 1M workload whose entire live database is only a few
hundred MiB.

### 7.6 Hysteresis

Safety should enter on a high threshold and leave on a lower threshold:

\[
\text{enter forced mode if } s_i \ge S_i^{high},
\]

\[
\text{leave forced mode if } s_i \le S_i^{low},
\quad S_i^{low} < S_i^{high}.
\]

For example, enter at 1.25 and leave only after score falls below 1.0. This
prevents gate oscillation around a single boundary.

### 7.7 L0 rules

L0 needs separate explicit behavior because file count, not bytes, controls its
primary trigger:

1. below compaction trigger: optional one-shot compact may be allowed;
2. from compaction trigger to slowdown: bounded policy deferral may be tested
   only after native parity works;
3. at slowdown: force L0 eligible and open all due supporting levels so native
   score ordering can reduce downstream obstruction;
4. at stop: make immediate progress using native legality checks;
5. never reset L0 due age because a request lost arbitration.

The safest first repair keeps L0 non-deferring, matching regular leveled
behavior on that axis while validating deeper-level control.

### 7.8 Decoupling observation and safety advance from `NeedsCompaction()`

This subsection specifies change 4 from section 1. It repairs the defect
documented in section 3.8 and supplies the sampling guarantee required by
section 7.3.

#### 7.8.1 Requirements

**R1. Observation liveness.** A snapshot must become available to the worker at
a bounded rate that does not depend on the gate state, on whether any level is
eligible, or on whether flushes and compactions are currently occurring. If
every level is deferred and the plant is idle, the agent must still receive
observations and must still be able to revise its own deferral.

**R2. Safety-clock liveness.** `A_i(t)` and `P_i(t)` must advance in real time
for every due level, including levels that are deferred, blocked, or in a
backoff window, and including intervals in which RocksDB schedules nothing at
all. The forced-open condition of section 7.3 must be reachable within
`H_i + epsilon` of wall time under any plant behavior.

`epsilon` is not a fudge factor and must be defined and measured, or
`H_i + epsilon` is not a testable guarantee. It is the sum of the delays
between the deadline being crossed and work actually being admitted:

\[
\epsilon =
T_{score\ event\ publication}
+ T_{worker\ tick}
+ T_{control\ queue}
+ T_{scheduler\ admission}.
\]

`T_score event publication` is the bounded delay inside the centralized score
observer; structural-snapshot coalescing is deliberately absent because the
safety clock does not depend on a rebuilt structural snapshot. `T_control queue`
includes timer dispatch and DB-mutex acquisition. Export each term and report
the p95 and maximum of the total. The acceptance criterion is stated against
the measured distribution, not against a nominal value.

**R3. Actuation liveness.** A permit is not an action. Changing eligibility
does not enqueue work, and RocksDB will not discover the change on its own if
the column family was already dequeued. When **any** gate transitions from
closed to eligible while the plant is idle, or a blocked-open timed retry
expires, the controller must actively cause a scheduling attempt. The
acceptance test is a scheduling *attempt*, not a changed permit bit.

**R4. No new lock inversion.** Satisfying R1--R3 must not introduce a path that
waits on socket I/O or inference while holding the DB mutex, nor a path that
acquires the DB mutex from a thread already holding `permit_mu_` or `snap_mu_`.
Section 10.7's ordering rules remain binding and are extended to cover
`snap_mu_`, which the current design also takes.

**R5. Bounded cost when idle.** The mechanism must not busy-poll the tree and
must not rebuild structural state every tick. Its steady-state cost with an
idle database must be comparable to the existing worker tick, which measured
`nc_total_ms=8` and `publish_total_ms=7` across the entire evidence run.
`BuildSnapshot()` iterates every level, calls `NumLevelFiles`, and computes
whole-level overlap through `NextLevelOverlapBytes`; running that under the DB
mutex on a 50 ms period is not obviously bounded at 50M/T2 and must not be the
steady-state path.

**R6. Lifetime safety.** The worker thread must not outlive the
`ColumnFamilyData` or `DBImpl` it references, and must not dereference either
during or after column-family drop, ordinary final destruction, or DB shutdown.
This requirement is per column family; a DB-global shutdown flag alone is not
sufficient.

**R7. Deferred-refresh liveness.** A rate-limited structural change must have a
DB-owned executor and timer. The picker worker cannot perform a later rebuild,
because it intentionally owns no `DBImpl` or live `ColumnFamilyData` reference.
`built_generation` must converge to `source_generation` after the limiter gap
even if no further database event occurs.

#### 7.8.2 Mechanism

Three changes. The third is the one an earlier draft of this section omitted,
and without it the other two do not produce a working safety guarantee.

**(a) Cache an immutable structural snapshot; do not rebuild it per tick.**
Publication today is synchronous inside `NeedsCompaction()` and builds a fresh
`RLStateV2` each time (`compaction_picker_rl.cc:609` calling `BuildSnapshot`).
Replace the steady-state path with a cached, immutable, refcounted structural
snapshot:

1. the snapshot is rebuilt only when the structure it describes changes, that
   is on superversion install and on compaction registration/completion, at
   which points the building thread already holds the DB mutex and the level
   metadata is already resident;
2. the worker holds a shared pointer to the most recent snapshot and *reads*
   it every tick. It does not consume it, does not clear `snapshot_valid_`, and
   does not rebuild it. Repeated ticks against an unchanged tree are pointer
   reads;
3. each snapshot carries its build timestamp, so the worker can report
   `structural_snapshot_age_micros`; source/built generations and the dirty
   timestamp separately identify a stale view, while response age identifies a
   stale policy.

**The rebuild limiter must coalesce, never discard.** The existing limiter at
`:182-188` returns early and drops the update. Under a cached-snapshot design
that is a permanent loss:

```text
t=0 ms   snapshot A published
t=5 ms   structural change B occurs, limiter suppresses the rebuild
then     the database goes idle
         -> no further structural events
         -> B is never published
         -> the worker uses A forever
```

Observation liveness fails silently. In the earlier sample-driven clock design,
safety liveness failed too because the worker integrated a score that no longer
existed. Revision 5's pressure observer removes that second dependency, but a
known-stale structural view must still be handled conservatively. Required
instead:

1. every structural change increments a **source generation** and marks the
   cache dirty; it never simply returns;
2. if a rebuild is suppressed by the minimum gap, exactly one **deferred
   rebuild** is registered with the DB-owned control coordinator for
   `last_build_micros + min_rebuild_gap`;
3. the cache records both `source_generation` and `built_generation`;
4. while those differ the view is by definition stale. It may still be emitted
   for observation liveness and diagnostics, but a response derived from a
   structural generation superseded before installation is discarded in full
   and marked invalid for replay. Preserve the last valid held frame and exact
   safety clocks during the bounded coalescing interval. If no valid frame
   exists, or `dirty_age` exceeds its configured deadline, enter tuned native
   due eligibility and disable optional work.

The deferred-refresh request is keyed by `(cf_id, registration_generation)` and
stores the newest requested `source_generation`. Additional changes inside the
gap update that one pending request rather than enqueueing unbounded work. At
the deadline the coordinator:

1. removes the request from its timer queue without holding the queue lock while
   acquiring the DB mutex;
2. acquires the DB mutex and resolves the registration token to a current,
   non-dropped `ColumnFamilyData`;
3. compares the requested, current source, and built generations;
4. rebuilds from the **latest** active `VersionStorageInfo`, not from state
   captured when the request was queued, and publishes `built_generation` equal
   to the source generation observed under the mutex;
5. drops the request if the registration is gone, and re-arms it if a newer
   source generation remains dirty.

An immediate rebuild cancels or makes obsolete any queued request for an older
generation. Queue delay, rebuild latency, coalesced-change count, and requests
dropped because a CF registration disappeared are exported. This is the
concrete mechanism behind the word "guaranteed"; no picker-side pull is relied
upon.

**The cache cannot be an `RLStateV2`.** `BuildSnapshot` mixes structural fields
with mutable execution state in one object
(`compaction_picker_rl.cc:118` onward): `defer_count`, `prev_action_executed`,
`prev_action_overridden`, `prev_compaction_picked`, `prev_decision_id`,
`prev_snapshot_epoch`, `prev_scheduling_result`, `prev_override_reason`, and
`prev_transition_valid` are all per-decision outcomes. Caching that object
wholesale would freeze the execution outcomes and feed the agent stale
attribution, corrupting training rather than merely delaying it. Split it:

```text
RLStructuralSnapshot   (cached, immutable, refcounted, generation-stamped)
  levels, files, bytes, targets, overlaps, tree generation

observation overlay    (built per emitted observation, never cached)
  current scores and exact due/pressure clock state from the shared observer,
  previous actions/results, defer counts, telemetry deltas, read deltas,
  fallback state, interval time, observation timestamp
```

An emitted observation is the current overlay applied over the newest
structural snapshot. Current compaction scores are deliberately in the overlay:
registration and option changes can recompute them more often than the
structural limiter publishes, and caching them would recreate the stale-clock
bug through the model input even though the safety accumulator was exact.

**Three distinct ages, which must not be conflated:**

- `observation_age` — near zero for a freshly emitted observation;
- `structural_snapshot_age` — may legitimately be large, because an unchanged
  idle tree needs no rebuild. A large value here is not an anomaly;
- `dirty_age` — how long a *known unpublished* structural change has waited.
  This is the one that must be bounded, and it is the correct Phase 1a gate.

Define the live deadline explicitly:

\[
D_{dirty}=T_{min\ rebuild\ gap}+T_{refresh\ queue,max}+T_{rebuild,max}.
\]

The queue and rebuild terms are measured under the Phase 1a stress fixture and
stored with the configuration; using only the nominal limiter gap is not a
bound. Crossing `D_dirty` records a deadline miss and activates the conservative
fallback below, even though the queued rebuild is still allowed to finish.

An unchanged idle tree therefore never needs a pull. If `dirty_age` exceeds its
bound, the controller enters conservative native eligibility and records a
coordinator deadline miss; the coordinator's already-queued refresh remains the
only rebuild path. The picker does not attempt a "rare pull": it has no safe
live object with which to take the DB mutex, and adding one would violate the
lifetime boundary this design establishes.

This distinction is essential: `source_generation != built_generation` during
an ordinary minimum-gap interval is expected and must not force native fallback
on every structural burst. A **deadline miss**, not mere bounded dirtiness,
causes fallback. Observations carry the structural generation; the C++ client
stamps the returned action frame with the generation of its matching request, so
the admission path can reject superseded advice without adding a file or
generation field to the v2 response payload.

Retain the synchronous read inside `NeedsCompaction()` for admission decisions,
since that path sees exact current `VersionStorageInfo`.

The `skipped_ticks` path at `:503-506` disappears in this design: with a cached
snapshot there is always something to read. Any residual increment becomes a
genuine anomaly counter.

**(b) Evaluate the event-driven safety clocks on the worker's monotonic tick.**
Section 10.3 previously placed due/pressure advance inside `NeedsCompaction()`,
which is the wrong thread for a wall-clock guarantee because it runs only when
the plant is active. The worker already ticks on an independent 50 ms timer.
Per tick it reads the latest lightweight `CompactionPressureObserver` state,
extends its zero-order-held score from `event_micros` to the tick time using the
formula in section 7.3, and evaluates the forced-open conditions. It does not
integrate from structural-snapshot samples and does not write a second competing
pressure accumulator. This is arithmetic over a handful of per-level scalars
and satisfies R5 even while a structural rebuild is coalesced.

`NeedsCompaction()` continues to re-evaluate safety synchronously against
current `VersionStorageInfo` before admitting work. Where the cached snapshot
and the synchronous state disagree, the synchronous state wins for admission,
and the disagreement is recorded as snapshot staleness. The worker's role is to
*guarantee* the deadline is reached; the synchronous path's role is to
*validate* before acting on it.

**(c) Explicitly wake the scheduler whenever a level becomes newly eligible.**
This is requirement R3, and it is the step that makes (a) and (b) mean anything.

Installing a permit changes eligibility. It does not enqueue a column family
and does not start a background job. If the column family was dequeued because
`NeedsCompaction()` previously returned false, and the plant is idle, then
there is no "next scheduler event" at which the new permit will be noticed. The
level would stay due and unserviced indefinitely.

**The trigger is the eligibility edge, not the forced-open edge.** An earlier
draft armed the wakeup only on a closed-to-`FORCED_OPEN` safety transition.
That is too narrow: the identical stranded-permit failure occurs whenever any
newly eligible gate is installed while the column family is dequeued. The four
cases that matter:

```text
NeedsCompaction sees due L1, current frame says defer
  -> returns false, column family dequeued
worker receives a policy response saying compact
  -> installs DUE_OPEN
  -> no scheduler event occurs
  -> the compaction never starts
```

1. a policy response moving a due level from defer to `DUE_OPEN`;
2. a policy response granting an `OPTIONAL_OPEN` token below threshold;
3. a safety transition to `FORCED_OPEN`;
4. the watchdog entering leveled fallback, or server reconnection installing a
   newly eligible frame after a fallback interval.

Case 1 is the one that decides Phase 1b. The deterministic oracle is exactly a
policy that flips due levels from defer to compact, so **without wakeup on the
policy edge the oracle cannot reliably pass parity**, and the wakeup must
therefore land no later than Phase 1b rather than in Phase 2.

Formally, arm the wakeup on the aggregate eligibility edge

\[
\Bigl(\sum_i E_i^{old} = 0\Bigr)
\;\land\;
\Bigl(\sum_i E_i^{new} > 0\Bigr),
\]

or, more conservatively and with generation-based deduplication, on each
level's individual closed-to-eligible edge. The conservative form is
recommended for the first implementation because it is harder to get subtly
wrong; the aggregate form is a later optimization once the per-level form is
measured.

RocksDB's entry points are `DBImpl::EnqueuePendingCompaction(ColumnFamilyData*)`
(`db/db_impl/db_impl_compaction_flush.cc:3360`, declared
`db/db_impl/db_impl.h:2483`) and `DBImpl::MaybeScheduleFlushOrCompaction()`
(`db/db_impl/db_impl_compaction_flush.cc:3049`). Both are `DBImpl` members and
both require the DB mutex.

`RLCompactionPicker` currently has access to neither. It holds no `DBImpl`, no
`ColumnFamilyData`, and no mutex handle; the sole mention of `DBImpl` in
`compaction_picker_rl.h` is a comment at line 70.

**A clearable `std::function` is not sufficient**, and an earlier draft's
proposal of one should not be implemented. Three problems:

*Deadlock.* The picker destructor joins the worker
(`compaction_picker_rl.cc`, `~RLCompactionPicker`: `worker_stop_.store(...)`,
`snap_cv_.notify_all()`, `worker_.join()`). Shutdown destroys `VersionSet`
while holding the DB mutex (`db/db_impl/db_impl.cc:697`: `versions_.reset();`
immediately before `mutex_.Unlock();`), and `ColumnFamilyData::~ColumnFamilyData`
is annotated `// DB mutex held` (`db/column_family.cc:743`). So:

```text
worker copies the callback and calls it
  -> worker blocks acquiring the DB mutex
shutdown holds the DB mutex
  -> destroys ColumnFamilyData
  -> ~RLCompactionPicker joins the worker
  -> worker is blocked on the mutex shutdown holds
  => deadlock
```

*Data race.* Clearing a plain `std::function` on one thread while another
copies it is a race regardless of when the clear happens.

*Wrong attachment point.* `DBImpl` does not construct the picker.
`ColumnFamilyData` does, at `db/column_family.cc:695`
(`compaction_picker_.reset(new RLCompactionPicker(...))`, alongside the other
styles at `:678`). "Install at picker construction" from `DBImpl` is therefore
not an existing seam.

**Use a DB-owned asynchronous control coordinator, not a synchronous wake
callback.** The same service owns wake requests, blocked-retry timers, and
deferred structural refreshes:

```text
class RLControlHandle {  // narrow, refcounted producer handle
  atomic<bool> closing;
  void RequestScheduling(cf_id, registration_generation,
                         eligibility_generation, retry_generation);
  void RequestSnapshotRefresh(cf_id, registration_generation,
                              source_generation, not_before_micros);
};

class RLControlCoordinator {  // owned and stopped by DBImpl
  short queue mutex + condition variable + timer queue;
  per-CF registration table;
  coordinator executor thread;
};
```

Both `Request...` methods only append or coalesce a small value request and
return. They never acquire the DB mutex, wait for the executor, or dereference a
`DBImpl`/`ColumnFamilyData`. The executor removes a request from the queue,
releases the queue lock, and only then acquires the DB mutex. Consequently a
picker worker can finish while a control request is waiting for the DB mutex;
`~ColumnFamilyData` cannot deadlock by joining that worker under the mutex.

**Registration and attachment.** Each attachment receives a fresh
`registration_generation`, distinct from policy decision/eligibility and
snapshot source generations. Requests carry the identities needed for their
purpose:

- `(cf_id, registration_generation)` identifies the live control target;
- `eligibility_generation` plus `retry_generation` deduplicates and validates a
  scheduling request without being invalidated by an identical new response;
- `source_generation` coalesces and validates a snapshot-refresh request.

The picker is constructed by `ColumnFamilyData`, but attachment is orchestrated
by `DBImpl` after the new/recovered `ColumnFamilyData` is visible under the DB
mutex. Dynamic creation attaches after successful `LogAndApply`; DB open
iterates every recovered CF and attaches before background scheduling begins.
The RL picker is constructed in an unattached, worker-not-started state; attach
publishes the initial structural/pressure views and only then starts the worker.
Construction-time injection in `ColumnFamilyData` alone is not sufficient.

**Per-CF invalidation and two-phase stop.** Whole-DB `closing` is not enough,
because a dropped column family can be destroyed while the DB and coordinator
remain live:

1. after a drop is durably accepted, but before releasing the DB mutex, remove
   `(cf_id, registration_generation)` from the coordinator table, atomically
   detach the picker's handle, cancel keyed timer entries, and mark the picker
   stopping. These operations do not wait for the coordinator or worker;
2. outside the DB mutex, stop and join that picker's worker. The caller's CF
   handle/reference keeps the object alive during this phase;
3. ordinary final destruction is allowed only after the registration has been
   invalidated and the worker is stopped. The destructor asserts both facts;
4. if `LogAndApply` fails, do not invalidate a still-live CF. Registration state
   follows the committed drop result, not merely the attempted request.

DB shutdown uses the same rule in bulk: under the DB mutex close registrations,
detach handles, and take the references needed for phase two; outside the mutex
stop/join picker workers and stop/drain the coordinator executor; only then
destroy `VersionSet` under the DB mutex. A queued request that was already
waiting simply resolves to no registration and is discarded.

**Executor validation and lock order.** The executor:

1. never holds its queue mutex while acquiring the DB mutex or calling a picker;
2. resolves the CF ID and checks registration generation, `IsDropped()`, and
   picker attachment under the DB mutex;
3. for a wake, revalidates current eligibility and eligibility/retry generation,
   then invokes `EnqueuePendingCompaction(cfd)` and
   `MaybeScheduleFlushOrCompaction()`;
4. for a refresh, follows the latest-generation rebuild procedure in part (a);
5. never retains a `ColumnFamilyData*` after releasing the DB mutex.

Workers issue requests with neither `permit_mu_` nor `snap_mu_` held. Requests
are edge triggered and deduplicated by stable eligibility generation; timed
blocked retries are distinguished by `retry_generation`. The
DB-mutex-to-`snap_mu_` order used by publication is never inverted.

**Testing this needs deterministic interleavings, not TSan alone.** Add one
`SyncPoint` test where a queued request is waiting for the DB mutex as its
specific column family is dropped, and another for whole-DB shutdown. Both must
complete, the request must be discarded by registration generation, and no
post-detach dispatch or object access may occur.

#### 7.8.3 Interaction with `NeedsCompaction()` return value

With (c) in place, `NeedsCompaction()` does not need to return true merely to
keep the scheduler polling: the wakeup supplies actuation, and the cached
snapshot of (a) supplies observation. Its return value can therefore stay
honest, reporting whether an authorized level currently has work.

A simpler variant remains available if (c) proves awkward to land first:
`NeedsCompaction()` additionally returns true whenever any level is
due-but-deferred with a pending safety deadline, accepting that
`PickCompaction()` may then return null. This keeps the column family queued
and therefore keeps both observation and actuation alive without any new
`DBImpl` dependency. It is cheaper to implement and strictly worse at steady
state, because it generates empty picks in proportion to how long a level sits
near its deadline. It is acceptable as a Phase 2 stepping stone but not as the
final design, because it conflates "this level has work" with "please keep
asking me".

Under neither design may `NeedsCompaction()` return true while no level is due
and no deadline is pending. Doing so would convert an idle database into a
permanent scheduling loop and would violate R5.

#### 7.8.4 Why this is a Phase 1 prerequisite

Section 14.2 requires the deterministic oracle `a_i(k) = 1[s_i(k) >= 1]` to
reproduce native leveled behavior within 5 percent on WAF. With observation
supply unrepaired, that oracle is not native leveled: it is native leveled plus
up to one observation period of trigger latency on every newly due level. At
the measured 2.6 observations/s that latency is roughly 400 ms, and at T=2 with
a 2 MiB write buffer the L0 trigger is reached in approximately 200 ms at the
observed ingest rate. The oracle would therefore miss its own parity gate for a
reason that has nothing to do with gate semantics, and Phase 1 would report a
failure whose cause is not the thing Phase 1 is testing.

Sequencing section 7.8 before the gate rewrite also produces an independently
useful measurement: it partitions the observed failure between the two defects.

The plan's own model predicts what to expect, and an earlier draft of this
section predicted the opposite. With `mu_{pulse,i} = q p_i c_i` and `p_1 = 0.33`
held fixed, raising `q` from 1.95 to 20 decisions/s raises L1 admission from
about 0.65 to about 6.6 operations/s, which would drain a 100-operation backlog
in roughly 15 seconds instead of roughly 150. **A substantial WAF improvement
from observation repair alone is therefore the model's prediction, not evidence
against it.** Phase 1a measures how much of the failure was sensing; the
residual is what held gates must close.

The falsifying result must be stated on realized service, not on decision rate.
Decision rate is an input to `mu_{pulse,i} = q p_i c_i`, not the quantity
itself: `p_i` could fall as `q` rises, leaving service flat. So "decision rate
reached 20/s but WAF did not move" is on its own consistent with the model.

The queueing model is falsified only if **realized per-level service rises
materially while debt and WAF do not improve** — that is, measured scheduled
jobs per second and scheduled bytes per second per level increase, and pending
compaction bytes, maximum score, and WAF all fail to respond. Instrument
`q`, `p_i`, and `c_i` separately so the three cases can be distinguished:
service did not rise, service rose but debt did not fall, or both moved as
predicted.

## 8. Fallback, maintenance, and final drain

### 8.1 Unavailable or stale RL server

When no valid response exists, the correct fallback is ordinary leveled
eligibility for every due level, not one fallback pulse for one level. The
ordinary parent picker may be used because fallback explicitly relinquishes RL
trigger authority for that interval.

All transitions overlapping fallback are invalid for replay.

### 8.2 Maintenance

TTL, periodic, bottommost, marked-file, forced blob-GC, and manual maintenance
remain native bypasses. They must not consume policy optional tokens or reset
policy due accounting unless their completed work actually makes the level
healthy.

### 8.3 Final `waitforcompaction`

Final drain remains a native parent-picker mode. Its metrics must be retained,
because deferred work is not avoided work. The report should separately expose:

- workload-phase compaction bytes/time;
- final-drain compaction bytes/time;
- total bytes/time used for authoritative WAF and runtime.

The observed RL run performed 205 trivial-move jobs, moving 751 files, in a
single second of the final drain burst.
Without phase separation, that catch-up is visible in total cost but not easy to
distinguish from learned policy behavior.

## 9. One-to-many attribution without candidate control

### 9.1 Attribution key

Use `(decision_id, generation, source_level)` as the policy attribution key.
Every native compaction scheduled while a due gate is open carries the same key
until a new frame supersedes it.

For each key, aggregate:

```text
policy action
gate-open and gate-close timestamps
score at open, maximum score, score at close
number of native pick attempts
number blocked
number scheduled
number completed
source bytes read
overlap/total bytes read
bytes written
trivial-move bytes
compaction duration
stall duration during the interval
override/fallback/drain flags
```

No field identifies a file chosen by RL. File numbers may remain in RocksDB's
ordinary LOG for auditing native behavior, but are not policy inputs or actions.

### 9.2 Executed-action definition

For training diagnostics:

- a defer action executed if the policy gate remained closed for a nonzero
  portion of its valid interval and no safety override replaced it;
- a due compact action executed if the gate opened, even if native conflicts
  temporarily prevented a schedule;
- `jobs_scheduled` separately distinguishes admission from plant execution;
- an optional compact action executed only after its one token successfully
  scheduled a native compaction;
- fallback, maintenance, drain, and superseded-before-use intervals are marked
  invalid for replay.

This avoids the current mistake of treating "requested compact" as successful
service.

### 9.3 Reward sequencing

Do not redesign reward in the same patch as the trigger bridge. First prove
native parity with a deterministic oracle. During the bridge phase, preserve
the existing reward but feed it correct execution and multiplicity fields.

After parity, evaluate whether reward normalization needs adjustment because a
due compact decision may now cover multiple native jobs. The physically correct
cost remains aggregate bytes and latency over the decision interval:

\[
C_k^{write} =
\frac{\sum_{j \in J_k} B_{written,j}}
     {\max(B_{user,k}, \epsilon)},
\]

not the number of jobs and not an assumed whole-level rewrite.

## 10. Concrete C++ design changes

### 10.1 `compaction_picker_rl.h`

Replace per-level single-use `ActionLease` storage with:

- one atomically installed policy decision frame;
- per-level permit mode/action/reason, decision generation, and stable
  eligibility-interval generation;
- a read-only refcounted view of the shared pressure observer's exact per-level
  event state; the picker does not maintain a second canonical due/pressure
  accumulator;
- per-level optional-token state;
- per-level counters for attempts, blocked picks, schedules, and completions;
- per-level consecutive-blocked count, monotonic backoff-until timestamp, and
  retry generation (section 5.2);
- response-age/watchdog state, with the watchdog bound derived from measured
  response spacing rather than from the configured interval (section 5.3);
- a shared pointer to the cached immutable structural snapshot, its build
  timestamp, and counters splitting publications by source (section 7.8.2a);
- an atomically loaded `shared_ptr<RLControlHandle>`, the per-CF registration
  generation, per-level eligibility-edge state, and eligibility/retry generations
  used to deduplicate wake requests (section 7.8.2c). The picker holds no
  `DBImpl` or `ColumnFamilyData` pointer and no raw callback; the asynchronous
  producer handle is the entire dependency;
- the structural/overlay split of section 7.8.2a: a `shared_ptr` to the cached
  `RLStructuralSnapshot` plus `source_generation`, `built_generation`, and the
  dirty timestamp. Execution fields stay per-observation and are never cached.

Remove `TakeLease()` and the rule that consumes authority before one pick.

Keep decision IDs and completion attribution, but redefine them as level-gate
interval attribution rather than candidate-like one-use authority.

### 10.2 `RunDecisionCycle()`

The worker should:

1. query Python off the DB mutex as it does now;
2. validate action-array length and level mapping;
3. build a complete decision frame stamped internally with the observation's
   structural `built_generation` and the current CF registration generation;
4. record whether each below-threshold compact action was granted one optional
   token by the observation-time mask, but defer the effective due/optional
   classification to admission against the current score;
5. discard and invalidate a frame whose structural generation was superseded
   while inference was in flight, preserving the last valid held frame and all
   safety overrides;
6. atomically publish the whole valid frame;
7. never globally discard all but one positive level action;
8. never reset due age or integrated pressure merely because compact was
   requested.

Safety and effective mode are evaluated again synchronously using current tree
state, so an action cannot rely only on the older observation's score.

**Worker tick responsibilities added by section 7.8.** `WorkerLoop()` currently
does one thing per tick: consume a snapshot if one happens to be valid, else
increment `skipped_ticks` and continue (`compaction_picker_rl.cc:503-506`).
It gains four responsibilities, which run on every tick regardless of whether
a policy round-trip occurs:

1. read the cached immutable snapshot, without consuming or rebuilding it
   (section 7.8.2a);
2. derive current `A_i` and `P_i` from the shared observer's last exact event
   state and the monotonic tick time (section 7.3);
3. evaluate the forced-open conditions and install forced-open permits
   directly, without waiting for a policy response or a scheduler event
   (section 7.8.2b);
4. on **any** closed-to-eligible edge — policy `DUE_OPEN`, optional token,
   safety `FORCED_OPEN`, or fallback entry — issue a wake request so the permit
   actually causes a scheduling attempt (section 7.8.2c).

Steps 1 through 3 are pointer reads and per-level scalar arithmetic. None
performs socket I/O, none rebuilds structural state, and none takes the DB
mutex, which is what keeps the tick within requirement R5.

Step 4 only enqueues through the asynchronous control handle, which the worker
must call while holding neither `permit_mu_` nor `snap_mu_`; the worker never
waits for the DB mutex. The DB-owned executor later acquires that mutex and
revalidates. Requests are edge triggered and deduplicated by stable eligibility
generation,
so a level that stays eligible produces one request rather than one per tick;
blocked-backoff expiry uses a distinct retry generation.

The consequence worth stating explicitly: after this change, a policy that
defers everything still produces a full-rate stream of observations, still has
its deferral bounded in real time, and that bound still results in real
scheduled work on an otherwise idle database. None of the three holds today,
and the third is the one an earlier draft of this plan left unaddressed.

### 10.3 `NeedsCompaction()`

On each scheduler check:

1. refresh current per-level scores and reconcile the due/pressure clocks
   against the shared observer's exact event state (section 7.8.2b), extending
   it to the current monotonic time and taking current synchronous state as
   authoritative for admission;
2. process drain/maintenance bypasses;
3. detect stale/unavailable policy and enter explicit leveled fallback;
4. re-evaluate per-level safety overrides against current state;
5. construct the current eligible-level mask, excluding levels inside a
   `BLOCKED_OPEN` backoff window (section 5.2);
6. return true if any eligible level has native work.

This function must not require an unconsumed one-shot lease in order to report
due work.

**What this function is no longer solely responsible for.** In the current
implementation `NeedsCompaction()` is the only publisher of observations
(`compaction_picker_rl.cc:609`) and would, under an earlier draft of this plan,
also have been the only advancer of the section 7 safety clocks. Both roles
move out per section 7.8:

- publication moves to a cached immutable snapshot refreshed on structural
  change, so a false return no longer stops the observation stream;
- deadline evaluation moves to the worker's monotonic timer over the shared
  event-driven clock state, so a false return no longer stops the safety
  guarantee;
- actuation of every newly eligible permit, plus blocked-backoff expiry, moves
  to the explicit coordinator wakeup of section 7.8.2c, so a false return no
  longer strands a permit nothing will act on.

`NeedsCompaction()` retains the synchronous refinement role, which is the one
it is actually well placed to perform, because it alone sees current
`VersionStorageInfo` at the moment of admission.

### 10.4 `PickCompaction()`

Pseudocode:

```text
if drain or maintenance bypass:
    return ordinary_parent_pick()

due_allowed = current due policy permits OR forced due permits

c = NativeLevelBuilderPick(allowed_source_levels=due_allowed)
if c is not null:
    source = c.start_level()
    attribute c to permit/reason for source
    record successful schedule
    return c

record native due path as currently blocked/empty

optional = highest-current-score level with an unconsumed optional token
if optional exists:
    c = PickCompactionFromLevel(optional, current_score[optional])
    if c is not null:
        consume only optional's token
        attribute and record c
        return c

return null
```

The native builder may move to another due level only when that level already
has independent authorization in `due_allowed`. Optional work is attempted only
after the allowed native due path finds no schedulable due compaction, so a
proactive token cannot displace known due work.

### 10.5 `compaction_picker_level.cc`

Do not add an exact-file API. Add only an allowed-source-level mask/predicate to
the existing native leveled builder for due work. This adapter must reuse the
normal `SetupInitialFiles` flow rather than duplicate its ordering and
L0/base-level special cases. Retain `PickCompactionFromLevel` for an authorized
below-threshold one-shot optional action. Audit both paths for these
requirements:

- the allowed mask contains levels, never files;
- the native due path uses current scores and the normal builder ordering;
- the optional path receives the current, not observation-time, level score;
- it uses current native compaction priority;
- it returns null on native conflicts without retargeting outside the forced
  source level for optional work;
- registration recomputes scores so repeated scheduling cannot blindly
  overqueue a level.

### 10.6 Telemetry

Extend trigger telemetry with per-level:

- policy gate open/closed duration;
- due duration;
- maximum score;
- integrated excess pressure;
- native pick attempts/blocked/scheduled/completed;
- number and bytes of trivial moves;
- decision-to-first-schedule latency;
- decision-to-healthy latency;
- consecutive-blocked count and time spent in backoff (section 5.2).

Add these global observation-health metrics, which the current diagnostic line
cannot express:

- worker ticks, ticks served from the cache, and anomalous ticks with no cache;
- `observation_age`, `structural_snapshot_age`, and `dirty_age` distributions at
  the moment of use, never collapsed into one ambiguous snapshot-age field;
- source/built/score-event/registration generations and maximum generation lag;
- achieved response spacing distribution, which is the input to the section 5.3
  watchdog bound;
- policy responses accepted and discarded for superseded structural generation,
  with inference age at discard;
- snapshot rebuilds by source: version install versus compaction registration
  versus completion/other structural events versus deferred-coordinator refresh,
  plus coalesced changes, coordinator deadline misses, refresh queue delay,
  rebuild latency, and rebuilds per worker tick as the R5 check;
- scheduler wakeups enqueued, deduplicated, executed, dropped for stale permit or
  retry generation, and dropped for missing/stale CF registration;
- blocked-retry timers armed, cancelled by version change, expired, and
  successfully admitted;
- per-CF attach, invalidate, worker-stop, and queued-request discard counts;
- watchdog expiries, separated by whether the server was reachable.

Log a compact final per-level diagnostic table. Aggregate totals such as
`actuations=75` are insufficient to explain which level was starved. The
evidence run is the worked example: `queries=75 skipped_ticks=694 nc_calls=440
publish_calls=99` was enough to show that something was wrong with the sensing
path, but not enough to show that L3 through L5 received zero authorizations
for the entire run. That fact had to be recovered by counting
`RL lease scheduled` lines. It should be a first-class metric.

### 10.7 Concurrency and lock discipline

The worker thread may construct a new immutable decision frame without the DB
mutex. It should publish that frame under one short permit mutex or through an
atomic shared frame pointer. The DB scheduler, already holding the appropriate
RocksDB synchronization, reads one complete generation and never waits for
socket I/O or inference.

Required ordering rules:

1. never hold the permit mutex while performing a socket operation;
2. never wait for the worker while holding the DB mutex;
3. install all per-level actions from one response atomically;
4. derive current scores, conflicts, and synchronous safety from the current
   `VersionStorageInfo`, not the observation cached in the worker;
5. once a `Compaction` has been registered, a newer defer frame affects only
   future scheduling; it does not cancel already admitted RocksDB work;
6. completion telemetry references the immutable decision ID/decision generation
   copied onto the `Compaction`, not whichever frame is current at completion
   time. Eligibility generation is retained separately for wake/backoff tracing;
7. the lock order is DB mutex first, then `snap_mu_`, then `permit_mu_`. The
   version-install publisher of section 7.8.2a runs with the DB mutex already
   held and acquires `snap_mu_`, which fixes that order; no other thread may
   invert it. The earlier draft of this section named only `permit_mu_`, which
   was incomplete, because `snap_mu_` is on the same inversion path;
8. worker-side safety evaluation and forced-open installation (section 7.8.2b)
   run without the DB mutex, from already-published snapshot state, and publish
   through the same atomic frame mechanism as a policy response so that a
   scheduler thread never observes a partially updated permit set;
9. control requests (section 7.8.2c) are issued with **no** picker lock held.
   Enqueue takes only the short coordinator queue mutex and never the DB mutex;
   the executor releases the queue mutex before acquiring the DB mutex, so the
   two mutexes have no nested order;
10. each request carries a column-family ID and registration generation plus its
    operation-specific eligibility/retry or source generation. The executor resolves
    and revalidates under the DB mutex and never retains a Cfd pointer after
    releasing it;
11. successful CF drop invalidates and detaches the registration under the DB
    mutex, then stops/joins that worker outside the mutex. Ordinary final
    destruction asserts invalidated/stopped state. This applies while the DB
    remains open, not only at shutdown;
12. shutdown invalidates all registrations under the DB mutex, stops picker
    workers and the coordinator outside it, and only then destroys `VersionSet`
    under the mutex. No thread joined during teardown can be synchronously
    waiting for the DB mutex;
13. the centralized pressure observer is updated under the DB mutex immediately
    after every active-score recomputation. Workers consume immutable observer
    state and never publish a competing accumulator.

This prevents a response arriving during a native pick from creating a mixed
generation or misattributing a completion, prevents the new observation path
from introducing a lock inversion between the worker and the DB mutex, and
prevents the new wakeup path from outliving what it references.

## 11. Python and protocol changes

### 11.1 Keep protocol v2 response semantics

The response stays:

```json
{"actions":[0,1,0]}
```

It still means defer/compact for each level in request order. No response field
for files is added.

### 11.2 Add observation fields only after bridge parity

After the C++ oracle passes, add these per-level observations if needed by the
learner:

```text
due_age_micros
integrated_excess_pressure
gate_open
gate_mode
jobs_attempted
jobs_blocked
jobs_scheduled
jobs_completed
decision_to_first_schedule_micros
consecutive_blocked
in_backoff
```

Global fields are also required, because the agent's time-based discount and
credit horizon are only correct if it knows both observation delay and whether
a known structural update is unpublished:

```text
observation_age_micros
structural_snapshot_age_micros
dirty_age_micros
source_generation
built_generation
score_event_generation
```

Today the agent cannot distinguish an observation taken 12 ms ago from one
taken 400 ms ago, nor an old-but-current idle snapshot from a snapshot known to
be stale. Collapsing these cases into `snapshot_age_micros` would be a silent
error in any wall-clock-discounted return and in conservative fallback.

Adding JSON fields is compatible with the v2 action contract, but changing the
encoded model state requires updating `ML_STATE_FIELDS`, state dimension,
normalization tests, and checkpoint compatibility. Since the system is
cold-start, old learned checkpoints should be rejected rather than silently
loaded with a different state dimension.

### 11.3 Action masking

Retain the current principle:

- compact is always available for a due level with files;
- optional compact is available only above `s_min` and when safety does not
  prohibit optional I/O;
- a level with no files has only defer;
- safety override changes the executed action/replay validity, not the model's
  historical chosen action.

## 12. Required tests

### 12.1 C++ state-machine tests

1. **One due compact response schedules repeatedly.** Construct an overfull L1
   requiring multiple native compactions. Install one compact frame. Repeated
   scheduler calls must produce multiple compactions until score is below 1,
   without another response.
2. **One optional response schedules at most once.** With score below 1, install
   compact and prove the optional token cannot be reused.
3. **Defer remains authoritative.** A due deferred level must not be selected
   through another level's open permit.
4. **Multiple open levels use native score order.** Open L0 and L1 with L1's
   score higher; L1 must be attempted first unless a true hard-stop exception
   applies.
5. **Blocked authorized level.** If L1 is blocked and L2 is independently open,
   L2 may run. If L2 is deferred, nothing may run through L1's authorization.
6. **No false defer reset.** A compact request that loses arbitration must not
   reset due age or integrated pressure.
7. **Due episode reset.** Due age resets only after current score becomes less
   than 1.
8. **Wall-clock budget.** Advance a fake clock without new decisions and prove
   a deferred due level becomes forced open at `H_i`.
9. **Pressure budget.** A high score exhausts `J_i` faster than a near-1 score.
10. **Response watchdog.** A stale compact/defer frame transitions to explicit
    native fallback, not silent no-compaction.
11. **Fallback parity.** Unavailable server invokes ordinary leveled due
    behavior and invalidates affected replay transitions.
12. **Maintenance and drain.** Native bypasses remain functional and do not
    consume optional tokens.
13. **Observation liveness under total deferral.** Install a frame deferring
    every level, hold the plant idle so no flush or compaction occurs, and
    advance the worker clock. Assert that **observations continue to be
    emitted** from the cached structural snapshot and that `skipped_ticks` does
    not grow. Do not assert new structural publications: an unchanged tree must
    not be rebuilt, which is what test 23 checks. This is the direct regression
    test for section 3.8 and must fail against the current implementation.
14. **Safety clock advances with an idle plant.** With every level deferred and
    a due level present, advance a fake clock past `H_i` without any scheduler
    event. Assert the level is forced open. This is the regression test for the
    section 7.3 sampling requirement; a version that advances `A_i` only inside
    `NeedsCompaction()` must fail it.
15. **Pressure accrues during backoff.** A level in a `BLOCKED_OPEN` backoff
    window must continue to accumulate `A_i` and `P_i` and must still reach its
    forced-open deadline on schedule.
16. **Blocked backoff is bounded.** A permanently conflicted open level must
    stop being offered to the scheduler after `K_blocked` consecutive failures
    and must resume when the conflict clears, without its permit closing and
    without authority transferring to a deferred level. Repeated identical open
    decision frames must not reset the consecutive-failure count. In a second
    case leave the database completely idle, advance the fake monotonic clock
    through `backoff_until`, and assert the coordinator emits a new
    retry-generation wake without any external scheduler event.
17. **Watchdog does not fire against a healthy server.** With responses
    arriving at the measured spacing and the policy deferring every level for
    an extended period, assert zero watchdog expiries. A watchdog bound derived
    from the configured interval rather than from measured spacing must fail
    this test.
18. **Idle database does not busy-poll.** With no due level and no pending
    deadline, assert `NeedsCompaction()` returns false and that scheduling work
    is not generated purely to sustain observation (requirement R5).
19. **Idle forced-open produces an actual scheduling attempt.** The strongest
    test in this list, and the one that distinguishes a working safety
    guarantee from a permit bit nobody reads. Bring a level to due, defer it,
    drain the plant so the column family is dequeued and no flush or compaction
    is outstanding, then advance a fake clock past `H_i`. Assert that a real
    scheduling attempt occurs by `H_i + epsilon`: a control request is queued,
    the coordinator resolves the current registration,
    `EnqueuePendingCompaction` is reached for that column family, and
    `PickCompaction()` is entered. Asserting only that the permit changed to
    `FORCED_OPEN` is insufficient and would pass against a design that never
    schedules anything. This is the regression test for requirement R3.
20. **Control dispatch takes no lock it must not.** Assert, under TSan or with
    instrumented mutexes, that worker enqueue holds neither `permit_mu_` nor
    `snap_mu_`, the coordinator releases its queue mutex before acquiring the DB
    mutex, and the version-install publisher's DB-mutex-to-`snap_mu_` order is
    never inverted (requirement R4).
21. **Wakeup is edge triggered.** A level that remains eligible across many
    ticks and repeated identical response frames must produce one wakeup per
    eligibility generation, not one per tick or decision generation. Run this
    for policy `DUE_OPEN`, `OPTIONAL_OPEN`, `FORCED_OPEN`, and fallback.
22. **Wakeup is lifetime safe.** Tear down a column family while its worker is
    mid-tick and assert the registration is invalidated, its handle is detached,
    its worker is stopped outside the DB mutex, and no queued request is
    delivered to that registration (requirement R6).
23. **Cached snapshot is not rebuilt per tick.** With an unchanged tree, assert
    that N worker ticks produce zero `BuildSnapshot` calls and that the worker
    still advances its clocks and still emits observations (requirement R5).
24. **Coalescing never drops the final update.** Emit two structural changes
    inside one limiter window, then make the database completely idle. Assert
    the second state eventually reaches the agent, that `built_generation`
    converges to `source_generation`, and that the view is reported stale until
    it does. This is the regression test for the discard bug in section 7.8.2a.
25. **Wakeup fires on a policy eligibility edge, not only on forced-open.**
    With the column family dequeued because the current frame defers a due L1,
    deliver a policy response setting L1 to `DUE_OPEN` and assert a scheduling
    attempt occurs without any independent scheduler event. Repeat for an
    `OPTIONAL_OPEN` token grant and for watchdog-to-fallback entry. This is the
    test that decides whether the Phase 1b oracle can pass at all.
26. **Shutdown while a control request is waiting for the DB mutex.** Using
    `SyncPoint`, arrange for the coordinator to have dequeued a wake request and
    to be waiting for the DB mutex at the instant shutdown begins. Assert bulk
    invalidation, worker stop, coordinator drain, and shutdown complete. TSan
    cannot detect this; the interleaving must be forced.
27. **Pressure integration is zero-order hold.** Drive a level's score from 1
    to 13 at a known instant between two worker ticks and assert the
    accumulated `P_i` matches the split-interval formula in section 7.2, not
    the trapezoidal value. Repeat for a 13-to-1 transition.
28. **Epsilon is bounded and measured.** Assert every component of `epsilon`
    is exported and that the measured total stays within its configured bound
    across the fake-clock suite.
29. **Drop one CF while its request waits for the DB mutex.** Keep the DB and a
    second CF live. With `SyncPoint`, pause the coordinator after it dequeues a
    request for the target CF but before it obtains the DB mutex. Successfully
    drop that CF, invalidate its registration, and stop its worker. Release the
    coordinator and assert it discards the stale registration generation,
    touches no destroyed object, and the second CF continues operating.
    Parameterize the test for a deferred-refresh request in Phase 1a and a
    scheduling request in Phase 1b.
30. **Every active score change reaches the pressure observer.** Exercise flush,
    registration, completion, ingestion, manual state change, `SetOptions`,
    recovery, and rollback/error fixtures. For every accepted change, compare
    observer generation, due transition, and zero-order-held integral with an
    exact fake-clock model. A direct production `ComputeCompactionScore()` that
    changes active state without invoking the observer must fail the test.
31. **SLO mask hysteresis and fallback.** With fake rolling windows, prove that
    read/space breaches force due gates, write breaches suppress only optional
    work without suppressing due safety work, three-window entry and recovery
    are required, and missing, mismatched, un-attributable, or
    simultaneous-breach inputs invoke tuned native due eligibility for the
    responsible level or all due levels when responsibility is unknown.
32. **Admission reclassifies compact mode using current score.** Grant an
    optional compact below 1, delay admission until the current score is due,
    and prove the held compact action supplies repeated due service. Conversely,
    issue a due compact response, make the level healthy before admission, and
    prove it performs no optional work unless the original response also carried
    a valid optional token. File selection remains native in both cases.
33. **A stale structural response cannot weaken control.** Send an observation
    from built generation `g`, advance `source_generation` before its response
    returns, and return both an optional grant and a defer in separate fixtures.
    Assert C++ stamps the frame with `g`, discards the entire response, preserves
    the last valid frame and safety gates, marks replay invalid, and accepts
    fresh advice again after the deferred rebuild converges. No response-schema
    change is required.

### 12.2 Native-file-selection proof

Build identical `VersionStorageInfo` fixtures for regular and RL paths. For an
authorized source level, assert that both paths select the same native priority
file and the same final clean-cut/overlap-expanded input set. The assertion is
about native equivalence; no file identity appears in the RL message.

### 12.3 Service-rate regression test

Create many non-overlapping L1 files above target with an empty L2, so native
work is primarily trivial moves. One due compact response must allow the tree
to drain at scheduler speed. Assert:

\[
N_{scheduled} > 1
\]

for one decision and that no new socket response is required between those
schedules.

This test directly prevents recurrence of the pulse-rate bug.

### 12.4 Python tests

- parse new execution/multiplicity fields;
- distinguish chosen action from effective gate and successful jobs;
- exclude fallback/maintenance/drain intervals from replay;
- preserve terminal transitions when one action owns multiple completions;
- validate state dimensions and reject incompatible checkpoints;
- verify time-based discount uses actual elapsed interval.

### 12.5 End-to-end deterministic trace

Record for every decision:

```text
decision_id/decision_generation/eligibility_generation
per-level chosen action
per-level effective gate/reason
current score and due age
each native scheduling attempt
selected source level
scheduled job ID
completion bytes/result
gate-close reason
```

The trace must prove:

- every workload-phase compaction maps to an open level gate or explicit
  bypass;
- no deferred level is compacted under another level's authority;
- a due-open decision may map to multiple native jobs;
- all SSTs were selected inside RocksDB.

## 13. Implementation phases and gates

### Phase 0: Preserve and automate the diagnosis

Before changing control flow:

1. add a read-only log-analysis script or test fixture for the supplied 1M/T2
   logs;
2. report compactions and bytes by source/output level;
3. report compaction input-size distributions;
4. report maximum score and pending debt over time;
5. report policy schedules by level/reason and final-drain work separately.

**Gate:** the automated report reproduces the figures in section 3.

### Phase 1a: Restore observation supply (observation only)

Implement section 7.8(a) alone: the cached immutable structural snapshot, its
refresh points, the refresh/timer subset of the asynchronous control coordinator,
the three distinct ages/generations, and the observation-health telemetry of
section 10.6. Implement per-CF registration, invalidation, and stop ordering in
this phase because the refresh queue already creates the lifetime relationship.
Leave scheduling-wake delivery disabled, pulse semantics, the existing lease
arbitration, and the decision-count deferral counter untouched.

Safety clocks are implemented in this phase in **shadow mode** only: `A_i`
and `P_i` are captured by the centralized score observer and derived on the
worker tick, then exported to telemetry, but no forced-open permit is installed
and no scheduling wakeup is issued. Shadow mode exists to collect the
due-episode distributions that section 7.4 calibration needs, and to prove the
clocks advance on an idle plant, without yet changing control flow. Section
7.8(b)'s **live safety activation** belongs to Phase 2. Section 7.8(c)'s general
eligibility wake belongs to Phase 1b, while the refresh executor it shares is
already present here.

**Gate:** on a 1M/T2 run, `skipped_ticks` falls to approximately zero, achieved
decision rate approaches the configured `1000 / RL_DECISION_INTERVAL_MS`,
**`dirty_age` stays within its configured bound**, and tests 13, 18, 23 and 24
of section 12.1 pass. The per-CF registration/refresh lifecycle portions of
tests 22 and 29 and the pressure-observer coverage in test 30 also pass before
the coordinator is allowed to carry scheduling requests.

The gate is stated on `dirty_age`, the latency from a known unpublished
structural change to its publication, not on absolute `structural_snapshot_age`.
An unchanged idle tree may hold an arbitrarily old structural snapshot without
that being a defect; only an *unpublished change* is one. Test 17 is not in
this gate: it exercises the response watchdog, which Phase 2 implements.

**Expected effect on amplification.** WAF should improve, possibly
substantially, and this supports rather than undermines the diagnosis; see
section 7.8.4 for the arithmetic. Record the improvement, because it partitions
the failure between the sensing defect and the pulse defect and sizes what
Phase 1b still has to close.

Report `q`, `p_i`, and `c_i` separately, not just the decision rate. The
falsifying result is that realized per-level service rises materially while
debt and WAF do not improve; a rise in `q` alone proves nothing, since `p_i`
may fall to compensate.

### Phase 1b: Repair the trigger bridge with a deterministic oracle

Implement held per-level permits, repeated due scheduling, optional one-shot
tokens, native-score arbitration, and correct due accounting.

**The coordinator's scheduling-request path in section 7.8.2c lands in this
phase, not Phase 2.**
The coordinator service and CF registrations already exist from Phase 1a for
deferred refresh; this phase enables and validates its scheduling-request type.
The oracle is precisely a policy that flips due levels from defer to compact,
so every oracle decision is a policy eligibility edge. With the column family
dequeued while the previous frame deferred, an oracle `DUE_OPEN` that nothing
wakes is a stranded permit and the compaction never starts. Deferring the
wakeup to Phase 2 would make this phase's parity gate unreachable for reasons
unrelated to gate semantics. Tests 21, 22, 25, 26, 29, 32 and 33 are part of
this phase with scheduling requests enabled.

Keep the model out of the validation path by using an oracle:

\[
a_i(k) = \mathbf{1}[s_i(k) \ge 1].
\]

The oracle never compacts early and never intentionally defers baseline-due
work.

**Gate:** oracle behavior matches regular leveled behavior within the parity
criteria in section 14. Do not proceed on model tuning if it fails.

**If the gate fails, diagnose in this order before concluding the gate design
is wrong:**

1. *Trigger latency.* Compare the distribution of "level became due" to "level
   became eligible". If the median exceeds one observation period, Phase 1a is
   incomplete rather than the gate being wrong.
2. *Arbitration starvation.* Check per-level authorization counts. Any level
   with zero authorizations while it was due is an arbitration defect, not a
   parity tolerance problem.
3. *Blocked backoff.* Check consecutive-blocked and backoff-window totals. A
   level spending significant time suppressed indicates `K_blocked` or
   `W_blocked` is mis-set.
4. *Gate close timing.* Confirm due episodes end on `s_i < 1` and not on a
   scheduled compaction, per section 5.5.

Only after all four are excluded should the held-gate semantics themselves be
reconsidered.

### Phase 1c: Restore the tuned baseline and SLO manifest

Section 7.4 calibrates every safety limit from "the preregistered tuned leveled
baseline". That artifact does not currently exist. `PROJECT_HISTORY_AND_
SYSTEM_DESCRIPTION.md:1184-1185` records both the tuned leveled grid with
preregistered selection and the `baseline_slo.json` generator as **not
currently available**, removed during the candidate-controller cleanup. No
phase in the earlier draft restored them, so Phase 2 would have had nothing to
calibrate against and would have shipped bootstrap caps presented as
baseline-derived envelopes.

This phase restores them:

1. **Enable baseline-side export from the shared episode instrumentation
   first.** This is a prerequisite, not a detail. Section 7.4 needs due-age,
   integrated-pressure and maximum-score distributions *from regular leveled
   RocksDB with no RL in the process*. A design that kept the Phase 1a clocks
   only in `RLCompactionPicker` could not produce them, because a regular run
   constructs `LevelCompactionPicker` instead (`db/column_family.cc:678` versus
   `:695`). Revision 5 therefore places episode instrumentation in the
   centralized `CompactionPressureObserver` in Phase 1a; this phase enables and
   verifies its regular-run export so both arms use identical event-driven code.
   Polling external properties on a timer is not an acceptable substitute: it
   misses short due episodes entirely, and a distribution missing its short
   episodes cannot support a quantile or tolerance bound;
2. run the independently controlled tuned leveled grid for the target workload
   and size;
3. perform preregistered selection and record the selection rule alongside the
   result, so the comparator cannot be chosen after seeing RL outcomes;
4. export a workload-specific SLO manifest. It must carry the safety inputs
   section 7.4 consumes *and* the original SLO fields, because Phase 2 enforces
   score and debt envelopes but the project's acceptance criteria are stated in
   latency and space:

   - selected baseline options and workload identity;
   - per-level due-episode duration, maximum score, integrated pressure, and
     normalized pending-debt distributions, each with its episode count;
   - operation-progress envelopes;
   - expected physical-space reference and its explicit allowed limit;
   - Get, scan, and write average/p95 reference values and explicit allowed
     limits. Store both rather than ambiguously naming a reference a "limit";
   - the metric definitions used, so a later run cannot silently substitute a
     different one;
   - sample thresholds and rolling-window hysteresis parameters;

5. define how an SLO breach maps into trigger-only behavior: force due gates
   open, or disable optional work, while leaving SST selection entirely native.
   Breach detection must cover the rolling latency and space windows above, not
   only the score and debt envelopes of Phase 2. The retired v3 consumer must
   not be resurrected.

The Phase 1a observer supplies both arms' episode distributions; step 1 proves
that the regular-run export is active before any manifest is calibrated.

**Gate:** a workload-tagged manifest exists, its per-level episode counts are
recorded, and every level is marked either calibrated or explicitly
un-calibrated per section 7.4. Levels below the episode threshold stay on the
conservative bootstrap cap and are labelled as such in exported metadata.

### Phase 2: Add time/pressure safety and fallback

Activate section 7.8(b): promote the shadow clocks to live forced-open control,
reusing the asynchronous control coordinator already landed in Phase 1b so that
a safety transition is just one more eligibility edge. Implement hard score
envelopes, response watchdog, normalized pending debt, and hysteresis, using
the Phase 1c manifest for limits. Validate with fake-clock and stress tests.

Implement the manifest's latency/space mask explicitly; score/debt caps are not
a substitute for the project's SLOs:

1. validate manifest schema version, workload/configuration fingerprint,
   selected baseline options, metric definitions, and sample-window parameters
   before enabling learned control. Missing or mismatched fields select
   conservative tuned-native fallback, never guessed limits;
2. maintain rolling Get, scan, aggregate-write, and physical-space windows. Do
   not classify a latency window until its manifest `minimum_samples` is met;
3. enter a breach only after three consecutive classifiable windows above the
   manifest's explicit allowed limit, normally recorded as
   `1.02 * baseline_reference`, and leave it only after three consecutive
   compliant windows. Isolated or under-sampled windows preserve the previous
   state;
4. if either Get or scan average/p95, or the physical-space limit, is breached,
   prohibit further deferral of due responsible levels by forcing their gates
   open. The trigger may expose those levels but RocksDB still chooses every
   input file;
5. on a write-average/write-p95 breach, revoke and prohibit optional
   below-threshold gates and stop granting new optional I/O. Already registered
   RocksDB work is not cancelled, and due safety work is not suppressed;
6. on simultaneous read/write breach, un-attributable global breach, no valid
   native work for the identified level, or unavailable policy, enable the tuned
   leveled due trigger for the specifically responsible level; if responsibility
   cannot be established, enable tuned due eligibility for all due levels.
   Optional work remains disabled;
7. record chosen action, effective masked action, breach windows, responsible
   level derivation, and fallback reason. Every affected transition is invalid
   for replay.

The Phase 1c manifest must define the trigger-only responsibility mapping—for
example, which retained run/debt contribution associates a breached read or
space envelope with a level. If that mapping is absent or ambiguous, rule 6 is
mandatory. The mask never names or ranks SST files and does not claim to be a
mathematical latency guarantee; final paired acceptance remains authoritative.

**Gate:** no synthetic test can drive a level beyond its configured hard
envelope while valid native work exists, and tests 14, 15, 16, 17, 19, 27, 28,
and 31 of section 12.1 pass. Test 19 is the one that distinguishes this phase
from a permit bit that nothing acts on; test 28 is what makes
`H_i + epsilon` a claim rather than a hope; test 31 proves the SLO manifest
changes trigger behavior rather than merely producing telemetry.

### Phase 3: Repair attribution and observations

Add decision-frame aggregation, one-to-many job attribution, phase-separated
drain metrics, and per-level diagnostics. Update Python state only after C++
parity.

**Gate:** every completed compaction is attributable to a policy gate or
explicit bypass, and fallback-affected transitions never enter replay.

### Phase 4: Re-enable analytic prior and online learning

Run, in order:

1. leveled oracle;
2. trigger bridge with analytic prior but learning disabled;
3. prior plus online residual learning;
4. safety-disabled learner as an ablation only, not as the production policy.

**Gate:** prior-only cannot violate the hard score/debt envelope; learning adds
no control-flow anomaly.

### Phase 5: Experimental expansion

Run T=2 first because it has the highest scheduling pressure with the current
512 KiB SST target. Only after T=2 passes should T=6 and T=10 run. Only after
the 1M smoke/parity suite passes should 10M--50M experiments begin.

## 14. Acceptance criteria

### 14.1 Functional invariants

All must hold:

1. protocol responses contain no file identity;
2. every RL-triggered physical compaction uses native file selection;
3. no deferred level runs without its own safety/bypass reason;
4. one due compact decision can schedule multiple native jobs;
5. one optional compact decision schedules at most one job;
6. due age is independent of query frequency **and of scheduler-observation
   frequency**;
7. losing arbitration does not reset due state;
8. stale/unavailable policy becomes explicit leveled fallback;
9. maintenance and drain remain native;
10. every completion has decision-level or bypass attribution;
11. observation supply is independent of gate state: a policy that defers every
    level continues to receive observations at the configured rate;
12. every due level reaches its forced-open condition within `H_i + epsilon` of
    wall time under any plant behavior, including a fully idle plant;
13. **any** closed-to-eligible transition produces an actual scheduling
    attempt, not merely a changed permit bit, on an otherwise idle database.
    This covers policy `DUE_OPEN`, optional tokens, safety `FORCED_OPEN`, and
    fallback entry; blocked-backoff expiry similarly produces a retry-generation
    scheduling attempt without an external plant event;
14. an idle database with no due level and no pending deadline generates no
    scheduling work merely to sustain observation;
15. no worker carries a picker lock into control enqueue, the coordinator never
    carries its queue mutex into DB-mutex acquisition, no wake request can be
    delivered into a dropped or destroyed column family, per-CF drop and
    whole-DB shutdown cannot deadlock against an in-flight request, and another
    CF remains operational after one registration is invalidated;
16. a structural change is never lost: `built_generation` converges to
    `source_generation` even if the database goes idle immediately after the
    change;
17. pressure integration is zero-order hold over piecewise-constant scores, and
    `epsilon` is decomposed, exported, and bounded;
18. every strict-improvement metric has demonstrated baseline headroom, and
    every one-sided non-regression metric has a feasible, correctly directed
    bound (section 14.5);
19. the latency/space SLO mask uses minimum samples and three-window hysteresis,
    changes only trigger eligibility, and falls back conservatively on an
    invalid manifest or ambiguous level responsibility;
20. advice derived from a superseded structural generation is discarded in full
    and cannot replace the last valid frame or any safety gate.

### 14.2 Oracle parity envelope

For at least three paired 1M/T2 seeds with alternating order:

- same operation counts and logical bytes;
- no oracle level's maximum score exceeds
  `max(1.05 * baseline_max, baseline_max + 0.10)`;
- oracle pending-debt maximum is within 5% of regular;
- WAF is within 5% of regular;
- point probes/Get and sorted-run seeks/scan are within 5% of regular;
- stall duration is no more than regular plus one scheduling quantum and no
  new stop event occurs;
- mean L0-to-L1 input size is within 10% of regular;
- oracle schedules more than one native job from at least one held due permit
  in the T=2 service-rate test;
- every level that RocksDB considered due during the run received at least one
  authorization, with zero-authorization levels treated as a hard failure
  rather than a tolerance miss;
- median latency from "level became due" to "level became eligible" is below
  one observation period, which is the check that distinguishes a gate defect
  from a residual Phase 1a defect.

Observation-health preconditions, which must hold before the parity figures
above are considered meaningful:

- `skipped_ticks` is approximately zero;
- achieved decision rate is within 20% of `1000 / RL_DECISION_INTERVAL_MS`;
- watchdog expiries are zero with a reachable server.

These are engineering parity tolerances, not final research claims. Failure
means the bridge still changes RocksDB behavior before learning is involved.
A parity result reported without the observation-health preconditions met is
not interpretable, because trigger latency and gate semantics are confounded.

### 14.3 Safety smoke envelope

With learned deferral enabled on 1M/T2:

- no L1+ score may exceed the calibrated hard cap;
- L0 must not cross stop threshold;
- normalized pending debt must remain within its safety envelope;
- fallback count must be zero in a healthy-server run;
- watchdog expiry count must be zero in a healthy-server run, including during
  extended intentional deferral;
- `skipped_ticks` must be approximately zero;
- every level due at any point must show a nonzero authorization count, or an
  explicit forced-open record explaining why it was serviced by safety instead;
- all override and blocked-attempt counts must be explainable from the trace;
- no final-drain burst may hide unresolved workload-phase starvation without
  being separately reported.

### 14.4 Final research acceptance

After the bridge and safety gates pass, retain the project's stricter paired
evaluation:

- 95% CI for RL-minus-tuned-baseline WAF strictly below zero;
- 95% CI for point-read amplification strictly below zero;
- 95% CI for scan amplification strictly below zero, **subject to the
  metric-sensitivity gate below**;
- upper 95% CI for relative space regression at most 2%;
- upper 95% CI for Get, scan, and aggregate-write average/p95 regression at
  most 2%;
- no increase in stall duration;
- read-heavy and write-heavy stress suites introduce no new stalls and stay
  inside the same space/latency bounds.

### 14.5 Metric-sensitivity gate, required before final experiments

The scan-amplification criterion above may be **mathematically unsatisfiable on
the current workload**, and this must be resolved before results are collected
rather than after.

Formal scan amplification is `(returned + internal skipped) / returned`. On the
1M/T2 evidence `rocksdb.number.iter.skip` is 0 in both arms, so the baseline
value is

\[
\frac{7{,}353{,}511 + 0}{7{,}353{,}511} = 1.000,
\]

which is the metric's minimum. Nothing can be strictly below it. A criterion
requiring the RL-minus-baseline CI to lie strictly below zero is therefore an
experiment designed to fail regardless of controller quality, and a run that
fails it would say nothing about the controller.

The correct generalization depends on the direction of the criterion:

- a **strict-improvement** criterion requires measurable baseline headroom in
  the improvement direction, exceeding paired-seed measurement noise. WAF,
  point-probe amplification, and the current strict scan-amplification objective
  are in this class;
- a **one-sided non-regression** criterion does not require the baseline to be
  strictly inside the entire achievable range. It requires that the allowed set
  intersect the physically achievable range and that the upper regression bound
  be measurable. A space-amplification baseline at its lower bound remains a
  meaningful comparator: a 2% upper-regression limit still rejects values above
  `1.02 * baseline`. Latency limits are evaluated the same way.

Before Phase 5, record for every criterion its direction, attainable bound,
baseline value, paired-seed noise, and a proof that the requested inequality is
feasible. Require improvement headroom only for strict-improvement criteria.

For scan amplification specifically, choose one, and record the choice **before
running**:

1. use a workload whose baseline generates real internal iterator skips, i.e.
   one with update and delete history over the scanned ranges, so the metric
   has headroom;
2. keep the current workload and **redefine the formal scan objective** to a
   metric with headroom here, such as sorted-run seeks per scan, which moved
   6.731 to 8.740 in the evidence set and clearly has room in both directions.
   This is a replacement objective measuring run searches, not an equivalent
   formula for internal iterator skips;
3. keep the current workload and demote scan amplification from a criterion to
   a reported non-regression check, with the explicit note that the workload
   cannot exercise it. This changes the research acceptance scope and is not an
   acceptance-equivalent metric repair.

Option 1 is the most faithful to the original intent. Option 2 is acceptable
only as a preregistered replacement research objective with its different
semantics documented in the project history and graph labels. Option 3 requires
explicit approval to weaken the original success criterion and must be reported
as a scope change. What is not acceptable is discovering the floor after the
experiment and reinterpreting the criterion then.

For space and latency, apply the one-sided feasibility test above rather than a
blanket interior-headroom requirement.

## 15. Worked examples of repaired behavior

### 15.1 Observed overloaded L1

Initial state:

```text
L1 target = 16 MiB
L1 actual = 219 MiB
L1 score = 13.7
policy action = compact
```

Current behavior schedules at most one native L1 compaction and consumes the
lease. Repaired behavior enters `DUE_OPEN`. Suppose native scheduling performs
four-file trivial moves of about 2 MiB each. It may repeatedly move work:

```text
219 -> 217 -> 215 -> ... -> approximately 16 MiB
```

without a new model response for every move. Every move's files are selected by
RocksDB. When current score drops below 1, the gate stops admitting due work.

### 15.2 Simultaneous L0 and L1 pressure

Initial state:

```text
L0 score = 3.8, forced eligible
L1 score = 13.7, policy compact
```

Both levels are open. Native score order selects L1 first. After sufficient L1
service, scores might become:

```text
L0 score = 3.8
L1 score = 2.9
```

Native order then selects L0. This reduces the next L0 compaction's overlap
relative to selecting L0 repeatedly while L1 remains at 13.7.

### 15.3 Proactive compaction

Initial state:

```text
L2 score = 0.75
policy action = compact
```

The bridge installs `OPTIONAL_OPEN` with one token. RocksDB selects one native
L2 compaction. On success the token is consumed. A second L2 compaction requires
a later policy response, preventing an optional action from draining the whole
level.

### 15.4 Deferred level loses no accounting

Initial state:

```text
L1 score = 1.2
due age = 400 ms
policy says compact
another authorized level wins this scheduler call
```

L1 remains open and its due age continues. It is not reset to zero. On the next
scheduler call, it remains eligible. If it stays unresolved past `H_1`, its
reason becomes forced budget/safety even if later policy responses say defer.

### 15.5 Server disconnect

If the last response says defer and the server disconnects, the watchdog must
not preserve that defer indefinitely. Once response age exceeds `H_stale`, the
controller enters explicit leveled fallback and ordinary RocksDB due work
continues. Those intervals are excluded from replay.

This example is only correct once section 7.8 is in place. Without it, the same
code path is reached whenever the policy defers every level with a perfectly
healthy server, because deferral suppresses the observations that would
otherwise refresh the response age. The controller cannot distinguish "the
server is gone" from "the policy is doing its job", and resolves the ambiguity
by discarding the policy. See section 5.3.

## 16. Approaches explicitly rejected

### 16.1 Merely reduce `RL_DECISION_INTERVAL_MS`

This increases CPU/socket traffic but does not remove coupling between inference
rate and storage service. Scheduler snapshots were already sparse; nominal
50 ms did not produce 20 usable decisions/s. It also fails to fix lost deferral
accounting.

Note the scope of this rejection. What is rejected is *shrinking the configured
interval as a remedy*, which cannot work because the interval was never the
binding constraint: the worker was already ticking 769 times and being starved
of observations on 694 of them. Repairing the observation supply itself, per
section 7.8, is required and is not covered by this rejection. Shrinking the
interval without 7.8 would simply increase the skipped-tick count.

### 16.2 Merely reduce `RL_MAX_DEFER_STEPS`

Decision counts still have variable wall-clock meaning, and a losing compact
request can still reset the count. This may hide the failure at 1M without
making the control invariant correct.

### 16.3 Let every compact response call the ordinary parent picker

The parent may select a different, deferred level. That breaks level-scoped
authority and makes action attribution false.

### 16.4 Give every level a one-use lease simultaneously

This removes global winner loss but still rate-limits every level to one job per
decision and does not match held trigger semantics.

### 16.5 Restore candidate-aware actions

Candidate selection is outside project scope and does not solve the actuator
rate mismatch. RocksDB already has the correct file-selection machinery.

### 16.6 Tune the reward before oracle parity

No reward can learn around a bridge whose service capacity is artificially
below workload arrival rate. Model changes before parity would confound policy
quality with actuator correctness.

### 16.7 Rely on held gates alone to fix the decision rate

It is tempting to treat section 3.8 as subsumed by the gate repair, on the
argument that an open gate lets RocksDB self-schedule and therefore no longer
needs a high observation rate. That argument is correct for the open case and
false for the closed case, which is the one that matters. Deferral is the
policy's only distinguishing action; a controller that cannot observe while
deferring cannot learn when to stop deferring, cannot bound its own deferral in
real time, and will have its deferral terminated by a staleness watchdog rather
than by policy or safety. Held gates reduce the exposure; they do not close the
loop. Section 7.8 is required.

### 16.8 Keep the safety clock inside `NeedsCompaction()`

Placing due age and integrated pressure updates in the synchronous scheduler
path is attractive because that path has exact current scores. It is
insufficient as the sole source, because that path does not run when the
controller defers everything, which is exactly the state the safety clock
exists to bound. The synchronous path remains the preferred *refinement*; it
cannot be the *guarantee*. See section 7.3 and 7.8.2b.

## 17. Risks and mitigations

| Risk | Consequence | Mitigation |
| --- | --- | --- |
| Held gate over-schedules optional work | WAF regression | Optional actions get exactly one token; due catch-up requires current score >=1 |
| Old response remains active | Stale policy controls new workload phase | Atomic generations plus watchdog-to-leveled fallback |
| Multiple open levels create authority ambiguity | Wrong level attribution | Independent per-level permits; native score ordering restricted to eligible set |
| Safety repeatedly prioritizes L0 | Deep-level starvation and huge overlaps | Safety opens gates; native current score orders them except true hard-stop legality |
| One decision owns many jobs | Reward magnitude changes | Aggregate physical cost over interval; verify parity before reward retuning |
| In-progress jobs distort score | Premature gate close or overqueue | Reuse RocksDB's post-registration score computation and native conflict checks |
| Baseline envelope is workload-specific | Unsafe generalization | Export workload/config identity with envelope; conservative fallback on mismatch |
| Final drain masks policy debt | Misleading interpretation | Report workload phase and drain phase separately while retaining total authoritative cost |
| Eligible permit never acted on | Work silently stranded on an idle plant, for policy `DUE_OPEN` and optional tokens as well as safety | Wake on every closed-to-eligible edge (7.8.2c) through `EnqueuePendingCompaction` + `MaybeScheduleFlushOrCompaction`; tests 19 and 25 assert a scheduling attempt, not a permit bit |
| Control path introduces lock inversion | Deadlock between worker, coordinator queue, and DB mutex | Worker enqueue never takes DB mutex; executor releases queue lock before DB mutex; DB mutex -> `snap_mu_` -> `permit_mu_`; test 20 |
| Shutdown races an in-flight control request | Teardown waits on a request waiting for the DB mutex | Bulk registration invalidation under DB mutex; picker/coordinator stop outside it; deterministic `SyncPoint` test 26 |
| Individual CF drop races a queued request | Use-after-free or DB-wide deadlock while coordinator remains live | Separate registration generation; invalidate/detach under DB mutex, stop worker outside, resolve only under mutex; tests 22 and 29 |
| Rate limiter discards the last structural change | Agent runs forever on a stale tree | Coordinator-owned keyed timer; dirty/source/built generations; rebuild latest state after deadline; conservative eligibility on deadline miss; test 24 |
| Caching `RLStateV2` wholesale | Frozen execution outcomes corrupt training attribution | Split structural snapshot from per-observation overlay (7.8.2a) |
| A production score-change path bypasses pressure accounting | Due age and pressure silently diverge from RocksDB state | One audited active-score recomputation seam, shared observer, call-site-generation test 30 |
| Trapezoidal integration of a stepwise score | Pressure misattributed at every score transition, in both directions | Event-driven zero-order hold; worker extends last exact event; test 27 |
| `epsilon` left undefined | `H_i + epsilon` is not testable | Decompose into score-event publication, worker tick, control queue, and admission; export and bound p95/max; test 28 |
| Strict-improvement metric has no baseline headroom | Experiment designed to fail regardless of controller quality | Direction-aware feasibility gate in section 14.5 before Phase 5 |
| Baseline episodes uncollectable | Phase 1c cannot calibrate, since regular runs never construct the RL picker | Shared `CompactionPressureObserver` attached for both pickers; no timer polling |
| Per-tick `BuildSnapshot` under the DB mutex | Foreground latency regression at 50M/T2, since it scans level metadata and computes whole-level overlap | Cached immutable snapshot rebuilt only on structural change; worker reads, never rebuilds; test 23 |
| Version-install publication hook fires on a hot path | Flush/compaction completion slowdown | Rebuild immediately only outside the minimum gap; otherwise mark dirty and coalesce one timed latest-generation refresh—never discard the change |
| Safety envelopes shipped without a baseline | Bootstrap caps presented as calibrated limits | Phase 1c restores the tuned grid and SLO manifest; levels below the episode threshold are marked un-calibrated in exported metadata |
| `Q99` estimated from too few episodes | Envelope with no statistical meaning | `N >= 299` merely for a 95% chance of one tail sample; use an order-statistic tolerance bound, a lower quantile, or the bootstrap cap, and record which |
| Worker-side forced-open acts on a stale snapshot | Unnecessary compaction | `NeedsCompaction()` re-validates against current `VersionStorageInfo` before admitting; disagreements recorded as snapshot staleness |
| Blocked-open backoff hides genuine starvation | Level silently unserved | Monotonic deadline plus coordinator retry wake; pressure/age still accrue; test 16 and per-level telemetry |
| Manifest SLOs are logged but do not affect control | Latency/space protection is illusory | Explicit trigger-only mask, minimum samples, three-window hysteresis, conservative mismatch fallback; test 31 |
| Watchdog bound derived from configured rather than achieved spacing | Spurious fallback against a healthy server | Derive from a live median-spacing estimate; treat nonzero expiries with a reachable server as a defect |

## 18. Files expected to change during implementation

| File | Planned responsibility |
| --- | --- |
| `lib/rocksdb/db/compaction/compaction_picker_rl.h` | Permit frame, immutable pressure-state view, timed blocked-backoff/retry state, cached structural snapshot and three ages/generations, asynchronous control handle, diagnostics |
| `lib/rocksdb/db/compaction/compaction_picker_rl.cc` | Held-gate installation, current-score mode classification, native-score arbitration, watchdog, safety; cached-snapshot read; worker-side deadline evaluation; edge-triggered request enqueue |
| New `lib/rocksdb/db/compaction/rl_control_coordinator.{h,cc}` (or equivalent DB-owned component) | Refcounted producer handle, per-CF registrations, nonblocking request/coalescing queue, monotonic timer queue, scheduling retries, deferred latest-generation snapshot refresh, executor validation, shutdown/drain |
| `lib/rocksdb/db/column_family.{h,cc}` | Structural-change notification and immutable-snapshot publication. `InstallSuperVersion` already owns the picker seam. Store/assert registration generation and stopped state; do not inject a `DBImpl` pointer at picker construction |
| `lib/rocksdb/db/db_impl/db_impl.{h,cc}`, `db_impl_compaction_flush.cc`, open/create/drop paths | Own/start/stop the coordinator; attach every recovered and dynamically created CF after it becomes visible; invalidate after a committed drop; perform two-phase per-CF and shutdown stop; executor resolves IDs then calls `EnqueuePendingCompaction(cfd)` and `MaybeScheduleFlushOrCompaction()` under the DB mutex |
| `lib/rocksdb/db/version_set.{h,cc}` and all active score-recompute call sites | Central `RecomputeActiveCompactionScoreAndObserve` seam and `CompactionPressureObserver`; event-driven zero-order-hold due/pressure state shared by regular and RL pickers. Audit direct production `ComputeCompactionScore()` calls |
| `scripts/` baseline tooling | Phase 1c: restore the tuned leveled grid, preregistered selection, and the `baseline_slo.json` generator, both recorded as not currently available at `PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md:1184-1185` |
| `lib/rocksdb/db/compaction/compaction_picker_level.{h,cc}` | Add due-level eligibility mask to the native builder; retain forced-source optional selection; no exact-file API |
| `lib/rocksdb/db/compaction/rl_compaction_telemetry.{h,cc}` | One-to-many decision attribution and per-level gate metrics |
| `lib/rocksdb/db/compaction/rl_compaction_client.{h,cc}` | Response-frame validation only if required; response remains actions-only |
| `lib/rocksdb/db/compaction/compaction_picker_test.cc` | State-machine, parity, service-rate, and authority tests |
| `rl_agent/multilevel.py` | New execution/pressure observations after C++ parity |
| `rl_agent/config.py` | State fields and calibrated time/pressure limits |
| `rl_agent/tests/` | Protocol, masking, attribution, terminal, and fallback tests |
| `scripts/dbbench_pipeline/03_run_experiments.sh` | Oracle/prior-only modes and phase-separated metadata |
| `scripts/dbbench_pipeline/04_generate_graphs.py` | Debt/score/gate/drain diagnostics if graphing is extended |
| `PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md` | Record diagnosis, approved design, implementation outcome, and retractions |

## 19. Review decisions required before implementation

The reviewer should explicitly approve or revise these points:

1. **Held-gate semantics:** due compact is a control-interval gate, optional
   compact is one-shot.
2. **Arbitration:** an allowed-level mask inside the native leveled builder, so
   current score ordering and L0/base-level rules apply only to independently
   open levels.
3. **Due reset:** only score below 1 ends a due episode.
4. **Safety units:** wall-clock due age plus integrated excess pressure, not
   decision counts.
5. **Fallback:** stale/unavailable policy becomes full ordinary leveled
   eligibility and invalid replay.
6. **Initial L0 posture:** no learned L0 deferral during bridge validation.
7. **Bootstrap caps:** whether to use L1+ score cap 1.25 until baseline-derived
   envelopes exist.
8. **Oracle parity tolerances:** 5% engineering parity for amplification/debt,
   followed by the stricter final 2% research limits.
9. **Attribution:** one decision-level gate may own multiple RocksDB-selected
   compactions.
10. **Experiment gate:** no 10M--50M sweep until deterministic oracle and 1M/T2
    safety tests pass.
11. **Observation decoupling as a prerequisite:** whether section 7.8(a) lands
    as its own Phase 1a with its own gate, as proposed, or is merged into the
    gate rewrite. The plan recommends separating them, because merging makes a
    Phase 1 failure ambiguous between trigger latency and gate semantics.
12. **Control coordinator:** approval of the asynchronous DB-owned coordinator,
    per-CF registration generation and two-phase drop/shutdown lifecycle in
    section 7.8.2c; scheduling requests are armed on every closed-to-eligible
    edge and backoff-expiry retry, deduplicated by stable eligibility interval
    rather than response generation, and enabled in Phase 1b. Raw `DBImpl`
    pointers, synchronous callbacks, and keep-polling are rejected.
13. **Safety-clock ownership:** confirmation that the shared score observer
    captures exact zero-order-held transitions and the worker independently
    evaluates the derived state against wall-clock deadlines, with
    `NeedsCompaction()` demoted to synchronous admission validation.
14. **Blocked-open backoff:** whether bootstrap `K_blocked = 4` and monotonic
    `W_blocked = 100 ms` are acceptable, with explicit retry-generation wake on
    expiry and uninterrupted safety-budget accrual.
15. **Baseline restoration scope:** whether Phase 1c restores the full tuned
    grid and `baseline_slo.json` generator, or whether Phase 2 ships explicitly
    un-calibrated bootstrap caps and defers calibration to a later milestone.
    The plan recommends the former, because section 7.4 has no meaning without
    it.
16. **Quantile policy:** which of the three options in section 7.4 is adopted
    for levels with few due episodes, given that `N >= 299` is needed merely
    for a 95 percent chance of one observation past the true 99th percentile.
17. **Phase 1a expected result:** confirmation that a substantial WAF
    improvement from observation repair alone is the predicted outcome and
    supports the diagnosis, and that the falsifying result is stated on
    realized per-level service rather than on decision rate.
18. **Pressure integration:** approval of the single audited
    `RecomputeActiveCompactionScoreAndObserve` seam, zero-order hold at each
    accepted active-score transition, and worker extension from the last exact
    event rather than reconstruction from coalesced structural snapshots.
19. **Scan-amplification objective:** which of the three options in section
    14.5 is adopted, given that the baseline sits at the metric's floor of
    1.000 and the current criterion is unsatisfiable. This must be decided
    before Phase 5 runs, not after.
20. **Baseline instrumentation placement:** approval that episode collection is
    part of the same generic `CompactionPressureObserver` used by both pickers,
    rather than RL-picker state or external polling.
21. **SLO breach mapping:** approval of the Phase 2 trigger-only mapping,
    minimum-sample/three-window hysteresis, and all-due tuned fallback when a
    global breach cannot be attributed safely to one level.
22. **Metric feasibility:** approval that strict-improvement metrics require
    directional headroom, while one-sided non-regression limits require a
    feasible correctly directed bound rather than interior headroom.

## 20. Definition of done

This repair is complete only when all of the following are true:

- the bridge implements held trigger gates rather than one-compaction pulses;
- a single due compact decision demonstrably permits multiple native
  compactions;
- optional work remains one-shot;
- observation supply is independent of gate state, so deferring does not blind
  the controller;
- deferral safety uses real time and integrated pressure, and advances from a
  source that ticks regardless of plant activity;
- a due level reaches its forced-open deadline on an idle plant **and that
  deadline produces a real scheduling attempt**, verified by section 12.1
  test 19 rather than by inspecting a permit bit;
- every newly eligible gate, not only a forced-open one, causes a real
  scheduling attempt;
- picker workers enqueue asynchronously and never wait for the DB mutex; each CF
  has a registration generation and can be dropped safely while the DB and
  other CFs remain live; whole-DB shutdown also passes its forced interleaving;
- a structural change is never permanently lost to rate limiting: a DB-owned
  timed executor makes built generation converge after idleness;
- pressure integration is event-driven zero-order hold across every active-score
  recomputation, and `epsilon` is measured rather than assumed;
- every strict-improvement metric has demonstrated directional baseline
  headroom, and every non-regression limit has passed its one-sided feasibility
  check;
- the worker does not rebuild structural snapshots per tick;
- safety envelopes are either calibrated from a restored tuned baseline or
  explicitly labelled un-calibrated;
- blocked-open levels back off on monotonic deadlines, retry on expiry without
  an external plant event, and lose neither permits nor accruing safety budgets;
- the manifest-driven latency/space mask has minimum samples, three-window
  hysteresis, explicit trigger-only actions, and conservative mismatch fallback;
- native-score arbitration cannot erase a losing level's due state;
- protocol v2 remains file-free;
- SST inputs match RocksDB's native picker;
- unavailable-server fallback cannot stop compaction;
- final drain is separately observable;
- the deterministic leveled oracle passes parity;
- the 1M/T2 failure cannot be reproduced under the oracle;
- only then are prior-only, learned, and large-scale experiments allowed.
