"""The masked double DQN (plan §6.3 test_agent; PATHWAYS H §4): the target's
argmax never lands on a masked action (ARCH-2), the discount is
exp(-dN / (n_H N_j)), the target takes the online argmax and the target
network's value, Q = -b at a cold start, the neighbour charge is 0 on hold
and at a cold start, and the export and checkpoints round-trip."""

import math
import unittest

try:
    import torch
except ImportError:  # the tier-1 fallback interpreter may lack torch
    torch = None

from tests import learner_fixtures as fx


@unittest.skipIf(torch is None, "torch is not installed")
class AgentTest(unittest.TestCase):
    def setUp(self):
        from learner.agent import Learner, Settings, Transition
        from learner.reward import NeighbourCase
        self.Transition, self.NeighbourCase = Transition, NeighbourCase
        self.learner = Learner(Settings.from_dict(fx.settings()["learner"]),
                               c_w=2.0)

    def transition(self, **kw):
        base = dict(key="interior-2", s=[0.1, 0.2, 0.3], a=1,
                    b=[0, 0.5, -0.5, 1], s2=[0.2, 0.1, 0.0],
                    mask2=[True, True, False, True], b2=[0, 1, -5, 2],
                    dn=1000.0, n_ops=2000.0, c_bytes=100.0, cost=50.0)
        base.update(kw)
        return self.Transition(**base)

    def test_the_target_argmax_skips_masked_actions(self):
        t = self.transition()
        self.learner.add(t)
        m = self.learner.models["interior-2"]
        y, _, a2 = self.learner.targets(m, [t])
        # -b2 = [0, -1, 5, -2]: defer (index 2) would win but is masked.
        self.assertEqual(int(a2[0]), 0)
        r, _ = self.learner.reward(t)
        self.assertAlmostEqual(float(y[0]), r + math.exp(-0.25) * 0.0, places=5)
        self.assertEqual(self.learner.audit_breaches, 0)

    def test_the_discount_counts_operations_in_turnovers(self):
        t = self.transition(dn=3000.0, n_ops=1500.0)
        self.assertAlmostEqual(self.learner.gamma(t), math.exp(-3000 / (2 * 1500)))

    def test_a_cold_start_is_the_prior(self):
        t = self.transition()
        self.learner.add(t)
        m = self.learner.models["interior-2"]
        f = m.online.f(torch.tensor([t.s]))[0]
        self.assertEqual(f.tolist(), [0.0] * 4)
        self.assertEqual(float(m.online.v_own(torch.tensor([t.s]))[0]), 0.0)

    def test_the_reward_is_the_cost_over_cw_c_plus_the_neighbour_charge(self):
        t = self.transition()
        r, own = self.learner.reward(t)
        self.assertAlmostEqual(own, -50.0 / (2.0 * 100.0))
        self.assertEqual(r, own)  # hold-free here, but no neighbour cases
        case = self.NeighbourCase(key="interior-3", c_bytes=1000.0,
                                  mask=[1, 1, 1, 1], prior=[0, 0, 0, 0],
                                  x_action=[1.0, 0, 0], x_hold=[0.0, 0, 0])
        t = self.transition(neighbours=[case])
        self.assertEqual(self.learner.neighbour_charge(t), 0.0)  # no model yet
        self.learner.add(self.transition(key="interior-3"))
        nbr = self.learner.models["interior-3"].target
        with torch.no_grad():
            nbr.out.bias[4] = 0.0
            nbr.out.weight[4, :] = 0.0
            # V_own = -h: a costlier future for the neighbour under the action
            # (x_action larger), so X > 0 and the reward falls.
            nbr.out.weight[4, :] = -1.0
            for layer in nbr.hidden:
                layer.weight.fill_(1.0)
                layer.bias.fill_(0.0)
        x = self.learner.neighbour_charge(t)
        self.assertGreater(x, 0)
        r, own = self.learner.reward(t)
        self.assertAlmostEqual(r, own - x / 200.0)

    def test_training_moves_q_toward_the_target_without_nan(self):
        for i in range(8):
            self.learner.add(self.transition(cost=40.0 + i))
        self.assertTrue(self.learner.ready("interior-2"))
        stats = [self.learner.train_step("interior-2") for _ in range(30)]
        self.assertFalse(any(s["nan"] for s in stats))
        self.assertLess(stats[-1]["td_abs"], stats[0]["td_abs"])
        self.assertEqual(self.learner.models["interior-2"].steps, 30)

    def test_export_and_checkpoint_round_trip(self):
        from learner import weights as W
        from learner.agent import Learner, Settings
        self.learner.note_names("interior", ["a", "b", "c"])
        for i in range(8):
            self.learner.add(self.transition(cost=40.0 + i))
        for _ in range(5):
            self.learner.train_step("interior-2")
        w = self.learner.export()
        self.assertEqual(w.version, 1)
        back = W.decode(W.encode(w), {"interior": ["a", "b", "c"]})
        model = back.find("interior", 2)
        s = [0.3, -0.2, 0.9]
        f_torch = self.learner.models["interior-2"].online.f(torch.tensor([s]))[0]
        for a, b in zip(W.forward(model, s), f_torch.tolist()):
            self.assertAlmostEqual(a, b, places=5)
        saved = self.learner.state_dict()
        fresh = Learner(Settings.from_dict(fx.settings()["learner"]), c_w=2.0)
        fresh.load_state_dict(saved)
        self.assertEqual(fresh.version, 1)
        self.assertEqual(len(fresh.replay["interior-2"]), 8)
        f_fresh = fresh.models["interior-2"].online.f(torch.tensor([s]))[0]
        self.assertEqual(f_fresh.tolist(), f_torch.tolist())

    def test_settings_are_checked(self):
        from learner.agent import Settings
        with self.assertRaises(ValueError):
            Settings.from_dict(fx.settings(echo="both")["learner"])
        with self.assertRaises(ValueError):
            Settings.from_dict(fx.settings(min_replay=1)["learner"])


if __name__ == "__main__":
    unittest.main()
