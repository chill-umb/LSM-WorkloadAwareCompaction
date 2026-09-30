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


def write_run(path: Path, *, fork=True, gets=57205, sst=41855600 * 2,
              useful=52968, positive=25162, probe=None, stall="00:00:0.074",
              l0_input=7989584, pending=1000, max_score=1.5):
    """A run directory in 22_check_native_parity.sh's layout; the lines are
    copied from a real db_bench 11.1.1 run."""
    path.mkdir(parents=True)
    fork_tickers = ""
    if fork:
        probe = useful + positive if probe is None else probe
        fork_tickers = (f"rocksdb.point.sst.probe COUNT : {probe}\n"
                        "rocksdb.sorted.run.seek COUNT : 28903\n")
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

    def test_too_few_pairs_is_undecided_not_passed(self):
        noisy = self.pairs(n=2)
        noisy[1][2]["write_amplification"] *= 1.03
        report = self.verdict(noisy)
        self.assertEqual(report["verdict"], "undecided")


if __name__ == "__main__":
    unittest.main()
