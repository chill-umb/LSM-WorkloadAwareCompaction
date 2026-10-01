import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import preflight_marker as pm

SCRIPT = Path(pm.__file__).resolve()


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True)


class MarkerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init", "-q")
        (self.root / ".gitignore").write_text("__pycache__/\n")
        for rel in ("rl_agent/agent.py", "scripts/dbbench_pipeline/03.sh"):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("v1\n")
        git(self.root, "add", "-A")
        self.db_bench = self.root / "db_bench"
        self.db_bench.write_bytes(b"binary-1")
        self.marker = self.root / "PREFLIGHT_PASSED"

    def tearDown(self):
        self.tmp.cleanup()

    def hashes(self):
        return pm.current_hashes(self.root, self.db_bench, None)

    def write(self, passed=(1, 2, 3, 4, 5, 6), skipped=None):
        pm.write_marker(self.marker, self.hashes(), set(passed), skipped or {})

    def problems(self, arms="regular"):
        return pm.problems(pm.read_marker(self.marker), self.hashes(),
                           pm.required_steps(arms.split()))

    def test_unchanged_tree_matches(self):
        self.write()
        self.assertEqual(self.problems(), [])

    def test_absent_plugin_and_code_dir(self):
        hashes = self.hashes()
        self.assertEqual(hashes["plugin_sha256"], "absent")
        self.assertEqual(hashes["tree_sha256:controller"], "absent")

    def test_plugin_hash_is_bound_when_given(self):
        # Plan §6.4 step 7: with controller/, 13 and 03 pass --plugin.
        plugin = self.root / "librl_controller.so"
        plugin.write_bytes(b"plugin-1")
        hashes = pm.current_hashes(self.root, self.db_bench, plugin)
        self.assertEqual(hashes["plugin_sha256"], pm.file_sha256(plugin))
        pm.write_marker(self.marker, hashes, {1, 2, 3, 4}, {})
        recheck = lambda: pm.problems(
            pm.read_marker(self.marker),
            pm.current_hashes(self.root, self.db_bench, plugin), {1})
        self.assertEqual(recheck(), [])
        plugin.write_bytes(b"plugin-2")  # rebuilt
        self.assertIn("plugin_sha256", recheck()[0])
        plugin.unlink()  # deleted: "missing", never a match
        self.assertEqual(pm.current_hashes(self.root, self.db_bench,
                                           plugin)["plugin_sha256"], "missing")
        self.assertIn("plugin_sha256", recheck()[0])

    def test_a_marker_without_the_plugin_refuses_a_check_with_it(self):
        self.write()  # written without --plugin: "absent"
        plugin = self.root / "librl_controller.so"
        plugin.write_bytes(b"plugin-1")
        problems = pm.problems(
            pm.read_marker(self.marker),
            pm.current_hashes(self.root, self.db_bench, plugin), {1})
        self.assertIn("plugin_sha256", problems[0])

    def test_edited_code_is_refused(self):
        self.write()
        (self.root / "rl_agent/agent.py").write_text("v2\n")
        self.assertEqual(len(self.problems()), 1)
        self.assertIn("tree_sha256:rl_agent", self.problems()[0])

    def test_new_untracked_file_counts_but_ignored_does_not(self):
        self.write()
        cache = self.root / "rl_agent/__pycache__/agent.pyc"
        cache.parent.mkdir()
        cache.write_bytes(b"x")
        self.assertEqual(self.problems(), [])
        (self.root / "scripts/dbbench_pipeline/new.py").write_text("x\n")
        self.assertIn("tree_sha256:scripts/dbbench_pipeline",
                      self.problems()[0])

    def test_deleted_tracked_file_is_refused(self):
        self.write()
        (self.root / "rl_agent/agent.py").unlink()
        self.assertIn("tree_sha256:rl_agent", self.problems()[0])

    def test_rebuilt_db_bench_is_refused(self):
        self.write()
        self.db_bench.write_bytes(b"binary-2")
        self.assertIn("db_bench_sha256", self.problems()[0])

    def test_required_steps_by_arm(self):
        self.assertEqual(pm.required_steps(["regular", "native",
                                            "static:uniform1"]), {1, 2, 3, 4})
        self.assertEqual(pm.required_steps(["native", "rules"]),
                         {1, 2, 3, 4, 5})
        self.assertEqual(pm.required_steps(["hold"]), {1, 2, 3, 4, 5})
        self.assertEqual(pm.required_steps(["learned"]),
                         {1, 2, 3, 4, 5, 6})
        # Old or unknown arms get the strictest set.
        self.assertEqual(pm.required_steps(["oracle"]), {1, 2, 3, 4, 5, 6})

    def test_skipped_step_refuses_a_run_that_needs_it(self):
        self.write(passed=(1, 2, 3, 4), skipped={5: "no plugin",
                                                 6: "no plugin"})
        self.assertEqual(self.problems("native static:uniform1"), [])
        problems = self.problems("native learned")
        self.assertEqual(len(problems), 1)
        self.assertIn("[5, 6]", problems[0])

    def test_missing_marker_is_refused(self):
        self.assertIn("no preflight marker",
                      pm.problems(pm.read_marker(self.marker), self.hashes(),
                                  {1})[0])

    def test_cli_exit_codes(self):
        base = [sys.executable, str(SCRIPT)]
        common = ["--root", str(self.root), "--marker", str(self.marker),
                  "--db-bench", str(self.db_bench)]
        run = lambda *a: subprocess.run([*base, *a], capture_output=True,
                                        text=True)
        self.assertEqual(run("write", *common, "--passed", "1", "2", "3", "4",
                             "--skipped", "5=no plugin", "6=no plugin")
                         .returncode, 0)
        record = json.loads(self.marker.read_text())
        self.assertEqual(record["steps_passed"], [1, 2, 3, 4])
        self.assertEqual(record["steps_skipped"], {"5": "no plugin",
                                                   "6": "no plugin"})
        self.assertEqual(run("check", *common, "--arms", "native").returncode,
                         0)
        refused = run("check", *common, "--arms", "learned")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("did not pass", refused.stderr)


if __name__ == "__main__":
    unittest.main()
