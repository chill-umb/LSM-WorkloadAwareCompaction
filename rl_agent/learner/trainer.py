#!/usr/bin/env python3
"""The trainer (plan §4 trainer.py; PATHWAYS H §6): tails the plugin's
transition log, prices each transition (reward.py), trains one masked double
DQN per agent (agent.py), and every push_interval_ms writes a new weights
version atomically (weights.py), keeping a copy of every version in the
archive for ARCH-6. It stops on SIGTERM or SIGINT, or once the plugin's
decision log has its "stop" line and the transition log is read to its end,
then saves its checkpoint and a summary with the totals ARCH-7 compares
with 04's.

  python -m learner.trainer --plugin-config CFG --transitions T.jsonl \\
      --decisions D.jsonl --weights W.bin --archive DIR --settings S.json \\
      --log trainer.jsonl --summary trainer_summary.json \\
      [--checkpoint-in CK.pt] [--checkpoint-out CK.pt]

Run from rl_agent/ (or with rl_agent on PYTHONPATH).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import signal
import sys
import time
from collections import defaultdict
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "learner"

import torch  # noqa: E402

from learner import reward as reward_module  # noqa: E402
from learner.agent import Learner, Settings, Transition  # noqa: E402
from learner.logs import (ACTIONS, SCHEMA, LogTail, finite,  # noqa: E402
                          model_key, vector)
from learner.prices import from_plugin_config  # noqa: E402
from learner.weights import write_atomic  # noqa: E402


class Totals:
    """C_W and C_R as the trainer prices them, every interval of every level
    from the start to the drain's end, the buckets included (ARCH-7)."""

    def __init__(self, pricing):
        self.p = pricing
        self.parts = defaultdict(float)

    def add(self, t: dict, jobs: list) -> None:
        p, c = self.p, t["cost"]
        lc = reward_module.level_cost(p, t, jobs)
        self.parts["job_part"] += lc.tau
        self.parts["level_read"] += lc.read
        self.parts["interference_read"] += lc.intf_read + lc.write_path_read
        self.parts["interference_write"] += lc.intf_write + lc.write_path_write
        self.parts["write_path_read"] += lc.write_path_read
        self.parts["write_path_write"] += lc.write_path_write
        self.parts["hit_read"] += p.base("block") * c["hit_reads"]
        self.parts["jobs"] += lc.jobs
        self.parts[f"transitions_level_{t['level']}"] += 1
        if t["level"] == 0:  # every record carries the global counts
            for name, value in reward_module.buckets(p, c).items():
                self.parts[name] += value

    def summary(self) -> dict:
        d = dict(self.parts)
        d["C_W"] = (d.get("job_part", 0) + d.get("fixed_write", 0) +
                    d.get("interference_write", 0))
        d["C_R"] = (d.get("level_read", 0) + d.get("hit_read", 0) +
                    d.get("scan_base", 0) + d.get("memtable", 0) +
                    d.get("fixed_read", 0) + d.get("interference_read", 0))
        return d


def build(t: dict, jobs: list, pricing, learner: Learner):
    """The replayable Transition of a transition line, or None (and why)."""
    if t.get("schema") != SCHEMA:
        return None, "schema"
    if not t.get("valid"):
        return None, "invalid"
    if t.get("state") is None or t.get("next_state") is None:
        return None, "no state"
    if t["agent"] != t["next_agent"]:
        return None, "agent changed"
    C, N = t.get("C"), t.get("N")
    if not (finite(C) and C > 0 and finite(N) and N > 0):
        return None, "no divisor or turnover"
    learner.note_names(t["agent"], list(t["state"].keys()))
    action = t["action"]
    cost = reward_module.level_cost(pricing, t, jobs).weighted(pricing)
    cases = []
    if action != "hold":
        for side, step in (("up", -1), ("down", 1)):
            nbr_state = t.get(f"{side}_state")
            if nbr_state is None:
                continue
            nbr_agent = t[f"{side}_agent"]
            learner.note_names(nbr_agent, list(nbr_state.keys()))
            rel = reward_module.release(
                t["agent"], t["state"], action, t["value_before"],
                t["value_after"], C, t.get("down_C"))
            x_a, x_h = reward_module.predict_neighbour(side, nbr_agent,
                                                      nbr_state, rel)
            cases.append(reward_module.NeighbourCase(
                key=model_key(nbr_agent, t["level"] + step),
                c_bytes=t.get(f"{side}_C"), mask=t[f"{side}_mask"],
                prior=t[f"{side}_prior_cost"], x_action=vector(x_a),
                x_hold=vector(x_h)))
    return Transition(
        key=model_key(t["agent"], t["level"]), s=vector(t["state"]),
        a=ACTIONS.index(action), b=[float(x) for x in t["prior_cost"]],
        s2=vector(t["next_state"]), mask2=[bool(x) for x in t["next_mask"]],
        b2=[float(x) for x in t["next_prior_cost"]], dn=float(t["dn"]),
        n_ops=float(N), c_bytes=float(C), cost=cost, neighbours=cases), ""


class Trainer:
    def __init__(self, args, settings: dict, plugin_cfg: dict):
        self.args = args
        self.pricing = from_plugin_config(plugin_cfg)
        self.settings = Settings.from_dict(settings["learner"])
        self.push_s = float(plugin_cfg["push_interval_ms"]) / 1000.0
        self.steps_per_transition = float(settings["train_steps_per_transition"])
        self.learner = Learner(self.settings, self.pricing.c_w)
        if args.checkpoint_in:
            self.learner.load_state_dict(
                torch.load(args.checkpoint_in, weights_only=False))
        self.tail = LogTail(args.transitions)
        self.decisions = LogTail(args.decisions) if args.decisions else None
        self.pending = defaultdict(list)
        self.totals = Totals(self.pricing)
        self.skipped = defaultdict(int)
        self.added = 0
        self.credit = 0.0
        self.trained_since_push = False
        self.stats = defaultdict(list)
        self.stop_seen = False
        self.stopping = False
        Path(args.archive).mkdir(parents=True, exist_ok=True)
        self.log = open(args.log, "a", encoding="ascii")
        self.pushes = 0

    def ingest(self) -> int:
        new = 0
        for r in self.tail.read():
            if r.get("type") == "job":
                self.pending[(r["level"], r["interval"])].append(r)
            elif r.get("type") == "transition":
                jobs = self.pending.pop((r["level"], r["id"]), [])
                self.totals.add(r, jobs)
                t, why = build(r, jobs, self.pricing, self.learner)
                if t is None:
                    self.skipped[why] += 1
                else:
                    self.learner.add(t)
                    self.added += 1
                    self.credit += self.steps_per_transition
                    new += 1
        if self.decisions is not None:
            for d in self.decisions.read():
                if d.get("type") == "stop":
                    self.stop_seen = True
        return new

    def train(self, budget_s: float) -> None:
        deadline = time.monotonic() + budget_s
        keys = [k for k in self.learner.models if self.learner.ready(k)]
        while keys and self.credit >= 1 and time.monotonic() < deadline:
            for key in keys:
                self.stats[key].append(self.learner.train_step(key))
            self.credit -= 1
            self.trained_since_push = True

    def push(self) -> None:
        w = self.learner.export()
        write_atomic(w, self.args.weights)
        shutil.copyfile(self.args.weights,
                        os.path.join(self.args.archive, f"weights.v{w.version}.bin"))
        self.pushes += 1
        self.trained_since_push = False
        record = {"type": "push", "version": w.version, "t": time.time(),
                  "transitions": self.added, "skipped": dict(self.skipped),
                  "audit_breaches": self.learner.audit_breaches,
                  "replay": {k: len(v) for k, v in self.learner.replay.items()},
                  "models": {}}
        for key, items in self.stats.items():
            if not items:
                continue
            record["models"][key] = {
                "steps": self.learner.models[key].steps,
                "loss": sum(i["loss"] for i in items) / len(items),
                "td_abs": sum(i["td_abs"] for i in items) / len(items),
                "q_abs_max": max(i["q_abs_max"] for i in items),
                "target_mean": sum(i["target_mean"] for i in items) / len(items),
                "nan": any(i["nan"] for i in items)}
        self.stats.clear()
        self.log.write(json.dumps(record) + "\n")
        self.log.flush()

    def run(self) -> int:
        # A warm start hands the plugin the trained weights before its first
        # decision.
        if self.learner.models:
            self.push()
        last_push = time.monotonic()
        while True:
            new = self.ingest()
            self.train(budget_s=0.2)
            if self.trained_since_push and time.monotonic() - last_push >= self.push_s:
                self.push()
                last_push = time.monotonic()
            if self.stopping or (self.stop_seen and new == 0 and
                                 not self.tail.partial):
                break
            if new == 0 and self.credit < 1:
                time.sleep(0.05)
        self.ingest()
        if self.trained_since_push:
            self.push()
        return self.finish()

    def finish(self) -> int:
        if self.args.checkpoint_out:
            torch.save(self.learner.state_dict(), self.args.checkpoint_out)
        summary = {"type": "summary", "version": self.learner.version,
                   "pushes": self.pushes, "transitions": self.added,
                   "skipped": dict(self.skipped),
                   "unmatched_job_lines": sum(len(v) for v in self.pending.values()),
                   "audit_breaches": self.learner.audit_breaches,
                   "totals": self.totals.summary(),
                   "steps": {k: m.steps for k, m in self.learner.models.items()}}
        Path(self.args.summary).write_text(json.dumps(summary, indent=2) + "\n")
        self.log.close()
        return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plugin-config", required=True)
    parser.add_argument("--transitions", required=True)
    parser.add_argument("--decisions")
    parser.add_argument("--weights", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--settings", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--checkpoint-in")
    parser.add_argument("--checkpoint-out")
    args = parser.parse_args(argv)
    settings = json.loads(Path(args.settings).read_text())
    plugin_cfg = json.loads(Path(args.plugin_config).read_text())
    trainer = Trainer(args, settings, plugin_cfg)

    def stop(signum, frame):
        trainer.stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    torch.set_num_threads(int(settings.get("torch_threads", 1)))
    return trainer.run()


if __name__ == "__main__":
    raise SystemExit(main())
