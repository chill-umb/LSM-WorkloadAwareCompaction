#!/usr/bin/env python3
"""Stage 38's fits: one machine's calibration (38_calibrate_machine.sh; owner,
2026-11-08: q-bar and every price that changes from machine to machine are
measured once on each machine, every price refitted). Exploratory: the
prices are provisional (D-24 §2), and q-bar is a stand-in where the
contract's reference rate is open (D-29 §6).

  reads     the read prices of D-15 §3 (c_f, c_blk, c_sk) from one set of
            price trees, every round fitted by 18_calibrate_prices.py's own
            round_record, the median over the rounds; written into a copy of
            the base prices file (stage 18's schema 4 or 5), which then
            serves calibration_v2.py foreground and stage 30 as their base.
            Carried from the base, not measured, and listed as such: c_open
            and the reopen timer (the suite keeps every table open, so no
            run reopens a table), c_s and the core price (the contract's
            money), c_w (stage 30 replaces it by the job fit's).
              --runs DIR: round<k>/T<T>/<benchmark>/stdout.txt, k = 1.. and
              every T in price_trees.SIZE_RATIOS
  arm-dirs  each native summary's arm directory, one per line (the job
            fit's reference runs)
  compose   the machine's results from its native runs, its prices and the
            preflight's turn smoke:
              qbar.json                 q-bar per workload: the native run's
                                        measured-phase throughput (04)
              turn_settings.machine.json  the turn settings with c_set
                                        measured: 37's c_set over both turn
                                        arms' accepted SetOptions calls at
                                        this machine's prices
              calibration.json, report.md  every value, and every price's
                                        ratio to the reference prices file

  machine-key  this machine's key: host, /etc/machine-id, and the disk under
            the databases (its serial and model), the drive q-bar is measured on
  seal      (owner, 2026-11-08: once in the machine's life) the finished
            calibration in DIR, sealed to this machine: MACHINE_CALIBRATED
            holds the machine's key and every output's sha256. Refused if
            c_set was not measured, a workload lacks a positive q-bar, or DIR
            is sealed already
  status    whether DIR holds a sealed calibration of THIS machine whose files
            are as sealed; prints PRICES_FILE=, QBAR_FILE=,
            TURN_SETTINGS_FILE=, MACHINE_CAL_ID= (36 reads them). Exit 0
            yes, 3 nothing sealed, 4 another machine's, 6 a file changed

  38_machine_calibration.py reads --runs DIR --base prices.json \\
      --db-bench-sha256 SHA --out base.machine.json
  38_machine_calibration.py arm-dirs --native W1=summary.csv ...
  38_machine_calibration.py compose --native W1=summary.csv ... \\
      --prices prices.machine.json --reference prices.provisional.v5.json \\
      --turn-settings config/turn_settings.json \\
      --turn-result <preflight turn arm dir> ... --turn-report <37 report> ... \\
      [--jobs cal_jobs.json] --out-dir DIR
  38_machine_calibration.py machine-key --db-root DIR
  38_machine_calibration.py seal --dir DIR --db-root DIR --workload W1 ... \\
      --db-bench-sha256 SHA [--root-head H --fork-head H --setting S]
  38_machine_calibration.py status --dir DIR --db-root DIR
"""

from __future__ import annotations

import argparse
import copy
import csv
import datetime
import hashlib
import importlib.util
import json
import math
import os
import platform
import subprocess
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE))

import price_trees  # noqa: E402


def _module(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, PIPELINE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


calibrate = _module("calibrate_prices_38", "18_calibrate_prices.py")

READ_PRICES = ("c_f", "c_blk", "c_sk")
CARRIED = ("c_open", "reopen_timer", "c_s", "price_per_core_second", "c_w",
           "qbar_reopen_checks", "read_processes_per_operation")
WORKLOADS = ("W1", "W2", "W3", "W4", "W5")


def now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: Path, value) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


# --------------------------------------------------------------- reads
def read_rounds(runs: Path) -> list[dict[int, dict[str, str]]]:
    """[{T: {benchmark: stdout}}] per round, in round order; ValueError
    unless every round has every tree's three benchmarks."""
    rounds = sorted((p for p in runs.glob("round*") if p.is_dir()
                     and p.name[5:].isdigit()), key=lambda p: int(p.name[5:]))
    if not rounds:
        raise ValueError(f"no round<k> folder under {runs}")
    out = []
    for path in rounds:
        trees = {}
        for ratio in price_trees.SIZE_RATIOS:
            trees[ratio] = {}
            for bench in calibrate.READS:
                stdout = path / f"T{ratio}" / bench / "stdout.txt"
                if not stdout.is_file():
                    raise ValueError(f"missing {stdout}")
                trees[ratio][bench] = stdout.read_text(errors="replace")
        out.append(trees)
    return out


def fit_reads(rounds: list[dict[int, dict[str, str]]]) -> dict:
    """Each round's round_record and the medians over the rounds."""
    records = [calibrate.round_record(trees) for trees in rounds]
    values = {k: [r[k] for r in records] for k in READ_PRICES}
    medians, spread = calibrate.summarise(values)
    return {"core_seconds": medians, "spread": spread, "rounds": records}


def machine_base(base: dict, fit: dict, db_bench_sha256: str) -> dict:
    if base.get("schema") not in (4, 5):
        raise ValueError("the base prices must be stage 18's schema 4 or 5")
    core = base.get("price_per_core_second")
    if not isinstance(core, (int, float)) or not core > 0:
        raise ValueError("the base prices carry no price per core-second")
    record = copy.deepcopy(base)
    per_unit = dict(record.get("core_seconds_per_unit") or {})
    spread = dict(record.get("core_seconds_spread") or {})
    for key in READ_PRICES:
        per_unit[key] = fit["core_seconds"][key]
        spread[key] = fit["spread"][key]
        record[key] = fit["core_seconds"][key] * core
    record.update(
        core_seconds_per_unit=per_unit, core_seconds_spread=spread,
        measured_utc=now_utc(), db_bench_sha256=db_bench_sha256,
        method="stage 38: D-15 §3's read fits (18_calibrate_prices.py "
               "round_record) on one set of price trees, the median over "
               "its rounds; everything else carried from the base",
        machine_calibration={
            "host": platform.node(),
            "base_prices_sha256": hashlib.sha256(
                json.dumps(base, sort_keys=True).encode()).hexdigest(),
            "rounds": [{k: r[k] for k in (*READ_PRICES, "get_overhead",
                                          "seek_intercept")}
                       for r in fit["rounds"]],
            "carried_from_base": [k for k in CARRIED if k in base],
            "carried_why": "c_open and the reopen timer: the suite keeps "
                           "every table open (open_files -1), so no run "
                           "reopens a table; c_s and the core price: the "
                           "contract's money; c_w: stage 30 replaces it by "
                           "the job fit's"})
    return record


def reads_command(args) -> int:
    fit = fit_reads(read_rounds(args.runs))
    record = machine_base(json.loads(args.base.read_text()), fit,
                          args.db_bench_sha256)
    write_json(args.out, record)
    print("[38 reads] " + ", ".join(
        f"{k} {fit['core_seconds'][k]:.4g} s" for k in READ_PRICES) +
        f" ({len(fit['rounds'])} rounds); wrote {args.out}")
    return 0


# --------------------------------------------------------------- natives
def parse_native(pairs: list[str]) -> dict[str, Path]:
    out = {}
    for pair in pairs:
        workload, sep, path = pair.partition("=")
        if not sep or workload not in WORKLOADS:
            raise ValueError(f"--native wants W<n>=<summary.csv>; got {pair!r}")
        if workload in out:
            raise ValueError(f"{workload} given twice")
        out[workload] = Path(path)
    return out


def native_row(summary: Path) -> dict:
    """The summary's one settled native row."""
    with summary.open(newline="") as f:
        rows = [r for r in csv.DictReader(f)
                if r.get("arm") == "native" and r.get("settle_ok") == "1"]
    if len(rows) != 1:
        raise ValueError(f"{summary}: {len(rows)} settled native rows, not 1")
    row = rows[0]
    rate = float(row["throughput_ops_per_second"])
    if not (math.isfinite(rate) and rate > 0):
        raise ValueError(f"{summary}: throughput {rate!r} is not positive")
    return row


def arm_dirs_command(args) -> int:
    for _, summary in sorted(parse_native(args.native).items()):
        print(native_row(summary)["result_directory"])
    return 0


# --------------------------------------------------------------- compose
def qbar_table(natives: dict[str, Path]) -> dict:
    table = {}
    for workload, summary in sorted(natives.items()):
        row = native_row(summary)
        table[workload] = {
            "ops_per_second": float(row["throughput_ops_per_second"]),
            "measured_operations": float(row["measured_operations"]),
            "size_ratio": row.get("size_ratio"),
            "result_directory": row.get("result_directory"),
            "summary": str(summary)}
    return table


def measure_c_set(results: list[Path], reports: list[Path], prices: dict,
                  options_bytes: float | None) -> dict:
    """37's c_set over every given turn arm's accepted calls, pooled; the
    OPTIONS bytes from the reports (their largest) unless given."""
    # 37 needs rl_agent/learner; loaded only here, so the fits need neither.
    turn_check = _module("check_turn_run_38", "37_check_turn_run.py")
    decisions = []
    for result in results:
        decisions += turn_check.read_jsonl(result / "decisions.jsonl")
    if options_bytes is None:
        found = [json.loads(p.read_text())["checks"]["c_set"].get("options_bytes")
                 for p in reports]
        found = [float(v) for v in found if isinstance(v, (int, float))]
        options_bytes = max(found) if found else None
    out = turn_check.c_set(decisions, prices, options_bytes)
    if "c_set" not in out:
        raise ValueError("no accepted SetOptions call with apply_us in "
                         + ", ".join(map(str, results)))
    out["sources"] = [str(p) for p in results]
    return out


def price_ratios(prices: dict, reference: dict) -> dict:
    new, ref = prices["core_seconds_per_unit"], reference["core_seconds_per_unit"]
    out = {}
    for key in sorted(set(new) | set(ref)):
        a, b = new.get(key), ref.get(key)
        entry = {"core_seconds": a, "reference": b}
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b > 0:
            entry["ratio"] = a / b
        out[key] = entry
    return out


def kappa_table(prices: dict, reference: dict) -> dict:
    out = {"lambda": {"value": prices["kappa"].get("lambda"),
                      "reference": reference["kappa"].get("lambda")}}
    for part in ("B", "J"):
        for step, value in sorted(prices["kappa"][part].items()):
            ref = reference["kappa"][part].get(step)
            out[f"{part}.{step}"] = {"value": value, "reference": ref}
    return out


def report_md(cal: dict) -> str:
    lines = [f"# Machine calibration, {cal['host']}, {cal['written_utc']}", "",
             f"Setting: {cal['setting']}", "", "## q-bar (ops/s)", "",
             "| Workload | ops/s | measured ops |", "|---|---:|---:|"]
    for w, q in cal["qbar"].items():
        lines.append(f"| {w} | {q['ops_per_second']:,.0f} | "
                     f"{q['measured_operations']:,.0f} |")
    c = cal.get("c_set")
    lines += ["", "## c_set", ""]
    if c:
        lines.append(f"{c['c_set']:.4g} (median SetOptions {c['median_us']:.0f} us "
                     f"over {c['calls']} calls; OPTIONS bytes "
                     f"{c.get('options_bytes', 'none')})")
    else:
        lines.append("not measured")
    lines += ["", "## Prices (core-seconds per unit) vs the reference", "",
              "| Price | This machine | Reference | Ratio |", "|---|---:|---:|---:|"]
    for key, p in cal["prices"].items():
        ratio = p.get("ratio")
        fmt = (lambda v: "" if v is None else f"{v:.4g}")
        lines.append(f"| {key} | {fmt(p['core_seconds'])} | {fmt(p['reference'])} | "
                     f"{'' if ratio is None else f'{ratio:.2f}'} |")
    return "\n".join(lines) + "\n"


def compose_command(args) -> int:
    natives = parse_native(args.native)
    prices = json.loads(args.prices.read_text())
    reference = json.loads(args.reference.read_text())
    for name, record in (("prices", prices), ("reference", reference)):
        if record.get("schema") != 6 or record.get("cost_model") != 2:
            raise ValueError(f"--{name} must be a schema-6 (cost model 2) record")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    qbar = qbar_table(natives)
    cal = {"host": platform.node(), "written_utc": now_utc(),
           "setting": args.setting, "qbar": qbar,
           "prices_file": str(args.prices),
           "prices_sha256": hashlib.sha256(args.prices.read_bytes()).hexdigest(),
           "reference_file": str(args.reference),
           "prices": price_ratios(prices, reference),
           "kappa": kappa_table(prices, reference),
           "n_win": prices.get("n_win")}
    if args.jobs:
        jobs = json.loads(args.jobs.read_text())
        cal["l0_merge_span_ops_percentiles"] = jobs.get("l0_merge_span_ops_percentiles")
        cal["c_cr_combined"] = jobs.get("c_cr_combined")
    settings = json.loads(args.turn_settings.read_text())
    if args.turn_result:
        cal["c_set"] = measure_c_set(args.turn_result, args.turn_report or [],
                                     prices, args.options_bytes)
        settings["trainer"]["c_set"] = cal["c_set"]["c_set"]
        settings["status"] = (settings.get("status", "") + f" c_set measured on "
                              f"{cal['host']} by stage 38 ({cal['written_utc']}).")
    write_json(args.out_dir / "qbar.json",
               {w: q["ops_per_second"] for w, q in qbar.items()})
    write_json(args.out_dir / "turn_settings.machine.json", settings)
    write_json(args.out_dir / "calibration.json", cal)
    (args.out_dir / "report.md").write_text(report_md(cal))
    print("[38 compose] q-bar " + ", ".join(
        f"{w} {q['ops_per_second']:,.0f}" for w, q in qbar.items()) +
        (f"; c_set {cal['c_set']['c_set']:.4g}" if "c_set" in cal else
         "; c_set not measured") + f"; wrote {args.out_dir}")
    return 0


# ------------------------------------------- once in the machine's life
SEALED = "MACHINE_CALIBRATED"
# What a sealed calibration hands the runs: the name 36 reads, and the file.
SEALED_FILES = {"PRICES_FILE": "prices.machine.json", "QBAR_FILE": "qbar.json",
                "TURN_SETTINGS_FILE": "turn_settings.machine.json",
                "CALIBRATION": "calibration.json"}
# The fields that name a machine. A home folder copied to a new node keeps
# the hostname's folder but not /etc/machine-id or the drive.
KEY_FIELDS = ("host", "machine_id", "disk_serial")


def _command_output(command: list[str]) -> str:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def machine_key(db_root: Path) -> dict:
    """The machine q-bar is measured on: host, machine id, and the drive that
    holds db_root (the nearest existing folder up its path)."""
    path = Path(os.path.realpath(db_root))
    while not path.exists() and path != path.parent:
        path = path.parent
    source = _command_output(["findmnt", "-n", "-o", "SOURCE", "--target", str(path)])
    parent = _command_output(["lsblk", "-ndo", "PKNAME", source]) if source else ""
    disk = f"/dev/{parent}" if parent else source
    try:
        machine_id = Path("/etc/machine-id").read_text().strip()
    except OSError:
        machine_id = ""
    return {"host": platform.node(), "machine_id": machine_id or "unknown",
            "filesystem": source or "unknown", "disk": disk or "unknown",
            "disk_serial": (_command_output(["lsblk", "-ndo", "SERIAL", disk])
                            if disk else "") or "unknown",
            "disk_model": (_command_output(["lsblk", "-ndo", "MODEL", disk])
                           if disk else "") or "unknown"}


def same_machine(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in KEY_FIELDS)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def machine_key_command(args) -> int:
    print(json.dumps(machine_key(args.db_root), indent=2, sort_keys=True))
    return 0


def seal_command(args) -> int:
    d = args.dir
    if (d / SEALED).exists():
        raise ValueError(f"{d / SEALED} exists: this machine's calibration is sealed")
    for name in SEALED_FILES.values():
        if not (d / name).is_file():
            raise ValueError(f"{d / name} is missing")
    cal = json.loads((d / SEALED_FILES["CALIBRATION"]).read_text())
    if not isinstance(cal.get("c_set"), dict) or "c_set" not in cal["c_set"]:
        raise ValueError("c_set was not measured (no preflight turn smoke under "
                         "the preflight's work folder): run 13, then 38's fit again")
    qbar = json.loads((d / SEALED_FILES["QBAR_FILE"]).read_text())
    for workload in args.workload:
        value = qbar.get(workload)
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value) or value <= 0:
            raise ValueError(f"no positive q-bar for {workload} in qbar.json")
    prices = json.loads((d / SEALED_FILES["PRICES_FILE"]).read_text())
    if prices.get("schema") != 6 or prices.get("cost_model") != 2:
        raise ValueError("prices.machine.json is not a schema-6 (cost model 2) record")
    record = {"schema": 1, "sealed_utc": now_utc(), "machine": machine_key(args.db_root),
              "db_root": os.path.realpath(args.db_root),
              "db_bench_identity": args.db_bench_sha256,
              "root_head": args.root_head, "fork_head": args.fork_head,
              "setting": args.setting, "workloads": sorted(qbar),
              "qbar": qbar, "c_set": cal["c_set"]["c_set"],
              "files": {key: {"name": name, "sha256": _sha256(d / name)}
                        for key, name in SEALED_FILES.items()}}
    write_json(d / SEALED, record)
    print(f"[38 seal] {d}: id {_sha256(d / SEALED)[:16]}, {record['machine']['host']}, "
          f"disk {record['machine']['disk_serial']}; q-bar "
          + ", ".join(f"{w} {v:,.0f}" for w, v in sorted(qbar.items()))
          + f"; c_set {record['c_set']:.4g}")
    return 0


def status_command(args) -> int:
    d = args.dir
    sealed = d / SEALED
    if not sealed.is_file():
        print(f"[38 status] no sealed calibration in {d}", file=sys.stderr)
        return 3
    record = json.loads(sealed.read_text())
    here = machine_key(args.db_root)
    if not same_machine(record["machine"], here):
        there = record["machine"]
        print(f"[38 status] {d} is the calibration of another machine: "
              + ", ".join(f"{k} {there.get(k)} (here {here.get(k)})" for k in KEY_FIELDS),
              file=sys.stderr)
        return 4
    for key, entry in record["files"].items():
        path = d / entry["name"]
        if not path.is_file() or _sha256(path) != entry["sha256"]:
            print(f"[38 status] {path} changed since it was sealed", file=sys.stderr)
            return 6
    for key, entry in record["files"].items():
        if key != "CALIBRATION":
            print(f"{key}={d.resolve() / entry['name']}")
    print(f"MACHINE_CAL_ID={_sha256(sealed)[:16]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("reads")
    p.add_argument("--runs", type=Path, required=True)
    p.add_argument("--base", type=Path, required=True)
    p.add_argument("--db-bench-sha256", required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("arm-dirs")
    p.add_argument("--native", action="append", required=True)
    p = sub.add_parser("compose")
    p.add_argument("--native", action="append", required=True)
    p.add_argument("--prices", type=Path, required=True)
    p.add_argument("--reference", type=Path, required=True)
    p.add_argument("--turn-settings", type=Path, required=True)
    p.add_argument("--turn-result", type=Path, action="append")
    p.add_argument("--turn-report", type=Path, action="append")
    p.add_argument("--options-bytes", type=float)
    p.add_argument("--jobs", type=Path)
    p.add_argument("--setting", default="")
    p.add_argument("--out-dir", type=Path, required=True)
    p = sub.add_parser("machine-key")
    p.add_argument("--db-root", type=Path, required=True)
    p = sub.add_parser("seal")
    p.add_argument("--dir", type=Path, required=True)
    p.add_argument("--db-root", type=Path, required=True)
    p.add_argument("--workload", action="append", required=True, choices=WORKLOADS)
    p.add_argument("--db-bench-sha256", required=True)
    p.add_argument("--root-head", default="unknown")
    p.add_argument("--fork-head", default="unknown")
    p.add_argument("--setting", default="")
    p = sub.add_parser("status")
    p.add_argument("--dir", type=Path, required=True)
    p.add_argument("--db-root", type=Path, required=True)
    args = parser.parse_args()
    command = {"reads": reads_command, "arm-dirs": arm_dirs_command,
               "compose": compose_command, "machine-key": machine_key_command,
               "seal": seal_command, "status": status_command}[args.command]
    try:
        return command(args)
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        print(f"[38] refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
