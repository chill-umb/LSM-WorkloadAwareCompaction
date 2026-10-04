"""The trainer (plan §6.3 test_trainer): it tails a growing transition log,
joins each job line to the interval it was charged in, survives a partial
last line, skips what replay must not hold, pushes versioned weights
atomically with an archived copy of each, stops after the plugin's stop
line, and writes its checkpoint and its ARCH-7 totals."""

import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path

try:
    import torch
except ImportError:
    torch = None

from tests import learner_fixtures as fx


@unittest.skipIf(torch is None, "torch is not installed")
class TrainerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = Path(self.dir.name)
        self.paths = {k: str(d / v) for k, v in {
            "plugin": "plugin_config.json", "settings": "settings.json",
            "transitions": "transitions.jsonl", "decisions": "decisions.jsonl",
            "weights": "weights.bin", "archive": "archive",
            "log": "trainer.jsonl", "summary": "summary.json",
            "checkpoint": "checkpoint.pt"}.items()}
        Path(self.paths["plugin"]).write_text(json.dumps(fx.plugin_config()))
        Path(self.paths["settings"]).write_text(json.dumps(fx.settings()))

    def tearDown(self):
        self.dir.cleanup()

    def args(self, checkpoint_in=None):
        p = self.paths
        return argparse.Namespace(
            plugin_config=p["plugin"], transitions=p["transitions"],
            decisions=p["decisions"], weights=p["weights"],
            archive=p["archive"], settings=p["settings"], log=p["log"],
            summary=p["summary"], checkpoint_in=checkpoint_in,
            checkpoint_out=p["checkpoint"])

    def trainer(self, checkpoint_in=None):
        from learner.trainer import Trainer
        return Trainer(self.args(checkpoint_in),
                       json.loads(Path(self.paths["settings"]).read_text()),
                       json.loads(Path(self.paths["plugin"]).read_text()))

    def records(self, n, start_id=100):
        out = []
        for i in range(n):
            tid = start_id + i
            out.append(fx.job_line(interval=tid))
            out.append(fx.interior_transition(id=tid))
        return out

    def append(self, key, text):
        with open(self.paths[key], "a") as handle:
            handle.write(text)

    def test_tails_joins_trains_and_pushes(self):
        tr = self.trainer()
        text = fx.dumps(self.records(6))
        self.append("transitions", text[:-40])  # a partial last line
        self.assertEqual(tr.ingest(), 5)
        self.append("transitions", text[-40:])
        self.assertEqual(tr.ingest(), 1)
        self.assertEqual(sum(len(v) for v in tr.pending.values()), 0)
        # Not replayed: invalid, opened at start, schema 3.
        bad = [fx.interior_transition(id=1, valid=0, invalid="fallback"),
               fx.interior_transition(id=2, state=None),
               fx.interior_transition(id=3, schema=3)]
        self.append("transitions", fx.dumps(bad))
        tr.ingest()
        self.assertEqual(tr.skipped["invalid"], 1)
        self.assertEqual(tr.skipped["no state"], 1)
        self.assertEqual(tr.skipped["schema"], 1)
        tr.train(budget_s=5)
        self.assertGreater(tr.learner.models["interior-2"].steps, 0)
        tr.push()
        tr.push()
        self.assertEqual(sorted(os.listdir(self.paths["archive"])),
                         ["weights.v1.bin", "weights.v2.bin"])
        from learner import weights as W
        names = {"interior": list(fx.interior_transition()["state"]),
                 "last": list(fx.interior_transition()["down_state"])}
        w = W.decode(Path(self.paths["weights"]).read_bytes(), names)
        self.assertEqual(w.version, 2)
        # The neighbours' models exist once their transitions arrive; the
        # charge of a level whose neighbour has none is 0.
        self.assertEqual([m.agent for m in w.models], ["interior"])

    def test_stops_after_the_plugins_stop_line_and_saves(self):
        self.append("transitions", fx.dumps(self.records(6)))
        self.append("decisions", json.dumps({"type": "stop"}) + "\n")
        tr = self.trainer()
        self.assertEqual(tr.run(), 0)
        summary = json.loads(Path(self.paths["summary"]).read_text())
        self.assertEqual(summary["transitions"], 6)
        self.assertGreaterEqual(summary["version"], 1)
        self.assertEqual(summary["audit_breaches"], 0)
        totals = summary["totals"]
        self.assertGreater(totals["C_W"], 0)
        self.assertGreater(totals["C_R"], 0)
        self.assertEqual(totals["jobs"], 6)
        self.assertTrue(Path(self.paths["checkpoint"]).exists())
        # A warm start pushes the trained weights before anything arrives.
        os.remove(self.paths["weights"])
        os.remove(self.paths["transitions"])
        os.remove(self.paths["decisions"])
        self.append("decisions", json.dumps({"type": "stop"}) + "\n")
        warm = self.trainer(checkpoint_in=self.paths["checkpoint"])
        self.assertEqual(warm.run(), 0)
        self.assertTrue(Path(self.paths["weights"]).exists())
        summary = json.loads(Path(self.paths["summary"]).read_text())
        self.assertEqual(summary["version"], totals and summary["version"])
        self.assertGreater(summary["version"], 1)


if __name__ == "__main__":
    unittest.main()
