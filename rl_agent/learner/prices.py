"""Prices for the reward, from the plugin's own config (the flat JSON
scripts/dbbench_pipeline/plugin_config.py writes), so the plugin and the
trainer price with the same values; the per-job formulas are the evaluator's
(scripts/dbbench_pipeline/cost_model_v2.py), so the trainer and 04 use one
estimator (ARCH-7)."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[2] / "scripts" / "dbbench_pipeline"
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

import cost_model_v2  # noqa: E402

from .logs import KINDS, STEP_TYPES  # noqa: E402


@dataclass(frozen=True)
class Pricing:
    v2: "cost_model_v2.Prices"
    c_w: float
    c_s: float
    q_bar: float
    beta_w: float
    beta_r: float
    beta_s: float

    def base(self, step_type: str) -> float:
        return self.v2.base[step_type]


def from_plugin_config(cfg: dict) -> Pricing:
    """ValueError unless the config is cost model 2 with every price."""
    if cfg.get("cost_model") != 2:
        raise ValueError("the learner needs a cost_model 2 plugin config")
    base_keys = {"probe": "c_f", "block": "c_blk", "seek": "c_sk",
                 "reopen": "c_open", "step": "c_st", "iblock": "c_ib",
                 "memtable": "c_mt", "get0": "c_get0", "scan0": "c_sc0",
                 "put": "c_put"}
    record = {
        "schema": cost_model_v2.PRICES_SCHEMA, "cost_model": 2,
        **{key: cfg.get(key) for key in base_keys.values()},
        "job_prices": {k: cfg.get(f"c_job_{k}") for k in KINDS},
        "kappa": {
            "lambda": cfg.get("lambda"),
            "B": {x: cfg.get(f"kappa_b_{x}") for x in STEP_TYPES},
            "J": {x: {k: cfg.get(f"kappa_j_{x}_{k}") for k in KINDS}
                  for x in STEP_TYPES},
        },
        "n_win": cfg.get("n_win"),
        "c_w": cfg.get("c_w"), "c_cr": cfg.get("c_cr"),
        "price_per_core_second": cfg.get("p_dev"),
    }
    v2 = cost_model_v2.load_prices(record)
    for key in ("c_s", "q_bar", "beta_w", "beta_r", "beta_s"):
        value = cfg.get(key)
        if not isinstance(value, (int, float)) or not value > 0:
            raise ValueError(f"{key} must be positive; got {value!r}")
    return Pricing(v2=v2, c_w=v2.c_w, c_s=float(cfg["c_s"]),
                   q_bar=float(cfg["q_bar"]), beta_w=float(cfg["beta_w"]),
                   beta_r=float(cfg["beta_r"]), beta_s=float(cfg["beta_s"]))
