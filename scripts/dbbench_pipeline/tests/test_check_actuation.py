"""20_check_actuation.py (ACT-1 on the Release db_bench): parsers, the
score and pending models, and that each check fails on the defect it names."""
import copy
import importlib.util
import json
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "check_actuation", PIPELINE / "20_check_actuation.py")
act = importlib.util.module_from_spec(spec)
spec.loader.exec_module(act)

# Trimmed from a real `stats,sstables` run (db_bench 11.1.1, this geometry).
REAL_OUTPUT = """\
** Compaction Stats [default] **
Level    Files   Size     Score Read(GB)  Rn(GB) Rnp1(GB) Write(GB)
------------------------------------------------------------------
  L0      5/0    299.89 KB   1.2      0.0     0.0      0.0       0.0
  L1      2/0    236.22 KB   0.9      0.0     0.0      0.0       0.0
  L4     16/0    964.01 KB   0.0      0.0     0.0      0.0       0.0
 Sum     23/0      1.47 MB   0.0      0.0     0.0      0.0       0.0
 Int      0/0      0.00 KB   0.0      0.0     0.0      0.0       0.0

** Compaction Stats [default] **
Priority    Files   Size     Score Read(GB)  Rn(GB) Rnp1(GB) Write(GB)
------------------------------------------------------------------
 Low      0/0      0.00 KB   0.7      0.0     0.0      0.0       0.0

Estimated pending compaction bytes: 1524985

--- level 0 --- version# 2 ---
 153:19499[32648 .. 32800]['00000000000000' seq:32656, type:1 .. '0A' seq:32675, type:1](0)
 152:95409[31764 .. 32647]['00000000000000' seq:32344, type:1 .. '0A' seq:32642, type:1](0)
--- level 1 --- version# 2 ---
 109:134053[9723 .. 29971]['64CE' seq:18236, type:1 .. '6D8C' seq:21361, type:1](0)
--- level 2 --- version# 2 ---
"""

# Level sizes of a real frozen tree whose printed pending estimate was
# 1524985 and scores 1.2/0.9/0.8/0.8 (L0 had 5 files).
REAL_SIZES = [307091, 241889, 433441, 868373, 987147]


def tree(sizes, l0_files=2):
    """files per level: L0 split into l0_files equal files, one file above."""
    files = {0: [(100 + i, sizes[0] // l0_files) for i in range(l0_files)]}
    files[0][0] = (100, sizes[0] - (l0_files - 1) * (sizes[0] // l0_files))
    for level in range(1, act.LEVELS):
        files[level] = [(level, sizes[level])] if sizes[level] else []
    return files


def stats_for(files, vector):
    scored = [level for level in range(act.LEVELS) if files.get(level)]
    scores = act.rendered(act.model_scores(files, vector),
                          [level for level in scored if level < act.LEVELS - 1])
    if files.get(act.LEVELS - 1):
        scores[act.LEVELS - 1] = "0.0"
    return {"scores": scores, "pending": act.model_pending(files, vector)}


def good_observations(files):
    absent = {"stats": stats_for(files, act.ONES), "sstables": files}
    scaled = {"stats": stats_for(files, act.VECTOR), "sstables": files}
    dumped = ":".join(f"{m:f}" for m in act.VECTOR)
    return {
        "absent": absent, "ones": copy.deepcopy(absent),
        "vector": scaled, "before": copy.deepcopy(absent),
        "after": copy.deepcopy(scaled),
        "setoptions_log": "open\nSet options on column family [default] (0/1)"
                          " succeeded, updated CF options:\n"
                          f"   level_target_multipliers: {dumped}\n",
        "refusals": {name: {"exit_code": 1, "output": f"open error: {text}"}
                     for name, text in act.REFUSALS_EXPECTED.items()},
    }


class ParseTest(unittest.TestCase):
    def test_stats_reads_level_scores_not_priority_rows(self):
        stats = act.parse_stats(REAL_OUTPUT)
        self.assertEqual(stats["scores"], {0: "1.2", 1: "0.9", 4: "0.0"})
        self.assertEqual(stats["pending"], 1524985)

    def test_sstables_reads_exact_sizes_per_level(self):
        files = act.parse_sstables(REAL_OUTPUT)
        self.assertEqual(files[0], [(153, 19499), (152, 95409)])
        self.assertEqual(files[1], [(109, 134053)])
        self.assertEqual(files[2], [])

    def test_missing_instruments_raise(self):
        with self.assertRaises(ValueError):
            act.parse_stats("no table here")
        with self.assertRaises(ValueError):
            act.parse_sstables("no listing here")


class ModelTest(unittest.TestCase):
    def test_targets_scale_levels_1_up_only(self):
        base = act.BASE
        self.assertEqual(act.targets(act.ONES),
                         [base, base, 2 * base, 4 * base, 8 * base])
        self.assertEqual(act.targets(act.VECTOR),
                         [base, base // 2, 2 * base, 8 * base, 8 * base])

    def test_l0_score_is_count_or_size_whichever_is_larger(self):
        files = tree([3 * act.BASE, 0, 0, 0, 0], l0_files=2)
        self.assertEqual(act.model_scores(files, act.ONES)[0], 3.0)
        files = tree([act.BASE // 4, 0, 0, 0, 0], l0_files=6)
        self.assertEqual(act.model_scores(files, act.ONES)[0], 6 / 8)
        self.assertEqual(act.model_scores(files, act.VECTOR)[0], 6 / 8)

    def test_pending_hand_worked(self):
        # L0 300 KiB >= base: L0 and L1 pending; L1 over by 244 KiB with
        # 400 KiB below; L2 then over with nothing below.
        files = tree([307200, 204800, 409600, 0, 0])
        self.assertEqual(act.model_pending(files, act.ONES),
                         307200 + 204800 + int(249856 * 1.8))
        # L1's target halves: 380928 bytes carried instead.
        self.assertEqual(act.model_pending(files, act.VECTOR),
                         307200 + 204800 + int(380928 * 1.8))

    def test_pending_is_zero_under_every_target(self):
        files = tree([1000, act.BASE - 1, 0, 0, 0])
        self.assertEqual(act.model_pending(files, act.ONES), 0)

    def test_pending_matches_a_real_rocksdb_run(self):
        files = tree(REAL_SIZES, l0_files=5)
        self.assertEqual(act.model_pending(files, act.ONES), 1524985)
        self.assertEqual(
            act.rendered(act.model_scores(files, act.ONES), range(4)),
            {0: "1.2", 1: "0.9", 2: "0.8", 3: "0.8"})

    def test_vector_is_valid_and_moves_two_levels(self):
        self.assertEqual(act.VECTOR[0], 1.0)
        for level in range(1, act.LEVELS - 1):
            self.assertGreaterEqual(act.VECTOR[level + 1] * act.RATIO,
                                    act.VECTOR[level])
        self.assertTrue(all(0.5 <= m <= 2.0 for m in act.VECTOR))
        self.assertEqual(act.vector_arg(act.VECTOR), "1:0.5:1:2:1")


class EvaluateTest(unittest.TestCase):
    def setUp(self):
        self.files = tree(REAL_SIZES, l0_files=5)
        self.obs = good_observations(self.files)

    def failed(self, obs):
        return sorted(name for name, check in act.evaluate(obs).items()
                      if not check["passed"])

    def test_consistent_observations_pass(self):
        self.assertEqual(self.failed(self.obs), [])

    def test_l0_scaled_by_a_multiplier_fails(self):
        self.obs["vector"]["stats"]["scores"][0] = "2.3"
        self.assertIn("l0_score_invariance", self.failed(self.obs))

    def test_count_dominated_l0_is_insensitive(self):
        files = tree([act.BASE // 4, *REAL_SIZES[1:]], l0_files=7)
        self.assertEqual(self.failed(good_observations(files)),
                         ["l0_score_invariance"])

    def test_unscaled_level_score_fails(self):
        self.obs["vector"]["stats"]["scores"][1] = \
            self.obs["absent"]["stats"]["scores"][1]
        self.obs["after"] = copy.deepcopy(self.obs["vector"])
        self.assertIn("scaled_scores", self.failed(self.obs))

    def test_pending_off_by_one_byte_fails(self):
        self.obs["vector"]["stats"]["pending"] += 1
        self.obs["after"] = copy.deepcopy(self.obs["vector"])
        self.assertEqual(self.failed(self.obs), ["scaled_pending"])

    def test_setoptions_without_effect_fails(self):
        self.obs["after"] = copy.deepcopy(self.obs["before"])
        self.assertEqual(self.failed(self.obs), ["setoptions_recompute"])

    def test_setoptions_log_must_show_the_new_vector(self):
        self.obs["setoptions_log"] = "level_target_multipliers: (all 1)\n"
        self.assertEqual(self.failed(self.obs), ["setoptions_recompute"])

    def test_all_ones_differing_from_absent_fails(self):
        self.obs["ones"]["stats"]["pending"] += 1
        self.assertEqual(self.failed(self.obs), ["all_ones_equals_absent"])

    def test_tree_changing_between_runs_fails(self):
        self.obs["after"]["sstables"] = tree(REAL_SIZES, l0_files=4)
        self.assertIn("tree_frozen", self.failed(self.obs))

    def test_accepted_bad_vector_fails(self):
        self.obs["refusals"]["shrinking"] = {"exit_code": 0, "output": "OK"}
        self.assertEqual(self.failed(self.obs), ["refusals"])

    def test_refusal_for_another_reason_fails(self):
        self.obs["refusals"]["dynamic_sizing"]["output"] = \
            "open error: Invalid argument: num_levels mismatch"
        self.assertEqual(self.failed(self.obs), ["refusals"])

    def test_missing_refusal_case_fails(self):
        del self.obs["refusals"]["not_leveled"]
        self.assertEqual(self.failed(self.obs), ["refusals"])


def settle_observations(files):
    """What the two settle runs print and log when the step works."""
    log = [{"type": "header", "schema": 1, "num_levels": act.LEVELS,
            "t_us": 10, "wall_us": 20},
           {"type": "stamp", "name": "settle", "t_us": 1000150, "op": 30000,
            "h": 1500000, "ok": 1, "wait_micros": 812,
            "hold_micros": 1000137, "levels": [], "tickers": {}}]
    return {
        "absent": {"sstables": files},
        "settle_ok": {
            "exit_code": 0,
            "output": "RL_SETTLED ok=1 wait_micros=812 hold_micros=1000137\n",
            "host_log": "".join(json.dumps(r) + "\n" for r in log)},
        "settle_due": {
            "exit_code": 1,
            "output": "RL_SETTLED ok=0 wait_micros=3 hold_micros=41 "
                      "reason=compaction pending\n"},
    }


class SettleTest(unittest.TestCase):
    def setUp(self):
        self.files = tree(REAL_SIZES, l0_files=5)  # L0 due (score 1.2)
        self.obs = settle_observations(self.files)

    def failed(self, obs):
        return sorted(name for name, check in act.evaluate_settle(obs).items()
                      if not check["passed"])

    def test_working_step_passes(self):
        self.assertEqual(self.failed(self.obs), [])

    def test_failed_hold_on_the_settled_tree_fails(self):
        self.obs["settle_ok"].update(
            exit_code=1, output="RL_SETTLED ok=0 wait_micros=812 "
                                "hold_micros=400 reason=compaction pending\n")
        self.assertEqual(self.failed(self.obs), ["settle_passes"])

    def test_hold_shorter_than_asked_fails(self):
        self.obs["settle_ok"]["output"] = \
            "RL_SETTLED ok=1 wait_micros=812 hold_micros=999999\n"
        self.assertEqual(self.failed(self.obs), ["settle_passes"])

    def test_host_log_must_carry_one_ok_settle_stamp(self):
        good = self.obs["settle_ok"]["host_log"]
        for log in ("", good.splitlines()[0] + "\n",
                    good.replace('"ok": 1', '"ok": 0'), "{broken\n"):
            with self.subTest(log=log[:40]):
                self.obs["settle_ok"]["host_log"] = log
                self.assertEqual(self.failed(self.obs), ["settle_passes"])

    def test_due_tree_accepted_fails(self):
        self.obs["settle_due"].update(
            exit_code=0,
            output="RL_SETTLED ok=1 wait_micros=3 hold_micros=1000041\n")
        self.assertEqual(self.failed(self.obs), ["settle_refuses_due"])

    def test_tree_with_nothing_due_is_insensitive(self):
        files = tree([act.BASE // 4, *REAL_SIZES[1:]], l0_files=7)
        self.assertEqual(self.failed(settle_observations(files)),
                         ["settle_refuses_due"])


if __name__ == "__main__":
    unittest.main()
