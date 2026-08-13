# Workload spec notes

Constraints discovered the hard way (2026-08-02) while building the balanced
spec. None of these are documented in the schema.

## The `balanced` specs, and what they measure

`5M/balanced/w_balanced_5m.spec.json` — measured time split on the leveled
baseline (`-T 10 -P 512 --bb 1024 --max_background_jobs 1`):

| operation | count | avg | share of time |
|---|---|---|---|
| insert | 700,000 | 43.7 us | 38.9% |
| scan | 1,150,000 | 16.8 us | 24.6% |
| update | 300,000 | 44.5 us | 17.0% |
| get | 1,850,000 | 4.5 us | 10.7% |
| delete | 250,000 | 28.0 us | 8.9% |

Writes 55.9%, reads 35.3%, deletes 8.9% — no operation above 39%. Roughly
139 s per arm, 750 MB of compaction. `1M/balanced/` mirrors the proportions for
fast probing.

Writes carry more time than reads because `max_background_jobs=1` puts
compaction back-pressure on the write path, which is the phenomenon under
study — that share is real, unlike the scan share described below.

**The `get` count is point queries plus empty point queries.** Empty point
queries (lookups for keys that were never inserted) are worth keeping: they
miss the bloom filter and probe every level, which is the read-amplification
signal the RL reward now measures.

## Balance by *time*, not by op count

The `5M/mixed` spec looks balanced by operation count — 3.25M inserts, 900k
point queries, 100k range queries. Measured, it was **99.5% range-scan time**:

| component | cost |
|---|---|
| 100k range scans @ 67.3 ms | 6732 s |
| 900k point queries @ 27.4 us | 25 s |
| 4M writes @ 1.56 us | 6 s |

A total-runtime comparison on that workload is a scan-latency benchmark. Any
policy that trades read amplification for write I/O — which is exactly what
compaction deferral does — is measured in the one unit where it must lose.

Always check the split with the per-operation block in
`experiment_metrics.json` (`operation_latency.<op>.count * .avg_ns`) before
trusting a result.

## Scans used to be a parser bug (fixed 2026-08-02)

Most of that 99.5% was not a workload property. `run_workload.cc` read the
operation with `char operation; stream >> operation;` — a **single character**.
Range queries are emitted as `SC <start_key> <scan_length>`, so the leftover
`"C"` became the start key, the intended start key became the end bound, and
the requested length was never read.

Every range query therefore seeked to the literal key `"C"` and iterated to a
random key — about a third of the database. Perf counters on a 150k-key DB with
a requested length of 50: `iter_next_count = 244,566,822` across 5,000 scans,
i.e. **48,913 `Next()` calls per scan**, while `rocksdb.db.seek.micros` showed
the seek itself cost only 12.9 us.

Reading the whole operation token fixed it. Scan cost for lengths 0 / 50 / 500
went from 3.84 / 4.03 / 4.14 **ms** to 10.6 / 16.3 / 63.6 **us** — a 240x drop,
and cost now scales with the requested length. A 50-key scan is ~3x a point
lookup rather than ~750x.

Scan latencies in any result produced before this fix are meaningless, and any
total-runtime number from a workload containing range queries is dominated by
the bug rather than by the policy under test.

## `scan_length` panics — use `selectivity`

`range_queries.scan_length` is accepted by the schema and by the CLI, then
panics during generation:

```
thread 'main' panicked at tectonic/src/keyset.rs:191:26:
index out of bounds
```

Reproduced for scan_length 5, 50, with and without a `selection`. `selectivity`
works. The consequence is that **scan cost scales with dataset size**: the same
selectivity reads ~10x more keys at 5M than at 500k, so a spec tuned at one
scale is not balanced at another. Pick selectivity per workload size.

Fixing this in `lib/tectonic` would allow fixed-size scans, which is the more
realistic shape (most production range queries are short and size-independent).

## `point_deletes` must be in a *later group of the same section*

- Same group as the inserts → `Error: Cannot have more point deletes than
  existing valid keys` (even for 5000 deletes against 150k inserts).
- Its own *section* → same error.
- A later **group** within the same section → works.

Groups within a section run in order and see prior groups' keys; sections do
not carry the valid-key set forward the same way.

## `point_queries` with `zipf` needs a populated keyspace

`zipf: {n: N}` interleaved with inserts in the same group can sample past the
end of the valid-key set while it is still small, panicking in `keyset.rs`.
Keep `n` at or below the number of keys that exist when the group starts (i.e.
roughly the warmup section's insert count), or use `uniform`.

## The generator infers the name from the path *and* the op mix

`scripts/workload_generator.sh` derives `workload_<size>_<type>.txt` from the
`workload_specs/<size>/<type>/` path, but only for types in its allow-list.
An unrecognised type silently falls back to `mixed` — which will overwrite an
existing `workload_<size>_mixed.txt`. `balanced` was added to the allow-list;
add any new type there too.
