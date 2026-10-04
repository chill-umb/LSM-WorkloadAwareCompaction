"""Weights export and re-import round-trip, and the golden file the plugin's
mlp_test.cc reads (plan §6.3 test_export; ARCH-6 at unit level): fixed
weights, a few states, and f per action, written by this module's forward
pass, which follows mlp.cc's order of operations. Regenerate after an
intended change with RL_REGEN_GOLDEN=1 and review the diff."""

import math
import os
import tempfile
import unittest
from pathlib import Path

from learner import weights as W
from learner.logs import read_all

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "controller" / "tests" / "fixtures"
GOLDEN_LOG = FIXTURES / "log_golden.jsonl"
GOLDEN_BIN = FIXTURES / "mlp_golden.bin"
GOLDEN_TXT = FIXTURES / "mlp_golden.txt"


def feature_names() -> dict:
    """Each agent's feature names in the plugin's order, from the golden log
    that log_test.cc writes."""
    names = {}
    for line in read_all(GOLDEN_LOG):
        if line["type"] != "transition":
            continue
        for agent_key, state_key in (("agent", "state"),
                                     ("next_agent", "next_state"),
                                     ("up_agent", "up_state"),
                                     ("down_agent", "down_state")):
            state = line.get(state_key)
            if state:
                names.setdefault(line[agent_key], list(state))
    return names


def fixed_model(agent, level, names, hidden=3, seed=1.0, delta=False):
    n = len(names)

    def value(i, j, k):
        return round(math.sin(seed * (7 * i + 3 * j + 11 * k + 1)) * 0.25, 6)

    layers = [([[value(0, r, c) for c in range(n)] for r in range(hidden)],
               [value(1, r, 0) for r in range(hidden)]),
              ([[value(2, r, c) for c in range(hidden)] for r in range(5)],
               [value(3, r, 0) for r in range(5)])]
    d = None
    if delta:
        d = ([[value(4, a, c) for c in range(n)] for a in range(4)],
             [value(5, a, 0) for a in range(4)])
    return W.ModelWeights(agent, level, names, 4.0, layers, d)


def golden_weights() -> W.WeightsFile:
    names = feature_names()
    return W.WeightsFile(version=3, models=[
        fixed_model("l0", 0, names["l0"], seed=0.7),
        fixed_model("interior", 2, names["interior"], seed=1.3),
        fixed_model("interior", -1, names["interior"], seed=1.9, delta=True),
        fixed_model("last", -1, names["last"], seed=2.3)])


def golden_cases(w: W.WeightsFile) -> list:
    """(agent, level, state) cases, NaN and out-of-clip values included."""
    names = feature_names()
    cases = []
    for agent, level in (("l0", 0), ("interior", 2), ("interior", 3),
                         ("last", 5)):
        n = len(names[agent])
        state = [math.sin(0.37 * k + level) * (6 if k % 5 == 0 else 1)
                 for k in range(n)]
        state[1] = math.nan
        cases.append((agent, level, state))
    return cases


def golden_text(w: W.WeightsFile) -> str:
    lines = []
    for agent, level, state in golden_cases(w):
        f = W.forward(w.find(agent, level), state)
        values = " ".join("nan" if math.isnan(v) else repr(v) for v in state)
        lines.append(f"{agent} {level} {len(state)} {values} | " +
                     " ".join(repr(v) for v in f))
    return "\n".join(lines) + "\n"


class ExportTest(unittest.TestCase):
    def test_round_trip(self):
        w = golden_weights()
        back = W.decode(W.encode(w), feature_names())
        self.assertEqual(back.version, 3)
        self.assertEqual(len(back.models), 4)
        for a, b in zip(w.models, back.models):
            self.assertEqual((a.agent, a.level, a.names), (b.agent, b.level, b.names))
            self.assertEqual(a.layers, b.layers)
            self.assertEqual(a.delta, b.delta)

    def test_a_level_model_wins_over_the_shared_one(self):
        w = golden_weights()
        self.assertEqual(w.find("interior", 2).level, 2)
        self.assertEqual(w.find("interior", 3).level, -1)
        self.assertIsNone(W.WeightsFile(1).find("l0", 0))

    def test_corruption_is_refused(self):
        data = bytearray(W.encode(golden_weights()))
        data[-1] ^= 1
        with self.assertRaises(ValueError):
            W.decode(bytes(data), feature_names())
        names = feature_names()
        names["l0"] = names["l0"][:-1] + ["renamed"]
        with self.assertRaises(ValueError):
            W.decode(W.encode(golden_weights()), names)

    def test_atomic_write_replaces_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "weights.bin")
            w = golden_weights()
            W.write_atomic(w, path)
            w.version = 4
            W.write_atomic(w, path)
            self.assertEqual(os.listdir(d), ["weights.bin"])
            self.assertEqual(W.decode(Path(path).read_bytes(),
                                      feature_names()).version, 4)

    def test_nan_becomes_zero_and_values_are_clipped(self):
        self.assertEqual(W.prepare([math.nan, 9, -9, 1.5], 4.0), [0, 4, -4, 1.5])

    def test_golden_files_for_the_plugin(self):
        w = golden_weights()
        data, text = W.encode(w), golden_text(w)
        if os.environ.get("RL_REGEN_GOLDEN") == "1":
            GOLDEN_BIN.write_bytes(data)
            GOLDEN_TXT.write_text(text)
        self.assertEqual(GOLDEN_BIN.read_bytes(), data,
                         "mlp_golden.bin is stale: RL_REGEN_GOLDEN=1")
        self.assertEqual(GOLDEN_TXT.read_text(), text)


if __name__ == "__main__":
    unittest.main()
