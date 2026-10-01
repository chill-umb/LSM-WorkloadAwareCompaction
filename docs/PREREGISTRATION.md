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

---

## 2. Gate verdicts as measured

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
