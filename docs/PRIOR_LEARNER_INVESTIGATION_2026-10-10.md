# Prior and learner investigation, 2026-10-10 (shelved)

**Status.** Shelved by the owner on 2026-10-10, to open a separate discussion on the propagation law (OD-6 in
`docs/OPEN_DECISIONS.md`). **Nothing here is adopted.** PATHWAYS, PREREGISTRATION and the contract are
unchanged. The theory amendments in §4 are proposals that need the owner's go, and several of them depend on how
OD-6 is settled (§6).

Node B, Programme 1 exploratory track (PREREGISTRATION D-24 §2). Every run here is a diagnostic; no PATHWAYS
gate covers it, so each result is judged against predictions written before the run.

Words used below:
- **prior**: the hand-written price formula b (PATHWAYS H §7) that gives each action a starting cost before the
  learner has learned anything; Q = −b + f, where f is the learned correction.
- **turnover**: the number of operations it takes one level's worth of new data to flow into the level (L1 at
  b32-k8: about 257k operations).
- **hidden steps**: the extra steps a scan takes over old versions of keys that newer versions hide.
- **reward units**: cost divided by the cost of writing one full L1 (c_w C_1).

## 1. Decisions taken

| Date | Decision (owner) | Record |
| --- | --- | --- |
| 2026-10-10 | Trace why the prior over-prices "expand", and find the hidden issues of the learner and the prior | §2.1; `~/node_ops/reports/2026-10-10-expand-trace/` |
| 2026-10-10 | Run the L1 step test, then the L1 reverse step, then the L1 x1.5 static (3 runs each) | §2.2; reports below |
| 2026-10-10 | Branch split, "option B": the actual charge moves to its own branch, `dqn-actual-charge`; `dqn-poc-new` goes back to R0's code and keeps the step-test tools | §1.1 |
| 2026-10-10 | The owner pushes `dqn-actual-charge`; Claude only created it | — |
| 2026-10-10 | Shelve this discussion; next, a discussion on the propagation law | this file |

### 1.1 The branch split (done 2026-10-10)

| Branch | Commit | What it holds |
| --- | --- | --- |
| `dqn-poc-new` | `3343a5a` | `e49da06` (the code R0 ran on, identical to `origin/dqn-poc-new`) + `d09a4f5` and `3343a5a`, copies of `7d12f4f` (hold arm: one scheduled change at operation X) and `d9b4ea6` (hold arm: start multipliers) **without** the actual charge |
| `dqn-actual-charge` | `d9b4ea6` | the originals: `802d31b` (the actual charge), `7d12f4f`, `d9b4ea6` |

- **Checks.** The difference between the two branches is exactly the actual-charge commit (`802d31b`'s 32 files).
  On `dqn-poc-new`: controller tests 128/128, rl_agent 34/34, pipeline 421/421. Preflight: see §1.2.
- **Never delete `dqn-actual-charge`.** Every run that recorded `802d31b`, `7d12f4f` or `d9b4ea6` in its
  `git_revision.txt` can be rebuilt only from it: R0a, the L2 step, the L2 reverse step and the three L1 tests
  of §2.2. R0a holds the first run that passed against native (prior-read-actual, read −4.87% vs native b32-k8,
  gain and stall rule both met; `~/node_ops/handoffs/2026-10-08-0255-handoff.md` §1.3 item 6).
- **What changes in use.** On `dqn-poc-new` the learner arms have only the counterfactual charge of PATHWAYS H §3
  (no `charge` key, no `LEARNER_CHARGE`). Any actual-charge run must be built and preflighted from
  `dqn-actual-charge`. The hold-arm tools (`HOLD_APPLY_*`, `HOLD_START_M`) and the step-test chains in
  `~/node_ops/diag/` work on both branches.
- **Worktrees removed** (owner, 2026-10-10): `~/lsm-actual-charge`, `~/lsm-poc`, `~/lsm-step-apply` and the
  RocksDB submodule's `~/lsm-rocksdb-wt`. All four were clean. Their branches stay, and each is contained in
  another branch:
  - `actual-charge`, `step-apply` and `poc-thursday` are in `dqn-actual-charge`; `poc-thursday` is also in
    `dqn-poc-new`.
  - `interim-instruments` (submodule) is in the pushed `rl-compaction-policy-new`.

  They can be deleted with `git branch -d` on the owner's word.
- `dqn-poc-new` is 2 commits ahead of `origin/dqn-poc-new`; not pushed.

### 1.2 Preflight after the split
**Passed, all six steps, on `dqn-poc-new` at `3343a5a`** (2026-10-10 18:39 UTC; marker
`build-dbbench/PREFLIGHT_PASSED`).
- `db_bench` identity unchanged: `3167c645…`. The plugin was rebuilt: `bd2a9be2…`.
- First try, with 10 parity pairs (`~/branchsplit_preflight.log`): steps 1–3 passed, but ACT-4 (stock RocksDB
  against the patched `db_bench` at multipliers 1) was undecided. In pair 6 the **stock** run's max score was
  4.82, against about 7.7 in every other run. ACT-4 involves neither the plugin nor any changed file, so this was
  noise.
- Retry with 20 pairs (`~/branchsplit_preflight2.log`), the step chains' rule for an undecided ACT-4: all steps
  passed.
- Long runs from `dqn-poc-new` are allowed again: static and hold arms (steps 1–4), rules (1–5), learner arms
  with the counterfactual charge (1–6).
- An actual-charge run needs its own build and preflight from `dqn-actual-charge`.

## 2. What was measured

### 2.1 Expand trace (offline, R0a data)
Report `~/node_ops/reports/2026-10-10-expand-trace/README.md`. Verdict: **bad** for the prior's expand price.

- The prior's L1 expand price, rebuilt from the logs, matches `prior.cc` exactly. Its median before the cap is
  17.9 reward units (prior-read-actual), and 86% of decisions sit at the cap b_max = 10.
- A random, short-lived expand was measured at −1.6 ± 1.8 against the prior's +9.6.
- Five causes:
  1. **Held bytes.** The prior assumes L1 holds all its new room for a whole turnover. Measured: 0.04–0.11 C_1;
     a fill-up model gives 0.10–0.16. The prior's figure is 5–19 times too high.
  2. **The hidden-step term divides by L1's fill right now**, while the hidden-step rate is an average over
     the last interval. Just after L1 drains, the fill is tiny and the term blows up (mean 191, median 8.7).
  3. **The L0→L1 merge term (P1, actual charge only)** assumes a fixed number of L0 merges. In fact L0 answers
     a bigger L1 with fewer, larger merges (−0.4 to −0.6 per turnover).
  4. **A missing credit.** A bigger L1 makes its own merges into L2 cheaper (−1.4 to −3.2 reward units).
  5. **The cap hides the size of the error.**
- **Learner** (same runs):
  - Each 5 s weights push flips the greedy action in 13–35% of fixed states, and moves the learned gaps by
    1.5–1.9 units, as large as the prior's real gaps at L1 (0.65 compact, 2.5 defer).
  - The share of states where the network overrules the prior swings between 7% and 79%.
  - Every output learns the same baseline (about −300) separately.
  - Training steps per own transition: about 12 at L1 and about 6 at L0; nothing documents which was meant.
  - The second value head (V_own) is trained but unused under the actual charge.
- **Inputs:**
  - About half of L1's 61 inputs carry almost nothing: about 16 are constant, about 12 barely move, and 4 sit
    at the ±10 clip.
  - L0's three trivial-move inputs (c_tm, tm_rd, tm_wr) are always empty, because L0 never makes a trivial
    move.

### 2.2 The three L1 tests (node B, 3 seeds each, hold-only, b32-k8, T=10, Assoc 58M)

| Test | What it does | Results | Report |
| --- | --- | --- | --- |
| L1 step | normal load; L1 → x1.5 at op 2.9M, kept | `db/explore-r0-nodeB/step-l1x15/` | `~/node_ops/reports/2026-10-10-l1-step/` |
| L1 reverse step | load with L1 at x1.5; everything → x1 at op 2.9M | `db/explore-r0-nodeB/step-rev-l1x15to1/` | `~/node_ops/reports/2026-10-10-l1-revstep/` |
| L1 x1.5 static | load with L1 at x1.5 and keep it | `db/explore-r0-nodeB/static-l1x15/` | `~/node_ops/reports/2026-10-10-l1-static15/` |

Cost against native b32-k8 (J, whole measured phase, paired by seed; read priority / write priority):

| | measured with L1 at x1 | measured with L1 at x1.5 |
| --- | --- | --- |
| **loaded with L1 at x1** | native | step: **+1.2% / +2.1%** |
| **loaded with L1 at x1.5** | reverse step: **−1.2% / +0.7%** | static: **−0.4% / +3.7%** |

**Findings.**
- **F1.** L1 fills only as fast as new data arrives, in jumps, one per L0→L1 merge. It reaches x1.5 in about half
  a turnover and then sits exactly at its target. L2, by contrast, stopped at 70% in the L2 step test.
- **F2.** L0→L1 merges happen exactly as often (569 vs 569), but each overlaps 50% more data.
- **F3.** L1's own merges into L2 get cheaper: overlap per byte moved −15% (the 1/size rule predicts −33%), bytes
  written −21%.
- **F4.** A bigger L1 costs more lookup work: filter checks +3%, seeks +9%, file reopens +1–5%. That is about +3.0
  reward units per turnover at read priority, and the prior has no term for it.
- **F5.** Hidden steps rise about 20% just after L1 grows, in every seed. Over the long run the change is near
  zero and differs by seed.
- **F6.** Shrinking L1 is fast and free: it drains in under 25k operations, with no extra writes.
- **F7.** The load leaves a lasting mark. Loading with L1 at x1.5 leaves L2 and L3 with fewer old versions for the
  whole 55M-operation phase:
  - 9% fewer hidden steps per scan;
  - 50–75% more L2→L3 merge bytes;
  - read J 1.2–1.6% lower, write J 0.7–1.6% higher.

  The static test shows the load causes it, not the shrink. Keeping x1.5 during the measured phase does not
  build it.
- **F8.** Keeping L1 at x1.5 during the run costs about read +0.8 to +1.2% and write +2.1 to +3.0%, from either
  load. The prior is right not to want a kept expand, but it gets there through wrong parts (F1, F3, F4, F5).

**Verdicts on the predictions written before each run.**

| Test | Prediction | Verdict |
| --- | --- | --- |
| Step | P1 fills at the inflow rate | confirmed, in jumps |
| | P2 stays at x1.5 | confirmed |
| | P3 hidden steps grow with L1's size | not confirmed (+15% for +49% bytes, seed-dependent) |
| | P4 same L0→L1 merge count, bigger overlap | confirmed exactly |
| | P5 L1→L2 merges cheaper (−33%) | half the size (−15%) |
| Reverse | R1 drains within 50k ops | confirmed (25k) |
| | R2 one-off release cost | not confirmed: about free |
| | R3 no lasting memory | not confirmed |
| | R4 hidden steps back to native within a turnover | first half of the run yes, second half no |
| | R5 cost over time | not confirmed |
| Static | S1 L1 stays at x1.5 | confirmed |
| | S2 the load builds the deep state | confirmed (L3 hidden: load effect −16.7, measured effect −3.6) |
| | S3 the effects add up | reads yes; writes 0.9 points worse than adding up |

### 2.3 Related results from earlier sessions
- **L0 lock-in (2026-10-10,** `~/node_ops/reports/2026-10-10-pref-why/`, `2026-10-10-loss-rise/`).
  - L0's "compact" is one-shot: it makes L0 due now, and the trigger drifts back within about 2 decisions.
  - After a compact, the learner (which mostly holds) sees the cost, +3.1 ± 1.6. The prior, which keeps
    compacting, sees a saving of 2.5 ± 1.7.
  - So the learner learned "hold beats compact" by 4–9.5 units, and the trigger sat at 6–7.6 instead of the
    prior's 3.4. Each step makes sense, but together they lock the learner in.
  - L1 shows no such lock-in.
- **L2 step (2026-10-07/08,** `~/node_ops/reports/2026-10-08-0230-step-response-results.md`).
  - Expanding L2 to x2 at op 2.9M cut L2's own net inflow from 1,058 to 127 MB per run.
  - L2 stopped at 70% of its new target and never merged into L3 again.
  - Read −0.25% (not separable from zero), write +1.5%.
  - L2's clock N_2 went from 16.7M to 139M operations: the action moved its own clock.
- **L2 reverse step (2026-10-08,** `~/node_ops/reports/2026-10-08-0405-reverse-step-results.md`): the same
  load-built pattern as F7, stronger. L3 hidden steps 155 vs 178, L2→L3 +90%, read −1.6%, write +0.9%.

## 3. Size of the proposed changes

Medium overall. Unchanged: the objective, the contract, the gates and acceptance criteria, the four actions at
L1 and deeper, the per-level agents, the residual law Q = −b + f + δ, cold start, and the L0 agent's price
g(K).

- **A (the prior)** is the only new theory: a new short lemma (fill-up), one new term (lookup cost of a level's
  size), and a note on Lemma D.8.
- **B and C** are standard machine-learning fixes written into the specification.
- **D (L0)** is medium to major: it changes what an action does (Pathway A §2), which every gate assumes.
- **E1** is small in text but has the largest effect: it changes what "beating the static" means.
- **Prerequisite, if the actual charge stays:** it is still only in code (`802d31b`, now on `dqn-actual-charge`).
  PATHWAYS H §3 and H §7 describe the counterfactual charge (OD-7). Its P1/P2 prior terms (A6) rest on it.

Text estimate:
- PATHWAYS: two H §7 bullets rewritten, plus a new lemma and a note on D.8; two H §4 bullets; one H §6 sentence;
  one H §2 paragraph; the L0 row of A §2 and a paragraph of A §3 (D).
- PREREGISTRATION: one dated entry with predictions for the rerun; D-25 with E1.
- About 2 hours to draft, plus about half a day of code and tests for D.

## 4. Proposed theory amendments (not adopted)

"Propagation" column: does the change still hold under the propagation law (PATHWAYS Pathway G), where levels L2
and deeper may share one learned model?
- **fits both**: right for either path.
- **fits if level-free**: right if written as one formula fed by each level's own measured values.
- **waits on OD-6**: depends on the clock or horizon decision.
- **clashes**: conflicts with pooling as planned.

### A. The prior's prices (PATHWAYS H §7, `controller/prior.cc`)

| | PATHWAYS now | Proposed | Targets | Size | Propagation |
| --- | --- | --- | --- | --- | --- |
| A1 | an expand is priced as if the level holds all its new room for a whole turnover | price only the bytes the level actually gains as new data arrives (a "fill-up" lemma). Open: how long the prior assumes an expand lasts (one decision interval or a whole turnover); the law must also explain L2 stopping at 70% | F1; trace cause 1 | medium (new lemma) | waits on OD-6: at L2 the expand cut its own inflow 8× (F1 of the 10-07 recheck) |
| A2 | "scale the hidden steps by the relative growth of B_j" (relative to what is not said) | take the hidden-step rate and the size from the same interval | trace cause 2 | minor | fits both |
| A3 | hidden steps grow in proportion to held bytes, for the whole turnover | a measured response below 1 (short-run +18–27% for +50% bytes), counted only over the learner's horizon; the number is still open | F5 | minor to medium | waits on OD-6 (the horizon, F2 of the recheck) |
| A4 | no credit for a level's own merges getting cheaper when it expands or defers | add it, with a measured strength (−15% per +50% at L1, half of Lemma D.8's 1/size); a note on D.8 | F3; trace cause 4 | medium | clashes as planned: an L1-only constant; a shared prior needs D.8's strength per level |
| A5 | reads priced only through hidden steps and job interference | a lookup-cost term for a level's size (filter checks, seeks, reopens): expand pays it, compact gets it back. H §7's own rule asks for it, since the reward already charges these | F4 | medium | fits if level-free (the pooled state already has e^f, e^b, e^o per level) |
| A6 | (P1/P2 are code only) | keep P1's mechanics (F2 confirmed them), fed by A1's bytes and L0's measured merge response | F2; trace cause 3 | minor | rests on the actual charge (see §6) |
| A7 | — | report the share of decisions at the cap b_max | trace cause 5 | a check, not theory | fits both |

The compact price needs only A5: its gap (about 0.65) is already small, which fits F6.

### B. The learner (PATHWAYS H §4, H §6; `rl_agent/learner/`, `controller/mlp.cc`, `config/learner_settings.json`)

| | Proposed | Targets | Size | Propagation |
| --- | --- | --- | --- | --- |
| B1 | split f into one shared value per state plus per-action differences, hold fixed at 0 ("dueling" network); the law Q = −b + f + δ and cold start are unchanged | each output relearns the −300 baseline; jitter 1.5–1.9 | minor | fits both |
| B2 | push a slow running average of the weights (Polyak averaging); restate H §6's "lags by at most Δ_push" | 13–35% of decisions flip per push | minor | fits both |
| B3 | follow the prior's choice unless the learned part beats it by a margin fixed in advance; the learning rule is unchanged | overrule share swings 7–79% | minor to medium | fits both (the margin may shrink with pooled data) |
| B4 | state the intended training steps per transition | L1 ~12, L0 ~6, undocumented | minor | fits both (restate per pooled sample) |
| B5 | drop the V_own head under the actual charge | trained, unused | minor | rests on the charge choice |

B1, B2, B4 and B5 change `config/learner_settings.json`, which the owner confirmed on 2026-10-05.

### C. Inputs (PATHWAYS H §2, G §3; `controller/state.cc`, `rl_agent/learner/model.py`)

| | Proposed | Targets | Size | Propagation |
| --- | --- | --- | --- | --- |
| C1 | a fixed scale per input, taken from native runs, instead of one ±10 clip | 4 inputs at the clip, ~12 that barely move | minor | fits if one scale per input for all levels |
| C2 | drop the inputs that never change within a run | ~16 dead inputs | minor | **clashes**: most are level terms (π, e_b, e_o, σ) or are constant only at L1 (burst, m_up). They are how a shared model tells levels apart. A pre-trained variant would also need β and the price ratios |
| C3 | L0's three trivial-move inputs are 0 when no trivial move has happened | always empty (NaN → 0) | minor | fits both (L0 is never pooled) |

### D. A persistent L0 compact (Pathway A §2 and §3, H §7; `controller/actions.cc`, masks, tests)

| PATHWAYS now | Proposed | Targets | Size | Propagation |
| --- | --- | --- | --- | --- |
| L0's compact: K_0 ← max(2, k_0), L0 due now, then drifts back (one-shot) | compact also lowers L0's lasting trigger setting by one (to at least 2), drifting back like expand. Options: (a) due now **and** a lower setting (Claude's pick); (b) replace today's compact. Drift rate: the same as expand (Claude's pick) | the L0 lock-in (§2.3) | medium to major (changes an action; ACT-5 must also cover downward drift) | fits both (L0 is never pooled) |

None of A–C fixes the lock-in. A leaves L0's price untouched. B3's margin would not stop it, because the
learner's 4–9.5 gap is larger than the jitter and is right about its own holding policy.

### E. Evaluation

| | Proposed | Targets | Size | Propagation |
| --- | --- | --- | --- | --- |
| E1 | a controller run and its static twin start the measured phase from the same build (into D-25). Options: the static loads at x1 and switches at the measured start, or the controller also acts during the load | F7: the load alone moves J by 1–1.6% at L1 (up to 1.6% read at L2), half the planned 2% effect size | small text, largest effect: statics already measured with their own load stop being valid twins | fits both |

## 5. What no change fixes
- F5's long-run, seed-dependent part cannot be predicted per decision.
- F7's load-built state cannot be reached by a controller that starts at the measured phase (the static test
  confirms it, as the L2 tests did). E1 makes the comparison fair; it does not make the state reachable.
- A4 and A5 rest on numbers measured only at b32-k8 and only at L1.

## 6. Relation to the propagation law (OD-6)
1. **L0 and L1 are never pooled** (PATHWAYS G §4's scope decision). Propagation is claimed only where the tree is
   deep enough: T=2 (candidates L2–L6), and T=6 if levels pass the admission test. At T=10, where every test here
   ran, nothing propagates and every level keeps its own model. So every number in §2 comes from a level that
   would never be pooled. A propagation test needs the T=2 testbed of OD-6 step 2.
2. **The prior is one piece of code for every level, pooled or not.** The A changes therefore have to be written
   level-free to serve both paths. A right prior matters more under propagation, because deep levels have little
   data and lean on b.
3. **The 10-07 recheck's F1 (an action moves its own level's clock) and F2 (the own-clock horizon discounts
   effects that land in the level below) hit the per-level agents too.** The clock and horizon decision is
   needed on both paths, and A1 and A3 wait on it.
4. **The actual charge is the main clash, and it predates these proposals.** It charges a level for its own cost
   plus the whole tree below it. L2's reward then contains L3 and everything deeper, L3's contains less, and the
   state carries little of it. Two pooled levels no longer face the same problem: condition (c2) of Proposition
   G.4 fails beyond its error term. The counterfactual neighbour charge of H §3 was designed with the pool in
   mind. If propagation stays, pooled levels need either the counterfactual charge or a proof that the actual
   charge can be made level-free.

**Fit at a glance.**
- **Fits both paths:** A2, A5 (level-free), A7, B1–B4, C1 (one scale), C3, D, E1.
- **Waits on OD-6:** A1, A3.
- **Clashes as planned:** A4 (until per level), C2.
- **Rests on the actual charge:** A6, B5.

Optional offline check before drafting (no node time): fit level-free forms of A1, A3, A4 and A5 to the L1 data
(2026-10-10) and the L2 data (2026-10-07/08) together. If one formula fits both levels, those changes suit
propagation; if not, the check shows which parts need a value per level.

## 7. Open decisions (all the owner's)
1. Does propagation stay in Programme 1, tested on a T=2 testbed (OD-6 step 2)?
2. OD-6's clock and horizon (F1, F2 of the recheck), needed on both paths.
3. The charge: counterfactual (`dqn-poc-new`) or actual (`dqn-actual-charge`), and for pooled levels in
   particular. If actual: PATHWAYS H §3/H §7 text and a dated entry (OD-7).
4. Go or no-go per group A, B, C, D, E1. Claude's recommendation: the "fits both" set first, the rest after 1–3.
5. Details: B3's margin (Claude can propose one from the measured 1.5–1.9 jitter); A1's assumed expand duration;
   D's form and drift rate; E1's option.
6. The optional offline check of §6.
7. Housekeeping:
   - delete the leftover branches `actual-charge`, `step-apply`, `poc-thursday` and (in the submodule)
     `interim-instruments` (worktrees already removed);
   - push `dqn-poc-new`'s two new commits;
   - commit this file with the other untracked docs (OD-8).
8. Unchanged: OD-1..OD-8 in `docs/OPEN_DECISIONS.md`; the five twin-rule confirmations and D-25
   (`~/node_ops/handoffs/2026-10-10-1142-handoff.md` §3).

## 8. Where things are
- **Reports** (`~/node_ops/reports/`):
  - `2026-10-10-expand-trace/`, `2026-10-10-l1-step/`, `2026-10-10-l1-revstep/`, `2026-10-10-l1-static15/`;
  - `2026-10-10-pref-why/`, `2026-10-10-loss-rise/`;
  - `2026-10-07-1710-propagation-law-recheck.md`, `2026-10-08-propagation-nmin/`;
  - `2026-10-08-0230-step-response-results.md`, `2026-10-08-0405-reverse-step-results.md`.
- **Scripts** (`~/node_ops/diag/expand_trace/`):
  - `expand_terms.py`, `expand_physics.py`, `policy_churn.py`, `input_audit.py`;
  - `l1_step_response.py`, `l1_step_units.py`, `l1_revstep_response.py`, `l1_static_response.py`,
    `deep_merges_by_bin.py`.
- **Chains:** `~/node_ops/diag/{l1step,l1revstep,l1static15}_chain.sh`; logs `~/l1step.log`, `~/l1revstep.log`,
  `~/l1static15.log`.
- **Handoffs:** `~/node_ops/handoffs/2026-10-10-1142-handoff.md`, `2026-10-10-1330-handoff.md`.
- **Investigation page** (does not yet include §2.1 onward): https://claude.ai/artifact/Pk95ANPfqENhtDkkFqGXKA
