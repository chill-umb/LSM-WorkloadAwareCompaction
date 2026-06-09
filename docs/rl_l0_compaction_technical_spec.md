# L0 RL Compaction Technical Spec

## Goal

Turn the current RL compaction prototype from `RocksDb-CodeSetup.pdf` into the L0-only online RL compaction proof of concept described in `Simplified RL Structure.pdf`.

The intended behavior is:

```text
RocksDB observes L0 state
  -> sends state plus previous reward to Python DQN
  -> DQN returns do_nothing / compact_now / delay
  -> C++ applies the action
  -> actual compaction outcome updates the next reward
  -> DQN trains online
```

The RL policy should control when L0 compaction is triggered. RocksDB should still control which files are compacted and how the compaction job executes.

## Current Implementation Summary

The current implementation already has useful scaffolding:

- `kCompactionStyleRL` is added to RocksDB.
- `RLCompactionPicker` is installed when `-C 5` is selected.
- `RLCompactionClient` sends state to Python over a Unix domain socket.
- Python implements a small DQN with replay buffer, epsilon-greedy exploration, target network, and online training.
- The protocol has 6 state features, 3 actions, reward, and done flag.

However, several important parts of the simplified RL design are incomplete or semantically incorrect.

## Required Changes

### 1. Make `compact_now` Actually Trigger L0 Compaction

Current issue:

`RLCompactionPicker::NeedsCompaction()` can return `true` when the RL agent returns `compact_now`, but `RLCompactionPicker::PickCompaction()` delegates to `LevelCompactionPicker::PickCompaction()`. The normal leveled picker only picks score-based compactions when RocksDB's own score is already `>= 1`.

This means RL cannot reliably trigger L0 compaction earlier than RocksDB's normal threshold.

Required change:

- Add a forced-L0 compaction path.
- When the previous RL action was `compact_now`, `RLCompactionPicker::PickCompaction()` must be able to pick L0 input files even if normal L0 score is below `1.0`.
- Keep RocksDB's normal file selection, overlap expansion, clean-cut handling, and output-level logic.
- If L0 has no files, or an L0 compaction is already in progress, return `nullptr` safely.

Candidate files:

```text
lib/rocksdb/db/compaction/compaction_picker_rl.h
lib/rocksdb/db/compaction/compaction_picker_rl.cc
lib/rocksdb/db/compaction/compaction_picker_level.h
lib/rocksdb/db/compaction/compaction_picker_level.cc
```

Acceptance criteria:

- A test or fake RL server returning `compact_now` should cause an actual L0 compaction even when L0 file count is below `level0_file_num_compaction_trigger`.
- `do_nothing` and `delay` should suppress only RL-controlled L0 trigger decisions, not correctness-critical compactions.

### 2. Make Fallback Behavior Explicit

Current issue:

The header comments say that an unavailable RL server falls back to normal RocksDB thresholds, but the socket client currently returns `kCompactNow` on connection or I/O failure.

Required change:

- Replace raw `RLAction` query result with a status-bearing result, for example:

```cpp
struct RLQueryResult {
  bool ok;
  RLAction action;
};
```

- If `ok == false`, use normal leveled behavior:

```cpp
LevelCompactionPicker::NeedsCompaction(vstorage)
```

- Emergency caps should still override both RL and fallback behavior.

Candidate files:

```text
lib/rocksdb/db/compaction/rl_compaction_client.h
lib/rocksdb/db/compaction/rl_compaction_client.cc
lib/rocksdb/db/compaction/compaction_picker_rl.cc
```

Acceptance criteria:

- Running `-C 5` without the Python server should behave like normal leveled compaction, except for explicit hard emergency safeguards.
- Log output should make fallback visible during debugging.

### 3. Correct State Feature Semantics

The PDF defines:

```text
s_t = [f0, delta_f0, score0, pcb, stall, bw]
```

Current implementation only partially matches this.

Required state definitions:

- `f0`: current number of L0 files, normalized.
- `delta_f0`: change in L0 file count since the previous RL decision, roughly centered or clamped.
- `score0`: actual RocksDB L0 compaction score, not only `l0_files / trigger`.
- `pcb`: estimated pending compaction bytes, normalized.
- `stall`: real stall indicator, stall count, or stall micros, normalized.
- `bw`: recent write rate or bytes flushed into L0 since the previous RL step, normalized.

Implementation notes:

- Add a helper to find the score entry whose `CompactionScoreLevel(i) == 0`.
- Treat `kCompactionStyleRL` like `kCompactionStyleLevel` anywhere RocksDB computes level-style L0 score.
- Replace hardcoded `stall = 0.0`.
- Replace `bw = max(0.0, delta_f0_norm)` with telemetry-backed flushed bytes or write rate.

Candidate files:

```text
lib/rocksdb/db/compaction/compaction_picker_rl.cc
lib/rocksdb/db/compaction/rl_compaction_client.h
lib/rocksdb/db/version_set.cc
src/event_listners.cc
include/event_listners.h
include/config_options.h
```

Acceptance criteria:

- State logs should show nonzero `stall` when stalls occur.
- State logs should show nonzero `bw` when writes/flushes happen.
- `score0` should match RocksDB's L0 compaction score for the current version.

### 4. Add Runtime Telemetry

The current picker does not know actual flush bytes, compaction bytes, or stalls.

Add a small telemetry component, for example:

```cpp
struct RLCompactionTelemetry {
  std::atomic<uint64_t> flushed_bytes_since_last_step;
  std::atomic<uint64_t> compaction_bytes_read_since_last_step;
  std::atomic<uint64_t> compaction_bytes_written_since_last_step;
  std::atomic<uint64_t> stall_micros_since_last_step;
  std::atomic<uint64_t> stall_count_since_last_step;
  std::atomic<uint64_t> completed_l0_compactions_since_last_step;
};
```

Required event sources:

- `OnFlushCompleted`: track bytes flushed into L0.
- `OnCompactionCompleted`: track compaction bytes read/write and whether L0 was involved.
- Write-stall events or RocksDB properties: track stall count/micros.

Candidate files:

```text
include/event_listners.h
src/event_listners.cc
include/config_options.h
lib/rocksdb/db/compaction/compaction_picker_rl.cc
```

Acceptance criteria:

- Telemetry counters are consumed/reset at each RL decision step.
- Reward and state use the same telemetry window.

### 5. Correct Reward Computation

The PDF reward is:

```text
r_t =
  - w1 * delta_f0_positive
  - w2 * delta_pcb_positive
  - w3 * stall_t
  - w4 * compact_t
  - w5 * bytes_compacted_t
  + w6 * pressure_relieved
```

Current issues:

- `stall` is hardcoded to `0.0`.
- `bytes_compacted_norm` is hardcoded to `0.0`.
- `compact_t` is based on `rl_last_decision_`, not actual compaction scheduling or completion.
- If RL says `compact_now` but `PickCompaction()` returns `nullptr`, reward can still penalize it as if compaction occurred.
- Reward weights are duplicated in C++ rather than clearly shared/configured.

Required change:

- Compute reward from actual state deltas plus telemetry accumulated since the previous RL decision.
- Define `compact_t` precisely:
  - Option A: `1` if an L0 compaction was successfully scheduled.
  - Option B: `1` if an L0 compaction completed in the telemetry window.
- Use actual `bytes_compacted_t` from compaction job stats.
- Include reward components in debug logs and Python metrics.

Candidate files:

```text
lib/rocksdb/db/compaction/compaction_picker_rl.cc
src/event_listners.cc
rl_agent/metrics.py
```

Acceptance criteria:

- Reward logs expose each component.
- A bad delay causing L0/PCB growth yields negative reward.
- A useful compaction relieving L0 pressure yields positive reward, offset by compaction cost.

### 6. Improve Socket Robustness

Current issue:

`RLCompactionClient::QueryAction()` uses blocking connect/write/read with no timeout. If Python accepts a connection but stops responding, RocksDB can block inside the compaction scheduling path.

Required change:

- Add connect, send, and receive timeouts.
- If a timeout occurs, return fallback status instead of blocking indefinitely.
- Keep the Unix domain socket request/response protocol.

Candidate files:

```text
lib/rocksdb/db/compaction/rl_compaction_client.h
lib/rocksdb/db/compaction/rl_compaction_client.cc
```

Acceptance criteria:

- A hung Python server cannot indefinitely block RocksDB background scheduling.
- Socket failure path is observable in logs.

### 7. Move Python Training Off The Response Path

Current issue:

`agent.observe()` stores transition, trains, then selects an action. This means training can add latency before the response is sent to RocksDB.

Required change:

- Split transition observation, action selection, and training.
- Preferred flow:

```text
receive state/reward
store transition
select action
send action immediately
train after sending response, or train in a background thread
```

- If async training is used, protect model access with a lock.

Candidate files:

```text
rl_agent/server.py
rl_agent/agent.py
```

Acceptance criteria:

- Python sends action before minibatch backpropagation.
- Training still proceeds online.
- Inference and training do not race on model weights.

### 8. Add Required Experiment Metrics

The PDF asks for:

```text
average and p95 write latency
average and p95 read latency
write throughput
L0 file count over time
pending compaction bytes over time
total stall micros / stall count
compaction bytes read/write
write amplification from rocksdb.stats
```

Current implementation:

- `run_workload.cc` logs per-operation timings and aggregate sums.
- `metrics.py` logs RL step, action, reward, epsilon, loss, and normalized state features.
- RocksDB stats can print histograms if enabled, but this is not yet a complete experiment metric pipeline.

Required change:

- Aggregate operation latencies by operation class.
- Compute average and p95 write/read latency.
- Compute write throughput.
- Log L0 file count and pending compaction bytes over time.
- Log stall and compaction bytes from telemetry.
- Include RocksDB stats-derived write amplification when available.

Candidate files:

```text
src/run_workload.cc
src/utils.cc
rl_agent/metrics.py
```

Acceptance criteria:

- Each experiment run produces a machine-readable metrics file.
- Metrics are sufficient to compare `-C 1` normal leveled compaction vs `-C 5` RL compaction.

## Implementation Order

1. Fix `compact_now` semantics so RL can force actual L0 compaction.
2. Fix socket fallback behavior and add timeouts.
3. Replace placeholder state features with real RocksDB signals.
4. Add telemetry-backed reward.
5. Move Python training off the synchronous response path.
6. Add complete metrics for the PDF experiment.
7. Add tests or controlled fake-server experiments.

## Final Acceptance Criteria

The implementation should be considered correct for the simplified PoC when:

- `compact_now` actually compacts L0 below the normal threshold.
- `do_nothing` suppresses only RL-controlled L0 compaction.
- `delay` skips one decision step unless an emergency safeguard fires.
- L0 hard cap, pending compaction bytes hard cap, and stall emergency always override RL.
- Missing or hung Python server falls back predictably.
- State features match the PDF definitions.
- Reward reflects actual LSM outcome, not just the previous returned action.
- Metrics match the `Simplified RL Structure.pdf` output requirements.

