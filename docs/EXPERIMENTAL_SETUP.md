# Experimental Setup

**Purpose.** Source text for the paper's setup section, and the single place
where every experimental control is written down. Values here are the ones the
pipeline actually applies; each is reproduced per arm in `metadata.env` so a
result can be checked against this document rather than trusted against it.

**Status.** Current as of 2026-09-12, against research objective contract v3
(SHA-256 `87eaddbcf64529760d91ff139e3c2a4db3787437bfd0b67a93394a4ab8a64bf6`).
Gates 0 and 1 are complete; Gates 2 to 6 have not been executed. Gate 1's
result is recorded in `PATHWAYS.md` and in the project history. The
`suite-20260902-193415` artifacts were produced on different hardware with a
different binary and are pilot evidence only.

---

## 1. Hardware and operating environment

| Property | Value |
| --- | --- |
| Platform | Chameleon Cloud, CHI@NCAR, bare metal |
| Processor | AMD EPYC 4545P, Zen 5 |
| Cores | 16 physical, SMT disabled, so 16 logical CPUs |
| Sockets / NUMA nodes | 1 / 1 |
| Last-level cache | 2 domains, CPUs 0-7 and 8-15, no L3 shared between them |
| Operating system and kernel | recorded per arm in `environment.env` (`uname -a`) |
| Storage device and filesystem | to be recorded from the lease; `DB_ROOT` is placed on the device under measurement, never on tmpfs |

Every run is single-tenant on a dedicated bare-metal lease. Absolute runtimes
are not comparable across leases or node families; all reported comparisons are
paired within a lease.

## 2. Build and toolchain

The measured RocksDB library and `db_bench` are compiled for a named
microarchitecture rather than for the host:

| Setting | Value |
| --- | --- |
| Target architecture | `-march=znver5` |
| Mechanism | RocksDB's `PORTABLE` CMake cache variable, set to `znver5` |
| Build type | `Release`, `-O3`, `-DNDEBUG` |
| Minimum toolchain | GCC 14.1 or Clang 19 |
| Compiler used | recorded per arm as `build_cxx` in `metadata.env` |

RocksDB's `PORTABLE=0` emits `-march=native`. On a compiler that does not
recognise the host CPU, `native` silently resolves to a generic or older
microarchitecture, which would change every measurement without any visible
signal. Naming the architecture converts that failure into a configure-time
error. The build script refuses to proceed if the toolchain does not accept the
target, and names `znver4` as the fallback to record as a deviation.

The resulting binary is not portable across node families. Its SHA-256 is part
of the experiment fingerprint (§10), so a binary built with different flags
cannot be paired with one built before it.

## 3. Source provenance

| Component | Commit |
| --- | --- |
| Project repository | `6b482e316f17281e9365872f6e8b11cf6ae8e494` |
| RocksDB fork (`chill-umb/rocksdb`, branch `rl-compaction-policy-new`) | `81d2742bf05883fa9978a9e984e32f709409444f` |
| RocksDB parent, and the base the contract pins | `7ea2d73655025332855864f0b8d6d2dbdad3f336` |
| RocksDB version string | 11.1.1 |

Source-level claims, cited line numbers and option semantics are valid against
the pinned RocksDB commit and must be re-verified if it moves. Each arm also
stores both repositories' revisions, `git status`, and binary worktree patches,
so a run is reconstructible even from an uncommitted tree.

## 4. Storage engine configuration

Applied identically to every arm. The RL arms differ from `regular` only in
compaction style and in the controller that gates compaction triggers.

| Option | Value |
| --- | --- |
| `key_size` | 64 B |
| `value_size` | 960 B, fixed (`value_k=0`, `value_sigma=0`) |
| `compression_type` | none |
| `write_buffer_size` | 2 MiB |
| `target_file_size_base` | 512 KiB |
| `max_bytes_for_level_base` | 16 MiB |
| `level_compaction_dynamic_level_bytes` | `false` |
| `num_levels` | 13 |
| `max_background_jobs` | 2 |
| `open_files` | 1000 |
| `cache_size` (block cache) | 8 MiB |
| `block_size` | 4 KiB |
| `bloom_bits` | 10 per key |
| `level0_file_num_compaction_trigger` | 4 |
| `level0_slowdown_writes_trigger` | 20 |
| `level0_stop_writes_trigger` | 36 |
| `compaction_pri` | 3, `kMinOverlappingRatio` |
| `soft_pending_compaction_bytes_limit` | 64 GiB |
| `hard_pending_compaction_bytes_limit` | 128 GiB |
| `disable_wal` | 1 |
| `use_direct_reads` | `false` |
| `use_direct_io_for_flush_and_compaction` | `false` |
| `compaction_readahead_size` | 2 MiB, the pinned tree's default |
| `threads` | 1 |
| `statistics`, `histogram`, `perf_level` | on, on, 1 |
| `stats_dump_period_sec` | 20 |

Level targets are therefore static: L1 is 16 MiB and L*i* is 16 MiB × T^(i−1).
`num_levels` is held at 13 for every size ratio so that varying T cannot
silently vary the available tree depth. The L0 thresholds are held fixed across
T rather than derived from it, so the size ratio and the L0 trigger are
independent experimental variables.

Setting the pending-compaction byte limits to zero does not disable them in
RocksDB; it creates immediate and permanent write pressure. Explicit large
limits are used to avoid that confound.

Direct I/O is pinned off on both the read path and background flush and
compaction, so reads are served through the host page cache. Enabling it was the
original decision and was reversed on measurement: a 10M T=2 pilot ran mixgraph
at 2,750 ops/s with direct I/O and 58,332 ops/s buffered, a 21 times penalty
that puts the Gate 1 sweep near 95 hours. An earlier revision of this paragraph
quoted 2.6 times, comparing against a buffered figure from the 2026-09-03 suite
on different hardware; that cross-machine comparison was invalid. About
40% of that gap was table-open churn, 7,509 SST files against a 1,000 file open
limit with index and filter blocks held outside the 8 MiB block cache; lifting
the limit recovered that share and no more.

The consequence is that the roughly 3.7 GB database sits in page cache, so
latency, runtime and stall figures are warm-cache measurements. They are
reported as bounds on foreground disruption, not as storage-latency claims. The
amplification metrics are unaffected, because write, point-read and space
amplification, sorted-run seeks and per-level merge survival are byte ratios
determined by tree shape rather than by where bytes are cached. The rejected
alternative was a cgroup memory cap on the benchmark process, which is external
state the experiment fingerprint cannot carry and a new lease silently loses.
The setting may be enabled for the final paper benchmark workload if the
amplification results justify the cost; those runs carry the `dio1` fingerprint
field, cannot be pooled with `dio0` runs, and need a regenerated comparator
manifest because stage 06 selects on runtime.

## 5. Workload

`db_bench` generates the database and the workload internally. Each arm runs one
measured command:

```
rlsuspend -> filluniquerandom -> resetstats -> rlresume -> mixgraph
          -> waitforcompaction -> levelstats -> stats
```

`rlsuspend` / `rlresume` hand the bulk load to RocksDB's native leveled
compaction in every arm, so the controller receives its first frame at the
first measured operation and every arm reaches `mixgraph` on the same tree.
`resetstats` zeroes the statistics after the load, so every ticker- and
histogram-derived metric (write amplification, stalls, latency, probes, seeks)
covers the measured phase — `mixgraph` plus the drain — and never the load.
Both are recorded under P1c (2026-09-20) before any run of the programme.

| Property | Value |
| --- | --- |
| Initial unique inserts | 29% of total operations |
| Mixed phase | 71% of total operations |
| `mix_get_ratio` | 0.5211267606 |
| `mix_put_ratio` | 0.1549295775 |
| `mix_seek_ratio` | 0.3239436620 |
| Scan length | 32, fixed (`iter_k=0`, `iter_sigma=0`) |
| `mix_max_scan_len` | 10000 |
| `keyrange_num` | 1, so key selection is uniform with no hotspot |
| Workload seed | 1, shared between the arms of a pair |
| Policy seed | distinct per cell, independent of the workload seed |

The operation mix reproduces the time shares of the project's corrected 5M
balanced workload. `db_bench` substitutes Put traffic for that workload's
explicit deletes, so the two are comparable in pressure mix but not
operation-identical, and this workload family produces no tombstones. Update
skew, workload phases and explicit deletes are introduced later, as Pathway B.

`waitforcompaction` is inside the measured command sequence, so a policy cannot
appear efficient by ending the run with unsettled compaction debt. Drain wall
time, pending bytes before and after, and drain compaction bytes are reported
separately, while the headline write amplification and runtime retain the drain.
A full compaction runs after the measured sequence purely as a garbage-free size
reference; its I/O is excluded from every reported metric.

### Matrix

| Axis | Values |
| --- | --- |
| Total operations | 10M for Gates 1 and 3b; 20M only if a 10M result is close enough that scale could flip it |
| Size ratio T | 2, 6, 10; `regular` additionally at 14 and 20 for the cross-T hull |
| Arms | `regular`, `oracle`, `prior_only`, `rl`, `unconstrained_rl` |
| Paired repeats | 10 preregistered for any acceptance claim; 3 to locate hull points, topped up |

`regular` is stock leveled RocksDB and is the baseline arm. `oracle` is a
deterministic trigger that opens every due level, used to measure the control
bridge's transparency. `prior_only` runs the analytic prior with no learned
residual. `rl` is the constrained learner. `unconstrained_rl` disables the live
SLO mask and is an ablation only, never an acceptance arm.

## 6. Compaction controller

| Property | Value |
| --- | --- |
| Protocol | v2, trigger-only, pinned |
| Action space | binary per level: 0 defer, 1 compact |
| File selection | RocksDB's native `kMinOverlappingRatio` picker, in every arm |
| Decision and observation interval | 50 ms each, required equal |
| Socket timeout | 250 ms |
| Structural staleness deadline | 250 ms |
| Minimum score for an optional action | 0.10, shared by both processes |
| L0 deferral | disabled; an L0 defer is promoted and attributed to `kPosture` |
| Crossing posture | a defer selected below the trigger does not bind once the level becomes due |
| Exploration | annealed on wall time, one third of the expected run duration |
| Credit assignment | schema v2, decision IDs echoed, overrides relabelled to the effective action and kept in replay |
| Bulk load | controller suspended (`rlsuspend`); native leveled compaction, attributed `kSuspended`, in every arm |
| Reward | the constrained objective: point-read probes per Get as a rate, plus hinge penalties above the manifest bounds for write amplification (windowed, +2%), space amplification (the rung), latency (average and p99, +2%), sorted-run seeks (+2%) and stall fraction (0%), each with a dual-ascended multiplier; shaping over the absolute sorted-run count |
| Analytic prior | L0 read relief in absolute runs, net of the run native RocksDB would remove one flush later (minimum reduction 2); deep levels earn no read relief and are charged for populating an empty level |
| Initialisation | cold start every run; the learned residual is zero-initialised, so step-zero behaviour equals the analytic prior |

No pretrained weights or checkpoints are loaded. The SLO manifest supplied to
the controller contains measurements and limits only, never learned parameters.
The controller never names an SST: an affirmative action opens a level-scoped
gate, and RocksDB performs all file selection, clean-cut expansion, overlap
discovery and conflict checking.

## 7. Process isolation

`db_bench` and the Python controller are pinned to disjoint cores that share no
last-level cache:

| Process | CPUs |
| --- | --- |
| `db_bench`, including its background compaction and flush threads | 0-7 |
| Python controller | 8 |
| Unused | 9-15 |

Both sets are applied in every arm, including `regular`, which starts no
controller. An arm that had the controller's cores to itself would not be
comparable with one that did not, and the difference would land in exactly the
paired comparison the evaluation reports.

The runner refuses to start if the two sets overlap, name an offline CPU, or
share a last-level cache on a machine that offers a disjoint choice. `taskset`
is used rather than `numactl`: the node has one NUMA node, so memory binding is
a no-op. The controller is single-threaded by construction
(`OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `torch.set_num_threads(1)`), so one
core does not induce thread contention.

## 8. Metric definitions

| Metric | Definition | Source |
| --- | --- | --- |
| Write amplification | (flush bytes + compaction bytes written) / user logical bytes written, over the measured phase (statistics reset after the bulk load; the drain is included) | `rocksdb.flush.write.bytes`, `rocksdb.compact.write.bytes`, `rocksdb.bytes.written` |
| Point-read amplification | logical SST probes per Get, counting a probe rejected by a Bloom filter | `rocksdb.point.sst.probe` |
| Scan amplification | (returned entries + internal entries skipped) / returned entries; diagnostic only | `rocksdb.number.iter.skip` and returned entries |
| Sorted-run seeks per scan | sorted runs opened per keyed scan seek — each L0 file and each non-empty deeper level once; a scan crossing a file boundary inside one level opens no further run — / scan operations | `rocksdb.sorted.run.seek` |
| Space amplification | settled SST bytes before the reference compaction / `rocksdb.estimate-live-data-size` | size capture and DB property |
| Stall duration | `rocksdb.stall.micros`, measured phase | ticker |
| Stall fraction (learner only) | stall seconds / measured-phase wall seconds (`rlresume` to drain end) | ticker and `db_bench` timestamps |
| Latency | Get, seek and write histograms, P50/P95/P99/P100, COUNT, SUM | `db_bench` histograms |

Point-read amplification counts logical run-search work rather than physical
I/O, because the block cache and Bloom filters can change physical reads without
changing how many runs the tree exposes. Scan amplification sits at its
mathematical floor of 1.0 on this workload and is therefore reported as a
diagnostic only; the scan objective rests on sorted-run seeks per scan.

## 9. Instrumentation added to RocksDB

Two event records were added for Gate 0 and are emitted by the pinned commit.

**Merge survival.** `compaction_finished` carries `merge_schema_version` 1 with
the job's input and output SST bytes, its source level, and its success flag.
Per-level merge survival η is output bytes over input bytes with trivial moves
excluded from both, computed separately for the workload phase, the drain phase
and the whole run.

**Release-time occupancy.** A `compaction_release` event is written under the DB
mutex immediately before an admitted job executes, carrying per-level occupancy,
nominal and effective level targets, the capacity scale vector and generation,
the originating decision ID and the override reason. It is recorded at admission
rather than at completion because other jobs may already have changed downstream
headroom by then.

`compaction_measurements.py` parses both into `compaction_measurements.json`
per arm, and the runner fails the arm if the instrument is incomplete.

## 10. Statistical protocol

- The statistical unit is the paired repeat: same workload cell, workload seed,
  binary, database image and objective manifest. Arm order alternates between
  pairs.
- Comparisons are paired relative differences with two-sided 95% Student-t
  intervals.
- A non-inferiority check passes when the upper bound is at most its margin,
  fails when the lower bound is above it, and is **undecidable** when the
  interval crosses it. An underpowered check is never reported as a failure.
- A strict-improvement check passes when the upper bound is below zero and fails
  when the lower bound is at least zero.
- Missing objective data fails closed.

| Quantity | Test | Margin |
| --- | --- | --- |
| Point-read amplification | strict improvement | 0 |
| Write amplification | non-inferiority | 2% |
| Sorted-run seeks per scan | non-inferiority | 2% |
| Get, scan, write latency, average and p99 | non-inferiority | 2% |
| `stall_seconds` | non-inferiority | 0% |
| Space amplification | non-inferiority, swept | 0, 2, 5, 10% |

The comparator is not a single tuned configuration but the Pareto hull of the
static configuration class over (W, R), reported per size ratio and pooled
across size ratios. Arms carrying a capacity action are compared against the
hull that also contains statically capacity-expanded configurations; arms
without one are compared against the hull at unit scale.

## 11. Provenance and reproducibility

Each arm writes its own directory containing the exact command line, the
effective configuration, both repositories' revisions and worktree patches, the
environment capture, the RocksDB `LOG`, the raw run log, per-level compaction
measurements, pressure episodes, the trigger trace, and, for learned arms, the
policy decision log, per-decision Q/prior/residual values, the learner health
summary and the server summary.

Every arm carries an `experiment_fingerprint` combining the workload profile,
operation count, size ratio, record and tree geometry, L0 thresholds,
compaction priority, mix ratios, cache and Bloom settings, background job count,
thread count, WAL setting, pending-byte limits, the `db_bench` SHA-256 and the
objective contract SHA-256. Three independent places refuse to combine
mismatched fingerprints: the runner will not start an arm against a manifest
from another geometry, the paired evaluator will not pool repeats across
fingerprints, and the frontier analysis requires one identity across the grid.

## 12. Declared deviations from RocksDB defaults

| Deviation | Default | Used | Reason |
| --- | --- | --- | --- |
| `level_compaction_dynamic_level_bytes` | `true` since v8.4 | `false` | The capacity mechanism presumes a static ladder; under the dynamic mode targets are computed top-down, per-level multipliers are ignored, and scaling leaks into L0's byte trigger |
| `PORTABLE` | 0, meaning `-march=native` | `znver5` | Prevents a silent architecture downgrade |
| WAL | enabled | disabled | Isolates compaction behaviour from log I/O |
| Compression | Snappy in many builds | none | Keeps SST bytes a direct function of logical bytes |
| `use_direct_reads`, `use_direct_io_for_flush_and_compaction` | both `false` | both `false` | No deviation. Pinned explicitly and fingerprinted so the choice is declared rather than inherited |
| L0 thresholds | derived from the size ratio in earlier tooling | fixed 4 / 20 / 36 | Makes the size ratio and the L0 trigger independent variables |

## 13. What this setup does not control

- CPU pinning is recorded per arm but is **not** part of the experiment
  fingerprint, so an arm pinned differently would still pair without complaint.
- `taskset` does not prevent the kernel from scheduling its own work on the
  pinned cores. Stronger isolation, via `isolcpus` or a cgroup cpuset, has not
  been applied.
- **The page cache is uncontrolled.** Buffered I/O is pinned, and the database
  fits in the node's RAM, so latency, runtime and stall figures are warm-cache.
  They bound foreground disruption and are not storage-latency claims. The
  amplification metrics and the Pareto hull are unaffected.
- One workload family only: uniform key selection, no tombstones, no phase
  changes. Skew, deletes and workload shift arrive with Pathway B.
- The binary is architecture-locked and must be rebuilt on each new lease.
- Latency intervals at five repeats span far more than the 2% margin, so
  latency claims require the preregistered ten repeats.
- The `write_latency_p95_us` below `write_latency_avg_us` observation is
  legitimate for a heavy-tailed stalled-write distribution but its histogram
  parsing has not yet been independently verified, and it must be resolved
  before any latency figure appears in a submitted table.
