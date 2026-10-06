"""plugin_config.py: each value comes from the file that fixes it, every
missing value is named, and the smoke config the preflight composes is the
one controller/tests/config_test.cc proves the plugin accepts."""
import importlib.util
import json
import unittest
from pathlib import Path

import research_objective

PIPELINE = Path(__file__).resolve().parents[1]
ROOT = PIPELINE.parents[1]
spec = importlib.util.spec_from_file_location(
    "plugin_config", PIPELINE / "plugin_config.py")
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)
CONTRACT, _ = research_objective.load_contract()
ADMISSION = json.loads((ROOT / "config" / "admission_test.json").read_text())
# Shared with controller/tests/config_test.cc, which parses it.
SMOKE_FIXTURE = ROOT / "controller" / "tests" / "fixtures" / "smoke_config.json"

BOUNDS = {"m_min": 0.5, "m_max": 2.0, "k0_min": 2, "k0_cap": 8,
          "epsilon": 0.15, "phi_min": 0.575, "alpha": 1.5, "kappa_d": 0.25,
          "kappa_a": 1.0, "setoptions_min_interval_ms": 100,
          "units": {"m_min": "documentation keys are ignored"}}
SETTINGS = {"b_max": 2.0, "rules": "k0_tracking,garbage_hold",
            "rule_garbage_drop": 0.2, "rule_release_fill": 0.9}
# Final prices (PREREGISTRATION D-22 f): two sessions agreed.
PRICES = {"schema": 5, "kind": "final",
          "reproducibility": {"passed": True, "tolerance":
                              CONTRACT["prices"]["reproducibility_tolerance"]},
          "price_per_core_second": CONTRACT["prices"]["price_per_core_second"],
          "c_w": 2e-15, "c_f": 3e-13, "c_blk": 4e-12, "c_sk": 5e-12,
          "c_open": 6e-11, "reopen_timer": {"seconds_per_reopen": 8.3e-6}}
# controller/config.cc's keys: every one required, rule thresholds by rule.
# c_open is one since D-21: the controller prices each level's reopens.
ALWAYS = {"mode", "decision_log", "transition_log", "m_min", "m_max",
          "k0_min", "k0_cap", "epsilon", "phi_min", "alpha", "kappa_d",
          "kappa_a", "setoptions_min_interval_ms", "k", "b_max", "beta_w",
          "beta_r", "beta_s", "c_w", "c_f", "c_blk", "c_sk", "c_open", "c_s",
          "q_bar", "cost_model"}
# A schema-6 (cost model 2) record for the learner arms (D-23, D-24 §2).
KINDS = ("flush", "l0", "deep", "move")
TYPES = ("probe", "block", "seek", "reopen", "step", "iblock", "memtable",
         "get0", "scan0", "put")
PRICES6 = {**PRICES, "schema": 6, "kind": "provisional", "cost_model": 2,
           "c_cr": 0.0, "c_st": 3.4e-16, "c_ib": 0.0, "c_mt": 1e-15,
           "c_get0": 2e-15, "c_sc0": 3e-15, "c_put": 4e-15,
           "job_prices": {"flush": 2.5e-12, "l0": 1.5e-12, "deep": 3.1e-12,
                          "move": 2.9e-12},
           "kappa": {"lambda": 0.0, "B": {x: 1e-9 for x in TYPES},
                     "J": {x: {k: 0.0 for k in KINDS} for x in TYPES}},
           "n_win": 1000}
LEARNER = {"explore": 0.1, "push_interval_ms": 1000, "weights_required": 0}


def compose(arm="rules", contract=CONTRACT, **changes):
    sources = {"bounds": BOUNDS, "settings": SETTINGS, "prices": PRICES,
               "admission": ADMISSION, "contract": contract,
               "decision_log": "d.jsonl", "transition_log": "t.jsonl",
               **changes}
    return pc.compose(arm, "read", "assoc", **sources)


def with_qbar(value):
    contract = json.loads(json.dumps(CONTRACT))
    contract["reference_rate"]["ops_per_second"]["assoc"] = value
    return contract


class ComposeTest(unittest.TestCase):
    def test_every_value_from_its_source(self):
        config, filled = compose(contract=with_qbar(61234.5))
        self.assertEqual(filled, [])
        self.assertEqual(set(config), ALWAYS | {"rules", "rule_garbage_drop"})
        self.assertEqual(config["mode"], "rules")
        self.assertEqual(config["epsilon"], 0.15)
        self.assertEqual(config["b_max"], 2.0)
        # Read priority at the headline beta* = 10 (D-13).
        self.assertEqual((config["beta_w"], config["beta_r"], config["beta_s"]),
                         (1.0, 10.0, 1.0))
        self.assertEqual(config["q_bar"], 61234.5)
        self.assertEqual(config["c_blk"], 4e-12)
        # D-21: stage 18's measured c_open, passed through unchanged.
        self.assertEqual(config["c_open"], 6e-11)
        self.assertEqual(config["c_s"],
                         CONTRACT["prices"]["storage_price_per_byte_second"])
        self.assertEqual(config["k"], ADMISSION["k"])
        # A threshold of a rule that is off is not sent: the plugin refuses
        # no unknown key, but sends only what its rules read.
        self.assertNotIn("rule_release_fill", config)

    def test_beta_star_other_than_the_headline_is_diagnostic_only(self):
        def weights(mode, **changes):
            sources = {"bounds": BOUNDS, "settings": SETTINGS,
                       "prices": PRICES, "admission": ADMISSION,
                       "contract": with_qbar(1.0), "decision_log": "d.jsonl",
                       "transition_log": "t.jsonl", **changes}
            config, _ = pc.compose("rules", mode, "assoc", **sources)
            return config["beta_w"], config["beta_r"], config["beta_s"]
        # A reported beta* (2, 5, 10) in a diagnostic run: only beta changes.
        self.assertEqual(weights("write", beta_star=5, diagnostic=True),
                         (5.0, 1.0, 1.0))
        self.assertEqual(weights("read", beta_star=5, diagnostic=True),
                         (1.0, 5.0, 1.0))
        self.assertEqual(weights("space", beta_star=2, diagnostic=True),
                         (1.0, 1.0, 2.0))
        # beta* does not enter balanced mode.
        self.assertEqual(weights("balanced", beta_star=5, diagnostic=True),
                         (1.0, 1.0, 1.0))
        # The headline, named or not, needs no diagnostic run.
        self.assertEqual(weights("read", beta_star=10), (1.0, 10.0, 1.0))
        self.assertEqual(weights("read"), (1.0, 10.0, 1.0))
        with self.assertRaisesRegex(ValueError, "diagnostic run only"):
            weights("write", beta_star=5)
        with self.assertRaisesRegex(ValueError, "not one the contract reports"):
            weights("write", beta_star=3, diagnostic=True)

    def test_a_learner_arm_gets_cost_model_2_and_its_settings(self):
        config, filled = compose("learned", contract=with_qbar(61234.5),
                                 prices=PRICES6, diagnostic=True,
                                 learner=LEARNER, weights_path="/w.bin",
                                 seed=4)
        self.assertEqual(filled, [])
        self.assertEqual(config["mode"], "learned")
        self.assertEqual(config["cost_model"], 2)
        self.assertEqual(config["c_job_deep"], 3.1e-12)
        self.assertEqual(config["p_dev"], PRICES6["price_per_core_second"])
        self.assertEqual(config["kappa_b_put"], 1e-9)
        self.assertEqual(config["kappa_j_probe_move"], 0.0)
        self.assertEqual(config["n_win"], 1000)
        self.assertEqual((config["explore"], config["seed"],
                          config["weights_path"]), (0.1, 4, "/w.bin"))
        prior, _ = compose("prior", contract=with_qbar(61234.5),
                           prices=PRICES6, diagnostic=True, learner=LEARNER,
                           seed=4)
        self.assertEqual(prior["mode"], "prior-only")
        self.assertNotIn("weights_path", prior)
        self.assertNotIn("push_interval_ms", prior)
        # The hold and rules arms stay on cost model 1.
        hold, _ = compose("hold", contract=with_qbar(61234.5))
        self.assertEqual(hold["cost_model"], 1)
        self.assertNotIn("c_st", hold)

    def test_a_learner_arm_refuses_cost_model_1_prices_and_missing_keys(self):
        with self.assertRaisesRegex(ValueError, "schema 6"):
            compose("learned", contract=with_qbar(61234.5), learner=LEARNER,
                    weights_path="/w.bin", seed=1)
        with self.assertRaisesRegex(ValueError, "weights_path"):
            compose("learned", contract=with_qbar(61234.5), prices=PRICES6,
                    diagnostic=True, learner=LEARNER, seed=1)
        with self.assertRaisesRegex(ValueError, "explore"):
            compose("prior", contract=with_qbar(61234.5), prices=PRICES6,
                    diagnostic=True, learner={}, seed=1)
        broken = json.loads(json.dumps(PRICES6))
        del broken["kappa"]["B"]["put"]
        with self.assertRaisesRegex(ValueError, "kappa.B.put"):
            compose("prior", contract=with_qbar(61234.5), prices=broken,
                    diagnostic=True, learner=LEARNER, seed=1)

    def test_hold_needs_no_rules(self):
        config, _ = compose("hold", contract=with_qbar(1.0))
        self.assertEqual(config["mode"], "hold-only")
        self.assertNotIn("rules", config)

    def test_every_missing_value_is_named(self):
        bounds = {**BOUNDS, "epsilon": None, "k0_cap": None}
        with self.assertRaises(ValueError) as caught:
            compose(bounds=bounds, settings={"rules": "garbage_hold"},
                    prices=None, contract=with_qbar(None))
        message = str(caught.exception)
        for name in ("epsilon (D-18", "k0_cap (D-18", "b_max (Gate N3",
                     "rule_garbage_drop (Gate N3", "q_bar (contract",
                     "c_w (prices", "c_sk (prices", "c_open (prices"):
            self.assertIn(name, message)

    def test_refusals(self):
        cases = [
            ({"settings": {**SETTINGS, "rules": "k0_tracking,fast"}},
             "unknown rule"),
            ({"prices": {**PRICES, "schema": 1}}, "per core-second"),
            ({"prices": {**PRICES, "schema": 2}}, "reopens apart"),
            # D-21: a schema-3 file has no reopen-time reference.
            ({"prices": {**PRICES, "schema": 3}}, "reopen-time reference"),
            ({"prices": {k: v for k, v in PRICES.items()
                         if k != "reopen_timer"}}, "reopen_timer"),
            ({"prices": {k: v for k, v in PRICES.items() if k != "c_open"}},
             "c_open"),
            ({"prices": {**PRICES, "c_f": 0}}, "positive money price"),
            ({"bounds": {**BOUNDS, "alpha": "1.5"}}, "finite number"),
            ({"bounds": {**BOUNDS, "alpha": float("nan")}}, "finite number"),
        ]
        for changes, message in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError) as caught:
                    compose(contract=with_qbar(1.0), **changes)
                self.assertIn(message, str(caught.exception))
        with self.assertRaises(ValueError):
            compose("native")

    def test_placeholders_fill_only_what_is_missing_and_say_so(self):
        config, filled = compose(bounds={**BOUNDS, "epsilon": None},
                                 settings={}, contract=with_qbar(None),
                                 placeholders=True)
        self.assertEqual(config["epsilon"], pc.PLACEHOLDERS["epsilon"])
        self.assertEqual(config["alpha"], BOUNDS["alpha"])
        self.assertEqual(config["c_w"], PRICES["c_w"])
        self.assertEqual(config["rules"], pc.PLACEHOLDERS["rules"])
        self.assertEqual(filled, sorted(
            ["epsilon", "b_max", "q_bar", "rules", "rule_l0_early_read_ratio",
             "rule_garbage_drop", "rule_release_fill"]))

    def test_the_smoke_config_is_the_plugin_fixture(self):
        """The preflight's smoke config, from nothing but placeholders, is the
        file config_test.cc parses: composer and plugin stay in lockstep."""
        config, _ = compose(bounds={}, settings={}, prices=None,
                            contract=with_qbar(None), placeholders=True,
                            decision_log="decisions.jsonl",
                            transition_log="transitions.jsonl")
        self.assertEqual(config, json.loads(SMOKE_FIXTURE.read_text()))


if __name__ == "__main__":
    unittest.main()
