"""Load the frozen research objective once for evaluation and frontier tools."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

DEFAULT_CONTRACT = (Path(__file__).resolve().parents[2] / "config" /
                    "research_objective_contract.v3.json")


def load_contract(path: Path = DEFAULT_CONTRACT) -> tuple[dict, str]:
    raw = path.read_bytes()
    contract = json.loads(raw)
    # Version 3 is frozen. Reject an incomplete or silently edited copy; a
    # deliberate amendment needs a new schema implementation and provenance.
    frozen = json.loads(DEFAULT_CONTRACT.read_bytes())
    if contract != frozen:
        raise ValueError("objective differs from frozen contract v3")
    if (contract["schema_version"] != 3 or
            contract["contract_status"] != "frozen" or
            contract["difference_form"] != "paired_relative" or
            contract["confidence_level"] != 0.95):
        raise ValueError("unsupported research objective contract")
    return contract, hashlib.sha256(raw).hexdigest()


def metric_specs(contract: dict, space_margin: float,
                 safety_only: bool = False) -> list[tuple[str, float, bool]]:
    constraints = contract["constraints"]
    if space_margin not in constraints["space"]["relative_margin_axis"]:
        raise ValueError("space margin must be a frozen W-R-S sweep point")
    specs = []
    if not safety_only:
        specs.append((contract["primary_objective"]["metric"], 0.0, True))
    for name in ("write", "space", "scan", "stalls"):
        block = constraints[name]
        specs.append((block["metric"], space_margin if name == "space"
                      else block["relative_margin"], False))
    latency = constraints["latency"]
    specs.extend((metric, latency["relative_margin"], False)
                 for metric in latency["acceptance_metrics"])
    return specs


def relative_difference(candidate: float, baseline: float) -> float:
    if not all(math.isfinite(v) and v >= 0 for v in (candidate, baseline)):
        raise ValueError("objective measurements must be finite and nonnegative")
    if baseline == 0:
        if candidate == 0:
            return 0.0
        raise ValueError("relative difference is undefined against zero baseline")
    return candidate / baseline - 1.0
