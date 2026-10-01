#!/usr/bin/env python3
"""Record q-bar by rule (PREREGISTRATION D-13 §1 as amended by D-14 §2): the
mean measured-phase throughput of five `native` arms at T=10, per workload.

Takes, per workload key of the contract's reference_rate (assoc,
powerlaw_get95), 04's summary.csv of that workload's q-bar arms. Refuses the
file unless it is exactly what D-14 §2 describes:
  - five rows, each a `native` arm at T=10 that 04 scored: settled, run under
    this contract and without prices (objective status "no prices"; D-15
    §3(e) runs the q-bar arms before any price exists), with a positive
    throughput, and no arm in refused_arms.json beside it;
  - one session, one db_bench binary, one configuration, five seeds;
  - the workload family the key names, and that workload's own fingerprint
    fields: Assoc's are config.sh's defaults (D-1: keyrange_num 30,
    value_theta, mix, scan lengths, no power-law segment); the power law's
    are the contract's `workloads.powerlaw_get95` (mix, keyrange_num 1,
    key_dist_a/b) with config.sh's scan lengths. The value-size fit is not in
    the power law's fingerprint, so it cannot be checked;
  - the pipeline's default engine options (config.sh's defaults, m = 1),
    the preregistered settle hold, and one of D-16's run-length rungs;
  - that rung is the one Gate N1 chose for the workload (D-14 §2, D-16 §6):
    the longest of the rungs in its admission_T{2,6,10}.json, in the folder
    --admission names (24's <NVME>/n1-<workload>), read by gate_n1_reports.
Refuses too if the contract already holds a value for the key (it is
frozen, and a recorded q-bar is never overwritten), and if the workloads'
arms ran different db_bench binaries, including one recorded earlier (the
amendment's reason carries the binary's hash).

Prints each q-bar and a drafted dated record for PREREGISTRATION, under
D-14 §2 (to --output if given). With --write it also amends the contract in
place, in the file's own JSON formatting: the values, one `amendments`
entry, and status_note's clause on the open reference rates, rewritten to
the ones still null (left alone, with a reminder, if the note was edited).
All workloads given are checked before anything is written.

  26_record_qbar.py --summary assoc=<qbar-assoc>/graphs/summary.csv \\
                    --admission assoc=<NVME>/n1-assoc \\
                    --summary powerlaw_get95=<qbar-powerlaw>/graphs/summary.csv \\
                    --admission powerlaw_get95=<NVME>/n1-powerlaw
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from datetime import date
from pathlib import Path

from fingerprint import parse_fingerprint_options
import gate_n1_reports
import research_objective

PIPELINE = Path(__file__).resolve().parent

ARMS = 5
SIZE_RATIO = 10
# 04's status for a settled Programme 1 arm under this contract that ran
# without a prices file.
SCORED = "no prices"
ENTRY = "PREREGISTRATION D-14 §2"
BINARY = re.compile(r"db_bench ([0-9a-f]{64})")
# status_note's clause on the reference rates still open.
NOTE_RATES = re.compile(r"the (?:two reference rates|\w+ reference rate) from "
                        r"five native arms at T=10(?: per workload)? \(D-14 §2; "
                        r"a dated amendment\), and ")
# Fingerprint options and the config.sh variables whose defaults they must
# carry: the engine options. The workload's own fields: workload_fields.
DEFAULT_OPTIONS = {
    "key_size": "KEY_SIZE", "value_size": "VALUE_SIZE",
    "write_buffer_size": "WRITE_BUFFER_SIZE",
    "target_file_size": "TARGET_FILE_SIZE", "block_size": "BLOCK_SIZE",
    "max_bytes_for_level_base": "MAX_BYTES_FOR_LEVEL_BASE",
    "num_levels": "NUM_LEVELS",
    "level0_file_num_compaction_trigger": "L0_COMPACTION_TRIGGER",
    "level0_slowdown_writes_trigger": "L0_SLOWDOWN_TRIGGER",
    "level0_stop_writes_trigger": "L0_STOP_TRIGGER",
    "compaction_priority": "COMPACTION_PRIORITY",
    "block_cache_size": "BLOCK_CACHE_SIZE", "bloom_bits": "BLOOM_BITS",
    "max_background_jobs": "MAX_BACKGROUND_JOBS", "threads": "THREADS",
    "disable_wal": "DISABLE_WAL", "use_direct_io": "USE_DIRECT_IO",
    "soft_pending_compaction_bytes_limit": "SOFT_PENDING_BYTES",
    "hard_pending_compaction_bytes_limit": "HARD_PENDING_BYTES",
}
ADMISSION = PIPELINE.parents[1] / "config" / "admission_test.json"


def config_defaults(path: Path = PIPELINE / "config.sh") -> dict[str, str]:
    """NAME -> default of every NAME="${NAME:-default}" line of config.sh."""
    return dict(re.findall(r'^([A-Z0-9_]+)="\$\{\1:-([^}]*)\}"',
                           path.read_text(), re.M))


def workload_fields(key: str, defaults: dict[str, str], contract: dict) -> dict:
    """The workload's own fingerprint fields as D-1 (Assoc, config.sh's
    defaults) and D-13 §3 (the power law, the contract's workload) fix them.
    A field whose segment is absent parses as None (keyrange_num as 1)."""
    fields = {"scan_length": int(defaults["SCAN_LENGTH"]),
              "mix_max_scan_length": int(defaults["MIX_MAX_SCAN_LENGTH"])}
    if key == "assoc":
        return {**fields, "keyrange_num": int(defaults["KEYRANGE_NUM"]),
                "value_theta": float(defaults["VALUE_THETA"]),
                "key_dist_a": None, "key_dist_b": None,
                **{f"mix_{op}_ratio": float(defaults[f"MIX_{op.upper()}_RATIO"])
                   for op in ("get", "put", "seek")}}
    spec = contract["workloads"][key]
    return {**fields, "keyrange_num": spec["keyrange_num"], "value_theta": None,
            **{name: spec[name] for name in (
                "key_dist_a", "key_dist_b", "mix_get_ratio", "mix_put_ratio",
                "mix_seek_ratio")}}


def updated_note(note: str, rates: dict):
    """status_note with its clause on the open reference rates matching the
    nulls left; None when the clause is not there (edited by hand)."""
    if not NOTE_RATES.search(note):
        return None
    left = [key for key, value in rates.items() if value is None]
    clause = "" if not left else (
        f"the {left[0]} reference rate from five native arms at T=10 "
        "(D-14 §2; a dated amendment), and ")
    return NOTE_RATES.sub(lambda _: clause, note, count=1)


def dump(contract: dict) -> str:
    """The contract file's own formatting."""
    return json.dumps(contract, indent=2, ensure_ascii=False) + "\n"


def number(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def one(rows: list[dict], column: str) -> str:
    values = {row.get(column) or "" for row in rows}
    if len(values) != 1 or "" in values:
        raise ValueError(f"not one {column}: {sorted(values)}")
    return values.pop()


def check(key: str, rows: list[dict], refused: list, contract: dict,
          defaults: dict[str, str], rungs: list[dict]) -> dict:
    """The q-bar of one workload, or ValueError naming what D-14 §2 refuses."""
    rates = contract["reference_rate"]["ops_per_second"]
    if key not in rates:
        raise ValueError(f"not a workload of the contract: {sorted(rates)}")
    if research_objective.reference_rate(contract, key) is not None:
        raise ValueError(f"the contract already holds q-bar = {rates[key]}; "
                         "it is frozen and never overwritten")
    if refused:
        raise ValueError(f"04 refused {len(refused)} arm(s) (refused_arms.json); "
                         "every q-bar arm must be scored")
    if len(rows) != ARMS:
        raise ValueError(f"{len(rows)} rows, not {ARMS}")
    for row in rows:
        where = row.get("result_directory", "?")
        if row.get("arm") != "native" or number(row.get("size_ratio")) != SIZE_RATIO:
            raise ValueError(f"{where}: not a native arm at T={SIZE_RATIO}")
        if row.get("workload_family") != key:
            raise ValueError(f"{where}: workload family "
                             f"{row.get('workload_family')!r}, not {key!r}")
        if (number(row.get("settle_ok")) != 1 or
                row.get("objective_status") != SCORED):
            raise ValueError(f"{where}: not a scored Programme 1 arm run without "
                             f"prices under this contract (settle_ok "
                             f"{row.get('settle_ok')!r}, status "
                             f"{row.get('objective_status')!r}; D-15 §3(e) runs "
                             f"the q-bar arms before any price exists)")
        rate = number(row.get("throughput_ops_per_second"))
        if not (math.isfinite(rate) and rate > 0):
            raise ValueError(f"{where}: throughput {rate} is not positive")
    session, binary = one(rows, "session_id"), one(rows, "dbbench_sha256")
    fingerprint = one(rows, "experiment_fingerprint")
    if len({row.get("dbbench_seed") for row in rows}) != ARMS:
        raise ValueError("the five arms do not have five seeds")
    try:
        options = parse_fingerprint_options(fingerprint)
    except SystemExit as error:
        raise ValueError(str(error)) from None
    wrong = {name: (options[name], defaults[var])
             for name, var in DEFAULT_OPTIONS.items()
             if str(options[name]) != defaults[var]}
    for name in ("level_target_multipliers", "static_capacity_scales"):
        if options[name] != "off":
            wrong[name] = (options[name], "off")
    hold = contract["measured_phase"]["hold_seconds"]
    if options["settle_hold_seconds"] != hold:
        wrong["settle_hold_seconds"] = (options["settle_hold_seconds"], hold)
    if wrong:
        raise ValueError("not the pipeline's default options: " + ", ".join(
            f"{name} {got} (default {want})" for name, (got, want) in wrong.items()))
    wrong = {name: (options[name], want)
             for name, want in workload_fields(key, defaults, contract).items()
             if options[name] != want}
    if wrong:
        raise ValueError(f"not the {key} workload: " + ", ".join(
            f"{name} {got} (expected {want})" for name, (got, want) in wrong.items()))
    rung = {"size_millions": options["size_millions"],
            "load_percent": options["load_percent"]}
    if rung not in rungs:
        raise ValueError(f"{rung} is not a D-16 rung: {rungs}")
    values = [number(row["throughput_ops_per_second"]) for row in rows]
    return {"qbar": statistics.fmean(values), "values": values,
            "sd": statistics.stdev(values), "session": session,
            "binary": binary, **rung}


def reason(results: dict[str, dict]) -> str:
    parts = "; ".join(
        f"{key} {r['qbar']:.1f} ops/s (session {r['session']}, "
        f"{r['size_millions']}M at {r['load_percent']}% load, "
        f"db_bench {r['binary']})"
        for key, r in results.items())
    return ("q-bar measured by rule, not chosen: the mean measured-phase "
            "throughput of five native arms at T=10 per workload, before any "
            f"Theta_s run: {parts}")


def draft(results: dict[str, dict], day: str) -> str:
    """A dated record for PREREGISTRATION, under D-14 §2."""
    binaries = sorted({r["binary"] for r in results.values()})
    lines = [
        f"**Dated record, {day}: $\\bar q$ measured (D-14 §2).** "
        "Recorded after the $\\bar q$ arms and before any $\\Theta_s$ run. "
        "The values are fixed by the rule, not chosen: the mean of "
        "`throughput_ops_per_second` over five `native` arms at $T = 10$, "
        "run through `03` with the settle step and the pipeline's default "
        f"options on `db_bench` `{', '.join(b[:12] for b in binaries)}`, "
        "checked by `26_record_qbar.py`.",
        "",
    ]
    for key, r in results.items():
        cv = r["sd"] / r["qbar"]
        runs = ", ".join(f"{v:.1f}" for v in r["values"])
        lines.append(
            f"- `{key}`: $\\bar q$ = **{r['qbar']:.1f} ops/s** (contract "
            f"value `{r['qbar']!r}`); session "
            f"`{r['session']}`, {r['size_millions']}M at {r['load_percent']}% "
            f"load; runs {runs}; SD {r['sd']:.1f} ({cv:.2%}).")
    lines += [
        "",
        "The contract's `reference_rate.ops_per_second` holds these values, "
        f"amended in place on {day}. From here on every Programme 1 arm of "
        "these workloads carries its `qbar` fingerprint segment, and `04` "
        "prices $\\mathcal C_S$ with it.",
    ]
    return "\n".join(lines) + "\n"


def amend(path: Path, results: dict[str, dict], day: str) -> bool:
    """The values, one amendments entry and status_note's rate clause; every
    other byte unchanged. False when status_note was left for the owner."""
    raw = path.read_text(encoding="utf-8")
    contract = json.loads(raw)
    if dump(contract) != raw:
        raise ValueError(f"{path} is not in json.dumps(indent=2, "
                         "ensure_ascii=False) form; refusing to reformat it")
    for key, r in results.items():
        contract["reference_rate"]["ops_per_second"][key] = r["qbar"]
    contract["amendments"].append({"date": day, "entry": ENTRY,
                                   "reason": reason(results)})
    note = updated_note(contract["status_note"],
                        contract["reference_rate"]["ops_per_second"])
    if note is not None:
        contract["status_note"] = note
    path.write_text(dump(contract), encoding="utf-8")
    return note is not None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--summary", action="append", required=True,
                        metavar="WORKLOAD=CSV",
                        help="a contract workload key and 04's summary.csv "
                             "of its q-bar arms; repeatable")
    parser.add_argument("--admission", action="append", required=True,
                        metavar="WORKLOAD=DIR",
                        help="the workload's Gate N1 folder (24's <NVME>/n1-"
                             "<workload>), whose admission reports fix its "
                             "rung (D-16 §6); one per --summary")
    parser.add_argument("--contract", type=Path,
                        default=research_objective.DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path,
                        help="write the drafted record here, not to stdout")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--write", action="store_true",
                        help="amend the contract in place")
    args = parser.parse_args(argv)

    results = {}
    try:
        contract = json.loads(args.contract.read_text(encoding="utf-8"))
        defaults = config_defaults()
        rungs = json.loads(ADMISSION.read_text())["rungs"]
        admission = {}
        for item in args.admission:
            key, sep, folder = item.partition("=")
            if not sep:
                raise ValueError(f"--admission {item!r}: give WORKLOAD=DIR")
            admission[key] = Path(folder)
        for item in args.summary:
            key, sep, csv_path = item.partition("=")
            if not sep or key in results:
                raise ValueError(f"--summary {item!r}: give WORKLOAD=CSV, "
                                 "once per workload")
            summary = Path(csv_path)
            with summary.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            refused = json.loads((summary.parent / "refused_arms.json").read_text())
            try:
                results[key] = check(key, rows, refused, contract, defaults, rungs)
                if key not in admission:
                    raise ValueError("no --admission folder, so not the rung "
                                     "Gate N1 chose")
                chosen = gate_n1_reports.workload_rung(admission[key])
                ran = (results[key]["size_millions"], results[key]["load_percent"])
                if ran != chosen:
                    raise ValueError(
                        f"ran {ran[0]}M at {ran[1]}% load, but Gate N1 chose "
                        f"{chosen[0]}M at {chosen[1]}% (D-16 §6, {admission[key]})")
            except ValueError as error:
                raise ValueError(f"{key}: {error}") from None
        binaries = {r["binary"] for r in results.values()} | {
            b for a in contract["amendments"] if a.get("entry") == ENTRY
            for b in BINARY.findall(a.get("reason", ""))}
        if len(binaries) > 1:
            raise ValueError("the q-bar arms ran different db_bench binaries "
                             f"(this call and the recorded ones): {sorted(binaries)}")
        noted = args.write and amend(args.contract, results, args.date)
    except (ValueError, OSError) as error:
        raise SystemExit(f"[26] refused: {error}") from None

    for key, r in results.items():
        print(f"{key}: q-bar {r['qbar']:.1f} ops/s (mean of {ARMS} runs)",
              file=sys.stderr)
    text = draft(results, args.date)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    if args.write:
        print(f"amended {args.contract}; commit it before any Theta_s run",
              file=sys.stderr)
        if not noted:
            print("status_note was edited by hand, so it was left alone: check "
                  "that it no longer lists these reference rates as open",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
