#!/usr/bin/env python3
"""The controller plugin's config for one arm (plan §3 and §5): the flat JSON
object controller/config.h reads, composed from the files that fix each
value, so no value is chosen when an arm starts.

  action bounds (D-18)   ACTION_BOUNDS_FILE: m_min m_max k0_min k0_cap
                         epsilon phi_min alpha kappa_d kappa_a
                         setoptions_min_interval_ms
  Gate N3 settings       CONTROLLER_RULES_FILE: b_max (the prior's clip,
                         H §7), and for a rules arm "rules" and the
                         threshold of each rule switched on
  the contract (D-13)    beta_w beta_r beta_s of the objective mode at the
                         headline beta*; c_s; q_bar of the workload family
  prices (18, D-15)      c_w c_f c_blk c_sk
  D-16                   config/admission_test.json: k

Every value missing, or null, in its file is listed and the arm is refused:
the D-18 bounds and the Gate N3 settings are the owner's to preregister, and
q-bar and the prices are measured. For the preflight's smoke runs only,
--placeholders fills each missing value from PLACEHOLDERS and prints which;
those configs exercise the code paths and price nothing.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import research_objective

PLUGIN_MODES = {"hold": "hold-only", "rules": "rules"}
BOUND_KEYS = ("m_min", "m_max", "k0_min", "k0_cap", "epsilon", "phi_min",
              "alpha", "kappa_d", "kappa_a", "setoptions_min_interval_ms")
# controller/config.cc's rule names and the threshold each one needs.
RULE_THRESHOLDS = {
    "k0_tracking": None,
    "l0_early": "rule_l0_early_read_ratio",
    "yield_slot": None,
    "garbage_hold": "rule_garbage_drop",
    "neighbour_release": "rule_release_fill",
}
# Smoke values only, never measured arms. Bounds are D-18's option A, k0_cap
# its rule at the default base (16 MiB) and write buffer (2 MiB), q-bar and
# the device prices are round numbers of the right order, and every rule is
# on with thresholds that let each fire. The SetOptions cap must stay below
# the fastest level's control interval or ACT-3 fails by design. On the
# 2026-10-02 rules smoke L0 decided every 30-64 ms at 2.5 decisions per flush;
# once per flush (G-iv as amended that day) its interval is about 2.5 times
# that, and 10 ms leaves a wide margin.
PLACEHOLDERS = {
    "m_min": 0.5, "m_max": 2.0, "k0_min": 2, "k0_cap": 8, "epsilon": 0.1,
    "phi_min": 0.55, "alpha": 1.5, "kappa_d": 0.25, "kappa_a": 1.0,
    "setoptions_min_interval_ms": 10, "b_max": 1.0, "q_bar": 50000.0,
    "c_w": 1e-14, "c_f": 1e-12, "c_blk": 1e-11, "c_sk": 1e-11,
    "rules": ",".join(RULE_THRESHOLDS), "rule_l0_early_read_ratio": 1.0,
    "rule_garbage_drop": 0.1, "rule_release_fill": 0.5,
}


def _value(source: dict, key: str, where: str, missing: list[str]):
    value = source.get(key)
    if value is None:
        missing.append(f"{key} ({where})")
    return value


def compose(arm: str, objective_mode: str, family: str, *, bounds: dict,
            settings: dict, contract: dict, prices: dict | None,
            admission: dict, decision_log: str, transition_log: str,
            placeholders: bool = False) -> tuple[dict, list[str]]:
    """The config, and the keys filled from PLACEHOLDERS (only when asked;
    otherwise a missing value raises ValueError naming every one)."""
    if arm not in PLUGIN_MODES:
        raise ValueError(f"no plugin mode for arm {arm!r}")
    missing: list[str] = []
    config: dict = {"mode": PLUGIN_MODES[arm],
                    "decision_log": decision_log,
                    "transition_log": transition_log}
    for key in BOUND_KEYS:
        config[key] = _value(bounds, key, "D-18 action bounds", missing)
    config["b_max"] = _value(settings, "b_max", "Gate N3 settings", missing)
    if arm == "rules":
        rules = _value(settings, "rules", "Gate N3 settings", missing)
        if rules is not None:
            config["rules"] = rules
            for rule in str(rules).split(","):
                if rule not in RULE_THRESHOLDS:
                    raise ValueError(f"unknown rule {rule!r} in \"rules\"")
                threshold = RULE_THRESHOLDS[rule]
                if threshold:
                    config[threshold] = _value(settings, threshold,
                                               "Gate N3 settings", missing)

    weights = research_objective.mode_weights(
        contract, objective_mode, contract["objective"]["headline_beta_star"])
    config.update(beta_w=weights[0], beta_r=weights[1], beta_s=weights[2])
    config["q_bar"] = research_objective.reference_rate(contract, family)
    if config["q_bar"] is None:
        missing.append(f"q_bar (contract reference_rate.{family}, D-14 §2)")
    if prices is None:
        for key in research_objective.DEVICE_PRICES:
            config[key] = None
            missing.append(f"{key} (prices file, stage 18)")
        config["c_s"] = contract["prices"]["storage_price_per_byte_second"]
    else:
        # As 04 (D-15 §3a): priced per core-second under this contract.
        if (prices.get("schema") != 2 or prices.get("price_per_core_second")
                != contract["prices"]["price_per_core_second"]):
            raise ValueError("the prices file is not priced per core-second "
                             "under this contract (schema 2, D-15 §3a)")
        config.update(research_objective.validate_prices(prices, contract))
    config["k"] = admission["k"]

    filled: list[str] = []
    if missing and placeholders:
        if arm == "rules" and "rules" not in config:
            # Every rule on, so every threshold is needed.
            config["rules"] = None
            for threshold in filter(None, RULE_THRESHOLDS.values()):
                config.setdefault(threshold, None)
        for key, value in list(config.items()):
            if value is None:
                config[key] = PLACEHOLDERS[key]
                filled.append(key)
        missing = []
    if missing:
        raise ValueError(f"the {arm} arm's plugin config lacks: " +
                         "; ".join(missing))
    for key, value in config.items():
        if key.endswith("_log") or key in ("mode", "rules"):
            continue
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value)):
            raise ValueError(f"{key} must be a finite number; got {value!r}")
    return config, sorted(filled)


def _load(path: Path | None, what: str) -> dict:
    """A JSON object, or {} when the file does not exist (every value it
    holds is then reported missing)."""
    if path is None or not path.exists():
        return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{what} {path} is not a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arm", required=True, choices=sorted(PLUGIN_MODES))
    parser.add_argument("--objective-mode", required=True,
                        choices=research_objective.MODES)
    parser.add_argument("--family", required=True)
    parser.add_argument("--bounds", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--prices", type=Path, required=True)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--decision-log", required=True)
    parser.add_argument("--transition-log", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--placeholders", action="store_true",
                        help="preflight smoke runs only")
    args = parser.parse_args()
    try:
        contract, _ = research_objective.load_contract()
        prices = (json.loads(args.prices.read_text())
                  if args.prices.exists() else None)
        config, filled = compose(
            args.arm, args.objective_mode, args.family,
            bounds=_load(args.bounds, "action bounds"),
            settings=_load(args.settings, "Gate N3 settings"),
            contract=contract, prices=prices,
            admission=json.loads(args.admission.read_text()),
            decision_log=args.decision_log,
            transition_log=args.transition_log,
            placeholders=args.placeholders)
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        print(f"[plugin_config] refused: {error}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    if filled:
        print(f"[plugin_config] SMOKE ONLY, placeholders for: "
              f"{', '.join(filled)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
