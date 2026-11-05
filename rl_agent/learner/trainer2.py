#!/usr/bin/env python3
"""The learned2 trainer (controller/learned2.h; owner, 2026-11-05). Tails the
plugin's decision log (its "window", "f2" and "stop" lines) and the fork's
host log; prices every window on the host log as 04 does (learned2.py,
F5); scores each level's ensemble on the window before learning from it
(the prequential error: the honest "does the loss go down" curve); trains
every level that has decided at least once on the windows settled for it
(F2), E bootstrap members each (F3); and every push_interval_ms writes a new
weights version atomically. Cold start: nothing is read from another run.

  python -m learner.trainer2 --plugin-config CFG --decisions D.jsonl \\
      --host-log H.jsonl --weights W.bin --settings S.json \\
      --log trainer2.jsonl --summary trainer2_summary.json

Telemetry lines in --log: "window" (the priced window), "pred" (a level's
prediction before training on it), "train" (one level's round: training
loss, fit error, prequential error, noise floor), "push", "stop".
Run from rl_agent/ (or with rl_agent on PYTHONPATH)."""

from __future__ import annotations

import argparse
import json
import math
import signal
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "learner"

import torch  # noqa: E402

from learner import learned2 as L2  # noqa: E402
from learner.logs import LogTail  # noqa: E402
from learner.prices import from_plugin_config  # noqa: E402
from learner.weights import WeightsFile, write_atomic  # noqa: E402

SETTINGS_KEYS = ("ensemble", "hidden", "layers", "lr", "huber", "steps",
                 "batch", "min_windows", "push_interval_ms", "seed",
                 "preq_span", "torch_threads")


class Trainer2:
    def __init__(self, args, settings: dict, plugin_cfg: dict):
        self.args = args
        self.s = settings
        self.pricer = L2.HostLogPricer(from_plugin_config(plugin_cfg))
        self.decisions = LogTail(args.decisions)
        self.host = LogTail(args.host_log)
        self.log = open(args.log, "a", encoding="ascii")
        self.pending = []            # window lines not priced yet
        self.ens = {}                # level -> Ensemble
        self.decided = set()         # levels the plugin has decided for
        self.history = []            # (x, y, settled, key, op1) of every clean window
        self.fresh = set()           # levels with samples since their last round
        self.preq = {}               # level -> [(op1, err)]
        self.series = {}             # level -> [(settings key, y)]
        self.version = 0
        self.stop_seen = False
        self.last_push = 0.0
        self.windows = self.priced = self.dropped = 0

    def emit(self, record: dict) -> None:
        self.log.write(json.dumps(record, separators=(",", ":")) + "\n")
        self.log.flush()

    def ingest(self) -> None:
        for r in self.host.read():
            self.pricer.add(r)
        for r in self.decisions.read():
            t = r.get("type")
            if t == "window":
                self.pending.append(r)
                self.windows += 1
            elif t == "f2":
                self.pending.append(r)
            elif t == "stop":
                self.stop_seen = True

    def price_pending(self) -> None:
        """Windows and first decisions in log order: a window waits until the
        host log has passed its end, and holds back what follows it, so a
        level's backfill sees every window before its first decision."""
        while self.pending:
            r = self.pending[0]
            if r.get("type") == "f2":
                level = int(r["level"])
                # A frozen level agent only holds (config.h deep_frozen):
                # nothing to learn for it.
                if (level not in self.decided and level <= 6 and
                        r.get("reason") != "frozen"):
                    self.decided.add(level)
                    self.backfill(level)
                self.pending.pop(0)
                continue
            b = self.pricer.bounds(int(r["op0"]), int(r["op1"]))
            if b is None:
                past_drain = (self.pricer.ds is not None and
                              int(r["op1"]) > self.pricer.recs[self.pricer.ds]["op"])
                if past_drain or (self.stop_seen and self.host_done()):
                    self.dropped += 1
                    self.pending.pop(0)
                    continue
                return
            self.learn_window(r, self.pricer.price(*b))
            self.pending.pop(0)

    def host_done(self) -> bool:
        return self.pricer.ds is not None

    def learn_window(self, w: dict, p: dict) -> None:
        self.priced += 1
        ops = p["ops"]
        jpo = p["J"] / ops if ops > 0 else math.nan
        x = [float(w["x"][n]) for n in L2.F_NAMES]
        changed = int(w.get("changed", 0)) == 1
        settled = [float(v) for v in w.get("settled", [])]
        self.emit({"type": "window", "op0": w["op0"], "op1": w["op1"], "ops": ops,
                   "secs": p["secs"], "C_W": p["C_W"], "C_R": p["C_R"], "C_S": p["C_S"],
                   "J": p["J"], "J_per_op": jpo, "k": w.get("k"), "m": w.get("m"),
                   "changed": int(changed), "settled": settled, "jobs": p["jobs"]})
        if not (jpo > 0) or changed:
            return
        y = math.log(jpo)
        key = (w.get("k"), tuple(round(v, 6) for v in w.get("m", [])))
        self.history.append((x, y, settled, key, int(w["op1"])))
        for level in sorted(self.decided):
            if level > 6 or level >= len(settled) or settled[level] != 1:
                continue
            ens = self.ensemble(level)
            if len(ens.samples) >= int(self.s["min_windows"]):
                preds = ens.predict(x)
                mean = sum(preds) / len(preds)
                err = y - mean
                self.preq.setdefault(level, []).append((w["op1"], err))
                self.emit({"type": "pred", "level": level, "op1": w["op1"], "y": y,
                           "pred": mean, "err": err,
                           "spread": max(preds) - min(preds)})
            ens.add(x, y, int(w["op1"]))
            self.series.setdefault(level, []).append((key, y))
            self.fresh.add(level)

    def ensemble(self, level: int) -> "L2.Ensemble":
        ens = self.ens.get(level)
        if ens is None:
            ens = self.ens[level] = L2.Ensemble(
                members=int(self.s["ensemble"]), hidden=int(self.s["hidden"]),
                layers=int(self.s["layers"]), lr=float(self.s["lr"]),
                huber=float(self.s["huber"]), seed=int(self.s["seed"]) * 100 + level)
        return ens

    def backfill(self, level: int) -> None:
        """A level's first decision: its ensemble starts from every clean
        window so far that was settled for it (no prequential score: the
        windows are already past)."""
        n = 0
        for x, y, settled, key, op1 in self.history:
            if level < len(settled) and settled[level] == 1:
                self.ensemble(level).add(x, y, op1)
                self.series.setdefault(level, []).append((key, y))
                n += 1
        if n:
            self.fresh.add(level)
        self.emit({"type": "backfill", "level": level, "windows": n})

    def train_round(self) -> bool:
        trained = False
        span = int(self.s["preq_span"])
        for level in sorted(self.fresh):
            ens = self.ens[level]
            if len(ens.samples) < int(self.s["min_windows"]):
                continue
            t0 = time.monotonic()
            r = ens.train(int(self.s["steps"]), int(self.s["batch"]))
            recent = [e for _, e in self.preq.get(level, [])[-span:]]
            preq = math.sqrt(sum(e * e for e in recent) / len(recent)) if recent else math.nan
            self.emit({"type": "train", "level": level, "op": ens.samples[-1].op1,
                       "n": r["n"], "train_loss": r["train_loss"], "fit_rmse": r["fit_rmse"],
                       "preq_rmse": preq, "preq_n": len(recent),
                       "noise_floor": L2.noise_floor(self.series.get(level, [])),
                       "secs": time.monotonic() - t0})
            trained = True
        self.fresh.clear()
        return trained

    def push(self) -> None:
        models = []
        for level in sorted(self.ens):
            ens = self.ens[level]
            if len(ens.samples) >= int(self.s["min_windows"]):
                models += ens.export(level)
        if not models:
            return
        self.version += 1
        write_atomic(WeightsFile(version=self.version, models=models), self.args.weights)
        self.emit({"type": "push", "version": self.version, "models": len(models),
                   "levels": sorted(self.ens)})

    def step(self) -> None:
        self.ingest()
        self.price_pending()
        now = time.monotonic()
        if now - self.last_push >= float(self.s["push_interval_ms"]) / 1000:
            self.last_push = now
            if self.train_round():
                self.push()

    def done(self) -> bool:
        return self.stop_seen and not self.pending

    def finish(self, why: str) -> int:
        self.emit({"type": "stop", "why": why, "windows": self.windows,
                   "priced": self.priced, "dropped": self.dropped, "version": self.version})
        summary = {"windows": self.windows, "priced": self.priced, "dropped": self.dropped,
                   "weights_version": self.version,
                   "levels": {str(level): {"samples": len(e.samples),
                                           "preq": len(self.preq.get(level, []))}
                              for level, e in sorted(self.ens.items())},
                   "why": why}
        Path(self.args.summary).write_text(json.dumps(summary, indent=1) + "\n")
        self.log.close()
        return 0


def load_settings(path: str) -> dict:
    raw = json.loads(Path(path).read_text())
    s = raw.get("learner2", raw)
    missing = [k for k in SETTINGS_KEYS if k not in s]
    if missing:
        raise ValueError(f"learner2 settings lack {missing}")
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for name in ("plugin-config", "decisions", "host-log", "weights", "settings",
                 "log", "summary"):
        ap.add_argument(f"--{name}", required=True)
    ap.add_argument("--poll-s", type=float, default=0.5)
    ap.add_argument("--max-idle-s", type=float, default=600.0,
                    help="give up after this long with no new line and no stop")
    args = ap.parse_args(argv)
    settings = load_settings(args.settings)
    torch.set_num_threads(int(settings["torch_threads"]))
    plugin_cfg = json.loads(Path(args.plugin_config).read_text())
    if plugin_cfg.get("mode") != "learned2":
        raise SystemExit("trainer2 needs a learned2 plugin config")
    tr = Trainer2(args, settings, plugin_cfg)
    stopping = {"why": None}

    def stop(signum, frame):  # noqa: ARG001
        stopping["why"] = f"signal {signum}"

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    idle_since = time.monotonic()
    while stopping["why"] is None:
        before = (tr.decisions.offset, tr.host.offset)
        tr.step()
        if tr.done():
            if tr.train_round():
                tr.push()
            return tr.finish("plugin stop")
        if (tr.decisions.offset, tr.host.offset) != before:
            idle_since = time.monotonic()
        elif time.monotonic() - idle_since > args.max_idle_s:
            return tr.finish("idle")
        time.sleep(args.poll_s)
    return tr.finish(stopping["why"])


if __name__ == "__main__":
    sys.exit(main())
