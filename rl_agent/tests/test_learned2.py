"""learned2's learner side (rl_agent/learner/learned2.py, trainer2.py):
the inputs match the plugin's, the exported ensemble computes in the
plugin's arithmetic what torch computes, the ensemble learns a cost that
moves with the setting, and the trainer scores a window before learning
from it and backfills a level that starts deciding late."""

from __future__ import annotations

import json
import math
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from learner import learned2 as L2  # noqa: E402
from learner import trainer2 as T2  # noqa: E402
from learner.weights import (AGENT_CODES, WeightsFile, decode, encode,  # noqa: E402
                             features_hash, forward)


def inputs(log2_k=2.0, op=1e6, shares=(0.2, 0.7, 0.1), h=2e10, m=(0, 0, 0, 0, 0, 0)):
    return [shares[0], shares[1], shares[2], math.log10(h), math.log10(op + 1), log2_k, *m]


def small(seed=1, members=4):
    return L2.Ensemble(members=members, hidden=16, layers=2, lr=1e-2, huber=1.0, seed=seed)


class InputsTest(unittest.TestCase):
    def test_names_and_hash_match_the_plugin(self):
        self.assertEqual(len(L2.F_NAMES), 12)
        self.assertEqual(features_hash(L2.F_NAMES), L2.F_HASH)
        self.assertEqual(L2.F_HASH, 0x4D7A0F34E38D4D67)  # learned2_test.cc
        self.assertEqual(AGENT_CODES["f"], 3)
        self.assertEqual(L2.own_feature(0), L2.F_NAMES.index("log2_k"))
        self.assertEqual(L2.own_feature(2), L2.F_NAMES.index("log2_m2"))


class EnsembleTest(unittest.TestCase):
    def test_export_computes_what_torch_computes(self):
        ens = small()
        for i in range(20):
            ens.add(inputs(log2_k=1 + (i % 3)), -10.0 + 0.1 * i, op1=i)
        ens.train(steps=20, batch=8)
        models = ens.export(level=0)
        self.assertEqual(len(models), 4)
        w = decode(encode(WeightsFile(version=3, models=models)), {"f": L2.F_NAMES})
        self.assertEqual(len(w.models), 4)
        x = inputs(log2_k=2.5, op=3e6, h=3.3e10, m=(0.3, -0.3, 0, 0, 0, 0))
        torch_preds = ens.predict(x)
        for model, p in zip(w.models, torch_preds):
            self.assertEqual(model.agent, "f")
            self.assertEqual(model.level, 0)
            # forward() applies NaN -> 0 and the (huge) clip, as mlp.cc does.
            self.assertAlmostEqual(forward(model, x)[0] + ens.y0, p, places=9)

    def test_it_learns_a_cost_that_rises_with_k(self):
        rng = random.Random(4)
        ens = small(members=5)
        for i in range(60):
            k = 1 + (i % 3)  # log2 K of 2, 4, 8
            ens.add(inputs(log2_k=k, op=1e5 * (i + 1)), -9.0 + 0.3 * k + rng.gauss(0, 0.02), op1=i)
        first = ens.train(steps=5, batch=32)
        for _ in range(30):
            last = ens.train(steps=20, batch=32)
        self.assertLess(last["train_loss"], first["train_loss"])
        self.assertLess(last["fit_rmse"], 0.06)
        lo = sum(ens.predict(inputs(log2_k=1, op=6e6))) / 5
        hi = sum(ens.predict(inputs(log2_k=3, op=6e6))) / 5
        self.assertAlmostEqual(hi - lo, 0.6, delta=0.15)

    def test_bootstrap_weights_are_poisson_one(self):
        rng = random.Random(9)
        draws = [L2.poisson1(rng) for _ in range(20000)]
        self.assertAlmostEqual(sum(draws) / len(draws), 1.0, delta=0.03)

    def test_noise_floor_counts_only_neighbours_with_the_same_settings(self):
        series = [("a", 0.0), ("a", 0.2), ("a", 0.0), ("a", 0.2), ("b", 5.0), ("b", 5.2)]
        self.assertAlmostEqual(L2.noise_floor(series), math.sqrt(0.04 / 2), places=9)
        self.assertTrue(math.isnan(L2.noise_floor(series[:2])))


class StubPricer:
    """Every window priceable: J per op = exp(-9 + 0.3 log2 K)."""
    ds = None
    recs = []

    def add(self, r):
        pass

    def bounds(self, op0, op1):
        return (op0, op1)

    def price(self, lo, hi):
        k = self.k
        ops = hi - lo
        return {"ops": ops, "C_W": 0, "C_R": 0, "C_S": 0,
                "J": ops * math.exp(-9 + 0.3 * math.log2(k)), "secs": 1.0, "jobs": 0}


class TrainerTest(unittest.TestCase):
    def make(self, tmp):
        args = type("A", (), {})()
        args.decisions = str(Path(tmp) / "d.jsonl")
        args.host_log = str(Path(tmp) / "h.jsonl")
        args.log = str(Path(tmp) / "t.jsonl")
        args.summary = str(Path(tmp) / "s.json")
        args.weights = str(Path(tmp) / "w.bin")
        s = {"ensemble": 3, "hidden": 8, "layers": 1, "lr": 1e-2, "huber": 1.0, "steps": 5,
             "batch": 8, "min_windows": 4, "push_interval_ms": 0, "seed": 1, "preq_span": 5,
             "torch_threads": 1}
        tr = T2.Trainer2.__new__(T2.Trainer2)
        tr.args, tr.s = args, s
        tr.pricer = StubPricer()
        tr.decisions = T2.LogTail(args.decisions)
        tr.host = T2.LogTail(args.host_log)
        tr.log = open(args.log, "a", encoding="ascii")
        tr.pending, tr.ens, tr.decided, tr.fresh = [], {}, set(), set()
        tr.history, tr.preq, tr.series = [], {}, {}
        tr.version, tr.stop_seen, tr.last_push = 0, False, 0.0
        tr.windows = tr.priced = tr.dropped = 0
        return tr

    def window(self, op0, k, settled=(1, 1, 1), changed=0):
        x = dict(zip(L2.F_NAMES, inputs(log2_k=math.log2(k), op=op0 + 250000)))
        return {"type": "window", "op0": op0, "op1": op0 + 250000, "x": x, "k": k,
                "m": [1, 1, 1], "settled": list(settled), "changed": changed}

    def test_scores_before_learning_backfills_and_pushes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr = self.make(tmp)
            lines = []
            for i in range(6):   # before any decision: history only
                lines.append(self.window(250000 * i, 4))
            lines.append({"type": "f2", "level": 0, "op": 1500000})
            for i in range(6, 12):
                lines.append(self.window(250000 * i, 4 if i % 2 else 6))
            lines.append(self.window(250000 * 12, 4, changed=1))   # never learned from
            lines.append(self.window(250000 * 13, 4, settled=(0, 1, 1)))  # not settled for L0
            Path(tr.args.decisions).write_text("".join(json.dumps(r) + "\n" for r in lines))
            tr.pricer.k = 4
            tr.step()
            tr.log.flush()
            log = [json.loads(line) for line in Path(tr.args.log).read_text().splitlines()]
            kinds = [r["type"] for r in log]
            self.assertEqual(kinds.count("window"), 14)
            backfill = [r for r in log if r["type"] == "backfill"]
            self.assertEqual(backfill, [{"type": "backfill", "level": 0, "windows": 6}])
            preds = [r for r in log if r["type"] == "pred"]
            self.assertEqual(len(preds), 6)    # windows 6..11, once 4 samples exist
            self.assertEqual(len(tr.ens[0].samples), 12)
            self.assertIn("train", kinds)
            self.assertIn("push", kinds)
            w = decode(Path(tr.args.weights).read_bytes(), {"f": L2.F_NAMES})
            self.assertEqual(len(w.models), 3)
            tr.log.close()


if __name__ == "__main__":
    unittest.main()
