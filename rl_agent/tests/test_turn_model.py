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


SPLIT = [{"levels": [0, 1], "summary": True}, {"levels": [2, 3, 4], "l0_inputs": True}]


@unittest.skipIf(torch is None, "needs torch")
class SplitTest(unittest.TestCase):
    def test_views_add_the_deep_summary_and_l0_inputs(self):
        ix = {n: i for i, n in enumerate(TW.feature_names(ACTING))}
        deep = lambda ks: [v for k in ks for v in (ix[f"l{k}_anchor"], ix[f"l{k}_owed"])]
        self.assertEqual(TM.split_view(0, ACTING, True, False), TW.vdn_view(0, ACTING) + deep([2, 3, 4]))
        self.assertEqual(TM.split_view(1, ACTING, True, False), TW.vdn_view(1, ACTING) + deep([3, 4]))
        self.assertEqual(TM.split_view(3, ACTING, True, False), TW.vdn_view(3, ACTING))
        self.assertEqual(TM.split_view(1, ACTING, False, True), TW.vdn_view(1, ACTING))
        self.assertEqual(TM.split_view(2, ACTING, False, True),
                         TW.vdn_view(2, ACTING) + [ix["l0_files_k"], ix["l0_anchor"]])
        self.assertEqual(TM.split_view(4, ACTING, False, False), TW.vdn_view(4, ACTING))

    def test_groups_must_cover_every_level_once(self):
        for bad in ([{"levels": [0, 1]}, {"levels": [1, 2, 3, 4]}],
                    [{"levels": [0, 1]}, {"levels": [2, 3]}],
                    [{"levels": [0, 1, 2, 3, 4]}],
                    [{"levels": [0, 1]}, {"levels": []}, {"levels": [2, 3, 4]}]):
            with self.assertRaises(ValueError, msg=bad):
                TM.SplitVdn(ACTING, bad)

    @no_grad
    def test_cold_start_ties_and_each_level_reads_only_its_view(self):
        model = TM.SplitVdn(ACTING, SPLIT)
        x = torch.randn(3, N_IN, dtype=torch.float64)
        self.assertEqual(float(model.scores(x).abs().max()), 0.0)
        self.assertEqual(TM.greedy(model, x, all_allowed(3)).tolist(), [[0] * ACTING] * 3)
        randomise(model, 6)
        x = torch.randn(1, N_IN, dtype=torch.float64)
        base = model.scores(x)
        for level, g in ((0, SPLIT[0]), (1, SPLIT[0]), (2, SPLIT[1]), (3, SPLIT[1]), (4, SPLIT[1])):
            view = TM.split_view(level, ACTING, g.get("summary", False), g.get("l0_inputs", False))
            y = x.clone()
            y[0, [j for j in range(N_IN) if j not in view]] += 3.0
            self.assertTrue(torch.equal(model.scores(y)[0, level], base[0, level]), level)
            y = x.clone()
            y[0, [j for j in view if j >= 0]] += 3.0
            self.assertFalse(torch.equal(model.scores(y)[0, level], base[0, level]), level)

    def test_a_groups_seed_fixes_its_nets_whatever_the_other_group(self):
        a = TM.SplitVdn(ACTING, SPLIT, seed=3)
        b = TM.SplitVdn(ACTING, [SPLIT[0], {"levels": [2, 3, 4]}], seed=3)
        for pa, pb in zip(a.groups[0].parameters(), b.groups[0].parameters()):
            self.assertTrue(torch.equal(pa, pb))
        c = TM.SplitVdn(ACTING, SPLIT, seed=4)
        self.assertFalse(all(torch.equal(pa, pc) for pa, pc in
                             zip(a.groups[0].parameters(), c.groups[0].parameters())))

    @no_grad
    def test_export_is_a_vdn_file_with_the_split_views(self):
        rng = random.Random(8)
        model = TM.SplitVdn(ACTING, SPLIT)
        randomise(model, 9)
        mean = [rng.uniform(-1, 1) for _ in range(N_IN)]
        scale = [rng.uniform(0.5, 2) for _ in range(N_IN)]
        w = TW.decode(TW.encode(TM.export(model, 3, mean, scale, 5.0)))
        self.assertEqual((w.arch, w.acting, len(w.nets)), (TW.ARCHS["vdn"], ACTING, ACTING))
        for level, g in ((0, SPLIT[0]), (1, SPLIT[0]), (2, SPLIT[1]), (3, SPLIT[1]), (4, SPLIT[1])):
            self.assertEqual(w.nets[level].view, TM.split_view(level, ACTING, g.get("summary", False),
                                                               g.get("l0_inputs", False)))
        for _ in range(5):
            x = [rng.uniform(-12, 12) for _ in range(N_IN)]
            xs = TM.scale_inputs(torch.tensor([x], dtype=torch.float64),
                                 torch.tensor(mean, dtype=torch.float64),
                                 torch.tensor(scale, dtype=torch.float64), 5.0)
            ours, theirs = TW.forward(w, x), model.scores(xs)[0]
            for i in range(ACTING):
                for k in range(4):
                    self.assertTrue(math.isclose(ours[i][k], float(theirs[i, k]),
                                                 rel_tol=1e-9, abs_tol=1e-12), (i, k))


@unittest.skipIf(torch is None, "needs torch")
class ExportTest(unittest.TestCase):
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
