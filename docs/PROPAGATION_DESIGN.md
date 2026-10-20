# Propagation law: model-based design under test (option D)

*Versions: §0-§8, 2026-10-10. §9, 2026-10-15: what step 1b and its follow-ups found, the owner's later decisions,
and the fast formula (record in `docs/PREREGISTRATION.md` D-28).*

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
