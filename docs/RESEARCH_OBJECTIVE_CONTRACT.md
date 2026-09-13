# Research Objective Contract v3

**Status:** frozen on 2026-09-12 (P0, P1 and P1b complete)

**Machine-readable counterpart:**
[`../config/research_objective_contract.v3.json`](../config/research_objective_contract.v3.json)

**Supersedes** v2, SHA-256 `d8e1d0fbb152c061ccedc1e78a5b19eb25bae225c431f4865459efad883ddc00`, which superseded v1, SHA-256
`0cdc1fd13e230762be0ba519f11ef74baca3d79068a1956374183a0559787c90`. Both stay in
the tree as superseded records, and **no run was executed under either**.
Nothing in the objective, the constraints, the margins or the decision rules has
changed across the three versions. v2 added the P1 amendments, recorded
2026-09-11 before any run of the programme, the build and measurement-environment
pins, and updated source provenance. v3 adds the direct-I/O pin, recorded before
Gate 1 because it moves the baseline and therefore the Pareto hull, and could
not honestly be adopted once the hull had been measured.

This contract governs the post-2026-09-05 Pareto-frontier programme. Changes
after this date require an explicit version bump and a written reason; they must
not be selected after inspecting a formal gate's outcomes.

## Statistical unit and baseline

- The baseline arm is `regular`.
- The statistical unit is the paired repeat: same workload cell, workload seed,
  binary, database image, and objective manifest.
- Metric comparisons use paired relative differences. Intervals are two-sided
  95% Student-t intervals on those differences.
- A non-inferiority check passes when its upper interval bound is at most its
  margin, fails when its lower bound is above the margin, and is `undecidable`
  when the interval crosses the margin. Missing objective fields fail closed.
- A strict-improvement check passes when its upper bound is below zero, fails
  when its lower bound is at least zero, and is otherwise `undecidable`.

## Objective and constraints

The primary objective is to minimise `point_read_amplification` subject to the
following constraints:

1. `write_amplification` is non-inferior to `regular` at
   \(\delta_W=0.02\) (2% relative regression).
2. `space_amplification` is evaluated as a W-R-S surface at relative regression
   budgets \(\{0, 0.02, 0.05, 0.10\}\), not at one post-hoc-selected bound.
3. `sorted_run_seeks_per_scan` is the scan constraint and is non-inferior at a
   2% relative margin. `scan_amplification` remains diagnostic because the
   current workload leaves it at its floor of 1.0.
4. Average and p99 get, scan, and write latencies are each non-inferior at a 2%
   relative margin. P50, p95, p100, COUNT, and SUM are retained as diagnostics;
   p95 is not the write-tail acceptance statistic.
5. `stall_seconds` is non-inferior at a zero relative margin using the paired
   interval rule. `stall_events` is diagnostic.

The primary paper claim is write non-inferiority, strict point-read improvement,
and non-domination against Hull\(_s\), with state-dependent policy behaviour.
Write improvement is a bonus, not a requirement.

## Phase cost

For phase \(p\), \(J_p=R_p/R_{p,\mathrm{regular}}\) among configurations that
satisfy all frozen constraints at the selected space budget. An infeasible
configuration has \(J_p=+\infty\). If no evaluated configuration is feasible,
the phase minimum and adaptivity gap are reported as undefined rather than
assigned a finite penalty.

## Analytical conventions

- Use write-accounting model M3,
  \(w_i=\tfrac12(f_i+1)\), for analytical optimisation.
- Empirical write amplification remains the ratio derived from RocksDB physical
  write counters and user logical write bytes.
- Every empirical comparison to Corollary A.4 uses \(W-1\), with the denominator
  stated explicitly.

## Capacity and policy conventions

- Pin `level_compaction_dynamic_level_bytes=false` and record the deviation
  from the RocksDB default in every formal manifest.
- Action 2 may expand only levels 1 through L-1, only when the level is above
  its nominal target, the observed workload is write-oriented, read/debt safety
  permits expansion, and the projected scale is within measured \(s_{\max}\).
- Under read pressure or unsafe debt the cold-start prior selects compaction;
  otherwise it defers without expansion.
- Safety-, drain-, or maintenance-forced contraction is an attributed override:
  requested and effective actions remain distinct, it is excluded from Q replay,
  and its telemetry and realised reward are retained.
- The question of a separately rate-limited debt-release actuator is explicitly
  deferred by the project owner. It is not a P0 blocker and must be revisited
  before P8 is accepted for formal runs.

## Workload and artifact provenance

- Delete rates are a synthetic \(\{0,3,6\}\%\) sweep on the Assoc key
  distribution and must be labelled synthetic. They must not be attributed to
  the published UDB Assoc mix.
- Existing `prior_only` runs from `suite-20260902-193415` are pilot evidence
  only. Formal Gate-1 comparisons rerun `prior_only` with the new binary,
  objective manifest, and fingerprint chain.
- Source claims are pinned to project commit
  `6b482e316f17281e9365872f6e8b11cf6ae8e494` and RocksDB commit
  `81d2742bf05883fa9978a9e984e32f709409444f`, whose parent is
  `7ea2d73655025332855864f0b8d6d2dbdad3f336`. That RocksDB base is the one v1
  pinned and is preserved. Later implementation commits must likewise record
  their parent and preserve the base unless the contract is amended.
- \(\kappa_i\) is occupancy divided by the nominal level target at outward
  compaction release, measured per level and repeat under the compatible
  capacity-off learned policy. P17a supplies its value; a selected-action-rate
  ratio is not a substitute.


## Build and measurement environment (added in v2)

- The measured RocksDB and `db_bench` build targets `-march=znver5`, set through
  RocksDB's `PORTABLE` cache variable rather than left at `PORTABLE=0`.
  `PORTABLE=0` emits `-march=native`, which degrades silently to a generic
  microarchitecture on a compiler that does not recognise the host CPU, so the
  architecture is named and an unsupported toolchain is a configure-time
  failure. Minimum toolchain is GCC 14.1 or Clang 19.
- The measurement node is a single-socket AMD EPYC 4545P: 16 cores, SMT off,
  one NUMA node, cores 0-7 and 8-15 in separate last-level-cache domains.
- Direct I/O is pinned **off** for the read path (`use_direct_reads`) and for
  background flush and compaction
  (`use_direct_io_for_flush_and_compaction`), with `compaction_readahead_size`
  at the pinned tree's 2 MB default. Enabling it was the original decision and
  was reversed in place on 2026-09-12, before any Gate 1 run, on measured cost:
  on the measurement node at 10M T=2, mixgraph ran at 2,750 operations per
  second with direct I/O and 58,332 buffered, a 21 times penalty putting the
  Gate 1 sweep near 95 hours. An earlier revision of this clause quoted 2.6
  times, comparing against a buffered figure from the 2026-09-03 suite on
  different hardware; that cross-machine comparison was invalid and is
  retracted here rather than silently replaced. The consequence is that the database
  fits in the host page cache, so latency, runtime and stall figures are
  warm-cache and are reported as bounds on foreground disruption rather than as
  storage-latency claims. Write, point-read and space amplification, sorted-run
  seeks and per-level merge survival are byte ratios fixed by tree shape and are
  unaffected, so the Pareto hull is cache-invariant. The rejected alternative
  was a cgroup memory cap on the benchmark process, which is external state the
  fingerprint cannot carry and a new lease silently loses. The setting may be
  enabled for the final paper benchmark workload if the amplification results
  justify the cost; those runs carry fingerprint field `dio1`, cannot be pooled
  with `dio0` runs, and require a regenerated comparator manifest because stage
  06 selects on runtime, which is cache-sensitive. The setting is pinned, never
  swept, and recorded as the `dio` field of the fingerprint and as
  `use_direct_io` in `metadata.env`.
- The `db_bench` binary's SHA-256 is part of `experiment_fingerprint`, so a
  build with different flags cannot be paired with one built before it.
- `db_bench` and the Python controller run on disjoint cores that share no
  last-level cache, in every arm including `regular`, which starts no
  controller. The two sets are recorded per arm as `dbbench_cpus` and
  `controller_cpus` in `metadata.env`. This is provenance only: the paired
  evaluator does not yet refuse an arm pinned differently, because the sets are
  not part of `experiment_fingerprint`.
