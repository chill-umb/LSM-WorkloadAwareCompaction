# Preregistration and Execution Record

**Status:** the dated companion to `docs/PATHWAYS.md`.

`docs/PATHWAYS.md` holds the theory, the per-pathway specification and the
done/not-done status, and nothing else. **This document holds what was decided,
when, and what was predicted before each run**, plus the gate verdicts as
measured. Split out on 2026-09-20 because the two kinds of content have
different lifetimes: the theory is amended when the theory changes, whereas a
dated decision is never edited after the run it governs.

The narrative of what happened is `PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md`
Section 14. This document is the *register*: the short, checkable statement of
what was committed to in advance. Where the two overlap, the history is the
fuller account and this is the one with the date that matters.

**The rule this document exists to enforce.** A criterion is never reworded,
relaxed or re-scored after its outcome has been seen. C-2 and E-1 are both
recorded failed for exactly that reason, and both had a defensible
reinterpretation available at the time. An entry added here after the run it
governs is worthless, and worse than worthless if it reads as though it was not.

---

## 1. Decision register

### D-1, 2026-09-20 — the programme's workload becomes UDB `Assoc` (Pathway B1)

**Recorded before any run of the re-executed programme, and specifically before
the Gate 1 re-measurement.**

**Decision.** Every arm of every gate — baseline sweep, guard calibration and
holdout, `prior_only`, `rl`, `unconstrained_rl` — runs on the skewed workload of
Pathway B1, the UDB `Assoc` column family of Cao et al., FAST 2020. The uniform
family that produced every result up to 2026-09-19 is retained as a named
control family (`WORKLOAD_SKEW=0`), not as the measurement workload.

**Why, stated independently of any result.** Two reasons, both true before the
Gate 1 outcome was known:

1. A hull measured on one workload is not a valid comparator for a policy
   measured on another. PATHWAYS already states the knob form of this rule —
   *"comparing a policy against a static class that lacks a knob the policy has,
   or has one the policy lacks, is invalid in either direction"* — and Gate 3c
   already concedes it for capacity by re-measuring Hull$_s$. Nobody had written
   down the workload form, and the published plan ran Gate 1 on `uniform` and
   Gate 4 on `skew`, which is that same invalid comparison.
2. Uniform random keys are not a workload anyone runs. The published fit exists
   because real key access is skewed.

**What this is not.** It is not a response to C-3 failing. The C-3 failure is
recorded and stands; it is not re-scored, and the uniform result is not
withdrawn. What changes is which workload the *next* programme measures.

**Predictions, recorded in advance.**

1. **The uniform family will not reach write parity, and this is analytic, not
   empirical.** Theorem B.1 caps the achievable relative reduction in $W - 1$ at
   the resident-garbage fraction, and Gate 0 measured that ceiling at
   **79.5% / 39.9% / 36.3%** at $T = 2/6/10$. On a near-garbage-free workload
   compaction has nothing stale to drop, so compacting more can only add write
   bytes. We predict the policy fails on `uniform` and expect to report that
   failure as a confirmed prediction rather than as a result to explain.
2. **Skew will raise resident garbage.** B-1's threshold is $\ge$ 10pp against
   the uniform control.
3. **Garbage does not favour the policy over the comparator.** Hull$_0$ is
   re-measured on the same workload and benefits from the same garbage. The
   change makes the contest decidable; it does not tilt it.

**Falsification.** If B-1 fails — skew does not raise $g$ by 10pp — the workload
change bought nothing and must be reported as such, with the uniform result
standing unqualified.

**Deviations from the published fit, and why.**

| Parameter | Published | Used | Reason |
| --- | --- | --- | --- |
| `value_theta` | 0 (mean ~34 B) | **925.5** (mean 960.4) | Holds the mean record size at the project's 960 B so the level ladder, populated depth and the $T$ sweep stay comparable with the geometry every other constant is calibrated for. Report as *"Assoc key distribution and operation mix at the project's record size"*, **never** as the published value distribution. |
| `mix_max_value_size` | 1024 (db_bench default) | **65536** | db_bench applies it as `val_size % value_max` (`db_bench_tool.cc:7316`), a wraparound rather than a clamp. At 1024, 6.85% of draws wrap to as little as one byte and the measured mean falls to 890.2. |

`value_k`, `value_sigma`, `iter_theta`, `iter_k`, `iter_sigma`,
`keyrange_dist_{a,b,c,d}` and `key_dist_{a,b}` take db_bench's own flag
defaults for this fit. The paper's appendix, the RocksDB wiki and its §7.4
disagree at the second decimal; this picks the tool's values rather than
silently choosing among them, and says so.

**Scope held open.** `keyrange_num` = 30, swept $\in \{5, 30, 100\}$ as the
skew-intensity axis. Delete rate is **0** — B3 is not implemented, and PATHWAYS
labels a nonzero rate synthetic (P0-10) since `Assoc` models no deletes, so a
nonzero rate would weaken the realism claim it is meant to support. Phases (B2)
are not implemented: they break guard calibration, whose tolerance bounds
estimate the tail of one stationary distribution and whose rolling windows
cannot distinguish a phase change from a breach.

**Implementation.** `WORKLOAD_SKEW`, `KEYRANGE_*`, `KEY_DIST_*`, `VALUE_*` and
`ITER_*` in `scripts/dbbench_pipeline/config.sh`; flags and the conditional
`:skew<keyrange_num>-<value_theta>` fingerprint segment in
`03_run_experiments.sh`; parser in lockstep in `06_select_baseline_slo.py`. The
full fit is recorded per arm in `metadata.env`, since only two of its parameters
reach the fingerprint.

### D-2, 2026-09-20 — the guard's budget force is memoryless; the guard is kept and scored on the conditional rate

**Recorded after E-1's verdict was seen and before any run of the re-executed
programme.** E-1 stays recorded failed. Nothing here re-scores it.

**What was found, by replaying the guard offline.** The force condition in
`ClassifyWorkerSafety` was re-implemented in Python from the per-frame,
per-level state in `io.jsonl` and the manifest's limits, and run over the
thirty `unconstrained_prior_only` arms of 2026-09-19 (the same manifest the
holdout used). At $T=2$ the replay agrees with `safety_shadow.jsonl` on
99.8% of frames, so the attribution below is the mechanism, not a model of it.

| $T=2$, per run (10 runs) | fraction of ready frames |
| --- | ---: |
| override, shadow log | 0.321–0.351 |
| an instantaneous term true (age, pressure, score, debt) | 0.153–0.201 |
| forced by **retention alone**, no term true | **0.150–0.160** |
| of the instantaneous frames, the global debt term | ≈ 0.04 |

The retention is `compaction_picker_rl.cc` `retain = previous_force_reason ==
kBudget || kEmergency || …`: once any per-level term trips, the force is held
until the level is no longer due. `kBudget` is the default reason for every
age, pressure, score and debt trip. In repeat-09, L2 exceeded its score limit
on **one frame** — 13.104 against 13.092, 0.1% over — at ready-frame 4, and
the latch held L2 forced for its entire 803-frame due run, 40.2 s. L3 tripped
on three frames (12.584 against 12.533) and latched identically. That is the
"single 802-frame event" of the 2026-09-14 verdict. The enforcement path
latches the same way (`InstallPolicyFrame` `safety_held`; `ApplyWorkerSafety`
releases a budget force only on `!item.due`), so with enforcement on a learned
policy would lose authority over L2–L4 for the whole backlog from a
hair-trigger. The frame-simulated calibration models no memory at all, which
is why it predicted 0.99% for a mechanism that produces 33%.

At $T=6$ and $T=10$ the replay over-predicts the shadow (0.22 against 0.15,
0.30 against 0.20) because about half the overrides there are `kSLO`
(`reason_mask` bit 6: 142 of 279, 154 of 353), a global term with no
per-frame record that releases on its own condition; the replay cannot model
it. The direction is the same.

**The residual after removing the latch, measured by split-half refit on real
frames.** Limits were refitted on five seeds at the 1% joint instantaneous
target and scored on the other five, per run:

| cell | fit → test | held-out, no latch | held-out, with latch |
| --- | --- | --- | --- |
| $T=2$ | 1–5 → 6–10 | 0.006 0.004 0.021 0.003 0.005 | 0.34 0.33 0.33 0.33 0.32 |
| $T=2$ | 6–10 → 1–5 | 0.133 0.141 0.010 0.137 0.117 | 0.33 0.34 0.32 0.35 0.34 |
| $T=6$ | both | means 0.017, 0.013 | 0.22, 0.23 |
| $T=10$ | both | means 0.005, 0.046 | 0.27, 0.31 |

With the latch, the limits do not matter. Without it, a per-frame 1%
threshold is a top order statistic of the fitting seeds and transfers to a
new seed only when that seed's backlog is no longer than the fitting seeds'
(the 6–10 → 1–5 split fails on four of five). No code change removes that;
it is the property of the marginal statistic that Corollary E.2 already
names.

**Decisions.**

1. **Mechanism.** A budget or emergency force lasts exactly as long as its
   condition holds. `retain` drops `kBudget` and `kEmergency`;
   `ApplyWorkerSafety` releases a budget/emergency permit whenever the frame's
   classification no longer forces it. Due age and pressure only grow while
   a level is due, so those forces persist on their own; only score, debt
   and `l0_slowdown` forces can now release. Manifest, SLO and
   stale-structure forces keep their own release conditions.
2. **Instrument.** `safety_shadow.jsonl` moves to schema 3 and logs
   `slo_force_due`, `global_debt_breach`, `l0_slowdown` and
   `pending_debt_ratio` per frame; the holdout validator reports override
   frames per global term. `frame_simulated_limits` reports a
   leave-one-run-out prediction next to the in-sample one; the in-sample
   number is not to be quoted as a holdout expectation.
3. **Criterion for the re-run, fixed now.** The guard is **kept**. The
   deciding guard criterion for the `Assoc` programme is **E-5: the override
   rate conditioned on the policy having selected defer, $\le$ 1% per cell on
   the guard holdout and on every learned arm**, which is the statistic
   Corollary E.2 asks for and which passed by more than an order of magnitude
   on 2026-09-19. E-1's marginal rate is measured and reported per cell
   without a pass/fail, together with the schema-3 term decomposition and
   the leave-one-out prediction it is compared against. E-1's 1% is not
   amended; it is retired as a decider because it thresholds a maximum
   statistic of one backlog event, established above.

**Predictions, in advance.** With the latch removed and limits recalibrated
on the `Assoc` baseline, the marginal rate on the holdout falls below 0.20 in
every cell and the leave-one-out prediction brackets it within a factor of
three; the conditional rate stays below 0.01. If the marginal rate on the
holdout exceeds the leave-one-out mean by more than 3×, the calibration's
transfer claim is wrong and must be reported as such.

**Not done, and why.** The global debt limit is still an episode-maximum
tolerance bound (the unit error that was corrected for age on 2026-09-14),
because the baseline sweep that feeds the frame replay records no per-frame
debt; it adds about 4pp at $T=2$ and nothing at $T=6$/$T=10$. It becomes
calibratable once the sweep arms log it, which is a separate instrumentation
change and is not taken here.

**Evidence.** `deprecated/pre-gate2-2026-09-20/results/prior_shadow/results/10M/T{2,6,10}/repeat-*/unconstrained_prior_only/{io,safety_shadow}.jsonl`
against `deprecated/pre-gate2-2026-09-20/baseline_slo_frame5_guard/balanced-v1/10M/T*/baseline_slo.json`.

### D-3, 2026-09-21 — the space denominator becomes the measured garbage-free size

**Recorded after the `Assoc` Hull-0 sweep was measured and the per-$T$ hulls
extracted, and before stage 06, the guard protocol and every learned arm.**
This document's rule is that a criterion is never reworded after its outcome
has been seen, so the timing is stated first and plainly. **C-1 and C-2 do not
use $S$** — the hull is built on $(W, R)$ and the C-2 width test runs over
those two axes only (`frontier_analysis.py:22`) — and **no criterion that does
use $S$ has been scored on `Assoc`**: the P1c-24 dominator filter inside
C-3/C-4/C-6, C-5's $s_{\max}$, and stage 06's comparator selection are all
unrun. The amendment therefore precedes every verdict it can influence. It does
**not** re-score the 2026-09-12 or 2026-09-19 Gate 1 verdicts, which stand as
history of the binary and workload that produced them.

**Decision.** Space amplification is settled physical SST bytes divided by
`sst_bytes_after_full_compaction`, the garbage-free size the reference
compaction measures. The superseded denominator,
`rocksdb.estimate-live-data-size`, is retained per run as
`space_amplification_estimate`. No run is re-executed: both terms were
already recorded for every arm.

**The contract file is deliberately NOT edited, and this is load-bearing.**
`constraints.space` freezes the metric, the test and the rung ladder; it has
never named a denominator, which lives in `04_generate_graphs.py`. And the
contract must not be edited here even to add one:
`research_objective.py:27` hashes the raw contract bytes,
`03_run_experiments.sh:75` stamps that hash into `metadata.env` and into the
`experiment_fingerprint` as `:objective<hash>`, and
`frontier_analysis.collect_grid` raises when a run's stamped hash differs from
the contract's current hash. **Editing the file therefore changes the
fingerprint of every future run and makes the evaluator refuse every past
one** — the same consequence a rebuild has through `dbbench_sha256`, and by
the same mechanism. An edit attempted on 2026-09-21 during the C-2 top-up was
reverted byte-identical before any arm was stamped with it; all arms carry
`5857ad35…`. If the definition is ever to be written into the contract, it
must be done when re-running the affected sweep is acceptable, or the identity
check must first be taught an explicit list of superseded-but-poolable hashes.
This entry is the record of the decision, and `docs/PREREGISTRATION.md` is
hashed into nothing.

**Why, stated as a defect in the instrument rather than as a result.**
`VersionStorageInfo::EstimateLiveDataSize` (`db/version_set.cc:5401`) sums a
maximal set of files with no range overlap in a deeper level. A file holding
live data that shadows the bottom level is dropped whole, so the estimate
excludes garbage — which is its job — **and a large share of the live data,
which is not**. The function's own comment states the failure mode: *"The less
compacted, the more optimistic (smaller) this estimate is."* The denominator is
therefore a function of tree depth, and the policy under test changes tree
depth (history §10.7 Finding 3), so the error biases both the acceptance
constraint and the P1c-22 reward hinge against the behaviour being measured.
Both denominators are physical SST bytes, so the units are unchanged; only the
estimate is replaced by a measurement.

**Measured over the 108 `Assoc` Hull-0 runs, binary `9b9321b1…`.**

| $T$ | garbage-free size | estimate | live data discarded | $S$ reported | $S$ measured |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 2.816 GiB | 1.494–1.668 GiB | **46%** | 1.80–2.05 | 1.066–1.090 |
| 6 | 2.816 GiB | 2.277–2.506 GiB | 14% | 1.17–1.30 | 1.039–1.050 |
| 10 | 2.816 GiB | 2.561–2.627 GiB | 8% | 1.11–1.14 | 1.031–1.043 |

The garbage-free size is **3,023,603,980 to 3,023,786,278 bytes across all 108
runs, a spread of 0.0060%**, as it must be: `filluniquerandom` writes 2.9M
unique keys and `mixgraph` only overwrites them. The estimate spans 76% over
those same runs. Data that is provably identical cannot have a live size that
moves by 76%.

**The independent check that settles it.** $S_{\text{flow}} =$ user bytes
written $/$ live bytes is a property of the workload, not of the tree, so it
must not vary with the size ratio. At the standard configuration it does not —
under the measured denominator, and only under it:

| denominator | $S_{\text{flow}}$ at $T=2$ | $T=6$ | $T=10$ |
| --- | ---: | ---: | ---: |
| estimate | 2.572 | 1.615 | 1.486 |
| measured | **1.386** | **1.386** | **1.386** |

Identical to three decimals across three ratios. The ratio-dependence under the
estimate is an artifact of depth, not a property of the workload.

**Predictions, recorded in advance.**

1. **Stage 06's space filter stops binding.** Under the measured denominator
   the four base-16 MiB configurations lie within 0.6–0.8% of one another at
   every ratio, so the "discard configurations more than 2% above the minimum"
   step admits all four and the comparator is decided by the runtime
   tie-break. Under the estimate it admits 2 of 4 at $T=2$, 1 of 4 at $T=6$
   and 3 of 4 at $T=10$, and the minimum-space winner differs at $T=2$
   (trigger 2 → 8) and $T=6$ (trigger 2 → 4).
2. **Theorem B.1's ceiling falls at $T=2$.** $g_{\text{flow}}$ moves from
   0.611 to 0.278 there, and the ceiling recomputed at measured
   $S_{\text{flow}}$ and $L$ falls with it. The Gate 0 ceilings of
   79.5%/39.9%/36.3% were computed on the estimate and are expected to be
   overstated, most severely at $T=2$.
3. **B-1's direction is decided by the denominator, and must be scored on the
   measured one.** Against the recorded uniform figures, resident garbage
   appears to *fall* under the estimate and to *rise* under the measurement.
   B-1 remains a Gate 4 criterion and is not scored here; when it is, it is
   scored against a `WORKLOAD_SKEW=0` control re-run on this binary, not
   against the deprecated uniform numbers.

**Falsification.** If the garbage-free size is not stable within 0.5% across
the arms of a future cell, or if $S_{\text{flow}}$ under the measured
denominator varies with $T$ by more than 2% at a fixed configuration, then the
reference compaction is not measuring what this entry claims and the amendment
must be withdrawn and reported as withdrawn.

**Not taken here, and why.** `metric_definitions_version` stays
`trigger-v2-logical-v3`. `rl_safety_manifest.cc` compares that string for
equality, so bumping it needs a C++ edit and a rebuild; a rebuild changes
`dbbench_sha256`, which `frontier_analysis.py:196` refuses to pool across, and
would void the 108-run Hull-0 sweep. There is no pooling hazard to prevent:
every pre-`Assoc` run is already deprecated, and all 108 `Assoc` runs recompute
under the amended definition from artifacts they already hold. And the live
per-frame signal in `compaction_picker_rl.cc:345` still calls
`EstimateLiveDataSize`, so the reward hinge and the guard still see the
estimate; replacing that is a separate decision, recorded under its own date,
and is only free if taken outside C++.

**Implementation.** No contract file, binary or run is touched.
`04_generate_graphs.amplification_metrics`
(`space_amplification` on the measured denominator,
`space_amplification_estimate` on the old one, the estimate as the fallback for
an arm with no `sizes.env`); `14_gate0_reanalysis.flow` for $S_{\text{flow}}$
and the B.1 ceiling; the manifest metric description in
`06_select_baseline_slo.py`. `frontier_analysis.py` inherits the change through
`collect_arm` and is not edited.

**Evidence.** `results/baseline_sweep/*/10M/T*/repeat-*/regular/{sizes.env,run.log}`,
108 runs, fingerprint `assoc-v1:…:binary9b9321b1…`.

### D-4, 2026-09-22 — the prior compacts a deep level only when RocksDB scores it due

**Recorded after the `Assoc` Hull-0 sweep and the guard protocol, and before
any policy arm has run on `Assoc`.** It follows a structural audit of
`analytic_advantage` against PATHWAYS, prompted by the 2026-09-19 uniform C-3
failure and by A-0's attribution of the prior's whole write excess to
eagerness. It does not re-score C-3, and there is no `Assoc` measurement of
the prior to fit to: every number below comes from the code evaluated on the
pipeline's geometry and from the `regular` sweep.

**What was found.** For levels $\ge 1$ the prior's benefit side was
`stall_urgency = fullness²` with `fullness = clamp(bytes / target)`. Below
score 1 a deep level stalls nothing — the only deep-level stall path is
`estimated_compaction_needed_bytes` against a 64 GiB soft limit, on a tree of
about 3 GB — so the term was a "due soon" signal. Its cost side,
`work_now = (1 + overlap/bytes) / size_ratio`, is fed `overlap_bytes` computed
over the whole level's key span (`NextLevelOverlapBytes`), so overlap
$\approx T \cdot$ bytes and the term clamps to 1.0 whenever the level below is
full: a constant, not a cost. Evaluated over the pipeline geometry (2 MiB
flush, 512 KiB SST, 16 MiB base) the prior authorises a deep compaction at
**0.82–0.92 of target** ($T$ = 10/6/2), against native's measured
φ-at-release p50 of **1.01–1.24** on the `Assoc` comparators. P1c-23's depth
charge ($-0.6 \cdot$ exposure when the output would populate an empty level)
loses to the urgency term in every state tried (advantage +0.12 to +0.78), so
the prior grows depth on demand. And it never defers due work: at score
$\ge 1$ the advantage is a flat +0.2 or more.

Under the constrained objective this is incoherent at every deep level. By A4
a deep level is one sorted run whatever its size, so compacting it early buys
nothing on $R$; by Theorem B.1 it forfeits the overwrites a later merge would
have dropped — and `Assoc` has them: $\eta$ = 0.84 / 0.86 / 0.93 at L1 and
0.76 / 0.88 / 0.87 at L2 for $T$ = 2 / 6 / 10 in the sweep — so it can only
add $W$. A-0 measured exactly this: $D_{\text{eager}}$ 100%, $D_{\text{depth}}$
0, in every cell.

**Decision.** For level $\ge 1$, `stall_urgency` becomes the due indicator
`score >= 1.0` — the same predicate the native picker and the guard use — and
the depth charge is removed. The deep branch is therefore
$W_{\text{stall}} \cdot [\text{due}] - W_{\text{work}} \cdot \text{work\_now}
- W_{\text{prem}} \cdot \text{premature}$, strictly negative below due and at
least +0.2 at due: **a deep level compacts if and only if RocksDB would
compact it.** L0 is unchanged from P1c-23. The prior's whole contribution
against the static twin is now the L0 proactive band, which exists only at
triggers $\ge 3$.

**Why the depth charge goes rather than being strengthened.** Output into an
empty level is a trivial move, free on $W$, so deferring it saves no write;
Corollary A.3 says removing a level — holding the bottom level over target —
is never write-profitable for $T \ge 2$; and under enforcement the guard's
due-age limit at the bottom levels is tens of milliseconds (61.6 ms at L7,
history 14.8), so a hold would be forced open almost at once and counted
against E-5.

**What this is not.** It is not a deferral policy. B-3's lever — hold a due
level so more overwrites accumulate before the merge — is left to the learned
residual, for two reasons recorded here: the protocol carries no per-level
garbage observable that could drive a prior term with a falsifiable
prediction, and under enforcement any deferral past native's due-age envelope
is forced by the guard (D-2, history 14.17), so on `prior_only` it could not
be measured in any case.

**Predictions, recorded in advance.** On `prior_only` and
`unconstrained_prior_only` at 10M, $T$ = 2 / 6 / 10, against the
same-configuration `regular` twin from the Hull-0 sweep
(`frontier_analysis.paired_comparison`, as in history 14.10):

1. **Mechanism.** φ at release (`compaction_measurements.json` `releases`,
   workload phase, trivial moves and drain excluded) at every populated level
   $\ge 1$ has p50 $\ge 0.98$ and within 0.05 of the twin's. Under the
   superseded prior the code's own flip points put it at 0.82–0.92.
2. **Merge survival.** Per-level $\eta$ (`views.workload.levels`) at L1 and
   L2 within $\pm 0.03$ of the twin's.
3. **Trigger-2 cells ($T$ = 2 and $T$ = 10), where no L0 band exists.** Mean
   paired relative $\Delta W$ and $\Delta R$ against the twin both inside
   $\pm 2\%$; at ten repeats the upper 95% bound on $\Delta W$ is $\le +2\%$.
   The prior is native there and should be indistinguishable from it.
4. **$T$ = 6 (trigger 4), the L0 band in isolation.** $\Delta W \in [+2\%,
   +12\%]$ and $\Delta R \in [-3\%, -12\%]$. This is the first measurement of
   the band's write cost with deep eagerness removed; it is expected to
   exceed $\delta_W$.
5. **E-5 on `prior_only` is zero by construction** — prior-compact ⟺ due at
   deep levels, and `RL_L0_ALLOW_DEFER=0` promotes any due L0 defer — so
   `prior_only` passes E-5 vacuously, exactly as the `oracle` holdout did,
   and cannot test the guard. E-5 is decided on `rl`.

**What this predicts for C-3, stated plainly: not a pass.** At $T$ = 2 and
$T$ = 10 the prior sits on its twin, which is on or near the hull:
non-dominated, contributing nothing. At $T$ = 6 it trades reads for writes
outside the margin and is expected to be dominated by a static point with a
lower trigger. D-4's purpose is to stop the prior spending write budget where
it buys no reads, so that what remains is the one lever the theory names,
measured cleanly.

**Falsification.** If prediction 1 fails, the change did not reach the plant
— an implementation defect, to be fixed before anything else is read. If 1
and 2 hold and 3 fails, the prior's write excess has a component other than
eagerness that A-0 did not see, and that is reported as a correction to A-0.

**Not taken, and why.** `work_now`'s normalisation stays (`/ size_ratio`,
where the typical overlap ratio is itself $\approx T$): under D-4 it only
grades the advantage at due, where the decision is already made. The L0
band's threshold stays at P1c-23's ruling; prediction 4 measures it.
`PRIOR_W_READ` is now inert and is kept as an ablation knob.

**Timing, and what git does and does not prove.** This entry and the code it
governs were committed **after** the arms that test it. The edit's file mtime
is 2026-09-22 08:07 UTC and the entry's 08:08; the first arm completed at
09:05, so the change was live for every run and the numbers are valid. But
mtimes survive no clone and any `touch` rewrites them, so **there is no dated
evidence that these predictions preceded the run**, and D-4 is therefore a
weaker record than D-1, D-2 or D-3. It is stated here rather than left for a
reader to discover, on the 14.15 precedent. The same applies to D-5.

**Predictions as scored, 2026-09-22** (`results/paired-assoc`, three repeats,
against the same-configuration static twin):

| # | Prediction | Result |
| --- | --- | --- |
| 1 | $\varphi$@release p50 $\ge$ 0.98 and within 0.05 of twin, every level $\ge$ 1 | **FAILED** at $T{=}6$ (L1 −0.062); passed at $T{=}2$ (0.003) and $T{=}10$ (0.001) |
| 2 | $\eta$ at L1/L2 within $\pm$0.03 | passed, max 0.008 |
| 3 | $T{=}2$/$T{=}10$ $\Delta W$, $\Delta R$ within $\pm$2% | passed: +0.09/+1.14, +0.10/+0.74 |
| 4 | $T{=}6$ $\Delta W \in [+2,+12]\%$, $\Delta R \in [-3,-12]\%$ | passed: +2.07, −5.69 |
| 5 | E-5 on `prior_only` zero by construction | confirmed: 0.0006 / 0.0000 / 0.0007 |

**Prediction 1 is recorded failed and is not reworded.** The rule it was
testing is confirmed by a direct measurement the prediction did not use:
**zero deep-level releases below due, 0 of ~20,000** merges across all three
cells. The $T{=}6$ gap is a downstream effect of the L0 proactive band, which
exists only at that cell's trigger of 4 — D-5 was preregistered to test that
reading and confirms it. The prediction was mis-specified: it used the static
twin as the reference for a deep-level property, but the twin also differs in
the L0 band, so it measured two things at once. The φ-below-due count is the
clean test and is what any successor should use.

**Implementation.** `rl_agent/multilevel.py` `analytic_advantage`, deep
branch only, and a comment in `rl_agent/config.py`. Python only: no rebuild,
`dbbench_sha256` unchanged, the 182-arm Hull-0 stands.
`docs/physics_informed_rl_architecture.md` §3.1 describes a superseded form
and is not the specification; this entry is.

**Evidence.** The decision map is reproducible from the pipeline constants;
native φ-at-release and $\eta$ from
`results/baseline_sweep/{T2-l0-2,T6-l0-4,T10-l0-2}-20-36-pri3-base16777216/10M/T*/repeat-0{1,2,3}/regular/compaction_measurements.json`.

### D-5, 2026-09-22 — the L0 proactive band is measured at a fixed trigger across ratios

**Recorded after the D-4 arms were scored at the comparators stage 06 selected
(L0 trigger 2 / 4 / 2 at $T$ = 2 / 6 / 10), and before the control runs below.**
Those arms are not re-scored and the programme's comparator does not change:
trigger 2 / 4 / 2 remains what stage 06 chose on 2026-09-21, before any policy
arm existed. This entry adds arms at a configuration the comparator rule did
not pick; it does not re-pick it.

**What was found.** Stage 06 selects minimum-space, then fastest, then lower
$W$. Under D-3's denominator the space filter admits all four triggers at
every ratio, so runtime decides — and at $T{=}6$ triggers 2 and 4 came in at
**156.487 s and 156.803 s on three repeats each, 0.20% apart**, inside the
rule's own 1% tie band. The tie went to trigger 4 on write amplification
(7.812 against 9.253). At $T{=}2$ and $T{=}10$ trigger 2 won on runtime
outright, by 4.2% and 2.6%.

The consequence was not foreseen when the comparator rule was written. The
prior's only remaining contribution after D-4 is the below-threshold L0 band,
and the band exists **only when the trigger is at least 3**: at trigger 2 the
sole below-threshold state is one file, which P1c-23's minimum run reduction
zeroes. So the D-4 arms measured the band at exactly one size ratio, and the
band's presence is perfectly confounded with $T{=}6$:

| cell | trigger | L0 jobs, policy / twin | $\Delta W$ | $\Delta R$ |
| --- | ---: | ---: | ---: | ---: |
| $T{=}2$ | 2 | 773 / 780 = **0.99×** | +0.09% | +1.14% |
| $T{=}6$ | 4 | 541 / 411 = **1.32×** | +2.07% | −5.69% |
| $T{=}10$ | 2 | 830 / 833 = **1.00×** | +0.10% | +0.74% |

Nothing distinguishes "the band costs 2% of $W$ and buys 5.7% of $R$" from
"the prior behaves this way at $T{=}6$". One arm set separates them.

**Decision.** Run `prior_only` and `unconstrained_prior_only` at **L0 trigger
4, base 16 MiB, $T$ = 2 and 10**, 10M, three repeats, on binary
`9b9321b1…`. The `regular` twins already exist in the Hull-0 sweep at nine and
three repeats, so no baseline is run. Results are reported as a named control,
never pooled with the comparator arms.

**Predictions, recorded in advance.** Against the trigger-4 twin at the same
ratio, by `frontier_analysis.paired_comparison`. The twins measure:

| cell | trigger | $W$ | $R$ | band headroom, $R_4 - R_2$ |
| --- | ---: | ---: | ---: | ---: |
| $T{=}2$ | 2 → 4 | 8.009 → 6.764 | 4.656 → 5.585 | **16.6%** of $R_4$ |
| $T{=}6$ | 2 → 4 | 9.253 → 7.812 | 3.662 → 4.260 | **14.0%** |
| $T{=}10$ | 2 → 4 | 10.493 → 8.742 | 3.026 → 4.004 | **24.4%** |

1. **The band appears at both ratios.** L0 compaction jobs against the twin
   $\ge 1.15\times$ at $T{=}2$ and $T{=}10$, against the 0.99× and 1.00×
   measured at trigger 2.
2. **Reads improve, ordered by headroom.** $\Delta R < 0$ at both, and the
   ordering is $|\Delta R|(T{=}10) > |\Delta R|(T{=}2) > |\Delta R|(T{=}6)$.
   Point estimates from $T{=}6$ recovering 41% of its headroom:
   $\Delta R \approx -6.8\%$ at $T{=}2$ and $-10\%$ at $T{=}10$; the
   preregistered intervals are $[-3\%, -11\%]$ and $[-5\%, -15\%]$.
3. **Writes rise, ordered by tree depth.** L0→L1 is a larger share of total
   write bytes in a shallow tree, so $\Delta W(T{=}10) > \Delta W(T{=}6) >
   \Delta W(T{=}2)$, with $\Delta W \in [+0.5\%, +4\%]$ at $T{=}2$ and
   $[+1.5\%, +7\%]$ at $T{=}10$.
4. **The D-4 prediction-1 failure reappears, which is what identifies it as a
   band artifact.** L1 $\varphi$ at release has p50 at least 0.02 below the
   twin's at both ratios — the same direction and mechanism as $T{=}6$'s
   −0.062. If instead $\varphi$ matches the twin at trigger 4, the explanation
   recorded for that failure is wrong and must be withdrawn.
5. **The D-4 rule still holds.** Zero deep-level releases below due at every
   populated level, as measured on the comparator arms (0 of ~20,000).

**Falsification.** If prediction 1 fails — the band does not appear at trigger
4 away from $T{=}6$ — then the attribution of $T{=}6$'s $\Delta W$/$\Delta R$
to the band is wrong, D-4's post-hoc reading of its own prediction-1 failure is
wrong, and both must be reported as withdrawn.

**What this does not decide.** It is not C-3. It does not change the
comparator, the manifest the programme's arms run against, or any acceptance
criterion. Whether the band should be retuned — `RL_PRIOR_MIN_RUN_REDUCTION`
is the constant that governs it — is **not** taken here: that constant would
be chosen after seeing the number it moves, which is the objection that kept
C-2 and E-1 recorded failed. The write hinge and $\lambda_W$ of Pathway D are
the preregistered mechanism for that trade, and they act on `rl`, not on the
prior.

**One consequence worth recording now, before the ten-repeat C-3.** At
$T{=}6$ the D-4 arms give $\Delta W$ = +2.07% with a 95% interval of
[+0.71, +3.43]. The write constraint is an upper-bound test at
$\delta_W = 2\%$, so on three repeats `prior_only` does not meet it at that
cell. That is reported as measured; no margin is widened.

**Implementation.** Manifests for the control cells come from stage 06 pointed
at the single trigger-4 configuration directory, so the existing selection rule
is applied to a one-candidate set rather than modified — no code change, and
the guard limits are calibrated from the arms the control actually runs on.
Driver: `d5_band_control.sh`.

**Evidence to be written.** `results/band-control/10M/T{2,10}/repeat-*/{prior_only,unconstrained_prior_only}/`,
against `results/baseline_sweep/T{2,10}-l0-4-20-36-pri3-base16777216/`.

### D-6, 2026-09-22 — the reward's space constraint is measured in bytes, not as a ratio

**Recorded before any `rl` arm has run on `Assoc`, and no `rl` arm has ever run
on this workload or this binary.** Unlike D-4 and D-5 this entry precedes every
run it can influence with nothing to re-score: the reward's space term has
produced no measurement on `Assoc` at all, because the only arms run so far are
`regular`, `oracle`, `prior_only` and `unconstrained_prior_only`, and the first
two carry no reward while the last two run in `eval_mode` with a zero residual,
so no multiplier has ever ascended. Found by reading the reward against the
manifest after D-5, not by observing a training failure.

**What was found.** `multilevel._global_reward` formed the space hinge as
`physical_sst_bytes / live_logical_bytes` against
`space_amplification_reference * (1 + rung)`. The numerator's denominator is
RocksDB's `EstimateLiveDataSize`, taken live per frame
(`compaction_picker_rl.cc:345`); the manifest's reference has been computed on
D-3's **measured garbage-free denominator** since 2026-09-21. The two sides are
therefore different metrics, and D-3 already documented the gap between them —
46% of live data discarded at $T{=}2$. Evaluated on today's `prior_only` arms:

| cell | $S$ estimate (live signal) | reference $\times 1.02$ | hinge, frame one |
| --- | ---: | ---: | ---: |
| $T{=}2$ | 1.9475 | 1.1120 | **+0.751** |
| $T{=}6$ | 1.2047 | 1.0620 | **+0.134** |
| $T{=}10$ | 1.1042 | 1.0517 | **+0.050** |

The hinge is violated on the first frame of every cell and stays violated
whatever the policy does, so `_dual_ascent` would drive $\lambda_S$ up
monotonically until it clipped at `LAMBDA_MAX`. **Pathway D's D-4 criterion
reads monotone multiplier divergence as evidence that a cell is analytically
infeasible** — so the learner would have manufactured a confirmation of
Theorem B.1 out of a unit mismatch, in every cell, and the diagnostic corollary
that makes D-4 "a positive result, not a failure" would have been reading its
own instrument.

**Decision.** The space hinge becomes settled physical SST bytes against the
tuned baseline's, at the rung's margin:
`hinge(physical_sst_bytes, expected_physical_sst_bytes * (1 + rung))`. Both
quantities are already in the manifest and in the protocol; nothing new is
measured.

**Why bytes rather than a repaired ratio.** The denominator is a constant of
the workload, not of the policy — `filluniquerandom` writes 2.9M unique keys
and `mixgraph` only overwrites them, and D-3 measured the garbage-free size
stable to **0.0060% across 108 runs**. Dividing two arms' physical bytes by the
same constant cannot change which is larger, so the ratio adds nothing except
the estimate's depth-sensitivity, which is exactly the defect D-3 documents and
which biases the term against the behaviour under test. And the correct
denominator is not available live in any case: it comes from a reference
compaction run after the measured phase.

**It also makes the reward and the shield agree.** `rl_safety_manifest.cc:200`
and `:345` already load `allowed_physical_sst_bytes` and compare
`state.physical_sst_bytes` against it. Before this change the learner's space
signal and the guard's space signal were different quantities in different
units; they are now the same quantity. At the 2% headline rung the two bounds
coincide to one byte (the manifest rounds `1.02 * expected`); at other rungs
the reward follows the swept margin (P0-6) while the guard keeps its fixed 2%,
which is intended — the guard is a safety envelope, not the objective.

**No acceptance criterion changes.** The paired evaluator continues to score
space through `04_generate_graphs.amplification_metrics` on D-3's measured
denominator. Only the learner's control signal is affected. The contract is
**not** edited, for D-3's reason: `research_objective.py:27` hashes its raw
bytes into every fingerprint, so an edit would void the 182-arm Hull-0.
`constraints.space` names a metric and a rung ladder and has never named a
denominator; P1c-22 says space enters "as a hinge above the manifest's
tuned-baseline reference" and does not specify its form.

**Predictions, recorded in advance.** On the first `rl` arms at 10M,
$T$ = 2/6/10:

1. **$\lambda_S$ does not diverge.** On every cell it plateaus or returns to
   zero rather than rising monotonically to `LAMBDA_MAX` (Pathway D's D-3
   criterion). Under the superseded form it would have clipped in all three.
2. **$\lambda_W$ is the binding multiplier, not $\lambda_S$.** The D-4/D-5 arms
   put settled bytes at 97.2%, 98.2% and 98.1% of the bound at $T$ = 2, 6, 10
   while the write constraint is missed at every ratio where the prior has a
   lever. If any cell shows $\lambda_S$ exceeding $\lambda_W$ at the end of the
   run, the space constraint is binding for a reason this entry has not
   identified.
3. **On a `prior_only` re-run under the fixed reward the frame-mean space
   excess is below 0.01 at every ratio**, against +0.751 / +0.134 / +0.050
   under the superseded form. This is the cheap check and it needs no learner.

**Falsification.** If $\lambda_S$ still rises monotonically on a cell whose
settled bytes finish inside the bound, the hinge remains mis-specified and this
entry must be reported as an incomplete repair rather than a fix.

**Not taken, and why.** The live per-frame signal in C++ still computes
`EstimateLiveDataSize` for the snapshot's `live_logical_bytes`, and the guard's
read-side and debt terms still use it. Replacing it is a C++ change, and a
rebuild voids the hull, so it is batched into Gate 2 with the other deferred
instrument work. Nothing in this entry depends on it: the reward no longer
reads that field for the space term, and it is retained in the frame log as
`space_amplification_estimate` so a frame can be joined to the pre-D-6 logs.

**Implementation.** `rl_agent/config.py` (`expected_physical_sst_bytes` added
to the manifest's required references; `SPACE_BYTES_BOUND`; `SPACE_BOUND`
retained as a diagnostic), `rl_agent/multilevel.py` (`_global_reward`'s hinge
and its logged components, `reward_state`), `rl_agent/server.py` (startup
banner). Python only: no rebuild, `dbbench_sha256` unchanged, the 182-arm
Hull-0 and the guard manifests stand.

### D-7, 2026-09-22 — what the learner is expected to do on `Assoc`

**Recorded before any `rl` arm has run on `Assoc`, on this binary, or under the
P1c-22 reward.** Nothing here can be fitted to an outcome: the learned arms of
2026-09-03 ran on the uniform workload, under the superseded reward, and with
the pre-D-4 prior. All three have since changed, so §10.7's measured behaviour
is not a baseline expectation and this entry says what replaces it.

**Why a prediction is owed.** §10.7 Finding 3 measured the residual deferring
top-of-tree compaction, deepening the tree by one to three levels, and trading
part of the prior's read advantage for space amplification. That was rational
for the reward it was given — $\Phi$ treated space as a quantity to minimise.
P1c-22 removed space as a minimand and D-6 repaired the hinge that replaced it.
If the same behaviour reappears, the repair is incomplete, and the cheapest
time to say so is before the run.

**Predictions.**

1. **Depth does not inflate.** $\delta L = 0$ against the paired `regular` arm
   in every cell, measured from `levelstats` and the release events. §10.7's
   +1 / +1 / +3 does not reproduce.
2. **$\lambda_W$ binds and $\lambda_S$ does not.** At the end of every run
   $\lambda_S < \lambda_W$, and $\lambda_S$ plateaus rather than rising
   monotonically (D-6 predictions 1 and 2, restated here as policy outcomes).
3. **E-5 becomes measurable for the first time, and passes.** The conditional
   override rate exceeds 0.001 in at least one cell — i.e. the policy actually
   disagrees with the guard, which neither the `oracle` holdout nor
   `prior_only` can do — and stays $\le$ 1% in every cell. A conditional rate
   of exactly zero everywhere would mean E-5 is undecidable by any arm this
   programme has, which is a reportable negative result about the criterion.
4. **The residual moves.** Argmax flip rate $>$ 0.1 per level (Pathway D's D-5
   threshold); on uniform at 10M it reached 0.42–0.73.
5. **No read gain at the trigger-2 comparators.** At $T{=}2$ and $T{=}10$ the
   comparator is L0 trigger 2, where no below-threshold band exists (D-5) and
   the only lever is deferring a due level, which costs reads through depth
   (D-4). So $\Delta R$ against `regular` does not fall below −2% at either
   cell. $T{=}6$ carries trigger 4 and may improve, bounded by the band's
   measured trade of 2.75 reads per unit of write.

**Falsification.** Prediction 1 is the load-bearing one. If depth inflates, the
reward still credits something the criteria do not, D-6 must be reported as an
incomplete repair rather than a fix, and the run is diagnostic rather than
evidential.

**What this does not claim.** Nothing here predicts that `rl` beats the hull.
C-4 is the criterion for that and is not evaluable until Hull$_s$ exists at
Gate 3c. A policy can satisfy every prediction above and still be dominated;
that outcome is reported as dominated.

**Scope.** 10M, $T$ = 2/6/10, three repeats, `rl` and `unconstrained_rl`, on
binary `9b9321b1…` at the stage-06 comparators. Three repeats is a mechanism
count, not an acceptance count; no criterion carrying a 2% margin is scored
from it.

### D-8, 2026-09-22 — the reward's latency hinge drops the windowed p99

**Recorded after the D-7 arms were scored and before any re-run.** D-7's
verdict stands exactly as measured and nothing in it is re-scored; this entry
repairs the instrument that verdict exposed and says, in advance, what the
repaired instrument is expected to show.

**What was found, from the D-7 arms.** `lambda_latency` reached `LAMBDA_MAX`
= 100.0 in **all six learned arms**, against `lambda_write` of 8.77 / 16.45 /
10.98 — six to eleven times larger. Per-frame `latency_excess` was **never
zero** across 2,231 scored frames: p50 19.92, mean 20.74, minimum above zero.
The learner's objective was therefore dominated by a term no policy could
satisfy.

The cause is a unit error, not a parse error. The reward hinges a **50 ms
window's** quantile against a **whole-run** quantile limit. For the average
that is sound — a window mean is an unbiased estimate of the run mean, and the
measured whole-run average hinges are 0.00 to 0.07. For the p99 it is not,
because the write-latency distribution is extreme. From the raw histogram of a
T{=}6 arm:

| statistic | value |
| --- | ---: |
| count | 2,900,000 |
| average | 16.48 us |
| P50 | 0.51 us |
| P99 | **2.33 us** |
| P99.9 | 2749.36 us |
| max | 14,076 us |

97.8% of writes complete in under 1 us and the top 0.1% run to milliseconds,
so the average sits **above** the P99. Over 2.9M samples the tail is 0.1% and
the run p99 is 2.33 us; over the ~500 writes in one 50 ms frame the p99 is the
fifth-largest sample and lands in that tail constantly. Against a manifest
limit of 1.02 us that yields a persistent excess of roughly twenty, which is
what was measured. **History 10.7 instrument problem 6 flagged the
p99-below-average anomaly and asked for the histogram to be verified rather
than assumed; it is hereby verified as a real property of the distribution.**
This is the same inspection-paradox error history 14.8 diagnosed for the guard
and repaired with `frame_simulated_limits`; the reward never received that fix.

**Decision.** The latency hinge sums the windowed **average** terms only. The
p99 terms are still computed per operation and logged as `latency_terms`, so
the decomposition is readable from a frame instead of inferred, but they do not
enter the hinge.

**What this does and does not relax.** P0-4 is untouched: average **and** p99
remain acceptance metrics at a 2% paired margin, scored at run end by the
paired evaluator on whole-run statistics, where both sides are the same
quantity. The guard keeps its own p95 envelope. What changes is only which
signal the learner is asked to act on per frame, and the principle is the one
P1c-22 already implies — a hinge must be a quantity the policy can move and
the limit must be measured in the same unit. Re-deriving per-frame p99 limits,
as `frame_simulated_limits` does for the guard, is the alternative; it needs
per-frame baseline latency that only a controller-bearing arm produces, and is
deferred rather than rejected.

**Predictions, recorded in advance.** On a re-run of the D-7 matrix
(10M, $T$ = 2/6/10, three repeats, `rl` and `unconstrained_rl`):

1. **$\lambda_{\text{lat}}$ does not rail.** It stays below `LAMBDA_MAX` in
   every cell, and per-frame `latency_excess` has p50 below 0.05 against
   19.92 under D-7.
2. **$\lambda_W$ becomes the largest multiplier in every cell.**
3. **The new `latency_terms` log attributes at least 90% of the superseded
   excess to `write_p99`**, which is currently an inference from arithmetic
   (1.02 us limit, ~22 us window p99) and not a measurement.
4. **Depth is re-tested, and either outcome is informative.** D-7's prediction
   1 failed at $\delta L$ = +2.0 / +1.0 / +0.7 while the objective was
   dominated by a broken term, so it did not test what it was written to test.
   If $\delta L \le 0$ here, that failure was instrument-driven. If depth
   inflates again, the behaviour is a real property of the learner under a
   correct objective and must be reported as such — §10.7's finding surviving
   both P1c-22 and D-6. **D-7's verdict is not re-scored either way**; this is
   a second experiment under a changed instrument, not a re-run of the first.
5. **E-5 stays above 1% in at least two cells.** The learner defers 41-43% of
   due frames and the guard forces on due age and pressure, which latch by
   construction (D-5 audit), so the conditional rate should remain high. If it
   instead passes everywhere, the 7-15x measured under D-7 was an artifact of
   the broken objective and the guard's status must be re-read accordingly.

**Falsification.** If prediction 1 fails — $\lambda_{\text{lat}}$ still rails
with the p99 terms removed — then an average term is also mis-specified and
this entry is an incomplete repair, to be reported as such.

**Implementation.** `rl_agent/multilevel.py` `_global_reward` only. Python:
no rebuild, `dbbench_sha256` unchanged, the 182-arm Hull-0 stands.

### D-9, 2026-09-23 — the learner's action space, state, reward and horizon are aligned with the constrained objective

**Recorded after D-7 was scored and after the architecture audit of
2026-09-23 (history 14.20), and before any arm runs under it.** D-7's verdict
stands as measured. D-8's repair is kept and its five predictions are
re-stated below under this instrument rather than run on their own: an
instrument change this large makes a D-8-only re-run a test of a harness that
is about to be replaced. Nothing in this entry is a re-scoring; every number
quoted from the D-7 arms is an audit measurement of the harness, not a result
about the learner, which is precisely the finding.

**What the audit found.** The learner was built when the objective was
latency and stalls, then given a constrained reward on 2026-09-20 without the
rest of the architecture being re-derived. Measured on the D-7 arms
(`results/learner-assoc`, repeat 1), against their same-configuration static
twins:

1. **The write hinge measured the wrong quantity.** It compared a 10 s
   exponentially weighted ratio against the whole-run bound. The window is
   time-weighted, the criterion is byte-weighted, and under `Assoc`'s 26–46%
   stall fractions the two differ: run-level $W$ 8.40 / 8.94 / 11.08 against
   window medians 9.25 / 12.86 / 15.76 at $T$ = 2 / 6 / 10, with the hinge
   positive on 94% / 100% / 99% of frames and in every decile of the run. The
   learner was told the constraint was violated by 13–61% when the evaluator
   saw 3–12%.
2. **Every multiplier was a ratchet.** The update fed the hinge $[g]^+$ rather
   than the signed constraint value, so no $\lambda$ could fall. In every D-7
   arm each multiplier's final value was its maximum and $\lambda_S$ never left
   1.00. Pathway D's D-3 ("plateaus") and D-4 ("diverges where infeasible")
   were reading a monotone counter.
3. **The action space offered the two write-costly moves and withheld the
   write-saving one.** Any level with score $\ge 0.10$ could be compacted early
   through an optional token, and L0 at one file; L0 could not be deferred
   while due (`RL_L0_ALLOW_DEFER=0`, a bridge-validation posture from
   2026-08-16 never lifted). The residual re-learned exactly what D-4 had
   removed from the prior: at $T{=}2$ it released L1 at a median 41% of target
   (686 of 935 releases below 0.95, twin 0 of 1184) and L2 at 38% (940 of
   1079), with merge survival $\eta$ rising from 0.84 to 0.93 at L1 and 0.76
   to 0.89 at L2 — each early merge dropped less garbage; and it compacted L0
   at a mean of 1.1 files on 16–26% of below-trigger frames. That, not
   deferral, is the depth mechanism of D-7's +2 / +1 / +0.7 levels.
4. **The state could not see the constraints.** No write-amplification
   feature, no multiplier, no per-level garbage signal; eleven of 31 features
   were stall-era pressure terms, one was the withdrawn scan-work metric (a
   constant at its floor) and one the space estimate D-3 retired.
5. **The horizon was shorter than the physics.** $\gamma$ = 0.95 per second
   is a 20 s effective horizon against consequences that persist for the rest
   of a 150 s run.
6. **The prior is a step-0 policy only.** $|$prior advantage$|$ averaged
   0.6–0.7 against a residual advantage of 258–652, because $Q$ is in return
   units and the prior in a dimensionless $\pm 2$; and the TD loss was
   squared on returns in the thousands (Pathway D item 5 asked for Huber and
   normalisation; only clipping was implemented).

**Decisions.** Six, each with an ablation knob that restores the previous
behaviour, all Python or runner environment, none touching the binary, the
contract file or any manifest.

1. **Flows and levels.** A constraint that is a ratio of run totals ($W$,
   $R$, seeks per scan, stall fraction) enters the reward as the frame's
   **signed marginal** contribution — numerator minus bound times
   denominator, over the run-to-date mean denominator rate, so the unit is the
   metric's own and the run sum is exactly the constraint the evaluator
   scores. A constraint that is a level (space bytes, latency averages) keeps
   the hinge on its window value, which estimates the run value. The
   objective becomes Get-weighted the same way. This supersedes P1c-22's
   "windowed" wording for $W$ on D-8's own principle — a hinge must be a
   quantity the policy can move and its limit must be measured in the same
   unit — and the contract JSON is not edited, for D-3's reason (its hash is
   in every fingerprint).
2. **Signed dual ascent, and replay re-priced at sample time.**
   $\lambda \leftarrow \text{clip}(\lambda + \eta\,\text{slack}, 0, 100)$
   with slack signed in the constraint's unit (run-to-date for flows, window
   for levels); $\eta$ = 0.05, from the single-episode budget (the write
   multiplier's useful range, $R / (\text{slack} \cdot W_{\text{base}})
   \approx 5$ at a 10% overshoot, must be traversable in the first third of a
   ~3,000-frame run). Transitions store the reward as a component vector and
   the trainer prices every sample with the multipliers current when it is
   drawn (`rl_agent/lagrange.py`), so one batch is priced under one
   objective and a fall in $\lambda$ reaches every stored sample.
3. **The D-4 rule becomes an action mask.** A level $\ge 1$ is offered
   `compact` only when RocksDB scores it due; L0 below its trigger only when
   the compaction nets at least `RL_PRIOR_MIN_RUN_REDUCTION` (2) runs. Due
   levels are never masked. The mask is never looser than the C++
   optional-token gate, so no offered action is silently dropped.
4. **The L0 posture is lifted for the learned arms.** `rl` and
   `unconstrained_rl` run with `RL_L0_ALLOW_DEFER_LEARNED=1`; `prior_only`
   and `unconstrained_prior_only` keep 0, so the D-4/D-5 record stays
   comparable. On the enforced arm the guard's calibrated L0 limits and its
   `l0_slowdown` term still bound the deferral; the unconstrained arm shows
   the lever unbounded. Recorded per arm in `metadata.env`; not a
   fingerprint field, since it is a controller setting that pairs against
   `regular` exactly as before.
5. **The state gains the constraints.** Run-to-date and windowed $W$ over
   bound, space bytes over bound and the five multipliers replace the two
   dead features: 37 inputs (`config.ML_STATE_FIELDS`).
6. **Horizon and loss.** $\gamma$ = 0.98 per second (50 s), credit window
   8 s, Huber TD loss. Reward normalisation and a re-scaled prior are **not**
   taken (below).

**Predictions, recorded in advance.** Scope: 10M, $T$ = 2 / 6 / 10, three
repeats, `rl` and `unconstrained_rl`, binary `9b9321b1…`, at the stage-06
comparators, after a one-arm smoke test; paired against the same-seed
`regular` twins of the Hull-0 sweep by `frontier_analysis.paired_comparison`.
Three repeats is a mechanism count; no criterion carrying a 2% margin is
scored from it.

1. **The instrument is consistent with the evaluator.** Per arm, the
   reward's run-to-date $W$ at shutdown equals the evaluator's $W$ within 2%.
2. **No multiplier is a ratchet.** In every arm at least one multiplier ends
   below its run maximum, and $\lambda_{\text{lat}}$ never reaches
   `LAMBDA_MAX` (D-8 prediction 1).
3. **The mask reaches the plant.** Zero releases below $\varphi$ = 0.95 at
   any level $\ge 1$ in the workload phase, trivial moves excluded, in every
   learned arm (against 686 of 935 at L1, $T{=}2$, under D-7); and the mean
   L0 file count at a below-trigger compaction is at least 2.0 (against 1.1).
4. **$\lambda_W$ binds.** It is the largest multiplier at the end of every
   `rl` arm (D-8 prediction 2), and in any arm whose run-level $W$ finishes
   inside the 2% margin it ends below its run maximum — the D-3 criterion,
   decidable for the first time.
5. **Depth does not inflate.** $\delta L \le 0$ against the paired `regular`
   in every cell (D-7 prediction 1 and D-8 prediction 4, re-asked under an
   instrument that no longer offers early deep compaction).
6. **Write moves toward parity.** Mean paired $\Delta W$ for `rl` is at most
   +3% at $T{=}2$ and $T{=}10$ (trigger-2 comparators, where the mask closes
   every write-costly lever) and at most +5% at $T{=}6$ (trigger 4, where the
   L0 band exists), against +6.1 / +13.2 / +4.6% under D-7; and
   `unconstrained_rl` finishes with lower $W$ than `rl` in every cell, because
   the L0 lever is bounded only on the enforced arm.
7. **Read does not regress at the trigger-2 cells.** If prediction 5 holds,
   mean paired $\Delta R$ for `rl` is at most +2% at $T{=}2$ and $T{=}10$
   (against +3.6 / +6.0% under D-7).
8. **E-5 stays above 1% in at least two cells** (D-8 prediction 5): `due_age`
   and `pressure` latch by construction, so the guard still changes a
   deferring policy's action on well over 1% of the frames where it could.
9. **The L0 lever is real but guard-bounded on `rl`.** `rl` defers a due L0
   on more than zero frames in every cell, and `unconstrained_rl` defers a
   larger fraction of its due-L0 frames than `rl` does.

D-8's prediction 3 — at least 90% of the superseded latency excess was
`write_p99` — is scored from the `latency_terms` log as a measurement of the
D-7 instrument, not of this one.

**Falsification.** Prediction 3 is the plumbing check: if any early deep
release appears, the mask did not reach the plant and nothing else is read
until that is fixed. Prediction 1 failing means the reward's write accounting
and the evaluator's disagree and the flow ruling must be withdrawn. If
predictions 3 and 5 both hold but 6 fails at the trigger-2 cells, then holding
due levels closed for the guard's due-age allowance costs more write than the
budget by relocation alone, without any elision gain — the learner's last
lever in this action space is worthless at parity and Pathway A is the only
remaining route. If 3 holds and 5 fails, the depth is coming from deferral,
not eagerness, which is the Theorem A.1 cascade; the same conclusion follows.
Either way the run is evidential about the architecture, which D-7 was not.

**Not taken, and why.** (a) A frame-simulated windowed-$W$ reference in the
manifest, as `frame_simulated_limits` does for the guard, would let the
window hinge stand; the marginal form needs no reference and sums to the
criterion exactly, so it is preferred. (b) Reward normalisation by running
standard deviation and a prior re-scaled to return units are left for a later
entry: the return scale moves roughly 300-fold under this change and D-2's
criterion should first be read on the new scale. (c) The guard's write sensor
(E-3) and Pathway A's capacity action are C++, and a rebuild voids the
182-arm Hull-0; they stay in Gate 2. (d) `RL_PRIOR_MIN_RUN_REDUCTION` and the
comparator are untouched.

**Implementation.** `rl_agent/lagrange.py` (new), `rl_agent/config.py`,
`rl_agent/multilevel.py` (`_global_reward`, `_encode`, `_compact_allowed`,
`process`), `rl_agent/replay_buffer.py`, `rl_agent/agent.py`,
`rl_agent/server.py`, `scripts/dbbench_pipeline/config.sh`
(`RL_L0_ALLOW_DEFER_LEARNED`) and `03_run_experiments.sh` (per-arm posture).
Exercised offline on a synthetic frame stream before commit: state width 37,
mask behaviour, the run-to-date $W$ identity, the marginal write sum
reproducing the constraint total to 0.7%, the write multiplier peaking where
the run-to-date $W$ crosses its bound and falling after, the four slack
constraints decaying to zero, and training under re-priced replay with Huber
loss. Python and shell only: no rebuild, `dbbench_sha256` unchanged, the
182-arm Hull-0 and every manifest stand. Driver: `d9_learner_run.sh`.

**Evidence to be written.** `results/learner-assoc-d9/10M/T{2,6,10}/repeat-*/{rl,unconstrained_rl}/`,
`results/learner-smoke-d9/`, `gate1/d9_verdict.txt`, `gate1/d9_conditional.{json,txt}`;
twins from `results/baseline_sweep/`.

**Smoke gate as scored, 2026-09-23.** One `rl` arm at 10M/$T{=}2$
(`results/learner-smoke-d9/10M/T2/rl`). The gate stopped the matrix on a
railed latency multiplier, so the matrix did not run and predictions 4 and
6–9 are unscored. On the one arm: prediction 3 holds (zero deep releases
below $\varphi$ 0.95; L0 compacted below trigger on 7 frames only), prediction
5 holds ($L$ = 9 against the twin's 9), prediction 1 **fails** (reward $W$
8.267 against evaluator $W$ 8.036, +2.9%), prediction 2 **fails**
($\lambda_{\text{lat}}$ = 100.0, $\lambda_W$ = 38.8, $\lambda_{\text{scan}}$
= 26.3). The arm itself, against its same-seed twin: $W$ +0.45% (8.036),
$R$ +3.2% (4.70), seeks 7.47 against 7.54–7.69, depth 9 against 9. The causes are three instrument defects,
measured in D-10, none of them in the policy.

### D-10, 2026-09-23 — the multiplier instrument repaired: latency references in the telemetry's unit, the write denominator, and a warm-up

**Recorded after D-9's smoke gate stopped its matrix, and before any further
arm runs.** D-9's predictions are not re-scored; its smoke arm is recorded
above as what it was. D-8's falsification clause — "if $\lambda_{\text{lat}}$
still rails with the p99 terms removed, an average term is also
mis-specified" — fired, and this entry is its consequence.

**Three defects, each measured on the smoke arm.**

1. **The latency hinge compared two instruments.** The frame's latency comes
   from the C++ window telemetry; the formal `*_latency_avg_ns_limit` comes
   from db_bench's histogram. Totalled over the oracle calibration windows,
   the baseline's own telemetry averages are:

   | operation | telemetry, $T$=2/6/10 (ns) | histogram, $T$=2/6/10 (ns) | ratio |
   | --- | --- | --- | ---: |
   | get | 7,801 / 5,895 / 5,477 | 7,901 / 5,788 / 5,381 | 1.0 |
   | scan | 460,780 / 289,357 / 255,392 | 23,761 / 13,755 / 10,330 | 19–25 |
   | write | 1,266 / 1,089 / 1,192 | 11,432 / 11,646 / 11,946 | 0.1 |

   The scan hinge therefore read +18 on 100% of the smoke arm's frames while
   the evaluator's scan latency for the same run was 22.9 us against a 24.2 us
   limit — under. **This corrects D-8.** The p99 hinge terms were zero on
   every frame of the smoke arm (`write_p99` maximum 0.000), so the D-7
   latency excess of p50 19.9 was `scan_avg` (mean 18.9 here), not the write
   p99 D-8 inferred from arithmetic; D-8's prediction 3 is falsified at 0%.
   D-8's remedy stands on its own ground — a window p99 is not an estimate of
   a run p99 — but its diagnosis of D-7 was wrong.
2. **The write denominators differ by 2.9%.** The evaluator divides by the
   `rocksdb.bytes.written` ticker, WriteBatch bytes including per-record
   framing; the telemetry counts key + value. Measured: 1,054 against 1,024.4
   bytes per Put. The reward's run-to-date $W$ read 8.267 against the
   evaluator's 8.036, so a run at parity looked 2.9% over a 2% margin, and
   D-9 prediction 1 failed at exactly this gap.
3. **The run-to-date ratios are dominated by the load's backlog early on.**
   $W_{\text{cum}}$ was 17 over the first 16 s and 11 at 32 s against a bound
   of 8.2; seeks per scan started at 26 against 7.8. Every flow multiplier
   climbed before its ratio meant anything — $\lambda_W$ reached 18 inside the
   first decile — and $\lambda_{\text{scan}}$ finished at 26.3 with the run
   *under* its seeks bound, because a decay of 0.05 × 0.04 per frame cannot
   undo a climb of 27 in the frames that remained. Both arms inherit the
   backlog and the evaluator counts it in both; the defect is only that the
   multiplier acted on it.

**Decisions.**

1. **Latency references in the telemetry's own unit.**
   `06_calibrate_live_guard.py` totals count and latency sum per operation
   over the calibration windows (those after the first Get; the controller is
   suspended during the load) and writes
   `{get,scan,write}_latency_avg_ns_telemetry_reference` and `_limit` (2%
   margin, P0-4) into the manifest. The reward's latency averages become
   flows in that unit — per frame, (sum − limit × count) over the run-to-date
   mean count rate, in units of the limit; the multiplier's slack is the worst
   operation's signed run-to-date excess. p99 is logged raw and enters
   nothing. The formal limits stay in the manifest and remain what the
   evaluator scores. The three manifests were regenerated from the existing
   calibration artifacts with every guard field byte-identical; only the six
   fields were added. SHA-256: $T$=2 `0bb12249…` → `d73012e3…`, $T$=6
   `25801e34…` → `929e3a42…`, $T$=10 `c4e51d8e…` → `274ba875…`. The band
   manifests under `baseline_slo_band/` are not regenerated; nothing runs
   under them.
2. **The write denominator gains the framing.** 30 bytes per write operation
   (`RL_WRITE_BATCH_OVERHEAD_BYTES`, measured; the
   `WriteBatchInternal::ByteSize` floor for one Put is 16). Prediction 1
   tightens to 1%.
3. **A multiplier warm-up and a slack clip.** No multiplier moves during the
   first 30 s of controlled time (`RL_LAMBDA_WARMUP_SECONDS`), and one step
   is bounded by `LAMBDA_LR` × 1 (`RL_LAMBDA_SLACK_CLIP`).

**Not taken, and why.** The principled repair for defect 3 is a time-indexed
reference — the baseline's own run-to-date $W(t)$, seeks$(t)$ and stall$(t)$
at the same elapsed time, against which a native-like policy reads zero slack
throughout. The window log carries no byte counters, so it needs either a
C++ change (deferred to Gate 2 with the rest) or a reconstruction from the
LOG's flush and compaction events plus an assumed put-rate profile, which is
its own instrument to validate. The warm-up is the cheap bound; prediction
10 says how far it must hold, and if it does not the trajectory reference is
the next entry.

**Predictions, recorded in advance.** D-9's scope: 10M, $T$ = 2 / 6 / 10,
three repeats, `rl` and `unconstrained_rl`, binary `9b9321b1…`, after a
one-arm smoke test gated on predictions 1–3 below. D-9's predictions 3 to 9
are restated unchanged; 1 and 2 are tightened; 10 is new.

1. **Instrument identity.** Per arm, the reward's run-to-date $W$ at shutdown
   equals the evaluator's $W$ within **1%**.
2. **Multipliers behave.** No multiplier reaches `LAMBDA_MAX` in any arm; at
   least one ends below its run maximum in every arm; and
   $\lambda_{\text{lat}}$ ends at or below 5 in every arm (the smoke arm's
   evaluator latencies were within 4% of the baseline's, and in the telemetry
   unit that is a slack near zero).
3. **The mask reaches the plant.** Zero deep releases below $\varphi$ 0.95;
   mean L0 file count at a below-trigger compaction at least 2.0.
4. **$\lambda_W$ binds.** Largest multiplier at the end of every `rl` arm, and
   below its run maximum in any arm whose $W$ finishes inside the 2% margin.
5. **Depth does not inflate.** $\delta L \le 0$ against the paired `regular`
   in every cell (held on the smoke arm: 9 against 9).
6. **Write moves toward parity.** Mean paired $\Delta W$ for `rl` at most
   +3% at $T{=}2$ and $T{=}10$ and +5% at $T{=}6$; `unconstrained_rl` below
   `rl` on $W$ in every cell.
7. **Read does not regress at the trigger-2 cells.** If 5 holds, mean paired
   $\Delta R$ for `rl` at most +2% at $T{=}2$ and $T{=}10$. The smoke arm
   sits at +3.2% against its same-seed twin, so this is the prediction most
   likely to fail; it is kept as D-9 wrote it.
8. **E-5 stays above 1% in at least two cells.**
9. **The L0 lever is real but guard-bounded on `rl`.**
10. **The transient is bounded.** $\lambda_W$ ends below 20 in every `rl`
    arm (38.8 on the smoke arm) and $\lambda_{\text{scan}}$ below 10 (26.3).

**Falsification.** If $\lambda_{\text{lat}}$ rails or ends above 5 with the
references in its own unit, a latency term is still mis-specified and this
entry is an incomplete repair. If prediction 1 fails at 1%, the framing is
not a per-operation constant and the correction must be reported as wrong. If
prediction 10 fails on arms at parity, the multiplier level is
transient-driven, D-3 and D-4 cannot be read from it, and the trajectory
reference above becomes required before any $\lambda$ figure is quoted.

**Implementation.** `scripts/dbbench_pipeline/06_calibrate_live_guard.py`
(`run_totals`, the six fields), `rl_agent/config.py` (required telemetry
fields, `WRITE_BATCH_OVERHEAD_BYTES`, `LAMBDA_WARMUP_SECONDS`,
`LAMBDA_SLACK_CLIP`), `rl_agent/lagrange.py` (clip), `rl_agent/multilevel.py`
(latency as telemetry-unit flows, the framing correction, the warm-up),
`d10_learner_run.sh` (regenerates the manifests as its first phase; gate
tightened to 1% and to every multiplier). Exercised offline on the synthetic
stream: warm-up held, the identity exact with the framing, $\lambda_W$
peaking and falling, $\lambda_{\text{lat}}$ rising gently on a 3% telemetry
excess. Python and shell only; no rebuild; the Hull-0 stands.

**Evidence to be written.** `results/learner-assoc-d10/10M/T{2,6,10}/repeat-*/{rl,unconstrained_rl}/`,
`results/learner-smoke-d10/`, `gate1/d10_verdict.txt`, `gate1/d10_conditional.{json,txt}`;
twins from `results/baseline_sweep/`.

**Smoke gate as scored, 2026-09-23.** One `rl` arm at 10M/$T{=}2$
(`results/learner-smoke-d10/10M/T2/rl`). The multiplier repair held:
$\lambda_{\text{lat}}$ ended at 0.10 (100 under D-9), $\lambda_W$ at 1.79
(38.8), $\lambda_{\text{scan}}$ at 0.0 (26.3), space and stall at 0 — so
predictions 2 and 10 hold on the one arm, and 3 and 5 hold again (zero early
deep releases, depth 9 against 9). The gate stopped on **prediction 1**: reward
$W$ 7.954 against evaluator $W$ 8.040, −1.07%, the opposite sign to D-9's
+2.9%. Diagnosis: the two sides were never measuring the same phase.
Decision 2 (30 bytes per Put) is **withdrawn**: the framing is 16 bytes,
measured at 15.8 once both sides were compared over the same phase. The
evaluator, not the reward, was wrong; D-11.

### D-11, 2026-09-23 — the evaluator scored the bulk load; write, stall and latency become measured-phase quantities, and the `Assoc` record is re-scored

**Recorded after D-10's smoke gate stopped on its write identity and the
term-by-term comparison behind it, and before any further arm runs.** This
is an instrument correction of the D-3 kind: a definition the record already
claimed, applied uniformly to every existing arm from artifacts it already
holds, with no criterion reworded. It is also the first defect found in the
evaluator itself rather than in the controller or the reward.

**What was found.** P1c-19 recorded that `resetstats` between the load and
mixgraph "zeroes tickers and histograms after the load". db_bench's
`resetstats` calls `DB::ResetStats`, which clears RocksDB's internal stats
only; the `Statistics` tickers and histograms the `stats` benchmark prints
are cumulative since open, and the run log holds exactly one dump, at the
end. `rocksdb.number.keys.written` on the D-10 smoke arm reads **4,029,089**:
2,900,000 load keys plus 1,129,089 mixgraph Puts. Consequently, from
2026-09-20 to 2026-09-23 the evaluator's write amplification, stall seconds
and write latency covered the whole run. On the smoke arm: stall 44.0 s
whole-run against **0.033 s** in the measured phase (the internal-stats
`Cumulative stall` line, which is reset); write latency 11.4 us against
1.7 us. Point-read amplification, sorted-run seeks and space were unaffected
(no reads in the load; space is settled), and get and scan latency were
measured-phase by the same accident, except that `rocksdb.db.seek.micros`
times the Seek call and not the scan — the 19× behind D-10's scan mismatch.

**Why the whole-run figure was not a diluted version of the right one.** The
load writes about 24 GB of the 34 GB an arm writes at $T{=}2$, so a
measured-phase effect appears in the whole-run ratio at roughly a quarter of
its size — but the load's own bytes scatter run to run by an amount
comparable to that effect, so whole-run paired deltas carried load noise as
well as diluted signal. They cannot be rescaled; they had to be re-scored.

**Decisions.**

1. **The evaluator reconstructs the measured phase.** `collect_arm`
   (`04_generate_graphs.py`) now returns, as `write_amplification`, physical
   write bytes from the event log — flush SST sizes joined to flush jobs,
   plus compaction outputs — inside the window between the
   `RL_CONTROL_RESUMED_MICROS` and `RL_DRAIN_END_MICROS` stamps every run log
   prints, over user bytes equal to the `rocksdb.bytes.written` ticker less
   the load's exact bytes (`load_operations × (key + value + 16)`: fixed
   record size plus the WriteBatch framing of one Put); `stall_seconds` from
   the internal-stats `Cumulative stall` line; and latency from db_bench's
   own per-benchmark histograms after the mixgraph line, which time the whole
   scan and exclude the load, with p95 interpolated from the printed buckets
   as db_bench derives its own percentiles. The whole-run values and the
   source of each figure are kept beside them (`*_whole_run`, `*_source`).
   On the smoke arm the reward's run-to-date $W$ and the evaluator's
   measured-phase $W$ agree to **0.015%** with the 16-byte framing.
2. **The manifests are regenerated on the corrected evaluator.** Stage 06
   re-selected from the existing sweep; the comparators did not move
   (trigger 2 / 4 / 2 at base 16 MiB), so the guard calibration binds through
   an explicitly accepted prior selection hash
   (`--accept-selection-sha256`, recorded in the output), the guard's level
   limits are unchanged at $T$ = 6 and 10 and differ at $T{=}2$ only in a
   debt-ratio bound's fourth digit and the physical-bytes bound's fifth. The
   references moved: $W$ 8.013 / 7.812 / 10.493 → **7.919 / 8.484 / 13.451**;
   stall fraction 0.258 / 0.404 / 0.459 → **0.00011 / 0.00006 / 0.0014**; scan
   latency average 23.8 / 13.8 / 10.3 us → 477 / 289 / 256 us; write latency
   average 11.4 / 11.6 / 11.9 us → 1.68 / 1.59 / 1.70 us. Manifest SHA-256:
   $T$=2 `d73012e3…` → `41fb57c5…`, $T$=6 `929e3a42…` → `ec5dad11…`,
   $T$=10 `274ba875…` → `4b263068…`. The previous selection and calibrated
   manifests and hull files are kept under `deprecated/pre-d11-2026-09-23/`.
3. **D-10's framing constant is withdrawn** (16 bytes, the
   `WriteBatchInternal::ByteSize` value, measured 15.8), and **the reward's
   stall term is off** until a manifest carries a stall reference in the
   telemetry's own unit: the frame's stall accounting (0.647 s on the smoke
   arm) and the internal stats' (0.033 s) are different quantities, and a
   hinge between them would rail on a run with no stall problem. Stall stays
   an acceptance criterion at the evaluator and a live guard term.

**The record re-scored, same artifacts, measured phase.** Paired against the
same-seed static twins by `frontier_analysis.paired_comparison`; the
whole-run column is what the entries above recorded.

| set | cell | n | $\Delta W$ measured | $\Delta W$ whole-run (as recorded) | $\Delta R$ |
| --- | --- | ---: | ---: | ---: | ---: |
| D-4 `prior_only` | $T{=}2$, trig 2 | 3 | **+2.75** [+0.20, +5.30] | +0.09 [−0.08, +0.27] | +1.14 |
| D-4 `prior_only` | $T{=}6$, trig 4 | 3 | **+6.32** [+3.14, +9.50] | +2.07 [+0.71, +3.43] | −5.69 |
| D-4 `prior_only` | $T{=}10$, trig 2 | 3 | +0.45 [−0.94, +1.84] | +0.10 [−0.33, +0.53] | +0.74 |
| D-5 band | $T{=}2$, trig 4 | 3 | **+14.43** [+13.75, +15.11] | +2.94 [+1.97, +3.92] | −4.53 |
| D-5 band | $T{=}10$, trig 4 | 3 | **+6.62** [−1.83, +15.07] | +2.11 [+1.26, +2.96] | −7.07 |
| D-7 `rl` | $T{=}2$ | 3 | **+24.4** [+18.8, +30.1] | +6.09 | +3.63 |
| D-7 `rl` | $T{=}6$ | 3 | **+42.7** [+33.2, +52.2] | +13.18 | −10.36 |
| D-7 `rl` | $T{=}10$ | 3 | **+13.1** [+4.8, +21.4] | +4.57 | +6.01 |
| D-9 smoke `rl` | $T{=}2$ | 1 | +5.47 | +0.45 | +3.20 |
| D-10 smoke `rl` | $T{=}2$ | 1 | +4.44 | +0.49 | −0.12 |

Three readings change. **D-4's "native at trigger 2" is withdrawn at
$T{=}2$**: the prior costs +2.75% write there, with an interval that excludes
zero; it holds at $T{=}10$. **The L0 band costs 6–14% of measured-phase
write**, not 2–3%, so D-5's conclusion that the prior misses the budget
strengthens by a factor of three to five. **The learned arms so far are 13–43%
over parity**, not 5–13%, and neither smoke arm reached parity; their
"write parity" was the load. Every earlier write, stall or write-latency
figure in the `Assoc` record is a whole-run figure and is superseded by this
table and by the corrected evaluator's output.

**The hulls, re-extracted.** C-1 still passes at every ratio: 8 / 7 / 8 hull
points of 12 at $T$ = 2 / 6 / 10, of which 8, 7 and 7 were on the previous
hulls. **C-2 at $T{=}6$ flips from pass to fail.** The reason is that the
honest write axis is noisier: the comparator's run-to-run coefficient of
variation is 1.8% / 1.1% / 0.75% measured against 0.20% / 0.33% / 0.27%
whole-run. The load's stable 72% share had been making every write interval
look three to nine times tighter than it is. The 14.7 justification of ten
repeats was computed on the diluted axis and does not hold as stated: a 2%
write margin is undecidable at three repeats on this axis, and at ten the
unpaired half-width is about 1.3% at $T{=}2$. The C-2 top-up decisions
(`15_top_up_hull.py`) are not re-run here and are owed. **The cross-$T$
pooled hull still holds 14 of 38 points, but only 9 of them are the same
points**: its composition moves from 8 / 4 / 2 at $T$ = 2 / 6 / 10 to
**4 / 7 / 3**, and $T{=}14$ and $T{=}20$ still contribute none, so P1-14's
conclusion that the ratio axis is bounded above survives while the frontier
within it is redrawn. Any C-6 verdict must be read against this hull, not
the 2026-09-21 one.

**Not re-scored, and why.** E-1 and E-5 are frame statistics and unaffected.
C-5 is a space measurement and unaffected. The 2026-09-19 C-3 verdict is on
the uniform workload and superseded already.

**The oracle parity gate, re-scored on the measured phase (same day).**
Stage 04 rebuilt the parity root's summary on the corrected evaluator and
stage 09 ran under the suite's own flags (`--minimum-pairs 10
--minimum-envelope-pairs 10 --admission-latency-limit-micros 5000`),
written to `results/oracle-parity-assoc-2/oracle_parity-measured-phase.json`
beside the recorded file. Verdict unchanged: `undecided`, `failed_checks: []`.
The write envelope is **+0.68% [−2.56%, +3.92%]** against a ±5% limit
(recorded +0.31% [−0.54%, +1.16%]), still passing at ten pairs with seven
required (three before) — the same 3.5× widening the comparator scatter
predicts. Point-read, seeks, L0–L1 input size, pending debt, observation
health and admission latency are byte-identical to the recorded values, as
they must be. Measured-phase stall duration is 0.000 s on every one of the
twenty 1M arms, so `stall_duration` stays `no_allowance_configured` with
nothing to allow for.

**C-2 on the measured axis: computed, decision open.** `15_top_up_hull.py`
over the re-extracted hulls (`gate1/topup.tsv`,
`gate1/hull_indistinguishable.tsv`; the 2026-09-21 files are under
`deprecated/pre-d11-2026-09-23/gate1/`) asks for **26 additional `regular`
arms across six configurations, about 1.3 h**, and finds five pairs it cannot
resolve: three $T{=}2$ points unresolvable at any $n \le 200$, one $T{=}2$
point needing 54 repeats against 7, and one $T{=}10$ point needing 20 against
5. The preregistered cap is ten additional arms per configuration
*cumulative*, and 14.16 records that the tool enforces it per pass, so the
list must be scored by hand against what each configuration already received
on 2026-09-21 before any arm runs. Whether to spend that time or to record
C-2 failed on the measured axis, as it was recorded failed on uniform, is a
decision for its own dated entry; nothing in D-11 or the learner run depends
on it.

**Predictions, recorded in advance.** D-10's scope and its predictions 2 to 5
and 7 to 10 are restated unchanged; 1 and 6 change; 11 is new. Scored by
`d11_learner_run.sh`, whose first phase regenerates the manifests exactly as
above and stops if the comparator selection moves.

1. **Instrument identity.** Per arm, the reward's run-to-date $W$ equals the
   evaluator's measured-phase $W$ within **0.5%**, and the evaluator reports
   `measured_phase_event_log` as the source on every arm.
6. **Write moves toward parity, measured.** Mean paired $\Delta W$ for `rl` at
   most **+6%** at $T{=}2$ and $T{=}10$ and **+10%** at $T{=}6$, against
   +24 / +43 / +13% under D-7; `unconstrained_rl` below `rl` on $W$ in every
   cell. The smoke arms sit at +5.5% and +4.4%, so the trigger-2 bound is
   deliberately tight.
11. **Measured-phase stall stays negligible.** Every learned arm's
    `stall_seconds` is below 1.0 s (references 0.02–0.14 s; the D-7 arms'
    measured stalls were 0.00–0.10 s), with the reward's stall term off.

**Falsification.** If prediction 1 fails, the reconstruction and the reward
disagree on the same phase and one of them is wrong in a way this entry has
not found. If prediction 6 fails at the trigger-2 cells while 3 and 5 hold,
the learner's remaining lever costs more write than the budget by relocation
alone, as D-9 said, now measured on the honest axis. If C-2 cannot be
recovered at ten repeats on the measured axis, the write criterion's margin
or repeat count must be re-derived before Gate 5, in its own dated entry.

**Implementation.** `scripts/dbbench_pipeline/04_generate_graphs.py`
(`parse_phase_histograms`, `parse_measured_stall_seconds`, `parse_geometry`,
phase-split flush bytes in `parse_drain`, measured-phase fields in
`collect_arm`), `06_calibrate_live_guard.py` (`--accept-selection-sha256`),
`rl_agent/config.py` (framing 16; stall term gated on a telemetry
reference), `rl_agent/multilevel.py`, `d11_learner_run.sh`. Every downstream
consumer — stage 06, the paired evaluator, `frontier_analysis`, the gate
scorers — reads through `collect_arm` and inherits the definition. No
rebuild; the Hull-0 arms stand and were re-read.

**Evidence.** `results/learner-smoke-d10/10M/T2/rl/{run.log,rocksdb_LOG.txt,learning_health.json}`
(the keys-written count, the internal-stats block, the reward's cumulative);
`deprecated/pre-d11-2026-09-23/` (the superseded manifests and hulls);
`gate1/hull-T{2,6,10}.json` and `gate1/hull-crossT.json` (re-extracted);
`baseline_selection/assoc-v1/10M/T*/baseline_frontier.csv`. To be written:
`results/learner-assoc-d11/`, `results/learner-smoke-d11/`,
`gate1/d11_verdict.txt`, `gate1/d11_conditional.{json,txt}`.

**Smoke gate as scored, 2026-09-23.** One `rl` arm at 10M/$T{=}2$
(`results/learner-smoke-d11/10M/T2/rl`). Prediction 1 holds: reward $W$
8.227 against the evaluator's measured-phase $W$ 8.222, +0.06%, source
`measured_phase_event_log`. **Prediction 2 fails**: $\lambda_{\text{lat}}$
ended at **27.75** against $\le 5$; no multiplier railed ($\lambda_W$ 10.03,
$\lambda_{\text{scan}}$ 0.91, $\lambda_S$ 0.00, $\lambda_{\text{stall}}$ 1.00
with the term off). Predictions 3, 5, 10 and 11 hold on the one arm: zero deep
releases below $\varphi$ 0.95 in 2,624; depth 9 against the twin's 9;
$\lambda_W$ 10.03 $<$ 20 and $\lambda_{\text{scan}}$ 0.91 $<$ 10;
measured-phase stall 0.000 s. The gate stopped the matrix, so 4 and 6–9 are
unscored. The arm against its same-seed twin: $W$ +6.5% (8.222 against
7.718), $R$ +1.9% (4.643 against 4.558), seeks −2.7%, get average −5.9%, scan
average −1.6%, write average +14.7%. The cause is D-12: D-10's falsification
clause for this prediction — "a latency term is still mis-specified" — fired,
and the mis-specification is in the term's time base, not its unit.

**Timing, stated plainly.** The D-10 and D-11 work was still uncommitted when
the node ran this smoke arm, so this entry's predictions carry no commit date
that precedes it; on the D-4 precedent D-11 is a weaker record than D-1
through D-3 and says so. The matrix the predictions are written for has not
run.

### D-12, 2026-09-23 — the latency multiplier's slack is measured from the warm-up against the baseline's own trajectory

**Recorded after D-11's smoke gate stopped on prediction 2, and before any
further arm runs.** D-11's verdict stands as scored above and nothing in it
is re-scored. D-10's falsification clause fired and this entry is its
consequence, as D-10 was D-8's.

**What was found, on the two smoke arms' own frame logs.** The worst
operation for the multiplier was write latency in both arms. Its *window*
value was under the limit from 10 s to the end; its *run-to-date* value was
not, because of the first two seconds after `rlresume`:

| arm | L0 files at resume | stall fraction, s 0 / s 1 | window write / limit, s 0 / s 1 | run-to-date write / limit at 30 s | at end | window write / limit, $t \ge 10$ s | $\lambda_{\text{lat}}$ at end |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| D-10 `rl` | 16 | 0.68 / 0.00 | 15.5 / 0.85 | 1.18 | 0.904 | 0.828 | 0.10 |
| D-11 `rl` | 22 | 1.00 / 0.47 | 32.2 / 3.33 | 1.65 | 1.019 | 0.837 | 27.75 |

The tree the suspended controller inherits from the bulk load holds L0 above
the slowdown trigger of 20 in the D-11 run, the write controller stalls, and
two seconds of stalled writes carry the latency sum of tens of seconds of
normal ones. The D-10 arm's cumulative crossed back under the limit at about
70 s and its multiplier decayed; the D-11 arm's never did. From second 2 on
the two arms are indistinguishable on write latency, and both compacted 78–82%
of due L0 frames through the backlog; the policy is not the cause.

**The baseline reads the same way.** The three $T{=}2$ oracle calibration
arms carry the same transient, so measured against their own whole-run
reference — the D-10 form — the baseline's cumulative write latency is:

| elapsed | 0.5 s | 10 s | 30 s | 60 s | 90 s | 120 s | 150 s | 160 s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| pooled cumulative / limit | 15.7 | 2.95 | 1.60 | 1.20 | 1.09 | 1.03 | 0.99 | 0.98 |

Any policy, the oracle included, reads a positive slack for 120–150 s of a
160 s run under that form. Same metric, same instrument, different elapsed
time: the sixth instrument of the family, in a new variant.

**Simulation, validated first.** The multiplier update was re-implemented
offline and reproduces the recorded 0.10 and 27.75 from the logged slack.
Then, on the same frames:

| slack form | D-10 arm $\lambda_{\text{lat}}$ at end | D-11 arm |
| --- | ---: | ---: |
| whole-run cumulative against the whole-run limit (D-10) | 0.10 | 27.75 |
| whole-run cumulative against the baseline's cumulative at the same elapsed time | 0.00 | 7.10 |
| **since-warm-up cumulative against the baseline's since-warm-up cumulative at the same elapsed time** | **0.00** | **4.04** (4.10 through the regenerated manifest and the real lookup) |

The middle row shows why the reference alone is not enough: the D-11 arm's
transient was about 10% heavier than the pooled calibration transient, and a
whole-run cumulative carries that difference to the end of the run. The
transient's size is the load's own scatter — the two smoke arms ran the same
seed and inherited 16 and 22 L0 files — so nothing the policy does can move
it, and the slack that drives the multiplier should not contain it. The
residual on the D-11 arm under the last row is scan latency (35 samples per
50 ms window), up to 9% above the calibration arms' at 45–90 s and 1% under
at the end; the evaluator scored the arm's scan latency below every twin, so
that is seed scatter against unpaired calibration seeds.

**Decisions.**

1. **The calibrator writes the baseline's since-warm-up trajectory.**
   `06_calibrate_live_guard.py` reads `latency_windows.jsonl` — which carries
   `time_micros` and per-operation count and latency sum per 50 ms window —
   and writes `latency_reference_trajectory`: the cumulative average per
   operation over windows at or after `--warmup-seconds` (30 s, the reward's
   `RL_LAMBDA_WARMUP_SECONDS`) of elapsed time since the first Get window, on
   a 1 s grid, pooled count-weighted over the calibration runs as of each grid
   point, so the last point is the pooled since-warm-up average and is also
   written as `*_latency_avg_ns_telemetry_steady_reference`. The whole-run
   `*_telemetry_reference` and `_limit` are unchanged.
2. **The latency multiplier's slack is since-warm-up against that
   trajectory.** Per operation, $\text{slack}(t) = \bar\ell_{[30,t]} /
   \big((\text{limit}/\text{reference}) \cdot \text{traj}(t)\big) - 1$, with
   the same 2% margin as before; the multiplier takes the worst operation, as
   before; before the warm-up ends the slack is 0, where the multiplier is
   held anyway. **The priced term is unchanged**, so the reward's run sum is
   still the constraint the evaluator scores. The superseded slack is still
   computed and logged per frame as `latency_slack_whole_run`, and the run
   summary records the slack form, the since-warm-up averages and the
   trajectory's end point. The reward refuses a manifest whose warm-up differs
   from its own; `RL_LATENCY_SLACK_SINCE_WARMUP=0` restores D-10.
3. **The manifests are regenerated.** Guard fields byte-identical, four
   fields added. SHA-256: $T$=2 `41fb57c5…` → `4fbe8122…`, $T$=6
   `ec5dad11…` → `8d3223be…`, $T$=10 `4b263068…` → `4c0df483…`; the D-11
   manifests are kept under `deprecated/pre-d12-2026-09-23/`. Steady
   references (ns), against the whole-run references in brackets:

   | $T$ | get | scan | write |
   | ---: | ---: | ---: | ---: |
   | 2 | 7,775 (7,801) | 481,521 (460,780) | 1,059 (1,266) |
   | 6 | 5,755 (5,895) | 283,996 (289,357) | 1,018 (1,089) |
   | 10 | 5,382 (5,477) | 249,724 (255,392) | 1,013 (1,192) |

   The write column shows the transient's weight in the whole-run reference:
   16–20%.

**Not taken, and why.** (a) $W$'s multiplier is transient-driven by the same
mechanism — the reward's run-to-date $W$ was 10.4 at 30 s against a bound of
8.08 and 8.23 at the end, so $\lambda_W$'s 10.03 is mostly the load's backlog
compacting under the controller's nominal authority — but D-10's prediction
10 held and its falsification clause did not fire. A trajectory reference for
$W$ needs the baseline's user bytes by time, which the window log's per-window
write count times the mean record size could supply against physical bytes
from the event log; that is the candidate instrument for its own entry.
(b) A dead-band on the slack at the calibration runs' own between-run
dispersion would be chosen after seeing 4.04, and is not taken. (c) The
latency multiplier's step size is left at D-9's, for the same reason.

**Predictions, recorded in advance.** D-11's scope; its predictions 1 and
3–11 are restated unchanged; 2 is restated in D-10's words; 12 and 13 are
new.

2. **Multipliers behave.** No multiplier reaches `LAMBDA_MAX` in any arm; at
   least one ends below its run maximum in every arm; and
   $\lambda_{\text{lat}}$ ends at or below 5 in every arm.
12. **The transient was the cause, on every arm.** The superseded whole-run
    form, simulated offline from the logged `latency_slack_whole_run` with
    the same update rule, ends above the recorded $\lambda_{\text{lat}}$ in
    every arm. If the two agree on some arm, the transient did not matter
    there and this entry's reading over-reached.
13. **The residual is seed scatter, bounded.** Each operation's since-warm-up
    cumulative at run end is within $\pm 10\%$ of the trajectory's end point
    in every arm. A violation means the residual is not seed scatter and must
    be read as a policy effect on that operation's latency, or as a defect in
    the trajectory.

**Falsification.** If $\lambda_{\text{lat}}$ ends above 5 in this form, the
term's residual is not the transient and this entry is an incomplete repair;
prediction 13 says whether the reference's own seed dispersion is where it
lives. If prediction 12 fails on an arm, the reading of the D-11 arm's 27.75
as transient-driven does not generalise to that arm.

**Implementation.** `scripts/dbbench_pipeline/06_calibrate_live_guard.py`
(`since_warmup_trajectory`, `--warmup-seconds`,
`--trajectory-resolution-seconds`, the manifest block and the three steady
references), `rl_agent/config.py` (`LATENCY_SLACK_SINCE_WARMUP`,
`_load_latency_trajectory`, `latency_reference_ns`), `rl_agent/multilevel.py`
(the since-warm-up accumulators, the slack, the logged fields,
`reward_state`), `d12_learner_run.sh` (the D-11 driver: phase 0 archives the
D-11 manifests and regenerates; the smoke gate and the scorer report the
superseded form simulated offline beside the recorded one, and score 12 and
13). Exercised offline before commit: the $T{=}2$ manifest regenerated with
every guard field unchanged and four fields added; the trajectory loaded
through the real config and both smoke arms replayed through the real lookup
(0.00 and 4.10); and the reward function driven on a synthetic frame stream
with a two-second 10× write transient — the slack is 0 through the warm-up,
the since-warm-up average excludes the transient (1,072 ns against 1,071
expected), the per-operation slacks equal the hand calculation, the multiplier
moves only after the warm-up, and the summary carries the new fields. Python
and shell only; no rebuild; the Hull-0 stands.

**Evidence.** `results/learner-smoke-d11/10M/T2/rl/{io.jsonl,learning_health.json}`,
`results/learner-smoke-d10/10M/T2/rl/{io.jsonl,learning_health.json}`,
`results/guard/assoc-v1/calibration/10M/T2/repeat-0{1,2,3}/oracle/latency_windows.jsonl`,
`deprecated/pre-d12-2026-09-23/baseline_slo/`. To be written:
`results/learner-assoc-d12/`, `results/learner-smoke-d12/`,
`gate1/d12_verdict.txt`, `gate1/d12_conditional.{json,txt}`. The smoke retry
writes to `results/learner-smoke-d12-2/` (see the first attempt below).

**First smoke attempt, 2026-09-23: invalid; two implementation defects,
corrected before the next.** One `rl` arm at 10M/$T{=}2$
(`results/learner-smoke-d12/10M/T2/rl`, kept as the record; the RocksDB LOG is
in the preserved database under `.dbbench_pipeline_dbs/learner-smoke-d12/`).
The runner's learning-health gate failed it on `no_reward_invalid_intervals`
(862, all `rejected_manifest`) and `full_horizon_replay` (0 of 29,052
transitions), so the driver stopped before the matrix. **No D-12 prediction
is scored from this arm and none is changed.**

1. **The C++ side rejected the manifest.** The RocksDB LOG reads *"RL baseline
   SLO manifest rejected; learned trigger will use conservative all-due native
   eligibility: baseline SLO schema, fingerprint, or required limits
   mismatch"*, and every diagnostics line carries `manifest_invalid=1`. Cause:
   `rl_safety_manifest.cc` reads each key by its **first textual occurrence**
   in the file, not the top-level one; the calibrator writes keys sorted, so
   the trajectory block's own `"schema_version": 1` came before the top-level
   `"schema_version": 2`, the parser read 1, and the whole manifest was
   refused. The Python loader parses JSON properly and accepted it, so the
   reward ran constrained while the guard ran its all-due fallback: 38,191
   `kEmergency` overrides, 3,745 of 41,184 decisions executed differently from
   the policy's choice. **Correction:** the nested key is
   `trajectory_schema_version`, and the calibrator now refuses to write a
   manifest in which any key `RLSafetyController::Parse` reads occurs other
   than exactly once (`CPP_FIRST_MATCH_KEYS`, in lockstep with `Parse`). It
   refuses the rejected manifests and accepts the corrected ones; an
   emulation of the first-match reader reads every key correctly on the
   corrected manifests and reproduces the `schema_version` = 1 misread on the
   rejected ones. Guard fields and trajectory data are byte-identical; only
   the key name changed. SHA-256: $T$=2 `4fbe8122…` → **`9e0faa5e…`**, $T$=6
   `8d3223be…` → **`2e19c6fc…`**, $T$=10 `4c0df483…` → **`52079b1e…`**;
   the rejected manifests are kept under `deprecated/d12-rejected-2026-09-23/`.
2. **The reward counted load writes in its first frame.** On this arm the
   reward's run-to-date $W$ was 7.838 against the evaluator's measured-phase
   7.923, −1.07%, which would fail prediction 1. The whole gap is in the
   denominator: the first controlled frame carried 14.7 MB of user writes in
   50 ms, about 14,100 records, against 0.36–0.38 MB on the D-10 and D-11
   arms. The C++ worker empties the telemetry window on every suspended tick,
   but the first controlled frame's window opens at the *last suspended
   tick*, not at `rlresume`, so load writes after that tick land in frame 1,
   while the evaluator's phase starts at `RL_CONTROL_RESUMED_MICROS` and
   excludes them. It is a race, and nothing in it involves the manifest.
   **Correction:** the reward leaves the first frame's flows out of every
   run-to-date total, marginal term and window average
   (`RL_REWARD_DROP_RESUME_FRAME`, default on); its elapsed time and tree
   state still count, and what it carried is reported as
   `resume_frame_dropped`. The cost is at most one tick of mixgraph, about
   0.03% of the phase. Replayed from the logged frames: this arm −1.07% →
   **−0.03%**, the D-11 arm +0.06% → **−0.01%**. The principled fix — consume
   the telemetry at the resume instant in C++ — needs a rebuild and is
   deferred with the rest of Gate 2.

**Two observations from this arm, recorded before any valid D-12 arm and used
for nothing.** The guard's fallback forced every due level open, so the plant
was close to native RocksDB. (a) Under the D-12 slack form
$\lambda_{\text{lat}}$ ended at **4.63**; the superseded whole-run form,
simulated offline on the same frames, ends at **17.86**. That supports the
transient diagnosis on a second arm. But it also puts a near-native plant
0.37 under prediction 2's threshold of 5, driven by get and scan latency
5.6% and 5.1% above the trajectory's end point. The calibration arms ran on
2026-09-21 and this arm two days later on a machine that has run over a
hundred arms since, and latency is a warm-cache figure (A-Impl-2), so
between-session drift of this size is expected; prediction 13's ±10% band
covers it and prediction 2 may not. (b) On $W$ this near-native arm sits
+2.7% over its same-seed twin (7.923 against 7.718) and $R$ +3.6%, against a
measured-phase write coefficient of variation of 1.8% at $T{=}2$ (D-11). The
earlier smoke arms' +4.4% to +6.5% should be read against that.

---

## 2. Gate verdicts as measured

### D-12 scored, 2026-09-23: the first evidential learner run, and the answer is no

**Verdict.** The instrument is right for the first time, and under it the
learned trigger does not improve point reads at write parity in any cell.
Nineteen arms — the smoke retry (`results/learner-smoke-d12-2`) and the
18-arm matrix (`results/learner-assoc-d12`, `rl` and `unconstrained_rl`,
10M × $T$ = 2/6/10 × seeds 1–3) — on binary `9b9321b1…`, manifests
`9e0faa5e…` / `2e19c6fc…` / `52079b1e…`, every arm through the
learning-health gate with the manifest accepted by the C++ parser. Three
repeats is a mechanism count; no criterion carrying a 2% margin is accepted
from it.

**Audit correction, same day (`docs/AUDIT_2026-09-23_D12_AND_PATHWAY_A.md`).**
Two readings below are wrong and are corrected here rather than rewritten.
(1) *The $T{=}2$ write excess is not a learned deferral lever.* All of it,
+0.68 GB per run, falls in the first 30 s after `rlresume`, before the first
gradient step (38–42 s). There Boltzmann exploration over a prior that gives
every due deep level the same +0.20 advantage deferred 43–45% of due L3–L5
frames against the greedy choice while the post-load backlog drained. After
30 s the arm writes 0.07 GB less than its twin and releases every deep level
at $\varphi$ = 1.00. The "L4/L5 at 2–2.6× target" releases all fall in that
window. (2) *The rising merge survival is not "overwrites absorbed higher
up".* The early merges combined bulk-load data with bulk-load data, and the
keys are unique, so nothing could drop. In addition, the $T{=}2$ twins ran on
2026-09-20, and the same trigger-4 configuration and seeds write +2.9% to
+4.1% more in later sessions ($T{=}6$ and $T{=}10$ show no such drift), so part
of the $T{=}2$ $\Delta W$ and $\Delta R$ is session drift. D-9's falsification
clause is therefore **not** established by this run; the predictions table
stands as scored. E-5 is 0–1.4% after the first 30 s, and $\lambda_W$ ($T{=}2$)
and $\lambda_{\text{scan}}$ ($T{=}10$) end at 0 when measured from the warm-up.

**Timing, stated first.** D-10, D-11 and D-12 were still uncommitted when
this run started, as they were for the D-11 smoke and the first D-12 attempt,
so none of their predictions carries a commit date that precedes the arms it
governs. The predictions below are scored exactly as written in the working
tree before the run; git cannot prove that, and D-10 through D-12 are
therefore weaker records than D-1 through D-3, on the D-4 precedent.

**The instrument, validated.** Reward and evaluator agree on measured-phase
$W$ to within 0.10% on every arm (prediction 1). No deep release below
$\varphi$ 0.95 anywhere (3). Populated depth equals the twin's in every arm,
9 / 5 / 5 (5) — the first learned arms in the project's history whose tree
did not deepen. Measured-phase stall at most 0.19 s (11). No multiplier
rails. The resume frame the reward now drops carried 6.8 MB on the smoke
retry, so the race D-12's first attempt exposed recurs and the correction
was needed.

**The arms against their same-seed static twins** (`paired_comparison`,
relative difference, 95% interval, seeds 1–3):

| cell | arm | $\Delta W$ | $\Delta R$ | $\Delta$seeks | $\Delta S$ |
| --- | --- | ---: | ---: | ---: | ---: |
| $T{=}2$ | `rl` | **+6.64** [+1.04, +12.24] | **+3.03** [−0.58, +6.64] | −1.83 [−2.98, −0.68] | −1.08 |
| | `unconstrained_rl` | +5.74 [−1.87, +13.36] | +2.41 [+0.37, +4.45] | +1.01 | −0.59 |
| $T{=}6$ | `rl` | +9.71 [−11.93, +31.35] | −7.07 [−21.25, +7.12] | −7.48 | +0.08 |
| | `unconstrained_rl` | +12.73 [−18.84, +44.29] | −9.88 [−21.12, +1.35] | −10.53 | −0.02 |
| $T{=}10$ | `rl` | **−0.56** [−2.11, +1.00] | +1.12 [−2.40, +4.64] | +1.66 | +0.07 |
| | `unconstrained_rl` | −1.66 [−4.16, +0.85] | +3.71 [−6.83, +14.25] | +3.96 | +0.05 |

For reference on the same seeds and the same evaluator: `prior_only` (D-4)
+2.75 / +6.32 / +0.45% $W$ and +1.14 / −5.69 / +0.74% $R$; the D-7 learner
+24.4 / +42.7 / +13.1% $W$. The realignment of D-9 to D-12 removed three
quarters or more of the learner's write excess in every cell, and what
remains is no better than the hand-written prior.

- **$T{=}2$: worse than plain RocksDB on both axes**, and worse than the
  prior on writes (paired by seed, +3.78% [+0.40, +7.17]) with reads
  undecidable against it. Dominated on means by its own twin. The write interval
  straddles the 2% margin, so non-inferiority is undecidable rather than
  failed; its lower bound excludes zero.
- **$T{=}6$: reads bought with writes, at a worse rate than the prior on
  means** — 0.73 units of read per unit of write against the prior's 0.90;
  paired against the prior both axes are undecidable. The wide
  intervals are one variable: the number of below-trigger L0 compactions the
  learner chose, 300 / 123 / 101 on `rl` seeds 1–3, giving $\Delta W$ +19.0 /
  +8.3 / +1.8% and $\Delta R$ −13.7 / −4.0 / −3.6%. One `unconstrained_rl`
  arm (seed 3, 90 such compactions) beat its twin on both axes, −1.9% $W$ and
  −4.7% $R$; one run is not a result and it is recorded as the only such
  point.
- **$T{=}10$: native.** The write constraint passes at the 2% margin — the
  first learned arm in the project to do so in any cell — and reads do not
  move. Release fill, merge survival and job counts match the twin level by
  level.

**Where the $T{=}2$ write goes, measured.** Compaction output in the measured
phase is +7.4% over the twin, and 0.50 of its 0.60 GB is at L3 and L4. The
learner holds deep levels far past due: fill at release has median 2.56 at L4
and 1.97 at L5 with 75–80% of releases above 1.5, against 1.00 and 0% for the
twin. Those late merges keep **more** of their input, not less — merge
survival rises from 0.79 to 0.84 at L3, 0.81 to 0.97 at L4 and 0.88 to 0.995
at L5. Holding a deep level back on `Assoc` elides nothing, because the
overwrites are absorbed at L0–L2 before they reach it; it only relocates and
enlarges the rewrite. L0 is deferred on about a quarter of its due frames
(0.24–0.26 per arm), the likely source of the +3% read cost, since a deep
level is one sorted run however full it is (A4). **This is D-9's falsification clause, measured
rather than inferred:** with the mask reaching the plant (3) and depth flat
(5), the learner's deferral lever costs write by relocation alone with no
elision gain, and on this workload it is worthless at parity. At $T{=}10$ the
learner does not pull it.

**Why the guard lets it happen at $T{=}2$.** The calibrated score limits at
L2–L4 are 12.9 / 12.4 / 11.9 at $T{=}2$, because the calibration runs include
the post-load backlog in which native RocksDB itself releases L3 at twelve
times its target (twin p90 12.2). A steady-state hold at 2–3× is therefore
far inside the envelope. It is the same root as D-12 — a whole-run statistic
carrying the startup transient — now in the guard's limits rather than the
reward. Not re-fitted here; recorded for its own entry.

**Predictions as scored.** The driver's scorer printed 1–9, 12 and 13;
10 and 11 were scored by hand from the same logs.

| # | Prediction | Result |
| --- | --- | --- |
| 1 | reward $W$ = evaluator $W$ within 0.5% | **PASS**, max gap 0.10% |
| 2 | no rail; one multiplier below its peak; $\lambda_{\text{lat}} \le 5$ | **FAIL** — 5.08, 8.24, 7.92 on three `unconstrained_rl` arms; all nine `rl` arms and the smoke arm $\le$ 4.86 |
| 3 | zero early deep releases; L0 below-trigger at $\ge$ 2 files | **PASS** |
| 4 | $\lambda_W$ largest at end of every `rl` arm; falls where $W$ ends inside 2% | **FAIL** / pass — at $T{=}10$ $\lambda_{\text{scan}}$ (7.1–8.0) binds and $\lambda_W$ ends 0.45–2.2, below its peak |
| 5 | $\delta L \le 0$ | **PASS**, 0 / 0 / 0 |
| 6 | `rl` $\Delta W \le$ +6 / +10 / +6%; `unconstrained_rl` below `rl` every cell | **FAIL** — +6.64% at $T{=}2$; $T{=}6$ +9.71 and $T{=}10$ −0.56 hold; `unconstrained_rl` above `rl` at $T{=}6$ |
| 7 | `rl` $\Delta R \le$ +2% at $T{=}2$, $T{=}10$ | **FAIL** at $T{=}2$ (+3.03); $T{=}10$ +1.12 holds |
| 8 | E-5 above 1% in $\ge$ 2 cells | **PASS** — 0.149–0.171 / 0.061–0.082 / 0.085–0.096 |
| 9 | L0 lever real, larger when unguarded | **PASS** (0.239 against 0.228 of due-L0 frames deferred) |
| 10 | $\lambda_W < 20$, $\lambda_{\text{scan}} < 10$ on `rl` | **FAIL** — $\lambda_W$ 20.95 on $T{=}6$ seed 1; $\lambda_{\text{scan}}$ max 8.03 holds |
| 11 | measured-phase stall $<$ 1 s | **PASS**, max 0.19 s |
| 12 | whole-run form ends above the recorded $\lambda_{\text{lat}}$ | **PASS** on all 18 matrix arms; **FAILS on the smoke retry** (whole-run 0.00 against 2.61) |
| 13 | since-warm-up latency within ±10% of the trajectory end | **FAIL** on one of 18 (`unconstrained_rl` $T{=}6$ seed 3, scan 1.107); holds on the other 17 and the smoke arm |

D-12's falsification clause for prediction 2 applies to the unguarded arms
only: their residual $\lambda_{\text{lat}}$ is scan latency 3–11% over the
calibration trajectory, a seed or session effect or a policy effect on scans,
not the transient. On the enforced arms the repair holds.

**Pathway D criteria, first scored.**

| # | Criterion | $T{=}2$ | $T{=}6$ | $T{=}10$ |
| --- | --- | --- | --- | --- |
| D-1 | residual tail $<$ 20 | pass (16.5) | pass (9.6) | **fail** (26.9 at L2) |
| D-2 | final TD loss $<$ 2× return scale | pass (12.6 vs 29.1) | pass (35.0 vs 125), TD loss **rising** (trend 2.2) | pass (2.6 vs 21.0) |
| D-3 | multipliers plateau on feasible cells | **fail** — $\lambda_W$ monotone to the end (11–15) | **fail** — monotone (12–21) | pass — $\lambda_W$ peaks mid-run and falls |
| D-4 | $\lambda_W$ diverges only where B.1 ceiling $< d'$ | **fail** — rises where the constraint is feasible | **fail** | pass |
| D-5 | flip $>$ 0.1 per level; beats the prior | flip pass except L7 (0.018); **loses to the prior**: $W$ +3.78% [+0.40, +7.17], $R$ undecidable | flip pass; undecidable against the prior; worse exchange rate on means | flip fails at L1 (0.066); $W$ −1.00% [−1.41, −0.59] against the prior, $R$ equal |

The monotone $\lambda_W$ at $T{=}2$ and $T{=}6$ is not the infeasibility
signal D-4 describes: the static twin meets the bound by definition. The arm's
run-to-date $W$ never returns under its bound within a 160 s run, and most of
the multiplier's level is accumulated while the post-load backlog holds that
ratio at 13–20 — the transient D-12 recorded for $W$ and left for its own
entry.
The scan multiplier at $T{=}10$ is the same effect: run-to-date seeks per
scan start at 10–11 against a bound of 3.69, fall under it only in the last
6–10% of the run, and every arm *ends* under it, yet $\lambda_{\text{scan}}$
finishes at 7.1–8.0 — which is why prediction 4 fails there. All three flow
multipliers inherit the startup transient; D-12 removed it from latency only.
The bias runs toward restraint: an inflated $\lambda_W$ pushes the learner
toward fewer writes, so the $T{=}2$ write excess arose despite it.

**Latency, under P0-4's rule** (pass if the upper bound $\le$ +2%, fail if the
lower bound $>$ +2%, otherwise undecidable): at $T{=}2$ write p99 fails
(+9.67% [+7.77, +11.56]) and the rest pass or are undecidable; at $T{=}6$ and
$T{=}10$ every latency check is undecidable or passes. The write-p99 figure is
not a clean policy effect: the twins ran on 2026-09-21 and these arms two days
later, latency is warm-cache (A-Impl-2), and `prior_only` shows the same
direction (+6.5%). Three same-session `regular` arms at $T{=}2$ would settle
it.

**The guard.** E-5's conditional rate is 6 to 17 times its 1% limit, with
`pressure` the dominant term, as D-8 and D-9 predicted. But the guard no
longer decides the outcome: `rl` and `unconstrained_rl` differ by less than
either differs from `regular` on $W$ at $T{=}2$ and $T{=}6$ (0.07 against 0.45,
0.25 against 1.08 absolute) — E-4's form, satisfied informally at three
repeats.

**Against the static frontier** (`frontier_analysis.py --policy-results`,
`gate1/d12_position-*.json`, space margin 2%; Hull$_0$ is the comparator for
an arm without a capacity action). Means decide the classification; the
paired interval against each dominator is reported with it.

| cell | arm | per-$T$ hull | cross-$T$ hull (C-6) |
| --- | --- | --- | --- |
| $T{=}2$ | `rl` | **dominated** by its own twin (trigger 2, 16 MiB); paired $W$ [−11.1, −1.3]%, $R$ [−6.4, +0.5]% | **dominated**, decided: $T{=}6$ trigger 4 / 8 MiB and trigger 8 / 8 MiB, both intervals below zero on both axes |
| | `unconstrained_rl` | **dominated** by its twin; $R$ decided, $W$ not | dominated on means by the same two; undecidable |
| $T{=}6$ | `rl` | non-dominated, between two hull points and 5% above the line joining them | dominated on means by $T{=}10$ trigger 4 / 8 MiB; undecidable |
| | `unconstrained_rl` | non-dominated | non-dominated |
| $T{=}10$ | `rl` | non-dominated — it sits on its twin ($W$ −0.1%, $R$ +0.8%) | non-dominated, same reason |
| | `unconstrained_rl` | non-dominated | non-dominated |

A non-dominated verdict at $T{=}10$ is not a contribution: the policy is its
twin within noise, and the twin is a hull point. The only cell where a
learned arm lies off the static frontier in a way the paired test can see is
$T{=}2$, and there it lies on the wrong side.

**What this settles.** The question D-9 was written to make evidential —
can a learned trigger beat native RocksDB on reads at write parity — is
answered for this action space on `Assoc` at 10M, at three repeats: **no**.
Where the learner defers, deferral costs write without elision (deep levels)
or reads (L0); where it compacts early, it reproduces the prior's L0 band at
a worse exchange rate; where it does neither, it is native. No reward or
instrument repair changes that, because the finding is about the levers, not
the signal. D-9 named the consequence in advance: Pathway A — capacity
expansion, the lever that lets a level hold data without releasing a burst —
is the remaining route, and it is C++ (Gate 2), which means a rebuild and a
re-measured hull.

**Evidence.** `results/learner-assoc-d12/`, `results/learner-smoke-d12-2/`,
`gate1/d12_verdict.txt`, `gate1/d12_conditional.{json,txt}`,
`gate1/d12_learning.json`, `gate1/d12_position-*.json`; twins from
`results/baseline_sweep/`, the prior from `results/paired-assoc/`, D-7 from
`results/learner-assoc/`.

### Oracle parity gate — `Assoc` re-execution, 2026-09-21

**Verdict: PASS.** Ten paired 1M/T=2 `regular`/`oracle` repeats,
`results/oracle-parity-assoc-2`, workload `assoc-v1`, binary
`9b9321b117ad3566…`, objective `5857ad35…`. `failed_checks: []`; the reported
verdict is `undecided` because three checks cannot be decided, which is the
acceptance condition the pipeline itself encodes — `run_full_experiment.sh:193`
and `13_run_preflight_verification.sh:154` both accept exit 0 and exit 2 and
treat anything else as a bridge that is not transparent. It is the same shape
the 2026-08-22 gate was recorded as passing under.

| Check | Result |
| --- | --- |
| `observation_health` | passed — `skipped_ticks` 0 on all ten, `watchdog_expiries` 0 |
| write amp / point-read amp | passed — +0.31% [−0.54, +1.16], +0.88% [−1.17, +2.92] |
| `mean_l0_l1_input_size` | passed — +0.16% [−0.34, +0.67] |
| `maximum_pending_debt` | passed — −0.33% [−0.89, +0.23] |
| `per_level_maximum_score` | passed — +0.35 normalized [0.06, 0.64], limit 1.0 |
| `due_to_admission_latency` | p50 **127 us** on all ten against the 5000 us limit |
| decision rate, held-gate service, due-level authorization, workload identity | passed |
| `sorted_run_seeks_per_scan` | **insufficient_pairs** — 18 required, 10 available |
| `stall_duration` | **no_allowance_configured** — the allowance is still owed by the baseline sweep |

`sorted_run_seeks_per_scan` is **undecidable, not failed**, per the frozen
§3.1 subsidiary decision. Its interval is [−6.70%, +4.62%] against a ±5% limit,
wider than the limit — the power loss D-1 predicted when the `Assoc` mix cut
scans from 32% to 3.5% of operations. It is not re-scored, and no threshold is
widened to accommodate it.

**What this authorizes.** The bridge is transparent on the `Assoc` workload at
this binary, so the Hull-0 sweep and everything behind it may run. **The hull
is bound to this binary**: `frontier_analysis.py:196` keys identity on
`(fingerprint, dbbench_sha256)` and refuses to pool across binaries, so every
step through `prior_only` must run on `9b9321b1…`. A rebuild voids the hull.
This is what invalidated the 2026-09-12 Gate 1 below.

**Re-scored on the measured phase, 2026-09-23 (D-11).** The write envelope
above was a whole-run figure. On the corrected evaluator it is +0.68%
[−2.56%, +3.92%], still inside the ±5% limit at ten pairs; every other check
is unchanged; verdict `undecided` with no failed checks, as before. The
artifact is `results/oracle-parity-assoc-2/oracle_parity-measured-phase.json`.

**Two prior conditions, recorded for completeness.** The first execution of this
gate (`results/oracle-parity-assoc`) failed `observation_health` on an
instrument defect, not the controller: suspended worker ticks were counted as
skipped ticks and the first admission after `rlresume` recorded the tail of the
bulk load. Fixed in submodule `25468bbaa`; the narrative is history Section
14.15. And the gate was first invoked without
`--admission-latency-limit-micros 5000`, which both callers pass, leaving
`due_to_admission_latency` unscored; the artifact was regenerated under the
suite's flags. The verdict is unchanged under either invocation.

### D-7 scored, 2026-09-22: the first learned arms on `Assoc`, and why they are diagnostic

**Verdict: predictions 2, 4 and 5 pass; 1 and 3 fail; and the run does not test
what D-7 wrote it to test.** Eighteen arms, `rl` and `unconstrained_rl`, 10M ×
$T$ = 2/6/10 × three repeats, binary `9b9321b1…`, at the stage-06 comparators.
Every arm passed the learning-health gate with `passed: true` and no errors.

| # | Prediction | Measured | |
| --- | --- | --- | --- |
| 1 | depth does not inflate | $\delta L$ = **+2.0 / +1.0 / +0.7** | **FAIL** |
| 2 | $\lambda_S < \lambda_W$ | **1.00** vs 8.77 / 16.45 / 10.98 | PASS |
| 3 | E-5 measurable and $\le$ 1% | measurable at last; **0.1497 / 0.0693 / 0.0973** | **FAIL, 15× / 7× / 10×** |
| 4 | argmax flip $>$ 0.1 | 0.244 / 0.429 / 0.394 | PASS |
| 5 | no read gain at trigger-2 cells | $\Delta R$ +3.63% / +6.01% | PASS |

| cell | $\Delta W$ vs `regular` | $\Delta R$ vs `regular` |
| --- | --- | --- |
| $T{=}2$ | +6.09% [+3.37, +8.81] | +3.63% [−1.39, +8.65] |
| $T{=}6$ | +13.18% [+10.54, +15.82] | −10.36% [−19.46, −1.27] |
| $T{=}10$ | +4.57% [+1.94, +7.21] | +6.01% [+1.38, +10.64] |

At $T{=}2$ and $T{=}10$ the learner is worse than plain RocksDB on **both**
axes; at $T{=}6$ it trades +13.2% of write for −10.4% of read.

**D-6 is confirmed, cleanly.** $\lambda_S$ sat at exactly **1.00 — its
initial value — in all six arms**, so the space hinge never fired once. Both
of D-6's predictions hold, and the repair it made is not in question.

**Why the run is diagnostic rather than evidential.**
`lambda_latency` reached `LAMBDA_MAX` = 100.0 in **every arm**, six to eleven
times `lambda_write`, and per-frame `latency_excess` was never zero (p50
19.92, mean 20.74, 2,231 frames). The learner was optimising an objective
dominated by a latency term no policy can satisfy, because the hinge compares
a 50 ms window's write-p99 against a whole-run write-p99 limit of 1.02 us on a
distribution whose average (16.5 us) exceeds its P99 (2.33 us). The mechanism,
the raw histogram and the repair are D-8.

**Prediction 1's failure therefore cannot be read as D-7 intended.** D-7 made
depth inflation load-bearing on the reasoning that if §10.7's behaviour
reappeared, the reward still credited space. **Space is provably clean here**
— $\lambda_S$ never moved. The learner deepened the tree while chasing a
broken latency signal, so the question D-7 asked is still open and D-8
prediction 4 re-asks it under a repaired instrument. The verdict above stands
as measured and is not re-scored.

**E-5 is measured for the first time, and fails.** Neither the `oracle`
holdout nor `prior_only` could produce a non-zero conditional rate — force
requires `due` and neither arm disagrees with that predicate. `rl` defers
41–43% of due frames (1511/3648 at $T{=}2$, 608/1407 at $T{=}10$), so it can,
and the guard changes its action on **15%, 7% and 10%** of the frames where it
could. Against D-2's 1% decider that is a clear failure, and it is the first
time E-5 has been decidable at all. It carries the same caveat as everything
else here: the policy that produced it was driven by the railed latency term,
so this diagnoses the reward-and-guard pair, not the guard alone. Re-measured
under D-8 prediction 5.

**`unconstrained_rl` produced no scored frames** in stage 17 at any cell, so
its conditional rate is unavailable. Enforcement is off for that arm by
construction, which is the intended ablation, but the shadow classifier was
expected to log regardless per the 2026-09-21 change. Recorded as an
instrumentation gap, not investigated here.

**A defect in this entry's own scoring script, found and fixed before the
verdict was recorded.** `d7_learner_run.sh` read the multipliers and the flip
rate from `io.jsonl`'s `reward_components`, but that file's `"input"` is the
per-**level** state and the multipliers live in
`learning_health.json` → `server_summary.constrained_reward`. The first run of
the scorer printed `nan` for predictions 2 and 4, and scored prediction 4 as
**PASS on an empty set** — `all()` over a generator that filtered out every
`nan`. Both were re-scored from the correct source before anything was
recorded. The arms were never wrong.

**Evidence.** `results/learner-assoc/10M/T{2,6,10}/repeat-*/{rl,unconstrained_rl}/`,
`results/learner-smoke/`, `gate1/d7_verdict.txt`, `gate1/d7_conditional.{json,txt}`;
twins from `results/baseline_sweep/`.

### D-5 control scored, 2026-09-22: the band is a property of the trigger, not of $T$

**Verdict: predictions 1, 2 (range), 3 (range), 4 and 5 pass; both orderings
fail.** Twelve `prior_only` / `unconstrained_prior_only` arms at L0 trigger 4,
$T$ = 2 and 10, three repeats, binary `9b9321b1…`, against the
same-configuration static twin restricted to the paired seeds. Six `oracle`
calibration arms produced the trigger-4 manifests through the same chain the
programme's own cells used; the independent holdout was not run, and E-1/E-5
are therefore not scored here.

| cell | $L$ | L0 jobs/run, policy / twin | $\Delta W$ | $\Delta R$ | L1 $\Delta\varphi$ |
| --- | ---: | ---: | ---: | ---: | ---: |
| $T{=}2$ | 9 | 178.7 / 134.7 = **1.33×** | +2.94 [+1.97, +3.92] | −4.53 [−7.15, −1.90] | −0.037 |
| $T{=}6$ | 5 | **1.32×** *(comparator cell)* | +2.07 [+0.71, +3.43] | −5.69 [−9.48, −1.90] | −0.062 |
| $T{=}10$ | 5 | 180.7 / 136.0 = **1.33×** | +2.11 [+1.26, +2.96] | −7.07 [−11.60, −2.53] | −0.070 |

**Prediction 1 passes and it settles the confound.** The band's magnitude is
1.33×, 1.32×, 1.33× at $T$ = 2, 6, 10 — it is a function of the L0 trigger and
essentially nothing else. The D-4 arms at trigger 2 measured 0.99× and 1.00×.
So "the band costs ~2% of $W$ and buys ~5% of $R$" is a statement about
trigger 4, not about $T{=}6$, and D-4's reading of its own prediction-1 failure
stands: prediction 4 here reproduces the $\varphi$ gap at both new cells
(−0.037, −0.070) exactly as that reading requires.

**Prediction 5 passes at 0 of 24,400** deep releases below due, adding to
D-4's 0 of ~20,000. The rule "a deep level compacts iff RocksDB scores it due"
now rests on ~44,000 merges across five (cell, trigger) combinations.

**Both orderings fail, and they fail for one reason.** The predicted orderings
were built from share-of-total arguments — read gain scaling with the band's
headroom, write cost scaling with L0's share of write bytes. Neither holds.
What both track is **populated depth $L$**, in opposite directions:

| ordering | predicted | measured |
| --- | --- | --- |
| $\|\Delta R\|$ | $T{=}10 > T{=}2 > T{=}6$ (headroom 24.4/16.6/14.0%) | $T{=}10 > T{=}6 > T{=}2$ — i.e. decreasing in $L$ |
| $\Delta W$ | $T{=}10 > T{=}6 > T{=}2$ (shallow tree, larger L0 share) | $T{=}2 > T{=}10 > T{=}6$ — i.e. increasing in $L$ |

The fraction of headroom recovered is 27%, 41% and 29% at $T$ = 2, 6, 10, so
the headroom model is simply wrong. The mechanism the measurement supports is
that a shallower tree gives L0 a larger share of the probe path, so removing
L0 runs is worth more; and that extra top-of-tree work in a deeper tree is
rewritten at every level it then crosses, so it costs more. Both are
statements about $L$, and together they make the band's trade monotone in
depth:

| cell | $L$ | reads bought per unit of write spent |
| --- | ---: | ---: |
| $T{=}10$ | 5 | **3.35** |
| $T{=}6$ | 5 | 2.75 |
| $T{=}2$ | 9 | 1.54 |

This was not predicted and is recorded as a finding of the control, not as a
confirmed prediction. It bears directly on Gate 3b cell selection: the
top-of-tree lever is worth most where the tree is shallowest.

**The consequence for the write constraint, measured at every ratio.** The
constraint is an upper-bound test at $\delta_W = 2\%$. At trigger 4 the upper
bound is **+3.92%, +3.43% and +2.96%** at $T$ = 2, 6, 10 — `prior_only` misses
it in every cell. At trigger 2 it passes trivially (+0.27%, +0.53%) because
the band does not exist and the policy is native. **The prior's only lever
costs more write than the budget allows, at every size ratio**, and that is now
measured across three ratios rather than inferred from one. No margin is
widened and no constant is retuned; `RL_PRIOR_MIN_RUN_REDUCTION` is left
exactly where P1c-23 set it, because choosing it now would be choosing it after
seeing the number it moves.

**A defect in the scoring script, found and fixed before the verdict was
recorded.** The first run of the D-5 scorer summed L0 compaction jobs across
every run in a glob without dividing by the repeat count. The $T{=}2$ twin
carries nine repeats against the policy's three, so its job count was inflated
3× and the ratio printed as **0.44×** — which would have falsified prediction 1
and, by D-5's own falsification clause, forced the withdrawal of D-4's reading.
$0.44 \times 3 = 1.33$. The $T{=}10$ twin has three repeats, matching, so that
cell was unaffected and the disagreement between the two cells is what exposed
it. The corrected scorer normalises per run and restricts the twin to the
paired seeds. The arms are untouched; only the analysis was wrong. Recorded
because a falsification clause that fires on an arithmetic error is worth more
as a caution than as a verdict.

**Timing.** As with D-4, this entry and its predictions were written before the
run but committed after it; see D-4's timing note. The predictions are
unedited from the pre-run text — only the verdict block above is new.

**An unexplained latency tail at the control cells, measured and not scored.**
D-5 predicted nothing about latency and none of its criteria are affected, but
the arms carry a get-latency result that must not go unrecorded, because
latency is a preregistered acceptance constraint at a 2% margin (P0-4):

| cell / arm | p50 | p95 | p99 | p100 |
| --- | ---: | ---: | ---: | ---: |
| $T{=}10$ trig 4 `regular` | 2.57 | 20.84 | 31.9 | 3521 |
| $T{=}10$ trig 4 `prior_only` | 2.60 | 21.60 | **88.4** | **6858** |
| $T{=}2$ trig 4 `regular` | 2.93 | 38.08 | 65.7 | 1887 |
| $T{=}2$ trig 4 `prior_only` | 2.95 | 43.46 | **104.3** | 2043 |
| $T{=}6$ trig 4 `regular` | 2.57 | 23.28 | 33.6 | 1694 |
| $T{=}6$ trig 4 `prior_only` | 2.58 | 23.17 | 33.6 | 1371 |

Median and p95 are untouched; only the top ~1% moves, and only at the two
cells D-5 added. **It is not the guard**: `unconstrained_prior_only`, which
classifies without enforcing, is identical to `prior_only` there (p99 88.3
against 88.4 at $T{=}10$, 104.4 against 104.3 at $T{=}2$). But $T{=}6$ runs the
same trigger with the same band intensity and shows none of it, so "the band
costs tail latency" does not fit either.

**The leading explanation is a confound in the comparison, not a property of
the policy.** Direct I/O is pinned off, so latency and elapsed are warm-cache
figures that A-Impl-2 already says must be read as bounds on foreground
disruption and never as storage-latency claims. The twins come from the
2026-09-21 Hull-0 sweep and the control arms ran on 2026-09-22 after roughly
forty arms of I/O, so the two sides of this comparison are not
cache-equivalent. The amplification rows are unaffected — $W$, $R$, $S$ and
sorted-run seeks are byte ratios and cache-invariant, which is the reason
A-Impl-2 accepts the buffered-I/O cost in the first place.

**Not resolved here, and what would resolve it:** three `regular` repeats at
each of the two band cells run in the same session as the policy arms, which
is six arms and about twenty minutes. Until that exists, **no latency figure
from `results/band-control` may enter an acceptance table**, and the D-4 arms'
latency — measured against twins from the same sweep and showing +0.39% at
$T{=}6$ — is subject to the same caveat.

**Evidence.** `results/band-control/10M/T{2,10}/repeat-*/{prior_only,unconstrained_prior_only}/`,
`results/band-control-guard/assoc-v1/calibration/`,
`baseline_slo_band/assoc-v1/10M/T{2,10}/baseline_slo.json`,
`gate1/d5_verdict.txt`; twins `results/baseline_sweep/T{2,10}-l0-4-20-36-pri3-base16777216/`.

### Gate 1 — Hull₀ re-measured on `Assoc`, 2026-09-21

**Verdict: C-1 passes, C-2 partial (18 of 26 points, complete at $T{=}6$), the
cross-$T$ hull is measured, E-2 passes. C-3, C-4 and C-6 remain unevaluable
pending `prior_only`; C-5 is open.** The 2026-09-12 record below is the uniform
measurement on binary `deb6753c`; it is superseded as the programme's
comparator and is not re-scored.

**Execution.** 10M, workload `assoc-v1`, buffered I/O (`dio0`), binary
`9b9321b1…`, objective `5857ad35…`. **182 `regular` arms**, one binary and one
objective hash throughout: 36 configurations (L0 trigger 2/4/8/16 × level base
8/16/32 MiB, `compaction_pri` pinned to 3) at 3 repeats = 108; a first top-up of
62 arms over 26 hull points; a second of 6 arms over 4; and 6 cross-$T$ arms at
$T = 14$ and $T = 20$, trigger 4, base 16 MiB, 3 repeats.

**C-1 passes at every ratio**, needing 4 hull points of 12 and finding **11, 7
and 8**. Membership is identical across all three extractions — at 108, 170 and
182 arms — so 74 additional arms moved no point on or off the frontier.

**C-2: 18 of 26 hull points, and $T{=}6$ passes completely (7 of 7).** That is
the first complete C-2 pass in this project; the uniform gate failed C-2 at all
three ratios. $T{=}2$ is 7 of 11 and $T{=}10$ is 4 of 8.

C-2 reduces to one quantity — the gap to the nearest hull neighbour measured in
units of run-to-run standard deviation, since $2t(n)\sigma/\sqrt{n} <
\text{gap}/2$ is exactly $\text{gap}/\sigma > 4t(n)/\sqrt{n}$. That
threshold is 4.97 at $n{=}5$, 2.86 at $n{=}10$ and **0.56 at $n{=}200$**, which
is the wall. Across the 26 points the median is **5.75**, against **0.55** for
the pair the 2026-09-12 gate was recorded failed on. The `Assoc` frontier is
roughly ten times better separated relative to noise, which is why more than
two thirds of the points are individually decidable where none were before.

The eight failures have three distinct causes, and only one is about
measurement:

| cause | points | detail |
| --- | ---: | --- |
| duplicate configurations | **2** | $T{=}10$ trigger 8 and 16 at base 8 MiB, gap/σ **0.03 and 0.04** |
| clustered at the low-$W$ end | 5 | gap/σ 1.59–2.33, needing 14–27 repeats, beyond the preregistered cap |
| stopped by decision | 1 | $T{=}2$ trigger 16 / base 8 MiB, needs 10 and has 9 |

**The duplicate pair is a collision in the grid, not a limit of the
instrument.** L0's score is $\max(\text{files}/\text{trigger},\;
\text{bytes}/\texttt{max\_bytes\_for\_level\_base})$ — assumption A3′ — so
the byte branch caps the *effective* trigger at roughly
$\texttt{base} / \text{L0 file size}$. At base 8 MiB against a 2 MiB write
buffer that ceiling is about 4–5 files, and every configured trigger above it is
dead. Measured directly, L0 compaction jobs in the measured phase are
**107.7 / 107.7 at $T{=}2$, 113.0 / 113.0 at $T{=}6$ and 111.3 / 111.3 at
$T{=}10$ — 0.00% apart** for triggers 8 and 16. No repeat count separates two
identical configurations. This also explains the 2026-09-12 unresolvable pair,
which was likewise at scale 0.5, i.e. base 8 MiB, and was recorded there as the
frontier being denser than the instrument. A3′ was in the plan; nobody had
connected it to the grid design. It should shape the Hull$_s$ grid at Gate 3c.

**The point stopped by decision is recorded as such.** $T{=}2$ trigger 16 /
base 8 MiB asked for 9 repeats, was given them, and came back asking for 10 —
its gap/σ having *fallen* from 3.28 at $n{=}7$ to 2.97 at $n{=}9$. One further
arm is inside the cumulative cap (it has spent 6 of 10), and it was not run.
Running a criterion until it passes is how a preregistered criterion stops
meaning anything, and a point that moves backwards on the deciding statistic
after being given what it asked for is not under-sampled. Two top-up passes
against a criterion recorded failed outright last time is already generous.

**The cap was scored cumulatively, which the tool does not do.**
`15_top_up_hull.py` compares `target - have` against `--add-cap`, so it
enforces the limit *per pass*; PATHWAYS preregisters ten additional arms *per
configuration*. The second pass was therefore selected to keep every
configuration within ten cumulative additional arms, excluding $T{=}2$
trigger 16 / base 16 MiB, which would have reached eleven.

**C-6's input is measured: the cross-$T$ pooled hull holds 14 of 38 points**,
contributed $T{=}2$ 8/12, $T{=}6$ 4/12, $T{=}10$ 2/12, **$T{=}14$ 0/1 and
$T{=}20$ 0/1**. Both cross-$T$ cells are dominated by one $T{=}10$
configuration — trigger 2 at base 8 MiB, $W = 9.0322$, $R = 3.4289$ — which is
better on write *and* point-read simultaneously than $T{=}14$ (9.4225, 3.7713)
and $T{=}20$ (10.1312, 3.5994). **The ratio axis is bounded above**, which is
what P1-14 added these cells to establish, and the conclusion is slightly
stronger than on uniform, where $T{=}14$ contributed one point. The cross-$T$
cells carry 3 repeats against 5–9 elsewhere; this does not weaken the reading,
because being dominated is easier to establish than being on the hull and
neither point is marginal.

**E-2 passes at 100%.** Calibrated populated level-cells are **8/8, 4/4 and
4/4** at $T = 2/6/10$ against a 90% threshold on the populated denominator
(amended 2026-09-14). Every populated level rests on a
`censored_order_statistic_tolerance_bound` at 0.99 coverage and 0.95 achieved
confidence, with **no bootstrap caps anywhere**. `allowed_pending_debt_ratio` is
4.4284, 2.9641 and 2.2954, comparable to uniform's 4.141, 2.689 and 2.718.

**Comparators selected by stage 06**, base 16 MiB at every ratio: **trigger 2 at
$T{=}2$, trigger 4 at $T{=}6$, trigger 2 at $T{=}10$**.

**D-3's first prediction is confirmed, and it changed a comparator.** Under the
measured denominator the space filter admits **4 of 4** triggers at every ratio;
under the superseded estimate it would have admitted 2 of 4 at $T{=}2$, **1 of
4** at $T{=}6$ and 4 of 4 at $T{=}10$. At $T{=}6$ the estimate admitted a single
configuration, so the comparator was forced to trigger 2; with the filter no
longer binding, the runtime tie-break selected trigger 4. $T{=}2$ and $T{=}10$
select trigger 2 either way. The prediction was recorded before stage 06 ran
(commit `b5fd0ce`, 17:06 UTC, which also precedes the guard protocol and every
learned arm).

**Still open.** C-3, C-4 and C-6 need `prior_only`, which needs a
guard-calibrated manifest — the Pathway E protocol, which has not run on
`Assoc`. C-5 needs stage 16 and the `STATIC_CAPACITY_SCALES` vectors. **No
policy arm has been run on this workload**, so nothing here bears on whether the
controller contributes anything.

**Evidence.** `results/baseline_sweep/` (182 arms), `gate1/hull-T{2,6,10}.json`,
`gate1/hull-crossT.json`, `gate1/hull_indistinguishable.tsv`,
`baseline_selection/assoc-v1/10M/T{2,6,10}/baseline_slo.json`.

### Gate 1 — Hull₀ and space calibration

**Result, 2026-09-12.** Executed at 10M with buffered I/O (`dio0`), contract v3
`87eaddbc`, binary `deb6753c`.

| | T=2 | T=6 | T=10 |
| --- | ---: | ---: | ---: |
| configurations | 12 | 12 | 12 |
| on the per-$T$ hull | 12 | 9 | 9 |
| C-1 | pass | pass | pass |
| C-2 | false | false | false |

Cross-$T$ pooled hull: **18 of 38** points, contributed T=2 9/12, T=6 6/12,
T=10 2/12, T=14 1/1, T=20 0/1.

- **C-1 passes** at all three ratios, needing four hull points and finding 12,
  9 and 9.
- **The ratio axis is bounded above.** T=20 contributes nothing to the pooled
  hull and T=14 contributes one point. Extending the size ratio past 10 adds no
  frontier, which is exactly what the cross-$T$ cells were added to establish
  (P1-14).
- **Most high-$T$ configurations are dominated across ratios.** T=10 keeps only
  2 of its 12 points once ratios compete, while T=2 keeps 9 of 12. Since a
  dominating point must be no worse on both $W$ and $R$, the reading is that
  raising the ratio past 6 buys little further read improvement for a
  substantial write cost. This does not reproduce the expectation, drawn from
  the §10.7 arm means, that non-dominated points would cluster at T=10. That
  expectation concerned *policy* points and remains untested until `prior_only`
  runs; what is now measured is that the *static* frontier does not cluster
  there.
- **C-2 verdict: fail, deferred.** Recorded as failed rather than relaxed. The
  criterion is not reinterpreted after seeing it fail; revisit before the
  paper's acceptance table is fixed. Five
  hull points fail the width condition, in two distinct classes recorded in
  `gate1/hull_indistinguishable.tsv`. Two, both at T=2 and both at scale 0.5,
  are unresolvable at any $n \le 200$: no achievable repeat count fits the
  interval inside half the gap to the nearest hull neighbour. Three more are
  resolvable in principle but only beyond a preregistered spend limit of ten
  additional arms per configuration, needing 32, 70 and 126 repeats against 14,
  14 and 3 present. Every hull point does meet the five-repeat floor.

  **The cause is measured, not inferred.** The two T=6 points at scale 0.5,
  with L0 triggers 16 and 8, were checked for an outlier run and have none:
  across 14 and 5 repeats their stall time spans 52–60 s and their sorted-run
  seeks 6.95–7.09. They are separated by 0.0067 in write amplification, which
  is 0.080% of the mean, against a run-to-run standard deviation of 0.145% and
  0.120%. **The gap between two distinct hull configurations is 0.55 and 0.66
  of one standard deviation.** C-2 would need 207 and 143 repeats on that axis.
  The frontier is denser than the instrument can resolve, and no amount of
  experimental hygiene changes that.

  **The same tightness is what makes the real criteria decidable**, because
  C-2 is the only one that compares hull points with each other; C-3, C-4 and
  C-6 compare a policy against them at 2% margins.

  | axis | full 95% CI width, n=5 | n=10 |
  | --- | ---: | ---: |
  | write amplification | 0.30–0.36% | 0.17–0.21% |
  | point-read amplification | 1.30–2.85% | 0.75–1.64% |

  Write amplification carries roughly six times the margin to spare at five
  repeats. Point-read is the binding one: at five repeats the worse of the two
  configurations gives a 2.85% interval, wider than a 2% margin, falling to
  1.64% at ten. That is a measured justification for the preregistered ten
  repeats on any cell carrying an acceptance claim, where previously the count
  rested on convention.
- **C-5 is not satisfied.** $\Delta S(\text{scale})$ is measured, four curves
  per ratio, but `capacity_s_max` is `None` at every ratio with
  `capacity_bound_status: requires_matched_deep_capacity_calibration`. The
  base-option scale moves L0's target along with the deep levels, so the curve
  is a proxy and no transfer bound to a per-level capacity actuator is proved.
  C-5 selects the (cell, rung) pairs Gate 3b runs, so this must be closed before
  Gate 3b rather than after.

  **Closed 2026-09-13 by a matched calibration.** The per-level actuator was
  exposed statically through `RL_STATIC_CAPACITY_SCALES` and driven directly,
  levels 1..L-1 scaled with L0 and the final level pinned per A-Impl-1, three
  repeats per (ratio, scale) at 10M. Each arm's applied vector is verified
  against its request from the Gate-0 release events, so a request that never
  reached `MaxBytesForLevel` is a hard error rather than a flat curve.

  | T | ΔS at s=1.5 | ΔS at s=2.0 | $s_{\max}$ at the 2% rung |
  | ---: | ---: | ---: | ---: |
  | 2 | −0.33% [−0.63, −0.03] | −0.43% [−0.66, −0.21] | 2.0 |
  | 6 | +0.72% [+0.61, +0.83] | +0.15% [−0.12, +0.43] | 2.0 |
  | 10 | −1.71% [−1.83, −1.59] | −1.89% [−2.30, −1.49] | 2.0 |

  Expansion to s=2.0 costs at most 1.9% of settled space and is negative in two
  cells of three, so $s_{\max} = 2.0$ at the 2% rung everywhere. T=6 is
  genuinely non-monotone, its two intervals not overlapping, which does not
  affect the bound because $s_{\max}$ already requires every scale below it to
  be affordable.

  **The calibration exposed a defect in the space metric itself.** ΔS computed
  against `estimate-live-data-size`, the frozen definition, swung by up to
  4.8% across runs whose true live data was identical: the garbage-free size
  from the reference compaction is 3.02 GB in all nine configurations. Settled
  bytes moved by under 2%. The estimate is sensitive to how data is spread
  across levels, which is exactly what the capacity actuator changes, and what
  §10.7 Finding 3 says the learner's whole strategy changes. Every space
  amplification figure in this project inherits that sensitivity, including the
  frozen constraint arms are judged against. Whether the denominator becomes
  the measured garbage-free size is a contract amendment and is **not taken
  here**.
- **C-3 and C-6 were not evaluable at the time**, both needing `prior_only`,
  which needs a guard-calibrated manifest, which is the Pathway E-1 gate. E-1
  was re-run with a repaired calibration chain on 2026-09-14 and is **recorded
  failed** at 0.342–0.377 measured override against a 1% limit.

  **Unblocked and decided 2026-09-19: C-3 and C-6 both FAIL.** The E-5
  measurement shows the guard changes at most 0.09% of frames, so
  `unconstrained_prior_only` — the prior with the guard classifying but not
  enforcing — is the same policy as `prior_only` here and stands in for it. Ten
  repeats per cell at 10M. The policy is **dominated** at every ratio and in the
  cross-$T$ pooled hull, with both paired intervals strictly below zero; the
  per-cell table and its consequences are under Pathway C. **Finding 2 is
  withdrawn.**

  **Gate 1 is therefore complete and not passed:** C-1 passes, C-5 is closed at
  $s_{\max}$ = 2.0, and **C-2, C-3 and C-6 are recorded failed**.
- **The ≈ 48 h estimate is stale**, being calibrated on the 2026-09-03 Zen 3
  node. The measured per-arm cost here is 173.6 s for the `db_bench` phase at
  10M T=2. Recalibrate the whole cost table from `driver.log` before planning
  the remaining leases.

---

## 3. Pathway E execution record

### E-1 verdict, 2026-09-14: **FAIL, recorded as failed**

The frame-calibrated manifest was run through the full guard protocol at
10M/T=2, seeds 10001–10003. It did not improve the measurement.

| seed | ready frames | override frames | fraction | predicted (ramp) |
| ---: | ---: | ---: | ---: | ---: |
| 10001 | 2,345 | 851 | 0.363 | 0.0099 |
| 10002 | 2,365 | 891 | 0.377 | 0.0098 |
| 10003 | 2,355 | 806 | 0.342 | 0.0099 |

Measured 36× the prediction. `actual_interventions` was 0 in all three, so this
remains shadow classification and nothing was perturbed. T=6 and T=10 ran but
were not scored: the validator exits non-zero on T=2 and `set -e` aborts the
loop before reaching them.

**Cause, established by elimination and then confirmed directly.** Of 2,345
scored frames in seed 10001: 851 overrode, 850 had whole-tree debt at or above
`allowed_pending_debt_ratio`, and 0 overrode without a level being due. The
debt term accounts for the override rate to within one frame. Debt is *global* —
one breach forces every due level in the same frame — and it was the one term in
the force condition left on an episode-derived tolerance bound while due age,
pressure and score were converted to the frame unit. The conversion was
therefore incomplete, and the incomplete part was the term that dominates.

**Why recalibrating debt does not fix it, which is the substantive finding.**
The debt ratio is not a distribution with a tail to place a quantile in; during
the backlog it sits on a **plateau**. On the holdout its p50 over due frames is
4.849 and its p90, p99, p99.9 and maximum are all 4.899. Placing any threshold
on a plateau is unstable by construction. Cross-validating the whole calibration
— limits fitted on the three `oracle` calibration runs, evaluated on the three
`oracle` holdout runs, identical binary, identical configuration, differing only
in seed — gives **0.0098 on the fitting set and 0.1804 on the held-out set**, an
18× transfer gap, with the fitted debt limit at 4.876 against a held-out maximum
of 4.899. Both figures are computed as upper bounds (exact due age, episode-max
score, episode-max debt), so no within-episode model can rescue them.

An earlier diagnosis in this thread attributed the transfer failure to
calibrating on `regular` sweep arms and testing on `oracle` bridge arms. That is
**wrong** and is retracted: `oracle`-to-`oracle` fails the same way.

**What the statistic is actually measuring.** Decomposing the override frames
into maximal consecutive stretches gives, on all three holdout runs, exactly
**one** event — of 851, 891 and 806 frames. The guard would engage once per run
and stay engaged for roughly 36% of it. The tree enters a single sustained
backlog: debt plateaued at ~4.88, a deep level continuously due, matching the
`longest_due_run_fraction` of ~50% per run that Gate 1's own calibration
reports, and the same event behind the debt p99 of 4.141 and maximum of 9.485.
The guard is not misfiring; it is responding continuously to a condition that
genuinely persists.

So two quantities are conflated by one number. How *often* the guard engages is
1 per run and is exactly stable across seeds. How *long* it stays engaged is
~36% of frames and swings 0.342–0.377 between seeds of the same configuration,
and 18× under cross-validation. E-1 thresholds the unstable one. Corollary E.2
above already holds that the marginal rate is the wrong statistic and asks for a
conditioned rate instead, so the form was suspect in this plan before any of
this was measured.

**Recorded as failed rather than reinterpreted.** An amendment to count override
events was considered and not taken. The criterion had already been seen to
fail, only one event per run was observed, and any bound set now would be fitted
to that observation — the same objection that kept C-2 recorded as failed. Two
instruments on this gate have already been amended after a failure (E-2's
denominator, and the limits' unit), which is reason for more caution here, not
less.

**The guard is not cut yet.** PATHWAYS prescribes that E-1 failing cuts the
guard from the paper. That consequence is deferred pending a design decision, on
the grounds that the failure is now understood to be a property of the
criterion's statistic and of one workload event, not evidence that the shield
misbehaves. Until it is resolved, C-3 and C-6 stay unevaluable, because
`prior_only` requires a guard-calibrated manifest.

### All three cells scored, 2026-09-17 — and the mechanism differs in each

The T=6 and T=10 holdout runs had completed but were never scored: the validator
exits non-zero on T=2 and `set -Eeuo pipefail` aborts the loop before reaching
them. **The validator is a scoring step, so a failing cell is a result, not an
error**; the loop should score every cell and exit non-zero at the end.

| cell | override fraction | over limit | debt term | model agreement | FN |
| --- | --- | ---: | ---: | ---: | ---: |
| T=2 | 0.363, 0.377, 0.342 | 36× | **0.362** | 1.000 | 0.000 |
| T=6 | 0.142, 0.154, 0.149 | 15× | **0.142** | 0.908 | 0.000 |
| T=10 | 0.193, 0.189, 0.191 | 19× | **0.000** | 0.857 | **0.144** |

**The debt diagnosis recorded above is specific to T=2 and T=6.** In both, the
debt term matches the override fraction exactly (0.362/0.363 and 0.142/0.142).
**At T=10 the debt term never fires at all** — the tree is shallow, ~4 populated
levels at space amplification ~1.17, and pending work never reaches the 2.718
limit — yet the guard still overrides 19% of frames. The offline replay misses
14.4% of those frames, so they come from a term with no per-frame record:
`l0_slowdown` (labelled `kBudget` by default) or `slo_force_due`. The reason
split at T=10 is `kBudget` 212–252 against `kSLO` 84–126, and `kSLO` alone
cannot cover the 14.4%, which leaves `l0_slowdown` as the leading candidate.
It is **not confirmed**: L0 file counts appear in no log. Reason composition
also differs sharply by ratio — `kSLO` is ~2% of overrides at T=2, ~56% at T=6
and ~25% at T=10 — so no single repair addresses the gate.

**Zero distortion, measured.** Across all three cells and all nine runs,
`override frames with no due level = 0`. Every override is a force on a level
that was already due. The holdout arm is `oracle`, whose action is
`level.score >= 1.0 ? kCompactNow : kDoNothing` (`compaction_picker_rl.cc:1791`),
while the guard's gate is `item.due = observed.score >= 1.0` and
`item.force = item.due && (...)`. Both branch on the same predicate over the
same `RLLevelState`, so `force ⟹ due ⟹ the oracle had already chosen compact`.
`revoke_optional` requires `!item.due` and the oracle never installs optional
permits, so nothing is revoked either. **The conditional override rate of
Corollary E.2 is identically zero on this holdout**, which makes E-1's 0.14–0.38
a measure of agreement, not of shield influence. A guard holdout whose arm
cannot disagree with the guard is not a test of the guard.

**The `guard_ready` window is exactly the bulk load.** `filluniquerandom`
reports `58.801 seconds` for 2,900,000 operations, and `guard_ready` arrives at
+58.8 s — the same number, matching `load29` in the fingerprint. The mechanism
is that the load phase issues no Gets or scans, so the latency histograms never
reach `guard_minimum_samples` and the SLO terms cannot classify. Scoring is
therefore unaffected (E-1 already counts ready frames only), but **the forcing
is not**: `due_age`, `pressure`, `score` and `debt` evaluate and force
regardless of readiness. With `safety_enforcement=1`, a `prior_only` or `rl` arm
would have its policy overridden for the whole first 29% of every run, under no
criterion at all. That is a live behaviour affecting the learned arms, not only
a gap in this gate's bookkeeping, and it needs a ruling independently of E-1.

---

### E-5 measured, 2026-09-19: the conditional rate, and two corrections

`unconstrained_prior_only` at 10M, $T$ = 2/6/10, ten repeats per cell. Guard
classifying, not enforcing: `intervention_applied` is zero on every frame.

| cell | marginal (E-1) | force changes an action | revoke changes an action |
| --- | ---: | ---: | ---: |
| T=2 | 0.3356 [0.321, 0.351] | 0.0009 | 0 |
| T=6 | 0.1567 [0.144, 0.173] | 0.0000 | 0 |
| T=10 | 0.1983 [0.189, 0.206] | 0.0000 | 0 |

**E-1 fails by 34x, 16x and 20x; the statistic Corollary E.2 asks for passes
the same 1% limit by more than an order of magnitude, in every cell.** E-5 is
satisfied. E-1 stays recorded as failed — the criterion was seen to fail before
any of this, which is the objection that kept C-2 and E-1 recorded failed.

**`revoke_optional` never fired**, established rather than bounded: 40-67% of
ready frames contain no due level, force requires a due level, so on those
frames a revoke is the only thing that could raise an override — and across
~30,000 such frames in thirty runs, none did. The write trip is sticky at three
windows in and out, so it could not have fired and missed all of them. Latency
margins agree: guard limits 139.7 us average and 2.14 ms p95, against a measured
Put average of 12.8 us and P99.99 of 433 us.

**The write-side sensor reads the wrong variable.** `prohibit_optional` is
driven by write *latency* (`rl_safety_manifest.cc:336`), which has 11x
headroom, while the prior's binding problem is write *amplification* at ~+15%,
for which the shield has no input. This pathway's Description says the *action
set* is asymmetric; the measurement says the *sensor* is too, and that is a
Pathway D linkage.

**Two corrections to the 2026-09-14 verdict**, both from generalising three T=2
runs. First, *"exactly one event per run"* holds only at $T$=2: override frames
form 1.3 events per run there, but **22.2 at $T$=6 and 14.9 at $T$=10**.
Second, *debt is not the dominant term*: the 850-of-851 figure came from each
episode's `max_pending_debt_ratio`, a per-episode maximum. Read per frame, from
the quantity `DebtRatioBreach` compares, debt holds on **11%** of override
frames at $T$=2 and does not reach the top five at $T$=6 or $T$=10. `pressure`
dominates everywhere; 42-53% of override frames have no instantaneous term
true, consistent with the sticky retention branch carrying the force.

---

---

### Guard protocol on `Assoc`, 2026-09-21/22: D-2 scored, and the calibration audited

**Timing, stated first.** The runs completed 2026-09-21 19:51 UTC. T=2 was
scored the same evening; T=6 and T=10 on 2026-09-22, after the defect below was
found. The audit is 2026-09-22. **Nothing here amends a criterion.** D-2 fixed
E-5 as the decider and recorded both of its predictions on 2026-09-20,
committed ahead of these runs; this entry scores them as measured. No limit was
re-fitted — see "Measure-only" below.

**Execution.** Nine `oracle` calibration arms (seeds 1001–1003) and nine
`oracle` holdout arms (seeds 11001–11003), 10M at T = 2, 6 and 10, three
repeats per cell, binary `9b9321b1…`, manifest `0bb12249…`.
`enforcement_enabled` is false and `actual_interventions` is **0 on every run**:
this is shadow classification and nothing was perturbed.

| cell | marginal override, per run | mean | leave-one-out prediction | ratio |
| --- | --- | ---: | ---: | ---: |
| T=2 | 0.1393, 0.1251, 0.1161 | 0.1268 | 0.0106 | **12.0×** |
| T=6 | 0.0779, 0.0745, 0.0758 | **0.0760** | 0.0107 | **7.1×** |
| T=10 | 0.1234, 0.1250, 0.1222 | 0.1235 | 0.0099 | **12.5×** |

**D-2 prediction 1 — "the marginal rate falls below 0.20 in every cell" —
CONFIRMED.** Against the uniform figures of 0.3356 / 0.1567 / 0.1983, the rate
roughly halved in every cell. Attribution is not clean: the latch removal, the
workload change and the recalibration all landed together, and this entry does
not claim the latch alone.

**D-2 prediction 2 — "the leave-one-out prediction brackets it within a factor
of three" — FALSIFIED in all three cells**, at 7.1×, 12.0× and 12.5×. D-2's own
falsification clause applies verbatim: *the calibration's transfer claim is
wrong and must be reported as such.* Leave-one-out was the out-of-sample
estimate the in-sample number was supposed to be checked against, and it
under-predicts by an order of magnitude.

**E-1 fails by 12.7×, 7.6× and 12.4×** at T = 2, 6 and 10. Reported without a
pass/fail, per D-2, which retired it as a decider on 2026-09-20.

**E-5 is NOT MEASURED, and this holdout cannot measure it.** The arm is
`oracle`, whose rule is `score >= 1 → compact`, and `item.force` requires
`item.due` (`compaction_picker_rl.cc:1322`). Force can therefore only land on a
level the oracle was already compacting, so the conditional rate is zero by
construction. This reproduces the 2026-09-17 finding and is a property of the
holdout design, not a result about the guard.

**The T=10 mechanism is identified, and it closes a question open since
2026-09-17.** Schema 3 logs the three global terms per frame, so the
attribution is read from a log rather than reconstructed:

| cell | override frames | from a global term | `slo_force_due` | `l0_slowdown` | `global_debt_breach` |
| --- | ---: | ---: | ---: | ---: | ---: |
| T=2 | 1272 | 200 | 28 | 66 | 145 |
| T=6 | 502 | 45 | 21 | 24 | 0 |
| T=10 | 739 | **524** | **524** | 45 | 0 |

At T=10 the global terms carry **71%** of all overrides, almost entirely
`slo_force_due`. History 14.8 named `l0_slowdown` as the leading unconfirmed
candidate for the 14.4% the offline replay could not attribute; that guess is
**wrong and is retracted**. `l0_slowdown` is a minor term in every cell.

---

**Audit of the calibration, 2026-09-22.** Four findings, none previously
recorded.

1. **The force condition has six terms; the calibration bounds three.**
   `compaction_picker_rl.cc:1322` forces on `due_age >= limit || pressure >=
   limit || score >= limit || global_debt_breach || slo_force_due ||
   l0_slowdown`. `frame_simulated_limits` fits its 1% joint target over the
   first three only, and its own docstring states the other three are not
   modelled because no per-frame record existed when it was written. The last
   three are global: one breach forces every due level in that frame. **A
   budget fitted to half a disjunction cannot bound the whole.**

2. **Discounting the global terms does not rescue it.** Counting only override
   frames on which no global term is true:

   | cell | per-level-only override | vs the 1% budget |
   | --- | --- | ---: |
   | T=2 | 0.1050 – 0.1070 | **10.5× – 10.7×** |
   | T=6 | 0.0669 – 0.0692 | **6.7× – 6.9×** |
   | T=10 | 0.0346 – 0.0359 | **3.5× – 3.6×** |

   The upper end is every frame with no global term true; the lower end
   subtracts one release frame per override event. Even restricted to exactly
   the terms it does calibrate, the guard misses its own budget by 3.5–10.7×.

3. **`due_age` and `pressure` latch by construction, so D-2's `retain` removal
   was structurally partial.** Both quantities only grow while a level is due,
   so once either crosses its limit it stays crossed for the rest of the due
   run whatever the retention rule does. Measured: the override frames form
   only **20, 15 and 8 events** carrying 1272, 502 and 739 frames — a mean
   engagement of 64, 34 and 92 frames (3.2 s, 1.7 s, 4.6 s). D-2 removed the
   explicit latch; the implicit one in the monotone terms remains, and it is
   why the rate halved rather than falling to the target.

4. **The calibration instrument cannot predict what any limits would achieve.**
   Replaying the exported limits against the holdout's *own* episodes under the
   three score models the calibration publishes:

   | cell | flat | ramp *(used to set limits)* | upper | measured per-level |
   | --- | ---: | ---: | ---: | ---: |
   | T=2 | 0.0195 | 0.0168 | 0.3397 | **0.105** |
   | T=6 | 0.0174 | 0.0155 | 0.3869 | **0.069** |
   | T=10 | 0.0168 | 0.0111 | 0.3958 | **0.035** |

   The measurement lands inside the bracket and **matches none of the three
   models**, and the bracket spans 20× at T=2. The decomposition rules out the
   easy explanation: `due_age` needs no model — it is exact from the episode
   start — and predicts only 0.0138 / 0.0124 / 0.0069. The missing information
   is the per-frame *pressure* and *score* trajectory, which
   `pressure_episodes.jsonl` does not carry; it stores episode summaries only.

**Instrumentation gaps recorded, not closed.** Schema 3 added the three global
terms but not `prohibit_optional` (the write-side trip behind
`revoke_optional`), and there is no per-frame release-frame flag. An override
frame with no global term true is therefore one of three things — a per-level
force, a release frame, or a write-driven revoke — and the log cannot separate
them. On uniform, 14.9 established `revoke_optional` never fired; on `Assoc`
that is unverifiable rather than measured. Both are C++ changes and both are
deferred to Gate 2, because a rebuild changes `dbbench_sha256` and
`frontier_analysis.py:196` refuses to pool the 182-arm Hull₀ across binaries.

**Measure-only, decided 2026-09-22 before any arm runs under a changed
manifest.** The per-level limits live in `baseline_slo.json`, which is data: a
manifest change alters no `experiment_fingerprint` (`03_run_experiments.sh:678`
carries no manifest hash) and would not void the hull. They are nevertheless
**not re-fitted**. Choosing new limits after observing 0.035–0.107 is fitting to
the outcome, which is the objection that kept C-2 and E-1 recorded failed; and
independently, finding 4 shows there is no instrument with which to predict what
new limits would achieve. The next step is to *measure* the per-frame guard
state — `rl_agent/server.py:92` logs `"input": raw_state` every frame and the
protocol-v2 per-level state carries `due_age_micros`, `pressure_score_micros`,
`score` and `files` (`multilevel.py:152–188`), which is exactly the missing
quantity and needs no rebuild. The `oracle` arm does not query the server, which
is why this holdout has no such log. If a re-fit is ever taken, it requires its
own dated entry with a falsifiable prediction, recorded before the run it
governs, on the D-3 precedent.

**A defect in the scoring loop, fixed.** `06_run_guard_protocol.sh` scored the
cells under `set -Eeuo pipefail`, so the validator's non-zero exit at T=2
aborted the loop and left T=6 and T=10 unscored — the same failure as
2026-09-14, which history 14.8 recorded and stated the fix for ("the validator
is a scoring step, so a failing cell is a result, not an error") without the
script ever being changed. It now scores every cell, reports each one, and exits
on the worst status at the end.

**Evidence.** `results/guard/assoc-v1/{calibration,holdout}/10M/T{2,6,10}/repeat-*/oracle/{safety_shadow,pressure_episodes}.jsonl`,
`results/guard/assoc-v1/holdout/readiness-10M-T{2,6,10}.json`,
`baseline_slo/assoc-v1/10M/T{2,6,10}/baseline_slo.json`.

---

## 4. Frozen preregistered decisions

**Authoritative contract:** `../config/research_objective_contract.v3.json`,
frozen 2026-09-12 with
P0, P1, P1b, the build pin and the direct-I/O pin. The prose companion
`docs/RESEARCH_OBJECTIVE_CONTRACT.md` was removed on 2026-09-20 as stale. v1 and
v2 remain in the tree as superseded records; no run was executed under either.
Any
later change requires a versioned amendment and must not be selected after
inspecting a formal gate.

**P0 (2026-09-10)**

1. **Scan objective.** `sorted_run_seeks_per_scan` with a 2% paired
   non-inferiority margin; `scan_amplification` (at its floor of 1.0) is
   diagnostic only.
2. **Stall test.** Paired 95% interval on `stall_seconds` with a zero
   non-inferiority margin; `stall_events` diagnostic; no `all(delta <= 0)`.
3. **Write margin $\delta_W$** = 2% paired relative non-inferiority. The $\beta$
   sweep is exposition, not the criterion.
4. **Latency.** Average and p99 get, scan and write latency at a 2% paired
   relative non-inferiority margin; an interval crossing the margin is
   `undecidable`, not failed.
5. **Histograms.** Preserve P50, P95, P99, P100, COUNT, SUM. Write p95 is
   diagnostic; p95 below the mean is valid for the heavy-tailed stalled-write
   distribution, subject to parser and stall-correlation verification.
6. **$S_{\text{bound}}$ as an axis.** Report the W–R–S surface at relative
   space-regression budgets $\{0, 2, 5, 10\}\%$.
7. **Write-accounting model.** M3; state its assumptions whenever quoting the
   $\approx 3.6$ optimum.
8. **Corollary A.4 comparison.** Recompute on $W-1$ and state the denominator.
9. **Static-ladder pin.** `level_compaction_dynamic_level_bytes=false`, declared
   with the A5 justification.
10. **Delete-rate provenance.** Sweep synthetic $\{0, 3, 6\}\%$ on the `Assoc`
    key distribution, labelled synthetic; do not attribute to the published
    `Assoc` mix.

**P1 (2026-09-11)** — recorded before any run of the programme, frozen in
contract v2 on 2026-09-12.

11. **A-0 added** (Gate 0). Required for the research track; attribution of any
    $W$ change to expansion is limited to the $D_{\text{depth}}$ share.
12. **A-2 is a joint A+D criterion.**
13. **Theorem B.1's floor is $1/S_{\text{flow}}$.** B-2 and D-4 ceilings are
    recomputed from measured $S_{\text{flow}}$ and $L$ at Gate 0.
14. **C-6 added:** non-domination against the hull pooled over
    $T \in \{2, 6, 10, 14, 20\}$, required for both tracks; Gate 1 gains
    `regular` cells at $T{=}14$ and $T{=}20$.
15. **Gate 3b runs at the P0-6 ladder** at every rung Gate 1 leaves open, all
    rungs reported, no post-hoc widening; headline rung 2% unless another is
    preregistered before 3b.
16. **Pathway D's hinge target** stays $W_{\text{base}} + \delta_W$ at the arm's
    own $T$; not moved to the hull, not moved after a C-6 failure.
17. **Build and measurement environment pinned.** `-march=znver5` through
    RocksDB's `PORTABLE` variable, GCC 14.1 or Clang 19 minimum, binary SHA-256
    inside `experiment_fingerprint`; `db_bench` and the controller on disjoint
    cores sharing no last-level cache in every arm, recorded per arm but not
    yet part of the fingerprint.

**P1b (2026-09-12)** — recorded before Gate 1, frozen in contract v3
(SHA-256 `87eaddbcf64529760d91ff139e3c2a4db3787437bfd0b67a93394a4ab8a64bf6`). Labelled P1b so the Pathway F block below keeps its P2 label.

18. **Direct I/O pinned off**, for the read path and for flush/compaction, with
    `compaction_readahead_size` at the 2 MB default. Not swept. Recorded in the
    fingerprint (`dio`) and in `metadata.env`. Reversed from the original
    pinned-on decision on measured cost, 2,750 ops/s direct against 58,332
    buffered on the same node, a 21× penalty.
    Latency, runtime and stalls are therefore warm-cache and reported as such;
    the amplification metrics and the hull are unaffected. May be enabled for
    the final paper benchmark workload, which then carries `dio1` and forms its
    own comparator chain.

**P1c (2026-09-20)** — recorded before any run of the re-executed programme,
after the objective-consistency audit of the same date; contract v3 amended
in place (`amendments_in_place`, key `2026-09-20`). Nothing here was chosen
after inspecting a gate; every prior measurement is being re-run from the
start on the amended instruments.

19. **The controller is suspended during the bulk load, and statistics cover
    the measured phase only.** `db_bench` runs
    `rlsuspend → filluniquerandom → resetstats → rlresume → mixgraph → …`.
    Under suspension the RL picker delegates to the native leveled picker
    (`ActionReason::kSuspended`), sends no frame and evaluates no safety
    rule, so every arm reaches `mixgraph` on the same tree and the
    controller's first frame is the first measured operation. `resetstats`
    zeroes tickers and histograms after the load, so $W$, stalls and latency
    are measured over `mixgraph` plus the drain. Load-phase compaction events
    carry `rl_suspended` and form the `load` view of the $\eta$ instrument.
    Metric definitions version `trigger-v2-logical-v3`; not poolable with
    earlier runs. *Corrected by D-11 (2026-09-23): `resetstats` clears
    RocksDB's internal stats only; the `Statistics` tickers and histograms
    the evaluator parsed were never reset, so $W$, stall seconds and write
    latency as scored from 2026-09-20 to 2026-09-23 covered the whole run.
    The evaluator now reconstructs the measured phase from the event log,
    db_bench's per-benchmark histograms and the reset internal stats; every
    `Assoc` arm is re-scored from its existing artifacts.*
20. **`sorted_run_seeks_per_scan` counts sorted runs.** The counter ticks
    once per keyed table seek (each L0 file) and once per level on
    `SeekToFirst`/`SeekToLast`; a scan crossing a file boundary inside one
    level opens no further run. Previously every table seek counted, which
    charged a policy for RocksDB's file cuts. Defined for `Seek`-initiated
    scans, which is the only kind `mixgraph` issues.
21. **Overridden actions are relabelled and kept in replay.** The sample
    keeps the action that actually ran and stays in the replay buffer;
    `capacity.forced_contraction.include_in_q_replay` becomes `true` and
    A-Impl-9 inherits the same rule. Only the five uncontrolled-attribution
    cases drop an interval. Q-learning is off-policy and the guard touches
    15–35% of frames, so dropping them would starve exactly the
    high-pressure states the policy is judged on.
22. **The reward is the constrained objective as written in Pathway D.**
    Point-read probes per Get as a rate; write (windowed, $+\delta_W$), space
    (the rung), latency (average and p99), sorted-run seeks ($+2\%$) and stall
    fraction ($0\%$) enter only as hinges above the manifest's tuned-baseline
    references, each with a dual-ascended multiplier logged per frame;
    shaping is $\Phi = -(\text{L0 files} + \text{non-empty deeper levels})$
    in the $\gamma^\tau$ form. Space is a minimand nowhere. The manifest gains
    `write_amplification_reference`, `space_amplification_reference`,
    `sorted_run_seeks_per_scan_reference`, `stall_fraction_reference` and
    the p99 limits; p95 remains the guard's quantile.
23. **The prior prices reads in absolute runs.** L0 relief is the run count
    net of the output run and, below the trigger, of the runs native
    RocksDB would remove one flush later, with a minimum reduction of two
    runs; a deep level earns no read relief and is charged one exposed probe
    for output into an empty level. The flush size is measured, not read
    from L1's target (the pipeline runs a 2 MiB write buffer against a
    16 MiB base). *The empty-level charge was withdrawn by D-4 (2026-09-22)
    before any policy arm ran under it; the L0 rule stands.*
24. **C-3/C-4/C-6 dominators must be inside the policy's space bound.** A
    hull point whose $S$ exceeds $S_{\text{policy}}(1 + \text{rung})$ is
    reported under `dominated_by_outside_space_bound` and does not decide.
    The 2026-09-19 C-3/C-6 verdicts predate this filter and are superseded
    by the re-run.
25. **Cadence constants are wall-clock.** Normalizer scales freeze after 30 s
    of controlled time, not 50 frames; the seconds-since-compaction feature
    saturates at 30 s.

**P2 (planned, not frozen)** — Pathway F's phase-class thresholds, episode
length, transition-abruptness settings and the F-detect / F-forecast version
must be frozen in a v3 contract before any F run. They are not part of
Programme 1's contract and do not alter it.
