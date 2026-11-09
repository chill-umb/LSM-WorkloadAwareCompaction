"""plugin_config.py: each value comes from the file that fixes it, every
missing value is named, and the smoke config the preflight composes is the
one controller/tests/config_test.cc proves the plugin accepts."""
import importlib.util
import json
import os
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
# The turn arm's config from the shipped settings, which turn_plugin_test.cc
# parses (regenerate with RL_REGEN_GOLDEN=1 after an intended change).
TURN_FIXTURE = ROOT / "controller" / "tests" / "fixtures" / "turn_config.json"

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

    def test_learned2_gets_its_keys_and_a_diagnostic_q_bar_stand_in(self):
        # controller/learned2.h (owner, 2026-11-05); the shipped settings file.
        shipped = json.loads((ROOT / "config" / "learner2_settings.json").read_text())
        learner = pc.learner_values(shipped, "training")
        config, filled = compose("learned2", prices=PRICES6, diagnostic=True,
                                 learner=learner, weights_path="/w.bin", seed=4,
                                 q_bar_standin=50000.0)
        self.assertEqual(filled, [])
        self.assertEqual(config["mode"], "learned2")
        self.assertEqual(config["cost_model"], 2)
        self.assertEqual(config["q_bar"], 50000.0)
        self.assertEqual(config["weights_path"], "/w.bin")
        for key in pc.LEARNED2_KEYS:
            self.assertEqual(config[key], shipped["plugin"][key])
        # The noise fix (owner, 2026-11-05): cycle windows (window_ops their
        # longest), K trials, frozen level agents, a minimum effect.
        self.assertEqual(config["window_ops"], 4000000)
        for key in pc.LEARNED2_OPTIONAL_KEYS:
            self.assertEqual(config[key], shipped["plugin"][key])
        self.assertEqual((config["window_cycles"], config["k_trials"],
                          config["deep_frozen"]), (1, 1, 1))
        # The optional keys stay out when the settings lack them.
        plain = {k: v for k, v in learner.items()
                 if k not in pc.LEARNED2_OPTIONAL_KEYS}
        config, _ = compose("learned2", prices=PRICES6, diagnostic=True,
                            learner=plain, weights_path="/w.bin", seed=4,
                            q_bar_standin=50000.0)
        for key in pc.LEARNED2_OPTIONAL_KEYS:
            self.assertNotIn(key, config)
        # The stand-in: diagnostic only, never over a contract value.
        with self.assertRaisesRegex(ValueError, "diagnostic run only"):
            compose("learned2", prices=PRICES6, learner=learner,
                    weights_path="/w.bin", seed=4, q_bar_standin=50000.0)
        with self.assertRaisesRegex(ValueError, "the contract has a q-bar"):
            compose("learned2", contract=with_qbar(61234.5), prices=PRICES6,
                    diagnostic=True, learner=learner, weights_path="/w.bin",
                    seed=4, q_bar_standin=50000.0)
        # Without its keys, learned2 is refused and names them.
        with self.assertRaisesRegex(ValueError, "window_ops"):
            compose("learned2", prices=PRICES6, diagnostic=True,
                    learner=LEARNER, weights_path="/w.bin", seed=4,
                    q_bar_standin=50000.0)

    def test_the_turn_arms_get_their_keys_from_the_turn_settings(self):
        shipped = json.loads((ROOT / "config" / "turn_settings.json").read_text())
        learner = pc.learner_values(shipped, "training")
        configs = {}
        for arm, arch in pc.TURN_ARMS.items():
            config, filled = compose(arm, prices=PRICES6, diagnostic=True,
                                     learner=learner, weights_path="/w.bin", seed=4,
                                     q_bar_standin=50000.0,
                                     decision_log="decisions.jsonl",
                                     transition_log="transitions.jsonl", decay=1)
            self.assertEqual(filled, [])
            self.assertEqual((config["mode"], config["turn_arch"]), ("turn", arch))
            self.assertEqual(config["turn_decay"], 1)
            self.assertEqual(config["cost_model"], 2)
            self.assertEqual(config["weights_path"], "/w.bin")
            self.assertEqual(config["seed"], 4)
            self.assertNotIn("explore", config)       # the turn's own rate
            for key in (*pc.TURN_KEYS, "push_interval_ms", "weights_required"):
                self.assertEqual(config[key], shipped["plugin"][key], key)
            configs[arm] = config
        # The two arms differ in the value function only.
        self.assertEqual({k for k in configs["turn"] if configs["turn"][k] !=
                          configs["turn_vdn"][k]}, {"turn_arch"})
        if os.environ.get("RL_REGEN_GOLDEN") == "1":
            TURN_FIXTURE.write_text(json.dumps(configs["turn"], indent=2,
                                               sort_keys=True) + "\n")
        self.assertEqual(configs["turn"], json.loads(TURN_FIXTURE.read_text()))
        # Without a turn key, the arm is refused and names it.
        partial = {k: v for k, v in learner.items() if k != "turn_kappa"}
        with self.assertRaisesRegex(ValueError, "turn_kappa"):
            compose("turn", prices=PRICES6, diagnostic=True, learner=partial,
                    weights_path="/w.bin", seed=4, q_bar_standin=50000.0, decay=1)
        with self.assertRaisesRegex(ValueError, "turn_m_rungs must be a string"):
            compose("turn", prices=PRICES6, diagnostic=True,
                    learner={**learner, "turn_m_rungs": 1.0},
                    weights_path="/w.bin", seed=4, q_bar_standin=50000.0, decay=1)

    def test_the_decay_flag(self):
        """DECAY: the turn arms' turn_decay, required
        there and nowhere else; 0, no decay, only in a diagnostic run."""
        shipped = json.loads((ROOT / "config" / "turn_settings.json").read_text())
        turn = dict(prices=PRICES6, learner=pc.learner_values(shipped, "training"),
                    weights_path="/w.bin", seed=4, q_bar_standin=50000.0)
        for arm in pc.TURN_ARMS:
            on, _ = compose(arm, diagnostic=True, decay=1, **turn)
            off, _ = compose(arm, diagnostic=True, decay=0, **turn)
            self.assertEqual((on["turn_decay"], off["turn_decay"]), (1, 0))
            # The flag is the only difference, so the config's identity (03's
            # plugin_config_sha256, in the fingerprint) tells the runs apart.
            self.assertEqual({k for k in on if on[k] != off[k]}, {"turn_decay"})
            with self.assertRaisesRegex(ValueError, "turn_decay"):
                compose(arm, diagnostic=True, **turn)
            with self.assertRaisesRegex(ValueError, "diagnostic run only"):
                compose(arm, decay=0, **turn)
            for bad in (2, -1, True, 0.5):
                with self.assertRaisesRegex(ValueError, "0 or 1"):
                    compose(arm, diagnostic=True, decay=bad, **turn)
        with self.assertRaisesRegex(ValueError, "turn arms"):
            compose("rules", contract=with_qbar(61234.5), decay=1)
        self.assertNotIn("turn_decay", compose("rules", contract=with_qbar(61234.5))[0])

    def test_the_prior_flag(self):
        """PRIOR: the turn arms' turn_prior, 0 by default; 1, the L0 prior,
        only in a diagnostic run and only for a turn arm."""
        shipped = json.loads((ROOT / "config" / "turn_settings.json").read_text())
        turn = dict(prices=PRICES6, learner=pc.learner_values(shipped, "training"),
                    weights_path="/w.bin", seed=4, q_bar_standin=50000.0, decay=1)
        for arm in pc.TURN_ARMS:
            off, _ = compose(arm, diagnostic=True, **turn)
            on, _ = compose(arm, diagnostic=True, prior=1, **turn)
            self.assertEqual((off["turn_prior"], on["turn_prior"]), (0, 1))
            # The flag is the only difference (03's plugin_config_sha256).
            self.assertEqual({k for k in on if on[k] != off[k]}, {"turn_prior"})
            with self.assertRaisesRegex(ValueError, "diagnostic run only"):
                compose(arm, prior=1, **turn)
            for bad in (2, -1, True, 0.5):
                with self.assertRaisesRegex(ValueError, "0 or 1"):
                    compose(arm, diagnostic=True, prior=bad, **turn)
        with self.assertRaisesRegex(ValueError, "turn arms"):
            compose("rules", contract=with_qbar(61234.5), diagnostic=True, prior=1)
        self.assertNotIn("turn_prior", compose("rules", contract=with_qbar(61234.5))[0])

    def test_the_hold_arms_scheduled_change(self):
        """The step-response arm (owner, 2026-10-07): hold only, diagnostic
        only, apply_at_op with apply_m, apply_k0 optional; none by default."""
        common = dict(contract=with_qbar(61234.5))
        hold, _ = compose("hold", **common)
        for key in ("apply_at_op", "apply_m", "apply_k0"):
            self.assertNotIn(key, hold)
        hold, _ = compose("hold", diagnostic=True, apply_at_op=2900000,
                          apply_m="1:1:2:1", **common)
        self.assertEqual((hold["apply_at_op"], hold["apply_m"]),
                         (2900000, "1:1:2:1"))
        self.assertNotIn("apply_k0", hold)
        hold, _ = compose("hold", diagnostic=True, apply_at_op=5,
                          apply_m="1:1:2:1", apply_k0=4, **common)
        self.assertEqual(hold["apply_k0"], 4)
        refusals = [
            (dict(apply_at_op=5, apply_m="1:2"), "diagnostic run only"),
            (dict(diagnostic=True, apply_at_op=5), "needs apply_at_op and apply_m"),
            (dict(diagnostic=True, apply_m="1:2"), "needs apply_at_op and apply_m"),
            (dict(diagnostic=True, apply_at_op=0, apply_m="1:2"), "positive integer"),
            (dict(diagnostic=True, apply_at_op=5, apply_m="2:1"), "L0's 1"),
            (dict(diagnostic=True, apply_at_op=5, apply_m="1:x"), "separated numbers"),
            (dict(diagnostic=True, apply_at_op=5, apply_m="1:2", apply_k0=0),
             "apply_k0 must be a positive integer"),
        ]
        for changes, message in refusals:
            with self.subTest(changes=changes), \
                    self.assertRaisesRegex(ValueError, message):
                compose("hold", **common, **changes)
        with self.assertRaisesRegex(ValueError, "hold only"):
            compose("rules", diagnostic=True, apply_at_op=5, apply_m="1:2")

    def test_the_hold_arms_schedule(self):
        """The K-step batch's multi-change runs (owner, 2026-10-21): hold
        only, diagnostic only, ';'-separated op|m|k0 entries with rising ops,
        in place of the single change."""
        common = dict(contract=with_qbar(61234.5))
        text = "10000000|1:1:1|8;12500000|1:1:1|0;20000000|1:1:2|3"
        hold, _ = compose("hold", diagnostic=True, apply_schedule=text, **common)
        self.assertEqual(hold["apply_schedule"], text)
        for key in ("apply_at_op", "apply_m", "apply_k0"):
            self.assertNotIn(key, hold)
        refusals = [
            (dict(apply_schedule=text), "diagnostic run only"),
            (dict(diagnostic=True, apply_schedule=text, apply_at_op=5),
             "replaces apply_at_op"),
            (dict(diagnostic=True, apply_schedule="5|1:1"), "is not op|m|k0"),
            (dict(diagnostic=True, apply_schedule="0|1:1|4"), "above 0"),
            (dict(diagnostic=True, apply_schedule="5|1:1|4;5|1:1|6"), "above 5"),
            (dict(diagnostic=True, apply_schedule="5|1:1|4;3|1:1|6"), "above 5"),
            (dict(diagnostic=True, apply_schedule="5|2:1|4"), "L0's 1"),
            (dict(diagnostic=True, apply_schedule="5|1:1|-1"), "k0 '-1'"),
        ]
        for changes, message in refusals:
            with self.subTest(changes=changes), \
                    self.assertRaisesRegex(ValueError, message):
                compose("hold", **common, **changes)
        with self.assertRaisesRegex(ValueError, "hold only"):
            compose("rules", diagnostic=True, apply_schedule=text)

    def test_the_hold_arms_k0_cap(self):
        """hold_k0_cap stands in for D-18's k0_cap on the hold arm of a
        diagnostic run (the scheduled triggers' bound), placeholders or not."""
        common = dict(contract=with_qbar(61234.5))
        bounds = {k: v for k, v in BOUNDS.items() if k != "k0_cap"}
        hold, filled = compose("hold", diagnostic=True, hold_k0_cap=19,
                               bounds=bounds, **common)
        self.assertEqual(hold["k0_cap"], 19)
        self.assertNotIn("k0_cap", filled)
        hold, filled = compose("hold", diagnostic=True, hold_k0_cap=19,
                               bounds={}, placeholders=True, **common)
        self.assertEqual(hold["k0_cap"], 19)
        self.assertNotIn("k0_cap", filled)
        self.assertIn("k0_min", filled)
        for changes, message in [
                (dict(hold_k0_cap=19), "diagnostic run only"),
                (dict(diagnostic=True, hold_k0_cap=1), ">= 2"),
                (dict(diagnostic=True, hold_k0_cap=True), ">= 2")]:
            with self.subTest(changes=changes), \
                    self.assertRaisesRegex(ValueError, message):
                compose("hold", **common, **changes)
        with self.assertRaisesRegex(ValueError, "diagnostic run only"):
            compose("rules", diagnostic=True, hold_k0_cap=19, **common)

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
