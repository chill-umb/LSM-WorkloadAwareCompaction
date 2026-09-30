"""04's collect_arm on the Programme 1 measured phase (PATHWAYS D §1, OBJ-6,
Gate N0 item 5), against a synthetic run directory with a hand-worked answer:
tests/fixtures/evaluator/1M/T2/native.

The fixture's measured phase runs from the measure_start stamp (op 290,
wall 5.000e9) to drain_end (op 1000, wall 5.020e9). Inside it: flushes of
2000 B (jobs 10, 12 and the drain's 13) and compactions of 3000 B (job 11)
and 1500 B (the drain's job 14). Outside it, and never counted: the load's
7777 B flush and 9999 B compaction, and a 5555 B flush after the drain.
H is 10000 from op 290, 12000 from 400, 11500 from 500 and 13500 from 800,
so the held byte-operations are 10000*110 + 12000*100 + 11500*300 +
13500*200 = 8,450,000.
"""
import hashlib
import importlib.util
import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path

import research_objective

PIPELINE = Path(__file__).resolve().parents[1]
FIXTURE = PIPELINE / "tests" / "fixtures" / "evaluator"
spec = importlib.util.spec_from_file_location(
    "generate_graphs", PIPELINE / "04_generate_graphs.py")
graphs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(graphs)
CONTRACT, CONTRACT_SHA = research_objective.load_contract()
C_S = CONTRACT["prices"]["storage_price_per_byte_second"]


class Run:
    """A writable copy of the fixture, bound to the current contract."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(FIXTURE, self.root, dirs_exist_ok=True)
        self.dir = self.root / "1M" / "T2" / "native"
        self.edit("metadata.env", "@CONTRACT_SHA256@", CONTRACT_SHA)

    def edit(self, name, old, new):
        path = self.dir / name
        text = path.read_text()
        assert old in text, (name, old)
        path.write_text(text.replace(old, new))

    def append(self, name, line):
        with (self.dir / name).open("a") as handle:
            handle.write(line + "\n")

    def set_prices(self, text):
        """Replace prices.json, recording its hash as 03 would."""
        old = (self.dir / "prices.json").read_bytes()
        (self.dir / "prices.json").write_text(text)
        self.edit("metadata.env", hashlib.sha256(old).hexdigest(),
                  hashlib.sha256(text.encode()).hexdigest())

    def drop_host_log_lines(self, marker):
        path = self.dir / "host_log.jsonl"
        path.write_text("".join(line for line in path.read_text().splitlines(True)
                                if marker not in line))

    def close(self):
        self.tmp.cleanup()


class EvaluatorTest(unittest.TestCase):
    def setUp(self):
        self.run = Run()
        self.addCleanup(self.run.close)

    def row(self):
        return graphs.collect_arm(self.run.dir)

    def refusal(self):
        rows, refused = graphs.collect(self.run.root)
        self.assertEqual(rows, [])
        self.assertEqual(len(refused), 1)
        return refused[0]["reason"]

    def test_measured_phase_known_answer(self):
        row = self.row()
        self.assertEqual(row["measured_operations"], 710)
        self.assertEqual(row["measured_operations_host_log"], 710)
        self.assertEqual(row["held_byte_operations"], 8_450_000)
        self.assertAlmostEqual(row["measured_stall_seconds"], 2.5)
        self.assertAlmostEqual(row["mixgraph_seconds"], 10.0)
        self.assertAlmostEqual(row["stall_fraction"], 0.25)
        self.assertAlmostEqual(row["throughput_ops_per_second"], 71.0)
        self.assertEqual(row["controller_cpu_seconds"], 0.0)

    def test_write_cost_is_windowed_sst_bytes_only(self):
        row = self.row()
        # The load's flush and compaction and the post-drain flush are
        # outside the window; a compaction's own output file is not a flush.
        self.assertEqual(row["flush_bytes_written"], 6000)
        self.assertEqual(row["compaction_bytes_written"], 4500)
        self.assertEqual(row["compaction_bytes_host_log"], 4500)
        # Not the whole-run tickers (flush 17777, compaction 14499).
        self.assertEqual(row["sst_bytes_written"], 10500)
        self.assertEqual(row["user_bytes_written"], 150_000)
        self.assertAlmostEqual(row["write_amplification_measured"], 0.07)

    def test_read_counts_are_tickers_differenced_at_n_w(self):
        row = self.row()
        # Whole-run tickers are 2500, 900 and 170; the stamp at n_w has
        # 1000, 300 and 50.
        self.assertEqual((row["filter_probes"], row["block_reading_probes"],
                          row["run_seeks"]), (1500, 600, 120))

    def test_priced_costs_and_every_mode(self):
        row = self.row()
        self.assertEqual(row["objective_status"], "priced")
        costs = (1e-9 * 10500, 1e-8 * 1500 + 1e-7 * 600 + 1e-6 * 120,
                 C_S / 100 * 8_450_000)
        for name, value in zip(("C_W", "C_R", "C_S"), costs):
            self.assertAlmostEqual(row[name] / value, 1.0, places=12)
        expected = {  # (beta_W, beta_R, beta_S) by hand, PATHWAYS D §2 table
            "J_balanced_cs1": (1, 1, 1),
            "J_read_b10_cs1": (1, 10, 1), "J_read_b2_cs1": (1, 2, 1),
            "J_write_b5_cs1": (5, 1, 1), "J_space_b10_cs1": (1, 1, 10),
            "J_space_b2_cs0.5": (1, 1, 1), "J_write_b10_cs2": (10, 1, 2),
            "J_balanced_cs2": (1, 1, 2), "J_read_b5_cs0.5": (1, 5, 0.5),
        }
        for column, weights in expected.items():
            value = sum(w * c for w, c in zip(weights, costs))
            self.assertAlmostEqual(row[column] / value, 1.0, places=12,
                                   msg=column)
        # 4 modes: balanced once and 3 others at each beta*, at 3 c_s scales.
        self.assertEqual(sum(key.startswith("J_") for key in row), 30)

    def test_failed_settle_line_is_refused(self):
        self.run.edit("run.log", "RL_SETTLED ok=1", "RL_SETTLED ok=0")
        self.assertIn("settle", self.refusal())

    def test_failed_settle_stamp_is_refused(self):
        self.run.edit("host_log.jsonl", '"ok":1,"wait_micros"',
                      '"ok":0,"wait_micros"')
        self.assertIn("settle", self.refusal())

    def test_missing_settle_is_refused(self):
        self.run.edit("run.log", "RL_SETTLED ok=1 wait_micros=123 "
                      "hold_micros=10000000\n", "")
        self.assertIn("settle", self.refusal())

    def test_operation_count_self_check(self):
        # mixgraph reports one more Get than the host log's intervals serve.
        self.run.edit("run.log", "Gets:560", "Gets:561")
        self.assertIn("per-interval operations", self.refusal())

    def test_last_h_self_check(self):
        self.run.edit("host_log.jsonl",
                      '"wall_us":5020000000,"op":1000,"h":15000',
                      '"wall_us":5020000000,"op":1000,"h":15001')
        self.assertIn("H", self.refusal())

    def test_host_log_counter_sums_are_checked(self):
        self.run.edit("host_log.jsonl",
                      '"wall_us":5020000000,"op":1000,"h":15000,'
                      '"stall_micros":2500500,"levels":[[1500,',
                      '"wall_us":5020000000,"op":1000,"h":15000,'
                      '"stall_micros":2500500,"levels":[[1501,')
        self.assertIn("levels sum", self.refusal())

    def test_unpriced_arm_still_runs_the_self_checks(self):
        (self.run.dir / "prices.json").unlink()
        row = self.row()
        self.assertEqual(row["objective_status"], "no prices")
        self.assertEqual(row["held_byte_operations"], 8_450_000)
        self.assertTrue(math.isnan(row["C_W"]))
        self.assertTrue(math.isnan(row["J_read_b10_cs1"]))

    def test_missing_reference_rate_leaves_costs_unpriced(self):
        self.run.edit("metadata.env", "reference_rate=100", "reference_rate=none")
        self.assertEqual(self.row()["objective_status"], "no reference rate")

    def test_bad_prices_are_refused(self):
        good = {"c_w": 1e-9, "c_f": 1e-8, "c_blk": 1e-7, "c_sk": 1e-6}
        for key, value, text in (("c_f", 0, None), ("c_w", True, None),
                                 ("c_s", 1e-16, None),
                                 ("c_w", None, '"c_w": Infinity')):
            with self.subTest(key=key, value=value):
                run = Run()
                self.addCleanup(run.close)
                body = json.dumps({**good, key: value})
                if text:
                    body = body.replace('"c_w": null', text)
                run.set_prices(body)
                self.run = run
                self.assertIn(key, self.refusal())

    def test_prices_other_than_the_recorded_file_are_refused(self):
        (self.run.dir / "prices.json").write_text(json.dumps(
            {"c_w": 2e-9, "c_f": 1e-8, "c_blk": 1e-7, "c_sk": 1e-6}))
        self.assertIn("recorded", self.refusal())

    def test_space_term_in_the_objective(self):
        row = self.row()
        # Space priority at beta* = 10 adds 9 C_S to the balanced cost.
        self.assertAlmostEqual(
            (row["J_space_b10_cs1"] - row["J_balanced_cs1"]) / (9 * row["C_S"]),
            1.0, places=6)

    def test_run_under_another_contract_is_not_priced(self):
        self.run.edit("metadata.env", CONTRACT_SHA, "f" * 64)
        self.assertEqual(self.row()["objective_status"],
                         "run recorded another contract")

    def test_legacy_arm_is_not_scored_as_programme1(self):
        self.run.edit("metadata.env", "settle_hold_seconds=10\n", "")
        self.run.edit("run.log", "RL_SETTLED ok=1 wait_micros=123 "
                      "hold_micros=10000000\n", "")
        row = self.row()
        self.assertEqual(row["objective_status"], "not programme 1")
        self.assertTrue(math.isnan(row["held_byte_operations"]))

    def test_settle_without_its_metadata_is_refused(self):
        self.run.edit("metadata.env", "settle_hold_seconds=10\n", "")
        self.assertIn("settle_hold_seconds", self.refusal())

    def test_hold_other_than_preregistered_is_refused(self):
        self.run.edit("metadata.env", "settle_hold_seconds=10", "settle_hold_seconds=5")
        self.assertIn("preregistered", self.refusal())

    def test_short_hold_is_refused(self):
        self.run.edit("host_log.jsonl", '"hold_micros":10000000',
                      '"hold_micros":9999999')
        self.assertIn("held", self.refusal())

    def test_missing_host_log_is_refused(self):
        (self.run.dir / "host_log.jsonl").unlink()
        self.assertIn("host log", self.refusal())

    def test_missing_settle_stamp_is_refused(self):
        self.run.drop_host_log_lines('"name":"settle"')
        self.assertIn("settle", self.refusal())

    def test_missing_drain_start_stamp_is_refused(self):
        self.run.drop_host_log_lines('"name":"drain_start"')
        self.assertIn("drain_start", self.refusal())

    def test_stamps_out_of_order_are_refused(self):
        path = self.run.dir / "host_log.jsonl"
        lines = path.read_text().splitlines(True)
        settle = next(i for i, l in enumerate(lines) if '"name":"settle"' in l)
        start = next(i for i, l in enumerate(lines)
                     if '"name":"measure_start"' in l)
        lines[settle], lines[start] = lines[start], lines[settle]
        path.write_text("".join(lines))
        self.assertIn("order", self.refusal())

    def test_other_host_log_defects_are_refused(self):
        for marker in ('"type":"job_end","job":11', '"type":"header"'):
            with self.subTest(marker=marker):
                run = Run()
                self.addCleanup(run.close)
                run.drop_host_log_lines(marker)
                self.run = run
                self.assertIn("host log", self.refusal())

    def test_missing_event_log_is_refused(self):
        (self.run.dir / "rocksdb_LOG.txt").unlink()
        self.assertIn("has no event log", self.refusal())

    def test_every_host_log_flush_must_be_in_the_event_log(self):
        # A LOG missing job 12's file would price 2000 flush bytes at zero.
        path = self.run.dir / "rocksdb_LOG.txt"
        path.write_text("".join(line for line in path.read_text().splitlines(True)
                                if '"file_number": 22' not in line))
        self.assertIn("flush jobs", self.refusal())

    def test_flush_jobs_are_matched_by_id_not_count(self):
        # Same number of flushes, but the LOG names job 22 where the host
        # log has job 12.
        self.run.edit("rocksdb_LOG.txt", '"job": 12, "event": "flush_started"',
                      '"job": 22, "event": "flush_started"')
        self.run.edit("rocksdb_LOG.txt",
                      '"job": 12, "event": "table_file_creation"',
                      '"job": 22, "event": "table_file_creation"')
        self.assertIn("flush jobs", self.refusal())

    def test_a_failed_job_writes_nothing(self):
        self.run.edit("host_log.jsonl",
                      '{"type":"h","cause":"flush","job":12',
                      '{"type":"job_begin","job":17,"cf":0,"t_us":6000000,'
                      '"op":700,"start_level":0,"output_level":1,"reason":1,'
                      '"trivial":0,"s":900,"o":100,"due_since_us":0}\n'
                      '{"type":"job_end","job":17,"cf":0,"t_us":6100000,'
                      '"op":700,"start_level":0,"output_level":1,"reason":1,'
                      '"trivial":0,"s":900,"o":100,"due_since_us":0,"x":777,'
                      '"ok":0}\n{"type":"h","cause":"flush","job":12')
        self.assertEqual(self.row()["compaction_bytes_host_log"], 4500)

    def test_event_and_host_log_compaction_bytes_must_agree(self):
        # A LOG missing job 11's completion would price 3000 bytes at zero.
        self.run.edit("rocksdb_LOG.txt", '"total_output_size": 3000',
                      '"total_output_size": 2999')
        self.assertIn("compaction bytes", self.refusal())

    def test_window_starts_at_n_w_not_at_rlresume(self):
        # A flush between rlresume (4.999e9) and measure_start (5.000e9).
        self.run.append("rocksdb_LOG.txt",
                        '2026/09/30-10:00:01.9 2 EVENT_LOG_v1 {"time_micros": '
                        '4999500000, "job": 9, "event": "flush_started"}')
        self.run.append("rocksdb_LOG.txt",
                        '2026/09/30-10:00:01.9 2 EVENT_LOG_v1 {"time_micros": '
                        '4999500001, "job": 9, "event": "table_file_creation",'
                        ' "file_number": 19, "file_size": 4444}')
        self.assertEqual(self.row()["flush_bytes_written"], 6000)

    def test_only_flush_and_compaction_outputs_count(self):
        # A table file from no flush (an ingestion, say) and the WAL ticker.
        self.run.append("rocksdb_LOG.txt",
                        '2026/09/30-10:00:05.0 1 EVENT_LOG_v1 {"time_micros": '
                        '5006000000, "job": 99, "event": "table_file_creation",'
                        ' "file_number": 30, "file_size": 7000}')
        self.run.append("run.log", "rocksdb.wal.bytes COUNT : 88888")
        self.assertEqual(self.row()["sst_bytes_written"], 10500)

    def test_trivial_moves_write_nothing(self):
        self.run.edit("host_log.jsonl",
                      '{"type":"h","cause":"flush","job":12',
                      '{"type":"job_begin","job":16,"cf":0,"t_us":6000000,'
                      '"op":700,"start_level":1,"output_level":2,"reason":1,'
                      '"trivial":1,"s":900,"o":0,"due_since_us":0}\n'
                      '{"type":"job_end","job":16,"cf":0,"t_us":6100000,'
                      '"op":700,"start_level":1,"output_level":2,"reason":1,'
                      '"trivial":1,"s":900,"o":0,"due_since_us":0,"x":0,"ok":1}\n'
                      '{"type":"h","cause":"flush","job":12')
        row = self.row()
        self.assertEqual(row["compaction_bytes_host_log"], 4500)
        self.assertEqual(row["sst_bytes_written"], 10500)

    def test_stall_is_differenced_to_the_end_of_the_drain(self):
        # 0.1 s more stall at drain_end than at drain_start: counted, over
        # mixgraph's 10 s; the literal D-13 §8 fraction divides by 14 s.
        self.run.edit("host_log.jsonl", '"wall_us":5020000000,"op":1000,'
                      '"h":15000,"stall_micros":2500500',
                      '"wall_us":5020000000,"op":1000,"h":15000,'
                      '"stall_micros":2600500')
        row = self.row()
        self.assertAlmostEqual(row["measured_stall_seconds"], 2.6)
        self.assertAlmostEqual(row["stall_fraction"], 0.26)
        self.assertAlmostEqual(row["measured_phase_seconds"], 14.0)
        self.assertAlmostEqual(row["stall_fraction_measured_phase"], 2.6 / 14)

    def test_no_read_counts_in_the_drain(self):
        self.assertEqual(self.row()["drain_read_ticks"], 0)

    def test_mix_without_seeks_prints_nan_and_is_scored(self):
        # mixgraph divides by the seek count: the power-law mix prints -nan.
        self.run.edit("run.log", "Gets:560 Puts:110 Seek:40 ScanEntries:600",
                      "Gets:600 Puts:110 Seek:0 ScanEntries:0")
        self.run.edit("run.log", "956.4 value, 15.0 scan", "956.4 value, -nan scan")
        row = self.row()
        self.assertEqual(row["measured_operations"], 710)
        self.assertEqual(row["objective_status"], "priced")


if __name__ == "__main__":
    unittest.main()
