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

---

## 2. Gate verdicts as measured

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

**Two prior conditions, recorded for completeness.** The first execution of this
gate (`results/oracle-parity-assoc`) failed `observation_health` on an
instrument defect, not the controller: suspended worker ticks were counted as
skipped ticks and the first admission after `rlresume` recorded the tail of the
bulk load. Fixed in submodule `25468bbaa`; the narrative is history Section
14.15. And the gate was first invoked without
`--admission-latency-limit-micros 5000`, which both callers pass, leaving
`due_to_admission_latency` unscored; the artifact was regenerated under the
suite's flags. The verdict is unchanged under either invocation.

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
    earlier runs.
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
    16 MiB base).
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
