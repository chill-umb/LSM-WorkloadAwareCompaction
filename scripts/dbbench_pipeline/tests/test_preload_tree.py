"""PREREGISTRATION D-27 §4: a preloaded tree's identity (tree_identity.py)
and stage 31, which builds and archives one tree per setting. The identity
covers the files a copy carries (not the info logs or the lock) and the
MANIFEST; 31 records the load's statistics, reuses a completed tree, keeps a
failed one's evidence under a new name, and refuses what D-29 §1 and
A-Impl-6 refuse, before loading anything."""
import json
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

import tree_identity

PIPELINE = Path(__file__).resolve().parents[1]

# A db_bench that "loads" a tiny database: two files, a MANIFEST, the info
# log and lock a copy does not carry, a host log with one flush, one
# compaction and the settle stamp, and levelstats' table.
STUB = """#!/usr/bin/env bash
for a in "$@"; do
  case "$a" in
    --help) exit 1 ;;
    --rl_host_log=*) log="${a#*=}" ;;
    --db=*) db="${a#*=}" ;;
    --num=*) num="${a#*=}" ;;
  esac
done
if [[ "${STUB_FAIL:-0}" == 1 ]]; then echo "IO error: no space"; exit 1; fi
mkdir -p "$db"
echo MANIFEST-000003 > "$db/CURRENT"
printf 'manifest-of-%s' "$num" > "$db/MANIFEST-000003"
head -c 4096 /dev/zero > "$db/000009.sst"
echo '[Version]' > "$db/OPTIONS-000005"
echo 'info log' > "$db/LOG"
: > "$db/LOCK"
cat > "$log" <<JSON
{"type":"header","schema":3,"num_levels":4,"fg":[],"stride":0}
{"type":"flush_begin","job":2,"fg":[]}
{"type":"flush_end","job":2,"x":${STUB_FLUSH:-1900000},"files":1,"fg":[]}
{"type":"h","cause":"flush","job":2,"op":10,"h":4096}
{"type":"job_begin","job":3,"fg":[]}
{"type":"job_end","job":3,"ok":1,"trivial":0,"fg":[]}
{"type":"job_end","job":4,"ok":1,"trivial":1,"fg":[]}
{"type":"stamp","name":"settle","op":$num,"h":4096,"stall_micros":7,"ok":${STUB_SETTLED:-1},"wait_micros":5,"hold_micros":10000000}
JSON
echo "RL_SETTLED ok=${STUB_SETTLED:-1} wait_micros=5 hold_micros=10000000"
printf 'Level Files Size(MB)\\n--------------------\\n  0        0        0\\n  1        1        3\\n'
"""


def run31(root: Path, **overrides) -> subprocess.CompletedProcess:
    build = root / "build"
    if not build.exists():
        build.mkdir(parents=True)
        (build / "db_bench").write_text(STUB)
        (build / "db_bench").chmod(0o755)
    env = {**os.environ, "CONFIRM_PRELOAD": "YES",
           "DBBENCH_BUILD_DIR": str(build), "PYTHON_VENV": str(root / "novenv"),
           "TREE_ROOT": str(root / "trees"), "SIZE_RATIOS": "4",
           "MAX_BYTES_FOR_LEVEL_BASE": "16777216", "L0_COMPACTION_TRIGGER": "4",
           "PRELOAD_KEYS": "1000", "NUM_LEVELS": "4", "DBBENCH_CPUS": "",
           "RUN_MEMORY_MAX": "", "ALLOW_CONCURRENT_RUNS": "1", **overrides}
    return subprocess.run(["bash", str(PIPELINE / "31_preload_tree.sh")],
                          env=env, capture_output=True, text=True)


def tree_dir(ran: subprocess.CompletedProcess) -> Path:
    return Path(ran.stdout.strip().splitlines()[-1].split("=", 1)[1])


class IdentityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "db"
        self.db.mkdir()
        (self.db / "CURRENT").write_text("MANIFEST-000005\n")
        (self.db / "MANIFEST-000005").write_bytes(b"m" * 64)
        (self.db / "000012.sst").write_bytes(b"x" * 100)

    def tearDown(self):
        self.tmp.cleanup()

    def test_info_logs_and_lock_are_not_part_of_it(self):
        before = tree_identity.identity(self.db)
        for name in ("LOG", "LOG.old.1697400000000000", "LOCK"):
            (self.db / name).write_text("anything")
        self.assertEqual(tree_identity.identity(self.db), before)

    def test_a_file_size_or_the_manifest_changes_it(self):
        before = tree_identity.identity(self.db)
        (self.db / "000012.sst").write_bytes(b"x" * 101)
        self.assertNotEqual(tree_identity.identity(self.db), before)
        (self.db / "000012.sst").write_bytes(b"x" * 100)
        self.assertEqual(tree_identity.identity(self.db), before)
        (self.db / "MANIFEST-000005").write_bytes(b"n" * 64)
        self.assertNotEqual(tree_identity.identity(self.db), before)

    def test_a_missing_or_extra_file_changes_it(self):
        before = tree_identity.identity(self.db)
        (self.db / "000013.sst").write_bytes(b"")
        self.assertNotEqual(tree_identity.identity(self.db), before)

    def test_refusals(self):
        (self.db / "sub").mkdir()
        with self.assertRaises(tree_identity.TreeError):
            tree_identity.identity(self.db)
        (self.db / "sub").rmdir()
        (self.db / "CURRENT").write_text("not-a-manifest\n")
        with self.assertRaises(tree_identity.TreeError):
            tree_identity.identity(self.db)

    def test_check_command(self):
        sha = tree_identity.identity(self.db)
        self.assertEqual(tree_identity.main(["check", str(self.db), sha]), 0)
        self.assertEqual(tree_identity.main(["check", str(self.db), "0" * 64]), 1)


class PreloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.ran = run31(cls.root)
        cls.tree = tree_dir(cls.ran) if cls.ran.returncode == 0 else None

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_tree_is_archived_with_its_record(self):
        self.assertEqual(self.ran.returncode, 0, self.ran.stderr)
        self.assertEqual(self.tree.name, "T4-l116-k4-n1000-s1000")
        self.assertTrue((self.tree / "COMPLETED").exists())
        # The archive carries no info log; the load's is kept beside it.
        self.assertFalse((self.tree / "db" / "LOG").exists())
        self.assertTrue((self.tree / "LOG.load").exists())
        record = json.loads((self.tree / "tree.json").read_text())
        self.assertEqual(record["identity"],
                         tree_identity.identity(self.tree / "db"))
        self.assertEqual((record["keys"], record["seed"]), (1000, 1000))
        self.assertEqual(record["options"]["size_ratio"], "4")
        self.assertEqual(record["options"]["level0_file_num_compaction_trigger"], "4")
        load = record["load"]
        self.assertEqual((load["flushes"], load["compactions"],
                          load["trivial_moves"], load["stall_micros"]),
                         (1, 1, 1, 7))
        self.assertEqual(load["largest_flush_bytes"], 1900000)
        self.assertEqual(load["largest_flush_full_buffer"], 0)
        self.assertEqual(load["levels_files_mb"], [[0, 0, 0], [1, 1, 3]])
        self.assertGreater(load["seconds"], 0)

    def test_the_load_command(self):
        command = (self.tree / "command.txt").read_text()
        for flag in ("--benchmarks=rlsuspend\\,filluniquerandom\\,settle",
                     "--num=1000", "--seed=1000", "--use_existing_db=0",
                     "--max_bytes_for_level_multiplier=4",
                     "--level0_file_num_compaction_trigger=4",
                     "--compaction_style=0", "--rl_settle_hold_seconds=10"):
            self.assertIn(flag, command)
        # Every workload shares the tree, so no workload flag shapes it.
        for flag in ("--mix_get_ratio", "--keyrange_num", "--mixgraph_decouple"):
            self.assertNotIn(flag, command)

    def test_a_completed_tree_is_reused(self):
        again = run31(self.root)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("[tree] reusing", again.stdout)
        self.assertEqual(tree_dir(again), self.tree)

    def test_a_full_buffer_flush_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            ran = run31(Path(tmp), STUB_FLUSH="2097152")
            self.assertEqual(ran.returncode, 0, ran.stderr)
            record = json.loads((tree_dir(ran) / "tree.json").read_text())
            self.assertEqual(record["load"]["largest_flush_full_buffer"], 1)

    def test_a_failed_load_keeps_its_evidence(self):
        for overrides in ({"STUB_FAIL": "1"}, {"STUB_SETTLED": "0"}):
            with self.subTest(overrides=overrides):
                with tempfile.TemporaryDirectory() as tmp:
                    ran = run31(Path(tmp), **overrides)
                    self.assertEqual(ran.returncode, 1)
                    kept = list((Path(tmp) / "trees").glob("*.failed-*"))
                    self.assertEqual(len(kept), 1, ran.stderr)
                    self.assertTrue((kept[0] / "load.out").exists())
                    self.assertFalse((kept[0] / "COMPLETED").exists())

    def test_refusals(self):
        cases = (
            ({"SIZE_RATIOS": "2 6"}, "whole numbers"),
            ({"MAX_BYTES_FOR_LEVEL_BASE": "1000000"}, "whole MiB"),
            # A-Impl-6: 16 MiB / 2 MiB buffer allows K <= 8.
            ({"L0_COMPACTION_TRIGGER": "9"}, "not admissible"),
            ({"L0_COMPACTION_TRIGGER": "20"}, "not admissible"),
            # D-29 §1: a long tree has the suite's preload and geometry.
            ({"PRELOAD_KEYS": "20000000"}, "WRITE_BUFFER_SIZE"),
            ({"PRELOAD_KEYS": "3000000"}, "PRELOAD_KEYS"),
        )
        for overrides, message in cases:
            with self.subTest(overrides=overrides):
                with tempfile.TemporaryDirectory() as tmp:
                    ran = run31(Path(tmp), **overrides)
                    self.assertEqual(ran.returncode, 1, ran.stdout)
                    self.assertIn(message, ran.stderr)
                    self.assertFalse((Path(tmp) / "trees").exists())

    def test_a_long_tree_needs_the_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            ran = run31(Path(tmp), PRELOAD_KEYS="20000000",
                        WRITE_BUFFER_SIZE="16777216", TARGET_FILE_SIZE="4194304",
                        OPEN_FILES="-1", RUN_MEMORY_MAX="32G",
                        MAX_BYTES_FOR_LEVEL_BASE="134217728",
                        PREFLIGHT_MARKER=str(Path(tmp) / "none"))
            self.assertEqual(ran.returncode, 7, ran.stderr)
            self.assertFalse((Path(tmp) / "trees").exists())


if __name__ == "__main__":
    unittest.main()
