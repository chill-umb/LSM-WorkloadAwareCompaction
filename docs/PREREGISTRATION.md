# Preregistration and Execution Record

**Status:** the dated companion to `docs/PATHWAYS.md`.

**Condensed on 2026-10-01 at the owner's request.** D-1 to D-12, §2, §3 and §4
are now summaries; D-13 to D-16 are unchanged, byte for byte. The full text before condensation
is `git show cb45743:docs/PREREGISTRATION.md`; it keeps the narrative, evidence
tables and evidence paths left out here.

`docs/PATHWAYS.md` holds the theory, the specification and the done/not-done
status. **This document holds what was decided, when, and what was predicted
before each run**, plus the gate verdicts as measured (split out on
2026-09-20). The narrative is `PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md` §14.
**The rule:** a criterion is never reworded, relaxed or re-scored after its
outcome has been seen; C-2 and E-1 are recorded failed for that reason. An
entry added after the run it governs is worthless.

---

## 1. Decision register

### D-1, 2026-09-20 — the programme's workload becomes UDB `Assoc` (Pathway B1)

Recorded before any run of the re-executed programme and before the Gate 1
re-measurement. **Decision.** Every arm of every gate (baseline sweep, guard
calibration and holdout, `prior_only`, `rl`, `unconstrained_rl`) runs on the
UDB `Assoc` column family of Cao et al., FAST 2020; the uniform family stays as
a named control (`WORKLOAD_SKEW=0`). A hull measured on one workload is not a
valid comparator for a policy measured on another. Not a response to C-3
failing; that failure stands. **Two deliberate departures from the published
fit:**

| Parameter | Published | Used | Reason |
| --- | --- | --- | --- |
| `value_theta` | 0 (mean ~34 B) | **925.5** (mean 960.4) | Holds the mean record size at the project's 960 B, so the level ladder, populated depth and the $T$ sweep stay comparable. Report as *"Assoc key distribution and operation mix at the project's record size"*, **never** as the published value distribution. |
| `mix_max_value_size` | 1024 (db_bench default) | **65536** | db_bench applies it as `val_size % value_max` (`db_bench_tool.cc:7316`), a wraparound, not a clamp. At 1024, 6.85% of draws wrap to as little as one byte and the mean falls to 890.2. |

Other fit parameters take db_bench's defaults. `keyrange_num` = 30 (swept over
$\{5, 30, 100\}$ as the skew axis); delete rate **0** (a nonzero rate is
synthetic, P0-10); no phases (B2). The fingerprint gains
`:skew<keyrange_num>-<value_theta>` only when skew is on. **Predictions.** (1)
The uniform family will not reach write parity, analytically: Theorem B.1 caps
the relative cut in $W - 1$ at the resident-garbage fraction, **79.5% / 39.9% /
36.3%** at $T = 2/6/10$ (D-3 later expected these overstated). (2) Skew raises
resident garbage by B-1's $\ge$ 10pp against the uniform control. (3) Garbage
does not favour the policy, since Hull$_0$ is re-measured on the same workload.
**Falsification:** if B-1 fails, the change bought nothing and the uniform
result stands unqualified.

### D-2, 2026-09-20 — the guard's budget force is memoryless; the guard is kept and scored on the conditional rate

Recorded after E-1's verdict, before any run of the re-executed programme; E-1
stays failed. **Finding** (offline replay of 30 arms of 2026-09-19): at $T=2$,
0.150–0.160 of ready frames per run were forced by the latch alone (`retain =
previous_force_reason == kBudget || kEmergency || …`), which held a level
forced for its whole due run after one frame over a limit. The calibration
modelled no memory, so it predicted 0.99% for 33%. **Decisions.** (1) A budget
or emergency force lasts exactly as long as its condition holds (`retain` drops
`kBudget` and `kEmergency`). (2) `safety_shadow.jsonl` schema 3 logs
`slo_force_due`, `global_debt_breach`, `l0_slowdown` and `pending_debt_ratio`
per frame; `frame_simulated_limits` adds a leave-one-run-out prediction. (3)
The guard is **kept**; the deciding guard criterion is **E-5: the override rate
conditioned on the policy having selected defer, $\le$ 1% per cell, on the
guard holdout and on every learned arm**. E-1's marginal rate is reported
without pass/fail; its 1% is retired, not amended. **Predictions:** (1) the
holdout's marginal rate is below 0.20 in every cell; (2) the leave-one-out
prediction brackets it within a factor of three; the conditional rate stays
below 0.01. If the marginal rate exceeds the leave-one-out mean by more than
3×, the calibration's transfer claim is wrong. **Scored** (§3, "Guard protocol
on `Assoc`"): (1) confirmed, (2) falsified at 7.1× to 12.5×; the conditional
rate cannot be measured on the `oracle` holdout.

### D-3, 2026-09-21 — the space denominator becomes the measured garbage-free size

Recorded after the `Assoc` Hull-0 sweep, before stage 06, the guard protocol
and every learned arm; no criterion using $S$ had yet been scored on `Assoc`.
It does not re-score the 2026-09-12 or 2026-09-19 Gate 1 verdicts.
**Decision.** Space amplification is settled physical SST bytes divided by
`sst_bytes_after_full_compaction`, the garbage-free size the reference
compaction measures. The old `rocksdb.estimate-live-data-size` ratio is kept as
`space_amplification_estimate`. No run is re-executed. **The contract file is
deliberately not edited**, because its bytes are hashed into every run's
fingerprint and an edit would void every past run. **Why:**
`EstimateLiveDataSize` discards live data as well as garbage, more so in a less
compacted tree, so it depends on depth, which the policy changes. Over the 108
`Assoc` Hull-0 runs the garbage-free size spans **0.0060%**, the estimate 76%;
measured $S$ is 1.066–1.090 / 1.039–1.050 / 1.031–1.043 at $T$ = 2/6/10 (the
estimate reported 1.80–2.05 / 1.17–1.30 / 1.11–1.14); $S_{\text{flow}}$ is
**1.386** at every ratio. **Predictions:** (1) stage 06's space filter stops
binding (the four base-16 MiB configurations lie within 0.6–0.8%); (2)
$g_{\text{flow}}$ at $T=2$ falls from 0.611 to 0.278, so the Gate 0 ceilings
are overstated; (3) B-1 is scored on the measured denominator against a
`WORKLOAD_SKEW=0` re-run. **Falsification:** if the garbage-free size is not
stable within 0.5% across a cell's arms, or $S_{\text{flow}}$ under the
measured denominator varies with $T$ by more than 2% at a fixed configuration,
the amendment is withdrawn and reported as withdrawn. **Outcome:** prediction 1
confirmed (§2, Gate 1 on `Assoc`), moving the $T=6$ comparator to trigger 4.

### D-4, 2026-09-22 — the prior compacts a deep level only when RocksDB scores it due

Recorded before any policy arm on `Assoc`. **Finding.** The prior compacted
deep levels at **0.82–0.92 of target** (native: φ-at-release p50 1.01–1.24) and
never deferred due work, though an early deep compaction buys nothing on $R$
(A4) and forfeits overwrites (Theorem B.1); merge survival $\eta$ on `Assoc` is
0.84 / 0.86 / 0.93 at L1 and 0.76 / 0.88 / 0.87 at L2 at $T$ = 2/6/10.
**Decision.** For level $\ge 1$ the urgency term becomes the due indicator
`score >= 1.0` and P1c-23's depth charge is removed: the deep branch
$W_{\text{stall}} \cdot [\text{due}] - W_{\text{work}} \cdot \text{work\_now} -
W_{\text{prem}} \cdot \text{premature}$ is strictly negative below due and at
least +0.2 at due, so **a deep level compacts if and only if RocksDB would.**
L0 is unchanged; the prior's one lever is the L0 proactive band, which exists
only at triggers $\ge 3$. `PRIOR_W_READ` is now inert, kept as an ablation
knob. **Scored 2026-09-22** (three repeats, against the same-configuration
twin): φ@release p50 $\ge$ 0.98 and within 0.05 of the twin at every level $\ge
1$ **FAILED** at $T=6$ (L1 −0.062; passed at $T=2$ and $T=10$); $\eta$ within
±0.03 passed; $\Delta W$, $\Delta R$ within ±2% at the trigger-2 cells passed
(+0.09/+1.14, +0.10/+0.74); $T=6$ $\Delta W \in [+2, +12]\%$, $\Delta R \in
[-3, -12]\%$ passed (+2.07, −5.69); E-5 on `prior_only` zero by construction,
confirmed (0.0006 / 0.0000 / 0.0007). Prediction 3 also asked, at ten repeats,
for an upper 95% bound on $\Delta W \le +2\%$. **For C-3, predicted in advance:
not a pass** — at $T=2$ and $T=10$ the prior sits on its twin; at $T=6$ it is
expected to be dominated by a static point with a lower trigger.
**Falsification:** if (1) fails, the change did not reach the plant; if (1) and
(2) hold and (3) fails, the write excess has a component other than eagerness
that A-0 did not see. Prediction 1 is recorded failed and not reworded; the
rule itself held (**0 of ~20,000** deep releases below due), and the $T=6$ gap
is attributed to the L0 band, which D-5 tested. **Timing:** committed after the
arms, so D-4 and D-5 are weaker records than D-1 to D-3. **Later:** D-11
withdrew "native at trigger 2" at $T=2$.

### D-5, 2026-09-22 — the L0 proactive band is measured at a fixed trigger across ratios

Recorded after the D-4 arms were scored, before the control runs; the
comparator (L0 trigger 2 / 4 / 2 at $T$ = 2 / 6 / 10) does not change.
**Finding.** Stage 06 is decided by runtime: at $T=6$ triggers 2 and 4
tied within 0.20% and trigger 4 won on $W$; at $T=2$ and $T=10$ trigger 2
won by 4.2% and 2.6%. So the band was measured only at $T=6$.
**Decision.** Run `prior_only` and `unconstrained_prior_only` at **L0
trigger 4, base 16 MiB, $T$ = 2 and 10**, 10M, three repeats, as a named
control never pooled. **Predictions:** (1) L0 jobs $\ge 1.15\times$ the
twin at both ratios; (2) $\Delta R < 0$ at both, ordered $|\Delta
R|(T{=}10) > |\Delta R|(T{=}2) > |\Delta R|(T{=}6)$, in $[-3\%,-11\%]$ at
$T=2$ and $[-5\%,-15\%]$ at $T=10$; (3) $\Delta W(T{=}10) > \Delta
W(T{=}6) > \Delta W(T{=}2)$, in $[+0.5\%,+4\%]$ at $T=2$ and
$[+1.5\%,+7\%]$ at $T=10$; (4) L1 φ@release p50 at least 0.02 below the
twin's at both ratios, else D-4's reading of its prediction-1 failure is
withdrawn; (5) zero deep releases below due. **Falsification:** if (1)
fails, the band attribution and D-4's reading are both withdrawn.
**Scored** in §2, "D-5 control scored": 1, 2 (range), 3 (range), 4 and 5
pass; both orderings fail.

### D-6, 2026-09-22 — the reward's space constraint is measured in bytes, not as a ratio

Recorded before any `rl` arm on `Assoc`. The reward's space hinge mixed the
live estimate with D-3's measured reference, so it read +0.751 / +0.134 /
+0.050 from the first frame whatever the policy did. **Decision.** The hinge is
`hinge(physical_sst_bytes, expected_physical_sst_bytes * (1 + rung))`, the
guard's own quantity; no acceptance criterion changes. **Predictions:** (1)
$\lambda_S$ plateaus or returns to zero rather than rising monotonically to
`LAMBDA_MAX` in every cell; (2) $\lambda_W$, not $\lambda_S$, binds —
$\lambda_S > \lambda_W$ at run end on any cell means a reason not identified
here; (3) a `prior_only` re-run has frame-mean space excess below 0.01 at every
ratio. **Falsification:** $\lambda_S$ still rising monotonically on a cell
whose settled bytes finish inside the bound means an incomplete repair.
**Outcome** (§2, D-7 scored): $\lambda_S$ stayed at its initial 1.00 in all six
arms; D-6 confirmed.

### D-7, 2026-09-22 — what the learner is expected to do on `Assoc`

Recorded before any `rl` arm on `Assoc`. **Predictions** (10M, $T$ = 2/6/10,
three repeats, `rl` and `unconstrained_rl`; no 2% criterion scored): (1)
**depth does not inflate** ($\delta L = 0$ against `regular`; load-bearing);
(2) $\lambda_S < \lambda_W$ at run end, and $\lambda_S$ plateaus; (3) E-5
measurable (above 0.001 somewhere) and $\le$ 1% everywhere; (4) argmax flip
rate above 0.1 per level; (5) $\Delta R$ not below −2% at the trigger-2 cells.
**Scored** in §2: 2, 4, 5 pass; 1 and 3 fail; diagnostic, because the latency
multiplier railed.

### D-8, 2026-09-22 — the reward's latency hinge drops the windowed p99

Recorded after D-7 was scored. `lambda_latency` hit `LAMBDA_MAX` = 100.0 in all
six D-7 arms, which D-8 attributed to hinging a 50 ms window's p99 against a
whole-run p99 limit (write latency's average, 16.48 us, sits above its P99,
2.33 us). **Decision.** The latency hinge sums the windowed **average** terms
only; p99 is logged as `latency_terms` and enters nothing. P0-4 is untouched.
**Predictions,** on a re-run of the D-7 matrix (10M, $T$ = 2/6/10, three
repeats, `rl` and `unconstrained_rl`): (1) $\lambda_{\text{lat}}$ stays below `LAMBDA_MAX` in every
cell and `latency_excess` has p50 below 0.05; (2) $\lambda_W$ is the largest
multiplier in every cell; (3) at least 90% of the superseded excess is
`write_p99`; (4) depth is re-tested, and D-7 is not re-scored either way; (5)
E-5 stays above 1% in at least two cells. **Falsification:** if
$\lambda_{\text{lat}}$ still rails with the p99 terms removed, an average term
is also mis-specified; this clause fired at D-9's smoke gate (D-10). **Later:**
D-10 found the diagnosis wrong (the excess was `scan_avg`; D-8's prediction
that at least 90% was `write_p99` is falsified at 0%); the remedy stands.

### D-9, 2026-09-23 — the learner's action space, state, reward and horizon are aligned with the constrained objective

Recorded after the architecture audit (history 14.20), before any arm ran under
it. **Audit findings** (D-7 arms): the write hinge used a 10 s window, so the
learner saw a 13–61% violation where the evaluator saw 3–12%; every multiplier
was a ratchet; the action space offered early compaction and withheld deferral
of a due L0, and the residual released L1 at a median 41% of target (686 of 935
releases below 0.95) and L2 at 38%, and compacted L0 at a mean 1.1 files on
16–26% of below-trigger frames, the depth mechanism of D-7; the state could not
see the constraints; $\gamma$ = 0.95/s was a 20 s horizon; and the prior, in a
dimensionless ±2, was swamped by $Q$ in return units ($|$prior advantage$|$
0.6–0.7 against a residual advantage of 258–652). **Decisions** (each with an
ablation knob; no binary, contract or manifest change):
1. **Flows and levels.** A ratio-of-totals constraint ($W$, $R$, seeks per
   scan, stall fraction) enters the reward as the frame's **signed marginal**:
   numerator minus bound times denominator, over the run-to-date mean
   denominator rate, so its run sum is the constraint the evaluator scores. A
   level constraint (space bytes, latency averages) keeps a hinge on its window
   value. The objective is Get-weighted. Supersedes P1c-22's "windowed" wording
   for $W$.
2. **Signed dual ascent.** $\lambda \leftarrow \text{clip}(\lambda +
   \eta\,\text{slack}, 0, 100)$, slack signed in the constraint's unit
   (run-to-date for flows, window for levels), $\eta$ = 0.05 (sized to the
   single-episode budget). Replay stores the reward as a component vector and
   prices each sample with the multipliers current when it is drawn
   (`rl_agent/lagrange.py`).
3. **Action mask.** A level $\ge 1$ is offered `compact` only when RocksDB
   scores it due; L0 below its trigger only when the compaction nets at least
   `RL_PRIOR_MIN_RUN_REDUCTION` (2) runs; due levels are never masked.
4. **L0 posture.** `rl` and `unconstrained_rl` run
   `RL_L0_ALLOW_DEFER_LEARNED=1`; `prior_only` and `unconstrained_prior_only`
   keep 0, so the D-4/D-5 record stays comparable.
5. **State.** $W$ over bound (run-to-date and windowed), space bytes over bound
   and the five multipliers: 37 inputs (`config.ML_STATE_FIELDS`).
6. **Horizon and loss.** $\gamma$ = 0.98 per second (50 s), credit window 8 s,
   Huber TD loss.

**Predictions** (10M, $T$ = 2/6/10, three repeats, `rl` and `unconstrained_rl`
against same-seed `regular` twins, after a one-arm smoke; a mechanism count, no
2% criterion scored from it): (1) reward $W$ = evaluator $W$ within 2%; (2)
some multiplier ends below its peak in every arm, $\lambda_{\text{lat}}$ never
at `LAMBDA_MAX`; (3) zero releases below φ 0.95 at levels $\ge 1$, mean L0 file
count at a below-trigger compaction at least 2.0; (4) $\lambda_W$ largest at
the end of every `rl` arm, and below its peak where $W$ ends inside the margin;
(5) $\delta L \le 0$; (6) mean paired `rl` $\Delta W$ at most +3% at $T=2$,
$T=10$ and +5% at $T=6$, `unconstrained_rl` below `rl`; (7) if (5) holds, mean
paired `rl` $\Delta R$ at most +2% at $T=2$, $T=10$; (8) E-5 above 1% in at
least two cells; (9) `rl` defers a due L0 on some frames in every cell,
`unconstrained_rl` on a larger fraction. **Falsification:** (3) is the plumbing
check — any early deep release means the mask did not reach the plant and
nothing else is read until fixed; (1) failing means the reward's and the
evaluator's write accounting disagree and the flow ruling is withdrawn; if 3
and 5 hold but 6 fails at the trigger-2 cells, holding due levels costs write
by relocation alone, with no elision gain, and Pathway A is the only remaining
route; if 3 holds and 5 fails, depth comes from deferral (the Theorem A.1
cascade), with the same conclusion. **Smoke gate, 2026-09-23:** 3 and 5 hold;
**1 fails** (+2.9%) and **2 fails** ($\lambda_{\text{lat}}$ 100.0); the matrix
did not run. Cause: three instrument defects, D-10.

### D-10, 2026-09-23 — the multiplier instrument repaired: latency references in the telemetry's unit, the write denominator, and a warm-up

Recorded after D-9's smoke gate stopped; D-8's falsification clause
($\lambda_{\text{lat}}$ still rails with the p99 terms removed, so an average
term is mis-specified) fired, and this entry is its consequence. **Defects.**
(1) The frame's latency comes from the C++ telemetry, the limit from db_bench's
histogram; they differ 19–25× on scan, so the scan hinge read +18 on every
frame while the run was under its limit. That, not write p99, was the D-7
excess. (2) The write denominators differ by WriteBatch framing (2.9%). (3)
Early run-to-date ratios carry the load's backlog, so $\lambda_W$ reached 18 in
the first decile. **Decisions.**
1. **Latency references in the telemetry's unit.** `06_calibrate_live_guard.py`
   writes the six fields `{get,scan,write}_latency_avg_ns_telemetry_reference`
   and `_limit` (2% margin, P0-4). The reward's latency averages become flows
   in that unit, (sum − limit × count) over the run-to-date mean count rate;
   the multiplier's slack is the worst operation's signed run-to-date excess.
   The formal limits remain what the evaluator scores. Manifests regenerated,
   guard fields byte-identical (SHA-256 at $T$ = 2/6/10 `0bb12249…` →
   `d73012e3…`, `25801e34…` → `929e3a42…`, `c4e51d8e…` → `274ba875…`).
2. **Write framing**, 30 bytes per write. *Withdrawn by D-11: it is 16 bytes.*
3. **Warm-up and clip.** No multiplier moves in the first 30 s of controlled
   time (`RL_LAMBDA_WARMUP_SECONDS`); one step is at most `LAMBDA_LR` × 1
   (`RL_LAMBDA_SLACK_CLIP`).

**Predictions:** D-9's 3–9 unchanged; (1) tightens to **1%**; (2) becomes: no
multiplier reaches `LAMBDA_MAX`, one ends below its peak in every arm, and
$\lambda_{\text{lat}} \le 5$ at the end of every arm; (10, new) $\lambda_W <
20$ and $\lambda_{\text{scan}} < 10$ on every `rl` arm. **Falsification:**
$\lambda_{\text{lat}}$ railing or ending above 5 means a latency term is still
mis-specified; (1) failing at 1% means the framing is not a per-operation
constant and the correction is wrong; (10) failing on arms at parity means the
multiplier level is transient-driven, D-3 and D-4 cannot be read from it, and a
trajectory reference is required before any $\lambda$ figure is quoted. **Smoke gate:** the multiplier repair
held ($\lambda_{\text{lat}}$ 0.10, $\lambda_W$ 1.79, $\lambda_{\text{scan}}$
0.0), so 2 and 10 hold, and 3 and 5 hold again; but **prediction 1 failed**
(−1.07%): the two sides measured different phases, and the evaluator was wrong
(D-11).

### D-11, 2026-09-23 — the evaluator scored the bulk load; write, stall and latency become measured-phase quantities, and the `Assoc` record is re-scored

Recorded after D-10's smoke gate stopped. An instrument correction of the D-3
kind, applied to every arm from its artifacts; no criterion reworded.
**Finding.** P1c-19 said `resetstats` zeroes tickers and histograms after the
load. It does not: db_bench's `resetstats` calls `DB::ResetStats`, which clears
RocksDB's internal stats only, and the `Statistics` tickers and histograms in
the end-of-run dump are cumulative since open (`rocksdb.number.keys.written` =
4,029,089 = 2,900,000 load + 1,129,089 mixgraph Puts). From 2026-09-20 to
2026-09-23, write amplification, stall seconds and write latency therefore
covered the whole run (smoke arm stall: 44.0 s against 0.033 s measured-phase).
Whole-run deltas carry the load's scatter and cannot be rescaled; they are
re-scored. **Decisions.**
1. **The evaluator reconstructs the measured phase.** `collect_arm`
   (`04_generate_graphs.py`) returns `write_amplification` as physical write
   bytes from the event log (flush SST sizes joined to flush jobs, plus
   compaction outputs) between the `RL_CONTROL_RESUMED_MICROS` and
   `RL_DRAIN_END_MICROS` stamps, over user bytes equal to the
   `rocksdb.bytes.written` ticker less the load's exact bytes, `load_operations
   × (key + value + 16)`; `stall_seconds` from the internal-stats `Cumulative
   stall` line, which is reset; and latency from db_bench's per-benchmark
   histograms after the mixgraph line. Whole-run values and sources are kept as
   `*_whole_run` and `*_source`.
2. **Manifests regenerated.** Comparators unchanged (trigger 2 / 4 / 2, base 16
   MiB), bound through `--accept-selection-sha256`. References: $W$ → 7.919 /
   8.484 / 13.451, stall fraction → 0.00011 / 0.00006 / 0.0014. SHA-256 at $T$
   = 2/6/10: `d73012e3…` → `41fb57c5…`, `929e3a42…` → `ec5dad11…`, `274ba875…`
   → `4b263068…`.
3. **The framing is 16 bytes** (`WriteBatchInternal::ByteSize`, measured 15.8),
   and **the reward's stall term is off** until a manifest carries a stall
   reference in the telemetry's unit. Stall stays an acceptance criterion and a
   guard term.

**Re-scored** (measured-phase $\Delta W$, whole-run in brackets): D-4
`prior_only` +2.75% [+0.20, +5.30] / +6.32% / +0.45% at $T$ = 2/6/10 (+0.09 /
+2.07 / +0.10); D-5 band +14.43% / +6.62% at $T$ = 2/10; D-7 `rl` +24.4% /
+42.7% / +13.1% (+6.09 / +13.18 / +4.57). So "native at trigger 2" is
**withdrawn at $T=2$** (it holds at $T=10$), the band costs 6–14% of write, and
the learned arms were 13–43% over parity. Every earlier whole-run write, stall
or write-latency figure in the `Assoc` record is superseded. **Hulls:** C-1
still passes (8 / 7 / 8); **C-2 at $T=6$ flips to fail** (the write axis's
run-to-run variation is 1.8% / 1.1% / 0.75%, not 0.20–0.33%), so a 2% write
margin is undecidable at three repeats on this axis (ten-repeat unpaired
half-width about 1.3% at $T=2$) and 14.7's ten-repeat justification does not
hold as stated; the cross-$T$ hull holds 14 of 38 points, 4 / 7 / 3 by ratio,
and only 9 of the 14 are the same points. Oracle parity: unchanged, write
+0.68% [−2.56%, +3.92%]. C-2's top-up on the measured axis is left to its own
entry. **Predictions:** D-10's others unchanged; (1) within **0.5%**, with
`measured_phase_event_log` the source on every arm; (6) `rl` $\Delta W$ at most
**+6%** at $T=2$, $T=10$ and **+10%** at $T=6$, `unconstrained_rl` below `rl`;
(11, new) measured-phase stall below 1.0 s. **Falsification:** (1) failing
means the reconstruction and the reward disagree on the same phase; (6) failing
at the trigger-2 cells while 3 and 5 hold means relocation alone, as D-9 said;
if C-2 cannot be recovered at ten repeats on the measured axis, the write
criterion's margin or repeat count must be re-derived before Gate 5 in its own
dated entry. **Smoke gate:** 1 holds (+0.06%), as do 3, 5, 10 and 11; **2
fails** ($\lambda_{\text{lat}}$ **27.75**): D-10's falsification clause fired;
cause D-12 (the term's time base, not its unit). **Timing:** the D-10 and D-11
work was uncommitted when the smoke ran, so a weaker record than D-1 to D-3.

### D-12, 2026-09-23 — the latency multiplier's slack is measured from the warm-up against the baseline's own trajectory

Recorded after D-11's smoke gate stopped, where D-10's falsification clause
fired; this entry is its consequence. **Finding.** In the first two seconds
after `rlresume` the tree inherited from the load stalls writes: window write
latency in second 0 reads **15.5 and 32.2** times its limit on the two smoke
arms. The baseline carries the same transient, so under a whole-run cumulative
any policy reads positive slack for 120–150 s of a 160 s run. Re-simulated
offline (the re-implementation reproduces the recorded 0.10 and 27.75), with
the slack measured since the warm-up against the baseline's since-warm-up
trajectory the D-11 arm's $\lambda_{\text{lat}}$ would have ended at **4.04**
(4.10 through the regenerated manifest) instead of the recorded 27.75, and at
7.10 with a whole-run cumulative against the baseline's cumulative at the same
elapsed time. **Decisions.**
1. **Trajectory.** `06_calibrate_live_guard.py` writes
   `latency_reference_trajectory`: the cumulative average per operation from
   `--warmup-seconds` (30 s, the reward's `RL_LAMBDA_WARMUP_SECONDS`) on a 1 s
   grid, pooled count-weighted over the calibration runs as of each grid point;
   its end point is `*_latency_avg_ns_telemetry_steady_reference`.
2. **Slack.** $\text{slack}(t) = \bar\ell_{[30,t]} /
   \big((\text{limit}/\text{reference}) \cdot \text{traj}(t)\big) - 1$, worst
   operation, 0 before the warm-up ends. **The priced term is unchanged.**
   `RL_LATENCY_SLACK_SINCE_WARMUP=0` restores D-10.
3. **Manifests regenerated** (SHA-256 `41fb57c5…` → `4fbe8122…`, `ec5dad11…` →
   `8d3223be…`, `4b263068…` → `4c0df483…`).

**Predictions:** D-11's 1 and 3–11 unchanged; (2) as D-10 worded it; (12, new)
the whole-run form, simulated offline from the logged
`latency_slack_whole_run`, ends above the recorded $\lambda_{\text{lat}}$ in
every arm; (13, new) each operation's since-warm-up average ends within ±10% of
the trajectory. **Falsification:** $\lambda_{\text{lat}}$ above 5 in this form
means the residual is not the transient (incomplete repair), with (13) locating
it; (12) failing on an arm means the transient reading of the D-11 arm does not
generalise to that arm. **First smoke attempt: invalid, nothing scored.** Two
defects, fixed before the retry: (1) `rl_safety_manifest.cc` reads each key by
its **first textual occurrence**, so the trajectory block's nested
`"schema_version": 1` made the C++ side reject the manifest while Python
accepted it, so the guard ran its all-due fallback; the key is now
`trajectory_schema_version`, and the calibrator refuses any manifest in which a
key `RLSafetyController::Parse` reads occurs other than once
(`CPP_FIRST_MATCH_KEYS`, in lockstep with `Parse`); SHA-256 → **`9e0faa5e…`** /
**`2e19c6fc…`** / **`52079b1e…`**. (2) The first controlled frame's window
opens at the last suspended tick, not at `rlresume`, and carried 14.7 MB of
load writes; the reward now leaves that frame's flows out of every run-to-date
total (`RL_REWARD_DROP_RESUME_FRAME`, default on), which moves this arm's $W$
gap from −1.07% to −0.03%. **Two observations from that near-native arm, used
for nothing:** $\lambda_{\text{lat}}$ ended at 4.63 (17.86 under the old form),
just under prediction 2's 5, with get and scan latency 5.6% and 5.1% over the
trajectory, which prediction 13's ±10% covers and prediction 2 may not; and its
$W$ sat +2.7% over its twin against a measured-phase write variation of 1.8% at
$T=2$, the yardstick for the earlier smoke arms' +4.4% to +6.5%. **Scored** in
§2, "D-12 scored".

### D-13, 2026-09-30 — Programme 1: the priced objective, its prices, the Gate N2 workloads, the static comparator, the settle rule and the stall rule

**Recorded before any run of Programme 1.** The Programme 1 binary does not
exist yet (Gate N0 items 2–7 are unbuilt), so no arm, pilot or calibration
run governed here has been made. The theory is `docs/PATHWAYS.md` as
committed with this entry; this entry fixes only the values and choices that
PATHWAYS §0.6 leaves to preregistration, for everything Gate N2 uses (§0.6
items 1, 2, 4, 5, 6, 8, 9 and 12). Items 3, 7, 10 and 11 are later entries:
the admission test and run length (item 7) before Gate N1, the controller's
action bounds (item 3) before Gate N3, the echo choice (item 11) before Gate
N4, the claim (item 10) before Gate N5.

**1. The objective (§0.6 item 1).**

- For this fork the constrained objective of P1c-22 is replaced by the priced
  cost $J_\beta$ of PATHWAYS D §2, in the four modes of that section's table.
  There are no hard limits; the guard and the latency bounds move to
  Programme 2.
- **Headline $\beta^\star = 10$.** $J_\beta$ is also reported at
  $\beta^\star \in \{2, 5\}$, and $\bar\beta$ of Proposition D.4 is reported
  per workload, so a reader can see whether 10 sits in the strict-priority
  regime.
- **Prices.** $c_w$, $c_f$, $c_{blk}$ and $c_{sk}$ are the device time of one
  operation of each kind, measured on the node (Gate N0 item 7, OBJ-2), times
  the instance price. Both money prices are AWS `us-east-1` on-demand list
  prices in USD, retrieved 2026-09-30.
  - **Instance price: \$0.974 per hour for `m8a.4xlarge`, i.e.
    \$2.7056 × 10⁻⁴ per device-second.** It is the public instance closest to
    the node, Chameleon `compute_zen5_grado` (AMD EPYC 4545P, 16 Zen 5 cores
    with SMT off, 64 GB RAM, local NVMe). It has 16 vCPUs, each a physical
    5th-generation EPYC core (AWS's M8a page), and 64 GiB. Sources: the AWS M8a
    instance page and the Vantage `m8a.4xlarge` page.
  - **Storage price: EBS `gp3` at \$0.08 per GB-month, i.e.
    $c_s$ = \$3.0441 × 10⁻¹⁷ per byte-second**, taking a GB as 10⁹ bytes and a
    month as 730 hours (the AWS pricing calculator's month). Reading GB as
    2³⁰ bytes would move $c_s$ by 7%, well inside the reported range. Block
    storage is used because it has a per-byte price; an instance with local
    NVMe bundles its disk into the hourly rate and has none.
  - Together: holding 1 GB for one hour costs as much as 0.41 s of the whole
    machine's time. Only this ratio matters, since the instance price scales
    the write and read terms together.
  - $c_s > 0$. Every result is also reported at $c_s/2$ and $2c_s$. Prices
    are re-measured on any hardware change and recorded in the fingerprint.
    The node is free to the project, so these prices stand for what the
    resources cost a typical deployment, not for this project's bill.
- **$\bar q$, one per workload.** $\bar q$ is the native arm's measured-phase
  throughput at $T = 10$: operations served from $n_w$ to the end of
  `mixgraph`, divided by the wall time between those two stamps, averaged over
  that arm's ACT-4 parity repeats on the Programme 1 binary. The native arm
  runs $m \equiv 1$, the configured $K_0$ and the pipeline's default options.
  - It is a measurement fixed by a rule, so its value is added here as a dated
    amendment when measured, and frozen before any $\Theta_s$ run.
  - Why one value per workload: C-6 compares $J_\beta$ across $T$, which needs
    one price scale per workload. Why $T = 10$: it is RocksDB's default fanout.
  - Resulting node order: ACT-4, then $\bar q$, then $\Theta_s$.

**2. Write accounting (§0.6 item 2): P0-7 amended.** For this fork the M3
convention is replaced by Lemma D.7's exact identity. The overlap constant
$c_i = o_i/f_i$ is measured per level from the event log ($\rho_i$, $o_i$ and
$t_i$ per job), and a trivial move counts as writing nothing. The
$\approx 3.6$ optimum quoted under P0-7 is not quoted for this fork. The
evaluator's $W$ is unchanged, since it always summed SST bytes; what changes
is the model used to explain it. P0-7 stands as the record of the 2026-09-11
programme.

**3. The Gate N2 workloads (§0.6 item 4).**

- **`Assoc`**, exactly as D-1: the fit, both deviations, `keyrange_num` = 30,
  delete rate 0.
- **A read-heavy power-law workload.** Label in the paper: *"YCSB-B operation
  mix with power-law key popularity"*. It is never labelled Zipfian or
  YCSB-B: `db_bench` at `25468bbaa` has no Zipfian generator. Adding one was
  considered on 2026-09-29 and not chosen.
  - `mixgraph` with `mix_get_ratio` 0.95, `mix_put_ratio` 0.05 and
    `mix_seek_ratio` 0.
  - `keyrange_dist_{a,b,c,d}` = 0 and `keyrange_num` = 1, so `mixgraph` skips
    prefix modelling. It draws a seed from the power law
    $f(x) = a\,x^b$ (`PowerCdfInversion`) and scrambles it with `Random64`
    (`tools/db_bench_tool.cc` at `25468bbaa`, lines 7215–7243).
  - `key_dist_a`, `key_dist_b` = 0.002312 / 0.3467, `Assoc`'s own key-hotness
    fit, so the two workloads differ in their operation mix, not in how
    popular their keys are.
  - Value-size fit, record size and load count are as for `Assoc`.
- **Fingerprint.** Each run's family and mix parameters are in its
  fingerprint. The new segment is emitted only for the power-law family, in
  lockstep with the parser in `06_select_baseline_slo.py`, so `Assoc` runs
  stay poolable.

**4. The static class $\Theta_s$ (§0.6 item 5).** As PATHWAYS C §1:

- $K_0 \in \{2, 4, 8\}$, restricted to admissible values (A-Impl-6).
- Base size $\in \{8, 16, 32\}$ MiB.
- Four multiplier profiles:
  - uniform 1;
  - survival-weighted (Theorem A.2), at the measured $c$ and $v_i = a_i - t_i$
    measured on the native arm's runs of the same (workload, $T$);
  - last-level-emptying: the level just above the native arm's deepest
    populated level (same workload and $T$, on its settled tree at $n_w$)
    held at 2× from the load, every other level at 1. This is the 2026-09-23
    audit's untested idea (§3). Scaling every upper level instead would repeat
    the base-size axis, since uniform 2× is the "base 32 MiB" point measured
    again. A-Impl-7 holds, because the last level's target, at $1 \cdot T$,
    is at least 2 for every $T$ in the class;
  - uniform 0.75.
- `compaction_pri = kMinOverlappingRatio`, pinned.
- $T \in \{2, 6, 10\}$. For the cross-$T$ check (C-6), each mode's
  $\theta^\star_\beta$ at $T = 10$ is re-run at $T = 14$ and $20$, per
  workload: at most four configurations each. The rule is fixed here; the
  configurations are known only once the $T = 10$ runs are scored.

The comparator is $\theta^\star_\beta(w) = \arg\min_{\Theta_s}J_\beta$ over
measured, seed-paired runs, per workload and mode.

**5. Repeats (§0.6 item 8).**

- **Five** seed-paired repeats per cell first; $\sigma_d$ is estimated from
  them.
- Each cell is then topped up to the smallest $n$ with
  $n \ge (t_{0.975,n-1}\,\sigma_d/h)^2$, where $h$ is a quarter of the gap in
  $J_\beta$ between $\theta^\star_\beta$ and the next-best static point, per
  (workload, $T$, mode).
- Runs are same-session and interleaved, with the session id recorded
  (CMP-8).

**6. The measured phase starts on a settled tree (§0.6 item 6).** This
amends PATHWAYS H §5's wake-up rule, under which the post-load backlog
drained under live `mixgraph` traffic and $n_w$ was a fixed operation number
set from pilot runs, so the operations before it went unscored. Decided so
that every `mixgraph` operation is measured.

- After the bulk load, `db_bench` issues no operation until the tree has
  settled: a new `settle` step calls RocksDB's `WaitForCompact` with flushes
  included, then holds for $h_w$ = **10 s**, during which every level's score
  must stay below 1 and $k_0 < K_0$. The fork's `waitforcompaction` step
  makes the same call (`tools/db_bench_tool.cc` at `25468bbaa`, lines
  8931–8953), but it is not reused, because it also enters the old stack's
  drain mode. ($h_w$ is H §5's window $h$, renamed only to keep it
  apart from item 5's half-width.)
- $n_w$ is the first `mixgraph` operation. Every arm, native included, is
  scored from $n_w$ to the end of the drain, on the tree its own load left.
- The controller is suspended through the load and the wait, so the tree
  settles under the arm's own configured settings, and it starts at $n_w$.
- An arm not settled at the end of the hold is invalid. It is reported, and
  the rule is not loosened after the fact (A8).
- No pilot runs are needed.

**7. Retirements (§0.6 item 9).** For this fork:
- P0-1 is retired as a constraint, because scans are priced through
  $c_{sk}R_{sk}$. `sorted_run_seeks_per_scan` stays the scan metric.
- P0-3 ($\delta_W$), P0-6 (the $S_{\text{bound}}$ ladder), P1-15, P1-16,
  P1c-22 and P1c-23 are retired.
- P0-4 (latency) moves to Programme 2. Latency is reported as a `dio0`
  warm-cache diagnostic.

Their records stand, and nothing scored under them is re-scored.

**8. The stall rule (§0.6 item 12).** Every claim also requires, on the same
paired runs and reported beside $J_\beta$ rather than priced into it:

- **Stalls, judged as a fraction.** Stall fraction is measured-phase stall
  seconds divided by measured-phase wall time. Stall seconds are the
  internal-stats `Cumulative stall` line differenced over the measured phase
  (D-11), never `rocksdb.stall.micros`. The upper 95% paired bound on
  (controller − comparator) stall fraction must be at most
  $\delta_{\text{stall}}$ = 0.02, i.e. 2 percentage points.
  A fraction is used because a relative margin on stall seconds is undefined
  when the comparator never stalls.
- **Throughput.** The lower 95% paired bound on the relative difference in
  measured-phase operations per second must be at least
  $-\delta_{\text{thr}} = -2\%$.

**Predictions, recorded in advance.** PATHWAYS D §5 as committed with this
entry. In brief: little room in read priority on stationary `Assoc`; write
priority's room is what holding a level adds to native's high drops; space
priority on `Assoc` is capped at the 3–8% resident garbage.

**Contract.** `config/research_objective_contract.json` holds this entry's
values in machine-readable form and is frozen in the same commit. Contracts
v1–v3 are deleted (owner, 2026-09-30); they remain recoverable from commit
`5bea343`.

**Falsification.** This entry sets values, not hypotheses. It fails as a
record if any value marked here is changed after an arm it governs has run.
Such a change is a new dated entry that names this one and states what it
supersedes.

### D-14, 2026-09-30 — D-13 amended: the stall rule's time base, how q̄ is measured, and where and how the two measured profiles are computed

**Recorded before any run of Programme 1.** No arm governed by D-13 has run:
the Programme 1 binary has not been built (no node exists). This entry names
D-13 and supersedes the parts stated below; everything else in D-13 stands.
The owner decided items 1 and 2 and asked for item 3's stage on 2026-09-30:
the time base after the evaluator's verifier flagged it as ambiguous, and
$\bar q$'s measurement after the implementer found that ACT-4 cannot supply
it. The implementer's choices within items 2 and 3 (the Gate N2 run length
for $\bar q$; profiles per grid point, run after that point's native arms;
the common $c$ reported, not used; clipping to $[0.5, 2]$; flows over
`mixgraph` only) were confirmed by the owner on 2026-10-01, before any run.

**1. The stall rule's time base (supersedes D-13 §8's "measured-phase wall
time" and its "measured-phase operations per second").** The stall fraction
is measured-phase stall seconds divided by
`mixgraph`'s wall time: from the `measure_start` stamp ($n_w$) to the
`drain_start` stamp. Stall seconds are still the internal-stats stall counter
differenced from $n_w$ to the end of the drain (D-11); the drain has no user
writes, so the two spans give the same stall seconds. Throughput uses the
same span, as D-13 §1's $\bar q$ does.
- *Why.* Read literally, D-13 §8 divided by the whole measured phase, drain
  included. A policy that defers work until the drain lengthens the drain and
  so lowers its own stall fraction: the literal rule rewarded the deferral
  the stall rule exists to bar. Stalls can only occur while `mixgraph`
  writes, so its time is the base on which they are a fraction.
- The margins are unchanged: at most 0.02 on the stall fraction, at least
  −2% on relative throughput.

**2. How $\bar q$ is measured (supersedes D-13 §1's "averaged over that arm's
ACT-4 parity repeats").** ACT-4 runs at T=2, 1M operations and without the
settle step, so it cannot give the native arm's measured-phase throughput at
T=10. Instead, per workload:
- five `native` arms at T=10, run through `03` with the settle step and the
  pipeline's default options, at the Gate N2 run length (Gate N1), on the
  Programme 1 binary that passed the preflight;
- $\bar q$ is the mean of their `throughput_ops_per_second` (operations from
  $n_w$ to the end of `mixgraph`, over that span's wall time, as `04`
  reports it);
- the value is added as a dated amendment and frozen before any $\Theta_s$
  run, as D-13 §1 already requires.

**3. The two measured profiles of $\Theta_s$ (supersedes D-13 §4's "the
native arm's runs of the same (workload, $T$)" and fixes the computation).**
- **Where.** Both profiles come from the `native` arms of the same
  (workload, $T$, $K_0$, base size) point of $\Theta_s$, pooled over their
  repeats, not from one default-configuration native arm per (workload,
  $T$): the survival-weighted $m_1 = f_0K_0F/C_1$ depends on $K_0$ and $C_1$,
  and the depth $L$ on the base size. So each (workload, $T$) has up to nine
  vectors of each profile. A point's profile arms run after its native arms,
  in the same session (CMP-8's session id), not interleaved with them: for
  these arms this supersedes D-13 §5's "interleaved", since a profile cannot
  run before the runs it is measured from.
- **How**, by `scripts/dbbench_pipeline/23_static_profiles.py`:
  - *The settled tree.* $L$, the deepest populated level, and $B_L$, its
    bytes, are read at $n_w$: from the first `compaction_release` snapshot
    after the `measure_start` stamp, before which only flushes (into L0)
    change the tree. $L$ must agree across the pooled runs.
  - *Last-level-emptying*: $m_{L-1} = 2$, every other level 1.
  - *Survival-weighted* (Theorem A.2(ii)): $f_i = \lambda/v_i$ for
    $i = 0..L-1$, with $v_i$ the merged bytes leaving level $i$ per user
    byte (Lemma D.7's $a_i - t_i$, from the host log's job records) and
    $\lambda = \big(\tfrac{B_L}{K_0F}\prod_iv_i\big)^{1/L}$; then
    $m_1 = f_0K_0F/C_1$ and $m_{i+1} = f_im_i/T$ for $1 \le i \le L-2$; the
    levels from $L$ down keep $m = 1$.
  - $v_i$ and $F$, the mean flush file size, are measured over `mixgraph`
    only ($n_w$ to the `drain_start` stamp): these are steady-state flows,
    and the drain is a transient whose last flush is partial.
  - A common overlap constant $c$ cancels from the optimum, so "at the
    measured $c$" (D-13 §4) means $c_i = o_i/f_i$ is measured and reported
    per level, not used; if the $c_i$ differ, that is a limit of Theorem
    A.2's assumption, reported with the profile.
  - An entry outside $[0.5, 2.0]$ (A-Impl-7's bounds) is clipped to it and
    the clipping reported. A profile that would then make a level's target
    smaller than the level's above is refused, not repaired.
  - A level with no merged bytes leaving it ($v_i = 0$) has no finite
    optimum; that profile is then not computed, the reason is recorded, and
    the other profile is still computed. Merges out of level $L$ or below
    (the tree deepened during the phase) are reported.
  - Theorem A.2's fixed-point step stands: the same computation on the
    profile's own runs gives the next iterate.

**Falsification.** As D-13's: this entry fails as a record if any of it is
changed after an arm it governs has run.

### D-15, 2026-10-01 — the stall rule's bounds, suite robustness without the two measured profiles, and how the device prices are measured

**Recorded before any run of Programme 1 and before any price was
measured.** No arm governed by D-13 or D-14 has run (no node exists). The
owner decided all three items on 2026-10-01, on the implementer's
recommendations. This entry names D-13 and D-14 and supersedes only what is
stated below.

**1. The stall rule's bounds (D-13 §8).** D-13's "upper 95% paired bound" on
the stall-fraction difference and its "lower 95% paired bound" on the
relative throughput difference are the two ends of the two-sided 95% paired
Student-t interval (`pipeline_stats.ci95`), so each is a one-sided 97.5%
bound.
- *Why.* It is the stricter reading, and it is the interval every other
  criterion uses (CMP-3, C-2, D-13 §5's top-up rule), so a claim passes the
  stall rule and CMP-3 on one interval.
- One-sided 95% bounds ($t_{0.95}$) were considered and not chosen.
- The margins are unchanged: $\delta_{\text{stall}}$ = 0.02 and
  $\delta_{\text{thr}}$ = 2%.

**2. Suite robustness (CMP-7, Definition C.5) and the two measured
profiles.** The survival-weighted and last-level-emptying profiles are
computed per workload (D-14 §3), so on two workloads the same profile name is
in general two different multiplier vectors: not one static setting across
the suite.
- In CMP-7's min–max, $\min_{\theta}\max_w\text{Reg}_\beta(\theta, w)$,
  $\theta$ ranges only over configurations run with the same setting on
  every workload: the same fingerprint once the workload's own segments are
  removed (`07_evaluate_paired.config_key`). A measured profile competes
  there only if its vector is the same on every workload.
- Both profiles stay in $\theta^\star_\beta(w)$ on each workload, so every
  regret, CMP-3 and C-6 are measured against the whole of $\Theta_s$.
- *Consequence, stated beside every CMP-7 result.* Removing competitors can
  only raise the min–max, so this CMP-7 is easier for the controller to pass
  than one over the whole class. The configurations left out are listed with
  each result (07's `excluded_configurations`).
- Not chosen: running each workload's vectors on the other workload (more
  node time), or one vector pooled across workloads (a setting measured for
  no workload).
- If the claim entry (§0.6 item 10) makes suite robustness a headline, it
  revisits this item before Gate N5.

**3. How the device prices are measured (D-13 §1; PATHWAYS D §1, OBJ-2).**
D-13 fixed the two money prices but not how $c_w$, $c_f$, $c_{blk}$ and
$c_{sk}$ are measured. The draft stage `18` charged every operation the whole
machine's price and divided each benchmark's whole time by the unit it
prices, so a probe's price carried the Get's fixed overhead. Replaced by:

*(a) The unit of device time is one core-second.* Every priced operation
runs on one thread: the reads on `db_bench`'s one client thread
(`THREADS=1`), each flush and compaction on one background thread
(`max_subcompactions` is left at RocksDB's default, 1). It occupies one of
the instance's 16 cores, so one second of it is priced at \$0.974 per hour
÷ 16 = **\$1.6910 × 10⁻⁵ per core-second** (contract
`price_per_core_second`). D-13's "instance price per device-second" charged
a one-thread operation for all 16 cores, which overstated the write and read
terms 16-fold against $c_s$. Holding 1 GB for one hour now costs as much as
6.48 core-seconds (D-13's 0.41 s was whole-machine seconds). $c_s$ is
unchanged.

*(b) $c_w$ from the experiments' own jobs.* Per run, $t_w$ is the wall time
of the flush and compaction jobs whose bytes $C_W$ counts, divided by those
bytes:
- a compaction's time is its `compaction_finished` event's
  `compaction_time_micros`; a flush's runs from its `flush_started` to its
  `flush_finished` event;
- the jobs, bytes and window are exactly `04`'s $C_W$: D-11's SST bytes,
  between the `measure_start` and `drain_end` stamps. `04` reports the time
  per arm as `sst_write_seconds`;
- the runs are the $\bar q$ arms of D-14 §2 (five `native` arms at $T=10$
  per workload, both workloads). $c_w$'s device time is the median of their
  per-run $t_w$; the minimum and maximum are reported;
- wall time, not CPU time: a job's I/O waits hold its thread too. A
  compaction's `compaction_time_micros` ends before its MANIFEST install,
  while a flush's span includes its install; the difference is one
  MANIFEST write and one directory sync per job, and is left in;
- *why:* the draft timed one full manual `compact` of a 1M-key tree, one
  large job unlike the experiments' compactions. The $\bar q$ arms' jobs are
  the experiments' own, at their geometry, load and run length.

*(c) The read prices are marginal times, from trees of different depth.*
- *Trees.* Three trees are loaded as the experiments load them:
  `filluniquerandom` of the Programme 1 load (2.9M keys, D-16 §2), the
  pipeline's default options ($K_0$ = 4, base 16 MiB), then the `settle`
  step; one each at
  $T$ = 2, 6 and 10, the $T$ values of $\Theta_s$. Their depths differ, so
  their probes per Get and runs per seek differ.
- *Runs.* On each tree, one unscored `readrandom` warms the page cache
  (the experiments are warm-cache, `dio0`). Then `readmissing`, `readrandom`
  and `seekrandom` (`seek_nexts` 0), 1,000,000 operations each, each in its
  own process so its tickers are its own, in turn, five times: five repeats.
- *$t_f$ and $t_{blk}$*, per repeat: one least-squares fit over the six
  (tree, benchmark) points of `readmissing` and `readrandom`,
  $$\text{seconds per Get} = a + t_f \cdot \text{filter probes per Get} +
  t_{blk} \cdot \text{block-reading probes per Get},$$
  with probes from `point.sst.probe` and block reads from
  `bloom.filter.full.positive`. `readmissing`'s Gets end filter-rejected
  (bar false positives), so across the trees they move the probes;
  `readrandom`'s read the block their key is found in, so on each tree the
  pair moves the block reads. The intercept $a$ is the overhead every Get
  pays whatever the tree holds (key generation, memtable and version
  lookup), which is not the price of a probe. Fitting both prices together
  keeps `readmissing`'s false-positive block reads out of $t_f$.
- *$t_{sk}$*, per repeat: the least-squares slope, across the three trees,
  of seconds per seek on run seeks per seek (`sorted.run.seek`) in
  `seekrandom`.
- Each price's device time is the median of its five per-repeat values; the
  minimum and maximum are reported.
- *Assumptions, stated:* the per-operation overhead does not change with the
  tree's depth, and it is the same for a present and a missing key (copying
  the found value out is taken as nil); a filter probe, a block read and a
  run seek cost the same at every level. The last is approximate: with
  `open_files` = 1000 and about 5,700 SST files of 512 KiB, deep probes often
  reopen their table, and a reopen's cost lands partly in the slope and
  partly in the intercept. The experiments run with the same table cache, so
  the prices carry the same mix. To audit it, `prices.json` keeps every read
  process's seconds and tickers per operation (`rocksdb.no.file.opens`
  among them) and each tree's load command.

*(d) Refusals.* Stage `18` writes no prices when:
- any per-repeat or per-run time is not positive;
- the trees' filter probes per Get in `readmissing`, or run seeks per seek,
  span less than 1, so the slope is not identified;
- on any tree `readrandom` reads fewer than 0.5 more blocks per Get than
  `readmissing`;
- fewer than five write runs of each workload are given, one is given
  twice, or any is not a settled `native` arm at $T=10$ of a contract
  workload, lacks a positive `sst_write_seconds` or SST byte count, or ran a
  different `db_bench` binary from the one being priced;
- `THREADS` is not 1.

`04` refuses to score an arm whose `prices.json` is not schema 2 at the
contract's `price_per_core_second`, so a draft file priced per whole machine
cannot enter $J_\beta$.

*(e) Node order.* ACT-4; Gate N1 (the run length); the $\bar q$ arms, run
without prices, which $\bar q$ does not need (`04` scores them "no prices");
then `18`, which reads the $\bar q$ arms; then $\Theta_s$. Prices are
re-measured on any hardware or binary change.

**Contract.** `config/research_objective_contract.json` is amended in place:
`price_per_core_second` (with `instance_price_per_second` and
`instance_cores`) replaces `instance_price_per_device_second`; the stall
rule records its bounds; a `suite_robustness` block records item 2.

**Falsification.** As D-13's: this entry fails as a record if any of it is
changed after an arm or a price measurement it governs has run.

### D-16, 2026-10-01 — Gate N1: pilot runs, the admission test's fixed values, and the run-length rule

**Recorded before any run of Programme 1.** This is PATHWAYS §0.6 item 7,
which D-13 left for an entry before Gate N1. The owner decided on 2026-09-30
that the earlier programme's runs are out of scope for Programme 1, so Gate N1
cannot run "on existing artifacts" as PATHWAYS said. On 2026-10-01 the owner
asked the implementer to draft this entry. **Status: the values below are the
implementer's proposals. The owner may change any of them in place, with a
written reason, until the first Gate N1 pilot run starts, and never after
it.** Their machine-readable form is `config/admission_test.json`.
PATHWAYS Gate N1 and G §4 are amended in the same commit.

**1. Gate N1 runs pilot native arms (supersedes PATHWAYS Gate N1's "on
existing artifacts, no node time").**
- Per workload (`Assoc`, the power-law workload) and $T \in \{2, 6, 10\}$:
  three `native` arms through `03` with the settle step and the host log, on
  the Programme 1 binary that passed the preflight. They run at the
  pipeline's default point: $K_0$ = 4, base 16 MiB, $m \equiv 1$, the
  configuration a controller arm starts from and drains to (A-Impl-8).
- Pilot length: the second rung of item 2, 29M operations at 10% load, so
  2.9M keys are loaded and `mixgraph` serves 26.1M operations (about 7.5
  minutes at `Assoc`'s buffered rate). That is 18 runs, roughly 3 to 4
  hours.
- Pool membership is decided per (workload, $T$) at this point (PROP-1).

**2. The load is fixed; a longer run lengthens `mixgraph` only.**
- Every Programme 1 tree loads **2.9M keys**: the 10M × 29% load of every
  earlier record, about 3 GiB of `Assoc`. PATHWAYS G §4's scope decision
  (a pool at T=2 in L2–L6, none at T=10) is stated for this tree, and
  loading more would deepen the tree and change the pools.
- `03` sets the load as size × `LOAD_PERCENT` / 100, so the run lengths are
  rungs with the same load: (size, load %) = (10, 29), (29, 10), (58, 5),
  (145, 2), (290, 1), i.e. 7.1M, 26.1M, 55.1M, 142.1M and 287.1M `mixgraph`
  operations.
- `03` refuses a Programme 1 arm above 2M operations that loads any other
  count. Stage `18` loads the same count (D-15 §3c). The count comes from
  the config file.

**3. The candidates and the reference level.**
- The reference level is **L2** in every cell: the shallowest candidate.
- The candidates follow G §4's scope decision. They run from L2 to $L-2$,
  where $L$ is the deepest populated level of the settled tree at $n_w$.
  $L-1$ joins them only when the last level is **near its target**, which
  is fixed here as holding at least **half** its target, $B_L/C_L \ge 0.5$,
  at $n_w$. Otherwise $L-1$'s fanout $f_{L-1} = B_L/C_{L-1}$ is set by the
  last level's fill, not by $T$: at T=10 on `Assoc`, $f_3 \approx 0.6$.
  - Expected: at T=2, L8 is last and the candidates are L2–L6, plus L7 if
    L8 is at half its target. At T=10, L4 is far below its target, so the
    only candidate is L2, and there is no pool.
- `19` reads $L$ and $B_L/C_L$ from the first `compaction_release` after
  the `measure_start` stamp. The candidate set belongs to the cell: $L-1$'s
  candidacy uses the **mean** of the cell's runs' $B_L/C_L$, so runs on
  either side of 0.5 cannot split it (amended in place on 2026-10-01,
  before any pilot run, after the estimate of 0.44–0.47 at T=2 put the
  threshold within reach of run-to-run spread). It refuses runs whose
  depths differ, and a tree too shallow to hold L2 as a candidate.
- L1 and the last level are never candidates (G §4: L1 is fed in L0-sized
  batches; the last level has no level below it). L1 keeps its own model.

**4. The compared statistics and their margins**, in each statistic's own
units, per (workload, $T$), the same for both workloads:

| Statistic | Margin | Reason |
| --- | --- | --- |
| fill sampled every $1/k$ turnover | 0.05 of a level | a twentieth of a level; the file granularity $F_{\text{sst}}/C_2$ is 1/64 at T=2 (512 KiB files, base 16 MiB), smaller at larger $T$ |
| fill at release | 0.05 | as above |
| bytes released per turnover, in units of $C_i$ | 0.10 | a tenth of a level per turnover |
| $(1-\xi_i)(\rho_i + o_i)$, bytes written per byte released | $0.1(1+T)$: 0.3, 0.7, 1.1 | a tenth of the nominal $\rho + o = 1 + T$ |
| $\tilde\rho_i$, the share that lands below | 0.05 | five points of pass-through |
| inflow ratio $\ell_i$ | 0.20 | a fifth of the mean rate |

The mean, 10th and 90th percentile differences must each have a 90%
interval inside $\pm$ the margin (G §4).

**5. The fixed values of the test.**
- $\omega_{\max}$ = **0.25** decision intervals: the upper 95% bound on a
  level's mean slot wait per release must be below a quarter of its decision
  interval. By G.4's heuristic, a release then crosses a decision boundary
  with probability under about a quarter.
- $k$ = **10**: fills are sampled ten times per turnover, and $\omega$ is in
  units of $N_i/10$. This also fixes the controller's cadence (G-iv): level
  $j$ decides every $N_j/10$ operations. A controller with another $k$
  needs this test re-run.
- Block length $b$ = **3** turnovers.
- Bootstrap replicates: **1,000**. Seed: **20261001**.
- **$n_{\min}$ by rule**, per cell, from the pilot's own reference
  turnovers: the smallest $n$ in {20, 30, 40, 60, 80, 120, 160} for which
  G §4's simulation, with 200 trials, finds that two pseudo-levels pass with
  probability ≥ 0.8, and that the pair with any one statistic shifted to
  its margin passes with probability ≤ 0.05. `19` tries the grid in order
  and stops at the first sufficient $n$.
  - A grid value above half the reference's pooled turnovers is not tried,
    since its two pseudo-levels would mostly share turnovers. This removes
    the gross overlap, not the pool's own sampling error. On synthetic data,
    the simulated pass rate at $n$ = 40 ranged over 0.11–0.47 across
    reference pools of 80 turnovers and 0.38–0.45 across pools of 1,000.
    $n_{\min}$ is therefore itself an estimate from the pilot, with an
    uncertainty well above the Monte Carlo error below. The simulation also
    draws each pseudo-level as one run, while the real test resamples
    within each of three runs.
  - The rule runs only in a cell with a candidate besides the reference.
  - With 200 trials, the Monte Carlo standard error is about 1.5 points at
    5% and 2.8 points at 80%, so a value near either threshold can fall on
    either side.
  - *Cost, measured on synthetic turnovers:* about 12 CPU-minutes per grid
    point at $n$ = 40 when a turnover has 64 releases (L2 at T=2, about
    $C_2/F_{\text{sst}}$), and about 47 at 320 (T=10). The cells run as
    separate processes.
  - If no value tried is sufficient, that cell's candidates are undecided
    at Gate N1. They then wait for a new dated entry.
- A candidate is **undecided** when it, or the reference, has fewer than
  $n_{\min}$ turnovers across the pilot. It is decided on Gate N2's
  `native` arms at the default point. Those arms then run at least
  $\max(5, \lceil n_{\min}/n_{\text{turn}}\rceil)$ repeats: PATHWAYS
  Gate N1's $\lceil n_{\min}/n_{\text{turn}}\rceil$ runs of
  $n_{\text{turn}}$ turnovers, and at least D-13 §5's five.

**6. The run-length rule (PATHWAYS Gate N1).** $n_{\text{turn}}$ = **10**.
- Per cell, the governing level is the deepest candidate that is admitted
  or undecided. An undecided candidate is sized as if pooled (PATHWAYS
  Gate N1), so that Gate N2's native arms can decide it. When every other
  candidate is refused, the governing level is L2, whatever L2's own
  decision (PATHWAYS: "its deepest interior level with enough turnovers,
  L2").
- Each pilot run's rate is that level's complete turnovers per `mixgraph`
  operation, from the `measure_start` to the `drain_start` stamp. The cell
  needs $n_{\text{turn}}$ divided by the slowest run's rate, and so the
  shortest rung whose `mixgraph` reaches it.
- **The Gate N2 run length of a workload is the longest of its three
  cells' rungs**, so C-6 compares $J_\beta$ across $T$ over the same
  operations. $\bar q$ (D-14 §2) and the prices (D-15 §3) are measured at
  it.
- If no rung is long enough for some cell, stop and report; the owner
  decides. This includes a pilot run in which the governing level completes
  no turnover.
- *Expected, a rough estimate by the implementer's verifier and not a
  criterion:*
  - For `Assoc`, the governing levels are L6 at T=2, L3 at T=6 and L2 at
    T=10. The workload's rung is then likely (58, 5): 55.1M `mixgraph`
    operations, about 16 minutes per run. It would be (145, 2) if L7
    joins at T=2, or if deep-level inflow is below about 71% of the put
    bytes.
  - For the power law, with its 5% puts, the rung is likely (145, 2), or
    (290, 1) if a pilot run completes only one turnover of its governing
    level.
  - The last level's fill at T=2 is estimated at 0.44–0.47, near the 0.5
    threshold of item 3. `19` reports each run's $(L, B_L/C_L)$.

**7. Known risks, which only the pilot data can settle.**
- `inflow_ratio`'s 10th and 90th percentiles may narrow with depth, since
  a variable averaged over a level's own interval tightens with depth
  (G.4). If so, a margin of 0.20 on them could refuse deeper levels whose
  dynamics match.
- $\omega_{\max}$ applies to the reference too. With one compaction slot,
  L2 at T=2 could exceed it, and then that cell has no pool.
- These values are not changed after the pilots have run. A new dated
  entry would record any change, and name it as made after seeing data.

**8. Deferred.** PROP-1b's one-step model and $\delta_{\text{kern}}$ judge
learner transitions, so nothing before Gate N4 uses them. They are a later
entry, before Gate N4.

**Implementation.** `19_admission_test.py` reads the config file. It takes
the margins of the runs' $T$, applies the $n_{\min}$ rule, derives the
candidates, and reports each cell's rung. It refuses runs whose load
differs from the rungs', runs that `03` did not complete or marked
unsettled, and fingerprints without the SST size that the fill margins'
floor needs. `03` enforces item 2's load. Tests: `test_admission.py`
and `test_run_experiments.py`.

**Falsification.** This entry fails as a record if any value in it, or in
`config/admission_test.json`, is changed after the first Gate N1 pilot run
has started.

### D-19, 2026-10-02 — Gate N1's outcome: no pool in any cell; the run length comes from L2, and undecided levels stay unpooled

**Recorded after the Gate N1 pilot runs were scored (the night of
2026-10-01), so it is not a prediction about those runs.** It records what
they showed, and it changes two rules for the runs that follow: D-16 §5's
Gate N2 decision of undecided levels, and D-16 §6's run-length rule. No value
of D-16 and nothing in `config/admission_test.json` changes. D-17 (the Gate N2
screen) and D-18 (the action bounds) are reserved for entries still in draft.

**1. What the pilots showed.**
- All 18 pilot `native` arms completed: three per workload and $T$, at the
  rung (29M, 10%), so 26.1M `mixgraph` operations each. They ran on the
  binary of root `d43f79e` (fork `4a31e8a71`), after the preflight passed.
- The $n_{\min}$ rule (D-16 §5) found no sufficient value in any cell where
  it ran. Two pseudo-levels drawn from L2's own turnovers passed the
  equivalence test in 2–10% of trials on `Assoc` at T=2 ($n$ = 20 to 80),
  and in 0% on the power-law workload. The rule needs at least 80%. At T=10,
  L2 was the only candidate, so the rule did not run there. Where L2 pooled
  fewer than 40 turnovers, no grid value could be tried. So every candidate
  besides L2 is undecided, and no cell has a pool.
- Net turnovers per pilot run, the mean of three runs, as stage 19 reported
  them on the node (`$NVME/n1-<workload>/admission_T<T>.json`, not in the
  repository):

  | Cell | L2 | L3 | L4 | L5 | L6 |
  | --- | ---: | ---: | ---: | ---: | ---: |
  | `Assoc`, T=2 | 79 | 25 | 6 | 1 | 0 |
  | power law, T=2 | 27 | 10 | 3.7 | 1 | 0 |
  | `Assoc`, T=6 | 16 | 0 | | | |
  | power law, T=6 | 6 | 0 | | | |
  | `Assoc`, T=10 | 9 | | | | |
  | power law, T=10 | 3 | | | | |

- **Finding: deep levels barely turn over under skewed updates.** Most writes
  overwrite keys that already sit high in the tree, so little net inflow
  ($\lambda_i$, as PATHWAYS defines it and as stage 19 computes it) reaches
  the deep levels. This is a property of the workload, not an instrument
  fault.
- Under D-16 §6, the deepest undecided candidate set the run length: L6 at
  T=2 and L3 at T=6. Each completed no turnover in at least one run, so no
  rung was long enough, and the chain stopped before the q̄ arms, as D-16 §6
  requires.

**2. Decision.** Decided by the owner on 2026-10-02, as D-16 §6 requires,
on the implementer's proposal.
- **(a) The run length comes from L2 when a cell has no pool.** D-16 §6 is
  amended: the governing level is the deepest pooled level, or L2 when the
  cell has no pool. An undecided candidate is no longer sized as if pooled.
  This is PATHWAYS Gate N1's own rule for a cell with no pool. A cell still
  stops if L2 completes no turnover in some pilot run.
- **(b) An undecided level stays unpooled and is not decided later.** D-16
  §5's decision of undecided levels on Gate N2's native arms is withdrawn,
  together with its extra native repeats. Gate N2's native arms run D-13 §5's
  five. A level can join a pool only through a new dated entry, and D-16's
  margins are not loosened to make a pool appear.
- **(c) Consequences.** At this tree size, Programme 1 has no pool in any
  cell. Every interior level keeps its own model (PATHWAYS G §4 scope
  decision). Global acceptance item 4 ("propagation contributes", PROP-2 to
  PROP-4) is not claimed. PROP-1 is recorded as: every candidate besides L2
  undecided, and no pool, in every cell. The verdict is *undecided*, not *refuted*: at this
  sample size, the test could not even resolve L2 against itself.

**3. Expected rungs (an estimate, not a criterion).** From the means above, 10
L2 turnovers need, in `mixgraph` operations:
- `Assoc`: 3.3M at T=2, 16.3M at T=6 and 29.0M at T=10, so the workload's
  rung is (58M, 5%).
- Power law: 9.7M, 43.5M and 87.0M, so its rung is (145M, 2%).

Stage 19 uses each cell's slowest run, so the rung it reports may be longer.
q̄ (D-14 §2) and the prices (D-15 §3) are measured at that rung, on the binary
that runs Gate N2.

**4. Reuse of the pilots, and the binary.** The q̄ arms, the prices and Gate
N2 run on the step-8 binary (root `2cb9b7e` or later, fork `31e087505`). The
pilots' turnover counts are reused on the ground that step 8 adds the
controller host without changing native compaction. The next preflight's
ACT-4 and ARCH-5 checks test that ground. `24` with `RESUME=1` skips the
completed pilots and re-runs 19 on them.

**5. Implementation.**
- `19_admission_test.py`: `run_length` takes the cell's pool.
- `gate_n1_reports.py`: no longer computes D-16 §5's repeats.
- `gate_n2_plan.py`: no longer raises the native repeats, and no longer
  refuses a cell without $n_{\min}$.
- `25_gate_n2_chain.sh`: no longer passes `--n1`, `--default-base` or
  `--default-k0`, so `N2_RUN_LENGTH_<workload>` works without Gate N1's
  reports.
- `27_screen_design.py`: the D-16 §5 caveat is removed.
- Tests:
  - `test_admission.py`: `RunLengthTest`, and the D-16 end-to-end case;
  - `test_gate_n2_chain.py`: `test_undecided_levels_change_nothing`, which
    also runs the override without Gate N1's reports;
  - `test_screen_design.py`: the caveat check.

**Falsification.** This entry fails as a record if its rules are changed
after the first q̄ arm at its rung has started.

### D-20, 2026-10-02 — the read prices are measured with every table open, and table reopens are priced apart

**Recorded after the first prices were measured, and after a diagnostic run
on them, so it is decided after seeing price data.** Stage 18 wrote the
first `prices.json` on 2026-10-02 at 00:19 UTC, at the end of Gate N1's
chain (run 3, `db_bench` `a8e9329d`). No arm has been priced with it and no
$\Theta_s$ run has started, so no Gate N2 outcome has been seen. This entry
names D-15 §3 and supersedes only what is stated below. The owner decided
it on 2026-10-02, on the node operator's proposal.

**1. What the first prices showed.**
- D-15 §3(c)'s fit gave, per unit (medians of five repeats):
  - 6.38 µs per filter probe;
  - 0.59 µs per block read;
  - 5.99 µs per run seek.
  
  Pooled over the five repeats, the same fit has an intercept of −5.7 µs.
  D-15 reads that intercept as the overhead every Get pays, which cannot be
  negative.
- **Most of that time was table reopens.**
  - RocksDB keeps at most `open_files` = 1,000 tables open, and the trees
    hold about 5,800 SSTs. A probe or seek into a closed table reopens it:
    it opens the file, then reads and parses the footer, index and filter.
    db_bench's default `cache_index_and_filter_blocks` is false, so a reopen
    reloads both.
  - RocksDB's own timer of that read (`rocksdb.table.open.io.micros`) gave
    8.2–8.4 µs per open in all 45 read runs. It accounted for 62–75% of the
    `readmissing` and `readrandom` runs' time.
- **The price runs and the experiments reopen at different rates.**
  - Reopens per filter probe (`rocksdb.no.file.opens`): 0.33–0.49 in the
    price runs, whose keys are uniform.
  - In the $\bar q$ arms' `mixgraph` phase, 0.056 (`Assoc`) and 0.13
    (power law), less the opens of new files (item 2d). Skewed keys keep hot
    tables open.
  - D-15 §3(c) assumed that "the experiments run with the same table
    cache, so the prices carry the same mix". That assumption does not hold.
- **A diagnostic A/B run** (2026-10-02, 13:12–13:18 UTC; the owner approved
  it).
  - Setup: one T=2 tree loaded and settled as stage 18 loads it. Stage 18's
    own `readmissing` and `readrandom` commands, each run with `open_files`
    1,000 and with −1 (every table kept open). Five repeats, with the order
    alternated by repeat.
  - The extra time per Get, divided by the extra reopens per Get, was:
    - 10.62 µs per reopen for `readmissing` (95% t interval [10.58, 10.66]);
    - 10.24 µs for `readrandom` ([10.16, 10.33]).
  - With every table open, a Get took 1.18 and 3.42 µs, against 26.0 and
    19.7 µs. Reopens were 95% of a missing-key Get's time.
- **The effect on the objective.** On the $\bar q$ arms, D-15's prices put
  read cost at 8.05 times write cost on `Assoc` (93% of it filter probes)
  and at 33.5 times on the power law (99%). With reopens priced at the
  measured rate and probes and blocks at their all-open times, the ratios
  are about 1.9 and 9.
- Evidence: the operator report
  `~/node_ops/reports/2026-10-02-0511-stage24-complete.md` and its scripts
  in `~/node_ops/diag/`, on the node. Run 3's `prices.json` and its 51
  outputs are kept in `~/node_ops/archive/run3-prices-d15/`.

**2. Decision.**
- **(a) Reopens are their own priced unit**, $c_{open}$ per table reopen.
  PATHWAYS D §1's cost rate gains $c_{open}\,\dot o$, charged to reads
  ($\beta_R$). Each run pays for the reopens it made, $O$.
- **(b) Stage 18 runs every read benchmark in two arms**, on each tree and
  in each repeat:
  - "capped", at the experiments' `open_files`;
  - "all_open", at `open_files` −1.
  
  The arms' order alternates by repeat. $c_f$, $c_{blk}$ and $c_{sk}$ come
  from the all-open arm, by D-15 §3(c)'s fits, unchanged otherwise.
  $c_{open}$ per repeat is the capped arm's extra seconds over the all-open
  arm's, summed over the six Get points (three trees, `readmissing` and
  `readrandom`), divided by its extra reopens, summed the same way. Each
  price is the median of five repeats, with the minimum and maximum
  reported. Seeks' reopens are priced at the Gets' $c_{open}$.
- **(c) Refusals added to D-15 §3(d).** Stage 18 writes no prices when:
  - an all-open Get run reopens more than 10% as many tables per Get as
    its capped pair (the arm did not keep the tables open);
  - a capped Get run reopens fewer than 0.5 more tables per Get than its
    all-open pair ($c_{open}$ not identified; the A/B run measured
    1.6–2.3);
  - a pair's filter probes or block reads per Get differ by more than 1%
    (or 0.005 per Get, whichever is larger), so the arms did not read
    alike;
  - any read run's command names `open_files` other than once, or with a
    value other than −1 in every all-open run and one positive value in
    every capped run.
- **(d) The evaluator's count.** $O$ = `rocksdb.no.file.opens` differenced
  from $n_w$ to the end of the drain, less the SST files created in that
  window (non-empty `table_file_creation` events).
  - Every new file is opened once, through the table cache, by the job that
    wrote it: BuildTable and `CompactionJob::VerifyOutputFiles`, "verify
    that the table is usable". That time is already in $c_w$'s job seconds.
  - Compaction inputs that background jobs reopen stay counted, so $O$ is
    an upper bound on the reads' reopens.
  - On the $\bar q$ arms, new-file opens were 1.9% (`Assoc`) and 0.17%
    (power law) of all opens. During the load, which has no reads, opens
    were 1.5 per new file. So the input reopens left in are about 1% and
    under 0.1%.
  - `04` refuses an arm whose stamps lack the counter, or that created more
    files than it opened.
- **(e) `prices.json` becomes schema 3.** `04`, `27`, `gate_n2_plan` and
  `plugin_config` refuse a schema-2 file, whose $c_f$ carried the price
  runs' reopens.
- **(f) Attribution (PATHWAYS D §4).** Reopens are charged to no level.
  They go to a shared reopen bucket that no agent is rewarded on, beside
  the hit-read bucket, and Proposition D.16 holds with both buckets. A
  per-level count needs a counter keyed by the opened table's level, a
  fork change left for a later entry.
- **(g) The controller is left unchanged, as an open item.**
  - The plugin keeps $c_w$, $c_f$, $c_{blk}$ and $c_{sk}$, and $c_f$ is now
    a probe's cost on an open table. So the controller's own cost model
    (the prior, H §7; the Gate N3 rules) does not see reopens.
  - Whether it should, and how, is decided by a dated entry before Gate
    N3's runs. The options are per-level reopen counters in the fork, or
    $c_f$ plus $c_{open}$ times a measured reopen rate.

**3. Assumptions, stated.**
- **A reopen costs the same whatever the benchmark, tree or level.** In the
  A/B run, `readmissing` and `readrandom` differ by 0.4 µs (4%), unexplained.
- **The skipped cache lookup is folded into $c_{open}$.** In the all-open
  arm, every probe also skips a table-cache lookup, because each reader is
  pinned to its file's metadata (`db/version_builder.cc`).
  - The A/B run could not resolve this: a two-benchmark split gave a
    negative lookup cost.
  - So $c_{open}$ carries the lookups. Taking a lookup as at most 0.1 µs,
    that is at most about 2% of $c_{open}$.
- **The all-open arm's opens at DB open are counted.** That arm opens every
  table once when the DB opens, outside the timed benchmark (0.006 per
  operation at 1M reads). Its tickers count those opens, which shortens the
  extra reopens by about 0.3%.

**4. Unchanged.**
- $c_w$ (D-15 §3b).
- The trees, the reads per process, the five repeats and the medians.
- `THREADS` = 1, and the money conversion at the price per core-second
  (D-15 §3a).
- $\bar q$ (D-14 §2), and everything else in D-13 to D-19.
- Run 3's prices are superseded and price no arm.

**5. Implementation.**
- `18_calibrate_prices.sh`: the two arms, and a refusal when the open-file
  limit is under 16,384.
- `18_calibrate_prices.py`: `marginal_times`, `reopen_time`,
  `arm_open_files` and schema 3.
- `research_objective.py`: `c_open` in `DEVICE_PRICES`, `PRICES_SCHEMA` =
  3, and `priced_costs` takes the reopens.
- `04_generate_graphs.py`: the columns `table_reopens` and
  `sst_files_created`, and their pricing.
- `27_screen_design.py`: `table_reopens` in `COUNTS`.
- `plugin_config.py`: `PLUGIN_PRICES`, which leaves out `c_open`.
- PATHWAYS: D §1 (metric $O$, price $c_{open}$, cost rate), D §2, D §4,
  Proposition D.16, OBJ-1, OBJ-2, H §3 and Gate N0 item 7.
- Tests:
  - `test_prices.py`: the two arms, and every new refusal;
  - `test_evaluator.py`: the count, and its refusals;
  - the chain fake `db_bench`, which models reopens at 10 µs;
  - the price fixtures of `test_evaluator`, `test_plugin_config`,
    `test_screen_design` and `test_gate_n2_chain`, moved to schema 3.

**6. Node order.**
1. Archive run 3's prices (done, `~/node_ops/archive/run3-prices-d15/`).
2. Stage 18 alone on the $\bar q$ arms' summaries. That is about 25
   minutes: the all-open runs take 1–4 s each.
3. The preflight (13), since the marker hashes the changed scripts. Run it
   after 18, because the preflight's evaluator smoke prices its arm with
   `PRICES_FILE` and `04` refuses a schema-2 file.
4. Then 25.

$\bar q$ is recorded by `26` from the $\bar q$ arms' existing
`summary.csv`. Re-running `04` on those folders after this entry's contract
amendment would mark them "run recorded another contract", and `26` would
refuse them. So $\bar q$ is recorded before `04` is re-run there.

**Falsification.** This entry fails as a record if any of it is changed
after the first prices it governs have been measured.

### D-21, 2026-10-02 — the reads' table reopens are counted and timed per level by the fork, the controller prices them, and every run checks c_open

**Recorded after D-20's prices (stage 18, 2026-10-02 17:28 UTC) and the
$\bar q$ record (`96e5c74`), before any $\Theta_s$ run and before any arm is
priced under it.** It resolves D-20 §2g and amends D-20 §2d and §2f. The
owner made three choices on 2026-10-02, on the node operator's proposal:
- option (b) of D-20 §2g, per-level reopen counters in the fork;
- every run checks c_open against its own measured time per reopen, with a
  10% tolerance;
- c_w is measured again from $\bar q$ arms run on the new binary.

The rules in §2 that turn those choices into code are the operator's
proposal. The owner confirms them before the runs in §6 start.

**Confirmed by the owner, 2026-10-02 19:53 UTC, before any run it governs,
with no changes.** All five of §2's operator-proposed rules stand as written:
- $O$ is the fork's count;
- `min_reopens` is 1,000;
- the timer runs from the open to the cache insert;
- L0's reopens move with the slot-blocking share;
- the controller's state gains $e^o_i$ and $\tilde R^o$.

**1. Why.**
- **The controller saw no reopens (D-20 §2g).** On `Assoc` it priced a probe
  at about $c_f + \varepsilon c_{blk} \approx 0.21$ µs. Counting the measured
  0.056 reopens per probe at $c_{open} = 10.44$ µs, a probe costs about
  0.80 µs, about 3.8 times more. Proposition D.11's $K_0^\star \propto
  \sqrt{\text{write price}/\text{read price}}$ was then about 1.9 times too
  high. The risk is a false negative at Gates N3 and N5: a controller blind
  to reopens loses to a static configuration chosen on the full cost.
- **D-20 §3's first assumption was unchecked.** "A reopen costs the same
  whatever the benchmark, tree or level." Stage 18 measures $c_{open}$ on its
  own trees, with uniform keys and no writes. In an experiment, compactions
  run beside the reads, and a workload with other files or another page-cache
  state could reopen at another cost. The owner asked that this be checked,
  not assumed.
- **D-20's count is an upper bound.** $O$ = `rocksdb.no.file.opens` less the
  SSTs created keeps the compaction inputs that background jobs reopen, about
  1% of the opens on `Assoc` and under 0.1% on the power law. $c_w$'s job
  seconds already pay for them.

**2. Decision.**
- **(a) The fork counts and times the reads' reopens per level.**
  - Where: `TableCache::FindTable`, when it opens a table it found closed
    for a Get (`kGetReopen`) or a user iterator (`kIterReopen`).
  - Which level: the one the read passes in. That is `FilePicker`'s hit
    level for a Get and the iterator's level for a seek, the same levels as
    the other per-level counters (OBJ-4).
  - The time (`kReopenNanos`, wall clock) runs from the open to the cache
    insert, which closes the table the insert evicts.
  - Tickers: `rocksdb.read.table.reopen` (both kinds) and
    `rocksdb.read.table.reopen.nanos`. The per-level rows sum to them, and
    the host log, now schema 2, carries both.
  - Not counted: opens by flushes, compactions, ingestion and verification
    (D-20 §2d: a new file's verifying open is a write cost). MultiGet is not
    counted either; the workloads issue none.
- **(b) $J_\beta$'s reopen count $O$ becomes the fork's count.** It is
  `rocksdb.read.table.reopen`, differenced from $n_w$ to the end of the
  drain. This is the reads' reopens exactly, by the same instrument as the
  controller's per-level counts (CLAUDE.md: a priced term and the
  controller's view of it come from one instrument over one phase). D-20
  §2d's count stays as a reported cross-check, `table_opens_less_created`.
  An arm whose host log is schema 1 (a binary before D-21) is not priced.
- **(c) Every run checks $c_{open}$.**
  - The reference: stage 18 records `reopen_timer`. Per repeat, it is the
    capped arm's reopen nanoseconds over its reopens, both summed over the
    six Get points; the reference is the median of the five repeats, with
    min and max. `prices.json` becomes schema 4.
  - The check: `04` computes each run's own seconds per reopen by the same
    tickers, from $n_w$ to the end of the drain, and divides it by the
    reference.
    - Outside $1 \pm 0.10$, $c_{open}$ does not hold for that run. Its
      `objective_status` is "c_open does not hold", it is not priced, and
      its workload's $c_{open}$ is measured on that workload by a dated
      entry before any of its arms is scored.
    - Under 1,000 reopens, the check does not apply and the reopens are
      priced unchecked. Their share of the read cost is then negligible, and
      the mean of fewer timings is too noisy for a 10% test.
  - Both values are in the contract, `prices.reopen_time_check` (amended in
    place, with its reason).
  - Stage 18 also reports each $\bar q$ arm's ratio (`qbar_reopen_checks`).
    It does not refuse: the check refuses at pricing.
- **(d) Attribution (amends D-20 §2f; PATHWAYS D §4).**
  - Each level is charged the reopens its Gets and iterators made, both
    kinds at $c_{open}$, at the level where they happen.
  - Under slot blocking, the share $(k_0 - K_0)^+/k_0$ of L0's reopens moves
    with its probes and seeks.
  - The reopen bucket keeps only reopens of no known level, and on the Get
    and iterator paths there are none. Proposition D.16 holds.
- **(e) The controller (resolves D-20 §2g).**
  - `c_open` is a required plugin key, $> 0$, with no default.
    `plugin_config.py` passes stage 18's value. Only the preflight's smoke
    runs may fill it, with a placeholder, and `03` refuses placeholders
    anywhere else.
  - State (G.2, H §2): each level gets $e^o_i$, its reads' reopens per
    operation. Every agent gets $\tilde R^o = c_{open}/(c_w\bar\lambda_1)$
    per operation, so $\tilde R^o e^o_i$ is level $i$'s reopen cost in the
    units of $\tilde R^f e^f_i$. L0's state also carries its own $e^o_0$.
  - The prior (H §7) and the Gate N3 rules price an L0 file's reads at
    $c_f + \varepsilon c_{blk} + \rho_g c_{open}$ per Get and
    $c_{sk} + \rho_s c_{open}$ per scan. Here $\rho_g$ and $\rho_s$ are L0's
    measured reopens per probe and per seek since the controller started,
    and 0 until measured. The slot-blocking charge includes L0's reopens.
  - The decision and transition logs become schema 3, with `reopens`,
    `slot_out_reopens` and `slot_in_reopens`.
- **(f) $c_w$ on the new binary (the owner's choice).**
  - The ten $\bar q$ arms run again on the new binary, into new folders
    (`24_gate_n1_chain.sh` with `QBAR_ONLY=1 QBAR_TAG=d21`), and stage 18
    takes $c_w$ from them as D-15 §3b says.
  - $\bar q$ stays as recorded (D-14 §2; it is never overwritten). The new
    arms' mean is reported beside it.
  - These arms also measure each workload's own time per reopen, so the
    check in (c) is first tested on both workloads before Gate N2.

**3. Assumptions, stated.**
- **The check holds the ratio fixed.** The timer covers the open, the
  footer, index and filter reads, and the insert. $c_{open}$ is the Get's
  extra time per reopen, measured by D-20's A/B. The check compares timer
  with timer, so it assumes $c_{open}$ over the timer's time is the same in
  every workload. A workload whose reopens cost more inside the open
  (larger index or filter blocks, a colder page cache, contention for the
  disk) shows. A change only outside the open does not.
- **$\rho$ does not depend on the knobs**, in the prior and the rules. This
  is D §1's approximation: more or larger levels change which tables stay
  open.
- **The timer is cheap.** It is two clock reads, two relaxed atomic adds
  and two ticker increments per reopen, against a reopen of about 10 µs. ACT-4 and ARCH-5 on the
  new binary check parity.

**4. Unchanged.**
- $c_f$, $c_{blk}$, $c_{sk}$ and $c_{open}$ are measured as D-20 §2b says.
- $\bar q$ and its record (D-14 §2).
- The run length (D-19), and everything else in D-13 to D-20.
- D-20's prices (`d20-prices`, `a8e9329d`) price no arm of the new binary.

**5. Implementation.**
- Fork (`lib/rocksdb`):
  - `db/rl_read_counters.h`: the three kinds, and `Add` with an amount.
  - `db/table_cache.{h,cc}`: `FindTable`'s `rl_reopen`; `Get` passes
    `kGetReopen`, and `NewIterator` passes `kIterReopen` for
    `kUserIterator`.
  - `include/rocksdb/statistics.h` and `monitoring/statistics.cc`: the two
    tickers.
  - `include/rocksdb/rl_controller_host.h`: `RLLevelReadCounts`' three
    fields.
  - `db/rl_controller_host.cc`: `ReadCounters`, and host log schema 2.
  - Tests: `db/per_level_read_counters_test.cc` (Get reopens per probed
    level, iterator reopens per table opened, job opens not counted, and
    no reopens with every table open) and `db/rl_controller_host_test.cc`
    (schema 2, and the stamp's reopen sums equal to the tickers).
- Controller: `config` (`c_open` required), `state` (`e_o`, `R_o`,
  `L0ReadPrices`), `attribution`, `prior`, `rules`, `log` (schema 3) and
  `plugin`, each with its tests; `log_golden.jsonl` and `smoke_config.json`.
- Pipeline:
  - `research_objective.py`: `PRICES_SCHEMA` = 4, `reopen_reference` and
    `reopen_check`.
  - `04_generate_graphs.py`: `table_reopens` from the fork, plus
    `reopen_seconds`, `reopen_time_ratio`, `reopen_check` and
    `table_opens_less_created`.
  - `18_calibrate_prices.py`: `reopen_timer` and `qbar_reopen_checks`.
  - `host_log.py`: schema-2 rows.
  - `plugin_config.py`: `c_open`.
  - `24_gate_n1_chain.sh`: `QBAR_ONLY` and `QBAR_TAG`.
  - The chain's fake `db_bench` and the price fixtures, moved to schema 4.

**6. Node order.**
1. Move `build-dbbench/prices.json` aside. D-20's copy is in
   `~/node_ops/archive/d20-prices/`.
2. `NVME=<results disk> QBAR_ONLY=1 QBAR_TAG=d21
   scripts/dbbench_pipeline/24_gate_n1_chain.sh`. It runs:
   - the preflight (13): it builds the new binary and plugin, runs tiers 1
     and 2, ACT-1, ACT-4 and ARCH-5 at 10 pairs and the smoke runs, and
     writes the marker;
   - the ten $\bar q$ arms, about 3 hours;
   - stage 18, about 25 minutes, which prints every $\bar q$ arm's reopen
     check.
3. Verdicts: parity (ACT-4, ARCH-5) on the new binary, and the reopen check
   on both workloads. A workload whose check fails gets a dated entry before
   Gate N2.
4. Gate N2 (25), after D-17 and D-18.

**Falsification.** This entry fails as a record if any of it is changed
after the first run it governs has started.

### D-22, 2026-10-03 — the read prices are measured on archived trees, at six size ratios in three builds, and must reproduce within 3% in a second session

**Recorded after the d21id chain's prices (stage 18, 2026-10-03 11:42 UTC)
and before any official run is priced under the prices it governs.** It amends D-15 §3
(the trees, the repeats and the medians of $c_f$, $c_{blk}$ and $c_{sk}$) and
leaves D-20's $c_{open}$ method and D-21's reopen timer unchanged. The owner
made three choices on 2026-10-03, on the node operator's proposal:
- 18 archived trees: $T$ = 2, 3, 4, 6, 8 and 10, three builds of each; the
  capped arm only on build 1's trees at $T$ = 2, 6 and 10;
- a second session must reproduce every read price within **3%**;
- the trees are archived as `.tgz` files, kept on Chameleon storage for at
  most a week, with the lasting copy on the owner's own machine, from which
  any later node receives them.

On the same day the owner also chose:
- (f): a reboot of the node between the two sessions;
- (g): one shared set of prices for every node that reproduces them within
  3%;
- (h): the sensitivity reports.

The owner requires this before any price is final: "If this varies between
sessions, then this is not a publishable result." The rules in §2 that turn
these choices into code are the operator's proposal. The owner confirms them
before §6's runs start.

**Data seen before this entry:**
- `build-dbbench/prices.d20.json` (2026-10-02 17:28 UTC) and
  `prices.d21.json` (23:44 UTC), every read process's seconds and tickers
  among them;
- the library A/B report, `~/node_ops/reports/2026-10-03-0711-ab-library.md`;
- the operator's plots of the two sessions' read points,
  `~/node_ops/reports/stage18-points/` (CSV, `c_f.png`, `c_sk.png`);
- the d21id chain's stage 18 prices, `build-dbbench/prices.json`
  (2026-10-03 11:42 UTC, schema 4), which are provisional under this entry.

**1. Why.**
- **Two prices moved between sessions; three did not.** From the D-20
  session to the D-21 session, $c_f$ went from 192.7 to 211.5 ns (+9.7%)
  and $c_{sk}$ from 1657 to 1802 ns (+8.7%). $c_{blk}$ moved +0.5%,
  $c_{open}$ −0.3% and $c_w$ −0.3%. A third session (d21id, 2026-10-03
  11:42 UTC) gave $c_f$ 195.3 ns and $c_{sk}$ 1804 ns. $c_{blk}$, $c_{open}$
  and $c_w$ again moved 1% or less.
- **The library did not cause it.** The same-session A/B found the new
  library at most 1.7% slower on filter-rejected Gets at $T$ = 10, unchanged
  at $T$ = 2, and faster on seeks.
- **The reported spread hid the movement.** Each session's five repeats ran
  on one load of each tree, so their range shows only timing noise on that
  load. The two sessions' ranges do not overlap: $c_f$ 187–199 ns, then
  207–220 ns; $c_{sk}$ 1653–1675 ns, then 1747–1819 ns.
- **The trees differ at every load.** The data is fixed (`--seed=1`); the
  layout is not, because flushes and compactions run on background threads.
  At $T$ = 2, filter probes per missing Get were 4.52, 4.73 and 4.58 in
  three loads.
- **One tree decides the slopes.** Filter probes per missing Get were
  2.67–2.75 at $T$ = 6, 2.99 at $T$ = 10 and 4.52–4.73 at $T$ = 2. The two
  larger ratios sit together, so each slope is in effect $T$ = 2 against
  the other two.
- **Some of the movement is not layout.** At $T$ = 2 the run seeks per seek
  were the same in both sessions (7.563 and 7.559), but a seek took 4.3%
  longer (13.08 to 13.65 µs). That is either the machine or a property of
  the tree the counts miss. Rebuilt trees cannot tell the two apart; the
  same bytes measured twice can.
- **The two Get benchmarks disagree on $c_f$.** Fitted alone, per repeat
  and then the median, `readmissing` gives 214, 219 and 216 ns per probe in
  the three sessions, and `readrandom` 194, 264 and 221 ns. D-15 §3(c) fits
  one shared slope, so `readrandom`'s changes moved $c_f$.

**2. Decision.**
- **(a) The trees.** 18 trees: $T$ ∈ {2, 3, 4, 6, 8, 10}, three builds of
  each. Build $b$ of every $T$ forms set $b$ (b = 1, 2, 3). Each is built as
  D-15 §3(b) builds today: `filluniquerandom` of the Programme 1 load
  (2.9M keys, `--seed=1`), the pipeline's default options ($K_0$ = 4, base
  16 MiB), `open_files` as the experiments, then `settle`, then
  `levelstats`. A build whose `settle` does not end `ok=1` is discarded and
  rebuilt, and that is reported. The builds run one at a time, pinned as
  every run is.
- **(a′) Age-based compaction is off in every price run.** The build and
  every session pass `--ttl_seconds=0 --periodic_compaction_seconds=0`. By
  default this fork compacts any file older than 30 days
  (`db/column_family.cc:423-428`, `ttl` for block-based tables). An archived
  tree opened more than 30 days after its build would otherwise compact
  itself on open. The experiments never run for 30 days, so this changes
  nothing they do.
- **(b) The archive and its identity.**
  - Each tree, as the build process leaves it, becomes one `.tgz` (names
    sorted, numeric owners, `gzip -n`). The files' own time stamps are kept,
    since RocksDB may read a file's modification time as its age.
  - `MANIFEST.sha256` lists the sha256 of every file of every tree, and of
    every `.tgz`. **The tree-set identity is the sha256 of that manifest.**
  - A dated build record, a new entry, gives the identity, each tree's files
    and bytes per level, the building binary's identity, and each build's
    `settle` outcome.
  - The archive is kept on Chameleon storage for at most a week. The lasting
    copy is on the owner's own machine, checked against the manifest after
    the transfer. Any later node receives it from there and checks it again.
- **(c) One measurement session.**
  - The sets run in order 1, 2, 3. For each set, its six trees are unpacked
    from the archive into fresh folders under `DB_ROOT`, every file is
    checked against the manifest, and each tree gets one unscored
    `readrandom` of 1,000,000 operations to warm the page cache, as today.
  - **Three rounds per set.** A round visits all six trees once and runs
    `readmissing`, `readrandom` and `seekrandom` (`seek_nexts` 0) on each,
    1,000,000 operations each, in its own process, all-open
    (`open_files` −1). The visiting order rotates. Round 1 visits
    2, 3, 4, 6, 8, 10; round 2 visits 4, 6, 8, 10, 2, 3; round 3 visits
    8, 10, 2, 3, 4, 6. Slow drift within a set then falls on every tree
    alike.
  - **The $c_{open}$ block** runs after set 1's rounds, on set 1's trees at
    $T$ = 2, 6 and 10. It is D-20 §2(b)'s procedure, unchanged: five
    repeats, every read benchmark in both arms, the arms' order alternating
    by repeat.
  - After the session, every SST file named in the manifest must be present
    and unchanged, and no other SST file may exist. Opening a database
    writes new MANIFEST, OPTIONS and LOG files. That is expected, and it is
    why every session starts from the archive, never from a copy that was
    already opened.
  - One session needs one binary. Its identity (6d80a52's sha256 over
    db_bench and its librocksdb) and the tree-set identity go into
    `prices.json`, with the kernel, the CPU governor, the free memory and
    the uptime at the start.
- **(d) Fits and prices.**
  - Each (set, round) gives one $t_f$ and one $t_{blk}$, from D-15 §3(c)'s
    fit over its 12 Get points (six trees, two benchmarks). It gives one
    $t_{sk}$, from the least-squares slope over its six `seekrandom`
    points. That is nine values of each per session.
  - A session's price is the median of its nine values. Its minimum and
    maximum, and each set's median, are reported.
  - $c_{open}$ and the reopen-timer reference come from the $c_{open}$ block,
    by D-20 §2(b) and D-21, each the median of its five repeats.
- **(e) Reported, not refused.** For every (set, round), `prices.json`
  keeps:
  - `readmissing`'s and `readrandom`'s slopes, each fitted alone;
  - each tree's residual from the fitted line.

  It also keeps D-15 §3's three-tree values on the $c_{open}$ block's
  all-open runs, the old method on the new trees, as a bridge to D-20 and
  D-21.

  **A seek's reopen, checked against a Get's.** D-20 §2(b) prices seeks'
  reopens at the Gets' $c_{open}$, an assumption no number tested. The
  $c_{open}$ block's `seekrandom` runs now test it. Their capped arm's
  extra seconds over the all-open arm's, divided by its extra opens, give a
  seek-based $c_{open}$, which is reported beside the Gets' value. On D-20's
  and D-21's runs it was 10.5–10.7 µs, against 10.41–10.44 µs from the
  Gets, at equal run seeks per seek in both arms.
- **(f) The reproducibility test.**
  - Two sessions, A and B, on the same binary and the same archive. Each
    unpacks every tree afresh.
  - The node is rebooted between them (`sudo reboot`), so session B
    starts from a fresh boot, as any new node does.
  - **It passes when each of $c_f$, $c_{blk}$, $c_{sk}$, $c_{open}$ and the
    reopen-timer reference satisfies $|B - A| \le 0.03 \cdot (A + B)/2$.**
    All five must pass.
  - **On a pass,** each final price is the median of both sessions' values
    (18 for $c_f$, $c_{blk}$ and $c_{sk}$; 10 for $c_{open}$ and the
    reference), with the minimum and maximum reported. The pooled range is
    the published uncertainty.
  - **On a fail,** no price is final. The operator reports which price
    failed, with the per-set and per-tree breakdown. **The test is not run
    again until a new dated entry says what changes;** it is never repeated
    until it passes.
- **(g) A new node or a new binary.**
  - It runs one session on the archived trees.
  - If all five prices are within 3% of the final prices by (f)'s rule, the
    final prices stand for that node or binary.
  - If any is not, no run on that node or binary is priced until a dated
    entry decides.
  - This is OBJ-2's re-measurement on a hardware change. It replaces
    rebuilding the trees.
- **(h) Sensitivity.** Each gate's verdict is also computed with $c_f$ and
  $c_{sk}$ both at the minimum of their pooled range, and both at the
  maximum. They are reported beside the main result, as OBJ-2 already
  reports every result at $c_s/2$ and $2c_s$. This is not a gate.
- **(i) Refusals added to D-15 §3(d) and D-20 §2(c).** Stage 18 writes no
  prices when:
  - an unpacked file's sha256 differs from the manifest;
  - after the session, an SST file named in the manifest is changed or
    missing, or an SST file outside it exists (a compaction ran during the
    reads);
  - the archive's tree-set identity differs from the build record's;
  - a set's six trees span less than D-15 §3(d)'s 1 probe per missing Get,
    or 1 run seek per seek.
- **(j) `prices.json` becomes schema 5.** It holds the tree-set identity,
  both sessions' values, (e)'s diagnostics and the test's outcome.
  - The official chains (`25` and later), `27` and `gate_n2_plan` refuse a
    file whose test did not pass.
  - `04` and `plugin_config` accept one only for a run marked diagnostic,
    and write "provisional prices" into every output.
  - Every price measured before this entry (D-20, D-21, d21id) is
    provisional.

**3. Assumptions, stated.**
- **Three builds sample the layout's variation.** That is a small sample:
  the pooled range is reported as observed, not as a confidence interval.
- **Reads do not change a tree.** Leveled RocksDB has no read-triggered
  compaction, the trees are settled, and (a′) turns off the age-based
  compactions. (c) checks this every session.
- **One set fits in memory.** Six trees of about 3.1 GB each, about 19 GB,
  sit inside the node's 60 GB, so a round's reads stay warm-cache, as the
  experiments' do.
- **A later binary reads the archived trees as they are.** RocksDB reads
  older table formats. The building binary is recorded; the measuring
  binary's identity goes into each session's `prices.json`.
- **The machine's state is recorded, not controlled,** beyond the pinning,
  the `performance` governor and SMT being off.

**4. Unchanged.**
- D-15 §3's model: one shared slope for both Get benchmarks, an intercept
  for each Get's fixed overhead, one thread, 1,000,000 operations per run,
  warm cache, `dio0`.
- D-20's $c_{open}$ method and refusals. D-21's timer and the 10% check
  every run makes.
- $c_w$, from the $\bar q$ arms' own jobs (D-14 §2, D-21). It moved 0.3%
  between the D-20 and D-21 sessions, and it is not part of (f)'s test.
- The money conversion, at the contract's price per core-second.

**5. Implementation (operator).**
- A new numbered stage, `29_build_price_trees.sh`, does (a) and (b). It
  builds, checks `settle`, records `levelstats`, writes the manifest and the
  `.tgz` files, and verifies them by unpacking.
- `18_calibrate_prices.sh` and `.py` do (c) to (e) and (i). The read
  benchmarks and the fits keep D-15's and D-20's code. The trees, the
  rounds and the medians are new.
- A comparison, `18_calibrate_prices.py compare A B`, does (f) and writes
  the final schema-5 `prices.json`.
- `research_objective.PRICES_SCHEMA` becomes 5, and the consumers in (j)
  are changed to match.
- The contract is edited in place, with this entry as the reason:
  - `prices.device_times` names the six ratios, the three builds, the
    archive and the test;
  - a new key `prices.reproducibility_tolerance` = 0.03 is added.
- Tier-1 tests cover:
  - the manifest check (a changed file, an extra SST);
  - (a′)'s two flags in every build and session command;
  - the rotation order;
  - the nine-value medians on fixtures;
  - the seek-based $c_{open}$ check of (e);
  - the 3% comparison at its edges;
  - the schema-5 refusals.
- New scripts get `.gitignore` `!` rules and `git add --chmod=+x`.

**6. Node order.**
1. The owner commits this entry.
2. The operator implements §5. Tier 1 passes, then the preflight, which
   writes a new marker (the code hash changes).
3. `29` builds and archives the trees: about 25 minutes. The dated build
   record follows.
4. Session A: about 45 minutes. A reboot follows.
5. Session B: about 45 minutes. Then `compare`, and the dated verdict
   record.
6. The owner copies the archive to their own machine and checks it against
   the manifest.

Steps 3 to 5 take about 2 hours of node time, plus the transfer. Today's
stage 18 takes 22 minutes per session.

**Predictions, made in advance.**
- $c_{blk}$, $c_{open}$ and the reopen-timer reference agree between A and
  B within 1%. On rebuilt trees they already moved 0.5% or less.
- $c_f$ and $c_{sk}$ pass at 3% if the D-20/D-21 movement came from the
  trees' layout, as the A/B report reads it. If $T$ = 2's longer seeks at
  equal seek counts came from the machine, $c_{sk}$ may fail, and the same
  bytes measured twice will show it.
- No prediction is made for the new prices' level. Six trees can give a
  different slope from three. None is made either for whether the two Get
  benchmarks' own slopes agree.

**Falsification.** This entry fails as a record if any part of it is
changed after session A starts. A change is a new dated entry.

### D-23, 2026-10-03 — cost model v2: $J_\beta$ prices every in-store cost of serving the operations, interference included; a new binary, calibrations before Gate N2, and per-run checks

**Recorded before any run it governs.** No Gate N2 run and no $\Theta_s$ arm
has run, no rule or learner arm beyond the preflight's smoke runs, and no
calibration of a price this entry adds. The binary that carries the new
instruments (PATHWAYS Gate N0 item 10) does not exist yet. The theory is
`docs/PATHWAYS.md` as amended on 2026-10-03 and committed with this entry;
its §0.7 says what changed. This entry records the decision, the measured
facts it rests on, and what PATHWAYS leaves to it (§0.6 item 13): the
instruments and the binary, the calibrations, the per-run checks, and the
owner decisions still pending. It amends D-13 to D-22 only where §8 says.

**Who decided what.**
- The owner asked for the amendment on 2026-10-03, after the interference
  critique: "We have to add interference, as it's a core RocksDB
  mechanism", "We need to model every cost of operation in the tree", and
  "Everything is based on the theoretical rigour."
- The design was fixed the same day, on that instruction, in four files,
  each overriding the one before:
  - the brief, `~/node_ops/drafts/2026-10-03-cost-model-v2-brief.md`;
  - the integration decisions,
    `~/node_ops/drafts/2026-10-03-cost-model-v2-integration.md` (I-1 to
    I-5, and its review-round decision on R-1);
  - the integrator's log and its post-integration decisions,
    `~/node_ops/drafts/amend-2026-10-03/INTEGRATION.md` (f);
  - the fixes for the referee review and its re-review,
    `~/node_ops/drafts/amend-2026-10-03/FIXES.md` (two rounds).
- Where those files and PATHWAYS differ, PATHWAYS' text is the decision.
- The rules in §3 to §6 that turn the design into instruments, calibrations
  and checks are the node operator's proposal. The owner confirms them
  before §3's node work starts. Every value §7 leaves open is fixed by a
  later dated entry before the first run it governs.

**Data seen before this entry:**
- Gate N1's pilots (D-19) and the d21id $\bar q$ arms, re-read for per-job
  time, $k_0$ during jobs, and scan counts;
- the `Assoc` reopen ablation and the Get-time check
  (`~/node_ops/reports/2026-10-03-1206-assoc-ablation.md`,
  `-1436-get-time-vs-prices.md`);
- the two contention runs (`-1514-contention-reads.md`,
  `-1632-contention-reads.md`) and the plot made from them,
  `interference_linearity.png`;
- the interference critique (`-1816-interference-theory-critique.md`), and
  the referee review and re-review of the amended PATHWAYS
  (`-2114-pathways-v2-review.md`, `-2234-pathways-v2-rereview.md`; PATHWAYS
  calls these two *the check reports*), which recount the figures from the
  run data;
- the d21id prices (`build-dbbench/prices.json`, schema 4), provisional
  under D-22.

No $\Theta_s$ outcome has been seen, and no price this entry adds has been
measured.

**1. The decision.**

*(a) $J_\beta$ becomes cost model v2*, as PATHWAYS D §1 and D §2 state it.
Its form stays $\beta_W\mathcal C_W + \beta_R\mathcal C_R + \beta_S\mathcal C_S$;
two of the three costs gain terms.
- $\mathcal C_W$:
  - each background job $\iota$ is priced, at its completion,
    $\tau^{job}_\iota = c^{\mathrm{kind}(\iota)}_{job} + c_{cr}(S_\iota + O_\iota) + c_wX_\iota$.
    The kinds are a flush ($F$), a merge sourced at L0 ($0$: L0→L1 and
    intra-L0), a merge sourced at a level $\ge 1$ ($d$, last-level
    self-compactions included) and a trivial move ($tm$);
  - a flush has $S_\iota = O_\iota = 0$. A trivial move's
    $\tau^{job}_\iota$ is $c^{tm}_{job}$ alone (until now it cost nothing);
    like every job, it also carries its interference charge;
  - each Put pays its memtable insert, $c_{put}$. The write-ahead log is off
    in every Programme 1 run (`wal1`), so $c_{wal}$ = 0;
  - the write part of interference;
  - so the job part of the write cost is
    $\mathcal C^{job}_W = \sum_\iota\tau^{job}_\iota$, and the whole write
    cost is $\mathcal C_W = \mathcal C^{job}_W + c_{put}\cdot(\text{Puts}) + \mathcal I^{\mathrm{wr}}$
    (D §1; Lemma D.18(iii) for the job part).
- $\mathcal C_R$:
  - filter probes, block reads, run seeks and reopens, as before, at their
    quiet prices $c^0_x$;
  - a scan's iteration, $\bar c_{st}(R_{nx} + R_{hd}) + c_{ib}R_{ib}$, with
    $c_{st}(r)$ nondecreasing in the heap size $r$; no result assumes it
    convex in $r$ unless it says so;
  - the memtable search, $c_{mt}$ per Get and per scan;
  - each Get's and scan's fixed in-store set-up, $c^0_{get}$ and $c^0_{sc}$;
  - the read part of interference.
- $\mathcal C_S$ is unchanged.
- New rows of D §1's metrics table: $W_r$, $\varpi^{\mathrm{kind}}$,
  $\mathcal C_W$, $R_{nx}$, $R_{hd}$, $R_{ib}$, the iteration steps by heap,
  and $\mathcal I$. $W$ is unchanged. The cost rate is D §1's amended $c(t)$.
- Every price but $c_s$ is a device time converted at $p_{\mathrm{dev}}$,
  D-15 §3(a)'s price per core-second; $c_s$ stays a storage price.

*(b) The principles* (PATHWAYS §0.7).
- **P-1, completeness**, in the scope PATHWAYS states.
  - Priced: every device-time cost incurred inside the store (the RocksDB
    library and its background threads) on behalf of the operation
    sequence. A cost that no policy changes is priced too, in a shared
    bucket, so that the level of $J_\beta$ is right.
  - Not priced: the benchmark client's own work; the controller's own work
    (its `SetOptions` calls and OPTIONS files, which carry no per-job price
    and no interference charge) and the trainer; and waiting (stall time,
    throughput, latency), which OBJ-6 reports.
- **P-2, the reference-rate principle.** $J_\beta$ is a function of the
  run's operation-indexed record $\mathfrak E$ (Lemma D.17). Wall time
  enters only converted at $\bar q$, through two anchors free of wall time:
  - the *operation anchor*, for space and for the byte part of
    interference;
  - the *priced-time anchor*, under which the busy part counts a job's
    priced device time $t^{job}_\iota$ as $\bar q\,t^{job}_\iota$
    operations.
- **P-3, victim-side pricing, cause-side attribution.** Interference is
  priced with the steps it slows: its read part in $\mathcal C_R$ (weight
  $\beta_R$), its write part, on Put inserts, in $\mathcal C_W$ (weight
  $\beta_W$). In the per-level rewards both parts go to the start level of
  the job that caused them; a flush's go to the shared write-path bucket.
  Foreground work (reads and Puts) slowing jobs is already inside the job
  prices (A11) and is not charged again.
- **P-4, measured counts, fixed prices.** $J_\beta$ multiplies counts
  measured in the run by prices fixed in advance (A9). Every price that a run
  can check with an instrument of the same source and phase is checked in
  every run, as $c_{open}$ is (D-21).
- **P-5, numbering.** Every existing number keeps its meaning. New results
  in Pathway D are D.17 to D.19. D.20 to D.23 are not used, so that no
  result is confused with a decision.

*(c) Interference* (D §1, A10, Lemma D.17).
- A10 is the physical model. A step of type $x$ served while the jobs
  $\mathcal J$ run costs
  $c^0_x\big(1 + \sum_{\iota\in\mathcal J}(\kappa^J_{x,\mathrm{kind}(\iota)} + \kappa^B_xv_\iota)\big)$:
  linear in its features and additive over jobs. It covers every foreground
  step: the read steps, the fixed parts and the Put insert.
- The charge is not the physical slowdown. Each job carries one charge, made
  at its completion:
  $I_\iota = \bar q\sum_x\bar\varrho^{\,x}_\iota\big(\kappa^B_xY_\iota + \kappa^J_{x,\mathrm{kind}(\iota)}t^{job}_\iota\big)$,
  with $\bar\varrho^{\,x}_\iota$ the quiet cost per operation of the type-$x$
  steps over the job's window $W_\iota$.
  - It contains no wall span, and it depends on how many operations the job
    overlapped only through which operations its window holds (integration
    decision I-2; Lemma D.17(v)). So a job run in a stall or in the drain is
    charged like any other.
  - The physical charge $I^{\text{phys}}_\iota$ is reported (OBJ-6) and
    tested (OBJ-8), never priced.
- **The window** (post-integration decision).
  - $W_\iota$ is the job's own operations if it served at least
    $n^{\mathrm{win}}$ of them. Otherwise it runs from the last counter
    snapshot at or before $n^e_\iota - n^{\mathrm{win}}$ to $n^e_\iota$.
  - $n^{\mathrm{win}}$ is a fixed operation count, set below the span of the
    merges that Proposition D.11's (d2) relies on, so that every such merge
    that serves at least $n^{\mathrm{win}}$ operations is charged over
    exactly its own operations. A merge in a stall or the drain, or a
    shorter one, reaches back before it began; OBJ-8(d) reports them.
    $n^{\mathrm{str}}$ is the snapshots' stride. §4(e) says how both are
    set.
- **The drain** (post-integration decision). Its jobs pay
  $\tau^{job}_\iota$ and interference. A job that runs only in the drain is
  charged over the last $n^{\mathrm{win}}$ operations of the phase; one that
  began during `mixgraph`, over the last $\max(\Delta n_\iota, n^{\mathrm{win}})$.
  A controller arm drains under its fallback settings. The rule errs against
  the controller, as the drain's bytes do.
- No mean field in $J_\beta$. An analytic result that uses mean field says
  so, with its error (A5).

*(d) Attribution* (D §4, Proposition D.16).
- Six shared buckets, on which no agent is rewarded: hit-read, reopen,
  scan-base, memtable, write-path and fixed.
- A job's $\tau^{job}_\iota$ and its $I_\iota$, read and write parts, go to
  its start level: an intra-L0 compaction to L0, a last-level self-compaction to the
  last level. A flush's $\tau^{job}_\iota$ goes to L0 and its $I_\iota$ to
  the write-path bucket.
- **Scan iteration, L0 last** (integration decision I-3). A step's price
  splits into its base $c_{st}(1)$, the non-L0 increment
  $c_{st}(r^-) - c_{st}(1)$ and L0's increment $c_{st}(r) - c_{st}(r^-)$,
  with $r^- = \max(r^{\neg0}, 1)$. L0 pays its increment on every step. A
  returned step's base goes to the scan-base bucket, and its non-L0
  increment in equal shares to its non-L0 children (a memtable's share to
  the memtable bucket).
- **Hidden steps to the level above** (I-4, in the reading INTEGRATION (f)6
  accepted). A hidden step's $c_{st}(r^-)$ goes to the level directly above
  the level where the hidden entry lives: an entry in L1 or in an L0 file to
  L0, an entry in a memtable to the memtable bucket. Its L0 increment stays
  with L0.
- Iterator blocks go to their table's level; $c_{mt}$ to the memtable
  bucket; $c^0_{get}$, $c^0_{sc}$ and $c_{put}$ to the fixed bucket, its read
  part weighted $\beta_R$ and its write part $\beta_W$.
- Slot blocking also moves L0's hidden-step charge, its iterator blocks and
  the same share of its heap increment. It does not move interference.
- The neighbour charge's one-step prediction also moves the upper
  neighbour's hidden-step input by the predicted $M^{hd}$ (H §3; ARCH-3,
  amended).

*(e) Assumptions, results, criteria and gates*, as PATHWAYS states them.
- A9 (fixed prices), A10 (linear, additive interference) and A11 (in-situ
  job prices) are new. A3′, A5, A7 and A8 are amended. A10 is tested on
  every run by OBJ-8 for the timed read steps, and by the calibration
  (OBJ-2) for the other step types.
- The new, amended and re-checked results are those §0.7 lists. The
  2026-09-29 statements in §0.7's table are corrected.
- New criteria: OBJ-7, OBJ-8, OBJ-9, PROP-6, ARCH-7, ARCH-8, WL-3 and
  CMP-9. Amended: OBJ-1, OBJ-2, OBJ-4, OBJ-6, ACT-4, ARCH-3 and ARCH-5.
- Gate N0 gains items 10 (§3) and 11 (§4).
- Gate N2 starts only after item 11, on item 10's binary, with every price,
  $\kappa$ and its basis fixed before any $\Theta_s$ outcome is seen. Its
  $\Theta_s$ arms at $K_0$ = 8 check D.11's (b2), (b3) and (d2). (b3) holds
  at $K_0$ = 4 only on average, so its exposure is reported at $K_0$ = 4 and
  8, and the secant slope gives D.11's corrected $B$. When (b2) or (d2)
  fails at a trigger, as §9 defines failing, D.11's closed form is not used
  there.
- Gate N3's rules use the amended cost. Gate N4 adds OBJ-7 to OBJ-9, ARCH-7
  and ARCH-8.
- The stall rule stays (D-13 §8, D-14 §1, D-15 §1). The interference charge
  closes only the interference route through stalls, by construction: a job
  run in a stall is charged like any other. Stall time itself stays
  unpriced, so a policy could still lower $J_\beta$ by deferring work until
  writes stall, and the rule bars any claim that does.

**2. The measured facts it rests on.** Node diagnostics of 2026-10-03,
quoted as measured. None is a theorem. A figure derived from a model says
so.

1. **Interference: its size and its path**
   (`~/node_ops/reports/2026-10-03-1632-contention-reads.md`, results in
   `~/node_ops/contention_reads/2026-10-03-1632/`. The earlier run,
   `-1514-`, had no neighbour database; its same-database arm agrees with
   this run's within about 1 point).
   - Setup: one reader thread, uniform keys, every table open; a
     rate-limited writer at about 11 MB/s of user writes and about 100 MB/s
     of compaction traffic; trees at T = 2, 6 and 10; one repeat.
   - Levels 1 and below, with the writer in a *neighbouring* database,
     compactions on / off: filter checks took 1.128 / 1.005 times their
     quiet time, block reads 1.118 / 1.031. A seek at its own counts took
     1.102 / 1.024, and 1.158 with the writer in the reader's own database.
   - A compaction in another database slows reads as much as one in the
     reader's own: the path is shared hardware, not the database. All three
     trees agree.
   - Size: about 0.12% (filter checks), 0.09% (block reads) and 0.08%
     (seeks) per MB/s of compaction traffic. The write path alone adds
     0.5–3 points. Sharing one database adds about 5.6 points to seeks.
   - At T = 10 the writers' compaction ran harder than in the `mixgraph`
     runs of the `Assoc` ablation (`-1206-`, `-1436-`): in `-1514-` the
     `Assoc` and power-law writers ran 123 and 64 MB/s against the
     ablation's 86 and 48.5 MB/s, about 1.3–1.4 times (the operator's
     reading); the `-1632-` neighbour ran 126.5 MB/s, about 1.47 times.
2. **The memtable search** (`-1514-`, `-1632-`, perf_level 4 timers):
   about 0.22 µs per read (0.21–0.25 over both runs) whenever writes keep
   the memtable
   populated, the same in every configuration; 0 with an empty memtable.
3. **Linearity** (`interference_linearity.png` in the repository root,
   untracked when this entry was drafted; made by
   `~/node_ops/diag/plot_interference_linearity.py` from both contention
   runs; 21 points per panel, one repeat per tree; x is the background
   compaction write rate):
   - filter checks are linear in the rate: +0.9% + 0.108% per MB/s,
     $R^2$ 0.88;
   - block reads are roughly linear: +1.4% + 0.105% per MB/s, $R^2$ 0.90;
   - run seeks are not: $R^2$ 0.52; most of the slowdown is there by about
     40 MB/s, and the middle rate lies 8.3, 2.4 and 3.7 points above the
     two-point line at T = 2, 6 and 10;
   - quiet runs scatter by up to ±3.5, ±1.1 and ±4.3 points.
   - The seek pattern suggests a part that does not grow with the byte
     rate: a busy, per-job part. The low-rate seek points are same-database
     runs, so part of it may be the 5.6 points that sharing one database
     adds.
   - Additivity over concurrent jobs cannot be tested from these runs: each
     is one 20 s total.
   - The regression slopes (about 0.11% per MB/s for all three steps) and
     item 1's on-minus-off figures (0.12, 0.09, 0.08) are two estimates of
     one quantity. Neither is a price.
4. **Interference follows the tree's state** (critique Q2; per job, the
   check reports `-2114-pathways-v2-review.md` and
   `-2234-pathways-v2-rereview.md`; $k_0$ from the event log's `lsm_state`
   over the measured phase; event and host logs under `db/qbar-assoc-d21id`,
   `db/n1-assoc` and `db/n1-powerlaw`). On the seven native runs (the d21id
   $\bar q$ arm and Gate N1's pilots, repeat 1) and on all 23 native runs of
   those cells (every repeat):
   - every L0→L1 merge ran with $k_0 = K_0$ = 4;
   - the merges sourced at a level $\ge 1$ ran with L0 empty on average:
     mean $k_0$ at most 0.13 per start level (time-weighted; 0.134
     byte-weighted in the seven runs, 0.14 in all 23), and at most 0.05
     pooled over a run's deeper merges;
   - per job it is not exact: 0.9% and 5.4% of those merges at `Assoc`
     T = 6 and 10 (repeat 1; 0.4–5.8% over all repeats; 3.8% on the
     $\bar q$ arm) ran with one L0 file, never more. Trivial moves at
     `Assoc` T = 10 did so more often: 14–30% of them, with a mean $k_0$ of
     0.12–0.27 at their start;
   - the cascade of deeper jobs after an L0 merge always ended before L0
     next fell due, but at `Assoc` T = 10 its last job ran past the next
     flush in 31–46% of L0 cycles on the $\bar q$ arm and 36–41% on the
     pilots (4.5–8.4% at T = 6, at most 0.2% at T = 2): that is why some of
     those jobs saw one L0 file;
   - other default-point `Assoc` T = 10 runs reach more: the ablation arms
     under `db/abl-assoc` (`asis`, and `pinned` with compactions on other
     cores) had up to 6.6% and 8.8% of deeper merges at one L0 file, pooled
     means up to 0.056 and 0.072, and up to 28% and 38% of trivial moves;
   - no job sourced at a level $\ge 1$ ran while L0 held two or more files,
     and none held the slot while L0 was due, so slot blocking had no
     occasion.
   - *Derived* (critique Q2, its model M2, provisional prices): mean field
     gets the total within 2%, but its slope in $K_0$ is 2.8 times too
     steep, and L0 bytes should pay 1.24–1.94 times what deeper bytes pay.
5. **Per-job time** (operator diagnostics on Gate N1's pilots, repeat 1,
   and the d21id $\bar q$ arm, reproduced in the check reports). The host
   log's job span minus the event log's `compaction_time_micros`,
   non-trivial jobs, `measure_start` to `drain_end`:
   - a median of 2.7–3.5 ms per job, 71–128% of the counted compaction time
     (`Assoc` T = 2: 45.7 s against 35.8 s);
   - 4.5–5.3 ms for jobs sourced at L0, 2.7–3.5 ms for deeper ones;
   - nearly independent of job size: 2.85 ms in the smallest decile, 3.18
     ms in the largest (`Assoc` T = 10);
   - 3.4–3.5 ms at T = 2 against 2.7–3.0 ms at T = 6 and 10 (the $\bar q$
     arm, 2.97 ms);
   - `compaction_time_micros` times only `RunSubcompactions`. The gap holds
     the output directory's fsync, the opening of every new output file,
     the install, mutex waits and listener work
     (`db/compaction/compaction_job.cc` at `8e903efd9`);
   - trivial moves took 2.5–3.3 ms each (the range of per-run medians):
     4,151 moves (13.8 s) on `Assoc` and
     2,153 (6.8 s) on the power law at T = 2, and 0.4–0.6 s per run at
     T = 6 and 10;
   - merges read 12–13% more than they write: 1.124 on the pilot and 1.127
     on the $\bar q$ arm (`Assoc` T = 10; event logs, the check reports);
   - per busy second, L1 and L2 bytes would pay 1.9 and 2.6 times what L0
     bytes pay; per job, 12 and 21 times (critique Q4; 11.7 and 21.0,
     recomputed from the $\bar q$ arm's event log, repeat 1, in
     `-2306-pathways-v2-final-check.md`).
6. **D-15 §3(b)'s per-byte write time depends on $T$**: it was higher at
   T = 2 than at T = 10, by 6.5–7.1% on `Assoc` and 2.2–3.2% on the power
   law, paired by repeat (means 6.7% and 2.8%; critique P3, recomputed from
   `sst_write_seconds` over `sst_bytes_written` in
   `db/n1-assoc/graphs/summary.csv` and `db/n1-powerlaw/graphs/summary.csv`,
   the check reports).
7. **Scans** (Gate N1's pilots, `Assoc`, repeat 1, `db/n1-assoc`, tickers
   between the host log's `measure_start` and `drain_end` stamps,
   reproduced in the check reports; the power law issues no scans):
   - each scan returns about 543 entries (543.5 on repeat 1, 542–546 over
     the three repeats): about 496M `Next`s per 26.1M operations;
   - hidden entries stepped over: 570M at T = 2 and 114M at T = 10, so 1.15
     and 0.23 per returned entry. That is about 11 (T = 2), 5 (T = 6) and
     5.4 (T = 10) times what uniformly spread garbage would give (10.8–11.0,
     5.1–5.5 and 5.4–5.5 over the three repeats; `scan_internal_skips` and
     `scan_returned_entries` against `space_amplification` in
     `db/n1-assoc/graphs/summary.csv`): hidden versions sit on hot keys.
     Garbage during a run can exceed garbage at its end, so these are upper
     estimates of the concentration;
   - data-block cache misses, Gets and scans together: 296.8M against
     178.8M; run seeks: 7.9M against 3.75M;
   - the client spent 410 s at T = 2 (repeat 1; at least 409 s in every
     repeat) against at least 208 s at T = 10 outside its Get, Seek and
     write calls. About 200 s of it
     depends on the configuration and was unpriced: more than T = 2's whole
     priced $\mathcal C_R$, about 88 s at the d21id prices without reopens.
8. **Reopens** (`-1206-assoc-ablation.md`): `Assoc`'s reads took 1.256
   times stage 18's time per reopen; 1.154 without writes; 1.071 without
   writes and scans; the power law 1.040. Multiplicatively, the writes
   account for about 0.09, the scans' pollution of the caches about 0.08,
   and the rest about 0.07.
9. **Gets against the quiet prices** (`-1436-get-time-vs-prices.md`):
   - the rest of a quiet `Assoc` Get, reopens left out, took 0.74 of the
     uniform-key prediction (0.715–0.780); the whole Get, with its reopens
     at the reference, 0.85–0.87. With writes the rest took 0.958, and on
     the power law 1.075;
   - on stage 18's d21id runs, the Get fit's intercept is 74 ns on
     RocksDB's internal Get timer against 157 ns on db_bench's. The seek
     fit on the internal Seek timer has a negative intercept, −724 ns. The
     contention runs' quiet seek intercepts were +184 ns (`-1514-`) and
     +491 ns (`-1632-`);
   - settle leaves 0 or 3 L0 files, depending on background timing.
10. **Step timers cost time.** At perf_level 4 a Get took about half as long
    again (1,698 against 1,101 ns, T = 2 misses, `-1514-`; critique Q4).
    The timers' overhead pulls loaded-to-quiet ratios toward 1.
11. **Job spans in operations.** Trivial moves served at most 461
    operations on the pilots and 484 on the $\bar q$ arm in their first
    repeats, and 503 and 546 over all repeats. An L0 merge took about
    39 ms at `Assoc` T = 10, about 2,700 operations at $\bar q$ (critique
    Q2).
12. **Read prices move between sessions**: $c_f$ by 9.7% (D-22 §1).

**3. Instruments and binary.**

*(a) The fork changes* (Gate N0 item 10). They make a new binary.
- Job-boundary step counters: the host log's `job_begin` and `job_end`
  records carry the cumulative counters of every priced foreground step type
  (read steps by type, and the counts of Gets, scans and Puts), with the
  stamps $n^b_\iota$ and $n^e_\iota$.
- Flush begin and end records, with the same stamps and counters.
- Counter snapshots: the same counters, with their operation stamp, every
  $n^{\mathrm{str}}$ operations.
- Per-level hidden-step counters, keyed by the level where the hidden entry
  lives, and per-level iterator-block counters, aggregated across threads,
  with a global ticker for each count that has none (OBJ-9).
- Heap child counts: each iteration step by its $r^{\mathrm{L0}}$ and
  $r^{\neg0}$ and by its type (returned, or hidden with its entry's level),
  with the levels of the non-L0 children of returned steps.
- The interference timer: SST-read time, at least D-21's reopen timer,
  split by whether at least one background job (a flush included) is
  running, with the matching counts, in §5's strata.
- The scan set-up timer: an in-store timer of each scan's set-up, the
  iterator's creation and the fixed part of its first `Seek`, excluding the
  run seeks that $c_{sk}$ prices, with its count of scans. $c^0_{sc}$ is
  measured from it (§4(d)). It is in this binary, like every other
  instrument, so every calibration runs on the one binary CMP-9 requires.
- A counter read at an event may include part of the operation in progress,
  so a window can be off by one operation's steps at each end. Whether the
  fork reads the counters as of the last completed operation instead is
  pending (§7, item C).

*(b) What runs again on it.*
- The fork's tier-2 tests for every new counter, record and timer, in the
  Debug tree; the tier-1 tests of the evaluator and the plugin.
- The preflight (13), which writes a new marker.
- ACT-4 and ARCH-5: the new instruments must not move native compaction
  (A-Impl-10).
- Every price is bound to its binary. D-22's read prices reach this binary
  through D-22 §2(g), or D-22's two sessions run on it (§4(f)). The hull is
  measured on it. Under CMP-9, every arm of a comparison runs one binary
  identity (6d80a52's, `db_bench` with its library) and is scored with one
  prices file, one $\kappa$ and one $\bar q$.

*(c) Gate N1's reuse.* Gate N1's outcome (D-19) is reused on this binary on
D-19 §4's ground, as PATHWAYS G §4 states it: the new counters and timers
observe, and change neither what RocksDB compacts or when, nor the
foreground's speed, since the admission test's $\omega_i$ is counted in
operations.
- ACT-4 and ARCH-5 on this binary test the compaction side.
- The foreground's speed is tested, as PATHWAYS G §4 says, by the native
  arms' throughput on this binary, paired against the pilots', within a
  margin pending under §7 item 4. The reference arms' throughput is also
  reported beside $\bar q$ (§4(c)). The instruments' own cost is bounded
  through the timer's sampling (§7, item B).
- If either side fails, the reuse stops, and a dated entry reopens Gate N1
  before any price, comparator arm or pilot reuse on this binary.

*(d) Pipeline and contract* (the operator's).
- The evaluator gains $\mathcal C_W$'s read bytes, its jobs by kind, its
  Put inserts ($c_{put}$ per Put) and its write part of interference;
  $\mathcal C_R$'s iteration, memtable, fixed-part and read-interference
  terms; the read and write parts of every $I_\iota$, from the host log
  alone; the physical interference (OBJ-6); and OBJ-7 to OBJ-9. The plugin's attribution log
  gains the same terms, by the same estimator (OBJ-1, ARCH-7).
- Each new price is a required plugin key with no default, as $c_{open}$ is
  (D-21 §2(e)). A $\kappa$ may be 0.
- `prices.json` gains a new schema, which holds the new prices, $\kappa$ and
  its basis, $n^{\mathrm{win}}$ and $n^{\mathrm{str}}$. Official runs refuse
  an older schema.
- The contract is amended in place, with this entry as the reason:
  `authority` names the amended theory and this entry, and
  `prices.device_times` names the new prices. Values enter when the dated
  entries of §7 fix them.

*(e) Node order.*
1. The owner commits this entry with PATHWAYS and confirms §3 to §6.
2. Dated entries decide §7 items B (the timer's sampling) and C (how the
   counters are read), which shape the binary.
3. The operator implements (a) and (d). Tiers 1 and 2 pass.
4. The preflight on the new binary. Verdicts: ACT-4 and ARCH-5, and the
   throughput side of (c).
5. A dated entry decides §7 item A's rule for $n^{\mathrm{win}}$ and
   $n^{\mathrm{str}}$, so that their values follow from the reference arms
   by that rule. Then the reference arms (§4(c)), and the job prices,
   $n^{\mathrm{win}}$ and $n^{\mathrm{str}}$ from them.
6. Dated entries decide §7 items D and E. Then the new trees with garbage
   are built and archived (§4(b)), with their dated build record.
7. Two price sessions with a reboot between them (§4(f)). A dated
   calibration record follows.
8. The dated entries of §7 that govern Gate N2.
9. Gate N2 (`25`), after D-17 and D-18.

D-22's own sessions may run first, on the current binary, as D-22 §6
orders. Their prices then reach the new binary only through D-22 §2(g).

**4. Calibrations before Gate N2** (Gate N0 item 11). The designs below are
the operator's proposal. The counts, rates and grids are proposed with the
implementation and confirmed by the owner before the runs (§7, item D).
Common rules:
- every calibration runs on the new binary. Every instrument the
  calibrations need is in it (§3(a)); nothing that items D and E decide
  changes it (CMP-9);
- the reader's keys and the price trees' loads are uniform, as stage 18's
  are, unless §7 item 2 decides that the foreground prices are measured per
  workload; then each is measured with that workload's keys and Puts;
- every price, $\kappa$ and its basis is fixed and recorded before any
  $\Theta_s$ outcome is seen;
- each price is a device time, converted at $p_{\mathrm{dev}}$;
- no fit writes a price that is not positive or not identified, as D-15
  §3(d), D-20 §2(c) and D-22 §2(i) refuse. The spans that make a price
  identified are pending (§7, item E);
- each is re-measured on any hardware or binary change.

*(a) $\kappa$ (A10).*
- **Reader.** One thread, warm cache, every table open (capped `open_files`
  for reopens), on archived trees, pinned as runs are. Its own database is
  never written, so its tree, memtable and caches stay as they are.
- **Writer: the neighbour design** (`-1632-`). A second `db_bench` writes
  its own copy of a tree on the same machine, so only shared hardware links
  it to the reader. Compactions on against off separate compaction traffic
  from the write path. On the new binary the writer's own host log gives its
  jobs' kinds, bytes and spans, so each run has its byte rates and its busy
  time by kind. One same-database arm, at the `Assoc` T = 10 rate, measures
  what sharing one database adds.
- **Linearity: a rate sweep.** Quiet, and at least four writer rates, on
  each tree, spanning the compaction traffic at every $T$ the experiments
  run. At T = 10 the ablation's `mixgraph` runs had 48–86 MB/s (`-1206-`,
  `-1436-`); over T = 2 to 10 the pilots span about 20–88 MB/s
  (`-2306-pathways-v2-final-check.md`). The critique proposes about 25, 50,
  100 and 150 MB/s.
- **The basis: a job-size sweep at fixed byte rates.** The writer's job
  size changes (its target file size and level base, or its $T$) while its
  compaction bytes read and written per second both stay fixed, so jobs per
  second and busy time change and bytes per second do not. Where both rates
  cannot be held (a change of $T$ moves the read/write mix), the
  coefficients are fitted jointly over all runs, with the busy fraction by
  kind, the read-byte rate and the written-byte rate as regressors.
- **Read against written bytes.** A second sweep holds the bytes written
  fixed and changes the bytes read (a writer whose merges drop much,
  against one whose merges drop nothing), to weigh the two in $Y_\iota$.
- **Flushes, apart from the neighbour's own Puts.** In the "write path
  alone" arm the neighbour client's Puts run beside the reader, which never
  happens with the experiments' one client. So that arm runs twice: with
  the normal write buffer, and with one large enough that no flush happens
  in the measured window. The difference is the flushes' effect; the
  large-buffer arm is the Puts' own. Where the reader's step times can be
  stamped and aligned with the writer's job spans (its host log), the
  reader's time is also split by which neighbour jobs run at each moment:
  none, a flush, a compaction, or both.
- **Additivity: one running job against two.** Compactions alone (a
  neighbour's manual compaction, with no writes), flushes alone (as above),
  and both (writing with compactions on), at matched rates. In the
  experiments at most one compaction and one flush overlap
  (`max_background_jobs` = 2, one compaction slot), so this is the case
  that matters. On arm averages, non-additivity shows only in proportion to
  the overlap; the moment-by-moment split, where it is available, tests it
  directly. Two neighbour compactions against one is reported beside it.
- **Step timers** (perf_level 4, as in the contention runs):
  - per-level table time on levels $\ge 1$, for filter checks and block
    reads;
  - the Seek call at its own counts, for run seeks;
  - the time per step after the seeks, in `seekrandom` with `seek_nexts` >
    0, for iteration steps and iterator blocks;
  - `get_from_memtable_time` and `seek_on_memtable_time`, for the memtable
    search;
  - D-21's reopen timer, for reopens;
  - the memtable-write timer, for Put inserts;
  - and the fixed parts as (d) defines them.

  The timers add about half to a Get and pull ratios toward 1. So loaded
  and quiet runs are compared only at one timer level, and each $\kappa$ is
  taken at perf_level 1, at the run's own counts, wherever an instrument
  there suffices. The timer runs locate the slowdown by step and level, and
  their dilution is reported.
- **Step types:** filter checks, block reads, run seeks, iteration steps
  (`seek_nexts` > 0), iterator blocks, memtable searches, reopens, Put
  inserts, and the fixed parts of Gets and of scans. The calibration decides
  which $\kappa$ are nonzero (§7, item 1).
- The reader's keys are uniform, as stage 18's are. Whether $\kappa$ is also
  measured with each workload's keys (locality moved the rest of `Assoc`'s
  Gets by 26%, §2 item 9) goes with §7 item 2.
- **What picks the basis** (the operator's reading rule; the owner decides,
  §7 item 1):
  - a slowdown that does not change with job size at a fixed byte rate:
    per byte, $\kappa^J = 0$;
  - a slowdown that follows busy time at a fixed byte rate: a busy part,
    $\kappa^J$, per kind if the kinds differ;
  - both: both features, fitted jointly with every $\kappa \ge 0$;
  - the bytes in $Y_\iota$: $Y_\iota = X_\iota + \lambda(S_\iota + O_\iota)$,
    with $\lambda$, the weight of a byte read against a byte written,
    fitted where the sweeps identify it. $\lambda = 0$ counts bytes written
    only, $\lambda = 1$ both alike, and bytes read only is
    $Y_\iota = S_\iota + O_\iota$. One $\lambda$ for every step type keeps
    A10's form; a $\lambda$ that differs by step type needs a dated
    amendment of A10. $\lambda$ is part of $\kappa$'s basis, pending under
    §7 item 1, and no default is set here: if the sweeps cannot identify
    it, the owner decides it by a dated entry before any run it governs;
  - a slowdown that follows the number of jobs, but neither busy time nor
    bytes: A10's two features do not fit, and a dated entry amends A10
    before Gate N2.

  The plot's seeks (§2 item 3) already point to a part that does not grow
  with bytes. So the sweep must be able to show a busy part, for seeks at
  least, and to tell it apart from the same-database share.
- **If linearity or additivity fails** for a step type, outside its
  tolerance (§7 item 4):
  - its $\kappa$ is not fixed, and no Gate N2 run starts;
  - the operator reports the failure per step type and tree;
  - a dated entry decides what changes: another form of A10; A10 kept as an
    approximation over the experiments' range, with its measured error
    stated wherever a result uses it; or that step's $\kappa$ set to 0, with
    the error reported;
  - the test is not repeated until that entry says what changes.

  For additivity, the share of job time in which a flush and a compaction
  overlapped on the reference arms is also reported, since it bounds what
  non-additivity can move.

*(b) The scan-step prices $c_{st}(r)$ and $c_{ib}$.*
- **Trees with garbage.** D-22's trees are loaded without overwrites, so
  they hide no versions. New trees are built as D-22 §2(a) builds, with
  (a′)'s two flags, then overwritten with keys drawn as the workloads draw
  them, so that hidden versions sit on hot keys as in the runs, then
  settled. The grid spans the depths of T = 2 to 10, garbage from none to
  at least the pilots' 1.15 hidden steps per returned entry, and two or
  more block sizes (below). The trees
  are archived and identified as D-22 §2(b) says, with a dated build
  record.
- **Runs.** `seekrandom` at several `seek_nexts` > 0, `Assoc`'s mean scan
  length of about 543 among them, and at 0; every table open; one thread;
  warm cache; each run in its own process. The new counters give the steps
  by heap size, the hidden steps and the iterator blocks.
- **Fit.** Seconds per scan on the scan's set-up, its run seeks at $c_{sk}$
  (D-22's `seek_nexts` = 0 price, held fixed), its steps at $c_{st}(r)$, and
  its blocks at $c_{ib}$.
  - $c_{st}(r)$ takes the form the data support. Roughly affine in
    $\log_2 r$ is expected; nondecreasing is required; convexity is not
    assumed.
  - Heap size moves with depth and with L0 files, hidden steps with
    garbage, returned entries with `seek_nexts`, and blocks with both.
  - Steps and blocks move together. Under D.19(v)'s block model a scan
    loads about one block per $b_{\text{blk}}$ entries it steps through, so
    `seek_nexts`, depth and garbage move both in proportion, and alone they
    identify only $c_{st}(1) + c_{ib}/b_{\text{blk}}$. So the grid also
    varies the entries per block: the same trees at two or more block sizes
    (or value sizes, if a step's own cost is shown not to depend on its
    entry's size). Where that still does not separate them, the fit reports
    the combination it identifies, and a dated entry decides how it is
    split.

*(c) The job prices* $c^F_{job}$, $c^0_{job}$, $c^d_{job}$, $c^{tm}_{job}$
and $c_{cr}$, with $c_w$ re-fitted.
- **The reference arms:** D-14 §2's five `native` arms at T = 10 per
  workload, with the settle step, the default options and the Gate N2 run
  length, on the new binary after its preflight, run as D-21 §2(f) ran them
  (with a new tag). Their mean throughput is reported beside $\bar q$.
  $\bar q$ does not change unless §7 item 6 says so.
- **The fit**, per run, over every job that completes from $n_w$ to the end
  of the drain, by least squares:
  wall span $= t^{\mathrm{kind}}_{job} + t_{cr}(S_\iota + O_\iota) + t_wX_\iota$,
  in device-seconds.
  - A span runs from `job_begin` to `job_end`, and for a flush between its
    new begin and end records.
  - A trivial move has $S_\iota = O_\iota = X_\iota = 0$, so $t^{tm}_{job}$
    is its mean span. A flush has $S_\iota = O_\iota = 0$.
  - Flushes and merges share $t_w$: a byte is taken to cost the same to
    write in either, as $\tau^{job}_\iota$ prices it.
- Each price is the median of the ten per-run values, with the minimum and
  maximum reported (D-15 §3(b)'s rule). D-15's per-run $t_w$ is reported
  beside it.
- **$c_{cr}$ against $c_w$.** A merge's bytes read and written move together
  (§2 item 5), so the fit may fix their sum better than either. The joint
  fit is reported with its per-run spread and the correlation of the two
  estimates. Which one is used is §7 item 11:
  - the joint fit as it stands;
  - $c_{cr}$ from a separate calibration in which read and written bytes
    come apart (for instance merges of trees whose merges drop a known
    share), with $c_w$ then fitted on the reference arms;
  - or the sum alone, priced per byte written with $c_{cr}$ = 0.

  A result that turns on the split says so (D §1).
- On the reference arms OBJ-7's ratio is near 1 by construction, so OBJ-7
  tests the other configurations. The per-job gap at T = 2 (3.4–3.5 ms) was
  above T = 10's (2.97 ms), which the kinds may not absorb (§7 item 5).

*(d) $c_{mt}$, the fixed parts and the Put insert.* No time is priced twice,
and none from outside the store.
- $c^0_{get}$, as PATHWAYS defines it (§1.1): the device time of the
  store's Get call that does not depend on the tree (snapshot, SuperVersion
  reference, lookup-key set-up and return), with the memtable search
  ($c_{mt}$), every table step ($c_f$, $c_{blk}$, $c_{open}$) and the
  client's own work excluded.
  - It is measured as stage 18's Get intercept (D-15 §3(c)'s fit, whose
    slopes carry the table steps; all-open, so without reopens), taken on
    RocksDB's internal Get timer (`rocksdb.db.get.micros`), which leaves out
    the client's work.
  - The memtable search on the price trees (their memtables are empty;
    `get_from_memtable_time` measures it) is subtracted. Anything else the
    measurement holds beyond the definition is removed or shown negligible
    (Gate N0 item 11).
- $c_{mt}$: the whole memtable search per Get and per scan, when the
  memtable holds what the experiments' memtables hold on average (the host
  log's memtable fill at each L0 decision, H §2). It is the extra internal
  time per read against the same reads with an empty memtable, at
  perf_level 1, plus the empty memtable's search time that $c^0_{get}$
  leaves out. The perf_level 4 memtable timers are a cross-check.
- $c^0_{sc}$, as PATHWAYS defines it (§1.1): the device time of creating a
  scan's iterator and of the fixed part of its first `Seek` (snapshot,
  SuperVersion reference, merging-iterator set-up), with the run seeks
  ($c_{sk}$), the memtable search ($c_{mt}$), the iteration steps and
  iterator blocks ($c_{st}$, $c_{ib}$), any reopen ($c_{open}$) and the
  client's own work excluded. It is §3(a)'s scan set-up timer's time per
  scan on the quiet, all-open price trees, whose memtables are empty. The
  seek fit's intercept is not used: it was negative on d21id (−724 ns), and
  the internal Seek timer does not cover the iterator's creation.
- $c_{put}$: the memtable insert of the workload's own Puts, with the WAL
  off and the experiments' memtable size, on RocksDB's memtable-write and
  internal write timers, with the Puts served while a flush runs reported
  apart. It is per Put, plus a part per byte if the fit over the workload's
  value sizes supports one; $c_{put}$ is then the mean over the workload's
  Puts (D §1), which makes it depend on the workload: §7 item 2 decides
  whether that stands.
- Their $\kappa$ come from (a).

*(e) The window and the stride.*
- $n^{\mathrm{win}}$, by PATHWAYS' rule: a fixed operation count, below the
  spans in operations of the merges Proposition D.11's (d2) relies on (the
  L0 merges and the merges sourced at a level $\ge 1$). The reference arms
  give each such merge's $\Delta n_\iota$, leaving out those in stalls or
  in the drain, and $n^{\mathrm{win}}$ is set below the shortest of those
  spans. How far below, and whether one value serves both workloads, is
  pending (§7, item A).
- $n^{\mathrm{str}}$: proposed no larger than $n^{\mathrm{win}}$, so that
  rounding a window's start down to a snapshot adds at most as many
  operations as the window holds. It is set with $n^{\mathrm{win}}$, and the
  host log's size is reported. Its value is pending (item A).

*(f) How the foreground prices enter D-22's sessions.*
- $c_{st}(r)$, $c_{ib}$, $c_{mt}$, $c^0_{get}$, $c^0_{sc}$, $c_{put}$ and
  every $\kappa$ are measured in each of two sessions on the new binary, in
  D-22's design: archived trees, checked against their manifest
  (D-22 §2(c)), and a reboot between the sessions (D-22 §2(f)). The new trees with garbage and the writer's
  trees are archived the same way.
- Each is reported with both sessions' values and $|B - A|$ over
  $(A + B)/2$.
- Whether D-22 §2(f)'s 3% test applies to each, so that a price that fails
  it is not final, is pending (§7 item 10). $\kappa$ is a slope of about
  0.1% per MB/s, measured once so far.
- The job prices, $n^{\mathrm{win}}$ and $n^{\mathrm{str}}$ come from the
  reference arms, outside the test, as $c_w$ does (D-22 §4).

*(g) Sensitivity* (extends D-22 §2(h); PATHWAYS C §3). Each gate's verdict
is also computed at the ends of each new price's reported range and of each
$\kappa$'s, and reported beside the main result. This is not a gate.

**5. Per-run checks.** Each runs over the measured phase, from $n_w$ to the
end of the drain. Every tolerance, and what every failure means, is pending
(§7 item 4), except OBJ-9's 1%, which PATHWAYS fixes; what an OBJ-9 fault
means is §7 item B. D-21's precedent, under which a run outside its
tolerance is not priced, is a precedent PATHWAYS names, not a decision.

- **OBJ-7, the job prices.**
  - Compares: the jobs' own summed wall spans with their priced device
    time, $\sum_\iota t^{job}_\iota$, as a ratio, overall and per kind.
    Ratios per start level and per configuration are reported.
  - Instrument: the host log's job and flush records, the event log, and
    the evaluator.
  - A failure means the jobs took longer or shorter than the in-situ prices
    (A11) say for this configuration; for instance, the per-job gap moved
    with $T$ (§7 item 5).
- **OBJ-8, interference.**
  - (a) The evaluator computes every window, the read and write parts of
    every $I_\iota$, and $\mathcal I$, from the host log alone.
  - (b) The timer check. Per stratum, the SST-read time of the units served
    while a job runs is compared with A10's *physical* prediction from the
    run's own jobs: the stratum's own time per unit with no job running,
    from the same run, times its count with a job running, times 1 plus the
    unit-weighted mean of $\sum_{\iota\in\mathcal J(n)}s^{\text{phys}}_{x,\iota}$.
    The comparison is made per stratum and summed over strata. Drain jobs
    carry a charge but meet no read, so they do not enter.
  - **The strata** (proposed): the step type (filter check, block read, run
    seek, reopen, with a Get's and an iterator's reopens apart) by level,
    with L0 apart from the levels below. That is also the table-size split:
    L0's flush-sized files of about 2 MB against the 512 KiB files below.
  - L0 is not split further by $k_0$. Every L0 merge runs at $k_0 = K_0$
    (§2 item 4), so a stratum at $k_0 = K_0$ would have almost no units
    served with no job running, and no baseline of its own. The $k_0$ mix
    of each L0 stratum, with and without a job running, is reported beside
    the check.
  - The timer times the timed read steps only. Put inserts and the fixed
    parts are not timed per run; their $\kappa$ are tested by the
    calibration (OBJ-2), as A10 now says.
  - Whether the timer samples operations, and at what rate, is pending
    (item B).
  - A failure means A10 or the calibrated $\kappa$ does not hold in that
    run, or that a mix difference was left inside a stratum. The charge
    stays well defined (Lemma D.17). What fails is the claim that it prices
    the physical slowdown.
  - (c) The calibration's linearity and additivity tests are recorded with
    the prices.
  - (d) The window guard, reported per run. Its first share decides
    whether (d2) fails at a trigger, and its last whether (b2) does (§9),
    each against a tolerance pending under §7 item 4:
    - the share of merge bytes, by kind and start level, whose window
      exceeded their own span;
    - the jobs charged over a window that reached back before they began,
      stalls and drain apart;
    - (b3)'s exposure: the $\mathcal W$-weighted mean L0 count over the
      windows of the deeper merges and trivial moves, and the share of them
      with $k_0 \ge 1$, at every trigger, $K_0$ = 4 included;
    - (b2)'s share: the share of L0-merge bytes in merges that did not
      start at $k_0 = K$, did not take all $K$ files, or saw a flush
      complete while they ran, from the event log's `lsm_state` and its
      compaction and flush records.
- **OBJ-9, the scan and memtable counters.**
  - Compares: the per-level hidden steps (keyed by the entry's level),
    iterator blocks and steps by heap children with their global tickers,
    within 1% (PATHWAYS' value); and $R_{nx}$ and the counts of Gets, scans
    and Puts across the arms of one workload and seed, which must be equal.
    Any difference is a fault.
  - What a fault means for the run is pending (item B). Proposed: its counts
    are invalid and it is not priced.
- **D-21's reopen check** runs as dated until §6 is decided.
- **OBJ-6** reports, with no threshold: the physical interference beside the
  charge; the share of compaction bytes in jobs that served no operation,
  stalls and drain apart; the drain's charged interference; and the client
  time that no price covers.
- **CMP-9** reports every arm's checks with each comparison.

**6. The proposed change to D-21's reopen check. Proposed, pending the
owner. D-21 stands until a dated entry decides.**
- **Why.** A reopen is a read step. Under A10, one served during jobs costs
  $c^0_{open}(1 + \sum_\iota s^{\text{phys}}_{open,\iota})$. D-21 compares
  the run's own seconds per reopen with stage 18's quiet reference, so it
  expects 1 where A10 predicts more.
- **The proposal** (PATHWAYS D §1). Compare the run's ratio with
  $1 + \bar s_{open}$, the reopen-weighted mean physical surcharge that A10
  predicts for the run's own jobs,
  $\bar s_{open} = O^{-1}\sum_\iota s^{\text{phys}}_{open,\iota}\,O(n^b_\iota, n^e_\iota]$,
  with $\kappa^J_{open}$ and $\kappa^B_{open}$ from §4(a). It uses the
  physical surcharge, not the charge, because the timer measures the run's
  own time.
- **The alternative.** With OBJ-8's split timer, compare the reopens served
  while no job runs with the reference directly: a check of $c^0_{open}$
  alone.
- The owner chooses one of the two (§7 item F); its tolerance under
  interference is §7 item 4. Either is stratified by level or by table-size class: L0's files take
  longer to reopen, and more of their reopens fall inside L0 merges. D-21's
  floor of 1,000 reopens and its consequence stand unless that entry changes
  them. The contract's `prices.reopen_time_check` changes only with it.
- **What it can do for `Assoc`.** Of `Assoc`'s excess of 0.256 over 1,
  removing the writes removes 0.102 (1.256 against 1.154; about 0.09 as a
  multiplicative factor, §2 item 8), and that bounds what interference can
  explain. The scans' cache pollution (about 0.08) and the rest (about 0.07)
  remain. This entry does not decide `Assoc`'s $c_{open}$ (§7 item 3).

**7. Pending owner decisions.** Nothing here resolves them. Each is decided
by a later dated entry before the first run it governs.

From PATHWAYS §0.7, numbered as there:
1. **$\kappa$'s basis.** Per byte, per busy time or per job; which bytes
   $Y_\iota$ count, $\lambda$ included (§4(a)); which $\kappa$ are nonzero; and whether the same-database
   share enters $\kappa$. After the calibration (§4(a)); before Gate N2.
   *Decided in D-24 §1 item 1.*
2. **How the quiet read prices $c^0_x$ are measured**: on quiet uniform-key
   trees, as stage 18 does, or per workload (the operator's open decision
   D2). The rest of a quiet `Assoc` Get took 0.74 of the uniform-key
   prediction (the whole Get 0.85–0.87). This also settles whether $c_{mt}$,
   $c_{put}$ and $\kappa$ are per workload (A9), and so whether §4's trees
   and reader keys stay uniform. Before Gate N2.
   *Decided in D-24 §1 item 2.*
3. **`Assoc`'s $c_{open}$** (the operator's open decision D3). Of its 1.256,
   removing the writes explains about 0.09 as a factor (0.102 of the 0.256
   excess), which bounds what interference can explain (the write path's
   own effect is in it too); the rest is open. Before any `Assoc` arm is
   priced (D-21 §2(c)).
   *Decided in D-24 §1 item 3.*
4. **The tolerances of the per-run checks, and what a failure means**:
   D-21's check under interference (§6), OBJ-7 and OBJ-8; and the
   tolerances of the calibration's tests of A10's linearity and additivity
   (OBJ-2, OBJ-8(c); §4(a)). Added by this entry: the tolerances on
   OBJ-8(d)'s share that defines a (d2) failure, and on its share of
   L0-merge bytes that defines a (b2) failure (§9); and the tolerance on the
   foreground's throughput, the margin for Gate N1's reuse (§3(c)). Before
   Gate N2.
   *Decided in D-24 §1 item 4.*
5. **Whether the per-job prices need a dependence on the configuration
   beyond the kinds.** The median gap was 3.4–3.5 ms at T = 2 against
   2.7–3.0 ms at T = 6 and 10. Before Gate N2; C.4 then needs the rule it
   states.
   *Decided in D-24 §1 item 5.*
6. **Whether $\bar q$ is re-measured on the new binary.** D-13 and D-14 tie
   it to the Programme 1 binary. Before any $\Theta_s$ run.
   *Decided in D-24 §1 item 6.*
7. **Whether $\Theta_s$ gains a priced profile per mode** (Theorem A.2(iv),
   C §1). Before any $\Theta_s$ run.
   *Decided in D-24 §1 item 7.*
8. **Whether the admission test's support screen compares Proposition G.2's
   new level inputs**, for any future pool (G §4). Before any level is
   admitted to a pool.
   *Decided in D-24 §1 item 8.*
9. **H §7's sign convention.** Keep $Q_j = -b + f_\theta + \delta_j$, with
   $b$ a change in normalised cost as `controller/prior.h` computes it, or
   redefine $b$ as a change in reward, which flips the code's sign. Before
   the first learner run (Gate N4). Until then PATHWAYS uses the code's
   convention.
   *Decided in D-24 §1 item 9.*
10. **Whether D-22's 3% test applies to $\kappa$ and to each new foreground
    price** (§4(f)). Before any of them is final.
    *Decided in D-24 §1 item 10.*
11. **How the fit separates $c_{cr}$ from $c_w$**, or whether only their sum
    is priced (§4(c)). Before Gate N2.
    *Decided in D-24 §1 item 11.*
12. **Whether CMP-7 and Definition C.5's regret use $J_\beta$, or $J_\beta$
    less the policy-independent buckets** (the scan-base bucket, the fixed
    bucket and the memtable bucket's searches). Before any regret is
    computed.
    *Decided in D-24 §1 item 12.*
13. **The workload's label.** The project uses ZippyDB's published
    key-range, key, value-size and scan-length parameters with `Assoc`'s
    operation mix, so D-1's label misattributes the distributions, and so
    does the comment in `scripts/dbbench_pipeline/config.sh`. Before the
    label is used in the paper or the comment changes, by an entry that
    names D-1.
    *Decided in D-24 §1 item 13.*

Left open here. PATHWAYS assigns A, C, D and E to this entry (§0.6 item 13,
§0.7, D §1); B is not in PATHWAYS; F is pending there too:
- **A.** The rule that turns the reference arms' merge spans into
  $n^{\mathrm{win}}$ (how far below the shortest span it is set), whether
  one value serves both workloads, and the stride $n^{\mathrm{str}}$.
  Before the reference arms run (§3(e)), so that the values follow from
  them by rule.
- **B.** Whether OBJ-8's timer samples operations, and at what rate, before
  the new binary is built; and what an OBJ-9 fault means, before Gate N2.
  (§5 proposes the strata and that Put inserts and the fixed parts are not
  timed per run. The owner confirms them with §3 to §6.)
- **C.** Whether the fork reads the host log's counters as of the last
  completed operation (exact windows) or at the event (windows off by at
  most one operation's steps at each end). Before the new binary is built.
- **D.** The calibration details §4 leaves to the implementation: the
  writer rates, the repeats, the large write buffer of §4(a), and the grid
  of trees with garbage with its block sizes. None of them changes the
  binary. Before the calibration runs.
- **E.** For each new fit, the spans that make its price identified, and so
  its refusals. Before the calibration runs.
- **F.** §6's choice between the two comparisons (its tolerance is item
  4). Before D-21's check changes.
  *Decided in D-24 §1 item 3.*

**8. What is unchanged, and what this entry amends.** D-13 to D-22 stand,
except at these points:
- **D-13 §1.** The priced device times are no longer only $c_w$, $c_f$,
  $c_{blk}$ and $c_{sk}$, and D-20's $c_{open}$: §1(a)'s prices join them.
  The two money prices (the instance price and $c_s$), $\beta^\star$ and
  $\bar q$ are unchanged.
  D-13's predictions stand as its record; the forward predictions are
  PATHWAYS D §5 as revised (§9).
- **D-13 §2** (write accounting) and **§7** (retirements) are unchanged.
  Scans are now priced by their seeks and their iteration, so P0-1's
  retirement stands.
- **D-15 §3(a)** is unchanged. PATHWAYS writes its price per core-second as
  $p_{\mathrm{dev}}$.
- **D-15 §3(b).** $c_w$ is re-fitted with $c_{cr}$ and the per-job prices,
  on the reference arms, on the new binary (§4(c)). It is now the price of
  one more byte written by a job, not a job's whole time per byte. For the
  new binary this replaces D-21 §2(f)'s re-measurement of $c_w$.
- **D-15 §3(c).** The Get fit's intercept, taken on the internal Get timer
  and less the memtable search, gives $c^0_{get}$ (§4(d)). The fit is otherwise unchanged, as amended by
  D-20 and D-22.
- **D-15 §3(d), D-20 §2(c) and D-22 §2(i)** gain §4's refusals.
- **D-20 §2(f) and D-21 §2(d).** There are now six shared buckets. Reopens
  are still charged at the level where they happen, and the reopen bucket
  keeps only reopens of no known level. A reopen's slowdown under a job is
  charged to the job.
- **D-21 §2(c)** stands until §6 is decided.
- **D-21 §2(e).** The controller's prior and the Gate N3 rules use the
  amended cost and the new prices (H §7, Gate N3), and each new price is a
  plugin key as $c_{open}$ is.
- **D-22.** Its sessions gain §4(f)'s prices and new archived trees with
  garbage. Its §2(g) governs the new binary. Its §2(h) sensitivity reports
  extend to the new prices and $\kappa$ (§4(g)). Its §4 rule, that $c_w$ is
  outside the 3% test, extends to the job prices. Its schema-5
  `prices.json` is followed by a new schema (§3(d)).
- **D-14** (the stall rule's time base, $\bar q$, the two measured
  profiles), **D-15 §1–§2**, **D-16** and **D-19** are unchanged. Gate N1's
  outcome is reused on the new binary on D-19 §4's ground (§3(c)).
- **D-1** is unchanged; its label is §7 item 13.
- D-17 (the Gate N2 screen) and D-18 (the action bounds) are still in draft.
  D-17's screen works on the vectors $(\mathcal C_W, \mathcal C_R, \mathcal C_S)$
  and takes the amended terms with them (Proposition C.4).

**9. Falsification and consequences.**

*Predictions, made in advance.* PATHWAYS D §5 as revised on 2026-10-03 and
committed with this entry. Its magnitudes are provisional until §7 items 1
to 4 are decided.
- **Read priority.** Little or no room beyond the static comparator on
  stationary `Assoc`. The L0 trigger's optimum rises, and has a floor
  wherever interference on reads is positive. Scan iteration is probably the
  largest read cost on `Assoc` that depends on the configuration, so read
  priority's comparator there is expected at the larger $T$. Lever (e) does
  not beat the best static trigger on stationary `Assoc`. Lever (f) gives no
  measurable gain at the default point.
- **Write priority.** The comparator is expected among configurations with
  fewer, larger jobs: larger $T$, larger $K_0$.
- **Space priority.** The room on `Assoc` stays small. Its optima move toward
  the configurations that hold the least garbage on hot keys.
- **OBJ-7.** At T = 2 the ratio of job spans to priced job time is above
  T = 10's, since the prices come from T = 10 arms and both the per-job gap
  and the per-byte write time were higher at T = 2 (§2 items 5 and 6). No
  size is predicted.
- **`Assoc`'s reopens.** Under §6's proposal too, `Assoc`'s ratio stays
  outside D-21's 10%: interference can explain at most the writes' part,
  0.102 of the 0.256 excess (about 0.09 as a factor).
- No prediction is made for $\kappa$'s basis or for any new price's value.

*What would falsify a decision, and what then.*
- **The instruments observe and do not act** (§3(c)). Falsified if ACT-4 or
  ARCH-5 fails on the new binary, or the foreground's throughput moves
  beyond its tolerance. Then no price, comparator arm or reuse of
  Gate N1 is made on that binary until a dated entry decides.
- **A10.** Falsified for a step type if the calibration's linearity or
  additivity test fails: §4(a)'s consequence follows. In a run, a failed
  OBJ-8 takes the consequence §7 item 4 fixes. Either way the charge stays
  defined; the claim that $\mathcal I$ prices the physical slowdown is not
  made for that step type.
- **A11.** Falsified if OBJ-7's ratios move with the configuration beyond
  their tolerance. Then §7 item 5 decides a dependence on the
  configuration, and C.4 needs the rule it states.
- **The window rule and D.11's cycle.**
  - (d2) fails at a trigger when OBJ-8(d)'s share, the merge bytes whose
    window ran past their own span, is above a tolerance pending under §7
    item 4. Short jobs, such as trivial moves and short flushes, fail (d2)
    job by job at every trigger by construction; they hold no merge bytes,
    so they do not enter the share, and their charges are small (D.11's
    guard).
  - (b2) fails at a trigger, likewise, when the share of L0-merge bytes in
    merges that did not start at $k_0 = K$, did not take all $K$ files, or
    saw a flush complete while they ran exceeds a tolerance, also pending
    under §7 item 4. OBJ-8(d) reports that share per run, from the event
    log's `lsm_state` and its compaction and flush records.
  - When either fails at a trigger, D.11's closed form is not used there,
    and Gate N2 compares the admissible triggers directly; D.11's integer
    form (iii) still holds (D.11's guard, Gate N2). The failure is reported
    with the measured exposure.
- **D-22's 3% test**, if §7 item 10 applies it to a new price: on a fail no
  price is final, and the test is not run again until a dated entry says
  what changes.
- **This entry fails as a record** if any part of it is changed after the
  first reference arm or calibration run on the new binary has started. A
  change is a new dated entry.

### D-24, 2026-10-04 — the owner's decisions on D-23's pending items, and an exploratory track to the first learner run

**Recorded before any run it governs.** No run under D-23 has started: the
new binary is not built, and no calibration, reference arm or $\Theta_s$ arm
has run. No exploratory run (§2) has started. The owner decided both sections
on 2026-10-04 (UTC). §1 accepts the node operator's recommendations on the
items that D-23 §7 and PATHWAYS §0.7 leave pending. §2 adds an exploratory
track, which changes no gate. D-23's §7 items 1 to 13 and F are marked
decided here; its items A to E stay pending. §4 names each earlier point
this entry amends.

**1. D-23's pending items** (PATHWAYS §0.7), numbered as there.
1. **$\kappa$'s basis, and $\lambda$.** The calibration decides them, by
   D-23 §4(a)'s rate sweep, job-size sweep, and one job against two. The
   model allows both a byte-rate part ($\kappa^B$) and a busy part
   ($\kappa^J$) for each step type. The owner decides only if the
   calibration cannot identify them, by a dated entry before any run it
   governs.
2. **The quiet read prices $c^0_x$ are measured per workload**, with that
   workload's key distribution, inside D-22's sessions, not as one
   uniform-key set. By D-23 §4's common rule, the other foreground prices
   and $\kappa$ are then measured with each workload's keys and Puts too.
3. **`Assoc`'s $c_{open}$ is measured on `Assoc` itself**, as item 2 says.
   - D-21's per-run check is amended as D-23 §6 proposed: the run's time per
     reopen is compared with its quiet value times $(1 + \bar s_{open})$,
     that is, the run's ratio to the quiet reference with
     $1 + \bar s_{open}$, not with 1. It is stratified by level or
     table-size class, as D-23 §6 says.
   - The 10% tolerance is kept. D-21's floor of 1,000 reopens and its
     consequence stand. D-23 §6's alternative, a direct check on the
     reopens served while no job runs, is not adopted.
4. **The per-run checks' tolerances** are each set from the calibration's
   own spread, about 2 to 3 times the session-to-session difference, by a
   dated entry before Gate N2.
   - This covers OBJ-7, OBJ-8 with its (d2) and (b2) guards, the
     calibration's tests of linearity and additivity, and Gate N1's reuse
     margin. The same entry says what each failure means.
   - During the exploratory track (§2) every check is reported only.
5. **The per-job prices are fixed per job kind.** A calibrated state term
   (for example, the number of files in the tree) is added only if OBJ-7
   fails at T = 2, by a dated entry. The owner rejected rolling-average
   prices: they bring wall time back into $J_\beta$ (Lemma D.17) and price
   different arms at different rates.
6. **$\bar q$ stays fixed** and is not re-measured on the new binary. The
   new binary's rate is reported beside it.
   - The owner rejected a rolling-average $\bar q$ for the same reasons: it
     would break D.15's per-operation space charge, Lemma D.17, A8 and the
     hull results, which need one price scale per workload.
   - The value is the recorded one (the $\bar q$ record of 2026-10-02:
     69,021.5 ops/s for `Assoc`, 120,609.3 for the power law; the
     contract's `reference_rate`). The instruction called it "the d21id
     value". No $\bar q$ was recorded from the d21id arms, whose mean D-21
     §2(f) reports beside $\bar q$, so this entry reads it as the recorded
     value. If the owner meant the d21id arms' mean, that is a new dated
     entry before any run it governs.
7. **$\Theta_s$ gains the priced profile** of Theorem A.2(iv), for the
   headline balanced mode only, at the formal Gate N2. It is not in the
   exploratory screen (§2).
8. **The support screen of the admission test is deferred** until a pool
   exists (D-19: none now). The entry that admits a level decides it.
9. **H §7's sign convention is the code's.** $b$ is a change in normalised
   cost, and $Q_j = -b + f_\theta + \delta_j$.
10. **D-22's 3% test applies to the new base prices**: $c_{st}(r)$,
    $c_{ib}$, $c_{mt}$, $c_{put}$, $c^0_{get}$ and $c^0_{sc}$. For $\kappa$
    the rule is different, and both parts must hold:
    - the two sessions' estimates agree within their combined confidence
      interval;
    - the slowdown each predicts at the reference compaction traffic
      agrees within 1 percentage point.
11. **$c_{cr}$ against $c_w$.** D-23 §4(a)'s job-size sweep adds a
    garbage-heavy neighbour, whose merges read far more than they write, to
    identify $c_{cr}$. If it is still not identified, $c_{cr}$ and $c_w$ are
    fitted combined, with the restriction stated.
12. **Regret** (CMP-7, Definition C.5) is computed on $J_\beta$ less the
    policy-independent buckets (memtable, scan-base, fixed). The regret on
    the full $J_\beta$ is reported beside it. The write-path bucket stays
    in, since it depends on $K_0$.
13. **The workload's label is corrected.**
    - The operation mix is Cao et al.'s `Assoc` mix. The key, value-size and
      scan-length parameters are their ZippyDB fit (FAST 2020, §7.3,
      App. A.3).
    - D-1's paper label is corrected accordingly. D-1's choices, its two
      departures from the published fit among them, stand.
    - The comment in `scripts/dbbench_pipeline/config.sh` is corrected in
      the same commit as this entry, and the paper names the workload this
      way.

**2. The exploratory track** (owner, 2026-10-04). Its goal is a learner
running and producing results by Thursday 2026-10-08.
- **Everything in it is exploratory.**
  - It supports no claim and no gate verdict, and its runs are never pooled
    with gate runs.
  - Its results are kept under their own sessions and roots (`explore-*`),
    with an `EXPLORATORY` marker.
  - They use provisional prices, so they run as D-22 §2(j)'s diagnostic
    runs: `04` and `plugin_config` write "provisional prices" into every
    output.
  - The gate track (D-23's calibrations, the formal Gate N2 on the final
    binary, Gates N3 onward) is unchanged and resumes after it.
- **Scope.** `Assoc` only; the power law is code-ready but not run. T = 10
  only.
- **The exploratory screen.**
  - $\Theta_s$ at T = 10 on the current binary: all 8 admissible
    $(K_0, \text{base})$ points times D-13 §4's 4 profiles, 2 repeats each,
    and the native arms with 5.
  - It is priced at the provisional prices (`build-dbbench/prices.json`,
    schema 4).
  - Its only use is ranking. The top 3 configurations by mean $J_\beta$ in
    balanced mode are re-run with 3 seed-paired repeats on the interim
    binary, beside native, for the learner comparison.
- **Provisional prices.**
  - The job prices come from the existing reference ($\bar q$) arms' host
    logs.
  - $\kappa$, the scan-step prices, $c_{mt}$, $c_{put}$ and the fixed parts
    come from one-session diagnostics.
  - D-22's two-session protocol follows later, in the gate track.
  - Every exploratory run's counts are kept, so that it can be re-priced.
- **An interim binary** carries a subset of Gate N0 item 10's instruments,
  and its run manifest records the subset. Until the full instruments
  exist, per-level scan attribution follows a provisional rule, recorded
  with that manifest. The formal Gate N2 runs on the final binary.
- **Plan order.** The learner (implementation plan step 10) is built now, on
  a separate branch and worktree, and run before Gate N3, for this track
  only. The implementation plan's §7 order otherwise stands.
- **Learner settings.** Balanced mode ($\beta$ = 1, 1, 1), at item 6's
  $\bar q$. Inference runs in the plugin's **learned** mode (owner,
  2026-10-04): the C++ MLP forward pass (`mlp.{h,cc}`) on weights the
  trainer exports, as the implementation plan §3 specifies. It is built in
  a separate session, on a separate branch and worktree, never in the tree
  a running chain uses.
- **Scores.** A configuration's score is its mean $J_\beta$ over its
  seed-paired repeats, never its best. Comparisons are paired by seed, with
  Student-t intervals (`07`).

**3. A clarification, not a change: what a configuration is.**
- A configuration of $\Theta_s$ is $(K_0, \text{base}, \text{profile})$.
- (Workload, $T$) is the cell. Repeats are seed-paired within a cell.
- $\theta^\star_\beta$ is the configuration with the lowest mean $J_\beta$ in
  the cell, per mode (D-13 §4). C-6's cross-$T$ comparator is its variant
  across $T$ (D-13 §4's cross-$T$ check).

**4. What this entry amends, and what stays open.**
- **D-1:** its paper label (item 13). Its choices stand.
- **D-13 §4:** $\Theta_s$ gains the priced profile, in balanced mode, at the
  formal Gate N2 (item 7).
- **D-15 §3(c), D-20 §2(b) and D-22 §2(c)–(d):** the read prices,
  $c_{open}$ included, are measured per workload, with its keys (items 2
  and 3). D-22's trees, rounds, sessions, reboot and 3% test are otherwise
  unchanged.
- **D-21 §2(c):** the reopen check compares with $1 + \bar s_{open}$; the
  10% tolerance is kept (item 3).
- **D-23:**
  - §7 items 1 to 13 and F are decided here;
  - $\lambda$ and the basis are decided by the calibration (item 1);
  - §4(a)'s job-size sweep gains a garbage-heavy neighbour, for §4(c)'s
    $c_{cr}$ (item 11);
  - §4(f)'s 3% question is settled by item 10;
  - §5's tolerances are set as item 4 says.
- **The implementation plan's §7 order:** changed for the exploratory track
  only (§2).
- **The contract** is amended in place, with this entry as the reason,
  where it records these values: `prices.reopen_time_check.rule`,
  `static_class.profiles` and the workload label.
- **Still open:**
  - D-23 §7 items A to E.
- **Settled here (owner, 2026-10-04):**
  - the inference mode: learned (§2);
  - item 10's "reference compaction traffic": the mean background
    compaction write rate, in MB/s, over the measured phase of the
    reference arms (D-14 §2's five native arms at T = 10 per workload), on
    the binary the price sessions run. The calibration record states its
    value before the sessions;
  - which terms of $J_\beta$ the exploratory runs on the current binary
    price. The current binary has no job-boundary read counters, no
    per-level hidden-step or iterator-block counters, and no scan set-up
    or interference timer. So:
    - priced from counts: the job terms (per kind, bytes read and
      written), the read terms of D-21, the scan's returned and hidden
      steps (global tickers differenced between the host log's stamps),
      the memtable, Put and fixed parts, and space;
    - priced by an approximation: interference, each job charged by D-23's
      formula $\bar q\,\bar\rho_\iota(\kappa^B Y_\iota + \kappa^J t^{\text{job}}_\iota)$,
      with $\bar\rho_\iota$ replaced by the run's mean quiet read cost per
      operation over the measured phase. The current binary has no
      job-boundary read counters, so the job's own window cannot be used
      (the mean-field form, whose total the critique found within 2%);
    - not priced: iterator blocks apart from the step price, and every
      per-level scan attribution.

    The same terms are priced for every configuration, so the ranking is
    like for like. Each run's manifest records which terms are exact,
    approximate or absent.

**Falsification.** This entry fails as a record if any part of it is
changed after the first run it governs has started, exploratory or gate. A
change is a new dated entry. An exploratory result that is pooled with gate
runs, or cited as a gate verdict or a claim, breaks §2.

---

## 2. Gate verdicts as measured

### q̄ recorded, 2026-10-02 (D-13 §1, D-14 §2)

**Dated record, 2026-10-02: $\bar q$ measured (D-14 §2).** Recorded after the $\bar q$ arms and before any $\Theta_s$ run. The values are fixed by the rule, not chosen: the mean of `throughput_ops_per_second` over five `native` arms at $T = 10$, run through `03` with the settle step and the pipeline's default options on `db_bench` `a8e9329d7cdd`, checked by `26_record_qbar.py`.

- `assoc`: $\bar q$ = **69021.5 ops/s** (contract value `69021.50770676274`); session `qbar-assoc`, 58M at 5% load; runs 69256.3, 68470.2, 69396.1, 69129.4, 68855.5; SD 366.9 (0.53%).
- `powerlaw_get95`: $\bar q$ = **120609.3 ops/s** (contract value `120609.31341295298`); session `qbar-powerlaw`, 145M at 2% load; runs 120363.1, 120871.5, 120749.1, 120768.8, 120294.1; SD 261.6 (0.22%).

The contract's `reference_rate.ops_per_second` holds these values, amended in place on 2026-10-02. From here on every Programme 1 arm of these workloads carries its `qbar` fingerprint segment, and `04` prices $\mathcal C_S$ with it.

The owner confirmed on 2026-10-02 that q̄ stays one fixed value per workload, measured at $T = 10$ and used at every $T$ (D-13 §1), after discussing pricing each $T$ at its own native speed: at $T = 2$ that would weigh space about 1.8 times more, within the $2c_s$ sensitivity every result reports, and it would give C-6's cross-$T$ comparison two price scales.

### D-12 scored, 2026-09-23: the first evidential learner run, and the answer is no

**Verdict.** The instrument is right for the first time, and under it the
learned trigger does not improve point reads at write parity in any cell.
Nineteen arms: the smoke retry (`results/learner-smoke-d12-2`) and the 18-arm
matrix (`results/learner-assoc-d12`; `rl` and `unconstrained_rl`, 10M × $T$ =
2/6/10 × seeds 1–3), binary `9b9321b1…`, manifests `9e0faa5e…` / `2e19c6fc…` /
`52079b1e…`, all accepted by the C++ parser. Three repeats is a mechanism
count; no 2% criterion is accepted from it. D-10 to D-12 were uncommitted when
it ran, so they are weaker records than D-1 to D-3. Validated: reward and
evaluator $W$ within 0.10% on every arm; no deep release below φ 0.95;
populated depth equal to the twin's in every arm (9 / 5 / 5), the first learned
arms whose tree did not deepen; stall at most 0.19 s; no multiplier rails.

| cell | arm | $\Delta W$ | $\Delta R$ | $\Delta$seeks | $\Delta S$ |
| --- | --- | ---: | ---: | ---: | ---: |
| $T{=}2$ | `rl` | **+6.64** [+1.04, +12.24] | **+3.03** [−0.58, +6.64] | −1.83 [−2.98, −0.68] | −1.08 |
| | `unconstrained_rl` | +5.74 [−1.87, +13.36] | +2.41 [+0.37, +4.45] | +1.01 | −0.59 |
| $T{=}6$ | `rl` | +9.71 [−11.93, +31.35] | −7.07 [−21.25, +7.12] | −7.48 | +0.08 |
| | `unconstrained_rl` | +12.73 [−18.84, +44.29] | −9.88 [−21.12, +1.35] | −10.53 | −0.02 |
| $T{=}10$ | `rl` | **−0.56** [−2.11, +1.00] | +1.12 [−2.40, +4.64] | +1.66 | +0.07 |
| | `unconstrained_rl` | −1.66 [−4.16, +0.85] | +3.71 [−6.83, +14.25] | +3.96 | +0.05 |

Relative % against same-seed static twins, 95% intervals. For reference,
`prior_only` is +2.75 / +6.32 / +0.45% $W$ and the D-7 learner +24.4 / +42.7 /
+13.1%: D-9 to D-12 removed three quarters or more of the write excess, and
what remains is no better than the prior. $T=2$: worse than RocksDB on both
axes and than the prior on writes (+3.78% [+0.40, +7.17]); write
non-inferiority undecidable. $T=6$: reads bought with writes at a worse rate
than the prior (0.73 against 0.90 on means); paired against the prior both axes
are undecidable, and the wide intervals are one variable, the number of
below-trigger L0 compactions the learner chose (300 / 123 / 101 on `rl` seeds
1–3). $T=10$: native; the first learned arm to pass the write constraint at the
2% margin, reads unmoved.

**Audit correction, same day** (`docs/AUDIT_2026-09-23_D12_AND_PATHWAY_A.md`).
(1) The $T=2$ write excess is not a learned deferral lever: all of it, +0.68 GB
per run, falls in the first 30 s after `rlresume`, before the first gradient
step, where Boltzmann exploration deferred 43–45% of due L3–L5 frames while the
post-load backlog drained; after 30 s the arm writes 0.07 GB less than its
twin. (2) The rising merge survival at L3–L5 is not overwrites absorbed higher
up; those merges combined bulk-load data with bulk-load data. The $T=2$ twins
also ran on 2026-09-20, and the same trigger-4 configuration and seeds write
+2.9% to +4.1% more in later sessions ($T=6$ and $T=10$ show no such drift), so
part of the $T=2$ result is session drift. **D-9's falsification clause is
therefore not established by this run**; the predictions stand as scored. E-5
is 0–1.4% after the first 30 s, and $\lambda_W$ ($T=2$) and
$\lambda_{\text{scan}}$ ($T=10$) end at 0 when measured from the warm-up.

**Predictions as scored.** Pass: 1 (max gap 0.10%), 3, 5, 8 (E-5 0.149–0.171 /
0.061–0.082 / 0.085–0.096), 9, 11 (max 0.19 s). Fail: 2 ($\lambda_{\text{lat}}$
5.08, 8.24, 7.92 on three `unconstrained_rl` arms; every `rl` arm $\le$ 4.86);
4, FAIL / pass ($\lambda_{\text{scan}}$ binds at $T=10$; the second half
passes, $\lambda_W$ ending below its peak there); 6 (+6.64% at $T=2$;
`unconstrained_rl` above `rl` at $T=6$); 7 (+3.03% at $T=2$); 10 ($\lambda_W$
20.95 on one arm); 13 (one of 18). 12 passes on all 18 matrix arms and fails on
the smoke retry. D-12's falsification clause for prediction 2 applies to the
unguarded arms only: their residual $\lambda_{\text{lat}}$ is scan latency
3–11% over the trajectory, not the transient; on the enforced arms the repair
holds. **Pathway D criteria:** D-1 fails at $T=10$ (residual tail 26.9 at L2);
D-2 passes (TD loss rising at $T=6$); D-3 and D-4 fail at $T=2$ and $T=6$
($\lambda_W$ monotone) and pass at $T=10$; D-5: flip passes except L7 at $T=2$
(0.018) and L1 at $T=10$ (0.066); against the prior the learner loses at $T=2$
($W$ +3.78% [+0.40, +7.17]), is undecidable at $T=6$, and is better on $W$ at
$T=10$ (−1.00% [−1.41, −0.59], $R$ equal). The monotone $\lambda_W$ is the
startup transient, not infeasibility: all three flow multipliers inherit it,
and D-12 removed it from latency only. Latency (P0-4): write p99 fails at $T=2$
(+9.67%), confounded by session. E-5 is 6–17× its limit. Against the static
frontier, `rl` at $T=2$ is dominated by its own twin; at $T=10$ `rl` sits on
its twin and both arms are non-dominated, which is not a contribution.

**What this settles.** For this action space, on `Assoc` at 10M, at three
repeats, a learned trigger does **not** beat native RocksDB on reads at write
parity. The finding is about the levers, so no reward repair changes it. D-9
named the remaining route: Pathway A (capacity), which is C++ (Gate 2) and
needs a re-measured hull.

### Oracle parity gate — `Assoc` re-execution, 2026-09-21

**Verdict: PASS.** Ten paired 1M/T=2 `regular`/`oracle` repeats
(`results/oracle-parity-assoc-2`), binary `9b9321b117ad3566…`, objective
`5857ad35…`: `failed_checks: []`, reported `undecided` because three checks
cannot be decided, which the pipeline accepts. Write +0.31% [−0.54, +1.16],
point-read +0.88% [−1.17, +2.92], admission latency p50 127 us against 5000 us.
`sorted_run_seeks_per_scan` is **undecidable, not failed** (10 pairs of 18
needed), the power loss D-1 predicted when `Assoc` cut scans from 32% to 3.5%
of operations; `stall_duration` has no allowance configured. It authorizes the
Hull-0 sweep. **The hull is bound to this binary**: `frontier_analysis.py:196`
refuses to pool across binaries, so a rebuild voids it. Re-scored on the
measured phase on 2026-09-23 (D-11): write +0.68% [−2.56%, +3.92%], verdict
unchanged.

### D-7 scored, 2026-09-22: the first learned arms on `Assoc`, and why they are diagnostic

**Verdict: predictions 2, 4 and 5 pass; 1 and 3 fail; and the run does not test
what D-7 wrote it to test.** Eighteen arms, `rl` and `unconstrained_rl`, 10M ×
$T$ = 2/6/10 × three repeats. (1) $\delta L$ = +2.0 / +1.0 / +0.7, FAIL; (2)
$\lambda_S$ 1.00 against $\lambda_W$ 8.77 / 16.45 / 10.98, PASS; (3) E-5 0.1497
/ 0.0693 / 0.0973, FAIL at 15× / 7× / 10×; (4) flip rate 0.244 / 0.429 / 0.394,
PASS; (5) $\Delta R$ +3.63% / +6.01% at the trigger-2 cells, PASS. $\Delta W$
against `regular` was +6.09% / +13.18% / +4.57% (whole-run; D-11 re-scored it
to +24.4 / +42.7 / +13.1%). **D-6 is confirmed**: $\lambda_S$ never left 1.00.
The run is diagnostic, because `lambda_latency` railed at 100.0 in every arm
(D-8), so E-5's first decidable measurement diagnoses the reward-and-guard
pair, not the guard alone. `unconstrained_rl` produced no scored frames (an
instrumentation gap).

### D-5 control scored, 2026-09-22: the band is a property of the trigger, not of $T$

**Verdict: predictions 1, 2 (range), 3 (range), 4 and 5 pass; both orderings
fail.** Twelve arms at L0 trigger 4, $T$ = 2 and 10. The band is 1.33× / 1.32×
/ 1.33× the twin's L0 jobs at $T$ = 2/6/10, a function of the trigger only, so
D-4's reading stands. $\Delta W$ +2.94% / +2.07% / +2.11% and $\Delta R$ −4.53%
/ −5.69% / −7.07%; zero deep releases below due in 24,400. The orderings fail
because both track populated depth: reads bought per unit of write are 3.35 at
$T=10$, 2.75 at $T=6$ and 1.54 at $T=2$ (a finding of the control, not a
prediction). The upper bound on $\Delta W$ (+3.92% / +3.43% / +2.96%) misses
$\delta_W$ = 2% at every ratio at trigger 4 (at trigger 2 it passes trivially),
so the prior's only lever costs more write than the budget allows. **Latency,
not scored:** get p99 rose at the two new cells only ($T=10$ 31.9 → 88.4, $T=2$
65.7 → 104.3; p50 and p95 untouched; not the guard). The leading explanation is
a cache confound between sessions, so **no latency figure from
`results/band-control` may enter an acceptance table** until same-session
`regular` repeats exist; D-4's latency carries the same caveat. Write re-scored
by D-11.

### Gate 1 — Hull₀ re-measured on `Assoc`, 2026-09-21

**Verdict: C-1 passes, C-2 partial (18 of 26 points, complete at $T{=}6$), the
cross-$T$ hull is measured, E-2 passes. C-3, C-4 and C-6 remain unevaluable
pending `prior_only`; C-5 is open.** 182 `regular` arms, 10M, `assoc-v1`,
buffered I/O, binary `9b9321b1…`, objective `5857ad35…`: L0 trigger 2/4/8/16 ×
level base 8/16/32 MiB at 3 repeats, two top-ups, and cross-$T$ arms at $T$ =
14 and 20. C-1: 11, 7 and 8 hull points of 12. C-2: $T=6$ 7 of 7 (the project's
first complete C-2 pass), $T=2$ 7 of 11, $T=10$ 4 of 8; of the eight failures,
two are duplicate configurations ($T=10$, base 8 MiB: triggers 8 and 16 behave
identically, because the byte branch of L0's score caps the effective trigger),
five need 14–27 repeats, beyond the cap, and one was stopped by decision. The
cross-$T$ hull holds 14 of 38 points ($T=14$ and $T=20$ contribute none), so
the ratio axis is bounded above (P1-14). E-2: 100%. **Comparators** (stage 06,
base 16 MiB): trigger 2 / 4 / 2 at $T$ = 2 / 6 / 10. D-3's prediction 1 is
confirmed and changed the $T=6$ comparator. Evidence:
`results/baseline_sweep/`, `gate1/hull-T{2,6,10}.json`,
`gate1/hull-crossT.json`, `gate1/hull_indistinguishable.tsv`. **Later:** D-11
re-extracted the hulls (C-2 at $T=6$ fails).

### Gate 1 — Hull₀ and space calibration

**Result, 2026-09-12** (uniform workload, binary `deb6753c`, contract v3
`87eaddbc`; superseded as the comparator by the `Assoc` record above). C-1
passes at every ratio (12 / 9 / 9 hull points); cross-$T$ 18 of 38, so the
ratio axis is bounded above. **C-2: fail, deferred**, recorded failed rather
than relaxed, to be revisited before the paper's acceptance table is fixed:
five hull points cannot be separated within the cap, two of them at any $n \le
200$. **C-5 closed 2026-09-13** by a matched per-level capacity calibration:
$s_{\max}$ = 2.0 at the 2% rung at every ratio; it also exposed the
space-metric defect D-3 later fixed. **C-3 and C-6 decided 2026-09-19: both
FAIL** (ten repeats per cell; `unconstrained_prior_only` stood in for
`prior_only` because the guard changes at most 0.09% of frames), the prior
dominated at every ratio and cross-$T$, both paired intervals strictly below
zero; **Finding 2 is withdrawn**. **Gate 1 is complete and not passed:** C-1
passes, C-5 is closed, C-2, C-3 and C-6 are recorded failed.

---

## 3. Pathway E execution record

### E-1 verdict, 2026-09-14: **FAIL, recorded as failed**

The frame-calibrated guard at 10M/T=2, seeds 10001–10003, overrode **0.363 /
0.377 / 0.342** of frames against a predicted ~0.0099, 36×, in shadow
classification only. The whole-tree debt term was diagnosed as the cause (850
of 851 override frames in seed 10001), and it sits on a plateau during the
backlog, so no threshold on it transfers (0.0098 fitted, 0.1804 held out). The
override frames formed one event per run. An amendment to count override events
instead was considered and not taken, because the criterion had already been
seen to fail. Cutting the guard was deferred. **Corrected 2026-09-19** (below):
"one event per run" holds only at $T=2$, and debt is not the dominant term read
per frame.

### All three cells scored, 2026-09-17 — and the mechanism differs in each

Override fractions: T=2 0.342–0.377 (36×), T=6 0.142–0.154 (15×), T=10
0.189–0.193 (19×); the debt term explains T=2 and T=6 but is zero at T=10. A
failing cell is a result, not an error, so the validator must score every cell.
**Zero distortion:** every override landed on a level already due. The holdout
arm `oracle` compacts exactly when due, so the conditional override rate is
identically zero and E-1 measures agreement, not shield influence. With
enforcement on, the guard's forcing terms would also override a policy during
the bulk load, the first 29% of each run, under no criterion; that needs its
own ruling.

### E-5 measured, 2026-09-19: the conditional rate, and two corrections

`unconstrained_prior_only`, ten repeats per cell, guard classifying only.
Marginal rate (E-1) 0.3356 / 0.1567 / 0.1983, failing by 34×, 16× and 20×; a
force changed an action on 0.0009 / 0.0000 / 0.0000 of frames. **E-5 is
satisfied by more than an order of magnitude in every cell; E-1 stays failed.**
The write-side sensor reads write latency, not write amplification. Corrections
to 2026-09-14: override events per run are 1.3 at $T=2$ but 22.2 and 14.9 at
$T=6$ and $T=10$; debt holds on 11% of override frames at $T=2$, and `pressure`
dominates.

---

### Guard protocol on `Assoc`, 2026-09-21/22: D-2 scored, and the calibration audited

Nothing here amends a criterion. Nine `oracle` calibration and nine holdout
arms, 10M, $T$ = 2/6/10, shadow only. Marginal override rate 0.1268 / 0.0760 /
0.1235 against leave-one-out predictions of 0.0106 / 0.0107 / 0.0099. **D-2
prediction 1 (below 0.20): CONFIRMED** (the rate roughly halved from uniform's
0.3356 / 0.1567 / 0.1983; latch removal, workload change and recalibration
landed together, so the latch alone is not claimed). **D-2 prediction 2 (within
3×): FALSIFIED** in every cell (12.0×, 7.1×, 12.5×), so the calibration's
transfer claim is wrong. E-1 fails by 12.7×, 7.6× and 12.4×, reported without a
pass/fail per D-2, which retired it as a decider. **E-5 is not measured**: the
`oracle` holdout cannot measure it. At T=10, `slo_force_due` carries 71% of
overrides; the earlier `l0_slowdown` guess is retracted. **Audit, 2026-09-22:**
the calibration bounds three of the force condition's six terms; even per-level
overrides miss the 1% budget by 3.5× to 10.7×; `due_age` and `pressure` only
grow while a level is due, so they latch by construction and D-2's fix was
partial; and none of the calibration's three score models predicts the measured
rates. **Measure-only, decided 2026-09-22:** the limits are not re-fitted,
since that would fit the outcome; the next step is to log the per-frame guard
state, and any re-fit needs its own dated entry with a falsifiable prediction,
recorded before the run it governs.

---

## 4. Frozen preregistered decisions

*Superseded for Programme 1 by D-13 (2026-09-30). Contracts v1–v3 were deleted
that day on the owner's instruction and are recoverable from commit `5bea343`;
`config/research_objective_contract.json` replaces them. This section stays as
the record of the 2026-09-11 programme, whose contract was v3 (frozen
2026-09-12 with P0, P1, P1b, the build pin and the direct-I/O pin; no run
executed under v1 or v2). Any change required a versioned amendment, never
selected after inspecting a formal gate.*

**P0 (2026-09-10)**

1. **Scan objective.** `sorted_run_seeks_per_scan` at a 2% paired
   non-inferiority margin; `scan_amplification` (at its 1.0 floor) is
   diagnostic only.
2. **Stall test.** Paired 95% interval on `stall_seconds` with a zero
   non-inferiority margin; `stall_events` diagnostic; no `all(delta <= 0)`.
3. **Write margin $\delta_W$** = 2% paired relative non-inferiority. The
   $\beta$ sweep is exposition, not the criterion.
4. **Latency.** Average and p99 get, scan and write latency at a 2% paired
   relative non-inferiority margin; an interval crossing the margin is
   `undecidable`, not failed.
5. **Histograms.** Preserve P50, P95, P99, P100, COUNT, SUM. Write p95 is
   diagnostic; p95 below the mean is valid for the heavy-tailed stalled-write
   distribution, subject to parser and stall-correlation checks.
6. **$S_{\text{bound}}$ as an axis.** Report the W–R–S surface at
   space-regression budgets $\{0, 2, 5, 10\}\%$.
7. **Write-accounting model** M3; state its assumptions whenever quoting the
   $\approx 3.6$ optimum.
8. **Corollary A.4 comparison.** Recompute on $W-1$ and state the denominator.
9. **Static-ladder pin.** `level_compaction_dynamic_level_bytes=false`, with
   the A5 justification.
10. **Delete-rate provenance.** Sweep synthetic $\{0, 3, 6\}\%$ on the `Assoc`
    key distribution, labelled synthetic, never attributed to the published
    `Assoc` mix.

**P1 (2026-09-11)** — recorded before any run of the programme, frozen in
contract v2 on 2026-09-12.

11. **A-0 added** (Gate 0), required for the research track; a $W$ change is
    attributed to expansion only up to the $D_{\text{depth}}$ share.
12. **A-2 is a joint A+D criterion.**
13. **Theorem B.1's floor is $1/S_{\text{flow}}$.** B-2 and D-4 ceilings are
    recomputed from measured $S_{\text{flow}}$ and $L$ at Gate 0.
14. **C-6 added:** non-domination against the hull pooled over $T \in \{2, 6,
    10, 14, 20\}$, required for both tracks; Gate 1 gains `regular` cells at
    $T{=}14$ and $T{=}20$.
15. **Gate 3b runs at the P0-6 ladder** at every rung Gate 1 leaves open, all
    rungs reported, no post-hoc widening; headline rung 2% unless another is
    preregistered before 3b.
16. **Pathway D's hinge target** stays $W_{\text{base}} + \delta_W$ at the
    arm's own $T$; not moved to the hull, nor after a C-6 failure.
17. **Build and measurement environment pinned.** `-march=znver5` through
    RocksDB's `PORTABLE`, GCC 14.1 or Clang 19 minimum, binary SHA-256 in
    `experiment_fingerprint`; `db_bench` and the controller on disjoint cores
    sharing no last-level cache, recorded per arm but not yet in the
    fingerprint.

**P1b (2026-09-12)** — recorded before Gate 1, frozen in contract v3 (SHA-256
`87eaddbcf64529760d91ff139e3c2a4db3787437bfd0b67a93394a4ab8a64bf6`); labelled
P1b so Pathway F keeps P2.

18. **Direct I/O pinned off** for reads and for flush/compaction,
    `compaction_readahead_size` at the 2 MB default, not swept, recorded in the
    fingerprint (`dio`) and `metadata.env`. Reversed from pinned-on on measured
    cost (2,750 ops/s direct against 58,332 buffered, 21×). Latency, runtime
    and stalls are therefore warm-cache and reported as such; amplification and
    the hull are unaffected. A final paper benchmark may enable it, carrying
    `dio1` and its own comparator chain.

**P1c (2026-09-20)** — recorded before any run of the re-executed programme,
after that day's objective-consistency audit; contract v3 amended in place
(`amendments_in_place`, key `2026-09-20`). Nothing was chosen after inspecting
a gate.

19. **The controller is suspended during the bulk load.** `db_bench` runs
    `rlsuspend → filluniquerandom → resetstats → rlresume → mixgraph → …`.
    Under suspension the RL picker delegates to the native leveled picker
    (`ActionReason::kSuspended`), sends no frame and evaluates no safety rule.
    Load-phase compaction events carry `rl_suspended` and form the `load` view
    of the $\eta$ instrument. Metric definitions version
    `trigger-v2-logical-v3`, not poolable with earlier runs. It also said
    `resetstats` zeroes tickers and histograms. *Corrected by D-11
    (2026-09-23): it clears RocksDB's internal stats only, so the evaluator
    reconstructs the measured phase and every `Assoc` arm is re-scored.*
20. **`sorted_run_seeks_per_scan` counts sorted runs:** once per keyed table
    seek (each L0 file) and once per level on `SeekToFirst`/`SeekToLast`; a
    scan crossing a file boundary inside one level opens no further run
    (previously every table seek counted). Defined for `Seek`-initiated scans,
    the only kind `mixgraph` issues.
21. **Overridden actions are relabelled and kept in replay.** The sample keeps
    the action that actually ran;
    `capacity.forced_contraction.include_in_q_replay` becomes `true`, and
    A-Impl-9 inherits the rule. Only the five uncontrolled-attribution cases
    drop an interval. Q-learning is off-policy.
22. **The reward is the constrained objective as written in Pathway D.**
    Point-read probes per Get as a rate; write (windowed, $+\delta_W$), space
    (the rung), latency (average and p99), sorted-run seeks ($+2\%$) and stall
    fraction ($0\%$) only as hinges above the manifest's tuned-baseline
    references, each with a dual-ascended multiplier logged per frame; shaping
    $\Phi = -(\text{L0 files} + \text{non-empty deeper levels})$ in the
    $\gamma^\tau$ form; space a minimand nowhere. The manifest gains
    `write_amplification_reference`, `space_amplification_reference`,
    `sorted_run_seeks_per_scan_reference`, `stall_fraction_reference` and the
    p99 limits; p95 stays the guard's quantile. *Later amended by D-6 (space in
    bytes), D-8 (no p99 in the hinge), D-9 (ratio constraints as signed flows),
    D-10 (latency averages as flows in the telemetry's unit), D-11 (stall term
    off) and D-12 (latency slack since the warm-up); retired for Programme 1 by
    D-13.*
23. **The prior prices reads in absolute runs.** L0 relief is the run count net
    of the output run and, below the trigger, of the runs native RocksDB would
    remove one flush later, with a minimum reduction of two runs; a deep level
    earns no read relief and is charged one exposed probe for output into an
    empty level. The flush size is measured (2 MiB write buffer against a 16
    MiB base). *The empty-level charge was withdrawn by D-4 (2026-09-22) before
    any policy arm ran under it; the L0 rule stands.*
24. **C-3/C-4/C-6 dominators must be inside the policy's space bound.** A hull
    point with $S > S_{\text{policy}}(1 + \text{rung})$ is reported under
    `dominated_by_outside_space_bound` and does not decide. The 2026-09-19
    C-3/C-6 verdicts predate this filter and are superseded by the re-run.
25. **Cadence constants are wall-clock.** Normalizer scales freeze after 30 s
    of controlled time, not 50 frames; the seconds-since-compaction feature
    saturates at 30 s.

**P2 (planned, not frozen)** — Pathway F's phase-class thresholds, episode
length, transition-abruptness settings and the F-detect / F-forecast version
must be frozen in a v3 contract before any F run. They are not part of
Programme 1's contract and do not alter it.
