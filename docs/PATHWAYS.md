# Improvement Pathways: Specification, Proofs, and Acceptance Criteria

**Revision:** 2026-09-29 (research fork). Supersedes the 2026-09-11 revision and
every status block added to it up to 2026-09-23. The narrative of what happened
is `PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md`; dated decisions and verdicts are
`docs/PREREGISTRATION.md`. "The audit" below is the 2026-09-23 audit this
revision acts on, removed from the tree on 2026-10-01; read it with
`git show cb45743:docs/AUDIT_2026-09-23_D12_AND_PATHWAY_A.md`.

**Amended 2026-10-03 (cost model v2, PREREGISTRATION D-23).** The cost model
gains the per-job cost of background jobs and trivial moves, the bytes a
compaction reads, scan iteration, the memtable search, the fixed in-store part
of every operation (a Get's or scan's set-up, a Put's memtable insert), and
the slowdown that background jobs impose on reads and on Puts
(interference). §0.7 says what changed, on which measured facts, and which
owner decisions D-23 left open; the owner took them on 2026-10-04
(PREREGISTRATION D-24). Every changed result is marked "amended
2026-10-03" in its header (with "D-23" where the change is part of cost model
v2), and every new result "new 2026-10-03" or "added 2026-10-03". New results:
Lemmas D.17, D.18 and D.19, Proposition A.8, Proposition G.6, Lemma G.7,
Lemma H.5, Proposition H.6, Proposition B.5, Corollary B.6 and Proposition
C.6. Three check reports recount the measured figures from the run data and
re-derive the proofs: `~/node_ops/reports/2026-10-03-2114-pathways-v2-review.md`
(*the first check report*),
`~/node_ops/reports/2026-10-03-2234-pathways-v2-rereview.md` and
`~/node_ops/reports/2026-10-03-2306-pathways-v2-final-check.md` (together
*the check reports* below).

**Target venue:** not yet chosen for this fork.

**Scope.** *Programme 1* (this revision) is a per-level reinforcement-learning
compaction-trigger controller for leveled RocksDB, a log-structured merge
tree [O'Neil et al.]. It minimises a priced cost of
write, read and space amplification under a chosen priority (reads, writes or
space), acting only through RocksDB's own compaction scores, so that RocksDB
keeps sole authority over which files are compacted. *Programme 2* (deferred)
is SLO-constrained operation: the constrained objective, the guard (Pathway E)
and phase-aware objective switching (Pathway F).

**Status.** Specification. Gate N1's pilot `native` arms and the $\bar q$
arms have run under this revision (PREREGISTRATION D-19 and the $\bar q$
record); no $\Theta_s$ arm has, and no rule or learner arm beyond the
preflight's smoke runs, which are not evidence. Every result
below is either proved from its stated assumptions or explicitly labelled a
conjecture, an approximation or a measured fact with its source. Results of the
2026-09-11 programme are summarised in history §18.1 and are not re-scored.
The 2026-10-03 amendment (D-23) holds to the same rule: where its exactness
depends on a model (A10, linear and additive interference), the result says
that it is exact within the model.

**Reading order.** §0 (what changed) → §1 (notation and assumptions) → Pathway D
(objective and cost model) → Pathway A (actuation) → Pathway G (propagation
across levels) → Pathway H (RL architecture) → Pathway B (workloads) → Pathway C
(comparator) → Pathways E and F (Programme 2) → execution order → global
acceptance → references.

**Referencing convention.** Numbered results use a dot (Lemma D.7,
Theorem A.1). Sections inside a pathway use a section sign (D §3, H §5).
Acceptance criteria use a prefix per pathway: retained criteria keep their
2026-09-11 names (B-1, C-1, C-2, C-6, E-*, F-*); new criteria use new prefixes
(OBJ, ACT, PROP, ARCH, WL, CMP) so that no new criterion reuses the name of one
already scored in `docs/PREREGISTRATION.md`.

**RocksDB version** (corrected 2026-10-03). The fork
(`chill-umb/rocksdb`) sits on upstream RocksDB 11.1.1 (`6cdeb9d9d`,
2026-04-10); its first fork commit is `4398ed337`. The contract records two
fork revisions (`config/research_objective_contract.json`, `revisions`):
`rocksdb_base` = `7ea2d73655025332855864f0b8d6d2dbdad3f336`, a fork commit of
2026-09-01 ("fix bootstrap error", the retired programme's fork), not an
upstream commit; and `rocksdb_fork_start` = `25468bbaa`, where Programme 1's
fork work began. (`6ad9f6b79`, parent `71627a1cd`, was contract v3's last
recorded fork revision, and `81d2742bf`, pinned 2026-09-12, contract v2's.)
The root branch records `8e903efd9`, six fork commits after `25468bbaa`. Those
six commits add the `level_target_multipliers` option, its validation and its
test file (Pathway A), the per-level read counters, the host log with its job
records and $H$ samples, the settle step (H §5), the controller host and
plugin loading, and D-21's reopen ticker and timer; none of these exists at
`25468bbaa`. Every RocksDB citation in this revision is to `8e903efd9`, read
with `git -C lib/rocksdb show 8e903efd9:<path>`, since the submodule's working
tree can lag the recorded commit. The files cited for iterators, compaction
jobs, picking, `SetOptions` and flushes (`db/db_iter.*`,
`table/merging_iterator.cc`, `util/heap.h`, `db/compaction/compaction_job.cc`,
`db/compaction/compaction_picker.cc`, `db/compaction/compaction.cc`,
`db/db_impl/db_impl.cc`, `db/flush_job.cc`) are identical at `25468bbaa` and
`8e903efd9`. Programme 1 adds one
option (Pathway A), so its binary differs from every earlier one and every
static measurement is regenerated on it (Pathway C). Every cited line number and
option semantic must be re-verified against the commit that binary is built
from. The counters and the timer that D-23 adds (OBJ-8, OBJ-9) change the
binary again, with the same consequence.

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
  history §18.1).

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
| 11 | D-4: $\lambda_W$ diverges where the write constraint is infeasible | Could never apply, since native RocksDB always meets its own bound; retired with the constrained objective | history §18.1 |
| 12 | Gate costs | 5–7 times too high (audit §4); new gates are priced by run count once run length is fixed | Execution order |
| 13 | D-12 verdict: the T=2 extra writing is a learned deferral lever | Start-up exploration during the backlog plus session drift | history §18.1 |
| 14 | Proposition F.2 bounds the anticipation term by the read-phase cost over the first $\max(0, \Delta_{\text{shape}} - \ell)$ | That quantity is the loss a predictor still leaves, not the anticipation term; restated with proof | Proposition F.2 |

### §0.6 Dated decisions this revision requires before any run

These belong in `docs/PREREGISTRATION.md`, each dated and committed before the
run it governs. This document states the theory; it does not substitute for the
dated record.

1. The objective change (§0.3), the headline priority factor $\beta^\star$,
   the reference operation rate $\bar q$ per workload, and the two money
   prices the others are converted with: the instance price per device-second
   (one core-second, the instance price over its cores; PREREGISTRATION D-15)
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
13. (Added 2026-10-03.) D-23, cost model v2 (§0.7), before any Gate N2 run:
    the background-job prices $c_{cr}$ and $c^{F}_{job}$, $c^{0}_{job}$,
    $c^{d}_{job}$, $c^{tm}_{job}$, and $c_w$ re-fitted with them (D §1); the
    scan prices $c_{st}(r)$ and $c_{ib}$ and the memtable price $c_{mt}$; the
    fixed per-operation prices $c^0_{get}$ and $c^0_{sc}$ and the Put's
    memtable-insert price $c_{put}$ (D §1); the interference coefficients $\kappa$, those of the Put
    insert and the fixed parts included, their basis and the bytes
    $Y_\iota$ they use; the rules for the averaging window $n^{\mathrm{win}}$
    of the interference charge and for the stride of the counter snapshots
    that compute it (D §1), their values fixed by D-23 or a later dated
    entry; the design of the per-run checks OBJ-7 and OBJ-8, whose
    tolerances and failure consequences are set as §0.7 item 4 records
    (D-24); and the binary with
    the new counters, records and timer (OBJ-8, OBJ-9). The owner decisions
    §0.7 lists were taken on 2026-10-04 in D-24, including the amendment of
    D-21's $c_{open}$ check, never in this document.

### §0.7 The 2026-10-03 amendment: cost model v2 (D-23)

**What changed.** On 2026-10-03 the owner approved pricing every device-time
cost of serving the operations inside the store ("We need to model every cost
of operation in the tree"). $J_\beta$ keeps its form,
$\beta_W\mathcal C_W + \beta_R\mathcal C_R + \beta_S\mathcal C_S$, and two of
its three costs gain terms (D §1, D §2):

- **Writes.** Each background job $\iota$ is priced, not only its bytes:
  $\tau^{job}_\iota = c^{\mathrm{kind}(\iota)}_{job} + c_{cr}(S_\iota + O_\iota) + c_wX_\iota$,
  a price per job of its kind (flush $F$, merge sourced at L0 $0$, merge
  sourced deeper $d$, trivial move $tm$), a price per compaction byte read,
  and a price per byte written. Trivial moves, priced at zero until now, cost
  their per-job price. Each Put pays its memtable insert, $c_{put}$; the
  write-ahead log is disabled in every Programme 1 run, so it costs nothing
  (D §1).
- **Reads.** Scans pay for iteration, not only for their seeks: entries
  returned, hidden versions stepped over, each step's cost in the merging
  iterator's heap, and the blocks the iterator loads. Every Get and scan pays
  a fixed set-up part ($c^0_{get}$, $c^0_{sc}$) and the memtable
  search.
- **Interference.** The foreground steps served while background jobs run
  take longer (A10). Each job is charged that extra device time at the
  reference rate: its *read part*, on read steps, in $\mathcal C_R$, and its
  *write part*, on the Puts' memtable inserts, in $\mathcal C_W$.
- **Space** is unchanged.
- **Principles** (the D-23 design):
  - *P-1, completeness.* P-1 covers every device-time cost incurred *inside the
    store* (the RocksDB library and its background threads) on behalf of the
    operation sequence: background jobs (bytes, per-job cost, trivial moves);
    each operation's foreground work (a Get's or scan's fixed set-up, its
    memtable search and its read steps; a Put's memtable insert, and the
    write-ahead log, which every Programme 1 run disables, so its price is
    zero); and the slowdown background jobs impose on that foreground work
    (interference, on read steps and on Put inserts alike). A cost that no
    policy changes is priced too, in a shared bucket, so that the level of
    $J_\beta$ is right. Outside P-1's scope, and unpriced: the benchmark
    client's own work (key and value generation, `mixgraph`'s draws, its
    statistics), which is outside the store; the controller's own work (its
    `SetOptions` calls and their OPTIONS files, ACT-2) and the trainer, which
    serve the controller, not the operations; and waiting, which is not
    device work, so stall time, throughput and latency stay unpriced
    (Programme 2; OBJ-6 reports them). P-1 is a design principle: how
    closely the prices reproduce the device time is A9's and A11's question,
    checked per run where an instrument exists (P-4).
  - *P-2, the reference-rate principle.* $J_\beta$ is a function of the run's
    operation-indexed record; wall time enters only converted at the
    reference rate $\bar q$, as space already did (Lemma D.17). A job's
    interference is what it would cost if the store served $\bar q$
    operations per second through the job's priced device time, at the
    foreground cost per operation measured around the job; neither its wall
    span nor the number of operations served during it enters the charge,
    so a job run during a stall or the drain pays like any other (D §1).
    The conversion uses two anchors, both free of wall time.
    Space and the byte part of interference anchor on the operations served:
    an interval that serves $\Delta N$ operations counts as $\Delta N/\bar q$
    reference seconds, and applied to a job's own operations this gives
    exactly the byte part of its charge. The busy part anchors on the job's
    *priced device time* instead, $\bar q\,t^{job}_\iota$ operations: on
    purpose, since anchored on its operations it would vanish for a job run
    in a stall (Lemma D.17(iv)).
  - *P-3, victim-side pricing, cause-side attribution.* Interference is
    device time of the slowed foreground steps, so it is priced with them:
    its read part in $\mathcal C_R$ (weight $\beta_R$), its write part, on
    Put inserts, in $\mathcal C_W$ (weight $\beta_W$). In the
    per-level rewards both parts go to the start level of the job that
    caused them; a flush's go to a shared write-path bucket (D §4). Foreground
    work (reads and Puts) slowing jobs is already inside the job prices
    (A11) and is not charged again.
  - *P-4, measured counts, fixed prices.* $J_\beta$ multiplies counts
    measured in the run by prices fixed in advance (A9), and every price that
    a run can check with an instrument of the same source and phase is
    checked in every run, as $c_{open}$ already is (D-21).
  - *P-5, numbering.* Every existing number keeps its meaning. New results in
    Pathway D are D.17 to D.19; the numbers D.20 to D.23 are not used, so that
    no result is confused with the decisions D-20 to D-23.
- **Results.**
  - *New:* Lemma D.17 (speed neutrality), Lemma D.18 (job and read-byte
    identities), Lemma D.19 (scan terms); Proposition A.8 (an early L0 merge,
    priced); Proposition G.6 (the interference term is a level input) and
    Lemma G.7 (the admission test does not depend on the prices); Lemma H.5
    (L0's share of the heap increment) and Proposition H.6 (charges at job
    completion); Proposition B.5 (the read price of held garbage) and
    Corollary B.6 (the garbage trade-off); Proposition C.6 (what a static
    point's priced cost depends on).
  - *Amended* (statement or proof changed 2026-10-03; each header carries
    "amended 2026-10-03"): Lemma D.1 (the job and read terms, the Put insert,
    the fixed parts and the read and write parts of interference) and Lemma
    D.10; the Definition of priced cost with priority (D §2); Proposition D.3
    (the metric form made conditional); Remark D.6 (the utopian reference
    point, and the augmented form for weak Pareto optimality); Proposition
    D.11 ((i)'s new terms, (b3) holding only on average, the floor's
    condition in (ii), the step-price cases of (iii), the window rule (d2)
    and the guard that decides when it fails, the write part); Corollary D.12; Proposition D.13 ((i): the write part in
    $\psi_i$; (iv): the $c_{cr}$ condition, L0's per-job busy term, (I4), the
    write part); Proposition D.16 (the fixed bucket and the write part);
    Theorem A.2 (part (iv) added); Proposition G.2 (the new level inputs); G
    §3's Definition (law) (the sign of $b$); Proposition G.4 (conditions
    re-read, error terms that grow, and the simulation bound it uses now
    proved in the text); Propositions H.1 and H.4 (proofs written out);
    Propositions H.2 and H.3 (restated); Proposition B.1″ (part (ii) added,
    with the gross charge); Corollary C.3 and Proposition C.4 (the split of
    interference by part).
  - *Re-checked, unchanged:* Propositions D.2, D.4, D.5, Lemmas D.7, D.8,
    D.14 and D.15, Corollary D.9, Theorem A.1, Lemmas A.5 and A.6, Corollary
    A.7, Propositions G.1 and G.3, Lemma G.5, Proposition B.1′, Corollary
    B.4, Conjecture B.3′ (still a conjecture), the Definition of the
    phase-adaptivity gap (B §3), Proposition C.1, Corollary C.2, Definition
    C.5 (its note gains the fixed bucket), the Definition of three
    controllers over one schedule (Pathway F), and Propositions F.1 and F.2.
    The superseded Corollaries A.3 and A.4 are not re-checked.
  - *Assumptions:* A9–A11 new (A10 extended the same day to every foreground
    step); A3′ (which L0 files the score counts), A5, A7 and A8 amended.
  - *Criteria:* new OBJ-7, OBJ-8, OBJ-9, PROP-6, ARCH-7, ARCH-8, WL-3 and
    CMP-9; amended OBJ-1, OBJ-2, OBJ-4, OBJ-6, ACT-4, ARCH-3 and ARCH-5.
    OBJ-1, OBJ-2, OBJ-6, OBJ-8, OBJ-9 and ARCH-7 also cover the Put insert
    and the fixed parts, and OBJ-1, OBJ-8 and ARCH-7 the write part of
    interference; OBJ-8's timer is stratified, and OBJ-8 reports (b3)'s
    exposure and the window guard.
- **Integration decisions** (2026-10-03, recorded with D-23). Three design
  points are fixed for the whole document:
  - *Speed-neutral interference* (D §1, Lemma D.17). Both the byte and the
    busy part of a job's charge are converted at $\bar q$: the busy part counts the job's priced
    device time, not the operations served during it, and the read cost per
    operation is averaged over a window of at least $n^{\mathrm{win}}$
    operations, with $n^{\mathrm{win}}$ set below the merges' spans so that
    every merge that serves at least $n^{\mathrm{win}}$ operations is charged
    over its own operations (a post-integration decision; a merge in a stall
    or the drain, or one shorter than $n^{\mathrm{win}}$, reaches back before
    it began, D §1). As
    first designed the busy part fell when the foreground
    slowed relative to the jobs and vanished in a stall, which a learner
    could exploit.
  - *Heap split, L0 last* (D §4, Lemma H.5). Each step's heap increment is
    split so that L0's files pay the marginal increment they add; the other
    children share the rest equally.
  - *Hidden steps to the level above* (D §4). A hidden step is charged to the
    level directly above the level where the hidden entry lives, the only
    level whose merges can remove it.

**Why: the measured facts.** Node diagnostics of 2026-10-03, quoted as
measured, each with its source; none is a theorem. Figures derived from a
model are marked *derived*. The check reports (header) recount the figures
from the run data named with each.

- **Interference** (`~/node_ops/reports/2026-10-03-1514-contention-reads.md`
  and `-1632-contention-reads.md`; one repeat, uniform reader keys, every
  table open). With a rate-limited writer (about 11 MB/s of user writes and
  100 MB/s of compaction traffic) in a *neighbouring* database on the same
  machine, read steps on levels 1 and below took, with the writer's
  compactions on / off, 1.128 / 1.005 times their quiet time per filter probe
  and 1.118 / 1.031 per block read; a seek at its own counts took 1.102 /
  1.024 (1.158 with the writer in the reader's own database). A compaction in
  another database slows reads as much as one in the reader's own: the path
  is shared hardware (memory bandwidth, caches, device), not anything inside
  the database. All three trees (T = 2, 6, 10) agree. The size is about
  0.12% ($c_f$), 0.09% ($c_{blk}$) and 0.08% ($c_{sk}$) per MB/s of
  compaction traffic; whether it goes per byte, per job or per busy second is
  not identified. The write path alone (compactions off) adds 0.5–3 points.
  The memtable search adds 0.22 µs per read whenever writes keep the
  memtable populated, the same in every configuration.
- **Interference is correlated with the tree's state** (critique,
  `~/node_ops/reports/2026-10-03-1816-interference-theory-critique.md` Q2;
  per job, the check reports; $k_0$ from the event log's `lsm_state`,
  measured phase; event and host logs under `db/qbar-assoc-d21id`,
  `db/n1-assoc` and `db/n1-powerlaw`). *The seven native runs* are the
  d21id $\bar q$ arm (`Assoc` T = 10) and Gate N1's pilots, `Assoc` and
  power law at T = 2, 6 and 10, each repeat 1. (The critique's text says
  seven runs were checked, but its table lists six, without power law
  T = 6; the same count on that run gives the same pattern. The check
  reports extend the count to every repeat of those cells: the $\bar q$
  arm's five repeats and the pilots' three, 23 native runs.) The pattern:
  - every L0→L1 merge ran with $k_0 = K_0 = 4$ throughout, in all 23 runs;
  - the merges sourced at a level $\ge 1$ ran with L0 empty *on average*:
    the mean $k_0$ during the merges of each start level was at most 0.13
    (time-weighted; 0.134 byte-weighted) in the seven runs, and at most 0.13
    (0.14) in all 23; pooled over all such merges of a run, at most 0.05;
  - per job it is not exact: at `Assoc` T = 6 and 10, 0.9% and 5.4% of
    those merges (repeat 1; 0.4–5.8% over all repeats; 3.8% on the $\bar q$
    arm) ran while L0 held one file, never more; at T = 2 at most three per
    run did, and on the power law none. Trivial moves (timed from the host
    log's job records) did so more often at `Assoc` T = 10 (14–30% of them, mean
    $k_0$ at their start 0.12–0.27), never with more than one file;
  - the deeper jobs that follow an L0 merge (the cascade) always ended
    before L0 next fell due, but at `Assoc` T = 10 the last of them ran past
    the next flush in nearly a third or more of L0 cycles (31–46% on the $\bar q$
    arm, 36–41% on the pilots; 4.5–8.4% at T = 6; at most 0.2% at T = 2),
    which is why some of them saw one L0 file;
  - so no job sourced at a level $\ge 1$ ran while L0 held two or more
    files, and none held the compaction slot while L0 was due: slot
    blocking (D §4) had no occasion.

  *Derived* from this pattern (critique Q2, model M2 with provisional prices
  and $\kappa$; not measurements): mean field gets the total within 2%, but
  its slope in $K_0$ is 2.8 times too steep, and its per-level weights are
  wrong: L0 bytes should pay 1.24–1.94 times what deeper bytes pay.
- **Per-job time** (operator diagnostics of 2026-10-03 on Gate N1's pilots,
  repeat 1, and the d21id $\bar q$ arm, reproduced in the check reports;
  the host-log job span minus the event log's
  `compaction_time_micros`, non-trivial jobs, `measure_start` to
  `drain_end`). The median gap is 2.7–3.5 ms per job, 71–128% of the counted
  compaction time (`Assoc` T = 2: 45.7 s against 35.8 s): 4.5–5.3 ms for jobs
  sourced at L0, 2.7–3.5 ms for deeper jobs, and nearly independent of job
  size (2.85 ms in the smallest decile, 3.18 ms in the largest, `Assoc`
  T = 10). `compaction_time_micros` times only `RunSubcompactions`, the part
  of `CompactionJob::Run` that reads and writes the data (`UpdateTimingStats`
  right after it, `db/compaction/compaction_job.cc`). The gap therefore holds the rest of `Run` — an fsync of the output
  directory (`SyncOutputDirectories`) and the opening of every new SST
  through the table cache (`VerifyOutputFiles`: footer, index and filter,
  and checksums where enabled), then the output properties and statistics —
  and, after `Run`, the DB-mutex waits, the install (a MANIFEST write and
  sync), the SuperVersion install and the listener work of the job's begin
  and end records; the parts are not yet split. The output-file opens grow
  with the number of output files, so the gap is not strictly independent of
  a job's size. A flush's span already includes its install. A trivial move
  writes no bytes, so the per-byte $c_w$ priced it at zero, yet the per-run median move took
  2.5–3.3 ms: 4,151 moves (13.8 s) on `Assoc` and 2,153 (6.8 s) on the power
  law at T = 2, and 0.4–0.6 s per run at T = 6 and at T = 10.
- **Scans** (Gate N1's pilots, `Assoc`, repeat 1, `db/n1-assoc`, tickers
  differenced between the host log's `measure_start` and `drain_end`
  stamps; reproduced in the check reports; the power law issues no
  scans). Stage 18 prices a
  scan by its run seeks, measured with `--seek_nexts=0`. Each `Assoc` scan
  returns about 543 entries (543.5 on repeat 1, 542–546 over the three
  repeats): about 496M `Next`s per 26.1M operations, 509 GB returned in
  every arm. Hidden internal
  entries stepped over (`rocksdb.number.iter.skip`): 570M at T = 2 and 114M at
  T = 10, 1.15 and 0.23 per returned entry. Garbage spread uniformly over the
  keys would give about $S - 1$ hidden versions per entry, and the same runs'
  $S$ at run end gives $S - 1$ = 0.105–0.107 at T = 2, 0.048 at T = 6 and
  0.043 at T = 10. So scans step over about 11 times (T = 2), 5 times (T = 6)
  and 5.4 times (T = 10) the uniform figure (10.8–11.0, 5.1–5.5 and 5.4–5.5
  over the three repeats; `db/n1-assoc/graphs/summary.csv`,
  `scan_internal_skips` and `scan_returned_entries` against
  `space_amplification`): hidden versions concentrate on hot keys. Garbage
  during a run can exceed garbage at its end, so these are upper estimates
  of the concentration. Data-block cache
  misses, Gets and scans together, over the measured phase: 296.8M against
  178.8M (tickers differenced between the stamps: since the database opened
  they would include the load, D-11's trap); run seeks 7.9M
  against 3.75M. The client spent at least 409 s (T = 2) against at least
  208 s (T = 10) outside its Get, Seek and write calls, in every repeat
  (`run.log`'s internal histograms): about 200 s that depends on the
  configuration and was unpriced, more than T = 2's whole priced
  $\mathcal C_R$ (about 88 s at the d21id prices, reopens left out). It
  holds the scans' `Next` calls and iterator set-up, which no internal
  histogram times (`rocksdb.db.seek.micros` times the `Seek` call only), as
  well as the client's own work; how it splits is not measured.
- **Reopens** (`~/node_ops/reports/2026-10-03-1206-assoc-ablation.md`).
  `Assoc`'s reads took 1.256 times stage 18's time per reopen, outside D-21's
  10%. Removing the writes gave 1.154 and removing the scans too 1.071, so,
  multiplicatively, writes account for about 0.09, the scans' pollution of
  the caches about 0.08, and the rest about 0.07. Interference can explain
  only the writes' part (D §1).

**Why it matters for the theory.** Every omitted cost but the memtable
search, the entries a scan returns, the fixed parts of Gets and scans and
the Puts' memtable inserts depends on the policy: per-job time and
interference (on reads and on Puts alike) grow with the number and volume of
jobs, iteration with the hidden versions the tree holds and the runs a scan
merges. The policy-independent costs are priced so that the level of
$J_\beta$ is right (P-1).
Leaving them out biased the static optima ($K_0^\star$, the fanout, Pathway A's
lever (e)) in known directions, most of all in read priority (critique Q2,
Q7). Adding them keeps $J_\beta$ linear in
$(\mathcal C_W, \mathcal C_R, \mathcal C_S)$, so Propositions D.2, D.4 and D.5
and the hull results of Pathway C hold as they are (D §2). What changes is
what the costs contain, and with it the static optima of D §3.

**Statements of the 2026-09-29 revision that D-23 corrects.**

| # | 2026-09-29 statement | Correction | Where |
| --- | --- | --- | --- |
| 1 | $c_w$ prices every job by its bytes written (D-15 §3(b)): a compaction's install is left out, and a trivial move costs nothing | Each job $\iota$ is priced $\tau^{job}_\iota$: a per-job price by kind, compaction bytes read, bytes written; a trivial move costs its per-job price | D §1, Lemma D.18 |
| 2 | A scan costs its run seeks (D §0 issue 2, Lemma D.10) | It also costs its iteration: entries returned, hidden versions, heap steps, iterator blocks | D §1, Lemma D.19 |
| 3 | "Stalls, slowdowns … are not priced" (D §1) | The slowdown of priced foreground steps by background jobs is priced (interference: on read steps and on Put inserts), converted at $\bar q$ so that $J_\beta$ still contains no time | D §1, Lemma D.17 |
| 3a | $J_\beta$ priced no Put and no fixed per-operation part | Each Put's memtable insert ($c_{put}$) and each Get's and scan's fixed set-up ($c^0_{get}$, $c^0_{sc}$) are priced, in the fixed bucket; the WAL, disabled, costs 0 | D §1, D §4 |
| 4 | Proposition D.3 in metric units, unconditionally | Holds only where each of the two costs is one price times one metric along the curve | D §2 |
| 5 | Proposition D.11: $K_0^\star \propto \sqrt{\beta_W/\beta_R}$ | False once compaction also costs read time; re-derived | D §3 |
| 6 | Pathway A §4 (e): early L0 compaction is a read lever "while reads are heavy" | Its extra overlap bytes now also cost read time; in read priority the lever can reverse | Pathway A §4 |
| 7 | Proposition G.2: one set of price ratios "the same at every level" | True of the new interference term only in mean field | Pathway G §2 |

**Owner decisions** (pending under D-23; decided 2026-10-04, PREREGISTRATION
D-24). This document states the model; the decisions are D-24's, and each
item below records the decision in one line, after the question it settles.

1. The basis of $\kappa$ (per byte, per busy time or per job) and the weight
   $\lambda$ of the bytes read in $Y_\iota = X_\iota + \lambda(S_\iota +
   O_\iota)$. *Decided 2026-10-04 (D-24):* set by the calibration; the owner
   decides only if the calibration cannot identify them.
2. How the quiet read prices $c^0_x$ are measured: on quiet uniform-key trees,
   as stage 18 does, or per workload (the operator's open decision D2).
   The rest of a quiet `Assoc` Get (its in-store time less its reopens) took
   0.74 of the uniform-key prediction, and the whole Get, its reopens at the
   reference, 0.85–0.87
   (`~/node_ops/reports/2026-10-03-1436-get-time-vs-prices.md`).
   *Decided 2026-10-04 (D-24):* per workload, on trees with that workload's
   key distribution.
3. `Assoc`'s $c_{open}$ (the operator's open decision D3): removing the
   writes takes its 1.256 to 1.154 (a factor of about 1.09; 0.10 of the
   0.256 excess), which bounds what interference can explain (the write
   path's own effect is also in it). *Decided 2026-10-04 (D-24):* measured
   on `Assoc`; D-21's check is amended to compare the run's time per reopen
   with $c^0_{open}(1 + \bar s_{open})$, keeping its 10%.
4. The tolerances of the new per-run checks, and what a failure means
   (D-21's $c_{open}$ check under interference, OBJ-7, OBJ-8), and of the
   calibration's tests of A10's linearity and additivity (OBJ-2,
   OBJ-8(c)); the tolerances on OBJ-8(d)'s two shares above which D.11's
   (d2) and (b2) count as failing at a trigger (Proposition D.11, the
   guard); and the margin on the new binary's native throughput, paired
   against the pilots', on which Gate N1's reuse rests (G §4). *Decided
   2026-10-04 (D-24):* each is set from the calibration's spread, at 2–3
   times the session-to-session difference, by a dated entry before Gate
   N2; during the exploratory track every such check is report-only.
5. Whether the per-job prices need a dependence on the configuration beyond
   the job kinds: the median per-job gap was 3.4–3.5 ms at T = 2 against
   2.7–3.0 ms at T = 6 and 10 (the $\bar q$ arm, at T = 10, 2.97 ms).
   *Decided 2026-10-04 (D-24):* fixed per kind; a state term is added only if
   OBJ-7 fails at T = 2; rolling-average prices are rejected.
6. Whether $\bar q$ is re-measured on the binary that carries D-23's
   instruments (D-13 and D-14 tie $\bar q$ to the Programme 1 binary).
   *Decided 2026-10-04 (D-24):* $\bar q$ stays fixed at its d21id value and is
   not re-measured; a rolling $\bar q$ is rejected.
7. Whether $\Theta_s$ gains a priced profile per mode (Theorem A.2(iv),
   C §1). *Decided 2026-10-04 (D-24):* the priced profile joins $\Theta_s$
   for the balanced mode only, at the formal Gate N2.
8. Whether the admission test's support screen compares the new level
   inputs of Proposition G.2, for any future pool (G §4). *Decided
   2026-10-04 (D-24):* deferred until a pool exists.
9. H §7's sign convention. The prior $b$ is a change in normalised *cost*,
   as `controller/prior.h` computes it, and $Q_j$ an expected normalised
   *reward*. *Decided 2026-10-04 (D-24):* the code's convention: $b$ is a
   cost, and $Q_j = -b + f_\theta + \delta_j$ (G §3, H §4).
10. Whether D-22's 3% session-to-session test applies to $\kappa$ and to each
    new foreground price ($c_{st}(r)$, $c_{ib}$, $c_{mt}$, $c^0_{get}$,
    $c^0_{sc}$, $c_{put}$): $\kappa$ is a slope of about 0.1% per MB/s,
    measured once (Execution order, Gate N0 item 11). *Decided 2026-10-04
    (D-24):* 3% for the new base prices; for $\kappa$, the two sessions'
    values agree within their combined confidence interval and the predicted
    slowdown at the reference traffic within 1 percentage point.
11. How the fit separates $c_{cr}$ from $c_w$: a merge's bytes read and
    written move together across jobs (D §1), so the fit may determine their
    sum better than either. *Decided 2026-10-04 (D-24):* a garbage-heavy
    neighbour in the job-size sweep identifies $c_{cr}$; failing that, the
    two are fitted combined, with the restriction stated.
12. Whether CMP-7 and Definition C.5's regret are computed on $J_\beta$ or on
    $J_\beta$ less the policy-independent buckets (the scan-base bucket, the
    fixed bucket and the memtable bucket's searches; the rest of the
    memtable bucket depends on the policy weakly, H §3). Those terms add the same
    constant to every arm of a workload, so they leave every difference
    unchanged but shrink every regret by a factor that differs between
    workloads (C.5). *Decided 2026-10-04 (D-24):* on $J_\beta$ less the
    policy-independent buckets (memtable, scan-base, fixed); the full
    $J_\beta$ is reported beside it.
13. The workload's label. Cao et al. publish numeric `mixgraph` parameters
    only for ZippyDB's fit (`Prefix_dist`, their App. A.3 and §7.3), and the
    project uses those key-range, key, value-size and scan-length parameters
    with `Assoc`'s operation mix (§7.4 gives no numeric fit for `Assoc`), so
    D-1's label ("Assoc key distribution and operation mix at the project's
    record size") misattributed the distributions (Pathway B §1). *Decided
    2026-10-04 (D-24):* the label is corrected to "the `Assoc` operation mix
    on Cao et al.'s ZippyDB parameter fit".

The window length $n^{\mathrm{win}}$ (by the rule of D §1: below the merges'
spans), the counter-snapshot stride $n^{\mathrm{str}}$, the strata of OBJ-8's
timer and whether it also times the Put inserts and the fixed parts, and
the calibration procedures (of the Put-insert and fixed per-operation prices
and their $\kappa$ included) are governed by D-23 (§0.6 item 13): D-23 states
their rules, and their values are fixed by D-23 or a later dated entry before
any run they govern. D-23
records the design of the per-run checks (D-21's $c_{open}$ check under
interference, OBJ-7, OBJ-8): what each compares, over which units and
strata. Their tolerances, and what a failure means, are set as item 4
decides (D-24): from the calibration's spread by a dated entry before Gate
N2, every check report-only during the exploratory track. D-24 also amends
D-21's reopen check (item 3): the run's time per reopen is compared with
$c^0_{open}(1 + \bar s_{open})$ (D §1), keeping D-21's 10%.

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
| $S, O, X$ | per compaction: source bytes, overlap bytes read from the level below, output bytes; $S_\iota$, $O_\iota$, $X_\iota$ for job $\iota$ (a flush has $S_\iota = O_\iota = 0$ and $X_\iota$ its file bytes; in $\tau^{job}_\iota$ a trivial move has $S_\iota = O_\iota = X_\iota = 0$). Not the space metric $S$, the reopen count $O$ or the neighbour charge $X_{i\pm1}$ (see the note after the table) | event log |
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
| $O$, $\dot o$; $O(a, b]$ | the reads' table reopens over the measured phase, and per second (D §1, D-20, D-21); the reopens of operations $a+1..b$ | fork counters, evaluator |
| $S$ (metric) | settled SST bytes / garbage-free bytes (D-3) | evaluator |
| $H(t)$ | SST bytes of the current version (`rocksdb.live-sst-files-size`): a running compaction's inputs count until it installs and its outputs from then on; obsolete files still pinned by older versions or awaiting deletion do not count | telemetry |
| $u$, $q_{pt}$, $q_{sc}$, $q_{put}$ | user bytes written, Gets, scans and Puts, per second ($q_{put}$ added 2026-10-03) | telemetry |
| $q$, $\bar q$ | all operations per second; a reference operation rate, fixed in advance per workload and recorded with the prices (OBJ-2), since it sets the weight of space against the other terms | telemetry; preregistration |
| $N_i = C_i\,q/\bar\lambda_i$ | operations served per turnover of level $i$ ($q$ and $\bar\lambda_i$ over the same window) | derived |
| $c_w, c_f, c_{blk}, c_{sk}, c_{open}, c_s$ | prices: per byte written, per filter probe, per block-reading probe, per run seek, per table reopen, per byte held per second. Amended 2026-10-03 (D-23): $c_w$ is the price per byte written inside a job's price $\tau^{job}_\iota$, no longer a job's whole time per byte; the read prices are quiet prices, also written $c^0_x$ | measured once per machine (A9) |
| $c_{put}$, $c^0_{get}$, $c^0_{sc}$, $c_{wal}$ | quiet prices (2026-10-03): of a Put's memtable insert (the mean over the workload's Puts if a dated entry gives it a part per byte); $c^0_{get}$, the fixed in-store part of a Get: the device time of the store's Get call that does not depend on the tree (snapshot, SuperVersion reference, lookup-key set-up and return), with the memtable search ($c_{mt}$), every table step ($c_f$, $c_{blk}$, $c_{open}$) and the client's own work outside the call excluded; $c^0_{sc}$, the fixed in-store part of a scan: the device time of creating its iterator and of the fixed part of its first `Seek` (snapshot, SuperVersion reference, merging-iterator set-up), with the run seeks ($c_{sk}$), the memtable search ($c_{mt}$), the iteration steps and iterator blocks ($c_{st}$, $c_{ib}$), any reopen ($c_{open}$) and the client's own work excluded, measured by the in-store scan set-up timer (Gate N0 item 10); per write-ahead-log byte, which is 0 in Programme 1 since every run disables the WAL (`wal1`) | calibration (D-23) |
| $p_{\mathrm{dev}}$ | the instance price per device-second (one core-second), which converts device times into money: a price $c$ is $p_{\mathrm{dev}}$ times a device time (added 2026-10-03, D-23) | §0.6 item 1, D-15 |
| $U$ | user bytes written in a window (the measured phase unless stated) | evaluator |
| $\beta = (\beta_W, \beta_R, \beta_S)$ | priority weights; $\beta^\star > 1$ is the factor on the prioritised term | run configuration |
| $J_\beta(\pi)$ | priced, priority-weighted cost of policy $\pi$ over the measured phase | Pathway D |
| $\mathcal C_W, \mathcal C_R, \mathcal C_S$; $\mathcal C^{job}_W$ | unweighted priced run costs of writes (bytes written, compaction bytes read, jobs, together the job part $\mathcal C^{job}_W = \sum_\iota\tau^{job}_\iota$; since D-23 also the Puts' memtable inserts and the write part of interference), reads (filter probes, block reads, run seeks and reopens; since D-23 also scan iteration, the memtable search, the fixed parts of Gets and scans and the read part of interference) and space; $J_\beta = \beta_W\mathcal C_W + \beta_R\mathcal C_R + \beta_S\mathcal C_S$ | Pathway D |
| $\Theta_s$ | the static configuration class | Pathway C |
| $\theta^\star_\beta$ | the static configuration minimising $J_\beta$ on a workload | Pathway C |
| $S_{\text{flow}} = N_{\text{written}}/N_{\text{live}}$, $g_{\text{flow}} = 1 - 1/S_{\text{flow}}$ | flow space ratio and cumulative garbage fraction | as 2026-09-11, measured denominator (D-3) |
*Added 2026-10-03 (D-23): background jobs.*

| Symbol | Meaning | Source |
| --- | --- | --- |
| $\iota$ | index of a background job, in every pathway; levels are indexed by $i$ and $j$ | host log |
| $\mathrm{kind}(\iota) \in \{F, 0, d, tm\}$ | a job's kind: flush ($F$); merge sourced at L0, L0→L1 and intra-L0 ($0$); merge sourced at a level $\ge 1$, last-level self-compactions included ($d$); trivial move ($tm$). A generic kind is written "kind" in sub- and superscripts; the letter $k$ is never a kind | host log, event log |
| $c^{F}_{job}$, $c^{0}_{job}$, $c^{d}_{job}$, $c^{tm}_{job}$; $c^{\mathrm{kind}}_{job}$; $c^{\langle i\rangle}_{job}$ | per-job prices of the four kinds (a job's time beyond reading and writing its data: the output directory's fsync and the opening of each new output file, the install, mutex waits, listener work; D §1); the price of a generic kind; the per-job price of a merge sourced at level $i$, $c^{0}_{job}$ at $i = 0$ and $c^{d}_{job}$ at $i \ge 1$ | calibration (D-23) |
| $c_{cr}$ | price per compaction byte read | calibration (D-23) |
| $\tau^{job}_\iota$, $t^{job}_\iota$ | job $\iota$'s priced cost, $\tau^{job}_\iota = c^{\mathrm{kind}(\iota)}_{job} + c_{cr}(S_\iota + O_\iota) + c_wX_\iota$ (money); the same as a device time, $t^{job}_\iota = \tau^{job}_\iota/p_{\mathrm{dev}}$ (seconds). Neither is the job's wall span | derived (D §1) |
| $\Xi^{\mathrm{kind}}$, $\dot\Xi^{\mathrm{kind}}$; $\dot w$, $\dot w_r$ | jobs of a kind completing in a window, and per second; bytes written and compaction bytes read per second | host log, event log |
| $W_r$, $\varpi^{\mathrm{kind}}$ | compaction bytes read per user byte, $\sum_\iota(S_\iota + O_\iota)/U$ over merges; jobs of a kind per user byte, $\Xi^{\mathrm{kind}}/U$ | evaluator (Lemma D.18) |
| $\bar s_i$, $\bar s^{tm}_i$, $\bar s_F$ | mean source bytes per merge sourced at level $i$, per trivial move from level $i$, and output bytes per flush | event log (Lemma D.18) |
| $n^b_\iota$, $n^e_\iota$, $\Delta n_\iota = n^e_\iota - n^b_\iota$ | job $\iota$'s stamps: measured-phase operations completed at its begin and at its end record; the operations served during it (operation $n$ is served during $\iota$ iff $n^b_\iota < n \le n^e_\iota$) | host log |
| $\mathcal J(n)$ | the jobs operation $n$ is served during | host log |
| $Y_\iota$, $\Delta t_\iota$, $v_\iota = Y_\iota/\Delta t_\iota$, $q_\iota = \Delta n_\iota/\Delta t_\iota$ | job $\iota$'s bytes on $\kappa$'s byte basis, $Y_\iota = X_\iota + \lambda(S_\iota + O_\iota)$: its bytes written plus $\lambda$ times the compaction bytes it reads (a flush has $Y_\iota = X_\iota$, a trivial move $Y_\iota = 0$), with $\lambda \ge 0$ one value for every step type, fixed by the calibration ($\lambda = 0$: bytes written only; bytes read alone is the limit $\lambda \to \infty$ with $\kappa^B\lambda$ held fixed); its wall span; its physical byte rate; the operations per second served during it | host log, event log |

*Added 2026-10-03 (D-23): reads, scans and interference.*

| Symbol | Meaning | Source |
| --- | --- | --- |
| $R_{nx}$ | entries a scan returns after the one its seek positions on | `rocksdb.number.db.next.found` |
| $R_{hd}$ | hidden internal entries a scan steps over (older versions; tombstones if any), counted per level where the hidden entry lives and charged to the level directly above it (D §4) | `rocksdb.number.iter.skip`; per level a fork counter (OBJ-9) |
| $R_{ib}$ | data blocks a scan's iterators load after their seeks, counted logically (cache hits included), per level | fork counter (OBJ-9) |
| $r$; $r^{\mathrm{L0}}$, $r^{\neg0}$; $r^-$ | children in the merging iterator's heap at an iteration step; those that are L0 files; the others (memtables and level iterators), $r = r^{\mathrm{L0}} + r^{\neg0}$; $r^- = \max(r^{\neg0}, 1)$, which sets the non-L0 part of a step's price in D §4's split | fork counter (OBJ-9) |
| $c_{st}(r)$, $\bar c_{st}$ | the price of a step with $r$ children, nondecreasing in $r$; the step-weighted mean of $c_{st}(r)$ over a set of steps | calibration (D-23); derived |
| $c_{ib}$, $c_{mt}$ | prices: per iterator block; per memtable search (one per Get and one per scan) | calibration (D-23) |
| $x$, $c^0_x$, $R_x(n)$ | a priced foreground step type: a read step (filter probe, block read, run seek, reopen, iteration step with heap size $r$, iterator block, memtable search, and the fixed set-up of a Get or a scan) or a Put's memtable insert; its quiet price ($c^0_x = c_{st}(r)$ for an iteration step, $c^0_{put} = c_{put}$ for the Put insert); the type-$x$ steps operation $n$ makes (for a fixed part or the Put insert, 1 if $n$ is a Get, a scan or a Put, else 0) | stage 18, D-23; counters |
| $\mathcal X_{\mathrm{rd}}$, $\mathcal X_{\mathrm{wr}}$; $\beta_x$ | the read step types and the write step type (the Put insert); a type's priority weight, $\beta_x = \beta_R$ for $x \in \mathcal X_{\mathrm{rd}}$ and $\beta_W$ for $x \in \mathcal X_{\mathrm{wr}}$ | D §1 |
| $\mathrm{ReadCost}^0_x(a, b]$ | quiet priced cost of the type-$x$ foreground steps of operations $a+1..b$, $c^0_x\sum_{n=a+1}^bR_x(n)$ (the name is kept for the Put insert too) | host log counters |
| $\kappa^J_{x,\mathrm{kind}}$, $\kappa^B_x$ | interference coefficients, $\ge 0$: fractional slowdown of a type-$x$ step per running job of a kind; per unit of a job's byte rate | calibration (D-23) |
| $s^{\text{phys}}_{x,\iota}$ | physical surcharge of job $\iota$ on a type-$x$ step, $\kappa^J_{x,\mathrm{kind}(\iota)} + \kappa^B_xv_\iota$ (A10) | derived |
| $n^{\mathrm{win}}$, $n^{\mathrm{str}}$, $W_\iota$ | the window length, in operations, set by D §1's rule and fixed by a dated entry (D-23 or later); the stride of the counter snapshots, in operations, fixed by a dated entry (D-23 or later); job $\iota$'s window: its own operations $(n^b_\iota, n^e_\iota]$ if $\Delta n_\iota \ge n^{\mathrm{win}}$, and otherwise the operations from the last snapshot at or before $n^e_\iota - n^{\mathrm{win}}$ to $n^e_\iota$, so between $n^{\mathrm{win}}$ and $n^{\mathrm{win}} + n^{\mathrm{str}} - 1$ of them (all of the phase's operations, if it has served fewer; D §1) | D-23; derived |
| $\bar\varrho^{\,x}_\iota$ | quiet priced cost of type-$x$ foreground steps per operation over $W_\iota$ | host log counters |
| $I_\iota$; $I^B_\iota$, $I^J_\iota$; $I^{\mathrm{rd}}_\iota$, $I^{\mathrm{wr}}_\iota$ | job $\iota$'s priced interference charge, $\bar q\sum_x\bar\varrho^{\,x}_\iota\big(\kappa^B_xY_\iota + \kappa^J_{x,\mathrm{kind}(\iota)}t^{job}_\iota\big)$ over every foreground step type, charged at its completion; its byte and busy parts; its read part (the sum over $\mathcal X_{\mathrm{rd}}$, in $\mathcal C_R$) and write part (over $\mathcal X_{\mathrm{wr}}$, the Put inserts, in $\mathcal C_W$) | evaluator (D §1) |
| $Z^\beta$ | the priority-weighted value of an interference quantity $Z$ (a job's charge, a sum or a mean of charges, a charge per byte): $Z^\beta = \beta_RZ^{\mathrm{rd}} + \beta_WZ^{\mathrm{wr}} = \sum_x\beta_xZ_x$, with $Z_x$ its type-$x$ part; e.g. $I^\beta_\iota$, the job's charge as $J_\beta$ weighs it | D §1 |
| $\mathcal I$, $\dot{\mathcal I}$; $\mathcal I^{\mathrm{rd}}$, $\mathcal I^{\mathrm{wr}}$; $I^{\text{phys}}_\iota$, $\mathcal I^{\text{phys}}$ | the total of the $I_\iota$ and its rate; its read part, in $\mathcal C_R$, and write part, in $\mathcal C_W$; the physical charge $\sum_xs^{\text{phys}}_{x,\iota}\mathrm{ReadCost}^0_x(n^b_\iota, n^e_\iota]$ and its total, reported only | evaluator |
| $\varrho^{put}$, $\mathfrak w^B$, $\mathfrak w^J_{\mathrm{kind}}$ | the Put inserts' quiet cost per operation, $c_{put}q_{put}/q$, a constant of the operation mix; the write part's surcharges per byte on a job's interference basis, $\bar q\varrho^{put}\kappa^B_{put}$ (money per byte), and per unit of priced job cost, $\bar q\varrho^{put}\kappa^J_{put,\mathrm{kind}}/p_{\mathrm{dev}}$ (dimensionless) (D §1) | derived |
| $\mathfrak E$, $\Phi_\beta(\mathfrak E)$ | a run's operation-indexed record; the run's realised priced cost as a function of it, $J_\beta = \mathbb E\,\Phi_\beta$ (Lemma D.17) | host log, event log, counters |
| $I^O_i$, $\psi_i$ | the interference charge one more overlap byte adds to a merge sourced at level $i$ (its read and write parts); that byte's priced cost, $\psi_i = \beta_W(c_w + c_{cr}) + I^{O,\beta}_i$ (money per byte; Proposition D.13(i), Theorem A.2(iv)) | derived |
| $\bar I_i$, $\tilde I_i$, $\tilde I^{tm}_i$ | interference charge of level $i$'s merges per merged source byte (money), and over $c_w$; of its trivial moves per moved byte, over $c_w$; each with read and write parts and a priority-weighted value ($\tilde I^\beta_i$, $\tilde I^{tm,\beta}_i$) | evaluator; derived |
| $\bar I^{\mathrm{gross}}_i$, $I^X_j$ | priority-weighted interference charge of stage-$i$ merges per source byte entering the stage, in the flow with nothing dropped; the part of $\bar I^{\mathrm{gross}}_j$ one byte not written removes (local to Proposition B.1″(ii) and Corollary B.6) | derived |
| $\tilde c_{cr}$, $\tilde c^{\mathrm{kind}}_{job}$ | $c_{cr}/c_w$; $c^{\mathrm{kind}}_{job}/c_w$ (in bytes) | derived (G §2) |
| $e^{hd}_i$, $e^{ib}_i$, $h_i$, $\tilde h_i$, $e^{hp}_i$ | per scan: hidden steps charged to level $i$ (over entries in level $i+1$; for L0, in L0 and L1); data blocks of level $i$ loaded after the seeks; the heap-increment cost charged to level $i$; $\tilde h_i = q_{sc}h_i/(c_w\bar\lambda_1)$; $e^{hp}_i = h_i/c_{st}(1)$ (H §2) | per-level counters (OBJ-9); derived |
| $\tilde R^{st}$, $\tilde R^{ib}$ | $q_{sc}c_{st}(1)/(c_w\bar\lambda_1)$, $q_{sc}c_{ib}/(c_w\bar\lambda_1)$: workload price ratios like those of Proposition G.2 | derived |
| $\varrho^{(0)}$, $\varrho^{(\ge1)}$ | read intensity: quiet priced read cost per operation over the last decision interval, L0's part per L0 file and the rest (H §2) | per-level counters |
| $c^{\text{stage}}_i$, $c^{\text{drop}}_j$, $\Psi_j$ | priced cost per source byte entering merge stage $i$; priced saving per byte dropped at stage $j$; priced value of a byte dropped at stage $j$ (Proposition B.1″(ii)) | derived |
| $e$, $k_e$, $\lvert e\rvert$, $N^{\text{hid}}_e$, $N^{\text{tab}}_e$, $n^{\text{sc}}_e$, $p_{\text{cov}}(y)$, $\bar c_{\text{hd},e}$, $c^{\text{hold}}_e$ | an obsolete version, its key and its table bytes; the operations at which it is resident and hidden, and at which it is in a table; the scans that step over it; the probability that one scan steps past key $y$; the mean price of one crossing; its holding price per operation (Proposition B.5) | derived |
| $K^\star_\beta$, $K^\star_R$ | the L0 trigger that minimises the priced cost, and the read cost alone, in Proposition D.11's model (Proposition A.8) | derived |
| $\varepsilon_I$, $\varepsilon_{hd}$; $\delta_{\text{rew}}$ | the interference and hidden-step parts of G.4's $\varepsilon_r$; PROP-6's margin | derived; preregistration |
| $\mathcal E_i$, $\mathcal E^\circ_i$, $\mathrm{Rd}_i$ | jobs sourced at level $i$ completing in an interval, and the same without flushes; the quiet priced read cost charged to level $i$ (H §3) | attribution log |
| $A^\star_n$, $d_\iota$, $d$ | advantage gap (H.3); decision points strictly inside job $\iota$'s span, and their maximum (H.6) | derived |

**Job and level indices.** A job's quantities carry the Greek index $\iota$,
a level's a Latin $i$ or $j$, so that no letter changes meaning with its
index: $B_i$ (bytes at level $i$) and $Y_\iota$ (a job's bytes for
interference); $N_i$ (operations per turnover of level $i$) and
$\Delta n_\iota$ (operations served during job $\iota$); $\tau_i$ (turnover
time) and $\tau^{job}_\iota$ (a job's priced cost); $a_i$ and the job stamps
$n^b_\iota$, $n^e_\iota$; $\nu_i$ (runs seeked per scan) and $\varpi^{\mathrm{kind}}$
(jobs per user byte). In Pathway G §3, $b_j$ is level $j$'s incoming burst.

**Letters with two meanings that predate 2026-10-03.** $S$ is the space metric
and a compaction's source bytes; $O$ is the reads' reopen count and a
compaction's overlap bytes; $X$ is a compaction's output bytes and, with a
level index $X_{i\pm1}$, H §3's neighbour charge; $G$ is the obsolete bytes
available to compaction (Pathway B) and the global cost (H §3); $D_j$ is a
stage's dropped bytes (Pathway B) and $D_i$ the difference reward (H §3). The
context says which: the metric $S$ and the reopen count $O$ never carry a job
or level index, while source and overlap bytes carry one ($S_\iota$,
$O_\iota$) or appear in a ratio with each other ($o_i = O/S$); $X_\iota$ (Greek)
is a job's output, $X_{i\pm1}$ (Latin) a neighbour charge. $p_x$ (H §5) is
the exploration probability, not a quantity of a read-step type $x$; $\rho$
without an index (D §1) is a probe's reopen probability, not a pass-through
$\rho_i$; and $\mathcal R$ is H.3's exploration regret (the run record is
$\mathfrak E$).

**More letters with several meanings** (none is renamed). $d$: the bytes one compaction drops (§1.1); H.6's
$d_\iota$ and $d$, decision points inside a job; the job kind $d$, a merge
sourced deeper, which only ever appears as a sub- or superscript
($c^d_{job}$, $\kappa^J_{x,d}$); Pathway A's timing factor $d_i$; and C §3's
paired difference $d$. $W$: the write metric; in $W_r$ the $r$ is a label
("read"), not the heap size $r$; $W_\iota$ (Greek index) is a job's window,
and $\mathcal W$ D.11's interference weights. $s_i$ (Latin) is a level's
score, $s_\iota$ (Greek, D.11's (d2)) a job's stretch of constant $k_0$, and
$s^{\text{phys}}_{x,\iota}$ a physical surcharge. $\kappa_a$ and $\kappa_d$
(Pathway A §2) are time constants, and the $\kappa$ of Theorem A.1's
correction a fill; a bare $\kappa$ names the interference coefficients
collectively, and a single one always carries a superscript, $\kappa^J$ or
$\kappa^B$ (or is $\hat\kappa$ in D.13(iv)). $\lambda$ without an index is
the weight of the bytes read in $Y_\iota = X_\iota + \lambda(S_\iota +
O_\iota)$ (§1.1, D §1, A10); inside single proofs $\lambda$ is also a local
multiplier or scale (D.13, A.2, A.8, D.17), and $\bar\lambda_i$, $\lambda_i$
are a level's inflow rates (Pathway G). $h$: D.13(iv)'s share
of Gets that probe a level; $h_i$, the heap-increment cost charged to level
$i$; $h_w$, the settle hold window. $\varphi_i$ is a fill and
$\Delta\varphi$ (H §7) a change of fill; the write part's surcharges are
written $\mathfrak w^B$, $\mathfrak w^J$. $\varepsilon$: Lemma D.10's
false-positive probability of a filter probe (also in D.13); D.11's local
$\varepsilon_x(K)$, the exposure of (b3), with slope $e_x$; and G.4's error
terms $\varepsilon_r$, $\varepsilon_I$, $\varepsilon_{hd}$ (and
$\varepsilon_P$). $e$ is also B.5's obsolete version and the stem of
$e^{hd}_i$, $e^{ib}_i$, $e^o_i$. $\tilde c$: §1.1's $\tilde c^{\mathrm{kind}}_{job}$
and G §2's $\tilde c^{\langle i\rangle}_{job}$ are per-job prices in bytes
(divided by $c_w$), while H §2's $\tilde c^{job}_i$ and $\tilde c^{tm}_i$
are per byte (also divided by the mean bytes of a job), so
$\tilde c^{job}_i = \tilde c^{\langle i\rangle}_{job}/\bar s_i$. M1–M3 in D §3 are the 2026-09-11
write-accounting conventions; "the critique's model M2" is the critique's
own interference model (`2026-10-03-1816-interference-theory-critique.md`
§2), a different thing.

**Local symbols.** Symbols defined inside one result and used only there keep
their meaning inside it only. They are listed where they are defined:
Proposition D.11 and Corollary D.12 ($Q_F$, $N_a$, $N_b$, $N^{(0)}(K)$,
$\theta_B$, $Y^F$, $t^{job}_F$, $\mathcal W^0_x$, $\mathcal W^1_x$,
$\mathcal W^F_x$, $\varrho_x(k)$, $\varrho'_x$, $A_W$, $A_I$, $B_R$,
$K_{\text{cap}}$, $\chi^{cc}_x$, $\chi^{cv}_x$, $\Gamma^M_x$, $\Gamma^\pm_x$,
$\Upsilon(K)$, $\Lambda_x$, $a_h$, $b_h$, $n_p$, $g_p$, $s_\iota$,
$\mathcal W^d_x$, $\varepsilon_x(K)$ and its slope $e_x$, $\pi$, $c_p$,
$R_{\text{deep}}$); Proposition D.13
((I1)–(I4), $\bar\varrho^{\,x}_i$, $R(L)$, $R_0$, $b_1$, $b$, $h$, $R_K$,
$\hat\kappa$, $\Pi$, $E$, $E_I$, $E^0_I$, $\mathcal M(L)$,
$\varsigma = \ln f$, $\Delta\mathcal C$, the proof's $P_I$, and, under
(I4), the kind-free $\kappa^J_x$ and $\mathfrak w^J$, the common values of
$\kappa^J_{x,0} = \kappa^J_{x,d}$ and of $\mathfrak w^J_0 = \mathfrak w^J_d$);
Proposition D.11 also $\bar\pi_{sc}$; Lemma D.18 ($M_i$,
$O^{tot}_i$, $T_i$, $X_F$, $n^m_i$, $n^{tm}_i$, $n_F$, $\bar k^m_0$); Lemma
D.19 ($\mathcal K_s$, $v_n(k)$, $g_n(k)$, $\bar g_n$, $\mathcal L_n$, $J_s$,
$L_n$, $w_s$, $E^{tab}_s$, $R^{tab}_{hd}$, $b_{\text{blk}}$); Proposition A.8
($\mathcal D(n)$, (m1)–(m3)); Proposition G.6 ($\bar\varrho^{\,B}_\iota$,
$\bar\varrho^{\,J}_\iota$, $\bar Y_i$, $\bar t^{job}_i$,
$\langle\bar\varrho^{\,B}\rangle_i$, $\langle\bar\varrho^{\,J}\rangle_i$,
$\tilde R^{IB}$, $\tilde R^{IJ}$); Lemma H.5 ($\Delta(r)$, $E^{=}$,
$Z^{\text{last}}$); Proposition H.6 ($c_\iota(n)$, $C_\iota$, $G^{\text{comp}}$, $G^{\text{read}}$); H §7
($\hat I_j(\varrho, o)$, $\bar Y_j(o)$, $\bar\varrho_j$, $\bar o_j$,
$\varrho_{\text{now}}$, $\Delta\varphi$, $g_J$, $g_R$, $\hat I^{\mathrm{L0},\beta}(K)$,
$\varrho^{(0)}_-$, $\bar h_0(K)$, $\rho_g$, $\rho_s$); Proposition B.5 ($n^{\text{mt}}$, (H1),
(H2)); Corollary B.6 ($j_e$, $\mathcal Q$); D §4's gaming analysis
($D^{hd}$, $M^{hd}$); the draft model of D §0 ($K$, $Z$, $w$, $r$, $s$).
Where a local symbol shares a letter with a global one ($b$ and $h$ in
D.13, $J_s$ in D.19, $E$ in D.13 and H.5, $\pi$ in D.11, $G^{\text{comp}}$
and $G^{\text{read}}$ in H.6), the local meaning holds inside the result and
the global one is not used there.

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
- **A3′ (L0; amended 2026-10-03).** L0's score is
  $\max(k_0/K_0,\; \text{L0 bytes}/C_1)$. Multipliers do not enter it. The byte
  branch caps the effective trigger at about $C_1/F$. Precisely, both branches count only the L0 files not being compacted, the byte
  branch at their compensated sizes (`ComputeCompactionScore`), while $k_0$
  counts every L0 file, as the slowdown trigger does; the two differ only
  while an L0 merge runs, whose inputs leave the score (with A6, compensated
  and plain sizes are equal).
- **A4 (downward movement).** Compactions move data from level $i$ to $i+1$, or
  within L0 or within the last level; never upward.
- **A5 (fluid approximation; amended 2026-10-03, D-23).** Where a result
  treats flows as continuous it says so; per-compaction granularity is noted
  where it matters. A result that replaces the mean of a product of two
  time-varying quantities by the product of their means (mean field) — for
  example compaction activity and read intensity — says so and gives its
  error, measured or derived with its source. Measured (§0.7): every L0→L1
  merge ran with $k_0 = K_0$, and the merges sourced deeper ran with L0 empty
  on average (mean $k_0$ at most 0.13 per start level), though up to 5.4% of
  them ran with one L0 file in the seven runs (5.8% over all 23 runs of
  their cells). *Derived* from that pattern (critique Q2, model
  M2, provisional prices): mean field is close for the total interference
  but not for its slope in $K_0$ or its weights by level. $J_\beta$ never
  uses mean field: it charges each job at the cost per operation over its
  own window $W_\iota$ (D §1), which is the job's own operations when it
  serves at least $n^{\mathrm{win}}$ of them. A window longer than the job
  also averages the operations just before it, and as $n^{\mathrm{win}}$
  grows past the L0 cycle the charge tends to mean field (D §1, Proposition
  D.11); $n^{\mathrm{win}}$ is set below the merges' spans (D §1) so that every
  merge that serves at least $n^{\mathrm{win}}$ operations is charged over its
  own operations (a merge in a stall or the drain, or a shorter one, is not).
- **A6 (entries).** Unless stated otherwise: fixed entry size, no deletes or
  tombstones, and compression that does not depend on how entries are grouped
  into files.
- **A7 (steady state).** A window in which each level's mean fill is constant,
  so that each level's mean inflow equals its mean outflow. Where a result
  averages a product over the window (A5), A7 also means that the two factors
  are jointly stationary over it (amended 2026-10-03, D-23).
- **A8 (same-phase measurement; amended 2026-10-03, D-23).** All compared arms
  are scored over the same operations — every `mixgraph` operation, from the
  first ($n_w$) to the last, then the drain — with same-session twins (C §3).
  Each arm reaches $n_w$ on the settled tree its own load left (H §5); an arm
  not settled by then is invalid. Costs are totals over these operations:
  functions of the run's *operation-indexed record*, its events each stamped
  with the number of measured-phase operations completed when it happens
  (D §1, Lemma D.17). Wall time enters a cost only converted at $\bar q$.
- **A9 (fixed prices; new 2026-10-03, D-23).** Every price — $c_w$, $c_{cr}$,
  the per-job prices $c^{\mathrm{kind}}_{job}$, the quiet read prices $c^0_x$,
  $c_s$, $p_{\mathrm{dev}}$ — and every
  interference coefficient $\kappa$ is a constant, fixed in advance per
  machine (and per workload, on trees with its key distribution, for the
  base read prices: D-24, §0.7 item 2), the
  same in every run.
  $J_\beta$ uses these constants by definition (P-4: measured counts times
  fixed prices), so it is well defined whatever the machine does. As a
  statement about the machine — that a run's device time per unit equals the
  price — A9 is an approximation, and results that use that reading say so.
  Measured departures: $c_f$ moved 9.7% between two price sessions (D-22);
  `Assoc`'s reopens took 1.256 times stage 18's time (§0.7); the per-byte
  write time of D-15 §3(b) was higher at T = 2 than at T = 10, by 6.5–7.1% on
  `Assoc` and 2.2–3.2% on the power law, paired by repeat (means 6.7% and
  2.8%; critique P3, recomputed from `sst_write_seconds` over
  `sst_bytes_written` in `db/n1-assoc/graphs/summary.csv` and
  `db/n1-powerlaw/graphs/summary.csv`, the check reports). Each price with an
  instrument of the same source and phase is checked in every run (D-21's
  $c_{open}$ check, OBJ-7, OBJ-8).
- **A10 (linear, additive interference; new 2026-10-03, D-23; its use by the
  charge amended, and extended to every foreground step, the same day).** A
  foreground step of type $x$ (a read step, the fixed
  set-up of a Get or a scan, or a Put's memtable insert) served while the set
  $\mathcal J$ of background jobs runs costs
  $$c^0_x\Big(1 + \sum_{\iota\in\mathcal J}\big(\kappa^J_{x,\mathrm{kind}(\iota)} + \kappa^B_x\,v_\iota\big)\Big)$$
  in money, that is, this over $p_{\mathrm{dev}}$ in device time, with $c^0_x$ its quiet price, $v_\iota = Y_\iota/\Delta t_\iota$ job
  $\iota$'s physical byte rate, $Y_\iota = X_\iota + \lambda(S_\iota +
  O_\iota)$ its bytes on the byte basis (§1.1), and every $\kappa \ge 0$.
  $\lambda \ge 0$ is one value for every step type, an assumption the
  calibration tests; if it finds that $\lambda$ differs by step type, A10 is
  amended by a dated entry. A10 is linear in the
  features (a job of a kind
  running; its byte rate) and additive over concurrent jobs. It is stated at
  operation granularity: a step of operation $n$ is served during job $\iota$
  iff $n^b_\iota < n \le n^e_\iota$. The calibration tests linearity (a rate
  sweep) and additivity (one job against two). Outside the model: any
  non-additive overlap of a flush and a compaction, and the trainer process,
  which runs only in learner arms and shares the machine. A10 is a statement
  about the physical slowdown, which OBJ-8 tests on every run for the timed
  read steps; the calibration (OBJ-2) tests it for the rest. $J_\beta$ does
  not price the physical slowdown: it prices each job's charge $I_\iota$ at the
  reference rate (P-2, D §1), with the job's priced device time in place of
  its wall span and of the operations served during it.
- **A11 (in-situ job prices; new 2026-10-03, D-23).** The job prices
  $c^{\mathrm{kind}}_{job}$, $c_{cr}$ and $c_w$ are averages over the jobs of the
  reference arms, measured in place: they contain the effect of the
  foreground's reads and Puts and of a concurrent job on a job's own time,
  at the reference workload and configuration. A run whose jobs take longer
  or shorter is caught by the per-run job check (OBJ-7). A dependence of a
  job's time on the foreground intensity during it is not modelled.
  Foreground work (reads and Puts) slowing jobs is therefore inside the job
  prices once, and is not charged again (P-3).

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
second, as Cosine prices compute and storage in money [Cosine]).

The three formulas are the draft's adaptation, not Dostoevsky's own model
(corrected 2026-10-03). Dostoevsky keeps $K(L-1) + Z$ for short
range lookups only; it prices point lookups through Bloom-filter
false-positive rates and counts a long range lookup's entries (its Eqs.
8–11); its update cost (Eq. 12) is about half the draft's $T/K$ per level;
and its space amplification, $Z - 1 + 1/T$ (Eq. 13), is a constraint, not a
priced term. So issues 2, 4 and 6 below are defects of the draft's
adaptation, not of Dostoevsky.

*Check of the plotted example.* With $Z = K$ and $s = 0$,
$C = wTL/K + rKL$, minimised at $K^\star = \sqrt{wT/r}$. At $T = 16$ and
$w = r = 1$ this gives $K^\star = 4$; with $L \approx 3.33$, $\text{WA} = \text{RA}
= 13.3$ and $\text{SA} = 4\cdot16/15 = 4.27$, matching the plot.

**Six issues, and where each is fixed** (the sixth added 2026-10-03, D-23).

1. **The controller's knobs do not change $K$.** Every level $\ge 1$ of leveled
   RocksDB holds one run (A2), so $K = Z = 1$ whatever the multipliers are. $K$
   exists only at L0, where the trigger $K_0$ plays its role. *Fixed* by
   writing the model in the actual knobs: $K_0$, the multipliers, and depth
   (§3).
2. **RA priced every run check as a random page read.** With Bloom filters,
   most checks are an in-memory filter probe. $K(L-1)+Z$ is right for scans,
   which seek every run, and for lookups without filters. *Fixed* by pricing
   filter probes, block-reading probes and scan seeks separately (§1,
   Lemma D.10). A scan that returns many entries also pays for its
   iteration, which RA does not count: see issue 6.
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
6. **The model priced bytes and run checks, not every cost the knobs move**
   (added 2026-10-03, D-23). Each compaction job also costs a time that does
   not grow with its bytes (outside the timed read and write of its data:
   the output directory's fsync and the opening of its new files, the
   install with its MANIFEST write and sync, mutex waits; a median of
   2.7–3.5 ms per job, 71–128% of the counted compaction time), including a
   trivial move, which writes nothing and was priced at zero. A merge reads
   bytes as well as writing them. A long scan steps through entries and
   hidden versions and loads blocks after its seeks (`Assoc`'s scans return
   about 543 entries each). Every read searches the memtable, and every
   operation has a fixed in-store part: a Get's or scan's set-up, a Put's
   memtable insert. And a foreground step served while a background job runs
   is slower, by about 8–12% for read steps at about 100 MB/s of compaction
   traffic, compactions on against off (interference). All of these depend on
   the policy except the memtable search, the fixed parts and the entries a
   scan returns (§0.7). *Fixed* by pricing each background job (§1, Lemma
   D.18), each scan's iteration (§1, Lemma D.19), the memtable search, the
   fixed parts and the Put insert, and interference
   (§1), with $J_\beta$ kept free of time by the reference-rate principle
   (Lemma D.17).

### §1 Metrics and prices

**Metrics**, all over the measured phase (A8):

| Metric | Definition | Instrument |
| --- | --- | --- |
| $W$ | (flush + compaction bytes written) / user bytes written. SST output only (flush `table_file_creation` sizes, compaction `total_output_size`): the OPTIONS file every `SetOptions` call writes (A-Impl-5), and MANIFEST, WAL and info-log writes, are not counted | event log, as D-11 |
| $R_f$ | filter probes per Get: tables whose key range covers the key and whose filter is consulted, including filter-rejected ones | existing counter |
| $R_{blk}$ | probes per Get that pass the filter and read a data block (true plus false positives) | `rocksdb.bloom.filter.full.positive`; per level through counters keyed by the version's level (§4, Gate N0) |
| $R_{sk}$ | sorted runs seeked per scan (P1c-20) | existing counter |
| $O$ | the reads' table reopens: tables opened because a probe or a seek found them closed (the table cache holds about `max_open_files` − 10 = 990 tables, in sharded LRU shards and as a soft limit, since tables in use by live iterators can exceed it) | `rocksdb.read.table.reopen` differenced over the measured phase: the fork counts each table a Get or a user iterator reopens in `TableCache::FindTable`, per level, and times it (D-21). Opens by flushes and compactions are not counted; D-20's count (`rocksdb.no.file.opens` less the SSTs created, an upper bound that kept the compaction inputs' reopens) is reported beside it |
| $S$ | settled SST bytes / garbage-free bytes (D-3), at run end | evaluator |
| $\mathcal C_S$ | $(c_s/\bar q)\sum_n H(n)$ over the measured phase, computed as the sum, over the intervals between version installs, of $H$ times the operations served in the interval; $H$ is sampled, with the operation count, by the host log's flush and compaction completion listeners, which run just after each install with the DB mutex released, on every arm, native included. So the sum is exact up to the operations completed between an install and its listener (sub-millisecond). `SetOptions` versions change no file and are not sampled | telemetry, evaluator |
| $W_r$ | (added 2026-10-03, D-23) compaction bytes read per user byte written: $\sum_\iota(S_\iota + O_\iota)$ over the merges completing in the window, over user bytes. Flushes read the memtable and trivial moves read nothing, so neither counts | event log, per job as for $W$ |
| $\varpi^{\mathrm{kind}}$ | (D-23) jobs of each kind ($F$, $0$, $d$, $tm$) completing in the window, $\Xi^{\mathrm{kind}}$, per user byte written | host log job records, event log |
| $\mathcal C_W$ | (D-23) $\sum_\iota\tau^{job}_\iota$ over the jobs completing in the window, from $n_w$ to the end of the drain, plus the Puts' memtable inserts at $c_{put}$ and the write part of interference (below) | evaluator |
| $R_{nx}$ | (D-23) entries a scan returns after the one its seek positions on (that one belongs to the seek, whose price $c_{sk}$ stage 18 measures with `seek_nexts` = 0) | `rocksdb.number.db.next.found` |
| $R_{hd}$ | (D-23) hidden internal entries a scan steps over: older versions of keys it returns or passes, and tombstones if any; counted per level where the hidden entry lives, and charged to the level directly above it (§4) | `rocksdb.number.iter.skip` globally; per level a fork counter (OBJ-9) |
| $R_{ib}$ | (D-23) data blocks a scan's iterators load after their seeks, per level | fork counter (OBJ-9) |
| iteration steps by heap | (D-23) the scan's internal steps after its seeks, one per returned or hidden entry, $R_{nx} + R_{hd}$ in all, counted by the numbers $r^{\mathrm{L0}}$ and $r^{\neg0}$ of L0-file and other children in the merging iterator's heap at the step | fork counter (OBJ-9) |
| $\mathcal I$ | (D-23) the interference charge $\sum_\iota I_\iota$ (below), its read part in $\mathcal C_R$ and its write part in $\mathcal C_W$ | host log job and flush records with the cumulative foreground-step counters (read steps by type, and Gets, scans and Puts), and counter snapshots at a fixed operation stride (OBJ-8) |

Block-reading probes are counted logically, whether or not the block was cached,
so the metric does not depend on the page cache. Iterator blocks ($R_{ib}$) are
counted the same way (D-23).

**Prices.** One currency throughout: money, as Cosine prices compute and
storage [Cosine] (Cosine itself optimises cost under a latency bound, or the
reverse; it does not sum the three amplifications). $c_w$ per
byte written; $c_f$ per filter probe, a CPU cost that is not negligible on fast
storage [Zhu et al.]; $c_{blk}$ per block-reading probe; $c_{sk}$ per run seek;
$c_{open}$ per table reopen; and, since D-23, $c^{F}_{job}$, $c^{0}_{job}$,
$c^{d}_{job}$ and $c^{tm}_{job}$ per background job of each kind, $c_{cr}$ per
compaction byte read, $c_{st}(r)$ per iteration step, $c_{ib}$ per iterator
block and $c_{mt}$ per memtable search, and $c^0_{get}$ and $c^0_{sc}$ per Get and per scan for their fixed set-up and
$c_{put}$ per Put for its memtable insert (below): each is the device time the
operation takes, measured once per machine, converted at a fixed instance
price $p_{\mathrm{dev}}$ per device-second, a device being one core (D-15:
every priced operation runs on one thread). The foreground prices are
*quiet*, measured with no background job running, and also written $c^0_x$;
what a running job adds is the interference term (below). Every price is a
constant fixed in advance (A9). $c_s$ per byte held per
second is a storage price, and $c_s > 0$ always. At $c_s = 0$ the space term
vanishes: space priority becomes balanced mode, and in every mode expanding
levels and holding garbage cost nothing. The two money prices are fixed in
advance (§0.6) and every price is recorded in the fingerprint (OBJ-2). Since
the weight of space against the other terms is set by a price choice, results
are also reported at $c_s/2$ and $2c_s$. The operation rates $u$, $q$,
$q_{pt}$, $q_{sc}$ and $q_{put}$ are measured online; the reference rate $\bar q$ is fixed
in advance per workload (§0.6).

**Background jobs are priced per job (D-23).** A background job $\iota$ of kind
$\mathrm{kind}(\iota)$ — a flush ($F$), a merge sourced at L0 ($0$: L0→L1 and
intra-L0), a merge sourced at a level $\ge 1$ ($d$, last-level
self-compactions included), or a trivial move ($tm$) — is priced at
$$\tau^{job}_\iota = c^{\mathrm{kind}(\iota)}_{job} + c_{cr}\,(S_\iota + O_\iota) + c_w\,X_\iota,\qquad t^{job}_\iota = \tau^{job}_\iota/p_{\mathrm{dev}},$$
with $S_\iota = O_\iota = 0$ for a flush (it reads the memtable, not tables)
and $S_\iota = O_\iota = X_\iota = 0$ for a trivial move (its file is
relinked, not read or written). $t^{job}_\iota$ is the same price as a device
time: the job's *priced device time*, set by its kind and bytes, not its wall
span. $c^{\mathrm{kind}}_{job}$ is what a job of that kind costs beyond reading
and writing its data: for a merge, the part of `CompactionJob::Run` outside
`compaction_time_micros` (which times only `RunSubcompactions`: the output
directory's fsync and the opening of every new output file through the table
cache), then the install (a MANIFEST write and sync), DB-mutex waits and
listener work. The measured gap outside
`compaction_time_micros` was 2.7–5.3 ms per merge and a trivial move took
2.5–3.3 ms (per-run medians, §0.7). The output-file opens grow with a merge's output files, so
a price per job of a kind is an approximation that OBJ-7 checks, per kind and
per start level. Each job is charged at its
completion, as $W$ already counts its bytes. Over the measured phase, with
$U$ the user bytes written, the *job part* of the write cost is
$$\mathcal C^{job}_W = \sum_\iota\tau^{job}_\iota = c_w\cdot(\text{bytes written}) + c_{cr}\cdot(\text{compaction bytes read}) + \sum_{\mathrm{kind}}c^{\mathrm{kind}}_{job}\,\Xi^{\mathrm{kind}} = U\Big(c_wW + c_{cr}W_r + \sum_{\mathrm{kind}}c^{\mathrm{kind}}_{job}\,\varpi^{\mathrm{kind}}\Big),$$
in money: each term is a price per byte or per job times bytes or jobs. The
whole write cost adds the Puts' memtable inserts and the write part of
interference (below):
$$\mathcal C_W = \mathcal C^{job}_W + c_{put}\cdot(\text{Puts}) + \mathcal I^{\mathrm{wr}}.$$ The
metric $W$ is unchanged; Lemma D.18 expresses $W_r$ and $\varpi^{\mathrm{kind}}$
through the per-level flows. The prices are fitted to the jobs' spans — the host log's
`job_begin` to `job_end`, a flush's `flush_started` to `flush_finished` — on
the reference arms; D-23 states the procedure (how the fit separates
$c_{cr}$ from $c_w$ was decided 2026-10-04, D-24, §0.7 item 11: a
garbage-heavy neighbour in the job-size sweep, or failing that a combined
fit with the restriction stated). So $c_w$ changes meaning: D-15 §3(b)
divided the jobs' whole time by their bytes; $c_w$ is now the price of one
more byte written by a job, with the per-job and read parts priced apart.
Two limits are stated, not assumed away. A merge's bytes read and written
move together (on `Assoc` at T = 10 merges read 12–13% more than they write,
read over written 1.124 on the pilot and 1.127 on the $\bar q$ arm, event
logs, the check reports; critique P10's 11% is the share of read bytes
not written, $1 - 1/1.127$), so the fit may separate $c_{cr}$ from $c_w$ less well than it
determines their sum; a result that turns on the split — a byte dropped by a
merge saves $c_w$ but is still read at $c_{cr}$ — says so. And the median
per-job gap was 3.4–3.5 ms at T = 2 against 2.7–3.0 ms at T = 6 and 10 (the
$\bar q$ arm, 2.97 ms), which job kinds
may not absorb: the per-run job check (OBJ-7) tests it. Decided 2026-10-04
(D-24, §0.7 item 5): the per-job prices stay fixed per kind, and a state term
is added only if OBJ-7 fails at T = 2.
The job prices are in-situ averages (A11); listener work includes the host
log's own, which runs on every arm.

**Scans are priced by their iteration too (D-23).** Beyond its $R_{sk}$ run
seeks, a scan returns $R_{nx}$ entries and steps over $R_{hd}$ hidden ones;
each of these $R_{nx} + R_{hd}$ internal steps passes through the merging
iterator's heap, whose children are the scan's live sources: the L0 files
with a key at or after the seek key, the levels with a file in range, and
non-empty memtables. A step has $r$
children, $r^{\mathrm{L0}}$ of them L0 files and $r^{\neg0}$ the others
(memtables and level iterators), and costs $c_{st}(r)$, nondecreasing in $r$;
its form is fixed by
calibration (expected roughly affine in $\log_2 r$, the heap's depth), and no
result assumes it convex in $r$ unless it says so. The iterator also loads
$R_{ib}$ data blocks after its seeks, at $c_{ib}$ each. So a scan costs
$$c_{sk}R_{sk} + \sum_{\text{steps}}c_{st}(r) + c_{ib}R_{ib} = c_{sk}R_{sk} + \bar c_{st}\,(R_{nx} + R_{hd}) + c_{ib}R_{ib},$$
where $\bar c_{st}$ is defined as the step-weighted mean of $c_{st}(r)$ over the
scan's steps, so the two forms are equal by definition, and the same holds
over any set of steps. $R_{nx}$ is fixed by the operation sequence; $R_{hd}$
and $R_{ib}$ depend on the policy (Lemma D.19). Holding garbage near hot keys
raises $R_{hd}$, so garbage now costs reads as well as space. The measured
hidden steps per returned entry (1.15 at T = 2, 0.23 at T = 10) are about 11
and 5.4 times the $S - 1$ that garbage spread uniformly over the keys would
give on the same runs (§0.7), so no result assumes uniform garbage.
$c_{st}(r)$ and $c_{ib}$
come from price trees run with `seek_nexts` > 0, at several depths and
garbage levels (D-23); $c_{sk}$ stays the seek's price at `seek_nexts` = 0
(D-15, D-22), which covers positioning on the first entry.

**The memtable search is priced (D-23).** Every Get and every scan first
searches the memtables, at $c_{mt}$ each. The number of Gets and scans is
fixed by the operation sequence, so this term is the same in every arm of a
workload: priced so that the level of $J_\beta$ is right (P-1), charged to a
shared bucket (§4), and cancelling from every difference between arms (it
does change ratios, such as the regret of Definition C.5). The search's device
time depends on what the memtables hold, which the policy changes only
through Lemma D.15's flush-timing caveat; $J_\beta$ charges the fixed price.
Measured: a read takes 0.22 µs more whenever writes keep the memtable
populated, in every configuration (§0.7). D-23 states how $c_{mt}$ is
measured; it is measured per workload, on trees with the workload's key
distribution (D-24, §0.7 item 2).

**The fixed parts and the Put insert are priced** (2026-10-03, D-23). Every Get and every scan also pays a fixed in-store
set-up, $c^0_{get}$ or $c^0_{sc}$, and every Put its memtable insert,
$c_{put}$. $c^0_{get}$ is the fixed in-store part of a Get: the device time
of the store's Get call that does not depend on the tree (snapshot,
SuperVersion reference, lookup-key set-up and return), with the memtable
search ($c_{mt}$), every table step ($c_f$, $c_{blk}$, $c_{open}$) and the
client's own work outside the call excluded. $c^0_{sc}$ is the fixed
in-store part of a scan: the device time of creating its iterator and of the
fixed part of its first `Seek` (snapshot, SuperVersion reference,
merging-iterator set-up), with the run seeks ($c_{sk}$), the memtable search
($c_{mt}$), the iteration steps and iterator blocks ($c_{st}$, $c_{ib}$),
any reopen ($c_{open}$) and the client's own work excluded; what grows with
the runs a scan opens is in its seeks. Their counts are fixed by the operation
sequence, so these terms, like the memtable search, are the same in every arm
of a workload: priced so that the level of $J_\beta$ is right (P-1), charged
to the shared *fixed* bucket (§4), the Puts' part in $\mathcal C_W$ (weight
$\beta_W$) and the Gets' and scans' in $\mathcal C_R$ (weight $\beta_R$). The
intercept of stage 18's Get fit (D-15 §3(c)) contains $c^0_{get}$, but also
the client's key generation, which P-1 excludes, and the search of the price
trees' memtables; D-23 states how $c^0_{get}$, $c^0_{sc}$ and $c_{put}$ are
calibrated so that no time is priced twice or priced from outside the store.
A scan's set-up has no such fit to start from: $c^0_{sc}$ is measured by an
in-store timer of the scan's set-up, which the new binary carries (Gate N0
item 10), so that every calibration runs on that one binary (CMP-9). If a
dated entry makes $c_{put}$ affine in the Put's bytes, $c_{put}$ is its mean over the
workload's Puts, again the same in every arm. The write-ahead log is
disabled in every Programme 1 run (`DISABLE_WAL=1`, fingerprint field
`wal1`), so its price $c_{wal}$ per log byte is zero. A run with the log on
would need $c_{wal}$, and the log's own slowdown under jobs, priced like the
Put insert's; the fingerprint keeps such runs apart from these. What the
policy does change is the interference on these steps, which is charged to
the jobs (below).

**Interference (D-23; the charge amended by the integration decision of the
same day, and extended the same day to every foreground step).** A
foreground step served while a background job runs takes longer,
because the job shares the machine's cores, memory bandwidth, caches and
device (§0.7). For every background job $\iota$ the host log records its kind,
its start level, its bytes on $\kappa$'s byte basis,
$Y_\iota = X_\iota + \lambda(S_\iota + O_\iota)$ (§1.1: the bytes it writes
plus $\lambda \ge 0$ times the compaction bytes it reads; $\lambda$ is one
value for every step type, fixed by the calibration, and $\lambda = 0$
counts bytes written only), its wall span $\Delta t_\iota$, and its stamps
$n^b_\iota$ and $n^e_\iota$: the numbers of measured-phase operations
completed at its begin and at its end record. $\Delta n_\iota = n^e_\iota -
n^b_\iota$ operations are served during the job, and operation $n$ is served
during job $\iota$ iff $n^b_\iota < n \le n^e_\iota$; $\mathcal J(n)$ is the
set of such jobs.

*The physical slowdown.* By A10 a type-$x$ step of operation $n$ costs
$c^0_x(1 + \sum_{\iota\in\mathcal J(n)}s^{\text{phys}}_{x,\iota})$ (its
device time is this over $p_{\mathrm{dev}}$), with
$s^{\text{phys}}_{x,\iota} = \kappa^J_{x,\mathrm{kind}(\iota)} + \kappa^B_xv_\iota$
and $v_\iota = Y_\iota/\Delta t_\iota$. Here $x$ runs over every priced
foreground step: the read steps $\mathcal X_{\mathrm{rd}}$ (filter probe,
block read, run seek, reopen, iteration step with $c^0_x = c_{st}(r)$,
iterator block, memtable search, and the fixed set-up of a Get or a scan)
and the write step $\mathcal X_{\mathrm{wr}}$, a Put's memtable insert; the
calibration decides which $\kappa$ are nonzero. Summing the surcharge over the
operations and exchanging the two finite sums,
$$\sum_{n=1}^{N}\sum_xc^0_xR_x(n)\sum_{\iota\in\mathcal J(n)}s^{\text{phys}}_{x,\iota} = \sum_\iota I^{\text{phys}}_\iota,\qquad I^{\text{phys}}_\iota = \sum_xs^{\text{phys}}_{x,\iota}\,\mathrm{ReadCost}^0_x(n^b_\iota, n^e_\iota],\qquad \mathrm{ReadCost}^0_x(a, b] = c^0_x\sum_{n=a+1}^{b}R_x(n),$$
an identity that holds with any number of concurrent jobs. $I^{\text{phys}}_\iota$
depends on the run's speed twice: through $v_\iota$, a rate per second, and
through $\Delta n_\iota$, which falls when the foreground slows relative to
the job and is 0 when the job runs only while the client is blocked in a
stalled write. It is reported per arm (OBJ-6) and tested against a timer
(OBJ-8), never priced.

*The charge.* $J_\beta$ prices each job at the reference rate (P-2, below):
$$I_\iota = \bar q\sum_x\bar\varrho^{\,x}_\iota\Big(\kappa^B_x\,Y_\iota + \kappa^J_{x,\mathrm{kind}(\iota)}\,t^{job}_\iota\Big),\qquad \bar\varrho^{\,x}_\iota = \frac{\mathrm{ReadCost}^0_x(W_\iota)}{|W_\iota|}.$$
$W_\iota$, the job's *window*, is its own operations
$(n^b_\iota, n^e_\iota]$ if $\Delta n_\iota \ge n^{\mathrm{win}}$, and
otherwise the operations from the last counter snapshot at or before
$n^e_\iota - n^{\mathrm{win}}$ up to $n^e_\iota$ (all of the phase's
operations, if it has served fewer than $n^{\mathrm{win}}$): it holds
$\max(\Delta n_\iota, n^{\mathrm{win}})$ operations, plus at most
$n^{\mathrm{str}} - 1$ from rounding its start down to a snapshot, with
$n^{\mathrm{str}}$ the snapshots' stride (the instrument, below). $n^{\mathrm{win}}$ and $n^{\mathrm{str}}$ are fixed numbers of
operations, fixed by a dated entry (D-23 or later); $\mathrm{ReadCost}^0_x(W)$ is the quiet priced cost
of the type-$x$ steps of the operations in $W$; and $t^{job}_\iota$ is the job's priced device
time (above), not its wall span. The reading: at the reference rate the store
serves $\bar q\,t^{job}_\iota$ operations during the job's priced device time;
each of their type-$x$ steps costs $\bar\varrho^{\,x}_\iota$ per operation
quiet and is slowed by $\kappa^J$ for the running job and by $\kappa^B$ times
the job's priced byte rate $Y_\iota/t^{job}_\iota$; so
$\bar q\,t^{job}_\iota\sum_x\bar\varrho^{\,x}_\iota(\kappa^J_{x,\mathrm{kind}(\iota)} + \kappa^B_xY_\iota/t^{job}_\iota) = I_\iota$:
what the run would cost if it served operations at $\bar q$. The conversion
has two anchors, both free of wall time. The byte
part follows space's convention exactly: anchoring the job's span on the
operations it serves, $\Delta n_\iota/\bar q$ reference seconds, gives the
byte rate $\bar qY_\iota/\Delta n_\iota$ and, over those $\Delta n_\iota$
operations, $\bar qY_\iota\sum_x\kappa^B_x\bar\varrho^{\,x}_\iota = I^B_\iota$
(for a window that is the job's own operations). The busy part departs from
it on purpose: space's convention would leave it at its physical form,
$\Delta n_\iota\sum_x\kappa^J_{x,\mathrm{kind}(\iota)}\bar\varrho^{\,x}_\iota$, which vanishes in a
stall; it is anchored instead on the job's priced device time,
$\bar q\,t^{job}_\iota$ operations (Lemma D.17(iv)). $I_\iota$ is in money ($\kappa^J$ dimensionless,
$\kappa^B$ time per byte, $\bar q\,t^{job}_\iota$ and $\bar q\,\kappa^BY_\iota$
counts of operations, $\bar\varrho$ money per operation). Each job carries one
charge, made at its completion. Split by step type, its *read part*
$I^{\mathrm{rd}}_\iota$ (the sum over $x \in \mathcal X_{\mathrm{rd}}$) is
device time of slowed read steps and sits in $\mathcal C_R$, weight
$\beta_R$; its *write part* $I^{\mathrm{wr}}_\iota$ ($x$ the Put insert) is
device time of slowed Puts and sits in $\mathcal C_W$, weight $\beta_W$ (P-3). So $J_\beta$ weighs the charge as
$I^\beta_\iota = \beta_RI^{\mathrm{rd}}_\iota + \beta_WI^{\mathrm{wr}}_\iota$,
and $\mathcal I = \sum_\iota I_\iota = \mathcal I^{\mathrm{rd}} + \mathcal I^{\mathrm{wr}}$.
Every $\kappa \ge 0$, so $I_\iota \ge 0$, and
$I_\iota \le \bar q\sum_x(\kappa^B_xY_\iota + \kappa^J_{x,\mathrm{kind}(\iota)}t^{job}_\iota)\max_nc^0_xR_x(n)$.
Write $I^B_\iota = \bar q\,Y_\iota\sum_x\kappa^B_x\bar\varrho^{\,x}_\iota$ and
$I^J_\iota = \bar q\,t^{job}_\iota\sum_x\kappa^J_{x,\mathrm{kind}(\iota)}\bar\varrho^{\,x}_\iota$
for its byte and busy parts.

- **What the charge depends on.** Given its kind and bytes, $I_\iota$ depends
  on the run only through the $\bar\varrho^{\,x}_\iota$, the mean quiet cost
  per operation of each step type over its window. It does not contain $\Delta t_\iota$, and it
  contains $\Delta n_\iota$ only through which operations the window holds. A
  job that serves no operation ($\Delta n_\iota = 0$), in a stall or in the
  drain, is charged at the read cost of the $n^{\mathrm{win}}$ operations
  before its end, like any other job (Lemma D.17(v)).
- **Charge against physics.** When the window is the job's own operations
  ($\Delta n_\iota \ge n^{\mathrm{win}}$), the byte part is the physical one
  times $\bar q/q_\iota$, and the busy part the physical one times
  $(\bar q/q_\iota)(t^{job}_\iota/\Delta t_\iota)$ (Lemma D.17(iii)). The first
  factor is the conversion to the reference rate; the second is the job's
  priced device time over its wall span, which the per-run job check (OBJ-7)
  keeps near 1 summed over the jobs.
- **The window** (length fixed by the post-integration decision of
  2026-10-03). $n^{\mathrm{win}}$ makes the charge defined at
  $\Delta n_\iota = 0$, and keeps a short job's charge from resting on a
  handful of operations. Its cost: a job that serves fewer than
  $n^{\mathrm{win}}$ operations is charged at a read cost that also averages
  operations served before it began. So the measured correlation below is
  carried into the charge exactly only by windows no longer than the jobs;
  as $n^{\mathrm{win}}$ grows past the L0 cycle every window's mean tends to
  the run's time average, which is mean field (Proposition D.11, "When (d2)
  fails"). Hence the rule: $n^{\mathrm{win}}$ is a fixed operation count,
  set from the reference arms' merge spans (its value fixed by a dated
  entry, D-23 or later) so that it lies below the
  span, in operations, of the merges Proposition D.11's (d2) relies on (the
  L0 merges and the merges sourced deeper). A merge that meets
  $\Delta n_\iota \ge n^{\mathrm{win}}$ is charged over exactly its own
  operations, $W_\iota = (n^b_\iota, n^e_\iota]$, so the measured correlation
  survives in its charge. Only the jobs shorter than $n^{\mathrm{win}}$ (the
  shortest jobs, such as trivial moves, which served at most 461 operations
  on the pilots and 484 on the $\bar q$ arm in their first repeats, H.6, and
  503 and 546 over all repeats) and the jobs that serve no
  operation (in a stall or the drain) are charged over the (rounded)
  $n^{\mathrm{win}}$ operations before $n^e_\iota$, reaching back before they
  began. OBJ-8 reports, per run, the
  share of merge bytes whose window exceeded their own span. (The 2026-10-03
  design had suggested about one decision interval's operations, longer than
  most jobs: an L0 merge took about 39 ms at `Assoc` T = 10, critique Q2,
  about 2,700 operations at $\bar q$ = 69,021.5 per second.)
- **No mean field in $J_\beta$.** $J_\beta$ charges each job at the read cost
  over its own window, not at the run's mean. The overlap is strongly
  correlated with the tree's state: L0→L1 merges run at $k_0 = K_0$, the
  merges sourced deeper with L0 empty on average (§0.7). Analytic results
  (D §3, Pathways A, G and H) that use mean field say so, with its error
  (A5); those that carry the correlation into the charge state the window
  condition they need, and D.11 also the exposure of the deeper jobs to
  $k_0 \ge 1$ (its (b3)).
- **The write part in the analytic results.** The Put
  insert's quiet cost does not depend on the tree. Assume that the Puts'
  share of a job's window has, given the job's kind and bytes, the mix's
  mean, as on a stationary `mixgraph` it does up to the window's dependence
  on the job's timing (Lemma D.17(v)). Then
  $$\mathbb E\big[I^{\mathrm{wr}}_\iota\,\big|\,\text{kind, bytes}\big] = \bar q\varrho^{put}\big(\kappa^B_{put}Y_\iota + \kappa^J_{put,\mathrm{kind}(\iota)}t^{job}_\iota\big) = \mathfrak w^BY_\iota + \mathfrak w^J_{\mathrm{kind}(\iota)}\,\tau^{job}_\iota,$$
  with $\varrho^{put} = c_{put}q_{put}/q$, $\mathfrak w^B = \bar q\varrho^{put}\kappa^B_{put}$
  and $\mathfrak w^J_{\mathrm{kind}} = \bar q\varrho^{put}\kappa^J_{put,\mathrm{kind}}/p_{\mathrm{dev}}$.
  *Proof.* $I^{\mathrm{wr}}_\iota = \bar q\bar\varrho^{\,put}_\iota(\kappa^B_{put}Y_\iota + \kappa^J_{put,\mathrm{kind}(\iota)}t^{job}_\iota)$,
  $\bar\varrho^{\,put}_\iota$ has conditional mean $\varrho^{put}$ by the
  assumption, and $t^{job}_\iota = \tau^{job}_\iota/p_{\mathrm{dev}}$.
  $\blacksquare$ So on the write side a job costs, in expectation,
  $(1 + \mathfrak w^J_{\mathrm{kind}})\tau^{job}_\iota + \mathfrak w^BY_\iota$:
  its per-job, read-byte and written-byte prices scaled by
  $1 + \mathfrak w^J_{\mathrm{kind}}$, plus $\mathfrak w^B$ per byte on the
  interference basis. The scaling is the same at every level unless
  $\kappa^J_{put}$ depends on the kind. The analytic results of D §3 and
  Pathways A, G, B and H §7 carry the write part this way or as an explicit
  term, each where it says so.
- **Concurrent jobs.** With `max_background_jobs` = 2 a flush and a compaction
  can overlap (G.4). Each job carries its own charge by definition. That the
  physical surcharges of two concurrent jobs add is A10's additivity, which
  the calibration tests (one job against two). Any non-additive overlap and
  the trainer process are outside the model, so every claim that
  $\mathcal I$ prices the physical slowdown holds within A10 only.
- **The reverse direction.** Foreground work (reads and Puts) also slows
  jobs. That effect is inside the in-situ job prices (A11), so it is not
  charged again (P-3).
- **The instrument.** The host log's begin and end records of every
  compaction, trivial move and flush (flushes gain a begin record) carry the
  stamps and the cumulative foreground-step counters by type (read steps,
  and the counts of Gets, scans and Puts), and the host log also writes the
  cumulative counters with their operation stamp every $n^{\mathrm{str}}$
  operations, so that a window that starts before its job's begin record can
  be read. A dated entry (D-23 or later) fixes $n^{\mathrm{str}}$; a window that does not start at its
  job's begin record starts at a snapshot, as defined above, so the
  evaluator computes every $\bar\varrho^{\,x}_\iota$ and $I_\iota$ from the
  log alone (a fork change, so a new binary). Read at an event, the counters
  may include part of the operation in progress, so the evaluator's
  $I_\iota$ equals the charge of the operation-indexed record (Lemma D.17)
  up to one operation's steps at each end of the window, unless a dated
  entry (D-23 or later) has the counters read as of the last completed
  operation. Each run
  checks A10 against a fork timer, stratified by step type and level so that
  the reads served during jobs are compared with reads of the same kind
  served without (OBJ-8(b)).
- **The basis is open.** Whether the physical slowdown goes per byte, per
  busy time or per job is not identified (§0.7), and it decides the weights
  by level. Physically, per busy second of wall time L1 and L2 bytes would
  pay 1.9 and 2.6 times what L0 bytes pay, and per job 12 and 21 times
  (critique Q4, `Assoc` T = 10). The charge's busy part counts priced device
  time, so its weight per byte at a level follows the level's per-job price
  over its job bytes and $c_w + c_{cr}$ per merged byte, not the level's
  measured job speed (Proposition G.6). The basis, and $\lambda$ in
  $Y_\iota$, are set by the calibration; the owner decides only if it
  cannot identify them (D-24, §0.7 item 1).

**Reopens are priced apart (D-20).** RocksDB's table cache keeps about
`max_open_files` − 10 tables open (`open_files` = 1,000 in every run, so 990,
against about 5,800 SSTs in the trees; a sharded and soft limit),
and a probe
or a seek that finds its table closed reopens it: it opens the file and reads
and parses the table's footer, index and filter. On the node a reopen takes
about 10 µs, against about 0.1–0.2 µs for a filter probe on an open table.
How often a probe reopens a table is set by the table cache and the key
distribution, not by the probe: the price runs' uniform keys reopened 0.33–0.49
tables per probe, the experiments' skewed keys 0.06–0.13. A price per probe
that holds reopens would carry the price runs' rate into every run. So $c_f$,
$c_{blk}$ and $c_{sk}$ are measured with every table open, $c_{open}$ from the
same reads run once with the experiments' `open_files` and once with every
table open, and each run pays for the reopens it made.

**Every run checks $c_{open}$ (D-21).** $c_{open}$ is measured on stage 18's
trees, and a workload could reopen at another cost. The fork times every
reopen it counts, from the open to the cache insert. Stage 18 records the
same timer's seconds per reopen on its capped Gets as a reference, and each
run's own seconds per reopen, over the measured phase, is divided by it.
Outside $1 \pm 0.10$, $c_{open}$ does not hold for the run: it is not
priced, and its workload's $c_{open}$ is measured on that workload by a
dated entry. Under 1,000 reopens the check does not apply. Amended
2026-10-04 by D-24 (§0.7 item 3; D-21 is a dated decision, which this
document does not amend): the run's ratio is compared with $1 + \bar s_{open}$,
not with 1 (below), that is, the run's time per reopen with
$c^0_{open}(1 + \bar s_{open})$, keeping D-21's 10%.

**Reopens in the analysis.** §3 and Pathway G price a probe at $c_f$ and do not
model the table cache. If a probe reopens a table with a probability $\rho$ the
knobs do not change, its expected price is $c_f + \rho\,c_{open}$, and their
results hold with that in place of $c_f$. In general the knobs do change $\rho$
(more or larger levels change which tables stay open), so that is an
approximation; $J_\beta$ always charges the counted reopens. The controller
uses it with $\rho$ measured per level (H §7, D-21). Interference does not
change this price: a reopen's surcharge under a running job is charged to the
job, through $I_\iota$, not to the probe, so $c_f + \rho\,c^0_{open}$ stays the
probe's quiet price.

**Reopens under interference (D-23).** A reopen is a read step like the
others: served during jobs $\mathcal J(n)$ it costs
$c^0_{open}(1 + \sum_{\iota\in\mathcal J(n)}s^{\text{phys}}_{open,\iota})$ (A10;
its device time is this over $p_{\mathrm{dev}}$), and
$J_\beta$ charges the surcharge to the jobs, with $\kappa^J_{open,\mathrm{kind}}$
and $\kappa^B_{open}$ calibrated like the other coefficients. So D-21's check,
which compares the run's own seconds per reopen with stage 18's quiet
reference, sees the surcharge as a departure. Under D-24's amendment
(above) the run's ratio is compared with
$$1 + \bar s_{open},\qquad \bar s_{open} = \frac{1}{O}\sum_\iota s^{\text{phys}}_{open,\iota}\;O(n^b_\iota, n^e_\iota],$$
the reopen-weighted mean surcharge A10 predicts for the run, with
$O(n^b_\iota, n^e_\iota]$ the reopens of operations $n^b_\iota+1..n^e_\iota$
($O$ here is always a reopen count, never overlap bytes). The prediction uses
the physical surcharge, not the charge $I_\iota$, because the timer measures
the run's own time. This keeps D-21 §3's assumption that the surcharge scales the timed
part of a reopen and the rest of it alike. With OBJ-8's split timer, the
reopens served while no job runs also give a direct check of $c^0_{open}$
alone; D-24 adopts the comparison with $1 + \bar s_{open}$. The comparison
must be stratified as
OBJ-8(b) is, by level or table-size class: L0's flush-sized
files take longer to reopen than the 512 KiB files below, and more of their
reopens fall inside L0 merges, so the reopens served during jobs and the
others are different mixes. The tolerance is D-21's 10% (D-24). Measured
(§0.7): of `Assoc`'s 1.256, the writes account for about 0.09, the scans'
pollution of the caches about 0.08 and the rest about 0.07. Interference can
explain only the writes' part. D-24 decided `Assoc`'s $c_{open}$
(§0.7 item 3): it is measured on `Assoc`.

**Cost rate** (amended 2026-10-03, D-23).
$$c(t) = \Big[c_w\dot w + c_{cr}\dot w_r + \sum_{\mathrm{kind}}c^{\mathrm{kind}}_{job}\dot\Xi^{\mathrm{kind}} + c_{put}q_{put} + \dot{\mathcal I}^{\mathrm{wr}}\Big] + \Big[q_{pt}\big(c^0_{get} + c_fR_f + c_{blk}R_{blk}\big) + q_{sc}\big(c^0_{sc} + c_{sk}R_{sk} + \bar c_{st}(R_{nx} + R_{hd}) + c_{ib}R_{ib}\big) + c_{open}\dot o + c_{mt}(q_{pt} + q_{sc}) + \dot{\mathcal I}^{\mathrm{rd}}\Big] + c_sH\,\frac{q}{\bar q},$$
every quantity at time $t$. The first
bracket is the write cost rate: $\dot w$, $\dot w_r$ and
$\dot\Xi^{\mathrm{kind}}$ are bytes written, compaction bytes read and jobs of
each kind per second, each job counted at its completion; $q_{put}$ is Puts
per second, each paying its memtable insert; and $\dot{\mathcal I}^{\mathrm{wr}}$
is the rate of the write part of the interference charge. The second is the
read cost rate, at quiet prices plus the surcharge: each Get and scan pays
its fixed set-up; $\dot o$ is the reads' table reopens per second ($O$ is its
integral); $\bar c_{st}$ is the step-weighted mean of $c_{st}(r)$ over the
steps served at $t$, so $q_{sc}\bar c_{st}(R_{nx} + R_{hd})$ is exactly the
rate of $\sum_{\text{steps}}c_{st}(r)$; and $\dot{\mathcal I}^{\mathrm{rd}}$ is
the rate of the read part of the interference charge. The write and read parts jump, by
$I^{\mathrm{wr}}_\iota$ and $I^{\mathrm{rd}}_\iota$, when job $\iota$
completes. The last term is space. Every term is money per second: a price
per byte, per job, per operation, per step or per search times a rate of
bytes, jobs, operations, steps or searches; $\dot{\mathcal I}^{\mathrm{wr}}$
and $\dot{\mathcal I}^{\mathrm{rd}}$ are money per second by construction; $c_s$ (money per byte-second) times $H$
(bytes) times the dimensionless $q/\bar q$. Rates such as $\dot w$ and
$\dot{\mathcal I}$ are rates of counting processes that jump at events; the
integrals below are sums over those events. Relation to the draft:
$w\cdot\text{WA} = c_wuW$ is the first term of the write bracket; $r\cdot\text{RA}$
becomes the run-check terms of the read bracket; $s\cdot\text{SA} =
c_sN_{\text{live}}S = c_sH$ at the reference rate.

**Space is charged per operation served, not per second.** Every arm serves the
same operation sequence (one client thread, a fixed seed, `mixgraph` stopped by
operation count) and is scored over the same operations (A8), so the write and
read terms integrate to counts that do not depend on how long the run takes
(the interference term too, once converted by P-2 below). A
space term of $c_sH$ per second would not: a run that takes longer holds the
same live bytes for longer and pays for it. With the factor $q/\bar q$,
$\int c_sH\,(q/\bar q)\,dt = (c_s/\bar q)\sum_n H(n)$, where $H(n)$ is $H$ when
operation $n$ is served. $H$ changes only when a version is installed, so the
sum is the sum, over the intervals between installs, of $H$ times the
operations served in the interval (computed from samples taken just after
each install, exact up to that lag; the metrics table). Space is priced by what is held
while the store serves each operation (Lemma D.15). $c_s$ keeps its meaning —
the price of holding one byte for one second — when the store serves $\bar q$
operations per second.

**The reference-rate principle (P-2; D-23).** $J_\beta$ is a function of the
run's *operation-indexed record* $\mathfrak E$: its events, each stamped with
the number of measured-phase operations completed when it happens (defined in
full in Lemma D.17). Wall time enters $J_\beta$ only converted at the
reference rate: an interval in which $\Delta N$ operations are served counts
as $\Delta N/\bar q$ reference seconds, and, for a job, $t^{job}_\iota$
priced device-seconds count as $\bar q\,t^{job}_\iota$ operations. These are
two anchors, not one: space, and the byte part of a job's
interference, use the first; the busy part uses the second, on purpose (D
§1, the charge). Space holds bytes over a time: $\int c_sH\,dt$ becomes
$(c_s/\bar q)\sum_nH(n)$. Interference is the product of two rates, a job's
activity and the foreground's intensity, and its physical form contains the
job's run-dependent duration twice: in the byte rate
$v_\iota = Y_\iota/\Delta t_\iota$ and in the operations $\Delta n_\iota$
served during the job. The charge $I_\iota$ replaces both by the job's priced
device time at the reference rate, and keeps the run only in the cost per
operation $\bar\varrho^{\,x}_\iota$ of each step type (D §1). (Multiplying the physical interference by $q/\bar q$, as the space
*rate* is multiplied, would be wrong: it would rescale the reads' intensity
and leave the job's duration in.) Every other term is a count times a fixed
price. Lemma D.17 proves that $J_\beta$ is then a function of $\mathfrak E$
alone, invariant under any change of wall-clock times that keeps every
event's stamp, and that a job's charge does not depend on how many
operations it overlapped. The physical interference $\mathcal I^{\text{phys}}$
is reported per arm (OBJ-6). The conversion is a convention: $I_\iota$ equals
the physical surcharge only for a job during which the store serves $\bar q$
operations per second and that takes its priced device time.

$J_\beta$ therefore contains no time. Stall time, foreground throughput,
latency and the controller's own overhead are not priced in Programme 1; they
are reported per arm as paired diagnostics (OBJ-6), and pricing them belongs to
Programme 2. The slowdown of priced foreground steps by background jobs is
priced (interference, D-23), but converted by P-2, so it adds no time to $J_\beta$
(Lemma D.17). Because stalls are unpriced, a policy could lower $J_\beta$ by
deferring work until writes stall; the stall rule (Global acceptance) bars any
claim that does. It cannot lower the interference charge that way: a job that
overlaps no operation ($\Delta n_\iota = 0$), such as one that runs only while
the client is blocked in a stalled write, is charged at the read cost of the
operations before it, like a job that overlaps many (Lemma D.17(v)).
Physically such a job slows no read, so here the charge and the physics part
on purpose: the charge prices the compaction work, not the moment it was
done. The drain serves no operations, so it carries no space cost; its jobs
are charged their prices $\tau^{job}_\iota$, bytes written included, and
their interference (post-integration decision, 2026-10-03): a job that runs
only in the drain is charged at $\bar\varrho^{\,x}_\iota$ over the last
$n^{\mathrm{win}}$ operations of the measured phase, and one that began
during `mixgraph` over the last $\max(\Delta n_\iota, n^{\mathrm{win}})$
operations up to its end, by D §1's window. The reason is the drain's own:
it exists so that no deferred work leaves the run uncharged. Work deferred
into the drain would have slowed reads had it run during the operations, so
under P-2 it carries the interference it would have carried at $\bar q$, at
the read cost of the last operations the run served. A controller arm drains
under its fallback settings ($m \equiv 1$, $K_0$ as configured; A-Impl-8),
the configuration it started from, and a static arm under its own
configuration, so every arm drains to the targets it would hold without a
controller and no deferred work leaves the run uncharged. Draining a static
profile at $m \equiv 1$ instead would charge it a compaction its
configuration never performs. The rule errs against the controller, for the
drain's interference as for its bytes: one that ends with anchors above 1
pays for the drain, while a static profile holding the same multipliers does
not.

**Lemma D.1 (the flow form is exact; amended 2026-10-03, D-23).** Let the reward of an interval $[t, t+\Delta t)$ be
$-\int_{[t,t+\Delta t)}C_\beta$ for some weights $\beta$ ($c$ is $C_\beta$ at
$\beta = (1, 1, 1)$, D §2). For any partition of the measured phase into
intervals, the rewards sum to minus the run's priced cost
$$\beta_W\Big[\sum_\iota\tau^{job}_\iota + c_{put}\cdot(\text{Puts}) + \sum_\iota I^{\mathrm{wr}}_\iota\Big] + \beta_R\Big[\sum(\text{quiet read price} \times \text{count}) + \sum_\iota I^{\mathrm{rd}}_\iota\Big] + \beta_S\,(c_s/\bar q)\int H\,q\,dt,$$
the sums over the jobs completing in the phase and over its read steps, the
fixed parts of its Gets and scans included.

*Proof.* Read $q\,dt$ as $dn$, the increment of the count of completed
operations, as §1 does for space. Then every term of $C_\beta$ is a sum of
point charges: a job's $\tau^{job}_\iota$, $I^{\mathrm{wr}}_\iota$ and
$I^{\mathrm{rd}}_\iota$ at its completion, a foreground step's quiet price
(a read step's, a Get's or scan's fixed part, a Put's insert) at the step,
and $(c_s/\bar q)H$ at each operation's completion. Each point charge lies in exactly one interval of the partition,
because the intervals are disjoint and half-open and cover the phase.
$\blacksquare$

*Charging at completion.* A job's $I_\iota$ depends on the operations of its
window $W_\iota$, which ends at the job's end, so it is known only then, and
it enters the interval in which the job completes, not the intervals of the
reads it slowed. Summed over a partition nothing is lost or counted twice. No
job is cut by the phase's ends: the tree is settled at $n_w$ (H §5), and the
drain ends when compaction has caught up.

*Consequence.* No run-to-date ratio enters the reward, so a start-up transient
cannot inflate a multiplier the way it inflated $\lambda_W$ and
$\lambda_{\text{scan}}$ in D-12. The interference charge uses only the
operations of the job's window, at most $\max(\Delta n_\iota, n^{\mathrm{win}})$
of them. The transient still enters the cost itself,
which is why the measured phase starts only after the tree settles (H §5).

*Scope.* Lemma D.1 is about the global cost. The agents of Pathway H are
trained on a different quantity: each level's attributed, priority-weighted
cost divided by $c_wC_i$, plus counterfactual neighbour charges (H §3). Summed
over levels, those rewards do not give minus the priced cost, for four
reasons. Each level is divided by a different constant. Space is charged per
level as shadowed garbage $g_i$ (§4), an estimate that leaves out live bytes,
not as held bytes $H$. The block read of the table where a Get finds its key
goes to a shared bucket no agent is rewarded on (§4), and table reopens of no
known level go to another (§4, D-20, D-21); since D-23 so do the scan-base,
memtable and write-path (flush interference) charges, and the fixed parts
of every operation (the fixed bucket), each in its own
shared bucket (§4). And a neighbour charge $X_{i+1}$ is a signed estimate of
how level $i$'s action, against holding, changes level $i+1$'s future cost;
level $i+1$ pays the realised change again in its own attributed cost, so the
sum carries each action's effect on its neighbours twice — once realised, once
estimated — above the cost when actions burden neighbours and below it when
they relieve them. The identity that can be checked on a run is Proposition
D.16's: the attributed write and read costs, summed over levels and over every
interval of the measured phase, plus the shared buckets (hit-read, reopen,
scan-base, memtable, write-path, fixed), before
normalisation and without neighbour charges, equal the global write and read
costs (OBJ-1, OBJ-4). For interference the identity is with the charge
$\mathcal I$ as defined; how it relates to the physical slowdown is A10's
question and the reference-rate convention's (Lemma D.17(iii)), tested by
OBJ-8 on the physical side and reported by OBJ-6. The agents'
discounted per-level returns are a training surrogate: a joint policy that every
agent's return rates optimal need not minimise $J_\beta$ (H §3), and every run
is judged on $J_\beta$.

**Lemma D.17 (speed neutrality; new 2026-10-03, D-23; re-proved the same day
for the speed-neutral charge, with (i), (iv) and (v) restated).** Number the measured phase's operations
$1, \dots, N$ in the order the client completes them ($n_w$ is operation 1);
the drain follows operation $N$ and serves none. The *stamp* of an event is
the number of measured-phase operations completed when it happens, so an
event of the drain has stamp $N$. A run's *operation-indexed record*
$\mathfrak E$ consists of:

- (R1) for every operation $n$: its type, and for every priced foreground
  step type $x$ (the read steps, the fixed part of a Get or a scan, a Put's
  memtable insert) the number $R_x(n)$ of type-$x$ steps it makes (an
  iteration step's type includes its heap size $r$ and its split
  $r^{\mathrm{L0}}$, $r^{\neg0}$), with each step's level (for a hidden step,
  the level of the hidden entry);
- (R2) for every background job completing in the phase or the drain: its
  kind, its start level, its bytes $S_\iota$, $O_\iota$, $X_\iota$ and
  $Y_\iota$, and the stamps $n^b_\iota \le n^e_\iota$ of its begin and its end;
- (R3) for every version install in the phase or the drain: its stamp and the
  held bytes $H$ after it, installs with equal stamps in the order they
  happen.

The record is the idealised one, every count read as of a completed
operation and every install at its own stamp. What the evaluator reads
differs from it in two bounded ways (D §1): the host log's counters may hold
part of the operation in progress at each job record or snapshot, at most one
operation's steps at each end of a window, and $H$ is sampled just after each
install, not at it. The statements below are about the
idealised record.

A *retiming* of the run is any other assignment of wall-clock times to its
operation completions and its events that gives every event the same stamp
and keeps the order of installs; it leaves $\mathfrak E$ unchanged. Take every
price ($p_{\mathrm{dev}}$ included), every $\kappa$, $\bar q$, $n^{\mathrm{win}}$,
$n^{\mathrm{str}}$ and $\beta$ fixed in advance (A9). Then:

- (i) the integral of $C_\beta$ over the measured phase is
  $$\Phi_\beta(\mathfrak E) = \beta_W\Big[\sum_\iota\tau^{job}_\iota + \sum_{n=1}^{N}\sum_{x\in\mathcal X_{\mathrm{wr}}}c^0_xR_x(n) + \sum_\iota I^{\mathrm{wr}}_\iota\Big] + \beta_R\Big[\sum_{n=1}^{N}\sum_{x\in\mathcal X_{\mathrm{rd}}}c^0_xR_x(n) + \sum_\iota I^{\mathrm{rd}}_\iota\Big] + \beta_S\,\frac{c_s}{\bar q}\sum_{n=1}^{N}H(n),$$
  with $\tau^{job}_\iota$, $I^{\mathrm{wr}}_\iota$ and $I^{\mathrm{rd}}_\iota$ as
  in §1, the read sum covering every priced read step (filter probes, block
  reads, run seeks, reopens, iteration steps at $c_{st}(r)$, iterator blocks,
  memtable searches, the fixed parts of Gets and scans) and the write sum the
  Puts' memtable inserts, and $H(n)$ the held bytes after the last install
  with stamp below $n$ (before any, the held bytes at $n_w$). $\Phi_\beta$ is a
  function of $\mathfrak E$ alone, and $J_\beta = \mathbb E\,\Phi_\beta(\mathfrak E)$;
- (ii) $\Phi_\beta$ is invariant under every retiming;
- (iii) the physical surcharge A10 gives for job $\iota$,
  $I^{\text{phys}}_\iota = \sum_x\big(\kappa^J_{x,\mathrm{kind}(\iota)} + \kappa^B_xv_\iota\big)\,\mathrm{ReadCost}^0_x(n^b_\iota, n^e_\iota]$,
  relates to the charge as follows. If $\Delta n_\iota \ge n^{\mathrm{win}}$,
  so that $W_\iota$ is the job's own operations, and $\Delta t_\iota > 0$, the
  byte part $I^B_\iota$ is the physical byte part times $\bar q/q_\iota$, and
  the busy part $I^J_\iota$ is the physical busy part times
  $(\bar q/q_\iota)(t^{job}_\iota/\Delta t_\iota)$. If $\Delta n_\iota = 0$,
  $I^{\text{phys}}_\iota = 0$, while $I_\iota$ is the charge at the read cost
  of the window. $I^{\text{phys}}_\iota$ is not invariant: a retiming that
  multiplies $\Delta t_\iota$ by $\lambda$ multiplies its byte part by
  $1/\lambda$. A space charge of $c_sH$ per second is not invariant either;
- (iv) the terms of $\Phi_\beta$ that needed a conversion are exactly those
  whose physical form depends on how fast the run goes — space (bytes held
  over a time) and interference (a job's byte rate, and the operations served
  during it) — and each is converted with one of two anchors, both free of
  wall time: space and the byte part of interference with the *operation
  anchor*, an interval in which $\Delta N$ operations are served counting as
  $\Delta N/\bar q$ reference seconds; the busy part of interference with the
  *priced-time anchor*, a job's priced device time $t^{job}_\iota$ counting as
  $\bar q\,t^{job}_\iota$ operations. For a job whose window is its own
  operations the two anchors give the same byte part, while the operation
  anchor would leave the busy part at its physical form, which vanishes in a
  stall. (Prices are device times too, but constants fixed in
  advance, not durations of the run.)
- (v) *relative-speed neutrality*: given a job's kind, bytes and window costs
  $\bar\varrho^{\,x}_\iota$, its charge $I_\iota$ depends on neither
  $\Delta t_\iota$ nor $\Delta n_\iota$. In particular, fix a job's kind and
  bytes and condition on its window $W_\iota$: if every operation in
  $W_\iota$ has the same conditional expected quiet cost $\varrho^x$ of each
  step type $x$, then
  $\mathbb E[I_\iota\mid W_\iota] = \bar q\sum_x\varrho^x(\kappa^B_xY_\iota + \kappa^J_{x,\mathrm{kind}(\iota)}t^{job}_\iota)$,
  the same whether the job overlapped many operations, few, or none (in a
  stall or the drain). Without the conditioning this can fail: the window's
  length is random and can be correlated with the operations' costs (slow
  operations mean fewer of them in a job's span), and the mean over a window
  of random length need not have the operations' common mean.

*Proof.* (i) Term by term, reading $q\,dt$ as $dn$, the increment of the
number of completed operations, as §1 already does for space.

- *Jobs.* $\dot w$, $\dot w_r$ and $\dot\Xi^{\mathrm{kind}}$ are rates of
  counting processes that jump, at each job's completion, by $X_\iota$, by
  $S_\iota + O_\iota$ and (for the job's kind) by 1. Their priced integral is
  $\sum_\iota\tau^{job}_\iota$ over the jobs completing in the window, the
  drain's included. Each $\tau^{job}_\iota$ — its per-job price
  $c^{\mathrm{kind}(\iota)}_{job}$ and its byte terms — is read from (R2).
- *Foreground steps.* As measures on the time axis, the terms of $C_\beta$
  other than the jobs', the interference and space put a point charge
  $c^0_x$ at every type-$x$ step: filter probes, block reads, run seeks,
  reopens, memtable searches, the fixed parts of Gets and scans and, for
  scans, the iteration steps and iterator blocks, in the read bracket; the
  Puts' memtable inserts, at $c_{put}$, in the write bracket. By the
  definition of $\bar c_{st}$ as the step-weighted mean of $c_{st}(r)$, the
  iteration term integrates to $\sum_{\text{steps}}c_{st}(r)$. The integrals
  are $\sum_n\sum_{x\in\mathcal X_{\mathrm{rd}}}c^0_xR_x(n)$ and
  $\sum_n\sum_{x\in\mathcal X_{\mathrm{wr}}}c^0_xR_x(n)$, read from (R1).
- *Interference.* $\dot{\mathcal I}^{\mathrm{rd}}$ and
  $\dot{\mathcal I}^{\mathrm{wr}}$ jump by $I^{\mathrm{rd}}_\iota$ and
  $I^{\mathrm{wr}}_\iota$ at job $\iota$'s completion, so they integrate to
  $\sum_\iota I^{\mathrm{rd}}_\iota$ and $\sum_\iota I^{\mathrm{wr}}_\iota$.
  Each part is read from (R1) and (R2): $t^{job}_\iota$ from the kind and the
  bytes at fixed prices; the window $W_\iota$ from $n^b_\iota$, $n^e_\iota$
  and the constants $n^{\mathrm{win}}$ and $n^{\mathrm{str}}$; and
  $\bar\varrho^{\,x}_\iota = c^0_x\sum_{n\in W_\iota}R_x(n)/|W_\iota|$ from
  (R1). $\kappa$, $\bar q$ and $Y_\iota$ are constants or in (R2). Both the
  read and the write part are therefore functions of $\mathfrak E$. The window is never empty for a
  job that completes after operation 1, and no job completes before it in a
  settled run (H §5); a job of the drain has stamp $n^e_\iota = N$, so its
  window is the last $\max(\Delta n_\iota, n^{\mathrm{win}})$ operations of
  the phase, also in the record. Concurrent jobs each use their own window.
- *Space.* $H$ is constant between installs, so
  $\int c_sH\,(q/\bar q)\,dt = (c_s/\bar q)\int H\,dn = (c_s/\bar q)\sum_{n=1}^{N}H(t_n)$,
  with $t_n$ the time operation $n$ completes. An install with stamp $s$
  happens after operation $s$ completes and before operation $s+1$ does, so
  the installs before $t_n$ are exactly those with stamp below $n$, and
  $H(t_n) = H(n)$, read from (R3). The drain adds nothing, since $dn = 0$
  there.

Adding the parts with their weights gives $\Phi_\beta(\mathfrak E)$; the
prices, $\kappa$, $\bar q$, $n^{\mathrm{win}}$, $n^{\mathrm{str}}$ and $\beta$
are its only other arguments, and they are constants. $J_\beta$ is its expectation, over seeds
and over the variation of the record between runs at one seed (background
threads do not run alike twice, D-22).

(ii) A retiming leaves $\mathfrak E$ unchanged, and $\Phi_\beta$ has no other
argument that a retiming can change.

(iii) If $\Delta n_\iota \ge n^{\mathrm{win}}$ then $W_\iota = (n^b_\iota,
n^e_\iota]$, so $\mathrm{ReadCost}^0_x(n^b_\iota, n^e_\iota] =
\Delta n_\iota\,\bar\varrho^{\,x}_\iota$. The physical byte part is then
$\sum_x\kappa^B_xv_\iota\Delta n_\iota\bar\varrho^{\,x}_\iota =
q_\iota Y_\iota\sum_x\kappa^B_x\bar\varrho^{\,x}_\iota$, since $v_\iota\Delta
n_\iota = Y_\iota\Delta n_\iota/\Delta t_\iota = q_\iota Y_\iota$; against
$I^B_\iota = \bar qY_\iota\sum_x\kappa^B_x\bar\varrho^{\,x}_\iota$ that is the
factor $\bar q/q_\iota$. The physical busy part is
$\sum_x\kappa^J_{x,\mathrm{kind}(\iota)}\Delta n_\iota\bar\varrho^{\,x}_\iota
= q_\iota\Delta t_\iota\sum_x\kappa^J_{x,\mathrm{kind}(\iota)}\bar\varrho^{\,x}_\iota$;
against $I^J_\iota = \bar q\,t^{job}_\iota\sum_x\kappa^J_{x,\mathrm{kind}(\iota)}\bar\varrho^{\,x}_\iota$
that is the factor $(\bar q/q_\iota)(t^{job}_\iota/\Delta t_\iota)$. If
$\Delta n_\iota = 0$ the physical window $(n^b_\iota, n^e_\iota]$ is empty and
$I^{\text{phys}}_\iota = 0$. A retiming changes $\Delta t_\iota$ but not
$\Delta n_\iota$, $Y_\iota$ or any window, so it changes $q_\iota$ and
$v_\iota$; multiplying $\Delta t_\iota$ by $\lambda$ divides $v_\iota$, and
with it the physical byte part, by $\lambda$. For space, $c_s\int H\,dt$ is
the sum, over the intervals between installs, of $H$ times the interval's
wall length, which a retiming changes while (R3) stays fixed.

(iv) From (i): $\tau^{job}_\iota$ and the foreground step costs are prices
times counts in $\mathfrak E$, and contain no duration and no rate. The
physical space cost, $c_s\int H\,dt$, integrates over wall time; replacing
$dt$ by $dn/\bar q$ gives $(c_s/\bar q)\sum_nH(n)$. The physical interference
of a job is, by (iii)'s computation,
$\sum_x\bar\varrho^{\,x}\,\Delta n_\iota(\kappa^B_xY_\iota/\Delta t_\iota +
\kappa^J_{x,\mathrm{kind}(\iota)})$, with $\bar\varrho^{\,x}$ over its own
operations: it contains the wall span $\Delta t_\iota$ and the count
$\Delta n_\iota$, which is the job's duration measured in operations at the
run's own relative speed. The operation anchor replaces $\Delta t_\iota$ by
$\Delta n_\iota/\bar q$: the byte part becomes
$\bar qY_\iota\sum_x\kappa^B_x\bar\varrho^{\,x} = I^B_\iota$, and the busy part
is left at $\Delta n_\iota\sum_x\kappa^J_{x,\mathrm{kind}(\iota)}\bar\varrho^{\,x}$,
still a count of the run. The priced-time anchor replaces $\Delta n_\iota$ by
$\bar q\,t^{job}_\iota$ (and $\Delta t_\iota$ by $t^{job}_\iota$), which gives
the same byte part and the busy part $I^J_\iota$. With the own-operations
mean replaced by the window mean, the result is $I_\iota$.

(v) In the formula for $I_\iota$, $\Delta n_\iota$ enters only through
$W_\iota$, and $\Delta t_\iota$ not at all. Given $W_\iota$,
$\bar\varrho^{\,x}_\iota = |W_\iota|^{-1}\sum_{n\in W_\iota}c^0_xR_x(n)$ is a
mean over a fixed set of operations, so its conditional expectation is
$|W_\iota|^{-1}\sum_{n\in W_\iota}\mathbb E[c^0_xR_x(n)\mid W_\iota] = \varrho^x$
by the assumption, whatever $|W_\iota|$ is; $Y_\iota$ and $t^{job}_\iota$ are
fixed by the kind and the bytes, and $I_\iota$ is linear in the
$\bar\varrho^{\,x}_\iota$. $\blacksquare$

*What "invariant" covers.* A retiming changes wall-clock times only. Any
slowdown or speed-up that stretches every operation and every job alike, so
that each job still overlaps the same operations, is one, and leaves
$J_\beta$ unchanged. A change in the *relative* speed of jobs and operations
is not a retiming: it changes which operations each job overlaps, so it
changes $\mathfrak E$ and $J_\beta$, as it changes the physical cost. By (v)
it moves a job's charge only through the read cost per operation of the
operations its window holds, never through how many operations the job
overlapped. Had the busy part counted the read cost of the operations
served during the job, a foreground slowed relative to its jobs would pay
less, and a job run in a stall nothing; the integration decision of
2026-10-03 rules both out.

*Why per operation for interference too.* This is the mirror image of Lemma
D.15's argument for space. Under the physical form, a retiming that
stretches every duration by $\lambda > 1$ leaves every other term of
$\Phi_\beta$ unchanged and divides the physical byte part of interference by $\lambda$:
$J_\beta$ would fall when the run slows down, the same compaction being
spread thinner in time over the same reads. A space charge per second rises
by the factor $\lambda$ under the same stretch. Either form would reward or
penalise elapsed time, which Programme 1 does not price (OBJ-6, the stall
rule). The busy part needs the conversion for the same reason with the
foreground's speed in place of the clock: counted over the operations served
during the job, it would fall whenever the client is slowed or blocked while
the job runs.

*A convention, not device time.* $I_\iota$ equals A10's physical surcharge
only for a job during which the store serves $\bar q$ operations per second
and that takes its priced device time. In the critique's mean-field estimate
on Gate N1's pilots (Q1; provisional prices, reopens left out, byte part
only) the converted charge is about 1.00 times the physical one at T = 10 and
1.77 (`Assoc`) or 1.87 (power law) times at T = 2: the ratio $\bar q/q$, mean
of three repeats (`db/n1-assoc`, `db/n1-powerlaw`); the T = 2 runs took 1.78
and 1.85 times as long as T = 10's (means of the wall-time ratios), a
different quantity; the busy part carries the further factor
$t^{job}_\iota/\Delta t_\iota$, whose sum over the jobs OBJ-7 checks; and a
job with $\Delta n_\iota = 0$ is charged although it slowed no read. Space's
conversion also has a cancellation behind it (Lemma D.15: live bytes are the
same under every policy, so policies differ only through garbage).
Interference has none: both of its factors, compaction work and read
intensity, depend on the policy. Its conversion rests on (ii) and (v) alone.
$\mathcal I^{\text{phys}}$ is reported per arm (OBJ-6), and A10 is tested on
the physical side (OBJ-8).

*Stalls, the drain and concurrent jobs.* A job that overlaps no operation
($\Delta n_\iota = 0$) — one that runs only while the client is blocked in a
stalled write, or only during the drain — is charged at the read cost of the
last $n^{\mathrm{win}}$ operations before its end (v), as if it had run at the
reference rate. Moving compaction into stalls or into the drain therefore
does not lower its interference charge; for the drain this is by design (D
§1: work deferred into it would have slowed reads had it run during the
operations, and it errs against the controller, as the drain's bytes do).
Deferring work into stalls can still lower $J_\beta$ by other routes, since
stall time is unpriced, and the stall rule (Global acceptance) bars any claim
that does; OBJ-6 reports the share of compaction bytes in jobs with
$\Delta n_\iota = 0$, stalls and drain apart. A job that runs across the
end of `mixgraph` is charged over the last $\max(\Delta n_\iota,
n^{\mathrm{win}})$ operations up to $N$. Concurrent jobs each use their own
window.

### §2 Priority modes

**Definition (priced cost with priority; amended 2026-10-03, D-23).** For weights $\beta = (\beta_W, \beta_R, \beta_S) > 0$:
$$C_\beta(t) = \beta_W\Big[c_w\dot w + c_{cr}\dot w_r + \sum_{\mathrm{kind}}c^{\mathrm{kind}}_{job}\dot\Xi^{\mathrm{kind}} + c_{put}q_{put} + \dot{\mathcal I}^{\mathrm{wr}}\Big] + \beta_R\Big[q_{pt}(c^0_{get} + c_fR_f + c_{blk}R_{blk}) + q_{sc}\big(c^0_{sc} + c_{sk}R_{sk} + \bar c_{st}(R_{nx} + R_{hd}) + c_{ib}R_{ib}\big) + c_{open}\dot o + c_{mt}(q_{pt} + q_{sc}) + \dot{\mathcal I}^{\mathrm{rd}}\Big] + \beta_S\,c_sH\,\frac{q}{\bar q},$$
with every symbol as in §1: $\bar c_{st}$ is the step-weighted mean of
$c_{st}(r)$, so the iteration term is exactly the rate of
$\sum_{\text{steps}}c_{st}(r)$, and $\dot{\mathcal I}^{\mathrm{wr}}$ and
$\dot{\mathcal I}^{\mathrm{rd}}$ are the rates of the two parts of the
interference charge, each $I_\iota$ charged at its job's completion, so Lemma
D.1 stays an exact identity. $J_\beta(\pi) = \mathbb{E}\int C_\beta\,dt$ over the
measured phase, the expectation over seeds and over the variation of a run's
record at one seed; by Lemma D.17, $J_\beta(\pi) = \mathbb E\,\Phi_\beta(\mathfrak E)$,
with no time in it. Write $\mathcal C_W(\pi)$, $\mathcal C_R(\pi)$,
$\mathcal C_S(\pi)$ for the three unweighted priced run costs, so
$J_\beta = \beta_W \mathcal C_W + \beta_R \mathcal C_R + \beta_S \mathcal C_S$:

- $\mathcal C_W$: bytes written, compaction bytes read and jobs by kind,
  $\sum_\iota\tau^{job}_\iota$; also the Puts' memtable inserts and the
  write part of interference,
  $\mathcal I^{\mathrm{wr}}$;
- $\mathcal C_R$: the four 2026-10-02 read terms (filter probes, block reads,
  run seeks, reopens), plus scan iteration (steps and iterator blocks), the
  memtable search, the fixed parts of Gets and scans, and the read part of
  interference, $\mathcal I^{\mathrm{rd}}$;
- $\mathcal C_S$: space, unchanged.

Each is money (§1: every term of $C_\beta$ is money per second).

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

**Proposition D.3 (exchange rate; amended 2026-10-03, D-23).** Suppose that
near an optimum the achievable operating points form a smooth curve along
which only the prioritised cost $\mathcal C_P$ and one other cost
$\mathcal C_X$ change. At an interior minimiser of $J_\beta$,
$-d\mathcal C_P/d\mathcal C_X = 1/\beta^\star$. If moreover, along the curve,
each of the two costs is one fixed price times one metric,
$\mathcal C_P = p_P\,P$ and $\mathcal C_X = p_X\,X$, then in metric units
$-dP/dX = p_X/(\beta^\star p_P)$.

*Proof.* First-order condition along the curve: $\beta^\star\,d\mathcal C_P + d\mathcal C_X = 0$.
Under the extra condition, $d\mathcal C_P = p_P\,dP$ and
$d\mathcal C_X = p_X\,dX$. $\blacksquare$

*Amendment.* The 2026-09-29 statement gave the metric form without the extra
condition, which held tacitly while each cost was one price times one metric.
Since D-23, $\mathcal C_W$ has three metrics ($W$, $W_r$ and the job counts)
and the write part of interference, and $\mathcal C_R$ the read part; both
parts move with compaction volume rather than with any read or write count.
Along a curve on which compaction changes — the $K_0$ trade of Proposition
D.11, for one — $\mathcal C_R$ moves through probes and through interference
at once, and $\mathcal C_W$ through bytes, jobs and the write part, so the
metric form needs the condition; the currency form does not.

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
10\}$ reported. Since D-23, $\mathcal C_R$ contains the read part of
interference, so strict read priority minimises probes, seeks, iteration *and*
the slowdown compaction causes to reads: it prefers less compaction wherever compaction slows reads by more
than it saves them, and no longer means "compact as eagerly as the class
allows".

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
(Proposition C.4); with exact ties a second minimiser can lie on a face
between vertices, but a vertex minimiser always exists. A trade that lies in a
dent of the frontier is reachable by no choice of weights.

**Remark D.6 (the actual min–max form; amended 2026-10-03).** Minimising the worst weighted gap to a
reference point, $\max_X \beta_X(\mathcal C_X - \mathcal C_X^{\text{ref}})$ (the weighted Chebyshev
form), with the *utopian* point as reference (the ideal point, the best
achievable value of each cost separately, less a small margin), reaches every
Pareto-optimal point for suitable weights, including points in dents; in
general its minimisers are only weakly Pareto optimal, which the augmented
form repairs [Miettinen, Thms 3.4.2 and 3.4.5]. It needs the ideal point, which a cold-started controller does not
have. Programme
1 therefore uses the weighted form. The Chebyshev form may be used offline to
select among measured configurations.

**D.2–D.6 with the new terms (2026-10-03, D-23).** The new terms sit inside
$\mathcal C_W$ (job prices, compaction reads, the Puts' memtable inserts, the
write part of interference) and $\mathcal C_R$ (iteration, memtable search,
the fixed parts of Gets and scans, the read part of interference); $\mathcal C_S$ is unchanged, and $J_\beta$ is
still $\beta_W\mathcal C_W + \beta_R\mathcal C_R + \beta_S\mathcal C_S$ with
fixed positive weights. Each result is re-checked against what its proof uses:

- **D.2** uses only that linear form. It holds as stated. Two readings
  change. A read-cost decrease can now come from compacting less, or at lower
  read intensity, since interference is in $\mathcal C_R$, and a write-cost
  decrease too, through the write part. And a term fixed by the operation
  sequence — the memtable search, $c_{mt}$ per Get and scan, the fixed parts
  $c^0_{get}$ and $c^0_{sc}$, the Puts' inserts at $c_{put}$, and the base
  price $c_{st}(1)$ of each returned entry's step (Lemma D.19) — is the same
  for $\pi$ and $\pi_0$ and cancels from $G_P$ exactly.
- **D.3** uses the first-order condition along a curve. The currency form
  holds; the metric form needs the condition now stated in it.
- **D.4** uses only finitely many points with finite costs and the linear
  form. It holds; with the read part of interference in $\mathcal C_R$,
  strict read priority also weighs compaction's slowdown of reads (its *Use*
  paragraph).
- **D.5** uses linearity of $J_\beta$ in the cost vector and that each point
  of $A$ has a well-defined vector. Linearity holds. Each configuration's
  expected vector is well defined: by Lemma D.17 it is the mean of a function
  of the run's record, with every price, $\kappa$ and $\bar q$ fixed in advance
  (A9). A session that is slower in every respect at once leaves it
  unchanged; one whose machine changes the relative speed of jobs and
  operations changes the record, and the vector with it, as for every term
  before D-23, which is why comparisons stay within one session (CMP-8). It
  holds.
- **D.6** concerns the form of the objective, not its terms; unaffected.

Two consequences hold for all five. A term that is the same in every arm of a
workload changes $J_\beta$'s level but no comparison between arms; it does
change ratios, such as the regret of Definition C.5. And the content of the
costs changes the static optima of §3 (re-derived there), not the logic of
this section.

### §3 Cost model in the actual knobs

Levels $0..L$; knobs $K_0$, $m_1..m_L$, and depth. The model explains and
bounds; the attributed costs in the reward are always measured (only the
neighbour charge of H §3 uses a one-step model prediction and learned values).

**What the 2026-10-03 amendment adds (D-23).** The cost of D §1–§2 now also
prices the device time of background jobs beyond their written bytes (a
per-job price for each job kind, and compaction bytes read at $c_{cr}$: Lemma
D.18), scan iteration (Lemmas D.10 and D.19), memtable search ($c_{mt}$),
the fixed parts of Gets and scans and the Puts' memtable inserts, and the
slowdown that background jobs impose on foreground steps
(interference, A10, charged at the reference rate, D §1; its write part, on
Put inserts, enters the analytic results as D §1's remark says). Every result
of this section that these terms change is re-derived below and marked
"amended 2026-10-03, D-23". Two conventions hold throughout:

- *Per operation.* Every cost is per operation served. Where a result uses a
  rate, it is a per-operation quantity times $\bar q$ (Lemma D.17): user bytes
  per operation times $\bar q$ for $u$, Gets and scans per operation times
  $\bar q$ for $q_{pt}$ and $q_{sc}$. "Fixed rates" therefore means a fixed
  operation mix, which A8 provides: every arm serves the same operations. The
  store's speed enters the quiet read cost only through which version each
  read meets (how many operations an L0 merge spans, for instance), and the
  interference charge only through which operations each job's window holds
  (Lemma D.17(v)); the charge counts each job's priced device time, not the
  operations it overlapped.
- *Measured correlation, not mean field.* Interference is charged at the
  cost per operation over each job's window (D §1). The analytic results use
  the overlap pattern measured on the seven native runs of §0.7 (both
  workloads, $T$ = 2, 6, 10, $K_0$ = 4, repeat 1; the same in all 23 native
  runs of those cells): every L0→L1 merge ran with $k_0 = K_0$ (4.000), and
  the merges sourced at a deeper level ran with L0 empty *on average* (mean
  $k_0$ at most 0.13 per start level), while up to 5.4% of them (5.8% over
  all repeats) ran with one L0 file (§0.7). The analytic
  results idealise this as "every deeper job runs with L0 empty" (Proposition
  D.11's (b3)) and say so; D.11 bounds what the exposure to $k_0 = 1$ adds. The pattern reaches the charge when each job's window sees
  the L0 count in force during the job, which a window no longer than the job
  guarantees (Proposition D.11, (d2)). A result that uses the time-average
  instead (mean field) says so. *Derived* from the pattern (critique Q2,
  model M2, provisional prices): mean field gets the total
  within 2% but makes the interference grow with $K_0$ about 2.8 times too
  steeply, and gives every level the same per-byte charge where L0 bytes pay
  1.24–1.94 times what deeper bytes pay.

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

**Lemma D.18 (job and read-byte identities; new 2026-10-03, D-23).** Fix a
window, let $U$ be the user bytes written in it, and count every job at its
completion (D §1). For each level $i$, let $n^m_i$ and $n^{tm}_i$ be the
merges and trivial moves sourced at level $i$ that complete in the window
(intra-L0 and last-level self-compactions are listed separately, as in Lemma
D.7), $M_i$ the merges' source bytes, $O^{tot}_i$ the overlap bytes they read
from level $i+1$, and $T_i$ the trivially moved bytes. Let $n_F$ be the
flushes and $X_F$ their output bytes. As in Lemma D.7, $a_i - t_i = M_i/U$,
$t_i = T_i/U$, $a_F = X_F/U$, and $o_i = O^{tot}_i/M_i$ is pooled over the
window's merges. The mean job sizes are $\bar s_i = M_i/n^m_i$,
$\bar s^{tm}_i = T_i/n^{tm}_i$ and $\bar s_F = X_F/n_F$; a term whose job count
is zero is zero. Then:

- (i) *Read bytes.* Compaction bytes read per user byte are
  $$W_r = \sum_{i=0}^{L-1}(a_i - t_i)(1 + o_i),$$
  plus, listed separately, the input bytes of intra-L0 and last-level
  self-compactions.
- (ii) *Job counts.* Per user byte, level $i$ completes $(a_i - t_i)/\bar s_i$
  merges and $t_i/\bar s^{tm}_i$ trivial moves, and there are $a_F/\bar s_F$
  flushes. By kind: $\varpi^F = a_F/\bar s_F$; $\varpi^0 = (a_0 - t_0)/\bar s_0$
  plus the intra-L0 compactions; $\varpi^d = \sum_{i\ge1}(a_i - t_i)/\bar s_i$
  plus the last-level self-compactions; $\varpi^{tm} = \sum_i t_i/\bar s^{tm}_i$.
- (iii) *Write cost of the jobs.* The job part of the write cost (D §1) is
  $\mathcal C^{job}_W = U\big[c_wW + c_{cr}W_r +
  \sum_{\mathrm{kind}}c^{\mathrm{kind}}_{job}\varpi^{\mathrm{kind}}\big]$; the
  whole write cost is $\mathcal C_W = \mathcal C^{job}_W + c_{put}\cdot(\text{Puts}) + \mathcal I^{\mathrm{wr}}$.
  Level $i$'s merges and trivial moves contribute
  $$U\Big[(a_i - t_i)\Big(c_w(\rho_i + o_i) + c_{cr}(1 + o_i) + \frac{c^{\langle i\rangle}_{job}}{\bar s_i}\Big) + t_i\,\frac{c^{tm}_{job}}{\bar s^{tm}_i}\Big],$$
  with $c^{\langle i\rangle}_{job}$ the per-job price of a merge sourced at
  level $i$ ($c^0_{job}$ for $i = 0$, $c^d_{job}$ otherwise), and the flushes
  $U a_F\,(c_w + c^{F}_{job}/\bar s_F)$.
- (iv) *Steady state.* Under A7, $a_{i+1} = (a_i - t_i)\rho_i + t_i$ (Lemma
  D.7). Suppose moreover that no intra-L0 compaction runs, no L0 file moves
  trivially, every flush writes one file of at most $C_1/K_0$ bytes, and
  every L0 merge takes every file L0 holds when it starts. Then
  $\varpi^0 = \varpi^F/\bar k^m_0$, where $\bar k^m_0$, the mean number of
  files per L0 merge, is at least the mean trigger in force at the merges'
  starts. If every L0 merge starts exactly at its trigger $K_0$, as measured
  at the default point ($k_0$ = 4.000 in every L0 job of the seven native runs
  of §0.7, and of all 23 runs of their cells), then $\bar s_0 = K_0\bar s_F$ and $\varpi^0 = a_F/(K_0\bar s_F)$.
  These two relations hold up to one L0 merge at each end of the window.

*Proof.* (i) A merge reads its source and its overlap, $S + O$ bytes. Summed
over level $i$'s merges this is $M_i + O^{tot}_i = M_i(1 + o_i)$, by the
definition of $o_i$. A trivial move reads nothing: it relinks its file without
reading it (Lemma D.7). A flush reads no table file. Divide by $U$.
(ii) By the definitions of the mean sizes, $n^m_i = M_i/\bar s_i$,
$n^{tm}_i = T_i/\bar s^{tm}_i$ and $n_F = X_F/\bar s_F$; divide by $U$.
(iii) $\mathcal C^{job}_W$ is the sum of $\tau^{job}_\iota$ over the jobs that
complete in the window (D §1). By the definition of $\tau^{job}_\iota$, that
sum is $c_w$ times all bytes written, plus $c_{cr}$ times all compaction bytes
read, plus $\sum_{\mathrm{kind}}c^{\mathrm{kind}}_{job}\Xi^{\mathrm{kind}}$.
Lemma D.7 gives the bytes written per level, (i) the
bytes read and (ii) the counts. (iv) The recursion is Lemma D.7's. Under the
extra conditions every flushed file leaves L0 in exactly one L0 merge. Over a
window that starts and ends at an L0 install, the files leaving L0 are the
files flushed into it, so $n^m_0\bar k^m_0 = n_F$; a general window differs
by the files L0 holds at its two ends. A merge starts only when L0's score is
at least 1 (A3′). When it starts no L0 file is being compacted, since the one
compaction slot (G.4) runs one job at a time and no intra-L0 compaction runs,
so the score counts all $k_0$ files (A3′). Its byte branch fires with fewer
than $K_0$ files only if those files average more than $C_1/K_0$ bytes, and
none is that large, so $k_0$ is at least the trigger at every start.
$\blacksquare$

*What the identities say.* Every term is a measured count times a price, so
the job part of the write cost needs no model beyond Lemma D.7 (the Put
inserts are a count fixed by the operation sequence, and the write part of
interference is D §1's charge). Only the recursion needs A7.
The count of merges per user byte is set by the bytes that flow (Lemma D.7)
and by the mean job size, which the picker sets. A merge out of a level
$\ge 1$ takes one source file of about $F_{\text{sst}}$ bytes and the files it
overlaps below; an L0 merge takes the L0 files. Under Corollary D.9's
assumptions ($\rho_i = 1$, $a_i = 1$, $t_i = 0$),
$W_r = L + c\sum_if_i = W - 1$, and level $i$ completes $1/\bar s_i$ merges per
user byte.

*Magnitudes (measured: operator diagnostics of 2026-10-03, §0.7, reproduced
in the check reports).* Each job's host-log span exceeds its
counted `compaction_time_micros` by a median 2.7–3.5 ms (L0 jobs 4.5–5.3 ms,
deeper jobs 2.7–3.5 ms), nearly independent of job size; the gap holds the
part of the job outside `RunSubcompactions` (§0.7). On `Assoc` at $T$ = 2 these
gaps total 45.7 s against 35.8 s of counted compaction time. Trivial moves
write nothing, yet the per-run median move takes 2.5–3.3 ms: `Assoc` at $T$ = 2 made 4,151
(13.8 s). The per-job price is fixed per kind (A9). How it depends on the
configuration (median gaps of 3.4–3.5 ms at $T$ = 2 against 2.7–3.0 ms at
$T$ = 6 and 10) is guarded by the per-run job check (A11), not modelled.

**Lemma D.8 (overlap with uniformly spread keys).** If keys are spread uniformly
within each level and a compaction's source range covers a fraction $x$ of level
$i$'s key space, then $o_i = B_{i+1}/B_i$ at that moment. With both levels at
their effective targets, $o_i = f_i$. For L0 → L1 with L0 files spanning the key
range, $o_0 = B_1/(k_0F) = m_1C_1/(K_0F) = f_0$ at the trigger.

*Proof.* The source holds $xB_i$ bytes and overlaps $xB_{i+1}$ bytes below.
$\blacksquare$

*The overlap constant.* Define $c_i = o_i/f_i$, measured per level. RocksDB's
`kMinOverlappingRatio` picks the file with the least overlap with the next
level (Sarkar et al.'s "least overlap with parent", LO+1 [Sarkar et al.]), so
at or below the level's average overlap, and $c_i \le 1$ is expected (the
inference is this document's), and skewed keys lower it further. The conventions of the 2026-09-11
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

**Lemma D.10 (read terms; amended 2026-10-03, D-23).** Runs are searched
newest to oldest (L0 files, then L1 to L), after the memtables. A Get answered
from the memtables probes no table, so $R_f = R_{blk} = 0$. Otherwise consider
a Get whose newest version is in the $n$-th table run whose key range covers
the key (a hit), or that finds no version in any table (a miss), and let
$\varepsilon$ be the probability that a covering run which does not hold the
key passes its filter. Then:

- $R_f = n$ for a hit, and the number of covering runs for a miss;
- $\mathbb{E}[R_{blk}] = \mathbf 1_{\text{hit}} + \varepsilon\,(R_f - \mathbf 1_{\text{hit}})$;
- for a scan, $R_{sk} = k_0 + $ (number of levels $1..L$ with a file at or
  after the seek key); a `DBIter` reseek, after more than
  `max_sequential_skip_in_iterations` hidden versions of one key, counts them
  again. Reseeks are rare on uniform keys. Hidden versions concentrate on hot
  keys (Lemma D.19), so each run checks them with
  `rocksdb.number.reseeks.iteration`;
- every Get and every scan searches the memtables once, whatever the tables
  hold, so there are $q_{pt} + q_{sc}$ memtable searches per second, priced
  at $c_{mt}$ each;
- after its seeks, a scan steps past $R_{nx}$ entries, one per `Next` call,
  and over $R_{hd}$ hidden internal entries: older versions of the keys it
  steps past (Lemma D.19). Each of these $R_{nx} + R_{hd}$ internal steps
  advances the merging iterator once, up to one step per reseek. A step's
  price $c_{st}(r)$ depends on the number $r$ of children in the merging
  iterator's heap when the step is taken: the memtables, the L0 files and the
  level iterators that are not yet exhausted, $r^{\mathrm{L0}}$ of them L0
  files and $r^{\neg0}$ the others;
- the scan's table children load $R_{ib}$ data blocks after their seeks,
  counted logically, cache hits included, as $R_{blk}$ is. A level iterator
  that moves to its next file loads that file's first block, which counts
  here and not in $R_{sk}$.

A scan's priced cost is therefore
$$c_{sk}R_{sk} + \sum_{\text{steps } e}c_{st}(r_e) + c_{ib}R_{ib} + c_{mt},$$
with $r_e$ the heap size at step $e$. Writing $\bar c_{st}$ for the
step-weighted mean $\sum_ec_{st}(r_e)/(R_{nx} + R_{hd})$, the sum is
$\bar c_{st}(R_{nx} + R_{hd})$ exactly. $c_{st}$ is nondecreasing in $r$. Its
form is fixed by calibration (D-23; roughly affine in $\log_2 r$, the heap's
depth, is expected), and no result below assumes it convex in $r$.

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
file does not tick again). The remaining items follow from the iterator's
construction, cited at `8e903efd9` (these files are the same at
`25468bbaa`). A user iterator is a `DBIter` over a
merging iterator whose children are the mutable memtable, each immutable
memtable, one table iterator per L0 file and one level iterator per non-empty
level $\ge 1$ (`DBImpl::NewInternalIterator`, `Version::AddIteratorsForLevel`).
`MergingIterator::Next` advances the child at the top of its min-heap and
restores the heap with `replace_top`, or with `pop` when that child is
exhausted (`table/merging_iterator.cc`). `DBIter::Next` advances it once past
the entry it returned. `FindNextUserEntryInternal` then advances it once past
every internal entry it examines and does not return. Every examined entry is
counted (`TooManyInternalKeysSkipped`), and the examined entries less the
returned ones are added to `rocksdb.number.iter.skip`, which therefore counts
$R_{hd}$ (`db/db_iter.cc`, `db/db_iter.h`). After more than
`max_sequential_skip_in_iterations` hidden versions of one key the iterator
reseeks instead of stepping: the versions it jumps over are neither examined
nor counted, and the reseek seeks every child again. A data block is loaded
when a table child's position enters a block the scan has not yet loaded.
`mixgraph` draws a scan's number of `Next` calls from the operation's own
random number (`ParetoCdfInversion(u, iter_theta, …) % mix_max_scan_len`,
`tools/db_bench_tool.cc`), and each call returns one entry. $\blacksquare$

*The step price is a calibrated mean.* `replace_top` costs one comparison when
the advanced child stays on top and the heap's root-comparison cache is valid
(the previous step also left the root in place), two when it stays on top
otherwise, and up to about $2\log_2 r$ when it sinks (`util/heap.h`).
So the cost of a step also depends on how the children's keys interleave, and
$c_{st}(r)$ is the mean at its calibration trees (D-23). Its prices come from
price trees run with `seek_nexts` > 0, at several depths and garbage levels,
and at more than one data-block size: with one block size, entries per block
are fixed, so only $c_{st}(1) + c_{ib}/b_{\text{blk}}$ is identified
($b_{\text{blk}}$, entries per data block, as in the block model below), and
varying the block size separates the two (D-23). Stage 18 measures $c_{sk}$ with `seek_nexts` = 0. Until the 2026-10-03
amendment a scan was priced as its $R_{sk}$ seeks only. `Assoc` scans step
past about 543 entries each (496M `Next` calls per 26.1M operations), and at
least about 200 s per 26.1M operations of client time outside Get, Seek and
write calls depended on the configuration and was unpriced: more than
$T$ = 2's whole priced $\mathcal C_R$ of about 88 s (Gate N1 pilots, `Assoc`,
repeat 1, `db/n1-assoc`; §0.7).

**Lemma D.19 (scan terms; new 2026-10-03, D-23).** Consider one scan, served
as operation $n$: a `Seek` followed by `Next` calls. Assume one client thread
(no write between the iterator's creation and its last step), no deletes,
range deletions or merge operands (A6), and no explicit snapshots (`db_bench`
takes none). Then a flush or compaction writes at most one version of a key
into each output file, since it drops a version shadowed by a newer one in the
same job when no snapshot needs it. An open iterator keeps its files by
pinning its version, not by registering a snapshot. Let $\mathcal K_s$ be the keys the scan
steps past, one per `Next` call, so $|\mathcal K_s| = R_{nx}(s)$. Let
$v_n(k)$ be the number of versions of key $k$ resident in the scan's sources
(the memtables and the table files of the version its iterator pins),
$g_n(k) = v_n(k) - 1$ the obsolete ones for a live key, $\mathcal L_n$ the live
keys, and $\bar g_n = \sum_{k\in\mathcal L_n}g_n(k)/|\mathcal L_n|$ the
resident obsolete versions per live key. Then:

- (i) *$R_{nx}$ does not depend on the policy.* The number of entries a scan
  steps past, and the entries themselves, are the same under every compaction
  policy.
- (ii) *Hidden steps are resident obsolete versions.*
  $R_{hd}(s) = \sum_{k\in\mathcal K_s}g_n(k) - J_s$, where $J_s \ge 0$ counts
  the versions that reseeks jump over ($J_s = 0$ without reseeks). Each hidden
  step is an obsolete version that lives in exactly one source: a memtable, an
  L0 file or a level $i \ge 1$. (D §4 charges the step to the level directly
  above that source: an entry in level $i \ge 1$ to level $i-1$, an entry in
  an L0 file to L0, an entry in a memtable to the memtable bucket.)
- (iii) *No uniformity is needed.* With $w_s(k) = \mathbf 1[k\in\mathcal K_s]/R_{nx}(s)$,
  $$\sum_{k\in\mathcal K_s}g_n(k) = R_{nx}(s)\big[\bar g_n + |\mathcal L_n|\,\mathrm{Cov}_n(w_s, g_n)\big],$$
  where $\mathrm{Cov}_n$ is the covariance over a live key drawn uniformly. The
  hidden versions per key stepped past exceed the uniform-garbage value
  $\bar g_n$ by exactly $|\mathcal L_n|$ times the covariance between where
  scans step and where obsolete versions are resident.
- (iv) *What can be bounded.* Write $R^{tab}_{hd}$ for the hidden steps in
  table files and $L_n$ for the number of non-empty levels $\ge 1$. Then
  $0 \le R_{hd}(s) \le \sum_{k\in\mathcal L_n}g_n(k)$, the resident obsolete versions, and
  $R^{tab}_{hd}(s) \le (k_0(n) + L_n)\,R_{nx}(s)$. The space ratio does not
  bound $R_{hd}$ more tightly: two trees that hold the same number of obsolete
  versions can give $R_{hd}(s) = 0$ (every obsolete version on a key no scan
  steps past) or put all of them on keys the scan steps past, up to the
  per-key limit.
- (v) *Iterator blocks (block model).* Suppose every data block of a table
  child holds $b_{\text{blk}}$ entries, and the position of each table child's first entry
  within its block is uniform on $\{0, \dots, b_{\text{blk}}-1\}$, independent of how many
  entries the scan takes from that child. Then
  $\mathbb E[R_{ib}(s)] = E^{tab}_s/b_{\text{blk}}$, where $E^{tab}_s$ is the number of
  internal steps whose stepped-past entry lives in a table file. Under the
  model, $R_{ib}$ depends on the policy only through the hidden steps in
  tables and through the share of steps taken in the memtables.

*Proof.* (i) The iterator's snapshot is the last sequence number at its
creation. It returns, in key order from the seek key, the newest version of
each key in the logical state after operations $1..n-1$, since no write
intervenes (one client thread). Flushes and compactions never change that
state: compaction never changes the result of a read (Lemma D.15's argument).
The number of `Next` calls is drawn from the operation's own random number,
independently of the data (Lemma D.10's proof), and the scan stops early only
at the end of the key space, which is a property of the logical state.
(ii) Lemma D.10's proof: after returning a key, the iterator examines every
resident version of that key before it lands on the next key, except the
versions a reseek jumps over. With no deletes and nothing newer than the
snapshot, every examined version but the newest is an obsolete version of the
key, and it sits in exactly one source. (iii) For weights summing to 1 over
the $M = |\mathcal L_n|$ live keys, $\sum_kw(k)g(k) = \frac1M\sum_kg(k) +
M\,\mathrm{Cov}_u(w, g)$ (expand the covariance, using $\mathbb E_u[w] =
1/M$). The keys stepped past are live, so $\mathcal K_s \subseteq \mathcal L_n$;
multiply by $R_{nx}(s)$. (iv) The first
bound is (ii). A2 allows at most one version per level $\ge 1$, and an L0 file
is the output of one flush or one intra-L0 compaction, which writes at most one
version per key. So a key has at most $k_0(n) + L_n$ versions in tables: all
are hidden if its newest version is in a memtable, and all but one otherwise.
For the last claim, place the same obsolete versions on keys the scan steps
past, or on live keys outside every scanned range; both placements respect A2.
(v) A child that takes $e$ consecutive entries from position $p$ loads one block
for each block boundary in $(p, p+e]$, that is $\lfloor (p \bmod b_{\text{blk}} + e)/b_{\text{blk}}\rfloor$
blocks. With $p \bmod b_{\text{blk}}$ uniform and independent of $e$, Hermite's identity
$\sum_{u=0}^{b_{\text{blk}}-1}\lfloor(u + e)/b_{\text{blk}}\rfloor = e$ gives an expectation of $e/b_{\text{blk}}$.
Sum over the table children by linearity. $\blacksquare$

*Measured* (§0.7; Gate N1 pilots, `db/n1-assoc`, tickers differenced
between the host log's stamps; reproduced in the check reports).
Hidden steps per returned entry are 1.15 at $T$ = 2 and 0.23 at $T$ = 10 on
`Assoc` (repeat 1; `rocksdb.number.iter.skip`, 570M and 114M). Under A6's fixed entry size the
uniform value is $\bar g_n \approx S - 1$, and the same runs' $S$ at run end
gives 0.105–0.107 at $T$ = 2, 0.048 at $T$ = 6 and 0.043 at $T$ = 10. So
hidden steps per returned entry are about 11 times the uniform value at
$T$ = 2, 5 times at $T$ = 6 and 5.4 times at $T$ = 10 (§0.7; three repeats
each, `db/n1-assoc/graphs/summary.csv`). The covariance term of (iii) carries
the difference: the obsolete versions sit on the hot keys that scans step
past. Garbage during a run can exceed garbage at its end, so the factors are
upper estimates. The model of (v) is an approximation. `Assoc`'s values vary
in size, and a file ends with a partial block. With 4 KiB blocks and entries
of about 1 KB, $b_{\text{blk}}$ is about 4, so each table step costs about a
quarter of a block load.

*Consequences.*

- *Garbage costs reads.* Each resident obsolete version costs one hidden step,
  plus about $1/b_{\text{blk}}$ of a block load when it is in a table, for every scan that
  steps past its key while it is resident. Space and scan cost therefore
  interact, most of all through hot keys. The shadowed-garbage estimate $g_i$
  of §4 and the space ratio $S$ do not see this concentration, and by (iv) they
  cannot bound it. $R_{hd}$ is measured per level of the hidden entry by a
  fork counter (D-23), and the evaluator maps each count to the level §4
  charges.
- *Memtable hidden steps* are fixed by the write sequence, up to Lemma D.15's
  flush-timing caveat. They do not depend on the policy.
- *The scan's own work.* $R_{nx}$, and so the base price of the entries
  returned, does not depend on the policy. What the policy changes is the
  hidden steps, the blocks they load, and the heap size $r$ at every step:
  through $k_0$, depth, and whether each level has a file in range.

**Proposition D.11 (static optimum of the L0 trigger; amended 2026-10-03,
D-23; (i), (b), (c), (d2), (ii) and (iii) amended again the same day).** Hold the trigger fixed at $K$. Costs are per operation
(§3 conventions). The symbols $Q_F$, $N_a$, $N_b$, $N^{(0)}$, $\theta_B$, $Y^F$,
$t^{job}_F$, $\mathcal W$, $\varrho_x$, $\varrho'_x$, $\bar\pi_{sc}$, $A_W$,
$A_I$, $B_R$, $\chi^{cc}_x$, $\chi^{cv}_x$, $\Gamma$, $\Upsilon$, $\Lambda_x$,
$a_h$, $b_h$, $s_\iota$, $\mathcal W^d_x$, $\varepsilon_x$, $e_x$, $\pi$,
$c_p$ and $R_{\text{deep}}$ are local to this proposition and to D.12. Assume:

- (a) *L0 merges.* No garbage is dropped at L0 ($\rho_0 = 1$), and L0 overlap
  is uniform-key (Lemma D.8), so each L0 merge reads and writes its $KF$
  source bytes and the $m_1C_1$ bytes of L1, and no L0 file moves trivially
  ($t_0 = 0$). Every Get that reaches the tables probes every L0 run, and every
  scan seeks every L0 file.
- (b) *The L0 cycle, an idealisation of the measured pattern.*
  - (b1) A flush adds one file of $F$ bytes every $Q_F$ operations. $Q_F$ is
    set by the write sequence.
  - (b2) Every L0 merge starts when $k_0$ reaches $K$ and takes all $K$
    files. It serves $N^{(0)}(K) = N_a + N_b(KF + m_1C_1)$ operations, affine
    in its bytes, with $N^{(0)}(K) < Q_F$, so no flush completes during it.
  - (b3) Every job sourced at a level $\ge 1$, and every trivial move, runs
    while L0 is empty. The flush that adds L0's $j$-th file runs entirely
    while $k_0 = j - 1$. Measured at the default point, (b3) holds on
    average, not per job (§0.7). In the 23 native runs of §0.7, during the
    merges of each start level $\ge 1$ L0 held at most 0.13 files on average
    (time-weighted; 0.14 by bytes; at most 0.05 pooled over a run's deeper
    merges); up to 5.8% of those merges (5.4% in the seven repeat-1 runs), and
    up to 30% of the trivial moves at `Assoc` T = 10, ran with one L0 file,
    and none with more. Other default-point `Assoc` T = 10 runs reach more:
    the ablation arms under `db/abl-assoc` (the same binary, `asis`; with
    compactions pinned to other cores, `pinned`) had up to 6.6% and 8.8% of
    deeper merges at one L0 file, pooled means up to 0.056 and 0.072, and up
    to 28% and 38% of trivial moves. (i)–(iv)
    are exact under (b3); "When (b3) holds only on average", after the proof,
    gives the exact correction and bounds its size at $K = 4$; its slope in
    $K$, which is what moves $K_0^\star$, is measured at Gate N2.
- (c) *Foreground costs.* For each foreground step type $x$ of D §1 — the read
  steps (filter probe, block read, run seek, reopen, heap step, iterator
  block, memtable search, the fixed parts of Gets and scans) and the Put
  insert — the quiet cost per operation at $k_0 = k$ is
  $\varrho_x(k) = \varrho_x(0) + \varrho'_xk$. One more L0 file adds
  $\varrho'_x \ge 0$, with $\sum_x\varrho'_x > 0$; $\varrho'_x = 0$ for the
  memtable search, the fixed parts and the Put insert, whose costs do not
  depend on the tree; nothing else the foreground costs depends on $K$. Each
  type is weighted $\beta_x$ (D §1: $\beta_R$ for a read step, $\beta_W$ for
  the Put insert). Every job's window holds the operation mix's share of
  each operation type (Puts, Gets and scans), so that over any window the
  Put insert and the fixed parts cost their mix constant per operation (D §1,
  "The write part in the analytic results").
- (d) *Prices and interference.* A9 holds, and each job carries D §1's charge
  $I_\iota$. An L0 merge reads and writes $KF + m_1C_1$ bytes (since
  $\rho_0 = 1$), so its bytes on the byte basis are
  $Y_\iota = X_\iota + \lambda(S_\iota + O_\iota) = \theta_B(KF + m_1C_1)$
  with $\theta_B = 1 + \lambda$, and its priced device time is
  $t^{job}_\iota = [c^0_{job} + (c_w + c_{cr})(KF + m_1C_1)]/p_{\mathrm{dev}}$.
  A flush reads no table bytes, so it has bytes $Y^F = F$ and priced device time
  $t^{job}_F = (c^F_{job} + c_wF)/p_{\mathrm{dev}}$.
- (d2) *Windows.* Every job's window $W_\iota$ (D §1) sees the L0 file count
  in force during the job: an L0 merge's window lies inside the merge, and a
  flush's or a deeper job's window inside the stretch of constant $k_0$ in
  which it runs. Exactly: let $s_\iota$ be the number of operations from the
  start of the maximal stretch of constant $k_0$ in which job $\iota$ ends to
  $n^e_\iota$ ($s_\iota = N^{(0)}(K)$ for an L0 merge; $Q_F$, or
  $Q_F - N^{(0)}(K)$ for the first flush of a cycle, for a flush; the
  operations since the L0 merge ended for a deeper job or trivial move).
  Every job lies inside its stretch by (b2)–(b3), so $\Delta n_\iota \le
  s_\iota$. A job with $\Delta n_\iota \ge n^{\mathrm{win}}$ has
  $W_\iota = (n^b_\iota, n^e_\iota]$, inside its stretch. A shorter job's
  window starts at the last counter snapshot at or before
  $n^e_\iota - n^{\mathrm{win}}$ (D §1), so it holds
  $|W_\iota| \le n^{\mathrm{win}} + n^{\mathrm{str}} - 1$ operations. Hence
  (d2) holds if and only if $|W_\iota| \le s_\iota$ for every job of the
  cycle shorter than $n^{\mathrm{win}}$; it holds when
  $n^{\mathrm{win}} + n^{\mathrm{str}} - 1 \le s_\iota$ for every such job,
  and, with a snapshot at every operation ($n^{\mathrm{str}} = 1$), if and
  only if $n^{\mathrm{win}} \le s_\iota$. A merge with $\Delta n_\iota \ge n^{\mathrm{win}}$ always meets it;
  a shorter job meets it if it ends at least
  $n^{\mathrm{win}} + n^{\mathrm{str}} - 1$ operations after the last change
  of $k_0$. $n^{\mathrm{win}}$ is set below the merges' spans (D §1) to
  secure it for those merges; a short job can still fail it, and the guard
  after the proof says when (d2) counts as failing at a trigger.
- (e) *Deeper flows.* The flows out of L1 and below do not depend on $K$. As
  in the 2026-09-29 proof, only the L0-sourced merges depend on $K$.

*L0 hidden steps* enter (c) linearly when the step price does not depend on
$r$; for a logarithmic price, see (iii). Each L0 file holds one flushed memtable,
so a key a scan steps past is in a given L0 file with a probability
$\bar\pi_{sc}$ that does not depend on $K$, and has $\bar\pi_{sc}k$ versions in
L0 in expectation. By Lemma D.19(ii) a key's hidden steps are its resident
versions less one, wherever the newest one is. So L0 adds $R_{nx}\bar\pi_{sc}$
hidden steps per scan per L0 file. Each adds a heap step and, by Lemma D.19(v),
about $1/b_{\text{blk}}$ of an iterator block. Hidden versions at levels $\ge 1$ are assumed
not to depend on $K$, as (c) says.

Define the interference weights of one job, in operations' worth of quiet
cost of type $x$ (D §1: $I_\iota = \sum_x\bar\varrho^{\,x}_\iota\,\bar q(\kappa^B_xY_\iota + \kappa^J_{x,\mathrm{kind}(\iota)}t^{job}_\iota)$):
$$\mathcal W^0_x = \bar q\Big[\kappa^B_x\theta_Bm_1C_1 + \kappa^J_{x,0}\,\frac{c^0_{job} + (c_w + c_{cr})m_1C_1}{p_{\mathrm{dev}}}\Big],\qquad \mathcal W^1_x = \bar q\Big[\kappa^B_x\theta_B + \kappa^J_{x,0}\,\frac{c_w + c_{cr}}{p_{\mathrm{dev}}}\Big]F,\qquad \mathcal W^F_x = \bar q\big(\kappa^B_xY^F + \kappa^J_{x,F}\,t^{job}_F\big).$$
An L0 merge's weight is $\mathcal W^0_x + \mathcal W^1_xK$, and a flush's is
$\mathcal W^F_x$; by (d2) a job's charge is its weight times
$\varrho_x(k_0)$ at the L0 count in force during it, summed over $x$, and
$J_\beta$ weighs each type's term by $\beta_x$. Then:

- (i) *Closed form.* For every admissible integer $K$, the part of the cost per
  operation that depends on $K$ is $g(K) = A/K + BK$, with
  $$A = \frac{\beta_W\big(c^0_{job} + (c_w + c_{cr})\,m_1C_1\big) + \sum_x\beta_x\mathcal W^0_x\,\varrho_x(0)}{Q_F},\qquad B = \frac{\beta_R}{Q_F}\sum_x\varrho'_x\Big[\frac{Q_F + \mathcal W^F_x}{2} + N_bF + \mathcal W^1_x\Big],$$
  the sum in $B$ running in effect over the read types, the only ones with
  $\varrho'_x > 0$.
  $A, B > 0$. Over real $K > 0$, $g$ is minimised at
  $$K_0^\star = \sqrt{A/B},$$
  and the best admissible trigger is one of the two integers next to
  $K_0^\star$, clamped to $[2, K_{\text{cap}}]$ with
  $K_{\text{cap}} = \min(\lfloor C_1/F\rfloor, K_{\text{slow}} - 1)$ (A3′).
- (ii) *Dependence on the priority.* Write
  $A = (\beta_WA_W + \beta_RA_I)/Q_F$ and $B = \beta_RB_R/Q_F$, with
  $A_W = c^0_{job} + (c_w + c_{cr})m_1C_1 + \sum_{x\in\mathcal X_{\mathrm{wr}}}\mathcal W^0_x\varrho_x(0)$
  (the last term, the write part of the L0 merge's interference, equals
  $\mathfrak w^J_0(c^0_{job} + (c_w + c_{cr})m_1C_1) + \mathfrak w^B\theta_Bm_1C_1$,
  D §1), $A_I = \sum_{x\in\mathcal X_{\mathrm{rd}}}\mathcal W^0_x\varrho_x(0)$
  and $B_R$ the sum in $B$; none of them depends on $\beta$. Then
  $$K_0^{\star2} = \frac{\beta_W}{\beta_R}\cdot\frac{A_W}{B_R} + \frac{A_I}{B_R}.$$
  Read priority still lowers $K_0^\star$ and write priority raises it, but
  $K_0^\star$ is not proportional to $\sqrt{\beta_W/\beta_R}$. As
  $\beta_R/\beta_W \to \infty$ it falls to the floor $\sqrt{A_I/B_R}$, set by
  the interference of L0 merges on reads. The floor is positive if and only
  if $A_I > 0$, that is, some read type $x$ has $\varrho_x(0) > 0$ and
  $\kappa^B_x > 0$ or $\kappa^J_{x,0} > 0$; without interference on reads it is 0, as in 2026-09-29. Only as
  $\beta_W/\beta_R \to \infty$ does it grow like $\sqrt{\beta_W/\beta_R}$.
  The read cost alone, $\mathcal C_R$, is the case $\beta_W = 0$: its
  $K$-dependent part per operation is $(A_I/K + B_RK)/Q_F$, minimised over
  real $K$ at the floor (Proposition A.8(ii) uses both).
- (iii) *A step price concave in $r$.* Replace (c) by
  $\varrho_x(k) = \varrho_x(0) + \varrho'_xk + \chi^{cc}_x(k) + \chi^{cv}_x(k)$,
  with $\chi^{cc}_x$ nondecreasing and concave, $\chi^{cv}_x$ nondecreasing
  and convex, and $\chi^{cc}_x(0) = \chi^{cv}_x(0) = 0$. This covers three
  cases:
  - a heap price $c_{st}(r) = a_h + b_h\log_2r$: a scan's $n_{st}$ steps at
    $r = r^{\neg0} + k$ children contribute the concave
    $n_{st}b_h\log_2(1 + k/r^{\neg0})$, where $r^{\neg0}$, the memtables and
    the levels with a file in range, is held fixed;
  - L0's hidden steps at that price: they contribute a convex
    $k\log_2(r^{\neg0} + k)$ part;
  - Gets that stop at a hit in an L0 file. With $\pi$ the probability that a
    given L0 file holds the Get's key, $c_p$ the cost of probing one L0 file
    (its false-positive block reads included) and $R_{\text{deep}}$ the
    Get's expected cost below L0, the Get part is
    $c_p\sum_{j=1}^{k}(1-\pi)^{j-1} + (1-\pi)^kR_{\text{deep}}$, whose
    increments are $(1-\pi)^k(c_p - \pi R_{\text{deep}})$. It is
    nondecreasing and concave in $k$ when $\pi R_{\text{deep}} \le c_p$,
    which this case assumes for every key (a sum over keys of such functions
    is again one). For hot keys with $\pi R_{\text{deep}} > c_p$ it
    *decreases* in $k$, and is convex: one more L0 file then saves deeper
    probes than it costs, and neither (c) nor (iii) covers it.
    Measuring L0's Get hits (Gate N0's per-level counters) shows whether it
    occurs.

  Let $\Gamma^M_x = \beta_R(1 + \mathcal W^F_x/Q_F)$,
  $\Gamma^-_x = \beta_R(N_a + N_bm_1C_1 + \mathcal W^0_x)/Q_F$ and
  $\Gamma^+_x = \beta_R(N_bF + \mathcal W^1_x)/Q_F$. For a function $\chi$ of
  the L0 count write $\Delta\chi(K) = \chi(K+1) - \chi(K)$ and
  $\Delta^2\chi(K) = \Delta\chi(K+1) - \Delta\chi(K)$, and take $B$ from the
  linear parts as in (i). Suppose that for every integer $K$ with
  $2 \le K < K_{\text{cap}}$,
  $$(\mathrm U)\qquad 2B + \sum_x\Big[\Gamma^M_x\Delta\chi^{cc}_x(K) + \Gamma^-_x\Delta^2\chi^{cc}_x(K) + \Gamma^+_x\big((K+2)\Delta\chi^{cc}_x(K+1) - K\Delta\chi^{cc}_x(K)\big)\Big] > 0.$$
  Then $g(K+1) - g(K)$ changes sign at most once on the admissible range,
  from negative to positive. So $g$ is unimodal there. The best admissible
  trigger is the smallest admissible $K$ with $g(K+1) \ge g(K)$, or
  $K_{\text{cap}}$ if there is none, and it is unique unless two adjacent
  triggers tie. $g$ need not be convex, and there is no closed form.
- (iv) *The logarithmic heap price.* For
  $\chi^{cc}_x(k) = \Lambda_x\log(1 + k/r^{\neg0})$, (U) holds whenever
  $$2B\,(r^{\neg0} + 2)^2 > \sum_x\Gamma^-_x\Lambda_x.$$
  $B$ and every $\Gamma^-_x$ carry $\beta_R$ and no $\beta_W$, so this
  condition is the same in every mode.

*Proof.* (i) By (b), one cycle of the L0 file count lasts $KQ_F$ operations:
$k_0 = j$ for $Q_F$ operations for each $j = 1, \dots, K-1$; $k_0 = K$ for the
$N^{(0)}(K)$ operations of the merge; and $k_0 = 0$ for the remaining
$Q_F - N^{(0)}(K)$. Per cycle, the cost has three parts.

- *Write side.* There is one L0 merge, priced
  $c^0_{job} + (c_w + c_{cr})(KF + m_1C_1)$: by Lemma D.18 with
  $\rho_0 = 1$ it writes and reads $KF + m_1C_1$ bytes. Per operation this is
  $[c^0_{job} + (c_w + c_{cr})m_1C_1]/(KQ_F)$ plus a constant. Flushes,
  deeper jobs and trivial moves cost the same per operation for every $K$, by
  (b1) and (e).
- *Quiet foreground costs.* Summing $\varrho_x$ over the cycle and dividing by
  $KQ_F$ gives
  $$\varrho_x(0) + \frac{1}{K}\sum_{j=1}^{K-1}\varrho'_xj + \frac{N^{(0)}(K)}{KQ_F}\,\varrho'_xK = \varrho_x(0) + \varrho'_x\Big(\frac{K-1}{2} + \frac{N_a + N_bm_1C_1}{Q_F} + \frac{N_bFK}{Q_F}\Big),$$
  a constant for the Put insert and the fixed parts, whose $\varrho'_x = 0$.
- *Interference.* The L0 merge runs at $k_0 = K$, and by (d2) its window
  does too, so its charge, weighted, is
  $\sum_x\beta_x(\mathcal W^0_x + \mathcal W^1_xK)(\varrho_x(0) + \varrho'_xK)$
  (D §1, (d)). Per operation this is
  $\sum_x\beta_x[\mathcal W^0_x\varrho_x(0)/(KQ_F) + \mathcal W^1_x\varrho'_xK/Q_F]$
  plus a constant. The $K$ flushes run at $k_0 = 0, \dots, K-1$ (b3), with
  their windows (d2), and charge
  $\sum_x\beta_x\mathcal W^F_x\sum_{j=0}^{K-1}\varrho_x(j)$, which is
  $\sum_x\beta_x\mathcal W^F_x\varrho'_x(K-1)/(2Q_F)$ per operation plus a
  constant. Deeper jobs and trivial moves run at $k_0 = 0$ (b3), with their
  windows (d2), and their bytes and device times do not depend on $K$ (e), so
  their charges do not depend on $K$.

Collecting the terms in $1/K$ and in $K$ gives $A$ and $B$; every term in
$K$ carries a $\varrho'_x$, nonzero only for read types, so $B$ carries
$\beta_R$ alone. $A > 0$ because $\beta_W(c_w + c_{cr})m_1C_1 > 0$, and
$B > 0$ because $\sum_x\varrho'_x > 0$. For
linear $\varrho_x$ the cycle sums are exact at every integer $K$. The real
function $A/K + BK$ has second derivative $2A/K^3 > 0$, so it is strictly
convex with minimiser $\sqrt{A/B}$. A strictly convex function decreases up to
its minimiser and increases after it, so over the integers of
$[2, K_{\text{cap}}]$ its minimum is at the largest integer at most
$K_0^\star$ or the smallest at least $K_0^\star$, clamped to that range.

(ii) Substitute $A$ and $B$ into $K_0^{\star2} = A/B$.

(iii) Let $\Upsilon(K) = K(K+1)[g(K+1) - g(K)]$, which has the sign of
$g(K+1) - g(K)$. The cost now has the terms $A/K + BK$, and for each nonlinear
part $\chi \in \{\chi^{cc}_x, \chi^{cv}_x\}$ the terms
$\Gamma^M_xM_\chi(K) + \Gamma^-_x\chi(K)/K + \Gamma^+_x\chi(K)$, where
$M_\chi(K) = \frac1K\sum_{j=1}^{K-1}\chi(j)$. These are, in turn: the cycle
average of the quiet cost and of the flushes' charges; the merge's operations
and interference at $k_0 = K$ that do not grow with $K$; and those that do.
Direct computation gives the increment $\Upsilon(K+1) - \Upsilon(K)$ term by
term:

- $A/K$ contributes nothing, since its $\Upsilon$ is the constant $-A$;
- $BK$ contributes $2B(K+1)$;
- $\chi$ contributes
  $(K+1)\big[\Gamma^M_x\Delta\chi(K) + \Gamma^-_x\Delta^2\chi(K) + \Gamma^+_x\big((K+2)\Delta\chi(K+1) - K\Delta\chi(K)\big)\big]$.

For the convex part each bracket is nonnegative: $\Delta\chi^{cv} \ge 0$,
$\Delta^2\chi^{cv} \ge 0$, and $(K+2)\Delta\chi^{cv}(K+1) \ge K\Delta\chi^{cv}(K)$.
So (U) makes $\Upsilon$ strictly increasing on the admissible range.
$\Upsilon$ then changes sign at most once, from negative to positive, and so
does $g(K+1) - g(K)$; that is unimodality on the integers.

(iv) For $\chi = \Lambda\log(1 + k/r^{\neg0})$:

- $\Delta\chi \ge 0$;
- $|\Delta^2\chi(K)| = |\chi''(\xi)| \le \Lambda/(r^{\neg0}+K)^2$ for some
  $\xi \in (K, K+2)$;
- $(K+2)\Delta\chi(K+1) \ge (K+2)\Lambda/(r^{\neg0}+K+2) \ge K\Lambda/(r^{\neg0}+K) \ge K\Delta\chi(K)$.
  This uses $\frac{1}{y+1} \le \log(1 + \frac1y) \le \frac1y$ and
  $(K+2)(r^{\neg0}+K) - K(r^{\neg0}+K+2) = 2r^{\neg0} \ge 0$.

So the left side of (U) is at least
$2B - \sum_x\Gamma^-_x\Lambda_x/(r^{\neg0}+K)^2 \ge 2B - \sum_x\Gamma^-_x\Lambda_x/(r^{\neg0}+2)^2$
for $K \ge 2$. $\blacksquare$

*Why unimodality and not convexity.* Under (iii) the concave parts give $g$
negative curvature that decays like $1/K^2$, while $A/K$'s positive curvature
decays like $1/K^3$. Unless a convex part offsets it (L0's hidden steps at a
logarithmic price do, with curvature decaying like $1/K$), $g$ stops being
convex at large enough $K$, and the 2026-09-29 argument from strict convexity
no longer applies. Unimodality survives under (U). It is all that the
conclusion needs, whether stated as "the best integer is next to the real
optimum" in (i) or in its integer form in (iii).

*Edge cases.* Above $C_1/F$ files, L0's byte branch fires first and (b) no
longer describes L0; $K_{\text{cap}}$ excludes that range. (b2) needs
$N^{(0)}(K_{\text{cap}}) < Q_F$. (b3) holds only on average at $K_0 = 4$
(below), and was measured only there. Each
cycle delivers $KF$ bytes to L1, so the deeper cascade per cycle grows with
$K$. Already at $K = 4$ it overruns the next flush in nearly a third or more of
`Assoc` T = 10 cycles (A §4 (f)); at larger $K$ more of its jobs run with
$k_0 \ge 1$, their charges grow with $K$, and $B$ is understated (below). A deeper job that holds the slot when $k_0$ reaches $K$
(slot blocking) breaks (b2), and was not seen at the default point: no job
sourced at a level $\ge 1$ ran while L0 held two or more files (§0.7). Gate
N2's $K_0 = 8$ arms check (b2) and (b3) from the event log's `lsm_state`
(Execution order). A job that serves no operation, as in a stall, is charged
at its window's read cost (D §1); the cycle of (b) has none, since
$K < K_{\text{slow}}$. Each job carries its own charge (D §1), so a flush
that overlaps a deeper job changes neither job's charge under (d2), and A10's
additivity, a claim about the physical slowdown, is not used.

*When (b3) holds only on average*. Let $\mathcal W^d_x$ be the
interference weight per operation of the jobs sourced at a level $\ge 1$ and
of the trivial moves (D §1's weights for their bytes and priced device
times, summed over a cycle and divided by $KQ_F$), which (e) makes
independent of $K$, and $\varepsilon_x(K)$ the $\mathcal W$-weighted mean L0
count over their windows, each window inside its job's stretch (d2). (b3)
says $\varepsilon_x \equiv 0$. Without it, these jobs' charges are
$\sum_x\beta_x\mathcal W^d_x(\varrho_x(0) + \varrho'_x\varepsilon_x(K))$ per
operation, so, exactly,
$$g(K) = \frac AK + BK + \beta_R\sum_x\varrho'_x\mathcal W^d_x\,\varepsilon_x(K),$$
and nothing else in the proof of (i) changes, as long as (b2) still holds:
these jobs do not change $k_0$, so the cycle's quiet costs are as before (in
the 23 runs of §0.7 none held the slot while L0 was due, so (b2) did hold). The extra term is nonnegative, and it vanishes under
(b3). If $\varepsilon_x$ is affine in $K$ on the admissible range, with slope
$e_x$, (i) and (ii) hold with $B + \beta_R\sum_x\varrho'_x\mathcal W^d_xe_x$
in place of $B$ (and so $B_R + Q_F\sum_x\varrho'_x\mathcal W^d_xe_x$ in
place of $B_R$, since $B = \beta_RB_R/Q_F$); with $e_x \ge 0$ the corrected $K_0^\star$ is lower, by about half the
relative rise of $B$. In general the term is not of the form $A/K + BK$
and must be measured at each trigger compared. *Size at the default point.* The weighted mean L0
count over all deeper merges of a run was at most 0.05 in the 23 runs of
§0.7 (at most 0.046 by bytes written), and up to 0.072 in the ablation arms
above. At the illustration's magnitudes
below (byte-written basis, $\lambda = 0$, $\kappa^B$ = 0.1% per MB/s) and with the deeper
merges writing about 770 bytes per operation at `Assoc` T = 10
(event log of the $\bar q$ arm, `db/qbar-assoc-d21id`; trivial moves carry no weight
on this basis), $\mathcal W^d_x \approx 0.053$,
so the term is at most about $0.0025\,\beta_R\sum_x\varrho'_x$ per
operation in the 23 runs, 0.11–0.13% of $BK$ at $K = 4$ (where
$BK \ge 2\beta_R\sum_x\varrho'_x$), and about 0.18% at the ablation arms'
0.072. This bounds the term's size at $K = 4$, not its slope. How it grows with $K$ is not measured:
Gate N2's $K_0 = 8$ arms report $\varepsilon$ at $K = 8$ (Execution order),
which with $K = 4$ gives the secant slope $e_x = (\varepsilon_x(8) -
\varepsilon_x(4))/4$, and so the corrected $B$. The trivial moves' larger
exposure (up to 30% of them at `Assoc` T = 10 in the 23 runs, 38% in the
ablation arms) enters through their small
weights, their busy part alone. A numerical check of the identity agrees
to a relative $6\cdot10^{-12}$ (the check reports).

*When (d2) fails.* A window longer than its job also averages operations
served before the job began (D §1). The bullets take $n^{\mathrm{str}} = 1$;
a coarser stride lengthens a window by up to $n^{\mathrm{str}} - 1$
operations at its start.

- *The L0 merge.* If $N^{(0)}(K) < n^{\mathrm{win}} \le N^{(0)}(K) + Q_F$, its
  window holds $N^{(0)}(K)$ operations at $k_0 = K$ and
  $n^{\mathrm{win}} - N^{(0)}(K)$ at $K - 1$, so its mean L0 count is
  $K - 1 + N^{(0)}(K)/n^{\mathrm{win}}$, still affine in $K$. Its part of $g$
  keeps the form $A/K + BK$, with $\varrho_x(0)$ replaced in $A$ by
  $\varrho_x(0) - \varrho'_x\big(1 - (N_a + N_bm_1C_1)/n^{\mathrm{win}}\big)$,
  and $\varrho'_x$ replaced in $B$'s $\mathcal W^1_x$ term by
  $\varrho'_x(1 + N_bF/n^{\mathrm{win}})$.
- *Deeper jobs and flushes.* The deeper jobs run right after the L0 merge
  (b3), so a window longer than the time since the merge ended reaches back
  into it, at $k_0 = K$, and into the stretch before it. Their charges then
  grow with $K$ at a rate set by the cascade's timing, which (b) does not
  model: $B$ gains a term, $A$ loses part of the L0 merges' correlation, and
  (i)'s closed form needs that exposure measured. The first flush after a
  merge is affected the same way when $n^{\mathrm{win}} > Q_F - N^{(0)}(K)$.
- *Long windows.* As $n^{\mathrm{win}}$ grows past the cycle ($KQ_F$
  operations), every window's mean L0 count tends to the cycle's
  time-average, which is mean field (below).

A numerical check (2026-10-03), which simulates the cycle operation by
operation and charges each job over its window, reproduces (i) to a relative
error of $1.5\cdot10^{-11}$ over 200 random parameter sets when every window
lies inside its job (the first check report, its §7), and shows the
interference's profile in $K$ moving toward the mean-field one as
$n^{\mathrm{win}}$ grows. A numerical check, with a write type weighted
$\beta_W$ and fixed read parts added, reproduces (i) to $2\cdot10^{-8}$, the
rounding of $N^{(0)}(K)$ to whole operations (the check reports).

*The guard* (post-integration decision, 2026-10-03). $n^{\mathrm{win}}$ is
set from the reference arms' merge spans, below the span in operations of
the L0 merges and the merges sourced deeper (D §1's rule; its value is fixed
by a dated entry, D-23 or later). Every such merge that serves at least
$n^{\mathrm{win}}$ operations is then charged over its own operations, so
those merges meet (d2) whatever their timing. A merge in a stall or the
drain serves fewer operations, often none, and is charged over the
$n^{\mathrm{win}}$ operations before it ended (D §1); OBJ-8(d) reports such
merges apart. What can still fail (d2) is such a merge, a merge shorter than
the reference arms' spans, or a job shorter than $n^{\mathrm{win}}$ that ends
within $n^{\mathrm{win}} + n^{\mathrm{str}} - 1$ operations of a change of
$k_0$: a trivial move or a short flush, whose charges are small (a trivial
move's charge is its busy part alone, at $c^{tm}_{job}/p_{\mathrm{dev}}$
device-seconds). Such short jobs occur at every trigger (at `Assoc` T = 10
the trivial moves that saw one L0 file started 15–21 ms, about 1,000–1,500
operations, after the flush; the check reports), so (d2) taken job by job
fails at every trigger, and the guard is a measured share instead. Each run
reports it (OBJ-8(d)): the share of merge bytes, by kind and start level,
whose window ran past their own span. The merges outside that share meet
(d2): their windows see the L0 count in force during them. That count is the
one (b) assumes for the L0 merges, and for the merges sourced deeper up to
(b3)'s exposure (above); so these merges are charged as (i)–(iv) assume, up
to that exposure (not exactly, since (b3) holds only on average). The share
bounds the merge bytes whose charges depart from (d2), and the bullets above
say which way each departure moves the closed form. *(d2) fails at a
trigger* when that share exceeds a tolerance, set by a dated entry before Gate N2 (D-24, §0.7 item 4). *(b2) fails at a trigger* likewise when the share of L0-merge bytes in
merges that did not start at $k_0 = K$, did not take all $K$ files, or saw a
flush complete while they ran exceeds a tolerance, likewise set (D-24,
§0.7 item 4); OBJ-8(d) reports that share per run, from the event log's `lsm_state`
and its compaction and flush records. Where (b2) or (d2) fails, D.11's
closed form is not used at that trigger, and Gate N2 compares the admissible
triggers directly.

*What changed, and why.* The 2026-09-29 statement assumed a time-average L0
count $K_0/2 + \delta_0$ with $\delta_0$ independent of $K_0$. In the cycle of
(b) the average is $(K-1)/2 + N^{(0)}(K)/Q_F$, which is affine in $K$ with
slope $1/2 + N_bF/Q_F$. Only affinity is needed. Three new terms raise
$K_0^\star$, since each adds to $A$:

- the per-job cost $c^0_{job}$, paid once per L0 merge, so
  $c^0_{job}/(KQ_F)$ per operation;
- the compaction bytes read, $c_{cr}m_1C_1$;
- the interference of L0 merges on the foreground steps they overlap,
  $\sum_x\beta_x\mathcal W^0_x\varrho_x(0)$, whose busy part now includes the
  merge's per-job device time $c^0_{job}/p_{\mathrm{dev}}$.

Interference also adds to $B$, through the merge's bytes that grow with $K$
($\mathcal W^1_x$) and the flushes' charges ($\mathcal W^F_x$), but at the
measured correlation that growth is small. Under mean field every deeper job's
charge would also grow with $K$, so the slope of interference in $K$ would be
about 2.8 times too steep, and $K_0^\star$ about 2–4% too low (1.9–4.0% at
the critique's provisional magnitudes; derived, critique Q2). Windows longer than the jobs move the charge toward mean field.
Under the charge as first designed (D §1), the busy part counted the
operations served during a job, so $\mathcal W$ carried $N_a$, $N_b$ and the
flush's operations; with priced device time in their place the weights
contain no speed of the run.

*Illustration, not a prediction.* Take provisional magnitudes for `Assoc` at
$T$ = 10, with (d2) and (b3), each with its source:

- $c_w + c_{cr}$ = 1.44 ns per L0 byte (L0 merges' run time per byte
  written, 1.43–1.45 ns over the runs of §0.7, host and event logs under
  `db/n1-assoc` and `db/qbar-assoc-d21id`, the check reports), and an L0
  merge serving $N_b = \bar q \times 1.44$ ns $\approx 9.9\cdot10^{-5}$
  operations per byte;
- $c^0_{job}$ = 4.7 ms (the median per-job gap of L0 merges, 4.67 ms on the
  pilot and 4.75 ms on the $\bar q$ arm, §0.7);
- $\kappa^B$ = 0.1% per MB/s on the byte-written basis ($\lambda = 0$, so
  $\theta_B = 1$; §0.7), $\kappa^J = 0$,
  and no write part ($\kappa^B_{put} = \kappa^J_{put,0} = 0$, not yet
  measured);
- $\varrho(0)$ = 4.0 µs and $\sum_x\varrho'_x$ = 0.24 µs per operation
  (critique Q2, provisional prices);
- $Q_F$ = 12,112 operations (measured: 12,111–12,113 operations per flush
  in the first repeat of every `Assoc` run of §0.7, 12,105–12,123 over all
  14 `Assoc` runs; operations over flushes in the measured phase, host logs
  under `db/n1-assoc` and `db/qbar-assoc-d21id`), $F$ = 1.96 MB (the mean
  flush file, event logs of the same runs) and $m_1C_1$ = 16 MiB.

$K_0^\star$ is then 4.65 in balanced mode and 2.20 in read priority at
$\beta^\star = 10$, with floor 1.73. In write priority at $\beta^\star = 10$
it is 13.8, clamped to 8. (At the critique's 1.42 ns, 4.63, 2.20, 1.73 and
13.7: the choice within the measured range moves them by under 1%.) The
2026-09-29 formula, at $c_w$ = 1.70 ns, gives 4.4, 1.4 and 14.0. Under a
logarithmic heap price, whether (U) holds follows from (iv)'s condition,
which is to be evaluated at the calibrated prices. The prices
come from the calibrations D-23 governs, and the scan-iteration terms, not yet priced in these
figures, raise $\varrho(0)$ and so $A_I$.

This has the same shape as the draft's $K^\star = \sqrt{wT/r}$, with the
L1-to-flush fanout $m_1C_1/F$ in place of $T$. The 2026-09-29 statement
"$K_0^\star \propto \sqrt{\beta_W/\beta_R}$" holds only without interference.
(ii) replaces it.

**Corollary D.12 (a provable adaptivity gap for the L0 trigger; amended
2026-10-03, D-23).** Suppose the workload alternates between phases $p$, each
with a positive share $n_p/N$ of the measured operations. Suppose each phase
lasts long enough that, under a fixed trigger $K$, its cost is $n_p\,g_p(K)$
plus a boundary term. Here $g_p$ is Proposition D.11's stationary cost per
operation, computed with the phase's operation mix: user bytes, Gets and scans
per operation, and scan lengths. If the phases' sets of best admissible
triggers are disjoint, then any fixed trigger costs strictly more than the
trigger switched per phase, not counting the cost of each switch (the boundary
terms).

*Proof.* With the boundary terms set aside, a fixed $K$ costs
$\sum_pn_pg_p(K) \ge \sum_pn_p\min_{K'}g_p(K')$, the cost of switching to each
phase's best trigger. Equality would need $K$ to be best in every phase, which
disjointness excludes. Every $n_p > 0$, so the inequality is strict.
$\blacksquare$

*What the proof needs.* The 2026-09-29 proof used strict convexity in each
phase. The amended proof uses neither convexity nor unimodality: the
admissible triggers are a finite set, so every phase has a best one.
Proposition D.11 computes those best triggers, by (i) or by (iii), and so
decides whether the premise holds. Under (i), a phase moves $K_0^\star$
through three channels:

- the operations per flush $Q_F$, set by its write share;
- the read costs per operation $\varrho_x(0)$ and $\varrho'_x$, set by its Get and
  scan shares;
- the interference weights.

Phases are counted in operations, so a phase's cost does not depend on how
fast it runs (Lemma D.17), and the shares $n_p/N$ are the same for every
policy, as in the definition of $\mathcal G$. The argument therefore holds
with every cost term of the 2026-10-03 amendment.

This predicts, in advance, a positive phase-adaptivity gap $\mathcal G$
(Pathway B) for one knob. A real controller also pays the switching
transient: the L0 contents at the switch, and the jobs, with their
interference charges, that span it.

**Proposition D.13 (interior multipliers and depth in steady state; amended
2026-10-03, D-23; (i) and (iv) amended again the same day).** Under Corollary D.9's assumptions, with $L$ and $K_0$ fixed:

- (i) $W$ is minimised over the interior multipliers exactly when all fanouts
  are equal, $f_i = (B_L/(K_0F))^{1/L}$. That profile is static.
  *Amended:* $W$ is no longer the whole write-side cost, and minimising $W$
  minimises $C_\beta$'s write side only when the per-byte weights are equal.
  Assume further:
  - (I1) the mean source bytes per merge $\bar s_i$ (Lemma D.18) do not depend
    on the multipliers;
  - (I2) the measured correlation of §3, carried into the charge: L0 merges
    run at L0's read cost with $K_0$ files, deeper jobs run with L0 empty
    (D.11's (b3), an idealisation of a pattern that holds on average, §3),
    each merge's window sees the L0 count in force during it (Proposition
    D.11, (d2)), and the Puts' share of every merge's window is the mix's (D
    §1, the write part);
  - (I3) the quiet read cost per operation over the windows of each level's
    merges does not depend on the interior multipliers, so (ii)'s read
    effects are held fixed.

  Let $\bar\varrho^{\,x}_i$ be the quiet cost per operation of foreground step
  type $x$ over the windows of level $i$'s merges, and $\theta_B$ as in (d)
  of Proposition D.11. Define the interference charge one more overlap byte
  adds to a level-$i$ merge, its priority-weighted value (D §1: each step
  type's term weighted $\beta_x$, $\beta_R$ for read steps and $\beta_W$ for
  the Put insert), and that byte's priced cost (money per byte),
  $$I^O_i = \bar q\sum_x\bar\varrho^{\,x}_i\Big(\kappa^B_x\theta_B + \kappa^J_{x,\langle i\rangle}\,\frac{c_w + c_{cr}}{p_{\mathrm{dev}}}\Big),\qquad I^{O,\beta}_i = \bar q\sum_x\beta_x\bar\varrho^{\,x}_i\Big(\kappa^B_x\theta_B + \kappa^J_{x,\langle i\rangle}\,\frac{c_w + c_{cr}}{p_{\mathrm{dev}}}\Big),\qquad \psi_i = \beta_W(c_w + c_{cr}) + I^{O,\beta}_i,$$
  with $\kappa^J_{x,\langle i\rangle}$ the busy coefficient of the kind of
  level $i$'s merges ($0$ at $i = 0$, $d$ below). An overlap byte is read
  once and written once, adds $\theta_B$ bytes to the job's $Y_\iota$ and
  $(c_w + c_{cr})/p_{\mathrm{dev}}$ to its priced device time; $\psi_i$ is
  also Theorem A.2(iv)'s weight. Then the part of the job-borne cost,
  $\beta_W\sum_\iota\tau^{job}_\iota + \sum_\iota I^\beta_\iota$, per user
  byte that depends on the interior multipliers is
  $c\sum_{i=0}^{L-1}\psi_if_i$. Under the product constraint of Corollary D.9
  it is minimised exactly at $f_i \propto 1/\psi_i$, which is the
  equal-fanout profile if and only if all $\psi_i$ are equal. Under (I2),
  $\bar\varrho^{\,x}_0 = \varrho_x(K_0)$ exceeds
  $\bar\varrho^{\,x}_i = \varrho_x(0)$ for $i \ge 1$ for every read type whose
  cost L0 files raise, and $\bar\varrho^{\,x}_0 = \bar\varrho^{\,x}_i$ for the
  memtable search, the fixed parts and the Put insert, which do not depend on
  the tree. If $\kappa^J_{x,0} \ge \kappa^J_{x,d}$ for every $x$ (in
  particular on the byte basis, $\kappa^J = 0$), any interference on a read
  type whose cost L0 files raise therefore makes $\psi_0 > \psi_i$, and the
  optimum gives L0 the smaller fanout: $f_0/f_i = \psi_i/\psi_0 < 1$. A busy
  coefficient larger for deeper merges than for L0's can reverse the order.
  The priced charge counts device time, not the operations a job serves, so
  the measured job speeds (606, 315 and 230 MB/s at L0, L1 and L2 on `Assoc`
  at $T$ = 10, the $\bar q$ arm's host and event logs) do
  not enter $\psi_i$. The profile is still static.
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
  L0 run count through $\delta_0$, the part of the mean L0 count beyond
  $K_0/2$ (Proposition D.11 models it as the L0 merge's operations
  $N^{(0)}/Q_F$): $m_1$ directly, since it
  sets the size, and so the duration, of every L0→L1 merge, and every $m_i$
  through the single compaction slot, whose busy time depends on each level's
  merge sizes. Flushes that arrive meanwhile raise the mean L0 run count, adding
  probes to every Get that reaches the tables and seeks to every scan; merges
  out of L0 grew 34–74% at $s = 1.5$–2 (audit §3). For a fixed profile all of
  this is static.
  *Amended.* The interior multipliers reach the cost through three further
  channels:
  - *Hidden steps and iterator blocks.* By Lemma D.19, a scan's hidden steps
    are the obsolete versions resident on the keys it steps past, and the
    blocks they load follow them (Lemma D.19(v)). Raising $m_i$ keeps data at
    level $i$ longer, so an older version below level $i$, shadowed by a newer
    one in it, stays resident longer. But merges into the larger level $i$
    also meet, and drop, more of the versions arriving from above. The model
    does not fix the net sign: it depends on where hot keys' versions sit.
    Uniform garbage cannot settle it, since the measured hidden steps are
    about 5–11 times the uniform value (Lemma D.19). It is measured per level
    with the fork's hidden-step counter (D-23). The heap size $r$ changes only if a
    level empties or gains its first file in a scanned range, as $R_{sk}$
    does.
  - *Job counts.* The multipliers change the merged bytes per user byte
    through survival and trivial-move shares (Theorem A.2's caveat), and so
    the merge counts $(a_i - t_i)/\bar s_i$ (Lemma D.18) and their
    interference. A trivial move now costs $c^{tm}_{job}$, so a profile that
    changes trivial-move shares changes the write cost, although trivial
    moves write nothing.
  - *Interference weights.* Every job's charge is linear in the quiet cost
    per operation of each step type over its window, and its read part in
    the read costs (D §1). So a profile that changes the read cost, by (ii)
    above, also changes every job's charge.

  For a fixed profile all of this is static too.
- (iii) The space bound of Lemma D.14 increases in every $m_i$. Garbage now
  also costs reads (Lemma D.19), so the space bound does not bound the whole
  cost of holding data.
- (iv) *Depth (amended 2026-10-03).*
  Treat $L$ as continuous with equal fanouts
  $f = (B_L/(K_0F))^{1/L}$ at all $L$ merge levels (L0's and $L - 1$ deeper
  ones), with $B_L > K_0F$ held fixed as $L$ varies, and (I1)–(I2). Assume
  also
  - (I4) the busy coefficients do not depend on the merge kind:
    $\kappa^J_{x,0} = \kappa^J_{x,d} =: \kappa^J_x$ for every step type $x$
    (true on the byte basis, $\kappa^J \equiv 0$). (The remark after the
    proof says what survives without it.)

  Let:
  - $R(L) = R_0 + b_1L$, with $R_0 > 0$: the quiet read cost per second (at
    the reference rate) with L0 empty, affine in $L$;
  - $b_1$: the quiet read cost rate one more level adds. For Gets this is
    $q_{pt}(c_f + \varepsilon c_{blk})h$, with $h$ the share of Gets that probe
    the level. For scans it is the level's seek, heap share, hidden steps and
    blocks. A heap price concave in $r$ makes "affine" an approximation;
  - $b = \beta_Rb_1$, as in 2026-09-29;
  - $R_K$: the extra quiet read cost rate of $K_0$ L0 files, at which L0
    merges run (I2);
  - $\hat\kappa = \kappa^B\theta_B + \kappa^J(c_w + c_{cr})/p_{\mathrm{dev}}$:
    the read part of the interference charge per merged byte per unit of read
    cost rate (one merged byte adds $\theta_B$ bytes to a job's $Y_\iota$ and
    $(c_w + c_{cr})/p_{\mathrm{dev}}$ to its priced device time, D §1), the
    same for every merge level by (I4) and for every read-step type. With
    distinct types, replace $\hat\kappa R$ by $\sum_x\hat\kappa_xR_x$ (and
    likewise $\hat\kappa R_K$, $\hat\kappa b_1$, $E_I R$ and $E^0_I R$); the
    argument is unchanged;
  - $\Pi = \beta_W\big[(1 + \mathfrak w^J)(c_w + c_{cr}) + \mathfrak w^B\theta_B\big]$:
    the priced write-side cost of one merged byte, its bytes read and written
    and the write part of its interference (D §1, the write part:
    $\mathfrak w^J = \bar q\varrho^{put}\kappa^J_{put}/p_{\mathrm{dev}}$ and
    $\mathfrak w^B = \bar q\varrho^{put}\kappa^B_{put}$, constants, since the
    Put insert's cost does not depend on the tree); without a write part
    $\Pi = \beta_W(c_w + c_{cr})$, as first written;
  - $E = \beta_Wu\,(1 + \mathfrak w^J)\,c^d_{job}/\bar s$: one more level adds
    $1/\bar s$ merges per user byte (Lemma D.18, (I1)), each at its per-job
    price and that price's write-part interference;
  - $E_I = \beta_Ru\,\kappa^Jc^d_{job}/(p_{\mathrm{dev}}\bar s)$ and
    $E^0_I = \beta_Ru\,\kappa^Jc^0_{job}/(p_{\mathrm{dev}}K_0\bar s_F)$: the
    read part of the busy charge of the per-job device time of a deeper merge
    level's merges and of L0's merges ($1/(K_0\bar s_F)$ L0 merges per user
    byte, Lemma D.18(iv)), per unit of read cost rate;
  - $\mathcal M(L) = L(1 + cf)$: the merge bytes written, and read, per user
    byte ($W - 1 = W_r$, Lemma D.18).

  The $L$-dependent part of $C_\beta$ is
  $$\mathcal C(L) = \Pi u\,\mathcal M(L) + E\,L + bL + \beta_R\hat\kappa u\big[\mathcal M(L)R(L) + R_K(1 + cf)\big] + E_I\,L\,R(L) + (E^0_I - E_I)\,b_1L.$$
  (The last term is L0's own per-job busy
  charge: L0's merges run at read cost $R + R_K$ with per-job device time
  $c^0_{job}$, the $L - 1$ deeper levels' at $R$ with $c^d_{job}$, and
  $E_I(L-1)R + E^0_I(R + R_K) = E_ILR + (E^0_I - E_I)b_1L$ plus a constant.)
  Then:
  - (iv-a) $\mathcal C$ is strictly convex on $L > 0$ and has exactly one
    minimiser. It is the unique root of
    $$c\,f(\ln f - 1) = 1 + \frac{E + b\,\big(1 + \hat\kappa u\mathcal M(L)\big) - \beta_R\hat\kappa uR_K\,c\,f\ln f/L + E_I\big(R_0 + 2b_1L\big) + (E^0_I - E_I)b_1}{u\,\big(\Pi + \beta_R\hat\kappa R(L)\big)},$$
    an equation implicit in $L$, since $\mathcal M$, $R$ and $f$ all depend
    on it.
  - (iv-b) At $E = E_I = E^0_I = 0$, $c_{cr} = 0$, $\hat\kappa = 0$ and
    $\mathfrak w^B = \mathfrak w^J = 0$ it is the 2026-09-29 equation
    $c\,f(\ln f - 1) = 1 + b/A$, with $A = \beta_Wc_wu$.
  - (iv-c) *How each new term moves $f^\star$, at a fixed priority.*
    - The per-job cost ($E$) raises $f^\star$: fewer levels. So does the busy
      charge of the per-job device time ($E_I$, $E^0_I$) at every optimum
      with $L \ge 1/2$ (a tree has L0's merge level, so $L \ge 1$ in
      practice).
    - $c_{cr}$. Raising $c_{cr}$, from any value,
      raises $L^\star$ (lowers $f^\star$: more levels) if and only if, at the
      optimum before the raise,
      $$\beta_W(1 + \mathfrak w^J)\,\mathcal M'(L) + \beta_R\,\frac{\kappa^J}{p_{\mathrm{dev}}}\big[\mathcal M'R + \mathcal Mb_1 + R_Kcf'\big](L) < 0$$
      (with distinct types, $\kappa^J$ times the bracket becomes
      $\sum_x\kappa^J_x[\mathcal M'R_x + \mathcal Mb_{1,x} + R_{K,x}cf']$),
      and lowers it if the left side is positive. On the byte basis
      ($\kappa^J \equiv 0$) the condition is $\mathcal M'(L) < 0$, that is,
      the right side of (iv-a) exceeds 1 there: $c_{cr}$ then moves
      $f^\star$ the way a larger $c_w$ does, and without any interference
      this holds whenever $b + E > 0$. With $\kappa^J > 0$ it is not the
      condition: raising $c_{cr}$ also raises $\hat\kappa$, and the
      interference term $\mathcal M R + R_K(1 + cf)$ can move $L^\star$ the
      other way. The byte-basis condition alone is therefore not enough
      when $\kappa^J > 0$: in random draws of parameter sets it fails in
      about 1% of them, all with $\kappa^J > 0$, while the condition above
      holds in every draw (numerical checks, the check reports).
    - The per-byte interference ($\hat\kappa$) lowers it (more levels)
      whenever $b_1 > 0$ and $\Pi u\mathcal M < \beta_RR$ at an optimum
      without it with $L \ge 1/2$: the weighted merge write-side cost rate
      is below the weighted quiet read cost rate. On `Assoc` the quiet read
      cost is about 2.1 times the merge write cost (derived: critique Q2, 245
      s against 69.1 GB at 1.70 ns, provisional prices), so this holds in
      balanced and read modes.
  - (iv-d) *Priority.* Read priority raises $f^\star$ (fewer levels) exactly
    when, at the optimum, one more level raises the read side, quiet reads
    plus the read part of interference:
    $b_1 + \hat\kappa u\,\frac{d}{dL}\big[\mathcal MR + R_K(1 + cf)\big] + \frac{1}{\beta_R}\frac{d}{dL}\big[E_ILR + (E^0_I - E_I)b_1L\big] > 0$.
    Write priority moves $f^\star$ the other way. Without interference the
    condition is $b_1 > 0$, as in 2026-09-29. With interference it can fail:
    one more level can lower the interference more than it raises the probes.
  - (iv-e) *Strict read priority.* If some read-type $\kappa$ is positive, so
    that $\hat\kappa > 0$, then as $\beta_R/\beta_W \to \infty$, $f^\star$
    tends to the minimiser of
    $b_1L + \hat\kappa u[\mathcal MR + R_K(1 + cf)] + (E_ILR + (E^0_I - E_I)b_1L)/\beta_R$,
    which is finite. So $f^\star$ stays bounded, where without interference it
    grows without bound. In strict read priority, depth trades read steps
    against interference, not against writes.
  - (iv-f) At $b = 0$, $\hat\kappa = 0$, $E = 0$ and $c = 1$,
    $f^\star \approx 3.59$, with $B_L$ held fixed as in 2026-09-29. On a
    geometric ladder with fixed total data $D_{\text{tot}}$, $B_L$ moves with
    $f$: $B_L = D_{\text{tot}}(1 - 1/f)$. Then $f^\star$ is 3.27–3.43 for
    $D_{\text{tot}}/(K_0F)$ between 375 and $10^5$ (numerical check in the
    critique, `~/node_ops/reports/2026-10-03-1816-interference-theory-critique.md`,
    reproduced in the check reports).

*Proof.* (i) $W$: minimise $\sum f_i$ with the product fixed; by the AM–GM
inequality equality of the terms is necessary and sufficient. *Amended part.*
By Lemma D.18 with $\rho_i = 1$, $a_i = 1$ and $t_i = 0$, a level-$i$ merge
writes and reads $1 + cf_i$ bytes per user byte, and level $i$ completes
$1/\bar s_i$ merges per user byte. By (I1) the count does not depend on the
multipliers. A merge $\iota$ of level $i$ has $Y_\iota = \theta_B(S + O)$ and,
with $\rho_i = 1$, priced device time
$t^{job}_\iota = [c^{\langle i\rangle}_{job} + (c_w + c_{cr})(S + O)]/p_{\mathrm{dev}}$;
by (I2) its window's cost of each step type is $\bar\varrho^{\,x}_i$, so by D
§1 its priority-weighted charge is
$\bar q\sum_x\beta_x\bar\varrho^{\,x}_i\big(\kappa^B_x\theta_B(S + O) + \kappa^J_{x,\langle i\rangle}[c^{\langle i\rangle}_{job} + (c_w + c_{cr})(S + O)]/p_{\mathrm{dev}}\big)$,
a constant per merge plus $I^{O,\beta}_i$ per byte of $S + O$. Per user byte
this sums to a term that does not depend on the multipliers ($1/\bar s_i$
merges' constants, and $I^{O,\beta}_i$ times the one source byte), plus
$I^{O,\beta}_i\,cf_i$. The bytes read and written add
$\beta_W(c_w + c_{cr})(1 + cf_i)$, and the per-job prices a constant. With
(I3), the $f_i$-dependent part of
$\beta_W\sum_\iota\tau^{job}_\iota + \sum_\iota I^\beta_\iota$ is
$c\sum_i\psi_if_i$. The interior
multipliers range over all fanout vectors with the product of Corollary D.9.
In $y_i = \ln f_i$ the problem is to minimise $\sum_i\psi_ie^{y_i}$, a strictly
convex function, on the hyperplane $\sum_iy_i = \ln(B_L/(K_0F))$. Its unique
minimiser satisfies $\psi_ie^{y_i} = \lambda$ for every $i$, by the
Lagrange condition, so $f_i \propto 1/\psi_i$.
(ii) Lemma D.10: the read counts depend on the number of runs and the hit
position. Interior multipliers change the number of runs at levels $\ge 1$
only if a level empties, the hit position only by delaying descent, and $k_0$
only through $\delta_0$, directly through $m_1$ and through the shared
compaction slot. The amended channels follow from Lemmas D.18 and D.19 and
from the interference charge of D §1.
(iii) Lemma D.14.
(iv) *The cost.* Under (I1), (I2) and (I4), with $a_i = 1$ and equal
fanouts, every merge level has $1 + cf$ merged bytes per user byte. A merged
byte's write side is $\beta_W[(c_w + c_{cr}) + \mathfrak w^J(c_w + c_{cr}) +
\mathfrak w^B\theta_B]$ in expectation (D §1, the write part), so the merge
bytes cost $\Pi u\mathcal M$; each deeper merge level adds $u/\bar s$ merges,
whose per-job price and its write part give $E$ per level (L0's merges give
an $L$-free constant). The read part of the interference of a merged byte is
$\hat\kappa$ times the read cost rate of its window: $R$ for the $L - 1$
deeper levels and $R + R_K$ for L0's level, which gives
$\beta_R\hat\kappa u[\mathcal MR + R_K(1 + cf)]$; the per-job busy part gives
$E_I(L-1)R + E^0_I(R + R_K)$, which is the last two terms plus a constant;
and the quiet reads give $bL$ plus a constant. Let
$\varsigma = \ln f = \ln(B_L/(K_0F))/L > 0$ (a local letter, since $x$
indexes step types). As in 2026-09-29,
$\frac{d}{dL}(Lf) = f(1 - \varsigma)$, so $\mathcal M' = 1 + ce^\varsigma(1 - \varsigma)$, and
$\mathcal M'' = c\varsigma^2e^\varsigma/L > 0$. Also $f' = -\varsigma f/L < 0$ and
$f'' = f(\varsigma^2 + 2\varsigma)/L^2 > 0$. Then
$$\mathcal C' = \Pi u\mathcal M' + E + b + \beta_R\hat\kappa u\,(\mathcal M'R + \mathcal Mb_1 + R_Kcf') + E_I(R_0 + 2b_1L) + (E^0_I - E_I)b_1,$$
$$\mathcal C'' = u(\Pi + \beta_R\hat\kappa R)\,\mathcal M'' + 2\beta_R\hat\kappa ub_1\mathcal M' + \beta_R\hat\kappa uR_Kcf'' + 2E_Ib_1$$
(the linear term $(E^0_I - E_I)b_1L$ adds a constant to $\mathcal C'$ and
nothing to $\mathcal C''$).

- Where $\mathcal M' \ge 0$, every term of $\mathcal C''$ is nonnegative and
  the first is positive.
- Where $\mathcal M' < 0$, $\varsigma > 1$ and $0 < -\mathcal M' < ce^\varsigma(\varsigma - 1)$.
  Since $\Pi \ge 0$ and $R \ge b_1L$, the first term is at least
  $\beta_R\hat\kappa ub_1L\cdot c\varsigma^2e^\varsigma/L = \beta_R\hat\kappa ub_1c\varsigma^2e^\varsigma$.
  For every real $\varsigma$, $\varsigma^2 > 2(\varsigma - 1)$, because $(\varsigma-1)^2 + 1 > 0$. So the
  first term exceeds $2\beta_R\hat\kappa ub_1|\mathcal M'|$ when
  $\beta_R\hat\kappa b_1 > 0$, and when it is 0 the first term is still
  positive. The last two terms are nonnegative.

So $\mathcal C'' > 0$ for every $L > 0$.

- As $L \to 0^+$, $\varsigma \to \infty$, so $\mathcal M' \sim -c\varsigma e^\varsigma$, while the
  positive terms of $\mathcal C'$ grow at most like $e^\varsigma/\varsigma$
  ($\mathcal M \le c\ln(B_L/(K_0F))e^\varsigma/\varsigma + L$) or stay bounded ($E_I$'s
  and $E^0_I$'s), and $R_K$'s term is negative. So $\mathcal C' \to -\infty$.
- As $L \to \infty$, $\varsigma \to 0$, so $\mathcal M' \to 1 + c$, and
  $\mathcal M, R \to \infty$. So $\mathcal C' \to +\infty$.

Hence $\mathcal C'$ has exactly one root, the unique minimiser. Dividing
$\mathcal C' = 0$ by $u(\Pi + \beta_R\hat\kappa R) > 0$ and using
$\mathcal M' = 1 - cf(\ln f - 1)$ gives (iv-a). (iv-b) is substitution.

(iv-c) Let $\mathcal C_0$ and $\mathcal C_0 + \Delta\mathcal C$ be strictly
convex, with minimisers $L_0$ and $L_1$. Then $L_1 > L_0$ if and only if
$\Delta\mathcal C'(L_0) < 0$, and $L_1 < L_0$ if and only if
$\Delta\mathcal C'(L_0) > 0$, since
$(\mathcal C_0 + \Delta\mathcal C)'(L_0) = \Delta\mathcal C'(L_0)$ and
$(\mathcal C_0 + \Delta\mathcal C)'$ is increasing. Both models are strictly
convex, by the argument above with the term removed or its parameter changed.

- For $E$, $\Delta\mathcal C' = E > 0$. For the per-job busy charge,
  $\Delta\mathcal C = E_I(L-1)R + E^0_I(R + R_K)$ up to a constant, so
  $\Delta\mathcal C'(L_0) = E_I(R_0 + b_1(2L_0 - 1)) + E^0_Ib_1$, positive at
  $L_0 \ge 1/2$ when $E_I > 0$ or $E^0_Ib_1 > 0$ (since $R_0 > 0$).
- For $c_{cr}$: $c_{cr}$ enters $\mathcal C$ only through
  $\Pi = \beta_W[(1 + \mathfrak w^J)(c_w + c_{cr}) + \mathfrak w^B\theta_B]$ and
  $\hat\kappa = \kappa^B\theta_B + \kappa^J(c_w + c_{cr})/p_{\mathrm{dev}}$,
  both affine in it; $E$, $E_I$ and $E^0_I$ do not contain it. So raising it
  by $\delta > 0$ adds
  $\Delta\mathcal C = \delta u\big[\beta_W(1 + \mathfrak w^J)\mathcal M + \beta_R(\kappa^J/p_{\mathrm{dev}})(\mathcal MR + R_K(1 + cf))\big]$,
  whose derivative at $L_0$ is $\delta u$ times the stated left side. On the
  byte basis the bracket's second part vanishes and the sign is that of
  $\mathcal M'(L_0)$; by $\mathcal M' = 1 - cf(\ln f - 1)$ that is negative
  exactly when the right side of (iv-a) exceeds 1. Without interference,
  $\mathcal C_0'(L_0) = 0$ gives $\mathcal M'(L_0) = -(E + b)/(u\Pi)$, negative
  when $b + E > 0$. (The change of $\hat\kappa$ must not be dropped: with
  $\Delta\mathcal C' = \beta_W\delta u\mathcal M'$ alone the condition would
  be the byte-basis one, which fails when $\kappa^J > 0$.)
- For the per-byte interference, $\mathcal C_0$ has no $\hat\kappa$ term, so
  $\mathcal C_0'(L_0) = 0$ gives
  $\mathcal M'(L_0) = -(E + b + P_I)/(u\Pi)$, with
  $P_I = E_I(R_0 + b_1(2L_0 - 1)) + E^0_Ib_1 \ge 0$ at $L_0 \ge 1/2$. Then
  $\Delta\mathcal C'(L_0) = \beta_R\hat\kappa u\big[\mathcal Mb_1 - (E + b + P_I)R/(u\Pi) + R_Kcf'\big]$.
  Here $R_Kcf' \le 0$, and $E$ and $P_I$ are nonnegative. If $b_1 > 0$ and
  $\Pi u\mathcal M < \beta_RR$, then $\mathcal Mb_1 < bR/(u\Pi)$, and so
  $\Delta\mathcal C'(L_0) < 0$.

(iv-d) By the implicit function theorem, with $\mathcal C'' > 0$,
$dL^\star/d\beta_R = -\partial_{\beta_R}\mathcal C'/\mathcal C''$, and
$\partial_{\beta_R}\mathcal C' = b_1 + \hat\kappa u\,(\mathcal M'R + \mathcal Mb_1 + R_Kcf') + (E_I/\beta_R)(R_0 + 2b_1L) + ((E^0_I - E_I)/\beta_R)b_1$
is the stated condition. $\mathcal C$ is linear in $(\beta_W, \beta_R)$
($\Pi$ and $E$ carry $\beta_W$; $b$, $\beta_R\hat\kappa$, $E_I$ and $E^0_I$
carry $\beta_R$), so $\mathcal C' = \beta_W\partial_{\beta_W}\mathcal C' + \beta_R\partial_{\beta_R}\mathcal C'$,
and at the optimum $\partial_{\beta_W}\mathcal C' = (\Pi u\mathcal M' + E)/\beta_W$
equals $-\beta_R\,\partial_{\beta_R}\mathcal C'/\beta_W$, so it has the
opposite sign.

(iv-e) Divide $\mathcal C$ by $\beta_R$. As $\beta_W/\beta_R \to 0$ its
derivative converges, uniformly on compact sets of $L$, to the derivative of
the stated limit function. That function is strictly convex with a finite
minimiser, by the argument above with $\Pi = E = 0$ (the first term of
$\mathcal C''$ is then $\beta_R\hat\kappa uR\mathcal M'' > 0$, since
$\hat\kappa > 0$), using $R_0 > 0$ as $L \to 0^+$. The limit derivative
crosses zero with positive slope, so the roots converge to that minimiser.

(iv-f) At $c = 1$, $b = 0$: $3.59\,(\ln 3.59 - 1) = 1.00$. $\blacksquare$

*Consequences.* Everything in (i)–(iv) is a static choice: an interior
profile, a depth fixed by base size at load time, and $K_0$ at fixed prices.
All of it belongs to the static comparator (Corollary C.3). Depth cannot be
reduced during a run (Lemma A.6), so (iv) is a design and comparator result
only. The 2026-09-11 Corollary A.4 ($f^\star \approx 3.59$ under M3) is the
special case $c = 1$, $b = 0$, with $B_L$ held fixed. The optimum moves with
the measured $c$, the priority, the per-job cost and interference. A
numerical check of (iv-a) on illustrative magnitudes finds, in read priority
at $\beta^\star = 10$, $f^\star$ falling from about 29 to 16 with
interference (28.8 to 16.4): more levels, as (iv-c) says. In random
parameter sets, $\mathcal C$ was strictly convex with one stationary point
and the root of (iv-a) agreed with the direct minimiser; with the write part
and $\kappa^J > 0$, the $c_{cr}$ condition of (iv-c) held in every draw,
while the byte-basis condition alone failed in about 1% of them; and
leaving out L0's own busy term $(E^0_I - E_I)b_1L$ moved $L^\star$ by a
median 0.3% and at most 5.2% (all in the check reports). These checks
illustrate the proof; they are not part of it.

*Without (I4).* If $\kappa^J_{x,0} \ne \kappa^J_{x,d}$, L0's merge level,
which runs at read cost $R + R_K$, carries the per-byte read charge
$\hat\kappa_0$ instead of $\hat\kappa_d$, which adds
$\beta_Ru(\hat\kappa_0 - \hat\kappa_d)(1 + cf)(R + R_K)$ to $\mathcal C$ (and
$E^0_I$ is computed with $\kappa^J_{x,0}$); a kind-dependent $\kappa^J_{put}$
likewise adds $\beta_Wu(\mathfrak w^J_0 - \mathfrak w^J_d)(c_w + c_{cr})(1 + cf)$.
Both are convex in $L$ when their coefficient is nonnegative: $1 + cf$ and
$R_K(1 + cf)$ are convex since $f'' > 0$, and $(1 + cf)R$ is strictly convex
on $L > 0$, its second derivative being
$(cf\varsigma/L^2)[(2 + \varsigma)R_0 + \varsigma b_1L] > 0$. So when
$\kappa^J_{x,0} \ge \kappa^J_{x,d}$ for every $x$, (iv-a)'s strict convexity
and unique minimiser survive, with the root equation taken from the full
$\mathcal C' = 0$; (iv-c)–(iv-e) are not re-derived for that case. When
$\kappa^J_{x,0} < \kappa^J_{x,d}$ convexity is not established.

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

*Checked 2026-10-03 (D-23).* The bound is about bytes, and the new cost terms
do not change it. They change what it covers. Held garbage now also costs
reads, through hidden steps and their blocks (Lemma D.19). Neither $S$ nor this
bound limits that cost (Lemma D.19(iv)), so the bound covers only the space
part of expansion's cost.

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

*Checked 2026-10-03 (D-23).* The lemma is about space and is unchanged. Its
argument is used in three new places:

- Lemma D.19(i) rests on the same fact, that compaction never changes a
  read's result, to show that the entries a scan returns do not depend on the
  policy.
- The memtable searches, one per Get and per scan, are fixed by the operation
  sequence exactly, so the memtable bucket's search cost (§4) does not depend
  on the policy; so are the fixed parts of Gets and scans and the Puts'
  memtable inserts, the fixed bucket (§4). Which Gets reach the tables, and which hidden versions sit in
  the memtables, carry this lemma's flush-timing caveat.
- The reason for pricing space per operation served applies equally to the
  rate-based part of the interference charge, which is converted at $\bar q$
  (Lemma D.17).

### §4 Per-level attribution

The agents of Pathway H each need their own share of the cost. Shared
*buckets* hold the costs that no level causes; no agent is rewarded on them.
Since 2026-10-03 (D-23) there are five: hit-read, reopen, scan-base, memtable
and write-path, and a sixth, *fixed*, for the fixed parts and the Put
inserts.

- **Writes and background jobs** (amended 2026-10-03, D-23) are charged to
  the source level of the job that caused them (the job's start level, not
  RocksDB's per-level compaction statistics, which are keyed by output
  level). The charge is the job's whole priced cost $\tau^{job}_\iota$ (D §1),
  made at its completion: its per-job price $c^{\mathrm{kind}(\iota)}_{job}$,
  its compaction bytes read at $c_{cr}$, and its bytes written at $c_w$.
  - A merge or trivial move goes to the level it is sourced at, an intra-L0
    compaction to L0, and a last-level self-compaction to the last level.
  - A flush goes to L0, as flush writes always have. (A flush's
    $\tau^{job}_\iota$ does not depend on the policy, so it could equally go
    to the write-path bucket; it stays with L0, where OBJ-1 has always
    counted flush bytes.)
  - A trivial move's $\tau^{job}_\iota$ is $c^{tm}_{job}$ alone (it reads and
    writes no bytes); its interference charge is attributed below.

  This is exact.
- **Interference** (new 2026-10-03, D-23) is priced on the victim's side —
  its read part inside $\mathcal C_R$, its write part, on Put inserts,
  inside $\mathcal C_W$ — and attributed to its cause. At job
  $\iota$'s completion, its charge $I_\iota$ (D §1), its read and write parts
  with their weights, goes:
  - to the job's start level, for merges, trivial moves and self-compactions;
  - to the shared *write-path bucket*, for flushes. A flush's volume does not
    depend on the policy, and its charge is set by the read cost of every
    level during the flush, so no one level causes it. Its value does depend
    on $K_0$: a flush runs while L0 holds $0..K_0 - 1$ files (Proposition
    D.11), so a higher trigger raises the read cost the flushes' windows see.
    No agent pays for that part (H §3).

  The reverse effect, foreground work (reads and Puts) slowing jobs, is
  inside the in-situ job prices (A11) and is not charged again. $I_\iota$ is computed from the cumulative
  foreground-step counters that the host log writes with the job's begin and
  end records and every $n^{\mathrm{str}}$ operations (D §1). The attribution log
  and the evaluator must use these same inputs for OBJ-1 to check the
  identity. The sum is exact by definition of $\mathcal I$; that it prices the
  physical slowdown holds within A10 (Proposition D.16, OBJ-8). The split
  between levels depends on $\kappa$'s basis: under the charge of D §1 the
  byte part weighs a level's merges by their bytes $Y_\iota$ and the busy
  part by their priced device time, both at the read cost of their windows,
  while the physical bases would weigh levels by measured job speeds and
  counts, by factors of up to about 20 (critique Q4; 11.7 and 21.0 at L1 and
  L2 against L0, the $\bar q$ arm's repeat-1 event log).
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
- **Reopens** (D-21) are charged to the level where they happen, like the
  probes and seeks that make them: the fork counts each table a Get or a user
  iterator reopens, keyed by the level the read passes in (`FilePicker`'s hit
  level, the iterator's level), at $c_{open}$. A reopen of no known level
  goes to a shared *reopen bucket* that no agent is rewarded on; on the Get
  and iterator paths there are none. Until D-21 every reopen went there
  (D-20), and the controller priced a probe at $c_f$ without them.
- **Scan iteration** (new 2026-10-03, D-23; the split rules fixed by the
  integration decisions of the same day). Take a step with $r$ children in
  the heap, $r^{\mathrm{L0}}$ of them L0 files and $r^{\neg0} = r - r^{\mathrm{L0}}$
  the others (memtables and level iterators), and let
  $r^- = \max(r^{\neg0}, 1)$. Split its price (Lemma D.10) into three parts
  that telescope to it:
  $$c_{st}(r) = \underbrace{c_{st}(1)}_{\text{base}} + \underbrace{c_{st}(r^-) - c_{st}(1)}_{\text{non-L0 increment}} + \underbrace{c_{st}(r) - c_{st}(r^-)}_{\text{L0's increment}}.$$
  - *L0 last.* L0 pays its increment $c_{st}(r) - c_{st}(r^-)$ on every step,
    returned or hidden: the heap cost its files add when they are added after
    every other child. L0's own action, its trigger, sets how many there are.
  - *A returned step* puts its base in the shared *scan-base bucket* (the
    number of such steps, $R_{nx}$, does not depend on the policy, Lemma
    D.19(i)) and splits its non-L0 increment equally among its $r^{\neg0}$
    non-L0 children: each level iterator's share to its level, each
    memtable's to the *memtable bucket*. If $r^{\neg0} = 0$ the non-L0
    increment is 0.
  - *A hidden step* is charged its base and its non-L0 increment together,
    $c_{st}(r^-)$, to the level directly above the level where the hidden
    entry lives: an entry in level $i \ge 1$ to level $i-1$ (to L0 for an
    entry in L1), an entry in an L0 file to L0, and an entry in a memtable to
    the memtable bucket. Its L0 increment $c_{st}(r) - c_{st}(r^-)$ stays with
    L0, as on every step. The fork counts hidden steps by the entry's level
    (OBJ-9), and the evaluator maps each count to the charged level.
  - Each iterator block goes, at $c_{ib}$, to the level of the table it
    belongs to.

  *The shares sum exactly.* A returned step is charged
  $c_{st}(1) + r^{\neg0}\cdot\frac{c_{st}(r^-) - c_{st}(1)}{r^{\neg0}} + c_{st}(r) - c_{st}(r^-) = c_{st}(r)$
  (with the middle term 0 when $r^{\neg0} = 0$, since then $r^- = 1$); a
  hidden step $c_{st}(r^-) + c_{st}(r) - c_{st}(r^-) = c_{st}(r)$. So every
  step's price is charged once, in full, whatever the price function. (A
  numerical check of 2026-10-03 over 40,000 random steps, with logarithmic
  and affine prices, found every sum exact.)

  *Why L0 last.* $c_{st}(r^-)$ does not contain $r^{\mathrm{L0}}$, so one
  more L0 file in the heap changes L0's charge by exactly the change of the
  step's price, and no other place's charge, on every step, returned or
  hidden (Lemma H.5(ii)). An equal split of the whole increment among the $r$
  children would let an L0 action move every level's share and would
  misprice L0's own effect by factors of 0.9–2.1 under a logarithmic price
  (Lemma H.5(iv)). Among the non-L0 children the equal split is the Shapley
  value of their increment, seen as a game in which the cost depends only on
  how many children are present; L0 last as a whole is the
  marginal-contribution split for the order that adds L0's files after every
  other child. Neither is a counterfactual for a level $\ge 1$: removing one
  level's child would change the step's price by $c_{st}(r) - c_{st}(r-1)$,
  not by its share.

  *Why the level above, and what the rule does to incentives.* A hidden
  entry disappears only in a merge that also holds a newer version of its
  key. With one version per key at each level $\ge 1$ (A2), that is a merge
  sourced at the level directly above the entry's level, once the newer
  version has reached it (an L0 merge for an entry in L0 or L1). So the level
  above is the only level whose decisions remove the entry, which is also
  why the space charge $g_i$ below goes to the shadowing level. Consider a
  release by level $i \ge 1$ into level $i+1$, and weigh each hidden entry by
  the hidden steps scans take over it. Let $D^{hd}$ be the hidden entries of
  level $i+1$ that the release drops (their newer version is in the released
  files), and $M^{hd}$ the hidden entries of level $i$ that it carries into
  level $i+1$, where they stay hidden. Then:
  - the global count changes by $-D^{hd}$;
  - level $i$'s charge changes by $-D^{hd} + M^{hd}$: it is credited the
    entries it drops, and takes over the ones it carries down, which only its
    own later releases can remove;
  - level $i-1$'s charge changes by $-M^{hd}$, and no other level's changes.

  So no level can lower its own charge by pushing hidden entries down:
  pushing them adds them to its own charge. Under the alternative rule
  (charge the level where the entry lives), the same release would change
  level $i$'s charge by $-M^{hd}$, crediting it for passing entries
  down and not for those it dropped. What remains is a neighbour effect:
  level $i$ is charged $M^{hd}$ more than the global change, and level $i-1$
  is credited $M^{hd}$ without acting. The neighbour charge $X_{i-1}$ of H §3
  removes it: its one-step prediction includes the hidden entries a release
  carries down, estimated from the counter keyed by the entry's level, so the
  removal is as exact as that estimate and the neighbour's value function
  (H §3, ARCH-3). For L0 nothing remains: an L0 merge drops the hidden
  entries of L0 and L1 that its own files shadow and carries into L1 the L0
  entries that memtable versions hide, which stay charged to L0, so L0's
  charge changes by exactly the global change. The last level no longer pays
  for old versions it cannot remove; level $L-1$, whose releases remove them,
  does. One difference from $g_i$ remains: $g_i$ counts only garbage in level
  $i+1$ shadowed by level $i$ itself, while the hidden-step charge counts
  every hidden entry of level $i+1$, wherever its newer version is.

  This needs counters, aggregated across threads (Gate N0, D-23): hidden steps
  by the level of the hidden entry, iterator blocks per level, and, per step,
  $r^{\mathrm{L0}}$ and $r^{\neg0}$, by step type (returned, or hidden with its
  entry's level), with the levels of the non-L0 children of returned steps.

  *Remark (blocks of returned entries).* The blocks loaded while stepping past
  returned entries go to the level of their table. Like a Get's hit read, they
  would be read somewhere whatever the tree (Lemma D.19(v)), so this charges a
  level for holding the data scans return. The part that no policy changes,
  the blocks the returned entries alone would need, cannot be separated from
  the part the policy causes without a model of where those entries sit
  (Lemma D.19(v) is one). An alternative rule would split the block loads by
  the type of step that triggered each (every load after the seeks is
  triggered by exactly one step) and send the loads of returned steps to the
  scan-base bucket; it needs a flag the iterator sets per load, and it is not
  adopted.
- **Memtable search** (new 2026-10-03, D-23): $c_{mt}$ per Get and per scan
  goes to the *memtable bucket*, together with the memtables' shares of
  returned steps' increments and the hidden steps over memtable entries. The
  number of searches does not depend on the policy (Lemma D.15).
- **Fixed parts and Put inserts** (amended 2026-10-03, D-23): $c^0_{get}$ per Get,
  $c^0_{sc}$ per scan and $c_{put}$ per Put go to the *fixed bucket*, its
  read part weighted $\beta_R$ and its write part $\beta_W$. Their counts are
  fixed by the operation sequence (Lemma D.15), so no level causes them. The
  interference on these steps is part of each job's charge and goes with it
  (above).
- **Slot blocking.** There is one compaction slot (G.4). While L0 is due
  (score at least 1) but cannot compact because a job sourced at level
  $i \ge 1$ holds the slot, the share $(k_0 - K_0)^+/k_0$ of L0's filter probes,
  false-positive block reads and seeks — the files beyond its trigger — is
  charged to level $i$ instead of to L0, for every operation served in that
  span, with the same share of L0's reopens (D-21). The rule moves cost between levels and leaves the total unchanged. It
  is an attribution rule, not an exact counterfactual: had the slot been free,
  flushes arriving during L0's own merge would still have added files. It is
  the channel through which an interior level changes read cost during a run
  (Proposition D.13(ii)'s $\delta_0$), and it lets interior agents in read
  priority learn to keep the slot free while L0 is due or about to be. The
  share is exact in expectation when every L0 file covers the key
  (Lemma D.8's uniform-key L0 files).
  *Amended 2026-10-03 (D-23).* The same share also moves L0's hidden-step
  charge (entries in L0 and in L1, above), its iterator blocks and its heap
  increment. All three grow with the number of L0 files. For L0's own hidden
  entries and its blocks the share is exact in expectation when every L0 file
  holds a stepped-past key with the same probability (Proposition D.11); for
  the L1 entries that L0's files hide it is an approximation. For the heap the
  share moves that fraction of L0's increment $c_{st}(r) - c_{st}(r^-)$;
  under a concave step price the excess files' own marginal is smaller than
  that fraction, so there the share is an attribution rule, not exact in
  expectation. The rule does not move interference. The job holding the slot
  is charged its own $I_\iota$ in full, and that charge already includes the
  surcharge on the excess L0 reads in its window. The L0 merge that runs
  after the wait, at $k_0 > K_0$, carries more bytes, more read bytes and a
  larger $I_\iota$; L0 pays them, and the rule does not move them. At the
  default point slot blocking had no occasion: in the seven native runs of
  §0.7, and in all 23 of their cells, no job sourced at a level $\ge 1$ ran
  while L0 held two or more files (§0.7), so none held the slot while L0
  was due.
- **Space:** level $i$ is charged its *shadowed garbage*
  $g_i = (1 - \hat{\tilde\rho}_i)B_i$ at the rate $c_sg_i\,q/\bar q$ (§1): the
  bytes its next compactions would drop at the recent net pass-through
  $\hat{\tilde\rho}_i$ (trivial moves drop nothing). This is an estimate, and it
  counts only garbage in level $i+1$ shadowed by level $i$. A version two or
  more levels below its next-newer version is dropped only after that version
  descends, and no $g_k$ counts it, so $\sum_ig_i$ undercounts resident garbage
  whenever upper-level keys also live deeper than the next level. Live bytes are
  not attributed: they are the same under every policy (Lemma D.15).
  Garbage also costs reads (Lemma D.19). That cost is charged on the read
  side, through the hidden steps and blocks above, not through $g_i$; the
  hidden steps over level $i+1$'s entries go to level $i$, as $g_i$ does.

**Proposition D.16 (decomposition; amended 2026-10-03, D-23; the fixed
bucket and the write part added the same day).** Work within the
model of D §1–§2:

- prices are fixed (A9);
- each job's interference charge is D §1's $I_\iota$, computed from the
  foreground-step counts of its window $W_\iota$, its read part in
  $\mathcal C_R$ and its write part in $\mathcal C_W$;
- every job is charged at its completion.

Then, over any partition of the measured phase into intervals, the per-level
charges of this section plus the six shared buckets (hit-read, reopen,
scan-base, memtable, write-path, fixed) sum exactly to the global write and
read costs, $\mathcal C_W$ and $\mathcal C_R$, every term included; weighted,
each part with its own weight, they sum to
$\beta_W\mathcal C_W + \beta_R\mathcal C_R$. For space, $\sum_i g_i$
counts only adjacent shadowing and so undercounts total garbage; it is
reported against measured garbage at run end (OBJ-3).

*Proof.* Go term by term. Each priced event is charged in full to exactly one
place, or split among places in shares that sum to 1. The charges therefore
sum to the costs by linearity.

- *Jobs.* Every job completes once and has one start level, or is a flush,
  charged to L0. So its $\tau^{job}_\iota$, with its per-job price, read bytes
  and written bytes, is charged exactly once, in the interval of its
  completion, where $\mathcal C_W$ counts it (D §1).
- *Quiet reads.* Every probe, seek and false-positive block read happens at
  exactly one level. It is charged in full there or, under slot blocking,
  split between L0 and one other level in shares that sum to 1, and so is
  every reopen at a known level. Every hit's block read goes to the hit-read
  bucket, and every other reopen to the reopen bucket.
- *Scan iteration.* Each step's price splits into its base $c_{st}(1)$, its
  non-L0 increment $c_{st}(r^-) - c_{st}(1)$ and L0's increment
  $c_{st}(r) - c_{st}(r^-)$, which telescope to $c_{st}(r)$ (§4). L0 always
  pays its own increment, on every step, returned or hidden. For a returned
  step the base goes to the scan-base bucket and the non-L0 increment in
  $r^{\neg0}$ equal shares to the places of the non-L0 children; for a hidden
  step the base and the non-L0 increment together, $c_{st}(r^-)$, go to the
  one place the entry's level determines, the level directly above it
  (§4). Every iterator block
  goes to its table's level. Slot blocking moves shares that sum to 1.
- *Memtable search.* Every search goes to the memtable bucket.
- *Fixed parts and Put inserts.* Every Get's and scan's fixed part goes to
  the fixed bucket's read part, which $\mathcal C_R$ counts, and every Put's
  insert to its write part, which $\mathcal C_W$ counts.
- *Interference.* $\mathcal I = \sum_\iota I_\iota$ by definition (D §1), and
  each $I_\iota$ goes, its read and write parts together, to exactly one place at its job's
  completion: its start level, or the write-path bucket for a flush. Its
  read part is counted in $\mathcal C_R$ there and its write part in
  $\mathcal C_W$, as the global costs count them. Each job carries its own
  charge, so with concurrent jobs nothing is counted twice or missed.
$\blacksquare$

*What the decomposition does not cover.*

- *Departures from the model.* These are errors in how well $J_\beta$ prices
  the device time, not in its decomposition:
  - the physical slowdown's departure from the charge: A10's linearity and
    additivity (flush–compaction non-additivity included), the conversion to
    the reference rate, and the window's averaging (D §1, Lemma D.17(iii));
    OBJ-8 tests A10 on every run for the timed read steps, and the
    calibration (OBJ-2) for the rest;
  - any dependence of prices on the configuration beyond what the per-run
    checks catch (A9, A11).
- *The drain.* It serves no operation, so it has no read charges. Its jobs'
  $\tau^{job}_\iota$ are charged to their start levels as usual, and so are
  their interference charges, over the last $n^{\mathrm{win}}$ operations of
  the phase (D §1); OBJ-1 runs to the end of the drain. A controller arm
  drains under its fallback settings (D §1), so these charges follow from no
  agent's decision taken during the drain.
- *Unpriced quantities* (D §1, OBJ-6), outside P-1's scope: stalls,
  throughput and latency, controller CPU, the OPTIONS file, the client's own
  work, and the trainer process (it runs only in learner arms and shares the
  machine, but appears in no job record and no price, so its effect on reads
  and jobs is outside $J_\beta$).
- *Space*, where the per-level charge is an estimate (OBJ-3).

Two further limits. The heap split, the hidden-step rule and the
slot-blocking rule are attribution rules: the decomposition is exact as a
sum, but one level's share is not the causal effect of that level (§4 says
how far each departs from it). And the per-level split of interference
depends on $\kappa$'s basis, while the sum is exact for whichever basis
$J_\beta$ uses.

### §5 Room by mode: predictions (revised 2026-10-03, D-23, before any Gate N2 run)

The predictions recorded with D-13 (this section as committed with that entry,
2026-09-30) are revised here, before any Gate N2 run and before any $\Theta_s$
run. The reason is that the cost now prices per-job overhead, compaction
reads, scan iteration, memtable search and interference (D-23). The 2026-09-30
text stays in git at D-13's commit. The magnitudes below are provisional.
Four decisions D-23 left open can change them; D-24 has taken them (§0.7
items 1–4), but their outcomes (κ's basis, the per-workload base read
prices, `Assoc`'s measured $c_{open}$, the tolerances) wait for the
calibration. Where a magnitude is unknown, the prediction is stated as a
direction.

- **Read priority.**
  - *Static levers, re-priced.*
    - The L0 trigger's optimum rises and has a floor, positive whenever
      interference on reads is ($A_I > 0$; Proposition D.11(ii)). So read priority lowers the trigger less than the 2026-09-29
      formula said, the read-priority and balanced optima move closer, and
      WL-2 is harder to pass for read priority.
    - Depth: in strict read priority $f^\star$ stays bounded (Proposition
      D.13(iv-e)), and interference favours more levels in balanced and read
      modes (D.13(iv-c)).
    - Scan iteration is now priced, and on `Assoc` it is probably the largest
      read cost that depends on the configuration: an estimate, from §0.7's
      unpriced client time (about 200 s of configuration-dependent time
      outside Get, Seek and write calls, against about 88 s of priced
      $\mathcal C_R$ at T = 2), not a result of Lemma D.10. Hidden steps per
      returned entry fall from 1.15 at $T$ = 2 to 0.23 at $T$ = 10
      (Lemma D.19). So read priority's comparator on `Assoc` is expected at
      the larger $T$.

    All of these are static and belong to $\Theta_s$ (Corollary C.3).
  - *Dynamic levers.*
    - (e), the L0 agent compacting early while the slot is idle and reads are
      heavy (Pathway A §4). With a closed-loop client on a stationary
      workload, every moment has the same operation mix. So an early merge at
      $k_0 = k < K_0$ repeats the cycle of a lower trigger, and Proposition
      D.11 prices it (Proposition A.8). It pays $c^0_{job}$, the extra overlap bytes at
      $\beta_W(c_w + c_{cr})$, and the merge's interference, its read part at
      $\beta_R$ (its write part at $\beta_W$), against the L0 read cost it
      saves. In read priority the interference is
      weighted by $\beta^\star$, so (e) is worth less than before and can
      reverse. Prediction: it does not beat the best static trigger on
      stationary `Assoc`.
    - (f), interior levels keeping the slot free while L0 is due. It had
      no occasion at the default point: no job sourced at a level $\ge 1$
      ran while L0 held two or more files in the seven native runs of §0.7
      (nor in all 23 of their cells). Prediction:
      no measurable gain there.
    - Timing compactions to moments when reads are light has no room on a
      stationary closed-loop workload. The client is never idle except in
      stalls, and a job run in a stall is charged at the read cost of the
      operations before it (D §1), so even a stall is not a light moment for
      the charge (Pathway B).
  - *Prediction.* Little or no room in read priority on stationary `Assoc`
    beyond the static comparator. Room is expected mainly on phased or
    changing workloads (Corollary D.12). This strengthens the 2026-09-30
    prediction.
- **Write priority.**
  - The write cost now includes the per-job cost and compaction reads, and
    trivial moves are no longer free. Per-job costs are large: on `Assoc` at
    $T$ = 2 the per-job gaps total more than the counted compaction time
    (45.7 s against 35.8 s; §0.7). Job counts are set by the bytes that flow
    and by job sizes (Lemma D.18). The file size and the picker fix the job
    sizes, which no action changes; actions change job counts only through
    the bytes that flow. So write priority's
    comparator is expected among configurations with fewer, larger jobs:
    larger $T$, and a larger $K_0$, whose optimum rises with
    $c^0_{job}$ (Proposition D.11). That room is static.
  - A static profile still gains $O((1-\eta)^2)$ in bytes (Theorem A.2(iii),
    where $\eta$ is a ratio of merged volumes, not merge survival). Its
    weights are now per-level prices, not equal (Proposition D.13(i)).
  - Holding a level so its merges drop more garbage (lever (c)) is still
    bounded in bytes by Proposition B.1″ and its native-relative form
    (Pathway B §3). A byte dropped at a stage now also saves the later
    stages' read bytes, their share of jobs and their interference, so each
    dropped byte is worth a little more than its write cost. On `Assoc`
    garbage is not scarce in the measured phase, since every Put overwrites,
    but native compaction already drops 77–92% of it, much of it high in the
    tree (merge survival $X/(S+O)$ at L1 0.84–0.93, D-4). First overwrites
    meet their loaded versions in the deepest levels, where a drop saves the
    fewest later stages. The room is what holding a level adds to high drops
    beyond native; Gate N3 measures it.
- **Space priority.**
  - On `Assoc`, $S$ is 1.03–1.09 (D-3; 1.04–1.11 at run end on Gate N1's
    pilots, §0.7), so garbage is $(S-1)/S$ = 3–8% (4–10%) of held bytes. Even
    a policy that held no garbage at all could remove no more than that from
    the space term.
  - Garbage now also costs reads (Lemma D.19), and on `Assoc` that cost is
    likely the larger one. Suppose a hidden step costs only 10 ns. Then
    `Assoc`'s 114M–570M hidden steps per 26.1M operations cost 1.1–5.7
    core-seconds (Gate N1's pilots, T = 10 to T = 2). Holding the 0.13–0.32 GB
    of garbage the same runs held at their end, for the same operations at
    $\bar q$ (about 380 s), costs 0.09–0.22 core-seconds, at D-15's 6.48
    core-seconds per GB-hour. So in space priority, removing
    garbage on hot keys also lowers the read cost, and space and read pull
    the same way on `Assoc`'s scans.
  - *Prediction.* The space term's room on `Assoc` stays small.
    Space-priority optima on `Assoc` move toward the configurations that hold
    the least hot-key garbage. High-garbage workloads are still needed for a
    space claim.

### §6 Acceptance

| # | Criterion | Threshold | Instrument |
| --- | --- | --- | --- |
| OBJ-1 | Flow identity (Lemma D.1, Proposition D.16; amended 2026-10-03, D-23) | on every arm that runs the plugin: the attribution log's per-level write charges — bytes written, compaction bytes read and jobs by kind, trivial moves included (flushes to L0, each compaction and trivial move to its start level, by completion time) — summed over levels and over every interval from $n_w$ to the end of the drain — intervals excluded from replay included — equal the evaluator's for the same window, with the log opening a partial interval for every level at $n_w$ and closing one at the end of the drain; any residual is listed by job and must consist only of jobs whose completion the two sources place on different sides of a boundary stamp. The log's per-level read counts — the probes, block reads, seeks and reopens, and the scan counts of OBJ-9 (hidden steps by the hidden entry's level, iterator blocks per level, steps by L0-file and other heap children) — taken before the slot-blocking rule moves any of them and with each level's hit block reads logged apart from its false-positive ones, equal the per-level counters' totals over the same window (their difference between the $n_w$ stamp and the end of the drain). The log's interference charges equal, job by job and part by part (read and write), the evaluator's $I_\iota$ computed from the same host-log job and flush records and counter snapshots (one estimator for both), and sum to $\mathcal I$. After the slot-blocking rule and the attribution of interference, of the hidden steps (to the level above the entry's) and of the heap increments (L0 last) (§4), per-level read charges plus the shared buckets' read parts (hit-read, reopen, scan-base, memtable, write-path, fixed) still sum to the global read cost $\mathcal C_R$, and the per-level write charges, the write part of interference included, plus the write-path and fixed buckets' write parts, to $\mathcal C_W$. Space is not checked here: the per-level charge is a garbage estimate (OBJ-3) | attribution log, event log, host log |
| OBJ-2 | Prices calibrated (amended 2026-10-03, D-23) | all prices in money: device times for $c_w$, $c_{cr}$ and the per-job price of each kind ($c^F_{job}$, $c^0_{job}$, $c^d_{job}$, $c^{tm}_{job}$), fitted to the jobs' spans on the reference arms (D-23); $c_f, c_{blk}, c_{sk}, c_{open}$ measured quiet on the node (D-15 §3, D-20, D-22); $c_{st}(r)$ and $c_{ib}$ on price trees run with `seek_nexts` > 0 at several depths and garbage levels, and $c_{mt}$ (D-23); the fixed parts $c^0_{get}$, $c^0_{sc}$ (as §1.1 defines them; $c^0_{sc}$ from the scan set-up timer, Gate N0 item 10) and the Put insert $c_{put}$, separated from the client's own work and from $c_{mt}$ ($c_{wal}$ = 0 while the WAL is off, `wal1`); the interference coefficients $\kappa$, those of the Put insert and the fixed parts included, their basis and the bytes $Y_\iota$ they use, from a calibration that also tests A10's linearity (a rate sweep) and additivity (one job against two) (D-23), and the window length $n^{\mathrm{win}}$ (D §1); the new prices and $\kappa$ all calibrated on the one binary identity of CMP-9, and the existing prices re-checked on it; all converted at the instance price $p_{\mathrm{dev}}$. Every run's own time per reopen within D-21's tolerance of stage 18's reference (10%; otherwise the run is not priced), the reference multiplied by $1 + \bar s_{open}$ once a dated entry adopts that proposed change (D §1, §0.7), and every run passing OBJ-7 and OBJ-8; the storage price $c_s > 0$, both money prices fixed in advance (§0.6), and $\bar q$ per workload, in the fingerprint; every price and $\kappa$ fixed before any Gate N2 outcome is seen; re-measured on any hardware or binary change; every result also reported at $c_s/2$ and $2c_s$ | calibration script |
| OBJ-3 | Space attribution | $\sum_ig_i$ and measured garbage at run end reported per arm. $\sum_ig_i$ targets only adjacent shadowing and undercounts (§4), so the criterion is on differences: across $\Theta_s$ at Gate N2, the ratio's spread and the slope of $\Delta\sum_ig_i$ on $\Delta$(measured garbage) are reported, and a tolerance on the ratio is fixed from them before Gate N4; at Gate N4 the learner's paired $\Delta\sum_ig_i$ against its comparator must agree in sign with $\Delta$(measured garbage) whenever that difference's paired interval excludes zero, and its ratio must lie within that tolerance | reference compaction, event log |
| OBJ-4 | Per-level read counters | per-level counters taken at the sites of §4 (the version's level: `FilePicker` hits in `Version::Get`; seeks per L0 file and per level iterator), so that a trivially moved file's reads are charged to its new level; block reads split into the hit's read and false-positive reads (§4); the reads' reopens counted and timed in `TableCache::FindTable` at the level the read passes in, Gets and iterators apart (D-21); their sums match the global counters within 1% (the read half of Proposition D.16). Amended 2026-10-03 (D-23): the scan counters of OBJ-9 (hidden steps keyed by the hidden entry's level, iterator blocks, heap child counts) are taken at the same sites and obey the same 1% rule, and the foreground-step counters of every priced type (read steps by type, and the counts of Gets, scans and Puts), cumulative, are written into the host log's begin and end records of every job and flush and every $n^{\mathrm{str}}$ operations (OBJ-8) | per-level counters, tickers, host log |
| OBJ-5 | Mode recorded | $\beta$, mode and $\bar q$ in every arm's manifest and fingerprint | `metadata.env` |
| OBJ-6 | Unpriced time reported (amended 2026-10-03, D-23) | measured-phase throughput, stall seconds and controller CPU per arm, paired against the comparator. Also, since D-23: the physical interference $\mathcal I^{\text{phys}}$ beside the priced $\mathcal I$, with their ratio (Lemma D.17(iii)); the share of compaction bytes in jobs that overlap no operation ($\Delta n_\iota = 0$; they are charged over their windows, D §1, and the share shows how much work ran in stalls or the drain), with stall jobs and drain jobs reported apart, and the interference charged to drain jobs (over the last $n^{\mathrm{win}}$ operations of the phase, D §1); and the client time no price covers, `mixgraph`'s wall time less the foreground's priced cost at quiet prices (read steps, the fixed parts and the Puts' inserts) and the physical interference, in device-seconds: the client's own work, outside the store, and anything the prices miss. None is part of $J_\beta$; throughput and stall seconds decide the stall rule (Global acceptance) | db_bench, event log, host log, per-thread CPU clocks (plugin thread, trainer process) |
| OBJ-7 | Job prices hold in the run (new 2026-10-03, D-23) | per run, from $n_w$ to the end of the drain: the jobs' own summed spans (compactions and trivial moves: the host log's `job_begin` to `job_end`; flushes: their begin and end records, or `flush_started` to `flush_finished`) against their priced device time $\sum_\iota t^{job}_\iota = \sum_\iota\tau^{job}_\iota/p_{\mathrm{dev}}$, as a ratio, overall and per kind; within a tolerance and with a consequence set by a dated entry before Gate N2 (D-24, §0.7 item 4) (report-only during the exploratory track; D-21's precedent: a run outside it is not priced). Ratios per kind, per start level and per configuration are reported, so that a dependence on the configuration beyond the kinds (pending, §0.7) shows | host log, event log, evaluator |
| OBJ-8 | Interference holds in the run (new 2026-10-03, D-23) | (a) the host log's begin and end records of every compaction, trivial move and flush (flushes gain a begin record) carry the stamps $n^b_\iota$, $n^e_\iota$ and the cumulative foreground-step counters by type (read steps, and the counts of Gets, scans and Puts), the host log writes the same counters with their operation stamp every $n^{\mathrm{str}}$ operations (a dated entry, D-23 or later, fixes the stride), and the evaluator computes every window $W_\iota$, the read and write parts of $I_\iota$, and $\mathcal I$ from them (D §1); (b) a fork timer splits the reads' SST time — at least D-21's reopen timer — and its counts by whether at least one background job, a flush included, is running, *stratified* by step type and level, and by the L0 file count $k_0$ where it matters (or by table-size class: L0's flush-sized files against the 512 KiB files below); a dated entry (D-23 or later) fixes the strata. Per run, for each stratum $h$ the time of its units served while a job runs is predicted as the stratum's own time per unit with no job running, from the same run, times its count with a job running, times $1$ plus the unit-weighted mean of $\sum_{\iota\in\mathcal J(n)}s^{\text{phys}}_{x,\iota}$ over those units (A10's *physical* prediction from the run's own jobs); the measured time, summed over strata and per stratum, is compared with the prediction, within a tolerance and with a consequence set by a dated entry before Gate N2 (D-24, §0.7 item 4) (report-only during the exploratory track). Stratifying is needed because the units served during jobs are a different mix from the others: L0 merges run at $k_0 = K_0$, so more L0 probes, seeks and reopens fall inside job time, L0's files are flush outputs of about 2 MB with larger index and filter blocks than the 512 KiB files below, and the cache state differs; an unstratified ratio of times per unit could depart from the prediction with $\kappa = 0$, or match it by cancellation. With the strata the check tests $\kappa$ and A10 up to the mix differences left inside a stratum (the cache state among them), which the stratum's no-job units estimate. The Put insert and the fixed parts are not timed by it: their $\kappa$ are tested by the calibration (OBJ-2), and whether a run also times them, split the same way, is fixed by a dated entry (D-23 or later). The reference-rate convention sets only the charge, so it does not enter, and jobs of the drain, which no read meets, carry a charge but no physical surcharge and do not enter it either. The timer's flag is set by wall clock and the model's job spans by operation stamps; they differ only in each job's two boundary operations. (c) The calibration's tests of linearity and additivity are recorded with the prices (OBJ-2). (d) The window guard (added by the post-integration decision of 2026-10-03; D §1, Proposition D.11): per run, the share of merge bytes, by kind and start level, whose window exceeded their own span ($\Delta n_\iota < n^{\mathrm{win}}$), and the number of jobs of each kind charged over a window that reached back before they began, stalls and the drain reported apart; where that share exceeds a tolerance, set by a dated entry before Gate N2 (D-24, §0.7 item 4), D.11's (d2) counts as failing at that trigger and its closed form is not used there (Proposition D.11, the guard); the share of L0-merge bytes in merges that did not start at $k_0 = K_0$, did not take all $K_0$ files, or saw a flush complete while they ran (from the event log's `lsm_state` and its compaction and flush records), against a tolerance set the same way, above which D.11's (b2) counts as failing; and the exposure of D.11's (b3): for the merges sourced at a level $\ge 1$ and the trivial moves, the $\mathcal W$-weighted mean L0 count over their windows and the share of them that ran with $k_0 \ge 1$, at every trigger run, the default $K_0 = 4$ included | fork timer, host log, evaluator |
| OBJ-9 | Scan and memtable counters (new 2026-10-03, D-23) | per level, keyed by the version's level as in OBJ-4: hidden entries stepped over (keyed by the level where the hidden entry lives; the evaluator maps them to the charged level, §4), data blocks loaded by iterators after their seeks (counted logically), iteration steps by their numbers $r^{\mathrm{L0}}$ and $r^{\neg0}$ of L0-file and other heap children and by step type (returned, or hidden with the entry's level), and the levels of the non-L0 children of returned steps, which §4's split of the heap increments needs; their sums match the global tickers within 1% (hidden entries against `rocksdb.number.iter.skip`; the others against a global ticker the fork adds for the same count, as D-21 did for reopens). The returned entries ($R_{nx}$, `rocksdb.number.db.next.found`) and the numbers of Gets, scans and Puts (the memtable searches and the fixed bucket's counts) are identical in every arm of one workload and seed, since the operation sequence fixes them (Lemma D.19); any difference is a fault | per-level counters, tickers |

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
  $$s_i = \frac{\text{bytes\_not\_compacting}_i}{\texttt{BaseMaxBytesForLevel}(i)\times m_i},$$
  the numerator being the compensated size of the level's files not being
  compacted. In the fork, `MaxBytesForLevel(i)` already returns
  `BaseMaxBytesForLevel(i)` $\times\,m_i$ (`db/version_set.cc` at
  `8e903efd9`), and the score divides by it once; the formula first written
  here, with `MaxBytesForLevel(i)` $\times\,m_i$, applied $m_i$ twice.

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
Since 2026-10-03 (D-23) "what ran" is priced in full: each job's per-job cost,
the bytes it reads and writes, and the slowdown it causes to the foreground
steps served while it runs (the read part on read steps, the write part on
Put inserts), all charged to its start level; a flush's interference goes to
the shared write-path bucket instead (Pathway D §4). A `SetOptions`
call is not a background job, so it carries none of these charges; its cost
stays an ACT-2 diagnostic (A-Impl-5).

### §3 Theory

**Re-check under the costs of D-23 (2026-10-03).** The cost model now prices
per-job costs and trivial moves, compaction bytes read, the slowdown that
background jobs cause to reads (interference), scan iteration and memtable
search (Pathway D §1–§2). Each result below was checked against these terms:

- Theorem A.1, Lemma A.5 and Lemma A.6 are statements about bytes,
  eligibility and depth. No price enters their statements or proofs, so they
  are unaffected. A.1 gains a priced reading of a burst.
- Theorem A.2 keeps parts (i)–(iii), which are about bytes. It gains
  part (iv): the profile that minimises the priced cost, whose weights become
  level-specific through interference.
- Corollaries A.3 and A.4 stay superseded. Corollary A.7 is unaffected as a
  statement, but the read figures it quotes were measured before scan
  iteration and interference were priced.
- Proposition A.8 is new. It prices lever (e) of §4.

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

*Priced burst (added 2026-10-03, D-23).* Theorem A.1 counts bytes. Under the
costs of D-23 a released burst also costs the bytes its merges read ($S + O$
per merge, at $c_{cr}$), one per-job price per merge or trivial move
($c^d_{job}$ or $c^{tm}_{job}$), and each of its jobs' interference charge
$I_\iota$ (Pathway D §1). RocksDB keeps picking the level while its
score is the highest, so a burst runs as back-to-back jobs and holds the
single compaction slot (G.4) for its whole span. Three consequences:

- An L0 merge that falls due during the span waits for the slot, which the
  slot-blocking charge prices (Pathway D §4).
- Each job's interference is priced at the quiet read cost per operation over
  its window, which for a job long enough is the operations served while it
  runs (D §1), and that cost grows with the L0 file count. At native timing,
  jobs sourced below L0 run with L0 nearly empty: averaged over the merges of
  each start level, L0 held at most 0.13 files in each of the seven native
  runs of §0.7 (both workloads, T = 2, 6 and 10; measured, event logs),
  though up to 5.4% of those merges ran with one L0 file (5.8% over all 23
  runs of their cells). A long burst is the case in which L0
  refills while the burst runs, so its later jobs pay more per byte.
- A deferral that turns a would-be trivial move into a merge (above) now
  costs the merge's per-job price, bytes read and written, and interference,
  less the move's $c^{tm}_{job}$ and its own interference. Before D-23 the
  move was free and the merge cost only its bytes written.

**Theorem A.2 (fanout conservation; corrected and extended; amended
2026-10-03, D-23: part (iv)).** Let $L \ge 2$, so that every $f_i$ of §1.1 is
defined.

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

- (iv) *(Priced weights; added 2026-10-03, D-23.)* Keep (ii)'s fixed weights
  $v_i > 0$ and let each level have its own overlap constant $c_i$. Assume
  also:
  - (a) $\bar s_i$, the mean source bytes per merge sourced at level $i$, does
    not depend on the profile, so the number of merges per user byte,
    $v_i/\bar s_i$, and with it their per-job cost, is fixed (for L0,
    $\bar s_0 \approx K_0F$ at fixed $K_0$);
  - (b) each merge carries D §1's interference charge, and the mean quiet
    cost per operation of each step type over the windows of level $i$'s
    merges does not depend on the profile (for the Put insert it is the
    mix's, D §1).

  Let $\psi_i$ be the priced cost of one more overlap byte in a merge sourced
  at level $i$ (money per byte), as in Proposition D.13(i):
  $$\psi_i = \beta_W(c_w + c_{cr}) + I^{O,\beta}_i,$$
  where $I^{O,\beta}_i$ is the priority-weighted interference charge that
  byte adds, its read part weighted $\beta_R$ and its write part, on Put
  inserts, $\beta_W$ (an overlap byte is read once, at $c_{cr}$, and written
  once, at $c_w$, and adds to the merge's bytes $Y_\iota$ and priced device
  time, with its write part). Then the part of the
  job-borne cost, $\beta_W\sum_\iota\tau^{job}_\iota + \sum_\iota I^\beta_\iota$,
  per user byte that depends on the profile is
  $\sum_i c_i\psi_iv_if_i$. Under (i) it is minimised at
  $f_i \propto 1/(c_i\psi_iv_i)$, with minimum
  $L\big(\tfrac{B_L}{K_0F}\prod_i c_i\psi_iv_i\big)^{1/L}$. Against the
  profile of (ii) computed with a common constant ($f_i \propto 1/v_i$,
  D-14 §3), the relative saving on this part is exactly
  $$1 - \frac{\big(\prod_i c_i\psi_i\big)^{1/L}}{\frac1L\sum_i c_i\psi_i},$$
  one minus the ratio of the geometric to the arithmetic mean of the
  $c_i\psi_i$. It is zero if and only if all $c_i\psi_i$ are equal.
  *Approximation:* for a small spread it is about half the variance of
  $\ln(c_i\psi_i)$ across levels.

*Proof.* (i) Corollary D.9. (ii) By the inequality of arithmetic and geometric
means, $\sum_iv_if_i \ge L\big(\prod_iv_if_i\big)^{1/L} =
L\big(\tfrac{B_L}{K_0F}\prod_iv_i\big)^{1/L} = L\lambda$, with equality if and
only if every $v_if_i$ equals $\lambda$. (A multiplier on the product
constraint gives the same point; the inequality also shows it is the global
minimum.) (iii) With $v_i = \eta^i$, $\prod_i v_i = \eta^{L(L-1)/2}$, and
equal fanouts give $(B_L/(K_0F))^{1/L}\sum_i\eta^i$; divide. The first-order
terms in $1-\eta$ cancel. (iv) By Lemma D.7 (and D.18 for the bytes read), a
merge sourced at level $i$ writes $\rho_iS + O$ and reads $S + O$ bytes, so per
user byte level $i$'s merges write $v_i(\rho_i + o_i)$ and read
$v_i(1 + o_i)$. By (a) their per-job cost is fixed. By (b) and D §1, a merge's
priority-weighted interference charge is affine in its overlap bytes with
slope $I^{O,\beta}_i$: its byte part is $\kappa^B$-weighted bytes, and its
busy part $\kappa^J$-weighted priced device time, which grows by
$(c_w + c_{cr})/p_{\mathrm{dev}}$ per overlap byte, each times the fixed mean
quiet cost per operation of each step type over the merge's window, weighted
$\beta_x$ (Proposition D.13(i)). With $o_i = c_if_i$, everything
that depends on the profile is therefore $\sum_iv_i\psi_ic_if_i$. Apply
(ii)'s argument to the weights $w_i = c_i\psi_iv_i$: the minimum is $L\mu$
with $\mu = \big(\tfrac{B_L}{K_0F}\prod_iw_i\big)^{1/L}$. The profile
$f_i = \lambda/v_i$ satisfies (i) and costs
$\lambda\sum_ic_i\psi_i$. Since $\mu = \lambda\big(\prod_ic_i\psi_i\big)^{1/L}$,
the ratio of the two is the stated mean ratio, which is 1 exactly when all
terms are equal (equality case of the same inequality). For the approximation,
write $x_i = \ln(c_i\psi_i)$ with mean $\bar x$: the ratio is
$e^{\bar x}/\big(\tfrac1L\sum_ie^{x_i}\big)$, and expanding $e^{x_i - \bar x}$
to second order gives $1 - \tfrac12\operatorname{Var}(x) + O(|x - \bar x|^3)$.
$\blacksquare$

*What makes the weights of (iv) level-specific.* With a common overlap
constant, a weight common to all levels cancels: the compaction-read price
$c_{cr}$ adds the same amount to every $\psi_i$, and so does interference
under mean field (every job seeing the time-average read cost), and so does
its write part, on Put inserts, whose cost per operation does not depend on
the tree, unless $\kappa^J_{put}$ depends on the kind. Under D §1's charge,
$I^{O,\beta}_i$ differs between levels only through the read cost per
operation over the windows of level $i$'s merges, and through the busy
coefficient of their kind ($\kappa^J_{x,0}$ at L0, $\kappa^J_{x,d}$ below):
the per-byte factors $\theta_B$ and $(c_w + c_{cr})/p_{\mathrm{dev}}$ are the
same at every level. One measured fact breaks the common weight:

- *Correlation with L0.* Measured (§0.7: seven native runs, both
  workloads, T = 2, 6 and 10): every L0→L1 merge runs with $k_0 = K_0$, so
  its reads probe every L0 file, and the deeper jobs run, on average, with
  L0 nearly empty. Derived from that pattern with provisional prices
  (critique Q2, the critique's model M2), an L0 byte's
  interference is 1.24 times a deeper byte's with probes alone and 1.94
  times with reopens too (scan iteration not included), for the byte part,
  and for the busy part too if it weighs the read-step types alike, as long
  as the windows see the pattern (Proposition D.11, (d2)). So
  $I^{O,\beta}_0 > I^{O,\beta}_{i\ge1}$ whenever $\kappa^J_{x,0} \ge \kappa^J_{x,d}$
  for every $x$ and some read type that L0 files make dearer has a positive
  $\kappa$.

Job speed does not enter. Physically, the busy part of the slowdown grows
with the operations a job serves, and deeper jobs are slower per byte (at
`Assoc` T=10 they wrote 315 (L1) and 230 (L2) MB/s against 606 at L0, the
$\bar q$ arm's host and event logs), so a deeper byte physically overlaps 1.9–2.6 times the
operations of an L0 byte. The charge counts priced device time instead (D
§1), so that factor is a difference between the physical and the priced
cost, reported through $\mathcal I^{\text{phys}}$ (OBJ-6), not a weight. So
the shift from the profile of D-14 has a definite sign when the busy
coefficients do not favour deeper merges: $m_1$ should be lower than D-14's
profile, giving L0 the smaller fanout (Proposition D.13(i)). Whether they do,
and the size of the shift, wait for the calibration of $\kappa$ and its basis,
which sets them (D-24, §0.7 item 1). Between interior levels, which all run with L0
nearly empty, the correlation does not separate the weights; $c_i$ can.

*Illustration, not a measurement.* With the byte part only and the
provisional magnitudes of the 2026-10-03 interference diagnostics (derived:
critique Q2, about $0.18c_w$ of interference per compaction byte at `Assoc`
T=10 under mean field; measured: 39% of compaction bytes sourced at L0,
the $\bar q$ arm's event log), with no write part, with the overlap
byte's read and write together priced at today's $c_w$, only $\psi_0$ differs
from the others, by a factor of about
1.03–1.11 in balanced mode and 1.15–1.53 in read priority at
$\beta^\star = 10$. The saving of (iv) on the fanout part is then at most
about 0.1% in balanced mode and 1.8% in read priority at $L = 4$, and less at
$L = 8$.

*When (a) fails.* RocksDB grows a merge's start-level inputs inside the key
range of the output files it already takes, provided no output file is
added, the expanded inputs are not being compacted, their cut is clean, and
the inputs and outputs together stay under twice `max_compaction_bytes`
(`CompactionPicker::SetupOtherInputs`, `db/compaction/compaction_picker.cc`
at `8e903efd9`). How much
it can add depends on how wide those output files are against the source
files and on that cap, so at small fanouts $\bar s_i$ can depend on the
profile. The per-job cost then enters the fanout part through
$v_i/\bar s_i(f_i)$, the part is no longer linear in $f_i$, and (iv) is an
approximation. Below L0 each job's span exceeds RocksDB's counted
compaction time by a median of 2.7–3.5 ms, nearly independent of its size
(measured, §0.7; the time outside `RunSubcompactions`). Against $c_w \approx$ 1.7 ns per byte (D-15) that gap alone
costs as much as writing 1.6–2.1 MB, three to four 512 KiB files, so the
per-job term is not small where merges are small.

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
only at its own profiles: two of them, uniform 1 and uniform 0.75, are
uniform scalings, as is the base-size axis, and these move $m_i$
and $m_{i+1}$ together, so their separate effects on $\rho_i$ (a larger level
$i+1$ lowers it) cannot be told apart; the last-level-emptying profile moves
one level only (D-13 §4); and the survival-weighted profile is computed from weights measured at
uniform 1. Treat that profile as one step of a fixed-point iteration:
re-measure the weights under it and recompute; if the profile moves by more
than its measurement interval, add the second profile to $\Theta_s$ before the
comparator is fixed (§0.6).

The same applies to (iv), whose $\psi_i$ hold fixed what the profile moves:
the read cost per operation over each level's merges' windows. The
trivial-move shares now also carry a price: each move
costs $c^{tm}_{job}$ and its interference, so a profile that turns moves
into merges, or merges into moves, changes the per-job cost too. And (iv)
optimises the write cost and the interference only. The profile also moves
the read cost, through where Gets find their key (Proposition D.13(ii)) and
through the shadowed versions that scans step over (Lemma D.19), which (iv)
holds fixed. So (iv)'s profile minimises $C_\beta$ only where those read
effects do not depend on the profile; the joint optimum is not derived here.

*Corrections.* The optimal profile needs some multipliers below 1, which the
2026-09-11 scale ($s \ge 1$) could not express (audit §3); multipliers below 1
are now allowed. The profile is static, so its gain belongs to the comparator
(Corollary C.3). The accounting in (ii) is Lemma D.7's, not a convention.
(iv)'s profile is static too, but $\Theta_s$ holds (ii)'s profile
(D-13 §4, D-14 §3), not (iv)'s. Where the $c_i\psi_i$ differ, (iv)'s profile
is a static point outside $\Theta_s$, and a controller that held it would
show its saving, at most (iv)'s mean ratio on the fanout part, against
$\theta^\star_\beta$ with no dynamic mechanism at all. Decided 2026-10-04
(D-24, §0.7 item 7): $\Theta_s$ gains it for the balanced mode only, at the
formal Gate N2.

**Corollary A.3 (2026-09-11: level removal is never profitable).** *Superseded.*
Its premise, that removing a level needs $s = T$, failed at T=10 where $s = 2$
removed one (audit §3). Whether fewer levels pay depends on the priority
(Proposition D.13(iv)), and removal is static in any case (Corollary A.7).

**Corollary A.4 (2026-09-11: write-optimal size ratio).** *Superseded* by
Proposition D.13(iv), in which the optimum depends on the measured overlap
constant and the priority, and since 2026-10-03 also on the job,
compaction-read and interference prices (D-23).

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

*Re-priced 2026-10-03 (D-23); the statement is unchanged.* The read figures
above were measured before scan iteration and interference were priced, and
are not re-scored. Under D-23's costs a level less also takes one child out
of the iterator heap of every scan step that reached it, and one place where
shadowed versions can sit for scans to step over (Lemma D.19), and its
removal changes how many bytes are compacted, and so the interference. Each
of these is a property of the static configuration too, so the conclusion
stands.

**Proposition A.8 (an early L0 merge, priced; lever (e)) (added 2026-10-03,
D-23; (ii)–(iv) restated by the integration of the same day as consequences
of Proposition D.11).** Assume:

- (m1) each flush adds one L0 file of $F$ bytes; no flush completes during an
  L0 merge, and an L0 merge is short against the flush interval, so L0 holds
  the files being merged until the merge installs and none of them after.
  Measured (§0.7): $k_0 = K_0$ throughout every L0 merge of the seven native
  runs, and at `Assoc` T=10 an L0 merge takes about 39 ms against a flush
  every 174–180 ms (mean flush interval in the measured phase, 174.3 ms on
  the pilot and 179.5 ms on the $\bar q$ arm, host logs);
- (m2) the compaction slot is free whenever L0 falls due. Supported at the
  default point (measured, §0.7): no job sourced below L0 ran while L0 held
  two or more files, in any of the seven native runs (or the 23 of their
  cells), so none held the slot when L0 fell due; averaged over the merges of
  each start level, L0 held at most 0.13 files, and up to 5.4% of them ran
  with one (5.8% over the 23);
- (m3) every L0 file covers the key range (Lemma D.8), so every L0 merge
  takes every L0 file (RocksDB widens an L0 merge to every overlapping L0
  file: `CompactionPicker::GetOverlappingL0Files`,
  `db/compaction/compaction_picker.cc` at `8e903efd9`) and reads and rewrites the same
  $O_0 = c_0m_1C_1$ bytes of L1 whatever its file count, with $\rho_0 = 1$ and
  no trivial move out of L0 (Proposition D.11's assumptions).

(i) *One early merge is a phase shift.* Let L0 hold $k$ files,
$0 < k < K_0$. Path E merges them now; path H holds until RocksDB merges at
$K_0$ files; both keep the trigger $K_0$ afterwards. Number the flushes after
the decision $n = 1, 2, \dots$ and let $\mathcal D(n)$ be H's L0 file count
minus E's, summed over the first $n$ flush intervals. Then both paths merge
every flushed byte exactly once; $0 \le \mathcal D(n) \le k(K_0 - k)$, with
$\mathcal D(n) = 0$ whenever $n$ is a multiple of $K_0$; and E has completed
one more L0 merge than H for
$K_0 - k$ of every $K_0$ consecutive values of $n$, and as many for the other
$k$. So one early merge changes neither the long-run mean L0 file count nor
the long-run rate of L0 merges, and its effect on the run's cost is bounded
and does not grow with the run's length. (The counts are exact up to each
merge's own span, during which its files are still read.)

(ii) *Repeated, it is a lower trigger.* Merging L0 whenever it holds $K$ files
is the static trigger $K$. Assume Proposition D.11's (a)–(e), of which
(m1)–(m3) restate the L0 part, and use its notation: $A_W$, $A_I$ and $B_R$
of D.11(ii), with the interference weights $\mathcal W$ of D.11 and the read
cost per operation $\varrho_x(k) = \varrho_x(0) + \varrho'_xk$ of D.11(c).
Then the part of the cost per operation that depends on $K$ is D.11's
$$g(K) = \frac{\beta_WA_W + \beta_RA_I}{Q_FK} + \frac{\beta_RB_R}{Q_F}\,K,$$
and the part of the read cost $\mathcal C_R$ alone is the same with
$\beta_W = 0$ and $\beta_R = 1$. Lowering the effective trigger from $K$ to
$K' < K$ lowers the priced, priority-weighted cost per operation, and so
$J_\beta$ over a stationary stretch, if and only if
$KK' > (K^\star_\beta)^2 := (\beta_WA_W + \beta_RA_I)/(\beta_RB_R)$, which is
$(K_0^\star)^2$ of D.11, and lowers the read cost $\mathcal C_R$ if and only
if $KK' > (K^\star_R)^2 := A_I/B_R$, the square of D.11(ii)'s floor.

(iii) *Read priority.* Without interference on reads (every read-type
$\kappa$ zero) every read type's $\mathcal W$ is 0, so $A_I = 0$ and a lower
trigger always lowers the read cost: the reading of lever (e) before
2026-10-03. With interference, $A_I > 0$ as soon as some read type $x$ with
$\varrho_x(0) > 0$ has $\kappa^B_x > 0$ or $\kappa^J_{x,0} > 0$, since then
$\mathcal W^0_x > 0$: an L0 merge's charge has a part that does not grow with
its file count (the L1 overlap it reads and writes, and its per-job device
time). Then $(K^\star_\beta)^2 = (K^\star_R)^2 + (\beta_W/\beta_R)\,A_W/B_R$,
which falls to $(K^\star_R)^2 > 0$ as $\beta_R/\beta_W \to \infty$: read
priority no longer means "merge L0 as early as possible". In read priority an
early merge that lowers the effective trigger from $K$ to $K'$ raises that
cost whenever $KK' < (K^\star_\beta)^2$, and raises the read cost itself
whenever $KK' < (K^\star_R)^2$. This is the condition under which lever (e)
reverses.

(iv) *Heavy reads.* Multiply every quiet read cost per operation (each
$\varrho_x(0)$ and $\varrho'_x$) by $\lambda > 0$, with $Q_F$, the merge's
operations $N^{(0)}$, the job and byte prices and $\kappa$ fixed. Then
$K^\star_R$ does not change and $K^\star_\beta$ falls toward it. Heavier reads
widen the range in which an earlier L0 merge pays, but never below
$K^\star_R$: an L0 merge's interference grows with the read cost of the
operations in its window exactly as the probes it saves do.

*Proof.* (i) By (m1)–(m3), during the $t$-th flush interval after the
decision ($t = 0, 1, \dots$) H holds $(k + t) \bmod K_0$ files and E holds
$t \bmod K_0$, since a merge starts at once (m2) and empties L0 at the flush
that brings it to $K_0$ files. Their difference is $k$ when $t \bmod K_0 < K_0 - k$ and $k - K_0$
otherwise. So it sums to zero over any $K_0$ consecutive intervals, and its
partial sums rise by $k$ for $K_0 - k$ intervals and then fall by $K_0 - k$
for $k$ intervals, from 0 to $k(K_0-k)$ and back. E merges at flushes
$0, K_0, 2K_0, \dots$ (counting the decision as flush 0) and H at
$K_0 - k, 2K_0 - k, \dots$, which gives the merge counts. Each L0 merge takes
every L0 file (m3), so each flushed file is merged exactly once on each path.
After the decision both paths are periodic with period $K_0$ and the same
cost per period; they differ only in a bounded first stretch and in phase.
(ii) Proposition D.11(i)–(ii) gives $g$, and its proof gives the read cost's
part: $\mathcal C_R$ collects the quiet reads and the read part of the
interference, whose $K$-dependent parts are $\beta_R$ times
$A_I/(Q_FK) + B_RK/Q_F$, while $A_W/(Q_FK)$ is the write side, the write part
of the L0 merges' interference included (D.11(ii)). Then, for $K' < K$,
$g(K') - g(K) = \frac{K - K'}{Q_F}\big[(\beta_WA_W + \beta_RA_I)/(KK') - \beta_RB_R\big]$,
which is negative exactly when $KK' > (K^\star_\beta)^2$; the read cost alone
is the case $\beta_W = 0$, $\beta_R = 1$. (iii) If every read-type $\kappa$
is zero, every read type's $\mathcal W$ is zero, so $A_I = 0$. $\mathcal W^0_x$ contains
$\bar q\kappa^B_x\theta_Bm_1C_1$ and
$\bar q\kappa^J_{x,0}(c^0_{job} + (c_w + c_{cr})m_1C_1)/p_{\mathrm{dev}}$, both
positive when the coefficient is. The identity for $(K^\star_\beta)^2$ is its
definition split in two, and its second term is positive and falls to 0 as
$\beta_R/\beta_W \to \infty$. (iv) $A_I = \sum_{x\in\mathcal X_{\mathrm{rd}}}\mathcal W^0_x\varrho_x(0)$
and $B_R = \sum_x\varrho'_x[(Q_F + \mathcal W^F_x)/2 + N_bF + \mathcal W^1_x]$
are linear in the read costs, with weights $\mathcal W$ that contain no read
cost, and $A_W$ does not contain them (its interference term is the Put
insert's, which is not a read cost), so $A_I$ and $B_R$ are both multiplied
by $\lambda$: their ratio is unchanged and $(K^\star_\beta)^2 = (K^\star_R)^2 +
\beta_WA_W/(\lambda\beta_RB_R)$ falls as $\lambda$ grows. $\blacksquare$

The 2026-10-03 draft of (ii) derived $g$ separately, with the mean L0 count
$K/2 + \delta_0$ taken independent of $K$ and the busy part of the charge
counting the operations served during a job. Proposition D.11 replaced the
first by the cycle's exact mean, $(K-1)/2 + N^{(0)}(K)/Q_F$, and the
integration replaced the second by priced device time; (ii) now cites D.11 so
that the two results have one $K^\star_R$.

*Limits of the model.* D.11(c) is an approximation for the heap-step price,
which is expected to be roughly affine in $\log r$ (D §1), so L0's read cost
is concave, not affine, in its file count. Then $g$ is not of the form above,
the product condition holds only for the linearised cost, and the exact
condition is $g(K') < g(K)$ on Proposition D.11(iii)'s cost. The conclusion of
(iii) survives: whenever $A_I > 0$, the term $A_I/K$ makes the read cost grow
without bound as $K \to 0$, so read priority's best trigger stays away from 0.
Under mean field, which D.11's (b3) and (d2) replace by an idealisation of
the measured correlation, the jobs below L0 would also see the mean L0 count
and add to $B_R$, lowering $K^\star_R$; derived at provisional magnitudes
(critique Q2), mean field gets this slope 2.8 times too steep, and windows
longer than the jobs move the charge toward it (D.11, "When (d2) fails"). The
measured exposure of the deeper jobs to $k_0 = 1$ adds a little to $B_R$
(D.11, "When (b3) holds only on average"). Flush–compaction non-additivity and the trainer
process are outside A10 (Pathway D §1).

*Magnitudes, an estimate and not a measurement.* The critique's computation
(Q2, at provisional prices) gives the
static optimum at `Assoc` T=10 at
$\beta^\star$ = 1, 5 and 10 under a simpler model: the byte part only,
probes and reopens as the read cost, and no $c^0_{job}$, $c_{cr}$ or flush
interference. Those values fit $(K^\star_\beta)^2 = (K^\star_R)^2 +
\text{const}/\beta^\star$ and give $K^\star_R \approx 1.7$ with L0's probes
alone and 0.9 with its reopens too, both below the smallest admissible
trigger, 2 (D.11's illustration, on its slightly fuller magnitudes, has the
same floor, 1.7). On those magnitudes, from the default $K_0 = 4$, lever (e)
is re-priced but not reversed in read priority. The busy part, scan
iteration (which raises both $\varrho_x(0)$ and $\varrho'_x$) and flush
interference can each move $K^\star_R$. Whether it exceeds 2 is known only
once D-23's prices and $\kappa$ are fixed.

*What is left of lever (e) as a dynamic lever.* By (i), its lasting effect is
that of a lower trigger, which is static (Proposition D.11) and in $\Theta_s$
($K_0 \in \{2, 4, 8\}$). What a fixed trigger cannot copy is the conditioning
on the slot's state, and that has value only where (m2) fails: L0 about to
fall due while a long job holds the slot. At the default point that almost
never happens (m2). On a stationary closed-loop workload "while reads are
heavy" can only mean a state of the tree, mainly $k_0$, because `mixgraph`
draws each operation's type independently and the single client is never
idle outside stalls. Time-varying read intensity belongs to phased workloads,
where $K^\star_\beta$ itself moves (lever (a), Corollary D.12).

### §4 What actuation can and cannot deliver

**Static, so the comparator has it too:** the equal-fanout or survival-weighted
profile (Theorem A.2), and its priced-weight form (Theorem A.2(iv); in
$\Theta_s$ for the balanced mode at the formal Gate N2, D-24), depth fixed at load (Corollary A.7), $K_0$ at fixed
prices (Proposition D.11), and the interior profile's read effect
(Proposition D.13(ii)). Since 2026-10-03 (D-23) that read effect has a second
channel: an expanded level holds more shadowed versions, which scans over
their keys step over (Lemma D.19). It works against the hit shift, which
lowers probes, and it is static too.

**Dynamic, so this is the controller's only case.** Each item is a hypothesis
tested without a learner at Gate N3. Each is re-priced under D-23's costs
(2026-10-03):

- (a) tracking $K_0^\star$ and the profile as prices change with the workload
  (Corollary D.12);
  - *re-priced:* $K_0^\star$ is the amended optimum of Proposition D.11
    (Proposition A.8(ii) reads it as the trigger an early merge competes
    with). It depends on the write volume and the read cost per operation
    together and is no longer proportional to $\sqrt{\beta_W/\beta_R}$. The
    profile's weights $\psi_i$ (Theorem A.2(iv)) move with the read cost per
    operation during each level's merges, so tracking now follows read
    intensity too.
- (b) timing a release against the neighbours: overlap per source byte is about
  $T\varphi_{i+1}/\varphi_i$ (Lemma D.8), and a burst may be arriving from above;
  - *re-priced:* an overlap byte costs $\psi_i$ (Theorem A.2(iv)), not
    $c_w$. A release's jobs also pay interference at the read cost of the
    operations in their windows, which grows with $k_0$. Native timing
    already runs the jobs sourced below L0 with L0 nearly empty
    (Proposition A.8, (m2)), so in that dimension it is at its minimum, and
    a release timed later in L0's fill cycle pays more per byte.
- (c) holding a level so its merges drop more garbage, bounded by the garbage
  native leaves and by how much higher the drops can move (Pathway B §3);
  - *re-priced, two ways.* A byte dropped higher now also saves, at every
    later stage, the price of reading it ($c_{cr}$) and its share of those
    merges' interference; the dropping merge still reads it. And holding now
    costs reads: a shadowed version left resident is an internal entry that
    every scan over its key steps over (a hidden step, Lemma D.19). On the
    `Assoc` pilots scans stepped over 1.15 hidden entries per returned entry
    at T=2 and 0.23 at T=10, about 11 and 5.4 times the $S - 1$ that
    uniformly spread garbage would give on the same runs: hidden versions
    concentrate on hot keys (measured, §0.7, `db/n1-assoc`). Holding a level keeps
    exactly the shadowed versions its next merges would drop, and those are
    the hidden entries of the level below that Pathway D §4 charges to the
    holding level, so in read priority (c) pays a read price besides its
    space price, in its own reward, and its write saving must exceed both.
- (d) preventing depth growth (Theorem A.1);
  - *re-priced upward:* a new level also adds a child to the iterator heap
    of every scan step that reaches it, a place for shadowed versions, and
    compaction bytes with their interference (Corollary A.7).
- (e) L0 compacting early (the L0 "compact" action) while the compaction slot
  is idle and reads are heavy, and holding while it is busy;
  - *re-priced by Proposition A.8, and possibly reversed in read priority.*
    One early merge only shifts L0's phase (A.8(i)); repeated, it is a lower
    trigger, which is static. Each extra L0 merge now costs a per-job price,
    the L1 overlap read and rewritten, and the interference of all of it,
    priced at the read cost of operations served with L0 full (Proposition
    D.11's $\mathcal W^0$, $\mathcal W^1$). Lowering the
    effective trigger from $K$ to $K'$ raises the cost whenever
    $KK' < (K^\star_\beta)^2$, and in read priority $K^\star_\beta$ falls only
    to the floor $K^\star_R$, positive whenever interference on reads is
    ($A_I > 0$), below which an earlier merge raises the read cost itself
    (A.8(iii)). Heavier reads lower $K^\star_\beta$ but not
    the floor (A.8(iv)).
- (f) interior levels holding their releases while L0 is due or about to be,
  so that L0 is not kept waiting for the slot (the slot-blocking charge,
  Pathway D §4).
  - *rare at the default point:* no job sourced below L0 ran while L0 held
    two or more files in the seven native runs of §0.7 (nor in the 23 of
    their cells), and averaged over the merges of each start level L0 held
    at most 0.13 files (measured, §0.7). RocksDB's cascade below L0 ends
    before L0 next falls due; at `Assoc` T = 10 its last jobs run past the
    next flush in nearly a third or more of L0 cycles (31–46% on the $\bar q$ arm,
    36–41% on the pilots; 4.5–8.4% at T = 6; at most 0.2% at T = 2; §0.7,
    from the event logs under `db/qbar-assoc-d21id` and `db/n1-assoc`), which
    is why some of them see one L0 file. So L0 did not fall due while such a
    job held the slot, and the slot-blocking charge did not apply. (f) acts only where deeper jobs
    run long or late, as when a held level is released as a burst (Theorem
    A.1, priced burst). In those states interference gives it a second
    reason: a deeper job that runs while L0 fills pays its interference at a
    higher read cost per operation. Native timing avoids both, so (f) mainly
    removes costs that a controller's own holds create.

(e) and (f) are read levers that act during a run. A fixed trigger or profile
can express neither, because each depends on the slot's state at the moment of
the decision. *Amended 2026-10-03 (D-23):* that conditioning is all that is
dynamic about them. At the default point the slot is almost never busy when
L0 falls due, so it has few occasions to act, and the rest of (e) is a lower
trigger, which $\Theta_s$ already holds.

**Not a lever on a stationary workload: timing compactions into light-read
moments.** Every arm serves its operations from one client thread (A8,
D §1), which issues each operation as soon as the last returns, so outside
stalls the store is never idle. `mixgraph` draws
each operation's type independently, so on a stationary mix the read cost per
operation changes only with the tree's state, mainly $k_0$, and native
RocksDB already runs the jobs sourced below L0 with L0 nearly empty
(Proposition A.8, (m2)). The only light moments left are stalls, and they are
not light for the charge: a job run in a stall is charged at the read cost of
the operations before it (Pathway D §1, Lemma D.17(v)); the stall rule
(Global acceptance) still bars any claim that moves work into them. Load
dips of an open-loop client, into which SILK's opportunistic bandwidth
allocation schedules compaction [SILK], are outside A8, and phased workloads
belong to the phase-adaptivity gap $\mathcal G$ (Pathway B), not to this
list. (SILK's other two techniques, priority for flushes and L0→L1
compactions and their preemption of deeper compactions, need no load dips;
they are scheduling inside the store, not a choice of targets and
triggers.)

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
  (verified at `25468bbaa` and `8e903efd9`). Re-check the soft and hard
  pending-bytes limits.
- **A-Impl-4. When a change takes effect.** Verified at `8e903efd9`
  (`DBImpl::SetOptions`): the call appends a new Version without a MANIFEST
  write, which recomputes every score, before it returns, waiting its turn
  behind any flush or compaction install. An action is therefore in the
  published scores at once, with or without writes.
- **A-Impl-5. Cost of `SetOptions`.** Verified at `8e903efd9`: each call
  appends that Version and installs a new SuperVersion under the DB mutex, then
  writes and renames a full OPTIONS file with the mutex released
  (`WriteOptionsFile`; RocksDB has no switch to skip it). The OPTIONS file is
  not an SST, so it enters none of $W$, $H$ or $J_\beta$ (D §1); its time cost
  is measured by ACT-2 and reported, not priced. A `SetOptions` call is not a
  background job either, so it carries no per-job price and no interference
  charge (D-23); the reads it slows are seen only by ACT-2. Call `SetOptions` only when a
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
  value, the patched binary must reproduce stock RocksDB (ACT-4). This
  includes the binary that carries D-23's instruments (foreground-step counters in
  the host log's job records, the iterator counters, the SST-read timer and
  the scan set-up timer): they must not move native compaction, and must not
  change the foreground's speed beyond a margin, since Gate N1's turnover
  counts are reused on that binary on that ground (G §4; the margin is
  set by a dated entry before Gate N2, D-24, §0.7 item 4). The comparator and the new prices are not reused:
  $\Theta_s$ and every new price are measured on that binary (C §0, Gate N0
  item 11), and the existing prices are re-checked on it (D-22 §2(g)).

### §6 Acceptance

| # | Criterion | Threshold | Instrument |
| --- | --- | --- | --- |
| ACT-1 | Option correctness | the fork's test file `level_target_multipliers_test` is green (Debug build), and the same checks pass on the Release binary in the preflight: L0 score invariance, rejection under dynamic sizing and of vectors whose targets shrink going down, scaled pending estimate, recompute after `SetOptions` | fork gtest; preflight check script over `db_bench` output and LOG |
| ACT-2 | `SetOptions` overhead | foreground throughput cost ≤ 1% at the planned call rate, paired, OPTIONS-file writes included; a diagnostic of overhead, not part of $J_\beta$ | db_bench |
| ACT-3 | Action fidelity | a requested $m_i$ appears in the published score within one control interval for ≥ 99% of changes | policy log, score observer |
| ACT-4 | Native parity | patched binary at $m \equiv 1$ inside the oracle-parity envelope of stock RocksDB (the checks of the 2026-08-22 gate); re-run on every binary change, D-23's instrumented binary included (amended 2026-10-03), before any price, comparator arm or reuse of Gate N1's pilots on that binary | paired evaluator |
| ACT-5 | No ratchet | anchors return to within $\epsilon$ of 1 after $5\kappa_a\tau_i$ without re-expansion | policy log |

The 2026-09-11 criteria A-0 to A-5 are retired (history §18.1).

---

## Pathway G — Propagation across levels (turnover normalisation)

### §0 Purpose and scope

Deep levels complete few outcomes per run, so a learner at a deep level has too
little data. RusKey reports the same for its FLSM-tree: training every level
separately, within a 24-hour training budget, failed from Level 3 onward
[RusKey, §7]. This pathway lets deep
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

### §2 The level clock, the price per turnover and its interference input

**Re-check under the costs of D-23 (2026-10-03).** Proposition G.1, the
level clock, is about flows of bytes and is unaffected. Proposition G.3 is
unaffected: every reward of a level is still divided by one positive constant.
Lemma G.5 is about least squares and is unaffected. Proposition G.2 gains the
new terms, and its interference term is a level input, not a workload ratio
(Proposition G.6, new). Proposition G.4's error terms grow (G §3), and the
admission test does not depend on the prices (Lemma G.7, new; G §4).

Definitions are in §1.1: pass-through $\rho_i$ over merges, trivial-move share
$\xi_i$, net pass-through $\tilde\rho_i$, overlap $o_i$, inflow $\lambda_i$,
outflow $\mu_i$, turnover time $\tau_i$, level clock $\theta_i$, survival to
level $i$ $\pi_i$. In addition, per level: $e^f_i$ filter probes per Get at level
$i$, $e^b_i$ false-positive block reads per Get at level $i$ (the hit's block
read goes to the hit-read bucket, Pathway D §4), $\nu_i$ runs seeked per
scan at level $i$ (1 if the level has a file at or after the seek key), $e^o_i$
the reads' table reopens at level $i$ per operation (D-21), and the
holding price
$\sigma_i = c_sN_i/(c_w\bar q)$, with $N_i$ the operations served per turnover
of level $i$ (§1.1): the cost of holding one byte while level $i$ turns over
once (space is charged per operation served, Pathway D §1), in units of the
cost of writing one byte.

*Added 2026-10-03 (D-23)*, per level $i$, for the new costs, all as means over
the same window as the other inputs:

- $\bar s_i$ and $\bar s^{tm}_i$: mean source bytes per merge, and per trivial
  move, sourced at level $i$ (Lemma D.18);
- $\tilde c_{cr} = c_{cr}/c_w$ and $\tilde c^{\mathrm{kind}}_{job} = c^{\mathrm{kind}}_{job}/c_w$:
  the price of reading one compaction byte, and of one job of a kind, in
  units of the price of writing one byte ($\tilde c^{\mathrm{kind}}_{job}$ is
  in bytes); $\tilde c^{\langle i\rangle}_{job}$ is that of a merge sourced at
  level $i$;
- $\tilde I_i$ and $\tilde I^{tm}_i$: the interference charge of level
  $i$'s merges per merged source byte, and of its trivial moves per moved
  byte, in units of $c_w$: $\tilde I_i = \sum_\iota I_\iota\big/\big(c_w\sum_\iota S_\iota\big)$
  over the merges $\iota$ sourced at level $i$ that complete in the window
  (Pathway D §1), and likewise over its trivial moves; $\bar I_i = c_w\tilde I_i$
  is the same in money. Each has a read and a write part, and
  $\tilde I^\beta_i = \beta_R\tilde I^{\mathrm{rd}}_i + \beta_W\tilde I^{\mathrm{wr}}_i$
  and $\tilde I^{tm,\beta}_i$ are their priority-weighted values (D §1);
- $e^{hd}_i$ and $e^{ib}_i$: the hidden steps charged to level $i$ per scan,
  that is, over the entries of level $i+1$ (for L0, of L0 and L1; Pathway D
  §4), and the data blocks of level $i$ that scan iterators load after their
  seeks, per scan (Lemma D.19);
- $h_i$: the heap-step cost charged to level $i$ per scan under D §4's split:
  for a level $\ge 1$, its equal share of each returned step's non-L0
  increment, plus the non-L0 increment of the hidden steps charged to it; for
  L0, its increment on every step plus the non-L0 increment of the hidden
  steps charged to it.

Job quantities carry the job index $\iota$; the level index in the formulas
that use them is $i$, and $N_i$ is still the operations per turnover of level
$i$.

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

**Proposition G.2 (price per turnover; amended 2026-10-03, D-23).** In steady
state, level $i$'s attributed part of $C_\beta$ (Pathway D §4: its write and
read shares, which now include its jobs' per-job prices, compaction bytes read
and interference charges and its scan iteration charges, and for space its
shadowed-garbage charge, which is not a share of $C_\beta$'s space term)
accumulated over one turnover, divided by $c_wC_i$ (the price of
writing one level's worth of bytes), is
$$\hat c_i = \beta_W\Big[(1-\xi_i)\Big(\rho_i + o_i + \tilde c_{cr}(1 + o_i) + \frac{\tilde c^{\langle i\rangle}_{job}}{\bar s_i}\Big) + \xi_i\,\frac{\tilde c^{tm}_{job}}{\bar s^{tm}_i}\Big] + \beta_R\,\frac{\tilde R^f e^f_i + \tilde R^b e^b_i + \tilde R_{sk}\nu_i + \tilde R^o e^o_i + \tilde R^{st}e^{hd}_i + \tilde R^{ib}e^{ib}_i + \tilde h_i + \tilde y_i}{\pi_i} + (1-\xi_i)\,\tilde I^\beta_i + \xi_i\,\tilde I^{tm,\beta}_i + \beta_S\,\sigma_i\,(1-\tilde\rho_i)\bar\varphi_i,$$
where $\tilde c^{\langle i\rangle}_{job}$ is the per-job price of level $i$'s
merges over $c_w$ (kind $0$ at L0, $d$ below), with the
workload price ratios, the same at every level,
$$\tilde R^f = \frac{q_{pt}c_f}{c_w\bar\lambda_1},\qquad \tilde R^b = \frac{q_{pt}c_{blk}}{c_w\bar\lambda_1},\qquad \tilde R_{sk} = \frac{q_{sc}c_{sk}}{c_w\bar\lambda_1},\qquad \tilde R^o = \frac{q\,c_{open}}{c_w\bar\lambda_1},$$
$$\tilde R^{st} = \frac{q_{sc}\,c_{st}(1)}{c_w\bar\lambda_1},\qquad \tilde R^{ib} = \frac{q_{sc}\,c_{ib}}{c_w\bar\lambda_1},$$
$\tilde h_i = q_{sc}h_i/(c_w\bar\lambda_1)$, and $\tilde y_i = y_i/(c_w\bar\lambda_1)$, where $y_i$ is the mean unweighted L0
read cost per second charged to level $i$ by the slot-blocking rule (Pathway
D §4). For L0, add the flushes' charges that D §4 gives it. The
interference terms $\tilde I^\beta_i$ and $\tilde I^{tm,\beta}_i$ carry both
parts of the jobs' charges, the write part (on Put inserts) at $\beta_W$; they are level inputs, not workload ratios (Proposition G.6).

*Proof.* Over one turnover the level releases $\bar\mu_i\tau_i = C_i$ source bytes
in steady state. A share $1-\xi_i$ of them is merged and writes
$(1-\xi_i)C_i(\rho_i + o_i)$ bytes; trivially moved bytes write nothing
(Lemma D.7). The merged bytes are read with their overlap,
$(1-\xi_i)C_i(1 + o_i)$ bytes (Lemma D.18), in $(1-\xi_i)C_i/\bar s_i$ merges
by the definition of $\bar s_i$ as a ratio of sums, each at
$c^{\langle i\rangle}_{job}$; the moved bytes go in $\xi_iC_i/\bar s^{tm}_i$
moves, each at $c^{tm}_{job}$. By the definitions of $\tilde I^\beta_i$ and
$\tilde I^{tm,\beta}_i$, the level's jobs' interference charges, each part
with its weight, sum to
$c_w\big((1-\xi_i)\tilde I^\beta_i + \xi_i\tilde I^{tm,\beta}_i\big)C_i$. Reads charged
to the level cost $\beta_R\tau_i[q_{pt}(c_fe^f_i + c_{blk}e^b_i) +
q_{sc}(c_{sk}\nu_i + c_{st}(1)e^{hd}_i + c_{ib}e^{ib}_i + h_i) + q\,c_{open}e^o_i +
y_i]$. The level's attributed garbage
$(1-\tilde\rho_i)B_i$ (Pathway D §4) costs $\beta_S(c_s/\bar q)
(1-\tilde\rho_i)\bar\varphi_iC_iN_i$ over the $N_i$ operations of the turnover.
Divide by $c_wC_i$ and use $\tau_i/C_i = 1/\bar\lambda_i =
1/(\bar\lambda_1\pi_i)$. $\blacksquare$

The proof rests on D §4's attribution of the new terms as fixed 2026-10-03: a
job's per-job price, bytes read and interference charge go to its start
level; a hidden step goes, at its base price $c_{st}(1)$ (in $\tilde R^{st}e^{hd}_i$)
and its non-L0 increment (in $\tilde h_i$), to the level directly above the
level where the hidden entry lives; an iterator block goes to its level; L0
pays the increment its files add to every step, and the other children share
a returned step's remaining increment equally. The base of the steps over
returned entries, memtable search, the fixed parts of Gets and scans with
the Puts' inserts (the fixed bucket), and a flush's interference go to shared
buckets, which no level is charged.

In plain terms: the write part has the same form at every level, with $\rho_i$,
$\xi_i$, $o_i$ and $\bar s_i$ as measured inputs. The levels differ through those inputs,
through the read share they carry ($e_i$, and $\pi_i$), through the holding
price $\sigma_i$, which grows about $T$ times per level down, and now through
the interference their own jobs cause, $\tilde I^\beta_i$. These become
*inputs* to a shared model rather than things it must learn. The per-job term
is probably not small. *Estimate:* below L0 a job's span exceeds RocksDB's
counted compaction time by a median of 2.7–3.5 ms (measured, §0.7); if the
per-job price is close to that gap, then with one 512 KiB source file per
merge and today's $c_w \approx$ 1.7 ns per byte, $\tilde c^{d}_{job}/\bar s_i$
is about 3–4, comparable to $\rho_i + o_i$ at T=2.

**Proposition G.6 (the interference term is a level input, not one workload
ratio) (added 2026-10-03, D-23; restated the same day for the charge of
D §1).** Fix a level $i$ and a window, and let the sums run over the merges
$\iota$ sourced at level $i$ that complete in it. For each merge write
$\bar\varrho^{\,B}_\iota = \sum_x\kappa^B_x\bar\varrho^{\,x}_\iota$ and
$\bar\varrho^{\,J}_\iota = \sum_x\kappa^J_{x,\langle i\rangle}\bar\varrho^{\,x}_\iota$
for its $\kappa$-weighted foreground cost per operation over its window (D
§1; the sums run over every step type, the Put insert included), and
let
$$\bar Y_i = \frac{\sum_\iota Y_\iota}{\sum_\iota S_\iota},\qquad \bar t^{job}_i = \frac{\sum_\iota t^{job}_\iota}{\sum_\iota S_\iota},\qquad \langle\bar\varrho^{\,B}\rangle_i = \frac{\sum_\iota Y_\iota\bar\varrho^{\,B}_\iota}{\sum_\iota Y_\iota},\qquad \langle\bar\varrho^{\,J}\rangle_i = \frac{\sum_\iota t^{job}_\iota\bar\varrho^{\,J}_\iota}{\sum_\iota t^{job}_\iota}$$
be the bytes on the interference basis and the priced device time per merged
source byte, and the byte-weighted and time-weighted read costs. Then,
exactly,
$$\tilde I_i = \frac{\bar q}{c_w}\Big(\bar Y_i\,\langle\bar\varrho^{\,B}\rangle_i + \bar t^{job}_i\,\langle\bar\varrho^{\,J}\rangle_i\Big),\qquad \bar t^{job}_i = \frac{1}{p_{\mathrm{dev}}}\Big(\frac{c^{\langle i\rangle}_{job}}{\bar s_i} + c_{cr}(1 + o_i) + c_w(\rho_i + o_i)\Big),$$
with $\bar Y_i = (\rho_i + o_i) + \lambda(1 + o_i)$, since a merge writes
$\rho_iS + O$ bytes and reads $S + O$ (D §1's $Y_\iota$; $\lambda = 0$ counts
bytes written only). The first term is the byte part, the second the
busy part. $\bar Y_i$ and $\bar t^{job}_i$ are made of level inputs that
Proposition G.2 already holds. The same identity holds for the read and the
write part separately (the sums restricted to $\mathcal X_{\mathrm{rd}}$ or
$\mathcal X_{\mathrm{wr}}$), and so for the priority-weighted
$\tilde I^\beta_i$ of G.2, with each $\kappa^B_x$ and
$\kappa^J_{x,\mathrm{kind}}$ replaced by $\beta_x\kappa^B_x$ and
$\beta_x\kappa^J_{x,\mathrm{kind}}$.

Consequently, $\tilde I_i = \tilde R^{IB}\,\bar Y_i + \tilde R^{IJ}\,\bar t^{job}_i$,
with two workload ratios $\tilde R^{IB}$ and $\tilde R^{IJ}$ the same at every
level, holds if $\langle\bar\varrho^{\,B}\rangle_i$ and
$\langle\bar\varrho^{\,J}\rangle_i$ are the same at every level. Mean field
(every job seeing the run's mean foreground cost per operation, read steps
and Put inserts alike) gives it when the
busy coefficients do not depend on the kind. In general $\tilde I_i$ is a
level input.

*Proof.* By D §1, merge $\iota$'s charge is
$I_\iota = \bar q\big(Y_\iota\bar\varrho^{\,B}_\iota + t^{job}_\iota\bar\varrho^{\,J}_\iota\big)$.
Sum over $\iota$, write $\sum_\iota Y_\iota\bar\varrho^{\,B}_\iota =
\langle\bar\varrho^{\,B}\rangle_i\sum_\iota Y_\iota$ and likewise for the busy
part, and divide by $c_w\sum_\iota S_\iota$. Every step is linear in the
sum over step types, which gives the statements for the parts and for
$\tilde I^\beta_i$. For $\bar t^{job}_i$: summed over
the window's merges, $\sum_\iota t^{job}_\iota$ is
$[n^m_ic^{\langle i\rangle}_{job} + c_{cr}M_i(1 + o_i) + c_wM_i(\rho_i + o_i)]/p_{\mathrm{dev}}$
with the pooled ratios and the counts of Lemma D.18 ($\sum_\iota X_\iota =
M_i(\rho_i + o_i)$ by the definitions of $\rho_i$ and $o_i$); divide by
$\sum_\iota S_\iota = M_i$ and use $\bar s_i = M_i/n^m_i$. If both weighted
read costs are constants, $\tilde I_i$ is the stated combination with
$\tilde R^{IB} = \bar q\langle\bar\varrho^{\,B}\rangle/c_w$ and
$\tilde R^{IJ} = \bar q\langle\bar\varrho^{\,J}\rangle/c_w$. $\blacksquare$

The identity is exact for the charge $J_\beta$ uses. How well that charge
matches the physical slowdown is a separate question, which the per-run
interference check answers (Pathway D §6); flush–compaction non-additivity
and the trainer process are outside A10. Had the busy part counted the
operations the level's merges serve, it would also vary between levels with
measured job speed (at `Assoc` T=10
jobs wrote 606, 315 and 230 MB/s sourced at L0, L1 and L2, the $\bar q$
arm's host and event logs); under
the charge of D §1 it counts priced device time instead, and job speed is no
longer a level input.

*The condition does not hold as measured* (§0.7):

- *L0 against the levels below.* Every L0→L1 merge runs with all $K_0$ L0
  files present, and the jobs below L0, on average, with L0 nearly empty (the
  seven native runs of §0.7). Derived with provisional prices (critique Q2),
  an L0 merge's
  $\kappa^B$-weighted read cost is 1.24 (probes) to 1.94 (probes and reopens)
  times that of the levels below, when the windows see this pattern
  (Proposition D.11, (d2)). L0 is not pooled (§0), so this affects L0's own
  price per turnover and Theorem A.2(iv), not the pool.
- *Between interior levels.* At native timing all of them run with L0 nearly
  empty, so their window read costs are close (their write parts too, the
  Puts' cost per operation being the mix's), as long as the windows do not
  reach back into the L0 merge that precedes the cascade (which would raise
  L1's most). The byte and the busy part then differ between levels mainly through
  $\bar Y_i$, which the admission test's support screen already compares in
  the form $(1-\xi_i)(\rho_i + o_i)$ (§4), and through $\bar t^{job}_i$, whose
  per-job term $c^{\langle i\rangle}_{job}/\bar s_i$ makes small merges pay
  more per byte.
- *Under a controller.* A level whose jobs run while L0 fills raises its own
  window read costs. So $\tilde I_i$ depends on the policy as well as on the
  level, and the state carries the current read cost per operation (§3).

Which of the byte and the busy part is nonzero is the calibration of $\kappa$'s basis,
which sets it (D-24, §0.7 item 1). So the interference term enters Proposition G.2 as a measured
level input $\tilde I_i$, and a single interference ratio "the same at every
level" holds only under the conditions above.

### §3 The propagation law

**Definition (law; amended 2026-10-03).** For an interior level $j$ in the pool $\mathcal P$, with
normalised state $\hat x_j$ and action $a$, the action value (expected future
normalised reward, rewards being negative costs) is
$$Q_j(\hat x_j, a) = -b(\hat x_j, a) + f_\theta(\hat x_j, a) + \delta_j(\hat x_j, a),$$
where $b$ is the analytic prior (code, H §7), a change in normalised *cost*,
so it enters a reward-valued $Q_j$ with a minus sign (amended 2026-10-03,
as `controller/prior.h` already notes; §0.7 item 9), $f_\theta$ is a
learned correction shared by the pool, and $\delta_j$ is a small per-level
correction. Both learned
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
  share of the horizon, a gain $J_\beta$ does not contain. This still holds
  with the costs of D-23: the interference charge is fixed by the
  operation-indexed record and converted at $\bar q$, so $J_\beta$ still
  contains no time (Lemma D.17). $f_\theta$ is fitted
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
  into one `SetOptions` call (A-Impl-5). Amended 2026-10-02: L0, which is
  not pooled, decides once per flush, every $N_0/K_0^{\text{cfg}}$
  operations, since its state changes only at flushes and L0 compactions; its
  transitions carry $e^{-1/(n_H K_0^{\text{cfg}})}$. The `SetOptions` rate
  cap (A-Impl-5) must stay below the shortest control interval in wall time,
  or ACT-3 cannot hold: at $k = 10$, L0 decided every 30–64 ms on the
  2026-10-02 rules smoke, against a 100 ms cap.
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
  $e^f_j$, $e^b_j$, $\nu_j$, $e^o_j$, $\sigma_j$; and, added 2026-10-03
  (D-23), $\tilde c^{d}_{job}/\bar s_j$, $\tilde c^{tm}_{job}/\bar s^{tm}_j$,
  $\tilde I_j$ and $\tilde I^{tm}_j$, each by part (read and write), $e^{hd}_j$, $e^{ib}_j$ and $\tilde h_j$ (Proposition G.2);
- workload price ratios $\tilde R^f$, $\tilde R^b$, $\tilde R_{sk}$, $\tilde R^o$,
  and $\tilde c_{cr}$, $\tilde R^{st}$, $\tilde R^{ib}$ (D-23);
- read intensity (D-23): the quiet priced read cost per operation over the
  last decision interval, over its mean since $n_w$. The same at every level.
  It sets what a job started now pays in interference (Proposition G.6), and
  $k_0$ alone does not carry it: scans' hidden steps and the operation mix
  move it too;
- the hidden steps charged to level $j$ per scan over the last decision
  interval, over $e^{hd}_j$ (D-23): how much the garbage it now keeps hidden
  in level $j+1$ costs scans;
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

**Proposition G.4 (when two levels can share one value function; amended
2026-10-03, D-23: conditions re-read, error terms, the bound proved).** Write
level $j$'s normalised state as $\hat x_j = (z_j, \vartheta_j)$: $z_j$ the
dynamic part (fills, multipliers, time since release, neighbours, burst, inflow
ratio, contention, queue position, backlog, the fills two levels down and at the
bottom, and since D-23 read intensity and the hidden-step ratio) and
$\vartheta_j$ the level terms and prices, which are fixed
within a run and include the overlap constant $c_j$. Hold the other agents'
policies fixed. Let levels $i$ and $j$ satisfy:

- (c1) conditional on $(\hat x, a)$, the law of the next normalised state and of
  the transition's length in turnovers is the same function at both levels. In
  particular $\hat x$ is Markov: the next state depends on the rest of the tree
  only through $\hat x$;
- (c2) the reward over a transition is the same function of $(\hat x, a, \hat
  x')$ at both levels. The attributed write, probe, seek and reopen costs are,
  by the per-transition form of Proposition G.2's accounting, once $c_j$ and
  $\tilde c^{\langle j\rangle}_{job}/\bar s_j$ are in $\vartheta_j$, and the
  iterator-block and heap-step charges in the same way as the probes. Since
  D-23 the interference and hidden-step charges are not fixed by level terms:
  they depend on the read cost per operation in the windows of the level's
  jobs (Proposition G.6) and on where the shadowed versions it keeps hidden
  sit against the keys scans read (Lemma D.19). They are the same function only as far as
  $\hat x$ carries what sets them; the rest enters $\varepsilon_r$ below. The
  neighbour
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
\varepsilon_r/(1-\gamma) + 2\varepsilon_PR/(1-\gamma)^2$, with
$1/(1-\gamma) = n_Hk + \tfrac12 + O(1/(n_Hk))$. *Proof.*
$Q^\star_i - Q^\star_j = (r_i - r_j) + \gamma(P_i - P_j)V^\star_i + \gamma P_j(V^\star_i - V^\star_j)$,
with $\lVert V^\star_i\rVert_\infty \le R/(1-\gamma)$, so
$|(P_i - P_j)V^\star_i| \le 2\varepsilon_PR/(1-\gamma)$, and
$\lVert V^\star_i - V^\star_j\rVert_\infty \le \lVert Q^\star_i - Q^\star_j\rVert_\infty$
(a maximum moves by at most the largest change of its arguments); so the
norm $\Delta$ satisfies $\Delta \le \varepsilon_r + 2\gamma\varepsilon_PR/(1-\gamma) + \gamma\Delta$,
which gives the bound, its transition term loose by a factor $\gamma$.
$\blacksquare$ This is a bound of the type of Strehl and Littman's Lemma 1
[Strehl & Littman]. Theirs bounds $|Q^\pi_1 - Q^\pi_2|$ for a fixed policy
$\pi$ by $(\alpha + \gamma R_{\max}\beta)/(1-\gamma)^2$, with rewards in
$[0, R_{\max}]$, reward difference $\alpha$ ($\varepsilon_r$ here) and $L_1$
transition distance $\beta$ ($2\varepsilon_P$ here): it is looser in the
reward term, and the bound above is looser by $\gamma$ in the transition
term. They use it to prove their Lemma 2, their improvement of Kearns and
Singh's Simulation Lemma [Kearns & Singh], an entrywise statement for a
finite MDP. Total
variation has no scale: a variable averaged over the level's own decision
interval ($\zeta$, $\hat\omega_j$, $\ell_j$) has a law that tightens with depth,
which drives $\varepsilon_P$ toward 1. The useful form assumes $\gamma V^\star_j$
is $L$-Lipschitz in the next state and uses the Wasserstein-1 distance
$\varepsilon_W$ instead: $\lVert Q^\star_i - Q^\star_j\rVert_\infty \le
(\varepsilon_r + L\varepsilon_W)/(1-\gamma)$. Lipschitz continuity is itself an
assumption; the mask thresholds can make $V^\star$ jump.

*The error terms grow under D-23 (added 2026-10-03).* The reward is a sum of
charges, so by the triangle inequality
$$\varepsilon_r \le \varepsilon^{\text{old}}_r + \varepsilon_I + \beta_R\,\varepsilon_{hd},$$
where $\varepsilon^{\text{old}}_r$ bounds the difference in the terms priced
before D-23 plus the per-job and read-byte terms (which (c2) covers through
$\vartheta$), $\varepsilon_I$ is the largest difference, over $(\hat x, a)$,
between the two levels' expected normalised priority-weighted interference
charges over a transition (each part with its weight, D §1), and
$\varepsilon_{hd}$ the same for the hidden-step charges, at the same
normalised state. Neither is zero in general:

- $\varepsilon_I$. By Proposition G.6, a level's charge per merged byte is set
  by the read cost per operation in its jobs' windows, weighted by bytes in
  the byte part and by priced device time in the busy part. The state carries
  the read intensity and $k_0/K_0$ as interval averages, not the timing of
  each job within L0's fill cycle, and two levels with the same mean
  $\tilde I$ can split it differently between the busy and byte parts
  and so respond differently to the read intensity. *Approximate size:*
  a transition merges about $C/k$ bytes on average, so if a level's merged
  bytes and its charge per merged byte are uncorrelated given $(\hat x, a)$,
  $\varepsilon_I$ is about $(1/k)$ times the largest difference in expected
  charge per merged byte, in units of $c_w$. Between L0 and the levels below
  the byte part alone differs by a factor 1.24–1.94 (G.6; derived), but L0
  is never pooled; between interior levels it is expected to be much
  smaller.
- $\varepsilon_{hd}$. Under skew, hidden versions concentrate on hot keys
  (on the `Assoc` pilots 1.15 hidden steps per returned entry at T=2 and 0.23
  at T=10, about 11 and 5.4 times the $S - 1$ of uniformly spread garbage on
  the same runs; measured, §0.7), and hot keys' versions sit mostly
  high in the tree. Two levels at the same normalised fill can
  therefore keep hidden shadowed versions that scans meet at very different rates.
  The level term $e^{hd}_j$ and the hidden-step ratio in $z_j$ carry part of
  this.

$\varepsilon_W$ grows too: read intensity and the hidden-step ratio are
averages over the level's own decision interval, so, like $\zeta$ and
$\hat\omega_j$, their laws tighten with depth. All of this bounds a
difference between pooled levels. At the current tree size no cell has a pool
(D-19), so nothing is pooled on these bounds until a dated entry admits a
level.

*Contention is where (c1) is only approximate.* With `max_background_jobs = 2`
and `max_background_flushes = max_background_compactions = −1` (db_bench's
defaults; the pipeline sets only the first, fingerprint field `bg2`),
`DBImpl::GetBGJobLimits` gives one flush slot, $\max(1, \lfloor 2/4\rfloor)$,
and one compaction slot, $\max(1, 2-1)$. The speed-up path can only set the
compaction count to one, so it is one in every state. The bottom-priority pool
counts against the same limit and db_bench starts it with no threads
(`db/db_impl/db_impl_compaction_flush.cc`, `GetBGJobLimits` and
`MaybeScheduleFlushOrCompaction`; unchanged from the contract's `7ea2d73` to
the recorded commit `8e903efd9`). `max_subcompactions` is 1, from db_bench's
`--subcompactions` default (`tools/db_bench_tool.cc`). A level that becomes
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
runs; PROP-1b re-checks (c1) under the controller, and PROP-6 (added
2026-10-03) re-checks (c2), which D-23's interference and hidden-step charges
no longer make automatic.

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
alone. Gate N1's pilot native runs (PREREGISTRATION D-16; the 2026-09-11
programme's artifacts are out of scope) decide every level that completes at
least $n_{\min}$ turnovers in them. Levels with fewer stay undecided and
unpooled (D-19, which withdrew their later decision on Gate N2's native arms).
On the 2026-10-01 pilots no cell had a pool (D-19).

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

**The new costs, the law and the admission test (added 2026-10-03, D-23).**
The law (G-i)–(G-v) keeps its form. Its reward is H §3's level cost, which
now carries the new charges (Proposition G.2). Its discount still counts
operations, and its cadence and membership rule are unchanged. The admission
test is unaffected, in the following sense.

**Lemma G.7 (the admission test does not depend on the prices; new
2026-10-03, D-23).** Every
statistic the test compares or bounds — the fill at fixed points of the level
clock and at release, bytes released per turnover, $(1-\xi_i)(\rho_i + o_i)$,
$\tilde\rho_i$, the inflow ratio $\ell_i$ and the slot wait $\omega_i$ — and
every rule that decides with them (D-16 §3–§5: the candidate set, the margins
in each statistic's own units, the block bootstrap with its fixed seed, and
$n_{\min}$) is a function of the runs' compaction and operation record alone:
each job's start level, bytes $S$, $O$ and $X$, trivial-move flag and
operation counts at its boundaries, the level fills, and the operation count.
No price, priority weight, reference rate $\bar q$ or cost term enters any of
them. So on a fixed set of runs, with the same random draws, the test gives
the same verdicts under every cost model, D-23's included, and D-19's record
(no pool in any cell, the run length from L2) stands unchanged.

*Proof.* By the definitions in this section and in D-16. A fill is bytes over
$C_i$. The level clock counts operations, $\Delta N/N_i$, and
$N_i = C_iq/\bar\lambda_i$ is $C_i$ times operations per byte of inflow over
the window. Bytes released per turnover are in units of $C_i$.
$(1-\xi_i)(\rho_i + o_i)$ and $\tilde\rho_i$ are ratios of bytes, and $\ell_i$
is a ratio of inflows. $\omega_i$ is a wait counted in operations, over
$N_i/k$. The candidate set uses $L$ and $B_L/C_L$. The margins, $k$, the block
length, the replicate count, the seed and the $n_{\min}$ grid are constants
fixed by D-16. The bootstrap and the $n_{\min}$ simulation resample the record
by rules D-16 fixes, so given their random draws they are functions of the
record. No price appears in any of these. $\blacksquare$

*On new runs.* The test's inputs change only if native compaction or the
foreground's speed changes: the pilots are `native` arms ($m \equiv 1$, the
configured $K_0$), in which RocksDB reads no price, and $\omega_i$, being
counted in operations, moves with the operation rate. D-23 changes the binary
(foreground-step counters in the host log's job records, iterator counters, an
SST-read timer, a scan set-up timer). Reusing the pilots' turnover counts on
that binary rests on its moving neither: ACT-4 tests native compaction on it
(A-Impl-10), as D-19 §4 did for the step-8 binary, and the foreground's speed
is tested by the native arms' throughput on it, paired against the pilots',
within a margin set by a dated entry before Gate N2 (D-24, §0.7 item 4) (Execution order, Gate N1).

*What D-23 does change for any future pool.* The support screen compares two
model inputs, $(1-\xi_i)(\rho_i + o_i)$ and $\tilde\rho_i$. Proposition G.2 now
has more ($\tilde c^{\langle i\rangle}_{job}/\bar s_i$, $\tilde I_i$, $e^{hd}_i$, $e^{ib}_i$,
$\tilde h_i$), along which $f_\theta$ could also be extrapolated, and (c2) is
no longer automatic (G.4). A dated entry that admits a level (D-19 §2(b))
would have to say whether the screen compares the new inputs. That choice is
deferred until a pool exists (D-24, §0.7 item 8); it changes nothing at the
current size, where no level is pooled.

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

*Amended 2026-10-03 (D-23).* An interior level's attributed cost now also
holds its own jobs' per-job prices, compaction bytes read and interference
(Proposition G.2), and the hidden steps scans take over the shadowed versions
it holds (Lemma D.19). So holding for garbage now costs reads as well as
space (Pathway A §4 (c)), and a release's price depends on the read cost of
the operations its jobs overlap (Pathway A §4 (b)). Native timing already
runs the jobs below L0 with L0 nearly empty, so an interior agent can mostly
avoid raising its interference, not lower it below native. Slot yielding,
(f), has almost no occasions at the default point (Pathway A §4).

The dynamic read levers are the L0 trigger, L0's use of an idle slot, interior
levels' yielding of the slot, and depth. Only slot yielding involves the pool.
The interior profile's read effect is static. In read priority any read gain
comes mainly from the L0 agent, from slot timing and from not deepening the
tree. This agrees with the 2026-09-11 record. *Amended 2026-10-03 (D-23):*
the L0 agent's levers are re-priced by Proposition A.8. An earlier L0 merge's
lasting effect is a lower trigger, which is static, and in read priority it
pays only above the floor $K^\star_R$, positive whenever interference on
reads is. With slot yielding also rare at
the default point, the read levers that act during a run have few occasions
on a stationary workload there, and most of their lasting effect is static.

### §6 Acceptance

| # | Criterion | Threshold | Instrument |
| --- | --- | --- | --- |
| PROP-1 | Admission recorded | collapse test run and pool membership recorded per (workload, $T$) before any learner run | Gate N1 |
| PROP-1b | Membership under control | for each pooled level, a one-step model of the normalised transition ($\hat x' \mid \hat x, a$: fill change, bytes released, time to next release) fitted on the pool has, on that level's held-out learner transitions, a mean residual whose 90% block-bootstrap interval lies inside $\pm\delta_{\text{kern}}$; and adding a level indicator lowers held-out error by less than $\delta_{\text{kern}}$. A level that fails leaves the pool at the next weight push, and the removal is logged. The check is conditional, not marginal, because each level's own policy changes its marginals | training log |
| PROP-2 | Transfer helps prediction | on each pooled level's held-out transitions, reported per level, the pooled model's normalised prediction error is below that of a model trained only on that level's data, paired over seeds | training log |
| PROP-3 | No harm | $J_\beta$ with the pooled deep-level agents acting is not worse than with those levels held (paired interval upper bound ≤ 0, or a margin fixed in advance); their attributed cost is reported alongside | paired evaluator |
| PROP-4 | Ablation | propagation on versus off (off = per-level models on own data), per mode | Gate N6 |
| PROP-5 | Related work | the §1 comparison with RusKey appears in the paper's related work | paper |
| PROP-6 | Reward equivalence under control (added 2026-10-03, D-23) | for each pooled level, a one-step model of the normalised attributed cost of a transition given $(\hat x, a)$, fitted on the pool, has, on that level's held-out learner transitions, a mean residual whose 90% block-bootstrap interval lies inside $\pm\delta_{\text{rew}}$; reported separately for the interference and hidden-step charges. A level that fails leaves the pool at the next weight push, and the removal is logged. G.4's (c2) is no longer automatic under D-23's costs, and PROP-1b checks only (c1). $\delta_{\text{rew}}$ is fixed in advance with PROP-1b's values (D-16 §8). Moot while no level is pooled (D-19) | training log |

The admission test's statistics contain no price (Lemma G.7), so the costs of
D-23 leave PROP-1's record (D-19) unchanged. PROP-1b, PROP-2, PROP-4 and
PROP-5 are unchanged. PROP-3 compares $J_\beta$, which now includes the new
costs.

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

**Amended 2026-10-03 (D-23).** The agents are now rewarded on every term of
D §1's amended cost that can be charged to a level. Each job's per-job cost,
compaction bytes read and written, and interference go to the job's start
level; a flush's $\tau^{job}_\iota$ goes to L0 and its interference to the
shared write-path bucket. The scan-iteration terms go to the levels D §4 names: iterator blocks
to their level, a hidden step to the level directly above the hidden entry's,
and each step's heap increment with L0 last. Six shared buckets go to no
agent (§3), the sixth, *fixed*, holding the fixed parts of Gets and scans and
the Puts' inserts. §2 adds the state inputs these terms need, §3 restates the reward,
Lemma H.5 shows why D §4 splits the heap increment with L0 last, Proposition
H.6 deals with charges made when a job completes, §6 says where the job counts
come from, §7 reprices the prior, and §9 adds two criteria. Propositions H.1,
H.2 and H.4 do not depend on what the cost contains; they now carry full
proofs. Proposition H.3 is restated, because its strict cost needs strictly
worse alternatives. Jobs are indexed by $\iota$ and levels by $i$ and $j$, as
everywhere (§1.1): a job serves the operations $(n^b_\iota, n^e_\iota]$, and
$\Delta n_\iota$, $Y_\iota$, $\tau^{job}_\iota$ and $I_\iota$ are D §1's.

### §1 Agents

- **L0 agent.** Actions on $K_0$ (Pathway A §2). Not pooled (A3′). It holds the
  main read lever (Proposition D.11). Its trigger also sets how many L0 merges
  run and the read cost per operation they run at: in native runs every L0
  merge ran with $k_0 = K_0$ (§0.7), and its interference is L0's own charge
  (§3).
- **Interior agents, levels $1..L-1$.** Actions on $m_i$. Levels that pass the
  admission test share one model (Pathway G); the others keep their own.
- **Last-level agent.** Actions hold and expand only; compact and defer have no
  meaning for a level far below its target. Its job is to stop the tree
  deepening when the last level nears its target (Theorem A.1). On `Assoc` at
  10M and T=10 it is idle, since the last level is far below its target
  (Theorem A.1, containment).
- **All agents** see the same priority vector $\beta$ and workload prices (the
  job, compaction-read, iteration, memtable and interference prices of D §1
  included), and all start from hold.

### §2 State

- **Interior agents:** the normalised state of G §3.
- **L0 agent:** $k_0/K_0$, $\bar K_0$, flush rate over its mean, L1 fill
  $\varphi_1$, the Get-to-write and scan-to-write rate ratios, the price ratios
  $\tilde R$ (with $\tilde R^o$, D-21) and L0's reopens per operation $e^o_0$,
  $\beta$, and the compaction slot's busy share over the last
  interval (L0 is the level most exposed to the single slot, G.4); and, as
  for interior agents (G §3), queue position, backlog and L2's fill
  $\varphi_2/m_2$. For lever (e) of Pathway A §4 it also sees whether the slot
  is idle now (the queue position's "none"), and the active memtable's fill
  over `write_buffer_size` (how soon the next flush adds an L0 file; RocksDB
  property `rocksdb.cur-size-active-mem-table`).
- **Last-level agent:** $\varphi_L/m_L$, headroom $(m_LC_L - B_L)/C_L$, the
  incoming burst, queue position and backlog.

**Inputs for the new cost terms** (amended 2026-10-03, D-23). Every agent also
gets the following for its own level $i$ (for L0, $i = 0$, with the divisor
$C_0$ of §3 wherever $C_i$ appears). Where G §3's list already has one of them,
it is the same input.

1. *Jobs running, by slot.* The compaction slot: idle, a merge, or a trivial
   move, with the start level's position relative to $i$ (G §3's none, above,
   same, below). The flush slot: whether a flush is running.
2. *Own job in flight.* When the running compaction is sourced at level $i$
   (for L0, also when a flush is running): the operations it has served so
   far, over the length of the decision interval; its source and overlap bytes,
   $(S + O)/C_i$; and the quiet priced cost per operation, by foreground step
   type (read steps, and the Put inserts for the write part),
   in the units of item 3, over the operations served since it began, which
   is the part of its window $W_\iota$ served so far whenever the job ends up
   serving at least $n^{\mathrm{win}}$ operations (D §1). Zero when no job of
   the level is running. The counter snapshots of the last
   $n^{\mathrm{win}} + n^{\mathrm{str}}$ operations, which a job shorter than
   $n^{\mathrm{win}}$ would need for an exact charge, are not in the state; Proposition H.6(iii) says what holds
   without them.
3. *Read intensity.* The quiet priced cost of the read steps served per
   operation over the last decision interval, split in two: L0's part per L0
   file, $\varrho^{(0)}$ (L0's read cost over the interval divided by the sum
   of $k_0$ over its operations; carried forward when L0 was empty
   throughout), and the rest, $\varrho^{(\ge1)}$. Every read step of a type
   that D §1's interference model covers counts, the shared buckets'
   included, since interference slows them wherever they are charged. The
   split assumes L0's read cost is linear in $k_0$: it is for seeks and
   reopens per file and for the probes of Gets that pass every L0 file; it
   is approximately linear for the probes of Gets that stop at a hit in an
   L0 file (concave, or even decreasing for hot keys, Proposition D.11(iii)), and an approximation for L0's heap increment
   (Lemma H.5) and its hidden steps. Both $\varrho^{(0)}$ and $\varrho^{(\ge1)}$
   are multiplied by $\bar q/(c_w\bar\lambda_1)$, which makes them dimensionless like G.2's price
   ratios, and both are kept by read-step type, which the interference model
   needs. The Put inserts' cost per operation, which sets the write part of a
   job's charge (D §1), is a constant of the operation mix and needs no
   input. The last-level agent also gets $k_0/K_0$, which the other agents
   already have.
4. *Iteration at the level.* Per scan: the hidden steps charged to level $i$,
   $e^{hd}_i$ (over the entries of level $i+1$; for L0, of L0 and L1; D §4),
   the iterator blocks of level $i$, $e^{ib}_i$, and the level's
   heap-increment charge $e^{hp}_i = h_i/c_{st}(1)$ (G §2). With the price
   ratios $\tilde R^{st} = q_{sc}c_{st}(1)/(c_w\bar\lambda_1)$ and
   $\tilde R^{ib} = q_{sc}c_{ib}/(c_w\bar\lambda_1)$, the level's iteration
   cost in G.2's units is
   $\tilde R^{st}(e^{hd}_i + e^{hp}_i) + \tilde R^{ib}e^{ib}_i =
   \tilde R^{st}e^{hd}_i + \tilde h_i + \tilde R^{ib}e^{ib}_i$. For L0 these are
   per L0 file.
5. *Job prices per source byte.* Over the level's recent merges, in units of
   $c_w$: their mean per-job cost per merged source byte,
   $\tilde c^{job}_i = c^{\langle i\rangle}_{job}/(c_w\bar s_i)$ (the price of
   kind $0$ at L0 and $d$ below), with $\bar s_i$ the mean source bytes per
   merge (Lemma D.18); and their mean interference charge per merged source
   byte, $\tilde I_i$, by part (read and write; G §2). Also the per-job cost
   of the level's trivial
   moves per byte moved, $\tilde c^{tm}_i = c^{tm}_{job}/(c_w\bar s^{tm}_i)$.

**Why these inputs, and why no others (Markov sufficiency).** G.4 (c1)–(c2)
and Proposition H.6(iii) need the expected reward of a transition, given the
state and the action, to depend on the past only through the state. Each new
term in the reward is a count the tree sets times a price fixed in advance
(A9). For each count, the state must carry what its conditional mean depends
on.

- *Per-job costs and compaction read bytes* ($\tau^{job}_\iota$, §3). The
  number of the level's jobs in the next interval is set by the inputs that
  already decide releases: fills, scores, the burst and the queue. Each job
  takes about one source file, so the jobs per byte released are set by file
  sizes, not by fills (Lemma D.18); item 5 carries them. A job's kind sets its
  price (item 1).
- *Interference* ($I_\iota$, §3). A job's charge is its bytes and priced
  device time, priced at the quiet read cost per operation over its window
  (D §1). That cost depends on $k_0$ around the job. In the seven native
  runs of §0.7 every L0 merge ran with $k_0 = K_0$, the mean $k_0$ during the
  merges of each deeper start level was at most 0.13, and no deeper job ran
  with more than one L0 file (§0.7); under the controller,
  slot blocking can raise $k_0$ above $K_0$ during a deeper job. Item 3's
  split and $k_0/K_0$ predict the read cost at the moment a job would run.
  One undivided average would overstate the deeper jobs' charges and
  understate L0's. Item 5 carries the level's realised charge per byte,
  whatever the coefficients' basis turns out to be (set by the calibration,
  D-24, §0.7 item 1).
- *Hidden steps and iterator blocks* (§3). Hidden versions sit on hot keys:
  about 5–11 times what uniformly spread garbage would give (§0.7). So
  no function of the fills, $\hat\rho_i$ and $\hat\xi_i$ predicts them, and
  item 4's recent rate is the state's estimate of the garbage near hot keys
  that the level keeps hidden. Holding and releasing move it within a run, so
  in G.4's terms it is part of the dynamic state $z_j$, not a level term.
- *Charges at completion* (Proposition H.6). A job that runs across a decision
  point is charged in the next interval, partly for operations served before
  it. Item 2 carries that part exactly for a job that serves at least
  $n^{\mathrm{win}}$ operations, and approximately for a shorter one
  (Proposition H.6(iii)).
- *The flush slot.* A running flush adds an L0 file when it ends, and the
  active memtable's fill does not show it, since a new active memtable starts
  empty when the flush begins. The next value of $k_0$, and with it L0's
  dueness and the read cost every job meets, depends on it.

Not added: the prices and the interference coefficients, which are fixed within
a run and the same at every level (the prior uses them, H §7); wall-clock
rates, such as job speeds and the operation rate, since the reward, like
$J_\beta$, is a function of the operation-indexed record (Lemma D.17) and time
enters it only through counts per operation, which items 2, 3 and 5 already
are; and, for each hidden entry, the level of the newer version that hides it,
which D §4's rule does not need (the charge depends on the entry's own level,
§3).

**Removed** from the 2026-09-11 state: the eleven stall-era pressure inputs, the
Lagrange multipliers, and the scan-work and space-estimate inputs (the scan
metric sat at its floor; the space estimate was retired by D-3). The iteration
inputs of item 4 are not the retired scan-work input: they count hidden steps
and iterator blocks, which D-23 prices.

### §3 Reward: attributed cost plus a counterfactual neighbour charge

For agent $i$ over a decision interval,
$$r_i = -\frac{c^\beta_i(\Delta t) + X_{i+1} + X_{i-1}}{c_wC_i},$$

- $c^\beta_i(\Delta t)$ is level $i$'s attributed, priority-weighted cost over
  the interval (Pathway D §4). Amended 2026-10-03 (D-23, with the
  write part separated), it is
  $$c^\beta_i = \beta_W\sum_{\iota\in\mathcal E_i}\tau^{job}_\iota \;+\; \beta_R\,\mathrm{Rd}_i \;+\; \sum_{\iota\in\mathcal E^\circ_i}I^\beta_\iota \;+\; \beta_S\,\frac{c_s}{\bar q}\sum_{n}g_i(n),$$
  where
  - $\mathcal E_i$ is the set of jobs sourced at level $i$ that complete in the
    interval: merges and trivial moves, and for L0 also flushes.
    $\mathcal E^\circ_i$ is the same set without flushes: a flush's
    interference goes to the write-path bucket (D-23).
  - $\tau^{job}_\iota = c^{\mathrm{kind}(\iota)}_{job} + c_{cr}(S_\iota + O_\iota) + c_wX_\iota$
    is the job's priced cost (D §1). A flush pays its per-job price and its
    bytes written; a trivial move pays its per-job price only. ($X_\iota$, a
    job's output bytes, is not the neighbour charge $X_{i\pm1}$ below.)
  - $I^\beta_\iota = \beta_RI^{\mathrm{rd}}_\iota + \beta_WI^{\mathrm{wr}}_\iota$
    is the job's interference charge (D §1) as $J_\beta$ weighs it: its bytes
    and priced device time at the reference rate, priced at the quiet cost
    per operation over its window, of the read steps (read part) and of the
    Puts' inserts (write part).
  - $\mathrm{Rd}_i$ is the quiet priced cost of the read steps charged to
    level $i$ (D §4): filter probes, false-positive block reads, run seeks and
    reopens, with the slot-blocking moves; the hidden steps charged to it
    (over the entries of level $i+1$; for L0, of L0 and L1), each at its base
    price and non-L0 increment together, $c_{st}(r^-)$; iterator blocks at
    $c_{ib}$; and its part of every step's heap increment: for L0 its own
    increment $c_{st}(r) - c_{st}(r^-)$ on every step, returned or hidden, and
    for a level $\ge 1$ its equal share of each returned step's non-L0
    increment (D §4).
  - $g_i(n)$ is the level's shadowed garbage (D §4) when operation $n$ is
    served, summed over the interval's operations (D §1's space rule).
- $X_{i\pm1} = \bar V_{i\pm1}(x'_{i\pm1}\mid a_i) - \bar V_{i\pm1}(x'_{i\pm1}\mid
  \text{hold})$ is the change in the neighbour's expected future cost caused by
  level $i$ taking $a_i$ instead of holding. It is evaluated with the neighbour's
  current value estimate, converted to currency by $c_wC_{i\pm1}$, and a one-step
  prediction of the neighbour's state: the burst $i$ releases or keeps
  (Theorem A.1) and the overlap that implies (Lemma D.8). Amended 2026-10-03
  (post-integration decision): for the upper neighbour, the prediction also
  moves the hidden-step input of §2 item 4, $e^{hd}_{i-1}$, by the hidden
  steps that leave level $i-1$'s charge when level $i$'s release carries its
  own hidden entries into level $i+1$ ($M^{hd}$, D §4). It is estimated as the
  share of level $i$'s bytes the predicted release moves, times the hidden
  steps per scan over level $i$'s entries (the fork's counter keyed by the
  entry's level, OBJ-9), which assumes those entries spread over level $i$'s
  files in proportion to bytes. (The level released into, $i+1$, keeps its
  hidden-step charge: the entries of level $i+2$ it covers stay hidden.)
- $X = 0$ when $a_i$ is hold.
- The divisor is a constant per agent. For the L0 agent it is
  $C_0 := K_0^{\text{cfg}}F$, the bytes L0 holds at its configured trigger, with
  $F$ as measured at $n_w$ and then held fixed; for the last-level agent it is
  $C_L$. A constant divisor leaves the agent's ranking of actions unchanged
  (Proposition G.3). The live $K_0F$ would not, since $K_0$ is the L0 agent's
  own action.

**Shared buckets** (amended 2026-10-03, D-23). Six buckets, parts of $\mathcal C_R$ and, for the last two, of
$\mathcal C_W$ too, are charged to no level, and no agent is rewarded on them
(D §4, Proposition D.16):

- the *hit-read* bucket: the block read of the table where a Get finds its key;
- the *reopen* bucket: reopens of no known level (none on the Get and iterator
  paths, D-21);
- the *scan-base* bucket: $c_{st}(1)$ for each entry a scan returns;
- the *memtable* bucket: $c_{mt}$ for each Get and each scan, the memtables'
  shares of returned steps' heap increments, and the hidden steps over
  memtable entries;
- the *write-path* bucket: the interference charge of flushes, read and write parts;
- the *fixed* bucket: $c^0_{get}$ for each Get and $c^0_{sc}$ for each scan
  (read part) and $c_{put}$ for each Put (write part).

The scan-base bucket, the fixed bucket and the memtable bucket's searches do
not depend on the policy at all: the number of Gets, scans and Puts, and the
entries each scan returns, are fixed by the operation sequence (Lemma D.19),
and the prices are fixed (A9). The rest of the memtable bucket depends on it only weakly: its
hidden steps through Lemma D.15's flush-timing caveat, and its heap shares
through the number of other children in the heap, which depth sets. The
hit-read bucket depends on the policy only through Lemma D.15's memtable
caveat. The write-path bucket does depend on it: a flush's charge is priced at
the read cost per operation over its window, and that read cost moves with
$k_0$ and the rest of the tree. No agent pays for that part. It is a gap
between the rewards and $J_\beta$, not between the decomposition and
$J_\beta$.

**Interference goes to its cause.** A job's interference charge prices its
work at the cost of every foreground step in its window, read steps at any
level and in any bucket, and the Puts' inserts. All of it, read and write
parts, goes to the job's start level (D-23's cause-side rule), the one level
whose decisions set when its jobs run; a flush's goes to the shared
write-path bucket. The reverse effect, foreground work (reads and
Puts) slowing the job, is inside the job's in-situ prices (A11) and is not
charged again. The charge is
made when the job completes, which can be one interval after some of the reads
it covers; Proposition H.6 says what that changes.

**Normalisation, re-checked** (2026-10-03). The divisor $c_wC_i$ is still the
price of writing $C_i$ bytes. It is no longer the write cost of one turnover,
which now also has compaction reads and per-job costs, and in read priority the
interference of the same jobs can be larger still. That does not matter for
decisions: Proposition G.3 needs only a positive constant per agent, and any
positive constant leaves every ranking of actions unchanged. What pooling needs
is that the normalised reward be the same function of the normalised state at
the pooled levels (G.4 (c2)). Over one turnover in steady state, level $i$
releases $C_i$ source bytes. The share $1-\xi_i$ is merged: each merged source
byte is read with its overlap, $1 + o_i$ bytes, writes $\rho_i + o_i$ bytes
(Lemma D.7), and carries on average the per-job cost $c_w\tilde c^{job}_i$ and
the interference $c_w\tilde I_i$ (§2, item 5). The trivially moved share
$\xi_i$ carries its per-job cost $c_w\tilde c^{tm}_i$ and the moves'
interference $c_w\tilde I^{tm}_i$. So the job-borne part of the level's cost
over one turnover, divided by $c_wC_i$, is
$$(1-\xi_i)\Big[\beta_W\Big(\rho_i + o_i + \frac{c_{cr}}{c_w}(1 + o_i) + \tilde c^{job}_i\Big) + \tilde I^\beta_i\Big] + \xi_i\big(\beta_W\,\tilde c^{tm}_i + \tilde I^{tm,\beta}_i\big),$$
with the interference priority-weighted by part (G §2), which is level-free
once $\tilde c^{job}_i$, $\tilde c^{tm}_i$, and $\tilde I_i$ and
$\tilde I^{tm}_i$ by part, are inputs, as $\rho_i$, $o_i$ and $\xi_i$ already are. This is
the job-borne part of Proposition G.2's price per turnover, in the same
accounting. $c_wC_i$ therefore stays the divisor. A divisor that tracked the
level's full cost per turnover would follow the scale better, but it would move
with the policy, and a divisor the agent's own actions move breaks
Proposition G.3, as the live $K_0F$ would for L0.

**The neighbour charge, re-checked** (2026-10-03). Its definition is unchanged.
$\bar V_{i\pm1}$ now values the neighbour's whole attributed cost. So a burst
that level $i$ releases into level $i+1$ is charged, through $X_{i+1}$, also
for the per-job cost, read bytes and interference of the merges it triggers
there. The one-step prediction models the burst and the overlap it implies,
and, since the post-integration decision of 2026-10-03, the upper
neighbour's hidden-step input (above); it holds the rest of items 2–5 of §2
at their current values. This stays inside the estimator's form: $X_{i-1}$
is still the neighbour's value at a predicted next state against its value
under hold, with one more state input predicted, and level $i-1$'s state
already carries that input (§2 item 4). So $X$ carries none of the
following:

- *interference through the read intensity*: an action that changes the read
  cost per operation changes the interference charge of every job that runs
  meanwhile, and that charge goes to those jobs' levels. At native timing every
  job below L0 runs with L0 almost empty (§2), so L0's trigger reaches other
  levels' charges little; the merges that run at $k_0 = K_0$ are L0's own;
- *effects beyond the two neighbours*, as before.

It carries the hidden entries a release carries down only as well as the
one-step estimate of $M^{hd}$ and $\bar V_{i-1}$'s response to its
hidden-step input allow (below).

**Hidden steps and credit** (rewritten for the attribution rule fixed
2026-10-03). D §4 charges a hidden step to the level directly above the level
where the hidden entry lives, because only that level's merges can remove the
entry. When level $i \ge 1$ releases into level $i+1$, it drops the hidden
entries of level $i+1$ that its released versions shadow ($D^{hd}$ in D §4,
weighted by the scans' steps over them), which lowers its own charge by
exactly the global change; and it carries its own hidden entries into level
$i+1$ ($M^{hd}$), where they stay hidden and become its own charge, while
level $i-1$, which was charged for them, is relieved. So a level cannot shed
its charge by passing entries down: its charge changes by
$-D^{hd} + M^{hd}$ against the global $-D^{hd}$, and level $i-1$'s by
$-M^{hd}$. The first draft's rule (charge the level where the entry lives)
did the opposite: a release lowered the releasing level's charge by
$M^{hd}$ and credited it nothing for $D^{hd}$, so a level could shed its
charge by passing entries down. Under the rule now in force the remaining gap
is a neighbour effect, and the neighbour charge closes it: its one-step
prediction includes $M^{hd}$ (the $X_{i\pm1}$ bullet above), so with
$X_{i-1}$ carrying level $i-1$'s relief, level $i$'s reward moves by
$(-D^{hd} + M^{hd}) - M^{hd} = -D^{hd}$, the global change. The prediction
uses the hidden steps over level $i$'s entries, which the fork counts by the
entry's level (OBJ-9), and the share of level $i$ a release carries down, from
the burst prediction; it does not need the level of the newer version. The
closure is as exact as that estimate and $\bar V_{i-1}$ are (ARCH-3 measures
the prediction); what remains is a bias against releasing of the size of
their error, and $J_\beta$ counts every hidden step, and every claim is
scored on $J_\beta$. For L0 there is no gap: its own entries
and those of L1 are both charged to L0, so an L0 merge changes L0's charge by
exactly the global change. And the last level, which holds many of the old
versions that later overwrites hide and can remove none of them, is no longer
charged for them; level $L-1$, whose releases remove them, is.

The charges do not model read shifts. A level that expands no longer pays the
block reads of the hits it takes over from deeper levels, since those go to the
hit-read bucket (D §4), but it is credited nothing for the filter probes saved
below it (G §5). The charges do carry the level's own reopens, at $c_{open}$
(D §4, D-21), its own iteration terms, and its own jobs' costs and
interference. The read effect of the profile is static and belongs to
$\Theta_s$. The one read effect an interior level has during a run, keeping L0
waiting for the compaction slot, is in its attributed cost $c^\beta_i$ through
the slot-blocking charge (D §4). A job that holds the slot while L0 is due also
runs while L0's read cost is raised, so its interference charge grows with the
wait as well.

In plain terms: a level pays for what it spends, and also for what its choice
makes its neighbours spend later. That is how a compaction that is good for one
level but bad for the next one is discouraged. A level also pays for the time
its jobs take beyond their bytes, and for slowing the reads that run while its
jobs run.

**Proposition H.1 (why a counterfactual charge, not a shaping term; proof
amended 2026-10-03).**

- (a) Adding $\gamma\Phi(s') - \Phi(s)$ to the reward, for any function $\Phi$ of
  the state, cannot change which policy is optimal [Ng et al.]. So charging a
  level for how its neighbour's value changes over time — a function of state
  that level $i$ observes — cannot make it take the neighbour into account.
- (b) The difference reward $D_i = G(a_i, a_{-i}) - G(\text{hold}, a_{-i})$, with
  $G$ the global cost and $a_{-i}$ the other agents' actions, changes by exactly
  $G$'s change when agent $i$ alone changes its action:
  $D_i(a') - D_i(a) = G(a', a_{-i}) - G(a, a_{-i})$ [Wolpert & Tumer].

*Proof.* (a) Proposition H.4 with every duration equal to 1 [Ng et al.,
Theorem 1]; for transitions of variable duration, Proposition H.4 itself.
(b) The subtracted term does not depend on $a_i$. $\blacksquare$

*Re-checked 2026-10-03 (D-23).* Neither part depends on what the cost contains,
so both hold with the new terms.

*The approximation* (amended 2026-10-03, D-23). By Proposition D.16 as amended,
$G$ is the sum of the level charges and the six shared buckets. So level
$i$'s own cost change plus exact neighbour charges equals $D_i$ exactly when
level $i$'s action changes no charge outside levels $i-1$, $i$ and $i+1$ and no
bucket. The reward above departs from $D_i$ in three ways. It restricts $G$'s
change to level $i$ and its two neighbours. It estimates the neighbours' change
with learned values and a one-step prediction of the burst (and, for the
upper neighbour, of the hidden entries a release carries down, "Hidden steps
and credit"), so it carries those only as well as the prediction does. And,
new with the 2026-10-03 terms, an action can move charges that the reward
leaves out: the interference of jobs at other levels that run at a read cost
the action set; and the write-path bucket, through the read cost while
flushes run. Under D §4's L0-last split an L0 action moves no other
place's heap charge (Lemma H.5); an equal split of the whole increment would
add that channel too. ARCH-3 measures the one-step state prediction the
charge relies on. The design follows counterfactual credit assignment in cooperative
multi-agent learning [Wolpert & Tumer; Foerster et al.]. It is not RusKey's
$\alpha$-mix of level and end-to-end latency.

**Lemma H.5 (L0's share of the heap increment; new 2026-10-03, D-23;
generalised the same day to memtable children).** Consider one scan step through
the merging iterator with $r^{\mathrm{L0}} \ge 0$ L0 children and
$r^{\neg0} \ge 1$ other children (memtables and level iterators), so
$r = r^{\mathrm{L0}} + r^{\neg0}$, at the price $c_{st}(r)$ (D §1). Write
$\Delta(r) = c_{st}(r) - c_{st}(1)$ for the heap increment. Two rules split it:

- *equal shares*: each child pays $\Delta(r)/r$, so L0 pays
  $E^{=} = r^{\mathrm{L0}}\,\Delta(r)/r$;
- *L0 last* (the rule of D §4): the $r^{\neg0}$ other children share
  $\Delta(r^{\neg0})$ equally, and L0 pays
  $Z^{\text{last}} = c_{st}(r) - c_{st}(r^{\neg0})$.

Then, for fixed $r^{\neg0}$, as functions of $r^{\mathrm{L0}}$:

- (i) both rules sum to $\Delta(r)$ exactly;
- (ii) under L0 last, $c_{st}(r) - Z^{\text{last}} = c_{st}(r^{\neg0})$ does
  not depend on $r^{\mathrm{L0}}$. A change of L0's file count changes L0's
  charge by exactly the change of the step's price, and changes no other
  child's charge;
- (iii) under equal shares, $c_{st}(r) - E^{=}$ is the same for every
  $r^{\mathrm{L0}} \ge 0$ if and only if $\Delta(r)/r$ is the same for every
  $r \ge r^{\neg0}$. Neither an affine price with a positive slope nor
  $a + b\log_2 r$ with $b > 0$ satisfies this;
- (iv) for $c_{st}(r) = a + b\log_2 r$, the change of $E^{=}$ per added L0
  file is 0.90–2.07 times the change of the step's price over
  $r^{\neg0} = 2$–8 and $r^{\mathrm{L0}} = 0$–8 (1.44–1.54 at
  $r^{\neg0} = 4$). For an affine price $\alpha + \beta' r$ with $\beta' > 0$
  it is 0.80–0.96 times at $r^{\neg0} = 4$, $r^{\mathrm{L0}} = 0$–5.

*Proof.* (i) Equal shares: $r$ shares of $\Delta(r)/r$. L0 last:
$\Delta(r^{\neg0}) + c_{st}(r) - c_{st}(r^{\neg0}) = \Delta(r)$. (ii) Direct;
the other children's total $\Delta(r^{\neg0})$ does not contain
$r^{\mathrm{L0}}$. (iii)
$c_{st}(r) - E^{=} = c_{st}(1) + r^{\neg0}\,\Delta(r)/r$, which is constant in
$r^{\mathrm{L0}} \ge 0$ if and only if $\Delta(r)/r = \Delta(r^{\neg0})/r^{\neg0}$
for every $r \ge r^{\neg0}$. For $\alpha + \beta' r$,
$\Delta(r)/r = \beta'(1 - 1/r)$, which strictly increases in $r$. For
$a + b\log_2 r$, $\Delta(r)/r = b\log_2(r)/r$, which differs at any two
consecutive integers: it takes $0$, $0.5b$ and $0.528b$ at $r = 1, 2, 3$ and
strictly decreases from $r = 3$ on, since $\log(r)/r$ decreases for $r > e$.
(iv) Evaluated directly, and re-evaluated in the integration check of
2026-10-03 (0.903–2.073; 1.443–1.541; 0.800–0.956). The ratios do not depend
on $a$, $b$, $\alpha$ or $\beta'$, which cancel from every difference or scale
both sides alike. $\blacksquare$

*Consequence.* The step's price is $c_{st}(r)$, and $r^{\neg0}$ is set by the
memtables and by depth, which the agents cannot change during a run except by
emptying a level. Under L0 last, then, for every step a scan takes, L0's heap
charge moves with $G$'s heap term exactly as Proposition H.1(b) asks, and no
other place's charge moves with L0's actions. When an L0 action also changes
how many steps a scan takes, through the hidden entries in L0's files or in
L1 that L0's files shadow, each added step is a hidden step charged to L0 (D
§4), its base and non-L0 increment $c_{st}(r^{\neg0})$ together with L0's
increment, so L0 pays the whole price of the steps it adds. Under equal
shares, L0's reward would misprice its trigger's effect on every step by the
factors of (iv), in a direction set by the price's curvature, and an L0 action
would move the levels' charges as well. Both rules keep Proposition D.16
exact; D §4 adopts L0 last (integration decision, 2026-10-03, to be recorded
with D-23).

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
- **Residual form.** $Q = -b + f_\theta + \delta_j$ (Pathway G; $b$ is a
  cost, $Q$ a reward). $f_\theta$ and
  $\delta_j$ start at zero; $b$ is code.
- **Replay** keeps only transitions with valid attribution; fallback intervals
  are excluded.
- **Job charges** (amended 2026-10-03, D-23). A transition's reward includes
  the charges of the jobs that complete in it (§3). Each job's interference is
  computed from the foreground-step counts over its window, read from its begin and
  end records and the counter snapshots (§6, D §1). A charge is known when its
  job completes, before the transition closes, so no transition waits. Item 2
  of §2 lets the target carry a charge that lands one transition after the
  decision that caused it (Proposition H.6(iii)). A transition in which a job
  completes without its records has invalid attribution and is not replayed.
- **Discount on operations.** Under D-23's reference-rate rule the reward, like
  $J_\beta$, is a function of the operation-indexed record (Lemma D.17): the
  interference charge counts priced device time at $\bar q$, not the job's
  wall span or the operations it overlapped. So (G-i)'s reason for discounting
  on operations holds with the new terms unchanged.

**Proposition H.2 (an unmasked target overestimates; amended 2026-10-03).** Let $\mathcal A(s')
\subsetneq \mathcal A$ for some reachable $s'$. If the target maximises over all
of $\mathcal A$, its fixed point $\tilde Q$ satisfies $\tilde Q \ge Q^\star$
pointwise, where $Q^\star$ is the masked fixed point. The inequality is strict at
every $(s, a)$ that reaches, with positive probability, a state $s'$ in which a
forbidden action has a higher value under $\tilde Q$ than every allowed action.

*Proof.* A maximum over a superset is at least the maximum over the subset, so
the unmasked Bellman operator $\tilde{\mathcal T}$ dominates the masked one
$\mathcal T$ pointwise. Both are monotone contractions, so by induction
$\tilde{\mathcal T}^nQ \ge \mathcal T^nQ$ from any common start
($\tilde{\mathcal T}^{n+1}Q \ge \tilde{\mathcal T}\mathcal T^nQ \ge
\mathcal T^{n+1}Q$), and the limits satisfy $\tilde Q \ge Q^\star$. At the fixed
points, $\tilde Q(s,a) - Q^\star(s,a) = \gamma\,\mathbb E\big[(\max_{\mathcal A}
\tilde Q(s',\cdot) - \max_{\mathcal A(s')}\tilde Q(s',\cdot)) +
(\max_{\mathcal A(s')}\tilde Q(s',\cdot) - \max_{\mathcal A(s')}
Q^\star(s',\cdot))\big]$. Both brackets are non-negative, and the first is
positive on the states named, which $(s, a)$ reaches with positive probability.
$\blacksquare$ (Proof expanded 2026-10-03; the statement now says under which
values the forbidden action is highest. It does not depend on the cost terms.)

In plain terms: the learner credits states with the value of actions it will
never be allowed to take there.

**Proposition H.6 (charges at job completion; new 2026-10-03, D-23; restated
the same day for the charge of D §1, with (iii)(a) stated in law).** Fix an agent $i$ and its
decision points $n_0 < n_1 < \dots$, counted in operations. Transition $t$
covers operations $(n_t, n_{t+1}]$ and carries the discount
$\gamma_i \in (0, 1)$ of (G-iv). Let a job $\iota$ sourced at level $i$ serve
the operations $(n^b_\iota, n^e_\iota]$ and complete at operation count
$n^e_\iota$, with charge $C_\iota \ge 0$ (its $\tau^{job}_\iota$, its
$I_\iota$, or their sum). For comparison, split the charge over the
operations the job serves, $C_\iota = \sum_{n^b_\iota < n \le n^e_\iota}c_\iota(n)$
with every $c_\iota(n) \ge 0$ (for interference, in proportion to each
operation's quiet read cost, say); a lump such as $\tau^{job}_\iota$, and any
charge of a job that serves no operation, sits at $n^e_\iota$.
*Completion charging* puts $C_\iota$ in the transition that contains
$n^e_\iota$. *Run-time charging* puts each $c_\iota(n)$ in the transition that
contains $n$. Let $d_\iota$ be the number of decision points $n_t$ with
$n^b_\iota < n_t < n^e_\iota$, and $d = \max_\iota d_\iota$.

- (i) Over any run of whole transitions that contains every job's operations,
  the two give the same total.
- (ii) From any decision point $n_s$, let $G^{\text{comp}}$ and
  $G^{\text{read}}$ be the agent's discounted cost, under each rule, of the
  jobs that start at or after $n_s$. Then $\gamma_i^{\,d}\,G^{\text{read}} \le
  G^{\text{comp}} \le G^{\text{read}}$. With $d \le 1$, completion charging
  lowers that discounted cost by at most the share $1 - \gamma_i$, which is
  below $1/(n_Hk)$ under (G-iv) (for L0, $1/(n_HK_0^{\text{cfg}})$).
- (iii) Assume one compaction slot (G.4), and that an action does not move
  RocksDB's write-slowdown state. Suppose the state at $n_t$ contains, for each
  of the agent's jobs in flight, the inputs of §2 item 2: its kind, its bytes
  $S$ and $O$, the operations served so far, and the quiet cost per
  operation by foreground step type over the operations since it began.
  Then:
  - (a) the conditional law of the charge of a job already in flight, given
    the state at $n_t$, does not depend on the action taken at $n_t$, up to
    `SetOptions`' own overhead (ACT-2), so that charge's conditional
    expectation shifts every action's value at $n_t$ by the same amount
    (corrected from "does not change the charge", which holds only in law:
    the job's window is random even under one action);
  - (b) the charge of a job in flight at $n_t$ whose window turns out to be
    its own span ($\Delta n_\iota \ge n^{\mathrm{win}}$; with $n^{\mathrm{win}}$
    set by D §1's rule this is every merge the guard of D §1 covers) is a
    function of those inputs and of what happens after $n_t$;
  - (b′) the charge of a job that ends with $\Delta n_\iota <
    n^{\mathrm{win}}$ (a trivial move, a short flush, a job in a stall) is
    such a function only if the state also carries the counter snapshots of
    the last $n^{\mathrm{win}} + n^{\mathrm{str}}$ operations before $n_t$ (the
    cumulative quiet cost by type at each snapshot). §2 item 2 does not carry them, so
    without them its charge also depends on where in those operations its
    window starts.

  So completion charging makes the reward exactly as Markov as the state is
  (G.4 (c1)) when the state carries the snapshots, and, with item 2 alone,
  up to the charges of the jobs shorter than $n^{\mathrm{win}}$, which OBJ-8
  counts.

*Proof.* (i) Both rules put every $c_\iota(n)$ in some transition of the run.
(ii) For $n^b_\iota < n \le n^e_\iota$, let $t(n)$ be the transition
containing $n$ and $t_e$ the one containing $n^e_\iota$. Every decision point
between them lies strictly inside $(n^b_\iota, n^e_\iota)$, so
$t_e - d_\iota \le t(n) \le t_e$. Hence
$\gamma_i^{\,d_\iota}\gamma_i^{\,t(n)} \le \gamma_i^{\,t_e} \le
\gamma_i^{\,t(n)}$. Multiply by $c_\iota(n) \ge 0$, sum over $n$ and over the
jobs (a lump at $n^e_\iota$ is the same under both rules), and use
$\gamma_i^{\,d_\iota} \ge \gamma_i^{\,d}$. Under (G-iv),
$\gamma_i = e^{-1/(n_Hk)}$, and $1 - e^{-x} < x$ for $x > 0$. (iii)(a) A
RocksDB compaction runs to its end on the inputs it was picked with; a
`SetOptions` call changes targets and scores, not a running job (A-Impl-4).
With one compaction slot, no other compaction runs before it ends, so the
tables change meanwhile only through flushes, which the action does not
schedule. The client's operation sequence is fixed (A8), and by assumption the
action does not slow it. Which operations the rest of the job overlaps, and so
its window and the steps in it, still depends on how the job's thread and the
client interleave (Proposition C.6), so it is random even under one action;
but the action changes none of the inputs of that interleaving, so its
conditional law given the state is the same whichever action is taken. The
one exception is the action's own `SetOptions` call (hold makes none): it
takes the DB mutex and writes the OPTIONS file, which can shift the
interleaving slightly, and ACT-2 measures that overhead. The job's bytes are
fixed by the inputs it was picked with. (b) D §1's charge is
$I_\iota = \bar q\sum_x\bar\varrho^{\,x}_\iota(\kappa^B_xY_\iota + \kappa^J_{x,\mathrm{kind}(\iota)}t^{job}_\iota)$:
$Y_\iota$ and $t^{job}_\iota$ are functions of the kind and the bytes, and
$\bar\varrho^{\,x}_\iota$ is the cost per operation over $W_\iota$ (D §1). If
$\Delta n_\iota \ge n^{\mathrm{win}}$, $W_\iota = (n^b_\iota, n^e_\iota]$; its
part up to $n_t$ is $(n^b_\iota, n_t]$, whose cost is the mean of item 2
times $n_t - n^b_\iota$, and the rest happens after $n_t$. (b′) If
$\Delta n_\iota < n^{\mathrm{win}}$, $W_\iota$ starts at the last snapshot at
or before $n^e_\iota - n^{\mathrm{win}}$; its part up to $n_t$ lies within
the last $n^{\mathrm{win}} + n^{\mathrm{str}}$ operations before $n_t$ and
starts at a point set by $n^e_\iota$; the snapshots give its cost for every
such start, and item 2's mean over the operations since the job began does
not. The same holds for $\tau^{job}_\iota$, which needs no read cost.
$\blacksquare$

*The assumption in (iii).* The pending-bytes limits, which the multipliers
reach through A-Impl-3, are 64 and 128 GiB here against about 3 GiB held
(G §3), so an action does not move the write-slowdown state in these runs.

*Measured* (host logs' job records, measured phase, the seven native runs of
§0.7: the $\bar q$ arm `qbar-assoc-d21id` at T = 10 and the Gate N1 pilots'
first repeats, `Assoc` and power law at T = 2, 6 and 10;
host-log job records and operation stamps). Every compaction job, merges and
trivial moves alike, served fewer operations than one decision interval of its
level at $k = 10$ (for L0, one flush interval), so $d \le 1$ throughout; no
trivial move served more than 484 operations, against intervals of at least
11,000. Flushes had no begin record in these runs' host logs (D-23 adds one,
Gate N0 item 10), and their spans were not measured; a flush's interference
goes to the write-path bucket, and its other charges are lumps at completion. No L0 merge contained an L0 decision point
(0 of 1,137, 538, 538, 538, 169, 169 and 169): each starts at the flush that
makes L0 due and ends before the next flush. If interior decision points fell
independently of job starts, 0.7–3.4% of L1's merges and at most 0.6% of
deeper merges would contain one; a "compact" action starts its job at the
decision point, which lowers that share.

*What the delay changes.* $J_\beta$ and OBJ-1 are totals over the measured
phase, so by (i) the delay does not touch them (§5 says why every job lies
inside the measured phase). For the agents, it costs at most the factor
$\gamma_i$ on the delayed charges (ii), and it biases nothing else once item 2
of §2 is in the state, apart from the charges of jobs shorter than
$n^{\mathrm{win}}$ (iii)(b′): the target $r + \gamma\,Q_{\text{target}}(s',
\cdot)$ carries a charge caused in one transition and made in the next through
$s'$, as it carries every other delayed cost. Without item 2, the charge of a
job in flight is averaged over states that look alike, and the measured shares
above bound how often that happens.

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
- **Job charges and the measured phase** (amended 2026-10-03, D-23). Job
  charges are made at completion, as bytes always were (OBJ-1). Both ends of
  the measured phase are clean.
  - *At $n_w$.* No job is running: `WaitForCompact`, flushes included, has
    returned, and the hold serves no operation and admits no due level, so
    nothing starts before $n_w$. No charge straddles $n_w$, and no operation before
    $n_w$ is charged.
  - *At the end.* The drain serves no operations and runs every job to its end
    inside the measured phase. A job that runs only in the drain has
    $\Delta n_\iota = 0$ and is charged over the last $n^{\mathrm{win}}$
    operations of `mixgraph` (D §1). A job that starts during `mixgraph` and
    ends in the drain is charged over the last
    $\max(\Delta n_\iota, n^{\mathrm{win}})$ operations up to the end of
    `mixgraph`. Both are charged inside the measured phase, in the final
    partial interval that OBJ-1's log closes at the end of the drain. Drain
    intervals run under the fallback and are not replayed, so these charges
    reach $J_\beta$ and OBJ-1 but no agent's reward.

  So every job charge of the measured phase is counted once, and
  Proposition H.6(i) applies to the whole phase.
- **Stalls** (amended 2026-10-03, D-23; rewritten for the speed-neutral
  charge the same day). A job that runs entirely inside a write stop serves
  no operations, so $\Delta n_\iota = 0$, and D §1 charges it at the read cost
  of the $n^{\mathrm{win}}$ operations before its end, as if it had run at the
  reference rate (Lemma D.17(v)). Physically it slowed no read; the charge
  prices the work, not the moment. So a learner gains nothing in interference
  by leaving compaction for stalls, and the per-level rewards inherit that.
  Stall time itself is still unpriced, so the stall rule (Global acceptance)
  still bars any claim that defers work into stalls, and ARCH-8 reports the
  compaction completed with $\Delta n_\iota = 0$.

**Proposition H.3 (exploration has a cost; the 2026-09-11 Proposition B.3;
amended 2026-10-03, D-23).** Model a run as a finite-horizon decision problem
whose state $s$ makes it Markov. This is an idealisation: $s$ holds everything
that affects the run, job progress and timing included, and the joint action
$a$ is every agent's action at a decision. The cost is $J_\beta$ (D §2), a
function of the operation-indexed record. Let $V^\star_n$ and $Q^\star_n$ be the
optimal expected cost-to-go from decision $n$, and $A^\star_n(s, a) =
Q^\star_n(s, a) - V^\star_n(s) \ge 0$ the advantage gap of $a$ in $s$. Suppose
that holding throughout, at the configuration the run starts from, is optimal,
and call that policy $\theta^\star$. Then for every policy $\pi$, exploration
included,
$$J(\pi) - J(\theta^\star) = \mathbb E_\pi\Big[\sum_n A^\star_n(s_n, a_n)\Big] \;=:\; \mathcal R \;\ge\; 0,$$
and $\mathcal R > 0$ if and only if, with positive probability, $\pi$ takes at
some decision a joint action whose advantage gap is positive. Exploration among
actions tied with hold, that is, whose expected cost-to-go in that state equals
hold's, costs nothing.

*Proof.* Let $c_n$ be the cost incurred between decisions $n$ and $n+1$, the
drain's cost included in the last one, and set $V^\star = 0$ after the last
decision. By the definition of $Q^\star$ and the Markov property,
$\mathbb E[c_n + V^\star_{n+1}(s_{n+1}) \mid h_n, a_n] = Q^\star_n(s_n, a_n)$
for every history $h_n$ ending in $s_n$, whatever the policy that produced it.
So $\varepsilon_n := c_n + V^\star_{n+1}(s_{n+1}) - Q^\star_n(s_n, a_n)$ has
conditional mean 0, and
$c_n = A^\star_n(s_n, a_n) + \big(V^\star_n(s_n) - V^\star_{n+1}(s_{n+1})\big)
+ \varepsilon_n$. Summed over the decisions, the middle terms telescope to
$V^\star_0(s_0)$, whose mean is $J(\theta^\star)$ because $\theta^\star$ is
optimal. Taking
expectations gives the identity (the finite-horizon performance-difference
identity). Each $A^\star_n \ge 0$,
so $\mathcal R \ge 0$, and the sum of non-negative terms has positive mean if
and only if one of them is positive with positive probability.
$\blacksquare$

*Correction.* The 2026-09-11 statement claimed $\mathcal R > 0$ for every
controller that "explores non-degenerately". Exploration among tied actions
has zero regret, so strictness needs a positive advantage gap, as now stated
(2026-10-03).

The hypothesis — that a constant action is optimal — is exactly what Conjecture
B.3′ doubts. The 2026-10-03 terms leave that unchanged: they make each job's
cost depend on the read cost around the moment it runs, which is a reason for state
dependence only where that read cost varies in time beyond what the static
knobs set (Pathway B, Conjecture B.3′ as amended).

*Consequence.* Exploration is reported as a cost, and the first $N_x$
turnovers after $n_w$ are logged separately.

**Proposition H.4 (shaping with variable durations; the 2026-09-11
Proposition D.1; proof amended 2026-10-03).** For semi-Markov transitions of duration $\tau$, the term
$F = \gamma^{\tau}\Phi(s') - \Phi(s)$ leaves optimal policies unchanged; the
single-step form $\gamma\Phi(s') - \Phi(s)$ does not when $\tau$ varies.

*Proof* (written out 2026-10-03; the statement is unchanged). Let transition
$t$ have reward $r_t$, duration $\tau_t \ge \tau_{\min} > 0$, and discount
$\gamma^{\tau_t}$, with $0 < \gamma < 1$; let $\Gamma_0 = 1$ and
$\Gamma_{t+1} = \Gamma_t\gamma^{\tau_t}$, so a return is
$\sum_t\Gamma_tr_t$. Let $\Phi$ be bounded, and $\Phi = 0$ at a terminal state
if there is one. With $F_t = \gamma^{\tau_t}\Phi(s_{t+1}) - \Phi(s_t)$,
$$\sum_{t<T}\Gamma_tF_t = \sum_{t<T}\big(\Gamma_{t+1}\Phi(s_{t+1}) - \Gamma_t\Phi(s_t)\big) = \Gamma_T\Phi(s_T) - \Phi(s_0),$$
which tends to $-\Phi(s_0)$, since $\Gamma_T \le \gamma^{T\tau_{\min}} \to 0$
(or $\Phi(s_T) = 0$ at the end of a finite horizon). So for every policy and
every first action, the shaped value is $Q(s, a) - \Phi(s)$, every action's
value in $s$ moves by the same amount, and the optimal policies are the same.
With the single-step form, $\sum_t\Gamma_t(\gamma\Phi(s_{t+1}) - \Phi(s_t)) =
-\Phi(s_0) + \sum_t\Gamma_t(\gamma - \gamma^{\tau_t})\Phi(s_{t+1})$ in the
limit, and the second sum depends on the actions through $\tau_t$ and
$s_{t+1}$. An example where the ranking flips: from $s_0$, action $a$ reaches
$s_1$ in duration 1 with reward 0, action $b$ reaches $s_1$ in duration 2 with
reward $-\eta$, $\eta > 0$; from $s_1$ one transition of duration 1 and reward 0
ends the episode, with $\Phi(s_1) = \phi > 0$ and $\Phi = 0$ at the end.
Unshaped, $a$ is better by $\eta$. With the single-step form, the shaped values
are $Q(a) - \Phi(s_0)$ and $Q(b) - \Phi(s_0) + \gamma(1-\gamma)\phi$, so $b$ is
better whenever $\eta < \gamma(1-\gamma)\phi$. $\blacksquare$

It applies to any shaping term added later. It does not depend on what the
cost contains, so the 2026-10-03 terms leave it unchanged.

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
- **Job records carry foreground-step counters** (amended 2026-10-03, D-23). Each
  job's and flush's begin and end records carry the cumulative
  foreground-step counters by type (read steps, and Gets, scans and Puts),
  and the host log writes the same counters every
  $n^{\mathrm{str}}$ operations (D §1's instrument, a fork change, so a new
  binary). For every completed job the plugin logs its kind, start level,
  operations served, bytes, and the difference of each foreground-step
  counter (read steps, and the Puts for the write part) over its window
  $W_\iota$, all unpriced. It also logs, per level and interval, the hidden
  steps (by the entry's level), iterator blocks and heap-increment counts that
  D §4's split needs, and the shared buckets' counts. The trainer prices
  these counts. The evaluator computes $\mathcal I$ from the same records in
  the host log. Plugin and evaluator thus use one estimator, which OBJ-1's
  identity for interference needs (ARCH-7).
- **The trainer shares the machine** (amended 2026-10-03, D-23). It runs on its
  own core, but it shares memory bandwidth and caches with `db_bench`, the
  mechanism the 2026-10-03 contention diagnostics measured (§0.7), and so
  does the plugin's thread. Neither is a job,
  so both are outside A10 and unpriced; OBJ-6 reports their CPU time. They
  enter no count, so $J_\beta$ does not see them. In the per-run interference
  check (OBJ-8(b)) each stratum's prediction is built from the same stratum's
  units with no job running. A slowdown that falls equally on a stratum's
  units with and without a job running scales its prediction and its
  measured time alike, so it cancels from each stratum's comparison to first
  order; a part that tracks the jobs (for example, the trainer working mainly
  while jobs complete) does not cancel and is read by the check as
  interference.

### §7 Analytic prior $b$

$b(\hat x, a)$ is the expected change in normalised cost over one turnover from
taking $a$ instead of hold, computed from Lemmas D.7–D.10 and Proposition G.2
with the current measured inputs. Amended 2026-10-03 (D-23), it also uses
Lemmas D.18 and D.19, Proposition D.11 as amended, and the new prices and
interference coefficients of D §1.

*What it prices.* $b$ is a prior for $Q_j$, which level $j$'s reward trains, so
each term prices a change in what that reward contains: the level's attributed
cost and its neighbour charges (§3). A term the reward does not contain would
only be unlearned by $f_\theta$. Every job an action causes is priced as
$\tau^{job}$ plus the interference it would carry (D §1): its per-job price,
$c_{cr}$ per byte read, $c_w$ per byte written, and its bytes and priced
device time at the reference rate, times the quiet read cost per operation
*around the moment the job would run* (the read part), and times the Puts'
cost per operation, a constant of the mix (the write part, weighted
$\beta_W$). That read cost comes from §2's item 3 and $k_0$, with
the measured correlation (§0.7): L0 merges run at $k_0 = K_0$, and natively
released deeper jobs at the mean read cost the level's merges have met. The prior uses item 3's $\varrho^{(0)}$ and $\varrho^{(\ge1)}$ in money
per operation, before item 3's normalisation. It prices:

- **a compaction now**, at level $j \ge 1$. It releases $\Delta\varphi\,C_j$
  bytes before RocksDB would, of which the share $1-\xi_j$ is merged
  (Lemma D.7). (1) Their extra overlap at the current fill (Lemma D.8),
  $c_j(o_{\text{now}} - f_j)$ per merged source byte, is read and written, at
  $\beta_W(c_w + c_{cr})$ per byte. (2) Their interference, at the current
  read cost instead of the mean their level's merges have met. Let
  $$\hat I_j(\varrho, o) = \bar I^{\mathrm{rd}}_j\,\frac{\varrho}{\bar\varrho_j}\cdot\frac{\bar Y_j(o)}{\bar Y_j(\bar o_j)}$$
  be the predicted read part of the charge per merged source byte at read
  cost $\varrho$ per operation and overlap $o$, with
  $\bar I^{\mathrm{rd}}_j = c_w\tilde I^{\mathrm{rd}}_j$ (G §2). The write
  part per merged source byte does not depend on the read cost; it is
  $\bar I^{\mathrm{wr}}_j\,\bar Y_j(o)/\bar Y_j(\bar o_j)$ at overlap $o$. Here
  $\bar\varrho_j$ and $\bar o_j$ are the means over the level's recent
  merges, and $\bar Y_j(o)$ is the bytes per source byte on the byte basis
  at overlap $o$, $(\rho_j + o) + \lambda(1 + o)$ (D §1; whether the byte
  part is nonzero, and $\lambda$, are set by the calibration, D-24, §0.7
  item 1). The term is
  $(1-\xi_j)\,\Delta\varphi\,C_j\,\{\beta_R[\hat I_j(\varrho_{\text{now}},
  c_jo_{\text{now}}) - \hat I_j(\bar\varrho_j, \bar o_j)] + \beta_W\bar I^{\mathrm{wr}}_j[\bar Y_j(c_jo_{\text{now}}) - \bar Y_j(\bar o_j)]/\bar Y_j(\bar o_j)\}$, where
  $c_jo_{\text{now}}$ is the overlap now (Lemma D.8 with the measured overlap
  constant) and $\varrho_{\text{now}} = \varrho^{(\ge1)} + k_0\,\varrho^{(0)}$
  in money per operation. D §1's charge is linear in the window's read cost;
  that it scales with the bytes per source byte is exact for the byte part
  and an approximation for the busy part, whose priced device time also
  holds the per-job price. (3) No per-job term: merges take
  about one source file each (Lemma D.18), and the same source bytes leave
  either way, so the job count does not change to first order. (4) The
  slot-blocking charge it would incur if L0 falls due while its job holds the
  slot (Pathway D §4), from L0's fill and the active memtable's fill, with L0's
  per-file read cost of §2 item 3, iteration terms and reopens included.
- **a deferral**, by (1) the garbage it keeps (Pathway D §4); (2) the growth of
  the level's own hidden-step charge: level $j$ is charged the hidden steps
  over level $j+1$'s entries (D §4), whose shadowing versions it holds, so the
  prior scales $e^{hd}_j$ by the relative growth of $B_j$, at $c_{st}(r^-)$
  per step, which assumes those entries grow in proportion to the level's held
  bytes at a fixed hotness profile (0 until measured); (3) nothing for the
  hidden entries of its own that a release would have carried into level
  $j+1$ ($M^{hd}$, §3): they would have joined its own charge and left its
  upper neighbour's, which the neighbour charge carries, and the two cancel
  in the reward to the accuracy of that charge's prediction; and
  (4) the burst it builds (Theorem A.1). The part of the burst the level
  below has no room for is rewritten there, priced as merges at level $j+1$:
  bytes written and read, per-job cost $\tilde c^{job}_{j+1}$ and interference
  $\tilde I^\beta_{j+1}$ per merged source byte (read and write parts, weighted).
- **an expansion**, by its space bound (Lemma D.14) and the same hidden-step
  terms as a deferral. The space bound counts every added byte as held, while
  the reward charges only the shadowed garbage $g_j$ (D §4; live bytes do not
  depend on the policy, Lemma D.15). This term is the one exception to the rule
  above: it prices expansion up to $1/(1-\tilde\rho_j)$ times higher than the
  reward will, a pessimistic prior on a move that holds data longest.
- **for the L0 agent**, the change in its cost per operation of holding the
  trigger at $K$. It is Proposition D.11's cost as amended, restricted to what
  L0 is charged, and split by who bears it, $g(K) = g_J(K) + g_R(K)$:
  $$g_J(K) = \frac{1}{KQ_F}\Big[\beta_W\big(c^0_{job} + (c_w + c_{cr})(KF + m_1C_1)\big) + \hat I^{\mathrm{L0},\beta}(K)\Big],\qquad g_R(K) = \beta_R\Big[\Big(\frac{K-1}{2} + \frac{N^{(0)}(K)}{Q_F}\Big)\varrho^{(0)}_- + \bar h_0(K)\Big].$$
  $g_J$ is borne by the L0 merges: $1/(KQ_F)$ of them per operation, with
  $Q_F$ the measured operations per flush (Lemma D.18, Proposition D.11),
  each reading and writing $KF + m_1C_1$ bytes (uniform-key overlap and
  nothing dropped at L0, as in Proposition D.11), each with interference
  $\hat I^{\mathrm{L0},\beta}(K)$: D §1's charge for a merge with those bytes
  and that priced device time, its read part at the quiet read cost per
  operation $\varrho^{(\ge1)} + K\,\varrho^{(0)}$, since L0 merges run at
  $k_0 = K$ (Proposition D.11's (d2)), weighted $\beta_R$, and its write part
  at the Puts' cost per operation, weighted $\beta_W$. $g_R$ is borne by reads: the factor before
  $\varrho^{(0)}_-$ is Proposition D.11's mean L0 count over its cycle, with
  $N^{(0)}(K)$ the operations an L0 merge serves, affine in its bytes and
  measured; $\varrho^{(0)}_-$ is L0's read cost per operation per file
  without its heap increment (probes, false-positive reads, seeks, reopens at
  $\rho_gc_{open}$ per Get probe and $\rho_sc_{open}$ per seek, with $\rho_g$
  and $\rho_s$ L0's measured reopens per probe and per seek since the
  controller started, 0 until measured, as D-21 §2(e) defines them; the
  hidden steps charged to L0 and iterator blocks; §2 items 3 and 4); and $\bar h_0(K)$ is
  L0's heap-increment charge per operation averaged over L0's cycle, in which
  $k_0$ steps through $0, 1, \dots, K-1$ one flush at a time and sits at $K$
  while the merge runs. Under D §4's L0-last split (Lemma H.5), $\bar h_0$ is
  the cycle mean of $\frac{q_{sc}}{q}(\bar R_{nx} +
  \bar R_{hd})\,[c_{st}(k_0 + r^{\neg0}) - c_{st}(r^{\neg0})]$, with $r^{\neg0}$
  the measured mean number of other children, which moves with $G$'s heap
  term exactly (Lemma H.5(ii)). Every input not measured yet contributes 0.
  Compact is charged
  the change of $g_J$ at once and credited the change of $g_R$ only while the
  slot is idle; defer and expand are charged $g(K') - g(K)$; each over one L0
  turnover, $N_0$ operations, divided by $c_wC_0$. The write-path bucket
  moves with $K$ too, through the read cost while flushes run, but no agent is
  charged for it (§3), so the prior leaves it out.

*Consequences.* $g$ need not be convex in $K$ once $\bar h_0$ is in, since the
step price is expected to be concave in its argument (D §1; Proposition D.11
as amended). The prior compares $g$ at two triggers and uses neither convexity
nor $K_0^\star$. In read priority, $g_J$ carries the merges' interference,
its read part at $\beta_R$. An early L0 compaction runs more merges per operation, each at a
lower read cost, so the prior can price lever (e) as a loss even while the slot
is idle (Pathway A §4 as amended, Proposition A.8(iii)).

*Units and sign.* $b$ is defined here as a change in normalised *cost*, and
G §3's $Q_j$ is an expected normalised *reward* (rewards are negative costs),
so the law enters $b$ with a minus sign, $Q_j = -b + f_\theta + \delta_j$
(G §3, H §4). The 2026-09-29 text wrote $+b$, which disagreed in sign; the
2026-10-03 amendment corrected it, matching `controller/prior.h`, which
computes the cost and notes that a learner must negate it. Which convention
the paper keeps is the owner's (§0.7, item 9); the terms above are stated as
costs.

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
| ARCH-3 | Counterfactual input | one-step prediction of neighbour fill after an action versus realised, and (amended 2026-10-03, D-23) of the upper neighbour's hidden-step input (H §3, $M^{hd}$) versus realised, errors reported per level pair; if above a tolerance fixed in advance, that pair uses the local reward only | policy log |
| ARCH-4 | Exploration and no freeze | exploration share and cost reported per level; the chosen action varies with state (flip rate and correlation with $\varphi$ reported where a level has ≥ 10 turnovers) | policy log |
| ARCH-5 | Plugin parity (amended 2026-10-03, D-23) | plugin at $m \equiv 1$ inside the oracle-parity envelope; re-run on every new binary, the one with D-23's job-record counters and timers included | paired evaluator |
| ARCH-6 | Inference agreement | C++ inference and Python evaluation of the same weights agree on ≥ 99.9% of logged decisions | replay |
| ARCH-7 | Reward pricing (new 2026-10-03, D-23) | on every learner arm: the attribution log carries every count the reward of §3 prices: per level and interval, jobs by kind, compaction bytes read and written, hidden steps (by the hidden entry's level, mapped to the charged level), iterator blocks and the heap counts D §4's split needs; per completed job and flush, its start level, kind, operations served, bytes and the difference of each foreground-step counter (read steps, and the Puts for the write part) over its window $W_\iota$; and the six shared buckets' counts (the fixed bucket's counts of Gets, scans and Puts included). The trainer's priced per-level costs, before $\beta$, normalisation and neighbour charges, summed over levels and over every interval from $n_w$ to the end of the drain, plus the priced buckets, equal the evaluator's $\mathcal C_W$ and $\mathcal C_R$, the write and read parts of interference included, each in its own cost, for the same window: apart from OBJ-1's listed boundary jobs, to a relative $10^{-9}$ | attribution log, training log, evaluator |
| ARCH-8 | Charge timing (new 2026-10-03, D-23; Proposition H.6) | reported per level: the share of its jobs whose operations contain a decision point of the level, the largest number $d$ that any one job contains, and the share of the level's job and interference charges those jobs carry; and the compaction jobs, and their bytes, completed with $\Delta n_\iota = 0$ (charged over their windows, D §1). Threshold: $d \le 1$ for every job, so that Proposition H.6(ii)'s factor is $\gamma_i$; a breach is reported with the factor $\gamma_i^{\,d}$ at the measured $d$, and the in-flight inputs of §2 item 2 are checked to be in the state | policy log, host log |

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
| `Assoc` operation mix on the ZippyDB parameter fit (primary) [Cao et al.] | `db_bench` mixgraph with `Assoc`'s operation mix (0.806/0.159/0.035, Cao et al. §7.4) on the key-range, key, value-size and scan-length parameters that Cao et al. publish for ZippyDB's fit (`Prefix_dist`, their App. A.3 and §7.3; §7.4 gives no numeric fit for `Assoc`), with the deviations of §2 (B1; attribution corrected 2026-10-03) | "the `Assoc` operation mix on Cao et al.'s ZippyDB parameter fit" (corrected 2026-10-04, D-24, §0.7 item 13; D-1's earlier label misattributed the distributions) |
| Read-heavy power-law mix (Gate N2's second workload, D-13 §3) | `mixgraph` with Get/Put/Seek ratios 0.95/0.05/0, one key range, keys from the fit's key-hotness power law (`key_dist` $a$ = 0.002312, $b$ = 0.3467, ZippyDB's in App. A.3) | "YCSB-B operation mix with power-law key popularity" (never "Zipfian") |
| Other Cao et al. mixes | published fits where available, e.g. the ZippyDB mix (which reuses the distributions above with ZippyDB's own mix) | realistic |
| Zipfian mixes in the style of YCSB A, B, C [Cooper et al.] and F (YCSB's core workloads, not in that paper [YCSB]) | YCSB, or `db_bench` once it has a Zipfian generator, which it lacks at `8e903efd9` (D-13 §3) | standard |
| Read-heavy / write-heavy / mixed | operation mix swept | synthetic |
| Garbage level | overwrite share and key-space size swept, giving low and high $g_{\text{flow}}$ | synthetic |
| Hot ranges | `keyrange_num` $\in \{5, 30, 100\}$ | synthetic |
| Deletes (B3) | `mix_delete_ratio` $\in \{0, 3, 6\}\%$ | synthetic |
| Phases (B2) | two phases first, more later; in-process `db_bench` phase patch | synthetic |

Every run records its family and parameters in the fingerprint. A static
comparator measured on one family is never a comparator for a policy measured on
another (the workload form of the knob-parity rule).

**Which cost terms a workload exercises (added 2026-10-03, D-23).** Since
D-23 a scan pays for its iteration, not only for its seeks (Pathway D §1,
Lemma D.19), so the suite's scans decide which read terms are live.

- **`Assoc` scans are long.** 3.5% of its operations are scans
  (`mix_seek_ratio` 0.035), and their lengths follow the fit's heavy-tailed
  generalized Pareto (ZippyDB's `iter_k`, `iter_sigma`; median about 27
  entries; `config.sh`). Measured on Gate N1's pilots (repeat 1): about 496M
  `Next`s per 26.1M operations, about 543 entries returned per scan, and 509
  GB returned in every arm. Hidden internal entries stepped over
  (`rocksdb.number.iter.skip`): 570M at T = 2 and 114M at T = 10 (node
  diagnostics of 2026-10-03, quoted in §0.7, `db/n1-assoc`).
- **The power-law mix has no scans** (`mix_seek_ratio` 0). On it the
  iteration terms, the scan-base bucket and the scan part of garbage's read
  price (Proposition B.5) are zero, and garbage costs reads nothing directly
  (§3). It is the suite's control for everything Pathway D adds on the scan
  side.

Which entries a scan returns is fixed by the operation sequence (Lemma D.19),
so the iteration terms that depend on the policy are the hidden entries, the
heap steps and the iterator blocks, all of which the policy moves only through
the tree it holds.

### §2 Implementation notes carried forward

1. **(Done 2026-09-20.)** `WORKLOAD_SKEW` selects the family. Deviations from the
   published fit (ZippyDB's parameters, with `Assoc`'s mix, §1): `value_theta`
   is 925.5, not 0, keeping the mean value
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
6. **(Added 2026-10-03, D-23.) Garbage where scans meet it.** Per arm, per
   level: hidden entries stepped over and iterator blocks loaded, per scan,
   from the per-level counters of Gate N0 item 10 (OBJ-9), reported beside
   item 4's dropped bytes, both by the level where the hidden entry lives and
   by the level Pathway D §4 charges (the level directly above it), so that
   what holding garbage costs the scans (Proposition B.5) is seen at the level
   that holds it and at the level whose merges would drop it (WL-3).

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

(Re-checked 2026-10-03: B.1′ is a statement about bytes, and D-23 leaves it
unchanged.)

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

**Proposition B.1″ (the drop budget bounds the elision saving; stage model;
amended 2026-10-03, D-23).**
Fix a window in which A7 holds. Model the tree as merge stages $0..L-1$ with the
accounting of Lemma D.7 and no trivial moves: bytes that are not dropped pass
through the stages in order; a stage-$i$ merge writes $w_i = 1 + o_i$ bytes per
source byte, less one byte for each byte it drops, from the source or from the
overlap; the $o_i$ are held fixed, as Theorem A.2(ii) holds its weights. Let $V$
be the bytes entering stage 0 in the window (the flushed bytes), and $G$ the
obsolete bytes available to compaction in it: garbage resident at the window's
start plus obsolete versions flushed during it. Let $D_j$ be the bytes dropped
by stage-$j$ merges.

- (i) *Bytes written* (the 2026-09-29 statement). Against the same flow with
  nothing dropped, the compaction write cost saved is at most
  $G\,(1 + \sum_{i=1}^{L-1}w_i)$, so the relative saving is at most
  $$\frac{G}{V}\cdot\frac{1 + \sum_{i\ge1}w_i}{\sum_{i\ge0}w_i} \;\le\; \frac{G}{V},$$
  the last step because $w_0 \ge 1$. With equal weights $w$ the factor is
  $(1 + (L-1)w)/(Lw)$, between $(L-1)/L$ and 1.
- (ii) *Priced compaction cost* (added 2026-10-03). Since D-23 a merge costs
  its job, the bytes it reads and its interference, not only the bytes it
  writes (Pathway D §1). Give stage $i$ the priced cost per source byte
  entering it
  $$c^{\text{stage}}_i = \beta_W\Big[(c_w + c_{cr})(1 + o_i) + \frac{c^{\langle i\rangle}_{job}}{\bar s_i}\Big] + \bar I^{\mathrm{gross}}_i ,$$
  and let a byte dropped by a stage-$j$ merge save $c^{\text{drop}}_j = \beta_Wc_w +
  I^X_j$ at stage $j$ itself. Here $c^{\langle i\rangle}_{job}$ is the
  per-job price of a merge sourced at level $i$ ($c^0_{job}$ at $i = 0$,
  $c^d_{job}$ for $i \ge 1$, the job kinds of D §1); $\bar s_i$ is the mean
  source bytes per stage-$i$ merge (Lemma D.18); $\bar I^{\mathrm{gross}}_i \ge 0$
  is the priority-weighted interference charge (D §1: the read part at
  $\beta_R$, the write part at $\beta_W$) of stage-$i$ merges per source byte
  entering the stage, *gross* of drops, that is, in the flow in which nothing
  is dropped; and $I^X_j \ge 0$ the part of $\bar I^{\mathrm{gross}}_j$ that one
  byte not written removes. (The realised $\bar I_i$ of §1.1 and G §2 is net
  of the drops; using it here would count the drop credit twice. The symbol
  is kept apart for that reason.) Hold $\bar s_i$, $\bar I^{\mathrm{gross}}_i$
  and $I^X_j$ fixed, as the $o_i$ are, and assume A9. Write
  $\Psi_j = c^{\text{drop}}_j + \sum_{i>j}c^{\text{stage}}_i$ for the priced value of one byte
  dropped at stage $j$. Then $\Psi_j$ is nonincreasing in $j$, the priced
  compaction cost saved is
  $$\sum_jD_j\Psi_j \;\le\; G\,\Psi_0 = G\Big(c^{\text{drop}}_0 + \sum_{i\ge1}c^{\text{stage}}_i\Big),$$
  and the relative saving is at most
  $\frac{G}{V}\cdot\Psi_0\big/\sum_{i\ge0}c^{\text{stage}}_i \le \frac{G}{V}$. With equal
  $c^{\text{stage}}_i = c^{\text{stage}}$ and $c^{\text{drop}}_j = c^{\text{drop}}$ the factor is
  $(c^{\text{drop}} + (L-1)c^{\text{stage}})/(Lc^{\text{stage}})$, again between $(L-1)/L$ and 1. Part (i) is
  the case $c^{\text{stage}}_i = w_i$, $c^{\text{drop}}_j = 1$: prices in
  units of $\beta_Wc_w$, with $c_{cr}$, the job prices and interference set
  to zero.

*Proof.* In steady state a level releases what reaches it, so the volume
entering stage $i$ is $V_i = V - \sum_{j<i}D_j$. (i) By Lemma D.7 stage $i$
writes $w_iV_i - D_i$. With nothing dropped the cost is $V\sum_iw_i$, so the
saving is
$\sum_jD_j\,(1 + \sum_{i>j}w_i) \le \big(\sum_jD_j\big)(1 + \sum_{i\ge1}w_i)$.
Every obsolete version is dropped at most once, so $\sum_jD_j \le G$.

(ii) Each term of $c^{\text{stage}}_i$ is a price times a quantity proportional to $V_i$,
less a drop term proportional to $D_i$. A stage-$i$ merge reads
$S + O = (1 + o_i)S$ bytes whether or not it then drops some (Lemma D.18), so
stage $i$ reads $(1 + o_i)V_i$; it writes $(1 + o_i)V_i - D_i$, as in (i); it
runs $V_i/\bar s_i$ merges, by the definition of $\bar s_i$; and its
priority-weighted interference charge is $\bar I^{\mathrm{gross}}_iV_i - I^X_iD_i$,
by the definitions of $\bar I^{\mathrm{gross}}_i$ and $I^X_i$. So stage $i$ costs $c^{\text{stage}}_iV_i - c^{\text{drop}}_iD_i$, the
no-drop flow costs $V\sum_ic^{\text{stage}}_i$, and the saving is
$$V\sum_ic^{\text{stage}}_i - \sum_i\Big(c^{\text{stage}}_i\big(V - \textstyle\sum_{j<i}D_j\big) - c^{\text{drop}}_iD_i\Big) = \sum_jD_j\Big(c^{\text{drop}}_j + \sum_{i>j}c^{\text{stage}}_i\Big) = \sum_jD_j\Psi_j .$$
Next, $c^{\text{drop}}_j \le c^{\text{stage}}_j$ for every $j$. Under each basis, a byte
not written removes at most the interference that one source byte carries
($I^X_j \le \bar I^{\mathrm{gross}}_j$), at the same cost per operation over the
merges' windows, which is held fixed (D §1); this holds step type by step
type, and so for the weighted sum, every $\beta_x \ge 0$. In the byte part,
$Y_\iota = X_\iota + \lambda(S_\iota + O_\iota)$: a byte not written lowers
$X_\iota$ by 1 and leaves the bytes read unchanged, while one source byte
carries $(1 + \lambda)(1 + o_j)$ in the no-drop flow, so it removes
$1/((1+\lambda)(1+o_j)) \le 1$ of that byte part, for every $\lambda \ge 0$
(and nothing in the limit of bytes read alone). In the
busy part, a byte not written shortens the merge's priced device time by
$c_w/p_{\mathrm{dev}}$, while one source byte carries
$[(c_w + c_{cr})(1 + o_j) + c^{\langle j\rangle}_{job}/\bar s_j]/p_{\mathrm{dev}}$,
which is larger. With $c_{cr}, c^{\langle j\rangle}_{job} \ge 0$ and
$1 + o_j \ge 1$, $c^{\text{drop}}_j \le c^{\text{stage}}_j$.
Hence $\Psi_j - \Psi_{j+1} = c^{\text{drop}}_j + c^{\text{stage}}_{j+1} - c^{\text{drop}}_{j+1} \ge
c^{\text{drop}}_j \ge 0$, so $\max_j\Psi_j = \Psi_0$, and
$\sum_jD_j\Psi_j \le \Psi_0\sum_jD_j \le G\,\Psi_0$. Finally
$\Psi_0 \le \sum_{i\ge0}c^{\text{stage}}_i$ because $c^{\text{drop}}_0 \le c^{\text{stage}}_0$. With equal
weights, $\Psi_0/\sum_ic^{\text{stage}}_i = (c^{\text{drop}} + (L-1)c^{\text{stage}})/(Lc^{\text{stage}})$, and
$0 \le c^{\text{drop}} \le c^{\text{stage}}$ puts it in $[(L-1)/L, 1]$. $\blacksquare$

*Scope.* This is a statement about the stage model. Three things lie outside
it. The comparison flow keeps every stale version at the bottom, so $B_L$ and
$o_{L-1}$ would grow, and the model holds them fixed. Trivial moves write
nothing: a heavy share at stage 0 would make the effective $w_0$ fall below 1,
and a heavy share at later stages, as over a window that contains the load
(Lemma D.7), lowers the no-drop cost, so every relative figure below assumes
that trivial moves are rare; with up to 71% of the bytes leaving levels $\ge 1$
moving trivially, as on the uniform whole run, a bound valid for any placement
of drops can exceed 58%. A window that contains the
load is not steady. Added 2026-10-03 (D-23): since D-23 a trivial move costs
its per-job price $c^{tm}_{job}$ although it writes nothing (Lemma D.18);
the stage model has none, so (ii) prices none. The comparison flow is a
reference for compaction cost only: its scans would step over every stale
version, and (ii) does not price that. The read side of garbage is
Proposition B.5. And (ii) holds $\bar I^{\mathrm{gross}}_i$ fixed although the read cost per
operation around a stage's jobs moves with the garbage the tree holds (scans
step over it, B.5) and with $k_0$ (L0→L1 merges run at $k_0 = K_0$, D §1):
dropping garbage early also lowers the interference price of later jobs, a
second-order effect (ii) leaves out.

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

These are figures in bytes written, part (i). Part (ii)'s priced figures need
the D-23 prices ($c_{cr}$, the per-job prices, $\kappa$) and are not yet
computed; their ceiling is the same $G/V$.

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
locality, so it is an upper bound, and it is computable from native's
per-level drops (§2 item 4) on Gate N2's native arms (amended 2026-10-03: the
2026-09-29 text said "the Hull-0 artifacts", which belong to the 2026-09-11
programme and are out of scope, D-16). A bound that uses locality needs a
model of where overwrites of one key meet, and is open. The proof also shows
where elision pays most: a byte dropped at stage $j$ saves one byte there and
every later stage, so drops high in the tree are worth the most.

*Priced form* (added 2026-10-03, D-23). With part (ii)'s weights, the same
argument bounds the priced compaction saving over native by
$$\sum_jD^{\text{nat}}_j\,(\Psi_0 - \Psi_j) \;+\; G_{\text{res}}\,\Psi_0,\qquad \Psi_0 - \Psi_j = c^{\text{drop}}_0 - c^{\text{drop}}_j + \sum_{i=1}^{j}c^{\text{stage}}_i \;\ge 0 .$$
*Proof.* Against the no-drop flow, $\pi$ saves $\sum_jD^\pi_j\Psi_j \le
\Psi_0\sum_jD^\pi_j \le \Psi_0\big(\sum_jD^{\text{nat}}_j + G_{\text{res}}\big)$
by (ii), and native saves exactly $\sum_jD^{\text{nat}}_j\Psi_j$; both are
measured against the same no-drop flow, so the difference of the savings is
native's priced compaction cost less $\pi$'s. $\Psi_0 - \Psi_j \ge 0$ because
$\Psi$ is nonincreasing. $\blacksquare$
With $c^{\text{stage}}_i = w_i$ and $c^{\text{drop}}_j = 1$ (part (i)'s units) it is the
bound in bytes above. The
weights rank stages as before: since $\Psi_j$ is nonincreasing, high drops are
still worth the most, and since $c^{\text{stage}}_i \ge \beta_Wc_ww_i$ and
$c^{\text{drop}}_j \ge \beta_Wc_w$, a drop is worth at least its bytes written priced at
$\beta_Wc_w$. What the bound leaves out is the other side of the trade:
holding garbage long enough to drop it high costs space and, where scans run,
read cost (Proposition B.5).

**Measured on `Assoc`** (D-3, measured denominator): $S_{\text{flow}} = 1.386$ at
T = 2, 6 and 10; $g_{\text{flow}} = 0.278$; resident $S$ 1.03–1.09. The
constant-survival *estimate* — not established by its proof, and above what
B.1″ allows for the whole run at every ratio when trivial moves are rare — is
58%, 35% and 35% (audit §4).
The B.1 and B.2 tables of 2026-09-11 (uniform workload, defective live-data
estimate) are withdrawn; Lemma D.14 replaces B.2's space budget.

**Garbage now also costs scans** (added 2026-10-03, D-23). Until D-23 held
garbage cost only space. A scan steps over every hidden version of each key
it steps past (Lemma D.19), and since D-23 each step is priced (D §1). So
garbage is priced twice, and where it sits matters: on `Assoc`, Gate N1's
pilots stepped over 1.15 hidden entries per returned entry at T = 2 and 0.23
at T = 10 (§1). Spread uniformly over keys and scans, garbage would give about
$S - 1$ per returned entry, and the same pilots' $S$ at run end gives 0.105–
0.107 at T = 2, 0.048 at T = 6 and 0.043 at T = 10 (§0.7). The measured
counts are about 11, 5 and 5.4 times that (10.8–11.0, 5.1–5.5 and 5.4–5.5
over three repeats; `db/n1-assoc/graphs/summary.csv`). Hidden versions sit
where scans go: the hot key ranges are both the most overwritten and the most
scanned. (Garbage during a run can exceed garbage at its end, which makes the
ratios upper estimates. A 2026-10-03 draft compared these counts with D-3's
2026-09 Hull-0 runs and found 13–17 and 5–7 times; the pilots' own $S$ is the
like-for-like denominator.)

**Proposition B.5 (the read price of held garbage; new 2026-10-03, D-23).**
Fix a workload and its operation sequence (A8). An *obsolete version* $e$ is
a version, written by the load or by a Put, whose key $k_e$ is written again
later; it is the same object under every policy, since sequence numbers follow
the write order, and the operation that makes it obsolete, the next Put of
$k_e$, does not depend on the policy. $|e|$ is its bytes in a table. Under a policy, $e$ is
*resident and hidden* at operation $n$ if, when $n$ reads the store, $e$ is
in a memtable or in a table of the current version while a newer version of
$k_e$ exists. Let $N^{\text{hid}}_e$ be the number of operations at which it is, and
$N^{\text{tab}}_e \le N^{\text{hid}}_e$ the number at which $e$ is in a table. Let
$n^{\text{sc}}_e$ be the number of scans that step over $e$. Assume:

- (H1) (Lemma D.19(ii).) A scan steps over $e$ only if $e$ is resident and
  hidden when it reads the store and $k_e$ is one of the keys the scan steps
  past (one per `Next` call, from the key its seek lands on). It steps over
  every such $e$ except those a `DBIter` reseek jumps over (Lemma D.10). No
  snapshots are held (`mixgraph` takes none) and there are no tombstones
  (A6).
- (H2) Operations are drawn independently and identically, as a stationary
  `mixgraph` draws type, key and scan length; whether $e$ is resident and
  hidden when operation $n$ reads the store is independent of operation
  $n$'s own draw (it is set by the earlier operations and by the background
  jobs, which do not see that draw); and the set of visible keys does not
  change during the phase (on `Assoc` every Put rewrites a loaded key, Lemma
  D.10).

Then:

- (a) *Exchange identity.* Over any window, the hidden steps of all scans sum
  to $\sum_en^{\text{sc}}_e$, and their priced cost
  $\sum_{\text{scans}}\sum_{\text{hidden steps}}c_{st}(r)$ is
  $\sum_e$ of the price of $e$'s own crossings. This needs neither (H1) nor
  (H2).
- (b) *Expected crossings.* Under (H1) and (H2),
  $\mathbb E[n^{\text{sc}}_e] \le \frac{q_{sc}}{q}\,p_{\text{cov}}(k_e)\,\mathbb E[N^{\text{hid}}_e]$,
  with equality when no reseek skips $e$. Here $q_{sc}/q$ is the scans' share
  of the operations, a constant of the mix, and $p_{\text{cov}}(y)$ the
  probability that key $y$ is among the keys one scan steps past.
- (c) *Holding price.* Let $\bar c_{\text{hd},e}$ be the mean price of one
  crossing of $e$ (the expected priced cost of $e$'s crossings over their
  expected number; a crossing costs $c_{st}(r)$ at the heap size of its
  moment). The expected part of $\beta_S\mathcal C_S + \beta_R\mathcal C_R$
  that $e$ causes as garbage, its table bytes and the scans' steps over it,
  satisfies
  $$\beta_S\frac{c_s}{\bar q}\,|e|\,\mathbb E[N^{\text{tab}}_e] + \beta_R\,\bar c_{\text{hd},e}\,\mathbb E[n^{\text{sc}}_e] \;\le\; c^{\text{hold}}_e\,\mathbb E[N^{\text{hid}}_e],\qquad c^{\text{hold}}_e = \beta_S\frac{c_s|e|}{\bar q} + \beta_R\frac{q_{sc}}{q}\,p_{\text{cov}}(k_e)\,\bar c_{\text{hd},e},$$
  with equality when no reseek skips $e$ and $N^{\text{tab}}_e =
  N^{\text{hid}}_e$, up to Lemma D.15's caveats (per-file metadata, flushes
  in progress). $c^{\text{hold}}_e$ is $e$'s holding price per operation: at
  a fixed mean crossing price, each operation by which a policy delays $e$'s
  drop costs $c^{\text{hold}}_e$ in expectation.
- (d) *Gets never step over garbage.* A Get never reaches an obsolete
  version of its own key, so its counted read steps are those of the runs it
  passes down to its key's newest version (Lemma D.10). Other keys' obsolete
  versions reach a Get only through the tree's shape: which tables' key
  ranges cover its key, and how many tables and levels there are.
- (e) *Depth bounds the hidden versions of one key.* Under A2, while L0 holds
  $k_0$ files and levels $1..L$ are populated, a key with $n^{\text{mt}}$ versions in
  the memtables has at most $n^{\text{mt}} + k_0 + L - 1$ hidden versions, so a scan
  steps over at most that many per key it steps past (the table part is
  Lemma D.19(iv)).

*Proof.* (a) Count the pairs (scan, hidden entry it steps over) by scan and
by entry. With (A6) every hidden entry is an obsolete version. The priced form
counts each pair at its own price.

(b) By (H1) without reseeks, $n^{\text{sc}}_e = \sum_n\mathbf 1\{e \text{
resident and hidden at } n\}\,\mathbf 1\{n \text{ is a scan that steps
past } k_e\}$; with reseeks it is at most that. The second indicator is a
function of operation $n$'s draw and of the visible key set, which is fixed
(H2); the first is independent of that draw (H2). So each term's expectation
factors: $\mathbb E[\mathbf 1\{\cdot\}]\cdot(q_{sc}/q)\,p_{\text{cov}}(k_e)$,
the draws being identically distributed.
Summing over $n$ gives $(q_{sc}/q)\,p_{\text{cov}}(k_e)\,\mathbb E[N^{\text{hid}}_e]$.

(c) Space: $\mathcal C_S = (c_s/\bar q)\sum_nH(n)$ (D §1), and $H(n)$ is the
live bytes in tables, which no policy changes up to Lemma D.15's caveats, plus
the bytes of the obsolete versions in tables. So $e$ adds $(c_s/\bar q)\,|e|$
for each of its $N^{\text{tab}}_e$ operations. Reads: by (a), $e$'s crossings cost
$\bar c_{\text{hd},e}\,\mathbb E[n^{\text{sc}}_e]$ in expectation, by the
definition of $\bar c_{\text{hd},e}$; apply (b). The bound uses
$N^{\text{tab}}_e \le N^{\text{hid}}_e$.

(d) Runs are searched newest first: memtables, then L0 files newest first,
then L1 to $L$ (Lemma D.10). RocksDB keeps every key's versions in that order,
newest first; the search order of Lemma D.10 relies on this invariant, which
data moving only downward (A4) and every merge keeping the newest version of
each key it holds (no snapshots) maintain. A Get stops at the first run
holding its key, which holds the key's newest version, so it never reaches an
obsolete version of its own key. Its filter probes, block-reading probes,
reopens and memtable search are counted per run passed (Lemma D.10); other
keys' versions enter those counts only through which runs cover the key and
how many runs there are.

(e) A2: each level $\ge 1$ holds one sorted run, so at most one version of a
key; each L0 file, the output of one flush or one intra-L0 compaction, holds
at most one (Lemma D.19(iv)). So a key has at most $n^{\text{mt}} + k_0 + L$ resident
versions, one of them visible. $\blacksquare$

*What the proposition does not cover.* Iterator blocks are not split by
entry: hidden entries in tables make a scan load more blocks. Under Lemma
D.19(v)'s block model a step in a table loads $1/b_{\text{blk}}$ of a block in
expectation ($b_{\text{blk}}$ entries per block, about 4 here), so a crossing
of $e$ in a table adds about $c_{ib}/b_{\text{blk}}$ to $\bar c_{\text{hd},e}$;
outside that model the share is an approximation. Garbage also moves Gets'
counts indirectly, through the tree's shape (a deeper tree, more tables, more
reopens), which is a property of the configuration, not of one version. And
(H2) fails on a phased workload, where $q_{sc}/q$ and $p_{\text{cov}}$ change
with the phase; (b) then holds phase by phase.

*Who pays it in the per-level rewards* (2026-10-03). B.5 is about $J_\beta$.
In the rewards, Pathway D §4 charges each crossing of $e$, at its base price
and non-L0 heap increment, to the level directly above the level where $e$
lives at that moment, the only level whose merges can drop $e$. So the read
part of $e$'s holding price falls on the level whose holding keeps $e$
resident, as the space charge $g_i$ does; the block loads stay with the level
of their table.

*Size on `Assoc`* (an estimate; $c_{st}$ is not yet calibrated). Take a
uniform coverage, $p_{\text{cov}} \approx 543/(2.9 \times 10^6) \approx
1.9 \times 10^{-4}$ (543 entries per scan, §0.7; 2.9M keys, D-16), scans at
$q_{sc}/q = 0.035$, $|e| \approx 1{,}032$ bytes (64-byte key, 960-byte mean
value, 8 bytes of sequence and type; compression off), $\bar q = 69{,}021.5$
operations per second, and $c_s$ = \$3.04 × 10⁻¹⁷ per byte-second, which is
$1.80 \times 10^{-12}$ core-seconds per byte-second at the contract's price
per core-second. The scan part of $c^{\text{hold}}_e$ then exceeds the space part when one
crossing costs more than about 4 ns of device time in balanced mode, 0.4 ns
in read priority and 41 ns in space priority, both at $\beta^\star = 10$.
A crossing is a merging-iterator step — the child's own `Next` (decoding
the next entry in its block), the heap update (at least one internal-key
comparison), and `DBIter`'s parse, visibility check and user-key comparison — plus its share of block loads. Unless the calibration finds crossings cheaper
than about 4 ns, held garbage on `Assoc` is priced mainly through scans in
balanced and read priority. Hot keys raise
$p_{\text{cov}}$ above the uniform figure, and the measured concentration says
that the garbage sits on them. On the power-law mix $q_{sc} = 0$, so
$c^{\text{hold}}_e$ is the space price alone.

**Corollary B.6 (the garbage trade-off, per version; new 2026-10-03,
D-23).** Under the assumptions of Proposition B.1″(ii) and Proposition B.5,
with $N^{\text{tab}}_e = N^{\text{hid}}_e$ and no reseeks, write the expected priced cost
of a window as
$$J_\beta = \underbrace{-\sum_e|e|\,\mathbb E\big[\Psi_{j_e}\big] \;+\; \sum_ec^{\text{hold}}_e\,\mathbb E[N^{\text{hid}}_e]}_{\text{garbage channel}} \;+\; V\sum_ic^{\text{stage}}_i \;+\; \mathcal Q ,$$
where $j_e$ is the stage whose merge drops $e$, a random variable
($\Psi_{j_e} = 0$ for a version no merge drops in the window), $V\sum_ic^{\text{stage}}_i$ is the no-drop
compaction cost, which no policy changes inside the model, and $\mathcal Q$
collects every other term (flushes and their interference, live bytes, Gets'
read steps, seeks, the steps over returned entries, iterator blocks, the
memtable search, reopens, the fixed parts of Gets and scans, and the Puts'
inserts). Inside the model none of them depends on where or
when obsolete versions are dropped, with two exceptions that B.1″(ii)'s scope
and B.5's caveats name: the blocks that hidden bytes add to a scan, and the
read cost per operation that sets later jobs' interference. Holding $\mathcal Q$
and the other versions' terms fixed, a change that raises
$\mathbb E[\Psi_{j_e}]$ by $\Delta\Psi_e$ while $e$'s expected garbage period
$\mathbb E[N^{\text{hid}}_e]$ changes by $\Delta N^{\text{hid}}_e$ lowers
$J_\beta$ exactly when $|e|\,\Delta\Psi_e > c^{\text{hold}}_e\,\Delta N^{\text{hid}}_e$;
in particular, moving $e$'s drop for certain from stage $j$ to stage $j' < j$
gives $\Delta\Psi_e = \Psi_{j'} - \Psi_j$.

*Proof.* In the proof of B.1″(ii) the priced compaction cost is
$V\sum_ic^{\text{stage}}_i - \sum_jD_j\Psi_j$, and $D_j = \sum_{e:\,j_e = j}|e|$, so it is
$V\sum_ic^{\text{stage}}_i - \sum_e|e|\Psi_{j_e}$, realised. $V$ is the flushed bytes, which do not
depend on the policy up to Lemma D.15's flush-timing caveat, and the $c^{\text{stage}}_i$
and the $\Psi_j$ are held fixed, so the expectation is
$V\sum_ic^{\text{stage}}_i - \sum_e|e|\,\mathbb E[\Psi_{j_e}]$. B.5(c) gives the
expected space cost of obsolete versions and the hidden-step cost, with
equality under the stated conditions. What remains of $J_\beta$ is
$\mathcal Q$. The second claim is the change in $e$'s two terms.
$\blacksquare$

*What this changes.* Before D-23, holding garbage cost only space, and on
`Assoc` garbage is 3–8% of held bytes (D §5). Now each operation of
delay costs $c^{\text{hold}}_e$, which on `Assoc` is dominated by scans and is largest for
the hot keys whose repeat overwrites lever (c) aims to drop high (Pathway A §4
(c)). So lever (c) is weaker on `Assoc` than B.1″ alone suggests, and most
of all in read priority, where the inequality can reverse: compacting earlier
to cut hidden steps is then the read lever. Its steady-state part is a static
profile, which belongs to $\Theta_s$ (Corollary C.3), and depth caps it
(B.5(e)): a key cannot keep more hidden versions than the tree has runs. That
is consistent with T = 2 (populated to L8) stepping over five times as many
hidden entries as T = 10 (to L4); how much of the factor depth explains is
not measured. On the power-law mix, holding costs space only, while each drop is
worth more than its bytes ($\Psi_j \ge \beta_Wc_w$ times its byte value), so
lever (c) is stronger there than it was. Holding can also shorten a garbage
period ($\Delta N^{\text{hid}}_e < 0$): a version kept higher meets its newer version
sooner. Then it gains on both sides. The corollary is an accounting identity
inside the model; which sign dominates on a workload is measured at Gate N3
(WL-3). In the per-level rewards the hold price falls on the holding level
(B.5's last paragraph, D §4), while most of the saving $|e|\Psi_{j_e}$ falls
on the deeper levels whose merges no longer carry $e$; the neighbour charge
$X_{i+1}$ of H §3 is what brings the next level's part back to the holding
level.

**Conjecture B.3′ (state-dependent value on a stationary workload).** On a
stationary workload there is a state-dependent trigger policy $\pi$ with
$J_\beta(\pi) < \min_{\theta\in\Theta_s}J_\beta(\theta)$.

*Status.* Unproven. The 2026-09-11 revision asserted it in its commentary on
Proposition B.3 (now Proposition H.3); B.3 does not imply it. No learned or hand-written policy on record lies
below the static frontier (audit §2). The candidate mechanisms are items (b)–(f)
of Pathway A §4 (amended 2026-10-03: the 2026-09-29 text named (b)–(d), but
Gate N3, which tests this conjecture, also tests (e) and (f)). They are tested
without a learner at Gate N3; if none works, the claim narrows to suite
robustness and changing workloads (Global acceptance). The statement is
unchanged by D-23; $J_\beta$ now contains the new terms.

*What the 2026-10-03 costs do to the candidates* (D-23; the levers'
re-pricing is Pathway A §4's, as amended).

- **(b) Timing a release against the neighbours.** It gains the per-job and
  read-byte prices and the interference of each job (D §1), which a release
  moves in proportion to the bytes it merges. On a stationary closed loop the
  read cost per operation around a job varies mainly with $k_0$, and native
  RocksDB already runs the jobs sourced at L1 or deeper with L0 nearly empty
  (mean $k_0$ during the merges of each start level at most 0.13 in the
  seven native runs of §0.7, both workloads, T = 2, 6 and 10; critique Q2,
  extended per job to all 23 runs of their cells), so timing these jobs against
  reads has little left to find.
- **(c) Holding a level so its merges drop more garbage.** Each drop is worth
  more than before (B.1″(ii)), but holding now pays the read price of the
  garbage it keeps where scans run (Proposition B.5, Corollary B.6). On
  `Assoc` the lever is weaker, most of all in read priority, where it can
  reverse; on the power-law mix it is stronger.
- **(d) Preventing depth growth.** Unchanged in kind; each populated level
  now also allows one more hidden version per key (B.5(e)) and adds a child
  to the heap of every scan whose range it covers (D §1), so a level avoided
  is worth more on `Assoc`.
- **(e) L0 compacting early while the slot is idle.** Each early L0→L1 merge
  now also costs its job price ($c^0_{job}$; the measured per-job gap
  beyond `compaction_time_micros` is 4.5–5.3 ms for jobs sourced at L0,
  §0.7), its read bytes and its interference, the read part weighted by
  $\beta_R$. The lever is devalued and can reverse in read priority
  (Pathway A §4 (e), amended); an early merge does run at a lower $k_0$ than
  one at the trigger, a small offset.
- **(f) Interior levels yielding the slot to L0.** Unchanged in kind, but
  rare at the default point: in the same native runs no job sourced at L1
  or deeper ran while L0 held two or more files (critique Q2, P8; §0.7), so
  L0 was never due while such a job held the slot, and slot blocking had no
  occasion.

**No "timing compactions to light-read moments" mechanism (2026-10-03,
D-23).** It was proposed to add, as a stationary candidate, timing
compactions to moments of light read load, which a static configuration
cannot copy, in the manner of SILK [SILK]. It is not added, for three reasons.

1. *A closed loop has no light moments.* Under A8 one client thread issues
   each operation as soon as the last one returns, so it is never idle except
   while a write stalls. A job's interference charge is its bytes and priced
   device time at the reference rate, priced at the mean quiet read cost per
   operation over its window (D §1). On a stationary mix a policy cannot pick
   cheap windows, since it does not see the coming operations' types; and a
   job run inside a stall, when no operation is served at all
   ($\Delta n_\iota = 0$), is charged at the read cost of the operations
   before it (Lemma D.17(v)). So not even a stall is a light moment for the
   charge. (As first drafted, the charge was zero for such a job, and the
   stall rule alone barred claims that exploited it.)
2. *On a stationary mix, read cost per operation moves only with the tree.*
   `mixgraph` draws each operation's type, key and scan length independently,
   so the expected read cost per operation changes only through the tree's
   state: mainly $k_0$, and the hidden versions in the ranges scanned.
3. *Native already does the timing that remains.* Native RocksDB runs
   47–63% of compaction bytes, those of the merges sourced deeper, with L0
   empty or nearly so on average (61% on `Assoc` at T = 10; critique Q2;
   §0.7). The rest are L0→L1 merges, which run with L0 full by
   definition, at a time set by $K_0$, a static knob (Proposition D.11,
   amended).

SILK monitors the I/O bandwidth that client operations use, gives the
leftover to flushes and compactions, and exploits transient low-load periods
of a client load issued in an open loop, to cut tail latency [SILK]. Under A8
that opportunistic bandwidth allocation has no counterpart. SILK's other two
techniques, priority for flushes and L0→L1 compactions and their preemption
of deeper compactions, need no load dips and would act under a closed loop
too; they schedule work inside the store rather than set targets and
triggers, so they are not candidates for this controller. On an open-loop workload, or a phased one whose phases
differ in read intensity, timing is real: the phased case belongs to
$\mathcal G$ and claim 3 (below; Global acceptance), and the open-loop case,
with its latency objective, to Programme 2 (Pathway F). So the stationary
candidates stay (b)–(f), re-priced.

**Definition (phase-adaptivity gap), retained.**
$$\mathcal G = \min_{\theta\in\Theta_s}J_\beta(\theta) - \sum_p\frac{n_p}{N}\min_{\theta\in\Theta_s}J_{\beta,p}(\theta),$$
the excess cost of the best single static configuration over a hindsight oracle
that switches static configuration per phase, where $n_p/N$ is phase $p$'s share
of the measured operations; time shares would depend on each configuration's
speed. (2026-10-03, D-23: $J_\beta$ and $J_{\beta,p}$ carry the amended
terms. Operation shares remain the right weights, since $J_\beta$ is a
function of the operation-indexed record, Lemma D.17.)

**Corollary B.4 (retained).** $\mathcal G > 0$ is sufficient evidence that
adaptivity has value on that workload. $\mathcal G = 0$ is not evidence that it
has none, because $\mathcal G$ sees only the phase component of adaptivity.
Corollary D.12 now predicts $\mathcal G > 0$ in advance for the L0 trigger
whenever phases' best triggers differ.

*Interference adds a source of phase adaptivity* (added 2026-10-03, D-23;
it depends on the amended Proposition D.11 and Corollary D.12). A job's
interference charge grows with the quiet read cost per operation over its
window (D §1), so a phase with heavy reads raises the price of compacting in
it and a phase with light reads lowers it.

- *Inside $\mathcal G$.* In the amended Proposition D.11 the L0→L1 merges,
  whose number $K_0$ sets, pay interference at the read cost of the reads
  around them (at $k_0 = K_0$, the measured correlation, D §3), so the write side of the
  $K_0$ trade carries the phase's read cost per operation, not only its write
  rate. The best trigger then depends on the read cost per operation as well
  as on the ratio of writes to reads: two phases with the same ratio but
  different read costs per operation (longer scans, Gets that reach deeper)
  can have different best triggers, which the 2026-09-29 Proposition D.11 did
  not allow. The interior profile can move the same way, wherever the
  per-level interference charges are not proportional to the write prices
  (Theorem A.2, amended).
- *Outside $\mathcal G$.* A policy can also move compaction out of a
  read-heavy phase into a read-light one that follows, where it charges less.
  $\mathcal G$ compares static configurations phase by phase, so it does not
  contain a gain from carrying work across a phase boundary. That is one more
  reason why $\mathcal G = 0$ is not evidence of no value.

*Corollary D.12's argument extends.* It needs only that no admissible trigger
be best in every phase. If the phases' sets of best admissible triggers have
no common element, every fixed trigger lies outside some phase's set, so it
costs strictly more in that phase and no less in any other, and the trigger
switched per phase (each phase at its best, switches not counted) costs
strictly less. Strict convexity served only to find each phase's best
admissible trigger (one of the two integers next to the real minimiser). So
the conclusion holds for the amended cost whenever the amended Proposition
D.11 identifies each phase's best admissible trigger, by strict convexity, by
unimodality, or by evaluating the admissible integers directly. The prediction
"$\mathcal G > 0$ for the L0 trigger" then needs the phases' best admissible
triggers under the amended Proposition D.11 to have no common element, a
statement about Pathway D §3 that this pathway records as a dependency.

### §4 Acceptance

| # | Criterion | Threshold | Instrument |
| --- | --- | --- | --- |
| B-1 | Skew raises resident garbage (retained) | garbage fraction under skew exceeds a skew-free control by ≥ 10 pp, on the measured denominator | evaluator |
| B-3 | Survival is measurable and responds (retained) | $\rho_i$ and $\eta_i$ per level, trivial moves excluded; lower under holding than under native at the held level | compaction log |
| B-4 | Shift produces a phase-adaptivity gap (retained) | $\mathcal G > 0$ with the 95% interval excluding 0 | hindsight-oracle runner |
| B-5 | Deletes activate compensated size (retained) | non-zero tombstone-driven file selections in the RocksDB LOG | LOG parse |
| WL-1 | Garbage is reported where it acts (replaces B-2) | per-level $\rho_i$ and dropped bytes per workload and mode | compaction log |
| WL-2 | The suite exercises every mode | for each mode, at least one workload whose best static setting differs from the balanced-mode best | Gate N2 |
| WL-3 | Garbage's read price is reported where it acts (added 2026-10-03, D-23) | per workload, mode and arm: hidden entries stepped over and iterator blocks loaded per scan, per level where the entry or block lives and, for hidden entries, also per level charged (the level directly above, Pathway D §4), and their priced cost, beside WL-1's dropped bytes; at Gate N3, the hold-for-garbage rule's paired change in these beside its paired change in priced compaction cost (Corollary B.6); zero on a workload without scans | per-level counters (OBJ-9), evaluator |

B-2 is retired: its threshold used Theorem B.1's ceiling, which its proof does
not establish.

*Re-checked 2026-10-03 (D-23).* B-1, B-3, B-5 and WL-1 count bytes, garbage
and LOG events, so the new terms do not touch them. B-4 is unchanged in form;
its $\mathcal G$ is computed with the amended $J_\beta$. WL-2 is harder to pass
than before: interference makes compaction dearer in read priority as well,
so read priority's best static setting moves toward the balanced one (the
amended Proposition D.11's trigger has a floor, positive when $A_I > 0$, as $\beta_R/\beta_W$
grows; critique Q2, Q8).

---

## Pathway C — Comparator

### §0 Purpose

Compare the controller with the best static setting for the same priced cost,
measured on the same workload and binary, in the same session. The claim is then
about a class of static settings rather than one tuned point.

**The binary (2026-10-03, D-23).** The amended cost needs counts that only a
new binary records: each job's cumulative foreground-step counters, per-level hidden
steps and iterator blocks, the timer that checks interference, and the scan
set-up timer that measures $c^0_{sc}$ (Gate N0 item 10; OBJ-8, OBJ-9). The hull is bound to its binary (the evaluator refuses
to pool across binary identities), so $\Theta_s$ is measured on that binary,
with the D-23 prices fixed before any of its outcomes is seen (Execution
order). No $\Theta_s$ arm has run as of 2026-10-03, so no comparator
measurement is discarded when the binary changes.

### §1 The static class and the comparator

**$\Theta_s$ (fixed in advance, §0.6):** a configuration of $\Theta_s$ is
a triple $(K_0, \text{base}, \text{profile})$; a (workload, $T$) pair is a
cell; repeats are seed-paired within a cell; and $\theta^\star_\beta$ is the
configuration with the lowest *mean* $J_\beta$ over a cell's repeats.

- L0 trigger $\in \{2, 4, 8\}$, restricted to admissible values
  (A-Impl-6), so that triggers the byte branch makes identical are not counted
  twice;
- base size $\in \{8, 16, 32\}$ MiB;
- static multiplier profiles: uniform 1; the equal-fanout or survival-weighted
  profile at the measured $c$ and $v_i = a_i - t_i$ (Theorem A.2); a last-level-emptying
  profile that holds the level just above the deepest populated level at 2
  times from load, every other level at 1 (audit §3; D-13 §4 fixed this
  form, which the 2026-09-29 text gave as "upper levels at 1.5–2 times");
  uniform 0.75;
- `compaction_pri = kMinOverlappingRatio`, pinned;
- $T \in \{2, 6, 10\}$, with each mode's $\theta^\star_\beta$ at $T = 10$
  re-run at 14 and 20 for the cross-$T$ check (D-13 §4).

**Comparator.** For each workload $w$ and mode $\beta$,
$\theta^\star_\beta(w) = \arg\min_{\theta\in\Theta_s}J_\beta(\theta, w)$, from
measured, seed-paired runs.

**The survival-weighted profile under the amended cost (2026-10-03, D-23).**
$\Theta_s$'s survival-weighted profile is Theorem A.2(ii)'s optimum for bytes
written, with weights $v_i$ (D-14 §3). Under the amended cost a merged byte
also pays for being read and for its interference, and each merge pays its
job price (the $c^{\text{stage}}_i$ of Proposition B.1″(ii)), so the profile
that minimises the priced cost weights level $i$ by $v_i$ times the priced
cost of one overlap byte merged there (Theorem A.2(iv), amended). That price
differs between levels only through the interference per overlap byte: $c_w$
and $c_{cr}$ are the same at every level, and a level's job count follows its
source bytes, not its fanout, so the job price does not move the optimum. So
the priced optimum is the byte optimum when the interference per overlap byte
is the same at every level (and the overlap constants $c_i$ are equal, as the
byte optimum already assumes); otherwise it differs from it and depends on
the mode, since the interference carries $\beta_R$ and the byte prices
$\beta_W$.
Measured, it is not the same: bytes merged out of L0 run at $k_0 = K_0$, and
derived at provisional prices they should pay 1.24–1.94 times what deeper
bytes pay (§0.7, critique Q2). The profile in
$\Theta_s$ stays the byte optimum, fixed in advance; it is a member of the
class whether or not it is the priced optimum, so every result of §2 holds
with it. Decided 2026-10-04 (D-24, §0.7 item 7): at the formal Gate N2,
$\Theta_s$ also gains the priced profile of Theorem A.2(iv) for the balanced
mode only.

### §2 Theory

**Proposition C.1 (comparator validity; retained, restated for priced cost).** If
some $\theta \in \Theta_s$ has priced cost at most $\pi$'s on every term, then a
static configuration is at least as good as $\pi$ in every mode, and $\pi$'s
contribution is nil whatever it achieves against any single setting. (The
terms are $\mathcal C_W$, $\mathcal C_R$ and $\mathcal C_S$; since 2026-10-03
they contain D-23's terms, and the proof is unchanged.)

*Proof.* Immediate from dominance, since every $J_\beta$ has positive weights.
$\blacksquare$

**Corollary C.2 (retained).** "$\pi$ beats the native setting" is strictly weaker
than "$\pi$ beats $\theta^\star_\beta$".

**Corollary C.3 (steady-state gains belong to the static class; extended;
amended 2026-10-03, D-23).** The steady-state optima of Proposition D.11 ($K_0$
at fixed prices), Proposition D.13 (interior profile, depth) and Theorem A.2
(survival-weighted profile), each in its amended form, and the steady-state
part of the garbage trade-off (Corollary B.6: the depth and fanouts that set
how many hidden versions a key keeps, B.5(e)) are static settings. Any gain
they explain belongs to $\Theta_s$, not to the controller.

*Conditions, made explicit.* Each of these optima minimises a steady-state
cost whose inputs are the configuration, the workload's stationary rates and
mix, and the prices. They are static because the last two are constant: the
workload by stationarity, the prices by A9 (and, for interference, a fixed
$\kappa$ and $\bar q$). Interference does not add a time-varying input under
these conditions: its charge per job depends on the read cost per operation
during the job, which in steady state is a property of the configuration and
the mix (for L0→L1 merges, at $k_0 = K_0$). If a price or the mix moved during
a run, the optimum would move with it, and tracking it would be the
controller's (Pathway A §4 (a); Corollary D.12).

**Proposition C.4 (one hull serves every mode; amended 2026-10-03, D-23).**
For every $\beta > 0$, $\theta^\star_\beta$ lies on the lower boundary of the
convex hull of the static points' priced-cost vectors
$(\mathcal C_W, \mathcal C_R, \mathcal C_S)$, and some minimiser is a vertex
of it. The hull extracted once per workload therefore contains a comparator
for every mode. This holds with D-23's terms: the job, compaction-read and
trivial-move charges, the Puts' inserts and the write part of interference
inside $\mathcal C_W$, and the iteration, memtable, fixed-part and read-part
interference charges inside $\mathcal C_R$ (the split by part
changes neither vector's dependence on the run nor its independence of
$\beta$).

*Proof.* Three steps.
(i) $J_\beta = \beta_W\mathcal C_W + \beta_R\mathcal C_R + \beta_S\mathcal C_S$
is linear in the vector (D §2), whatever $\mathcal C_W$ and $\mathcal C_R$
contain: D-23 adds its terms inside them, and adds no term outside the three.
(ii) A static point's vector does not depend on $\beta$. A static arm runs no
controller, so $\beta$ enters only its scoring; every price, $\kappa$ and
$\bar q$ is fixed before any comparison (A9, OBJ-2, OBJ-5); and the
interference charge of each job is computed from that run's record with
those constants (D §1, A10). So $\Theta_s$ gives one finite set of vectors
for every mode.
(iii) Proposition D.5 on that set: a linear function with positive weights
attains its minimum over the hull on a face of the lower boundary, every
vertex of which is a point of $\Theta_s$, so a vertex minimiser exists, and
every minimiser in $\Theta_s$ lies on that face. $\blacksquare$

*Ties* (the 2026-09-29 text said "is a vertex"). With exact ties a minimiser
can lie on a face of the hull without being a vertex; a vertex minimiser
always exists. With continuous measured costs ties are immaterial.

*What A9 does here.* Steps (i) and (iii) hold for any finite set of
well-defined vectors, even if each point carried its own prices; step (ii)
uses only that the prices are fixed before the comparison. A9 is needed
elsewhere. A controller changes its configuration during a run, so its
$J_\beta$ needs prices that do not depend on the configuration (A9), or a rule
that prices each job and step at the configuration of its moment. With A9 a
paired difference $J_\beta(\pi) - J_\beta(\theta)$ is a difference of counts
at common prices (§3). A per-job price that depended on the configuration
beyond the job kind (D-24, §0.7 item 5: added only if OBJ-7 fails at
T = 2) would keep C.4 but need that rule.

**Definition C.5 (regret and suite robustness).** The regret of $\pi$ on
workload $w$ in mode $\beta$ is
$$\text{Reg}_\beta(\pi, w) = \frac{J_\beta(\pi, w)}{J_\beta(\theta^\star_\beta(w), w)} - 1.$$
$\pi$ is *suite-robust* in mode $\beta$ if
$$\max_w \text{Reg}_\beta(\pi, w) < \min_{\theta\in\Theta_s}\max_w\text{Reg}_\beta(\theta, w),$$
i.e. its worst regret across the suite is below the best worst-case regret of
any single static setting. This is where a min–max criterion properly belongs.
It is a regret analogue of Endure's min–max robust tuning under workload
uncertainty, which minimises the worst-case expected cost over a
neighbourhood of an expected workload rather than a regret over a finite
suite [Endure].

A controller can be suite-robust without beating $\theta^\star_\beta(w)$ on any
single workload: it only has to avoid being far from the best static setting
everywhere, which no one static setting may manage.

The regret is a ratio, so it needs $J_\beta > 0$; D-23's terms are
non-negative prices times counts, so this still holds ($c_s > 0$ alone makes
$J_\beta$ positive). Where D-23 prices a quantity per workload (A9), the regret
compares costs within one workload only, as it always did. One effect is new:
a cost that is the same in every arm of a workload, such as the memtable
searches, the scan-base bucket and the fixed bucket (the fixed parts of Gets
and scans and the Puts' inserts; D §1, §4), adds a constant $K_w$ to both
$J_\beta$'s of the ratio, so it multiplies every regret on workload $w$ by
$J_\beta(\theta^\star_\beta(w), w)/(J_\beta(\theta^\star_\beta(w), w) + K_w) < 1$.
The factor differs between workloads (the power-law mix has no scans), so it
can change which workload sets the worst regret, and with it CMP-7's verdict.
Differences of $J_\beta$ (CMP-3) are unaffected (D §2). Whether the regret
is computed on $J_\beta$ or on $J_\beta$ less those buckets was decided
2026-10-04 (D-24, §0.7 item 12): on $J_\beta$ less the policy-independent
buckets (memtable, scan-base, fixed), with the full $J_\beta$ reported
beside it.

**Proposition C.6 (what a static point's priced cost depends on; new
2026-10-03, D-23).** Fix the binary, the prices (A9), $\kappa$, $\bar q$ and
a workload. Call a run's *record* its operation-indexed record (Lemma D.17):
the operation sequence; every version install (with the bytes $H$ it leaves),
flush, and job begin and end (with each job's kind, start level and bytes),
stamped with the number of operations served before it; and the read steps of
every operation. Then:

- (i) the run's vector $(\mathcal C_W, \mathcal C_R, \mathcal C_S)$ is a
  function of its record;
- (ii) for a static configuration $\theta$, the record is set by $\theta$,
  the operation sequence (the seed) and the *interleaving*: which operations
  are served between each pair of consecutive background events. The
  interleaving is set by the relative speeds of the client thread and the
  background threads, not by $\theta$ alone;
- (iii) a change of wall-clock timing that keeps every event's operation
  index leaves the vector unchanged; a change that moves events relative to
  the operations, such as the client running faster or slower relative to
  the jobs, changes it in general, through the version each operation reads
  and, with D-23, through the operations each job's window holds. The byte
  and the busy part of a job's charge depend on them only through the
  window's mean cost per operation of each step type, its read steps and its
  Puts (Lemma D.17(v)); neither counts how many operations the job
  overlapped.
  (As first drafted, the busy part counted them, and so moved with that
  relative speed.)

So $J_\beta(\theta)$ is an expectation over seeds and interleavings, and an
estimate of it is bound to the binary (counts, instruments), to the machine
(prices, A9) and, through the interleaving's distribution, to the session.
Interference adds no dependence on wall-clock time given the record; it adds
one more channel through which the interleaving enters.

*Proof.* (i) $\mathcal C_W$ prices each job at completion by its kind and
bytes, each flush by its bytes, and each Put's insert (D §1): events of the
record, at fixed prices. $\mathcal C_R$ prices each operation's read steps
and fixed part (in the record) at fixed prices. Each job's interference
charge, its read part in $\mathcal C_R$ and its write part in
$\mathcal C_W$,
$I_\iota = \bar q\sum_x\bar\varrho^{\,x}_\iota(\kappa^B_xY_\iota + \kappa^J_{x,\mathrm{kind}(\iota)}t^{job}_\iota)$
uses its kind, its bytes, its stamps $n^b_\iota$, $n^e_\iota$ and the
foreground steps of the operations in its window: all in the record, at fixed
prices, $\kappa$, $\bar q$, $n^{\mathrm{win}}$ and $n^{\mathrm{str}}$ (Lemma
D.17). $\mathcal C_S =
(c_s/\bar q)\sum_nH(n)$, and $H(n)$ is set by the last install before
operation $n$. (ii) The operation sequence is fixed by the seed (A8).
RocksDB's choices of what to flush and compact are functions of the tree and
the options, and the tree changes only at installs; so given $\theta$ and the
seed, the record is fixed once the order of the background events relative to
the operations is fixed, and that order is a matter of timing. D-22 saw it:
with the data fixed by the seed, three loads of one tree gave 4.52, 4.73 and
4.58 filter probes per missing Get, because flushes and compactions run on
background threads. (iii) A change of timing that keeps every event's
operation index keeps the record, so by (i) the vector. In $I_\iota$ the
byte and the busy part multiply a quantity fixed by the kind and the bytes
($\bar qY_\iota$, $\bar q\,t^{job}_\iota$) by the window's quiet cost per
operation of each foreground step type, read steps and Put inserts alike
($\bar\varrho^{\,x}_\iota$, weighted by its $\kappa$), which is the only
place the interleaving enters (Lemma D.17(v)). $\blacksquare$

*Consequences.* Same-session twins (CMP-8) control the session's share of the
interleaving, as they already controlled its effect on the old terms (which
version each Get reads, $k_0$ at each probe). The physical interference, which
Lemma D.17 converts away, is session-dependent and is only reported (OBJ-6).

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
- **Drift through the interleaving (added 2026-10-03, D-23).** By
  Proposition C.6 a session moves $J_\beta$ through the order of background
  events relative to operations, and since D-23 also through the operations
  each job's window holds: if jobs run slower relative to the client in one
  session, their windows hold more operations, at whatever read cost those
  have; the charge does not grow with their number (Lemma D.17(v)).
  Same-session, interleaved twins control this, since both arms
  of a pair share the session's speeds, as they control the write drift
  above. The per-run checks of D §6 (OBJ-7, OBJ-8) flag a run whose job
  times or loaded read times leave the model.
- **Prices are constants, not draws (added 2026-10-03, D-23).** Under A9 a
  price error is the same in both arms of a pair, so it adds no variance to
  $d$; it biases $d$. Re-price the same runs with every price $c_x$ (the
  $\beta$-weights folded in) replaced by $c_x + \epsilon_x$ and $\kappa$
  unchanged. Write $\Delta n_x = \partial d/\partial c_x$, the arms'
  difference in what $c_x$ multiplies (for a quiet foreground price, read or
  Put insert, its count plus its weight in the interference charges; for a job price, its count plus its
  weight in the busy part). Then $d$ moves by $\sum_x\epsilon_x\Delta n_x$ plus
  products of errors, and to first order its sign is safe when
  $|\sum_x\epsilon_x\Delta n_x| < |d|$. Errors in a quiet read price alone, or
  in a job price alone, move $d$ exactly linearly. An error in $\kappa$ moves
  $d$ linearly too, through the interference charges. *Proof:* re-pricing
  leaves a run's counts unchanged, and at fixed counts $J_\beta$ is linear in
  each price separately: each term of the interference charge is a product of
  a $\kappa$, a quiet read price and, in the busy part, the priced device time
  $t^{job}_\iota$, which is linear in the job prices (D §1). So $d$ is a
  polynomial in the errors whose first-order part is
  $\sum_x\epsilon_x\Delta n_x$ and whose other terms are products of errors in
  a read price, a job price and $\kappa$. $\blacksquare$ D-22's sensitivity reports (its §2(h)) evaluate this
  at the ends of the pooled price ranges; the D-23 prices, $\kappa$ included,
  need the same reports, since they are less certain than $c_f$ or $c_w$.
- **Effect size against resolution.** Gate N2's resolution is its own hull
  spacing ($h$ above, C-2), so whether it resolves the new terms is known only
  after Gate N2. A pre-run estimate, on Gate N1's pilots at provisional prices
  and mean field (critique Q8): two configurations whose compaction traffic
  differs by about 30 MB/s differ in interference by about 3% of
  $\mathcal C_R$, about 1.9% of $J_\beta$ in balanced mode and 2.8% in read
  priority at $\beta^\star = 10$. That is the size of D-22's 3% price
  reproducibility, but a price error biases a pair (above) rather than adding
  to $\sigma_d$, so the two are not on one scale.

### §4 Acceptance

| # | Criterion | Threshold | Instrument |
| --- | --- | --- | --- |
| C-1 | Static class sampled (retained, extended) | every knob level of $\Theta_s$ present; ≥ 4 hull vertices per (workload, $T$) | sweep output |
| C-2 | Hull points decidable (retained) | repeats until the interval width is under half the spacing between neighbouring hull points | paired evaluator |
| CMP-3 | Per-workload comparison | $J_\beta(\pi) - J_\beta(\theta^\star_\beta)$ with paired interval, per (workload, mode, $T$) | paired evaluator |
| C-6 | Cross-$T$ (retained) | comparison repeated with $\theta^\star_\beta$ taken over $T \in \{2, 6, 10, 14, 20\}$ | cross-$T$ sweep |
| CMP-7 | Suite robustness | Definition C.5 reported per mode | suite evaluator |
| CMP-8 | Same-session twins | every comparison paired within one session, session id recorded | run manifest |
| CMP-9 | One binary, one set of prices (added 2026-10-03, D-23) | every arm of a comparison, and every $\Theta_s$ point of its hull, ran the same binary identity (`db_bench` with the library it loads) carrying the D-23 counters and timers (Gate N0 item 10, the scan set-up timer included), and is scored with the same prices file, $\kappa$ and $\bar q$, every new price and $\kappa$ in it calibrated on that binary identity and the existing prices re-checked on it (Gate N0 item 11, D-22 §2(g)); each arm's per-run price checks (D-21's $c_{open}$ check, OBJ-7, OBJ-8) are reported with it, a failed $c_{open}$ check leaving the arm unpriced as D-21 rules, and a failed OBJ-7 or OBJ-8 check having the consequence set by a dated entry before Gate N2 (D-24, §0.7 item 4) (report-only during the exploratory track) | fingerprint, prices file, evaluator |

The 2026-09-11 criteria C-3, C-4 and C-5 are historical (history §18.1).

*Re-checked 2026-10-03 (D-23).* C-1, C-2, CMP-3, CMP-7 and CMP-8 are
unchanged in form and use the amended $J_\beta$ (Proposition C.4). C-6
compares $T$ values at one price scale and one $\bar q$ per workload (D-13
§1). Under Lemma D.17 a job's interference is charged at $\bar q$, not at the
run's own speed, so a configuration that runs slower than $\bar q$ is charged
more interference than it physically causes: by the factor $\bar q/q_\iota$
for job $\iota$'s byte part, and by $(\bar q/q_\iota)(t^{job}_\iota/\Delta t_\iota)$
for its busy part (Lemma D.17(iii)). On Gate N1's pilots T = 2 took 1.78
(`Assoc`) and 1.85 (power law) times the wall time of T = 10 (means of three
repeats; critique Q1), and $\bar q$ is T = 10's native rate, so at T = 2 the
converted byte part is about $\bar q/q$ = 1.77 (`Assoc`) and 1.87 (power
law) times the physical one (means of three repeats; exactly so if
operations are served during jobs at the run's mean rate;
`db/n1-assoc`, `db/n1-powerlaw`; the two ratios are different quantities). That is the
convention's purpose (a run is not charged less for being slow, D §1), the
same choice that D-13 made for space across $T$. OBJ-6 reports the physical
interference beside it. And the scan-iteration terms now price what the
2026-09-29 cost left out of T = 2's slowness on `Assoc` (about 200 s of
configuration-dependent client time, §0.7), as far as the calibrated prices
capture it, so C-6's comparison omits less than it did.

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

Verdicts to date are in history §18.1.

---

## Pathway F — Phase-aware objective switching (Programme 2)

**Status:** second programme. Its 2026-09-11 description — a layer above the
controller that switches a parameter set per detected or forecast phase; the
F-detect and F-forecast versions; the leak guard; switchable knobs only — is
retained. Four changes (the fourth added 2026-10-03):

- Under Programme 1, what switches per phase is the priority vector $\beta$ (and
  $K_0$ at its phase optimum, Corollary D.12), not an SLO manifest. SLO manifests
  return with Programme 2.
- Corollary D.12 predicts $\mathcal G > 0$ for the L0 trigger in advance whenever
  the phases' best triggers differ.
- The audit's suggestion of a slow tuner above native RocksDB is this layer.
- **What Programme 2 still owns, now that interference is priced (D-23).**
  Programme 1 prices the slowdown that background jobs impose on priced
  foreground steps, each job at the reference rate and at the cost of the
  operations around it, so $J_\beta$ still contains no time (Lemma D.17). What stays here is the
  wall-clock side: stall time, throughput and latency, the physical
  interference (reported in Programme 1, OBJ-6), and scheduling background
  work into the low-load periods of an open-loop client, which is SILK's
  opportunistic bandwidth allocation for tail latency [SILK] (one of its
  three techniques; the other two need no load dips) and has no counterpart
  under A8's closed
  loop (Conjecture B.3′'s discussion). Phase-aware switching gains a reason of
  its own: a phase's read cost per operation sets the price of compacting in
  it, so phases that differ in read cost can differ in their best settings
  even at the same ratio of writes to reads (Pathway B §3, after Corollary
  B.4).

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
   then. (That repair is in the recorded commit `8e903efd9`, which flags a
   move from the job's stats and counts its relinked bytes.)
4. The settle step after the load (`WaitForCompact`, then the $h_w$ hold) and
   the measured-phase stamp at the first `mixgraph` operation (A8, H §5).
5. Evaluator: $\mathcal C_W$, $\mathcal C_R$, $\mathcal C_S$ and $J_\beta$
   over the measured phase, from operation $n_w$ to the end of the drain — a
   ticker snapshot and an internal-stats stall snapshot at $n_w$, $R_{blk}$ from
   `rocksdb.bloom.filter.full.positive`, compactions windowed by completion
   time, and $H$ sampled with the operation count by the listeners that run
   just after every flush and compaction install, on every arm, native
   included (exact up to the operations completed in that lag, D §1). Self-check on every arm: the per-interval
   operation counts sum to the measured operations, and the last sampled $H$
   equals `rocksdb.live-sst-files-size` at the end of the drain. And the OBJ-6
   diagnostics.
6. The C++ inference plugin, weight push, masked target and fallback (H §4,
   H §6).
7. Device price calibration: $c_w$, $c_f$, $c_{blk}$, $c_{sk}$, $c_{open}$, $c_s$
   (OBJ-2; $c_{open}$ and the all-open read prices by D-20, the reopen
   timer's reference and every run's check by D-21). The 2026-10-03 prices
   are item 11.
8. The dated `PREREGISTRATION.md` entries of §0.6.
9. Test suites for every item above, and the preflight (CLAUDE.md "Tests";
   plan §6). Every later gate that needs a long node run starts only with a
   preflight marker matching the current `db_bench`, plugin and code hashes.
10. **(Added 2026-10-03, D-23.) The instruments of the amended cost, and a new
    binary.** Fork changes:
    - *job-boundary step counters:* the host log's `job_begin` and `job_end`
      records carry the cumulative foreground-step counters of every priced
      type (read steps by type, and the counts of Gets, scans and Puts, which
      the fixed parts and the write part of interference need),
      with the operation stamps $n^b_\iota$ and $n^e_\iota$ (D §1);
    - *flush begin and end records:* every flush gets a begin record and an
      end record with its operation stamps and the same counters, so that a
      flush's interference (the write-path bucket) is computed like a
      compaction's;
    - *counter snapshots:* the same cumulative counters with their operation
      stamp every $n^{\mathrm{str}}$ operations, a stride fixed by a dated
      entry (D-23 or later), so
      that every
      window $W_\iota$, including one that starts before its job's begin
      record, and with it every interference charge, is computed from the
      log alone, equal to the operation-indexed record's charge up to one
      operation's steps at each end of the window, unless a dated entry has
      the counters read as of the last completed operation (D §1);
    - *per-level hidden-step counters*, keyed by the level where the hidden
      entry lives (the evaluator maps them to the charged level, D §4), and
      *per-level iterator-block counters*, aggregated across threads as item
      3's are (OBJ-9);
    - *heap child counts:* each iteration step counted by its numbers of L0
      and other children in the merging iterator's heap, by step type
      (returned, or hidden with its entry's level), with the levels of the
      other children of returned steps, as D §4's split needs (OBJ-9);
    - *the interference timer:* a timer that splits SST-read time, at least
      D-21's reopen timer, by whether at least one background job (flushes
      included) is running, with the matching counts, stratified by step type
      and level (and $k_0$ where the strata need it; the strata are fixed by
      a dated entry, D-23 or later), for the per-run interference
      check (OBJ-8(b));
    - *the scan set-up timer:* an in-store timer of each scan's set-up, the
      iterator's creation and the fixed part of its first `Seek`, excluding
      the run seeks that $c_{sk}$ prices, with its count, from which item 11
      measures $c^0_{sc}$ (D §1).

    These make a new binary. On it: the fork's tier-2 tests for the new
    counters and timers (item 9), a new preflight marker, and ACT-4 and ARCH-5
    again, since the hull and every price are bound to their binary (Pathway
    C §0). The evaluator (item 5) gains $\mathcal C_W$'s read bytes and jobs by
    kind (trivial moves included, Lemma D.18), its Put inserts and the write
    part of interference; $\mathcal C_R$'s iteration, memtable, fixed-part
    terms and the read part of interference; the per-run checks (OBJ-7,
    OBJ-8) and the physical interference (OBJ-6); the plugin's attribution log gains the same
    terms (OBJ-1).
11. **(Added 2026-10-03, D-23.) The calibrations of the amended cost, before
    Gate N2.** On the item-10 binary, each fixed before any $\Theta_s$
    outcome is seen:
    - $\kappa$ (A10): a writer-rate sweep (linearity) spanning the compaction
      byte rates of every configuration compared, T = 2 as well as T = 10,
      a job-size sweep at a
      fixed byte rate (the basis: per byte, per job or per busy second), and
      one running job against two (additivity), so that the basis, $\lambda$
      and the coefficients $\kappa^J$, $\kappa^B$ are identified and A10 is
      tested, $\lambda$'s being one value for every step type included;
    - the scan-step prices $c_{st}(r)$ and $c_{ib}$: price trees read with
      `seek_nexts` $> 0$, at several depths and garbage levels, and at more
      than one data-block size so that $c_{st}$ and $c_{ib}$ are separately
      identified (stage 18 measures $c_{sk}$ with `seek_nexts` 0);
    - the job prices $c^F_{job}$, $c^0_{job}$, $c^d_{job}$, $c^{tm}_{job}$
      and $c_{cr}$, with $c_w$ re-fitted with them: from the host log's job
      spans and flush records on the reference arms (D-14 §2's five native
      arms at $T = 10$ per workload), run on the new binary;
    - $c_{mt}$, the memtable search price; the fixed per-operation prices
      $c^0_{get}$ and $c^0_{sc}$, as §1.1 and D §1 define them ($c^0_{sc}$
      from item 10's scan set-up timer; for $c^0_{get}$, whatever a
      measurement holds beyond the definition, such as a memtable search or
      the client's own work, is removed or shown negligible) and the Put
      insert's $c_{put}$, with the interference coefficients of the Put
      insert and the fixed parts;
    - the window length $n^{\mathrm{win}}$, from the reference arms' merge
      spans in operations, below the span of the merges Proposition D.11's
      (d2) relies on (D §1), and the snapshot stride.

    The foreground prices among them ($c_{st}(r)$, $c_{ib}$, $c_{mt}$,
    $c^0_{get}$, $c^0_{sc}$, $c_{put}$ and $\kappa$) are measured inside
    D-22's price sessions: two sessions on one
    binary with a reboot between them, on archived trees (new ones where a
    garbage level is needed, since D-22's trees are loaded without
    overwrites), each price reported with its session-to-session difference;
    D-24 (§0.7 item 10) applies D-22's 3% test to the new base prices,
    and requires for $\kappa$ that the two sessions' values agree within
    their combined confidence interval and that their predicted slowdowns
    at the reference traffic agree within 1 percentage point. The job prices come from
    the reference arms, as $c_w$ does (D-22 §4). The existing prices are
    re-checked on the new binary by D-22 §2(g). D-23 states the procedures'
    rules; their values are fixed by D-23 or a later dated entry before any
    run they govern; the
    per-run checks' tolerances and what a failure means are set by a dated
    entry before Gate N2, report-only during the exploratory track (D-24,
    §0.7 item 4); $\bar q$ is not re-measured on the new binary (D-24, §0.7
    item 6).

**Gate N1 — admission test on pilot native runs (node time: about 18 runs).**
Amended 2026-10-01 (PREREGISTRATION D-16): the 2026-09-11 programme's artifacts
are out of scope (owner, 2026-09-30) and carry no host log, so Gate N1 runs its
own pilot `native` arms at the default point, three per (workload, $T$). PROP-1
for the levels the pilots decide. It fixes the run length: the measured phase
must contain at least $n_{\text{turn}}$ turnovers of the deepest pooled level,
with $n_{\text{turn}}$ = 10 (D-16). A cell with no pool applies the same rule
to its deepest interior level with enough turnovers, L2. The load stays fixed
and only `mixgraph` grows, so the tree the pools were decided on is the tree
every later run settles. Amended 2026-10-02 (PREREGISTRATION D-19): a
candidate the pilots leave undecided is not pooled, does not set the run
length, and is not decided later on Gate N2.
**Status (D-19):** run on 2026-10-01. No cell has a pool, so the run length
comes from L2 (D-19 §3 estimates the rungs).
**The new binary (2026-10-03, D-23).** Gate N1 ran on the binary before
Gate N0 item 10. Its outcome is reused on the item-10 binary on D-19 §4's
ground, which G §4 states in full: the new counters and timers observe;
they change neither what RocksDB compacts or when, nor the foreground's speed
beyond a margin ($\omega_i$ is counted in operations). ACT-4 and ARCH-5 on
the new binary test the first; the native arms' throughput on it, paired
against the pilots', tests the second, within a margin set by a dated entry before Gate N2 (D-24, §0.7 item 4). If either fails, Gate N1's reuse is reopened by a dated
entry.

**Gate N2 — static comparator on the new binary.** ACT-4 (native parity) first.
Then $\Theta_s$ on `Assoc` and the read-heavy power-law workload (D-13 §3) at
T = 2, 6 and 10, in the same-session design, with repeat counts from C §3.
Runs: $|\Theta_s| \times$ workloads $\times$ $T$ values $\times$ repeats.
(Amended 2026-10-03: the 2026-09-29 text said "one Zipfian workload"; D-13 §3
chose the power-law mix and forbids calling it Zipfian.) Since D-23 "the new
binary" is Gate N0 item 10's, and Gate N2 starts only after item 11's
calibrations: every price, $\kappa$ and its basis fixed and recorded before
any $\Theta_s$ outcome is seen (OBJ-2, CMP-9), so that no D-23 value is chosen
after seeing where the hull falls. D-17's screen, still in draft, works on the
vectors $(\mathcal C_W, \mathcal C_R, \mathcal C_S)$ (Proposition C.4) and
takes the amended terms with them. *Added 2026-10-03 (D-23):* the
$\Theta_s$ arms at $K_0 = 8$ also check Proposition D.11's cycle assumptions,
(b2) and (b3), which were measured only at $K_0 = 4$: from the event log's
`lsm_state`, whether every L0 merge still runs at $k_0 = K_0$ and ends before
the next flush, and how far the deeper jobs run with L0 empty; and, from
the host log, whether each job's window sees the L0 count in force during it
((d2)). Since (b3) holds at $K_0 = 4$ only on average, the
exposure $\varepsilon_x$ of D.11 ("When (b3) holds only on average") is
reported at $K_0 = 4$ as well as at 8, on the same binary; with both, the
secant slope gives D.11's corrected $B$. Where (b2) or (d2) fails at a
trigger, as D.11's guard defines it (OBJ-8(d)'s shares against tolerances
set by a dated entry before Gate N2, D-24, §0.7 item 4), the failure is reported with the measured
exposure, and D.11's closed form is not used at that trigger (its integer
form, (iii), and the direct comparison of admissible triggers still are).

**Gate N3 — learner-free test of Conjecture B.3′.** Hand-written state rules —
$K_0$ tracking Proposition D.11 at measured prices; releasing on neighbour fill;
holding for garbage; L0 compacting early while the slot is idle and reads are
heavy (Pathway A §4 (e)); interior levels holding releases while L0 is due or
within one flush of due (Pathway A §4 (f)) — against $\theta^\star_\beta$ in each
mode, with the stall rule applied (Global acceptance). This decides whether the
stationary-workload claim stays before any learner run. If neither slot rule
beats $\theta^\star_\beta$ in read priority, read priority's claim narrows to
changing workloads and suite robustness (claims 2 and 3) before Gate N5.
Amended 2026-10-03 (D-23): the rules use the amended cost. $K_0$ tracks the
amended Proposition D.11 at the D-23 prices; the hold-for-garbage rule is
scored with garbage's read price (Corollary B.6), and WL-3 reports its effect
on hidden steps; the early-L0 rule pays each extra L0 merge's job, read bytes
and interference, and may lose in read priority (Pathway A §4 (e), amended):
lowering the effective trigger from $K$ to $K'$ raises $J_\beta$ whenever
$KK' < (K^\star_\beta)^2$ (Proposition A.8(ii)–(iii)).
No rule times compaction to moments of light reads: on a stationary closed
loop there are none outside stalls, and a stall is not one for the charge
(Conjecture B.3′'s discussion, D §1). At the
default point the slot rules have few occasions (Conjecture B.3′'s
discussion, item (f)), so a null result for them there says little about
other configurations.

**Gate N4 — learner smoke test.** One workload, read priority, one $T$:
ARCH-1 to ARCH-6, OBJ-1, OBJ-3, OBJ-4, and since 2026-10-03 the per-run checks
and counters of OBJ-7 to OBJ-9 and the reward-pricing and charge-timing
criteria ARCH-7 and ARCH-8.

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
   admits two levels, never T=10 at this size. Gate N1 (D-19) found no pool
   in any cell, so this claim is not made at the current size.

**Stall rule** (applies to every claim). $J_\beta$ prices no time (D §1), so a
policy could lower it by deferring work until writes stall. A claim therefore
also requires, on the same paired runs and reported beside $J_\beta$ rather than
priced into it (OBJ-6), a measured-phase stall fraction no more than
$\delta_{\text{stall}}$ above the comparator's and foreground throughput no more
than $\delta_{\text{thr}}$ below it. Both use the paired interval's bound; both
margins are fixed in advance (§0.6 item 12). (Amended 2026-10-03 to match the
dated rule: the 2026-09-29 text said "stall seconds"; D-13 §8 and D-14 §1 judge
the stall fraction, stall seconds over `mixgraph`'s wall time, and D-15 §1
takes both bounds from the two-sided 95% paired interval.) Stall seconds are read as in D-11,
from the internal-stats `Cumulative stall` line differenced over the measured
phase, never from `rocksdb.stall.micros`.

**The stall rule and the interference charge (added 2026-10-03, D-23;
rewritten for the speed-neutral charge the same day).** $J_\beta$ still prices
no time (Lemma D.17). A job's interference charge is its bytes and priced
device time at the reference rate, priced at the read cost per operation over
its window (D §1), so it does not depend on how many operations the job
overlapped: a job run wholly inside a stall ($\Delta n_\iota = 0$) is charged
at the read cost of the operations before it, and a job partly inside one is
charged like any other (Lemma D.17(v)). Running compaction while the client
is stalled therefore lowers no interference charge, and that loophole is
closed by construction. (As the charge was first drafted, a job wholly inside
a stall carried no charge and a job partly inside one a smaller busy part; the
stall rule bounded that channel, by an estimate of about 1% of
$\mathcal C_R$ on `Assoc` at T = 10, critique Q1.) The stall rule stays, for
the route that remains: stall time is unpriced, so a policy could still lower
$J_\beta$ by deferring work until writes stall, and the rule bars any claim
that does. A stall also needs L0 at its slowdown trigger (20 files here) or
both memtables full (or pending compaction bytes above the soft limit, 64
GiB here against about 3 GiB held, which cannot fire in these runs), and $J_\beta$ prices the L0 probes and seeks that the first of these
costs every read. The same holds for work deferred to the
end of the run: jobs of the drain are charged their interference over the
last $n^{\mathrm{win}}$ operations of the measured phase (D §1), so deferring
compaction into the drain lowers no interference charge either, and, as for
the drain's bytes, the rule errs against a controller that ends with anchors
above 1. OBJ-6 (amended) reports, per arm, the share of compaction bytes in
jobs that overlapped no operation, stalls and drain apart, so how much work
ran in each is seen on every paired comparison.

**Physical interference beside the charge (added 2026-10-03, D-23).** OBJ-6
(amended) reports, per arm and paired against the comparator, the physical
interference $\mathcal I^{\text{phys}}$ beside the converted charge
$\mathcal I$ that $J_\beta$ uses. Job by job, when the window is the job's own
operations, the byte parts differ by the factor $q_\iota/\bar q$ and the busy
parts by $(q_\iota/\bar q)(\Delta t_\iota/t^{job}_\iota)$ (Lemma D.17(iii)),
and a job that overlaps no operation has a physical charge of 0; so they can
rank two arms differently only if the arms serve operations at different
rates during their jobs, take different wall time over their priced device
time, or run different work in stalls. It is reported, not a condition; the
throughput margin bounds how much slower a claiming policy may run.

**Re-checked 2026-10-03 (D-23).** Claims 1 and 2 are unchanged in form: they
use the amended $J_\beta$, on the item-10 binary with one set of prices
(CMP-9). Claim 3 gains a source: phases that differ in read cost per
operation price compaction differently (Pathway B §3, after Corollary B.4).
Claim 4 is unchanged and not made.

**If none holds.** If Gate N3 finds no state-dependent rule that beats
$\theta^\star_\beta$ and Gate N5 confirms it, the paper is an analysis paper: the
structure of the static optimum (Propositions D.11 and D.13, Theorem A.2, in
their amended forms), depth
monotonicity (Lemma A.6), the corrected elision analysis (Propositions B.1′ and B.1″), the
cost of exploration (Proposition H.3), and the propagation measurements
(Proposition G.1). Added 2026-10-03 (D-23): the complete cost model and its
time-free form (Lemmas D.17 to D.19, Proposition D.16 extended), the priced
trigger and lever (e) (Propositions D.11 and A.8), the interference term as a
level input (Proposition G.6), and the garbage trade-off with its read price
(Proposition B.5, Corollary B.6).

---

## References

- [Cao et al.] Z. Cao, S. Dong, S. Vemuri, D. H. C. Du. Characterizing, Modeling,
  and Benchmarking RocksDB Key-Value Workloads at Facebook. USENIX FAST 2020.
- [Cooper et al.] B. F. Cooper, A. Silberstein, E. Tam, R. Ramakrishnan,
  R. Sears. Benchmarking Cloud Serving Systems with YCSB. ACM SoCC 2010.
- [Cosine] S. Chatterjee, M. Jagadeesan, W. Qin, S. Idreos. Cosine: A
  Cloud-Cost Optimized Self-Designing Key-Value Storage Engine. PVLDB 15(1):
  112–126, 2021 (issue dated September 2021; the paper's own reference line
  prints 2022).
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
- [How to Grow] D. Mo, S. Luo, S. Idreos. How to Grow an LSM-tree? Towards
  Bridging the Gap Between Theory and Practice. Proc. ACM Manag. Data 3(3)
  (SIGMOD), Article 173, 2025; presented at SIGMOD 2025; arXiv:2504.17178.
  Source of the M3 convention used in P0-7.
- [Kearns & Singh] M. Kearns, S. Singh. Near-Optimal Reinforcement Learning in
  Polynomial Time. Machine Learning 49:209–232, 2002. Simulation Lemma
  (Lemma 4), entrywise, for a finite MDP.
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
  Proc. ACM Manag. Data 1(3), Article 213, 2023; presented at SIGMOD 2024;
  arXiv:2308.07013. Lerp and policy propagation in §5; evaluation in §7.
- [Sarkar et al.] S. Sarkar, D. Staratzis, Z. Zhu, M. Athanassoulis.
  Constructing and Analyzing the LSM Compaction Design Space. PVLDB 14(11), 2021.
- [SILK] O. Balmau, F. Dinu, W. Zwaenepoel, K. Gupta, R. Chandhiramoorthi,
  D. Didona. SILK: Preventing Latency Spikes in Log-Structured Merge Key-Value
  Stores. USENIX ATC 2019, pp. 753–766. An I/O scheduler that gives flushes
  and compactions the bandwidth client operations leave over, exploiting
  low-load periods of an open-loop client load (cited in Pathways A, B and F).
- [Strehl & Littman] A. L. Strehl, M. L. Littman. An Analysis of
  Model-Based Interval Estimation for Markov Decision Processes. Journal of
  Computer and System Sciences 74(8):1309–1331, 2008. Lemma 1, a
  fixed-policy bound of the type G.4 proves, and Lemma 2, their improvement
  of Kearns and Singh's Simulation Lemma.
- [van Hasselt et al.] H. van Hasselt, A. Guez, D. Silver. Deep Reinforcement
  Learning with Double Q-Learning. AAAI 2016.
- [Wolpert & Tumer] D. H. Wolpert, K. Tumer. Optimal Payoff Functions for Members
  of Collectives. Advances in Complex Systems 4(2–3):265–279, 2001.
- [YCSB] Yahoo! Cloud Serving Benchmark, core workloads A–F,
  `github.com/brianfrankcooper/YCSB` (wiki page "Core Workloads"). Workload F
  (read-modify-write) is defined there, not in [Cooper et al.], which defines
  A–E.
- [Zhu et al.] Z. Zhu, J. H. Mun, A. Raman, M. Athanassoulis. Reducing Bloom
  Filter CPU Overhead in LSM-Trees on Modern Storage Devices. DaMoN 2021.

RocksDB behaviour (score computation, file picking, `SetOptions`) is cited to
the pinned source, not to documentation.
