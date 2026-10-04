#!/usr/bin/env python3
"""Stage 30: write a schema-6 prices file, cost model 2's provisional prices
(PREREGISTRATION D-23 §3(d), D-24 §2: the exploratory track).

It starts from a schema-4 or schema-5 prices file (stage 18's read prices,
c_open, the reopen timer, c_s and the core price), and adds the prices of
D-23's cost model: the job prices by kind, c_cr, the refitted c_w, c_st,
c_ib, c_mt, c_get0, c_sc0, c_put, kappa and lambda, n_win and n_str.

The new values come from calibration files (stage 31's output, or A6's job
fit), each a JSON object of
    {"source": "...", "core_seconds": {"job_flush": ..., "job_l0": ...,
     "job_deep": ..., "job_move": ..., "c_w": ..., "c_cr": ..., "c_st": ...,
     "c_ib": ..., "c_mt": ..., "c_get0": ..., "c_sc0": ..., "c_put": ...},
     "c_cr_combined": true, "kappa": {"lambda": ..., "B": {x: ...},
     "J": {x: {kind: ...}}, "basis": "..."}, "reference_traffic_mb_s": ...}
with every key optional; later files override earlier ones, key by key.
Anything still missing is refused, unless --placeholders fills it from the
owner-confirmed placeholder table below (2026-10-04, the interim interface
§6), and the file then lists every placeholder it holds.

Every price is money: core-seconds times the contract's price per
core-second, which the base file carries (D-15 §3a). The result is checked
by cost_model_v2.load_prices before it is written, atomically.

    30_provisional_prices_v2.py --base build-dbbench/prices.json \\
        --calibration a6_calibration_assoc.json [--calibration ...] \\
        [--placeholders] --out build-dbbench/prices.provisional.json
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

import cost_model_v2

PIPELINE_DIR = Path(__file__).resolve().parent
KAPPA_TYPES = cost_model_v2.STEP_TYPES
# The new prices, by their key in core_seconds, and where each goes.
NEW_PRICES = {"c_w": "c_w", "c_cr": "c_cr", "c_st": "c_st", "c_ib": "c_ib",
              "c_mt": "c_mt", "c_get0": "c_get0", "c_sc0": "c_sc0",
              "c_put": "c_put"}
JOB_KEYS = {f"job_{kind}": kind for kind in cost_model_v2.KINDS}

# Owner-confirmed placeholders (2026-10-04; ~/node_ops/drafts/
# interim-interface.md §3 and §6), used only with --placeholders and named in
# the output. Core-seconds; kappa^B in seconds per byte (a slowdown per byte/s).
KAPPA_BLOCK = 0.9e-9
PLACEHOLDERS = {
    "c_st": (0.341e-6, "upper bound: the Assoc T=10 pilots' client time "
             "outside Get, Seek and write (>= 208 s) over their returned plus "
             "hidden steps (496M + 114M); it includes client work"),
    "c_ib": (0.0, "iterator blocks are not counted on the interim binary "
             "(D-24 §4)"),
    "c_mt": (0.22e-6, "2026-10-03 contention diagnostic: a read takes 0.22 us "
             "more whenever writes keep the memtable populated"),
    "c_get0": (0.5e-6, "unmeasured; policy-independent, cancels between arms"),
    "c_sc0": (1.0e-6, "unmeasured; policy-independent, cancels between arms"),
    "c_put": (0.50e-6, "median rocksdb.db.write.micros of the d21id q-bar "
              "arm, Assoc T=10 repeat 1 (WAL off; the mean holds stall waits)"),
}
PLACEHOLDER_KAPPA = {
    "lambda": 0.0,
    "B": {"probe": 1.2e-9, "block": 0.9e-9, "seek": 0.8e-9,
          **{x: KAPPA_BLOCK for x in ("reopen", "step", "iblock", "memtable",
                                      "get0", "scan0", "put")}},
    "J": {x: {kind: 0.0 for kind in cost_model_v2.KINDS} for x in KAPPA_TYPES},
    "basis": "placeholder: 2026-10-03 contention diagnostics (0.12%, 0.09%, "
             "0.08% per MB/s for probes, blocks, seeks); other step types "
             "assumed equal to block reads; no busy part",
}


def merge(calibrations: list[dict]) -> tuple[dict, dict, dict, dict]:
    """(core-seconds, kappa, sources, extras) merged key by key."""
    seconds, kappa, sources, extras = {}, {}, {}, {}
    for cal in calibrations:
        source = cal.get("source", "unnamed calibration")
        for key, value in (cal.get("core_seconds") or {}).items():
            if key not in NEW_PRICES and key not in JOB_KEYS:
                raise ValueError(f"unknown core_seconds key {key!r} in {source}")
            seconds[key] = value
            sources[key] = source
        if "kappa" in cal:
            kappa = json.loads(json.dumps(cal["kappa"]))
            sources["kappa"] = source
        for key in ("c_cr_combined", "reference_traffic_mb_s"):
            if key in cal:
                extras[key] = cal[key]
                sources[key] = source
    return seconds, kappa, sources, extras


def build(base: dict, calibrations: list[dict], placeholders: bool,
          n_win: int, n_str: int) -> dict:
    if base.get("schema") not in (4, 5):
        raise ValueError("the base prices must be stage 18's schema 4 or 5")
    core_price = base["price_per_core_second"]
    seconds, kappa, sources, extras = merge(calibrations)
    used = []
    if placeholders:
        for key, (value, why) in PLACEHOLDERS.items():
            if key not in seconds:
                seconds[key] = value
                sources[key] = "PLACEHOLDER: " + why
                used.append(key)
        if not kappa:
            kappa = json.loads(json.dumps(PLACEHOLDER_KAPPA))
            sources["kappa"] = "PLACEHOLDER: " + PLACEHOLDER_KAPPA["basis"]
            used.append("kappa")
    missing = [k for k in (*NEW_PRICES, *JOB_KEYS) if k not in seconds]
    if not kappa:
        missing.append("kappa")
    if missing:
        raise ValueError("no value for " + ", ".join(missing) +
                         " (a calibration file, or --placeholders)")
    record = {k: v for k, v in base.items()
              if k not in ("schema", "kind", "reproducibility")}
    record.update(
        schema=cost_model_v2.PRICES_SCHEMA, kind="provisional", cost_model=2,
        base_prices_sha256=hashlib.sha256(
            json.dumps(base, sort_keys=True).encode()).hexdigest(),
        base_schema=base.get("schema"),
        written_utc=datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        job_prices={kind: seconds[key] * core_price
                    for key, kind in JOB_KEYS.items()},
        kappa=kappa, n_win=n_win, n_str=n_str,
        sources=sources, placeholders=used,
        c_cr_combined=bool(extras.get("c_cr_combined", False)))
    for key, target in NEW_PRICES.items():
        record[target] = seconds[key] * core_price
    if "reference_traffic_mb_s" in extras:
        record["reference_traffic_mb_s"] = extras["reference_traffic_mb_s"]
    per_unit = dict(record.get("core_seconds_per_unit") or {})
    per_unit.update({key: seconds[key] for key in (*NEW_PRICES, *JOB_KEYS)})
    record["core_seconds_per_unit"] = per_unit
    cost_model_v2.load_prices(record)  # refuses anything malformed
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, action="append",
                        default=[])
    parser.add_argument("--placeholders", action="store_true")
    parser.add_argument("--n-win", type=int, default=1000)
    parser.add_argument("--n-str", type=int, default=100)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        record = build(json.loads(args.base.read_text()),
                       [json.loads(p.read_text()) for p in args.calibration],
                       args.placeholders, args.n_win, args.n_str)
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        print(f"[30] refused: {error}", file=sys.stderr)
        return 1
    tmp = args.out.with_suffix(args.out.suffix + ".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, args.out)
    print(f"[30] wrote {args.out} (schema 6, provisional"
          + (f"; placeholders: {', '.join(record['placeholders'])}"
             if record["placeholders"] else "") + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
