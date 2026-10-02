"""26_record_qbar.py (PREREGISTRATION D-14 §2): q-bar is the mean throughput
of five scored native arms at T=10 per workload; every refusal; and --write
changes only the value and the amendments list, in the file's formatting."""
import contextlib
import csv
import difflib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "record_qbar", PIPELINE / "26_record_qbar.py")
record = importlib.util.module_from_spec(spec)
spec.loader.exec_module(record)
# The contract as it stood before q-bar was recorded (d07e111). The live one
# holds q-bar since 2026-10-02, and 26 rightly refuses to write it again.
CONTRACT_BEFORE_QBAR = (PIPELINE / "tests" / "fixtures" / "record_qbar"
                        / "contract_before_qbar.json")

SHA, OBJ = "a" * 64, "b" * 64
ENGINE = ("T10:k64:v960:wb2097152:sst524288:block4096:l116777216:levels13:"
          "l0-4-20-36:pri3:load5")
TAIL = ("cache8388608:bloom10:bg2:threads1:wal1:dio0:settle10:dynamic0:"
        f"soft68719476736:hard137438953472:binary{SHA}:objective{OBJ}")
FINGERPRINT = {
    "assoc": f"assoc-v1:58M:{ENGINE}:mix0.806-0.159-0.035:scan0-10000:"
             f"skew30-925.5:{TAIL}",
    "powerlaw_get95": f"powerlaw-get95-v1:58M:{ENGINE}:mix0.95-0.05-0:"
                      f"scan0-10000:pow0.002312-0.3467:{TAIL}",
}
RATES_CLAUSE = ("the two reference rates from five native arms at T=10 per "
                "workload (D-14 §2; a dated amendment), and ")
RATES = {"assoc": [100.0, 110.0, 90.0, 105.0, 95.0],
         "powerlaw_get95": [200.0, 210.0, 190.0, 205.0, 196.0]}


def rows(family):
    return [{"arm": "native", "size_ratio": "10", "size_millions": "58",
             "workload_family": family, "session_id": f"qbar-{family}",
             "dbbench_sha256": SHA, "experiment_fingerprint": FINGERPRINT[family],
             "dbbench_seed": str(seed), "settle_ok": "1",
             "objective_status": "no prices",
             "throughput_ops_per_second": repr(rate),
             "result_directory": f"/r/{family}/{seed}"}
            for seed, rate in enumerate(RATES[family], start=1)]


class RecordQbarTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.tmp = Path(folder.name)
        self.contract = self.tmp / "contract.json"
        self.original = CONTRACT_BEFORE_QBAR.read_text(encoding="utf-8")
        self.contract.write_text(self.original, encoding="utf-8")

    def admission(self, family, size=58, load=5):
        """Gate N1's reports for the workload: the rung the rows ran."""
        folder = self.tmp / family / "n1"
        folder.mkdir(parents=True, exist_ok=True)
        for t in (2, 6, 10):
            (folder / f"admission_T{t}.json").write_text(json.dumps({"run_length": {
                "level": 2, "rung": {"size_millions": size, "load_percent": load}}}))

    def summary(self, family, data=None, refused=()):
        folder = self.tmp / family
        folder.mkdir(exist_ok=True)
        if not (folder / "n1").exists():
            self.admission(family)
        data = rows(family) if data is None else data
        with (folder / "summary.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows(family)[0]))
            writer.writeheader()
            writer.writerows(data)
        (folder / "refused_arms.json").write_text(json.dumps(list(refused)))
        return f"{family}={folder / 'summary.csv'}"

    def run_tool(self, *summaries, write=False):
        argv = [a for s in summaries for a in ("--summary", s)]
        for key in dict.fromkeys(s.partition("=")[0] for s in summaries):
            argv += ["--admission", f"{key}={self.tmp / key / 'n1'}"]
        argv += ["--contract", str(self.contract), "--date", "2026-10-02"]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                record.main(argv + (["--write"] if write else []))
            finally:
                self.stderr = err.getvalue()
        return out.getvalue()

    def refused(self, *summaries, pattern):
        with self.assertRaisesRegex(SystemExit, pattern):
            self.run_tool(*summaries, write=True)
        self.assertEqual(self.contract.read_text(encoding="utf-8"), self.original)

    def test_the_mean_and_the_draft(self):
        text = self.run_tool(self.summary("assoc"), self.summary("powerlaw_get95"))
        self.assertIn("**100.0 ops/s**", text)
        self.assertIn("**200.2 ops/s**", text)
        self.assertIn("D-14 §2", text)
        # Without --write the contract is untouched.
        self.assertEqual(self.contract.read_text(encoding="utf-8"), self.original)

    def test_write_changes_only_the_values_and_the_amendments(self):
        self.run_tool(self.summary("assoc"), self.summary("powerlaw_get95"),
                      write=True)
        new = self.contract.read_text(encoding="utf-8")
        contract = json.loads(new)
        self.assertEqual(contract["reference_rate"]["ops_per_second"],
                         {"assoc": 100.0, "powerlaw_get95": 200.2})
        added = contract["amendments"][-1]
        self.assertEqual(list(added), ["date", "entry", "reason"])
        self.assertEqual((added["date"], added["entry"]),
                         ("2026-10-02", "PREREGISTRATION D-14 §2"))
        # Every other key as it was, and the file's own formatting (indent 2,
        # "§" kept as UTF-8, a trailing newline), which reproduces the frozen
        # file byte for byte.
        expected = json.loads(self.original)
        self.assertEqual(self.original, record.dump(expected))
        expected["reference_rate"]["ops_per_second"].update(
            assoc=100.0, powerlaw_get95=200.2)
        expected["amendments"].append(added)
        # status_note no longer lists the two rates as open nulls.
        expected["status_note"] = expected["status_note"].replace(RATES_CLAUSE, "")
        self.assertEqual(new, record.dump(expected))
        removed = {line[2:] for line in difflib.ndiff(
            self.original.splitlines(), new.splitlines()) if line.startswith("- ")}
        values = {'      "assoc": null,', '      "powerlaw_get95": null'}
        self.assertLessEqual(values, removed)
        note = '  "status_note": ' + json.dumps(json.loads(self.original)["status_note"],
                                              ensure_ascii=False) + ","
        self.assertLessEqual(removed, values | {"    }", note})
        # The binary is in the reason, so a later call can check it.
        self.assertIn(SHA, added["reason"])

    def test_one_workload_at_a_time(self):
        self.run_tool(self.summary("assoc"), write=True)
        contract = json.loads(self.contract.read_text())
        self.assertEqual(contract["reference_rate"]["ops_per_second"],
                         {"assoc": 100.0, "powerlaw_get95": None})
        note = json.loads(self.original)["status_note"]
        self.assertEqual(contract["status_note"], note.replace(
            RATES_CLAUSE, "the powerlaw_get95 reference rate from five native "
            "arms at T=10 (D-14 §2; a dated amendment), and "))
        self.run_tool(self.summary("powerlaw_get95"), write=True)
        contract = json.loads(self.contract.read_text())
        self.assertEqual(contract["status_note"], note.replace(RATES_CLAUSE, ""))

    def test_an_edited_status_note_is_left_alone(self):
        contract = json.loads(self.original)
        contract["status_note"] = "edited by hand"
        self.original = record.dump(contract)
        self.contract.write_text(self.original, encoding="utf-8")
        self.run_tool(self.summary("assoc"), write=True)
        self.assertEqual(json.loads(self.contract.read_text())["status_note"],
                         "edited by hand")
        self.assertIn("status_note was edited by hand", self.stderr)

    def test_a_later_workload_must_use_the_recorded_binary(self):
        self.run_tool(self.summary("assoc"), write=True)
        once = self.contract.read_text(encoding="utf-8")
        other = [{**row, "dbbench_sha256": "c" * 64,
                  "experiment_fingerprint": row["experiment_fingerprint"].replace(
                      f"binary{SHA}", "binary" + "c" * 64)}
                 for row in rows("powerlaw_get95")]
        with self.assertRaisesRegex(SystemExit, "different db_bench binaries"):
            self.run_tool(self.summary("powerlaw_get95", other), write=True)
        self.assertEqual(self.contract.read_text(encoding="utf-8"), once)

    def test_the_workloads_own_fields(self):
        cases = {
            # D-1: keyrange_num 30 (the verifier's KEYRANGE_NUM=5 case).
            ("assoc", "skew30-925.5", "skew5-925.5"): "keyrange_num 5",
            ("assoc", "skew30-925.5", "skew30-900"): "value_theta 900",
            ("assoc", ":skew30-925.5", ""): "keyrange_num 1",
            ("assoc", "mix0.806-", "mix0.9-"): "mix_get_ratio 0.9",
            ("assoc", "scan0-10000", "scan10-10000"): "scan_length 10",
            ("assoc", "skew30-925.5", "skew30-925.5:pow0.002312-0.3467"):
                "key_dist_a 0.002312",
            ("powerlaw_get95", "pow0.002312-", "pow0.002-"): "key_dist_a 0.002",
            ("powerlaw_get95", "pow0.002312-0.3467", "pow0.002312-0.4"):
                "key_dist_b 0.4",
            ("powerlaw_get95", "mix0.95-0.05-0", "mix0.9-0.1-0"): "mix_get_ratio 0.9",
            ("powerlaw_get95", "scan0-10000", "scan0-100"):
                "mix_max_scan_length 100",
            ("powerlaw_get95", "scan0-10000:", "scan0-10000:skew30-925.5:"):
                "keyrange_num 30",
        }
        for (family, old, new), pattern in cases.items():
            fp = FINGERPRINT[family]
            self.assertIn(old, fp)
            data = [{**row, "experiment_fingerprint": fp.replace(old, new, 1)}
                    for row in rows(family)]
            with self.subTest(pattern):
                self.refused(self.summary(family, data),
                             pattern=f"not the {family} workload: .*{pattern}")

    def test_config_key_fit_is_the_contracts(self):
        defaults = record.config_defaults()
        spec = json.loads(self.original)["workloads"]["powerlaw_get95"]
        self.assertEqual((float(defaults["KEY_DIST_A"]), float(defaults["KEY_DIST_B"])),
                         (spec["key_dist_a"], spec["key_dist_b"]))

    def test_arms_run_with_prices_are_refused(self):
        # D-15 §3(e): the q-bar arms run without prices, so 04 says "no prices".
        data = [{**row, "objective_status": "no reference rate"}
                for row in rows("assoc")]
        self.refused(self.summary("assoc", data), pattern="without prices")

    def test_a_recorded_value_is_never_overwritten(self):
        self.run_tool(self.summary("assoc"), write=True)
        once = self.contract.read_text(encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "already holds q-bar"):
            self.run_tool(self.summary("assoc"), write=True)
        self.assertEqual(self.contract.read_text(encoding="utf-8"), once)

    def test_nothing_is_written_when_any_workload_is_refused(self):
        bad = rows("powerlaw_get95")[:4]
        self.refused(self.summary("assoc"), self.summary("powerlaw_get95", bad),
                     pattern="powerlaw_get95: 4 rows")

    def test_refusals(self):
        def edit(**change):
            data = rows("assoc")
            data[2] = {**data[2], **change}
            return data

        def everywhere(**change):
            return [{**row, **change} for row in rows("assoc")]

        fp = FINGERPRINT["assoc"]
        cases = {
            "rows, not 5": rows("assoc")[:4],
            "not a native arm": edit(arm="static:uniform_0_75"),
            "not a native arm at T=10": edit(size_ratio="6"),
            "workload family": edit(workload_family="powerlaw_get95"),
            "not a scored": edit(objective_status="not programme 1"),
            "settle_ok": edit(settle_ok="nan"),
            "not positive": edit(throughput_ops_per_second="nan"),
            "not one session_id": edit(session_id="other"),
            "not one dbbench_sha256": edit(dbbench_sha256="c" * 64),
            "not one experiment_fingerprint": edit(
                experiment_fingerprint=fp.replace("load5", "load10")),
            "five seeds": edit(dbbench_seed="1"),
            "level0_file_num_compaction_trigger 8": everywhere(
                experiment_fingerprint=fp.replace("l0-4-", "l0-8-")),
            "max_bytes_for_level_base 8388608": everywhere(
                experiment_fingerprint=fp.replace("l116777216", "l18388608")),
            "level_target_multipliers": everywhere(
                experiment_fingerprint=fp.replace("dio0:", "dio0:ltm1x1x2:")),
            "settle_hold_seconds": everywhere(
                experiment_fingerprint=fp.replace("settle10", "settle5")),
            "not a D-16 rung": everywhere(
                experiment_fingerprint=fp.replace("load5", "load7")),
            "malformed": everywhere(experiment_fingerprint="nonsense"),
        }
        for pattern, data in cases.items():
            with self.subTest(pattern):
                self.refused(self.summary("assoc", data), pattern=pattern)
        with self.subTest("refused arm"):
            self.refused(self.summary("assoc", refused=[{"reason": "x"}]),
                         pattern="refused 1 arm")
        with self.subTest("unknown workload"):
            self.refused(self.summary("assoc").replace("assoc=", "uniform=", 1),
                         pattern="not a workload of the contract")
        with self.subTest("given twice"):
            self.refused(self.summary("assoc"), self.summary("assoc"),
                         pattern="once per workload")

    def test_the_rung_is_the_one_gate_n1_chose(self):
        # A D-16 rung, but not the longest of the workload's three cells'.
        self.admission("assoc", size=145, load=2)
        self.refused(self.summary("assoc"),
                     pattern=r"assoc: ran 58M at 5% load, but Gate N1 chose 145M at 2%")
        # Without Gate N1's reports, no rung is known.
        (self.tmp / "assoc" / "n1" / "admission_T6.json").write_text("{}")
        self.refused(self.summary("assoc"), pattern="T=6 has no run_length")
        (self.tmp / "assoc" / "n1" / "admission_T6.json").unlink()
        self.refused(self.summary("assoc"), pattern="no Gate N1 report")
        with self.assertRaises(SystemExit):  # --admission is required
            record.main(["--summary", self.summary("assoc"),
                         "--contract", str(self.contract)])

    def test_both_workloads_ran_one_binary(self):
        other = [{**row, "dbbench_sha256": "c" * 64} for row in rows("powerlaw_get95")]
        self.refused(self.summary("assoc"), self.summary("powerlaw_get95", other),
                     pattern="different db_bench binaries")

    def test_a_reformatted_contract_is_not_rewritten(self):
        self.original = json.dumps(json.loads(self.original), indent=4) + "\n"
        self.contract.write_text(self.original, encoding="utf-8")
        self.refused(self.summary("assoc"), pattern="refusing to reformat")

    def test_config_defaults_cover_every_checked_option(self):
        defaults = record.config_defaults()
        self.assertEqual(defaults["L0_COMPACTION_TRIGGER"], "4")
        self.assertEqual(defaults["MAX_BYTES_FOR_LEVEL_BASE"], "16777216")
        self.assertLessEqual(set(record.DEFAULT_OPTIONS.values()), set(defaults))


if __name__ == "__main__":
    unittest.main()
