"""Unit tests for the multi-level RL compaction agent.

Run from the repository root:
    .venv/bin/python3 -m unittest discover -s rl_agent/tests -v

Each test names the defect it guards against, so a regression points straight
at the finding it re-opens.
"""

import os
import sys
import threading
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import multilevel  # noqa: E402
from agent import DQNAgent, SharedTrunk  # noqa: E402
from multilevel import (MultiLevelProcessor, analytic_advantage,  # noqa: E402
                        _AdaptiveScales, _RunningStandardizer)


def make_level(level=0, files=2, bytes_=None, score=None, target=1 << 26,
               overlap=0, defer=0, default_needed=False, is_last=False,
               prev_action_executed=0, bytes_in=1 << 22, bytes_out=0):
    if bytes_ is None:
        bytes_ = files * (1 << 22)
    if score is None:
        score = files / 4.0 if level == 0 else bytes_ / float(target)
    return {
        "level": level, "files": files, "bytes": bytes_, "score": score,
        "target_bytes": 0 if level == 0 else target,
        "next_level_files": 10, "next_level_bytes": 1 << 25,
        "next_level_score": 0.5, "next_level_target_bytes": 1 << 26,
        "overlap_bytes": overlap, "bytes_in": bytes_in,
        "bytes_read_out": bytes_out // 2, "bytes_written_out": bytes_out // 2,
        "compactions_from": 0, "compactions_scheduled": 0,
        "compactions_forced": 0,
        "prev_action_executed": prev_action_executed,
        "prev_action_overridden": False, "prev_compaction_picked": False,
        "defer_count": defer, "default_needed": default_needed,
        "is_last": is_last,
        "due_age_micros": defer * 50_000,
        "pressure_score_micros": max(0.0, score - 1.0) * defer * 50_000,
        "gate_open": False, "gate_mode": 0,
        "jobs_attempted": 0, "jobs_blocked": 0,
        "consecutive_blocked": 0, "in_backoff": False,
        "prev_transition_valid": True,
    }


def make_msg(levels, interval_micros=50_000, stall=0, stop=0, done=False,
             **globals_):
    msg = {
        "version": 2, "pending_compaction_bytes": 1 << 20,
        "flushed_bytes": 1 << 22, "compaction_bytes_read": 1 << 21,
        "compaction_bytes_written": 1 << 21, "compactions_completed": 1,
        "stall_count": stall, "stop_count": stop,
        "l0_compaction_trigger": 4, "l0_slowdown_trigger": 20,
        "l0_stop_trigger": 36, "l0_delay_trigger_count": 0,
        "interval_micros": interval_micros,
        "keys_read": 1000, "seeks": 50, "get_hit_l0": 300, "get_hit_l1": 400,
        "get_hit_l2_and_up": 300, "bloom_useful": 900,
        "non_last_level_read_count": 700, "last_level_read_count": 300,
        "user_logical_write_bytes": 1 << 22,
        "point_sst_probes": 3000,
        "scan_returned_entries": 1600,
        "scan_internal_skipped": 20,
        "scan_sorted_run_seeks": 100,
        "physical_sst_bytes": 1 << 28, "live_logical_bytes": 1 << 27,
        "stall_duration_micros": 0,
        "get_latency_count": 1000, "get_latency_avg_ns": 10_000,
        "get_latency_p95_ns": 20_000,
        "scan_latency_count": 50, "scan_latency_avg_ns": 30_000,
        "scan_latency_p95_ns": 60_000,
        "write_latency_count": 100, "write_latency_avg_ns": 15_000,
        "write_latency_p95_ns": 30_000,
        "done": done, "levels": levels,
    }
    msg.update(globals_)
    return msg


class ConfigOverride:
    """Temporarily set config attributes (config reads env at import time)."""

    def __init__(self, **overrides):
        self.overrides = overrides
        self.saved = {}

    def __enter__(self):
        for key, value in self.overrides.items():
            self.saved[key] = getattr(config, key)
            setattr(config, key, value)
        return self

    def __exit__(self, *exc):
        for key, value in self.saved.items():
            setattr(config, key, value)
        return False


class TestHyperparameterBudget(unittest.TestCase):
    """The defaults must fit the *measured* decision budget.

    Measured 2026-08-02 with default db_runner args: ~0.19 decisions per 1000
    ops per level agent (60k -> 13, 250k -> 50, 1M -> 185). Any default gated
    on a step count above that simply never fires. This is not hypothetical:
    MIN_REPLAY_SIZE=200 meant a 1M run performed **zero** gradient steps, and
    EXPLORATION_DECAY_STEPS=1000 left the temperature at 0.905 of its initial
    1.0 for the entire run.
    """

    DECISIONS_PER_1M = 185

    def test_training_actually_starts_on_a_1m_workload(self):
        self.assertLess(config.MIN_REPLAY_SIZE, self.DECISIONS_PER_1M,
                        "replay warmup exceeds the whole decision budget, so "
                        "training would never run")

    def test_batch_fits_the_warmup_threshold(self):
        # random.sample() raises if the buffer is smaller than the batch.
        self.assertGreaterEqual(config.MIN_REPLAY_SIZE, config.BATCH_SIZE)

    def test_exploration_anneals_within_a_1m_run(self):
        frac = min(1.0, self.DECISIONS_PER_1M / config.EXPLORATION_DECAY_STEPS)
        temperature = (config.BOLTZMANN_TEMP_START
                       + frac * (config.BOLTZMANN_TEMP_END
                                 - config.BOLTZMANN_TEMP_START))
        self.assertLess(temperature, 0.3,
                        f"temperature only reaches {temperature:.3f}; the agent "
                        "stays near-random for the whole run")

    def test_normalizer_freeze_fires_within_a_1m_run(self):
        self.assertLess(config.NORM_FREEZE_AFTER, self.DECISIONS_PER_1M,
                        "scales would drift for the entire run, making the "
                        "freeze a no-op")


class TestAnalyticPrior(unittest.TestCase):
    """Finding: a cold DQN spends the whole run exploring. The prior gives
    step-0 competence without pre-training."""

    def _globals(self):
        # Includes read telemetry: the prior now values compaction against the
        # reads that actually traverse a level, so a globals dict without it
        # exercises only the write-side terms.
        return {"l0_compaction_trigger": 4.0, "l0_slowdown_trigger": 20.0,
                "keys_read": 10000.0, "seeks": 2000.0,
                "get_hit_l0": 2000.0, "get_hit_l1": 3000.0,
                "get_hit_l2_and_up": 1000.0}

    def test_advantage_rises_with_l0_files(self):
        g = self._globals()
        low, _ = analytic_advantage(make_level(files=1), g)
        high, _ = analytic_advantage(make_level(files=8), g)
        self.assertGreater(high, low, "more L0 files must favour compacting")

    def test_overlap_discourages_premature_compaction(self):
        g = self._globals()
        base = make_level(level=1, files=4, bytes_=1 << 24, overlap=0)
        heavy = make_level(level=1, files=4, bytes_=1 << 24, overlap=1 << 26)
        adv_base, _ = analytic_advantage(base, g)
        adv_heavy, _ = analytic_advantage(heavy, g)
        self.assertLess(adv_heavy, adv_base,
                        "large next-level overlap makes merging more costly")

    def _deep_level(self, mult, overlap_ratio=0.9, target=1 << 26):
        """L1 at `mult` times its target, with a realistic file count (a fuller
        level holds proportionally more files of the same size, it does not
        hold bigger ones)."""
        b = int(target * mult)
        return make_level(level=1, files=max(1, b // (8 << 20)), bytes_=b,
                          target=target, overlap=int(b * overlap_ratio))

    def test_compacting_an_overfull_deep_level_gets_more_attractive(self):
        """Finding (2026-08-06): `work_now` priced merging the WHOLE level, but
        a deep-level compaction moves one file plus its overlaps. So the fuller
        a level got, the more expensive the prior thought it was to fix — the
        advantage FELL from +0.82 at target to +0.58 at 3x.

        Measured consequence on the 5M run: L1 parked at 1.50x target (p50),
        over target 63% of the time and reaching 7.68x, contributing 72% of the
        excess probe cost against leveled.
        """
        g = self._globals()
        advs = [analytic_advantage(self._deep_level(m), g)[0]
                for m in (1.0, 1.5, 2.0, 3.0)]
        self.assertEqual(advs, sorted(advs),
                         f"a more overfull level must be more worth "
                         f"compacting, got {advs}")

    def test_deep_merge_cost_tracks_overlap_not_level_size(self):
        """The cost of one compaction is its write amplification — bytes
        rewritten per byte of progress — which depends on the next level's
        overlap, not on how much the source level has accumulated."""
        g = self._globals()
        # Pinned: RL_PRIOR_MARGINAL_WORK=0 deliberately restores the
        # whole-level form this test exists to rule out.
        with ConfigOverride(PRIOR_MARGINAL_WORK=True):
            by_fullness = [analytic_advantage(self._deep_level(m), g)[1]["prior_work_now"]
                           for m in (1.0, 3.0, 7.7)]
            self.assertEqual(len(set(round(w, 6) for w in by_fullness)), 1,
                             f"merge cost must not grow with level size: {by_fullness}")
            by_overlap = [analytic_advantage(self._deep_level(1.5, ov), g)[1]["prior_work_now"]
                          for ov in (0.0, 3.0, 9.0)]
            self.assertEqual(by_overlap, sorted(by_overlap))
            self.assertLess(by_overlap[0], by_overlap[-1],
                            "overlap is what makes a merge expensive")

    def test_underfull_deep_level_is_still_left_alone(self):
        """The fix must not turn into 'always compact': below target there is
        little to reclaim and the merge is premature."""
        g = self._globals()
        lean = analytic_advantage(self._deep_level(0.5), g)[0]
        due = analytic_advantage(self._deep_level(1.5), g)[0]
        self.assertLess(lean, due)

    def test_advantage_is_clamped(self):
        g = self._globals()
        adv, _ = analytic_advantage(make_level(files=10_000), g)
        self.assertLessEqual(abs(adv), config.PRIOR_CLAMP)

    def _globals_with_reads(self, gets=1000, seeks=100, hit_l0=200, hit_l1=300):
        g = {k: float(v) for k, v in
             {"l0_compaction_trigger": 4, "l0_slowdown_trigger": 20}.items()}
        g.update({"keys_read": float(gets), "seeks": float(seeks),
                  "get_hit_l0": float(hit_l0), "get_hit_l1": float(hit_l1),
                  "get_hit_l2_and_up": 0.0})
        return g

    def test_read_exposure_decays_with_depth(self):
        """A lookup probes L0 always, L1 only if it missed L0, and so on."""
        g = self._globals_with_reads()
        e0 = multilevel.read_exposure(0, g)
        e1 = multilevel.read_exposure(1, g)
        e2 = multilevel.read_exposure(2, g)
        self.assertGreater(e0, e1)
        self.assertGreater(e1, e2)
        self.assertLessEqual(e0, 1.0)
        self.assertGreaterEqual(e2, 0.0)

    def test_no_reads_means_no_read_relief(self):
        """On a write-only phase, compacting buys no read amplification, so the
        prior must not claim any — otherwise it compacts for a benefit nobody
        collects."""
        g = self._globals_with_reads(gets=0, seeks=0, hit_l0=0, hit_l1=0)
        _, terms = analytic_advantage(make_level(files=8), g)
        self.assertEqual(terms["prior_read_exposure"], 0.0)
        self.assertEqual(terms["prior_readamp_relief"], 0.0)

    def test_read_traffic_makes_the_prior_favour_compacting(self):
        """The defect this fixes: the prior valued compaction identically with
        and without read traffic, so in the low-pressure regime the merge-cost
        term dominated and it deferred by default."""
        level = make_level(files=6, overlap=1 << 22)
        quiet = analytic_advantage(
            level, self._globals_with_reads(gets=0, seeks=0, hit_l0=0, hit_l1=0))[0]
        busy = analytic_advantage(
            level, self._globals_with_reads(gets=100000, seeks=20000,
                                            hit_l0=1000, hit_l1=1000))[0]
        self.assertGreater(busy, quiet,
                           "read traffic must raise the value of compacting")

    def test_l0_relief_tracks_run_count_not_fullness(self):
        """At trigger=10, two L0 files is 20% 'full' but doubles L0's probe
        cost. Relief has to follow the run count."""
        g = self._globals_with_reads(gets=10000, seeks=0, hit_l0=10000, hit_l1=0)
        one = analytic_advantage(make_level(files=1), g)[1]["prior_runs_removed"]
        three = analytic_advantage(make_level(files=3), g)[1]["prior_runs_removed"]
        self.assertGreater(three, one * 1.5)

    def test_l0_is_valued_far_above_a_deep_level_for_the_same_pressure(self):
        """Measured inversion this guards against: on the write-heavy 1M
        workload the agent compacted L1 88% of the time while holding L0 back
        at 41% — the worst way round, since every extra L0 run is probed by
        every lookup while a deeper level is one sorted run whatever its size.
        """
        g = self._globals_with_reads(gets=100000, seeks=20000,
                                     hit_l0=1000, hit_l1=1000)
        l0 = analytic_advantage(make_level(level=0, files=3), g)[0]
        l2 = analytic_advantage(
            make_level(level=2, files=3, bytes_=1 << 25), g)[0]
        self.assertGreater(l0, l2,
                           "L0 must be valued above a deep level under read load")

    def test_due_age_feature_uses_wall_clock_state(self):
        """Decision-count deferral was removed; the feature must follow time."""
        proc = MultiLevelProcessor()
        idx = config.ML_STATE_FIELDS.index("due_age_norm")
        d0 = proc.process(make_msg([make_level(level=0, defer=1)]))[0]
        self.assertGreater(float(d0.state[idx]), 0.0)
        later = make_level(level=0, defer=10)
        d1 = proc.process(make_msg([later]))[0]
        self.assertGreaterEqual(float(d1.state[idx]), float(d0.state[idx]))

    def test_zero_init_residual_means_policy_equals_prior(self):
        with ConfigOverride(ANALYTIC_PRIOR=True, EVAL_MODE=True,
                            ASYNC_TRAINING=False):
            agent = DQNAgent(state_dim=config.ML_STATE_DIM, action_dim=2,
                             save_path="/tmp/_unused.pt", name="t")
            state = np.zeros(config.ML_STATE_DIM, dtype=np.float32)
            q, residual = agent._composed_q(state, np.array([0.0, 0.7]))
            self.assertTrue(np.allclose(residual, 0.0),
                            "residual head must start at exactly zero")
            self.assertEqual(int(np.argmax(q)), 1)
            q, _ = agent._composed_q(state, np.array([0.0, -0.7]))
            self.assertEqual(int(np.argmax(q)), 0)
            agent.close()


class TestPotentialReward(unittest.TestCase):
    """Finding: eleven always-on penalties clamped to [-1,1] made the reward a
    near-constant offset that saturated where it mattered."""

    def test_all_levels_receive_one_tree_reward(self):
        proc = MultiLevelProcessor()
        decisions = proc.process(make_msg([
            make_level(level=0, files=4),
            make_level(level=1, files=2, bytes_=1 << 26),
        ]))
        self.assertEqual(len(decisions), 2)
        self.assertEqual(decisions[0].reward, decisions[1].reward)
        self.assertEqual(decisions[0].components["global_tree_cost"],
                         decisions[1].components["global_tree_cost"])

    def test_relief_is_rewarded_and_growth_penalised(self):
        with ConfigOverride(REWARD_STANDARDIZE=False, REWARD_LEGACY=False):
            proc = MultiLevelProcessor()
            proc.process(make_msg([make_level(files=8)]))
            relieved = proc.process(make_msg([make_level(files=1)]))[0]
            self.assertGreater(relieved.reward, 0.0,
                               "draining L0 must be rewarded")

            proc2 = MultiLevelProcessor()
            proc2.process(make_msg([make_level(files=1)]))
            grown = proc2.process(make_msg([make_level(files=8)]))[0]
            self.assertLess(grown.reward, 0.0, "L0 growth must be penalised")

    def test_moving_one_run_between_levels_does_not_manufacture_relief(self):
        """Source relief and output growth belong to the same tree state."""
        proc = MultiLevelProcessor()
        target = 1 << 26
        before = proc.process(make_msg([
            make_level(level=1, files=1, bytes_=target, target=target),
            make_level(level=2, files=0, bytes_=0, target=target,
                       is_last=True),
        ]))[0]
        after = proc.process(make_msg([
            make_level(level=1, files=0, bytes_=0, target=target),
        ], output_only_level_files=1, output_only_level_bytes=target,
            output_only_level_target_bytes=10 * target))[0]
        self.assertEqual(before.components["structural_probe_cost"],
                         after.components["structural_probe_cost"])
        self.assertEqual(before.components["structural_scan_cost"],
                         after.components["structural_scan_cost"])
        self.assertAlmostEqual(before.components["global_tree_cost"],
                               after.components["global_tree_cost"])

    def test_read_amp_is_priced_at_every_level_by_its_own_mechanism(self):
        """Finding (2026-08-06): deep levels were priced at read_amp = 0 on the
        grounds that one sorted run adds no probe. True for the PROBE COUNT,
        false for the read cost — a scan merges whatever the level holds inside
        its key range, so a level at 3x target makes every overlapping scan do
        3x that level's share of the work.

        Measured over 10 repeats at 5M: the RL arm carried 3.23 vs leveled's
        2.80 non-empty deep levels and 0.70 vs 0.52 L0 runs — +18% probe units
        against +27% scan latency — and only the L0 part, 28% of the excess,
        was priced.

        L0 is still ranked by RUN COUNT and deep levels by FULLNESS: the two
        mechanisms differ, which is the asymmetry worth keeping.
        """
        proc = MultiLevelProcessor()
        g = proc._parse_globals(make_msg([]))
        _, l0_terms = proc._potential(proc._parse_level(make_level(files=8)), g)
        _, l1_terms = proc._potential(
            proc._parse_level(make_level(level=1, bytes_=1 << 25)), g)
        self.assertGreater(l0_terms["read_amp"], 0.0)
        self.assertGreater(l1_terms["read_amp"], 0.0,
                           "a deep level holding data is not free to read")

    def test_deep_read_amp_has_a_gradient_rather_than_being_constant(self):
        """The failure mode this whole area keeps hitting: a term that looks
        like a read cost but never varies, so it cannot influence the policy.
        `non_last_read_fraction` sat at p50 1.00 for entire runs; an
        overshoot-only term would be identically zero at L2-L4, which measured
        0.17x/0.10x/0.03x of target and never once exceeded it.
        """
        proc = MultiLevelProcessor()
        g = proc._parse_globals(make_msg([]))
        target = 1 << 26
        values = []
        for mult in (0.1, 0.5, 1.0, 2.0, 4.0):
            raw = proc._parse_level(make_level(
                level=2, bytes_=int(target * mult), target=target))
            _, terms = proc._potential(raw, g)
            values.append(terms["read_amp"])
        self.assertEqual(len(set(values)), len(values),
                         f"read_amp must move with fullness, got {values}")
        self.assertEqual(values, sorted(values), "and move monotonically")

    def test_read_cost_vanishes_on_a_write_only_phase(self):
        """What separates read amplification from space: bytes on disk cost
        space whether or not anyone reads them, but they only cost READ time
        when reads actually traverse the level. Without this the term is just
        a second space penalty — the exact defect that made the previous
        deep-level read term meaningless."""
        proc = MultiLevelProcessor()
        busy = proc._parse_globals(make_msg([]))
        idle = proc._parse_globals(make_msg(
            [], keys_read=0, seeks=0, get_hit_l0=0, get_hit_l1=0,
            get_hit_l2_and_up=0))
        raw = proc._parse_level(make_level(level=1, bytes_=1 << 27))
        _, busy_terms = proc._potential(raw, busy)
        _, idle_terms = proc._potential(raw, idle)
        self.assertGreater(busy_terms["read_amp"], 0.0)
        self.assertEqual(idle_terms["read_amp"], 0.0)
        # ...while the space cost of the same bytes is unchanged.
        self.assertEqual(busy_terms["space_overshoot"],
                         idle_terms["space_overshoot"])

    def test_space_overshoot_grows_past_target(self):
        proc = MultiLevelProcessor()
        g = proc._parse_globals(make_msg([]))
        under = proc._parse_level(make_level(level=1, bytes_=1 << 25))
        over = proc._parse_level(make_level(level=1, bytes_=1 << 27))
        _, under_terms = proc._potential(under, g)
        _, over_terms = proc._potential(over, g)
        self.assertEqual(under_terms["space_overshoot"], 0.0)
        self.assertGreater(over_terms["space_overshoot"], 0.0)

    def test_reward_is_not_a_constant_offset(self):
        """The specific pathology of the old reward: action-dependent variation
        swamped by a fixed negative baseline."""
        with ConfigOverride(REWARD_STANDARDIZE=False, REWARD_LEGACY=False):
            proc = MultiLevelProcessor()
            rewards = []
            for files in (1, 6, 2, 9, 3, 7):
                d = proc.process(make_msg([make_level(files=files)]))[0]
                rewards.append(d.reward)
            self.assertGreater(np.std(rewards[1:]), 0.05)
            self.assertGreater(max(rewards[1:]), 0.0)
            self.assertLess(min(rewards[1:]), 0.0)

    def test_late_penalty_keys_on_stalls_not_on_rocksdb_trigger(self):
        """Keying on default_needed made the agent imitate the baseline."""
        with ConfigOverride(REWARD_STANDARDIZE=False, REWARD_LEGACY=False):
            proc = MultiLevelProcessor()
            proc.process(make_msg([make_level(files=4, default_needed=True)]))
            due = proc.process(
                make_msg([make_level(files=4, default_needed=True)]))[0]
            self.assertEqual(due.components["late_no_compaction"], 0.0)

            proc2 = MultiLevelProcessor()
            proc2.process(make_msg([make_level(files=4, defer=3)], stall=1))
            stalled = proc2.process(
                make_msg([make_level(files=4, defer=3)], stall=1))[0]
            self.assertEqual(stalled.components["late_no_compaction"], 1.0)

    def test_stall_blame_follows_deferrals(self):
        """Finding: every level got the full global stall penalty, so each
        agent's reward was dominated by something it barely influenced."""
        proc = MultiLevelProcessor()
        levels = [make_level(level=0, defer=9),
                  make_level(level=1, bytes_=1 << 25, defer=1, is_last=True)]
        shares = proc._stall_shares(
            [proc._parse_level(x) for x in levels],
            proc._parse_globals(make_msg([])))
        self.assertAlmostEqual(sum(shares.values()), 1.0, places=6)
        self.assertAlmostEqual(shares[0], 0.9, places=6)
        self.assertAlmostEqual(shares[1], 0.1, places=6)

    def test_idle_tree_blames_nobody_for_a_stall(self):
        """Found in the first end-to-end run: an all-idle tree fell back to an
        equal split, charging empty deep levels 25% of a stall they could not
        have caused."""
        proc = MultiLevelProcessor()
        levels = [make_level(level=0, files=0, bytes_=0, score=0.0),
                  make_level(level=1, files=0, bytes_=0, score=0.0,
                             is_last=True)]
        shares = proc._stall_shares(
            [proc._parse_level(x) for x in levels],
            proc._parse_globals(make_msg([])))
        self.assertEqual(set(shares.values()), {0.0})

    def test_cost_half_scales_with_the_interval_it_covers(self):
        """Finding (2026-08-06): the cost terms are rates and state quantities,
        not amounts accrued, so summing them across a credit window multiplied
        the target by however many decisions the window happened to contain.

        Measured over a 5M run: decision gaps ranged 0.050s (p10) to 0.551s
        (p99), so a fixed 4000ms window held 7 to 80 rewards. Returns averaged
        |9.46| against an analytic prior of |0.284| and the TD loss diverged on
        L0, L2 and L3.

        Same state, same cost rate, only the interval differs: the cost half of
        the reward must scale with the interval, the shaping half must not.
        """
        with ConfigOverride(REWARD_STANDARDIZE=False, REWARD_LEGACY=False):
            def sample(interval_micros):
                proc = MultiLevelProcessor()
                proc.process(make_msg([make_level(files=4, bytes_out=1 << 24)],
                                      interval_micros=interval_micros))
                return proc.process(
                    make_msg([make_level(files=4, bytes_out=1 << 24)],
                             interval_micros=interval_micros))[0]

            short = sample(50_000)     # 0.05s
            long = sample(400_000)     # 0.40s, 8x the interval
            self.assertGreater(short.components["cost_rate"], 0.0,
                               "test needs a non-zero cost to be meaningful")
            # Steady state: identical states, so no potential difference.
            self.assertAlmostEqual(short.components["shaping"],
                                   long.components["shaping"], places=6)
            # The cost half tracks the interval, so an 8x longer interval
            # accrues 8x the cost.
            self.assertAlmostEqual(
                long.components["cost_integrated"]
                / short.components["cost_integrated"], 8.0, places=4)

    def test_latency_cost_is_excess_over_manifest_budget(self):
        limits = {
            "get_latency_avg_ns_limit": 10_000.0,
            "get_latency_p95_ns_limit": 20_000.0,
            "scan_latency_avg_ns_limit": 30_000.0,
            "scan_latency_p95_ns_limit": 60_000.0,
            "write_latency_avg_ns_limit": 15_000.0,
            "write_latency_p95_ns_limit": 30_000.0,
        }
        with ConfigOverride(BASELINE_LATENCY_LIMITS=limits):
            at_budget = MultiLevelProcessor().process(
                make_msg([make_level(files=4)]))[0]
            self.assertEqual(at_budget.components["latency_budget_cost"], 0.0)
            breached = MultiLevelProcessor().process(make_msg(
                [make_level(files=4)], get_latency_avg_ns=12_000))[0]
            self.assertAlmostEqual(
                breached.components["latency_budget_cost"], 0.2)

    def test_compaction_only_window_is_not_free_in_waf_cost(self):
        proc = MultiLevelProcessor()
        proc.process(make_msg([make_level(files=4)]))
        decision = proc.process(make_msg(
            [make_level(files=4)], flushed_bytes=0,
            compaction_bytes_written=1 << 22,
            user_logical_write_bytes=0))[0]
        self.assertGreater(decision.components["write_amplification"], 0.0)

    def test_return_is_invariant_to_decision_density(self):
        """The property the dt factor exists to provide, stated end to end.

        A constant cost rate observed over the same wall-clock window must
        produce the same return whether it was sampled 5 times or 80. Before
        the fix this varied 16x (-36.6 at a 0.05s cadence, -2.3 at 0.80s),
        which is what made the regression target depend on how busy the
        database happened to be rather than on the policy.
        """
        if config.CREDIT_HORIZON_MS <= 0:
            self.skipTest(
                "count-based credit windows (RL_CREDIT_HORIZON_MS=0, the "
                "RL_N_STEP ablation) close after a fixed NUMBER of decisions, "
                "so the window's wall-clock span moves with the cadence "
                "instead of its sample count. Density invariance is not "
                "available in that mode — which is why the wall-clock horizon "
                "is the default.")
        horizon = config.CREDIT_HORIZON_MS / 1000.0
        cost_rate = 0.5

        def window_return(gap):
            total, discount, elapsed = 0.0, 1.0, 0.0
            while elapsed < horizon:
                total += discount * -(cost_rate * gap)
                discount *= config.GAMMA_PER_SEC ** gap
                elapsed += gap
            return total

        returns = [window_return(gap) for gap in (0.05, 0.1, 0.2, 0.4, 0.8)]
        spread = (max(returns) - min(returns)) / abs(np.mean(returns))
        self.assertLess(spread, 0.15,
                        f"return varies {spread:.0%} with sampling density "
                        f"alone: {returns}")

    def test_returns_stay_on_the_same_scale_as_the_analytic_prior(self):
        """Q(s,a) = b(s,a) + f_theta(s,a) is only meaningful while b and the
        regression target are commensurate. With standardization on and the
        cost half unscaled, returns averaged |9.46| against a prior clamped to
        +-2.0, so the prior contributed ~3% of Q and the residual — fitted from
        a few hundred samples — decided the policy on its own.
        """
        with ConfigOverride(REWARD_STANDARDIZE=False, REWARD_LEGACY=False):
            proc = MultiLevelProcessor()
            rewards = []
            for files in (1, 6, 2, 9, 3, 7, 4, 5):
                d = proc.process(make_msg([make_level(files=files)]))[0]
                rewards.append(abs(d.reward))
            # A single decision's reward must not dwarf the prior's clamp; the
            # credit window sums ~30-40 of these.
            self.assertLess(max(rewards), config.PRIOR_CLAMP)

    def test_legacy_reward_still_available_and_clamped(self):
        """RL_REWARD_LEGACY=1 must reproduce the old reward exactly, so the
        redesign can be ablated with a single knob. The legacy path reads
        prev-state written by advance(), so drive it the way the server does."""
        with ConfigOverride(REWARD_LEGACY=True):
            proc = MultiLevelProcessor()
            for files in (1, 30):
                d = proc.process(make_msg([make_level(files=files)]))[0]
                proc.advance(d, 0, 1 << 20)
            self.assertGreaterEqual(d.reward, -1.0)
            self.assertLessEqual(d.reward, 1.0)
            self.assertIn("score_pressure", d.components)


class TestReadPathSignals(unittest.TestCase):
    """Finding (2026-08-06): `non_last_read_fraction` measured p50 = 1.00 and
    mean 0.978 over a whole 5M run — a constant network input that also served
    as the deep-level read_pressure in the reward, turning that term into a
    second fullness penalty."""

    def test_file_reads_per_op_tracks_read_amplification(self):
        """The replacement must respond to how many files a read touches, not
        to how those files are split between level classes."""
        proc = MultiLevelProcessor()
        cheap = proc._parse_globals(make_msg(
            [], keys_read=1000, seeks=0,
            non_last_level_read_count=1000, last_level_read_count=0))
        expensive = proc._parse_globals(make_msg(
            [], keys_read=1000, seeks=0,
            non_last_level_read_count=5000, last_level_read_count=1000))
        _, cheap_ratio = proc._read_fractions(cheap)
        _, expensive_ratio = proc._read_fractions(expensive)
        self.assertAlmostEqual(cheap_ratio, 1.0, places=6)
        self.assertAlmostEqual(expensive_ratio, 6.0, places=6)

    def test_old_ratio_would_have_been_constant(self):
        """Guards the specific defect: the two workloads above are 1x and 6x
        read amplification, but the ratio between level classes cannot tell
        them apart once the last level is rarely read."""
        proc = MultiLevelProcessor()
        a = proc._parse_globals(make_msg(
            [], non_last_level_read_count=1000, last_level_read_count=0))
        b = proc._parse_globals(make_msg(
            [], non_last_level_read_count=9000, last_level_read_count=0))
        old = lambda g: (g["non_last_level_read_count"]
                         / (g["non_last_level_read_count"]
                            + g["last_level_read_count"]))
        self.assertEqual(old(a), old(b), "precondition: the old ratio is blind")
        self.assertNotEqual(*[proc._read_fractions(g)[1] for g in (a, b)])

    def test_read_amp_cost_is_charged_to_l0_by_run_count(self):
        """L0 holds overlapping runs, so probe cost is linear in file count —
        not in fullness. At trigger 10, two L0 files is 20% 'full' but doubles
        L0's probe cost."""
        with ConfigOverride(REWARD_STANDARDIZE=False, REWARD_LEGACY=False):
            def cost(files):
                proc = MultiLevelProcessor()
                proc.process(make_msg([make_level(files=files)]))
                return proc.process(
                    make_msg([make_level(files=files)]))[0].components[
                        "read_amp_cost"]
            self.assertGreater(cost(4), cost(1),
                               "more L0 runs must cost more read amplification")

    def test_deep_level_read_cost_scales_with_how_full_the_level_is(self):
        """A deep level adds no probe, but a read that reaches it merges
        whatever it holds. Charging zero here left 72% of the measured excess
        probe cost (the +0.43 non-empty deep levels against leveled) unpriced,
        while the +0.18 at L0 was the only part the reward could see."""
        with ConfigOverride(REWARD_STANDARDIZE=False, REWARD_LEGACY=False):
            def cost(bytes_):
                proc = MultiLevelProcessor()
                lvl = make_level(level=1, bytes_=bytes_, target=1 << 26)
                proc.process(make_msg([lvl]))
                return proc.process(make_msg([lvl]))[0].components["read_amp_cost"]
            lean, heavy = cost(1 << 24), cost(1 << 27)
            self.assertGreater(lean, 0.0,
                               "a populated deep level is not free to read")
            self.assertGreater(heavy, lean,
                               "holding more data must cost more read work")

    def test_read_path_audit_is_recorded(self):
        """The globals were never logged, which is why a feature pinned at 1.00
        for entire runs went unnoticed."""
        with ConfigOverride(REWARD_STANDARDIZE=False, REWARD_LEGACY=False):
            proc = MultiLevelProcessor()
            proc.process(make_msg([make_level(files=3)]))
            c = proc.process(make_msg([make_level(files=3)]))[0].components
            for key in ("read_gets", "read_seeks", "read_l0_hit_fraction",
                        "read_file_reads_per_op", "read_exposure_level"):
                self.assertIn(key, c)


class TestStateEncoding(unittest.TestCase):

    def test_64_bit_attribution_identifiers_remain_exact_in_diagnostics(self):
        identifier = (1 << 63) + 12345
        level = make_level()
        level.update({
            "prev_decision_id": identifier,
            "prev_snapshot_epoch": identifier + 1,
            "prev_completed_decision_generation": identifier + 2,
            "prev_completed_eligibility_generation": identifier + 3,
        })
        raw = MultiLevelProcessor().process(make_msg([level]))[0].raw
        self.assertEqual(raw["prev_decision_id"], identifier)
        self.assertEqual(raw["prev_snapshot_epoch"], identifier + 1)
        self.assertEqual(raw["prev_completed_decision_generation"],
                         identifier + 2)
        self.assertEqual(raw["prev_completed_eligibility_generation"],
                         identifier + 3)

    def test_64_bit_structural_generations_remain_distinguishable(self):
        identifier = (1 << 63) + 1024
        msg = make_msg([make_level(level=1, files=1, score=1.0)])
        msg["structural_source_generation"] = identifier
        msg["structural_built_generation"] = identifier + 1
        decision = MultiLevelProcessor().process(msg)[0]
        dirty_index = config.ML_STATE_FIELDS.index("structural_dirty_flag")
        self.assertEqual(decision.state[dirty_index], 1.0)

    def test_no_feature_is_dead_for_deep_levels(self):
        """Finding: two L0-only features were hardcoded to zero for L>=1."""
        proc = MultiLevelProcessor()
        d = proc.process(make_msg(
            [make_level(level=1, bytes_=1 << 27, defer=5)]))[0]
        names = config.ML_STATE_FIELDS
        idx = names.index("slowdown_pressure")
        self.assertGreater(d.state[idx], 0.0,
                           "an over-target deep level must show pressure")

    def test_score_feature_has_headroom_above_the_trigger(self):
        """Finding: score/2.0 only ever spanned [0, 0.5]."""
        proc = MultiLevelProcessor()
        idx = config.ML_STATE_FIELDS.index("score_norm")
        low = proc.process(make_msg([make_level(files=2)]))[0].state[idx]
        high = proc.process(make_msg([make_level(files=10)]))[0].state[idx]
        self.assertGreater(high, 0.5)
        self.assertGreater(high, low)
        self.assertLessEqual(high, 1.0)

    def test_rates_use_the_reported_interval(self):
        """Finding: deltas over an unbounded window were fed in as state."""
        proc_fast = MultiLevelProcessor()
        proc_slow = MultiLevelProcessor()
        idx = config.ML_STATE_FIELDS.index("arrival_rate_norm")
        level = make_level(files=2, bytes_in=1 << 24)
        fast = proc_fast.process(make_msg([level], interval_micros=10_000))[0]
        slow = proc_slow.process(make_msg([level], interval_micros=1_000_000))[0]
        # Same byte count over a 100x longer window is a 100x lower rate. Both
        # normalize to their own running max on the first sample, so compare
        # the underlying rate instead.
        self.assertEqual(proc_fast._rate(1 << 24, 0.01),
                         100 * proc_slow._rate(1 << 24, 1.0))
        self.assertTrue(np.isfinite(fast.state[idx]))
        self.assertTrue(np.isfinite(slow.state[idx]))

    def test_zero_interval_does_not_produce_infinite_rates(self):
        proc = MultiLevelProcessor()
        d = proc.process(make_msg([make_level()], interval_micros=0))[0]
        self.assertTrue(np.all(np.isfinite(d.state)))

    def test_due_level_can_always_agree_with_the_default(self):
        """The mask must not remove compact_now from a level RocksDB
        considers due, or the agent cannot even match the baseline."""
        proc = MultiLevelProcessor()
        d = proc.process(make_msg(
            [make_level(files=4, score=0.01, default_needed=True)]))[0]
        self.assertIn(1, d.valid_actions)

    def test_executed_action_is_paired_with_the_action_it_followed(self):
        """Found in the first end-to-end run: the override rate compared the
        previous decision's outcome against the action chosen in the current
        message — off by one decision, which inflated the reported rate."""
        proc = MultiLevelProcessor()
        first = proc.process(make_msg([make_level(files=3)]))[0]
        self.assertIsNone(first.executed_action)
        self.assertIsNone(first.prev_chosen_action)
        proc.advance(first, 1, 1 << 20)

        second = proc.process(
            make_msg([make_level(files=3, prev_action_executed=0)]))[0]
        self.assertEqual(second.prev_chosen_action, 1, "agent chose compact")
        self.assertEqual(second.executed_action, 0, "RocksDB did not")

    def test_empty_level_cannot_be_compacted(self):
        proc = MultiLevelProcessor()
        d = proc.process(make_msg([make_level(files=0, bytes_=0, score=0.0)]))[0]
        self.assertEqual(d.valid_actions, (0,))


class TestLevelLifecycle(unittest.TestCase):
    """RocksDB pre-creates all N levels, but an empty level (>L0) is filtered
    out of the request C++-side, so no agent exists for it and it cannot
    contribute to any other level's credit. Measured on a real 250k run: L1
    first appeared at message 5, L5 at message 25, and levels dropped in and
    out (L1 had 5 interior gaps).
    """

    def test_no_agent_is_created_for_a_level_that_never_appears(self):
        pool = multilevel.AgentPool()
        self.assertEqual(pool.levels(), [])
        pool.get(0)
        pool.get(1)
        self.assertEqual(pool.levels(), [0, 1],
                         "agents are created lazily, per level actually seen")
        pool.close_all()

    def test_absent_levels_take_no_share_of_stall_blame(self):
        """An empty level is not in the message, so it cannot dilute the blame
        assigned to the levels that are actually under pressure."""
        proc = MultiLevelProcessor()
        present = [make_level(level=0, defer=3),
                   make_level(level=1, bytes_=1 << 25, defer=1)]
        shares = proc._stall_shares(
            [proc._parse_level(x) for x in present],
            proc._parse_globals(make_msg([])))
        self.assertEqual(set(shares), {0, 1})
        self.assertAlmostEqual(sum(shares.values()), 1.0, places=6)

    def test_terminal_message_manufactures_no_reward(self):
        """The picker sends zeroed level states on shutdown (it has no
        VersionStorageInfo in its destructor). Running the potential difference
        against them read as a huge burst of relief — measured +2.109 on L1,
        above the 95th percentile of every real reward — and `done` finalises
        every open credit window, so that fiction was paid to the last
        decisions of every agent.

        Pinned at the shared process/global-reward boundary so both current and
        legacy ablations finalize without synthetic tree relief."""
        for legacy in (False, True):
            with self.subTest(legacy=legacy), ConfigOverride(
                    REWARD_LEGACY=legacy):
                proc = MultiLevelProcessor()
                for _ in range(3):
                    d = proc.process(
                        make_msg([make_level(level=1, bytes_=1 << 27)]))[0]
                    proc.advance(d, 1, 1 << 20)
                terminal = proc.process(
                    make_msg([make_level(level=1, files=0, bytes_=0,
                                         score=0.0, target=0)], done=True))[0]
                self.assertEqual(terminal.reward, 0.0)
                self.assertIn("terminal", terminal.components)

    def test_reappearing_level_is_discounted_over_the_real_gap(self):
        """A level that empties drops out of the message entirely. When it
        comes back its transition spans the whole absence, not one telemetry
        window, so the SMDP discount must use the real elapsed time."""
        proc = MultiLevelProcessor()
        first = proc.process(make_msg([make_level(level=1, bytes_=1 << 25)],
                                      interval_micros=50_000))[0]
        proc.advance(first, 0, 1 << 20)
        time.sleep(0.25)
        again = proc.process(make_msg([make_level(level=1, bytes_=1 << 25)],
                                      interval_micros=50_000))[0]
        self.assertAlmostEqual(again.dt_seconds, 0.05, places=3,
                               msg="telemetry window is still one interval")
        self.assertGreater(again.dt_discount, 0.2,
                           "discount must span the level's real absence")


class TestNormalizerFreeze(unittest.TestCase):
    """Finding: a moving encoding invalidates everything already in replay."""

    def test_scales_freeze_after_warmup(self):
        scales = _AdaptiveScales(freeze_after=3)
        for _ in range(3):
            scales.tick()
            scales.observe("k", 100.0)
        self.assertTrue(scales.frozen)
        before = scales.scales["k"]
        scales.tick()
        scales.observe("k", 10_000.0)
        self.assertEqual(scales.scales["k"], before)

    def test_standardizer_does_not_amplify_a_near_idle_level(self):
        """Found in the first end-to-end run: a level with almost no activity
        produced raw rewards around 1e-3, which the standardizer blew up to
        O(1) and would have trained that agent on pure numerical noise."""
        std = _RunningStandardizer(freeze_after=0)
        outputs = [std.apply(0.002) for _ in range(50)]
        self.assertTrue(all(abs(v) < 1.0 for v in outputs),
                        f"noise was amplified: max |r| = {max(map(abs, outputs))}")

    def test_standardizer_passes_through_until_enough_samples(self):
        std = _RunningStandardizer(freeze_after=0)
        self.assertEqual(std.apply(0.5), 0.5)

    def test_standardizer_still_scales_real_signal(self):
        std = _RunningStandardizer(freeze_after=0)
        for value in np.linspace(-2.0, 2.0, 40):
            std.apply(float(value))
        self.assertGreater(abs(std.apply(2.0)), 0.5)

    def test_standardizer_freezes(self):
        std = _RunningStandardizer(freeze_after=5)
        for value in range(5):
            std.apply(float(value))
        self.assertTrue(std.frozen)
        mean, deviation = std.mean, std.std
        std.apply(1000.0)
        self.assertEqual(std.mean, mean)
        self.assertEqual(std.std, deviation)


class TestCreditAssignment(unittest.TestCase):

    def _agent(self, **overrides):
        defaults = dict(ASYNC_TRAINING=False, EVAL_MODE=False,
                        ANALYTIC_PRIOR=False, MIN_REPLAY_SIZE=10 ** 9)
        defaults.update(overrides)
        self._override = ConfigOverride(**defaults)
        self._override.__enter__()
        return DQNAgent(state_dim=4, action_dim=2,
                        save_path="/tmp/_unused.pt", name="t")

    def tearDown(self):
        if hasattr(self, "_override"):
            self._override.__exit__(None, None, None)

    def test_wall_clock_horizon_finalises_transitions(self):
        """Finding: N_STEP=5 at 50ms looked 250ms ahead while compactions take
        seconds, so the window saw the cost but never the relief."""
        agent = self._agent(CREDIT_HORIZON_MS=30)
        state = np.zeros(4, dtype=np.float32)
        agent.observe(state, 0.0, False, dt_seconds=0.01)
        self.assertEqual(len(agent.buffer), 0)
        time.sleep(0.05)
        agent.observe(state, 1.0, False, dt_seconds=0.05)
        self.assertEqual(len(agent.buffer), 1)
        agent.close()

    def test_count_based_window_still_available(self):
        agent = self._agent(CREDIT_HORIZON_MS=0, N_STEP=1)
        state = np.zeros(4, dtype=np.float32)
        agent.observe(state, 0.0, False, dt_seconds=0.05)
        agent.observe(state, 1.0, False, dt_seconds=0.05)
        self.assertEqual(len(agent.buffer), 1)
        agent.close()

    def test_transition_is_keyed_on_the_executed_action(self):
        """Finding: safety overrides produced mislabelled samples, clustered in
        exactly the high-pressure states that matter."""
        agent = self._agent(CREDIT_HORIZON_MS=0, N_STEP=1)
        state = np.zeros(4, dtype=np.float32)
        agent.observe(state, 0.0, False, valid_actions=(0,))  # chooses 0
        # RocksDB reports that a safety guard compacted anyway.
        agent.observe(state, 1.0, False, valid_actions=(0,), executed_action=1)
        stored_action = agent.buffer._buf[0][1]
        self.assertEqual(stored_action, 1,
                         "must store what was executed, not what was chosen")
        agent.close()

    def test_invalid_interval_is_excluded_from_replay(self):
        """Fallback/stale/masked global reward cannot enter an older window."""
        agent = self._agent(CREDIT_HORIZON_MS=0, N_STEP=1)
        state = np.zeros(4, dtype=np.float32)
        agent.observe(state, 0.0, False, valid_actions=(0,))
        agent.observe(state, -100.0, False, valid_actions=(0,),
                      transition_valid=False)
        self.assertEqual(len(agent.buffer), 0)
        self.assertEqual(agent.invalid_intervals, 1)
        self.assertEqual(agent.cleared_pending_windows, 1)
        # A fresh valid decision after the invalid boundary can learn normally.
        agent.observe(state, 1.0, False, valid_actions=(0,))
        self.assertEqual(len(agent.buffer), 1)
        agent.close()

    def test_smdp_discount_is_per_second(self):
        with ConfigOverride(GAMMA_PER_SEC=0.5, GAMMA=0.99):
            self.assertAlmostEqual(DQNAgent._discount(1.0), 0.5, places=6)
            self.assertAlmostEqual(DQNAgent._discount(2.0), 0.25, places=6)
        with ConfigOverride(GAMMA_PER_SEC=0.0, GAMMA=0.99):
            self.assertAlmostEqual(DQNAgent._discount(3.0), 0.99, places=6)

    def test_flush_pending_finalises_open_windows(self):
        """Finding: run-end decisions were silently dropped."""
        agent = self._agent(CREDIT_HORIZON_MS=10 ** 6)
        state = np.zeros(4, dtype=np.float32)
        for _ in range(3):
            agent.observe(state, 0.5, False, dt_seconds=0.05)
        self.assertEqual(len(agent.buffer), 0)
        self.assertEqual(agent.flush_pending(), 3)
        self.assertEqual(agent.flush_pending(), 0)
        self.assertEqual(len(agent.buffer), 3)
        self.assertTrue(all(t[4] for t in agent.buffer._buf),
                        "flushed transitions must be terminal")
        agent.close()

    def test_done_flag_finalises_and_masks_bootstrap(self):
        agent = self._agent(CREDIT_HORIZON_MS=10 ** 6)
        state = np.zeros(4, dtype=np.float32)
        agent.observe(state, 0.5, False, dt_seconds=0.05)
        agent.observe(state, 0.5, True, dt_seconds=0.05)
        self.assertEqual(len(agent.buffer), 1)
        self.assertTrue(agent.buffer._buf[0][4])
        agent.close()


class TestSharedTrunk(unittest.TestCase):
    """Pooling exists because the sample budget is the binding constraint:
    550-615 decisions per level per 5M run, 110-120 at 1M."""

    def _trunk(self, levels=4):
        return SharedTrunk(state_dim=config.ML_STATE_DIM,
                           action_dim=config.ACTION_DIM,
                           num_levels=levels, name="test")

    def test_step_zero_policy_is_still_the_analytic_prior(self):
        """The property the whole online claim rests on: no pre-training, so
        the residual must start at exactly zero for every level."""
        with ConfigOverride(ASYNC_TRAINING=False, ANALYTIC_PRIOR=True):
            trunk = self._trunk()
            try:
                state = np.random.rand(config.ML_STATE_DIM).astype(np.float32)
                for level in range(4):
                    q = trunk.q_values(state, level)
                    np.testing.assert_allclose(q, np.zeros_like(q), atol=1e-7)
            finally:
                trunk.close()

    def test_every_level_trains_the_shared_trunk(self):
        """The point of pooling: one level's decision must move the parameters
        another level reads. With independent networks it cannot."""
        with ConfigOverride(ASYNC_TRAINING=False, ANALYTIC_PRIOR=True,
                            MIN_REPLAY_SIZE=8, BATCH_SIZE=8):
            trunk = self._trunk()
            try:
                # Only level 0 ever pushes experience.
                buf = trunk.buffer_view(0)
                rng = np.random.default_rng(0)
                for _ in range(32):
                    s = rng.random(config.ML_STATE_DIM).astype(np.float32)
                    s2 = rng.random(config.ML_STATE_DIM).astype(np.float32)
                    buf.push(s, 1, 0.5, s2, False,
                             np.array([0.0, 0.3], dtype=np.float32),
                             np.array([0.0, 0.3], dtype=np.float32), 0.9)
                probe = rng.random(config.ML_STATE_DIM).astype(np.float32)
                before = trunk.q_values(probe, 2).copy()
                for _ in range(20):
                    trunk.train_step()
                self.assertEqual(trunk.train_steps, 20)
                self.assertIsNotNone(trunk.first_train_elapsed_seconds)
                after = trunk.q_values(probe, 2)
                self.assertGreater(
                    float(np.abs(after - before).max()), 1e-6,
                    "level 2's Q did not move although the shared trunk was "
                    "trained on level 0's experience")
            finally:
                trunk.close()

    def test_quiesce_waits_for_coalesced_async_training(self):
        """A request arriving during a gradient step remains pending; the
        completion gate must not snapshot the learner between the two."""
        with ConfigOverride(
            ASYNC_TRAINING=True,
            EVAL_MODE=False,
            TRAIN_STEPS_PER_OBSERVATION=1,
        ):
            trunk = self._trunk()
            first_started = threading.Event()
            first_release = threading.Event()
            second_started = threading.Event()
            second_release = threading.Event()
            calls = 0

            def blocked_train_step():
                nonlocal calls
                calls += 1
                if calls == 1:
                    first_started.set()
                    first_release.wait(2.0)
                else:
                    second_started.set()
                    second_release.wait(2.0)

            trunk.train_step = blocked_train_step
            try:
                trunk.request_training()
                self.assertTrue(first_started.wait(1.0))
                trunk.request_training()
                first_release.set()
                self.assertTrue(second_started.wait(1.0))
                self.assertFalse(trunk.quiesce(timeout=0.05))
                second_release.set()
                self.assertTrue(trunk.quiesce(timeout=1.0))
                self.assertEqual(calls, 2)
            finally:
                first_release.set()
                second_release.set()
                trunk.close()

    def test_heads_stay_distinguishable(self):
        """Sharing the trunk must not collapse the levels onto one policy: L0
        is a different regime (overlapping runs, probed by every lookup)."""
        with ConfigOverride(ASYNC_TRAINING=False, ANALYTIC_PRIOR=True,
                            MIN_REPLAY_SIZE=8, BATCH_SIZE=8):
            trunk = self._trunk()
            try:
                rng = np.random.default_rng(1)
                # Opposite rewards for the same action at two levels.
                for level, reward in ((0, 1.0), (3, -1.0)):
                    buf = trunk.buffer_view(level)
                    for _ in range(32):
                        s = rng.random(config.ML_STATE_DIM).astype(np.float32)
                        buf.push(s, 1, reward, s, False, None, None, 0.9)
                for _ in range(100):
                    trunk.train_step()
                probe = rng.random(config.ML_STATE_DIM).astype(np.float32)
                q0, q3 = trunk.q_values(probe, 0), trunk.q_values(probe, 3)
                self.assertGreater(float(q0[1]), float(q3[1]),
                                   "heads collapsed: the level rewarded for "
                                   "compacting must value it more highly")
            finally:
                trunk.close()

    def test_pool_shares_one_buffer_and_one_trainer(self):
        with ConfigOverride(SHARED_TRUNK=True, ASYNC_TRAINING=False):
            pool = multilevel.AgentPool()
            try:
                a0, a1 = pool.get(0), pool.get(1)
                self.assertIs(a0.policy_net, a1.policy_net)
                self.assertIs(a0.optimizer, a1.optimizer)
                self.assertIsNone(a0._trainer_thread)
                self.assertIsNone(a1._trainer_thread)
                # A push through one level's view is visible to the other:
                # that is the pooled buffer.
                s = np.zeros(config.ML_STATE_DIM, dtype=np.float32)
                a0.buffer.push(s, 0, 0.0, s, False, None, None, 1.0)
                self.assertEqual(len(a1.buffer), 1)
            finally:
                pool.close_all()

    def test_independent_networks_remain_available_for_ablation(self):
        with ConfigOverride(SHARED_TRUNK=False, ASYNC_TRAINING=False):
            pool = multilevel.AgentPool()
            try:
                a0, a1 = pool.get(0), pool.get(1)
                self.assertIsNot(a0.policy_net, a1.policy_net)
                s = np.zeros(config.ML_STATE_DIM, dtype=np.float32)
                a0.buffer.push(s, 0, 0.0, s, False, None, None, 1.0)
                self.assertEqual(len(a1.buffer), 0,
                                 "unpooled levels must not share a buffer")
            finally:
                pool.close_all()

    def test_checkpoint_refuses_to_load_across_architectures(self):
        """The two layouts have different network shapes; loading one into the
        other would mis-map weights rather than fail."""
        import tempfile
        with ConfigOverride(SHARED_TRUNK=True, ASYNC_TRAINING=False):
            pool = multilevel.AgentPool()
            path = os.path.join(tempfile.mkdtemp(), "pooled.pt")
            try:
                pool.get(0).save(path)
            finally:
                pool.close_all()
        with ConfigOverride(SHARED_TRUNK=False, ASYNC_TRAINING=False):
            pool = multilevel.AgentPool()
            try:
                with self.assertRaises(ValueError):
                    pool.get(0).load(path)
            finally:
                pool.close_all()


class TestTraining(unittest.TestCase):

    def test_training_runs_with_priors_and_variable_discounts(self):
        with ConfigOverride(ASYNC_TRAINING=False, ANALYTIC_PRIOR=True,
                            MIN_REPLAY_SIZE=8, BATCH_SIZE=8,
                            CREDIT_HORIZON_MS=0, N_STEP=1, EVAL_MODE=False,
                            TRAIN_STEPS_PER_OBSERVATION=1):
            agent = DQNAgent(state_dim=4, action_dim=2,
                             save_path="/tmp/_unused.pt", name="t")
            rng = np.random.default_rng(0)
            for i in range(40):
                state = rng.random(4).astype(np.float32)
                prior = np.array([0.0, 0.3], dtype=np.float32)
                agent.observe(state, float(i % 3) - 1.0, False,
                              prior=prior, dt_seconds=0.05)
                agent.request_training()
            self.assertGreater(len(agent.buffer), 8)
            self.assertIsNotNone(agent.last_loss)
            self.assertTrue(np.isfinite(agent.last_loss))
            agent.close()

    def test_polyak_update_moves_target_gradually(self):
        with ConfigOverride(TARGET_TAU=0.5, ASYNC_TRAINING=False,
                            ANALYTIC_PRIOR=False, EVAL_MODE=False):
            agent = DQNAgent(state_dim=4, action_dim=2,
                             save_path="/tmp/_unused.pt", name="t")
            with agent._net_lock:
                for param in agent.policy_net.parameters():
                    param.data.fill_(1.0)
                for param in agent.target_net.parameters():
                    param.data.fill_(0.0)
                agent._soft_update()
                first = next(agent.target_net.parameters()).data.flatten()[0]
            self.assertAlmostEqual(float(first), 0.5, places=6)
            agent.close()

    def test_eval_mode_is_greedy_and_does_not_train(self):
        with ConfigOverride(EVAL_MODE=True, ANALYTIC_PRIOR=True,
                            ASYNC_TRAINING=False):
            agent = DQNAgent(state_dim=4, action_dim=2,
                             save_path="/tmp/_unused.pt", name="t")
            state = np.zeros(4, dtype=np.float32)
            prior = np.array([0.0, 1.0], dtype=np.float32)
            actions = {agent.select_action(state, (0, 1), prior)
                       for _ in range(20)}
            self.assertEqual(actions, {1}, "eval mode must not explore")
            agent.request_training()
            self.assertIsNone(agent.last_loss)
            agent.close()

    def test_boltzmann_exploration_is_stochastic_but_prior_weighted(self):
        with ConfigOverride(EXPLORATION="boltzmann", EVAL_MODE=False,
                            ANALYTIC_PRIOR=True, ASYNC_TRAINING=False,
                            BOLTZMANN_TEMP_START=0.2, BOLTZMANN_TEMP_END=0.2):
            agent = DQNAgent(state_dim=4, action_dim=2,
                             save_path="/tmp/_unused.pt", name="t")
            state = np.zeros(4, dtype=np.float32)
            prior = np.array([0.0, 1.0], dtype=np.float32)
            np.random.seed(0)
            picks = [agent.select_action(state, (0, 1), prior)
                     for _ in range(200)]
            share = sum(picks) / len(picks)
            self.assertGreater(share, 0.8,
                               "sampling must favour the prior's preference")
            self.assertLess(share, 1.0, "but must still explore")
            agent.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
