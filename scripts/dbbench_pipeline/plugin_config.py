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
                         headline beta* (or, in a diagnostic run only, at
                         --beta-star, one of the beta* the contract
                         reports); c_s; q_bar of the workload family
  prices (18, D-15)      c_w c_f c_blk c_sk c_open (D-21: the controller
                         prices each level's reopens at stage 18's c_open)
  D-16                   config/admission_test.json: k
  cost model 2 (D-23)    the learner arms only (prior, learned): from a
                         schema-6 prices file (stage 30; cost_model_v2's
                         load_prices), c_cr c_job_<kind> c_st c_ib c_mt
                         c_get0 c_sc0 c_put p_dev lambda n_win and every
                         kappa_b_<type> and kappa_j_<type>_<kind>; hold and
                         rules arms run under cost model 1
  learner settings       LEARNER_SETTINGS_FILE (D-24 §2, exploratory):
                         explore, push_interval_ms, weights_required; the
                         seed and the weights path come from the arm

Every value missing, or null, in its file is listed and the arm is refused:
the D-18 bounds and the Gate N3 settings are the owner's to preregister, and
q-bar and the prices are measured. For the preflight's smoke runs only,
--placeholders fills each missing value from PLACEHOLDERS and prints which;
those configs exercise the code paths and price nothing.

Prices must be final (PREREGISTRATION D-22 f) unless the run is marked
diagnostic (--diagnostic, or --placeholders): provisional prices (D-22 j)
are then accepted and "provisional prices" is printed on stderr, since the
config itself takes no key the plugin does not read.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import research_objective

PLUGIN_MODES = {"hold": "hold-only", "rules": "rules", "prior": "prior-only",
                "learned": "learned"}
LEARNER_ARMS = ("prior", "learned")
LEARNER_KEYS = ("explore", "push_interval_ms", "weights_required")
# The device prices controller/config.cc reads, c_open among them since
# PREREGISTRATION D-21 (resolving D-20 §2g): measured by stage 18, never a
# default; only the preflight's smoke runs fill a missing one.
PLUGIN_PRICES = ("c_w", "c_f", "c_blk", "c_sk", "c_open")
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
    "c_w": 1e-14, "c_f": 1e-12, "c_blk": 1e-11, "c_sk": 1e-11, "c_open": 1e-10,
    "rules": ",".join(RULE_THRESHOLDS), "rule_l0_early_read_ratio": 1.0,
    "rule_garbage_drop": 0.1, "rule_release_fill": 0.5,
    "explore": 0.1, "push_interval_ms": 1000, "weights_required": 0,
    "seed": 1,
}


def cost_model_2_keys(record: dict) -> dict:
    """Cost model 2's plugin keys from a schema-6 prices record, checked by
    the evaluator's loader (ValueError otherwise), so the plugin, the
    trainer and 04 price with one set of values."""
    import cost_model_v2
    p = cost_model_v2.load_prices(record)
    keys = {"c_cr": p.c_cr, "c_st": p.base["step"], "c_ib": p.base["iblock"],
            "c_mt": p.base["memtable"], "c_get0": p.base["get0"],
            "c_sc0": p.base["scan0"], "c_put": p.base["put"],
            "p_dev": p.p_dev, "lambda": p.lam, "n_win": p.n_win}
    for kind in cost_model_v2.KINDS:
        keys[f"c_job_{kind}"] = p.job[kind]
    for x in cost_model_v2.STEP_TYPES:
        keys[f"kappa_b_{x}"] = p.kappa_b[x]
        for kind in cost_model_v2.KINDS:
            keys[f"kappa_j_{x}_{kind}"] = p.kappa_j[x][kind]
    return keys


def _value(source: dict, key: str, where: str, missing: list[str]):
    value = source.get(key)
    if value is None:
        missing.append(f"{key} ({where})")
    return value


def compose(arm: str, objective_mode: str, family: str, *, bounds: dict,
            settings: dict, contract: dict, prices: dict | None,
            admission: dict, decision_log: str, transition_log: str,
            placeholders: bool = False, diagnostic: bool = False,
            learner: dict | None = None, weights_path: str | None = None,
            seed: int | None = None,
            beta_star: float | None = None,
            apply_at_op: int | None = None, apply_m: str | None = None,
            apply_k0: int | None = None, apply_schedule: str | None = None,
            hold_k0_cap: int | None = None) -> tuple[dict, list[str]]:
    """The config, and the keys filled from PLACEHOLDERS (only when asked;
    otherwise a missing value raises ValueError naming every one).
    Provisional prices only for a diagnostic run (D-22 j). A learner arm
    needs a schema-6 prices file (cost model 2), the learner settings, and,
    for learned, the weights path; its seed is the arm's. beta_star other
    than the headline only for a diagnostic run, and only one the contract
    reports (so that 04 scores the run at the beta* it ran under). A
    scheduled change (apply_at_op with apply_m, optionally apply_k0: the
    step-response arm, owner 2026-10-07), or a schedule of several
    (apply_schedule, owner 2026-10-21), is for the hold arm in a diagnostic
    run only, and so is hold_k0_cap: the K bound the hold arm's scheduled
    triggers are checked against, in place of D-18's (hold-only never acts,
    so the bound reaches nothing else)."""
    if arm not in PLUGIN_MODES:
        raise ValueError(f"no plugin mode for arm {arm!r}")
    schedule = scheduled_change(arm, diagnostic, apply_at_op, apply_m,
                                apply_k0, apply_schedule)
    missing: list[str] = []
    config: dict = {"mode": PLUGIN_MODES[arm],
                    "decision_log": decision_log,
                    "transition_log": transition_log,
                    "cost_model": 2 if arm in LEARNER_ARMS else 1}
    for key in BOUND_KEYS:
        config[key] = _value(bounds, key, "D-18 action bounds", missing)
    if hold_k0_cap is not None:
        if arm != "hold" or not diagnostic:
            raise ValueError("hold_k0_cap is for the hold arm in a diagnostic "
                             "run only")
        if (isinstance(hold_k0_cap, bool) or not isinstance(hold_k0_cap, int)
                or hold_k0_cap < 2):
            raise ValueError(f"hold_k0_cap must be a whole number >= 2; got "
                             f"{hold_k0_cap!r}")
        config["k0_cap"] = hold_k0_cap
        missing = [m for m in missing if not m.startswith("k0_cap ")]
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

    headline = contract["objective"]["headline_beta_star"]
    if beta_star is not None and float(beta_star) != float(headline):
        reported = contract["objective"]["reported_beta_star"]
        if float(beta_star) not in [float(b) for b in reported]:
            raise ValueError(f"beta* {beta_star:g} is not one the contract "
                             f"reports ({reported})")
        if not diagnostic:
            raise ValueError(f"beta* {beta_star:g} is not the headline "
                             f"({headline}): a diagnostic run only")
    weights = research_objective.mode_weights(
        contract, objective_mode, headline if beta_star is None else beta_star)
    config.update(beta_w=weights[0], beta_r=weights[1], beta_s=weights[2])
    config["q_bar"] = research_objective.reference_rate(contract, family)
    if config["q_bar"] is None:
        missing.append(f"q_bar (contract reference_rate.{family}, D-14 §2)")
    if prices is None:
        for key in PLUGIN_PRICES:
            config[key] = None
            missing.append(f"{key} (prices file, stage 18)")
        config["c_s"] = contract["prices"]["storage_price_per_byte_second"]
    else:
        # As 04 (D-15 §3a, D-20, D-22): priced per core-second, reopens
        # apart, final unless the run is diagnostic.
        checked = research_objective.checked_prices(
            prices, contract, provisional_ok=diagnostic or placeholders)
        config.update({key: checked[key] for key in (*PLUGIN_PRICES, "c_s")})
    config["k"] = admission["k"]
    if arm in LEARNER_ARMS:
        if prices is None:
            raise ValueError(f"the {arm} arm needs a schema-6 prices file "
                             "(cost model 2, stage 30)")
        if prices.get("schema") != 6:
            raise ValueError(f"the {arm} arm needs cost model 2's prices: "
                             f"schema 6, not {prices.get('schema')!r}")
        config.update(cost_model_2_keys(prices))
        learner = learner or {}
        config["explore"] = _value(learner, "explore", "learner settings",
                                   missing)
        if seed is None:
            missing.append("seed (the arm's)")
        config["seed"] = seed
        if arm == "learned":
            if not weights_path:
                missing.append("weights_path (the arm's)")
            config["weights_path"] = weights_path
            for key in ("push_interval_ms", "weights_required"):
                config[key] = _value(learner, key, "learner settings", missing)

    filled: list[str] = []
    if missing and placeholders:
        if arm == "rules" and "rules" not in config:
            # Every rule on, so every threshold is needed.
            config["rules"] = None
            for threshold in filter(None, RULE_THRESHOLDS.values()):
                config.setdefault(threshold, None)
        for key, value in list(config.items()):
            if value is None:
                if key not in PLACEHOLDERS:
                    raise ValueError(f"{key} has no smoke placeholder")
                config[key] = PLACEHOLDERS[key]
                filled.append(key)
        missing = []
    if missing:
        raise ValueError(f"the {arm} arm's plugin config lacks: " +
                         "; ".join(missing))
    config.update(schedule)
    for key, value in config.items():
        if key.endswith("_log") or key in ("mode", "rules", "weights_path",
                                           "apply_m", "apply_schedule"):
            continue
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value)):
            raise ValueError(f"{key} must be a finite number; got {value!r}")
    return config, sorted(filled)


def check_vector(apply_m: str, where: str = "apply_m") -> None:
    """A scheduled multiplier vector: ":"-separated, finite and positive,
    L0's 1 (the plugin checks the bounds and the length)."""
    try:
        values = [float(x) for x in apply_m.split(":")]
    except ValueError:
        raise ValueError(f"{where} must be ':'-separated numbers; got {apply_m!r}") from None
    if not all(math.isfinite(v) and v > 0 for v in values) or values[0] != 1:
        raise ValueError(f"{where} needs finite positive values and L0's 1; got {apply_m!r}")


def check_schedule(schedule: str) -> None:
    """apply_schedule: ";"-separated "op|m|k0" entries, each op a positive
    whole number above the one before, m as apply_m, k0 0 (keep the
    trigger) or a positive whole number (controller/config.cc
    ParseSchedule; the plugin checks k0 against its bounds)."""
    last = 0
    for number, entry in enumerate(schedule.split(";"), 1):
        parts = entry.split("|")
        if len(parts) != 3:
            raise ValueError(f"apply_schedule entry {number}: {entry!r} is not op|m|k0")
        op, m, k0 = parts
        if not op.isdigit() or int(op) <= last:
            raise ValueError(f"apply_schedule entry {number}: op {op!r} must be a "
                             f"positive whole number above {last}")
        last = int(op)
        check_vector(m, f"apply_schedule entry {number}'s m")
        if not k0.isdigit():
            raise ValueError(f"apply_schedule entry {number}: k0 {k0!r} must be 0 "
                             "or a positive whole number")


def scheduled_change(arm: str, diagnostic: bool, apply_at_op: int | None,
                     apply_m: str | None, apply_k0: int | None,
                     apply_schedule: str | None = None) -> dict:
    """The hold arm's scheduled change as config keys ({} when none): at the
    first poll at or after operation apply_at_op the plugin applies apply_m
    (":"-separated, one value per level, L0's 1) and apply_k0, once, then
    holds (controller/config.h); or apply_schedule, several such changes in
    order. The plugin checks the bounds and the length against the host."""
    if (apply_at_op is None and apply_m is None and apply_k0 is None
            and apply_schedule is None):
        return {}
    if arm != "hold":
        raise ValueError(f"the {arm} arm takes no scheduled change: hold only")
    if not diagnostic:
        raise ValueError("a scheduled change is for a diagnostic run only")
    if apply_schedule is not None:
        if apply_at_op is not None or apply_m is not None or apply_k0 is not None:
            raise ValueError("apply_schedule replaces apply_at_op, apply_m and "
                             "apply_k0")
        check_schedule(apply_schedule)
        return {"apply_schedule": apply_schedule}
    if apply_at_op is None or apply_m is None:
        raise ValueError("a scheduled change needs apply_at_op and apply_m")
    if isinstance(apply_at_op, bool) or not isinstance(apply_at_op, int) or apply_at_op < 1:
        raise ValueError(f"apply_at_op must be a positive integer; got {apply_at_op!r}")
    check_vector(apply_m)
    out = {"apply_at_op": apply_at_op, "apply_m": apply_m}
    if apply_k0 is not None:
        if isinstance(apply_k0, bool) or not isinstance(apply_k0, int) or apply_k0 < 1:
            raise ValueError(f"apply_k0 must be a positive integer; got {apply_k0!r}")
        out["apply_k0"] = apply_k0
    return out


def learner_values(settings: dict, phase: str) -> dict:
    """The plugin's learner keys: "plugin", with "evaluation" over it for a
    frozen arm."""
    values = dict(settings.get("plugin") or {})
    if phase == "evaluation":
        values.update(settings.get("evaluation") or {})
    return values


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
    parser.add_argument("--diagnostic", action="store_true",
                        help="a run marked diagnostic: provisional prices "
                             "are accepted (D-22 j)")
    parser.add_argument("--learner-settings", type=Path,
                        help="the learner arms' settings (explore, "
                             "push_interval_ms, weights_required)")
    parser.add_argument("--weights-path", help="the learned arm's weights file")
    parser.add_argument("--seed", type=int, help="the learner arms' seed")
    parser.add_argument("--beta-star", type=float,
                        help="the priority modes' beta* (default the "
                             "contract's headline); another reported beta* "
                             "only with --diagnostic")
    parser.add_argument("--apply-at-op", type=int,
                        help="hold arm, diagnostic only: the operation at or "
                             "after which the scheduled change is applied")
    parser.add_argument("--apply-m",
                        help="the scheduled multipliers, ':'-separated, one "
                             "per level (L0's 1)")
    parser.add_argument("--apply-k0", type=int,
                        help="the scheduled L0 trigger (default: keep it)")
    parser.add_argument("--apply-schedule",
                        help="hold arm, diagnostic only, in place of the three "
                             "above: several changes, ';'-separated "
                             "'op|m|k0' entries (k0 0: keep the trigger)")
    parser.add_argument("--hold-k0-cap", type=int,
                        help="hold arm, diagnostic only: k0_cap in place of "
                             "D-18's (the scheduled triggers' bound)")
    parser.add_argument("--learner-phase", choices=("training", "evaluation"),
                        default="training",
                        help="evaluation: the settings file's \"evaluation\" "
                             "values over its \"plugin\" ones (a frozen arm)")
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
            placeholders=args.placeholders, diagnostic=args.diagnostic,
            learner=learner_values(_load(args.learner_settings,
                                         "learner settings"),
                                   args.learner_phase),
            weights_path=args.weights_path, seed=args.seed,
            beta_star=args.beta_star,
            apply_at_op=args.apply_at_op, apply_m=args.apply_m,
            apply_k0=args.apply_k0, apply_schedule=args.apply_schedule,
            hold_k0_cap=args.hold_k0_cap)
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        print(f"[plugin_config] refused: {error}", file=sys.stderr)
        return 1
    if prices is not None and not research_objective.prices_final(prices, contract):
        print(f"[plugin_config] provisional prices ({args.prices}; D-22 j): "
              "a diagnostic run only", file=sys.stderr)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    if filled:
        print(f"[plugin_config] SMOKE ONLY, placeholders for: "
              f"{', '.join(filled)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
