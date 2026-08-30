#!/usr/bin/env python3
"""Hard completion gate for learned and prior-only experiment arms."""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from pathlib import Path


RESIDUAL_EPSILON = 1e-8
MINIMUM_REPLAY = 32


def integer(summary: dict, name: str, default: int) -> int:
    value = summary.get(name, default)
    return value if type(value) is int else default


def number(summary: dict, name: str, default: float) -> float:
    value = summary.get(name, default)
    if type(value) not in (int, float):
        return default
    value = float(value)
    return value if math.isfinite(value) else default


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument(
        "--arm", required=True, choices=("prior_only", "rl", "unconstrained_rl")
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    errors = []
    summary = None
    try:
        summary = json.loads(args.summary.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"missing or invalid server summary: {exc}")

    checks = {}
    if summary is not None:
        checks["schema_version"] = summary.get("schema_version") == 1
        checks["clients_drained"] = summary.get("clients_drained") is True
        checks["training_quiesced"] = summary.get("training_quiesced") is True
        # These are part of the treatment, not optional diagnostics. A run
        # without the analytic prior or shared representation answers a
        # different research question even if its optimizer moved.
        checks["analytic_prior"] = summary.get("analytic_prior") is True
        checks["shared_trunk"] = summary.get("shared_trunk") is True
        checks["no_pending_credit"] = integer(
            summary, "pending_windows_at_shutdown", -1
        ) == 0
        checks["trainer_error"] = (
            "trainer_error" in summary and summary["trainer_error"] is None
        )
        if args.arm == "prior_only":
            checks.update({
                "eval_mode": summary.get("eval_mode") is True,
                "zero_train_steps": integer(summary, "train_steps", -1) == 0,
                "zero_residual": number(
                    summary, "max_abs_residual_advantage", -1.0
                ) == 0.0,
            })
        else:
            checks.update({
                "training_mode": summary.get("eval_mode") is False,
                "finalized_replay": integer(
                    summary, "finalized_transitions", -1
                ) >= MINIMUM_REPLAY,
                "replay_warm": integer(summary, "replay_size", -1)
                >= MINIMUM_REPLAY,
                "optimizer_stepped": integer(summary, "train_steps", 0) > 0,
                "nonzero_residual": number(
                    summary, "max_abs_residual_advantage", 0.0
                ) > RESIDUAL_EPSILON,
                "prior_residual_compared": integer(
                    summary, "argmax_comparison_count", 0
                ) > 0,
            })
        errors.extend(name for name, passed in checks.items() if not passed)

    report = {
        "schema_version": 1,
        "arm": args.arm,
        "summary_path": str(args.summary),
        "thresholds": {
            "minimum_finalized_transitions": MINIMUM_REPLAY,
            "minimum_replay_size": MINIMUM_REPLAY,
            "residual_epsilon": RESIDUAL_EPSILON,
        },
        "checks": checks,
        "errors": errors,
        "passed": not errors and bool(checks),
        "server_summary": summary,
    }
    atomic_json(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
