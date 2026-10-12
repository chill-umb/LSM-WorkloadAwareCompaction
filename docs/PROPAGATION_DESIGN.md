# Propagation law: model-based design under test (option D)

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
