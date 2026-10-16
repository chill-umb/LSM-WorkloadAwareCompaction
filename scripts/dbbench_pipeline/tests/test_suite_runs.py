"""03 with PREREGISTRATION D-29's workload suite and D-27 §4's preloaded
trees, on test_run_experiments' stub db_bench: each of W1-W5 reaches db_bench
with its keys, mix and generator options, and the fingerprint with its
segments; a run on a tree opens a checked copy, loads nothing and is scored by
04; and 03 refuses, before any run, what D-27 and D-29 refuse."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import fingerprint as select
import preflight_marker
import tree_identity
from tests.test_run_experiments import run03, run04, summary

CELL = Path("results") / "1M" / "T2" / "native"
# The tree options run03's defaults give (NUM_LEVELS=4, SIZE_RATIOS=2).
TREE_OPTIONS = {
    "size_ratio": "2", "write_buffer_size": "2097152",
    "target_file_size_base": "524288", "max_bytes_for_level_base": "16777216",
    "num_levels": "4", "level0_file_num_compaction_trigger": "4",
    "level0_slowdown_writes_trigger": "20", "level0_stop_writes_trigger": "36",
    "compaction_pri": "3", "key_size": "64", "value_size": "960",
    "block_size": "4096", "bloom_bits": "10"}
# A systemd-run that records its arguments and runs what follows "--".
SYSTEMD_RUN = """#!/usr/bin/env bash
echo "$*" >> "$STUB_SCOPE_LOG"
while [[ "$1" != -- ]]; do shift; done
shift
exec "$@"
"""


def metadata(arm_dir: Path) -> dict:
    return dict(line.split("=", 1) for line in
                (arm_dir / "metadata.env").read_text().splitlines())


def make_tree(root: Path, keys: int = 290000, **options) -> Path:
    """A fake tree as 31 records one, built for run03's stub db_bench."""
    run03(root, EXPERIMENT_ARMS="bogus")  # creates the stub; refuses the arm
    tree = root / "tree"
    db = tree / "db"
    db.mkdir(parents=True)
    (db / "CURRENT").write_text("MANIFEST-000005\n")
    (db / "MANIFEST-000005").write_bytes(b"manifest" * 8)
    (db / "000012.sst").write_bytes(b"x" * 1000)
    (db / "OPTIONS-000007").write_text("[Version]\n")
    record = {"schema": 1, "identity": tree_identity.identity(db),
              "keys": keys, "seed": 1000,
              "options": {**TREE_OPTIONS, **options},
              "dbbench_sha256": preflight_marker.db_bench_identity(
                  root / "build" / "db_bench")}
    (tree / "tree.json").write_text(json.dumps(record))
    (tree / "COMPLETED").touch()
    return tree


class SuiteWorkloadTest(unittest.TestCase):
    """Short runs that load in their own process (the preflight's kind)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ran = {}
        for workload in ("W1", "W2", "W3", "W4", "W5"):
            root = Path(cls.tmp.name) / workload
            cls.ran[workload] = (root, run03(root, SUITE_WORKLOAD=workload))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def arm(self, workload):
        root, ran = self.ran[workload]
        self.assertEqual(ran.returncode, 0, ran.stderr)
        return root / CELL

    def options(self, workload):
        return select.parse_fingerprint_options(
            metadata(self.arm(workload))["experiment_fingerprint"])

    def test_every_workload_runs_decoupled_and_04_scores_it(self):
        for workload, (root, ran) in self.ran.items():
            with self.subTest(workload=workload):
                command = (self.arm(workload) / "command.txt").read_text()
                self.assertIn("--mixgraph_decouple_query=true", command)
                self.assertIn("--mixgraph_decouple_sizes=true", command)
                options = self.options(workload)
                self.assertEqual((options["decouple_query"],
                                  options["decouple_sizes"]), (1, 1))
                self.assertEqual(metadata(self.arm(workload))["suite_workload"],
                                 workload)
                self.assertEqual(run04(root / "results").returncode, 0)

    def test_profiles_and_families(self):
        expected = {"W1": ("w1-assoc", "assoc"),
                    "W2": ("w2-read-heavy", "powerlaw_get95"),
                    "W3": ("w3-update-heavy", "powerlaw_put90"),
                    "W4": ("w4-growth", "growth_insert90"),
                    "W5": ("w5-phased", "phased_uniform")}
        for workload, (profile, family) in expected.items():
            with self.subTest(workload=workload):
                values = metadata(self.arm(workload))
                self.assertEqual((values["workload_profile"],
                                  values["workload_family"]), (profile, family))

    def test_keys_and_mixes(self):
        w1 = (self.arm("W1") / "command.txt").read_text()
        self.assertIn("--keyrange_num=30", w1)
        self.assertIn("--mix_get_ratio=0.806", w1)
        for workload, mix in (("W2", ("0.95", "0.05")), ("W3", ("0.1", "0.9"))):
            command = (self.arm(workload) / "command.txt").read_text()
            self.assertIn("--keyrange_num=1", command)
            self.assertIn("--key_dist_a=0.002312", command)
            self.assertIn(f"--mix_get_ratio={mix[0]}", command)
            self.assertIn(f"--mix_put_ratio={mix[1]}", command)
            self.assertNotIn("keyrange_dist", command)
        for workload in ("W4", "W5"):
            command = (self.arm(workload) / "command.txt").read_text()
            self.assertIn("--keyrange_num=1", command)
            self.assertNotIn("key_dist", command)

    def test_w4_inserts(self):
        command = (self.arm("W4") / "command.txt").read_text()
        self.assertIn("--mix_insert_ratio=0.9", command)
        self.assertIn("--mix_put_ratio=0", command)
        self.assertEqual(self.options("W4")["mix_insert_ratio"], 0.9)
        self.assertEqual(self.options("W1")["mix_insert_ratio"], 0.0)

    def test_w5_phases_split_the_run_in_thirds(self):
        # 1M at 29% load: 710,000 operations, the remainder to phase 3.
        phases = "236666:0.1:0.9:0;236666:0.5:0.5:0;236668:0.9:0.1:0"
        command = (self.arm("W5") / "command.txt").read_text()
        self.assertIn("--mix_phases=" + phases.replace(";", "\\;"), command)
        self.assertEqual(self.options("W5")["mix_phases"], phases)
        self.assertEqual(metadata(self.arm("W5"))["mix_phases"], phases)
        self.assertNotIn("mix_phases", (self.arm("W1") / "command.txt").read_text())


class TreeRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.tree = make_tree(cls.root)
        cls.ran = run03(cls.root, SUITE_WORKLOAD="W1", TREE_DIR=str(cls.tree))
        cls.arm = cls.root / "results" / "1M" / "T2" / "native"
        cls.scored = run04(cls.root / "results")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_run_opens_a_checked_copy_and_loads_nothing(self):
        self.assertEqual(self.ran.returncode, 0, self.ran.stderr)
        command = (self.arm / "command.txt").read_text()
        self.assertTrue(command.startswith("bash -c "), command[:80])
        self.assertIn(f"copy-and-check {self.tree}/db ", command)
        self.assertIn("tree_identity.py", command)
        self.assertIn("--benchmarks=rlsuspend\\,settle\\,resetstats\\,rlresume"
                      "\\,mixgraph", command)
        self.assertNotIn("filluniquerandom", command)
        self.assertIn("--use_existing_db=1", command)
        self.assertIn("--num=290000", command)
        self.assertIn("--reads=1000000", command)
        values = metadata(self.arm)
        self.assertEqual(values["load_operations"], "0")
        self.assertEqual(values["mixed_operations"], "1000000")
        self.assertEqual(values["preload_keys"], "290000")
        self.assertEqual(values["preload_seed"], "1000")
        self.assertEqual(values["tree_sha256"],
                         tree_identity.identity(self.tree / "db"))

    def test_the_fingerprint_names_the_tree(self):
        options = select.parse_fingerprint_options(
            metadata(self.arm)["experiment_fingerprint"])
        self.assertEqual((options["tree_keys"], options["tree_seed"]),
                         (290000, 1000))
        self.assertEqual(options["tree_sha256"],
                         tree_identity.identity(self.tree / "db"))
        self.assertEqual((options["size_millions"], options["load_percent"]),
                         (1, 0))

    def test_04_scores_it(self):
        self.assertEqual(self.scored.returncode, 0, self.scored.stderr)
        rows = summary(self.root / "results")
        self.assertEqual(rows["native"]["settle_ok"], "1")
        # Nothing was loaded in the run, so no load bytes come off the
        # measured user bytes (D-11).
        self.assertEqual(float(rows["native"]["load_user_write_bytes"]), 0.0)

    def test_a_resumed_matrix_keeps_its_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tree = make_tree(root)
            self.assertEqual(run03(root, SUITE_WORKLOAD="W1",
                                   TREE_DIR=str(tree)).returncode, 0)
            (tree / "db" / "000013.sst").write_bytes(b"y")
            record = json.loads((tree / "tree.json").read_text())
            record["identity"] = tree_identity.identity(tree / "db")
            (tree / "tree.json").write_text(json.dumps(record))
            other = run03(root, SUITE_WORKLOAD="W1", TREE_DIR=str(tree),
                          RESUME="1")
            self.assertEqual(other.returncode, 1)
            self.assertIn("TREE_SHA256", other.stderr)
            other = run03(root, SUITE_WORKLOAD="W2", TREE_DIR=str(tree),
                          RESUME="1")
            self.assertEqual(other.returncode, 1)


class MemoryCapTest(unittest.TestCase):
    def test_copy_run_and_compaction_run_under_the_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tree = make_tree(root)
            bin_dir = root / "fakebin"
            bin_dir.mkdir()
            (bin_dir / "systemd-run").write_text(SYSTEMD_RUN)
            (bin_dir / "systemd-run").chmod(0o755)
            log = root / "scope.log"
            ran = run03(root, SUITE_WORKLOAD="W2", TREE_DIR=str(tree),
                        RUN_MEMORY_MAX="4G", STUB_SCOPE_LOG=str(log),
                        PATH=f"{bin_dir}:{os.environ['PATH']}")
            self.assertEqual(ran.returncode, 0, ran.stderr)
            command = (root / CELL / "command.txt").read_text()
            self.assertTrue(command.startswith(
                "systemd-run --user --scope -q -p MemoryMax=4G -- bash -c"))
            scopes = log.read_text().splitlines()
            # The start-up check, the run (copy, check, db_bench) and the
            # space measurement's compaction.
            self.assertEqual(len(scopes), 3, scopes)
            self.assertTrue(scopes[0].endswith("-- true"))
            self.assertIn("copy-and-check", scopes[1])
            self.assertIn("--benchmarks=compact", scopes[2])


class RefusalTest(unittest.TestCase):
    """Each refused before any run."""

    def test_refusals(self):
        long_suite = {"WORKLOAD_SIZES_M": "40", "WRITE_BUFFER_SIZE": "16777216",
                      "TARGET_FILE_SIZE": "4194304", "OPEN_FILES": "-1",
                      "RUN_MEMORY_MAX": "32G"}
        cases = (
            ({"SUITE_WORKLOAD": "W9"}, "one of W1-W5"),
            ({"SUITE_WORKLOAD": "W1", "MIX_GET_RATIO": "0.5"}, "MIX_GET_RATIO"),
            ({"SUITE_WORKLOAD": "W2", "WORKLOAD_PROFILE": "x"}, "WORKLOAD_PROFILE"),
            # D-27: a long suite run starts from a tree.
            ({"SUITE_WORKLOAD": "W1", "WORKLOAD_SIZES_M": "40"}, "TREE_DIR"),
            # Assoc's keys are not uniform: 0.465 + 0.035 + 0.5 inserts = 1.
            ({"MIX_INSERT_RATIO": "0.5", "MIX_GET_RATIO": "0.465",
              "MIX_PUT_RATIO": "0"}, "uniform keys"),
            ({"MIX_PHASE_MIXES": "1:0:0"}, "must be 0"),
            ({"MIX_PHASE_MIXES": "0.5:0.4:0", "MIX_GET_RATIO": "0",
              "MIX_PUT_RATIO": "0", "MIX_SEEK_RATIO": "0"}, "not one"),
            ({"MIXGRAPH_DECOUPLE_QUERY": "2"}, "0 or 1"),
            ({"TREE_DIR": "/nonexistent"}, "SUITE_WORKLOAD"),
        )
        for overrides, message in cases:
            with self.subTest(overrides=overrides):
                with tempfile.TemporaryDirectory() as tmp:
                    ran = run03(Path(tmp), **overrides)
                    self.assertEqual(ran.returncode, 1, ran.stdout)
                    self.assertIn(message, ran.stderr)
                    self.assertFalse((Path(tmp) / "db").exists())
                    self.assertFalse((Path(tmp) / "results").exists())
        # Runs on a tree that does not fit it, or a long suite run that does
        # not have D-29 §1's shape, or whose family the contract does not
        # list yet (D-29 §6).
        tree_cases = (
            ({}, {"SIZE_RATIOS": "3"}, "size_ratio 3"),
            ({}, {"MAX_BYTES_FOR_LEVEL_BASE": "33554432"},
             "max_bytes_for_level_base"),
            ({}, {"EXPERIMENT_ARMS": "regular"}, "old stack"),
            ({}, {"SUITE_WORKLOAD": ""}, "SUITE_WORKLOAD"),
            ({"keys": 20000000, "write_buffer_size": "16777216",
              "target_file_size_base": "4194304"},
             {**long_suite, "RUN_MEMORY_MAX": ""}, "RUN_MEMORY_MAX"),
            ({"keys": 290000, "write_buffer_size": "16777216",
              "target_file_size_base": "4194304"}, long_suite, "TREE_KEYS"),
            ({"keys": 20000000, "write_buffer_size": "16777216",
              "target_file_size_base": "4194304"}, long_suite, "D-29 §6"),
        )
        for tree_options, overrides, message in tree_cases:
            with self.subTest(overrides=overrides):
                with tempfile.TemporaryDirectory() as tmp:
                    keys = tree_options.pop("keys", 290000)
                    tree = make_tree(Path(tmp), keys=keys, **tree_options)
                    ran = run03(Path(tmp), **{"SUITE_WORKLOAD": "W1",
                                              "TREE_DIR": str(tree),
                                              **overrides})
                    self.assertEqual(ran.returncode, 1, ran.stdout)
                    self.assertIn(message, ran.stderr)
                    self.assertFalse((Path(tmp) / "db").exists())

    def test_a_changed_archive_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = make_tree(Path(tmp))
            (tree / "db" / "000012.sst").write_bytes(b"x" * 999)
            ran = run03(Path(tmp), SUITE_WORKLOAD="W1", TREE_DIR=str(tree))
            self.assertEqual(ran.returncode, 1)
            self.assertIn("not its recorded", ran.stderr)

    def test_a_long_diagnostic_suite_run_reaches_the_preflight_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tree = make_tree(root, keys=20000000, write_buffer_size="16777216",
                             target_file_size_base="4194304")
            bin_dir = root / "fakebin"
            bin_dir.mkdir()
            (bin_dir / "systemd-run").write_text(SYSTEMD_RUN)
            (bin_dir / "systemd-run").chmod(0o755)
            ran = run03(root, SUITE_WORKLOAD="W1", TREE_DIR=str(tree),
                        WORKLOAD_SIZES_M="40", WRITE_BUFFER_SIZE="16777216",
                        TARGET_FILE_SIZE="4194304", OPEN_FILES="-1",
                        RUN_MEMORY_MAX="32G", DIAGNOSTIC_RUN="1",
                        STUB_SCOPE_LOG=str(root / "scope.log"),
                        PREFLIGHT_MARKER=str(root / "none"),
                        PATH=f"{bin_dir}:{os.environ['PATH']}")
            self.assertEqual(ran.returncode, 7, ran.stderr)
            self.assertIn("without a matching preflight", ran.stderr)


if __name__ == "__main__":
    unittest.main()
