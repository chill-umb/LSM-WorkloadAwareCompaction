"""The turn mode's weights file (learner/turn_weights.py, controller/turn.h
RLTW0001): the inputs and views match the plugin's, the file round-trips,
bad files are refused, and the golden files the plugin's
turn_plugin_test.cc reads (TurnGolden): fixed weights of both arms, a few
inputs (some past the clip, one not finite), and the scores this module's
forward pass gives, which follows turn.cc's order of operations. Regenerate
after an intended change with RL_REGEN_GOLDEN=1 and review the diff."""

import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from learner import turn_weights as TW  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "controller" / "tests" / "fixtures"
GOLDEN_TXT = FIXTURES / "turn_golden.txt"
ACTING = 5


def value(seed, i, j, k):
    return round(math.sin(seed * (7 * i + 3 * j + 11 * k + 1)) * 0.3, 6)


def fixed_net(view, outputs, seed, hidden=(7, 5)):
    layers, width = [], len(view)
    for li, h in enumerate(list(hidden) + [outputs]):
        rows = [[value(seed, li, r, c) for c in range(width)] for r in range(h)]
        layers.append((rows, [value(seed, li + 10, r, 0) for r in range(h)]))
        width = h
    return TW.Net(view, layers)


def golden(arch: str) -> TW.TurnWeights:
    n = len(TW.feature_names(ACTING))
    mean = [value(0.3, 20, i, 0) for i in range(n)]
    scale = [0.5 + abs(value(0.9, 21, i, 0)) for i in range(n)]
    if arch == "factored":
        nets = [fixed_net(list(range(n)), 1 + 4 * ACTING, 0.7)]
    else:
        nets = [fixed_net(TW.vdn_view(level, ACTING), 4, 0.5 + 0.1 * level)
                for level in range(ACTING)]
    return TW.TurnWeights(version=4, arch=TW.ARCHS[arch], acting=ACTING, clip=5.0,
                          mean=mean, scale=scale, nets=nets, spread=2.5)


def cases():
    n = len(TW.feature_names(ACTING))
    xs = [[value(1.3, 30 + c, i, 0) * 4 for i in range(n)] for c in range(2)]
    big = [value(1.7, 40, i, 0) * 40 for i in range(n)]    # past the clip
    big[3] = math.nan                                       # not finite: 0
    return xs + [big]


def golden_lines():
    lines = []
    for arch in ("factored", "vdn"):
        w = golden(arch)
        for x in cases():
            scores = TW.forward(w, x)
            xs = " ".join("nan" if not math.isfinite(v) else repr(float(v)) for v in x)
            ss = " ".join(repr(float(s)) for level in scores for s in level)
            lines.append(f"{arch} {len(x)} {xs} | {ss}")
    return lines


class InputsTest(unittest.TestCase):
    def test_names_blocks_and_views_match_the_plugin(self):
        names = TW.feature_names(ACTING)
        self.assertEqual(len(names), 35)
        self.assertEqual(names[:6], list(TW.GLOBAL))
        self.assertEqual(names[6:11], list(TW.L0_BLOCK))
        self.assertEqual(names[11], "l1_score")
        self.assertEqual(names[34], "l4_probes_get")
        self.assertEqual(TW.block(0, ACTING), list(range(6, 11)))
        self.assertEqual(TW.block(2, ACTING), list(range(17, 23)))
        self.assertIsNone(TW.block(5, ACTING))
        # 17 inputs at L0, 23 at L1, 24 deeper (the design's VDN section).
        self.assertEqual([len(TW.vdn_view(i, ACTING)) for i in range(ACTING)],
                         [17, 23, 24, 24, 24])
        self.assertEqual(TW.vdn_view(4, ACTING)[12:18], [-1] * 6)
        self.assertEqual(TW.vdn_view(1, ACTING)[6:11], list(range(6, 11)))


class FileTest(unittest.TestCase):
    def test_round_trip_and_refusals(self):
        for arch in ("factored", "vdn"):
            w = golden(arch)
            back = TW.decode(TW.encode(w))
            self.assertEqual((back.version, back.arch, back.acting), (4, w.arch, ACTING))
            self.assertEqual(back.mean, w.mean)
            self.assertEqual(back.spread, 2.5)    # the reward's spread rides along
            for x in cases():
                self.assertEqual(TW.forward(back, x), TW.forward(w, x))
            data = bytearray(TW.encode(w))
            data[40] ^= 1
            with self.assertRaises(ValueError):
                TW.decode(bytes(data))
        bad = golden("vdn")
        bad.nets = bad.nets[:4]
        with self.assertRaises(ValueError):
            TW.encode(bad)
        bad = golden("factored")
        bad.scale[0] = 0
        with self.assertRaises(ValueError):
            TW.encode(bad)
        for spread in (0.0, -1.0, math.nan, math.inf):
            bad = golden("vdn")
            bad.spread = spread
            with self.assertRaises(ValueError):
                TW.encode(bad)

    def test_write_atomic_replaces_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "w.bin")
            TW.write_atomic(golden("factored"), path)
            w = golden("factored")
            w.version = 5
            TW.write_atomic(w, path)
            self.assertEqual(TW.decode(Path(path).read_bytes()).version, 5)
            self.assertEqual(os.listdir(d), ["w.bin"])


class GoldenTest(unittest.TestCase):
    def test_golden_files_are_current(self):
        lines = golden_lines()
        if os.environ.get("RL_REGEN_GOLDEN") == "1":
            for arch in ("factored", "vdn"):
                (FIXTURES / f"turn_golden_{arch}.bin").write_bytes(TW.encode(golden(arch)))
            GOLDEN_TXT.write_text("\n".join(lines) + "\n")
        for arch in ("factored", "vdn"):
            self.assertEqual((FIXTURES / f"turn_golden_{arch}.bin").read_bytes(),
                             TW.encode(golden(arch)), arch)
        self.assertEqual(GOLDEN_TXT.read_text().splitlines(), lines)


if __name__ == "__main__":
    unittest.main()
