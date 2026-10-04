"""The plugin's decision and transition lines parse in Python (plan §6.2).

controller/tests/log_test.cc writes the same golden file byte for byte, so the
C++ writer and this reader are held to one format (A-Impl-8, H §6).
"""
import json
import math
import unittest
from pathlib import Path

from learner import logs

GOLDEN = (Path(__file__).resolve().parents[2] / "controller" / "tests" /
          "fixtures" / "log_golden.jsonl")
ACTIONS = logs.ACTIONS
AGENTS = logs.AGENTS
# Reopens per level since D-21 (log schema 3); cost model 2's counts since
# schema 4 (D-23, D-24).
COST_KEYS = set(logs.COST_KEYS)
# H §2's cost-model-2 inputs, in every agent's state (schema 4).
COST_MODEL_2_STATE = {"slot_move", "flush_running", "own_ops", "own_bytes",
                      "own_intf_rd", "own_intf_wr", "rho0", "rho1", "e_hd",
                      "R_st", "c_job", "intf_rd", "intf_wr"}
# A few names each agent's state must carry (G §3, H §2).
STATE_CORE = {
    "interior": {"phi", "anchor", "timing", "score", "burst", "burst_absent",
                 "queue", "backlog"},
    "l0": {"l0_fill", "anchor", "trigger", "mem_fill", "busy_share"},
    "last": {"fill", "headroom", "anchor", "timing"},
}


def number_or_null(value):
    return value is None or (isinstance(value, (int, float))
                             and not isinstance(value, bool)
                             and math.isfinite(value))


def parse(path=GOLDEN):
    with open(path, encoding="ascii") as handle:
        return [json.loads(line) for line in handle]


class LogFormatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lines = parse()

    def check_mask_and_b(self, mask, b):
        self.assertEqual(len(mask), 4)
        self.assertTrue(all(flag in (0, 1) for flag in mask))
        self.assertEqual(mask[0], 1, "hold is always allowed")
        # prior_cost is H §7's b in cost units: lower is better.
        self.assertEqual(len(b), 4)
        self.assertTrue(all(number_or_null(x) for x in b))
        self.assertEqual(b[0], 0, "the prior of hold is 0")

    def check_state(self, agent, state):
        self.assertIn(agent, AGENTS)
        if state is None:
            return
        self.assertIsInstance(state, dict)
        self.assertTrue(STATE_CORE[agent] <= set(state), agent)
        self.assertTrue(COST_MODEL_2_STATE <= set(state), agent)
        self.assertTrue(all(number_or_null(v) for v in state.values()))

    def test_every_line_is_a_known_record(self):
        self.assertTrue(self.lines)
        for line in self.lines:
            self.assertIn(line["type"], ("decision", "transition", "job"))
            self.assertEqual(line["schema"], logs.SCHEMA)

    def test_decision_lines(self):
        decisions = [l for l in self.lines if l["type"] == "decision"]
        self.assertTrue(decisions)
        for d in decisions:
            for key in ("id", "level", "op", "t_us", "weights"):
                self.assertIsInstance(d[key], int, key)
            self.assertIn(d["agent"], AGENTS)
            self.assertIn(d["action"], ACTIONS)
            self.check_mask_and_b(d["mask"], d["prior_cost"])
            self.assertEqual(d["mask"][ACTIONS.index(d["action"])], 1,
                             "the action taken was allowed")
            for key in ("old", "requested", "effective", "anchor"):
                self.assertTrue(number_or_null(d[key]), key)
            # L0 acts on its trigger: anchor and offset; levels on m_i.
            self.assertIn("offset" if d["agent"] == "l0" else "timing", d)
            self.assertEqual(d["level"] == 0, d["agent"] == "l0")
            # Q in the learner modes, with the weights version it used.
            if d["q"] is not None:
                self.assertEqual(len(d["q"]), 4)
                self.assertTrue(d["mode"] in ("prior-only", "learned"))
        self.assertTrue(any(d["q"] for d in decisions))

    def test_transition_lines(self):
        transitions = [l for l in self.lines if l["type"] == "transition"]
        self.assertTrue(transitions)
        keys = {}
        for t in transitions:
            self.assertEqual(t["dn"], t["end_op"] - t["start_op"])
            self.check_state(t["agent"], t["state"])
            self.check_state(t["next_agent"], t["next_state"])
            if t["state"] is None:
                # The interval opened at start: no decision began it.
                self.assertIsNone(t["mask"])
                self.assertIsNone(t["prior_cost"])
                self.assertIsNone(t["action"])
                self.assertEqual(t["id"], 0)
            else:
                self.check_mask_and_b(t["mask"], t["prior_cost"])
                self.assertIn(t["action"], ACTIONS)
            if t["next_state"] is None:
                self.assertIsNone(t["next_mask"])
            else:
                self.check_mask_and_b(t["next_mask"], [0, 0, 0, 0])
            for agent, state in ((t["agent"], t["state"]),
                                 (t["next_agent"], t["next_state"])):
                if state is not None:
                    self.assertEqual(keys.setdefault(agent, set(state)),
                                     set(state), "one key set per agent")
            self.assertEqual(set(t["cost"]), COST_KEYS)
            self.assertTrue(all(number_or_null(v) for v in t["cost"].values()))
            self.assertIn(t["valid"], (0, 1))
            # Replay needs both states; an invalid line says why.
            if t["valid"]:
                self.assertIsNotNone(t["state"])
                self.assertIsNotNone(t["next_state"])
                self.assertEqual(t["invalid"], "")
            else:
                self.assertNotEqual(t["invalid"], "")

    def test_the_prior_is_named_by_its_sign(self):
        # Logged as prior_cost (lower is better), never as an unlabelled b.
        for line in self.lines:
            self.assertNotIn("b", line)
            if line["type"] != "job":
                self.assertIn("prior_cost", line)

    def test_transitions_carry_cost_model_2_fields(self):
        transitions = [l for l in self.lines if l["type"] == "transition"]
        for t in transitions:
            for key in ("C", "N", "value_before", "value_after"):
                self.assertTrue(number_or_null(t[key]), key)
            for side in ("up", "down"):
                state = t[f"{side}_state"]
                if state is None:
                    self.assertIsNone(t[f"{side}_mask"])
                    continue
                self.check_state(t[f"{side}_agent"], state)
                self.check_mask_and_b(t[f"{side}_mask"],
                                      t[f"{side}_prior_cost"])
                self.assertTrue(number_or_null(t[f"{side}_C"]))
            if t["next_state"] is not None:
                self.check_mask_and_b(t["next_mask"], t["next_prior_cost"])
        self.assertTrue(any(t["up_state"] for t in transitions))

    def test_job_lines(self):
        jobs = [l for l in self.lines if l["type"] == "job"]
        self.assertTrue(jobs)
        for j in jobs:
            self.assertIn(j["kind"], logs.KINDS)
            self.assertEqual(tuple(j["win"]), logs.STEP_TYPES)
            self.assertTrue(all(number_or_null(v) for v in j["win"].values()))
            self.assertIn(j["win_own"], (0, 1))
            if j["kind"] == "flush":
                self.assertEqual(j["start_level"], -1)
                self.assertEqual(j["level"], 0)
            if j["win_own"]:
                self.assertEqual(j["win_start"], j["n_begin"])
                self.assertEqual(j["win_ops"], j["n_end"] - j["n_begin"])

    def test_unmeasured_values_are_null(self):
        states = [t["state"] for t in self.lines
                  if t["type"] == "transition" and t["state"]]
        self.assertTrue(any(v is None for s in states for v in s.values()))

    def test_slot_moves_leave_the_read_total_unchanged(self):
        # D.16: the slot-blocking rule moves reads from L0 to the level that
        # held the slot; summed over levels, charged reads equal raw reads.
        moved = 0.0
        for t in self.lines:
            if t["type"] == "transition":
                cost = t["cost"]
                moved += cost["slot_in_probes"] - cost["slot_out_probes"]
        self.assertAlmostEqual(moved, 0.0)


if __name__ == "__main__":
    unittest.main()
