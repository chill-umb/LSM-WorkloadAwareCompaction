#!/usr/bin/env python3
"""Gate alpha-4: local smoothness of the alpha-conditioned Q-network.

Loads a saved per-level checkpoint from a completed run and queries it at a
small sweep of alpha values around a handful of real states drawn from the
end of that run's log (docs/RUNTIME_ALPHA_OBJECTIVE_PLAN.md, docs/
ALPHA_EXPERIMENT_RUNBOOK.md S7). This is deliberately a LOCAL check only: it
says nothing about alpha values far from the ones the run actually visited,
by design -- see the plan doc's S2a for why full-range generalization from a
single cold-started run is out of scope.

Pass: for each probed state, the implied action (compact vs. defer) and the
advantage (Q_compact - Q_defer) move smoothly across adjacent alpha values --
no sign flip or discontinuity between neighbours.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch

RL_AGENT_DIR = Path(__file__).resolve().parents[1] / "rl_agent"
sys.path.insert(0, str(RL_AGENT_DIR))

import config  # noqa: E402
from model import MultiHeadDQN  # noqa: E402


def load_states(metrics_path: Path, level: int, count: int) -> list[list[float]]:
    """Real encoded states for `level`, oldest to newest, last `count` kept.

    metrics.jsonl already logs the exact post-_encode() feature vector under
    "state_features" (named by config.ML_STATE_FIELDS), so no re-encoding of
    raw observables is needed here.
    """
    states = []
    with metrics_path.open() as handle:
        for line in handle:
            record = json.loads(line)
            if record.get("level") != level or record.get("done"):
                continue
            features = record["state_features"]
            states.append([features[name] for name in config.ML_STATE_FIELDS])
    if not states:
        raise SystemExit(
            f"no non-terminal records for level {level} in {metrics_path}")
    return states[-count:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", type=Path,
                        help="a completed arm's result directory, e.g. "
                             "results/alpha-live/10M/T2/rl-alpha1.0live")
    parser.add_argument("--level", type=int, default=0,
                        help="which level's checkpoint/states to probe")
    parser.add_argument("--states", type=int, default=5,
                        help="how many recent real states to probe")
    parser.add_argument("--alphas", type=float, nargs="+",
                        default=[0.0, 0.05, 0.1, 0.15, 0.2],
                        help="alpha values to query at, holding the rest of "
                             "each probed state fixed")
    args = parser.parse_args()

    alpha_index = config.ML_STATE_FIELDS.index("objective_alpha")
    ckpt_path = args.result_dir / f"model.l{args.level}.pt"
    if not ckpt_path.exists():
        raise SystemExit(f"no checkpoint at {ckpt_path}")
    ckpt = torch.load(ckpt_path, map_location="cpu")
    if not ckpt.get("shared_trunk"):
        raise SystemExit(
            f"{ckpt_path}: not a shared-trunk checkpoint; this script "
            "assumes RL_SHARED_TRUNK=1 (the project default)")

    net = MultiHeadDQN(
        state_dim=config.ML_STATE_DIM, hidden_dim=config.HIDDEN_DIM,
        action_dim=config.ACTION_DIM, num_levels=config.ML_MAX_LEVELS,
        dueling=config.DUELING,
    )
    net.load_state_dict(ckpt["policy_net"])
    net.eval()

    states = load_states(
        args.result_dir / "metrics.jsonl", args.level, args.states)
    levels = torch.LongTensor([args.level])

    print(f"level={args.level}  checkpoint step={ckpt['step']}  "
          f"probing {len(states)} real states x {len(args.alphas)} alpha "
          f"values\n")
    for i, state in enumerate(states):
        recorded_alpha = state[alpha_index]
        print(f"-- state {i} (recorded objective_alpha={recorded_alpha:.3f}) --")
        prev_action = None
        for a in sorted(args.alphas):
            probe = list(state)
            probe[alpha_index] = a
            x = torch.tensor([probe], dtype=torch.float32)
            with torch.no_grad():
                q = net(x, levels).squeeze(0).tolist()
            advantage = q[1] - q[0]
            action = "compact" if advantage > 0 else "defer"
            flip = " <-- action flip vs previous alpha" if (
                prev_action is not None and action != prev_action) else ""
            prev_action = action
            print(f"  alpha={a:5.2f}  Q_defer={q[0]:+.4f}  "
                  f"Q_compact={q[1]:+.4f}  advantage={advantage:+.4f}  "
                  f"-> {action}{flip}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
