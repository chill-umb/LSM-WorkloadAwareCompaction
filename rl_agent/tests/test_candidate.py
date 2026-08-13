import json
import os
import sys
import tempfile
import unittest

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import config
from candidate import CandidateController, SLOMask
from metric_formulas import (point_read_amplification, scan_amplification,
                             sorted_run_seeks_per_scan, space_amplification,
                             write_amplification)
from model import ParametricCandidateDQN


def candidate(file_number=11, deletions=2, write_bytes=400, conflict=False,
              empties=True):
    return {
        "snapshot_epoch": 7, "source_file_number": file_number,
        "source_level": 0, "output_level": 1, "source_bytes": 100,
        "expanded_source_bytes": 200, "overlap_bytes": 300,
        "estimated_read_bytes": 500, "estimated_write_bytes": write_bytes,
        "overlap_ratio": 1.5, "num_entries": 10,
        "num_deletions": deletions, "compensated_size": 120,
        "projected_source_fullness": 0.0,
        "projected_output_fullness": .8,
        "expanded_source_files": [file_number], "overlap_files": [21],
        "empties_source_level": empties, "priority_rank": 0,
        "conflict": conflict,
    }


def message(level_candidates=None, score=1.1, **global_fields):
    result = {
        "version": 3, "snapshot_epoch": 7, "interval_micros": 100000,
        "physical_sst_bytes": 1000, "live_logical_bytes": 800,
        "levels": [{"level": 0, "files": 2, "bytes": 200,
                    "score": score, "default_needed": score >= 1,
                    "target_bytes": 0, "next_level_target_bytes": 1000,
                    "candidates": level_candidates or []}],
    }
    result.update(global_fields)
    return result


class MetricFormulaTest(unittest.TestCase):
    def test_synthetic_get_scan_write_delete_and_empty_scan(self):
        self.assertEqual(write_amplification(100, 300, 200), 2.0)
        # Four logical table probes includes two Bloom-filter negatives and is
        # independent of whether the other two hit cache.
        self.assertEqual(point_read_amplification(4, 2), 2.0)
        self.assertEqual(scan_amplification(10, 5), 1.5)
        self.assertEqual(scan_amplification(0, 20), 0.0)
        self.assertEqual(sorted_run_seeks_per_scan(12, 3), 4.0)
        self.assertEqual(space_amplification(1200, 1000), 1.2)


class ParametricModelTest(unittest.TestCase):
    def test_zero_initialized_residual(self):
        model = ParametricCandidateDQN(24, 18, 8, 4)
        output = model(torch.randn(2, 24), torch.randn(2, 4, 3, 18))
        self.assertTrue(torch.equal(output, torch.zeros_like(output)))

    def test_per_level_head_isolation(self):
        model = ParametricCandidateDQN(24, 18, 8, 3)
        with torch.no_grad():
            model.level_heads[1].weight.fill_(1.0)
        output = model(torch.randn(1, 24), torch.randn(1, 3, 2, 18))
        output[:, 1].sum().backward()
        grad0 = model.level_heads[0].weight.grad
        self.assertTrue(grad0 is None or torch.count_nonzero(grad0) == 0)
        self.assertGreater(torch.count_nonzero(model.level_heads[1].weight.grad), 0)


class CandidateControllerTest(unittest.TestCase):
    def test_variable_candidates_and_conflicts_are_masked(self):
        controller = CandidateController()
        built = controller.build_candidates(message([
            candidate(11), candidate(12, conflict=True)]))
        self.assertTrue(built.mask[0, 0])
        self.assertTrue(built.mask[0, 1])
        self.assertFalse(built.mask[0, 2])
        self.assertFalse(built.mask[1].any())

    def test_one_decision_selects_at_most_one_candidate(self):
        controller = CandidateController()
        msg = message([candidate(11)])
        msg["levels"].append({"level": 1, "files": 1, "bytes": 100,
                              "target_bytes": 100, "score": 1.1,
                              "default_needed": True,
                              "next_level_target_bytes": 1000,
                              "candidates": [{**candidate(22),
                                              "source_level": 1,
                                              "output_level": 2}]})
        response = controller.handle(msg)
        self.assertLessEqual(sum(response["actions"]), 1)
        self.assertEqual(response["snapshot_epoch"], 7)
        self.assertGreater(response["decision_id"], 0)

    def test_stale_and_fallback_transitions_are_excluded(self):
        controller = CandidateController()
        controller._previous = {"action": 1, "level": 0, "decision_id": 9,
                                "valid_by_construction": True}
        controller._previous_fallback_count = 0
        stale = message([candidate(11)])
        stale["levels"][0].update({"prev_transition_valid": False,
                                    "prev_decision_id": 9,
                                    "prev_scheduling_result": 2})
        self.assertFalse(controller._previous_transition_valid(stale))
        stale["fallback_count"] = 1
        self.assertFalse(controller._previous_transition_valid(stale))

    def test_moving_bytes_between_levels_cannot_create_tree_relief(self):
        controller = CandidateController()
        first = message([], score=.5)
        first["levels"].append({"level": 1, "files": 1, "bytes": 800,
                                "target_bytes": 1000, "candidates": []})
        second = json.loads(json.dumps(first))
        first["levels"][0]["bytes"], first["levels"][1]["bytes"] = 200, 800
        second["levels"][0]["bytes"], second["levels"][1]["bytes"] = 500, 500
        self.assertEqual(controller._tree_cost(first)[0],
                         controller._tree_cost(second)[0])

    def test_draining_candidate_receives_run_removal_credit(self):
        level = message([candidate(11)])["levels"][0]
        draining = CandidateController.analytic_prior(level, candidate(11, empties=True))
        retained = CandidateController.analytic_prior(level, candidate(11, empties=False))
        self.assertGreater(draining, retained)


class SLOMaskTest(unittest.TestCase):
    def test_three_window_hysteresis_and_minimum_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "slo.json")
            with open(path, "w", encoding="utf-8") as output:
                json.dump({"latency_limits_ns": {
                    "get": {"avg": 100, "p95": 200},
                    "scan": {"avg": 100, "p95": 200},
                    "write": {"avg": 100, "p95": 200}}}, output)
            mask = SLOMask(path)
            sample = {"get_latency_count": config.SLO_MIN_SAMPLES,
                      "get_latency_avg_ns": 103, "get_latency_p95_ns": 100}
            self.assertFalse(mask.update(sample)["read"])
            self.assertFalse(mask.update(sample)["read"])
            self.assertTrue(mask.update(sample)["read"])


if __name__ == "__main__":
    unittest.main()
