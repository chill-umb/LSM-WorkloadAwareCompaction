#!/usr/bin/env python3
"""A stand-in for db_bench (test_gate_n1_chain.py). For each 03 arm it
prints a run.log and writes a RocksDB LOG and a host log that agree with
each other, so 04 scores the arm and 19 finds turnovers of L2: 20 per 26.1M
mixgraph operations for Assoc, 6 for the power law. For 18 it prints result
lines and tickers of trees whose per-operation costs are known: 0.5 us a
filter probe, 1 us a block read, 2 us a run seek, and 10 us a table reopen,
which a capped run (open_files > 0) makes on half its probes and run seeks
and an all-open run (open_files -1, D-20) never makes; both open 0.006
tables per operation outside the timed work. The fork's timer (D-21) puts
each reopen at 8 us, in the price runs and in mixgraph alike, so every
arm's reopen check holds (ratio 1). FAKE_FAIL_WORKLOAD=assoc (or
powerlaw) makes every arm of that workload fail as an I/O error would.
Only L1 and L2 merge, so 23 refuses the survival-weighted profile (no merges
out of L0 and L3); at a base size in FAKE_SURVIVAL_BASES (bytes, space
separated; test_gate_n2_chain.py) L0 and L3 merge too, and 23 computes it.

For 29 (PREREGISTRATION D-22), filluniquerandom,settle,levelstats writes a
small tree into --db (two SSTs, CURRENT, MANIFEST, OPTIONS, LOG) and prints
levelstats; FAKE_UNSETTLED_ONCE=<folder> makes the first build of s1/T2 end
ok=0. A read on a tree rewrites its LOG and adds an OPTIONS file, as opening
a database does; FAKE_COMPACT_ON_READ=1 also writes a new SST, as a
compaction would. Reads cover T = 2, 3, 4, 6, 8 and 10."""
import json
import os
import sys
from pathlib import Path

args = {}
# 03 reads --help for the interim instruments' flag (D-23 §3(a)); like
# gflags, --help exits non-zero. The stride is accepted and ignored: this
# stand-in writes schema-2 host logs, which carry no snapshots.
if "--help" in sys.argv[1:]:
    print("    -rl_host_log_stride (stand-in)")
    sys.exit(1)

for a in sys.argv[1:]:
    if a.startswith("--") and "=" in a:
        k, v = a[2:].split("=", 1)
        args[k] = v
bench = args.get("benchmarks", "")
T = float(args.get("max_bytes_for_level_multiplier", 10))

if bench == "compact":
    sys.exit(0)
if bench == "filluniquerandom,settle":
    print("RL_SETTLED ok=1 wait_micros=5 hold_micros=10000000")
    sys.exit(0)
if bench == "filluniquerandom,settle,levelstats":
    db = Path(args["db"])
    marker = os.environ.get("FAKE_UNSETTLED_ONCE")
    if marker and db.as_posix().endswith("s1/T2") and not (Path(marker) / "s1-T2").exists():
        (Path(marker) / "s1-T2").write_text("")
        print("RL_SETTLED ok=0 wait_micros=5 hold_micros=0 reason=compaction pending")
        sys.exit(1)
    db.mkdir(parents=True, exist_ok=True)
    for number, size in ((10, 4000), (11, 2000)):
        (db / f"0000{number}.sst").write_bytes(
            f"{db.name} {args.get('seed')} {number}\n".encode() * (size // 16))
    (db / "CURRENT").write_text("MANIFEST-000005\n")
    (db / "MANIFEST-000005").write_text(f"levels of {db.name}\n")
    (db / "OPTIONS-000007").write_text(f"T={T}\n")
    (db / "LOG").write_text("built\n")
    print("RL_SETTLED ok=1 wait_micros=5 hold_micros=10000000")
    print("\nLevel Files Size(MB)\n--------------------\n"
          "  0        0        0\n  1        1        0\n  2        1        0\n")
    sys.exit(0)
if bench in ("readrandom", "readmissing", "seekrandom"):
    ops = int(args["reads"])
    if args.get("use_existing_db") == "1" and Path(args.get("db", "/nonexistent")).is_dir():
        db = Path(args["db"])
        with (db / "LOG").open("a") as log:
            log.write(f"opened for {bench}\n")
        (db / "OPTIONS-000099").write_text("reopened\n")
        if os.environ.get("FAKE_COMPACT_ON_READ") == "1":
            (db / "000099.sst").write_text("compacted\n")
    miss, found, runs = {2.0: (9, 8, 10), 3.0: (7.5, 6.8, 8.5), 4.0: (6.5, 6, 7.5),
                         6.0: (5, 4.5, 6), 8.0: (4.5, 4, 5.5), 10.0: (4, 3.5, 5)}[T]
    if bench == "seekrandom":
        per_op, tick = 3e-6 + 2e-6 * runs, {"sorted.run.seek": runs}
        touched = runs
    else:
        probes = miss if bench == "readmissing" else found
        blocks = 0.01 * miss if bench == "readmissing" else 1 + 0.01 * (found - 1)
        per_op = 2e-6 + 0.5e-6 * probes + 1e-6 * blocks
        tick = {"point.sst.probe": probes, "bloom.filter.full.positive": blocks}
        touched = probes
    reopens = 0.0 if int(args.get("open_files", 1000)) == -1 else 0.5 * touched
    per_op += 10e-6 * reopens
    tick["no.file.opens"] = 0.006 + reopens
    tick["read.table.reopen"] = reopens
    tick["read.table.reopen.nanos"] = 8000 * reopens
    print(f"{bench:<12} : {per_op*1e6:11.3f} micros/op 1 ops/sec "
          f"{per_op*ops:.3f} seconds {ops} operations; (1 of 1 found)")
    print("STATISTICS:")
    for k, v in tick.items():
        print(f"rocksdb.{k} COUNT : {round(v * ops)}")
    sys.exit(0)
assert "mixgraph" in bench, bench
power = float(args.get("mix_get_ratio", "0.806")) > 0.9
if os.environ.get("FAKE_FAIL_WORKLOAD") == ("powerlaw" if power else "assoc"):
    print("IO error: fake failure")
    sys.exit(1)

B = int(args["max_bytes_for_level_base"])
num, reads, nlev = int(args["num"]), int(args["reads"]), int(args["num_levels"])
C = [0] + [int(B * T ** (i - 1)) for i in range(1, nlev)]
turnovers = (6 if power else 20) * reads / 26_100_000
merges = max(8, int(turnovers * 8))            # L1 -> L2 merges, 8 per turnover
survival = str(B) in os.environ.get("FAKE_SURVIVAL_BASES", "").split()
WS, US = 5_000_000_000, 16                     # wall-clock start; us per operation
host, events, H, job = [], [], 3_000_000_000, 100


def levels_row(scale):
    """Schema 2 (D-21): probe, pass, hit, seek, Get and iterator reopens,
    and their nanoseconds at 8 us each."""
    return [[int(600 * scale), int(200 * scale), int(150 * scale), int(30 * scale),
             int(400 * scale), int(100 * scale), int(500 * scale) * 8000],
            [int(400 * scale), int(100 * scale), int(50 * scale), int(20 * scale),
             int(300 * scale), int(50 * scale), int(350 * scale) * 8000]]


def tickers(scale, written, created):
    """created: SST files written so far, each opened once by its job."""
    rows = levels_row(scale)
    return {"rocksdb.point.sst.probe": int(1000 * scale),
            "rocksdb.bloom.filter.full.positive": int(300 * scale),
            "rocksdb.bloom.filter.full.true.positive": int(200 * scale),
            "rocksdb.sorted.run.seek": int(50 * scale),
            "rocksdb.no.file.opens": int(1000 * scale) + created,
            "rocksdb.read.table.reopen": sum(r[4] + r[5] for r in rows),
            "rocksdb.read.table.reopen.nanos": sum(r[6] for r in rows),
            "rocksdb.bytes.written": written}


def stamp(name, t_us, wall, op, h, stall_us, scale, written, created=0, **extra):
    host.append({"type": "stamp", "name": name, "t_us": t_us, "wall_us": wall,
                 "op": op, "h": h, "stall_micros": stall_us,
                 "levels": levels_row(scale),
                 "tickers": tickers(scale, written, created), **extra})


def occupancy(fill2):
    """L = 4, with L4 at 0.3 of its target: the candidates are L2 alone."""
    occ = [0] * nlev
    occ[1], occ[2] = int(0.8 * C[1]), int(fill2 * C[2])
    occ[3], occ[4] = int(0.9 * C[3]), int(0.3 * C[4])
    return occ


host.append({"type": "header", "schema": 2, "num_levels": nlev, "t_us": 0,
             "wall_us": WS - 3_000_000})
written = num * 1040
stamp("settle", 500, WS - 2_000_000, num, H, 0, 1, written, ok=1,
      wait_micros=5, hold_micros=10_000_000)
stamp("measure_start", 1_000_000, WS, num, H, 0, 1, written)
for m in range(merges):
    op = num + int((m + 0.5) * reads / merges)
    t, wall = 1_000_000 + (op - num) * US, WS + (op - num) * US
    job += 1                                   # a flush
    H += 2 << 20
    written += 2 << 20
    host.append({"type": "h", "cause": "flush", "job": job, "t_us": t, "op": op, "h": H})
    events += [{"time_micros": wall, "job": job, "event": "flush_started"},
               {"time_micros": wall + 1, "job": job, "event": "table_file_creation",
                "file_number": job, "file_size": 2 << 20},
               {"time_micros": wall + 2, "job": job, "event": "flush_finished"}]
    # An L1 -> L2 merge landing C2/8; every 8th merge, also an L2 release.
    moves = [(1, C[2] // 16, C[2] // 16, C[2] // 16 + C[2] // 8)]
    if m % 8 == 7:
        moves.append((2, C[2] // 2, C[3] // 4, C[2] // 2 + C[3] // 4))
    if survival:                               # the flushed file into L1; an L3 release
        moves.insert(0, (0, 2 << 20, C[1] // 8, (2 << 20) + C[1] // 8))
        if m % 8 == 3:
            moves.append((3, C[3] // 8, C[4] // 32, C[3] // 8 + C[4] // 32))
    for k, (level, s, o, x) in enumerate(moves):
        job += 1
        tj, wj = t + 10 + k * 20, wall + 10 + k * 20
        base = {"job": job, "cf": 0, "start_level": level, "output_level": level + 1,
                "reason": 1, "trivial": 0, "s": s, "o": o, "due_since_us": tj - 5}
        host.append({"type": "job_begin", "t_us": tj, "op": op, **base})
        events.append({"time_micros": wj, "job": job, "event": "compaction_release",
                       "occupancy_bytes": occupancy(0.6 + 0.4 * ((m % 8) / 8)),
                       "nominal_target_bytes": C})
        host.append({"type": "job_end", "t_us": tj + 10, "op": op, **base,
                     "x": x, "ok": 1})
        events.append({"time_micros": wj + 10, "job": job,
                       "event": "compaction_finished", "compaction_time_micros": 50_000,
                       "output_level": level + 1, "total_output_size": x,
                       "rl_drain": 0, "rl_suspended": 0})
        H += x - s - o
        host.append({"type": "h", "cause": "compaction", "job": job,
                     "t_us": tj + 10, "op": op, "h": H})
end_op, end_wall = num + reads, WS + reads * US
stamp("drain_start", 1_000_000 + reads * US, end_wall, end_op, H, 1_000_000, 3,
      written, merges)
stamp("drain_end", 2_000_000 + reads * US, end_wall + 1_000_000, end_op, H,
      1_000_000, 3, written, merges)

Path(args["rl_host_log"]).write_text("".join(json.dumps(r) + "\n" for r in host))
db = Path(args["db"])
db.mkdir(parents=True, exist_ok=True)
(db / "LOG").write_text("".join(f"2026/10/01-00:00:00.000000 1 EVENT_LOG_v1 "
                                f"{json.dumps(e)}\n" for e in events))
g, p = int(reads * 0.8), int(reads * 0.16)
print(f"""RocksDB:    version 11.1.1
filluniquerandom :       4.948 micros/op 202056 ops/sec 1.000 seconds {num} operations;  197.3 MB/s
RL_SETTLED ok=1 wait_micros=5 hold_micros=10000000
RL_CONTROL_RESUMED_MICROS {WS - 1000}
RL_MEASURE_START_OP {num}
mixgraph     :      16.000 micros/op 62500 ops/sec {reads * US / 1e6:.3f} seconds {reads} operations;  0.1 MB/s ( Gets:{g} Puts:{p} Seek:{reads - g - p} ScanEntries:600, reads {g} in {g} found, avg size: 956.4 value, 15.0 scan)
RL_DRAIN_START_MICROS {end_wall}
RL_DRAIN_END_MICROS {end_wall + 1_000_000}
Cumulative stall: 00:00:1.000 H:M:S, 0.1 percent
STATISTICS:
rocksdb.bytes.written COUNT : {written}
rocksdb.flush.write.bytes COUNT : {merges * (2 << 20)}
rocksdb.compact.write.bytes COUNT : 1000
rocksdb.point.sst.probe COUNT : 3000
rocksdb.bloom.filter.full.positive COUNT : 900
rocksdb.sorted.run.seek COUNT : 150
rocksdb.stall.micros COUNT : 9999999""")
