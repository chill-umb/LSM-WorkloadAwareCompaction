# CLAUDE.md

## First rule

Before starting a conversation, check which model is running. If it is Fable 5.1, confirm with the user that they want to use it.

## Project

A user-facing SLO controller for RocksDB compaction, restarted from scratch on 2026-09-27. The controller changes only the inputs RocksDB scores against: the L0 trigger `k` and a per-level score multiplier `mᵢ`. RocksDB's own picker still chooses every compaction. In each detected workload phase, the controller minimises the user's priority metric (reads or writes) while the other metrics stay inside a (1+β) box around the reachable knee.

| Question | Source of truth |
|---|---|
| Requirements, theory, what would falsify each claim | `docs/rl_compaction_feasibility_v4.pdf` (Revision 4); verdict table in §12 |
| What to build, invariants, flag defaults, tests, milestones | `docs/ROCKSDB_PATCH_ARCHITECTURE.md` (v2.1) |
| Facts checked on our RocksDB version, and every deviation from the architecture doc | `docs/PATCH_NOTES.md` (written by milestone M0) |

**Read §0 of the architecture doc before any implementation work.** It holds the rules for Claude Code: milestone order, the ban on editing picker files, and "stop and report" when a value or symbol is missing.

The pre-restart project (per-level DQN trigger, objective contract v3) lives on root branch `dqn-poc-new` and submodule branch `wt-pathways-consistency`. Its `PATHWAYS.md`, `PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md`, `PREREGISTRATION.md` and `research_objective_contract.v1–v3.json` have been stale since 2026-09-27. Open them only when the user asks.

## Where code lives

| Part | Location |
|---|---|
| RocksDB core patch: option, score scaling, supervisor hook (architecture Part A) | `lib/rocksdb/` |
| C++ controller library and `db_bench` wiring (Part B, §13) | `lib/rocksdb/tools/rl_controller/`, `lib/rocksdb/tools/db_bench_tool.cc` |
| Python learner and offline tools (Part C, §16) | root `rl_controller/`. This deviates from the doc's `tools/rl_controller/` and is recorded in `docs/PATCH_NOTES.md` |

- `lib/rocksdb` is the fork `chill-umb/rocksdb` on branch `user-facing-slo-rocksdb`. That branch starts from **stock RocksDB 11.1.1 at `6cdeb9d9d`**, which is the stock build: arm A of the A/B harness and the "unpatched build" of invariant I1. The fork's older `7ea2d73` already carries old RL code, so it is never a stock baseline.
- Commit inside the submodule first, then bump the pointer in the root repo (branch `user-facing-slo`).
- `src/`, `include/`, `lib/tectonic/`, `workloads/`, the root `CMakeLists.txt` and `scripts/manage.sh`/`setup.sh` belong to the legacy `db_runner`/Tectonic workflow from `main`. The controller does not use them.
- RocksDB's own conventions (registering new `.cc` files, adding options, writing unit tests) are in `lib/rocksdb/CLAUDE.md`.

## Build and test

- **RocksDB builds and C++ test runs happen on the Chameleon node** (CHI@NCAR, Zen 5). This machine never builds RocksDB. When a step needs the node, give the user the exact command.
- **Python tests run locally**, and must pass here before anything goes to the node, so that a long node run never fails on an error a local test would have caught.
- The local check is syntax-only: run `g++ -fsyntax-only` with the flags recorded for `db/version_set.cc` in `build/compile_commands.json`. That file comes from a legacy build tree. Files it lacks, such as `tools/db_bench_tool.cc` and new `tools/rl_controller/` sources, reuse the same flags.
- **Tests follow architecture §15.** A milestone is done when its listed tests pass: T and C tests on the node in a debug build (assertions on), using RocksDB's framework (gtest, `DBTestBase`, `SyncPoint`); P tests locally with `pytest`. Commit each milestone separately.
- Python uses the standard library plus `numpy`, and `scipy` for M8 only.
- Node build scripts are `scripts/dbbench_pipeline/00_install_dependencies.sh` and `01_build.sh` (`release` for `db_bench`, `debug <test>...` for named test binaries), with defaults in `config.sh`. Every binary gets a `.provenance` file next to it (commit, dirty flag, compile command, sha256). Run them with `bash`: this checkout sits on NTFS, so git does not record the executable bit unless it is added with `git add --chmod=+x`.

## Measurement

- **Paper mode** (direct I/O on, WAL on, sync off) produces every reported number, the knee table, the hull and the `--rl_mu0` calibration. Iteration mode (both off) is only for debugging and correctness tests (feasibility §9.2, architecture §15).
- Paper mode is slow. In the old configuration the node ran 2,750 ops/s with direct I/O, against 58,332 buffered (10M ops, T=2). Measure throughput once in the new configuration before sizing a sweep.
- Put the database on the device being measured. `/tmp` is tmpfs here, so a run there would measure RAM.
- **Every long node run gets a short trial first**: the same script, flags and mode, with fewer operations. Bugs are found in the trial, never in the long run.
- The hull and the knee table belong to the `db_bench` binary that measured them. Re-measure both whenever the binary that runs the policy changes.

## Repo gotchas

- The root `.gitignore` rule `**/db/**` makes any tool that applies it, graphify included, skip `lib/rocksdb/db/`, where the scoring patch lives. If a search there comes back empty, search that path explicitly.
- Blanket ignore rules can silently hide a new file. After adding one, confirm it is tracked with `git status` or `git check-ignore -v <path>`.
- `graphify-out/` was built from the old branch. Run `graphify update .` before trusting it, and again after modifying code.

## Talking guidelines

- Talk in plain, simple English. Assume the reader is a CS student with a basic grasp of LSM trees, and when a technical term is needed, explain it in a few words.
- End each response with the next immediate task, including the exact command when it runs on the node.
- For each result, give a verdict (good or bad) against the matching criterion: the feasibility doc's "Falsified by" column (§12), or the architecture doc's pass conditions (§15 tests, §16.2 A/B equivalence).
