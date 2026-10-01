# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Most important rule

Before starting a conversation, always check the model being used and if the model is Fable 5.1, always confirm whether I want to use this model. 

## Project

This is research code for **Programme 1: a per-level compaction-trigger controller for leveled RocksDB**. The controller is a separately loaded C++ plugin (`controller/`). It changes only each level's target multiplier and the L0 trigger, through RocksDB's own compaction scores, so RocksDB keeps sole authority over which files are compacted. It minimises a priced cost of write, read and space amplification under a chosen priority (reads, writes or space).

The earlier design, a Python DQN answering `defer`/`compact` per level over a socket (protocol v2, `RLCompactionPicker`, `rl_agent/server.py`), was retired on 2026-09-27. Its code stays until plan step 12 (WP10); its record is the history document.

### Where authority lives

| Topic | File |
| --- | --- |
| Forward plan, gates, acceptance criteria, theory | `docs/PATHWAYS.md` (`docs/PATHWAYS.tex` is a generated LaTeX reading copy; regenerate it whenever PATHWAYS changes) |
| Build plan, work packages, test suites | `docs/IMPLEMENTATION_PLAN_PROGRAMME1.md` |
| Dated decisions, predictions made in advance, gate verdicts | `docs/PREREGISTRATION.md` |
| Research objective contract | `config/research_objective_contract.json` (machine-readable Programme 1 values; the prose is `docs/PREREGISTRATION.md` D-13) |
| Experimental controls, paper setup text | `docs/EXPERIMENTAL_SETUP.md` (as of 2026-09-12 and contract v3; not yet updated for Programme 1) |
| Historical record | `PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md` |
| Pipeline operation | `scripts/dbbench_pipeline/README.md` |

- **`docs/PATHWAYS.md` is the only forward plan.** It holds only theory, specification and done/not-done status. Nothing else defines what to do next. Its vocabulary: `A-Impl-N`, Gates `N0`–`N6`, and criteria prefixed OBJ, ACT, PROP, ARCH, WL and CMP.
- **`docs/PREREGISTRATION.md` holds every dated decision and verdict.** Put new ones there, never in PATHWAYS. Its commit date is the only proof that a decision predates the runs it governs, so it must stay tracked, and an entry is never changed after a run it governs: a new decision is a new dated entry. On 2026-10-01 the owner had D-1..D-12 and the old verdict records condensed to summaries; D-13 to D-16 are unchanged, byte for byte, and the full text before condensation is `git show cb45743:docs/PREREGISTRATION.md`.
- **`PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md`** is the record of the retired programme, condensed on 2026-10-01 (full text at `cb45743`). Every section number cited elsewhere is kept, §12 stays unused, and §18.1 holds the programme summary moved from PATHWAYS' former Appendix R.
- **The objective contract is `config/research_objective_contract.json`** (Programme 1, 2026-09-30). Contracts v1–v3 were deleted on the owner's instruction on 2026-09-30 and are recoverable from commit `5bea343`. Edit the file in place with a written reason, never make a versioned copy, and never change it after seeing a gate's outcome.

## Commands

### Build

**Do not build RocksDB on this machine.** Builds happen on the Chameleon node (CHI@NCAR, Zen 5). The sanctioned local check is syntax-only against the compile database of the root CMake tree `build/` (the root `CMakeLists.txt` only configures `lib/rocksdb`, for this purpose):

```bash
g++ -fsyntax-only $(...flags from build/compile_commands.json...) lib/rocksdb/db/<file>.cc
```

Check every changed fork file with both the Debug flags and the Release flags (`-fno-rtti -DNDEBUG`, because 01 builds with `USE_RTTI=AUTO`). `controller/tests/run_local.sh` builds and runs the plugin's own tests locally (owner exception, 2026-10-01); RocksDB itself is never built here.

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
- `LEVEL_TARGET_MULTIPLIERS` (":"-separated, one per level, entry 0 = 1) becomes db_bench's `--level_target_multipliers`, the fork's `level_target_multipliers` option (WP1; empty = all 1).

**Rebuilding.** The experiment runners never build anything. After editing fork C++ or `controller/`, rebuild on the node and rerun the preflight.

RocksDB's own conventions are in `lib/rocksdb/CLAUDE.md`: registering new `.cc` files in `src.mk`, `CMakeLists.txt`, `Makefile` and BUCK; make targets; `make format-auto`. Its test-writing guidance applies to the fork's own test files (see Tests below).

### Tests

**Every piece of Programme 1 code ships with tests, and no multi-hour node run
starts until the preflight passes.** This replaces the 2026-09-13 "no tests"
rule (owner, 2026-09-29). The owner wants every bug found in minutes, not after
an 18-hour run.

The suites come in three tiers. Their layout and full case list are in
`docs/IMPLEMENTATION_PLAN_PROGRAMME1.md` §6, which is the specification until
they exist.

1. **Local, seconds, before every commit.**
   - Python `unittest` suites, stdlib only, in a `tests/` folder beside the
     code they cover (`rl_agent/tests/`, `scripts/dbbench_pipeline/tests/`).
   - `-fsyntax-only` for every changed C++ file.
2. **Node, build.**
   - The fork's own gtest files and the controller plugin's tests.
   - Built in a separate Debug tree, so RocksDB's `assert`s fire.
3. **Node, preflight.**
   - A short end-to-end run: build, plugin, trainer, logs and evaluator
     (stage 13, reworked).
   - It writes a marker bound to the `db_bench`, plugin and code hashes.
   - Every long-run driver checks for a matching marker and refuses to start
     without one.

**Where fork tests go.** Fork tests live in new files of the fork's own, such
as `db/level_target_multipliers_test.cc`, registered like any `.cc`. RocksDB's
upstream test files stay exactly as upstream ships them.

**When a test fails.** Fix the code. Change a test only when the specification
in `docs/PATHWAYS.md` changed, and name that change in the commit.

**Suites and gates do different jobs.** A green suite shows the code does what
the specification says. The gates in PATHWAYS decide the research claims. Both
are required.

## Running experiments

`scripts/dbbench_pipeline/README.md` gives the exact commands (its node runbook). Programme 1's node order:
1. Preflight (`13`): tiers 1–2, ACT-1, ACT-4, the evaluator smoke, ARCH-5 and the rules smoke. It writes the `PREFLIGHT_PASSED` marker.
2. Gate N1 (`24_gate_n1_chain.sh`): pilot native arms, the admission test (`19`), the q̄ arms and the prices (`18`).
3. Gate N2 (`25_gate_n2_chain.sh`): the static comparator Θ_s. `26` records q̄; `27` simulates the D-17 screen.
4. Gates N3 onward, in the order PATHWAYS' execution order gives.

**Starting and resuming**
- Every runner requires `CONFIRM_*=YES` before it will start.
- `RESUME=1` skips only arms that have a `COMPLETED` marker. Partial result directories are never deleted automatically.
- `03` refuses to start while a stray `db_bench` or `rl_agent/server.py` process is running, and refuses runs above 2M operations without a matching preflight marker (exit 7).
- Never pull or edit on the node while a long run is going: the marker hashes `rl_agent/`, `controller/` and `scripts/dbbench_pipeline/`.

**Arms and workloads**
- Programme 1 arms: `native`, `static:<profile>` (Θ_s), `hold` and `rules` (the plugin). The old arms (`regular`, `oracle`, `prior_only`, `rl`, `unconstrained_rl`, `unconstrained_prior_only`) stay in `03` until plan step 12.
- The workloads are UDB `Assoc` (D-1) and a 95/5 power-law mix (D-13; never call it Zipfian).
- Direct I/O is pinned **off** (`dio0`): 2,750 ops/s direct against 58,332 buffered at 10M/T=2 on the node, a 21× penalty.

**Disk**
- A failed run keeps its database, roughly 1 GB per million operations.
- Put `DB_ROOT` on the device you are measuring, never on tmpfs `/tmp`.

## Architecture

```
db_bench --rl_plugin=<librl_controller.so> --rl_host_log=<path>
  └─ RLControllerHost             lib/rocksdb/db/rl_controller_host.*, include/rocksdb/rl_controller_host.h
       ├─ tree snapshot, published by the pressure observer only while a plugin is attached
       ├─ host log: H after every flush and compaction, job begin/end records, settle/measure/drain stamps
       └─ controller/ plugin: state, masks, actions, prior, rules, policy, attribution, decision log
            └─ one batched SetOptions(level_target_multipliers, level0_file_num_compaction_trigger)
```

- **The controller only moves scores.** It sets multipliers and the L0 trigger and never names a file. RocksDB's native leveled picker keeps its score ordering, file choice, clean-cut expansion and overlap checks. File-level control (the old protocol v3) was rejected; don't bring it back.
- **Nothing slow runs under the DB mutex.** No inference runs while it is held; the plugin decides on its own thread.
- **Plugin modes:** hold-only (parity, ARCH-5) and rules (Gate N3) are built; prior-only, learned and remote come with plan step 10.
- **Level target multipliers are applied in `PrepareForVersionAppend`.** Every new version copies the `level_target_multipliers` option into `capacity_scales_`, which `MaxBytesForLevel` multiplies in; L0 is never scaled (A-Impl-1). `ColumnFamilyData::ValidateOptions` rejects bad vectors at open and in `SetOptions`, and `db/level_target_multipliers_test.cc` covers it.
- **Tickers are cumulative since open.** db_bench's `resetstats` does not reset them (D-11). The evaluator differences them between host-log stamps; never read a raw ticker as a measured-phase figure.

### Python

- `rl_agent/` is still the retired protocol-v2 DQN (`server.py`, `multilevel.py`, `lagrange.py`, `agent.py`). Plan step 10 replaces it with `trainer.py`, `reward.py`, `model.py` and `membership.py`, and deletes `server.py`, `multilevel.py` and `lagrange.py` (plan §4). Its reward and mask history is PREREGISTRATION D-9..D-12.
- The evaluator is `scripts/dbbench_pipeline/`: `04_generate_graphs.py` (`collect_arm`, measured phase from the host log), `host_log.py`, `compaction_measurements.py`, `frontier_analysis.py` (hull, θ*_β), `07_evaluate_paired.py` with `pipeline_stats.py` (paired Student-t), `18` (prices), `19` (admission), and `research_objective.py`, which loads the frozen contract.
- **Before adding any priced or constrained term, check that its value and its limit are the same quantity, from the same instrument, over the same phase.** Seven instruments failed that test in the old programme (history 14.21–14.24).

## Repo gotchas

- **`.gitignore` hides new files.** It ignores whole categories (`*.json`, `*.txt`, `*.tex`, `scripts/*`) and then re-includes specific paths with `!` rules. New JSON configs and scripts in new directories end up untracked without warning — add a `!` rule for them. Already-tracked paths are unaffected, so the hazard is invisible until a file you expect never shows up in `git status`; `docs/PREREGISTRATION.md` went missing through four commits that way. `docs/` itself is not ignored and needs no `!` rule; `.claude/` is ignored entirely.
- **`core.fileMode=false`** (the drive shows every file as 755): a new script needs `git add --chmod=+x`.
- **Some tools skip all the RL C++.** The root rule `**/db/**` is meant for database working directories. Git doesn't apply it inside the submodule. Tools that apply the root `.gitignore` recursively, such as graphify, skip `lib/rocksdb/db/`, which is where all the RL C++ lives. If a search comes back empty, search `lib/rocksdb/db/` explicitly.
- **All RocksDB changes are in the `lib/rocksdb` submodule.** It is the fork `chill-umb/rocksdb`, and the working branch is `rl-compaction-policy-new`.
  - Commit inside the submodule first, then bump the submodule pointer in the root repo.
  - The research contract pins the RocksDB base at `7ea2d73`, and later commits must record their parent.
  - The `*.cc.d` files next to the sources are make dependency outputs.
- **The fingerprint string in `03` and the regex in `06_select_baseline_slo.py` must stay in lockstep.** `parse_fingerprint_options` is anchored with `fullmatch` and rejects unknown trailing fields. Any new fingerprint field needs the parser updated; emit the segment conditionally if existing runs must stay poolable (this is why the `ltm` segment appears only when multipliers are set; `tests/test_fingerprint.py` checks the pair).
- **The C++ manifest parser reads every key by its first textual match** (old stack). `rl_safety_manifest.cc` searches the raw text for `"key"`, not the top-level field. Never reuse a key name `RLSafetyController::Parse` reads inside a nested manifest block; `06_calibrate_live_guard.py` refuses such a manifest (`CPP_FIRST_MATCH_KEYS`, keep in lockstep with `Parse`).
- **RocksDB's `JSONWriter` has no bool overload**, so `status.ok()` and `rl_drain` reach the event log as `1`/`0`. Never test `is True` against an event-log field; use the tolerant form `in (True, 1, "true", "1")` that `04_generate_graphs.py` already uses.
- **Result layout has a `repeat-NN/` level only when `REPEATS > 1`.**
- **Stage 06 must exclude the level-base scale axis** from comparator selection (`--level-base-bytes`), or a 0.5× configuration can win minimum-space and silently redefine the baseline.
- **The hull is bound to its binary.** The evaluator refuses to pool across `dbbench_sha256`. Any criterion comparing a policy against the hull needs the hull re-measured on whatever binary finally runs that policy.
- **Put repeated pipeline logic in a numbered stage, not a pasted heredoc**, as `24` and `25` do.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- The graph may not cover `docs/` or `lib/rocksdb/db/` (see Repo gotchas). Read those files directly.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).


## Talking Guidelines

# Rules: 
- Talk in plain simple English, rather than using jargons
- Assumne the person you are talking to is a CS student who only has a basic grasp of LSM Tree concepts, so frame your responses so that the person can       understand your responses properly
- If Jargons are necessary, provide a short explanation in simpler terms
- For each response, outline the next immediate task to perform. If some command needs to be executed in the cloud machine, provide the command.
- For each result, provide a verdict whether the result is good or bad according to the corresponding acceptance criteria in PATHWAYS