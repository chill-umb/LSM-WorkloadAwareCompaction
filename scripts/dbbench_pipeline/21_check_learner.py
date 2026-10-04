#!/usr/bin/env python3
"""A learned arm's learner criteria (PATHWAYS H §9; plan §6.5), from its
result directory:

  ARCH-1  learning health: no NaN in any push; each model's last TD error
          and largest |Q| (trainer.jsonl)
  ARCH-2  zero executed actions outside their decision's mask, zero masked
          target argmaxes (the trainer's audit)
  ARCH-6  C++ inference against Python: each decision made with weights
          version v is recomputed from the archived weights.v<v>.bin and its
          transition's state and prior (Q = -b + f); the allowed argmax must
          agree on at least 99.9% of decisions
  ARCH-8  charge timing: per level, the share of its jobs whose operations
          contain one of the level's decision points, the largest number d,
          and the jobs that served no operation
  pushes  weight versions the trainer wrote, and those the plugin loaded

ARCH-7 (the trainer's priced totals against 04's) needs 04's cost model 2
row for the same run: --evaluator gives it ({"C_W": ..., "C_R": ...}); on
the interim binary the difference is reported, not judged (the plugin and
the host log read the counters in separate callbacks; the interim interface
§2.6).

On D-24 §2's exploratory track every criterion is reported only; --strict
(the preflight's step 6) exits 1 when ARCH-1, 2 or 6 fails or fewer than
--min-pushes versions reached the plugin.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "rl_agent"))

from learner import weights as W  # noqa: E402
from learner.logs import ACTIONS, read_all  # noqa: E402

ARCH6_SHARE = 0.999


def allowed_argmax(q, mask) -> int:
    best = 0
    for a in range(1, len(q)):
        if mask[a] and q[a] > q[best]:
            best = a
    return best


def arch6(decisions: list, transitions: dict, archive: Path) -> dict:
    names: dict[str, list] = {}
    for t in transitions.values():
        for agent_key, state_key in (("agent", "state"),
                                     ("next_agent", "next_state")):
            if t.get(state_key):
                names.setdefault(t[agent_key], list(t[state_key]))
    cache: dict[int, W.WeightsFile] = {}
    checked = agree = 0
    missing = []
    max_dq = 0.0
    for d in decisions:
        v = d.get("weights") or 0
        if v <= 0 or d.get("q") is None:
            continue
        t = transitions.get(d["id"])
        if t is None or t.get("state") is None:
            missing.append(d["id"])
            continue
        if v not in cache:
            path = archive / f"weights.v{v}.bin"
            try:
                cache[v] = W.decode(path.read_bytes(), names)
            except (OSError, ValueError, KeyError) as error:
                return {"passed": False, "problems": [f"version {v}: {error}"]}
        model = cache[v].find(d["agent"], d["level"])
        if model is None:
            missing.append(d["id"])
            continue
        f = W.forward(model, [math.nan if x is None else x
                              for x in t["state"].values()])
        q_py = [-b + fa for b, fa in zip(t["prior_cost"], f)]
        checked += 1
        agree += allowed_argmax(q_py, d["mask"]) == allowed_argmax(d["q"], d["mask"])
        max_dq = max(max_dq, max(abs(a - b) for a, b in zip(q_py, d["q"])))
    share = agree / checked if checked else None
    problems = []
    if share is not None and share < ARCH6_SHARE:
        problems.append(f"agreement {share:.5f} < {ARCH6_SHARE}")
    return {"passed": not problems, "problems": problems, "decisions": checked,
            "agree": agree, "share": share, "max_abs_dq": max_dq,
            "unjoined": len(missing)}


def arch8(jobs: list) -> dict:
    by_level = defaultdict(lambda: {"jobs": 0, "with_point": 0, "max_d": 0,
                                    "no_ops": 0, "no_ops_bytes": 0.0})
    for j in jobs:
        if j.get("n_begin") is None:
            continue
        row = by_level[j["level"]]
        row["jobs"] += 1
        d = j.get("decision_points", 0)
        row["with_point"] += d >= 1
        row["max_d"] = max(row["max_d"], d)
        if j["n_end"] == j["n_begin"] and j["kind"] != "flush":
            row["no_ops"] += 1
            row["no_ops_bytes"] += j["s"] + j["o"]
    out = {str(k): {**v, "share_with_point": v["with_point"] / v["jobs"]}
           for k, v in sorted(by_level.items()) if v["jobs"]}
    max_d = max((v["max_d"] for v in by_level.values()), default=0)
    return {"passed": max_d <= 1, "problems": [] if max_d <= 1 else
            [f"a job contains {max_d} decision points (H.6(ii)'s factor is "
             f"gamma^{max_d})"], "levels": out, "max_d": max_d}


def check(result: Path, evaluator: dict | None, min_pushes: int) -> dict:
    decisions_all = read_all(result / "decisions.jsonl")
    records = read_all(result / "transitions.jsonl")
    decisions = [d for d in decisions_all if d["type"] == "decision"]
    transitions = {t["id"]: t for t in records
                   if t["type"] == "transition" and t["id"]}
    jobs = [r for r in records if r["type"] == "job"]
    summary = json.loads((result / "trainer_summary.json").read_text())
    pushes = [json.loads(line) for line in
              (result / "trainer.jsonl").read_text().splitlines() if line]
    out: dict = {}

    nan = any(m.get("nan") for p in pushes for m in p.get("models", {}).values())
    last = {}
    for p in pushes:
        for key, m in p.get("models", {}).items():
            last[key] = {"steps": m["steps"], "td_abs": m["td_abs"],
                         "q_abs_max": m["q_abs_max"]}
    out["arch1"] = {"passed": not nan, "problems": ["NaN in training"] if nan
                    else [], "models": last}

    masked = [d["id"] for d in decisions
              if d["mask"][ACTIONS.index(d["action"])] != 1]
    breaches = summary.get("audit_breaches", 0)
    out["arch2"] = {"passed": not masked and breaches == 0,
                    "problems": ([f"masked actions taken: {masked[:10]}"]
                                 if masked else []) +
                                ([f"{breaches} masked target argmaxes"]
                                 if breaches else []),
                    "masked_actions": len(masked), "audit_breaches": breaches}
    out["arch6"] = arch6(decisions, transitions, result / "weights")
    out["arch8"] = arch8(jobs)

    loaded = [d["version"] for d in decisions_all if d["type"] == "weights"]
    out["pushes"] = {"passed": len(loaded) >= min_pushes,
                     "problems": [] if len(loaded) >= min_pushes else
                     [f"{len(loaded)} versions reached the plugin, fewer than "
                      f"{min_pushes}"],
                     "written": summary.get("pushes"), "loaded": len(loaded),
                     "last_loaded": loaded[-1] if loaded else None}

    totals = summary.get("totals", {})
    arch7 = {"trainer_C_W": totals.get("C_W"), "trainer_C_R": totals.get("C_R"),
             "judged": False}
    if evaluator:
        for key in ("C_W", "C_R"):
            ours, theirs = totals.get(key), evaluator.get(key)
            if ours is not None and theirs:
                arch7[f"relative_difference_{key}"] = (ours - theirs) / theirs
    else:
        arch7["note"] = "04's cost model 2 row not given (--evaluator)"
    out["arch7"] = arch7

    decisive = ("arch1", "arch2", "arch6", "pushes")
    failed = [k for k in decisive if not out[k]["passed"]]
    return {"verdict": "failed" if failed else "passed", "failed": failed,
            "report_only": ["arch7", "arch8"], **out}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evaluator", type=Path,
                        help="04's cost model 2 totals for this run (JSON)")
    parser.add_argument("--min-pushes", type=int, default=1)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    try:
        evaluator = (json.loads(args.evaluator.read_text())
                     if args.evaluator else None)
        report = check(args.result, evaluator, args.min_pushes)
    except (OSError, ValueError, KeyError) as error:
        print(f"[learner check] cannot check {args.result}: {error}",
              file=sys.stderr)
        return 1
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    a6 = report["arch6"]
    print(f"[learner check] {report['verdict']}: ARCH-2 masked "
          f"{report['arch2']['masked_actions']}, ARCH-6 "
          f"{a6.get('agree')}/{a6.get('decisions')} (max |dq| "
          f"{a6.get('max_abs_dq')}), pushes loaded "
          f"{report['pushes']['loaded']}, ARCH-8 max d "
          f"{report['arch8']['max_d']}")
    return 1 if args.strict and report["verdict"] != "passed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
