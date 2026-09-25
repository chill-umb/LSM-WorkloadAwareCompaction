# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Most important rule

Before starting a conversation, always check the model being used and if the model is Fable 5.1, always confirm whether I want to use this model. 

## Project

This is research code for a **trigger-only RL compaction controller for RocksDB**. For every observed level, a Python DQN returns `0 = defer` or `1 = compact`. RocksDB's native leveled picker still chooses every input SST and runs the compaction. The goal is point-read improvement with write non-inferiority. The other amplifications and the latencies are held as constraints.

### Where authority lives

| Topic | File |
| --- | --- |
| Forward plan, gates, acceptance criteria, theory | `docs/PATHWAYS.md` |
| Dated decisions, predictions made in advance, gate verdicts | `docs/PREREGISTRATION.md` |
| Frozen research objective | `config/research_objective_contract.v3.json` (machine-readable; the prose is `docs/PREREGISTRATION.md` §4 "Frozen preregistered decisions", moved there from PATHWAYS on 2026-09-20 — the former `docs/RESEARCH_OBJECTIVE_CONTRACT.md` was removed the same day as stale) |
| Every experimental control, paper setup text | `docs/EXPERIMENTAL_SETUP.md` |
| Historical record | `PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md` |
| Pipeline operation | `scripts/dbbench_pipeline/README.md` |

- **`docs/PATHWAYS.md` is the only forward plan**, and since 2026-09-20 it holds
  *only* theory, specification and done/not-done status. Dated verdicts and
  preregistered decisions live in `docs/PREREGISTRATION.md`; put new ones there,
  not in PATHWAYS. A decision recorded after the run it governs is worthless, so
  that file is tracked by git — its commit date is the only proof it was written
  in advance. Pathways A–F, their proofs, per-pathway acceptance criteria and the gated execution order. Its vocabulary is `A-Impl-N`, `C-N`, `Gate N`, `P0`/`P1` preregistration items. Nothing else defines what to do next.
- **`PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md`** is the authoritative record of what happened. §5–6 architecture and the learning/safety system (§5.7 is the six bypass reasons), §11 the pipeline, §14 the dated gate records (§14.7 is Gate 1), §15–16 what is open. It was trimmed on 2026-09-13: §12 is gone and the number is not reused, so §§13–18 keep their numbers and every external citation still resolves.
- **The objective contract is frozen** (v3, 2026-09-12). **Never create a new version — edit `config/research_objective_contract.v3.json` in place**, with a written reason. v1 and v2 remain in the tree only as superseded records; no run was ever executed under either. Never change the contract after seeing a gate's outcome.
- **`TRIGGER_CONTROLLER_REPAIR_PLAN.md` is historical.** Its audited deviations were closed on 2026-08-16 and the controller design it describes is built. Read it for the per-level state machine and the deferral math, not for what to do next.

### docs/ is tracked

**Resolved 2026-09-21 (`b253e85`).** `.gitignore` no longer has any rule covering `docs/`: the blanket `/docs/*` and the `!/docs/PREREGISTRATION.md` re-include were both removed and the directory was committed. `PATHWAYS.md`, `EXPERIMENTAL_SETUP.md`, `PREREGISTRATION.md` and the two design notes all survive a clean checkout, and a new file under `docs/` is picked up by `git add` like any other source file — no `!` rule needed.

`PREREGISTRATION.md` in particular must stay tracked: a preregistration's whole value is a commit date proving it predates the runs it governs. If it ever appears as untracked again, that is a defect, not a convention.

The failure mode that produced the old warning has not gone away for other paths — see "Repo gotchas" below. A blanket ignore plus `!` re-includes silently hides *new* files, which is how `PREREGISTRATION.md` went untracked through four commits while its older neighbours did not. That pattern still governs `*.json`, `*.txt` and `scripts/*`.

## Commands

### Build

**Do not build RocksDB on this machine.** Builds happen on the Chameleon node (CHI@NCAR, Zen 5). The sanctioned local check is syntax-only against the legacy build tree's compile database, which also covers the RL C++ sources:

```bash
g++ -fsyntax-only $(...flags from build/compile_commands.json...) lib/rocksdb/db/compaction/compaction_picker_rl.cc
```

The numbered pipeline is the supported build path when you are on the node (Ubuntu/Debian only, run from the repo root):

```bash
git submodule update --init --recursive
scripts/dbbench_pipeline/00_install_dependencies.sh  # apt deps + venv with rl_agent/requirements.txt (torch, numpy) + matplotlib
scripts/dbbench_pipeline/01_build_rocksdb.sh         # CMake-configure lib/rocksdb (Release, tests off), build librocksdb
scripts/dbbench_pipeline/02_build_db_bench.sh        # build db_bench in the same build dir
```

**Config and this checkout.** `scripts/dbbench_pipeline/config.sh` holds every default; all are overridable from the environment.
- Defaults are `DBBENCH_BUILD_DIR=build-dbbench` and `PYTHON_VENV=.venv-dbbench`. **This checkout has `.venv/` and `build-bench/` instead** — a CMake tree of `lib/rocksdb` containing `db_bench`.
- `ROCKSDB_PORTABLE=znver5` pins the march; it needs GCC 14.1+ or Clang 19+ and has a toolchain preflight.
- `DBBENCH_CPUS=0-7` and `CONTROLLER_CPUS=8` pin the engine and the controller to disjoint cores.
- `STATIC_CAPACITY_SCALES` drives the per-level capacity actuator (empty = off).

**Rebuilding.** The experiment runners never build anything. `scripts/run_full_experiment.sh` refuses to start if `db_bench` is older than `compaction_picker_rl.cc` or `rl_agent/agent.py`, so rebuild after editing either one.

**The root CMake build is legacy.** The root `CMakeLists.txt` builds the old `db_runner` wrapper (`src/`, `include/`) and `tectonic-cli` (Rust nightly) into `bin/`. Its build tree `build/` carries the `compile_commands.json` used for the syntax check above.

RocksDB's own conventions are in `lib/rocksdb/CLAUDE.md`: registering new `.cc` files in `src.mk`, `CMakeLists.txt`, `Makefile` and BUCK; make targets; `make format-auto`. Its test-writing guidance does not apply here — see below.

### No tests

**This repository has no test suite, and none should be written.** The Python
`rl_agent/tests/` and `scripts/dbbench_pipeline/tests/` directories and the RL
cases inside the submodule's `compaction_picker_test.cc` and `version_set_test.cc`
were removed on 2026-09-13. Do not add `test_*.py` files, `TEST_F` cases, a
`__main__` self-check, or a test dependency, and do not re-add them as part of
some other change.

This is research code. The deliverable is a measurement, and correctness is
established by the gates in `docs/PATHWAYS.md` — the oracle parity gate, the
guard holdout, the hull criteria and the paired acceptance evaluators — not by
unit tests. When you change the controller, the proof is the relevant gate
re-run on the node, and a `-fsyntax-only` check locally.

RocksDB's own upstream tests in the submodule are untouched and stay that way;
do not delete or extend them.

## Running experiments

`scripts/dbbench_pipeline/README.md` gives the exact command per stage. Order:
1. Oracle parity gate: 1M operations at T=2, arms `regular oracle`, then `09_evaluate_oracle_parity.py`.
2. Tuned baseline sweep (`05`).
3. SLO manifest selection (`06_select_baseline_slo.py`).
4. Guard calibration and holdout (`06_run_guard_protocol.sh`).
5. Learner preflight (`13`).
6. Paired matrix (`03`).
7. Graphs (`04`), which build `graphs/summary.csv`.
8. Paired acceptance (`07`).
9. Stress suites (`08`).

`scripts/run_full_experiment.sh` runs the whole suite. To resume, pass the same `SUITE_ROOT` again.

Gate-specific stages, added for the PATHWAYS programme:
- `14_gate0_reanalysis.py` — Gate 0 against existing artifacts.
- `15_top_up_hull.{py,sh}` — decide which hull points still need repeats, and record those unresolvable at any affordable cost.
- `16_capacity_calibration.py` — measure ΔS(s) and derive `s_max`, verifying each arm's applied capacity vector against its request.

**Starting and resuming**
- Every runner requires `CONFIRM_*=YES` before it will start.
- `RESUME=1` skips only arms that have a `COMPLETED` marker. Partial result directories are never deleted automatically.
- `03` refuses to start while a stray `db_bench` or `rl_agent/server.py` process is running.

**Disk**
- A failed learner gate keeps its database, roughly 1 GB per million operations.
- Put `DB_ROOT` on the device you are measuring, never on tmpfs `/tmp`.

**Arms and manifests**
- The arms are `regular`, `oracle`, `prior_only`, `rl`, `unconstrained_rl` and `unconstrained_prior_only`.
- **The workload is UDB `Assoc` (Pathway B1) since 2026-09-20**, not the old uniform `balanced-v1`. `WORKLOAD_SKEW=0` restores the uniform family for the B-1 control arms. The decision and its two deliberate departures from the published fit are `docs/PREREGISTRATION.md` D-1.
- `unconstrained_rl` turns off the live SLO mask. It is an ablation only.
- Learned and `prior_only` arms need a schema-v2, calibrated `baseline_slo.json`. Its workload and geometry fingerprint must match the run, or the runner stops. Since D-10 the learner also requires the six `*_latency_avg_ns_telemetry_*` fields, and since D-12 the `latency_reference_trajectory` block, both written by `06_calibrate_live_guard.py`; a manifest from before 2026-09-23 must be regenerated from its calibration artifacts (the guard fields come out identical). Manifests are gitignored data, so each machine regenerates its own; the learner drivers (`d12_learner_run.sh` is current) do this as their first phase, and that phase is safe to re-run: it reads the accepted selection hash from the calibration arms' own `metadata.env`, never from the final manifest, which the calibrator rewrites.

**Protocol settings**
- Protocol v2 is pinned and can't be overridden.
- `RL_OBSERVE_INTERVAL_MS` must equal `RL_DECISION_INTERVAL_MS`. The default for both is 50 ms.
- Direct I/O is pinned **off** (`use_direct_reads`, `use_direct_io_for_flush_and_compaction`). Measured on the node at 10M/T=2: 2,750 ops/s direct against 58,332 buffered, a 21× penalty. Contract v3 records the reversal and retracts an earlier cross-machine 2.6× claim.

## Architecture

```
db_bench --compaction_style=4 (kCompactionStyleRL; the regular arm uses 0)
  └─ RLCompactionPicker            lib/rocksdb/db/compaction/compaction_picker_rl.*
       ├─ worker thread: builds requests from an immutable structural snapshot, off the DB mutex
       ├─ RLCompactionClient ──Unix socket, newline-delimited JSON, protocol v2──► rl_agent/server.py
       ├─ RLControlCoordinator     async coordinator owned by the DB
       ├─ RLSafetyController       SLO mask driven by baseline_slo.json (rl_safety_manifest.*)
       ├─ CompactionPressureObserver, RLCompactionTelemetry   (flush/compaction/stall/latency windows)
       └─ per-level actions → allowed-source-level mask → native LevelCompactionPicker
```

### Rules that span both processes

- **The controller only sets triggers.**
  - A response is an ordered array of binary per-level actions. It never contains file numbers.
  - A "due" authorization adds a level to a mask that is passed into RocksDB's native leveled builder. That builder keeps RocksDB's score ordering, `FilesByCompactionPri`, clean-cut expansion and overlap checks.
  - A below-threshold "optional" authorization goes through the native forced-level path exactly once.
  - Protocol v3, which let the controller pick exact SSTs, was rejected and removed. Don't bring back candidate or file-level control.
- **Nothing slow runs under the DB mutex.** No socket I/O and no Python inference happen there. While holding the mutex, the picker only publishes a snapshot and reads a response that was computed earlier.
- **Messages carry `interval_micros`.** Rates and the SMDP discount use the real elapsed time, not a nominal interval.
- **Forced actions are recorded as overrides.** For safety, drain, maintenance and fallback actions, the requested action and the effective action are kept separate. The sample is relabelled to the action that actually ran and **kept** in replay (Q-learning is off-policy; decided 2026-09-20, P1c). Only the five uncontrolled cases — socket fallback, watchdog fallback, malformed protocol, rejected manifest, unknown ownership — mark the interval invalid and drop it.
- **The controller is suspended during the bulk load.** `db_bench` runs `rlsuspend` before `filluniquerandom` and `rlresume` before `mixgraph`; in between the native leveled picker runs under `ActionReason::kSuspended`, no frame is sent and no safety rule evaluates. Event-log jobs from that window carry `rl_suspended`. **`resetstats` does not reset the tickers** (D-11, 2026-09-23): db_bench's `resetstats` calls `DB::ResetStats`, which clears RocksDB's internal stats only, so the `Statistics` tickers and histograms in the final `stats` dump are cumulative since open and include the load. The evaluator reconstructs the measured phase itself: physical write bytes from the event log inside `[RL_CONTROL_RESUMED_MICROS, RL_DRAIN_END_MICROS]`, user bytes as the ticker less the load's exact bytes (`load_operations × (key + value + 16)`), stall seconds from the internal-stats `Cumulative stall` line, and latency from db_bench's own per-benchmark histograms after the mixgraph line. Never read `rocksdb.bytes.written`, `rocksdb.stall.micros` or `rocksdb.db.*.micros` as measured-phase figures.
- **Capacity expansion is applied in `PrepareForVersionAppend`.** L0 and the final level are pinned per A-Impl-1; a controller-set vector always beats the static one.

### The Python agent (`rl_agent/`)

- **Configuration:** the agent is set up entirely through `RL_*` environment variables, which `start_server()` in `03_run_experiments.sh` sets. db_bench gets its settings the same way, through `RL_COMPACTION_*` variables in that script.
- **Request flow:** `server.py` passes each request to `multilevel.py`, the protocol-v2 processor.
- **Agent:** `agent.py` holds `DQNAgent`.
- **Model:** `model.py` has a shared trunk with a separate two-action head for each level.
- **Q-values:** Q is an analytic prior (`analytic_advantage`) plus a learned residual. The residual starts at zero, so a cold start behaves exactly like the prior.
- **Reward:** `multilevel.MultiLevelProcessor._global_reward` gives every level decision in a frame the same reward, as a **component vector** priced by the multipliers in `lagrange.py` (PREREGISTRATION D-9, 2026-09-23): the objective is point probes per Get, Get-weighted; write amplification, sorted-run seeks and the stall fraction enter as the frame's **signed marginal** contribution to the whole-run constraint (numerator minus bound times denominator, over the run-to-date mean rate), so their run sums equal the evaluator's statistic; the latency averages are flows too, measured against the manifest's `*_latency_avg_ns_telemetry_*` references, which are in the C++ telemetry's own unit (D-10: db_bench's histogram and the telemetry disagree 19× on scan latency, so the formal `*_latency_avg_ns_limit` must never be a hinge target); space bytes is the one hinge; potential shaping runs over the absolute sorted-run count. **The latency multiplier's slack is measured from the end of the 30 s warm-up against the baseline's own since-warm-up trajectory at the same elapsed time** (D-12): the first two seconds after `rlresume` stall writes at 15–32× the limit under the L0 backlog inherited from the load, in every arm and in the calibration arms alike, so a whole-run cumulative reads a positive slack for most of the run whatever the policy does. The priced term is still whole-run. The reward's logical write bytes add 16 B of WriteBatch framing per write op so its W matches the evaluator's measured-phase denominator (D-11; D-10's 30 B was withdrawn), and the first frame after `rlresume` is left out of every run-to-date total because its telemetry window can carry bulk-load writes (D-12 correction). Multipliers take one **signed** dual-ascent step per frame on their constraint's slack after a 30 s warm-up, with each step clipped, and replay stores the vector and re-prices it at sample time, so a multiplier can fall and every batch is priced under one objective. **Before adding any hinge, check that its value and its limit are the same quantity from the same instrument** — seven instruments have failed that test so far (history 14.21–14.24); ask also whether both sides cover the same phase and the same elapsed time. `reward.py` was deleted 2026-09-20; it was the unreachable protocol-v1 path.
- **Action mask (D-9):** a level at or below L1 is offered `compact` only when RocksDB scores it due; L0 below its trigger only when the compaction nets at least `RL_PRIOR_MIN_RUN_REDUCTION` runs. Due levels are never masked. The learned arms run with `RL_L0_ALLOW_DEFER_LEARNED=1` (they may defer a due L0); `prior_only` keeps `RL_L0_ALLOW_DEFER=0`.
- **Known open defects (audit 2026-09-23, `docs/AUDIT_2026-09-23_D12_AND_PATHWAY_A.md`):** Boltzmann exploration starts at temperature 1.0 exactly while the post-load backlog drains and before the first gradient step, which produced D-12's whole T=2 write excess; the temperature is not scaled to Q, so choices are near coin flips before training and frozen after it; the Double-DQN target in `agent.py` `train_step` ignores the next-state action mask; stage 11's flip rate reads stale diagnostics; the write and scan multipliers and the guard's limits still carry the post-load transient; the structural staleness check rejects 19–39% of answers because any file change anywhere invalidates a frame.
- **State:** 37 features (`config.ML_STATE_FIELDS`), including the run-to-date and windowed W over bound, space bytes over bound and the five multipliers. Discount 0.98 per second, 8 s credit window, Huber TD loss.

### Analysis code (`scripts/dbbench_pipeline/*.py`)

- `pipeline_stats.py` has the paired Student-t helpers.
- `slo_statistics.py` has the tolerance bounds used by stage 06.
- `research_objective.py` loads the frozen contract.
- `frontier_analysis.py` builds the empirical hulls the Gate 1 criteria are evaluated against.
- Formal space amplification is the SST bytes measured before compaction divided by `estimate-live-data-size`. The pipeline also records `sst_bytes_after_full_compaction` as the measured alternative denominator; switching to it would be a contract amendment.
- `collect_arm` in `04_generate_graphs.py` returns measured-phase `write_amplification`, `stall_seconds` and latencies since D-11, with `*_whole_run` and `*_source` fields beside them. The whole-run write figure is not a diluted version of the measured one: the load's own run-to-run scatter is comparable to the measured-phase effect, so paired write deltas scored before 2026-09-23 must be re-read from the corrected evaluator, not rescaled.
- The scan objective is sorted-run seeks, because scan amplification is already at its floor.

### Legacy code

`src/`, `include/`, `lib/tectonic/` and `workload_specs/` belong to the older workflow that ran Tectonic workload files. The pipeline doesn't use them, because db_bench generates its own workload: a `filluniquerandom` load followed by `mixgraph`. `workload_specs/README.md` still mentions `scripts/workload_generator.sh`, which no longer exists.

## Repo gotchas

- **`.gitignore` hides new files.** It ignores whole categories (`*.json`, `*.txt`, `scripts/*`) and then re-includes specific paths with `!` rules. New JSON configs and scripts in new directories end up untracked without warning — add a `!` rule for them. Already-tracked paths are unaffected, so the hazard is invisible until a file you expect never shows up in `git status`; `docs/PREREGISTRATION.md` went missing through four commits that way. `docs/` itself is no longer ignored (2026-09-21) and needs no `!` rule.
- **Some tools skip all the RL C++.** The root rule `**/db/**` is meant for database working directories. Git doesn't apply it inside the submodule. Tools that apply the root `.gitignore` recursively, such as graphify, skip `lib/rocksdb/db/`, which is where all the RL C++ lives. If a search comes back empty, search `lib/rocksdb/db/compaction/` explicitly.
- **All C++ changes are in the `lib/rocksdb` submodule.** It is the fork `chill-umb/rocksdb`, and the working branch is `rl-compaction-policy-new`.
  - Commit inside the submodule first, then bump the submodule pointer in the root repo.
  - The research contract pins the RocksDB base at `7ea2d73`, and later commits must record their parent.
  - The `*.cc.d` files next to the sources are make dependency outputs.
- **The fingerprint string in `03` and the regex in `06_select_baseline_slo.py` must stay in lockstep.** `parse_fingerprint_options` is anchored with `fullmatch` and rejects unknown trailing fields. Any new fingerprint field needs the parser updated; emit the segment conditionally if existing runs must stay poolable (this is why the `cap` segment appears only when capacity expansion is on).
- **The C++ manifest parser reads every key by its first textual match.** `rl_safety_manifest.cc` searches the raw text for `"key"`, not the top-level field, and the calibrator writes keys sorted. Never reuse a key name `RLSafetyController::Parse` reads inside a nested manifest block; a nested `schema_version` made the D-12 manifest read as schema 1 and be rejected while the Python side accepted it. `06_calibrate_live_guard.py` now refuses such a manifest (`CPP_FIRST_MATCH_KEYS`, keep in lockstep with `Parse`).
- **RocksDB's `JSONWriter` has no bool overload**, so `status.ok()` and `rl_drain` reach the event log as `1`/`0`. Never test `is True` against an event-log field; use the tolerant form `in (True, 1, "true", "1")` that `04_generate_graphs.py` already uses.
- **Result layout has a `repeat-NN/` level only when `REPEATS > 1`.**
- **Stage 06 must exclude the level-base scale axis** from comparator selection (`--level-base-bytes`), or a 0.5× configuration can win minimum-space and silently redefine the baseline.
- **The hull is bound to its binary.** The evaluator refuses to pool across `dbbench_sha256`. Any criterion comparing a policy against the hull needs the hull re-measured on whatever binary finally runs that policy.
- **Put repeated pipeline logic in a numbered stage, not a pasted heredoc.** That is what `15` and `16` are.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- `docs/` is gitignored, so nothing under it is in the graph. Read those files directly.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).


## Talking Guidelines

# Rules: 
- Talk in plain simple English, rather than using jargons
- Assumne the person you are talking to is a CS student who only has a basic grasp of LSM Tree concepts, so frame your responses so that the person can       understand your responses properly
- If Jargons are necessary, provide a short explanation in simpler terms
- For each response, outline the next immediate task to perform. If some command needs to be executed in the cloud machine, provide the command.
- For each result, provide a verdict whether the result is good or bad according to the corresponding acceptance criteria in PATHWAYS