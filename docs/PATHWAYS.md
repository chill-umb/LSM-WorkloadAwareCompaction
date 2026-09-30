# Improvement Pathways: Specification, Proofs, and Acceptance Criteria

**Revision:** 2026-09-29 (research fork). Supersedes the 2026-09-11 revision and
every status block added to it up to 2026-09-23. The narrative of what happened
is `PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md`; dated decisions and verdicts are
`docs/PREREGISTRATION.md`; the audit this revision acts on is
`docs/AUDIT_2026-09-23_D12_AND_PATHWAY_A.md`.

**Target venue:** not yet chosen for this fork.

**Scope.** *Programme 1* (this revision) is a per-level reinforcement-learning
compaction-trigger controller for leveled RocksDB. It minimises a priced cost of
write, read and space amplification under a chosen priority (reads, writes or
space), acting only through RocksDB's own compaction scores, so that RocksDB
keeps sole authority over which files are compacted. *Programme 2* (deferred)
is SLO-constrained operation: the constrained objective, the guard (Pathway E)
and phase-aware objective switching (Pathway F).

**Status.** Specification. No arm has run under this revision. Every result
below is either proved from its stated assumptions or explicitly labelled a
conjecture, an approximation or a measured fact with its source. Results of the
2026-09-11 programme are summarised in Appendix R and are not re-scored.

**Reading order.** §0 (what changed) → §1 (notation and assumptions) → Pathway D
(objective and cost model) → Pathway A (actuation) → Pathway G (propagation
across levels) → Pathway H (RL architecture) → Pathway B (workloads) → Pathway C
(comparator) → Pathways E and F (Programme 2) → execution order → global
acceptance → references → Appendix R.

**Referencing convention.** Numbered results use a dot (Lemma D.7,
Theorem A.1). Sections inside a pathway use a section sign (D §3, H §5).
Acceptance criteria use a prefix per pathway: retained criteria keep their
2026-09-11 names (B-1, C-1, C-2, C-6, E-*, F-*); new criteria use new prefixes
(OBJ, ACT, PROP, ARCH, WL, CMP) so that no new criterion reuses the name of one
already scored in `docs/PREREGISTRATION.md`.

**RocksDB version.** Upstream base
`7ea2d73655025332855864f0b8d6d2dbdad3f336` (contract v3, `rocksdb_base`). The
contract's last recorded fork revision is `6ad9f6b79` (parent `71627a1cd`); the
root branch records `25468bbaa`, three fork commits later. (`81d2742bf`, pinned
2026-09-12, is contract v2's revision.) Every RocksDB citation in this revision
is to `25468bbaa`, read with `git -C lib/rocksdb show 25468bbaa:<path>`, since
the submodule's working tree can lag the recorded commit. Programme 1 adds one
option (Pathway A), so its binary differs from every earlier one and every
static measurement is regenerated on it (Pathway C). Every cited line number and
option semantic must be re-verified against the commit that binary is built
from.

**Build and I/O.** Unchanged from 2026-09-11: `-march=znver5` through RocksDB's
`PORTABLE` variable, binary SHA-256 inside `experiment_fingerprint`. Direct I/O
is pinned **off** for development (a 21× throughput penalty was measured) and
fingerprinted as `dio0`. Paper runs may use `dio1`; they then form their own
comparator chain and are never pooled with `dio0` runs. Latency figures under
`dio0` are warm-cache and are reported as diagnostics only.

---

## 0. What changed in this revision, and why

### §0.1 The premise behind Pathway A was an artifact of the controller

The 2026-09-11 revision built Pathway A (capacity co-design) so that the
controller could defer compaction "without the depth cascade" measured in
history §10.7, where learned arms deepened the tree by one to three levels and
paid for it in reads. That depth growth came from the controller that produced
it, not from deferral as a mechanism.

- **The learning setup was never re-derived when the objective changed.** The
  project's goal moved from latency and stall reduction, to a strict three-way
  improvement of write, read and space amplification, to "reduce reads with at
  most 2% more writes". The reward, state and horizon were carried forward
  through both changes. The reward priced write amplification, probes, scan
  work and latency, and treated space as a quantity to minimise (history §6.3).
  The state had eleven stall-era pressure inputs out of 31 and no
  write-amplification input, and the discount gave a horizon of about 20 s
  (history §14.20, findings 1–6).
- **Two different mechanisms produced the depth growth, and neither was
  deferral as such.** On the uniform workload (2026-09-03) the learner deferred
  top-of-tree compaction while being rewarded for lower space (history §10.7,
  Finding 3). On `Assoc` (D-7) it compacted L1 and L2 at about 40% of target,
  which the action space allowed (history §14.20, finding 3).
- **With the setup realigned (D-9 to D-12), depth did not grow.** Populated
  depth equalled the static twin's in all 18 D-12 arms (audit §3).
- **Pathway A's own measurement already said depth was a minor write cost.**
  Depth accounted for at most 35% of the learner's extra writing (A-0,
  Appendix R).

**Consequence.** Pathway A's motivation is withdrawn. Its mechanism —
changing a level's effective capacity through its compaction score — survives as
one of the controller's actions (Pathway A), with its theory corrected.

### §0.2 What D-12 and the audit established

- **The first run with correct instruments found no read gain at write parity**
  on `Assoc` at 10M. At T=2 all of the extra writing (+0.68 GB per run) fell in
  the first 30 s after the controller resumed, before the first gradient step
  (38–42 s), while random exploration deferred due levels during the post-load
  backlog. After 30 s the learner wrote 0.07 GB *less* than native. Part of the
  T=2 deficit was session drift: the same static setting writes 2.9–4.1% more
  in later sessions.
- **The constrained objective had almost no room at its comparators.** At T=2
  and T=10 the comparator was already the lowest-read static setting within +2%
  write, and at T=10 the lowest-read of all 38 static settings (audit §2).
- **Stale answers were real but not the cause.** 27–39% of controller answers
  were rejected over a run because the tree had changed since the snapshot. The
  prior-only arm makes the same round trip and matched native at T=10 (audit §6).
- **Several instruments carried the post-load transient:** the write and scan
  multipliers and the guard's limits. The guard's conditional override rate was
  6–17 times its limit over the whole run and 0–1.4% after the first 30 s.
- **The Double-DQN target ignored the next state's action mask** (`agent.py`
  `train_step`); its effect was never measured.
- **Static capacity expansion never lowered write cost** in 27 runs (merges out
  of L0 grew 34–74%), and lowered read cost only by removing a level.

### §0.3 The objective changes

The constrained objective — minimise point-read amplification subject to write
parity at a 2% margin and to space, latency and stall bounds — is replaced by a
**priced cost with a priority mode** (Pathway D). The controller trades the three
amplifications at stated prices and weights the prioritised one by a factor
$\beta^\star$. There are no hard limits. Constraints, the guard and latency
bounds move to Programme 2.

### §0.4 Fixes adopted from the parallel stream

- **Measure only a settled tree.** After the load, no operation is issued
  until compaction has caught up and the tree has stayed settled for a hold
  window; the measured phase and the controller both start at the first
  `mixgraph` operation (H §5). This removes the start-up exploration damage of
  D-12, and every `mixgraph` operation is scored.
- **In-process inference.** Inference runs in C++ inside the RocksDB process;
  Python only trains, and pushes weights at a fixed period (H §6). No answer can
  be stale by more than one decision.

### §0.5 Corrections to the 2026-09-11 revision

| # | 2026-09-11 statement | Correction | Where |
| --- | --- | --- | --- |
| 1 | Pathway A lets the controller "defer without the depth cascade" of §10.7 | The cascade was produced by the misaligned controller and was absent in D-12 | §0.1 |
| 2 | Theorem A.1: a released burst is $\kappa C_i$ | The burst is $(\kappa-1)C_i$ at $m_i=1$; in general $(\varphi_i - m_i)^+C_i$ | Theorem A.1 |
| 3 | Theorem A.2(iii): the survival-weighted optimal profile | Needs some multipliers below 1, which $s \ge 1$ could not express; multipliers below 1 are now allowed. The profile is static | Theorem A.2 |
| 4 | Corollary A.3: removing a level needs $s = T$ | Premise failed: $s=2$ removed a level at T=10. Superseded: depth cannot fall during a run, so removal is static | Lemma A.6, Corollary A.7 |
| 5 | Corollary A.4 and P0-7: write-accounting convention M3 | Replaced by an exact accounting identity whose one modelled quantity, the overlap constant, is measured | Lemmas D.7, D.8 |
| 6 | Theorem B.1: elision ceiling on $W-1$ | The proof evaluates a constant-survival formula at a pooled floor, which the floor does not justify. In the stage model without trivial moves the saving is limited by the garbage available: about 26–32% of whole-run $W-1$ on `Assoc` at T = 2, about half the formula's 58%. In the measured phase the budget is weak, native already drops most of it, and where drops happen binds | Propositions B.1′, B.1″ |
| 7 | B.1 and B.2 tables, $\kappa$ values | Measured on the uniform workload with the defective live-data estimate (D-3); replaced by measured values | Pathway B |
| 8 | §B and §D: cumulative garbage "is large"; §C: "almost no resident garbage" | Resolved by D-3: on `Assoc`, resident $S$ is 1.03–1.09 and $g_{\text{flow}} = 0.278$ | Pathway B |
| 9 | Commentary on Proposition B.3: a state-dependent policy "can strictly beat every member of $\Theta$" | Unproven; no learned or hand-written policy on record lies below the static frontier | Conjecture B.3′ |
| 10 | Gate 3b runs on the uniform workload | Contradicted D-1 (every gate on `Assoc`) | Execution order |
| 11 | D-4: $\lambda_W$ diverges where the write constraint is infeasible | Could never apply, since native RocksDB always meets its own bound; retired with the constrained objective | Appendix R |
| 12 | Gate costs | 5–7 times too high (audit §4); new gates are priced by run count once run length is fixed | Execution order |
| 13 | D-12 verdict: the T=2 extra writing is a learned deferral lever | Start-up exploration during the backlog plus session drift | Appendix R |
| 14 | Proposition F.2 bounds the anticipation term by the read-phase cost over the first $\max(0, \Delta_{\text{shape}} - \ell)$ | That quantity is the loss a predictor still leaves, not the anticipation term; restated with proof | Proposition F.2 |

### §0.6 Dated decisions this revision requires before any run

These belong in `docs/PREREGISTRATION.md`, each dated and committed before the
run it governs. This document states the theory; it does not substitute for the
dated record.

1. The objective change (§0.3), the headline priority factor $\beta^\star$,
   the reference operation rate $\bar q$ per workload, and the two money
   prices the others are converted with: the instance price per device-second
   and the storage price $c_s > 0$ per byte-second (D §1).
2. An amendment to P0-7 (write accounting): exact identity plus measured
   overlap constant.
3. The actuation option and its bounds (Pathway A).
4. The workload suite and its fingerprint fields (Pathway B).
5. The static comparator class $\Theta_s$ (Pathway C).
6. The settle rule and its hold window $h_w$ (H §5).
7. Run length, and the admission test's fixed parameters: reference level,
   statistics, margins $\delta_{\text{adm},s}$ and $\omega_{\max}$, block length
   $b$, minimum turnovers per level $n_{\min}$ (Gate N1), and PROP-1b's
   one-step model and margin $\delta_{\text{kern}}$.
8. Repeat counts per cell (C §3).
9. Retirement, for this fork, of P0-1 as a constraint (scans are now priced),
   P0-3, P0-6, P1-15, P1-16, P1c-22 and P1c-23; P0-4 (latency) moves to
   Programme 2.
10. The claim the paper will make (Global acceptance), recorded before Gate N5.
11. Whether the neighbour charge of H §3 uses the neighbour's full value or a
    head fitted to its attributed cost alone (the echo).
12. The stall rule's margins $\delta_{\text{stall}}$ and $\delta_{\text{thr}}$
    (Global acceptance).

---

## 1. Notation and standing assumptions

### §1.1 Notation

| Symbol | Meaning | Source |
| --- | --- | --- |
| $L$ | index of the last populated level; levels are $0..L$ | `levelstats` |
| $T$ | size ratio between adjacent level targets | `max_bytes_for_level_multiplier` |
| $C_i = C_1T^{i-1}$ | nominal target of level $i \ge 1$; $C_1$ = `max_bytes_for_level_base` | options |
| $m_i$ | target multiplier of level $i$; $m_0 \equiv 1$ | `level_target_multipliers` (Pathway A) |
| $K_0$ | L0 compaction trigger (file count) | `level0_file_num_compaction_trigger` |
| $k_0(t)$, $F$ | L0 file count; flush file size | telemetry |
| $F_{\text{sst}}$ | SST target file size (`target_file_size_base`, 512 KiB here) | options |
| $B_i$ | bytes at level $i$ not being compacted | telemetry |
| $\varphi_i = B_i/C_i$ | fill of level $i$ against its nominal target | derived |
| $s_i = \varphi_i/m_i$ | compaction score of level $i \ge 1$ | RocksDB |
| $S, O, X$ | per compaction: source bytes, overlap bytes read from the level below, output bytes | event log |
| $\rho_i = (X-O)/S$ | pass-through of level $i$: net bytes landing below per source byte of a merge, summed over merges with trivial moves excluded; 1 if nothing is dropped | event log |
| $o_i = O/S$ | overlap ratio of level $i$ | event log |
| $d = S + O - X = (1-\rho_i)S$ | bytes dropped by one compaction | event log |
| $\lambda_i$, $\mu_i$ | inflow to level $i$ (net bytes added by compactions from above) and outflow (source bytes leaving), bytes/s | event log |
| $\bar\lambda_i$ | mean inflow over a window | derived |
| $\tau_i = C_i/\bar\lambda_i$ | turnover time: time for the inflow to deliver one nominal level of bytes | derived |
| $\theta_i = t/\tau_i$ | the level's own clock, in turnovers | derived |
| $a_i$ | source bytes leaving level $i$ per user byte, by merge or by trivial move ($a_0$: bytes leaving L0) | derived |
| $a_F$ | flush bytes per user byte; $a_F = a_0$ unless an intra-L0 compaction drops entries | derived |
| $t_i$, $\xi_i = t_i/a_i$ | bytes leaving level $i$ by trivial move (a file relinked into level $i+1$ without being rewritten) per user byte, and their share of $a_i$ | event log |
| $\tilde\rho_i = \xi_i + (1-\xi_i)\rho_i$ | net pass-through of level $i$, trivial moves included; $= a_{i+1}/a_i$ in steady state | derived |
| $\pi_i = \prod_{k=1}^{i-1}\tilde\rho_k$ | share of L1's inflow that reaches level $i$; $= a_i/a_1$ in steady state | derived |
| $f_i$ | effective fanout: $f_0 = m_1C_1/(K_0F)$; $f_i = Tm_{i+1}/m_i$ for $1 \le i \le L-2$; $f_{L-1} = B_L/(m_{L-1}C_{L-1})$ | derived |
| $c_i = o_i/f_i$ | overlap constant of level $i$ | measured |
| $W$ | write amplification: (flush + compaction bytes written) / user bytes written | evaluator |
| $R_f$ | filter probes per Get (the 2026-09-11 "point-read amplification") | existing counter |
| $R_{blk}$ | probes per Get that pass the filter and read a data block | Pathway D |
| $R_{sk}$ | sorted runs seeked per scan (P1c-20) | existing counter |
| $S$ (metric) | settled SST bytes / garbage-free bytes (D-3) | evaluator |
| $H(t)$ | SST bytes of the current version (`rocksdb.live-sst-files-size`): a running compaction's inputs count until it installs and its outputs from then on; obsolete files still pinned by older versions or awaiting deletion do not count | telemetry |
| $u$, $q_{pt}$, $q_{sc}$ | user bytes written, Gets and scans, per second | telemetry |
| $q$, $\bar q$ | all operations per second; a reference operation rate, fixed in advance per workload and recorded with the prices (OBJ-2), since it sets the weight of space against the other terms | telemetry; preregistration |
| $N_i = C_i\,q/\bar\lambda_i$ | operations served per turnover of level $i$ ($q$ and $\bar\lambda_i$ over the same window) | derived |
| $c_w, c_f, c_{blk}, c_{sk}, c_s$ | prices: per byte written, per filter probe, per block-reading probe, per run seek, per byte held per second | measured once per machine |
| $\beta = (\beta_W, \beta_R, \beta_S)$ | priority weights; $\beta^\star > 1$ is the factor on the prioritised term | run configuration |
| $J_\beta(\pi)$ | priced, priority-weighted cost of policy $\pi$ over the measured phase | Pathway D |
| $\mathcal C_W, \mathcal C_R, \mathcal C_S$ | unweighted priced run costs of writes, reads (three terms together) and space; $J_\beta = \beta_W\mathcal C_W + \beta_R\mathcal C_R + \beta_S\mathcal C_S$ | Pathway D |
| $\Theta_s$ | the static configuration class | Pathway C |
| $\theta^\star_\beta$ | the static configuration minimising $J_\beta$ on a workload | Pathway C |
| $S_{\text{flow}} = N_{\text{written}}/N_{\text{live}}$, $g_{\text{flow}} = 1 - 1/S_{\text{flow}}$ | flow space ratio and cumulative garbage fraction | as 2026-09-11, measured denominator (D-3) |

### §1.2 Standing assumptions

- **A1 (static ladder).** Leveled compaction with
  `level_compaction_dynamic_level_bytes = false`; the nominal targets $C_i$ are
  fixed for the run. The multiplier option is rejected otherwise (Pathway A).
  This deviates from the RocksDB default since v8.4 and is declared in the paper.
- **A2 (one run per level).** Levels $\ge 1$ each hold one sorted run, so one
  version per key per level; L0 holds $k_0$ overlapping runs. Exceptions to
  state in the paper: intra-L0 compaction, subcompactions, trivial moves, SST
  ingestion.
- **A3 (eligibility).** Level $i \ge 1$ is eligible for compaction iff
  $s_i = B_i/(m_iC_i) \ge 1$. RocksDB ranks eligible levels by score, and
  admission depends on free compaction slots. Results use eligibility only
  unless they say otherwise.
- **A3′ (L0).** L0's score is
  $\max(k_0/K_0,\; \text{L0 bytes}/C_1)$. Multipliers do not enter it. The byte
  branch caps the effective trigger at about $C_1/F$.
- **A4 (downward movement).** Compactions move data from level $i$ to $i+1$, or
  within L0 or within the last level; never upward.
- **A5 (fluid approximation).** Where a result treats flows as continuous it
  says so; per-compaction granularity is noted where it matters.
- **A6 (entries).** Unless stated otherwise: fixed entry size, no deletes or
  tombstones, and compression that does not depend on how entries are grouped
  into files.
- **A7 (steady state).** A window in which each level's mean fill is constant,
  so that each level's mean inflow equals its mean outflow.
- **A8 (same-phase measurement).** All compared arms are scored over the same
  operations — every `mixgraph` operation, from the first ($n_w$) to the last,
  then the drain — with same-session twins (C §3). Each arm reaches $n_w$ on
  the settled tree its own load left (H §5); an arm not settled by then is
  invalid. Costs are totals over these operations.

---

## Pathway D — Objective: a priced cost with a priority mode

### §0 Purpose, and the model this pathway starts from

This pathway defines what the controller minimises, how "prioritise reads",
"prioritise writes" and "prioritise space" are expressed, and the cost model the
rest of the document uses. It replaces the constrained objective of the
2026-09-11 revision. Of that revision's Pathway D results, the shaping result
is retained as Proposition H.4; the hinge and two-timescale results are retired
with the constraints.

**The starting model.** The draft of 2026-09-29 adapted the cost form of
Dostoevsky's Fluid LSM-tree [Dostoevsky]. With $K$ runs per upper level, $Z$ at
the last level and $L$ levels:

$$\text{RA} = K(L-1) + Z,\qquad \text{WA} = \frac{T}{K}(L-1) + \frac{T}{Z},\qquad \text{SA} = \frac{ZT}{T-1},$$
$$C = w\cdot\text{WA} + r\cdot\text{RA} + s\cdot\text{SA},$$

where $w$ is the ingest rate times the cost of writing one byte, $r$ the lookup
rate times the cost of checking one run, and $s$ the live data size times the
cost of holding one byte for one second. The prices turn three quantities in
different units into one currency (device time per second, or money per
second [Cosine]).

*Check of the plotted example.* With $Z = K$ and $s = 0$,
$C = wTL/K + rKL$, minimised at $K^\star = \sqrt{wT/r}$. At $T = 16$ and
$w = r = 1$ this gives $K^\star = 4$; with $L \approx 3.33$, $\text{WA} = \text{RA}
= 13.3$ and $\text{SA} = 4\cdot16/15 = 4.27$, matching the plot.

**Five issues, and where each is fixed.**

1. **The controller's knobs do not change $K$.** Every level $\ge 1$ of leveled
   RocksDB holds one run (A2), so $K = Z = 1$ whatever the multipliers are. $K$
   exists only at L0, where the trigger $K_0$ plays its role. *Fixed* by
   writing the model in the actual knobs: $K_0$, the multipliers, and depth
   (§3).
2. **RA priced every run check as a random page read.** With Bloom filters,
   most checks are an in-memory filter probe. $K(L-1)+Z$ is right for scans,
   which seek every run, and for lookups without filters. *Fixed* by pricing
   filter probes, block-reading probes and scan seeks separately (§1,
   Lemma D.10).
3. **The write term used one convention (T per level) and the frozen P0-7 used
   another (M3).** *Fixed* by an exact accounting identity whose only modelled
   quantity, the overlap constant, is measured (Lemmas D.7, D.8).
4. **SA is a worst case.** $ZT/(T-1)$ assumes every upper-level entry updates a
   key in the last level: 2.0 at T=2, where `Assoc` measures 1.07–1.09.
   *Fixed*: the evaluation uses measured space and the per-level reward the
   shadowed-garbage estimate of §4 (OBJ-3); the model is used only as a bound
   (Lemma D.14).
5. **"Min-max" named a weighted sum.** *Fixed*: the objective is a priced cost
   with a priority factor (§2). The true min–max form appears only where it
   belongs: the Chebyshev alternative (Remark D.6) and suite robustness
   (Definition C.5).

### §1 Metrics and prices

**Metrics**, all over the measured phase (A8):

| Metric | Definition | Instrument |
| --- | --- | --- |
| $W$ | (flush + compaction bytes written) / user bytes written. SST output only (flush `table_file_creation` sizes, compaction `total_output_size`): the OPTIONS file every `SetOptions` call writes (A-Impl-5), and MANIFEST, WAL and info-log writes, are not counted | event log, as D-11 |
| $R_f$ | filter probes per Get: tables whose key range covers the key and whose filter is consulted, including filter-rejected ones | existing counter |
| $R_{blk}$ | probes per Get that pass the filter and read a data block (true plus false positives) | `rocksdb.bloom.filter.full.positive`; per level through counters keyed by the version's level (§4, Gate N0) |
| $R_{sk}$ | sorted runs seeked per scan (P1c-20) | existing counter |
| $S$ | settled SST bytes / garbage-free bytes (D-3), at run end | evaluator |
| $\mathcal C_S$ | $(c_s/\bar q)\sum_n H(n)$ over the measured phase, computed exactly as the sum, over the intervals between version installs, of $H$ times the operations served in the interval; $H$ is sampled with the operation count at every install, on every arm, native included | telemetry, evaluator |

Block-reading probes are counted logically, whether or not the block was cached,
so the metric does not depend on the page cache.

**Prices.** One currency throughout: money, as in Cosine [Cosine]. $c_w$ per
byte written; $c_f$ per filter probe, a CPU cost that is not negligible on fast
storage [Zhu et al.]; $c_{blk}$ per block-reading probe; $c_{sk}$ per run seek:
each is the device time the operation takes, measured once per machine,
converted at a fixed instance price per device-second. $c_s$ per byte held per
second is a storage price, and $c_s > 0$ always. At $c_s = 0$ the space term
vanishes: space priority becomes balanced mode, and in every mode expanding
levels and holding garbage cost nothing. The two money prices are fixed in
advance (§0.6) and every price is recorded in the fingerprint (OBJ-2). Since
the weight of space against the other terms is set by a price choice, results
are also reported at $c_s/2$ and $2c_s$. The operation rates $u$, $q$,
$q_{pt}$ and $q_{sc}$ are measured online; the reference rate $\bar q$ is fixed
in advance per workload (§0.6).

**Cost rate.**
$$c(t) = c_w\,\dot w(t) + q_{pt}(t)\big(c_f R_f + c_{blk}R_{blk}\big)(t) + q_{sc}(t)\,c_{sk}R_{sk}(t) + c_s H(t)\,\frac{q(t)}{\bar q},$$
where $\dot w$ is bytes written per second. Relation to the draft: $w\cdot
\text{WA} = c_w u W$; $r\cdot\text{RA}$ becomes the three read terms;
$s\cdot\text{SA} = c_s N_{\text{live}}S = c_s H$ at the reference rate.

**Space is charged per operation served, not per second.** Every arm serves the
same operation sequence (one client thread, a fixed seed, `mixgraph` stopped by
operation count) and is scored over the same operations (A8), so the write and
read terms integrate to counts that do not depend on how long the run takes. A
space term of $c_sH$ per second would not: a run that takes longer holds the
same live bytes for longer and pays for it. With the factor $q/\bar q$,
$\int c_sH\,(q/\bar q)\,dt = (c_s/\bar q)\sum_n H(n)$, where $H(n)$ is $H$ when
operation $n$ is served. $H$ changes only when a version is installed, so the
sum is computed exactly as the sum, over the intervals between installs, of $H$
times the operations served in the interval. Space is priced by what is held
while the store serves each operation (Lemma D.15). $c_s$ keeps its meaning —
the price of holding one byte for one second — when the store serves $\bar q$
operations per second.

$J_\beta$ therefore contains no time. Stalls, slowdowns, foreground throughput
and the controller's own overhead are not priced in Programme 1; they are
reported per arm as paired diagnostics (OBJ-6), and pricing them belongs to
Programme 2. Because they are unpriced, a policy could lower $J_\beta$ by
deferring work until writes stall; the stall rule (Global acceptance) bars any
claim that does. The drain serves no operations, so it carries no space cost; its
bytes written are counted. A controller arm drains under its fallback settings
($m \equiv 1$, $K_0$ as configured; A-Impl-8), the configuration it started
from, and a static arm under its own configuration, so every arm drains to the
targets it would hold without a controller and no deferred work leaves the run
uncharged. Draining a static profile at $m \equiv 1$ instead would charge it a
compaction its configuration never performs. The rule errs against the
controller: one that ends with anchors above 1 pays for the drain, while a
static profile holding the same multipliers does not.

**Lemma D.1 (the flow form is exact).** Let the reward of an interval
$[t, t+\Delta t)$ be $-\int_t^{t+\Delta t} c$. For any partition of the measured
phase into intervals, the rewards sum to minus the run's priced cost:
$c_w\cdot(\text{bytes written}) + \sum(\text{read price} \times \text{count}) +
(c_s/\bar q)\int H\,q\,dt$.

*Proof.* Counts and integrals are additive over disjoint intervals. $\blacksquare$

*Consequence.* No run-to-date ratio enters the reward, so a start-up transient
cannot inflate a multiplier the way it inflated $\lambda_W$ and
$\lambda_{\text{scan}}$ in D-12. The transient still enters the cost itself,
which is why the measured phase starts only after the tree settles (H §5).

*Scope.* Lemma D.1 is about the global cost. The agents of Pathway H are
trained on a different quantity: each level's attributed, priority-weighted
cost divided by $c_wC_i$, plus counterfactual neighbour charges (H §3). Summed
over levels, those rewards do not give minus the priced cost, for four
reasons. Each level is divided by a different constant. Space is charged per
level as shadowed garbage $g_i$ (§4), an estimate that leaves out live bytes,
not as held bytes $H$. The block read of the table where a Get finds its key
goes to a shared bucket no agent is rewarded on (§4). And a neighbour charge $X_{i+1}$ is a signed estimate of
how level $i$'s action, against holding, changes level $i+1$'s future cost;
level $i+1$ pays the realised change again in its own attributed cost, so the
sum carries each action's effect on its neighbours twice — once realised, once
estimated — above the cost when actions burden neighbours and below it when
they relieve them. The identity that can be checked on a run is Proposition
D.16's: the attributed write and read costs, summed over levels and over every
interval of the measured phase, plus the shared hit-read bucket, before
normalisation and without neighbour charges, equal the global write and read
costs (OBJ-1, OBJ-4). The agents'
discounted per-level returns are a training surrogate: a joint policy that every
agent's return rates optimal need not minimise $J_\beta$ (H §3), and every run
is judged on $J_\beta$.

### §2 Priority modes

**Definition (priced cost with priority).** For weights $\beta = (\beta_W,
\beta_R, \beta_S) > 0$:
$$C_\beta(t) = \beta_W\,c_w\dot w + \beta_R\big[q_{pt}(c_fR_f + c_{blk}R_{blk}) + q_{sc}c_{sk}R_{sk}\big] + \beta_S\,c_sH\,q/\bar q,$$
and $J_\beta(\pi) = \mathbb{E}\int C_\beta\,dt$ over the measured phase, the
expectation over seeds. Write $\mathcal C_W(\pi)$, $\mathcal C_R(\pi)$, $\mathcal C_S(\pi)$ for the
three unweighted priced run costs (write, the three read terms together,
space), so $J_\beta = \beta_W \mathcal C_W + \beta_R \mathcal C_R + \beta_S \mathcal C_S$.

**Modes.** $\beta^\star > 1$ is the priority factor.

| Mode | $(\beta_W, \beta_R, \beta_S)$ |
| --- | --- |
| balanced (pure priced cost) | $(1, 1, 1)$ |
| read priority | $(1, \beta^\star, 1)$ |
| write priority | $(\beta^\star, 1, 1)$ |
| space priority | $(1, 1, \beta^\star)$ |

**Proposition D.2 (gain–loss form).** Fix any reference policy $\pi_0$, for
example native RocksDB with the same options. For a mode with prioritised term
$P \in \{W, R, S\}$, define
$$G_P(\pi) = \beta^\star\big[\mathcal C_P(\pi_0) - \mathcal C_P(\pi)\big] - \sum_{X \ne P}\big[\mathcal C_X(\pi) - \mathcal C_X(\pi_0)\big].$$
Then $J_\beta(\pi) = J_\beta(\pi_0) - G_P(\pi)$. Minimising $J_\beta$ is therefore
the same as maximising "$\beta^\star$ times the decrease of the prioritised cost,
minus the increases of the other two costs", and the reference cancels out of
the choice.

*Proof.* Expand $J_\beta(\pi_0) - J_\beta(\pi) = \beta^\star(\mathcal C_P(\pi_0) -
\mathcal C_P(\pi)) + \sum_{X\ne P}(\mathcal C_X(\pi_0) - \mathcal C_X(\pi))$. $\blacksquare$

*The three modes written out.* "Increase" is signed: if a non-prioritised cost
falls, that counts in the policy's favour at its price.

- **Read priority:** maximise $\beta^\star\cdot$(read-cost decrease) $-$
  (write-cost increase) $-$ (space-cost increase).
- **Write priority:** maximise $\beta^\star\cdot$(write-cost decrease) $-$
  (read-cost increase) $-$ (space-cost increase).
- **Space priority:** maximise $\beta^\star\cdot$(space-cost decrease) $-$
  (read-cost increase) $-$ (write-cost increase).

No reference policy is needed online: by Lemma D.1 and Proposition D.2, a
global per-interval reward $-\int C_\beta$ would implement all three modes. The
agents of Pathway H receive per-level rewards built from it instead (H §3):
attributed write and read costs, a garbage charge for space, and neighbour
charges. $\beta$ enters every one, so the mode reaches every agent, but the per-level rewards
implement it only as far as they approximate the difference reward (H §3, "The
approximation"). Runs are scored on $J_\beta$.

**Proposition D.3 (exchange rate).** Suppose that near an optimum the achievable
operating points form a smooth curve along which only the prioritised cost $\mathcal C_P$
and one other cost $\mathcal C_X$ change. At an interior minimiser of $J_\beta$,
$-d\mathcal C_P/d\mathcal C_X = 1/\beta^\star$. In metric units,
$-dP/dX = \text{price}_X / (\beta^\star\,\text{price}_P)$.

*Proof.* First-order condition along the curve: $\beta^\star\,d\mathcal C_P + d\mathcal C_X = 0$.
$\blacksquare$

In plain terms, the controller accepts one extra unit of another cost only if it
buys at least $1/\beta^\star$ units of reduction in the prioritised cost.
$\beta^\star$ says how much more the prioritised metric is worth than its price.

**Proposition D.4 (a large enough factor gives strict priority on a finite
class).** Let $A$ be a finite set of operating points, such as the static
class. There is a finite $\bar\beta$ such that for every $\beta^\star > \bar\beta$,
every minimiser of $J_\beta$ over $A$ minimises $\mathcal C_P$ over $A$ and, among those
points, minimises $\sum_{X\ne P}\mathcal C_X$.

*Proof.* If every point has the same $\mathcal C_P$ the claim is immediate.
Otherwise let $P_{\min} = \min_A \mathcal C_P$, $\Delta = \min\{\mathcal C_P(a) - P_{\min} :
\mathcal C_P(a) > P_{\min}\} > 0$ (finite set), and $M = \max_A\Sigma - \min_A\Sigma$
with $\Sigma = \sum_{X\ne P}\mathcal C_X$. Take $\bar\beta = M/\Delta$. If $\mathcal C_P(a) >
P_{\min}$ then $J_\beta(a) \ge \beta^\star(P_{\min} + \Delta) + \min_A\Sigma >
\beta^\star P_{\min} + \max_A\Sigma \ge J_\beta(a')$ for every $a'$ with
$\mathcal C_P(a') = P_{\min}$. Among such $a'$, $J_\beta$ differs only through $\Sigma$.
$\blacksquare$

*Use.* A large $\beta^\star$ reads "prioritise reads first, then keep writes and
space as low as possible"; a moderate one reads "trade at this rate". Report
$\bar\beta$ per workload so readers know which regime a result is in. The
headline $\beta^\star$ is fixed in advance (§0.6), with $\beta^\star \in \{2, 5,
10\}$ reported.

**Proposition D.5 (weighted costs select only supported points).** A minimiser
of $J_\beta$ over a set $A$ lies on the lower boundary of the convex hull of $A$'s
priced-cost vectors. A Pareto-optimal point of $A$ strictly above that boundary
minimises $J_\beta$ for no $\beta > 0$.

*Proof.* $J_\beta$ is linear in the priced-cost vector, and the minimum of a
linear function over $\operatorname{conv}(A)$ is attained at points of $A$. If
$x$ minimises it for some $\beta > 0$, the hyperplane $\{y : \langle\beta, y\rangle
= \langle\beta, x\rangle\}$ supports $\operatorname{conv}(A)$ at $x$. A point
strictly above the lower boundary has, for every $\beta$, a point of the hull
with a smaller value. $\blacksquare$ [Das & Dennis; Miettinen]

*Consequences.* The comparator for every mode is a vertex of one hull
(Proposition C.4). A trade that lies in a dent of the frontier is reachable by
no choice of weights.

**Remark D.6 (the actual min–max form).** Minimising the worst weighted gap to an
ideal point, $\max_X \beta_X(\mathcal C_X - \mathcal C_X^{\text{ideal}})$ (the weighted Chebyshev
form), reaches every Pareto-optimal point for suitable weights, including points
in dents [Miettinen]. It needs the ideal point — the best achievable value of
each cost separately — which a cold-started controller does not have. Programme
1 therefore uses the weighted form. The Chebyshev form may be used offline to
select among measured configurations.

### §3 Cost model in the actual knobs

Levels $0..L$; knobs $K_0$, $m_1..m_L$, and depth. The model explains and
bounds; the attributed costs in the reward are always measured (only the
neighbour charge of H §3 uses a one-step model prediction and learned values).

**Lemma D.7 (write accounting identity).** In steady state (A7), with $a_F$
the flush bytes per user byte, $a_0$ the bytes leaving L0 per user byte (equal
to $a_F$ unless an intra-L0 compaction drops entries), $t_i$ the
bytes leaving level $i$ by trivial
move per user byte, $\rho_i$ and $o_i$ measured over merges only, and
$a_{i+1} = (a_i - t_i)\rho_i + t_i$,
$$W = a_F + \sum_{i=0}^{L-1} (a_i - t_i)(\rho_i + o_i),$$
plus, listed separately, any intra-L0 or last-level self-compaction bytes.

*Proof.* A merge sourced at level $i$ writes $X = (X - O) + O = \rho_iS + O
= S(\rho_i + o_i)$ bytes. A trivial move relinks its file into level $i+1$ and
writes nothing. In steady state the bytes leaving level $i$ per user byte equal
those entering it, $a_i$: $a_i - t_i$ by merge and $t_i$ by trivial move. The
net bytes landing in level $i+1$ per user byte are $(a_i - t_i)\rho_i + t_i =
a_{i+1}$. Summing flush bytes and the bytes written by every level's merges, per
user byte, gives the identity. $\blacksquare$

The identity is exact given measured $\rho_i$, $o_i$ and $t_i$; no convention
enters. The expression for $W$ needs no steady state: with $a_i - t_i$ read as
the merged source bytes measured in the window, it holds over any window, the
measured phase included; only the recursion for $a_{i+1}$ needs A7. A RocksDB
job is either a trivial move or a merge, never part of each
(`Compaction::IsTrivialMove` accepts or rejects the whole input set), so $t_i$
is well defined per job. Counting trivially moved bytes as merged would
overstate $W$ by $\sum_i t_i(\rho_i + o_i)$. How large $t_i$ is must be
measured (Pathway B §2 item 4), not assumed. Trivial moves happen where the
level below has gaps in its key coverage: while the tree grows, in the holes a
compaction leaves behind, and while a backlog drains, which native RocksDB does
with trivial moves (audit 2026-09-23, §1). On thirty 2026-09 uniform runs of
the hand-written prior (`unconstrained_prior_only`, 10M, T = 2/6/10), whole run
with the load, 42–71% of the bytes leaving levels $\ge 1$ moved trivially,
and charging them as merges would have overstated bytes written by 26–70%.
Their share in the measured phase on `Assoc` has not been measured.

**Lemma D.8 (overlap with uniformly spread keys).** If keys are spread uniformly
within each level and a compaction's source range covers a fraction $x$ of level
$i$'s key space, then $o_i = B_{i+1}/B_i$ at that moment. With both levels at
their effective targets, $o_i = f_i$. For L0 → L1 with L0 files spanning the key
range, $o_0 = B_1/(k_0F) = m_1C_1/(K_0F) = f_0$ at the trigger.

*Proof.* The source holds $xB_i$ bytes and overlaps $xB_{i+1}$ bytes below.
$\blacksquare$

*The overlap constant.* Define $c_i = o_i/f_i$, measured per level. RocksDB's
`kMinOverlappingRatio` picks files with below-average overlap [Sarkar et al.],
so $c_i \le 1$ is expected, and skewed keys lower it further. The conventions of the 2026-09-11
revision (M1: $c f$ with $c = 1$; M2: $c = \tfrac12$; M3: $c(f+1)$ with
$c = \tfrac12$ [How to Grow]) differ in how they count the source rewrite and the overlap; the
identity counts both exactly and leaves $c_i$ to measurement. This resolves
issue 3 and amends P0-7 (§0.6).

**Corollary D.9 (garbage-free steady state).** With $\rho_i = 1$, $a_F = a_0 = 1$,
no trivial moves ($t_i = 0$), levels at their effective targets and a common
overlap constant $c$,
$$W = 1 + L + c\sum_{i=0}^{L-1} f_i,\qquad \prod_{i=0}^{L-1} f_i = \frac{B_L}{K_0F},$$
and the product does not depend on $m_1, \dots, m_{L-1}$.

*Proof.* Lemma D.7 with $a_i = 1$ and Lemma D.8. The product telescopes:
$\frac{m_1C_1}{K_0F}\cdot\prod_{i=1}^{L-2}\frac{m_{i+1}C_{i+1}}{m_iC_i}\cdot
\frac{B_L}{m_{L-1}C_{L-1}} = \frac{B_L}{K_0F}$. $\blacksquare$

**Lemma D.10 (read terms).** Runs are searched newest to oldest (L0 files, then
L1 to L), after the memtables. A Get answered from the memtables probes no
table, so $R_f = R_{blk} = 0$. Otherwise consider a Get whose newest version is
in the $n$-th table run whose key range covers the key (a hit), or that finds no
version in any table (a miss), and let $\varepsilon$ be the probability that a
covering run which does not hold the key passes its filter. Then:

- $R_f = n$ for a hit, and the number of covering runs for a miss;
- $\mathbb{E}[R_{blk}] = \mathbf 1_{\text{hit}} + \varepsilon\,(R_f - \mathbf 1_{\text{hit}})$;
- for a scan, $R_{sk} = k_0 + $ (number of levels $1..L$ with a file at or
  after the seek key); a `DBIter` reseek, after more than
  `max_sequential_skip_in_iterations` hidden versions of one key, counts them
  again, which is rare here and checked with `rocksdb.number.reseeks.iteration`.

On `Assoc` there are no misses: the load writes every key below `num`, and
`mixgraph` draws only such keys. With a per-level allocation such as Monkey
[Monkey], $\varepsilon$ becomes $\varepsilon_j$ inside the sum.

*Proof.* Filters have no false negatives, so every covering run before the hit
is probed and does not hold the key; each of those reads a block with
probability $\varepsilon$, by linearity of expectation (no independence between
runs is needed), and the hit run reads one. A scan seeks each L0 file and each
level that has a file at or after the seek key, once per level however many of
its files the scan then enters (P1c-20: at the recorded RocksDB commit the table
iterator ticks only on a keyed seek, and a level iterator moving to its next
file does not tick again). $\blacksquare$

**Proposition D.11 (static optimum of the L0 trigger).** Assume no garbage
dropped at L0 ($\rho_0 = 1$), uniform-key overlap at L0 (Lemma D.8; each L0 file then overlaps L1, so none moves trivially and $t_0 = 0$), a time-average L0 file count $\bar k_0 = K_0/2 +
\delta_0$ with $\delta_0$ not depending on $K_0$ (it accounts for flushes that
arrive during an L0 compaction, and while the single compaction slot runs
another level's job; independence from $K_0$ is an assumption, checked on the
static sweep of Gate N2), Gets and scans that probe every L0 run, and
fixed prices and rates. The part of $C_\beta$ that depends on $K_0$ is
$$\frac{\beta_W c_w u\,m_1C_1}{K_0F} + \beta_R\frac{K_0}{2}\big[q_{pt}(c_f + \varepsilon c_{blk}) + q_{sc}c_{sk}\big],$$
minimised over real $K_0 > 0$ at
$$K_0^\star = \sqrt{\frac{2\,\beta_W c_w u\,(m_1C_1/F)}{\beta_R\big[q_{pt}(c_f + \varepsilon c_{blk}) + q_{sc}c_{sk}\big]}}.$$
Admissible triggers are the integers in $[2, K_{\text{cap}}]$ with $K_{\text{cap}}
= \min(\lfloor C_1/F\rfloor, K_{\text{slow}} - 1)$ (A3′), and the best admissible
trigger is one of the two integers next to $K_0^\star$, clamped to that range.

*Proof.* By Lemma D.7 only the overlap term $a_0o_0 = m_1C_1/(K_0F)$ of the
L0-sourced writes depends on $K_0$; by Lemma D.10 each L0 run adds one filter
probe and $\varepsilon$ block reads per Get and one seek per scan. So the cost
is $g(K) = A/K + BK$ with $A, B > 0$, strictly convex on $K > 0$ with minimiser
$\sqrt{A/B}$. Strict convexity makes the best integer one of the two neighbours
of the real minimiser. $\blacksquare$

This has the same shape as the draft's $K^\star = \sqrt{wT/r}$, with the L1-to-flush
fanout $m_1C_1/F$ in place of $T$. $K_0^\star \propto \sqrt{\beta_W/\beta_R}$:
read priority lowers the trigger and write priority raises it.

**Corollary D.12 (a provable adaptivity gap for the L0 trigger).** Suppose the
workload alternates between phases whose rates $(u, q_{pt}, q_{sc})$ give
different best admissible triggers under Proposition D.11. Then any fixed
trigger costs strictly more than the trigger switched per phase, not counting
the cost of each switch.

*Proof.* Each phase's cost is strictly convex in $K_0$ with its own minimiser. A
fixed $K_0$ is at most one phase's minimiser, so it is strictly worse in at
least one phase and no better in any. $\blacksquare$

This predicts, in advance, a positive phase-adaptivity gap $\mathcal G$
(Pathway B) for one knob. A real controller also pays the switching transient:
the L0 contents at the switch.

**Proposition D.13 (interior multipliers and depth in steady state).** Under
Corollary D.9's assumptions, with $L$ and $K_0$ fixed:

- (i) $W$ is minimised over the interior multipliers exactly when all fanouts
  are equal, $f_i = (B_L/(K_0F))^{1/L}$. That profile is static.
- (ii) (This part does not use Corollary D.9's assumptions.) Up to the L0 run
  count, $R_{sk}$ does not depend on the interior
  multipliers unless a level empties, and $R_f$ and $R_{blk}$ depend on them
  only through where Gets find their key. Raising $m_i$ delays every version's
  descent below level $i$, so a Get whose key's newest version would already
  have crossed a boundary at or below level $i$ finds it one level higher,
  saving one filter probe (and $\varepsilon$ block reads) per level it no longer
  passes. Under uniform access the moved share is small for the upper levels,
  which hold little of the data; for the levels just above the last it is the
  same shift that, taken far enough, empties the last level (audit §3). Under
  skew it can be material at the upper levels too, but only for Gets whose key
  is rewritten during the run. In db_bench's `mixgraph` the key and the
  operation type come from one random draw, so on `Assoc` about 44% of Gets read
  keys that no Put writes during the measured phase, and on the uniform control
  no Get ever does. The moved share is measured from per-level hit shares with
  Gate N0's counters (§4), not assumed. The interior multipliers also reach the
  L0 run count through $\delta_0$ (Proposition D.11): $m_1$ directly, since it
  sets the size, and so the duration, of every L0→L1 merge, and every $m_i$
  through the single compaction slot, whose busy time depends on each level's
  merge sizes. Flushes that arrive meanwhile raise the mean L0 run count, adding
  probes to every Get that reaches the tables and seeks to every scan; merges
  out of L0 grew 34–74% at $s = 1.5$–2 (audit §3). For a fixed profile all of
  this is static.
- (iii) The space bound of Lemma D.14 increases in every $m_i$.
- (iv) Treat $L$ as continuous with equal fanouts $f = (B_L/(K_0F))^{1/L}$. The
  $C_\beta$-optimal fanout solves
  $$c\,f(\ln f - 1) = 1 + b/A,$$
  where $A = \beta_Wc_wu$ and $b = \beta_R[q_{pt}(c_f + \varepsilon c_{blk})h +
  q_{sc}c_{sk}]$ is the read cost rate one more level adds, with $h$ the share
  of Gets that probe it. Read priority raises $f^\star$ (fewer levels). At
  $b = 0$ and $c = 1$, $f^\star \approx 3.59$.

*Proof.* (i) Minimise $\sum f_i$ with the product fixed; by the AM–GM
inequality equality of the terms is necessary and sufficient. (ii) Lemma D.10:
the read counts depend on the number of runs and the hit position. Interior
multipliers change the number of runs at levels $\ge 1$ only if a level
empties, the hit position only by delaying descent, and $k_0$ only through
$\delta_0$, directly through $m_1$ and through the shared compaction slot.
(iii) Lemma D.14. (iv) The $L$-dependent cost is
$A(1 + L + cLf) + bL$. Since $\ln f = \ln(B_L/(K_0F))/L$,
$\frac{d}{dL}(Lf) = f(1 - \ln f)$, so the derivative is
$A(1 + cf(1 - \ln f)) + b$; setting it to zero gives the equation. At $c = 1$,
$b = 0$: $3.59\,(\ln 3.59 - 1) = 1.00$. $\blacksquare$

*Consequences.* Everything in (i)–(iv) is a static choice: an interior profile,
a depth fixed by base size at load time, and $K_0$ at fixed prices. All of it
belongs to the static comparator (Corollary C.3). Depth cannot be reduced during
a run (Lemma A.6), so (iv) is a design and comparator result only. The
2026-09-11 Corollary A.4 ($f^\star \approx 3.59$ under M3) is the special case
$c = 1$, $b = 0$; the optimum moves with the measured $c$ and with the priority.

**Lemma D.14 (space bound).** Under A2 and A6,
$$S \le 1 + \sum_{i<L}\frac{B_i}{B_L} \le 1 + \frac{k_0F}{B_L} + \sum_{i=1}^{L-1}\frac{m_iC_i}{B_L},$$
the second inequality holding outside release transients, when $B_i \le m_iC_i$.

*Proof.* Level $L$ holds one version per key (A2), so the number of distinct keys
is at least the number of keys at level $L$, and with fixed entry size the live
bytes are at least $B_L$. Total bytes are $\sum_i B_i$, so $S \le \sum_iB_i/B_L$.
$\blacksquare$

For a full geometric ladder at $m \equiv 1$ the bound is about $1 + 1/(T-1)$,
which is the draft's $ZT/(T-1)$ at $Z = 1$. It is a worst case (every upper
entry updates a last-level key): about 2.0 at T=2 against a measured 1.07–1.09
on `Assoc`. It is used only to bound the space cost of expansion,
$\partial(\text{bound})/\partial m_i = C_i/B_L$.

**Lemma D.15 (live bytes do not depend on the policy).** Index the measured
phase by operation number $n$ (A8). Suppose the arms serve the same operation
sequence, and that the bytes an entry occupies in a table do not depend on which
entries share its block or file (A6's last clause; with compression off it fails
only through per-file metadata, index and filter blocks and restart points, a
small fraction of a percent — the garbage-free size of 108 `Assoc` runs spanned
0.006%, D-3). Then the live bytes after operation $n$ are the same under every
compaction policy, and with space charged per operation served (§1) two
policies' space costs differ only through the garbage they hold:
$(c_s/\bar q)\sum_n\big(H_\pi(n) - H_{\pi_0}(n)\big) = (c_s/\bar q)\sum_n\big(G_\pi(n) - G_{\pi_0}(n)\big)$,
up to the live bytes of a memtable whose flush completes after a different
operation under different policies: at most one write buffer at any operation
(2 MiB here, with two memtables), and on average far less, against about 3 GiB
held. Entry sizes may vary; with deletes the lemma holds with tombstones counted
as garbage.

*Proof.* Compaction never changes the result of a read, so the newest version
of every key after operation $n$, and therefore the live set, is fixed by the
operations. The current version's table files ($H$) hold every live entry not
in the memtables, plus garbage; which entries the memtables hold after
operation $n$ is fixed by the write sequence except for flushes still in
progress. $\blacksquare$

*Why per operation and not per second.* Under a per-second charge the lemma
fails: two policies spend different times on the same operations, so the
difference gains $c_s\sum_n L(n)(\Delta t^\pi_n - \Delta t^{\pi_0}_n) \approx
c_sN_{\text{live}}(T_\pi - T_{\pi_0})$, with $L(n)$ the live bytes and
$\Delta t_n$ the time spent at operation $n$. On record, runtime moves more
than space. The four base-16 MiB static triggers on `Assoc` held within
0.6–0.8% of one another's space (D-3), while the runtime winner led by 4.2% at
T=2 and 2.6% at T=10 (D-5). In the 2026-08-24 uniform matrix, learned and prior
arms ran 3.6–6.3% longer than native (history §10.6; its space figures used the
live-data estimate D-3 retired, so only the runtimes carry over). Per second,
the runtime term would be 3–7 times the space difference. A space-priority
controller would chase speed.

*Consequence.* Space needs no online estimate of live data — the estimate D-3
retired. The evaluator prices held bytes $H$ directly; the per-level rewards
need only the policy-dependent part, garbage, of which §4 attributes the
adjacent-level part as shadowed garbage $g_i$ (an estimate that undercounts;
OBJ-3 checks how it tracks measured garbage).

### §4 Per-level attribution

The agents of Pathway H each need their own share of the cost:

- **Writes** are charged to the source level of the compaction that wrote them
  (the job's start level, not RocksDB's per-level compaction statistics, which
  are keyed by output level); flush writes to L0. This is exact.
- **Reads:** each filter probe, false-positive block read and run seek is
  charged to the level where it happens. The block read of the table where a
  Get finds its key is charged to no level. It goes to a shared *hit-read
  bucket* that no agent is rewarded on. Every Get that reaches the tables makes
  exactly one such read, wherever its key is found (Lemma D.10), and the share
  of Gets answered from the memtables depends on the policy only through Lemma
  D.15's memtable caveat. Charging it to the level that holds the key would
  charge that level for a read that merely moved up from a deeper level, and
  teach it to avoid holding recently written keys. This is exact, given
  per-level counters that take the level from the version being read —
  `FilePicker`'s hit level in `Version::Get`, and for seeks the level at which
  the version builds the iterator (0 for each L0 file, the level iterator's
  level otherwise) — that separate the hit's block read from false-positive
  ones, and that aggregate across threads (Gate N0). RocksDB's per-level perf context does not qualify as it stands: it
  is thread-local, needs `perf_level` at least `kEnableCount`, has no seek
  counter, and keys its filter counters by the level at which a table reader was
  first opened, which is stale after a trivial move.
- **Slot blocking.** There is one compaction slot (G.4). While L0 is due
  (score at least 1) but cannot compact because a job sourced at level
  $i \ge 1$ holds the slot, the share $(k_0 - K_0)^+/k_0$ of L0's filter probes,
  false-positive block reads and seeks — the files beyond its trigger — is
  charged to level $i$ instead of to L0, for every operation served in that
  span. The rule moves cost between levels and leaves the total unchanged. It
  is an attribution rule, not an exact counterfactual: had the slot been free,
  flushes arriving during L0's own merge would still have added files. It is
  the channel through which an interior level changes read cost during a run
  (Proposition D.13(ii)'s $\delta_0$), and it lets interior agents in read
  priority learn to keep the slot free while L0 is due or about to be. The
  share is exact in expectation when every L0 file covers the key
  (Lemma D.8's uniform-key L0 files).
- **Space:** level $i$ is charged its *shadowed garbage*
  $g_i = (1 - \hat{\tilde\rho}_i)B_i$ at the rate $c_sg_i\,q/\bar q$ (§1): the
  bytes its next compactions would drop at the recent net pass-through
  $\hat{\tilde\rho}_i$ (trivial moves drop nothing). This is an estimate, and it
  counts only garbage in level $i+1$ shadowed by level $i$. A version two or
  more levels below its next-newer version is dropped only after that version
  descends, and no $g_k$ counts it, so $\sum_ig_i$ undercounts resident garbage
  whenever upper-level keys also live deeper than the next level. Live bytes are
  not attributed: they are the same under every policy (Lemma D.15).

**Proposition D.16 (decomposition).** The per-level charges, plus the shared
hit-read bucket, sum to the global cost rate $c(t)$ exactly for the write and
read terms. For space, $\sum_i g_i$ counts only adjacent shadowing and so
undercounts total garbage; it is reported against measured garbage at run end
(OBJ-3).

*Proof.* Every written byte has exactly one source (a flush or one compaction).
Every probe, seek and false-positive block read happens at exactly one level and
is charged in full either there or, under slot blocking, split between L0 and
one other level in shares that sum to 1. Every hit's block read goes to the
bucket. $\blacksquare$

### §5 Room by mode: predictions recorded before any run

- **Read priority.** On `Assoc` at the old comparators there was almost no room
  in steady state (audit §2). The read levers are the L0 trigger — static at
  fixed prices, dynamic when prices move (Corollary D.12) — and depth, which is
  static (Corollary A.7); under skew the interior profile is a third read lever,
  also static (Proposition D.13(ii)). Two further levers act during a run,
  both through the single compaction slot: the L0 agent compacting early while
  the slot is idle and reads are heavy (Pathway A §4 (e)), and interior levels
  keeping the slot free while L0 is due or about to be (Pathway A §4 (f), priced
  by the slot-blocking charge of §4). Their size is unmeasured. Room is expected mainly on
  phased or changing workloads.
- **Write priority.** A static profile gains $O((1-\eta)^2)$ (Theorem A.2(iii),
  where $\eta$ is a ratio of merged volumes, not merge survival), plus what
  timing merges against garbage can add, bounded by Proposition B.1″ and its
  native-relative form (Pathway B §3). On `Assoc` garbage is not scarce in the
  measured phase, since every Put overwrites, but native compaction already
  drops 77–92% of it, much of it high in the tree (merge survival
  $X/(S+O)$ at L1 0.84–0.93, D-4). First overwrites meet their loaded versions
  in the deepest levels, where a drop saves the fewest later stages. The room is
  what holding a level adds to high drops beyond native; Gate N3 measures it.
- **Space priority.** On `Assoc`, $S$ is 1.03–1.09, so garbage is $(S-1)/S$ =
  3–8% of held bytes; even a policy that held no garbage at all could remove no
  more than that. High-garbage workloads are needed.

### §6 Acceptance

| # | Criterion | Threshold | Instrument |
| --- | --- | --- | --- |
| OBJ-1 | Flow identity (Lemma D.1, Proposition D.16) | on every arm that runs the plugin: the attribution log's per-level write bytes (flushes to L0, each compaction to its start level, by completion time), summed over levels and over every interval from $n_w$ to the end of the drain — intervals excluded from replay included — equal the evaluator's flush and compaction bytes for the same window, with the log opening a partial interval for every level at $n_w$ and closing one at the end of the drain; any residual is listed by job and must consist only of jobs whose completion the two sources place on different sides of a boundary stamp. The log's per-level read counts, taken before the slot-blocking rule moves any of them and with each level's hit block reads logged apart from its false-positive ones, equal the per-level counters' totals over the same window (their difference between the $n_w$ stamp and the end of the drain); after the rule, per-level read charges plus the hit-read bucket still sum to the global read cost. Space is not checked here: the per-level charge is a garbage estimate (OBJ-3) | attribution log, event log |
| OBJ-2 | Prices calibrated | all prices in money: device times for $c_w, c_f, c_{blk}, c_{sk}$ measured on the node and converted at the instance price, the storage price $c_s > 0$, both money prices fixed in advance (§0.6), and $\bar q$ per workload, in the fingerprint; re-measured on any hardware change; every result also reported at $c_s/2$ and $2c_s$ | calibration script |
| OBJ-3 | Space attribution | $\sum_ig_i$ and measured garbage at run end reported per arm. $\sum_ig_i$ targets only adjacent shadowing and undercounts (§4), so the criterion is on differences: across $\Theta_s$ at Gate N2, the ratio's spread and the slope of $\Delta\sum_ig_i$ on $\Delta$(measured garbage) are reported, and a tolerance on the ratio is fixed from them before Gate N4; at Gate N4 the learner's paired $\Delta\sum_ig_i$ against its comparator must agree in sign with $\Delta$(measured garbage) whenever that difference's paired interval excludes zero, and its ratio must lie within that tolerance | reference compaction, event log |
| OBJ-4 | Per-level read counters | per-level counters taken at the sites of §4 (the version's level: `FilePicker` hits in `Version::Get`; seeks per L0 file and per level iterator), so that a trivially moved file's reads are charged to its new level; block reads split into the hit's read and false-positive reads (§4); their sums match the global counters within 1% (the read half of Proposition D.16) | per-level counters, tickers |
| OBJ-5 | Mode recorded | $\beta$, mode and $\bar q$ in every arm's manifest and fingerprint | `metadata.env` |
| OBJ-6 | Unpriced time reported | measured-phase throughput, stall seconds and controller CPU per arm, paired against the comparator; not part of $J_\beta$, but the first two decide the stall rule (Global acceptance) | db_bench, event log, per-thread CPU clocks (plugin thread, trainer process) |

---

## Pathway A — Actuation: level target multipliers and the L0 trigger

### §0 Purpose

Give the controller three moves per level — compact now, defer, and defer while
expanding the level's capacity — through RocksDB's own compaction scores, so that
RocksDB keeps sole authority over which files are compacted (history §3.3). The
2026-09-11 motivation, avoiding a depth cascade, is withdrawn (§0.1).

### §1 Mechanism

**New option `level_target_multipliers`** (mutable column-family option):

- a `vector<double>` with one entry per level; entry 0 must be 1.0;
- rejected when `level_compaction_dynamic_level_bytes = true` or when the
  compaction style is not leveled;
- changeable at run time through `SetOptions`;
- in `ComputeCompactionScore`, level $i \ge 1$ is scored as
  $$s_i = \frac{\text{bytes\_not\_compacting}_i}{\texttt{MaxBytesForLevel}(i)\times m_i}.$$

**L0** keeps its native scoring. The controller changes only the mutable
`level0_file_num_compaction_trigger` ($K_0$), through `SetOptions`.

### §2 Actions

For level $i \ge 1$ the effective multiplier is $m_i = \bar m_i\,d_i$: a
persistent **anchor** $\bar m_i$ (the level's capacity) and a short-lived
**timing factor** $d_i$ that relaxes back to 1 with time constant
$\kappa_d\tau_i$.

| Action | Effect | Allowed when |
| --- | --- | --- |
| hold | nothing: RocksDB proceeds natively under the current $m_i$ | always |
| compact | $d_i \leftarrow \varphi_i/(\bar m_i(1+\epsilon))$, so $s_i > 1$ now | $s_i < 1$ and $\varphi_i \ge \varphi_{\min}$ |
| defer | $d_i \leftarrow \varphi_i(1+\epsilon)/\bar m_i$, so $s_i < 1$ now | $s_i \ge 1 - \epsilon$ |
| expand (defer and increase capacity) | $\bar m_i \leftarrow \min(\alpha\bar m_i, m_{\max})$, $d_i \leftarrow 1$ | $\bar m_i < m_{\max}$ |

- **Hold is the default.** It means "let RocksDB act natively", so a controller
  that always holds is native RocksDB (ACT-4).
- **Anchors decay.** When not re-expanded,
  $\bar m_i \leftarrow 1 + (\bar m_i - 1)e^{-\Delta t/(\kappa_a\tau_i)}$, so
  expansion cannot ratchet (ACT-5).
- **Bounds.** Every $m_i$ stays in $[m_{\min}, m_{\max}]$ (A-Impl-7).
- **Masks.** An action not allowed in a state is excluded both when the action
  is chosen and in the learning target (Proposition H.2).

**L0 analogues**, acting on $K_0$: hold; compact, $K_0 \leftarrow \max(2, k_0)$ so
L0 is due now; defer, $K_0 \leftarrow \min(K_{\text{cap}}, k_0 + 1)$ temporarily;
expand, raise the L0 anchor $\bar K_0$ by one within $[2, K_{\text{cap}}]$,
decaying back to the configured value.

**Lemma A.5 (what an action does).** Under A3, "compact" makes level $i$
eligible at the next score computation and "defer" makes it ineligible. Neither
guarantees admission: RocksDB admits the eligible level with the highest score
when a compaction slot is free.

*Proof.* A3 and RocksDB's ranking of eligible levels by score. $\blacksquare$

*Consequence.* Actions at different levels interact through score ranking. That
is why neighbour and global state are inputs (H §2), and why each level is
charged for what actually ran (Proposition D.16), not for what it requested.

### §3 Theory

**Theorem A.1 (no release while held; corrected).** If $\varphi_i(t) < m_i(t)$
throughout $[t_0, t_1]$, level $i$ is not eligible and releases nothing in that
interval. When it is released at fill $\varphi_i \ge m_i$, RocksDB compacts it
until its score falls below 1, so it sends down about $(\varphi_i - m_i)C_i$
source bytes (to within one file), of which about $\tilde\rho_i(\varphi_i -
m_i)C_i$ net bytes land below (trivially moved files land whole; here
$\tilde\rho_i$ is the pass-through of the released files, which after a hold can
differ from the window mean in either direction: a backlog clears partly by
trivial moves, but in D-12 deferrals during the post-load backlog turned
would-be moves into merges, audit 2026-09-23, §1).

*Proof.* A3, and RocksDB re-scores after each compaction and keeps picking the
level while its score is at least 1 and highest. $\blacksquare$

*Correction.* The 2026-09-11 statement charged a burst of $\kappa C_i$. At
$m_i = 1$ the burst is $(\kappa-1)C_i$ (audit §3).

*Containment (fluid, A5).* A burst of $b$ net bytes landing at level $i+1$ makes
level $j > i$ due only if $b$ exceeds the combined headroom
$\sum_{k=i+1}^{j}(m_kC_k - B_k)^+$. Populated depth grows only if a burst passes
the last populated level's headroom $(m_LC_L - B_L)^+$. At T=10 on `Assoc` the
last level's nominal target (about 16 GiB) is far above its content (about
1 GB), so no burst can deepen the tree there.

**Theorem A.2 (fanout conservation; corrected and extended).**

- (i) $\prod_{i=0}^{L-1}f_i = B_L/(K_0F)$, independent of $m_1, \dots, m_{L-1}$
  (Corollary D.9's product, which uses only the definitions of the $f_i$ and
  none of that corollary's assumptions).
- (ii) With weights $v_i = a_i - t_i$, the merged bytes leaving level $i$ per
  user byte (Lemma D.7), held fixed, and a common overlap constant $c$, the
  fanout part of the write cost, $c\sum_iv_if_i$, is minimised under (i) at
  $f_i \propto 1/v_i$, with minimum $cL\,(B_L/(K_0F))^{1/L}(\prod_i
  v_i)^{1/L}$.
- (iii) For $v_i = \eta^i$, that is a constant ratio $\eta = v_{i+1}/v_i =
  \tilde\rho_i(1-\xi_{i+1})/(1-\xi_i)$ of merged volumes (not the merge survival
  $\eta_i = X/(S+O)$ of Pathway B §2), the relative gain over equal fanouts is
  $$1 - \frac{L\,\eta^{(L-1)/2}(1-\eta)}{1-\eta^{L}} = O\big((1-\eta)^2\big).$$

| $\eta$ | $L$ | optimum / equal fanouts | available gain |
| ---: | ---: | ---: | ---: |
| 0.95 | 4 | 0.9984 | 0.2% |
| 0.90 | 4 | 0.9931 | 0.7% |
| 0.80 | 5 | 0.9519 | 4.8% |
| 0.60 | 5 | 0.7807 | 21.9% |

*Proof.* (i) Corollary D.9. (ii) With a multiplier on the product constraint,
stationarity gives $v_if_i = \lambda$ for every $i$. The constraint then gives
$\lambda = (B_L/(K_0F)\cdot\prod_iv_i)^{1/L}$, and the minimum of $\sum v_if_i$
is $L\lambda$. (iii) With $v_i = \eta^i$, $\prod_i v_i = \eta^{L(L-1)/2}$, and
equal fanouts give $(B_L/(K_0F))^{1/L}\sum_i\eta^i$; divide. The first-order
terms in $1-\eta$ cancel. $\blacksquare$

*Assumption behind (ii) and (iii).* The weights are held fixed while the profile
varies, but they depend on it. Levels $\ge 1$ hold one version per key, so an
overwrite is absorbed when the newer version arrives: a larger level $i$ holds
more keys, the merges into it (sourced at $i-1$) drop more, and $\rho_{i-1}$
falls, and with it the weight $v_i$ of level $i$'s own merges; fewer duplicates
are left for the merges out of level $i$, so $\rho_i$ may rise. The trivial-move
shares can move too (in one simulation with a round-robin picker, by more than
$\rho$: enlarging level $i$ lowered $\xi_{i-1}$ and raised $\xi_i$; under
min-overlap picking, the pinned `kMinOverlappingRatio`, they barely moved). The overlap constant moves with the
fanouts. (ii) is therefore the optimum for fixed weights. The joint optimum
needs survival measured as a function of the profile. $\Theta_s$ supplies it
only at its own profiles: three of them are uniform scalings, which move $m_i$
and $m_{i+1}$ together, so their separate effects on $\rho_i$ (a larger level
$i+1$ lowers it) cannot be told apart, and its survival-weighted profile is computed from weights measured at
uniform 1. Treat that profile as one step of a fixed-point iteration:
re-measure the weights under it and recompute; if the profile moves by more
than its measurement interval, add the second profile to $\Theta_s$ before the
comparator is fixed (§0.6).

*Corrections.* The optimal profile needs some multipliers below 1, which the
2026-09-11 scale ($s \ge 1$) could not express (audit §3); multipliers below 1
are now allowed. The profile is static, so its gain belongs to the comparator
(Corollary C.3). The accounting in (ii) is Lemma D.7's, not a convention.

**Corollary A.3 (2026-09-11: level removal is never profitable).** *Superseded.*
Its premise, that removing a level needs $s = T$, failed at T=10 where $s = 2$
removed one (audit §3). Whether fewer levels pay depends on the priority
(Proposition D.13(iv)), and removal is static in any case (Corollary A.7).

**Corollary A.4 (2026-09-11: write-optimal size ratio).** *Superseded* by
Proposition D.13(iv), in which the optimum depends on the measured overlap
constant and the priority.

**Lemma A.6 (depth never falls during a run).** Under A4 and without deletes,
the index of the deepest non-empty level never decreases.

*Proof.* Compactions move bytes from level $i$ to $i+1$ or within a level. The
deepest non-empty level can only receive bytes or push them deeper, and without
deletes no compaction removes all of its data. $\blacksquare$

**Corollary A.7 (level removal is a static effect).** A controller can prevent
depth growth but cannot remove a level during a run. Removing a level — the
largest read benefit static expansion showed (audit §3; at $s = 1.5$, with depth
unchanged, reads also fell 6.3% at T=2 and 3.4% at T=10, the static hit shift of
Proposition D.13(ii)) — is a property of the
configuration the tree was loaded under (base size, multipliers at load time),
and so belongs to the static comparator.

### §4 What actuation can and cannot deliver

**Static, so the comparator has it too:** the equal-fanout or survival-weighted
profile (Theorem A.2), depth fixed at load (Corollary A.7), $K_0$ at fixed
prices (Proposition D.11), and the interior profile's read effect
(Proposition D.13(ii)).

**Dynamic, so this is the controller's only case.** Each item is a hypothesis
tested without a learner at Gate N3:

- (a) tracking $K_0^\star$ and the profile as prices change with the workload
  (Corollary D.12);
- (b) timing a release against the neighbours: overlap per source byte is about
  $T\varphi_{i+1}/\varphi_i$ (Lemma D.8), and a burst may be arriving from above;
- (c) holding a level so its merges drop more garbage, bounded by the garbage
  native leaves and by how much higher the drops can move (Pathway B §3);
- (d) preventing depth growth (Theorem A.1);
- (e) L0 compacting early (the L0 "compact" action) while the compaction slot
  is idle and reads are heavy, and holding while it is busy;
- (f) interior levels holding their releases while L0 is due or about to be,
  so that L0 is not kept waiting for the slot (the slot-blocking charge,
  Pathway D §4).

(e) and (f) are read levers that act during a run. A fixed trigger or profile
can express neither, because each depends on the slot's state at the moment of
the decision.

### §5 Implementation

- **A-Impl-1. Scale inside the score only.** Never route the multiplier through
  `max_bytes_for_level_base`. That option is also L0's byte threshold and L1's
  base, so routing through it would silently change L0's trigger. The
  implementation folds $m_i$ into `MaxBytesForLevel(i)` through the fork's
  existing per-level `capacity_scales_` (which already leaves level 0
  unscaled). That gives the score of §1 exactly, and makes A-Impl-3 automatic.
  The only other reader of `MaxBytesForLevel` that affects file picking is the
  round-robin input expansion in `compaction_picker_level.cc`, which the pinned
  `kMinOverlappingRatio` never runs. Tested (ACT-1): L0's score is
  unchanged under any multiplier vector.
- **A-Impl-2. Static ladder.** `level_compaction_dynamic_level_bytes = false`
  is pinned, and option validation rejects multipliers otherwise (A1).
- **A-Impl-3. Pending-compaction estimate.**
  `EstimateCompactionBytesNeeded` computes pending bytes from level targets. It
  must apply $m_i$ too, or RocksDB holds two different notions of what is due,
  and the pending-bytes slowdown and speed-up logic reads unscaled targets. It
  reads `MaxBytesForLevel`, so the implementation of A-Impl-1 satisfies this
  (verified at `25468bbaa`). Re-check the soft and hard pending-bytes limits.
- **A-Impl-4. When a change takes effect.** Verified at `25468bbaa`
  (`DBImpl::SetOptions`): the call appends a new Version without a MANIFEST
  write, which recomputes every score, before it returns, waiting its turn
  behind any flush or compaction install. An action is therefore in the
  published scores at once, with or without writes.
- **A-Impl-5. Cost of `SetOptions`.** Verified at `25468bbaa`: each call
  appends that Version and installs a new SuperVersion under the DB mutex, then
  writes and renames a full OPTIONS file with the mutex released
  (`WriteOptionsFile`; RocksDB has no switch to skip it). The OPTIONS file is
  not an SST, so it enters none of $W$, $H$ or $J_\beta$ (D §1); its time cost
  is measured by ACT-2 and reported, not priced. Call `SetOptions` only when a
  value changes (hold makes no call), send all levels' changes in one call,
  and cap the call rate.
- **A-Impl-6. L0 trigger range.** $[2, K_{\text{cap}}]$ with $K_{\text{cap}} =
  \min(\lfloor C_1/F\rfloor, K_{\text{slow}} - 1)$ (A3′). Gate 1 found triggers
  8 and 16 identical at an 8 MiB base.
- **A-Impl-7. Multiplier bounds.** $m_i \in [m_{\min}, m_{\max}]$, fixed in
  advance; $m_{\min} = 0.5$ is suggested. $m_{\max} \le 2.0$ unless larger values
  are measured, since 2.0 is only the edge of the tested static grid (audit §3).
  Level targets must also never shrink going down the tree:
  $m_{i+1}C_{i+1} \ge m_iC_i$, i.e. $m_{i+1}T \ge m_i$. RocksDB assumes this
  ordering (`version_set.cc` asserts it over non-empty levels), and the fork's
  capacity code already rejects vectors that break it. At $T = 2$ the bounds
  $[0.5, 2]$ alone do not guarantee it, so option validation rejects such a
  vector, and the controller's masks exclude any action that would produce one.
- **A-Impl-8. Attribution and fallback.** Log every change with level, old and
  new value, action, decision id and reason. If the controller is absent or its
  weights are invalid, reset $m \equiv 1$ and $K_0$ to its configured value, and
  log it as a fallback.
- **A-Impl-9. Ruled out: `max_bytes_for_level_multiplier_additional`.** It is an
  integer vector, its effect accumulates down the tree, and it is ignored under
  dynamic level sizing (as in 2026-09-11).
- **A-Impl-10. Native parity.** With $m \equiv 1$ and $K_0$ at its configured
  value, the patched binary must reproduce stock RocksDB (ACT-4).

### §6 Acceptance

| # | Criterion | Threshold | Instrument |
| --- | --- | --- | --- |
| ACT-1 | Option correctness | the fork's test file `level_target_multipliers_test` is green (Debug build), and the same checks pass on the Release binary in the preflight: L0 score invariance, rejection under dynamic sizing and of vectors whose targets shrink going down, scaled pending estimate, recompute after `SetOptions` | fork gtest; preflight check script over `db_bench` output and LOG |
| ACT-2 | `SetOptions` overhead | foreground throughput cost ≤ 1% at the planned call rate, paired, OPTIONS-file writes included; a diagnostic of overhead, not part of $J_\beta$ | db_bench |
| ACT-3 | Action fidelity | a requested $m_i$ appears in the published score within one control interval for ≥ 99% of changes | policy log, score observer |
| ACT-4 | Native parity | patched binary at $m \equiv 1$ inside the oracle-parity envelope of stock RocksDB (the checks of the 2026-08-22 gate) | paired evaluator |
| ACT-5 | No ratchet | anchors return to within $\epsilon$ of 1 after $5\kappa_a\tau_i$ without re-expansion | policy log |

The 2026-09-11 criteria A-0 to A-5 are retired (Appendix R).

---

## Pathway G — Propagation across levels (turnover normalisation)

### §0 Purpose and scope

Deep levels complete few outcomes per run, so a learner at a deep level has too
little data. RusKey reports the same for its FLSM-tree: training every level
separately failed from Level 3 onward [RusKey, §7]. This pathway lets deep
levels use what upper levels learn, without copying decisions downward and
without stopping deep levels from learning.

**Scope.** Only *interior* levels, $1 \le i \le L-1$, which have a level below
them, can be pooled. L0 is tiered and has its own trigger action (A3′). The last
level has no output level, and its only role is whether the tree deepens
(Lemma A.6). Both are separate, unpooled agents (Pathway H).

### §1 Related work: RusKey's policy propagation

What RusKey does [RusKey, §5]:

- **Why deep levels lack data.** Compaction at a deeper level is exponentially
  less frequent, because FLSM merges a whole level at a time.
- **Case 1, uniform bits-per-key.** Only Level 1's policy $K_1$ is learned, with
  the other levels held fixed; every deeper level is then set to $K_1$.
- **Case 2, Monkey filters.** Levels 1 and 2 are learned until stable. Deeper
  levels are then set by a closed-form recurrence (their Lemma 5.1), derived by
  minimising a per-level cost in which false-positive rates grow by $T$ per level,
  and rounded to an integer.
- **After the transfer,** deep levels are not learned.
- **Also in Lerp:** one independent DDPG agent per level; actions limited to
  $K-1$, $K$, $K+1$; reward $\alpha\cdot(\text{level latency}) + (1-\alpha)\cdot
  (\text{end-to-end latency})$.

Two passages in the paper are inconsistent with the rest; describe the method
from the lemma and the figure, not from these sentences:

- The Case 2 bullet says Level $i$ has *lower* bits-per-key than Level $i+1$,
  while the Monkey description and the lemma have false-positive rates *rising*
  with depth.
- The Fig. 9 text says the policy gets lazier with depth, while the figure
  (10, 8, 3, 1) and the lemma's worked example (9, 7, 3, 1) show $K$ falling
  with depth, i.e. more aggressive deeper.

**How this pathway differs.**

| | RusKey (Lerp) | This pathway |
| --- | --- | --- |
| What propagates | the chosen $K$, copied (Case 1) or extrapolated by a closed form (Case 2) | a value estimate in level-free units; each level still decides from its own state |
| Justification | the closed-form optimum of a per-level analytic cost | each level faces the same problem on its own clock (Proposition G.1) at its own prices (Proposition G.2); differences between levels are measured inputs |
| Deep levels after propagation | fixed | keep learning; their own correction grows with their own data (Lemma G.5) |
| Timing | sequential: learn L1 (and L2) to convergence, then transfer once | concurrent and continuous |
| Why deep data is scarce | deeper compactions are exponentially rarer (whole-level merges) | with RocksDB's file-by-file compaction, job counts are similar across levels; completed *turnovers* are about $T/\tilde\rho$ times rarer per level |
| Knob | runs per level, $K \in [1, T]$, in FLSM | target multiplier on leveled RocksDB ($K = 1$); L0 trigger separately |
| Neighbours | level statistics and all levels' policies in the state | the burst about to land from above, as an explicit forecast input |
| Reward | $\alpha$-mix of level and end-to-end latency | attributed priced cost plus a counterfactual neighbour charge (H §3) |

**Shared ideas, cited to RusKey:** one agent per level, and actions limited to one
step at a time.

### §2 Two propositions: the level clock and the price per turnover

Definitions are in §1.1: pass-through $\rho_i$ over merges, trivial-move share
$\xi_i$, net pass-through $\tilde\rho_i$, overlap $o_i$, inflow $\lambda_i$,
outflow $\mu_i$, turnover time $\tau_i$, level clock $\theta_i$, survival to
level $i$ $\pi_i$. In addition, per level: $e^f_i$ filter probes per Get at level
$i$, $e^b_i$ false-positive block reads per Get at level $i$ (the hit's block
read goes to the hit-read bucket, Pathway D §4), $\nu_i$ runs seeked per
scan at level $i$ (1 if the level has a file at or after the seek key), and the
holding price
$\sigma_i = c_sN_i/(c_w\bar q)$, with $N_i$ the operations served per turnover
of level $i$ (§1.1): the cost of holding one byte while level $i$ turns over
once (space is charged per operation served, Pathway D §1), in units of the
cost of writing one byte.

**Proposition G.1 (each level runs on its own clock).**

- (a) $\dfrac{d\varphi_i}{d\theta_i} = \dfrac{\lambda_i - \mu_i}{\bar\lambda_i}$.
- (b) In steady state, $\tau_{i+1} = (T/\tilde\rho_i)\,\tau_i$.

*Proof.* (a) $dB_i/dt = \lambda_i - \mu_i$; substitute $B_i = \varphi_iC_i$ and
$dt = \tau_i\,d\theta_i$ with $\tau_i = C_i/\bar\lambda_i$. (b) In steady state
$\bar\mu_i = \bar\lambda_i$, and by the definition of $\tilde\rho_i$ as the net
share of level $i$'s outflow that lands in level $i+1$ (merged bytes that
survive plus trivially moved bytes), $\bar\lambda_{i+1} = \tilde\rho_i\bar\mu_i$.
Hence $\tau_{i+1}/\tau_i = (C_{i+1}/C_i)(\bar\lambda_i/\bar\lambda_{i+1}) =
T/\tilde\rho_i$. $\blacksquare$

In plain terms: on its own clock every interior level fills at the same average
rate, one level's worth per turnover. What differs between levels is how many
seconds one tick takes — about $T$ times longer per level down.

*Evidence that jobs are not the scarce quantity.* In D-7 at T=2, L1, L2 and L3
released 935, 1,079 and 927 times (history §14.20). That arm compacted early, so
treat the numbers as indicative. At the old measured-phase ingest rate
(about 7 MB/s), T=10 gives $\tau \approx 2$ s at L1, 23 s at L2 and 230 s at L3,
so the old 160 s runs completed no turnover at L3.

**Proposition G.2 (price per turnover).** In steady state, level $i$'s attributed
part of $C_\beta$ (Pathway D §4: its write and read shares, and for space its
shadowed-garbage charge, which is not a share of $C_\beta$'s space term)
accumulated over one turnover, divided by $c_wC_i$ (the price of
writing one level's worth of bytes), is
$$\hat c_i = \beta_W(1-\xi_i)(\rho_i + o_i) + \frac{\beta_R\big(\tilde R^f e^f_i + \tilde R^b e^b_i + \tilde R_{sk}\nu_i + \tilde y_i\big)}{\pi_i} + \beta_S\,\sigma_i\,(1-\tilde\rho_i)\bar\varphi_i,$$
with the workload price ratios, the same at every level,
$$\tilde R^f = \frac{q_{pt}c_f}{c_w\bar\lambda_1},\qquad \tilde R^b = \frac{q_{pt}c_{blk}}{c_w\bar\lambda_1},\qquad \tilde R_{sk} = \frac{q_{sc}c_{sk}}{c_w\bar\lambda_1},$$
and $\tilde y_i = y_i/(c_w\bar\lambda_1)$, where $y_i$ is the mean unweighted L0
read cost per second charged to level $i$ by the slot-blocking rule (Pathway
D §4).

*Proof.* Over one turnover the level releases $\bar\mu_i\tau_i = C_i$ source bytes
in steady state. A share $1-\xi_i$ of them is merged and writes
$(1-\xi_i)C_i(\rho_i + o_i)$ bytes; trivially moved bytes write nothing
(Lemma D.7). Reads charged to the level cost $\beta_R\tau_i[q_{pt}(c_fe^f_i +
c_{blk}e^b_i) + q_{sc}c_{sk}\nu_i + y_i]$. The level's attributed garbage
$(1-\tilde\rho_i)B_i$ (Pathway D §4) costs $\beta_S(c_s/\bar q)
(1-\tilde\rho_i)\bar\varphi_iC_iN_i$ over the $N_i$ operations of the turnover.
Divide by $c_wC_i$ and use $\tau_i/C_i = 1/\bar\lambda_i =
1/(\bar\lambda_1\pi_i)$. $\blacksquare$

In plain terms: the write part has the same form at every level, with $\rho_i$,
$\xi_i$ and $o_i$ as measured inputs. The levels differ through those inputs,
through the read share they carry ($e_i$, and $\pi_i$) and through the holding
price $\sigma_i$, which grows about $T$ times per level down. These become
*inputs* to a shared model rather than things it must learn.

### §3 The propagation law

**Definition (law).** For an interior level $j$ in the pool $\mathcal P$, with
normalised state $\hat x_j$ and action $a$, the action value (expected future
normalised reward, rewards being negative costs) is
$$Q_j(\hat x_j, a) = b(\hat x_j, a) + f_\theta(\hat x_j, a) + \delta_j(\hat x_j, a),$$
where $b$ is the analytic prior (code, H §7), $f_\theta$ is a learned correction
shared by the pool, and $\delta_j$ is a small per-level correction. Both learned
parts start at zero (cold start, H §4). The law has five rules:

- **(G-i) Pooled training in level-free units.** Every pooled level's transitions
  enter one training set after conversion: reward
  $\hat r = -(\text{level cost over the transition})/(c_wC_j)$, the level
  cost being H §3's (attributed cost plus neighbour charge), and discount
  $\gamma_j = \exp(-\Delta N/(n_HN_j))$, where $\Delta N$ is the number of
  operations served over the transition, $N_j$ the operations per turnover of
  level $j$ (§1.1), and $n_H$ the look-ahead in turnovers, the same at every
  level. The discount runs on operations, like $J_\beta$: on the wall clock, any
  action that delays operations — a stall, or compaction that slows the
  foreground — would lower every later discounted cost by about the delay's
  share of the horizon, a gain $J_\beta$ does not contain. $f_\theta$ is fitted
  on this pooled set. Pooling
  converted transitions is the "shared experience" option; the level-specific
  inputs keep it from imposing one level's garbage rate on another. Every length
  "in turnovers" in Pathways G and H — a transition's length, the time since a
  release, the exploration spend, $n_{\text{turn}}$ — is counted in operations,
  $\Delta N/N_j$; it equals $\Delta t/\tau_j$ while the operation rate is
  steady, which is the sense of the level clock $\theta_i$ in Proposition G.1.
- **(G-ii) Per-level correction.** $\delta_j$ is fitted only on level $j$'s own
  transitions, pulled toward zero with a strength of $n_0$ turnovers
  (Lemma G.5). Each transition is counted by its duration in turnovers, because
  decisions inside one turnover are strongly correlated.
- **(G-iii) Own decisions.** Each level acts on its own $Q_j$ and its own state.
  Nothing is copied between levels.
- **(G-iv) Cadence.** Level $j$ decides every $N_j/k$ operations, with $k$ fixed
  in advance, so every transition serves the same number of operations and
  carries the same discount, $e^{-1/(n_Hk)}$. During a write stop no decision
  falls due; RocksDB's own triggers act. Decisions that fall due together go
  into one `SetOptions` call (A-Impl-5).
- **(G-v) Membership.** A level joins $\mathcal P$ only if it passes the
  admission test (§4). A level that fails keeps its own model.

**Normalised state** of level $j$, all dimensionless:

- own: $\varphi_j$, $\bar m_j$, $d_j$, $s_j$, and time since its last release in
  turnovers;
- neighbours: $\varphi_{j\pm1}$, $m_{j\pm1}$;
- incoming burst, for $j \ge 2$:
  $b_j = \tilde\rho_{j-1}(\varphi_{j-1} - m_{j-1})^+/T$, the share of level $j$'s
  capacity about to land from above;
- slot contention $\chi_j = (\hat\omega_j, \zeta)$: over the last decision
  interval, level $j$'s mean wait per release for the compaction slot, counted
  in operations served meanwhile and in units of the decision interval, and the
  share of the interval's operations served while the slot was busy
  (Proposition G.4). A job's wait runs from the later of the level's score
  reaching 1 and its previous job ending, to the job starting; $\hat\omega_j$
  is carried forward from the last interval with a release. These are averages
  over the interval, not snapshots: the slot changes state several times a
  second, far faster than any interior level's decision interval;
- inflow ratio $\ell_j = \lambda_j^{\text{recent}}/\bar\lambda_j$: whether
  inflow is running above or below its average;
- queue position: the number of due levels (L0 included) whose score exceeds
  $\max(s_j, 1+\epsilon)$, i.e. how many levels RocksDB's score order would try
  before a compaction at $j$, now or after "compact" (Lemma A.5: an action
  changes eligibility, not admission); and where the running compaction's
  start level lies relative to $j$ (none, above, same, below);
- backlog: RocksDB's pending-compaction estimate, with the multipliers applied
  (A-Impl-3), over the held SST bytes $H$; and L0's distance to the slowdown
  trigger, $(K_{\text{slow}} - k_0)/K_{\text{slow}}$. Both are the same at every
  level. The estimate is divided by $H$, not by the soft pending-bytes limit,
  which here is 64 GiB against about 3 GiB held and would read near 0;
- two levels down, and the bottom: $\varphi_{j+2}/m_{j+2}$ (the last level's
  value when $j+2 = L$; 0 with an absent flag when $j+2 > L$), and the last
  level's free room $(m_LC_L - B_L)^+/(m_LC_L)$. A burst that overflows level
  $j+1$ reaches $j+2$ next (Theorem A.1, containment), and the last level's
  room decides whether the tree deepens (Lemma A.6);
- level terms: $\hat\rho_j$, $\hat\xi_j$, the overlap constant $c_j$, $\pi_j$,
  $e^f_j$, $e^b_j$, $\nu_j$, $\sigma_j$;
- workload price ratios $\tilde R^f$, $\tilde R^b$, $\tilde R_{sk}$;
- the priority vector $\beta$, and L0's $k_0/K_0$.

**Proposition G.3 (normalisation does not change a level's decisions).** Define
level $j$'s objective with the discount of (G-i). Then its action values in
currency equal $c_wC_j$ times its normalised action values, so every ranking of
actions, and every greedy choice, is the same in both units.

*Proof.* Every reward of level $j$ is divided by the same positive constant, and
the discount depends on $\Delta N/N_j$ in both. The Bellman equation is
therefore scaled by that constant, and so is its unique fixed point. Ranking is
invariant under positive scaling. $\blacksquare$

*The modelling choice this rests on.* Discounting per turnover, counted in
operations, rather than per second is a choice, justified by Proposition G.1:
a level's outcomes arrive on
its own clock. It gives deep levels a longer horizon in seconds, which the
2026-09-11 controller lacked (0.98 per second, about 50 s of look-ahead at every
level).

**Proposition G.4 (when two levels can share one value function).** Write
level $j$'s normalised state as $\hat x_j = (z_j, \vartheta_j)$: $z_j$ the
dynamic part (fills, multipliers, time since release, neighbours, burst, inflow
ratio, contention, queue position, backlog, the fills two levels down and at the
bottom) and $\vartheta_j$ the level terms and prices, which are fixed
within a run and include the overlap constant $c_j$. Hold the other agents'
policies fixed. Let levels $i$ and $j$ satisfy:

- (c1) conditional on $(\hat x, a)$, the law of the next normalised state and of
  the transition's length in turnovers is the same function at both levels. In
  particular $\hat x$ is Markov: the next state depends on the rest of the tree
  only through $\hat x$;
- (c2) the reward over a transition is the same function of $(\hat x, a, \hat
  x')$ at both levels. The attributed cost is, by the per-transition form of
  Proposition G.2's accounting, once $c_j \in \vartheta_j$. The neighbour
  charges of H §3 are the same function only if both levels' neighbours are
  pool members; at the pool's edges (next to L1 or the last level) the
  difference enters $\varepsilon_r$ below;
- (c3) one compaction job moves or delivers a negligible share of the level
  (source file and incoming batch $\ll C_i$).

Then the two levels' optimal normalised action values are the same function of
$\hat x$.

*Proof.* Under (c1) and (c2), and with the same admissible actions as a
function of $\hat x$ (the masks' $\varphi_{\min}$, $\epsilon$, $m_{\min}$,
$m_{\max}$ and $\alpha$ equal at both levels), the two normalised problems are
one semi-Markov decision problem. With the cadence of (G-iv) every transition
serves $N_j/k$ operations, so its discount is $\gamma = e^{-1/(n_Hk)} < 1$. The
Bellman operator is a contraction with a unique fixed point. (c3) is what makes
(c1) plausible on the level's own clock (A5). $\blacksquare$

*When the conditions hold only approximately.* Let the expected rewards given
$(\hat x, a)$ differ by at most $\varepsilon_r$, and let $|r| \le R$. If the
laws of the next state differ by at most $\varepsilon_P$ in total variation,
uniformly in $(\hat x, a)$, then $\lVert Q^\star_i - Q^\star_j\rVert_\infty \le
\varepsilon_r/(1-\gamma) + 2\varepsilon_PR/(1-\gamma)^2$ [Kearns & Singh,
simulation lemma], with $1/(1-\gamma) = n_Hk + \tfrac12 + O(1/(n_Hk))$. Total
variation has no scale: a variable averaged over the level's own decision
interval ($\zeta$, $\hat\omega_j$, $\ell_j$) has a law that tightens with depth,
which drives $\varepsilon_P$ toward 1. The useful form assumes $\gamma V^\star_j$
is $L$-Lipschitz in the next state and uses the Wasserstein-1 distance
$\varepsilon_W$ instead: $\lVert Q^\star_i - Q^\star_j\rVert_\infty \le
(\varepsilon_r + L\varepsilon_W)/(1-\gamma)$. Lipschitz continuity is itself an
assumption; the mask thresholds can make $V^\star$ jump.

*Contention is where (c1) is only approximate.* With `max_background_jobs = 2`
and `max_background_flushes = max_background_compactions = −1` (db_bench's
defaults; the pipeline sets only the first, fingerprint field `bg2`),
`DBImpl::GetBGJobLimits` gives one flush slot, $\max(1, \lfloor 2/4\rfloor)$,
and one compaction slot, $\max(1, 2-1)$. The speed-up path can only set the
compaction count to one, so it is one in every state. The bottom-priority pool
counts against the same limit and db_bench starts it with no threads, and
`max_subcompactions` is 1 (`db/db_impl/db_impl_compaction_flush.cc`,
`GetBGJobLimits` and `MaybeScheduleFlushOrCompaction`; unchanged from the
pinned base `7ea2d73` to the recorded commit `25468bbaa`). A level that becomes
due while the slot is busy, or while a level with a higher score is due, waits.
The wait cannot be made level-free: the slot changes state on the wall clock,
several times a second at every depth, so on level $j$'s clock its dynamics run
about $T/\tilde\rho$ times faster per level down. What makes pooling possible is
that the wait is short on a deep level's clock. Let $\omega_j$ be the mean of
$\hat\omega_j$: the mean wait per release in units of level $j$'s decision
interval $N_j/k$. The contention part of $\varepsilon_W$ (per unit of $L$)
between two levels is then at most about
$\omega_i + \omega_j$ (a heuristic: a delay moves outflow across a decision
boundary with probability about its length over the interval, if decisions are
not synchronised with releases). $\omega_j$ falls roughly $T/\tilde\rho$-fold
per level down and is largest at L1, one reason L1 is expected to fail. It is
measured (Gate N0, item 3) and bounded in the admission test (§4).

The content is in (c1) and (c2). The admission test screens them on native
runs; PROP-1b re-checks (c1) under the controller.

**Lemma G.5 (how a level's trust in its own data grows).** Let $\delta_j$ be
linear in $k$ standardised, mutually orthogonal features, fitted by least squares
with penalty $n_0\lVert w\rVert^2$ on $n_j$ own samples. Each fitted coefficient
equals the own-data least-squares coefficient times $n_j/(n_j + n_0)$.

*Proof.* With $X^\top X = n_jI$, the penalised solution is $w = (n_jI +
n_0I)^{-1}X^\top y = \frac{n_j}{n_j+n_0}\cdot\frac{X^\top y}{n_j}$, and
$X^\top y/n_j$ is the unpenalised solution. $\blacksquare$

In plain terms: with no data of its own a level uses the shared model; after
$n_0$ turnovers of its own, half of its correction comes from its own data. Keep
$\delta_j$ to a few features so it can be fitted from little data.

### §4 Admission test (the collapse test)

From static runs ($m \equiv 1$, one configuration per (workload, $T$, $K_0$)),
compute for each interior level, on the level's own clock:

- the fill sampled at fixed points of the level clock (every $1/k$ turnover),
  and the fill at release;
- bytes released per turnover, in units of $C_i$;
- $(1-\xi_i)(\rho_i + o_i)$ and $\tilde\rho_i$ per turnover;
- the normalised inflow ratio $\ell_i$;
- the mean slot wait per release, $\omega_i$, in decision intervals (G.4).

$(1-\xi_i)(\rho_i + o_i)$ and $\tilde\rho_i$ are model inputs; comparing them is
a support screen, so that $f_\theta$ is not extrapolated beyond the levels it was
fitted on.

Statistics whose scale is set by the file size rather than the level are (c3)
diagnostics, reported but not compared: jobs per turnover, and time between
jobs in turnovers, both fall by about $T/\tilde\rho$ per level whatever the
controller does.

A level joins the pool only if it is shown equivalent to the reference level,
named in advance (the shallowest candidate interior level). For each compared
statistic $s$, the differences in mean and in the 10th and 90th percentiles, in
the statistic's own units, must have 90% intervals inside
$\pm\delta_{\text{adm},s}$. Margins are fixed in advance in those units; for
statistics in fill units they are no smaller than the file granularity
$F_{\text{sst}}/C_i$ of the shallower level. The upper 95% bound on $\omega_i$ must be below $\omega_{\max}$. Intervals
come from a moving-block bootstrap over whole turnovers (all events of a
turnover together), resampled within each run, with block length $b$ fixed in
advance. Requiring every statistic to pass is an intersection–union test, so the
chance of admitting a level that differs by more than its margin on any one of
them is at most 5% without a multiplicity correction (asymptotically; the
intervals are bootstrap intervals). The minimum number of turnovers per level,
$n_{\min}$, is fixed in advance by simulation: resample the reference level's
own turnovers into two pseudo-levels, which must pass with probability at least
0.8; shift one statistic at a time to its margin, the others unshifted, and in
each case the pair must pass with probability at most 0.05.

A Kolmogorov–Smirnov distance is reported but is not the criterion. It has no
scale, so it rejects tight distributions that differ by less than one file. Its
plug-in value is also biased upward: at 30 turnovers, the upper 95% bootstrap
bound between identical distributions has a median of 0.43, so identical levels
would almost never pass.

A test that merely fails to reject a difference is not evidence of sameness:
with few turnovers it passes for lack of data. Static runs measure native
dynamics, while (c1) must hold under the controller, so membership is re-checked
on the learner's own transitions by the conditional check PROP-1b.

Expected failures: L1, which is fed in L0-sized batches and whose job size is
comparable to $C_1$ at small base sizes; the last level; and the level above the
last whenever the last level is far from its target, since its fanout
$f_{L-1} = B_L/(m_{L-1}C_{L-1})$ is set by the last level's fill (at T=10 on
`Assoc`, $f_3 \approx 0.6$ against $f_2 = 10$), so the T=10 pool may be L2
alone. Existing artifacts already cover levels that complete at least two
turnovers per run — roughly L1–L6 at T=2 and L1–L2 at T=10. Levels without
enough turnovers in existing artifacts (L3 at T=10) are decided on Gate N2's
static runs.

**Scope decision (2026-09-29): propagation is claimed only where the tree is
deep enough to pool.** A pool needs at least two admitted levels. L1 and the
last level never qualify, and the level above the last qualifies only when the
last level is near its target, so a pool needs the last populated level at L5
or deeper, or at L4 when L4 is near its target. With
about 3 GiB of `Assoc` data that means T=2 (populated to L8, candidates L2–L6);
T=6 is decided by the admission test. At T=10 the tree reaches L4 with L4 far
below target, so the pool is at most L2 alone and nothing propagates. Every
level then keeps its own model, and L3, which completes about one turnover per
run, learns little and stays close to its prior and to hold. Pooling a level
that failed admission (starting it from $f_\theta$) is not done: at T=10 it
would amount to copying L2's policy into a level whose merges write about
1.6 bytes per byte moved against L2's 11, and it would use $f_\theta$ outside
the levels it was fitted on. More data at T=10 (about 17 GiB, so that L4 nears
its target and L3 can join L2) is the escalation path, to be decided by one
static run at that size before any other.

### §5 What propagation does and does not deliver

By Lemma D.10, an interior level's own filter probes do not depend on its
multiplier (except through the L0 run count: directly for L1, weakly for every
level through the shared compaction slot), and a scan seeks each level
with a file at or after the seek key once however full it is. Raising $m_i$
moves hits up from deeper levels (Proposition D.13(ii)): level $i$ takes over
hit reads, which go to the hit-read bucket and are charged to no level
(Pathway D §4), and the probes at levels $i+1..L$ fall. The saving, which can
be material under skew, lands in other levels' attributed costs; the neighbour
charges of H §3 do not carry it, since their one-step prediction models bursts
and overlap only. It is a static effect of the profile and belongs to
$\Theta_s$. Pooled interior agents
therefore mainly save write and space cost — timing releases against neighbour
fills and incoming bursts, and holding data so merges drop more garbage — and,
in read priority, keep the compaction slot free while L0 is due or about to be
(Pathway A §4 (f)), which the slot-blocking charge prices.

The dynamic read levers are the L0 trigger, L0's use of an idle slot, interior
levels' yielding of the slot, and depth. Only slot yielding involves the pool.
The interior profile's read effect is static. In read priority any read gain
comes mainly from the L0 agent, from slot timing and from not deepening the
tree. This agrees with the 2026-09-11 record.

### §6 Acceptance

| # | Criterion | Threshold | Instrument |
| --- | --- | --- | --- |
| PROP-1 | Admission recorded | collapse test run and pool membership recorded per (workload, $T$) before any learner run | Gate N1 |
| PROP-1b | Membership under control | for each pooled level, a one-step model of the normalised transition ($\hat x' \mid \hat x, a$: fill change, bytes released, time to next release) fitted on the pool has, on that level's held-out learner transitions, a mean residual whose 90% block-bootstrap interval lies inside $\pm\delta_{\text{kern}}$; and adding a level indicator lowers held-out error by less than $\delta_{\text{kern}}$. A level that fails leaves the pool at the next weight push, and the removal is logged. The check is conditional, not marginal, because each level's own policy changes its marginals | training log |
| PROP-2 | Transfer helps prediction | on each pooled level's held-out transitions, reported per level, the pooled model's normalised prediction error is below that of a model trained only on that level's data, paired over seeds | training log |
| PROP-3 | No harm | $J_\beta$ with the pooled deep-level agents acting is not worse than with those levels held (paired interval upper bound ≤ 0, or a margin fixed in advance); their attributed cost is reported alongside | paired evaluator |
| PROP-4 | Ablation | propagation on versus off (off = per-level models on own data), per mode | Gate N6 |
| PROP-5 | Related work | the §1 comparison with RusKey appears in the paper's related work | paper |

---

## Pathway H — RL architecture (Programme 1)

### §0 Overview

```
 workload (db_bench mixgraph / suite, Pathway B)
        |
        v
 RocksDB (pinned base + level_target_multipliers)  <-- SetOptions, batched (A-Impl-5)
        |  state read in-process                              ^
        v                                                      |
 C++ controller plugin (separately fingerprinted library)  ----+
   - L0 agent (K0), interior agents (pooled, Pathway G), last-level agent
   - masks, analytic prior b, fallback to native
        |  transitions out                ^  weights in, every Δ_push
        v                                 |
 Python trainer: shared model f_θ + per-level corrections δ_j, Double DQN
```

Inference happens where the state is. Training happens off the critical path.
Every learned quantity starts at zero at the start of each run (cold start).

### §1 Agents

- **L0 agent.** Actions on $K_0$ (Pathway A §2). Not pooled (A3′). It holds the
  main read lever (Proposition D.11).
- **Interior agents, levels $1..L-1$.** Actions on $m_i$. Levels that pass the
  admission test share one model (Pathway G); the others keep their own.
- **Last-level agent.** Actions hold and expand only; compact and defer have no
  meaning for a level far below its target. Its job is to stop the tree
  deepening when the last level nears its target (Theorem A.1). On `Assoc` at
  10M and T=10 it is idle, since the last level is far below its target
  (Theorem A.1, containment).
- **All agents** see the same priority vector $\beta$ and workload prices, and
  all start from hold.

### §2 State

- **Interior agents:** the normalised state of G §3.
- **L0 agent:** $k_0/K_0$, $\bar K_0$, flush rate over its mean, L1 fill
  $\varphi_1$, the Get-to-write and scan-to-write rate ratios, the price ratios
  $\tilde R$, $\beta$, and the compaction slot's busy share over the last
  interval (L0 is the level most exposed to the single slot, G.4); and, as
  for interior agents (G §3), queue position, backlog and L2's fill
  $\varphi_2/m_2$. For lever (e) of Pathway A §4 it also sees whether the slot
  is idle now (the queue position's "none"), and the active memtable's fill
  over `write_buffer_size` (how soon the next flush adds an L0 file; RocksDB
  property `rocksdb.cur-size-active-mem-table`).
- **Last-level agent:** $\varphi_L/m_L$, headroom $(m_LC_L - B_L)/C_L$, the
  incoming burst, queue position and backlog.

**Removed** from the 2026-09-11 state: the eleven stall-era pressure inputs, the
Lagrange multipliers, and the scan-work and space-estimate inputs (the scan
metric sat at its floor; the space estimate was retired by D-3).

### §3 Reward: attributed cost plus a counterfactual neighbour charge

For agent $i$ over a decision interval,
$$r_i = -\frac{c^\beta_i(\Delta t) + X_{i+1} + X_{i-1}}{c_wC_i},$$

- $c^\beta_i(\Delta t)$ is level $i$'s attributed, priority-weighted cost over
  the interval (Pathway D §4);
- $X_{i\pm1} = \bar V_{i\pm1}(x'_{i\pm1}\mid a_i) - \bar V_{i\pm1}(x'_{i\pm1}\mid
  \text{hold})$ is the change in the neighbour's expected future cost caused by
  level $i$ taking $a_i$ instead of holding. It is evaluated with the neighbour's
  current value estimate, converted to currency by $c_wC_{i\pm1}$, and a one-step
  prediction of the neighbour's state: the burst $i$ releases or keeps
  (Theorem A.1) and the overlap that implies (Lemma D.8).
- $X = 0$ when $a_i$ is hold.
- The divisor is a constant per agent. For the L0 agent it is
  $C_0 := K_0^{\text{cfg}}F$, the bytes L0 holds at its configured trigger, with
  $F$ as measured at $n_w$ and then held fixed; for the last-level agent it is
  $C_L$. A constant divisor leaves the agent's ranking of actions unchanged
  (Proposition G.3). The live $K_0F$ would not, since $K_0$ is the L0 agent's
  own action.

The charges do not model read shifts. A level that expands no longer pays the
block reads of the hits it takes over from deeper levels, since those go to the
hit-read bucket (D §4), but it is credited nothing for the filter probes saved
below it (G §5). The read effect of the profile is static and belongs to
$\Theta_s$. The one read effect an interior level has during a run, keeping L0
waiting for the compaction slot, is in its attributed cost $c^\beta_i$ through
the slot-blocking charge (D §4).

In plain terms: a level pays for what it spends, and also for what its choice
makes its neighbours spend later. That is how a compaction that is good for one
level but bad for the next one is discouraged.

**Proposition H.1 (why a counterfactual charge, not a shaping term).**

- (a) Adding $\gamma\Phi(s') - \Phi(s)$ to the reward, for any function $\Phi$ of
  the state, cannot change which policy is optimal [Ng et al.]. So charging a
  level for how its neighbour's value changes over time — a function of state
  that level $i$ observes — cannot make it take the neighbour into account.
- (b) The difference reward $D_i = G(a_i, a_{-i}) - G(\text{hold}, a_{-i})$, with
  $G$ the global cost and $a_{-i}$ the other agents' actions, changes by exactly
  $G$'s change when agent $i$ alone changes its action:
  $D_i(a') - D_i(a) = G(a', a_{-i}) - G(a, a_{-i})$ [Wolpert & Tumer].

*Proof.* (a) Ng et al., Theorem 1; for transitions of variable duration use
Proposition H.4. (b) The subtracted term does not depend on $a_i$. $\blacksquare$

*The approximation.* The reward above approximates $D_i$ in two ways: it
restricts $G$'s change to level $i$ and its two neighbours, and it estimates the
neighbours' change with learned values. ARCH-3 measures the one-step state
prediction the charge relies on. The design follows counterfactual credit
assignment in cooperative multi-agent learning [Wolpert & Tumer; Foerster et
al.]. It is not RusKey's $\alpha$-mix of level and end-to-end latency.

Two further gaps separate these rewards from $J_\beta$ even if every value were
exact. First, $\bar V_{i\pm1}$ is the neighbour's value under its own reward,
which includes the neighbour's charge for its effect on level $i$; so
$X_{i\pm1}$ carries back part of level $i$'s own future cost, which level $i$
also pays directly. A value head fitted to the neighbour's attributed cost alone
avoids this echo; which of the two is used is fixed in advance (§0.6), and a
Gate N6 ablation compares the two. Second,
a difference reward aligns one agent's unilateral
change with $G$ (Proposition H.1(b)), but agents that each maximise their own
return — discounted per turnover of their own level (G-i), while $J_\beta$ is an
undiscounted sum over the measured phase — can settle where no agent can
improve its own return and $J_\beta$ is still not minimal. The per-level returns
are therefore a training surrogate; every claim is scored on $J_\beta$ (Global
acceptance).

### §4 Learning rule

- **Double DQN** [van Hasselt et al.; Mnih et al.] on normalised rewards with
  the turnover discount of (G-i); Huber loss, a target network and gradient
  clipping.
- **Masked target** (fixes the defect found in the audit):
  $y = r + \gamma\,Q_{\text{target}}\big(s', \arg\max_{a' \in \mathcal A(s')}
  Q_{\text{online}}(s', a')\big)$, the $\arg\max$ taken only over the actions
  allowed in $s'$.
- **Residual form.** $Q = b + f_\theta + \delta_j$ (Pathway G). $f_\theta$ and
  $\delta_j$ start at zero; $b$ is code.
- **Replay** keeps only transitions with valid attribution; fallback intervals
  are excluded.

**Proposition H.2 (an unmasked target overestimates).** Let $\mathcal A(s')
\subsetneq \mathcal A$ for some reachable $s'$. If the target maximises over all
of $\mathcal A$, its fixed point $\tilde Q$ satisfies $\tilde Q \ge Q^\star$
pointwise, where $Q^\star$ is the masked fixed point. The inequality is strict at
every $(s, a)$ that reaches, with positive probability, a state $s'$ in which a
forbidden action has the highest value.

*Proof.* A maximum over a superset is at least the maximum over the subset, so
the unmasked Bellman operator dominates the masked one pointwise. Both are
monotone contractions, so iterating from the same start keeps the order, and the
limits satisfy $\tilde Q \ge Q^\star$. Strictness follows from the
positive-probability state. $\blacksquare$

In plain terms: the learner credits states with the value of actions it will
never be allowed to take there.

### §5 Timing, exploration, and the measured phase

- **Settle, then measure** (adopted from the parallel stream; amended
  2026-09-30, `docs/PREREGISTRATION.md` D-13). After the load, `db_bench`
  issues no operation until RocksDB's `WaitForCompact`, flushes included,
  returns. It then holds for $h_w$ = 10 s, during which every level's score
  must stay below 1 and $k_0 < K_0$. The measured phase starts at $n_w$, the
  first `mixgraph` operation, on the tree the arm's own load left. Every arm,
  native included, is scored from $n_w$ (A8), and the controller starts at
  $n_w$, so no controller action falls outside the scored phase. An arm not
  settled at the end of the hold is invalid. The earlier rule drained the
  backlog under live traffic and set $n_w$ from pilot runs with a margin,
  leaving the operations before $n_w$ unscored; this one needs no pilots and
  scores every `mixgraph` operation.
- **Default is hold.** Exploration departs from hold with probability $p_x$ per
  decision and never takes a masked action. $p_x$ keeps a floor after training,
  so the policy cannot freeze. The exploration spend is counted per level in
  turnovers.
- **Units.** The prior's action differences are expressed in the same normalised
  units as $Q$. This fixes the D-7 mismatch, where prior terms of about ±2 were
  added to $Q$ values in the thousands.

**Proposition H.3 (exploration has a cost; the 2026-09-11 Proposition B.3).**
Let the best static configuration $\theta^\star$ be constant over the run, and
suppose the optimal policy for the control problem is realisable as a constant
action. Then any online controller $\pi$ that does not know $\theta^\star$ and
explores non-degenerately has, over a finite horizon,
$J(\pi) = J(\theta^\star) + \mathcal R$ with $\mathcal R > 0$.

*Proof.* As 2026-09-11: under the hypothesis, $\pi$'s cost is $\theta^\star$'s
cost plus regret on every interval where its action differs, and any schedule
with a positive probability of a suboptimal action on a set of positive measure
contributes strictly positive regret. $\blacksquare$

The hypothesis — that a constant action is optimal — is exactly what Conjecture
B.3′ doubts.

*Consequence.* Exploration is reported as a cost, and the first $N_x$
turnovers after $n_w$ are logged separately.

**Proposition H.4 (shaping with variable durations; the 2026-09-11
Proposition D.1).** For semi-Markov transitions of duration $\tau$, the term
$F = \gamma^{\tau}\Phi(s') - \Phi(s)$ leaves optimal policies unchanged; the
single-step form $\gamma\Phi(s') - \Phi(s)$ does not when $\tau$ varies.

*Proof.* As 2026-09-11, following Ng et al. with the discount of each transition
raised to its duration. $\blacksquare$

It applies to any shaping term added later.

### §6 Where inference and training run

- **C++ plugin inside the RocksDB process.** It reads the state in-process at
  decision time, evaluates the per-level models, and applies `SetOptions` in one
  batched call. There is no socket round trip per decision, so the
  stale-answer rejection of D-12 (27–39% of answers) cannot occur.
- **Python trainer.** It receives transitions, trains, and pushes weights every
  $\Delta_{\text{push}}$ (fixed in advance). The deployed policy lags training by
  at most $\Delta_{\text{push}}$. The learning rule is off-policy, so it
  tolerates the lag. The weight version is logged with every decision.
- **A separately fingerprinted library.** Changing the controller does not
  change the database binary's hash, so the static comparator stays valid
  (audit §6). ARCH-5 checks that the plugin at $m \equiv 1$ reproduces native.
- **Fallback.** Plugin absent or weights invalid: $m \equiv 1$ and $K_0$ at its
  configured value (A-Impl-8).

### §7 Analytic prior $b$

$b(\hat x, a)$ is the expected change in normalised cost over one turnover from
taking $a$ instead of hold, computed from Lemmas D.7–D.10 and Proposition G.2
with the current measured inputs. It prices:

- a compaction now, at the current overlap (Lemma D.8), plus the slot-blocking
  charge it would incur if L0 falls due while its job holds the slot (Pathway D
  §4), from L0's fill and the active memtable's fill;
- a deferral, by the garbage it keeps (Pathway D §4) and the burst it builds
  (Theorem A.1);
- an expansion, by its space bound (Lemma D.14);
- for the L0 agent, an early compaction, by the extra L0 → L1 overlap it writes
  (Proposition D.11) against the probes and seeks saved while the slot is idle.

It is clipped to $\pm b_{\max}$ in normalised units. It is code, not trained
weights, so cold start is preserved.

### §8 Removed from the 2026-09-11 architecture

- Lagrange multipliers, dual ascent and windowed hinges: Programme 1 has no hard
  constraints.
- The guard, which moves to Programme 2. RocksDB's own slowdown and stop
  triggers and pending-bytes limits remain the only safety mechanism.
- Socket-based inference and the snapshot staleness check.
- The per-second discount; the stall-era state inputs.

### §9 Acceptance

| # | Criterion | Threshold | Instrument |
| --- | --- | --- | --- |
| ARCH-1 | Learning health | TD error in normalised units bounded, final value below twice the reward scale; no NaN; residual magnitudes reported | training log |
| ARCH-2 | Masks | unit test of the masked target green (`rl_agent/tests/test_agent.py`); the training log's masked-target audit counts zero target argmaxes over a masked action; zero masked actions executed | tests, training log, policy log |
| ARCH-3 | Counterfactual input | one-step prediction of neighbour fill after an action versus realised, error reported per level pair; if above a tolerance fixed in advance, that pair uses the local reward only | policy log |
| ARCH-4 | Exploration and no freeze | exploration share and cost reported per level; the chosen action varies with state (flip rate and correlation with $\varphi$ reported where a level has ≥ 10 turnovers) | policy log |
| ARCH-5 | Plugin parity | plugin at $m \equiv 1$ inside the oracle-parity envelope | paired evaluator |
| ARCH-6 | Inference agreement | C++ inference and Python evaluation of the same weights agree on ≥ 99.9% of logged decisions | replay |

---

## Pathway B — Workloads and garbage

### §0 Purpose

Programme 1 is measured across a suite of workloads, for two reasons. No static
setting is best everywhere, which is what gives a controller something to add
across workloads (Definition C.5). And each priority mode needs a workload on
which it has room (Pathway D §5).

### §1 The suite

| Family | Construction | Label in the paper |
| --- | --- | --- |
| UDB `Assoc` (primary) [Cao et al.] | `db_bench` mixgraph with the published fit (B1), with the deviations of §2 | "Assoc key distribution and operation mix at the project's record size" |
| Other Cao et al. mixes | published fits where available, e.g. the ZippyDB mix | realistic |
| Zipfian mixes in the style of YCSB A, B, C and F [Cooper et al.] | `db_bench` or YCSB | standard |
| Read-heavy / write-heavy / mixed | operation mix swept | synthetic |
| Garbage level | overwrite share and key-space size swept, giving low and high $g_{\text{flow}}$ | synthetic |
| Hot ranges | `keyrange_num` $\in \{5, 30, 100\}$ | synthetic |
| Deletes (B3) | `mix_delete_ratio` $\in \{0, 3, 6\}\%$ | synthetic |
| Phases (B2) | two phases first, more later; in-process `db_bench` phase patch | synthetic |

Every run records its family and parameters in the fingerprint. A static
comparator measured on one family is never a comparator for a policy measured on
another (the workload form of the knob-parity rule).

### §2 Implementation notes carried forward

1. **(Done 2026-09-20.)** `WORKLOAD_SKEW` selects the family. Deviations from the
   published `Assoc` fit: `value_theta` is 925.5, not 0, keeping the mean value
   size at the project's 960 bytes so the level ladder and the $T$ sweep stay
   comparable; `mix_max_value_size` is 65536, because `db_bench` applies it as
   `val_size % value_max` and the default 1024 wraps 6.85% of draws down to as
   little as one byte, pulling the mean to 890.2.
2. **Phases (B2)** as a `db_bench` phase patch. Trap:
   `QueryDecider::Initiate` resets its range total but appends to `type_` and
   `ratio_` without clearing them, and `GetType` returns the first threshold
   the draw falls below (`tools/db_bench_tool.cc`). A second `Initiate` for a
   new phase leaves the first phase's thresholds in front, so the old mix keeps
   running and nothing reports it. Clear both vectors, or build a new
   `QueryDecider`, at each phase boundary. Phase boundaries go in the manifest
   and fingerprint.
3. **Deletes (B3)** as a fourth operation type in the same patch, appended at
   index 3 after Get, Put and Seek. `GetType` returns a position in the ratio
   vector, so reordering the vector would remap every existing mix; a delete
   ratio of 0 leaves the existing thresholds unchanged.
4. **Per-level survival.** Per compaction record $S$, $O$, $X$ and the source
   level, excluding trivial moves (whose output equals input and would bias the
   ratios toward 1), and record trivially moved bytes per source level
   separately. Derive $\rho_i$, $o_i$, $t_i$ and $\xi_i$, dropped bytes, and
   the 2026-09-11 merge survival $\eta_i = X/(S+O)$.
5. **Hindsight-oracle runner.** Per phase, run the static sweep and record the
   best static cost; compose them into $\mathcal G$.

### §3 Theory

**Proposition B.1′ (pooled survival floor; the part of the 2026-09-11
Theorem B.1 that is proved).** Suppose total compaction input over the run is at
least the bytes the user wrote. Then pooled merge survival — total compaction
output over total compaction input — is at least $1/S_{\text{flow}}$.

*Proof.* Each compaction's output is its input minus what it drops. Each obsolete
byte is dropped at most once, so total drops are at most $N_{\text{written}} -
N_{\text{live}}$. Hence output/input $\ge 1 - (N_{\text{written}} -
N_{\text{live}})/\text{input} \ge N_{\text{live}}/N_{\text{written}}$, using
input $\ge N_{\text{written}}$. $\blacksquare$

**Status of the 2026-09-11 ceiling.** Theorem B.1 went on to evaluate the
formula $\sum_i\eta^i$, whose $\eta$ is a ratio of merged volumes as in
Theorem A.2(iii), at $\eta = 1/S_{\text{flow}}$, and called the result a
ceiling on the achievable reduction of $W - 1$. The floor justifies neither
step. It bounds pooled merge survival $X/(S+O)$, whose denominator holds the
overlap, so a dropped byte lowers the volume ratio $1 + o_i$ times as much as it
lowers merge survival. And it limits pooled survival, not how survival is
spread over the merge stages. The first draft of this revision offered a
counterexample in which the first stage keeps 0.245 of its input and every later
stage keeps all of it. That counterexample is withdrawn: every flushed byte
enters stage 0, so it drops 0.755 bytes per byte written, while only
$g_{\text{flow}} = 0.278$ bytes of garbage exist per byte written. It met the
floor because its drops are 0.278 of its *total* compaction input (2.71 per
byte written); the floor sees only total input and cannot tell the two apart,
which is why it bounds nothing about where survival goes. The bound that holds
in the stage model is the following.

**Proposition B.1″ (the drop budget bounds the elision saving; stage model).**
Fix a window in which A7 holds. Model the tree as merge stages $0..L-1$ with the
accounting of Lemma D.7 and no trivial moves: bytes that are not dropped pass
through the stages in order; a stage-$i$ merge writes $w_i = 1 + o_i$ bytes per
source byte, less one byte for each byte it drops, from the source or from the
overlap; the $o_i$ are held fixed, as Theorem A.2(ii) holds its weights. Let $V$
be the bytes entering stage 0 in the window (the flushed bytes), and $G$ the
obsolete bytes available to compaction in it: garbage resident at the window's
start plus obsolete versions flushed during it. Against the same flow with
nothing dropped, the compaction write cost saved is at most
$G\,(1 + \sum_{i=1}^{L-1}w_i)$, so the relative saving is at most
$$\frac{G}{V}\cdot\frac{1 + \sum_{i\ge1}w_i}{\sum_{i\ge0}w_i} \;\le\; \frac{G}{V},$$
the last step because $w_0 \ge 1$. With equal weights $w$ the factor is
$(1 + (L-1)w)/(Lw)$, between $(L-1)/L$ and 1.

*Proof.* Let $D_j$ be the bytes dropped by stage-$j$ merges. In steady state a
level releases what reaches it, so the volume entering stage $i$ is
$V_i = V - \sum_{j<i}D_j$, and by Lemma D.7 stage $i$ writes $w_iV_i - D_i$.
With nothing dropped the cost is $V\sum_iw_i$, so the saving is
$\sum_jD_j\,(1 + \sum_{i>j}w_i) \le \big(\sum_jD_j\big)(1 + \sum_{i\ge1}w_i)$.
Every obsolete version is dropped at most once, so $\sum_jD_j \le G$.
$\blacksquare$

*Scope.* This is a statement about the stage model. Three things lie outside
it. The comparison flow keeps every stale version at the bottom, so $B_L$ and
$o_{L-1}$ would grow, and the model holds them fixed. Trivial moves write
nothing: a heavy share at stage 0 would make the effective $w_0$ fall below 1,
and a heavy share at later stages, as over a window that contains the load
(Lemma D.7), lowers the no-drop cost, so every relative figure below assumes
that trivial moves are rare; with up to 71% of the bytes leaving levels $\ge 1$
moving trivially, as on the uniform whole run, a bound valid for any placement
of drops can exceed 58%. A window that contains the
load is not steady.

*Values on `Assoc`.*

- **Whole run, load included** (the view in which $S_{\text{flow}}$ is
  measured): memtable drops remove the same bytes from $G$ and $V$, so
  $G/V \le g_{\text{flow}} = 0.278$. The window is not steady: the load grows
  the tree from empty, and bytes that end in L1–L7 never cross the later stages.
  Because levels $1..L-1$ end at target in both flows, the saving is still at
  most $G(1 + \sum_{i\ge1}w_i)$, but the no-drop cost is
  $\sum_iw_i(V - R_{\le i})$, with $R_{\le i}$ the bytes resident in levels
  $1..i$ at the end. With that growth counted and no trivial moves, the bound is
  about 26–32% of whole-run $W - 1$ at T = 2 (27–29% at T = 6, 25–32% at
  T = 10) for overlap constants from 1 to 0, about half the 58% of the
  2026-09-11 formula at T = 2.
- **Measured phase** (the view the objective scores, A8): every Put overwrites a
  live key, so each user byte creates an obsolete byte, and the tree the load
  leaves holds almost none, since its keys are unique. $G/V$ is then close to 1
  and so is the bound: it says little. Native compaction also spends most of the
  budget. Resident $S$ of 1.03–1.09 at run end leaves 0.09–0.27 GB of the
  phase's 1.17 GB of obsolete bytes, so native drops 77–92% of them.

What binds in the measured phase is *where* drops happen. A stale version can be
dropped only in a merge that also holds a newer version of its key, and on
`Assoc` the loaded versions sit in the deepest levels (at T = 2, L6–L8 hold
about 84% of them and L8 alone about a third). So a first overwrite drops its
loaded version deep, while repeat overwrites of hot keys can meet high: merge
survival $X/(S+O)$ at L1 was 0.84, 0.86 and 0.93 at T = 2, 6 and 10 (D-4).
Against native rather than against no drops, a controller can add at most the
garbage native leaves, $G_{\text{res}} = (S-1)N_{\text{live}}$, and can
otherwise only move native's drops higher. In the stage model its saving over
native is therefore at most
$$\sum_j D^{\text{nat}}_j\sum_{i=1}^{j}w_i \;+\; G_{\text{res}}\Big(1 + \sum_{i\ge1}w_i\Big),$$
with $D^{\text{nat}}_j$ native's dropped bytes per level (§2 item 4). It
follows from $\sum_jD^\pi_j \le G = \sum_jD^{\text{nat}}_j + G_{\text{res}}$,
with every drop placed at stage 0 where it saves the most. This ignores
locality, so it is an upper bound, and it is computable from the Hull-0
artifacts. A bound that uses locality needs a model of where overwrites of one
key meet, and is open. The proof also shows where elision pays most: a byte
dropped at stage $j$ saves one byte there and every later stage, so drops high
in the tree are worth the most.

**Measured on `Assoc`** (D-3, measured denominator): $S_{\text{flow}} = 1.386$ at
T = 2, 6 and 10; $g_{\text{flow}} = 0.278$; resident $S$ 1.03–1.09. The
constant-survival *estimate* — not established by its proof, and above what
B.1″ allows for the whole run at every ratio when trivial moves are rare — is
58%, 35% and 35% (audit §4).
The B.1 and B.2 tables of 2026-09-11 (uniform workload, defective live-data
estimate) are withdrawn; Lemma D.14 replaces B.2's space budget.

**Conjecture B.3′ (state-dependent value on a stationary workload).** On a
stationary workload there is a state-dependent trigger policy $\pi$ with
$J_\beta(\pi) < \min_{\theta\in\Theta_s}J_\beta(\theta)$.

*Status.* Unproven. The 2026-09-11 revision asserted it in its commentary on
Proposition B.3 (now Proposition H.3); B.3 does not imply it. No learned or hand-written policy on record lies
below the static frontier (audit §2). The candidate mechanisms are items (b)–(d)
of Pathway A §4. They are tested without a learner at Gate N3; if none works,
the claim narrows to suite robustness and changing workloads (Global
acceptance).

**Definition (phase-adaptivity gap), retained.**
$$\mathcal G = \min_{\theta\in\Theta_s}J_\beta(\theta) - \sum_p\frac{n_p}{N}\min_{\theta\in\Theta_s}J_{\beta,p}(\theta),$$
the excess cost of the best single static configuration over a hindsight oracle
that switches static configuration per phase, where $n_p/N$ is phase $p$'s share
of the measured operations; time shares would depend on each configuration's
speed.

**Corollary B.4 (retained).** $\mathcal G > 0$ is sufficient evidence that
adaptivity has value on that workload. $\mathcal G = 0$ is not evidence that it
has none, because $\mathcal G$ sees only the phase component of adaptivity.
Corollary D.12 now predicts $\mathcal G > 0$ in advance for the L0 trigger
whenever phases' best triggers differ.

### §4 Acceptance

| # | Criterion | Threshold | Instrument |
| --- | --- | --- | --- |
| B-1 | Skew raises resident garbage (retained) | garbage fraction under skew exceeds a skew-free control by ≥ 10 pp, on the measured denominator | evaluator |
| B-3 | Survival is measurable and responds (retained) | $\rho_i$ and $\eta_i$ per level, trivial moves excluded; lower under holding than under native at the held level | compaction log |
| B-4 | Shift produces a phase-adaptivity gap (retained) | $\mathcal G > 0$ with the 95% interval excluding 0 | hindsight-oracle runner |
| B-5 | Deletes activate compensated size (retained) | non-zero tombstone-driven file selections in the RocksDB LOG | LOG parse |
| WL-1 | Garbage is reported where it acts (replaces B-2) | per-level $\rho_i$ and dropped bytes per workload and mode | compaction log |
| WL-2 | The suite exercises every mode | for each mode, at least one workload whose best static setting differs from the balanced-mode best | Gate N2 |

B-2 is retired: its threshold used Theorem B.1's ceiling, which its proof does
not establish.

---

## Pathway C — Comparator

### §0 Purpose

Compare the controller with the best static setting for the same priced cost,
measured on the same workload and binary, in the same session. The claim is then
about a class of static settings rather than one tuned point.

### §1 The static class and the comparator

**$\Theta_s$ (fixed in advance, §0.6):**

- L0 trigger $\in \{2, 4, 8\}$, restricted to admissible values
  (A-Impl-6), so that triggers the byte branch makes identical are not counted
  twice;
- base size $\in \{8, 16, 32\}$ MiB;
- static multiplier profiles: uniform 1; the equal-fanout or survival-weighted
  profile at the measured $c$ and $v_i = a_i - t_i$ (Theorem A.2); a last-level-emptying
  profile that holds upper levels at 1.5–2 times from load (audit §3); uniform
  0.75;
- `compaction_pri = kMinOverlappingRatio`, pinned;
- $T \in \{2, 6, 10\}$, with 14 and 20 at one point for the cross-$T$ check.

**Comparator.** For each workload $w$ and mode $\beta$,
$\theta^\star_\beta(w) = \arg\min_{\theta\in\Theta_s}J_\beta(\theta, w)$, from
measured, seed-paired runs.

### §2 Theory

**Proposition C.1 (comparator validity; retained, restated for priced cost).** If
some $\theta \in \Theta_s$ has priced cost at most $\pi$'s on every term, then a
static configuration is at least as good as $\pi$ in every mode, and $\pi$'s
contribution is nil whatever it achieves against any single setting.

*Proof.* Immediate from dominance, since every $J_\beta$ has positive weights.
$\blacksquare$

**Corollary C.2 (retained).** "$\pi$ beats the native setting" is strictly weaker
than "$\pi$ beats $\theta^\star_\beta$".

**Corollary C.3 (steady-state gains belong to the static class; extended).** The
steady-state optima of Proposition D.11 ($K_0$ at fixed prices), Proposition
D.13 (interior profile, depth) and Theorem A.2 (survival-weighted profile) are
static settings. Any gain they explain belongs to $\Theta_s$, not to the
controller.

**Proposition C.4 (one hull serves every mode).** For every $\beta > 0$,
$\theta^\star_\beta$ is a vertex of the lower convex hull of the static points'
priced-cost vectors $(\mathcal C_W, \mathcal C_R, \mathcal C_S)$. The hull extracted once per workload
therefore contains every mode's comparator.

*Proof.* Proposition D.5 applied to the finite set $\Theta_s$. $\blacksquare$

**Definition C.5 (regret and suite robustness).** The regret of $\pi$ on
workload $w$ in mode $\beta$ is
$$\text{Reg}_\beta(\pi, w) = \frac{J_\beta(\pi, w)}{J_\beta(\theta^\star_\beta(w), w)} - 1.$$
$\pi$ is *suite-robust* in mode $\beta$ if
$$\max_w \text{Reg}_\beta(\pi, w) < \min_{\theta\in\Theta_s}\max_w\text{Reg}_\beta(\theta, w),$$
i.e. its worst regret across the suite is below the best worst-case regret of
any single static setting. This is where a min–max criterion properly belongs.
It is the regret form of robust tuning under workload uncertainty
[Endure].

A controller can be suite-robust without beating $\theta^\star_\beta(w)$ on any
single workload: it only has to avoid being far from the best static setting
everywhere, which no one static setting may manage.

### §3 Drift and repeats

- **Same session.** Comparator arms run in the same session as policy arms, in
  interleaved order, and the session id is recorded. The same static setting
  wrote 2.9–4.1% more at T=2 in later sessions (audit).
- **Repeats.** For the paired difference $d$ between an arm and its comparator,
  the interval half-width is $t_{n-1}\sigma_d/\sqrt n$, so the repeat count per
  cell is the smallest $n$ with
  $$n \ge \left(\frac{t_{0.975,\,n-1}\,\sigma_d}{h}\right)^2,$$
  where $\sigma_d$ is estimated at Gate N2 and $h$ is the half-width required —
  a quarter of the gap between $\theta^\star_\beta$ and the next-best static
  point, or an effect size fixed in advance.

### §4 Acceptance

| # | Criterion | Threshold | Instrument |
| --- | --- | --- | --- |
| C-1 | Static class sampled (retained, extended) | every knob level of $\Theta_s$ present; ≥ 4 hull vertices per (workload, $T$) | sweep output |
| C-2 | Hull points decidable (retained) | repeats until the interval width is under half the spacing between neighbouring hull points | paired evaluator |
| CMP-3 | Per-workload comparison | $J_\beta(\pi) - J_\beta(\theta^\star_\beta)$ with paired interval, per (workload, mode, $T$) | paired evaluator |
| C-6 | Cross-$T$ (retained) | comparison repeated with $\theta^\star_\beta$ taken over $T \in \{2, 6, 10, 14, 20\}$ | cross-$T$ sweep |
| CMP-7 | Suite robustness | Definition C.5 reported per mode | suite evaluator |
| CMP-8 | Same-session twins | every comparison paired within one session, session id recorded | run manifest |

The 2026-09-11 criteria C-3, C-4 and C-5 are historical (Appendix R).

---

## Pathway E — Guard (Programme 2; on hold)

Programme 1 has no service-level objective, so it has no guard. RocksDB's own
slowdown and stop triggers and pending-bytes limits are the only safety
mechanism, together with the multiplier and trigger bounds of A-Impl-6 and
A-Impl-7.

**Retained for Programme 2:** the 2026-09-11 Proposition E.1 (the realised
policy is a mixture of the policy and its projection onto the shield's action
set, so a shield that fires often replaces the policy) and Corollary E.2 (the
shield's influence is bounded by its override probability, and a shield action
set narrower than the policy's biases every override the same way), the
criteria E-1 to E-5 as defined there, and three calibration lessons:

- limits must be calibrated in the unit the criterion scores — frames, not
  episodes (the inspection-paradox finding of 2026-09-14);
- every term of the force condition must be calibrated, not only the per-level
  ones;
- calibration windows must exclude the post-load transient. E-5 was 6–17 times
  its limit over whole runs and 0–1.4% after the first 30 s (audit).

Verdicts to date are in Appendix R.

---

## Pathway F — Phase-aware objective switching (Programme 2)

**Status:** second programme. Its 2026-09-11 description — a layer above the
controller that switches a parameter set per detected or forecast phase; the
F-detect and F-forecast versions; the leak guard; switchable knobs only — is
retained. Three changes:

- Under Programme 1, what switches per phase is the priority vector $\beta$ (and
  $K_0$ at its phase optimum, Corollary D.12), not an SLO manifest. SLO manifests
  return with Programme 2.
- Corollary D.12 predicts $\mathcal G > 0$ for the L0 trigger in advance whenever
  the phases' best triggers differ.
- The audit's suggestion of a slow tuner above native RocksDB is this layer.

**Definition (three controllers over one schedule; retained).** For phases
$p = 1..P$ with boundaries known to the evaluator: $J_{\text{oracle}}$ switches
the best static setting per phase exactly at the boundaries (its advantage over
the best single static setting is $\mathcal G$); $J_{\text{react}}$ chooses from
the current window's measured mix, with no look-ahead; $J_{\text{pred}}$ chooses
from a predicted class for the next episode, with lead time $\ell$.

**Proposition F.1 (decomposition; retained).**
$\mathcal G = (J_{\text{static}} - J_{\text{react}}) + (J_{\text{react}} -
J_{\text{pred}}) + (J_{\text{pred}} - J_{\text{oracle}})$: the value of reacting,
the value of anticipating, and the remaining loss.

*Proof.* Telescoping. $\blacksquare$

**Proposition F.2 (catch-up bounds the value of anticipation; corrected).**
Suppose $J_{\text{react}}$ and $J_{\text{pred}}$ choose the same settings except
that the predictor switches $\ell$ earlier at each transition. Let
$e_r(t) \ge 0$ be the reactive controller's excess cost rate over the new
setting's steady cost during the first $\Delta_{\text{shape}}$ after a
transition (the catch-up), and assume the new setting's steady cost is a floor
for both controllers after the transition, and that running the new setting
early costs the predictor $w(t) \ge 0$ before the transition. Then, per
transition,
$$J_{\text{react}} - J_{\text{pred}} \;\le \int_0^{\Delta_{\text{shape}}} e_r(t)\,dt.$$
The difference can be negative if the pre-transition cost outweighs the saving.
A predictor reaches the bound only if it completes the reshaping before the
transition ($\ell \ge \Delta_{\text{shape}}$) at no pre-transition cost.

*Proof.* The two controllers differ only in $[-\ell, \Delta_{\text{shape}}]$
around a transition. There $J_{\text{react}} - J_{\text{pred}} = \int e_r -
\int e_p - \int w$, with $e_p \ge 0$ by the floor assumption and $w \ge 0$, so
the difference is at most $\int e_r$. $\blacksquare$

*Correction.* The 2026-09-11 statement bounded the anticipation term by the cost
over the first $\max(0, \Delta_{\text{shape}} - \ell)$ of the read phase. That
quantity is what a predictor with lead $\ell$ still leaves — part of the third
term of F.1 — not the anticipation term. The conclusion stands: on a gradual
transition the catch-up excess is small, so the anticipation term is small;
transition abruptness is the axis to sweep.

**Acceptance (retained, Programme 2):** F-1 ($\mathcal G > 0$; this is B-4), F-2
(reacting captures a measured share of $\mathcal G$), F-3 (anticipation adds
value on the abrupt schedule), F-4 (whole-run bounds hold under switching), F-5
(no churn), F-6 (forecaster is honest), F-7 (lead-time dependence shown), as
defined in the 2026-09-11 revision. F-4's bounds are re-stated when Programme 2's
constraints are.

---

## Execution order (Programme 1)

Each gate's cost is stated in runs. Node-hours follow once Gate N1 fixes run
length.

**Gate N0 — code, instruments and tests (node time only for builds, tier-2
tests and the preflight).**

1. **(Done 2026-09-25, `1fc8852`.)** Commit the D-10 to D-12 work, the D-12
   verdict and the audit (audit §5, item 1).
2. `level_target_multipliers` with its tests (ACT-1; the implementation plan
   is `docs/IMPLEMENTATION_PLAN_PROGRAMME1.md`).
3. Per-level read counters, keyed by the version's level and aggregated
   across threads, with each level's block reads split into the hit's read and
   false-positive reads (D §4), and $R_{blk}$; the slot-blocking spans (L0 due
   but waiting, and the start level of the job holding the slot) with the
   operations served in them, for the slot-blocking charge (D §4); the active
   memtable's fill at every L0 decision (H §2); $\rho_i$, $o_i$, dropped bytes and
   trivially moved bytes $t_i$ per source level, per compaction; turnover
   logging; per-release slot wait (from the later of a level's score reaching 1
   and its previous job ending, to the job starting) and slot occupancy, for
   $\hat\omega_j$, $\omega_j$ and $\zeta$ (G.4); at every decision, the score
   order, the running compaction's start level and the pending-compaction
   estimate with the multipliers applied (A-Impl-3), read in-process from the
   current version, for the queue-position and backlog inputs (G §3). Repair the
   existing per-level telemetry counters `trivial_move_jobs` and
   `trivial_move_bytes`, which read zero: `NotifyOnCompactionCompleted` passes
   `Compaction::is_trivial_move()`, a flag only the universal picker sets, and
   the trivial-move branch of `BackgroundCompaction` never fills
   `total_input_bytes`. $\hat\xi_j$ (G §3) must not be read from them until
   then.
4. The settle step after the load (`WaitForCompact`, then the $h_w$ hold) and
   the measured-phase stamp at the first `mixgraph` operation (A8, H §5).
5. Evaluator: $\mathcal C_W$, $\mathcal C_R$, $\mathcal C_S$ and $J_\beta$
   over the measured phase, from operation $n_w$ to the end of the drain — a
   ticker snapshot and an internal-stats stall snapshot at $n_w$, $R_{blk}$ from
   `rocksdb.bloom.filter.full.positive`, compactions windowed by completion
   time, and $H$ sampled with the operation count at every version install on
   every arm, native included. Self-check on every arm: the per-interval
   operation counts sum to the measured operations, and the last sampled $H$
   equals `rocksdb.live-sst-files-size` at the end of the drain. And the OBJ-6
   diagnostics.
6. The C++ inference plugin, weight push, masked target and fallback (H §4,
   H §6).
7. Device price calibration: $c_w$, $c_f$, $c_{blk}$, $c_{sk}$, $c_s$ (OBJ-2).
8. The dated `PREREGISTRATION.md` entries of §0.6.
9. Test suites for every item above, and the preflight (CLAUDE.md "Tests";
   plan §6). Every later gate that needs a long node run starts only with a
   preflight marker matching the current `db_bench`, plugin and code hashes.

**Gate N1 — admission test on existing artifacts (no node time).** PROP-1 for
the levels existing runs cover. It fixes the run length: the measured phase must
contain at least $n_{\text{turn}}$ turnovers of the deepest pooled level, with
$n_{\text{turn}}$ fixed in advance (10 suggested). A cell with no pool (T=10 at
the current size, G §4 scope decision) applies the same rule to its deepest
interior level with enough turnovers, L2: about 4 minutes at the old ingest
rate, against about 40 if L3 were pooled. Levels that existing artifacts cannot
decide (fewer than the minimum turnovers, e.g. L3 at T=10) are decided on
Gate N2's static runs, sized provisionally as if pooled: at least
$\lceil n_{\min}/n_{\text{turn}}\rceil$ static runs of $n_{\text{turn}}$
turnovers each.

**Gate N2 — static comparator on the new binary.** ACT-4 (native parity) first.
Then $\Theta_s$ on `Assoc` and one Zipfian workload at T = 2, 6 and 10, in the
same-session design, with repeat counts from C §3. Runs: $|\Theta_s| \times$
workloads $\times$ $T$ values $\times$ repeats.

**Gate N3 — learner-free test of Conjecture B.3′.** Hand-written state rules —
$K_0$ tracking Proposition D.11 at measured prices; releasing on neighbour fill;
holding for garbage; L0 compacting early while the slot is idle and reads are
heavy (Pathway A §4 (e)); interior levels holding releases while L0 is due or
within one flush of due (Pathway A §4 (f)) — against $\theta^\star_\beta$ in each
mode, with the stall rule applied (Global acceptance). This decides whether the
stationary-workload claim stays before any learner run. If neither slot rule
beats $\theta^\star_\beta$ in read priority, read priority's claim narrows to
changing workloads and suite robustness (claims 2 and 3) before Gate N5.

**Gate N4 — learner smoke test.** One workload, read priority, one $T$:
ARCH-1 to ARCH-6, OBJ-1, OBJ-3, OBJ-4.

**Gate N5 — mode matrices.** Read priority across the suite; then write and
space priority (only $\beta$ changes), with the same instruments. The claim is
recorded before N5 (Global acceptance).

**Gate N6 — ablations.** Propagation on and off (PROP-4); counterfactual charge
on and off; neighbour charge from the full value versus the attributed-cost head
(§0.6 item 11); prior on and off; plugin versus socket inference (the plugin's
remote-inference mode, same policy with Q evaluated in the trainer).

Programme 2 (Pathways E and F) starts after Programme 1's claim is recorded.

---

## Global acceptance

**Claims**, one or more of which is recorded in `PREREGISTRATION.md` before
Gate N5:

1. **Per-workload gain.** In mode $\beta$, $J_\beta(\pi) < J_\beta(\theta^\star_\beta)$
   with the paired interval below zero on named workloads (CMP-3), and against
   the cross-$T$ comparator (C-6).
2. **Suite robustness.** Definition C.5 holds in the mode (CMP-7).
3. **Changing workloads.** $\mathcal G > 0$ (B-4) and the controller captures a
   measured share of it (F-2 form).
4. **Propagation contributes.** PROP-2 to PROP-4, claimed only for (workload,
   $T$) cells whose pool holds at least two admitted levels (G §4, scope
   decision): T=2 on `Assoc` at the current size, T=6 if the admission test
   admits two levels, never T=10 at this size.

**Stall rule** (applies to every claim). $J_\beta$ prices no time (D §1), so a
policy could lower it by deferring work until writes stall. A claim therefore
also requires, on the same paired runs and reported beside $J_\beta$ rather than
priced into it (OBJ-6), measured-phase stall seconds no more than
$\delta_{\text{stall}}$ above the comparator's and foreground throughput no more
than $\delta_{\text{thr}}$ below it. Both use the paired interval's bound; both
margins are fixed in advance (§0.6 item 12). Stall seconds are read as in D-11,
from the internal-stats `Cumulative stall` line differenced over the measured
phase, never from `rocksdb.stall.micros`.

**If none holds.** If Gate N3 finds no state-dependent rule that beats
$\theta^\star_\beta$ and Gate N5 confirms it, the paper is an analysis paper: the
structure of the static optimum (Propositions D.11 and D.13, Theorem A.2), depth
monotonicity (Lemma A.6), the corrected elision analysis (Propositions B.1′ and B.1″), the
cost of exploration (Proposition H.3), and the propagation measurements
(Proposition G.1).

---

## References

- [Cao et al.] Z. Cao, S. Dong, S. Vemuri, D. H. C. Du. Characterizing, Modeling,
  and Benchmarking RocksDB Key-Value Workloads at Facebook. USENIX FAST 2020.
- [Cooper et al.] B. F. Cooper, A. Silberstein, E. Tam, R. Ramakrishnan,
  R. Sears. Benchmarking Cloud Serving Systems with YCSB. ACM SoCC 2010.
- [Cosine] S. Chatterjee, M. Jagadeesan, W. Qin, S. Idreos. Cosine: A
  Cloud-Cost Optimized Self-Designing Key-Value Storage Engine. PVLDB 15(1),
  2021.
- [Das & Dennis] I. Das, J. E. Dennis. A Closer Look at Drawbacks of Minimizing
  Weighted Sums of Objectives for Pareto Set Generation in Multicriteria
  Optimization Problems. Structural Optimization 14:63–69, 1997.
- [Dostoevsky] N. Dayan, S. Idreos. Dostoevsky: Better Space-Time Trade-Offs for
  LSM-Tree Based Key-Value Stores via Adaptive Removal of Superfluous Merging.
  ACM SIGMOD 2018.
- [Endure] A. Huynh, H. A. Chaudhari, E. Terzi, M. Athanassoulis. Endure: A
  Robust Tuning Paradigm for LSM Trees Under Workload Uncertainty. PVLDB 15(8),
  2022.
- [Foerster et al.] J. N. Foerster, G. Farquhar, T. Afouras, N. Nardelli,
  S. Whiteson. Counterfactual Multi-Agent Policy Gradients. AAAI 2018.
- [How to Grow] How to Grow an LSM-tree? Towards Bridging the Gap Between Theory
  and Practice. ACM SIGMOD 2025. Source of the M3 convention used in P0-7.
- [Kearns & Singh] M. Kearns, S. Singh. Near-Optimal Reinforcement Learning in
  Polynomial Time. Machine Learning 49:209–232, 2002. Simulation lemma.
- [Miettinen] K. Miettinen. Nonlinear Multiobjective Optimization. Kluwer, 1999.
- [Mnih et al.] V. Mnih et al. Human-Level Control through Deep Reinforcement
  Learning. Nature 518:529–533, 2015.
- [Monkey] N. Dayan, M. Athanassoulis, S. Idreos. Monkey: Optimal Navigable
  Key-Value Store. ACM SIGMOD 2017.
- [Ng et al.] A. Y. Ng, D. Harada, S. Russell. Policy Invariance under Reward
  Transformations: Theory and Application to Reward Shaping. ICML 1999.
- [O'Neil et al.] P. O'Neil, E. Cheng, D. Gawlick, E. O'Neil. The
  Log-Structured Merge-Tree (LSM-Tree). Acta Informatica 33(4):351–385, 1996.
- [RusKey] D. Mo, F. Chen, S. Luo, C. Shan. Learning to Optimize LSM-trees:
  Towards a Reinforcement Learning Based Key-Value Store for Dynamic Workloads.
  ACM SIGMOD 2024; arXiv:2308.07013. Lerp and policy propagation in §5;
  evaluation in §7.
- [Sarkar et al.] S. Sarkar, D. Staratzis, Z. Zhu, M. Athanassoulis.
  Constructing and Analyzing the LSM Compaction Design Space. PVLDB 14(11), 2021.
- [van Hasselt et al.] H. van Hasselt, A. Guez, D. Silver. Deep Reinforcement
  Learning with Double Q-Learning. AAAI 2016.
- [Wolpert & Tumer] D. H. Wolpert, K. Tumer. Optimal Payoff Functions for Members
  of Collectives. Advances in Complex Systems 4(2–3):265–279, 2001.
- [Zhu et al.] Z. Zhu, J. H. Mun, A. Raman, M. Athanassoulis. Reducing Bloom
  Filter CPU Overhead in LSM-Trees on Modern Storage Devices. DaMoN 2021.

RocksDB behaviour (score computation, file picking, `SetOptions`) is cited to
the pinned source, not to documentation.

---

## Appendix R — Record of the 2026-09-11 programme (historical; not re-scored)

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