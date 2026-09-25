# Audit, 2026-09-25: is `docs/PATHWAYS.md` still the right plan?

**Question.** The goal has changed. It is now a trigger-only RL controller that:
- detects workload phases live;
- follows a user priority dial between reads and writes, with space and latency as hard limits;
- beats (a) the Pareto hull of static configurations run over the whole phased workload, and ideally (b) the best static configuration chosen separately for each phase in hindsight.

This audit checks every theory, lemma and criterion in `docs/PATHWAYS.md` (revision 2026-09-11) against three things: that goal, the data measured since, and the two papers the work builds on:
- "How to Grow an LSM-tree" (below: *Grow*);
- RusKey, "Learning to Optimize LSM-trees" (below: *RusKey*).

The contract v3 objective is retired, as decided by the owner on 2026-09-25.

**Inputs.**
- The whole of PATHWAYS.
- Both papers, read in full. The digests with page numbers are in the session scratchpad (`pw/paper_grow.md`, `pw/paper_ruskey.md`).
- A re-analysis of all 351 measured arms (`pw/data_inventory.md`, `pw/frontier.csv`, `pw/gap.py`).
- The 2026-09-23 audit.

Nothing was run on the node.

---

## 0. Bottom line

1. **PATHWAYS is the wrong plan for the new goal and should be replaced, not patched.** It is built around an objective that no longer exists: one steady workload, minimise reads, write within 2% of one tuned point. Of its six pathways:
   - only **F** (phase-aware) and parts of **B** (phases, workload) and **C** (hull comparator) serve the new goal;
   - **A** (capacity expansion) is shown below to be unable to help;
   - **D** and **E** (Lagrangian reward, calibrated guard) are machinery for the retired objective.
2. **Its algebra is mostly right; its premises are what fail.** Every numerical table re-computes (Theorem A.2's table, Corollary A.4's root 3.59, Theorem B.1's ceilings). The failures are in the assumptions:
   - the write model mis-describes L0;
   - "M3" is not in the paper it cites;
   - B.1's second half is a model, not a bound;
   - B.3's "a state-dependent policy can beat the static class" is unproven, and a new lemma below limits it.
3. **Pathway A's capacity expansion cannot lower write amplification in steady state, and does not lower point reads.**
   - Expanding one level by a factor s raises steady-state write cost by c·T·(s−1)²/s (Lemma N-2 below).
   - Under trigger-only control, "expand" and "defer" are the same physical move; A itself admits expansion only changes RocksDB's scoring.
   - Its one real use is **letting the last level grow sideways instead of adding a new level while the database grows**. That is Grow's horizontal growth, and a trigger-only controller already has it: defer the last level.
4. **FLSM (RusKey's design) is a structural change, not a trigger.** RusKey's gains come from letting every level hold several sorted runs (tiering). In leveled RocksDB, levels L1 and below always hold one run, so a trigger cannot create that lever. The only tiered level we have is L0, and L0's run count is exactly the lever our data says matters.
5. **There is room for the new goal, and it sits almost entirely at L0.**
   - A first-order model built from the 182-arm static sweep (§6) puts target (a) at **2–13% of per-operation cost** below the best single static setting.
   - Almost all of it comes from holding more L0 files during write-heavy phases, which cuts write amplification by 32–47% there.
   - Target (b) is reachable in principle, because a trigger controller can imitate any per-phase L0 trigger. Beating it needs levers no static setting has; §5.3 names two.
6. **The 50 ms frames did not throw the tree out of shape.**
   - D-12's damage came from random exploration while the post-load backlog drained.
   - Depth matched the static twin in all 18 arms, and `prior_only`, which uses the same round trip, matched native at T=10.
   - But 50 ms is 4–20× finer than the rate at which level state changes (flush every 180–290 ms, deep levels about once a second). The new design should decide on the time scale the tree actually moves on.
7. **Two measurement problems must be fixed before any claim.**
   - **(i) No data exists at any other read/write mix.** All 351 arms use the `Assoc` mix, so the gain above is a model and not a measurement.
   - **(ii) At 10M the database (about 4–10 GB) lives in page cache on this 60 GB machine.** Across the T=2 frontier, R changes 2.5× while Get latency changes only 20%, and W changes 2× while write latency does not move. Performance claims need the 100M scale, where the database exceeds RAM.

---

## 1. The goal, stated precisely, and one thing it cannot mean

**Per-phase cost.**

$$J_p(\pi)=\pi_w\,x^{put}_p\,\frac{W_p}{W_0}+\pi_r\Big(x^{get}_p\,\frac{R_p}{R_0}+x^{seek}_p\,\frac{Q_p}{Q_0}\Big)$$

- $x_p$ is the phase's operation mix.
- $(\pi_w,\pi_r)$ is the user's priority dial.
- $W, R, Q$ are write amplification, point probes per Get, and sorted-run seeks per scan.
- The subscript-0 values are normalising references.

The workload's own mix already weights writes heavily in a write phase and reads in a read phase. The dial tilts that further. This is the form Grow's self-tuning uses (ζ = w·W + r·R + q·Q, §5), and RusKey's reward is the latency version of it.

**What "lower write amplification in the write phase" can honestly mean.** Write amplification counts bytes written. Deferring a compaction during a write phase does not delete those bytes. It postpones them into the next phase as **compaction debt**, the bytes RocksDB still owes to bring every level back under its target.

> **Lemma N-1 (debt conservation).** For any trigger policy that ends in the same drained state as native RocksDB, whole-run physical write bytes differ from native only through (1) merge survival (how much of a merge's input is dropped as stale), and (2) tree shape: L0 run count per merge and per-level fill. Deferral alone changes neither. So a per-phase write figure can be lowered by moving debt into later phases without improving anything.
>
> *Proof sketch.* Every byte flushed is later rewritten once per level it passes through, times the overlap it meets there. Those are shape terms, or it is dropped when a newer version meets it in a merge, a survival term. Timing that changes neither only moves when the bytes are written.

Consequences:
- A per-phase write claim must use **debt-adjusted** write amplification, $\tilde W_p=(B_p+\hat c\,\Delta D_p)/U_p$. Here $B_p$ is bytes written in the phase, $\Delta D_p$ the change in debt, and $U_p$ user bytes. The same point was made informally in PATHWAYS §F's objective table ("W and S are whole-run bounds, never per-phase targets").
- **Moving debt is still worth something for performance.** Compaction work shifted from a busy write phase into a quiet read phase can raise write-phase throughput and cut its tail latency. The SILK / bLSM line of work does exactly this. But that is a latency and throughput result, and it only shows when the device is the bottleneck. That is not the case at 10M in page cache (§0 point 7).

**Genuine per-phase write gains exist, and they are shape gains.** The measured frontier shows W falling about 2× as the L0 trigger rises from 2 to 16 (T=2, base 32 MiB: 10.94 → 5.24). That is not debt shifting. An L0→L1 merge rewrites the overlapping part of L1 once per batch of k L0 files, so its cost per byte falls roughly as $1+C_1/(kM)$:
- $C_1$ is L1's target size, 16 MiB;
- $M$ is the memtable (write buffer) size, 2 MiB;
- $k$ is the L0 file count at merge time.

Holding more L0 files in a write phase is a real, repeatable saving. Its price is more point-read probes (each L0 file is one more place to look) and more scan seeks.

---

## 2. What a trigger-only controller can physically change

**Can change:**
- **L0 run count k(t).** Deferring L0 compaction lets files pile up. That is tiering at L0, up to the slowdown trigger (20 here), beyond which RocksDB throttles writes.
- **Per-level fill κᵢ(t) in both directions.**
  - *Hold* a level above its target by deferring.
  - *Shrink* it below target through the native optional ("forced-level") compaction. D-9 masked that off; the clean slate may reopen it.
- **Timing relative to stale data.** Compacting after overwrites have piled up lets a merge drop more (lower merge survival). Grow §5.3's skew rule is this.
- **Draining upper levels in a read-only stretch.** Compacting L0 and upper levels down reduces the number of places a read must check. No static setting ever does this.

**Cannot change:**
- the size ratio T;
- the number of runs at L1 and below (always one, the one-run-per-level assumption A4);
- which files are merged (RocksDB's `CompactionPri`).

> **Lemma N-3 (reachability).** At a fixed T, any schedule that switches the L0 trigger and the level base size at phase boundaries can be imitated by a trigger-only controller, up to one merge's granularity.
> - A trigger k is imitated by "defer L0 until k files, then allow".
> - A larger base is imitated by holding the level to fill s > 1.
> - A smaller base is imitated by optional early compaction.
>
> So the per-phase hindsight oracle restricted to the same T, target (b) at fixed T, lies inside the controller's reachable set. Detection delay and transition cost are the only unavoidable losses against it.

**Target (b) with T allowed to change per phase is not reachable by any runtime controller**, because changing T means rebuilding the tree. Report it as a ceiling only.

---

## 3. Every PATHWAYS item, judged

Verdicts:
- **Keep**: correct and still relevant.
- **Restate**: correct core, wrong frame.
- **Drop**: wrong or irrelevant to the new goal.
- **Stale**: numbers from the retired uniform workload or an old evaluator.

### Standing assumptions and notation

| Item | Verdict | Reason |
|---|---|---|
| A1–A4 | Keep | Correct RocksDB leveled semantics. A3′ (L0's byte-driven branch) matters: it capped triggers 8 and 16 at base 8 MiB into the same configuration (history 14.16). |
| A5, pin `dynamic_level_bytes=false` | **Restate** | Pinned only so capacity scales would work. With A dropped, the static class should also include RocksDB's **default** (dynamic level sizing on), or a reviewer will call the baseline weak. |
| M3 write model, $c=\tfrac12$ constant (P0-7) | **Drop as stated** | Grow does not define it. The only source is one sentence on p.13 quoting (T+1)/2 for one partial compaction, citing Lim et al. On the same page a merge into a full level costs T, so c is about 1 there. c must be **measured per level** on our picker. |
| Notation η, φ, κ, S_flow | Keep | Useful. Survival η is now measured on `Assoc`, steady window: global 0.87–0.93; at T=2, L1 0.83, L2 0.73, L3 0.71, L5 0.90, L6 0.96. |

### Pathway A: capacity expansion

| Item | Verdict | Reason |
|---|---|---|
| Purpose ("defer without the cascade") | Drop | D-12 had no cascade: depth equalled the twin's in all 18 arms. |
| Theorem A.1, depth invariance | Restate, narrow | True, but it is true because a level that is not due is not eligible, which is A3 restated. The burst size is (κ−1)C, not κC (2026-09-23 audit). Relevant only when the database **grows**, where holding the last level avoids opening a new one. That is Grow's horizontal growth, and a trigger-only controller does it by deferring the last level. |
| Theorem A.2 (i)–(ii) | Keep | Telescoping and the survival-weighted write sum are correct. |
| Theorem A.2 (iii)–(iv) | Restate | Algebra verified. But the optimum $f_i\propto\eta^{-i}$ needs levels *smaller* than nominal, which s ≥ 1 forbids. And the L0→L1 term is not a capacity ratio: its cost is set by k (§1). The model omits the largest adjustable term in W. |
| Corollary A.3, level removal never profitable | Restate | Correct for W only (algebra checked). It ignores that removing a level saves one probe per Get. The measured s = 2.0 arm at T=10 dropped a level and sits on the frontier (W 10.91, R 3.73). |
| Corollary A.4, write-optimal ratio | Keep as static design | Root verified (3.59 ln 3.59 = 4.589). It depends on constant c (see M3). T is static, so this belongs to the baseline, not the controller. |
| **New: Lemma N-2, expansion penalty** | — | See §4. Expansion strictly raises steady-state W. |
| A-0 decomposition | Stale | Measured on the uniform workload under the old evaluator. |
| A-Impl-1…10, schema v3, three-action protocol | Drop | About 1,200–1,800 lines plus 60 node-hours of re-measurement, for a lever with no steady-state gain. |
| A-1a/b, A-2…A-5 | Drop | They test depth flattening, which is no longer the question. |

### Pathway B: workload realism

| Item | Verdict | Reason |
|---|---|---|
| B1, skew (`Assoc`) | Keep, done | 100% of measured-phase Puts overwrite loaded keys; the hottest 1% of keys take 57% of Puts. |
| B2, in-process phase patch | **Keep: now the critical path** | Every claim needs it. The `QueryDecider::Initiate` append trap stands. |
| B3, deletes | Keep, secondary | Tombstones are a real write-phase garbage class. Not needed for the first claim. |
| Theorem B.1, elision bound | Restate | First half ($\eta\ge 1/S_{flow}$ pooled) holds if every written byte enters at least one merge. PATHWAYS' proof argues it loosely; the clean argument is "dropped bytes ≤ stale bytes written". The second half substitutes the pooled survival as a per-level constant, so it is a model, not a bound. The table is uniform-workload (stale), and D-12 showed the ceiling is far from reachable because deep levels have almost nothing left to drop. |
| Theorem B.2, expansion budget | Drop | Self-declared falsified as an absolute bound; only needed for A. |
| Proposition B.3, exploration cost | Keep. **Drop** the "positive result" paragraph | The proposition is correct. The claim that state-dependent control "can strictly beat every member of Θ" on a steady workload is unproven, and Lemma N-4 (§5.1) says time-sharing cannot. |
| Definition of 𝒢 (phase-adaptivity gap) | **Keep: now central** | Must be defined over knobs that can be switched at runtime at a fixed T; cross-T only as a ceiling. It is target (a) − target (b). |
| Corollary B.4 | Keep | Correct. |

### Pathway C: frontier comparator

| Item | Verdict | Reason |
|---|---|---|
| Proposition C.1, Corollaries C.2 and C.3 | Keep | Correct and essential. |
| Two hulls (Hull₀ / Hull_s) | Restate | Replace with the two goal targets: (a) the static hull over the whole phased run (cross-T), and (b) the per-phase switching oracle at fixed T. Add the **convex envelope** as the practical static bar (Lemma N-4). |
| Grid (trigger × base × T) | Keep, extend | Add dynamic level sizing (see A5). |
| C-1…C-6 as scored | Stale | Scored against the retired objective on one mix. |

### Pathway D: constrained objective

| Item | Verdict | Reason |
|---|---|---|
| Objective (min R subject to W within 2%) | Drop | Replaced by §1's per-phase cost with the dial. |
| Proposition D.1, shaping with $\gamma^\tau$ | Keep | Correct (Ng et al. 1999, extended to variable durations). |
| Proposition D.2 | Keep | Correct, trivial. |
| Proposition D.3, two-timescale convergence | Drop | The code runs fast multipliers (2026-09-23 audit), and a 160 s run cannot separate timescales. With a user-supplied dial the weights are **given**, not learned, so no multipliers are needed for the objective. |
| λ-divergence diagnostic, criterion D-4 | Drop | It can never fire: native RocksDB is always feasible. |
| D-1…D-5 | Drop | Tied to the DQN and multipliers. |
| Implementation items and status log | Historical | The status paragraphs belong in history, not in a forward plan. |

### Pathway E: shield and guard

| Item | Verdict | Reason |
|---|---|---|
| Proposition E.1, Corollary E.2 | Keep | Correct mixture algebra. The lesson stands: report overrides conditioned on the policy's choice. |
| Calibrated percentile guard (E-1, E-2, E-5) | Drop | Six rounds of calibration never reached its own 1% target; limits fitted on the post-load backlog. Replace with a few **hard, physical limits**: L0 slowdown and stop triggers, a space ceiling, a pending-bytes ceiling, and the latency SLO. |

### Pathway F: dynamic SLO

| Item | Verdict | Reason |
|---|---|---|
| Purpose | **Keep: becomes the programme** | It is the new goal. |
| Objective table (W, S whole-run; phase-local latency and R) | Keep in spirit | Consistent with Lemma N-1. Extend with debt-adjusted $\tilde W_p$ and the L0 shape gain. |
| "Layer above a DQN that switches manifests" | Restate | Keep "slow layer chooses what to pursue", but the lower layer should be a deterministic trigger that enforces the chosen per-level thresholds, not a 50 ms DQN (§7). Still trigger-only: the only output is when each level compacts. |
| F-forecast / F-detect split, leak guard | Keep | The owner chose "detect live", so this is F-detect. |
| Proposition F.1 (telescoping), F.2 (lead time) | Keep | Correct. F.2's Δ_shape is measurable: settling after the load takes 7–23 s median. |
| Scale (50M dev, 500M confirm) | Restate | The owner caps realistic workloads at 100M. |
| F-1…F-7 | Restate | They carry over once 𝒢 is defined at fixed T. |

### Execution order, budget, global acceptance

All **drop**. Gates 3a–6 and the two-track acceptance rule are built on A and D.

---

## 4. The capacity scheme, checked directly

The owner asked whether "temporarily enlarge a level in a write-heavy phase, then decay back exponentially" reduces the metrics that matter.

> **Lemma N-2 (expansion penalty).** Let levels $i-1, i, i+1$ have capacities $C_{i-1}, sC_i, C_{i+1}$, with per-level cost $c\cdot$fanout (A2). Expanding level $i$ by $s$ multiplies the fanout into it by $s$ and divides the fanout out of it by $s$:
> $$\Delta W = c\big[(s-1)f_{i-1}+(1/s-1)f_i\big].$$
> With uniform $f=T$: $\Delta W=c\,T\,(s-1)^2/s>0$ for all $s\ne1$. With survival weights, the minimiser is $s^\star=\sqrt{\eta\, f_i/f_{i-1}}<1$, so expansion is worse still.
>
> *Proof.* Direct substitution; the minimum of $(s-1)a+(1/s-1)b$ over $s>0$ is at $s=\sqrt{b/a}$.

**Point reads.** Enlarging a level at L1 or below adds no sorted run (A4), so R does not change unless the enlargement removes a whole level.

**Measured, 27 static capacity arms (trigger 4, base 16 MiB).**

| T | s = 1.5: W / R vs s = 1 | s = 2.0: W / R vs s = 1 |
|---|---|---|
| 2 | +0.5% / −6.3% | +4.0% / −12.9% (a level removed) |
| 6 | +8.4% / +3.1% | +30% / +1.9% |
| 10 | +5.2% / −3.4% | +2.4% / −6.2% (a level removed) |

- Expansion never lowers W.
- Where R falls, a level disappeared, which the base-size knob already offers statically.

**The decay schedule.** Under trigger-only control, letting s decay back to 1 means releasing the held data gradually: paying the debt in instalments. By Lemma N-1 that changes *when* bytes are written, not how many. It could smooth write-phase tail latency on a device-bound system, and that is the only defensible claim for it.

**Verdict.** Drop Pathway A as a mechanism. Keep one narrow idea: **in a phase where the database grows, hold the last level (grow sideways) instead of opening a new level**, as Grow's horizontal scheme and Vertiorizon do. It needs a growing database; the current workload has zero net growth, since every Put overwrites.

---

## 5. How much room is there?

### 5.1 On a steady workload: little

> **Lemma N-4 (time-sharing cannot beat the convex envelope).** Suppose a policy's long-run (W, R) is a time-weighted average of static operating points. Then it lies on or above the lower convex envelope of the static frontier. Beating the envelope needs effects that are not averages:
> - lower survival from well-timed merges (elision);
> - avoided stalls;
> - consolidation while writes are near zero.
>
> *Proof.* Convex combinations of points lie in their convex hull.

Measured: the per-T (W, R) frontiers are nearly convex. 5 of 8 points are on the envelope at T=2, 6 of 7 at T=6, 5 of 8 at T=10. The largest dent is T=2 trigger 16 / base 8 MiB, 13.9% above the chord. Two consequences:
- **The static bar a reviewer should accept is the convex envelope.** Time-sharing between two static settings is trivial to build.
- On a steady `Assoc` workload the elision term is near zero at deep levels (D-12), so a steady-state gain is unlikely. That matches the 2026-09-23 finding of "no room".

### 5.2 On a phased workload: a first-order estimate

**Assumption.** A static setting's (W, R, seeks) are tree properties that do not depend on the mix. Unproven, and the reason B2 runs come first.

**Setup.**
- Three equal phases: write-heavy (put/get/seek 0.75/0.15/0.10), mixed `Assoc` (0.159/0.806/0.035), read-heavy (0.05/0.85/0.10).
- §1's cost, normalised to the stage-06 comparator.
- Script: `pw/gap.py`.

| Dial (write : read) | T | Best single static | Per-phase switching | 𝒢, target (a) gain | L0-trigger-only switching |
|---|---|---|---|---|---|
| 1 : 1 | 2 | trigger 2 / 32 MiB | 8/32 → 2/32 → 2/32 | **11.0%** | 11.0% |
| 1 : 1 | 6 | 2 / 8 | 8/16 → 2/8 → 2/32 | **9.2%** | 4.7% |
| 1 : 1 | 10 | 2 / 16 | 4/8 → 2/16 → 2/16 | **5.5%** | 4.1% |
| 2 : 1 | 2 / 6 / 10 | — | — | 9.5 / 12.9 / 11.1% | — |
| 1 : 2 | 2 / 6 / 10 | — | — | 5.2 / 5.2 / 1.8% | — |

- In the write phase, switching lowers W by 32–47% against the best single static setting, at a cost of +30–80% in R.
- With the read-priority dial, the read phases barely move, because the single static setting is already read-optimised.
- **Nearly all of 𝒢 is the L0 trigger.** That is the knob a trigger-only controller controls most directly.

### 5.3 Beating target (b)

By Lemma N-3 the controller can at best match the per-phase oracle in steady state, minus:
- **detection lag**: a loss of lag / phase length. At 100M with phases of minutes and a lag of a few seconds, that is 1–2%;
- **transition cost**: reshaping takes 7–23 s after a heavy write burst (the post-load settling times).

It can beat (b) only through levers no static setting has:
1. **Read-phase consolidation.** When writes nearly stop, drain L0 and upper levels once. The break-even point is (Gets in phase) × (probes saved) × (cost per probe) > (bytes rewritten) × (cost per byte). The payoff is large for scans, whose seek count grows with the number of runs (T=2: 5.3 → 14.8 seeks per scan along the frontier, scan latency 373 → 482 µs). It is small for point Gets behind Bloom filters: at 10 bits per key a rejected probe costs CPU, not I/O.
2. **Garbage-timed L1 merges under skew** (Grow's skew rule, Eq. 6). Survival is lowest at L2–L3 (0.71–0.73), so the rule is most likely to find something to drop there.

Both are untested.

---

## 6. RusKey and FLSM

- **Where RusKey's gain comes from.** Its lever is K, the maximum sorted runs per level (1 = leveling, 10 = tiering). With uniform Bloom filters, RL tunes one number, L1's K, and copies it to every level.
- **How it decides.** One decision per 50k operations; a new phase is simply re-learned; no phase detector.
- **How it was evaluated.**
  - Baselines: only static K ∈ {1, 5, 10} and Lazy-Leveling. No static hull and no per-phase oracle.
  - "Up to 4×" is against the worst static setting per phase, with transients excluded.
  - It never measures space, write amplification, p99 latency or stalls, and each experiment is a single run.
- **Verdict.** Its gains come from the structural lever plus per-phase switching. The RL is a slow search over about 10 options; its own Eq. 5 gives the best K in closed form.
- **For us:**
  - A trigger cannot create K > 1 below L0.
  - L0 *is* our K. Our measured 𝒢 is exactly RusKey's lever at the one level we have it.
  - Adding FLSM would break the trigger-only rule. It should be a separate, later pathway, taken only if the L0-only gain turns out too small once measured.
  - Two things are worth copying: decisions on a slow time scale with few options, and a closed-form prior.
  - Errors found in the paper: Eq. 3 is missing a factor x; the tiering write cost is given as T/2K on p.9 but T/K on p.13; the Monkey direction is reversed on p.13.
- **This is also a contribution opening.** A comparison against the static hull *and* the per-phase oracle is stronger than any baseline in RusKey or Grow. Grow's "Pareto frontier" is an envelope of a few static points from single runs.

---

## 7. The 50 ms frames

| Claim | Evidence | Verdict |
|---|---|---|
| The 50 ms delay put the tree out of shape and ruined the numbers | Depth equalled the static twin in all 18 D-12 arms. `prior_only` with the same round trip matched native at T=10. The T=2 excess lies entirely in the first 30 s and is explained by exploration during the backlog drain (2026-09-23 audit). | **Not supported.** |
| The 50 ms interval is a poor design | Flushes every 180–290 ms. Deep levels at T=2 change about once a second. After the first 30 s only 31–50% of frames see any change, so level state changes every 4–20 frames. 19–39% of answers were rejected as stale. | **Supported.** |

**What the new design should do.**
- Make the RL decision on the scale of phases and flushes: seconds, or per N operations as RusKey does.
- Have it choose a few per-level thresholds (target L0 run count, per-level fill), guided by the dial and the detected phase.
- Let a deterministic in-process trigger enforce those thresholds on every flush and compaction event.

That is still trigger-only: the only output is when each level compacts, and RocksDB picks every file.

---

## 8. Measurement problems the new plan must fix first

1. **No data at other mixes.** B2 plus a static sweep at 2–3 mixes are prerequisites for everything in §5.
2. **Scale and cache.** At 10M the database sits in page cache:
   - across the T=2 frontier, R changes 2.5× while Get latency changes 20% (7.3 → 9.2 µs);
   - W changes 2× while write latency stays at 1.6–1.9 µs.

   Only amplification (byte and probe counts) is meaningful at 10M. Latency and throughput claims need about 100M (about 100 GB, above the 60 GB of RAM) or direct I/O.
3. **The bulk load is a write phase.** Suspending the controller during the load and then measuring from `rlresume` put a 10–30 s transient into every metric (D-10 to D-12). In a phased design the load should be phase 1, controlled and measured like any other.
4. **Session drift.** T=2 drifted 3–4% in W between sessions. Every comparison needs same-session static twins.
5. **Instruments to keep.** The measured-phase evaluator (D-11), survival η per level, φ at release, and the frontier tools all carry over.

---

## 9. What goes into the new Pathways document

**Carry over:** A1–A4, A.2(i)–(ii), A.4 (static), B1–B3, 𝒢 and B.4, C.1–C.3, D.1, E.1–E.2, F's objective split, F.1, F.2, and the instruments in §8.5.

**New results to prove:**
- N-1, debt conservation (full proof);
- N-2, expansion penalty;
- N-3, reachability of the fixed-T per-phase oracle;
- N-4, the convex-envelope bar;
- an **L0 amortization lemma**: W and R as functions of k, fitted to the measured frontier with a measured c;
- a **detection-lag bound** against target (b);
- a **consolidation break-even lemma**, especially for scans.

**Drop:** Pathway A's mechanism, D's Lagrangian machinery, E's calibrated guard, the Gate 0–6 plan, the two-track acceptance rule.

**Open for the owner:**
- a later FLSM pathway (structural);
- whether dynamic level sizing joins the static class.

---

## 10. Next steps

1. **Owner:** approve this audit's verdicts. In particular: drop Pathway A's mechanism, and make target (a) the headline with target (b) reported.
2. **Draft** `docs/PATHWAYS_V2.md` with the lemmas and proofs in §9, figures from the existing sweep, and a preregistration entry before any run.
3. **First node work, no learner:**
   - the B2 phase patch;
   - a static sweep at write-heavy and read-heavy mixes (trigger × base at one T, 3 repeats);
   - a measured 𝒢 on a 2-phase schedule.

   If the measured 𝒢 is near zero, the programme stops there with a clean negative result, before any controller is built.
