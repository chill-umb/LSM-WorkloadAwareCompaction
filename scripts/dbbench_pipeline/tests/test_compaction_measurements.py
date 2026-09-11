import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compaction_measurements import analyze


class CompactionMeasurementsTest(unittest.TestCase):
    def release(self, job, **changes):
        return dict(event="compaction_release", job=job, release_schema_version=1,
                    release_micros=123, source_level=1, output_level=2,
                    rl_decision_id=2**63 + job, effective_action=1,
                    rl_override_reason=0, capacity_generation=0,
                    occupancy_bytes=[4, 150, 300], nominal_target_bytes=[0, 100, 400],
                    **changes)

    def measure(self, records):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "LOG"
            path.write_text("\n".join("EVENT_LOG_v1 " + json.dumps(r) for r in records))
            return analyze(path, 3)

    def test_phases_trivial_moves_and_zero_input_levels(self):
        records = []
        for job, source, read, written, drain in ((1, 0, 100, 80, False),
                                                  (2, 1, 200, 100, True)):
            records.extend([self.release(job), dict(
                event="compaction_finished", job=job, merge_schema_version=1,
                merge_success=True, source_level=source,
                merge_input_bytes=read, merge_output_bytes=written, rl_drain=drain)])
        records.extend([self.release(3), dict(event="trivial_move", job=3,
                                             total_files_size=10**9)])
        result = self.measure(records)
        self.assertTrue(result["complete"])
        self.assertEqual(result["views"]["whole_run"]["global"]["eta"], .6)
        self.assertEqual(result["views"]["workload"]["global"]["eta"], .8)
        self.assertEqual(result["views"]["drain"]["global"]["eta"], .5)
        self.assertIsNone(result["views"]["whole_run"]["levels"]["2"]["eta"])
        self.assertEqual(result["releases"][0]["phi"], [None, 1.5, .75])
        self.assertEqual(result["releases"][0]["rl_decision_id"], 2**63 + 1)

    def test_old_logs_are_not_synthetic_release_measurements(self):
        result = self.measure([dict(event="compaction_finished", job=1,
                                    total_output_size=100)])
        self.assertFalse(result["complete"])
        self.assertEqual(result["missing_release_jobs"], [1])
        self.assertEqual(result["missing_merge_measurements"], 1)

    def test_unfinished_release_fails_closed(self):
        result = self.measure([self.release(1)])
        self.assertFalse(result["complete"])
        self.assertEqual(result["unfinished_jobs"], [1])

    def test_malformed_snapshot_rejected(self):
        record = self.release(1)
        record["nominal_target_bytes"] = [0, 0, 10]
        with self.assertRaises(ValueError):
            self.measure([record])


if __name__ == "__main__":
    unittest.main()
