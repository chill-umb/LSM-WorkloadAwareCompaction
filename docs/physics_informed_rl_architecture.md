# Physics-Informed Model-Based RL Architecture for LSM Compaction Triggering

*Detailed architecture specification and design rationale. Branch:
`physics-informed-model-based-rl`. Last updated 2026-07-19.*

This document describes the learning architecture that decides *when* to trigger
compaction at each LSM level. It is the flagship research direction. The
surrounding system — per-level agents, protocol v2, telemetry, safety fallback —
is documented in [multilevel_rl_design.md](multilevel_rl_design.md); this
document focuses on the *decision function* and why it is shaped the way it is.

---

## 1. One-Paragraph Summary

Each LSM level is controlled by an agent whose action-value function is a sum of
two parts: an **analytic prior** `b(s, a)` derived in closed form from LSM cost
theory, and a small **learned residual** `f_θ(s, a)` (a DQN) trained online. The
prior encodes what forty years of LSM analysis already knows about the value of
compacting now (stall risk, read-amplification relief, merge cost, amortization
waste); the residual learns only what the theory gets *wrong* on real hardware
(CPU, cache, concurrency, workload idiosyncrasy). The residual is initialized to
exactly zero, so a freshly started agent's policy **is** the analytic policy —
and every gradient step is a correction to the physics, not a replacement for
it.

```
Q(s, a) = b(s, a) + f_θ(s, a)
          └─ analytic ─┘ └─ learned ─┘
             (fixed,        (DQN,
              per-step)      online)
```

---

## 2. Why This Architecture — The Core Argument

### 2.1 The three prior approaches and what each gets wrong

LSM compaction control has been attacked three ways, and each sacrifices
something this task cannot afford to lose:

| Approach | Exemplar | Strength | Fatal weakness for *this* task |
|---|---|---|---|
| **Closed-form cost models → offline tuning** | Monkey, Dostoevsky, Cosine, Vertiorizon | Interpretable, provably optimal *in the model*, zero data cost | Systematically wrong on real hardware (ignores CPU, cache, concurrency); static — cannot adapt online |
| **Model-free deep RL** | RusKey (DDPG) | Adapts to reality online; no white-box assumptions | Demands thousands of samples; poor cross-workload/hardware generalization; discards decades of usable theory |
| **Hand-designed score/simulation engine** | ArceKV (Arce) | Fast, effective, <1% overhead | Decision function frozen at design time — its constants embed the authors' hardware and workloads; reactive; no calibration to the deployment |

The field's 2026 critique of RL-for-storage is explicit: *it needs too much
training data and does not generalize.* That critique is fatal precisely because
of the two structural facts of **this** environment (§2.2).

### 2.2 Two facts about LSM compaction that dictate the architecture

**Fact 1 — Samples are extremely scarce.** A compaction-trigger agent only acts
at flush/compaction-decision boundaries. A 1M-operation run produces on the
order of **300–400 decisions per level**, not the 10⁵–10⁶ transitions deep RL
expects. A from-scratch model-free learner spends that entire budget exploring
and never reaches a competent policy (empirically confirmed on this project:
runs ended with ε ≈ 0.8, i.e. ~80% random, having learned almost nothing). *Any
architecture that starts from a blank slate is dead on arrival here.*

**Fact 2 — The physics is genuinely known, and mostly right.** Unlike most RL
domains, the effect of compaction on tree *shape* is **deterministic and
computable**: post-merge level sizes, the number of runs removed from the read
path, and the bytes rewritten are all exact functions of the current state.
Only the *temporal cost* (how long that I/O takes, how it interferes with
foreground ops) is hardware-dependent and uncertain. So the environment hands us
a high-quality model for free — throwing it away (model-free) is indefensible,
and freezing it (static cost models, Arce) forfeits the one thing learning is
good for: closing the last-mile reality gap.

### 2.3 The synthesis

Physics-informed model-based RL is the architecture that respects *both* facts
simultaneously:

- **It answers Fact 1 (scarcity) by construction.** With `f_θ ≡ 0` at
  initialization, the cold-start policy is the analytic policy — already
  competent. Learning does not need to *discover* good compaction timing from
  random exploration; it needs only to *refine* an already-good policy. This
  collapses the sample requirement by roughly the ratio of "learn from scratch"
  to "calibrate a good prior." It also transfers across workloads and hardware:
  the analytic backbone is the same everywhere; only the thin residual
  re-learns. This is the direct, structural rebuttal to the "RL needs too much
  data / doesn't generalize" objection.

- **It answers Fact 2 (known-but-imperfect physics) by construction.** The prior
  captures the 90% the theory gets right; the residual captures the 10% it gets
  wrong (measured CPU/cache/concurrency effects), and *only* that. Neither the
  pure-analytic camp (can't adapt) nor the model-free camp (can't exploit the
  free model) can do this.

- **It is strictly dominant over its competitors on the decision function.**
  Versus ArceKV: same *kind* of analytic scoring, but **self-calibrated to the
  deployment** instead of frozen at design time. Versus RusKey: keeps the theory
  RusKey discards, so it converges in a fraction of the samples. Versus static
  cost models: online-adaptive.

- **It degrades gracefully.** If the residual ends up learning nothing (`f_θ ≈
  0` throughout), the system is still a *calibrated analytic compaction
  scheduler* — a defensible result on its own. There is no configuration in
  which the architecture is worse than the analytic baseline it starts from
  (modulo exploration noise, which the design bounds — §5). This asymmetric
  risk profile is why it is the flagship bet.

### 2.4 Lineage

The architecture imports a paradigm proven in robotics and control into storage
systems:

- **Physics-informed ML** (Karniadakis et al., 2021) — combine analytic domain
  models with data-driven learning.
- **Residual policy / value learning** (Silver et al. 2018; Johannink et al.
  2018) — learn a correction on top of an imperfect prior controller;
  "conventional controller handles the bulk, learning handles the residual."
- **Physics-informed model-based / Dyna-style RL** (Ramesh et al., 2024) — plan
  over a physics prior with a learned residual; here the structural model is
  even stronger (deterministic tree-shape transitions).

The novelty claim: *residual RL exists (robotics), physics-informed cost models
exist (databases), but nobody has closed the loop of an analytic LSM cost model
calibrated online by value-space residual learning.* Full source list in §6.1 of
the roadmap.

---

## 3. The Analytic Prior `b(s, a)`

The prior assigns `b(s, do_nothing) = 0` and
`b(s, compact_now) = A_analytic(s)` — the closed-form **advantage** of compacting
this level now over deferring. It is computed per level, per decision, from raw
observables already carried in the protocol-v2 message (no extra telemetry).
Implementation: `analytic_advantage()` in
[rl_agent/multilevel.py](../rl_agent/multilevel.py).

### 3.1 The four terms (each a named piece of LSM theory)

```
A_analytic(s) =  W_stall · stall_urgency
              +  W_read  · readamp_relief
              −  W_work  · work_now
              −  W_prem  · premature_penalty        (clamped to ±PRIOR_CLAMP)
```

**1. `stall_urgency` — queueing theory (benefit).** How close this level is to
inducing a write stall, given current inflow. For L0 this is projected
occupancy against the slowdown trigger:
`clamp((files + bytes_in/flush_size) / slowdown_trigger)` — it counts not just
current files but the flush inflow arriving before the next decision. For deeper
levels (which trigger on size score, not file count) it is superlinear in
fullness (`fullness²`), reflecting that pressure bites sharply near capacity.
*Compacting relieves imminent stalls; this term rewards that.*

**2. `readamp_relief` — probe-count model (benefit).** Read amplification ≈
number of sorted runs a lookup must probe. Compacting L0 removes `files` runs
from every subsequent lookup (`clamp(files / trigger)`); compacting a deeper
level removes one run, credited partially and weighted by fullness
(`0.25 · fullness`). *Compacting improves read latency; this term rewards that.*

**3. `work_now` — merge-I/O cost (cost).** The bytes this compaction must
rewrite: this level's bytes plus the overlapping bytes it must merge into in the
next level, normalized by the two levels' capacities:
`clamp((bytes + overlap) / (cap_i + cap_{i+1}))`. The **next-level overlap** is
the dominant term of real compaction cost and is only visible because the state
carries it (via `GetOverlappingInputs`). *Compaction is not free; this term
charges for it.*

**4. `premature_penalty` — Bentley–Saxe amortization (cost).** In the tiering/
amortized-merge analysis, overlap I/O is *re-paid every time you compact*, so
compacting an **underfull** level with **large overlap** is maximally wasteful
versus waiting for it to fill: `clamp(overlap / bytes / 4) · (1 − fullness)`. The
`(1 − fullness)` factor makes the penalty vanish as the level fills (at which
point compaction becomes unavoidable and cheap-per-byte). *This term is the
theory's "don't compact too early" wisdom, made explicit.*

### 3.2 The weights are the "physics constants"

`W_stall = 0.8`, `W_read = 0.4`, `W_work = 0.5`, `W_prem = 0.5` (defaults;
env-tunable via `RL_PRIOR_W_*`). These are the analog of empirical constants in a
physical model — a first-principles *form* with calibratable coefficients. The
learned residual exists precisely to correct whatever these coefficients (and
the functional form) miss. `RL_PRIOR_CLAMP = 2.0` bounds the advantage so a
single term cannot dominate the Q-scale.

---

## 4. The Learned Residual `f_θ(s, a)`

`f_θ` is the DQN from the multi-level architecture, reinterpreted: it no longer
predicts Q directly — it predicts the *residual* between the true Q and the
analytic prior. Implementation across
[agent.py](../rl_agent/agent.py), [model.py](../rl_agent/model.py),
[replay_buffer.py](../rl_agent/replay_buffer.py).

### 4.1 Network

MLP, `state_dim (19) → 64 → 64 → action_dim (2)`, ReLU. Optional dueling head
(`RL_DUELING`) decomposing into value/advantage streams — helpful because in
this task the two actions' values are usually close, so isolating the
state-value stabilizes learning. The non-dueling layout is byte-compatible with
prior checkpoints.

### 4.2 Zero initialization — the load-bearing detail

When `RL_ANALYTIC_PRIOR=1`, the residual network's **final layer weights and
biases are set to zero** at construction (`_zero_final_layers` in
[agent.py](../rl_agent/agent.py)). Therefore at step 0, `f_θ(s, a) = 0` for all
`s, a`, and `Q = b`. The initial greedy policy is exactly `argmax_a b(s, a)` —
the analytic policy. This is what makes "learning refines, never replaces the
prior" literally true rather than aspirational. `RL_RESIDUAL_WEIGHT_DECAY` (L2 on
the residual params) optionally pulls `f_θ` back toward zero, so in states the
data has not informed, the policy relaxes to the prior.

### 4.3 Composition in selection and in the target

The prior is added wherever Q is used — greedy action selection and the TD
target — but gradients flow **only** through `f_θ` (the prior is a per-sample
constant). From `_train_step`:

```
Q(s, a)      = f_θ(s)[a] + b(s)[a]                          # prediction
a'*          = argmax_a' ( f_θ(s') + b(s') )                # Double-DQN selection
Q_target     = R^(n) + γ^n · ( f_θ_target(s') + b(s') )[a'*] · (1 − done)
loss         = MSE( Q(s,a), Q_target )
```

Both the prior vectors `b(s,·)` and the bootstrap prior `b(s',·)` are stored in
each replay transition (the replay buffer tuple was widened to carry them),
because the prior depends on *raw* observables that the normalized encoded state
does not preserve. With `RL_ANALYTIC_PRIOR=0` the stored priors are zero and
every equation above collapses to the plain n-step Double-DQN update —
guaranteeing exact backward compatibility and a clean on/off ablation.

### 4.4 Credit assignment interplay

The residual is trained with the same **n-step windowed reward attribution** the
base architecture uses (compaction relief lands several decisions after the
action; the pending window accumulates discounted rewards before the transition
is finalized). The prior handles the *structural* part of value (known
instantly); the residual + n-step returns handle the *temporal, hardware-
dependent* part (learned from delayed outcomes). This is a clean division of
labor: physics for what is knowable now, learning for what is only observable
later.

---

## 5. Properties and Guarantees

- **Cold-start competence.** At zero data, policy = analytic policy. No random-
  exploration warmup is wasted discovering basic compaction sense.
- **Exploration is cheap and bounded.** Because the prior already encodes the
  sensible action, exploration should start *low* (`RL_EPSILON_START ≈ 0.25`,
  not 1.0) — the agent is perturbing a good policy, not searching from scratch.
  Combined with **action masking** (compact_now withheld from empty/low-score
  levels) and the picker-side **safety guards** (hard L0 cap, stall emergency,
  server-unavailable fallback), the space of harmful exploratory actions is
  small.
- **Graceful degradation.** `f_θ → 0` ⇒ calibrated analytic scheduler; a
  publishable artifact even if learning adds nothing.
- **Interpretability.** Every decision decomposes into named, inspectable terms
  (`prior_stall_urgency`, `prior_readamp_relief`, `prior_work_now`,
  `prior_premature_penalty`) plus a scalar residual advantage — logged per
  decision. This yields **calibration curves** (analytic advantage vs realized
  n-step return) that no black-box baseline can produce.

---

## 6. Configuration & Diagnostics

| Knob | Default | Meaning |
|---|---|---|
| `RL_ANALYTIC_PRIOR` | 0 (off) | Master switch; off ⇒ plain DQN, exact legacy behavior |
| `RL_PRIOR_W_STALL` | 0.8 | Weight: stall-urgency benefit |
| `RL_PRIOR_W_READ` | 0.4 | Weight: read-amp relief benefit |
| `RL_PRIOR_W_WORK` | 0.5 | Weight: merge-I/O cost |
| `RL_PRIOR_W_PREMATURE` | 0.5 | Weight: Bentley–Saxe amortization penalty |
| `RL_PRIOR_CLAMP` | 2.0 | Bound on \|A_analytic\| |
| `RL_RESIDUAL_WEIGHT_DECAY` | 0.0 | L2 on residual params (≈1e-4 recommended when prior on) |
| `RL_DUELING` | 0 | Value/advantage head |
| `RL_EPSILON_START` | 1.0 | **Set ≈0.25 when prior on** — don't discard the warm start |

**Diagnostics.** Per decision the agent logs `analytic_advantage` (prior) and
`residual_advantage` (learned) into `rl_compaction_metrics.jsonl`.
[analyze_prior_calibration.py](../scripts/analyze_prior_calibration.py) turns
these into the calibration scatter (does the prior predict realized value?),
prior-vs-residual magnitude over time (how much is learning correcting?), and
agreement rate (how often the learned policy deviates from the analytic argmax).
[plot_convergence.py](../scripts/plot_convergence.py) compares prior-on vs
prior-off sample efficiency by decision index.

**Ablations built in.** `RL_ANALYTIC_PRIOR` on/off is the headline ablation
(and a dedicated arm of the parallel sweep). Secondary: residual-off (pure
analytic, `RL_RESIDUAL_WEIGHT_DECAY` very large or lr 0), prior-weight
sensitivity, dueling on/off.

---

## 7. How to Run

```bash
# prior enabled, exploration matched to the warm start and decision budget:
scripts/experiment_runner.sh \
  --workload workloads/workload_1M_mixed.txt --db-path /mnt/nvme/rocksdb-data \
  --results-dir results/1M/prior_on \
  --rl-param RL_ANALYTIC_PRIOR=1 \
  --rl-param RL_RESIDUAL_WEIGHT_DECAY=0.0001 \
  --rl-epsilon-start 0.25 --rl-epsilon-decay-steps 100

# then the evidence:
scripts/analyze_prior_calibration.py --run results/1M/prior_on
scripts/plot_convergence.py --runs results/1M/prior_off/rl results/1M/prior_on/rl \
  --labels model_free analytic_prior --out-dir results/1M/prior_ablation
```

**Precondition (learned the hard way):** validate that the leveled baseline
actually exhibits L0 pressure *before* trusting any RL-vs-leveled comparison —
on a pressure-free config the baseline is unbeatable and the prior's value
cannot show. Use [check_pressure.py](../scripts/check_pressure.py) /
[run_pressure_experiment.sh](../scripts/run_pressure_experiment.sh).

---

## 8. Open Extensions (Not Yet Implemented)

- **Decision-time one-step lookahead planning** over the deterministic
  post-compaction state (the model side of "model-based" is currently used only
  to *shape* Q via the prior, not to *plan*). Because tree shape after a
  compaction is exact, a one-ply rollout is cheap and honest.
- **Read-rate term** in `readamp_relief`: weight probe relief by *measured*
  read frequency per level (needs per-level read telemetry from the C++ side),
  turning read-amp relief from a structural estimate into a workload-aware one.
- **Learned prior weights**: treat `W_*` themselves as slowly-adapted parameters
  (meta-calibration) rather than fixed constants.
