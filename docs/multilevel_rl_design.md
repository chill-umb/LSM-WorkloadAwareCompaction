# Multi-Level RL Compaction Trigger — Design & Implementation

Expands the single-level (L0) RL compaction trigger to **one DQN agent per LSM
level**, per Phase 3 of the retired FLSM structural plan:
each level's agent observes its own level's state **plus the next level's
state** (except the last level), including **next-level overlap bytes** — the
spec's mandatory feature, since compaction cost is dominated by how much of
level i+1 the merge must rewrite.

Kept from the L0 PoC: binary actions per level (`do_nothing`/`compact_now` —
no run-based `expand-allowance`, which needs the FLSM substrate we deferred),
Double DQN, socket protocol transport, fallback and safety-guard philosophy.

> **2026-08-01 rework.** The first multi-level implementation performed worse
> than the leveled baseline, and neither Double DQN nor the analytic prior
> changed that. A full audit found the learner was never the bottleneck; see
> **[Rework: what was wrong and what changed](#rework-2026-08-01)** at the end
> of this document. The sections below describe the current design.

## Why per-level agents (not one monolithic model)

A monolithic model over all levels would multiply state width by level count,
grow the joint action space combinatorially, and make training/inference
slower and less sample-efficient. Per-level models keep each MDP small (19
features, 2 actions), train each level on exactly its own credit, and mirror
RusKey's level-based decomposition argument. Deeper levels decide rarely, so
their agents naturally stay exploratory longer (epsilon decays per agent
steps) — matching the spec's observation that deep-level training data is
sparse.

## Protocol v2 (C++ → Python, newline-delimited JSON)

One batched request per decision cadence (one socket round trip regardless of
level count):

```json
{"version": 2,
 "pending_compaction_bytes": N, "flushed_bytes": N,
 "compaction_bytes_read": N, "compaction_bytes_written": N,
 "compactions_completed": N, "stall_count": N, "stop_count": N,
 "l0_compaction_trigger": 4, "l0_slowdown_trigger": 3, "l0_stop_trigger": 4,
 "l0_delay_trigger_count": 0, "done": false,
 "levels": [
   {"level": 0, "files": n, "bytes": b, "score": s, "target_bytes": t,
    "next_level_files": nf, "next_level_bytes": nb, "next_level_score": ns,
    "next_level_target_bytes": nt, "overlap_bytes": ob,
    "bytes_in": bi, "bytes_read_out": bro, "bytes_written_out": bwo,
    "compactions_from": cf, "compactions_scheduled": cs,
    "default_needed": false, "is_last": false},
   ...]}
```

Response: `{"actions": [a_0, a_1, ...]}` — one per level entry, request order.
A mis-sized response is treated as server-unavailable (fallback), never
misrouted. The legacy single-level message (`{"l0_files": ...}` →
`{"action": N}`) is still served on the same socket, auto-detected by the
absence of a `"levels"` key, so old binaries keep working.

Candidate levels: L0 always; level i ∈ [1, MaxInputLevel] only when it holds
files. `is_last` marks the deepest level (no next-level features).

## C++ changes (RocksDB submodule)

| File | Change |
|------|--------|
| `db/compaction/rl_compaction_telemetry.{h,cc}` | Per-level atomic counter arrays (16 levels): `bytes_into_level` (flush→L0, compaction output→output level), `compaction_read/written_from_level`, `compactions_from_level`, `compactions_scheduled_from_level`. `RecordCompactionCompleted` now takes `output_level`. Globals kept for the legacy protocol. |
| `db/compaction/rl_compaction_client.{h,cc}` | `RLLevelState`/`RLStateV2` structs, `FormatStateV2` serializer, `ParseIntArrayField` response parser, `QueryActions()` (validates response size == request size). Legacy `RLState`/`QueryAction` removed. |
| `db/compaction/compaction_picker_rl.{h,cc}` | Per-level force flags + cached scores. `QueryRL` assembles all candidate levels (score via `CompactionScoreLevel` lookup, `MaxBytesForLevel` targets, **overlap via `GetOverlappingInputs` on the level's key span**), sends one batch, sets flags from per-level actions. `PickCompaction` serves pending levels **highest-score-first** (stall-risk arbiter; failed picks skip to the next), then falls through to the parent picker. Forced non-L0 picks use `PickCompactionFromLevel(forced_start_level=i)` with `kLevelMaxLevelSize`. |
| `db/db_impl/db_impl_compaction_flush.cc` | Passes `c->output_level()` to telemetry. |

Safety behavior unchanged in spirit: non-score triggers (TTL/periodic/marked/
blob GC) and any RocksDB score ≥ 1 bypass RL (parent handles); L0 stop-trigger
cap, 10 GB pending-bytes cap, and stall/stop emergencies force L0; server
unreachable ⇒ fall back to normal leveled thresholds.

## Python changes (`rl_agent/`)

| File | Change |
|------|--------|
| [multilevel.py](../rl_agent/multilevel.py) | **New.** `MultiLevelProcessor` (per connection): parses v2, per-level adaptive normalization, 19-feature state, per-level reward. `AgentPool` (shared): lazily creates one `DQNAgent` per level with per-level checkpoint paths (`model.l<i>.pt`). |
| [agent.py](../rl_agent/agent.py) | Constructor generalized: `state_dim`, `action_dim`, `save_path`, `name` (defaults = legacy L0 shapes). N-step + Double DQN apply per agent unchanged. |
| [server.py](../rl_agent/server.py) | Auto-detects v2 by `"levels"` key; routes each level slice to its pool agent; replies `{"actions": [...]}` before logging/training; legacy path untouched. Shutdown saves all pool agents. |
| [config.py](../rl_agent/config.py) | `ML_STATE_FIELDS` (19 features), `ML_STATE_DIM`, `ML_MAX_LEVELS`. |
| [metrics.py](../rl_agent/metrics.py) | Rolling windows keyed per level; records carry `level`; console lines tagged `L<i>`. |

### Per-level state (19 features)

Own level: score/2, fullness (L0: files/trigger; L≥1: bytes/target), files,
bytes, bytes-in (arrival rate), bytes-out (compaction I/O), compaction events,
steps-since-compaction. Next level: fullness, score/2, files, **overlap ratio**
(overlap/own bytes) and normalized overlap (zeros when `is_last`). Global:
stall flag, stop flag, pending-bytes, default-trigger flag, and L0-only
slowdown/stop pressure (zeros for deeper levels).

### Per-level reward

Same weight family as the L0 reward (reuses `RL_REWARD_*`): penalties for own
score pressure, growth, global pending pressure/growth, stalls/stops,
compaction I/O + events from this level, `unnecessary_compaction`
(compacted below score 0.75 with no relief) and `late_no_compaction`; reward
for own fullness relief. One-step values feed each agent's n-step window, so
delayed relief still propagates to the triggering decision.

## Coordination (Phase 3 concerns, v1 answers)

- **Thread contention**: one compaction is picked per `PickCompaction` call;
  competing `compact_now` levels are served highest-RocksDB-score-first, rest
  stay pending. No concurrent-forced-compaction cap needed yet since RocksDB's
  `max_background_jobs` still bounds execution.
- **Cross-level credit**: v1 gives every level the global stall/stop penalty
  and relies on next-level observability; the spec's stall-attribution term
  (charge the most recent deferring level) is future work.
- **Staggered epochs / parameter sharing**: not implemented; knobs to consider
  if agents thrash.

## Validation performed

- Python: unit test of processor encoding/reward bookkeeping; end-to-end test
  running the real `server.py` over a real Unix socket with a fake C++ client —
  v2 routing, action ordering, legacy message on the same connection,
  per-level checkpoints on shutdown, per-level metrics, and online training
  all verified.
- C++: `g++ -std=c++20 -fsyntax-only` clean on all four modified files. Full
  build + experiment left for the cloud machine (`scripts/artifact_builder.sh`).

## Design improvements (2026-07-19)

Applied after the protocol-bug postmortem, all pure Python/scripts:

- **Reproducibility (`RL_SEED`)**: seeds python/numpy/torch at server startup;
  0 = unseeded. The parallel sweep driver defaults every config to the *same*
  seed (paired comparison: ranking differences reflect the hyperparameter, not
  exploration luck); repeats should vary the seed.
- **Exploration guardrail (action masking)**: `compact_now` is withheld from a
  level with no files or score below `RL_ML_MIN_COMPACT_SCORE` (default 0.10).
  Masking constrains both exploration sampling and greedy argmax in
  `DQNAgent.select_action(state, valid_actions)`; the executed action is what
  gets stored. Rationale: fresh high-epsilon deep-level agents otherwise force
  pointless near-empty compactions whose I/O dominates early-run cost.
- **Pressure-scaled stall attribution** (`RL_ML_STALL_SCALE_BY_PRESSURE`,
  default on): the global stall/stop penalty is multiplied by the level's own
  fullness — a near-empty deep level is no longer blamed for an L0-caused
  stall. Lightweight version of the spec's stall-attribution term.
- **Reconnect-stable processing**: one shared `MultiLevelProcessor` per server
  (was per-connection), so client reconnects (e.g. after a socket timeout) no
  longer reset adaptive-normalizer scales and per-level prev-state. A lock
  keeps each message's process→observe→advance sequence atomic.
- **Opt-in dueling head** (`RL_DUELING`, default off): V/A decomposition in
  [model.py](../rl_agent/model.py); non-dueling layout unchanged so existing
  checkpoints still load.
- **Checkpoint hygiene**: experiment_runner now clears
  `rl_compaction_model*.pt` (the per-level files too), so a reused results dir
  cannot silently resume stale weights.

## Open items

- Rebuild RocksDB and run a paired experiment (`-C 1` vs `-C 5`) to validate
  end-to-end; the RL run now exercises all populated levels.
- Tune: deeper levels may need smaller stall weights (their causal link to L0
  stalls is weaker); watch action distributions per level.
- The L0-only technical spec docs describe the previous protocol; this doc
  supersedes them for the multi-level design.

---

<a name="rework-2026-08-01"></a>

# Rework (2026-08-01): what was wrong and what changed

The audit found three independent classes of defect stacked on each other.
Every change below is flag-gated, so the previous behaviour stays reproducible
for ablation.

## 1. The agent had no authority where it mattered

`RLCompactionPicker::NeedsCompaction` returned `true` without consulting the
agent whenever *any* non-L0 level had score ≥ 1, and the emergency guard
discarded the agent's answer when L0 had score ≥ 1. The agent was therefore
only ever asked about levels that were **not yet due**. Its entire policy class
was "compact things RocksDB thinks aren't worth compacting" — strictly more
work than the baseline, which is exactly the over-compaction that was measured.
For levels ≥ 1 every non-trivial action was harmful, so adding levels could only
make things worse.

**Now:** the score ≥ 1 bypass is gone. A due level is included in the query with
`default_needed=true`, and `do_nothing` there means **defer** — the only way an
RL trigger can do *less* work than the baseline.

Deferral is bounded (`RL_MAX_DEFER_STEPS`, default 20): past the budget the
level is compacted regardless and the counter resets. This is the
"expand, then compact and reset" shape of the research design, in the *capacity*
sense — no FLSM substrate required, and the natural seam where run-based
expansion can later be substituted. `PickCompaction` no longer falls through to
the parent picker on a pure RL verdict, since that fallthrough would compact
exactly the levels the agent had just deferred.

The physics matters here: deferring a level ≥ 1 costs space and future merge
work but **not** read amplification, because the level is a single sorted run
whatever its size. Deferring L0 does raise read amplification, since L0 holds
overlapping runs — which is why L0's bound is the slowdown trigger. That
asymmetry is encoded directly in the reward potential.

`RL_ALLOW_DEFER=0` restores the old add-only action space.

## 2. The measurement was confounded

The blocking socket round-trip ran inside `NeedsCompaction`, which RocksDB calls
while holding the **global DB mutex** (`DBImpl::MaybeScheduleFlushOrCompaction`
→ `EnqueuePendingCompaction`, both `mutex_.AssertHeld()`). Every query froze the
whole database for the round-trip, up to the 100 ms socket timeout. The RL arm
paid a latency tax the baseline did not, so the comparison largely measured IPC
latency rather than policy quality.

**Now:** an `RLDecisionWorker` thread owns the round-trip.

| | before | after |
|---|---|---|
| `NeedsCompaction` | blocking socket I/O under the DB mutex | publishes a cheap structural snapshot, reads decisions from atomics |
| decision cadence | whenever `MaybeScheduleFlushOrCompaction` fired (43 call sites), gated at 50 ms | fixed `RL_DECISION_INTERVAL_MS` tick |
| telemetry window | "since the last query" — unbounded, policy-dependent | exactly one tick, with `interval_micros` on the wire |

The fixed tick also makes the time step **exogenous**. Previously the cadence
was a consequence of the policy's own actions (compacting more produced more
completion events, hence more decision points), which breaks the MDP.

On the Python side `ASYNC_TRAINING` did not actually decouple training:
`observe()` and the trainer shared one lock, so a decision response could block
behind a full minibatch backward pass. The lock is now split into `_data_lock`
(pending windows, buffer, step) and `_net_lock` (nets, optimizer), with the net
lock taken *per gradient step*.

## 3. The MDP was not Markov and the sample budget was ~100× too small

| defect | fix |
|---|---|
| deltas over an unbounded window used as state | `interval_micros` on the wire; all rate features are per-second |
| fixed per-step γ over variable intervals | SMDP discounting, `RL_GAMMA_PER_SEC` (default 0.95/s) |
| `N_STEP=5` ≈ 250 ms of lookahead vs multi-second compactions — window saw the cost, never the relief | wall-clock credit horizon `RL_CREDIT_HORIZON_MS` (default 2000) |
| chosen action stored even when a safety guard overrode it | picker reports `prev_action_executed`; transitions are keyed on the executed action (Q-learning is off-policy) |
| parent-picker compactions credited to the agent | telemetry splits `compactions_forced_from_level` from `compactions_from_level` |
| `done` hardcoded false; run-end decisions dropped | done-on-destruct message, plus `flush_pending()` on client disconnect |
| adaptive scales drifted, invalidating replayed transitions | scales freeze after `RL_NORM_FREEZE_AFTER` observations |
| ε reached only ≈0.81 by run end (2000 decay steps vs ~330 decisions) | Boltzmann exploration over the prior-composed Q; schedule derived from measured baseline runtime |
| ~330 gradient steps per run; target net synced once | fixed cadence gives ~10× more decisions; 4 train steps/observation; Polyak target updates (τ=0.01) |

## 4. The reward did not contain the objective

There was **no read-side signal anywhere** — not in the state, not in the
reward. But compaction exists to bound read amplification. With only write-side
costs, the reward-optimal policy is "never compact"; the only term opposing that
was `late_no_compaction`, keyed on `default_needed` (RocksDB's own trigger).
The agent was being paid to imitate the baseline, so its ceiling was parity.

**Now:** read-path deltas come from the `Statistics` object RocksDB already
maintains (`NUMBER_KEYS_READ`, `NUMBER_DB_SEEK`, `GET_HIT_L0/L1/L2_AND_UP`,
`BLOOM_FILTER_USEFUL`, `NON_LAST_LEVEL_READ_COUNT`, `LAST_LEVEL_READ_COUNT`),
read by the worker thread — **zero hot-path cost**, since the tickers are
incremented whether or not anyone reads them.

The reward is now a potential difference plus measured costs:

```
Phi(s) = W_STALL*stall_risk + W_READ*read_amp + W_SPACE*space_overshoot
r      = -(Phi(s') - Phi(s)) - io - stalls - realized read amplification
```

Potential-based shaping (Ng et al., 1999) leaves the optimal policy unchanged
while centring the signal near zero. The old form summed eleven always-on
penalties and clamped to [-1, 1], so it was a near-constant negative offset
whose clamp saturated in exactly the high-pressure states that mattered.
`late_no_compaction` is re-keyed to *observed* stalls. Stall blame is split
across levels in proportion to their deferrals rather than charged in full to
every agent. Weights take per-level overrides (`RL_REWARD_<TERM>_L<i>`).

`RL_REWARD_LEGACY=1` restores the old reward as a single-knob ablation.

## 5. Cold-start competence without pre-training

The system must boot cold and adapt online — pre-training on the evaluation
workload would undercut the research claim. So step-0 competence comes from the
**analytic prior** (now on by default): `Q(s,a) = b(s,a) + f_θ(s,a)` with a
zero-initialised residual head, meaning the initial policy *is* the analytic
LSM-physics policy and the DQN only ever learns corrections. `RL_RESUME` and
`RL_EVAL_MODE` exist purely as debugging diagnostics, **not** as the evaluation
protocol.

## State vector (23 features, no dead inputs)

Own level: score (clamped at `RL_SCORE_CLAMP=3.0`, so it has headroom above the
trigger — the old `/2.0` scaling only ever spanned [0, 0.5]), fullness, files,
bytes, arrival rate, compaction I/O rate, compaction event rate,
steps-since-compaction, **deferral-budget consumption**.
Next level: fullness, score, files, overlap ratio, normalized overlap.
Pressure: slowdown and stop pressure — now defined for *every* level as
overshoot past its own target, replacing two features that were hardcoded to
zero for L ≥ 1 — plus stall/stop flags, pending bytes, `default_needed`.
Read path: read rate, L0 hit fraction, non-last-level read fraction.

## Diagnostics

Three ways an "RL run" can silently be a leveled run in disguise, each of which
has bitten this project: the server is unreachable and every query falls back
(one 81-run sweep was invalidated this way); RocksDB reaches its verdict without
consulting the agent; the agent is consulted but overridden. None show up in
throughput numbers, so `compare_experiment_metrics.py` now reports
`rl_bypass_rate`, `override_rate` (overall and per level), `rl_fallbacks` and
`rl_skipped_ticks` next to the results, and warns when the RL path was not
actually in control.

## Tests

`rl_agent/tests/test_rl_agent.py` (29 tests) and
`rl_agent/tests/test_socket_e2e.py` (7 tests, real server over a real socket).
Each test names the finding it guards. The e2e suite asserts on the **raw bytes**
of the response, which is what would have caught the 2026-07-19 protocol bug
where the C++ parser's needle never matched `json.dumps` output.

```
.venv/bin/python3 rl_agent/tests/test_rl_agent.py
.venv/bin/python3 rl_agent/tests/test_socket_e2e.py
```
