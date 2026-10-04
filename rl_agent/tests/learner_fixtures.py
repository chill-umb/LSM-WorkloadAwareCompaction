"""Shared fixtures for the learner's tests: a cost-model-2 plugin config,
learner settings, and transition and job lines built from the plugin's
golden log (controller/tests/fixtures/log_golden.jsonl)."""

import copy
import json
from pathlib import Path

from learner.logs import KINDS, STEP_TYPES, read_all

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_LOG = ROOT / "controller" / "tests" / "fixtures" / "log_golden.jsonl"


def plugin_config() -> dict:
    cfg = {"mode": "learned", "cost_model": 2, "c_w": 2.0, "c_f": 100.0,
           "c_blk": 1000.0, "c_sk": 2000.0, "c_open": 5000.0, "c_s": 0.001,
           "q_bar": 1000.0, "beta_w": 1.0, "beta_r": 1.0, "beta_s": 1.0,
           "c_cr": 0.5, "c_st": 10.0, "c_ib": 0.0, "c_mt": 20.0,
           "c_get0": 30.0, "c_sc0": 40.0, "c_put": 50.0, "p_dev": 1e9,
           "lambda": 0.25, "n_win": 1000, "push_interval_ms": 1}
    for kind, price in zip(KINDS, (1000.0, 2000.0, 3000.0, 400.0)):
        cfg[f"c_job_{kind}"] = price
    for x in STEP_TYPES:
        cfg[f"kappa_b_{x}"] = 1e-9
        for kind in KINDS:
            cfg[f"kappa_j_{x}_{kind}"] = 0.0
    cfg["kappa_j_probe_deep"] = 0.1
    return cfg


def settings(**changes) -> dict:
    learner = {"n_h": 2.0, "hidden": 16, "layers": 2, "clip": 10.0,
               "lr": 1e-3, "batch": 4, "target_every": 5, "huber": 1.0,
               "grad_clip": 10.0, "replay": 1000, "min_replay": 4,
               "echo": "own", "seed": 1}
    learner.update(changes)
    return {"learner": learner, "train_steps_per_transition": 2,
            "torch_threads": 1}


def golden() -> list:
    return read_all(GOLDEN_LOG)


def interior_transition(**changes) -> dict:
    """The golden log's valid interior transition (level 2, compact)."""
    t = next(copy.deepcopy(l) for l in golden()
             if l["type"] == "transition" and l["valid"] == 1)
    t.update(changes)
    return t


def job_line(**changes) -> dict:
    j = next(copy.deepcopy(l) for l in golden()
             if l["type"] == "job" and l["kind"] == "deep")
    j.update(changes)
    return j


def flush_line(**changes) -> dict:
    j = next(copy.deepcopy(l) for l in golden()
             if l["type"] == "job" and l["kind"] == "flush")
    j.update(changes)
    return j


def dumps(records) -> str:
    return "".join(json.dumps(r) + "\n" for r in records)
