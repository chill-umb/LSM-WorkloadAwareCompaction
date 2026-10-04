#!/usr/bin/env python3
"""Cost model 2's one-session calibrations (PREREGISTRATION D-24 §2, the
exploratory track; designs from D-23 §4 and D-24 §1 items 1, 10, 11): the
fits that turn calibration runs into a calibration file for stage 30
(30_provisional_prices_v2.py). The runs come from the operator's runner
(~/node_ops/diag/calib_v2.sh); every price here is core-seconds.

    calibration_v2.py jobs --run DIR [--run DIR ...] --out cal_jobs.json
        Job prices from host-log spans (A11, D-23 §4(c)): span = c_job(kind)
        + c_cr (S + O) + c_w X. c_cr is kept only when identified (positive
        and at least twice its standard error, which the garbage-heavy
        neighbour's jobs make possible, D-24 item 11); otherwise the
        combined fit is written, c_cr = 0, with c_cr_combined. Also the L0
        merges' spans in operations (n_win, D-23 §7 A) and the reference
        compaction traffic (D-24 §4).

    calibration_v2.py foreground --runs DIR --base prices.json --out cal_fg.json
        The quiet foreground prices and kappa from the runner's table
        (DIR/rows.jsonl, one row per run, written by `rows`).

    calibration_v2.py rows --runs DIR
        Parses every run directory below DIR (its stdout.txt, run.env and,
        for a neighbour arm, the neighbour's host log) into DIR/rows.jsonl.

Foreground (D-23 §4(b), (d); all tables open, so no reopens):
- c_put: the in-store Put (rocksdb.db.write.micros) of a Put-only run with
  compactions off and the write-ahead log off;
- c_mt: perf context get_from_memtable_time per Get of a run that reads
  over a populated memtable; c0_get: a quiet Get's in-store time
  (rocksdb.db.get.micros) less its memtable search and its filter probes and
  block reads at stage 18's prices;
- c_st and c_ib: seekrandom at several seek_nexts on trees of two data-block
  sizes, per-scan time = a + c_st steps + c_ib iterator blocks (the data
  blocks a scan touches beyond those of its seek at seek_nexts 0);
- c0_sc: the scan set-up timer (set-up plus teardown per iterator) plus the
  seek's in-store time (rocksdb.db.seek.micros) less its run seeks at c_sk,
  at seek_nexts 0.
kappa (A10): each loaded run's slowdown against its quiet twin,
s = t / t_quiet - 1, is fitted per step type to kappaB v + kappaJ busy,
with v the neighbour's job bytes per second, Y = X + lambda (S + O), and
busy its mean number of running jobs, over the reader's window. lambda is
chosen on a grid by the pooled residual. A coefficient is kept only when
identified (two standard errors); the basis is reported. The step types the
runs measure are probe (readmissing), block (readrandom), seek (seekrandom,
seek_nexts 0), step (seekrandom, seek_nexts > 0) and put (Put-only); the
others take a stated stand-in.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

import cost_model_v2 as cm

RESULT = re.compile(r"^(\w+)\s+:\s+([\d.]+) micros/op .*? ([\d.]+) seconds (\d+) operations;", re.M)
TICKER = re.compile(r"^(rocksdb\.[\w.\-]+) COUNT : (\d+)", re.M)
HIST = re.compile(r"^(rocksdb\.db\.(?:get|seek|write)\.micros) .*?COUNT : (\d+) SUM : (\d+)", re.M)
PERF_PAIR = re.compile(r"\b(\w+) = (\d+)(?![\d@])")
EVENT = re.compile(r"EVENT_LOG_v1 (\{.*\})\s*$")
LAMBDA_GRID = (0.0, 0.25, 0.5, 1.0)
# Step types without their own runs take a stand-in, stated in the output.
STAND_INS = {"reopen": "block", "iblock": "step", "memtable": "block",
             "get0": "block", "scan0": "seek"}
DATA_BLOCKS = ("rocksdb.block.cache.data.hit", "rocksdb.block.cache.data.miss")


# --- least squares --------------------------------------------------------

def ols(x: np.ndarray, y: np.ndarray):
    """Coefficients, standard errors and the residual sum of squares."""
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    resid = y - x @ coef
    rss = float(resid @ resid)
    dof = max(len(y) - x.shape[1], 1)
    cov = np.linalg.pinv(x.T @ x) * rss / dof
    return coef, np.sqrt(np.maximum(np.diag(cov), 0.0)), rss


def identified(value: float, se: float) -> bool:
    return value > 0 and value >= 2 * se


# --- job prices -----------------------------------------------------------

def events_of(log: Path) -> list[dict]:
    out = []
    with open(log, errors="replace") as handle:
        for line in handle:
            match = EVENT.search(line)
            if match:
                try:
                    out.append(json.loads(match.group(1)))
                except json.JSONDecodeError:
                    pass
    return out


def run_jobs(run: Path) -> tuple[list, dict]:
    """A run's measured-phase jobs (cost_model_v2.Job) and its phase."""
    records = [json.loads(line) for line in
               (run / "host_log.jsonl").read_text().splitlines() if line.strip()]
    at = {r["name"]: i for i, r in enumerate(records) if r.get("type") == "stamp"}
    sizes, spans = {}, {}
    if records[0].get("schema", 0) < 3:
        started, finished = {}, {}
        flush_jobs = set()
        for e in events_of(run / "rocksdb_LOG.txt"):
            kind = e.get("event")
            if kind == "flush_started":
                started[e["job"]] = e["time_micros"]
                flush_jobs.add(e["job"])
            elif kind == "flush_finished":
                finished[e["job"]] = e["time_micros"]
            elif kind == "table_file_creation" and e.get("job") in flush_jobs:
                sizes[e["job"]] = sizes.get(e["job"], 0) + e.get("file_size", 0)
        spans = {j: (finished[j] - started[j]) / 1e6 for j in finished
                 if j in started}
    jobs = cm.jobs_from_host_log(records, at["measure_start"], at["drain_end"],
                                 sizes, spans)
    start, end = records[at["measure_start"]], records[at["drain_end"]]
    phase = {"seconds": (end["t_us"] - start["t_us"]) / 1e6,
             "mix_end_op": records[at["drain_start"]]["op"]}
    return jobs, phase


def fit_jobs(jobs: list) -> dict:
    """Separate and combined fits over jobs with a span; the chosen one."""
    rows = [j for j in jobs if j.span_s is not None]
    if not rows:
        raise ValueError("no job has a span")
    kinds = list(cm.KINDS)

    def design(combined):
        x = np.zeros((len(rows), len(kinds) + (1 if combined else 2)))
        for i, j in enumerate(rows):
            x[i, kinds.index(j.kind)] = 1
            if j.kind != "move":
                if combined:
                    x[i, -1] = j.x / 1e6
                else:
                    x[i, -2] = (j.s + j.o) / 1e6
                    x[i, -1] = j.x / 1e6
        return x

    y = np.array([j.span_s for j in rows])
    out = {}
    # Bytes read exactly proportional to bytes written leave the separate
    # fit rank-deficient; its pseudo-inverse errors would hide that.
    separate = design(False)
    full_rank = (np.linalg.matrix_rank(separate,
                                       tol=1e-9 * np.linalg.norm(separate, 2))
                 == separate.shape[1])
    for name, combined in (("separate", False), ("combined", True)):
        coef, se, rss = ols(design(combined), y)
        names = kinds + (["c_w"] if combined else ["c_cr", "c_w"])
        scale = [1.0] * len(kinds) + [1e-6] * (1 if combined else 2)
        out[name] = {n: float(c * s) for n, c, s in zip(names, coef, scale)}
        out[name + "_se"] = {n: float(e * s) for n, e, s in zip(names, se, scale)}
        out[name + "_rmse_s"] = math.sqrt(rss / len(y))
    sep, sep_se = out["separate"], out["separate_se"]
    use_separate = full_rank and identified(sep["c_cr"], sep_se["c_cr"])
    chosen = out["separate" if use_separate else "combined"]
    out["c_cr_combined"] = not use_separate
    out["core_seconds"] = {
        **{f"job_{k}": chosen[k] for k in kinds},
        "c_w": chosen["c_w"], "c_cr": chosen.get("c_cr", 0.0) if use_separate else 0.0}
    out["counts"] = {k: sum(j.kind == k for j in rows) for k in kinds}
    bad = [k for k in kinds if out["counts"][k] and chosen[k] <= 0]
    if bad or chosen["c_w"] <= 0:
        raise ValueError(f"non-positive job prices for {bad or ['c_w']}: "
                         "the fit does not identify them")
    return out


def jobs_command(args) -> int:
    jobs, l0_ops, traffic = [], [], []
    # D-24 item 11: a garbage-heavy neighbour's jobs, whose merges read far
    # more than they write, identify c_cr. Its whole host log counts.
    for log in args.neighbour_log or []:
        records = [json.loads(line) for line in log.read_text().splitlines()
                   if line.strip()]
        jobs += cm.jobs_from_host_log(records, 0, len(records), {}, {})
    for run in args.run:
        run_jobs_, phase = run_jobs(run)
        jobs += run_jobs_
        l0_ops += [j.n_end - j.n_begin for j in run_jobs_
                   if j.kind == "l0" and j.n_begin is not None and
                   j.n_end <= phase["mix_end_op"]]
        written = sum(j.x for j in run_jobs_ if j.kind in ("l0", "deep"))
        traffic.append(written / phase["seconds"] / 1e6)
    fit = fit_jobs(jobs)
    pct = {q: float(np.percentile(l0_ops, q)) for q in (1, 5, 50)} if l0_ops else {}
    result = {"source": "calibration_v2.py jobs: " + ", ".join(map(str, args.run)),
              "core_seconds": fit["core_seconds"],
              "c_cr_combined": fit["c_cr_combined"],
              "reference_traffic_mb_s": float(np.mean(traffic)),
              "fit": {k: v for k, v in fit.items() if k != "core_seconds"},
              "l0_merge_span_ops_percentiles": pct}
    args.out.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({k: result[k] for k in ("core_seconds", "c_cr_combined",
                                             "reference_traffic_mb_s",
                                             "l0_merge_span_ops_percentiles")},
                     indent=1))
    return 0


# --- calibration runs -----------------------------------------------------

def parse_stdout(text: str) -> dict:
    """The measured benchmark's result line, tickers, in-store histograms
    and perf context (the last of each in the file)."""
    results = RESULT.findall(text)
    if not results:
        raise ValueError("no benchmark result line")
    bench, micros, seconds, ops = results[-1]
    perf = {}
    if "PERF_CONTEXT:" in text:
        line = text.rsplit("PERF_CONTEXT:", 1)[1].lstrip("\n").split("\n", 1)[0]
        perf = {k: int(v) for k, v in PERF_PAIR.findall(line)}
    return {"bench": bench, "micros_per_op": float(micros),
            "seconds": float(seconds), "ops": int(ops),
            "tickers": {k: int(v) for k, v in TICKER.findall(text)},
            "hist": {k: (int(c), int(s)) for k, c, s in HIST.findall(text)},
            "perf": perf}


def neighbour_load(records: list[dict], t0_wall_us: float,
                   t1_wall_us: float) -> dict:
    """The neighbour's jobs over the reader's window [t0, t1] (wall µs): the
    mean number running, by kind, and the bytes per second they write (X)
    and read (S + O), each job's bytes spread evenly over its span. Host-log
    times are steady-clock µs, mapped to wall µs by the header's pair."""
    header = records[0]
    offset = header["wall_us"] - header["t_us"]
    begins = {}
    length = (t1_wall_us - t0_wall_us) / 1e6
    out = {"busy_" + k: 0.0 for k in cm.KINDS}
    out.update(x_rate=0.0, read_rate=0.0, window_s=length)
    if length <= 0:
        return out
    for r in records:
        kind = r.get("type")
        if kind in ("job_begin", "flush_begin"):
            begins[(kind, r["job"])] = r
            continue
        if kind == "job_end" and r.get("ok") == 1:
            b = begins.get(("job_begin", r["job"]))
            k = ("move" if r["trivial"] else
                 "l0" if r["start_level"] == 0 else "deep")
            x = 0 if r["trivial"] else r["x"]
            read = 0 if r["trivial"] else r["s"] + r["o"]
        elif kind == "flush_end":
            b = begins.get(("flush_begin", r["job"]))
            k, x, read = "flush", r["x"], 0
        else:
            continue
        if b is None:
            continue
        a, z = b["t_us"] + offset, r["t_us"] + offset
        overlap = max(0.0, min(z, t1_wall_us) - max(a, t0_wall_us)) / 1e6
        if overlap <= 0:
            continue
        span = max((z - a) / 1e6, 1e-9)
        out["busy_" + k] += overlap / length
        out["x_rate"] += x * overlap / span / length
        out["read_rate"] += read * overlap / span / length
    return out


def per_op(row: dict) -> dict:
    """A row's per-operation measurements (core-seconds and counts)."""
    t = row["tickers"]
    ops = max(row["ops"], 1)

    def hist(name):
        count, total = row["hist"].get(name, (0, 0))
        return total / count / 1e6 if count else math.nan

    blocks = sum(t.get(n, 0) for n in DATA_BLOCKS)
    seeks = max(t.get("rocksdb.number.db.seek", 0), 1)
    gets = max(t.get("rocksdb.number.keys.read", 0), 1)
    setups = t.get("rocksdb.rl.scan.setup.count", 0)
    return {
        "wall": row["micros_per_op"] / 1e6,
        "get": hist("rocksdb.db.get.micros"),
        "seek": hist("rocksdb.db.seek.micros"),
        "put": hist("rocksdb.db.write.micros"),
        "probes": t.get("rocksdb.point.sst.probe", 0) / gets,
        "blocks_get": t.get("rocksdb.bloom.filter.full.positive", 0) / gets,
        "run_seeks": t.get("rocksdb.sorted.run.seek", 0) / seeks,
        "steps": (t.get("rocksdb.number.db.next.found", 0) +
                  t.get("rocksdb.number.iter.skip", 0)) / seeks,
        "data_blocks": blocks / ops,
        "setup": ((t.get("rocksdb.rl.scan.setup.nanos", 0) +
                   t.get("rocksdb.rl.scan.teardown.nanos", 0)) / setups / 1e9
                  if setups else math.nan),
        "memtable": (row["perf"].get("get_from_memtable_time", 0) /
                     max(row["perf"].get("get_from_memtable_count", 0), 1) / 1e9
                     if row["perf"].get("get_from_memtable_count") else math.nan),
    }


def rows_command(args) -> int:
    """run.env holds: label, kind (miss, hit, seek, put, memtable), arm
    (quiet or a neighbour arm), tree, nexts, perf_level, end_wall_us (and
    optionally start_wall_us), and for a neighbour arm neighbour_host_log."""
    rows = []
    for env in sorted(args.runs.glob("**/run.env")):
        meta = dict(line.split("=", 1) for line in env.read_text().splitlines()
                    if "=" in line)
        stdout = env.parent / "stdout.txt"
        try:
            row = parse_stdout(stdout.read_text(errors="replace"))
        except (OSError, ValueError) as error:
            print(f"[rows] {env.parent}: {error}", file=sys.stderr)
            continue
        row.update(meta)
        log = meta.get("neighbour_host_log")
        if log:
            records = [json.loads(line) for line in Path(log).read_text()
                       .splitlines() if line.strip()]
            # The reader's benchmark window: it ends as the process ends
            # (closing is short) and lasts its reported seconds, so the
            # database's opening is left out.
            end = float(meta["end_wall_us"])
            start = float(meta.get("start_wall_us",
                                   end - row["seconds"] * 1e6))
            row["neighbour"] = neighbour_load(records, start, end)
        rows.append(row)
    (args.runs / "rows.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))
    print(f"[rows] {len(rows)} runs -> {args.runs / 'rows.jsonl'}")
    return 0


# --- foreground prices and kappa ------------------------------------------

def timed(row: dict) -> bool:
    """A perf-level-4 run: its per-step timers are on, which slows it, so
    it gives timers (the memtable search) and never times."""
    return row.get("perf_level", "1") != "1"


def fit_foreground(rows: list[dict], base_core: dict) -> dict:
    """The quiet foreground prices (core-seconds) from quiet rows."""
    quiet = [r for r in rows if r.get("arm") == "quiet"]
    m = {id(r): per_op(r) for r in quiet}
    out, notes = {}, {}
    puts = [m[id(r)]["put"] for r in quiet if r.get("kind") == "put"]
    if puts:
        out["c_put"] = float(np.nanmean(puts))
    mts = [m[id(r)]["memtable"] for r in quiet if r.get("kind") == "memtable"]
    if mts:
        out["c_mt"] = float(np.nanmean(mts))
    gets = [m[id(r)] for r in quiet
            if r.get("kind") in ("hit", "miss") and not timed(r)]
    if gets:
        residual = [g["get"] - base_core["c_f"] * g["probes"] -
                    base_core["c_blk"] * g["blocks_get"] for g in gets]
        # The quiet tree's memtable is empty: its search is the part of the
        # residual the timer runs (perf level 4) attribute to the memtable.
        empty = [m[id(r)]["memtable"] for r in quiet
                 if r.get("kind") in ("hit", "miss") and
                 not math.isnan(m[id(r)]["memtable"])]
        out["c_get0"] = float(np.mean(residual) - (np.mean(empty) if empty else 0.0))
        notes["c_get0"] = ("Get time less filter probes and block reads at "
                           "stage 18's prices" + (" and the empty memtable's "
                                                  "search" if empty else ""))
    scans = [(r, m[id(r)]) for r in quiet
             if r.get("kind") == "seek" and not timed(r)]
    if scans:
        # c_st and c_ib: per-scan wall time on steps and iterator blocks.
        base_blocks = {}
        for r, s in scans:
            if int(r.get("nexts", 0)) == 0:
                base_blocks.setdefault(r.get("tree"), []).append(s["data_blocks"])
        x, y = [], []
        for r, s in scans:
            seek_blocks = np.mean(base_blocks.get(r.get("tree"), [0.0]))
            x.append([1.0, s["steps"], max(s["data_blocks"] - seek_blocks, 0.0)])
            y.append(s["wall"])
        x, y = np.array(x), np.array(y)
        if len({row[1] for row in x.tolist()}) >= 2:
            coef, se, _ = ols(x, y)
            out["c_st"] = float(coef[1])
            ib_ok = identified(float(coef[2]), float(se[2]))
            out["c_ib"] = float(coef[2]) if ib_ok else 0.0
            notes["c_ib"] = ("identified" if ib_ok else
                             "not identified (two block sizes needed): 0")
            notes["c_st"] = f"slope {coef[1]:.3e} ± {se[1]:.3e} s per step"
        at0 = [s for r, s in scans if int(r.get("nexts", 0)) == 0]
        if at0:
            seek_part = np.nanmean([s["seek"] - base_core["c_sk"] * s["run_seeks"]
                                    for s in at0])
            setup = np.nanmean([s["setup"] for s in at0])
            out["c_sc0"] = float(max(seek_part, 0.0) +
                                 (setup if not math.isnan(setup) else 0.0))
    for key, value in out.items():
        if not (math.isfinite(value) and value >= 0):
            raise ValueError(f"{key} = {value}: not a price")
    return {"core_seconds": out, "notes": notes}


def quiet_time(rows: list[dict], row: dict) -> float:
    """The in-store time per operation of the row's quiet twins."""
    key = lambda r: (r.get("kind"), r.get("tree"), r.get("nexts", "0"))
    twins = [per_op(r) for r in rows
             if r.get("arm") == "quiet" and key(r) == key(row) and not timed(r)]
    return float(np.nanmean([time_of(row.get("kind"), t) for t in twins])) \
        if twins else math.nan


def time_of(kind: str, measured: dict) -> float:
    return {"hit": measured["get"], "miss": measured["get"],
            "put": measured["put"]}.get(kind, measured["wall"])


KIND_TYPE = {"miss": "probe", "hit": "block", "put": "put"}


def fit_kappa(rows: list[dict]) -> dict:
    """kappa^B and kappa^J per measured step type, and lambda (A10)."""
    loaded = []
    for r in rows:
        if r.get("arm") == "quiet" or "neighbour" not in r or timed(r):
            continue
        kind = r.get("kind")
        x_type = KIND_TYPE.get(kind) or (
            "seek" if kind == "seek" and int(r.get("nexts", 0)) == 0 else
            "step" if kind == "seek" else None)
        quiet = quiet_time(rows, r)
        if x_type is None or not (quiet > 0):
            continue
        s = time_of(kind, per_op(r)) / quiet - 1
        loaded.append((x_type, s, r["neighbour"]))
    if not loaded:
        raise ValueError("no loaded run with a quiet twin")
    best = None
    for lam in LAMBDA_GRID:
        total, fits = 0.0, {}
        for x_type in sorted({t for t, _, _ in loaded}):
            data = [(s, n) for t, s, n in loaded if t == x_type]
            v = np.array([n["x_rate"] + lam * n["read_rate"] for _, n in data])
            busy = np.array([sum(n["busy_" + k] for k in cm.KINDS)
                             for _, n in data])
            y = np.array([s for s, _ in data])
            coef, se, rss = ols(np.column_stack([v, busy]), y)
            total += rss
            fits[x_type] = (coef, se)
        if best is None or total < best[0]:
            best = (total, lam, fits)
    _, lam, fits = best
    kappa_b, kappa_j, basis = {}, {}, {}
    for x_type, (coef, se) in fits.items():
        b_ok = identified(float(coef[0]), float(se[0]))
        j_ok = identified(float(coef[1]), float(se[1]))
        kappa_b[x_type] = float(coef[0]) if b_ok else 0.0
        kappa_j[x_type] = float(coef[1]) if j_ok else 0.0
        basis[x_type] = ("bytes" if b_ok else "") + ("+busy" if j_ok else "") or \
            "neither identified"
    for x_type, stand_in in STAND_INS.items():
        if stand_in in kappa_b:
            kappa_b.setdefault(x_type, kappa_b[stand_in])
            kappa_j.setdefault(x_type, kappa_j[stand_in])
            basis.setdefault(x_type, f"stand-in: {stand_in}")
    missing = [x for x in cm.STEP_TYPES if x not in kappa_b]
    if missing:
        raise ValueError("no kappa for " + ", ".join(missing))
    return {"lambda": lam, "B": kappa_b,
            "J": {x: {k: kappa_j[x] for k in cm.KINDS} for x in cm.STEP_TYPES},
            "basis": "; ".join(f"{x}: {basis[x]}" for x in cm.STEP_TYPES)}


def foreground_command(args) -> int:
    rows = [json.loads(line) for line in
            (args.runs / "rows.jsonl").read_text().splitlines() if line.strip()]
    base = json.loads(args.base.read_text())
    core = base["core_seconds_per_unit"]
    fg = fit_foreground(rows, core)
    result = {"source": f"calibration_v2.py foreground: {args.runs}",
              "core_seconds": fg["core_seconds"], "notes": fg["notes"]}
    try:
        result["kappa"] = fit_kappa(rows)
    except ValueError as error:
        print(f"[foreground] kappa not fitted: {error}", file=sys.stderr)
    args.out.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("jobs")
    p.add_argument("--run", type=Path, action="append", required=True)
    p.add_argument("--neighbour-log", type=Path, action="append")
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("rows")
    p.add_argument("--runs", type=Path, required=True)
    p = sub.add_parser("foreground")
    p.add_argument("--runs", type=Path, required=True)
    p.add_argument("--base", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return {"jobs": jobs_command, "rows": rows_command,
            "foreground": foreground_command}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
