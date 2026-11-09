"""The turn mode's two value functions (learner/turn_model.py; the design's
§8, §9 and stage 1's list): a cold net ties every action, so greedy holds;
the factored arm's argmax respects the masks and its Q centres each head over
the allowed actions; a VDN agent's choice depends only on its own view, the
team value and the target add the agents' values, and one transition moves
every agent's weights; and both arms' exports compute in the plugin's
arithmetic (turn_weights.forward, which the C++ golden test pins) what torch
computes."""

import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import torch
except ImportError:  # the prime's system python; the workers' venv has it
    torch = None

if torch is not None:
    from learner import turn_model as TM  # noqa: E402
from learner import turn_weights as TW  # noqa: E402

ACTING = 5
N_IN = 35


def randomise(model, seed=1):
    g = torch.Generator().manual_seed(seed)
    with torch.no_grad():
        for p in model.parameters():
            p.copy_(torch.randn(p.shape, generator=g, dtype=torch.float64) * 0.4)


def no_grad(test):
    def run(self):
        with torch.no_grad():
            test(self)
    run.__name__ = test.__name__
    return run


def all_allowed(batch=1):
    return torch.ones(batch, ACTING, 4, dtype=torch.bool)


@unittest.skipIf(torch is None, "needs torch")
class ColdStartTest(unittest.TestCase):
    @no_grad
    def test_every_action_ties_and_greedy_holds(self):
        for arch in ("factored", "vdn"):
            model = TM.make(arch, ACTING)
            x = torch.randn(3, N_IN, dtype=torch.float64)
            self.assertEqual(float(model.scores(x).detach().abs().max()), 0.0, arch)
            self.assertEqual(TM.greedy(model, x, all_allowed(3)).tolist(),
                             [[0] * ACTING] * 3, arch)


def l0_prior(values, batch=1):
    """[B, acting, 4]: `values` at L0, 0 elsewhere (the trainer's prior_q)."""
    out = torch.zeros(batch, ACTING, 4, dtype=torch.float64)
    out[:, 0, :] = torch.tensor(values, dtype=torch.float64)
    return out


@unittest.skipIf(torch is None, "needs torch")
class PriorTest(unittest.TestCase):
    """turn_prior: Q = net - prior, the plugin's greedy choice of L0's scores
    less the prior; the factored arm's prior centred over the allowed
    actions like its advantages."""
    @no_grad
    def test_cold_greedy_follows_the_prior_within_the_masks(self):
        for arch in ("factored", "vdn"):
            model = TM.make(arch, ACTING)
            x = torch.randn(1, N_IN, dtype=torch.float64)
            prior = l0_prior([0.0, 2.0, 0.0, -3.0])       # expand cheapest
            got = TM.greedy(model, x, all_allowed(), prior)[0].tolist()
            self.assertEqual(got, [3] + [0] * (ACTING - 1), arch)
            masks = all_allowed()
            masks[0, 0, 3] = False                      # expand masked: hold (0 ties defer)
            self.assertEqual(TM.greedy(model, x, masks, prior)[0, 0].item(), 0, arch)
            prior = l0_prior([0.0, -1.0, 0.0, 2.0])     # compact cheapest
            self.assertEqual(TM.greedy(model, x, all_allowed(), prior)[0, 0].item(), 1, arch)

    @no_grad
    def test_joint_less_the_prior_term(self):
        x = torch.randn(4, N_IN, dtype=torch.float64)
        a = torch.tensor([[3, 0, 2, 0, 1], [1, 2, 0, 0, 0], [0, 1, 1, 3, 0], [3, 0, 0, 0, 0]])
        masks = all_allowed(4)
        masks[:, 0, 2] = False
        p = [0.5, -1.5, 4.0, 2.0]
        prior = l0_prior(p, 4)
        for arch in ("factored", "vdn"):
            model = TM.make(arch, ACTING)
            randomise(model, 3)
            q = model.joint(x, a, masks)
            got = TM.joint_prior(model, x, a, masks, prior)
            self.assertTrue(torch.equal(TM.joint_prior(model, x, a, masks, None), q))
            for b in range(4):
                taken = p[a[b, 0]]
                if arch == "factored":     # centred over L0's allowed actions
                    taken -= (p[0] + p[1] + p[3]) / 3
                self.assertAlmostEqual(float(got[b]), float(q[b]) - taken, places=12)
            # greedy of (scores - prior) maximises joint_prior over L0's action
            best = TM.greedy(model, x, masks, prior)
            for b in range(4):
                values = []
                for act in (0, 1, 3):
                    aa = best[b:b + 1].clone()
                    aa[0, 0] = act
                    values.append(float(TM.joint_prior(model, x[b:b + 1], aa,
                                                       masks[b:b + 1], prior[b:b + 1])[0]))
                self.assertAlmostEqual(max(values), float(TM.joint_prior(
                    model, x[b:b + 1], best[b:b + 1], masks[b:b + 1], prior[b:b + 1])[0]),
                    places=12)


@unittest.skipIf(torch is None, "needs torch")
class FactoredTest(unittest.TestCase):
    @no_grad
    def test_argmax_respects_the_masks(self):
        model = TM.make("factored", ACTING)
        with torch.no_grad():
            bias = model.net[-1].bias
            bias[1 + 4 * 2 + 3] = 5.0   # level 2 prefers expand
            bias[1 + 4 * 2 + 1] = 2.0   # then compact
        x = torch.zeros(1, N_IN, dtype=torch.float64)
        masks = all_allowed()
        self.assertEqual(TM.greedy(model, x, masks)[0, 2].item(), 3)
        masks[0, 2, 3] = False
        self.assertEqual(TM.greedy(model, x, masks)[0, 2].item(), 1)
        masks[0, 2, 1] = False
        self.assertEqual(TM.greedy(model, x, masks)[0, 2].item(), 0)

    @no_grad
    def test_q_is_v_plus_heads_centred_over_the_allowed_actions(self):
        model = TM.make("factored", ACTING)
        randomise(model)
        x = torch.randn(2, N_IN, dtype=torch.float64)
        masks = all_allowed(2)
        masks[0, 1, 2] = False
        masks[1, 4, 1:] = False          # hold only: the level adds nothing
        actions = torch.tensor([[1, 3, 0, 2, 0], [0, 0, 1, 1, 0]])
        q = model.joint(x, actions, masks)
        v, a = model.heads(x)
        for b in range(2):
            want = float(v[b])
            for i in range(ACTING):
                allowed = [float(a[b, i, k]) for k in range(4) if masks[b, i, k]]
                want += float(a[b, i, actions[b, i]]) - sum(allowed) / len(allowed)
            self.assertAlmostEqual(float(q[b]), want, places=12)


@unittest.skipIf(torch is None, "needs torch")
class VdnTest(unittest.TestCase):
    @no_grad
    def test_each_agent_sees_only_its_own_view(self):
        model = TM.make("vdn", ACTING)
        randomise(model, 2)
        x = torch.randn(1, N_IN, dtype=torch.float64)
        base = model.scores(x)
        for level in range(ACTING):
            outside = [j for j in range(N_IN) if j not in TW.vdn_view(level, ACTING)]
            y = x.clone()
            y[0, outside] += 3.0
            self.assertTrue(torch.equal(model.scores(y)[0, level], base[0, level]), level)
            inside = [j for j in TW.vdn_view(level, ACTING) if j >= 0]
            y = x.clone()
            y[0, inside] += 3.0
            self.assertFalse(torch.equal(model.scores(y)[0, level], base[0, level]), level)

    @no_grad
    def test_team_value_and_target_add_the_agents_values(self):
        model = TM.make("vdn", ACTING)
        randomise(model, 3)
        x = torch.randn(2, N_IN, dtype=torch.float64)
        actions = torch.tensor([[1, 0, 2, 3, 0], [0, 3, 3, 1, 2]])
        q = model.scores(x)
        team = model.joint(x, actions, all_allowed(2))
        for b in range(2):
            self.assertAlmostEqual(float(team[b]),
                                   sum(float(q[b, i, actions[b, i]]) for i in range(ACTING)),
                                   places=12)
        # The bootstrap: each agent's greedy allowed choice, summed.
        masks = all_allowed(2)
        masks[0, 0, :] = torch.tensor([True, False, False, False])
        a_star = TM.greedy(model, x, masks)
        self.assertEqual(a_star[0, 0].item(), 0)
        boot = model.joint(x, a_star, masks)
        best = q.masked_fill(~masks, float("-inf")).max(-1).values.sum(-1)
        self.assertTrue(torch.allclose(boot, best))

    def test_one_transition_moves_every_agents_weights(self):
        model = TM.make("vdn", ACTING)
        before = [[p.clone() for p in net.parameters()] for net in model.nets]
        opt = torch.optim.Adam(model.parameters(), lr=1e-2)
        x = torch.randn(1, N_IN, dtype=torch.float64)
        q = model.joint(x, torch.tensor([[1, 2, 0, 3, 1]]), all_allowed())
        loss = torch.nn.functional.huber_loss(q, torch.tensor([1.0], dtype=torch.float64))
        opt.zero_grad()
        loss.backward()
        opt.step()
        for level, net in enumerate(model.nets):
            moved = any(not torch.equal(p, b) for p, b in zip(net.parameters(), before[level]))
            self.assertTrue(moved, level)


@unittest.skipIf(torch is None, "needs torch")
class ExportTest(unittest.TestCase):
    def test_export_carries_the_spread(self):
        model = TM.make("vdn", ACTING)
        w = TM.export(model, 3, [0.0] * N_IN, [1.0] * N_IN, 5.0, spread=7.5)
        self.assertEqual(TW.decode(TW.encode(w)).spread, 7.5)
        self.assertEqual(TM.export(model, 3, [0.0] * N_IN, [1.0] * N_IN, 5.0).spread, 1.0)

    @no_grad
    def test_export_computes_what_torch_computes(self):
        rng = random.Random(5)
        for arch in ("factored", "vdn"):
            model = TM.make(arch, ACTING)
            randomise(model, 4)
            mean = [rng.uniform(-1, 1) for _ in range(N_IN)]
            scale = [rng.uniform(0.5, 2) for _ in range(N_IN)]
            w = TW.decode(TW.encode(TM.export(model, 7, mean, scale, 5.0)))
            self.assertEqual((w.version, w.arch, w.acting), (7, TW.ARCHS[arch], ACTING))
            for _ in range(5):
                x = [rng.uniform(-12, 12) for _ in range(N_IN)]
                xs = TM.scale_inputs(torch.tensor([x], dtype=torch.float64),
                                     torch.tensor(mean, dtype=torch.float64),
                                     torch.tensor(scale, dtype=torch.float64), 5.0)
                ours = TW.forward(w, x)
                theirs = model.scores(xs)[0].detach()
                for i in range(ACTING):
                    for k in range(4):
                        self.assertTrue(math.isclose(ours[i][k], float(theirs[i, k]),
                                                     rel_tol=1e-9, abs_tol=1e-12),
                                        (arch, i, k))


if __name__ == "__main__":
    unittest.main()
