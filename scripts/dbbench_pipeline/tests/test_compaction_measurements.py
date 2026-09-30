"""compaction_measurements.py: per-source-level rho, o, eta, xi, rho_tilde,
t, a and dropped bytes from host-log job_end records, trivial moves excluded
from rho, o and eta (PATHWAYS §1.1, B §2 item 4)."""
import unittest

import compaction_measurements as cm


def job(job_id, level, s, o, x, trivial=0, ok=1):
    common = {"job": job_id, "start_level": level, "output_level": level + 1,
              "trivial": trivial, "s": s, "o": o, "op": job_id}
    return [{"type": "job_begin", **common},
            {"type": "job_end", **common, "x": x, "ok": ok}]


def stamp(name, bytes_written):
    return {"type": "stamp", "name": name, "op": 0,
            "tickers": {"rocksdb.bytes.written": bytes_written}}


def records(*jobs, before=(), after=()):
    out = [{"type": "header"}]
    for item in before:
        out += item
    out.append(stamp("measure_start", 1000))
    for item in jobs:
        out += item
    out.append(stamp("drain_end", 11000))
    for item in after:
        out += item
    return out


class CompactionMeasurementsTest(unittest.TestCase):
    def setUp(self):
        self.report = cm.analyze(records(
            job(11, 0, 2000, 1500, 3000), job(14, 0, 4000, 1000, 1500),
            job(20, 1, 1000, 3000, 3500), job(21, 1, 800, 0, 0, trivial=1),
            before=[job(3, 0, 7777, 0, 9999)]), num_levels=3)
        self.measured = self.report["views"]["measured"]["levels"]

    def test_merge_ratios(self):
        l0 = self.measured["0"]
        self.assertAlmostEqual(l0["rho"], (4500 - 2500) / 6000)
        self.assertAlmostEqual(l0["o"], 2500 / 6000)
        self.assertAlmostEqual(l0["eta"], 4500 / 8500)
        self.assertEqual(l0["dropped_bytes"], 6000 + 2500 - 4500)
        self.assertEqual(l0["xi"], 0.0)
        self.assertAlmostEqual(l0["rho_tilde"], l0["rho"])

    def test_trivial_moves_excluded_from_rho_and_o(self):
        l1 = self.measured["1"]
        self.assertEqual((l1["merge_jobs"], l1["trivial_jobs"]), (1, 1))
        self.assertAlmostEqual(l1["rho"], 0.5)   # (3500 - 3000) / 1000
        self.assertAlmostEqual(l1["o"], 3.0)
        self.assertAlmostEqual(l1["eta"], 3500 / 4000)
        self.assertEqual(l1["trivially_moved_bytes"], 800)
        xi = 800 / 1800
        self.assertAlmostEqual(l1["xi"], xi)
        self.assertAlmostEqual(l1["rho_tilde"], xi + (1 - xi) * 0.5)

    def test_per_user_byte(self):
        self.assertEqual(self.report["views"]["measured"]["user_bytes"], 10000)
        l1 = self.measured["1"]
        self.assertAlmostEqual(l1["a"], 1800 / 10000)
        self.assertAlmostEqual(l1["t"], 800 / 10000)

    def test_measured_view_excludes_the_load(self):
        self.assertEqual(self.measured["0"]["s_bytes"], 6000)
        whole = self.report["views"]["whole_run"]["levels"]["0"]
        self.assertEqual(whole["s_bytes"], 6000 + 7777)
        self.assertTrue(self.report["complete"])

    def test_empty_level_has_no_ratios(self):
        l2 = self.measured["2"]
        self.assertEqual((l2["rho"], l2["xi"], l2["rho_tilde"]),
                         (None, None, None))

    def test_all_trivial_level(self):
        report = cm.analyze(records(job(5, 1, 800, 0, 0, trivial=1)), 2)
        level = report["views"]["measured"]["levels"]["1"]
        self.assertEqual((level["rho"], level["xi"], level["rho_tilde"]),
                         (None, 1.0, 1.0))

    def test_jobs_after_the_drain_are_not_measured(self):
        report = cm.analyze(records(job(11, 0, 2000, 1500, 3000),
                                    after=[job(30, 0, 500, 0, 500)]), 2)
        self.assertEqual(report["views"]["measured"]["levels"]["0"]["s_bytes"], 2000)
        self.assertEqual(report["views"]["whole_run"]["levels"]["0"]["s_bytes"], 2500)

    def test_within_level_jobs_are_listed_apart(self):
        # An intra-L0 compaction moves nothing down (Lemma D.7).
        intra = job(12, 0, 900, 0, 800)
        for record in intra:
            record["output_level"] = 0
        level = cm.analyze(records(job(11, 0, 2000, 1500, 3000), intra),
                           2)["views"]["measured"]["levels"]["0"]
        self.assertEqual((level["merge_jobs"], level["s_bytes"]), (1, 2000))
        self.assertEqual((level["within_level_jobs"],
                          level["within_level_x_bytes"]), (1, 800))
        self.assertEqual(level["leaving_bytes"], 2000)

    def test_jobs_outside_the_tree_and_empty_logs_are_incomplete(self):
        outside = cm.analyze(records(job(7, 5, 10, 0, 10)), 3)
        self.assertEqual(outside["jobs_outside_tree"], [7])
        self.assertFalse(outside["complete"])
        self.assertFalse(cm.analyze(records(), 3)["complete"])

    def test_failed_and_unfinished_jobs_make_it_incomplete(self):
        failed = cm.analyze(records(job(5, 0, 10, 0, 10, ok=0)), 2)
        self.assertEqual(failed["failed_jobs"], [5])
        self.assertFalse(failed["complete"])
        self.assertEqual(failed["views"]["measured"]["levels"]["0"]["s_bytes"], 0)
        unfinished = cm.analyze(records(job(6, 0, 10, 0, 10)[:1]), 2)
        self.assertEqual(unfinished["unfinished_jobs"], [6])
        self.assertFalse(unfinished["complete"])


if __name__ == "__main__":
    unittest.main()
