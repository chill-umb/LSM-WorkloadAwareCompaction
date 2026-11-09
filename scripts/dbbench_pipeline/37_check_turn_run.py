#!/usr/bin/env python3
"""A turn arm's learner criteria (controller/turn.h; the preflight's step 6),
from its result directory:

  pushes  weight versions the turn trainer wrote (turn_trainer_summary.json)
          and those the plugin loaded (its "turn_weights" lines)
  ARCH-1  learning health: every score the plugin logged and every training
          loss the trainer logged is finite
  ARCH-6  C++ inference against Python: each turn line chosen with weights
          version v is recomputed from the archived weights/turn.v<v>.bin and
          the line's own inputs (rl_agent/learner/turn_weights.py forward,
          which follows turn.cc); every score must agree to 1e-9 (relative)
          and every level's greedy allowed action must be the logged one
          (with the L0 prior on, turn_prior, of L0's scores less the logged
          prior over the weights' spread, which must be the logged
          prior_spread)
  c_set   the SetOptions call's price, measured (the turn reward's change
          price): the accepted apply lines' wall time (apply_us) at the
          price per core-second, plus, with --options-bytes, the OPTIONS
          file RocksDB writes at c_w per byte; reported, not judged

--strict (the preflight) exits 1 when pushes, ARCH-1 or ARCH-6 fails.

  37_check_turn_run.py --result <arm dir> --prices <schema-6 prices> \\
      [--options-bytes N] [--min-pushes 3] [--strict] [--output report.json]
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "rl_agent"))

from learner import turn_weights as TW  # noqa: E402

TOLERANCE = 1e-9


def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def greedy(scores: list, masks: list) -> list:
    """turn.cc Greedy: the best allowed action, a tie to the lowest index."""
    out = []
    for level, mask in enumerate(masks):
        best = 0
        if level < len(scores):
            for a in range(1, TW.N_ACTIONS):
                if mask[a] and scores[level][a] > scores[level][best]:
                    best = a
        out.append(TW.ACTIONS[best])
    return out


def arch6(decisions: list, archive: Path, acting: int) -> dict:
    turns = [r for r in decisions if r["type"] == "turn" and r.get("final") != 1
             and int(r.get("weights", 0)) > 0]
    cache, missing, score_bad, greedy_bad, judged = {}, set(), [], [], 0
    spread_bad = []
    names = TW.feature_names(acting)
    for r in turns:
        v = int(r["weights"])
        if v not in cache:
            path = archive / f"turn.v{v}.bin"
            cache[v] = TW.decode(path.read_bytes()) if path.exists() else None
        w = cache[v]
        if w is None:
            missing.add(v)
            continue
        judged += 1
        ours = TW.forward(w, [float(r["x"][n]) for n in names])
        theirs = r["scores"]
        if any(not (isinstance(b, (int, float)) and
                    math.isclose(a, b, rel_tol=TOLERANCE, abs_tol=TOLERANCE))
               for la, lb in zip(ours, theirs) for a, b in zip(la, lb)):
            score_bad.append(r["t"])
        acted = [list(level) for level in ours]
        prior = r.get("prior")
        if isinstance(prior, list) and acted:
            if not math.isclose(float(r.get("prior_spread", math.nan)), w.spread,
                                rel_tol=TOLERANCE, abs_tol=TOLERANCE):
                spread_bad.append(r["t"])
            acted[0] = [a - float(b) / w.spread for a, b in zip(acted[0], prior)]
        if greedy(acted, r["masks"]) != r.get("greedy"):
            greedy_bad.append(r["t"])
    problems = []
    if missing:
        problems.append(f"versions without an archived file: {sorted(missing)[:10]}")
    if score_bad:
        problems.append(f"turns whose scores differ from Python's: {score_bad[:10]}")
    if greedy_bad:
        problems.append(f"turns whose greedy choice differs: {greedy_bad[:10]}")
    if spread_bad:
        problems.append(f"turns whose prior_spread is not the weights': {spread_bad[:10]}")
    return {"passed": not problems, "problems": problems, "judged": judged,
            "turns_with_weights": len(turns)}


def c_set(decisions: list, prices: dict, options_bytes: float | None) -> dict:
    calls = [r for r in decisions if r["type"] == "apply" and r.get("ok") == 1
             and isinstance(r.get("apply_us"), (int, float))]
    out = {"calls": len(calls)}
    if not calls:
        return out
    us = sorted(float(r["apply_us"]) for r in calls)
    median_s = statistics.median(us) / 1e6
    p_dev = float(prices["price_per_core_second"])
    out.update(median_us=statistics.median(us), mean_us=statistics.fmean(us),
               p90_us=us[min(len(us) - 1, int(0.9 * len(us)))],
               time_price=median_s * p_dev)
    total = out["time_price"]
    if options_bytes is not None:
        out["options_bytes"] = options_bytes
        out["write_price"] = options_bytes * float(prices["c_w"])
        total += out["write_price"]
    out["c_set"] = total
    return out


def check(result: Path, prices: dict, options_bytes: float | None,
          min_pushes: int) -> dict:
    decisions = read_jsonl(result / "decisions.jsonl")
    trainer = read_jsonl(result / "turn_trainer.jsonl")
    config = json.loads((result / "plugin_config.json").read_text())
    acting = int(config["turn_acting"])
    summary_path = result / "turn_trainer_summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    checks = {}
    loaded = [r for r in decisions if r["type"] == "turn_weights"]
    written = int(summary.get("weights_version", 0))
    problems = []
    if not summary:
        problems.append("no turn_trainer_summary.json")
    if written < min_pushes:
        problems.append(f"the trainer wrote {written} versions, fewer than {min_pushes}")
    if len(loaded) < min_pushes:
        problems.append(f"the plugin loaded {len(loaded)} versions, fewer than {min_pushes}")
    if any(r.get("arch") != config["turn_arch"] for r in loaded):
        problems.append("a loaded version of another arch")
    checks["pushes"] = {"passed": not problems, "problems": problems,
                        "written": written, "loaded": len(loaded),
                        "transitions": summary.get("transitions")}
    bad_scores = [r["t"] for r in decisions if r["type"] == "turn" and r.get("final") != 1
                  and not all(isinstance(v, (int, float)) and math.isfinite(v)
                              for level in r.get("scores", []) for v in level)]
    bad_loss = [r.get("t") for r in trainer if r["type"] == "train"
                and not math.isfinite(float(r.get("loss", math.nan)))]
    problems = ([f"turns with a score not finite: {bad_scores[:10]}"] if bad_scores else []) + \
               ([f"training losses not finite at turns {bad_loss[:10]}"] if bad_loss else [])
    checks["arch1"] = {"passed": not problems, "problems": problems,
                       "train_lines": sum(1 for r in trainer if r["type"] == "train")}
    checks["arch6"] = arch6(decisions, result / "weights", acting)
    checks["c_set"] = c_set(decisions, prices, options_bytes)
    failed = sorted(k for k in ("pushes", "arch1", "arch6") if not checks[k]["passed"])
    return {"arch": config["turn_arch"], "verdict": "failed" if failed else "passed",
            "failed_checks": failed, "checks": checks}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--result", type=Path, required=True)
    ap.add_argument("--prices", type=Path, required=True)
    ap.add_argument("--options-bytes", type=float)
    ap.add_argument("--min-pushes", type=int, default=3)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    report = check(args.result, json.loads(args.prices.read_text()),
                   args.options_bytes, args.min_pushes)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    c = report["checks"]
    cset = c["c_set"].get("c_set")
    print(f"[turn run] {report['verdict']} ({report['arch']}): failed "
          f"{report['failed_checks']}; versions written {c['pushes']['written']}, "
          f"loaded {c['pushes']['loaded']}; ARCH-6 {c['arch6']['judged']} turns judged; "
          f"c_set {cset if cset is None else f'{cset:.3e}'} from "
          f"{c['c_set']['calls']} calls")
    return 1 if args.strict and report["verdict"] != "passed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
