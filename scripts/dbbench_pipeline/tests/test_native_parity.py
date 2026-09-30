"""22_check_native_parity.py (ACT-4): parsing a run from instruments stock
also has, and each check failing on the defect it names."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "native_parity", PIPELINE / "22_check_native_parity.py")
act4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(act4)


def host_log(probe, positive):
    """A consistent patched-run host log (db/rl_controller_host.h records)."""
    tickers = dict(zip(act4.LEVEL_TICKERS, (probe, positive, 20000, 28903)))
    empty = {"levels": [[0, 0, 0, 0], [0, 0, 0, 0]], "tickers": {}}
    return [
        {"type": "header", "schema": 1, "num_levels": 2, "t_us": 1,
         "wall_us": 1},
        {"type": "h", "cause": "flush", "job": 2, "t_us": 2, "op": 29000,
         "h": 4000},
        {"type": "stamp", "name": "measure_start", "t_us": 3, "op": 29000,
         "h": 4000, **empty},
        {"type": "job_begin", "job": 6, "t_us": 4, "op": 50000,
         "start_level": 0, "trivial": 0, "s": 3000, "o": 0},
        {"type": "job_end", "job": 6, "t_us": 5, "op": 51000,
         "start_level": 0, "trivial": 0, "s": 3000, "o": 0, "x": 2900,
         "ok": 1},
        {"type": "h", "cause": "compaction", "job": 6, "t_us": 5,
         "op": 51000, "h": 5000},
        {"type": "stamp", "name": "drain_end", "t_us": 6, "op": 100000,
         "h": 5000, "tickers": tickers,
         "levels": [[probe - 10, positive - 5, 19990, 28000],
                    [10, 5, 10, 903]]},
    ]


def write_run(path: Path, *, fork=True, gets=57205, sst=41855600 * 2,
              useful=52968, positive=25162, probe=None, stall="00:00:0.074",
              l0_input=7989584, pending=1000, max_score=1.5, log=True):
    """A run directory in 22_check_native_parity.sh's layout; the lines are
    copied from a real db_bench 11.1.1 run. A fork run also gets a host log
    unless `log` is False; a list replaces its records."""
    path.mkdir(parents=True)
    fork_tickers = ""
    if fork:
        probe = useful + positive if probe is None else probe
        fork_tickers = (f"rocksdb.point.sst.probe COUNT : {probe}\n"
                        "rocksdb.sorted.run.seek COUNT : 28903\n")
        if log:
            records = log if isinstance(log, list) else host_log(probe, positive)
            (path / act4.HOST_LOG).write_text(
                "".join(json.dumps(r) + "\n" for r in records))
    scan = " ScanEntries:299816" if fork else ""
    (path / "stdout.txt").write_text(
        "filluniquerandom :       4.948 micros/op 202056 ops/sec 0.144 "
        "seconds 29000 operations;  197.3 MB/s\n"
        "mixgraph     :       8.390 micros/op 119180 ops/sec 0.596 seconds "
        f"71000 operations;  583.7 MB/s ( Gets:{gets} Puts:11285 Seek:2510"
        f"{scan}, reads 59715 in 59715 found, avg size: 956.4 value, "
        "119.4 scan)\n"
        f"Cumulative stall: {stall} H:M:S, 1.1 percent\n"
        "STATISTICS:\n"
        f"rocksdb.bloom.filter.useful COUNT : {useful}\n"
        f"rocksdb.bloom.filter.full.positive COUNT : {positive}\n"
        "rocksdb.number.keys.written COUNT : 40285\n"
        "rocksdb.blobdb.bytes.written COUNT : 0\n"
        "rocksdb.bytes.written COUNT : 41855600\n" + fork_tickers)
    events = [
        {"event": "table_file_creation", "job": 2, "file_size": sst // 2},
        {"event": "table_file_creation", "job": 6, "file_size": sst - sst // 2},
        {"event": "compaction_started", "job": 6, "files_L0": [11, 10],
         "input_data_size": l0_input},
        {"event": "compaction_finished", "job": 6, "output_level": 1},
    ]
    (path / "rocksdb_LOG.txt").write_text(
        "".join(f"2026/09/30-16:45:32.2 1 EVENT_LOG_v1 {json.dumps(e)}\n"
                for e in events) +
        "[default] Level summary: files[2 0 0] max score "
        f"{max_score:.2f}, estimated pending compaction bytes {pending}\n")


class ParityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def pairs(self, n=5, stock=None, patched=None):
        work = Path(tempfile.mkdtemp(dir=self.root))
        out = []
        for pair in range(1, n + 1):
            write_run(work / f"{pair}s", fork=False, **(stock or {}))
            write_run(work / f"{pair}p", **(patched or {}))
            out.append((pair, act4.collect(work / f"{pair}s"),
                        act4.collect(work / f"{pair}p")))
        return out

    def test_collect_reads_every_quantity(self):
        write_run(self.root / "r")
        run = act4.collect(self.root / "r")
        self.assertEqual(run["workload"]["gets"], 57205)
        self.assertEqual(run["workload"]["rocksdb.bytes.written"], 41855600)
        self.assertAlmostEqual(run["write_amplification"], 2.0)
        self.assertAlmostEqual(run["point_read_amplification"],
                               (52968 + 25162) / 57205)
        self.assertEqual(run["fork_point_probes"], 52968 + 25162)
        self.assertAlmostEqual(run["stall_fraction"], 0.074 / (0.144 + 0.596))
        self.assertEqual(run["mean_l0_l1_input_bytes"], 7989584)
        self.assertEqual(run["max_pending_bytes"], 1000)
        self.assertEqual(run["max_score"], 1.5)

    def test_stock_run_without_fork_tickers_parses(self):
        write_run(self.root / "s", fork=False)
        run = act4.collect(self.root / "s")
        self.assertIsNone(run["fork_point_probes"])
        self.assertIsNone(run["fork_sorted_run_seeks_per_scan"])

    def test_missing_stall_line_is_an_error(self):
        write_run(self.root / "r")
        text = (self.root / "r/stdout.txt").read_text()
        (self.root / "r/stdout.txt").write_text(
            text.replace("Cumulative stall", "Cumulative"))
        with self.assertRaises(ValueError):
            act4.collect(self.root / "r")

    def verdict(self, pairs, margin=0.02):
        return act4.evaluate(pairs, margin, 5)

    def test_identical_arms_pass(self):
        report = self.verdict(self.pairs())
        self.assertEqual(report["verdict"], "passed", report["failed_checks"])

    def test_write_amplification_drift_fails(self):
        report = self.verdict(self.pairs(patched={"sst": 41855600 * 2 * 11 // 10}))
        self.assertEqual(report["failed_checks"], ["write_amplification"])

    def test_workload_mismatch_fails(self):
        report = self.verdict(self.pairs(patched={"gets": 57204}))
        self.assertIn("paired_workload_identity", report["failed_checks"])

    def test_probe_proxy_must_equal_the_fork_counter(self):
        report = self.verdict(self.pairs(patched={"probe": 1}))
        self.assertEqual(report["failed_checks"], ["probe_identity"])

    def test_stall_margin(self):
        worse = {"stall": "00:00:0.111"}  # +5 points of 0.74 s
        self.assertEqual(self.verdict(self.pairs(patched=worse))
                         ["failed_checks"], ["stall_fraction"])
        slightly = {"stall": "00:00:0.081"}  # +0.9 points
        self.assertEqual(self.verdict(self.pairs(patched=slightly))
                         ["verdict"], "passed")

    def test_higher_pending_debt_fails_lower_passes(self):
        self.assertEqual(self.verdict(self.pairs(patched={"pending": 1100}))
                         ["failed_checks"], ["maximum_pending_debt"])
        self.assertEqual(self.verdict(self.pairs(patched={"pending": 900}))
                         ["verdict"], "passed")

    def test_score_growth_beyond_allowance_fails(self):
        report = self.verdict(self.pairs(patched={"max_score": 1.8}))
        self.assertEqual(report["failed_checks"], ["maximum_score"])

    def test_no_l0_to_l1_compaction_is_an_instrument_failure(self):
        report = self.verdict(self.pairs(
            stock={"l0_input": float("nan")}, patched={"l0_input": float("nan")}))
        self.assertEqual(report["failed_checks"], ["mean_l0_l1_input_size"])

    def test_patched_run_without_a_host_log_fails(self):
        report = self.verdict(self.pairs(patched={"log": False}))
        self.assertEqual(report["failed_checks"], ["host_log_consistency"])

    def test_inconsistent_host_log_fails(self):
        records = host_log(52968 + 25162, 25162)
        records[-1]["levels"][1][3] += 1  # a seek the ticker never saw
        report = self.verdict(self.pairs(patched={"log": records}))
        self.assertEqual(report["failed_checks"], ["host_log_consistency"])

    def test_too_few_pairs_is_undecided_not_passed(self):
        noisy = self.pairs(n=2)
        noisy[1][2]["write_amplification"] *= 1.03
        report = self.verdict(noisy)
        self.assertEqual(report["verdict"], "undecided")


class HostLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / act4.HOST_LOG

    def tearDown(self):
        self.tmp.cleanup()

    def problems(self, records):
        self.path.write_text("".join(json.dumps(r) + "\n" for r in records))
        return act4.host_log_problems(self.path)

    def test_consistent_log_has_no_problems(self):
        self.assertEqual(self.problems(host_log(100, 40)), [])

    def test_each_defect_is_named(self):
        cases = {
            "no header first": lambda r: r.pop(0),
            "no drain_end stamp": lambda r: r.pop(),
            "no measure_start stamp": lambda r: r.pop(2),
            "1 jobs began, 0 ended": lambda r: r.pop(4),
            "last H sample": lambda r: r[-1].update(h=4999),
            "operation count decreased": lambda r: r[3].update(op=60000),
            "levels sum": lambda r: r[-1]["levels"][0].__setitem__(0, 0),
        }
        for expected, edit in cases.items():
            with self.subTest(expected):
                records = host_log(100, 40)
                edit(records)
                found = self.problems(records)
                self.assertTrue(any(p.startswith(expected) for p in found),
                                found)

    def test_unparsable_log_is_a_problem(self):
        self.path.write_text("{not json\n")
        self.assertTrue(act4.host_log_problems(self.path)[0]
                        .startswith("unreadable"))


if __name__ == "__main__":
    unittest.main()
