"""37_check_turn_run.py on a synthetic turn arm: the pushes counted, the
plugin's logged scores recomputed from the archived weights of their version
(ARCH-6), a changed score or greedy choice failing it, a score that is not
finite failing ARCH-1, and the SetOptions price measured from the calls'
wall time."""
import importlib.util
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
ROOT = PIPELINE.parents[1]
sys.path.insert(0, str(ROOT / "rl_agent"))
from learner import turn_weights as TW  # noqa: E402

spec = importlib.util.spec_from_file_location("check_turn_run",
                                              PIPELINE / "37_check_turn_run.py")
check37 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check37)

ACTING = 5
N_IN = len(TW.feature_names(ACTING))


def net(version, seed, spread=1.0):
    def v(i, j):
        return round(math.sin(seed * (3 * i + 7 * j + 1)) * 0.3, 6)
    layers = [([[v(r, c) for c in range(N_IN)] for r in range(6)], [v(r, 99) for r in range(6)]),
              ([[v(r + 50, c) for c in range(6)] for r in range(1 + 4 * ACTING)],
               [v(r, 77) for r in range(1 + 4 * ACTING)])]
    return TW.TurnWeights(version=version, arch=0, acting=ACTING, clip=5.0,
                          mean=[0.0] * N_IN, scale=[1.0] * N_IN,
                          nets=[TW.Net(list(range(N_IN)), layers)], spread=spread)


def write_arm(root: Path, tamper=None, prior=None):
    """prior: the L0 prior's four values (J per turn) on every line, with
    the weights' spread 2, as the plugin logs them with turn_prior 1."""
    (root / "weights").mkdir(parents=True)
    spread = 2.0 if prior else 1.0
    nets = {v: net(v, 0.3 + 0.2 * v, spread) for v in (1, 2)}
    for v, w in nets.items():
        (root / "weights" / f"turn.v{v}.bin").write_bytes(TW.encode(w))
    (root / "plugin_config.json").write_text(json.dumps(
        {"mode": "turn", "turn_arch": "factored", "turn_acting": ACTING}))
    names = TW.feature_names(ACTING)
    lines = [{"type": "start", "mode": "turn"},
             {"type": "turn_weights", "version": 1, "arch": "factored"},
             {"type": "turn_weights", "version": 2, "arch": "factored"}]
    masks = [[1, 1, 1, 1], [1, 0, 1, 1], [1, 1, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0]]
    for t in range(6):
        x = [0.1 * ((i * 5 + t) % 9) - 0.3 for i in range(N_IN)]
        v = 0 if t == 0 else 1 if t < 3 else 2
        scores = TW.forward(nets[v], x) if v else [[0.0] * 4 for _ in range(ACTING)]
        acted = [list(level) for level in scores]
        if prior:
            acted[0] = [a - b / spread for a, b in zip(acted[0], prior)]
        line = {"type": "turn", "t": t, "final": 0, "weights": v,
                "x": dict(zip(names, x)), "masks": masks, "scores": scores,
                "greedy": check37.greedy(acted, masks)}
        if prior:
            line.update(prior=list(prior), prior_spread=spread if v else 1.0)
        if tamper:
            tamper(line)
        lines.append(line)
        lines.append({"type": "apply", "ok": 1, "apply_us": 1000 + 100 * t})
    lines.append({"type": "stop"})
    (root / "decisions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines))
    (root / "turn_trainer.jsonl").write_text(
        json.dumps({"type": "train", "t": 4, "loss": 0.5}) + "\n")
    (root / "turn_trainer_summary.json").write_text(json.dumps(
        {"weights_version": 2, "transitions": 5}))


PRICES = {"price_per_core_second": 2e-5, "c_w": 3e-14}


class CheckTurnRunTest(unittest.TestCase):
    def run_check(self, tamper=None, options_bytes=None, prior=None):
        with tempfile.TemporaryDirectory() as tmp:
            write_arm(Path(tmp), tamper, prior)
            return check37.check(Path(tmp), PRICES, options_bytes, min_pushes=2)

    def test_a_clean_run_passes_and_c_set_is_measured(self):
        report = self.run_check(options_bytes=7000)
        self.assertEqual(report["verdict"], "passed", report)
        self.assertEqual(report["checks"]["arch6"]["judged"], 5)
        c = report["checks"]["c_set"]
        self.assertEqual(c["calls"], 6)
        self.assertEqual(c["median_us"], 1250)
        self.assertAlmostEqual(c["c_set"], 1250e-6 * 2e-5 + 7000 * 3e-14)

    def test_each_defect_fails_its_check(self):
        def score(line):
            if line["t"] == 4:
                line["scores"][1][2] += 1e-6
        self.assertEqual(self.run_check(score)["failed_checks"], ["arch6"])

        def choice(line):
            if line["t"] == 2:
                line["greedy"][2] = "compact" if line["greedy"][2] == "hold" else "hold"
        self.assertEqual(self.run_check(choice)["failed_checks"], ["arch6"])

        def nan(line):
            if line["t"] == 1:
                line["scores"][0][0] = None
        self.assertIn("arch1", self.run_check(nan)["failed_checks"])

        def version(line):
            if line["t"] == 5:
                line["weights"] = 9      # no archived file
        self.assertEqual(self.run_check(version)["failed_checks"], ["arch6"])
        with tempfile.TemporaryDirectory() as tmp:
            write_arm(Path(tmp))
            report = check37.check(Path(tmp), PRICES, None, min_pushes=3)
            self.assertEqual(report["failed_checks"], ["pushes"])


    def test_the_l0_prior_enters_the_greedy_check(self):
        """turn_prior 1: L0's greedy choice is its scores less the logged
        prior over the weights' spread; ignoring the prior, or a logged
        prior_spread that is not the weights', fails ARCH-6."""
        prior = [0.0, 50.0, 0.0, -50.0]       # expand far cheaper: L0 expands
        report = self.run_check(prior=prior)
        self.assertEqual(report["verdict"], "passed", report)
        self.assertEqual(report["checks"]["arch6"]["judged"], 5)

        def raw(line):                         # the net's choice alone
            line["greedy"] = check37.greedy(line["scores"], line["masks"])
        self.assertEqual(self.run_check(raw, prior=prior)["failed_checks"], ["arch6"])

        def spread(line):
            if line["t"] == 3:
                line["prior_spread"] = 3.0
        report = self.run_check(spread, prior=prior)
        self.assertEqual(report["failed_checks"], ["arch6"])
        self.assertIn("prior_spread", " ".join(report["checks"]["arch6"]["problems"]))


if __name__ == "__main__":
    unittest.main()
