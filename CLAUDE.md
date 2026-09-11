# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

This is research code for a **trigger-only RL compaction controller for RocksDB**. For every observed level, a Python DQN returns `0 = defer` or `1 = compact`. RocksDB's native leveled picker still chooses every input SST and runs the compaction. The goal is point-read improvement with write non-inferiority. The other amplifications and the latencies are held as constraints.

Read these before making non-trivial changes:
- **`PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md`** is the authoritative record.
  - §5–6: architecture and the learning/safety system.
  - §11: the pipeline.
  - §13: test status.
  - §14–15: what is implemented and what is still open.
- **`TRIGGER_CONTROLLER_REPAIR_PLAN.md`** is the current controller design plan. It covers the per-level state machine, the deferral math, the required tests and the phases.
- **`docs/RESEARCH_OBJECTIVE_CONTRACT.md`** and **`config/research_objective_contract.v1.json`** hold the research objective, frozen on 2026-09-10.
  - Changing either needs a version bump and a written reason.
  - Never change them after seeing a gate's outcome.
- **`docs/rl_l0_compaction_technical_spec.md`** is tracked, but it describes the old L0-only, 3-action proof of concept. Don't treat it as current.

## Commands

### Build

The supported path is the numbered db_bench pipeline. Run it from the repo root; it supports Ubuntu/Debian only.

```bash
git submodule update --init --recursive
scripts/dbbench_pipeline/00_install_dependencies.sh  # apt deps + venv with rl_agent/requirements.txt (torch, numpy) + matplotlib
scripts/dbbench_pipeline/01_build_rocksdb.sh         # CMake-configure lib/rocksdb (Release, tests off), build librocksdb
scripts/dbbench_pipeline/02_build_db_bench.sh        # build db_bench in the same build dir
```

**Config and this checkout.** `scripts/dbbench_pipeline/config.sh` holds every default, and any of them can be overridden from the environment.
- The defaults are `DBBENCH_BUILD_DIR=build-dbbench` and `PYTHON_VENV=.venv-dbbench`.
- This checkout has `.venv/` instead, with torch installed.
- It also has `build-bench/` instead, a CMake tree of `lib/rocksdb` that contains `db_bench`.

**Rebuilding.** The experiment runners never build anything. `scripts/run_full_experiment.sh` refuses to start if `db_bench` is older than `compaction_picker_rl.cc` or `rl_agent/agent.py`, so rebuild after editing either one.

**The root CMake build is legacy.** The root `CMakeLists.txt` builds the old `db_runner` wrapper (`src/`, `include/`) and `tectonic-cli` (Rust nightly) into `bin/`. Its build tree, `build/`, has a `compile_commands.json` that also covers the RocksDB RL sources. Use it for `-fsyntax-only` checks without doing a full build.

### Python tests

The tests use `unittest`; the venv has no pytest.

```bash
.venv/bin/python -m unittest discover -s rl_agent/tests                  # needs torch; test_socket_e2e.py binds Unix sockets
.venv/bin/python -m unittest discover -s scripts/dbbench_pipeline/tests
.venv/bin/python -m unittest discover -s rl_agent/tests -p test_config.py -k test_reward_keeps_formal_limits_separate_from_live_guard  # single test
```

As of 2026-09-11, `rl_agent` has exactly two failing tests. They are the "pre-existing failures" baseline referred to in the project history:
- `TestPotentialReward.test_cost_half_scales_with_the_interval_it_covers`
- `TestReadPathSignals.test_deep_level_read_cost_scales_with_how_full_the_level_is`

### C++ RL tests

The C++ RL tests are in `lib/rocksdb/db/compaction/compaction_picker_test.cc`. They are `CompactionPickerTest.RL*` plus `PressureObserverUsesZeroOrderHold`. Build them with RocksDB's make, which gives a debug build:

```bash
make -C lib/rocksdb -j"$(nproc)" compaction_picker_test
lib/rocksdb/compaction_picker_test --gtest_filter='CompactionPickerTest.RL*:CompactionPickerTest.PressureObserver*'
```

RocksDB's own conventions are in `lib/rocksdb/CLAUDE.md`. That file covers:
- registering new `.cc` files in `src.mk`, `CMakeLists.txt`, `Makefile` and BUCK;
- make targets;
- formatting with `make format-auto`.

## Running experiments

`scripts/dbbench_pipeline/README.md` gives the exact command for each stage. Run the stages in this order:
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

**Starting and resuming**
- Every runner requires `CONFIRM_*=YES` before it will start.
- `RESUME=1` skips only arms that have a `COMPLETED` marker. Partial result directories are never deleted automatically.
- `03` refuses to start while a stray `db_bench` or `rl_agent/server.py` process is running.

**Disk**
- A failed learner gate keeps its database, which takes roughly 1 GB per million operations.
- Put `DB_ROOT` on the device you are measuring, never on tmpfs `/tmp`.

**Arms and manifests**
- The arms are `regular`, `oracle`, `prior_only`, `rl` and `unconstrained_rl`.
- `unconstrained_rl` turns off the live SLO mask. It is an ablation only.
- Learned and `prior_only` arms need a schema-v2, calibrated `baseline_slo.json`. Its workload and geometry fingerprint must match the run, or the runner stops.

**Protocol settings**
- Protocol v2 is pinned and can't be overridden.
- `RL_OBSERVE_INTERVAL_MS` must equal `RL_DECISION_INTERVAL_MS`. The default for both is 50 ms.

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
- **Forced actions are recorded as overrides.** For safety, drain, maintenance and fallback actions, the requested action and the effective action are kept separate. Those intervals stay out of Q replay, but their telemetry is kept.

### The Python agent (`rl_agent/`)

- **Configuration:** the agent is set up entirely through `RL_*` environment variables, which `start_server()` in `03_run_experiments.sh` sets. db_bench gets its settings the same way, through `RL_COMPACTION_*` variables in that script.
- **Request flow:** `server.py` passes each request to `multilevel.py`, the protocol-v2 processor.
- **Agent:** `agent.py` holds `DQNAgent`.
- **Model:** `model.py` has a shared trunk with a separate two-action head for each level.
- **Q-values:** Q is an analytic prior (`analytic_advantage`) plus a learned residual. The residual starts at zero, so a cold start behaves exactly like the prior.
- **Reward:** `reward.py` gives every level decision in a frame the same reward, based on the potential of the whole tree.

### Analysis code (`scripts/dbbench_pipeline/*.py`)

- `pipeline_stats.py` has the paired Student-t helpers.
- `slo_statistics.py` has the tolerance bounds used by stage 06.
- `research_objective.py` loads the frozen contract.
- Formal space amplification is the SST bytes measured before compaction divided by `estimate-live-data-size`.
- The scan objective is sorted-run seeks, because scan amplification is already at its floor.

### Legacy code

`src/`, `include/`, `lib/tectonic/` and `workload_specs/` belong to the older workflow that ran Tectonic workload files. The pipeline doesn't use them, because db_bench generates its own workload: a `filluniquerandom` load followed by `mixgraph`. `workload_specs/README.md` still mentions `scripts/workload_generator.sh`, which no longer exists.

## Repo gotchas

- **`.gitignore` hides new files.** It ignores whole categories (`*.json`, `*.txt`, `/docs/*`, `scripts/*`) and then re-includes specific paths with `!` rules. New JSON configs, new docs, and scripts in new directories end up untracked without warning. Add a `!` rule for them.
- **Some tools skip all the RL C++.** The root rule `**/db/**` is meant for database working directories. Git doesn't apply it inside the submodule. Tools that apply the root `.gitignore` recursively, such as graphify, skip `lib/rocksdb/db/`, which is where all the RL C++ lives. If a search comes back empty, search `lib/rocksdb/db/compaction/` explicitly.
- **All C++ changes are in the `lib/rocksdb` submodule.** It is the fork `chill-umb/rocksdb`, and the working branch is `rl-compaction-policy-new`.
  - Commit inside the submodule first, then bump the submodule pointer in the root repo.
  - The research contract pins the RocksDB base at `7ea2d73`, and later commits must record their parent.
  - The `*.cc.d` files next to the sources are make dependency outputs.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
