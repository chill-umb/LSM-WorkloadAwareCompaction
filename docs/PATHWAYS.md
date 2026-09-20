# Improvement Pathways: Specification, Proofs, and Acceptance Criteria

**Revision:** 2026-09-11. Supersedes all earlier revisions; the project record
(`PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md`) carries the history of what was
corrected and why.

**Target:** EDBT research track (12 pages, ACM double-column) or Experiments &
Analysis track (same length), depending on the outcome of Gate 3b.

**Scope:** Pathways A–E and Gates 0–6 are Programme 1 (the current paper).
Pathway F and Gates F-0 to F-4 are Programme 2 (the dynamic-SLO follow-on),
specified now so Programme 1's instruments are built in a reusable form.

**Status:** design specification. Every theorem is proved from the stated
assumptions. Every acceptance criterion is decidable from measurements the
pipeline already produces or from instrumentation named in the pathway.
Gate 0 is complete (2026-09-11; results in the Gate 0 section, Theorem B.1's
table, and Pathway A's A-0 table). Gates 1–6 have not been executed.

**RocksDB version.** Every source-level claim, line number, default and option
semantic below is version-dependent. Pinned 2026-09-12:
`ROCKSDB_COMMIT = 81d2742bf05883fa9978a9e984e32f709409444f`, parent
`7ea2d73655025332855864f0b8d6d2dbdad3f336`, which is the base the contract
pins and which this commit preserves. It carries the Gate 0 instrumentation.
Re-verify each cited line number against the pinned commit.

**Build and I/O.** The measured binary is compiled `-march=znver5` via
RocksDB's `PORTABLE` cache variable, not `PORTABLE=0`/`-march=native`, and its
SHA-256 is part of `experiment_fingerprint`. Direct I/O is pinned **off** on
measured cost, and is fingerprinted too. See the contract's build section and
A-Impl-2.

**Setup.** `EXPERIMENTAL_SETUP.md` carries the full experimental configuration
in one place and is the source text for the paper's setup section.

---

## 0. Notation

| Symbol | Meaning | Project equivalent |
| --- | --- | --- |
| $N$ | live logical entries (unique keys currently valid) | `live_logical_bytes` / entry size |
| $N_{res}$ | resident entries (live + obsolete, settled) | `settled_sst_bytes` / entry size |
| $N_{\text{written}}$ | user logical entries written over the run | `user_logical_bytes_written` / entry size |
| $M$ | write buffer capacity | `write_buffer_size` |
| $T$ | size ratio between adjacent levels | `max_bytes_for_level_multiplier`, `-T` |
| $L$ | number of populated levels | `levelstats` populated depth |
| $C_i = M T^i$ | nominal capacity of level $i$ | derived from `max_bytes_for_level_base` |
| $s_i \ge 1$ | **capacity expansion scale** at level $i$ | `capacity_scale_[i]`, Pathway A |
| $\hat C_i = s_i C_i$ | effective capacity of level $i$ | — |
| $f_i = \hat C_{i+1} / \hat C_i$ | effective fanout from level $i$ to $i+1$ | — |
| $\phi_i$ | occupancy fraction of level $i$, occupancy / $C_i$ | `levelstats` |
| $\kappa_i \ge 1$ | **deferral factor**: occupancy reached, as a multiple of $C_i$, before release | derivable from per-level compact rates |
| $\eta_i \in (0,1]$ | **merge survival**: compaction output bytes / input bytes at level $i$, trivial moves excluded | Gate 0 instrument |
| $W$ | write amplification | §2.3 definition |
| $R$ | logical point-read amplification | §2.3 definition |
| $S = N_{res}/N$ | space amplification (settled snapshot) | §2.3 definition |
| $g = 1 - 1/S$ | resident garbage fraction (snapshot) | derived |
| $S_{\text{flow}} = N_{\text{written}}/N$ | **flow space ratio**; $S_{\text{flow}} \ge S$ | $W$ denominator / $S$ denominator, both already recorded |
| $g_{\text{flow}} = 1 - 1/S_{\text{flow}}$ | **cumulative garbage fraction**: share of user-written bytes obsolete by run end. This, not $g$, bounds elision | derived |
| $D_{\text{depth}}$ | write bytes an arm wrote at levels the paired `regular` run never populated | per-level `compaction_bytes_written`, A-0 |
| $D_{\text{eager}}$ | write-byte excess over the paired `regular` run at levels both populated | per-level `compaction_bytes_written`, A-0 |
| $\Theta$ | class of static RocksDB configurations reachable by the sweep | §3.1 sweep space |
| $\theta^\star$ | cost-minimising static configuration | current `regular` arm |
| $\pi$ | the learned controller | `rl` arm |
| $J(\cdot)$ | scalar cost under the chosen objective | — |
| $c$ | write-accounting convention constant | see below |

**Write-accounting models.** Three conventions exist in the literature and
they do not share optima:

- **M1** $w_i = c f_i$, $c = 1$ — plain per-level fanout (Monkey, Dostoevsky).
- **M2** $w_i = c f_i$, $c = \tfrac12$ — averaged partial compaction (HotRAP).
- **M3** $w_i = c(f_i + 1)$, $c = \tfrac12$ — partial compaction with the
  promoted run counted ("How to Grow an LSM-tree", SIGMOD 2025).

M1 and M2 differ by a constant and share every optimum; M3 does not
(Corollary A.4). The paper uses **M3** (P0-7) and states it in §2.3.

### Standing assumptions

- **A1.** Effective fanout is $f_i = \hat C_{i+1}/\hat C_i$.
- **A2.** Bytes physically written while operating level $i$, per byte
  *entering* level $i$, are $w_i = c\,f_i$. Survival is applied to the volume
  reaching each level: $v_i = v_0\prod_{j<i}\eta_j$. The conservation identity
  in Theorem A.2 does not depend on A2; only the optimality half does. State
  this in the paper.
- **A3.** A level whose occupancy exceeds its effective capacity becomes
  **eligible** for outward compaction (score $\ge 1$). This is RocksDB's leveled
  score rule for levels $\ge 1$. It is not a scheduling law: RocksDB ranks
  eligible levels by score and admission depends on free compaction slots and
  `NeedsCompaction`. Every theorem uses only eligibility.
- **A3′.** For L0 under `kCompactionStyleLevel` with `num_levels() > 1`, the
  score is $\max(\text{runs}/\texttt{level0\_file\_num\_compaction\_trigger},\;
  \text{bytes}/\texttt{max\_bytes\_for\_level\_base})$. L0 has a byte-sensitive
  branch; A3 does not apply to it unmodified.
- **A4.** In leveled RocksDB, levels $1..L$ hold exactly one sorted run each;
  only L0 holds multiple runs. Exceptions to note in the paper: intra-L0
  compaction, subcompactions, trivial moves, SST ingestion, leveled-N. Under
  `level_compaction_dynamic_level_bytes` the invariant holds for non-empty
  levels but upper levels may be deliberately kept empty.
- **A5.** All experiments pin `level_compaction_dynamic_level_bytes = false`.
  This deviates from the RocksDB default since v8.4 and must be declared in the
  paper with the justification in A-Impl-2.

---

## Pathway A — Capacity Co-Design

### Purpose

Give the controller authority over level capacity, not only compaction timing.
This is the half of the original plan — "if compactions are deferred, the level
size could be temporarily increased" — that was never implemented.

### Description

Extend the per-level action set from two actions to three:

```
0 = defer, capacity unchanged
1 = compact
2 = defer and expand this level's effective capacity
```

Action 2 sets $s_i \leftarrow \min(s_i \cdot \alpha, s_{\max})$ for a fixed
step $\alpha$ (start at $1.25$). All $s_i$ decay geometrically toward $1.0$
with time constant $\tau$ when not re-expanded, preventing ratchet.

**$s_0 \equiv 1$.** L0's runs overlap, so "capacity" at L0 does not have the
single-sorted-run meaning the theory uses (A4), and Theorem A.1's absorption
argument does not apply. Operationally, the L0 byte term reads
`max_bytes_for_level_base` — the option that also sets L1's target — so any
mechanism scaling it would couple L0 and L1. The scale vector is defined on
levels $1..L-1$ and must not leak into L0's byte term (A-Impl-1).

**What action 2 does that action 0 does not.** Under trigger-only v2 the
controller's gate decides whether level $i$ compacts, so in the instant they are
taken, actions 0 and 2 are the same physical move. Expansion's entire content is
downstream, through `target_bytes`:

1. **Due-ness.** At occupancy $\kappa C_i$, action 0 leaves score $\kappa > 1$
   (due, held only by the gate). Action 2 leaves score $\kappa/s_i \le 1$ (not
   due).
2. **The safety envelope.** §6.4's safeguards force a due level open on due-age,
   held excess pressure, instantaneous score, or normalised debt. Under action 0
   those clocks run and fire; under action 2 they are rescaled and do not.
3. **Debt and stall accounting.** Pending compaction bytes are computed against
   target; expansion reduces measured debt.
4. **The prior's input.** §6.2's per-level urgency is bytes / target bytes, so
   expansion changes what the analytic prior sees.

Pathway A is therefore **authority over the debt and safety envelope, not over
physical capacity**. State in the paper that expansion is score-mediated, or a
RocksDB-literate reviewer will conclude action 2 is a no-op. Expansion and the
shield write to the same envelope, so E-3's spanning requirement covers
contraction of $s_i$ as well as gate forcing.

By A4, deferring at a deep level does not create additional sorted runs — it
pushes the level past its target, which cascades data downward. §10.7 measured
that cascade: one to three extra populated levels, and with them the read
regression of Finding 3. Deferral bought space ($-29.0/-9.9/-4.5\%$ against the
prior) but through depth, and depth is what the read criterion pays for.
Expansion lets the controller defer without the cascade.

**What expansion does not do.** It does not address the prior's top-of-tree
eagerness, which A-0 shows is the dominant component of the write excess in
every cell (see "What A delivers").

### Theory

**Theorem A.1 (Depth invariance).**
*Let $\phi_i(t) = \text{occupancy}_i(t)/C_i$. If the controller defers at level
$i$ over $[t_0,t_1]$ and maintains $s_i(t) \ge \phi_i(t)$ throughout, then level
$i$ is not eligible for outward compaction on $[t_0,t_1]$, no burst is released,
and populated depth $L$ is unchanged relative to the eager policy over that
interval.*

*Proof.* Without expansion, occupancy $\kappa_i C_i$ against capacity $C_i$
gives score $\kappa_i > 1$; by A3 the level is eligible and once admitted
releases $\kappa_i C_i$ bytes downstream. A released burst is absorbed by the
*headroom* below, not by nominal capacity, so it is contained at the smallest
$m$ with

$$\kappa_i C_i \;\le\; \sum_{j=1}^{m} (1 - \phi_{i+j})\, \hat C_{i+j},
\qquad \delta L = \max(0,\; m - 1).$$

With $s_i(t) \ge \phi_i(t)$, score $\le 1$; by A3 the level is not eligible; the
accumulation is absorbed horizontally at level $i$; $m = 0$; $L$ unchanged.
$\blacksquare$

Three points about the statement. (1) Downstream inflow is *deferred*, not
equal to the eager policy's — over the whole run it is also reduced by whatever
the deferred merge elides. (2) The hypothesis is a condition on the whole
interval: the decay schedule (A-Impl-8) can drive $s_i$ below $\phi_i$ while the
level is still over-full, restoring eligibility and firing the burst late. Depth
invariance is a property of *sustained* expansion; A-4 tests the conjunction.
(3) The headroom form requires per-level occupancy $\phi_j$ at the moment of
release, which existing logs do not retain. **Theorem A.1 is untested**; Gate 3a
is its first test and must log $\phi_j$ at release time (Gate 0).

---

**Theorem A.2 (Fanout conservation and the survival-weighted optimum).**
*Let $\rho = \hat C_L / \hat C_0$.*

*(i) Conservation.* $\prod_{i=0}^{L-1} f_i = \rho$ independently of the interior
profile $\{s_1,\dots,s_{L-1}\}$.

*(ii) Write amplification.* Under A2 with per-level survival $\eta_j$,
$$W \;=\; 1 + c\sum_{i=0}^{L-1} f_i \prod_{j<i}\eta_j
\;\;\xrightarrow{\;\eta_j \equiv \eta\;}\;\; 1 + c\sum_{i=0}^{L-1} f_i\,\eta^{i}.$$

*(iii) Optimal profile.* Minimising $\sum_i f_i\eta^i$ subject to $\prod_i f_i =
\rho$ gives $f_i^\star = \rho^{1/L}\eta^{(L-1)/2 - i}$, with minimum value
$L\rho^{1/L}\eta^{(L-1)/2}$. At $\eta = 1$ this is uniform fanout and AM–GM.

*(iv) Departure from uniform.*
$$\frac{J^\star}{J_{\text{unif}}}
= \frac{L\,\eta^{(L-1)/2}\,(1-\eta)}{1-\eta^{L}} \;=\; 1 - O\!\big((1-\eta)^2\big).$$

*Proof.* (i) The product telescopes. (ii) is A2 plus the definition of $v_i$.
(iii) Lagrange on $\min \sum a_i f_i$ s.t. $\prod f_i = \rho$ with $a_i =
\eta^i$ gives $a_i f_i = \lambda$, so $f_i \propto \eta^{-i}$; the constraint
fixes $\lambda = \rho^{1/L}\eta^{(L-1)/2}$ and the objective is $L\lambda$. (iv)
is division; first-order terms in $(1-\eta)$ cancel. $\blacksquare$

| $\eta$ | $L$ | $J^\star/J_{\text{unif}}$ | available gain |
| ---: | ---: | ---: | ---: |
| 0.95 | 4 | 0.9983 | 0.2% |
| 0.90 | 4 | 0.9931 | 0.7% |
| 0.80 | 5 | 0.9519 | 4.8% |
| 0.60 | 5 | 0.7807 | 21.9% |

**Consequences.** Geometry does not forbid sub-parity $W$; it bounds it at
$O((1-\eta)^2)$, under 1% at the measured $\eta$. And $f^\star$ is a **static**
profile, so any gain it offers belongs to Hull$_s$ (Corollary C.3), not to the
learner. It is a design result, not an adaptivity result.

**Corollary A.3 (Level removal is never profitable for $T \ge 2$).**
Expanding uniformly by $s = T$ to delete one level pins $f_0 = T^2$ (since $s_0
= 1$) with $f_i = T$ below, giving $\Delta W / c = T^2 - T(1 + \eta^{L-1})$,
strictly positive for every $T \ge 2$ when $\eta < 1$ (and $T(T-2)$ at $\eta =
1$). Removing a level places the largest fanout at the position with the largest
survival weight.

**Corollary A.4 (Write-optimal size ratio) — convention-dependent.**
Under M1/M2 the write-optimal fanout is $f^\star = e$ and $W - 1 \propto
T/\ln T$; under M3 it solves $f \ln f = f + 1$, $f^\star \approx 3.59$. $e$ is
not a universal constant and must not be quoted as one. **The empirical
comparison against §10.7 `regular` is suspended** until recomputed as ratios of
$W - 1$ with the denominator stated (P0-8); the earlier "1.7% agreement" mixed
$W$ and $W-1$. $L$ is integer-valued in RocksDB, which the continuous derivation
does not capture.

### What A therefore does and does not deliver

The §10.7 arm table does not support attributing the learner's write excess to
depth inflation:

| cell | `prior_only` vs `regular` | `rl` vs `regular` | `rl` vs `prior_only` | levels added by `rl` |
| --- | ---: | ---: | ---: | ---: |
| 10M T=2 | +18.3% | +15.9% | **−2.1%** | +3 |
| 10M T=6 | +15.3% | +22.9% | +6.5% | +1 |
| 10M T=10 | +13.9% | +9.8% | **−3.6%** | +1 |

`prior_only` never inflates depth and still writes 14–18% more than the tuned
baseline; the learner writes *less* than the prior in two cells of three,
including the one where it added three levels. The dominant component of the
excess is the prior's eagerness at the top of the tree (L2 compaction rates
0.30 / 0.42 / 0.51), which expansion does not touch.

**A-0 measured (Gate 0, 2026-09-11, `14_gate0_reanalysis.py`).** Paired means
over five repeats; per-level bytes from the 0.1 GB stats table, reconciled to
the exact tickers within 0.8%.

| cell | arm | excess | $D_{\text{depth}}$ (GiB) | $D_{\text{eager}}$ (GiB) | depth share |
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

The prior's excess is entirely eagerness in every cell. The learner's depth
share peaks at 0.35 (20M T=2) and is below the 0.05 GB quantisation at T=10.
Pathway A's attributable $W$ claim is bounded by that share.

- **Delivers:** the ability to defer at a level without releasing a burst
  downstream — no depth cascade, no read cost (Theorem A.1). This makes
  deferral a usable action for the constrained objective.
- **Delivers, conditionally:** removal of the depth component $D_{\text{depth}}$
  of the write excess, measured per cell at A-0, expected small or negative
  outside $T{=}6$.
- **Does not deliver:** parity on its own. A-2 requires Pathway D to reduce the
  prior's eagerness, and every unit of eagerness given up returns read
  amplification. **A-2 is a joint A+D criterion.** The paper attributes a $W$
  change to expansion only for the $D_{\text{depth}}$ share.
- **Does not deliver:** $W$ meaningfully below parity by geometry (Theorem
  A.2(iv), static, Hull$_s$'s). Material sub-parity requires a fall in $\eta$;
  deferral is the mechanism (B-3) and Theorem B.1 bounds it by cumulative
  garbage.

### Implementation

**A-Impl-1. State lives in `VersionStorageInfo`, and stays out of L0's byte
branch.** `level_max_bytes_` and the compaction score live in
`VersionStorageInfo` (`ComputeCompactionScore`, `MaxBytesForLevel`; re-locate
against the pinned commit). A picker-local scale cannot reach the score. Hold
the scale vector in `VersionStorageInfo`, set it from the picker, apply it
inside `MaxBytesForLevel` for levels $\ge 1$ **only**, and never route it through
`max_bytes_for_level_base`, or expanding L1 silently suppresses L0's
byte-driven trigger. Add a unit test asserting L0's computed score is invariant
to the scale vector. An L0 leak would show up as a spurious A-1b pass.

**A-Impl-2. Pin `level_compaction_dynamic_level_bytes = false` and declare
it.** Under the default-on mode, level targets are computed top-down from the
last level, `max_bytes_for_level_multiplier_additional` is ignored, levels below
`max_bytes_for_level_base / multiplier` are kept empty, the L0 target term is
raised to $\max(\texttt{max\_bytes\_for\_level\_base},\;
\texttt{level\_max\_bytes\_}[\text{base\_level}]/\text{multiplier})$ (so
scaling leaks into L0), and `MaxBytesForLevel` is not a pure function of options
and level. The capacity-scale mechanism presumes a static ladder. Document the
pin in §11.2; justify it in the setup section as isolating the mechanism from
an externally varying target schedule.

**Pinned alongside it: direct I/O, off.** `use_direct_reads` and
`use_direct_io_for_flush_and_compaction` are both **false**, with
`compaction_readahead_size` at the pinned tree's 2 MB default. Enabling them was
the original decision and was reversed on measurement: a 10M T=2 pilot ran
mixgraph at 2,750 ops/s with direct I/O and 58,332 ops/s buffered, a 21×
penalty that puts Gate 1 near 95 h. An earlier revision quoted 2.6×, comparing
against a buffered figure from the 2026-09-03 suite on different hardware; that
cross-machine comparison was invalid and is retracted here. Roughly 40% of that
gap came from table-open churn, 7,509 SST files against `max_open_files` 1000
with index and filter blocks outside the block cache; lifting the limit
recovered that much and no more.

The cost is accepted because almost nothing Gate 1 produces is cache-sensitive.
Write, point-read and space amplification, sorted-run seeks, per-level merge
survival and the $\Delta S(\text{scale})$ curve are byte ratios fixed by tree
shape and volume, so Hull₀ over $(W, R)$ is cache-invariant. What buffered I/O
does cost is that latency, runtime and stall figures are warm-cache: the ~3.7 GB
database sits in page cache. They must be reported as bounds on foreground
disruption, never as storage-latency claims. One coupling to watch: stage 06
selects the comparator by lowest mean runtime among the minimum-space survivors,
and runtime is cache-sensitive, so a change of I/O mode requires a regenerated
manifest.

The setting may be turned on for the final paper benchmark workload if the
amplification results justify the cost. Such runs carry fingerprint field `dio1`
and cannot be pooled with `dio0` runs. The rejected alternative remains a cgroup
page-cache cap, which is external state the fingerprint cannot carry. Pinned,
never swept, recorded as the `dio` fingerprint field and as `use_direct_io` in
`metadata.env`.

**A-Impl-3. Snapshot epoch — decompose, do not invalidate.** The epoch mixes
`MaxBytesForLevel(level)` (`compaction_picker_rl.cc:300`), so routing capacity
through `level_max_bytes_` naively makes every in-flight frame structurally
stale on each action-2. Split the quantity: hash the **static base ladder** into
the epoch; carry the **controller-set scale** as an explicit echoed field. This
is sound only under A5; if A5 is lifted, the base ladder returns to the epoch as
a hashed input.

**A-Impl-4. Debt estimator.** The debt safeguard (`:1177–1185`,
`pending_debt_ratio_limit`) and `NeedsCompaction` (`:2177`) read
`estimated_compaction_needed_bytes()`, computed by
`EstimateCompactionBytesNeeded` from `CalculateBaseBytes` targets. If the scale
does not feed that estimator, debt accrues against an unchanged limit and §6.4
forces the gate open precisely when expansion is meant to hold it closed, so
A-1b fails for a reason unrelated to Theorem A.1. The estimator must be in
scope. The ratio is then self-consistent; the **absolute** pending-byte limits
do not scale and must be re-checked. Defaults: `soft_pending_compaction_bytes_limit`
= 64 GB, `hard_pending_compaction_bytes_limit` = 256 GB, decimal. Re-derive the
§11.2 headroom with 256 GB.

**A-Impl-5. Ruled out: `max_bytes_for_level_multiplier_additional`.** It is
`kMutable`, but (a) it is `vector<int>`, so $\alpha = 1.25$ and the decay are
inexpressible; (b) its effect is cumulative down the tree, so it cannot express
independent per-level $s_i$; (c) it is ignored outright when
`level_compaction_dynamic_level_bytes` is on.

**A-Impl-6. Protocol.** Extend the protocol-v2 response from a binary action
array to a ternary one. Bump to `credit_assignment_version: 3`; preserve the
`decision_id` echo and effective-action reporting from the 2026-08-31 repair.

**A-Impl-7. Bounds.** Enforce $s_i \in [1, s_{\max}]$ with $s_{\max}$ read from
Gate 1's measured $\Delta S(\text{scale})$ curve at the budget rung the run is
executing. Record the rung and its $s_{\max}$ in the run manifest and the
experiment fingerprint. Reject expansions violating the projected space bound
before applying them.

**A-Impl-8. Decay.** $s_i \leftarrow \max(1, s_i \cdot e^{-\Delta t/\tau})$ each
control interval where action 2 was not selected; start $\tau$ at 4× the
decision horizon. Decay can drive $s_i$ below $\phi_i$ while the level is still
over-full; either the controller re-expands to hold the condition, or depth
invariance degrades to depth deferral. A-4 tests the conjunction.

**A-Impl-9. Attribution.** Expansion appears in the effective-action stream,
the replay transition, and per-level diagnostics. A forced contraction (safety,
drain, maintenance) is an override and is relabelled as such, exactly as forced
opens are.

**A-Impl-10. Pressure audit.** `CheckPressureDivergence` (`:1188`) asserts the
held score equals the score published through the `ComputeCompactionScore`
observer; locating the scale in `VersionStorageInfo` satisfies this. Extend the
audit to assert L0 score invariance under the scale vector.

### Acceptance criteria

| # | Criterion | Threshold | Instrument |
| --- | --- | --- | --- |
| A-0 | **Write-excess decomposition** (Gate 0, existing §10.7 artifacts) | $D_{\text{depth}}$ and $D_{\text{eager}}$ per cell for `prior_only` and `rl`, summing to the total excess; attribution of any $W$ change to expansion is limited to $D_{\text{depth}}$ | per-level `compaction_bytes_written`, paired by seed |
| A-1a | **Mechanism:** with $s_i \ge \phi_i$ enforced, $\delta L = 0$ | every paired repeat, T=2, relaxed space bound (Gate 3a-0) | `levelstats`, $\phi_j$ at release |
| A-1b | **Acceptance:** $\delta L = 0$ under the space bound of the budget rung being run | every paired repeat, at the (cell, rung) pairs selected by the rule below; 2% rung is the research-track headline unless another is preregistered before 3b | `levelstats` |
| A-2 | Write amplification non-inferior to baseline (**joint A+D**) | upper 95% bound on $W_{rl} - W_{base}$ $\le \delta_W$ (2%, P0-3) | paired evaluator |
| A-3 | Space amplification inside the space bound | upper 95% bound $\le$ the bound of the rung being run | paired evaluator |
| A-4 | **Sustained** expansion — Theorem A.1's hypothesis held | $s_i \ge \phi_i$ in $\ge$ 99% of frames where $\phi_i > 1$; $\max_i s_i \le s_{\max}$ in 100% of frames | policy log |
| A-5 | Accounting integrity preserved | zero hard-invalid frames, balanced acceptance, C++/Python decision agreement | `learning_health.json` schema 3 |

A-2 uses non-inferiority, not "CI contains 0": with $\pm 20$–$30\%$ CIs at five
repeats, a CI containing 0 passes by lack of power. A-4's $s_{\max}$ clause is a
construction invariant; the sustained-condition clause is the measured
criterion.

**Preregistered cell rule (replaces any cell list):**

> A-1b runs at every (cell, budget rung) pair, rungs from the P0-6 ladder
> $\{0, 2, 5, 10\}\%$, where Gate 1's measured $\Delta S(\text{scale})$ curve
> gives $s_{\max}(\text{rung}) \ge 1.10\,\kappa$. Pairs with $s_{\max} < \kappa$
> are reported as closed at that rung, with the measured curve, and not run.
> Pairs in $[\kappa,\,1.10\kappa)$ are reported as undecidable at the available
> margin and not run. A cell closed at every rung is closed. The research-track
> verdict is read at the 2% rung.

**A-1a's relaxed bound — one rule.** Set it to whatever $S$ the measured
$\Delta S(s)$ curve says is required to reach $s = 1.10\,\kappa_{1M}$, and
report it as a measured space cost ("depth invariance at $T{=}2$ costs $X\%$
space"), not as a threshold. A-1a is diagnostic and carries no acceptance
weight.

**A-1b is the gate.** If depth does not flatten at the surviving pairs, A3/A4
are wrong for this system and the analytical spine needs revision.

---
## Pathway B — Workload Realism

### Purpose

Theorem A.2 shows the only *material* route to $W$ below parity is a fall in
merge survival $\eta$. Deferral is the mechanism that lowers $\eta$: holding a
merge back lets more overwrites accumulate above it, so each merge drops more
(B-3). The fall is bounded by **cumulative** garbage (Theorem B.1), which is
large on the current workload — the same workload settles to $S = 2.271$ at
$T{=}2$ and $1.166$ at $T{=}10$, so $g_{\text{flow}} \ge 0.56$ and the $T{=}10$
baseline has simply already elided most of it. What Pathway B adds is (B1) skew,
which concentrates overwrites on hot keys so that deferral at a level has garbage
to act on before the baseline reaches it; (B3) tombstones, a garbage class the
uniform workload never produces, which activate the compensated-size term of the
file-selection control; and (B2) phases, which create the non-stationarity that
lets phase-adaptivity be measured.

### Description

**B1 — Update skew (creates concentrated $g$). (Done)** `mixgraph` uses prefix-based
hotspot key generation only when at least one of `keyrange_dist_{a,b,c,d}` is
nonzero; all-zero (the current configuration) yields uniform random. Adopt the
published fit:

```
-keyrange_dist_a=14.18  -keyrange_dist_b=-2.917
-keyrange_dist_c=0.0164 -keyrange_dist_d=-0.08082
-keyrange_num=30
-key_dist_a=0.002312    -key_dist_b=0.3467
-value_k=0.2615         -value_sigma=25.45
-iter_k=2.517           -iter_sigma=14.236
```

These encode the **UDB `Assoc`** column family, modelled mix Get/Put/Iterator
$= 0.806 / 0.159 / 0.035$, **no modelled deletes**. Cite the column family; note
that the paper's appendix, the RocksDB wiki and §7.4 disagree at the second
decimal rather than picking one silently. Sweep `keyrange_num` $\in \{5, 30,
100\}$ as the skew-intensity axis.

**B2 — Workload shift (creates non-stationarity).** `sine_*` flags vary QPS
intensity, not the operation mix. Chaining `db_bench` invocations restarts the
process and resets the learner (violates §3.2); routing through the Tectonic
runner creates a second experiment surface (rejected, see Gate 2). **Use an
in-process `db_bench` phase patch.** Schedule: 2 phases (read-heavy ↔
write-heavy) until a nonzero $\mathcal{G}$ is demonstrated; extend to 4 only
afterwards. Shift magnitude and frequency are separate swept axes. Shift points
are identical across arms so pairing stays valid.

**B3 — Explicit deletes (creates tombstones). A patch, not a flag.** `mixgraph`
models exactly three operation types (Get, Put, Seek) from
`FLAGS_mix_{get,put,seek}_ratio`, read once at benchmark start; there is no
`mix_delete_ratio`. Add a fourth operation type to `QueryDecider` and a
`mix_delete_ratio` flag, in the same patch as B2's phase schedule. Rejected
alternatives: `DeleteRange` injection (range tombstones, different compaction
semantics, not comparable to the published mix); a side thread (breaks the
single-decider op accounting the paired evaluator relies on). Delete rate is a
swept axis $\in \{0\%, 3\%, 6\%\}$ on the `Assoc` key distribution, labelled
synthetic (P0-10); the 78/13/6/3 mix is ZippyDB, not `Assoc`. Tombstones are
dropped only at the bottommost level, so the $\eta$ response to deletes will be
concentrated there — predict this before measuring it.

### Theory

**Theorem B.1 (Elision bound).**
*Run-cumulative merge survival satisfies $\eta \ge 1/S_{\text{flow}}$. Under
the write model of Theorem A.2(ii) with uniform fanout and constant survival,
the maximum achievable relative reduction in $W - 1$ is*

$$\frac{|\Delta (W-1)|}{W-1} \;\le\;
1 - \frac{1 - \eta_{\min}^{\,L}}{L\,(1-\eta_{\min})},
\qquad \eta_{\min} = \frac{1}{S_{\text{flow}}},$$

*which for small garbage is $\approx (L-1)\,g_{\text{flow}}/2$.*

*Proof.* Every byte live at run end must survive every merge it participates
in, so any merge's output is at least the bytes among its inputs that are live
at run end. Pooled over the run, compaction output is at least the live bytes
and input is at most the bytes the user wrote plus their rewrites, so the pooled
ratio is at least $N/N_{\text{written}} = 1/S_{\text{flow}}$. With uniform $f$,
$(W-1)(\eta)/(W-1)(1) = \frac{1}{L}\sum_{i<L}\eta^i = \frac{1-\eta^L}{L(1-\eta)}$;
substitute $\eta_{\min}$ and expand. $\blacksquare$

The floor is $1/S_{\text{flow}}$, not $1/S$: resident garbage is a snapshot,
elision is a flow, and the two differ by exactly the garbage the baseline has
already elided. The table below is the Gate 0 recomputation from measured
$S_{\text{flow}}$ on the `regular` arm of `suite-20260902-193415`, with $L$
the number of merge stages (populated levels minus one). The earlier
resident-$S$ table, which was a lower bound on the ceiling, is withdrawn.

| cell | $S_{\text{flow}}$ | $g_{\text{flow}}$ | resident $S$ | $L$ | ceiling on $W-1$ |
| --- | ---: | ---: | ---: | ---: | ---: |
| 10M $T{=}2$ | 2.553 | 60.8% | 2.271 | 8 | 79.5% |
| 10M $T{=}6$ | 1.503 | 33.5% | 1.261 | 4 | 39.9% |
| 10M $T{=}10$ | 1.422 | 29.7% | 1.166 | 4 | 36.3% |
| 20M $T{=}2$ | 2.561 | 60.9% | 2.250 | 9 | 81.8% |

This is a strict upper bound, not an expectation. **The quantity to trust is
measured per-level $\eta$ from the Gate 0 instrument.** B.1 is a sanity check
and the ceiling for B-2 and D-4, not a design input. Do not draw feasibility
conclusions from the current `rl` gaps: they are pre-A/D deficits (A-0), and the
comparison that matters is the ceiling against $d'$, the residual deficit
measured after Gate 3b.

---

**Theorem B.2 (Expansion budget) — marginal bound, sanity check only.**

$\sum_{i<L}C_i = C_0\frac{T^L-1}{T-1}$ and $C_L = C_0T^L$, so
$C_L/\sum_{i<L}C_i \to T-1$; the structural form is $S \le 1 + s/(T-1)$. As an
*absolute* bound it is falsified by the $s{=}1$ baselines (predicts
2.000/1.200/1.111 against measured 2.271/1.261/1.166), so it cannot set
$s_{\max}$. Let $\Delta S(s) = S(s) - S(1)$. Two models bracket it:

- **Worst case** (all deferred volume is pure garbage): $\Delta S \le
  \frac{s-1}{T-1}$, $s_{\max} = 1 + (T-1)\,\Delta S_{\text{budget}}$.
- **Survival-weighted** (only the elidable fraction of deferred volume is
  additional): $\Delta S \lesssim \frac{(1-\eta_i)(s-1)}{T-1}$ with the
  **local** $\eta_i$ at the expanded level — a model, not a theorem, since a
  merge also touches overlapping bytes below.

| $T$ | $S_{\text{base}}$ | 2% budget $\Delta S$ | $s_{\max}$, worst case | required $\kappa$ |
| ---: | ---: | ---: | ---: | ---: |
| 2 | 2.2710 | 0.0454 | 1.045 | 3.00 |
| 6 | 1.2614 | 0.0252 | 1.126 | 1.60 |
| 10 | 1.1655 | 0.0233 | 1.210 | 1.71 |

Under the worst case every cell is closed at a 2% budget; under the
survival-weighted model $T{=}10$ is the most plausibly open cell, but the model
carries no predictive weight for cell selection. The one expectation surviving
both brackets is that $T{=}2$ is closed at any reasonable budget ($\kappa =
3.00$ against $S_{\text{base}} = 2.27$). Therefore **$s_{\max}$ is measured at
Gate 1, not derived**: the grid's `max_bytes_for_level_base` axis (0.5×/1×/2×)
is a static uniform capacity-scale axis; extracting $\Delta S(\text{scale})$ and
$\eta_i$ from those runs costs no additional node-hours. Uniform scaling also
changes $L$ whereas per-level $s_i$ does not, so the measured curve is an upper
bound on the per-level mechanism's space cost — the conservative direction.

**The space bound is a swept axis** (P0-6). Report the $W$–$R$–$S$ surface at
budgets $\{0, 2, 5, 10\}\%$ rather than fixing 2% and discovering the mechanism
cannot be tested inside it.

---

**Proposition B.3 (Exploration cost is strictly positive).**
*Let $\theta^\star = \arg\min_{\theta\in\Theta} J(\theta)$ be constant over the
run, and suppose the optimal policy for the underlying control problem is
realisable as a constant action. Then for any online controller $\pi$ that does
not know $\theta^\star$ and performs non-degenerate exploration, over a finite
horizon $J(\pi) = J(\theta^\star) + \mathcal{R}$ with $\mathcal{R} > 0$.*

*Proof.* Under the hypothesis, $\pi$'s cost decomposes into $\theta^\star$'s
cost plus regret on every interval where the action differs; any exploration
schedule with nonzero probability of a suboptimal action on a positive-measure
set contributes strictly positive regret. $\blacksquare$

**The added hypothesis is generally false here, and that is a positive result.**
A stationary *workload* does not imply a stationary *system state*: level
occupancies, pending-compaction debt, L0 file count and stall state evolve under
stationary arrivals, and compaction control is a genuine decision problem over
that internal state. $\Theta$ is exactly the subclass of constant-action
policies, so a state-dependent policy can strictly beat every member of
$\Theta$ on a perfectly stationary workload. The learner's primary route to
value is therefore **state-dependent control**; phase-adaptivity is secondary.
The data (§10.6: `prior_only` and `rl` on identical policies differing by
$+0.8$pp $W$ and $+4.8$pp stalls) supports only "exploration is not free".
Do not use "no-regret" for this; no-regret means sublinear regret.

**Definition (phase-adaptivity gap).**
$$\mathcal{G} \;=\; \min_{\theta \in \Theta} J(\theta) \;-\; \sum_{p} \frac{t_p}{T_{\text{tot}}}\min_{\theta\in\Theta} J_p(\theta),$$
the excess cost of the best single static configuration over a hindsight oracle
that switches per phase. Both terms range over $\Theta$.

**Corollary B.4.** $\mathcal{G} > 0$ is sufficient evidence that adaptivity has
value on that workload. $\mathcal{G} = 0$ is **not** evidence that it does not:
$\mathcal{G}$ measures only the workload-phase component of adaptivity, and the
learner's per-level, per-interval action space lies outside $\Theta$ in a way
$\mathcal{G}$ does not see. $\mathcal{G}$ is a lower bound on one component, not
an upper bound on the whole. B-4 is therefore not a research-track requirement.

### Implementation

1. **(Done, 2026-09-20)** Parameterise the `db_bench` pipeline over a
   workload-family flag: `uniform`, `skew` (B1), `skew+delete` (B1+B3).
   `WORKLOAD_SKEW` in `config.sh` selects the first two; `skew+delete` awaits
   B3. Adopted as the programme-wide workload, not a Gate 4 axis: a hull
   measured on one family is not a valid comparator for a policy measured on
   another, which is the workload form of the knob-parity rule stated under
   "Two hulls". Deviations from the published fit: `value_theta` is 925.5, not
   the paper's 0, holding the mean value size at the project's 960 bytes so the
   level ladder and the T sweep stay comparable — report as "Assoc key
   distribution and operation mix at the project's record size", never as the
   published value distribution. `mix_max_value_size` is raised to 65536
   because db_bench applies it as `val_size % value_max`, and the 1024 default
   wraps 6.85% of draws down to as little as one byte and pulls the measured
   mean to 890.2.
2. Implement `shift` (B2) as a `db_bench` phase patch (see Gate 2 for the
   `QueryDecider::Initiate` trap). Record phase boundaries in the run manifest
   and the experiment fingerprint.
3. Implement deletes (B3) as a fourth operation type in the same `QueryDecider`
   patch, with `mix_delete_ratio`.
4. **Instrument $\eta$ (Gate 0):** `compaction_bytes_written /
   compaction_bytes_read`, per level and per run, **excluding trivial moves**
   (output = input, would bias toward 1). Per level because tombstone dropping
   happens only at the bottommost level.
5. Hindsight-oracle runner: per phase, run the static sweep and record
   $\min_\theta J_p(\theta)$; compose to obtain $\mathcal{G}$.

### Acceptance criteria

| # | Criterion | Threshold | Instrument |
| --- | --- | --- | --- |
| B-1 | Skew raises resident garbage | $g$ under `skew` exceeds $g$ under `uniform` by $\ge$ 10pp at the surviving cells | space amp, `regular` arm |
| B-2 | Elision ceiling exceeds the residual deficit | Theorem B.1 ceiling at measured $S_{\text{flow}}$ and $L$ $>$ measured $d'$ for that cell | derived |
| B-3 | Merge survival is measurable and responds | $\eta$ per level, trivial moves excluded; $\eta_{\text{defer}} < \eta_{\text{eager}}$; response concentrated at the bottommost level | Gate 0 instrument |
| B-4 | Shift produces a positive phase-adaptivity gap | $\mathcal{G} > 0$ with 95% CI excluding 0 | hindsight-oracle runner |
| B-5 | Deletes activate compensated size | nonzero tombstone-driven file selections in RocksDB LOG | `LOG` parse |

B-2 is evaluated against the post-Gate-3b deficit $d'$, not the current gaps,
and against the ceiling at measured $S_{\text{flow}}$; prefer measured
per-level $\eta$ over the bound wherever both are available. B-4 gates the
*phase*-adaptivity claim only; state adaptivity is D-5's business.

---

## Pathway C — Frontier Comparator

### Purpose

Replace a single tuned baseline point with the Pareto hull of the static
configuration class, so the claim is about a class rather than a point. Under
Theorem A.2(iii) the survival-weighted optimal fanout profile is static, so any
geometric gain from non-uniform capacity must appear in Hull$_s$ or the
comparison is unfair to the baseline. Gate 1's grid also supplies Theorem B.2's
calibration.

### Description

The §3.1 baseline is selected as minimum-space, then lowest-runtime, a procedure
that never explores trading write bandwidth for reads; the comparator sits at
one arbitrary point on a curve that was never traced.

**Two hulls.**

- **Hull₀** — static configurations at $s = 1$. Comparator for `prior_only` and
  any arm without a capacity action (C-3). Runs at Gate 1.
- **Hull$_s$** — Hull₀ plus statically capacity-expanded configurations,
  including the profile $f_i \propto \eta^{-i}$ at measured $\eta$. Comparator
  for post-Pathway-A `rl` (C-4). Runs after Gate 2, only at cells surviving
  Gate 3b.

Comparing a policy against a static class that lacks a knob the policy has, or
has one the policy lacks, is invalid in either direction.

**Grid, sized to a lease.** The full $4\times3\times2 = 24$ configurations per
$T$ at ~110 s/Mop is ≈ 120 h at five repeats. Pin `compaction_pri` to
`kMinOverlappingRatio` (the experimental control in §2.2/§4.3) for a
$4\times3 = 12$ subset; L0 trigger and level scale move along the $W$–$R$ trade,
`compaction_pri` changes *which* files rather than *how much*. Test the
alternative priority only at retained hull points.

| Knob | Values | In the 12-point subset |
| --- | --- | --- |
| `level0_file_num_compaction_trigger` | 2, 4, 8, 16 | yes |
| level target scale (`max_bytes_for_level_base`) | 0.5×, 1×, 2× | yes — the B.2 calibration axis |
| `compaction_pri` | `kMinOverlappingRatio`, `kOldestSmallestSeqFirst` | pinned to the former; alternative only at retained points |
| size ratio $T$ (cross-$T$ hull) | 2, 6, 10 (full grid) plus 14, 20 (`regular` at trigger 4, scale 1× only) | 14 and 20 are two extra `regular` cells per repeat |
| static $s$ (Hull$_s$ only) | 1.0, 1.5, 2.0 | surviving cells only |

**Why $T$ is on the grid.** A per-$T$ hull is the right comparison for the
paired evaluator but not the static class a reviewer means by "a tuned
baseline": a different size ratio is a legitimate static competitor. Cross-$T$
dominance is already present in §10.7 means — `regular` $T{=}6$ (8.33, 5.30,
1.26) dominates `prior_only` $T{=}2$ (8.78, 6.53, 2.28) and `rl` $T{=}2$ (8.60,
7.59, 1.62) on $(W, R, S)$; `regular` $T{=}10$ (9.36, 4.52, 1.17) dominates `rl`
$T{=}6$ (10.24, 4.71, 1.23). Every non-dominated policy point is at $T{=}10$,
past the last measured `regular` point. Two extra `regular` cells at three to
five repeats cost ≈ 3–4 h and bound the hull on the side where those points sit.

**Adaptive repeat schedule.** Three repeats to locate the hull ($12 \times 3T
\times 3 \approx 36$ h), retained points topped up to five or until C-2's width
condition is met ($\approx 8$ h), plus the cross-$T$ cells ($\approx 3$–4 h).
Total $\approx 48$ h.

### Theory

**Proposition C.1 (Comparator validity).** *Let $\mathcal{H}$ be the Pareto
hull of $\{(W(\theta), R(\theta)) : \theta \in \Theta\}$. If $\pi$'s operating
point is dominated by any point of $\mathcal{H}$, a static configuration exists
that is at least as good on every axis, and $\pi$'s contribution is nil
regardless of its performance against any single $\theta_0$.* Immediate from
the definition of dominance.

**Corollary C.2.** "$\pi$ beats $\theta_0$" is strictly weaker than "$\pi$ is
non-dominated by $\mathcal{H}$". Finding 2 (`prior_only` at $-19.3\%$ reads for
$+13.9\%$ writes) is currently in exactly this unresolved state.

**Corollary C.3 (The geometric gain belongs to the hull).** By Theorem A.2(iii)
the survival-weighted optimal fanout profile is fixed, hence realisable
statically. Any $W$ reduction attributable to fanout shaping alone is a point of
Hull$_s$, not a contribution of $\pi$. The learner's case rests on
state-dependent action (D-5, C-4).

### Implementation

1. Baseline-only sweep script: no socket, no learner, no guard.
2. Hull extraction with paired-seed CIs on each retained point, per $T$ and
   pooled over $T$.
3. Plotting routine overlaying `prior_only`, `rl` and the hindsight oracle on
   the hull, per $T$ and per workload family.
4. Extraction step producing $\Delta S(\text{scale})$, per-level $\eta$ and $L$
   from the Gate 1 runs, which sets $s_{\max}$ per (cell, rung) for A-Impl-7.

### Acceptance criteria

| # | Criterion | Threshold | Instrument |
| --- | --- | --- | --- |
| C-1 | Hull₀ adequately sampled | 12 static configurations per $T$; $\ge$ 4 on the hull | sweep output |
| C-2 | Retained hull points decidable | 3 repeats to locate, topped up to $\ge$ 5 until CI width $<$ half the inter-point spacing | paired evaluator |
| C-3 | `prior_only` position resolved against Hull₀ | classified dominated or non-dominated, with CI | hull comparison |
| C-4 | `rl` non-dominated against Hull$_s$ | no hull point dominates $\pi$ on $(W, R)$ simultaneously | hull comparison |
| C-5 | $s_{\max}$ calibrated | $\Delta S(\text{scale})$ and per-level $\eta$ measured at every $T$; $s_{\max}$ set per (cell, rung) before Gate 3b | Gate 1 re-analysis |
| C-6 | **Cross-$T$ non-domination** | C-3 and C-4 also pass against the hull pooled over $T \in \{2, 6, 10, 14, 20\}$ on $(W, R)$ with $S$ inside the cell's bound; a policy dominated by a `regular` point at another $T$ is reported as dominated | hull comparison, cross-$T$ |

C-3 decides whether Finding 2 survives: if `prior_only` lies on or inside the
hull, it reduces to "compacting more improves reads" and is withdrawn.

**C-3 and C-6 executed 2026-09-19: both FAIL. Finding 2 is withdrawn.**
Measured on `unconstrained_prior_only` at 10M, ten repeats per cell — the
analytic prior with the guard classifying but not enforcing, which the E-5
measurement below shows is the same policy as `prior_only` on this workload
(the guard changes at most 0.09% of frames). All thirty arms passed the
learning-health gate with `eval_mode`, `zero_train_steps` and `zero_residual`.

| cell | $W$ | $R$ | $S$ | C-3 | dominator CI, hull minus policy |
| --- | ---: | ---: | ---: | --- | --- |
| T=2 | 9.5621 | 7.4761 | 2.1798 | **dominated** | $W$ [-7.02%, -6.38%], $R$ [-4.34%, -3.66%] |
| T=6 | 9.8464 | 5.2476 | 1.3370 | **dominated** | $W$ [-9.05%, -8.57%], $R$ [-3.72%, -0.92%] |
| T=10 | 10.4058 | 4.8861 | 1.2125 | **dominated** | $W$ [-6.49%, -5.58%], $R$ [-5.74%, -3.81%] |

One static configuration per cell is better on $W$ **and** $R$ at once, both
paired intervals strictly below zero. **C-6 fails identically**: the cross-$T$
pooled hull over $T \in \{2,6,10,14,20\}$ holds 18 of 38 points — reproducing
the Gate 1 figure exactly — and the policy is dominated within it.

**Consequence for C-4.** Hull$_s$ is the static class plus capacity-expanded
configurations, so its configuration set contains Hull$_0$'s. If a Hull$_0$
point dominates a policy, then either that point is in Hull$_s$ or something in
Hull$_s$ dominates it; domination is transitive, so **anything dominated by
Hull$_0$ is dominated by Hull$_s$**. That applies to the prior directly. It
does not transfer to `rl`, which is a different policy once it carries a
capacity action — but it fixes the bar Gate 3b must clear: 6-9% on $W$ and
4-6% on $R$, simultaneously, against a comparator at least as strong as the one
the prior lost to.

**Scope, and why Gate 4 is now upstream of Gate 3b.** This workload has almost
no resident garbage, and Gate 0 measured the ceiling that follows: Theorem B.1
caps $W-1$ at 79.5%, 39.9% and 36.3% at $T$ = 2, 6 and 10. Compaction's
write-side benefit is elision of stale versions, so where there are none,
compacting more can only add write bytes — the class wins because the policy's
mechanism has nothing here to work on. This negative result is specific to a
near-garbage-free workload and must be reported with that scope. Pathway B
tests whether it generalises, and that question is upstream of whether a
capacity knob helps. C-5
gates Gate 3b. C-6 is what a "tuned baseline" objection actually tests; report
both verdicts and lead with the cross-$T$ one.

---

## Pathway D — Constrained Objective and Reward Alignment

### Purpose

Make the reward the learner optimises identical to the criterion it is judged
against. §10.7 Finding 3: $\Phi$ treats space as a quantity to minimise while
the criteria treat it as a bound, and the learner correctly spent reads on space
it received no credit for.

### Description

The objective is

$$\min_\pi\; R(\pi) \quad\text{s.t.}\quad W(\pi)\le W_{\text{base}} + \delta_W,\;\;
S(\pi)\le S_{\text{bound}},\;\; \text{lat}(\pi)\le \text{lat}_{\text{bound}},\;\;
\text{stall}(\pi)\le \text{stall}_{\text{base}} .$$

The write constraint is non-inferiority at margin $\delta_W = 2\%$ (P0-3),
matching A-2; exact parity is not testable at the available power.
Parity-plus-margin is the *acceptance* target because it is the claim the
current evidence supports, not because the theory forecloses improvement:
geometry offers $O((1-\eta)^2)$ (Theorem A.2), but elision is bounded at
cumulative garbage (Theorem B.1), which is large, and deferral reaches it (B-3).
Improvement on $W$ is a bonus. Sweep $\beta$ only to exhibit the frontier's
shape. $S_{\text{bound}}$ is a swept axis (P0-6).

**The reward trains against a point; the paper is judged against a hull.** The
write hinge needs a scalar the learner can observe online, so it uses
$W_{\text{base}} + \delta_W$ from the tuned point at the arm's own $T$. C-4 and
C-6 compare the resulting policy with Hull$_s$ and the cross-$T$ hull. A policy
can satisfy the hinge and still be dominated; that outcome is reported as
dominated and the hinge target is not moved to rescue it.

### Theory

**Proposition D.1 (Shaping invariance, with the variable-duration correction).**
For a standard MDP with $F(s,a,s') = \gamma\Phi(s') - \Phi(s)$, the optimal
policy under $r + F$ equals that under $r$ (Ng, Harada & Russell, ICML 1999).
The controller operates over variable-duration decisions, for which the
invariance-preserving form is $F = \gamma^{\tau}\Phi(s') - \Phi(s)$ with $\tau$
the realised duration. The single-step form is not policy-invariant when $\tau$
varies and mis-credits long compactions in a state-correlated way. **Audit the
existing $\Phi$ term for which form it implements before retaining it.**

**Proposition D.2 (Hinge terms are not shaping).** Terms $-\lambda\max(0, x -
x_{\text{bound}})$ are not potential differences and do change the optimal
policy. This is intended; the paper must state which terms are shaping and
which are objective.

**Proposition D.3 (Two-timescale convergence — design motivation only).** With
multipliers updated on a slower timescale than the Q-function ($\eta_\lambda /
\eta_Q \to 0$, $\sum\eta = \infty$, $\sum\eta^2 < \infty$), the primal–dual
iteration converges to a saddle point of the Lagrangian under standard
stochastic-approximation conditions (Borkar, *Systems & Control Letters* 29(5),
1997; 54(3):207–213, 2005). Those results assume step-size separation, iterate
boundedness and compatible/linear approximation, none of which hold for a deep
nonlinear off-policy controller. Cite for the *design* of the separation; do not
claim convergence. Practically: update $\lambda$ 10–100× more slowly than Q.

**Diagnostic corollary.** An unbounded $\lambda_W$ trajectory is evidence that
the write constraint is infeasible at that cell — which is exactly what should
happen wherever the Theorem B.1 ceiling is below the residual deficit. A
training pathology becomes a confirmation of the analysis (D-4). Evaluate
against the ceiling at measured $S_{\text{flow}}$ and $L$.

### Implementation

1. Reward: $r_t = -\Delta R_t - \lambda_W[W_t - W_{\text{base}} - \delta_W]^+ -
   \lambda_S[S_t - S_{\text{bound}}]^+ - \lambda_L[\text{lat}_t -
   \text{lat}_{\text{bound}}]^+$.
2. Dual ascent: $\lambda \leftarrow [\lambda + \eta_\lambda(\text{violation})]^+$
   on the slow timescale.
3. Remove space from $\Phi$ as a minimand; it enters only through its hinge.
4. Audit and, if necessary, correct $\Phi$'s discounting to the $\gamma^\tau$
   form (Proposition D.1).
5. Stabilisation, in this order: Huber loss on TD error, gradient-norm clipping
   at 10, target-network period sweep, reward normalisation by running standard
   deviation. §14.5 reports `max_abs_residual_advantage` 1012–1102 against
   `residual_scale` 1.5–3.4 and TD loss near 6,136, several times the return
   scale; that is not converged.
6. Log $\lambda$ trajectories as first-class output.

### Acceptance criteria

| # | Criterion | Threshold | Instrument |
| --- | --- | --- | --- |
| D-1 | Residual tail controlled | $\max_i \|\text{residual}\| / \text{residual\_scale} < 20$ | `11_analyze_learning.py` |
| D-2 | TD loss converges to return scale | final TD loss $<$ 2× observed return scale | learning log |
| D-3 | Multipliers bounded on feasible cells | $\lambda$ plateaus within run; no monotone divergence | $\lambda$ log |
| D-4 | Multipliers diverge on infeasible cells | $\lambda_W$ grows monotonically exactly where the Theorem B.1 ceiling $< d'$ | $\lambda$ log + Theorem B.1 |
| D-5 | **Learned policy is state-dependent and beats the prior** | argmax flip rate $>$ 0.1 per level; action distribution varies with level occupancy and debt, not just time; reward improves over `prior_only` | learning health |

D-5 is a primary criterion (Proposition B.3, Corollary C.3): the learner's case
rests on behaviour no fixed member of $\Theta$ and no static fanout profile can
reproduce. A high flip rate uncorrelated with state is churn, not adaptivity.
D-4 is a positive result, not a failure.

---

## Pathway E — Shield Symmetry and Guard Calibration

### Purpose

§10.6 Finding 2: the SLO mask collapses a $+19\%/-12\%$ policy excursion to
within $\sim1\%$ of baseline on every amplification metric. The shield
determines the outcome more than the policy does.

### Description

**Structural.** §6.4's action set is asymmetric: space breach and read breach
force due gates open; write breach revokes only *optional* below-threshold work.
None can hold due work closed. Under a write-parity objective this opposes the
only direction the policy needs to move. Add a deferral-side action: on
write-pressure breach, the shield may hold due work closed and contract $s_i$.

**Calibration.** The holdout sits at 3.8–6.0% predicted override against a 1%
preregistered threshold. Either derive the limits from measured baseline
dispersion — as §14.2 does for stall seconds — or amend the threshold with
justification.

### Theory

**Proposition E.1 (Shield projection).** *Let the shield override with
probability $p$ and project the action onto $A_{\text{shield}} \subseteq
A_{\text{policy}}$. The realised policy is $\pi_{\text{real}} = (1-p)\pi +
p\,\Pi_{A_{\text{shield}}}\pi$; as $p \to 1$ it tends to
$\Pi_{A_{\text{shield}}}\pi$, independent of $\pi$ on $A_{\text{policy}}
\setminus A_{\text{shield}}$.* Immediate from the mixture.

**Corollary E.2.** $\|\pi_{\text{real}} - \pi\| \le p\,\|\Pi_{A_{\text{shield}}}\pi -
\pi\|$. Small $p$ alone bounds the shield's influence. What symmetry buys is
that the residual influence is unbiased in direction: with $A_{\text{shield}}
\subsetneq A_{\text{policy}}$ every override pushes toward compaction, a
systematic drift against the direction the policy needs. And the marginal rate
is the wrong statistic: a shield that fires 1% of the time but only when
deferral would have paid can still determine the outcome. Report the override
rate *conditioned on the policy having selected defer or expand*.

### Implementation

1. Add the deferral-side shield action and its attribution reason code,
   extending the six-reason table in §5.7, including contraction of $s_i$.
2. Recalibrate limits from preregistered baseline dispersion rather than
   hard-coded bootstrap caps (§10.6: most manifests returned
   `calibrated: false`, forcing the caps).
3. Report shield intervention rate per cell, marginal and conditioned on the
   policy's selected action.

### Acceptance criteria

| # | Criterion | Threshold | Instrument |
| --- | --- | --- | --- |
| E-1 | Holdout meets its preregistered limit | $\le$ 1% predicted override, or an amended and justified limit | guard holdout |
| E-2 | Manifests calibrate | `calibrated: true` for $\ge$ 90% of **populated** level-cells, where populated means `episode_count > 0`; the count of populated and declared levels is reported alongside | manifest generator |
| E-3 | Shield action set spans policy action set | every policy action has a shield counterpart, including expansion/contraction | source review |
| E-4 | Shield no longer dominates | $\|J(\texttt{rl}) - J(\texttt{unconstrained\_rl})\|$ smaller than $\|J(\texttt{unconstrained\_rl}) - J(\texttt{regular})\|$ | paired evaluator |
| E-5 | Conditional override rate reported | override rate given policy selected defer/expand, per cell | guard log |

E-1 must pass or the guard is cut from the paper. **E-1 is recorded failed as of
2026-09-14** (verdict and evidence below). **Decided 2026-09-20, PREREGISTRATION
D-2:** the failure was traced by offline replay to a budget-force latch the
calibration never modelled (about half the rate) plus two unmodelled global
terms; the latch is removed, the shadow log gains the global terms, and the
guard is kept with **E-5 as the deciding criterion** for the re-run. E-1's
marginal rate is reported per cell without a pass/fail.

**E-2 denominator amended 2026-09-14, recorded before the holdout re-run.** The
denominator becomes populated level-cells rather than declared ones. `num_levels`
is a fixed configuration constant of 12, while a 10M run populates 8 levels at
$T=2$ and 4 at $T=6$ and $T=10$. An unpopulated level never became due, emits no
pressure episode, and therefore carries nothing to calibrate; counting it as a
calibration failure makes E-2 unsatisfiable at any size ratio above 2 regardless
of the estimator's behaviour. The amendment is a denominator definition and does
not relax the threshold, which stays at 90%.

It is taken now because the confound it was masking has been removed and
measured. `censored_tolerance_bound` returned no bound whenever a single
truncated episode appeared in a sample, which forced the hard-coded bootstrap
caps that Pathway E's implementation item 2 exists to eliminate. It now charges
each censored episode to the tail when choosing its order-statistic rank, which
is valid distribution-free without a preregistered censoring model. Re-running
stage 06 on the Gate 1 sweep gives `calibrated == populated` in all three cells —
8/8, 4/4, 4/4 — so every remaining E-2 shortfall against the declared count is
attributable to unpopulated levels alone and to nothing about the estimator.

Two consequences are recorded with it. The whole-tree
`allowed_pending_debt_ratio` moves from the hard-coded 0.50 floor to a measured
4.141, 2.689 and 2.718 at $T=2$, 6 and 10, which removes the mechanism diagnosed
behind the 36.2% override. And the bound walks the existing
`TOLERANCE_COVERAGES` ladder under censoring: $T=6$ L1 needed rank 600 from 599
completed episodes and fell to 0.90 coverage, which yields a *tighter* limit, not
a missing one. Read `achieved_coverage` before comparing two limits.

**E-1 re-run with the censoring fix, 2026-09-14: fail, unchanged.** Holdout at
10M/T=2, seeds 10001–10003: `would_override_fraction` 0.368 / 0.366 / 0.357
against 0.362 / 0.372 / 0.362 before, `actual_interventions` 0, manifest
verified live by `baseline_slo_sha256`. The debt limit moving 0.50 → 4.141
changed nothing, which retires the debt hypothesis alongside the bridge-latency
and bootstrap-cap hypotheses.

**Measured cause: the limits are calibrated in the wrong unit.** Reconstructing
the force condition offline from `pressure_episodes.jsonl` (due age is exact at
frame time from the episode start) attributes **99.4%** of scored override
frames to `due_age >= due_age_limit_micros`. The limits are 99% tolerance
bounds on *completed episode durations* — 61.6 ms at L7, 84.9 at L6, 161.5 at
L2 — and are correct as such. But the picker tests a level's *current* due age
on every 50 ms frame, so frames sample due time, not episodes: a rare long
episode covers hundreds of consecutive frames while a short one covers one or
none (the inspection paradox). A limit at the episode p99 therefore fires on
far more than 1% of frames, and the frame fraction is what E-1 scores. This
explains the fraction being stable across two manifests, the sticky multi-hundred-
frame runs, and the separately observed ~100% override in the 1,181-frame window
before `guard_ready` (33% of the run; not scored by the validator).

**Instrument amended, recorded before the next re-run.** All three per-level
terms of the force condition — `due_age_limit_micros`,
`pressure_limit_score_micros` and `score_limit` — are now calibrated together by
`frame_simulated_limits`: every baseline run is replayed at the observation
cadence, each level's limits are the frame quantiles at a common per-level
exceedance $k/N$, and $k$ is the largest count for which the fraction of frames
where *any* level trips *any* term is $\le$ 1% — the same statistic, in the same
unit, as E-1. The three are calibrated jointly because a frame overrides on a
disjunction: fixing a subset only moves the firing onto the terms left out, and
an earlier revision of this fix that calibrated due age and pressure alone would
have handed the entire override rate to the untouched `score_limit`, which had
already co-fired on 98.7% of frames. The manifest records the prediction under
`guard_frame_simulation`, so E-1 is readable before node time is spent. The 1%
threshold is unchanged.

**The score trajectory is not logged, so it is modelled — and the model is
measured, not assumed.** For a linear ramp within an episode the peak excess is
twice the mean, so `(max_score - 1) / (integrated_excess / duration)` should be
2. Across 429,115 baseline episodes, weighted by the frames each covers, that
ratio is **1.93 to 2.50 on every level of the tree**; the median reads 1.0 only
on episodes short enough that peak and mean fall inside one observation. Score
is therefore modelled as a linear ramp from 1 to the episode's observed
`max_score`, and pressure as the integral of that ramp,
`total * (age / duration)²`, which reaches the measured
`integrated_excess_score_micros` exactly at the episode's end.

Two alternatives are reported but never used to select limits:
`predicted_override_fraction_score_flat` holds each episode at its mean excess
(lower bound) and `..._score_upper` holds it at `max_score` throughout (upper
bound). Their spread is the residual modelling risk. An earlier revision of this
fix used the flat model to *choose* limits, which the ramp measurement shows
understates the score term.

Three terms of the condition are not modelled at all, having no per-frame
record: `global_debt_breach`, `slo_force_due` and `l0_slowdown`. Each is global
rather than per-level, so no per-level limit can offset one. **That omission
turned out to decide the gate**; see the verdict below.

**Execution record.** E-1 is **recorded failed** (2026-09-14, all three cells
scored 2026-09-17); E-2 passes under an amended denominator; E-5 is satisfied
(2026-09-19). The measurements, the retraction of the offline replay's
per-level attribution, and the two corrections to the T=2 reading are in
`docs/PREREGISTRATION.md`; the narrative is history Sections 14.8 and 14.9.

## Pathway F — Dynamic SLO: phase-aware objective switching (Programme 2)

**Status: second programme, second paper.** Pathway F changes the objective per
phase, so it cannot run inside Programme 1 without a versioned amendment to the
frozen contract. It is specified here so that Programme 1's instruments (Gate 0
$\eta$/$\phi_j$ logging, the B2 phase patch, the hindsight-oracle runner, the
stress-suite manifests) are built in a form Programme 2 can reuse. Nothing in
Programme 1's acceptance depends on it.

### Purpose

On a workload whose composition changes over time — a write-heavy start, a
mixed middle, a read-heavy end — a single objective is the wrong target: the
write phase wants compaction bandwidth kept off the foreground, the read phase
wants a shallow tree. Pathway F lets the controller change *which* trade-off it
is pursuing as the workload changes, and tests whether anticipating a change is
worth more than reacting to it.

### Objective — what is per-phase and what is whole-run

Write amplification is bytes written over the whole run divided by bytes the
user wrote; deferring compaction in one phase does not remove those rewrites, it
moves them into the next phase. **W and S are therefore whole-run bounds, never
per-phase targets.** What a phase can legitimately target is what it experiences
while it runs.

| Phase class | Per-phase objective (measured inside the phase) | Whole-run constraints |
| --- | --- | --- |
| write-heavy | minimise write avg / p99 latency and `stall_seconds`; hold write throughput | $W \le W_{\text{base}} + \delta_W$, $S \le S_{\text{bound}}$ |
| mixed | the Programme 1 rule: minimise $R$ subject to the same bounds, plus per-phase latency bounds on get, scan and write | same |
| read-heavy | minimise $R$ and get avg / p99 latency; `sorted_run_seeks_per_scan` if scans are present | same |

"The most optimal point on the frontier" is not a rule; a frontier is a set of
points none of which is best. The mixed-phase rule is the frozen Programme 1
objective, not a new one. Phase classes are defined by the operation mix in a
window: write-heavy if Put $\ge 60\%$ of operations, read-heavy if Get + Seek
$\ge 80\%$, mixed otherwise; thresholds are preregistered before any F run.

### Description

**Architecture: a layer above the existing controller, not a new controller.**
The per-level trigger (protocol v2, or v3 with Pathway A's capacity action) is
unchanged. An upper layer chooses, per episode, *which SLO manifest* the lower
layer's constraint weights come from. The stress suites already define
read-heavy, write-heavy and balanced manifests; F switches between them. This
keeps every Programme 1 instrument valid and makes the ablation clean: same
lower controller, manifest fixed versus switched.

**Episodes.** The run is divided into fixed-length episodes. The episode
length is a preregistered parameter tied to the tree's reshaping time: the
time a full catch-up compaction takes at the run's scale (measured at Gate 3a-0
/ Gate 1 as the `waitforcompaction` drain duration). Shorter episodes give
noisy mix estimates and setting churn; longer ones miss transitions. Start at
one drain-time, sweep $\{0.5, 1, 2\}\times$.

**Two versions of "predict episode $n+1$ from episode $n$", and they are
different papers.** Every run starts cold (§3.2). On a workload with three
phases, the controller sees each transition once and there is nothing to learn
a forecast from.

- **F-forecast** — the workload has *repeating* structure (many episodes with a
  period, as in diurnal production traces). Predicting episode $n+1$ means
  learning the period and phase within it. Requires a workload family with real
  repetition; mixgraph phase schedules with more than ~6 periods, or replayed
  Tectonic traces.
- **F-detect** — the workload has no repetition. "Prediction" is early
  detection: notice the operation mix drifting from leading signals (Put share,
  L0 arrival rate, memtable fill rate, iterator count) and act before the tree
  has fully adapted the wrong way. This is what is achievable under cold start
  on the three-phase 500M example, and it is the default.

State which version a run is under. Do not describe F-detect as prediction in
the paper.

**Leak guard.** Paired arms must share identical shift points across repeats
(B2), so a fixed schedule is trivially learnable across runs. Under cold start
this is harmless; if cold start is ever relaxed (offline pretraining), the
schedule itself becomes the thing learned. Any relaxation of §3.2 for F must
randomise phase lengths and order per seed and must be recorded as an amendment.

**The value is in the transition, not the phase.** The tree's state carries
across phases. Knowing a read phase is coming is worth something only if the
lead time is used to compact *before* it starts — spending write bandwidth at
the tail of the write phase so the tree is shallow when reads arrive. A
reactive controller cannot do this; it is the only thing a predictor can do
that a reactor cannot. F's headline measurement is therefore how much of the
oracle's advantage a reactor already captures, and how much lead time buys.

**Switchable knobs only.** A "parameter set" may contain only settings that
change cheaply at runtime: the manifest (constraint weights and bounds),
`level0_file_num_compaction_trigger` and its slowdown/stop thresholds,
`max_bytes_for_level_base` (via `SetOptions`), `compaction_pri`, and Pathway
A's $s_i$. **The size ratio $T$ is not switchable** — changing it mid-run
reorganises the whole tree — and is fixed per run as in Programme 1.

### Theory

**Definition (three controllers over one schedule).** For a phase schedule
$p = 1..P$ with boundaries known to the evaluator:

- $J_{\text{oracle}}$: the hindsight oracle of Pathway B — best static setting
  per phase, switched exactly at the boundaries. This is the ceiling; its
  advantage over the best single static setting is $\mathcal{G}$.
- $J_{\text{react}}$: the manifest is chosen from the *current* window's
  measured mix, no lookahead, switched when the window's class changes.
- $J_{\text{pred}}$: the manifest is chosen from a predicted class for the
  *next* episode, with lead time $\ell$ used to pre-compact before a read phase.

**Proposition F.1 (Decomposition of the adaptivity value).**
*$\mathcal{G} = \big(J_{\text{static}} - J_{\text{react}}\big) +
\big(J_{\text{react}} - J_{\text{pred}}\big) + \big(J_{\text{pred}} -
J_{\text{oracle}}\big)$, where the first term is the value of reacting, the
second the value of anticipating, and the third the remaining loss to
imperfect prediction and finite lead time. Each term is measurable with the
paired evaluator.* Immediate by telescoping. The second term is the paper's
claim; if it is within noise of zero, prediction has no room on that workload
and F-detect is the complete result.

**Proposition F.2 (Lead time bounds the value of anticipation).**
*Let $\Delta_{\text{shape}}$ be the time a catch-up compaction takes to bring
the tree from its write-phase shape to its read-phase shape, and $\ell$ the
lead time a predictor provides. The anticipation term is bounded by the
read-phase cost incurred during the first $\max(0, \Delta_{\text{shape}} -
\ell)$ of the read phase under the reactive controller.* A predictor with $\ell
\ge \Delta_{\text{shape}}$ can remove that cost entirely; one with $\ell = 0$
is the reactive controller. On a *gradual* transition $\Delta_{\text{shape}}$
is spread across the transition and the reactive controller pays little, so the
anticipation term is expected to be small. On an abrupt transition it is
expected to be the whole of $\mathcal{G} - (J_{\text{static}} -
J_{\text{react}})$. **Sweep transition abruptness** as an axis; it is the
variable the result depends on.

### Implementation

1. **Manifest switching** in the Python controller: load all three stress-suite
   manifests at start; expose `active_manifest` as a per-episode field in the
   protocol response echo and in the effective-action stream, so a switch is
   attributed like any other action.
2. **Phase classifier** (F-detect): windowed operation-mix estimate from the
   existing foreground telemetry accumulator; class thresholds preregistered;
   hysteresis of one episode to prevent churn.
3. **Forecaster** (F-forecast only): a per-episode class predictor trained
   online within the run on the episode sequence; it may use only episodes
   already completed. Log its accuracy per episode as a first-class output.
4. **Pre-compaction action**: on a predicted read phase with lead $\ell$, the
   upper layer sets the lower layer's manifest to read-heavy $\ell$ before the
   predicted boundary. Attribute the resulting compactions to the upper layer.
5. **Reactive arm**: the same code path with the forecaster replaced by the
   current-window class. This is the ablation and must share every other
   setting with the predictive arm.
6. **Reuse, not rebuild**: the B2 phase patch supplies the schedule; the
   hindsight-oracle runner supplies $J_{\text{oracle}}$; the paired evaluator
   is extended with per-phase windows (latency, stall, throughput, $R$ inside
   each phase) alongside the whole-run $W$ and $S$.
7. **Scale**: develop at 50M with phases scaled proportionally (≈ 1.5 h per
   run). Run 500M (≈ 15 h per run) once per arm as confirmation, not as the
   evidence base; at 500M, five paired repeats of four arms is ≈ 300 h per
   cell.

### Acceptance criteria

| # | Criterion | Threshold | Instrument |
| --- | --- | --- | --- |
| F-1 | Phase-adaptivity gap exists | $\mathcal{G} > 0$ with 95% CI excluding 0 (this is B-4; F does not proceed without it) | hindsight-oracle runner |
| F-2 | Reacting captures a measured share | $J_{\text{static}} - J_{\text{react}}$ reported with CI as a fraction of $\mathcal{G}$ | paired evaluator, per-phase windows |
| F-3 | Anticipation adds value | $J_{\text{react}} - J_{\text{pred}} > 0$ with 95% CI excluding 0 on at least the abrupt-transition schedule | paired evaluator |
| F-4 | Whole-run bounds hold under switching | $W$ and $S$ non-inferior at $\delta_W$ / $S_{\text{bound}}$ against the best single static setting | paired evaluator |
| F-5 | No churn | manifest switches per run $\le$ number of phase boundaries + 1; per-phase latency not worse than the reactive arm by more than 2% in any phase | policy log |
| F-6 | Forecaster is honest (F-forecast only) | per-episode class accuracy reported; accuracy on a seed-randomised schedule within 5pp of accuracy on the fixed schedule | forecaster log |
| F-7 | Lead-time dependence shown | anticipation term reported against $\ell / \Delta_{\text{shape}} \in \{0, 0.5, 1, 2\}$ | paired evaluator |

F-3 is the claim. F-1 and F-2 are prerequisites that can each end the
programme honestly: no gap, or a gap the reactor already closes, is a complete
negative result about forecasting on that workload class.

---
## Execution order

Ordered to maximise information per node-hour and to fail cheaply.

### Gate 0 — Instrumentation and re-analysis (0 node-hours) — **complete 2026-09-11**

Items 1–2 are instrumentation in the RocksDB working tree (`merge_schema_version`
1 fields on `compaction_finished`; a `compaction_release` event under the DB
mutex with per-level occupancy, nominal/effective targets and capacity
generation), parsed by `compaction_measurements.py`, which `03_run_experiments.sh`
now runs on every arm. They need the rebuilt binary; no historical arm carries
them. Items 3–4 were executed by `14_gate0_reanalysis.py` on
`suite-20260902-193415`; results are in Pathway A (A-0 table) and Theorem B.1.
Record: `PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md` §14.6.

1. **$\eta$ instrument**, log-parse only: `compaction_bytes_written /
   compaction_bytes_read` per level, excluding trivial moves.
2. **$\phi_j$ logging at release time** (Theorem A.1's headroom form cannot be
   evaluated without it).
3. **A-0 decomposition** on the existing §10.7 artifacts (suite
   `suite-20260902-193415`). For each paired (`regular`, arm) at each cell, arm
   $\in$ {`prior_only`, `rl`, `unconstrained_rl`}: with $\mathcal{L}_{\text{reg}}$
   the levels the `regular` run populated,
   $D_{\text{depth}} = \sum_{i \notin \mathcal{L}_{\text{reg}}} \text{bytes}_i(\text{arm})$
   and
   $D_{\text{eager}} = \sum_{i \in \mathcal{L}_{\text{reg}}} (\text{bytes}_i(\text{arm}) - \text{bytes}_i(\text{regular}))$,
   flush bytes included at L0. Report both as fractions of the total excess
   with paired intervals; include the partial 20M cells as supporting evidence.
4. **$S_{\text{flow}}$ and $g_{\text{flow}}$ per cell** from the recorded
   `user_logical_bytes_written` and `live_logical_bytes`; recompute the Theorem
   B.1 ceiling table at measured $S_{\text{flow}}$ and $L$ and replace the
   resident-$S$ table.

- **Cost:** 0 node-hours. Items 1–2 required a C++ change (contrary to the
  original "log-parse only" wording); items 3–4 were parse-only.
- **Blocks:** Gate 1's re-analysis, Gate 3b, and the scoping of Pathway A in
  Gate 2. All three are now unblocked.

### Gate 1 — Hull₀ and space calibration (Pathway C, no learner)

12-point subset (`compaction_pri` pinned), 10M × T=2/6/10, 3 repeats to locate
the hull, retained points topped up to $\ge 5$; no static $s$ axis. Plus two
cross-$T$ `regular` cells, $T = 14$ and $T = 20$ at trigger 4, scale 1×, 3
repeats topped up to 5 if either lands on the pooled hull (seeds derive from
repeat index, so they pair with the existing repeats). From the same runs,
extract $\Delta S(\text{scale})$ per cell, per-level $\eta$, and populated $L$.

- **Pass:** C-1, C-2, C-3, C-5, C-6 for `prior_only`.
- **Decides:** whether Finding 2 is in the paper (per-$T$ and cross-$T$); which
  (cell, rung) pairs Gate 3b runs.
- **Cost:** ≈ 48 h. Baseline-only. Sequence it to run *during* Gate 2's
  implementation — it is the one block that parallelises.

**Status: complete, not passed (2026-09-19).** C-1 passes, C-5 is closed at
$s_{\max} = 2.0$, and **C-2, C-3 and C-6 are recorded failed**. Measured on
binary `deb6753c` under contract `87eaddbc`, so the hull is bound to a binary
that no longer exists and must be re-measured. Evidence and the per-criterion
reasoning are in `docs/PREREGISTRATION.md`; the narrative is history Sections
14.7 and 14.9.

### Gate 2 — Implementation (0 node-hours, longest calendar item)

Pathway A (A-Impl-1 through A-Impl-10, schema v3), Pathway D (Lagrangian
reward, $\Phi$ discounting audit, stabilisation), Pathway E (shield symmetry
including $s_i$ contraction, recalibration), B1 (mixgraph skew — a flag change),
and **B2 + B3 as a single `db_bench` patch**.

**Why a `db_bench` patch, not Tectonic.** Every gate instrument — manifest
generation, guard calibration, oracle parity, the paired evaluator (§11) — is
built on `db_bench`; a second experiment surface would need that chain ported
and revalidated at 10M. `mixgraph` reads `FLAGS_mix_{get,put,seek}_ratio` once
into a `QueryDecider` at benchmark start; making that a schedule that
re-initialises the decider at preregistered operation counts is tens of lines,
keeps one surface, and preserves pairing and fingerprinting. B3's fourth
operation type touches the same class.

**Trap: `QueryDecider::Initiate` appends, it does not reset.** It calls
`type_.push_back` and `ratio_.push_back` with no `clear()`; only `range_` is
zeroed. Re-calling it mid-run leaves stale entries and the query lookup selects
against stale boundaries. The failure is silent: the run completes with wrong
mix ratios. Fix is two lines (clear both vectors); add an assertion on
`type_.size()` after each re-initialisation and a per-phase op-count check in
the summary. Verify the function body against the pinned commit.

**Hindsight-oracle sweep** ($\mathcal{G}$) is Gate 1's sweep re-run at each
phase mix; use 2 phases until a nonzero $\mathcal{G}$ is shown, and reuse Gate 1
points wherever a phase mix coincides with one already swept.

- **Cost:** 0 node-hours; price in days when planning.

### Gate 3a-0 — Calibrate 3a's parameters (~2 h)

$\kappa = 3.00$ and $S = 2.271$ are 10M/T=2 measurements. Run `regular` and
`prior_only` at 1M/T=2, 3 repeats, to measure $\kappa_{1M}$, $S_{1M}$,
$\eta_{1M}$ and $L_{1M}$; set A-1a's relaxed bound to the $S$ the measured
$\Delta S(s)$ curve requires to reach $s = 1.10\,\kappa_{1M}$. **Verify the "six
leveled levels at 1M" claim** from `levelstats` before committing to 1M/T=2 as
the mechanism cell. 3a needs its own manifest at the relaxed bound: the
per-arm gate at `03_run_experiments.sh:335` keys off arm name, so a learned arm
demands a manifest regardless of `RL_REQUIRE_BASELINE_SLO=0` — a full
sweep → select → guard chain at 1M.

### Gate 3a — Mechanism (~1 h)

1M × T=2 at the calibrated relaxed bound, 3 repeats, logging $\phi_j$ at
release time. Diagnostic only.

- **Pass:** A-1a, A-4, A-5.

### Gate 3b — Acceptance (the track decision)

10M, uniform workload, 5 repeats, `rl` with and without the capacity action, at
the (cell, rung) pairs selected by the preregistered rule in Pathway A, rungs
from the P0-6 ladder $\{0, 2, 5, 10\}\%$. Every rung run is reported; the bound
is not widened after seeing a result. The one expectation that survives both
Theorem B.2 brackets is that $T{=}2$ is closed at any reasonable budget. If one
cell survives at one rung, accept a single-cell result and say so plainly.

- **Pass:** A-1b, A-2, A-3, A-5, at each rung run.
- **Decides:** research track (A-1b passes at the 2% rung, or at a rung
  preregistered as the headline before 3b) vs. Experiments & Analysis track
  (A-1b fails at every rung).
- **Cost:** up to 2 cells × 4 arms × 5 repeats = 40 runs ≈ 14 h per rung; the
  `regular` and `prior_only` arms are shared across rungs, so each extra rung
  costs ≈ 7 h per cell. Budget 14–35 h.

### Gate 3c — Hull$_s$ (Pathway C, after Gate 2 and Gate 3b)

Static $s \in \{1.0, 1.5, 2.0\}$ only at cells surviving Gate 3b, plus the
$f_i \propto \eta^{-i}$ profile at measured $\eta$ (Corollary C.3). Gate 1's
$s{=}1.0$ points are reusable (same cells, same seeds).

- **Pass:** C-4 becomes decidable.
- **Cost:** 12 × 2 new $s$-values × 2 cells × 3 repeats = 144 runs ≈ 48 h,
  plus ≈ 8 h for the profile points ≈ **56 h**.

### Gate 4 — Garbage and phase adaptivity (Pathway B)

Off the critical path (Corollary B.4). Keeps T=2 for the **baseline arms only**
— $g$, $\eta$ and $\mathcal{G}$ are properties of the workload and the static
class, and T=2 carries the largest garbage fraction; `rl` is excluded at T=2
where it would inflate depth.

- Baseline arms: 4 families × 3 T × {`regular`, `prior_only`} × 5 = 120 runs
  ≈ 40 h.
- `rl` arm: 4 families × 2 T × 5 = 40 runs ≈ 13 h.
- Hindsight oracle: 12 configs × 2 phases × 2 T × 3 = 144 runs ≈ 48 h.
- **Cost: ≈ 101 h.** **Pass:** B-1 through B-5. **Decides:** which cells are
  analytically open under Theorem B.1 at measured $S_{\text{flow}}$ and $L$.

### Gate 5 — Frontier at ten repeats (Pathways C + D, ~27 h)

Headline cells only — those surviving Gate 3b and B-2 — at the preregistered
ten paired repeats, constrained objective, $S_{\text{bound}}$ swept. Produces
the paper's principal figure.

- **Pass:** C-4, C-6, D-1 through D-5.

### Gate 6 — Ablations (~8 h)

Capacity action on/off, shield on/off, prior/residual/both, shared trunk vs
independent heads. Three repeats each; ablations, not claims.

- **Pass:** E-4.

Run 20M only if a 10M result is close enough that scale could plausibly flip it.

### Total budget and lease boundaries

| Gate | Cost |
| --- | ---: |
| 0 — instrumentation and re-analysis | 0 (off-box) |
| 1 — Hull₀ + space calibration + cross-$T$ cells | 48 h |
| 2 — implementation | 0 node-hours (longest calendar item) |
| 3a-0 + 3a — mechanism at 1M | 3 h |
| 3b — acceptance, at the $S_{\text{bound}}$ ladder | 14–35 h |
| 3c — Hull$_s$ | ~56 h |
| 4 — garbage and phase adaptivity | 101 h |
| 5 — frontier at ten repeats | ~27 h |
| 6 — ablations | ~8 h |
| **Total** | **~257–278 h ≈ 11–12 days** |

A seven-day lease is 168 h, so this is two leases minimum; results must be
copied off-box before each lease ends.

- **Lease 1 — decides the track.** Gates 1, 3a-0, 3a, 3b ≈ 65–86 h, leaving
  margin for the reruns Gate 3b failures would require.
- **Lease 2 — produces the headline.** Gates 3c, 5, 6 ≈ 91 h.
- **Lease 3 — extends it.** Gate 4 ≈ 101 h. Separable: the parity claim does
  not depend on B-2 or B-4.

If only two leases are available, cut Gate 4; the claim narrows from
parity + non-domination + phase-adaptivity to parity + non-domination +
**state**-adaptivity, which D-5 delivers without Gate 4.

---

## Programme 2 execution order (Pathway F)

Runs only after Programme 1's Gate 4, which supplies $\mathcal{G}$ and the
phase patch, and after the Programme 1 paper's claim is recorded.

### Gate F-0 — Reactive arm and per-phase evaluator (0 node-hours)

Implement items 1, 2, 5 and 6 of Pathway F. Extend the paired evaluator with
per-phase windows. No learner change below the manifest layer.

### Gate F-1 — Reactor versus oracle (reuses Gate 4 runs)

Add the reactive arm to Gate 4's 2-phase schedule at 50M, two abruptness
settings (gradual, abrupt), 5 paired repeats. Report F-1 and F-2.

- **Decides:** whether anticipation has room. If $J_{\text{react}}$ is within
  noise of $J_{\text{oracle}}$ on both schedules, stop; publish F-detect as the
  result.
- **Cost:** 2 schedules × 2 arms × 5 repeats × ≈ 1.5 h ≈ **30 h** beyond Gate 4.

### Gate F-2 — Lead-time sweep with a perfect predictor

Before building a forecaster, give the predictive arm the *true* next-phase
class at lead $\ell \in \{0, 0.5, 1, 2\} \times \Delta_{\text{shape}}$. This
measures the ceiling of anticipation independent of prediction quality (F-7).

- **Cost:** 2 schedules × 4 leads × 5 repeats × ≈ 1.5 h ≈ **60 h**.
- **Decides:** the lead a real forecaster must deliver to matter. If no lead
  beats $\ell = 0$ outside noise, the forecaster is not built.

### Gate F-3 — Forecaster (F-forecast only)

Only on a workload family with repetition (≥ 6 periods). 3-phase schedule,
seed-randomised phase lengths and order, 5 repeats. Report F-3, F-5, F-6.

- **Cost:** ≈ **45 h** at 50M.

### Gate F-4 — 500M confirmation

One run per arm (static, reactive, predictive, oracle) on the three-phase 500M
schedule. Confirmation only; not the evidence base.

- **Cost:** ≈ **60 h**.

| Gate | Cost |
| --- | ---: |
| F-0 | 0 |
| F-1 | 30 h |
| F-2 | 60 h |
| F-3 | 45 h |
| F-4 | 60 h |
| **Total** | **≈ 195 h**, one to two leases, after Programme 1 |

---

## Global acceptance: what constitutes a paper

**The research-track claim is non-inferiority on $W$, not improvement.** Geometry
permits only $O((1-\eta)^2)$ below parity (static, Hull$_s$'s); elision can add
more, bounded by cumulative garbage, but no arm has yet shown it. The claim the
evidence supports: *write amplification non-inferior at margin $\delta_W$,
point-read amplification strictly improved, non-dominated against Hull$_s$ and
against the cross-$T$ hull, with state-dependent policy behaviour.* It requires
neither B-2 nor B-4. Improvement on $W$ is a bonus governed by measured $\eta$.
Record which claim is being made before Gate 4; the two select different
acceptance sets and cannot both stand.

**A-2 is not Pathway A's to claim alone.** By A-0 the write excess is mostly
the prior's eagerness. If A-2 passes, attribute it to expansion only for the
$D_{\text{depth}}$ share and to the constrained reward for the rest, and report
what the read gain cost to buy it.

**Research track** requires: A-0, A-1b, A-2, A-4, C-4, C-6, D-5. B-2 only if
the claim is improvement rather than non-inferiority.

**Experiments & Analysis track** requires: A-1b, or a documented refutation of
A3 with $\phi_j$ logged at release time; B-1 through B-3 across all workload
families; C-1 through C-3 and C-6 against Hull₀. A refutation of Theorem A.1's
assumptions is itself publishable if the measurement is clean. A third E&A
outcome is available: a measured $\Delta S(s)$ curve showing that depth
invariance at the observed deferral factors is incompatible with any reasonable
space budget, with the analytical bracket explaining why — a complete negative
result about capacity-mediated deferral that does not depend on the learner.

**Neither is reachable** if Gate 1 shows `prior_only` on or inside Hull₀
(per-$T$ or cross-$T$) *and* Gate 3b shows depth not flattening at any surviving
pair. Then the analysis stands but the system contributes nothing, and the
honest output is a short paper on the conservation result, the elision bound,
and the measured space cost of deferral.

---

## Frozen preregistered decisions

Moved to `docs/PREREGISTRATION.md` on 2026-09-20, together with the dated gate
verdicts that had accumulated inside Pathway E and Gate 1. This document is the
theory, the specification and the done/not-done status; that one is the dated
record of what was decided, when, and what was predicted before each run.
