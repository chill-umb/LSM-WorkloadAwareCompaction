# Audit, 2026-09-23: why D-12 failed, and whether Pathway A can fix it

Eight read-only audits covered the D-12 arms, the code, the static capacity arms and `docs/PATHWAYS.md`. Their full findings and scripts are in the session scratchpad (`audit/01-*.md` … `08-*.md`). The key numbers below were re-checked directly.

## Bottom line

1. **Most of the D-12 failure is not the learner learning something bad.**
   - At T=2, the whole extra write cost comes from the first 30 s after the controller wakes up. In that window the untrained learner defers due levels at random while the bulk-load backlog drains.
   - The T=2 twins were also run three days earlier, and at T=2 the same setting drifts by 3–4% in write cost between sessions.
2. **The objective has almost no room at the chosen comparators.** At T=2 and T=10 the comparator is already the lowest read cost that any static setting reaches within +2% write. At T=10 it is the lowest read cost of all 38 static settings. The learner's allowed actions contain no move that lowers reads at trigger 2.
3. **Pathway A would not fix this.** It prevents depth growth, but D-12 had none. It makes deferral free of guard forcing, but deferral saves nothing on this workload. Its one real read lever is removing a level, and that is a static effect the frontier gets too.
4. **Several instruments still carry the post-load transient.** Examples: the write and scan multipliers, the guard's limits and E-5. PATHWAYS is out of date in many places (list below).

## 1. What failed, and why

**Class** means where the failure comes from:
- **exploration:** random early actions;
- **drift:** a difference between sessions;
- **lever:** no useful action exists;
- **instrument:** the measurement is wrong;
- **algorithm:** a learning defect.

| Failure | Cause | Class |
|---|---|---|
| T=2 write +6.6%, reads +3.0% | All of the extra writing, +0.68 GB per run, falls in the first 30 s. After that the learner writes 0.07 GB *less* than native. During those 30 s the learner has not trained yet (first gradient step at 38–42 s). Its random exploration deferred 43–45% of due deep levels, each time against its own greedy choice. Native RocksDB drains the backlog with free file moves; the deferrals turned those into real merges of load data with load data, which drop nothing. The same setting also measures +2.9% to +4.1% write and about +2.5% reads in later sessions than on the day the twins ran. | exploration + drift |
| T=6 write +9.7%, reads −7.1%, huge spread | The only lever used is compacting L0 early. Around 25–45 s the learner commits to "always" or "never" almost by chance (300 / 123 / 101 early compactions per seed), then stops exploring. Its exchange rate, 0.73% reads per 1% writes, equals the static frontier's own slope (0.71–1.04). It slides along the frontier instead of beating it. | algorithm (lock-in) + lever |
| T=10 native, no read gain | The comparator already has the lowest read cost of any static setting. At trigger 2 the action mask forbids every early compaction, so the only non-native move is deferring due work, which cannot lower reads. | lever |
| No read gain anywhere | The read objective is at most 0.1% of the reward's variation; the write term carries 82–99.9%. A real read gain could not be detected in one run anyway: it needs about 10⁵ decisions, and a run gives 250–790 useful ones. | instrument + budget |
| λ_W rises all run (D-3, D-4) | The post-load backlog holds run-to-date write near 13–20 against a bound of about 8. Measured from the warm-up instead, λ_W would end at 0 at T=2. A native policy replayed at T=6 also looks "monotone". | instrument |
| λ_scan binds at T=10 (prediction 4) | Same transient: it would end at 0. | instrument |
| λ_lat > 5 on unguarded arms (predictions 2, 13) | Real: scans skip more obsolete versions. The sorted-run-seeks objective cannot see this. | policy effect the objective misses |
| E-5, 6–17× over 1% | The guard's pressure and due-age clocks carry the load's history into the first 30 s. After 30 s E-5 is 0–1.4%. | instrument (transient) |
| T=2 write p99 +9.7% | The twin alone moves +6.5% between sessions. The policy's share is about 3% at most. | drift |
| D-1 at T=10 | Cold-start residual spike divided by a small late scale. | algorithm + instrument |
| D-5 flip rate | Stage 11 counts stale diagnostic values; the true T=2 L0 rate is about 0.01, not 0.51. | instrument bug |
| (found) | The Double-DQN target ignores the next-state action mask (`agent.py` `train_step`). Effect not measured. | algorithm bug |

Two statements in the D-12 verdict recorded today are wrong and are corrected in `docs/PREREGISTRATION.md`:
- "The learner holds deep levels past due as a learned lever" is wrong. The deep holds were random exploration during the backlog; after 30 s the learner releases every level at φ = 1.00, like native.
- "Overwrites are absorbed higher up, so held levels drop nothing" is wrong as the explanation. Those merges combined unique bulk-load keys with each other, so nothing could be dropped.

## 2. Is there room to win at all?

The objective is: lower read cost R, with write cost W no more than 2% above the comparator. Taking the best static settings from the Hull₀ data:

| Cell | Comparator (W, R) | Best R any static setting reaches within +2% W | What that means |
|---|---|---|---|
| T=2 | 7.92, 4.66 | the comparator itself (0%). Across ratios, a T=6 setting reaches −3.4% | no same-ratio room |
| T=6 | 8.48, 4.26 | −1.7%, and at 4% lower W | the comparator is itself beaten by a static setting |
| T=10 | 13.45, 3.03 | the comparator itself; lowest R of all 38 settings | no room at any W |

Stage 06 picks the fastest setting that meets the space bound. On this read-heavy workload that turns out to be the lowest-read corner of the frontier, so "improve reads at write parity" has nowhere to go. No learned or hand-written policy on record lies below the static frontier. The only exception is one unguarded T=6 run.

## 3. Would Pathway A fix it?

Your understanding is that Pathway A "prevents harmful depth growth and allows deferral". Both halves are true of the mechanism. Neither addresses these failures.

| Claim | True? | Does it help here? |
|---|---|---|
| Prevents depth growth | Yes. A level that is not due releases no burst (Theorem A.1, after small fixes) | **No.** Depth equalled the twin's in all 18 D-12 arms. There is nothing to prevent. |
| Allows deferral | Yes. An expanded level is not due, so the guard does not force it | **No, and likely harmful.** Deferral saved no write in D-12. A held level is physically bigger, and expansion only changes its score, so the eventual merge costs the same (model 0.79 GB, measured 0.775 GB). Expansion also hides the level from the guard. |
| Helps L0 | — | **No.** L0 is excluded by design (s₀ = 1). The T=2 read cost and the whole T=6 write cost are at L0. |

**What the 27 static capacity runs show** (trigger 4, measured phase, against s = 1.0):

| T | s = 1.5: W / R | s = 2.0: W / R | Depth |
|---|---|---|---|
| 2 | +0.5% / −6.3% | +4.0% / −12.9% | 9 → 8 at s = 2 |
| 6 | +8.4% / +3.1% | +30% / +1.9% | 5 |
| 10 | +5.2% / −3.4% | +2.4% / −6.2% | 5 → 4 at s = 2 |

- **Expansion never lowers write cost.** Merges out of L0 grow by 34–74%, because L1 is bigger.
- **It lowers read cost only by removing a level**, which is the same trade that changing the base size already offers.
- **Uniform s = 2 is the existing static point "base 32 MiB" measured again.** No capacity run beats the static frontier or meets the objective.
- **These gains are static**, so by Corollary C.3 they raise the bar (Hull_s) as much as they help the learner.

**The one untested idea.** Keep the upper levels at 1.5–2× so the last level empties. That removes one probe from cold reads at nearly the same write cost. It has only been measured at trigger 4, not at the trigger-2 comparators. It can be tested statically first, by expanding only L7 at T=2 and trigger 2 (3 runs plus 3 same-session twins, about 20 minutes). If it works statically, it belongs to Hull_s; a learner would still have to beat it.

**Could Pathway A be built?** Yes. Most of the C++ already exists: the scale vector, the scaled score and debt, the release logs, L0 pinned. But `SetCapacityScales` has **no callers**. The missing pieces:
- a three-action protocol and model;
- a decay rule;
- the guard learning to contract a level (without it, the guard is blind to an expanded level);
- contraction at drain (otherwise debt goes unpaid and W is wrong);
- a prior and mask for the new action;
- a rebuild, which voids the static frontier, the manifests and the parity gate. That is about 60 node-hours of re-measurement plus roughly 1,200–1,800 lines of code.

The theory also needs fixes:
- The burst size in A.1 is (κ−1)C, not κC.
- A.2's "optimal profile" requires *shrinking* levels, which s ≥ 1 cannot do. With s ≥ 1, the uniform profile is already write-optimal.
- A.3's premise fails: s = 2 removed a level at T=10 too.
- s_max = 2.0 is only the edge of the tested grid.

## 4. PATHWAYS audit — main problems

1. **Stale workload and numbers.** Gate 3b still says "uniform workload". The B.1 and B.2 tables are from the uniform workload with the old space measure; on `Assoc` the B.1 ceilings are 58 / 35 / 35%. "T=2 closed at any budget" and "per-level scaling keeps depth" are both contradicted by data. The costs are 5–7× too high.
2. **D-11 and D-12 not reflected.**
   - C-2 at T=6 now fails.
   - The L0 band costs +6 to +14% write, not +2%.
   - The guard section still quotes 3.8–6.0% and "E-1 must pass or the guard is cut", although E-5 replaced E-1 as the decider and E-5 has failed on every learned arm without any ruling.
3. **Criteria that cannot say what they claim.**
   - D-4 ("λ_W diverges where infeasible") can never apply, because native RocksDB is always feasible.
   - D-3 reads the transient.
   - D-2 passed while TD loss rose.
   - D-5 compares rewards from different reward definitions.
4. **Too few repeats.** At T=2, a 2% write margin needs about 20 paired repeats, not 10. Latency at T=2 needs 55–100, plus same-session twins, because drift cannot be averaged away.
5. **Pathway D text lags the code.** The reward uses signed flows, not hinges. The multipliers are fast, not "10–100× slower". There is no target sweep and no reward normalisation. The stall term is dead.
6. **Acceptance tracks.** The research track is effectively out of reach with the trigger alone. The E&A track is blocked by C-2 and the unbuilt B3. The "neither reachable" clause needs rewriting around D-12's clean negative result.

## 5. What this means, and what to do next

- **The honest reading.** On `Assoc` at 10M, a trigger-only controller has no lever that beats the tuned static frontier on reads at write parity. D-12's apparent losses are mostly startup exploration and session drift. Pathway A does not change that conclusion; it adds a static depth lever the comparator can also use.
- **Do not start Pathway A yet.** It costs a rebuild and about 60 node-hours, and the evidence says it cannot move the result.
- **Next immediate tasks**, cheapest first:
  1. Commit everything. D-10 to D-12, the verdict and this audit are all still uncommitted.
  2. Decide the paper's claim. The evidence supports an Experiments & Analysis-style negative result: why learned compaction triggers cannot beat a tuned static frontier on a skewed workload. The reasons are no garbage left to remove at deep levels, a comparator already at the low-read corner, and exploration cost during the post-load transient.
  3. If you want one more experiment first, run two cheap confirmations in one session. Each has same-session twins, about 1 hour in total.
     - (a) T=2 `rl` with exploration held off until the backlog drains, which tests "near-native once exploration is fixed".
     - (b) Static L7-only expansion at trigger 2, which tests the one untested depth lever.
  4. Revise PATHWAYS as listed in §4. Record the guard ruling and the repeat counts in `docs/PREREGISTRATION.md`.

## 6. Follow-up diagnosis (2026-09-23, after the report)

**The controller's action sampling is flawed, but fixing it would not create a win.** Exploration is Boltzmann sampling with a temperature that falls from 1.0 to 0.05 over about 100 s. Three problems:
- It is most random during the first 30 s, while the post-load backlog drains and before any training.
- The prior's +0.2 preference for compacting a due deep level gives a 45% chance of deferring, which is nearly a coin flip.
- After training, the learned scores reach tens to hundreds, so choices freeze. That is the T=6 lock-in.

Fixing this would most likely turn the T=2 loss into a tie, because §2 shows no room to win at the trigger-2 comparators.

**Staleness of controller answers is real, detected and bounded.** Every answer is checked against a fingerprint of every file in the tree (`SnapshotEpoch`, `AcceptResponseStructure` in `compaction_picker_rl.cc`). Any change since the snapshot rejects the answer and keeps the previous one in force. Measured on D-12 repeat 1, steady part of the run:

| T | tree changes per second | answers rejected | implied snapshot-to-answer window | decision to compaction start, median / p90 / p99 |
|---|---:|---:|---:|---|
| 2 | 85 | 23% | 3.0 ms | 1.8 / 21 / 765 ms |
| 6 | 68 | 19% | 3.1 ms | — |
| 10 | 92 | 33% | 4.3 ms | 3.0 / 45 / 1,312 ms |

Over the whole run, 27–39% of answers are rejected. File choice is always current because RocksDB picks files at run time. The prior-only arm, which takes the same round trip, matched native RocksDB at T=10, so staleness did not cause the D-12 failures. Its costs are lost control, blurred credit, and a check that is too strict, since a flush at L0 invalidates a decision about L5.

**Architecture options considered (no decision taken).**
- **Recommended direction:** a slow tuner above native RocksDB. Every few seconds, or at a workload shift, it sets static knobs (L0 trigger, level base, capacity) through `SetOptions`, using Bayesian optimisation or a bandit started from the cost model. This is PATHWAYS F's "layer above". It needs a phased workload (B2) and, first, a learner-free measurement of whether the best setting per phase beats the best single setting.
- **Alternatives:** model-based planning with the cost model (same "no room" ceiling on a steady workload); fixing the current DQN (ceiling is a tie at T=2/T=10); offline pretraining (breaks the cold-start rule).
- **A full C++ port of the learner is not recommended.** It would remove most staleness: in-process inference takes about 0.1 ms, an estimate. But it fixes none of the audit's causes. It would also make every learner change a new `db_bench` binary, and the static frontier is bound to the binary, so each change would need about 48 h of re-measurement.
- **Cheaper ways to get the staleness benefit:** train in Python and run inference in C++ on weights sent about once a second; a per-level staleness check; or ship the controller as a separately fingerprinted plugin library.
