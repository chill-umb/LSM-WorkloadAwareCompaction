# Open decisions (owner's)

A register of questions waiting for the owner. It is not a plan: `docs/PATHWAYS.md` stays the only forward plan.
A decision, once made, becomes a dated entry in `docs/PREREGISTRATION.md` if it governs theory or runs. Its item
here then moves to "Decided" with the entry's number. Evidence paths are on node B (`~/node_ops/...`); the key
numbers are copied here so this file stands alone. Every result quoted is exploratory (provisional v5 prices,
D-24 §2), not a gate verdict.

## A. Raised by the 2026-10-08 theory checks

Report: `~/node_ops/reports/2026-10-08-theory-checks/README.md`; scripts `~/node_ops/diag/theory_checks/`.

OD-1 to OD-3 were decided on 2026-10-11 (D-26) and moved to "Decided" below.

## B. Carried over (handoff `~/node_ops/handoffs/2026-10-08-0255-handoff.md` §3)

### OD-4 (2026-10-07): the anchors' decay clock
- **Evidence.** Expanded anchors decay toward 1 on the level's own turnover clock (κ_a = 1 turnover; ACT-5). An
  expanded L2 turns over about 10 times more slowly (N_2 ranges 5.7M–191M operations over m_2 = 0.75–2). So
  ACT-5's five turnovers come to about 750M operations, and an expanded level barely decays within a run.
- **Options.** (a) A fixed number of operations. (b) Each level's turnover measured once at n_w and then held
  fixed. (c) L1's clock.
- **Recommendation.** (b).
- **A decision changes.** Plugin only, with no RocksDB rebuild. PATHWAYS A §2, ACT-5 and G-iv, by a dated entry.

### OD-5 (2026-10-08): the comparator rule (statics from open, controllers from n_w)
- **Evidence.** The read θ* (L2 ×2, K0 4) builds its advantage during the load, which a controller starting at n_w
  cannot do. Step tests: switching ×1 → ×2 at n_w gives −0.25% read (no gain); switching ×2 → ×1 loses the gain
  within about 1.4M operations.
  - Against the same-start static (native b32-k8): prior-read-actual −4.87% [CI excludes 0]. It passes both the
    gain test and the stall rule, the first controller to do so.
  - Write verdicts were already fair. Read verdicts against θ* (C7, R0, R0a) were not.
  - Reports: `2026-10-08-0230-step-response-results.md`, `2026-10-08-0405-reverse-step-results.md`,
    `2026-10-08-fair-rescore/`.
- **Options.**
  - (a) The controller acts from open (during the load), with scoring still from n_w.
  - (b) θ* only over statics applied at n_w (needs node time).
  - (c) Keep the rule and report the same-start comparison beside it.
- **Recommendation.** (a), with (c) meanwhile.
- **A decision changes.** PATHWAYS A8, H §5 and C §1, by a dated entry.

### OD-6 (2026-10-07): the corrected propagation law
- **Evidence.** `~/node_ops/reports/2026-10-07-1710-propagation-law-recheck.md`. The proofs are algebraically
  correct, but:
  - F1: the level clock N_j is set by the action itself.
  - F2: the own-clock horizon discounts effects that land in the level below.
  - F3: "the same problem on its own clock" fails structurally (b32, T = 10: one interior level).
  - F4: the admission gate needs scarce data.
- **Status.** Waiting for the owner's go on the report's §5 directions.
- **Step 1 done (2026-10-08, owner's go):** the admission test is passable with enough data, but needs
  roughly 300-1,000 turnovers per level (Gate N1 had 237 for L2, 25 per run for L3). The blocking check is the
  90th percentile of rho_tilde (margin 0.05; interval about +/-0.08 at n = 100). Report
  `~/node_ops/reports/2026-10-08-propagation-nmin/README.md`. Next, if the owner agrees: a uniform-key, write-heavy
  T = 2 testbed sized so the deepest candidate level gets >= ~1,000 turnovers pooled (a dated entry first).
- **Owner, 2026-10-10.** The law stays in Programme 1 until every avenue is exhausted; PATHWAYS is amended only
  after data proves a theory, and for this law only once it works at every T, both workloads and all run sizes
  (no testbed chosen to make it pass, so the uniform-key testbed above is dropped). Chosen route: D, model-based
  propagation, with three decisions accepted for testing (purpose, clock, form). Design and proof plan:
  `docs/PROPAGATION_DESIGN.md`. Step 1 (offline) has the owner's go; its margins (§6.1) await confirmation.
  Evidence: `~/node_ops/reports/2026-10-10-deep-channels/README.md`. Fallbacks B, C, E stay on record (§7).
- **2026-10-10/11.** Step 1b ran offline: M1 ("levels as age bands") fails 1b(i), 237 of 614 checks inside the
  margins (`~/node_ops/reports/2026-10-10-prop-step1b/README.md`); stopped by the early-stop rule. The owner rules
  out a simulator in the controller (decision latency). The owner first resolves the cost model's dependencies
  (OD-1 to OD-3, D-26). Proposed next, awaiting the owner's go: an offline study of RocksDB's own LOG files (how mixed
  each level's ages are), then a fast-formula model (M2c+) as a new dated version of the design note.

### OD-7: records not yet written
- Theory for the prior's hidden-step guess (D-23, H §7).
- The "actual" charge in PATHWAYS H §3/§7, with a dated entry.
- A dated entry for "v5 accepted on node B".
- The R0, R0a, L2-map and step-test designs, which are only in ops reports.
- D-23 §7 items A–E stay pending (D-24 §1).

### OD-8: housekeeping
- Done 2026-10-10:
  - 802d31b..d9b4ea6 pushed as branch `dqn-actual-charge`; `dqn-poc-new` keeps copies of the two step-test
    commits without the actual charge (`docs/PRIOR_LEARNER_INVESTIGATION_2026-10-10.md` §1.1).
  - All four worktrees removed: `~/lsm-actual-charge`, `~/lsm-poc`, `~/lsm-step-apply`, `~/lsm-rocksdb-wt`.
  - The PATHWAYS fixes and this file committed (6ca2836).
  - Figures and the LaTeX build script are kept outside the repo, in `~/figures` and `~/latex` (owner).
- Open: delete the leftover branches `actual-charge`, `step-apply`, `poc-thursday` and, in the submodule,
  `interim-instruments`; each is contained in another branch.

### OD-9 (2026-10-11): controller code that still uses D.11's closed form
- **Evidence.** D-26 §4 (OD-3, option (b)): D.11's $K_0^\star$ is no longer used to choose or predict the
  trigger. The rules arm's `k0_tracking` (`controller/rules.h`) tracks D.11's best admissible trigger, and the
  prior's L0 term (`controller/prior.h`) uses D.11's cost per operation.
- **Options.** (a) Replace them with the measured best static trigger from $\Theta_s$ (Gate N2) before Gate N3.
  (b) Keep them and report that their trigger choice rests on a model that fails (a) and (e).
- **Recommendation.** (a), after Gate N2 measures the triggers. Not before Gate N2: Gate N2 runs no controller arm,
  and a plugin change moves the preflight marker.
- **A decision changes.** The plugin and its tests, and PATHWAYS A §4 lever (e) and the rules arm's specification,
  by a dated entry.

## Decided

### OD-1 (2026-10-08; decided 2026-10-11, D-26: option (a)): A10's additivity has never been tested
- **Evidence.** The calibration arm meant to run two compactions at once (`nb2j`, node A 2026-10-05 and node B
  2026-10-06) raised the mean number of running jobs only from 1.00 to 1.07–1.08. The slowdowns rose by about
  the same ratio (1.06–1.13). That is consistent with additivity, but the test has no power.
- **Options.** (a) A calibration arm whose jobs really overlap, for example two neighbour writers, or a higher
  trigger with more background threads (node time). (b) Keep additivity as an untested assumption and say so.
- **Recommendation.** (a).
- **A decision changes.** The calibration design (`calib_v2.sh`, D-23 §4(a)), by a dated entry. PATHWAYS changes
  only if the test fails.

### OD-2 (2026-10-08; decided 2026-10-11, D-26: option (a)): one interference coefficient for every job kind, and the Put slowdown
- **Evidence.** v5's κ is busy-only, one value per step type copied to all four job kinds. Five step types borrow
  another type's value.
  - Linearity on the rate sweep: block reads pass on both nodes; probes and scan steps pass on one node each.
    Seeks fail on both: the slowdown levels off at high load.
  - Puts fail on both nodes: +25–30% at any load, not proportional to the number of running jobs.
  - A per-kind fit puts most of the seek slowdown on flushes and L0 merges and little on deeper merges (node B:
    1.43 ± 0.41 against −0.04 ± 0.07). The kinds are confounded across arms, so the errors are large.
- **Options.** (a) Calibration arms that separate the job kinds; a κ per kind; a separate model of the Put
  slowdown. (b) Keep the shared busy-only κ and report the bias. Option (b) conflicts with the owner's rule that
  every real cost is priced.
- **Recommendation.** (a).
- **A decision changes.** A10's form (PATHWAYS §1.2) and D §1's charge, plus the calibration, by a dated entry.
  D.11 and A.2(iv) use kind-specific κ (κ_{x,0}, κ_{x,F}, κ_{x,d}), which v5 cannot supply today.

### OD-3 (2026-10-08; decided 2026-10-11, D-26: option (b)): D.11 leaves out K's effect on the levels below L0
- **Evidence** (screen `db/explore-n2-assoc`, bases 8/16/32 MiB, K0 2/4/8, five seeds).
  - What D.11 gets right:
    - (b2) holds in all 52 native runs: every L0 merge starts at exactly K files and no flush ends during one.
    - The cycle formula reproduces the mean L0 count to ±0.01 file.
    - Probes and seeks change per L0 file as modelled.
  - (b3) fails at K = 8: 21–47% of deeper merges run with one L0 file. Its cost effect is under 1%.
  - (a) and (e) fail. L0 merges drop 1–2% of their input at K = 2 and 6–8% at K = 8, against none assumed.
    Deeper work per operation moves 25–33% with K, and scan hidden steps change beyond L0's share.
  - D.11 picks the measured best K in 6 of 9 (base, mode) cases. Its predicted cost differences are 2–5× off,
    with the wrong sign twice.
- **Options.**
  - (a) Extend D.11 with L0's drop fraction ρ_0(K) and its downstream effect.
  - (b) Treat K0 as an empirical static choice, which Θ_s already measures, and keep D.11 only for L0's
    mechanics. Claims that rest on K0* would be re-scoped: A.8's floor, D.12's prediction, and B.4's dependency.
  - (c) Both.
- **Recommendation.** (b) now; (a) as theory work if the paper needs a law for K0.
- **A decision changes.** PATHWAYS D.11 (a)/(e), A.8, D.12 and B.4, by a dated entry.
- **Status (2026-10-11).** OD-1 and OD-2: one calibration session on node B before Gate N2
  (`~/node_ops/diag/od12/calib_od12.sh`, output `~/node_ops/calib_v2/<date>-od12`); tests and rules in D-26 §2–§3;
  results pending. OD-3: PATHWAYS D.11, A.8, D.12 and B.4 re-scoped the same day (each has a "Scope, 2026-10-11"
  note); the controller code that uses D.11 is OD-9.
