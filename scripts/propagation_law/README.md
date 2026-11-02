# Cases 1-2 propagation law: code of record

A copy of the law's source from the node, with the same folders as `~/node_ops/diag/kcase/`. Case 1 is the L0 trigger K
raised, case 2 is K lowered. Scores are in `docs/LAW_SCORES.md`; the design is `docs/PROPAGATION_DESIGN.md` §13.

- **`clean/r7/` is the current law**, all its source files, including the earlier versions named `*.pre_*` and `*.g1`.
  `run_adopted.sh` runs the law end to end and writes the parts it uses at its top.
- **The other folders hold only the files the law imports**, found by tracing its imports: `clean/` (`forward.py`,
  `fwd_diag.py`), `clean/r1/`, `clean/r5/` (the baseline the self-check S1 compares with), `learncheck/`, `v2/` and
  `score_k_v1.py`.

**To run it, copy it back to `~/node_ops/diag/kcase/`.** The code uses that path directly, so it does not run from the
repository.

**Data is not here.** The scorers need `~/clean/multi` and `~/clean/dev` (the runs' records and snapshots) and
`clean/r7/ft/`, built by `build_ft.py` from the runs' host logs. Rows and logs (`adopted_*`) are not here either.

Nothing in this folder is part of the pipeline's preflight marker, which hashes `rl_agent/`, `controller/` and
`scripts/dbbench_pipeline/`.
