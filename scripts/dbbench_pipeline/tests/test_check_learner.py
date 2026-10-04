"""21_check_learner.py on a synthetic learned arm: ARCH-6 recomputes each
decision's Q from the archived weights, ARCH-2 counts masked actions, ARCH-8
counts decision points inside jobs, and --strict fails a run whose weights
never reached the plugin."""

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
spec = importlib.util.spec_from_file_location(
    "check_learner", PIPELINE / "21_check_learner.py")
cl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cl)

from learner import weights as W  # noqa: E402
from learner.logs import read_all  # noqa: E402

GOLDEN = ROOT / "controller" / "tests" / "fixtures"


def arm(tmp: Path, *, version=3, perturb=0.0, masked=False, loaded=True):
    golden = read_all(GOLDEN / "log_golden.jsonl")
    t = next(l for l in golden if l["type"] == "transition" and l["valid"])
    job = next(l for l in golden if l["type"] == "job" and l["kind"] == "deep")
    names = {"interior": list(t["state"]), "l0": None, "last": None}
    for l in golden:
        for a, s in (("agent", "state"), ("next_agent", "next_state"),
                     ("down_agent", "down_state")):
            if l.get(s):
                names[l[a]] = list(l[s])
    w = W.decode((GOLDEN / "mlp_golden.bin").read_bytes(), names)
    model = w.find("interior", 2)
    f = W.forward(model, [math.nan if x is None else x
                          for x in t["state"].values()])
    q = [-b + fa + perturb for b, fa in zip(t["prior_cost"], f)]
    action = 2 if masked else max(
        (a for a in range(4) if t["mask"][a]), key=lambda a: q[a])
    decision = {"type": "decision", "id": t["id"], "level": 2,
                "agent": "interior", "weights": version, "q": q,
                "mask": t["mask"], "action": ["hold", "compact", "defer",
                                              "expand"][action]}
    if masked:
        decision["mask"] = [1, 1, 0, 1]
    lines = [{"type": "start"}] + ([{"type": "weights", "version": version}]
                                   if loaded else []) + [decision,
                                                         {"type": "stop"}]
    (tmp / "decisions.jsonl").write_text(
        "".join(json.dumps(l) + "\n" for l in lines))
    (tmp / "transitions.jsonl").write_text(json.dumps(job) + "\n" +
                                           json.dumps(t) + "\n")
    (tmp / "weights").mkdir()
    (tmp / "weights" / f"weights.v{version}.bin").write_bytes(
        (GOLDEN / "mlp_golden.bin").read_bytes())
    (tmp / "trainer_summary.json").write_text(json.dumps(
        {"pushes": 1, "audit_breaches": 0, "totals": {"C_W": 2.0, "C_R": 3.0}}))
    (tmp / "trainer.jsonl").write_text(json.dumps(
        {"type": "push", "models": {"interior-2": {
            "steps": 5, "td_abs": 0.1, "q_abs_max": 2.0, "nan": False}}}) + "\n")


class CheckLearnerTest(unittest.TestCase):
    def run_check(self, **kw):
        with tempfile.TemporaryDirectory() as d:
            arm(Path(d), **kw)
            return cl.check(Path(d), {"C_W": 1.0, "C_R": 3.0}, 1)

    def test_a_faithful_run_passes(self):
        r = self.run_check()
        self.assertEqual(r["verdict"], "passed", r)
        self.assertEqual(r["arch6"]["decisions"], 1)
        self.assertLess(r["arch6"]["max_abs_dq"], 1e-9)
        self.assertEqual(r["arch8"]["max_d"], 1)
        self.assertAlmostEqual(r["arch7"]["relative_difference_C_W"], 1.0)

    def test_q_values_that_disagree_with_python_are_reported(self):
        r = self.run_check(perturb=0.5)
        self.assertAlmostEqual(r["arch6"]["max_abs_dq"], 0.5)

    def test_a_masked_action_fails_arch2(self):
        r = self.run_check(masked=True)
        self.assertIn("arch2", r["failed"])

    def test_weights_that_never_reached_the_plugin_fail(self):
        r = self.run_check(loaded=False)
        self.assertIn("pushes", r["failed"])


if __name__ == "__main__":
    unittest.main()
