# LSM Workload Aware Compaction

Research code for a per-level compaction-trigger controller for leveled
RocksDB (Programme 1). The controller is a separately loaded C++ plugin
(`controller/`). It changes only each level's target multiplier and the L0
trigger, through RocksDB's own compaction scores, so RocksDB keeps sole
authority over which files are compacted. It minimises a priced cost of
write, read and space amplification under a chosen priority.

- `docs/PATHWAYS.md`: the specification, proofs and acceptance gates
  (`docs/PATHWAYS.tex` is a LaTeX reading copy).
- `docs/PREREGISTRATION.md`: dated decisions and gate verdicts.
- `docs/IMPLEMENTATION_PLAN_PROGRAMME1.md`: the build plan.
- `PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md`: the record of the retired DQN
  programme.
- `scripts/dbbench_pipeline/README.md`: the numbered stages and the node
  runbook.

Build on the node (Ubuntu or Debian), from the repository root:

```bash
git submodule update --init --recursive
scripts/dbbench_pipeline/00_install_dependencies.sh
scripts/dbbench_pipeline/01_build_rocksdb.sh
scripts/dbbench_pipeline/02_build_db_bench.sh
```

`lib/rocksdb` is the fork `chill-umb/rocksdb`, branch
`rl-compaction-policy-new`.
