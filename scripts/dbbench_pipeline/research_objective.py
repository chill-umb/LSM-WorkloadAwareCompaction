"""Load the frozen Programme 1 contract (PREREGISTRATION D-13) and price a
run's costs with it (PATHWAYS D §2).

A run's three priced costs, in USD over the measured phase, are
C_W (bytes written), C_R (filter probes, block-reading probes, run seeks and
the reads' table reopens; D-20, D-21) and C_S (bytes held per operation
served). J_beta weights them by a mode: J = beta_W C_W + beta_R C_R + beta_S C_S.
A run's reopens are priced at c_open only when its own time per reopen is
within the contract's tolerance of stage 18's (D-21, reopen_check).
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

DEFAULT_CONTRACT = (Path(__file__).resolve().parents[2] / "config" /
                    "research_objective_contract.json")
MODES = ("balanced", "read", "write", "space")
# Device prices 18_calibrate_prices.py measures, USD per unit: per byte
# written, per filter probe, per block-reading probe, per run seek, per
# table reopen (core-seconds per unit times the price per core-second,
# D-15 §3 as amended by D-20).
DEVICE_PRICES = ("c_w", "c_f", "c_blk", "c_sk", "c_open")
# The prices file's schema: 3 since D-20 added c_open, so a schema-2 file,
# whose c_f carried the price runs' reopens, cannot price a run; 4 since D-21
# added stage 18's time per reopen by the fork's own timer (reopen_timer),
# the reference every run's reopens are checked against; 5 since D-22
# measures the read prices on archived trees in two sessions that must agree
# within the contract's reproducibility_tolerance. Only a schema-5 file whose
# test passed is final. A schema-4 file, or a schema-5 file of one session or
# of a failed test, holds provisional prices: diagnostic runs only (D-22 j).
PRICES_SCHEMA = 5
# Schema 6 (D-23 §3(d)): cost model 2's prices, written by stage 30. Until
# D-23's calibrations and D-22's two sessions make one final, it is
# provisional: diagnostic runs only.
PROVISIONAL_SCHEMAS = (4, 5, 6)
# The fork's tickers of the reads' reopens and their time (D-21).
READ_REOPENS = "rocksdb.read.table.reopen"
READ_REOPEN_NANOS = "rocksdb.read.table.reopen.nanos"


def load_contract(path: Path = DEFAULT_CONTRACT) -> tuple[dict, str]:
    """The contract and its SHA-256. A copy that differs from the frozen file
    is refused: an amendment is an in-place edit with a written reason."""
    raw = Path(path).read_bytes()
    contract = json.loads(raw)
    if contract != json.loads(DEFAULT_CONTRACT.read_bytes()):
        raise ValueError(f"{path} differs from the frozen contract")
    objective = contract.get("objective", {})
    if (contract.get("schema_version") != 1 or
            contract.get("contract_status") != "frozen" or
            objective.get("weight_order") != ["write", "read", "space"] or
            set(objective.get("modes", {})) != set(MODES)):
        raise ValueError("unsupported research objective contract")
    storage = contract["prices"].get("storage_price_per_byte_second")
    if not (isinstance(storage, (int, float)) and storage > 0):
        raise ValueError("the contract's storage price c_s must be > 0")
    return contract, hashlib.sha256(raw).hexdigest()


def beta_stars(contract: dict) -> list[int]:
    return list(contract["objective"]["reported_beta_star"])


def storage_scales(contract: dict) -> list[float]:
    """c_s itself, then the sensitivity points c_s/2 and 2c_s (OBJ-2)."""
    return [1.0, *contract["prices"]["storage_price_sensitivity"]]


def mode_weights(contract: dict, mode: str,
                 beta_star: float) -> tuple[float, float, float]:
    """(beta_W, beta_R, beta_S) for a mode (PATHWAYS D §2 table)."""
    return tuple(float(beta_star) if w == "beta_star" else float(w)
                 for w in contract["objective"]["modes"][mode])


def j_beta(costs: tuple[float, float, float], weights, cs_scale=1.0) -> float:
    """J at the storage price scaled by cs_scale; costs = (C_W, C_R, C_S)."""
    w, r, s = costs
    return weights[0] * w + weights[1] * r + weights[2] * cs_scale * s


def j_column(mode: str, beta_star: float, cs_scale: float) -> str:
    """Column name of one J in summary.csv. Balanced mode has no beta*."""
    head = "J_balanced" if mode == "balanced" else f"J_{mode}_b{beta_star:g}"
    return f"{head}_cs{cs_scale:g}"


def objective_grid(contract: dict):
    """Every (mode, beta*, c_s scale) the contract reports; balanced once per
    scale, since beta* does not enter it."""
    for scale in storage_scales(contract):
        yield "balanced", 1, scale
        for mode in MODES[1:]:
            for beta in beta_stars(contract):
                yield mode, beta, scale


def objective_columns(contract: dict,
                      costs: tuple[float, float, float]) -> dict[str, float]:
    return {j_column(mode, beta, scale):
            j_beta(costs, mode_weights(contract, mode, beta), scale)
            for mode, beta, scale in objective_grid(contract)}


def validate_prices(prices: dict, contract: dict) -> dict:
    """Prices must be money (OBJ-2): positive, finite device prices, and the
    contract's c_s > 0 and core price > 0. Returns prices with c_s."""
    core = contract["prices"].get("price_per_core_second")
    storage = contract["prices"].get("storage_price_per_byte_second")
    for name, value in (("core price", core), ("c_s", storage),
                        *((key, prices.get(key)) for key in DEVICE_PRICES)):
        if (not isinstance(value, (int, float)) or isinstance(value, bool) or
                not math.isfinite(value) or value <= 0):
            raise ValueError(f"{name} must be a positive money price; "
                             f"got {value!r}")
    if prices.get("c_s", storage) != storage:
        raise ValueError("prices carry a c_s other than the contract's")
    return {**{key: float(prices[key]) for key in DEVICE_PRICES},
            "c_s": float(storage)}


def prices_final(record: dict, contract: dict) -> bool:
    """Whether a prices.json record is final (D-22 f): schema 5, written by
    18's compare from two sessions that agreed within this contract's
    reproducibility_tolerance."""
    test = record.get("reproducibility")
    return (record.get("schema") == PRICES_SCHEMA and
            record.get("kind") == "final" and isinstance(test, dict) and
            test.get("passed") is True and
            test.get("tolerance") ==
            contract["prices"]["reproducibility_tolerance"])


def checked_prices(record: dict, contract: dict,
                   provisional_ok: bool = False) -> dict:
    """A prices.json record of stage 18 as money prices: priced per
    core-second under this contract (D-15 §3a; a draft schema-1 file priced
    per whole machine is 16 times too high), with reopens priced apart
    (D-20; a schema-2 file's c_f carried the price runs' reopens) and a
    reference time per reopen (D-21; a schema-3 file has none), and
    valid (OBJ-2). Final (D-22 f) unless provisional_ok, which only a run
    marked diagnostic may pass (D-22 j)."""
    if (record.get("schema") not in PROVISIONAL_SCHEMAS or
            record.get("price_per_core_second")
            != contract["prices"]["price_per_core_second"]):
        raise ValueError("not priced per core-second with reopens apart and a "
                         "reopen-time reference under this contract (schema "
                         f"{PRICES_SCHEMA}, D-15 §3a, D-20, D-21, D-22)")
    if not (provisional_ok or prices_final(record, contract)):
        raise ValueError("provisional prices: not two sessions that agreed "
                         "within the contract's reproducibility_tolerance "
                         "(D-22 f); only a run marked diagnostic may use "
                         "them (D-22 j)")
    reopen_reference(record)
    return validate_prices(record, contract)


def reproducible(a: float, b: float, tolerance: float) -> bool:
    """D-22 (f): two sessions' values agree when |b - a| <= tolerance times
    their mean."""
    return abs(b - a) <= tolerance * (a + b) / 2


def reopen_reference(record: dict) -> float:
    """Stage 18's seconds per reopen by the fork's own timer (D-21): the
    median over repeats of its capped Gets' reopen time over their reopens."""
    value = (record.get("reopen_timer") or {}).get("seconds_per_reopen")
    if (not isinstance(value, (int, float)) or isinstance(value, bool) or
            not math.isfinite(value) or value <= 0):
        raise ValueError("prices carry no positive reopen_timer."
                         f"seconds_per_reopen (D-21); got {value!r}")
    return float(value)


def reopen_check(contract: dict, reference: float, reopens: float,
                 reopen_seconds: float) -> tuple[str, float]:
    """D-21: whether stage 18's c_open holds for a run. The run's own time
    per reopen, by the same timer as the reference, over its reference.
    Returns ("held", ratio), ("does not hold", ratio) when the ratio is
    outside 1 +- the contract's tolerance (the run is not priced), or ("too
    few reopens", ratio) under the contract's minimum count, whose reopens
    are priced unchecked."""
    rule = contract["prices"]["reopen_time_check"]
    if not (math.isfinite(reopens) and math.isfinite(reopen_seconds)):
        raise ValueError("no reopen count or time to check")
    ratio = (reopen_seconds / reopens / reference if reopens > 0
             else math.nan)
    if reopens < rule["min_reopens"]:
        return "too few reopens", ratio
    if abs(ratio - 1) > rule["tolerance"]:
        return "does not hold", ratio
    return "held", ratio


def priced_costs(prices: dict, rate: float, sst_bytes: float,
                 filter_probes: float, block_probes: float, run_seeks: float,
                 table_reopens: float,
                 held_byte_operations: float) -> tuple[float, float, float]:
    """(C_W, C_R, C_S) of one run (PATHWAYS D §1): SST bytes written at c_w;
    filter probes, block-reading probes, run seeks and the reads' table
    reopens at c_f, c_blk, c_sk and c_open (D-20; the reopens counted by the
    fork since D-21); held byte-operations at c_s / q-bar (Lemma D.15)."""
    return (prices["c_w"] * sst_bytes,
            prices["c_f"] * filter_probes + prices["c_blk"] * block_probes +
            prices["c_sk"] * run_seeks + prices["c_open"] * table_reopens,
            prices["c_s"] / rate * held_byte_operations)


def reference_rate(contract: dict, family: str):
    """q-bar for a workload family, or None while it is unmeasured."""
    return contract["reference_rate"]["ops_per_second"].get(family)


def relative_difference(candidate: float, baseline: float) -> float:
    if not all(math.isfinite(v) and v >= 0 for v in (candidate, baseline)):
        raise ValueError("measurements must be finite and nonnegative")
    if baseline == 0:
        if candidate == 0:
            return 0.0
        raise ValueError("relative difference is undefined against zero baseline")
    return candidate / baseline - 1.0
