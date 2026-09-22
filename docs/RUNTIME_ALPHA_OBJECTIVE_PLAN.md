# Runtime-Tunable Read/Write Objective Weight (`alpha`): Plan

**Status: implemented in source, not built or run.** The changes below are in
`rl_agent/` on this checkout. Consistent with `CLAUDE.md` ("do not build
RocksDB on this machine"), nothing has been compiled or executed — that
happens on the Chameleon node. Nothing in this document authorizes a
research run: any experiment in §7 needs a contract amendment and a
`docs/PREREGISTRATION.md` entry, recorded before it runs, per this project's
own rule (`PREREGISTRATION.md` §0: a criterion or decision written after its
outcome is seen is worthless). The correctness gates in §6, by contrast, are
engineering verification, not research claims, and can run as soon as the
node builds this revision.

## 0. What's actually in the tree

| File | Change |
| --- | --- |
| `rl_agent/config.py` | `RL_OBJECTIVE_ALPHA` (default `1.0`), `RL_ALPHA_CONTROL_FILE` (default off), `RL_REWARD_WRITE` (symmetric counterpart to `RL_REWARD_READ`), `"objective_alpha"` appended to `ML_STATE_FIELDS` (state width is now 32, was 31 — flows automatically through `ML_STATE_DIM`). |
| `rl_agent/multilevel.py` | New `RuntimeAlpha` class (file-poll, once per decision tick, atomic-write-safe, logs reload events). `analytic_advantage()` takes `alpha`, scales only the read-relief credit. `_global_reward()` takes `alpha`, blends the rate-priced objective term. `_encode()` takes `alpha`, appends it as the last state feature. `MultiLevelProcessor` owns one `RuntimeAlpha` instance, polls it once per `process()` call, threads `alpha` through every per-frame call. `reward_state()` reports the live value and reload log. |
| `rl_agent/server.py` | Startup log line now prints `objective_alpha` and `alpha_control_file`. |
| `scripts/dbbench_pipeline/set_objective_alpha.sh` | New. Atomic writer (`mktemp` + `mv`) with value validation. |
| `scripts/dbbench_pipeline/config.sh` | `OBJECTIVE_ALPHA` (default `1.0`), `OBJECTIVE_ALPHA_LIVE` (default `0`). |
| `scripts/dbbench_pipeline/03_run_experiments.sh` | `start_server()` passes `RL_OBJECTIVE_ALPHA`/`RL_ALPHA_CONTROL_FILE` through. `run_arm()`'s directory name gains an `-alpha<value>[live]` suffix when non-default, or every alpha in a sweep would collide on one `result_dir`. `metadata.env` records `objective_alpha`/`objective_alpha_live`/`alpha_control_file`. **Alpha is deliberately NOT part of `experiment_fingerprint`** — see the correction below. |
| `scripts/dbbench_pipeline/06_select_baseline_slo.py` | No change (a fingerprint-segment addition here was tried, then reverted — see below). |
| `scripts/dbbench_pipeline/04_generate_graphs.py` | `collect_arm()` now copies `objective_alpha`/`objective_alpha_live` from `metadata.env` into each `summary.csv` row (they were being written to `metadata.env` but silently dropped before this, since `collect_arm` builds an explicit field list rather than dumping the file). |

**Confirmed needing no changes**, by reading rather than assuming:
`07_evaluate_paired.py` filters by the `arm` column only, which alpha never
touches.

### 0a. A real bug, caught on hardware: alpha does not belong in the fingerprint

**First implementation (wrong, since fixed).** The fingerprint gained a
conditional `:alpha<value>[live]` segment, on the same pattern as `:cap`/
`:skew`. This looked consistent by analogy but was wrong: `:cap` and `:skew`
describe changes that affect *every* arm including `regular` (capacity
expansion is applied in `PrepareForVersionAppend` regardless of compaction
style; skew changes the workload `db_bench` itself generates). `alpha` only
ever reaches the RL controller's reward — `regular` never starts a server
and is structurally incapable of having an alpha value. A manifest is always
generated from a `regular` run, which therefore never carries an `:alpha`
segment at all. Putting alpha in the fingerprint that gets checked against
the manifest meant **every non-default alpha would fail that check against
every manifest, unconditionally** — which is exactly backwards for a knob
whose whole purpose (the frontier sweep, §7) is checking many alpha values
against one shared manifest.

**Found by running Gate α-1 on the node**, not by review: `alpha=1.0` (the
default, empty fingerprint segment) passed cleanly; `alpha=0.0` failed with
`Current geometry does not match baseline_slo/.../baseline_slo.json`, the
manifest-match check, the moment a non-default value was tried. That
asymmetry — the first real value tested being the one that breaks — is what
distinguishes a design bug in the fingerprint scheme itself from a fluke in
one run.

**Fix.** Alpha is not part of `experiment_fingerprint` at all — same
treatment as `RL_OPTIONAL_MIN_SCORE`, `RL_EXPLORATION_ANNEAL_SECONDS`, and
every other RL-only policy knob that already lived outside the fingerprint,
recorded only in `metadata.env`. `06_select_baseline_slo.py`'s regex change
was reverted to its pre-alpha form rather than kept dormant.

**What this costs, honestly.** `frontier_analysis.py`'s `collect_grid()`
used to be able to catch an accidentally-mixed-alpha results root through
its own fingerprint self-consistency check (`len(identities) != 1` raises).
With alpha out of the fingerprint, two different alpha values now produce
*identical* fingerprints, so that automatic check no longer distinguishes
them. The mitigation is operational, not automatic: every alpha value gets
its own top-level results root (§7's note, and this is now how the runbook
is actually run), so nothing relies on the fingerprint to keep sweep points
apart.

No changes to `model.py`, `agent.py`, or `replay_buffer.py`. Conditioning the
network on alpha turned out to need **no architecture change**: `alpha` is
just one more entry in the state vector, and every network already takes
`state_dim` from `config.ML_STATE_DIM`, which grew by 1 automatically. See
§4a for why this also resolves the credit-window integrity question more
simply than originally planned.

**Scope relative to the existing program.** `docs/PATHWAYS.md` Pathway D
already anticipates a version of this idea — sweeping a relaxation `β` of
the write-parity margin to trace the frontier's shape, explicitly *"exposition
... never the acceptance criterion."* This plan generalizes that one-sided
margin sweep into a symmetric, runtime-changeable weight, and treats it the
same way: diagnostic and exploratory, not a replacement for the Pathway
A–E acceptance criteria, unless a later decision explicitly promotes it.

---

## 1. Goal

A scalar `alpha ∈ [0,1]`, changeable while an experiment is running (no
restart, no loss of learned weights or replay buffer), that blends the
controller's objective between "minimize point-read amplification"
(`alpha=1`, today's default) and "minimize write amplification" (`alpha=0`).

## 2. Decisions

| # | Decision | Status |
| --- | --- | --- |
| D1 | Learner handles a moving alpha via **an alpha-conditioned Q-network** (multi-objective RL / universal value function), not the cheap regime-switch branch. | **Decided.** See §2a for the constraint this runs into and the scope it's held to as a result. |
| D2 | Where this sits relative to the frozen contract | **Still open.** Given §2a below, recommend treating it as exploratory/diagnostic only (no contract amendment needed to build or run it, only to ever cite a result from it in the paper) until proven otherwise. |

### 2a. The constraint this decision runs into

`PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md` §3.2 and the frozen v3 contract
state a **hard rule: the policy starts from scratch every experimental run,
no persisted checkpoints, no offline pretraining.** ("Historical documents
discuss checkpoint persistence and one roadmap considered offline-RL
pretraining. Those are not part of the current formal experiment.")

A genuinely *generalizing* alpha-conditioned network — one that gives sane
Q-values at an alpha value it never saw during the run — needs exposure to a
spread of alpha values during training to learn to interpolate across that
axis. Under strict per-run cold start, that training has to happen inside
the same scarce sample budget the project already struggles with (~300–400
decisions per level per 1M-op run, per `physics_informed_rl_architecture.md`
§2.2) — and conditioning multiplies what the network has to learn (a
surface over `(state, alpha)`, not a point). That's a materially harder
learning problem on an unchanged sample budget.

**Since you said the knob won't change often, the plan below deliberately
does not chase full-surface generalization.** It targets a narrower,
achievable goal: the network conditions on alpha so that (a) a change in
alpha doesn't destabilize training the way an *unconditioned* moving-target
reward would, and (b) it interpolates *locally*, near whichever alpha
values actually get used in a given run — not across the full `[0,1]` range
sight-unseen. That is achievable inside one run's budget and doesn't touch
the frozen no-pretraining rule. Full-range generalization from a single
cold start is very unlikely to be reliable and is **not** a goal of this
plan; if you want that later, it requires reopening §3.2, which is a
contract-level decision, not an implementation one, and should be a
separate conversation.

## 3. Design, as built

```
cost_rate = alpha * REWARD_READ * point_amp          # was the whole term before this change
          + (1 - alpha) * REWARD_WRITE * waf          # new; waf is the existing windowed WAF
          + lambda_write * write_excess                # unconditional — write's own hinge
          + lambda_space  * space_excess                #   still enforces parity/bound
          + lambda_latency * latency_excess             #   regardless of alpha
          + lambda_scan   * scan_excess
          + lambda_stall  * stall_excess
reward = shaping - cost_rate * dt
```

At `alpha=1` (the default), `(1-alpha)*REWARD_WRITE*waf = 0` and the formula
is textually identical to the reward before this change — the regression gate
in §6 checks exactly this. `alpha` reuses the reward's existing windowed WAF
(`waf`, already computed for the write hinge) rather than a second write-cost
computation.

**The prior scales only the read-relief credit, not the work/premature cost
terms** — a deliberate change from the original sketch. Scaling `work_now` by
`(1-alpha)` would have meant the *default* (`alpha=1`) applies work cost at
full weight while a naive reading suggested it should vanish at `alpha=1`;
keeping the cost terms untouched and only multiplying the read-side benefit
by `alpha` is what makes `alpha=1` reproduce today's prior exactly, and it is
also the more defensible physical reading: `alpha=0` should mean "no read
justification for compacting," not "compaction magically stops costing
anything."

## 4. Implementation changes, by file (as built — see §0 for the summary table)

No C++ or protocol changes. `alpha` is consumed entirely inside the Python
controller process, which already owns both the prior and the reward.

**`model.py`, `agent.py` and `replay_buffer.py` needed no changes.** The
original sketch assumed conditioning the network on alpha would require
widening the network input, the encoder, and the replay tuple by hand. In
the real code, `alpha` is just appended as one more entry in
`ML_STATE_FIELDS` (`config.py`), and every downstream consumer —
`MultiHeadDQN`'s input width, the replay buffer's stored `state`/`next_state`
arrays, the TD target computation — already derives its shape from
`config.ML_STATE_DIM`, which grew by one automatically. No separate "alpha
channel" was needed.

**The credit-window snapshot risk from the original plan turned out not to
be a real bug, for a specific reason worth recording.** The concern was that
a mid-window alpha change could corrupt a pending transition's reward. But
`_global_reward()` and `analytic_advantage()` are both called fresh on every
`process()` invocation using whatever `alpha` is live *at that real
timestamp* — so a reward accrued into an open credit window during frame N+2
correctly reflects the objective actually in force at frame N+2, not the
objective that was in force when the window opened at frame N. That is the
*correct* behavior for online credit assignment, not a bug: the return really
is "what this action was worth given what actually happened afterward,"
including a real objective change if one occurred. The state vector's alpha
feature is likewise captured fresh at each `_encode()` call and stored
verbatim in each transition's `state`/`next_state`, so the network sees the
true historical alpha at both ends of every transition by construction — no
extra snapshotting machinery needed. What remains a real, undiminished risk
is **non-stationarity of the reward signal itself** if alpha moves often
(§7's Risk 2 from the original answer) — that's a training-stability
question, not a correctness bug, and it's why §7's infrequent-change
assumption still matters.

**`RL_ALPHA_TRAIN_SAMPLING` (the "jitter" idea) was not built.** Given you
don't expect the knob to change often, adding a synthetic alpha-jitter
schedule now would be speculative machinery with no data yet showing it's
needed. Revisit only if the local-conditioning validation experiment (§7.4)
shows the network isn't picking up local structure around the alpha values a
run actually visits.

## 5. New instrumentation needed (not tests — this project runs no test suite)

Per `CLAUDE.md`: no `test_*.py`, no `TEST_F`, no pytest-style suite. Verification
here follows the existing convention — a `-fsyntax-only` check for anything
touching C++ (none needed here) plus **gates**: short, scripted diagnostic
runs whose output is inspected against a stated threshold, the same way the
oracle-parity gate works.

- `objective_alpha`, `objective_read_term` and `objective_write_term` per
  decision record — already implemented, they ride along in the existing
  `reward_components`/`io.jsonl` path every level's decision already logs.
- `objective_alpha_reload_events` in `reward_state()` (surfaced in
  `server_summary.json` at shutdown): timestamp, frame index, old value, new
  value — proof the hot-reload path actually fired during a real run.
- A replay-buffer diagnostic (analysis-time, no code change needed): each
  stored `state`/`next_state` array's last element **is** the alpha that was
  live for that transition (it's a state feature, not a side table), so
  `11_analyze_learning.py`-style tooling can already histogram transitions by
  that column directly from the buffer/logged states.

## 6. Correctness gates (run before any research experiment)

| Gate | What it checks | Threshold |
| --- | --- | --- |
| α-0 (regression) | `alpha=1` fixed (the default — `RL_ALPHA_CONTROL_FILE` unset), paired against the pre-change binary at an existing cell (e.g. 1M/T=2) | Zero measurable difference — this is a bridge-transparency gate, same shape as the oracle-parity gate. Textually, `objective_read_term`/`objective_write_term` in the decision log should show `write_term == 0.0` on every frame. |
| α-1 (flip sanity) | `alpha=0` fixed on a workload with known write/read pressure | `prior_readamp_relief`'s effective weight (`prior_w_read_effective`, now logged) is 0 on every frame; compact rate should fall relative to `alpha=1` since only stall urgency remains as a benefit; `objective_write_term` should be the dominant driver of `cost_rate` |
| α-2 (reload latency) | Write the control file mid-run at a known timestamp; measure ticks until `objective_alpha` in the decision log changes | Should equal 1 decision-interval tick (~50ms) ± scheduling jitter; a larger gap means the poll isn't actually per-tick |
| α-3 (state/reward consistency) | Flip alpha mid-run at a known point; for a transition whose window closes after the flip, confirm its logged `objective_alpha` matches the alpha that was live at the frame the *reward* was earned (not the frame the *decision* was made) | This should trivially hold by construction (§4) — the gate exists to catch a regression in that construction, not to detect an expected anomaly. A mismatch means someone reintroduced a cached/stale-alpha path. |
| α-4 (local generalization, conditioned-network specific) | Train at one alpha value for most of a run, then hold the level's Q-network fixed (eval mode) and query it at a handful of nearby untrained alpha values (e.g. trained at 0.7, queried at 0.6/0.65/0.75/0.8) | Q-values and implied action ranking change *smoothly* with alpha near the trained point — no discontinuity or sign flip in the advantage between adjacent queried alphas. This is the cheapest check that conditioning is doing something sane locally; it is explicitly **not** a claim about far-away alpha values (e.g. querying 0.7-trained network at 0.1) — that's out of scope per §2a. |

Run α-0 to α-3 at 1M, 3 repeats, before spending any node-hours on the
frontier sweep below. α-4 is a post-hoc analysis of α-2/α-3's logs, no
extra run needed. Estimated cost: ~1–2 h.

## 7. Experiments to validate the *research* behavior

All reuse the existing paired-repeat, alternating-order, Student-t
machinery (`07_evaluate_paired.py`) — `alpha` is just a new axis alongside
`T`, not a new statistical instrument.

**Operational note: one `RESULTS_ROOT` (or `SUITE_ROOT`) per alpha value —
now load-bearing, not just tidy.** Alpha is deliberately not part of
`experiment_fingerprint` (§0a), which means `frontier_analysis.py`'s
`collect_grid()` can no longer catch a mixed-alpha results root through its
fingerprint self-consistency check — two different alpha values now produce
identical fingerprints. Give each swept alpha value its own results tree
(`OBJECTIVE_ALPHA=0.25 ./03_run_experiments.sh ...` into its own
`RESULTS_ROOT`, repeated per alpha), then call `frontier_analysis.py
--policy-results <that tree> --policy-arm rl` once per alpha.
`docs/PREREGISTRATION.md` D-3 records this as the intended usage.

1. **Frontier sweep (static alpha per run).** `alpha ∈ {0, 0.25, 0.5, 0.75,
   1}`, fixed per run, at existing Gate-1-style cells (e.g. 10M × T=2/6/10),
   paired repeats. Plot the resulting `(W, R)` points **directly on the
   existing Pareto hull** (Pathway C machinery, no new tooling) — the
   question this answers is whether sweeping alpha traces a frontier that
   is *non-dominated* by the static class, which is the same question C-3/
   C-4 already ask, just parameterized by alpha instead of by static config.
2. **Runtime-switch dynamics.** One longer run per cell; flip `alpha` at a
   preregistered midpoint. Measure: (a) decisions until the action
   distribution visibly shifts (should match α-2's tick-level latency, not
   drift over minutes), (b) TD-loss behavior around the flip (a spike that
   doesn't recover is the non-stationarity risk from the prior answer,
   showing up empirically), (c) α-3's integrity check under real load
   rather than a synthetic one.
3. **Infrequent-change realism test (replaces a full non-stationarity stress
   test, given your expected usage).** Rather than sweeping change
   frequency from rapid to rare, run the pattern you actually expect: one
   run, alpha fixed for the first ~80% of decisions, one or two flips near
   the end. Confirms the conditioned network handles the *realistic* usage
   pattern cleanly. A rapid-alternation stress test is not worth building
   for its own sake given "I don't expect the knob to change very
   frequently" — drop it unless experiment 4 below shows a problem.
4. **Local-conditioning validation.** Compare the conditioned network,
   evaluated at each of a few alpha values actually visited during
   training, against separately-trained fixed-alpha networks at those same
   values (not the full grid — per §2a, only nearby/visited points are in
   scope). This is now the central validation experiment for D1, not
   optional: it's the direct test of whether conditioning cost anything
   relative to just training a plain network at a fixed alpha.

## 8. Statistical protocol and preregistration

- Comparator for the frontier sweep is the existing Pareto hull (Hull₀ for
  arms without a capacity action), not a new comparator — reuse, don't
  rebuild.
- The sweep grid, the flip schedule, and N for the stress test must be
  **fixed before running**, written into `docs/PREREGISTRATION.md` with a
  date, exactly like D-1/D-2. This is a new experimental axis on top of the
  frozen v3 contract, so it needs an in-place contract amendment
  (`config/research_objective_contract.v3.json`), not a new version.
- Hard bounds (space/latency/stall) stay fixed regardless of alpha — the
  sweep should never be allowed to violate them, and the guard/safety mask
  should not be made alpha-aware (§6.4's SLO mask is about safety, not
  about the read/write tradeoff; conflating them would make failures
  harder to attribute).

## 9. Cost estimate

| Item | Cost |
| --- | --- |
| Implementation (conditioned network) | 0 node-hours; ~1 day engineering — state/model/agent/replay widening is more surface area than the cheap branch, but each piece is small |
| Gates α-0 to α-4 | 1M, 3 repeats each ≈ 1–2 h |
| Frontier sweep (5 alphas × 3 T × paired repeats) | comparable to a scaled-down Gate 1 ≈ 8–12 h |
| Infrequent-change realism test (3 cells) | ≈ 2–3 h |
| Local-conditioning validation | reuses frontier-sweep runs plus a small number of fixed-alpha comparison runs at the same points ≈ 2–3 h |
| **Total** | **~1–1.5 days of node time** — still well under any single Pathway A–E gate |

## 10. Open items blocking start

1. **D2** (§2, still open): exploratory-only vs. formal Pathway-F-adjacent
   extension. Recommendation stands: exploratory-only until §2a's local-
   generalization scope is validated by α-4 and experiment 4.
2. Whether the frontier-sweep result is meant to ever appear in the paper
   (then it needs the full preregistration treatment and belongs after
   Gate 3b, not before) or stays an internal diagnostic tool (then it can
   be built and run now, off the critical path, same status as Pathway F).
3. Confirm the §2a scope limitation (local generalization only, no
   full-range interpolation from a single cold start) is acceptable — it's
   the direct consequence of respecting the frozen §3.2 no-pretraining
   rule rather than quietly working around it.
