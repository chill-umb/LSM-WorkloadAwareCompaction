# Propagation law: model-based design under test (option D)

*Versions: §0-§8, 2026-10-10. §9, 2026-10-15: what step 1b and its follow-ups found, the owner's later decisions,
and the fast formula (record in `docs/PREREGISTRATION.md` D-28). §10-§11, 2026-10-20: v3 and v3.1. §12, 2026-10-20:
hard constraint R7. §13, 2026-10-26: the law for cases 1 and 2 (K raised, K lowered), v2 at round 18.*

**Status: design under test, not adopted.** Nothing here is in `docs/PATHWAYS.md`. Owner, 2026-10-10:
"we don't amend the PATHWAYS doc unless we prove a theory with data", and nothing about the propagation law
changes there "until we can reliably claim the law works for all workload sizes, not just some cherry picked
workload which guarantees the ideal condition". The law stays in Programme 1 until every avenue to fix it is
exhausted. Dated decisions and the predictions each test makes go into `docs/PREREGISTRATION.md` before the runs
that test them; this note holds the design and the test plan.

**Requirements (owner, 2026-10-10).**
- R1. It works at every T (2, 6, 10 and higher), not only where levels turn over often.
- R2. It works on both workloads (`Assoc`, the power-law mix) and at least two run sizes.
- R3. No testbed is chosen because it makes the law pass (e.g. OD-6's uniform-key T=2 idea is out).
- **R7 (hard constraint, owner, 2026-10-20).** The law is a general law of RocksDB, built from RocksDB's own
  mechanisms, with no workload-specific characteristic in it (§12).

## 1. Why the current law (PATHWAYS Pathway G) is being redesigned

Evidence (all exploratory, none from a gate):
- Gate N1 (D-19): no level passed the admission test in any cell; it needs about 300-1,000 turnovers per level
  (`~/node_ops/reports/2026-10-08-propagation-nmin/README.md`), which deep levels never get, and at T=10 there is
  no second interior level at all.
- Recheck (`~/node_ops/reports/2026-10-07-1710-propagation-law-recheck.md`): F1, the turnover clock is set by
  the level's own action; F2, the own-clock look-ahead cuts off effects below; F3, "same problem on its own
  clock" is assumed, not shown; F4, the test needs the scarce data.
- Deep channels (`~/node_ops/reports/2026-10-10-deep-channels/README.md`): the main cost a deep level moves in a
  run is a read cost in the level below (hidden steps), not its own write or space cost; L2's turnover clock moves
  23x with its own setting while its source-inflow clock does not; levels differ in shape (survival, overlap,
  hits, reopens) at any clock; events (merges, probes, scans) are plentiful at every level and T where turnovers
  are not.

The root problem: G shares *values* between levels and so must first prove levels are the same, counted in
turnovers. This design shares a *model of the workload and the tree* instead. A level needs no proof of sameness;
it needs its own numbers, which come from events.

## 2. The three decisions (owner's yes, 2026-10-10; adopted for testing only)

| | PATHWAYS now | This design |
| --- | --- | --- |
| 1. What a level's agent is for | mainly save write and space by merge timing (G §5) | choose the level's size during the run to lower every priced cost that size causes, at its own level and every level below; the model says which costs move |
| 2. Clock and look-ahead | turnovers N_j set cadence, reward unit and look-ahead (n_H = 2) | time in operations at every level; a decision each time source inflow from above reaches a fixed share of the level's native size; a choice is judged by its cost per operation over a fixed window W of operations, the path to the new size included |
| 3. Propagation form | one shared value part for levels that pass an equality test (turnovers) | one shared model of the workload and the tree, fitted from every level's events, plus one shared learned correction with the levels' measured numbers as inputs; L1 included as a source |

## 3. Candidate model M1: levels as age bands of one write stream

Precedent: Lim, Andersen and Kaminsky (FAST 2016) predicted multi-level LSM write costs under skew from the
number of distinct keys in a stream of writes. M1 extends that idea to every priced cost of cost model 2.

### 3.1 The workload, once for the whole tree
- In both workloads db_bench draws one key per operation from one popularity rule (key ranges with a two-term
  exponential hotness, `Assoc` 30 ranges; inside a range a power-law key seed hashed to a key), then picks the
  operation type (`tools/db_bench_tool.cc`, `DistGetKeyID`, `PowerCdfInversion`). So Gets, Puts and scan
  starts share one popularity p_k, and hot keys are scattered evenly inside their range.
- Puts arrive at w per operation; key k's put rate is w p_k. Mean entry size e is measured (mixgraph values vary).
- U(n) = sum_k (1 - exp(-p_k n)): distinct keys among n puts. U_r(n) the same inside key range r.

### 3.2 The tree as age bands (assumption A1, to be tested)
Level j holds, for each key written in its age band (a_{j-1}, a_j] (ages in puts), that key's newest version in
the band; band widths come from the sizes: S_j / e = U(a_j - a_{j-1}). The last level holds every key whose
newest version is older than a_{L-1}. T and the multipliers enter only through the sizes, so one p serves every
T, every setting and every run size.

### 3.3 What M1 predicts, per level j (each a measured quantity on the host log)
- Hidden entries: H_j = U(a_j - a_{j-1}) - [U(a_j) - U(a_{j-1})] (last level: U(a_{L-1})). Hidden steps per
  scan at j, per key range, weighted by where scans start.
- Survival of a push j -> j+1 (rho~_j): 1 - [U(D_j) + U(D_{j+1}) - U(D_j + D_{j+1})] / U(D_j), D the band widths.
- Overlap per source byte: phi_j U(D_{j+1}) / U(D_j), with phi_j RocksDB's file-choice factor (min-overlap
  picking), a per-level number from merge records.
- Gets ending at j: sum_k p_k [exp(-p_k a_{j-1}) - exp(-p_k a_j)]; probes at j need the key inside a file's
  span (coverage, per key range: small levels leave cold ranges uncovered).
- Reopens: the table cache holds 1,000 files (`max_open_files`, 16 shards). Che's approximation for an LRU
  cache: file f with access rate lambda_f is cached with probability 1 - exp(-lambda_f t_C), where
  sum_f (1 - exp(-lambda_f t_C)) = 1,000; file access rates follow from the hit and coverage predictions.
- Job bytes (S, O, X per merge), then job prices and interference through cost model 2's formulas.
- The path to a new size: growing is limited by surviving inflow from above (which falls as the level grows,
  through U; this should reproduce the L2 forward step's plateau at ~447 MB); shrinking is limited only by
  compaction speed (the reverse step drained in < 1.4M operations).

### 3.4 Decision rule
For each candidate multiplier, M1 predicts cost per operation over the next W operations, path included, at the
contract's prices and the run's priority. The agent takes the cheapest, corrected by the learned part (§4).
W is one number for every level and T, fixed in the dated entry before any test.

## 4. The learned part and what propagates
- Shared by every level and every T: M1's equations, the fitted workload p (each level's events pin down the part
  of p its band covers: upper levels the hot keys, deep levels the cold ones), and one learned correction
  g(state, action, level numbers) for what M1 misses (timing, interference).
- Per level: its measured numbers (phi_j, coverage, sizes) from its own merges, probes and scans, and a small own
  correction that grows with its own data (G.5's shrinkage idea, kept).
- L1 is a source: at T=10 it is the only data-rich interior level, so without it nothing propagates there (R1).

## 5. Checks that replace the admission test (events, not turnovers)
- Model check, per level: M1's predictions (§3.3) against the level's own measured events, over rolling windows,
  inside margins fixed in advance. A level that fails keeps using its own data only, and the failure is logged.
- Transfer check, per level: the shared correction lowers held-out prediction error at that level against a
  correction fitted on that level alone.
Data per run at the scarcest levels: T=10 L2 ~3,100 merges, 38M Get probes, 1.9M scans; T=2 L6 ~165 merges,
17M Get probes, 1.9M scans (deep-channels report).

## 6. Proof plan (nothing enters PATHWAYS until all three pass, at every T, workload and size)

**Step 1, offline, existing data.**
- 1a. This note.
- 1b. Code M1; test it on existing runs with margins fixed first (§6.1):
  - (i) p taken from db_bench's generator (tests A1 and §3.3 alone);
  - (ii) p fitted from per-level counters (tests whether events identify p);
  - (iii) across T: fit at T=2, predict T=6 and T=10 with no refit, and the reverse;
  - (iv) across sizes: fit on 29M runs, predict 58M / 145M;
  - (v) across settings: fit on native T=10 `Assoc`, predict the L2 map statics, the L2 and L1 steps and R0a.
  Data: N1 and diag-native-d21 natives (T=2/6/10, both workloads, schema 1-2: hits, probes, seeks, reopens,
  flows), the 57 T=10 `Assoc` schema-3 runs (adds hidden steps per level). Gaps: hidden steps per level outside
  T=10 `Assoc`, and power law at schema 3; step 2 fills them.
**Step 2, node (lean plan with corners (a)-(d) restored; owner, 2026-10-10 ~22:00 UTC).** Gate N2 runs on
other nodes (five in parallel), not on node B, on a binary that writes schema-3 host logs. Its natives and statics,
at its own size and seeds, double as M1's test data; M1's predictions for them are registered in
`docs/PREREGISTRATION.md` before N2's arms run. Added on top of N2, so R1-R3 still hold:
- (a) natives at a second size on the same binary (schema 3), every T, both workloads (~3.4 h);
- (b) deep-level statics at T=2 and T=6 if N2's static set (D-17) does not change deep multipliers (~6.6 h);
- (c) the full step set: grow and shrink steps at two interior levels per T and workload (~21 h; L1/L2 at T=10
  `Assoc` already exist).
**Step 3, node.** M1 replaces the hand-written prior in the controller. Propagation on/off is Gate N6's PROP-4
ablation, and (d) its scope is every T, both workloads and two sizes (~144 runs), fixed in the dated
PREREGISTRATION entry (not in PATHWAYS). Controllers are judged against same-config natives (the twin rule).

### 6.1 Margins for step 1b (owner confirmed 2026-10-10 ~22:00 UTC, before any scoring; first row's floor changed)

| quantity, per level | margin |
| --- | --- |
| share of Gets ending at the level | +/- 15% relative, but never tighter than +/- 0.01 absolute |
| probes per Get at the level | +/- 15% |
| survival rho~ of a push | +/- 0.10 absolute |
| overlap per source byte | +/- 20% |
| hidden steps per scan at the level (schema 3) | +/- 20% |
| reopens per probe | +/- 25% |
| change under a setting change (v) | right sign at every level, size within +/- 30% of the measured change |

M1 passes 1b only if every level of every run set passes. A failure is reported as found; a revised model (M2)
is a new dated version of this note, never a quiet edit.

## 7. Fallbacks kept on record (untried)
- B. The current law on a larger testbed (fails R3 if chosen to make pooling possible).
- C. The admission test counted on events (needs an argument that per-event sameness implies a shared value).
- E. Pooling across runs (the pre-trained variant): covers L3 at T=2 in ~28 runs; L4 and deeper out of reach.

## 8. Risks known now
- A1 may not hold under RocksDB's file-by-file, min-overlap compaction (ages inside a level are mixed); 1b(i)
  tests it before anything else.
- The load (`filluniquerandom`) builds a state the band picture may not describe (L2 at 962 MB, 3x target, at
  the measured phase's start; the x2 static's empty-looking L3).
- Interference and additivity (OD-1 to OD-3) are unproven; any error there reaches every level.
- The plugin sees no keys: p must be fitted from aggregate per-level counters (1b(ii)).

---

## 9. Version of 2026-10-15: what step 1b and its follow-ups found, and the fast formula

**Status: design under test, not adopted.** §1-§8 above are the 2026-10-10 version, unchanged.
- **Record.** The owner's decisions and every test's result are in `docs/PREREGISTRATION.md`: D-28, and §2,
  "Propagation route D: exploratory record". Both were recorded after the fact, at the owner's request.
- **Status of the proof.** Nothing here is proven. PATHWAYS is unchanged, step 1b stays failed, PROP-1 stays at
  D-19.

### 9.1 What the tests showed about RocksDB (measured; T=10 unless stated)

- **The age-band picture (A1) is false** at every T and workload, from the preload on. M1 failed step 1b(i),
  237/614 (2026-10-10).
- **RocksDB pushes the hot files first.** A level's files go in order of (bytes below / file bytes). Where a level
  is dense (the hot ranges), a file covers a narrow key span with little below it. So the bytes a level gains or
  loses are mostly hot data:
  - the first 19-50% of L2's bytes in pick order hold 1.8-2.7× L2's average hits per byte;
  - L2 ×2: +19% bytes, +72% L2 hits; L2 ×0.5: -50% bytes, -70% hits.
- **Per-level totals cannot carry this.** At the same 17 MB above, L1 serves 7.0% of Gets at T=2 and 1.1% at T=10.
- **Overlap per source byte equals the next level's density inside the merge's own key range.**
  - The whole-file excess at the edges is 0.00-0.05 files per merge, measured by MANIFEST replay.
  - Under a step, the change is all in that density: L2 ×0.5 ×2.36; L1 ×0.5 ×0.32; L1 ×2 ×1.29.
- **L1 responds to L2's layout.**
  - Under L2 ×0.5, 29% of L1's jobs land where L2 covers less than half the range: spots L2 has just emptied. They
    carry overlap 0.50 against 1.46, and some are trivial moves.
  - In the twin such landings are 1.5%; under ×2, none.
- **Every L0 → L1 merge takes all of L1.** In the hottest key range (72.5% of Gets), RocksDB pushes L1's data down
  every 10.7k operations.
- **Loaded data persists in the levels.**
  - mixgraph writes only 119,855 distinct keys.
  - At T=10, 45% of L2 (71.7k entries) is keys loaded before the run and never written. They stay until their spot
    is pushed. Under ×0.5, 28.4k were left in L2 at chunk 4's end.
- **L0.** L0 files while Gets run ≈ (K−1)/2 + L0 merge duration / flush interval (76/76 chunks within 0.7%). A
  deep step moves L0 through foreground speed: T=10 L2 ×0.5 cut the flush interval from 180 to 167 ms.
- **A file-level model reproduces most of this offline, but fails the every-cell rule.** The file model
  (`~/node_ops/diag/slicev/filev.py`) is a key-by-key simulation over RocksDB's real files, frozen 2026-10-15.
  - Weighted pass rate 0.98-0.99 on three test sets.
  - Change cells 299/439.
  - T=2 is noisier than its allowances, and is handled as an edge case (D-28 1(c)).

### 9.2 What the design now requires (owner's decisions, D-28 §1)

- **R4.** The controller runs a fast formula, never a simulator (2026-10-11). Offline models are test benches only.
- **R5. The controller's snapshot carries per-file data (option (b), 2026-10-15).**
  - **Fields:** per file, key range, bytes, entries and RocksDB's sampled read count, read without the DB mutex.
  - **Why.** The step response lives in individual key ranges and files, which per-level counters cannot show
    (§9.1).
  - **Not built.** The exact fields wait for the formula's inputs; then a fork change with tests and the
    preflight.
- **R6.** Measured tests come before any formula change (2026-10-15).
- **R1-R3 (§0) still hold.** Every T, both workloads, two sizes, no testbed chosen to pass.

### 9.3 The fast formula

**What it must predict.** For each candidate setting at level j: the priced cost per operation over the next W
operations, at j and every level below, including the path to the new size. Its parts:
- bytes written and read, and jobs by kind;
- probes, block reads, seeks, reopens, hidden steps and heap children per level;
- interference;
- space;
- the operations until the level reaches its new size.

**Inputs.** The controller's per-level counters and job records (today), plus the per-file list (R5). In the
offline checks:
- **Per-file data** comes from the dumps and MANIFESTs.
- **Per-key Put and Get rates** come from db_bench's exact probabilities (`keypop2`). The controller cannot see
  them, and how it would estimate them from per-file data is not yet tested.

**Candidates tried** (development runs of 2026-10-11 only: T=10 `assoc30` twin, L2 ×0.5 and ×2):
1. **One-shot ranking** of the current files by RocksDB's pick order: **3/7, failed.**
   - It finds the right files, the hot ones.
   - It cannot see the level below refilling over time.
2. **Balance formula: 5/8, failed.**
   - **How it works.** Each key range of a level cycles: emptied by a push, refilled by the keys written since,
     pushed again when its entries reach the next level's entries there over one threshold r_j per level. r_j is
     solved by bisection so the average contents equal the level's size. Loaded data stays until its range is
     pushed. Growth is modelled as no pushes until the new size is reached.
   - **What it gets right.** It needs no time stepping, and it gets the L2/L3 hits right: four cells within 2-6%.
   - **What it gets wrong:**
     - the threshold's change: ×3.01 against the measured density ×2.36 at L2 ×0.5;
     - L1: its cycles in the hottest range are 40.4k operations against the measured 10.7k, which puts 0.030 of
       Gets in L1 there against 0.000;
     - the L1 overlap pull toward L2's gaps under ×0.5.

### 9.4 Test data and open risks

- **Development data is small.** Three runs at T=10, steps at L2 only. Tests A/B/C have been used by the file model
  and are unfitted for the formula, but their summary numbers have been seen. A clean test of the formula needs
  fresh step runs at other T, levels and both workloads (node C's lease ends about 2026-10-19).
- **MANIFEST replay as evidence awaits the owner's ruling** (D-28 §3).
- **Per-key rates are not observable** by the controller. This is the largest open input question.
- **L0 needs measured inputs.** The L0 merge duration and the foreground speed must enter the formula (§9.1, L0).
- **Interference and additivity** (D-26) are unchanged from §8.

### 9.5 Next steps (each needs the owner's go)

1. Compare the balance formula's per-range cycle with the measured per-range push cycle (MANIFEST replay) at L1 and
   L2, for the twin, ×0.5 and ×2.
2. Then a revised formula, with its rules fixed before scoring, on the development runs.
3. Fresh step runs for the formula's clean test, planned for approval.
4. The snapshot export (R5), once the formula's inputs are settled.

## 10. Version of 2026-10-20: distinct keys (fix 1), every cell cycles (fix 2), and the test plan

*Entered 2026-10-20 at the owner's go ("Yes, go for all three"), unchanged from the draft sealed at 11:22:24 UTC
(`~/node_ops/drafts/2026-10-20-design-note-v10-draft.md`, sha256 bba9cd66c2daa1ac7a2083b97aa2b35d276a6c709e70c5a80a6fd74c24c614ff).*

**Status: design under test, not adopted.**
- §1-§9 are unchanged.
- PATHWAYS is unchanged. Step 1b stays failed. PROP-1 stays at D-19.
- Evidence: `~/node_ops/reports/2026-10-20-fullscale-recheck/` and `~/node_ops/reports/2026-10-20-why-5-13/`
  (18 replayed full-scale runs, W1/W2 × T 3, 6, 9, 128 MiB / K=4).

### 10.1 What went wrong

- **#5, the file model.** Its merge rules were right; its input was wrong.
  - At development it drew Puts from db_bench's exact per-key probabilities ("keypop", from enumerating the
    generator; `balance_check_rules.md` v2).
  - In the full-scale re-check it drew them from the run's own Put counts, treated as probabilities. That loses the
    keys a run writes once or twice: distinct keys fell to ×0.87 (W1) and ×0.67 (W2).
  - So too many copies dropped at the memtable, L0 and L1 (W2: ×2.4-2.6).
  - Fed the run's real Put order instead, with nothing else changed, it scores 1.000 (572/573 cells).
- **#13, balance formula v2.** It has the same input bias, plus one wrong rule.
  - The rule: a cell holding loaded, never-written data does not cycle unless full.
  - At full scale nearly every L2 cell holds some. So v2 freezes most cells (W2 T6: 4-6% cycle), while RocksDB
    empties 50-90% of the frozen ones.
  - A frozen cell keeps its written keys at L2. That is 97-101% of W2's extra L2 hits.

### 10.2 Fix 1: count distinct keys correctly from measured counts

**Rule.** A level, or a cell of it, holds one entry per distinct key. A merge drops one copy per extra version.
- So every formula and every test bench must get the number of distinct keys written in a stretch of operations
  right.
- A per-key count c_k measured over a window of M operations is a measurement, not a rate: Poisson draws at rate
  c_k / M double the noise.

**Estimator** (exact in expectation for any stretch of t <= M operations inside a window whose operation order is
random, as in W1 and W2):
- q(t; c) = (1 - t/M)^c: the chance that a key with c Puts in the window gets none in a given stretch of t
  operations.
- Q(t; c) = (1/t) ∫_0^t q(u; c) du = M / ((c + 1) t) · [1 - (1 - t/M)^(c+1)]: the same, averaged over a stretch
  whose length is uniform on [0, t].
- **D_x(t) = Σ_{k in x} [1 - q(t; c_k)]:** the distinct keys written into cell x in a stretch of t operations.

**Where it replaces the old forms:**
- In balance formula v2, every e^(-w_k t) becomes q(t; c_k), and every (1 - e^(-w_k t)) / (w_k t) becomes Q(t; c_k).
- For the file model, the rule is: drive it with the exact Put stream (`suitegen`) or exact generator
  probabilities, never with counts used as probabilities.

**Limits:**
- It assumes the Put pattern does not change inside the window. W3-W5's phases are outside this version.
- It needs per-key counts, which the controller does not have (§9.4). In the offline tests below, the counts come
  from the run's own stream, a stand-in.
- How the controller would estimate D_x(t) is open; it needs its own check after this version passes. Candidates are
  flush entries against Puts, merge entries in and out per key range, and per-file entries (R5).

### 10.3 Fix 2: every cell cycles, starting from the state it is in

Balance formula v3 = v2 with fix 1, and this rule in place of v2's change 2 ("which cells cycle"). The same rule
holds at L1 and L2.

**Inputs:**
- **The window and the state.** W = the measured phase (40M operations, M = W). The state s0 is the run's start of
  measurement: the replayed state at measured-phase operation 0. The controller would read it from the per-file
  snapshot.
- **The cells x of level j:** level j's files at s0, with v2's spans.
- **E_x:** the cell's entries at s0. **n_x:** level j+1's entries in x at s0, spread evenly as in v2.
  - At L1, n_x is L2's predicted mean entries, spread evenly over each L2 cell, as in v2.
- **cap_x = n_x / r_j,** one threshold r_j per level.

**Equations:**
- **First stretch** (from s0 until the cell is first pushed): content C_x(t) = E_x + Σ_{k in x, k has no entry at
  level j at s0} [1 - q(t; c_k)].
  - A rewritten key already at level j adds nothing; a key arriving from above adds one entry.
- **First push:** t1_x = the least t with C_x(t) >= cap_x.
  - t1_x = 0 if E_x >= cap_x.
  - t1_x = W if the cap is not reached within W.
  - The cell's loaded data goes down with that push. Nothing is frozen by rule.
- **After the first push,** the cell cycles from empty. τ_x solves D_x(τ) = cap_x, with τ_x = ∞ if D_x(M) < cap_x.
  The stretch it averages over is τ̃_x = min(τ_x, W - t1_x).
- **Mean entries of x over the window:**
  m_x = (1/W) · [∫_0^{t1_x} C_x(t) dt + (W - t1_x) · Σ_{k in x} (1 - Q(τ̃_x; c_k))].
  - The integral is closed form: ∫_0^a q(t; c) dt = M/(c+1) · [1 - (1 - a/M)^(c+1)].
- **r_j** is solved by log-bisection, on [1e-2, 1e4] as in v2, so that Σ_x m_x × 1040.52 equals level j's measured
  mean bytes over the window. L2 is solved first, then L1, as in v2.
- **Time shares.** φ_x = t1_x / W is the share of the window before cell x's first push.

**Hits** (time-averaged over the window; levels independent, as in v2):
- **Above L1:** E0_k = Q(A0; c_k). A0 is solved, as in v2, so the predicted share of Gets above L1 equals the run's
  measured memtable + L0 share.
- **Key k in L1 cell y.** It is "not at L1":
  - before the push, with probability 0 if k has an L1 entry at s0, else Q(t1_y; c_k);
  - after the push, with probability Q(τ̃_y; c_k).
  - E1_k mixes the two with φ_y. P(newest at L1) = E0_k (1 - E1_k).
- **Key k in L2 cell x:** P(newest at L2) = E0_k E1_k · [φ_x a_k + (1 - φ_x)(1 - Q(τ̃_x; c_k))].
  - a_k = 1 if k has an L2 entry at s0, else 1 - Q(t1_x; c_k).
  - A loaded, never-written key (c = 0) counts at L2 only until its cell's first push.
- **Hit share at level j** = Σ_k g_k P(newest at j) / Σ_k g_k, with g_k the key's Gets in the window (stand-in).
- **L3 and below** = 1 - (measured memtable + L0) - L1 - L2.

**Kept from v2 and named as limits:**
- n_x held at its s0 value;
- no delay for a key to pass the levels above;
- levels independent;
- overlap changes by v2's r-ratio form (reported, not scored).

### 10.4 Test plan

**Step 0: seal.** This text is sealed before any number is computed with either fix.

**Step 1: development, on the 18 replayed runs (practice set; already seen, so not a test).**
- **Code self-check:** with both fixes switched off (Poisson rates c_k / 40M, v2's cycle rule, the state at
  operation 20M), the new code must reproduce `balance_fs.py`'s hold numbers exactly.
- **Reported only:**
  - the hold L1 and L2 hit shares;
  - the share of L2 cells pushed at least once, against the replay (`rules.md` §3's emptying, cells at s0);
  - L2's mean never-written loaded share, against the replay's mean over the 9 checkpoints.
- **Allowed:** code bug fixes only. Any change to an equation, constant or rule is a new dated version, before Step 2.

**Step 2: held-out runs, scored (needs the owner's go for the node time and a preflight).**
- **Runs:** W1 and W2 × T ∈ {4, 8} × settings {64:4, 128:4, 256:4} × 3 repeats = 36 runs on 6 trees, 40M operations
  each, at the suite geometry, every tree kept (`SUITE_KEEP_TREES=all`).
  - About 1 hour per setting at the T3-T9 sweep's pace, so about 6 hours on the prime.
  - T 4 and 8 have never been seen. T 2, 5, 7 and 10 stay unseen for later versions.
- **Instruments:** each tree's snapdump and each run's exact replay, with the same checks as `replay.py` (all must
  pass); `truth.py` counters.

**Scored rules** (fixed now; noise thresholds as the re-check's §5, from the held-out repeats):
- **F1(a), test bench:** the file model driven by the run's exact Put stream, on all 36 runs, with L1 base from the
  setting and the other constants of the re-check's §4. It passes if the weighted balanced pass rate is >= 0.98
  (frozen scorer, rules a4). The every-cell rule is reported.
- **F1(b), estimator:** in every run, D(L) from the run's counts against the exact mean distinct keys over the
  stream's disjoint windows of L operations, for L = one memtable (the run's mean Puts per flush, in operations), 1M,
  5M and 20M. It passes if every value is within ±2%.
- **F2(c), hold:** in every run, from its own s0, the L1 and L2 hit shares against the counters, within the frozen
  share allowance max(0.01, 0.15 × measured). It passes if every cell passes.
- **F2(d), mechanism:** in every run, two checks; it passes if both pass in every run.
  - The predicted share of L2 cells with t1_x < W is within ±0.10 of the measured share emptied at least once.
  - The predicted L2 never-written loaded share, Σ_x P_x φ_x / Σ_x m_x, is within ±0.05 of the replay's mean.
- **F2(e), static change:** for (64:4, 128:4) and (256:4, 128:4) per workload, T and repeat, each setting predicted
  from its own tree, on the L2 and the L3-and-below hit-share changes.
  - A cell passes with the right sign and |pred - meas| <= 0.30 |meas|.
  - A cell counts only if |meas| reaches its noise threshold (at least 0.02) and the deepest level is the same in
    both runs (Addendum 1's rule).
  - It passes if every counted cell passes.

**Verdicts:**
- **Fix 1 is verified** if F1(a) and F1(b) pass.
- **Fix 2 is verified** if F2(c), (d) and (e) pass.
- A pass makes v3 a candidate for the mid-run step test (D-29 §4) and for the controller's own inputs: the
  distinct-key estimator without per-key counts, and R5. It does not touch PATHWAYS.
- A failure is reported as found.

**Reported, not scored:** the L1 change and overlap cells; r_j and any bound hit; prediction time per run (R4).

**Not covered by this plan:**
- the second run size (R2);
- T 2 and 10 (R1);
- W3-W5;
- a mid-run step;
- inputs the controller can actually observe (per-key counts are a stand-in throughout).

## 11. Version of 2026-10-20 (v3.1): an L2 cell fills to its cap, then holds it while entries leave in pieces

*Entered 2026-10-20 at the owner's go ("go ahead, draft v3.1. Then apply the fix, run it and test it"), unchanged from the draft
sealed at 18:49:22 UTC (`~/node_ops/drafts/2026-10-20-design-note-v3.1-draft.md`, sha256
f20c99fef1696d9b9d2db9cadf3ae5c572b9e2e8df622c5024f546e0154f1af9).*

**Result: FAILED at Step 1** (2026-10-20, run 18:57-19:23 UTC; this block was added after the run, at the owner's
instruction). The sealed text below is unchanged; its status line is superseded by this one.
- **Numbers.** On the 18 practice runs, within the Step 2 allowances (shown, not scored), v3 → v3.1:
  - L1 12 → 12 and L2 13 → 12;
  - L2 cells pushed 6 → 18 and the never-written share 6 → 12;
  - the hot-cell stay 3 → 3.
- **All code checks passed,** so the failure is the rule's.
- **W1's L2 miss grew:** at T6 from -0.050..-0.055 to -0.056..-0.060, and at T9 from -0.068..-0.084 to
  -0.083..-0.099.
- **Why.**
  - v3.1 holds every pushed cell at one fill per L3 entry. In RocksDB, the busy cells hold 1.3-2 times more per L3
    entry than the quiet ones (W1 T9 0.26-0.27 against 0.13).
  - v3 had the quiet cells about right and the busy ones low.
  - v3.1 raised the quiet cells. The level's total is fixed, so it took those entries from the busy cells.
- **Not taken to Step 2.** The held-out runs (worker 10, T 4 and 8) stay unread for the law. Report:
  `~/node_ops/reports/2026-10-20-v31-step1/README.md`.
- **It also fails R7 (§12):** the rule was chosen from practice-run measurements (§11.1), not derived from RocksDB's
  code.

**Status: design under test, not adopted.**
- §1-§10 are unchanged. PATHWAYS is unchanged. Step 1b stays failed. PROP-1 stays at D-19.
- Evidence: `~/node_ops/reports/2026-10-20-v3-step1/`, `2026-10-20-v3-diag/`, `2026-10-20-v3-piece/` and the
  measurement in §11.1 (`~/node_ops/fullscale/v3/cellrate/`); all on the 18 practice runs.

### 11.1 What v3's Step 1 and its follow-ups showed (practice set; report only)

- **v3, Step 1.** W2's L2 hit share is within ±0.002 in all 9 runs. W1's is low by 0.05 at T6 and 0.07-0.08 at T9.
  L2 is within the share allowance in 13/18 runs.
- **Why only W1 shows it.** W1 sends 96% of its Gets to written keys, W2 70% to never-written keys. The defect is in
  both workloads; W2's fit is small errors that cancel.
- **The piece test (rules sealed 17:07:47 UTC).**
  - RocksDB pushes L2 in pieces: in the busiest cells, 60-98% of the entries pushed leave in jobs that take less
    than half of the cell. v3 pushes the whole cell at once.
  - It does not spare hot keys. In those cells, keys Put at least 4 times leave L2 at the same rate per operation
    as the rest (ratio 0.96-1.11 at W1 T6/T9 and W2 T9). The exceptions are W1 T3 (2.2) and W2 T6 (0.77-0.80).
  - An entry's mean stay at L2 per visit is 1.3-1.9 times v3's (W1 2.0-2.6M operations against 1.1-1.8M).
  - With the measured time at L2 in place of v3's L2 presence, the L2 hit share is within the allowance in 18/18
    runs.
- **How the level shares its entries among cells** (`cellrate.py`, measured only, before this text). Over the s0 L2
  cells of a run, two candidate rules were set against the replay:
  - **(A) One push rate per entry for the whole level: ruled out.** The rate per entry varies 8-18 times between
    the busiest cells and the rest in W1 (spread of its log, entries-weighted, 1.3-2.2).
  - **(B) Each cell's fill is set by the entries below it** (mean entries / L3 entries in the cell): spread of its
    log 0.40-0.62 in W1 and 0.38-0.57 in W2. That is smaller than (A)'s in 17/18 runs, weighted either by entries
    or by Gets.
  - Busy cells still hold more per L3 entry than quiet ones (W1 T9 median 0.26 against 0.14). The first stretch
    below allows that: a quiet cell spends much of the window filling.

### 11.2 Balance formula v3.1: an L2 cell fills to its cap, then holds it while entries leave in pieces

v3.1 = v3 (§10.2 and §10.3) with one change: at L2, "after the first push the cell cycles from empty" becomes the
held stretch below. L1 keeps §10.3's rule unchanged, because no per-key measurement of L1 exists yet (R6).

**Inputs:** as §10.3 (W = M = 40M operations, the state s0, the cells x = L2's files at s0, E_x, n_x,
cap_x = n_x / r_2).

**First stretch, unchanged:** C_x(t) and t1_x as in §10.3.
- t1_x = 0 if E_x >= cap_x.
- t1_x = W if the cap is not reached within the window.

**The held stretch** [t1_x, W), of length D_x = W - t1_x:
- **The cell holds its cap.** Its mean entries over the held stretch equal cap_x. RocksDB pushes the most over-full
  pieces first, which keeps a cell near its cap instead of emptying it.
- **One rate per cell.** Every entry in the cell, written or loaded, leaves at the same rate μ_x per operation,
  whatever its age and however often its key is written (the piece test's second finding). The mean stay per
  visit is 1/μ_x.
- A key written while it has no L2 entry gets one at once (as in §10.3: no delay through the levels above).

**Presence of key k in cell x.**
- **Notation.** c_k is the key's number of Puts in the window; by §10.2, they fall at uniformly random operations.
  a0_k = 1 if k has an L2 entry at s0, else 0.
- **First stretch** (t <= t1_x): p_k(t) = 1 - (1 - a0_k) q(t; c_k).
- **Held stretch** (t = t1_x + d, 0 <= d <= D_x):
  p_k(t) = e^(-μ_x d) [1 - (1 - a0_k) q(t; c_k)] + μ_x ∫_0^d e^(-μ_x s) [1 - q(s; c_k)] ds.
  - Derivation: k has an L2 entry at t exactly when no push has come since its last source. That source is its
    last Put before t, or the start of the held stretch if it had an entry then.
  - A loaded, never-written key (c = 0, a0 = 1) gives e^(-μ_x d): it leaves at rate μ_x and does not return.
  - With μ_x = 0, this is the first-stretch form continued.
- **μ_x** solves Σ_{k in x} ∫_0^{D_x} p_k(t1_x + d) dd = D_x cap_x.
  - It is found by log bisection on [1e-12, 1] per operation. The left side falls as μ_x rises, and at μ_x = 0 it
    is at least D_x cap_x.
  - If D_x = 0, the cell is never pushed and μ_x = 0.
  - If cap_x = 0 (nothing below at s0; 19 cells at T3 and T6), μ_x takes the bound 1, and the cell holds almost
    nothing after operation 0.
- **The integrals** use closed forms where they exist (c = 0, and §10.3's first stretch). Otherwise they are
  numerical: Gauss-Legendre on geometric pieces of [0, D_x]. Keys in one cell with the same (c, a0) share one value.

**Mean entries of x:** m_x = (1/W) [∫_0^{t1_x} C_x(t) dt + D_x cap_x].
- This does not depend on μ_x. So r_2 is solved as in §10.3: log bisection on [1e-2, 1e4], with
  Σ_x m_x × 1040.52 equal to L2's measured mean bytes.
- μ_x is then solved once per cell. L1 follows as in §10.3, with L2's new m_x spread evenly over each L2 cell.

**Hits:** as in §10.3, with key k's L2 presence bracket replaced by its time average over the window:
- P̄_k = φ_x a_k + (1/W) ∫_0^{D_x} p_k(t1_x + d) dd, with a_k as in §10.3 (1 if k had an L2 entry at s0, else
  1 - Q(t1_x; c_k)).
- For a loaded, never-written key in x: φ_x + (1 - e^(-μ_x D_x)) / (μ_x W).
- E0, E1, the L1 term and "L3 and below" are unchanged.

**Pushes** (for the mechanism check): key k's predicted pushes are μ_x ∫_0^{D_x} p_k(t1_x + d) dd. The mean stay per
visit over a set of cells is its predicted time at L2 (W Σ P̄_k) divided by its predicted pushes.

**Kept, and named as limits:**
- L1 keeps v3's whole-cell cycle.
- The cells are L2's files at s0, with n_x held at its s0 value; a cell with n_x = 0 holds nothing.
- There is one rate per cell. Differences inside a cell are not modelled (W1 T3's 2.2).
- The held stretch's mean is exactly cap_x. Piece sizes (a median of 7-45% of a cell per job) are not modelled; r_2
  absorbs any common factor.
- As in v3: levels are independent; keys need no time to pass the levels above; overlap uses v2's r-ratio form
  (reported only); per-key counts are a stand-in (§10.2's limits).

### 11.3 Test plan

**Step 0: seal.** This text is sealed before any number is computed with v3.1. The §11.1 measurement used measured
quantities only.

**Step 1: development, on the 18 practice runs (already seen, so not a test).**
- **Code checks.** All must pass; a failure is fixed in the code.
  - With the new rule off, the code reproduces `balance_v3.py`'s v3 numbers exactly (L1 and L2 hit shares, r1,
    r2, A0; 18 runs).
  - The numerical integrals match a fine trapezoid reference within 1e-6 relative, on a sample of groups.
  - The presence equation matches a Monte Carlo simulation of the rule, within 4 standard errors, over a grid of
    (c, a0, t1, μ). The simulation uses uniform Put positions and Poisson push times while the entry is present.
  - The balance holds: Σ m_x × 1040.52 is within 1e-6 of the measured bytes, and each held stretch's mean is
    cap_x within 1e-6.
- **Reported only,** with v3's numbers beside them:
  - the hold L1 and L2 hit shares (the share allowance shown);
  - the share of L2 cells with t1_x < W, against the replay's share of cells from which an L2 job removed at least
    one entry;
  - the L2 never-written loaded share, against the replay's mean over the 9 checkpoints;
  - the mean stay per visit at L2 in the hot cells, against `replay_piece.py`'s;
  - prediction time per run.
- **Allowed:** code bug fixes only. Any change to an equation, constant or rule is a new dated version (v3.2), and
  Step 2 waits for it.

**Step 2: held-out runs, scored.**
- **Runs:** as §10.4 Step 2: W1 and W2 × T 4, 8 × settings 64:4, 128:4, 256:4 × 3 repeats = 36 runs. They are run
  on worker 10, with its version identity matched to the prime's.
- **Instruments:** as §10.4, plus `replay_piece.py` on every run. All their checks must pass.
- **Scored rules:** as §10.4, with these changes:
  - **F1(a) and F1(b):** unchanged (fix 1 is unchanged).
  - **F2(c) and F2(e):** unchanged, with v3.1 in place of v3.
  - **F2(d), mechanism:** three checks; it passes if all three pass in every run.
    - The predicted share of L2 cells with t1_x < W is within ±0.10 of the measured share from which an L2 job
      removed at least one entry. This replaces "emptied at least once", which the diagnostic showed undercounts
      piece pushes.
    - The predicted L2 never-written loaded share, from this section's presence, is within ±0.05 of the replay's
      mean.
    - New: the mean stay per visit at L2 in the hot cells is within ±25% of `replay_piece.py`'s. The hot cells are
      the fewest cells holding half of the written-key Gets.
- **Verdicts:**
  - Fix 1 is verified if F1(a) and F1(b) pass.
  - v3.1's L2 rule is verified if F2(c), (d) and (e) pass.
  - A pass makes v3.1 a candidate for the mid-run step test (D-29 §4). It does not touch PATHWAYS.
  - A failure is reported as found.
- **Reported, not scored:** as §10.4, plus v3's numbers on the same runs.
- **Not covered:** as §10.4.

## 12. Hard constraint R7 (owner, 2026-10-20): a general law of RocksDB, not a fit to a workload

*Added 2026-10-20 at the owner's instruction, after v3.1 failed (§11). It governs every version from v3.2 on and
every later test of the law.*

**The owner's words:** "The propagation law must be a general law, not fitted to a specific workload. It must not
contain any workload specific characteristics, rather it should contain general RocksDB characteristic."

**What the law may contain:**
- **RocksDB's own mechanisms,** each named with the source function or option it comes from. For example:
  - a level's compaction score (its bytes over its target);
  - the order in which a level's files are picked: `compaction_pri` is `kMinOverlappingRatio` here, implemented by
    `SortFileByOverlappingRatio` in `db/version_set.cc`;
  - clean-cut expansion: `ExpandInputsToCleanCut` in `db/compaction/compaction_picker.cc`;
  - where output files are cut: `CompactionOutputs::ShouldStopBefore` in `db/compaction/compaction_outputs.cc`;
  - trivial moves;
  - which versions a merge drops.
- **Constants that RocksDB sets, or that the controller measures from RocksDB at run time:**
  - options: level targets, target file size, max_compaction_bytes, the L0 trigger;
  - entries and bytes per file.

**What it may take as input:** only what the controller can measure from RocksDB at run time (D-30 §2 (a)-(c)):
per-file data, flush contents, job records and counters. Workloads differ only through these measured inputs.

**What it must not contain:**
- any rule, constant, switch or functional form chosen because of a workload's traits: its read/write mix, its key
  distribution, the shape of its hot keys, or which keys its reads go to;
- anything tuned so that one workload's numbers fit;
- as inputs to the frozen law, the workload generator's settings, or per-key counts taken from its stream. These
  stay test-bench devices only, as §10.2 and D-30 (a) already say.

**How a rule is admitted (code first):**
1. Name the RocksDB code path, and write down its step rule as the code does it.
2. Derive the rule's long-run average from it, stating each approximation.
3. Only then check it against measurements.
4. Trace a miss to the approximation that broke, using the code and a measured mechanism test. Never patch it with a
   rule chosen from the data, and never patch it for one workload.

**R4 still holds.** The code gives a step rule. The law is that rule's derived average over a window, not a
step-by-step run of it in the controller.

**What this means for earlier versions:** v3's whole-cell cycle (§10.3) and v3.1's held stretch (§11.2) were each
chosen from measurements, not derived from RocksDB's code. As they stand, neither is admitted under R7.

## 13. Version of 2026-10-26: cases 1 and 2 (K raised, K lowered), law v2 at round 18

*Entered 2026-10-26 at the owner's go ("Yes to 13"). It records the law as it stands after round 18. Sources: the
operator's drafts `~/node_ops/drafts/2026-10-22-propagation-law-math-model-v1-FROZEN.md` (v1) and
`~/node_ops/drafts/2026-10-24-propagation-law-v2-cases12.md` (v2, with rounds 2-18 in its §9), and the test report
`~/node_ops/reports/2026-10-24-case12-test-v2.md`. Written after every result below was seen.*

**Status: design under test, not adopted.**
- §1-§12 are unchanged. §10-§11 (v3, v3.1) model which keys each level holds. This section is a different model: how
  a change of the L0 trigger K moves each level's sizes, flows, merges and reads.
- PATHWAYS is unchanged. The owner (2026-10-26): PATHWAYS gets the law's design once the law succeeds; until then
  the design lives here, and the code may differ from PATHWAYS.
- **Scope:** cases 1 and 2 of D-32's case list only: K raised or lowered, every level multiplier at 1. Cases 3-5 are
  not rebuilt in this form. v1's equations for them stand in the v1 draft (its §4-§5).
- **Not yet tested cleanly.** The rules from round 2 on rest on RocksDB's code or on a measured mechanism (R7), but
  each was written after its misses on the 675 single-change runs had been seen. So the score in §13.10 is "after seeing", not a test. The
  clean test is the 135 multi-change runs (D-32 §2), still sealed; their use as a sealed test is D-32 §7, still open.
- **Records:** D-32 (the K-step batch), D-33 (settled, the change measure, the noise rule, the law's structure and
  purpose), D-34 (inputs), D-35 (the twins of record), D-36 (the law is rolling).

### 13.1 The picture
- Each level is a tank with a mark on it, its target size S_i = B·T^(i−1) (B = L1's target, `max_bytes_for_level_base`).
  One pump moves data down. It always serves the tank furthest over its mark, relative to the mark. L0 counts as
  over its mark once it holds K files.
- **Calm tree:** the pump keeps up at full write speed. Levels sit at their marks, and L0 merges every K_e flushes.
- **Busy tree:** the pump cannot keep up. Scores rise until RocksDB slows writes. Every level that receives data then
  sits at one shared score, the **water level σ\***, above its mark. L1 rides above it, because each L0 merge drops a
  whole batch into L1.
- A change of K moves L0 at once. In a calm tree it reaches L1 and the level below the ones that push data on. In a
  busy tree it moves the water level, so it reaches every level.

### 13.2 RocksDB facts the law is built on
| Fact | Code |
|---|---|
| One merge at a time. `max_background_jobs` = 2 gives 1 flush + 1 merge, and the speed-up path cannot add a merge. Highest score first; a tie goes to the shallower level; a score below 1 never merges. | `GetBGJobLimits`, `db_impl_compaction_flush.cc:3208-3229`; `version_set.cc:4007-4020`; `compaction_picker_level.cc:240-292` |
| Level i's score is bytes_i/S_i. L0's is max(N/K, L0 bytes/B), over the L0 files not being merged. B is the configured option; no multiplier scales it. | `version_set.cc:3846-3853, 3909-3910, 3953-3955` |
| So L0 never waits for more than ⌈B/F_0⌉ files: **K_e = min(K, ⌈B/F_0⌉)** = 5, 9, 17 at B = 64, 128, 256 MiB with flushes of about 15.5 MiB (F_0; 15.55 MiB in the runs read for #1's double check). A larger K changes nothing. A busy tree's L0 score divides by the unrounded **K̂ = min(K, B/F_0)**. | as above (v1 G3, v2 A2') |
| An L0 → L1 merge takes every L0 file present and all of L1. | `compaction_picker.cc:1298` |
| An Li → Li+1 merge (i ≥ 1) takes the file with the least overlap below per byte (`kMinOverlappingRatio`). A file with nothing below moves without a rewrite (a trivial move). | `version_set.cc:4355` |
| A `SetOptions` call acts at once; a running merge finishes first. | `db_impl.cc:1407-1440` |
| **Write throttle.** Writes slow while L0 holds 20 or more files or the pending-merge estimate is 64 GiB or more; they stop at 36 files or 128 GiB. This L0 count includes a running merge's files until that merge installs. The delayed rate starts at most at 8 MiB/s; at each new version it is cut ×0.8 if the estimate did not fall and raised ×1.25 if it fell; near a stop (L0 ≥ 34) it is cut ×0.6 (floor 16 KB/s); ×1.4 on leaving the throttle. | `column_family.cc:865-933, 1010-1045, 1113-1158, 1204-1207`; `version_set.cc:5156-5167` |
| **Pending-merge estimate.** est = [if L0 is due: L0 bytes + L1 bytes] + Σ_{i≥1} E_i·(size_{i+1}/(size_i + E_{i−1}) + 1) over the levels above their targets, with E_i = size_i + E_{i−1} − S_i (E_0 = L0 bytes if due). | `version_set.cc:3656-3740` |

### 13.3 Inputs: the run's own data only
- **D-34:** only what the controller sees at run time, as recent-window values: flush and merge records (sizes,
  flows, overlaps, bytes dropped, merge length), RocksDB's per-level read counters, and the per-file snapshot (key
  range, bytes, entries, sampled reads, smallest and largest sequence number). Never a workload name, a generator
  setting or a constant tuned to a workload.
- **D-36, rolling:** never a native tree. The twin is only the scoring baseline.
  - **Settled checks** predict in reverse: from the step run's own window k (running at K') back to K = 4, from the
    same state at the change. The predicted change (step − twin) is minus that prediction.
  - **Path checks** predict forward from the run's own window before the change (window 0), its per-file snapshot at
    the change, and its own merge records.
  - Every formula takes the K a window runs at as an input.
- **Main symbols.** q: bytes reaching L0 per operation. F_0: bytes per flush; t_F = F_0/q: operations per flush.
  λ: operations per byte a merge reads. f_i: bytes per operation pushed out of level i (f_0 = q). ω_i: bytes read
  from level i+1 per byte pushed out of level i. ξ_i: share of f_i moved by trivial moves. u_j: level j's growth per
  operation. N_b: the files an L0 merge takes. s_i, o_i: bytes per operation level i's merges read from level i and
  from level i+1.

### 13.4 Which state the tree is in
- **Busy** if any non-last level i ≥ 2 holds more than S_i + its calm overshoot + one 4 MiB file. The calm overshoot
  (round 16) is round 13's f_i·λ·(rd_{i−1} + rd_i)·C/2, with C = K_e·t_F at the window's own K and rd_i = s_i + o_i.
  - **Pending-bound** if the estimate of the state reaches 64 GiB: the 64 GiB brake holds the water level.
  - **L0-bound** if not, and the median L0 merge takes 20 or more files: the 20-file throttle holds it.
  - **Neither** (busy, the estimate under 64 GiB, the median L0 merge under 20 files): treated like pending-bound,
    with the estimate held where it is now.
- **Calm** otherwise.
- **Water level:** σ\* = min(N_fire/K̂, σ_pend). N_fire is the files L0 holds when it merges. σ_pend solves
  est(state at σ) = a target, by bisection over at most L terms. The target is 64 GiB in an L0-bound tree, where it
  caps the water level, and the estimate's present value in a pending-bound or "neither" tree, where it holds it.
- **"State at σ":** L1 holds σ·S_1. A level at the water level scales with σ. A level counts as at the water level
  when it holds at least 90% of σ\*·S_i (a tolerance in the spirit of v1's A6). A level below it keeps its present size,
  capped at σ·S_i. The last level holds the rest.
- **Calm → busy after a change** when the merge load at full write speed reaches 1:
  ```
  (G9')  ρ = λ·[ q·(1 + S_1/(K_e F_0)) + Σ_{i≥1} f_i·(1 + ω_i) ] ≥ 1
  ```
  - The bracket is the bytes all merges read per operation. In the scorer, ρ' is the window's measured ρ times the
    bracket at K' over the bracket now. Only L0 → L1's and L1 → L2's terms change in a calm tree.
  - **Busy → calm** when ρ at K', at the marks and at full write speed, falls below 1 (λ from the stretches with
    L0 < 20).

### 13.5 Settled change, calm tree
- **K above the cap** (K ≥ ⌈B/F_0⌉ before and after): nothing changes.
- **L0, L0 → L1, L1 → L2** (v1 K1-K3 with v2's G4'), κ = K_e'/K_e:
  ```
  (G4')  N̄_0 = (K_e − 1)/2 + λq(K_e + S_1/F_0) + ρ_o·τ_res/t_F      mean L0 files
  (K2)   Δw_0 = (q·S_1/F_0)·(1/K_e' − 1/K_e)                         L0 → L1 bytes written per op
  (K3)   ω_1'/ω_1 = (2S_1 + Δ_1)/(2S_1 + κ·Δ_1),   Δ_1 = f_1·K_e·F_0/q
  ```
  ρ_o: the share of operations in which the pump runs another level's merge; τ_res = E[τ²]/(2E[τ]) over those
  merges: the time left on a merge found running.
- **Few flushes per window** (rounds 9-11). L0 fills one file per flush and empties at K_e', so a window holds whole
  merges and L0 holds a whole number of files at its ends. L0's schedule after the change comes from the run's own
  records before it.
  - L0 → L1 bytes written per window: the K' schedule's merges ending in the window minus K = 4's, × (n·F_0 + B)/ops.
  - L0's mean files: the operation-weighted mean of L0's count under K' minus under K = 4.
  - Files per L0 merge come from the schedule. When K is lowered while L0 holds n_x ≥ K_e' files, the first merge
    takes all n_x.
  - The flows out of L1 and deeper get L0's storage term: L0's change in held bytes over the settled span (whole
    files), per operation.
- **The levels that push data on (1..m)** (round 13). Once per L0 cycle (C operations) level i sheds f_i·C bytes, one
  file at a time on the one pump. It rises while the level above drains (L1 at once, when the L0 merge installs) and
  falls while it drains itself. Its mean above its resting point changes with K by
  `Δsize_i = f_i·λ·(rd_{i−1} + rd_i)·(C' − C)/2`, with rd_0 = 0 for L1: L1's rise comes all at once.
- **The level just below them, j = m + 1 (the lag rule; round 7, extended by round 13).** It sees the same stream
  later: `Δsize_j = −Σ_{i=0..m} min(u_j, f_i)·Δsize_i/f_i`, the i = 0 term being L0's ΔN̄_0·F_0. With no overwrites
  (u_j = f_i) this is byte conservation. Levels below j do not change.
- **Flows below L1 do not change** (v1: a copy a bigger batch drops in the L0 → L1 merge would have been dropped in
  L1 anyway), apart from L0's storage term above.

### 13.6 Settled change, busy tree
K → K', K̂' = min(K', B/F_0). The state after: σ\*' = min(N_fire'/K̂', σ_pend').
- **L0's batch.** L0-bound: N_fire' from the hold rule at K'. Pending-bound: N_fire' = σ_pend·K̂' (below 20 files).
- **The hold rule** (round 3). Writes slowed at 20 files run at the delayed-rate cap, and the pump is never idle. Per
  L0 cycle the time L0 holds 20 or more files before it merges is
  `H = [w·r_s·τ_0 + (20 − a)·M·(w − 1/r_f)] / (1 − w·r_s)`.
  N_fire = 20 + the slowed flushes that land within H: the first at δ = (M − min(M, r_f·d_f))/r_s + d_f, then one
  every M/r_s. w: the other merges' pump seconds per key, gaps between merges included. τ_0: the L0 merge's length.
  a: files flushed during it. M: keys per file. r_s, r_f: keys per second written slowed and not. d_f: a flush's
  mean duration in seconds. At K', w and τ_0 scale with the predicted merge reads per operation, and N_fire' is a
  fixed point over whole files (the levels sit at N_fire'/K̂').
  - The formula gives a change, not a level: H' = the window's measured H + [H(state at K') − H(window's own state)],
    so the formula's own bias cancels.
- **L0's mean files** (round 17). Counting gives N̄_0 = (N_b − 1)/2 + y + x + c.
  - N_b = ΣN²/ΣN over the window's L0 merges (a cycle's operations grow with its files).
  - x: the writes held in memtables during an L0 merge, in files. y: the memtable's fill when the merge begins.
    c cancels in the change.
  - **The change used is ΔN̄_0 = (N_fire' − N_fire)/2 + (x' − x).** The N_b spread term changed no verdict and was
    dropped. N_b is measured but does not enter the change.
  - At K', x' = x·g(τ')/g(τ), with τ' = τ·b'/b: a merge reads at the same bytes per second at every K. g is
    logarithmic while writes are slowed (the delayed rate is cut ×0.8 per installed version and nothing lifts it
    during an L0 merge), linear otherwise.
  - The merge's bytes b' take L1 at the water level, o' = o·σ\*'/σ\* (round 17): L0 merges when its score meets L1's,
    so L1 then holds σ\*·S_1. L1's mean size would carry the batch just added.
- **L0 → L1 bytes written per op** (BK3, both states): w_0 = q·S_1/(K̂·F_0) + f_1. L1 holds σ\*·S_1 when L0 merges,
  so the rewrite per flushed byte is S_1/(K̂·F_0).
- **Level sizes, top-down fill** (BK4'). Data only moves down, and score-first serving keeps it as high as the caps
  allow (cap_i = σ\*'·S_i; L1's cap adds its batch sawtooth). From the state at the change, levels above their new
  cap drain to it. Every byte that arrived since, plus what drained, settles in the highest level with room. The
  last level takes the rest.
  - K raised: the water level falls, every level drains toward its mark, and deep levels fill up to the lower water
    level.
  - K lowered: the water level rises. The upper levels keep the inflow, and the deep levels are starved meanwhile.
- **Overlaps** (BK5, as the scorer computes it: v1's G7, ω_i ∝ size_{i+1}/size_i, at the predicted sizes):
  ```
  L1 → L2:  ω_1' = ω_1·(size_2'/size_2)·(σ*S_1 + Δ_1/2)/(σ*'S_1 + Δ_1'/2),
            Δ_1 = f_1·N_fire·F_0/q,   Δ_1' = f_1·N_fire'·F_0/q
  deeper:   ω_i' = ω_i·(size_{i+1}'/size_{i+1})·(size_i/size_i')
  ```
  - Where levels i and i+1 are both at the water level, both sizes scale by σ\*'/σ\*, so ω_i does not change.
  - Where level i is at it and level i+1 is not (still filling, or the last level), ω_i' ≈ ω_i·σ\*/σ\*'.
  - With N_fire unchanged and L2 at the water level, L1 → L2 gives back the v2 draft's BK5.
- **Bytes written** (BK6): w_i = f_i·(ω_i − ξ_i) + f_{i+1}.
- **Flows below L1, the range layer** (rounds 4, 5, 18). A level passes on what no newer copy catches while it is there:
  f_i = q·R(Δ_i), with residence Δ_i = size_i/f_i. R is read from the run's own levels. The busy levels' band widths
  come from v1's R0, with the curve a power law between its points. Round 18: R is fitted on the steady flows
  fn_i = q − the copies dropped above level i, because at the 64 GiB brake the deep levels also drain stored data
  (f_i > q). The predicted flow is f_i + (fn_i' − fn_i): a K change moves only the dropped copies.
- **The level just below the busy levels** (the lag rule; rounds 5, 18).
  `Δsize_j = −Σ_{i=0..m} min(u_j, f_i)·d_i`, with d_0 = ΔN̄_0·F_0/f_0.
  - For i ≥ 1, d_i is the change in residence time, size_i'/f_i' − size_i/f_i (f_i' from the range layer), in a level
    that drops copies: bytes in − out − kept > 2% of the bytes that arrive. Elsewhere d_i = Δsize_i/f_i.
  - Level j passes data down only above its new cap.

### 13.7 The path: from the change to the new state
- **Start:** the new K acts at the end of the running merge.
- **Calm tree** (v1 T-K):
  - if L0 already holds N ≥ K_e' files, it merges at the next merge's end, if its score is the highest;
  - otherwise it merges at the flush that brings it to K_e', (K_e' − N)·t_F operations on;
  - L0 is settled one cycle later, K_e'·t_F; L1's batch changes at the first new merge.
- **Busy tree, the first L0 merge after the change** (rounds 2, 3, 8, 10, 14).
  - L0 merges when its rising score (N_x + t/t_F)/K̂' meets the falling top score of the other levels.
  - The pump drains the highest levels first. The bytes it reads come from RocksDB's least-overlap-first order on the
    run's per-file snapshot at the change: each level drains its cheapest-overlap files first, and data pushed into a
    draining level is pushed again (round 8). This is a formula on the snapshot (sort once, running sums), not a
    replay.
  - The drain's speed is λ per write band, from window 0's other merges (not L0's, not trivial moves) begun in the
    same band: λ_fast below 20 L0 files, λ_slow from 20 to 34 (round 14). A band with no such merges keeps
    round 13's λ.
  - L0 holds at most 34 files: from 34 on, writes all but stop (round 14). Every use of the stop trigger takes 34.
  - The merge takes the first whole number of files at or above the crossing, since L0's score moves only when a
    flush ends (round 10).
- **Busy tree, K lowered, L0-bound:** L0's score is now the highest. It merges at the next merge's end with the files
  it holds. The levels then fill only as fast as data arrives from above, f_{i−1} − f_i per operation, up to their
  new caps (round 4's refill time, below). Whether writes are slowed is the bullet below.
- **Pending-bound:** the water level does not move. Only L0's batch changes, from the next L0 merge on.
- **Does L0 reach 20 files (writes slowed) after the change? (#7)**
  - Calm: yes if G9' gives ρ' ≥ 1 (§13.4).
  - Busy: yes if the settled batch N_fire' reaches 20 files (19.5 in the scorer), as in every L0-bound tree.
  - Either state: also yes if the first merge after the change takes 20 or more files, or (round 17) if it reaches
    20 counting the files that flush in while it runs: `n_1 + a_0·(n_1·F_0 + o_0·L1_X/L1_mean)/(s_0 + o_0) ≥ 20`.
  - n_1: the files the first merge takes; a_0 = τ_0·r_f/M: the files a window-0 L0 merge sees flushed;
  - s_0 + o_0: a window-0 L0 merge's bytes; L1_X/L1_mean: L1 at the change over its window-0 mean.
- **When L0 is settled.** Calm: one cycle after the first new merge. Busy: the levels' refill (K lowered: the bytes
  the levels must add to reach the new caps, poured at the write bytes per operation) or drain (K raised: the bytes
  above the new caps, pushed through every draining level at the slowed λ) (round 4). After a K raise it is also no
  earlier than the batch's relaxation, N_{k+1} − N\* = r·(N_k − N\*) per L0 merge, with r from the one pump's balance and the
  drain bytes from the snapshot at the change; the settle time is the larger of the two (round 17).

### 13.8 Reads
- Under v1's assumption A11, a Get whose key's newest copy has age a ends at the level that holds that age. H(a) is
  the share of Gets whose newest copy is younger than a; h_m is the share ending in the memtable.
- L0 holds ages [0, N(t)·t_F), with N(t) taken as uniform on [0, N_b]. N_b = 2·N̄_0, twice L0's predicted mean from
  §13.5-§13.6 (rounds 10 and 17); v2 §5 wrote K_e (calm) or N_fire (busy). The edge where data leaves L0 is
  A_0 = N̄_0·t_F; level edges are A_i = A_0 + Δ_1 + … + Δ_i.
  ```
  (C1)  L0 probes per Get:   e^f_0 = (1 − h_m)·N_b/2 − η·t_F·N_b²/6,   h_0 = η·t_F·N_b/2   (η: H's slope over L0's ages)
  (C2)  level i ≥ 1:         P_i = 1 − H(A_{i−1})   (each Get reaching level i probes one file there)
  (C3)  change:              Δ(probes per Get) = Δe^f_0 + Σ_{i≥1} ΔP_i
  ```
- Calm: N_b changes, so A_0 moves and every edge with it. Busy, L0-bound: N_b does not change, but every Δ_k scales
  with the water level, so the deeper edges move.
- **Round 12:** a level that pushes nothing in a window keeps every byte that arrives, so its band is size/inflow; a
  level with neither inflow nor outflow has its edge at infinity.
- **Measured only per phase.** The per-level read counters are logged at the phase stamps, not per window. H comes
  from the step run's own whole-phase counters, with edges from its own windows (D-36 rule 5). A single step's
  prediction is the phase-weighted mix (X/40M)·before + (1 − X/40M)·after. H is drawn linearly between its points:
  the weakest part of the read layer.

### 13.9 In the controller now
- **The live cap** (commit 856e049, owner 2026-10-26). The cap claim (K at or above ⌈B/F_0⌉ acts as that count) is
  used as an action mask: in the rules mode and in the decision modes to come, L0's trigger is bounded by
  `LiveK0Cap(C_1, F) = ⌈C_1/F⌉` (`controller/actions.cc`), so the L0 agent is not offered choices that change
  nothing. Hold-only mode keeps the configured bound, so native runs and scheduled changes are unchanged.
- PATHWAYS A-Impl-6 writes K_cap = min(⌊C_1/F⌋, K_slow − 1). The code's first file count whose bytes reach C_1 is
  ⌈C_1/F⌉. PATHWAYS is left as written until the law is adopted (owner, 2026-10-26).
- D-18 (the action bounds) waits until Gate N3 is near; the K ceiling recommended for it is 8, Θ_s's largest trigger,
  on top of the live cap (owner, 2026-10-26).
- The law's purpose is D-33 §3's: each lower level's agent gets the upper levels' actions and the law's forecast of
  their effect on it as context, and makes its own decision.

### 13.10 Test status at round 18 (675 single-change runs, after seeing)
- **The batch** (D-32 §1, §3): W1-W5 × T 2-10 × L1 64, 128, 256 MiB. Each run is its twin (native, K = 4, same tree,
  seed and binary) with K changed once at X = 10M of 40M measured operations, to K' ∈ {2, 3, 5, 8} at 64 MiB,
  {2, 3, 6, 9, 12} at 128 MiB and {2, 3, 8, 12, 16, 19} at 256 MiB. Twins of record: D-35.
- **Change measure and noise** (D-33 §2): Δ = (step in w − step before X) − (twin in w − twin before X); noise limit
  3·√2·√(pooled variance of the three original repeats).
- **A hit:** |mean measured Δ over the settled windows − predicted Δ| ≤ the noise limit + the model's stated error
  (one L0 file, or one 4 MiB file of bytes per level; 10% on times). A check passes if at least 90% of the scored
  runs hit, in every workload separately (owner's rule). An accepted limitation (§13.11) excuses the runs it covers.
- **Results** (hits %, runs scored in brackets; `~/node_ops/diag/kcase/v2/out_r18/summary.md`):

| # | Check | W1 | W2 | W3 | W4 | W5 | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | K above the cap acts as the cap | 100 (5) | 100 (6) | 100 (9) | 67 (3) | 93 (15) | FAIL |
| 2 | mean L0 files | 100 (76) | 100 (69) | 100 (30) | 100 (23) | 100 (96) | PASS |
| 3 | files per L0 → L1 merge | 100 | 100 | 97 | 91 | 98 | PASS |
| 4 | L0 → L1 bytes written per op | 97 | 94 | 100 | 91 | 92 | PASS |
| 5 | L1 → L2 overlap per byte pushed | 72 (lim.) | 97 | 90 | 83 | 96 | FAIL |
| 5b | L1 → L2 bytes written per op | 71 (lim.) | 94 | 100 | 91 | 99 | PASS |
| 6 | L2 and below (bytes, writes, overlap) | 91 | 96 | 53 (63 with lim.) | 35 (65) | 71 (77) | FAIL |
| 7 | writes slowed (L0 reaches 20) or not | 100 (135) | 100 (135) | 100 (135) | 97 (135) | 100 (135) | PASS |
| 8a | first L0 merge after the change: when | 100 | 100 | 94 | 95 | 99 | PASS |
| 8b | first L0 merge after the change: files | 100 | 100 | 96 | 93 | 96 | PASS |
| 9 | L0 settled within one L0 cycle | 99 | 100 | 97 | 91 | 92 | PASS |
| 10 | flows out of L1 and deeper | 91 | 97 | 97 | 78 | 91 | FAIL |
| 11 | probes per Get | 24 (lim.) | 48 (lim.) | 97 | 91 | 99 | PASS |

Rows 2-6 and 9-11 score the settled runs (76, 69, 30, 23, 96 per workload); rows 7, 8a and 8b score every run.
#2s and #3s (the same with no one-file allowance) are reported as information only (owner, 2026-10-25).

- **Count: 9 of 13 pass.** This is by the owner's 90% rule. No PATHWAYS criterion covers the law's accuracy.
- **The four that fail:**
  - **#1 W4:** one pair of three (T4-l164, K8 vs K5). The cap claim itself holds there: both runs' L0 score is the byte
    term at every file count, and they make 89 vs 88 L0 merges at 19.55 files each after X. The miss is D-33's
    window-0 gap (chance timing as the tree first got busy). The owner kept D-33's measure (2026-10-26); the
    after-change-only figure, 100%, is reported as information.
  - **#5 W4:** 83% (4 misses of 23). A perfect law (one that predicts the plain gap after X) scores 87% (20 of 23)
    under D-33, because of window-0 gaps above the limit in T4-l164-K5/K8 and T8-l1128-K3. It cannot reach 90%
    without the twin's window 0, which the law may not use. The fourth miss, T9-l1128-K6, is a window-0 gap plus an
    ordinary law error of 0.052.
  - **#6 W3, W4:** a perfect law's ceiling is W4 19-20 of 23 (needs 21) and W3 20-25 of 30 (needs 27; 28 only under
    the most favourable assumption about which gaps carry on). The blocks are window-0 gaps: in W4 2-5× the limit at
    L2-L4 (T4-l164-K5/K8, T4-l1256-K3); in W3 about 3× at L2 and L4 (T2-l164-K5/K8) and 1.2-1.6× in L2's overlap
    (T8-l164).
  - **#6 W5:** no ceiling was worked out. At round 16, 7 of its misses were window-0 gaps (the T2-l1256 group) and 15
    were law errors (mostly the L2 → L3 overlap, L4's size and L3's writes).
  - **#10 W4:** 78% (5 misses of 23): one law error (T2-l1128-K2: deep flows predicted to rise, measured unchanged;
    round 18's R0NET fixed the other law error, T2-l164-K2), one twin of record (T4-l164-K5), one settle point (T10-l1256-K8), and
    two L0/L1 sawtooth at the window edges (T10-l164-K5/K8).

### 13.11 Accepted limitations (owner's)
Each is a known gap in the law, stated by its mechanism. None is a rule in the law.
1. **Reads under hot-key access** (2026-10-24; W1, W2; #11): the age-band layer (A11) fails when reads concentrate on a
   few recently written keys.
2. **A hot key range** (2026-10-25; W1; #5, #5b, #10's margin): most operations fall in a small part of the key space.
   The L1 → L2 overlap and the flows out of L1 follow how the pick meets the hot range, which D-34's inputs do not
   describe. Not per-key heat: W2 has that too, and the law fits it.
3. **Busy at the change, calm later** (2026-10-25; W5; #6, the last level's size only): the last level keeps a
   different number of old copies (K raised: more, up to +1.55 GiB), and the law predicts no change. No closed form
   reached the precision needed.
4. **The level just below the busy levels, under skewed rewrites** (2026-10-25; W3; #6): it moves by a different share
   of the bytes held above than the lag rule's one share says. Right on average. Its run list no longer matches its
   misses under the rolling law (open, §13.12).
5. **Precision at the 64 GiB brake** (2026-10-25; W4; #6): RocksDB brakes on the estimate at each moment, in which a
   byte in L0 or L1 weighs 4-5 times a deep byte. So the deep busy levels and the last level are predicted to about
   0.03 GiB, against noise limits of 0.01-0.02 GiB.

### 13.12 Open
- **The clean test.** The owner freezes v2; the predictions for the 135 multi-change runs are written to a file, each
  from the run's own state just before each change; then those runs are opened and scored (v2 draft §7).
- **§13.4's test misfiles W5's throttled trees.** Their median L0 merge takes 16-19 files, so they fall under
  "neither", although writes are slowed at 20 files for 32-62% of window 0. Their settled batch at K' is predicted
  too small.
- **#6 W4's law errors** (not window-0 gaps):
  - T2 with K lowered to 2, at the brake: every level 10-20× too big. N_fire/K̂ overstates the deep levels' score.
    Trial SIGD=2, the deep levels' own score, was not adopted.
  - T8-l164-K3: the tree goes from 20-file-bound to the brake, and which throttle sets the water level is not yet
    derived.
  - T10-l1256-K8/K12: the hold rule's files per merge are 1-2 low, so L2's writes are low.
- **The round 17 #7 rule ignores the 64 GiB brake:** W4's four brake runs at T2 are predicted at 24-62 files,
  measured 12-18 (they missed before too).
- **Limitation 4's run list** needs rewording for the rolling law (T8-l1128-K9/K12 flipped sign; T6-l1128-K6 misses
  the same way, unnamed).
- **Per-window read counters:** a fork change, recorded as open in D-36.
- **Not fixed:**
  - #11 W4's two misses. T9-l1128-K6 and #9 W4 T4-l1128-K2: at K = 4 about one memtable fills at full speed before
    the brake trips, and the run's own windows cannot show it. T4-l164-K8: files per merge predicted +0.5, measured
    +4.3; this run is also a #5 W4 window-0 miss.
- **Narrow margins:** two #11 W4 runs clear by under 0.01 probe per Get; the relaxation rule binds in 5 runs; #9 W4
  is 87% if #9's allowance is the measured settled change; #7 is a weak test in busy trees (every W3 and W5 run
  reaches 20 files).
- **Cases 3-5** in this form; v2's busy-state L0 rules also apply to their L0 part.

### 13.13 Code and data of record
- **Scorer:** `~/node_ops/diag/kcase/v2/score_k_v2r18.py`. Its defaults are round 18; each round's switch turned off
  gives the round before (`v2/out_r18_off/` equals `v2/out_r17/` record for record). Helpers: `v2/r0.py` (band
  widths), `v2/drain_order.py` (round 8's drain), `v2/l0_schedule.py` (rounds 9-11). Runs, change measure, noise
  rule and hit rule: `score_k_v1.py`.
- **Output:** `v2/out_r18/` (`summary.md`, `runs.json`).
- **Inputs:** under `~/node_ops/diag/kcase/data/`: `steps/`, `l0_merges/` (the 405 W3-W5 step runs' own per-merge records,
  no twin files), `snap_x/` (per-file snapshots at the change), `l0_events/`, `w0_speeds/`; `twins_rec/` (D-35's
  twins, the scoring baseline) and `twins/` (the three original repeats, the noise limits).
- **Record of every round, trial and diagnosis:** the report `~/node_ops/reports/2026-10-24-case12-test-v2.md` and
  the subagent write-ups `~/node_ops/reports/2026-10-26-sub-{p7,p11,c1,c5,c10,c6}.md`.
